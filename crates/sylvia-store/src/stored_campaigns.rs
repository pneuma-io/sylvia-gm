//! `_verify_campaign_authority`: one campaign's revision, action and canonical chronology
//! authority, reconciled at startup and on backups (part 3 of `_verify_stored_authority`).
//!
//! Python: `SQLiteAuthorityStore._verify_campaign_authority` with `_stored_action_hash` and
//! `sqlite_packages.lock_lineage`.
//!
//! Python catches nothing here except the parse of the campaign's lifecycle and writer: only the
//! failures it raises itself become its `SchemaIdentityError`. A `ValueError` from a stored enum,
//! a non-canonical JSON text, a `CommandRejected` from a lock read or a SQLite error escapes, and
//! startup words it as `_migrate` does. Each step keeps Python's evaluation order, so the same
//! damage fails the same way.
//!
//! A failed action's one consequence must be the owner's player-action marker
//! (`player_actions.valid_player_action_consequence`, lane 2's
//! `sylvia_domain::player_actions`).

use crate::actions::{dump, load_json, load_payload};
use crate::campaigns::{named_rows, parse, read_lock_record, Named};
use crate::error::{schema_identity, value_error, StoreError, StoreResult};
use crate::pytext::py_str;
use rusqlite::types::Value;
use rusqlite::{Connection, OptionalExtension};
use std::collections::{HashMap, HashSet};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    canonical_payload_is_safe, lifecycle_writer_is_legal, response_blocks_text,
    validate_authority_identity_value, ActionStatus, CanonicalEventType, EventVisibility, LifecycleState,
    WriterRole,
};
use sylvia_domain::package_library::PackageLockRecord;
use sylvia_domain::player_actions::valid_player_action_consequence;
use sylvia_domain::pyops::py_eq;

const INVALID: &str = "Saved campaign records failed an integrity check";

fn invalid() -> StoreError {
    schema_identity(INVALID)
}

/// The columns and rows of one query, borrowed as [`Named`] rows.
struct Table {
    columns: Vec<String>,
    rows: Vec<Vec<Value>>,
}

impl Table {
    fn read(connection: &Connection, query: &str, values: &[&dyn rusqlite::ToSql]) -> StoreResult<Self> {
        let (columns, rows) = named_rows(connection, query, values)?;
        Ok(Self { columns, rows })
    }

    /// Python's `()` for a table this schema does not have yet.
    fn empty() -> Self {
        Self { columns: Vec::new(), rows: Vec::new() }
    }

    fn named(&self) -> Vec<Named<'_>> {
        self.rows.iter().map(|values| Named { columns: &self.columns, values: values.clone() }).collect()
    }
}

fn object(pairs: Vec<(&str, Json)>) -> Json {
    Json::Object(pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect::<JsonMap>())
}

/// `{namespace: {key: value}}`, the shape Python checks state values in.
fn nested(namespace: &str, key: &str, value: Json) -> Json {
    object(vec![(namespace, object(vec![(key, value)]))])
}

fn event_type(text: &str) -> StoreResult<CanonicalEventType> {
    parse(text, CanonicalEventType::parse, "CanonicalEventType")
}

fn action_status(text: &str) -> StoreResult<ActionStatus> {
    parse(text, ActionStatus::parse, "ActionStatus")
}

/// `_stored_action_hash`: the SHA-256 of the canonical action identity.
pub(crate) struct StoredAction<'a> {
    pub(crate) campaign_id: &'a str,
    pub(crate) action_id: &'a str,
    pub(crate) writer: &'a str,
    pub(crate) expected_revision: i64,
    pub(crate) turn_id: &'a str,
    pub(crate) package_id: &'a str,
    pub(crate) package_version: &'a str,
    pub(crate) package_sha256: &'a str,
    pub(crate) tool_name: &'a str,
    pub(crate) parameters: &'a JsonMap,
}

impl StoredAction<'_> {
    pub(crate) fn hash(&self) -> String {
        let encoded = dump(&object(vec![
            ("campaign_id", self.campaign_id.into()),
            ("action_id", self.action_id.into()),
            ("writer", self.writer.into()),
            ("expected_revision", self.expected_revision.into()),
            ("turn_id", self.turn_id.into()),
            ("package_id", self.package_id.into()),
            ("package_version", self.package_version.into()),
            ("package_sha256", self.package_sha256.into()),
            ("tool_name", self.tool_name.into()),
            ("parameters", Json::Object(self.parameters.clone())),
        ]));
        sha256_hex(encoded.as_bytes())
    }
}

