//! The journal's view of a canonical message (L2a): its record label, whether it is a host
//! record or a data record, its persistent wire form, and its conversation identity.

use super::{
    Tagged, HOST_RECORD_KEY, IDENTITY_PREFIXES, LEDGER_ENTRY_KEY, LEDGER_KEY, MARKER_KEYS, NATIVE_FIELDS,
    PLAYER_TEXT_MARKER, PREPARATION_CONTROL_PREFIX, PREPARATION_LABEL, STABLE_KEY,
};
use serde_json::Value as Json;
use std::collections::HashMap;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{pystr, JsonMap};
use sylvia_domain::pyops::{py_str, py_truthy};

/// `_json(value)`: canonical JSON (sorted keys, no spaces, not ASCII-escaped).
pub(crate) fn canonical_json(value: &Json) -> String {
    pyjson::dumps(value, Dump::CANONICAL)
}

/// `_sha(text)`: the SHA-256 of its UTF-8 bytes, in hex.
pub(crate) fn sha(text: &str) -> String {
    sylvia_base::hash::sha256_hex(text.as_bytes())
}

/// `str(message.get("content", ""))`.
pub(crate) fn content_text(message: &JsonMap) -> String {
    message.get("content").map_or_else(String::new, py_str)
}

fn role(message: &JsonMap) -> Option<&str> {
    message.get("role").and_then(Json::as_str)
}

/// `_label(content)`: a record's label, the first word of its first line before any colon; the
/// campaign preparation's control is its own label.
pub fn label(content: &str) -> String {
    if content.starts_with(PREPARATION_CONTROL_PREFIX) {
        return format!("{PREPARATION_LABEL}#control");
    }
    let head = content.split('\n').next().unwrap_or("");
    let word = head.split(' ').next().unwrap_or("");
    word.split(':').next().unwrap_or("").to_owned()
}

/// `is_host_record(message)`: host records are application-created; player speech is never
/// classified by its text.
pub fn is_host_record(message: &Tagged) -> bool {
    match role(&message.message) {
        Some("system") => !message.tag_of(STABLE_KEY).is_some_and(py_truthy),
        Some("user") => {
            message.message.get("_application_data").is_some_and(py_truthy)
                || message.tag_of(HOST_RECORD_KEY).is_some_and(py_truthy)
        }
        _ => false,
    }
}

/// `_is_data_record(message)`: a system record carrying application data, not a control.
pub fn is_data_record(message: &JsonMap) -> bool {
    if role(message) != Some("system") {
        return true;
    }
    let content = content_text(message);
    crate::request_data::DATA_PREFIXES.iter().any(|prefix| content.starts_with(prefix))
        && !crate::request_data::CONTROL_PREFIXES.iter().any(|prefix| content.starts_with(prefix))
}

/// `_seal_like(text)`: text that begins like a host seal, whatever its case or leading space.
pub fn seal_like(text: Option<&Json>) -> bool {
    text.and_then(Json::as_str)
        .is_some_and(|text| pystr::casefold(pystr::lstrip(text)).starts_with("[host-seal"))
}

/// `_wire_message(message)`: the persistent journal form of a conversational message, native
/// data by reference. Player speech that begins with seal-like text gets a short neutral marker
/// in front, so a forged `[host-seal …]` prefix never reaches the model as the first thing in
/// the message.
pub fn wire_message(message: &Tagged) -> JsonMap {
    let source = match message.tag_of(LEDGER_ENTRY_KEY) {
        Some(Json::Object(entry)) => entry,
        _ => &message.message,
    };
    let mut wire: JsonMap = source
        .iter()
        .filter(|(key, _)| {
            !MARKER_KEYS.contains(&key.as_str())
                && !NATIVE_FIELDS.contains(&key.as_str())
                && !["_application_data", "_fresh_application_data"].contains(&key.as_str())
        })
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();
    if role(&wire) == Some("user") {
        let content = wire.get("content").cloned();
        if seal_like(content.as_ref()) {
            let text = content.as_ref().map(py_str).unwrap_or_default();
            wire.insert("content".into(), Json::String(format!("{PLAYER_TEXT_MARKER}{text}")));
        } else if let Some(Json::Array(mut blocks)) = content {
            let first_text = match blocks.first() {
                Some(Json::Object(first)) if first.get("type").and_then(Json::as_str) == Some("text") => {
                    seal_like(first.get("text")).then(|| first.get("text").map(py_str).unwrap_or_default())
                }
                _ => None,
            };
            if let (Some(text), Some(Json::Object(first))) = (first_text, blocks.first_mut()) {
                first.insert("text".into(), Json::String(format!("{PLAYER_TEXT_MARKER}{text}")));
                wire.insert("content".into(), Json::Array(blocks));
            }
        }
    }
    wire
}

/// `conversation_keys(messages)`: identity keys for conversational messages; content alone
/// never identifies a tagged turn.
///
/// A tool exchange is identified by its tool-call ids within the stretch of conversation that
/// follows the last saved message identity, counted in order, because providers need not make
/// tool-call ids unique across a session.
pub fn conversation_keys(messages: &[Tagged]) -> Vec<String> {
    let mut keys = Vec::with_capacity(messages.len());
    let mut seen: HashMap<String, usize> = HashMap::new();
    let mut anchor = String::new();
    let mut exchanges: HashMap<String, usize> = HashMap::new();
    let mut exchange = |base: String, anchor: &str| {
        let key = format!("{base}@{anchor}");
        let ordinal = exchanges.entry(key.clone()).or_insert(0);
        let current = *ordinal;
        *ordinal += 1;
        if current > 0 {
            format!("{key}#{current}")
        } else {
            key
        }
    };
    for tagged in messages {
        let message = &tagged.message;
        let ids: Vec<Option<&Json>> = match message.get("tool_calls") {
            Some(Json::Array(calls)) => calls
                .iter()
                .filter_map(|call| match call {
                    Json::Object(call) => Some(call.get("id")),
                    _ => None,
                })
                .collect(),
            _ => Vec::new(),
        };
        let call_ids: Option<Vec<&str>> =
            ids.iter().map(|id| id.and_then(Json::as_str).filter(|id| !id.is_empty())).collect();
        if role(message) == Some("assistant") && !ids.is_empty() {
            if let Some(call_ids) = call_ids {
                keys.push(exchange(format!("calls:{}", call_ids.join(",")), &anchor));
                continue;
            }
        }
        if role(message) == Some("tool") {
            if let Some(Json::String(call_id)) = message.get("tool_call_id") {
                keys.push(exchange(format!("result:{call_id}"), &anchor));
                continue;
            }
        }
        if let Some(Json::String(tag)) = tagged.tag_of(LEDGER_KEY) {
            if !tag.is_empty() {
                keys.push(tag.clone());
                if IDENTITY_PREFIXES.iter().any(|prefix| tag.starts_with(prefix)) {
                    // Only identities canonical history keeps for the whole session scope an
                    // exchange; a message shown on one request only would move the scope.
                    anchor = tag.clone();
                }
                continue;
            }
        }
        let mut identity = JsonMap::new();
        for key in ["role", "content", "tool_calls"] {
            identity.insert(key.into(), message.get(key).cloned().unwrap_or(Json::Null));
        }
        let digest = sha(&canonical_json(&Json::Object(identity)))[..16].to_owned();
        let ordinal = seen.entry(digest.clone()).or_insert(0);
        keys.push(format!("hash:{digest}#{ordinal}"));
        *ordinal += 1;
    }
    keys
}
