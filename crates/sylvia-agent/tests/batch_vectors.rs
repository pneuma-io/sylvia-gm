//! Compare one round's tool batch (T3b-1) with Python, on real turns.
//!
//! Regenerate with `tests/vectors/generate_batch_vectors.py`: `prepare_tool_batch` and
//! `execute_tool_batch` traced in the `ruleless-turn` and `srd-mechanics-turn` turns. Rust is
//! answered every owner call, `uuid4` value and callback (narrative writer, knowledge searcher,
//! reference reader) Python's phase met, in order; its events must be Python's, and an action's
//! rejection is the one Python's session loop wrote back.

mod common;

use common::{session_context, session_record, turn_state, Recorded};
use serde_json::{json, Value as J};
use std::sync::{Arc, Mutex};
use sylvia_agent::provider_session::context::ProviderSessionAdapter;
use sylvia_agent::provider_session::reads::{OpeningReads, TurnOwner};
use sylvia_agent::provider_turn::tool_batch::{execute_tool_batch, prepare_tool_batch};
use sylvia_agent::provider_turn::{TurnEvents, TurnResult};
use sylvia_agent::session_loop::{ProviderControl, ProviderEvent, SessionProviderInput};
use sylvia_agent::stateless_mcp::AuthorityFailure;
use sylvia_base::{CodedError, JsonMap};
use sylvia_domain::authority::ActionCommand;
use sylvia_domain::error::{DomainError, MechanicsDetails, MechanicsValidationIssue};
use sylvia_provider::config::env::Environment;
use sylvia_provider::types::{ModelInfo, ProviderResponse};
use sylvia_provider::{BoxFuture, ChunkStream, Provider, ProviderRequest, ProviderResult};

/// A provider the batch never calls.
struct Unused;

impl Provider for Unused {
    fn model_info(&self) -> Option<&ModelInfo> {
        None
    }
    fn generate(&self, _request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        unreachable!()
    }
    fn generate_stream(&self, _request: ProviderRequest) -> ChunkStream<'_> {
        unreachable!()
    }
    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }
}

fn command_json(command: &ActionCommand) -> J {
    json!({
        "__class__": "ActionCommand", "campaign_id": command.campaign_id, "action_id": command.action_id,
        "idempotency_key": command.idempotency_key, "owner_id": command.owner_id,
        "writer": command.writer.as_str(), "expected_revision": command.expected_revision,
        "turn_id": command.turn_id, "package_id": command.package_id, "tool_name": command.tool_name,
        "parameters": command.parameters, "initiating_session_id": command.initiating_session_id,
    })
}

/// `plain(event)` for the events a batch emits.
fn event_json(event: &ProviderEvent) -> J {
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
            value["action_command"] = command_json(command);
            if let Some(feedback) = feedback {
                value["action_feedback"] =
                    json!({"__class__": "ProviderActionFeedback", "rejection": feedback.rejection()});
            }
        }
        _ => {}
    }
    value
}

/// The session loop's side: each event checked against Python's, and an action answered with
/// the rejection Python's loop wrote back.
struct Loop {
    expected: Vec<J>,
    answers: Vec<J>,
}

