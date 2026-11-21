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
//! Not ported yet (pull request 3 of lane 5): provider-work reservation for those seven, the
//! update-startup and update-in-progress gates, the provider error sentences and the desktop
//! update methods.

use crate::commands::RUN_LOOP_METHODS;
use crate::desktop_update::drafts::DraftStore;
use crate::diagnostics::{Fields, LocalDiagnostics};
use crate::dispatch::{self, required_text, DispatchContext, DispatchResult, Failure, PORT_PENDING};
use crate::display_reads::DisplayReads;
use crate::handshake::derive_owner_response;
use crate::protocol::{self, Frame};
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use sylvia_base::{Json, JsonMap};
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
    std::thread::Builder::new()
        .name("sylvia-owner-stdin".into())
        .spawn(move || read_frames(input, &frame_sender))
        .expect("the stdin reader thread starts");

    let (reply_sender, replies) = mpsc::unbounded_channel::<Json>();
    let writer = tokio::task::spawn_blocking(move || write_replies(output, replies));
    let replies = Replies { sender: reply_sender, closing: Arc::new(AtomicBool::new(false)) };

    let code = serve(&mut frames, &replies).await;
    // Dropping the last sender lets the writer drain what is queued and stop.
    drop(replies);
    let _ = writer.await;
    code
}

fn read_frames(mut input: impl BufRead, frames: &mpsc::UnboundedSender<Frame>) {
    loop {
        let frame = protocol::read_message(&mut input);
        let last = matches!(frame, Frame::End | Frame::Fatal(_));
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
    diagnostics: LocalDiagnostics,
    /// `desktop_drafts`, opened on first use.
    drafts: Mutex<Option<Arc<DraftStore>>>,
}

async fn serve(frames: &mut mpsc::UnboundedReceiver<Frame>, replies: &Replies) -> i32 {
    // Launch: the first message must be a valid launch envelope. Any failure here ends the
    // owner without a reply, as Python does ("Sylvia GM owner startup failed" on stderr).
    let (context, response_hex) = match frames.recv().await {
        Some(Frame::Message(envelope)) => match launch(&envelope) {
            Ok(launched) => launched,
            Err(()) => return startup_failed(),
        },
        _ => return startup_failed(),
    };
    let owner = Arc::new(Owner {
        diagnostics: LocalDiagnostics::new(&context.storage_root),
        context,
        drafts: Mutex::new(None),
    });
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
        };
        let id = request.get("id").and_then(Json::as_str).filter(|id| !id.is_empty());
        let (id, method, params, load_id) = match validate(&request, id) {
            Ok(parts) => parts,
            Err(message) => {
                send_invalid(replies, id, &message);
                continue;
            }
        };

        if method == "shutdown" {
            let mut result = JsonMap::new();
            result.insert("status".into(), Json::String("closed".into()));
            replies.send(protocol::ok_reply(&id, Json::Object(result)));
            break EXIT_OK;
        }
        if RUN_LOOP_METHODS.contains(&method.as_str()) {
            let error =
                protocol::plain_error(PORT_PENDING, &format!("The Rust owner does not answer {method} yet."));
            replies.send(protocol::error_reply(Some(&id), error));
            continue;
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
                Box::pin(async move { execute_request(&owner, &request_id, &method, &params).await }),
            );
            continue;
        }
        if WORKER_METHODS.contains(&method.as_str()) {
            let owner = Arc::clone(&owner);
            let replies = replies.clone();
            workers.spawn(async move {
                let reply = execute_request(&owner, &id, &method, &params).await;
                replies.send(reply);
            });
            continue;
        }
        let reply = execute_request(&owner, &id, &method, &params).await;
        replies.send(reply);
    };

    // Python's `finally`: stop writing, let running display reads finish, give workers five
    // seconds, then the last diagnostics write.
    replies.closing.store(true, Ordering::SeqCst);
    display_reads.close().await;
    let deadline = tokio::time::sleep(Duration::from_secs(5));
    tokio::pin!(deadline);
    while !workers.is_empty() {
        tokio::select! {
            _ = workers.join_next() => {}
            () = &mut deadline => break,
        }
    }
    let diagnostics_owner = Arc::clone(&owner);
    let _ = tokio::task::spawn_blocking(move || diagnostics_owner.diagnostics.close()).await;
    code
}

/// `execute_request`: run one command and build its reply. Every reply is recorded in local
/// diagnostics, and an error reply carries the record's identity as `diagnostic_id`.
async fn execute_request(owner: &Arc<Owner>, request_id: &str, method: &str, params: &JsonMap) -> Json {
    let started = Instant::now();
    let outcome = match method {
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
            let (context, method, params) = (owner.context.clone(), method.to_owned(), params.clone());
            match tokio::spawn(async move { dispatch::dispatch(&context, &method, &params).await }).await {
                Ok(outcome) => outcome,
                Err(_) => Err(Failure::Internal { error_type: "RuntimeError" }),
            }
        }
    };
    let (mut reply, error) = reply_for(request_id, outcome);
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
fn reply_for(request_id: &str, outcome: DispatchResult) -> (Json, Option<(String, &'static str)>) {
    match outcome {
        Ok(result) => (protocol::ok_reply(request_id, result), None),
        Err(Failure::Rejected(error)) => {
            let code = error.code.clone();
            let error_type = error.error_type.unwrap_or("CommandRejected");
            (protocol::error_reply(Some(request_id), error.to_reply_error()), Some((code, error_type)))
        }
        Err(Failure::Internal { error_type }) => {
            eprintln!("Sylvia GM owner command failed: internal_error");
            let error = protocol::plain_error("internal_error", INTERNAL_ERROR_MESSAGE);
            (protocol::error_reply(Some(request_id), error), Some(("internal_error".into(), error_type)))
        }
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

/// `_launch`, as far as it is ported: validate the envelope and derive the handshake reply.
///
/// Not ported yet (lanes 3 and 5): update-startup validation, acquiring the application lock,
/// opening and migrating the store, installing the bundled packages. Until those exist the
/// Rust owner must not be given a database that matters.
fn launch(envelope: &JsonMap) -> Result<(DispatchContext, String), ()> {
    if envelope.get("type").and_then(Json::as_str) != Some("launch") {
        return Err(());
    }
    let text = |key: &str| envelope.get(key).and_then(Json::as_str).filter(|value| !value.is_empty());
    let secret_hex = text("secret_hex").ok_or(())?;
    let database_path = PathBuf::from(text("database_path").ok_or(())?);
    let owner_id = text("owner_id").ok_or(())?.to_owned();
    let response_hex = derive_owner_response(secret_hex).map_err(|_| ())?;
    let storage_root = database_path.parent().map(Path::to_path_buf).ok_or(())?;
    std::fs::create_dir_all(&storage_root).map_err(|_| ())?;
    Ok((DispatchContext { owner_id, storage_root, handshake_verified: true }, response_hex))
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
