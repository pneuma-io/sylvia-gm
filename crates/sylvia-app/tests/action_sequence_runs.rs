//! Replay Python's own action-sequence runs through sylvia-app::action_sequences.
//!
//! Each run (`tests/vectors/generate_action_sequence_runs.py`) is one top-level call of `read`,
//! `compose`, `execute` or `last_attempt_commit`, with the ordered trace of every owner operation
//! Python's sequence code made. A scripted [`SequenceOwner`] answers each call from the trace in
//! order, checking the call's name and exact arguments and each guard's nesting (its enter and
//! exit around the body); the run's result or exception must be Python's, and the whole trace must
//! be consumed.

use serde_json::{json, Value as Json};
use std::collections::VecDeque;
use std::future::Future;
use std::sync::Mutex;
use sylvia_app::action_sequences::{
    action_sequence, compose_action_sequence, execute_action_sequence, last_attempt_commit, ComposeRequest,
    ExecuteRequest, PlayerActionAnswer, PlayerActionRequest, ResolvedControl, SequenceOwner, SequenceResult,
};
use sylvia_app::authority::actions::ActionError;
use sylvia_app::authority::content_reads::CatalogTool;
use sylvia_app::player_actions::PlayerControls;
use sylvia_base::testing::json_difference;
use sylvia_base::JsonMap;
use sylvia_domain::authority::{
    ActionReceipt, ActionRecord, ActionStatus, AwaitingChoiceReceipt, CampaignSnapshot, CampaignState,
    CanonicalEvent, CanonicalEventType, ConsequenceRecord, EventVisibility, LifecycleState,
    MechanicsChoiceOption, MechanicsChoiceRequest, ModelToolBehavior, PackageLock, WriterRole,
};
use sylvia_domain::package_library::ToolDeclaration;
use sylvia_domain::session::{
    SessionContext, SessionMessage, SessionMessageKind, SessionRecord, SessionRecordFields, SessionStatus,
};
use sylvia_domain::DomainError;
use sylvia_store::actions::IdempotencyHit;

const RUNS: &str = include_str!("vectors/action_sequence_runs.json");

/// The classes Python's `except AuthorityError` catches, as their `DomainError::Rejected` form.
const AUTHORITY: [&str; 13] = [
    "AuthorityError",
    "CommandRejected",
    "IdempotencyConflict",
    "MechanicsRejected",
    "OwnerConflict",
    "PortableRecoveryFailure",
    "RulesetPreconditionRejected",
    "SchemaIdentityError",
    "StaleRevision",
    "WrongOwner",
    "WrongWriter",
    "_BackupFailure",
    "_ExportFailure",
];

/// A run with every `{"__shared__": key}` replaced by its shared value, at any depth.
fn expand(value: &Json, shared: &Json) -> Json {
    match value {
        Json::Object(map) if map.len() == 1 && map.contains_key("__shared__") => {
            expand(&shared[map["__shared__"].as_str().unwrap()], shared)
        }
        Json::Object(map) => {
            Json::Object(map.iter().map(|(key, item)| (key.clone(), expand(item, shared))).collect())
        }
        Json::Array(items) => Json::Array(items.iter().map(|item| expand(item, shared)).collect()),
        other => other.clone(),
    }
}

fn leak(text: &str) -> &'static str {
    Box::leak(text.to_owned().into_boxed_str())
}

/// The exception a recorded owner call raised.
fn raised(record: &Json) -> ActionError {
    let class = record["class"].as_str().unwrap();
    let message = record["message"].as_str().unwrap_or_default().to_owned();
    let error = match class {
        "ValueError" => DomainError::value(message),
        "TypeError" => DomainError::type_error(message),
        _ if AUTHORITY.contains(&class) => DomainError::Rejected {
            error_type: leak(class),
            code: record["code"].as_str().unwrap_or("command_rejected").to_owned(),
            message,
            diagnostics: Vec::new(),
            mechanics: None,
        },
        _ => DomainError::uncaught(leak(class), message),
    };
    error.into()
}

fn text(value: &Json) -> String {
    value.as_str().unwrap().to_owned()
}

fn optional_text(value: &Json) -> Option<String> {
    value.as_str().map(str::to_owned)
}

