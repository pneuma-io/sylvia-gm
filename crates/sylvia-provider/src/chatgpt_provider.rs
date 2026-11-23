//! `provider/_chatgpt.py`'s `ChatGPTProvider`: subscription inference through Codex, with
//! Sylvia retaining the tool loop. Lane 5.
//!
//! Each call runs one turn on an ephemeral runtime thread loaded from Sylvia's history, or, for
//! a request the context ledger built, on a thread kept from the last request: continued with a
//! further turn (`stable`), or (`answer`) with the call its open turn waits on answered in
//! place. At the first Sylvia tool call the turn stops; Codex never runs a Sylvia operation.
//!
//! Python's `generate_stream` and `_attempt` are async generators whose `finally` blocks keep
//! or close the thread and write the reuse and lifecycle audits. Here they are [`generator`]
//! bodies: an error or the end runs the same steps; a stream dropped mid-turn (Python's
//! cancellation) stops and closes the child from a guard, which cannot await those audits
//! (differences row 45's rule, for this provider).

use crate::chatgpt::{
    app_server_request, call_tail, continuation_failed, in_place_answer, kept_threads, reported_usage,
    thread_setting, usage_figures, KeptThread, KeptThreadMismatch, PendingCall, ThreadState,
    DRAFT_KEEPING_VALUE, THREAD_MODES, TURN_MOVED_ON, USAGE_REPORT_SECONDS, USAGE_SETTLE_SECONDS,
};
use crate::codex_rpc::{perf_counter_ns, python_resolve, CodexConnection, CodexError};
use crate::config::env::Environment;
use crate::context_capacity::{resolve_context_capacity, CapacityInputs};
use crate::context_mode::take_ledger_request;
use crate::error::{ErrorKind, ProviderError, ProviderResult};
use crate::generator::{generator, Co};
use crate::hooks::{HookContext, HookManager};
use crate::lifecycle::LifecycleTiming;
use crate::provider::{BoxFuture, ChunkStream, Provider, ProviderRequest};
use crate::pyobj::{py_eq, py_str, truthy};
use crate::types::{LiveContext, ModelInfo, ProviderResponse, ProviderStreamChunk, PyValue, ToolCall};
use futures_util::StreamExt;
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};

const PROVIDER_KEY: &str = "chatgpt_subscription";
/// `call(...)`'s default timeout.
const CALL_TIMEOUT: Duration = Duration::from_secs(30);
/// `next_event()`'s default wait.
const EVENT_WAIT: Duration = Duration::from_millis(250);

type Chunks = Co<ProviderResult<ProviderStreamChunk>>;

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

fn text(value: &str) -> Json {
    Json::String(value.to_owned())
}

/// A failure of an attempt: the provider error, and for a kept thread that cannot serve the
/// request, the reason code a fresh thread is started for (`getattr(error, "reason", ...)`).
struct Failure {
    error: ProviderError,
    reason: Option<&'static str>,
}

impl From<ProviderError> for Failure {
    fn from(error: ProviderError) -> Self {
        Self { error, reason: None }
    }
}

impl From<KeptThreadMismatch> for Failure {
    fn from(mismatch: KeptThreadMismatch) -> Self {
        Self { error: mismatch.error(), reason: Some(mismatch.reason) }
    }
}

/// A runtime failure as the attempt meets it: `CodexCancelledError` is a cancellation,
/// `CodexConnectionError` a lost connection, and an `OSError` escapes as it is.
enum Runtime {
    Cancelled,
    Connection(String),
    Io(String),
}

impl From<CodexError> for Runtime {
    fn from(error: CodexError) -> Self {
        match error {
            CodexError::Cancelled(_) => Self::Cancelled,
            CodexError::Connection(message) => Self::Connection(message),
            CodexError::Io(message) => Self::Io(message),
        }
    }
}

enum Fault {
    Provider(Failure),
    Runtime(Runtime),
}

impl From<ProviderError> for Fault {
    fn from(error: ProviderError) -> Self {
        Self::Provider(error.into())
    }
}

impl From<KeptThreadMismatch> for Fault {
    fn from(mismatch: KeptThreadMismatch) -> Self {
        Self::Provider(mismatch.into())
    }
}

impl From<CodexError> for Fault {
    fn from(error: CodexError) -> Self {
        Self::Runtime(error.into())
    }
}

fn cancelled() -> ProviderError {
    ProviderError::new(ErrorKind::Cancelled, "The ChatGPT response was cancelled.")
}

fn local(message: impl Into<String>) -> ProviderError {
    ProviderError::new(ErrorKind::Internal, message)
}

/// `value[key]` of a reply, as Python indexes it (`KeyError` or `TypeError` escaping).
fn index<'a>(value: &'a Json, key: &str) -> Result<&'a Json, Fault> {
    match value {
        Json::Object(map) => map.get(key).ok_or_else(|| local(format!("'{key}'")).into()),
        _ => Err(local(format!("'{}' object is not subscriptable", crate::pyobj::type_name(value))).into()),
    }
}

/// `params.get(key) in {None, value}`.
fn none_or(params: &JsonMap, key: &str, value: &Json) -> bool {
    params.get(key).is_none_or(|found| found.is_null() || py_eq(found, value))
}

/// `_Request`: one generation request, as the runtime will see it, and how it ended.
struct Request {
    model: String,
    allowed: BTreeSet<String>,
    items: Vec<Json>,
    instructions: String,
    dynamic_tools: Vec<Json>,
    timeout: f64,
    options: JsonMap,
    thread_mode: Option<&'static str>,
    exchange_id: Option<String>,
    generation: u64,
    ordinal: u64,
    status: Option<&'static str>,
    keep: Option<Arc<KeptThread>>,
    kept_dropped_draft: bool,
    usage_reports: Vec<JsonMap>,
}

/// The state of the attempt in progress (Python's `_usage_reports`, `_owing`, `_owing_ago`
/// and `_extra_call_seen`).
struct Attempt<'a> {
    usage_reports: &'a mut Vec<JsonMap>,
    /// The kept thread whose waiting call's usage this attempt expects first.
    owing: Option<Arc<KeptThread>>,
    owing_ago: i64,
    extra_call_seen: bool,
}

/// Stops and closes an attempt's child when its stream is dropped mid-turn.
struct ChildGuard(Option<CodexConnection>);

