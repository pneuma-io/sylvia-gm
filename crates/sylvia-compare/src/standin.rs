//! A stand-in model service that records every request the owner sends.
//!
//! It answers the OpenAI-compatible chat API the way `scripts/qa/standin_model_endpoint.py`
//! does: one model, fixed narration, and a Campaign File tool call when that tool is offered
//! and the last message is not a tool result. Unlike that script it keeps the whole request,
//! headers and body, because what the model is sent is one of the things the port must
//! reproduce. No real model, credential or network is involved.
//!
//! A session can script a reply by a marker in the latest player message, as `JourneyGM` in
//! `tests/stop_resume_owner_fixture.py` and `tests/stop_resume/scripted-owner.cjs` do:
//!
//! - `[hold]` sends a partial chunk, then holds the connection until the owner closes it (Stop).
//! - `[fail]` sends a partial chunk and drops the connection mid-stream, the first time a given
//!   player message is seen; the owner sees a lost connection. Later requests are answered.
//! - `[late]` holds before answering until the session releases it, then answers.
//! - `[call:<suffix> <json>]` makes the GM call a tool: on a request that offers a tool named
//!   `<suffix>` or ending `_<suffix>` (the first such tool, in request order), the stand-in
//!   answers with a call to it whose arguments are `<json>` byte for byte. A message may carry
//!   several; they are called in order, one a round. Once the last one's result is the last
//!   message (or a marker's tool is not offered), the stand-in answers as it would without
//!   them: the Campaign File update, then the narration. A marker call's id is
//!   `standin-mechanic-<n>`, `<n>` counting the request's assistant tool-call messages, so every
//!   platform's recording agrees. The marker is left out of the echoed narration.
//!
//! The session drives the holds with two step kinds (see `session.rs`): `wait_held` blocks
//! until the stand-in holds one more request than it had been waited for, and `release`
//! lets every `[late]` request being held answer.
//!
//! The server is a small HTTP/1.1 implementation on `std::net`, because holding a stream open
//! and dropping one mid-body need control over the socket that an HTTP library hides.

use regex::Regex;
use serde::Deserialize;
use serde_json::json;
use std::collections::HashSet;
use std::io::{BufRead, BufReader, ErrorKind, Read, Write};
use std::net::{Shutdown, SocketAddr, TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex, OnceLock};
use std::time::{Duration, Instant};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_compare::dbdump::encoding_of;

/// Model identifier the stand-in lists.
pub const MODEL: &str = "standin-narrator";
/// API key the owner must present.
pub const KEY: &str = "standin-key";
const CONTEXT_LENGTH: u64 = 4_000_000;
const NARRATION: &str = "The stand-in narrator answers. A lantern sways over the crossroads inn as you arrive, and the keeper waves you toward the fire. What do you do?";
const TURN_REPLY: &str = "The stand-in narrator resolves your action. The keeper nods, pours a cup of tea and slides it across the table. What do you do next?";
const CAMPAIGN_FILE_TOOL: &str = "update_campaign_file";
/// Fixed so that anything the owner stores from a reply is the same on every run.
const CREATED: u64 = 1_700_000_000;

/// Markers in the latest player message, as in `tests/stop_resume_owner_fixture.py`.
const HOLD: &str = "[hold]";
const FAIL: &str = "[fail]";
const LATE: &str = "[late]";
/// `PARTIAL` in the fixture: what a held or failed reply streams before stopping.
const PARTIAL: &str = "Partial narration that Stop must discard.";
/// How often a held connection checks whether the owner closed it.
const POLL: Duration = Duration::from_millis(25);
/// Largest request head or body the stand-in reads.
const MAX_REQUEST_BYTES: usize = 64 * 1024 * 1024;

/// What the stand-in narrates.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Narration {
    /// The fixed narration of `scripts/qa/standin_model_endpoint.py`.
    #[default]
    Fixed,
    /// `scripted_reply` of the Stop and resume fixture: "The Game Master answers: " and the
    /// player's message without its markers, so a recording shows which message was answered.
    Journey,
}

#[derive(Default)]
struct Holds {
    /// Requests that have started holding, ever.
    started: usize,
    /// How many of those a `wait_held` step has already waited for.
    awaited: usize,
    /// Incremented by every `release`; a `[late]` hold ends when it moves past its start.
    releases: u64,
}

struct State {
    narration: Narration,
    requests: Mutex<Vec<Json>>,
    /// Player messages that already failed once.
    failed: Mutex<HashSet<String>>,
    holds: Mutex<Holds>,
    changed: Condvar,
    closing: AtomicBool,
    /// Every accepted connection, so `finish` can close the ones still held.
    connections: Mutex<Vec<TcpStream>>,
}