fn object(value: &Json) -> JsonMap {
    value.as_object().cloned().unwrap_or_default()
}

fn session(record: &Json) -> SessionContext {
    let session = &record["session"];
    let fields = SessionRecordFields {
        session_id: text(&session["session_id"]),
        campaign_id: optional_text(&session["campaign_id"]),
        role: WriterRole::parse(session["role"].as_str().unwrap()).unwrap(),
        provider_id: text(&session["provider_id"]),
        model_id: text(&session["model_id"]),
        status: SessionStatus::parse(session["status"].as_str().unwrap()).unwrap(),
        ..Default::default()
    };
    let messages = record["messages"]
        .as_array()
        .unwrap()
        .iter()
        .map(|message| {
            SessionMessage::new(
                text(&message["session_id"]),
                message["sequence"].as_i64().unwrap(),
                text(&message["message_id"]),
                SessionMessageKind::parse(message["kind"].as_str().unwrap()).unwrap(),
                &message["content"],
                message["canonical_sequence"].as_i64(),
            )
            .unwrap()
        })
        .collect();
    SessionContext { session: SessionRecord::new(fields).unwrap(), messages }
}

fn snapshot(record: &Json) -> CampaignSnapshot {
    let events = record["events"]
        .as_array()
        .unwrap()
        .iter()
        .map(|event| CanonicalEvent {
            sequence: event["sequence"].as_i64().unwrap(),
            event_type: CanonicalEventType::parse(event["event_type"].as_str().unwrap()).unwrap(),
            turn_id: text(&event["turn_id"]),
            action_id: optional_text(&event["action_id"]),
            safe_payload: JsonMap::new(),
            visibility: EventVisibility::InternalSafe,
            attempt_id: None,
            attempt_ordinal: None,
        })
        .collect();
    let actions = record["actions"]
        .as_array()
        .unwrap()
        .iter()
        .map(|action| ActionRecord {
            action_id: text(&action["action_id"]),
            turn_id: "turn".into(),
            status: ActionStatus::Committed,
            expected_revision: 0,
            resulting_revision: 0,
            writer: WriterRole::Dm,
            package_id: "replay.package".into(),
            package_version: "1.0.0".into(),
            package_sha256: "0".repeat(64),
            tool_name: "replay".into(),
            parameters: JsonMap::new(),
            result: JsonMap::new(),
        })
        .collect();
    let consequences = record["consequences"]
        .as_array()
        .unwrap()
        .iter()
        .map(|item| ConsequenceRecord {
            action_id: text(&item["action_id"]),
            ordinal: item["ordinal"].as_i64().unwrap(),
            payload: object(&item["payload"]),
        })
        .collect();
    CampaignSnapshot {
        state: CampaignState {
            campaign_id: text(&record["campaign_id"]),
            display_name: "Campaign".into(),
            lifecycle: LifecycleState::Active,
            writer: WriterRole::Dm,
            revision: record["revision"].as_i64().unwrap(),
            package_lock: PackageLock::new(Vec::new()).unwrap(),
            state: JsonMap::new(),
            participants: Vec::new(),
        },
        actions,
        events,
        mutations: Vec::new(),
        consequences,
        turns: Vec::new(),
        continuity: Default::default(),
    }
}

fn receipt(record: &Json) -> PlayerActionAnswer {
    match record["kind"].as_str().unwrap() {
        "receipt" => PlayerActionAnswer::Receipt(action_receipt(record)),
        "awaiting_choice" => PlayerActionAnswer::AwaitingChoice(AwaitingChoiceReceipt {
            campaign_id: text(&record["campaign_id"]),
            action_id: text(&record["action_id"]),
            basis_revision: record["basis_revision"].as_i64().unwrap(),
            turn_id: text(&record["turn_id"]),
            choice: MechanicsChoiceRequest {
                choice_id: text(&record["choice"]["choice_id"]),
                prompt: text(&record["choice"]["prompt"]),
                options: record["choice"]["options"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|option| MechanicsChoiceOption {
                        option_id: text(&option["option_id"]),
                        label: text(&option["label"]),
                    })
                    .collect(),
            },
            choice_sequence: record["choice_sequence"].as_i64().unwrap(),
        }),
        _ => PlayerActionAnswer::Preview(object(&record["value"])),
    }
}

