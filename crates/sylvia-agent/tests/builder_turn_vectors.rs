//! Compare one canonical Builder turn (B3a, `stream_builder_turn`) with the frozen Python: each
//! scenario's provider events, requests, tool definitions, result and outcome, with the same
//! scripted provider and steering.
//!
//! Regenerate with `tests/vectors/generate_builder_turn_vectors.py` (needs the snapshot's
//! Python).

use futures_util::{stream, StreamExt};
use serde_json::json;
use std::collections::VecDeque;
use std::path::Path;
use std::sync::{Arc, Mutex};
use sylvia_agent::builder::character_tools::ParticipantInput;
use sylvia_agent::builder::source_tools::{
    CampaignContent, LibrarySources, ReferenceSource, SettingPublisher,
};
use sylvia_agent::builder::turn::{exported_tool, stream_builder_turn, BuilderTurn, BuilderTurnTools};
use sylvia_agent::provider_turn::TurnFailure;
use sylvia_agent::session_loop::provider::{SessionEvents, SessionFailure, SessionProviderError};
use sylvia_agent::session_loop::{ProviderControl, ProviderEvent};
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::WriterRole;
use sylvia_domain::package_library::ParticipantArtifactDeclaration;
use sylvia_domain::session::{
    SessionContext, SessionMessage, SessionMessageKind, SessionRecord, SessionRecordFields, SessionStatus,
};
use sylvia_provider::types::{ModelInfo, ProviderResponse, ProviderStreamChunk, PyValue};
use sylvia_provider::{BoxFuture, ChunkStream, Provider, ProviderRequest, ProviderResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/builder_turn_vectors.json")).expect("vector file is valid JSON")
}

struct Scripted {
    model: Option<ModelInfo>,
    responses: Mutex<VecDeque<Json>>,
    requests: Mutex<Vec<Json>>,
    definitions: Mutex<Option<Json>>,
    control: Arc<ProviderControl>,
    after_first: Mutex<Option<Vec<String>>>,
}

fn chunk(item: &JsonMap) -> ProviderResult<ProviderStreamChunk> {
    let text = |key: &str| item.get(key).and_then(Json::as_str).unwrap_or_default().to_owned();
    Ok(ProviderStreamChunk {
        content: text("content"),
        reasoning_content: text("reasoning_content"),
        tool_call_delta: item.get("tool_call_delta").cloned(),
        finish_reason: item
            .get("finish_reason")
            .and_then(Json::as_str)
            .map(|reason| PyValue::Text(reason.into())),
        ..ProviderStreamChunk::default()
    })
}

impl Provider for Scripted {
    fn model_info(&self) -> Option<&ModelInfo> {
        self.model.as_ref()
    }
    fn generate(&self, _request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        unreachable!("the Builder streams")
    }
    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        let names = request
            .tools
            .as_ref()
            .map(|tools| Json::Array(tools.iter().map(|t| t["function"]["name"].clone()).collect()));
        // Python's working context passes `timeout` as a keyword, before the other options.
        let mut options = JsonMap::new();
        options.insert("timeout".into(), json!(request.timeout));
        options.extend(request.options.clone());
        self.requests.lock().unwrap().push(json!({
            "messages": request.messages, "tools": names.unwrap_or(Json::Null), "options": options,
        }));
        let mut definitions = self.definitions.lock().unwrap();
        if definitions.is_none() {
            *definitions = Some(request.tools.clone().map_or(Json::Null, Json::Array));
        }
        let response = self.responses.lock().unwrap().pop_front().expect("a scripted response");
        let items: Vec<_> =
            response.as_array().unwrap().iter().map(|item| chunk(item.as_object().unwrap())).collect();
        // The generator adds the scenario's later steering when the first response has ended.
        let steering = self.after_first.lock().unwrap().take();
        let control = Arc::clone(&self.control);
        let tail = stream::once(async move {
            for text in steering.unwrap_or_default() {
                control.add_steering(&text).await.unwrap();
            }
        })
        .filter_map(|()| async { None });
        Box::pin(stream::iter(items).chain(tail))
    }
    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }
}

/// The generator's `event_of`: kind, text, message id, evidence and response id.
struct Recorded(Vec<Json>);

