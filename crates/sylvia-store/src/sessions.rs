//! Sessions and messages, part 1: agent sessions, standalone Builder chats, message appends and
//! reads, and the status, model and reasoning-preference updates.
//!
//! Python: `sqlite_session.py` (`read_session`, `builder_chat_from_row`, `read_builder_chat(s)`,
//! `message_from_row`, `read_messages`, `read_context`, `append_message`,
//! `update_session_status`, `restore_stopped_session`, `insert_builder_chat`,
//! `rename_builder_chat`, `delete_builder_chat`, `update_session_model`, `insert_session`),
//! `sqlite_effort.update_preferences`, `builder_workspace_migration.remove_builder_workspace`
//! and the session operations of `SQLiteAuthorityStore`.
//!
//! The history page, activity basis, child sessions and human review come later: the page reads
//! campaign continuity and mechanics activity, which are not ported yet.

use crate::actions::{dump, rejected, require_active_campaign};
use crate::campaigns::{named_rows, parse, session_from_row, session_payload, Named};
use crate::error::{value_error, StoreError, StoreResult};
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use rusqlite::{params, Connection, OptionalExtension};
use std::path::{Path, PathBuf};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pystr, Json, JsonMap};
use sylvia_domain::authority::{LifecycleState, WriterRole};
use sylvia_domain::session::{
    agent_session_payload_sha256, builder_chat_payload_sha256, BuilderChatRecord, SessionContext,
    SessionMessage, SessionMessageKind, SessionRecord, SessionRecordFields, SessionStatus,
};

fn authority(kind: &'static str, message: &str) -> StoreError {
    StoreError::Coded(crate::error::authority_coded(kind, message))
}

fn table_exists(connection: &Connection, name: &str) -> StoreResult<bool> {
    Ok(connection
        .query_row("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", [name], |_| Ok(()))
        .optional()?
        .is_some())
}

/// The first row of a query, by column name.
fn first(
    connection: &Connection,
    query: &str,
    values: &[&dyn rusqlite::ToSql],
) -> StoreResult<Option<(Vec<String>, Vec<Value>)>> {
    let (columns, mut rows) = named_rows(connection, query, values)?;
    Ok(if rows.is_empty() { None } else { Some((columns, rows.swap_remove(0))) })
}

/// `sqlite_session.builder_chat_from_row`.
fn builder_chat_from_row(row: &Named<'_>) -> StoreResult<BuilderChatRecord> {
    let preference = |column: &str| -> StoreResult<Option<String>> {
        if row.has(column) {
            row.optional_text(column)
        } else {
            Ok(None)
        }
    };
    let session = SessionRecord::new(SessionRecordFields {
        session_id: row.text("session_id")?,
        campaign_id: None,
        role: WriterRole::Builder,
        provider_id: row.text("provider_id")?,
        model_id: row.text("model_id")?,
        status: parse(&row.text("status")?, SessionStatus::parse, "SessionStatus")?,
        reasoning_effort: preference("reasoning_effort")?,
        reasoning_effort_once: preference("reasoning_effort_once")?,
        ..SessionRecordFields::default()
    })?;
    let record = BuilderChatRecord::new(session, row.text("display_name")?, row.int("created_ordinal")?)?;
    if row.text("payload_sha256")? != builder_chat_payload_sha256(&record) {
        return Err(value_error("Stored standalone Builder chat payload identity is invalid"));
    }
    Ok(record)
}

/// `sqlite_session.read_session`.
pub(crate) fn read_session(connection: &Connection, session_id: &str) -> StoreResult<SessionRecord> {
    if let Some((columns, values)) =
        first(connection, "SELECT * FROM agent_sessions WHERE session_id = ?", &[&session_id])?
    {
        return session_from_row(&Named { columns: &columns, values });
    }
    if !table_exists(connection, "standalone_builder_chats")? {
        return Err(rejected("Agent session is unavailable", "session_unavailable"));
    }
    match first(connection, "SELECT * FROM standalone_builder_chats WHERE session_id = ?", &[&session_id])? {
        Some((columns, values)) => {
            Ok(builder_chat_from_row(&Named { columns: &columns, values })?.session().clone())
        }
        None => Err(rejected("Agent session is unavailable", "session_unavailable")),
    }
}