fn action_receipt(record: &Json) -> ActionReceipt {
    ActionReceipt {
        campaign_id: text(&record["campaign_id"]),
        action_id: text(&record["action_id"]),
        status: ActionStatus::parse(record["status"].as_str().unwrap()).unwrap(),
        result: object(&record["result"]),
        resulting_revision: record["resulting_revision"].as_i64().unwrap(),
    }
}

fn receipt_args(receipt: &ActionReceipt) -> Json {
    json!({"kind": "receipt", "campaign_id": receipt.campaign_id, "action_id": receipt.action_id,
           "status": receipt.status.as_str(), "result": receipt.result,
           "resulting_revision": receipt.resulting_revision})
}

/// One tool, materialized once from a tiny runtime, then relabelled for each recorded tool.
fn base_tool() -> CatalogTool {
    let package = "replay.sequence";
    let runtime = format!(
        "sylvia_gm_runtime:\n  format: 1\n  state_namespace: {package}\ntools:\n  act:\n    description: Act.\n    parameters: []\n    effects: []\n"
    );
    let declaration = ToolDeclaration {
        name: "act".into(),
        capability: "replay.act".into(),
        input_schema:
            json!({"type": "object", "properties": {}, "required": [], "additionalProperties": false})
                .as_object()
                .unwrap()
                .clone(),
        state_predicates: Vec::new(),
        turn_statuses: vec!["open".into()],
        writers: vec!["dm".into()],
    };
    let sha = "a".repeat(64);
    let mechanic = sylvia_app::package_mechanic::materialize_package_mechanic(
        package,
        "1.0.0",
        &sha,
        &declaration,
        runtime.as_bytes(),
    )
    .unwrap_or_else(|error| panic!("the replay runtime materializes: {error:?}"));
    CatalogTool {
        package_id: package.into(),
        package_version: "1.0.0".into(),
        package_sha256: sha,
        name: "act".into(),
        capability: "replay.act".into(),
        input_schema: JsonMap::new(),
        execute: mechanic,
        description: String::new(),
        model_behavior: ModelToolBehavior::Optional,
        model_behavior_state_predicates: Vec::new(),
        player_controls: PlayerControls::default(),
        check_player_action: None,
        preview_player_action: None,
        decision_title: String::new(),
        permanently_refused: false,
    }
}

fn tool(base: &CatalogTool, record: &Json) -> CatalogTool {
    let mut tool = base.clone();
    tool.package_id = text(&record["package_id"]);
    tool.package_version = text(&record["package_version"]);
    tool.package_sha256 = text(&record["package_sha256"]);
    tool.name = text(&record["name"]);
    tool.capability = text(&record["capability"]);
    tool.model_behavior = ModelToolBehavior::parse(record["model_behavior"].as_str().unwrap()).unwrap();
    tool.decision_title = text(&record["decision_title"]);
    tool.player_controls.available = record["player_controls"]["available"].as_bool().unwrap();
    tool.player_controls.response = text(&record["player_controls"]["response"]);
    tool.player_controls.confirmation = text(&record["player_controls"]["confirmation"]);
    tool
}

fn snapshot_args(snapshot: &CampaignSnapshot) -> Json {
    json!({"campaign_id": snapshot.state.campaign_id, "revision": snapshot.state.revision})
}

fn tool_args(tool: &CatalogTool) -> Json {
    json!({"name": tool.name, "package_sha256": tool.package_sha256})
}

/// The owner that answers from one run's trace.
struct Scripted {
    events: Mutex<VecDeque<Json>>,
    failure: Mutex<Option<String>>,
    base: CatalogTool,
}

impl Scripted {
    fn mismatch(&self, message: String) -> ActionError {
        let mut failure = self.failure.lock().unwrap();
        failure.get_or_insert(message.clone());
        DomainError::uncaught("ReplayMismatch", message).into()
    }

