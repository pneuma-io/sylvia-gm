//! `application/desktop_update/preparation.py`: one verified download, an immutable copy of
//! the application and a coherent data checkpoint, bound to one preparation attempt.
//!
//! Both distributions are complete: a portable copy replaces its files itself
//! (`replacement.py`), an installed copy runs its retained, verified NSIS installers
//! (`installed.py`).

use super::checkpoint::{acquire_update_data_lock, DataCheckpoint, STORAGE_COMPATIBILITY};
use super::installed::{run_nsis, InstalledReplacement, Installer, Registration, WindowsRegistration};
use super::manifest::{
    bounded_json, parse_current_recovery_manifest, parse_manifest, Context, KeyRing, Manifest, PayloadFile,
    Version, MANIFEST_LIMIT,
};
use super::payload::{extract_portable, verify_artifact};
use super::replacement::{copy_file, file_matches, PortableReplacement};
use super::restart::RestartHandshake;
use super::storage::{
    atomic_receipt, checked_file, directory_identity, operation_id_text, os_error, phase, same_path,
    Identity, UpdateStore,
};
use super::update_error;
use crate::util::uuid4_hex;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use sylvia_base::{CodedResult, Json, JsonMap};

/// The phases in which a prepared attempt is still the selected one.
const SELECTED_PHASES: [&str; 10] = [
    phase::PREPARED,
    phase::CLOSING,
    phase::APPLYING,
    phase::AWAITING_RESTART,
    phase::RECOVERY_REQUIRED,
    phase::ROLLING_BACK,
    phase::ROLLBACK_READY,
    phase::ROLLBACK_AWAITING_RESTART,
    phase::ROLLED_BACK,
    phase::CONFIRMED,
];

/// `new_attempt()`.
pub fn new_attempt() -> String {
    uuid4_hex()
}

/// The replacement a prepared update applies: Python's `PortableReplacement` or
/// `InstalledReplacement`.
#[derive(Debug, Clone)]
pub enum Replacement {
    Portable(Box<PortableReplacement>),
    Installed(Box<InstalledReplacement>),
}

/// The registration an installed copy is checked against and the installer it runs:
/// `registration=` and `run_installer=` in Python, the real ones unless a test gives others.
#[derive(Clone)]
pub struct InstalledHooks {
    pub registration: Arc<dyn Registration>,
    pub run: Installer,
}

impl Default for InstalledHooks {
    fn default() -> Self {
        Self { registration: Arc::new(WindowsRegistration), run: Arc::new(run_nsis) }
    }
}

impl std::fmt::Debug for InstalledHooks {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("InstalledHooks")
    }
}

impl Replacement {
    pub fn prepare(&mut self) -> CodedResult<()> {
        match self {
            Self::Portable(replacement) => replacement.prepare(),
            Self::Installed(replacement) => replacement.prepare(),
        }
    }

    pub fn apply(&mut self, processes_exited: &dyn Fn() -> CodedResult<bool>) -> CodedResult<()> {
        match self {
            Self::Portable(replacement) => replacement.apply(processes_exited),
            Self::Installed(replacement) => replacement.apply(processes_exited),
        }
    }

    pub fn rollback(
        &mut self,
        processes_exited: &dyn Fn() -> CodedResult<bool>,
        compatibility: &dyn Fn() -> CodedResult<()>,
    ) -> CodedResult<()> {
        match self {
            Self::Portable(replacement) => replacement.rollback(processes_exited, compatibility),
            Self::Installed(replacement) => replacement.rollback(processes_exited, compatibility),
        }
    }

    pub fn confirm(&mut self) -> CodedResult<()> {
        match self {
            Self::Portable(replacement) => replacement.confirm(),
            Self::Installed(replacement) => replacement.confirm(),
        }
    }

    pub fn verify_rollback(&mut self) -> CodedResult<()> {
        match self {
            Self::Portable(replacement) => replacement.verify_rollback(),
            Self::Installed(replacement) => replacement.verify_rollback(),
        }
    }

