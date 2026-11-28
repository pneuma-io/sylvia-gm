//! Shared test support for the vector tests that replay traced Python runs: domain values built
//! from Python's `plain()` dumps, and an owner that answers each read from the recording, in
//! order, checking the names and arguments.

#![allow(dead_code)]

pub mod replay;
pub mod turn_state;

use serde_json::Value as J;
use std::sync::Mutex;
use sylvia_agent::provider_session::reads::{HeldWork, NarrationAttemptStart, OpeningReads, TurnOwner};
use sylvia_domain::authority::{NarrationInterruption, ProseChannel};
use sylvia_domain::provider_records::ProviderPrivateRecord;
use sylvia_agent::stateless_mcp::listing::CatalogEntry;
use sylvia_agent::stateless_mcp::AuthorityFailure;
use sylvia_agent::stateless_mcp::ModelMechanicsTool;
use sylvia_app::authority::actions::ActionExecution;
use sylvia_base::testing::json_difference;
use sylvia_base::{CodedResult, JsonMap};
use sylvia_domain::authority::{
    ActionCommand, ActionReceipt, ActionStatus, AwaitingChoiceReceipt, MechanicsChoiceOption,
    MechanicsChoiceRequest,
};
use sylvia_domain::authority::{
    CampaignConstructionBasis, CampaignContinuityRecord, CampaignContinuityView, CampaignDisplaySnapshot,
    CampaignSnapshot, CampaignState, CanonicalEvent, CanonicalEventType, CanonicalReplay, EventVisibility,
    LifecycleState, ModelDecisionActor, ModelDecisionBoundary, ModelDecisionOwner, ModelToolBehavior,
    PackageDependency, PackageLock, PackageLockEntry, PackageRole, Participant, ParticipantFields,
    TurnStatus, WriterRole,
};
use sylvia_domain::error::{DomainError, MechanicsDetails, MechanicsValidationIssue};
use sylvia_domain::package_library::{PackageLockAvailability, PackageLockRecord};
use sylvia_domain::session::SessionActivityBasis;
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
    CampaignContinuityView {
        current_scene: value["current_scene"].as_object().cloned(),
        facts: maps(&value["facts"]),
        reveals: map(&value["reveals"]),
        plan_progress: maps(&value["plan_progress"]),
        records: value["records"]
            .as_array()
            .map(|records| records.iter().map(continuity_record).collect())
            .unwrap_or_default(),
    }
}

pub fn continuity_record(value: &J) -> CampaignContinuityRecord {
    CampaignContinuityRecord {
        entry_id: value["entry_id"].as_str().unwrap().into(),
        record_id: value["record_id"].as_str().unwrap().into(),
        kind: value["kind"].as_str().unwrap().into(),
        status: value["status"].as_str().unwrap().into(),
        payload: map(&value["payload"]),
        basis_message_ids: strings(&value["basis_message_ids"]),
        admitted_revision: value["admitted_revision"].as_i64().unwrap(),
        operation_id: opt(&value["operation_id"]),
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

/// A snapshot's action, mutation or consequence history: no ported phase reads them, so they are
/// built empty whatever was recorded (a phase that starts reading them must parse them first).
fn empty_history(_value: &J, _key: &str) {}

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
        if read["name"].as_str() != Some(name)
            || json_difference(&args, &J::Array(recorded.clone())).is_some()
        {
            self.failures.lock().unwrap().push(format!(
                "call differs: Python {}({}), Rust {name}({args})",
                read["name"],
                J::Array(recorded)
            ));
        }
        read["result"].clone()
    }

    /// The next call's result, or the refusal Python's call raised.
    pub fn next_result(&self, name: &str, args: J) -> CodedResult<J> {
        let raised = {
            let reads = self.reads.lock().unwrap();
            reads.first().and_then(|read| read.get("raised")).cloned()
        };
        let result = self.next(name, args);
        match raised {
            Some(raised) => Err(sylvia_base::CodedError::new(
                raised["code"].as_str().unwrap_or("internal_error"),
                raised["message"].as_str().unwrap_or(""),
            )
            .with_error_type("CommandRejected")),
            None => Ok(result),
        }
    }

    /// Every recorded call was made, and each as Python made it.
    pub fn check(&self, name: &str) {
        let failures = self.failures.lock().unwrap().clone();
        assert!(failures.is_empty(), "{name}: {}", failures.join("\n"));
        let left = self.reads.lock().unwrap().clone();
        assert!(left.is_empty(), "{name}: Rust made fewer calls than Python; next {}", left[0]);
    }
}

