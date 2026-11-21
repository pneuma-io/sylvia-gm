//! `SQLiteAuthorityStore._verify_exact_schema_identity`: the schema, migration rows, extension
//! rows and ownership triggers must be exactly one supported state of the stored version.

use crate::error::{schema_identity, StoreResult};
use crate::pytext::{py_int, py_str};
use crate::schema::{
    all_rows, expected_campaign_extension_rows, expected_trigger_identity, migration_row, schema_identity_of,
    trigger_identity_of, Known, CAMPAIGN_SCHEMA_VERSION, MIGRATION_5_NAME, PREDECESSOR_MIGRATION_5_SHA256,
    PRE_BUILDER_CHAT_SCHEMA_VERSION, PRE_CONTINUITY_SCHEMA_VERSION, PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
    PRE_EFFORT_SCHEMA_VERSION, PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION, PRE_REASONING_SCHEMA_VERSION,
    PURGE_EXTENSION_NAME, SCHEMA_VERSION, SESSION_SCHEMA_VERSION, STATE_OWNERSHIP_SCHEMA_VERSION,
};
use rusqlite::types::Value;
use rusqlite::Connection;
use std::collections::BTreeSet;

/// `_REQUIRED_TABLES`.
const REQUIRED_TABLES: [&str; 33] = [
    "schema_migrations",
    "packages",
    "package_dependencies",
    "campaigns",
    "campaign_package_memberships",
    "participants",
    "campaign_state",
    "turns",
    "actions",
    "canonical_events",
    "narration_interruptions",
    "mutations",
    "consequences",
    "idempotency_records",
    "campaign_revisions",
    "package_blobs",
    "package_blob_chunks",
    "package_manifests",
    "package_manifest_files",
    "package_exact_dependencies",
    "package_capabilities",
    "package_tools",
    "package_prompt_fragments",
    "package_ui_surfaces",
    "package_staging",
    "package_staging_chunks",
    "package_candidates",
    "package_candidate_memberships",
    "campaign_locks",
    "campaign_lock_memberships",
    "package_activations",
    "package_read_leases",
    "package_recovery_roots",
];
/// `_SESSION_TABLES`.
const SESSION_TABLES: [&str; 4] =
    ["agent_sessions", "agent_session_messages", "campaign_plans", "builder_dm_handoffs"];
/// `_CAMPAIGN_TABLES`.
const CAMPAIGN_TABLES: [&str; 8] = [
    "package_participant_artifacts",
    "participant_adaptations",
    "participant_quarantines",
    "authority_schema_extensions",
    "campaign_operations",
    "campaign_constructions",
    "campaign_lifecycle_state",
    "campaign_lifecycle_transitions",
];
/// `_STATE_OWNERSHIP_TABLES`.
const STATE_OWNERSHIP_TABLES: [&str; 1] = ["campaign_state_namespace_owners"];
/// `_MECHANICS_CHOICE_TABLES`.
const MECHANICS_CHOICE_TABLES: [&str; 3] =
    ["pending_mechanics_actions", "pending_mechanics_action_aliases", "mechanics_choice_resolutions"];
/// `_BUILDER_CHAT_TABLES`.
const BUILDER_CHAT_TABLES: [&str; 2] = ["standalone_builder_chats", "standalone_builder_chat_messages"];
/// `_CONTINUITY_TABLES`.
const CONTINUITY_TABLES: [&str; 1] = ["campaign_continuity_records"];

/// `int(connection.execute("PRAGMA user_version").fetchone()[0])`.
pub(crate) fn user_version(connection: &Connection) -> StoreResult<i64> {
    Ok(connection.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))?)
}

fn has_purge_extension(connection: &Connection) -> StoreResult<bool> {
    let mut statement =
        connection.prepare("SELECT 1 FROM authority_schema_extensions WHERE extension_name = ?")?;
    Ok(statement.exists([PURGE_EXTENSION_NAME])?)
}

type MigrationRows = Vec<(i64, String, String)>;

/// `tuple((int(row[0]), str(row[1]), str(row[2])) for row in schema_migrations)`.
fn migration_rows(connection: &Connection) -> StoreResult<MigrationRows> {
    let rows = all_rows(
        connection,
        "SELECT version, applied_name, sql_sha256 FROM schema_migrations ORDER BY version",
    )?;
    rows.iter()
        .map(|row| {
            let version = py_int(&row[0]).ok_or_else(|| {
                crate::error::StoreError::Other("invalid literal for int() with base 10".into())
            })?;
            Ok((version, py_str(&row[1]), py_str(&row[2])))
        })
        .collect()
}

