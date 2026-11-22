//! The owner's run loop: `owner_process.run` in Python.
//!
//! Concurrency model for the whole owner (see `docs/rust-port/README.md`):
//! - one tokio runtime;
//! - one blocking thread reads stdin and hands frames to the loop over a channel;
//! - one task owns stdout, so replies never interleave (Python's `write_lock`).
//!
//! Requests run where Python runs them:
//! - ordinary commands run in the loop itself, one after another, so the next request is read
//!   only once the previous one is answered;
//! - the four display reads go to [`DisplayReads`], two at a time;
//! - the seven commands that can wait on a model run as their own tasks and answer whenever
//!   they finish.
//!
//! A model-bound command reserves provider work for its session before it starts, as Python's
//! `provider_request_work` does, and releases it before its reply is written.
//!
//! The desktop update: the startup check before any saved data opens (and again under the
//! ownership lock, before migrations), the `desktop_update_*` methods, the update reservation
//! and both update gates. Not ported yet: the recovery-only owner Python starts when an
//! interrupted update meets a native update context, and confirming an update restart; both
//! need update preparation. Also not ported: the Builder migrations at launch (lane 3), the
//! provider resolver and session loop (lane 4).

use crate::desktop_update::drafts::DraftStore;
use crate::desktop_update::launch::Installation;
use crate::desktop_update::service::DesktopUpdateService;
use crate::desktop_update::startup::{owner_startup_context, AuthorizedStartup};
use crate::desktop_update::storage::operation_id;
use crate::diagnostics::{Fields, LocalDiagnostics};
use crate::dispatch::{self, required_text, DispatchContext, DispatchResult, Failure};
use crate::display_reads::DisplayReads;
use crate::protocol::{self, Frame};
use crate::services::WorkGuard;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use sylvia_base::{pystr, Json, JsonMap};
use sylvia_store::launch::{acquire_owner, derive_owner_response};
use sylvia_store::StoreThread;
use tokio::sync::mpsc;

/// `_DISPLAY_READ_METHODS`.
const DISPLAY_READ_METHODS: &[&str] =
    &["display_snapshot", "player_projection", "player_projection_page", "campaign_panels"];

/// The commands Python runs on their own worker thread: each may wait on a model.
const WORKER_METHODS: &[&str] = &[
    "start_dm_session",
    "submit_session_input",
    "manual_retry_session",
    "ready_campaign_catalog",
    "start_ready_campaign",
    "customize_ready_campaign",
    "send_character_chat_message",
];

/// The methods an unconfirmed updated application still answers (`update_startup` is set and
/// `desktop_update_confirm_startup` has not succeeded).
const UNCONFIRMED_STARTUP_METHODS: &[&str] = &[
    "desktop_update_confirm_startup",
    "desktop_update_state",
    "shutdown",
    "load_desktop_drafts",
    "save_desktop_drafts",
    "campaign_index",
    "builder_chat_index",
    "provider_settings",
    "model_choices",
    "display_snapshot",
    "player_projection",
    "player_projection_page",
    "campaign_panels",
    "cancel_display_load",
    "record_local_fault",
];

/// The methods answered while update preparation has reserved the app.
const UPDATE_RESERVED_METHODS: &[&str] = &[
    "shutdown",
    "desktop_update_state",
    "desktop_update_abort",
    "desktop_update_reserve",
    "desktop_update_shutdown",
    "desktop_update_prepare",
    "desktop_update_defer",
    "desktop_update_handoff",
    "load_desktop_drafts",
    "save_desktop_drafts",
    "cancel_display_load",
    "campaign_index",
    "builder_chat_index",
];

/// The desktop update state the run loop's gates read, taken before each request.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct UpdateGates {
    /// `update_startup is not None and not update_start_confirmed`.
    pub startup_unconfirmed: bool,
    /// `supervisor.desktop_update_operation()`.
    pub reserved_operation: Option<String>,
}

impl UpdateGates {
    /// The refusal Python writes before admitting `method`, if any. The startup gate is checked
    /// first.
    pub fn refusal(&self, request_id: &str, method: &str) -> Option<Json> {
        if self.startup_unconfirmed && !UNCONFIRMED_STARTUP_METHODS.contains(&method) {
            let error = protocol::plain_error(
                "update_startup_unconfirmed",
                "Wait for the updated application to confirm startup.",
            );
            return Some(protocol::error_reply(Some(request_id), error));
        }
        if self.reserved_operation.is_some() && !UPDATE_RESERVED_METHODS.contains(&method) {
            let error = protocol::plain_error(
                "update_in_progress",
                "Update preparation has reserved this app. Finish or cancel it before changing saved work.",
            );
            return Some(protocol::error_reply(Some(request_id), error));
        }
        None
    }
}

/// `_model_slash_command(user_text)`: the bounded `/model [reference] [--once|--global]`
/// command, as `(reference, mode)`, or `None` when the text is not one.
pub fn model_slash_command(user_text: &str) -> Result<Option<(Option<String>, &'static str)>, Failure> {
    // `str.split()`: runs of Python whitespace, no empty parts.
    let parts: Vec<&str> = user_text.split(pystr::is_space).filter(|part| !part.is_empty()).collect();
    if parts.is_empty() || pystr::casefold(parts[0]) != "/model" {
        return Ok(None);
    }
    if parts.len() > 3 {
        return Err(Failure::protocol("Model command accepts one model and one optional mode"));
    }
    let flags: Vec<&str> = parts[1..].iter().copied().filter(|part| part.starts_with("--")).collect();
    let references: Vec<&str> = parts[1..].iter().copied().filter(|part| !part.starts_with("--")).collect();
    if flags.len() > 1 || references.len() > 1 {
        return Err(Failure::protocol("Model command is ambiguous"));
    }
    let mode = match flags.first() {
        None => "current",
        Some(&"--once") => "once",
        Some(&"--global") => "global",
        Some(_) => return Err(Failure::protocol("Model command mode is unsupported")),
    };
    Ok(Some((references.first().map(|reference| (*reference).to_owned()), mode)))
}