impl SessionEvents for Recorded {
    fn emit(&mut self, event: ProviderEvent) -> BoxFuture<'_, Result<(), SessionProviderError>> {
        let (message_id, evidence) = match &event {
            ProviderEvent::Evidence { message_id, payload } => {
                (json!(message_id), Json::Object(payload.clone()))
            }
            _ => (Json::Null, Json::Null),
        };
        self.0.push(json!([event.kind().as_str(), event.text(), message_id, evidence, event.response_id()]));
        Box::pin(async { Ok(()) })
    }
}

fn context(spec: &Json) -> SessionContext {
    let session = &spec["session"];
    let text = |key: &str| session[key].as_str().map(str::to_owned);
    let status = match session["status"].as_str().unwrap() {
        "active" => SessionStatus::Active,
        "stopped" => SessionStatus::Stopped,
        other => panic!("status {other}"),
    };
    let record = SessionRecord::new(SessionRecordFields {
        session_id: text("session_id").unwrap(),
        campaign_id: text("campaign_id"),
        role: WriterRole::Builder,
        provider_id: text("provider_id").unwrap(),
        model_id: text("model_id").unwrap(),
        status,
        selected_skill: text("selected_skill"),
        ..Default::default()
    })
    .unwrap();
    let messages = spec["messages"]
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
        .map(|(index, row)| {
            SessionMessage::new(
                record.session_id(),
                index as i64 + 1,
                row[1].as_str().unwrap(),
                SessionMessageKind::parse(row[0].as_str().unwrap()).unwrap(),
                &row[2],
                None,
            )
            .unwrap()
        })
        .collect();
    SessionContext { session: record, messages }
}

fn mask(value: &Json, roots: &[String]) -> Json {
    let mut text = serde_json::to_string(value).unwrap();
    for root in roots {
        for form in [root.replace('\\', "\\\\\\\\"), root.replace('\\', "\\\\"), root.replace('\\', "/")] {
            text = text.replace(&form, "<ROOT>");
        }
    }
    pyjson::loads(&text).unwrap()
}

fn outcome(error: &SessionProviderError) -> Json {
    match &error.error {
        SessionFailure::Turn(TurnFailure::Coded(error)) => {
            json!([error.error_type.unwrap_or("CommandRejected"), error.message, error.code])
        }
        SessionFailure::Turn(TurnFailure::Provider(error)) => {
            json!([error.kind.class_name(), error.message, null])
        }
        SessionFailure::AgentLoop { message, .. } => json!(["AgentLoopError", message, null]),
    }
}

/// The first place two values differ, as a JSON pointer, with both values there.
fn first_difference(a: &Json, b: &Json, path: String) -> (String, Json, Json) {
    match (a, b) {
        (Json::Object(x), Json::Object(y)) => {
            for key in x.keys().chain(y.keys()) {
                if x.get(key) != y.get(key) {
                    let (left, right) = (
                        x.get(key).cloned().unwrap_or(Json::Null),
                        y.get(key).cloned().unwrap_or(Json::Null),
                    );
                    return first_difference(&left, &right, format!("{path}/{key}"));
                }
            }
            (path, a.clone(), b.clone())
        }
        (Json::Array(x), Json::Array(y)) => {
            for (index, (left, right)) in x.iter().zip(y).enumerate() {
                if left != right {
                    return first_difference(left, right, format!("{path}/{index}"));
                }
            }
            (format!("{path} (lengths {} and {})", x.len(), y.len()), json!(x.len()), json!(y.len()))
        }
        _ => (path, a.clone(), b.clone()),
    }
}

fn fresh_work(name: &str) -> std::path::PathBuf {
    let work = Path::new(env!("CARGO_TARGET_TMPDIR")).join(name);
    match std::fs::remove_dir_all(&work) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => panic!("cannot clear the old work folder {}: {error}", work.display()),
    }
    work
}

/// The generator's `Anything()` library reader: no scenario calls it.
struct NoLibrary;

