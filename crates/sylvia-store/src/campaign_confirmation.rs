//! Confirming a NEW CAMPAIGN and completing its construction, in the store
//! (`SQLiteAuthorityStore.confirm_new_campaign`, `complete_campaign_construction` and
//! `campaign_confirmation`), and the owner's prepared-start fence
//! (`AuthorityOwner._ready_campaign_lock` and `_rejected_ready_campaign_operations`, with
//! `ready_campaign_start_work`, `_assert_campaign_creation_not_rejected`,
//! `reject_uncreated_ready_campaign` and `campaign_confirmation`).
//!
//! The application halves are `sylvia_app::authority::campaign_creation`. Beginning construction
//! is `guided_start`, which the guided Interview shares.
//!
//! Python's `_before_construction_commit` is a fault-injection hook that does nothing outside its
//! tests, so nothing runs in its place.

use crate::actions::{dump, load_payload};
use crate::campaigns::{named_rows, Named};
use crate::error::{StoreError, StoreResult};
use crate::guided_start::{
    campaign_operation_hit_on, construction_basis, initial_owner, insert_campaign_lock,
    insert_campaign_operation, participant_adaptation_admitted_on, NewOperation,
};
use crate::handle::StoreHandle;
use crate::owner_lifecycle::OperationGuard;
use crate::packages::verify_published_lock;
use crate::sessions::insert_session;
use crate::store::AuthorityStore;
use crate::stored_construction::{operation_result, OperationResult};
use rusqlite::params;
use rusqlite::types::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    validate_authority_identity, CampaignConfirmation, CampaignConstructionCompleted, CampaignRecord,
    PackageLock, Participant, ParticipantValidationStatus, StateValue, WriterRole,
};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::session::{SessionRecord, SessionStatus};
use tokio::sync::{Mutex, OwnedMutexGuard};

fn coded(kind: &'static str, message: &str) -> StoreError {
    StoreError::Coded(crate::error::authority_coded(kind, message))
}

fn rejected(message: &str, code: &str) -> StoreError {
    crate::actions::rejected(message, code)
}

/// `AuthorityOwner._ready_campaign_lock` and the set it guards,
/// `_rejected_ready_campaign_operations`: prepared-start identities refused without creating a
/// campaign. Never evicted while the owner lives, as in Python.
#[derive(Debug, Clone, Default)]
pub struct ReadyCampaignFence {
    rejected: Arc<Mutex<BTreeSet<String>>>,
}

/// The fence held, as `with self._operation(), self._ready_campaign_lock:` holds it.
pub struct ReadyStartWork {
    rejected: OwnedMutexGuard<BTreeSet<String>>,
    _operation: OperationGuard,
}

impl ReadyStartWork {
    /// `_assert_campaign_creation_not_rejected(operation_id)`.
    pub fn assert_not_rejected(&self, operation_id: &str) -> StoreResult<()> {
        if self.rejected.contains(operation_id) {
            return Err(rejected(
                "This prepared start was rejected without creating a campaign. \
                 Review the current choices and start again.",
                "ready_campaign_not_created",
            ));
        }
        Ok(())
    }
}

impl StoreHandle {
    /// The fence held for one prepared start (`ready_campaign_start_work` and the first half of
    /// `confirm_new_campaign`): the operation guard, then the lock.
    pub async fn hold_ready_campaigns(&self) -> StoreResult<ReadyStartWork> {
        let operation = self.operation()?;
        let rejected = Arc::clone(&self.lanes().ready_campaigns.rejected).lock_owned().await;
        Ok(ReadyStartWork { rejected, _operation: operation })
    }

    /// `AuthorityOwner.ready_campaign_start_work(operation_id)`: the fence held, refusing an
    /// identity already rejected. The start's work runs while the returned value is held; hand it
    /// to `confirm_new_campaign`, which would otherwise take the fence again (Python's lock is
    /// reentrant).
    pub async fn ready_campaign_start_work(&self, operation_id: &str) -> StoreResult<ReadyStartWork> {
        let held = self.hold_ready_campaigns().await?;
        held.assert_not_rejected(operation_id)?;
        Ok(held)
    }

    /// `AuthorityOwner.reject_uncreated_ready_campaign`: fence a rejected identity before
    /// reporting its absence, unless it already confirmed a campaign.
    pub async fn reject_uncreated_ready_campaign(&self, operation_id: &str) -> StoreResult<bool> {
        let mut held = self.hold_ready_campaigns().await?;
        if self.campaign_confirmation(operation_id).await?.is_some() {
            return Ok(false);
        }
        held.rejected.insert(operation_id.to_owned());
        Ok(true)
    }

