//! Publishing one portable campaign archive: `SQLiteAuthorityStore.export_campaign` with
//! `_validate_export_target_lexical`, `_validate_export_envelope`, `_write_export_member`,
//! `_write_export_blob` and `_validate_export_claim`.
//!
//! The sequence and every failure code are Python's:
//! 1. refuse an unsupported target name, capture the campaign ([`crate::export`]) and validate
//!    the target's envelope;
//! 2. claim a private temporary file beside the target under a write guard, as a backup does;
//! 3. stream the archive into it (`ZipFile(path, "w", ZIP_STORED, allowZip64=True)`): the
//!    authority member, every package blob (read chunk by chunk under a package read lease,
//!    through a spool), the private provider member, then the manifest;
//! 4. take the content seal, close the write guard, flush, validate the archive as a recovery
//!    would ([`crate::archive_validation`]) and hash it;
//! 5. revalidate the claim and the envelope, then atomically replace the target.
//!
//! Any failure cleans up what this export owns; a cleanup that is not complete adds
//! `portable_export_cleanup_incomplete`. A publication whose durability is uncertain is not
//! cleaned up: the renamed archive is then the only artifact.
//!
//! The fault seam `_export_failure_stage(stage)` is [`crate::store::Faults::export_stage`], called
//! at every stage Python names. The caller must exclusively manage the target directory for the
//! whole call; the guard and the seal are defence in depth, as in Python.
//!
//! The blob spool is Python's `SpooledTemporaryFile(max_size=4 * 64 KiB)`, rolled over to a file
//! in the system temporary folder after the first chunk: a file deleted when closed (Windows) or
//! unlinked as soon as it is made (elsewhere), as `tempfile.TemporaryFile` makes it.

use crate::backup::{
    acquire_content_seal, anchor, cleanup_owned, lexists, lstat_identity, path_key, samefile, Backup,
    BackupFailure, Claimed, Envelope,
};
use crate::campaign_archive::{checked_archive_ledger, limit, make_manifest, manifest_bytes, ManifestInput};
use crate::campaign_archive::{PRIVATE_MEMBER_NAME, TABLES};
use crate::error::StoreError;
use crate::export::{ExportCapture, ExportFailure};
use crate::pytext::{py_int, py_str};
use crate::safe_file::{self, stat_identity, Publication};
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use sha2::Digest;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::CampaignExportResult;
use sylvia_domain::package_archive::zipfile::ZIP_STORED;
use sylvia_domain::package_archive::zipfile_write::{StreamedMemberWriter, StreamingZipWriter, ZipMember};

/// `STREAM_CHUNK_BYTES`.
const STREAM_CHUNK_BYTES: usize = 65_536;

/// Why an export failed: Python's `_ExportFailure`, or what escapes `export_campaign` before its
/// claim: the `_BackupFailure` of a refused ancestor, or an `OSError` while the envelope is read.
#[derive(Debug)]
pub enum ExportError {
    /// `_ExportFailure`, with its cleanup diagnostics.
    Failure(ExportFailure),
    /// Anything else, as the owner reports it.
    Store(StoreError),
}

impl From<ExportFailure> for ExportError {
    fn from(failure: ExportFailure) -> Self {
        Self::Failure(failure)
    }
}

impl From<ExportError> for StoreError {
    fn from(error: ExportError) -> Self {
        match error {
            ExportError::Failure(failure) => failure.into_store_error(),
            ExportError::Store(error) => error,
        }
    }
}

/// `_as_export_failure(error)`: a `CommandRejected` keeps its code; anything else is
/// `portable_export_publication_failed`.
fn as_failure(error: StoreError) -> ExportFailure {
    match error.rejection() {
        Some(coded)
            if matches!(
                coded.error_type,
                None | Some("CommandRejected" | "_ExportFailure" | "_BackupFailure")
            ) =>
        {
            ExportFailure::new(coded.code)
        }
        _ => publication_failed(),
    }
}

fn publication_failed() -> ExportFailure {
    ExportFailure::new("portable_export_publication_failed")
}

/// A `_BackupFailure` raised inside the export: a `CommandRejected`, so its code is kept.
fn from_backup(failure: BackupFailure) -> ExportFailure {
    ExportFailure::new(failure.code())
}

