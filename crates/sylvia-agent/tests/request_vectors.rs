//! Compare one provider round's request helpers (T2c) with Python.
//!
//! Regenerate with `tests/vectors/generate_request_vectors.py`. Every expected value was read
//! back from the Python functions; the adapter's helpers run against a scripted owner whose
//! calls (in order, with results) Rust must make too.

mod common;

use common::{map, maps, session_record, text, Recorded};
use indexmap::IndexMap;
use serde_json::Value as J;
use std::sync::Arc;
use sylvia_agent::native_continuation::{attach_chat_metadata, requires_reasoning_with_tools};
use sylvia_agent::provider_session::context::ProviderSessionAdapter;
use sylvia_agent::provider_session::reads::{OpeningReads, TurnOwner};
use sylvia_agent::provider_session::request::{
    contains_serialized_tool_call, forced_single_tool_call_from_json, legacy_tool_exchange,
    native_content_for_text_operation,
};
use sylvia_agent::provider_turn::state::ToolCallAssembly;
use sylvia_agent::session_loop::{ProviderControl, SessionProviderInput};
use sylvia_agent::stateless_mcp::ModelMechanicsTool;
use sylvia_base::{CodedResult, JsonMap};
use sylvia_domain::session::SessionContext;
use sylvia_provider::config::env::Environment;
use sylvia_provider::types::{ModelInfo, ProviderResponse};
use sylvia_provider::{BoxFuture, ChunkStream, Provider, ProviderRequest, ProviderResult};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/request_vectors.json")).expect("vector file is valid JSON")
}

fn outcome(result: CodedResult<J>) -> J {
    match result {
        Ok(value) => serde_json::json!({"result": value}),
        Err(error) => serde_json::json!({"error": {"code": error.code, "message": error.message}}),
    }
}

fn maps_json(messages: Vec<JsonMap>) -> J {
    J::Array(messages.into_iter().map(J::Object).collect())
}

fn tool(name: &str) -> ModelMechanicsTool {
    ModelMechanicsTool::new("p", "1", "0".repeat(64), name, "c", JsonMap::new())
}

#[test]
fn text_helpers_match_python() {
    let vectors = vectors();
    for case in vectors["serialized"].as_array().unwrap() {
        assert_eq!(J::Bool(contains_serialized_tool_call(case["text"].as_str().unwrap())), case["result"], "{case}");
    }
    for case in vectors["forced"].as_array().unwrap() {
        let aliases: IndexMap<String, ModelMechanicsTool> = case["aliases"]
            .as_array()
            .unwrap()
            .iter()
            .map(|alias| (alias.as_str().unwrap().to_owned(), tool(alias.as_str().unwrap())))
            .collect();
        let mut drawn = 0;
        let result = forced_single_tool_call_from_json(case["text"].as_str().unwrap(), &aliases, || {
            drawn += 1;
            format!("id{drawn:030}")
        });
        let result = result.map_or(J::Null, |assembly| {
            serde_json::json!({"__class__": "_ToolCallAssembly", "call_id": assembly.call_id, "name": assembly.name, "arguments": assembly.arguments})
        });
        assert_eq!(text(&result), text(&case["result"]), "{case}");
        assert_eq!(J::from(drawn), case["ids_drawn"], "{case}");
    }
    let assembly = ToolCallAssembly { call_id: "call-1".into(), name: "tool_a".into(), arguments: "{\"x\":1}".into() };
    for case in vectors["native_text"].as_array().unwrap() {
        let blocks = case["blocks"].as_array().map(|_| maps(&case["blocks"]));
        let result = native_content_for_text_operation(blocks.as_ref(), case["text"].as_str().unwrap(), &assembly).unwrap();
        assert_eq!(text(&result.map_or(J::Null, maps_json)), text(&case["result"]), "{case}");
    }
    for case in vectors["legacy_exchange"].as_array().unwrap() {
        let changes: IndexMap<String, Option<String>> = case["changes"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(key, value)| (key.clone(), value.as_str().map(str::to_owned)))
            .collect();
        let result = legacy_tool_exchange(
            &map(&case["assistant"]),
            case["calls"].as_array().unwrap(),
            &maps(&case["results"]),
            &changes,
        )
        .unwrap();
        assert_eq!(text(&maps_json(result)), text(&case["result"]), "{case}");
    }
    for case in vectors["chat_metadata"].as_array().unwrap() {
        let result = attach_chat_metadata(&map(&case["message"]), Some(&case["metadata"]), case["required"].as_bool().unwrap())
            .map(J::Object);
        assert_eq!(text(&outcome(result)), text(&case["outcome"]), "{case}");
    }
}