    pub fn helper_payload(&mut self) -> CodedResult<(PathBuf, PayloadFile)> {
        match self {
            Self::Portable(replacement) => replacement.helper_payload(),
            Self::Installed(replacement) => replacement.helper_payload(),
        }
    }
}

/// `PreparedUpdate`.
#[derive(Debug, Clone)]
pub struct PreparedUpdate {
    pub root: PathBuf,
    pub live: PathBuf,
    pub identity: String,
    pub attempt: String,
    pub base: PathBuf,
    pub stage: PathBuf,
    pub current: Version,
    pub target: String,
    pub distribution: String,
    pub store: UpdateStore,
    pub manifest: Manifest,
    pub previous: Option<Manifest>,
    root_identity: Identity,
    live_identity: Identity,
    installed: InstalledHooks,
    replacement: Option<Replacement>,
}

fn identity_value(identity: Identity) -> Json {
    Json::Array(vec![identity.0.into(), identity.1.into()])
}

impl PreparedUpdate {
    /// `PreparedUpdate(data_root, live, identity, attempt, current=, target=, distribution=,
    /// verify=)`: the signed download and its retained artifacts, verified again.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        data_root: &Path,
        live: &Path,
        identity: &str,
        attempt: &str,
        current: &Version,
        target: &str,
        distribution: &str,
        keyring: &KeyRing,
    ) -> CodedResult<Self> {
        let identity = operation_id_text(identity)?;
        let attempt = operation_id_text(attempt)?;
        let base = data_root.join(".application-updates").join(&identity);
        let stage = base.join("attempts").join(&attempt);
        let root_identity = directory_identity(data_root)?;
        let live_identity = directory_identity(live)?;
        directory_identity(&base)?;
        let store = UpdateStore::new(&data_root.join(".application-updates"))?;
        let context = Context { current, target, distribution, keyring };
        let manifest = parse_manifest(&checked_file(&base.join("manifest.json"), MANIFEST_LIMIT)?, context)?;
        verify_artifact(&base.join("artifact.verified"), &manifest)?;
        let previous = match distribution {
            "portable" => {
                if !same_path(data_root, &live.join("isolated-data")) {
                    return Err(update_error(
                        "wrong_data_root",
                        "Portable updates preserve adjacent isolated-data.",
                    ));
                }
                None
            }
            "installed" => {
                let previous = parse_current_recovery_manifest(
                    &checked_file(&base.join("previous-manifest.json"), MANIFEST_LIMIT)?,
                    context,
                )?;
                verify_artifact(&base.join("previous-installer/artifact.verified"), &previous)?;
                Some(previous)
            }
            _ => return Err(update_error("wrong_distribution", "This update distribution is unsupported.")),
        };
        Ok(Self {
            root: data_root.to_path_buf(),
            live: live.to_path_buf(),
            identity,
            attempt,
            base,
            stage,
            current: current.clone(),
            target: target.to_string(),
            distribution: distribution.to_string(),
            store,
            manifest,
            previous,
            root_identity,
            live_identity,
            installed: InstalledHooks::default(),
            replacement: None,
        })
    }

    /// The registration and installer an installed copy uses instead of the real ones.
    pub fn with_installed(mut self, installed: InstalledHooks) -> Self {
        self.installed = installed;
        self.replacement = None;
        self
    }

    /// `_check()`.
    fn check(&self) -> CodedResult<()> {
        if directory_identity(&self.root)? != self.root_identity
            || directory_identity(&self.live)? != self.live_identity
        {
            return Err(update_error(
                "changed_update_root",
                "The selected application or data folder changed.",
            ));
        }
        Ok(())
    }

    /// `replacement()`: made once, then reused.
    pub fn replacement(&mut self) -> CodedResult<&mut Replacement> {
        self.check()?;
        if self.replacement.is_none() {
            let replacement = match &self.previous {
                None => Replacement::Portable(Box::new(PortableReplacement::new(
                    &self.live,
                    &self.stage,
                    &self.manifest,
                )?)),
                Some(previous) => Replacement::Installed(Box::new(InstalledReplacement::new(
                    &self.live,
                    &self.stage,
                    &self.manifest,
                    previous,
                    self.installed.registration.clone(),
                    self.installed.run.clone(),
                    Some(&self.base),
                )?)),
            };
            self.replacement = Some(replacement);
        }
        Ok(self.replacement.as_mut().expect("made above"))
    }

    /// `_selected(phases)`: this operation is still the selected one, in one of `phases`.
    fn selected(&self, phases: &[&str]) -> CodedResult<()> {
        self.check()?;
        let row = self.store.load()?;
        let selected = row.is_some_and(|row| {
            row.operation_id == self.identity
                && phases.contains(&row.phase.as_str())
                && row.version.as_deref() == Some(self.manifest.version.text.as_str())
        });
        if !selected {
            return Err(update_error("stale_operation", "This prepared update is no longer selected."));
        }
        Ok(())
    }

    /// `_checkpoint_binding()`: the checkpoint receipt's size and hash, and its contract.
    pub fn checkpoint_binding(&self) -> CodedResult<JsonMap> {
        let raw = checked_file(&self.stage.join("data-checkpoint/checkpoint.json"), 16 * 1024)?;
        let row = bounded_json(&raw, 16 * 1024)?;
        let compatibility = row
            .get("storage_compatibility")
            .filter(|value| value.is_number())
            .and_then(Json::as_i64)
            .filter(|value| 0 < *value && *value < 1 << 31);
        let Some(compatibility) = compatibility.filter(|_| row.is_object()) else {
            return Err(update_error("invalid_checkpoint", "The retained storage contract is invalid."));
        };
        let mut binding = JsonMap::new();
        binding.insert("size".into(), raw.len().into());
        binding.insert("sha256".into(), sylvia_base::hash::sha256_hex(&raw).into());
        binding.insert("storage_compatibility".into(), compatibility.into());
        Ok(binding)
    }

    /// The binding `prepared-update.json` records.
    fn binding(&mut self) -> CodedResult<JsonMap> {
        let (_, helper) = self.replacement()?.helper_payload()?;
        let mut helper_row = JsonMap::new();
        helper_row.insert("size".into(), helper.size.into());
        helper_row.insert("sha256".into(), helper.sha256.into());
        let mut value = JsonMap::new();
        value.insert("format_version".into(), 2.into());
        value.insert("operation_id".into(), self.identity.clone().into());
        value.insert("attempt".into(), self.attempt.clone().into());
        value.insert("current_version".into(), self.current.text.clone().into());
        value.insert("version".into(), self.manifest.version.text.clone().into());
        value.insert("target".into(), self.target.clone().into());
        value.insert("distribution".into(), self.distribution.clone().into());
        value.insert("data_identity".into(), identity_value(self.root_identity));
        value.insert("app_identity".into(), identity_value(self.live_identity));
        value.insert("helper".into(), Json::Object(helper_row));
        value.insert("checkpoint".into(), Json::Object(self.checkpoint_binding()?));
        Ok(value)
    }

    /// `prepare(backup_database, quiesced)`: only while the native parent holds the owner and
    /// worker admission reservation. A failure keeps the attempt and moves back to verified.
    pub fn prepare(
        &mut self,
        backup_database: &dyn Fn(&Path) -> CodedResult<()>,
        quiesced: &dyn Fn() -> bool,
    ) -> CodedResult<()> {
        self.selected(&[phase::VERIFIED, phase::DEFERRED, phase::PREPARING])?;
        if !quiesced() {
            return Err(update_error("application_busy", "Finish admitted work before preparing an update."));
        }
        let version = self.manifest.version.text.clone();
        self.store.save(&self.identity, phase::PREPARING, Some(&version), None)?;
        match self.prepare_attempt(backup_database, quiesced) {
            Ok(()) => Ok(()),
            Err(error) => {
                // No application or data file was replaced. Keep the failed attempt; an explicit
                // retry takes a fresh checkpoint and never reuses a stale saved database.
                self.store.save(&self.identity, phase::VERIFIED, Some(&version), None)?;
                Err(error)
            }
        }
    }

    fn prepare_attempt(
        &mut self,
        backup_database: &dyn Fn(&Path) -> CodedResult<()>,
        quiesced: &dyn Fn() -> bool,
    ) -> CodedResult<()> {
        let attempts = self.stage.parent().unwrap_or(Path::new("")).to_path_buf();
        match std::fs::create_dir(&attempts) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists && attempts.is_dir() => {}
            Err(error) => return Err(os_error(&error)),
        }
        directory_identity(&attempts)?;
        // Every retry uses a fresh attempt; earlier checkpoints stay retained.
        std::fs::create_dir(&self.stage).map_err(|error| os_error(&error))?;
        directory_identity(&self.stage)?;
        if self.distribution == "portable" {
            extract_portable(
                &self.base.join("artifact.verified"),
                &self.manifest,
                &self.stage.join("prepared"),
            )?;
        }
        self.replacement()?.prepare()?;
        let (source, helper) = self.replacement()?.helper_payload()?;
        copy_file(&source, &self.stage.join("helper/sylvia-gm-owner.exe"), &helper)?;
        let checkpoint = DataCheckpoint::new(&self.root.join("authority.sqlite3"), &self.stage)?;
        checkpoint.prepare(backup_database, quiesced, &self.current.text)?;
        if !quiesced() {
            return Err(update_error("application_busy", "New work prevents a safe update close."));
        }
        self.check()?;
        let binding = self.binding()?;
        atomic_receipt(&self.stage.join("prepared-update.json"), &Json::Object(binding))?;
        let mut selection = JsonMap::new();
        selection.insert("format_version".into(), 1.into());
        selection.insert("attempt".into(), self.attempt.clone().into());
        atomic_receipt(&self.base.join("selected-attempt.json"), &Json::Object(selection))?;
        let version = self.manifest.version.text.clone();
        self.store.save(&self.identity, phase::PREPARED, Some(&version), None)
    }

    /// `require_selected(checkpoint_compatibility=)`: this attempt is the selected one, and its
    /// binding, retained helper and checkpoint are unchanged.
    pub fn require_selected(&mut self, checkpoint_compatibility: Option<i64>) -> CodedResult<()> {
        self.selected(&SELECTED_PHASES)?;
        let selection =
            bounded_json(&checked_file(&self.base.join("selected-attempt.json"), 4096)?, MANIFEST_LIMIT)?;
        let mut expected_selection = JsonMap::new();
        expected_selection.insert("format_version".into(), 1.into());
        expected_selection.insert("attempt".into(), self.attempt.clone().into());
        if selection != Json::Object(expected_selection) || !selection["format_version"].is_number() {
            return Err(update_error("stale_attempt", "Another update preparation superseded this attempt."));
        }
        let row =
            bounded_json(&checked_file(&self.stage.join("prepared-update.json"), 8192)?, MANIFEST_LIMIT)?;
        let expected = self.binding()?;
        let integers = |value: &Json| {
            value.as_array().is_some_and(|parts| parts.iter().all(|part| part.is_number() && part.is_u64()))
        };
        if row != Json::Object(expected)
            || !row["format_version"].is_number()
            || !integers(&row["data_identity"])
            || !integers(&row["app_identity"])
        {
            return Err(update_error(
                "changed_preparation",
                "The prepared application/data context changed.",
            ));
        }
        let (_, helper) = self.replacement()?.helper_payload()?;
        if !file_matches(&self.stage.join("helper/sylvia-gm-owner.exe"), &helper)? {
            return Err(update_error("changed_helper", "The retained update helper changed."));
        }
        let checkpoint = DataCheckpoint::new(&self.root.join("authority.sqlite3"), &self.stage)?;
        // A new owner checks the retained old checkpoint's bytes before migration; an old
        // helper's rollback uses its own compiled contract by default.
        checkpoint.load(checkpoint_compatibility.unwrap_or(STORAGE_COMPATIBILITY))?;
        Ok(())
    }

    /// `apply(processes_exited)`: under the data lock, after every owned process exited.
    pub fn apply(&mut self, processes_exited: &dyn Fn() -> CodedResult<bool>) -> CodedResult<()> {
        self.require_selected(None)?;
        let row = self.store.load()?;
        if !row.is_some_and(|row| {
            [phase::PREPARED, phase::CLOSING, phase::APPLYING, phase::AWAITING_RESTART]
                .contains(&row.phase.as_str())
        }) {
            return Err(update_error("invalid_phase", "This update cannot be applied in its current state."));
        }
        if !processes_exited()? {
            return Err(update_error("application_busy", "Owned app processes have not all exited."));
        }
        let mut lock = acquire_update_data_lock(&self.root.join("authority.sqlite3"))?;
        let outcome = (|| -> CodedResult<()> {
            self.require_selected(None)?;
            let version = self.manifest.version.text.clone();
            self.store.save(&self.identity, phase::APPLYING, Some(&version), None)?;
            self.replacement()?.apply(processes_exited)?;
            self.store.save(&self.identity, phase::AWAITING_RESTART, Some(&version), None)
        })();
        lock.close();
        outcome
    }

    /// `create_restart(owner_identity)`: one restart intent for the updated owner the helper
    /// probed.
    pub fn create_restart(&mut self, owner_identity: &Json) -> CodedResult<RestartHandshake> {
        self.require_selected(None)?;
        let keys = ["format_version", "product", "component", "version", "storage_compatibility", "target"];
        let valid = owner_identity.as_object().is_some_and(|identity| {
            let integer =
                |key: &str| identity.get(key).filter(|value| value.is_number()).and_then(Json::as_i64);
            identity.len() == keys.len()
                && keys.iter().all(|key| identity.contains_key(*key))
                && integer("format_version") == Some(1)
                && identity["product"] == "Sylvia GM"
                && identity["component"] == "owner"
                && identity["version"] == self.manifest.version.text.as_str()
                && identity["target"] == self.target.as_str()
                && integer("storage_compatibility").is_some_and(|value| value >= 1)
        });
        if !valid {
            return Err(update_error(
                "wrong_owner_identity",
                "The updated sidecar identity is inconsistent.",
            ));
        }
        let row = self.store.load()?;
        if !row.is_some_and(|row| row.phase == phase::AWAITING_RESTART) {
            return Err(update_error("invalid_phase", "Apply must finish before restarting this copy."));
        }
        let handshake = RestartHandshake::new(&self.root, &self.identity, &self.attempt, false)?;
        handshake.create(
            &self.live,
            &self.manifest.version,
            &self.target,
            &self.distribution,
            &owner_identity["storage_compatibility"],
        )?;
        Ok(handshake)
    }

    /// `confirm(checkpoint_compatibility=)`: the restarted app acknowledged its data.
    pub fn confirm(&mut self, checkpoint_compatibility: Option<i64>) -> CodedResult<()> {
        self.require_selected(checkpoint_compatibility)?;
        let row = self.store.load()?;
        if !row.is_some_and(|row| [phase::AWAITING_RESTART, phase::CONFIRMED].contains(&row.phase.as_str())) {
            return Err(update_error("invalid_phase", "The update is no longer awaiting confirmation."));
        }
        if !RestartHandshake::new(&self.root, &self.identity, &self.attempt, false)?.confirmed()? {
            return Err(update_error("startup_unconfirmed", "The updated app has not confirmed its data."));
        }
        self.replacement()?.confirm()?;
        let version = self.manifest.version.text.clone();
        self.store.save(&self.identity, phase::CONFIRMED, Some(&version), None)
    }
}