/// `campaign_archive.fixed_zip_info(name)`.
fn fixed_zip_info(name: &str) -> ZipMember {
    ZipMember {
        filename: name.to_owned(),
        date_time: (1980, 1, 1, 0, 0, 0),
        compress_type: ZIP_STORED,
        create_system: 3,
        external_attr: 0o100600 << 16,
    }
}

/// One `non_manifest_members` entry.
fn member_record(name: &str, byte_count: i64, sha256: &str) -> JsonMap {
    let mut record = JsonMap::new();
    record.insert("name".into(), name.into());
    record.insert("byte_count".into(), byte_count.into());
    record.insert("sha256".into(), sha256.into());
    record
}

/// `_validate_export_target_lexical`: no SQLite and no filesystem.
fn validate_target_lexical(target: &Path) -> Result<(), ExportFailure> {
    if safe_file::lexical_export_target_supported(target) {
        Ok(())
    } else {
        Err(ExportFailure::new("portable_export_destination_unsupported"))
    }
}

/// What `export_campaign` holds while it runs, for its cleanup.
#[derive(Default)]
struct Held {
    claimed: Option<Claimed>,
    guard: Option<File>,
    seal: Option<File>,
}

/// How a failure after the claim unwinds.
enum Unwind {
    /// Everything this export holds is closed and its temporary removed.
    Cleanup(ExportFailure),
    /// The publication's durability is uncertain: nothing is touched.
    Uncertain(ExportFailure),
}

impl From<ExportFailure> for Unwind {
    fn from(failure: ExportFailure) -> Self {
        Self::Cleanup(failure)
    }
}

type Step<T> = Result<T, ExportFailure>;

impl AuthorityStore {
    /// `export_campaign(campaign_id, target, include_thinking=...)`: publish one deterministic,
    /// self-contained campaign archive at `target`. The caller exclusively manages the target's
    /// directory for this call, including the private temporary name used inside it.
    pub fn export_campaign(
        &self,
        campaign_id: &str,
        target: &Path,
        include_thinking: bool,
    ) -> Result<CampaignExportResult, ExportError> {
        validate_target_lexical(target)?;
        let capture = self.capture_campaign_export(campaign_id, include_thinking)?;
        let backup = Backup::new(self.database_path());
        let envelope = self.validate_export_envelope(&backup, target, None, None)?;
        let mut held = Held::default();
        let failure = match self.export_claimed(&backup, campaign_id, &capture, &envelope, &mut held) {
            Ok(result) => return Ok(result),
            Err(Unwind::Uncertain(failure)) => return Err(failure.into()),
            Err(Unwind::Cleanup(failure)) => failure,
        };
        let mut incomplete = false;
        if let Some(seal) = held.seal.take() {
            incomplete |= !safe_file::close_handle(seal);
        }
        if let Some(guard) = held.guard.take() {
            incomplete |= !safe_file::close_handle(guard);
        }
        if let Some(claimed) = &held.claimed {
            incomplete |= !cleanup_owned(claimed).is_empty();
        }
        Err(if incomplete {
            failure.with_supplemental("portable_export_cleanup_incomplete")
        } else {
            failure
        }
        .into())
    }

