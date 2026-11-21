//! The parts of `application/desktop_update/storage.py` the drafts store uses: refusing
//! aliased folders, reading a file that must not change while it is read, and replacing a
//! receipt atomically.

use super::manifest::{canonical_bytes, MANIFEST_LIMIT};
use super::update_error;
use crate::util::uuid4_hex;
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};
use std::time::SystemTime;
use sylvia_base::{CodedResult, Json};

/// `FILE_ATTRIBUTE_REPARSE_POINT`.
#[cfg(windows)]
const REPARSE_POINT: u64 = 0x400;

/// A file's `(st_dev, st_ino)`: the volume and file index on Windows.
pub type Identity = (u64, u64);

#[cfg(windows)]
fn identity_of_path(path: &Path) -> std::io::Result<(Identity, u64)> {
    let handle = winapi_util::Handle::from_path_any(path)?;
    let information = winapi_util::file::information(&handle)?;
    Ok(((information.volume_serial_number(), information.file_index()), information.number_of_links()))
}

#[cfg(not(windows))]
fn identity_of_path(path: &Path) -> std::io::Result<(Identity, u64)> {
    use std::os::unix::fs::MetadataExt;
    let metadata = std::fs::metadata(path)?;
    Ok(((metadata.dev(), metadata.ino()), metadata.nlink()))
}

#[cfg(windows)]
fn identity_of_file(file: &std::fs::File) -> std::io::Result<Identity> {
    let handle = winapi_util::Handle::from_file(file.try_clone()?);
    let information = winapi_util::file::information(&handle)?;
    Ok((information.volume_serial_number(), information.file_index()))
}

#[cfg(not(windows))]
fn identity_of_file(file: &std::fs::File) -> std::io::Result<Identity> {
    use std::os::unix::fs::MetadataExt;
    let metadata = file.metadata()?;
    Ok((metadata.dev(), metadata.ino()))
}

/// `_is_reparse_point(path)`: a symbolic link, or on Windows any reparse point (junctions).
fn is_reparse_point(path: &Path) -> std::io::Result<bool> {
    let metadata = std::fs::symlink_metadata(path)?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if u64::from(metadata.file_attributes()) & REPARSE_POINT != 0 {
            return Ok(true);
        }
    }
    Ok(metadata.file_type().is_symlink())
}

/// A path's text as `pathlib` compares it: components joined, case-folded on Windows.
fn comparable(path: &Path) -> Vec<String> {
    path.components()
        .filter(|component| !matches!(component, Component::CurDir))
        .map(|component| {
            let text = component.as_os_str().to_string_lossy().into_owned();
            if cfg!(windows) {
                text.replace('/', "\\").to_lowercase()
            } else {
                text
            }
        })
        .collect()
}

/// `Path.resolve(strict=True)`, without Windows' `\\?\` prefix, which pathlib does not show.
fn resolve_strict(path: &Path) -> std::io::Result<PathBuf> {
    let resolved = std::fs::canonicalize(path)?;
    let text = resolved.to_string_lossy();
    Ok(if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        PathBuf::from(format!(r"\\{rest}"))
    } else if let Some(rest) = text.strip_prefix(r"\\?\") {
        PathBuf::from(rest)
    } else {
        resolved
    })
}

/// `Path.absolute()`: the current folder joined in front of a relative path, nothing resolved.
fn absolute(path: &Path) -> std::io::Result<PathBuf> {
    if path.is_absolute() {
        Ok(path.to_path_buf())
    } else {
        Ok(std::env::current_dir()?.join(path))
    }
}

fn checked_identity(path: &Path) -> std::io::Result<Option<Identity>> {
    if comparable(&resolve_strict(path)?) != comparable(&absolute(path)?) || !path.is_dir() {
        return Ok(None);
    }
    // `_reject_reparse_chain(path, path)` and the same for every parent: each checks only
    // the folder itself.
    if is_reparse_point(path)? {
        return Ok(None);
    }
    for parent in path.ancestors().skip(1) {
        if parent.as_os_str().is_empty() {
            break;
        }
        if is_reparse_point(parent)? {
            return Ok(None);
        }
    }
    Ok(Some(identity_of_path(path)?.0))
}

/// `directory_identity(path)`: refuse aliases and reparse ancestors before using a folder.
pub fn directory_identity(path: &Path) -> CodedResult<Identity> {
    match checked_identity(path) {
        Ok(Some(identity)) => Ok(identity),
        _ => Err(update_error("unsafe_staging", "The update staging folder is unsafe.")),
    }
}

/// `operation_id(value)`: 32 lowercase hex digits.
pub fn operation_id(value: &Json) -> CodedResult<String> {
    match value {
        Json::String(text)
            if text.len() == 32 && text.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')) =>
        {
            Ok(text.clone())
        }
        _ => Err(update_error("invalid_operation", "The update operation is no longer available.")),
    }
}

struct Stat {
    identity: Identity,
    size: u64,
    modified: SystemTime,
}

fn lstat(path: &Path) -> std::io::Result<(std::fs::Metadata, Stat)> {
    let metadata = std::fs::symlink_metadata(path)?;
    let (identity, _) = identity_of_path(path)?;
    let modified = metadata.modified()?;
    let size = metadata.len();
    Ok((metadata, Stat { identity, size, modified }))
}

