//! Compare the GM turn's setup phases and whole turns (T5a-1) with Python, on real turns.
//!
//! Regenerate with `tests/vectors/generate_turn_vectors.py`, which records `ruleless-turn` and
//! `srd-mechanics-turn`:
//! - `turn_setup_vectors.json`: `prepare_turn_recovery`, `restore_saved_turn_progress` and
//!   `resume_exchange_results`, each real call and its variants. Rust starts from the same turn
//!   locals and input, is answered every owner call Python's phase made, and must emit Python's
//!   events and leave Python's locals (or raise its error).
//! - `turn_vectors.json`: `_turn_events` whole, for each real GM turn; and
//!   `actor_decision_vectors.json` (`generate_actor_decision_vectors.py`), the same for turns on
//!   Python's hidden-token Ruleset, with a required transition and isolated participant decisions. Rust is answered every
//!   owner call, `uuid4` value, catalog, callback and provider stream Python's turn met, in
//!   order; it must send Python's provider requests, emit Python's events (an action answered
//!   with the rejection Python's loop wrote back) and end as Python's turn ended.

mod common;

use common::replay::{self, install_catalogs, rank, Callbacks, Loop, Scripted};
use common::{turn_state, Recorded};
use serde_json::Value as J;
use std::sync::{Arc, Mutex};
use sylvia_agent::provider_session::context::ProviderSessionAdapter;
use sylvia_agent::provider_session::reads::{OpeningReads, TurnOwner};
use sylvia_agent::provider_turn::turn::{
    prepare_turn_recovery, restore_saved_turn_progress, resume_exchange_results, turn_events,
};
use sylvia_agent::provider_turn::TurnFailure;
use sylvia_provider::config::env::Environment;
use sylvia_provider::Provider;

/// The recorded `uuid4` values, drawn in order.
fn uuids(events: &[J]) -> Arc<Mutex<Vec<String>>> {
    Arc::new(Mutex::new(
        events
            .iter()
            .filter(|event| event["event"] == "uuid")
            .map(|event| event["hex"].as_str().unwrap().to_owned())
            .collect(),
    ))
}

/// The adapter Python's turn ran with: its recorded arguments, owner, provider and identities.
fn adapter(
    case: &J,
    owner: &Arc<Recorded>,
    provider: &Arc<Scripted>,
    ids: &Arc<Mutex<Vec<String>>>,
) -> Arc<ProviderSessionAdapter> {
    let args = &case["adapter"];
    let draw = Arc::clone(ids);
    Arc::new(
        ProviderSessionAdapter::new(
            Arc::clone(owner) as Arc<dyn OpeningReads>,
            Arc::clone(provider) as Arc<dyn Provider>,
            args["owner_id"].as_str().unwrap(),
            common::opt(&args["gm_system_prompt"]),
            120.0,
            common::opt(&args["campaign_plan_text"]),
            args["campaign_plan"].as_object().cloned(),
            args["campaign_knowledge"].as_object().cloned(),
            &Environment::default(),
        )
        .expect("the recorded adapter inputs are valid")
        .with_turn_owner(Arc::clone(owner) as Arc<dyn TurnOwner>, Arc::new(rank))
        .with_record_warning(Arc::new(|_: &str| {}))
        .with_ids(Arc::new(move || {
            let mut ids = draw.lock().unwrap();
            assert!(!ids.is_empty(), "Rust drew more uuid4 values than Python");
            ids.remove(0)
        })),
    )
}

/// A phase's or turn's ending against Python's: done, or its refusal's class, code and message.
fn check_ending(name: &str, outcome: Result<(), TurnFailure>, raised: Option<&J>) {
    match (outcome, raised) {
        (Ok(()), None) => {}
        (Err(TurnFailure::Coded(error)), Some(raised)) => {
            assert_eq!(
                J::from(error.error_type.unwrap_or("CommandRejected")),
                raised["class"],
                "{name}: the class"
            );
            // An uncaught exception has no `code`; the owner reports it as `internal_error`.
            let code = raised["code"].as_str().unwrap_or("internal_error");
            assert_eq!(error.code.as_str(), code, "{name}: the code");
            assert_eq!(J::from(error.message.as_str()), raised["message"], "{name}: the message");
        }
        (outcome, raised) => panic!("{name}: got {outcome:?}, Python raised {raised:?}"),
    }
}

