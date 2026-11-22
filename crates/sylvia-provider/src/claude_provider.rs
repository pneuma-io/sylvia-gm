//! `provider/_claude.py`'s `ClaudeProvider`: Sylvia's own Claude Code copy as a streaming
//! provider, one native reply per step of Sylvia's tool loop. Lane 5.
//!
//! A step starts a process from the saved transcript ([`prepare_request`]), or, with the context
//! ledger's route and `SYLVIA_CLAUDE_PROCESS=persistent`, continues a process kept from the last
//! request. It reads one native reply to its end: a Sylvia tool call (which a kept process waits
//! on until the next request answers it) or a result. No game action runs in the child.
//!
//! Python's `generate_stream` is an async generator whose `finally` blocks close the process and
//! remove its files on every exit. Here the stream is a [`generator`]: an error or the end runs
//! the same cleanup with its audits; a stream dropped mid-step (Python's cancellation) runs it
//! from a guard, which cannot await the `claude/process_reuse` audit Python's `finally` fires.

use crate::claude::{
    check_still_waiting, failure_code, invalid_reason, measured_usage, model_usage, prepare_request,
    random_uuid4, reported_window, runtime_failure, tool_search, RecordIds, PREFIX, TOOL_SEARCH,
};
use crate::claude_kept::{
    continuation, kept_processes, process_setting, ContinuationError, Delivery, KeptProcess, KeptState,
    KEPT_SESSION, PERSISTENT, PROCESS_SWITCH,
};
use crate::claude_native::{
    collect, collect_answer, delete_session, remove_debug_logs, remove_step_folder, session_files, sweep,
    take_native_route, wait_for_flush, NativeCollection, NativeRoute, TRANSIENT_SESSION,
};
use crate::claude_runtime::{
    documented_context_window, find_runtime, native_environment, runtime_version, ClaudeConnection,
    DOCUMENTED_CONTEXT_WINDOW_AT,
};
use crate::codex_rpc::python_resolve;
use crate::context_capacity::{resolve_context_capacity, CapacityInputs};
use crate::error::{ErrorKind, ProviderError, ProviderResult};
use crate::generator::{generator, Co};
use crate::hooks::{HookContext, HookManager};
use crate::provider::{BoxFuture, ChunkStream, Provider, ProviderRequest};
use crate::pyobj::{py_eq, py_str, truthy};
use crate::types::{LiveContext, ModelInfo, ProviderResponse, ProviderStreamChunk, PyValue, ToolCall};
use futures_util::StreamExt;
use std::collections::{BTreeMap, BTreeSet};
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};

/// The ledger's continue record text (`agent._context_ledger.CONTINUE_RECORD_TEXT`), which a
/// kept process may skip. Lane 4 owns it when the ledger is ported.
pub const CONTINUE_RECORD_TEXT: &str = "SYLVIA_GM_CONTINUE (application invocation, not player speech): Continue the response to the saved Sylvia conversation, using the committed tool results above.";

/// `_ACTIVE_STEPS`: session ids of steps running in this process, by runtime home (kept out of
/// the sweep).
fn active_steps() -> &'static Mutex<BTreeMap<PathBuf, BTreeSet<String>>> {
    static ACTIVE: OnceLock<Mutex<BTreeMap<PathBuf, BTreeSet<String>>>> = OnceLock::new();
    ACTIVE.get_or_init(Default::default)
}

fn discard_active(home: &Path, session_id: &str) {
    if let Some(active) = active_steps().lock().expect("active steps").get_mut(home) {
        active.remove(session_id);
    }
}

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

fn text(value: &str) -> Json {
    Json::String(value.to_owned())
}

fn invalid(message: &str) -> ProviderError {
    ProviderError::new(ErrorKind::InvalidResponse, message)
}

/// A local failure Python raises raw (an `OSError`, or an accidental `TypeError`): it reaches
/// the caller as an error the audits and the GM loop treat as local.
fn local(message: impl Into<String>) -> ProviderError {
    ProviderError::new(ErrorKind::Internal, message)
}

fn from_chat(error: crate::chat::ChatError) -> ProviderError {
    match error {
        crate::chat::ChatError::Provider(error) => error,
        crate::chat::ChatError::Incidental(incidental) => local(incidental.0),
    }
}

/// `_Step`: one native reply as it is read.
struct Step {
    response: ProviderResponse,
    allowed: BTreeSet<String>,
    tools: Vec<Json>,
    pending: Vec<Json>,
    text_parts: Vec<String>,
    call_event: Option<JsonMap>,
    call_id: String,
    call_arguments: Json,
    keepable: bool,
    not_kept: Option<&'static str>,
    model_usage_before: JsonMap,
    model_usage_after: Option<JsonMap>,
    searches: Vec<JsonMap>,
    final_message_id: Option<String>,
}

impl Step {
    fn new(wire_model: &str, tools: Vec<Json>, model_usage_before: JsonMap) -> Self {
        let allowed = tools.iter().filter_map(|tool| tool.get("name").map(py_str)).collect();
        Self {
            response: ProviderResponse {
                model: wire_model.to_owned(),
                usage_reported: Some(false),
                ..Default::default()
            },
            allowed,
            tools,
            pending: Vec::new(),
            text_parts: Vec::new(),
            call_event: None,
            call_id: String::new(),
            call_arguments: Json::Null,
            keepable: true,
            not_kept: None,
            model_usage_before,
            model_usage_after: None,
            searches: Vec::new(),
            final_message_id: None,
        }
    }
}

/// `_KeepPlan`: what a kept process must hold to serve one ledger request, and its reuse audit.
struct KeepPlan {
    home: PathBuf,
    model: String,
    system: String,
    tools_key: String,
    history: Vec<Json>,
    generation: u64,
    reuse: JsonMap,
}

/// Why the event loop stopped: a provider error, or one of Python's accidental exceptions
/// (`AttributeError`, `KeyError`, `TypeError`, `ValueError`), named for the audit.
enum Fault {
    Provider(ProviderError),
    Malformed(&'static str),
}

impl From<ProviderError> for Fault {
    fn from(error: ProviderError) -> Self {
        Self::Provider(error)
    }
}

type Read<T> = Result<T, Fault>;

/// `value.get(key)` on a dict, else `AttributeError`.
fn get<'a>(value: &'a Json, key: &str) -> Read<Option<&'a Json>> {
    match value {
        Json::Object(map) => Ok(map.get(key)),
        _ => Err(Fault::Malformed("AttributeError")),
    }
}

/// `value.get(key, {})`.
fn get_or_empty(value: &Json, key: &str) -> Read<Json> {
    Ok(get(value, key)?.cloned().unwrap_or_else(|| Json::Object(JsonMap::new())))
}

/// `value[key]`.
fn item<'a>(value: &'a Json, key: &str) -> Read<&'a Json> {
    match value {
        Json::Object(map) => map.get(key).ok_or(Fault::Malformed("KeyError")),
        _ => Err(Fault::Malformed("TypeError")),
    }
}

/// `dict(value)` of a dict (a list of pairs or anything else is refused).
fn as_dict(value: &Json) -> Read<JsonMap> {
    match value {
        Json::Object(map) => Ok(map.clone()),
        Json::Array(_) | Json::String(_) => Err(Fault::Malformed("ValueError")),
        _ => Err(Fault::Malformed("TypeError")),
    }
}

/// A key Python's dicts would accept (`blocks[native["index"]]`): anything but a list or dict.
fn hashable(value: &Json) -> Read<Json> {
    match value {
        Json::Array(_) | Json::Object(_) => Err(Fault::Malformed("TypeError")),
        other => Ok(other.clone()),
    }
}

/// `x == "literal"` on any JSON value.
fn is_text(value: Option<&Json>, literal: &str) -> bool {
    value.and_then(Json::as_str) == Some(literal)
}

/// `_AnthropicStreamContent` over Claude Code's stream events, which Python hands it as
/// `SimpleNamespace`s of the event's dict (its `delta` one too): one native message's ordered
/// blocks, complete at `message_stop`.
#[derive(Default)]
struct NativeContent {
    blocks: Vec<(Json, JsonMap)>,
    arguments: Vec<(Json, String)>,
    completed: bool,
}

fn find<'a, T>(entries: &'a mut [(Json, T)], key: &Json) -> Option<&'a mut T> {
    entries.iter_mut().find(|(existing, _)| py_eq(existing, key)).map(|(_, value)| value)
}

