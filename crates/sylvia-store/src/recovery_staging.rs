//! Staging one portable campaign archive for recovery, from `campaign_archive.py`:
//! `stage_campaign_archive` with `PortableRecoveryFailure`, `StagedCampaignArchive` (`iter_rows`,
//! `iter_blob`, `private_records`), `_recovery_zip_preflight`, `_discover_recovery_manifest`,
//! `_write_recovery_spool` and `_stage_recovery_members`.
//!
//! The source is copied once into an unnamed temporary file and hashed as it is copied, so every
//! later read sees the same sealed bytes. The ZIP directory is bounded before the archive is
//! opened, the manifest is discovered, the archive is validated
//! ([`crate::archive_validation::validate_campaign_archive_file`]) and its authority lines and
//! blobs are spooled into two more temporary files, indexed by position. Every temporary file is
//! deleted when the staged archive is dropped, as Python closes them when its context ends.
//!
//! A failure Python does not catch (a `BadZipFile` from reading a member, an `OSError` from a
//! spool) keeps its class: it is an `internal_error` with that class, which the owner reports as
//! its internal-error reply. Python's cleanup diagnostics (`portable_recovery_cleanup_incomplete`)
//! are kept on its exception object only; closing a temporary file here cannot fail.

use crate::campaign_archive::{
    failure, limit, PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION, PRIVATE_FORMAT_VERSION, PRIVATE_MEMBER_NAME,
};
use crate::error::{StoreError, StoreResult};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::path::Path;
use sylvia_base::pyjson::{self, HookPolicy};
use sylvia_base::{CodedError, Json, JsonMap};
use sylvia_domain::package_archive::zip_bounds::preflight_zip_directory_source;
use sylvia_domain::package_archive::zipfile::{file_source, ReadError, ZipArchive, ZipError, ZipInfo};
use sylvia_domain::pyops::py_eq;

/// `STREAM_CHUNK_BYTES`.
pub const STREAM_CHUNK_BYTES: usize = 65_536;
/// `RECOVERY_MAX_ARCHIVE_BYTES`.
pub const RECOVERY_MAX_ARCHIVE_BYTES: u64 = 8_608_809_058;
/// `RECOVERY_MAX_CENTRAL_DIRECTORY_BYTES`.
pub const RECOVERY_MAX_CENTRAL_DIRECTORY_BYTES: u64 = 9_437_184;
/// `RECOVERY_MAX_MEMBER_NAME_BYTES`.
pub const RECOVERY_MAX_MEMBER_NAME_BYTES: usize = 70;
/// The detail Python adds to an archive's own refusal.
const INVALID_DETAIL: &str = "Campaign file is invalid or unsupported. Try another export.";

/// `PortableRecoveryFailure(code, detail=...)`: a path-free `CommandRejected` with its code.
pub fn recovery_failure(code: &str, detail: Option<&str>) -> StoreError {
    let mut message = format!("Portable campaign recovery failed ({code})");
    if let Some(detail) = detail {
        message.push_str(": ");
        message.push_str(detail);
    }
    StoreError::Coded(CodedError::new(code, message).with_error_type("PortableRecoveryFailure"))
}

/// An exception Python does not catch, with its class: the owner's internal-error reply.
pub fn uncaught(class: &'static str, message: impl Into<String>) -> StoreError {
    StoreError::Coded(CodedError::new("internal_error", message).with_error_type(class))
}

fn zip_uncaught(error: &ZipError) -> StoreError {
    uncaught(error.python_class(), error.message())
}

/// `_decode_json(data, code)`.
fn decode_json(data: &[u8], code: &str) -> StoreResult<Json> {
    if data.starts_with(b"\xef\xbb\xbf") {
        return Err(failure(code));
    }
    let Ok(text) = std::str::from_utf8(data) else { return Err(failure(code)) };
    match pyjson::loads_hooked(text, HookPolicy { refuse_duplicates: true, refuse_constants: true }) {
        Ok(read) if !read.non_finite => Ok(read.value),
        _ => Err(failure(code)),
    }
}