#[tokio::test]
async fn turn_setup_matches_python() {
    for (index, case) in replay::cases(include_str!("vectors/turn_setup_vectors.json")).iter().enumerate() {
        let phase = case["phase"].as_str().unwrap();
        let name = format!("{index} {phase} ({})", case["variant"].as_str().unwrap_or("recorded"));
        let events = case["events"].as_array().unwrap();
        let owner = Arc::new(Recorded::new(events));
        let ids = uuids(events);
        let provider = Arc::new(Scripted::new(&case["model"], events));
        let adapter = adapter(case, &owner, &provider, &ids);
        let mut input = replay::provider_input(&case["input"]);
        let callbacks = Callbacks::install(events, &mut input);
        if !case["input"]["has_reference_reader"].as_bool().unwrap() {
            input.reference_reader = None;
        }
        let mut sink = Loop::new(events);
        let mut state = turn_state::load(&case["before"]);
        let outcome = match phase {
            "prepare_turn_recovery" => prepare_turn_recovery(&adapter, &mut state, &input, &mut sink).await,
            "restore_saved_turn_progress" => {
                restore_saved_turn_progress(&mut state, &input).map_err(TurnFailure::Coded)
            }
            "resume_exchange_results" => resume_exchange_results(&mut state).map_err(TurnFailure::Coded),
            other => panic!("{name}: no setup phase {other}"),
        };
        check_ending(&name, outcome, case.get("raised"));
        owner.check(&name);
        assert!(ids.lock().unwrap().is_empty(), "{name}: Rust drew fewer uuid4 values than Python");
        assert!(sink.expected.is_empty(), "{name}: Rust emitted fewer events than Python");
        callbacks.check(&name);
        turn_state::compare(
            &name,
            &turn_state::dump(&state),
            &turn_state::expected_after(&case["before"], &case["after"]),
        );
    }
}

#[tokio::test]
async fn turns_match_python() {
    replay_turns(include_str!("vectors/turn_vectors.json")).await;
}

/// T6b: whole turns with a required transition and isolated participant decisions, traced
/// in-process on Python's hidden-token Ruleset (`generate_actor_decision_vectors.py`).
#[tokio::test]
async fn actor_decision_turns_match_python() {
    replay_turns(include_str!("vectors/actor_decision_vectors.json")).await;
}

/// Each recorded turn replayed through `turn_events`, answered as Python's was.
async fn replay_turns(vectors: &str) {
    for (index, case) in replay::cases(vectors).iter().enumerate() {
        let label = case["scenario"].as_str().unwrap_or_else(|| case["turn_id"].as_str().unwrap());
        let name = format!("{index} turn {label}");
        let events = case["events"].as_array().unwrap();
        let owner = Arc::new(Recorded::new(events));
        let ids = uuids(events);
        let provider = Arc::new(Scripted::new(&case["model"], events));
        let adapter = adapter(case, &owner, &provider, &ids);
        let recorded = &case["input"];
        let mut input = replay::provider_input(recorded);
        let callbacks = Callbacks::install(events, &mut input);
        if !recorded["has_reference_reader"].as_bool().unwrap() {
            input.reference_reader = None;
        }
        if recorded["has_catalog_supplier"].as_bool().unwrap() {
            install_catalogs(events, &mut input);
        }
        let mut sink = Loop::new(events);
        let outcome = turn_events(&adapter, &input, case["turn_id"].as_str().unwrap(), &mut sink).await;
        check_ending(&name, outcome, case.get("raised"));
        owner.check(&name);
        assert!(ids.lock().unwrap().is_empty(), "{name}: Rust drew fewer uuid4 values than Python");
        assert!(sink.expected.is_empty(), "{name}: Rust emitted fewer events than Python");
        callbacks.check(&name);
        provider.check(&name, events);
    }
}
