//! Compare the guided-start opening preflight with two real Python preflights.
//!
//! Regenerate with `tests/vectors/generate_opening_preflight_vectors.py`. Each vector holds the
//! preflight's arguments, the provider the resolver gave, every owner and bus read (arguments
//! and full result, in order) and the Admin Full Audit records, which the trace forced on. The
//! fake reads answer from the recording and check Rust makes the same reads in the same order;
//! the resolver gives the same stand-in Chat Completions provider. The audit's context-fit
//! record (the measurement, the request's SHA-256, the provider identity and the turn's
//! selections) must then be Python's, byte for byte.

use futures_util::future::BoxFuture;
use futures_util::FutureExt;
use serde_json::{json, Value as J};
use std::sync::{Arc, Mutex};
use sylvia_agent::campaign_memory::KnowledgeReference;
use sylvia_agent::provider_session::reads::OpeningReads;
use sylvia_agent::stateless_mcp::ModelMechanicsTool;
use sylvia_base::{CodedResult, JsonMap};
use sylvia_domain::authority::{
    CampaignConstructionBasis, CampaignContinuityView, CampaignDisplaySnapshot, CampaignIndexEntry,
    CampaignSessionIndexEntry, CampaignSnapshot, CampaignState, CanonicalReplay, LifecycleState,
    ModelDecisionBoundary, PackageLock, Participant, ParticipantFields, TurnStatus, WriterRole,
};
use sylvia_domain::session::{
    SessionContext, SessionMessage, SessionMessageKind, SessionRecord, SessionRecordFields, SessionStatus,
};
use sylvia_owner::preflight::{preflight_guided_start, GuidedStartPreflight, PreflightReads};
use sylvia_owner::services::ProviderResolver;
use sylvia_provider::admin_audit::AuditSink;
use sylvia_provider::config::env::Environment;
use sylvia_provider::http::{HttpTransport, ScriptedTransport};
use sylvia_provider::openai_compat::{OpenAICompatProvider, SdkEnvironment};
use sylvia_provider::types::ModelInfo;
use sylvia_provider::Provider;

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/opening_preflight_vectors.json"))
        .expect("vector file is valid JSON")
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
        scope: value["scope"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item.as_str().unwrap().to_owned())
            .collect(),
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