/// A session-loop owner call no recorded phase makes.
fn loop_call<'a, T: Send + 'a>(name: &'static str) -> BoxFuture<'a, Result<T, AuthorityFailure>> {
    Box::pin(async move { panic!("no recorded phase reaches the session loop's {name}") })
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
        // The GM turn reads only the canonical events; a recording with provisional projections
        // would need them built too.
        assert!(
            result["provisional"].as_array().is_none_or(Vec::is_empty),
            "a recorded replay has provisional projections the test owner does not build"
        );
        ready(CanonicalReplay {
            canonical: result["canonical"]
                .as_array()
                .map(|events| events.iter().map(canonical_event).collect())
                .unwrap_or_default(),
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
    fn record_mechanics_diagnostic(&self, diagnostic: JsonMap) -> CodedResult<()> {
        // Python passes the fields as keywords; the payload adds its two constant keys.
        assert_eq!(diagnostic["kind"], "mechanics_rejection");
        assert_eq!(diagnostic["privacy"], "safe_metadata_only");
        let fields: Vec<J> = diagnostic
            .iter()
            .filter(|(key, _)| !matches!(key.as_str(), "kind" | "privacy"))
            .map(|(_, v)| v.clone())
            .collect();
        let result = self.next("record_mechanics_diagnostic", J::Array(fields));
        assert!(result.is_null(), "the recorder returns None");
        Ok(())
    }
    fn campaign_panels<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<JsonMap>> {
        let result = self.next_result("campaign_panels", serde_json::json!([campaign_id]));
        Box::pin(async move { result.map(|value| map(&value)) })
    }
    fn action_sequence<'a>(
        &'a self,
        session_id: &'a str,
        sequence_id: &'a J,
    ) -> BoxFuture<'a, CodedResult<J>> {
        let result = self.next_result("action_sequence", serde_json::json!([session_id, sequence_id]));
        Box::pin(async move { result })
    }
    fn compose_action_sequence_from_agent<'a>(
        &'a self,
        campaign_id: &'a str,
        session_id: &'a str,
        arguments: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<J>> {
        let result = self.next_result(
            "_compose_action_sequence_from_agent",
            serde_json::json!([campaign_id, session_id, arguments]),
        );
        Box::pin(async move { result })
    }
    fn execute_action_sequence_from_agent<'a>(
        &'a self,
        campaign_id: &'a str,
        session_id: &'a str,
        arguments: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<J>> {
        let result = self.next_result(
            "_execute_action_sequence_from_agent",
            serde_json::json!([campaign_id, session_id, arguments]),
        );
        Box::pin(async move { result })
    }
    fn provider_continuation<'a>(
        &'a self,
        session_id: &'a str,
        response_id: &'a str,
        provider_id: &'a str,
        model_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<J>>> {
        let result = self.next(
            "provider_continuation",
            serde_json::json!([session_id, response_id, provider_id, model_id]),
        );
        ready((!result.is_null()).then_some(result))
    }
    fn package_record<'a>(
        &'a self,
        package_id: &'a str,
        version: &'a str,
        sha256: &'a str,
    ) -> BoxFuture<'a, CodedResult<JsonMap>> {
        let result = self.next_result("package_record", serde_json::json!([package_id, version, sha256]));
        Box::pin(async move { result.map(|value| map(&value)) })
    }
    fn package_lock_lineage<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Vec<PackageLockRecord>>> {
        let result = self.next("package_lock_lineage", serde_json::json!([campaign_id]));
        ready(result.as_array().unwrap().iter().map(lock_record).collect())
    }
    fn append_agent_message<'a>(
        &'a self,
        session_id: &'a str,
        message_id: &'a str,
        kind: SessionMessageKind,
        content: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<SessionMessage>> {
        let result = self.next(
            "append_agent_message",
            serde_json::json!([session_id, message_id, kind.as_str(), content]),
        );
        ready(session_message(&result))
    }
    fn catalog<'a>(
        &'a self,
        campaign_id: &'a str,
        include_unexposed: bool,
    ) -> BoxFuture<'a, CodedResult<Vec<CatalogEntry>>> {
        // Python passes `include_unexposed` only for the fixed list.
        let args = if include_unexposed {
            serde_json::json!([campaign_id, true])
        } else {
            serde_json::json!([campaign_id])
        };
        let result = self.next_result("catalog", args);
        Box::pin(
            async move { result.map(|value| value.as_array().unwrap().iter().map(catalog_entry).collect()) },
        )
    }
    fn accepted_campaign_plan<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<JsonMap>> {
        let result = self.next_result("accepted_campaign_plan", serde_json::json!([campaign_id]));
        Box::pin(async move { result.map(|plan| map(&plan["content"])) })
    }
    fn campaign_narrative_options<'a>(&'a self, session_id: &'a str) -> BoxFuture<'a, CodedResult<JsonMap>> {
        let result = self.next_result("campaign_narrative_options", serde_json::json!([session_id]));
        Box::pin(async move { result.map(|value| map(&value)) })
    }
    fn execute_action<'a>(
        &'a self,
        command: &'a ActionCommand,
    ) -> BoxFuture<'a, Result<ActionExecution, AuthorityFailure>> {
        let raised = self.reads.lock().unwrap().first().and_then(|read| read.get("raised")).cloned();
        let result = self.next("execute_action", serde_json::json!([action_command_json(command)]));
        let outcome = match raised {
            Some(raised) => Err(recorded_failure(&raised)),
            None => Ok(recorded_execution(&result)),
        };
        Box::pin(async move { outcome })
    }
    fn session_activity_basis<'a>(
        &'a self,
        session_id: &'a str,
        attempt_id: Option<&'a str>,
    ) -> BoxFuture<'a, CodedResult<SessionActivityBasis>> {
        let result = self.next("session_activity_basis", serde_json::json!([session_id, attempt_id]));
        let message = |value: &J| (!value.is_null()).then(|| session_message(value));
        ready(SessionActivityBasis {
            session: session_record(&result["session"]),
            accepted: message(&result["accepted"]),
            terminal: message(&result["terminal"]),
            has_compaction: result["has_compaction"].as_bool().unwrap(),
        })
    }
    fn actor_decision_request<'a>(
        &'a self,
        campaign_id: &'a str,
        turn_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<sylvia_app::actor_decisions::ActorDecisionRequest>>> {
        let result = self.next("actor_decision_request", serde_json::json!([campaign_id, turn_id]));
        // A recorded request is T6's (`run_required_actor_decision`); none is recorded yet.
        assert!(result.is_null(), "a recorded actor decision request is not replayed before T6");
        ready(None)
    }
    // The session loop's DM run (T5a-2) is proven by the comparison tool's sessions; no phase
    // vector reaches these calls, so a test that does fails here.
    fn agent_session_work<'a>(&'a self, _session_id: &'a str) -> BoxFuture<'a, Result<HeldWork, AuthorityFailure>> {
        loop_call("agent_session_work")
    }
    fn campaign_ending_choice_text<'a>(
        &'a self,
        _session_id: &'a str,
        _message_id: &'a str,
        _choice: &'a JsonMap,
    ) -> BoxFuture<'a, Result<String, AuthorityFailure>> {
        loop_call("campaign_ending_choice_text")
    }
    fn player_action_response_basis<'a>(
        &'a self,
        _session_id: &'a str,
        _action_id: &'a str,
        _automatic: bool,
    ) -> BoxFuture<'a, Result<JsonMap, AuthorityFailure>> {
        loop_call("player_action_response_basis")
    }
    fn unresolved_session_interruption<'a>(
        &'a self,
        _context: &'a SessionContext,
    ) -> BoxFuture<'a, Result<Option<(NarrationInterruption, bool)>, AuthorityFailure>> {
        loop_call("unresolved_session_interruption")
    }
    fn narration_interruption<'a>(
        &'a self,
        _campaign_id: &'a str,
        _interruption_id: &'a str,
    ) -> BoxFuture<'a, Result<NarrationInterruption, AuthorityFailure>> {
        loop_call("narration_interruption")
    }
    fn redirect_narration<'a>(
        &'a self,
        _session_id: &'a str,
        _message_id: &'a str,
        _content: &'a JsonMap,
        _interruption_id: &'a str,
    ) -> BoxFuture<'a, Result<NarrationInterruption, AuthorityFailure>> {
        loop_call("redirect_narration")
    }
    fn saved_action_receipt<'a>(
        &'a self,
        _campaign_id: &'a str,
        _action_id: &'a str,
    ) -> BoxFuture<'a, Result<ActionReceipt, AuthorityFailure>> {
        loop_call("saved_action_receipt")
    }
    fn start_narration_attempt(&self, _start: NarrationAttemptStart) -> BoxFuture<'_, Result<(), AuthorityFailure>> {
        loop_call("start_narration_attempt")
    }
    fn emit_narration_token<'a>(
        &'a self,
        _campaign_id: &'a str,
        _attempt_id: &'a str,
        _channel: ProseChannel,
        _token: &'a str,
        _response_blocks: Option<&'a J>,
    ) -> BoxFuture<'a, Result<(), AuthorityFailure>> {
        loop_call("emit_narration_token")
    }
    fn flush_narration<'a>(&'a self, _campaign_id: &'a str, _attempt_id: &'a str) -> BoxFuture<'a, Result<(), AuthorityFailure>> {
        loop_call("flush_narration")
    }
    fn bind_narration_action<'a>(
        &'a self,
        _campaign_id: &'a str,
        _attempt_id: &'a str,
        _action_id: &'a str,
    ) -> BoxFuture<'a, Result<(), AuthorityFailure>> {
        loop_call("bind_narration_action")
    }
    fn complete_narration_attempt<'a>(
        &'a self,
        _campaign_id: &'a str,
        _attempt_id: &'a str,
    ) -> BoxFuture<'a, Result<Vec<CanonicalEvent>, AuthorityFailure>> {
        loop_call("complete_narration_attempt")
    }
    fn interrupt_narration<'a>(
        &'a self,
        _campaign_id: &'a str,
        _attempt_id: &'a str,
    ) -> BoxFuture<'a, Result<NarrationInterruption, AuthorityFailure>> {
        loop_call("interrupt_narration")
    }
    fn disconnect_narration<'a>(
        &'a self,
        _campaign_id: &'a str,
        _attempt_id: &'a str,
        _now: f64,
    ) -> BoxFuture<'a, Result<f64, AuthorityFailure>> {
        loop_call("disconnect_narration")
    }
    fn reconnect_narration<'a>(
        &'a self,
        _campaign_id: &'a str,
        _attempt_id: &'a str,
        _now: f64,
        _acknowledged_sequence: i64,
    ) -> BoxFuture<'a, Result<CanonicalReplay, AuthorityFailure>> {
        loop_call("reconnect_narration")
    }
    fn expire_narration<'a>(
        &'a self,
        _campaign_id: &'a str,
        _attempt_id: &'a str,
        _now: f64,
    ) -> BoxFuture<'a, Result<NarrationInterruption, AuthorityFailure>> {
        loop_call("expire_narration")
    }
    fn append_private_provider_record<'a>(
        &'a self,
        _record: &'a ProviderPrivateRecord,
    ) -> BoxFuture<'a, Result<(), AuthorityFailure>> {
        loop_call("append_private_provider_record")
    }
    fn finish_private_provider_attempt<'a>(
        &'a self,
        _session_id: &'a str,
        _attempt_id: &'a str,
        _completed: bool,
    ) -> BoxFuture<'a, Result<(), AuthorityFailure>> {
        loop_call("finish_private_provider_attempt")
    }
    fn append_session_canonical_references<'a>(
        &'a self,
        _session_id: &'a str,
        _events: &'a [CanonicalEvent],
    ) -> BoxFuture<'a, Result<Vec<SessionMessage>, AuthorityFailure>> {
        loop_call("append_session_canonical_references")
    }
    fn append_live_steering_message<'a>(
        &'a self,
        _session_id: &'a str,
        _message_id: &'a str,
        _kind: SessionMessageKind,
        _content: &'a JsonMap,
    ) -> BoxFuture<'a, Result<SessionMessage, AuthorityFailure>> {
        loop_call("append_live_steering_message")
    }
    fn stop_agent_session<'a>(&'a self, _session_id: &'a str) -> BoxFuture<'a, Result<SessionRecord, AuthorityFailure>> {
        loop_call("stop_agent_session")
    }
    fn current_open_turn<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<Option<String>>> {
        ready(opt(&self.next("current_open_turn", serde_json::json!([campaign_id]))))
    }
    fn update_campaign_file<'a>(
        &'a self,
        session_id: &'a str,
        update_id: &'a str,
        updates: Vec<JsonMap>,
        basis_message_ids: Vec<String>,
        turn_id: Option<&'a str>,
    ) -> BoxFuture<'a, Result<JsonMap, AuthorityFailure>> {
        let args = serde_json::json!([session_id, update_id, updates, basis_message_ids, turn_id]);
        let outcome = self.answer("update_campaign_file", args);
        Box::pin(async move { outcome })
    }
    fn search_campaign_knowledge<'a>(
        &'a self,
        campaign_id: &'a str,
        query: &'a J,
        sources: Option<&'a J>,
        limit: &'a J,
        offset: &'a J,
    ) -> BoxFuture<'a, Result<JsonMap, AuthorityFailure>> {
        let args = serde_json::json!([campaign_id, query, sources, limit, offset]);
        let outcome = self.answer("search_campaign_knowledge", args);
        Box::pin(async move { outcome })
    }
    fn read_campaign_reference<'a>(
        &'a self,
        campaign_id: &'a str,
        source: &'a J,
        path: &'a J,
    ) -> BoxFuture<'a, Result<JsonMap, AuthorityFailure>> {
        let outcome = self.answer("read_campaign_reference", serde_json::json!([campaign_id, source, path]));
        Box::pin(async move { outcome })
    }
}

