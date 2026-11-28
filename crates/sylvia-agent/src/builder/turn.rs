//! `stream_builder_turn`, `_run_cancellable_stream`, `_streamed_builder_messages`,
//! `_steering_prompt`, `_builder_skill_loader_tool` and `persist_builder_turn` from
//! `agent/_builder_session.py`: one canonical Builder turn, streamed as session provider events.
//!
//! **The tool list** is assembled in Python's order from the chat's shape. Every tool's model
//! definition is Python's, exported into `builder_tool_specs.json` (or `sylvia-app`'s
//! `builder_tools.json`), so the model's request is the same. The handlers of the tools later
//! PRs port (B4 to B7) answer `rust_port_pending` in their tool result. A chat with character
//! declarations needs B5's character tools, whose definitions depend on the declarations, so
//! such a turn answers `rust_port_pending` before any request.
//!
//! **The stream bridge.** Python's bridge reads the runner's events in batches together with its
//! history, on the provider loop, each time the runner's task waits. Here the turn's future is
//! polled in place, and the events it queued are handled, with the history as it stands, each time
//! it waits and once more when it ends. A provider that answers without waiting gives one batch
//! for the whole response, as in Python.

use super::character_tools::{
    character_draft_tool, participant_artifact_tool, participant_draft_check_tool, participant_file_tool,
    reuse_character_copy_tool, ParticipantPublisher, ParticipantReader, SourceProfileResolver,
};
use super::content_definition::content_definition_tool;
use super::evidence::{builder_tool_evidence, tool_call_details};
use super::history::{builder_native_request, provider_history};
use super::mode::AgentMode;
use super::plan_tools::{
    campaign_plan_tools, creation_subflow_completion_tool, OwnerCheck, PlanAuthoritySource, PlanPublisher,
};
use super::prompt::{builder_prompt, creation_subflow_purpose, effective_tracked_skill};
use super::rejected;
use super::ruleset_tools::{ruleset_draft_check_tool, ruleset_test_tool, Cancelled};
use super::runner::{AgentRunner, RunnerOptions, StreamEvent, StreamEventKind, TurnError};
use super::skills::{builder_skill_references, load_builder_skill};
use super::source_tools::{
    builder_reference_copy_tool, builder_reference_tool, campaign_content_tools, campaign_setting_tool,
    library_source_copy_tool, library_source_tool, CampaignContent, LibrarySources, ReferenceSource,
    SettingPublisher,
};
use super::title::{title_instruction, FirstReplyTitle};
use super::tools::{ToolHandler, ToolSpec, ToolsetName};
use crate::native_continuation::requires_reasoning_with_tools;
use crate::session_loop::provider::{SessionEvents, SessionProviderError};
use crate::session_loop::{ProviderControl, ProviderEvent};
use crate::working_context::{history_tool, CheckpointBridge, WorkingContextOptions, WorkingContextProvider};
use indexmap::IndexMap;
use std::future::Future;
use std::path::Path;
use std::sync::{Arc, OnceLock};
use std::task::Poll;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, CodedError, CodedResult, Json, JsonMap};
use sylvia_domain::package_library::ParticipantArtifactDeclaration;
use sylvia_domain::session::{SessionContext, SessionMessageKind, SessionStatus, BUILDER_SKILLS};
use sylvia_provider::Provider;

/// `builder_tool_specs.json`'s `constant` definitions, read once.
fn specs() -> &'static JsonMap {
    static SPECS: OnceLock<JsonMap> = OnceLock::new();
    SPECS.get_or_init(|| match pyjson::loads(include_str!("builder_tool_specs.json")) {
        Ok(Json::Object(mut specs)) => match specs.shift_remove("constant") {
            Some(Json::Object(constant)) => constant,
            _ => panic!("builder_tool_specs.json has no constant definitions"),
        },
        _ => panic!("builder_tool_specs.json is exported JSON"),
    })
}

fn definition(name: &str) -> &'static Json {
    specs().get(name).unwrap_or_else(|| panic!("no exported definition for {name}"))
}

/// A Builder tool's exported definition (`builder_tool_specs.json`), for the modules that port
/// its handler.
pub(crate) fn exported_definition(name: &str) -> &'static Json {
    definition(name)
}

