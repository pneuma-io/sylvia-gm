//! The owner's launch pieces: `infrastructure/launch_handshake.py` (the per-launch secret and
//! its HMAC transcript), `infrastructure/owner_process_probe.py` (the second-instance probe) and
//! the store half of `LocalOwnerSupervisor.acquire_owner` (take the lock by opening the store,
//! then issue the credential).
//!
//! The supervisor's decision, its command routing and `complete_handshake` are lane 5's
//! (`interfaces/local_owner.py`); they call these.

use crate::error::StoreResult;
use crate::handle::{StoreHandle, StoreThread};
use crate::store::{AuthorityStore, BeforeMigration};
use hmac::{Hmac, Mac};
use sha2::Sha256;
use std::fs::{OpenOptions, TryLockError};
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use sylvia_base::CodedError;

/// `_HANDSHAKE_TRANSCRIPT`.
pub const HANDSHAKE_TRANSCRIPT: &[u8] = b"sylvia-gm-f6-local-owner-handshake-v1";

/// `LaunchHandshakeError(message)`: a `RuntimeError` with no code of its own.
pub fn launch_handshake_error(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("LaunchHandshakeError")
}

fn response_for(secret: &[u8]) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(secret).expect("HMAC accepts any key length");
    mac.update(HANDSHAKE_TRANSCRIPT);
    hex::encode(mac.finalize().into_bytes())
}

/// `bytes.fromhex(text)` (CPython 3.12): ASCII whitespace may come before each pair and at the
/// end, never inside one; anything else, including a non-ASCII character, is refused.
fn py_fromhex(text: &str) -> Option<Vec<u8>> {
    fn digit(byte: u8) -> Option<u8> {
        (byte as char).to_digit(16).and_then(|value| u8::try_from(value).ok())
    }
    if !text.is_ascii() {
        return None;
    }
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len() / 2);
    let mut index = 0;
    loop {
        while index < bytes.len() && matches!(bytes[index], b' ' | b'\t' | b'\n' | b'\r' | b'\x0b' | b'\x0c')
        {
            index += 1;
        }
        if index == bytes.len() {
            return Some(out);
        }
        let top = digit(bytes[index])?;
        let bottom = digit(*bytes.get(index + 1)?)?;
        out.push(top * 16 + bottom);
        index += 2;
    }
}

/// `derive_owner_response(secret_hex)`: the owner's answer to the injected secret.
///
/// As in Python, any 64-character text `bytes.fromhex` accepts is a secret: whitespace between
/// pairs gives a shorter key, and 64 spaces give the empty key.
pub fn derive_owner_response(secret_hex: &str) -> Result<String, CodedError> {
    if secret_hex.chars().count() != 64 {
        return Err(launch_handshake_error("Owner secret must be 64 hex characters"));
    }
    let secret = py_fromhex(secret_hex)
        .ok_or_else(|| launch_handshake_error("Owner secret is not valid hexadecimal"))?;
    Ok(response_for(&secret))
}

/// `OwnerCredential`: what the supervisor injects into the owner process.
#[derive(Clone, PartialEq, Eq)]
pub struct OwnerCredential {
    /// A 256-bit ephemeral bearer secret as 64 lowercase hex characters.
    pub secret_hex: String,
    /// The HMAC-SHA256 response token the owner must expose to prove possession.
    pub expected_response_hex: String,
}

impl std::fmt::Debug for OwnerCredential {
    /// Never prints the secret (Python's dataclass `repr` would; nothing logs it).
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("OwnerCredential(<sealed>)")
    }
}

/// `_Sealed`.
struct Sealed {
    secret: [u8; 32],
}

/// `LaunchHandshake`: one-shot per-launch state. `issue` → inject the secret → `verify`.
#[derive(Default)]
pub struct LaunchHandshake {
    sealed: Option<Sealed>,
    done: bool,
}

impl std::fmt::Debug for LaunchHandshake {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("LaunchHandshake(<sealed>)")
    }
}

/// Python's `hmac.compare_digest(a, b)` on two `str`: refuses non-ASCII text with a
/// `TypeError`, otherwise compares in time independent of where they differ.
fn compare_digest(left: &str, right: &str) -> Result<bool, CodedError> {
    if !left.is_ascii() || !right.is_ascii() {
        return Err(CodedError::rejected("comparing strings with non-ASCII characters is not supported")
            .with_error_type("TypeError"));
    }
    let (left, right) = (left.as_bytes(), right.as_bytes());
    let mut difference = u8::from(left.len() != right.len());
    for (index, byte) in left.iter().enumerate() {
        difference |= byte ^ right.get(index).copied().unwrap_or(!byte);
    }
    Ok(difference == 0)
}

impl LaunchHandshake {
    /// A handshake that has not issued yet.
    pub fn new() -> Self {
        Self::default()
    }

    /// `issue`: a fresh credential. May be called exactly once.
    pub fn issue(&mut self) -> Result<OwnerCredential, CodedError> {
        if self.done {
            return Err(launch_handshake_error("Launch handshake already consumed"));
        }
        if self.sealed.is_some() {
            return Err(launch_handshake_error("Launch handshake already issued"));
        }
        let mut secret = [0_u8; 32];
        getrandom::getrandom(&mut secret).map_err(|error| CodedError::rejected(error.to_string()))?;
        let response = response_for(&secret);
        self.sealed = Some(Sealed { secret });
        Ok(OwnerCredential { secret_hex: hex::encode(secret), expected_response_hex: response })
    }