impl TurnEvents for Loop {
    fn emit(&mut self, event: ProviderEvent) -> BoxFuture<'_, TurnResult<()>> {
        assert!(!self.expected.is_empty(), "Rust emitted an extra event");
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
fn answers(events: &[J], name: &str) -> Arc<Mutex<Vec<J>>> {
    Arc::new(Mutex::new(
        events
            .iter()
            .filter(|event| event["event"] == "callback" && event["name"] == name)
            .cloned()
            .collect(),
    ))
}

fn next_row(rows: &Mutex<Vec<J>>, name: &str, arguments: J) -> J {
    let mut rows = rows.lock().unwrap();
    assert!(!rows.is_empty(), "Rust called {name} more often than Python");
    let row = rows.remove(0);
    assert_eq!(row["arguments"], arguments, "{name}'s arguments");
    row
}

fn text_list(value: &J) -> Vec<String> {
    value
        .as_array()
        .map(|items| items.iter().filter_map(J::as_str).map(str::to_owned).collect())
        .unwrap_or_default()
}

/// The narrative writer's recorded refusal, a `MechanicsRejected` kept with its fields.
fn writer_failure(row: &J) -> AuthorityFailure {
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

fn raised(row: &J) -> CodedError {
    let raised = &row["raised"];
    CodedError::new(
        raised["code"].as_str().unwrap_or("internal_error"),
        raised["message"].as_str().unwrap_or(""),
    )
    .with_error_type("CommandRejected")
}

/// `{"__shared__": key}` written back as the shared value, recursively.
fn expand(value: &J, shared: &J) -> J {
    match value {
        J::Object(map) => match map.get("__shared__") {
            Some(J::String(key)) if map.len() == 1 => expand(&shared[key.as_str()], shared),
            _ => J::Object(map.iter().map(|(key, item)| (key.clone(), expand(item, shared))).collect()),
        },
        J::Array(items) => J::Array(items.iter().map(|item| expand(item, shared)).collect()),
        other => other.clone(),
    }
}

#[tokio::test]
async fn tool_batches_match_python() {
    let vectors: J =
        serde_json::from_str(include_str!("vectors/batch_vectors.json")).expect("vector file is valid JSON");
    let cases = expand(&vectors["cases"], &vectors["shared"]);
    let cases = cases.as_array().expect("cases");
    assert!(!cases.is_empty());
    for (index, case) in cases.iter().enumerate() {
        let phase = case["phase"].as_str().unwrap();
        let name = format!("{index} {phase}");
        let events = case["events"].as_array().unwrap();
        let owner = Arc::new(Recorded::new(events));
        let ids: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(
            events
                .iter()
                .filter(|event| event["event"] == "uuid")
                .map(|e| e["hex"].as_str().unwrap().to_owned())
                .collect(),
        ));
        let draw = Arc::clone(&ids);
        let adapter = ProviderSessionAdapter::new(
            Arc::clone(&owner) as Arc<dyn OpeningReads>,
            Arc::new(Unused),
            case["adapter"]["owner_id"].as_str().unwrap(),
            None,
            120.0,
            None,
            None,
            None,
            &Environment::default(),
        )
        .expect("an adapter")
        .with_turn_owner(Arc::clone(&owner) as Arc<dyn TurnOwner>, Arc::new(|_, _| Ok(Vec::new())))
        .with_record_warning(Arc::new(|_: &str| {}))
        .with_ids(Arc::new(move || {
            let mut ids = draw.lock().unwrap();
            assert!(!ids.is_empty(), "Rust drew more uuid4 values than Python");
            ids.remove(0)
        }));
        let input = &case["input"];
        let mut provider_input = SessionProviderInput::new(
            session_record(&input["session"]),
            session_context(&input["history"]),
            Vec::new(),
            Arc::new(ProviderControl::new()),
        );
        let writer_rows = answers(events, "narrative_writer");
        let rows = Arc::clone(&writer_rows);
        provider_input.narrative_writer = Some(Arc::new(move |parameters: JsonMap| {
            let row = next_row(&rows, "narrative_writer", json!([parameters]));
            Box::pin(async move {
                if row.get("raised").is_some() {
                    return Err(writer_failure(&row));
                }
                Ok(row["result"].as_object().cloned().unwrap())
            })
        }));
        let search_rows = answers(events, "knowledge_searcher");
        let rows = Arc::clone(&search_rows);
        provider_input.knowledge_searcher = Some(Arc::new(move |query, sources, limit, offset| {
            let row = next_row(&rows, "knowledge_searcher", json!([query, sources, limit, offset]));
            Box::pin(async move {
                if row.get("raised").is_some() {
                    return Err(raised(&row));
                }
                Ok(row["result"].as_object().cloned().unwrap())
            })
        }));
        let read_rows = answers(events, "reference_reader");
        let rows = Arc::clone(&read_rows);
        provider_input.reference_reader = Some(Arc::new(move |source, path| {
            let row = next_row(&rows, "reference_reader", json!([source, path]));
            Box::pin(async move {
                if row.get("raised").is_some() {
                    return Err(raised(&row));
                }
                Ok(row["result"].as_object().cloned().unwrap())
            })
        }));
        let mut sink = Loop {
            expected: events
                .iter()
                .filter(|e| e["event"] == "phase_event")
                .map(|e| e["value"].clone())
                .collect(),
            answers: events
                .iter()
                .filter(|e| e["event"] == "action_handled")
                .map(|e| e["rejection"].clone())
                .collect(),
        };
        // A snapshot left from an earlier batch is not read: each call reads its own first.
        let mut before = case["before"].clone();
        before["locals"].as_object_mut().unwrap().shift_remove("snapshot");
        let mut state = turn_state::load(&before);
        let turn_id = case["turn_id"].as_str().unwrap();
        let outcome = match phase {
            "prepare_tool_batch" => {
                prepare_tool_batch(&adapter, &mut state, &provider_input, turn_id, &mut sink).await
            }
            _ => execute_tool_batch(&adapter, &mut state, &provider_input, turn_id, &mut sink).await,
        };
        match (outcome, case.get("raised")) {
            (Ok(()), None) => {}
            (Err(error), Some(raised)) => panic!("{name}: both raised; compare {error:?} with {raised}"),
            (outcome, raised) => panic!("{name}: got {outcome:?}, Python raised {raised:?}"),
        }
        owner.check(&name);
        assert!(ids.lock().unwrap().is_empty(), "{name}: Rust drew fewer uuid4 values than Python");
        assert!(sink.expected.is_empty(), "{name}: Rust emitted fewer events than Python");
        for (rows, callback) in [
            (&writer_rows, "narrative_writer"),
            (&search_rows, "knowledge_searcher"),
            (&read_rows, "reference_reader"),
        ] {
            assert!(rows.lock().unwrap().is_empty(), "{name}: Rust called {callback} less often than Python");
        }
        // The snapshot is the owner's read, already checked against the recording; the shared
        // turn-state dump leaves it out.
        let mut expected = turn_state::expected_after(&before, &case["after"]);
        expected["locals"].as_object_mut().unwrap().shift_remove("snapshot");
        // Python assigns non-object argument JSON to `state.parameters` before refusing the call;
        // the Rust local is an object and is left as it was. Nothing reads it for that call.
        if expected["locals"].get("parameters").is_some_and(|parameters| !parameters.is_object()) {
            let before_value = before["locals"].get("parameters").cloned();
            match before_value {
                Some(value) => expected["locals"]["parameters"] = value,
                None => drop(expected["locals"].as_object_mut().unwrap().shift_remove("parameters")),
            }
        }
        turn_state::compare(&name, &turn_state::dump(&state), &expected);
    }
}