/// A running stand-in.
pub struct Standin {
    state: Arc<State>,
    address: SocketAddr,
    acceptor: Option<std::thread::JoinHandle<()>>,
    /// Base URL to give the owner, ending in `/v1`.
    pub url: String,
}

impl Standin {
    /// Listen on a free loopback port.
    pub fn start(narration: Narration) -> Result<Self, String> {
        let listener = TcpListener::bind("127.0.0.1:0")
            .map_err(|error| format!("stand-in could not listen: {error}"))?;
        let address = listener.local_addr().map_err(|error| format!("stand-in has no address: {error}"))?;
        let state = Arc::new(State {
            narration,
            requests: Mutex::new(Vec::new()),
            failed: Mutex::new(HashSet::new()),
            holds: Mutex::new(Holds::default()),
            changed: Condvar::new(),
            closing: AtomicBool::new(false),
            connections: Mutex::new(Vec::new()),
        });
        let acceptor = {
            let state = Arc::clone(&state);
            std::thread::spawn(move || accept(&listener, &state))
        };
        Ok(Self {
            state,
            address,
            acceptor: Some(acceptor),
            url: format!("http://127.0.0.1:{}/v1", address.port()),
        })
    }

    /// Wait until one more request is being held than earlier calls waited for.
    pub fn wait_held(&self, timeout: Duration) -> Result<(), String> {
        let deadline = Instant::now() + timeout;
        let mut holds = self.state.holds.lock().expect("hold lock");
        while holds.started <= holds.awaited {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(format!("the stand-in held no new request within {} s", timeout.as_secs()));
            }
            holds = self.state.changed.wait_timeout(holds, remaining).expect("hold lock").0;
        }
        holds.awaited += 1;
        Ok(())
    }

    /// Let every `[late]` request being held answer.
    pub fn release(&self) {
        self.state.holds.lock().expect("hold lock").releases += 1;
        self.state.changed.notify_all();
    }

    /// Stop listening, close every connection and return every recorded request, in arrival
    /// order.
    pub fn finish(mut self) -> Vec<Json> {
        self.state.closing.store(true, Ordering::SeqCst);
        self.state.changed.notify_all();
        // Wake the acceptor, which is blocked in `accept`.
        let _ = TcpStream::connect_timeout(&self.address, Duration::from_secs(5));
        if let Some(acceptor) = self.acceptor.take() {
            let _ = acceptor.join();
        }
        for connection in self.state.connections.lock().expect("connection lock").drain(..) {
            let _ = connection.shutdown(Shutdown::Both);
        }
        std::mem::take(&mut *self.state.requests.lock().expect("request log lock"))
    }
}

fn accept(listener: &TcpListener, state: &Arc<State>) {
    for stream in listener.incoming() {
        if state.closing.load(Ordering::SeqCst) {
            return;
        }
        let Ok(stream) = stream else { continue };
        if let Ok(copy) = stream.try_clone() {
            state.connections.lock().expect("connection lock").push(copy);
        }
        let state = Arc::clone(state);
        std::thread::spawn(move || serve_connection(stream, &state));
    }
}

/// One HTTP request as received.
struct Request {
    method: String,
    path: String,
    /// In the order and spelling received.
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

impl Request {
    fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(key, _)| key.eq_ignore_ascii_case(name)).map(|(_, value)| value.as_str())
    }
}

fn serve_connection(stream: TcpStream, state: &State) {
    let Ok(reading) = stream.try_clone() else { return };
    let mut reader = BufReader::new(reading);
    while let Some(request) = read_request(&mut reader) {
        let keep_open = respond(&stream, &request, state);
        if !keep_open || state.closing.load(Ordering::SeqCst) {
            break;
        }
    }
    let _ = stream.shutdown(Shutdown::Both);
}

fn read_line(reader: &mut impl BufRead) -> Option<String> {
    let mut line = Vec::new();
    match reader.take(MAX_REQUEST_BYTES as u64).read_until(b'\n', &mut line) {
        Ok(0) | Err(_) => None,
        Ok(_) => {
            let text = String::from_utf8(line).ok()?;
            Some(text.trim_end_matches(['\r', '\n']).to_owned())
        }
    }
}

