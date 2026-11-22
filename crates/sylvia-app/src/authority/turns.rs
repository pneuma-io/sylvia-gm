//! TURN reads and writes, the application half: `AuthorityOwner.current_open_turn` and
//! `AuthorityOwner.open_turn`. The store halves are `sylvia_store::actions`.

use sylvia_domain::authority::validate_authority_identity;
use sylvia_store::{StoreHandle, StoreResult};

/// `AuthorityOwner.current_open_turn(campaign_id)`: the exact currently open TURN, if any.
pub async fn current_open_turn(store: &StoreHandle, campaign_id: &str) -> StoreResult<Option<String>> {
    validate_authority_identity(campaign_id, "Campaign identity", false)?;
    let campaign_id = campaign_id.to_owned();
    store.call(move |store| store.current_open_turn(&campaign_id)).await
}

/// `AuthorityOwner.open_turn(campaign_id, turn_id)`: one new TURN after the previous one reached
/// a checkpoint, under the campaign's work and refused while a mechanics choice is pending.
///
/// Declared reduction until the mechanics-choice family lands, as for the lifecycle operations:
/// `_reject_pending_mechanics_choice` asks only whether a pending row exists.
pub async fn open_turn(store: &StoreHandle, campaign_id: &str, turn_id: &str) -> StoreResult<()> {
    validate_authority_identity(campaign_id, "Campaign identity", false)?;
    validate_authority_identity(turn_id, "TURN identity", false)?;
    let _work = super::guided::campaign_work(store, campaign_id).await?;
    let owned = campaign_id.to_owned();
    if store.call(move |store| store.mechanics_choice_is_pending(&owned)).await? {
        return Err(super::guided::rejected(
            "Campaign is awaiting a mechanics choice",
            "mechanics_choice_pending",
        ));
    }
    let (campaign_id, turn_id) = (campaign_id.to_owned(), turn_id.to_owned());
    store.call(move |store| store.open_turn(&campaign_id, &turn_id)).await
}
