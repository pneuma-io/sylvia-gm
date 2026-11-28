//! Bounded package reads for read-only application queries (lane 1's library sources):
//! `SQLiteAuthorityStore.package_file_paths` and `package_file_bytes`, and `AuthorityOwner`'s
//! `package_file_paths`, `package_text_file`, `package_record` and `package_label`.
//!
//! Python opens a connection for each `package_file_bytes` call. Opening one costs about as much
//! as reading a small file, so a caller reading many files in one store call (the knowledge
//! search reads every text file of its packages) reads them through one [`PackageFileReader`].
//! The answers are the same: each read is its own autocommit statement, and an installed
//! package's files and blobs never change.

use crate::campaigns::{named_rows, parse_canonical};
use crate::error::{value_error, StoreError, StoreResult};
use crate::handle::StoreHandle;
use crate::packages::{exact_manifest, verify_blob};
use crate::pytext::{py_int, py_str};
use crate::store::AuthorityStore;
use rusqlite::Connection;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::owned_payload;

/// `AuthorityOwner.package_file_paths`'s default bound.
pub const DEFAULT_MAXIMUM_FILES: i64 = 512;
/// `AuthorityOwner.package_text_file`'s default bound.
pub const DEFAULT_MAXIMUM_BYTES: i64 = 64 * 1024;

fn rejected(message: &str, code: &str) -> StoreError {
    crate::actions::rejected(message, code)
}

impl AuthorityStore {
    /// `package_file_paths`: one exact package's portable paths, in order, refused when there are
    /// more than `maximum_files`.
    pub fn package_file_paths(
        &self,
        package_id: &str,
        version: &str,
        sha256: &str,
        maximum_files: i64,
    ) -> StoreResult<Vec<String>> {
        if maximum_files < 1 {
            return Err(value_error("Package file limit must be positive"));
        }
        // Python binds `maximum_files + 1`; past SQLite's integer range its `sqlite3` refuses.
        let limit = maximum_files
            .checked_add(1)
            .ok_or_else(|| StoreError::Other("Python int too large to convert to SQLite INTEGER".into()))?;
        let rows = named_rows(
            &self.connect()?,
            "SELECT portable_path FROM package_manifest_files \
             WHERE package_id = ? AND version = ? AND content_sha256 = ? ORDER BY portable_path LIMIT ?",
            &[&package_id, &version, &sha256, &limit],
        )?
        .1;
        if rows.len() as i64 > maximum_files {
            return Err(rejected(
                "Package has too many files for this consumer",
                "package_file_catalog_oversized",
            ));
        }
        Ok(rows.iter().map(|row| py_str(&row[0])).collect())
    }

    /// `package_file_bytes`: one exact package file under a byte bound, or `None` when the
    /// package has no such path.
    pub fn package_file_bytes(
        &self,
        package_id: &str,
        version: &str,
        sha256: &str,
        portable_path: &str,
        maximum_bytes: i64,
    ) -> StoreResult<Option<Vec<u8>>> {
        if maximum_bytes < 0 {
            return Err(value_error("Package file byte limit cannot be negative"));
        }
        self.package_file_reader()?.file_bytes(package_id, version, sha256, portable_path, maximum_bytes)
    }

    /// One connection for several [`AuthorityStore::package_file_bytes`] reads.
    pub fn package_file_reader(&self) -> StoreResult<PackageFileReader<'_>> {
        Ok(PackageFileReader { connection: self.connect()?, _store: self })
    }

    /// `AuthorityOwner.package_text_file`: one exact package file as UTF-8 (pass
    /// [`DEFAULT_MAXIMUM_BYTES`] for Python's default). A missing path and text that is not
    /// UTF-8 are `CommandRejected`. For synchronous callers on the store thread, such as lane 1's
    /// library sources; [`StoreHandle::package_text_file`] is the same read.
    pub fn package_text_file(
        &self,
        package_id: &str,
        version: &str,
        content_sha256: &str,
        portable_path: &str,
        maximum_bytes: i64,
    ) -> StoreResult<String> {
        let Some(value) =
            self.package_file_bytes(package_id, version, content_sha256, portable_path, maximum_bytes)?
        else {
            return Err(rejected("Package source path is unavailable", "command_rejected"));
        };
        String::from_utf8(value)
            .map_err(|_| rejected("Package source is not valid UTF-8", "command_rejected"))
    }

    /// The exact manifest's canonical record, as `parse_canonical_json_bytes` reads it.
    fn manifest_record(&self, package_id: &str, version: &str, content_sha256: &str) -> StoreResult<Json> {
        let manifest = exact_manifest(&self.connect()?, (package_id, version, content_sha256))?;
        parse_canonical(&manifest.canonical_json)
    }

    /// `AuthorityOwner.package_record`: one exact installed package record.
    pub fn package_record(
        &self,
        package_id: &str,
        version: &str,
        content_sha256: &str,
    ) -> StoreResult<JsonMap> {
        Ok(owned_payload(&self.manifest_record(package_id, version, content_sha256)?)?)
    }

    /// `AuthorityOwner.package_label`: `str(record["metadata"]["label"])`. A record without one is
    /// Python's uncaught `KeyError` or `TypeError`.
    pub fn package_label(
        &self,
        package_id: &str,
        version: &str,
        content_sha256: &str,
    ) -> StoreResult<String> {
        let record = self.manifest_record(package_id, version, content_sha256)?;
        match record.get("metadata").and_then(|metadata| metadata.get("label")) {
            Some(label) => Ok(json_text(label)),
            None => Err(StoreError::Other("'label'".into())),
        }
    }
}

