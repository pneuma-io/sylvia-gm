//! Admitting a Campaign Plan and handing a campaign from its Builder to its DM, each in its own
//! transaction, in the store (`SQLiteAuthorityStore.commit_campaign_plan` and
//! `commit_builder_dm_handoff`), and the owner's `record_builder_evidence`
//! (`SessionApplication.record_evidence`) on the store handle.
//!
//! The prepared and legacy Builder paths of `finalize_campaign_construction` run these one after
//! another; a guided Interview commits all of them at once (`guided_finalize`). The application
//! halves are `sylvia_app::authority::construction_handoff`.
//!
//! Python's `_handoff_failure_stage` is a fault-injection hook that does nothing outside its
//! tests, so nothing runs in its place.

use crate::actions::dump;
use crate::campaigns::{current_lock_record, named_rows, Named};
use crate::error::{StoreError, StoreResult};
use crate::handle::StoreHandle;
use crate::packages::{primary_manifest_for_lock, read_candidate};
use crate::sessions::{
    append_message, insert_session, message_from_row, read_handoff, read_plan, read_session,
};
use crate::store::AuthorityStore;
use rusqlite::{params, Connection};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{LifecycleState, WriterRole};
use sylvia_domain::model_capability::{
    model_guidance_payload, model_guidance_payload_sha256, ModelGuidanceDecision,
};
use sylvia_domain::session::{
    builder_dm_handoff_payload_sha256, campaign_plan_payload_sha256, BuilderDMHandoff,
    BuilderDMHandoffFields, BuilderDMHandoffRequest, BuilderEvidence, CampaignPlan, CampaignPlanFields,
    CampaignPlanRequest, PlanAcceptanceStatus, PlanValidationOutcome, SessionMessage, SessionMessageKind,
    SessionRecord, SessionRecordFields, SessionStatus,
};

fn coded(kind: &'static str, message: &str) -> StoreError {
    StoreError::Coded(crate::error::authority_coded(kind, message))
}

fn rejected(message: &str, code: &str) -> StoreError {
    crate::actions::rejected(message, code)
}

fn exists(connection: &Connection, query: &str, values: &[&dyn rusqlite::ToSql]) -> StoreResult<bool> {
    Ok(!named_rows(connection, query, values)?.1.is_empty())
}

/// `commit_builder_dm_handoff`'s keyword arguments: the request whose identity
/// `builder_dm_handoff_payload_sha256` hashes, the model guidance it hashes, and that hash.
pub struct CommitHandoff<'a> {
    pub request: &'a BuilderDMHandoffRequest,
    pub model_guidance: &'a ModelGuidanceDecision,
    pub payload_sha256: &'a str,
}

