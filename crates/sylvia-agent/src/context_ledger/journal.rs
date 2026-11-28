//! The wire journal and Claude Code's native records (L3): the journal's state for one context
//! epoch and generation, rebuilt from saved `gm_wire_journal` evidence and checked link by link,
//! and the `gm_native_records` evidence of what each request's Claude Code session file held.
//!
//! A saved value is read as Python reads it: `isinstance(x, int)` admits a boolean (so a saved
//! `true` generation is generation 1, printed `True`), `int(x)` converts a float or a string,
//! and comparisons are Python's `==`.

use super::state::reconstruct_state;
use super::tools::saved;
use super::wire::{canonical_json, sha};
use super::{JOURNAL_KIND, JOURNAL_VERSION, NATIVE_KIND, NATIVE_STATUSES, NATIVE_VERSION, TOOL_CHANGE_CLASS};
use indexmap::IndexMap;
use serde_json::Value as Json;
use std::collections::{BTreeMap, BTreeSet};
use sylvia_base::JsonMap;
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::pyops::{py_eq, py_iter, py_require_hashable, py_str, py_truthy, py_type_name};

/// `isinstance(value, int)`: an integer or a boolean.
fn is_int(value: &Json) -> bool {
    matches!(value, Json::Bool(_)) || value.as_i64().is_some() || value.as_u64().is_some()
}

/// `int(value)`: a boolean or integer as itself, a float truncated, a string parsed; anything
/// else is Python's `TypeError`.
pub fn py_int(value: &Json) -> DomainResult<i64> {
    match value {
        Json::Bool(flag) => Ok(i64::from(*flag)),
        Json::Number(number) => {
            if let Some(integer) = number.as_i64() {
                return Ok(integer);
            }
            let float = number.as_f64().unwrap_or(f64::NAN);
            if float.is_finite() && float.abs() < 9.2e18 {
                Ok(float.trunc() as i64)
            } else {
                Err(DomainError::uncaught("OverflowError", "cannot convert float to integer"))
            }
        }
        Json::String(text) => sylvia_base::pystr::parse_int(text)
            .map_err(|error| DomainError::uncaught("ValueError", error.to_string())),
        other => Err(DomainError::type_error(format!(
            "int() argument must be a string, a bytes-like object or a real number, not '{}'",
            match other {
                Json::Null => "NoneType",
                other => py_type_name(other),
            }
        ))),
    }
}

/// `int(value or 0)`.
fn int_or_zero(value: Option<&Json>) -> DomainResult<i64> {
    match value {
        Some(value) if py_truthy(value) => py_int(value),
        _ => Ok(0),
    }
}

/// `_genesis(epoch, generation)`: the chain's first head.
pub fn genesis(epoch: &str, generation: &Json) -> String {
    sha(&format!("{JOURNAL_KIND}/v{JOURNAL_VERSION}|{epoch}|{}", py_str(generation)))
}

/// `_chain(head, entries)`: the head after appending `entries`, one link each.
pub fn chain(head: &str, entries: &[Json]) -> String {
    let mut head = head.to_owned();
    for entry in entries {
        head = sha(&format!("{head}\n{}", canonical_json(entry)));
    }
    head
}

/// `_valid_entry(entry)`: a journal entry, its message and class as the ledger writes them. The
/// class is looked up in a set, so an unhashable one is Python's `TypeError`.
pub fn valid_entry(entry: &Json) -> DomainResult<bool> {
    let Json::Object(entry) = entry else {
        return Ok(false);
    };
    let role_is_text = matches!(entry.get("m"), Some(Json::Object(message)) if message.get("role").is_some_and(Json::is_string));
    if !role_is_text {
        return Ok(false);
    }
    let class = entry.get("c").unwrap_or(&Json::Null);
    py_require_hashable(class)?;
    Ok(class.as_str().is_some_and(|class| {
        ["conversation", "latest", "event", "state", TOOL_CHANGE_CLASS].contains(&class)
    }))
}

/// `all(_valid_entry(entry) for entry in entries)`: stops at the first invalid one.
fn all_valid(entries: &[Json]) -> DomainResult<bool> {
    for entry in entries {
        if !valid_entry(entry)? {
            return Ok(false);
        }
    }
    Ok(true)
}

