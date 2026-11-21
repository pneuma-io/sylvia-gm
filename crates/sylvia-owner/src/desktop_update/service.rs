//! `application/desktop_update/service.py`: the owner's lazy boundary for the desktop's fixed
//! updater operations.
//!
//! The 0.4.1 build pins no public key and supports only `windows-x86_64`, so every action but
//! `state` is refused (`trust_not_configured` or `unsupported_target`) and `state` returns the
//! `unavailable` view. That is the shipped behaviour and the `desktop-update` session checks
//! it. The paths behind a pinned key are reached only through [`DesktopUpdateService::with_trust`],
//! which the vector tests use the way Python's tests patch `PUBLIC_KEYS`.
//!
//! Not ported yet: the `prepare` and `handoff` actions, `abort_preparation`'s and
//! `arm_shutdown`'s checks of a prepared update, which need update preparation
//! (`preparation.py`). They answer [`PREPARATION_PENDING`].

use super::controller::{Setup, UpdateController, SOURCE};
use super::launch::Installation;
use super::manifest::{KeyRing, Version, APP_VERSION, RELEASE_CHANNEL};
use super::preparation::{new_attempt, PreparedUpdate};
use super::storage::{is_update_error, operation_id, phase};
use super::transport::Transport;
use super::update_error;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, MutexGuard};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

/// `PUBLIC_KEYS`: the release bootstrap pins approved public keys here. Trust or private keys
/// are never loaded from IPC, the environment, downloaded metadata or a chosen file. Empty in
/// this build.
pub fn public_keys() -> BTreeMap<String, [u8; 32]> {
    BTreeMap::new()
}

/// The code a not-yet-ported preparation path answers with.
pub const PREPARATION_PENDING: &str = "rust_port_pending";

/// Makes the transport a restored controller uses (Python constructs `HttpsTransport()`).
pub type TransportFactory = Arc<dyn Fn() -> Arc<dyn Transport> + Send + Sync>;

/// `reserved(identity)` and `release(identity)` from the run loop.
pub type Reserved = Arc<dyn Fn(&str) -> bool + Send + Sync>;
pub type Release = Arc<dyn Fn(&str) -> CodedResult<()> + Send + Sync>;
/// `backup_database(target)`: the owning authority's coherent online backup.
pub type Backup = Arc<dyn Fn(&Path) -> CodedResult<()> + Send + Sync>;

/// A transport for a build without update trust: it is never asked for anything, because
/// every request is refused before a controller exists.
struct NoTransport;

impl Transport for NoTransport {
    fn open<'a>(
        &'a self,
        _url: &str,
        _cancel: &super::transport::Cancel,
    ) -> CodedResult<Box<dyn super::transport::Response + 'a>> {
        Err(update_error("network_unavailable", "The update connection failed. You can retry safely."))
    }
}

struct Restore {
    worker: Option<tokio::task::JoinHandle<()>>,
    error: Option<CodedError>,
}

struct Inner {
    controller: Option<UpdateController>,
    context: Option<(String, String)>,
    restore: Restore,
    closed: bool,
    generation: u64,
}

/// `DesktopUpdateService`.
pub struct DesktopUpdateService {
    storage: PathBuf,
    root: PathBuf,
    installation: Option<Installation>,
    backup: Option<Backup>,
    reserved: Option<Reserved>,
    release: Option<Release>,
    keys: BTreeMap<String, [u8; 32]>,
    transport: TransportFactory,
    inner: Arc<Mutex<Inner>>,
}

fn lock(inner: &Mutex<Inner>) -> MutexGuard<'_, Inner> {
    inner.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
}

fn invalid_request() -> CodedError {
    update_error("invalid_update_request", "This desktop update request is unsupported.")
}

impl DesktopUpdateService {
    /// `DesktopUpdateService(storage_root, installation=, backup_database=, reserved=, release=)`.
    pub fn new(
        storage_root: &Path,
        installation: Option<Installation>,
        backup: Option<Backup>,
        reserved: Option<Reserved>,
        release: Option<Release>,
    ) -> Self {
        Self::with_trust(
            storage_root,
            installation,
            backup,
            reserved,
            release,
            public_keys(),
            Arc::new(|| Arc::new(NoTransport)),
        )
    }

    /// The same service with pinned keys and a transport of the caller's. Only the vector tests
    /// call this; the product always uses [`DesktopUpdateService::new`].
    #[doc(hidden)]
    pub fn with_trust(
        storage_root: &Path,
        installation: Option<Installation>,
        backup: Option<Backup>,
        reserved: Option<Reserved>,
        release: Option<Release>,
        keys: BTreeMap<String, [u8; 32]>,
        transport: TransportFactory,
    ) -> Self {
        Self {
            storage: storage_root.to_path_buf(),
            root: storage_root.join(".application-updates"),
            installation,
            backup,
            reserved,
            release,
            keys,
            transport,
            inner: Arc::new(Mutex::new(Inner {
                controller: None,
                context: None,
                restore: Restore { worker: None, error: None },
                closed: false,
                generation: 0,
            })),
        }
    }

