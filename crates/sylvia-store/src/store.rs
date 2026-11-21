//! `SQLiteAuthorityStore`: opening, migrating and connecting to the one live authority.
//!
//! Python opens a fresh connection for every operation and closes it afterwards; this port does
//! the same, on the store thread ([`crate::handle::StoreHandle`]), so connection settings,
//! transactions and checkpoints behave as they do in Python.

use crate::error::{schema_identity, StoreError, StoreResult};
use crate::owner_lock::{authority_lock_identity, OwnerLock};
use crate::pytext::py_str;
use crate::schema::{
    self, apply_continuity_migration, apply_draft_lifecycle_migration, apply_effort_migration,
    apply_lifecycle_extension, apply_mechanics_choice_migration, apply_portable_recovery_extension,
    apply_reasoning_migration, apply_state_ownership_migration, execute_statements, insert_extension,
    migrate_legacy_campaigns, migrate_v4_participants, record_migration, schema_identity_of, Known,
    CAMPAIGN_SCHEMA_VERSION, MIGRATION_5_NAME, PREDECESSOR_MIGRATION_5_SHA256,
    PRE_BUILDER_CHAT_SCHEMA_VERSION, PRE_CONTINUITY_SCHEMA_VERSION, PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
    PRE_EFFORT_SCHEMA_VERSION, PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION, PRE_REASONING_SCHEMA_VERSION,
    SCHEMA_VERSION, SESSION_SCHEMA_VERSION, STATE_OWNERSHIP_SCHEMA_VERSION,
};
use crate::sql;
use crate::verify::{user_version, verify_exact_schema_identity};
use rusqlite::types::Value;
use rusqlite::{Connection, OpenFlags, OptionalExtension};
use std::path::{Path, PathBuf};
use std::time::Duration;
use sylvia_base::CodedError;

/// `timeout=30.0` on every operation connection.
const BUSY_TIMEOUT: Duration = Duration::from_secs(30);

/// Run before migrations, under the ownership lock (Python's `before_migration`).
pub type BeforeMigration = Box<dyn FnOnce() -> Result<(), CodedError> + Send>;

/// The only adapter that opens the live SQLite authority.
#[derive(Debug)]
pub struct AuthorityStore {
    database_path: PathBuf,
    owner_lock: OwnerLock,
    created_new_database: bool,
    closed: bool,
}

impl AuthorityStore {
    /// `_open_for_application_owner`: take the owner lock, run `before_migration`, then the
    /// startup migration chain. Only the application owner calls this.
    pub fn open_for_application_owner(
        database_path: &Path,
        before_migration: Option<BeforeMigration>,
    ) -> StoreResult<Self> {
        let database_path = resolve(database_path);
        let owner_lock = OwnerLock::acquire(&authority_lock_identity(&database_path))?;
        let mut store = Self { database_path, owner_lock, created_new_database: false, closed: false };
        // Startup admission stays under the ownership lock through migrations.
        if let Some(before_migration) = before_migration {
            before_migration()?;
        }
        store.migrate()?;
        Ok(store)
    }

    /// The resolved database path.
    pub fn database_path(&self) -> &Path {
        &self.database_path
    }

    /// Whether this open created the database (cleared by [`Self::prepare_new_campaign_storage`]).
    pub fn created_new_database(&self) -> bool {
        self.created_new_database
    }

    /// `prepare_new_campaign_storage`: finish a fresh profile's migrations before bundled
    /// packages make verified backups expensive.
    pub fn prepare_new_campaign_storage(&mut self) -> StoreResult<()> {
        if !self.created_new_database {
            return Ok(());
        }
        let connection = self.connect()?;
        let result = (|| -> StoreResult<()> {
            connection.execute_batch("BEGIN IMMEDIATE")?;
            self.ensure_campaign_schema(&connection)?;
            connection.execute_batch("COMMIT")?;
            Ok(())
        })();
        rollback_if_open(&connection);
        result?;
        self.created_new_database = false;
        Ok(())
    }