fn model(value: &J) -> Option<ModelInfo> {
    value.is_object().then(|| common::model(value))
}

#[test]
fn reasoning_rule_matches_python() {
    for case in vectors()["reasoning"].as_array().unwrap() {
        let model = model(&case["model"]);
        let result = requires_reasoning_with_tools(model.as_ref(), case["has_tools"].as_bool().unwrap());
        assert_eq!(J::Bool(result), case["result"], "{case}");
    }
}

/// A provider that only carries a model.
struct ModelOnly(ModelInfo);

impl Provider for ModelOnly {
    fn model_info(&self) -> Option<&ModelInfo> {
        Some(&self.0)
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

fn adapter(model: &J, owner: Arc<Recorded>) -> ProviderSessionAdapter {
    ProviderSessionAdapter::new(
        Arc::clone(&owner) as Arc<dyn OpeningReads>,
        Arc::new(ModelOnly(common::model(model))),
        "owner",
        None,
        120.0,
        None,
        None,
        None,
        &Environment::default(),
    )
    .unwrap()
    .with_turn_owner(owner as Arc<dyn TurnOwner>, Arc::new(|_, _| Ok(Vec::new())))
}

fn provider_input() -> SessionProviderInput {
    let session = session_record(&serde_json::json!({
        "session_id": "session-1", "campaign_id": "campaign-1", "role": "dm", "provider_id": "prov",
        "model_id": "mod", "status": "active", "parent_session_id": null, "selected_skill": null, "scope": [],
        "reasoning_effort": null, "reasoning_effort_once": null,
    }));
    let history = SessionContext { session: session.clone(), messages: Vec::new() };
    SessionProviderInput::new(session, history, Vec::new(), Arc::new(ProviderControl::new()))
}

fn coded_outcome(result: CodedResult<Vec<JsonMap>>) -> J {
    outcome(result.map(maps_json))
}

#[test]
fn request_messages_match_python() {
    for case in vectors()["request_messages"].as_array().unwrap() {
        let adapter = adapter(&case["model"], Arc::new(Recorded::new(&[])));
        let result = adapter.request_messages(maps(&case["messages"]));
        assert_eq!(text(&coded_outcome(result)), text(&case["outcome"]), "{case}");
    }
}

#[tokio::test]
async fn native_continuation_matches_python() {
    for case in vectors()["native"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let owner = Arc::new(Recorded::new(case["calls"].as_array().unwrap()));
        let adapter = adapter(&case["model"], Arc::clone(&owner));
        let result = adapter
            .attach_native_continuation(maps(&case["messages"]), &provider_input(), case["has_tools"].as_bool().unwrap())
            .await;
        assert_eq!(text(&coded_outcome(result)), text(&case["outcome"]), "{name}");
        owner.check(name);
    }
}

#[tokio::test]
async fn legacy_adoption_matches_python() {
    let model = serde_json::json!({
        "id": "openai_compat/standin-narrator", "name": "standin-narrator", "provider_name": "openai_compat",
        "provider_key": "openai_compat", "provider_mode": "api", "transport": "chat_completions",
        "provider_model_id": "standin-narrator", "context_window": null, "max_output_tokens": null,
    });
    for case in vectors()["adoption"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let owner = Arc::new(Recorded::new(case["calls"].as_array().unwrap()));
        let adapter = adapter(&model, Arc::clone(&owner));
        let result = adapter.legacy_adopted_history(maps(&case["messages"]), "campaign-1").await;
        assert_eq!(text(&coded_outcome(result)), text(&case["outcome"]), "{name}");
        owner.check(name);
    }
}
