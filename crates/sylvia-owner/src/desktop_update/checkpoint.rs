//! `application/desktop_update/checkpoint.py`: a consistent authority backup taken before an
//! update, and its explicit, resumable restore.

use super::manifest::{bounded_json, canonical_bytes, PayloadFile, Version, MANIFEST_LIMIT};
use super::replacement::{copy_file, file_matches};
use super::storage::{
    atomic_receipt, checked_file, directory_identity, ensure_directory, operation_id, os_error, present,
    Identity,
};
use super::update_error;
use crate::util::uuid4_hex;
use std::io::Read;
use std::path::{Path, PathBuf};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_store::owner_lock::{authority_lock_identity, OwnerLock, OwnerLockError};

/// `STORAGE_COMPATIBILITY`: bumped when stored semantics change even without SQL DDL changes.
pub const STORAGE_COMPATIBILITY: i64 = 1;

/// `RESTORE_CONFIRMATION`: the exact words that admit losing post-checkpoint progress.
pub const RESTORE_CONFIRMATION: &str = "Restore pre-update data";

/// `RestorePhase`.
pub mod restore_phase {
    pub const RESTORING: &str = "restoring";
    pub const RESTORED: &str = "restored";
}

/// `FILE_ATTRIBUTE_REPARSE_POINT` set on a file's own metadata.
fn reparse_attribute(metadata: &std::fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        let _ = metadata;
        false
    }
}

/// `st_nlink` of a path, not following a final link.
fn link_count(path: &Path) -> std::io::Result<u64> {
    #[cfg(windows)]
    {
        let handle = winapi_util::Handle::from_path_any(path)?;
        Ok(winapi_util::file::information(&handle)?.number_of_links())
    }
    #[cfg(not(windows))]
    {
        use std::os::unix::fs::MetadataExt;
        Ok(std::fs::symlink_metadata(path)?.nlink())
    }
}

/// A regular, single-linked, non-reparse file (`S_ISREG`, `st_nlink == 1`, no reparse
/// attribute), from `lstat`.
fn private_regular_file(path: &Path) -> std::io::Result<bool> {
    let metadata = std::fs::symlink_metadata(path)?;
    if !metadata.file_type().is_file() || reparse_attribute(&metadata) {
        return Ok(false);
    }
    Ok(link_count(path)? == 1)
}

/// `acquire_update_data_lock(database)`: refuse an aliased lock file before the authority
/// lock may create or append a byte, then take it. A held lock is Python's `OwnerConflict`.
pub fn acquire_update_data_lock(database: &Path) -> CodedResult<OwnerLock> {
    directory_identity(database.parent().unwrap_or(Path::new("")))?;
    let identity = authority_lock_identity(database);
    let mut name = identity.file_name().unwrap_or_default().to_os_string();
    name.push(".owner.lock");
    let path = identity.with_file_name(name);
    if present(&path) && !private_regular_file(&path).map_err(|error| os_error(&error))? {
        return Err(update_error("unsafe_data_lock", "The selected data ownership lock is unsafe."));
    }
    OwnerLock::acquire(&identity).map_err(|error| match error {
        OwnerLockError::Conflict(error) => error,
        OwnerLockError::Io(error) => os_error(&error),
    })
}

/// `_fingerprint(path)`: the size and SHA-256 of a regular private file, refused when it
/// changes while read.
fn fingerprint(path: &Path) -> CodedResult<PayloadFile> {
    directory_identity(path.parent().unwrap_or(Path::new("")))?;
    if !private_regular_file(path).map_err(|error| os_error(&error))? {
        return Err(update_error("unsafe_checkpoint", "A checkpoint input is not a regular private file."));
    }
    let changed = || update_error("checkpoint_changed", "Checkpoint inputs changed during preparation.");
    let size = std::fs::symlink_metadata(path).map_err(|error| os_error(&error))?.len();
    let before = super::storage::file_identity(path).map_err(|error| os_error(&error))?;
    use sha2::Digest;
    let mut digest = sha2::Sha256::new();
    let mut count = 0u64;
    let read = (|| -> std::io::Result<Result<(), CodedError>> {
        let mut stream = std::fs::File::open(path)?;
        if super::storage::open_identity(&stream)? != before {
            return Ok(Err(update_error("checkpoint_changed", "A checkpoint input changed before reading.")));
        }
        let mut chunk = vec![0u8; 256 * 1024];
        loop {
            let read = stream.read(&mut chunk)?;
            if read == 0 {
                return Ok(Ok(()));
            }
            count += read as u64;
            if count > size {
                return Ok(Err(changed()));
            }
            digest.update(&chunk[..read]);
        }
    })();
    read.map_err(|error| os_error(&error))??;
    let name = path.file_name().unwrap_or_default().to_string_lossy().to_string();
    let row = PayloadFile { path: name, size: count, sha256: hex::encode(digest.finalize()) };
    if !file_matches(path, &row)? {
        return Err(changed());
    }
    Ok(row)
}