fn owned(version: i64) -> (i64, String, String) {
    let (version, name, sha256) = migration_row(version);
    (version, name.to_owned(), sha256.to_owned())
}

/// Which Python check runs: they test the same identity with their own messages and order.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Flavor {
    /// `_verify_exact_schema_identity`.
    Authority,
    /// The identity half of `_verify_backup_authority`.
    Backup,
}

impl Flavor {
    fn unsupported(self) -> &'static str {
        match self {
            Flavor::Authority => "Authority schema identity is incomplete",
            Flavor::Backup => "Backup schema identity is unsupported",
        }
    }
    fn tables(self) -> &'static str {
        match self {
            Flavor::Authority => "Authority schema identity does not match its version",
            Flavor::Backup => "Backup schema identity does not match its version",
        }
    }
    fn structure(self) -> &'static str {
        match self {
            Flavor::Authority => "Authority schema structure does not match its version",
            Flavor::Backup => "Backup schema structure does not match its version",
        }
    }
    fn migrations(self) -> &'static str {
        match self {
            Flavor::Authority => "Authority migration identity does not match its version",
            Flavor::Backup => "Backup migration identity does not match its version",
        }
    }
    fn extensions(self) -> &'static str {
        match self {
            Flavor::Authority => "Authority schema extension identity is invalid",
            Flavor::Backup => "Backup schema extension identity is invalid",
        }
    }
}

/// Verify the exact identity of `version` (read from the database when `None`) and return it.
pub(crate) fn verify_exact_schema_identity(
    connection: &Connection,
    version: Option<i64>,
) -> StoreResult<i64> {
    check_identity(connection, version, Flavor::Authority)
}

/// The schema, trigger, migration and extension checks of `_verify_backup_authority`, on a
/// backup copy opened read-only. Returns its version.
pub(crate) fn verify_backup_identity(connection: &Connection) -> StoreResult<i64> {
    check_identity(connection, None, Flavor::Backup)
}