/// A tool whose handler a later PR ports: its exact definition, and `rust_port_pending`.
fn pending_tool(definition: &Json) -> CodedResult<ToolSpec> {
    let name = definition["function"]["name"].as_str().unwrap_or_default().to_owned();
    let handler: ToolHandler = Arc::new(move |_params: &JsonMap| {
        Ok(serde_json::json!({
            "success": false, "error_code": "rust_port_pending", "error": format!("not yet ported: {name}"),
        }))
    });
    ToolSpec::from_openai_schema(definition, handler, &[ToolsetName::BUILDER_TOOLS])
}

/// An exported tool, its handler not yet ported: what callers offer for the tools a later PR
/// ports (the character profile, import repair and source selection tools).
pub fn exported_tool(name: &str) -> CodedResult<ToolSpec> {
    pending(name)
}

fn pending(name: &str) -> CodedResult<ToolSpec> {
    pending_tool(definition(name))
}

/// `_builder_skill_loader_tool()`: a tracked skill's full instructions, only after Builder selects
/// one by name.
pub fn builder_skill_loader_tool() -> CodedResult<ToolSpec> {
    let handler: ToolHandler = Arc::new(|params: &JsonMap| {
        let refused = |code: &str| serde_json::json!({"success": false, "status": "rejected", "code": code});
        let keys_ok =
            params.contains_key("name") && params.keys().all(|key| key == "name" || key == "reference");
        let name = params.get("name").and_then(Json::as_str).filter(|name| BUILDER_SKILLS.contains(name));
        let reference_ok = !matches!(params.get("reference"), Some(value) if !value.is_string());
        // `params.get("name") not in BUILDER_SKILLS` hashes the name.
        if let Some(value @ (Json::Array(_) | Json::Object(_))) = params.get("name") {
            if keys_ok {
                let kind = if value.is_array() { "list" } else { "dict" };
                return Err(format!("unhashable type: '{kind}'"));
            }
        }
        let (true, Some(name), true) = (keys_ok, name, reference_ok) else {
            return Ok(refused("skill_unavailable"));
        };
        let reference = params.get("reference").and_then(Json::as_str);
        let Ok(loaded) = load_builder_skill(name, reference) else {
            return Ok(refused("skill_reference_unavailable"));
        };
        let mut result = JsonMap::new();
        result.insert("success".into(), Json::Bool(true));
        result.insert("status".into(), Json::from("loaded"));
        result.insert("name".into(), Json::from(name));
        result.insert("sha256".into(), Json::from(sha256_hex(loaded.text.as_bytes())));
        result.insert("instructions".into(), Json::from(loaded.text));
        let references = builder_skill_references(name).unwrap_or_default();
        result.insert("references".into(), serde_json::json!(references));
        if let Some(reference) = reference {
            result.insert("reference".into(), Json::from(reference));
        }
        Ok(Json::Object(result))
    });
    ToolSpec::from_openai_schema(definition("load_builder_skill"), handler, &[ToolsetName::BUILDER_TOOLS])
}

/// `continuation_reader(response_id)`: a retained native continuation.
pub type ContinuationReader = Arc<dyn Fn(&str) -> Option<JsonMap> + Send + Sync>;

