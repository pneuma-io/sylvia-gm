//! The guided campaign interview's application operations: `AuthorityOwner`'s
//! `create_guided_campaign_draft`, `advance_guided_campaign_draft` (with `_next_guided_question`
//! and `_guided_answer_value`), `bind_guided_campaign_basis`, `begin_campaign_construction`,
//! `campaign_construction_basis` and `finalize_campaign_construction` (with
//! `_model_guidance_for_lock` and `_validate_required_plan_state`).
//!
//! Each runs on the owner's store handle: the campaign's work and lanes it holds, the checks and
//! payload identities it computes, then the store operation in `sylvia-store` (`guided`,
//! `guided_advance`, `guided_start`, `guided_finalize`) that commits them.
//!
//! Interview Contributions are resolved with lane 1's `interview_content`
//! (`resolve_interview_content`, on the store as its `ReferenceOwner`) and required starting
//! state is checked with its `plan_state_admission`.
//!
//! Not yet ported, so refused (`StoreError::NotPorted`) rather than run differently: finalizing a
//! construction that is not a guided Interview, and binding participants or initial state (only
//! a saved-library Start supplies them).
//!
//! Python's owner never gives its `AuthorityOwner` a model capability registry, so guidance is
//! assessed against the empty one (`EMPTY_MODEL_CAPABILITY_REGISTRY`).

use crate::bundled_packages::BundledPackages;
use crate::interview_content::{require_active_ruleset, resolve_interview_content};
use crate::plan_state_admission::validate_plan_state;
use std::collections::BTreeSet;
use std::sync::Arc;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    canonical_payload_is_safe, owned_payload, validate_authority_identity, CampaignConfirmation,
    CampaignConstructionBasis, CampaignConstructionBegun, CampaignConstructionCompleted, CampaignRecord,
    GuidedCampaignDraft, LifecycleState, PackageLock, PackageRole, WriterRole,
};
use sylvia_domain::campaign_plan::{
    validate_campaign_plan, CampaignPlanExpectations, CAMPAIGN_PLAN_FORMAT_VERSION,
};
use sylvia_domain::interview_contribution::{
    parse_interview_contribution_bytes, validate_interview_contribution_answers, InterviewContribution,
    INTERVIEW_CONTRIBUTION_BYTE_LIMIT, INTERVIEW_CONTRIBUTION_PATH,
};
use sylvia_domain::model_capability::{
    model_guidance_payload_sha256, ModelCapabilityRegistry, ModelGuidanceDecision,
};
use sylvia_domain::package_library::{campaign_setting_entries, canonical_json_bytes, PackageLockRecord};
use sylvia_domain::pyops::py_eq;
use sylvia_domain::session::{
    builder_dm_handoff_payload_sha256, campaign_plan_payload_sha256, BuilderDMHandoff,
    BuilderDMHandoffRequest, BuilderEvidence, CampaignPlan, CampaignPlanRequest, EvidenceSourceKind,
    PlanAcceptanceStatus, PlanValidationOutcome, SessionMessageKind, SessionRecord, SessionRecordFields,
    SessionStatus,
};
use sylvia_store::campaigns::current_lock_record;
use sylvia_store::guided_advance::AdvanceDraft;
use sylvia_store::guided_finalize::GuidedStartCommit;
use sylvia_store::guided_start::{BeginConstruction, BindBasis};
use sylvia_store::packages::primary_manifest_for_lock;
use sylvia_store::{OperationResult, StoreError, StoreHandle, StoreResult};

/// `_GUIDED_CAMPAIGN_STEPS`.
pub const GUIDED_CAMPAIGN_STEPS: [&str; 11] = [
    "ruleset",
    "setting",
    "plan",
    "ruleset_contribution",
    "setting_contribution",
    "plan_contribution",
    "name",
    "participants",
    "boundaries_preferences",
    "specific_requests",
    "review",
];

/// `_GUIDED_CONTRIBUTION_STEPS` (its keys).
const GUIDED_CONTRIBUTION_STEPS: [&str; 3] =
    ["ruleset_contribution", "setting_contribution", "plan_contribution"];

/// `_MAX_GUIDED_VISIBLE_ANSWER_CHARS`.
const MAX_GUIDED_VISIBLE_ANSWER_CHARS: usize = 131_072;

