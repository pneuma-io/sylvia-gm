//! `application/desktop_update/payload.py`: the stored artifact is checked again at each
//! trust boundary instead of trusting a journal label.
//!
//! Both functions are ported: `verify_artifact` and `extract_portable`. Extracting an update
//! never writes to the live copy.

use super::manifest::{bounded_json, payload_path, Manifest, PayloadFile};
use super::replacement::portable_marker_valid;
use super::storage::{
    directory_identity, disk_free, ensure_directory, is_update_error, os_error, present, stable_digest,
};
use super::update_error;
use std::collections::{BTreeMap, HashSet};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use sylvia_base::{CodedError, CodedResult, Json};
use sylvia_domain::package_archive::zipfile::{ReadError, ZipArchive};

/// `S_IFMT` and `S_IFREG` of a ZIP member's Unix mode (`external_attr >> 16`).
const S_IFMT: u32 = 0o170000;
const S_IFREG: u32 = 0o100000;

fn prepare_failed() -> CodedError {
    update_error("prepare_failed", "The portable update could not be prepared. The live copy is unchanged.")
}

/// `extract_portable(artifact, manifest, destination)`: the signed archive's members, each
/// matched to the signed inventory, into a new folder beside the update; then the portable
/// marker and both Windows executables are checked, and the artifact once more.
///
/// The archive is read whole into memory for lane 1's ZIP reader; Python streams it from the
/// file. The artifact is at most `ARTIFACT_LIMIT` (512 MiB).
pub fn extract_portable(artifact: &Path, manifest: &Manifest, destination: &Path) -> CodedResult<PathBuf> {
    if manifest.distribution != "portable" {
        return Err(update_error(
            "wrong_distribution",
            "Only a portable archive can prepare a portable copy.",
        ));
    }
    verify_artifact(artifact, manifest)?;
    let parent = destination.parent().unwrap_or(Path::new(""));
    let parent_identity = directory_identity(parent)?;
    if present(destination) {
        return Err(update_error("staging_exists", "A previous prepared update needs reconciliation."));
    }
    let needed: u64 = manifest.files.iter().map(|row| row.size).sum();
    let free = disk_free(parent).map_err(|error| os_error(&error))?;
    if u128::from(free) < u128::from(needed) + 16 * 1024 * 1024 {
        return Err(update_error("disk_full", "There is not enough space to prepare the portable update."));
    }
    // Python's `except (OSError, BadZipFile, RuntimeError, ValueError)`: an `UpdateError`
    // passes through, anything else is reported as a failed preparation.
    match extract(artifact, manifest, destination, parent, parent_identity) {
        Ok(()) => Ok(destination.to_path_buf()),
        Err(error) if is_update_error(&error) => Err(error),
        Err(_) => Err(prepare_failed()),
    }
}

fn extract(
    artifact: &Path,
    manifest: &Manifest,
    destination: &Path,
    parent: &Path,
    parent_identity: super::storage::Identity,
) -> CodedResult<()> {
    let expected: BTreeMap<&str, &PayloadFile> =
        manifest.files.iter().map(|row| (row.path.as_str(), row)).collect();
    let data = std::fs::read(artifact).map_err(|error| os_error(&error))?;
    let archive = ZipArchive::open(&data).map_err(|_| prepare_failed())?;
    let mismatch =
        || update_error("unsafe_payload", "The portable archive does not match its signed inventory.");
    let mut names: HashSet<String> = HashSet::new();
    for entry in archive.infos() {
        let name = payload_path(&Json::String(entry.filename.clone()))?;
        let mode = entry.external_attr >> 16;
        let row = expected.get(name.as_str());
        if names.contains(&name)
            || row.is_none()
            || entry.is_dir()
            || !matches!(mode & S_IFMT, 0 | S_IFREG)
            || entry.flag_bits & 1 != 0
            || !matches!(entry.compress_type, 0 | 8)
            || row.is_some_and(|row| entry.file_size != row.size)
        {
            return Err(mismatch());
        }
        names.insert(name);
    }
    if names.len() != expected.len() || !expected.keys().all(|name| names.contains(*name)) {
        return Err(update_error("unsafe_payload", "The portable archive omits signed application files."));
    }
    if directory_identity(parent)? != parent_identity {
        return Err(update_error("unsafe_staging", "The update staging folder changed."));
    }
    std::fs::create_dir(destination).map_err(|error| os_error(&error))?;
    let destination_identity = directory_identity(destination)?;
    for entry in archive.infos() {
        let row = expected[entry.filename.as_str()];
        let target = destination.join(&row.path);
        let target_parent = target.parent().unwrap_or(Path::new(""));
        ensure_directory(target_parent)?;
        directory_identity(target_parent)?;
        let mut output = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&target)
            .map_err(|error| os_error(&error))?;
        use sha2::Digest;
        let mut digest = sha2::Sha256::new();
        let mut count = 0u64;
        let written = archive.read_chunks(entry, 256 * 1024, |chunk| -> CodedResult<()> {
            count += chunk.len() as u64;
            if count > row.size {
                return Err(update_error("payload_mismatch", "An update file exceeds its signed length."));
            }
            output.write_all(chunk).map_err(|error| os_error(&error))?;
            digest.update(chunk);
            Ok(())
        });
        match written {
            Ok(()) => {}
            Err(ReadError::Caller(error)) => return Err(error),
            Err(ReadError::Zip(_)) => return Err(prepare_failed()),
        }
        output.flush().and_then(|()| output.sync_all()).map_err(|error| os_error(&error))?;
        drop(output);
        if count != row.size || hex::encode(digest.finalize()) != row.sha256 {
            return Err(update_error(
                "payload_mismatch",
                "An update file does not match its signed identity.",
            ));
        }
    }
    if directory_identity(destination)? != destination_identity {
        return Err(update_error("unsafe_staging", "The prepared portable folder changed."));
    }
    let marker = std::fs::read(destination.join("sylvia-portable.json")).map_err(|error| os_error(&error))?;
    if !portable_marker_valid(&bounded_json(&marker, 128)?) {
        return Err(update_error("wrong_distribution", "The portable update marker is incompatible."));
    }
    for name in ["Sylvia GM.exe", "sylvia-gm-owner.exe"] {
        let mut head = Vec::with_capacity(2);
        std::fs::File::open(destination.join(name))
            .and_then(|file| file.take(2).read_to_end(&mut head))
            .map_err(|error| os_error(&error))?;
        if head != b"MZ" {
            return Err(update_error(
                "invalid_executable",
                "The portable update has an invalid Windows executable.",
            ));
        }
    }
    // The artifact is checked again: it must not have changed while it was extracted.
    verify_artifact(artifact, manifest)
}

/// `verify_artifact(path, manifest)`: the file is regular, unaliased, unchanged while read,
/// and has exactly the signed size and SHA-256.
pub fn verify_artifact(path: &Path, manifest: &Manifest) -> CodedResult<()> {
    directory_identity(path.parent().unwrap_or(Path::new("")))?;
    match stable_digest(path, manifest.artifact_size) {
        Ok(Some(digest)) if digest == manifest.artifact_sha256 => Ok(()),
        _ => {
            Err(update_error("artifact_mismatch", "The stored update no longer matches the signed release."))
        }
    }
}
