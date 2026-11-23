//! The GM turn locals as the tracer dumps them (`trace_turn_phases.py`): read into a
//! [`ProviderTurnState`] and written back in the same shape, so a phase's result compares with
//! Python's. Only assigned locals appear in a dump; message lists carry their ledger tags under
//! `<name>__tags`.

use super::{boundary, map, maps, participant, session_message, strings, text, tool};
use indexmap::{IndexMap, IndexSet};
use serde_json::{json, Value as J};
use sylvia_agent::provider_session::tagged::Tagged;
use sylvia_agent::provider_turn::state::{ProviderTurnState, Slot, ToolCallAssembly};
use sylvia_agent::stateless_mcp::ModelMechanicsTool;
use sylvia_base::JsonMap;
use sylvia_domain::authority::{ModelDecisionBoundary, ModelToolBehavior};

/// Locals read but never written back (the phases traced so far do not change them).
pub const READ_ONLY: [&str; 3] = ["current_player_message", "participants", "actor_request"];

fn tagged_list(messages: &J, tags: &J) -> Vec<Tagged> {
    let tags = tags.as_array().cloned().unwrap_or_default();
    messages
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
        .map(|(index, message)| Tagged {
            message: map(message),
            tags: tags.get(index).map(map).unwrap_or_default(),
        })
        .collect()
}

fn set_of(value: &J) -> IndexSet<String> {
    strings(&value["__set__"]).into_iter().collect()
}

fn tools(value: &J) -> Vec<ModelMechanicsTool> {
    value.as_array().unwrap().iter().map(tool).collect()
}

fn opt_maps(value: &J) -> Option<Vec<JsonMap>> {
    (!value.is_null()).then(|| maps(value))
}

