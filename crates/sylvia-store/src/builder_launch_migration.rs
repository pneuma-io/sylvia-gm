//! The owner-startup migration of former campaign-creation content chats
//! (`sqlite_builder_launch.py` and `builder_workspace_migration.py`, the parts the migration
//! uses). Lane 5 writes this module beside lane 3's store at the manager's direction.
//!
//! [`AuthorityStore::legacy_builder_contexts`] finds them; `builder_workspace_migration`'s
//! [`retain_legacy_workspace`] copies a chat's shared campaign workspace to its own folder; and
//! [`AuthorityStore::detach_legacy_builder_chat`] then moves the chat to independent storage with
//! its migration evidence.

use crate::actions::rejected;
use crate::error::{value_error, StoreError, StoreResult};
use crate::sessions::{insert_builder_chat, read_messages, read_session, update_session_status};
use crate::store::AuthorityStore;
use std::path::{Path, PathBuf};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{owned_payload, WriterRole};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::py_eq;
use sylvia_domain::session::{SessionContext, SessionMessageKind, SessionStatus};

fn authority(kind: &'static str, message: &str) -> StoreError {
    StoreError::Coded(crate::error::authority_coded(kind, message))
}

/// `CONTENT_PURPOSES`.
pub const CONTENT_PURPOSES: [&str; 5] = ["ruleset", "setting", "plan", "player_character", "companion"];

/// The `kind` of a former content child's link evidence.
const LINK_KIND: &str = "creation_subflow_link";

impl AuthorityStore {
    /// `legacy_builder_contexts()`: the former reusable-content children, oldest first, read
    /// without creating session tables in a fresh database (other delegated work stays attached).
    pub fn legacy_builder_contexts(&self) -> StoreResult<Vec<SessionContext>> {
        let connection = self.connect_read_only()?;
        connection.execute_batch("BEGIN")?;
        let result = (|| {
            let exists = connection
                .query_row(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'agent_sessions'",
                    [],
                    |_| Ok(()),
                )
                .map(|()| true)
                .or_else(|error| match error {
                    rusqlite::Error::QueryReturnedNoRows => Ok(false),
                    other => Err(other),
                })?;
            if !exists {
                return Ok(Vec::new());
            }
            legacy_contexts(&connection)
        })();
        // Python closes the connection without committing a read-only transaction.
        let _ = connection.execute_batch("ROLLBACK");
        result
    }
}

/// `sqlite_builder_launch.legacy_contexts(connection)`.
fn legacy_contexts(connection: &rusqlite::Connection) -> StoreResult<Vec<SessionContext>> {
    let mut statement = connection.prepare(
        "SELECT session.session_id FROM agent_sessions AS session \
         WHERE session.role = 'builder' AND EXISTS (\
         SELECT 1 FROM agent_session_messages AS message \
         WHERE message.session_id = session.session_id \
         AND message.kind = 'evidence' \
         AND json_extract(message.content_json, '$.kind') = 'creation_subflow_link') \
         ORDER BY session.rowid",
    )?;
    let ids: Vec<String> =
        statement.query_map([], |row| row.get::<_, String>(0))?.collect::<Result<_, _>>()?;
    let mut contexts = Vec::new();
    for session_id in ids {
        let context = SessionContext {
            session: read_session(connection, &session_id)?,
            messages: read_messages(connection, &session_id)?,
        };
        let mut content_link = false;
        for message in &context.messages {
            let content = message.content();
            if message.kind() != SessionMessageKind::Evidence
                || content.get("kind") != Some(&Json::String(LINK_KIND.into()))
            {
                continue;
            }
            match content.get("purpose") {
                Some(Json::String(purpose)) if CONTENT_PURPOSES.contains(&purpose.as_str()) => {
                    content_link = true;
                    break;
                }
                // `purpose in CONTENT_PURPOSES`: a list or dict is unhashable (`TypeError`).
                Some(Json::Array(_) | Json::Object(_)) => {
                    let kind = if matches!(content.get("purpose"), Some(Json::Array(_))) {
                        "FrozenJsonList"
                    } else {
                        "FrozenJsonDict"
                    };
                    return Err(StoreError::Other(format!("unhashable type: '{kind}'")));
                }
                _ => {}
            }
        }
        if content_link {
            contexts.push(context);
        }
    }
    Ok(contexts)
}

