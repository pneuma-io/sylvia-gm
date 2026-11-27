//! Replaying a traced Python phase or turn: its events checked as the session loop takes them,
//! its callbacks and provider streams answered from the recording, and interned values expanded.

use super::{action_command_json, model, opt, session_context, session_record, tool};
use serde_json::{json, Value as J};
use sylvia_base::testing::assert_same_json;
use std::sync::{Arc, Mutex};
use sylvia_agent::campaign_memory::KnowledgeReference;
use sylvia_agent::provider_turn::{TurnEvents, TurnResult};
use sylvia_agent::session_loop::{ProviderControl, ProviderEvent, SessionProviderInput};
use sylvia_agent::stateless_mcp::AuthorityFailure;
use sylvia_base::{CodedError, CodedResult, JsonMap};
use sylvia_domain::authority::{ActionReceipt, ActionStatus};
use sylvia_domain::error::{DomainError, MechanicsDetails, MechanicsValidationIssue};
use sylvia_provider::types::{ModelInfo, ProviderResponse, ProviderStreamChunk, PyValue};
use sylvia_provider::{BoxFuture, ChunkStream, Provider, ProviderRequest, ProviderResult};

/// `{"__shared__": key}` written back as the shared value, recursively.
pub fn expand(value: &J, shared: &J) -> J {
    match value {
        J::Object(map) => match map.get("__shared__") {
            Some(J::String(key)) if map.len() == 1 => expand(&shared[key.as_str()], shared),
            _ => J::Object(map.iter().map(|(key, item)| (key.clone(), expand(item, shared))).collect()),
        },
        J::Array(items) => J::Array(items.iter().map(|item| expand(item, shared)).collect()),
        other => other.clone(),
    }
}

/// A vector file's cases, their interned values expanded.
pub fn cases(text: &str) -> Vec<J> {
    let vectors: J = serde_json::from_str(text).expect("vector file is valid JSON");
    let cases = expand(&vectors["cases"], &vectors["shared"]);
    let cases = cases.as_array().expect("cases").clone();
    assert!(!cases.is_empty());
    cases
}

/// `plain(event)` for a `ProviderEvent`.
pub fn event_json(event: &ProviderEvent) -> J {
    let mut value = json!({
        "__class__": "ProviderEvent", "kind": event.kind().as_str(), "text": event.text(),
        "action_command": null, "evidence": null, "message_id": null, "action_feedback": null,
        "response_blocks": null, "response_id": event.response_id(),
    });
    match event {
        ProviderEvent::Evidence { message_id, payload } => {
            value["evidence"] = J::Object(payload.clone());
            value["message_id"] = J::from(message_id.as_str());
        }
        ProviderEvent::Action { command, feedback } => {
            value["action_command"] = action_command_json(command);
            if let Some(feedback) = feedback {
                value["action_feedback"] =
                    json!({"__class__": "ProviderActionFeedback", "rejection": feedback.rejection()});
            }
        }
        ProviderEvent::Talk { response_blocks: Some(blocks), .. } => {
            value["response_blocks"] = J::Array(blocks.iter().cloned().map(J::Object).collect());
        }
        _ => {}
    }
    value
}

/// The session loop's side: each event checked against Python's, and an action answered with
/// the rejection Python's loop wrote back.
pub struct Loop {
    pub expected: Vec<J>,
    pub answers: Vec<J>,
}

impl Loop {
    /// The recorded events (`phase_event`) and the loop's answers to actions (`action_handled`).
    pub fn new(events: &[J]) -> Self {
        let rows = |name: &str, key: &str| -> Vec<J> {
            events.iter().filter(|event| event["event"] == name).map(|event| event[key].clone()).collect()
        };
        Self { expected: rows("phase_event", "value"), answers: rows("action_handled", "rejection") }
    }
}