/// `sqlite_session.read_builder_chat`.
fn read_builder_chat(connection: &Connection, session_id: &str) -> StoreResult<BuilderChatRecord> {
    match first(connection, "SELECT * FROM standalone_builder_chats WHERE session_id = ?", &[&session_id])? {
        Some((columns, values)) => builder_chat_from_row(&Named { columns: &columns, values }),
        None => Err(rejected("Builder chat is unavailable", "session_unavailable")),
    }
}

/// `sqlite_session.read_builder_chats`: newest first.
fn read_builder_chats(connection: &Connection) -> StoreResult<Vec<BuilderChatRecord>> {
    let (columns, rows) =
        named_rows(connection, "SELECT * FROM standalone_builder_chats ORDER BY created_ordinal DESC", &[])?;
    rows.into_iter().map(|values| builder_chat_from_row(&Named { columns: &columns, values })).collect()
}

/// `sqlite_session.message_from_row`.
fn message_from_row(row: &Named<'_>) -> StoreResult<SessionMessage> {
    let canonical_sequence = match row.get("canonical_sequence")? {
        Value::Null => None,
        _ => Some(row.int("canonical_sequence")?),
    };
    Ok(SessionMessage::new(
        row.text("session_id")?,
        row.int("sequence")?,
        row.text("message_id")?,
        parse(&row.text("kind")?, SessionMessageKind::parse, "SessionMessageKind")?,
        &Json::Object(session_payload(&row.text("content_json")?)?),
        canonical_sequence,
    )?)
}

fn message_table(session: &SessionRecord) -> &'static str {
    if session.campaign_id().is_none() {
        "standalone_builder_chat_messages"
    } else {
        "agent_session_messages"
    }
}

/// `sqlite_session.read_messages`. Python's decoded-message cache only saves work; every read
/// still decodes exactly the saved rows, as here.
fn read_messages(connection: &Connection, session_id: &str) -> StoreResult<Vec<SessionMessage>> {
    let session = read_session(connection, session_id)?;
    let (columns, rows) = named_rows(
        connection,
        &format!("SELECT * FROM {} WHERE session_id = ? ORDER BY sequence", message_table(&session)),
        &[&session_id],
    )?;
    rows.into_iter().map(|values| message_from_row(&Named { columns: &columns, values })).collect()
}

/// Python's `==` between two JSON values: `1 == 1.0` and `True == 1`, dict order ignored.
pub(crate) fn py_eq(left: &Json, right: &Json) -> bool {
    fn number(value: &Json) -> Option<f64> {
        match value {
            Json::Bool(flag) => Some(f64::from(u8::from(*flag))),
            Json::Number(number) => number.as_f64(),
            _ => None,
        }
    }
    match (left, right) {
        (Json::Object(a), Json::Object(b)) => {
            a.len() == b.len()
                && a.iter().all(|(key, value)| b.get(key).is_some_and(|other| py_eq(value, other)))
        }
        (Json::Array(a), Json::Array(b)) => a.len() == b.len() && a.iter().zip(b).all(|(x, y)| py_eq(x, y)),
        (Json::Number(a), Json::Number(b)) if a.is_i64() && b.is_i64() => a.as_i64() == b.as_i64(),
        (Json::Number(a), Json::Number(b)) if a.is_u64() && b.is_u64() => a.as_u64() == b.as_u64(),
        _ => match (number(left), number(right)) {
            (Some(a), Some(b)) => a == b,
            _ => left == right,
        },
    }
}

