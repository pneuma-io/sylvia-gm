//! `application/desktop_update/startup.py`: an update restart is authorized before migration,
//! from update-owned bytes only. No owner acquisition, SQL migration, apply or launch here.
//!
//! All of it is ported: the launch-envelope reading (`owner_startup_context`), the ordinary
//! start (refused when a retained update was interrupted mid-apply), and an update or
//! recovery restart, authorized against its retained preparation and restart intent and
//! later confirmed (`AuthorizedStartup.confirm`).

use super::checkpoint::STORAGE_COMPATIBILITY;
use super::launch::Installation;
use super::manifest::{bounded_json, KeyRing, Version, APP_VERSION, MANIFEST_LIMIT};
use super::preparation::PreparedUpdate;
use super::replacement::file_matches;
use super::restart::{RestartContext, RestartHandshake};
use super::service::public_keys;
use super::storage::{checked_file, phase, present, same_path, UpdateStore};
use super::update_error;
use std::path::{Path, PathBuf};
use sylvia_base::{CodedResult, Json, JsonMap};

/// `_UNSAFE_ORDINARY_START`: phases in which saved data must not be opened normally.
pub const UNSAFE_ORDINARY_START: [&str; 7] = [
    phase::CLOSING,
    phase::APPLYING,
    phase::AWAITING_RESTART,
    phase::RECOVERY_REQUIRED,
    phase::ROLLING_BACK,
    phase::ROLLBACK_READY,
    phase::ROLLBACK_AWAITING_RESTART,
];

/// `AuthorizedStartup`: the update (or recovery) restart this owner was started to confirm.
#[derive(Debug, Clone)]
pub struct AuthorizedStartup {
    pub prepared: PreparedUpdate,
    pub handshake: RestartHandshake,
    pub nonce: String,
    pub installation: Installation,
    pub version: String,
    pub desktop_version: String,
    pub compatibility: i64,
    pub retained_compatibility: i64,
    pub recovery: bool,
    pub completed: bool,
}

impl AuthorizedStartup {
    /// `_context()`.
    fn context(&self) -> RestartContext<'_> {
        RestartContext {
            nonce: &self.nonce,
            live: &self.installation.live,
            desktop_version: &self.desktop_version,
            owner_version: &self.version,
            target: &self.installation.target,
            distribution: &self.installation.distribution,
            storage_compatibility: self.compatibility,
        }
    }

    /// `confirm()`: the real new owner calls this only once the native app reports a usable
    /// start. Only the helper finalizes the operation; until it does, admission stays closed,
    /// so a late helper write cannot overtake the player's next check.
    pub fn confirm(&mut self) -> CodedResult<bool> {
        self.installation.check(&self.installation.target, &self.installation.distribution)?;
        if self.completed {
            return Ok(true);
        }
        self.prepared.require_selected(Some(self.retained_compatibility))?;
        let context = self.context();
        self.handshake.confirm(&context)?;
        let row = self.prepared.store.load()?;
        let Some(row) = row.filter(|row| row.operation_id == self.prepared.identity) else {
            return Err(update_error("changed_restart", "The startup operation changed before completion."));
        };
        let finished = if self.recovery { phase::ROLLED_BACK } else { phase::CONFIRMED };
        if row.phase == finished {
            self.completed = true;
            return Ok(true);
        }
        let error = self.handshake.error_receipt()?;
        if present(&error) {
            checked_file(&error, 4096)?;
            return Err(update_error(
                "update_completion_failed",
                "The update helper could not finish. Use recovery.",
            ));
        }
        Ok(false)
    }
}

