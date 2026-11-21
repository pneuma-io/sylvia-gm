//! `application/desktop_update/storage.py`: update-owned staging and small durable receipts,
//! never campaign or credential storage. Folders are refused when aliased, files when they
//! change while read, and receipts are replaced atomically.

use super::manifest::{bounded_json, canonical_bytes, MANIFEST_LIMIT};
use super::update_error;
use crate::util::uuid4_hex;
use std::io::{Read, Write};
use std::path::{Component, Path, PathBuf};
use std::time::SystemTime;
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

/// `UpdatePhase`: every phase a receipt or the controller can be in. `idle` exists only in
/// controller memory, never in a receipt.
pub mod phase {
    pub const CHECKING: &str = "checking";
    pub const AVAILABLE: &str = "available";
    pub const CURRENT: &str = "current";
    pub const DOWNLOADING: &str = "downloading";
    pub const VERIFIED: &str = "verified";
    pub const DEFERRED: &str = "deferred";
    pub const CANCELLED: &str = "cancelled";
    pub const INTERRUPTED: &str = "interrupted";
    pub const ERROR: &str = "error";
    pub const PREPARING: &str = "preparing";
    pub const PREPARED: &str = "prepared";
    pub const CLOSING: &str = "closing";
    pub const APPLYING: &str = "applying";
    pub const AWAITING_RESTART: &str = "awaiting_restart";
    pub const RECOVERY_REQUIRED: &str = "recovery_required";
    pub const ROLLING_BACK: &str = "rolling_back";
    pub const ROLLBACK_READY: &str = "rollback_ready";
    pub const ROLLBACK_AWAITING_RESTART: &str = "rollback_awaiting_restart";
    pub const CONFIRMED: &str = "confirmed";
    pub const ROLLED_BACK: &str = "rolled_back";
    pub const IDLE: &str = "idle";
    /// `UpdateViewPhase.UNAVAILABLE`.
    pub const UNAVAILABLE: &str = "unavailable";
    /// `UpdateViewPhase.RECOVERING`.
    pub const RECOVERING: &str = "recovering";
}

/// `PHASES`: every `UpdatePhase` except `idle`.
pub const PHASES: [&str; 20] = [
    phase::CHECKING,
    phase::AVAILABLE,
    phase::CURRENT,
    phase::DOWNLOADING,
    phase::VERIFIED,
    phase::DEFERRED,
    phase::CANCELLED,
    phase::INTERRUPTED,
    phase::ERROR,
    phase::PREPARING,
    phase::PREPARED,
    phase::CLOSING,
    phase::APPLYING,
    phase::AWAITING_RESTART,
    phase::RECOVERY_REQUIRED,
    phase::ROLLING_BACK,
    phase::ROLLBACK_READY,
    phase::ROLLBACK_AWAITING_RESTART,
    phase::CONFIRMED,
    phase::ROLLED_BACK,
];

/// `EMPTY_TERMINAL_PHASES`: phases whose operation folder may be empty and pruned.
pub const EMPTY_TERMINAL_PHASES: [&str; 4] =
    [phase::CURRENT, phase::ERROR, phase::CANCELLED, phase::INTERRUPTED];

/// `UPDATE_MOVES`: the phases each phase may move to. Python's receipts are not checked
/// against it at run time; its tests check the controller's moves against it, as
/// `controller::tests` does here.
pub const UPDATE_MOVES: [(&str, &[&str]); 21] = {
    use phase::*;
    [
        (
            IDLE,
            &[
                CHECKING,
                AVAILABLE,
                CURRENT,
                VERIFIED,
                DEFERRED,
                CANCELLED,
                INTERRUPTED,
                ERROR,
                RECOVERY_REQUIRED,
                CONFIRMED,
                ROLLED_BACK,
            ],
        ),
        (CHECKING, &[CHECKING, AVAILABLE, CURRENT, CANCELLED, ERROR, INTERRUPTED]),
        (AVAILABLE, &[AVAILABLE, CHECKING, DOWNLOADING, DEFERRED, ERROR, CANCELLED]),
        (CURRENT, &[CURRENT, CHECKING, ERROR, CANCELLED]),
        (DOWNLOADING, &[DOWNLOADING, CHECKING, VERIFIED, CANCELLED, ERROR, INTERRUPTED]),
        (VERIFIED, &[VERIFIED, CHECKING, PREPARING, DEFERRED, ERROR, CANCELLED]),
        (DEFERRED, &[DEFERRED, CHECKING, DOWNLOADING, PREPARING, ERROR]),
        (CANCELLED, &[CANCELLED, CHECKING, ERROR]),
        (INTERRUPTED, &[INTERRUPTED, CHECKING]),
        (ERROR, &[ERROR, CHECKING, PREPARING]),
        (PREPARING, &[PREPARING, PREPARED, VERIFIED, CANCELLED, ERROR, RECOVERY_REQUIRED]),
        (
            PREPARED,
            &[
                PREPARED,
                DEFERRED,
                CLOSING,
                APPLYING,
                VERIFIED,
                ROLLING_BACK,
                CANCELLED,
                ERROR,
                RECOVERY_REQUIRED,
            ],
        ),
        (CLOSING, &[CLOSING, APPLYING, ROLLING_BACK, RECOVERY_REQUIRED]),
        (APPLYING, &[APPLYING, AWAITING_RESTART, ROLLING_BACK, RECOVERY_REQUIRED]),
        (AWAITING_RESTART, &[AWAITING_RESTART, APPLYING, CONFIRMED, ROLLING_BACK, RECOVERY_REQUIRED]),
        (RECOVERY_REQUIRED, &[RECOVERY_REQUIRED, ROLLING_BACK]),
        (ROLLING_BACK, &[ROLLING_BACK, ROLLBACK_READY, RECOVERY_REQUIRED]),
        (ROLLBACK_READY, &[ROLLBACK_READY, ROLLBACK_AWAITING_RESTART, RECOVERY_REQUIRED]),
        (ROLLBACK_AWAITING_RESTART, &[ROLLBACK_AWAITING_RESTART, ROLLED_BACK, RECOVERY_REQUIRED]),
        (CONFIRMED, &[CONFIRMED, CHECKING, ROLLING_BACK]),
        (ROLLED_BACK, &[ROLLED_BACK, CHECKING]),
    ]
};

