//! Admitting a Campaign Plan and handing a campaign from its Builder to its DM, one operation at
//! a time: `AuthorityOwner`'s `admit_campaign_plan`, `campaign_plan`, `handoff_builder_to_dm` and
//! `builder_dm_handoff`, and the prepared and legacy Builder branch of
//! `finalize_campaign_construction`, which runs them in turn (a guided Interview commits them at
//! once instead; `guided`).
//!
//! The store halves are `sylvia_store::plan_handoff`, with `StoreHandle::record_builder_evidence`.

use super::campaign_creation::complete_campaign_construction;
use super::guided::{model_guidance_for_lock, rejected, validate_required_plan_state, FinalizeConstruction};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    validate_authority_identity, CampaignConstructionBasis, CampaignConstructionCompleted, WriterRole,
};
use sylvia_domain::model_capability::model_guidance_payload_sha256;
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::session::{
    builder_dm_handoff_payload_sha256, campaign_plan_payload_sha256, BuilderDMHandoff,
    BuilderDMHandoffRequest, BuilderEvidence, CampaignPlan, CampaignPlanRequest, EvidenceSourceKind,
    PlanAcceptanceStatus, PlanValidationOutcome, SessionMessageKind,
};
use sylvia_store::campaigns::current_lock_record;
use sylvia_store::plan_handoff::CommitHandoff;
use sylvia_store::{StoreError, StoreHandle, StoreResult};

/// `admit_campaign_plan`'s keywords. Its callers pass the plan's outcome and status as values.
pub struct AdmitPlan<'a> {
    pub plan_id: &'a str,
    pub campaign_id: &'a str,
    pub builder_session_id: &'a str,
    pub expected_revision: i64,
    pub expected_package_lock_id: &'a str,
    pub content: &'a JsonMap,
    pub content_sha256: &'a str,
    pub provenance_evidence_ids: &'a [String],
    pub validation_outcome: PlanValidationOutcome,
    pub acceptance_status: PlanAcceptanceStatus,
}

/// `handoff_builder_to_dm`'s keywords.
pub struct HandoffRequest<'a> {
    pub handoff_id: &'a str,
    pub campaign_id: &'a str,
    pub builder_session_id: &'a str,
    pub dm_session_id: &'a str,
    pub dm_provider_id: &'a str,
    pub dm_model_id: &'a str,
    pub expected_writer: WriterRole,
    pub expected_revision: i64,
    pub expected_package_lock_id: &'a str,
    pub expected_package_candidate_id: Option<&'a str>,
    pub plan_id: &'a str,
    pub expected_plan_revision: i64,
    pub expected_plan_content_sha256: &'a str,
    pub completed_turn_id: &'a str,
    pub model_guidance_override: bool,
}

/// The campaign's current lock identity (`self._store.current_package_lock(campaign_id).lock_id`).
async fn current_lock_id(store: &StoreHandle, campaign_id: &str) -> StoreResult<String> {
    let campaign_id = campaign_id.to_owned();
    Ok(store.call(move |store| current_lock_record(&store.connect()?, &campaign_id)).await?.lock_id)
}

/// `AuthorityOwner.model_guidance`: an exact selected model assessed against the campaign's
/// primary Ruleset (the identity check is a `ValueError` Python does not catch).
pub async fn model_guidance(
    store: &StoreHandle,
    campaign_id: &str,
    model_id: &str,
    override_accepted: bool,
) -> StoreResult<sylvia_domain::model_capability::ModelGuidanceDecision> {
    let _operation = store.operation()?;
    validate_authority_identity(campaign_id, "Campaign identity", false)?;
    let lock_id = current_lock_id(store, campaign_id).await?;
    model_guidance_for_lock(store, &lock_id, model_id, override_accepted).await
}