    /// Everything from the first stage on, inside Python's `try`.
    fn export_claimed(
        &self,
        backup: &Backup<'_>,
        campaign_id: &str,
        capture: &ExportCapture,
        envelope: &Envelope,
        held: &mut Held,
    ) -> Result<CampaignExportResult, Unwind> {
        self.run_export_stage("metadata_spool")?;
        let (claimed, guard) = backup.claim_temporary(envelope).map_err(from_backup)?;
        held.guard = Some(guard);
        let claimed = held.claimed.insert(claimed);
        self.write_archive(&claimed.path, capture)?;
        self.run_export_stage("seal")?;
        held.seal = Some(acquire_content_seal(claimed).map_err(from_backup)?);
        self.run_export_stage("write_handle_close")?;
        if !safe_file::close_handle(held.guard.take().expect("the write guard is held")) {
            return Err(ExportFailure::new("portable_export_write_handle_close_failed").into());
        }
        self.run_export_stage("flush")?;
        if !safe_file::flush_handle(held.seal.as_ref().expect("the seal is held")) {
            return Err(ExportFailure::new("portable_export_flush_failed").into());
        }
        self.run_export_stage("reopen")?;
        self.run_export_stage("validate")?;
        crate::archive_validation::validate_campaign_archive(&claimed.path, campaign_id)
            .map_err(as_failure)?;
        self.run_export_stage("verifier_close")?;
        self.run_export_stage("hash")?;
        let (archive_sha256, byte_count) = hash_archive(&claimed.path)?;
        if !safe_file::close_handle(held.seal.take().expect("the seal is held")) {
            return Err(ExportFailure::new("portable_export_verifier_close_failed").into());
        }
        let result = CampaignExportResult {
            campaign_id: campaign_id.to_owned(),
            captured_revision: capture.captured_revision,
            archive_sha256,
            byte_count,
        };
        if validate_export_claim(backup, claimed, envelope).is_err() {
            return Err(ExportFailure::new("portable_export_namespace_changed").into());
        }
        self.validate_export_envelope(backup, &envelope.target, Some(envelope), Some(&claimed.path))
            .map_err(|error| match error {
                ExportError::Failure(failure) => failure,
                ExportError::Store(error) => as_failure(error),
            })?;
        self.run_export_stage("move_file_ex")?;
        match safe_file::publish_replace(&claimed.path, &envelope.target, envelope.parent_identity) {
            Publication::Published => Ok(result),
            Publication::DurabilityUncertain => {
                Err(Unwind::Uncertain(ExportFailure::new("portable_export_publication_durability_uncertain")))
            }
            Publication::NotPublished => {
                Err(ExportFailure::new("portable_export_move_file_ex_failed").into())
            }
        }
    }

    /// `_validate_export_envelope(target, expected=..., temporary=...)`. Before the claim its
    /// `_BackupFailure` and `OSError` escape as they are; after it they become export failures.
    fn validate_export_envelope(
        &self,
        backup: &Backup<'_>,
        target: &Path,
        expected: Option<&Envelope>,
        temporary: Option<&Path>,
    ) -> Result<Envelope, ExportError> {
        let refused =
            |code: &str| ExportError::Failure(ExportFailure::new(format!("portable_export_{code}")));
        let os = |error: std::io::Error| ExportError::Store(StoreError::Other(error.to_string()));
        validate_target_lexical(target)?;
        let normalized = safe_file::abspath(target);
        let volume_root = anchor(&normalized);
        if let Some(refusal) = safe_file::volume_admission(&normalized) {
            return Err(refused(&format!("destination_{refusal}")));
        }
        let parent = normalized.parent().map_or_else(|| normalized.clone(), Path::to_path_buf);
        let Some(parent_attributes) = safe_file::entry_attributes(&parent).map_err(os)? else {
            return Err(refused("destination_parent"));
        };
        if safe_file::attributes_are_link(parent_attributes) {
            return Err(refused("destination_reparse"));
        }
        if !std::fs::metadata(&parent).map_err(os)?.is_dir() {
            return Err(refused("destination_parent"));
        }
        if let Some(refusal) = safe_file::ancestors_admission(&parent).map_err(os)? {
            return Err(ExportError::Store(StoreError::Backup(BackupFailure::new(
                "target_envelope",
                refusal,
            ))));
        }
        let target_key = path_key(&normalized);
        let protected = backup.protected_source_paths();
        if target_key == path_key(self.database_path()) {
            return Err(refused("destination_source_alias"));
        }
        if protected.iter().any(|path| path_key(path) == target_key) {
            return Err(refused("destination_source_artifact"));
        }
        let mut target_identity = None;
        if let Some(attributes) = safe_file::entry_attributes(&normalized).map_err(os)? {
            if safe_file::attributes_are_link(attributes) {
                return Err(refused("destination_reparse"));
            }
            let metadata = std::fs::symlink_metadata(&normalized).map_err(os)?;
            if metadata.is_dir() {
                return Err(refused("destination_directory"));
            }
            if !metadata.file_type().is_file() {
                return Err(refused("destination_unsupported"));
            }
            target_identity = Some(lstat_identity(&normalized, &metadata).map_err(os)?);
            for path in &protected {
                if lexists(path) && samefile(&normalized, path).map_err(os)? {
                    return Err(refused("destination_source_alias"));
                }
            }
        }
        if temporary.is_some_and(|temporary| target_key == path_key(temporary)) {
            return Err(refused("destination_source_alias"));
        }
        let envelope = Envelope {
            target: normalized,
            parent_identity: stat_identity(&parent).map_err(os)?,
            target_identity,
            source_identity: stat_identity(self.database_path()).map_err(os)?,
            volume_root: path_key(&volume_root),
        };
        if expected.is_some_and(|expected| &envelope != expected) {
            return Err(refused("namespace_changed"));
        }
        Ok(envelope)
    }