/// An `OSError` the Python code lets escape (a failed `mkdir`, for example). It is not an
/// `UpdateError`, so the controller reports its generic failure and the owner an internal
/// error, as Python's handlers do.
pub fn os_error(error: &std::io::Error) -> CodedError {
    let error_type = match error.kind() {
        std::io::ErrorKind::NotFound => "FileNotFoundError",
        std::io::ErrorKind::AlreadyExists => "FileExistsError",
        std::io::ErrorKind::PermissionDenied => "PermissionError",
        _ => "OSError",
    };
    CodedError::new("internal_error", error.to_string()).with_error_type(error_type)
}

/// Whether an error stands for Python's `UpdateError` (not an escaped `OSError`).
pub fn is_update_error(error: &CodedError) -> bool {
    error.error_type == Some("UpdateError")
}

/// `path.exists() or path.is_symlink()`.
pub fn present(path: &Path) -> bool {
    std::fs::symlink_metadata(path).is_ok()
}

/// `operation_id(value)` for text.
pub fn operation_id_text(value: &str) -> CodedResult<String> {
    operation_id(&Json::String(value.to_string()))
}

/// `ensure_directory(path)`: check the existing ancestry before creating each missing folder,
/// never through an alias.
pub fn ensure_directory(path: &Path) -> CodedResult<()> {
    if !path.is_absolute() || path.components().any(|part| matches!(part, Component::ParentDir)) {
        return Err(update_error("unsafe_staging", "An absolute update-owned folder is required."));
    }
    let mut missing: Vec<&Path> = Vec::new();
    let mut ancestor = path;
    while !present(ancestor) {
        missing.push(ancestor);
        match ancestor.parent() {
            Some(parent) => ancestor = parent,
            None => break,
        }
    }
    directory_identity(ancestor)?;
    for child in missing.into_iter().rev() {
        let parent_path = child.parent().unwrap_or(Path::new(""));
        let parent = directory_identity(parent_path)?;
        match std::fs::create_dir(child) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists && child.is_dir() => {}
            Err(error) => return Err(os_error(&error)),
        }
        if directory_identity(parent_path)? != parent {
            return Err(update_error("unsafe_staging", "The update staging parent changed."));
        }
        directory_identity(child)?;
    }
    Ok(())
}

/// `UpdateStore`: one active receipt (`current.json`) with separately retained operation
/// folders. Nothing is deleted automatically.
#[derive(Debug, Clone)]
pub struct UpdateStore {
    pub root: PathBuf,
    identity: Identity,
}

/// A loaded `current.json`.
#[derive(Debug, Clone, PartialEq)]
pub struct Receipt {
    pub operation_id: String,
    pub phase: String,
    pub version: Option<String>,
    pub error_code: Option<String>,
}

fn error_code_valid(code: &str) -> bool {
    (1..=64).contains(&code.len()) && code.bytes().all(|byte| byte.is_ascii_lowercase() || byte == b'_')
}