/// `_generation_session(method, params)`: the session whose provider work a command reserves.
/// `/model` is only a settings command.
pub fn generation_session(method: &str, params: &JsonMap) -> Result<Option<String>, Failure> {
    if !matches!(method, "submit_session_input" | "manual_retry_session" | "start_dm_session") {
        return Ok(None);
    }
    if method == "submit_session_input" {
        if let (Some(Json::String(user_text)), Some(Json::Null) | None) =
            (params.get("user_text"), params.get("ooc_text"))
        {
            // Python reads `params.get("ooc_text") is None`: missing and null are the same.
            if model_slash_command(user_text)?.is_some() {
                return Ok(None);
            }
        }
    }
    required_text(params, "session_id").map(Some)
}

/// The fixed reply text of Python's `except Exception` branch.
const INTERNAL_ERROR_MESSAGE: &str = "Sylvia encountered an internal error, not a confirmed provider failure. \
Previously saved work is kept. Try the action again; if it repeats, report this error with the action you were taking.";

/// Exit code of a clean shutdown.
pub const EXIT_OK: i32 = 0;
/// Exit code Python's `main` returns when startup fails.
pub const EXIT_STARTUP_FAILED: i32 = 2;

/// Where replies go. Once the owner is closing nothing more is written (Python's
/// `write_reply` checks `closing`).
#[derive(Clone)]
pub struct Replies {
    sender: mpsc::UnboundedSender<Json>,
    closing: Arc<AtomicBool>,
}

impl Replies {
    /// Write one reply unless the owner is closing.
    pub fn send(&self, reply: Json) {
        if !self.closing.load(Ordering::SeqCst) {
            let _ = self.sender.send(reply);
        }
    }
}

/// Serve one owner session over the given streams and return the process exit code.
pub async fn run<R, W>(input: R, output: W) -> i32
where
    R: BufRead + Send + 'static,
    W: Write + Send + 'static,
{
    let (frame_sender, mut frames) = mpsc::unbounded_channel::<Frame>();
    let (mode_sender, mode) = std::sync::mpsc::channel::<InputMode>();
    std::thread::Builder::new()
        .name("sylvia-owner-stdin".into())
        .spawn(move || read_frames(input, &frame_sender, &mode))
        .expect("the stdin reader thread starts");

    let (reply_sender, replies) = mpsc::unbounded_channel::<Json>();
    let writer = tokio::task::spawn_blocking(move || write_replies(output, replies));
    let replies = Replies { sender: reply_sender, closing: Arc::new(AtomicBool::new(false)) };

    let code = serve(&mut frames, &replies, &mode_sender).await;
    // Dropping the last sender lets the writer drain what is queued and stop.
    drop(replies);
    let _ = writer.await;
    code
}

/// How the stdin thread reads after the launch envelope: owner messages, or the raw lines of
/// the recovery-only owner (`run_recovery` reads its own bounded requests).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum InputMode {
    Owner,
    Recovery,
}

fn read_frames(
    mut input: impl BufRead,
    frames: &mpsc::UnboundedSender<Frame>,
    mode: &std::sync::mpsc::Receiver<InputMode>,
) {
    let first = protocol::read_message(&mut input);
    let last = matches!(first, Frame::End | Frame::Fatal(_));
    if frames.send(first).is_err() || last {
        return;
    }
    // The serve loop decides, from the launch envelope, how the rest is read.
    let Ok(mode) = mode.recv() else { return };
    loop {
        let (frame, last) = match mode {
            InputMode::Owner => {
                let frame = protocol::read_message(&mut input);
                let last = matches!(frame, Frame::End | Frame::Fatal(_));
                (frame, last)
            }
            InputMode::Recovery => {
                let line = protocol::read_raw_line(&mut input);
                let last = !matches!(line, crate::desktop_update::recovery_service::RecoveryLine::Line(_));
                (Frame::Raw(line), last)
            }
        };
        if frames.send(frame).is_err() || last {
            return;
        }
    }
}

fn write_replies(mut output: impl Write, mut replies: mpsc::UnboundedReceiver<Json>) {
    while let Some(reply) = replies.blocking_recv() {
        if protocol::write_message(&mut output, &reply).is_err() {
            return;
        }
    }
}

/// The owner's shared state while it serves: Python's closure variables in `run`.
struct Owner {
    context: DispatchContext,
    diagnostics: Arc<LocalDiagnostics>,
    /// `desktop_drafts`, opened on first use.
    drafts: Mutex<Option<Arc<DraftStore>>>,
    /// `desktop_updates`, made on first use.
    updates: Mutex<Option<Arc<DesktopUpdateService>>>,
    /// `update_installation`: the native update context, when the launch gave a valid one.
    update_installation: Option<Installation>,
    /// `update_startup`: the update restart this owner was started to confirm.
    update_startup: Mutex<Option<AuthorizedStartup>>,
    /// `update_start_confirmed`.
    update_start_confirmed: AtomicBool,
    /// `supervisor._desktop_update_operation`: the update that has reserved this owner.
    update_operation: Arc<Mutex<Option<String>>>,
    /// Python's `closing` event, for the updater's `reserved` check.
    closing: Arc<AtomicBool>,
}

impl Owner {
    /// The gates' view of the update state.
    fn gates(&self) -> UpdateGates {
        UpdateGates {
            startup_unconfirmed: self.update_startup.lock().expect("update startup lock").is_some()
                && !self.update_start_confirmed.load(Ordering::SeqCst),
            reserved_operation: self.update_operation.lock().expect("update reservation lock").clone(),
        }
    }

