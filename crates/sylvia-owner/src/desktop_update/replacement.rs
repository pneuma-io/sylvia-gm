//! `application/desktop_update/replacement.py`: resumable portable file replacement, used only
//! after the native process barrier. Old files are retained first, and each atomic
//! replacement is reconciled by its exact byte identity.

use super::manifest::{
    bounded_json, payload_path, required_payloads, Manifest, PayloadFile, ARTIFACT_LIMIT, ENTRY_MANIFEST,
    MANIFEST_LIMIT,
};
use super::storage::{
    atomic_receipt, checked_file, directory_identity, disk_free, ensure_directory, is_relative_to, os_error,
    present, same_path, stable_digest, Identity,
};
use super::update_error;
use crate::util::uuid4_hex;
use std::collections::{BTreeMap, HashSet};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

/// `ReplacementPhase`.
pub mod phase {
    pub const RETAINING: &str = "retaining";
    pub const PREPARED: &str = "prepared";
    pub const APPLYING: &str = "applying";
    pub const AWAITING_RESTART: &str = "awaiting_restart";
    pub const ROLLING_BACK: &str = "rolling_back";
    pub const ROLLED_BACK: &str = "rolled_back";
    pub const CONFIRMED: &str = "confirmed";
}

/// `REPLACEMENT_MOVES`, kept as data as Python's tests use it.
pub const REPLACEMENT_MOVES: [(&str, &[&str]); 7] = {
    use phase::*;
    [
        (RETAINING, &[PREPARED]),
        (PREPARED, &[APPLYING, ROLLING_BACK]),
        (APPLYING, &[APPLYING, AWAITING_RESTART, ROLLING_BACK]),
        (AWAITING_RESTART, &[APPLYING, CONFIRMED, ROLLING_BACK]),
        (ROLLING_BACK, &[ROLLING_BACK, ROLLED_BACK]),
        (ROLLED_BACK, &[ROLLING_BACK]),
        (CONFIRMED, &[CONFIRMED, ROLLING_BACK]),
    ]
};

/// `file_matches(path, row)`: one regular, unaliased application file with exactly the
/// row's size and SHA-256, read without loading it whole. A missing file does not match; an
/// aliased folder is refused.
pub fn file_matches(path: &Path, row: &PayloadFile) -> CodedResult<bool> {
    let parent = path.parent().unwrap_or(Path::new(""));
    if !present(parent) {
        let mut ancestor = parent;
        while !present(ancestor) {
            match ancestor.parent() {
                Some(next) => ancestor = next,
                None => break,
            }
        }
        directory_identity(ancestor)?;
        return Ok(false);
    }
    directory_identity(parent)?;
    match stable_digest(path, row.size) {
        Ok(Some(digest)) => Ok(digest == row.sha256),
        Ok(None) => Ok(false),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(os_error(&error)),
    }
}

/// `_require_file(path, row)`.
pub fn require_file(path: &Path, row: &PayloadFile) -> CodedResult<()> {
    if !file_matches(path, row)? {
        return Err(update_error(
            "application_changed",
            "Application files changed; keep this copy open for repair.",
        ));
    }
    Ok(())
}

/// `_copy(source, destination, row)`: a verified copy into a new file, checked on both sides
/// afterwards.
pub fn copy_file(source: &Path, destination: &Path, row: &PayloadFile) -> CodedResult<()> {
    require_file(source, row)?;
    let parent = destination.parent().unwrap_or(Path::new(""));
    ensure_directory(parent)?;
    directory_identity(parent)?;
    let copied = (|| -> std::io::Result<()> {
        let mut incoming = std::fs::File::open(source)?;
        let mut output = std::fs::OpenOptions::new().write(true).create_new(true).open(destination)?;
        let mut chunk = vec![0u8; 256 * 1024];
        loop {
            let read = incoming.read(&mut chunk)?;
            if read == 0 {
                break;
            }
            output.write_all(&chunk[..read])?;
        }
        output.flush()?;
        output.sync_all()
    })();
    copied.map_err(|error| os_error(&error))?;
    require_file(destination, row)?;
    require_file(source, row)
}

/// `ReleaseArtifactError`: a `ValueError` that is not an `UpdateError`, so a caller reports
/// its generic failure.
fn release_artifact_error(message: &str) -> CodedError {
    CodedError::new("command_rejected", message).with_error_type("ReleaseArtifactError")
}