/// `sqlite_packages.lock_lineage`: every lock the campaign has had, oldest first.
pub(crate) fn lock_lineage(
    connection: &Connection,
    campaign_id: &str,
) -> StoreResult<Vec<PackageLockRecord>> {
    let locks = Table::read(
        connection,
        "SELECT lock_id FROM campaign_locks WHERE campaign_id = ? ORDER BY created_revision",
        &[&campaign_id],
    )?;
    locks.rows.iter().map(|row| read_lock_record(connection, &py_str(&row[0]))).collect()
}

/// The keyword arguments Python passes for one campaign row.
pub(crate) struct CampaignAuthority<'a> {
    pub(crate) campaign_id: &'a str,
    pub(crate) current_lifecycle: &'a str,
    pub(crate) current_revision: i64,
    pub(crate) current_writer: &'a str,
}

/// The optional `campaign_revisions` columns, in Python's order.
const OPTIONAL_REVISION_COLUMNS: [&str; 5] = [
    "plan_id",
    "handoff_id",
    "construction_operation_id",
    "lifecycle_operation_id",
    "continuity_operation_id",
];

/// Every authority id column of a revision row, in Python's order.
const REVISION_AUTHORITIES: [&str; 7] = [
    "action_id",
    "activation_id",
    "plan_id",
    "handoff_id",
    "construction_operation_id",
    "lifecycle_operation_id",
    "continuity_operation_id",
];

/// `{str(row[key]): row}` and whether its keys were unique.
fn by_id<'r, 'a>(rows: &'r [Named<'a>], key: &str) -> StoreResult<(HashMap<String, &'r Named<'a>>, bool)> {
    let mut map = HashMap::new();
    for row in rows {
        map.insert(row.text(key)?, row);
    }
    let unique = map.len() == rows.len();
    Ok((map, unique))
}