    /// The next recorded call, which must be `name` with `args`; its answer, or what it raised.
    fn call(&self, name: &str, args: Json) -> SequenceResult<Json> {
        let event = self.events.lock().unwrap().pop_front();
        let Some(event) = event else {
            return Err(self.mismatch(format!("Rust called {name} after the trace ended")));
        };
        if event.get("call").and_then(Json::as_str) != Some(name) {
            return Err(self.mismatch(format!("Rust called {name} {args}; Python's next was {event}")));
        }
        let recorded = match (name, event.get("args")) {
            // Python's envelope has no `confirmation` key before its preview, and the request
            // always carries one.
            ("player_action_in_owned_work", Some(Json::Object(recorded))) => {
                let mut normalized = JsonMap::new();
                for key in [
                    "campaign_id",
                    "source",
                    "operation_id",
                    "expected_revision",
                    "selection",
                    "session_id",
                    "confirmation",
                    "preview_only",
                ] {
                    normalized.insert(key.into(), recorded.get(key).cloned().unwrap_or(Json::Null));
                }
                Json::Object(normalized)
            }
            (_, Some(recorded)) => recorded.clone(),
            (_, None) => Json::Null,
        };
        if name != "attempts" {
            if let Some(difference) = json_difference(&args, &recorded) {
                return Err(self.mismatch(format!("{name}: {difference}; Rust passed {args}")));
            }
        }
        if let Some(error) = event.get("raise") {
            return Err(raised(error));
        }
        Ok(event.get("return").cloned().unwrap_or(Json::Null))
    }

    fn enter(&self, guard: &str, args: Json) -> SequenceResult<()> {
        let event = self.events.lock().unwrap().pop_front();
        match event {
            Some(event)
                if event.get("guard").and_then(Json::as_str) == Some(guard)
                    && event.get("enter") == Some(&args) =>
            {
                match event.get("raise") {
                    Some(error) => Err(raised(error)),
                    None => Ok(()),
                }
            }
            other => Err(self.mismatch(format!("Rust entered {guard} {args}; Python's next was {other:?}"))),
        }
    }

    fn exit(&self, guard: &str, args: Json) {
        let event = self.events.lock().unwrap().pop_front();
        match event {
            Some(event)
                if event.get("guard").and_then(Json::as_str) == Some(guard)
                    && event.get("exit") == Some(&args) => {}
            other => {
                self.mismatch(format!("Rust left {guard} {args}; Python's next was {other:?}"));
            }
        }
    }

    async fn guarded<R, Fut>(&self, guard: &str, args: Json, body: impl FnOnce() -> Fut) -> SequenceResult<R>
    where
        Fut: Future<Output = SequenceResult<R>>,
    {
        self.enter(guard, args.clone())?;
        let result = body().await;
        self.exit(guard, args);
        result
    }
}