/// `JournalState`: one context epoch and generation of the wire journal.
#[derive(Debug, Clone, PartialEq)]
pub struct JournalState {
    pub epoch: String,
    /// A Python `int`: an integer, or a boolean a saved record carried.
    pub generation: Json,
    pub entries: Vec<JsonMap>,
    pub head: String,
    pub view: Option<JsonMap>,
    pub keyframe_bytes: i64,
    pub update_bytes: i64,
    /// The chain head after each journal length, to recognise saved records on this chain.
    pub heads: BTreeMap<usize, String>,
    /// `(label, digest)` of the host records in the previous request's canonical list.
    pub present: BTreeSet<(String, String)>,
    /// `claude_code` only: the journal length that ended a request, and the CLI's records for
    /// it (`status`, `records`, and `answer` when a kept process answered in place), in the
    /// order they were found.
    pub native: IndexMap<i64, JsonMap>,
    /// `anthropic_messages` only: assistant turns below this journal index get no thinking
    /// blocks reattached. Moves only forward within a generation.
    pub thinking_floor: i64,
    /// The previous request's system-prefix and tool-set fingerprints in this generation.
    pub last_prefix: Option<Json>,
    pub last_toolset: Option<Json>,
}

impl JournalState {
    /// `JournalState.fresh(epoch, generation)`.
    pub fn fresh(epoch: &str, generation: Json) -> Self {
        let head = genesis(epoch, &generation);
        Self {
            epoch: epoch.to_owned(),
            generation,
            entries: Vec::new(),
            heads: BTreeMap::from([(0, head.clone())]),
            head,
            view: None,
            keyframe_bytes: 0,
            update_bytes: 0,
            present: BTreeSet::new(),
            native: IndexMap::new(),
            thinking_floor: 0,
            last_prefix: None,
            last_toolset: None,
        }
    }
}

/// `max(values)` of Python `int`s (integers and booleans): the first of equal maxima is kept.
fn py_max(values: &[Json]) -> DomainResult<Option<Json>> {
    let mut best: Option<(i64, &Json)> = None;
    for value in values {
        let number = py_int(value)?;
        if best.is_none_or(|(best, _)| number > best) {
            best = Some((number, value));
        }
    }
    Ok(best.map(|(_, value)| value.clone()))
}

/// `latest + 1`.
fn plus_one(value: &Json) -> DomainResult<Json> {
    Ok(Json::from(py_int(value)? + 1))
}

/// What [`saved_tip`] answers: the newest saved record's generation, journal length and head.
#[derive(Debug, Clone, PartialEq)]
pub struct SavedTip {
    pub generation: i64,
    pub count: i64,
    pub head: String,
}

/// `saved_tip(records, epoch)`: the generation, journal length and head of the newest saved
/// record for `epoch`.
pub fn saved_tip(records: &[Json], epoch: &str) -> DomainResult<Option<SavedTip>> {
    let rows: Vec<&JsonMap> = records
        .iter()
        .filter(|record| saved(record, JOURNAL_KIND))
        .filter_map(Json::as_object)
        .filter(|record| record.get("epoch").and_then(Json::as_str) == Some(epoch))
        .filter(|record| record.get("generation").is_some_and(is_int))
        .collect();
    if rows.is_empty() {
        return Ok(None);
    }
    let mut latest = i64::MIN;
    for row in &rows {
        latest = latest.max(py_int(&row["generation"])?);
    }
    let last = rows
        .iter()
        .rev()
        .find(|row| py_eq(&row["generation"], &Json::from(latest)))
        .expect("the maximum is one of the rows");
    let entries = match last.get("entries") {
        Some(Json::Array(items)) => items.len() as i64,
        Some(Json::String(text)) => sylvia_base::pystr::len(text) as i64,
        _ => 0,
    };
    let count = int_or_zero(last.get("base_count"))? + entries;
    Ok(Some(SavedTip { generation: latest, count, head: py_str(last.get("head").unwrap_or(&Json::Null)) }))
}

