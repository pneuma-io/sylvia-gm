//! Portable campaign archives, the application half: `AuthorityOwner.export_campaign` and
//! `AuthorityOwner.recover_campaign`. The store halves are `sylvia_store::export_writer`,
//! `sylvia_store::recovery_staging` and `sylvia_store::recovery_graph`.

use std::path::Path;
use std::sync::Arc;
use sylvia_domain::authority::{validate_authority_identity, CampaignExportResult};
use sylvia_store::recovery::CampaignRecoveryResult;
use sylvia_store::recovery_staging::stage_campaign_archive_blocking;
use sylvia_store::{StoreError, StoreHandle, StoreResult};

/// `AuthorityOwner.export_campaign(operation_id, campaign_id, target, include_thinking=...)`:
/// both identities checked (a `ValueError`), then the store publishes one self-contained archive
/// at `target`. The operation identity is checked but not recorded, as in Python.
pub async fn export_campaign(
    store: &StoreHandle,
    operation_id: &str,
    campaign_id: &str,
    target: &Path,
    include_thinking: bool,
) -> StoreResult<CampaignExportResult> {
    validate_authority_identity(operation_id, "Operation id", false)?;
    validate_authority_identity(campaign_id, "Campaign id", false)?;
    let (campaign_id, target) = (campaign_id.to_owned(), target.to_path_buf());
    store
        .call(move |store| {
            store.export_campaign(&campaign_id, &target, include_thinking).map_err(StoreError::from)
        })
        .await
}

/// `AuthorityOwner.recover_campaign(recovery_id=..., source=...)`: the identity checked (a
/// `ValueError`), the source staged (`stage_campaign_archive`), the receipt looked up without the
/// campaign's lane, then the store's recovery under the lane. The staged spools are deleted when
/// this returns, as Python's context closes them.
pub async fn recover_campaign(
    store: &StoreHandle,
    recovery_id: &str,
    source: &Path,
) -> StoreResult<CampaignRecoveryResult> {
    validate_authority_identity(recovery_id, "Recovery identity", false)?;
    let staged = Arc::new(stage_campaign_archive_blocking(source.to_path_buf()).await?);
    let (id, lookup) = (recovery_id.to_owned(), Arc::clone(&staged));
    if let Some(replay) = store.call(move |store| store.campaign_recovery_hit(&id, &lookup)).await? {
        return Ok(replay);
    }
    let _lane = store.lanes().owner.acquire(&staged.campaign_id).await;
    let (id, recover) = (recovery_id.to_owned(), Arc::clone(&staged));
    store.call(move |store| store.recover_campaign(&id, &recover)).await
}