fn snapshot(value: &J) -> CampaignSnapshot {
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

fn index_entry(value: &J) -> CampaignIndexEntry {
    let text = |key: &str| value[key].as_str().unwrap().to_owned();
    CampaignIndexEntry {
        campaign_id: text("campaign_id"),
        display_name: text("display_name"),
        lifecycle: text("lifecycle"),
        writer: text("writer"),
        revision: value["revision"].as_i64().unwrap(),
        ruleset_summary: text("ruleset_summary"),
        package_summary: text("package_summary"),
        package_count: value["package_count"].as_i64().unwrap(),
        playable: value["playable"].as_bool().unwrap(),
        availability: text("availability"),
        ruleset_id: opt(&value["ruleset_id"]),
        ruleset_label: text("ruleset_label"),
        campaign_setting_id: opt(&value["campaign_setting_id"]),
        campaign_setting_label: text("campaign_setting_label"),
        session_activity_ordinal: value["session_activity_ordinal"].as_i64().unwrap(),
        turn_id: opt(&value["turn_id"]),
        turn_status: opt(&value["turn_status"]),
        session_id: opt(&value["session_id"]),
        session_status: opt(&value["session_status"]),
        provider_id: opt(&value["provider_id"]),
        model_id: opt(&value["model_id"]),
        sessions: value["sessions"]
            .as_array()
            .unwrap()
            .iter()
            .map(|session| {
                assert!(session["model_guidance"].is_null(), "the recorded index has no model guidance");
                CampaignSessionIndexEntry {
                    session_id: session["session_id"].as_str().unwrap().into(),
                    role: session["role"].as_str().unwrap().into(),
                    status: session["status"].as_str().unwrap().into(),
                    provider_id: session["provider_id"].as_str().unwrap().into(),
                    model_id: session["model_id"].as_str().unwrap().into(),
                    selected_skill: opt(&session["selected_skill"]),
                    model_guidance: None,
                }
            })
            .collect(),
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

/// The recorded reads, answered in order; the audit records the sink receives.
struct Recorded {
    reads: Mutex<Vec<J>>,
    failures: Mutex<Vec<String>>,
    audit: Arc<Mutex<Vec<J>>>,
}

impl Recorded {
    fn next(&self, name: &str, args: J) -> J {
        let mut reads = self.reads.lock().unwrap();
        assert!(!reads.is_empty(), "Rust made an extra read: {name} {args}");
        let read = reads.remove(0);
        let mut recorded = read["args"].as_array().cloned().unwrap_or_default();
        recorded.extend(read["kwargs"].as_object().into_iter().flat_map(|kwargs| kwargs.values().cloned()));
        if read["name"].as_str() != Some(name) || J::Array(recorded.clone()) != args {
            self.failures.lock().unwrap().push(format!(
                "read differs: Python {}({}), Rust {name}({args})",
                read["name"],
                J::Array(recorded)
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
        ready(session_context(&self.next("session_context", json!([session_id]))))
    }
    fn model_context_owner<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<JsonMap>>> {
        ready(self.next("model_context_owner", json!([campaign_id])).as_object().cloned())
    }
    fn player_action_context<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>> {
        ready(
            self.next("player_action_context", json!([campaign_id]))
                .as_array()
                .unwrap()
                .iter()
                .map(map)
                .collect(),
        )
    }
    fn player_action_response_context<'a>(
        &'a self,
        campaign_id: &'a str,
        response: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>> {
        let result = self.next("player_action_response_context", json!([campaign_id, response]));
        ready(result.as_array().unwrap().iter().map(map).collect())
    }
    fn action_sequence_response_results<'a>(
        &'a self,
        session_id: &'a str,
        basis_message_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>> {
        let result = self.next("action_sequence_response_results", json!([session_id, basis_message_id]));
        ready(result.as_array().unwrap().iter().map(map).collect())
    }
    fn canonical_replay<'a>(
        &'a self,
        campaign_id: &'a str,
        sequence: i64,
    ) -> BoxFuture<'a, CodedResult<CanonicalReplay>> {
        let result = self.next("canonical_replay", json!([campaign_id, sequence]));
        assert!(result["canonical"].as_array().unwrap().is_empty());
        ready(CanonicalReplay {
            canonical: Vec::new(),
            provisional: Vec::new(),
            high_water: result["high_water"].as_i64().unwrap(),
        })
    }
    fn ruleset_gm_operating_guide<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<String>>> {
        ready(opt(&self.next("ruleset_gm_operating_guide", json!([campaign_id]))))
    }
    fn model_context_snapshot<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<(CampaignDisplaySnapshot, Option<ModelDecisionBoundary>)>> {
        let result = self.next("model_context_snapshot", json!([campaign_id]));
        assert!(result[1].is_null());
        let value = &result[0];
        ready((
            CampaignDisplaySnapshot {
                state: campaign_state(value),
                actions: Vec::new(),
                events: Vec::new(),
                turns: turns(&value["turns"]),
                continuity: continuity(&value["continuity"]),
            },
            None,
        ))
    }
    fn campaign_knowledge_catalog<'a>(&'a self, session_id: &'a str) -> BoxFuture<'a, CodedResult<JsonMap>> {
        ready(map(&self.next("campaign_knowledge_catalog", json!([session_id]))))
    }
    fn package_text_file<'a>(
        &'a self,
        package_id: &'a str,
        version: &'a str,
        sha256: &'a str,
        path: &'a str,
        _maximum_bytes: i64,
    ) -> BoxFuture<'a, CodedResult<String>> {
        let result = self.next("package_text_file", json!([package_id, version, sha256, path]));
        ready(result.as_str().unwrap().to_owned())
    }
    fn snapshot<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<CampaignSnapshot>> {
        ready(snapshot(&self.next("snapshot", json!([campaign_id]))))
    }
    fn campaign_construction_basis<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<CampaignConstructionBasis>> {
        ready(construction_basis(&self.next("campaign_construction_basis", json!([campaign_id]))))
    }
}

impl PreflightReads for Recorded {
    fn campaign_index(&self) -> BoxFuture<'_, CodedResult<Vec<CampaignIndexEntry>>> {
        ready(self.next("campaign_index", json!([])).as_array().unwrap().iter().map(index_entry).collect())
    }
    fn admin_full_audit_enabled(&self) -> BoxFuture<'_, CodedResult<bool>> {
        ready(self.next("admin_full_audit_enabled", json!([])).as_bool().unwrap())
    }
    fn admin_full_audit_sink(&self) -> AuditSink {
        let audit = Arc::clone(&self.audit);
        Arc::new(move |record| audit.lock().unwrap().push(record))
    }
    fn campaign_knowledge_catalog_for_plan<'a>(
        &'a self,
        campaign_id: &'a str,
        plan: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<JsonMap>> {
        ready(map(&self.next("campaign_knowledge_catalog_for_plan", json!([campaign_id, plan]))))
    }
    fn catalog<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<Vec<ModelMechanicsTool>>> {
        let result = self.next("bus.catalog", json!([campaign_id]));
        assert!(result.as_array().unwrap().is_empty(), "the recorded preflights have no catalog (#382)");
        ready(Vec::new())
    }
}

