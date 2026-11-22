//! Compare the GM opening request (`_messages`) with two real Python openings.
//!
//! Regenerate with `tests/vectors/generate_opening_messages_vectors.py`. Each vector holds the
//! adapter's inputs, the provider input, every owner read the Python assembly made (arguments
//! and full result, in order) and the messages it returned. The fake reads below answer from
//! those results and check that Rust makes the same reads with the same arguments, in the same
//! order; the messages must then be Python's, byte for byte. Domain records are built from the
//! recorded values; fields the assembly never reads (the package lock) are left empty.

use futures_util::FutureExt;
use serde_json::Value as J;
use std::sync::{Arc, Mutex};
use sylvia_agent::campaign_memory::KnowledgeReference;
use sylvia_agent::provider_session::context::{ProviderSessionAdapter, SessionProviderInput};
use sylvia_agent::provider_session::reads::OpeningReads;
use sylvia_base::{CodedResult, JsonMap};
use sylvia_domain::authority::{
    CampaignConstructionBasis, CampaignContinuityView, CampaignDisplaySnapshot, CampaignSnapshot, CampaignState,
    CanonicalReplay, LifecycleState, ModelDecisionBoundary, PackageLock, Participant, ParticipantFields, TurnStatus,
    WriterRole,
};
use sylvia_domain::session::{SessionContext, SessionMessage, SessionMessageKind, SessionRecord, SessionRecordFields, SessionStatus};
use sylvia_provider::config::env::Environment;
use sylvia_provider::types::{ModelInfo, ProviderResponse};
use sylvia_provider::{BoxFuture, ChunkStream, Provider, ProviderRequest, ProviderResult};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/opening_messages_vectors.json")).expect("vector file is valid JSON")
}

fn text(value: &J) -> String {
    serde_json::to_string(value).unwrap()
}

fn opt(value: &J) -> Option<String> {
    value.as_str().map(str::to_owned)
}

fn map(value: &J) -> JsonMap {
    value.as_object().cloned().unwrap_or_default()
}

fn session_record(value: &J) -> SessionRecord {
    SessionRecord::new(SessionRecordFields {
        session_id: value["session_id"].as_str().unwrap().into(),
        campaign_id: opt(&value["campaign_id"]),
        role: WriterRole::parse(value["role"].as_str().unwrap()).unwrap(),
        provider_id: value["provider_id"].as_str().unwrap().into(),
        model_id: value["model_id"].as_str().unwrap().into(),
        status: SessionStatus::parse(value["status"].as_str().unwrap()).unwrap(),
        parent_session_id: opt(&value["parent_session_id"]),
        selected_skill: opt(&value["selected_skill"]),
        scope: value["scope"].as_array().unwrap().iter().map(|item| item.as_str().unwrap().to_owned()).collect(),
        reasoning_effort: opt(&value["reasoning_effort"]),
        reasoning_effort_once: opt(&value["reasoning_effort_once"]),
    })
    .expect("a valid recorded session")
}

fn session_context(value: &J) -> SessionContext {
    SessionContext {
        session: session_record(&value["session"]),
        messages: value["messages"]
            .as_array()
            .unwrap()
            .iter()
            .map(|message| {
                SessionMessage::new(
                    message["session_id"].as_str().unwrap(),
                    message["sequence"].as_i64().unwrap(),
                    message["message_id"].as_str().unwrap(),
                    SessionMessageKind::parse(message["kind"].as_str().unwrap()).unwrap(),
                    &message["content"],
                    message["canonical_sequence"].as_i64(),
                )
                .expect("a valid recorded message")
            })
            .collect(),
    }
}

fn campaign_state(value: &J) -> CampaignState {
    CampaignState {
        campaign_id: value["campaign_id"].as_str().unwrap().into(),
        display_name: value["display_name"].as_str().unwrap().into(),
        lifecycle: LifecycleState::parse(value["lifecycle"].as_str().unwrap()).unwrap(),
        writer: WriterRole::parse(value["writer"].as_str().unwrap()).unwrap(),
        revision: value["revision"].as_i64().unwrap(),
        package_lock: PackageLock::new(Vec::new()).unwrap(),
        state: map(&value["state"]),
        participants: value["participants"]
            .as_array()
            .unwrap()
            .iter()
            .map(|participant| {
                Participant::new(ParticipantFields {
                    participant_id: participant["participant_id"].as_str().unwrap().into(),
                    kind: participant["kind"].as_str().unwrap().into(),
                    package_id: participant["package_id"].as_str().unwrap().into(),
                    package_version: participant["package_version"].as_str().unwrap().into(),
                    data: map(&participant["data"]),
                    package_sha256: opt(&participant["package_sha256"]),
                    schema_id: opt(&participant["schema_id"]),
                    schema_sha256: opt(&participant["schema_sha256"]),
                    validation_status: participant["validation_status"].as_str().unwrap().into(),
                    adaptation_id: opt(&participant["adaptation_id"]),
                })
                .expect("a valid recorded participant")
            })
            .collect(),
    }
}

