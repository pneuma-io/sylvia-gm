//! Permanently purging one tombstoned campaign in the store: `SQLiteAuthorityStore.
//! preflight_campaign_purge`, `commit_campaign_purge` (every campaign-local row deleted, one
//! content-free receipt kept, unrooted package blobs reclaimed, then every verifier) with
//! `_verify_campaign_purge_cleanup`, and `_ensure_purge_schema`. The application half
//! (`AuthorityOwner.purge_campaign`) is `sylvia_app::authority::lifecycle`; the Builder sources'
//! filesystem half is [`crate::builder_sources`].
//!
//! The commit seams are the lifecycle ones, as in Python: `_before_lifecycle_commit` and
//! `_after_lifecycle_commit`.

use crate::campaigns::named_rows;
use crate::error::{authority_coded, schema_identity, StoreError, StoreResult};
use crate::guided_start::{campaign_operation_hit_on, insert_campaign_operation, NewOperation};
use crate::pytext::{py_int, py_str};
use crate::schema::{self, Known, CAMPAIGN_SCHEMA_VERSION, PURGE_EXTENSION_NAME};
use crate::store::AuthorityStore;
use crate::stored_construction::OperationResult;
use rusqlite::Connection;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{CampaignPurgeResult, LifecycleState, WriterRole};

/// `commit_campaign_purge`'s and `preflight_campaign_purge`'s keyword arguments.
#[derive(Debug, Clone, Copy)]
pub struct PurgeCommand<'a> {
    pub operation_id: &'a str,
    pub semantic_payload: &'a JsonMap,
    pub payload_sha256: &'a str,
    pub campaign_id: &'a str,
    pub expected_revision: i64,
    pub confirmation: &'a str,
}

/// `CampaignPurgeResult`'s receipt code when a committed purge cannot be resolved.
pub const PURGE_OUTCOME_UNCERTAIN: &str = "campaign_purge_commit_outcome_uncertain";

/// Every campaign-local table the cleanup check reads, in Python's order.
const CAMPAIGN_TABLES: [&str; 23] = [
    "actions",
    "agent_sessions",
    "builder_dm_handoffs",
    "campaign_constructions",
    "campaign_continuity_records",
    "campaign_lifecycle_state",
    "campaign_lifecycle_transitions",
    "campaign_locks",
    "campaign_package_memberships",
    "campaign_plans",
    "campaign_recovery_receipts",
    "campaign_revisions",
    "campaign_state",
    "campaigns",
    "canonical_events",
    "consequences",
    "idempotency_records",
    "mutations",
    "narration_interruptions",
    "package_activations",
    "participant_quarantines",
    "participants",
    "turns",
];

/// The tables deleted by campaign before the operations, in Python's order.
const DELETED_TABLES: [&str; 10] = [
    "campaign_continuity_records",
    "narration_interruptions",
    "canonical_events",
    "mutations",
    "consequences",
    "idempotency_records",
    "campaign_revisions",
    "campaign_lifecycle_transitions",
    "campaign_constructions",
    "actions",
];

fn rejected(message: &str, code: &str) -> StoreError {
    crate::actions::rejected(message, code)
}

fn exists(connection: &Connection, query: &str, values: &[&dyn rusqlite::ToSql]) -> StoreResult<bool> {
    Ok(!named_rows(connection, query, values)?.1.is_empty())
}

