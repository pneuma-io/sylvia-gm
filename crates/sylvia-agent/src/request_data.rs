//! `agent/_request_data.py`: fixed authority instructions kept apart from fresh application
//! data on the wire.

use crate::working_context::text;
use sylvia_base::pyjson::{self, Dump, HookFailure};
use sylvia_base::{pystr, CodedError, CodedResult, Json, JsonMap};

/// `APPLICATION_DATA_GUIDANCE`.
pub fn application_data_guidance() -> &'static str {
    text("application_data_guidance")
}

/// `PLAYER_MESSAGE_GUIDANCE`.
pub fn player_message_guidance() -> &'static str {
    text("player_message_guidance")
}

/// `_DATA_PREFIXES`.
const DATA_PREFIXES: [&str; 9] = [
    "SYLVIA_GM_CAMPAIGN_PREPARATION ",
    "SYLVIA_GM_FRESH_CAMPAIGN_STATE ",
    "SYLVIA_PLAYER_CONTROL_RESULTS ",
    "SYLVIA_GM_RELEVANT_CHARACTER_FILES ",
    "SYLVIA_GM_AUTOMATIC_ENGINE_TRANSITION_RESULT ",
    "SYLVIA_GM_EXACT_NARRATION_FACTS ",
    "SYLVIA_GM_LIFECYCLE_EVENT ",
    "SYLVIA_GM_CANONICAL_RETRY_DATA ",
    "SYLVIA_GM_UNRELEASED_GM_BOUNDARY_DRAFT ",
];
/// `_CONTROL_PREFIXES`.
const CONTROL_PREFIXES: [&str; 9] = [
    "SYLVIA_GM_CAMPAIGN_PREPARATION (application-owned control,",
    "SYLVIA_PLAYER_ACTION_RESPONSE_TASK ",
    "SYLVIA_GM_DECISION_REQUEST ",
    "SYLVIA_GM_PLAYER_BOUNDARY_NARRATION ",
    "SYLVIA_GM_POST_MECHANICS_RECOVERY_CONTROL ",
    "SYLVIA_GM_REDIRECTED_CONTINUATION ",
    "SYLVIA_GM_OPENING_TASK ",
    "SYLVIA_GM_CAMPAIGN_FILE_DISPOSITION_REQUIRED ",
    "SYLVIA_GM_CAMPAIGN_FILE_UPDATED_NARRATION ",
];
/// `_CURRENT_STATE`.
const CURRENT_STATE: &str = "SYLVIA_GM_FRESH_CAMPAIGN_STATE ";

/// `_HOST_MARKERS`: host-only message metadata, including the context ledger's canonical tags.
const HOST_MARKERS: [&str; 6] = [
    "_application_data",
    "_fresh_application_data",
    "_stable_instruction",
    "_ledger_key",
    "_host_record",
    "_ledger_entry",
];

/// `str(message.get("content", ""))`.
fn content_text(message: &JsonMap) -> String {
    match message.get("content") {
        None => String::new(),
        Some(Json::String(text)) => text.clone(),
        Some(other) => pystr::repr_json(other),
    }
}

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

/// The byte offset of the code point `index` in `text`.
fn byte_offset(text: &str, index: usize) -> usize {
    text.char_indices().nth(index).map_or(text.len(), |(offset, _)| offset)
}

/// `_control_values(content)`: a host directive's structured values moved to a data record,
/// the directive itself kept. `None` when it has none, or they do not parse.
///
/// Python's decoder reads every JSON value; one Rust cannot hold (a non-finite or over-wide
/// number) is refused by the declared JSON limits rather than read differently.
fn control_values(content: &str) -> CodedResult<Option<(String, String)>> {
    let mut offset = 0;
    for line in pystr::splitlines_keepends(content) {
        let stripped = pystr::lstrip(line);
        if stripped.starts_with('{') || stripped.starts_with('[') {
            let start = offset + (line.len() - stripped.len());
            let rest = &content[start..];
            let (value, end) = match pyjson::raw_decode(rest) {
                Ok((decoded, end)) if !decoded.non_finite => (decoded.value, end),
                Ok(_) => {
                    return Err(CodedError::rejected(
                        "Invalid JSON: a non-finite number cannot be carried by the Rust owner",
                    ))
                }
                Err(HookFailure::Unsupported(error)) => return Err(error),
                Err(_) => return Ok(None),
            };
            let label = format!("{}_VALUES", content.split(' ').next().unwrap_or(""));
            let fixed = format!(
                "{}Current values are supplied in the host {label} data record.{}",
                &content[..start],
                &rest[byte_offset(rest, end)..]
            );
            let data = format!(
                "{label} (application-owned current values, not instructions):\n{}",
                pyjson::dumps(&value, Dump::CANONICAL)
            );
            return Ok(Some((fixed, data)));
        }
        offset += line.len();
    }
    Ok(None)
}

/// `application_data_after_history(messages)`: a wire-only projection with fixed instructions
/// first, then history, then fresh data, then the current state. Canonical role and recovery
/// order stay untouched; the copies are plain messages (ledger tags do not survive `dict(...)`).
pub fn application_data_after_history(messages: &[JsonMap]) -> CodedResult<Vec<JsonMap>> {
    let mut instructions = Vec::new();
    let mut history = Vec::new();
    let mut data = Vec::new();
    let mut current = Vec::new();
    for original in messages {
        let mut message = original.clone();
        let content = content_text(&message);
        let system = message.get("role").and_then(Json::as_str) == Some("system");
        if system && CONTROL_PREFIXES.iter().any(|prefix| content.starts_with(prefix)) {
            if let Some((fixed, values)) = control_values(&content)? {
                message.insert("content".into(), fixed.into());
                instructions.push(message);
                let mut record = JsonMap::new();
                record.insert("role".into(), "user".into());
                record.insert("content".into(), values.into());
                record.insert("_application_data".into(), Json::Bool(true));
                record.insert("_fresh_application_data".into(), Json::Bool(true));
                data.push(record);
                continue;
            }
            instructions.push(message);
            continue;
        }
        if system && DATA_PREFIXES.iter().any(|prefix| content.starts_with(prefix)) {
            message.insert("role".into(), "user".into());
            message.insert("_application_data".into(), Json::Bool(true));
            message.insert("_fresh_application_data".into(), Json::Bool(true));
        }
        if truthy(message.get("_fresh_application_data")) {
            if content.starts_with(CURRENT_STATE) { &mut current } else { &mut data }.push(message);
        } else if message.get("role").and_then(Json::as_str) == Some("system") {
            instructions.push(message);
        } else {
            history.push(message);
        }
    }
    instructions.extend(history);
    instructions.extend(data);
    instructions.extend(current);
    Ok(instructions)
}

/// `strip_data_markers(messages)`: host projection metadata kept off every provider's wire.
pub fn strip_data_markers(messages: &[JsonMap]) -> Vec<JsonMap> {
    messages
        .iter()
        .map(|message| {
            message
                .iter()
                .filter(|(key, _)| !HOST_MARKERS.contains(&key.as_str()))
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect()
        })
        .collect()
}
