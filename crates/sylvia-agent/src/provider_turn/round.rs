//! One provider round's preparation, from `agent/_provider_turn_phases.py`:
//! `prepare_round_catalog` and `prepare_provider_round`, with the `_provider_session.py` helpers
//! they call (`_require_checked_campaign_file_narration`, `_current_combat_state`).
//!
//! The round's request itself (`stream_provider_round`, `_stream_round` and the context-ledger
//! seam) is T2c.

use super::decisions as d;
use super::state::{ProviderTurnState, ToolCallAssembly};
use crate::provider_session::context::{py_str, ProviderSessionAdapter, SessionProviderInput};
use crate::provider_session::control_texts::engine_turn_control_message;
use crate::provider_session::tagged::Tagged;
use crate::provider_session::tools::{provider_tools, ToolAliases, CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT};
use crate::stateless_mcp::{action_sequence_model_tools, ModelMechanicsTool};
use indexmap::{IndexMap, IndexSet};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_domain::authority::{ModelDecisionOwner, ModelToolBehavior, WriterRole};

fn coded(code: &str, message: &str) -> CodedError {
    CodedError::new(code, message).with_error_type("CommandRejected")
}

fn cancelled() -> CodedError {
    CodedError::rejected("Agent session provider work was cancelled").with_error_type("CommandRejected")
}

/// `_current_combat_state(state)`: the one Ruleset-owned combat projection of an authority
/// snapshot's state, else empty.
pub(crate) fn current_combat_state(state: &JsonMap) -> JsonMap {
    state
        .iter()
        .find_map(|(key, value)| (key.ends_with(".combat")).then(|| value.as_object().cloned()).flatten())
        .unwrap_or_default()
}

/// `_require_checked_campaign_file_narration(tools, aliases, narration_held=...)`: the GM's one
/// settled response becomes part of its final Campaign File decision.
pub(crate) fn require_checked_campaign_file_narration(
    tools: &mut [Json],
    aliases: &ToolAliases,
    narration_held: bool,
) {
    for provider_tool in tools.iter_mut() {
        let Some(Json::Object(function)) = provider_tool.get_mut("function") else { continue };
        let catalog_tool = function.get("name").and_then(Json::as_str).and_then(|alias| aliases.get(alias));
        let Some(catalog_tool) = catalog_tool else { continue };
        if catalog_tool.category != "narrative_state"
            || catalog_tool.capability != "narrative_state.update_campaign_file"
        {
            continue;
        }
        let Some(Json::Object(parameters)) = function.get_mut("parameters") else { continue };
        let required = parameters.entry("required").or_insert_with(|| Json::Array(Vec::new()));
        if let Json::Array(required) = required {
            required.retain(|name| name.as_str() != Some(CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT));
            if !narration_held {
                required.push(CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT.into());
            }
        }
    }
}

fn is_campaign_file_update(tool: &ModelMechanicsTool) -> bool {
    tool.category == "narrative_state" && tool.capability == "narrative_state.update_campaign_file"
}

/// Python truthiness of a JSON value.
fn truthy(value: Option<&Json>) -> bool {
    match value {
        None | Some(Json::Null) => false,
        Some(Json::Bool(flag)) => *flag,
        Some(Json::Number(number)) => number.as_f64() != Some(0.0),
        Some(Json::String(text)) => !text.is_empty(),
        Some(Json::Array(items)) => !items.is_empty(),
        Some(Json::Object(map)) => !map.is_empty(),
    }
}