    /// The archive, written into the claimed temporary and closed.
    fn write_archive(&self, path: &Path, capture: &ExportCapture) -> Step<()> {
        let file = std::fs::OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(true)
            .open(path)
            .map_err(|_| publication_failed())?;
        let mut archive = StreamingZipWriter::new(file).map_err(|_| publication_failed())?;
        if let Err(failure) = self.write_members(&mut archive, capture) {
            // Python closes the archive (`with suppress(BaseException)`); the temporary is then
            // removed with everything else the export owns.
            if let Ok(file) = archive.finish() {
                safe_file::close_handle(file);
            }
            return Err(failure);
        }
        let closed = archive.finish().map(safe_file::close_handle);
        if !matches!(closed, Ok(true)) {
            return Err(ExportFailure::new("portable_export_zip_final_close_failed"));
        }
        self.run_export_stage("zip_final_close")
    }

    fn write_members(&self, archive: &mut StreamingZipWriter<File>, capture: &ExportCapture) -> Step<()> {
        let authority = &capture.authority_bytes;
        self.write_export_member(archive, "authority.jsonl", authority)?;
        let mut non_manifest =
            vec![member_record("authority.jsonl", authority.len() as i64, &sha256_hex(authority))];
        for blob_sha256 in &capture.blob_sha256s {
            let byte_count = self.write_export_blob(archive, blob_sha256)?;
            non_manifest.push(member_record(&format!("blobs/{blob_sha256}"), byte_count, blob_sha256));
        }
        if let Some(private) = &capture.private_bytes {
            self.write_export_member(archive, PRIVATE_MEMBER_NAME, private)?;
            non_manifest.push(member_record(PRIVATE_MEMBER_NAME, private.len() as i64, &sha256_hex(private)));
        }
        let manifest = make_manifest(&ManifestInput {
            source_schema_version: capture.source_schema_version,
            source_schema_identity_sha256: &capture.source_schema_identity_sha256,
            migration_provenance: &capture.migration_provenance,
            campaign_id: &capture.campaign_id,
            captured_revision: capture.captured_revision,
            rows: &capture.rows,
            non_manifest_members: &non_manifest,
        })
        .map_err(as_failure)?;
        let encoded = manifest_bytes(&manifest).map_err(as_failure)?;
        self.check_ledger(capture, &non_manifest, encoded.len())?;
        self.write_export_member(archive, "manifest.json", &encoded)
    }

    /// `checked_archive_ledger(...)` over what was written. A sum past 64 bits is past every
    /// limit, so it is the limit's refusal, as Python's exact arithmetic gives.
    fn check_ledger(
        &self,
        capture: &ExportCapture,
        non_manifest: &[JsonMap],
        manifest_length: usize,
    ) -> Step<()> {
        let row_counts: Vec<i64> =
            TABLES.iter().map(|table| capture.rows.get(table.name).len() as i64).collect();
        let single_blob_bytes = capture
            .rows
            .get("package_blobs")
            .iter()
            .map(|row| row.get("byte_count").and_then(Json::as_i64).unwrap_or(0))
            .max()
            .unwrap_or(0);
        let over_total = || ExportFailure::new("portable_export_limit_total_uncompressed_bytes");
        let mut total = (capture.authority_bytes.len() as i64)
            .checked_add(manifest_length as i64)
            .ok_or_else(over_total)?;
        for member in &non_manifest[1..] {
            let count = member.get("byte_count").and_then(Json::as_i64).unwrap_or(0);
            total = total.checked_add(count).ok_or_else(over_total)?;
        }
        checked_archive_ledger(&[
            ("member_count", non_manifest.len() as i64 + 1),
            ("authority_record_count", row_counts.iter().sum()),
            ("single_table_row_count", row_counts.iter().copied().max().unwrap_or(0)),
            ("manifest_bytes", manifest_length as i64),
            ("authority_jsonl_bytes", capture.authority_bytes.len() as i64),
            ("single_authority_row_bytes", 0),
            ("single_text_or_json_value_bytes", 0),
            ("single_blob_bytes", single_blob_bytes),
            ("total_uncompressed_bytes", total),
        ])
        .map_err(as_failure)
    }