    /// The data folder the updater serves.
    pub fn storage_root(&self) -> &Path {
        &self.storage
    }

    /// `_restore(target, distribution, keyring)`: build the controller in the background, so
    /// reading a retained update (which may hash a large download) never blocks the owner.
    fn restore(&self, inner: &mut Inner, target: &str, distribution: &str, keyring: KeyRing) {
        let generation = inner.generation;
        inner.restore.error = None;
        let shared = Arc::clone(&self.inner);
        let root = self.root.clone();
        let setup = Setup {
            current: Version::parse_text(APP_VERSION).expect("the build version is SemVer"),
            target: target.to_string(),
            distribution: distribution.to_string(),
            transport: (self.transport)(),
            keyring,
        };
        let Ok(handle) = tokio::runtime::Handle::try_current() else {
            inner.restore.error = Some(update_error(
                "worker_unavailable",
                "The update recovery worker could not start. You can retry.",
            ));
            return;
        };
        inner.restore.worker = Some(handle.spawn_blocking(move || {
            let built = UpdateController::new(&root, setup);
            let mut inner = lock(&shared);
            match built {
                Err(cause) => {
                    let error = if is_update_error(&cause) {
                        cause
                    } else {
                        update_error(
                            "recovery_unavailable",
                            "Stored update status could not be checked. You can retry explicitly.",
                        )
                    };
                    if generation == inner.generation {
                        inner.restore.error = Some(error);
                    }
                }
                Ok(controller) => {
                    if inner.closed || generation != inner.generation {
                        controller.close();
                    } else {
                        inner.controller = Some(controller);
                    }
                }
            }
        }));
    }

    /// `handle(action, params)`: one `desktop_update_<action>` request.
    pub fn handle(&self, action: &str, params: &JsonMap) -> CodedResult<JsonMap> {
        if lock(&self.inner).closed {
            return Err(update_error("updater_closed", "The desktop updater is closing."));
        }
        let required: &[&str] =
            if matches!(action, "state" | "check") { &["host"] } else { &["host", "operation_id"] };
        if !matches!(action, "state" | "check" | "download" | "cancel" | "defer" | "prepare" | "handoff")
            || params.len() != required.len()
            || !required.iter().all(|key| params.contains_key(*key))
        {
            return Err(invalid_request());
        }
        if !matches!(action, "state" | "check") {
            operation_id(&params["operation_id"])?;
        }
        let inconsistent = || {
            update_error("wrong_component_version", "The desktop and owner update context is inconsistent.")
        };
        let Json::Object(host) = &params["host"] else {
            return Err(inconsistent());
        };
        let keys = ["version", "target", "distribution"];
        let target = match host.get("target") {
            Some(Json::String(target)) if target.chars().count() <= 64 => target.clone(),
            _ => return Err(inconsistent()),
        };
        if host.len() != keys.len()
            || !keys.iter().all(|key| host.contains_key(*key))
            || host["version"] != APP_VERSION
            || !matches!(host["distribution"].as_str(), Some("installed" | "portable"))
        {
            return Err(inconsistent());
        }
        let distribution = host["distribution"].as_str().unwrap_or_default().to_string();
        let context = (target.clone(), distribution.clone());
        {
            let mut inner = lock(&self.inner);
            if inner.context.as_ref().is_some_and(|existing| *existing != context) {
                return Err(update_error(
                    "changed_update_context",
                    "Restart this copy before using its updater.",
                ));
            }
            inner.context = Some(context);
        }
        let keyring = KeyRing::new(self.keys.clone());
        let supported = target == "windows-x86_64";
        let mut base = JsonMap::new();
        base.insert("operation_id".into(), Json::Null);
        base.insert("current_version".into(), APP_VERSION.into());
        base.insert("version".into(), Json::Null);
        base.insert("channel".into(), RELEASE_CHANNEL.into());
        base.insert("target".into(), target.clone().into());
        base.insert("distribution".into(), distribution.clone().into());
        base.insert("notes".into(), "".into());
        base.insert("received".into(), 0.into());
        base.insert("total".into(), Json::Null);
        base.insert("downloaded".into(), false.into());
        base.insert("can_apply".into(), false.into());
        base.insert("source".into(), SOURCE.into());
        if !supported || !keyring.has_keys() {
            let (code, message) = if !supported {
                ("unsupported_target", "In-app updating is not supported for this platform.")
            } else {
                (
                    "trust_not_configured",
                    "This build has no trusted update key. Install an approved updater-capable bootstrap before updating.",
                )
            };
            if action != "state" {
                return Err(update_error(code, message));
            }
            base.insert("phase".into(), phase::UNAVAILABLE.into());
            base.insert("busy".into(), false.into());
            base.insert("error_code".into(), code.into());
            base.insert("message".into(), message.into());
            return Ok(base);
        }
        let controller = {
            let mut inner = lock(&self.inner);
            match inner.controller.clone() {
                Some(controller) => controller,
                None => {
                    let finished =
                        inner.restore.worker.as_ref().is_none_or(tokio::task::JoinHandle::is_finished);
                    if inner.restore.worker.is_none() || (action == "check" && finished) {
                        self.restore(&mut inner, &target, &distribution, keyring);
                    }
                    let error = inner.restore.error.clone();
                    base.insert(
                        "phase".into(),
                        if error.is_some() { phase::ERROR } else { phase::RECOVERING }.into(),
                    );
                    base.insert("busy".into(), error.is_none().into());
                    base.insert(
                        "error_code".into(),
                        error.as_ref().map_or(Json::Null, |e| e.code.clone().into()),
                    );
                    base.insert(
                        "message".into(),
                        match &error {
                            Some(error) => error.message.clone().into(),
                            None => "Reading previous update status. Check for releases when this finishes."
                                .into(),
                        },
                    );
                    return Ok(base);
                }
            }
        };
        let mut result = match action {
            "state" => controller.state(),
            "check" => controller.check()?,
            "prepare" => {
                let identity = operation_id(&params["operation_id"])?;
                self.prepare(&controller, &identity, &keyring, &target, &distribution)?
            }
            "handoff" => {
                let identity = operation_id(&params["operation_id"])?;
                return self.handoff(&controller, &identity, &keyring, &target, &distribution);
            }
            _ => {
                let identity = operation_id(&params["operation_id"])?;
                let result = match action {
                    "download" => controller.download(&identity)?,
                    "cancel" => controller.cancel(&identity)?,
                    _ => controller.defer(&identity)?,
                };
                if action == "defer" {
                    self.release_if_reserved(&identity)?;
                }
                result
            }
        };
        // Apply stays unavailable until its separate native checkpoint and shutdown boundary exists.
        result.insert("can_apply".into(), false.into());
        Ok(result)
    }

