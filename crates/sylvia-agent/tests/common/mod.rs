//! Shared test support for the vector tests that replay traced Python runs: domain values built
//! from Python's `plain()` dumps, and an owner that answers each read from the recording, in
//! order, checking the names and arguments.

#![allow(dead_code)]

pub mod turn_state;

use serde_json::Value as J;
use std::sync::Mutex;
use sylvia_agent::provider_session::reads::{OpeningReads, TurnOwner};
use sylvia_agent::stateless_mcp::ModelMechanicsTool;
use sylvia_base::{CodedResult, JsonMap};
use sylvia_domain::authority::{
    CampaignConstructionBasis, CampaignContinuityView, CampaignDisplaySnapshot, CampaignSnapshot,
    CampaignState, CanonicalEvent, CanonicalEventType, CanonicalReplay, EventVisibility, LifecycleState,
    ModelDecisionActor, ModelDecisionBoundary, ModelDecisionOwner, ModelToolBehavior, PackageDependency, PackageLock,
    PackageLockEntry, PackageRole, Participant, ParticipantFields, TurnStatus, WriterRole,
};
use sylvia_domain::package_library::{PackageLockAvailability, PackageLockRecord};
use sylvia_domain::session::{
    SessionContext, SessionMessage, SessionMessageKind, SessionRecord, SessionRecordFields, SessionStatus,
};
use sylvia_provider::types::ModelInfo;
use sylvia_provider::BoxFuture;

pub fn text(value: &J) -> String {
    serde_json::to_string(value).unwrap()
}

pub fn opt(value: &J) -> Option<String> {
    value.as_str().map(str::to_owned)
}

pub fn map(value: &J) -> JsonMap {
    value.as_object().cloned().unwrap_or_default()
}

pub fn maps(value: &J) -> Vec<JsonMap> {
    value.as_array().map(|items| items.iter().map(map).collect()).unwrap_or_default()
}

pub fn strings(value: &J) -> Vec<String> {
    value
        .as_array()
        .map(|items| items.iter().map(|item| item.as_str().unwrap().to_owned()).collect())
        .unwrap_or_default()
}

pub fn session_record(value: &J) -> SessionRecord {
    SessionRecord::new(SessionRecordFields {
        session_id: value["session_id"].as_str().unwrap().into(),
        campaign_id: opt(&value["campaign_id"]),
        role: WriterRole::parse(value["role"].as_str().unwrap()).unwrap(),
        provider_id: value["provider_id"].as_str().unwrap().into(),
        model_id: value["model_id"].as_str().unwrap().into(),
        status: SessionStatus::parse(value["status"].as_str().unwrap()).unwrap(),
        parent_session_id: opt(&value["parent_session_id"]),
        selected_skill: opt(&value["selected_skill"]),
        scope: strings(&value["scope"]),
        reasoning_effort: opt(&value["reasoning_effort"]),
        reasoning_effort_once: opt(&value["reasoning_effort_once"]),
    })
    .expect("a valid recorded session")
}

pub fn session_message(message: &J) -> SessionMessage {
    SessionMessage::new(
        message["session_id"].as_str().unwrap(),
        message["sequence"].as_i64().unwrap(),
        message["message_id"].as_str().unwrap(),
        SessionMessageKind::parse(message["kind"].as_str().unwrap()).unwrap(),
        &message["content"],
        message["canonical_sequence"].as_i64(),
    )
    .expect("a valid recorded message")
}

pub fn session_context(value: &J) -> SessionContext {
    SessionContext {
        session: session_record(&value["session"]),
        messages: value["messages"].as_array().unwrap().iter().map(session_message).collect(),
    }
}

