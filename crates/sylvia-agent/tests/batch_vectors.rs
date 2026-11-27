//! Compare one round's tool batch (T3b-1), its reconciliation (T3b-2) and the narration phases
//! (T4) with Python, on real turns.
//!
//! Regenerate with `tests/vectors/generate_batch_vectors.py`: `prepare_tool_batch` and
//! `execute_tool_batch` traced in the `ruleless-turn` and `srd-mechanics-turn` turns. Rust is
//! answered every owner call, `uuid4` value and callback (narrative writer, knowledge searcher,
//! reference reader) Python's phase met, in order; its events must be Python's, and an action's
//! rejection is the one Python's session loop wrote back.

mod common;

use common::replay::{self, Callbacks, Loop};
use common::{session_context, session_record, turn_state, Recorded};
use serde_json::Value as J;
use std::sync::{Arc, Mutex};
use sylvia_agent::provider_session::context::ProviderSessionAdapter;
use sylvia_agent::provider_session::reads::{OpeningReads, TurnOwner};
use sylvia_agent::provider_turn::narration;
use sylvia_agent::provider_turn::tool_batch::{execute_tool_batch, prepare_tool_batch, reconcile_tool_batch};
use sylvia_agent::provider_turn::TurnFailure;
use sylvia_agent::session_loop::{ProviderControl, SessionProviderInput};
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

#[tokio::test]
async fn tool_batches_match_python() {
    replay(include_str!("vectors/batch_vectors.json")).await;
}

/// T3b-2: `reconcile_tool_batch`, traced in the same turns (`generate_reconcile_vectors.py`).
#[tokio::test]
async fn tool_reconciliations_match_python() {
    replay(include_str!("vectors/reconcile_vectors.json")).await;
}

/// T4: the narration phases, traced in the same turns (`generate_narration_vectors.py`).
#[tokio::test]
async fn narration_phases_match_python() {
    replay(include_str!("vectors/narration_vectors.json")).await;
}

/// Replay every traced call of one vector file.
async fn replay(text: &str) {
    for (index, case) in replay::cases(text).iter().enumerate() {
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
        let callbacks = Callbacks::install(events, &mut provider_input);
        let mut sink = Loop::new(events);
        // A snapshot left from an earlier batch is not read: each call reads its own first.
        let mut before = case["before"].clone();
        before["locals"].as_object_mut().unwrap().shift_remove("snapshot");
        let mut state = turn_state::load(&before);
        let turn_id = case["turn_id"].as_str().unwrap();
        let outcome = match phase {
            "prepare_tool_batch" => {
                prepare_tool_batch(&adapter, &mut state, &provider_input, turn_id, &mut sink).await
            }
            "reconcile_tool_batch" => {
                reconcile_tool_batch(&adapter, &mut state, &provider_input, turn_id, &mut sink).await
            }
            "record_empty_completion" => {
                narration::record_empty_completion(&mut state, turn_id, &mut sink).await
            }
            "retry_repeated_response" => {
                narration::retry_repeated_response(&mut state, turn_id, &mut sink).await
            }
            "retry_missing_tool_call" => {
                narration::retry_missing_tool_call(&mut state, turn_id, &mut sink).await
            }
            "retry_private_deliberation" => {
                narration::retry_private_deliberation(&mut state).map_err(TurnFailure::Coded)
            }
            "record_held_narration" => {
                narration::record_held_narration(&mut state, &provider_input, &mut sink).await
            }
            "retry_missing_campaign_file_update" => {
                narration::retry_missing_campaign_file_update(&mut state, turn_id, &mut sink).await
            }
            "preserve_boundary_narration" => {
                narration::preserve_boundary_narration(&mut state, turn_id, &mut sink).await
            }
            "preserve_player_intent_narration" => {
                narration::preserve_player_intent_narration(&mut state, turn_id, &mut sink).await
            }
            "retain_campaign_file_narration" => {
                narration::retain_campaign_file_narration(&mut state, turn_id, &mut sink).await
            }
            "record_final_narration" => {
                narration::record_final_narration(&mut state, &provider_input, &mut sink).await
            }
            _ => execute_tool_batch(&adapter, &mut state, &provider_input, turn_id, &mut sink).await,
        };
        match (outcome, case.get("raised")) {
            (Ok(()), None) => {}
            // A phase's refusal is Python's: its class, code and message.
            (Err(TurnFailure::Coded(error)), Some(raised)) => {
                assert_eq!(J::from(error.error_type.unwrap_or("CommandRejected")), raised["class"], "{name}");
                assert_eq!(J::from(error.code.as_str()), raised["code"], "{name}");
                assert_eq!(J::from(error.message.as_str()), raised["message"], "{name}");
            }
            (outcome, raised) => panic!("{name}: got {outcome:?}, Python raised {raised:?}"),
        }
        owner.check(&name);
        assert!(ids.lock().unwrap().is_empty(), "{name}: Rust drew fewer uuid4 values than Python");
        assert!(sink.expected.is_empty(), "{name}: Rust emitted fewer events than Python");
        callbacks.check(&name);
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