/// `AuthorityOwner.admit_campaign_plan`: admit one exact accepted plan through the campaign's
/// revision authority, under its owner lane.
pub async fn admit_campaign_plan(store: &StoreHandle, admit: &AdmitPlan<'_>) -> StoreResult<CampaignPlan> {
    let _operation = store.operation()?;
    let _lane = store.lanes().owner.acquire(admit.campaign_id).await;
    if admit.content.contains_key("sequel") {
        return Err(rejected("Live adventure additions require explicit sequel review", "command_rejected"));
    }
    let invalid = || rejected("Campaign Plan admission payload is invalid", "invalid_campaign_plan");
    for (value, label, sha256) in [
        (admit.plan_id, "Campaign Plan identity", false),
        (admit.campaign_id, "Campaign identity", false),
        (admit.builder_session_id, "Builder session identity", false),
        (admit.expected_package_lock_id, "Campaign Plan expected package lock identity", false),
        (admit.content_sha256, "Campaign Plan content SHA-256", true),
    ] {
        validate_authority_identity(value, label, sha256).map_err(|_| invalid())?;
    }
    if admit.expected_revision < 0 || admit.provenance_evidence_ids.is_empty() {
        return Err(invalid());
    }
    for evidence_id in admit.provenance_evidence_ids {
        validate_authority_identity(evidence_id, "Campaign Plan provenance evidence", false)
            .map_err(|_| invalid())?;
    }
    if sha256_hex(canonical_json_bytes(&Json::Object(admit.content.clone()))) != admit.content_sha256 {
        return Err(rejected(
            "Campaign Plan content identity does not match",
            "campaign_plan_content_mismatch",
        ));
    }
    let request = CampaignPlanRequest {
        plan_id: admit.plan_id.to_owned(),
        campaign_id: admit.campaign_id.to_owned(),
        source_session_id: admit.builder_session_id.to_owned(),
        expected_revision: admit.expected_revision,
        expected_package_lock_id: admit.expected_package_lock_id.to_owned(),
        content_sha256: admit.content_sha256.to_owned(),
        content: Json::Object(admit.content.clone()),
        provenance_evidence_ids: admit.provenance_evidence_ids.to_vec(),
        validation_outcome: admit.validation_outcome,
        acceptance_status: admit.acceptance_status,
    };
    let payload_sha256 = campaign_plan_payload_sha256(&request)?;
    let plan_id = admit.plan_id.to_owned();
    if let Some(existing) = store.call(move |store| store.campaign_plan_hit(&plan_id)).await? {
        if existing.fields().payload_sha256 != payload_sha256 {
            return Err(super::guided::coded(
                "IdempotencyConflict",
                "Campaign Plan identity payload does not match",
            ));
        }
        return Ok(existing);
    }
    if admit.acceptance_status == PlanAcceptanceStatus::Accepted {
        let lock_id = current_lock_id(store, admit.campaign_id).await?;
        validate_required_plan_state(store, &lock_id, admit.content).await?;
    }
    store.call(move |store| store.commit_campaign_plan(&request, &payload_sha256)).await
}

/// `AuthorityOwner.campaign_plan`.
pub async fn campaign_plan(store: &StoreHandle, plan_id: &str) -> StoreResult<CampaignPlan> {
    let _operation = store.operation()?;
    let plan_id = plan_id.to_owned();
    store
        .call(move |store| store.campaign_plan_hit(&plan_id))
        .await?
        .ok_or_else(|| rejected("Campaign Plan is unavailable", "command_rejected"))
}

/// `AuthorityOwner.builder_dm_handoff`.
pub async fn builder_dm_handoff(store: &StoreHandle, handoff_id: &str) -> StoreResult<BuilderDMHandoff> {
    let _operation = store.operation()?;
    let handoff_id = handoff_id.to_owned();
    store
        .call(move |store| store.handoff_hit(&handoff_id))
        .await?
        .ok_or_else(|| rejected("Builder-to-DM handoff is unavailable", "command_rejected"))
}

