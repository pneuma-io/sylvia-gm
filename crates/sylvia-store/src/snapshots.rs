//! Campaign snapshots, from `sqlite_authority.py`: `SQLiteAuthorityStore.snapshot`,
//! `display_snapshot` and the `_read_snapshot` they share, with `_read_state`.
//!
//! Both read one campaign's current authority in one transaction: the campaign, its lock, state,
//! participants and turns, its continuity at schema 12, and its history. A complete snapshot has
//! every action, event, mutation and consequence; a display snapshot has only the actions and
//! events a [`DisplayQuery`] asks for, and never mutations or consequences.

use crate::actions::{load_json, load_payload};
use crate::campaigns::{named_rows, parse, Named};
use crate::error::StoreResult;
use crate::schema::CAMPAIGN_SCHEMA_VERSION;
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use rusqlite::Connection;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    ActionRecord, ActionStatus, CampaignContinuityView, CampaignDisplaySnapshot, CampaignSnapshot,
    CampaignState, CanonicalEvent, CanonicalEventType, ConsequenceRecord, EventVisibility, MutationRecord,
    Participant, ParticipantFields, TurnStatus, WriterRole,
};

/// What a display snapshot reads of the history (`display_snapshot`'s keyword arguments).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DisplayQuery {
    /// `event_after`: the events after this sequence (and the tool calls of their actions).
    pub event_after: Option<i64>,
    /// `actions_after_revision`: the actions after this revision.
    pub actions_after_revision: Option<i64>,
    /// `action_ids`: these actions too.
    pub action_ids: Vec<String>,
    /// `include_continuity`: the continuity view (at schema 12).
    pub include_continuity: bool,
}

/// What `_read_snapshot` read.
struct Read {
    state: CampaignState,
    actions: Vec<ActionRecord>,
    events: Vec<CanonicalEvent>,
    mutations: Vec<MutationRecord>,
    consequences: Vec<ConsequenceRecord>,
    turns: Vec<(String, TurnStatus)>,
    continuity: CampaignContinuityView,
}

fn rows(
    connection: &Connection,
    query: &str,
    values: &[&dyn rusqlite::ToSql],
) -> StoreResult<Vec<Vec<Value>>> {
    Ok(named_rows(connection, query, values)?.1)
}