pub fn participant(value: &J) -> Participant {
    Participant::new(ParticipantFields {
        participant_id: value["participant_id"].as_str().unwrap().into(),
        kind: value["kind"].as_str().unwrap().into(),
        package_id: value["package_id"].as_str().unwrap().into(),
        package_version: value["package_version"].as_str().unwrap().into(),
        data: map(&value["data"]),
        package_sha256: opt(&value["package_sha256"]),
        schema_id: opt(&value["schema_id"]),
        schema_sha256: opt(&value["schema_sha256"]),
        validation_status: value["validation_status"].as_str().unwrap().into(),
        adaptation_id: opt(&value["adaptation_id"]),
    })
    .expect("a valid recorded participant")
}

pub fn package_lock(value: &J) -> PackageLock {
    let entries = value["entries"]
        .as_array()
        .map(|entries| {
            entries
                .iter()
                .map(|entry| {
                    let dependencies = entry["dependencies"]
                        .as_array()
                        .map(|items| {
                            items
                                .iter()
                                .map(|dependency| {
                                    let text = |key: &str| dependency[key].as_str().unwrap();
                                    PackageDependency::new(
                                        text("package_id"),
                                        text("version"),
                                        text("sha256"),
                                        text("kind"),
                                        text("namespace"),
                                    )
                                    .expect("a valid recorded dependency")
                                })
                                .collect()
                        })
                        .unwrap_or_default();
                    PackageLockEntry::new(
                        entry["package_id"].as_str().unwrap(),
                        entry["version"].as_str().unwrap(),
                        entry["sha256"].as_str().unwrap(),
                        PackageRole::parse(entry["role"].as_str().unwrap()).unwrap(),
                        entry["load_order"].as_i64().unwrap(),
                        dependencies,
                        strings(&entry["capabilities"]),
                    )
                    .expect("a valid recorded lock entry")
                })
                .collect()
        })
        .unwrap_or_default();
    PackageLock::new(entries).expect("a valid recorded lock")
}

pub fn campaign_state(value: &J) -> CampaignState {
    CampaignState {
        campaign_id: value["campaign_id"].as_str().unwrap().into(),
        display_name: value["display_name"].as_str().unwrap().into(),
        lifecycle: LifecycleState::parse(value["lifecycle"].as_str().unwrap()).unwrap(),
        writer: WriterRole::parse(value["writer"].as_str().unwrap()).unwrap(),
        revision: value["revision"].as_i64().unwrap(),
        package_lock: package_lock(&value["package_lock"]),
        state: map(&value["state"]),
        participants: value["participants"].as_array().unwrap().iter().map(participant).collect(),
    }
}

pub fn continuity(value: &J) -> CampaignContinuityView {
    assert!(value["records"].as_array().is_none_or(Vec::is_empty), "records are not read");
    CampaignContinuityView {
        current_scene: value["current_scene"].as_object().cloned(),
        facts: maps(&value["facts"]),
        reveals: map(&value["reveals"]),
        plan_progress: maps(&value["plan_progress"]),
        records: Vec::new(),
    }
}

pub fn turns(value: &J) -> Vec<(String, TurnStatus)> {
    value
        .as_object()
        .unwrap()
        .iter()
        .filter(|(key, _)| *key != "__class__")
        .map(|(key, status)| (key.clone(), TurnStatus::parse(status.as_str().unwrap()).unwrap()))
        .collect()
}

fn empty_history(value: &J, key: &str) {
    assert!(value[key].as_array().is_none_or(Vec::is_empty), "{key} are not read");
}

pub fn canonical_event(value: &J) -> CanonicalEvent {
    CanonicalEvent {
        sequence: value["sequence"].as_i64().unwrap(),
        event_type: CanonicalEventType::parse(value["event_type"].as_str().unwrap()).unwrap(),
        turn_id: value["turn_id"].as_str().unwrap().into(),
        action_id: opt(&value["action_id"]),
        safe_payload: map(&value["safe_payload"]),
        visibility: EventVisibility::parse(value["visibility"].as_str().unwrap()).unwrap(),
        attempt_id: opt(&value["attempt_id"]),
        attempt_ordinal: value["attempt_ordinal"].as_i64(),
    }
}

fn events(value: &J) -> Vec<CanonicalEvent> {
    value["events"].as_array().map(|events| events.iter().map(canonical_event).collect()).unwrap_or_default()
}