/// `load_journal(records, epoch=..., context_owner=..., seal=...)`: the epoch's latest
/// generation rebuilt from saved evidence; invalid evidence is discarded. The second value is a
/// reset reason (`journal_invalid`), `None` when the saved chain is valid or absent.
pub fn load_journal(
    records: &[Json],
    epoch: &str,
    context_owner: Option<&Json>,
    seal: &str,
) -> DomainResult<(JournalState, Option<&'static str>)> {
    let epoch_json = Json::from(epoch);
    let rows: Vec<&JsonMap> = records
        .iter()
        .filter(|record| saved(record, JOURNAL_KIND))
        .filter_map(Json::as_object)
        .filter(|record| record.get("epoch").is_some_and(|value| py_eq(value, &epoch_json)))
        .collect();
    if rows.is_empty() {
        return Ok((JournalState::fresh(epoch, Json::from(0)), None));
    }
    let generations: Vec<Json> =
        rows.iter().filter_map(|row| row.get("generation")).filter(|value| is_int(value)).cloned().collect();
    let latest = py_max(&generations)?.unwrap_or_else(|| Json::from(0));
    let chain_rows: Vec<&&JsonMap> =
        rows.iter().filter(|row| py_eq(row.get("generation").unwrap_or(&Json::Null), &latest)).collect();
    let mut state = JournalState::fresh(epoch, latest.clone());
    let mut valid = !chain_rows.is_empty() && generations.len() == rows.len();
    if valid {
        for record in chain_rows {
            let entries = match record.get("entries") {
                Some(Json::Array(entries)) => Some(entries),
                _ => None,
            };
            let linked = record
                .get("version")
                .is_some_and(|version| py_eq(version, &Json::from(JOURNAL_VERSION)))
                && context_owner
                    .is_none_or(|owner| py_eq(record.get("context_owner").unwrap_or(&Json::Null), owner))
                && record
                    .get("base_count")
                    .is_some_and(|count| py_eq(count, &Json::from(state.entries.len())))
                && record.get("previous_head").is_some_and(|head| head.as_str() == Some(state.head.as_str()))
                && match entries {
                    Some(entries) => all_valid(entries)?,
                    None => false,
                }
                && entries.is_some_and(|entries| {
                    record.get("head").and_then(Json::as_str) == Some(chain(&state.head, entries).as_str())
                });
            let Some(entries) = entries.filter(|_| linked) else {
                valid = false;
                break;
            };
            state.entries.extend(entries.iter().filter_map(Json::as_object).cloned());
            state.head = py_str(&record["head"]);
            state.heads.insert(state.entries.len(), state.head.clone());
            state.keyframe_bytes = match record.get("keyframe_bytes") {
                Some(value) => int_or_zero(Some(value))?,
                None => state.keyframe_bytes,
            };
            state.update_bytes = match record.get("update_bytes") {
                Some(value) => int_or_zero(Some(value))?,
                None => state.update_bytes,
            };
            if let Some(floor) = record.get("thinking_floor").and_then(Json::as_i64) {
                state.thinking_floor = state.thinking_floor.max(floor);
            }
            if let Some(prefix) = record.get("prefix_sha256").filter(|value| py_truthy(value)) {
                state.last_prefix = Some(prefix.clone());
            }
            if let Some(toolset) = record.get("toolset_sha256").filter(|value| py_truthy(value)) {
                state.last_toolset = Some(toolset.clone());
            }
        }
    }
    if !valid {
        return Ok((JournalState::fresh(epoch, plus_one(&latest)?), Some("journal_invalid")));
    }
    for record in records {
        // Saved Claude Code context records count only for a request on this exact chain.
        if !saved(record, NATIVE_KIND) {
            continue;
        }
        let record = record.as_object().expect("saved evidence is a mapping");
        let field = |key: &str| record.get(key).unwrap_or(&Json::Null);
        let status = field("status");
        let earlier = py_eq(field("version"), &Json::from(NATIVE_VERSION))
            && py_eq(field("epoch"), &epoch_json)
            && py_eq(field("generation"), &state.generation)
            && context_owner.is_none_or(|owner| py_eq(field("context_owner"), owner))
            && is_int(field("boundary"));
        if !earlier {
            continue;
        }
        let boundary = py_int(field("boundary"))?;
        // `state.heads.get(boundary) == record.get("head")`: a missing head on both sides matches.
        let head = usize::try_from(boundary)
            .ok()
            .and_then(|key| state.heads.get(&key))
            .map_or(Json::Null, |head| Json::from(head.as_str()));
        if !py_eq(&head, field("head")) {
            continue;
        }
        // `status in NATIVE_STATUSES` hashes the status, so a list or mapping raises here.
        py_require_hashable(status)?;
        if !status.as_str().is_some_and(|status| NATIVE_STATUSES.contains(&status))
            || !matches!(field("records"), Json::Array(_) | Json::String(_))
        {
            continue;
        }
        let mut native = JsonMap::new();
        native.insert("status".into(), status.clone());
        native.insert("records".into(), field("records").clone());
        if let Some(answer @ Json::Object(_)) = record.get("answer") {
            native.insert("answer".into(), answer.clone());
        }
        state.native.insert(boundary, native);
    }
    state.view = reconstruct_state(&state.entries, seal)?;
    Ok((state, None))
}

