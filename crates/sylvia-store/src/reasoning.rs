//! Private provider records, kept apart from ordinary session messages, from
//! `sqlite_reasoning.py`: `append_record`, `finish_attempt`, `inspection_index`, `read_thinking`,
//! `read_continuation` and `read_actor_context`, with the store's `provider_continuation` and
//! `actor_context`. The table itself (`MIGRATION_11_SQL`) is in [`crate::schema`].
//!
//! Each function runs on the caller's connection, inside the caller's transaction where it
//! writes, as Python's do.

use crate::campaigns::named_rows;
use crate::error::{authority_coded, json_error, StoreError, StoreResult};
use crate::pytext::py_str;
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use rusqlite::Connection;
use sylvia_base::pyjson;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::pyops::py_eq;

fn rejected(message: &str) -> StoreError {
    crate::actions::rejected(message, "command_rejected")
}

/// One private provider record's identity and content, as `append_record` takes it.
#[derive(Debug, Clone, Copy)]
pub struct PrivateRecord<'a> {
    pub session_id: &'a str,
    pub attempt_id: &'a str,
    pub response_id: &'a str,
    pub turn_id: &'a str,
    pub basis_message_id: &'a str,
    pub source_order: i64,
    pub provider_id: &'a str,
    pub model_id: &'a str,
    pub kind: &'a str,
    pub payload: &'a str,
}

/// `append_record(connection, ...)`: retain exact ingress data once. A replay with the same data
/// is accepted; a conflicting replay never replaces private content.
pub fn append_record(connection: &Connection, record: &PrivateRecord<'_>) -> StoreResult<()> {
    let texts = [
        record.session_id,
        record.attempt_id,
        record.response_id,
        record.turn_id,
        record.basis_message_id,
        record.provider_id,
        record.model_id,
    ];
    if texts.iter().any(|text| text.is_empty())
        || record.source_order < 0
        || !matches!(record.kind, "thinking" | "continuation")
        || record.payload.is_empty()
    {
        return Err(rejected("Invalid private provider record"));
    }
    if record.kind == "continuation" {
        // `json.loads(payload)`: a `ValueError` or a non-object is refused.
        if !matches!(pyjson::loads(record.payload), Ok(Json::Object(_))) {
            return Err(rejected("Invalid native continuation record"));
        }
    }
    let exists = |table: &str| -> StoreResult<bool> {
        let sql = format!("SELECT 1 FROM {table} WHERE session_id = ?");
        Ok(!named_rows(connection, &sql, &[&record.session_id])?.1.is_empty())
    };
    let agent = exists("agent_sessions")?;
    let builder = exists("standalone_builder_chats")?;
    if agent == builder {
        return Err(rejected("Private provider record requires one known session"));
    }
    let values = [
        record.response_id,
        record.turn_id,
        record.basis_message_id,
        record.provider_id,
        record.model_id,
        record.kind,
        record.payload,
    ];
    let existing = named_rows(
        connection,
        "SELECT response_id, turn_id, basis_message_id, provider_id, model_id, kind, payload \
         FROM provider_private_records WHERE session_id = ? AND attempt_id = ? AND source_order = ?",
        &[&record.session_id, &record.attempt_id, &record.source_order],
    )?
    .1;
    if let Some(existing) = existing.first() {
        let same = existing.len() == values.len()
            && existing
                .iter()
                .zip(values)
                .all(|(stored, value)| matches!(stored, Value::Text(text) if text == value));
        if !same {
            return Err(StoreError::Coded(authority_coded(
                "IdempotencyConflict",
                "Private provider record identity already has different data",
            )));
        }
        return Ok(());
    }
    let terminal = named_rows(
        connection,
        "SELECT 1 FROM provider_private_records WHERE session_id = ? AND attempt_id = ? \
         AND status != 'partial' LIMIT 1",
        &[&record.session_id, &record.attempt_id],
    )?
    .1;
    if !terminal.is_empty() {
        return Err(rejected("Private provider attempt is already terminal"));
    }
    let owner = |known: bool| if known { Some(record.session_id) } else { None };
    connection.execute(
        "INSERT INTO provider_private_records \
         (session_id, agent_session_id, builder_session_id, attempt_id, source_order, \
         response_id, turn_id, basis_message_id, provider_id, model_id, kind, payload, status) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'partial')",
        rusqlite::params![
            record.session_id,
            owner(agent),
            owner(builder),
            record.attempt_id,
            record.source_order,
            record.response_id,
            record.turn_id,
            record.basis_message_id,
            record.provider_id,
            record.model_id,
            record.kind,
            record.payload,
        ],
    )?;
    Ok(())
}

