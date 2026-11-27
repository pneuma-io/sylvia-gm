//! Narration's durable half, from `sqlite_authority.py`: `append_narration_segment` with
//! `_append_narration_in_connection`, `record_interruption`, `interruption`, `interruptions`,
//! `append_retry_resolution`, `resolve_interruption` and `_interruption_from_row`, and the
//! store's `private_provider_index`.
//!
//! A segment is one complete player-safe prose run of a narration attempt, appended as a TALK or
//! THINK canonical event keyed by `(attempt_id, attempt_ordinal)`, so a replayed append returns
//! the saved event. An interruption is the safe retry basis of one stopped attempt; a Retry
//! either appends its final TALK and resolves the interruption in one transaction, or resolves it
//! by the sequence of its action's last event. Python's `_before_narration_commit` is a test
//! fault seam with no behaviour, so it has no counterpart here.

use crate::campaigns::{named_rows, Named};
use crate::error::{authority_coded, schema_identity, value_error, StoreError, StoreResult};
use crate::store::AuthorityStore;
use rusqlite::{params, Connection, OptionalExtension};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    response_blocks_text, validate_authority_identity, validate_response_blocks, CanonicalEvent,
    CanonicalEventType, EventVisibility, NarrationInterruption, ProseChannel,
};

/// One `append_narration_segment` call.
#[derive(Debug, Clone, PartialEq)]
pub struct NarrationSegment {
    pub campaign_id: String,
    pub turn_id: String,
    pub attempt_id: String,
    pub ordinal: i64,
    pub channel: ProseChannel,
    pub text: String,
    /// `response_blocks`: the TALK's admitted response blocks, when it has them.
    pub response_blocks: Option<Vec<JsonMap>>,
}

fn rejected(message: &str) -> StoreError {
    crate::actions::rejected(message, "command_rejected")
}

fn conflict(message: &str) -> StoreError {
    StoreError::Coded(authority_coded("IdempotencyConflict", message))
}

/// `_interruption_from_row(row)`.
fn interruption_from_row(row: &Named<'_>) -> StoreResult<NarrationInterruption> {
    let optional_int = |column: &str| -> StoreResult<Option<i64>> {
        match row.get(column)? {
            rusqlite::types::Value::Null => Ok(None),
            _ => row.int(column).map(Some),
        }
    };
    Ok(NarrationInterruption {
        campaign_id: row.text("campaign_id")?,
        interruption_id: row.text("interruption_id")?,
        turn_id: row.text("turn_id")?,
        last_action_id: row.optional_text("last_action_id")?,
        failed_attempt_id: row.text("failed_attempt_id")?,
        retryable: row.text("status")? == "retryable",
        resolving_retry_id: row.optional_text("resolving_retry_id")?,
        resolved_sequence: optional_int("resolved_sequence")?,
    })
}