impl TurnEvents for Loop {
    fn emit(&mut self, event: ProviderEvent) -> BoxFuture<'_, TurnResult<()>> {
        assert!(!self.expected.is_empty(), "Rust emitted an extra event: {}", event_json(&event));
        let expected = self.expected.remove(0);
        assert_eq!(
            serde_json::to_string(&event_json(&event)).unwrap(),
            serde_json::to_string(&expected).unwrap(),
            "the event differs"
        );
        if let ProviderEvent::Action { feedback: Some(feedback), .. } = &event {
            // A variant's events never reach Python's loop, so nothing answers its actions.
            if !self.answers.is_empty() {
                let answer = self.answers.remove(0);
                feedback.set_rejection(answer.as_object().cloned());
            }
        }
        Box::pin(async { Ok(()) })
    }
}

/// The recorded callback rows of one name, answered in order.
pub fn callback_rows(events: &[J], name: &str) -> Arc<Mutex<Vec<J>>> {
    Arc::new(Mutex::new(
        events
            .iter()
            .filter(|event| event["event"] == "callback" && event["name"] == name)
            .cloned()
            .collect(),
    ))
}

pub fn next_row(rows: &Mutex<Vec<J>>, name: &str, arguments: J) -> J {
    let mut rows = rows.lock().unwrap();
    assert!(!rows.is_empty(), "Rust called {name} more often than Python");
    let row = rows.remove(0);
    assert_same_json(&arguments, &row["arguments"], format!("{name}'s arguments"));
    row
}

fn text_list(value: &J) -> Vec<String> {
    value
        .as_array()
        .map(|items| items.iter().filter_map(J::as_str).map(str::to_owned).collect())
        .unwrap_or_default()
}

/// The narrative writer's recorded refusal, a `MechanicsRejected` kept with its fields.
pub fn writer_failure(row: &J) -> AuthorityFailure {
    let raised = &row["raised"];
    let Some(issues) = raised.get("validation_issues").and_then(J::as_array) else {
        return AuthorityFailure::Coded(self::raised(row));
    };
    let issue = |payload: &J| MechanicsValidationIssue {
        path: payload["path"].as_str().unwrap().to_owned(),
        kind: payload["kind"].as_str().unwrap().to_owned(),
        expected_type: payload["expected_type"].as_str().map(str::to_owned),
        actual_type: payload["actual_type"].as_str().map(str::to_owned),
        parent_path: payload["parent_path"].as_str().map(str::to_owned),
        allowed_values: text_list(&payload["allowed_values"]),
        minimum: payload["minimum"].as_number().cloned(),
        maximum: payload["maximum"].as_number().cloned(),
        actual_length: payload["actual_length"].as_i64(),
    };
    let details = MechanicsDetails {
        field_paths: text_list(&raised["field_paths"]),
        validation_issues: issues.iter().map(issue).collect(),
        state_changed: raised["state_changed"].as_bool().unwrap_or(false),
        refusal_reasons: text_list(&raised["refusal_reasons"]),
    };
    assert_eq!(raised["class"], "MechanicsRejected", "the writer's refusal class");
    AuthorityFailure::Domain(
        DomainError::mechanics_rejected(
            raised["message"].as_str().unwrap(),
            raised["code"].as_str().unwrap(),
            details,
        )
        .expect("a valid recorded refusal"),
    )
}

pub fn raised(row: &J) -> CodedError {
    let raised = &row["raised"];
    CodedError::new(
        raised["code"].as_str().unwrap_or("internal_error"),
        raised["message"].as_str().unwrap_or(""),
    )
    .with_error_type("CommandRejected")
}