/// Read a dump's turn locals.
pub fn load(dump: &J) -> ProviderTurnState {
    let mut state = ProviderTurnState::default();
    let flag = |value: &J| value.as_bool().unwrap();
    for (name, value) in dump["obligations"].as_object().unwrap() {
        let o = &mut state.obligations;
        let slot = match name.as_str() {
            "buffered_narration_needs_disposition" => &mut o.buffered_narration_needs_disposition,
            "campaign_file_narration_pending" => &mut o.campaign_file_narration_pending,
            "force_campaign_file_update" => &mut o.force_campaign_file_update,
            "pending_failed_tool_call" => &mut o.pending_failed_tool_call,
            "player_intent_pending" => &mut o.player_intent_pending,
            "player_intent_resolution_required" => &mut o.player_intent_resolution_required,
            "engine_transition_review_pending" => &mut o.engine_transition_review_pending,
            "provisional_tool_prose_pending" => &mut o.provisional_tool_prose_pending,
            other => panic!("unknown obligation {other}"),
        };
        slot.set(flag(value));
    }
    for (name, value) in dump["progress"].as_object().unwrap() {
        let p = &mut state.progress;
        let slot = match name.as_str() {
            "campaign_file_disposition_in_exchange" => &mut p.campaign_file_disposition_in_exchange,
            "any_commit_in_exchange" => &mut p.any_commit_in_exchange,
            "campaign_file_commit_in_exchange" => &mut p.campaign_file_commit_in_exchange,
            "state_changing_call_submitted" => &mut p.state_changing_call_submitted,
            "round_failed" => &mut p.round_failed,
            "sequence_execution_stopped" => &mut p.sequence_execution_stopped,
            other => panic!("unknown progress {other}"),
        };
        slot.set(flag(value));
    }
    for (name, value) in dump["decisions"].as_object().unwrap() {
        let k = &mut state.decisions;
        let slot = match name.as_str() {
            "gm_boundary_decision" => &mut k.gm_boundary_decision,
            "player_boundary_narration" => &mut k.player_boundary_narration,
            other => panic!("unknown decision {other}"),
        };
        slot.set(flag(value));
    }
    let locals = dump["locals"].as_object().unwrap();
    let l = &mut state.locals;
    for (name, value) in locals {
        let text = || value.as_str().unwrap().to_owned();
        let int = || value.as_i64().unwrap();
        let tags = |key: &str| locals.get(&format!("{key}__tags")).cloned().unwrap_or(J::Null);
        match name.as_str() {
            name if name.ends_with("__tags") || name.ends_with("__is_messages") => {}
            "actor_request" => {
                assert!(value.is_null(), "an actor request is not loaded yet");
                l.actor_request.set(None);
            }
            "aliases" => {
                l.aliases.set(value.as_object().unwrap().iter().map(|(k, v)| (k.clone(), tool(v))).collect())
            }
            "assemblies" => l.assemblies.set(
                value
                    .as_object()
                    .unwrap()
                    .iter()
                    .map(|(k, v)| {
                        let text = |key: &str| v[key].as_str().unwrap().to_owned();
                        (
                            k.parse().unwrap(),
                            ToolCallAssembly {
                                call_id: text("call_id"),
                                name: text("name"),
                                arguments: text("arguments"),
                            },
                        )
                    })
                    .collect(),
            ),
            "assistant_reasoning" => l.assistant_reasoning.set(strings(value)),
            "assistant_text" => l.assistant_text.set(strings(value)),
            "basis_message_id" => l.basis_message_id.set(value.as_str().map(str::to_owned)),
            "behavior" => l.behavior.set(ModelToolBehavior::parse(value.as_str().unwrap()).unwrap()),
            "buffered_narration" => l.buffered_narration.set(value.as_str().map(str::to_owned)),
            "buffered_raw_response_blocks" => l.buffered_raw_response_blocks.set(opt_maps(value)),
            "buffered_response_blocks" => l.buffered_response_blocks.set(opt_maps(value)),
            "campaign_file_disposition_supported" => l.campaign_file_disposition_supported.set(flag(value)),
            "campaign_file_mutation_attempted" => l.campaign_file_mutation_attempted.set(flag(value)),
            "campaign_id" => l.campaign_id.set(text()),
            "canonical_player_narration" => l.canonical_player_narration.set(maps(value)),
            "committed_action_ids" => l.committed_action_ids.set(set_of(value)),
            "context_fields" => l.context_fields.set(map(value)),
            "context_owner" => l.context_owner.set(value.as_object().cloned()),
            "current_application_controls" => l.current_application_controls.set(maps(value)),
            "current_catalog" => l.current_catalog.set(tools(value)),
            "current_player_action" => l.current_player_action.set(value.as_str().map(str::to_owned)),
            "current_player_message" => {
                l.current_player_message.set((!value.is_null()).then(|| session_message(value)));
            }
            "current_user_wire_text" => l.current_user_wire_text.set(value.as_str().map(str::to_owned)),
            "decision_boundary" => l.decision_boundary.set((!value.is_null()).then(|| boundary(value))),
            "direct_response" => l.direct_response.set(flag(value)),
            "discovery_id" => l.discovery_id.set(text()),
            "empty_completion_corrections_used" => l.empty_completion_corrections_used.set(int()),
            "entity_display_names" => l.entity_display_names.set(
                value
                    .as_object()
                    .unwrap()
                    .iter()
                    .map(|(k, v)| (k.clone(), v.as_str().unwrap().to_owned()))
                    .collect(),
            ),
            "exchange_messages" => {
                assert_eq!(locals.get("exchange_messages__is_messages"), Some(&J::Bool(true)));
                l.exchange_messages.set(());
            }
            "inference_id" => l.inference_id.set(text()),
            "inference_messages" => l.inference_messages.set(tagged_list(value, &tags("inference_messages"))),
            "local_tool_failures" => l.local_tool_failures.set(set_of(value)),
            "local_tool_results" => l
                .local_tool_results
                .set(value.as_object().unwrap().iter().map(|(k, v)| (k.clone(), map(v))).collect()),
            "max_empty_completion_corrections" => l.max_empty_completion_corrections.set(int()),
            "max_missing_required_tool_corrections" => l.max_missing_required_tool_corrections.set(int()),
            "max_private_deliberation_corrections" => l.max_private_deliberation_corrections.set(int()),
            "max_rejected_tool_corrections" => l.max_rejected_tool_corrections.set(int()),
            "max_repeated_response_corrections" => l.max_repeated_response_corrections.set(int()),
            "mechanics_action_ids" => l.mechanics_action_ids.set(text_map(value)),
            "mechanics_parameters" => l.mechanics_parameters.set(map_map(value)),
            "mechanics_pre_entities" => l.mechanics_pre_entities.set(map_map(value)),
            "mechanics_preparation_scopes" => {
                l.mechanics_preparation_scopes.set(map(value).into_iter().collect())
            }
            "mechanics_tool_behaviors" => l.mechanics_tool_behaviors.set(
                value
                    .as_object()
                    .unwrap()
                    .iter()
                    .map(|(k, v)| (k.clone(), ModelToolBehavior::parse(v.as_str().unwrap()).unwrap()))
                    .collect(),
            ),
            "mechanics_tool_names" => l.mechanics_tool_names.set(text_map(value)),
            "messages" => l.messages.set(tagged_list(value, &tags("messages"))),
            "missing_required_tool_corrections_used" => l.missing_required_tool_corrections_used.set(int()),
            "native_assistant_content" => l.native_assistant_content.set(opt_maps(value)),
            "native_chat_metadata" => l.native_chat_metadata.set(value.as_object().cloned()),
            "ooc_ack_required" => l.ooc_ack_required.set(flag(value)),
            "opening_footing" => l.opening_footing.set(value.as_object().cloned()),
            "opening_request" => l.opening_request.set(flag(value)),
            "participant_ooc_only" => l.participant_ooc_only.set(flag(value)),
            "participants" => l.participants.set(value.as_array().unwrap().iter().map(participant).collect()),
            "pending_narration_facts" => l.pending_narration_facts.set(maps(value)),
            "post_commit_tool_required" => l.post_commit_tool_required.set(flag(value)),
            "post_mechanics_recovery" => l.post_mechanics_recovery.set(flag(value)),
            "previous_player_action" => l.previous_player_action.set(value.as_str().map(str::to_owned)),
            "previous_player_facing_response" => {
                l.previous_player_facing_response.set(value.as_str().map(str::to_owned))
            }
            "private_deliberation_correction_message" => {
                l.private_deliberation_correction_message.set(value.as_object().cloned())
            }
            "private_deliberation_corrections_used" => l.private_deliberation_corrections_used.set(int()),
            "provider_round" => l.provider_round.set(int()),
            "provider_tool_schemas" => l.provider_tool_schemas.set(map_map(value)),
            "rejected_tool_corrections_used" => l.rejected_tool_corrections_used.set(int()),
            "repeated_response_corrections_used" => l.repeated_response_corrections_used.set(int()),
            "required_tool_call_correction_message" => {
                l.required_tool_call_correction_message.set(value.as_object().cloned())
            }
            "required_transitions" => l.required_transitions.set(tools(value)),
            "resumed_messages" => l.resumed_messages.set(tagged_list(value, &tags("resumed_messages"))),
            "round_finish_reason" => l.round_finish_reason.set(value.as_str().map(str::to_owned)),
            "saved_sequence_results" => l.saved_sequence_results.set(maps(value)),
            "sequence_narration_facts" => l.sequence_narration_facts.set(maps(value)),
            "tools" => l.tools.set(maps(value)),
            other => panic!("the state reader does not read {other} yet"),
        }
    }
    state
}