/// `sqlite_session.append_message`.
fn append_message(
    connection: &Connection,
    session_id: &str,
    message_id: &str,
    kind: SessionMessageKind,
    content: &JsonMap,
    canonical_sequence: Option<i64>,
) -> StoreResult<SessionMessage> {
    let session = read_session(connection, session_id)?;
    if let Some(campaign) = session.campaign_id() {
        require_active_campaign(connection, campaign)?;
    }
    let table = message_table(&session);
    if let Some((columns, values)) = first(
        connection,
        &format!("SELECT * FROM {table} WHERE session_id = ? AND message_id = ?"),
        &[&session_id, &message_id],
    )? {
        let captured = message_from_row(&Named { columns: &columns, values })?;
        if captured.kind() != kind
            || !py_eq(&Json::Object(captured.content().clone()), &Json::Object(content.clone()))
            || captured.canonical_sequence() != canonical_sequence
        {
            return Err(authority("IdempotencyConflict", "Agent message identity payload does not match"));
        }
        return Ok(captured);
    }
    let status = session.fields().status;
    let terminal_kind = content.get("kind").and_then(Json::as_str);
    let stopped_terminal = status == SessionStatus::Stopped
        && kind == SessionMessageKind::Evidence
        && matches!(
            terminal_kind,
            Some("session_attempt_terminal" | "builder_attempt_terminal" | "builder_title_preference")
        );
    if status != SessionStatus::Active
        && !(status == SessionStatus::Stopped
            && (kind == SessionMessageKind::CanonicalReference || stopped_terminal))
    {
        return Err(rejected("Agent session is not active", "command_rejected"));
    }
    if let Some(sequence) = canonical_sequence {
        let Some(campaign) = session.campaign_id() else {
            return Err(rejected("Standalone Builder chats have no campaign chronology", "command_rejected"));
        };
        if first(
            connection,
            "SELECT campaign_id FROM canonical_events WHERE campaign_id = ? AND sequence = ?",
            &[&campaign, &sequence],
        )?
        .is_none()
        {
            return Err(rejected("Canonical session reference is unavailable", "command_rejected"));
        }
    }
    let next: i64 = connection.query_row(
        &format!("SELECT COALESCE(MAX(sequence), 0) + 1 FROM {table} WHERE session_id = ?"),
        [session_id],
        |row| row.get(0),
    )?;
    let record = SessionMessage::new(
        session_id,
        next,
        message_id,
        kind,
        &Json::Object(content.clone()),
        canonical_sequence,
    )?;
    connection.execute(
        &format!(
            "INSERT INTO {table} (session_id, sequence, message_id, kind, content_json, canonical_sequence) \
             VALUES (?, ?, ?, ?, ?, ?)"
        ),
        params![
            session_id,
            next,
            message_id,
            kind.as_str(),
            dump(&Json::Object(content.clone())),
            canonical_sequence
        ],
    )?;
    Ok(record)
}

fn with_fields(
    record: &SessionRecord,
    change: impl FnOnce(&mut SessionRecordFields),
) -> StoreResult<SessionRecord> {
    let mut fields = record.fields().clone();
    change(&mut fields);
    Ok(SessionRecord::new(fields)?)
}

/// Write one session's changed columns and its new payload identity, in whichever table holds it.
fn store_session(
    connection: &Connection,
    updated: &SessionRecord,
    assignments: &str,
    values: &[&dyn rusqlite::ToSql],
) -> StoreResult<()> {
    let session_id = updated.session_id();
    let (table, digest) = if updated.campaign_id().is_none() {
        let chat = read_builder_chat(connection, session_id)?;
        let updated_chat =
            BuilderChatRecord::new(updated.clone(), chat.display_name(), chat.created_ordinal())?;
        ("standalone_builder_chats", builder_chat_payload_sha256(&updated_chat))
    } else {
        ("agent_sessions", agent_session_payload_sha256(updated))
    };
    let mut all: Vec<&dyn rusqlite::ToSql> = values.to_vec();
    all.push(&digest);
    all.push(&session_id);
    connection.execute(
        &format!("UPDATE {table} SET {assignments}payload_sha256 = ? WHERE session_id = ?"),
        all.as_slice(),
    )?;
    Ok(())
}

