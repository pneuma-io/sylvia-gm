//! `builder/_workspace_files.py`: reject stale Builder file mutations across chats in the
//! application owner process.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use sylvia_base::hash::sha256_hex;
use sylvia_domain::error::{DomainError, DomainResult};

/// `MISSING_VERSION`.
pub const MISSING_VERSION: &str = "missing";

/// All Builder chats run in one owner process; compare-and-replace is serialized there
/// (`_MUTATION_LOCK`).
static MUTATION_LOCK: Mutex<()> = Mutex::new(());

/// `StaleWorkspaceFileError` (a `ValueError`): the file no longer matches the version the Builder
/// used for its edit.
fn stale() -> DomainError {
    DomainError::Diagnosed {
        error_type: "StaleWorkspaceFileError",
        message: "File changed or has not been read. Call read_file again, reconcile the latest content, then retry \
                  with its version as expected_version. Ask the user if the requested changes conflict; do not \
                  blindly repeat the old replacement."
            .into(),
        diagnostics: Vec::new(),
    }
}

/// `LiteralMatchError(matches)` (a `ValueError`): an exact edit target did not identify one unique
/// piece of text.
fn literal_match(matches: usize) -> DomainError {
    let message = if matches == 0 {
        "old_text was not found. Read the file again and use an exact literal."
    } else {
        "old_text occurs more than once. Include more surrounding text so it uniquely identifies the intended correction."
    };
    DomainError::Diagnosed {
        error_type: "LiteralMatchError",
        message: message.into(),
        diagnostics: Vec::new(),
    }
}

/// An `OSError` Python lets through (the owner's internal error), as its class and `str()` in
/// CPython's `[Errno N] strerror: 'path'` form for the common kinds; other kinds keep the
/// platform's own text.
fn os_error(error: &std::io::Error, path: &Path) -> DomainError {
    let (class, errno, text) = match error.kind() {
        std::io::ErrorKind::NotFound => ("FileNotFoundError", 2, "No such file or directory".to_owned()),
        std::io::ErrorKind::PermissionDenied => ("PermissionError", 13, "Permission denied".to_owned()),
        std::io::ErrorKind::AlreadyExists => ("FileExistsError", 17, "File exists".to_owned()),
        _ => ("OSError", error.raw_os_error().unwrap_or_default(), error.to_string()),
    };
    let name = sylvia_base::pystr::repr(&path.display().to_string());
    DomainError::uncaught(class, format!("[Errno {errno}] {text}: {name}"))
}

/// `content_version(content)`: the exact bytes read or written, line endings included.
pub fn content_version(content: &[u8]) -> String {
    sha256_hex(content)
}

/// `_check_version(path, expected_version)`: require an observed version for existing files and
/// detect competing creations.
fn check_version(path: &Path, expected_version: Option<&str>) -> DomainResult<()> {
    let current = match std::fs::read(path) {
        Ok(data) => content_version(&data),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => MISSING_VERSION.to_owned(),
        Err(error) => return Err(os_error(&error, path)),
    };
    let expected = expected_version.filter(|version| !version.is_empty()).unwrap_or(MISSING_VERSION);
    if current != expected {
        return Err(stale());
    }
    Ok(())
}

/// `tempfile.NamedTemporaryFile(dir=..., prefix=..., delete=False)` then `os.replace`; the
/// temporary file is removed if anything fails.
fn replace_atomically(path: &Path, prefix: &str, data: &[u8]) -> DomainResult<()> {
    let parent = path.parent().unwrap_or_else(|| Path::new("."));
    let mut temporary: Option<PathBuf> = None;
    let result = (|| -> std::io::Result<()> {
        let mut attempt = 0u32;
        let mut handle = loop {
            let name = format!("{prefix}{:08x}{attempt:x}", std::process::id() ^ nanos());
            let candidate = parent.join(name);
            match std::fs::OpenOptions::new().write(true).create_new(true).open(&candidate) {
                Ok(handle) => {
                    temporary = Some(candidate);
                    break handle;
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists && attempt < 100 => {
                    attempt += 1
                }
                Err(error) => return Err(error),
            }
        };
        handle.write_all(data)?;
        drop(handle);
        std::fs::rename(temporary.as_ref().expect("created"), path)?;
        temporary = None;
        Ok(())
    })();
    if let Some(leftover) = temporary {
        let _ = std::fs::remove_file(leftover);
    }
    result.map_err(|error| os_error(&error, path))
}

fn nanos() -> u32 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or_default()
}

