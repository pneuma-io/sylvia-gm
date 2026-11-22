//! `SQLiteAuthorityStore.backup_database` and its helpers: durably publish one coherent online
//! copy of the authority in the supported local envelope.
//!
//! The sequence and every failure code are Python's:
//! 1. validate the target envelope;
//! 2. claim a private temporary file beside it under a write guard;
//! 3. copy online, then remove prepublication sidecars;
//! 4. take the content seal, close the write guard and flush;
//! 5. verify the copy twice through read-only, immutable connections;
//! 6. revalidate the claim and the envelope;
//! 7. close the seal and atomically replace the target.
//!
//! Any failure cleans up what this operation owns, and the cleanup failures it meets become
//! supplemental codes.
//!
//! For the whole call the caller must exclusively manage the target directory; the guard and
//! the seal are defence in depth, as in Python.

use crate::error::{StoreError, StoreResult};
use crate::safe_file::{self, stat_identity, Identity, Publication};
use crate::verify::verify_backup_identity;
use rusqlite::{Connection, OpenFlags};
use std::fs::File;
use std::path::{Path, PathBuf};
use std::time::Duration;
use sylvia_base::CodedError;

/// `_BACKUP_PAGES_PER_STEP`.
const PAGES_PER_STEP: std::os::raw::c_int = 16;
/// `_BACKUP_TEMP_ATTEMPTS`.
const TEMP_ATTEMPTS: usize = 128;
/// `timeout=30.0`.
const BUSY_TIMEOUT: Duration = Duration::from_secs(30);

/// `_BackupFailure`: a path-free failure, `backup_<phase>_<reason>`, with cleanup diagnostics.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BackupFailure {
    /// The phase that failed.
    pub phase: String,
    /// Why.
    pub reason: String,
    /// Cleanup failures met while unwinding, in order.
    pub supplemental_codes: Vec<String>,
}

impl BackupFailure {
    fn new(phase: &str, reason: &str) -> Self {
        Self { phase: phase.to_owned(), reason: reason.to_owned(), supplemental_codes: Vec::new() }
    }

    /// `backup_<phase>_<reason>`.
    pub fn code(&self) -> String {
        format!("backup_{}_{}", self.phase, self.reason)
    }

    fn with_supplemental<I: IntoIterator<Item = String>>(mut self, codes: I) -> Self {
        self.supplemental_codes.extend(codes);
        self
    }

    /// The owner-facing error: `CommandRejected("Database backup failed", code=...)`.
    pub fn to_coded(&self) -> CodedError {
        CodedError::new(self.code(), "Database backup failed").with_error_type("_BackupFailure")
    }
}

impl From<BackupFailure> for StoreError {
    fn from(failure: BackupFailure) -> Self {
        StoreError::Backup(failure)
    }
}

type Step<T> = Result<T, BackupFailure>;

fn fail<T>(phase: &str, reason: &str) -> Step<T> {
    Err(BackupFailure::new(phase, reason))
}

/// `_BackupEnvelope`.
#[derive(Debug, Clone, PartialEq, Eq)]
struct Envelope {
    target: PathBuf,
    parent_identity: Identity,
    target_identity: Option<Identity>,
    source_identity: Identity,
    volume_root: String,
}

/// `_ClaimedBackupTemporary`.
struct Claimed {
    path: PathBuf,
    identity: Identity,
    parent_identity: Identity,
    volume_root: String,
    attributes: u32,
}

/// `os.path.samefile`.
fn samefile(left: &Path, right: &Path) -> std::io::Result<bool> {
    Ok(stat_identity(left)? == stat_identity(right)?)
}

/// `os.path.lexists`.
fn lexists(path: &Path) -> bool {
    std::fs::symlink_metadata(path).is_ok()
}

/// `os.path.normcase(os.path.abspath(path))`.
fn path_key(path: &Path) -> String {
    let absolute = safe_file::abspath(path).to_string_lossy().into_owned();
    if cfg!(windows) {
        absolute.replace('/', "\\").to_lowercase()
    } else {
        absolute
    }
}