/// The Interview steps a binding requires by default.
const REQUIRED_INTERVIEW_STEPS: [&str; 7] =
    ["ruleset", "setting", "plan", "name", "participants", "boundaries_preferences", "specific_requests"];

/// `_GUIDED_CAMPAIGN_QUESTIONS`.
fn guided_question(step: &str) -> &'static str {
    match step {
        "ruleset" => "Choose a Ruleset for this campaign, or choose No Ruleset for narrative-only play.",
        "ruleset_contribution" => "Answer the selected Ruleset's campaign setup questions.",
        "setting" => "Choose a compatible Campaign Setting.",
        "setting_contribution" => "Answer the selected Campaign Setting's setup questions.",
        "plan" => "Choose a compatible Campaign Plan.",
        "plan_contribution" => "Answer the selected Campaign Plan's setup questions.",
        "name" => "Review or edit the suggested campaign name.",
        "participants" => "Choose or create the participants required by this campaign.",
        "boundaries_preferences" => "State any hard Boundaries and softer Preferences.",
        "specific_requests" => "Add any Specific Requests, or choose No additional requests.",
        "review" => "Review the spoiler-free campaign summary before choosing Ready to Begin.",
        other => unreachable!("no question for step {other}"),
    }
}

fn step_index(step: &str) -> usize {
    GUIDED_CAMPAIGN_STEPS.iter().position(|item| *item == step).expect("a known step")
}

fn needs_contribution(step: &str) -> bool {
    step == "plan" || GUIDED_CONTRIBUTION_STEPS.contains(&step)
}

/// One of Python's `AuthorityError` classes, by name.
pub(super) fn coded(kind: &'static str, message: &str) -> StoreError {
    StoreError::Coded(sylvia_store::error::authority_coded(kind, message))
}

/// `CommandRejected(message, code=code)`.
pub(super) fn rejected(message: &str, code: &str) -> StoreError {
    sylvia_domain::DomainError::command_rejected_with(message, code, Vec::new()).into()
}

/// The first 24 hex digits of the SHA-256 of `text`: Python's `hexdigest()[:24]`.
fn digest24(text: &str) -> String {
    sha256_hex(text.as_bytes())[..24].to_owned()
}

fn canonical_sha256(value: &Json) -> String {
    sha256_hex(canonical_json_bytes(value))
}

/// `{"operation_id": ..., "operation_kind": ..., **values}` and the SHA-256 of its canonical bytes.
pub(super) fn operation_payload(
    operation_id: &str,
    operation_kind: &str,
    values: Vec<(&str, Json)>,
) -> (JsonMap, String) {
    let mut payload = JsonMap::new();
    payload.insert("operation_id".into(), operation_id.into());
    payload.insert("operation_kind".into(), operation_kind.into());
    for (key, value) in values {
        payload.insert(key.to_owned(), value);
    }
    let sha256 = canonical_sha256(&Json::Object(payload.clone()));
    (payload, sha256)
}

/// `_package_lock_payload`.
fn package_lock_payload(package_lock: &PackageLock) -> Json {
    sylvia_store::guided_start::package_lock_payload(package_lock)
}

/// `{"id", "version", "content_sha256"}` of one lock entry, as a structured Plan binds it.
fn binding(entry: &sylvia_domain::authority::PackageLockEntry) -> Json {
    let mut value = JsonMap::new();
    value.insert("id".into(), entry.package_id().into());
    value.insert("version".into(), entry.version().into());
    value.insert("content_sha256".into(), entry.sha256().into());
    Json::Object(value)
}

/// `CommandRejected("Guided Campaign Draft authority is invalid", code=...)`.
fn invalid_draft_identity() -> StoreError {
    rejected("Guided Campaign Draft authority is invalid", "invalid_authority_identity")
}

/// The application's arguments: `AuthorityOwner.advance_guided_campaign_draft`'s keywords.
pub struct AdvanceAnswer<'a> {
    pub operation_id: &'a str,
    pub campaign_id: &'a str,
    pub builder_session_id: &'a str,
    pub expected_construction_revision: i64,
    pub current_step: &'a str,
    pub visible_answer: &'a str,
    pub answer: &'a Json,
}

/// The application's binding arguments.
pub struct BindRequest<'a> {
    pub operation_id: &'a str,
    pub campaign_id: &'a str,
    pub display_name: &'a str,
    pub package_lock: &'a PackageLock,
    pub turn_id: &'a str,
    pub builder_session_id: &'a str,
    pub package_candidate_id: Option<&'a str>,
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