fn record(row: &PayloadFile) -> Json {
    let mut value = JsonMap::new();
    value.insert("path".into(), row.path.clone().into());
    value.insert("size".into(), row.size.into());
    value.insert("sha256".into(), row.sha256.clone().into());
    Json::Object(value)
}

/// `_parse_record(value, name)`.
fn parse_record(value: &Json, name: &str) -> CodedResult<PayloadFile> {
    let invalid = || update_error("invalid_checkpoint", "The update checkpoint receipt is invalid.");
    let object = value.as_object().ok_or_else(invalid)?;
    let keys_ok = object.len() == 3 && ["path", "size", "sha256"].iter().all(|key| object.contains_key(*key));
    let size = match object.get("size") {
        Some(size) if size.is_number() => size.as_u64(),
        _ => None,
    };
    let sha256 = object.get("sha256").and_then(Json::as_str).filter(|text| {
        text.len() == 64 && text.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
    });
    match (keys_ok && object.get("path") == Some(&Json::String(name.to_string())), size, sha256) {
        (true, Some(size), Some(sha256)) => {
            Ok(PayloadFile { path: name.to_string(), size, sha256: sha256.to_string() })
        }
        _ => Err(invalid()),
    }
}

/// `schema_identity(database)`: the schema version and a hash of the schema rows, read only;
/// no rows, content, provider data or raw schema SQL leave this function.
pub fn schema_identity(database: &Path) -> CodedResult<(i64, String)> {
    directory_identity(database.parent().unwrap_or(Path::new("")))?;
    if !private_regular_file(database).map_err(|error| os_error(&error))? {
        return Err(update_error("unsafe_checkpoint", "The intended database is an alias or reparse file."));
    }
    let unavailable =
        |_| update_error("checkpoint_unavailable", "The intended storage could not be checked safely.");
    let limit =
        || update_error("checkpoint_limit", "The storage schema exceeds the supported checkpoint limit.");
    let connection = rusqlite::Connection::open_with_flags(
        database,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(unavailable)?;
    let read = (|| -> Result<Result<(i64, String), CodedError>, rusqlite::Error> {
        connection.execute_batch("BEGIN")?;
        let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        let mut statement =
            connection.prepare("SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name")?;
        let mut rows = statement.query([])?;
        let mut schema = Vec::new();
        while let Some(row) = rows.next()? {
            if schema.len() == 5000 {
                return Ok(Err(limit()));
            }
            let text = |index: usize| -> Result<Json, rusqlite::Error> {
                Ok(row.get::<_, Option<String>>(index)?.map_or(Json::Null, Json::String))
            };
            schema.push(Json::Array(vec![text(0)?, text(1)?, text(2)?, text(3)?]));
        }
        let raw = canonical_bytes(&Json::Array(schema));
        if raw.len() > MANIFEST_LIMIT {
            return Ok(Err(limit()));
        }
        Ok(Ok((version, sylvia_base::hash::sha256_hex(&raw))))
    })();
    let outcome = read.map_err(unavailable)?;
    drop(connection);
    outcome
}

/// `DataCheckpoint`: a backup of the authority database and a copy of the local settings,
/// whose DPAPI binding is kept as it is.
#[derive(Debug, Clone)]
pub struct DataCheckpoint {
    pub database: PathBuf,
    pub operation: PathBuf,
    pub root: PathBuf,
    data_identity: Identity,
    operation_identity: Identity,
}

const CHECKPOINT_KEYS: [&str; 7] = [
    "format_version",
    "app_version",
    "storage_compatibility",
    "database",
    "config",
    "schema_version",
    "schema_sha256",
];

impl DataCheckpoint {
    /// `DataCheckpoint(database, operation)`.
    pub fn new(database: &Path, operation: &Path) -> CodedResult<Self> {
        let data_identity = directory_identity(database.parent().unwrap_or(Path::new("")))?;
        let operation_identity = directory_identity(operation)?;
        let name = database.file_name().map(|name| name.to_string_lossy().to_string()).unwrap_or_default();
        if !database.is_absolute() || matches!(name.as_str(), "config.yaml" | "" | "." | "..") {
            return Err(update_error("unsafe_checkpoint", "The intended database location is invalid."));
        }
        Ok(Self {
            database: database.to_path_buf(),
            operation: operation.to_path_buf(),
            root: operation.join("data-checkpoint"),
            data_identity,
            operation_identity,
        })
    }

    /// `_check()`.
    fn check(&self) -> CodedResult<()> {
        if directory_identity(self.database.parent().unwrap_or(Path::new("")))? != self.data_identity
            || directory_identity(&self.operation)? != self.operation_identity
        {
            return Err(update_error("unsafe_checkpoint", "The checkpoint or data location changed."));
        }
        Ok(())
    }

    /// `prepare(backup_database, work_quiesced, app_version)`.
    pub fn prepare(
        &self,
        backup_database: &dyn Fn(&Path) -> CodedResult<()>,
        work_quiesced: &dyn Fn() -> bool,
        app_version: &str,
    ) -> CodedResult<()> {
        self.check()?;
        Version::parse_text(app_version)?;
        if !work_quiesced() {
            return Err(update_error("application_busy", "Finish active work before preparing an update."));
        }
        if present(&self.root) {
            return Err(update_error(
                "checkpoint_exists",
                "An existing checkpoint must be retained or reconciled.",
            ));
        }
        std::fs::create_dir(&self.root).map_err(|error| os_error(&error))?;
        // Reuse the owning authority's coherent, verified online backup.
        backup_database(&self.root.join("authority.sqlite3"))?;
        let database = fingerprint(&self.root.join("authority.sqlite3"))?;
        let (schema_version, schema_sha256) = schema_identity(&self.root.join(&database.path))?;
        if !work_quiesced() {
            return Err(update_error(
                "application_busy",
                "New work began; this update cannot close the app.",
            ));
        }
        let config = self.database.parent().unwrap_or(Path::new("")).join("config.yaml");
        let mut config_row = None;
        if present(&config) {
            let size = std::fs::symlink_metadata(&config).map_err(|error| os_error(&error))?.len();
            if size > 8 * 1024 * 1024 {
                return Err(update_error(
                    "checkpoint_limit",
                    "The settings file is too large to checkpoint safely.",
                ));
            }
            let row = fingerprint(&config)?;
            copy_file(&config, &self.root.join(&row.path), &row)?;
            config_row = Some(row);
        }
        let mut value = JsonMap::new();
        value.insert("format_version".into(), 1.into());
        value.insert("app_version".into(), app_version.into());
        value.insert("storage_compatibility".into(), STORAGE_COMPATIBILITY.into());
        value.insert("database".into(), record(&database));
        value.insert("config".into(), config_row.as_ref().map_or(Json::Null, record));
        value.insert("schema_version".into(), schema_version.into());
        value.insert("schema_sha256".into(), schema_sha256.into());
        atomic_receipt(&self.root.join("checkpoint.json"), &Json::Object(value))
    }

    /// `_load(expected_storage_compatibility=)`: the checkpoint receipt, with the retained
    /// files and schema checked against it.
    pub fn load(&self, expected_storage_compatibility: i64) -> CodedResult<JsonMap> {
        if !(0 < expected_storage_compatibility && expected_storage_compatibility < 1 << 31) {
            return Err(update_error("invalid_checkpoint", "The retained storage contract is invalid."));
        }
        self.check()?;
        let value =
            bounded_json(&checked_file(&self.root.join("checkpoint.json"), 16 * 1024)?, MANIFEST_LIMIT)?;
        let incompatible = || update_error("invalid_checkpoint", "The update checkpoint is incompatible.");
        let Json::Object(value) = value else {
            return Err(incompatible());
        };
        let integer = |key: &str| value.get(key).filter(|item| item.is_number()).and_then(Json::as_i64);
        if value.len() != CHECKPOINT_KEYS.len()
            || !CHECKPOINT_KEYS.iter().all(|key| value.contains_key(*key))
            || integer("format_version") != Some(1)
            || integer("storage_compatibility") != Some(expected_storage_compatibility)
            || !integer("schema_version").is_some_and(|version| version >= 0)
        {
            return Err(incompatible());
        }
        Version::parse(&value["app_version"])?;
        let database = parse_record(&value["database"], "authority.sqlite3")?;
        if !file_matches(&self.root.join(&database.path), &database)? {
            return Err(update_error("invalid_checkpoint", "The retained database checkpoint changed."));
        }
        let schema = schema_identity(&self.root.join(&database.path))?;
        if value["schema_version"] != schema.0 || value["schema_sha256"] != schema.1.as_str() {
            return Err(update_error("invalid_checkpoint", "The retained database schema changed."));
        }
        if !value["config"].is_null() {
            let config = parse_record(&value["config"], "config.yaml")?;
            if !file_matches(&self.root.join(&config.path), &config)? {
                return Err(update_error("invalid_checkpoint", "The retained settings checkpoint changed."));
            }
        }
        Ok(value)
    }

    /// `require_compatible(started_storage_compatibility, expected_storage_compatibility=)`.
    pub fn require_compatible(&self, started: i64, expected_storage_compatibility: i64) -> CodedResult<()> {
        let row = self.load(expected_storage_compatibility)?;
        let schema = schema_identity(&self.database)?;
        if row["storage_compatibility"] != started
            || row["schema_version"] != schema.0
            || row["schema_sha256"] != schema.1.as_str()
        {
            return Err(update_error(
                "restore_choice_required",
                "The new data format is incompatible. Restoring the checkpoint would lose progress made since the update; choose explicitly before restoring.",
            ));
        }
        Ok(())
    }

    /// `_paths()`: the live files a restore replaces, by key.
    fn paths(&self) -> [(&'static str, PathBuf); 4] {
        let with_suffix = |suffix: &str| {
            let mut name = self.database.file_name().unwrap_or_default().to_os_string();
            name.push(suffix);
            self.database.with_file_name(name)
        };
        [
            ("database", self.database.clone()),
            ("wal", with_suffix("-wal")),
            ("shm", with_suffix("-shm")),
            ("config", self.database.parent().unwrap_or(Path::new("")).join("config.yaml")),
        ]
    }

    /// `restore(confirmation, processes_exited)`: newer data is retained before an explicitly
    /// chosen restore; no work is replayed.
    pub fn restore(
        &self,
        confirmation: &str,
        processes_exited: &dyn Fn() -> CodedResult<bool>,
    ) -> CodedResult<()> {
        if confirmation != RESTORE_CONFIRMATION {
            return Err(update_error(
                "restore_choice_required",
                "Confirm the loss of post-checkpoint progress before restoring.",
            ));
        }
        self.check()?;
        if !processes_exited()? {
            return Err(update_error(
                "application_busy",
                "Close all owned app processes before restoring data.",
            ));
        }
        let mut lock = acquire_update_data_lock(&self.database)?;
        let outcome = self.restore_locked(processes_exited);
        lock.close();
        outcome
    }

    /// `_restore_receipt()`: a retained restore intent, validated before a missing live file
    /// is treated as resumable.
    fn restore_receipt(&self) -> CodedResult<Option<JsonMap>> {
        self.check()?;
        let receipt = self.root.join("restore.json");
        if !present(&receipt) {
            return Ok(None);
        }
        let row = bounded_json(&checked_file(&receipt, 16 * 1024)?, MANIFEST_LIMIT)?;
        let reconcile =
            || update_error("invalid_checkpoint", "The interrupted restore needs manual reconciliation.");
        let Json::Object(row) = row else {
            return Err(reconcile());
        };
        let keys = ["format_version", "phase", "generation", "before"];
        let paths = self.paths();
        let before_ok = row.get("before").and_then(Json::as_object).is_some_and(|before| {
            before.len() == paths.len() && paths.iter().all(|(key, _)| before.contains_key(*key))
        });
        if row.len() != keys.len()
            || !keys.iter().all(|key| row.contains_key(*key))
            || !(row["format_version"].is_number() && row["format_version"].as_i64() == Some(1))
            || !matches!(row["phase"].as_str(), Some(restore_phase::RESTORING | restore_phase::RESTORED))
            || !before_ok
        {
            return Err(reconcile());
        }
        operation_id(&row["generation"])?;
        for (key, path) in &paths {
            let value = &row["before"][*key];
            if !value.is_null() {
                parse_record(value, &path.file_name().unwrap_or_default().to_string_lossy())?;
            }
        }
        Ok(Some(row))
    }

    /// `restore_pending()`: inspection may resume a recorded restore without opening a
    /// temporarily retained database.
    pub fn restore_pending(&self) -> CodedResult<bool> {
        Ok(self.restore_receipt()?.is_some_and(|row| row["phase"] == restore_phase::RESTORING))
    }

    /// `_restore_locked(processes_exited)`, under the data lock.
    pub fn restore_locked(&self, processes_exited: &dyn Fn() -> CodedResult<bool>) -> CodedResult<()> {
        let checkpoint = self.load(STORAGE_COMPATIBILITY)?;
        let receipt = self.root.join("restore.json");
        let paths = self.paths();
        let (generation, mut row) = match self.restore_receipt()? {
            Some(row) => {
                let generation = operation_id(&row["generation"])?;
                if row["phase"] == restore_phase::RESTORED {
                    // A duplicate request must never discard later player progress.
                    return Ok(());
                }
                (generation, row)
            }
            None => {
                let generation = uuid4_hex();
                let mut row = JsonMap::new();
                row.insert("format_version".into(), 1.into());
                row.insert("phase".into(), restore_phase::RESTORING.into());
                row.insert("generation".into(), generation.clone().into());
                let mut before = JsonMap::new();
                for (key, path) in &paths {
                    let value = if present(path) { record(&fingerprint(path)?) } else { Json::Null };
                    before.insert((*key).into(), value);
                }
                row.insert("before".into(), Json::Object(before));
                atomic_receipt(&receipt, &Json::Object(row.clone()))?;
                (generation, row)
            }
        };
        let before = row["before"].clone();
        let retained = self.root.join("post-checkpoint").join(&generation);
        ensure_directory(&retained)?;
        directory_identity(&retained)?;
        let desired_database = parse_record(&checkpoint["database"], "authority.sqlite3")?;
        let desired_config = if checkpoint["config"].is_null() {
            None
        } else {
            Some(parse_record(&checkpoint["config"], "config.yaml")?)
        };
        let desired = |key: &str| match key {
            "database" => Some(desired_database.clone()),
            "config" => desired_config.clone(),
            _ => None,
        };
        let changed = || update_error("checkpoint_changed", "Data changed during the interrupted restore.");
        for (key, target) in &paths {
            self.check()?;
            if !processes_exited()? {
                return Err(update_error("application_busy", "A process is still using the intended data."));
            }
            let value = &before[*key];
            if value.is_null() {
                if present(target) {
                    match desired(key) {
                        Some(expected) if file_matches(target, &expected)? => {}
                        _ => return Err(changed()),
                    }
                }
                continue;
            }
            let name = target.file_name().unwrap_or_default().to_string_lossy().to_string();
            let original = parse_record(value, &name)?;
            let saved = retained.join(&name);
            if present(&saved) {
                if !file_matches(&saved, &original)? {
                    return Err(update_error("checkpoint_changed", "Retained post-checkpoint data changed."));
                }
                if !present(target) {
                    continue;
                }
                if let Some(expected) = desired(key) {
                    if file_matches(target, &expected)? {
                        continue;
                    }
                }
            }
            if !file_matches(target, &original)? {
                return Err(changed());
            }
            std::fs::rename(target, &saved).map_err(|error| os_error(&error))?;
        }
        for key in ["database", "config"] {
            let Some(record) = desired(key) else { continue };
            let target = &paths.iter().find(|(name, _)| *name == key).expect("a known key").1;
            if file_matches(target, &record)? {
                continue;
            }
            let temporary = self.root.join("restore-payload").join(uuid4_hex()).join(&record.path);
            copy_file(&self.root.join(&record.path), &temporary, &record)?;
            self.check()?;
            if !processes_exited()? || present(target) {
                return Err(update_error("checkpoint_changed", "The intended restore location changed."));
            }
            std::fs::rename(&temporary, target).map_err(|error| os_error(&error))?;
        }
        row.insert("phase".into(), restore_phase::RESTORED.into());
        atomic_receipt(&receipt, &Json::Object(row))
    }
}