/// The boundaries of earlier requests in this journal: the saved lengths strictly inside
/// `(0, end)`, in order.
fn replayed(state: &JournalState, end: usize) -> Vec<usize> {
    state.heads.keys().copied().filter(|key| 0 < *key && *key < end).collect()
}

fn native_status(state: &JournalState, key: i64) -> Option<&str> {
    state.native.get(&key).and_then(|native| native.get("status")).and_then(Json::as_str)
}

/// `native_gap(state, end)`: would a transcript ending at journal length `end` replay unknown
/// CLI records? The first request of a generation always leaves records, so a missing first
/// entry means they were lost.
pub fn native_gap(state: &JournalState, end: usize) -> bool {
    let replayed = replayed(state, end);
    let Some(first) = replayed.first() else {
        return false;
    };
    if !state.native.contains_key(&(*first as i64)) {
        return true;
    }
    replayed.iter().any(|key| native_status(state, *key as i64) == Some("unknown"))
}

/// What [`native_inputs`] answers: the boundaries of earlier requests, and their collected CLI
/// records.
#[derive(Debug, Clone, PartialEq)]
pub struct NativeInputs {
    pub boundaries: Vec<usize>,
    pub records: IndexMap<i64, Vec<Json>>,
}

/// `native_inputs(state)`: the boundaries of earlier requests and their collected CLI records,
/// for the transcript.
pub fn native_inputs(state: &JournalState) -> DomainResult<NativeInputs> {
    let boundaries = replayed(state, state.entries.len());
    let mut records = IndexMap::new();
    for (key, native) in &state.native {
        let inside = usize::try_from(*key).is_ok_and(|key| boundaries.contains(&key));
        if inside && native_status(state, *key) == Some("collected") {
            let listed = match native.get("records") {
                Some(value) if py_truthy(value) => py_iter(value)?,
                _ => Vec::new(),
            };
            records.insert(*key, listed);
        }
    }
    Ok(NativeInputs { boundaries, records })
}

/// `native_answers(state)`: earlier boundaries whose call a kept Claude Code process answered
/// in place.
pub fn native_answers(state: &JournalState) -> IndexMap<i64, JsonMap> {
    let end = state.entries.len() as i64;
    state
        .native
        .iter()
        .filter(|(key, _)| 0 < **key && **key < end)
        .filter(|(key, _)| native_status(state, **key) == Some("collected"))
        .filter_map(|(key, native)| {
            native.get("answer").and_then(Json::as_object).map(|answer| (*key, answer.clone()))
        })
        .collect()
}

/// `native_record(state, context_owner=..., boundary=..., status=..., reason=..., records=...,
/// answer=...)`: session evidence for what one request's Claude Code session file contained.
/// `record_id` is the `uuid4().hex` Python draws for it.
#[allow(clippy::too_many_arguments)]
pub fn native_record(
    state: &JournalState,
    record_id: &str,
    context_owner: Option<&Json>,
    boundary: usize,
    status: &str,
    reason: Option<&str>,
    records: &[Json],
    answer: Option<&JsonMap>,
) -> JsonMap {
    let mut record = JsonMap::new();
    record.insert("kind".into(), NATIVE_KIND.into());
    record.insert("version".into(), NATIVE_VERSION.into());
    record.insert("native_record_id".into(), record_id.into());
    if let Some(owner) = context_owner {
        record.insert("context_owner".into(), owner.clone());
    }
    record.insert("epoch".into(), state.epoch.clone().into());
    record.insert("generation".into(), state.generation.clone());
    record.insert("boundary".into(), boundary.into());
    record.insert("head".into(), state.heads.get(&boundary).map_or(Json::Null, |head| head.clone().into()));
    record.insert("status".into(), status.into());
    record.insert("reason".into(), reason.map_or(Json::Null, Json::from));
    record.insert("records".into(), Json::Array(records.to_vec()));
    if let Some(answer) = answer {
        record.insert("answer".into(), Json::Object(answer.clone()));
    }
    record
}