fn read_request(reader: &mut impl BufRead) -> Option<Request> {
    let start = read_line(reader)?;
    let mut parts = start.split(' ');
    let method = parts.next()?.to_owned();
    let path = parts.next()?.to_owned();
    let mut headers = Vec::new();
    loop {
        let line = read_line(reader)?;
        if line.is_empty() {
            break;
        }
        let (name, value) = line.split_once(':')?;
        headers.push((name.to_owned(), value.trim().to_owned()));
    }
    let mut request = Request { method, path, headers, body: Vec::new() };
    if request.header("Transfer-Encoding").is_some_and(|value| value.eq_ignore_ascii_case("chunked")) {
        loop {
            let size = read_line(reader)?;
            let size = usize::from_str_radix(size.split(';').next()?.trim(), 16).ok()?;
            if size == 0 {
                // Trailers, then the blank line.
                while !read_line(reader)?.is_empty() {}
                break;
            }
            // The size comes from the request: an overflowing sum is refused, never wrapped.
            if request.body.len().checked_add(size).is_none_or(|total| total > MAX_REQUEST_BYTES) {
                return None;
            }
            let mut chunk = vec![0; size];
            reader.read_exact(&mut chunk).ok()?;
            request.body.extend_from_slice(&chunk);
            read_line(reader)?;
        }
    } else if let Some(length) = request.header("Content-Length") {
        let length: usize = length.parse().ok()?;
        if length > MAX_REQUEST_BYTES {
            return None;
        }
        request.body = vec![0; length];
        reader.read_exact(&mut request.body).ok()?;
    }
    Some(request)
}

/// The recorded form of a request: everything but the credential itself.
fn record(request: &Request, authorised: bool) -> Json {
    let headers = request
        .headers
        .iter()
        .map(|(name, value)| {
            let value =
                if name.eq_ignore_ascii_case("Authorization") { Json::Null } else { value.clone().into() };
            Json::Array(vec![name.clone().into(), value])
        })
        .collect();
    let (encoding, body) = if request.body.is_empty() {
        ("none", Json::Null)
    } else {
        match std::str::from_utf8(&request.body).ok().and_then(|text| {
            serde_json::from_str::<Json>(text).ok().map(|parsed| (encoding_of(text, &parsed), parsed))
        }) {
            Some((encoding, parsed)) => (encoding, parsed),
            None => ("other", Json::Null),
        }
    };
    let mut entry = JsonMap::new();
    entry.insert("method".into(), request.method.clone().into());
    entry.insert("path".into(), request.path.clone().into());
    entry.insert("authorised".into(), authorised.into());
    entry.insert("headers".into(), Json::Array(headers));
    entry.insert("body_encoding".into(), encoding.into());
    entry.insert("body".into(), body);
    Json::Object(entry)
}

/// Answer one request. Returns whether the connection may carry another.
fn respond(stream: &TcpStream, request: &Request, state: &State) -> bool {
    let authorised = request.header("Authorization") == Some(format!("Bearer {KEY}").as_str());
    state.requests.lock().expect("request log lock").push(record(request, authorised));
    let body: Json = std::str::from_utf8(&request.body)
        .ok()
        .and_then(|text| serde_json::from_str(text).ok())
        .unwrap_or(Json::Null);

    let trimmed = request.path.trim_end_matches('/');
    if !authorised {
        return write_json(stream, 401, &json!({"error": {"message": "stand-in: wrong or missing API key"}}));
    }
    if request.method == "GET" && trimmed.ends_with("/models") {
        return write_json(
            stream,
            200,
            &json!({"object": "list", "data": [
                {"id": MODEL, "object": "model", "context_length": CONTEXT_LENGTH},
            ]}),
        );
    }
    if !(request.method == "POST" && trimmed.ends_with("/chat/completions")) {
        return write_json(stream, 404, &json!({"error": {"message": "stand-in: unknown path"}}));
    }

    let streaming = body.get("stream").and_then(Json::as_bool).unwrap_or(false);
    let player = latest_player_message(&body);
    match script(&player, state) {
        Script::Hold => {
            if streaming {
                start_chunked_stream(stream, &[role_chunk(), content_chunk(PARTIAL)]);
            }
            hold(stream, state, false);
            false
        }
        Script::Fail => {
            if streaming {
                start_chunked_stream(stream, &[role_chunk(), content_chunk(PARTIAL)]);
            }
            // Without the closing chunk: the owner sees the connection lost mid-body.
            let _ = stream.shutdown(Shutdown::Both);
            false
        }
        Script::Late => {
            if hold(stream, state, true) {
                answer(stream, &body, &player, state, streaming)
            } else {
                false
            }
        }
        Script::Answer => answer(stream, &body, &player, state, streaming),
    }
}

enum Script {
    Answer,
    Hold,
    Fail,
    Late,
}

/// JourneyGM's order: hold first, then fail once per message.
fn script(player: &str, state: &State) -> Script {
    if player.contains(HOLD) {
        Script::Hold
    } else if player.contains(FAIL) && state.failed.lock().expect("failed lock").insert(player.to_owned()) {
        Script::Fail
    } else if player.contains(LATE) {
        Script::Late
    } else {
        Script::Answer
    }
}