    /// `_write_export_member(archive, name, BytesIO(data), len(data))`.
    fn write_export_member(
        &self,
        archive: &mut StreamingZipWriter<File>,
        name: &str,
        data: &[u8],
    ) -> Step<()> {
        let close_failed = || ExportFailure::new("portable_export_zip_member_close_failed");
        let mut member =
            archive.open_member(fixed_zip_info(name), data.len() as u64).map_err(|_| close_failed())?;
        for chunk in data.chunks(STREAM_CHUNK_BYTES) {
            if member.write(chunk).is_err() {
                // `with archive.open(...)` closes the member before the error leaves it.
                let _ = member.close();
                return Err(close_failed());
            }
        }
        member.close().map_err(|_| close_failed())?;
        self.run_export_stage("zip_member_close")
    }

    /// `_write_export_blob(archive, blob_sha256)`: the blob's chunks verified into a spool under a
    /// package read lease, then written as `blobs/<sha256>`. The spool's and the lease's own
    /// failures are added after whatever failed first.
    fn write_export_blob(&self, archive: &mut StreamingZipWriter<File>, blob_sha256: &str) -> Step<i64> {
        let lease_id = format!("export-{}", uuid4_hex().map_err(|_| publication_failed())?);
        let mut lease_acquired = false;
        let mut spool: Option<Spool> = None;
        let mut failure =
            self.blob_into_member(archive, blob_sha256, &lease_id, &mut lease_acquired, &mut spool).err();
        let byte_count = match &failure {
            None => spool.as_ref().map_or(0, |spool| spool.length),
            Some(_) => 0,
        };
        let mut add = |code: &str| {
            failure = Some(match failure.take() {
                None => ExportFailure::new(code),
                Some(failure) => failure.with_supplemental(code),
            });
        };
        if let Some(spool) = spool {
            let stage_failed = self.run_export_stage("blob_spool_close").is_err();
            if !spool.close() || stage_failed {
                add("portable_export_blob_spool_close_failed");
            }
        }
        if lease_acquired {
            let released =
                self.run_export_stage("lease_release").is_ok() && self.release_lease(&lease_id).is_ok();
            if !released {
                add("portable_export_lease_release_failed");
            }
        }
        match failure {
            Some(failure) => Err(failure),
            None => Ok(byte_count as i64),
        }
    }

    fn blob_into_member(
        &self,
        archive: &mut StreamingZipWriter<File>,
        blob_sha256: &str,
        lease_id: &str,
        lease_acquired: &mut bool,
        slot: &mut Option<Spool>,
    ) -> Step<()> {
        let corrupt = || ExportFailure::new("portable_export_package_corrupt");
        self.run_export_stage("lease_acquire")?;
        let (declared_bytes, declared_chunks) = self.acquire_lease(blob_sha256, lease_id)?;
        *lease_acquired = true;
        self.run_export_stage("blob_spool_create")?;
        let spool = slot.insert(Spool::new());
        let mut digest = sha2::Sha256::new();
        for ordinal in 0..declared_chunks {
            let data = self.read_chunk(blob_sha256, ordinal)?;
            self.run_export_stage("blob_spool_write")?;
            spool.write(&data).map_err(|_| publication_failed())?;
            if ordinal == 0 {
                self.run_export_stage("blob_spool_rollover")?;
                spool.rollover().map_err(|_| publication_failed())?;
            }
            digest.update(&data);
        }
        if spool.length != declared_bytes as u64 || hex::encode(digest.finalize()) != blob_sha256 {
            return Err(corrupt());
        }
        self.run_export_stage("blob_spool_seek")?;
        spool.seek_start().map_err(|_| publication_failed())?;
        let byte_count = spool.length;
        let close_failed = || ExportFailure::new("portable_export_zip_member_close_failed");
        let mut member = archive
            .open_member(fixed_zip_info(&format!("blobs/{blob_sha256}")), byte_count)
            .map_err(|_| close_failed())?;
        if let Err(failure) = self.copy_spool(spool, &mut member, byte_count) {
            // `with archive.open(...)` closes the member before the error leaves it.
            let _ = member.close();
            return Err(failure);
        }
        member.close().map_err(|_| close_failed())?;
        self.run_export_stage("zip_member_close")
    }