fn model(value: &J) -> ModelInfo {
    let get = |key: &str| value[key].as_str().unwrap();
    let mut model = ModelInfo::new(
        get("id"),
        get("name"),
        get("provider_name"),
        get("provider_key"),
        get("provider_mode"),
    );
    model.transport = get("transport").into();
    model.provider_model_id = get("provider_model_id").into();
    model.context_window = value["context_window"].as_i64();
    model.declared_context_window = value["declared_context_window"].as_i64();
    model.declared_context_source = get("declared_context_source").into();
    model.runtime_context_window = value["runtime_context_window"].as_i64();
    model.runtime_context_source = get("runtime_context_source").into();
    model.configured_context_window = value["configured_context_window"].as_i64();
    model.context_window_source = get("context_window_source").into();
    model.declared_context_observed_at = opt(&value["declared_context_observed_at"]);
    model.runtime_context_observed_at = opt(&value["runtime_context_observed_at"]);
    model.configured_context_observed_at = opt(&value["configured_context_observed_at"]);
    model.context_observed_at = opt(&value["context_observed_at"]);
    model.max_output_tokens = value["max_output_tokens"].as_i64();
    model.builder_output_tokens = value["builder_output_tokens"].as_i64().unwrap();
    assert!(value["supported_reasoning_efforts"].is_null() && value["speed_options"].is_null());
    model
}

/// The resolver: the stand-in Chat Completions provider Python resolved, never sent to.
struct Resolved {
    provider: Arc<dyn Provider>,
    sessions: Mutex<Vec<J>>,
}

impl ProviderResolver for Resolved {
    fn resolve<'a>(&'a self, session: &'a SessionRecord) -> BoxFuture<'a, CodedResult<Arc<dyn Provider>>> {
        self.sessions.lock().unwrap().push(json!(session.session_id()));
        ready(Arc::clone(&self.provider))
    }
    fn model_choices(&self) -> BoxFuture<'_, CodedResult<Vec<J>>> {
        unreachable!()
    }
    fn reload(&self) -> BoxFuture<'_, CodedResult<()>> {
        unreachable!()
    }
}

fn no_facts(_query: &str, references: &[KnowledgeReference]) -> CodedResult<Vec<JsonMap>> {
    assert!(references.is_empty(), "the recorded openings have no campaign facts");
    Ok(Vec::new())
}

#[test]
fn opening_preflights_match_python() {
    let vectors = vectors();
    for case in vectors["cases"].as_array().unwrap() {
        assert_eq!(case["outcome"], json!({"ok": true}), "the recorded preflights pass");
        let audit = Arc::new(Mutex::new(Vec::new()));
        let reads = Arc::new(Recorded {
            reads: Mutex::new(case["reads"].as_array().unwrap().clone()),
            failures: Mutex::default(),
            audit: Arc::clone(&audit),
        });
        let resolved = &case["resolved"];
        assert_eq!(resolved["class"], "OpenAICompatProvider");
        let provider = OpenAICompatProvider::new(
            resolved["base_url"].as_str().unwrap(),
            Some("standin-key"),
            Some(model(&resolved["model"])),
            None,
            None,
            Vec::new(),
            SdkEnvironment::default(),
            Arc::new(ScriptedTransport::new(Vec::new())) as Arc<dyn HttpTransport>,
        )
        .unwrap();
        let resolver = Resolved { provider: Arc::new(provider), sessions: Mutex::default() };
        let request = &case["request"];
        let structured_plan = request["structured_plan"].as_object().cloned();
        let preflight = GuidedStartPreflight {
            owner_id: request["owner_id"].as_str().unwrap(),
            campaign_id: request["campaign_id"].as_str().unwrap(),
            provider_id: request["provider_id"].as_str().unwrap(),
            model_id: request["model_id"].as_str().unwrap(),
            plan_text: request["plan_text"].as_str().unwrap(),
            structured_plan: structured_plan.as_ref(),
            gm_system_prompt: request["gm_system_prompt"].as_str(),
            reasoning_effort: request["reasoning_effort"].as_str(),
        };
        let outcome = preflight_guided_start(
            Arc::clone(&reads) as Arc<dyn PreflightReads>,
            &resolver,
            preflight,
            &no_facts,
            &Environment::default(),
        )
        .now_or_never()
        .expect("every recorded read is ready");
        assert!(outcome.is_ok(), "{}: {outcome:?}", case["name"]);
        let failures = reads.failures.lock().unwrap().clone();
        assert!(failures.is_empty(), "{}: {}", case["name"], failures.join("\n"));
        assert!(
            reads.reads.lock().unwrap().is_empty(),
            "{}: Rust made fewer reads than Python",
            case["name"]
        );
        assert_eq!(
            resolver.sessions.lock().unwrap().clone(),
            vec![resolved["session"]["session_id"].clone()]
        );
        let actual = J::Array(audit.lock().unwrap().clone());
        assert_eq!(text(&actual), text(&case["audit"]), "{}: audit records differ", case["name"]);
    }
}