    /// `AuthorityOwner.campaign_confirmation`: the identity check (a `ValueError` Python does not
    /// catch), then the store's read.
    pub async fn campaign_confirmation(
        &self,
        operation_id: &str,
    ) -> StoreResult<Option<CampaignConfirmation>> {
        let _operation = self.operation()?;
        validate_authority_identity(operation_id, "Campaign operation identity", false)?;
        let operation_id = operation_id.to_owned();
        self.call(move |store| store.campaign_confirmation(&operation_id)).await
    }
}

/// `confirm_new_campaign`'s store arguments.
pub struct ConfirmNewCampaign<'a> {
    pub operation_id: &'a str,
    pub semantic_payload: &'a JsonMap,
    pub payload_sha256: &'a str,
    pub campaign: &'a CampaignRecord,
    pub package_lock: &'a PackageLock,
    pub turn_id: &'a str,
    pub builder_session: Option<&'a SessionRecord>,
    pub participants: &'a [Participant],
    pub initial_state: &'a [StateValue],
}

/// `complete_campaign_construction`'s store arguments.
pub struct CompleteConstruction<'a> {
    pub operation_id: &'a str,
    pub semantic_payload: &'a JsonMap,
    pub payload_sha256: &'a str,
    pub campaign_id: &'a str,
    pub expected_revision: i64,
    pub canonical_plan: &'a JsonMap,
}

impl AuthorityStore {
    /// `campaign_confirmation`: the saved confirmation, on a read-only connection; none before
    /// the campaign schema exists.
    pub fn campaign_confirmation(&self, operation_id: &str) -> StoreResult<Option<CampaignConfirmation>> {
        let connection = self.connect_read_only()?;
        let version = crate::verify::verify_exact_schema_identity(&connection, None)?;
        if version == crate::schema::SCHEMA_VERSION || version == crate::schema::SESSION_SCHEMA_VERSION {
            return Ok(None);
        }
        let (columns, rows) = named_rows(
            &connection,
            "SELECT * FROM campaign_operations WHERE operation_id = ?",
            &[&operation_id],
        )?;
        let Some(values) = rows.into_iter().next() else { return Ok(None) };
        match operation_result(&Named { columns: &columns, values })? {
            OperationResult::Confirmation(confirmation) => Ok(Some(confirmation)),
            _ => Err(coded("IdempotencyConflict", "Campaign operation is not a confirmation")),
        }
    }