    /// `_prepare(...)`: an update launch context, the same copy, no engineering overrides and a
    /// reservation of the idle owner; then the checkpoint and file preparation run as the
    /// controller's operation, which releases the reservation if it fails.
    fn prepare(
        &self,
        controller: &UpdateController,
        identity: &str,
        keyring: &KeyRing,
        target: &str,
        distribution: &str,
    ) -> CodedResult<JsonMap> {
        let (Some(installation), Some(backup), Some(reserved), Some(_)) =
            (&self.installation, &self.backup, &self.reserved, &self.release)
        else {
            return Err(update_error(
                "unsupported_launch_context",
                "This launch cannot prepare an application update.",
            ));
        };
        installation.check(target, distribution)?;
        // Refuse engineering overrides before any checkpoint or file preparation.
        super::runtime::child_environment()?;
        if !reserved(identity) {
            return Err(update_error("update_busy", "Reserve an idle application before preparing."));
        }
        let (inner, reserved_check) = (Arc::clone(&self.inner), Arc::clone(reserved));
        let quiet_identity = identity.to_string();
        let quiesced = move || !lock(&inner).closed && reserved_check(&quiet_identity);
        let (storage, live, backup) = (self.storage.clone(), installation.live.clone(), Arc::clone(backup));
        let (action_identity, keyring) = (identity.to_string(), keyring.clone());
        let (target, distribution) = (target.to_string(), distribution.to_string());
        let action = move || -> CodedResult<String> {
            let mut update = PreparedUpdate::new(
                &storage,
                &live,
                &action_identity,
                &new_attempt(),
                &Version::parse_text(APP_VERSION).expect("the build version is SemVer"),
                &target,
                &distribution,
                &keyring,
            )?;
            update.prepare(&|target: &Path| backup(target), &quiesced)?;
            Ok(update.attempt)
        };
        let (release_reserved, release) = (self.reserved.clone(), self.release.clone());
        let failure_identity = identity.to_string();
        let on_failure = move || {
            if let (Some(reserved), Some(release)) = (&release_reserved, &release) {
                if reserved(&failure_identity) {
                    let _ = release(&failure_identity);
                }
            }
        };
        controller.prepare(identity, action, on_failure)
    }

