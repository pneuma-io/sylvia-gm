//! Compare the tool batch's helpers that read the campaign (T3a) with Python, on real turns.
//!
//! Regenerate with `tests/vectors/generate_check_vectors.py`: the helpers traced inside the
//! `ruleless-turn` and `srd-mechanics-turn` turns, each call rerun on copies with the tracer's
//! variants, and each case cut to the locals its helper reads or writes. Rust replays each call
//! against the owner reads Python made, and must leave the same turn locals.

mod common;

use common::{session_context, session_record, turn_state, Recorded};
use serde_json::Value as J;
use sylvia_base::testing::assert_same_json;
use std::sync::Arc;
use sylvia_agent::provider_session::context::ProviderSessionAdapter;
use sylvia_agent::provider_session::reads::{OpeningReads, TurnOwner};
use sylvia_agent::provider_turn::tool_checks::{
    prepare_campaign_file_revision, record_unchanged_campaign_file, reject_ruleless_mechanics,
    settle_campaign_file_narration, validate_revision_speakers,
};
use sylvia_agent::session_loop::{ProviderControl, SessionProviderInput};
use sylvia_base::CodedResult;
use sylvia_provider::config::env::Environment;
use sylvia_provider::types::{ModelInfo, ProviderResponse};
use sylvia_provider::{BoxFuture, ChunkStream, Provider, ProviderRequest, ProviderResult};

/// A provider the helpers never call; with no model facts the route is a legacy one.
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
async fn campaign_reading_helpers_match_python() {
    let vectors: J =
        serde_json::from_str(include_str!("vectors/check_vectors.json")).expect("vector file is valid JSON");
    let cases = vectors["cases"].as_array().expect("cases");
    let mut seen = std::collections::BTreeSet::new();
    for case in cases {
        let phase = case["phase"].as_str().expect("a phase");
        let name = format!("{phase} | {}", case["variant"].as_str().unwrap_or("real call"));
        seen.insert(phase);
        let owner = Arc::new(Recorded::new(case["events"].as_array().expect("events")));
        let adapter = ProviderSessionAdapter::new(
            Arc::clone(&owner) as Arc<dyn OpeningReads>,
            Arc::new(Unused),
            "owner",
            None,
            120.0,
            None,
            None,
            None,
            &Environment::default(),
        )
        .expect("an adapter")
        .with_turn_owner(Arc::clone(&owner) as Arc<dyn TurnOwner>, Arc::new(|_, _| Ok(Vec::new())))
        .with_record_warning(Arc::new(|_: &str| {}));
        let input = &case["input"];
        let provider_input = SessionProviderInput::new(
            session_record(&input["session"]),
            session_context(&input["history"]),
            Vec::new(),
            Arc::new(ProviderControl::new()),
        );
        let mut state = turn_state::load(&case["before"]);
        let outcome: CodedResult<J> = match phase {
            "reject_ruleless_mechanics" => {
                reject_ruleless_mechanics(&adapter, &mut state, &provider_input).await.map(J::Bool)
            }
            "validate_revision_speakers" => {
                validate_revision_speakers(&adapter, &mut state).await.map(|()| J::Null)
            }
            "prepare_campaign_file_revision" => {
                prepare_campaign_file_revision(&adapter, &mut state).await.map(|()| J::Null)
            }
            "settle_campaign_file_narration" => settle_campaign_file_narration(&mut state).map(|()| J::Null),
            "record_unchanged_campaign_file" => record_unchanged_campaign_file(&mut state).map(|()| J::Null),
            other => panic!("unknown helper {other}"),
        };
        match (outcome, case.get("raised")) {
            (Ok(returned), None) => {
                assert_same_json(
                    &returned,
                    &case.get("returned").cloned().unwrap_or(J::Null),
                    format!("{name}: what it returned"),
                )
            }
            (Err(error), Some(raised)) => {
                assert_eq!(J::from(error.message.as_str()), raised["message"], "{name}")
            }
            (outcome, raised) => panic!("{name}: got {outcome:?}, Python raised {raised:?}"),
        }
        owner.check(&name);
        turn_state::compare(
            &name,
            &turn_state::dump(&state),
            &turn_state::expected_after(&case["before"], &case["after"]),
        );
    }
    assert!(
        seen.contains("reject_ruleless_mechanics") && seen.contains("validate_revision_speakers"),
        "{seen:?}"
    );
}