    /// The spool's bytes into the open member, one stream chunk at a time.
    fn copy_spool(
        &self,
        spool: &mut Spool,
        member: &mut StreamedMemberWriter<'_, File>,
        byte_count: u64,
    ) -> Step<()> {
        let close_failed = || ExportFailure::new("portable_export_zip_member_close_failed");
        let mut remaining = byte_count;
        while remaining > 0 {
            self.run_export_stage("blob_spool_read")?;
            let want = remaining.min(STREAM_CHUNK_BYTES as u64) as usize;
            let chunk = spool.read(want).map_err(|_| close_failed())?;
            if chunk.is_empty() {
                return Err(ExportFailure::new("portable_export_package_corrupt"));
            }
            member.write(&chunk).map_err(|_| close_failed())?;
            remaining -= chunk.len() as u64;
        }
        Ok(())
    }

    /// The lease transaction: the blob's metadata checked, then the lease inserted.
    fn acquire_lease(&self, blob_sha256: &str, lease_id: &str) -> Step<(i64, i64)> {
        let corrupt = || ExportFailure::new("portable_export_package_corrupt");
        let connection = self.connect().map_err(as_failure)?;
        let outcome = (|| -> Step<(i64, i64)> {
            connection.execute_batch("BEGIN IMMEDIATE").map_err(|error| as_failure(error.into()))?;
            let metadata: Option<(Value, Value)> = connection
                .query_row(
                    "SELECT byte_count, chunk_count FROM package_blobs WHERE blob_sha256 = ?",
                    [blob_sha256],
                    |row| Ok((row.get(0)?, row.get(1)?)),
                )
                .map(Some)
                .or_else(|error| match error {
                    rusqlite::Error::QueryReturnedNoRows => Ok(None),
                    error => Err(error),
                })
                .map_err(|error| as_failure(error.into()))?;
            let (bytes, chunks) = metadata.ok_or_else(corrupt)?;
            let declared_bytes = py_int(&bytes).ok_or_else(publication_failed)?;
            let declared_chunks = py_int(&chunks).ok_or_else(publication_failed)?;
            // Python's exact floor division.
            let chunk = STREAM_CHUNK_BYTES as i128;
            if (i128::from(declared_bytes) + chunk - 1).div_euclid(chunk) != i128::from(declared_chunks) {
                return Err(corrupt());
            }
            if declared_bytes > limit("single_blob_bytes") {
                return Err(ExportFailure::new("portable_export_limit_single_blob_bytes"));
            }
            connection
                .execute(
                    "INSERT INTO package_read_leases(lease_id, blob_sha256) VALUES (?, ?)",
                    [lease_id, blob_sha256],
                )
                .map_err(|error| as_failure(error.into()))?;
            connection.execute_batch("COMMIT").map_err(|error| as_failure(error.into()))?;
            Ok((declared_bytes, declared_chunks))
        })();
        if outcome.is_err() && !connection.is_autocommit() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        outcome
    }

