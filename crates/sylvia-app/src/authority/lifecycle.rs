//! Reversible campaign lifecycle operations, the application half: `AuthorityOwner.
//! rename_campaign`, `archive_campaign`, `restore_archived_campaign`, `restore_trashed_campaign`
//! and `trash_campaign`, all through `_lifecycle_operation`. The store half is
//! `sylvia_store::lifecycle`.
//!
//! `_lifecycle_operation` refuses a campaign awaiting a mechanics choice, checks every identity,
//! looks for the receipt before and after taking the campaign's owner lane, reserves the
//! campaign's transition (refused, not queued, while campaign work runs) and commits.
//! `_operation()` only refuses work once the owner is closing (see
//! [`StoreHandle::rename_builder_chat`]).

use super::guided::{coded, operation_payload, rejected};
use sylvia_base::Json;
use sylvia_domain::authority::{validate_authority_identity, CampaignLifecycleResult};
use sylvia_store::lifecycle::LifecycleCommit;
use sylvia_store::{OperationResult, StoreHandle, StoreResult};

/// `AuthorityOwner.rename_campaign`.
pub async fn rename_campaign(
    store: &StoreHandle,
    operation_id: &str,
    campaign_id: &str,
    display_name: &str,
    expected_revision: i64,
) -> StoreResult<CampaignLifecycleResult> {
    lifecycle_operation(
        store,
        operation_id,
        "rename_campaign",
        campaign_id,
        expected_revision,
        Some(display_name),
        None,
    )
    .await
}

/// `AuthorityOwner.archive_campaign`.
pub async fn archive_campaign(
    store: &StoreHandle,
    operation_id: &str,
    campaign_id: &str,
    expected_revision: i64,
    designated_session_id: Option<&str>,
) -> StoreResult<CampaignLifecycleResult> {
    lifecycle_operation(
        store,
        operation_id,
        "archive_campaign",
        campaign_id,
        expected_revision,
        None,
        designated_session_id,
    )
    .await
}

/// `AuthorityOwner.restore_archived_campaign`.
pub async fn restore_archived_campaign(
    store: &StoreHandle,
    operation_id: &str,
    campaign_id: &str,
    expected_revision: i64,
    session_id: Option<&str>,
) -> StoreResult<CampaignLifecycleResult> {
    lifecycle_operation(
        store,
        operation_id,
        "restore_campaign",
        campaign_id,
        expected_revision,
        None,
        session_id,
    )
    .await
}

/// `AuthorityOwner.restore_trashed_campaign`.
pub async fn restore_trashed_campaign(
    store: &StoreHandle,
    operation_id: &str,
    campaign_id: &str,
    expected_revision: i64,
) -> StoreResult<CampaignLifecycleResult> {
    lifecycle_operation(store, operation_id, "restore_campaign", campaign_id, expected_revision, None, None)
        .await
}

/// `AuthorityOwner.trash_campaign`.
pub async fn trash_campaign(
    store: &StoreHandle,
    operation_id: &str,
    campaign_id: &str,
    expected_revision: i64,
    designated_session_id: Option<&str>,
) -> StoreResult<CampaignLifecycleResult> {
    lifecycle_operation(
        store,
        operation_id,
        "trash_campaign",
        campaign_id,
        expected_revision,
        None,
        designated_session_id,
    )
    .await
}

/// The receipt prelookup: a lifecycle receipt, or a conflict for any other kind.
async fn receipt(
    store: &StoreHandle,
    operation_id: &str,
    operation_kind: &str,
    payload_sha256: &str,
) -> StoreResult<Option<CampaignLifecycleResult>> {
    let (id, kind, sha) = (operation_id.to_owned(), operation_kind.to_owned(), payload_sha256.to_owned());
    match store.call(move |store| store.campaign_operation_hit(&id, &kind, &sha)).await? {
        None => Ok(None),
        Some(OperationResult::Lifecycle(existing)) => Ok(Some(existing)),
        Some(_) => Err(coded("IdempotencyConflict", "Campaign operation result kind does not match")),
    }
}

/// `AuthorityOwner._lifecycle_operation`.
async fn lifecycle_operation(
    store: &StoreHandle,
    operation_id: &str,
    operation_kind: &'static str,
    campaign_id: &str,
    expected_revision: i64,
    display_name: Option<&str>,
    designated_session_id: Option<&str>,
) -> StoreResult<CampaignLifecycleResult> {
    let owned = campaign_id.to_owned();
    if store.call(move |store| store.mechanics_choice_is_pending(&owned)).await? {
        return Err(rejected("Campaign is awaiting a mechanics choice", "mechanics_choice_pending"));
    }
    let identities_valid = validate_authority_identity(operation_id, "Campaign operation identity", false)
        .is_ok()
        && validate_authority_identity(campaign_id, "Campaign identity", false).is_ok()
        && display_name
            .is_none_or(|name| validate_authority_identity(name, "Campaign display name", false).is_ok())
        && designated_session_id.is_none_or(|session| {
            validate_authority_identity(session, "Designated campaign session identity", false).is_ok()
        })
        && expected_revision >= 0;
    if !identities_valid {
        return Err(rejected("Campaign lifecycle identity is invalid", "command_rejected"));
    }
    let mut values = vec![("campaign_id", Json::from(campaign_id))];
    if let Some(name) = display_name {
        values.push(("display_name", Json::from(name)));
    }
    if matches!(operation_kind, "archive_campaign" | "restore_campaign") {
        values.push(("designated_session_id", designated_session_id.map_or(Json::Null, Json::from)));
    }
    let (semantic_payload, payload_sha256) = operation_payload(operation_id, operation_kind, values);
    if let Some(existing) = receipt(store, operation_id, operation_kind, &payload_sha256).await? {
        return Ok(existing);
    }
    let _lane = store.lanes().owner.acquire(campaign_id).await;
    if let Some(existing) = receipt(store, operation_id, operation_kind, &payload_sha256).await? {
        return Ok(existing);
    }
    let _transition = store.lanes().work.reserve_transition(campaign_id).await?;
    let (operation_id, campaign_id) = (operation_id.to_owned(), campaign_id.to_owned());
    let (display_name, designated_session_id) =
        (display_name.map(str::to_owned), designated_session_id.map(str::to_owned));
    store
        .call(move |store| {
            store.commit_lifecycle_operation(&LifecycleCommit {
                operation_id: &operation_id,
                operation_kind,
                semantic_payload: &semantic_payload,
                payload_sha256: &payload_sha256,
                campaign_id: &campaign_id,
                expected_revision,
                display_name: display_name.as_deref(),
                designated_session_id: designated_session_id.as_deref(),
            })
        })
        .await
}