/// `Path(...).anchor`.
fn anchor(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for component in path.components() {
        match component {
            std::path::Component::Prefix(prefix) => out.push(prefix.as_os_str()),
            std::path::Component::RootDir => {
                out.push(std::path::MAIN_SEPARATOR_STR);
                break;
            }
            _ => break,
        }
    }
    out
}

fn with_suffix(path: &Path, suffix: &str) -> PathBuf {
    let mut text = path.as_os_str().to_os_string();
    text.push(suffix);
    PathBuf::from(text)
}

fn with_name_suffix(path: &Path, suffix: &str) -> PathBuf {
    let mut name = path.file_name().unwrap_or_default().to_os_string();
    name.push(suffix);
    path.with_file_name(name)
}

/// `_owned_backup_artifacts`: the temporary and everything SQLite or a lock could add beside it.
fn owned_artifacts(temporary: &Path) -> [(PathBuf, &'static str); 5] {
    [
        (temporary.to_path_buf(), "main"),
        (with_suffix(temporary, "-wal"), "wal"),
        (with_suffix(temporary, "-shm"), "shm"),
        (with_suffix(temporary, "-journal"), "journal"),
        (with_name_suffix(temporary, ".owner.lock"), "owner_lock"),
    ]
}

/// The backup operation for one authority database.
pub(crate) struct Backup<'a> {
    database_path: &'a Path,
}

impl<'a> Backup<'a> {
    pub(crate) fn new(database_path: &'a Path) -> Self {
        Self { database_path }
    }

    /// `_protected_backup_source_paths`.
    fn protected_source_paths(&self) -> [PathBuf; 6] {
        let source = self.database_path;
        [
            source.to_path_buf(),
            with_suffix(source, "-wal"),
            with_suffix(source, "-shm"),
            with_suffix(source, "-journal"),
            with_name_suffix(source, ".owner.lock"),
            with_name_suffix(source, ".authority-owner.owner.lock"),
        ]
    }

    /// `backup_database`.
    pub(crate) fn run(&self, target: &Path) -> Result<(), BackupFailure> {
        // Before the claim there is nothing of ours to clean up, and a failed claim cleans up
        // after itself, as in Python.
        let envelope = self.validate_envelope(target, None, None)?;
        let (claimed, guard) = self.claim_temporary(&envelope)?;
        let mut guard = Some(guard);
        let mut seal = None;
        let Err((failure, claim_owned)) = self.publish_claimed(&envelope, &claimed, &mut guard, &mut seal)
        else {
            return Ok(());
        };
        let mut codes = Vec::new();
        if let Some(seal) = seal {
            if !safe_file::close_handle(seal) {
                codes.push("backup_cleanup_content_seal_close_failed".to_owned());
            }
        }
        if let Some(guard) = guard {
            if !safe_file::close_handle(guard) {
                codes.push("backup_cleanup_write_guard_close_failed".to_owned());
            }
        }
        if claim_owned {
            codes.extend(cleanup_owned(&claimed));
        }
        Err(failure.with_supplemental(codes))
    }

    /// Everything after the claim. A failure says whether the temporary is still ours to clean
    /// up: after an uncertain publication the renamed copy is the only artifact, and nothing
    /// may be claimed about the old destination.
    fn publish_claimed(
        &self,
        envelope: &Envelope,
        claimed: &Claimed,
        guard: &mut Option<File>,
        seal: &mut Option<File>,
    ) -> Result<(), (BackupFailure, bool)> {
        let owned = |failure: BackupFailure| (failure, true);
        self.copy_online(&claimed.path).map_err(owned)?;
        remove_prepublication_sidecars(&claimed.path).map_err(owned)?;
        *seal = Some(acquire_content_seal(claimed).map_err(owned)?);
        if !safe_file::close_handle(guard.take().expect("the write guard is held")) {
            return Err(owned(BackupFailure::new("write_guard_close", "failed")));
        }
        if !safe_file::flush_handle(seal.as_ref().expect("the seal is held")) {
            return Err(owned(BackupFailure::new("flush", "failed")));
        }
        verify_twice(&claimed.path).map_err(owned)?;
        self.validate_claimed(claimed, seal.as_ref().expect("the seal is held"), envelope).map_err(owned)?;
        self.validate_envelope(&envelope.target, Some(envelope), Some(&claimed.path)).map_err(owned)?;
        require_absent(&owned_artifacts(&claimed.path)[1..], "final_envelope").map_err(owned)?;
        if !safe_file::close_handle(seal.take().expect("the seal is held")) {
            return Err(owned(BackupFailure::new("content_seal_close", "failed")));
        }
        match safe_file::publish_replace(&claimed.path, &envelope.target, envelope.parent_identity) {
            Publication::Published => Ok(()),
            Publication::DurabilityUncertain => {
                Err((BackupFailure::new("publication", "durability_uncertain"), false))
            }
            Publication::NotPublished => Err(owned(BackupFailure::new("publication", "replace_failed"))),
        }
    }