/// `prepare_round_catalog(self, state, provider_input, turn_id=...)`: the round's identities and
/// current catalog, and whether an engine transition must be reviewed first.
pub async fn prepare_round_catalog(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
    _turn_id: &str,
) -> CodedResult<()> {
    if input.control.cancelled() {
        return Err(cancelled());
    }
    let owner = adapter.turn_owner()?;
    state.locals.inference_id.set(format!("inference-{}", adapter.uuid_hex()));
    state.locals.discovery_id.set(format!("mcp-discovery-{}", adapter.uuid_hex()));
    let discovery_id = state.locals.discovery_id.value()?;
    state.locals.current_catalog.set(input.current_catalog(&discovery_id).await?);
    if input.session.role() == WriterRole::Dm {
        let queue_tools = action_sequence_model_tools();
        if !queue_tools.is_empty() {
            let campaign_id = state.locals.campaign_id.value()?;
            if !owner.snapshot(&campaign_id).await?.state.package_lock.entries().is_empty() {
                let mut added = Vec::new();
                for tool in queue_tools {
                    if !*state.progress.sequence_execution_stopped.get()? || tool.category == "reference" {
                        added.push(tool);
                    }
                }
                state.locals.current_catalog.get_mut()?.extend(added);
            }
        }
    }
    if *state.locals.participant_ooc_only.get()? {
        state
            .locals
            .current_catalog
            .get_mut()?
            .retain(|tool| matches!(tool.category.as_str(), "reference" | "narrative_state"));
    }
    if input.control.cancelled() {
        return Err(cancelled());
    }
    let required: Vec<ModelMechanicsTool> = state
        .locals
        .current_catalog
        .get()?
        .iter()
        .filter(|tool| tool.model_behavior == ModelToolBehavior::RequiredEngineTransition)
        .cloned()
        .collect();
    state.locals.required_transitions.set(required);
    if !state.locals.required_transitions.get()?.is_empty()
        && *state.locals.provider_round.get()? == 0
        && state.locals.current_player_message.get()?.is_some()
        && state.locals.current_catalog.get()?.iter().any(|tool| tool.name == "end_combat_encounter")
    {
        let campaign_id = state.locals.campaign_id.value()?;
        let combat = current_combat_state(&owner.snapshot(&campaign_id).await?.state.state);
        // A new instruction can end the fight at the just-resolved NPC action. Read it before
        // finishing that turn or advancing to a Death Save.
        state.obligations.engine_transition_review_pending.set(
            combat.get("turn_active") == Some(&Json::Bool(true))
                && combat.get("active_actor_kind").and_then(Json::as_str) == Some("monster")
                && combat.get("action_available") == Some(&Json::Bool(false)),
        );
    }
    Ok(())
}

/// The provider tools for `catalog`, assigned to the round (`state.tools, state.aliases = ...`).
fn assign_tools(state: &mut ProviderTurnState, catalog: &[ModelMechanicsTool]) -> CodedResult<()> {
    let (tools, aliases) = provider_tools(catalog)?;
    state.locals.tools.set(tools.into_iter().filter_map(|tool| tool.as_object().cloned()).collect());
    state.locals.aliases.set(aliases);
    Ok(())
}