/// The work `_campaign_work(campaign_id)` registers, held while the operation runs.
pub(crate) async fn campaign_work(
    store: &StoreHandle,
    campaign_id: &str,
) -> StoreResult<sylvia_store::work::CampaignWorkGuard> {
    let owned = campaign_id.to_owned();
    store
        .lanes()
        .work
        .begin(campaign_id, || {
            let campaign_id = owned.clone();
            async move { store.call(move |store| store.campaign_lifecycle(&campaign_id)).await }
        })
        .await
}

/// `AuthorityOwner.create_guided_campaign_draft`: the identity checks, the Draft's
/// campaign identity from the operation, its Builder session and campaign records, then
/// the store's create. `_operation()` only refuses work once the owner is closing (see
/// [`Self::rename_builder_chat`](crate::handle::StoreHandle::rename_builder_chat)).
pub async fn create_guided_campaign_draft(
    store: &StoreHandle,
    operation_id: &str,
    display_name: &str,
    builder_session_id: &str,
    builder_provider_id: &str,
    builder_model_id: &str,
) -> StoreResult<GuidedCampaignDraft> {
    for (value, label) in [
        (operation_id, "Campaign operation identity"),
        (display_name, "Campaign display name"),
        (builder_session_id, "Builder session identity"),
        (builder_provider_id, "Builder provider identity"),
        (builder_model_id, "Builder model identity"),
    ] {
        if validate_authority_identity(value, label, false).is_err() {
            return Err(invalid_draft_identity());
        }
    }
    let campaign_id = format!("campaign-draft-{}", digest24(operation_id));
    let session = SessionRecord::new(SessionRecordFields {
        session_id: builder_session_id.to_owned(),
        campaign_id: Some(campaign_id.clone()),
        role: WriterRole::Builder,
        provider_id: builder_provider_id.to_owned(),
        model_id: builder_model_id.to_owned(),
        status: SessionStatus::Active,
        selected_skill: Some("BUILD CAMPAIGN PLAN".into()),
        ..SessionRecordFields::default()
    })?;
    let campaign = CampaignRecord {
        campaign_id,
        display_name: display_name.to_owned(),
        lifecycle: LifecycleState::Active,
        writer: WriterRole::Builder,
        revision: 0,
    };
    let operation_id = operation_id.to_owned();
    store.call(move |store| store.create_guided_campaign_draft(&operation_id, &campaign, &session)).await
}

/// `AuthorityOwner._guided_answer_value(session_id, step)`: one accepted Interview answer from
/// its canonical User message, as the latest Interview State names it.
async fn guided_answer_value(
    store: &StoreHandle,
    session_id: &str,
    step: &str,
) -> StoreResult<Option<JsonMap>> {
    let session = session_id.to_owned();
    let context = store.call(move |store| store.session_context(&session)).await?;
    let Some(state) = context.messages.iter().rev().find(|message| {
        message.kind() == SessionMessageKind::Evidence
            && message.content().get("kind") == Some(&Json::String("campaign_interview_state".into()))
    }) else {
        return Ok(None);
    };
    let Some(Json::Object(references)) = state.content().get("answers") else { return Ok(None) };
    let Some(Json::String(message_id)) = references.get(step) else { return Ok(None) };
    let message = context
        .messages
        .iter()
        .find(|item| item.message_id() == message_id && item.kind() == SessionMessageKind::User);
    Ok(match message.and_then(|message| message.content().get("interview_value")) {
        Some(Json::Object(value)) => Some(value.clone()),
        _ => None,
    })
}