/// Which of `stream_builder_turn`'s optional collaborators a chat has. The tools later PRs port
/// take their owner callbacks then; here a present collaborator offers its tools' definitions.
#[derive(Default, Clone)]
pub struct BuilderTurnTools {
    /// `participant_declarations`: the chat's character declarations.
    pub participant_declarations: Vec<ParticipantArtifactDeclaration>,
    /// `setting_character_schema`: the Setting's character data schema.
    pub setting_character_schema: Option<JsonMap>,
    /// The selected Plan's `character_declarations` and `setting_character_schema`
    /// (`campaign_plan_authority()`), used when the chat has no declarations of its own.
    pub plan_characters: Option<(Vec<ParticipantArtifactDeclaration>, Option<JsonMap>)>,
    /// `participant_schemas`: the schemas the publication tool offers, by schema ID.
    pub participant_schemas: IndexMap<String, JsonMap>,
    /// `resolve_source_profile`: verifies a draft check's named general profile.
    pub resolve_source_profile: Option<SourceProfileResolver>,
    pub publish_ruleset_candidate: bool,
    /// The worker `test_ruleset_draft` runs; this executable in its worker mode when `None`.
    pub ruleset_test_worker: Option<sylvia_app::ruleset_testing::WorkerCommand>,
    pub publish_participant: Option<ParticipantPublisher>,
    pub reuse_participant: Option<ParticipantReader>,
    pub read_participant: Option<ParticipantReader>,
    /// `campaign_plan_authority`: the Plan's exact closure, fixed or selected later.
    pub campaign_plan_authority: Option<PlanAuthoritySource>,
    pub publish_campaign_plan: Option<PlanPublisher>,
    pub publish_campaign_setting: Option<SettingPublisher>,
    pub check_campaign_setting: Option<SettingPublisher>,
    pub check_campaign_plan: Option<OwnerCheck>,
    pub repair_tools: Vec<ToolSpec>,
    pub profile_tools: Vec<ToolSpec>,
    pub campaign_content_authority: Option<CampaignContent>,
    pub references: Option<ReferenceSource>,
    pub source_tools: Vec<ToolSpec>,
    pub library_sources: Option<Arc<dyn LibrarySources>>,
}

/// One canonical Builder turn's inputs (`stream_builder_turn`'s keyword arguments).
#[derive(Clone)]
pub struct BuilderTurn {
    pub message_id: String,
    pub user_text: String,
    pub request_title: bool,
    pub continuation_reader: Option<ContinuationReader>,
    pub tools: BuilderTurnTools,
    pub allow_public_web: bool,
    pub application_start: bool,
    /// `uuid4().hex` for the turn's response and checkpoint identities, unless replaced.
    pub ids: Option<TurnIds>,
}

/// Where a turn's `uuid4().hex` values come from.
pub type TurnIds = Arc<dyn Fn() -> String + Send + Sync>;

/// `BuilderTurnResult`: what the turn prepared, held until the session loop terminalizes.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct BuilderTurnResult {
    pub tool_calls_made: Vec<String>,
    pub messages: Vec<(String, SessionMessageKind, JsonMap)>,
    pub suggested_title: Option<String>,
}

fn character_purpose(purpose: Option<&str>) -> bool {
    matches!(purpose, Some("player_character" | "companion"))
}

