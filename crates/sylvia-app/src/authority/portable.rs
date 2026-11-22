//! Portable campaign archives, the application half: `AuthorityOwner.export_campaign`. The store
//! half is `sylvia_store::export_writer`.

use std::path::Path;
use sylvia_domain::authority::{validate_authority_identity, CampaignExportResult};
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
