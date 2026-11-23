//! `agent/_provider_session.py`, the Campaign File and response-block helpers the tool batch uses:
//! which call arguments establish a change, the provider's response blocks admitted into held
//! blocks, the speakers a disposition can name, and the entities a mechanic's arguments name.

use super::request::contains_serialized_tool_call;
use super::tools::CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT;
use serde_json::Value as Json;
use std::collections::{BTreeMap, BTreeSet};
use sylvia_base::{pystr, CodedError, CodedResult, JsonMap};
use sylvia_domain::authority::{validate_authority_identity, CampaignContinuityView, Participant};

/// `_CAMPAIGN_FILE_ASSESSMENT_ARGUMENTS`.
pub const CAMPAIGN_FILE_ASSESSMENT_ARGUMENTS: [&str; 2] = ["state_change", "state_change_summary"];
/// `_SPEAKER_NAME_MAX_LENGTH`.
const SPEAKER_NAME_MAX_LENGTH: usize = 80;

fn value_error(message: impl Into<String>) -> CodedError {
    CodedError::rejected(message).with_error_type("ValueError")
}

/// `name in _CAMPAIGN_FILE_PROVIDER_ONLY_ARGUMENTS`.
fn provider_only(name: &str) -> bool {
    name == CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT
}

/// `_campaign_file_arguments_establish_changes(arguments)`: whether a call attempted a durable
/// Campaign File mutation, rather than an unchanged review. A non-empty collection, or any
/// supplied non-array (malformed, but still an attempt), establishes one.
pub fn campaign_file_arguments_establish_changes(arguments: &JsonMap) -> bool {
    arguments.iter().any(|(name, value)| {
        !(provider_only(name) || CAMPAIGN_FILE_ASSESSMENT_ARGUMENTS.contains(&name.as_str()))
            && match value {
                Json::Array(items) => !items.is_empty(),
                _ => true,
            }
    })
}

/// `_campaign_file_empty_collection_paths(arguments)`: the empty collections supplied; an
/// unchanged review is narration with no state collections.
pub fn campaign_file_empty_collection_paths(arguments: &JsonMap) -> Vec<String> {
    arguments
        .iter()
        .filter(|(name, value)| {
            !provider_only(name) && matches!(value, Json::Array(items) if items.is_empty())
        })
        .map(|(name, _)| name.clone())
        .collect()
}

/// `_current_referenced_entities(parameters, state)`: the current state of each entity a
/// completed call names by a text argument, from every `*.entities` collection.
pub fn current_referenced_entities(parameters: &JsonMap, state: &JsonMap) -> JsonMap {
    let referenced: BTreeSet<&str> = parameters.values().filter_map(Json::as_str).collect();
    let mut current = JsonMap::new();
    for (key, entities) in state {
        let Json::Object(entities) = entities else { continue };
        if !key.ends_with(".entities") {
            continue;
        }
        let named: BTreeSet<&str> =
            entities.keys().map(String::as_str).filter(|entity_id| referenced.contains(entity_id)).collect();
        for entity_id in named {
            current.insert(entity_id.to_owned(), entities[entity_id].clone());
        }
    }
    current
}

/// `_validated_speaker_name(value)`: one short single-line speaker name, its spacing collapsed.
pub fn validated_speaker_name(value: &Json) -> CodedResult<String> {
    let Json::String(text) = value else {
        return Err(value_error("speaker_name must be a string"));
    };
    let name = text.split(pystr::is_space).filter(|part| !part.is_empty()).collect::<Vec<_>>().join(" ");
    if name.is_empty()
        || pystr::len(&name) > SPEAKER_NAME_MAX_LENGTH
        || text.chars().any(|character| character != ' ' && !pystr::is_printable(character))
        || contains_serialized_tool_call(&name)
    {
        return Err(value_error("speaker_name must be a short single-line name"));
    }
    Ok(name)
}

