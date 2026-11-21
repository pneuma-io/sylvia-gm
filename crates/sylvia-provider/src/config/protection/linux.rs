//! The Linux key file (`_key_file_seal` and its helpers): AES-256-GCM under a 32-byte per-user
//! key in `<state>/sylvia-gm/credential-key`, where state is an absolute `XDG_STATE_HOME` or
//! `~/.local/state`.
//!
//! The folder is opened without following a link and must belong to the user with no group or
//! other write permission; the key must be a private regular file with one link and exactly 32
//! bytes. A new key is published by hard link, so an existing key is never replaced, and a
//! folder lock keeps readers from seeing it while its temporary name still exists.

use super::{OVERSIZE_MESSAGE, SECRET_BYTE_LIMIT, UNAVAILABLE_MESSAGE};
use crate::config::ConfigFailure;
use aes_gcm::aead::{Aead, KeyInit, Payload};
use aes_gcm::{Aes256Gcm, Nonce};
use rustix::fd::{AsFd, OwnedFd};
use rustix::fs::{self, AtFlags, FileType, FlockOperation, Mode, OFlags};
use rustix::io::Errno;
use std::path::{Path, PathBuf};

const KEY_FILE_NAME: &str = "credential-key";
const KEY_BYTES: usize = 32;
const NONCE_BYTES: usize = 12;
const TAG_BYTES: usize = 16;
const ASSOCIATED_DATA: &[u8] = b"sylvia-gm:linux-user-key-file:v1";
const PROTECT_FAILED: &str = "Saved credential protection failed";

/// Why a key-file step failed; never shown (Python's message replaces it).
#[derive(Debug)]
enum Failure {
    Missing,
    Other,
}

impl From<Errno> for Failure {
    fn from(errno: Errno) -> Self {
        if errno == Errno::NOENT {
            Failure::Missing
        } else {
            Failure::Other
        }
    }
}

/// The key folder of one user.
#[derive(Debug, Clone)]
pub struct KeyFile {
    /// `<state>/sylvia-gm`, or `None` when it is not absolute.
    folder: Option<PathBuf>,
}

impl KeyFile {
    /// `_key_folder()` from the process environment, read now as Python reads it per call.
    pub fn current() -> Self {
        let configured = std::env::var_os("XDG_STATE_HOME").unwrap_or_default();
        let state = if Path::new(&configured).is_absolute() {
            PathBuf::from(configured)
        } else {
            home().join(".local").join("state")
        };
        Self::in_state_folder(&state)
    }

    /// The key folder under a given state folder (tests pass a temporary folder).
    pub fn in_state_folder(state: &Path) -> Self {
        let folder = state.join("sylvia-gm");
        Self { folder: folder.is_absolute().then_some(folder) }
    }

    /// `_key_file_seal(value, decrypt=...)`.
    pub fn seal(&self, value: &[u8], decrypt: bool) -> Result<Vec<u8>, ConfigFailure> {
        let result = if decrypt { self.open_sealed(value) } else { self.seal_new(value) };
        let result = result
            .map_err(|_| ConfigFailure::config(if decrypt { UNAVAILABLE_MESSAGE } else { PROTECT_FAILED }))?;
        if result.len() > SECRET_BYTE_LIMIT {
            return Err(ConfigFailure::config(OVERSIZE_MESSAGE));
        }
        Ok(result)
    }

    fn open_sealed(&self, value: &[u8]) -> Result<Vec<u8>, Failure> {
        if !(NONCE_BYTES + TAG_BYTES <= value.len() && value.len() <= SECRET_BYTE_LIMIT) {
            return Err(Failure::Other);
        }
        let key = self.user_key(false)?;
        let cipher = Aes256Gcm::new_from_slice(&key).map_err(|_| Failure::Other)?;
        let nonce = Nonce::from_slice(&value[..NONCE_BYTES]);
        cipher
            .decrypt(nonce, Payload { msg: &value[NONCE_BYTES..], aad: ASSOCIATED_DATA })
            .map_err(|_| Failure::Other)
    }

    fn seal_new(&self, value: &[u8]) -> Result<Vec<u8>, Failure> {
        let key = self.user_key(true)?;
        let mut nonce = [0u8; NONCE_BYTES];
        getrandom::getrandom(&mut nonce).map_err(|_| Failure::Other)?;
        seal_with(&key, &nonce, value)
    }

    /// `_user_key(create=...)`: read the key under the folder lock; only protection may create
    /// a missing one.
    fn user_key(&self, create: bool) -> Result<Vec<u8>, Failure> {
        let folder = self.open_key_folder(create)?;
        fs::flock(&folder, if create { FlockOperation::LockExclusive } else { FlockOperation::LockShared })?;
        if create {
            remove_interrupted_temporaries(&folder);
        }
        match read_key(&folder) {
            Ok(key) => return Ok(key),
            Err(Failure::Missing) if create => {}
            Err(failure) => return Err(failure),
        }
        create_key(&folder)?;
        read_key(&folder)
    }