/// The basis both the preflight and the commit check: the campaign exists at the expected
/// revision, is tombstoned, the confirmation and payload are exact, and no surviving
/// participant's adaptation names it as its source.
fn check_purge_basis(connection: &Connection, command: &PurgeCommand<'_>) -> StoreResult<()> {
    let campaign_id = command.campaign_id;
    let rows = named_rows(
        connection,
        "SELECT lifecycle, writer, revision FROM campaigns WHERE campaign_id = ?",
        &[&campaign_id],
    )?
    .1;
    let Some(campaign) = rows.first() else {
        return Err(rejected("Campaign is missing or already purged", "command_rejected"));
    };
    let revision = py_int(&campaign[2])
        .ok_or_else(|| crate::error::value_error("invalid literal for int() with base 10"))?;
    if revision != command.expected_revision {
        return Err(StoreError::Coded(authority_coded(
            "StaleRevision",
            "Campaign purge expected revision is stale",
        )));
    }
    if py_str(&campaign[0]) != LifecycleState::Tombstoned.as_str()
        || py_str(&campaign[1]) != WriterRole::None.as_str()
    {
        return Err(rejected("Campaign must be tombstoned before purge", "command_rejected"));
    }
    let mut expected = JsonMap::new();
    expected.insert("operation_id".into(), command.operation_id.into());
    expected.insert("operation_kind".into(), "purge_campaign".into());
    expected.insert("campaign_id".into(), campaign_id.into());
    expected.insert("confirmation".into(), command.confirmation.into());
    if command.confirmation != format!("permanently purge {campaign_id}")
        || *command.semantic_payload != expected
    {
        return Err(rejected("Campaign purge confirmation is not exact", "command_rejected"));
    }
    if exists(
        connection,
        "SELECT 1 FROM participants AS participant JOIN participant_adaptations AS adaptation \
         ON adaptation.operation_id = participant.adaptation_id \
         WHERE participant.campaign_id != ? AND adaptation.source_campaign_id = ? LIMIT 1",
        &[&campaign_id, &campaign_id],
    )? {
        return Err(rejected(
            "Campaign purge is blocked by surviving adaptation provenance",
            "campaign_purge_adaptation_referenced",
        ));
    }
    Ok(())
}

/// `_verify_campaign_purge_cleanup`.
fn verify_campaign_purge_cleanup(connection: &Connection, campaign_id: &str) -> StoreResult<()> {
    for table in CAMPAIGN_TABLES {
        if exists(
            connection,
            &format!("SELECT 1 FROM \"{table}\" WHERE campaign_id = ? LIMIT 1"),
            &[&campaign_id],
        )? {
            return Err(schema_identity("Campaign purge left campaign-local authority"));
        }
    }
    if exists(
        connection,
        "SELECT 1 FROM campaign_operations WHERE campaign_id = ? AND (operation_kind != 'purge_campaign' \
         OR basis_turn_id IS NOT NULL OR basis_lock_id IS NOT NULL) LIMIT 1",
        &[&campaign_id],
    )? || exists(
        connection,
        "SELECT 1 FROM participant_adaptations WHERE source_campaign_id = ? LIMIT 1",
        &[&campaign_id],
    )? {
        return Err(schema_identity("Campaign purge retained non-receipt authority"));
    }
    if exists(connection, "PRAGMA foreign_key_check", &[])? {
        return Err(schema_identity("Campaign purge left invalid foreign keys"));
    }
    let integrity = named_rows(connection, "PRAGMA integrity_check", &[])?.1;
    let ok = integrity.len() == 1 && sylvia_base::pystr::casefold(&py_str(&integrity[0][0])) == "ok";
    if !ok {
        return Err(schema_identity("Campaign purge failed integrity verification"));
    }
    Ok(())
}

/// A purge receipt among the receipts `_campaign_operation_hit` can return.
fn purge_hit(hit: Option<OperationResult>) -> StoreResult<Option<CampaignPurgeResult>> {
    match hit {
        None => Ok(None),
        Some(OperationResult::Purge(result)) => Ok(Some(result)),
        Some(_) => Err(StoreError::Coded(authority_coded(
            "IdempotencyConflict",
            "Campaign operation result kind does not match",
        ))),
    }
}

impl AuthorityStore {
    /// `_ensure_purge_schema`: install the purge extension inside the caller's transaction.
    pub(crate) fn ensure_purge_schema(&self, connection: &Connection) -> StoreResult<()> {
        let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        if version != CAMPAIGN_SCHEMA_VERSION {
            return Err(schema_identity("Campaign purge requires state ownership authority"));
        }
        if schema::schema_identity_of(connection)? != *Known::Effort.identity() {
            return Err(schema_identity("Campaign purge predecessor schema identity is not exact"));
        }
        if exists(
            connection,
            "SELECT 1 FROM authority_schema_extensions WHERE extension_name = ?",
            &[&PURGE_EXTENSION_NAME],
        )? {
            return Ok(());
        }
        schema::insert_extension(connection, &schema::purge_extension_row())?;
        crate::verify::verify_exact_schema_identity(connection, Some(CAMPAIGN_SCHEMA_VERSION))?;
        Ok(())
    }

    /// `preflight_campaign_purge`: the exact purge basis, read without a write transaction.
    pub fn preflight_campaign_purge(&self, command: &PurgeCommand<'_>) -> StoreResult<()> {
        let connection = self.connect()?;
        check_purge_basis(&connection, command)
    }