    /// `_connect`: a read-write operation connection with Python's settings.
    pub fn connect(&self) -> StoreResult<Connection> {
        if self.closed {
            return Err(StoreError::Other("SQLite authority store is closed".into()));
        }
        let connection = Connection::open_with_flags(
            &self.database_path,
            OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_CREATE
                | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        connection.busy_timeout(BUSY_TIMEOUT)?;
        connection.execute_batch("PRAGMA foreign_keys = ON")?;
        connection.execute_batch("PRAGMA synchronous = FULL")?;
        Ok(connection)
    }

    /// `_connect_read_only`: existing bytes, no persistent settings or side effects.
    pub fn connect_read_only(&self) -> StoreResult<Connection> {
        if self.closed {
            return Err(StoreError::Other("SQLite authority store is closed".into()));
        }
        let connection = Connection::open_with_flags(
            &self.database_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        connection.busy_timeout(BUSY_TIMEOUT)?;
        connection.execute_batch("PRAGMA foreign_keys = ON")?;
        connection.execute_batch("PRAGMA query_only = ON")?;
        Ok(connection)
    }

    /// `campaign_schema_is_active`: whether the current (schema 12) proof governs writes.
    pub fn campaign_schema_is_active(&self) -> StoreResult<bool> {
        let connection = self.connect()?;
        Ok(user_version(&connection)? == CAMPAIGN_SCHEMA_VERSION)
    }

    /// `close`: release the owner lock. Later operations are refused.
    pub fn close(&mut self) {
        if self.closed {
            return;
        }
        self.closed = true;
        self.owner_lock.close();
    }

    /// `_migrate`: the startup chain. Old campaign schemas (5 to 11) advance to 12 here; a
    /// fresh database stops at schema 3, and schema 3 or 4 advance lazily.
    fn migrate(&mut self) -> StoreResult<()> {
        let exists = self.database_path.exists();
        if exists && std::fs::metadata(&self.database_path)?.len() == 0 {
            return Err(schema_identity("Authority schema identity is incomplete"));
        }
        if let Some(parent) = self.database_path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        // sqlite3.connect(path, isolation_level=None): read-write, create, default 5 s timeout.
        let connection = Connection::open_with_flags(
            &self.database_path,
            OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_CREATE
                | OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )?;
        let result = self.migrate_steps(&connection, !exists);
        rollback_if_open(&connection);
        result.map_err(|error| StoreError::Coded(error.at_startup()))
    }

    fn migrate_steps(&mut self, connection: &Connection, new_database: bool) -> StoreResult<()> {
        connection.execute_batch("PRAGMA foreign_keys = ON")?;
        let mut version = user_version(connection)?;
        if version > CAMPAIGN_SCHEMA_VERSION {
            return Err(schema_identity("Authority schema identity is unsupported"));
        }
        if version == PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION {
            verify_exact_schema_identity(connection, Some(version))?;
            self.preserve_pre_migration_source(version)?;
            let live = schema_identity_of(connection)?;
            if &live != Known::W12Campaign.identity() && &live != Known::PurgeCampaign.identity() {
                self.upgrade_predecessor_campaign_schema(connection)?;
            }
            self.upgrade_w12_recovery_extension(connection)?;
            self.upgrade_state_ownership_schema(connection)?;
            version = STATE_OWNERSHIP_SCHEMA_VERSION;
        }
        if version == STATE_OWNERSHIP_SCHEMA_VERSION {
            verify_exact_schema_identity(connection, Some(version))?;
            self.preserve_pre_migration_source(version)?;
            self.upgrade_step(
                connection,
                version,
                PRE_BUILDER_CHAT_SCHEMA_VERSION,
                apply_mechanics_choice_migration,
            )?;
            version = PRE_BUILDER_CHAT_SCHEMA_VERSION;
        }
        if version == PRE_BUILDER_CHAT_SCHEMA_VERSION {
            verify_exact_schema_identity(connection, Some(version))?;
            self.preserve_pre_migration_source(version)?;
            self.upgrade_step(connection, version, PRE_CONTINUITY_SCHEMA_VERSION, |connection| {
                execute_statements(connection, sql::MIGRATION_8)
            })?;
            version = PRE_CONTINUITY_SCHEMA_VERSION;
        }
        if version == PRE_CONTINUITY_SCHEMA_VERSION {
            verify_exact_schema_identity(connection, Some(version))?;
            self.preserve_pre_migration_source(version)?;
            self.upgrade_step(
                connection,
                version,
                PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION,
                apply_continuity_migration,
            )?;
            version = PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION;
        }
        if version == PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION {
            verify_exact_schema_identity(connection, Some(version))?;
            self.preserve_pre_migration_source(version)?;
            self.upgrade_step(
                connection,
                version,
                PRE_REASONING_SCHEMA_VERSION,
                apply_draft_lifecycle_migration,
            )?;
            version = PRE_REASONING_SCHEMA_VERSION;
        }
        if version == PRE_REASONING_SCHEMA_VERSION {
            verify_exact_schema_identity(connection, Some(version))?;
            self.preserve_pre_migration_source(version)?;
            connection.execute_batch("BEGIN IMMEDIATE")?;
            apply_reasoning_step(connection)?;
            connection.execute_batch("COMMIT")?;
            version = PRE_EFFORT_SCHEMA_VERSION;
        }
        if version == PRE_EFFORT_SCHEMA_VERSION {
            verify_exact_schema_identity(connection, Some(version))?;
            self.preserve_pre_migration_source(version)?;
            connection.execute_batch("BEGIN IMMEDIATE")?;
            apply_effort_step(connection)?;
            connection.execute_batch("COMMIT")?;
            version = CAMPAIGN_SCHEMA_VERSION;
        }
        if version == 0 {
            connection.execute_batch(&format!("BEGIN IMMEDIATE;\n{}", sql::MIGRATION_1))?;
            record_migration(connection, 1)?;
            connection.execute_batch("COMMIT")?;
            self.created_new_database = new_database;
            version = 1;
        }
        if version == 1 {
            connection.execute_batch(&format!("BEGIN IMMEDIATE;\n{}", sql::MIGRATION_2))?;
            record_migration(connection, 2)?;
            connection.execute_batch("COMMIT")?;
            version = 2;
        }
        if version == 2 {
            connection.execute_batch(&format!("BEGIN IMMEDIATE;\n{}", sql::MIGRATION_3))?;
            migrate_legacy_campaigns(connection)?;
            record_migration(connection, 3)?;
            connection.execute_batch("COMMIT")?;
            version = 3;
        }
        verify_exact_schema_identity(connection, Some(version))?;
        // sqlite_packages.clear_stale_read_leases
        connection.execute("DELETE FROM package_read_leases", [])?;
        // Not yet ported: the stored-content verifiers Python runs here
        // (`_verify_stored_authority`, `sqlite_packages.verify_package_authority` and, at schema
        // 12, `sqlite_campaign.verify_f5_campaign_authority` and
        // `sqlite_continuity.verify_authority`). They only refuse databases whose rows break
        // the authority's invariants; each arrives with the transaction family that owns its
        // tables (see the lane 3 ledger).
        if version == CAMPAIGN_SCHEMA_VERSION {
            // Exclusive owner startup cannot inherit a live provider attempt.
            connection.execute(
                "UPDATE provider_private_records SET status = 'interrupted' WHERE status = 'partial'",
                [],
            )?;
        }
        let journal_mode: Option<Value> =
            connection.query_row("PRAGMA journal_mode = WAL", [], |row| row.get(0)).optional()?;
        match journal_mode {
            Some(mode) if schema::casefold_equals(&py_str(&mode), "wal") => Ok(()),
            _ => Err(schema_identity("Authority schema identity could not be verified")),
        }
    }

    /// One `_upgrade_*_schema` step from `from` to `to`: verify, apply in one transaction,
    /// record the migration, verify the result, commit.
    fn upgrade_step(
        &self,
        connection: &Connection,
        from: i64,
        to: i64,
        apply: impl FnOnce(&Connection) -> StoreResult<()>,
    ) -> StoreResult<()> {
        verify_exact_schema_identity(connection, Some(from))?;
        connection.execute_batch("BEGIN IMMEDIATE")?;
        apply(connection)?;
        record_migration(connection, to)?;
        verify_exact_schema_identity(connection, Some(to))?;
        connection.execute_batch("COMMIT")?;
        Ok(())
    }

    /// `_upgrade_predecessor_campaign_schema`.
    fn upgrade_predecessor_campaign_schema(&self, connection: &Connection) -> StoreResult<()> {
        let live = schema_identity_of(connection)?;
        if &live == Known::Campaign.identity() {
            return Ok(());
        }
        let predecessor: Vec<(i64, String, String)> = (1..=4)
            .map(schema::migration_row)
            .map(|(version, name, sha256)| (version, name.to_owned(), sha256.to_owned()))
            .chain(std::iter::once((
                5,
                MIGRATION_5_NAME.to_owned(),
                PREDECESSOR_MIGRATION_5_SHA256.to_owned(),
            )))
            .collect();
        let mut current = predecessor[..4].to_vec();
        let (version, name, sha256) = schema::migration_row(5);
        current.push((version, name.to_owned(), sha256.to_owned()));
        let rows: Vec<(i64, String, String)> = schema::all_rows(
            connection,
            "SELECT version, applied_name, sql_sha256 FROM schema_migrations ORDER BY version",
        )?
        .iter()
        .map(|row| {
            let version = crate::pytext::py_int(&row[0])
                .ok_or_else(|| StoreError::Other("invalid literal for int() with base 10".into()))?;
            Ok((version, py_str(&row[1]), py_str(&row[2])))
        })
        .collect::<StoreResult<_>>()?;
        connection.execute_batch("BEGIN IMMEDIATE")?;
        if &live == Known::PredecessorCampaign.identity() {
            if rows != predecessor {
                return Err(schema_identity("Predecessor campaign schema identity is not exact"));
            }
            execute_statements(connection, sql::CAMPAIGN_IDENTITY_DELTA)?;
            insert_extension(connection, &schema::campaign_identity_extension_row())?;
        } else if &live == Known::Probe01Campaign.identity() {
            if rows != predecessor && rows != current {
                return Err(schema_identity("Saved campaign migration identity does not match"));
            }
            let extension = schema::all_rows(
                connection,
                "SELECT extension_name, extension_sha256, predecessor_migration_name, \
                 predecessor_sql_sha256 FROM authority_schema_extensions",
            )?;
            let wanted = schema::campaign_identity_extension_row();
            let matches = extension.first().is_some_and(|row| {
                row.len() == 4
                    && row
                        .iter()
                        .zip(&wanted)
                        .all(|(value, text)| matches!(value, Value::Text(stored) if stored == text))
            });
            if !matches {
                return Err(schema_identity("Saved campaign extension identity does not match"));
            }
        } else {
            return Err(schema_identity("Predecessor campaign schema identity is not exact"));
        }
        apply_lifecycle_extension(connection)?;
        insert_extension(connection, &schema::lifecycle_extension_row())?;
        connection.execute_batch("COMMIT")?;
        Ok(())
    }

    /// `_upgrade_w12_recovery_extension`.
    fn upgrade_w12_recovery_extension(&self, connection: &Connection) -> StoreResult<()> {
        let live = schema_identity_of(connection)?;
        if &live == Known::W12Campaign.identity() || &live == Known::PurgeCampaign.identity() {
            return Ok(());
        }
        if &live != Known::Campaign.identity() {
            return Err(schema_identity("W12 predecessor campaign schema identity is not exact"));
        }
        connection.execute_batch("BEGIN IMMEDIATE")?;
        let rows = schema::all_rows(
            connection,
            "SELECT extension_name, extension_sha256, predecessor_migration_name, \
             predecessor_sql_sha256 FROM authority_schema_extensions \
             ORDER BY extension_name",
        )?;
        let wanted = schema::expected_campaign_extension_rows(false, false);
        let same = rows.len() == wanted.len()
            && rows.iter().zip(&wanted).all(|(row, expected)| {
                row.len() == 4
                    && row
                        .iter()
                        .zip(expected)
                        .all(|(value, text)| matches!(value, Value::Text(stored) if stored == text))
            });
        if !same {
            return Err(schema_identity("W12 predecessor extension identity is not exact"));
        }
        apply_portable_recovery_extension(connection)?;
        connection.execute_batch("COMMIT")?;
        Ok(())
    }

    /// `_upgrade_state_ownership_schema`.
    fn upgrade_state_ownership_schema(&self, connection: &Connection) -> StoreResult<()> {
        self.upgrade_step(
            connection,
            PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION,
            STATE_OWNERSHIP_SCHEMA_VERSION,
            apply_state_ownership_migration,
        )
    }

    /// `_ensure_session_schema`: additive migration 4 when the session surface is first used.
    pub fn ensure_session_schema(&self) -> StoreResult<()> {
        let connection = self.connect()?;
        let result = (|| -> StoreResult<()> {
            connection.execute_batch("BEGIN IMMEDIATE;")?;
            let version = user_version(&connection)?;
            // Python's list: 5, 7, 8 and 9 are absent, so those versions are refused below.
            if matches!(
                version,
                SESSION_SCHEMA_VERSION
                    | STATE_OWNERSHIP_SCHEMA_VERSION
                    | PRE_REASONING_SCHEMA_VERSION
                    | PRE_EFFORT_SCHEMA_VERSION
                    | CAMPAIGN_SCHEMA_VERSION
            ) {
                connection.execute_batch("ROLLBACK")?;
                return Ok(());
            }
            if version != SCHEMA_VERSION {
                return Err(schema_identity("Session schema cannot migrate from this authority version"));
            }
            self.preserve_pre_migration_source(version)?;
            execute_statements(&connection, sql::MIGRATION_4)?;
            record_migration(&connection, 4)?;
            connection.execute_batch("COMMIT")?;
            Ok(())
        })();
        rollback_if_open(&connection);
        result
    }

    /// `_ensure_campaign_schema`: advance to schema 12 inside the caller's transaction.
    pub fn ensure_campaign_schema(&self, connection: &Connection) -> StoreResult<()> {
        let mut version = verify_exact_schema_identity(connection, None)?;
        if version == CAMPAIGN_SCHEMA_VERSION {
            return Ok(());
        }
        self.preserve_pre_migration_source(version)?;
        if version == PRE_REASONING_SCHEMA_VERSION {
            apply_reasoning_step(connection)?;
            version = PRE_EFFORT_SCHEMA_VERSION;
        }
        if version == PRE_EFFORT_SCHEMA_VERSION {
            return apply_effort_step(connection);
        }
        if version == SCHEMA_VERSION {
            execute_statements(connection, sql::MIGRATION_4)?;
            record_migration(connection, 4)?;
            version = SESSION_SCHEMA_VERSION;
        }
        if version == SESSION_SCHEMA_VERSION {
            execute_statements(connection, &sql::migration_5())?;
            migrate_v4_participants(connection)?;
            insert_extension(connection, &schema::campaign_identity_extension_row())?;
            apply_lifecycle_extension(connection)?;
            insert_extension(connection, &schema::lifecycle_extension_row())?;
            record_migration(connection, 5)?;
            version = PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION;
        }
        if version == PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION {
            let mut live = schema_identity_of(connection)?;
            if &live == Known::Campaign.identity() {
                apply_portable_recovery_extension(connection)?;
                live = schema_identity_of(connection)?;
            }
            if &live != Known::W12Campaign.identity() && &live != Known::PurgeCampaign.identity() {
                return Err(schema_identity("State ownership migration predecessor is not exact"));
            }
            apply_state_ownership_migration(connection)?;
            record_migration(connection, 6)?;
            version = STATE_OWNERSHIP_SCHEMA_VERSION;
        }
        if version == STATE_OWNERSHIP_SCHEMA_VERSION {
            apply_mechanics_choice_migration(connection)?;
            record_migration(connection, 7)?;
            version = PRE_BUILDER_CHAT_SCHEMA_VERSION;
        }
        if version == PRE_BUILDER_CHAT_SCHEMA_VERSION {
            execute_statements(connection, sql::MIGRATION_8)?;
            record_migration(connection, 8)?;
            version = PRE_CONTINUITY_SCHEMA_VERSION;
        }
        if version == PRE_CONTINUITY_SCHEMA_VERSION {
            apply_continuity_migration(connection)?;
            record_migration(connection, 9)?;
            version = PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION;
        }
        if version != PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION {
            return Err(schema_identity("Draft lifecycle migration predecessor is invalid"));
        }
        apply_draft_lifecycle_migration(connection)?;
        record_migration(connection, 10)?;
        apply_reasoning_step(connection)?;
        apply_effort_step(connection)
    }

    /// `_pre_migration_source_path`.
    pub fn pre_migration_source_path(&self, version: i64) -> PathBuf {
        let stem = self.database_path.file_stem().unwrap_or_default().to_string_lossy();
        self.database_path
            .parent()
            .unwrap_or_else(|| Path::new(""))
            .join("migration-sources")
            .join(format!("{stem}.pre-migration-v{version}.sqlite3"))
    }

    /// `_preserve_pre_migration_source`: one backup before material schema DDL.
    fn preserve_pre_migration_source(&self, version: i64) -> StoreResult<()> {
        let target = self.pre_migration_source_path(version);
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent)?;
        }
        self.backup_database(&target)
    }

