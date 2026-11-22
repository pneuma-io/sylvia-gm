//! Finishing a guided campaign's construction: completion, Campaign Plan admission, the
//! Builder-to-DM handoff and the first DM TURN in one transaction, in the store
//! (`SQLiteAuthorityStore.commit_guided_campaign_start`) and the application owner
//! (`AuthorityOwner.finalize_campaign_construction`, with `_model_guidance_for_lock` and
//! `_validate_required_plan_state`).
//!
//! The owner handler, `handle_finalize_campaign_construction`, is not ported: before this
//! commits it runs `_preflight_guided_start`, which needs lane 4's provider session adapter.
//!
//! Not yet ported, so refused (`StoreError::NotPorted`) rather than run differently:
//! - a construction that is not a guided Interview (the prepared and legacy Builder paths, which
//!   chain `complete_campaign_construction`, `record_builder_evidence`, `admit_campaign_plan`,
//!   `complete_turn` and `handoff_builder_to_dm`);
//! - a Ruleset that declares required starting state, which `plan_state_admission` (lane 1's,
//!   in `sylvia-app`) checks.
//!
//! Python's owner never gives its `AuthorityOwner` a model capability registry, so guidance is
//! assessed against the empty one (`EMPTY_MODEL_CAPABILITY_REGISTRY`), as here. Before
//! committing, Python also runs `sqlite_campaign.verify_f5_campaign_authority`, which is not
//! ported yet (see `guided_start`).

use crate::actions::{dump, load_payload};
use crate::campaigns::{current_lock_record, named_rows, Named};
use crate::error::{schema_identity, StoreError, StoreResult};
use crate::guided::digest24;
use crate::handle::StoreHandle;
use crate::packages::primary_manifest_for_lock;
use crate::sessions::{append_message, insert_session, read_handoff, read_messages, read_plan, read_session};
use crate::store::AuthorityStore;
use rusqlite::params;
use rusqlite::types::Value;
use std::collections::BTreeSet;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    owned_payload, validate_authority_identity, CampaignConstructionCompleted, PackageRole, WriterRole,
};
use sylvia_domain::campaign_plan::{
    validate_campaign_plan, CampaignPlanExpectations, CAMPAIGN_PLAN_FORMAT_VERSION,
};
use sylvia_domain::model_capability::{
    model_guidance_payload, model_guidance_payload_sha256, ModelCapabilityRegistry, ModelGuidanceDecision,
};
use sylvia_domain::package_library::{campaign_setting_entries, canonical_json_bytes, PackageLockRecord};
use sylvia_domain::pyops::py_eq;
use sylvia_domain::session::{
    builder_dm_handoff_payload_sha256, campaign_plan_payload_sha256, BuilderDMHandoff,
    BuilderDMHandoffFields, BuilderDMHandoffRequest, BuilderEvidence, CampaignPlan, CampaignPlanFields,
    CampaignPlanRequest, EvidenceSourceKind, PlanAcceptanceStatus, PlanValidationOutcome, SessionMessageKind,
    SessionRecord, SessionRecordFields, SessionStatus,
};

fn coded(kind: &'static str, message: &str) -> StoreError {
    StoreError::Coded(crate::error::authority_coded(kind, message))
}

fn rejected(message: &str, code: &str) -> StoreError {
    crate::actions::rejected(message, code)
}

fn canonical_sha256(value: &Json) -> String {
    sha256_hex(canonical_json_bytes(value))
}

/// What `commit_guided_campaign_start` commits and returns.
pub struct GuidedStart {
    pub completed: CampaignConstructionCompleted,
    pub plan: CampaignPlan,
    pub handoff: BuilderDMHandoff,
    pub dm_turn_id: String,
}

/// `commit_guided_campaign_start`'s keyword arguments.
pub struct GuidedStartCommit<'a> {
    pub completion_id: &'a str,
    pub completion_semantic_payload: &'a JsonMap,
    pub completion_payload_sha256: &'a str,
    pub campaign_id: &'a str,
    pub expected_begin_revision: i64,
    pub canonical_plan: &'a JsonMap,
    pub evidence: &'a BuilderEvidence,
    pub plan_id: &'a str,
    pub plan_content_sha256: &'a str,
    pub plan_content: &'a JsonMap,
    pub plan_payload_sha256: &'a str,
    pub handoff_id: &'a str,
    pub dm_session_id: &'a str,
    pub dm_provider_id: &'a str,
    pub dm_model_id: &'a str,
    pub expected_package_candidate_id: Option<&'a str>,
    pub model_guidance: &'a ModelGuidanceDecision,
    pub handoff_payload_sha256: &'a str,
    pub dm_turn_id: &'a str,
}