    /// `_open_key_folder(create=...)`.
    fn open_key_folder(&self, create: bool) -> Result<OwnedFd, Failure> {
        let folder = self.folder.as_deref().ok_or(Failure::Other)?;
        if create {
            create_private_folders(folder)?;
        }
        let descriptor = fs::open(
            folder,
            OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
            Mode::empty(),
        )?;
        let status = fs::fstat(&descriptor)?;
        if !FileType::from_raw_mode(status.st_mode).is_dir()
            || status.st_uid != rustix::process::geteuid().as_raw()
            || status.st_mode & 0o022 != 0
        {
            return Err(Failure::Other);
        }
        Ok(descriptor)
    }
}

/// AES-256-GCM: `nonce + AESGCM(key).encrypt(nonce, value, associated data)`.
fn seal_with(key: &[u8], nonce: &[u8; NONCE_BYTES], value: &[u8]) -> Result<Vec<u8>, Failure> {
    let cipher = Aes256Gcm::new_from_slice(key).map_err(|_| Failure::Other)?;
    let sealed = cipher
        .encrypt(Nonce::from_slice(nonce), Payload { msg: value, aad: ASSOCIATED_DATA })
        .map_err(|_| Failure::Other)?;
    let mut out = nonce.to_vec();
    out.extend_from_slice(&sealed);
    Ok(out)
}

/// `Path.home()`: `$HOME` when set (even empty), else the password database.
fn home() -> PathBuf {
    match std::env::var_os("HOME") {
        Some(home) => PathBuf::from(home),
        #[allow(deprecated)] // `home_dir` reads the password database when HOME is unset.
        None => std::env::home_dir().unwrap_or_default(),
    }
}

/// `_create_private_folders`: make each missing folder with mode 0700 (an existing one is
/// never changed, and one created meanwhile is fine).
fn create_private_folders(folder: &Path) -> Result<(), Failure> {
    let mut missing = Vec::new();
    let mut current = folder;
    while std::fs::symlink_metadata(current).is_err() {
        match current.parent() {
            Some(parent) if parent != current => {
                missing.push(current.to_path_buf());
                current = parent;
            }
            _ => break,
        }
    }
    for path in missing.iter().rev() {
        match fs::mkdir(path, Mode::from_raw_mode(0o700)) {
            Ok(()) | Err(Errno::EXIST) => {}
            Err(errno) => return Err(errno.into()),
        }
    }
    Ok(())
}

/// `_read_key`: through the folder, never following a link; `Missing` only if absent.
fn read_key(folder: &OwnedFd) -> Result<Vec<u8>, Failure> {
    let descriptor = fs::openat(
        folder,
        KEY_FILE_NAME,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::CLOEXEC | OFlags::NONBLOCK,
        Mode::empty(),
    )?;
    let status = fs::fstat(&descriptor)?;
    if !FileType::from_raw_mode(status.st_mode).is_file()
        || status.st_uid != rustix::process::geteuid().as_raw()
        || status.st_mode & 0o077 != 0
        || status.st_nlink != 1
        || status.st_size != KEY_BYTES as i64
    {
        return Err(Failure::Other);
    }
    let mut key = Vec::with_capacity(KEY_BYTES + 1);
    let mut chunk = [0u8; KEY_BYTES + 1];
    while key.len() <= KEY_BYTES {
        let read = rustix::io::read(&descriptor, &mut chunk[..KEY_BYTES + 1 - key.len()])?;
        if read == 0 {
            break;
        }
        key.extend_from_slice(&chunk[..read]);
    }
    if key.len() != KEY_BYTES {
        return Err(Failure::Other);
    }
    Ok(key)
}

/// `_create_key`: write a random key under a temporary name, then link it into place; losing
/// a race leaves the other process's key, which is read instead.
fn create_key(folder: &OwnedFd) -> Result<(), Failure> {
    let mut suffix = [0u8; 8];
    getrandom::getrandom(&mut suffix).map_err(|_| Failure::Other)?;
    let temporary =
        format!(".{KEY_FILE_NAME}-{}.tmp", suffix.iter().map(|b| format!("{b:02x}")).collect::<String>());
    let descriptor = fs::openat(
        folder,
        temporary.as_str(),
        OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::from_raw_mode(0o600),
    )?;
    let result = (|| -> Result<(), Failure> {
        let written = (|| -> Result<(), Failure> {
            let mut key = [0u8; KEY_BYTES];
            getrandom::getrandom(&mut key).map_err(|_| Failure::Other)?;
            let mut remaining: &[u8] = &key;
            while !remaining.is_empty() {
                let count = rustix::io::write(&descriptor, remaining)?;
                remaining = &remaining[count..];
            }
            fs::fsync(&descriptor)?;
            Ok(())
        })();
        drop(descriptor);
        written?;
        match fs::linkat(folder, temporary.as_str(), folder, KEY_FILE_NAME, AtFlags::empty()) {
            Ok(()) | Err(Errno::EXIST) => Ok(()),
            Err(errno) => Err(errno.into()),
        }
    })();
    let _ = fs::unlinkat(folder, temporary.as_str(), AtFlags::empty());
    result?;
    fs::fsync(folder)?;
    Ok(())
}