/// `AuthorityOwner._next_guided_question`: choose content first, then ask required questions
/// from its saved copies. No Ruleset skips the content steps; other steps follow in order.
async fn next_guided_question(
    store: &StoreHandle,
    bundled: &Arc<BundledPackages>,
    session_id: &str,
    current_step: &str,
    answer: &JsonMap,
) -> StoreResult<(&'static str, String)> {
    let ruleset_answer = if current_step == "ruleset" {
        Some(answer.clone())
    } else {
        guided_answer_value(store, session_id, "ruleset").await?
    };
    if let Some(ruleset) = &ruleset_answer {
        if matches!(ruleset.get("candidate_id"), None | Some(Json::Null)) {
            if current_step == "ruleset" {
                return Ok(("name", guided_question("name").to_owned()));
            }
            if current_step == "name" {
                return Ok(("boundaries_preferences", guided_question("boundaries_preferences").to_owned()));
            }
        }
    }
    if needs_contribution(current_step) {
        let mut overrides = JsonMap::new();
        overrides.insert(current_step.to_owned(), Json::Object(answer.clone()));
        for required_step in ["ruleset", "setting", "plan"] {
            if !overrides.contains_key(required_step)
                && guided_answer_value(store, session_id, required_step).await?.is_none()
            {
                return Ok((required_step, guided_question(required_step).to_owned()));
            }
        }
        for step in GUIDED_CONTRIBUTION_STEPS {
            let Some(contribution) =
                guided_contribution_object(store, bundled, session_id, step, Some(&overrides)).await?
            else {
                continue;
            };
            // Later answers are detached by this save, including on an exact retry.
            let precedes_current = step_index(step) < step_index(current_step);
            let previous = if step == current_step {
                Some(answer.clone())
            } else if precedes_current {
                guided_answer_value(store, session_id, step).await?
            } else {
                None
            };
            let expected = canonical_sha256(&Json::Object(contribution.as_payload()));
            if let Some(previous) = previous {
                if previous.get("contribution_sha256") == Some(&Json::String(expected)) {
                    let answers: JsonMap =
                        previous.into_iter().filter(|(key, _)| key != "contribution_sha256").collect();
                    if validate_interview_contribution_answers(&contribution, &Json::Object(answers)).is_ok()
                    {
                        continue;
                    }
                }
            }
            return Ok((step, contribution.question_text()));
        }
        return Ok(("name", guided_question("name").to_owned()));
    }
    let step = GUIDED_CAMPAIGN_STEPS[step_index(current_step) + 1];
    Ok((step, guided_question(step).to_owned()))
}

/// `_GUIDED_CONTRIBUTION_STEPS[step][1]`: the owner kind a Contribution step reads.
fn contribution_owner_kind(step: &str) -> &'static str {
    match step {
        "ruleset_contribution" => "ruleset",
        "setting_contribution" => "campaign_setting",
        _ => "campaign_plan",
    }
}

fn is_candidate_unavailable(error: &StoreError) -> bool {
    error.rejection().is_some_and(|coded| {
        coded.error_type == Some("CommandRejected") && coded.code == "candidate_unavailable"
    })
}

/// `AuthorityOwner._package_interview_contribution`: one hash-listed package sidecar, failing
/// closed when it is malformed.
async fn package_interview_contribution(
    store: &StoreHandle,
    candidate_id: &str,
    owner_kind: &'static str,
    package_id: Option<&str>,
) -> StoreResult<Option<InterviewContribution>> {
    let id = candidate_id.to_owned();
    let candidate = match store.call(move |store| store.package_candidate(&id)).await {
        Ok(candidate) => candidate,
        Err(error) if is_candidate_unavailable(&error) => return Ok(None),
        Err(error) => return Err(error),
    };
    let entries: Vec<_> = candidate
        .package_lock()
        .entries()
        .iter()
        .filter(|entry| {
            (owner_kind == "ruleset" && entry.role() == PackageRole::Primary)
                || (owner_kind == "campaign_setting" && Some(entry.package_id()) == package_id)
        })
        .collect();
    let [entry] = entries.as_slice() else { return Ok(None) };
    let (package, version, sha256) =
        (entry.package_id().to_owned(), entry.version().to_owned(), entry.sha256().to_owned());
    let paths = store.package_file_paths(&package, &version, &sha256, 4_096).await?;
    if !paths.iter().any(|path| path == INTERVIEW_CONTRIBUTION_PATH) {
        return Ok(None);
    }
    let raw = store
        .call(move |store| {
            store.package_file_bytes(
                &package,
                &version,
                &sha256,
                INTERVIEW_CONTRIBUTION_PATH,
                INTERVIEW_CONTRIBUTION_BYTE_LIMIT as i64,
            )
        })
        .await?;
    let Some(raw) = raw else {
        return Err(rejected(
            "Selected artifact Interview Contribution is unavailable",
            "interview_contribution_unavailable",
        ));
    };
    match parse_interview_contribution_bytes(&raw, owner_kind) {
        Ok(contribution) => Ok(Some(contribution)),
        // Python catches `ValueError` here, `ContentShapeError` among them.
        Err(error) if error.rejection().is_some_and(|coded| coded.error_type != Some("CommandRejected")) => {
            Err(rejected(
                &format!("Selected {} Interview Contribution is invalid", owner_kind.replace('_', " ")),
                "invalid_interview_contribution",
            ))
        }
        Err(error) => Err(error.into()),
    }
}