    /// `backup_database`: durably publish one coherent online copy in the supported local
    /// envelope (Windows fixed disks, or Linux ext4, xfs or btrfs). The caller must manage the
    /// target directory exclusively for the whole call. Fails with `backup_<phase>_<reason>`.
    pub fn backup_database(&self, target: &Path) -> StoreResult<()> {
        crate::backup::Backup::new(&self.database_path).run(target).map_err(StoreError::Backup)
    }
}

impl Drop for AuthorityStore {
    fn drop(&mut self) {
        self.close();
    }
}

/// `_apply_reasoning_migration` in the caller's transaction.
fn apply_reasoning_step(connection: &Connection) -> StoreResult<()> {
    verify_exact_schema_identity(connection, Some(PRE_REASONING_SCHEMA_VERSION))?;
    apply_reasoning_migration(connection)?;
    record_migration(connection, 11)?;
    verify_exact_schema_identity(connection, Some(PRE_EFFORT_SCHEMA_VERSION))?;
    Ok(())
}

/// `_apply_effort_migration` in the caller's transaction.
fn apply_effort_step(connection: &Connection) -> StoreResult<()> {
    verify_exact_schema_identity(connection, Some(PRE_EFFORT_SCHEMA_VERSION))?;
    apply_effort_migration(connection)?;
    record_migration(connection, 12)?;
    verify_exact_schema_identity(connection, Some(CAMPAIGN_SCHEMA_VERSION))?;
    Ok(())
}

