//! `application/desktop_update/controller.py`: the explicit, asynchronous check and download
//! lifecycle, with durable interruption recovery.
//!
//! The native shell supplies the context; operation identities go out. Nothing here takes a
//! renderer-selected URL, path or command. Python's worker thread is one blocking task
//! (`tokio::task::spawn_blocking`), and Python's `RLock` is one mutex over the state: every
//! place Python holds the lock holds the mutex, and no await happens while it is held.

use super::discovery::{discover, retain_current_manifest};
use super::manifest::{
    bounded_json, parse_current_recovery_manifest, parse_manifest, Context, KeyRing, Manifest, Version,
    MANIFEST_LIMIT, RELEASE_CHANNEL,
};
use super::payload::verify_artifact;
use super::storage::{
    atomic_receipt, checked_file, is_update_error, operation_id_text, phase, present, UpdateStore,
};
use super::transport::{download_artifact, Cancel, Transport};
use super::update_error;
use std::path::Path;
use std::sync::{Arc, Mutex, MutexGuard};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

/// `_APPLY_PHASES`: phases of an update being prepared, applied or recovered.
pub const APPLY_PHASES: [&str; 9] = [
    phase::PREPARING,
    phase::PREPARED,
    phase::CLOSING,
    phase::APPLYING,
    phase::AWAITING_RESTART,
    phase::RECOVERY_REQUIRED,
    phase::ROLLING_BACK,
    phase::ROLLBACK_READY,
    phase::ROLLBACK_AWAITING_RESTART,
];

/// The text `state()` names as the update source.
pub const SOURCE: &str = "Official pneuma-io/sylvia-gm GitHub releases";

/// What the controller is checked against: Python's constructor arguments.
pub struct Setup {
    pub current: Version,
    pub target: String,
    pub distribution: String,
    pub transport: Arc<dyn Transport>,
    pub keyring: KeyRing,
}

struct State {
    identity: Option<String>,
    phase: String,
    candidate: Option<Manifest>,
    error: Option<CodedError>,
    received: u64,
    total: Option<u128>,
    downloaded: bool,
    transfer_kind: &'static str,
    prepared_attempt: Option<String>,
    cancel: Cancel,
    worker: Option<tokio::task::JoinHandle<()>>,
}

struct Shared {
    store: UpdateStore,
    setup: Setup,
    state: Mutex<State>,
}

/// `UpdateController`.
#[derive(Clone)]
pub struct UpdateController {
    shared: Arc<Shared>,
}

/// Python's generic report of an exception that is not an `UpdateError`.
fn failed() -> CodedError {
    update_error("update_failed", "The update operation failed. The current application is unchanged.")
}

