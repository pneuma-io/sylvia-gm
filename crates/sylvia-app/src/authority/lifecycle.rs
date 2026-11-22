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
//!
//! `AuthorityOwner.purge_campaign` and `campaign_purge_receipt_exists` are here too: a purge is
//! the irreversible end of the same lifecycle, under the same lane and transition reservation.

use super::guided::{coded, operation_payload, rejected};
use sylvia_base::Json;
use sylvia_domain::authority::{validate_authority_identity, CampaignLifecycleResult, CampaignPurgeResult};
use sylvia_store::lifecycle::LifecycleCommit;
use sylvia_store::purge::{PurgeCommand, PURGE_OUTCOME_UNCERTAIN};
use sylvia_store::{AuthorityStore, OperationResult, StoreHandle, StoreResult};

/// The external half of a purge, prepared before the store commits and rolled back if it fails:
/// Python's `prepare_external_purge` and `rollback_external_purge` callbacks.
pub type ExternalPurgeStep<'a> = &'a mut (dyn FnMut() -> StoreResult<()> + Send);

/// The purge receipt prelookup: a purge receipt, or a conflict for any other kind.
async fn purge_receipt(
    store: &StoreHandle,
    operation_id: &str,
    payload_sha256: &str,
) -> StoreResult<Option<CampaignPurgeResult>> {
    let (id, sha) = (operation_id.to_owned(), payload_sha256.to_owned());
    match store.call(move |store| store.campaign_operation_hit(&id, "purge_campaign", &sha)).await? {
        None => Ok(None),
        Some(OperationResult::Purge(existing)) => Ok(Some(existing)),
        Some(_) => Err(coded("IdempotencyConflict", "Campaign operation result kind does not match")),
    }
}

/// `AuthorityOwner.purge_campaign`: permanently delete one tombstoned campaign after the exact
/// confirmation `permanently purge <campaign_id>`. `prepare_external` runs after the store's
/// preflight and before its commit; `rollback_external` runs if the commit then fails, unless
/// its outcome is uncertain.
pub async fn purge_campaign(
    store: &StoreHandle,
    operation_id: &str,
    campaign_id: &str,
    expected_revision: i64,
    confirmation: &str,
    mut prepare_external: Option<ExternalPurgeStep<'_>>,
    mut rollback_external: Option<ExternalPurgeStep<'_>>,
) -> StoreResult<CampaignPurgeResult> {
    let identities_valid = validate_authority_identity(operation_id, "Campaign operation identity", false)
        .is_ok()
        && validate_authority_identity(campaign_id, "Campaign identity", false).is_ok()
        && validate_authority_identity(confirmation, "Campaign purge confirmation", false).is_ok()
        && expected_revision >= 0;
    if !identities_valid {
        return Err(rejected("Campaign purge identity is invalid", "command_rejected"));
    }
    let (semantic_payload, payload_sha256) = operation_payload(
        operation_id,
        "purge_campaign",
        vec![("campaign_id", Json::from(campaign_id)), ("confirmation", Json::from(confirmation))],
    );
    if let Some(existing) = purge_receipt(store, operation_id, &payload_sha256).await? {
        return Ok(existing);
    }
    if confirmation != format!("permanently purge {campaign_id}") {
        return Err(rejected("Campaign purge confirmation is not exact", "command_rejected"));
    }
    let _lane = store.lanes().owner.acquire(campaign_id).await;
    if let Some(existing) = purge_receipt(store, operation_id, &payload_sha256).await? {
        return Ok(existing);
    }
    let owned = campaign_id.to_owned();
    if store.call(move |store| store.mechanics_choice_is_pending(&owned)).await? {
        return Err(rejected("Campaign is awaiting a mechanics choice", "mechanics_choice_pending"));
    }
    let _transition = store.lanes().work.reserve_transition(campaign_id).await?;
    let mut external_prepared = false;
    let (operation_id, campaign_id, confirmation) =
        (operation_id.to_owned(), campaign_id.to_owned(), confirmation.to_owned());
    let command = move |store: &AuthorityStore, commit: bool| {
        let command = PurgeCommand {
            operation_id: &operation_id,
            semantic_payload: &semantic_payload,
            payload_sha256: &payload_sha256,
            campaign_id: &campaign_id,
            expected_revision,
            confirmation: &confirmation,
        };
        if commit {
            store.commit_campaign_purge(&command).map(Some)
        } else {
            store.preflight_campaign_purge(&command).map(|()| None)
        }
    };
    let preflight = command.clone();
    let mut outcome = store.call(move |store| preflight(store, false)).await;
    if outcome.is_ok() {
        if let Some(prepare) = prepare_external.as_mut() {
            outcome = prepare().map(|()| None);
            external_prepared = outcome.is_ok();
        }
    }
    if outcome.is_ok() {
        outcome = store.call(move |store| command(store, true)).await;
    }
    match outcome {
        Ok(Some(result)) => Ok(result),
        Ok(None) => unreachable!("the commit always returns a receipt"),
        Err(error) => {
            let uncertain = error.rejection().is_some_and(|coded| coded.code == PURGE_OUTCOME_UNCERTAIN);
            if external_prepared && !uncertain {
                if let Some(rollback) = rollback_external.as_mut() {
                    rollback()?;
                }
            }
            Err(error)
        }
    }
}

/// `AuthorityOwner.campaign_purge_receipt_exists`, on the store thread: whether this exact
/// operation durably purged this campaign. The owner's launch reconciliation asks it of each
/// Builder-source purge journal.
pub fn campaign_purge_receipt_exists(
    store: &AuthorityStore,
    operation_id: &str,
    campaign_id: &str,
) -> StoreResult<bool> {
    if validate_authority_identity(operation_id, "Campaign operation identity", false).is_err()
        || validate_authority_identity(campaign_id, "Campaign identity", false).is_err()
    {
        return Err(rejected("Campaign purge identity is invalid", "command_rejected"));
    }
    let (_, payload_sha256) = operation_payload(
        operation_id,
        "purge_campaign",
        vec![
            ("campaign_id", Json::from(campaign_id)),
            ("confirmation", Json::from(format!("permanently purge {campaign_id}"))),
        ],
    );
    match store.campaign_operation_hit(operation_id, "purge_campaign", &payload_sha256)? {
        None => Ok(false),
        Some(OperationResult::Purge(_)) => Ok(true),
        Some(_) => Err(coded("IdempotencyConflict", "Campaign operation result kind does not match")),
    }
}

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