/// `tuple(int(row[column]) for row in rows) != tuple(range(start, start + len(rows)))`, with every
/// `int()` evaluated first, as Python builds the tuple before comparing.
fn is_not_sequence(rows: &[Named<'_>], column: &str, start: i64) -> StoreResult<bool> {
    let values = rows.iter().map(|row| row.int(column)).collect::<StoreResult<Vec<_>>>()?;
    Ok(values.iter().enumerate().any(|(index, value)| *value != start + index as i64))
}

fn is_null(row: &Named<'_>, column: &str) -> StoreResult<bool> {
    Ok(row.get(column)? == &Value::Null)
}

/// `value != text` for a stored SQLite value and a Python `str`.
fn differs(value: &Value, text: &str) -> bool {
    !matches!(value, Value::Text(stored) if stored == text)
}

/// `SQLiteAuthorityStore._verify_campaign_authority`.
pub(crate) fn verify_campaign_authority(
    connection: &Connection,
    campaign: &CampaignAuthority<'_>,
) -> StoreResult<()> {
    let CampaignAuthority { campaign_id, current_lifecycle, current_revision, current_writer } = *campaign;
    let (Some(lifecycle), Some(writer_role)) =
        (LifecycleState::parse(current_lifecycle), WriterRole::parse(current_writer))
    else {
        return Err(invalid());
    };
    if !lifecycle_writer_is_legal(lifecycle, writer_role) {
        return Err(invalid());
    }
    let revision_columns: HashSet<String> =
        Table::read(connection, "PRAGMA table_info(campaign_revisions)", &[])?
            .rows
            .iter()
            .map(|row| py_str(&row[1]))
            .collect();
    let has = |name: &str| revision_columns.contains(name);
    let (has_plans, has_handoffs, has_constructions, has_lifecycles, has_continuity) = (
        has("plan_id"),
        has("handoff_id"),
        has("construction_operation_id"),
        has("lifecycle_operation_id"),
        has("continuity_operation_id"),
    );
    let revision_select = format!(
        "SELECT revision, action_id, activation_id, {} FROM campaign_revisions \
         WHERE campaign_id = ? ORDER BY revision",
        OPTIONAL_REVISION_COLUMNS
            .iter()
            .map(|name| if has(name) { (*name).to_owned() } else { format!("NULL AS {name}") })
            .collect::<Vec<_>>()
            .join(", ")
    );
    let revisions_table = Table::read(connection, &revision_select, &[&campaign_id])?;
    let revisions = revisions_table.named();
    let revision_numbers =
        revisions.iter().map(|row| row.int("revision")).collect::<StoreResult<Vec<_>>>()?;
    let expected_count = current_revision.checked_add(1).ok_or_else(invalid)?;
    if revision_numbers.len() as i64 != expected_count.max(0)
        || revision_numbers.iter().enumerate().any(|(index, number)| *number != index as i64)
    {
        return Err(invalid());
    }
    let Some(first) = revisions.first() else {
        return Err(invalid());
    };
    for column in REVISION_AUTHORITIES {
        if !is_null(first, column)? {
            return Err(invalid());
        }
    }
    for row in &revisions[1..] {
        let mut present = 0;
        for column in REVISION_AUTHORITIES {
            if !is_null(row, column)? {
                present += 1;
            }
        }
        if present != 1 {
            return Err(invalid());
        }
    }

    let actions_table = Table::read(
        connection,
        "SELECT * FROM actions WHERE campaign_id = ? ORDER BY resulting_revision",
        &[&campaign_id],
    )?;
    let activations_table = Table::read(
        connection,
        "SELECT * FROM package_activations WHERE campaign_id = ? \
         AND status = 'committed' ORDER BY resulting_revision",
        &[&campaign_id],
    )?;
    let optional = |present: bool, query: &str| -> StoreResult<Table> {
        if present {
            Table::read(connection, query, &[&campaign_id])
        } else {
            Ok(Table::empty())
        }
    };
    let plans_table = optional(
        has_plans,
        "SELECT * FROM campaign_plans WHERE campaign_id = ? ORDER BY resulting_campaign_revision",
    )?;
    let handoffs_table = optional(
        has_handoffs,
        "SELECT * FROM builder_dm_handoffs WHERE campaign_id = ? ORDER BY resulting_campaign_revision",
    )?;
    let constructions_table = optional(
        has_constructions,
        "SELECT * FROM campaign_operations WHERE campaign_id = ? AND operation_kind IN \
         ('begin_campaign_construction', 'complete_campaign_construction') ORDER BY resulting_revision",
    )?;
    let lifecycles_table = optional(
        has_lifecycles,
        "SELECT * FROM campaign_lifecycle_transitions WHERE campaign_id = ? ORDER BY resulting_revision",
    )?;
    let continuity_table = optional(
        has_continuity,
        "SELECT * FROM campaign_operations WHERE campaign_id = ? \
         AND operation_kind = 'commit_campaign_continuity' ORDER BY resulting_revision",
    )?;
    let actions = actions_table.named();
    let activations = activations_table.named();
    let plans = plans_table.named();
    let handoffs = handoffs_table.named();
    let constructions = constructions_table.named();
    let lifecycle_operations = lifecycles_table.named();
    let continuity_operations = continuity_table.named();
    let total = actions.len()
        + activations.len()
        + plans.len()
        + handoffs.len()
        + constructions.len()
        + lifecycle_operations.len()
        + continuity_operations.len();
    if total as i64 != current_revision {
        return Err(invalid());
    }
    let (action_by_id, actions_unique) = by_id(&actions, "action_id")?;
    let (activation_by_id, activations_unique) = by_id(&activations, "activation_id")?;
    let (plan_by_id, plans_unique) = by_id(&plans, "plan_id")?;
    let (handoff_by_id, handoffs_unique) = by_id(&handoffs, "handoff_id")?;
    let (construction_by_id, constructions_unique) = by_id(&constructions, "operation_id")?;
    let (lifecycle_by_id, lifecycles_unique) = by_id(&lifecycle_operations, "operation_id")?;
    let (continuity_by_id, continuity_unique) = by_id(&continuity_operations, "operation_id")?;
    if !(actions_unique
        && activations_unique
        && plans_unique
        && handoffs_unique
        && constructions_unique
        && lifecycles_unique
        && continuity_unique)
    {
        return Err(invalid());
    }
    for row in &revisions[1..] {
        let revision = row.int("revision")?;
        let sources = [
            (&action_by_id, "resulting_revision"),
            (&activation_by_id, "resulting_revision"),
            (&plan_by_id, "resulting_campaign_revision"),
            (&handoff_by_id, "resulting_campaign_revision"),
            (&construction_by_id, "resulting_revision"),
            (&lifecycle_by_id, "resulting_revision"),
        ];
        // The first non-null id names the authority; the last kind is Python's `else`.
        let mut chosen = (&continuity_by_id, "resulting_revision", "continuity_operation_id");
        for ((map, resulting), column) in sources.into_iter().zip(REVISION_AUTHORITIES) {
            if !is_null(row, column)? {
                chosen = (map, resulting, column);
                break;
            }
        }
        let (map, resulting, column) = chosen;
        match map.get(&row.text(column)?) {
            Some(authority) if authority.int(resulting)? == revision => {}
            _ => return Err(invalid()),
        }
    }

    let package_contexts: HashSet<(String, String)> = lock_lineage(connection, campaign_id)?
        .iter()
        .flat_map(|record| {
            record
                .package_lock
                .entries()
                .iter()
                .map(|entry| (entry.package_id().to_owned(), entry.version().to_owned()))
                .collect::<Vec<_>>()
        })
        .collect();
    let turns_table = Table::read(
        connection,
        "SELECT turn_id, status, created_revision FROM turns WHERE campaign_id = ? \
         ORDER BY created_revision, turn_id",
        &[&campaign_id],
    )?;
    let turns = turns_table.named();
    let mut turn_created_revisions: HashMap<String, i64> = HashMap::new();
    for turn in &turns {
        turn_created_revisions.insert(turn.text("turn_id")?, turn.int("created_revision")?);
    }
    let current_lock: Value = connection.query_row(
        "SELECT current_lock_id FROM campaigns WHERE campaign_id = ?",
        [campaign_id],
        |row| row.get(0),
    )?;
    let unbound_draft = current_lock == Value::Null
        && crate::packages::has_only_draft_lifecycle_history(connection, campaign_id, current_revision)?;
    let statuses = turns.iter().map(|turn| turn.text("status")).collect::<StoreResult<Vec<_>>>()?;
    if (turns.is_empty() && !unbound_draft)
        || turn_created_revisions.len() != turns.len()
        || statuses.iter().filter(|status| *status == "open").count() > 1
        || statuses.iter().any(|status| status != "open" && status != "completed")
        || turn_created_revisions.values().any(|revision| *revision < 0 || *revision > current_revision)
    {
        return Err(invalid());
    }
    let participants = Table::read(
        connection,
        "SELECT package_id, package_version, data_json FROM participants \
         WHERE campaign_id = ? ORDER BY participant_id",
        &[&campaign_id],
    )?;
    for participant in participants.named() {
        let context = (participant.text("package_id")?, participant.text("package_version")?);
        if !package_contexts.contains(&context)
            || !canonical_payload_is_safe(&Json::Object(load_payload(&participant.text("data_json")?)?))
        {
            return Err(invalid());
        }
    }
    let mut current_state: HashMap<(String, String), Json> = HashMap::new();
    let state_rows = Table::read(
        connection,
        "SELECT namespace, state_key, value_json FROM campaign_state \
         WHERE campaign_id = ? ORDER BY namespace, state_key",
        &[&campaign_id],
    )?;
    for state in state_rows.named() {
        let value = load_json(&state.text("value_json")?)?;
        let identity = (state.text("namespace")?, state.text("state_key")?);
        let safe = canonical_payload_is_safe(&nested(&identity.0, &identity.1, value.clone()));
        current_state.insert(identity, value);
        if !safe {
            return Err(invalid());
        }
    }

    let events_table = Table::read(
        connection,
        "SELECT * FROM canonical_events WHERE campaign_id = ? ORDER BY sequence",
        &[&campaign_id],
    )?;
    let events = events_table.named();
    if is_not_sequence(&events, "sequence", 1)? {
        return Err(invalid());
    }
    let mut event_payloads: HashMap<i64, JsonMap> = HashMap::new();
    // Insertion order is kept so the per-attempt check runs as Python's dict does.
    let mut prose_by_attempt: Vec<(String, Vec<usize>)> = Vec::new();
    let mut action_event_sequences: HashSet<i64> = HashSet::new();
    for (index, event) in events.iter().enumerate() {
        let sequence = event.int("sequence")?;
        let payload = load_payload(&event.text("safe_payload_json")?)?;
        if !canonical_payload_is_safe(&Json::Object(payload.clone())) {
            return Err(invalid());
        }
        event_payloads.insert(sequence, payload.clone());
        let kind = event_type(&event.text("event_type")?)?;
        let visibility = parse(&event.text("visibility")?, EventVisibility::parse, "EventVisibility")?;
        if matches!(kind, CanonicalEventType::Think | CanonicalEventType::Talk) {
            let mut blocks_valid = !payload.contains_key("response_blocks");
            if let (Some(blocks), CanonicalEventType::Talk) = (payload.get("response_blocks"), kind) {
                // Python catches only `ValueError` here; lane 1's checks raise nothing else.
                blocks_valid = match response_blocks_text(blocks) {
                    Ok(text) => payload.get("text") == Some(&Json::String(text)),
                    Err(_) => false,
                };
            }
            let keys: HashSet<&str> = payload.keys().map(String::as_str).collect();
            let refused = !is_null(event, "action_id")?
                || is_null(event, "attempt_id")?
                || is_null(event, "attempt_ordinal")?
                || event.int("attempt_ordinal")? < 0
                || visibility != EventVisibility::PlayerSafe
                || !(keys == HashSet::from(["text"]) || keys == HashSet::from(["text", "response_blocks"]))
                || !matches!(payload.get("text"), Some(Json::String(text)) if !text.is_empty())
                || !blocks_valid
                || !turn_created_revisions.contains_key(&event.text("turn_id")?);
            if refused {
                return Err(invalid());
            }
            let attempt = event.text("attempt_id")?;
            match prose_by_attempt.iter_mut().find(|(id, _)| *id == attempt) {
                Some((_, members)) => members.push(index),
                None => prose_by_attempt.push((attempt, vec![index])),
            }
        } else {
            let expected_visibility =
                if matches!(kind, CanonicalEventType::ToolCall | CanonicalEventType::ToolResult) {
                    EventVisibility::PlayerSafe
                } else {
                    EventVisibility::InternalSafe
                };
            if is_null(event, "action_id")?
                || !action_by_id.contains_key(&event.text("action_id")?)
                || !is_null(event, "attempt_id")?
                || !is_null(event, "attempt_ordinal")?
                || visibility != expected_visibility
            {
                return Err(invalid());
            }
            action_event_sequences.insert(sequence);
        }
    }
    for (_, members) in &prose_by_attempt {
        let ordinals = members
            .iter()
            .map(|index| events[*index].int("attempt_ordinal"))
            .collect::<StoreResult<Vec<_>>>()?;
        if ordinals.iter().enumerate().any(|(index, ordinal)| *ordinal != index as i64) {
            return Err(invalid());
        }
    }

    let lifecycle_state_table = if has_lifecycles {
        let table = Table::read(
            connection,
            "SELECT * FROM campaign_lifecycle_state WHERE campaign_id = ?",
            &[&campaign_id],
        )?;
        if table.rows.is_empty() {
            return Err(invalid());
        }
        Some(table)
    } else {
        None
    };
    let lifecycle_state = lifecycle_state_table
        .as_ref()
        .map(|table| Named { columns: &table.columns, values: table.rows[0].clone() });

    enum WriterEvent<'r, 'a> {
        Handoff(&'r Named<'a>),
        Lifecycle(&'r Named<'a>),
    }
    let mut writer_events: HashMap<i64, WriterEvent<'_, '_>> = HashMap::new();
    for handoff in &handoffs {
        let revision = handoff.int("resulting_campaign_revision")?;
        if writer_events.insert(revision, WriterEvent::Handoff(handoff)).is_some() {
            return Err(invalid());
        }
    }
    for transition in &lifecycle_operations {
        let revision = transition.int("resulting_revision")?;
        if writer_events.insert(revision, WriterEvent::Lifecycle(transition)).is_some() {
            return Err(invalid());
        }
    }

    let initial_writer = if !handoffs.is_empty() {
        WriterRole::Builder.as_str().to_owned()
    } else if let Some(first) = actions.first() {
        first.text("writer")?
    } else if !lifecycle_operations.is_empty() {
        let state = lifecycle_state.as_ref().ok_or_else(invalid)?;
        match state.get("retained_active_writer")? {
            Value::Null => return Err(invalid()),
            retained => py_str(retained),
        }
    } else {
        current_writer.to_owned()
    };
    if initial_writer != WriterRole::Builder.as_str() && initial_writer != WriterRole::Dm.as_str() {
        return Err(invalid());
    }

    let mut writer_by_revision: HashMap<i64, String> = HashMap::from([(0, initial_writer.clone())]);
    let mut writer_cursor = initial_writer;
    for revision_number in 1..=current_revision {
        match writer_events.get(&revision_number) {
            Some(WriterEvent::Handoff(row)) => {
                if row.int("expected_revision")? != revision_number - 1
                    || writer_cursor != WriterRole::Builder.as_str()
                {
                    return Err(invalid());
                }
                writer_cursor = WriterRole::Dm.as_str().to_owned();
            }
            Some(WriterEvent::Lifecycle(row)) => {
                if row.int("basis_revision")? != revision_number - 1
                    || row.text("from_writer")? != writer_cursor
                    || WriterRole::parse(&row.text("to_writer")?).is_none()
                {
                    return Err(invalid());
                }
                writer_cursor = row.text("to_writer")?;
            }
            None => {}
        }
        writer_by_revision.insert(revision_number, writer_cursor.clone());
    }
    if writer_cursor != current_writer {
        return Err(invalid());
    }

    if let Some(state) = &lifecycle_state {
        let retained_writer = state.get("retained_active_writer")?;
        let prior_lifecycle = state.get("tombstone_prior_lifecycle")?;
        let prior_writer = state.get("tombstone_prior_writer")?;
        if retained_writer != &Value::Null {
            let retained = py_str(retained_writer);
            if retained != WriterRole::Builder.as_str() && retained != WriterRole::Dm.as_str() {
                return Err(invalid());
            }
        }
        if let Some(latest) = lifecycle_operations.last() {
            // A later Builder-to-GM handoff can change the writer without another lifecycle
            // transition. The full writer sequence was checked above.
            if latest.text("to_lifecycle")? != current_lifecycle {
                return Err(invalid());
            }
            let mut previous = LifecycleState::Active.as_str().to_owned();
            for transition in &lifecycle_operations {
                if transition.text("from_lifecycle")? != previous {
                    return Err(invalid());
                }
                previous = transition.text("to_lifecycle")?;
            }
        }
        let refused = match lifecycle {
            LifecycleState::Active => {
                differs(retained_writer, current_writer) || prior_lifecycle != &Value::Null
            }
            LifecycleState::Archived => {
                current_writer != WriterRole::None.as_str() || prior_lifecycle != &Value::Null
            }
            LifecycleState::Tombstoned => {
                (differs(prior_lifecycle, "active") && differs(prior_lifecycle, "archived"))
                    || prior_writer == &Value::Null
            }
        };
        if refused {
            return Err(invalid());
        }
    }

    let mut mutation_final_values: Vec<((String, String), Json)> = Vec::new();
    for action in &actions {
        let action_id = action.text("action_id")?;
        let expected_revision = action.int("expected_revision")?;
        let resulting_revision = action.int("resulting_revision")?;
        let writer = action.text("writer")?;
        let turn_id = action.text("turn_id")?;
        let package_id = action.text("package_id")?;
        let package_version = action.text("package_version")?;
        let package_sha256 = action.text("package_sha256")?;
        let tool_name = action.text("tool_name")?;
        let status = action_status(&action.text("status")?)?;
        let parameters = load_payload(&action.text("payload_json")?)?;
        let result = load_payload(&action.text("result_json")?)?;
        let payload_hash = action.text("payload_hash")?;
        let exact_action_pin = match action.get("lock_id")? {
            Value::Null => false,
            lock_id => connection
                .query_row(
                    "SELECT 1 FROM campaign_lock_memberships WHERE lock_id = ? \
                     AND package_id = ? AND version = ? AND content_sha256 = ?",
                    [py_str(lock_id), package_id.clone(), package_version.clone(), package_sha256.clone()],
                    |_| Ok(()),
                )
                .optional()?
                .is_some(),
        };
        let refused = expected_revision != resulting_revision.checked_sub(1).ok_or_else(invalid)?
            || resulting_revision < 1
            || resulting_revision > current_revision
            || revisions[resulting_revision as usize].text("action_id")? != action_id
            || writer_by_revision.get(&expected_revision) != Some(&writer)
            || !turn_created_revisions.contains_key(&turn_id)
            || expected_revision < turn_created_revisions[&turn_id]
            || !exact_action_pin
            || !canonical_payload_is_safe(&Json::Object(parameters.clone()))
            || !canonical_payload_is_safe(&Json::Object(result.clone()));
        if refused {
            return Err(invalid());
        }
        let stored = StoredAction {
            campaign_id,
            action_id: &action_id,
            writer: &writer,
            expected_revision,
            turn_id: &turn_id,
            package_id: &package_id,
            package_version: &package_version,
            package_sha256: &package_sha256,
            tool_name: &tool_name,
            parameters: &parameters,
        };
        if payload_hash != stored.hash() {
            return Err(invalid());
        }

        let mutations_table = Table::read(
            connection,
            "SELECT * FROM mutations WHERE campaign_id = ? AND action_id = ? ORDER BY ordinal",
            &[&campaign_id, &action_id],
        )?;
        let consequences_table = Table::read(
            connection,
            "SELECT * FROM consequences WHERE campaign_id = ? AND action_id = ? ORDER BY ordinal",
            &[&campaign_id, &action_id],
        )?;
        let mutations = mutations_table.named();
        let consequences = consequences_table.named();
        if is_not_sequence(&mutations, "ordinal", 0)? || is_not_sequence(&consequences, "ordinal", 0)? {
            return Err(invalid());
        }

        let mut action_events: Vec<&Named<'_>> = Vec::new();
        for event in &events {
            if !is_null(event, "action_id")? && event.text("action_id")? == action_id {
                action_events.push(event);
            }
        }
        let terminal = if status == ActionStatus::Committed {
            CanonicalEventType::ActionCommitted
        } else {
            CanonicalEventType::ActionFailed
        };
        let mut expected_types = vec![
            CanonicalEventType::ActionDeclared,
            CanonicalEventType::ToolCall,
            CanonicalEventType::ToolResult,
        ];
        expected_types.extend(mutations.iter().map(|_| CanonicalEventType::StateMutation));
        expected_types.extend(consequences.iter().map(|_| CanonicalEventType::Consequence));
        expected_types.push(terminal);
        let action_sequences =
            action_events.iter().map(|event| event.int("sequence")).collect::<StoreResult<Vec<_>>>()?;
        let mut refused = action_sequences.is_empty()
            || action_sequences.len() != expected_types.len()
            || action_sequences
                .iter()
                .enumerate()
                .any(|(index, sequence)| *sequence != action_sequences[0] + index as i64);
        if !refused {
            let types = action_events
                .iter()
                .map(|event| event_type(&event.text("event_type")?))
                .collect::<StoreResult<Vec<_>>>()?;
            refused = types != expected_types;
        }
        if !refused {
            for event in &action_events {
                if event.text("turn_id")? != turn_id {
                    refused = true;
                    break;
                }
            }
        }
        if refused {
            return Err(invalid());
        }
        if status == ActionStatus::Failed && (!mutations.is_empty() || consequences.len() > 1) {
            return Err(invalid());
        }

        let payloads: Vec<Json> = action_sequences
            .iter()
            .map(|sequence| Json::Object(event_payloads.get(sequence).cloned().unwrap_or_default()))
            .collect();
        let declared =
            object(vec![("action_id", action_id.as_str().into()), ("writer", writer.as_str().into())]);
        let called = object(vec![
            ("package_id", package_id.as_str().into()),
            ("package_version", package_version.as_str().into()),
            ("package_sha256", package_sha256.as_str().into()),
            ("tool_name", tool_name.as_str().into()),
            ("parameters", Json::Object(parameters.clone())),
        ]);
        let returned =
            object(vec![("status", status.as_str().into()), ("result", Json::Object(result.clone()))]);
        let finished = object(vec![
            ("status", status.as_str().into()),
            ("resulting_revision", resulting_revision.into()),
        ]);
        if !py_eq(&payloads[0], &declared)
            || !py_eq(&payloads[1], &called)
            || !py_eq(&payloads[2], &returned)
            || !py_eq(&payloads[payloads.len() - 1], &finished)
        {
            return Err(invalid());
        }
        for (ordinal, mutation) in mutations.iter().enumerate() {
            let before = load_json(&mutation.text("before_json")?)?;
            let after = load_json(&mutation.text("after_json")?)?;
            let namespace = mutation.text("namespace")?;
            let key = mutation.text("state_key")?;
            let identity = (namespace.clone(), key.clone());
            let previous =
                mutation_final_values.iter().find(|(known, _)| *known == identity).map(|(_, value)| value);
            if !canonical_payload_is_safe(&nested(&namespace, &key, before.clone()))
                || !canonical_payload_is_safe(&nested(&namespace, &key, after.clone()))
                || !py_eq(
                    &payloads[3 + ordinal],
                    &object(vec![("namespace", namespace.as_str().into()), ("key", key.as_str().into())]),
                )
                || previous.is_some_and(|value| !py_eq(value, &before))
            {
                return Err(invalid());
            }
            match mutation_final_values.iter_mut().find(|(known, _)| *known == identity) {
                Some((_, value)) => *value = after,
                None => mutation_final_values.push((identity, after)),
            }
        }
        let consequence_offset = 3 + mutations.len();
        for (ordinal, consequence) in consequences.iter().enumerate() {
            let consequence_payload = load_payload(&consequence.text("payload_json")?)?;
            if let Some(kind) = consequence_payload.get("kind") {
                if kind != &Json::Null {
                    validate_authority_identity_value(kind, "Stored consequence kind", false)?;
                }
            }
            if !canonical_payload_is_safe(&Json::Object(consequence_payload.clone()))
                || !py_eq(&payloads[consequence_offset + ordinal], &Json::Object(consequence_payload.clone()))
                || (status == ActionStatus::Failed
                    && !valid_player_action_consequence(&consequence_payload, expected_revision))
            {
                return Err(invalid());
            }
        }

        let idempotency = Table::read(
            connection,
            "SELECT payload_hash, status, result_json, resulting_revision \
             FROM idempotency_records WHERE campaign_id = ? AND action_id = ? ORDER BY idempotency_key",
            &[&campaign_id, &action_id],
        )?;
        let idempotency_rows = idempotency.named();
        if idempotency_rows.is_empty() {
            return Err(invalid());
        }
        for row in &idempotency_rows {
            if row.text("payload_hash")? != payload_hash
                || action_status(&row.text("status")?)? != status
                || !py_eq(
                    &Json::Object(load_payload(&row.text("result_json")?)?),
                    &Json::Object(result.clone()),
                )
                || row.int("resulting_revision")? != resulting_revision
            {
                return Err(invalid());
            }
        }
    }
    let mut with_action = 0;
    for event in &events {
        if !is_null(event, "action_id")? {
            with_action += 1;
        }
    }
    if action_event_sequences.len() != with_action {
        return Err(invalid());
    }

    let interruptions_table = Table::read(
        connection,
        "SELECT * FROM narration_interruptions WHERE campaign_id = ?",
        &[&campaign_id],
    )?;
    for interruption in interruptions_table.named() {
        let turn_id = interruption.text("turn_id")?;
        let last_action_id = interruption.get("last_action_id")?;
        let interruption_status = interruption.text("status")?;
        let retry_id = interruption.get("resolving_retry_id")?;
        let resolved_sequence = interruption.get("resolved_sequence")?;
        let last_action_refused = match last_action_id {
            Value::Null => false,
            value => match action_by_id.get(&py_str(value)) {
                None => true,
                Some(action) => action.text("turn_id")? != turn_id,
            },
        };
        if !turn_created_revisions.contains_key(&turn_id)
            || last_action_refused
            || (interruption_status != "retryable" && interruption_status != "resolved")
            || (interruption_status == "retryable" && resolved_sequence != &Value::Null)
            || (interruption_status == "resolved"
                && (retry_id == &Value::Null || resolved_sequence == &Value::Null))
        {
            return Err(invalid());
        }
        let mut retry_events: Vec<&Named<'_>> = Vec::new();
        if retry_id != &Value::Null {
            let retry = py_str(retry_id);
            for event in &events {
                if !is_null(event, "attempt_id")? && event.text("attempt_id")? == retry {
                    retry_events.push(event);
                }
            }
        }
        for event in &retry_events {
            if event.text("turn_id")? != turn_id {
                return Err(invalid());
            }
        }
        if resolved_sequence != &Value::Null {
            let resolved = || {
                crate::pytext::py_int(resolved_sequence)
                    .ok_or_else(|| value_error("invalid literal for int() with base 10"))
            };
            let mut matched = None;
            for event in &events {
                if event.int("sequence")? == resolved()? {
                    matched = Some(event);
                    break;
                }
            }
            let refused = match matched {
                None => true,
                Some(_) if retry_events.is_empty() => true,
                Some(event) => {
                    let mut last = i64::MIN;
                    for retry_event in &retry_events {
                        last = last.max(retry_event.int("sequence")?);
                    }
                    differs(event.get("event_type")?, CanonicalEventType::Talk.as_str())
                        || resolved()? != last
                        || event.text("attempt_id")? != py_str(retry_id)
                }
            };
            if refused {
                return Err(invalid());
            }
        }
    }
    for (identity, value) in &mutation_final_values {
        match current_state.get(identity) {
            Some(current) if py_eq(current, value) => {}
            _ => return Err(invalid()),
        }
    }

    let orphans = Table::read(
        connection,
        "SELECT action_id FROM idempotency_records WHERE campaign_id = ?",
        &[&campaign_id],
    )?;
    for row in &orphans.rows {
        if !action_by_id.contains_key(&py_str(&row[0])) {
            return Err(invalid());
        }
    }
    Ok(())
}