fn continuity(value: &J) -> CampaignContinuityView {
    let maps = |key: &str| value[key].as_array().unwrap().iter().map(map).collect::<Vec<_>>();
    assert!(value["records"].as_array().is_none_or(Vec::is_empty), "records are not read by the assembly");
    CampaignContinuityView {
        current_scene: value["current_scene"].as_object().cloned(),
        facts: maps("facts"),
        reveals: map(&value["reveals"]),
        plan_progress: maps("plan_progress"),
        records: Vec::new(),
    }
}

fn turns(value: &J) -> Vec<(String, TurnStatus)> {
    value
        .as_object()
        .unwrap()
        .iter()
        .filter(|(key, _)| *key != "__class__")
        .map(|(key, status)| (key.clone(), TurnStatus::parse(status.as_str().unwrap()).unwrap()))
        .collect()
}

fn empty_history(value: &J, key: &str) {
    assert!(value[key].as_array().is_none_or(Vec::is_empty), "{key} are not read by the assembly");
}

fn display_snapshot(value: &J) -> CampaignDisplaySnapshot {
    empty_history(value, "actions");
    empty_history(value, "events");
    CampaignDisplaySnapshot {
        state: campaign_state(value),
        actions: Vec::new(),
        events: Vec::new(),
        turns: turns(&value["turns"]),
        continuity: continuity(&value["continuity"]),
    }
}

fn snapshot(value: &J) -> CampaignSnapshot {
    for key in ["actions", "events", "mutations", "consequences"] {
        empty_history(value, key);
    }
    CampaignSnapshot {
        state: campaign_state(value),
        actions: Vec::new(),
        events: Vec::new(),
        mutations: Vec::new(),
        consequences: Vec::new(),
        turns: turns(&value["turns"]),
        continuity: continuity(&value["continuity"]),
    }
}

fn construction_basis(value: &J) -> CampaignConstructionBasis {
    CampaignConstructionBasis {
        campaign_id: value["campaign_id"].as_str().unwrap().into(),
        display_name: value["display_name"].as_str().unwrap().into(),
        lane: value["lane"].as_str().unwrap().into(),
        canonical_input: map(&value["canonical_input"]),
        canonical_plan: value["canonical_plan"].as_object().cloned(),
        authority_turn_id: value["authority_turn_id"].as_str().unwrap().into(),
        package_lock_id: value["package_lock_id"].as_str().unwrap().into(),
        package_candidate_id: opt(&value["package_candidate_id"]),
        builder_session_id: value["builder_session_id"].as_str().unwrap().into(),
        begin_revision: value["begin_revision"].as_i64().unwrap(),
        completion_revision: value["completion_revision"].as_i64(),
        canonical_representation_sha256: opt(&value["canonical_representation_sha256"]),
    }
}

/// The recorded reads, answered in order.
struct Recorded {
    reads: Mutex<Vec<J>>,
    failures: Mutex<Vec<String>>,
}

impl Recorded {
    fn next(&self, name: &str, args: J) -> J {
        let mut reads = self.reads.lock().unwrap();
        assert!(!reads.is_empty(), "Rust made an extra read: {name} {args}");
        let read = reads.remove(0);
        let mut recorded_args = read["args"].as_array().cloned().unwrap_or_default();
        recorded_args.extend(read["kwargs"].as_object().into_iter().flat_map(|kwargs| kwargs.values().cloned()));
        if read["name"] != J::from(name) || J::Array(recorded_args.clone()) != args {
            self.failures.lock().unwrap().push(format!(
                "read differs: Python {}({}), Rust {name}({args})",
                read["name"],
                J::Array(recorded_args)
            ));
        }
        read["result"].clone()
    }
}

fn ready<'a, T: Send + 'a>(value: T) -> BoxFuture<'a, CodedResult<T>> {
    Box::pin(async move { Ok(value) })
}

