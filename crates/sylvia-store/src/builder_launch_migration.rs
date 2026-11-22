//! The owner-startup migration of former campaign-creation content chats
//! (`sqlite_builder_launch.py` and `builder_workspace_migration.py`, the parts the migration
//! uses). Lane 5 writes this module beside lane 3's store at the manager's direction.
//!
//! So far: [`AuthorityStore::legacy_builder_contexts`], the read that finds them. The detachment,
//! its migration evidence and the retained workspace follow.

use crate::error::{StoreError, StoreResult};
use crate::sessions::{read_messages, read_session};
use crate::store::AuthorityStore;
use sylvia_base::Json;
use sylvia_domain::session::{SessionContext, SessionMessageKind};

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
                    let kind =
                        if matches!(content.get("purpose"), Some(Json::Array(_))) { "list" } else { "dict" };
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
