//! An opted-in actor's decision through the owner: `AuthorityOwner.actor_decision_request` and
//! `actor_decision_command`, which the GM turn's `run_required_actor_decision` (lane 4) calls.
//!
//! The request reads one isolated participant's observation and legal offers from the current
//! display snapshot, through the package's decision scope (lane 1's `decision_scope` and
//! `actor_decisions`); the command binds an offered identity to that actor as an action, never
//! accepting model-supplied arguments.

use super::actions::ActionError;
use super::content_reads::{catalog_on, decision_scope_declaration};
use crate::actor_decisions::{project_actor_decision, ActorDecisionRequest};
use crate::decision_scope::project_model_decision_scope;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{ActionCommand, LifecycleState, PackageRole, TurnStatus, WriterRole};
use sylvia_domain::DomainError;
use sylvia_store::snapshots::DisplayQuery;
use sylvia_store::{StoreError, StoreHandle, StoreResult};

fn rejected(message: &str) -> ActionError {
    DomainError::command_rejected(message).into()
}

/// What the store call answers: `None` for a campaign that declares no isolated participant, or
/// the projection's own result.
type Projected = Result<Option<ActorDecisionRequest>, ActionError>;

/// `AuthorityOwner.actor_decision_request(campaign_id, turn_id=...)`: one opted-in actor's
/// observation and legal offers from the current authority revision.
pub async fn actor_decision_request(
    store: &StoreHandle,
    campaign_id: &str,
    turn_id: &str,
) -> Result<Option<ActorDecisionRequest>, ActionError> {
    let _operation = store.operation()?;
    let (campaign, turn) = (campaign_id.to_owned(), turn_id.to_owned());
    // `self._catalog(campaign_id)` checks the campaign is not transitioning first; it runs only
    // past the declaration and turn checks, as in Python.
    let transitioning = store.lanes().work.ensure_not_transitioning(campaign_id).err();
    let projected: StoreResult<Projected> = store
        .call(move |store| {
            let snapshot = store.display_snapshot(&campaign, &DisplayQuery::default())?;
            let Some(declared) = decision_scope_declaration(store, &snapshot.state)? else {
                return Ok(Ok(None));
            };
            let Some(participant) = declared.get("isolated_participant") else { return Ok(Ok(None)) };
            let open = snapshot.turns.iter().any(|(id, status)| *id == turn && *status == TurnStatus::Open);
            if snapshot.state.lifecycle != LifecycleState::Active
                || snapshot.state.writer != WriterRole::Dm
                || !open
            {
                return Ok(Err(rejected("Isolated participant turn is unavailable")));
            }
            let boundary = match project_model_decision_scope(&snapshot.state, &declared) {
                Ok(boundary) => boundary,
                Err(error) => return Ok(Err(error.into())),
            };
            let Some(primary) = snapshot
                .state
                .package_lock
                .entries()
                .iter()
                .find(|entry| entry.role() == PackageRole::Primary)
            else {
                return Ok(Err(StoreError::Other("StopIteration".into()).into()));
            };
            if let Some(error) = transitioning {
                return Ok(Err(error.into()));
            }
            let mut schemas = JsonMap::new();
            for tool in catalog_on(store, &campaign, false, false)? {
                if tool.package_id == primary.package_id() && tool.package_sha256 == primary.sha256() {
                    schemas.insert(tool.name.clone(), Json::Object(tool.input_schema.clone()));
                }
            }
            let participant = match participant {
                Json::Object(map) => map.clone(),
                _ => {
                    return Ok(Err(
                        DomainError::type_error("Isolated participant declaration is invalid").into()
                    ))
                }
            };
            Ok(project_actor_decision(&snapshot.state, boundary.as_ref(), &participant, &turn, &schemas)
                .map_err(ActionError::from))
        })
        .await;
    projected?
}

/// `AuthorityOwner.actor_decision_command(request, choice_id=..., session_id=..., owner_id=...,
/// provider_id=..., model_id=...)`: bind an offered identity to the current actor.
pub async fn actor_decision_command(
    store: &StoreHandle,
    request: &ActorDecisionRequest,
    choice_id: &str,
    session_id: &str,
    owner_id: &str,
    provider_id: Option<&str>,
    model_id: Option<&str>,
) -> Result<ActionCommand, ActionError> {
    let _operation = store.operation()?;
    let current = actor_decision_request(store, &request.campaign_id, &request.turn_id).await?;
    if current.as_ref() != Some(request) {
        return Err(StoreError::Coded(sylvia_store::error::authority_coded(
            "StaleRevision",
            "Isolated participant decision is stale",
        ))
        .into());
    }
    let id = session_id.to_owned();
    let session = store.call(move |store| store.session_activity_basis(&id, None)).await?.session;
    let fields = session.fields();
    if session.campaign_id() != Some(request.campaign_id.as_str())
        || session.role() != WriterRole::Dm
        || fields.status.as_str() != "active"
        || provider_id.is_some_and(|provider| fields.provider_id != provider)
        || model_id.is_some_and(|model| fields.model_id != model)
    {
        return Err(rejected("Isolated participant session changed"));
    }
    let Some(choice) = request.actions.iter().find(|action| action.identity == choice_id) else {
        return Err(rejected("Isolated participant choice is not offered"));
    };
    let identity = format!("actor-action-{}", request.decision_id);
    Ok(ActionCommand {
        campaign_id: request.campaign_id.clone(),
        action_id: identity.clone(),
        idempotency_key: identity,
        owner_id: owner_id.to_owned(),
        writer: WriterRole::Dm,
        expected_revision: request.revision,
        turn_id: request.turn_id.clone(),
        package_id: request.package_id.clone(),
        tool_name: choice.operation.clone(),
        parameters: choice.arguments.clone(),
        initiating_session_id: Some(session_id.to_owned()),
    })
}