impl Drop for ChildGuard {
    fn drop(&mut self) {
        if let Some(connection) = self.0.take() {
            connection.request_stop();
            match tokio::runtime::Handle::try_current() {
                Ok(handle) => {
                    handle.spawn_blocking(move || connection.close());
                }
                Err(_) => connection.close(),
            }
        }
    }
}

type Clock = Box<dyn Fn() -> String + Send + Sync>;

/// `ChatGPTProvider`: one runtime generation through Sylvia's streaming provider interface.
pub struct ChatGPTProvider {
    home: PathBuf,
    model: ModelInfo,
    live: LiveContext,
    hooks: HookManager,
    executable: Option<PathBuf>,
    lock: tokio::sync::Mutex<()>,
    active: Mutex<Option<CodexConnection>>,
    last_response: Mutex<ProviderResponse>,
    thread_mode: Option<&'static str>,
    stable_threads: bool,
    keeps_dropped_draft: bool,
    clock: Clock,
}

impl ChatGPTProvider {
    /// `ChatGPTProvider(home, model_info, executable=...)`, reading `SYLVIA_CHATGPT_THREAD`
    /// from `environment`. The model's context figures become live: a window the runtime
    /// reports is what every holder of this `ModelInfo` plans within.
    pub fn new(
        home: &Path,
        mut model_info: ModelInfo,
        executable: Option<&Path>,
        environment: &Environment,
    ) -> Self {
        let live = model_info.share_live_context();
        let switch = thread_setting(environment);
        let thread_mode = THREAD_MODES.iter().find(|(name, _)| *name == switch).map(|(_, mode)| *mode);
        Self {
            home: home.to_owned(),
            model: model_info,
            live,
            hooks: HookManager::new(),
            executable: executable.map(Path::to_owned),
            lock: tokio::sync::Mutex::new(()),
            active: Mutex::new(None),
            last_response: Mutex::new(ProviderResponse::default()),
            thread_mode,
            stable_threads: thread_mode.is_some(),
            keeps_dropped_draft: switch == DRAFT_KEEPING_VALUE,
            clock: Box::new(crate::hooks::utc_now_isoformat),
        }
    }

    /// Python's `datetime.now(UTC).isoformat()`, replaced (tests).
    pub fn with_clock(mut self, clock: Clock) -> Self {
        self.clock = clock;
        self
    }

    fn wire(&self) -> &str {
        self.model.wire_model_id()
    }

    fn resolved_home(&self) -> PathBuf {
        python_resolve(&self.home)
    }

    /// `_audit(method, params, received=...)`: the runtime boundary, without claiming the
    /// downstream wire.
    async fn audit(&self, method: &str, params: Json, received: bool) {
        let request_json = object(vec![
            ("direction", text(if received { "receive" } else { "send" })),
            ("method", text(method)),
            ("params", params),
            ("downstream_model_request", text("not_exposed_by_runtime")),
        ]);
        let context = HookContext {
            provider_key: PROVIDER_KEY.into(),
            model_id: self.model.id.clone(),
            stream: true,
            request_json: Some(request_json),
            ..HookContext::default()
        };
        self.hooks.fire_on_request(&context).await;
    }

    /// `_observe_context_window(value)`: the runtime's reported window, without raising a
    /// configured operating cap.
    fn observe_context_window(&self, value: Option<&Json>) {
        let Some(Json::Number(number)) = value else { return };
        let Some(window) = number.as_i64().filter(|window| !number.is_f64() && *window > 0) else { return };
        let model = &self.model;
        let observed_at = (self.clock)();
        let capacity = resolve_context_capacity(&CapacityInputs {
            declared_context_window: model.declared_context_window,
            declared_context_source: Some(&model.declared_context_source),
            runtime_context_window: Some(window),
            runtime_context_source: Some("runtime_reported"),
            configured_context_window: model.configured_context_window,
            legacy_context_window: None,
            observed_at: None,
            declared_observed_at: model.declared_context_observed_at.as_deref(),
            runtime_observed_at: Some(&observed_at),
            configured_observed_at: model.configured_context_observed_at.as_deref(),
        });
        self.live.update(|figures| {
            figures.runtime_context_window = Some(window);
            figures.runtime_context_source = "runtime_reported".into();
            figures.runtime_context_observed_at = Some(observed_at.clone());
            figures.context_observed_at = Some(observed_at.clone());
            figures.context_window = capacity.effective_context_window;
            figures.context_window_source = capacity.effective_context_source.clone();
            figures.configured_runtime_mismatch = capacity.configured_runtime_mismatch;
        });
    }

    /// `_capture_usage(response, params, thread_id, turn_id)`: the latest owned last-request
    /// report; the first after an answer belongs to the answered call's exchange.
    fn capture_usage(
        &self,
        attempt: &mut Attempt<'_>,
        response: &mut ProviderResponse,
        params: &JsonMap,
        thread_id: Option<&Json>,
        turn_id: Option<&Json>,
    ) {
        let (Some(thread_id), Some(turn_id)) =
            (thread_id.filter(|id| truthy(id)), turn_id.filter(|id| truthy(id)))
        else {
            return;
        };
        if !params.get("threadId").is_some_and(|id| py_eq(id, thread_id))
            || !params.get("turnId").is_some_and(|id| py_eq(id, turn_id))
        {
            return;
        }
        let Some(Json::Object(token_usage)) = params.get("tokenUsage") else { return };
        self.observe_context_window(token_usage.get("modelContextWindow"));
        let Some((usage, cache)) = reported_usage(&Json::Object(params.clone())) else { return };
        if let Some(owing) = &attempt.owing {
            let mut state = owing.state.lock().expect("thread state");
            if let Some(pending) = state.pending.as_mut().filter(|pending| pending.usage_owed) {
                let figures = pending.credit(usage, cache);
                let ago = attempt.owing_ago;
                let mut report = object(vec![
                    ("response", text(if ago == 1 { "previous" } else { "earlier" })),
                    ("requests_ago", Json::from(ago)),
                ]);
                report.extend(figures);
                attempt.usage_reports.push(report);
                return;
            }
        }
        let mut own = object(vec![("response", text("own"))]);
        own.extend(usage_figures(&usage, &cache));
        let repeated = attempt
            .usage_reports
            .last()
            .is_some_and(|last| py_eq(&Json::Object(last.clone()), &Json::Object(own.clone())));
        if !repeated {
            attempt.usage_reports.push(own);
        }
        response.usage = usage;
        response.cache_usage = cache;
        response.usage_reported = Some(true);
    }