impl SequenceOwner for Scripted {
    fn with_operation<'a, R, F, Fut>(&'a self, body: F) -> impl Future<Output = SequenceResult<R>> + Send + 'a
    where
        R: Send + 'a,
        F: FnOnce() -> Fut + Send + 'a,
        Fut: Future<Output = SequenceResult<R>> + Send + 'a,
    {
        async move { self.guarded("operation", json!([]), body).await }
    }

    fn with_lane<'a, R, F, Fut>(
        &'a self,
        campaign_id: &'a str,
        body: F,
    ) -> impl Future<Output = SequenceResult<R>> + Send + 'a
    where
        R: Send + 'a,
        F: FnOnce() -> Fut + Send + 'a,
        Fut: Future<Output = SequenceResult<R>> + Send + 'a,
    {
        async move { self.guarded("lane", json!([campaign_id]), body).await }
    }

    fn with_direct_action_work<'a, R, F, Fut>(
        &'a self,
        campaign_id: &'a str,
        body: F,
    ) -> impl Future<Output = SequenceResult<R>> + Send + 'a
    where
        R: Send + 'a,
        F: FnOnce() -> Fut + Send + 'a,
        Fut: Future<Output = SequenceResult<R>> + Send + 'a,
    {
        async move { self.guarded("direct_action_work", json!([campaign_id]), body).await }
    }

    fn with_narration_lock<'a, R, F, Fut>(
        &'a self,
        body: F,
    ) -> impl Future<Output = SequenceResult<R>> + Send + 'a
    where
        R: Send + 'a,
        F: FnOnce() -> Fut + Send + 'a,
        Fut: Future<Output = SequenceResult<R>> + Send + 'a,
    {
        async move { self.guarded("narration", json!([]), body).await }
    }

    fn active_dm_context<'a>(
        &'a self,
        session_id: &'a str,
    ) -> impl Future<Output = SequenceResult<SessionContext>> + Send + 'a {
        async move { Ok(session(&self.call("active_dm_context", json!([session_id]))?)) }
    }

    fn session_context<'a>(
        &'a self,
        session_id: &'a str,
    ) -> impl Future<Output = SequenceResult<SessionContext>> + Send + 'a {
        async move { Ok(session(&self.call("session_context", json!([session_id]))?)) }
    }

    fn append_session_message<'a>(
        &'a self,
        session_id: &'a str,
        message_id: &'a str,
        kind: SessionMessageKind,
        content: &'a JsonMap,
    ) -> impl Future<Output = SequenceResult<()>> + Send + 'a {
        async move {
            let args = json!({"session_id": session_id, "message_id": message_id, "kind": kind.as_str(), "content": content});
            self.call("append_session_message", args).map(|_| ())
        }
    }

    fn snapshot<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> impl Future<Output = SequenceResult<CampaignSnapshot>> + Send + 'a {
        async move { Ok(snapshot(&self.call("snapshot", json!([campaign_id]))?)) }
    }

    fn action_hit<'a>(
        &'a self,
        campaign_id: &'a str,
        action_id: &'a str,
    ) -> impl Future<Output = SequenceResult<Option<IdempotencyHit>>> + Send + 'a {
        async move {
            let hit = self.call("action_hit", json!([campaign_id, action_id]))?;
            Ok((!hit.is_null()).then(|| IdempotencyHit {
                payload_hash: text(&hit["payload_hash"]),
                receipt: action_receipt(&hit["receipt"]),
            }))
        }
    }

    fn has_pending_mechanics_action<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> impl Future<Output = SequenceResult<bool>> + Send + 'a {
        async move { Ok(self.call("has_pending_mechanics_action", json!([campaign_id]))?.as_bool().unwrap()) }
    }

    fn current_open_turn<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> impl Future<Output = SequenceResult<Option<String>>> + Send + 'a {
        async move { Ok(optional_text(&self.call("current_open_turn", json!([campaign_id]))?)) }
    }

    fn campaign_work_count<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> impl Future<Output = SequenceResult<usize>> + Send + 'a {
        async move { Ok(self.call("campaign_work_count", json!([campaign_id]))?.as_u64().unwrap() as usize) }
    }

    fn campaign_session_contexts<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> impl Future<Output = SequenceResult<Vec<SessionContext>>> + Send + 'a {
        async move {
            let contexts = self.call("campaign_session_contexts", json!([campaign_id]))?;
            Ok(contexts.as_array().unwrap().iter().map(session).collect())
        }
    }

    fn player_action_markers<'a>(
        &'a self,
        campaign_id: &'a str,
        action_ids: &'a [&'a str],
        kind: &'a str,
    ) -> impl Future<Output = SequenceResult<Vec<JsonMap>>> + Send + 'a {
        async move {
            let markers = self.call("player_action_markers", json!([campaign_id, action_ids, kind]))?;
            Ok(markers.as_array().unwrap().iter().map(object).collect())
        }
    }

    fn player_control_scope<'a>(
        &'a self,
        snapshot: &'a CampaignSnapshot,
    ) -> impl Future<Output = SequenceResult<Option<JsonMap>>> + Send + 'a {
        async move {
            Ok(self.call("player_control_scope", json!([snapshot_args(snapshot)]))?.as_object().cloned())
        }
    }

    fn resolve_panel_control<'a>(
        &'a self,
        snapshot: &'a CampaignSnapshot,
        source: &'a Json,
        selection: &'a Json,
    ) -> impl Future<Output = SequenceResult<ResolvedControl>> + Send + 'a {
        async move { self.control("resolve_panel_control", snapshot, source, selection) }
    }

    fn resolve_card_control<'a>(
        &'a self,
        snapshot: &'a CampaignSnapshot,
        source: &'a Json,
        selection: &'a Json,
    ) -> impl Future<Output = SequenceResult<ResolvedControl>> + Send + 'a {
        async move { self.control("resolve_card_control", snapshot, source, selection) }
    }

    fn check_player_action<'a>(
        &'a self,
        tool: &'a CatalogTool,
        parameters: &'a JsonMap,
        snapshot: &'a CampaignSnapshot,
    ) -> impl Future<Output = SequenceResult<()>> + Send + 'a {
        async move {
            let args = json!([tool_args(tool), parameters, snapshot_args(snapshot)]);
            self.call("check_player_action", args).map(|_| ())
        }
    }

    fn player_action_preview<'a>(
        &'a self,
        tool: &'a CatalogTool,
        parameters: &'a JsonMap,
        snapshot: &'a CampaignSnapshot,
        source: &'a Json,
        selection: &'a Json,
    ) -> impl Future<Output = SequenceResult<Option<JsonMap>>> + Send + 'a {
        async move {
            let args = json!([tool_args(tool), parameters, snapshot_args(snapshot), source, selection]);
            Ok(self.call("player_action_preview", args)?.as_object().cloned())
        }
    }

    fn campaign_panels<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> impl Future<Output = SequenceResult<JsonMap>> + Send + 'a {
        async move { Ok(object(&self.call("campaign_panels", json!([campaign_id]))?)) }
    }

    fn player_action_in_owned_work<'a>(
        &'a self,
        request: &'a PlayerActionRequest,
    ) -> impl Future<Output = SequenceResult<PlayerActionAnswer>> + Send + 'a {
        async move {
            let args = json!({
                "campaign_id": request.campaign_id, "source": request.source, "operation_id": request.operation_id,
                "expected_revision": request.expected_revision, "selection": request.selection,
                "session_id": request.session_id, "confirmation": request.confirmation,
                "preview_only": request.preview_only,
            });
            Ok(receipt(&self.call("player_action_in_owned_work", args)?))
        }
    }

    fn append_player_action_references<'a>(
        &'a self,
        session_id: &'a str,
        receipt: &'a ActionReceipt,
    ) -> impl Future<Output = SequenceResult<()>> + Send + 'a {
        async move {
            self.call("append_player_action_references", json!([session_id, receipt_args(receipt)]))
                .map(|_| ())
        }
    }

    fn catalog<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> impl Future<Output = SequenceResult<Vec<CatalogTool>>> + Send + 'a {
        async move {
            let tools = self.call("catalog", json!([campaign_id]))?;
            Ok(tools.as_array().unwrap().iter().map(|record| tool(&self.base, record)).collect())
        }
    }

    fn open_narration_attempts<'a>(
        &'a self,
        campaign_id: &'a str,
        turn_id: Option<&'a str>,
    ) -> impl Future<Output = SequenceResult<Vec<String>>> + Send + 'a {
        async move {
            let attempts = self.call("attempts", Json::Null)?;
            Ok(attempts
                .as_array()
                .unwrap()
                .iter()
                .filter(|attempt| {
                    attempt["campaign_id"] == campaign_id
                        && attempt["terminal"] == false
                        && turn_id.is_some_and(|turn| attempt["turn_id"] == turn)
                })
                .map(|attempt| text(&attempt["attempt_id"]))
                .collect())
        }
    }

    fn bind_narration_action<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
        action_id: &'a str,
    ) -> impl Future<Output = SequenceResult<()>> + Send + 'a {
        async move { self.call("bind_narration_action", json!([campaign_id, attempt_id, action_id])).map(|_| ()) }
    }
}

