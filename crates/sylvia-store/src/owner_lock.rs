//! `infrastructure/owner_lock.py`: the cross-process lock held for an owner's lifetime.
//!
//! Python opens `<identity>.owner.lock` for appending, writes `0` into an empty file and takes
//! a non-blocking exclusive lock: `fcntl.flock(LOCK_EX | LOCK_NB)` on Linux and
//! `msvcrt.locking(LK_NBLCK, 1)` (byte 0) on Windows. Rust's `File::try_lock` is
//! `flock(LOCK_EX | LOCK_NB)` on Linux, the same call, and `LockFileEx` over the whole file on
//! Windows, a range that contains Python's byte, so a Python owner and a Rust owner exclude
//! each other on both platforms (`tests/vectors/lock_interop.py` checks it both ways).

use std::fs::{File, OpenOptions, TryLockError};
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use sylvia_base::CodedError;

/// `authority_lock_identity`: shared by authority startup and application-update recovery.
pub fn authority_lock_identity(database_path: &Path) -> PathBuf {
    let mut name = database_path.file_name().unwrap_or_default().to_os_string();
    name.push(".authority-owner");
    database_path.with_file_name(name)
}

/// The message `OwnerConflict` carries; the owner reports it with code `command_rejected`.
pub const OWNER_CONFLICT_MESSAGE: &str = "The Sylvia GM SQLite authority already has a live owner";

/// `DatabaseOwnerLock`: held until [`OwnerLock::close`] or drop.
#[derive(Debug)]
pub struct OwnerLock {
    path: PathBuf,
    file: Option<File>,
}

impl OwnerLock {
    /// Take the lock for `identity` (its file is `<identity>.owner.lock`).
    ///
    /// A held lock is refused with Python's `OwnerConflict`; I/O failures before the lock
    /// attempt surface as the `OSError` Python would raise.
    pub fn acquire(identity: &Path) -> Result<Self, OwnerLockError> {
        let mut name = identity.file_name().unwrap_or_default().to_os_string();
        name.push(".owner.lock");
        let path = identity.with_file_name(name);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(OwnerLockError::Io)?;
        }
        // Python's "a+b": read, append, create.
        let mut file = OpenOptions::new()
            .read(true)
            .append(true)
            .create(true)
            .open(&path)
            .map_err(OwnerLockError::Io)?;
        let end = file.seek(SeekFrom::End(0)).map_err(OwnerLockError::Io)?;
        if end == 0 {
            file.write_all(b"0").map_err(OwnerLockError::Io)?;
            file.flush().map_err(OwnerLockError::Io)?;
        }
        file.seek(SeekFrom::Start(0)).map_err(OwnerLockError::Io)?;
        match file.try_lock() {
            Ok(()) => Ok(Self { path, file: Some(file) }),
            // Python turns every OSError from the lock call into OwnerConflict.
            Err(TryLockError::WouldBlock | TryLockError::Error(_)) => Err(OwnerLockError::Conflict(
                CodedError::rejected(OWNER_CONFLICT_MESSAGE).with_error_type("OwnerConflict"),
            )),
        }
    }

    /// The lock file.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Release the lock and close the file. Idempotent.
    pub fn close(&mut self) {
        if let Some(file) = self.file.take() {
            let _ = file.unlock();
        }
    }
}

impl Drop for OwnerLock {
    fn drop(&mut self) {
        self.close();
    }
}

/// Why the lock was not taken.
#[derive(Debug, thiserror::Error)]
pub enum OwnerLockError {
    /// Another owner holds it (`OwnerConflict`).
    #[error("{0}")]
    Conflict(CodedError),
    /// The lock file could not be created or opened.
    #[error("{0}")]
    Io(std::io::Error),
}

impl From<OwnerLockError> for crate::error::StoreError {
    fn from(error: OwnerLockError) -> Self {
        match error {
            OwnerLockError::Conflict(error) => Self::Coded(error),
            OwnerLockError::Io(error) => Self::Other(error.to_string()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identity_and_file_names_match_python() {
        let identity = authority_lock_identity(Path::new("data/authority.sqlite3"));
        assert_eq!(identity, Path::new("data/authority.sqlite3.authority-owner"));
    }

    #[test]
    fn a_second_owner_is_refused_until_the_first_closes() {
        let folder = std::env::temp_dir().join(format!("sylvia-store-lock-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&folder);
        let identity = authority_lock_identity(&folder.join("authority.sqlite3"));
        let mut first = OwnerLock::acquire(&identity).expect("first owner locks");
        assert_eq!(first.path(), folder.join("authority.sqlite3.authority-owner.owner.lock"));
        match OwnerLock::acquire(&identity) {
            Err(OwnerLockError::Conflict(error)) => {
                assert_eq!(error.code, "command_rejected");
                assert_eq!(error.message, OWNER_CONFLICT_MESSAGE);
                assert_eq!(error.error_type, Some("OwnerConflict"));
            }
            other => panic!("second owner was not refused: {other:?}"),
        }
        first.close();
        let second = OwnerLock::acquire(&identity).expect("lock is free after close");
        let path = second.path().to_path_buf();
        // On Windows the held range cannot be read through another handle (Python's 1-byte
        // lock over the 1-byte file behaves the same), so read it after release.
        drop(second);
        assert_eq!(std::fs::read(path).unwrap(), b"0");
        let _ = std::fs::remove_dir_all(&folder);
    }
}