impl NativeContent {
    fn accept(&mut self, event: &JsonMap) -> Read<Option<Vec<Json>>> {
        let kind = event.get("type").cloned().unwrap_or_else(|| text(""));
        let index = event.get("index").cloned().unwrap_or(Json::from(0));
        match kind.as_str() {
            Some("content_block_start") => {
                let block = event.get("content_block").ok_or(Fault::Malformed("AttributeError"))?;
                // `_content_block_dict`: a dict is copied; a non-dict has no `model_dump` and
                // `vars()` of it fails.
                let dict = match block {
                    Json::Object(map) => map.clone(),
                    _ => return Err(Fault::Malformed("TypeError")),
                };
                let key = hashable(&index)?;
                match find(&mut self.blocks, &key) {
                    Some(slot) => *slot = dict,
                    None => self.blocks.push((key, dict)),
                }
            }
            Some("content_block_delta") => {
                let delta = event.get("delta").ok_or(Fault::Malformed("AttributeError"))?;
                // `getattr(delta, "type", "")`: a dict delta is a namespace; any other value
                // has no such attribute.
                let attribute =
                    |name: &str| -> Option<&Json> { delta.as_object().and_then(|map| map.get(name)) };
                let field = match attribute("type").and_then(Json::as_str) {
                    Some("text_delta") => Some("text"),
                    Some("thinking_delta") => Some("thinking"),
                    Some("signature_delta") => Some("signature"),
                    _ => None,
                };
                if let Some(field) = field {
                    let key = hashable(&index)?;
                    let block = find(&mut self.blocks, &key).ok_or(Fault::Malformed("KeyError"))?;
                    let added = attribute(field).cloned().unwrap_or_else(|| text(""));
                    let joined = match (block.get(field).cloned().unwrap_or_else(|| text("")), added) {
                        (Json::String(before), Json::String(more)) => before + &more,
                        _ => return Err(Fault::Malformed("TypeError")),
                    };
                    block.insert(field.into(), Json::String(joined));
                } else if is_text(attribute("type"), "input_json_delta") {
                    let partial = attribute("partial_json").ok_or(Fault::Malformed("AttributeError"))?;
                    let Json::String(partial) = partial else { return Err(Fault::Malformed("TypeError")) };
                    let key = hashable(&index)?;
                    match find(&mut self.arguments, &key) {
                        Some(joined) => joined.push_str(partial),
                        None => self.arguments.push((key, partial.clone())),
                    }
                }
            }
            Some("message_stop") => {
                for (key, fragments) in &self.arguments {
                    if fragments.is_empty() {
                        continue;
                    }
                    let parsed = pyjson::loads(fragments).ok();
                    let block = self.blocks.iter_mut().find(|(existing, _)| py_eq(existing, key));
                    match (parsed, block) {
                        (Some(input), Some((_, block))) => {
                            block.insert("input".into(), input);
                        }
                        _ => {
                            return Err(Fault::Provider(ProviderError::new(
                                ErrorKind::InvalidToolArguments,
                                format!(
                                    "Anthropic returned invalid streamed tool input at block {}",
                                    py_str(key)
                                ),
                            )))
                        }
                    }
                }
                self.completed = true;
                let mut ordered: Vec<(i64, &JsonMap)> = Vec::new();
                for (key, block) in &self.blocks {
                    // `sorted(self.blocks)`: whole-number keys (bools are ints).
                    let position = match key {
                        Json::Number(number) if !number.is_f64() => number.as_i64().unwrap_or(0),
                        Json::Bool(flag) => i64::from(*flag),
                        _ => return Err(Fault::Malformed("TypeError")),
                    };
                    ordered.push((position, block));
                }
                ordered.sort_by_key(|(position, _)| *position);
                return Ok(Some(ordered.into_iter().map(|(_, block)| Json::Object(block.clone())).collect()));
            }
            _ => {}
        }
        Ok(None)
    }
}

/// Files a fresh step leaves only while it runs, removed however it ends.
struct StepFiles {
    home: PathBuf,
    resolved_home: PathBuf,
    config: PathBuf,
    cache: Option<PathBuf>,
    session_id: String,
    step_folder: PathBuf,
    /// The step's process, closed on drop unless a kept process took it over.
    connection: Option<ClaudeConnection>,
    /// False once a kept process owns the files.
    armed: bool,
}

impl StepFiles {
    fn clean(&mut self) {
        if let Some(connection) = self.connection.take() {
            connection.close();
        }
        if !self.armed {
            return;
        }
        self.armed = false;
        delete_session(&self.config, &self.session_id, &self.home);
        if self.step_folder.exists() {
            remove_step_folder(&self.step_folder, &self.home);
        }
        remove_debug_logs(self.cache.as_deref(), &self.home);
        discard_active(&self.resolved_home, &self.session_id);
    }
}

impl Drop for StepFiles {
    fn drop(&mut self) {
        // A dropped stream: close the process and remove its files, off the async thread when
        // a runtime is there to take it.
        if self.connection.is_none() && !self.armed {
            return;
        }
        let mut rest = StepFiles {
            home: std::mem::take(&mut self.home),
            resolved_home: std::mem::take(&mut self.resolved_home),
            config: std::mem::take(&mut self.config),
            cache: self.cache.take(),
            session_id: std::mem::take(&mut self.session_id),
            step_folder: std::mem::take(&mut self.step_folder),
            connection: self.connection.take(),
            armed: std::mem::replace(&mut self.armed, false),
        };
        match tokio::runtime::Handle::try_current() {
            Ok(handle) => {
                handle.spawn_blocking(move || rest.clean());
            }
            Err(_) => rest.clean(),
        }
    }
}

/// Closes a kept process when a continued step is dropped mid-way.
struct KeptGuard(Option<Arc<KeptProcess>>);

impl Drop for KeptGuard {
    fn drop(&mut self) {
        if let Some(kept) = self.0.take() {
            match tokio::runtime::Handle::try_current() {
                Ok(handle) => {
                    handle.spawn_blocking(move || kept.close());
                }
                Err(_) => kept.close(),
            }
        }
    }
}

/// The record names and clock a provider uses; tests supply Python's.
type Uuid4 = Box<dyn FnMut() -> [u8; 16] + Send>;
type Clock = Box<dyn Fn() -> String + Send + Sync>;

fn utc_now() -> String {
    crate::hooks::utc_now_isoformat()
}

/// `ClaudeProvider`: an ephemeral native conversation for each step of Sylvia's tool loop.
pub struct ClaudeProvider {
    home: PathBuf,
    model: ModelInfo,
    live: LiveContext,
    hooks: HookManager,
    active: Mutex<Option<ClaudeConnection>>,
    lock: tokio::sync::Mutex<()>,
    last_response: Mutex<ProviderResponse>,
    reported_window_refused: AtomicBool,
    uuid4: Mutex<Uuid4>,
    clock: Clock,
}

impl ClaudeProvider {
    /// `ClaudeProvider(home, model_info)`. The model's context figures become live: a window
    /// Claude Code reports is what every holder of this `ModelInfo` plans within.
    pub fn new(home: &Path, mut model_info: ModelInfo) -> Self {
        let live = model_info.share_live_context();
        let provider = Self {
            home: home.to_owned(),
            model: model_info,
            live,
            hooks: HookManager::new(),
            active: Mutex::new(None),
            lock: tokio::sync::Mutex::new(()),
            last_response: Mutex::new(ProviderResponse::default()),
            reported_window_refused: AtomicBool::new(false),
            uuid4: Mutex::new(Box::new(random_uuid4)),
            clock: Box::new(utc_now),
        };
        if provider.model.runtime_context_window().is_none() {
            // A model saved before this figure existed, or by a caller that named none.
            let wire = Json::String(provider.model.wire_model_id().to_owned());
            if let Some(documented) = documented_context_window(&wire) {
                provider.apply_runtime_window(
                    Some(documented),
                    "model_catalog",
                    Some(DOCUMENTED_CONTEXT_WINDOW_AT.into()),
                );
            }
        }
        provider
    }

    /// Python's `uuid.uuid4` and `datetime.now(UTC).isoformat`, replaced (tests).
    pub fn with_ids(mut self, uuid4: Uuid4, clock: Clock) -> Self {
        self.uuid4 = Mutex::new(uuid4);
        self.clock = clock;
        self
    }

    fn wire(&self) -> &str {
        self.model.wire_model_id()
    }

    fn context(&self, request_json: JsonMap) -> HookContext {
        HookContext {
            provider_key: "claude_runtime".into(),
            model_id: self.model.id.clone(),
            stream: true,
            request_json: Some(request_json),
            ..HookContext::default()
        }
    }

    async fn audit(&self, request_json: JsonMap) {
        self.hooks.fire_on_request(&self.context(request_json)).await;
    }

    fn prepare(
        &self,
        messages: &[Json],
        tools: Option<&[Json]>,
        route: Option<&NativeRoute>,
    ) -> ProviderResult<crate::claude::PreparedRequest> {
        let mut uuid4 = self.uuid4.lock().expect("uuid source");
        let mut ids = RecordIds { uuid4: &mut **uuid4, now: &*self.clock };
        prepare_request(messages, tools, self.wire(), &self.home.join("workspace"), route, &mut ids)
            .map_err(from_chat)
    }

    fn session_id(&self) -> String {
        let mut uuid4 = self.uuid4.lock().expect("uuid source");
        let mut ids = RecordIds { uuid4: &mut **uuid4, now: &*self.clock };
        ids.text()
    }