/// `stream_builder_turn`'s tool list, in Python's order, before the history tool. The Ruleset
/// draft tools work in `workspace`, and the test stops when `cancelled` says so.
pub fn application_tools(
    context: &SessionContext,
    tools: &BuilderTurnTools,
    workspace: &Path,
    cancelled: Cancelled,
) -> CodedResult<Vec<ToolSpec>> {
    let purpose = creation_subflow_purpose(context);
    let character = character_purpose(purpose.as_deref());
    let skill = effective_tracked_skill(context);
    // `character_declarations`: the chat's own, else the selected Plan's.
    let (declarations, world_schema) = match &tools.plan_characters {
        Some((plan, schema)) if tools.participant_declarations.is_empty() => (plan.clone(), schema.clone()),
        _ => (tools.participant_declarations.clone(), tools.setting_character_schema.clone()),
    };
    let mut specs = vec![content_definition_tool(&declarations, world_schema.clone())?];
    specs.extend(tools.repair_tools.iter().cloned());
    specs.extend(tools.source_tools.iter().cloned());
    if context.session.campaign_id().is_none() {
        specs.extend(tools.profile_tools.iter().cloned());
    }
    specs.push(builder_skill_loader_tool()?);
    let subflow_link = context.messages.iter().any(|message| {
        message.kind() == SessionMessageKind::Evidence
            && message.content().get("kind").and_then(Json::as_str) == Some("creation_subflow_link")
    });
    if context.session.campaign_id().is_some() && subflow_link {
        specs.push(creation_subflow_completion_tool()?);
    }
    if !declarations.is_empty() && (character || tools.campaign_plan_authority.is_some()) {
        let role = if character { purpose.as_deref() } else { None };
        let check = participant_draft_check_tool(
            role,
            &declarations,
            world_schema,
            tools.resolve_source_profile.clone(),
        )?;
        let file = if character { Some(participant_file_tool(&check, workspace)?) } else { None };
        specs.push(check);
        specs.extend(file);
    }
    if matches!(skill.as_deref(), None | Some("BUILD RULESET")) && !character {
        specs.push(ruleset_draft_check_tool(workspace)?);
        specs.push(ruleset_test_tool(workspace, cancelled, tools.ruleset_test_worker.clone())?);
    }
    let schema_ids: Vec<String> =
        tools.participant_declarations.iter().map(|declaration| declaration.schema_id.clone()).collect();
    if let (true, Some(read)) = (character, &tools.read_participant) {
        specs.push(character_draft_tool(Arc::clone(read), workspace, &schema_ids)?);
    }
    if let (true, Some(reuse)) = (character, &tools.reuse_participant) {
        specs.push(reuse_character_copy_tool(Arc::clone(reuse), &schema_ids)?);
    }
    if let (true, Some(publish)) = (character, &tools.publish_participant) {
        let tool = participant_artifact_tool(
            purpose.as_deref(),
            Arc::clone(publish),
            &tools.participant_schemas,
            false,
        )?;
        let file = participant_file_tool(&tool, workspace)?;
        specs.push(tool);
        specs.push(file);
    }
    if let Some(authority) = &tools.campaign_content_authority {
        specs.extend(campaign_content_tools(authority.clone())?);
    }
    if let Some(references) = &tools.references {
        specs.push(builder_reference_tool(Arc::clone(references))?);
        specs.push(builder_reference_copy_tool(Arc::clone(references), workspace)?);
    }
    if let (Some(library), None, false) = (&tools.library_sources, context.session.campaign_id(), character) {
        specs.push(library_source_tool(Arc::clone(library))?);
        specs.push(library_source_copy_tool(Arc::clone(library), workspace)?);
    }
    if let (true, false, Some(authority)) = (
        matches!(skill.as_deref(), None | Some("BUILD CAMPAIGN PLAN")),
        character,
        &tools.campaign_plan_authority,
    ) {
        specs.extend(campaign_plan_tools(
            workspace,
            authority.clone(),
            tools.publish_campaign_plan.clone(),
            tools.check_campaign_plan.clone(),
        )?);
    }
    let setting_skill = matches!(skill.as_deref(), None | Some("BUILD CAMPAIGN SETTING")) && !character;
    if let (true, Some(publisher)) = (setting_skill, &tools.publish_campaign_setting) {
        specs.push(campaign_setting_tool(Arc::clone(publisher), false)?);
    }
    if let (true, Some(checker)) = (setting_skill, &tools.check_campaign_setting) {
        specs.push(campaign_setting_tool(Arc::clone(checker), true)?);
    }
    let selected = context.session.fields().selected_skill.as_deref();
    if matches!(selected, None | Some("BUILD RULESET")) && tools.publish_ruleset_candidate {
        specs.push(pending("publish_ruleset_candidate")?);
    }
    Ok(specs)
}

/// `_steering_prompt(steering)`.
pub fn steering_prompt(steering: &[String]) -> String {
    let lines: Vec<String> = steering.iter().map(|text| format!("- {text}")).collect();
    format!(
        "The user sent steering while this Builder turn was active. Apply it to the work that follows:\n{}",
        lines.join("\n")
    )
}

/// `_streamed_builder_messages(message_id, visible, history_offset=..., call_history=...)`: a
/// stream batch projected; earlier batches may hold calls for later results.
pub fn streamed_builder_messages(
    message_id: &str,
    visible: &[JsonMap],
    history_offset: usize,
    call_history: &[JsonMap],
) -> CodedResult<Vec<(String, SessionMessageKind, JsonMap)>> {
    let calls = tool_call_details(call_history);
    let mut prepared = Vec::new();
    for (index, message) in visible.iter().enumerate() {
        let position = history_offset + index + 1;
        match message.get("role").and_then(Json::as_str) {
            Some("assistant") => {
                if let Some(Json::String(text)) =
                    message.get("content").filter(|content| content.as_str().is_some_and(|t| !t.is_empty()))
                {
                    let mut content = JsonMap::new();
                    content.insert("text".into(), Json::from(text.as_str()));
                    prepared.push((
                        format!("{message_id}-history-{position}-talk"),
                        SessionMessageKind::Talk,
                        content,
                    ));
                }
            }
            Some("tool") => {
                let evidence = builder_tool_evidence(message, &calls)?;
                prepared.push((
                    format!("{message_id}-history-{position}-tool"),
                    SessionMessageKind::Evidence,
                    evidence,
                ));
            }
            _ => {}
        }
    }
    Ok(prepared)
}