    /// `_validate_backup_envelope`.
    fn validate_envelope(
        &self,
        target: &Path,
        expected: Option<&Envelope>,
        temporary: Option<&Path>,
    ) -> Step<Envelope> {
        let io = |_| BackupFailure::new("target_envelope", "failed");
        if !safe_file::lexical_backup_target_supported(target) {
            return fail("target_envelope", "unsupported");
        }
        let normalized = safe_file::abspath(target);
        let volume_root = anchor(&normalized);
        if safe_file::volume_admission(&normalized).is_some() {
            return fail("target_envelope", "volume_unsupported");
        }
        let parent = normalized.parent().map_or_else(|| normalized.clone(), Path::to_path_buf);
        let parent_attributes = safe_file::entry_attributes(&parent).map_err(io)?;
        let parent_is_dir = match parent_attributes {
            Some(_) => std::fs::metadata(&parent).map_err(io)?.is_dir(),
            None => false,
        };
        let Some(parent_attributes) = parent_attributes.filter(|_| parent_is_dir) else {
            return fail("target_envelope", "parent_invalid");
        };
        require_supported(parent_attributes)?;
        if let Some(refusal) = safe_file::ancestors_admission(&parent).map_err(io)? {
            return fail("target_envelope", refusal);
        }
        let parent_identity = stat_identity(&parent).map_err(io)?;

        let target_attributes = safe_file::entry_attributes(&normalized).map_err(io)?;
        let mut target_identity = None;
        if let Some(attributes) = target_attributes {
            require_supported(attributes)?;
            let metadata = std::fs::symlink_metadata(&normalized).map_err(io)?;
            if !metadata.file_type().is_file() {
                return fail("target_envelope", "target_not_regular");
            }
            target_identity = Some(lstat_identity(&normalized, &metadata).map_err(io)?);
        }

        if lexists(&with_name_suffix(&normalized, ".owner.lock")) {
            return fail("target_envelope", "destination_owner_lock");
        }

        let target_key = path_key(&normalized);
        for protected in self.protected_source_paths() {
            if target_key == path_key(&protected) {
                return fail("target_envelope", "source_alias");
            }
            if target_attributes.is_some() && lexists(&protected) {
                match samefile(&normalized, &protected) {
                    Ok(true) => return fail("target_envelope", "source_alias"),
                    Ok(false) => {}
                    Err(_) => return fail("target_envelope", "identity_failed"),
                }
            }
        }
        if let Some(temporary) = temporary {
            if target_key == path_key(temporary) {
                return fail("target_envelope", "temporary_alias");
            }
            if target_attributes.is_some() {
                match samefile(&normalized, temporary) {
                    Ok(true) => return fail("target_envelope", "temporary_alias"),
                    Ok(false) => {}
                    Err(_) => return fail("target_envelope", "identity_failed"),
                }
            }
        }
        let source_identity = stat_identity(self.database_path).map_err(io)?;
        let envelope = Envelope {
            target: normalized,
            parent_identity,
            target_identity,
            source_identity,
            volume_root: path_key(&volume_root),
        };
        if let Some(expected) = expected {
            if &envelope != expected {
                return fail("final_envelope", "identity_drift");
            }
        }
        Ok(envelope)
    }