impl Shared {
    fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    fn context(&self) -> Context<'_> {
        Context {
            current: &self.setup.current,
            target: &self.setup.target,
            distribution: &self.setup.distribution,
            keyring: &self.setup.keyring,
        }
    }

    /// `_phase_to(phase, error)`: record the phase durably, then in memory, then prune an
    /// empty terminal folder.
    fn phase_to(&self, state: &mut State, next: &str, error: Option<CodedError>) -> CodedResult<()> {
        let identity = state.identity.clone().expect("an operation is selected");
        let version = state.candidate.as_ref().map(|candidate| candidate.version.text.clone());
        self.store.save(
            &identity,
            next,
            version.as_deref(),
            error.as_ref().map(|error| error.code.as_str()),
        )?;
        state.phase = next.to_string();
        state.error = error;
        self.store.prune_empty_terminal(&identity)
    }

    /// `_restore()`: a fresh controller reads the retained receipt.
    fn restore(&self, state: &mut State) -> CodedResult<()> {
        let Some(row) = self.store.load()? else {
            return Ok(());
        };
        let identity = operation_id_text(&row.operation_id)?;
        state.identity = Some(identity.clone());
        let saved = row.phase.as_str();
        if APPLY_PHASES.contains(&saved) {
            state.phase = phase::RECOVERY_REQUIRED.into();
            return Ok(());
        }
        if [phase::AVAILABLE, phase::VERIFIED, phase::DEFERRED].contains(&saved) {
            match self.restore_candidate(state, &identity, saved, row.version.as_deref()) {
                Ok(()) => state.phase = saved.to_string(),
                Err(error) if is_update_error(&error) => {
                    state.phase = phase::ERROR.into();
                    state.error = Some(error);
                }
                Err(error) => return Err(error),
            }
            return Ok(());
        }
        state.phase =
            if [phase::CHECKING, phase::DOWNLOADING].contains(&saved) { phase::INTERRUPTED } else { saved }
                .to_string();
        Ok(())
    }

    fn restore_candidate(
        &self,
        state: &mut State,
        identity: &str,
        saved: &str,
        version: Option<&str>,
    ) -> CodedResult<()> {
        let stage = self.store.stage(identity)?;
        let raw = checked_file(&stage.join("manifest.json"), MANIFEST_LIMIT)?;
        let candidate = parse_manifest(&raw, self.context())?;
        state.candidate = Some(candidate.clone());
        if version != Some(candidate.version.text.as_str()) {
            return Err(update_error("invalid_journal", "The stored update identity is inconsistent."));
        }
        let artifact = self.store.stage(identity)?.join("artifact.verified");
        if saved == phase::VERIFIED || present(&artifact) {
            verify_artifact(&artifact, &candidate)?;
            if self.setup.distribution == "installed" {
                let stage = self.store.stage(identity)?;
                let previous = parse_current_recovery_manifest(
                    &checked_file(&stage.join("previous-manifest.json"), MANIFEST_LIMIT)?,
                    self.context(),
                )?;
                let stage = self.store.stage(identity)?;
                verify_artifact(&stage.join("previous-installer/artifact.verified"), &previous)?;
            }
            state.downloaded = true;
        }
        Ok(())
    }

    fn state_value(&self, state: &State) -> JsonMap {
        let candidate = state.candidate.as_ref();
        let mut value = JsonMap::new();
        value.insert("phase".into(), state.phase.clone().into());
        value.insert("operation_id".into(), state.identity.clone().map_or(Json::Null, Json::from));
        value.insert("current_version".into(), self.setup.current.text.clone().into());
        value.insert("channel".into(), RELEASE_CHANNEL.into());
        value.insert("target".into(), self.setup.target.clone().into());
        value.insert("distribution".into(), self.setup.distribution.clone().into());
        value.insert("source".into(), SOURCE.into());
        value.insert("version".into(), candidate.map_or(Json::Null, |c| c.version.text.clone().into()));
        value.insert("notes".into(), candidate.map_or(String::new(), |c| c.notes.clone()).into());
        value.insert("received".into(), state.received.into());
        value.insert("total".into(), state.total.map_or(Json::Null, |total| Json::from(total as u64)));
        value.insert("downloaded".into(), state.downloaded.into());
        value.insert("transfer_kind".into(), state.transfer_kind.into());
        value
            .insert("prepared_attempt".into(), state.prepared_attempt.clone().map_or(Json::Null, Json::from));
        value.insert("busy".into(), state.worker.as_ref().is_some_and(|worker| !worker.is_finished()).into());
        value.insert("error_code".into(), state.error.as_ref().map_or(Json::Null, |e| e.code.clone().into()));
        value.insert("message".into(), state.error.as_ref().map_or(Json::Null, |e| e.message.clone().into()));
        value
    }

    /// `_require(identity)`.
    fn require(state: &State, identity: &str) -> CodedResult<()> {
        if Some(operation_id_text(identity)?) != state.identity {
            return Err(update_error("stale_operation", "This update is no longer selected."));
        }
        Ok(())
    }

    /// `_idle_worker()`.
    fn idle_worker(state: &State) -> CodedResult<()> {
        if state.worker.as_ref().is_some_and(|worker| !worker.is_finished()) {
            return Err(update_error("update_busy", "The current update operation is still finishing."));
        }
        Ok(())
    }
}

impl UpdateController {
    /// `UpdateController(root=, current=, target=, distribution=, transport=, verify=)`:
    /// opens the store and restores any retained operation. Runs blocking file work, so call
    /// it from a blocking task.
    pub fn new(root: &Path, setup: Setup) -> CodedResult<Self> {
        let store = UpdateStore::new(root)?;
        let state = State {
            identity: None,
            phase: phase::IDLE.into(),
            candidate: None,
            error: None,
            received: 0,
            total: None,
            downloaded: false,
            transfer_kind: "update",
            prepared_attempt: None,
            cancel: Cancel::default(),
            worker: None,
        };
        let shared = Arc::new(Shared { store, setup, state: Mutex::new(state) });
        {
            let mut state = shared.lock();
            shared.restore(&mut state)?;
        }
        Ok(Self { shared })
    }

    /// `state()`.
    pub fn state(&self) -> JsonMap {
        let state = self.shared.lock();
        self.shared.state_value(&state)
    }