/// `AuthorityOwner.guided_campaign_contribution`: the exact selected artifact's optional
/// restricted question group, resolved by lane 1's `resolve_interview_content`.
async fn guided_campaign_contribution(
    store: &StoreHandle,
    bundled: &Arc<BundledPackages>,
    session_id: &str,
    contribution_step: &str,
    overrides: Option<&JsonMap>,
) -> StoreResult<Option<Json>> {
    if !GUIDED_CONTRIBUTION_STEPS.contains(&contribution_step) {
        return Err(rejected("Campaign Interview Contribution step is unsupported", "command_rejected"));
    }
    let owner_kind = contribution_owner_kind(contribution_step);
    let mut selections = JsonMap::new();
    for step in ["ruleset", "setting", "plan"] {
        if let Some(value) = guided_answer_value(store, session_id, step).await? {
            selections.insert(step.to_owned(), Json::Object(value));
        }
    }
    for (key, value) in overrides.into_iter().flatten() {
        selections.insert(key.clone(), value.clone());
    }
    let bundled = Arc::clone(bundled);
    let content = store
        .call(move |store| {
            // `AuthorityOwner.open` gives the owner `content_storage_root=database_path.resolve().parent`,
            // which `_guided_campaign_contribution` hands on (a saved Plan is read from it).
            resolve_interview_content(
                store,
                &bundled,
                store.database_path().parent(),
                &Json::Object(selections),
            )
        })
        .await?;
    if owner_kind == "campaign_plan" {
        return Ok(content.plan_contribution);
    }
    let Some(candidate) = content.candidate else { return Ok(None) };
    let contribution = package_interview_contribution(
        store,
        candidate.candidate_id(),
        owner_kind,
        content.setting_id.as_deref(),
    )
    .await?;
    Ok(contribution.map(|contribution| Json::Object(contribution.as_payload())))
}

/// `AuthorityOwner._guided_contribution_object`: the contribution, parsed for its owner kind.
async fn guided_contribution_object(
    store: &StoreHandle,
    bundled: &Arc<BundledPackages>,
    session_id: &str,
    contribution_step: &str,
    overrides: Option<&JsonMap>,
) -> StoreResult<Option<InterviewContribution>> {
    let Some(payload) =
        guided_campaign_contribution(store, bundled, session_id, contribution_step, overrides).await?
    else {
        return Ok(None);
    };
    Ok(Some(parse_interview_contribution_bytes(
        &canonical_json_bytes(&payload),
        contribution_owner_kind(contribution_step),
    )?))
}