impl Recorded {
    /// The next call's object result, or the refusal Python's call raised, rebuilt in its class.
    pub fn answer(&self, name: &str, args: J) -> Result<JsonMap, AuthorityFailure> {
        let raised = self.reads.lock().unwrap().first().and_then(|read| read.get("raised")).cloned();
        let result = self.next(name, args);
        match raised {
            Some(raised) => Err(recorded_failure(&raised)),
            None => Ok(map(&result)),
        }
    }
}

/// `plain(command)` for an `ActionCommand`.
pub fn action_command_json(command: &ActionCommand) -> J {
    serde_json::json!({
        "__class__": "ActionCommand", "campaign_id": command.campaign_id, "action_id": command.action_id,
        "idempotency_key": command.idempotency_key, "owner_id": command.owner_id,
        "writer": command.writer.as_str(), "expected_revision": command.expected_revision,
        "turn_id": command.turn_id, "package_id": command.package_id, "tool_name": command.tool_name,
        "parameters": command.parameters, "initiating_session_id": command.initiating_session_id,
    })
}

/// A recorded exception's class, as the port names it.
fn class_name(name: &str) -> &'static str {
    const KNOWN: [&str; 12] = [
        "CommandRejected",
        "MechanicsRejected",
        "RulesetPreconditionRejected",
        "StaleRevision",
        "WrongOwner",
        "WrongWriter",
        "IdempotencyConflict",
        "OwnerConflict",
        "ValueError",
        "TypeError",
        "KeyError",
        "RuntimeError",
    ];
    KNOWN.into_iter().find(|known| *known == name).unwrap_or_else(|| panic!("unknown class {name}"))
}