pub fn display_snapshot(value: &J) -> CampaignDisplaySnapshot {
    empty_history(value, "actions");
    CampaignDisplaySnapshot {
        state: campaign_state(value),
        actions: Vec::new(),
        events: events(value),
        turns: turns(&value["turns"]),
        continuity: continuity(&value["continuity"]),
    }
}

pub fn snapshot(value: &J) -> CampaignSnapshot {
    for key in ["actions", "mutations", "consequences"] {
        empty_history(value, key);
    }
    CampaignSnapshot {
        state: campaign_state(value),
        actions: Vec::new(),
        events: events(value),
        mutations: Vec::new(),
        consequences: Vec::new(),
        turns: turns(&value["turns"]),
        continuity: continuity(&value["continuity"]),
    }
}

pub fn construction_basis(value: &J) -> CampaignConstructionBasis {
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

pub fn boundary(value: &J) -> ModelDecisionBoundary {
    ModelDecisionBoundary {
        owner: ModelDecisionOwner::parse(value["owner"].as_str().unwrap()).unwrap(),
        actor_id: opt(&value["actor_id"]),
        actor_name: opt(&value["actor_name"]),
        actor_state: map(&value["actor_state"]),
        campaign_revision: value["campaign_revision"].as_i64().unwrap(),
        active_side_id: opt(&value["active_side_id"]),
        focused_actor_id: opt(&value["focused_actor_id"]),
        eligible_actors: value["eligible_actors"]
            .as_array()
            .unwrap()
            .iter()
            .map(|actor| ModelDecisionActor {
                actor_id: actor["actor_id"].as_str().unwrap().into(),
                actor_name: actor["actor_name"].as_str().unwrap().into(),
                actor_state: map(&actor["actor_state"]),
            })
            .collect(),
        preparation: value["preparation"].as_object().cloned(),
    }
}

pub fn tool(value: &J) -> ModelMechanicsTool {
    let text = |key: &str| value[key].as_str().unwrap().to_owned();
    let mut tool = ModelMechanicsTool::new(
        text("package_id"),
        text("package_version"),
        text("package_sha256"),
        text("name"),
        text("capability"),
        map(&value["input_schema"]),
    );
    tool.parameter_derivations = value["parameter_derivations"].as_object().cloned();
    tool.description = text("description");
    tool.category = text("category");
    tool.model_behavior = ModelToolBehavior::parse(&text("model_behavior")).unwrap();
    tool
}

pub fn lock_record(value: &J) -> PackageLockRecord {
    PackageLockRecord {
        lock_id: value["lock_id"].as_str().unwrap().into(),
        campaign_id: value["campaign_id"].as_str().unwrap().into(),
        closure_sha256: value["closure_sha256"].as_str().unwrap().into(),
        package_lock: package_lock(&value["package_lock"]),
        availability: PackageLockAvailability::parse(value["availability"].as_str().unwrap()).unwrap(),
        predecessor_lock_id: opt(&value["predecessor_lock_id"]),
        created_revision: value["created_revision"].as_i64().unwrap(),
    }
}

pub fn model(value: &J) -> ModelInfo {
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
    model.max_output_tokens = value["max_output_tokens"].as_i64();
    model
}

/// The recorded owner calls, answered in order.
pub struct Recorded {
    pub reads: Mutex<Vec<J>>,
    pub failures: Mutex<Vec<String>>,
}

impl Recorded {
    /// The `call` events of a recording.
    pub fn new(events: &[J]) -> Self {
        let reads = events.iter().filter(|event| event["event"] == "call").cloned().collect();
        Self { reads: Mutex::new(reads), failures: Mutex::default() }
    }

    pub fn next(&self, name: &str, args: J) -> J {
        let mut reads = self.reads.lock().unwrap();
        assert!(!reads.is_empty(), "Rust made an extra call: {name} {args}");
        let read = reads.remove(0);
        // A different call would be answered with another call's result: stop here.
        assert_eq!(read["name"].as_str(), Some(name), "Rust's next call differs from Python's");
        let mut recorded = read["args"].as_array().cloned().unwrap_or_default();
        recorded.extend(read["kwargs"].as_object().into_iter().flat_map(|kwargs| kwargs.values().cloned()));
        if read["name"].as_str() != Some(name) || J::Array(recorded.clone()) != args {
            self.failures.lock().unwrap().push(format!(
                "call differs: Python {}({}), Rust {name}({args})",
                read["name"],
                J::Array(recorded)
            ));
        }
        read["result"].clone()
    }

    /// Every recorded call was made, and each as Python made it.
    pub fn check(&self, name: &str) {
        let failures = self.failures.lock().unwrap().clone();
        assert!(failures.is_empty(), "{name}: {}", failures.join("\n"));
        let left = self.reads.lock().unwrap().clone();
        assert!(left.is_empty(), "{name}: Rust made fewer calls than Python; next {}", left[0]);
    }
}

pub fn ready<'a, T: Send + 'a>(value: T) -> BoxFuture<'a, CodedResult<T>> {
    Box::pin(async move { Ok(value) })
}

