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

use common::replay::{event_json, no_facts, Scripted};
use common::turn_state;
use common::{session_context, session_record, text, tool, Recorded};
use serde_json::Value as J;
use std::sync::{Arc, Mutex};
use sylvia_agent::provider_session::context::ProviderSessionAdapter;
use sylvia_agent::provider_session::reads::{OpeningReads, TurnOwner};
use sylvia_agent::provider_turn::round::stream_provider_round;
use sylvia_agent::provider_turn::{TurnEvents, TurnFailure, TurnResult};
use sylvia_agent::session_loop::{ProviderControl, ProviderEvent, SessionProviderInput};
use sylvia_provider::config::env::Environment;
use sylvia_provider::{BoxFuture, Provider};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/stream_vectors.json")).expect("vector file is valid JSON")
}

struct Collected(Vec<J>);

impl TurnEvents for Collected {
    fn emit(&mut self, event: ProviderEvent) -> BoxFuture<'_, TurnResult<()>> {
        self.0.push(event_json(&event));
        Box::pin(async { Ok(()) })
    }
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
        let provider = Arc::new(Scripted::new(&case["model"], events));
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
        provider.check(&name, events);
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