/// The two message tables migration evidence may be appended to.
const MIGRATION_TABLES: [&str; 2] = ["agent_session_messages", "standalone_builder_chat_messages"];

/// `_append_migration_evidence(connection, table=, session_id=, message_id=, content=)`: one
/// `evidence` message after the session's last, without changing earlier ones.
fn append_migration_evidence(
    connection: &rusqlite::Connection,
    table: &str,
    session_id: &str,
    message_id: &str,
    content: &JsonMap,
) -> StoreResult<()> {
    if !MIGRATION_TABLES.contains(&table) {
        return Err(value_error("Migration message table is invalid"));
    }
    let text = canonical_text(&Json::Object(content.clone()))?;
    connection.execute(
        &format!(
            "INSERT INTO {table} (session_id, sequence, message_id, kind, content_json, canonical_sequence) \
             SELECT ?, COALESCE(MAX(sequence), 0) + 1, ?, 'evidence', ?, NULL FROM {table} WHERE session_id = ?"
        ),
        rusqlite::params![session_id, message_id, text, session_id],
    )?;
    Ok(())
}

/// `canonical_json_bytes(value).decode("utf-8")`.
fn canonical_text(value: &Json) -> StoreResult<String> {
    String::from_utf8(canonical_json_bytes(value)).map_err(|_| value_error("canonical JSON is not UTF-8"))
}

/// `value in CONTENT_PURPOSES`, as Python evaluates it: a list or dict is unhashable.
fn is_content_purpose(value: Option<&Json>) -> StoreResult<bool> {
    match value {
        Some(Json::String(purpose)) => Ok(CONTENT_PURPOSES.contains(&purpose.as_str())),
        Some(Json::Array(_)) => Err(StoreError::Other("unhashable type: 'FrozenJsonList'".into())),
        Some(Json::Object(_)) => Err(StoreError::Other("unhashable type: 'FrozenJsonDict'".into())),
        _ => Ok(false),
    }
}

/// `int(value)` for a JSON value.
fn python_int(value: &Json) -> StoreResult<i64> {
    match value {
        Json::Bool(flag) => Ok(i64::from(*flag)),
        Json::Number(number) => {
            if let Some(integer) = number.as_i64() {
                return Ok(integer);
            }
            let float = number.as_f64().unwrap_or(f64::NAN);
            if !float.is_finite() {
                return Err(value_error("cannot convert float to integer"));
            }
            Ok(float.trunc() as i64)
        }
        Json::String(text) => sylvia_base::pystr::parse_int(text).map_err(|_| {
            value_error(format!("invalid literal for int() with base 10: {}", sylvia_base::pystr::repr(text)))
        }),
        _ => Err(StoreError::Other(
            "int() argument must be a string, a bytes-like object or a real number".into(),
        )),
    }
}

fn is_link(map: &JsonMap) -> bool {
    map.get("kind") == Some(&Json::String(LINK_KIND.into()))
}