fn find(haystack: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    if from > haystack.len() {
        return None;
    }
    haystack[from..].windows(needle.len()).position(|window| window == needle).map(|at| at + from)
}

/// `write_text(path, content, expected_version)`: save atomically only if the current file still
/// matches the Builder's observation. Returns the new version.
pub fn write_text(path: &Path, content: &str, expected_version: Option<&str>) -> DomainResult<String> {
    let data = content.as_bytes();
    let _guard = MUTATION_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    check_version(path, expected_version)?;
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|error| os_error(&error, parent))?;
    }
    replace_atomically(path, ".sylvia-builder-write-", data)?;
    Ok(content_version(data))
}

/// `edit_text(path, old_text, new_text, expected_version)`: replace one unique UTF-8 literal
/// atomically, keeping every other byte. Returns the new version and the new text's byte count.
pub fn edit_text(
    path: &Path,
    old_text: &str,
    new_text: &str,
    expected_version: Option<&str>,
) -> DomainResult<(String, usize)> {
    let (old_data, new_data) = (old_text.as_bytes(), new_text.as_bytes());
    if old_data.is_empty() {
        return Err(DomainError::value("old_text must be non-empty"));
    }
    let _guard = MUTATION_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    check_version(path, expected_version)?;
    let data = std::fs::read(path).map_err(|error| os_error(&error, path))?;
    let Some(start) = find(&data, old_data, 0) else { return Err(literal_match(0)) };
    // `data.find(old_data, start + 1)`: an overlapping second occurrence counts.
    if find(&data, old_data, start + 1).is_some() {
        return Err(literal_match(2));
    }
    let mut edited = Vec::with_capacity(data.len() - old_data.len() + new_data.len());
    edited.extend_from_slice(&data[..start]);
    edited.extend_from_slice(new_data);
    edited.extend_from_slice(&data[start + old_data.len()..]);
    replace_atomically(path, ".sylvia-builder-edit-", &edited)?;
    Ok((content_version(&edited), new_data.len()))
}

/// `delete_file(path, expected_version)`: delete only the file version the Builder read.
pub fn delete_file(path: &Path, expected_version: Option<&str>) -> DomainResult<()> {
    let _guard = MUTATION_LOCK.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
    check_version(path, expected_version)?;
    std::fs::remove_file(path).map_err(|error| os_error(&error, path))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!("sylvia-workspace-files-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        root
    }

    #[test]
    fn writes_edits_and_deletes_only_the_observed_version() {
        let root = scratch("cycle");
        let path = root.join("nested").join("notes.md");
        let class = |error: DomainError| error.python_class();
        assert_eq!(
            class(write_text(&path, "x", Some("0".repeat(64).as_str())).unwrap_err()),
            "StaleWorkspaceFileError"
        );
        let first = write_text(&path, "one\r\nTwo two\n", None).unwrap();
        assert_eq!(first, content_version(b"one\r\nTwo two\n"));
        assert_eq!(class(write_text(&path, "again", None).unwrap_err()), "StaleWorkspaceFileError");
        assert_eq!(
            edit_text(&path, "zzz", "y", Some(&first)).unwrap_err().message(),
            literal_match(0).message()
        );
        assert_eq!(
            edit_text(&path, "wo", "y", Some(&first)).unwrap_err().message(),
            literal_match(2).message()
        );
        assert_eq!(
            edit_text(&path, "", "y", Some(&first)).unwrap_err().message(),
            "old_text must be non-empty"
        );
        let (second, written) = edit_text(&path, "Two", "Three", Some(&first)).unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"one\r\nThree two\n");
        assert_eq!((second.clone(), written), (content_version(b"one\r\nThree two\n"), 5));
        assert_eq!(class(delete_file(&path, Some(&first)).unwrap_err()), "StaleWorkspaceFileError");
        delete_file(&path, Some(&second)).unwrap();
        assert!(!path.exists());
        let leftovers: Vec<_> = std::fs::read_dir(path.parent().unwrap()).unwrap().collect();
        assert!(leftovers.is_empty(), "no temporary file is left behind");
        // Python's `data.find(old, start + 1)` counts an overlapping second occurrence.
        let overlap = root.join("overlap.txt");
        let version = write_text(&overlap, "aaa", None).unwrap();
        assert_eq!(
            edit_text(&overlap, "aa", "b", Some(&version)).unwrap_err().message(),
            literal_match(2).message()
        );
        std::fs::remove_dir_all(&root).unwrap();
    }
}