    /// `desktop_updates`, made on first use with the run loop's reservation callbacks.
    fn updates(&self) -> Arc<DesktopUpdateService> {
        let mut slot = self.updates.lock().expect("updates lock");
        if let Some(service) = &*slot {
            return Arc::clone(service);
        }
        let (operation, closing) = (Arc::clone(&self.update_operation), Arc::clone(&self.closing));
        let reserved: crate::desktop_update::service::Reserved = Arc::new(move |identity: &str| {
            !closing.load(Ordering::SeqCst)
                && operation.lock().expect("update reservation lock").as_deref() == Some(identity)
        });
        let operation = Arc::clone(&self.update_operation);
        let release: crate::desktop_update::service::Release =
            Arc::new(move |identity: &str| abort_desktop_update(&operation, identity));
        // `bus.backup_database`: the store's coherent online backup, called from the update
        // controller's blocking worker.
        let store = self.context.store.clone();
        let backup: crate::desktop_update::service::Backup = Arc::new(move |target: &Path| {
            let target = target.to_path_buf();
            store
                .call_blocking(move |store| store.backup_database(&target))
                .map_err(crate::commands::campaigns::store_error)
        });
        let service = Arc::new(DesktopUpdateService::new(
            &self.context.storage_root,
            self.update_installation.clone(),
            Some(backup),
            Some(reserved),
            Some(release),
        ));
        *slot = Some(Arc::clone(&service));
        service
    }
}

/// `supervisor.begin_desktop_update(identity)`: reserve an idle owner without stopping work or
/// changing committed game state. Lane 4's session loop is not in the owner yet, so no
/// narration lease or provider work can be active here.
fn begin_desktop_update(
    operation: &Mutex<Option<String>>,
    identity: &str,
) -> Result<(), sylvia_base::CodedError> {
    let mut operation = operation.lock().expect("update reservation lock");
    match operation.as_deref() {
        Some(existing) if existing == identity => Ok(()),
        Some(_) => Err(sylvia_base::CodedError::new("update_in_progress", "Another update is preparing")),
        None => {
            *operation = Some(identity.to_owned());
            Ok(())
        }
    }
}

/// `supervisor.abort_desktop_update(identity)`: release only the selected reservation.
fn abort_desktop_update(
    operation: &Mutex<Option<String>>,
    identity: &str,
) -> Result<(), sylvia_base::CodedError> {
    let mut operation = operation.lock().expect("update reservation lock");
    if operation.as_deref() != Some(identity) {
        return Err(sylvia_base::CodedError::new(
            "stale_update_operation",
            "This update reservation is stale",
        ));
    }
    *operation = None;
    Ok(())
}

/// `run_recovery(launch, ...)`: an interrupted update met a native update context, so this
/// owner serves only update recovery. It opens no authority, migrates nothing and reads no
/// campaign. An exception Python lets escape ends it without a reply (exit code 2).
async fn serve_recovery(
    frames: &mut mpsc::UnboundedReceiver<Frame>,
    replies: &Replies,
    envelope: JsonMap,
) -> i32 {
    use crate::desktop_update::recovery_service::{
        recovery_launch, recovery_step, RecoveryLine, RecoveryStep,
    };
    let Some(Ok(launched)) = blocking(move || recovery_launch(&envelope)).await else {
        return startup_failed();
    };
    replies.send(launched.ready);
    let service = Arc::new(launched.service);
    loop {
        let line = match frames.recv().await {
            Some(Frame::Raw(line)) => line,
            _ => RecoveryLine::End,
        };
        let step_service = Arc::clone(&service);
        match blocking(move || recovery_step(&step_service, line)).await {
            Some(RecoveryStep::Reply(reply)) => replies.send(reply),
            Some(RecoveryStep::Closed(reply)) => {
                replies.send(reply);
                return EXIT_OK;
            }
            Some(RecoveryStep::End) => return EXIT_OK,
            Some(RecoveryStep::Failed) | None => return startup_failed(),
        }
    }
}

/// Python's fixed refusal when the update startup check fails.
fn update_recovery_refusal() -> Json {
    let mut error = JsonMap::new();
    error.insert("code".into(), "update_recovery_required".into());
    error.insert(
        "message".into(),
        "The interrupted update must be reconciled before opening saved data.".into(),
    );
    let mut reply = JsonMap::new();
    reply.insert("ok".into(), false.into());
    reply.insert("type".into(), "update_recovery_required".into());
    reply.insert("error".into(), Json::Object(error));
    Json::Object(reply)
}

/// Run blocking store work (opening, closing) off the runtime's worker threads.
async fn blocking<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static) -> Option<T> {
    tokio::task::spawn_blocking(work).await.ok()
}