/// `sqlite_builder_launch.detach_legacy_chat(connection, ...)`: move a former content subflow to
/// independent storage in the caller's transaction.
fn detach_legacy_chat(
    connection: &rusqlite::Connection,
    session_id: &str,
    expected_message_count: usize,
    launch_context: &JsonMap,
    display_name: &str,
) -> StoreResult<()> {
    let session = read_session(connection, session_id)?;
    let messages = read_messages(connection, session_id)?;
    let fields = session.fields();
    let Some(campaign_id) = fields.campaign_id.clone() else { return Ok(()) };
    // `or` short-circuits as Python's does: the purpose is tested last.
    if fields.role != WriterRole::Builder
        || messages.len() != expected_message_count
        || !is_content_purpose(launch_context.get("purpose"))?
    {
        return Err(authority("StaleRevision", "The legacy Builder chat changed before migration"));
    }
    let null = Json::Null;
    let campaign = Json::String(campaign_id.clone());
    let link = messages.iter().find(|message| is_link(message.content()));
    let parent_id = match link.map(|link| link.content()) {
        Some(content)
            if py_eq(
                content.get("purpose").unwrap_or(&null),
                launch_context.get("purpose").unwrap_or(&null),
            ) && py_eq(content.get("campaign_id").unwrap_or(&null), &campaign) =>
        {
            match content.get("parent_session_id") {
                Some(Json::String(parent)) => parent.clone(),
                _ => {
                    return Err(rejected(
                        "The legacy Builder launch does not match its saved context",
                        "command_rejected",
                    ))
                }
            }
        }
        _ => {
            return Err(rejected(
                "The legacy Builder launch does not match its saved context",
                "command_rejected",
            ))
        }
    };
    let mut statement = connection.prepare(
        "SELECT sequence, message_id, kind, content_json, canonical_sequence \
         FROM agent_session_messages WHERE session_id = ? ORDER BY sequence",
    )?;
    let rows: Vec<(i64, String, String, String, Option<i64>)> = statement
        .query_map([session_id], |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?, row.get(4)?)))?
        .collect::<Result<_, _>>()?;
    drop(statement);
    // `read_context(parent)`: the session, then its messages.
    let parent = read_session(connection, &parent_id)?;
    let parent_messages = read_messages(connection, &parent_id)?;
    let state = parent_messages
        .iter()
        .rev()
        .find(|message| {
            message.content().get("kind") == Some(&Json::String("campaign_interview_state".into()))
        })
        .map(|message| message.content().clone());

    connection.execute("DELETE FROM agent_sessions WHERE session_id = ?", [session_id])?;
    insert_builder_chat(connection, session_id, &fields.provider_id, &fields.model_id, display_name)?;
    for (sequence, message_id, kind, content, canonical_sequence) in rows {
        let (kind, content) = match canonical_sequence {
            // A historical event becomes retained reference data, never independent-chat game
            // authority.
            Some(canonical) => {
                let reference =
                    sylvia_base::pyjson::loads(&content).map_err(|error| value_error(format!("{error}")))?;
                let mut retained = JsonMap::new();
                retained.insert("kind".into(), Json::String("retained_campaign_reference".into()));
                retained.insert("campaign_id".into(), campaign.clone());
                retained.insert("canonical_sequence".into(), Json::from(canonical));
                retained.insert("reference".into(), reference);
                ("evidence".to_string(), canonical_text(&Json::Object(retained))?)
            }
            None => (kind, content),
        };
        connection.execute(
            "INSERT INTO standalone_builder_chat_messages \
             (session_id, sequence, message_id, kind, content_json, canonical_sequence) \
             VALUES (?, ?, ?, ?, ?, NULL)",
            rusqlite::params![session_id, sequence, message_id, kind, content],
        )?;
    }
    let suffix = sylvia_base::hash::sha256_hex(session_id.as_bytes())[..24].to_string();
    append_migration_evidence(
        connection,
        "standalone_builder_chat_messages",
        session_id,
        &format!("builder-migrated-{suffix}"),
        launch_context,
    )?;
    // Completion used to mean the subflow was closed; explicit Stop and review stay.
    if matches!(fields.status, SessionStatus::Stopped | SessionStatus::ReviewRequired) {
        update_session_status(connection, session_id, fields.status)?;
    }
    let Some(state) = state else { return Ok(()) };
    let active = state.get("active_subflow");
    let retained: Vec<Json> = match state.get("subflows") {
        None => Vec::new(),
        Some(Json::Array(items)) => items.clone(),
        Some(_) => return Err(StoreError::Other("'subflows' is not a list".into())),
    };
    let session_value = Json::String(session_id.to_owned());
    let active_matches = matches!(
        active,
        Some(Json::Object(map)) if py_eq(map.get("child_session_id").unwrap_or(&null), &session_value)
    );
    let mut remaining = Vec::new();
    for item in &retained {
        let Json::Object(map) = item else {
            return Err(StoreError::Other("a 'subflows' item has no attribute 'get'".into()));
        };
        if !py_eq(map.get("child_session_id").unwrap_or(&null), &session_value) {
            remaining.push(item.clone());
        }
    }
    if !active_matches && remaining.len() == retained.len() {
        return Ok(());
    }
    let revision = python_int(
        state
            .get("construction_revision")
            .ok_or_else(|| StoreError::Other("KeyError: 'construction_revision'".into()))?,
    )?;
    let mut detached = state.clone();
    detached.insert(
        "active_subflow".into(),
        if active_matches { Json::Null } else { active.cloned().unwrap_or(Json::Null) },
    );
    detached.insert("subflows".into(), Json::Array(remaining));
    detached.insert("construction_revision".into(), Json::from(revision + 1));
    detached.insert("last_operation_id".into(), Json::String(format!("builder-detached-{suffix}")));
    let detached = owned_payload(&Json::Object(detached))?;
    append_migration_evidence(
        connection,
        "agent_session_messages",
        parent.session_id(),
        &format!("builder-detached-{suffix}"),
        &detached,
    )
}