    /// `_note_extra_call`: a second tool call of the same response; nothing is kept.
    fn note_extra_call(
        attempt: &mut Attempt<'_>,
        method: Option<&Json>,
        params: &JsonMap,
        thread_id: Option<&Json>,
    ) {
        if method.and_then(Json::as_str) == Some("item/tool/call")
            && none_or(params, "threadId", thread_id.unwrap_or(&Json::Null))
        {
            attempt.extra_call_seen = true;
        }
    }

    /// The events after an interrupt, until the turn ends or the report window closes:
    /// `"ended"`, or `None` when the window closed.
    async fn await_turn_events(
        &self,
        connection: &CodexConnection,
        attempt: &mut Attempt<'_>,
        response: &mut ProviderResponse,
        thread_id: &Json,
        turn_id: &Json,
        until_usage: bool,
    ) -> Result<Option<&'static str>, CodexError> {
        let deadline = Instant::now() + Duration::from_secs_f64(USAGE_REPORT_SECONDS);
        while !(until_usage && response.usage_reported == Some(true)) && Instant::now() < deadline {
            let Some(event) = next_event(connection).await? else { continue };
            let method = event.get("method").cloned();
            let Some(Json::Object(params)) = event.get("params").cloned() else { continue };
            match method.as_ref().and_then(Json::as_str) {
                Some("thread/tokenUsage/updated") => {
                    self.audit("thread/tokenUsage/updated", Json::Object(params.clone()), true).await;
                    self.capture_usage(attempt, response, &params, Some(thread_id), Some(turn_id));
                }
                Some("turn/completed") if none_or(&params, "threadId", thread_id) => {
                    self.audit("turn/completed", Json::Object(params), true).await;
                    return Ok(Some("ended"));
                }
                _ => {
                    Self::note_extra_call(attempt, method.as_ref(), &params, Some(thread_id));
                    tokio::task::yield_now().await;
                }
            }
        }
        Ok(None)
    }

    /// `_await_usage_after_tool_call`: collect this request's usage before the runtime is
    /// stopped, by interrupting the turn. Bounded; never fails the request.
    async fn await_usage_after_tool_call(
        &self,
        connection: &CodexConnection,
        attempt: &mut Attempt<'_>,
        response: &mut ProviderResponse,
        thread_id: Option<&Json>,
        turn_id: Option<&Json>,
    ) -> Result<&'static str, Fault> {
        let (Some(thread_id), Some(turn_id)) =
            (thread_id.filter(|id| truthy(id)), turn_id.filter(|id| truthy(id)))
        else {
            return Ok("skipped");
        };
        if response.usage_reported == Some(true) {
            return Ok("skipped");
        }
        tokio::time::sleep(Duration::from_secs_f64(USAGE_SETTLE_SECONDS)).await;
        let identity = serde_json::json!({"threadId": thread_id, "turnId": turn_id});
        self.audit("turn/interrupt", identity.clone(), false).await;
        let outcome = async {
            call(connection, "turn/interrupt", identity).await?;
            self.await_turn_events(connection, attempt, response, thread_id, turn_id, true).await
        }
        .await;
        match outcome {
            Ok(Some(ended)) => Ok(ended),
            Ok(None) => Ok("interrupted"),
            Err(CodexError::Connection(_)) => Ok("failed"),
            Err(error) => Err(error.into()),
        }
    }

    /// `_await_interrupted_turn_end`: wait, bounded, until the interrupted turn ends.
    async fn await_interrupted_turn_end(
        &self,
        connection: &CodexConnection,
        attempt: &mut Attempt<'_>,
        response: &mut ProviderResponse,
        thread_id: &Json,
        turn_id: &Json,
    ) -> Result<bool, Fault> {
        match self.await_turn_events(connection, attempt, response, thread_id, turn_id, false).await {
            Ok(ended) => Ok(ended.is_some()),
            Err(CodexError::Connection(_)) => Ok(false),
            Err(error) => Err(error.into()),
        }
    }

    async fn measured<T>(
        timing: &LifecycleTiming,
        name: &str,
        work: impl std::future::Future<Output = Result<T, Fault>>,
    ) -> Result<T, Fault> {
        let start = perf_counter_ns();
        let outcome = work.await;
        let status = match &outcome {
            Ok(_) => "completed",
            Err(Fault::Runtime(Runtime::Cancelled)) => "cancelled",
            Err(Fault::Provider(failure)) if failure.error.kind == ErrorKind::Cancelled => "cancelled",
            Err(_) => "error",
        };
        timing.record(name, start, perf_counter_ns(), status, None);
        outcome
    }

    /// `_inject`.
    async fn inject(
        &self,
        connection: &CodexConnection,
        thread_id: &Json,
        items: &[Json],
        timing: &LifecycleTiming,
    ) -> Result<(), Fault> {
        let inject = serde_json::json!({"threadId": thread_id, "items": items});
        self.audit("thread/inject_items", inject.clone(), false).await;
        let injected = Self::measured(timing, "history_injection", async {
            Ok(call(connection, "thread/inject_items", inject).await?)
        })
        .await?;
        self.audit("thread/inject_items/result", Json::Object(injected), true).await;
        Ok(())
    }

    /// `_open_thread`: an ephemeral thread with Sylvia's whole history.
    async fn open_thread(
        &self,
        connection: &CodexConnection,
        request: &Request,
        timing: &LifecycleTiming,
    ) -> Result<Json, Fault> {
        let start = serde_json::json!({
            "model": request.model,
            "allowProviderModelFallback": false,
            "ephemeral": true,
            "environments": [],
            "selectedCapabilityRoots": [],
            "baseInstructions": request.instructions,
            "approvalPolicy": "never",
            "dynamicTools": request.dynamic_tools,
        });
        self.audit("thread/start", start.clone(), false).await;
        let thread = Self::measured(timing, "thread_start", async {
            Ok(call(connection, "thread/start", start).await?)
        })
        .await?;
        self.audit("thread/start/result", Json::Object(thread.clone()), true).await;
        if let Some(Json::String(actual)) = thread.get("model") {
            if *actual != request.model {
                return Err(ProviderError::new(
                    ErrorKind::ModelNotFound,
                    "ChatGPT did not select the requested model.",
                )
                .into());
            }
        }
        let thread = Json::Object(thread);
        let thread_id = index(index(&thread, "thread")?, "id")?.clone();
        self.inject(connection, &thread_id, &request.items, timing).await?;
        Ok(thread_id)
    }

    /// `_continue_thread`: only what this request appends, which must start with the model's
    /// own text, call and result.
    async fn continue_thread(
        &self,
        connection: &CodexConnection,
        kept: &KeptThread,
        request: &Request,
        timing: &LifecycleTiming,
    ) -> Result<(), Fault> {
        let (held, call_id, spoken) = {
            let state = kept.state.lock().expect("thread state");
            (state.items.len(), state.call_id.clone(), state.spoken.clone())
        };
        let tail = &request.items[held.min(request.items.len())..];
        call_tail(tail, &call_id, &spoken, false, false)?;
        // Events of the finished turn must not be read as events of the next one.
        connection.notifications()?;
        self.inject(connection, &Json::String(kept.thread_id.clone()), tail, timing).await
    }

    /// `_answer_pending_call`: what this request appends, delivered as the answer to the call
    /// the open turn waits on; user-role messages are steered into the turn first.
    async fn answer_pending_call(
        &self,
        connection: &CodexConnection,
        kept: &KeptThread,
        attempt: &mut Attempt<'_>,
        request: &mut Request,
        timing: &LifecycleTiming,
    ) -> Result<(), Fault> {
        let (held, spoken, pending, effort, tier) = {
            let state = kept.state.lock().expect("thread state");
            let pending = state.pending.clone().expect("a waiting call");
            (state.items.len(), state.spoken.clone(), pending, state.effort.clone(), state.tier.clone())
        };
        let tail: Vec<Json> = request.items[held.min(request.items.len())..].to_vec();
        let (result, texts) = in_place_answer(&tail, &pending.call_id, &spoken, self.keeps_dropped_draft)?;
        request.kept_dropped_draft = !spoken.is_empty()
            && !tail.iter().take(1).any(|item| {
                item.get("type").and_then(Json::as_str) == Some("message")
                    && item.get("role").and_then(Json::as_str) == Some("assistant")
            });
        let option = |key: &str| request.options.get(key).cloned().unwrap_or(Json::Null);
        if !py_eq(&option("reasoning_effort"), &effort) || !py_eq(&option("service_tier"), &tier) {
            // The open turn keeps the effort and tier it was started with.
            return Err(KeptThreadMismatch {
                reason: "effort_or_tier_changed",
                message: "The request asks for another effort or tier.",
                in_place: true,
            }
            .into());
        }
        let thread_id = Json::String(kept.thread_id.clone());
        let turn_id = Json::String(pending.turn_id.clone());
        for event in connection.notifications()? {
            let Some(Json::Object(params)) = event.get("params").cloned() else { continue };
            if !none_or(&params, "threadId", &thread_id) {
                continue;
            }
            let method = event.get("method").and_then(Json::as_str).unwrap_or("");
            if method == "thread/tokenUsage/updated" {
                // The report for the response that made the call, sent while it waited.
                self.audit(method, Json::Object(params.clone()), true).await;
                let owed = kept
                    .state
                    .lock()
                    .expect("thread state")
                    .pending
                    .as_ref()
                    .is_some_and(|pending| pending.usage_owed);
                if owed {
                    let mut scratch = ProviderResponse::default();
                    self.capture_usage(attempt, &mut scratch, &params, Some(&thread_id), Some(&turn_id));
                }
            } else if TURN_MOVED_ON.contains(&method) {
                // The runtime gave up on the call, or went on without its answer.
                return Err(KeptThreadMismatch {
                    reason: "turn_moved_on",
                    message: "The runtime turn went on before its answer.",
                    in_place: true,
                }
                .into());
            }
        }
        Self::measured(timing, "history_injection", async {
            for text_item in &texts {
                // One message each, as a fresh thread would hold them.
                let steer = serde_json::json!({
                    "threadId": thread_id, "expectedTurnId": pending.turn_id,
                    "input": [{"type": "text", "text": text_item}],
                });
                self.audit("turn/steer", steer.clone(), false).await;
                let steered = call(connection, "turn/steer", steer).await?;
                self.audit("turn/steer/result", Json::Object(steered), true).await;
            }
            let answer = object(vec![
                ("contentItems", serde_json::json!([{"type": "inputText", "text": result}])),
                ("success", Json::Bool(true)),
            ]);
            self.audit(
                "item/tool/call/result",
                serde_json::json!({"callId": pending.call_id, "result": answer}),
                false,
            )
            .await;
            let (responder, id) = (connection.clone(), pending.request_id.clone());
            blocking(move || responder.respond(&id, answer)).await?;
            Ok(())
        })
        .await
    }

    /// `_close_kept`: collect idle kept children; a failure to close never fails a request.
    async fn close_kept(threads: Vec<Arc<KeptThread>>) {
        let pool = Arc::clone(kept_threads());
        for kept in threads {
            kept.stop_timer();
            let collecting = Arc::clone(&kept);
            let figures =
                tokio::task::spawn_blocking(move || collecting.collect_owed_usage()).await.ok().flatten();
            if let Some(figures) = figures {
                let ordinal = kept.state.lock().expect("thread state").ordinal;
                pool.abandon(&kept.home, ordinal, figures);
            }
            close_owned(&kept.connection).await;
        }
    }

    /// `_take_kept_thread`.
    async fn take_kept_thread(&self, request: &mut Request) -> Option<Arc<KeptThread>> {
        let pool = kept_threads();
        let home = self.resolved_home();
        request.generation = pool.generation(&home);
        request.ordinal = pool.next_ordinal(&home, true);
        let (kept, stale) =
            pool.take(&home, &request.model, &request.instructions, &request.dynamic_tools, &request.items);
        Self::close_kept(stale).await;
        kept
    }

    /// `_keep_thread`.
    async fn keep_thread(&self, kept: Arc<KeptThread>) -> bool {
        let stale = kept_threads().keep(Arc::clone(&kept));
        let refused = stale.iter().any(|each| Arc::ptr_eq(each, &kept));
        Self::close_kept(stale).await;
        !refused
    }

    async fn stream(&self, provider_request: ProviderRequest, co: &Chunks) -> ProviderResult<()> {
        let marked = take_ledger_request();
        let _held = self.lock.lock().await;
        let timing = Arc::new(LifecycleTiming::new());
        let projected =
            app_server_request(self.wire(), &provider_request.messages, provider_request.tools.as_deref())
                .map_err(|error| match error {
                    crate::chat::ChatError::Provider(error) => error,
                    crate::chat::ChatError::Incidental(incidental) => local(incidental.0),
                })?;
        let mut instructions = Vec::new();
        for instruction in &projected.system_instructions {
            instructions.push(
                instruction
                    .as_str()
                    .ok_or_else(|| local("sequence item 0: expected str instance"))?
                    .to_owned(),
            );
        }
        let instructions = instructions.join("\n\n");
        let allowed: BTreeSet<String> =
            projected.dynamic_tools.iter().filter_map(|tool| tool.get("name").map(py_str)).collect();
        let dynamic_tools = if projected.dynamic_tools.is_empty() {
            Vec::new()
        } else {
            vec![serde_json::json!({
                "type": "namespace", "name": "sylvia", "description": "Authorized Sylvia operations.",
                "tools": projected.dynamic_tools,
            })]
        };
        let item_count = projected.items.len();
        let mut request = Request {
            model: self.wire().to_owned(),
            allowed,
            items: projected.items,
            instructions: if instructions.is_empty() {
                "Follow the supplied Sylvia instructions and history.".into()
            } else {
                instructions
            },
            dynamic_tools,
            timeout: provider_request.timeout,
            options: provider_request.options,
            thread_mode: if marked.is_some() { self.thread_mode } else { None },
            exchange_id: marked,
            generation: 0,
            ordinal: 0,
            status: None,
            keep: None,
            kept_dropped_draft: false,
            usage_reports: Vec::new(),
        };
        let mode = request.thread_mode;
        let mut kept = if mode.is_some() { self.take_kept_thread(&mut request).await } else { None };
        let held_items = kept.as_ref().map_or(0, |kept| kept.state.lock().expect("thread state").items.len());
        let mut reuse = object(vec![
            ("continued", Json::Bool(kept.is_some())),
            ("held_items", Json::from(held_items)),
            ("injected_items", Json::from(item_count as i64 - held_items as i64)),
            ("retried_fresh", Json::Bool(false)),
            ("kept", Json::Bool(false)),
        ]);
        if mode == Some("answer") {
            let waiting =
                kept.as_ref().is_some_and(|kept| kept.state.lock().expect("thread state").pending.is_some());
            reuse.insert("answered_in_place".into(), Json::Bool(waiting));
        }
        let mut outcome: ProviderResult<()> = Ok(());
        loop {
            let mut yielded = false;
            match self.attempt(&mut request, &timing, kept.clone(), co, &mut yielded).await {
                Ok(()) => break,
                Err(failure) => {
                    // Retry once, on a fresh thread, only before the caller saw anything.
                    if kept.is_none() || yielded || !continuation_failed(&failure.error) {
                        outcome = Err(failure.error);
                        break;
                    }
                    kept = None;
                    reuse.insert("continued".into(), Json::Bool(false));
                    reuse.insert("held_items".into(), Json::from(0));
                    reuse.insert("injected_items".into(), Json::from(item_count));
                    reuse.insert("retried_fresh".into(), Json::Bool(true));
                    // Why the kept thread could not serve this request.
                    reuse.insert("fresh_reason".into(), text(failure.reason.unwrap_or("runtime_error")));
                    if reuse.contains_key("answered_in_place") {
                        reuse.insert("answered_in_place".into(), Json::Bool(false));
                    }
                }
            }
        }
        let finished = outcome.is_ok();
        if let Some(held) = request.keep.take() {
            if finished {
                let kept_now = self.keep_thread(held).await;
                reuse.insert("kept".into(), Json::Bool(kept_now));
            } else {
                Self::close_kept(vec![held]).await;
            }
        }
        if let Some(status) = request.status {
            let mut receipt = timing.snapshot(status);
            if mode.is_some() {
                if mode == Some("answer") {
                    // In order: a response whose call this request answered, or whose waiting
                    // turn was closed unanswered; never this response's.
                    let mut reports: Vec<Json> = kept_threads()
                        .take_abandoned(&self.resolved_home(), request.ordinal)
                        .into_iter()
                        .map(Json::Object)
                        .collect();
                    reports.extend(request.usage_reports.iter().cloned().map(Json::Object));
                    reuse.insert("usage_reports".into(), Json::Array(reports));
                }
                if self.keeps_dropped_draft {
                    let answered = reuse.get("answered_in_place").is_some_and(truthy);
                    reuse.insert(
                        "kept_dropped_draft".into(),
                        Json::Bool(answered && request.kept_dropped_draft),
                    );
                }
                self.audit("sylvia/thread_reuse", Json::Object(reuse.clone()), false).await;
                receipt.insert("thread_reuse".into(), Json::Object(reuse));
            }
            self.audit("sylvia/provider_lifecycle", Json::Object(receipt), false).await;
        }
        outcome
    }

    /// `_attempt`: one turn on a fresh runtime thread, or on the kept thread it continues. The
    /// child is closed when the attempt ends, unless the turn ended in a tool call with a
    /// kept-thread mode on; `request.keep` then names the thread for the caller to keep.
    async fn attempt(
        &self,
        request: &mut Request,
        timing: &Arc<LifecycleTiming>,
        kept: Option<Arc<KeptThread>>,
        co: &Chunks,
        yielded: &mut bool,
    ) -> Result<(), Failure> {
        request.status = None;
        let connection = match &kept {
            None => CodexConnection::new(&self.home, self.executable.as_deref(), request.allowed.clone()),
            Some(kept) => kept.connection.clone(),
        };
        let mut guard = ChildGuard(Some(connection.clone()));
        *self.active.lock().expect("active connection") = Some(connection.clone());
        let observer = Arc::clone(timing);
        connection.set_lifecycle_observer(Arc::new(
            move |name: &str, start: u128, end: u128, status: &str| {
                observer.record(name, start, end, status, Some("runtime_start"));
            },
        ));
        let kept_ordinal = kept.as_ref().map(|kept| kept.state.lock().expect("thread state").ordinal);
        let waiting =
            kept.as_ref().is_some_and(|kept| kept.state.lock().expect("thread state").pending.is_some());
        let mut usage_reports = std::mem::take(&mut request.usage_reports);
        let mut attempt = Attempt {
            usage_reports: &mut usage_reports,
            owing: kept.clone().filter(|_| waiting),
            owing_ago: kept_ordinal.map_or(1, |ordinal| request.ordinal as i64 - ordinal as i64),
            extra_call_seen: false,
        };
        let mut status = "error";
        request.status = Some(status);
        let mut stream_start: Option<u128> = None;
        let outcome = self
            .run_turn(
                &connection,
                request,
                timing,
                kept.as_ref(),
                &mut attempt,
                &mut stream_start,
                co,
                yielded,
            )
            .await;
        drop(attempt);
        request.usage_reports = usage_reports;
        let outcome: Result<(), Failure> = match outcome {
            Ok(()) => {
                status = "completed";
                Ok(())
            }
            Err(Fault::Provider(failure)) => {
                if failure.error.kind == ErrorKind::Cancelled {
                    status = "cancelled";
                }
                Err(failure)
            }
            Err(Fault::Runtime(Runtime::Cancelled)) => {
                status = "cancelled";
                Err(cancelled().into())
            }
            Err(Fault::Runtime(Runtime::Connection(message))) => {
                // A stop that lands while a kept thread waits for events can surface as the
                // child having gone: that is a cancellation, not a lost connection to retry.
                if kept.is_some() && connection.check_stopped().is_err() {
                    status = "cancelled";
                    Err(cancelled().into())
                } else {
                    Err(ProviderError::new(ErrorKind::Connection, message).into())
                }
            }
            Err(Fault::Runtime(Runtime::Io(message))) => Err(local(message).into()),
        };
        request.status = Some(status);
        // Closing the owned child cancels unresolved runtime calls and ephemeral history.
        if let Some(start) = stream_start.take() {
            timing.record("turn_stream", start, perf_counter_ns(), status, None);
        }
        if request.keep.is_none() {
            if let Some(kept) = &kept {
                let owed = kept
                    .state
                    .lock()
                    .expect("thread state")
                    .pending
                    .as_ref()
                    .is_some_and(|pending| pending.usage_owed);
                if owed && status != "cancelled" {
                    // The waiting turn will not be answered: its response's report is collected
                    // before the child closes. A stopped request does not wait for it.
                    let collecting = Arc::clone(kept);
                    let figures = tokio::task::spawn_blocking(move || collecting.collect_owed_usage())
                        .await
                        .ok()
                        .flatten();
                    if let Some(figures) = figures {
                        let ordinal = kept.state.lock().expect("thread state").ordinal;
                        kept_threads().abandon(&kept.home, ordinal, figures);
                    }
                }
            }
            stop(&connection, timing).await;
        }
        guard.0 = None;
        *self.active.lock().expect("active connection") = None;
        outcome
    }

    #[allow(clippy::too_many_arguments)]
    async fn run_turn(
        &self,
        connection: &CodexConnection,
        request: &mut Request,
        timing: &Arc<LifecycleTiming>,
        kept: Option<&Arc<KeptThread>>,
        attempt: &mut Attempt<'_>,
        stream_start: &mut Option<u128>,
        co: &Chunks,
        yielded: &mut bool,
    ) -> Result<(), Fault> {
        let kwargs = request.options.clone();
        let mut response = ProviderResponse {
            model: self.wire().to_owned(),
            usage_reported: Some(false),
            ..Default::default()
        };
        let mut text_parts: Vec<String> = Vec::new();
        let mut keep = false;
        let mut open_call: Option<PendingCall> = None;
        if kept.is_none() {
            Self::measured(timing, "runtime_start", async {
                let starting = connection.clone();
                Ok(blocking(move || starting.start()).await?)
            })
            .await?;
        }
        let requested: JsonMap = ["max_tokens", "temperature", "tool_choice"]
            .iter()
            .filter_map(|key| kwargs.get(*key).map(|value| ((*key).to_owned(), value.clone())))
            .collect();
        self.audit(
            "sylvia/runtime_controls",
            serde_json::json!({
                "managed_by_runtime": ["max_tokens", "temperature", "tool_choice"],
                "application_requested": requested,
            }),
            false,
        )
        .await;
        let account = Self::measured(timing, "account_read", async {
            Ok(call(connection, "account/read", serde_json::json!({"refreshToken": false})).await?)
        })
        .await?;
        let signed_in = matches!(account.get("account"), Some(Json::Object(details)) if details.get("type").and_then(Json::as_str) == Some("chatgpt"));
        if !signed_in {
            return Err(ProviderError::new(
                ErrorKind::Authentication,
                "Sign in to ChatGPT in Sylvia Settings to use this model.",
            )
            .into());
        }
        let waiting = kept.and_then(|kept| kept.state.lock().expect("thread state").pending.clone());
        let thread_id: Json = match kept {
            None => self.open_thread(connection, request, timing).await?,
            Some(kept) => {
                if waiting.is_none() {
                    self.continue_thread(connection, kept, request, timing).await?;
                } else {
                    self.answer_pending_call(connection, kept, attempt, request, timing).await?;
                }
                Json::String(kept.thread_id.clone())
            }
        };
        let turn_id: Json = match &waiting {
            // The answered turn goes on; its effort and tier were set when it started.
            Some(pending) => Json::String(pending.turn_id.clone()),
            None => {
                let mut turn = object(vec![
                    ("threadId", thread_id.clone()),
                    ("input", Json::Array(Vec::new())),
                    ("environments", Json::Array(Vec::new())),
                    ("model", text(self.wire())),
                ]);
                if let Some(effort) = kwargs.get("reasoning_effort").filter(|value| !value.is_null()) {
                    turn.insert("effort".into(), effort.clone());
                }
                if let Some(tier) = kwargs.get("service_tier").filter(|value| !value.is_null()) {
                    // A tier id advertised by model/list; absent keeps the runtime default.
                    turn.insert("serviceTier".into(), tier.clone());
                }
                self.audit("turn/start", Json::Object(turn.clone()), false).await;
                let started = Self::measured(timing, "turn_start", async {
                    Ok(call(connection, "turn/start", Json::Object(turn)).await?)
                })
                .await?;
                self.audit("turn/start/result", Json::Object(started.clone()), true).await;
                let started = Json::Object(started);
                index(index(&started, "turn")?, "id")?.clone()
            }
        };
        let mut last_event = Instant::now();
        *stream_start = Some(perf_counter_ns());
        loop {
            let Some(event) = next_event(connection).await? else {
                if last_event.elapsed().as_secs_f64() > request.timeout {
                    return Err(ProviderError::new(
                        ErrorKind::Timeout,
                        "The ChatGPT response stopped making progress.",
                    )
                    .into());
                }
                continue;
            };
            let method = event.get("method").cloned().unwrap_or(Json::Null);
            let params = event.get("params").cloned().unwrap_or_else(|| Json::Object(JsonMap::new()));
            let Json::Object(params) = params else { continue };
            if !none_or(&params, "threadId", &thread_id) || !none_or(&params, "turnId", &turn_id) {
                continue;
            }
            last_event = Instant::now();
            // Account notifications are settings metadata, never campaign audit content.
            if let Json::String(name) = &method {
                if !name.starts_with("account/") && !name.starts_with("remoteControl/") {
                    self.audit(name, Json::Object(params.clone()), true).await;
                }
            }
            connection.check_stopped()?;
            match method.as_str() {
                Some("model/rerouted") => {
                    return Err(ProviderError::new(
                        ErrorKind::ModelNotFound,
                        "ChatGPT routed to a different model; Sylvia stopped the response.",
                    )
                    .into())
                }
                Some("item/started")
                    if params.get("item").and_then(|item| item.get("type")).and_then(Json::as_str) == Some("contextCompaction") =>
                {
                    return Err(ProviderError::new(
                        ErrorKind::ContextCompactionRequired,
                        "ChatGPT requested context compaction. Sylvia must prepare a smaller working context; saved history is retained.",
                    )
                    .into())
                }
                Some("item/agentMessage/delta") => {
                    if let Some(Json::String(delta)) = params.get("delta") {
                        text_parts.push(delta.clone());
                        *yielded = true;
                        co.yield_item(Ok(ProviderStreamChunk::content(delta.clone()))).await;
                    }
                }
                Some("item/reasoning/textDelta" | "item/reasoning/summaryTextDelta") => {
                    if let Some(Json::String(delta)) = params.get("delta") {
                        *yielded = true;
                        co.yield_item(Ok(ProviderStreamChunk { reasoning_content: delta.clone(), ..Default::default() })).await;
                    }
                }
                Some("item/tool/call") => {
                    let (name, call_id) = (params.get("tool").and_then(Json::as_str), params.get("callId").and_then(Json::as_str));
                    let (Some(name), Some(call_id)) = (name, call_id) else {
                        return Err(ProviderError::new(ErrorKind::InvalidResponse, "ChatGPT requested an unavailable Sylvia operation.").into());
                    };
                    if !request.allowed.contains(name) || params.get("namespace").and_then(Json::as_str) != Some("sylvia") {
                        return Err(ProviderError::new(ErrorKind::InvalidResponse, "ChatGPT requested an unavailable Sylvia operation.").into());
                    }
                    let (name, call_id) = (name.to_owned(), call_id.to_owned());
                    let arguments = pyjson::dumps(params.get("arguments").unwrap_or(&Json::Null), Dump::DEFAULT);
                    response.tool_calls = vec![ToolCall::new(call_id.clone(), name.clone(), arguments.clone())];
                    response.finish_reason = PyValue::from_json(text("tool_calls"));
                    let server_request = event.get("id").filter(|id| !id.is_null()).cloned();
                    if let (Some("answer"), Some(request_id)) = (request.thread_mode, server_request) {
                        // Leave the turn waiting on this call: the runtime starts nothing until
                        // it is answered, and reports this response's usage then.
                        let mut pending = PendingCall::new(request_id, &call_id, &py_str(&turn_id));
                        pending.usage_owed = response.usage_reported != Some(true);
                        pending.exchange_id = request.exchange_id.clone();
                        pending.model = request.model.clone();
                        if let Some(exchange_id) = &request.exchange_id {
                            pending.listeners = self.hooks.late_usage_listeners(PROVIDER_KEY, exchange_id);
                        }
                        open_call = Some(pending);
                        keep = true;
                    } else {
                        let interrupt = self
                            .await_usage_after_tool_call(connection, attempt, &mut response, Some(&thread_id), Some(&turn_id))
                            .await?;
                        // A kept thread must have ended its turn; one still awaiting the call's
                        // result is stopped like any other.
                        keep = request.thread_mode.is_some()
                            && (interrupt == "ended"
                                || (interrupt == "interrupted"
                                    && self
                                        .await_interrupted_turn_end(connection, attempt, &mut response, &thread_id, &turn_id)
                                        .await?));
                    }
                    if keep {
                        // Another call of the same response already waiting means the thread
                        // would hold a call the request never will.
                        for queued in connection.notifications()? {
                            if let Some(Json::Object(queued_params)) = queued.get("params") {
                                Self::note_extra_call(attempt, queued.get("method"), queued_params, Some(&thread_id));
                                if queued.get("method").and_then(Json::as_str) == Some("thread/tokenUsage/updated") {
                                    self.capture_usage(attempt, &mut response, queued_params, Some(&thread_id), Some(&turn_id));
                                }
                            }
                        }
                        if let Some(pending) = open_call.as_mut() {
                            pending.usage_owed = response.usage_reported != Some(true);
                        }
                        if attempt.extra_call_seen {
                            keep = false;
                            open_call = None;
                        }
                    }
                    // Stop the runtime before the owner can run this call; a kept runtime has
                    // ended its turn (or waits on the call) and starts nothing until continued.
                    if let Some(start) = stream_start.take() {
                        timing.record("turn_stream", start, perf_counter_ns(), "completed", None);
                    }
                    if !keep {
                        stop(connection, timing).await;
                    }
                    connection.check_stopped()?;
                    *yielded = true;
                    co.yield_item(Ok(ProviderStreamChunk::tool_call(serde_json::json!({
                        "index": 0, "id": call_id, "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }))))
                    .await;
                    break;
                }
                Some("thread/tokenUsage/updated") => {
                    self.capture_usage(attempt, &mut response, &params, Some(&thread_id), Some(&turn_id));
                }
                Some("turn/completed") => {
                    let completed = params.get("turn").cloned().unwrap_or_else(|| Json::Object(JsonMap::new()));
                    let Json::Object(completed) = completed else {
                        return Err(local(format!("'{}' object has no attribute 'get'", crate::pyobj::type_name(&completed))).into());
                    };
                    if completed.get("status").and_then(Json::as_str) != Some("completed") {
                        let error = completed.get("error").filter(|error| truthy(error)).cloned().unwrap_or(Json::Null);
                        let code = error.as_object().and_then(|error| error.get("codexErrorInfo")).and_then(Json::as_str);
                        return Err(match code {
                            Some("serverOverloaded") => ProviderError::new(
                                ErrorKind::ModelCapacity,
                                "The selected model is at capacity. Saved work is kept. Try again later or choose another model for this chat.",
                            ),
                            Some("usageLimitExceeded") => ProviderError::rate_limit(
                                "ChatGPT subscription allowance is unavailable. Try after it resets.",
                                None,
                            ),
                            Some("contextWindowExceeded") => ProviderError::new(
                                ErrorKind::TokenLimit,
                                "ChatGPT rejected this request because its context window was exceeded. Saved conversation and completed work remain.",
                            ),
                            _ => ProviderError::new(ErrorKind::Provider, "ChatGPT did not complete this response. Saved work is retained."),
                        }
                        .into());
                    }
                    break;
                }
                _ => {}
            }
        }
        // Usage received around shutdown is kept without starting another model call; a kept
        // child stays open, and draining clears its queue for the next turn.
        if let Some(start) = stream_start.take() {
            timing.record("turn_stream", start, perf_counter_ns(), "completed", None);
        }
        if !keep {
            stop(connection, timing).await;
        }
        for queued in connection.notifications()? {
            if queued.get("method").and_then(Json::as_str) == Some("thread/tokenUsage/updated") {
                if let Some(Json::Object(queued_params)) = queued.get("params") {
                    self.capture_usage(
                        attempt,
                        &mut response,
                        queued_params,
                        Some(&thread_id),
                        Some(&turn_id),
                    );
                }
            }
        }
        let spoken = text_parts.concat();
        response.content = (!spoken.is_empty()).then(|| spoken.clone());
        let context = HookContext {
            provider_key: PROVIDER_KEY.into(),
            model_id: self.model.id.clone(),
            response: Some(response.clone()),
            stream: true,
            ..HookContext::default()
        };
        self.hooks.fire_on_response(&context).await;
        connection.check_stopped()?;
        *self.last_response.lock().expect("last response") = response.clone();
        request.status = Some("completed");
        if keep {
            if let Json::String(thread) = &thread_id {
                let answered = kept.filter(|_| waiting.is_some());
                let (effort, tier) = match answered {
                    // An answered turn keeps what it was started with.
                    Some(kept) => {
                        let state = kept.state.lock().expect("thread state");
                        (state.effort.clone(), state.tier.clone())
                    }
                    None => (
                        kwargs.get("reasoning_effort").cloned().unwrap_or(Json::Null),
                        kwargs.get("service_tier").cloned().unwrap_or(Json::Null),
                    ),
                };
                request.keep = Some(KeptThread::new(
                    connection.clone(),
                    &self.resolved_home(),
                    &request.model,
                    thread,
                    &request.instructions,
                    request.dynamic_tools.clone(),
                    ThreadState {
                        items: request.items.clone(),
                        generation: request.generation,
                        call_id: py_str(&response.tool_calls[0].id.to_json()),
                        spoken,
                        effort,
                        tier,
                        pending: open_call,
                        ordinal: request.ordinal,
                    },
                ));
            }
        }
        *yielded = true;
        co.yield_item(Ok(ProviderStreamChunk {
            finish_reason: Some(response.finish_reason.clone()),
            ..Default::default()
        }))
        .await;
        Ok(())
    }

    /// `release_idle()`: close this home and model's kept threads; a waiting turn's usage is
    /// collected first and written to the audit now.
    async fn release(&self) {
        if !self.stable_threads {
            return;
        }
        let home = self.resolved_home();
        let pool = kept_threads();
        Self::close_kept(pool.release(Some(&home), Some(self.wire()))).await;
        let reports = pool.take_abandoned(&home, pool.next_ordinal(&home, false));
        if !reports.is_empty() {
            let reports = Json::Array(reports.into_iter().map(Json::Object).collect());
            self.audit("sylvia/thread_reuse_closed", serde_json::json!({"usage_reports": reports}), false)
                .await;
        }
    }
}

async fn blocking<T: Send + 'static>(
    work: impl FnOnce() -> Result<T, CodexError> + Send + 'static,
) -> Result<T, CodexError> {
    tokio::task::spawn_blocking(work)
        .await
        .unwrap_or_else(|error| Err(CodexError::Connection(error.to_string())))
}

async fn call(
    connection: &CodexConnection,
    method: &'static str,
    params: Json,
) -> Result<JsonMap, CodexError> {
    let connection = connection.clone();
    blocking(move || connection.call(method, params, CALL_TIMEOUT)).await
}

async fn next_event(connection: &CodexConnection) -> Result<Option<JsonMap>, CodexError> {
    let connection = connection.clone();
    blocking(move || connection.next_event(EVENT_WAIT)).await
}

/// `_close_owned`: collect the child.
async fn close_owned(connection: &CodexConnection) {
    let connection = connection.clone();
    let _ = tokio::task::spawn_blocking(move || connection.close()).await;
}

/// `stop()`: the shutdown span, then the child collected.
async fn stop(connection: &CodexConnection, timing: &LifecycleTiming) {
    let start = perf_counter_ns();
    close_owned(connection).await;
    timing.record("shutdown", start, perf_counter_ns(), "completed", None);
}

impl Provider for ChatGPTProvider {
    fn model_info(&self) -> Option<&ModelInfo> {
        Some(&self.model)
    }

    fn hooks(&self) -> Option<&HookManager> {
        Some(&self.hooks)
    }

    fn generate(&self, request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
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
                active.request_stop();
                close_owned(&active).await;
            }
            self.release().await;
        })
    }

    fn release_idle(&self) -> BoxFuture<'_, ()> {
        Box::pin(self.release())
    }
}