fn has_row(connection: &rusqlite::Connection, query: &str, campaign_id: &str) -> StoreResult<bool> {
    Ok(!named_rows(connection, query, &[&campaign_id])?.1.is_empty())
}

impl AuthorityStore {
    /// `commit_guided_campaign_start`: guided completion, Plan admission, the handoff and the
    /// initial DM TURN together. A replay of the same handoff returns what it committed.
    pub fn commit_guided_campaign_start(&self, start: &GuidedStartCommit<'_>) -> StoreResult<GuidedStart> {
        self.in_immediate(|connection| {
            crate::verify::verify_exact_schema_identity(connection, Some(crate::schema::CAMPAIGN_SCHEMA_VERSION))?;
            if let Some(existing) = read_handoff(connection, start.handoff_id)? {
                if existing.fields().payload_sha256 != start.handoff_payload_sha256 {
                    return Err(coded("IdempotencyConflict", "Builder-to-DM handoff identity payload does not match"));
                }
                let (construction_columns, construction) = named_rows(
                    connection,
                    "SELECT * FROM campaign_constructions WHERE campaign_id = ?",
                    &[&start.campaign_id],
                )?;
                let existing_plan = read_plan(connection, start.plan_id)?;
                let (_, turn) = named_rows(
                    connection,
                    "SELECT status FROM turns WHERE campaign_id = ? AND turn_id = ?",
                    &[&start.campaign_id, &start.dm_turn_id],
                )?;
                let (Some(construction), Some(plan), Some(turn)) =
                    (construction.into_iter().next(), existing_plan, turn.into_iter().next())
                else {
                    return Err(schema_identity("Guided Campaign start replay is incomplete"));
                };
                if crate::pytext::py_str(&turn[0]) != "open" {
                    return Err(schema_identity("Guided Campaign start replay is incomplete"));
                }
                let construction = Named { columns: &construction_columns, values: construction };
                // Python rolls back here; nothing was written, so committing is the same.
                return Ok(GuidedStart {
                    completed: CampaignConstructionCompleted {
                        campaign_id: start.campaign_id.to_owned(),
                        lane: construction.text("lane")?,
                        canonical_representation_sha256: construction.text("canonical_representation_sha256")?,
                        resulting_revision: construction.int("completion_revision")?,
                    },
                    plan,
                    handoff: existing,
                    dm_turn_id: start.dm_turn_id.to_owned(),
                });
            }
            let rows = crate::guided_start::construction_basis(connection, start.campaign_id)?;
            let (campaign, construction, construction_turn) = (rows.campaign(), rows.construction(), rows.turn());
            if construction.get("begin_operation_id")? == &Value::Null
                || construction.get("completion_operation_id")? != &Value::Null
                || campaign.int("revision")? != start.expected_begin_revision
                || start.expected_begin_revision != construction.int("begin_revision")?
                || campaign.text("writer")? != WriterRole::Builder.as_str()
                || campaign.text("lifecycle")? != "active"
                || construction_turn.text("status")? != "open"
            {
                return Err(rejected("Guided Campaign start basis has drifted", "command_rejected"));
            }
            let (confirmation_columns, confirmation) = named_rows(
                connection,
                "SELECT semantic_payload_json FROM campaign_operations WHERE operation_id = ?",
                &[&construction.text("confirmation_operation_id")?],
            )?;
            let Some(confirmation) = confirmation.into_iter().next() else {
                return Err(StoreError::Other("'NoneType' object is not subscriptable".into()));
            };
            let confirmation_payload =
                load_payload(&Named { columns: &confirmation_columns, values: confirmation }.text("semantic_payload_json")?)?;
            let builder_session_id = match confirmation_payload.get("builder_session").and_then(|b| b.get("session_id")) {
                Some(value) => crate::campaigns::json_str(value),
                None => return Err(StoreError::Other("'builder_session'".into())),
            };
            let builder = read_session(connection, &builder_session_id)?;
            if builder.fields().status != SessionStatus::Active {
                return Err(rejected("Guided Campaign Builder session is not active", "command_rejected"));
            }
            let messages = read_messages(connection, &builder_session_id)?;
            let Some(ready_message) = messages.iter().rev().find(|message| {
                message.kind() == SessionMessageKind::User
                    && message.content().get("interview_step") == Some(&Json::String("ready".into()))
                    && message.content().get("text") == Some(&Json::String("Ready to Begin".into()))
            }) else {
                return Err(rejected("Guided Campaign start requires genuine Ready input", "command_rejected"));
            };
            let interview_state = messages.iter().rev().find(|message| {
                message.kind() == SessionMessageKind::Evidence
                    && message.content().get("kind") == Some(&Json::String("campaign_interview_state".into()))
            });
            let unfinished = match interview_state {
                None => true,
                Some(state) => {
                    state.content().get("current_step") != Some(&Json::String("review".into()))
                        || !matches!(state.content().get("active_subflow"), None | Some(Json::Null))
                }
            };
            if unfinished {
                return Err(rejected("Guided Campaign start has unfinished Builder work", "command_rejected"));
            }
            if has_row(
                connection,
                "SELECT 1 FROM agent_sessions WHERE campaign_id = ? AND parent_session_id IS NOT NULL \
                 AND status NOT IN ('stopped', 'completed', 'handoff_closed') LIMIT 1",
                start.campaign_id,
            )? {
                return Err(rejected("Guided Campaign start has active delegated child work", "command_rejected"));
            }
            if has_row(
                connection,
                "SELECT 1 FROM agent_sessions WHERE campaign_id = ? AND status = 'review_required' LIMIT 1",
                start.campaign_id,
            )? {
                return Err(rejected("Guided Campaign start has unresolved review work", "command_rejected"));
            }

            let canonical_input = load_payload(&construction.text("canonical_input_json")?)?;
            let mut representation = JsonMap::new();
            representation.insert("canonical_input".into(), Json::Object(canonical_input));
            representation.insert("canonical_plan".into(), Json::Object(start.canonical_plan.clone()));
            let representation_sha256 = canonical_sha256(&Json::Object(representation));
            let completion_revision = start
                .expected_begin_revision
                .checked_add(1)
                .ok_or_else(|| StoreError::Other("campaign revision overflow".into()))?;
            let lane = construction.text("lane")?;
            let authority_turn_id = construction.text("authority_turn_id")?;
            let authority_lock_id = construction.text("authority_lock_id")?;
            let mut completion_result = JsonMap::new();
            completion_result.insert("campaign_id".into(), start.campaign_id.into());
            completion_result.insert("lane".into(), lane.as_str().into());
            completion_result.insert("canonical_representation_sha256".into(), representation_sha256.as_str().into());
            completion_result.insert("resulting_revision".into(), completion_revision.into());
            crate::guided_start::insert_campaign_operation(
                connection,
                &crate::guided_start::NewOperation {
                    operation_id: start.completion_id,
                    operation_kind: "complete_campaign_construction",
                    campaign_id: start.campaign_id,
                    semantic_payload: start.completion_semantic_payload,
                    payload_sha256: start.completion_payload_sha256,
                    result: &completion_result,
                    basis_turn_id: Some(&authority_turn_id),
                    basis_lock_id: Some(&authority_lock_id),
                    basis_revision: start.expected_begin_revision,
                    resulting_revision: completion_revision,
                },
            )?;
            connection.execute(
                "UPDATE campaign_constructions SET canonical_plan_json = ?, completion_operation_id = ?, \
                 completion_revision = ?, canonical_representation_sha256 = ? WHERE campaign_id = ?",
                params![
                    dump(&Json::Object(start.canonical_plan.clone())),
                    start.completion_id,
                    completion_revision,
                    representation_sha256,
                    start.campaign_id
                ],
            )?;
            connection.execute(
                "UPDATE campaigns SET revision = ? WHERE campaign_id = ? AND revision = ?",
                params![completion_revision, start.campaign_id, start.expected_begin_revision],
            )?;
            connection.execute(
                "INSERT INTO campaign_revisions \
                 (campaign_id, revision, action_id, activation_id, plan_id, handoff_id, construction_operation_id) \
                 VALUES (?, ?, NULL, NULL, NULL, NULL, ?)",
                params![start.campaign_id, completion_revision, start.completion_id],
            )?;
            append_message(
                connection,
                &builder_session_id,
                &start.evidence.evidence_id,
                SessionMessageKind::Evidence,
                &start.evidence.payload(),
                None,
            )?;
            let expected_plan_payload = campaign_plan_payload_sha256(&CampaignPlanRequest {
                plan_id: start.plan_id.to_owned(),
                campaign_id: start.campaign_id.to_owned(),
                source_session_id: builder_session_id.clone(),
                expected_revision: completion_revision,
                expected_package_lock_id: authority_lock_id.clone(),
                content_sha256: start.plan_content_sha256.to_owned(),
                content: Json::Object(start.plan_content.clone()),
                provenance_evidence_ids: vec![start.evidence.evidence_id.clone()],
                validation_outcome: PlanValidationOutcome::Valid,
                acceptance_status: PlanAcceptanceStatus::Accepted,
            })?;
            if expected_plan_payload != start.plan_payload_sha256 {
                return Err(coded("IdempotencyConflict", "Campaign Plan payload identity does not match"));
            }
            let plan_revision = 1;
            let plan_resulting_revision = completion_revision + 1;
            let plan = CampaignPlan::new(CampaignPlanFields {
                plan_id: start.plan_id.to_owned(),
                campaign_id: start.campaign_id.to_owned(),
                package_lock_id: authority_lock_id.clone(),
                source_session_id: builder_session_id.clone(),
                content_sha256: start.plan_content_sha256.to_owned(),
                content: Json::Object(start.plan_content.clone()),
                provenance_evidence_ids: vec![start.evidence.evidence_id.clone()],
                validation_outcome: PlanValidationOutcome::Valid,
                acceptance_status: PlanAcceptanceStatus::Accepted,
                plan_revision,
                resulting_campaign_revision: plan_resulting_revision,
                payload_sha256: start.plan_payload_sha256.to_owned(),
            })?;
            connection.execute(
                "INSERT INTO campaign_plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params![
                    start.plan_id,
                    start.campaign_id,
                    authority_lock_id,
                    builder_session_id,
                    start.plan_content_sha256,
                    dump(&Json::Object(plan.content().clone())),
                    dump(&Json::Array(vec![start.evidence.evidence_id.as_str().into()])),
                    PlanValidationOutcome::Valid.as_str(),
                    PlanAcceptanceStatus::Accepted.as_str(),
                    plan_revision,
                    plan_resulting_revision,
                    start.plan_payload_sha256
                ],
            )?;
            connection.execute(
                "UPDATE campaigns SET revision = ? WHERE campaign_id = ? AND revision = ?",
                params![plan_resulting_revision, start.campaign_id, completion_revision],
            )?;
            connection.execute(
                "INSERT INTO campaign_revisions (campaign_id, revision, action_id, activation_id, plan_id, handoff_id) \
                 VALUES (?, ?, NULL, NULL, ?, NULL)",
                params![start.campaign_id, plan_resulting_revision, start.plan_id],
            )?;
            connection.execute(
                "UPDATE turns SET status = 'completed' WHERE campaign_id = ? AND turn_id = ?",
                params![start.campaign_id, authority_turn_id],
            )?;

            let current_lock = current_lock_record(connection, start.campaign_id)?;
            let candidate = match start.expected_package_candidate_id {
                None => None,
                Some(candidate_id) => Some(crate::packages::read_candidate(connection, candidate_id)?),
            };
            if !crate::stored_sessions::handoff_candidate_matches_lock(&current_lock, candidate.as_ref()) {
                return Err(rejected("Guided Campaign start package candidate is inexact", "command_rejected"));
            }
            let minimum = primary_manifest_for_lock(connection, &authority_lock_id)?
                .map(|manifest| manifest.minimum_model_capability_tier);
            let guidance = start.model_guidance.fields();
            if guidance.model_id != start.dm_model_id
                || guidance.minimum_tier_id != minimum
                || !start.model_guidance.may_continue()
            {
                return Err(rejected("Guided Campaign start model guidance is not admitted", "command_rejected"));
            }
            let expected_handoff_payload = builder_dm_handoff_payload_sha256(&BuilderDMHandoffRequest {
                handoff_id: start.handoff_id.to_owned(),
                campaign_id: start.campaign_id.to_owned(),
                builder_session_id: builder_session_id.clone(),
                dm_session_id: start.dm_session_id.to_owned(),
                dm_provider_id: start.dm_provider_id.to_owned(),
                dm_model_id: start.dm_model_id.to_owned(),
                expected_writer: WriterRole::Builder,
                expected_revision: plan_resulting_revision,
                expected_package_lock_id: authority_lock_id.clone(),
                expected_package_candidate_id: start.expected_package_candidate_id.map(str::to_owned),
                plan_id: start.plan_id.to_owned(),
                expected_plan_revision: plan_revision,
                expected_plan_content_sha256: start.plan_content_sha256.to_owned(),
                completed_turn_id: authority_turn_id.clone(),
                model_guidance_sha256: model_guidance_payload_sha256(start.model_guidance),
            });
            if expected_handoff_payload != start.handoff_payload_sha256 {
                return Err(coded("IdempotencyConflict", "Builder-to-DM handoff payload identity does not match"));
            }
            let handoff_revision = plan_resulting_revision + 1;
            let mut chronology = JsonMap::new();
            chronology.insert("campaign_id".into(), start.campaign_id.into());
            chronology.insert("builder_session_id".into(), builder_session_id.as_str().into());
            chronology.insert("dm_session_id".into(), start.dm_session_id.into());
            chronology.insert("dm_provider_id".into(), start.dm_provider_id.into());
            chronology.insert("package_lock_id".into(), authority_lock_id.as_str().into());
            chronology.insert(
                "package_candidate_id".into(),
                start.expected_package_candidate_id.map_or(Json::Null, Json::from),
            );
            chronology.insert("plan_id".into(), start.plan_id.into());
            chronology.insert("resulting_campaign_revision".into(), handoff_revision.into());
            chronology.insert("completed_turn_id".into(), authority_turn_id.as_str().into());
            chronology.insert("model_guidance".into(), Json::Object(model_guidance_payload(start.model_guidance)));
            let handoff = BuilderDMHandoff::new(BuilderDMHandoffFields {
                handoff_id: start.handoff_id.to_owned(),
                campaign_id: start.campaign_id.to_owned(),
                builder_session_id: builder_session_id.clone(),
                dm_session_id: start.dm_session_id.to_owned(),
                package_lock_id: authority_lock_id.clone(),
                package_candidate_id: start.expected_package_candidate_id.map(str::to_owned),
                plan_id: start.plan_id.to_owned(),
                plan_revision,
                plan_content_sha256: start.plan_content_sha256.to_owned(),
                expected_revision: plan_resulting_revision,
                resulting_campaign_revision: handoff_revision,
                chronology_ordinal: 1,
                completed_turn_id: authority_turn_id.clone(),
                model_guidance: start.model_guidance.clone(),
                payload_sha256: start.handoff_payload_sha256.to_owned(),
            })?;
            connection.execute(
                "UPDATE campaigns SET writer = 'dm', revision = ? WHERE campaign_id = ? \
                 AND writer = 'builder' AND revision = ?",
                params![handoff_revision, start.campaign_id, plan_resulting_revision],
            )?;
            connection.execute(
                "UPDATE campaign_lifecycle_state SET retained_active_writer = 'dm' \
                 WHERE campaign_id = ? AND retained_active_writer = 'builder'",
                params![start.campaign_id],
            )?;
            insert_session(
                connection,
                &SessionRecord::new(SessionRecordFields {
                    session_id: start.dm_session_id.to_owned(),
                    campaign_id: Some(start.campaign_id.to_owned()),
                    role: WriterRole::Dm,
                    provider_id: start.dm_provider_id.to_owned(),
                    model_id: start.dm_model_id.to_owned(),
                    status: SessionStatus::Active,
                    ..SessionRecordFields::default()
                })?,
            )?;
            if let Some(Json::Object(structured)) = start.plan_content.get("campaign_plan") {
                crate::continuity::seed_initial_state(connection, start.campaign_id, structured, handoff_revision)?;
            }
            let mut reference = JsonMap::new();
            reference.insert("kind".into(), "ready_to_begin_reference".into());
            reference.insert("source_session_id".into(), builder_session_id.as_str().into());
            reference.insert("source_message_id".into(), ready_message.message_id().into());
            append_message(
                connection,
                start.dm_session_id,
                &format!("ready-reference-{}", ready_message.message_id()),
                SessionMessageKind::Evidence,
                &reference,
                None,
            )?;
            connection.execute(
                "INSERT INTO builder_dm_handoffs \
                 (handoff_id, campaign_id, payload_sha256, builder_session_id, dm_session_id, \
                 package_lock_id, package_candidate_id, plan_id, plan_revision, plan_content_sha256, \
                 expected_revision, resulting_campaign_revision, chronology_ordinal, completed_turn_id, \
                 safe_payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params![
                    start.handoff_id,
                    start.campaign_id,
                    start.handoff_payload_sha256,
                    builder_session_id,
                    start.dm_session_id,
                    authority_lock_id,
                    start.expected_package_candidate_id,
                    start.plan_id,
                    plan_revision,
                    start.plan_content_sha256,
                    plan_resulting_revision,
                    handoff_revision,
                    1,
                    authority_turn_id,
                    dump(&Json::Object(chronology))
                ],
            )?;
            connection.execute(
                "INSERT INTO campaign_revisions (campaign_id, revision, action_id, activation_id, plan_id, handoff_id) \
                 VALUES (?, ?, NULL, NULL, NULL, ?)",
                params![start.campaign_id, handoff_revision, start.handoff_id],
            )?;
            connection.execute(
                "INSERT INTO turns VALUES (?, ?, 'open', ?)",
                params![start.campaign_id, start.dm_turn_id, handoff_revision],
            )?;
            crate::stored::verify_stored_authority(connection)?;
            crate::packages::verify_package_authority(connection)?;
            // `sqlite_campaign.verify_f5_campaign_authority` runs here in Python (see the module
            // documentation).
            crate::continuity::verify_authority(connection)?;
            Ok(GuidedStart {
                completed: CampaignConstructionCompleted {
                    campaign_id: start.campaign_id.to_owned(),
                    lane,
                    canonical_representation_sha256: representation_sha256,
                    resulting_revision: completion_revision,
                },
                plan,
                handoff,
                dm_turn_id: start.dm_turn_id.to_owned(),
            })
        })
    }
}