impl AuthorityStore {
    /// `detach_legacy_builder_chat(...)`: commit the relocation, after its independent workspace
    /// is retained ([`retain_legacy_workspace`]), in one verified owner transaction.
    pub fn detach_legacy_builder_chat(
        &self,
        session_id: &str,
        expected_message_count: usize,
        launch_context: &JsonMap,
        display_name: &str,
    ) -> StoreResult<()> {
        self.in_immediate(|connection| {
            crate::verify::verify_exact_schema_identity(
                connection,
                Some(crate::schema::CAMPAIGN_SCHEMA_VERSION),
            )?;
            detach_legacy_chat(connection, session_id, expected_message_count, launch_context, display_name)?;
            crate::stored::verify_stored_authority(connection)
        })
    }
}

/// `builder_workspace_migration._RECEIPT`.
const RECEIPT: &str = ".sylvia-legacy-workspace.json";

/// `os.lstat(path)` shows a symbolic link, or on Windows a reparse point.
fn linked(path: &Path) -> std::io::Result<bool> {
    let metadata = std::fs::symlink_metadata(path)?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        Ok(metadata.file_type().is_symlink() || metadata.file_attributes() & 0x400 != 0)
    }
    #[cfg(not(windows))]
    {
        Ok(metadata.file_type().is_symlink())
    }
}

fn not_a_name() -> StoreError {
    rejected("Builder workspace migration identity is not a directory name", "command_rejected")
}

/// `shutil.copytree(source, target)` of a tree already checked to hold no links: directories,
/// then each file with its contents and, as `copy2` keeps them, its permissions and times.
fn copy_tree(source: &Path, target: &Path) -> std::io::Result<()> {
    std::fs::create_dir(target)?;
    let mut entries: Vec<_> = std::fs::read_dir(source)?.collect::<Result<_, _>>()?;
    entries.sort_by_key(std::fs::DirEntry::file_name);
    for entry in entries {
        let from = entry.path();
        let to = target.join(entry.file_name());
        if entry.file_type()?.is_dir() {
            copy_tree(&from, &to)?;
        } else {
            std::fs::copy(&from, &to)?;
            let metadata = std::fs::metadata(&from)?;
            let times = std::fs::FileTimes::new()
                .set_accessed(metadata.accessed()?)
                .set_modified(metadata.modified()?);
            std::fs::File::options().write(true).open(&to)?.set_times(times)?;
        }
    }
    Ok(())
}