fn read_checked(path: &Path, maximum: usize) -> std::io::Result<Option<Vec<u8>>> {
    let (metadata, before) = lstat(path)?;
    let links = identity_of_path(path)?.1;
    let reparse = is_reparse_point(path)?;
    if metadata.file_type().is_symlink()
        || !path.is_file()
        || links != 1
        || reparse
        || !(0 < before.size && before.size <= maximum as u64)
    {
        return Ok(None);
    }
    let mut file = std::fs::File::open(path)?;
    if identity_of_file(&file)? != before.identity {
        return Ok(None);
    }
    let mut raw = Vec::new();
    Read::by_ref(&mut file).take(maximum as u64 + 1).read_to_end(&mut raw)?;
    drop(file);
    let (_, after) = lstat(path)?;
    if raw.len() as u64 != before.size
        || (after.identity, after.size, after.modified) != (before.identity, before.size, before.modified)
    {
        return Ok(None);
    }
    Ok(Some(raw))
}

/// The identity a download stage is checked by (`download_artifact`'s `checked_stage`): the
/// folder resolves to itself and is not itself a reparse point. Unlike
/// [`directory_identity`], Python checks no ancestor and does not require a directory here.
pub fn stage_identity(path: &Path) -> std::io::Result<Option<Identity>> {
    if comparable(&resolve_strict(path)?) != comparable(&absolute(path)?) || is_reparse_point(path)? {
        return Ok(None);
    }
    Ok(Some(identity_of_path(path)?.0))
}

/// The SHA-256 of a regular, unaliased file whose size must be `size`, read in 256 KiB
/// chunks with Python's checks: a single link, no reparse point, the same file opened as
/// was inspected, no growth while it is read, and the same identity, size and modification
/// time afterwards. `None` where any check fails; the digest is the caller's to compare.
pub fn stable_digest(path: &Path, size: u64) -> std::io::Result<Option<String>> {
    use sha2::Digest;
    let (metadata, before) = lstat(path)?;
    let links = identity_of_path(path)?.1;
    if !metadata.file_type().is_file() || links != 1 || is_reparse_point(path)? || before.size != size {
        return Ok(None);
    }
    let mut file = std::fs::File::open(path)?;
    if identity_of_file(&file)? != before.identity {
        return Ok(None);
    }
    let mut digest = sha2::Sha256::new();
    let mut count = 0u64;
    let mut chunk = vec![0u8; 256 * 1024];
    loop {
        let read = file.read(&mut chunk)?;
        if read == 0 {
            break;
        }
        count += read as u64;
        if count > size {
            return Ok(None);
        }
        digest.update(&chunk[..read]);
    }
    drop(file);
    let (_, after) = lstat(path)?;
    if count != size
        || (after.identity, after.size, after.modified) != (before.identity, before.size, before.modified)
    {
        return Ok(None);
    }
    Ok(Some(hex::encode(digest.finalize())))
}

/// `checked_file(path, maximum=)`: the whole file, refused if it is a link, has other names,
/// is empty or too large, or changes while it is read.
pub fn checked_file(path: &Path, maximum: usize) -> CodedResult<Vec<u8>> {
    directory_identity(path.parent().unwrap_or(Path::new("")))?;
    match read_checked(path, maximum) {
        Ok(Some(raw)) => Ok(raw),
        _ => Err(update_error("invalid_staging", "Stored update information could not be verified.")),
    }
}

/// `atomic_receipt(path, value)`: canonical bytes through a fresh temporary file, then one
/// rename. Like Python, a temporary file left by a failed write is not removed.
pub fn atomic_receipt(path: &Path, value: &Json) -> CodedResult<()> {
    let parent = path.parent().unwrap_or(Path::new(""));
    let identity = directory_identity(parent)?;
    let raw = canonical_bytes(value);
    if raw.len() > MANIFEST_LIMIT {
        return Err(update_error("metadata_limit", "Stored update information is too large."));
    }
    let failed = || {
        update_error(
            "journal_failed",
            "The update receipt could not be saved. Recovery information is retained.",
        )
    };
    let temporary = path.with_file_name(format!(".receipt-{}.tmp", uuid4_hex()));
    let written = (|| -> std::io::Result<()> {
        let mut stream = std::fs::OpenOptions::new().write(true).create_new(true).open(&temporary)?;
        stream.write_all(&raw)?;
        stream.flush()?;
        stream.sync_all()
    })();
    written.map_err(|_| failed())?;
    if directory_identity(parent)? != identity || path.is_symlink() {
        return Err(update_error("unsafe_staging", "The update staging folder changed."));
    }
    // Replacing the receipt replaces a directory entry; it never truncates an existing link.
    std::fs::rename(&temporary, path).map_err(|_| failed())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_plain_folder_has_an_identity_and_a_missing_one_does_not() {
        let root = std::env::temp_dir().join(format!("sylvia-owner-storage-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let root = resolve_strict(&root).unwrap();
        assert!(directory_identity(&root).is_ok());
        assert_eq!(directory_identity(&root.join("missing")).unwrap_err().code, "unsafe_staging");
        let receipt = root.join("receipt.json");
        atomic_receipt(&receipt, &json!({"b": 1, "a": "é"})).unwrap();
        assert_eq!(std::fs::read(&receipt).unwrap(), "{\"a\":\"é\",\"b\":1}".as_bytes());
        assert_eq!(checked_file(&receipt, 1024).unwrap(), "{\"a\":\"é\",\"b\":1}".as_bytes());
        assert_eq!(checked_file(&receipt, 4).unwrap_err().code, "invalid_staging");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn operation_ids_are_lowercase_hex() {
        assert!(operation_id(&json!("0123456789abcdef0123456789abcdef")).is_ok());
        assert_eq!(
            operation_id(&json!("0123456789ABCDEF0123456789abcdef")).unwrap_err().code,
            "invalid_operation"
        );
        assert!(operation_id(&json!(7)).is_err());
    }
}
