//! `application/reusable_library_deletion.py`: safe, retryable removal of one exact
//! reusable-library directory, and the entry checks the reusable libraries share.

use crate::path_guardrail::platform;
use crate::workspace_files::os_error;
use std::path::{Path, PathBuf};
use sylvia_domain::error::{DomainError, DomainResult};

/// `FILE_ATTRIBUTE_REPARSE_POINT`.
#[cfg(windows)]
const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;

/// `os.path.lexists(path)`: the entry itself exists, link or not; any error reads as absent.
pub(crate) fn lexists(path: &Path) -> bool {
    std::fs::symlink_metadata(path).is_ok()
}

/// `Path(path).resolve()` (non-strict), as text.
pub(crate) fn resolve(path: &Path) -> String {
    let text = path.to_string_lossy();
    let absolute = if platform::is_absolute(&text) {
        text.into_owned()
    } else {
        platform::join(&platform::cwd(), &text)
    };
    platform::resolve(&absolute)
}

/// `is_link_or_reparse(path)`: recognize symbolic links and Windows junctions before traversing
/// an entry. `path.lstat()` raises when the entry is missing.
pub fn is_link_or_reparse(path: &Path) -> DomainResult<bool> {
    let metadata = std::fs::symlink_metadata(path).map_err(|error| os_error(&error, path))?;
    Ok(metadata.file_type().is_symlink() || is_reparse_point(&metadata))
}

#[cfg(windows)]
fn is_reparse_point(metadata: &std::fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;
    metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
}

/// `st_file_attributes` exists only on Windows; `getattr(..., 0)` reads 0 elsewhere.
#[cfg(not(windows))]
fn is_reparse_point(_metadata: &std::fs::Metadata) -> bool {
    false
}

/// `validated_library_entry(storage_root, library_name=..., artifact_id=...)`: resolve one entry
/// without following a linked library root or entry. `None` when the entry does not exist.
pub fn validated_library_entry(
    storage_root: &Path,
    library_name: &str,
    artifact_id: &str,
) -> DomainResult<Option<PathBuf>> {
    let resolved_storage = resolve(storage_root);
    let library_root = storage_root.join(library_name);
    let target = library_root.join(artifact_id);
    if !lexists(&target) {
        return Ok(None);
    }
    for path in [&library_root, &target] {
        if is_link_or_reparse(path)? || !path.is_dir() {
            return Err(DomainError::value("Reusable library deletion cannot follow linked directories"));
        }
        if platform::relative_parts(&resolve(path), &resolved_storage).is_none() {
            return Err(DomainError::value("Reusable library deletion leaves application storage"));
        }
    }
    Ok(Some(target))
}

/// `validate_owned_files(target, owned_names)`: reject unexpected files, directories, links and
/// Windows reparse points.
pub fn validate_owned_files(target: &Path, owned_names: &[&str]) -> DomainResult<()> {
    let entries = std::fs::read_dir(target).map_err(|error| os_error(&error, target))?;
    for entry in entries {
        let entry = entry.map_err(|error| os_error(&error, target))?;
        let item = entry.path();
        let name = entry.file_name().to_string_lossy().into_owned();
        if !owned_names.contains(&name.as_str()) || is_link_or_reparse(&item)? || !item.is_file() {
            return Err(DomainError::value("Reusable library entry contains an unsafe path"));
        }
    }
    Ok(())
}

/// `delete_exact_library_entry(storage_root, library_name=..., artifact_id=..., owned_names=...)`:
/// remove the owned files, keeping `record.json` as the retry fence until last. `false` when
/// the entry is already gone.
pub fn delete_exact_library_entry(
    storage_root: &Path,
    library_name: &str,
    artifact_id: &str,
    owned_names: &[&str],
) -> DomainResult<bool> {
    let Some(target) = validated_library_entry(storage_root, library_name, artifact_id)? else {
        return Ok(false);
    };
    validate_owned_files(&target, owned_names)?;
    let mut original_files: Vec<(&str, Vec<u8>)> = Vec::new();
    for name in owned_names {
        let path = target.join(name);
        if lexists(&path) {
            original_files.push((name, std::fs::read(&path).map_err(|error| os_error(&error, &path))?));
        }
    }
    let removal = (|| -> std::io::Result<()> {
        let mut names: Vec<&str> =
            owned_names.iter().copied().filter(|name| *name != "record.json").collect();
        names.sort_unstable();
        for name in names {
            let path = target.join(name);
            if lexists(&path) {
                std::fs::remove_file(&path)?;
            }
        }
        std::fs::remove_file(target.join("record.json"))?;
        std::fs::remove_dir(&target)
    })();
    if removal.is_err() {
        let mut restore_failed = false;
        for (name, value) in &original_files {
            let path = target.join(name);
            if !lexists(&path) && std::fs::write(&path, value).is_err() {
                restore_failed = true;
            }
        }
        let message = if restore_failed {
            "Some saved content could not be removed or restored; the entry is unavailable"
        } else {
            "Some saved content could not be removed; the original entry was restored for retry"
        };
        return Err(DomainError::uncaught("OSError", message));
    }
    Ok(true)
}
