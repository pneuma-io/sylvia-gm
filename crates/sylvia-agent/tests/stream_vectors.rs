//! Compare one provider round's request and stream (T2c) with traced Python runs.
//!
//! Regenerate with `tests/vectors/generate_round_vectors.py --phases stream_provider_round
//! --output stream_vectors.json`. Each case is one real call of `stream_provider_round` from
//! the `ruleless-turn` session, or one of its variants. Rust starts from the same turn locals
//! and input, is answered every owner call from the recording and draws the same `uuid4`
//! values; the GM provider below answers with Python's recorded chunks and records what it is
//! sent. Rust must send Python's request (messages, tools, timeout, options), emit Python's
//! events, and leave Python's turn locals (or raise its error).

mod common;

use common::turn_state;
use common::{model, session_context, session_record, text, tool, Recorded};
use serde_json::Value as J;
use std::sync::{Arc, Mutex};
use sylvia_agent::campaign_memory::KnowledgeReference;
use sylvia_agent::provider_session::context::ProviderSessionAdapter;
use sylvia_agent::provider_session::reads::{OpeningReads, TurnOwner};
use sylvia_agent::provider_turn::round::stream_provider_round;
use sylvia_agent::provider_turn::{TurnEvents, TurnFailure, TurnResult};
use sylvia_agent::session_loop::{ProviderControl, ProviderEvent, SessionProviderInput};
use sylvia_base::{CodedResult, JsonMap};
use sylvia_provider::config::env::Environment;
use sylvia_provider::types::{ModelInfo, ProviderResponse, ProviderStreamChunk, PyValue};
use sylvia_provider::{BoxFuture, ChunkStream, Provider, ProviderRequest, ProviderResult};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/stream_vectors.json")).expect("vector file is valid JSON")
}

fn chunk(value: &J) -> ProviderStreamChunk {
    let text = |key: &str| value[key].as_str().unwrap_or("").to_owned();
    let opt = |key: &str| value[key].as_str().map(str::to_owned);
    ProviderStreamChunk {
        content: text("content"),
        reasoning_content: text("reasoning_content"),
        tool_call_delta: (!value["tool_call_delta"].is_null()).then(|| value["tool_call_delta"].clone()),
        finish_reason: (!value["finish_reason"].is_null())
            .then(|| PyValue::from_json(value["finish_reason"].clone())),
        anthropic_content: value["anthropic_content"].as_array().cloned(),
        chat_completion_metadata: value["chat_completion_metadata"].as_object().cloned(),
        provider_reported_reasoning_effort: opt("provider_reported_reasoning_effort"),
        provider_reported_service_tier: opt("provider_reported_service_tier"),
    }
}

/// The GM provider: answers each stream with the next recorded one and records the request.
struct Scripted {
    model: ModelInfo,
    streams: Mutex<Vec<Vec<J>>>,
    requests: Mutex<Vec<J>>,
}