    /// `_backup_path_handle_identity`: the identity through a handle on the name itself.
    fn path_handle_identity(path: &Path) -> std::io::Result<Identity> {
        let handle = safe_file::open_identity_handle(path)?;
        let identity = safe_file::handle_identity(&handle);
        if !safe_file::close_handle(handle) && identity.is_ok() {
            return Err(std::io::Error::other("identity handle close failed"));
        }
        identity
    }

    /// `_claim_backup_temporary`.
    fn claim_temporary(&self, envelope: &Envelope) -> Step<(Claimed, File)> {
        for _ in 0..TEMP_ATTEMPTS {
            let candidate = temp_candidate(&envelope.target)?;
            let descriptor = match safe_file::create_exclusive(&candidate) {
                Ok(descriptor) => descriptor,
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(_) => return fail("temporary_claim", "create_failed"),
            };
            let mut descriptor = Some(descriptor);
            let mut guard: Option<File> = None;
            let mut identity: Option<Identity> = None;
            let mut attributes: Option<u32> = None;
            let attempt = (|| -> Step<()> {
                let held = descriptor.as_ref().expect("descriptor held");
                identity = Some(
                    safe_file::descriptor_identity(held)
                        .map_err(|_| BackupFailure::new("temporary_claim", "identity_failed"))?,
                );
                let validation = |_| BackupFailure::new("temporary_claim", "validation_failed");
                let is_regular = held.metadata().map_err(validation)?.file_type().is_file();
                attributes = safe_file::entry_attributes(&candidate).map_err(validation)?;
                let Some(found) = attributes.filter(|_| is_regular) else {
                    return fail("temporary_claim", "object_invalid");
                };
                require_supported(found)?;
                let parent = candidate.parent().unwrap_or_else(|| Path::new(""));
                if stat_identity(parent).map_err(validation)? != envelope.parent_identity {
                    return fail("temporary_claim", "parent_identity_drift");
                }
                if path_key(&anchor(&candidate)) != envelope.volume_root {
                    return fail("temporary_claim", "volume_drift");
                }
                if identity == Some(Self::path_handle_identity(self.database_path).map_err(validation)?) {
                    return fail("temporary_claim", "source_alias");
                }
                guard = Some(
                    safe_file::open_write_guard(&candidate)
                        .map_err(|_| BackupFailure::new("write_guard_open", "failed"))?,
                );
                let guard_identity = safe_file::handle_identity(guard.as_ref().expect("guard held"))
                    .map_err(|_| BackupFailure::new("write_guard_identity", "failed"))?;
                if Some(guard_identity) != identity {
                    return fail("write_guard_identity", "mismatch");
                }
                if !safe_file::close_handle(descriptor.take().expect("descriptor held")) {
                    return fail("temporary_claim", "close_failed");
                }
                Ok(())
            })();
            match attempt {
                Ok(()) => {
                    let claimed = Claimed {
                        path: candidate,
                        identity: identity.expect("identity read"),
                        parent_identity: envelope.parent_identity,
                        volume_root: envelope.volume_root.clone(),
                        attributes: attributes.expect("attributes read"),
                    };
                    return Ok((claimed, guard.expect("guard held")));
                }
                Err(failure) => {
                    let mut codes = Vec::new();
                    if let Some(descriptor) = descriptor {
                        if !safe_file::close_handle(descriptor) {
                            codes.push("backup_cleanup_claim_descriptor_close_failed".to_owned());
                        }
                    }
                    if let Some(guard) = guard {
                        if !safe_file::close_handle(guard) {
                            codes.push("backup_cleanup_write_guard_close_failed".to_owned());
                        }
                    }
                    match (identity, attributes) {
                        (Some(identity), Some(attributes)) => codes.extend(cleanup_owned(&Claimed {
                            path: candidate,
                            identity,
                            parent_identity: envelope.parent_identity,
                            volume_root: envelope.volume_root.clone(),
                            attributes,
                        })),
                        _ => codes.push("backup_cleanup_main_identity_failed".to_owned()),
                    }
                    return Err(failure.with_supplemental(codes));
                }
            }
        }
        fail("temporary_claim", "collision_limit")
    }

