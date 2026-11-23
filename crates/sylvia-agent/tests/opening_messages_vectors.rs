//! Compare the GM opening request (`_messages`) with two real Python openings.
//!
//! Regenerate with `tests/vectors/generate_opening_messages_vectors.py`. Each vector holds the
//! adapter's inputs, the provider input, every owner read the Python assembly made (arguments
//! and full result, in order) and the messages it returned with their ledger tags, once on the
//! route's own (legacy) mode and once with the ledger mode forced. The fake reads below answer
//! from those results and check that Rust makes the same reads with the same arguments, in the
//! same order; the messages must then be Python's, byte for byte, with Python's tags. Domain
//! records are built from the recorded values by the shared `tests/common` helpers.

mod common;

use common::{model, opt, session_context, session_record, text, Recorded};
use futures_util::FutureExt;
use serde_json::Value as J;
use std::sync::{Arc, Mutex};
use sylvia_agent::campaign_memory::KnowledgeReference;
use sylvia_agent::provider_session::context::{
    ProviderSessionAdapter, SessionProviderInput, LEDGER_MODE, MODE_ENV,
};
use sylvia_agent::provider_session::reads::OpeningReads;
use sylvia_agent::session_loop::ProviderControl;
use sylvia_base::{CodedResult, JsonMap};
use sylvia_provider::config::env::Environment;
use sylvia_provider::types::{ModelInfo, ProviderResponse};
use sylvia_provider::{BoxFuture, ChunkStream, Provider, ProviderRequest, ProviderResult};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/opening_messages_vectors.json"))
        .expect("vector file is valid JSON")
}

/// The stand-in provider: only its model is read by the assembly.
struct ModelOnly(ModelInfo);

impl Provider for ModelOnly {
    fn model_info(&self) -> Option<&ModelInfo> {
        Some(&self.0)
    }
    fn generate(&self, _request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        unreachable!("the assembly sends nothing")
    }
    fn generate_stream(&self, _request: ProviderRequest) -> ChunkStream<'_> {
        unreachable!("the assembly sends nothing")
    }
    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }
}

/// The openings have no facts, so the ranking only ever sees an empty reference list.
fn no_facts(_query: &str, references: &[KnowledgeReference]) -> CodedResult<Vec<JsonMap>> {
    assert!(references.is_empty(), "the recorded openings have no campaign facts");
    Ok(Vec::new())
}

/// Assemble one case's request in one mode and compare the reads, the messages and the tags.
fn check(case: &J, mode: &str, recorded: &J, environment: &Environment) {
    let name = format!("{} ({mode})", case["name"].as_str().unwrap());
    let reads = Arc::new(Recorded {
        reads: Mutex::new(recorded["reads"].as_array().unwrap().clone()),
        failures: Mutex::default(),
    });
    let adapter_args = &case["adapter"];
    let adapter = ProviderSessionAdapter::new(
        Arc::clone(&reads) as Arc<dyn OpeningReads>,
        Arc::new(ModelOnly(model(&case["model"]))),
        adapter_args["owner_id"].as_str().unwrap(),
        opt(&adapter_args["gm_system_prompt"]),
        120.0,
        opt(&adapter_args["campaign_plan_text"]),
        adapter_args["campaign_plan"].as_object().cloned(),
        adapter_args["campaign_knowledge"].as_object().cloned(),
        environment,
    )
    .expect("the recorded adapter inputs are valid");
    assert_eq!(adapter.context_mode(), mode, "{name}: the adapter's context mode");
    let input = &case["input"];
    let mut provider_input = SessionProviderInput::new(
        session_record(&input["session"]),
        session_context(&input["history"]),
        Vec::new(),
        Arc::new(ProviderControl::new()),
    );
    provider_input.basis_message_id = opt(&input["basis_message_id"]);
    provider_input.recovery_kind = opt(&input["recovery_kind"]);
    assert_eq!(
        input["has_reference_reader"],
        J::Bool(false),
        "{name}: the openings have no reference reader"
    );
    // Every recorded read is ready, so the assembly completes on its first poll.
    let messages = adapter
        .messages(&provider_input, &no_facts)
        .now_or_never()
        .expect("the assembly needs no waiting")
        .expect("the assembly succeeds");
    let failures = reads.failures.lock().unwrap().clone();
    assert!(
        failures.is_empty(),
        "{name}: {}",
        failures.join(
            "
"
        )
    );
    assert!(reads.reads.lock().unwrap().is_empty(), "{name}: Rust made fewer reads than Python");
    let tags = J::Array(messages.iter().map(|message| J::Object(message.tags.clone())).collect());
    let actual = J::Array(messages.into_iter().map(|message| J::Object(message.message)).collect());
    for (what, ours, theirs) in
        [("message", &actual, &recorded["messages"]), ("tags of message", &tags, &recorded["tags"])]
    {
        if text(ours) == text(theirs) {
            continue;
        }
        let rows = |value: &J| value.as_array().unwrap().iter().map(text).collect::<Vec<_>>();
        let (ours, theirs) = (rows(ours), rows(theirs));
        for index in 0..ours.len().max(theirs.len()) {
            let (a, b) = (ours.get(index), theirs.get(index));
            if a != b {
                panic!(
                    "{name}: {what} {index} differs
  expected {}
  got      {}",
                    b.map_or("<none>", |text| &text[..text.len().min(1500)]),
                    a.map_or("<none>", |text| &text[..text.len().min(1500)])
                );
            }
        }
    }
}

#[test]
fn opening_messages_match_python() {
    let vectors = vectors();
    for case in vectors["cases"].as_array().unwrap() {
        // The route's own mode (legacy for these openings), then the ledger mode forced.
        check(case, case["context_mode"].as_str().unwrap(), case, &Environment::default());
        let ledger = Environment::from_vars(vec![(MODE_ENV.to_owned(), Some(LEDGER_MODE.to_owned()))], false);
        check(case, LEDGER_MODE, &case["ledger"], &ledger);
    }
}