    /// One chunk, read in its own transaction and verified.
    fn read_chunk(&self, blob_sha256: &str, ordinal: i64) -> Step<Vec<u8>> {
        let corrupt = || ExportFailure::new("portable_export_package_corrupt");
        let reader = self.connect().map_err(as_failure)?;
        let read = (|| -> rusqlite::Result<Option<[Value; 4]>> {
            reader.execute_batch("BEGIN")?;
            let row = reader
                .query_row(
                    "SELECT chunk_order, byte_count, chunk_sha256, data \
                     FROM package_blob_chunks WHERE blob_sha256 = ? AND chunk_order = ?",
                    rusqlite::params![blob_sha256, ordinal],
                    |row| Ok([row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?]),
                )
                .map(Some)
                .or_else(|error| match error {
                    rusqlite::Error::QueryReturnedNoRows => Ok(None),
                    error => Err(error),
                })?;
            reader.execute_batch("COMMIT")?;
            Ok(row)
        })();
        if !reader.is_autocommit() {
            let _ = reader.execute_batch("ROLLBACK");
        }
        let [order, byte_count, chunk_sha256, data] =
            read.map_err(|error| as_failure(error.into()))?.ok_or_else(corrupt)?;
        // `bytes(row["data"])`: a blob as it is; an integer is that many zero bytes (one past a
        // stream chunk is refused below without being made).
        let (data, length) = match data {
            Value::Blob(data) => {
                let length = data.len() as i64;
                (data, length)
            }
            Value::Integer(count) if count < 0 => return Err(publication_failed()),
            Value::Integer(count) if count as u64 > STREAM_CHUNK_BYTES as u64 => (Vec::new(), count),
            Value::Integer(count) => (vec![0; count as usize], count),
            _ => return Err(publication_failed()),
        };
        // Python's `or` chain: each check only once the ones before it passed.
        if py_int(&order).ok_or_else(publication_failed)? != ordinal
            || py_int(&byte_count).ok_or_else(publication_failed)? != length
            || length > STREAM_CHUNK_BYTES as i64
            || py_str(&chunk_sha256) != sha256_hex(&data)
        {
            return Err(corrupt());
        }
        Ok(data)
    }

    fn release_lease(&self, lease_id: &str) -> Result<(), StoreError> {
        let cleanup = self.connect()?;
        let outcome = (|| -> rusqlite::Result<()> {
            cleanup.execute_batch("BEGIN IMMEDIATE")?;
            cleanup.execute("DELETE FROM package_read_leases WHERE lease_id = ?", [lease_id])?;
            cleanup.execute_batch("COMMIT")
        })();
        if outcome.is_err() && !cleanup.is_autocommit() {
            let _ = cleanup.execute_batch("ROLLBACK");
        }
        Ok(outcome?)
    }
}

/// `_validate_export_claim`: the claimed temporary proven through a new identity handle.
fn validate_export_claim(backup: &Backup<'_>, claimed: &Claimed, envelope: &Envelope) -> Result<(), ()> {
    let handle = safe_file::open_identity_handle(&claimed.path).map_err(|_| ())?;
    let outcome = backup.validate_claimed(claimed, &handle, envelope).map_err(|_| ());
    safe_file::close_handle(handle);
    outcome
}

/// The archive's SHA-256 and length, read as `iter_bounded_chunks(source,
/// limit=LIMITS["total_uncompressed_bytes"])` reads it.
fn hash_archive(path: &Path) -> Step<(String, i64)> {
    let mut source = File::open(path).map_err(|_| publication_failed())?;
    let bound = limit("total_uncompressed_bytes") as u64;
    let mut digest = sha2::Sha256::new();
    let mut total = 0u64;
    let mut buffer = vec![0u8; STREAM_CHUNK_BYTES];
    while total < bound {
        let want = (bound - total).min(STREAM_CHUNK_BYTES as u64) as usize;
        let read = read_some(&mut source, &mut buffer[..want])?;
        if read == 0 {
            return Ok((hex::encode(digest.finalize()), total as i64));
        }
        digest.update(&buffer[..read]);
        total += read as u64;
    }
    if read_some(&mut source, &mut buffer[..1])? > 0 {
        return Err(ExportFailure::new("portable_export_limit_total_uncompressed_bytes"));
    }
    Ok((hex::encode(digest.finalize()), total as i64))
}