/// `AuthorityOwner.advance_guided_campaign_draft`: `_operation()` and the campaign's work,
/// the answer checks, the next question, then the store's advance.
pub async fn advance_guided_campaign_draft(
    store: &StoreHandle,
    bundled: &Arc<BundledPackages>,
    advance: &AdvanceAnswer<'_>,
) -> StoreResult<GuidedCampaignDraft> {
    let _work = campaign_work(store, advance.campaign_id).await?;
    let invalid = || rejected("Guided Campaign answer is invalid", "invalid_campaign_interview_answer");
    for (value, label) in [
        (advance.operation_id, "Campaign operation identity"),
        (advance.campaign_id, "Campaign identity"),
        (advance.builder_session_id, "Builder session identity"),
    ] {
        if validate_authority_identity(value, label, false).is_err() {
            return Err(invalid());
        }
    }
    let answerable = &GUIDED_CAMPAIGN_STEPS[..GUIDED_CAMPAIGN_STEPS.len() - 1];
    if !answerable.contains(&advance.current_step)
        || advance.expected_construction_revision < 0
        || sylvia_base::pystr::strip(advance.visible_answer).is_empty()
        || advance.visible_answer.chars().count() > MAX_GUIDED_VISIBLE_ANSWER_CHARS
    {
        return Err(invalid());
    }
    let mut captured = owned_payload(advance.answer).map_err(|_| invalid())?;
    if GUIDED_CONTRIBUTION_STEPS.contains(&advance.current_step) {
        let Some(contribution) = guided_contribution_object(
            store,
            bundled,
            advance.builder_session_id,
            advance.current_step,
            None,
        )
        .await?
        else {
            return Err(rejected(
                "Selected artifact has no Interview Contribution",
                "interview_contribution_unavailable",
            ));
        };
        let mut answers = validate_interview_contribution_answers(&contribution, &Json::Object(captured))
            .map_err(|_| {
                rejected(
                    "Campaign Interview Contribution answer is invalid",
                    "invalid_interview_contribution_answer",
                )
            })?;
        answers.insert(
            "contribution_sha256".into(),
            canonical_sha256(&Json::Object(contribution.as_payload())).into(),
        );
        captured = answers;
    }
    let index = step_index(advance.current_step);
    let (next_step, next_question) =
        next_guided_question(store, bundled, advance.builder_session_id, advance.current_step, &captured)
            .await?;
    let preceding: Vec<&'static str> = GUIDED_CAMPAIGN_STEPS[..index].to_vec();
    let (operation_id, campaign_id, session_id, current_step, visible_answer) = (
        advance.operation_id.to_owned(),
        advance.campaign_id.to_owned(),
        advance.builder_session_id.to_owned(),
        advance.current_step.to_owned(),
        advance.visible_answer.to_owned(),
    );
    let expected = advance.expected_construction_revision;
    store
        .call(move |store| {
            store.advance_guided_campaign_draft(&AdvanceDraft {
                operation_id: &operation_id,
                campaign_id: &campaign_id,
                builder_session_id: &session_id,
                expected_construction_revision: expected,
                current_step: &current_step,
                visible_answer: &visible_answer,
                answer: &captured,
                preceding_steps: &preceding,
                next_step,
                next_question: &next_question,
            })
        })
        .await
}

/// `AuthorityOwner.campaign_construction_basis`: the identity check (a `ValueError` Python
/// does not catch), then the store's read.
pub async fn campaign_construction_basis(
    store: &StoreHandle,
    campaign_id: &str,
) -> StoreResult<CampaignConstructionBasis> {
    validate_authority_identity(campaign_id, "Campaign identity", false)?;
    let campaign_id = campaign_id.to_owned();
    store.call(move |store| store.campaign_construction_basis(&campaign_id)).await
}

