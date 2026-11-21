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
//! Part 2 adds `_with_mechanics_activity`, `read_activity_basis`, `read_history_page`,
//! `plan_from_row`, `read_plan`, `read_handoff`, `require_human_review`, `insert_child_session` and
//! `return_child_evidence`, with their store operations.

use crate::actions::{dump, rejected, require_active_campaign};
use crate::campaigns::{named_rows, parse, session_from_row, session_payload, Named};
use crate::error::{value_error, StoreError, StoreResult};
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use rusqlite::{params, Connection, OptionalExtension};
use std::path::{Path, PathBuf};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pystr, Json, JsonMap};
use sylvia_domain::authority::{owned_payload, LifecycleState, WriterRole};
use sylvia_domain::session::{
    agent_session_payload_sha256, builder_chat_payload_sha256, BuilderChatRecord, BuilderDMHandoff,
    CampaignPlan, CampaignPlanFields, ChildEvidence, PlanAcceptanceStatus, PlanValidationOutcome,
    SessionActivityBasis, SessionContext, SessionHistoryPage, SessionMessage, SessionMessageKind,
    SessionRecord, SessionRecordFields, SessionStatus,
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

// ---- Sessions part 2: display pages, activity bases, plans, review and child sessions ----

/// `_with_mechanics_activity`: attach the saved execution link to each `tool_result` row, even
/// when its evidence is on an older page.
fn with_mechanics_activity(
    connection: &Connection,
    session: &SessionRecord,
    messages: Vec<SessionMessage>,
) -> StoreResult<Vec<SessionMessage>> {
    let sequences: Vec<i64> = messages
        .iter()
        .filter(|message| {
            message.kind() == SessionMessageKind::CanonicalReference
                && message.content().get("event_type") == Some(&Json::from("tool_result"))
        })
        .filter_map(SessionMessage::canonical_sequence)
        .collect();
    let Some(campaign_id) = session.campaign_id() else { return Ok(messages) };
    if sequences.is_empty() {
        return Ok(messages);
    }
    let placeholders = vec!["?"; sequences.len()].join(",");
    let session_id = &session.fields().session_id;
    let mut values: Vec<&dyn rusqlite::ToSql> = vec![session_id, &campaign_id];
    values.extend(sequences.iter().map(|sequence| sequence as &dyn rusqlite::ToSql));
    let (columns, rows) = named_rows(
        connection,
        &format!(
            "SELECT event.sequence, (SELECT json_extract(message.content_json, '$.activity') \
             FROM agent_session_messages AS message WHERE message.session_id = ? \
             AND message.kind = 'evidence' \
             AND json_extract(message.content_json, '$.kind') = 'mechanics_activity' \
             AND json_extract(message.content_json, '$.action_id') = event.action_id \
             ORDER BY message.sequence LIMIT 1) AS activity \
             FROM canonical_events AS event WHERE event.campaign_id = ? \
             AND event.sequence IN ({placeholders}) AND event.event_type = 'tool_result'"
        ),
        &values,
    )?;
    let mut activity: Vec<(i64, JsonMap)> = Vec::new();
    for values in rows {
        let row = Named { columns: &columns, values };
        if row.get("activity")? != &Value::Null {
            activity.push((row.int("sequence")?, session_payload(&row.text("activity")?)?));
        }
    }
    messages
        .into_iter()
        .map(|message| {
            let found = message
                .canonical_sequence()
                .and_then(|sequence| activity.iter().rev().find(|(key, _)| *key == sequence));
            let Some((_, saved)) = found else { return Ok(message) };
            let mut content = message.content().clone();
            content.insert("activity".into(), Json::Object(saved.clone()));
            Ok(SessionMessage::new(
                message.session_id().to_owned(),
                message.sequence(),
                message.message_id().to_owned(),
                message.kind(),
                &Json::Object(owned_payload(&Json::Object(content))?),
                message.canonical_sequence(),
            )?)
        })
        .collect()
}

fn messages_of(
    connection: &Connection,
    query: &str,
    values: &[&dyn rusqlite::ToSql],
) -> StoreResult<Vec<SessionMessage>> {
    let (columns, rows) = named_rows(connection, query, values)?;
    rows.into_iter().map(|values| message_from_row(&Named { columns: &columns, values })).collect()
}

fn optional_int(
    connection: &Connection,
    query: &str,
    values: &[&dyn rusqlite::ToSql],
) -> StoreResult<Option<i64>> {
    let value: Value = connection.query_row(query, values, |row| row.get(0))?;
    Ok(crate::pytext::py_int(&value))
}

/// `sqlite_session.read_activity_basis`: only the selected accepted input and its terminal
/// outcome.
fn read_activity_basis(
    connection: &Connection,
    session_id: &str,
    attempt_id: Option<&str>,
) -> StoreResult<SessionActivityBasis> {
    let session = read_session(connection, session_id)?;
    let table = message_table(&session);
    let compaction = "json_extract(content_json, '$.kind') = 'context_compaction_requested'";
    let has_compaction = first(
        connection,
        &format!("SELECT 1 FROM {table} WHERE session_id = ? AND {compaction} LIMIT 1"),
        &[&session_id],
    )?
    .is_some();
    let inputs = if session.role() == WriterRole::Builder {
        format!("({compaction} OR kind = 'user' OR json_extract(content_json, '$.kind') = 'builder_start')")
    } else {
        compaction.to_owned()
    };
    let matching =
        if attempt_id.is_some() { " AND json_extract(content_json, '$.attempt_id') = ?" } else { "" };
    let query = format!(
        "SELECT * FROM {table} WHERE session_id = ? AND {inputs} \
         AND json_type(content_json, '$.attempt_id') = 'text'{matching} ORDER BY sequence DESC LIMIT 1"
    );
    let accepted = match attempt_id {
        None => messages_of(connection, &query, &[&session_id])?,
        Some(attempt) => messages_of(connection, &query, &[&session_id, &attempt])?,
    }
    .into_iter()
    .next();
    let selected = match (attempt_id, &accepted) {
        (Some(attempt), _) => Some(attempt.to_owned()),
        (None, Some(message)) => Some(sylvia_domain::pyops::py_str(sylvia_domain::pyops::py_item(
            &Json::Object(message.content().clone()),
            "attempt_id",
        )?)),
        (None, None) => None,
    };
    let terminal = match &selected {
        None => None,
        Some(attempt) => messages_of(
            connection,
            &format!(
                "SELECT * FROM {table} WHERE session_id = ? AND kind = 'evidence' \
                 AND json_extract(content_json, '$.kind') IN \
                 ('builder_attempt_terminal', 'context_compaction_terminal') \
                 AND json_extract(content_json, '$.attempt_id') = ? ORDER BY sequence DESC LIMIT 1"
            ),
            &[&session_id, attempt],
        )?
        .into_iter()
        .next(),
    };
    Ok(SessionActivityBasis { session, accepted, terminal, has_compaction })
}

/// `sqlite_session.read_history_page`: the newest rows before a stable cursor, without decoding
/// the rest of history.
fn read_history_page(
    connection: &Connection,
    session_id: &str,
    before_sequence: Option<i64>,
    limit: i64,
) -> StoreResult<SessionHistoryPage> {
    if !(1..=200).contains(&limit) {
        return Err(value_error("History page limit must be an integer from 1 to 200"));
    }
    if before_sequence.is_some_and(|cursor| cursor <= 0) {
        return Err(value_error("History cursor must be a positive integer"));
    }
    let session = read_session(connection, session_id)?;
    let table = message_table(&session);
    let fetch = limit + 1;
    let rows = match before_sequence {
        None => messages_of(
            connection,
            &format!("SELECT * FROM {table} WHERE session_id = ? ORDER BY sequence DESC LIMIT ?"),
            &[&session_id, &fetch],
        )?,
        Some(cursor) => messages_of(
            connection,
            &format!(
                "SELECT * FROM {table} WHERE session_id = ? AND sequence < ? ORDER BY sequence DESC LIMIT ?"
            ),
            &[&session_id, &cursor, &fetch],
        )?,
    };
    let more = i64::try_from(rows.len()).unwrap_or(i64::MAX) > limit;
    let mut page: Vec<SessionMessage> =
        rows.into_iter().take(usize::try_from(limit).unwrap_or(usize::MAX)).collect();
    page.reverse();
    let messages = with_mechanics_activity(connection, &session, page)?;
    let latest = optional_int(
        connection,
        &format!("SELECT MAX(sequence) FROM {table} WHERE session_id = ?"),
        &[&session_id],
    )?;
    let latest_user = optional_int(
        connection,
        &format!("SELECT MAX(sequence) FROM {table} WHERE session_id = ? AND kind = ?"),
        &[&session_id, &SessionMessageKind::User.as_str()],
    )?;
    // A display cursor must not truncate the current form: decode only its tool evidence.
    let builder_question_messages = if session.role() == WriterRole::Builder {
        messages_of(
            connection,
            &format!(
                "SELECT * FROM {table} WHERE session_id = ? AND sequence > ? \
                 AND kind = 'evidence' \
                 AND json_extract(content_json, '$.kind') = 'builder_tool_result' \
                 AND json_extract(content_json, '$.tool_name') = 'ask_question' ORDER BY sequence"
            ),
            &[&session_id, &latest_user.unwrap_or(0)],
        )?
    } else {
        Vec::new()
    };
    let launch_messages = messages_of(
        connection,
        &format!(
            "SELECT * FROM {table} WHERE session_id = ? AND kind = 'evidence' \
             AND json_extract(content_json, '$.kind') IN \
             ('creation_subflow_link', 'builder_launch_context') ORDER BY sequence DESC LIMIT 2"
        ),
        &[&session_id],
    )?;
    let latest_canonical = optional_int(
        connection,
        &format!("SELECT MAX(canonical_sequence) FROM {table} WHERE session_id = ?"),
        &[&session_id],
    )?;
    let story = match session.campaign_id() {
        Some(campaign_id) if session.role() == WriterRole::Dm => Some(campaign_id.to_owned()),
        _ => None,
    };
    let (act_markers, campaign_ending, campaign_endings) = match &story {
        Some(campaign_id) => (
            crate::continuity::read_act_markers(connection, session_id, campaign_id)?,
            crate::continuity::read_campaign_ending(connection, session_id, campaign_id)?,
            crate::continuity::read_campaign_endings(connection, session_id, campaign_id)?,
        ),
        None => (Vec::new(), None, Vec::new()),
    };
    Ok(SessionHistoryPage {
        next_before_sequence: if more { messages.first().map(SessionMessage::sequence) } else { None },
        session,
        messages,
        latest_sequence: latest,
        latest_user_sequence: latest_user,
        launch_messages,
        latest_canonical_sequence: latest_canonical,
        act_markers,
        campaign_ending,
        campaign_endings,
        builder_question_messages,
    })
}

/// `sqlite_session.plan_from_row`.
pub(crate) fn plan_from_row(row: &Named<'_>) -> StoreResult<CampaignPlan> {
    let provenance_text = row.text("provenance_json")?;
    let provenance = match crate::sequel::json_loads(&provenance_text)? {
        parsed if crate::actions::dump(&parsed) != provenance_text => {
            return Err(value_error("Stored session JSON is not canonical"))
        }
        Json::Array(items) => items.iter().map(crate::campaigns::json_str).collect(),
        _ => return Err(value_error("Stored Campaign Plan provenance is not an array")),
    };
    Ok(CampaignPlan::new(CampaignPlanFields {
        plan_id: row.text("plan_id")?,
        campaign_id: row.text("campaign_id")?,
        package_lock_id: row.text("package_lock_id")?,
        source_session_id: row.text("source_session_id")?,
        content_sha256: row.text("content_sha256")?,
        content: Json::Object(session_payload(&row.text("content_json")?)?),
        provenance_evidence_ids: provenance,
        validation_outcome: parse(
            &row.text("validation_outcome")?,
            PlanValidationOutcome::parse,
            "PlanValidationOutcome",
        )?,
        acceptance_status: parse(
            &row.text("acceptance_status")?,
            PlanAcceptanceStatus::parse,
            "PlanAcceptanceStatus",
        )?,
        plan_revision: row.int("plan_revision")?,
        resulting_campaign_revision: row.int("resulting_campaign_revision")?,
        payload_sha256: row.text("payload_sha256")?,
    })?)
}

/// `sqlite_session.read_plan`.
fn read_plan(connection: &Connection, plan_id: &str) -> StoreResult<Option<CampaignPlan>> {
    match first(connection, "SELECT * FROM campaign_plans WHERE plan_id = ?", &[&plan_id])? {
        Some((columns, values)) => plan_from_row(&Named { columns: &columns, values }).map(Some),
        None => Ok(None),
    }
}

/// `sqlite_session.read_handoff`.
fn read_handoff(connection: &Connection, handoff_id: &str) -> StoreResult<Option<BuilderDMHandoff>> {
    match first(connection, "SELECT * FROM builder_dm_handoffs WHERE handoff_id = ?", &[&handoff_id])? {
        Some((columns, values)) => {
            crate::campaigns::handoff_from_row(&Named { columns: &columns, values }).map(Some)
        }
        None => Ok(None),
    }
}

/// `sqlite_session.require_human_review`.
fn require_human_review(
    connection: &Connection,
    session_id: &str,
    review_id: &str,
    content: &JsonMap,
) -> StoreResult<SessionRecord> {
    let current = read_session(connection, session_id)?;
    if current.role() != WriterRole::Builder {
        return Err(rejected("Only Builder sessions can require construction review", "command_rejected"));
    }
    if !matches!(current.fields().status, SessionStatus::Active | SessionStatus::ReviewRequired) {
        return Err(rejected("Builder session is not review-admissible", "command_rejected"));
    }
    append_message(connection, session_id, review_id, SessionMessageKind::HumanReview, content, None)?;
    update_session_status(connection, session_id, SessionStatus::ReviewRequired)
}

/// `sqlite_session.insert_child_session`, with Python's bound of three children.
fn insert_child_session(connection: &Connection, record: &SessionRecord) -> StoreResult<SessionRecord> {
    const MAXIMUM_CHILDREN: i64 = 3;
    let fields = record.fields();
    if let Some((columns, values)) =
        first(connection, "SELECT * FROM agent_sessions WHERE session_id = ?", &[&fields.session_id])?
    {
        let captured = session_from_row(&Named { columns: &columns, values })?;
        if &captured != record {
            return Err(authority("IdempotencyConflict", "Agent session identity payload does not match"));
        }
        return Ok(captured);
    }
    let Some(parent_id) = fields.parent_session_id.as_deref() else {
        return Err(value_error("Child session requires a parent_session_id"));
    };
    let parent = read_session(connection, parent_id)?;
    if parent.fields().parent_session_id.is_some() {
        return Err(rejected("Builder child sessions cannot create nested children", "command_rejected"));
    }
    if parent.role() != WriterRole::Builder || parent.fields().status != SessionStatus::Active {
        return Err(rejected("Builder parent session is not active", "command_rejected"));
    }
    let children = optional_int(
        connection,
        "SELECT COUNT(*) FROM agent_sessions WHERE parent_session_id = ?",
        &[&parent.fields().session_id],
    )?
    .unwrap_or(0);
    if children >= MAXIMUM_CHILDREN {
        return Err(rejected("Builder child sessions are bounded at three", "command_rejected"));
    }
    insert_session(connection, record)
}

/// `sqlite_session.return_child_evidence`.
fn return_child_evidence(
    connection: &Connection,
    evidence: &ChildEvidence,
) -> StoreResult<(ChildEvidence, SessionRecord)> {
    let child = read_session(connection, &evidence.child_session_id)?;
    if child.fields().parent_session_id.as_deref() != Some(evidence.parent_session_id.as_str()) {
        return Err(rejected("Child evidence parent identity does not match", "command_rejected"));
    }
    if child.role() != WriterRole::Builder {
        return Err(rejected("Only Builder children return construction evidence", "command_rejected"));
    }
    if !matches!(child.fields().status, SessionStatus::Active | SessionStatus::Completed) {
        return Err(rejected("Builder child session cannot return evidence", "command_rejected"));
    }
    append_message(
        connection,
        &evidence.parent_session_id,
        &evidence.evidence_id,
        SessionMessageKind::Evidence,
        &evidence.payload(),
        None,
    )?;
    let completed = update_session_status(connection, &evidence.child_session_id, SessionStatus::Completed)?;
    Ok((evidence.clone(), completed))
}

impl AuthorityStore {
    /// `session_activity_basis`: attempt recovery facts from one consistent saved state.
    pub fn session_activity_basis(
        &self,
        session_id: &str,
        attempt_id: Option<&str>,
    ) -> StoreResult<SessionActivityBasis> {
        self.ensure_session_schema()?;
        let connection = self.connect()?;
        connection.execute_batch("BEGIN")?;
        let result = read_activity_basis(&connection, session_id, attempt_id);
        let _ = connection.execute_batch(if result.is_ok() { "COMMIT" } else { "ROLLBACK" });
        result
    }

    /// `session_history_page`: page rows and current sequence metadata in one read transaction.
    pub fn session_history_page(
        &self,
        session_id: &str,
        before_sequence: Option<i64>,
        limit: i64,
    ) -> StoreResult<SessionHistoryPage> {
        self.ensure_session_schema()?;
        let connection = self.connect()?;
        connection.execute_batch("BEGIN")?;
        let result = read_history_page(&connection, session_id, before_sequence, limit);
        let _ = connection.execute_batch(if result.is_ok() { "COMMIT" } else { "ROLLBACK" });
        result
    }

    /// `require_agent_human_review`.
    pub fn require_agent_human_review(
        &self,
        session_id: &str,
        review_id: &str,
        content: &JsonMap,
    ) -> StoreResult<SessionRecord> {
        self.ensure_session_schema()?;
        self.in_immediate(|connection| require_human_review(connection, session_id, review_id, content))
    }

    /// `create_builder_child_session`.
    pub fn create_builder_child_session(&self, record: &SessionRecord) -> StoreResult<SessionRecord> {
        self.ensure_session_schema()?;
        self.in_immediate(|connection| insert_child_session(connection, record))
    }

    /// `return_builder_child_evidence`: the evidence on the parent, and the completed child.
    pub fn return_builder_child_evidence(
        &self,
        evidence: &ChildEvidence,
    ) -> StoreResult<(ChildEvidence, SessionRecord)> {
        self.ensure_session_schema()?;
        self.in_immediate(|connection| return_child_evidence(connection, evidence))
    }

    /// `campaign_plan_hit`.
    pub fn campaign_plan_hit(&self, plan_id: &str) -> StoreResult<Option<CampaignPlan>> {
        self.ensure_session_schema()?;
        read_plan(&self.connect()?, plan_id)
    }

    /// `handoff_hit`.
    pub fn handoff_hit(&self, handoff_id: &str) -> StoreResult<Option<BuilderDMHandoff>> {
        self.ensure_session_schema()?;
        read_handoff(&self.connect()?, handoff_id)
    }

    /// `accepted_campaign_plan`: the latest accepted revision.
    pub fn accepted_campaign_plan(&self, campaign_id: &str) -> StoreResult<Option<CampaignPlan>> {
        self.ensure_session_schema()?;
        let connection = self.connect()?;
        match first(
            &connection,
            "SELECT * FROM campaign_plans WHERE campaign_id = ? \
             AND acceptance_status = 'accepted' ORDER BY plan_revision DESC LIMIT 1",
            &[&campaign_id],
        )? {
            Some((columns, values)) => plan_from_row(&Named { columns: &columns, values }).map(Some),
            None => Ok(None),
        }
    }

    /// `campaign_adventures`: the accepted adventure lineage, oldest first.
    pub fn campaign_adventures(&self, campaign_id: &str) -> StoreResult<Vec<CampaignPlan>> {
        let connection = self.connect_read_only()?;
        crate::sequel::adventure_rows(&connection, campaign_id)?
            .iter()
            .map(|row| plan_from_row(&row.named()))
            .collect()
    }
}