async fn serve(
    frames: &mut mpsc::UnboundedReceiver<Frame>,
    replies: &Replies,
    mode: &std::sync::mpsc::Sender<InputMode>,
) -> i32 {
    // Launch: the first message must be a valid launch envelope. Any failure here ends the
    // owner without a reply, as Python does ("Sylvia GM owner startup failed" on stderr).
    let launched = match frames.recv().await {
        Some(Frame::Message(envelope)) => {
            let launch_envelope = envelope.clone();
            match blocking(move || launch(&launch_envelope)).await {
                Some(Ok(launched)) => launched,
                Some(Err(LaunchFailure::Update { recovery })) => {
                    if recovery {
                        let _ = mode.send(InputMode::Recovery);
                        return serve_recovery(frames, replies, envelope).await;
                    }
                    // No authority acquisition, schema migration or provider work occurred.
                    replies.send(update_recovery_refusal());
                    return EXIT_STARTUP_FAILED;
                }
                _ => return startup_failed(),
            }
        }
        _ => return startup_failed(),
    };
    let _ = mode.send(InputMode::Owner);
    let Launched { context, response_hex, store_thread, installation, startup } = launched;
    let diagnostics = Arc::new(LocalDiagnostics::new(&context.storage_root));
    let owner = Arc::new(Owner {
        diagnostics,
        context,
        drafts: Mutex::new(None),
        updates: Mutex::new(None),
        update_installation: installation,
        update_startup: Mutex::new(startup),
        update_start_confirmed: AtomicBool::new(false),
        update_operation: Arc::new(Mutex::new(None)),
        closing: Arc::clone(&replies.closing),
    });
    // Python's `application_owner.prepare_new_campaign_storage()`, after diagnostics opened, then
    // `_preinstall_bundled_srd(bus)` and `_preinstall_bundled_riichi(bus)`.
    let bundled = Arc::clone(&owner.context.bundled);
    let prepared = owner
        .context
        .store
        .call(move |store| {
            store.prepare_new_campaign_storage()?;
            crate::preinstall::preinstall_bundled(store, &bundled)
        })
        .await;
    if prepared.is_err() {
        owner.diagnostics.record(
            "owner_failed",
            Fields { outcome: Some("error"), error_code: Some("internal_error"), ..Fields::default() },
        );
        close_owner(owner, store_thread).await;
        return startup_failed();
    }
    // Python's `reconcile_builder_source_purges(storage_root, live campaign ids,
    // application_owner.campaign_purge_receipt_exists)`, after storage preparation and the bundled
    // preinstalls. (The legacy Builder chat migration Python runs between them is not ported
    // yet.) A failure ends the launch as the preparation's does.
    let storage_root = owner.context.storage_root.clone();
    let reconciled = owner
        .context
        .store
        .call(move |store| {
            let live: std::collections::HashSet<String> =
                store.campaign_index_entries()?.into_iter().map(|entry| entry.campaign_id).collect();
            sylvia_store::builder_sources::reconcile_builder_source_purges(
                &storage_root,
                &live,
                &mut |operation, campaign| {
                    sylvia_app::authority::lifecycle::campaign_purge_receipt_exists(
                        store, operation, campaign,
                    )
                },
            )
        })
        .await;
    if reconciled.is_err() {
        owner.diagnostics.record(
            "owner_failed",
            Fields { outcome: Some("error"), error_code: Some("internal_error"), ..Fields::default() },
        );
        close_owner(owner, store_thread).await;
        return startup_failed();
    }
    let mut ready = JsonMap::new();
    ready.insert("ok".into(), Json::Bool(true));
    ready.insert("type".into(), Json::String("ready".into()));
    ready.insert("response_hex".into(), Json::String(response_hex));
    replies.send(Json::Object(ready));

    let display_reads = {
        let written = replies.clone();
        let cancelled = replies.clone();
        DisplayReads::new(
            Arc::new(move |reply| written.send(reply)),
            Arc::new(move |request_id: &str| {
                let error = protocol::plain_error(
                    "display_load_cancelled",
                    "This display load is no longer selected",
                );
                cancelled.send(protocol::error_reply(Some(request_id), error));
            }),
        )
    };
    let mut workers = tokio::task::JoinSet::new();

    let code = loop {
        let frame = tokio::select! {
            frame = frames.recv() => frame,
            Some(_) = workers.join_next(), if !workers.is_empty() => continue,
        };
        let request = match frame {
            None | Some(Frame::End) => break EXIT_OK,
            Some(Frame::Fatal(_)) => {
                owner.diagnostics.record(
                    "owner_failed",
                    Fields {
                        outcome: Some("error"),
                        error_code: Some("internal_error"),
                        ..Fields::default()
                    },
                );
                break EXIT_STARTUP_FAILED;
            }
            Some(Frame::Invalid(message)) => {
                send_invalid(replies, None, message);
                continue;
            }
            Some(Frame::Message(request)) => request,
            // Raw lines are read only for the recovery-only owner, never in this loop.
            Some(Frame::Raw(_)) => continue,
        };
        let id = request.get("id").and_then(Json::as_str).filter(|id| !id.is_empty());
        let (id, method, params, load_id) = match validate(&request, id) {
            Ok(parts) => parts,
            Err(message) => {
                send_invalid(replies, id, &message);
                continue;
            }
        };

        if let Some(refusal) = owner.gates().refusal(&id, &method) {
            replies.send(refusal);
            continue;
        }
        if matches!(
            method.as_str(),
            "desktop_update_reserve" | "desktop_update_abort" | "desktop_update_shutdown"
        ) {
            // Workers still running count as admitted work, as Python's live threads do.
            while workers.try_join_next().is_some() {}
            let busy = !workers.is_empty() || !display_reads.idle();
            let (reply, closed) = update_lifecycle(&owner, &id, &method, &params, busy);
            replies.send(reply);
            if closed {
                // An idle owner closes; no provider cancellation or replay.
                break EXIT_OK;
            }
            continue;
        }
        if method == "shutdown" {
            let mut result = JsonMap::new();
            result.insert("status".into(), Json::String("closed".into()));
            replies.send(protocol::ok_reply(&id, Json::Object(result)));
            break EXIT_OK;
        }
        if method == "cancel_display_load" {
            display_reads.cancel(load_id.as_deref().expect("validated"));
            let mut result = JsonMap::new();
            result.insert("status".into(), Json::String("cancelled".into()));
            replies.send(protocol::ok_reply(&id, Json::Object(result)));
            continue;
        }
        if DISPLAY_READ_METHODS.contains(&method.as_str()) {
            let owner = Arc::clone(&owner);
            let request_id = id.clone();
            display_reads.submit(
                id,
                load_id,
                Box::pin(async move { execute_request(&owner, &request_id, &method, &params, None).await }),
            );
            continue;
        }
        if WORKER_METHODS.contains(&method.as_str()) {
            let reservation = match reserve(&owner, &method, &params) {
                Ok(reservation) => reservation,
                Err(error) => {
                    // Python records `request_rejected` and replies with code and message only.
                    owner.diagnostics.record(
                        "request_rejected",
                        Fields {
                            operation: Some(&method),
                            outcome: Some("error"),
                            error_code: Some("command_rejected"),
                            error_type: Some(error.error_type.unwrap_or("CommandRejected")),
                            ..Fields::default()
                        },
                    );
                    replies.send(protocol::error_reply(
                        Some(&id),
                        protocol::plain_error(&error.code, &error.message),
                    ));
                    continue;
                }
            };
            let owner = Arc::clone(&owner);
            let replies = replies.clone();
            workers.spawn(async move {
                let reply = execute_request(&owner, &id, &method, &params, reservation).await;
                replies.send(reply);
            });
            continue;
        }
        let reply = execute_request(&owner, &id, &method, &params, None).await;
        replies.send(reply);
    };

    // Python's `finally`: stop writing, let running display reads finish, give workers five
    // seconds, then the last diagnostics write.
    replies.closing.store(true, Ordering::SeqCst);
    let updates = owner.updates.lock().expect("updates lock").clone();
    if let Some(updates) = updates {
        updates.close().await;
    }
    display_reads.close().await;
    let deadline = tokio::time::sleep(Duration::from_secs(5));
    tokio::pin!(deadline);
    while !workers.is_empty() {
        tokio::select! {
            _ = workers.join_next() => {}
            () = &mut deadline => break,
        }
    }
    // Workers still running after five seconds are abandoned, as Python's daemon threads are.
    workers.abort_all();
    while workers.join_next().await.is_some() {}
    close_owner(owner, store_thread).await;
    code
}