/// `AuthorityOwner.bind_guided_campaign_basis`: the binding checks under the campaign's work,
/// the semantic payload, the replay prelookup, the campaign inputs, the required Interview steps
/// (with each Contribution the selected content declares), then the store's binding.
/// Participants and initial state, which only a saved-library Start supplies, are not ported.
pub async fn bind_guided_campaign_basis(
    store: &StoreHandle,
    bundled: &Arc<BundledPackages>,
    bind: &BindRequest<'_>,
) -> StoreResult<CampaignConfirmation> {
    let campaign_id = bind.campaign_id.to_owned();
    let _work = campaign_work(store, &campaign_id).await?;
    let invalid = || rejected("Guided Campaign binding authority is invalid", "invalid_authority_identity");
    for (value, label) in [
        (bind.operation_id, "Campaign operation identity"),
        (bind.campaign_id, "Campaign identity"),
        (bind.display_name, "Campaign display name"),
        (bind.turn_id, "TURN identity"),
        (bind.builder_session_id, "Builder session identity"),
    ] {
        if validate_authority_identity(value, label, false).is_err() {
            return Err(invalid());
        }
    }
    if let Some(candidate) = bind.package_candidate_id {
        if validate_authority_identity(candidate, "Campaign package candidate identity", false).is_err() {
            return Err(invalid());
        }
    }
    let session_id = bind.builder_session_id.to_owned();
    let session = store.call(move |store| store.session_context(&session_id)).await?.session;
    if session.campaign_id() != Some(bind.campaign_id)
        || session.role() != WriterRole::Builder
        || session.fields().status != SessionStatus::Active
    {
        return Err(invalid());
    }
    let fields = session.fields();
    let mut builder = JsonMap::new();
    builder.insert("session_id".into(), fields.session_id.as_str().into());
    builder.insert("provider_id".into(), fields.provider_id.as_str().into());
    builder.insert("model_id".into(), fields.model_id.as_str().into());
    builder.insert(
        "selected_skill".into(),
        fields.selected_skill.as_deref().unwrap_or("BUILD CAMPAIGN PLAN").into(),
    );
    let mut values = vec![
        ("display_name", Json::from(bind.display_name)),
        ("package_lock", package_lock_payload(bind.package_lock)),
        ("turn_id", Json::from(bind.turn_id)),
        ("builder_session", Json::Object(builder)),
    ];
    if let Some(candidate) = bind.package_candidate_id {
        values.push(("package_candidate_id", Json::from(candidate)));
    }
    let (semantic_payload, payload_sha256) =
        operation_payload(bind.operation_id, "confirm_new_campaign", values);
    let (operation_id, sha) = (bind.operation_id.to_owned(), payload_sha256.clone());
    match store
        .call(move |store| store.campaign_operation_hit(&operation_id, "confirm_new_campaign", &sha))
        .await?
    {
        Some(OperationResult::Confirmation(existing)) => return Ok(existing),
        Some(_) => return Err(coded("IdempotencyConflict", "Campaign operation result kind does not match")),
        None => {}
    }
    // `_validate_campaign_inputs`: the primary Ruleset must be active. With no participants and
    // no initial state its other checks read and refuse nothing.
    for entry in bind.package_lock.entries() {
        if entry.role() == PackageRole::Primary {
            require_active_ruleset(entry.package_id())?;
        }
    }
    let mut required: Vec<&str> = REQUIRED_INTERVIEW_STEPS.to_vec();
    let ruleset_answer = guided_answer_value(store, bind.builder_session_id, "ruleset").await?;
    if bind.package_lock.entries().is_empty()
        && ruleset_answer.is_some_and(|answer| matches!(answer.get("candidate_id"), None | Some(Json::Null)))
    {
        required.retain(|step| !matches!(*step, "setting" | "plan" | "participants"));
    }
    for step in GUIDED_CONTRIBUTION_STEPS {
        if guided_contribution_object(store, bundled, bind.builder_session_id, step, None).await?.is_some() {
            required.push(step);
        }
    }
    let (operation_id, campaign_id, display_name, turn_id, session_id) = (
        bind.operation_id.to_owned(),
        bind.campaign_id.to_owned(),
        bind.display_name.to_owned(),
        bind.turn_id.to_owned(),
        bind.builder_session_id.to_owned(),
    );
    let package_lock = bind.package_lock.clone();
    store
        .call(move |store| {
            store.bind_guided_campaign_basis(&BindBasis {
                operation_id: &operation_id,
                semantic_payload: &semantic_payload,
                payload_sha256: &payload_sha256,
                campaign_id: &campaign_id,
                display_name: &display_name,
                package_lock: &package_lock,
                turn_id: &turn_id,
                builder_session_id: &session_id,
                required_interview_steps: &required,
            })
        })
        .await
}

/// `AuthorityOwner.begin_campaign_construction`: the input checks, the semantic payload and
/// replay prelookup, then the store's begin under the campaign's work and owner lane.
pub async fn begin_campaign_construction(
    store: &StoreHandle,
    operation_id: &str,
    campaign_id: &str,
    lane: &str,
    expected_revision: i64,
    canonical_input: &Json,
) -> StoreResult<CampaignConstructionBegun> {
    let invalid = || rejected("Campaign construction input is invalid", "construction_input_invalid");
    if validate_authority_identity(operation_id, "Campaign operation identity", false).is_err()
        || validate_authority_identity(campaign_id, "Campaign identity", false).is_err()
        || !matches!(lane, "quick_start" | "customize")
        || expected_revision < 0
    {
        return Err(invalid());
    }
    let captured = owned_payload(canonical_input).map_err(|_| invalid())?;
    if !canonical_payload_is_safe(&Json::Object(captured.clone())) {
        return Err(rejected(
            "Campaign construction input contains a reserved canonical channel",
            "unsafe_canonical_payload",
        ));
    }
    let (semantic_payload, payload_sha256) = operation_payload(
        operation_id,
        "begin_campaign_construction",
        vec![
            ("campaign_id", Json::from(campaign_id)),
            ("lane", Json::from(lane)),
            ("canonical_input", Json::Object(captured.clone())),
        ],
    );
    let (id, sha) = (operation_id.to_owned(), payload_sha256.clone());
    match store
        .call(move |store| store.campaign_operation_hit(&id, "begin_campaign_construction", &sha))
        .await?
    {
        Some(OperationResult::Begun(existing)) => return Ok(existing),
        Some(_) => return Err(coded("IdempotencyConflict", "Campaign operation result kind does not match")),
        None => {}
    }
    let _work = campaign_work(store, campaign_id).await?;
    let _lane = store.lanes().owner.acquire(campaign_id).await;
    let (operation_id, campaign_id, lane) =
        (operation_id.to_owned(), campaign_id.to_owned(), lane.to_owned());
    store
        .call(move |store| {
            store.begin_campaign_construction(&BeginConstruction {
                operation_id: &operation_id,
                semantic_payload: &semantic_payload,
                payload_sha256: &payload_sha256,
                campaign_id: &campaign_id,
                lane: &lane,
                expected_revision,
                canonical_input: &captured,
            })
        })
        .await
}

