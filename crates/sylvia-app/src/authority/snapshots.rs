//! Campaign snapshots, the application half: `AuthorityOwner.snapshot`,
//! `model_context_snapshot` and `_engine_private_keys`. The store half is
//! `sylvia_store::snapshots`.
//!
//! `model_context_snapshot` hides the state a package declares engine-private (`visibility:
//! engine`). Where the package declares such state, it also projects the current model decision
//! boundary from the full state, with lane 2's `decision_scope::project_model_decision_scope`
//! and, for an isolated participant under the GM, `actor_decisions::public_actor_observation`,
//! as Python does.

use std::collections::BTreeSet;
use sylvia_domain::authority::{
    CampaignDisplaySnapshot, CampaignSnapshot, ModelDecisionBoundary, ModelDecisionOwner, PackageLock,
};
use sylvia_store::snapshots::DisplayQuery;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle, StoreResult};

/// `AuthorityOwner.snapshot(campaign_id)`.
pub async fn snapshot(store: &StoreHandle, campaign_id: &str) -> StoreResult<CampaignSnapshot> {
    let campaign_id = campaign_id.to_owned();
    store.call(move |store| store.snapshot(&campaign_id)).await
}

/// `_engine_private_keys(lock)`: `package_id.key` of every state schema an exact locked package
/// declares with `visibility: engine`.
pub fn engine_private_keys(store: &AuthorityStore, lock: &PackageLock) -> StoreResult<BTreeSet<String>> {
    let mut keys = BTreeSet::new();
    for entry in lock.entries() {
        let manifest = store.exact_package_manifest(entry.package_id(), entry.version(), entry.sha256())?;
        keys.extend(
            manifest
                .state_schemas
                .iter()
                .filter(|item| item.visibility == "engine")
                .map(|item| format!("{}.{}", entry.package_id(), item.key)),
        );
    }
    Ok(keys)
}

/// `AuthorityOwner.model_context_snapshot(campaign_id)`: the display snapshot with its continuity
/// and without engine-private state, read from one revision, and the model decision boundary.
pub async fn model_context_snapshot(
    store: &StoreHandle,
    campaign_id: &str,
) -> StoreResult<(CampaignDisplaySnapshot, Option<ModelDecisionBoundary>)> {
    let campaign_id = campaign_id.to_owned();
    store
        .call(move |store| {
            let query = DisplayQuery { include_continuity: true, ..DisplayQuery::default() };
            let snapshot = store.display_snapshot(&campaign_id, &query)?;
            let private_keys = engine_private_keys(store, &snapshot.state.package_lock)?;
            let mut boundary = None;
            if !private_keys.is_empty() {
                if let Some(declared) =
                    super::content_reads::decision_scope_declaration(store, &snapshot.state)?
                {
                    boundary = project_boundary(&snapshot.state, &declared)?;
                }
            }
            // The boundary is projected from the full state; the model sees only the rest.
            let mut visible = snapshot;
            visible.state.state.retain(|key, _| !private_keys.contains(key));
            Ok((visible, boundary))
        })
        .await
}

/// `except DecisionScopeProjectionError as error: raise CommandRejected(str(error))`.
pub(super) fn projection_rejected(error: sylvia_domain::DomainError) -> StoreError {
    match &error {
        sylvia_domain::DomainError::Diagnosed {
            error_type: "DecisionScopeProjectionError", message, ..
        } => sylvia_domain::DomainError::command_rejected(message.clone()).into(),
        _ => error.into(),
    }
}

/// The projection `model_context_snapshot` makes of a declared decision scope: the current
/// boundary and, when the GM decides for an isolated participant, that participant's public
/// observation in place of its private state, with no eligible actors.
/// `DecisionScopeProjectionError` is Python's `CommandRejected` with its message.
fn project_boundary(
    state: &sylvia_domain::authority::CampaignState,
    declared: &sylvia_base::JsonMap,
) -> StoreResult<Option<ModelDecisionBoundary>> {
    let rejected = projection_rejected;
    let Some(mut boundary) =
        crate::decision_scope::project_model_decision_scope(state, declared).map_err(rejected)?
    else {
        return Ok(None);
    };
    if let Some(sylvia_base::Json::Object(isolated)) = declared.get("isolated_participant") {
        if boundary.owner == ModelDecisionOwner::Gm {
            boundary.actor_state =
                crate::actor_decisions::public_actor_observation(state, isolated).map_err(rejected)?;
            boundary.eligible_actors = Vec::new();
        }
    }
    Ok(Some(boundary))
}