/// Python's `finally`, after the loop: close the store (the supervisor and bus), then write
/// diagnostics for the last time. The store thread ends once every handle is dropped; a
/// handler task still holding one gets five seconds, as Python's workers do.
async fn close_owner(owner: Arc<Owner>, store_thread: StoreThread) {
    let diagnostics = Arc::clone(&owner.diagnostics);
    drop(owner);
    let joined = blocking(move || store_thread.join());
    let _ = tokio::time::timeout(Duration::from_secs(5), joined).await;
    let _ = blocking(move || diagnostics.close()).await;
}

/// `desktop_update_reserve`, `desktop_update_abort` and `desktop_update_shutdown`, which
/// Python answers in the run loop itself, without a diagnostics record. Returns the reply and
/// whether the owner now closes. `busy` is Python's `workers or not display_reads.idle()`.
fn update_lifecycle(
    owner: &Owner,
    request_id: &str,
    method: &str,
    params: &JsonMap,
    busy: bool,
) -> (Json, bool) {
    let outcome = (|| -> Result<&'static str, sylvia_base::CodedError> {
        if params.len() != 1 || !params.contains_key("operation_id") {
            return Err(sylvia_base::CodedError::rejected("Unsupported update lifecycle request"));
        }
        let identity = operation_id(&params["operation_id"])?;
        let updates = owner.updates.lock().expect("updates lock").clone();
        if method == "desktop_update_abort" {
            if let Some(updates) = &updates {
                updates.abort_preparation(&identity)?;
            }
            abort_desktop_update(&owner.update_operation, &identity)?;
            return Ok("aborted");
        }
        if busy || updates.as_ref().is_some_and(|updates| updates.preparing()) {
            return Err(sylvia_base::CodedError::new(
                "update_busy",
                "Finish admitted work and loading before updating",
            ));
        }
        if method == "desktop_update_reserve" {
            begin_desktop_update(&owner.update_operation, &identity)?;
            return Ok("reserved");
        }
        let reserved = owner.update_operation.lock().expect("update reservation lock").clone();
        if reserved.as_deref() == Some(identity.as_str()) {
            if let Some(updates) = &updates {
                updates.arm_shutdown(&identity)?;
            }
            return Ok("closed");
        }
        Err(sylvia_base::CodedError::new("stale_update_operation", "This update reservation is stale"))
    })();
    match outcome {
        Ok(status) => {
            let mut result = JsonMap::new();
            result.insert("status".into(), status.into());
            (protocol::ok_reply(request_id, Json::Object(result)), status == "closed")
        }
        Err(error) => (
            protocol::error_reply(Some(request_id), protocol::plain_error(&error.code, &error.message)),
            false,
        ),
    }
}

/// `desktop_update_confirm_startup`: only an owner started to confirm an update restart
/// answers it. The native host must name the same version, target and distribution. The
/// first confirmation discards the updater's boot-time recovery projection.
async fn confirm_update_startup(owner: &Owner, params: &JsonMap) -> DispatchResult {
    let confirmed = {
        let mut startup = owner.update_startup.lock().expect("update startup lock");
        let Some(startup) = startup.as_mut().filter(|_| params.len() == 1 && params.contains_key("host"))
        else {
            return Err(Failure::protocol("No update startup is awaiting confirmation"));
        };
        let mut expected = JsonMap::new();
        expected.insert("version".into(), startup.version.clone().into());
        expected.insert("target".into(), startup.installation.target.clone().into());
        expected.insert("distribution".into(), startup.installation.distribution.clone().into());
        if params["host"] != Json::Object(expected) {
            return Err(Failure::protocol("The native startup context changed"));
        }
        startup.confirm().map_err(Failure::from)?
    };
    let already = owner.update_start_confirmed.swap(confirmed, Ordering::SeqCst);
    if confirmed && !already {
        let updates = owner.updates.lock().expect("updates lock").clone();
        if let Some(updates) = updates {
            updates.startup_completed().await;
        }
    }
    let mut result = JsonMap::new();
    result.insert("status".into(), if confirmed { "confirmed" } else { "waiting_for_helper" }.into());
    Ok(Json::Object(result))
}

/// The provider-work reservation of a model-bound command, taken before it runs as a task.
fn reserve(
    owner: &Owner,
    method: &str,
    params: &JsonMap,
) -> Result<Option<WorkGuard>, sylvia_base::CodedError> {
    let rejected = |failure: Failure| match failure {
        Failure::Rejected(error) => error,
        _ => sylvia_base::CodedError::rejected("Owner request could not be reserved"),
    };
    let Some(session_id) = generation_session(method, params).map_err(rejected)? else {
        return Ok(None);
    };
    match &owner.context.session_loop {
        Some(session_loop) => session_loop.provider_request_work(&session_id).map(Some),
        // Lane 4's session loop is not in the owner yet, so there is nothing to reserve.
        None => Ok(None),
    }
}