    /// `commit_campaign_purge`: delete one tombstoned campaign and keep one content-free receipt,
    /// atomically. A purge that may have committed is resolved from its receipt, or refused as
    /// uncertain.
    pub fn commit_campaign_purge(&self, command: &PurgeCommand<'_>) -> StoreResult<CampaignPurgeResult> {
        let connection = self.connect()?;
        let mut commit_started = false;
        let outcome = self.purge_transaction(&connection, command, &mut commit_started);
        let error = match outcome {
            Ok(result) => return Ok(result),
            Err(error) => error,
        };
        if !connection.is_autocommit() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        drop(connection);
        if !commit_started {
            return Err(error);
        }
        // Any failure to read the receipt back leaves the outcome uncertain.
        match self.campaign_operation_hit(command.operation_id, "purge_campaign", command.payload_sha256) {
            Ok(Some(OperationResult::Purge(result))) => Ok(result),
            _ => Err(rejected("Campaign purge commit outcome is uncertain", PURGE_OUTCOME_UNCERTAIN)),
        }
    }

    fn purge_transaction(
        &self,
        connection: &Connection,
        command: &PurgeCommand<'_>,
        commit_started: &mut bool,
    ) -> StoreResult<CampaignPurgeResult> {
        let campaign_id = command.campaign_id;
        connection.execute_batch("BEGIN IMMEDIATE")?;
        self.ensure_purge_schema(connection)?;
        let replay = campaign_operation_hit_on(
            connection,
            command.operation_id,
            "purge_campaign",
            command.payload_sha256,
        )?;
        if let Some(replay) = purge_hit(replay)? {
            connection.execute_batch("ROLLBACK")?;
            return Ok(replay);
        }
        check_purge_basis(connection, command)?;
        connection.execute_batch("PRAGMA defer_foreign_keys = ON")?;
        for table in DELETED_TABLES {
            connection.execute(&format!("DELETE FROM \"{table}\" WHERE campaign_id = ?"), [campaign_id])?;
        }
        connection.execute(
            "DELETE FROM campaign_operations WHERE campaign_id = ? AND operation_kind != 'purge_campaign'",
            [campaign_id],
        )?;
        connection.execute("DELETE FROM campaign_recovery_receipts WHERE campaign_id = ?", [campaign_id])?;
        // Package-owned state goes before the campaign row: cascading the campaign first may visit
        // namespace owners before their state rows, which the ownership trigger refuses.
        connection.execute("DELETE FROM campaign_state WHERE campaign_id = ?", [campaign_id])?;
        connection.execute("DELETE FROM campaigns WHERE campaign_id = ?", [campaign_id])?;
        connection
            .execute("DELETE FROM participant_adaptations WHERE source_campaign_id = ?", [campaign_id])?;
        // Python's integers do not overflow; SQLite's binding refuses one past 64 bits.
        let resulting_revision = command
            .expected_revision
            .checked_add(1)
            .ok_or_else(|| StoreError::Other("Python int too large to convert to SQLite INTEGER".into()))?;
        let mut result = JsonMap::new();
        result.insert("campaign_id".into(), Json::from(campaign_id));
        result.insert("resulting_revision".into(), Json::from(resulting_revision));
        insert_campaign_operation(
            connection,
            &NewOperation {
                operation_id: command.operation_id,
                operation_kind: "purge_campaign",
                campaign_id,
                semantic_payload: command.semantic_payload,
                payload_sha256: command.payload_sha256,
                result: &result,
                basis_turn_id: None,
                basis_lock_id: None,
                basis_revision: command.expected_revision,
                resulting_revision,
            },
        )?;
        crate::packages::reclaim(connection)?;
        verify_campaign_purge_cleanup(connection, campaign_id)?;
        crate::verify::verify_exact_schema_identity(connection, Some(CAMPAIGN_SCHEMA_VERSION))?;
        crate::stored::verify_stored_authority(connection)?;
        crate::packages::verify_package_authority(connection)?;
        crate::f5::verify_f5_campaign_authority(connection)?;
        let purged = CampaignPurgeResult { campaign_id: campaign_id.to_owned(), resulting_revision };
        if let Some(fault) = &self.faults.before_lifecycle_commit {
            fault()?;
        }
        *commit_started = true;
        connection.execute_batch("COMMIT")?;
        if let Some(fault) = &self.faults.after_lifecycle_commit {
            fault()?;
        }
        Ok(purged)
    }
}