fn text_map(value: &J) -> IndexMap<String, String> {
    value.as_object().unwrap().iter().map(|(k, v)| (k.clone(), v.as_str().unwrap().to_owned())).collect()
}

fn map_map(value: &J) -> IndexMap<String, JsonMap> {
    value.as_object().unwrap().iter().map(|(k, v)| (k.clone(), map(v))).collect()
}

/// `plain(ModelMechanicsTool)`.
pub fn tool_json(tool: &ModelMechanicsTool) -> J {
    json!({
        "__class__": "ModelMechanicsTool",
        "package_id": tool.package_id,
        "package_version": tool.package_version,
        "package_sha256": tool.package_sha256,
        "name": tool.name,
        "capability": tool.capability,
        "input_schema": tool.input_schema,
        "parameter_derivations": tool.parameter_derivations,
        "description": tool.description,
        "category": tool.category,
        "model_behavior": tool.model_behavior.as_str(),
    })
}

/// `plain(ModelDecisionBoundary)`.
pub fn boundary_json(boundary: &ModelDecisionBoundary) -> J {
    json!({
        "__class__": "ModelDecisionBoundary",
        "owner": boundary.owner.as_str(),
        "actor_id": boundary.actor_id,
        "actor_name": boundary.actor_name,
        "actor_state": boundary.actor_state,
        "campaign_revision": boundary.campaign_revision,
        "active_side_id": boundary.active_side_id,
        "focused_actor_id": boundary.focused_actor_id,
        "eligible_actors": boundary.eligible_actors.iter().map(|actor| json!({
            "__class__": "ModelDecisionActor",
            "actor_id": actor.actor_id,
            "actor_name": actor.actor_name,
            "actor_state": actor.actor_state,
        })).collect::<Vec<_>>(),
        "preparation": boundary.preparation,
    })
}