/// The application start signal's text (`_run_cancellable_stream`).
const APPLICATION_START: &str = "Begin this saved Builder conversation from its launch or import-repair context. Load the relevant skills and reference documents, continue the selected work, and ask the player about any missing meaningful choices.";

fn cancelled_error() -> SessionProviderError {
    rejected("Agent session provider work was cancelled").into()
}

fn turn_error(error: TurnError) -> SessionProviderError {
    match error {
        TurnError::Loop(error) => SessionProviderError::caused_by_loop(
            crate::provider_turn::TurnFailure::Coded(rejected(error.user_message)),
            error.reason,
        ),
        TurnError::Provider(error) => error.into(),
        TurnError::Coded(error) => error.into(),
    }
}

/// The bridge's state across one stream (`_run_cancellable_stream`'s locals).
struct Bridge<'a> {
    message_id: &'a str,
    control: &'a ProviderControl,
    cursor: &'a mut usize,
    title: Option<&'a mut FirstReplyTitle>,
    checkpoints: &'a CheckpointBridge,
    tool_calls_made: Vec<String>,
    last_response: Option<String>,
}

impl Bridge<'_> {
    async fn evidence(
        &mut self,
        snapshot: &[JsonMap],
        events: &mut dyn SessionEvents,
    ) -> Result<(), SessionProviderError> {
        let start = (*self.cursor).min(snapshot.len());
        for (id, kind, content) in
            streamed_builder_messages(self.message_id, &snapshot[start..], start, snapshot)?
        {
            if kind == SessionMessageKind::Evidence {
                events.emit(ProviderEvent::Evidence { message_id: id, payload: content }).await?;
            }
        }
        *self.cursor = snapshot.len();
        Ok(())
    }

    /// One batch: the queued events with the history as it stood when they were read.
    async fn batch(
        &mut self,
        queued: Vec<StreamEvent>,
        snapshot: Vec<JsonMap>,
        writes: Vec<crate::working_context::CheckpointWrite>,
        events: &mut dyn SessionEvents,
    ) -> Result<(), SessionProviderError> {
        for event in queued {
            if self.control.cancelled()
                && !matches!(
                    event.kind,
                    StreamEventKind::ToolCall
                        | StreamEventKind::Done
                        | StreamEventKind::Reasoning
                        | StreamEventKind::Continuation
                )
            {
                continue;
            }
            let response_id = || event.meta.as_ref().and_then(|meta| meta.get("response_id")).cloned();
            match event.kind {
                StreamEventKind::Content if !event.data.is_empty() => {
                    self.last_response = response_id().and_then(|id| id.as_str().map(str::to_owned));
                    let text = match self.title.as_deref_mut() {
                        Some(title) => title.feed(&event.data, false),
                        None => event.data.clone(),
                    };
                    if !text.is_empty() {
                        events
                            .emit(ProviderEvent::Talk {
                                text,
                                response_blocks: None,
                                response_id: self.last_response.clone(),
                            })
                            .await?;
                    }
                }
                StreamEventKind::Reasoning if !event.data.is_empty() => {
                    let response_id = response_id().and_then(|id| id.as_str().map(str::to_owned));
                    events
                        .emit(ProviderEvent::PrivateReasoning { text: event.data.clone(), response_id })
                        .await?;
                }
                StreamEventKind::Continuation => {
                    let response_id = response_id();
                    let payload = pyjson::loads(&event.data).ok();
                    let (Some(Json::String(id)), Some(Json::Object(payload))) = (&response_id, &payload)
                    else {
                        return Err(rejected("Builder continuation identity is unavailable").into());
                    };
                    if id.is_empty() {
                        return Err(rejected("Builder continuation identity is unavailable").into());
                    }
                    events.emit(ProviderEvent::private_continuation(payload, id)?).await?;
                }
                StreamEventKind::Done => {
                    if let Some(Json::Array(names)) =
                        event.meta.as_ref().and_then(|meta| meta.get("tool_calls_made"))
                    {
                        self.tool_calls_made = names.iter().map(sylvia_domain::pyops::py_str).collect();
                    }
                }
                StreamEventKind::Error => return Err(rejected("Builder provider streaming failed").into()),
                _ => {}
            }
            if matches!(event.kind, StreamEventKind::ToolCall | StreamEventKind::Done) {
                if !self.control.cancelled() {
                    if let Some(title) = self.title.as_deref_mut() {
                        let tail = title.feed("", true);
                        if !tail.is_empty() {
                            let response_id = self.last_response.clone();
                            events
                                .emit(ProviderEvent::Talk { text: tail, response_blocks: None, response_id })
                                .await?;
                        }
                    }
                    events.emit(ProviderEvent::ProseComplete).await?;
                }
                self.evidence(&snapshot, events).await?;
            }
        }
        if self.control.cancelled() {
            return Err(cancelled_error());
        }
        let writes =
            writes.into_iter().chain(std::iter::from_fn(|| self.checkpoints.take())).collect::<Vec<_>>();
        for write in writes {
            if write.accepted.is_closed() {
                continue;
            }
            // Flush completed tool receipts before committing their continuation note.
            events.emit(ProviderEvent::ProseComplete).await?;
            self.evidence(&snapshot, events).await?;
            let checkpoint =
                write.payload.get("checkpoint_id").map(sylvia_domain::pyops::py_str).unwrap_or_default();
            events
                .emit(ProviderEvent::Evidence {
                    message_id: format!("context-{checkpoint}"),
                    payload: write.payload.clone(),
                })
                .await?;
            let _ = write.accepted.send(());
        }
        Ok(())
    }
}