/// A recorded owner refusal (`refusal_fields`), rebuilt in its class.
pub fn recorded_failure(raised: &J) -> AuthorityFailure {
    let class = class_name(raised["class"].as_str().unwrap());
    let message = raised["message"].as_str().unwrap().to_owned();
    let texts = |value: &J| -> Vec<String> {
        value
            .as_array()
            .map(|items| items.iter().map(|item| item.as_str().unwrap().to_owned()).collect())
            .unwrap_or_default()
    };
    let mechanics = raised.get("validation_issues").and_then(J::as_array).map(|issues| {
        Box::new(MechanicsDetails {
            field_paths: texts(&raised["field_paths"]),
            validation_issues: issues
                .iter()
                .map(|payload| MechanicsValidationIssue {
                    path: payload["path"].as_str().unwrap().to_owned(),
                    kind: payload["kind"].as_str().unwrap().to_owned(),
                    expected_type: payload["expected_type"].as_str().map(str::to_owned),
                    actual_type: payload["actual_type"].as_str().map(str::to_owned),
                    parent_path: payload["parent_path"].as_str().map(str::to_owned),
                    allowed_values: texts(&payload["allowed_values"]),
                    minimum: payload["minimum"].as_number().cloned(),
                    maximum: payload["maximum"].as_number().cloned(),
                    actual_length: payload["actual_length"].as_i64(),
                })
                .collect(),
            state_changed: raised["state_changed"].as_bool().unwrap_or(false),
            refusal_reasons: texts(&raised["refusal_reasons"]),
        })
    });
    AuthorityFailure::Domain(match class {
        "ValueError" => DomainError::Value(message),
        "TypeError" => DomainError::Type(message),
        "KeyError" | "RuntimeError" => DomainError::uncaught(class, message),
        _ => DomainError::Rejected {
            error_type: class,
            code: raised["code"].as_str().unwrap().to_owned(),
            message,
            diagnostics: Vec::new(),
            mechanics,
        },
    })
}