    /// `confirm_new_campaign`: create the campaign, its initial basis, its construction authority
    /// and its receipt in one transaction.
    pub fn confirm_new_campaign(
        &self,
        confirm: &ConfirmNewCampaign<'_>,
    ) -> StoreResult<CampaignConfirmation> {
        self.in_immediate(|connection| {
            self.ensure_campaign_schema(connection)?;
            match campaign_operation_hit_on(
                connection,
                confirm.operation_id,
                "confirm_new_campaign",
                confirm.payload_sha256,
            )? {
                Some(OperationResult::Confirmation(replay)) => return Ok(replay),
                Some(_) => return Err(coded("IdempotencyConflict", "Campaign operation result kind does not match")),
                None => {}
            }
            let campaign_id = confirm.campaign.campaign_id.as_str();
            verify_published_lock(connection, confirm.package_lock)?;
            let lock_hash = crate::stored::lock_representation(confirm.package_lock);
            connection.execute(
                "INSERT INTO campaigns \
                 (campaign_id, display_name, lifecycle, writer, revision, lock_hash, current_lock_id) \
                 VALUES (?, ?, 'active', 'builder', 0, ?, NULL)",
                params![campaign_id, confirm.campaign.display_name, lock_hash],
            )?;
            connection.execute(
                "INSERT INTO campaign_lifecycle_state (campaign_id, retained_active_writer) VALUES (?, 'builder')",
                params![campaign_id],
            )?;
            if let Some(session) = confirm.builder_session {
                if session.campaign_id() != Some(campaign_id)
                    || session.role() != WriterRole::Builder
                    || session.fields().status != SessionStatus::Active
                {
                    return Err(rejected(
                        "Confirmed Builder session does not match campaign authority",
                        "command_rejected",
                    ));
                }
                insert_session(connection, session)?;
            }
            let lock_record = insert_campaign_lock(connection, campaign_id, confirm.package_lock, 0, None)?;
            connection.execute(
                "UPDATE campaigns SET current_lock_id = ? WHERE campaign_id = ?",
                params![lock_record.lock_id, campaign_id],
            )?;
            for entry in confirm.package_lock.entries() {
                connection.execute(
                    "INSERT INTO campaign_package_memberships VALUES (?, ?, ?, ?, ?, ?)",
                    params![
                        campaign_id,
                        entry.package_id(),
                        entry.version(),
                        entry.sha256(),
                        entry.role().as_str(),
                        entry.load_order()
                    ],
                )?;
            }
            if confirm.participants.iter().any(|item| item.validation_status() != ParticipantValidationStatus::Validated)
            {
                return Err(rejected(
                    "Confirmed campaign participants require exact package and schema proof",
                    "participant_exact_proof_required",
                ));
            }
            for participant in confirm.participants {
                if !participant_adaptation_admitted_on(connection, participant)? {
                    return Err(rejected(
                        "Cross-schema participant reuse requires a durable adaptation operation",
                        "participant_adaptation_required",
                    ));
                }
            }
            for participant in confirm.participants {
                let fields = participant.fields();
                connection.execute(
                    "INSERT INTO participants VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    params![
                        campaign_id,
                        fields.participant_id,
                        fields.kind,
                        fields.package_id,
                        fields.package_version,
                        dump(&Json::Object(fields.data.clone())),
                        fields.package_sha256,
                        fields.schema_id,
                        fields.schema_sha256,
                        fields.validation_status,
                        fields.adaptation_id
                    ],
                )?;
            }
            let mut initial_owners = BTreeMap::new();
            for state_value in confirm.initial_state {
                let owner = initial_owner(confirm.package_lock, state_value.namespace())?;
                initial_owners.insert(state_value.namespace().to_owned(), owner);
            }
            for (namespace, owner_package_id) in &initial_owners {
                connection.execute(
                    "INSERT INTO campaign_state_namespace_owners (campaign_id, namespace, owner_package_id) \
                     VALUES (?, ?, ?)",
                    params![campaign_id, namespace, owner_package_id],
                )?;
            }
            for state_value in confirm.initial_state {
                connection.execute(
                    "INSERT INTO campaign_state VALUES (?, ?, ?, ?)",
                    params![campaign_id, state_value.namespace(), state_value.key(), dump(state_value.value())],
                )?;
            }
            connection.execute("INSERT INTO turns VALUES (?, ?, 'open', 0)", params![campaign_id, confirm.turn_id])?;
            connection.execute(
                "INSERT INTO campaign_revisions \
                 (campaign_id, revision, action_id, activation_id, plan_id, handoff_id, construction_operation_id) \
                 VALUES (?, 0, NULL, NULL, NULL, NULL, NULL)",
                params![campaign_id],
            )?;
            let mut result = JsonMap::new();
            result.insert("campaign_id".into(), campaign_id.into());
            result.insert("resulting_revision".into(), 0.into());
            insert_campaign_operation(
                connection,
                &NewOperation {
                    operation_id: confirm.operation_id,
                    operation_kind: "confirm_new_campaign",
                    campaign_id,
                    semantic_payload: confirm.semantic_payload,
                    payload_sha256: confirm.payload_sha256,
                    result: &result,
                    basis_turn_id: Some(confirm.turn_id),
                    basis_lock_id: Some(&lock_record.lock_id),
                    basis_revision: 0,
                    resulting_revision: 0,
                },
            )?;
            connection.execute(
                "INSERT INTO campaign_constructions \
                 (campaign_id, confirmation_operation_id, authority_turn_id, authority_lock_id, \
                 confirmation_revision) VALUES (?, ?, ?, ?, 0)",
                params![campaign_id, confirm.operation_id, confirm.turn_id, lock_record.lock_id],
            )?;
            Ok(CampaignConfirmation { campaign_id: campaign_id.to_owned(), resulting_revision: 0 })
        })
    }

    /// `complete_campaign_construction`: commit the lane-neutral canonical representation on the
    /// begun exact basis.
    pub fn complete_campaign_construction(
        &self,
        complete: &CompleteConstruction<'_>,
    ) -> StoreResult<CampaignConstructionCompleted> {
        self.in_immediate(|connection| {
            self.ensure_campaign_schema(connection)?;
            match campaign_operation_hit_on(
                connection,
                complete.operation_id,
                "complete_campaign_construction",
                complete.payload_sha256,
            )? {
                Some(OperationResult::Completed(replay)) => return Ok(replay),
                Some(_) => return Err(coded("IdempotencyConflict", "Campaign operation result kind does not match")),
                None => {}
            }
            let rows = construction_basis(connection, complete.campaign_id)?;
            let (campaign, construction, turn) = (rows.campaign(), rows.construction(), rows.turn());
            if construction.get("begin_operation_id")? == &Value::Null {
                return Err(rejected("Campaign construction has not begun", "command_rejected"));
            }
            if construction.get("completion_operation_id")? != &Value::Null {
                return Err(rejected("Campaign construction is already complete", "command_rejected"));
            }
            if campaign.text("lifecycle")? != "active" {
                return Err(rejected("Campaign lifecycle blocks construction", "command_rejected"));
            }
            if campaign.text("writer")? != WriterRole::Builder.as_str() {
                return Err(coded("WrongWriter", "Builder is not the authoritative campaign writer"));
            }
            if campaign.int("revision")? != complete.expected_revision {
                return Err(coded("StaleRevision", "Campaign construction expected revision is stale"));
            }
            let begin_revision = construction.int("begin_revision")?;
            let authority_lock_id = construction.text("authority_lock_id")?;
            if complete.expected_revision != begin_revision
                || campaign.text("current_lock_id")? != authority_lock_id
                || turn.text("status")? != "open"
                || turn.int("created_revision")? != construction.int("confirmation_revision")?
            {
                return Err(rejected("Campaign construction begin basis has drifted", "command_rejected"));
            }
            let canonical_input = load_payload(&construction.text("canonical_input_json")?)?;
            let mut representation = JsonMap::new();
            representation.insert("canonical_input".into(), Json::Object(canonical_input));
            representation.insert("canonical_plan".into(), Json::Object(complete.canonical_plan.clone()));
            let representation_sha256 = sha256_hex(canonical_json_bytes(&Json::Object(representation)));
            let lane = construction.text("lane")?;
            let resulting_revision = begin_revision
                .checked_add(1)
                .ok_or_else(|| StoreError::Other("campaign revision overflow".into()))?;
            let mut result = JsonMap::new();
            result.insert("campaign_id".into(), complete.campaign_id.into());
            result.insert("lane".into(), lane.as_str().into());
            result.insert("canonical_representation_sha256".into(), representation_sha256.as_str().into());
            result.insert("resulting_revision".into(), resulting_revision.into());
            let authority_turn_id = construction.text("authority_turn_id")?;
            insert_campaign_operation(
                connection,
                &NewOperation {
                    operation_id: complete.operation_id,
                    operation_kind: "complete_campaign_construction",
                    campaign_id: complete.campaign_id,
                    semantic_payload: complete.semantic_payload,
                    payload_sha256: complete.payload_sha256,
                    result: &result,
                    basis_turn_id: Some(&authority_turn_id),
                    basis_lock_id: Some(&authority_lock_id),
                    basis_revision: begin_revision,
                    resulting_revision,
                },
            )?;
            let updated = connection.execute(
                "UPDATE campaigns SET revision = ? WHERE campaign_id = ? AND revision = ? \
                 AND lifecycle = 'active' AND writer = 'builder' AND current_lock_id = ?",
                params![resulting_revision, complete.campaign_id, begin_revision, authority_lock_id],
            )?;
            if updated != 1 {
                return Err(coded("StaleRevision", "Campaign construction revision fence rejected admission"));
            }
            connection.execute(
                "UPDATE campaign_constructions SET canonical_plan_json = ?, completion_operation_id = ?, \
                 completion_revision = ?, canonical_representation_sha256 = ? WHERE campaign_id = ? \
                 AND completion_operation_id IS NULL",
                params![
                    dump(&Json::Object(complete.canonical_plan.clone())),
                    complete.operation_id,
                    resulting_revision,
                    representation_sha256,
                    complete.campaign_id
                ],
            )?;
            connection.execute(
                "INSERT INTO campaign_revisions \
                 (campaign_id, revision, action_id, activation_id, plan_id, handoff_id, construction_operation_id) \
                 VALUES (?, ?, NULL, NULL, NULL, NULL, ?)",
                params![complete.campaign_id, resulting_revision, complete.operation_id],
            )?;
            Ok(CampaignConstructionCompleted {
                campaign_id: complete.campaign_id.to_owned(),
                lane,
                canonical_representation_sha256: representation_sha256,
                resulting_revision,
            })
        })
    }
}