/// `sqlite_session.update_session_status`.
fn update_session_status(
    connection: &Connection,
    session_id: &str,
    status: SessionStatus,
) -> StoreResult<SessionRecord> {
    let current = read_session(connection, session_id)?;
    if let Some(campaign) = current.campaign_id() {
        require_active_campaign(connection, campaign)?;
    }
    let from = current.fields().status;
    if from == status {
        return Ok(current);
    }
    if from != SessionStatus::Active
        && !(from == SessionStatus::ReviewRequired && status == SessionStatus::Stopped)
    {
        return Err(rejected("Agent session is not active", "command_rejected"));
    }
    let updated = with_fields(&current, |fields| fields.status = status)?;
    store_session(connection, &updated, "status = ?, ", &[&status.as_str()])?;
    read_session(connection, session_id)
}

/// `sqlite_session.restore_stopped_session`.
fn restore_stopped_session(connection: &Connection, session_id: &str) -> StoreResult<SessionRecord> {
    let current = read_session(connection, session_id)?;
    if current.fields().status != SessionStatus::Stopped {
        return Err(rejected("Designated campaign session is not stopped", "command_rejected"));
    }
    let updated = with_fields(&current, |fields| fields.status = SessionStatus::Active)?;
    let (table, digest) = if current.campaign_id().is_none() {
        let chat = read_builder_chat(connection, session_id)?;
        let updated_chat =
            BuilderChatRecord::new(updated.clone(), chat.display_name(), chat.created_ordinal())?;
        ("standalone_builder_chats", builder_chat_payload_sha256(&updated_chat))
    } else {
        ("agent_sessions", agent_session_payload_sha256(&updated))
    };
    connection.execute(
        &format!("UPDATE {table} SET status = 'active', payload_sha256 = ? WHERE session_id = ? AND status = 'stopped'"),
        params![digest, session_id],
    )?;
    read_session(connection, session_id)
}

/// `sqlite_session.update_session_model`.
fn update_session_model(
    connection: &Connection,
    session_id: &str,
    provider_id: &str,
    model_id: &str,
) -> StoreResult<SessionRecord> {
    let current = read_session(connection, session_id)?;
    let updated = with_fields(&current, |fields| {
        fields.provider_id = provider_id.to_owned();
        fields.model_id = model_id.to_owned();
    })?;
    if current.campaign_id().is_some() {
        // Older openings omitted the provider from their chronology. Preserve it before
        // changing the session; the opening digest still binds its value.
        if let Some((columns, values)) = first(
            connection,
            "SELECT handoff_id, safe_payload_json FROM builder_dm_handoffs WHERE dm_session_id = ?",
            &[&session_id],
        )? {
            let row = Named { columns: &columns, values };
            let mut chronology = session_payload(&row.text("safe_payload_json")?)?;
            if !chronology.contains_key("dm_provider_id") {
                chronology
                    .insert("dm_provider_id".into(), Json::String(current.fields().provider_id.clone()));
                connection.execute(
                    "UPDATE builder_dm_handoffs SET safe_payload_json = ? WHERE handoff_id = ?",
                    params![dump(&Json::Object(chronology)), row.text("handoff_id")?],
                )?;
            }
        }
    }
    store_session(connection, &updated, "provider_id = ?, model_id = ?, ", &[&provider_id, &model_id])?;
    read_session(connection, session_id)
}

/// `sqlite_effort.update_preferences`.
fn update_preferences(
    connection: &Connection,
    session_id: &str,
    effort: Option<&str>,
    once: Option<&str>,
) -> StoreResult<SessionRecord> {
    let current = read_session(connection, session_id)?;
    let updated = with_fields(&current, |fields| {
        fields.reasoning_effort = effort.map(str::to_owned);
        fields.reasoning_effort_once = once.map(str::to_owned);
    })?;
    store_session(
        connection,
        &updated,
        "reasoning_effort = ?, reasoning_effort_once = ?, ",
        &[&effort, &once],
    )?;
    read_session(connection, session_id)
}