impl LibrarySources for NoLibrary {
    fn candidates(&self, _query: &str) -> Result<Vec<JsonMap>, sylvia_domain::DomainError> {
        Ok(Vec::new())
    }
    fn files(&self, _candidate_id: &str) -> Result<JsonMap, sylvia_domain::DomainError> {
        Ok(JsonMap::new())
    }
    fn read(&self, _candidate_id: &str, _path: &str) -> Result<JsonMap, sylvia_domain::DomainError> {
        Ok(JsonMap::new())
    }
    fn record(&self, _candidate_id: &str, _prefix: &str) -> Result<JsonMap, sylvia_domain::DomainError> {
        Ok(JsonMap::new())
    }
}

/// The generator's `stub` Setting publisher.
fn setting_stand_in() -> SettingPublisher {
    Arc::new(|_: &str, _: &str, _: &str| Ok(JsonMap::new()))
}

/// The generator's recorded declarations.
fn declarations(value: &Json) -> Vec<ParticipantArtifactDeclaration> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .map(|fields| ParticipantArtifactDeclaration {
            kind: fields["kind"].as_str().unwrap().into(),
            schema_id: fields["schema_id"].as_str().unwrap().into(),
            schema_sha256: fields["schema_sha256"].as_str().unwrap().into(),
            schema: fields["schema"].as_object().cloned().unwrap(),
            entity_state_schema: fields["entity_state_schema"].as_object().cloned(),
            initial_state_calculations: fields["initial_state_calculations"]
                .as_array()
                .unwrap()
                .iter()
                .map(|item| item.as_object().cloned().unwrap())
                .collect(),
            initial_state_validation: fields["initial_state_validation"].as_object().cloned(),
        })
        .collect()
}

/// The generator's `stub` reader: answers an empty mapping.
fn stand_in_reader() -> sylvia_agent::builder::character_tools::ParticipantReader {
    Arc::new(|_: &str, _: &str, _: Option<&str>| Ok(JsonMap::new()))
}