/// `_run_cancellable_stream(runner, user_text, control, ...)`: the runner's stream bridged into
/// session provider events. Returns the tool names the pass made.
#[allow(clippy::too_many_arguments)]
async fn run_stream(
    runner: &mut AgentRunner,
    user_text: &str,
    control: &ProviderControl,
    message_id: &str,
    cursor: &mut usize,
    application_start: bool,
    checkpoints: &CheckpointBridge,
    title: Option<&mut FirstReplyTitle>,
    rejection: &Rejection,
    events: &mut dyn SessionEvents,
) -> Result<Vec<String>, SessionProviderError> {
    // `_builder_task` admits the work on the control first.
    control.admit(|| ())?;
    let history = runner.shared_history();
    let (sender, mut receiver) = tokio::sync::mpsc::unbounded_channel();
    let mut steering = || {
        Box::pin(async move {
            let steering = control.take_steering().await;
            (!steering.is_empty()).then(|| steering_prompt(&steering))
        }) as std::pin::Pin<Box<dyn std::future::Future<Output = Option<String>> + Send + '_>>
    };
    let prompt = application_start.then_some(APPLICATION_START);
    let mut bridge = Bridge {
        message_id,
        control,
        cursor,
        title,
        checkpoints,
        tool_calls_made: Vec::new(),
        last_response: None,
    };
    let mut turn = std::pin::pin!(runner.turn_stream(user_text, &sender, Some(&mut steering), prompt));
    let outcome = loop {
        // Poll the turn once: it runs until it waits or ends.
        // A checkpoint write waits for its acknowledgement, so it is taken here, while the turn
        // waits, rather than left for a wake-up that would never come.
        let mut writes = Vec::new();
        let step = std::future::poll_fn(|cx| match turn.as_mut().poll(cx) {
            Poll::Ready(result) => Poll::Ready(Some(result)),
            Poll::Pending => {
                while let Some(write) = checkpoints.take() {
                    writes.push(write);
                }
                if receiver.is_empty() && writes.is_empty() {
                    Poll::Pending
                } else {
                    Poll::Ready(None)
                }
            }
        })
        .await;
        let mut queued = Vec::new();
        while let Ok(event) = receiver.try_recv() {
            queued.push(event);
        }
        let snapshot = history.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone();
        bridge.batch(queued, snapshot, writes, events).await?;
        if let Some(result) = step {
            break result;
        }
    };
    if control.cancelled() {
        return Err(cancelled_error());
    }
    if let Err(error) = outcome {
        // A refusal `prepare_continuation` stored is raised as itself (Python's
        // `CommandRejected` passes through the working context unchanged).
        if let Some(refusal) = rejection.lock().unwrap_or_else(std::sync::PoisonError::into_inner).take() {
            return Err(refusal.into());
        }
        return Err(turn_error(error));
    }
    Ok(bridge.tool_calls_made)
}