/// `sqlite_session.insert_builder_chat`.
fn insert_builder_chat(
    connection: &Connection,
    session_id: &str,
    provider_id: &str,
    model_id: &str,
    display_name: &str,
) -> StoreResult<BuilderChatRecord> {
    if first(connection, "SELECT 1 FROM agent_sessions WHERE session_id = ?", &[&session_id])?.is_some() {
        return Err(authority(
            "IdempotencyConflict",
            "Builder chat identity collides with a campaign session",
        ));
    }
    if let Some((columns, values)) =
        first(connection, "SELECT * FROM standalone_builder_chats WHERE session_id = ?", &[&session_id])?
    {
        let captured = builder_chat_from_row(&Named { columns: &columns, values })?;
        let fields = captured.session().fields();
        if fields.provider_id != provider_id
            || fields.model_id != model_id
            || captured.display_name() != display_name
        {
            return Err(authority("IdempotencyConflict", "Builder chat identity payload does not match"));
        }
        return Ok(captured);
    }
    let ordinal: i64 = connection.query_row(
        "SELECT COALESCE(MAX(created_ordinal), 0) + 1 FROM standalone_builder_chats",
        [],
        |row| row.get(0),
    )?;
    let session = SessionRecord::new(SessionRecordFields {
        session_id: session_id.to_owned(),
        campaign_id: None,
        role: WriterRole::Builder,
        provider_id: provider_id.to_owned(),
        model_id: model_id.to_owned(),
        ..SessionRecordFields::default()
    })?;
    let record = BuilderChatRecord::new(session, display_name, ordinal)?;
    connection.execute(
        "INSERT INTO standalone_builder_chats \
         (session_id, provider_id, model_id, status, display_name, created_ordinal, payload_sha256) \
         VALUES (?, ?, ?, ?, ?, ?, ?)",
        params![
            session_id,
            provider_id,
            model_id,
            record.session().fields().status.as_str(),
            display_name,
            ordinal,
            builder_chat_payload_sha256(&record)
        ],
    )?;
    Ok(record)
}

/// `sqlite_session.rename_builder_chat`: a user rename records the preference that protects it
/// from a later automatic title.
fn rename_builder_chat(
    connection: &Connection,
    session_id: &str,
    display_name: &str,
    automatic: bool,
) -> StoreResult<BuilderChatRecord> {
    let current = read_builder_chat(connection, session_id)?;
    if automatic
        && read_messages(connection, session_id)?.iter().any(|message| {
            message.kind() == SessionMessageKind::Evidence
                && message.content().get("kind").and_then(Json::as_str) == Some("builder_title_preference")
        })
    {
        return Ok(current);
    }
    let updated = BuilderChatRecord::new(current.session().clone(), display_name, current.created_ordinal())?;
    if !automatic {
        let mut content = JsonMap::new();
        content.insert("kind".into(), "builder_title_preference".into());
        content.insert("automatic".into(), false.into());
        let message_id = format!("builder-title-user-{}", &sha256_hex(session_id.as_bytes())[..24]);
        append_message(connection, session_id, &message_id, SessionMessageKind::Evidence, &content, None)?;
    }
    connection.execute(
        "UPDATE standalone_builder_chats SET display_name = ?, payload_sha256 = ? WHERE session_id = ?",
        params![display_name, builder_chat_payload_sha256(&updated), session_id],
    )?;
    Ok(updated)
}