/// One recorded receipt (`plain(receipt)`).
pub fn recorded_execution(value: &J) -> ActionExecution {
    let text = |value: &J, key: &str| value[key].as_str().unwrap().to_owned();
    match value["__class__"].as_str().unwrap() {
        "AwaitingChoiceReceipt" => ActionExecution::AwaitingChoice(AwaitingChoiceReceipt {
            campaign_id: text(value, "campaign_id"),
            action_id: text(value, "action_id"),
            basis_revision: value["basis_revision"].as_i64().unwrap(),
            turn_id: text(value, "turn_id"),
            choice: MechanicsChoiceRequest {
                choice_id: text(&value["choice"], "choice_id"),
                prompt: text(&value["choice"], "prompt"),
                options: value["choice"]["options"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|option| MechanicsChoiceOption {
                        option_id: text(option, "option_id"),
                        label: text(option, "label"),
                    })
                    .collect(),
            },
            choice_sequence: value["choice_sequence"].as_i64().unwrap(),
        }),
        "ActionReceipt" => ActionExecution::Receipt(ActionReceipt {
            campaign_id: text(value, "campaign_id"),
            action_id: text(value, "action_id"),
            status: ActionStatus::parse(value["status"].as_str().unwrap()).unwrap(),
            result: map(&value["result"]),
            resulting_revision: value["resulting_revision"].as_i64().unwrap(),
        }),
        other => panic!("unknown receipt {other}"),
    }
}

/// One recorded `CatalogTool`: the fields the listing reads.
pub fn catalog_entry(value: &J) -> CatalogEntry {
    let text = |key: &str| value[key].as_str().unwrap().to_owned();
    CatalogEntry {
        package_id: text("package_id"),
        package_version: text("package_version"),
        package_sha256: text("package_sha256"),
        name: text("name"),
        capability: text("capability"),
        input_schema: map(&value["input_schema"]),
        description: text("description"),
        model_behavior: ModelToolBehavior::parse(value["model_behavior"].as_str().unwrap()).unwrap(),
    }
}