/// `execute_request`: run one command and build its reply. Every reply is recorded in local
/// diagnostics, and an error reply carries the record's identity as `diagnostic_id`.
async fn execute_request(
    owner: &Arc<Owner>,
    request_id: &str,
    method: &str,
    params: &JsonMap,
    reservation: Option<WorkGuard>,
) -> Json {
    let started = Instant::now();
    let outcome = match method {
        "desktop_update_confirm_startup" => confirm_update_startup(owner, params).await,
        "desktop_update_state"
        | "desktop_update_check"
        | "desktop_update_download"
        | "desktop_update_cancel"
        | "desktop_update_defer"
        | "desktop_update_prepare"
        | "desktop_update_handoff" => {
            let action = method.trim_start_matches("desktop_update_");
            owner.updates().handle(action, params).map(Json::Object).map_err(Failure::from)
        }
        "load_desktop_drafts" | "save_desktop_drafts" => drafts(owner, method, params),
        "record_local_fault" => {
            owner.diagnostics.client_fault(params.get("category"), params.get("delta")).map_err(Failure::from)
        }
        "preview_local_report" => {
            let empty = Json::String(String::new());
            owner
                .diagnostics
                .preview(
                    params.get("kind"),
                    Some(params.get("reproduction").unwrap_or(&empty)),
                    Some(params.get("feedback").unwrap_or(&empty)),
                )
                .map_err(Failure::from)
        }
        "cancel_local_report" => required_text(params, "preview_id").map(|preview_id| {
            owner.diagnostics.cancel(&preview_id);
            let mut result = JsonMap::new();
            result.insert("status".into(), "cancelled".into());
            Json::Object(result)
        }),
        "save_local_report" => save_local_report(owner, params),
        _ => {
            // A task of its own, so a handler's panic is an internal error, not a dead owner.
            let mut context = owner.context.clone();
            context.method = method.to_owned();
            // `record_provider_warning`: a `provider_warning` record for this command.
            let (warned, operation) = (Arc::clone(owner), method.to_owned());
            context.record_provider_warning = Arc::new(move |code: &str| {
                warned.diagnostics.record(
                    "provider_warning",
                    Fields { operation: Some(&operation), error_code: Some(code), ..Fields::default() },
                );
            });
            let (method, params) = (method.to_owned(), params.clone());
            match tokio::spawn(async move { dispatch::dispatch(&context, &method, &params).await }).await {
                Ok(outcome) => outcome,
                Err(_) => Err(Failure::Internal { error_type: "RuntimeError" }),
            }
        }
    };
    // Python closes the reservation in `finally`, before the reply is recorded and written.
    drop(reservation);
    let submitted =
        (method == "submit_session_input").then(|| params.get("message_id").and_then(Json::as_str)).flatten();
    let (mut reply, error) = reply_for(request_id, outcome, submitted);
    let duration_ms = started.elapsed().as_secs_f64() * 1000.0;
    let (error_code, error_type) = match &error {
        Some((code, error_type)) => (Some(code.as_str()), Some(*error_type)),
        None => (None, None),
    };
    let diagnostic_id = owner.diagnostics.record(
        "command_completed",
        Fields {
            operation: Some(method),
            outcome: Some(if error.is_some() { "error" } else { "ok" }),
            error_code,
            error_type,
            duration_ms: Some(duration_ms),
            count_delta: None,
        },
    );
    if error.is_some() {
        if let Some(object) = reply.get_mut("error").and_then(Json::as_object_mut) {
            object.insert("diagnostic_id".into(), diagnostic_id.into());
        }
    }
    reply
}

/// The reply for an outcome, and for an error its diagnostics code and exception class.
fn reply_for(
    request_id: &str,
    outcome: DispatchResult,
    submitted_message_id: Option<&str>,
) -> (Json, Option<(String, &'static str)>) {
    let internal = |error_type: &'static str| {
        eprintln!("Sylvia GM owner command failed: internal_error");
        let error = protocol::plain_error("internal_error", INTERNAL_ERROR_MESSAGE);
        (protocol::error_reply(Some(request_id), error), Some(("internal_error".to_owned(), error_type)))
    };
    match outcome {
        Err(Failure::Provider(error)) => {
            let reply = sylvia_provider::reply::provider_error_reply(&error, submitted_message_id);
            if reply.code == "internal_error" {
                // An internal error that escaped a provider is Python's `except Exception`.
                return internal(error.kind.class_name());
            }
            let error_reply = sylvia_base::CodedError::new(reply.code, reply.message).to_reply_error();
            (
                protocol::error_reply(Some(request_id), error_reply),
                Some((reply.code.to_owned(), error.kind.class_name())),
            )
        }
        Ok(result) => (protocol::ok_reply(request_id, result), None),
        Err(Failure::Rejected(error)) => {
            let code = error.code.clone();
            let error_type = error.error_type.unwrap_or("CommandRejected");
            (protocol::error_reply(Some(request_id), error.to_reply_error()), Some((code, error_type)))
        }
        Err(Failure::Internal { error_type }) => internal(error_type),
    }
}

/// `load_desktop_drafts` and `save_desktop_drafts`.
fn drafts(owner: &Owner, method: &str, params: &JsonMap) -> DispatchResult {
    let store = {
        let mut slot = owner.drafts.lock().expect("drafts lock");
        match &*slot {
            Some(store) => Arc::clone(store),
            None => {
                let store = Arc::new(DraftStore::new(&owner.context.storage_root).map_err(Failure::from)?);
                *slot = Some(Arc::clone(&store));
                store
            }
        }
    };
    let updated = |result: sylvia_base::CodedResult<Json>| result.map_err(Failure::from);
    if method == "load_desktop_drafts" && params.is_empty() {
        updated(store.load())
    } else if method == "save_desktop_drafts" && params.len() == 1 && params.contains_key("drafts") {
        updated(store.save(&params["drafts"]))
    } else {
        Err(Failure::protocol("Unsupported draft storage request"))
    }
}

/// `save_local_report`: the destination must be an absolute file path.
fn save_local_report(owner: &Owner, params: &JsonMap) -> DispatchResult {
    let destination = PathBuf::from(required_text(params, "destination_path")?);
    if !destination.is_absolute() || crate::util::path_name(&destination).is_empty() {
        return Err(Failure::protocol("Choose a local report file."));
    }
    let preview_id = required_text(params, "preview_id")?;
    owner.diagnostics.save(&preview_id, &destination).map_err(Failure::from)
}

fn startup_failed() -> i32 {
    // Never include the launch envelope, credential or database path.
    eprintln!("Sylvia GM owner startup failed");
    EXIT_STARTUP_FAILED
}