/// One buffered `read(n)`: as many bytes as the file gives, up to `n`.
fn read_some(source: &mut File, buffer: &mut [u8]) -> Step<usize> {
    let mut filled = 0;
    while filled < buffer.len() {
        match source.read(&mut buffer[filled..]) {
            Ok(0) => break,
            Ok(read) => filled += read,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
            Err(_) => return Err(publication_failed()),
        }
    }
    Ok(filled)
}

/// `uuid4().hex`.
fn uuid4_hex() -> Result<String, getrandom::Error> {
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes)?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    Ok(hex::encode(bytes))
}

/// `_new_package_read_spool()`: in memory until [`Spool::rollover`], then a temporary file.
struct Spool {
    memory: Vec<u8>,
    file: Option<File>,
    /// The bytes written.
    length: u64,
}

impl Spool {
    fn new() -> Self {
        Self { memory: Vec::new(), file: None, length: 0 }
    }

    fn write(&mut self, data: &[u8]) -> std::io::Result<()> {
        match &mut self.file {
            Some(file) => file.write_all(data)?,
            None => self.memory.extend_from_slice(data),
        }
        self.length += data.len() as u64;
        Ok(())
    }

    /// `rollover()`: the bytes so far moved into a new temporary file.
    fn rollover(&mut self) -> std::io::Result<()> {
        if self.file.is_some() {
            return Ok(());
        }
        let mut file = temporary_file()?;
        file.write_all(&self.memory)?;
        self.memory = Vec::new();
        self.file = Some(file);
        Ok(())
    }

    fn seek_start(&mut self) -> std::io::Result<()> {
        if let Some(file) = &mut self.file {
            file.seek(SeekFrom::Start(0))?;
        }
        Ok(())
    }

    /// `read(n)`.
    fn read(&mut self, count: usize) -> std::io::Result<Vec<u8>> {
        match &mut self.file {
            Some(file) => {
                let mut buffer = vec![0u8; count];
                let mut filled = 0;
                while filled < count {
                    match file.read(&mut buffer[filled..]) {
                        Ok(0) => break,
                        Ok(read) => filled += read,
                        Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
                        Err(error) => return Err(error),
                    }
                }
                buffer.truncate(filled);
                Ok(buffer)
            }
            None => {
                // Only an empty blob is never rolled over.
                let taken: Vec<u8> = self.memory.drain(..count.min(self.memory.len())).collect();
                Ok(taken)
            }
        }
    }

    /// `close()`: whether the temporary file closed.
    fn close(self) -> bool {
        self.file.is_none_or(safe_file::close_handle)
    }
}

/// `tempfile.TemporaryFile()`: `tmp` and eight characters of `_RandomNameSequence` in the
/// system temporary folder, made exclusively and never left behind.
pub(crate) fn temporary_file() -> std::io::Result<File> {
    const CHARACTERS: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789_";
    let folder = std::env::temp_dir();
    for _ in 0..10_000 {
        let mut random = [0u8; 8];
        getrandom::getrandom(&mut random).map_err(|error| std::io::Error::other(error.to_string()))?;
        let name: String =
            random.iter().map(|byte| CHARACTERS[usize::from(*byte) % CHARACTERS.len()] as char).collect();
        let path: PathBuf = folder.join(format!("tmp{name}"));
        match open_temporary(&path) {
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            other => return other,
        }
    }
    Err(std::io::Error::new(std::io::ErrorKind::AlreadyExists, "No usable temporary file name found"))
}

/// `O_TEMPORARY`: the file is deleted when its handle closes.
#[cfg(windows)]
fn open_temporary(path: &Path) -> std::io::Result<File> {
    use std::os::windows::fs::OpenOptionsExt;
    const FILE_FLAG_DELETE_ON_CLOSE: u32 = 0x0400_0000;
    std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .custom_flags(FILE_FLAG_DELETE_ON_CLOSE)
        .open(path)
}

/// Made with mode 0600, then unlinked at once.
#[cfg(not(windows))]
fn open_temporary(path: &Path) -> std::io::Result<File> {
    use std::os::unix::fs::OpenOptionsExt;
    let file = std::fs::OpenOptions::new().read(true).write(true).create_new(true).mode(0o600).open(path)?;
    std::fs::remove_file(path)?;
    Ok(file)
}