impl OpeningReads for Recorded {
    fn session_context<'a>(&'a self, session_id: &'a str) -> BoxFuture<'a, CodedResult<SessionContext>> {
        ready(session_context(&self.next("session_context", serde_json::json!([session_id]))))
    }
    fn model_context_owner<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<Option<JsonMap>>> {
        ready(self.next("model_context_owner", serde_json::json!([campaign_id])).as_object().cloned())
    }
    fn player_action_context<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>> {
        let result = self.next("player_action_context", serde_json::json!([campaign_id]));
        ready(result.as_array().unwrap().iter().map(map).collect())
    }
    fn player_action_response_context<'a>(
        &'a self,
        campaign_id: &'a str,
        response: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>> {
        let result = self.next("player_action_response_context", serde_json::json!([campaign_id, response]));
        ready(result.as_array().unwrap().iter().map(map).collect())
    }
    fn action_sequence_response_results<'a>(
        &'a self,
        session_id: &'a str,
        basis_message_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>> {
        let result = self.next("action_sequence_response_results", serde_json::json!([session_id, basis_message_id]));
        ready(result.as_array().unwrap().iter().map(map).collect())
    }
    fn canonical_replay<'a>(
        &'a self,
        campaign_id: &'a str,
        acknowledged_sequence: i64,
    ) -> BoxFuture<'a, CodedResult<CanonicalReplay>> {
        let result = self.next("canonical_replay", serde_json::json!([campaign_id, acknowledged_sequence]));
        empty_history(&result, "canonical");
        empty_history(&result, "provisional");
        ready(CanonicalReplay { canonical: Vec::new(), provisional: Vec::new(), high_water: result["high_water"].as_i64().unwrap() })
    }
    fn ruleset_gm_operating_guide<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<Option<String>>> {
        ready(opt(&self.next("ruleset_gm_operating_guide", serde_json::json!([campaign_id]))))
    }
    fn model_context_snapshot<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<(CampaignDisplaySnapshot, Option<ModelDecisionBoundary>)>> {
        let result = self.next("model_context_snapshot", serde_json::json!([campaign_id]));
        assert!(result[1].is_null(), "both openings have no decision boundary");
        ready((display_snapshot(&result[0]), None))
    }
    fn campaign_knowledge_catalog<'a>(&'a self, session_id: &'a str) -> BoxFuture<'a, CodedResult<JsonMap>> {
        ready(map(&self.next("campaign_knowledge_catalog", serde_json::json!([session_id]))))
    }
    fn package_text_file<'a>(
        &'a self,
        package_id: &'a str,
        version: &'a str,
        sha256: &'a str,
        path: &'a str,
        _maximum_bytes: i64,
    ) -> BoxFuture<'a, CodedResult<String>> {
        // Python passes the default `maximum_bytes`, so it is not among the recorded arguments.
        let result = self.next("package_text_file", serde_json::json!([package_id, version, sha256, path]));
        ready(result.as_str().unwrap().to_owned())
    }
    fn snapshot<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<CampaignSnapshot>> {
        ready(snapshot(&self.next("snapshot", serde_json::json!([campaign_id]))))
    }
    fn campaign_construction_basis<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<CampaignConstructionBasis>> {
        ready(construction_basis(&self.next("campaign_construction_basis", serde_json::json!([campaign_id]))))
    }
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

fn model(value: &J) -> ModelInfo {
    let get = |key: &str| value[key].as_str().unwrap();
    let mut model = ModelInfo::new(get("id"), get("name"), get("provider_name"), get("provider_key"), get("provider_mode"));
    model.transport = get("transport").into();
    model.provider_model_id = get("provider_model_id").into();
    model
}

/// The openings have no facts, so the ranking only ever sees an empty reference list.
fn no_facts(_query: &str, references: &[KnowledgeReference]) -> Vec<JsonMap> {
    assert!(references.is_empty(), "the recorded openings have no campaign facts");
    Vec::new()
}

#[test]
fn opening_messages_match_python() {
    let vectors = vectors();
    for case in vectors["cases"].as_array().unwrap() {
        let reads = Arc::new(Recorded {
            reads: Mutex::new(case["reads"].as_array().unwrap().clone()),
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
            &Environment::default(),
        )
        .expect("the recorded adapter inputs are valid");
        let input = &case["input"];
        let provider_input = SessionProviderInput {
            session: session_record(&input["session"]),
            history: session_context(&input["history"]),
            catalog: Vec::new(),
            basis_message_id: opt(&input["basis_message_id"]),
            recovery_kind: opt(&input["recovery_kind"]),
            has_reference_reader: input["has_reference_reader"].as_bool().unwrap(),
        };
        // Every recorded read is ready, so the assembly completes on its first poll.
        let messages = adapter
            .messages(&provider_input, &no_facts)
            .now_or_never()
            .expect("the assembly needs no waiting")
            .expect("the assembly succeeds");
        let failures = reads.failures.lock().unwrap().clone();
        assert!(failures.is_empty(), "{}: {}", case["name"], failures.join("\n"));
        assert!(reads.reads.lock().unwrap().is_empty(), "{}: Rust made fewer reads than Python", case["name"]);
        let actual = J::Array(messages.into_iter().map(J::Object).collect());
        let expected = &case["messages"];
        if text(&actual) != text(expected) {
            let rows = |value: &J| value.as_array().unwrap().iter().map(text).collect::<Vec<_>>();
            let (ours, theirs) = (rows(&actual), rows(expected));
            for index in 0..ours.len().max(theirs.len()) {
                let (a, b) = (ours.get(index), theirs.get(index));
                if a != b {
                    panic!(
                        "{}: message {index} differs\n  expected {}\n  got      {}",
                        case["name"],
                        b.map_or("<none>", |text| &text[..text.len().min(1500)]),
                        a.map_or("<none>", |text| &text[..text.len().min(1500)])
                    );
                }
            }
        }
    }
}