/// Hold the connection until the owner closes it, the stand-in finishes, or (`late`) the
/// session releases it. Returns true only when released.
fn hold(stream: &TcpStream, state: &State, late: bool) -> bool {
    let start = {
        let mut holds = state.holds.lock().expect("hold lock");
        holds.started += 1;
        holds.releases
    };
    state.changed.notify_all();
    let _ = stream.set_read_timeout(Some(POLL));
    let mut probe = [0u8; 256];
    let mut reading = stream;
    loop {
        if state.closing.load(Ordering::SeqCst) {
            return false;
        }
        if late && state.holds.lock().expect("hold lock").releases > start {
            let _ = stream.set_read_timeout(None);
            return true;
        }
        match reading.read(&mut probe) {
            Ok(0) => return false,
            Ok(_) => {}
            Err(error) if matches!(error.kind(), ErrorKind::WouldBlock | ErrorKind::TimedOut) => {}
            Err(_) => return false,
        }
    }
}

fn answer(stream: &TcpStream, body: &Json, player: &str, state: &State, streaming: bool) -> bool {
    let reply = plan_reply(body, player, state.narration);
    if streaming {
        write_stream(stream, &reply);
        false
    } else {
        write_json(stream, 200, &completion_body(&reply))
    }
}

/// The player's text: the newest user message the application did not seal as its own
/// record (`latest_player_message` in `tests/gm_context_contract.py`).
fn latest_player_message(request: &Json) -> String {
    static SEAL: OnceLock<Regex> = OnceLock::new();
    let seal = SEAL.get_or_init(|| Regex::new(r"^\[host-seal [0-9a-f]{12}\] ").expect("static pattern"));
    let Some(messages) = request.get("messages").and_then(Json::as_array) else {
        return String::new();
    };
    messages
        .iter()
        .rev()
        .filter(|message| message.get("role").and_then(Json::as_str) == Some("user"))
        .map(|message| content_text(message.get("content")))
        .find(|content| !seal.is_match(content))
        .unwrap_or_default()
}

fn content_text(content: Option<&Json>) -> String {
    match content {
        Some(Json::String(text)) => text.clone(),
        Some(Json::Array(parts)) => {
            parts.iter().filter_map(|part| part.get("text").and_then(Json::as_str)).collect()
        }
        _ => String::new(),
    }
}

/// One `[call:<suffix> <json>]` marker: the tool suffix, the arguments as written, and where
/// the marker is in the message (byte offsets).
#[derive(Debug, Clone, PartialEq, Eq)]
struct CallMarker {
    suffix: String,
    arguments: String,
    span: (usize, usize),
}

/// The `[call:...]` markers of a player message, in order. A marker is `[call:`, a suffix of
/// ASCII letters, digits and underscores, one space, one JSON value and `]`; anything else is
/// plain text.
fn call_markers(text: &str) -> Vec<CallMarker> {
    const OPEN: &str = "[call:";
    let mut markers = Vec::new();
    let mut from = 0;
    while let Some(found) = text[from..].find(OPEN) {
        let start = from + found;
        from = start + OPEN.len();
        let rest = &text[from..];
        let suffix_len = rest.find(|c: char| !(c.is_ascii_alphanumeric() || c == '_')).unwrap_or(rest.len());
        if suffix_len == 0 || !rest[suffix_len..].starts_with(' ') {
            continue;
        }
        let json_start = from + suffix_len + 1;
        let mut values = serde_json::Deserializer::from_str(&text[json_start..]).into_iter::<Json>();
        let Some(Ok(_)) = values.next() else { continue };
        let json_end = json_start + values.byte_offset();
        if !text[json_end..].starts_with(']') {
            continue;
        }
        markers.push(CallMarker {
            suffix: rest[..suffix_len].to_owned(),
            arguments: text[json_start..json_end].to_owned(),
            span: (start, json_end + 1),
        });
        from = json_end + 1;
    }
    markers
}

/// `text` without its `[call:...]` markers.
fn without_call_markers(text: &str) -> String {
    let mut kept = String::new();
    let mut from = 0;
    for marker in call_markers(text) {
        kept.push_str(&text[from..marker.span.0]);
        from = marker.span.1;
    }
    kept.push_str(&text[from..]);
    kept
}

/// The first offered tool whose name is `suffix` or ends `_<suffix>`.
fn offered_tool(request: &Json, suffix: &str) -> Option<String> {
    request.get("tools")?.as_array()?.iter().find_map(|tool| {
        let name = tool.get("function")?.get("name")?.as_str()?;
        (name == suffix || name.ends_with(&format!("_{suffix}"))).then(|| name.to_owned())
    })
}

/// A marker call's id prefix.
const MECHANIC_CALL: &str = "standin-mechanic-";

fn calls_of(message: &Json) -> impl Iterator<Item = &str> {
    message
        .get("tool_calls")
        .and_then(Json::as_array)
        .into_iter()
        .flatten()
        .filter_map(|call| call.get("id").and_then(Json::as_str))
}