    /// `_handoff(...)`: private owner-pipe metadata for the native helper launch, never a
    /// renderer-selected path or executable.
    fn handoff(
        &self,
        controller: &UpdateController,
        identity: &str,
        keyring: &KeyRing,
        target: &str,
        distribution: &str,
    ) -> CodedResult<JsonMap> {
        let state = controller.state();
        let installation = match (&self.installation, &self.reserved) {
            (Some(installation), Some(reserved))
                if reserved(identity)
                    && state["operation_id"] == identity
                    && state["phase"] == phase::PREPARED
                    && state["busy"] != true =>
            {
                installation
            }
            _ => {
                return Err(update_error("update_busy", "A matching prepared owner reservation is required."))
            }
        };
        installation.check(target, distribution)?;
        super::runtime::child_environment()?;
        let attempt = operation_id(&state["prepared_attempt"])?;
        let mut prepared = PreparedUpdate::new(
            &self.storage,
            &installation.live,
            identity,
            &attempt,
            &Version::parse_text(APP_VERSION).expect("the build version is SemVer"),
            target,
            distribution,
            keyring,
        )?;
        prepared.require_selected(None)?;
        let (_, helper) = prepared.replacement()?.helper_payload()?;
        let mut helper_row = JsonMap::new();
        helper_row.insert("size".into(), helper.size.into());
        helper_row.insert("sha256".into(), helper.sha256.into());
        let mut result = JsonMap::new();
        result.insert("operation_id".into(), identity.into());
        result.insert("attempt".into(), attempt.into());
        result.insert("helper".into(), Json::Object(helper_row));
        Ok(result)
    }

    /// `_release_if_reserved(identity)`.
    fn release_if_reserved(&self, identity: &str) -> CodedResult<()> {
        if let (Some(reserved), Some(release)) = (&self.reserved, &self.release) {
            if reserved(identity) {
                release(identity)?;
            }
        }
        Ok(())
    }

    /// `abort_preparation(identity)`: a matching native abort cannot release admission while a
    /// checkpoint still runs.
    pub fn abort_preparation(&self, identity: &str) -> CodedResult<()> {
        let controller = lock(&self.inner).controller.clone();
        if let Some(controller) = controller {
            let state = controller.state();
            if state["busy"] == true {
                return Err(update_error("update_busy", "Wait for update preparation to finish."));
            }
            if state["operation_id"] == identity && state["phase"] == phase::PREPARED {
                controller.defer(identity)?;
            }
        }
        Ok(())
    }

    /// `preparing()`: whether a controller operation is still running.
    pub fn preparing(&self) -> bool {
        let controller = lock(&self.inner).controller.clone();
        controller.is_some_and(|controller| controller.state()["busy"] == true)
    }

    /// `startup_completed()`: discard a boot-time recovery projection after the helper's
    /// final commit.
    pub async fn startup_completed(&self) {
        let controller = {
            let mut inner = lock(&self.inner);
            inner.generation += 1;
            inner.restore = Restore { worker: None, error: None };
            inner.controller.take()
        };
        if let Some(controller) = controller {
            controller.close();
            controller.wait().await;
        }
    }

    /// `arm_shutdown(identity)`: the handoff checks, then the controller moves to `closing`.
    /// Without a controller (always, in this build) the update is not prepared.
    pub fn arm_shutdown(&self, identity: &str) -> CodedResult<()> {
        let Some(controller) = lock(&self.inner).controller.clone() else {
            return Err(update_error("invalid_phase", "The update is not prepared for shutdown."));
        };
        let state = controller.state();
        let text = |key: &str| state[key].as_str().unwrap_or_default().to_string();
        self.handoff(
            &controller,
            identity,
            &KeyRing::new(self.keys.clone()),
            &text("target"),
            &text("distribution"),
        )?;
        controller.arm_shutdown(identity)
    }

    /// Wait for the background restore and the controller's running operation, as Python's
    /// tests join `_restore_thread` and then `controller.wait()`. Only the vector tests call it.
    #[doc(hidden)]
    pub async fn wait_for_workers(&self) {
        loop {
            let restoring =
                lock(&self.inner).restore.worker.as_ref().is_some_and(|worker| !worker.is_finished());
            if !restoring {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        }
        let controller = lock(&self.inner).controller.clone();
        if let Some(controller) = controller {
            controller.wait().await;
        }
    }

    /// The selected operation's identity, for the vector tests.
    #[doc(hidden)]
    pub fn selected_operation(&self) -> Option<String> {
        let controller = lock(&self.inner).controller.clone();
        controller.and_then(|controller| controller.state()["operation_id"].as_str().map(str::to_string))
    }

    /// `close()`: no apply or retry starts; an admitted operation finishes first.
    pub async fn close(&self) {
        let controller = {
            let mut inner = lock(&self.inner);
            inner.closed = true;
            inner.controller.clone()
        };
        if let Some(controller) = controller {
            controller.close();
            controller.wait().await;
        }
    }
}
