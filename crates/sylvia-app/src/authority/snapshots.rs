//! Campaign snapshots, the application half: `AuthorityOwner.snapshot`,
//! `model_context_snapshot` and `_engine_private_keys`. The store half is
//! `sylvia_store::snapshots`.
//!
//! `model_context_snapshot` hides the state a package declares engine-private (`visibility:
//! engine`). Where the package declares such state, Python also projects the current model
//! decision boundary with lane 2's `decision_scope.py`, `actor_decisions.py` and
//! `package_runtime.package_model_decision_boundary`, which are not ported yet: a campaign with
//! engine-private state is refused as [`StoreError::NotPorted`] until they are. Of the bundled
//! packages only Riichi declares any; a No Ruleset or SRD Lite campaign is answered exactly.

use std::collections::BTreeSet;
use sylvia_domain::authority::{
    CampaignDisplaySnapshot, CampaignSnapshot, ModelDecisionBoundary, PackageLock,
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
            // With no engine-private state the visible state is all of it and Python projects no
            // boundary; with some, the projection is lane 2's (see the module documentation).
            if !engine_private_keys(store, &snapshot.state.package_lock)?.is_empty() {
                return Err(StoreError::NotPorted(
                    "model decision scope projection (lane 2: decision_scope.py, actor_decisions.py, \
                     package_runtime.package_model_decision_boundary)",
                ));
            }
            Ok((snapshot, None))
        })
        .await
}