/// `builder_workspace_migration.retain_legacy_workspace(storage_root, campaign_id=, session_id=)`:
/// publish one complete independent copy of the campaign's workspace as the chat's own
/// (`builder-chats/<session>`), with a receipt; a retry finds its own receipt and replaces nothing.
pub fn retain_legacy_workspace(
    storage_root: &Path,
    campaign_id: &str,
    session_id: &str,
) -> StoreResult<PathBuf> {
    for value in [campaign_id, session_id] {
        if value == "." || value == ".." || value.contains(['/', '\\', ':']) {
            return Err(not_a_name());
        }
    }
    let root = crate::store::resolve(storage_root);
    let source = root.join("campaigns").join(campaign_id);
    let target = root.join("builder-chats").join(session_id);
    for path in [&source, &target] {
        if !crate::store::resolve(path).starts_with(&root) {
            return Err(rejected(
                "Builder workspace migration path leaves application storage",
                "command_rejected",
            ));
        }
        let mut ancestor = path.clone();
        while ancestor != root {
            // `ancestor.exists()` follows links: a dangling one is passed over, as in Python.
            if ancestor.exists() && linked(&ancestor)? {
                return Err(rejected(
                    "Builder workspace migration cannot follow linked directories",
                    "command_rejected",
                ));
            }
            match ancestor.parent() {
                Some(parent) => ancestor = parent.to_path_buf(),
                None => break,
            }
        }
    }
    let mut receipt = JsonMap::new();
    receipt.insert("format".into(), Json::from(1));
    receipt.insert("campaign_id".into(), Json::String(campaign_id.into()));
    receipt.insert("session_id".into(), Json::String(session_id.into()));
    let receipt = Json::Object(receipt);
    if target.exists() {
        let marker = target.join(RECEIPT);
        // An unreadable or unparsable receipt is not this migration's (`except (OSError,
        // ValueError): pass`); a link or an oversized one is refused outright.
        if let Ok(metadata) = std::fs::symlink_metadata(&marker) {
            if linked(&marker)? || metadata.len() > 4096 {
                return Err(rejected("Builder migration receipt is invalid", "command_rejected"));
            }
            let parsed = std::fs::read(&marker)
                .ok()
                .and_then(|bytes| String::from_utf8(bytes).ok())
                .and_then(|text| sylvia_base::pyjson::loads(&text).ok());
            if parsed.is_some_and(|parsed| py_eq(&parsed, &receipt)) {
                return Ok(target);
            }
        }
        return Err(rejected(
            "Builder migration found an existing independent workspace",
            "command_rejected",
        ));
    }
    if source.exists() {
        let mut pending = vec![source.clone()];
        while let Some(directory) = pending.pop() {
            // `os.walk` skips a directory it cannot list.
            let Ok(entries) = std::fs::read_dir(&directory) else { continue };
            for entry in entries {
                let path = entry?.path();
                if linked(&path)? {
                    return Err(rejected(
                        "Builder workspace migration cannot follow linked files",
                        "command_rejected",
                    ));
                }
                if std::fs::symlink_metadata(&path)?.is_dir() {
                    pending.push(path);
                }
            }
        }
        if source.join(RECEIPT).exists() {
            return Err(rejected(
                "Builder migration receipt conflicts with an existing file",
                "command_rejected",
            ));
        }
    }
    let parent = target.parent().ok_or_else(not_a_name)?;
    std::fs::create_dir_all(parent)?;
    // `tempfile.TemporaryDirectory(prefix=".legacy-builder-", dir=target.parent)`.
    let mut random = [0_u8; 6];
    getrandom::getrandom(&mut random).map_err(|error| StoreError::Other(error.to_string()))?;
    let temporary = parent.join(format!(".legacy-builder-{}", hex::encode(random)));
    std::fs::create_dir(&temporary)?;
    let staged = temporary.join("workspace");
    let published = (|| -> StoreResult<()> {
        if source.exists() {
            copy_tree(&source, &staged)?;
        } else {
            std::fs::create_dir(&staged)?;
        }
        std::fs::write(staged.join(RECEIPT), canonical_json_bytes(&receipt))?;
        std::fs::rename(&staged, &target)?;
        Ok(())
    })();
    let _ = std::fs::remove_dir_all(&temporary);
    published.map(|()| target)
}