/// The marker call this request should get, if any: the next marker after those already
/// called since the latest player message, unless the last message is another tool's result.
fn marker_call(request: &Json, player: &str) -> Option<Reply> {
    let markers = call_markers(player);
    if markers.is_empty() {
        return None;
    }
    let messages = request.get("messages").and_then(Json::as_array)?;
    let last = messages.last()?;
    let last_is_tool_result = last.get("role").and_then(Json::as_str) == Some("tool");
    let last_is_marker_result = last_is_tool_result
        && last.get("tool_call_id").and_then(Json::as_str).is_some_and(|id| id.starts_with(MECHANIC_CALL));
    if last_is_tool_result && !last_is_marker_result {
        return None;
    }
    let player_index = messages.iter().rposition(|message| {
        message.get("role").and_then(Json::as_str) == Some("user") && content_text(message.get("content")) == player
    })?;
    let called = messages[player_index + 1..]
        .iter()
        .filter(|message| calls_of(message).any(|id| id.starts_with(MECHANIC_CALL)))
        .count();
    let marker = markers.get(called)?;
    let tool = offered_tool(request, &marker.suffix)?;
    let round = messages.iter().filter(|message| calls_of(message).next().is_some()).count() + 1;
    Some(Reply::ToolCall { tool, arguments: marker.arguments.clone(), id: format!("{MECHANIC_CALL}{round}") })
}

/// `scripted_reply`: Python's `str.strip()` also strips `\x1c`-`\x1f`.
fn journey_reply(player: &str) -> String {
    static MARKERS: OnceLock<Regex> = OnceLock::new();
    let markers = MARKERS.get_or_init(|| Regex::new(r"\[(hold|fail|late)\]").expect("static pattern"));
    let player = without_call_markers(player);
    let without = markers.replace_all(&player, "");
    let stripped = without.trim_matches(|c: char| c.is_whitespace() || ('\x1c'..='\x1f').contains(&c));
    format!("The Game Master answers: {stripped}")
}

enum Reply {
    ToolCall { tool: String, arguments: String, id: String },
    Text(String),
}

/// The id of the stand-in's Campaign File call.
const CAMPAIGN_FILE_CALL: &str = "standin-call-1";

fn campaign_file_tool_name(request: &Json) -> Option<String> {
    request.get("tools")?.as_array()?.iter().find_map(|tool| {
        let name = tool.get("function")?.get("name")?.as_str()?;
        (name == CAMPAIGN_FILE_TOOL || name.ends_with(&format!("_{CAMPAIGN_FILE_TOOL}")))
            .then(|| name.to_owned())
    })
}

fn plan_reply(request: &Json, player: &str, narration: Narration) -> Reply {
    if let Some(reply) = marker_call(request, player) {
        return reply;
    }
    let tool = campaign_file_tool_name(request);
    let last = request.get("messages").and_then(Json::as_array).and_then(|messages| messages.last());
    // A marker call's result is not the Campaign File's: the update still follows it.
    let last_is_tool_result = last.and_then(|last| last.get("role")).and_then(Json::as_str) == Some("tool")
        && !last
            .and_then(|last| last.get("tool_call_id"))
            .and_then(Json::as_str)
            .is_some_and(|id| id.starts_with(MECHANIC_CALL));
    match narration {
        Narration::Fixed => match tool {
            Some(tool) if !last_is_tool_result => {
                let arguments = json!({
                    "state_change": "unchanged",
                    "state_change_summary": "The stand-in reply changes no durable campaign truth.",
                    "revised_player_facing_response": [{"kind": "narration", "text": TURN_REPLY}],
                });
                Reply::ToolCall { tool, arguments: arguments.to_string(), id: CAMPAIGN_FILE_CALL.into() }
            }
            Some(_) => Reply::Text(TURN_REPLY.into()),
            None => Reply::Text(NARRATION.into()),
        },
        Narration::Journey => {
            let reply = journey_reply(player);
            match tool {
                Some(tool) if !last_is_tool_result => {
                    // The fixture's own `json.dumps` of the same arguments.
                    let arguments = json!({
                        "state_change": "unchanged",
                        "state_change_summary": "No durable state changes.",
                        "revised_player_facing_response": [{"kind": "narration", "text": reply}],
                    });
                    Reply::ToolCall {
                        tool,
                        arguments: pyjson::dumps(&arguments, Dump::DEFAULT),
                        id: CAMPAIGN_FILE_CALL.into(),
                    }
                }
                _ => Reply::Text(reply),
            }
        }
    }
}

fn usage() -> Json {
    json!({"prompt_tokens": 10, "completion_tokens": 30, "total_tokens": 40})
}