/// `finish_attempt(connection, session_id, attempt_id, completed=...)`: the attempt's partial
/// records made terminal. An attempt without records gains none.
pub fn finish_attempt(
    connection: &Connection,
    session_id: &str,
    attempt_id: &str,
    completed: bool,
) -> StoreResult<()> {
    connection.execute(
        "UPDATE provider_private_records SET status = ? \
         WHERE session_id = ? AND attempt_id = ? AND status = 'partial'",
        rusqlite::params![if completed { "completed" } else { "interrupted" }, session_id, attempt_id],
    )?;
    Ok(())
}

/// A stored value as Python's `sqlite3` returns it, in JSON.
fn value_json(value: &Value) -> Json {
    match value {
        Value::Null => Json::Null,
        Value::Integer(integer) => Json::from(*integer),
        Value::Real(real) => Json::from(*real),
        Value::Text(text) => Json::String(text.clone()),
        Value::Blob(bytes) => Json::String(py_str(&Value::Blob(bytes.clone()))),
    }
}

/// `inspection_index(connection, session_id)`: each thinking attempt's availability and
/// correlation, never its text or any continuation content.
pub fn inspection_index(connection: &Connection, session_id: &str) -> StoreResult<Vec<JsonMap>> {
    let rows = named_rows(
        connection,
        "SELECT attempt_id, response_id, turn_id, basis_message_id, \
         MIN(source_order), status, COUNT(*) \
         FROM provider_private_records WHERE session_id = ? AND kind = 'thinking' \
         GROUP BY attempt_id, response_id, turn_id, basis_message_id, status \
         ORDER BY MIN(rowid)",
        &[&session_id],
    )?
    .1;
    let fields =
        ["attempt_id", "response_id", "turn_id", "basis_message_id", "source_order", "status", "chunk_count"];
    Ok(rows
        .iter()
        .map(|row| {
            fields.iter().zip(row).map(|(field, value)| ((*field).to_owned(), value_json(value))).collect()
        })
        .collect())
}

/// `read_thinking(connection, session_id, attempt_id, response_id)`: one response's thinking
/// text, in order, or `None` when it has none.
pub fn read_thinking(
    connection: &Connection,
    session_id: &str,
    attempt_id: &str,
    response_id: &str,
) -> StoreResult<Option<String>> {
    let rows = named_rows(
        connection,
        "SELECT payload FROM provider_private_records WHERE session_id = ? AND attempt_id = ? \
         AND response_id = ? AND kind = 'thinking' ORDER BY source_order",
        &[&session_id, &attempt_id, &response_id],
    )?
    .1;
    if rows.is_empty() {
        return Ok(None);
    }
    Ok(Some(rows.iter().map(|row| py_str(&row[0])).collect()))
}

/// `read_continuation(connection, session_id, response_id, provider_id=..., model_id=...)`: the
/// exact native data, only for its originating provider, model and response.
pub fn read_continuation(
    connection: &Connection,
    session_id: &str,
    response_id: &str,
    provider_id: &str,
    model_id: &str,
) -> StoreResult<Option<Json>> {
    let rows = named_rows(
        connection,
        "SELECT payload FROM provider_private_records WHERE session_id = ? AND response_id = ? \
         AND provider_id = ? AND model_id = ? AND kind = 'continuation' ORDER BY rowid",
        &[&session_id, &response_id, &provider_id, &model_id],
    )?
    .1;
    match rows.as_slice() {
        [] => Ok(None),
        [row] => pyjson::loads(&py_str(&row[0])).map(Some).map_err(json_error),
        _ => Err(rejected("Native continuation identity is ambiguous")),
    }
}