fn check_identity(connection: &Connection, version: Option<i64>, flavor: Flavor) -> StoreResult<i64> {
    let version = match version {
        Some(version) => version,
        None => user_version(connection)?,
    };
    if !(SCHEMA_VERSION..=CAMPAIGN_SCHEMA_VERSION).contains(&version) {
        return Err(schema_identity(flavor.unsupported()));
    }
    let tables: BTreeSet<String> =
        all_rows(connection, "SELECT name FROM sqlite_master WHERE type = 'table'")?
            .iter()
            .map(|row| py_str(&row[0]))
            .collect();
    let mut required: Vec<&str> = REQUIRED_TABLES.to_vec();
    let mut has_recovery_extension = false;
    let mut has_purge = false;
    let full = |required: &mut Vec<&str>, builder: bool, continuity: bool| {
        required.extend(SESSION_TABLES);
        required.extend(CAMPAIGN_TABLES);
        required.extend(STATE_OWNERSHIP_TABLES);
        required.extend(MECHANICS_CHOICE_TABLES);
        if builder {
            required.extend(BUILDER_CHAT_TABLES);
        }
        if continuity {
            required.extend(CONTINUITY_TABLES);
        }
    };
    let expected = if matches!(
        version,
        PRE_REASONING_SCHEMA_VERSION | PRE_EFFORT_SCHEMA_VERSION | CAMPAIGN_SCHEMA_VERSION
    ) {
        full(&mut required, true, true);
        if version >= PRE_EFFORT_SCHEMA_VERSION {
            required.push("provider_private_records");
        }
        has_recovery_extension = true;
        has_purge = has_purge_extension(connection)?;
        match version {
            CAMPAIGN_SCHEMA_VERSION => Known::Effort,
            PRE_EFFORT_SCHEMA_VERSION => Known::Reasoning,
            _ => Known::DraftLifecycle,
        }
    } else if version == PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION {
        full(&mut required, true, true);
        has_recovery_extension = true;
        if &schema_identity_of(connection)? == Known::PurgeContinuity.identity() {
            has_purge = true;
            Known::PurgeContinuity
        } else {
            Known::Continuity
        }
    } else if version == PRE_CONTINUITY_SCHEMA_VERSION {
        full(&mut required, true, false);
        has_recovery_extension = true;
        if &schema_identity_of(connection)? == Known::PurgeBuilderChat.identity() {
            has_purge = true;
            Known::PurgeBuilderChat
        } else {
            Known::BuilderChat
        }
    } else if version == PRE_BUILDER_CHAT_SCHEMA_VERSION {
        full(&mut required, false, false);
        has_recovery_extension = true;
        if &schema_identity_of(connection)? == Known::PurgeMechanicsChoice.identity() {
            has_purge = true;
            Known::PurgeMechanicsChoice
        } else {
            Known::MechanicsChoice
        }
    } else if version == STATE_OWNERSHIP_SCHEMA_VERSION {
        required.extend(SESSION_TABLES);
        required.extend(CAMPAIGN_TABLES);
        required.extend(STATE_OWNERSHIP_TABLES);
        has_recovery_extension = true;
        if &schema_identity_of(connection)? == Known::PurgeStateOwnership.identity() {
            has_purge = true;
            Known::PurgeStateOwnership
        } else {
            Known::StateOwnership
        }
    } else if version == PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION {
        required.extend(SESSION_TABLES);
        required.extend(CAMPAIGN_TABLES);
        let live = schema_identity_of(connection)?;
        if &live == Known::PurgeCampaign.identity() {
            has_recovery_extension = true;
            has_purge = true;
            Known::PurgeCampaign
        } else if &live == Known::W12Campaign.identity() {
            has_recovery_extension = true;
            Known::W12Campaign
        } else {
            Known::Campaign
        }
    } else if version == SESSION_SCHEMA_VERSION {
        required.extend(SESSION_TABLES);
        Known::Session
    } else {
        Known::Base
    };
    if !required.iter().all(|table| tables.contains(*table)) {
        return Err(schema_identity(flavor.tables()));
    }
    if &schema_identity_of(connection)? != expected.identity() {
        return Err(schema_identity(flavor.structure()));
    }
    // The backup check tests the ownership triggers here, before the migration rows.
    if flavor == Flavor::Backup
        && version >= STATE_OWNERSHIP_SCHEMA_VERSION
        && &trigger_identity_of(connection)? != expected_trigger_identity()
    {
        return Err(schema_identity("Backup state-ownership triggers are invalid"));
    }
    let rows = migration_rows(connection)?;
    let mut expected_migrations: MigrationRows = (1..=3).map(owned).collect();
    if version >= SESSION_SCHEMA_VERSION {
        expected_migrations.push(owned(4));
    }
    if version >= PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION {
        // Python builds the set of supported histories: migration 5 with either digest, then
        // each later migration the version includes.
        let current_5 = owned(5);
        let predecessor_5 = (5, MIGRATION_5_NAME.to_owned(), PREDECESSOR_MIGRATION_5_SHA256.to_owned());
        let later: MigrationRows = (6..=version).map(owned).collect();
        let supported = [current_5, predecessor_5].into_iter().map(|fifth| {
            let mut history = expected_migrations.clone();
            history.push(fifth);
            history.extend(later.iter().cloned());
            history
        });
        let supported: Vec<MigrationRows> = supported.collect();
        if !supported.contains(&rows) {
            return Err(schema_identity(flavor.migrations()));
        }
        let extension_rows = all_rows(
            connection,
            "SELECT extension_name, extension_sha256, predecessor_migration_name, \
             predecessor_sql_sha256 FROM authority_schema_extensions \
             ORDER BY extension_name",
        )?;
        let wanted = expected_campaign_extension_rows(has_recovery_extension, has_purge);
        let same = extension_rows.len() == wanted.len()
            && extension_rows.iter().zip(&wanted).all(|(row, expected)| {
                row.len() == 4
                    && row
                        .iter()
                        .zip(expected)
                        .all(|(value, text)| matches!(value, Value::Text(stored) if stored == text))
            });
        if !same {
            return Err(schema_identity(flavor.extensions()));
        }
        if flavor == Flavor::Authority
            && version >= STATE_OWNERSHIP_SCHEMA_VERSION
            && &trigger_identity_of(connection)? != expected_trigger_identity()
        {
            return Err(schema_identity("Authority state ownership trigger identity is invalid"));
        }
    } else if rows != expected_migrations {
        return Err(schema_identity(flavor.migrations()));
    }
    Ok(version)
}