/// `prepare_provider_round(self, state, provider_input, turn_id=...)`: the round's tool list and
/// decision scope, the refreshed campaign state, the reset round locals and the audit context.
pub async fn prepare_provider_round(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
    turn_id: &str,
) -> CodedResult<()> {
    let owner = adapter.turn_owner()?;
    let mut model_catalog: Vec<ModelMechanicsTool> = state.locals.current_catalog.value()?;
    let opening_update_required = d::opening_needs_campaign_file_update(state)?;
    let supported = state
        .locals
        .current_catalog
        .get()?
        .iter()
        .any(|tool| is_campaign_file_update(tool) && !truthy(tool.input_schema.get("minProperties")));
    state.locals.campaign_file_disposition_supported.set(supported);
    // Keep every valid next operation available, but do not let a committed Ruleset result skip
    // the Campaign File checkpoint by ending in prose.
    let post_commit = d::committed_work_needs_campaign_file_disposition(state)?;
    state.locals.post_commit_tool_required.set(post_commit);
    if d::campaign_file_needs_narration(state)? {
        model_catalog = Vec::new();
    } else if d::campaign_file_must_be_settled(state)? {
        model_catalog = state
            .locals
            .current_catalog
            .get()?
            .iter()
            .filter(|tool| is_campaign_file_update(tool))
            .cloned()
            .collect();
        if model_catalog.is_empty() {
            return Err(coded(
                "campaign_file_update_unavailable",
                "Campaign File disposition operation became unavailable",
            ));
        }
    } else if opening_update_required && !*state.locals.campaign_file_disposition_supported.get()? {
        return Err(coded(
            "campaign_file_update_unavailable",
            "Campaign opening requires the Campaign File update operation",
        ));
    }
    assign_tools(state, &model_catalog)?;
    state.locals.decision_boundary.set(None);
    let campaign_id = state.locals.campaign_id.value()?;
    if !*state.locals.participant_ooc_only.get()? {
        let boundary = if d::exchange_has_committed_work(state)? {
            owner.model_decision_boundary(&campaign_id).await?
        } else {
            owner.campaign_preparation_boundary(&campaign_id).await?
        };
        state.locals.decision_boundary.set(boundary);
    }
    if d::saved_gm_boundary_should_be_read(state)? {
        // An already active private game must resume its GM decision before narration.
        let (_, private_boundary) = owner.model_context_snapshot(&campaign_id).await?;
        if let Some(private_boundary) =
            private_boundary.filter(|boundary| boundary.owner == ModelDecisionOwner::Gm)
        {
            state.locals.decision_boundary.set(Some(private_boundary));
        }
    }
    state.decisions.player_boundary_narration.set(false);
    state.obligations.player_intent_resolution_required.set(false);
    state.decisions.gm_boundary_decision.set(false);
    if let Some(boundary) = state.locals.decision_boundary.value()? {
        let mut record = JsonMap::new();
        record.insert("kind".into(), "model_decision_boundary".into());
        record.insert("campaign_id".into(), campaign_id.as_str().into());
        record.insert("session_id".into(), input.session.session_id().into());
        record.insert("turn_id".into(), turn_id.into());
        record.insert("mcp_discovery_id".into(), state.locals.discovery_id.value()?.into());
        record.insert("owner".into(), boundary.owner.as_str().into());
        let text = |value: &Option<String>| value.as_deref().map_or(Json::Null, Json::from);
        record.insert("actor_id".into(), text(&boundary.actor_id));
        record.insert("actor_name".into(), text(&boundary.actor_name));
        record.insert("active_side_id".into(), text(&boundary.active_side_id));
        record.insert("focused_actor_id".into(), text(&boundary.focused_actor_id));
        record.insert(
            "eligible_actor_ids".into(),
            Json::Array(
                boundary.eligible_actors.iter().map(|actor| actor.actor_id.as_str().into()).collect(),
            ),
        );
        record.insert("campaign_revision".into(), boundary.campaign_revision.into());
        owner.record_admin_full_audit(record)?;
        if let Some(preparation) = &boundary.preparation {
            let key = |name: &str| {
                preparation.get(name).cloned().ok_or_else(|| {
                    CodedError::new("internal_error", format!("'{name}'")).with_error_type("KeyError")
                })
            };
            let mut selected = Vec::new();
            for tool in state.locals.current_catalog.get()? {
                if matches!(tool.category.as_str(), "reference" | "narrative_state")
                    || (key("package_id")? == tool.package_id.as_str()
                        && key("intent")? == tool.name.as_str())
                {
                    selected.push(tool.clone());
                }
            }
            assign_tools(state, &selected)?;
        } else if boundary.owner == ModelDecisionOwner::Player {
            state.decisions.player_boundary_narration.set(true);
            if d::player_intent_remains_pending(state)? {
                state.obligations.player_intent_resolution_required.set(true);
                state.obligations.force_campaign_file_update.set(false);
                let selected: Vec<ModelMechanicsTool> = state
                    .locals
                    .current_catalog
                    .get()?
                    .iter()
                    .filter(|tool| {
                        tool.category == "reference"
                            || matches!(
                                tool.model_behavior,
                                ModelToolBehavior::ResolvesPlayerIntent
                                    | ModelToolBehavior::SupportsPlayerIntent
                                    | ModelToolBehavior::LeavesPlayerIntentPending
                            )
                    })
                    .cloned()
                    .collect();
                if !selected.iter().any(|tool| tool.model_behavior == ModelToolBehavior::ResolvesPlayerIntent)
                {
                    return Err(coded(
                        "player_intent_resolution_unavailable",
                        "Ruleset left player intent pending without exposing a resolver",
                    ));
                }
                assign_tools(state, &selected)?;
            }
        } else {
            state.decisions.gm_boundary_decision.set(true);
            // A Campaign File disposition is terminal for this provider exchange. Keep it
            // unavailable until Ruleset control reaches the player, or prose can strand an
            // active GM-controlled combat turn.
            state.obligations.force_campaign_file_update.set(false);
            let selected: Vec<ModelMechanicsTool> = state
                .locals
                .current_catalog
                .get()?
                .iter()
                .filter(|tool| tool.category != "narrative_state")
                .cloned()
                .collect();
            assign_tools(state, &selected)?;
        }
    }
    let narration_held = state.locals.buffered_narration.get()?.is_some()
        || state.locals.buffered_raw_response_blocks.get()?.is_some();
    {
        let aliases = state.locals.aliases.value()?;
        let tools = state.locals.tools.get_mut()?;
        let mut as_json: Vec<Json> = tools.drain(..).map(Json::Object).collect();
        require_checked_campaign_file_narration(&mut as_json, &aliases, narration_held);
        tools.extend(as_json.into_iter().filter_map(|tool| match tool {
            Json::Object(map) => Some(map),
            _ => None,
        }));
    }
    let schemas: IndexMap<String, JsonMap> = state
        .locals
        .tools
        .get()?
        .iter()
        .filter_map(|tool| {
            let function = tool.get("function")?.as_object()?;
            let name = function.get("name")?.as_str()?;
            let parameters = function.get("parameters")?.as_object()?;
            Some((name.to_owned(), parameters.clone()))
        })
        .collect();
    state.locals.provider_tool_schemas.set(schemas);
    let opening_request = *state.locals.opening_request.get()?;
    let (_, refreshed) = adapter.campaign_context_messages(input, opening_request, adapter.ranker()?).await?;
    replace_fresh_state(state.locals.messages.get_mut()?, refreshed);
    state.locals.assemblies.set(IndexMap::<i64, ToolCallAssembly>::new());
    state.locals.mechanics_action_ids.set(IndexMap::new());
    state.locals.mechanics_parameters.set(IndexMap::new());
    state.locals.mechanics_tool_names.set(IndexMap::new());
    state.locals.mechanics_tool_behaviors.set(IndexMap::new());
    state.locals.mechanics_preparation_scopes.set(IndexMap::new());
    state.locals.mechanics_pre_entities.set(IndexMap::new());
    state.locals.local_tool_results.set(IndexMap::new());
    state.locals.local_tool_failures.set(IndexSet::new());
    state.locals.sequence_narration_facts.set(Vec::new());
    state.locals.assistant_text.set(Vec::new());
    state.locals.assistant_reasoning.set(Vec::new());
    state.locals.native_assistant_content.set(None);
    state.locals.native_chat_metadata.set(None);
    state.locals.round_finish_reason.set(None);
    *state.locals.provider_round.get_mut()? += 1;
    let mut audit = JsonMap::new();
    audit.insert("inference_id".into(), state.locals.inference_id.value()?.into());
    audit.insert("mcp_discovery_id".into(), state.locals.discovery_id.value()?.into());
    audit.insert("campaign_id".into(), campaign_id.into());
    audit.insert("turn_id".into(), turn_id.into());
    audit.insert("round".into(), (*state.locals.provider_round.get()?).into());
    audit.insert("context_id".into(), adapter.gm_request_context_id(input).await?.into());
    adapter.provider().set_audit_context(&audit);
    state.locals.exchange_messages.set(());
    let inference = state.locals.messages.value()?;
    state.locals.inference_messages.set(inference);
    if d::engine_transition_needs_review(state)? {
        if let Some(control) = engine_turn_control_message(state.locals.aliases.get()?) {
            state.locals.inference_messages.get_mut()?.push(Tagged::plain(control));
        }
    }
    Ok(())
}