/// `_read_member(archive, info)`, through `iter_bounded_chunks(member, limit=info.file_size)`.
/// A failed ZIP read escapes with its class.
fn read_member(archive: &ZipArchive<'_>, info: &ZipInfo) -> StoreResult<Vec<u8>> {
    let mut buffer = Vec::new();
    let outcome = archive.read_chunks(info, STREAM_CHUNK_BYTES, |chunk| {
        let room = (info.file_size - (buffer.len() as u64).min(info.file_size)) as usize;
        let taken = chunk.len().min(room);
        buffer.extend_from_slice(&chunk[..taken]);
        if chunk.len() > taken {
            return Err(failure("portable_export_limit_total_uncompressed_bytes"));
        }
        Ok(())
    });
    match outcome {
        Ok(()) => {}
        Err(ReadError::Caller(error)) => return Err(error),
        Err(ReadError::Zip(error)) => return Err(zip_uncaught(&error)),
    }
    if buffer.len() as u64 != info.file_size {
        return Err(failure("portable_export_invalid_member_size"));
    }
    Ok(buffer)
}

/// `fp.read(count)` on a regular file: `count` bytes, fewer only at the end.
fn read_full(file: &mut impl Read, count: usize) -> std::io::Result<Vec<u8>> {
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

/// `_write_recovery_spool(handle, data)`.
fn write_spool(handle: &mut File, data: &[u8]) -> StoreResult<()> {
    handle.write_all(data).map_err(|_| recovery_failure("portable_recovery_spool_write_failed", None))
}

fn spool() -> StoreResult<File> {
    crate::export_writer::temporary_file()
        .map_err(|_| recovery_failure("portable_recovery_spool_create_failed", None))
}

/// `StagedCampaignArchive`: the sealed, validated archive bytes and their disk indexes.
#[derive(Debug)]
pub struct StagedCampaignArchive {
    pub campaign_id: String,
    pub captured_revision: i64,
    pub archive_sha256: String,
    pub byte_count: i64,
    pub manifest: JsonMap,
    raw: File,
    authority_spool: File,
    blob_spool: File,
    /// `_table_ranges`, in the order the tables first appear: offset, bytes, rows.
    table_ranges: Vec<(String, (u64, u64, u64))>,
    /// `_blob_ranges`, in member order: offset, bytes.
    blob_ranges: Vec<(String, (u64, u64))>,
}

impl StagedCampaignArchive {
    /// `iter_rows(table)`: each validated row of `table`, in archive order, given to `each`; an
    /// archive made before effort preferences gains the two absent session preferences.
    pub fn for_each_row(
        &self,
        table: &str,
        mut each: impl FnMut(JsonMap) -> StoreResult<()>,
    ) -> StoreResult<()> {
        if crate::campaign_archive::table(table).is_none() {
            return Err(crate::error::value_error("Recovery requested an unknown frozen table"));
        }
        let (offset, byte_count, expected_count) =
            self.table_ranges.iter().find(|(name, _)| name == table).map_or((0, 0, 0), |(_, range)| *range);
        let read_failed = || recovery_failure("portable_recovery_spool_read_failed", None);
        let mut file = &self.authority_spool;
        file.seek(SeekFrom::Start(offset)).map_err(|_| read_failed())?;
        let mut reader = BufReader::new(file);
        let row_limit = limit("single_authority_row_bytes") as u64;
        let pre_effort = self
            .manifest
            .get("source_authority_schema_version")
            .and_then(Json::as_i64)
            .is_some_and(|version| version <= PRE_EFFORT_SOURCE_AUTHORITY_SCHEMA_VERSION);
        let mut remaining = byte_count;
        let mut seen = 0u64;
        while remaining > 0 {
            let mut line = Vec::new();
            (&mut reader)
                .take(remaining.min(row_limit))
                .read_until(b'\n', &mut line)
                .map_err(|_| read_failed())?;
            if line.is_empty() || line.len() as u64 > remaining || !line.ends_with(b"\n") {
                return Err(recovery_failure("portable_export_invalid_authority_framing", None));
            }
            remaining -= line.len() as u64;
            let item = decode_json(&line[..line.len() - 1], "portable_export_invalid_authority_framing")?;
            let schema = || recovery_failure("portable_export_invalid_authority_schema", None);
            let Json::Object(mut item) = item else { return Err(schema()) };
            if !item.get("table").is_some_and(|name| py_eq(name, &Json::from(table))) {
                return Err(schema());
            }
            let Some(Json::Object(mut row)) = item.remove("row") else { return Err(schema()) };
            if table == "agent_sessions" && pre_effort {
                row.insert("reasoning_effort".into(), Json::Null);
                row.insert("reasoning_effort_once".into(), Json::Null);
            }
            seen += 1;
            each(row)?;
        }
        if seen != expected_count {
            return Err(recovery_failure("portable_export_invalid_authority_closure", None));
        }
        Ok(())
    }

    /// `iter_blob(blob_sha256)`: one validated blob in `STREAM_CHUNK_BYTES` chunks, given to `each`.
    pub fn for_each_blob_chunk(
        &self,
        blob_sha256: &str,
        mut each: impl FnMut(Vec<u8>) -> StoreResult<()>,
    ) -> StoreResult<()> {
        let Some((_, (offset, byte_count))) = self.blob_ranges.iter().find(|(sha, _)| sha == blob_sha256)
        else {
            return Err(recovery_failure("portable_export_invalid_blob_closure", None));
        };
        let read_failed = || recovery_failure("portable_recovery_spool_read_failed", None);
        let mut file = &self.blob_spool;
        file.seek(SeekFrom::Start(*offset)).map_err(|_| read_failed())?;
        let mut remaining = *byte_count;
        while remaining > 0 {
            let chunk = read_full(&mut file, remaining.min(STREAM_CHUNK_BYTES as u64) as usize)
                .map_err(|_| read_failed())?;
            if chunk.is_empty() {
                return Err(recovery_failure("portable_export_invalid_blob_closure", None));
            }
            remaining -= chunk.len() as u64;
            each(chunk)?;
        }
        Ok(())
    }

    /// `private_records()`: the sealed private member's rows, read only for recovery; none
    /// unless the archive's format carries it.
    pub fn private_records(&self) -> StoreResult<Vec<JsonMap>> {
        if !self
            .manifest
            .get("format_version")
            .is_some_and(|version| py_eq(version, &Json::from(PRIVATE_FORMAT_VERSION)))
        {
            return Ok(Vec::new());
        }
        let archive = ZipArchive::open_file(&self.raw).map_err(|error| zip_uncaught(&error))?;
        let Some(info) = archive.infos().iter().rev().find(|info| info.filename == PRIVATE_MEMBER_NAME)
        else {
            return Err(uncaught(
                "KeyError",
                format!("\"There is no item named '{PRIVATE_MEMBER_NAME}' in the archive\""),
            ));
        };
        if info.file_size > crate::private_records::MAX_BYTES as u64 {
            return Err(recovery_failure("portable_export_invalid_private_records", None));
        }
        let data = read_member(&archive, info)?;
        let mut session_ids = HashSet::new();
        self.for_each_row("agent_sessions", |row| {
            let id = row.get("session_id").ok_or_else(|| uncaught("KeyError", "'session_id'"))?;
            session_ids.insert(crate::campaigns::json_str(id));
            Ok(())
        })?;
        crate::private_records::decode(&data, &session_ids)
    }

    /// `blob_sha256s`.
    pub fn blob_sha256s(&self) -> impl Iterator<Item = &str> {
        self.blob_ranges.iter().map(|(sha, _)| sha.as_str())
    }
}

/// `_recovery_zip_preflight(raw, byte_count)`: bound the central directory before opening it.
fn recovery_zip_preflight(raw: &File, byte_count: u64) -> StoreResult<()> {
    let metadata = || recovery_failure("portable_export_invalid_zip_metadata", None);
    let read_at = |offset: u64, count: usize| -> StoreResult<Vec<u8>> {
        let mut file = raw;
        file.seek(SeekFrom::Start(offset)).map_err(|error| uncaught("OSError", error.to_string()))?;
        read_full(&mut file, count).map_err(|error| uncaught("OSError", error.to_string()))
    };
    let tail_bytes = byte_count.min(65_557 + 56 + 20);
    let tail = read_at(byte_count - tail_bytes, tail_bytes as usize)?;
    let Some(index) = tail.windows(4).rposition(|window| window == b"PK\x05\x06") else {
        return Err(metadata());
    };
    if index + 22 != tail.len() {
        return Err(metadata());
    }
    let u16_at = |bytes: &[u8], at: usize| u64::from(u16::from_le_bytes([bytes[at], bytes[at + 1]]));
    let u32_at =
        |bytes: &[u8], at: usize| u64::from(u32::from_le_bytes(bytes[at..at + 4].try_into().unwrap()));
    let u64_at = |bytes: &[u8], at: usize| u64::from_le_bytes(bytes[at..at + 8].try_into().unwrap());
    let eocd_offset = u128::from(byte_count - tail_bytes) + index as u128;
    let mut central_end = eocd_offset;
    let (disk, central_disk, mut on_disk, mut entries) = (
        u16_at(&tail, index + 4),
        u16_at(&tail, index + 6),
        u16_at(&tail, index + 8),
        u16_at(&tail, index + 10),
    );
    let (mut central_bytes, mut central_offset) = (u32_at(&tail, index + 12), u32_at(&tail, index + 16));
    let comment = u16_at(&tail, index + 20);
    if disk != 0 || central_disk != 0 || on_disk != entries || comment != 0 {
        return Err(metadata());
    }
    let has_locator = index >= 20 && &tail[index - 20..index - 16] == b"PK\x06\x07";
    if has_locator
        || entries == 0xFFFF
        || on_disk == 0xFFFF
        || central_bytes == 0xFFFF_FFFF
        || central_offset == 0xFFFF_FFFF
    {
        if index < 20 {
            return Err(metadata());
        }
        let locator = &tail[index - 20..index];
        let (zip64_disk, zip64_offset, disk_count) =
            (u32_at(locator, 4), u64_at(locator, 8), u32_at(locator, 16));
        if &locator[..4] != b"PK\x06\x07" || zip64_disk != 0 || disk_count != 1 {
            return Err(metadata());
        }
        let record = read_at(zip64_offset, 56)?;
        if record.len() != 56 {
            return Err(metadata());
        }
        let record_size = u64_at(&record, 4);
        let (disk64, central_disk64) = (u32_at(&record, 16), u32_at(&record, 20));
        on_disk = u64_at(&record, 24);
        entries = u64_at(&record, 32);
        central_bytes = u64_at(&record, 40);
        central_offset = u64_at(&record, 48);
        if &record[..4] != b"PK\x06\x06"
            || record_size < 44
            || disk64 != 0
            || central_disk64 != 0
            || on_disk != entries
            || u128::from(zip64_offset) + 12 + u128::from(record_size) + 20 != eocd_offset
        {
            return Err(metadata());
        }
        central_end = u128::from(zip64_offset);
    }
    if entries > limit("member_count") as u64 {
        return Err(recovery_failure("portable_export_limit_member_count", None));
    }
    if central_bytes > RECOVERY_MAX_CENTRAL_DIRECTORY_BYTES {
        return Err(recovery_failure("portable_recovery_limit_central_directory_bytes", None));
    }
    if u128::from(central_offset) + u128::from(central_bytes) != central_end {
        return Err(metadata());
    }
    let source = file_source(raw).map_err(|error| zip_uncaught(&error))?;
    preflight_zip_directory_source(
        source.as_ref(),
        limit("member_count") as u64,
        RECOVERY_MAX_CENTRAL_DIRECTORY_BYTES,
    )
    .map_err(|error| match error.rejection() {
        // Its `ValueError`s; a failed read is the source's uncaught `OSError`.
        Some(_) => metadata(),
        None => uncaught("OSError", error.message().to_owned()),
    })
}

/// `_discover_recovery_manifest(raw)`: the manifest's campaign and captured revision.
fn discover_recovery_manifest(raw: &File) -> StoreResult<(String, i64)> {
    let Ok(archive) = ZipArchive::open_file(raw) else {
        return Err(recovery_failure("portable_export_invalid_zip", None));
    };
    let infos = archive.infos();
    if infos.is_empty() || infos.len() as i64 > limit("member_count") {
        return Err(recovery_failure("portable_export_invalid_zip", None));
    }
    if infos.iter().any(|info| info.filename.len() > RECOVERY_MAX_MEMBER_NAME_BYTES) {
        return Err(recovery_failure("portable_export_invalid_member_path", None));
    }
    let last = &infos[infos.len() - 1];
    if last.filename != "manifest.json" || last.file_size as i64 > limit("manifest_bytes") {
        return Err(recovery_failure("portable_export_invalid_manifest", None));
    }
    for info in infos {
        if !crate::archive_validation::zip_metadata_is_exact(info) {
            return Err(failure("portable_export_invalid_zip_metadata"));
        }
    }
    let encoded = read_member(&archive, last)?;
    let invalid = || recovery_failure("portable_export_invalid_manifest", None);
    let Json::Object(manifest) = decode_json(&encoded, "portable_export_invalid_manifest")? else {
        return Err(invalid());
    };
    let campaign_id = match manifest.get("campaign_id") {
        Some(Json::String(id)) if !id.is_empty() => id.clone(),
        _ => return Err(invalid()),
    };
    // An integer past 64 bits is refused here (differences row of the archive validation).
    let captured_revision = match manifest.get("captured_revision") {
        Some(Json::Number(number)) => {
            number.as_i64().filter(|revision| *revision >= 0).ok_or_else(invalid)?
        }
        _ => return Err(invalid()),
    };
    Ok((campaign_id, captured_revision))
}

/// `_stage_recovery_members(raw, authority_spool, blob_spool)`: the authority lines and blobs
/// spooled, with each table's and blob's range.
#[allow(clippy::type_complexity)]
fn stage_recovery_members(
    raw: &File,
    authority_spool: &mut File,
    blob_spool: &mut File,
) -> StoreResult<(Vec<(String, (u64, u64, u64))>, Vec<(String, (u64, u64))>)> {
    let archive = ZipArchive::open_file(raw).map_err(|error| zip_uncaught(&error))?;
    let infos = archive.infos();
    let Some(authority) = infos.first() else {
        return Err(uncaught("IndexError", "list index out of range"));
    };
    let mut table_ranges: Vec<(String, (u64, u64, u64))> = Vec::new();
    let mut position = 0u64;
    let mut pending = Vec::new();
    let mut take_line = |line: &[u8], table_ranges: &mut Vec<(String, (u64, u64, u64))>| -> StoreResult<()> {
        if !line.ends_with(b"\n") {
            return Err(recovery_failure("portable_export_invalid_authority_framing", None));
        }
        let item = decode_json(&line[..line.len() - 1], "portable_export_invalid_authority_framing")?;
        let table = match &item {
            Json::Object(item) => match item.get("table") {
                Some(Json::String(table)) => table.clone(),
                _ => return Err(recovery_failure("portable_export_invalid_authority_schema", None)),
            },
            _ => return Err(recovery_failure("portable_export_invalid_authority_schema", None)),
        };
        let start = position;
        write_spool(authority_spool, line)?;
        position += line.len() as u64;
        match table_ranges.iter_mut().find(|(name, _)| *name == table) {
            None => table_ranges.push((table, (start, line.len() as u64, 1))),
            Some((_, (offset, length, count))) => {
                if start != *offset + *length {
                    return Err(recovery_failure("portable_export_invalid_authority_order", None));
                }
                *length += line.len() as u64;
                *count += 1;
            }
        }
        Ok(())
    };
    // `for raw_line in authority`: lines end at each newline; the last may have none.
    let read = archive.read_chunks(authority, STREAM_CHUNK_BYTES, |chunk| {
        let mut rest = chunk;
        while let Some(at) = rest.iter().position(|byte| *byte == b'\n') {
            pending.extend_from_slice(&rest[..=at]);
            take_line(&pending, &mut table_ranges)?;
            pending.clear();
            rest = &rest[at + 1..];
        }
        pending.extend_from_slice(rest);
        Ok(())
    });
    match read {
        Ok(()) => {}
        Err(ReadError::Caller(error)) => return Err(error),
        Err(ReadError::Zip(error)) => return Err(zip_uncaught(&error)),
    }
    if !pending.is_empty() {
        take_line(&pending, &mut table_ranges)?;
    }
    let mut blob_ranges = Vec::new();
    let mut blob_position = 0u64;
    for info in infos.get(1..infos.len().saturating_sub(1)).unwrap_or(&[]) {
        if info.filename == PRIVATE_MEMBER_NAME {
            continue;
        }
        let blob_sha256 = info.filename.strip_prefix("blobs/").unwrap_or(&info.filename).to_owned();
        let offset = blob_position;
        let read = archive.read_chunks(info, STREAM_CHUNK_BYTES, |chunk| {
            write_spool(blob_spool, chunk)?;
            blob_position += chunk.len() as u64;
            Ok(())
        });
        match read {
            Ok(()) => {}
            Err(ReadError::Caller(error)) => return Err(error),
            Err(ReadError::Zip(error)) => return Err(zip_uncaught(&error)),
        }
        // A blob name seen twice keeps its first place and its last range, as a dict does.
        let range = (offset, blob_position - offset);
        match blob_ranges.iter_mut().find(|(sha, _)| *sha == blob_sha256) {
            Some((_, existing)) => *existing = range,
            None => blob_ranges.push((blob_sha256, range)),
        }
    }
    for spool in [&mut *authority_spool, &mut *blob_spool] {
        spool.flush().map_err(|error| uncaught("OSError", error.to_string()))?;
    }
    Ok((table_ranges, blob_ranges))
}

/// `stage_campaign_archive(path)`: open the source once, seal it, validate it and spool it.
pub fn stage_campaign_archive(path: &Path) -> StoreResult<StagedCampaignArchive> {
    // Python's `open(path, "rb")` refuses a folder on every platform.
    let source = File::open(path)
        .ok()
        .filter(|file| file.metadata().map_or(true, |metadata| !metadata.is_dir()))
        .ok_or_else(|| recovery_failure("portable_recovery_source_open_failed", None))?;
    let metadata =
        source.metadata().map_err(|_| recovery_failure("portable_recovery_source_stat_failed", None))?;
    if !metadata.is_file() || metadata.len() > RECOVERY_MAX_ARCHIVE_BYTES {
        return Err(recovery_failure("portable_recovery_limit_archive_bytes", None));
    }
    let mut raw = spool()?;
    let mut digest = Sha256::new();
    let mut byte_count = 0u64;
    let mut reader = &source;
    while byte_count < RECOVERY_MAX_ARCHIVE_BYTES {
        let want = (STREAM_CHUNK_BYTES as u64).min(RECOVERY_MAX_ARCHIVE_BYTES - byte_count) as usize;
        let chunk = read_full(&mut reader, want)
            .map_err(|_| recovery_failure("portable_recovery_source_read_failed", None))?;
        if chunk.is_empty() {
            break;
        }
        byte_count += chunk.len() as u64;
        digest.update(&chunk);
        write_spool(&mut raw, &chunk)?;
    }
    let overflow = read_full(&mut reader, 1)
        .map_err(|_| recovery_failure("portable_recovery_source_read_failed", None))?;
    if !overflow.is_empty() {
        return Err(recovery_failure("portable_recovery_limit_archive_bytes", None));
    }
    raw.flush().map_err(|_| recovery_failure("portable_recovery_spool_write_failed", None))?;
    drop(source);
    // The archive's own refusals carry the detail; a recovery limit passes through as raised.
    let with_detail = |error: StoreError| match error {
        StoreError::Coded(coded)
            if coded.code.starts_with("portable_export_") && coded.code != "internal_error" =>
        {
            recovery_failure(&coded.code, Some(INVALID_DETAIL))
        }
        other => other,
    };
    recovery_zip_preflight(&raw, byte_count).map_err(with_detail)?;
    let (campaign_id, captured_revision) = discover_recovery_manifest(&raw).map_err(with_detail)?;
    let manifest =
        crate::archive_validation::validate_campaign_archive_file(&raw, &campaign_id).map_err(|error| {
            match error.rejection() {
                Some(coded) => recovery_failure(&coded.code, Some(INVALID_DETAIL)),
                None => error,
            }
        })?;
    let mut authority_spool = spool()?;
    let mut blob_spool = spool()?;
    let (table_ranges, blob_ranges) = stage_recovery_members(&raw, &mut authority_spool, &mut blob_spool)?;
    Ok(StagedCampaignArchive {
        campaign_id,
        captured_revision,
        archive_sha256: hex::encode(digest.finalize()),
        byte_count: byte_count as i64,
        manifest,
        raw,
        authority_spool,
        blob_spool,
        table_ranges,
        blob_ranges,
    })
}

/// [`stage_campaign_archive`] on tokio's blocking pool, so copying and validating a large source
/// holds neither the store thread nor a runtime worker.
pub async fn stage_campaign_archive_blocking(path: std::path::PathBuf) -> StoreResult<StagedCampaignArchive> {
    tokio::task::spawn_blocking(move || stage_campaign_archive(&path))
        .await
        .map_err(|error| StoreError::Other(error.to_string()))?
}