/// Each row of `query` by column name, through `build`.
fn each<T>(
    connection: &Connection,
    query: &str,
    values: &[&dyn rusqlite::ToSql],
    mut build: impl FnMut(&Named<'_>) -> StoreResult<T>,
) -> StoreResult<Vec<T>> {
    let (columns, found) = named_rows(connection, query, values)?;
    found.into_iter().map(|values| build(&Named { columns: &columns, values })).collect()
}

/// `str(row[column])` of a column that may be `NULL`.
fn optional_int(row: &Named<'_>, column: &str) -> StoreResult<Option<i64>> {
    match row.get(column)? {
        Value::Null => Ok(None),
        _ => row.int(column).map(Some),
    }
}

/// `_read_state`: every state value by `namespace.state_key`, in key order.
pub(crate) fn read_state(connection: &Connection, campaign_id: &str) -> StoreResult<JsonMap> {
    let mut state = JsonMap::new();
    for row in rows(
        connection,
        "SELECT namespace, state_key, value_json FROM campaign_state \
         WHERE campaign_id = ? ORDER BY namespace, state_key",
        &[&campaign_id],
    )? {
        let key = format!("{}.{}", crate::pytext::py_str(&row[0]), crate::pytext::py_str(&row[1]));
        state.insert(key, load_json(&crate::pytext::py_str(&row[2]))?);
    }
    Ok(state)
}

pub(crate) fn participant(row: &Named<'_>, has_f5: bool) -> StoreResult<Participant> {
    let f5_text = |column: &str| -> StoreResult<Option<String>> {
        if has_f5 {
            row.optional_text(column)
        } else {
            Ok(None)
        }
    };
    Ok(Participant::new(ParticipantFields {
        participant_id: row.text("participant_id")?,
        kind: row.text("kind")?,
        package_id: row.text("package_id")?,
        package_version: row.text("package_version")?,
        data: load_payload(&row.text("data_json")?)?,
        package_sha256: f5_text("package_sha256")?,
        schema_id: f5_text("schema_id")?,
        schema_sha256: f5_text("schema_sha256")?,
        validation_status: if has_f5 { row.text("validation_status")? } else { "quarantined".to_owned() },
        adaptation_id: f5_text("adaptation_id")?,
    })?)
}

fn action(row: &Named<'_>) -> StoreResult<ActionRecord> {
    Ok(ActionRecord {
        action_id: row.text("action_id")?,
        turn_id: row.text("turn_id")?,
        status: parse(&row.text("status")?, ActionStatus::parse, "ActionStatus")?,
        expected_revision: row.int("expected_revision")?,
        resulting_revision: row.int("resulting_revision")?,
        writer: parse(&row.text("writer")?, WriterRole::parse, "WriterRole")?,
        package_id: row.text("package_id")?,
        package_version: row.text("package_version")?,
        package_sha256: row.text("package_sha256")?,
        tool_name: row.text("tool_name")?,
        parameters: load_payload(&row.text("payload_json")?)?,
        result: load_payload(&row.text("result_json")?)?,
    })
}

pub(crate) fn event(row: &Named<'_>) -> StoreResult<CanonicalEvent> {
    Ok(CanonicalEvent {
        sequence: row.int("sequence")?,
        event_type: parse(&row.text("event_type")?, CanonicalEventType::parse, "CanonicalEventType")?,
        turn_id: row.text("turn_id")?,
        action_id: row.optional_text("action_id")?,
        safe_payload: load_payload(&row.text("safe_payload_json")?)?,
        visibility: parse(&row.text("visibility")?, EventVisibility::parse, "EventVisibility")?,
        attempt_id: row.optional_text("attempt_id")?,
        attempt_ordinal: optional_int(row, "attempt_ordinal")?,
    })
}

impl AuthorityStore {
    /// `snapshot(campaign_id)`: the complete campaign snapshot the engine runs on.
    pub fn snapshot(&self, campaign_id: &str) -> StoreResult<CampaignSnapshot> {
        let read = self.read_snapshot(campaign_id, None)?;
        Ok(CampaignSnapshot {
            state: read.state,
            actions: read.actions,
            events: read.events,
            mutations: read.mutations,
            consequences: read.consequences,
            turns: read.turns,
            continuity: read.continuity,
        })
    }

    /// `display_snapshot(campaign_id, ...)`: current state plus the requested history.
    pub fn display_snapshot(
        &self,
        campaign_id: &str,
        query: &DisplayQuery,
    ) -> StoreResult<CampaignDisplaySnapshot> {
        let read = self.read_snapshot(campaign_id, Some(query))?;
        Ok(CampaignDisplaySnapshot {
            state: read.state,
            actions: read.actions,
            events: read.events,
            turns: read.turns,
            continuity: read.continuity,
        })
    }

    /// `_read_snapshot(campaign_id, display_only=...)`, in one transaction.
    fn read_snapshot(&self, campaign_id: &str, display: Option<&DisplayQuery>) -> StoreResult<Read> {
        let connection = self.connect()?;
        connection.execute_batch("BEGIN")?;
        let read = read_snapshot_on(&connection, campaign_id, display)?;
        connection.execute_batch("COMMIT")?;
        Ok(read)
    }
}

fn read_snapshot_on(
    connection: &Connection,
    campaign_id: &str,
    display: Option<&DisplayQuery>,
) -> StoreResult<Read> {
    let campaign =
        each(connection, "SELECT * FROM campaigns WHERE campaign_id = ?", &[&campaign_id], |row| {
            crate::guided::campaign_record(row)
        })?
        .into_iter()
        .next()
        .ok_or_else(|| {
            crate::actions::rejected("Campaign is unavailable: missing or purged", "command_rejected")
        })?;
    let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    let has_f5 = version == CAMPAIGN_SCHEMA_VERSION;
    let participants = each(
        connection,
        "SELECT * FROM participants WHERE campaign_id = ? ORDER BY participant_id",
        &[&campaign_id],
        |row| participant(row, has_f5),
    )?;

    let mut actions = Vec::new();
    let mut events = Vec::new();
    match display {
        None => {
            actions = each(
                connection,
                "SELECT * FROM actions WHERE campaign_id = ? ORDER BY resulting_revision",
                &[&campaign_id],
                action,
            )?;
            events = each(
                connection,
                "SELECT * FROM canonical_events WHERE campaign_id = ? ORDER BY sequence",
                &[&campaign_id],
                event,
            )?;
        }
        Some(query) => {
            if query.event_after.is_some()
                || query.actions_after_revision.is_some()
                || !query.action_ids.is_empty()
            {
                let ids = Json::Array(query.action_ids.iter().map(|id| Json::from(id.as_str())).collect());
                let ids = pyjson::dumps(&ids, Dump::DEFAULT);
                actions = each(
                    connection,
                    "SELECT * FROM actions WHERE campaign_id = ? AND (action_id IN (SELECT action_id FROM \
                     canonical_events WHERE campaign_id = ? AND sequence > ?) OR resulting_revision > ? \
                     OR action_id IN (SELECT value FROM json_each(?))) ORDER BY resulting_revision",
                    &[&campaign_id, &campaign_id, &query.event_after, &query.actions_after_revision, &ids],
                    action,
                )?;
            }
            if query.event_after.is_some() {
                events = each(
                    connection,
                    "SELECT * FROM canonical_events WHERE campaign_id = ? AND (sequence > ? OR \
                     (event_type = 'tool_call' AND action_id IN (SELECT action_id FROM canonical_events \
                     WHERE campaign_id = ? AND sequence > ?))) ORDER BY sequence",
                    &[&campaign_id, &query.event_after, &campaign_id, &query.event_after],
                    event,
                )?;
            }
        }
    }
    let (mutations, consequences) = if display.is_some() {
        (Vec::new(), Vec::new())
    } else {
        let mutations = each(
            connection,
            "SELECT * FROM mutations WHERE campaign_id = ? ORDER BY action_id, ordinal",
            &[&campaign_id],
            |row| {
                Ok(MutationRecord {
                    action_id: row.text("action_id")?,
                    ordinal: row.int("ordinal")?,
                    namespace: row.text("namespace")?,
                    key: row.text("state_key")?,
                    before: load_json(&row.text("before_json")?)?,
                    after: load_json(&row.text("after_json")?)?,
                })
            },
        )?;
        let consequences = each(
            connection,
            "SELECT * FROM consequences WHERE campaign_id = ? ORDER BY action_id, ordinal",
            &[&campaign_id],
            |row| {
                Ok(ConsequenceRecord {
                    action_id: row.text("action_id")?,
                    ordinal: row.int("ordinal")?,
                    payload: load_payload(&row.text("payload_json")?)?,
                })
            },
        )?;
        (mutations, consequences)
    };
    // `MappingProxyType({...})`: a repeated turn identity keeps its first position, last value.
    let mut turns: Vec<(String, TurnStatus)> = Vec::new();
    for row in rows(
        connection,
        "SELECT turn_id, status FROM turns WHERE campaign_id = ? ORDER BY turn_id",
        &[&campaign_id],
    )? {
        let turn_id = crate::pytext::py_str(&row[0]);
        let status = parse(&crate::pytext::py_str(&row[1]), TurnStatus::parse, "TurnStatus")?;
        match turns.iter_mut().find(|(known, _)| *known == turn_id) {
            Some(entry) => entry.1 = status,
            None => turns.push((turn_id, status)),
        }
    }
    let package_lock = crate::stored::read_lock(connection, campaign_id)?;
    let state = read_state(connection, campaign_id)?;
    let continuity = match display {
        Some(query) if !(query.include_continuity && has_f5) => CampaignContinuityView::default(),
        _ if has_f5 => crate::continuity::read_view(connection, campaign_id)?,
        _ => CampaignContinuityView::default(),
    };
    Ok(Read {
        state: CampaignState {
            campaign_id: campaign.campaign_id,
            display_name: campaign.display_name,
            lifecycle: campaign.lifecycle,
            writer: campaign.writer,
            revision: campaign.revision,
            package_lock,
            state,
            participants,
        },
        actions,
        events,
        mutations,
        consequences,
        turns,
        continuity,
    })
}