/// The provider input's callbacks, each answered from its recorded rows.
pub struct Callbacks {
    rows: [(Arc<Mutex<Vec<J>>>, &'static str); 3],
}

impl Callbacks {
    /// Give `input` the narrative writer, knowledge searcher and reference reader Python's had.
    pub fn install(events: &[J], input: &mut SessionProviderInput) -> Self {
        let writer = callback_rows(events, "narrative_writer");
        let rows = Arc::clone(&writer);
        input.narrative_writer = Some(Arc::new(move |parameters: JsonMap| {
            let row = next_row(&rows, "narrative_writer", json!([parameters]));
            Box::pin(async move {
                if row.get("raised").is_some() {
                    return Err(writer_failure(&row));
                }
                Ok(row["result"].as_object().cloned().unwrap())
            })
        }));
        let search = callback_rows(events, "knowledge_searcher");
        let rows = Arc::clone(&search);
        input.knowledge_searcher = Some(Arc::new(move |query, sources, limit, offset| {
            let row = next_row(&rows, "knowledge_searcher", json!([query, sources, limit, offset]));
            Box::pin(async move {
                if row.get("raised").is_some() {
                    return Err(raised(&row));
                }
                Ok(row["result"].as_object().cloned().unwrap())
            })
        }));
        let read = callback_rows(events, "reference_reader");
        let rows = Arc::clone(&read);
        input.reference_reader = Some(Arc::new(move |source, path| {
            let row = next_row(&rows, "reference_reader", json!([source, path]));
            Box::pin(async move {
                if row.get("raised").is_some() {
                    return Err(raised(&row));
                }
                Ok(row["result"].as_object().cloned().unwrap())
            })
        }));
        Self { rows: [(writer, "narrative_writer"), (search, "knowledge_searcher"), (read, "reference_reader")] }
    }

    /// Every recorded callback was made.
    pub fn check(&self, name: &str) {
        for (rows, callback) in &self.rows {
            assert!(rows.lock().unwrap().is_empty(), "{name}: Rust called {callback} less often than Python");
        }
    }
}

/// Give `input` a catalog supplier answering each request with Python's recorded catalog.
pub fn install_catalogs(events: &[J], input: &mut SessionProviderInput) {
    let catalogs: Arc<Mutex<Vec<J>>> =
        Arc::new(Mutex::new(events.iter().filter(|event| event["event"] == "catalog").cloned().collect()));
    input.catalog_supplier = Some(Arc::new(move |request_id: String| {
        let next = {
            let mut catalogs = catalogs.lock().unwrap();
            assert!(!catalogs.is_empty(), "Rust asked for more catalogs than Python");
            catalogs.remove(0)
        };
        assert_eq!(next["request_id"].as_str(), Some(request_id.as_str()), "the catalog request id");
        let tools = next["result"].as_array().unwrap().iter().map(tool).collect();
        Box::pin(async move { Ok(tools) })
    }));
}

/// `plain(ActionReceipt)` read back.
pub fn action_receipt(value: &J) -> ActionReceipt {
    ActionReceipt {
        campaign_id: value["campaign_id"].as_str().unwrap().to_owned(),
        action_id: value["action_id"].as_str().unwrap().to_owned(),
        status: ActionStatus::parse(value["status"].as_str().unwrap()).unwrap(),
        result: super::map(&value["result"]),
        resulting_revision: value["resulting_revision"].as_i64().unwrap(),
    }
}

/// The provider input a trace's `dump_input` gives, without its callbacks.
pub fn provider_input(input: &J) -> SessionProviderInput {
    let control = Arc::new(ProviderControl::new());
    if input["cancelled"].as_bool() == Some(true) {
        control.cancel().unwrap();
    }
    let catalog = input["catalog"].as_array().map(|tools| tools.iter().map(tool).collect()).unwrap_or_default();
    let mut provider_input =
        SessionProviderInput::new(session_record(&input["session"]), session_context(&input["history"]), catalog, control);
    provider_input.basis_message_id = opt(&input["basis_message_id"]);
    provider_input.recovery_kind = opt(&input["recovery_kind"]);
    provider_input.retry_action_receipt =
        (!input["retry_action_receipt"].is_null()).then(|| action_receipt(&input["retry_action_receipt"]));
    provider_input
}

fn chunk(value: &J) -> ProviderStreamChunk {
    let text = |key: &str| value[key].as_str().unwrap_or("").to_owned();
    let opt = |key: &str| value[key].as_str().map(str::to_owned);
    ProviderStreamChunk {
        content: text("content"),
        reasoning_content: text("reasoning_content"),
        tool_call_delta: (!value["tool_call_delta"].is_null()).then(|| value["tool_call_delta"].clone()),
        finish_reason: (!value["finish_reason"].is_null()).then(|| PyValue::from_json(value["finish_reason"].clone())),
        anthropic_content: value["anthropic_content"].as_array().cloned(),
        chat_completion_metadata: value["chat_completion_metadata"].as_object().cloned(),
        provider_reported_reasoning_effort: opt("provider_reported_reasoning_effort"),
        provider_reported_service_tier: opt("provider_reported_service_tier"),
    }
}

/// The recorded streams: each request's chunks, in order.
fn streams(events: &[J]) -> Vec<Vec<J>> {
    let mut streams: Vec<Vec<J>> = Vec::new();
    for event in events {
        match event["event"].as_str() {
            Some("provider_stream") => streams.push(Vec::new()),
            Some("provider_chunk") => streams.last_mut().unwrap().push(event["chunk"].clone()),
            Some("provider_chunk_error") => panic!("no recorded stream fails"),
            _ => {}
        }
    }
    streams
}

/// The GM provider: answers each stream with the next recorded one and records the request.
pub struct Scripted {
    model: ModelInfo,
    streams: Mutex<Vec<Vec<J>>>,
    requests: Mutex<Vec<J>>,
}

impl Scripted {
    pub fn new(model_info: &J, events: &[J]) -> Self {
        Self { model: model(model_info), streams: Mutex::new(streams(events)), requests: Mutex::default() }
    }

    /// Rust sent Python's requests (messages, tools, timeout, options), all of them.
    pub fn check(&self, name: &str, events: &[J]) {
        let sent: Vec<J> = events
            .iter()
            .filter(|event| event["event"] == "provider_stream")
            .map(|event| {
                json!({"messages": event["messages"], "tools": event["tools"], "timeout": event["timeout"], "options": event["options"]})
            })
            .collect();
        assert_eq!(
            super::text(&J::Array(self.requests.lock().unwrap().clone())),
            super::text(&J::Array(sent)),
            "{name}: the requests"
        );
    }
}

impl Provider for Scripted {
    fn model_info(&self) -> Option<&ModelInfo> {
        Some(&self.model)
    }
    fn generate(&self, _request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        unreachable!("no recorded round compacts")
    }
    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        self.requests.lock().unwrap().push(json!({
            "messages": request.messages, "tools": request.tools, "timeout": request.timeout, "options": request.options,
        }));
        let chunks = {
            let mut streams = self.streams.lock().unwrap();
            assert!(!streams.is_empty(), "Rust sent more requests than Python");
            streams.remove(0)
        };
        Box::pin(futures_util::stream::iter(chunks.into_iter().map(|value| Ok(chunk(&value)))))
    }
    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }
    fn set_audit_context(&self, _context: &JsonMap) -> bool {
        true
    }
}

/// Lane 3's `rank_references`, as the owner gives the request assembly (`sylvia-owner`'s
/// preflight), for a recording whose campaign has knowledge facts.
pub fn rank(query: &str, references: &[KnowledgeReference]) -> CodedResult<Vec<JsonMap>> {
    let candidates: Vec<sylvia_app::knowledge_search::KnowledgeReference> = references
        .iter()
        .map(|reference| sylvia_app::knowledge_search::KnowledgeReference {
            result: reference.result.clone(),
            identity: reference.identity.clone(),
            body: reference.body.clone(),
        })
        .collect();
    sylvia_app::knowledge_search::rank_references(query, &candidates)
        .map_err(|error| CodedError::new("internal_error", error.to_string()))
}

/// A rank function for a campaign without knowledge facts.
pub fn no_facts(_query: &str, references: &[KnowledgeReference]) -> CodedResult<Vec<JsonMap>> {
    assert!(references.is_empty(), "the recorded campaign has no facts");
    Ok(Vec::new())
}