/// The fresh campaign state record of `messages` replaced by `refreshed`: the first system or
/// fresh-data message whose content starts `SYLVIA_GM_FRESH_CAMPAIGN_STATE `.
fn replace_fresh_state(messages: &mut [Tagged], refreshed: Tagged) {
    let found = messages.iter().position(|message| {
        let message = &message.message;
        let owned = message.get("role").and_then(Json::as_str) == Some("system")
            || truthy(message.get("_fresh_application_data"));
        owned
            && message
                .get("content")
                .map_or_else(String::new, py_str)
                .starts_with("SYLVIA_GM_FRESH_CAMPAIGN_STATE ")
    });
    if let Some(index) = found {
        messages[index] = refreshed;
    }
}

/// `stream_provider_round(self, state, provider_input, turn_id=...)`: the round's controls,
/// its request through the session's working context, and what the stream assembled; private
/// reasoning, the native continuation and a normalized forced call are emitted as events.
pub async fn stream_provider_round(
    adapter: &std::sync::Arc<ProviderSessionAdapter>,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
    turn_id: &str,
    events: &mut dyn super::TurnEvents,
) -> super::TurnResult<()> {
    use crate::provider_session::control_texts::{
        campaign_file_disposition_message, campaign_file_updated_narration_message,
        campaign_preparation_message, gm_decision_control_message, player_boundary_narration_message,
    };
    use crate::provider_session::request::{
        forced_single_tool_call_from_json, native_content_for_text_operation,
    };
    use crate::provider_session::texts::{gm_text, system};
    use crate::session_loop::ProviderEvent;
    use sylvia_base::pyjson::{self, Dump};

    if !state.locals.pending_narration_facts.get()?.is_empty() {
        let facts = Json::Array(
            state.locals.pending_narration_facts.value()?.into_iter().map(Json::Object).collect(),
        );
        let data = pyjson::dumps(&facts, Dump::CANONICAL);
        let provisional = if d::provisional_tool_prose_needs_review(state)? {
            gm_text("narration_facts_provisional")
        } else {
            ""
        };
        let content = format!(
            "{}{data}{}{provisional}{}",
            gm_text("narration_facts_prefix"),
            gm_text("narration_facts_continue"),
            gm_text("narration_facts_suffix")
        );
        state.locals.inference_messages.get_mut()?.push(Tagged::plain(system(content)));
    }
    let controls = std::mem::take(state.locals.current_application_controls.get_mut()?);
    state.locals.inference_messages.get_mut()?.extend(controls.into_iter().map(Tagged::plain));
    if d::campaign_file_needs_narration(state)? {
        let message = campaign_file_updated_narration_message(
            state.locals.opening_footing.get()?.as_ref(),
            state.locals.current_player_action.get()?.as_deref(),
        );
        state.locals.inference_messages.get_mut()?.push(Tagged::plain(message));
    } else if d::held_disposition_must_be_retried(state)? {
        let message = campaign_file_disposition_message(
            state.locals.opening_footing.get()?.as_ref(),
            state.locals.current_player_action.get()?.as_deref(),
        );
        state.locals.inference_messages.get_mut()?.push(Tagged::plain(message));
    }
    // The player's exact message keeps its natural user role.
    if let Some(boundary) = state.locals.decision_boundary.value()? {
        let message = if boundary.preparation.is_some() {
            campaign_preparation_message(&boundary)
        } else if d::round_is_at_player_boundary(state)? {
            let committed: Vec<String> = state
                .locals
                .pending_narration_facts
                .get()?
                .iter()
                .filter_map(|fact| fact.get("tool").and_then(Json::as_str).map(str::to_owned))
                .collect();
            player_boundary_narration_message(
                &boundary,
                state.locals.current_player_action.get()?.as_deref(),
                &committed,
                *state.obligations.player_intent_pending.get()?,
            )
        } else {
            gm_decision_control_message(&boundary)
        };
        state.locals.inference_messages.get_mut()?.push(Tagged::plain(message));
    }
    if let Some(message) = state.locals.private_deliberation_correction_message.value()? {
        state.locals.inference_messages.get_mut()?.push(Tagged::plain(message));
        state.locals.private_deliberation_correction_message.set(None);
    }
    if let Some(message) = state.locals.required_tool_call_correction_message.value()? {
        state.locals.inference_messages.get_mut()?.push(Tagged::plain(message));
        state.locals.required_tool_call_correction_message.set(None);
    }
    let atomic = d::campaign_file_requires_atomic_completion(state, input)?;
    let required = d::round_requires_tool_call(state, atomic)?;
    state.locals.provider_tool_call_required.set(required);
    let history_allowed = d::round_allows_history_tool(state)?;
    let mut stream = adapter
        .stream_round(
            state.locals.inference_messages.value()?,
            state.locals.tools.value()?,
            input,
            required.then_some("required"),
            history_allowed,
        )
        .await?;
    while let Some(chunk) = stream.next().await? {
        if let Some(reason) = &chunk.finish_reason {
            let reason = reason.as_text().map_or_else(|| py_str(&reason.to_json()), str::to_owned);
            state.locals.round_finish_reason.set(Some(reason));
        }
        if let Some(metadata) = &chunk.chat_completion_metadata {
            state.locals.native_chat_metadata.set(Some(metadata.clone()));
        }
        if let Some(content) = &chunk.anthropic_content {
            state.locals.native_assistant_content.set(Some(
                content.iter().map(|block| block.as_object().cloned().unwrap_or_default()).collect(),
            ));
        }
        if !chunk.reasoning_content.is_empty() {
            state.locals.assistant_reasoning.get_mut()?.push(chunk.reasoning_content.clone());
            // Only provider-classified reasoning enters the private inspection sink.
            events
                .emit(ProviderEvent::PrivateReasoning {
                    text: chunk.reasoning_content.clone(),
                    response_id: Some(state.locals.inference_id.value()?),
                })
                .await?;
        }
        if !chunk.content.is_empty() {
            state.locals.assistant_text.get_mut()?.push(chunk.content.clone());
        }
        if let Some(delta) = &chunk.tool_call_delta {
            let fragment = sylvia_provider::tool_delta::parse_tool_call_delta(delta);
            let index = i64::try_from(fragment.index).unwrap_or(i64::MAX);
            let assembly = state.locals.assemblies.get_mut()?.entry(index).or_default();
            assembly.call_id.push_str(&fragment.call_id);
            assembly.name.push_str(&fragment.name);
            assembly.arguments.push_str(&fragment.arguments);
            let current = assembly.clone();
            state.locals.assembly.set(current);
        }
    }
    drop(stream);
    // The provider's original fields, before any application normalization: private owner
    // storage only, never canonical tool evidence.
    let mut native = JsonMap::new();
    if let Some(metadata) = state.locals.native_chat_metadata.value()? {
        native.insert("chat_completion_metadata".into(), Json::Object(metadata));
    }
    if let Some(content) = state.locals.native_assistant_content.value()? {
        native
            .insert("anthropic_content".into(), Json::Array(content.into_iter().map(Json::Object).collect()));
    }
    if !state.locals.assistant_reasoning.get()?.is_empty() {
        native.insert("reasoning_content".into(), state.locals.assistant_reasoning.get()?.concat().into());
    }
    if !native.is_empty() {
        for (key, value) in state.locals.context_fields.value()? {
            native.insert(key, value);
        }
        let event = ProviderEvent::private_continuation(&native, &state.locals.inference_id.value()?)?;
        events.emit(event).await?;
    }
    if state.locals.assemblies.get()?.is_empty() && *state.locals.provider_tool_call_required.get()? {
        let encoded = state.locals.assistant_text.get()?.concat();
        let normalized =
            forced_single_tool_call_from_json(&encoded, state.locals.aliases.get()?, || adapter.uuid_hex());
        // `loader_text_call` answers only for Sylvia's tool loader, which serves ledger mode
        // alone, so a legacy round never has one.
        if let Some(normalized) = normalized {
            let native_content = state.locals.native_assistant_content.value()?;
            let normalized_native =
                native_content_for_text_operation(native_content.as_ref(), &encoded, &normalized)?;
            if native_content.is_none() || normalized_native.is_some() {
                state.locals.native_assistant_content.set(normalized_native);
                state.locals.assemblies.get_mut()?.insert(0, normalized.clone());
                state.locals.assistant_text.get_mut()?.clear();
                let inference_id = state.locals.inference_id.value()?;
                let mut payload = JsonMap::new();
                payload.insert("kind".into(), "provider_forced_tool_json_normalized".into());
                payload.insert("turn_id".into(), turn_id.into());
                payload.insert("inference_id".into(), inference_id.as_str().into());
                payload.insert(
                    "basis_message_id".into(),
                    state.locals.basis_message_id.value()?.map_or(Json::Null, Json::from),
                );
                payload.insert("tool_name".into(), normalized.name.as_str().into());
                events
                    .emit(ProviderEvent::Evidence {
                        message_id: format!("{inference_id}-forced-tool-json-normalized"),
                        payload,
                    })
                    .await?;
            }
        }
    }
    if !state.locals.assemblies.get()?.is_empty() {
        // Even a rejected assembly is substantive progress, so a later empty round starts a
        // fresh empty sequence.
        state.locals.empty_completion_corrections_used.set(0);
    }
    Ok(())
}