/// `AuthorityOwner._model_guidance_for_lock` against the owner's (empty) registry.
async fn model_guidance_for_lock(
    store: &StoreHandle,
    lock_id: &str,
    model_id: &str,
    override_accepted: bool,
) -> StoreResult<ModelGuidanceDecision> {
    let lock_id = lock_id.to_owned();
    let minimum = store
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
    store: &StoreHandle,
    finalize: &FinalizeConstruction<'_>,
) -> StoreResult<(CampaignConstructionCompleted, CampaignPlan, BuilderDMHandoff)> {
    validate_authority_identity(finalize.campaign_id, "Campaign identity", false)?;
    validate_authority_identity(finalize.dm_provider_id, "DM provider identity", false)?;
    validate_authority_identity(finalize.dm_model_id, "DM model identity", false)?;
    let campaign_id = finalize.campaign_id.to_owned();
    let basis = {
        let campaign_id = campaign_id.clone();
        store.call(move |store| store.campaign_construction_basis(&campaign_id)).await?
    };
    let lock: PackageLockRecord = {
        let campaign_id = campaign_id.clone();
        store.call(move |store| current_lock_record(&store.connect()?, &campaign_id)).await?
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
        let candidate = store.call(move |store| store.package_candidate(&candidate_id)).await?;
        if candidate.package_lock() != &lock.package_lock || candidate.closure_sha256() != lock.closure_sha256
        {
            return Err(rejected(
                "Campaign construction package candidate basis is inexact",
                "command_rejected",
            ));
        }
    } else if basis.package_candidate_id.is_some() {
        return Err(rejected("No-rules construction cannot bind a package candidate", "command_rejected"));
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
    // `_validate_required_plan_state`: a Ruleset's required starting state, checked with lane 1's
    // `plan_state_admission` against the exact retained manifest.
    {
        let lock_id = lock.lock_id.clone();
        let manifest =
            store.call(move |store| primary_manifest_for_lock(&store.connect()?, &lock_id)).await?;
        if let Some(manifest) =
            manifest.filter(|manifest| manifest.state_schemas.iter().any(|schema| schema.required))
        {
            let payload = match plan_content.get("campaign_plan") {
                Some(Json::Object(plan)) => plan.clone(),
                _ => JsonMap::new(),
            };
            if let Err(error) = validate_plan_state(&payload, &manifest) {
                if error.python_class() != "ContentShapeError" {
                    return Err(error.into());
                }
                return Err(sylvia_domain::DomainError::command_rejected_with(
                    "Campaign Plan is missing or violates required Ruleset declarations",
                    "plan_required_state_invalid",
                    error.diagnostics().to_vec(),
                )
                .into());
            }
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
        store.call(move |store| store.session_context(&session_id)).await?
    };
    let guided = builder_context.messages.iter().any(|message| {
        message.kind() == SessionMessageKind::Evidence
            && message.content().get("kind") == Some(&Json::String("campaign_interview_state".into()))
    });
    if !guided {
        return Err(StoreError::NotPorted("finalizing a construction that is not a guided Interview"));
    }
    let guidance =
        model_guidance_for_lock(store, &lock.lock_id, finalize.dm_model_id, finalize.model_guidance_override)
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
    let (dm_provider_id, dm_model_id) = (finalize.dm_provider_id.to_owned(), finalize.dm_model_id.to_owned());
    let candidate_id = basis.package_candidate_id.clone();
    let begin_revision = basis.begin_revision;
    let started = store
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