/// `sqlite_session.insert_session`.
fn insert_session(connection: &Connection, record: &SessionRecord) -> StoreResult<SessionRecord> {
    let fields = record.fields();
    let Some(campaign_id) = fields.campaign_id.as_deref() else {
        return Err(value_error("Standalone Builder chats use insert_builder_chat"));
    };
    if table_exists(connection, "standalone_builder_chats")?
        && first(
            connection,
            "SELECT 1 FROM standalone_builder_chats WHERE session_id = ?",
            &[&fields.session_id],
        )?
        .is_some()
    {
        return Err(authority("IdempotencyConflict", "Agent session identity collides with a Builder chat"));
    }
    let Some((columns, values)) =
        first(connection, "SELECT lifecycle, writer FROM campaigns WHERE campaign_id = ?", &[&campaign_id])?
    else {
        return Err(rejected("Campaign is unavailable", "campaign_unavailable"));
    };
    let campaign = Named { columns: &columns, values };
    if campaign.text("lifecycle")? != LifecycleState::Active.as_str() {
        return Err(rejected(
            "Campaign lifecycle does not admit an active agent session",
            "command_rejected",
        ));
    }
    if campaign.text("writer")? != fields.role.as_str() {
        return Err(rejected("Agent session role is not the campaign writer", "wrong_writer"));
    }
    if let Some(parent_id) = fields.parent_session_id.as_deref() {
        let parent = read_session(connection, parent_id)?;
        if parent.campaign_id() != Some(campaign_id)
            || parent.role() != WriterRole::Builder
            || fields.role != WriterRole::Builder
        {
            return Err(rejected("Builder child session parent is incompatible", "command_rejected"));
        }
    }
    if let Some((columns, values)) =
        first(connection, "SELECT * FROM agent_sessions WHERE session_id = ?", &[&fields.session_id])?
    {
        let captured = session_from_row(&Named { columns: &columns, values })?;
        if &captured != record {
            return Err(authority("IdempotencyConflict", "Agent session identity payload does not match"));
        }
        return Ok(captured);
    }
    let scope = Json::Array(fields.scope.iter().cloned().map(Json::String).collect());
    connection.execute(
        "INSERT INTO agent_sessions \
         (session_id, campaign_id, role, provider_id, model_id, status, \
         parent_session_id, selected_skill, scope_json, payload_sha256) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        params![
            fields.session_id,
            campaign_id,
            fields.role.as_str(),
            fields.provider_id,
            fields.model_id,
            fields.status.as_str(),
            fields.parent_session_id,
            fields.selected_skill,
            dump(&scope),
            agent_session_payload_sha256(record)
        ],
    )?;
    if fields.reasoning_effort.is_some() || fields.reasoning_effort_once.is_some() {
        connection.execute(
            "UPDATE agent_sessions SET reasoning_effort = ?, reasoning_effort_once = ? WHERE session_id = ?",
            params![fields.reasoning_effort, fields.reasoning_effort_once, fields.session_id],
        )?;
    }
    Ok(record.clone())
}

/// `builder_workspace_migration._is_link_or_reparse`.
fn is_link_or_reparse(path: &Path) -> std::io::Result<bool> {
    let metadata = std::fs::symlink_metadata(path)?;
    let link = metadata.file_type().is_symlink();
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        Ok(link || metadata.file_attributes() & 0x400 != 0)
    }
    #[cfg(not(windows))]
    {
        Ok(link)
    }
}

/// `PureWindowsPath(session_id).is_reserved()` for an identity holding no separator or colon
/// (CPython 3.12.11, `Lib/pathlib.py`): the part before the first `.`, right-stripped of spaces
/// and upper-cased, is one of Windows' reserved device names.
fn is_reserved_windows_name(session_id: &str) -> bool {
    const RESERVED: [&str; 30] = [
        "AUX",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "COM\u{b2}",
        "COM\u{b3}",
        "COM\u{b9}",
        "CON",
        "CONIN$",
        "CONOUT$",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
        "LPT\u{b2}",
        "LPT\u{b3}",
        "LPT\u{b9}",
        "NUL",
        "PRN",
    ];
    if session_id.is_empty() || session_id == "." {
        return false;
    }
    let name = session_id.split('.').next().unwrap_or_default().trim_end_matches(' ');
    RESERVED.contains(&pystr::upper(name).as_str())
}

/// `builder_workspace_migration._builder_workspace_path`.
fn builder_workspace_path(storage_root: &Path, session_id: &str) -> StoreResult<PathBuf> {
    if session_id.is_empty()
        || session_id.ends_with(['.', ' '])
        || (!session_id.contains(['/', '\\', ':']) && is_reserved_windows_name(session_id))
        || session_id.contains(['/', '\\', ':'])
    {
        return Err(rejected("Builder workspace identity is not a directory name", "command_rejected"));
    }
    let root = crate::store::resolve(storage_root);
    let target = root.join("builder-chats").join(session_id);
    if !crate::store::resolve(&target).starts_with(&root) {
        return Err(rejected("Builder workspace path leaves application storage", "command_rejected"));
    }
    let mut ancestor = target.clone();
    while ancestor != root {
        if std::fs::symlink_metadata(&ancestor).is_ok() && is_link_or_reparse(&ancestor)? {
            return Err(rejected(
                "Builder workspace deletion cannot follow linked directories",
                "command_rejected",
            ));
        }
        match ancestor.parent() {
            Some(parent) => ancestor = parent.to_path_buf(),
            None => break,
        }
    }
    Ok(target)
}