/// `portable_artifact._validate_payload_path(name, metadata=True)`.
fn validate_portable_payload(name: &str) -> CodedResult<()> {
    let mut allowed: Vec<String> = required_payloads();
    allowed.extend(
        [ENTRY_MANIFEST, "BUILD-IDENTITY.json", "SOURCE-INPUTS.json"].into_iter().map(str::to_string),
    );
    if !allowed.iter().any(|known| known == name) && !name.starts_with("licenses/") {
        return Err(release_artifact_error("Unexpected portable payload; mutable data is never packaged"));
    }
    sylvia_domain::package_library::validate_portable_path(name)
        .map(|_| ())
        .map_err(|_| release_artifact_error("Unsafe portable payload path"))
}

fn lower_hex_64(value: &Json) -> Option<String> {
    let text = value.as_str()?;
    (text.len() == 64 && text.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')))
        .then(|| text.to_string())
}

/// `{"version": 1}` with an integer (`marker == {"version": 1}` and `type(...) is int`).
pub fn portable_marker_valid(marker: &Json) -> bool {
    marker.as_object().is_some_and(|object| {
        object.len() == 1
            && object.get("version").is_some_and(|version| version.is_number() && version.as_i64() == Some(1))
    })
}

/// `_inventory(root)`: the portable copy's own hash manifest, every file in it checked, sorted
/// by path, with the manifest itself last added.
pub fn inventory(root: &Path) -> CodedResult<Vec<PayloadFile>> {
    let raw = checked_file(&root.join(ENTRY_MANIFEST), MANIFEST_LIMIT)?;
    let entries = bounded_json(&raw, MANIFEST_LIMIT)?;
    let entries = match &entries {
        Json::Object(entries) if (3..=4096).contains(&entries.len()) => entries,
        _ => {
            return Err(update_error(
                "invalid_installation",
                "This portable copy has no usable application inventory.",
            ))
        }
    };
    let mut files = Vec::new();
    let mut names: HashSet<String> = HashSet::new();
    for (name, record) in entries {
        let name = payload_path(&Json::String(name.clone()))?;
        validate_portable_payload(&name)?;
        let invalid =
            || update_error("invalid_installation", "The current application inventory is invalid.");
        let folded = name.to_ascii_lowercase();
        let Some(record) = record.as_object() else {
            return Err(invalid());
        };
        if names.contains(&folded) || name == ENTRY_MANIFEST || record.len() != 2 {
            return Err(invalid());
        }
        let size = match record.get("bytes") {
            Some(value) if value.is_number() => {
                value.as_u64().filter(|size| (1..=ARTIFACT_LIMIT).contains(size))
            }
            _ => None,
        };
        let (Some(size), Some(sha256)) = (size, record.get("sha256").and_then(lower_hex_64)) else {
            return Err(invalid());
        };
        names.insert(folded);
        files.push(PayloadFile { path: name, size, sha256 });
    }
    if !required_payloads().iter().all(|name| entries.contains_key(name)) {
        return Err(update_error("invalid_installation", "The current portable copy is incomplete."));
    }
    files.push(PayloadFile {
        path: ENTRY_MANIFEST.to_string(),
        size: raw.len() as u64,
        sha256: sylvia_base::hash::sha256_hex(&raw),
    });
    for row in &files {
        require_file(&root.join(&row.path), row)?;
    }
    let marker = bounded_json(&checked_file(&root.join("sylvia-portable.json"), 128)?, 128)?;
    if !portable_marker_valid(&marker) {
        return Err(update_error("wrong_distribution", "The current portable marker is incompatible."));
    }
    files.sort_by(|left, right| left.path.cmp(&right.path));
    Ok(files)
}

fn record_value(row: &PayloadFile) -> Json {
    let mut value = JsonMap::new();
    value.insert("path".into(), row.path.clone().into());
    value.insert("size".into(), row.size.into());
    value.insert("sha256".into(), row.sha256.clone().into());
    Json::Object(value)
}

/// The one way the portable replacement reports that its folders changed.
fn folders_changed() -> CodedError {
    update_error("unsafe_staging", "The application or update folder changed.")
}

/// `PortableReplacement`: retain the old files, then replace each one atomically.
#[derive(Debug, Clone)]
pub struct PortableReplacement {
    pub live: PathBuf,
    pub operation: PathBuf,
    pub manifest: Manifest,
    pub prepared: PathBuf,
    pub previous: PathBuf,
    journal: PathBuf,
    live_identity: Identity,
    operation_identity: Identity,
    old: Vec<PayloadFile>,
}

impl PortableReplacement {
    /// `PortableReplacement(live, operation, manifest)`.
    pub fn new(live: &Path, operation: &Path, manifest: &Manifest) -> CodedResult<Self> {
        if manifest.distribution != "portable" {
            return Err(update_error(
                "wrong_distribution",
                "An installed update cannot replace a portable copy.",
            ));
        }
        let live_identity = directory_identity(live)?;
        let operation_identity = directory_identity(operation)?;
        if same_path(live, operation) || is_relative_to(live, operation) {
            return Err(update_error(
                "unsafe_staging",
                "Application and update staging must be separate trees.",
            ));
        }
        Ok(Self {
            live: live.to_path_buf(),
            operation: operation.to_path_buf(),
            manifest: manifest.clone(),
            prepared: operation.join("prepared"),
            previous: operation.join("previous"),
            journal: operation.join("replacement.json"),
            live_identity,
            operation_identity,
            old: Vec::new(),
        })
    }

    /// `_directories()`.
    fn directories(&self) -> CodedResult<()> {
        if directory_identity(&self.live)? != self.live_identity
            || directory_identity(&self.operation)? != self.operation_identity
        {
            return Err(folders_changed());
        }
        Ok(())
    }

    /// `_save(phase)`: the replacement receipt with the retained inventory.
    fn save(&self, phase: &str) -> CodedResult<()> {
        self.directories()?;
        let mut row = JsonMap::new();
        row.insert("format_version".into(), 1.into());
        row.insert("phase".into(), phase.into());
        row.insert("version".into(), self.manifest.version.text.clone().into());
        row.insert("old".into(), Json::Array(self.old.iter().map(record_value).collect()));
        atomic_receipt(&self.journal, &Json::Object(row))
    }

    /// `prepare()`: retain a verified previous tree. This never alters the application or its
    /// data.
    pub fn prepare(&mut self) -> CodedResult<()> {
        self.directories()?;
        if self.journal.exists() || self.previous.exists() {
            return Err(update_error("staging_exists", "Previous replacement state needs reconciliation."));
        }
        self.old = inventory(&self.live)?;
        let old_names: HashSet<&str> = self.old.iter().map(|row| row.path.as_str()).collect();
        for row in &self.manifest.files {
            require_file(&self.prepared.join(&row.path), row)?;
            let target = self.live.join(&row.path);
            if !old_names.contains(row.path.as_str()) && present(&target) {
                return Err(update_error(
                    "unowned_file",
                    "An update would overwrite a file outside the application inventory.",
                ));
            }
        }
        let needed: u64 = self.old.iter().map(|row| row.size).sum();
        let free = disk_free(&self.operation).map_err(|error| os_error(&error))?;
        if u128::from(free) < u128::from(needed) + 16 * 1024 * 1024 {
            return Err(update_error("disk_full", "There is not enough space to retain the previous app."));
        }
        std::fs::create_dir(&self.previous).map_err(|error| os_error(&error))?;
        self.save(phase::RETAINING)?;
        for row in &self.old {
            copy_file(&self.live.join(&row.path), &self.previous.join(&row.path), row)?;
        }
        self.save(phase::PREPARED)
    }

    /// `_restore_receipt()`: the retained inventory from the receipt, which must equal the
    /// previous tree's own inventory. Returns the receipt's phase.
    fn restore_receipt(&mut self) -> CodedResult<String> {
        self.directories()?;
        let row = bounded_json(&checked_file(&self.journal, MANIFEST_LIMIT)?, MANIFEST_LIMIT)?;
        let reconcile =
            || update_error("invalid_journal", "The replacement receipt needs manual reconciliation.");
        let Json::Object(row) = row else {
            return Err(reconcile());
        };
        let keys = ["format_version", "phase", "version", "old"];
        let phase = row.get("phase").and_then(Json::as_str).filter(|phase| {
            [
                phase::PREPARED,
                phase::APPLYING,
                phase::AWAITING_RESTART,
                phase::ROLLING_BACK,
                phase::ROLLED_BACK,
                phase::CONFIRMED,
            ]
            .contains(phase)
        });
        let old = row.get("old").and_then(Json::as_array).filter(|old| (3..=4097).contains(&old.len()));
        let format_ok =
            row.get("format_version").is_some_and(|value| value.is_number() && value.as_i64() == Some(1));
        let shaped = row.len() == keys.len() && keys.iter().all(|key| row.contains_key(*key));
        let (true, true, Some(phase), Some(old)) = (
            shaped && format_ok,
            row.get("version") == Some(&Json::String(self.manifest.version.text.clone())),
            phase,
            old,
        ) else {
            return Err(reconcile());
        };
        let mut files = Vec::new();
        for entry in old {
            let invalid =
                || update_error("invalid_journal", "The retained application inventory is invalid.");
            let Some(entry) = entry.as_object() else {
                return Err(invalid());
            };
            let keys_ok =
                entry.len() == 3 && ["path", "size", "sha256"].iter().all(|key| entry.contains_key(*key));
            let size = match entry.get("size") {
                Some(value) if value.is_number() => {
                    value.as_u64().filter(|size| (1..=ARTIFACT_LIMIT).contains(size))
                }
                _ => None,
            };
            let (true, Some(size), Some(sha256)) =
                (keys_ok, size, entry.get("sha256").and_then(lower_hex_64))
            else {
                return Err(invalid());
            };
            files.push(PayloadFile { path: payload_path(&entry["path"])?, size, sha256 });
        }
        let folded: HashSet<String> = files.iter().map(|row| row.path.to_ascii_lowercase()).collect();
        if folded.len() != files.len() {
            return Err(update_error("invalid_journal", "The retained application inventory is ambiguous."));
        }
        self.old = files;
        // The recipe's inventory and hash manifest remain the recovery authority.
        if self.old != inventory(&self.previous)? {
            return Err(update_error(
                "invalid_journal",
                "The retained application does not match its receipt.",
            ));
        }
        Ok(phase.to_string())
    }

    /// `_barrier(processes_exited)`.
    fn barrier(&self, processes_exited: &dyn Fn() -> CodedResult<bool>) -> CodedResult<()> {
        self.directories()?;
        if !processes_exited()? {
            return Err(update_error(
                "application_busy",
                "Close all owned Sylvia processes before applying the update.",
            ));
        }
        Ok(())
    }

    /// `os.replace(source, target)`: rename over an existing target.
    fn replace(source: &Path, target: &Path) -> CodedResult<()> {
        std::fs::rename(source, target).map_err(|error| os_error(&error))
    }

    /// `apply(processes_exited)`: the native helper supplies the process checks.
    pub fn apply(&mut self, processes_exited: &dyn Fn() -> CodedResult<bool>) -> CodedResult<()> {
        self.barrier(processes_exited)?;
        let phase = self.restore_receipt()?;
        if ![phase::PREPARED, phase::APPLYING, phase::AWAITING_RESTART].contains(&phase.as_str()) {
            return Err(update_error(
                "invalid_phase",
                "This replacement cannot be applied in its current state.",
            ));
        }
        let old: BTreeMap<String, PayloadFile> =
            self.old.iter().map(|row| (row.path.clone(), row.clone())).collect();
        let new: HashSet<&str> = self.manifest.files.iter().map(|row| row.path.as_str()).collect();
        self.save(phase::APPLYING)?;
        for row in &self.manifest.files {
            self.barrier(processes_exited)?;
            let target = self.live.join(&row.path);
            ensure_directory(target.parent().unwrap_or(Path::new("")))?;
            if file_matches(&target, row)? {
                continue;
            }
            if let Some(previous) = old.get(&row.path) {
                require_file(&target, previous)?;
            } else if present(&target) {
                return Err(update_error("unowned_file", "A new application path is no longer empty."));
            }
            let source = self.prepared.join(&row.path);
            require_file(&source, row)?;
            directory_identity(target.parent().unwrap_or(Path::new("")))?;
            Self::replace(&source, &target)?;
            require_file(&target, row)?;
        }
        for (name, row) in &old {
            if new.contains(name.as_str()) {
                continue;
            }
            self.barrier(processes_exited)?;
            let target = self.live.join(name);
            let retired = self.operation.join("retired").join(name);
            if !target.exists() && file_matches(&retired, row)? {
                continue;
            }
            require_file(&target, row)?;
            ensure_directory(retired.parent().unwrap_or(Path::new("")))?;
            directory_identity(retired.parent().unwrap_or(Path::new("")))?;
            if present(&retired) {
                return Err(update_error("staging_exists", "An obsolete-file receipt needs reconciliation."));
            }
            Self::replace(&target, &retired)?;
        }
        for row in &self.manifest.files {
            require_file(&self.live.join(&row.path), row)?;
        }
        self.save(phase::AWAITING_RESTART)
    }

    /// `rollback(processes_exited, check_data_compatibility)`: the native checkpoint decision
    /// must admit compatibility or an explicit restore first.
    pub fn rollback(
        &mut self,
        processes_exited: &dyn Fn() -> CodedResult<bool>,
        check_data_compatibility: &dyn Fn() -> CodedResult<()>,
    ) -> CodedResult<()> {
        self.barrier(processes_exited)?;
        let phase = self.restore_receipt()?;
        check_data_compatibility()?;
        let old: BTreeMap<String, PayloadFile> =
            self.old.iter().map(|row| (row.path.clone(), row.clone())).collect();
        let new: BTreeMap<String, PayloadFile> =
            self.manifest.files.iter().map(|row| (row.path.clone(), row.clone())).collect();
        // A completed apply consumed every staged source. An absent new-only file at that
        // point is a later change, not an interrupted move to ignore.
        if [phase::AWAITING_RESTART, phase::CONFIRMED].contains(&phase.as_str()) {
            for (name, row) in self.manifest.files.iter().map(|row| (&row.path, row)) {
                if !old.contains_key(name) {
                    require_file(&self.live.join(name), row)?;
                }
            }
        }
        self.save(phase::ROLLING_BACK)?;
        for row in &self.old {
            self.barrier(processes_exited)?;
            let target = self.live.join(&row.path);
            ensure_directory(target.parent().unwrap_or(Path::new("")))?;
            if file_matches(&target, row)? {
                continue;
            }
            if present(&target) {
                match new.get(&row.path) {
                    Some(updated) => require_file(&target, updated)?,
                    None => {
                        return Err(update_error(
                            "application_changed",
                            "A retained application path changed.",
                        ))
                    }
                }
            }
            let temporary = self.operation.join("restoring").join(uuid4_hex()).join(&row.path);
            copy_file(&self.previous.join(&row.path), &temporary, row)?;
            Self::replace(&temporary, &target)?;
        }
        for row in &self.manifest.files {
            if old.contains_key(&row.path) {
                continue;
            }
            self.barrier(processes_exited)?;
            let target = self.live.join(&row.path);
            let retained = self.operation.join("failed-new").join(&row.path);
            if !present(&target) {
                if file_matches(&retained, row)? {
                    continue;
                }
                // `os.replace` consumed the verified source. Its exact remaining bytes prove
                // this new-only path was never installed during a partial apply.
                if file_matches(&self.prepared.join(&row.path), row)? {
                    continue;
                }
            }
            require_file(&target, row)?;
            ensure_directory(retained.parent().unwrap_or(Path::new("")))?;
            directory_identity(retained.parent().unwrap_or(Path::new("")))?;
            Self::replace(&target, &retained)?;
        }
        for row in &self.old {
            require_file(&self.live.join(&row.path), row)?;
        }
        self.save(phase::ROLLED_BACK)
    }

    /// `confirm()`: record a separately verified startup; no application or user file changes.
    pub fn confirm(&mut self) -> CodedResult<()> {
        let phase = self.restore_receipt()?;
        if ![phase::AWAITING_RESTART, phase::CONFIRMED].contains(&phase.as_str()) {
            return Err(update_error(
                "invalid_phase",
                "This replacement has not reached restart confirmation.",
            ));
        }
        self.save(phase::CONFIRMED)
    }

    /// `verify_rollback()`: the retained old inventory is live again and no new-only file is.
    pub fn verify_rollback(&mut self) -> CodedResult<()> {
        if self.restore_receipt()? != phase::ROLLED_BACK {
            return Err(update_error("recovery_incomplete", "Application recovery has not finished."));
        }
        let old: HashSet<String> = self.old.iter().map(|row| row.path.clone()).collect();
        for row in &self.old {
            require_file(&self.live.join(&row.path), row)?;
        }
        for row in &self.manifest.files {
            if !old.contains(&row.path) && present(&self.live.join(&row.path)) {
                return Err(update_error("application_changed", "A recovered application path changed."));
            }
        }
        Ok(())
    }

    /// `helper_payload()`: the retained copy of the current owner executable, which runs the
    /// helper.
    pub fn helper_payload(&mut self) -> CodedResult<(PathBuf, PayloadFile)> {
        if self.old.is_empty() {
            self.restore_receipt()?;
        }
        self.directories()?;
        let row = self.old.iter().find(|row| row.path == "sylvia-gm-owner.exe").cloned();
        let Some(row) = row else {
            return Err(update_error("invalid_installation", "The retained helper is missing."));
        };
        Ok((self.previous.join(&row.path), row))
    }
}