    /// `_start(action)`: run `action` as the operation's worker. A failure ends the operation
    /// as cancelled (when cancellation was asked for) or as an error, unless another operation
    /// was selected meanwhile.
    fn start(
        &self,
        state: &mut State,
        action: impl FnOnce(&Arc<Shared>) -> CodedResult<()> + Send + 'static,
    ) -> CodedResult<()> {
        let identity = state.identity.clone();
        let cancel = state.cancel.clone();
        let shared = Arc::clone(&self.shared);
        let handle = tokio::runtime::Handle::try_current().map_err(|_| {
            update_error("worker_unavailable", "The update worker could not start. You can retry.")
        });
        let handle = match handle {
            Ok(handle) => handle,
            Err(error) => {
                self.shared.phase_to(state, phase::ERROR, Some(error.clone()))?;
                return Err(error);
            }
        };
        state.worker = Some(handle.spawn_blocking(move || {
            let Err(cause) = action(&shared) else {
                return;
            };
            let error = if is_update_error(&cause) { cause } else { failed() };
            let mut state = shared.lock();
            if identity != state.identity {
                return;
            }
            let next = if cancel.is_set() { phase::CANCELLED } else { phase::ERROR };
            if let Err(journal) = shared.phase_to(&mut state, next, Some(error)) {
                state.phase = phase::ERROR.into();
                state.error = Some(if is_update_error(&journal) { journal } else { failed() });
            }
        }));
        Ok(())
    }

    /// `check()`: look for a newer official release in a fresh operation folder.
    pub fn check(&self) -> CodedResult<JsonMap> {
        let mut state = self.shared.lock();
        Shared::idle_worker(&state)?;
        if APPLY_PHASES.contains(&state.phase.as_str()) {
            return Err(update_error("recovery_required", "Finish update recovery before checking again."));
        }
        let (identity, stage) = self.shared.store.create()?;
        state.identity = Some(identity);
        state.cancel = Cancel::default();
        state.candidate = None;
        state.error = None;
        state.downloaded = false;
        state.transfer_kind = "update";
        state.received = 0;
        state.total = None;
        self.shared.phase_to(&mut state, phase::CHECKING, None)?;
        let cancel = state.cancel.clone();
        self.start(&mut state, move |shared| {
            let candidate = discover(shared.setup.transport.as_ref(), &cancel, shared.context())?;
            let mut state = shared.lock();
            if state.cancel.is_set() {
                return Err(update_error("cancelled", "The update check was cancelled."));
            }
            if let Some(candidate) = &candidate {
                // Signed metadata only, never campaign data or provider credentials.
                atomic_receipt(
                    &stage.join("manifest.json"),
                    &bounded_json(&candidate.envelope_bytes, MANIFEST_LIMIT)?,
                )?;
            }
            let next = if candidate.is_some() { phase::AVAILABLE } else { phase::CURRENT };
            state.candidate = candidate;
            shared.phase_to(&mut state, next, None)
        })?;
        Ok(self.shared.state_value(&state))
    }

    /// `download(identity)`: an installed copy first keeps the current version's installer for
    /// recovery, then downloads the update.
    pub fn download(&self, identity: &str) -> CodedResult<JsonMap> {
        let mut state = self.shared.lock();
        Shared::require(&state, identity)?;
        Shared::idle_worker(&state)?;
        let manifest = match &state.candidate {
            Some(candidate)
                if [phase::AVAILABLE, phase::DEFERRED].contains(&state.phase.as_str())
                    && !state.downloaded =>
            {
                candidate.clone()
            }
            _ => {
                return Err(update_error(
                    "invalid_phase",
                    "Check for an available update before downloading.",
                ))
            }
        };
        let stage = self.shared.store.stage(identity)?;
        state.cancel = Cancel::default();
        self.shared.phase_to(&mut state, phase::DOWNLOADING, None)?;
        let cancel = state.cancel.clone();
        self.start(&mut state, move |shared| {
            let mut progress = |received: u64, total: Option<u128>| {
                let mut state = shared.lock();
                state.received = received;
                state.total = total;
            };
            let transport = shared.setup.transport.as_ref();
            if shared.setup.distribution == "installed" {
                {
                    let mut state = shared.lock();
                    state.transfer_kind = "previous_installer";
                    state.received = 0;
                    state.total = None;
                }
                let previous = retain_current_manifest(transport, &cancel, shared.context())?;
                atomic_receipt(
                    &stage.join("previous-manifest.json"),
                    &bounded_json(&previous.envelope_bytes, MANIFEST_LIMIT)?,
                )?;
                let retained = stage.join("previous-installer");
                std::fs::create_dir(&retained).map_err(|error| super::storage::os_error(&error))?;
                download_artifact(transport, &previous, &retained, &cancel, &mut progress)?;
                let mut state = shared.lock();
                state.transfer_kind = "update";
                state.received = 0;
                state.total = None;
            }
            download_artifact(transport, &manifest, &stage, &cancel, &mut progress)?;
            let mut state = shared.lock();
            if state.cancel.is_set() {
                return Err(update_error("cancelled", "The update download was cancelled."));
            }
            state.downloaded = true;
            shared.phase_to(&mut state, phase::VERIFIED, None)
        })?;
        Ok(self.shared.state_value(&state))
    }