#[test]
fn builder_turn_matches_python() {
    let vectors = vectors();
    let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
    let profile_names: Vec<String> = vectors["profile_tool_names"]
        .as_array()
        .unwrap()
        .iter()
        .map(|name| name.as_str().unwrap().to_owned())
        .collect();
    let mut failures = Vec::new();
    for scenario in vectors["scenarios"].as_array().unwrap() {
        let name = scenario["name"].as_str().unwrap();
        let spec = &scenario["spec"];
        let work = fresh_work(&format!("builder-turn-{name}"));
        std::fs::create_dir_all(&work).unwrap();
        let workspace = work.join("workspace");
        let roots = vec![
            sylvia_app::path_guardrail::PathGuardrail::new(&workspace).allowed_root().to_owned(),
            workspace.display().to_string(),
            sylvia_app::path_guardrail::PathGuardrail::new(&work).allowed_root().to_owned(),
            work.display().to_string(),
        ];
        let control = Arc::new(ProviderControl::new());
        let strings = |key: &str| -> Vec<String> {
            spec[key]
                .as_array()
                .map(|items| items.iter().map(|item| item.as_str().unwrap().to_owned()).collect())
                .unwrap_or_default()
        };
        let model = spec.get("model").map(|model| {
            let mut info = ModelInfo::new("model", "Model", "Provider", "provider", "byok");
            info.transport = model["transport"].as_str().unwrap().into();
            info
        });
        let provider = Arc::new(Scripted {
            model,
            responses: Mutex::new(scenario["script"].as_array().unwrap().iter().cloned().collect()),
            requests: Mutex::new(Vec::new()),
            definitions: Mutex::new(None),
            control: Arc::clone(&control),
            after_first: Mutex::new(spec.get("steer_after_first").map(|_| strings("steer_after_first"))),
        });
        let flags: Vec<String> = strings("tools");
        let has = |flag: &str| flags.iter().any(|item| item == flag);
        let tools = BuilderTurnTools {
            publish_ruleset_candidate: has("publish_ruleset_candidate"),
            campaign_content_authority: has("campaign_content").then(CampaignContent::default),
            library_sources: has("library_sources").then(|| Arc::new(NoLibrary) as Arc<dyn LibrarySources>),
            references: has("references").then(|| Arc::new(|| Ok(None)) as ReferenceSource),
            profile_tools: if has("profile_tools") {
                profile_names.iter().map(|name| exported_tool(name).unwrap()).collect()
            } else {
                Vec::new()
            },
            publish_campaign_setting: has("publish_campaign_setting").then(setting_stand_in),
            check_campaign_setting: has("check_campaign_setting").then(setting_stand_in),
            campaign_plan_authority: has("campaign_plan_authority"),
            check_campaign_plan: has("check_campaign_plan"),
            publish_campaign_plan: has("publish_campaign_plan"),
            participant_declarations: declarations(&spec["declarations"]),
            setting_character_schema: spec["setting_character_schema"].as_object().cloned(),
            participant_schemas: declarations(&spec["declarations"])
                .into_iter()
                .map(|declaration| (declaration.schema_id, declaration.schema))
                .collect(),
            plan_characters: spec.get("plan_declarations").map(|plan| (declarations(plan), None)),
            publish_participant: has("publish_participant").then(|| {
                Arc::new(|_: &ParticipantInput| Ok(JsonMap::new()))
                    as sylvia_agent::builder::character_tools::ParticipantPublisher
            }),
            reuse_participant: has("reuse_participant").then(stand_in_reader),
            read_participant: has("read_participant").then(stand_in_reader),
            resolve_source_profile: has("resolve_source_profile").then(|| {
                Arc::new(|_: Option<&Json>| Ok(None))
                    as sylvia_agent::builder::character_tools::SourceProfileResolver
            }),
            ..BuilderTurnTools::default()
        };
        let turn = BuilderTurn {
            message_id: "msg-1".into(),
            user_text: spec["user_text"].as_str().unwrap().into(),
            request_title: spec["request_title"].as_bool().unwrap_or(false),
            continuation_reader: (spec["reader"].as_str() == Some("empty")).then(|| {
                Arc::new(|_response_id: &str| None) as sylvia_agent::builder::turn::ContinuationReader
            }),
            tools,
            allow_public_web: spec["allow_public_web"].as_bool().unwrap_or(false),
            application_start: spec["application_start"].as_bool().unwrap_or(false),
            ids: Some({
                let counter = Arc::new(std::sync::atomic::AtomicU32::new(0));
                Arc::new(move || {
                    format!("{:032x}", counter.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1)
                })
            }),
        };
        let context = context(spec);
        let mut events = Recorded(Vec::new());
        let result = runtime.block_on(async {
            for text in strings("steer_before") {
                control.add_steering(&text).await.unwrap();
            }
            if spec["cancel"].as_bool() == Some(true) {
                control.cancel().unwrap();
            }
            stream_builder_turn(
                Arc::clone(&provider) as Arc<dyn Provider>,
                &context,
                &workspace,
                &control,
                &turn,
                &mut events,
            )
            .await
        });
        let (rust_result, rust_outcome) = match &result {
            Ok(result) => (
                json!({"tool_calls_made": result.tool_calls_made, "suggested_title": result.suggested_title}),
                json!(["ok"]),
            ),
            Err(error) => (Json::Null, outcome(error)),
        };
        let mut check = |what: &str, python: &Json, rust: Json| {
            if &rust != python {
                let (path, a, b) = first_difference(python, &rust, String::new());
                let (a, b) = (a.to_string(), b.to_string());
                failures.push(format!(
                    "{name} {what} at {path}: python {}, rust {}",
                    &a[..a.len().min(400)],
                    &b[..b.len().min(400)]
                ));
            }
        };
        check("outcome", &scenario["outcome"], rust_outcome);
        check("result", &scenario["result"], rust_result);
        check("events", &scenario["events"], mask(&Json::Array(events.0), &roots));
        let requests = provider.requests.lock().unwrap().clone();
        check("requests", &scenario["requests"], mask(&Json::Array(requests), &roots));
        let definitions = provider.definitions.lock().unwrap().clone().unwrap_or(Json::Null);
        check("definitions", &scenario["definitions"], mask(&definitions, &roots));
        let _ = std::fs::remove_dir_all(&work);
    }
    assert!(failures.is_empty(), "{} checks differ:\n{}", failures.len(), failures.join("\n"));
}