/// `builder_workspace_migration.remove_builder_workspace`.
fn remove_builder_workspace(storage_root: &Path, session_id: &str) -> StoreResult<()> {
    let target = builder_workspace_path(storage_root, session_id)?;
    if std::fs::symlink_metadata(&target).is_err() {
        return Ok(());
    }
    let mut pending = vec![target.clone()];
    while let Some(directory) = pending.pop() {
        for entry in std::fs::read_dir(&directory)? {
            let path = entry?.path();
            if is_link_or_reparse(&path)? {
                return Err(rejected(
                    "Builder workspace deletion cannot follow linked files",
                    "command_rejected",
                ));
            }
            if std::fs::symlink_metadata(&path)?.is_dir() {
                pending.push(path);
            }
        }
    }
    std::fs::remove_dir_all(&target).map_err(|_| {
        rejected(
            "Some files could not be removed. The chat is still saved; retry deletion.",
            "builder_workspace_cleanup_incomplete",
        )
    })
}

impl AuthorityStore {
    /// `create_agent_session`.
    pub fn create_agent_session(&self, record: &SessionRecord) -> StoreResult<SessionRecord> {
        self.ensure_session_schema()?;
        self.in_immediate(|connection| {
            let fields = record.fields();
            if fields.reasoning_effort.is_some() || fields.reasoning_effort_once.is_some() {
                self.ensure_campaign_schema(connection)?;
            }
            insert_session(connection, record)
        })
    }

    /// `create_builder_chat`.
    pub fn create_builder_chat(
        &self,
        session_id: &str,
        provider_id: &str,
        model_id: &str,
        display_name: &str,
    ) -> StoreResult<BuilderChatRecord> {
        self.in_immediate(|connection| {
            self.ensure_campaign_schema(connection)?;
            insert_builder_chat(connection, session_id, provider_id, model_id, display_name)
        })
    }

    /// `builder_chat_index`.
    pub fn builder_chat_index(&self) -> StoreResult<Vec<BuilderChatRecord>> {
        self.in_immediate(|connection| {
            self.ensure_campaign_schema(connection)?;
            read_builder_chats(connection)
        })
    }

    /// `rename_builder_chat`.
    pub fn rename_builder_chat(
        &self,
        session_id: &str,
        display_name: &str,
        automatic: bool,
    ) -> StoreResult<BuilderChatRecord> {
        self.in_immediate(|connection| {
            self.ensure_campaign_schema(connection)?;
            rename_builder_chat(connection, session_id, display_name, automatic)
        })
    }

    /// `delete_builder_chat`: the chat and its workspace go together, or neither does.
    pub fn delete_builder_chat(&self, session_id: &str) -> StoreResult<BuilderChatRecord> {
        let storage_root = self.database_path().parent().map(Path::to_path_buf).unwrap_or_default();
        self.in_immediate(|connection| {
            self.ensure_campaign_schema(connection)?;
            let current = read_builder_chat(connection, session_id)?;
            if connection
                .execute("DELETE FROM standalone_builder_chats WHERE session_id = ?", [session_id])?
                != 1
            {
                return Err(rejected("Standalone Builder chat is unavailable", "command_rejected"));
            }
            remove_builder_workspace(&storage_root, session_id)?;
            Ok(current)
        })
    }

    /// `update_agent_session_reasoning`.
    pub fn update_agent_session_reasoning(
        &self,
        session_id: &str,
        effort: Option<&str>,
        once: Option<&str>,
    ) -> StoreResult<SessionRecord> {
        self.in_immediate(|connection| {
            self.ensure_campaign_schema(connection)?;
            update_preferences(connection, session_id, effort, once)
        })
    }