impl AuthorityStore {
    /// `commit_campaign_plan`: persist one accepted plan and its single campaign revision.
    pub fn commit_campaign_plan(
        &self,
        request: &CampaignPlanRequest,
        payload_sha256: &str,
    ) -> StoreResult<CampaignPlan> {
        self.ensure_session_schema()?;
        self.in_immediate(|connection| {
            if let Some(existing) = read_plan(connection, &request.plan_id)? {
                if existing.fields().payload_sha256 != payload_sha256 {
                    return Err(coded(
                        "IdempotencyConflict",
                        "Campaign Plan identity payload does not match",
                    ));
                }
                // Python rolls back here; nothing was written, so committing is the same.
                return Ok(existing);
            }
            let campaign_id = request.campaign_id.as_str();
            let (columns, rows) = named_rows(
                connection,
                "SELECT lifecycle, writer, revision, current_lock_id FROM campaigns WHERE campaign_id = ?",
                &[&campaign_id],
            )?;
            let Some(values) = rows.into_iter().next() else {
                return Err(rejected("Campaign is unavailable", "command_rejected"));
            };
            let campaign = Named { columns: &columns, values };
            if campaign.text("lifecycle")? != LifecycleState::Active.as_str() {
                return Err(rejected(
                    "Campaign lifecycle blocks Campaign Plan admission",
                    "command_rejected",
                ));
            }
            if campaign.text("writer")? != WriterRole::Builder.as_str() {
                return Err(coded("WrongWriter", "Builder is not the authoritative campaign writer"));
            }
            if campaign.int("revision")? != request.expected_revision {
                return Err(coded("StaleRevision", "Campaign Plan expected revision is stale"));
            }
            if campaign.optional_text("current_lock_id")?.as_deref().unwrap_or("None")
                != request.expected_package_lock_id
            {
                return Err(rejected(
                    "Campaign Plan expected package lock is not current",
                    "campaign_plan_lock_mismatch",
                ));
            }
            let source = read_session(connection, &request.source_session_id)?;
            if source.campaign_id() != Some(campaign_id)
                || source.role() != WriterRole::Builder
                || source.fields().status != SessionStatus::Active
                || source.fields().selected_skill.as_deref() != Some("BUILD CAMPAIGN PLAN")
            {
                return Err(rejected(
                    "Campaign Plan source session is not authoritative",
                    "command_rejected",
                ));
            }
            let wanted: std::collections::BTreeSet<&str> =
                request.provenance_evidence_ids.iter().map(String::as_str).collect();
            let (message_columns, message_rows) = named_rows(
                connection,
                "SELECT message_id, kind FROM agent_session_messages WHERE session_id = ?",
                &[&request.source_session_id],
            )?;
            let mut found = Vec::new();
            for values in message_rows {
                let row = Named { columns: &message_columns, values };
                let message_id = row.text("message_id")?;
                if wanted.contains(message_id.as_str()) {
                    found.push((message_id, row.text("kind")?));
                }
            }
            let found_ids: std::collections::BTreeSet<&str> =
                found.iter().map(|(id, _)| id.as_str()).collect();
            if found.len() != request.provenance_evidence_ids.len()
                || found_ids != wanted
                || found.iter().any(|(_, kind)| kind != SessionMessageKind::Evidence.as_str())
            {
                return Err(rejected("Campaign Plan provenance evidence is unavailable", "command_rejected"));
            }
            if campaign_plan_payload_sha256(request)? != payload_sha256 {
                return Err(coded("IdempotencyConflict", "Campaign Plan payload identity does not match"));
            }
            let plan_revision: i64 = connection.query_row(
                "SELECT COALESCE(MAX(plan_revision), 0) + 1 FROM campaign_plans WHERE campaign_id = ?",
                params![campaign_id],
                |row| row.get(0),
            )?;
            let resulting_revision = request
                .expected_revision
                .checked_add(1)
                .ok_or_else(|| StoreError::Other("campaign revision overflow".into()))?;
            let plan = CampaignPlan::new(CampaignPlanFields {
                plan_id: request.plan_id.clone(),
                campaign_id: request.campaign_id.clone(),
                package_lock_id: request.expected_package_lock_id.clone(),
                source_session_id: request.source_session_id.clone(),
                content_sha256: request.content_sha256.clone(),
                content: request.content.clone(),
                provenance_evidence_ids: request.provenance_evidence_ids.clone(),
                validation_outcome: request.validation_outcome,
                acceptance_status: request.acceptance_status,
                plan_revision,
                resulting_campaign_revision: resulting_revision,
                payload_sha256: payload_sha256.to_owned(),
            })?;
            let provenance = Json::Array(
                request.provenance_evidence_ids.iter().map(|id| Json::from(id.as_str())).collect(),
            );
            connection.execute(
                "INSERT INTO campaign_plans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params![
                    request.plan_id,
                    campaign_id,
                    request.expected_package_lock_id,
                    request.source_session_id,
                    request.content_sha256,
                    dump(&Json::Object(plan.content().clone())),
                    dump(&provenance),
                    request.validation_outcome.as_str(),
                    request.acceptance_status.as_str(),
                    plan_revision,
                    resulting_revision,
                    payload_sha256
                ],
            )?;
            let updated = connection.execute(
                "UPDATE campaigns SET revision = ? WHERE campaign_id = ? AND revision = ? \
                 AND lifecycle = 'active' AND writer = 'builder'",
                params![resulting_revision, campaign_id, request.expected_revision],
            )?;
            if updated != 1 {
                return Err(coded("StaleRevision", "Campaign Plan revision fence rejected admission"));
            }
            connection.execute(
                "INSERT INTO campaign_revisions (campaign_id, revision, action_id, activation_id, plan_id) \
                 VALUES (?, ?, NULL, NULL, ?)",
                params![campaign_id, resulting_revision, request.plan_id],
            )?;
            Ok(plan)
        })
    }

    /// `commit_builder_dm_handoff`: move one quiescent campaign from its Builder to a new DM
    /// session, with every handoff authority field, in one transaction.
    pub fn commit_builder_dm_handoff(&self, commit: &CommitHandoff<'_>) -> StoreResult<BuilderDMHandoff> {
        let request = commit.request;
        self.ensure_session_schema()?;
        self.in_immediate(|connection| {
            if let Some(existing) = read_handoff(connection, &request.handoff_id)? {
                if existing.fields().payload_sha256 != commit.payload_sha256 {
                    return Err(coded("IdempotencyConflict", "Builder-to-DM handoff identity payload does not match"));
                }
                return Ok(existing);
            }
            let campaign_id = request.campaign_id.as_str();
            let (columns, rows) = named_rows(
                connection,
                "SELECT lifecycle, writer, revision, current_lock_id FROM campaigns WHERE campaign_id = ?",
                &[&campaign_id],
            )?;
            let Some(values) = rows.into_iter().next() else {
                return Err(rejected("Campaign is unavailable", "command_rejected"));
            };
            let campaign = Named { columns: &columns, values };
            if campaign.text("writer")? != request.expected_writer.as_str()
                || request.expected_writer != WriterRole::Builder
            {
                return Err(coded("WrongWriter", "Builder is not the authoritative campaign writer"));
            }
            if campaign.int("revision")? != request.expected_revision {
                return Err(coded("StaleRevision", "Builder-to-DM handoff expected revision is stale"));
            }
            if campaign.optional_text("current_lock_id")?.as_deref().unwrap_or("None")
                != request.expected_package_lock_id
            {
                return Err(rejected("Builder-to-DM handoff package lock is not exact", "handoff_lock_mismatch"));
            }
            let minimum = primary_manifest_for_lock(connection, &request.expected_package_lock_id)?
                .map(|manifest| manifest.minimum_model_capability_tier);
            let guidance = commit.model_guidance.fields();
            if guidance.model_id != request.dm_model_id
                || guidance.minimum_tier_id != minimum
                || !commit.model_guidance.may_continue()
            {
                return Err(rejected(
                    "Builder-to-DM handoff model guidance is not exact or confirmed",
                    "handoff_model_guidance_mismatch",
                ));
            }
            if campaign.text("lifecycle")? != LifecycleState::Active.as_str() {
                return Err(rejected(
                    "Builder-to-DM handoff campaign is not quiescent",
                    "handoff_lifecycle_not_quiescent",
                ));
            }
            let current_lock = current_lock_record(connection, campaign_id)?;
            let candidate = match &request.expected_package_candidate_id {
                None => None,
                Some(candidate_id) => Some(read_candidate(connection, candidate_id)?),
            };
            if !crate::stored_sessions::handoff_candidate_matches_lock(&current_lock, candidate.as_ref()) {
                return Err(rejected(
                    "Builder-to-DM handoff package candidate is not exact and published",
                    "handoff_candidate_mismatch",
                ));
            }
            if exists(connection, "SELECT handoff_id FROM builder_dm_handoffs WHERE campaign_id = ?", &[&campaign_id])? {
                return Err(coded("IdempotencyConflict", "Campaign already has another Builder-to-DM handoff"));
            }
            let plan = read_plan(connection, &request.plan_id)?;
            let latest_plan_revision: Option<i64> = connection.query_row(
                "SELECT MAX(plan_revision) FROM campaign_plans WHERE campaign_id = ?",
                params![campaign_id],
                |row| row.get(0),
            )?;
            let exact_plan = plan.as_ref().filter(|plan| {
                let fields = plan.fields();
                fields.campaign_id == campaign_id
                    && fields.acceptance_status == PlanAcceptanceStatus::Accepted
                    && fields.validation_outcome == PlanValidationOutcome::Valid
                    && fields.plan_revision == request.expected_plan_revision
                    && fields.plan_revision == latest_plan_revision.unwrap_or(0)
                    && fields.content_sha256 == request.expected_plan_content_sha256
                    && fields.package_lock_id == request.expected_package_lock_id
                    && fields.source_session_id == request.builder_session_id
                    && !fields.provenance_evidence_ids.is_empty()
                    && fields.resulting_campaign_revision <= request.expected_revision
            });
            let Some(plan) = exact_plan else {
                return Err(rejected(
                    "Builder-to-DM handoff Campaign Plan is not exact and accepted",
                    "handoff_plan_mismatch",
                ));
            };
            let builder = read_session(connection, &request.builder_session_id)?;
            if builder.campaign_id() != Some(campaign_id)
                || builder.role() != WriterRole::Builder
                || builder.fields().status != SessionStatus::Active
                || builder.fields().parent_session_id.is_some()
            {
                return Err(rejected("Builder handoff session is not authoritative", "command_rejected"));
            }
            let guided = exists(
                connection,
                "SELECT 1 FROM agent_session_messages WHERE session_id = ? \
                 AND kind = 'evidence' AND content_json LIKE '%campaign_interview_state%' LIMIT 1",
                &[&request.builder_session_id],
            )?;
            let mut ready_message: Option<SessionMessage> = None;
            if guided {
                let (user_columns, user_rows) = named_rows(
                    connection,
                    "SELECT * FROM agent_session_messages WHERE session_id = ? AND kind = 'user' \
                     ORDER BY sequence DESC",
                    &[&request.builder_session_id],
                )?;
                for values in user_rows {
                    let message = message_from_row(&Named { columns: &user_columns, values })?;
                    if message.content().get("interview_step") == Some(&Json::String("ready".into()))
                        && message.content().get("text") == Some(&Json::String("Ready to Begin".into()))
                    {
                        ready_message = Some(message);
                        break;
                    }
                }
                if ready_message.is_none() {
                    return Err(rejected(
                        "Guided Campaign handoff requires the genuine Ready to Begin input",
                        "command_rejected",
                    ));
                }
            }
            if exists(
                connection,
                "SELECT 1 FROM agent_sessions WHERE campaign_id = ? AND parent_session_id IS NOT NULL \
                 AND status NOT IN ('stopped', 'completed', 'handoff_closed') LIMIT 1",
                &[&campaign_id],
            )? {
                return Err(rejected("Builder-to-DM handoff has active child work", "handoff_child_work_active"));
            }
            if exists(
                connection,
                "SELECT 1 FROM agent_sessions WHERE campaign_id = ? AND status = 'review_required' LIMIT 1",
                &[&campaign_id],
            )? {
                return Err(rejected(
                    "Builder-to-DM handoff has an unresolved human-review blocker",
                    "handoff_review_blocked",
                ));
            }
            if exists(
                connection,
                "SELECT 1 FROM agent_sessions WHERE campaign_id = ? AND status = 'active' AND session_id != ? LIMIT 1",
                &[&campaign_id, &request.builder_session_id],
            )? {
                return Err(rejected(
                    "Builder-to-DM handoff has another active session",
                    "handoff_session_work_active",
                ));
            }
            if exists(
                connection,
                "SELECT 1 FROM turns WHERE campaign_id = ? AND status != 'completed' LIMIT 1",
                &[&campaign_id],
            )? {
                return Err(rejected("Builder-to-DM handoff has an incomplete TURN", "handoff_turn_incomplete"));
            }
            if exists(
                connection,
                "SELECT 1 FROM actions WHERE campaign_id = ? AND status NOT IN ('committed', 'failed') LIMIT 1",
                &[&campaign_id],
            )? {
                return Err(rejected("Builder-to-DM handoff has an incomplete action", "handoff_action_incomplete"));
            }
            let (turn_columns, turn_rows) = named_rows(
                connection,
                "SELECT status FROM turns WHERE campaign_id = ? AND turn_id = ?",
                &[&campaign_id, &request.completed_turn_id],
            )?;
            let completed = match turn_rows.into_iter().next() {
                Some(values) => Named { columns: &turn_columns, values }.text("status")? == "completed",
                None => false,
            };
            if !completed {
                return Err(rejected("Builder-to-DM handoff requires a completed TURN", "handoff_turn_incomplete"));
            }
            if exists(connection, "SELECT 1 FROM agent_sessions WHERE session_id = ?", &[&request.dm_session_id])? {
                return Err(coded("IdempotencyConflict", "DM session identity is already in use"));
            }
            let mut expected = request.clone();
            expected.model_guidance_sha256 = model_guidance_payload_sha256(commit.model_guidance);
            if builder_dm_handoff_payload_sha256(&expected) != commit.payload_sha256 {
                return Err(coded("IdempotencyConflict", "Builder-to-DM handoff payload identity does not match"));
            }
            let resulting_revision = request
                .expected_revision
                .checked_add(1)
                .ok_or_else(|| StoreError::Other("campaign revision overflow".into()))?;
            let mut chronology = JsonMap::new();
            chronology.insert("campaign_id".into(), campaign_id.into());
            chronology.insert("builder_session_id".into(), request.builder_session_id.as_str().into());
            chronology.insert("dm_session_id".into(), request.dm_session_id.as_str().into());
            chronology.insert("dm_provider_id".into(), request.dm_provider_id.as_str().into());
            chronology.insert("package_lock_id".into(), request.expected_package_lock_id.as_str().into());
            chronology.insert(
                "package_candidate_id".into(),
                request.expected_package_candidate_id.as_deref().map_or(Json::Null, Json::from),
            );
            chronology.insert("plan_id".into(), request.plan_id.as_str().into());
            chronology.insert("resulting_campaign_revision".into(), resulting_revision.into());
            chronology.insert("completed_turn_id".into(), request.completed_turn_id.as_str().into());
            chronology.insert("model_guidance".into(), Json::Object(model_guidance_payload(commit.model_guidance)));
            let handoff = BuilderDMHandoff::new(BuilderDMHandoffFields {
                handoff_id: request.handoff_id.clone(),
                campaign_id: request.campaign_id.clone(),
                builder_session_id: request.builder_session_id.clone(),
                dm_session_id: request.dm_session_id.clone(),
                package_lock_id: request.expected_package_lock_id.clone(),
                package_candidate_id: request.expected_package_candidate_id.clone(),
                plan_id: request.plan_id.clone(),
                plan_revision: request.expected_plan_revision,
                plan_content_sha256: request.expected_plan_content_sha256.clone(),
                expected_revision: request.expected_revision,
                resulting_campaign_revision: resulting_revision,
                chronology_ordinal: 1,
                completed_turn_id: request.completed_turn_id.clone(),
                model_guidance: commit.model_guidance.clone(),
                payload_sha256: commit.payload_sha256.to_owned(),
            })?;
            let updated = connection.execute(
                "UPDATE campaigns SET writer = 'dm', revision = ? WHERE campaign_id = ? \
                 AND writer = 'builder' AND revision = ? AND lifecycle = 'active' AND current_lock_id = ?",
                params![resulting_revision, campaign_id, request.expected_revision, request.expected_package_lock_id],
            )?;
            if updated != 1 {
                return Err(coded("StaleRevision", "Builder-to-DM handoff revision fence rejected transition"));
            }
            let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
            if version == crate::schema::CAMPAIGN_SCHEMA_VERSION {
                let lifecycle_updated = connection.execute(
                    "UPDATE campaign_lifecycle_state SET retained_active_writer = 'dm' \
                     WHERE campaign_id = ? AND retained_active_writer = 'builder' \
                     AND tombstone_prior_lifecycle IS NULL",
                    params![campaign_id],
                )?;
                if lifecycle_updated != 1 {
                    return Err(crate::error::schema_identity("Campaign lifecycle authority is inconsistent"));
                }
            }
            insert_session(
                connection,
                &SessionRecord::new(SessionRecordFields {
                    session_id: request.dm_session_id.clone(),
                    campaign_id: Some(request.campaign_id.clone()),
                    role: WriterRole::Dm,
                    provider_id: request.dm_provider_id.clone(),
                    model_id: request.dm_model_id.clone(),
                    status: SessionStatus::Active,
                    ..SessionRecordFields::default()
                })?,
            )?;
            if let Some(Json::Object(structured)) = plan.content().get("campaign_plan") {
                crate::continuity::seed_initial_state(connection, campaign_id, structured, resulting_revision)?;
            }
            if let Some(ready) = &ready_message {
                let mut reference = JsonMap::new();
                reference.insert("kind".into(), "ready_to_begin_reference".into());
                reference.insert("source_session_id".into(), request.builder_session_id.as_str().into());
                reference.insert("source_message_id".into(), ready.message_id().into());
                append_message(
                    connection,
                    &request.dm_session_id,
                    &format!("ready-reference-{}", ready.message_id()),
                    SessionMessageKind::Evidence,
                    &reference,
                    None,
                )?;
            }
            connection.execute(
                "INSERT INTO builder_dm_handoffs \
                 (handoff_id, campaign_id, payload_sha256, builder_session_id, dm_session_id, \
                 package_lock_id, package_candidate_id, plan_id, plan_revision, plan_content_sha256, \
                 expected_revision, resulting_campaign_revision, chronology_ordinal, completed_turn_id, \
                 safe_payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params![
                    request.handoff_id,
                    campaign_id,
                    commit.payload_sha256,
                    request.builder_session_id,
                    request.dm_session_id,
                    request.expected_package_lock_id,
                    request.expected_package_candidate_id,
                    request.plan_id,
                    request.expected_plan_revision,
                    request.expected_plan_content_sha256,
                    request.expected_revision,
                    resulting_revision,
                    1,
                    request.completed_turn_id,
                    dump(&Json::Object(chronology))
                ],
            )?;
            connection.execute(
                "INSERT INTO campaign_revisions (campaign_id, revision, action_id, activation_id, plan_id, handoff_id) \
                 VALUES (?, ?, NULL, NULL, NULL, ?)",
                params![campaign_id, resulting_revision, request.handoff_id],
            )?;
            Ok(handoff)
        })
    }
}