    /// `_copy_online_backup`: page-stepped online copy from a read-only source connection.
    fn copy_online(&self, temporary: &Path) -> Step<()> {
        (|| -> Step<()> {
            let as_failure = |phase: &'static str| move |_| BackupFailure::new(phase, "failed");
            let source = Connection::open_with_flags(
                self.database_path,
                OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
            )
            .map_err(as_failure("source_open"))?;
            source.busy_timeout(BUSY_TIMEOUT).map_err(as_failure("source_open"))?;
            let mut destination = Connection::open_with_flags(
                temporary,
                OpenFlags::SQLITE_OPEN_READ_WRITE
                    | OpenFlags::SQLITE_OPEN_CREATE
                    | OpenFlags::SQLITE_OPEN_NO_MUTEX,
            )
            .map_err(as_failure("target_open"))?;
            destination.busy_timeout(BUSY_TIMEOUT).map_err(as_failure("target_open"))?;
            let mut hook_fired = false;
            {
                use rusqlite::backup::StepResult;
                let backup = rusqlite::backup::Backup::new(&source, &mut destination)
                    .map_err(as_failure("online_copy"))?;
                loop {
                    let step = backup.step(PAGES_PER_STEP).map_err(as_failure("online_copy"))?;
                    let progress = backup.progress();
                    // Python's progress callback fires `_after_backup_snapshot_started` once,
                    // after the first OK or DONE step that copied pages.
                    if !hook_fired
                        && matches!(step, StepResult::Done | StepResult::More)
                        && progress.pagecount > progress.remaining
                    {
                        hook_fired = true;
                    }
                    match step {
                        StepResult::Done => break,
                        StepResult::More => {}
                        _ => std::thread::sleep(Duration::from_millis(10)),
                    }
                }
            }
            if !hook_fired {
                return fail("online_copy", "progress_missing");
            }
            let mut failure: Option<BackupFailure> = None;
            if destination.close().is_err() {
                failure = Some(BackupFailure::new("target_close", "failed"));
            }
            if source.close().is_err() {
                failure = Some(match failure {
                    None => BackupFailure::new("source_close", "failed"),
                    Some(failure) => failure.with_supplemental(["backup_source_close_failed".to_owned()]),
                });
            }
            failure.map_or(Ok(()), Err)
        })()
    }

    /// `_validate_claimed_backup_temporary`.
    fn validate_claimed(&self, claimed: &Claimed, seal: &File, envelope: &Envelope) -> Step<()> {
        let validation = |_| BackupFailure::new("final_envelope", "temporary_validation_failed");
        let seal_identity = safe_file::handle_identity(seal).map_err(validation)?;
        if seal_identity != claimed.identity {
            return fail("final_envelope", "temporary_identity_drift");
        }
        let path_identity = Self::path_handle_identity(&claimed.path).map_err(validation)?;
        if path_identity != claimed.identity || path_identity != seal_identity {
            return fail("final_envelope", "temporary_identity_drift");
        }
        let attributes = safe_file::entry_attributes(&claimed.path).map_err(validation)?;
        if attributes != Some(claimed.attributes) {
            return fail("final_envelope", "temporary_attribute_drift");
        }
        require_supported(claimed.attributes)?;
        if !std::fs::symlink_metadata(&claimed.path).map_err(validation)?.file_type().is_file() {
            return fail("final_envelope", "temporary_not_regular");
        }
        let parent = claimed.path.parent().unwrap_or_else(|| Path::new(""));
        if stat_identity(parent).map_err(validation)? != claimed.parent_identity {
            return fail("final_envelope", "temporary_parent_drift");
        }
        if path_key(&anchor(&claimed.path)) != claimed.volume_root {
            return fail("final_envelope", "temporary_volume_drift");
        }
        let claimed_key = path_key(&claimed.path);
        let forbidden = self
            .protected_source_paths()
            .into_iter()
            .chain([envelope.target.clone(), with_name_suffix(&envelope.target, ".owner.lock")]);
        for path in forbidden {
            if claimed_key == path_key(&path) {
                return fail("final_envelope", "temporary_alias");
            }
            if lexists(&path) && samefile(&claimed.path, &path).map_err(validation)? {
                return fail("final_envelope", "temporary_alias");
            }
        }
        Ok(())
    }
}

