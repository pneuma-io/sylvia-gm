//! Confirming a NEW CAMPAIGN and completing its construction: `AuthorityOwner`'s
//! `confirm_new_campaign` and `complete_campaign_construction`.
//!
//! Each makes Python's checks and semantic payload, looks the operation up for a replay, then
//! commits through `sylvia_store::campaign_confirmation`. Beginning construction between the two
//! is `guided::begin_campaign_construction`. The prepared-start fence these share with lane 1's
//! ready starts (`ready_campaign_start_work`, `reject_uncreated_ready_campaign`,
//! `campaign_confirmation`) is on the store handle.
//!
//! Python first copies the caller's lock, participants and state (`_capture_campaign_creation`),
//! catching the `TypeError` or `ValueError` a malformed one raises. Here they arrive as checked
//! domain values, so only the identity and session checks can fail at that point.

use super::guided::{
    campaign_work, coded, operation_payload, package_lock_payload, rejected, validate_campaign_inputs,
};
use sylvia_base::Json;
use sylvia_domain::authority::{
    canonical_payload_is_safe, owned_payload, validate_authority_identity, CampaignConfirmation,
    CampaignConstructionCompleted, CampaignRecord, LifecycleState, PackageLock, Participant, StateValue,
    WriterRole,
};
use sylvia_domain::session::{SessionRecord, SessionRecordFields, SessionStatus, BUILDER_SKILLS};
use sylvia_store::campaign_confirmation::{CompleteConstruction, ConfirmNewCampaign, ReadyStartWork};
use sylvia_store::{OperationResult, StoreHandle, StoreResult};

/// `confirm_new_campaign`'s keywords. The Builder session is all three of its identities or none.
pub struct ConfirmRequest<'a> {
    pub operation_id: &'a str,
    pub display_name: &'a str,
    pub package_lock: &'a PackageLock,
    pub turn_id: &'a str,
    pub package_candidate_id: Option<&'a str>,
    pub builder_session_id: Option<&'a str>,
    pub builder_provider_id: Option<&'a str>,
    pub builder_model_id: Option<&'a str>,
    /// Python's default is `"BUILD CAMPAIGN PLAN"`.
    pub builder_selected_skill: &'a str,
    pub participants: &'a [Participant],
    pub initial_state: &'a [StateValue],
}

/// The confirmation payload's `participants`: each participant's context, by identity.
pub(super) fn participants_payload(participants: &[Participant]) -> Json {
    let mut sorted: Vec<&Participant> = participants.iter().collect();
    sorted.sort_by(|left, right| left.participant_id().cmp(right.participant_id()));
    Json::Array(sorted.into_iter().map(super::actions::participant_context).collect())
}

/// The confirmation payload's `initial_state`: `{namespace, key, value}` by namespace and key.
pub(super) fn initial_state_payload(initial_state: &[StateValue]) -> Json {
    let mut sorted: Vec<&StateValue> = initial_state.iter().collect();
    sorted.sort_by(|left, right| (left.namespace(), left.key()).cmp(&(right.namespace(), right.key())));
    Json::Array(
        sorted
            .into_iter()
            .map(|item| {
                let mut row = sylvia_base::JsonMap::new();
                row.insert("namespace".into(), item.namespace().into());
                row.insert("key".into(), item.key().into());
                row.insert("value".into(), item.value().clone());
                Json::Object(row)
            })
            .collect(),
    )
}

/// The Builder session's three identities, when the request names any of them.
struct BuilderIdentity<'a> {
    session_id: &'a str,
    provider_id: &'a str,
    model_id: &'a str,
}

/// The checks inside Python's `try`, whose `TypeError` or `ValueError` becomes one refusal.
fn checked_identities<'a>(request: &ConfirmRequest<'a>) -> Result<Option<BuilderIdentity<'a>>, ()> {
    for (value, label) in [
        (request.operation_id, "Campaign operation identity"),
        (request.display_name, "Campaign display name"),
        (request.turn_id, "TURN identity"),
    ] {
        validate_authority_identity(value, label, false).map_err(|_| ())?;
    }
    if let Some(candidate) = request.package_candidate_id {
        validate_authority_identity(candidate, "Campaign package candidate identity", false)
            .map_err(|_| ())?;
    }
    let (session, provider, model) =
        (request.builder_session_id, request.builder_provider_id, request.builder_model_id);
    if session.is_none() && provider.is_none() && model.is_none() {
        return Ok(None);
    }
    let (Some(session_id), Some(provider_id), Some(model_id)) = (session, provider, model) else {
        return Err(());
    };
    for (value, label) in [
        (session_id, "Builder session identity"),
        (provider_id, "Builder provider identity"),
        (model_id, "Builder model identity"),
    ] {
        validate_authority_identity(value, label, false).map_err(|_| ())?;
    }
    if !BUILDER_SKILLS.contains(&request.builder_selected_skill) {
        return Err(());
    }
    Ok(Some(BuilderIdentity { session_id, provider_id, model_id }))
}