/// `AuthorityOwner.finalize_campaign_construction`'s arguments.
pub struct FinalizeConstruction<'a> {
    pub campaign_id: &'a str,
    pub dm_provider_id: &'a str,
    pub dm_model_id: &'a str,
    pub plan_artifact_text: &'a str,
    pub structured_plan: Option<&'a JsonMap>,
    pub model_guidance_override: bool,
}

/// `{"id", "version", "content_sha256"}` of one lock entry, as a structured Plan binds it.
fn binding(entry: &sylvia_domain::authority::PackageLockEntry) -> Json {
    let mut value = JsonMap::new();
    value.insert("id".into(), entry.package_id().into());
    value.insert("version".into(), entry.version().into());
    value.insert("content_sha256".into(), entry.sha256().into());
    Json::Object(value)
}

impl StoreHandle {
    /// `AuthorityOwner._model_guidance_for_lock` against the owner's (empty) registry.
    async fn model_guidance_for_lock(
        &self,
        lock_id: &str,
        model_id: &str,
        override_accepted: bool,
    ) -> StoreResult<ModelGuidanceDecision> {
        let lock_id = lock_id.to_owned();
        let minimum = self
            .call(move |store| {
                Ok(primary_manifest_for_lock(&store.connect()?, &lock_id)?
                    .map(|manifest| manifest.minimum_model_capability_tier))
            })
            .await?;
        Ok(ModelCapabilityRegistry::empty().assess(model_id, minimum.as_deref(), override_accepted)?)
    }

