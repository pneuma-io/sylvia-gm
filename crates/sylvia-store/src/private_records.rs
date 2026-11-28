//! The optional private provider member of a portable archive, from `private_records_archive.py`:
//! `validate_rows`, `encode` (the export's) and `decode` (archive validation's and recovery's).
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

/// `validate_rows(rows, session_ids=...)`: every row an object with exactly [`FIELDS`], its
/// texts non-empty, its `source_order` a non-negative integer, its session in `session_ids`,
/// its identity unique and its attempt's status consistent; each row comes back in [`FIELDS`]
/// order.
pub fn validate_rows(rows: &[Json], session_ids: &HashSet<String>) -> StoreResult<Vec<JsonMap>> {
    let mut result = Vec::new();
    let mut identities: HashSet<(String, String, i64)> = HashSet::new();
    let mut attempts: HashMap<(String, String), String> = HashMap::new();
    for row in rows {
        let Json::Object(row) = row else { return Err(reject()) };
        if result.len() >= MAX_ROWS
            || row.len() != FIELDS.len()
            || FIELDS.iter().any(|field| !row.contains_key(*field))
        {
            return Err(reject());
        }
        let text = |field: &str| match &row[field] {
            Json::String(text) if !text.is_empty() => Some(text.clone()),
            _ => None,
        };
        if FIELDS.iter().any(|field| *field != "source_order" && text(field).is_none()) {
            return Err(reject());
        }
        let (session_id, attempt_id) =
            (text("session_id").unwrap_or_default(), text("attempt_id").unwrap_or_default());
        // `type(order) is not int or order < 0`.
        let order = match &row["source_order"] {
            Json::Number(number) if number.is_i64() || number.is_u64() => number.as_i64(),
            _ => None,
        };
        let Some(order) = order.filter(|order| *order >= 0) else { return Err(reject()) };
        if !session_ids.contains(&session_id) {
            return Err(reject());
        }
        let kind = text("kind").unwrap_or_default();
        let status = text("status").unwrap_or_default();
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
            match pyjson::loads_hooked(&text("payload").unwrap_or_default(), policy) {
                Ok(read) if matches!(read.value, Json::Object(_)) => {}
                _ => return Err(reject()),
            }
        }
        result.push(FIELDS.iter().map(|field| ((*field).to_owned(), row[*field].clone())).collect());
    }
    Ok(result)
}

/// A row of `provider_private_records` in [`FIELDS`] order as the values Python's `sqlite3`
/// gives `validate_rows`: a value that is neither text nor an integer is refused there whatever
/// it holds, so it stands as `null`.
fn row_json(row: &[Value]) -> Json {
    Json::Object(
        FIELDS
            .iter()
            .zip(row)
            .map(|(field, value)| {
                let value = match value {
                    Value::Text(text) => Json::String(text.clone()),
                    Value::Integer(number) => Json::from(*number),
                    _ => Json::Null,
                };
                ((*field).to_owned(), value)
            })
            .collect(),
    )
}

/// `encode(rows, session_ids=...)`: the member's JSONL bytes, for rows read from
/// `provider_private_records` in [`FIELDS`] order.
pub fn encode(rows: &[Vec<Value>], session_ids: &HashSet<String>) -> StoreResult<Vec<u8>> {
    let rows: Vec<Json> = rows.iter().map(|row| row_json(row)).collect();
    let mut output = Vec::new();
    for row in validate_rows(&rows, session_ids)? {
        let mut line = pyjson::dump_bytes(&Json::Object(row), Dump::CANONICAL.sorted(false));
        line.push(b'\n');
        if line.len() > MAX_ROW_BYTES || output.len() + line.len() > MAX_BYTES {
            return Err(reject());
        }
        output.extend(line);
    }
    Ok(output)
}

/// `decode(data, session_ids=...)`: the member's rows, read back as `json.loads` reads each
/// `bytes` line: decoded by its leading bytes (UTF-8, with or without its byte order mark,
/// UTF-16 or UTF-32), as `json.loads(bytes)` decodes. Empty data is no rows. A lone surrogate,
/// which a Python string can hold, is refused (`docs/rust-port/differences.md` row 3).
pub fn decode(data: &[u8], session_ids: &HashSet<String>) -> StoreResult<Vec<JsonMap>> {
    if data.len() > MAX_BYTES || (!data.is_empty() && !data.ends_with(b"\n")) {
        return Err(reject());
    }
    let mut rows = Vec::new();
    for line in split_lines_keep_ends(data) {
        if line.len() > MAX_ROW_BYTES || rows.len() >= MAX_ROWS {
            return Err(reject());
        }
        let Ok((text, false)) = pyjson::decode_detected(line) else { return Err(reject()) };
        let policy = HookPolicy { refuse_duplicates: true, refuse_constants: false };
        match pyjson::loads_hooked(&text, policy) {
            Ok(read) if matches!(read.value, Json::Object(_)) => rows.push(read.value),
            _ => return Err(reject()),
        }
    }
    validate_rows(&rows, session_ids)
}

/// `bytes.splitlines(keepends=True)`: lines end at `\n`, `\r` or `\r\n`.
fn split_lines_keep_ends(data: &[u8]) -> Vec<&[u8]> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut index = 0;
    while index < data.len() {
        let end = match data[index] {
            b'\n' => index + 1,
            b'\r' if data.get(index + 1) == Some(&b'\n') => index + 2,
            b'\r' => index + 1,
            _ => {
                index += 1;
                continue;
            }
        };
        lines.push(&data[start..end]);
        start = end;
        index = end;
    }
    if start < data.len() {
        lines.push(&data[start..]);
    }
    lines
}
