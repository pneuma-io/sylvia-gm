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

/// What `AuthorityOwner.desktop_snapshot` returns: the complete snapshot, or the display
/// snapshot when `display_only`.
#[derive(Debug, Clone, PartialEq)]
pub enum DesktopSnapshot {
    Full(CampaignSnapshot),
    Display(CampaignDisplaySnapshot),
}

/// `_project_private_tool_inputs`' rewrite of the history: for a package with engine-private
/// state, every action's parameters become `{}`, and so does every tool call's `parameters`
/// (`{**payload, "parameters": {}}`: an existing key keeps its place).
pub(super) fn hide_private_tool_inputs(
    lock: &PackageLock,
    private_keys: &BTreeSet<String>,
    actions: &mut [sylvia_domain::authority::ActionRecord],
    events: &mut [sylvia_domain::authority::CanonicalEvent],
) {
    use sylvia_base::{Json, JsonMap};
    use sylvia_domain::authority::CanonicalEventType;
    let private_packages: BTreeSet<String> = lock
        .entries()
        .iter()
        .filter(|entry| private_keys.iter().any(|key| key.starts_with(&format!("{}.", entry.package_id()))))
        .map(|entry| entry.package_id().to_owned())
        .collect();
    for action in actions {
        if private_packages.contains(&action.package_id) {
            action.parameters = JsonMap::new();
        }
    }
    for event in events {
        let private = matches!(event.safe_payload.get("package_id"), Some(Json::String(id)) if private_packages.contains(id));
        if event.event_type == CanonicalEventType::ToolCall && private {
            event.safe_payload.insert("parameters".into(), Json::Object(JsonMap::new()));
        }
    }
}

/// `AuthorityOwner.desktop_snapshot(campaign_id, display_only=...)`: the snapshot the desktop
/// sees, without package-declared engine state (`visibility: engine`), its mutation history or
/// the tool inputs of a package that has such state.
pub async fn desktop_snapshot(
    store: &StoreHandle,
    campaign_id: &str,
    display_only: bool,
) -> StoreResult<DesktopSnapshot> {
    let campaign_id = campaign_id.to_owned();
    store
        .call(move |store| {
            if display_only {
                let mut snapshot = store.display_snapshot(&campaign_id, &DisplayQuery::default())?;
                let private_keys = engine_private_keys(store, &snapshot.state.package_lock)?;
                hide_private_tool_inputs(
                    &snapshot.state.package_lock,
                    &private_keys,
                    &mut snapshot.actions,
                    &mut snapshot.events,
                );
                snapshot.state.state.retain(|key, _| !private_keys.contains(key));
                return Ok(DesktopSnapshot::Display(snapshot));
            }
            let mut snapshot = store.snapshot(&campaign_id)?;
            let private_keys = engine_private_keys(store, &snapshot.state.package_lock)?;
            hide_private_tool_inputs(
                &snapshot.state.package_lock,
                &private_keys,
                &mut snapshot.actions,
                &mut snapshot.events,
            );
            snapshot.state.state.retain(|key, _| !private_keys.contains(key));
            snapshot
                .mutations
                .retain(|item| !private_keys.contains(&format!("{}.{}", item.namespace, item.key)));
            Ok(DesktopSnapshot::Full(snapshot))
        })
        .await
}