/// `_launch`, as far as it is ported, in Python's order: validate the envelope, derive the
/// external handshake reply, acquire the application owner (lane 3's `acquire_owner`: the store
/// opens on its thread, taking the owner lock and running the migrations, then the internal
/// handshake is issued) and complete the internal handshake. A failure after the store opened
/// closes it again, as `supervisor.shutdown()` does.
///
/// Before any of that, the desktop update's startup check (`owner_startup_context`) reads the
/// update folder only; it runs again under the ownership lock, before migrations, as Python's
/// `before_migration` hook. The second run's context is the one kept, as in Python.
fn launch(envelope: &JsonMap) -> Result<Launched, LaunchFailure> {
    // A context Python's recovery-only owner would serve (`run_recovery`).
    let recovery = envelope.get("desktop_update_context").is_some_and(|context| !context.is_null());
    let update_refused = |error: &sylvia_base::CodedError| LaunchFailure::Update {
        recovery: recovery && error.code == "update_recovery_required",
    };
    let checked = Arc::new(Mutex::new(
        owner_startup_context(envelope, resolve_database_path).map_err(|e| update_refused(&e))?,
    ));
    if envelope.get("type").and_then(Json::as_str) != Some("launch") {
        return Err(LaunchFailure::Other);
    }
    let text = |key: &str| envelope.get(key).and_then(Json::as_str).filter(|value| !value.is_empty());
    let secret_hex = text("secret_hex").ok_or(LaunchFailure::Other)?;
    let database_path = resolve_database_path(Path::new(text("database_path").ok_or(LaunchFailure::Other)?))
        .ok_or(LaunchFailure::Other)?;
    let owner_id = text("owner_id").ok_or(LaunchFailure::Other)?.to_owned();
    let storage_root = database_path.parent().map(Path::to_path_buf).ok_or(LaunchFailure::Other)?;
    std::fs::create_dir_all(&storage_root).map_err(|_| LaunchFailure::Other)?;
    let response_hex = derive_owner_response(secret_hex).map_err(|_| LaunchFailure::Other)?;
    let (hook_envelope, hook_checked) = (envelope.clone(), Arc::clone(&checked));
    let before_migration: sylvia_store::store::BeforeMigration = Box::new(move || {
        let context = owner_startup_context(&hook_envelope, resolve_database_path)?;
        *hook_checked.lock().expect("startup context lock") = context;
        Ok(())
    });
    let acquired = acquire_owner(&database_path, Some(before_migration)).map_err(|error| match error {
        sylvia_store::StoreError::Coded(error) if crate::desktop_update::storage::is_update_error(&error) => {
            update_refused(&error)
        }
        _ => LaunchFailure::Other,
    })?;
    let (store, store_thread, mut handshake) = (acquired.store, acquired.thread, acquired.handshake);
    // `supervisor.complete_handshake(derive_owner_response(internal.secret_hex))`.
    let verified = derive_owner_response(&acquired.credential.secret_hex)
        .and_then(|internal| handshake.verify(&internal));
    if verified.is_err() {
        drop(store);
        store_thread.join();
        return Err(LaunchFailure::Other);
    }
    let providers: Arc<dyn crate::services::ProviderResolver> =
        Arc::new(crate::providers::OwnerProviders::new(&storage_root));
    let context = DispatchContext {
        owner_id,
        storage_root,
        handshake_verified: true,
        method: String::new(),
        gm_system_prompt: None,
        store,
        providers: Some(providers),
        session_loop: None,
        bundled: Arc::new(sylvia_app::bundled_packages::BundledPackages::new(
            sylvia_app::bundled_packages::BundledSources::from_repository_root(&crate::util::source_root()),
        )),
        record_provider_warning: Arc::new(|_: &str| {}),
    };
    let (installation, startup) = std::mem::take(&mut *checked.lock().expect("startup context lock"));
    Ok(Launched { context, response_hex, store_thread, installation, startup })
}

/// A launched owner: its dispatch context, the external handshake reply, the store thread,
/// and what the launch said about updates.
struct Launched {
    context: DispatchContext,
    response_hex: String,
    store_thread: StoreThread,
    installation: Option<Installation>,
    startup: Option<AuthorizedStartup>,
}

/// Why a launch failed.
enum LaunchFailure {
    /// The update startup check refused (an `UpdateError`). `recovery` says Python would now
    /// serve its recovery-only owner instead of replying with the refusal.
    Update { recovery: bool },
    /// Anything else: Python's "Sylvia GM owner startup failed", with no reply.
    Other,
}

/// `Path(text).expanduser().resolve()`: `~` becomes the home folder, then the path is made
/// absolute with links resolved as far as it exists (Python's non-strict `resolve`).
fn resolve_database_path(path: &Path) -> Option<PathBuf> {
    let text = path.to_string_lossy();
    let expanded = match text.strip_prefix('~') {
        Some(rest) if rest.is_empty() || rest.starts_with(['/', '\\']) => {
            let home = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })?;
            PathBuf::from(home).join(rest.trim_start_matches(['/', '\\']))
        }
        _ => path.to_path_buf(),
    };
    let absolute =
        if expanded.is_absolute() { expanded } else { std::env::current_dir().ok()?.join(expanded) };
    // Resolve the longest existing ancestor, then append the rest unchanged.
    let mut existing = absolute.as_path();
    let mut rest = Vec::new();
    loop {
        if let Ok(resolved) = std::fs::canonicalize(existing) {
            let mut resolved = PathBuf::from(plain(&resolved));
            for part in rest.iter().rev() {
                resolved.push(part);
            }
            return Some(resolved);
        }
        rest.push(existing.file_name()?.to_os_string());
        existing = existing.parent()?;
    }
}

/// A path without Windows' `\\?\` prefix, as Python prints it.
fn plain(path: &Path) -> String {
    let text = path.to_string_lossy().into_owned();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{rest}")
    } else {
        text.strip_prefix(r"\\?\").map(str::to_owned).unwrap_or(text)
    }
}

/// A validated request: its id, method, parameters and display load identity.
type Admitted = (String, String, JsonMap, Option<String>);