/// `authorize_startup(data, installation, request, running=, desktop_version=, ...)`.
pub fn authorize_startup(
    data: &Path,
    installation: Option<&Installation>,
    request: Option<&Json>,
    running: &Version,
    desktop_version: &str,
    storage_compatibility: i64,
    keyring: &KeyRing,
) -> CodedResult<Option<AuthorizedStartup>> {
    let update_root = data.join(".application-updates");
    let journal = update_root.join("current.json");
    let request = request.filter(|request| !request.is_null());
    if !present(&journal) {
        if request.is_some() {
            return Err(update_error("invalid_restart", "This update restart has no retained operation."));
        }
        return Ok(None);
    }
    let row = UpdateStore::new(&update_root)?.load()?.expect("the receipt exists");
    let Some(request) = request else {
        if UNSAFE_ORDINARY_START.contains(&row.phase.as_str()) {
            return Err(update_error(
                "update_recovery_required",
                "Reconcile the interrupted application update before opening saved data.",
            ));
        }
        return Ok(None);
    };
    let recovery = request.get("recovery") == Some(&Json::Bool(true));
    let mut request_keys = vec!["operation_id", "attempt", "nonce"];
    if recovery {
        request_keys.push("recovery");
    }
    let permitted: &[&str] = if recovery {
        &[phase::ROLLBACK_AWAITING_RESTART, phase::ROLLED_BACK]
    } else {
        &[phase::AWAITING_RESTART, phase::RECOVERY_REQUIRED, phase::CONFIRMED]
    };
    let shaped = request.as_object().is_some_and(|object| {
        object.len() == request_keys.len() && request_keys.iter().all(|key| object.contains_key(*key))
    });
    let installation = match installation {
        Some(installation)
            if shaped && permitted.contains(&row.phase.as_str()) && same_path(&installation.data, data) =>
        {
            installation
        }
        _ => {
            return Err(update_error("invalid_restart", "The native update restart context is unavailable."))
        }
    };
    let object = request.as_object().expect("checked above");
    let mut identities = Vec::new();
    for name in ["operation_id", "attempt", "nonce"] {
        identities.push(super::storage::operation_id(&object[name])?);
    }
    if identities[0] != row.operation_id || desktop_version != running.text {
        return Err(update_error("wrong_restart", "The selected app and update operation do not match."));
    }
    installation.check(&installation.target, &installation.distribution)?;
    let (identity, attempt, nonce) = (&identities[0], &identities[1], &identities[2]);
    let stage = update_root.join(identity).join("attempts").join(attempt);
    let binding = bounded_json(&checked_file(&stage.join("prepared-update.json"), 8192)?, MANIFEST_LIMIT)?;
    let Some(checkpoint) =
        binding.as_object().and_then(|binding| binding.get("checkpoint")).and_then(Json::as_object)
    else {
        return Err(update_error("invalid_restart", "The retained restart preparation is incomplete."));
    };
    let old = Version::parse(binding.get("current_version").unwrap_or(&Json::Null))?;
    let old_compatibility = checkpoint
        .get("storage_compatibility")
        .filter(|value| value.is_number())
        .and_then(Json::as_i64)
        .filter(|value| 0 < *value && *value < 1 << 31);
    let Some(old_compatibility) = old_compatibility else {
        return Err(update_error("invalid_checkpoint", "The retained storage contract is invalid."));
    };
    let mut prepared = PreparedUpdate::new(
        data,
        &installation.live,
        identity,
        attempt,
        &old,
        &installation.target,
        &installation.distribution,
        keyring,
    )?;
    prepared.require_selected(Some(old_compatibility))?;
    let expected_version = if recovery { old.text.clone() } else { prepared.manifest.version.text.clone() };
    if expected_version != running.text
        || row.version.as_deref() != Some(prepared.manifest.version.text.as_str())
    {
        return Err(update_error("wrong_restart", "The running owner is not the signed update version."));
    }
    if recovery {
        prepared.replacement()?.verify_rollback()?;
        if storage_compatibility != old_compatibility {
            return Err(update_error("wrong_restart", "The restored owner's data contract is inconsistent."));
        }
    } else {
        for payload in &prepared.manifest.files {
            if !file_matches(&installation.live.join(&payload.path), payload)? {
                return Err(update_error("application_changed", "The updated application is incomplete."));
            }
        }
    }
    let handshake = RestartHandshake::new(data, identity, attempt, recovery)?;
    let result = AuthorizedStartup {
        prepared,
        handshake,
        nonce: nonce.clone(),
        installation: installation.clone(),
        version: running.text.clone(),
        desktop_version: desktop_version.to_string(),
        compatibility: storage_compatibility,
        retained_compatibility: old_compatibility,
        recovery,
        completed: false,
    };
    result.handshake.authorize(&result.context())?;
    Ok(Some(result))
}

/// What the launch envelope says about updates: the installation context, when the native
/// shell supplied a valid one, and the restart this owner was started to confirm.
pub type StartupContext = (Option<Installation>, Option<AuthorizedStartup>);

/// `owner_startup_context(launch)`: only the private native launch envelope supplies the
/// application identity and restart arguments. `resolve` is `Path(text).expanduser().resolve()`.
pub fn owner_startup_context(
    launch: &JsonMap,
    resolve: impl Fn(&Path) -> Option<PathBuf>,
) -> CodedResult<StartupContext> {
    let unavailable = || update_error("invalid_restart", "The selected authority database is unavailable.");
    let raw = launch
        .get("database_path")
        .and_then(Json::as_str)
        .filter(|raw| !raw.is_empty())
        .ok_or_else(unavailable)?;
    let database = resolve(Path::new(raw)).ok_or_else(unavailable)?;
    let data = database.parent().map(Path::to_path_buf).unwrap_or_default();
    // No update capability; an ordinary launch can still play safely.
    let installation = Installation::from_launch(launch.get("desktop_update_context"), &data).unwrap_or(None);
    let running = Version::parse_text(APP_VERSION).expect("the build version is SemVer");
    let start = authorize_startup(
        &data,
        installation.as_ref(),
        launch.get("desktop_update_start"),
        &running,
        APP_VERSION,
        STORAGE_COMPATIBILITY,
        &KeyRing::new(public_keys()),
    )?;
    Ok((installation, start))
}