/// One connection for several exact package file reads, from
/// [`AuthorityStore::package_file_reader`] (see the module note).
pub struct PackageFileReader<'a> {
    connection: Connection,
    _store: &'a AuthorityStore,
}

impl PackageFileReader<'_> {
    /// [`AuthorityStore::package_file_bytes`] on this reader's connection.
    pub fn file_bytes(
        &self,
        package_id: &str,
        version: &str,
        sha256: &str,
        portable_path: &str,
        maximum_bytes: i64,
    ) -> StoreResult<Option<Vec<u8>>> {
        if maximum_bytes < 0 {
            return Err(value_error("Package file byte limit cannot be negative"));
        }
        let Some(row) = named_rows(
            &self.connection,
            "SELECT byte_count, blob_sha256 FROM package_manifest_files \
             WHERE package_id = ? AND version = ? AND content_sha256 = ? AND portable_path = ?",
            &[&package_id, &version, &sha256, &portable_path],
        )?
        .1
        .into_iter()
        .next() else {
            return Ok(None);
        };
        let byte_count =
            py_int(&row[0]).ok_or_else(|| value_error("invalid literal for int() with base 10"))?;
        if byte_count > maximum_bytes {
            return Err(rejected("Package file exceeds its consumer byte limit", "package_file_oversized"));
        }
        let value = {
            let mut chunks = verify_blob(&self.connection, &py_str(&row[1]))?;
            if chunks.len() == 1 {
                chunks.pop().expect("one chunk")
            } else {
                chunks.concat()
            }
        };
        if value.len() as i64 != byte_count {
            return Err(rejected("Exact package blob is corrupt", "package_corrupt"));
        }
        Ok(Some(value))
    }
}

/// `str(value)` of a parsed JSON value.
fn json_text(value: &Json) -> String {
    crate::campaigns::json_str(value)
}

impl StoreHandle {
    /// `AuthorityOwner.package_file_paths` (`_operation()` around the store's read; pass
    /// [`DEFAULT_MAXIMUM_FILES`] for Python's default).
    pub async fn package_file_paths(
        &self,
        package_id: &str,
        version: &str,
        content_sha256: &str,
        maximum_files: i64,
    ) -> StoreResult<Vec<String>> {
        let _operation = self.operation()?;
        let (package_id, version, sha256) =
            (package_id.to_owned(), version.to_owned(), content_sha256.to_owned());
        self.call(move |store| store.package_file_paths(&package_id, &version, &sha256, maximum_files)).await
    }

    /// `AuthorityOwner.package_text_file` ([`AuthorityStore::package_text_file`] on the store
    /// thread).
    pub async fn package_text_file(
        &self,
        package_id: &str,
        version: &str,
        content_sha256: &str,
        portable_path: &str,
        maximum_bytes: i64,
    ) -> StoreResult<String> {
        let _operation = self.operation()?;
        let (package_id, version, sha256, path) =
            (package_id.to_owned(), version.to_owned(), content_sha256.to_owned(), portable_path.to_owned());
        self.call(move |store| store.package_text_file(&package_id, &version, &sha256, &path, maximum_bytes))
            .await
    }

    /// `AuthorityOwner.package_record` ([`AuthorityStore::package_record`] on the store thread).
    pub async fn package_record(
        &self,
        package_id: &str,
        version: &str,
        content_sha256: &str,
    ) -> StoreResult<JsonMap> {
        let _operation = self.operation()?;
        let (package_id, version, sha256) =
            (package_id.to_owned(), version.to_owned(), content_sha256.to_owned());
        self.call(move |store| store.package_record(&package_id, &version, &sha256)).await
    }

    /// `AuthorityOwner.package_label` ([`AuthorityStore::package_label`] on the store thread).
    pub async fn package_label(
        &self,
        package_id: &str,
        version: &str,
        content_sha256: &str,
    ) -> StoreResult<String> {
        let _operation = self.operation()?;
        let (package_id, version, sha256) =
            (package_id.to_owned(), version.to_owned(), content_sha256.to_owned());
        self.call(move |store| store.package_label(&package_id, &version, &sha256)).await
    }
}