impl Provider for Scripted {
    fn model_info(&self) -> Option<&ModelInfo> {
        Some(&self.model)
    }
    fn generate(&self, _request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        unreachable!("no recorded round compacts")
    }
    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        self.requests.lock().unwrap().push(serde_json::json!({
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

/// `plain(ProviderEvent)`.
fn event_json(event: &ProviderEvent) -> J {
    let mut value = serde_json::json!({
        "__class__": "ProviderEvent", "kind": event.kind().as_str(), "text": event.text(),
        "action_command": null, "evidence": null, "message_id": null, "action_feedback": null,
        "response_blocks": null, "response_id": event.response_id(),
    });
    match event {
        ProviderEvent::Evidence { message_id, payload } => {
            value["evidence"] = J::Object(payload.clone());
            value["message_id"] = J::from(message_id.as_str());
        }
        ProviderEvent::Talk { response_blocks: Some(blocks), .. } => {
            value["response_blocks"] = J::Array(blocks.iter().cloned().map(J::Object).collect());
        }
        _ => {}
    }
    value
}

struct Collected(Vec<J>);

impl TurnEvents for Collected {
    fn emit(&mut self, event: ProviderEvent) -> BoxFuture<'_, TurnResult<()>> {
        self.0.push(event_json(&event));
        Box::pin(async { Ok(()) })
    }
}

fn no_facts(_query: &str, references: &[KnowledgeReference]) -> CodedResult<Vec<JsonMap>> {
    assert!(references.is_empty(), "the recorded campaign has no facts");
    Ok(Vec::new())
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

#[tokio::test]
async fn stream_round_matches_python() {
    for case in vectors()["cases"].as_array().unwrap() {
        let name = format!("stream_provider_round ({})", case["variant"].as_str().unwrap_or("recorded"));
        let events = case["events"].as_array().unwrap();
        let owner = Arc::new(Recorded::new(events));
        let ids: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(
            events
                .iter()
                .filter(|event| event["event"] == "uuid")
                .map(|event| event["hex"].as_str().unwrap().to_owned())
                .collect(),
        ));
        let provider = Arc::new(Scripted {
            model: model(&case["model"]),
            streams: Mutex::new(streams(events)),
            requests: Mutex::default(),
        });
        let args = &case["adapter"];
        let draw = Arc::clone(&ids);
        let adapter = Arc::new(
            ProviderSessionAdapter::new(
                Arc::clone(&owner) as Arc<dyn OpeningReads>,
                Arc::clone(&provider) as Arc<dyn Provider>,
                args["owner_id"].as_str().unwrap(),
                common::opt(&args["gm_system_prompt"]),
                120.0,
                common::opt(&args["campaign_plan_text"]),
                args["campaign_plan"].as_object().cloned(),
                args["campaign_knowledge"].as_object().cloned(),
                &Environment::default(),
            )
            .expect("the recorded adapter inputs are valid")
            .with_turn_owner(Arc::clone(&owner) as Arc<dyn TurnOwner>, Arc::new(no_facts))
            .with_ids(Arc::new(move || {
                let mut ids = draw.lock().unwrap();
                assert!(!ids.is_empty(), "Rust drew more uuid4 values than Python");
                ids.remove(0)
            })),
        );
        let input = &case["input"];
        let control = Arc::new(ProviderControl::new());
        if input["cancelled"].as_bool().unwrap() {
            control.cancel().unwrap();
        }
        let mut provider_input = SessionProviderInput::new(
            session_record(&input["session"]),
            session_context(&input["history"]),
            input["catalog"].as_array().unwrap().iter().map(tool).collect(),
            control,
        );
        provider_input.basis_message_id = common::opt(&input["basis_message_id"]);
        provider_input.recovery_kind = common::opt(&input["recovery_kind"]);
        if input["has_reference_reader"].as_bool().unwrap() {
            provider_input.reference_reader =
                Some(Arc::new(|source, path| panic!("the round read a reference: {source} {path}")));
        }
        let mut state = turn_state::load(&case["before"]);
        let mut collected = Collected(Vec::new());
        let outcome = stream_provider_round(
            &adapter,
            &mut state,
            &provider_input,
            case["turn_id"].as_str().unwrap(),
            &mut collected,
        )
        .await;
        match (outcome, case.get("raised")) {
            (Ok(()), None) => {}
            (Err(TurnFailure::Coded(error)), Some(raised)) => {
                assert_eq!(J::from(error.message.as_str()), raised["message"], "{name}: the error");
                assert_eq!(J::from(error.code.as_str()), raised["code"], "{name}: the error code");
            }
            (outcome, raised) => panic!("{name}: got {outcome:?}, Python raised {raised:?}"),
        }
        owner.check(&name);
        assert!(ids.lock().unwrap().is_empty(), "{name}: Rust drew fewer uuid4 values than Python");
        let sent: Vec<J> = events
            .iter()
            .filter(|event| event["event"] == "provider_stream")
            .map(|event| {
                serde_json::json!({"messages": event["messages"], "tools": event["tools"], "timeout": event["timeout"], "options": event["options"]})
            })
            .collect();
        assert_eq!(
            text(&J::Array(provider.requests.lock().unwrap().clone())),
            text(&J::Array(sent)),
            "{name}: the request"
        );
        let emitted: Vec<J> = events
            .iter()
            .filter(|event| event["event"] == "phase_event")
            .map(|event| event["value"].clone())
            .collect();
        assert_eq!(text(&J::Array(collected.0)), text(&J::Array(emitted)), "{name}: the events");
        turn_state::compare(
            &name,
            &turn_state::dump(&state),
            &turn_state::expected_after(&case["before"], &case["after"]),
        );
    }
}