    /// `verify`: prove the owner produced the expected response. The secret is erased first,
    /// so after any outcome the handshake will not issue or verify again.
    pub fn verify(&mut self, response_hex: &str) -> Result<(), CodedError> {
        let sealed = self.sealed.take();
        self.done = true;
        let Some(sealed) = sealed else {
            return Err(launch_handshake_error("Launch handshake was not issued or already consumed"));
        };
        if response_hex.is_empty() {
            return Err(launch_handshake_error("Owner response is missing"));
        }
        if !compare_digest(&response_for(&sealed.secret), response_hex)? {
            return Err(launch_handshake_error("Owner response did not match the per-launch secret"));
        }
        Ok(())
    }

    /// `issued`.
    pub fn issued(&self) -> bool {
        self.sealed.is_some()
    }

    /// `consumed`.
    pub fn consumed(&self) -> bool {
        self.done && self.sealed.is_none()
    }

    /// Seal a known secret, for reference vectors only.
    #[doc(hidden)]
    pub fn sealed_for_vectors(secret: [u8; 32]) -> Self {
        Self { sealed: Some(Sealed { secret }), done: false }
    }
}

/// `OwnerProbeResult`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OwnerProbeResult {
    /// Another process holds the owner lock for this database.
    pub owner_live: bool,
    /// The lock sidecar that was probed.
    pub lock_path: PathBuf,
    /// Diagnostic reason for a recovery screen; never contains secrets.
    pub reason: &'static str,
}

/// `_probe_lock_file`: try the lock without keeping it.
fn probe_lock_file(lock_path: &Path) -> std::io::Result<(bool, &'static str)> {
    let mut probe = OpenOptions::new().read(true).append(true).create(true).open(lock_path)?;
    if probe.seek(SeekFrom::End(0))? == 0 {
        probe.write_all(b"0")?;
        probe.flush()?;
    }
    probe.seek(SeekFrom::Start(0))?;
    match probe.try_lock() {
        Ok(()) => {
            if probe.unlock().is_err() {
                return Ok((true, "owner lock is held by a live process"));
            }
            Ok((false, "owner lock is free"))
        }
        Err(TryLockError::WouldBlock | TryLockError::Error(_)) => {
            Ok((true, "owner lock is held by a live process"))
        }
    }
}

/// `probe_owner_live`: whether an owner holds the lock for `database_path`, without keeping
/// it and without opening the database.
pub fn probe_owner_live(database_path: &Path) -> std::io::Result<OwnerProbeResult> {
    let resolved = crate::store::resolve(database_path);
    let mut name = resolved.file_name().unwrap_or_default().to_os_string();
    name.push(".authority-owner.owner.lock");
    let lock_path = resolved.with_file_name(name);
    if let Some(parent) = lock_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let (owner_live, reason) = probe_lock_file(&lock_path)?;
    Ok(OwnerProbeResult { owner_live, lock_path, reason })
}

/// `write_recover_hint`: a human-readable recovery hint beside the lock of a stale owner.
pub fn write_recover_hint(database_path: &Path, message: &str) -> std::io::Result<PathBuf> {
    let resolved = crate::store::resolve(database_path);
    let mut name = resolved.file_name().unwrap_or_default().to_os_string();
    name.push(".authority-owner.recover.txt");
    let hint_path = resolved.with_file_name(name);
    if let Some(parent) = hint_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&hint_path, message.as_bytes())?;
    Ok(hint_path)
}

/// The owner's store, its thread, and the armed handshake.
#[derive(Debug)]
pub struct AcquiredOwner {
    /// The store thread's handle.
    pub store: StoreHandle,
    /// Joins the store thread once every handle is dropped.
    pub thread: StoreThread,
    /// The handshake to complete with the owner's response.
    pub handshake: LaunchHandshake,
    /// The credential to inject into the owner process.
    pub credential: OwnerCredential,
}

/// The store half of `LocalOwnerSupervisor.acquire_owner`: open the authority on its thread,
/// which takes the owner lock (a held lock is Python's `OwnerConflict`), then issue the
/// handshake, so a concurrent second instance sees the owner live before any credential exists.
pub fn acquire_owner(
    database_path: &Path,
    before_migration: Option<BeforeMigration>,
) -> StoreResult<AcquiredOwner> {
    let path = database_path.to_path_buf();
    let (store, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, before_migration))?;
    let mut handshake = LaunchHandshake::new();
    let credential = handshake.issue()?;
    Ok(AcquiredOwner { store, thread, handshake, credential })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fromhex_follows_cpython() {
        assert_eq!(py_fromhex("00 ff\tAB\n"), Some(vec![0, 255, 171]));
        assert_eq!(py_fromhex("0 0"), None);
        assert_eq!(py_fromhex("    "), Some(vec![]));
        assert_eq!(py_fromhex("0"), None);
        assert_eq!(py_fromhex("00\u{1c}"), None);
        assert_eq!(py_fromhex("\u{e9}\u{e9}"), None);
    }

    #[test]
    fn debug_never_shows_the_secret() {
        let mut handshake = LaunchHandshake::new();
        let credential = handshake.issue().unwrap();
        assert_eq!(format!("{handshake:?}"), "LaunchHandshake(<sealed>)");
        assert!(!format!("{credential:?}").contains(&credential.secret_hex));
    }
}