    /// `AuthorityOwner.finalize_campaign_construction` for a guided Interview: materialise,
    /// admit, checkpoint and hand the construction to the DM in one store transaction.
    pub async fn finalize_campaign_construction(
        &self,
        finalize: &FinalizeConstruction<'_>,
    ) -> StoreResult<(CampaignConstructionCompleted, CampaignPlan, BuilderDMHandoff)> {
        validate_authority_identity(finalize.campaign_id, "Campaign identity", false)?;
        validate_authority_identity(finalize.dm_provider_id, "DM provider identity", false)?;
        validate_authority_identity(finalize.dm_model_id, "DM model identity", false)?;
        let campaign_id = finalize.campaign_id.to_owned();
        let basis = {
            let campaign_id = campaign_id.clone();
            self.call(move |store| store.campaign_construction_basis(&campaign_id)).await?
        };
        let lock: PackageLockRecord = {
            let campaign_id = campaign_id.clone();
            self.call(move |store| current_lock_record(&store.connect()?, &campaign_id)).await?
        };
        if lock.lock_id != basis.package_lock_id {
            return Err(rejected("Campaign construction package lock basis has drifted", "command_rejected"));
        }
        if !lock.package_lock.entries().is_empty() {
            let Some(candidate_id) = basis.package_candidate_id.clone() else {
                return Err(rejected(
                    "Campaign construction package candidate basis is unavailable",
                    "command_rejected",
                ));
            };
            let candidate = self.call(move |store| store.package_candidate(&candidate_id)).await?;
            if candidate.package_lock() != &lock.package_lock
                || candidate.closure_sha256() != lock.closure_sha256
            {
                return Err(rejected(
                    "Campaign construction package candidate basis is inexact",
                    "command_rejected",
                ));
            }
        } else if basis.package_candidate_id.is_some() {
            return Err(rejected(
                "No-rules construction cannot bind a package candidate",
                "command_rejected",
            ));
        }
        if sylvia_base::pystr::strip(finalize.plan_artifact_text).is_empty() {
            return Err(rejected("Campaign plan file is empty or unusable", "command_rejected"));
        }
        let plan_content = match finalize.structured_plan {
            None => {
                let mut content = JsonMap::new();
                content.insert("campaign_plan_markdown".into(), finalize.plan_artifact_text.into());
                content
            }
            Some(structured) => {
                let captured = owned_payload(&Json::Object(structured.clone()))?;
                let format_ok = captured.get("format_id")
                    == Some(&Json::String("sylvia-gm-campaign-plan".into()))
                    && captured
                        .get("format_version")
                        .is_some_and(|version| py_eq(version, &Json::from(CAMPAIGN_PLAN_FORMAT_VERSION)));
                let Some(Json::Object(bindings)) = captured.get("bindings").filter(|_| format_ok) else {
                    return Err(rejected("Structured Campaign Plan format is invalid", "command_rejected"));
                };
                let primary =
                    lock.package_lock.entries().iter().find(|entry| entry.role() == PackageRole::Primary);
                let settings = campaign_setting_entries(&lock.package_lock);
                let expected_ruleset = primary.map_or(Json::Null, binding);
                let expected_setting = if settings.len() == 1 { binding(settings[0]) } else { Json::Null };
                let bound = |key: &str| bindings.get(key).cloned().unwrap_or(Json::Null);
                if !py_eq(&bound("ruleset"), &expected_ruleset)
                    || !py_eq(&bound("campaign_setting"), &expected_setting)
                    || !py_eq(&bound("package_closure_sha256"), &Json::from(lock.closure_sha256.as_str()))
                {
                    return Err(rejected(
                        "Structured Campaign Plan package bindings are stale",
                        "command_rejected",
                    ));
                }
                if primary.is_none() {
                    let paths = BTreeSet::new();
                    let knowledge = BTreeSet::new();
                    validate_campaign_plan(
                        &Json::Object(captured.clone()),
                        finalize.plan_artifact_text,
                        CampaignPlanExpectations {
                            expected_ruleset: None,
                            expected_setting: None,
                            expected_closure_sha256: &lock.closure_sha256,
                            setting_paths: &paths,
                            setting_knowledge_ids: &knowledge,
                        },
                    )
                    .map_err(|error| {
                        rejected(
                            &format!("No Ruleset Campaign Plan is invalid: {}", error.message()),
                            "invalid_campaign_plan",
                        )
                    })?;
                }
                let mut content = JsonMap::new();
                content.insert(
                    "artifact_content_sha256".into(),
                    canonical_sha256(&Json::Object(captured.clone())).into(),
                );
                content.insert("campaign_plan".into(), Json::Object(captured));
                content.insert("campaign_plan_markdown".into(), finalize.plan_artifact_text.into());
                content.insert("package_lock_id".into(), lock.lock_id.as_str().into());
                content
            }
        };
        // `_validate_required_plan_state`: nothing to check unless the Ruleset declares required
        // starting state, which needs lane 1's `plan_state_admission`.
        {
            let lock_id = lock.lock_id.clone();
            let manifest =
                self.call(move |store| primary_manifest_for_lock(&store.connect()?, &lock_id)).await?;
            if manifest.is_some_and(|manifest| manifest.state_schemas.iter().any(|schema| schema.required)) {
                return Err(StoreError::NotPorted(
                    "required Ruleset starting state (lane 1's plan_state_admission)",
                ));
            }
        }
        let content_sha256 = canonical_sha256(&Json::Object(plan_content.clone()));
        let suffix = digest24(finalize.campaign_id);
        let completion_id = format!("construction-complete-{suffix}");
        let evidence_id = format!("construction-evidence-{suffix}");
        let plan_id = format!("campaign-plan-{suffix}");
        let handoff_id = format!("builder-dm-handoff-{suffix}");
        let dm_session_id = format!("session-dm-{suffix}");
        let builder_context = {
            let session_id = basis.builder_session_id.clone();
            self.call(move |store| store.session_context(&session_id)).await?
        };
        let guided = builder_context.messages.iter().any(|message| {
            message.kind() == SessionMessageKind::Evidence
                && message.content().get("kind") == Some(&Json::String("campaign_interview_state".into()))
        });
        if !guided {
            return Err(StoreError::NotPorted("finalizing a construction that is not a guided Interview"));
        }
        let guidance = self
            .model_guidance_for_lock(&lock.lock_id, finalize.dm_model_id, finalize.model_guidance_override)
            .await?;
        if !guidance.may_continue() {
            let code = guidance
                .fields()
                .warning_code
                .clone()
                .unwrap_or_else(|| "model_guidance_confirmation_required".into());
            return Err(rejected("Selected model capability guidance requires explicit confirmation", &code));
        }
        let mut completion_payload = JsonMap::new();
        completion_payload.insert("operation_id".into(), completion_id.as_str().into());
        completion_payload.insert("operation_kind".into(), "complete_campaign_construction".into());
        completion_payload.insert("campaign_id".into(), finalize.campaign_id.into());
        completion_payload.insert("canonical_plan".into(), Json::Object(plan_content.clone()));
        let completion_payload_sha256 = canonical_sha256(&Json::Object(completion_payload.clone()));
        let completion_revision = basis
            .begin_revision
            .checked_add(1)
            .ok_or_else(|| StoreError::Other("campaign revision overflow".into()))?;
        let plan_payload_sha256 = campaign_plan_payload_sha256(&CampaignPlanRequest {
            plan_id: plan_id.clone(),
            campaign_id: campaign_id.clone(),
            source_session_id: basis.builder_session_id.clone(),
            expected_revision: completion_revision,
            expected_package_lock_id: lock.lock_id.clone(),
            content_sha256: content_sha256.clone(),
            content: Json::Object(plan_content.clone()),
            provenance_evidence_ids: vec![evidence_id.clone()],
            validation_outcome: PlanValidationOutcome::Valid,
            acceptance_status: PlanAcceptanceStatus::Accepted,
        })?;
        let handoff_payload_sha256 = builder_dm_handoff_payload_sha256(&BuilderDMHandoffRequest {
            handoff_id: handoff_id.clone(),
            campaign_id: campaign_id.clone(),
            builder_session_id: basis.builder_session_id.clone(),
            dm_session_id: dm_session_id.clone(),
            dm_provider_id: finalize.dm_provider_id.to_owned(),
            dm_model_id: finalize.dm_model_id.to_owned(),
            expected_writer: WriterRole::Builder,
            expected_revision: completion_revision + 1,
            expected_package_lock_id: lock.lock_id.clone(),
            expected_package_candidate_id: basis.package_candidate_id.clone(),
            plan_id: plan_id.clone(),
            expected_plan_revision: 1,
            expected_plan_content_sha256: content_sha256.clone(),
            completed_turn_id: basis.authority_turn_id.clone(),
            model_guidance_sha256: model_guidance_payload_sha256(&guidance),
        });
        let evidence = BuilderEvidence {
            evidence_id: evidence_id.clone(),
            source_kind: EvidenceSourceKind::UserProvided,
            content_sha256: content_sha256.clone(),
            source_locator: None,
            safe_excerpt: "Owner-materialized guided Campaign Plan.".into(),
            claims: vec!["construction-input".into(), "package-lock".into(), "participant-artifacts".into()],
        }
        .validated()?;
        let dm_turn_id = format!("turn-dm-{suffix}");
        let (dm_provider_id, dm_model_id) =
            (finalize.dm_provider_id.to_owned(), finalize.dm_model_id.to_owned());
        let candidate_id = basis.package_candidate_id.clone();
        let begin_revision = basis.begin_revision;
        let started = self
            .call(move |store| {
                store.commit_guided_campaign_start(&GuidedStartCommit {
                    completion_id: &completion_id,
                    completion_semantic_payload: &completion_payload,
                    completion_payload_sha256: &completion_payload_sha256,
                    campaign_id: &campaign_id,
                    expected_begin_revision: begin_revision,
                    canonical_plan: &plan_content,
                    evidence: &evidence,
                    plan_id: &plan_id,
                    plan_content_sha256: &content_sha256,
                    plan_content: &plan_content,
                    plan_payload_sha256: &plan_payload_sha256,
                    handoff_id: &handoff_id,
                    dm_session_id: &dm_session_id,
                    dm_provider_id: &dm_provider_id,
                    dm_model_id: &dm_model_id,
                    expected_package_candidate_id: candidate_id.as_deref(),
                    model_guidance: &guidance,
                    handoff_payload_sha256: &handoff_payload_sha256,
                    dm_turn_id: &dm_turn_id,
                })
            })
            .await?;
        Ok((started.completed, started.plan, started.handoff))
    }
}