    /// `update_agent_session_model`.
    pub fn update_agent_session_model(
        &self,
        session_id: &str,
        provider_id: &str,
        model_id: &str,
    ) -> StoreResult<SessionRecord> {
        self.ensure_session_schema()?;
        self.in_immediate(|connection| update_session_model(connection, session_id, provider_id, model_id))
    }

    /// `resume_agent_session`: reactivate the exact stopped top-level writer session; a DM
    /// session gets an open TURN, and retryable interruptions move to it.
    pub fn resume_agent_session(&self, session_id: &str) -> StoreResult<SessionRecord> {
        self.ensure_session_schema()?;
        self.in_immediate(|connection| {
            let current = read_session(connection, session_id)?;
            let fields = current.fields();
            let Some(campaign_id) = fields.campaign_id.clone() else {
                if fields.parent_session_id.is_some() {
                    return Err(rejected("Standalone Builder chat cannot be a child session", "command_rejected"));
                }
                return restore_stopped_session(connection, session_id);
            };
            let campaign = first(connection, "SELECT lifecycle, writer, revision FROM campaigns WHERE campaign_id = ?", &[&campaign_id])?;
            let owns = match &campaign {
                Some((columns, values)) => {
                    let row = Named { columns, values: values.clone() };
                    fields.parent_session_id.is_none()
                        && row.text("lifecycle")? == LifecycleState::Active.as_str()
                        && row.text("writer")? == fields.role.as_str()
                }
                None => false,
            };
            if !owns {
                return Err(rejected("Stopped session no longer owns active campaign authority", "command_rejected"));
            }
            let result = restore_stopped_session(connection, session_id)?;
            if fields.role == WriterRole::Dm {
                let open = first(connection, "SELECT turn_id FROM turns WHERE campaign_id = ? AND status = 'open'", &[&campaign_id])?;
                let active_turn_id = match open {
                    Some((columns, values)) => Named { columns: &columns, values }.text("turn_id")?,
                    None => {
                        let (columns, values) = campaign.expect("checked above");
                        let revision = Named { columns: &columns, values }.int("revision")?;
                        let mut token = [0_u8; 16];
                        getrandom::getrandom(&mut token).map_err(|error| StoreError::Other(error.to_string()))?;
                        let turn_id = format!("turn-dm-{}", hex::encode(token));
                        connection.execute(
                            "INSERT INTO turns(campaign_id, turn_id, status, created_revision) VALUES (?, ?, 'open', ?)",
                            params![campaign_id, turn_id, revision],
                        )?;
                        turn_id
                    }
                };
                connection.execute(
                    "UPDATE narration_interruptions SET turn_id = ? \
                     WHERE campaign_id = ? AND status = 'retryable' AND last_action_id IS NULL AND turn_id != ?",
                    params![active_turn_id, campaign_id, active_turn_id],
                )?;
            }
            Ok(result)
        })
    }

    /// `session_context`: the session and every saved message.
    pub fn session_context(&self, session_id: &str) -> StoreResult<SessionContext> {
        self.ensure_session_schema()?;
        let connection = self.connect()?;
        connection.execute_batch("BEGIN")?;
        let result = (|| {
            Ok(SessionContext {
                session: read_session(&connection, session_id)?,
                messages: read_messages(&connection, session_id)?,
            })
        })();
        let _ = connection.execute_batch(if result.is_ok() { "COMMIT" } else { "ROLLBACK" });
        result
    }

    /// `update_agent_session_status`.
    pub fn update_agent_session_status(
        &self,
        session_id: &str,
        status: SessionStatus,
    ) -> StoreResult<SessionRecord> {
        self.ensure_session_schema()?;
        self.in_immediate(|connection| update_session_status(connection, session_id, status))
    }

    /// `append_agent_message`.
    pub fn append_agent_message(
        &self,
        session_id: &str,
        message_id: &str,
        kind: SessionMessageKind,
        content: &JsonMap,
        canonical_sequence: Option<i64>,
    ) -> StoreResult<SessionMessage> {
        self.ensure_session_schema()?;
        self.in_immediate(|connection| {
            append_message(connection, session_id, message_id, kind, content, canonical_sequence)
        })
    }
}