    /// `_keep_plan`.
    fn keep_plan(
        &self,
        messages: &[Json],
        tools: Option<&[Json]>,
        route: &NativeRoute,
    ) -> ProviderResult<KeepPlan> {
        let prepared = self.prepare(messages, tools, Some(route))?;
        let request = crate::anthropic::format_request(self.wire(), messages, tools, true, &JsonMap::new())
            .map_err(from_chat)?;
        let history = match request.get("messages") {
            Some(Json::Array(history)) => history.clone(),
            _ => Vec::new(),
        };
        let home = python_resolve(&self.home);
        let generation = kept_processes().generation(&home);
        Ok(KeepPlan {
            home,
            model: self.wire().to_owned(),
            system: prepared.system,
            tools_key: pyjson::dumps(
                &Json::Array(prepared.native_tools),
                Dump { sort_keys: true, ..Dump::DEFAULT },
            ),
            history,
            generation,
            reuse: object(vec![
                ("continued", Json::Bool(false)),
                ("answered_in_place", Json::Bool(false)),
                ("fresh_reason", Json::Null),
                ("kept", Json::Bool(false)),
            ]),
        })
    }

    async fn close_kept(kept: Vec<Arc<KeptProcess>>) {
        for each in kept {
            let _ = tokio::task::spawn_blocking(move || each.close()).await;
        }
    }

    async fn close_owned(connection: &ClaudeConnection) {
        let connection = connection.clone();
        let _ = tokio::task::spawn_blocking(move || connection.close()).await;
    }

    async fn audit_reuse(&self, reuse: &JsonMap) {
        self.audit(object(vec![
            ("method", text("claude/process_reuse")),
            ("params", Json::Object(reuse.clone())),
        ]))
        .await;
    }

    async fn audit_failure(&self, plan: &mut KeepPlan, error: &ProviderError) {
        plan.reuse.insert("kept".into(), Json::Bool(false));
        plan.reuse.insert("failure".into(), text(failure_code(Some(error), false)));
        self.audit_reuse(&plan.reuse).await;
    }

    async fn stream(
        &self,
        request: ProviderRequest,
        co: &Co<ProviderResult<ProviderStreamChunk>>,
    ) -> ProviderResult<()> {
        // The context ledger's route belongs to exactly the request it was planned for.
        let route = take_native_route();
        let _held = self.lock.lock().await;
        let messages = &request.messages;
        let tools = request.tools.as_deref();
        let timeout = Duration::from_secs_f64(request.timeout.max(0.0));
        let keep =
            route.is_some() && process_setting(std::env::var(PROCESS_SWITCH).ok().as_deref()) == PERSISTENT;
        let mut plan = None;
        if keep {
            let route = route.as_ref().expect("a route");
            let mut planned = self.keep_plan(messages, tools, route)?;
            let (kept, stale) = kept_processes().take(
                &planned.home,
                &planned.model,
                &planned.system,
                &planned.tools_key,
                &planned.history,
            );
            Self::close_kept(stale).await;
            if let Some(kept) = kept {
                let delivery = {
                    let state = kept.state.lock().expect("kept state").clone();
                    check_still_waiting(&kept.connection.drain())
                        .and_then(|()| continuation(&state, &planned.history, CONTINUE_RECORD_TEXT))
                };
                match delivery {
                    Err(ContinuationError::Mismatch(mismatch)) => {
                        planned.reuse.insert("fresh_reason".into(), text(mismatch.reason));
                        Self::close_kept(vec![kept]).await;
                    }
                    Err(ContinuationError::Raw(message)) => return Err(local(message)),
                    Ok(delivery) => {
                        let mut yielded = false;
                        match self
                            .continue_kept(&kept, delivery, route, &mut planned, timeout, co, &mut yielded)
                            .await
                        {
                            Ok(()) => return Ok(()),
                            Err(error)
                                if error.is(ErrorKind::IncompleteStream)
                                    || error.is(ErrorKind::Connection) =>
                            {
                                // The process had gone: serve the request once on a fresh one,
                                // but only before the caller saw anything.
                                if yielded {
                                    self.audit_failure(&mut planned, &error).await;
                                    return Err(error);
                                }
                                planned.reuse.insert("continued".into(), Json::Bool(false));
                                planned.reuse.insert("answered_in_place".into(), Json::Bool(false));
                                planned.reuse.insert("retried_fresh".into(), Json::Bool(true));
                                planned.reuse.insert("fresh_reason".into(), text("runtime_error"));
                            }
                            Err(error) => {
                                self.audit_failure(&mut planned, &error).await;
                                return Err(error);
                            }
                        }
                    }
                }
            }
            plan = Some(planned);
        }
        self.fresh(messages, tools, route.as_ref(), timeout, plan, co).await
    }