/// The request checks `run` makes before admitting a request. Messages are Python's.
fn validate(request: &JsonMap, id: Option<&str>) -> Result<Admitted, String> {
    let id = id.ok_or("Owner request id must be a nonempty string")?.to_owned();
    let method = request
        .get("method")
        .and_then(Json::as_str)
        .filter(|method| !method.is_empty())
        .ok_or("method must be a nonempty string")?
        .to_owned();
    let params = match request.get("params") {
        None => JsonMap::new(),
        Some(Json::Object(params)) => params.clone(),
        Some(_) => return Err("Owner request params must be an object".into()),
    };
    let load_id = if method == "cancel_display_load" {
        match params.get("load_id").and_then(Json::as_str).filter(|value| !value.is_empty()) {
            Some(load_id) => Some(Json::String(load_id.to_owned())),
            None => return Err("load_id must be a nonempty string".into()),
        }
    } else if DISPLAY_READ_METHODS.contains(&method.as_str()) {
        params.get("load_id").filter(|value| !value.is_null()).cloned()
    } else {
        None
    };
    let load_id = match load_id {
        None => None,
        // Python measures the identity in characters, not bytes.
        Some(Json::String(text)) if (1..=128).contains(&text.chars().count()) => Some(text),
        Some(_) => return Err("Display load identity must be a bounded string".into()),
    };
    Ok((id, method, params, load_id))
}

fn send_invalid(replies: &Replies, id: Option<&str>, message: &str) {
    let error = protocol::plain_error("invalid_owner_request", message);
    replies.send(protocol::error_reply(id, error));
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use sylvia_provider::error::{ErrorKind, ProviderError};

    fn params(value: Json) -> JsonMap {
        value.as_object().cloned().unwrap()
    }

    fn message(failure: Failure) -> String {
        match failure {
            Failure::Rejected(error) => error.message,
            other => panic!("expected a rejection, got {other:?}"),
        }
    }

    #[test]
    fn model_commands_parse_like_python() {
        assert_eq!(model_slash_command("/MODEL gpt --once").unwrap(), Some((Some("gpt".into()), "once")));
        assert_eq!(model_slash_command("  /model\u{1c}").unwrap(), Some((None, "current")));
        assert_eq!(model_slash_command("/models x").unwrap(), None);
        assert_eq!(model_slash_command("I look around").unwrap(), None);
        assert_eq!(
            message(model_slash_command("/model a b c d").unwrap_err()),
            "Model command accepts one model and one optional mode"
        );
        assert_eq!(message(model_slash_command("/model a b").unwrap_err()), "Model command is ambiguous");
        assert_eq!(
            message(model_slash_command("/model --soon").unwrap_err()),
            "Model command mode is unsupported"
        );
    }

    #[test]
    fn only_model_bound_commands_reserve_their_session() {
        let submit = params(json!({"session_id": "s", "user_text": "hello", "ooc_text": null}));
        assert_eq!(generation_session("submit_session_input", &submit).unwrap(), Some("s".into()));
        let slash = params(json!({"session_id": "s", "user_text": "/model x"}));
        assert_eq!(generation_session("submit_session_input", &slash).unwrap(), None);
        let slash_with_ooc = params(json!({"session_id": "s", "user_text": "/model x", "ooc_text": "aside"}));
        assert_eq!(generation_session("submit_session_input", &slash_with_ooc).unwrap(), Some("s".into()));
        assert_eq!(generation_session("ready_campaign_catalog", &JsonMap::new()).unwrap(), None);
        assert_eq!(
            message(generation_session("start_dm_session", &JsonMap::new()).unwrap_err()),
            "session_id must be a nonempty string"
        );
    }

    #[test]
    fn update_gates_refuse_with_pythons_replies() {
        assert_eq!(UpdateGates::default().refusal("r", "submit_session_input"), None);
        let startup = UpdateGates { startup_unconfirmed: true, reserved_operation: None };
        assert_eq!(startup.refusal("r", "campaign_index"), None);
        assert_eq!(
            startup.refusal("r", "submit_session_input").unwrap().to_string(),
            r#"{"id":"r","ok":false,"error":{"code":"update_startup_unconfirmed","message":"Wait for the updated application to confirm startup."}}"#
        );
        let reserved = UpdateGates { startup_unconfirmed: false, reserved_operation: Some("op".into()) };
        assert_eq!(reserved.refusal("r", "builder_chat_index"), None);
        assert_eq!(reserved.refusal("r", "model_choices").unwrap()["error"]["code"], "update_in_progress");
    }

    #[test]
    fn provider_errors_take_lane_4s_sentence_and_record_their_class() {
        let error = ProviderError::connection("refused", None).with_accepted_message_id("m1");
        let (reply, recorded) = reply_for("r", Err(Failure::Provider(error.clone())), Some("m1"));
        assert_eq!(reply["error"]["code"], "provider_error");
        assert!(reply["error"]["message"]
            .as_str()
            .unwrap()
            .starts_with("Your message was saved. Sylvia could not connect"));
        assert_eq!(reply["error"]["diagnostics"], json!([]));
        assert_eq!(recorded, Some(("provider_error".to_owned(), "ProviderConnectionError")));
        let (other, _) = reply_for("r", Err(Failure::Provider(error)), None);
        assert!(other["error"]["message"].as_str().unwrap().starts_with("Sylvia could not connect"));
        let compaction = ProviderError::new(ErrorKind::ContextCompaction, "Could not compact");
        let (reply, recorded) = reply_for("r", Err(Failure::Provider(compaction)), None);
        assert_eq!(reply["error"]["code"], "context_compaction_failed");
        assert_eq!(reply["error"]["message"], "Could not compact");
        assert_eq!(recorded.unwrap().0, "context_compaction_failed");
    }

    #[test]
    fn an_internal_error_from_a_provider_is_pythons_except_exception() {
        let escaped = ProviderError::new(ErrorKind::Internal, "unexpected keyword argument 'reasoning'");
        let (reply, recorded) = reply_for("r", Err(Failure::Provider(escaped)), Some("m1"));
        assert_eq!(reply["error"], json!({"code": "internal_error", "message": INTERNAL_ERROR_MESSAGE}));
        assert_eq!(recorded, Some(("internal_error".to_owned(), "Exception")));
    }

    #[test]
    fn an_internal_fault_has_the_fixed_reply_and_no_diagnostics_list() {
        let (reply, recorded) = reply_for("r", Err(Failure::Internal { error_type: "KeyError" }), None);
        assert_eq!(reply["error"], json!({"code": "internal_error", "message": INTERNAL_ERROR_MESSAGE}));
        assert_eq!(recorded, Some(("internal_error".to_owned(), "KeyError")));
    }
}
