//! Compare one provider round's preparation (T2b) with traced Python runs.
//!
//! Regenerate with `tests/vectors/generate_round_vectors.py`. Each case is one real call of
//! `prepare_round_catalog` or `prepare_provider_round` from the `ruleless-turn` session, or one
//! of its variants. The Rust phase starts from the same turn locals and provider input, draws
//! the same `uuid4` values, is answered the catalog supplier's results and every owner call
//! from the recording (in order, with the same arguments), and must leave the turn locals
//! Python left (or raise its error) and set the same audit contexts.

mod common;

use common::turn_state;
use common::{model, session_context, session_record, tool, Recorded};
use serde_json::Value as J;
use sylvia_base::testing::assert_same_json;
use std::sync::{Arc, Mutex};
use sylvia_agent::campaign_memory::KnowledgeReference;
use sylvia_agent::provider_session::context::ProviderSessionAdapter;
use sylvia_agent::provider_session::reads::TurnOwner;
use sylvia_agent::provider_turn::round::{prepare_provider_round, prepare_round_catalog};
use sylvia_agent::session_loop::{ProviderControl, SessionProviderInput};
use sylvia_base::{CodedResult, JsonMap};
use sylvia_provider::config::env::Environment;
use sylvia_provider::types::{ModelInfo, ProviderResponse};
use sylvia_provider::{BoxFuture, ChunkStream, Provider, ProviderRequest, ProviderResult};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/round_vectors.json")).expect("vector file is valid JSON")
}

/// The GM provider: only its model and audit context are used by the round's preparation.
struct Audited {
    model: ModelInfo,
    contexts: Mutex<Vec<J>>,
}

impl Provider for Audited {
    fn model_info(&self) -> Option<&ModelInfo> {
        Some(&self.model)
    }
    fn generate(&self, _request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        unreachable!("the round's preparation sends nothing")
    }
    fn generate_stream(&self, _request: ProviderRequest) -> ChunkStream<'_> {
        unreachable!("the round's preparation sends nothing")
    }
    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }
    fn set_audit_context(&self, context: &JsonMap) -> bool {
        self.contexts.lock().unwrap().push(J::Object(context.clone()));
        true
    }
}

/// The ruleless campaign has no facts, so the ranking only ever sees an empty list.
fn no_facts(_query: &str, references: &[KnowledgeReference]) -> CodedResult<Vec<JsonMap>> {
    assert!(references.is_empty(), "the recorded campaign has no facts");
    Ok(Vec::new())
}

#[tokio::test]
async fn round_preparation_matches_python() {
    let vectors = vectors();
    for case in vectors["cases"].as_array().unwrap() {
        let phase = case["phase"].as_str().unwrap();
        let name = format!("{phase} ({})", case["variant"].as_str().unwrap_or("recorded"));
        let events = case["events"].as_array().unwrap();
        let owner = Arc::new(Recorded::new(events));
        let ids: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(
            events
                .iter()
                .filter(|event| event["event"] == "uuid")
                .map(|event| event["hex"].as_str().unwrap().to_owned())
                .collect(),
        ));
        let provider = Arc::new(Audited { model: model(&case["model"]), contexts: Mutex::default() });
        let adapter_args = &case["adapter"];
        let draw = Arc::clone(&ids);
        let adapter = ProviderSessionAdapter::new(
            Arc::clone(&owner) as Arc<dyn sylvia_agent::provider_session::reads::OpeningReads>,
            Arc::clone(&provider) as Arc<dyn Provider>,
            adapter_args["owner_id"].as_str().unwrap(),
            common::opt(&adapter_args["gm_system_prompt"]),
            120.0,
            common::opt(&adapter_args["campaign_plan_text"]),
            adapter_args["campaign_plan"].as_object().cloned(),
            adapter_args["campaign_knowledge"].as_object().cloned(),
            &Environment::default(),
        )
        .expect("the recorded adapter inputs are valid")
        .with_turn_owner(Arc::clone(&owner) as Arc<dyn TurnOwner>, Arc::new(no_facts))
        .with_ids(Arc::new(move || {
            let mut ids = draw.lock().unwrap();
            assert!(!ids.is_empty(), "Rust drew more uuid4 values than Python");
            ids.remove(0)
        }));
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
        assert!(input["retry_action_receipt"].is_null(), "no recorded retry receipt");
        if input["has_reference_reader"].as_bool().unwrap() {
            // Live turns always carry one; the round's preparation never reads a reference.
            provider_input.reference_reader = Some(Arc::new(|source, path| {
                panic!("the round's preparation read a reference: {source} {path}")
            }));
        }
        if input["has_catalog_supplier"].as_bool().unwrap() {
            let catalogs: Arc<Mutex<Vec<J>>> = Arc::new(Mutex::new(
                events.iter().filter(|event| event["event"] == "catalog").cloned().collect(),
            ));
            provider_input.catalog_supplier = Some(Arc::new(move |request_id: String| {
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
        let mut state = turn_state::load(&case["before"]);
        let turn_id = case["turn_id"].as_str().unwrap();
        let outcome = match phase {
            "prepare_round_catalog" => {
                prepare_round_catalog(&adapter, &mut state, &provider_input, turn_id).await
            }
            _ => prepare_provider_round(&adapter, &mut state, &provider_input, turn_id).await,
        };
        match (outcome, case.get("raised")) {
            (Ok(()), None) => {}
            (Err(error), Some(raised)) => {
                assert_eq!(J::from(error.message.as_str()), raised["message"], "{name}: the error message");
                let code = raised["code"].as_str().unwrap_or("command_rejected");
                assert_eq!(error.code, code, "{name}: the error code");
            }
            (outcome, raised) => panic!("{name}: got {outcome:?}, Python raised {raised:?}"),
        }
        owner.check(&name);
        assert!(ids.lock().unwrap().is_empty(), "{name}: Rust drew fewer uuid4 values than Python");
        turn_state::compare(
            &name,
            &turn_state::dump(&state),
            &turn_state::expected_after(&case["before"], &case["after"]),
        );
        let audits: Vec<J> = events
            .iter()
            .filter(|event| event["event"] == "audit_context")
            .map(|event| event["context"].clone())
            .collect();
        assert_same_json(
            &J::Array(provider.contexts.lock().unwrap().clone()),
            &J::Array(audits),
            format!("{name}: audit contexts"),
        );
    }
}