/// `_TEMPORARY_NAME.fullmatch(name)`: `.credential-key-` and 16 lower-case hex digits, `.tmp`.
fn is_temporary_name(name: &[u8]) -> bool {
    let Some(rest) = name.strip_prefix(b".credential-key-") else { return false };
    let Some(hex) = rest.strip_suffix(b".tmp") else { return false };
    hex.len() == 16 && hex.iter().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(b))
}

/// `_remove_interrupted_temporaries`: under the exclusive lock, clear temporaries a killed
/// creation left behind. The key itself is never touched.
fn remove_interrupted_temporaries(folder: &OwnedFd) {
    let Ok(listing) = fs::Dir::read_from(folder.as_fd()) else { return };
    let names: Vec<Vec<u8>> =
        listing.filter_map(Result::ok).map(|entry| entry.file_name().to_bytes().to_vec()).collect();
    for name in names {
        if is_temporary_name(&name) {
            if let Ok(name) = std::ffi::CString::new(name) {
                let _ = fs::unlinkat(folder, name.as_c_str(), AtFlags::empty());
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temporary_state() -> PathBuf {
        let mut suffix = [0u8; 8];
        getrandom::getrandom(&mut suffix).unwrap();
        let folder = std::env::temp_dir().join(format!(
            "sylvia-keyfile-test-{}",
            suffix.iter().map(|b| format!("{b:02x}")).collect::<String>()
        ));
        std::fs::create_dir(&folder).unwrap();
        folder
    }

    /// A disposable key in a folder this test creates; no real credential is read or written.
    #[test]
    fn seal_creates_a_private_key_and_opens_its_own_envelope() {
        let state = temporary_state();
        let keys = KeyFile::in_state_folder(&state);
        let sealed = keys.seal(b"disposable secret", false).unwrap();
        assert_eq!(sealed.len(), NONCE_BYTES + 17 + TAG_BYTES);
        assert_eq!(keys.seal(&sealed, true).unwrap(), b"disposable secret");
        let key_path = state.join("sylvia-gm").join(KEY_FILE_NAME);
        let metadata = std::fs::metadata(&key_path).unwrap();
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(metadata.permissions().mode() & 0o777, 0o600);
        assert_eq!(metadata.len(), KEY_BYTES as u64);
        let mut tampered = sealed.clone();
        tampered[NONCE_BYTES] ^= 1;
        assert_eq!(keys.seal(&tampered, true).unwrap_err().to_string(), UNAVAILABLE_MESSAGE);
        std::fs::remove_dir_all(&state).unwrap();
    }

    #[test]
    fn opening_never_creates_a_key() {
        let state = temporary_state();
        let keys = KeyFile::in_state_folder(&state);
        assert_eq!(keys.seal(&[0u8; 40], true).unwrap_err().to_string(), UNAVAILABLE_MESSAGE);
        assert!(!state.join("sylvia-gm").join(KEY_FILE_NAME).exists());
        std::fs::remove_dir_all(&state).unwrap();
    }

    #[test]
    fn a_relative_state_folder_fails_closed() {
        let keys = KeyFile::in_state_folder(Path::new("relative"));
        assert_eq!(keys.seal(b"x", false).unwrap_err().to_string(), PROTECT_FAILED);
    }

    #[test]
    fn temporary_names_match_python_pattern() {
        assert!(is_temporary_name(b".credential-key-0123456789abcdef.tmp"));
        assert!(!is_temporary_name(b".credential-key-0123456789ABCDEF.tmp"));
        assert!(!is_temporary_name(b".credential-key-0123.tmp"));
        assert!(!is_temporary_name(b"credential-key"));
    }

    /// The bytes Python's `AESGCM` writes for a fixed key and nonce
    /// (`tests/vectors/credential_vectors.json`), so each side opens the other's envelopes.
    #[test]
    fn sealing_matches_python_bytes() {
        let vectors: serde_json::Value =
            serde_json::from_str(include_str!("../../../tests/vectors/credential_vectors.json")).unwrap();
        for case in vectors["aes_gcm"].as_array().unwrap() {
            let hex = |field: &str| -> Vec<u8> {
                let text = case[field].as_str().unwrap();
                (0..text.len()).step_by(2).map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap()).collect()
            };
            let nonce: [u8; NONCE_BYTES] = hex("nonce").try_into().unwrap();
            assert_eq!(seal_with(&hex("key"), &nonce, &hex("plaintext")).unwrap(), hex("sealed"));
        }
    }
}
