//! The optional private provider member of a portable archive, from `private_records_archive.py`:
//! `validate_rows` and `encode` (the export's half; `decode` comes with recovery).
//!
//! Declared difference: a continuation payload holding an integer past 64 bits, which Python
//! reads, is refused here as an invalid member (`docs/rust-port/differences.md`).

use crate::error::{StoreError, StoreResult};
use rusqlite::types::Value;
use std::collections::{HashMap, HashSet};
use sylvia_base::pyjson::{self, Dump, HookPolicy};
use sylvia_base::{Json, JsonMap};

/// `MEMBER_NAME`.
pub const MEMBER_NAME: &str = "provider-private.jsonl";
/// `MAX_BYTES`.
pub const MAX_BYTES: usize = 67_108_864;
/// `MAX_ROW_BYTES`.
pub const MAX_ROW_BYTES: usize = 16_777_216;
/// `MAX_ROWS`.
pub const MAX_ROWS: usize = 250_000;
/// `FIELDS`, in the member's order.
pub const FIELDS: [&str; 11] = [
    "session_id",
    "attempt_id",
    "response_id",
    "turn_id",
    "basis_message_id",
    "source_order",
    "provider_id",
    "model_id",
    "kind",
    "payload",
    "status",
];

/// `_reject()`.
pub fn reject() -> StoreError {
    crate::actions::rejected(
        "Invalid private provider archive member",
        "portable_export_invalid_private_records",
    )
}

/// `validate_rows(rows, session_ids=...)` for rows read from `provider_private_records`, each in
/// [`FIELDS`] order: foreign sessions, ambiguous identities and inconsistent attempt status are
/// refused.
pub fn validate_rows(rows: &[Vec<Value>], session_ids: &HashSet<String>) -> StoreResult<Vec<JsonMap>> {
    let mut result = Vec::new();
    let mut identities: HashSet<(String, String, i64)> = HashSet::new();
    let mut attempts: HashMap<(String, String), String> = HashMap::new();
    for row in rows {
        if result.len() >= MAX_ROWS || row.len() != FIELDS.len() {
            return Err(reject());
        }
        let text = |index: usize| match &row[index] {
            Value::Text(text) if !text.is_empty() => Some(text.clone()),
            _ => None,
        };
        let mut values = JsonMap::new();
        for (index, field) in FIELDS.iter().enumerate() {
            if *field == "source_order" {
                continue;
            }
            let Some(value) = text(index) else { return Err(reject()) };
            values.insert((*field).to_owned(), Json::String(value));
        }
        let (session_id, attempt_id) = (text(0).unwrap_or_default(), text(1).unwrap_or_default());
        let Value::Integer(order) = row[5] else { return Err(reject()) };
        if !session_ids.contains(&session_id) || order < 0 {
            return Err(reject());
        }
        let kind = text(8).unwrap_or_default();
        let status = text(10).unwrap_or_default();
        if !matches!(kind.as_str(), "thinking" | "continuation")
            || !matches!(status.as_str(), "partial" | "completed" | "interrupted")
        {
            return Err(reject());
        }
        if !identities.insert((session_id.clone(), attempt_id.clone(), order)) {
            return Err(reject());
        }
        if attempts.entry((session_id, attempt_id)).or_insert_with(|| status.clone()) != &status {
            return Err(reject());
        }
        if kind == "continuation" {
            // `json.loads(payload, object_pairs_hook=_unique_object)`: duplicate members are
            // refused, `NaN` and `Infinity` are read.
            let policy = HookPolicy { refuse_duplicates: true, refuse_constants: false };
            match pyjson::loads_hooked(&text(9).unwrap_or_default(), policy) {
                Ok(read) if matches!(read.value, Json::Object(_)) => {}
                _ => return Err(reject()),
            }
        }
        let mut ordered = JsonMap::new();
        for field in FIELDS {
            let value = if field == "source_order" { Json::from(order) } else { values[field].clone() };
            ordered.insert(field.to_owned(), value);
        }
        result.push(ordered);
    }
    Ok(result)
}

/// `encode(rows, session_ids=...)`: the member's JSONL bytes.
pub fn encode(rows: &[Vec<Value>], session_ids: &HashSet<String>) -> StoreResult<Vec<u8>> {
    let mut output = Vec::new();
    for row in validate_rows(rows, session_ids)? {
        let mut line = pyjson::dump_bytes(&Json::Object(row), Dump::CANONICAL.sorted(false));
        line.push(b'\n');
        if line.len() > MAX_ROW_BYTES || output.len() + line.len() > MAX_BYTES {
            return Err(reject());
        }
        output.extend(line);
    }
    Ok(output)
}