/// A coded refusal from `prepare_continuation`: the working context sees an `Internal` provider
/// error, and the turn's consumer raises the stored refusal instead (lane 4's pattern for the GM's
/// `_attach_native_continuation`).
type Rejection = Arc<std::sync::Mutex<Option<CodedError>>>;

/// `stream_builder_turn(provider, context=..., message_id=..., ...)`: one canonical Builder
/// turn streamed as session provider events, its result prepared at completion.
pub async fn stream_builder_turn(
    provider: Arc<dyn Provider>,
    context: &SessionContext,
    workspace: &Path,
    control: &Arc<ProviderControl>,
    turn: &BuilderTurn,
    events: &mut dyn SessionEvents,
) -> Result<BuilderTurnResult, SessionProviderError> {
    if context.session.fields().status != SessionStatus::Active {
        return Err(rejected("Agent session is not active").into());
    }
    if context.session.role() != sylvia_domain::authority::WriterRole::Builder {
        return Err(rejected("Full Builder tools require a Builder session").into());
    }
    std::fs::create_dir_all(workspace)
        .map_err(|error| CodedError::rejected(error.to_string()).with_error_type("OSError"))?;
    let cancelled: Cancelled = {
        let control = Arc::clone(control);
        Arc::new(move || control.cancelled())
    };
    let mut tools = application_tools(context, &turn.tools, workspace, cancelled)?;
    let mut title = turn.request_title.then(FirstReplyTitle::new);
    let checkpoints = Arc::new(CheckpointBridge::new());
    let model = provider.model_info().cloned();
    let transport = model.as_ref().map(|model| model.transport.clone());
    let reader = turn.continuation_reader.clone();
    let identity = (context.session.fields().provider_id.clone(), context.session.fields().model_id.clone());
    let mut options = WorkingContextOptions::new({
        let checkpoints = Arc::clone(&checkpoints);
        Arc::new(move |payload: JsonMap| {
            let checkpoints = Arc::clone(&checkpoints);
            Box::pin(async move { checkpoints.save(payload).await })
        })
    });
    options.records = context.messages.iter().map(|message| message.content().clone()).collect();
    options.cancelled = {
        let control = Arc::clone(control);
        Arc::new(move || control.cancelled())
    };
    options.progress = {
        let control = Arc::clone(control);
        Arc::new(move |text: Option<&str>| {
            let _ = control.set_status_hint(text);
        })
    };
    let rejection: Rejection = Arc::new(std::sync::Mutex::new(None));
    let stored = Arc::clone(&rejection);
    options.prepare_continuation = Some(Arc::new(move |selected: Vec<JsonMap>, has_tools: bool| {
        let resend = requires_reasoning_with_tools(model.as_ref(), has_tools);
        let read = reader.clone();
        let reader_fn = move |id: &str| read.as_ref().and_then(|read| read(id));
        let prepared = builder_native_request(
            &selected,
            transport.as_deref(),
            Some(&reader_fn),
            resend,
            Some((identity.0.as_str(), identity.1.as_str())),
        );
        let prepared = prepared.map_err(|error| {
            let internal = sylvia_provider::ProviderError::new(
                sylvia_provider::error::ErrorKind::Internal,
                error.message.clone(),
            );
            *stored.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = Some(error);
            internal
        });
        Box::pin(async move { prepared })
    }));
    if let Some(ids) = &turn.ids {
        options.checkpoint_id = Arc::clone(ids);
    }
    let working = Arc::new(WorkingContextProvider::new(provider, options));
    let reading = Arc::clone(&working);
    let history_handler: ToolHandler =
        Arc::new(move |params: &JsonMap| Ok(Json::Object(reading.read_history(params))));
    tools.push(ToolSpec::from_openai_schema(
        &history_tool(),
        history_handler,
        &[ToolsetName::BUILDER_TOOLS],
    )?);
    let progress = {
        let control = Arc::clone(control);
        Arc::new(move |phase: &str, tool: Option<&str>| {
            let _ = control.progress.update(phase, tool);
        })
    };
    let mut prompt = builder_prompt(context)?;
    if turn.request_title {
        prompt.push_str(title_instruction());
    }
    let mut runner = AgentRunner::new(
        AgentMode::BuilderFreeform,
        Arc::clone(&working) as Arc<dyn Provider>,
        provider_history(context, Some(&turn.message_id))?,
        workspace,
        RunnerOptions {
            ruleset_name: Some("campaign".into()),
            system_prompt_override: Some(prompt),
            prompt_mode: "replace".into(),
            trusted_system_override: true,
            builder_application_tools: tools,
            allow_public_web: turn.allow_public_web,
            activity_progress: Some(progress),
            // Python's working context keeps the runner's own message list, so each message the
            // turn appends after a request is part of what `read_session_history` reads.
            outgoing: Some({
                let source = Arc::clone(&working);
                Arc::new(move |message: &JsonMap| source.extend_source(std::slice::from_ref(message)))
            }),
            ..RunnerOptions::default()
        },
    )?;
    if let Some(ids) = turn.ids.clone() {
        runner.set_response_ids(Box::new(move || ids()));
    }
    let mut cursor = runner.history_snapshot().len();
    let mut next_input = turn.user_text.clone();
    let mut tool_calls_made = Vec::new();
    let mut first_pass = true;
    loop {
        let round = run_stream(
            &mut runner,
            &next_input,
            control,
            &turn.message_id,
            &mut cursor,
            turn.application_start && first_pass,
            &checkpoints,
            title.as_mut(),
            &rejection,
            events,
        )
        .await?;
        tool_calls_made.extend(round);
        if first_pass && turn.allow_public_web {
            runner.revoke_public_web();
        }
        first_pass = false;
        let steering = control.take_steering_or_close().await;
        if steering.is_empty() {
            break;
        }
        next_input = steering_prompt(&steering);
    }
    Ok(BuilderTurnResult {
        tool_calls_made,
        messages: Vec::new(),
        suggested_title: title.and_then(|title| title.title),
    })
}