impl UpdateStore {
    /// `UpdateStore(root)`: the parent must be unaliased; the root is created if missing.
    pub fn new(root: &Path) -> CodedResult<Self> {
        directory_identity(root.parent().unwrap_or(Path::new("")))?;
        match std::fs::create_dir(root) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists && root.is_dir() => {}
            Err(error) => return Err(os_error(&error)),
        }
        let identity = directory_identity(root)?;
        Ok(Self { root: root.to_path_buf(), identity })
    }

    fn check(&self) -> CodedResult<()> {
        if directory_identity(&self.root)? != self.identity {
            return Err(update_error("unsafe_staging", "The update staging folder changed."));
        }
        Ok(())
    }

    /// `stage(identity)`: an existing, unaliased operation folder.
    pub fn stage(&self, identity: &str) -> CodedResult<PathBuf> {
        self.check()?;
        let stage = self.root.join(operation_id_text(identity)?);
        directory_identity(&stage)?;
        Ok(stage)
    }

    /// `create()`: a fresh operation folder. Retained interruptions are never deleted to make
    /// room for another download.
    pub fn create(&self) -> CodedResult<(String, PathBuf)> {
        self.check()?;
        let entries = std::fs::read_dir(&self.root).map_err(|error| os_error(&error))?;
        let mut count = 0usize;
        for entry in entries {
            entry.map_err(|error| os_error(&error))?;
            count += 1;
        }
        if count >= 65 {
            return Err(update_error(
                "staging_limit",
                "Retained update files need review before another download.",
            ));
        }
        let identity = uuid4_hex();
        let stage = self.root.join(&identity);
        std::fs::create_dir(&stage).map_err(|error| os_error(&error))?;
        directory_identity(&stage)?;
        Ok((identity, stage))
    }

    /// `save(identity, phase, version, error_code)`.
    pub fn save(
        &self,
        identity: &str,
        phase: &str,
        version: Option<&str>,
        error_code: Option<&str>,
    ) -> CodedResult<()> {
        self.stage(identity)?;
        if !PHASES.contains(&phase) || error_code.is_some_and(|code| !error_code_valid(code)) {
            return Err(update_error("invalid_journal", "The update state cannot be recorded."));
        }
        let mut row = JsonMap::new();
        row.insert("format_version".into(), 1.into());
        row.insert("operation_id".into(), identity.into());
        row.insert("phase".into(), phase.into());
        row.insert("version".into(), version.map_or(Json::Null, Json::from));
        row.insert("error_code".into(), error_code.map_or(Json::Null, Json::from));
        atomic_receipt(&self.root.join("current.json"), &Json::Object(row))
    }

    /// `load()`: the active receipt, or `None` when there is none.
    pub fn load(&self) -> CodedResult<Option<Receipt>> {
        self.check()?;
        let path = self.root.join("current.json");
        if !present(&path) {
            return Ok(None);
        }
        let row = bounded_json(&checked_file(&path, 16 * 1024)?, 16 * 1024)?;
        let invalid =
            || update_error("invalid_journal", "The update receipt is invalid; recovery needs review.");
        let Json::Object(row) = row else {
            return Err(invalid());
        };
        let keys = ["format_version", "operation_id", "phase", "version", "error_code"];
        if row.len() != keys.len() || !keys.iter().all(|key| row.contains_key(*key)) {
            return Err(invalid());
        }
        let format_ok = row["format_version"].is_number() && row["format_version"].as_i64() == Some(1);
        let phase = row["phase"].as_str().filter(|phase| PHASES.contains(phase));
        let version_ok = row["version"].is_null() || row["version"].is_string();
        let error_ok = match &row["error_code"] {
            Json::Null => true,
            Json::String(code) => error_code_valid(code),
            _ => false,
        };
        let (true, Some(phase), true, true) = (format_ok, phase, version_ok, error_ok) else {
            return Err(invalid());
        };
        let identity = operation_id(&row["operation_id"])?;
        let stage = self.root.join(&identity);
        if !EMPTY_TERMINAL_PHASES.contains(&phase) || present(&stage) {
            self.stage(&identity)?;
        }
        Ok(Some(Receipt {
            operation_id: identity,
            phase: phase.to_string(),
            version: row["version"].as_str().map(str::to_string),
            error_code: row["error_code"].as_str().map(str::to_string),
        }))
    }

    /// `prune_empty_terminal(identity)`: remove only this operation's empty terminal folder,
    /// never any retained bytes.
    pub fn prune_empty_terminal(&self, identity: &str) -> CodedResult<()> {
        let Some(row) = self.load()? else {
            return Ok(());
        };
        if row.operation_id != operation_id_text(identity)?
            || !EMPTY_TERMINAL_PHASES.contains(&row.phase.as_str())
        {
            return Ok(());
        }
        let stage = self.root.join(identity);
        if !present(&stage) {
            return Ok(());
        }
        self.stage(identity)?;
        // Not recursive: partial downloads, manifests and recovery files stay.
        let _ = std::fs::remove_dir(&stage);
        Ok(())
    }
}

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
/// `left == right` for two `pathlib` paths: the same components, case-insensitively on Windows.
pub fn same_path(left: &Path, right: &Path) -> bool {
    comparable(left) == comparable(right)
}

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