impl StoreHandle {
    /// `AuthorityOwner.record_builder_evidence`: the session's campaign work, then
    /// `SessionApplication.record_evidence` (the evidence checked first, its `ValueError`
    /// uncaught) under the session lane. Only an active Builder session admits evidence.
    pub async fn record_builder_evidence(
        &self,
        evidence: BuilderEvidence,
        session_id: &str,
    ) -> StoreResult<BuilderEvidence> {
        let _operation = self.operation()?;
        let _work = self.session_campaign_work(session_id).await?;
        let evidence = evidence.validated()?;
        let _session = self.lanes().sessions.acquire(session_id).await;
        let session = session_id.to_owned();
        let context = self.call(move |store| store.session_context(&session)).await?;
        if context.session.role() != WriterRole::Builder {
            return Err(rejected("Only Builder sessions admit construction evidence", "command_rejected"));
        }
        if context.session.fields().status != SessionStatus::Active {
            return Err(rejected("Builder session is not active", "command_rejected"));
        }
        let (session, message_id, content) =
            (session_id.to_owned(), evidence.evidence_id.clone(), evidence.payload());
        self.call(move |store| {
            store.append_agent_message(&session, &message_id, SessionMessageKind::Evidence, &content, None)
        })
        .await?;
        Ok(evidence)
    }
}