/// The application-owner operations the Builder uses. B3b adds the rest of what the submit
/// handler's Builder branch calls; lane 5 implements it in `sylvia-owner` over the store.
pub trait BuilderOwner: Send + Sync {
    /// `append_agent_message(session_id=..., message_id=..., kind=..., content=...)`.
    fn append_agent_message<'a>(
        &'a self,
        session_id: &'a str,
        message_id: &'a str,
        kind: SessionMessageKind,
        content: &'a JsonMap,
    ) -> sylvia_provider::BoxFuture<'a, CodedResult<sylvia_domain::session::SessionMessage>>;
}

/// `persist_builder_turn(owner, session_id=..., result=...)`: the prepared outputs appended; the
/// authority refuses every write after the session's Stop.
pub async fn persist_builder_turn(
    owner: &dyn BuilderOwner,
    session_id: &str,
    result: &BuilderTurnResult,
) -> CodedResult<Vec<sylvia_domain::session::SessionMessage>> {
    let mut appended = Vec::new();
    for (message_id, kind, content) in &result.messages {
        appended.push(owner.append_agent_message(session_id, message_id, *kind, content).await?);
    }
    Ok(appended)
}

/// The Builder as a session provider: `builder_provider(provider_input)` in the submit handler,
/// one `stream_builder_turn` over the loop's history and control, its result kept for persist.
pub struct BuilderSessionProvider {
    pub provider: Arc<dyn Provider>,
    pub workspace: std::path::PathBuf,
    pub turn: BuilderTurn,
    /// `complete(result)`: set when the turn ends.
    pub result: Arc<std::sync::Mutex<Option<BuilderTurnResult>>>,
}

impl crate::session_loop::provider::SessionProvider for BuilderSessionProvider {
    fn run<'a>(
        &'a self,
        input: crate::session_loop::SessionProviderInput,
        events: &'a mut dyn SessionEvents,
    ) -> sylvia_provider::BoxFuture<'a, Result<(), SessionProviderError>> {
        Box::pin(async move {
            let result = stream_builder_turn(
                Arc::clone(&self.provider),
                &input.history,
                &self.workspace,
                &input.control,
                &self.turn,
                events,
            )
            .await?;
            *self.result.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = Some(result);
            Ok(())
        })
    }
}