/// `AuthorityOwner.confirm_new_campaign`: create the stable campaign aggregate before either
/// Builder lane begins. A prepared start already holding the fence passes it as `held`; anything
/// else takes it here.
pub async fn confirm_new_campaign(
    store: &StoreHandle,
    request: &ConfirmRequest<'_>,
    held: Option<&ReadyStartWork>,
) -> StoreResult<CampaignConfirmation> {
    let (_operation, taken) = match held {
        Some(_) => (Some(store.operation()?), None),
        None => (None, Some(store.hold_ready_campaigns().await?)),
    };
    let fence = held.or(taken.as_ref()).expect("one of the two is present");
    fence.assert_not_rejected(request.operation_id)?;
    let builder = checked_identities(request)
        .map_err(|()| rejected("Campaign confirmation authority is invalid", "invalid_authority_identity"))?;
    let mut values = vec![
        ("display_name", Json::from(request.display_name)),
        ("package_lock", package_lock_payload(request.package_lock)),
        ("turn_id", Json::from(request.turn_id)),
    ];
    if !request.participants.is_empty() {
        values.push(("participants", participants_payload(request.participants)));
    }
    if !request.initial_state.is_empty() {
        values.push(("initial_state", initial_state_payload(request.initial_state)));
    }
    if let Some(candidate) = request.package_candidate_id {
        values.push(("package_candidate_id", Json::from(candidate)));
    }
    if let Some(builder) = &builder {
        let mut session = sylvia_base::JsonMap::new();
        session.insert("session_id".into(), builder.session_id.into());
        session.insert("provider_id".into(), builder.provider_id.into());
        session.insert("model_id".into(), builder.model_id.into());
        session.insert("selected_skill".into(), request.builder_selected_skill.into());
        values.push(("builder_session", Json::Object(session)));
    }
    let (semantic_payload, payload_sha256) =
        operation_payload(request.operation_id, "confirm_new_campaign", values);
    let (operation_id, sha) = (request.operation_id.to_owned(), payload_sha256.clone());
    match store
        .call(move |store| store.campaign_operation_hit(&operation_id, "confirm_new_campaign", &sha))
        .await?
    {
        Some(OperationResult::Confirmation(existing)) => return Ok(existing),
        Some(_) => return Err(coded("IdempotencyConflict", "Campaign operation result kind does not match")),
        None => {}
    }
    let (package_lock, participants, initial_state) =
        (request.package_lock.clone(), request.participants.to_vec(), request.initial_state.to_vec());
    store
        .call(move |store| validate_campaign_inputs(store, &package_lock, &participants, &initial_state))
        .await?;
    let campaign = CampaignRecord {
        campaign_id: sylvia_store::interruptions::uuid4()?,
        display_name: request.display_name.to_owned(),
        lifecycle: LifecycleState::Active,
        writer: WriterRole::Builder,
        revision: 0,
    };
    let builder_session = match &builder {
        Some(builder) => Some(SessionRecord::new(SessionRecordFields {
            session_id: builder.session_id.to_owned(),
            campaign_id: Some(campaign.campaign_id.clone()),
            role: WriterRole::Builder,
            provider_id: builder.provider_id.to_owned(),
            model_id: builder.model_id.to_owned(),
            status: SessionStatus::Active,
            selected_skill: Some(request.builder_selected_skill.to_owned()),
            ..SessionRecordFields::default()
        })?),
        None => None,
    };
    let (operation_id, turn_id) = (request.operation_id.to_owned(), request.turn_id.to_owned());
    let (package_lock, participants, initial_state) =
        (request.package_lock.clone(), request.participants.to_vec(), request.initial_state.to_vec());
    store
        .call(move |store| {
            store.confirm_new_campaign(&ConfirmNewCampaign {
                operation_id: &operation_id,
                semantic_payload: &semantic_payload,
                payload_sha256: &payload_sha256,
                campaign: &campaign,
                package_lock: &package_lock,
                turn_id: &turn_id,
                builder_session: builder_session.as_ref(),
                participants: &participants,
                initial_state: &initial_state,
            })
        })
        .await
}

/// `AuthorityOwner.complete_campaign_construction`: the input checks, the semantic payload and
/// replay prelookup, then the store's completion under the campaign's work and owner lane.
pub async fn complete_campaign_construction(
    store: &StoreHandle,
    operation_id: &str,
    campaign_id: &str,
    expected_revision: i64,
    canonical_plan: &Json,
) -> StoreResult<CampaignConstructionCompleted> {
    let _operation = store.operation()?;
    let invalid = || rejected("Campaign construction plan is invalid", "construction_plan_invalid");
    if validate_authority_identity(operation_id, "Campaign operation identity", false).is_err()
        || validate_authority_identity(campaign_id, "Campaign identity", false).is_err()
        || expected_revision < 0
    {
        return Err(invalid());
    }
    let captured = owned_payload(canonical_plan).map_err(|_| invalid())?;
    if !canonical_payload_is_safe(&Json::Object(captured.clone())) {
        return Err(rejected(
            "Campaign construction plan contains a reserved canonical channel",
            "unsafe_canonical_payload",
        ));
    }
    let (semantic_payload, payload_sha256) = operation_payload(
        operation_id,
        "complete_campaign_construction",
        vec![("campaign_id", Json::from(campaign_id)), ("canonical_plan", Json::Object(captured.clone()))],
    );
    let (id, sha) = (operation_id.to_owned(), payload_sha256.clone());
    match store
        .call(move |store| store.campaign_operation_hit(&id, "complete_campaign_construction", &sha))
        .await?
    {
        Some(OperationResult::Completed(existing)) => return Ok(existing),
        Some(_) => return Err(coded("IdempotencyConflict", "Campaign operation result kind does not match")),
        None => {}
    }
    let _work = campaign_work(store, campaign_id).await?;
    let _lane = store.lanes().owner.acquire(campaign_id).await;
    let (operation_id, campaign_id) = (operation_id.to_owned(), campaign_id.to_owned());
    store
        .call(move |store| {
            store.complete_campaign_construction(&CompleteConstruction {
                operation_id: &operation_id,
                semantic_payload: &semantic_payload,
                payload_sha256: &payload_sha256,
                campaign_id: &campaign_id,
                expected_revision,
                canonical_plan: &captured,
            })
        })
        .await
}