    /// `_continue`: serve the request in the kept process, a new prompt or the waiting call's
    /// answer.
    #[allow(clippy::too_many_arguments)]
    async fn continue_kept(
        &self,
        kept: &Arc<KeptProcess>,
        delivery: Delivery,
        route: &NativeRoute,
        plan: &mut KeepPlan,
        timeout: Duration,
        co: &Co<ProviderResult<ProviderStreamChunk>>,
        yielded: &mut bool,
    ) -> ProviderResult<()> {
        let mut guard = KeptGuard(Some(Arc::clone(kept)));
        let connection = kept.connection.clone();
        *self.active.lock().expect("active connection") = Some(connection.clone());
        let tools = match pyjson::loads(&kept.tools_key) {
            Ok(Json::Array(tools)) => tools,
            _ => Vec::new(),
        };
        let state = kept.state.lock().expect("kept state").clone();
        let mut step = Step::new(self.wire(), tools, state.model_usage.clone());
        let answered = matches!(delivery, Delivery::Answer(_));
        plan.reuse.insert("continued".into(), Json::Bool(true));
        plan.reuse.insert("answered_in_place".into(), Json::Bool(answered));
        let searching = tool_search(&step.tools);
        let outcome: ProviderResult<()> = async {
            let prompt = match delivery {
                Delivery::Prompt(content) => {
                    let prompt = object(vec![
                        ("type", text("user")),
                        ("message", Json::Object(object(vec![("role", text("user")), ("content", Json::Array(content))]))),
                    ]);
                    self.audit_continued(Json::Object(prompt.clone()), searching).await;
                    connection.write(&Json::Object(prompt.clone()))?;
                    Some(prompt)
                }
                Delivery::Answer(payload) => {
                    let pending = Json::Object(state.pending.clone().unwrap_or_default());
                    self.audit_continued(Json::Object(object(vec![("tool_result", Json::Object(payload.clone()))])), searching)
                        .await;
                    let request_id = pending.get("request_id").cloned().ok_or_else(|| local("'request_id'"))?;
                    let message_id = pending
                        .get("request")
                        .and_then(|request| request.get("message"))
                        .and_then(|message| message.get("id"))
                        .cloned()
                        .unwrap_or(Json::from(0));
                    connection.write(&serde_json::json!({
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": request_id,
                            "response": {"mcp_response": {"jsonrpc": "2.0", "id": message_id, "result": payload}},
                        },
                    }))?;
                    None
                }
            };
            let answered_call = answered.then(|| state.call_id.clone());
            let session = Session { config: &kept.config, session_id: &kept.session_id, route: Some(route) };
            self.run_events(&connection, &mut step, &session, prompt.as_ref(), timeout, true, answered_call.as_deref(), co, yielded)
                .await
        }
        .await;
        *self.active.lock().expect("active connection") = None;
        match outcome {
            Ok(()) if step.keepable => {
                {
                    let mut state = kept.state.lock().expect("kept state");
                    state.held = plan.history.clone();
                    state.spoken = step.text_parts.concat();
                    state.pending = step.call_event.clone();
                    state.call_id = step.call_id.clone();
                    state.call_arguments = step.call_arguments.clone();
                    if let Some(after) = &step.model_usage_after {
                        state.model_usage = after.clone();
                    }
                }
                guard.0 = None;
                let evicted = kept_processes().keep(Arc::clone(kept), plan.generation);
                let still_kept = !evicted.iter().any(|each| Arc::ptr_eq(each, kept));
                plan.reuse.insert("kept".into(), Json::Bool(still_kept));
                Self::close_kept(evicted).await;
                self.audit_reuse(&plan.reuse).await;
                Ok(())
            }
            Ok(()) => {
                plan.reuse.insert("kept".into(), Json::Bool(false));
                plan.reuse.insert("not_kept".into(), step.not_kept.map_or(Json::Null, text));
                guard.0 = None;
                Self::close_kept(vec![Arc::clone(kept)]).await;
                self.audit_reuse(&plan.reuse).await;
                Ok(())
            }
            Err(error) => {
                // An error or a closed stream: the process's state is unknown. The caller
                // records the request (a fresh retry writes its own record).
                guard.0 = None;
                Self::close_kept(vec![Arc::clone(kept)]).await;
                Err(error)
            }
        }
    }

    async fn audit_continued(&self, given: Json, searching: bool) {
        self.audit(object(vec![
            ("method", text("claude/native_step")),
            ("continued", Json::Bool(true)),
            ("input", given),
            (
                "controls",
                Json::Object(object(vec![
                    ("safe_mode", Json::Bool(true)),
                    (
                        "builtin_tools",
                        Json::Array(if searching { vec![text(TOOL_SEARCH)] } else { Vec::new() }),
                    ),
                    ("persist_session", text(KEPT_SESSION)),
                    ("output_allowance", text("runtime_managed")),
                    ("tool_choice", text("runtime_managed")),
                ])),
            ),
            ("downstream_model_request", text("not_exposed_by_runtime")),
        ]))
        .await;
    }

    /// `_fresh`: start a process from the saved transcript; keep it afterwards when `plan`
    /// says so.
    async fn fresh(
        &self,
        messages: &[Json],
        tools: Option<&[Json]>,
        route: Option<&NativeRoute>,
        timeout: Duration,
        mut plan: Option<KeepPlan>,
        co: &Co<ProviderResult<ProviderStreamChunk>>,
    ) -> ProviderResult<()> {
        let Some(executable) = find_runtime(&self.home) else {
            return Err(ProviderError::new(
                ErrorKind::Connection,
                "Set up Claude Code for Sylvia in Settings > Accounts, then try again.",
            ));
        };
        let (checked, home) = (executable.clone(), self.home.clone());
        tokio::task::spawn_blocking(move || runtime_version(&checked, &home))
            .await
            .map_err(|error| local(error.to_string()))??;
        std::fs::create_dir_all(&self.home).map_err(|error| local(error.to_string()))?;
        // One fixed, empty working directory: Claude Code names it in every request. The system
        // and transcript files live in a per-step folder that is always removed.
        let workspace = self.home.join("workspace");
        make_dir(&workspace)?;
        let steps = self.home.join("steps");
        make_dir(&steps)?;
        let mut environment = native_environment(&self.home);
        let variable = |environment: &[(OsString, OsString)], name: &str| {
            environment
                .iter()
                .find(|(key, _)| key == name)
                .map(|(_, value)| value.clone())
                .filter(|value| !value.is_empty())
        };
        let config = variable(&environment, "CLAUDE_CONFIG_DIR")
            .map_or_else(|| self.home.join("profile"), PathBuf::from);
        let cache = variable(&environment, "XDG_CACHE_HOME").map(PathBuf::from);
        let session_id = self.session_id();
        let step_folder = steps.join(&session_id);
        let resolved_home = python_resolve(&self.home);
        let running = {
            let mut active = active_steps().lock().expect("active steps");
            let set = active.entry(resolved_home.clone()).or_default();
            let running = set.clone();
            set.insert(session_id.clone());
            running
        };
        let mut files = StepFiles {
            home: self.home.clone(),
            resolved_home,
            config: config.clone(),
            cache: cache.clone(),
            session_id: session_id.clone(),
            step_folder: step_folder.clone(),
            connection: None,
            armed: true,
        };
        let outcome = self
            .fresh_step(
                messages,
                tools,
                route,
                timeout,
                &mut plan,
                co,
                &mut files,
                &executable,
                &workspace,
                &mut environment,
                running,
            )
            .await;
        if files.armed || files.connection.is_some() {
            let mut rest = std::mem::replace(
                &mut files,
                StepFiles {
                    home: PathBuf::new(),
                    resolved_home: PathBuf::new(),
                    config: PathBuf::new(),
                    cache: None,
                    session_id: String::new(),
                    step_folder: PathBuf::new(),
                    connection: None,
                    armed: false,
                },
            );
            // Success, error or timeout: the CLI's session file and the step's files hold full
            // copies of the request and never outlive the step.
            let _ = tokio::task::spawn_blocking(move || rest.clean()).await;
        }
        outcome
    }

    #[allow(clippy::too_many_arguments)]
    async fn fresh_step(
        &self,
        messages: &[Json],
        tools: Option<&[Json]>,
        route: Option<&NativeRoute>,
        timeout: Duration,
        plan: &mut Option<KeepPlan>,
        co: &Co<ProviderResult<ProviderStreamChunk>>,
        files: &mut StepFiles,
        executable: &Path,
        workspace: &Path,
        environment: &mut Vec<(OsString, OsString)>,
        mut running: BTreeSet<String>,
    ) -> ProviderResult<()> {
        let config = files.config.clone();
        let session_id = files.session_id.clone();
        let step_folder = files.step_folder.clone();
        // Earlier session files hold copies of earlier requests: never leave them.
        running.insert(session_id.clone());
        sweep(&config, &self.home.join("steps"), &running, &self.home, None);
        std::fs::create_dir(&step_folder).map_err(|error| local(error.to_string()))?;
        let prepared = self.prepare(messages, tools, route)?;
        let searching = tool_search(&prepared.native_tools);
        if searching {
            // Deferred tool list (#204): Claude Code's own tool search loads deferred tools.
            let value = OsString::from("true");
            match environment.iter_mut().find(|(key, _)| key == "ENABLE_TOOL_SEARCH") {
                Some((_, existing)) => *existing = value,
                None => environment.push(("ENABLE_TOOL_SEARCH".into(), value)),
            }
        }
        let system_path = step_folder.join("system.txt");
        let history_path = step_folder.join("history.jsonl");
        std::fs::write(&system_path, prepared.system.as_bytes()).map_err(|error| local(error.to_string()))?;
        let mut transcript = String::new();
        for record in &prepared.records {
            transcript.push_str(&pyjson::dumps(record, Dump::DEFAULT));
            transcript.push('\n');
        }
        if route.is_none() && cfg!(windows) {
            // `write_text`: text mode writes each newline as CRLF on Windows.
            transcript = transcript.replace('\n', "\r\n");
        }
        std::fs::write(&history_path, transcript.as_bytes()).map_err(|error| local(error.to_string()))?;
        let mut arguments: Vec<String> = [
            "--print",
            "--safe-mode",
            "--restricted",
            "--tools",
            if searching { TOOL_SEARCH } else { "" },
            "--disable-slash-commands",
            "--setting-sources",
            "",
            "--settings",
            r#"{"disableAllHooks":true,"autoMemoryEnabled":false}"#,
            "--strict-mcp-config",
        ]
        .map(String::from)
        .to_vec();
        if route.is_none() {
            arguments.push("--no-session-persistence".into());
        }
        arguments.extend(
            [
                "--no-chrome",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--verbose",
                "--include-partial-messages",
                "--system-prompt-file",
            ]
            .map(String::from),
        );
        arguments.push(system_path.to_string_lossy().into_owned());
        arguments.push("--model".into());
        arguments.push(self.wire().to_owned());
        if !prepared.records.is_empty() {
            arguments.extend([
                "--resume".into(),
                history_path.to_string_lossy().into_owned(),
                "--fork-session".into(),
            ]);
        }
        if route.is_some() {
            // Persisted only so Sylvia can copy the CLI's context records; deleted below (or,
            // for a kept process, when that process closes).
            arguments.extend(["--session-id".into(), session_id.clone()]);
        }
        if prepared.native_tools.is_empty() {
            arguments.extend(["--mcp-config".into(), r#"{"mcpServers":{}}"#.into()]);
        } else {
            let mut allowed: Vec<String> = if searching { vec![TOOL_SEARCH.into()] } else { Vec::new() };
            allowed.extend(
                prepared.native_tools.iter().map(|tool| format!("{PREFIX}{}", py_str(&tool["name"]))),
            );
            arguments.extend([
                "--mcp-config".into(),
                r#"{"mcpServers":{"sylvia":{"type":"sdk","name":"sylvia"}}}"#.into(),
                "--allowedTools".into(),
                allowed.join(","),
            ]);
        }
        let connection = ClaudeConnection::new(
            executable,
            workspace,
            arguments.into_iter().map(OsString::from).collect(),
            environment.clone(),
        );
        files.connection = Some(connection.clone());
        *self.active.lock().expect("active connection") = Some(connection.clone());
        let mut step = Step::new(self.wire(), prepared.native_tools.clone(), JsonMap::new());
        let persist = match (route, plan.is_some()) {
            (None, _) => Json::Bool(false),
            (Some(_), true) => text(KEPT_SESSION),
            (Some(_), false) => text(TRANSIENT_SESSION),
        };
        let mut yielded = false;
        let outcome: ProviderResult<()> = async {
            self.audit(object(vec![
                ("method", text("claude/native_step")),
                ("system", text(&prepared.system)),
                ("transcript", Json::Array(prepared.records.clone())),
                ("input", Json::Object(prepared.prompt.clone())),
                ("tools", Json::Array(prepared.native_tools.clone())),
                (
                    "controls",
                    Json::Object(object(vec![
                        ("safe_mode", Json::Bool(true)),
                        ("builtin_tools", Json::Array(if searching { vec![text(TOOL_SEARCH)] } else { Vec::new() })),
                        ("persist_session", persist),
                        ("output_allowance", text("runtime_managed")),
                        ("tool_choice", text("runtime_managed")),
                    ])),
                ),
                ("downstream_model_request", text("not_exposed_by_runtime")),
            ]))
            .await;
            let starting = connection.clone();
            tokio::task::spawn_blocking(move || starting.start()).await.map_err(|error| local(error.to_string()))??;
            connection.write(&serde_json::json!({
                "type": "control_request",
                "request_id": "initialize",
                "request": {
                    "subtype": "initialize",
                    "sdkMcpServers": if prepared.native_tools.is_empty() { serde_json::json!([]) } else { serde_json::json!(["sylvia"]) },
                },
            }))?;
            connection.write(&Json::Object(prepared.prompt.clone()))?;
            let session = Session { config: &config, session_id: &session_id, route };
            self.run_events(&connection, &mut step, &session, Some(&prepared.prompt), timeout, plan.is_some(), None, co, &mut yielded)
                .await
        }
        .await;
        let mut kept_here = false;
        if let (Ok(()), Some(plan)) = (&outcome, plan.as_mut()) {
            if !step.keepable {
                plan.reuse.insert("kept".into(), Json::Bool(false));
                plan.reuse.insert("not_kept".into(), step.not_kept.map_or(Json::Null, text));
            } else {
                let kept = KeptProcess::new(
                    connection.clone(),
                    &plan.home,
                    &plan.model,
                    &plan.system,
                    &plan.tools_key,
                    &session_id,
                    &step_folder,
                    &config,
                    files.cache.as_deref(),
                    KeptState {
                        held: plan.history.clone(),
                        spoken: step.text_parts.concat(),
                        pending: step.call_event.clone(),
                        call_id: step.call_id.clone(),
                        call_arguments: step.call_arguments.clone(),
                        model_usage: step.model_usage_after.clone().unwrap_or_default(),
                    },
                );
                kept.on_close(closer(&self.home, &config, &session_id, &step_folder, files.cache.clone()));
                // The kept process owns its connection and files from here.
                files.connection = None;
                files.armed = false;
                let evicted = kept_processes().keep(Arc::clone(&kept), plan.generation);
                let ours = evicted.iter().any(|each| Arc::ptr_eq(each, &kept));
                plan.reuse.insert("kept".into(), Json::Bool(!ours));
                kept_here = !ours;
                // Closing an evicted process (ours included) runs its own cleanup.
                Self::close_kept(evicted).await;
            }
        }
        if !kept_here {
            Self::close_owned(&connection).await;
        }
        *self.active.lock().expect("active connection") = None;
        if let Some(plan) = plan.as_ref() {
            self.audit_reuse(&plan.reuse).await;
        }
        outcome
    }

    /// `_run_events`: read one native reply to its end, a Sylvia tool call or a result.
    #[allow(clippy::too_many_arguments)]
    async fn run_events(
        &self,
        connection: &ClaudeConnection,
        step: &mut Step,
        session: &Session<'_>,
        prompt: Option<&JsonMap>,
        timeout: Duration,
        keep: bool,
        answered_call: Option<&str>,
        co: &Co<ProviderResult<ProviderStreamChunk>>,
        yielded: &mut bool,
    ) -> ProviderResult<()> {
        let mut event = JsonMap::new();
        let outcome = self
            .read_reply(
                connection,
                step,
                session,
                prompt,
                timeout,
                keep,
                answered_call,
                co,
                yielded,
                &mut event,
            )
            .await;
        match outcome {
            Ok(()) => Ok(()),
            Err(Fault::Provider(error)) if error.is(ErrorKind::InvalidResponse) => {
                let reason = invalid_reason(&error.message).unwrap_or("invalid_response");
                self.audit_invalid_response(reason, &event, &step.pending, None).await;
                Err(error)
            }
            Err(Fault::Malformed(class)) => {
                self.audit_invalid_response("malformed_event", &event, &step.pending, Some(class)).await;
                Err(invalid("Claude returned an invalid runtime response."))
            }
            Err(Fault::Provider(error)) if error.is(ErrorKind::Connection) => {
                Err(ProviderError::new(ErrorKind::IncompleteStream, error.message))
            }
            Err(Fault::Provider(error)) => Err(error),
        }
    }

    async fn emit(
        co: &Co<ProviderResult<ProviderStreamChunk>>,
        yielded: &mut bool,
        chunk: ProviderStreamChunk,
    ) {
        *yielded = true;
        co.yield_item(Ok(chunk)).await;
    }

    #[allow(clippy::too_many_arguments)]
    async fn read_reply(
        &self,
        connection: &ClaudeConnection,
        step: &mut Step,
        session: &Session<'_>,
        prompt: Option<&JsonMap>,
        timeout: Duration,
        keep: bool,
        answered_call: Option<&str>,
        co: &Co<ProviderResult<ProviderStreamChunk>>,
        yielded: &mut bool,
        event_out: &mut JsonMap,
    ) -> Read<()> {
        let mut native_state = NativeContent::default();
        let mut native_content: Option<Vec<Json>> = None;
        let mut has_native_stream = false;
        let mut deferred_call: Option<JsonMap> = None;
        let mut blocks: Vec<(Json, JsonMap)> = Vec::new();
        let mut input_usage = JsonMap::new();
        let mut streamed_text = false;
        let mut resolved_model: Option<Json> = None;
        // Deferred tool list (#204): a step may hold several model requests, each ToolSearch
        // call answered by Claude Code itself before the model calls a Sylvia tool or narrates.
        let searching = tool_search(&step.tools);
        let mut searched_here = false;
        let mut said_here = false;
        loop {
            let event = match deferred_call.take() {
                Some(call) if native_state.completed => call,
                other => {
                    deferred_call = other;
                    next_event(connection, timeout).await?
                }
            };
            *event_out = event.clone();
            let whole = Json::Object(event.clone());
            let kind = event.get("type").and_then(Json::as_str);
            let parent = event.get("parent_tool_use_id").is_some_and(truthy);
            match kind {
                Some("control_request") => {
                    let request = get_or_empty(&whole, "request")?;
                    let subtype = get(&request, "subtype")?;
                    let server = get(&request, "server_name")?;
                    if !is_text(subtype, "mcp_message") || !is_text(server, "sylvia") {
                        connection.write(&serde_json::json!({
                            "type": "control_response",
                            "response": {
                                "subtype": "error",
                                "request_id": event.get("request_id").cloned().unwrap_or(Json::Null),
                                "error": "This operation is unavailable in Sylvia.",
                            },
                        }))?;
                        continue;
                    }
                    let message = get_or_empty(&request, "message")?;
                    let method = get(&message, "method")?.cloned().unwrap_or(Json::Null);
                    if is_text(Some(&method), "tools/call") {
                        if has_native_stream && !native_state.completed {
                            if deferred_call.is_none() {
                                deferred_call = Some(event);
                            }
                            continue;
                        }
                        let params = get_or_empty(&message, "params")?;
                        let original = get(&params, "name")?.cloned().unwrap_or(Json::Null);
                        let arguments = get(&params, "arguments")?
                            .cloned()
                            .unwrap_or_else(|| Json::Object(JsonMap::new()));
                        let wanted = format!("{PREFIX}{}", py_str(&original));
                        let mut candidate = None;
                        for call in &step.pending {
                            if is_text(get(call, "name")?, &wanted)
                                && py_eq(get(call, "input")?.unwrap_or(&Json::Null), &arguments)
                            {
                                candidate = Some(call.clone());
                                break;
                            }
                        }
                        let allowed = match hashable(&original)? {
                            Json::String(name) => step.allowed.contains(&name),
                            _ => false,
                        };
                        let candidate = match candidate {
                            Some(candidate)
                                if allowed
                                    && candidate
                                        .get("id")
                                        .and_then(Json::as_str)
                                        .is_some_and(|id| !id.is_empty())
                                    && candidate.get("input").is_some_and(Json::is_object) =>
                            {
                                candidate
                            }
                            _ => {
                                return Err(invalid(
                                    "Claude requested an unavailable or uncorrelated Sylvia operation.",
                                )
                                .into())
                            }
                        };
                        let call_id = candidate["id"].as_str().expect("an id").to_owned();
                        let call = ToolCall::new(
                            call_id.clone(),
                            original.as_str().expect("an allowed name"),
                            pyjson::dumps(&candidate["input"], Dump::DEFAULT),
                        );
                        // Reaching the actual MCP call means native permissions admitted it. No
                        // result is sent to Claude and no game action runs in this child: a kept
                        // process waits on the call until the next request answers it.
                        let flushed = await_flush(session, prompt, step.searches.len()).await;
                        // Claude Code can report one call twice (its stream and an assistant
                        // event), so the calls of the reply are counted by id.
                        let ids: BTreeSet<String> = step
                            .pending
                            .iter()
                            .map(|block| py_str(block.get("id").unwrap_or(&Json::Null)))
                            .collect();
                        if keep && ids.len() > 1 {
                            // Sylvia takes one call; a kept process would later send the dropped
                            // ones. Close it here, as a per-step process is.
                            step.keepable = false;
                            step.not_kept = Some("parallel_calls");
                        } else if keep && searched_here {
                            // A ToolSearch call beside this one stays in the process's own
                            // message, which the journal does not keep (it holds the call).
                            step.keepable = false;
                            step.not_kept = Some("search_beside_call");
                        }
                        if keep && step.keepable {
                            step.call_event = Some(event.clone());
                            step.call_id = call_id.clone();
                            step.call_arguments = candidate["input"].clone();
                        } else {
                            Self::close_owned(connection).await;
                        }
                        self.collect(session, prompt, flushed, !keep, step).await;
                        self.collect_answer(session, answered_call, step).await;
                        self.audit_searches(step).await;
                        step.response.tool_calls = vec![call.clone()];
                        step.response.finish_reason = PyValue::from_json(text("tool_calls"));
                        if let Some(content) = &native_content {
                            let mut continuation_blocks = Vec::new();
                            for block in content {
                                let keep_block = !is_text(block.get("type"), "tool_use")
                                    || is_text(block.get("id"), &call_id);
                                if keep_block {
                                    let mut block = block.clone();
                                    if is_text(block.get("type"), "tool_use") {
                                        block["name"] = call.name.to_json();
                                    }
                                    continuation_blocks.push(block);
                                }
                            }
                            step.response.anthropic_content = Some(continuation_blocks.clone());
                            Self::emit(
                                co,
                                yielded,
                                ProviderStreamChunk {
                                    anthropic_content: Some(continuation_blocks),
                                    ..Default::default()
                                },
                            )
                            .await;
                        }
                        Self::emit(
                            co,
                            yielded,
                            ProviderStreamChunk::tool_call(serde_json::json!({
                                "index": 0,
                                "id": call.id.to_json(),
                                "type": "function",
                                "function": {"name": call.name.to_json(), "arguments": call.arguments.to_json()},
                            })),
                        )
                        .await;
                        break;
                    }
                    let result = match method.as_str() {
                        Some("initialize") => serde_json::json!({
                            "protocolVersion": "2025-11-25",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "sylvia", "version": "1"},
                        }),
                        Some("tools/list") => serde_json::json!({"tools": step.tools}),
                        Some("notifications/initialized" | "ping") => Json::Object(JsonMap::new()),
                        _ => {
                            hashable(&method)?;
                            return Err(invalid("Claude requested an unsupported runtime operation.").into());
                        }
                    };
                    let request_id = item(&whole, "request_id")?.clone();
                    let message_id = get(&message, "id")?.cloned().unwrap_or(Json::from(0));
                    connection.write(&serde_json::json!({
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": request_id,
                            "response": {"mcp_response": {"jsonrpc": "2.0", "id": message_id, "result": result}},
                        },
                    }))?;
                }
                Some("system") if is_text(event.get("subtype"), "init") => {
                    let exposed = event.get("tools");
                    let mut expected: BTreeSet<String> =
                        step.allowed.iter().map(|name| format!("{PREFIX}{name}")).collect();
                    if searching {
                        expected.insert(TOOL_SEARCH.into());
                    }
                    let names = match exposed {
                        Some(Json::Array(names)) if names.iter().all(Json::is_string) => Some(
                            names
                                .iter()
                                .filter_map(|name| name.as_str().map(str::to_owned))
                                .collect::<BTreeSet<_>>(),
                        ),
                        _ => None,
                    };
                    if names.as_ref() != Some(&expected) {
                        return Err(
                            invalid("Claude did not expose exactly this request's Sylvia tools.").into()
                        );
                    }
                    resolved_model = Some(event.get("model").cloned().unwrap_or(Json::Null));
                    let selected = self.wire();
                    if selected.starts_with("claude-")
                        && resolved_model.as_ref().and_then(Json::as_str) != Some(selected)
                    {
                        return Err(ProviderError::new(
                            ErrorKind::ModelNotFound,
                            "Claude did not select the requested model.",
                        )
                        .into());
                    }
                    if let Some(Json::String(model)) = &resolved_model {
                        step.response.model = model.clone();
                    }
                }
                Some("stream_event") if !parent => {
                    let native = get_or_empty(&whole, "event")?;
                    let subtype = get(&native, "type")?.cloned();
                    let subtype = subtype.as_ref().and_then(Json::as_str).map(str::to_owned);
                    has_native_stream = true;
                    if searching && subtype.as_deref() == Some("message_start") {
                        // Each model request of the step is its own native message; only the
                        // last one is the step's reply.
                        native_state = NativeContent::default();
                        native_content = None;
                        searched_here = false;
                        said_here = false;
                        let message = native.get("message").filter(|message| truthy(message));
                        let message_id = match message {
                            None => None,
                            Some(message) => get(message, "id")?.cloned(),
                        };
                        step.final_message_id = message_id.and_then(|id| id.as_str().map(str::to_owned));
                    }
                    if let Some(completed) = native_state.accept(&as_dict(&native)?)? {
                        native_content = Some(completed);
                    }
                    match subtype.as_deref() {
                        Some("message_start") => {
                            let message = get_or_empty(&native, "message")?;
                            let actual = get(&message, "model")?.cloned().unwrap_or(Json::Null);
                            if let Some(resolved) =
                                resolved_model.as_ref().filter(|resolved| truthy(resolved))
                            {
                                if !py_eq(&actual, resolved) {
                                    return Err(ProviderError::new(
                                        ErrorKind::ModelNotFound,
                                        "Claude changed models during this response.",
                                    )
                                    .into());
                                }
                            }
                            input_usage = as_dict(&get_or_empty(&message, "usage")?)?;
                        }
                        Some("content_block_start") => {
                            let key = hashable(item(&native, "index")?)?;
                            let block = as_dict(item(&native, "content_block")?)?;
                            match find(&mut blocks, &key) {
                                Some(slot) => *slot = block,
                                None => blocks.push((key, block)),
                            }
                        }
                        Some("content_block_delta") => {
                            let delta = get_or_empty(&native, "delta")?;
                            let delta_type = get(&delta, "type")?.cloned();
                            match delta_type.as_ref().and_then(Json::as_str) {
                                Some("text_delta") => {
                                    let Json::String(piece) =
                                        get(&delta, "text")?.cloned().unwrap_or_else(|| text(""))
                                    else {
                                        return Err(invalid("Claude returned invalid text.").into());
                                    };
                                    step.text_parts.push(piece.clone());
                                    streamed_text = true;
                                    said_here = true;
                                    Self::emit(co, yielded, ProviderStreamChunk::content(piece)).await;
                                }
                                Some("thinking_delta") => {
                                    let thinking =
                                        get(&delta, "thinking")?.cloned().unwrap_or_else(|| text(""));
                                    let reasoning =
                                        thinking.as_str().map_or_else(|| py_str(&thinking), str::to_owned);
                                    Self::emit(
                                        co,
                                        yielded,
                                        ProviderStreamChunk {
                                            reasoning_content: reasoning,
                                            ..Default::default()
                                        },
                                    )
                                    .await;
                                }
                                Some("input_json_delta") => {
                                    let key = hashable(item(&native, "index")?)?;
                                    let block =
                                        find(&mut blocks, &key).ok_or(Fault::Malformed("KeyError"))?;
                                    let more =
                                        get(&delta, "partial_json")?.cloned().unwrap_or_else(|| text(""));
                                    let joined = match (
                                        block.get("partial_json").cloned().unwrap_or_else(|| text("")),
                                        more,
                                    ) {
                                        (Json::String(before), Json::String(more)) => before + &more,
                                        _ => return Err(Fault::Malformed("TypeError")),
                                    };
                                    block.insert("partial_json".into(), Json::String(joined));
                                }
                                _ => {}
                            }
                        }
                        Some("content_block_stop") => {
                            let key = hashable(item(&native, "index")?)?;
                            let mut block =
                                match blocks.iter().position(|(existing, _)| py_eq(existing, &key)) {
                                    Some(position) => blocks.remove(position).1,
                                    None => JsonMap::new(),
                                };
                            if is_text(block.get("type"), "tool_use") {
                                if let Some(fragments) = block.shift_remove("partial_json") {
                                    // An operation without arguments streams an empty string;
                                    // its input stays the start block's {}.
                                    if truthy(&fragments) {
                                        let Json::String(fragments) = fragments else {
                                            return Err(Fault::Malformed("TypeError"));
                                        };
                                        let input = pyjson::loads(&fragments)
                                            .map_err(|_| Fault::Malformed("JSONDecodeError"))?;
                                        block.insert("input".into(), input);
                                    }
                                }
                                if searching && is_text(block.get("name"), TOOL_SEARCH) {
                                    // Claude Code answers it itself; it is no Sylvia call. Text
                                    // the model wrote before it was streamed as it came.
                                    if !searched_here {
                                        let mut round = object(vec![
                                            ("loaded", Json::Array(Vec::new())),
                                            ("usage", Json::Object(JsonMap::new())),
                                        ]);
                                        if said_here {
                                            round.insert("said".into(), Json::Bool(true));
                                        }
                                        step.searches.push(round);
                                    }
                                    searched_here = true;
                                } else {
                                    step.pending.push(Json::Object(block));
                                }
                            }
                        }
                        Some("message_delta") => {
                            let more = get_or_empty(&native, "usage")?;
                            input_usage.extend(as_dict(&more)?);
                            usage(&mut step.response, &Json::Object(input_usage.clone()));
                            if searched_here {
                                if let Some(round) = step.searches.last_mut() {
                                    let counts = input_usage
                                        .iter()
                                        .filter(|(_, value)| matches!(value, Json::Number(number) if !number.is_f64()))
                                        .map(|(key, value)| (key.clone(), value.clone()))
                                        .collect();
                                    round.insert("usage".into(), Json::Object(counts));
                                }
                            }
                        }
                        _ => {}
                    }
                }
                Some("user") if searching && !parent && !step.searches.is_empty() => {
                    // Claude Code's answer to a ToolSearch call: the tools it loaded.
                    let message = event
                        .get("message")
                        .filter(|message| truthy(message))
                        .cloned()
                        .unwrap_or_else(|| Json::Object(JsonMap::new()));
                    let content = get(&message, "content")?.cloned();
                    if let Some(Json::Array(content)) = content {
                        for block in &content {
                            let found = block.as_object().and_then(|block| block.get("content"));
                            if let Some(Json::Array(found)) = found {
                                for entry in found {
                                    if entry.is_object() && is_text(entry.get("type"), "tool_reference") {
                                        let name = py_str(entry.get("tool_name").unwrap_or(&text("")));
                                        let loaded = name.strip_prefix(PREFIX).unwrap_or(&name).to_owned();
                                        if let Some(Json::Array(list)) =
                                            step.searches.last_mut().and_then(|round| round.get_mut("loaded"))
                                        {
                                            list.push(Json::String(loaded));
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                Some("assistant") if !parent => {
                    let message = get_or_empty(&whole, "message")?;
                    let content = get(&message, "content")?.cloned().unwrap_or(Json::Array(Vec::new()));
                    for block in crate::pyobj::py_iter(&content).map_err(|_| Fault::Malformed("TypeError"))? {
                        if searching && is_text(get(&block, "name")?, TOOL_SEARCH) {
                            continue;
                        }
                        if is_text(get(&block, "type")?, "tool_use") {
                            let id = block.get("id").cloned().unwrap_or(Json::Null);
                            let mut known = false;
                            for pending in &step.pending {
                                known |= py_eq(get(pending, "id")?.unwrap_or(&Json::Null), &id);
                            }
                            if !known {
                                step.pending.push(block);
                            }
                        }
                    }
                }
                Some("result") => {
                    if event.get("is_error").is_some_and(truthy) || !is_text(event.get("subtype"), "success")
                    {
                        let failure = runtime_failure(&event);
                        if failure.is(ErrorKind::TokenLimit) {
                            self.refuse_reported_window();
                        }
                        return Err(failure.into());
                    }
                    if event.get("permission_denials").is_some_and(truthy) || !step.pending.is_empty() {
                        return Err(invalid(
                            "Claude could not admit the requested operation under its native permissions.",
                        )
                        .into());
                    }
                    if !streamed_text {
                        if let Some(Json::String(result)) = event.get("result") {
                            step.text_parts.push(result.clone());
                            Self::emit(co, yielded, ProviderStreamChunk::content(result.clone())).await;
                        }
                    }
                    if !(keep && step.response.usage_reported == Some(true)) {
                        // A kept process's result reports the whole native turn, which can span
                        // calls answered in place: its step's own usage is the one its message
                        // stream reported.
                        let reported =
                            event.get("usage").cloned().unwrap_or_else(|| Json::Object(JsonMap::new()));
                        usage(&mut step.response, &reported);
                    }
                    let by_model = event.get("modelUsage").cloned().unwrap_or(Json::Null);
                    let model = step.response.model.clone();
                    let since = keep.then(|| step.model_usage_before.clone());
                    step.model_usage_after = self.audit_model_usage(&by_model, &model, since.as_ref()).await;
                    self.observe_context_window(&by_model, &model);
                    break;
                }
                Some("rate_limit_event") => {
                    let info = get_or_empty(&whole, "rate_limit_info")?;
                    if is_text(get(&info, "status")?, "rejected") {
                        return Err(ProviderError::rate_limit(
                            "Claude reports that this account has reached its usage limit.",
                            None,
                        )
                        .into());
                    }
                }
                _ => {}
            }
        }
        if step.response.tool_calls.is_empty() {
            let flushed = await_flush(session, prompt, step.searches.len()).await;
            if !keep {
                Self::close_owned(connection).await;
            }
            self.collect(session, prompt, flushed, !keep, step).await;
            self.collect_answer(session, answered_call, step).await;
            self.audit_searches(step).await;
        }
        let spoken = step.text_parts.concat();
        step.response.content = (!spoken.is_empty()).then_some(spoken);
        *self.last_response.lock().expect("last response") = step.response.clone();
        let context = HookContext {
            provider_key: "claude_runtime".into(),
            model_id: self.model.id.clone(),
            stream: true,
            response: Some(step.response.clone()),
            ..HookContext::default()
        };
        self.hooks.fire_on_response(&context).await;
        Self::emit(
            co,
            yielded,
            ProviderStreamChunk {
                finish_reason: Some(step.response.finish_reason.clone()),
                ..Default::default()
            },
        )
        .await;
        Ok(())
    }

    /// `_audit_searches`: a step's ToolSearch rounds, tools loaded and each round's usage.
    async fn audit_searches(&self, step: &Step) {
        if step.searches.is_empty() {
            return;
        }
        let rounds = step.searches.iter().cloned().map(Json::Object).collect();
        self.audit(object(vec![
            ("direction", text("receive")),
            ("method", text("claude/tool_search")),
            ("params", Json::Object(object(vec![("rounds", Json::Array(rounds))]))),
        ]))
        .await;
    }

    /// `_collect_answer`: save the tool-result message a kept process stored for the call it
    /// was answered on. Never fatal.
    async fn collect_answer(&self, session: &Session<'_>, call_id: Option<&str>, step: &Step) {
        let Some(route) = session.route else { return };
        let Some(on_collected) = route.on_collected.clone() else { return };
        let Some(call_id) = call_id.filter(|id| !id.is_empty()) else { return };
        let (config, session_id, call_id) =
            (session.config.to_owned(), session.session_id.to_owned(), call_id.to_owned());
        let (final_id, requests) = if step.searches.is_empty() {
            (None, None)
        } else {
            // The ToolSearch rounds after the answer are replayed with it.
            (step.final_message_id.clone(), Some(step.searches.len() + 1))
        };
        let result = tokio::task::spawn_blocking(move || {
            collect_answer(&config, &session_id, &call_id, 2.0, final_id.as_deref(), requests)
        })
        .await;
        let Ok(result) = result else { return };
        if result.status != "collected" {
            crate::log::warning(&format!(
                "The message Claude Code stored for an answered call was not found ({}); a fresh start replays the per-step form.",
                result.reason.unwrap_or("None")
            ));
            return;
        }
        on_collected(result).await;
    }

    /// `_collect`: copy the CLI's context records for the ledger, then delete its session file.
    /// Never fatal.
    async fn collect(
        &self,
        session: &Session<'_>,
        prompt: Option<&JsonMap>,
        flushed: bool,
        delete: bool,
        step: &Step,
    ) {
        let (Some(route), Some(prompt)) = (session.route, prompt) else { return };
        let files: Vec<PathBuf> = session_files(session.config, session.session_id)
            .into_iter()
            .filter(|path| path.extension().is_some_and(|ext| ext == "jsonl"))
            .collect();
        let content = &prompt["message"]["content"];
        let final_id = if step.searches.is_empty() { None } else { step.final_message_id.as_deref() };
        let mut result = collect(files.first().map(PathBuf::as_path), content, final_id);
        if !flushed && result.status == "collected" {
            // The CLI may still have had records queued: what was read may be incomplete.
            result = NativeCollection::unknown("flush_incomplete");
        }
        if delete {
            delete_session(session.config, session.session_id, &self.home);
        }
        if result.status != "collected" {
            crate::log::warning(&format!(
                "Claude runtime context records are unknown for this step ({}); the next request starts a new context journal generation.",
                result.reason.unwrap_or("None")
            ));
        }
        let Some(on_collected) = route.on_collected.clone() else { return };
        if !(!result.records.is_empty() || result.status != "collected" || route.first_step()) {
            return;
        }
        on_collected(result).await;
    }

    /// `_audit_invalid_response`: which check refused a response, never content.
    async fn audit_invalid_response(
        &self,
        reason: &str,
        event: &JsonMap,
        pending: &[Json],
        error: Option<&str>,
    ) {
        let empty = Json::Object(JsonMap::new());
        let part = |value: Option<&Json>| -> Json {
            value.filter(|value| value.is_object()).cloned().unwrap_or_else(|| empty.clone())
        };
        let request = part(event.get("request"));
        let candidates = [
            event.get("subtype").cloned(),
            part(event.get("event")).get("type").cloned(),
            part(request.get("message")).get("method").cloned(),
            request.get("subtype").cloned(),
        ];
        let subtype = candidates.into_iter().flatten().find(Json::is_string).unwrap_or(Json::Null);
        let denials = match event.get("permission_denials") {
            Some(Json::Array(denials)) => denials
                .iter()
                .filter(|denial| denial.is_object())
                .map(|denial| text(&py_str(denial.get("tool_name").unwrap_or(&Json::Null))))
                .collect(),
            _ => Vec::new(),
        };
        let facts = object(vec![
            ("reason", text(reason)),
            ("event", event.get("type").filter(|kind| kind.is_string()).cloned().unwrap_or(Json::Null)),
            ("subtype", subtype),
            ("exception", error.map_or(Json::Null, text)),
            ("permission_denials", Json::Array(denials)),
            (
                "pending_tool_uses",
                Json::Array(
                    pending
                        .iter()
                        .map(|block| text(&py_str(block.get("name").unwrap_or(&Json::Null))))
                        .collect(),
                ),
            ),
        ]);
        crate::log::warning(&format!(
            "The Claude adapter refused a response: {}",
            crate::pyobj::py_repr(&Json::Object(facts.clone()))
        ));
        self.audit(object(vec![
            ("direction", text("receive")),
            ("method", text("claude/invalid_response")),
            ("params", Json::Object(facts)),
        ]))
        .await;
    }

    /// `_audit_model_usage`: the counts and windows Claude Code reports per model, never its
    /// price estimate. Returns the reported counts.
    async fn audit_model_usage(
        &self,
        usage_by_model: &Json,
        resolved_model: &str,
        since: Option<&JsonMap>,
    ) -> Option<JsonMap> {
        let (counts, recorded) = model_usage(usage_by_model, since)?;
        let mut params =
            object(vec![("model", text(resolved_model)), ("model_usage", Json::Object(recorded))]);
        if since.is_some() {
            params.insert("since_previous_step".into(), Json::Bool(true));
        }
        self.audit(object(vec![
            ("direction", text("receive")),
            ("method", text("claude/result_model_usage")),
            ("params", Json::Object(params)),
        ]))
        .await;
        Some(counts)
    }

    /// `_apply_runtime_window`: set or clear the window planned within; a saved allowance can
    /// only lower it.
    fn apply_runtime_window(&self, value: Option<i64>, source: &str, observed_at: Option<String>) {
        let model = &self.model;
        let observed_at = value.map(|_| observed_at.unwrap_or_else(|| (self.clock)()));
        let capacity = resolve_context_capacity(&CapacityInputs {
            declared_context_window: model.declared_context_window,
            declared_context_source: Some(&model.declared_context_source),
            runtime_context_window: value,
            runtime_context_source: Some(if value.is_some() { source } else { "unknown" }),
            configured_context_window: model.configured_context_window,
            legacy_context_window: None,
            observed_at: None,
            declared_observed_at: model.declared_context_observed_at.as_deref(),
            runtime_observed_at: observed_at.as_deref(),
            configured_observed_at: model.configured_context_observed_at.as_deref(),
        });
        let configured_observed_at = model.configured_context_observed_at.clone();
        self.live.update(|figures| {
            figures.runtime_context_window = capacity.runtime_context_window;
            figures.runtime_context_source = capacity.runtime_context_source.clone();
            figures.runtime_context_observed_at = capacity.runtime_observed_at.clone();
            figures.context_observed_at =
                observed_at.clone().filter(|at| !at.is_empty()).or(configured_observed_at);
            figures.context_window = capacity.effective_context_window;
            figures.context_window_source = capacity.effective_context_source.clone();
            figures.configured_runtime_mismatch = capacity.configured_runtime_mismatch;
        });
    }

    /// `_observe_context_window`: plan later requests within the window Claude Code reports.
    fn observe_context_window(&self, usage_by_model: &Json, resolved_model: &str) {
        if self.reported_window_refused.load(Ordering::SeqCst) {
            return;
        }
        let Some(value) = reported_window(usage_by_model, Some(resolved_model), self.wire()) else { return };
        if Some(value) != self.model.runtime_context_window() {
            self.apply_runtime_window(Some(value), "runtime_reported", None);
        }
    }

    /// `_refuse_reported_window`: Claude refused a request as too large; stop planning within
    /// that window for as long as this provider lives.
    fn refuse_reported_window(&self) {
        let window = self.model.runtime_context_window();
        if self.reported_window_refused.load(Ordering::SeqCst) || window.is_none() {
            return;
        }
        self.reported_window_refused.store(true, Ordering::SeqCst);
        crate::log::warning(&format!(
            "Claude refused a request planned within a context window of {} tokens ({}); planning within the saved or default allowance from now on.",
            window.expect("a window"),
            self.model.runtime_context_source()
        ));
        self.apply_runtime_window(None, "runtime_reported", None);
    }

    /// `release_idle`: close the processes kept for this home and model.
    async fn release(&self) {
        let released = kept_processes().release(Some(&python_resolve(&self.home)), Some(self.wire()));
        Self::close_kept(released).await;
    }
}

/// Where a step's session lives.
struct Session<'a> {
    config: &'a Path,
    session_id: &'a str,
    route: Option<&'a NativeRoute>,
}

fn make_dir(path: &Path) -> ProviderResult<()> {
    match std::fs::create_dir(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists && path.is_dir() => Ok(()),
        Err(error) => Err(local(error.to_string())),
    }
}

/// `_closer`: remove a kept process's private files once the process itself has closed.
fn closer(
    home: &Path,
    config: &Path,
    session_id: &str,
    step: &Path,
    cache: Option<PathBuf>,
) -> crate::claude_kept::OnClose {
    let (home, config, session_id, step) =
        (home.to_owned(), config.to_owned(), session_id.to_owned(), step.to_owned());
    Box::new(move |_kept| {
        delete_session(&config, &session_id, &home);
        if step.exists() {
            remove_step_folder(&step, &home);
        }
        remove_debug_logs(cache.as_deref(), &home);
        discard_active(&python_resolve(&home), &session_id);
    })
}

/// `_usage(response, usage)`: only measured counts.
fn usage(response: &mut ProviderResponse, reported: &Json) {
    if let Some((tokens, cache)) = measured_usage(reported) {
        response.usage = tokens;
        response.usage_reported = Some(true);
        response.cache_usage = cache;
    }
}

async fn next_event(connection: &ClaudeConnection, timeout: Duration) -> Result<JsonMap, ProviderError> {
    let connection = connection.clone();
    tokio::task::spawn_blocking(move || connection.next_event(timeout))
        .await
        .unwrap_or_else(|error| Err(ProviderError::new(ErrorKind::Connection, error.to_string())))
}

/// `_await_flush`: let the CLI finish writing this step's records before its process closes.
async fn await_flush(session: &Session<'_>, prompt: Option<&JsonMap>, searches: usize) -> bool {
    let (Some(_), Some(prompt)) = (session.route, prompt) else { return true };
    let (config, session_id, content) =
        (session.config.to_owned(), session.session_id.to_owned(), prompt["message"]["content"].clone());
    let requests = (searches > 0).then_some(searches + 1);
    tokio::task::spawn_blocking(move || wait_for_flush(&config, &session_id, &content, 2.0, requests))
        .await
        .unwrap_or(false)
}

impl Provider for ClaudeProvider {
    fn model_info(&self) -> Option<&ModelInfo> {
        Some(&self.model)
    }

    fn hooks(&self) -> Option<&HookManager> {
        Some(&self.hooks)
    }

    fn generate(
        &self,
        request: ProviderRequest,
    ) -> BoxFuture<'_, ProviderResult<crate::types::ProviderResponse>> {
        Box::pin(async move {
            let mut stream = self.generate_stream(request);
            while let Some(chunk) = stream.next().await {
                chunk?;
            }
            Ok(self.last_response.lock().expect("last response").clone())
        })
    }

    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        generator(move |co| async move {
            if let Err(error) = self.stream(request, &co).await {
                co.yield_item(Err(error)).await;
            }
        })
    }

    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move {
            let active = self.active.lock().expect("active connection").clone();
            if let Some(active) = active {
                Self::close_owned(&active).await;
            }
            self.release().await;
        })
    }

    fn release_idle(&self) -> BoxFuture<'_, ()> {
        Box::pin(self.release())
    }
}