fn completion_body(reply: &Reply) -> Json {
    let (message, finish) = match reply {
        Reply::ToolCall { tool, arguments, id } => (
            json!({"role": "assistant", "content": null, "tool_calls": [{
                "id": id, "type": "function",
                "function": {"name": tool, "arguments": arguments},
            }]}),
            "tool_calls",
        ),
        Reply::Text(text) => (json!({"role": "assistant", "content": text}), "stop"),
    };
    json!({
        "id": "standin-1", "object": "chat.completion", "created": CREATED, "model": MODEL,
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": usage(),
    })
}

fn chunk(delta: Json, finish: Option<&str>) -> Json {
    json!({
        "id": "standin-1", "object": "chat.completion.chunk", "created": CREATED, "model": MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    })
}

fn role_chunk() -> Json {
    chunk(json!({"role": "assistant", "content": ""}), None)
}

fn content_chunk(text: &str) -> Json {
    chunk(json!({"content": text}), None)
}

fn stream_events(reply: &Reply) -> Vec<Json> {
    let mut events = vec![role_chunk()];
    match reply {
        Reply::ToolCall { tool, arguments, id } => {
            events.push(chunk(
                json!({"tool_calls": [{
                    "index": 0, "id": id, "type": "function",
                    "function": {"name": tool, "arguments": ""},
                }]}),
                None,
            ));
            let characters: Vec<char> = arguments.chars().collect();
            for piece in characters.chunks(64) {
                let piece: String = piece.iter().collect();
                events.push(chunk(
                    json!({"tool_calls": [{"index": 0, "function": {"arguments": piece}}]}),
                    None,
                ));
            }
            events.push(chunk(json!({}), Some("tool_calls")));
        }
        Reply::Text(text) => {
            let words: Vec<&str> = text.split(' ').collect();
            for (position, word) in words.iter().enumerate() {
                let content =
                    if position == words.len() - 1 { (*word).to_owned() } else { format!("{word} ") };
                events.push(content_chunk(&content));
            }
            events.push(chunk(json!({}), Some("stop")));
        }
    }
    events.push(json!({
        "id": "standin-1", "object": "chat.completion.chunk", "created": CREATED, "model": MODEL,
        "choices": [], "usage": usage(),
    }));
    events
}

fn event_text(events: &[Json]) -> String {
    let mut body = String::new();
    for event in events {
        body.push_str("data: ");
        body.push_str(&event.to_string());
        body.push_str("\n\n");
    }
    body
}

fn reason(status: u16) -> &'static str {
    match status {
        200 => "OK",
        401 => "Unauthorized",
        _ => "Not Found",
    }
}

fn write_all(mut stream: &TcpStream, bytes: &[u8]) -> bool {
    stream.write_all(bytes).and_then(|()| stream.flush()).is_ok()
}

/// A JSON reply with a length; the connection stays open for the next request.
fn write_json(stream: &TcpStream, status: u16, payload: &Json) -> bool {
    let body = payload.to_string();
    let head = format!(
        "HTTP/1.1 {status} {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
        reason(status),
        body.len()
    );
    write_all(stream, head.as_bytes()) && write_all(stream, body.as_bytes())
}

/// A whole event stream with a length, then the connection closes.
fn write_stream(stream: &TcpStream, reply: &Reply) {
    let mut body = event_text(&stream_events(reply));
    body.push_str("data: [DONE]\n\n");
    let head = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n\
         Connection: close\r\nContent-Length: {}\r\n\r\n",
        body.len()
    );
    let _ = write_all(stream, head.as_bytes()) && write_all(stream, body.as_bytes());
}

/// The head of an event stream of unknown length and its first events, as one chunk.
fn start_chunked_stream(stream: &TcpStream, events: &[Json]) {
    let head = "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n\
                Connection: close\r\nTransfer-Encoding: chunked\r\n\r\n";
    let text = event_text(events);
    let framed = format!("{:x}\r\n{text}\r\n", text.len());
    let _ = write_all(stream, head.as_bytes()) && write_all(stream, framed.as_bytes());
}

#[cfg(test)]
mod tests {
    use super::*;

    fn state(narration: Narration) -> State {
        State {
            narration,
            requests: Mutex::new(Vec::new()),
            failed: Mutex::new(HashSet::new()),
            holds: Mutex::new(Holds::default()),
            changed: Condvar::new(),
            closing: AtomicBool::new(false),
            connections: Mutex::new(Vec::new()),
        }
    }

    #[test]
    fn offers_a_campaign_file_call_only_before_the_tool_result() {
        let offered = json!({"tools": [{"function": {"name": "sylvia_update_campaign_file"}}],
                             "messages": [{"role": "user"}]});
        assert!(
            matches!(plan_reply(&offered, "", Narration::Fixed), Reply::ToolCall { tool, .. } if tool == "sylvia_update_campaign_file")
        );
        let after = json!({"tools": [{"function": {"name": "update_campaign_file"}}],
                           "messages": [{"role": "tool"}]});
        assert!(matches!(plan_reply(&after, "", Narration::Fixed), Reply::Text(text) if text == TURN_REPLY));
        assert!(
            matches!(plan_reply(&json!({"messages": []}), "", Narration::Fixed), Reply::Text(text) if text == NARRATION)
        );
    }