/// `read_actor_context(connection, session_id, context_id, provider_id=..., model_id=...)`: one
/// private participant's stored exchanges assembled, the last record's context carrying every
/// record's messages.
pub fn read_actor_context(
    connection: &Connection,
    session_id: &str,
    context_id: &str,
    provider_id: &str,
    model_id: &str,
) -> StoreResult<Option<JsonMap>> {
    if context_id.len() != 64 || !context_id.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f')) {
        return Err(rejected("Invalid actor context identity"));
    }
    let pattern = format!("actor-context-{context_id}-%");
    let rows = named_rows(
        connection,
        "SELECT payload FROM provider_private_records WHERE session_id = ? \
         AND response_id LIKE ? AND provider_id = ? AND model_id = ? AND kind = 'continuation' \
         ORDER BY rowid",
        &[&session_id, &pattern, &provider_id, &model_id],
    )?
    .1;
    if rows.is_empty() {
        return Ok(None);
    }
    let invalid = || rejected("Actor context ownership is invalid");
    let mut messages = Vec::new();
    let mut last = JsonMap::new();
    for row in &rows {
        let payload = pyjson::loads(&py_str(&row[0])).map_err(json_error)?;
        let Json::Object(payload) = payload else {
            return Err(StoreError::Other(format!(
                "'{}' object has no attribute 'get'",
                sylvia_domain::pyops::py_type_name(&payload)
            )));
        };
        let Some(Json::Object(context)) = payload.get("actor_context") else { return Err(invalid()) };
        if !context.get("format").is_some_and(|format| py_eq(format, &Json::from(1)))
            || !context.get("context_id").is_some_and(|id| py_eq(id, &Json::from(context_id)))
        {
            return Err(invalid());
        }
        let Some(Json::Array(more)) = context.get("messages") else { return Err(invalid()) };
        messages.extend(more.iter().cloned());
        last = context.clone();
    }
    // `{**context, "messages": messages}`: the key keeps its place.
    last.insert("messages".into(), Json::Array(messages));
    Ok(Some(last))
}

/// `PRAGMA user_version`: a profile before schema 11 has no private records.
fn has_private_records(connection: &Connection) -> StoreResult<bool> {
    let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    Ok(version >= 11)
}

impl AuthorityStore {
    /// `append_private_provider_record(record)`: commit private ingress on its own, apart from
    /// ordinary history and Full Audit capture, advancing the profile to schema 12 first.
    pub fn append_private_provider_record(
        &self,
        record: &sylvia_domain::provider_records::ProviderPrivateRecord,
    ) -> StoreResult<()> {
        let connection = self.connect()?;
        let result = (|| {
            connection.execute_batch("BEGIN IMMEDIATE")?;
            self.ensure_campaign_schema(&connection)?;
            append_record(
                &connection,
                &PrivateRecord {
                    session_id: &record.session_id,
                    attempt_id: &record.attempt_id,
                    response_id: &record.response_id,
                    turn_id: &record.turn_id,
                    basis_message_id: &record.basis_message_id,
                    source_order: record.source_order,
                    provider_id: &record.provider_id,
                    model_id: &record.model_id,
                    kind: &record.kind,
                    payload: &record.payload,
                },
            )?;
            connection.execute_batch("COMMIT")?;
            Ok(())
        })();
        if !connection.is_autocommit() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        result
    }

    /// `finish_private_provider_attempt(session_id, attempt_id, completed=...)`: the attempt's
    /// partial records made terminal; nothing before schema 11.
    pub fn finish_private_provider_attempt(
        &self,
        session_id: &str,
        attempt_id: &str,
        completed: bool,
    ) -> StoreResult<()> {
        let connection = self.connect()?;
        if !has_private_records(&connection)? {
            return Ok(());
        }
        let result = (|| {
            connection.execute_batch("BEGIN IMMEDIATE")?;
            finish_attempt(&connection, session_id, attempt_id, completed)?;
            connection.execute_batch("COMMIT")?;
            Ok(())
        })();
        if !connection.is_autocommit() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        result
    }

    /// `provider_continuation(session_id, response_id, provider_id=..., model_id=...)`.
    pub fn provider_continuation(
        &self,
        session_id: &str,
        response_id: &str,
        provider_id: &str,
        model_id: &str,
    ) -> StoreResult<Option<Json>> {
        let connection = self.connect_read_only()?;
        if !has_private_records(&connection)? {
            return Ok(None);
        }
        read_continuation(&connection, session_id, response_id, provider_id, model_id)
    }

    /// `actor_context(session_id, context_id, provider_id=..., model_id=...)`.
    pub fn actor_context(
        &self,
        session_id: &str,
        context_id: &str,
        provider_id: &str,
        model_id: &str,
    ) -> StoreResult<Option<JsonMap>> {
        let connection = self.connect_read_only()?;
        if !has_private_records(&connection)? {
            return Ok(None);
        }
        read_actor_context(&connection, session_id, context_id, provider_id, model_id)
    }

    /// `current_package_lock(campaign_id)`: the campaign's current lock record.
    pub fn current_package_lock(
        &self,
        campaign_id: &str,
    ) -> StoreResult<sylvia_domain::package_library::PackageLockRecord> {
        let connection = self.connect()?;
        crate::campaigns::current_lock_record(&connection, campaign_id)
    }
}