impl OpeningReads for Recorded {
    fn session_context<'a>(&'a self, session_id: &'a str) -> BoxFuture<'a, CodedResult<SessionContext>> {
        ready(session_context(&self.next("session_context", serde_json::json!([session_id]))))
    }
    fn model_context_owner<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<JsonMap>>> {
        ready(self.next("model_context_owner", serde_json::json!([campaign_id])).as_object().cloned())
    }
    fn player_action_context<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>> {
        ready(maps(&self.next("player_action_context", serde_json::json!([campaign_id]))))
    }
    fn player_action_response_context<'a>(
        &'a self,
        campaign_id: &'a str,
        response: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>> {
        ready(maps(&self.next("player_action_response_context", serde_json::json!([campaign_id, response]))))
    }
    fn action_sequence_response_results<'a>(
        &'a self,
        session_id: &'a str,
        basis_message_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>> {
        ready(maps(
            &self.next("action_sequence_response_results", serde_json::json!([session_id, basis_message_id])),
        ))
    }
    fn canonical_replay<'a>(
        &'a self,
        campaign_id: &'a str,
        acknowledged_sequence: i64,
    ) -> BoxFuture<'a, CodedResult<CanonicalReplay>> {
        let result = self.next("canonical_replay", serde_json::json!([campaign_id, acknowledged_sequence]));
        empty_history(&result, "canonical");
        empty_history(&result, "provisional");
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
        ready(opt(&self.next("ruleset_gm_operating_guide", serde_json::json!([campaign_id]))))
    }
    fn model_context_snapshot<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<(CampaignDisplaySnapshot, Option<ModelDecisionBoundary>)>> {
        let result = self.next("model_context_snapshot", serde_json::json!([campaign_id]));
        let private = (!result[1].is_null()).then(|| boundary(&result[1]));
        ready((display_snapshot(&result[0]), private))
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

impl TurnOwner for Recorded {
    fn model_decision_boundary<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<ModelDecisionBoundary>>> {
        let result = self.next("model_decision_boundary", serde_json::json!([campaign_id]));
        ready((!result.is_null()).then(|| boundary(&result)))
    }
    fn campaign_preparation_boundary<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<ModelDecisionBoundary>>> {
        let result = self.next("campaign_preparation_boundary", serde_json::json!([campaign_id]));
        ready((!result.is_null()).then(|| boundary(&result)))
    }
    fn current_package_lock<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<PackageLockRecord>> {
        ready(lock_record(&self.next("current_package_lock", serde_json::json!([campaign_id]))))
    }
    fn record_admin_full_audit(&self, payload: JsonMap) -> CodedResult<()> {
        let result = self.next("record_admin_full_audit", serde_json::json!([payload]));
        assert!(result.is_null(), "the recorder returns None");
        Ok(())
    }
}