    /// `cancel(identity)`: stop a running check or download.
    pub fn cancel(&self, identity: &str) -> CodedResult<JsonMap> {
        let state = self.shared.lock();
        Shared::require(&state, identity)?;
        if ![phase::CHECKING, phase::DOWNLOADING].contains(&state.phase.as_str()) {
            return Err(update_error(
                "invalid_phase",
                "This update can no longer be cancelled as a download.",
            ));
        }
        state.cancel.set();
        Ok(self.shared.state_value(&state))
    }

    /// `prepare(identity, action, on_failure)`: run `action` (the checkpoint and file
    /// preparation, which returns the attempt identity) once the owning service has reserved
    /// the idle owner.
    pub fn prepare(
        &self,
        identity: &str,
        action: impl FnOnce() -> CodedResult<String> + Send + 'static,
        on_failure: impl Fn() + Send + Sync + 'static,
    ) -> CodedResult<JsonMap> {
        let mut state = self.shared.lock();
        Shared::require(&state, identity)?;
        Shared::idle_worker(&state)?;
        if !state.downloaded
            || ![phase::VERIFIED, phase::DEFERRED, phase::ERROR].contains(&state.phase.as_str())
        {
            return Err(update_error("invalid_phase", "Download and verify this update first."));
        }
        state.prepared_attempt = None;
        self.shared.phase_to(&mut state, phase::PREPARING, None)?;
        let on_failure = Arc::new(on_failure);
        let failure = Arc::clone(&on_failure);
        let started = self.start(&mut state, move |shared| {
            let outcome = action().and_then(|attempt| operation_id_text(&attempt));
            match outcome {
                Ok(attempt) => {
                    let mut state = shared.lock();
                    state.prepared_attempt = Some(attempt);
                    shared.phase_to(&mut state, phase::PREPARED, None).inspect_err(|_| failure())
                }
                Err(error) => {
                    failure();
                    Err(error)
                }
            }
        });
        if let Err(error) = started {
            on_failure();
            return Err(error);
        }
        Ok(self.shared.state_value(&state))
    }

    /// `defer(identity)`.
    pub fn defer(&self, identity: &str) -> CodedResult<JsonMap> {
        let mut state = self.shared.lock();
        Shared::require(&state, identity)?;
        Shared::idle_worker(&state)?;
        if ![phase::AVAILABLE, phase::VERIFIED, phase::DEFERRED, phase::PREPARED]
            .contains(&state.phase.as_str())
        {
            return Err(update_error("invalid_phase", "This update cannot be deferred at this stage."));
        }
        state.prepared_attempt = None;
        self.shared.phase_to(&mut state, phase::DEFERRED, None)?;
        Ok(self.shared.state_value(&state))
    }

    /// `arm_shutdown(identity)`: block ordinary data startup before the reserved owner releases
    /// its ownership lock.
    pub fn arm_shutdown(&self, identity: &str) -> CodedResult<()> {
        let mut state = self.shared.lock();
        Shared::require(&state, identity)?;
        Shared::idle_worker(&state)?;
        if state.phase != phase::PREPARED {
            return Err(update_error("invalid_phase", "The update is not prepared for shutdown."));
        }
        self.shared.phase_to(&mut state, phase::CLOSING, None)
    }

    /// `close()`: closing never starts apply or a retry; network reads have a bounded timeout.
    pub fn close(&self) {
        self.shared.lock().cancel.set();
    }

    /// `wait()`: keep the owner's database alive until an admitted checkpoint finishes.
    pub async fn wait(&self) {
        let worker = self.shared.lock().worker.take();
        if let Some(worker) = worker {
            let _ = worker.await;
        }
    }
}