/// `AuthorityOwner.handoff_builder_to_dm`: move one existing quiescent campaign from its Builder
/// to its DM, under its owner lane and a reserved campaign transition.
pub async fn handoff_builder_to_dm(
    store: &StoreHandle,
    handoff: &HandoffRequest<'_>,
) -> StoreResult<BuilderDMHandoff> {
    let _operation = store.operation()?;
    let _lane = store.lanes().owner.acquire(handoff.campaign_id).await;
    let invalid = || rejected("Builder-to-DM handoff request is invalid", "invalid_builder_dm_handoff");
    for (value, label) in [
        (handoff.handoff_id, "Builder-to-DM handoff identity"),
        (handoff.campaign_id, "Campaign identity"),
        (handoff.builder_session_id, "Builder session identity"),
        (handoff.dm_session_id, "DM session identity"),
        (handoff.dm_provider_id, "DM provider identity"),
        (handoff.dm_model_id, "DM model identity"),
        (handoff.expected_package_lock_id, "Expected package lock identity"),
        (handoff.plan_id, "Campaign Plan identity"),
        (handoff.completed_turn_id, "Completed TURN identity"),
    ] {
        validate_authority_identity(value, label, false).map_err(|_| invalid())?;
    }
    if let Some(candidate) = handoff.expected_package_candidate_id {
        validate_authority_identity(candidate, "Expected package candidate identity", false)
            .map_err(|_| invalid())?;
    }
    validate_authority_identity(
        handoff.expected_plan_content_sha256,
        "Expected Campaign Plan content SHA-256",
        true,
    )
    .map_err(|_| invalid())?;
    if handoff.expected_revision < 0 || handoff.expected_plan_revision <= 0 {
        return Err(invalid());
    }
    let handoff_id = handoff.handoff_id.to_owned();
    let existing = store.call(move |store| store.handoff_hit(&handoff_id)).await?;
    let guidance = match &existing {
        Some(existing) => existing.fields().model_guidance.clone(),
        None => {
            model_guidance_for_lock(
                store,
                handoff.expected_package_lock_id,
                handoff.dm_model_id,
                handoff.model_guidance_override,
            )
            .await?
        }
    };
    let request = BuilderDMHandoffRequest {
        handoff_id: handoff.handoff_id.to_owned(),
        campaign_id: handoff.campaign_id.to_owned(),
        builder_session_id: handoff.builder_session_id.to_owned(),
        dm_session_id: handoff.dm_session_id.to_owned(),
        dm_provider_id: handoff.dm_provider_id.to_owned(),
        dm_model_id: handoff.dm_model_id.to_owned(),
        expected_writer: handoff.expected_writer,
        expected_revision: handoff.expected_revision,
        expected_package_lock_id: handoff.expected_package_lock_id.to_owned(),
        expected_package_candidate_id: handoff.expected_package_candidate_id.map(str::to_owned),
        plan_id: handoff.plan_id.to_owned(),
        expected_plan_revision: handoff.expected_plan_revision,
        expected_plan_content_sha256: handoff.expected_plan_content_sha256.to_owned(),
        completed_turn_id: handoff.completed_turn_id.to_owned(),
        model_guidance_sha256: model_guidance_payload_sha256(&guidance),
    };
    let payload_sha256 = builder_dm_handoff_payload_sha256(&request);
    if let Some(existing) = existing {
        if existing.fields().payload_sha256 != payload_sha256 {
            return Err(super::guided::coded(
                "IdempotencyConflict",
                "Builder-to-DM handoff identity payload does not match",
            ));
        }
        return Ok(existing);
    }
    if !guidance.may_continue() {
        let code = guidance
            .fields()
            .warning_code
            .clone()
            .unwrap_or_else(|| "model_guidance_confirmation_required".into());
        return Err(rejected("Selected model capability guidance requires explicit confirmation", &code));
    }
    let plan = campaign_plan(store, handoff.plan_id).await?;
    let lock_id = current_lock_id(store, handoff.campaign_id).await?;
    validate_required_plan_state(store, &lock_id, plan.content()).await?;
    let _transition = store.lanes().work.reserve_transition(handoff.campaign_id).await?;
    store
        .call(move |store| {
            store.commit_builder_dm_handoff(&CommitHandoff {
                request: &request,
                model_guidance: &guidance,
                payload_sha256: &payload_sha256,
            })
        })
        .await
}

/// What `finalize_campaign_construction` has settled before its prepared or legacy branch.
pub(super) struct Settled<'a> {
    pub finalize: &'a FinalizeConstruction<'a>,
    pub basis: &'a CampaignConstructionBasis,
    pub lock_id: &'a str,
    pub plan_content: &'a JsonMap,
    pub content_sha256: &'a str,
    pub suffix: &'a str,
    /// A User message of the Builder session names a prepared campaign (`ready_campaign_id`).
    pub prepared_selection: bool,
}

/// Whether a session's messages name a prepared campaign: `prepared_selection`.
pub(super) fn names_prepared_campaign(messages: &[sylvia_domain::session::SessionMessage]) -> bool {
    messages.iter().any(|message| {
        message.kind() == SessionMessageKind::User
            && !matches!(message.content().get("ready_campaign_id"), None | Some(Json::Null))
    })
}

/// The handoff `finalize_campaign_construction` asks for, on `plan` at `expected_revision`.
fn handoff_request<'a>(
    settled: &'a Settled<'_>,
    handoff_id: &'a str,
    dm_session_id: &'a str,
    expected_revision: i64,
    plan: &'a CampaignPlan,
) -> HandoffRequest<'a> {
    let (finalize, basis) = (settled.finalize, settled.basis);
    HandoffRequest {
        handoff_id,
        campaign_id: finalize.campaign_id,
        builder_session_id: &basis.builder_session_id,
        dm_session_id,
        dm_provider_id: finalize.dm_provider_id,
        dm_model_id: finalize.dm_model_id,
        expected_writer: WriterRole::Builder,
        expected_revision,
        expected_package_lock_id: settled.lock_id,
        expected_package_candidate_id: basis.package_candidate_id.as_deref(),
        plan_id: &plan.fields().plan_id,
        expected_plan_revision: plan.fields().plan_revision,
        expected_plan_content_sha256: &plan.fields().content_sha256,
        completed_turn_id: &basis.authority_turn_id,
        model_guidance_override: finalize.model_guidance_override,
    }
}

