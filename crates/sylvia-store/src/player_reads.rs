//! Reads behind a GM response's saved player actions, from `sqlite_authority.py`:
//! `campaign_session_contexts`, `response_window_actions` and `player_action_markers`. All three
//! read through a read-only connection, as Python's do.

use crate::actions::load_payload;
use crate::campaigns::{named_rows, Named};
use crate::error::StoreResult;
use crate::pytext::py_str;
use crate::sessions::{message_from_row, read_messages, read_session};
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::session::SessionContext;

/// `json.dumps(values)` of a list of strings, Python's default spacing and escaping.
fn json_list(values: &[&str]) -> String {
    pyjson::dumps(&Json::Array(values.iter().map(|value| Json::from(*value)).collect()), Dump::DEFAULT)
}

impl AuthorityStore {
    /// `campaign_session_contexts(campaign_id, evidence_kinds=...)`: every session of the
    /// campaign with its messages, or, when `evidence_kinds` is given, only its evidence messages
    /// of those kinds.
    pub fn campaign_session_contexts(
        &self,
        campaign_id: &str,
        evidence_kinds: &[&str],
    ) -> StoreResult<Vec<SessionContext>> {
        let connection = self.connect_read_only()?;
        connection.execute_batch("BEGIN")?;
        let has_sessions = connection
            .query_row(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'agent_sessions'",
                [],
                |_| Ok(()),
            )
            .map(|()| true)
            .or_else(|error| match error {
                rusqlite::Error::QueryReturnedNoRows => Ok(false),
                error => Err(error),
            })?;
        if !has_sessions {
            connection.execute_batch("ROLLBACK")?;
            return Ok(Vec::new());
        }
        let identities: Vec<String> = named_rows(
            &connection,
            "SELECT session_id FROM agent_sessions WHERE campaign_id = ?",
            &[&campaign_id],
        )?
        .1
        .iter()
        .map(|row| py_str(&row[0]))
        .collect();
        let kinds = json_list(evidence_kinds);
        let mut contexts = Vec::new();
        for session_id in identities {
            let session = read_session(&connection, &session_id)?;
            let messages = if evidence_kinds.is_empty() {
                read_messages(&connection, &session_id)?
            } else {
                let (columns, rows) = named_rows(
                    &connection,
                    "SELECT * FROM agent_session_messages WHERE session_id = ? AND kind = 'evidence' \
                     AND json_extract(content_json, '$.kind') IN (SELECT value FROM json_each(?)) \
                     ORDER BY sequence",
                    &[&session_id, &kinds],
                )?;
                rows.into_iter()
                    .map(|values| message_from_row(&Named { columns: &columns, values }))
                    .collect::<StoreResult<_>>()?
            };
            contexts.push(SessionContext { session, messages });
        }
        connection.execute_batch("COMMIT")?;
        Ok(contexts)
    }

    /// `response_window_actions(campaign_id, response_attempts=..., through=...)`: the actions
    /// since the last ordinary TALK at the cursor, a TALK of a response attempt not counting.
    pub fn response_window_actions(
        &self,
        campaign_id: &str,
        response_attempts: &[String],
        through: Option<i64>,
    ) -> StoreResult<Vec<String>> {
        let mut attempts: Vec<&str> = response_attempts.iter().map(String::as_str).collect();
        attempts.sort_unstable();
        attempts.dedup();
        let connection = self.connect_read_only()?;
        let rows = named_rows(
            &connection,
            "SELECT DISTINCT action_id FROM canonical_events WHERE campaign_id = ?1 \
             AND action_id IS NOT NULL AND (?2 IS NULL OR sequence <= ?2) \
             AND sequence > (SELECT COALESCE(MAX(sequence), 0) FROM canonical_events \
             WHERE campaign_id = ?1 AND event_type = 'talk' \
             AND (?2 IS NULL OR sequence <= ?2) AND (attempt_id IS NULL OR \
             attempt_id NOT IN (SELECT value FROM json_each(?3))))",
            &[&campaign_id, &through, &json_list(&attempts)],
        )?
        .1;
        Ok(rows.iter().map(|row| py_str(&row[0])).collect())
    }

    /// `player_action_markers(campaign_id, action_ids, marker_kind)`: each selected action's
    /// identity, turn, status and saved origin marker (its last of `marker_kind`), in commit order.
    pub fn player_action_markers(
        &self,
        campaign_id: &str,
        action_ids: &[&str],
        marker_kind: &str,
    ) -> StoreResult<Vec<JsonMap>> {
        if action_ids.is_empty() {
            return Ok(Vec::new());
        }
        let connection = self.connect_read_only()?;
        let (columns, rows) = named_rows(
            &connection,
            "SELECT a.action_id, a.turn_id, a.status, (\
             SELECT json_extract(c.payload_json, '$.origin') FROM consequences c \
             WHERE c.campaign_id = a.campaign_id AND c.action_id = a.action_id \
             AND json_extract(c.payload_json, '$.kind') = ? \
             AND json_type(c.payload_json, '$.origin') = 'object' \
             ORDER BY c.ordinal DESC LIMIT 1) AS origin FROM actions a \
             WHERE a.campaign_id = ? AND a.action_id IN (SELECT value FROM json_each(?)) \
             ORDER BY a.resulting_revision",
            &[&marker_kind, &campaign_id, &json_list(action_ids)],
        )?;
        rows.into_iter()
            .map(|values| {
                let row = Named { columns: &columns, values };
                let mut marker = JsonMap::new();
                for key in ["action_id", "turn_id", "status"] {
                    marker.insert(key.into(), Json::from(row.text(key)?));
                }
                let origin = match row.get("origin")? {
                    Value::Null => Json::Null,
                    _ => Json::Object(load_payload(&row.text("origin")?)?),
                };
                marker.insert("origin".into(), origin);
                Ok(marker)
            })
            .collect()
    }
}

impl AuthorityStore {
    /// `current_campaign_entities(campaign_id)`: each entity record's latest active version, for
    /// panels (not the action or audit history). A predecessor before migration 9 has none, and
    /// reading a panel never migrates it.
    pub fn current_campaign_entities(&self, campaign_id: &str) -> StoreResult<Vec<JsonMap>> {
        let connection = self.connect()?;
        let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        if version < 9 {
            return Ok(Vec::new());
        }
        let (columns, rows) = named_rows(
            &connection,
            "SELECT record.record_id, record.payload_json FROM campaign_continuity_records AS record              JOIN (SELECT record_id, MAX(sequence) AS sequence FROM campaign_continuity_records              WHERE campaign_id = ? AND kind = 'entity' GROUP BY record_id) AS latest              ON latest.record_id = record.record_id AND latest.sequence = record.sequence              WHERE record.campaign_id = ? AND record.kind = 'entity' AND record.status = 'active'              ORDER BY record.record_id",
            &[&campaign_id, &campaign_id],
        )?;
        rows.into_iter()
            .map(|values| {
                let row = Named { columns: &columns, values };
                // `{"record_id": ..., **payload}`: the payload's own `record_id` keeps the first place.
                let mut entity = JsonMap::new();
                entity.insert("record_id".into(), py_str(row.get("record_id")?).into());
                entity.extend(load_payload(&row.text("payload_json")?)?);
                Ok(entity)
            })
            .collect()
    }
}