impl Scripted {
    fn control(
        &self,
        name: &str,
        snapshot: &CampaignSnapshot,
        source: &Json,
        selection: &Json,
    ) -> SequenceResult<ResolvedControl> {
        let record = self.call(name, json!([snapshot_args(snapshot), source, selection]))?;
        Ok(ResolvedControl {
            tool: tool(&self.base, &record["tool"]),
            parameters: object(&record["parameters"]),
            label: text(&record["label"]),
            actor_parameter: optional_text(&record["actor_parameter"]),
        })
    }
}

fn error_record(error: &ActionError) -> Json {
    match error {
        ActionError::Domain(error) => {
            json!({"class": error.python_class(), "message": error.message(), "code": error.code()})
        }
        ActionError::Store(error) => match error.rejection() {
            Some(coded) => {
                json!({"class": coded.error_type.unwrap_or("CommandRejected"), "message": coded.message, "code": coded.code})
            }
            None => json!({"class": "StoreError", "message": error.to_string(), "code": null}),
        },
    }
}

async fn replay(run: &Json, owner: &Scripted) -> Json {
    let keywords = &run["arguments"]["keywords"];
    let positional = &run["arguments"]["positional"];
    let keyword = |key: &str| keywords.get(key).cloned().unwrap_or(Json::Null);
    let from_agent = keywords.get("from_agent").and_then(Json::as_bool).unwrap_or(false);
    let outcome = match run["entry"].as_str().unwrap() {
        "read" => action_sequence(
            owner,
            keywords["session_id"].as_str().unwrap(),
            keywords.get("sequence_id").and_then(Json::as_str),
        )
        .await
        .map(|value| value.map_or(Json::Null, Json::Object)),
        "compose" => {
            let request = ComposeRequest {
                campaign_id: text(&keywords["campaign_id"]),
                session_id: text(&keywords["session_id"]),
                sequence_id: text(&keywords["sequence_id"]),
                expected_revision: keyword("expected_revision"),
                steps: keyword("steps"),
                expected_version: keywords.get("expected_version").cloned().unwrap_or(json!(0)),
            };
            compose_action_sequence(owner, &request, from_agent).await.map(Json::Object)
        }
        "execute" => {
            let request = ExecuteRequest {
                campaign_id: text(&keywords["campaign_id"]),
                session_id: text(&keywords["session_id"]),
                sequence_id: text(&keywords["sequence_id"]),
                expected_version: keyword("expected_version"),
                confirmation: optional_text(&keyword("confirmation")),
            };
            execute_action_sequence(owner, &request, from_agent).await.map(Json::Object)
        }
        "last_attempt_commit" => {
            last_attempt_commit(owner, positional[0].as_str().unwrap(), positional[1].as_str().unwrap())
                .await
                .map(|value| value.map_or(Json::Null, Json::from))
        }
        other => panic!("unknown entry {other}"),
    };
    match outcome {
        Ok(value) => json!({"ok": value}),
        Err(error) => json!({"error": error_record(&error)}),
    }
}

