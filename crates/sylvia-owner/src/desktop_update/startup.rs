//! `application/desktop_update/startup.py`: an update restart is authorized before migration,
//! from update-owned bytes only. No owner acquisition, SQL migration, apply or launch here.
//!
//! Ported: the launch-envelope reading (`owner_startup_context`) and every refusal of
//! `authorize_startup` that comes before update preparation. An ordinary start (no restart
//! request) is complete: it refuses when a retained update was interrupted mid-apply. A
//! restart request that reaches the retained preparation needs `preparation.py` and answers
//! [`super::service::PREPARATION_PENDING`] until it is ported.

use super::launch::Installation;
use super::manifest::{Version, APP_VERSION};
use super::storage::{phase, present, same_path, UpdateStore};
use super::update_error;
use std::path::{Path, PathBuf};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

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

/// `AuthorizedStartup`: an update restart this owner was started to confirm. Its
/// confirmation needs update preparation, which is not ported yet, so no value of this type
/// is made yet.
#[derive(Debug, Clone, PartialEq)]
pub struct AuthorizedStartup {
    pub version: String,
    pub installation: Installation,
}

/// `authorize_startup(data, installation, request, running=, desktop_version=, ...)`.
pub fn authorize_startup(
    data: &Path,
    installation: Option<&Installation>,
    request: Option<&Json>,
    running: &Version,
    desktop_version: &str,
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
    Err(CodedError::new(
        super::service::PREPARATION_PENDING,
        "The Rust owner does not confirm update restarts yet.",
    ))
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
    )?;
    Ok((installation, start))
}
