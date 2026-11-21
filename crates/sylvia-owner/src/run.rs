//! The owner's run loop: `owner_process.run` in Python.
//!
//! Concurrency model for the whole owner (see `docs/rust-port/README.md`):
//! - one tokio runtime;
//! - one blocking thread reads stdin and hands frames to the loop over a channel;
//! - one task owns stdout, so replies never interleave (Python's `write_lock`);
//! - each admitted request runs as its own task and replies when it finishes, in any order.

use crate::commands::RUN_LOOP_METHODS;
use crate::dispatch::{self, DispatchContext, PORT_PENDING};
use crate::handshake::derive_owner_response;
use crate::protocol::{self, Frame};
use std::io::{BufRead, Write};
use std::path::PathBuf;
use sylvia_base::{Json, JsonMap};
use tokio::sync::mpsc;

/// `_DISPLAY_READ_METHODS`.
const DISPLAY_READ_METHODS: &[&str] =
    &["display_snapshot", "player_projection", "player_projection_page", "campaign_panels"];

/// Exit code of a clean shutdown.
pub const EXIT_OK: i32 = 0;
/// Exit code Python's `main` returns when startup fails.
pub const EXIT_STARTUP_FAILED: i32 = 2;

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

    let code = serve(&mut frames, &reply_sender).await;
    // Dropping the sender lets the writer drain what is queued and stop.
    drop(reply_sender);
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

async fn serve(frames: &mut mpsc::UnboundedReceiver<Frame>, replies: &mpsc::UnboundedSender<Json>) -> i32 {
    // Launch: the first message must be a valid launch envelope. Any failure here ends the
    // owner without a reply, as Python does ("Sylvia GM owner startup failed" on stderr).
    let context = match frames.recv().await {
        Some(Frame::Message(envelope)) => match launch(&envelope) {
            Ok((context, response_hex)) => {
                let mut ready = JsonMap::new();
                ready.insert("ok".into(), Json::Bool(true));
                ready.insert("type".into(), Json::String("ready".into()));
                ready.insert("response_hex".into(), Json::String(response_hex));
                let _ = replies.send(Json::Object(ready));
                context
            }
            Err(()) => return startup_failed(),
        },
        _ => return startup_failed(),
    };

    let (done_sender, mut done) = mpsc::unbounded_channel::<()>();
    let mut running = 0usize;
    let code = loop {
        let frame = tokio::select! {
            frame = frames.recv() => frame,
            Some(()) = done.recv() => { running -= 1; continue; }
        };
        let request = match frame {
            None | Some(Frame::End) => break EXIT_OK,
            Some(Frame::Fatal(_)) => break EXIT_STARTUP_FAILED,
            Some(Frame::Invalid(message)) => {
                send_invalid(replies, None, message);
                continue;
            }
            Some(Frame::Message(request)) => request,
        };
        let id = request.get("id").and_then(Json::as_str).filter(|id| !id.is_empty());
        let (id, method, params) = match validate(&request, id) {
            Ok(parts) => parts,
            Err(message) => {
                send_invalid(replies, id, &message);
                continue;
            }
        };

        if method == "shutdown" {
            let mut result = JsonMap::new();
            result.insert("status".into(), Json::String("closed".into()));
            let _ = replies.send(protocol::ok_reply(&id, Json::Object(result)));
            break EXIT_OK;
        }
        if RUN_LOOP_METHODS.contains(&method.as_str()) || is_local_method(&method) {
            let error =
                protocol::plain_error(PORT_PENDING, &format!("The Rust owner does not answer {method} yet."));
            let _ = replies.send(protocol::error_reply(Some(&id), error));
            continue;
        }

        running += 1;
        let context = context.clone();
        let replies = replies.clone();
        let done_sender = done_sender.clone();
        tokio::spawn(async move {
            let reply = match dispatch::dispatch(&context, &method, &params).await {
                Ok(result) => protocol::ok_reply(&id, result),
                Err(error) => protocol::error_reply(Some(&id), error.to_reply_error()),
            };
            let _ = replies.send(reply);
            let _ = done_sender.send(());
        });
    };

    // Python joins its workers for up to five seconds before closing.
    let deadline = tokio::time::sleep(std::time::Duration::from_secs(5));
    tokio::pin!(deadline);
    while running > 0 {
        tokio::select! {
            Some(()) = done.recv() => running -= 1,
            () = &mut deadline => break,
        }
    }
    code
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
    let storage_root = database_path.parent().map(PathBuf::from).ok_or(())?;
    std::fs::create_dir_all(&storage_root).map_err(|_| ())?;
    Ok((DispatchContext { owner_id, storage_root }, response_hex))
}

/// The request checks `run` makes before admitting a request. Messages are Python's.
fn validate(request: &JsonMap, id: Option<&str>) -> Result<(String, String, JsonMap), String> {
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
    if let Some(load_id) = load_id {
        // Python measures the identity in characters, not bytes.
        let bounded = load_id.as_str().is_some_and(|text| (1..=128).contains(&text.chars().count()));
        if !bounded {
            return Err("Display load identity must be a bounded string".into());
        }
    }
    Ok((id, method, params))
}

/// Registry entries without a handler: the run loop answers them from local services.
fn is_local_method(method: &str) -> bool {
    matches!(
        method,
        "cancel_display_load"
            | "cancel_local_report"
            | "load_desktop_drafts"
            | "preview_local_report"
            | "record_local_fault"
            | "renderer"
            | "save_desktop_drafts"
            | "save_local_report"
    )
}

fn send_invalid(replies: &mpsc::UnboundedSender<Json>, id: Option<&str>, message: &str) {
    let error = protocol::plain_error("invalid_owner_request", message);
    let _ = replies.send(protocol::error_reply(id, error));
}