    fn call_reply(request: &Json, player: &str) -> Option<(String, String, String)> {
        match plan_reply(request, player, Narration::Journey) {
            Reply::ToolCall { tool, arguments, id } => Some((tool, arguments, id)),
            Reply::Text(_) => None,
        }
    }

    #[test]
    fn call_markers_match_exact_and_suffix_names_in_request_order() {
        let tools = json!([
            {"function": {"name": "tool_aaa_skill_check"}},
            {"function": {"name": "skill_check"}},
            {"function": {"name": "tool_bbb_update_campaign_file"}},
        ]);
        let player = "I search [call:skill_check {\"skill\": \"perception\",  \"dc\":12}] the room.";
        let request = json!({"tools": tools, "messages": [{"role": "user", "content": player}]});
        // The first offered match, with the arguments exactly as written.
        assert_eq!(
            call_reply(&request, player),
            Some(("tool_aaa_skill_check".into(), "{\"skill\": \"perception\",  \"dc\":12}".into(), "standin-mechanic-1".into()))
        );
        let exact = json!({"tools": [{"function": {"name": "skill_check"}}], "messages": [{"role": "user", "content": player}]});
        assert_eq!(call_reply(&exact, player).map(|reply| reply.0), Some("skill_check".into()));
        // No offered match: today's reply (here the Campaign File update).
        let other = "[call:attack {}] go";
        let unmatched = json!({"tools": tools, "messages": [{"role": "user", "content": other}]});
        assert_eq!(call_reply(&unmatched, other).map(|reply| reply.2), Some("standin-call-1".into()));
    }

    #[test]
    fn call_markers_run_in_order_then_fall_back() {
        let player = "[call:first {\"a\":1}] then [call:second [1, 2]] done";
        let tools = json!([
            {"function": {"name": "tool_x_first"}}, {"function": {"name": "tool_x_second"}},
            {"function": {"name": "tool_x_update_campaign_file"}},
        ]);
        let mut messages = vec![json!({"role": "user", "content": player})];
        let request = |messages: &Vec<Json>| json!({"tools": tools, "messages": messages});
        let first = call_reply(&request(&messages), player).unwrap();
        assert_eq!((first.0.as_str(), first.1.as_str(), first.2.as_str()), ("tool_x_first", "{\"a\":1}", "standin-mechanic-1"));
        messages.push(json!({"role": "assistant", "tool_calls": [{"id": first.2}]}));
        messages.push(json!({"role": "tool", "tool_call_id": first.2, "content": "{}"}));
        let second = call_reply(&request(&messages), player).unwrap();
        assert_eq!((second.0.as_str(), second.1.as_str(), second.2.as_str()), ("tool_x_second", "[1, 2]", "standin-mechanic-2"));
        messages.push(json!({"role": "assistant", "tool_calls": [{"id": second.2}]}));
        messages.push(json!({"role": "tool", "tool_call_id": second.2, "content": "{}"}));
        // After the last marker's result: the Campaign File update, then the narration.
        let update = call_reply(&request(&messages), player).unwrap();
        assert_eq!((update.0.as_str(), update.2.as_str()), ("tool_x_update_campaign_file", "standin-call-1"));
        messages.push(json!({"role": "assistant", "tool_calls": [{"id": update.2}]}));
        messages.push(json!({"role": "tool", "tool_call_id": update.2, "content": "{}"}));
        assert_eq!(call_reply(&request(&messages), player), None);
    }

    #[test]
    fn call_markers_leave_the_echoed_narration() {
        assert_eq!(
            journey_reply("I swing [call:attack {\"target\": \"wolf-1\"}] at the wolf."),
            "The Game Master answers: I swing  at the wolf."
        );
        assert_eq!(without_call_markers("[call:bad no json] and [call: {}] stay"), "[call:bad no json] and [call: {}] stay");
        assert!(call_markers("[call:x {\"a\": \"]\"}]").first().is_some_and(|marker| marker.arguments == "{\"a\": \"]\"}"));
    }

    #[test]
    fn stream_ends_with_usage() {
        let events = stream_events(&Reply::Text(NARRATION.into()));
        assert_eq!(events.last().unwrap()["usage"]["total_tokens"], 40);
        let text: String =
            events.iter().filter_map(|event| event["choices"][0]["delta"]["content"].as_str()).collect();
        assert_eq!(text, NARRATION);
    }