/// The prepared and legacy Builder branch of `finalize_campaign_construction`: a replay of the
/// saved handoff, or the completion, the plan's evidence, its admission, the construction TURN's
/// completion and the handoff, one operation after another.
pub(super) async fn finalize_prepared(
    store: &StoreHandle,
    settled: &Settled<'_>,
) -> StoreResult<(CampaignConstructionCompleted, CampaignPlan, BuilderDMHandoff)> {
    let (finalize, basis, suffix) = (settled.finalize, settled.basis, settled.suffix);
    let campaign_id = finalize.campaign_id;
    let completion_id = format!("construction-complete-{suffix}");
    let evidence_id = format!("construction-evidence-{suffix}");
    let plan_id = format!("campaign-plan-{suffix}");
    let handoff_id = format!("builder-dm-handoff-{suffix}");
    let dm_session_id = format!("session-dm-{suffix}");
    let existing = match builder_dm_handoff(store, &handoff_id).await {
        Ok(handoff) => Some(handoff),
        Err(StoreError::Coded(error)) if error.error_type == Some("CommandRejected") => None,
        Err(error) => return Err(error),
    };
    if existing.is_some() {
        let (Some(completion_revision), Some(representation)) =
            (basis.completion_revision, basis.canonical_representation_sha256.clone())
        else {
            return Err(rejected("Completed construction receipt basis is unavailable", "command_rejected"));
        };
        let existing_plan = campaign_plan(store, &plan_id).await?;
        if existing_plan.fields().content_sha256 != settled.content_sha256 {
            return Err(rejected("Campaign plan file changed after DM startup", "command_rejected"));
        }
        let completed = CampaignConstructionCompleted {
            campaign_id: campaign_id.to_owned(),
            lane: basis.lane.clone(),
            canonical_representation_sha256: representation,
            resulting_revision: completion_revision,
        };
        let replayed = handoff_builder_to_dm(
            store,
            &handoff_request(
                settled,
                &handoff_id,
                &dm_session_id,
                existing_plan.fields().resulting_campaign_revision,
                &existing_plan,
            ),
        )
        .await?;
        return Ok((completed, existing_plan, replayed));
    }
    let guidance = model_guidance_for_lock(
        store,
        settled.lock_id,
        finalize.dm_model_id,
        finalize.model_guidance_override,
    )
    .await?;
    if !guidance.may_continue() {
        let code = guidance
            .fields()
            .warning_code
            .clone()
            .unwrap_or_else(|| "model_guidance_confirmation_required".into());
        return Err(rejected("Selected model capability guidance requires explicit confirmation", &code));
    }
    let completed = complete_campaign_construction(
        store,
        &completion_id,
        campaign_id,
        basis.begin_revision,
        &Json::Object(settled.plan_content.clone()),
    )
    .await?;
    // Python's third excerpt, for a missing plan file, cannot happen: the text is always given.
    let excerpt = if settled.prepared_selection {
        "Selected bundled prepared Campaign Plan."
    } else {
        "Builder-authored campaign plan file."
    };
    let evidence = BuilderEvidence {
        evidence_id: evidence_id.clone(),
        source_kind: EvidenceSourceKind::UserProvided,
        content_sha256: settled.content_sha256.to_owned(),
        source_locator: None,
        safe_excerpt: excerpt.into(),
        claims: vec!["construction-input".into(), "package-lock".into(), "participant-artifacts".into()],
    };
    store.record_builder_evidence(evidence, &basis.builder_session_id).await?;
    let plan = admit_campaign_plan(
        store,
        &AdmitPlan {
            plan_id: &plan_id,
            campaign_id,
            builder_session_id: &basis.builder_session_id,
            expected_revision: completed.resulting_revision,
            expected_package_lock_id: settled.lock_id,
            content: settled.plan_content,
            content_sha256: settled.content_sha256,
            provenance_evidence_ids: std::slice::from_ref(&evidence_id),
            validation_outcome: PlanValidationOutcome::Valid,
            acceptance_status: PlanAcceptanceStatus::Accepted,
        },
    )
    .await?;
    let (id, turn) = (campaign_id.to_owned(), basis.authority_turn_id.clone());
    if !store.call(move |store| store.turn_is_completed(&id, &turn)).await? {
        super::turns::complete_turn(store, campaign_id, &basis.authority_turn_id).await?;
    }
    let handoff = handoff_builder_to_dm(
        store,
        &handoff_request(
            settled,
            &handoff_id,
            &dm_session_id,
            plan.fields().resulting_campaign_revision,
            &plan,
        ),
    )
    .await?;
    Ok((completed, plan, handoff))
}