/// `os.lstat` identity of an entry that is known to be a regular file: the metadata already
/// read on Linux, the same handle identity as `stat` elsewhere (a regular file is no link).
#[cfg(target_os = "linux")]
fn lstat_identity(_path: &Path, metadata: &std::fs::Metadata) -> std::io::Result<Identity> {
    use std::os::unix::fs::MetadataExt;
    Ok((metadata.dev(), metadata.ino()))
}

#[cfg(not(target_os = "linux"))]
fn lstat_identity(path: &Path, _metadata: &std::fs::Metadata) -> std::io::Result<Identity> {
    stat_identity(path)
}

/// `_require_supported_backup_attributes`.
fn require_supported(attributes: u32) -> Step<()> {
    if safe_file::attributes_supported(attributes) {
        Ok(())
    } else {
        fail("target_envelope", "attributes_unsupported")
    }
}

/// `_backup_temp_candidate`: `.<name>.backup-<32 hex>.tmp` beside the target.
fn temp_candidate(target: &Path) -> Step<PathBuf> {
    let mut token = [0_u8; 16];
    getrandom::getrandom(&mut token).map_err(|_| BackupFailure::new("temporary_claim", "create_failed"))?;
    let name = target.file_name().unwrap_or_default().to_string_lossy();
    Ok(target.with_file_name(format!(".{name}.backup-{}.tmp", hex(&token))))
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// `_acquire_backup_content_seal`.
fn acquire_content_seal(claimed: &Claimed) -> Step<File> {
    let seal = safe_file::open_content_seal(&claimed.path)
        .map_err(|_| BackupFailure::new("content_seal_open", "failed"))?;
    let result = match safe_file::handle_identity(&seal) {
        Err(_) => fail("content_seal_identity", "failed"),
        Ok(identity) if identity != claimed.identity => fail("content_seal_identity", "mismatch"),
        Ok(_) => return Ok(seal),
    };
    if !safe_file::close_handle(seal) {
        return result.map_err(|failure| {
            failure.with_supplemental(["backup_cleanup_content_seal_close_failed".to_owned()])
        });
    }
    result
}

/// `_remove_prepublication_sidecars`.
fn remove_prepublication_sidecars(temporary: &Path) -> Step<()> {
    for (path, role) in &owned_artifacts(temporary)[1..] {
        if lexists(path) && std::fs::remove_file(path).is_err() {
            return fail("prepublication_cleanup", &format!("{role}_unlink_failed"));
        }
        if lexists(path) {
            return fail("prepublication_cleanup", &format!("{role}_absence_failed"));
        }
    }
    Ok(())
}

/// `_require_backup_artifacts_absent`.
fn require_absent(artifacts: &[(PathBuf, &'static str)], phase: &str) -> Step<()> {
    for (path, role) in artifacts {
        if lexists(path) {
            return fail(phase, &format!("{role}_present"));
        }
    }
    Ok(())
}

/// `_cleanup_owned_backup`: sidecars in reverse order, then the claimed main file.
fn cleanup_owned(claimed: &Claimed) -> Vec<String> {
    let mut codes = Vec::new();
    for (path, role) in owned_artifacts(&claimed.path)[1..].iter().rev() {
        if lexists(path) && std::fs::remove_file(path).is_err() {
            codes.push(format!("backup_cleanup_{role}_unlink_failed"));
        }
        if lexists(path) {
            codes.push(format!("backup_cleanup_{role}_absence_failed"));
        }
    }
    codes.extend(cleanup_claimed_main(claimed));
    codes
}

/// `_cleanup_claimed_backup_main`: delete the main file through a handle proven to hold it.
fn cleanup_claimed_main(claimed: &Claimed) -> Vec<String> {
    let mut codes = Vec::new();
    let mut identity_matched = false;
    let mut disposition_requested = false;
    match safe_file::open_cleanup_handle(&claimed.path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => codes.push("backup_cleanup_main_identity_failed".to_owned()),
        Ok(handle) => {
            match safe_file::handle_identity(&handle) {
                Err(_) => codes.push("backup_cleanup_main_identity_failed".to_owned()),
                Ok(identity) if identity != claimed.identity => {
                    codes.push("backup_cleanup_main_identity_drift".to_owned());
                }
                Ok(_) => {
                    identity_matched = true;
                    disposition_requested = safe_file::delete_by_handle(&handle, &claimed.path);
                    if !disposition_requested {
                        codes.push("backup_cleanup_main_disposition_failed".to_owned());
                    }
                }
            }
            if !safe_file::close_handle(handle) {
                codes.push("backup_cleanup_main_close_failed".to_owned());
            }
        }
    }
    if identity_matched && disposition_requested && lexists(&claimed.path) {
        codes.push("backup_cleanup_main_absence_failed".to_owned());
    }
    codes
}

/// `_verify_backup_twice`: two independent read-only, immutable verifications.
fn verify_twice(temporary: &Path) -> Step<()> {
    let sidecars = &owned_artifacts(temporary)[1..];
    require_absent(sidecars, "verification")?;
    for _ in 0..2 {
        let connection =
            open_verifier(temporary).map_err(|_| BackupFailure::new("verification", "failed"))?;
        let verified = (|| -> Step<()> {
            let failed = |_| BackupFailure::new("verification", "failed");
            connection.execute_batch("PRAGMA query_only = ON").map_err(failed)?;
            let query_only: i64 =
                connection.query_row("PRAGMA query_only", [], |row| row.get(0)).map_err(failed)?;
            if query_only != 1 {
                return fail("verification", "query_only_failed");
            }
            verify_backup_authority(&connection).map_err(|_| BackupFailure::new("verification", "failed"))
        })();
        let closed = connection.close().is_ok();
        match (verified, closed) {
            (Err(failure), true) => return Err(failure),
            (Err(failure), false) => {
                return Err(failure.with_supplemental(["backup_verifier_close_failed".to_owned()]));
            }
            (Ok(()), false) => return fail("verifier_close", "failed"),
            (Ok(()), true) => {}
        }
        require_absent(sidecars, "verification")?;
    }
    Ok(())
}

/// `_open_backup_verifier`: `file:...?mode=ro&immutable=1`.
fn open_verifier(path: &Path) -> rusqlite::Result<Connection> {
    let connection = Connection::open_with_flags(
        file_uri(path, "mode=ro&immutable=1"),
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_URI | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    connection.busy_timeout(BUSY_TIMEOUT)?;
    Ok(connection)
}

/// `Path.as_uri()` followed by `?query`: percent-encoded UTF-8 with `/` separators.
fn file_uri(path: &Path, query: &str) -> String {
    let absolute = safe_file::abspath(path).to_string_lossy().replace('\\', "/");
    let mut encoded = String::new();
    for byte in absolute.bytes() {
        if byte.is_ascii_alphanumeric() || b"-._~/:".contains(&byte) {
            encoded.push(char::from(byte));
        } else {
            encoded.push_str(&format!("%{byte:02X}"));
        }
    }
    let prefix = if encoded.starts_with('/') { "file://" } else { "file:///" };
    format!("{prefix}{encoded}?{query}")
}

/// `_verify_backup_authority`: the identity of a backup copy, then the stored-content verifiers
/// Python runs on it: `verify_f5_campaign_authority` from schema 5, the continuity verifier from
/// schema 9.
fn verify_backup_authority(connection: &Connection) -> StoreResult<()> {
    let version = verify_backup_identity(connection)?;
    crate::stored::verify_stored_authority(connection)?;
    crate::packages::verify_package_authority(connection)?;
    if (crate::schema::PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION..=crate::schema::CAMPAIGN_SCHEMA_VERSION)
        .contains(&version)
    {
        crate::f5::verify_f5_campaign_authority(connection)?;
    }
    if version >= crate::schema::PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION {
        crate::continuity::verify_authority(connection)?;
    }
    Ok(())
}