fn set_json(set: &IndexSet<String>) -> J {
    let mut items: Vec<&String> = set.iter().collect();
    items.sort();
    json!({"__set__": items})
}

fn put<T>(out: &mut JsonMap, name: &str, slot: &Slot<T>, write: impl FnOnce(&T) -> J) {
    if let Ok(value) = slot.get() {
        out.insert(name.into(), write(value));
    }
}

fn messages_json(out: &mut JsonMap, name: &str, slot: &Slot<Vec<Tagged>>) {
    if let Ok(messages) = slot.get() {
        out.insert(name.into(), J::Array(messages.iter().map(|m| J::Object(m.message.clone())).collect()));
        out.insert(
            format!("{name}__tags"),
            J::Array(messages.iter().map(|m| J::Object(m.tags.clone())).collect()),
        );
    }
}

/// Write the turn locals in the dump's shape (the read-only ones are left out).
pub fn dump(state: &ProviderTurnState) -> J {
    let flags = |pairs: &[(&str, &Slot<bool>)]| -> J {
        let mut out = JsonMap::new();
        for (name, slot) in pairs {
            if let Ok(value) = slot.get() {
                out.insert((*name).into(), J::Bool(*value));
            }
        }
        J::Object(out)
    };
    let o = &state.obligations;
    let p = &state.progress;
    let k = &state.decisions;
    let l = &state.locals;
    let mut out = JsonMap::new();
    let tools_json = |tools: &Vec<ModelMechanicsTool>| J::Array(tools.iter().map(tool_json).collect());
    let to = |value: &dyn erased::ToJson| value.to_json();
    put(&mut out, "aliases", &l.aliases, |aliases| {
        J::Object(aliases.iter().map(|(k, v)| (k.clone(), tool_json(v))).collect())
    });
    put(&mut out, "assemblies", &l.assemblies, |assemblies| {
        J::Object(
            assemblies
                .iter()
                .map(|(k, v)| {
                    (k.to_string(), json!({"__class__": "_ToolCallAssembly", "call_id": v.call_id, "name": v.name, "arguments": v.arguments}))
                })
                .collect(),
        )
    });
    put(&mut out, "assistant_reasoning", &l.assistant_reasoning, |v| to(v));
    put(&mut out, "assistant_text", &l.assistant_text, |v| to(v));
    put(&mut out, "basis_message_id", &l.basis_message_id, |v| to(v));
    put(&mut out, "behavior", &l.behavior, |v| J::from(v.as_str()));
    put(&mut out, "buffered_narration", &l.buffered_narration, |v| to(v));
    put(&mut out, "buffered_raw_response_blocks", &l.buffered_raw_response_blocks, |v| to(v));
    put(&mut out, "buffered_response_blocks", &l.buffered_response_blocks, |v| to(v));
    put(&mut out, "campaign_file_disposition_supported", &l.campaign_file_disposition_supported, |v| to(v));
    put(&mut out, "campaign_file_mutation_attempted", &l.campaign_file_mutation_attempted, |v| to(v));
    put(&mut out, "campaign_id", &l.campaign_id, |v| to(v));
    put(&mut out, "canonical_player_narration", &l.canonical_player_narration, |v| to(v));
    put(&mut out, "committed_action_ids", &l.committed_action_ids, set_json);
    put(&mut out, "context_fields", &l.context_fields, |v| to(v));
    put(&mut out, "context_owner", &l.context_owner, |v| to(v));
    put(&mut out, "current_application_controls", &l.current_application_controls, |v| to(v));
    put(&mut out, "current_catalog", &l.current_catalog, tools_json);
    put(&mut out, "current_player_action", &l.current_player_action, |v| to(v));
    put(&mut out, "current_user_wire_text", &l.current_user_wire_text, |v| to(v));
    put(&mut out, "decision_boundary", &l.decision_boundary, |v| v.as_ref().map_or(J::Null, boundary_json));
    put(&mut out, "direct_response", &l.direct_response, |v| to(v));
    put(&mut out, "discovery_id", &l.discovery_id, |v| to(v));
    put(&mut out, "empty_completion_corrections_used", &l.empty_completion_corrections_used, |v| to(v));
    put(&mut out, "entity_display_names", &l.entity_display_names, |v| {
        J::Object(v.iter().map(|(k, v)| (k.clone(), J::from(v.as_str()))).collect())
    });
    if l.exchange_messages.get().is_ok() {
        if let Ok(messages) = l.messages.get() {
            out.insert(
                "exchange_messages".into(),
                J::Array(messages.iter().map(|m| J::Object(m.message.clone())).collect()),
            );
            out.insert("exchange_messages__is_messages".into(), J::Bool(true));
        }
    }
    put(&mut out, "inference_id", &l.inference_id, |v| to(v));
    messages_json(&mut out, "inference_messages", &l.inference_messages);
    put(&mut out, "local_tool_failures", &l.local_tool_failures, set_json);
    put(&mut out, "local_tool_results", &l.local_tool_results, |v| {
        J::Object(v.iter().map(|(k, v)| (k.clone(), J::Object(v.clone()))).collect())
    });
    for (name, slot) in [
        ("max_empty_completion_corrections", &l.max_empty_completion_corrections),
        ("max_missing_required_tool_corrections", &l.max_missing_required_tool_corrections),
        ("max_private_deliberation_corrections", &l.max_private_deliberation_corrections),
        ("max_rejected_tool_corrections", &l.max_rejected_tool_corrections),
        ("max_repeated_response_corrections", &l.max_repeated_response_corrections),
        ("missing_required_tool_corrections_used", &l.missing_required_tool_corrections_used),
        ("private_deliberation_corrections_used", &l.private_deliberation_corrections_used),
        ("provider_round", &l.provider_round),
        ("rejected_tool_corrections_used", &l.rejected_tool_corrections_used),
        ("repeated_response_corrections_used", &l.repeated_response_corrections_used),
    ] {
        put(&mut out, name, slot, |v| J::from(*v));
    }
    put(&mut out, "mechanics_action_ids", &l.mechanics_action_ids, |v| {
        J::Object(v.iter().map(|(k, v)| (k.clone(), J::from(v.as_str()))).collect())
    });
    for (name, slot) in [
        ("mechanics_parameters", &l.mechanics_parameters),
        ("mechanics_pre_entities", &l.mechanics_pre_entities),
        ("provider_tool_schemas", &l.provider_tool_schemas),
    ] {
        put(&mut out, name, slot, |v| {
            J::Object(v.iter().map(|(k, v)| (k.clone(), J::Object(v.clone()))).collect())
        });
    }
    put(&mut out, "mechanics_preparation_scopes", &l.mechanics_preparation_scopes, |v| {
        J::Object(v.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
    });
    put(&mut out, "mechanics_tool_behaviors", &l.mechanics_tool_behaviors, |v| {
        J::Object(v.iter().map(|(k, v)| (k.clone(), J::from(v.as_str()))).collect())
    });
    put(&mut out, "mechanics_tool_names", &l.mechanics_tool_names, |v| {
        J::Object(v.iter().map(|(k, v)| (k.clone(), J::from(v.as_str()))).collect())
    });
    messages_json(&mut out, "messages", &l.messages);
    put(&mut out, "native_assistant_content", &l.native_assistant_content, |v| to(v));
    put(&mut out, "native_chat_metadata", &l.native_chat_metadata, |v| to(v));
    put(&mut out, "ooc_ack_required", &l.ooc_ack_required, |v| to(v));
    put(&mut out, "opening_footing", &l.opening_footing, |v| to(v));
    put(&mut out, "opening_request", &l.opening_request, |v| to(v));
    put(&mut out, "participant_ooc_only", &l.participant_ooc_only, |v| to(v));
    put(&mut out, "pending_narration_facts", &l.pending_narration_facts, |v| to(v));
    put(&mut out, "post_commit_tool_required", &l.post_commit_tool_required, |v| to(v));
    put(&mut out, "post_mechanics_recovery", &l.post_mechanics_recovery, |v| to(v));
    put(&mut out, "previous_player_action", &l.previous_player_action, |v| to(v));
    put(&mut out, "previous_player_facing_response", &l.previous_player_facing_response, |v| to(v));
    put(
        &mut out,
        "private_deliberation_correction_message",
        &l.private_deliberation_correction_message,
        |v| to(v),
    );
    put(&mut out, "required_tool_call_correction_message", &l.required_tool_call_correction_message, |v| {
        to(v)
    });
    put(&mut out, "required_transitions", &l.required_transitions, tools_json);
    messages_json(&mut out, "resumed_messages", &l.resumed_messages);
    put(&mut out, "round_finish_reason", &l.round_finish_reason, |v| to(v));
    put(&mut out, "saved_sequence_results", &l.saved_sequence_results, |v| to(v));
    put(&mut out, "sequence_narration_facts", &l.sequence_narration_facts, |v| to(v));
    put(&mut out, "tools", &l.tools, |v| to(v));
    json!({
        "obligations": flags(&[
            ("buffered_narration_needs_disposition", &o.buffered_narration_needs_disposition),
            ("campaign_file_narration_pending", &o.campaign_file_narration_pending),
            ("force_campaign_file_update", &o.force_campaign_file_update),
            ("pending_failed_tool_call", &o.pending_failed_tool_call),
            ("player_intent_pending", &o.player_intent_pending),
            ("player_intent_resolution_required", &o.player_intent_resolution_required),
            ("engine_transition_review_pending", &o.engine_transition_review_pending),
            ("provisional_tool_prose_pending", &o.provisional_tool_prose_pending),
        ]),
        "progress": flags(&[
            ("campaign_file_disposition_in_exchange", &p.campaign_file_disposition_in_exchange),
            ("any_commit_in_exchange", &p.any_commit_in_exchange),
            ("campaign_file_commit_in_exchange", &p.campaign_file_commit_in_exchange),
            ("state_changing_call_submitted", &p.state_changing_call_submitted),
            ("round_failed", &p.round_failed),
            ("sequence_execution_stopped", &p.sequence_execution_stopped),
        ]),
        "decisions": flags(&[
            ("gm_boundary_decision", &k.gm_boundary_decision),
            ("player_boundary_narration", &k.player_boundary_narration),
        ]),
        "locals": out,
    })
}

/// Plain values written as JSON.
mod erased {
    use serde_json::Value as J;
    use sylvia_agent::provider_session::tagged::Tagged;
    use sylvia_base::JsonMap;

    pub trait ToJson {
        fn to_json(&self) -> J;
    }
    impl ToJson for bool {
        fn to_json(&self) -> J {
            J::Bool(*self)
        }
    }
    impl ToJson for String {
        fn to_json(&self) -> J {
            J::from(self.as_str())
        }
    }
    impl ToJson for JsonMap {
        fn to_json(&self) -> J {
            J::Object(self.clone())
        }
    }
    impl ToJson for J {
        fn to_json(&self) -> J {
            self.clone()
        }
    }
    impl ToJson for i64 {
        fn to_json(&self) -> J {
            J::from(*self)
        }
    }
    impl<T: ToJson> ToJson for Option<T> {
        fn to_json(&self) -> J {
            self.as_ref().map_or(J::Null, ToJson::to_json)
        }
    }
    impl<T: ToJson> ToJson for Vec<T> {
        fn to_json(&self) -> J {
            J::Array(self.iter().map(ToJson::to_json).collect())
        }
    }
    impl ToJson for Tagged {
        fn to_json(&self) -> J {
            J::Object(self.message.clone())
        }
    }
}

/// Python's turn locals after the phase: as before, with the changes applied.
pub fn expected_after(before: &J, changed: &J) -> J {
    let mut expected = before.clone();
    for (group, values) in changed.as_object().unwrap() {
        for (key, value) in values.as_object().unwrap() {
            expected[group][key] = value.clone();
        }
    }
    expected
}

/// The Rust locals (written back) against Python's, key by key.
pub fn compare(name: &str, ours: &J, theirs: &J) {
    for group in ["obligations", "progress", "decisions", "locals"] {
        let (a, b) = (ours[group].as_object().unwrap(), theirs[group].as_object().unwrap());
        for (key, value) in b {
            if group == "locals" && READ_ONLY.contains(&key.as_str()) {
                continue;
            }
            match a.get(key) {
                Some(ours) if text(ours) == text(value) => {}
                Some(ours) => panic!(
                    "{name}: {group}.{key} differs\n  expected {}\n  got      {}",
                    &text(value)[..text(value).len().min(2000)],
                    &text(ours)[..text(ours).len().min(2000)]
                ),
                None => panic!("{name}: {group}.{key} is unassigned in Rust"),
            }
        }
        for key in a.keys() {
            assert!(b.contains_key(key), "{name}: Rust assigned {group}.{key}, which Python left unassigned");
        }
    }
}