/// `if connection.in_transaction: connection.rollback()`.
fn rollback_if_open(connection: &Connection) {
    if !connection.is_autocommit() {
        let _ = connection.execute_batch("ROLLBACK");
    }
}

/// `Path.resolve()` (non-strict): absolute, with the existing part's links resolved.
fn resolve(path: &Path) -> PathBuf {
    let absolute = std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf());
    if let Ok(canonical) = std::fs::canonicalize(&absolute) {
        return strip_verbatim(canonical);
    }
    match (absolute.parent(), absolute.file_name()) {
        (Some(parent), Some(name)) => match std::fs::canonicalize(parent) {
            Ok(parent) => strip_verbatim(parent).join(name),
            Err(_) => absolute,
        },
        _ => absolute,
    }
}

/// Windows `canonicalize` returns `\\?\C:\...`; Python's `resolve()` returns `C:\...`.
fn strip_verbatim(path: PathBuf) -> PathBuf {
    let text = path.to_string_lossy();
    if let Some(rest) = text.strip_prefix(r"\\?\") {
        if rest.as_bytes().get(1) == Some(&b':') {
            return PathBuf::from(rest);
        }
        if let Some(unc) = rest.strip_prefix("UNC\\") {
            return PathBuf::from(format!(r"\\{unc}"));
        }
    }
    path
}
