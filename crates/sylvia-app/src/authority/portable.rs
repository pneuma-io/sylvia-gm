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

/// `AuthorityOwner.export_package_candidate(candidate_id, target)`: the exact installed closure
/// written to `target` as a deterministic portable package archive, rebuilt from the admitted
/// manifests and files through lane 1's `materialize_package_archive`. A closure that does not
/// rebuild exactly is refused, so a different package never leaves under the candidate's name.
/// The reads run on the store thread; the archive is built and written outside it, as Python
/// builds it outside `_operation()`. A failed write is the target's uncaught `OSError`.
pub async fn export_package_candidate(
    store: &StoreHandle,
    candidate_id: &str,
    target: &mut dyn std::io::Write,
) -> StoreResult<sylvia_base::JsonMap> {
    use sylvia_base::Json;
    use sylvia_domain::package_library::{parse_canonical_json_bytes, JsonShapeLimits};
    let id = candidate_id.to_owned();
    let (candidate, drafts) = store
        .call(move |store| {
            let candidate = store.package_candidate(&id)?;
            let mut drafts = Vec::new();
            for entry in candidate.package_lock().entries() {
                let manifest =
                    store.exact_package_manifest(entry.package_id(), entry.version(), entry.sha256())?;
                let parsed = parse_canonical_json_bytes(
                    manifest.canonical_json.as_bytes(),
                    "Package manifest",
                    JsonShapeLimits::NONE,
                )?;
                let Json::Object(mut record) = parsed else {
                    return Err(rejected("Installed package manifest is unavailable", "command_rejected"));
                };
                record.shift_remove("files");
                record.shift_remove("content_sha256");
                let mut payloads = Vec::new();
                for file in &manifest.files {
                    let value = store.package_file_bytes(
                        entry.package_id(),
                        entry.version(),
                        entry.sha256(),
                        file.path(),
                        file.byte_count(),
                    )?;
                    let Some(value) = value else {
                        return Err(rejected(
                            "Installed package file is unavailable",
                            "package_file_unavailable",
                        ));
                    };
                    payloads.push((file.path().to_owned(), value));
                }
                drafts.push(crate::package_materialization::NormalizedPackageDraft::new(
                    record,
                    payloads,
                    manifest.format_version,
                ));
            }
            Ok((candidate, drafts))
        })
        .await?;
    let materialized = crate::package_materialization::materialize_package_archive(&drafts)?;
    if materialized.package_lock != *candidate.package_lock()
        || materialized.closure_sha256 != candidate.closure_sha256()
    {
        return Err(rejected("Installed package closure could not be reproduced", "package_export_inexact"));
    }
    let uncaught = |error: std::io::Error| {
        StoreError::from(sylvia_domain::DomainError::uncaught("OSError", error.to_string()))
    };
    for chunk in materialized.archive.chunks(64 * 1024) {
        target.write_all(chunk).map_err(uncaught)?;
    }
    let mut result = sylvia_base::JsonMap::new();
    result.insert("candidate_id".into(), candidate_id.into());
    result.insert("closure_sha256".into(), candidate.closure_sha256().into());
    result.insert("archive_sha256".into(), sylvia_base::hash::sha256_hex(&materialized.archive).into());
    result.insert("byte_count".into(), (materialized.archive.len() as i64).into());
    Ok(result)
}

/// `CommandRejected(message, code=code)`.
fn rejected(message: &str, code: &str) -> StoreError {
    sylvia_base::CodedError::new(code, message).with_error_type("CommandRejected").into()
}