    #[test]
    fn the_player_message_skips_sealed_host_records() {
        let request = json!({"messages": [
            {"role": "user", "content": "[fail] I open the door."},
            {"role": "assistant", "content": "..."},
            {"role": "user", "content": "[host-seal 0123456789ab] Campaign state"},
        ]});
        assert_eq!(latest_player_message(&request), "[fail] I open the door.");
        assert_eq!(latest_player_message(&json!({})), "");
    }

    #[test]
    fn journey_replies_echo_the_message_without_markers() {
        assert_eq!(
            journey_reply(" [hold] I listen at the door.\x1c"),
            "The Game Master answers: I listen at the door."
        );
        let offered = json!({"tools": [{"function": {"name": "update_campaign_file"}}], "messages": []});
        let Reply::ToolCall { arguments, .. } = plan_reply(&offered, "[late] I wait.", Narration::Journey)
        else {
            panic!("a tool call is offered");
        };
        assert_eq!(
            arguments,
            "{\"state_change\": \"unchanged\", \"state_change_summary\": \"No durable state changes.\", \
             \"revised_player_facing_response\": [{\"kind\": \"narration\", \"text\": \"The Game Master answers: I wait.\"}]}"
        );
    }

    #[test]
    fn markers_pick_the_script_and_fail_only_once() {
        let state = state(Narration::Journey);
        assert!(matches!(script("[hold] wait", &state), Script::Hold));
        assert!(matches!(script("[fail] go", &state), Script::Fail));
        assert!(matches!(script("[fail] go", &state), Script::Answer));
        assert!(matches!(script("[late] go", &state), Script::Late));
        assert!(matches!(script("go", &state), Script::Answer));
    }

    #[test]
    fn records_headers_without_the_credential_and_names_the_body_encoding() {
        let request = Request {
            method: "POST".into(),
            path: "/v1/chat/completions".into(),
            headers: vec![
                ("Host".into(), "127.0.0.1:1".into()),
                ("Authorization".into(), "Bearer standin-key".into()),
            ],
            body: b"{\"stream\":true,\"model\":\"m\"}".to_vec(),
        };
        let recorded = record(&request, true);
        assert_eq!(recorded["headers"], json!([["Host", "127.0.0.1:1"], ["Authorization", null]]));
        assert_eq!(recorded["body_encoding"], "wire");
        assert!(!recorded.to_string().contains("standin-key"));
    }

    fn send(url: &str, path: &str, body: &str) -> TcpStream {
        let address = url.trim_start_matches("http://").trim_end_matches("/v1");
        let mut stream = TcpStream::connect(address).unwrap();
        write!(
            stream,
            "POST /v1{path} HTTP/1.1\r\nHost: {address}\r\nAuthorization: Bearer {KEY}\r\n\
             Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        )
        .unwrap();
        stream
    }

    fn chat(text: &str) -> String {
        json!({"stream": true, "messages": [{"role": "user", "content": text}]}).to_string()
    }

    #[test]
    fn hold_streams_a_partial_chunk_until_the_owner_closes() {
        let standin = Standin::start(Narration::Journey).unwrap();
        let mut held = send(&standin.url, "/chat/completions", &chat("[hold] I listen."));
        standin.wait_held(Duration::from_secs(10)).unwrap();
        let mut head = vec![0u8; 512];
        let read = held.read(&mut head).unwrap();
        let text = String::from_utf8_lossy(&head[..read]).into_owned();
        assert!(text.contains("Transfer-Encoding: chunked"), "{text}");
        drop(held);
        let mut answered = send(&standin.url, "/chat/completions", &chat("[late] I wait."));
        standin.wait_held(Duration::from_secs(10)).unwrap();
        standin.release();
        let mut reply = String::new();
        answered.read_to_string(&mut reply).unwrap();
        assert!(reply.contains("data: [DONE]"));
        assert!(reply.contains("wait.") && !reply.contains("[late]"), "{reply}");
        let requests = standin.finish();
        assert_eq!(requests.len(), 2);
    }

    #[test]
    fn fail_drops_the_connection_once_then_answers() {
        let standin = Standin::start(Narration::Journey).unwrap();
        let mut failed = send(&standin.url, "/chat/completions", &chat("[fail] I open the door."));
        let mut reply = Vec::new();
        let _ = failed.read_to_end(&mut reply);
        let reply = String::from_utf8_lossy(&reply).into_owned();
        assert!(reply.contains(PARTIAL) && !reply.contains("0\r\n\r\n"), "{reply}");
        let mut retried = send(&standin.url, "/chat/completions", &chat("[fail] I open the door."));
        let mut reply = String::new();
        retried.read_to_string(&mut reply).unwrap();
        assert!(reply.contains("door.") && reply.contains("data: [DONE]"), "{reply}");
        standin.finish();
    }
}