#[test]
fn action_sequence_runs_replay_as_python() {
    let vectors: Json = serde_json::from_str(RUNS).unwrap();
    let runs = vectors["runs"].as_array().unwrap();
    assert!(runs.len() >= 40, "the recording is complete");
    let base = base_tool();
    let runtime = tokio::runtime::Builder::new_current_thread().build().unwrap();
    let mut failures = Vec::new();
    for run in runs {
        let run = &expand(run, &vectors["shared"]);
        let events: VecDeque<Json> = run["trace"].as_array().unwrap().iter().cloned().collect();
        let owner = Scripted { events: Mutex::new(events), failure: Mutex::new(None), base: base.clone() };
        let actual = runtime.block_on(replay(run, &owner));
        let label = run["label"].as_str().unwrap();
        if let Some(failure) = owner.failure.lock().unwrap().take() {
            failures.push(format!("{label}: {failure}"));
            continue;
        }
        let left = owner.events.lock().unwrap().len();
        if left != 0 {
            failures.push(format!(
                "{label}: {left} recorded events left unused; next {:?}",
                owner.events.lock().unwrap().front()
            ));
            continue;
        }
        if let Some(difference) = json_difference(&actual, &run["outcome"]) {
            failures.push(format!("{label}: {}", difference.chars().take(1500).collect::<String>()));
        }
    }
    let shown: Vec<String> =
        failures.iter().take(15).map(|failure| failure.chars().take(1500).collect()).collect();
    assert!(failures.is_empty(), "{} of {} runs differ:\n{}", failures.len(), runs.len(), shown.join("\n"));
}