/// The first row of `query`, by column name, through `build`.
fn first<T>(
    connection: &Connection,
    query: &str,
    values: &[&dyn rusqlite::ToSql],
    build: impl Fn(&Named<'_>) -> StoreResult<T>,
) -> StoreResult<Option<T>> {
    let (columns, rows) = named_rows(connection, query, values)?;
    rows.into_iter().next().map(|values| build(&Named { columns: &columns, values })).transpose()
}

/// Run `body` in a `BEGIN IMMEDIATE` transaction on a new connection, rolling back whatever it
/// leaves open (its own early `rollback()`, or a failure).
fn immediate<T>(store: &AuthorityStore, body: impl FnOnce(&Connection) -> StoreResult<T>) -> StoreResult<T> {
    let connection = store.connect()?;
    let result = (|| {
        connection.execute_batch("BEGIN IMMEDIATE")?;
        body(&connection)
    })();
    if !connection.is_autocommit() {
        let _ = connection.execute_batch("ROLLBACK");
    }
    result
}

/// `validate_response_blocks` as the store admits them: a `ValueError` is "not player-safe".
fn admitted_blocks(text: &str, blocks: &[JsonMap]) -> StoreResult<Vec<JsonMap>> {
    let value = Json::Array(blocks.iter().cloned().map(Json::Object).collect());
    let not_safe = |error: sylvia_domain::DomainError| -> StoreError {
        if error.python_class() == "ValueError" {
            rejected("Response blocks are not player-safe")
        } else {
            error.into()
        }
    };
    let admitted = validate_response_blocks(&value).map_err(not_safe)?;
    let fallback = response_blocks_text(&Json::Array(admitted.iter().cloned().map(Json::Object).collect()))
        .map_err(not_safe)?;
    if fallback != text {
        return Err(rejected("Response block fallback text does not match"));
    }
    Ok(admitted)
}

/// `_append_narration_in_connection(connection, ...)`.
fn append_in_connection(connection: &Connection, segment: &NarrationSegment) -> StoreResult<CanonicalEvent> {
    crate::actions::require_active_campaign(connection, &segment.campaign_id)?;
    let existing = first(
        connection,
        "SELECT * FROM canonical_events WHERE campaign_id = ? AND attempt_id = ? AND attempt_ordinal = ?",
        &[&segment.campaign_id, &segment.attempt_id, &segment.ordinal],
        crate::snapshots::event,
    )?;
    let event_type = match segment.channel {
        ProseChannel::SafeThink => CanonicalEventType::Think,
        ProseChannel::Talk => CanonicalEventType::Talk,
    };
    let admitted = match &segment.response_blocks {
        Some(blocks) => {
            if event_type != CanonicalEventType::Talk {
                return Err(rejected("Response blocks are valid only for TALK"));
            }
            Some(admitted_blocks(&segment.text, blocks)?)
        }
        None => None,
    };
    let mut payload = JsonMap::new();
    payload.insert("text".into(), segment.text.as_str().into());
    if let Some(blocks) = admitted {
        payload.insert("response_blocks".into(), Json::Array(blocks.into_iter().map(Json::Object).collect()));
    }
    if let Some(existing) = existing {
        if existing.event_type != event_type
            || existing.turn_id != segment.turn_id
            || existing.safe_payload != payload
        {
            return Err(conflict("Narration segment identity payload does not match"));
        }
        return Ok(existing);
    }
    let turn: Option<i64> = connection
        .query_row(
            "SELECT 1 FROM turns WHERE campaign_id = ? AND turn_id = ?",
            [&segment.campaign_id, &segment.turn_id],
            |row| row.get(0),
        )
        .optional()?;
    if turn.is_none() {
        return Err(rejected("TURN is unavailable"));
    }
    let sequence: i64 = connection.query_row(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM canonical_events WHERE campaign_id = ?",
        [&segment.campaign_id],
        |row| row.get(0),
    )?;
    connection.execute(
        "INSERT INTO canonical_events \
         (campaign_id, sequence, event_type, turn_id, action_id, \
         safe_payload_json, visibility, attempt_id, attempt_ordinal) \
         VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?)",
        params![
            segment.campaign_id,
            sequence,
            event_type.as_str(),
            segment.turn_id,
            crate::actions::dump(&Json::Object(payload.clone())),
            EventVisibility::PlayerSafe.as_str(),
            segment.attempt_id,
            segment.ordinal,
        ],
    )?;
    Ok(CanonicalEvent {
        sequence,
        event_type,
        turn_id: segment.turn_id.clone(),
        action_id: None,
        safe_payload: payload,
        visibility: EventVisibility::PlayerSafe,
        attempt_id: Some(segment.attempt_id.clone()),
        attempt_ordinal: Some(segment.ordinal),
    })
}

impl AuthorityStore {
    /// `append_narration_segment(...)`: idempotently append one complete player-safe prose
    /// segment.
    pub fn append_narration_segment(&self, segment: &NarrationSegment) -> StoreResult<CanonicalEvent> {
        for (value, label) in [
            (&segment.campaign_id, "Campaign identity"),
            (&segment.turn_id, "TURN identity"),
            (&segment.attempt_id, "Narration attempt identity"),
        ] {
            validate_authority_identity(value, label, false)?;
        }
        if segment.ordinal < 0 {
            return Err(value_error("Narration ordinal cannot be negative"));
        }
        if segment.text.is_empty() {
            return Err(value_error("Completed narration text cannot be empty"));
        }
        immediate(self, |connection| {
            let event = append_in_connection(connection, segment)?;
            connection.execute_batch("COMMIT")?;
            Ok(event)
        })
    }

    /// `record_interruption(...)`: persist only safe retry metadata for one terminal
    /// interrupted attempt. A Retry that fails again moves its source interruption onto itself,
    /// with a later result of the same turn when it has one.
    pub fn record_interruption(
        &self,
        campaign_id: &str,
        interruption_id: &str,
        turn_id: &str,
        last_action_id: Option<&str>,
        failed_attempt_id: &str,
    ) -> StoreResult<NarrationInterruption> {
        for (value, label) in [
            (campaign_id, "Campaign identity"),
            (interruption_id, "Interruption identity"),
            (turn_id, "TURN identity"),
            (failed_attempt_id, "Failed narration attempt identity"),
        ] {
            validate_authority_identity(value, label, false)?;
        }
        if let Some(action) = last_action_id {
            validate_authority_identity(action, "Last action identity", false)?;
        }
        immediate(self, |connection| {
            crate::actions::require_active_campaign(connection, campaign_id)?;
            let existing = first(
                connection,
                "SELECT * FROM narration_interruptions WHERE campaign_id = ? AND failed_attempt_id = ?",
                &[&campaign_id, &failed_attempt_id],
                interruption_from_row,
            )?;
            if let Some(record) = existing {
                if record.interruption_id != interruption_id
                    || record.turn_id != turn_id
                    || record.last_action_id.as_deref() != last_action_id
                {
                    return Err(conflict("Interruption identity payload does not match"));
                }
                return Ok(record);
            }
            let source = first(
                connection,
                "SELECT * FROM narration_interruptions WHERE campaign_id = ? \
                 AND resolving_retry_id = ? AND status = 'retryable'",
                &[&campaign_id, &failed_attempt_id],
                interruption_from_row,
            )?;
            if let Some(source) = source {
                if source.turn_id != turn_id {
                    return Err(conflict("Retry interruption basis does not match"));
                }
                if source.last_action_id.as_deref() != last_action_id {
                    let revision = |action: Option<&str>| {
                        first(
                            connection,
                            "SELECT turn_id, resulting_revision FROM actions \
                             WHERE campaign_id = ? AND action_id = ?",
                            &[&campaign_id, &action],
                            |row| Ok((row.text("turn_id")?, row.int("resulting_revision")?)),
                        )
                    };
                    let latest = revision(last_action_id)?;
                    let previous = revision(source.last_action_id.as_deref())?;
                    let later = match (&latest, &previous) {
                        (None, _) => false,
                        (Some((turn, _)), _) if turn != turn_id => false,
                        (Some((_, latest)), Some((_, previous))) => latest > previous,
                        (Some(_), None) => true,
                    };
                    if !later {
                        return Err(conflict("Retry requires a later result in the same turn"));
                    }
                }
                connection.execute(
                    "UPDATE narration_interruptions SET failed_attempt_id = ?, last_action_id = ?, \
                     resolving_retry_id = NULL WHERE campaign_id = ? AND interruption_id = ?",
                    params![failed_attempt_id, last_action_id, campaign_id, source.interruption_id],
                )?;
                connection.execute_batch("COMMIT")?;
                return Ok(NarrationInterruption {
                    campaign_id: campaign_id.to_owned(),
                    interruption_id: source.interruption_id,
                    turn_id: turn_id.to_owned(),
                    last_action_id: last_action_id.map(str::to_owned),
                    failed_attempt_id: failed_attempt_id.to_owned(),
                    retryable: true,
                    resolving_retry_id: None,
                    resolved_sequence: None,
                });
            }
            connection.execute(
                "INSERT INTO narration_interruptions \
                 (campaign_id, interruption_id, turn_id, last_action_id, \
                 failed_attempt_id, status, resolving_retry_id, resolved_sequence) \
                 VALUES (?, ?, ?, ?, ?, 'retryable', NULL, NULL)",
                params![campaign_id, interruption_id, turn_id, last_action_id, failed_attempt_id],
            )?;
            connection.execute_batch("COMMIT")?;
            Ok(NarrationInterruption {
                campaign_id: campaign_id.to_owned(),
                interruption_id: interruption_id.to_owned(),
                turn_id: turn_id.to_owned(),
                last_action_id: last_action_id.map(str::to_owned),
                failed_attempt_id: failed_attempt_id.to_owned(),
                retryable: true,
                resolving_retry_id: None,
                resolved_sequence: None,
            })
        })
    }

    /// `interruption(campaign_id, interruption_id)`.
    pub fn interruption(
        &self,
        campaign_id: &str,
        interruption_id: &str,
    ) -> StoreResult<NarrationInterruption> {
        let connection = self.connect()?;
        first(
            &connection,
            "SELECT * FROM narration_interruptions WHERE campaign_id = ? AND interruption_id = ?",
            &[&campaign_id, &interruption_id],
            interruption_from_row,
        )?
        .ok_or_else(|| rejected("Narration interruption is unavailable"))
    }

    /// `interruptions(campaign_id)`: every interruption of the campaign, oldest first.
    pub fn interruptions(&self, campaign_id: &str) -> StoreResult<Vec<NarrationInterruption>> {
        let connection = self.connect()?;
        let (columns, rows) = named_rows(
            &connection,
            "SELECT * FROM narration_interruptions WHERE campaign_id = ? ORDER BY rowid",
            &[&campaign_id],
        )?;
        rows.into_iter().map(|values| interruption_from_row(&Named { columns: &columns, values })).collect()
    }

    /// `append_retry_resolution(...)`: atomically append a Retry's final TALK (`segment`, whose
    /// attempt is the Retry) and resolve its interruption; a resolved one replays its event.
    pub fn append_retry_resolution(
        &self,
        interruption_id: &str,
        segment: &NarrationSegment,
    ) -> StoreResult<CanonicalEvent> {
        let campaign_id = segment.campaign_id.as_str();
        immediate(self, |connection| {
            crate::actions::require_active_campaign(connection, campaign_id)?;
            let record = first(
                connection,
                "SELECT * FROM narration_interruptions WHERE campaign_id = ? AND interruption_id = ?",
                &[&campaign_id, &interruption_id],
                interruption_from_row,
            )?
            .ok_or_else(|| rejected("Narration interruption is unavailable"))?;
            if record.resolving_retry_id.as_deref() != Some(segment.attempt_id.as_str()) {
                return Err(conflict("Retry identity does not own this interruption"));
            }
            if !record.retryable {
                let Some(sequence) = record.resolved_sequence else {
                    return Err(schema_identity("Resolved Retry has no canonical sequence"));
                };
                return first(
                    connection,
                    "SELECT * FROM canonical_events WHERE campaign_id = ? AND sequence = ?",
                    &[&campaign_id, &sequence],
                    crate::snapshots::event,
                )?
                .ok_or_else(|| schema_identity("Resolved Retry chronology is unavailable"));
            }
            let talk = NarrationSegment { channel: ProseChannel::Talk, ..segment.clone() };
            let event = append_in_connection(connection, &talk)?;
            connection.execute(
                "UPDATE narration_interruptions SET status = 'resolved', \
                 resolved_sequence = ? WHERE campaign_id = ? AND interruption_id = ?",
                params![event.sequence, campaign_id, interruption_id],
            )?;
            connection.execute_batch("COMMIT")?;
            Ok(event)
        })
    }

    /// `resolve_interruption(...)`: resolve a Retry's interruption by the sequence of its
    /// chronology (its own event, or its action's).
    pub fn resolve_interruption(
        &self,
        campaign_id: &str,
        interruption_id: &str,
        retry_id: &str,
        resolved_sequence: i64,
    ) -> StoreResult<NarrationInterruption> {
        immediate(self, |connection| {
            crate::actions::require_active_campaign(connection, campaign_id)?;
            let record = first(
                connection,
                "SELECT * FROM narration_interruptions WHERE campaign_id = ? AND interruption_id = ?",
                &[&campaign_id, &interruption_id],
                interruption_from_row,
            )?
            .ok_or_else(|| rejected("Narration interruption is unavailable"))?;
            if record.resolving_retry_id.as_deref() != Some(retry_id) {
                return Err(conflict("Retry identity does not own this interruption"));
            }
            if !record.retryable {
                if record.resolved_sequence != Some(resolved_sequence) {
                    return Err(conflict("Retry resolution sequence does not match"));
                }
                return Ok(record);
            }
            // `str(event["attempt_id"])`: a NULL column is the text "None", never an identity.
            let event = first(
                connection,
                "SELECT attempt_id, action_id FROM canonical_events WHERE campaign_id = ? AND sequence = ?",
                &[&campaign_id, &resolved_sequence],
                |row| {
                    let text = |column: &str| -> StoreResult<String> {
                        Ok(row.optional_text(column)?.unwrap_or_else(|| "None".to_owned()))
                    };
                    Ok((text("attempt_id")?, text("action_id")?))
                },
            )?;
            let names_retry = match &event {
                None => false,
                Some((attempt, action)) => {
                    attempt == retry_id || record.last_action_id.as_deref().is_some_and(|last| action == last)
                }
            };
            if !names_retry {
                return Err(conflict("Retry resolution does not name retry chronology"));
            }
            connection.execute(
                "UPDATE narration_interruptions SET status = 'resolved', \
                 resolved_sequence = ? WHERE campaign_id = ? AND interruption_id = ?",
                params![resolved_sequence, campaign_id, interruption_id],
            )?;
            connection.execute_batch("COMMIT")?;
            Ok(NarrationInterruption {
                retryable: false,
                resolving_retry_id: Some(retry_id.to_owned()),
                resolved_sequence: Some(resolved_sequence),
                ..record
            })
        })
    }

    /// `private_provider_index(session_id)`: each thinking attempt's availability and
    /// correlation, never its text; none before schema 11.
    pub fn private_provider_index(&self, session_id: &str) -> StoreResult<Vec<JsonMap>> {
        let connection = self.connect_read_only()?;
        let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        if version < 11 {
            return Ok(Vec::new());
        }
        crate::reasoning::inspection_index(&connection, session_id)
    }
}

/// `f"interruption-{uuid.uuid4()}"`: a new interruption identity.
pub fn new_interruption_id() -> StoreResult<String> {
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes).map_err(|error| StoreError::Other(error.to_string()))?;
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let hex = hex::encode(bytes);
    Ok(format!("interruption-{}-{}-{}-{}-{}", &hex[..8], &hex[8..12], &hex[12..16], &hex[16..20], &hex[20..]))
}