/// `_validated_raw_response_blocks(value, allow_legacy_dialogue_text=...)`: admit the
/// provider's block shape into held blocks. `npc_utterance` blocks become `npc_dialogue` with
/// their `spoken_words` as `text`; legacy `npc_dialogue` text is only for saved native history.
pub fn validated_raw_response_blocks(
    value: &Json,
    allow_legacy_dialogue_text: bool,
) -> CodedResult<Vec<JsonMap>> {
    let items = match value {
        Json::Array(items) if !items.is_empty() => items,
        _ => return Err(value_error("response must be a non-empty ordered block list")),
    };
    let mut blocks = Vec::with_capacity(items.len());
    for item in items {
        let Json::Object(item) = item else {
            return Err(value_error("each response block must be an object"));
        };
        let mut kind = item.get("kind");
        if kind.and_then(Json::as_str) == Some("npc_dialogue") && !allow_legacy_dialogue_text {
            return Err(value_error("NPC blocks require npc_utterance and spoken_words"));
        }
        let dialogue = Json::from("npc_dialogue");
        if kind.and_then(Json::as_str) == Some("npc_utterance") {
            kind = Some(&dialogue);
        }
        let kind_text = kind.and_then(Json::as_str);
        let is_dialogue = kind_text == Some("npc_dialogue");
        let mut text_key = if is_dialogue { "spoken_words" } else { "text" };
        if allow_legacy_dialogue_text && !item.contains_key("spoken_words") {
            text_key = "text";
        }
        let text = item.get(text_key).and_then(Json::as_str);
        let valid_kind = matches!(kind_text, Some("narration" | "npc_dialogue" | "ooc"));
        let Some(text) = text.filter(|text| valid_kind && !pystr::strip(text).is_empty()) else {
            return Err(value_error(format!("response block kind and {text_key} are invalid")));
        };
        let mut allowed: BTreeSet<&str> =
            if is_dialogue { ["kind", text_key, "speaker_id"].into() } else { ["kind", "text"].into() };
        if is_dialogue {
            if let Some(speaker_name) = item.get("speaker_name") {
                allowed.insert("speaker_name");
                validated_speaker_name(speaker_name)?;
            }
        }
        let fields: BTreeSet<&str> = item.keys().map(String::as_str).collect();
        if fields != allowed {
            return Err(value_error("response block fields are invalid"));
        }
        if contains_serialized_tool_call(text) {
            return Err(value_error("response blocks cannot contain serialized tool calls"));
        }
        if is_dialogue {
            let Some(speaker_id) = item.get("speaker_id").and_then(Json::as_str) else {
                return Err(value_error("NPC dialogue requires a speaker identity"));
            };
            if speaker_id != "anonymous" {
                validate_authority_identity(speaker_id, "Response speaker identity", false)
                    .map_err(|error| value_error(error.message()))?;
            }
        }
        // `block = dict(item); block["kind"] = kind; block["text"] = block.pop("spoken_words")`.
        let mut block = item.clone();
        block.insert("kind".into(), kind.cloned().unwrap_or(Json::Null));
        if text_key == "spoken_words" {
            let spoken = block.shift_remove("spoken_words").unwrap_or(Json::Null);
            block.insert("text".into(), spoken);
        }
        blocks.push(block);
    }
    Ok(blocks)
}

/// `_unsupported_response_speaker_ids(blocks, snapshot, parameters)`: the dialogue speakers the
/// disposition cannot establish: not a participant, an active Campaign Entity, an entity this
/// update admits, or `anonymous`. Sorted.
pub fn unsupported_response_speaker_ids(
    blocks: &[JsonMap],
    participants: &[Participant],
    continuity: &CampaignContinuityView,
    parameters: &JsonMap,
) -> Vec<String> {
    let mut admitted: BTreeSet<String> =
        participants.iter().map(|participant| participant.participant_id().to_owned()).collect();
    let mut records: Vec<_> = continuity.records.iter().collect();
    records.sort_by_key(|record| record.admitted_revision);
    let mut latest = BTreeMap::new();
    for record in records.into_iter().filter(|record| record.kind == "entity") {
        latest.insert(record.record_id.clone(), record);
    }
    admitted.extend(latest.into_iter().filter(|(_, record)| record.status == "active").map(|(id, _)| id));
    if let Some(Json::Array(entities)) = parameters.get("entities") {
        admitted.extend(
            entities
                .iter()
                .filter_map(|item| item.get("entity_id").and_then(Json::as_str))
                .map(str::to_owned),
        );
    }
    admitted.insert("anonymous".into());
    let unsupported: BTreeSet<String> = blocks
        .iter()
        .filter(|block| block.get("kind").and_then(Json::as_str) == Some("npc_dialogue"))
        .filter(|block| {
            !block.get("speaker_id").and_then(Json::as_str).is_some_and(|id| admitted.contains(id))
        })
        .map(|block| match block.get("speaker_id") {
            Some(Json::String(id)) => id.clone(),
            Some(other) => sylvia_base::pystr::repr_json(other),
            None => "None".to_owned(),
        })
        .collect();
    unsupported.into_iter().collect()
}
