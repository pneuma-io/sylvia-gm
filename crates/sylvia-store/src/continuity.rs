//! Campaign continuity, part 1: the Campaign State records, their view, their verifier, the
//! legacy-evidence backfill of migration 9 and the revision-fenced commit batch.
//!
//! Python: `sqlite_continuity.py` (`append_record`, `read_view`, `verify_authority`,
//! `_backfill_legacy_messages`, `_load_mapping`, `_next_sequence`) and
//! `SQLiteAuthorityStore.commit_campaign_continuity(_batch)` with the campaign-operation
//! receipt helpers it uses (`_campaign_operation_hit`, `_insert_campaign_operation`, the
//! continuity branch of `_campaign_operation_result`).
//!
//! Sessions part 2 adds what reads accepted Campaign Plans: `read_act_markers` (with
//! `_act_marker_anchor`), `read_campaign_ending(s)`, `seed_initial_state` (with
//! `_initial_entry_id`) and the seeding step of `apply_migration`, on lane 1's
//! `campaign_ending` and `no_ruleset_plan`.

use crate::actions::{dump, load_payload, rejected};
use crate::campaigns::{named_rows, Named};
use crate::error::{schema_identity, value_error, StoreError, StoreResult};
use crate::pytext::{py_int, py_str};
use crate::sequel::{adventure_rows, bind_json, json_loads, PlanRow};
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use rusqlite::{params, Connection, OptionalExtension};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    canonical_payload_is_safe, owned_payload, validate_authority_identity, CampaignContinuityCommand,
    CampaignContinuityReceipt, CampaignContinuityRecord, CampaignContinuityView, LifecycleState, WriterRole,
};
use sylvia_domain::campaign_ending::{
    ending_progress_id, intended_endings, required_ending_items, required_story_items, resolved_progress,
};
use sylvia_domain::no_ruleset_plan::{is_no_ruleset_plan, no_ruleset_world_records};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::{py_eq, py_get_attr, py_item, py_iter, py_type_name};
use sylvia_domain::session::SessionStatus;

/// `_load_mapping`: plain `json.loads`, `None` for anything that is not a JSON object.
fn load_mapping(text: &str) -> Option<JsonMap> {
    match sylvia_base::pyjson::loads(text) {
        Ok(Json::Object(map)) => Some(map),
        _ => None,
    }
}

/// `_dump` of `sqlite_continuity`: canonical text.
fn continuity_dump(value: &Json) -> String {
    dump(value)
}

/// `_next_sequence`.
fn next_sequence(connection: &Connection, campaign_id: &str) -> StoreResult<i64> {
    Ok(connection.query_row(
        "SELECT COALESCE(MAX(sequence), 0) + 1 FROM campaign_continuity_records WHERE campaign_id = ?",
        [campaign_id],
        |row| row.get(0),
    )?)
}

/// One `append_record` call.
pub(crate) struct ContinuityAppend<'a> {
    pub campaign_id: &'a str,
    pub entry_id: &'a str,
    pub record_id: &'a str,
    pub kind: &'a str,
    pub status: &'a str,
    pub payload: &'a JsonMap,
    pub basis_message_ids: &'a [String],
    pub operation_id: Option<&'a str>,
    pub admitted_revision: i64,
}

/// `append_record`: one immutable continuity change inside the caller's transaction.
pub(crate) fn append_record(connection: &Connection, record: &ContinuityAppend<'_>) -> StoreResult<()> {
    let basis = Json::Array(record.basis_message_ids.iter().cloned().map(Json::String).collect());
    connection.execute(
        "INSERT INTO campaign_continuity_records \
         (campaign_id, sequence, entry_id, record_id, kind, status, payload_json, \
         basis_message_ids_json, operation_id, admitted_revision) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        params![
            record.campaign_id,
            next_sequence(connection, record.campaign_id)?,
            record.entry_id,
            record.record_id,
            record.kind,
            record.status,
            continuity_dump(&Json::Object(record.payload.clone())),
            continuity_dump(&basis),
            record.operation_id,
            record.admitted_revision,
        ],
    )?;
    Ok(())
}

fn int(value: &Value) -> StoreResult<i64> {
    py_int(value).ok_or_else(|| value_error("invalid literal for int() with base 10"))
}

/// `{**payload, "record_id": ..., "record_kind": ..., "admitted_revision": ...}`.
fn with_record_fields(payload: &JsonMap, record_id: &str, kind: &str, revision: i64) -> StoreResult<JsonMap> {
    let mut merged = payload.clone();
    merged.insert("record_id".into(), record_id.into());
    merged.insert("record_kind".into(), kind.into());
    merged.insert("admitted_revision".into(), revision.into());
    Ok(owned_payload(&Json::Object(merged))?)
}

/// `read_view`: every record in order and the current Campaign State they leave.
pub(crate) fn read_view(connection: &Connection, campaign_id: &str) -> StoreResult<CampaignContinuityView> {
    let (columns, rows) = named_rows(
        connection,
        "SELECT * FROM campaign_continuity_records WHERE campaign_id = ? ORDER BY sequence",
        &[&campaign_id],
    )?;
    let mut records = Vec::with_capacity(rows.len());
    let mut current_scene = None;
    let mut facts: BTreeMap<String, JsonMap> = BTreeMap::new();
    let mut entities: BTreeMap<String, JsonMap> = BTreeMap::new();
    let mut progress: BTreeMap<String, JsonMap> = BTreeMap::new();
    let mut reveals: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for values in rows {
        let row = Named { columns: &columns, values };
        let payload_raw = load_mapping(&row.text("payload_json")?);
        let basis_raw = sylvia_base::pyjson::loads(&row.text("basis_message_ids_json")?)
            .map_err(|_| schema_identity("Campaign continuity provenance is invalid"))?;
        let basis: Option<Vec<String>> = match &basis_raw {
            Json::Array(items) => items.iter().map(|item| item.as_str().map(str::to_owned)).collect(),
            _ => None,
        };
        let (Some(payload_raw), Some(basis)) = (payload_raw, basis) else {
            return Err(schema_identity("Campaign continuity record is invalid"));
        };
        let payload = owned_payload(&Json::Object(payload_raw.clone()))?;
        let kind = row.text("kind")?;
        let record_id = row.text("record_id")?;
        let status = row.text("status")?;
        let revision = row.int("admitted_revision")?;
        records.push(CampaignContinuityRecord {
            entry_id: row.text("entry_id")?,
            record_id: record_id.clone(),
            kind: kind.clone(),
            status: status.clone(),
            payload: payload.clone(),
            basis_message_ids: basis,
            admitted_revision: revision,
            operation_id: row.optional_text("operation_id")?,
        });
        let active = status == "active";
        match kind.as_str() {
            "scene" => current_scene = active.then_some(payload),
            "fact" | "entity" => {
                let target = if kind == "fact" { &mut facts } else { &mut entities };
                if active {
                    target.insert(
                        record_id.clone(),
                        with_record_fields(&payload_raw, &record_id, &kind, revision)?,
                    );
                } else {
                    target.remove(&record_id);
                }
            }
            "plan_progress" => {
                if active {
                    let mut merged = JsonMap::new();
                    merged.insert("record_id".into(), record_id.clone().into());
                    for (key, value) in &payload_raw {
                        merged.insert(key.clone(), value.clone());
                    }
                    progress.insert(record_id.clone(), owned_payload(&Json::Object(merged))?);
                } else {
                    progress.remove(&record_id);
                }
            }
            "reveal" if active => {
                if let Some(Json::Array(audience)) = payload_raw.get("audience") {
                    reveals
                        .entry(record_id.clone())
                        .or_default()
                        .extend(audience.iter().filter_map(Json::as_str).map(str::to_owned));
                } else if !payload_raw.contains_key("audience") {
                    // `payload_raw.get("audience", [])`: an empty list, the reveal is kept.
                    reveals.entry(record_id.clone()).or_default();
                }
            }
            _ => {}
        }
    }
    let mut reveal_map = JsonMap::new();
    for (key, audience) in reveals {
        reveal_map.insert(key, Json::Array(audience.into_iter().map(Json::String).collect()));
    }
    Ok(CampaignContinuityView {
        current_scene,
        facts: facts.into_values().chain(entities.into_values()).collect(),
        reveals: owned_payload(&Json::Object(reveal_map))?,
        plan_progress: progress.into_values().collect(),
        records,
    })
}

/// `verify_authority`: fail closed on malformed records or broken operation ownership.
pub(crate) fn verify_authority(connection: &Connection) -> StoreResult<()> {
    let (_, campaigns) = named_rows(
        connection,
        "SELECT DISTINCT campaign_id FROM campaign_continuity_records ORDER BY campaign_id",
        &[],
    )?;
    for row in campaigns {
        let campaign_id = py_str(&row[0]);
        read_view(connection, &campaign_id)?;
        let (_, sequences) = named_rows(
            connection,
            "SELECT sequence FROM campaign_continuity_records WHERE campaign_id = ? ORDER BY sequence",
            &[&campaign_id],
        )?;
        for (index, row) in sequences.iter().enumerate() {
            if int(&row[0])? != i64::try_from(index).unwrap_or(i64::MAX) + 1 {
                return Err(schema_identity("Campaign continuity sequence is invalid"));
            }
        }
    }
    let orphan = connection
        .query_row(
            "SELECT 1 FROM campaign_continuity_records AS record \
             LEFT JOIN campaign_operations AS operation \
             ON operation.operation_id = record.operation_id \
             WHERE record.operation_id IS NOT NULL AND (operation.operation_id IS NULL \
             OR operation.operation_kind != 'commit_campaign_continuity' \
             OR operation.campaign_id != record.campaign_id \
             OR operation.resulting_revision != record.admitted_revision) LIMIT 1",
            [],
            |_| Ok(()),
        )
        .optional()?;
    if orphan.is_some() {
        return Err(schema_identity("Campaign continuity operation authority is invalid"));
    }
    Ok(())
}

/// `_backfill_legacy_messages`: DM evidence saved before migration 9 becomes continuity.
pub(crate) fn backfill_legacy_messages(connection: &Connection) -> StoreResult<()> {
    let (_, rows) = named_rows(
        connection,
        "SELECT session.campaign_id, message.session_id, message.message_id, \
         message.content_json FROM agent_session_messages AS message \
         JOIN agent_sessions AS session ON session.session_id = message.session_id \
         WHERE session.role = 'dm' AND session.campaign_id IS NOT NULL \
         AND message.kind = 'evidence' ORDER BY message.rowid",
        &[],
    )?;
    for row in rows {
        let Some(content) = load_mapping(&py_str(&row[3])) else { continue };
        let get = |key: &str| content.get(key).cloned().unwrap_or(Json::Null);
        let text_of = |key: &str| content.get(key).and_then(Json::as_str).map(str::to_owned);
        let mut payload = JsonMap::new();
        let (kind, record_id) = match content.get("kind").and_then(Json::as_str) {
            Some("campaign_scene") if text_of("act_id").is_some() => {
                payload.insert("act_id".into(), get("act_id"));
                payload.insert("location_id".into(), get("location_id"));
                payload.insert("summary".into(), get("summary"));
                payload.insert("origin".into(), "legacy_session_evidence".into());
                ("scene", "current_scene".to_owned())
            }
            Some("campaign_memory") if text_of("fact_id").is_some() => {
                payload.insert("category".into(), get("category"));
                payload.insert("text".into(), get("text"));
                payload.insert("visibility".into(), get("visibility"));
                payload.insert(
                    "known_by".into(),
                    content.get("known_by").cloned().unwrap_or_else(|| Json::Array(Vec::new())),
                );
                payload.insert(
                    "subjects".into(),
                    content.get("subjects").cloned().unwrap_or_else(|| Json::Array(Vec::new())),
                );
                payload.insert("origin".into(), "legacy_session_evidence".into());
                ("fact", text_of("fact_id").unwrap_or_default())
            }
            Some("campaign_secret_reveal") if text_of("secret_id").is_some() => {
                let secret = text_of("secret_id").unwrap_or_default();
                payload.insert("secret_id".into(), secret.clone().into());
                payload.insert(
                    "audience".into(),
                    content.get("audience").cloned().unwrap_or_else(|| Json::Array(Vec::new())),
                );
                payload.insert("origin".into(), "legacy_session_evidence".into());
                ("reveal", secret)
            }
            _ => continue,
        };
        let campaign_id = py_str(&row[0]);
        let legacy_key = format!("{}\0{}", py_str(&row[1]), py_str(&row[2]));
        let entry_id = format!("legacy-{}", &sha256_hex(legacy_key.as_bytes())[..32]);
        let basis: Vec<String> = match content.get("basis_message_ids") {
            Some(Json::Array(items)) => items.iter().filter_map(Json::as_str).map(str::to_owned).collect(),
            _ => Vec::new(),
        };
        let revision: Value = connection.query_row(
            "SELECT revision FROM campaigns WHERE campaign_id = ?",
            [&campaign_id],
            |row| row.get(0),
        )?;
        append_record(
            connection,
            &ContinuityAppend {
                campaign_id: &campaign_id,
                entry_id: &entry_id,
                record_id: &record_id,
                kind,
                status: "active",
                payload: &payload,
                basis_message_ids: &basis,
                operation_id: None,
                admitted_revision: int(&revision)?,
            },
        )?;
    }
    Ok(())
}

fn authority(kind: &'static str, message: &str) -> StoreError {
    StoreError::Coded(crate::error::authority_coded(kind, message))
}

/// The continuity branch of `_campaign_operation_result`.
fn continuity_receipt_from(row: &Named<'_>) -> StoreResult<CampaignContinuityReceipt> {
    let result = load_payload(&row.text("result_json")?)?;
    let campaign_id = row.text("campaign_id")?;
    let resulting_revision = row.int("resulting_revision")?;
    // Python's `!=`: a stored `5.0` (or `true` for 1) equals the integer revision.
    let revision_matches = result
        .get("resulting_revision")
        .is_some_and(|value| crate::sessions::py_eq(value, &Json::from(resulting_revision)));
    if result.get("campaign_id") != Some(&Json::String(campaign_id.clone())) || !revision_matches {
        return Err(schema_identity("Campaign operation result authority is invalid"));
    }
    let keys: BTreeSet<&str> = result.keys().map(String::as_str).collect();
    let expected: BTreeSet<&str> =
        ["campaign_id", "operation_id", "record_id", "kind", "resulting_revision"].into();
    if row.text("operation_kind")? != "commit_campaign_continuity" || keys != expected {
        return Err(StoreError::NotPorted("campaign operation results other than continuity receipts"));
    }
    let text = |key: &str| crate::campaigns::json_str(&result[key]);
    Ok(CampaignContinuityReceipt {
        campaign_id,
        operation_id: text("operation_id"),
        record_id: text("record_id"),
        kind: text("kind"),
        resulting_revision,
    })
}

/// `_campaign_operation_hit` for continuity operations.
fn continuity_operation_hit(
    connection: &Connection,
    operation_id: &str,
    payload_sha256: &str,
) -> StoreResult<Option<CampaignContinuityReceipt>> {
    let (columns, mut rows) =
        named_rows(connection, "SELECT * FROM campaign_operations WHERE operation_id = ?", &[&operation_id])?;
    if rows.is_empty() {
        return Ok(None);
    }
    let row = Named { columns: &columns, values: rows.swap_remove(0) };
    if row.text("operation_kind")? != "commit_campaign_continuity"
        || row.text("semantic_payload_sha256")? != payload_sha256
    {
        return Err(authority("IdempotencyConflict", "Campaign operation identity payload does not match"));
    }
    continuity_receipt_from(&row).map(Some)
}

impl AuthorityStore {
    /// `commit_campaign_continuity`.
    pub fn commit_campaign_continuity(
        &self,
        command: &CampaignContinuityCommand,
    ) -> StoreResult<CampaignContinuityReceipt> {
        Ok(self.commit_campaign_continuity_batch(std::slice::from_ref(command))?.remove(0))
    }

    /// `commit_campaign_continuity_batch`: a complete Campaign File update in one transaction.
    pub fn commit_campaign_continuity_batch(
        &self,
        commands: &[CampaignContinuityCommand],
    ) -> StoreResult<Vec<CampaignContinuityReceipt>> {
        let Some(first) = commands.first() else {
            return Err(rejected("Campaign continuity batch is empty", "command_rejected"));
        };
        if commands.len() > 64 {
            return Err(rejected("Campaign continuity batch is too large", "command_rejected"));
        }
        let mut operation_ids = BTreeSet::new();
        let mut prepared = Vec::with_capacity(commands.len());
        for (index, command) in commands.iter().enumerate() {
            for (value, label) in [
                (&command.campaign_id, "Campaign continuity campaign"),
                (&command.operation_id, "Campaign continuity operation"),
                (&command.session_id, "Campaign continuity session"),
                (&command.turn_id, "Campaign continuity turn"),
                (&command.record_id, "Campaign continuity record"),
            ] {
                validate_authority_identity(value, label, false)?;
            }
            for message_id in &command.basis_message_ids {
                validate_authority_identity(message_id, "Campaign continuity basis message", false)?;
            }
            if !["scene", "fact", "entity", "reveal", "plan_progress"].contains(&command.kind.as_str()) {
                return Err(rejected("Campaign continuity kind is unsupported", "command_rejected"));
            }
            if !["active", "retired"].contains(&command.status.as_str()) {
                return Err(rejected("Campaign continuity status is unsupported", "command_rejected"));
            }
            if command.expected_revision < 0
                || !canonical_payload_is_safe(&Json::Object(command.payload.clone()))
            {
                return Err(rejected("Campaign continuity payload is invalid", "command_rejected"));
            }
            let offset = i64::try_from(index).unwrap_or(i64::MAX);
            if command.campaign_id != first.campaign_id
                || command.session_id != first.session_id
                || command.turn_id != first.turn_id
                || Some(command.expected_revision) != first.expected_revision.checked_add(offset)
            {
                return Err(rejected("Campaign continuity batch basis is inconsistent", "command_rejected"));
            }
            if !operation_ids.insert(command.operation_id.clone()) {
                return Err(rejected(
                    "Campaign continuity batch operation is duplicated",
                    "command_rejected",
                ));
            }
            let mut semantic = JsonMap::new();
            semantic.insert("operation_id".into(), command.operation_id.clone().into());
            semantic.insert("operation_kind".into(), "commit_campaign_continuity".into());
            semantic.insert("campaign_id".into(), command.campaign_id.clone().into());
            semantic.insert("session_id".into(), command.session_id.clone().into());
            semantic.insert("turn_id".into(), command.turn_id.clone().into());
            semantic.insert("kind".into(), command.kind.clone().into());
            semantic.insert("record_id".into(), command.record_id.clone().into());
            semantic.insert("status".into(), command.status.clone().into());
            semantic.insert("payload".into(), Json::Object(command.payload.clone()));
            semantic.insert(
                "basis_message_ids".into(),
                Json::Array(command.basis_message_ids.iter().cloned().map(Json::String).collect()),
            );
            let semantic = Json::Object(owned_payload(&Json::Object(semantic))?);
            let sha256 = sha256_hex(canonical_json_bytes(&semantic));
            prepared.push((command, semantic, sha256));
        }
        let connection = self.connect()?;
        let result = (|| -> StoreResult<Vec<CampaignContinuityReceipt>> {
            connection.execute_batch("BEGIN IMMEDIATE")?;
            self.ensure_campaign_schema(&connection)?;
            let hits = prepared
                .iter()
                .map(|(command, _, sha256)| {
                    continuity_operation_hit(&connection, &command.operation_id, sha256)
                })
                .collect::<StoreResult<Vec<_>>>()?;
            if hits.iter().any(Option::is_some) {
                if !hits.iter().all(Option::is_some) {
                    return Err(schema_identity("Campaign continuity batch replay is partial"));
                }
                connection.execute_batch("ROLLBACK")?;
                return Ok(hits.into_iter().flatten().collect());
            }
            let campaign = first_row(
                &connection,
                "SELECT lifecycle, writer, revision, current_lock_id FROM campaigns WHERE campaign_id = ?",
                &first.campaign_id,
            )?;
            let session = first_row(
                &connection,
                "SELECT campaign_id, role, status FROM agent_sessions WHERE session_id = ?",
                &first.session_id,
            )?;
            let turn = connection
                .query_row(
                    "SELECT 1 FROM turns WHERE campaign_id = ? AND turn_id = ?",
                    [&first.campaign_id, &first.turn_id],
                    |_| Ok(()),
                )
                .optional()?;
            let Some((campaign_columns, campaign_values)) = campaign else {
                return Err(rejected("Campaign continuity campaign is unavailable", "command_rejected"));
            };
            let campaign = Named { columns: &campaign_columns, values: campaign_values };
            if campaign.text("lifecycle")? != LifecycleState::Active.as_str()
                || campaign.text("writer")? != WriterRole::Dm.as_str()
            {
                return Err(authority("WrongWriter", "Campaign State can be changed only by the active DM"));
            }
            if campaign.int("revision")? != first.expected_revision {
                return Err(authority("StaleRevision", "Campaign continuity expected revision is stale"));
            }
            let session_ok = match &session {
                Some((columns, values)) => {
                    let row = Named { columns, values: values.clone() };
                    row.text("campaign_id")? == first.campaign_id
                        && row.text("role")? == WriterRole::Dm.as_str()
                        && row.text("status")? == SessionStatus::Active.as_str()
                }
                None => false,
            };
            if !session_ok || turn.is_none() {
                return Err(rejected("Campaign continuity basis is unavailable", "command_rejected"));
            }
            let Some(lock_id) = campaign.optional_text("current_lock_id")? else {
                return Err(rejected(
                    "Campaign continuity requires an admitted package lock",
                    "command_rejected",
                ));
            };
            let mut receipts = Vec::with_capacity(prepared.len());
            for (command, semantic, sha256) in &prepared {
                let resulting_revision = command
                    .expected_revision
                    .checked_add(1)
                    .ok_or_else(|| StoreError::Other("campaign revision overflow".into()))?;
                let mut result = JsonMap::new();
                result.insert("campaign_id".into(), command.campaign_id.clone().into());
                result.insert("operation_id".into(), command.operation_id.clone().into());
                result.insert("record_id".into(), command.record_id.clone().into());
                result.insert("kind".into(), command.kind.clone().into());
                result.insert("resulting_revision".into(), resulting_revision.into());
                connection.execute(
                    "INSERT INTO campaign_operations \
                     (operation_id, operation_kind, campaign_id, semantic_payload_json, \
                     semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, \
                     basis_revision, resulting_revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    params![
                        command.operation_id,
                        "commit_campaign_continuity",
                        command.campaign_id,
                        dump(semantic),
                        sha256,
                        dump(&Json::Object(owned_payload(&Json::Object(result))?)),
                        command.turn_id,
                        lock_id,
                        command.expected_revision,
                        resulting_revision,
                    ],
                )?;
                append_record(
                    &connection,
                    &ContinuityAppend {
                        campaign_id: &command.campaign_id,
                        entry_id: &command.operation_id,
                        record_id: &command.record_id,
                        kind: &command.kind,
                        status: &command.status,
                        payload: &command.payload,
                        basis_message_ids: &command.basis_message_ids,
                        operation_id: Some(&command.operation_id),
                        admitted_revision: resulting_revision,
                    },
                )?;
                connection.execute(
                    "INSERT INTO campaign_revisions (campaign_id, revision, continuity_operation_id) VALUES (?, ?, ?)",
                    params![command.campaign_id, resulting_revision, command.operation_id],
                )?;
                receipts.push(CampaignContinuityReceipt {
                    campaign_id: command.campaign_id.clone(),
                    operation_id: command.operation_id.clone(),
                    record_id: command.record_id.clone(),
                    kind: command.kind.clone(),
                    resulting_revision,
                });
            }
            let count = i64::try_from(commands.len()).unwrap_or(i64::MAX);
            let final_revision = first
                .expected_revision
                .checked_add(count)
                .ok_or_else(|| StoreError::Other("campaign revision overflow".into()))?;
            let updated = connection.execute(
                "UPDATE campaigns SET revision = ? WHERE campaign_id = ? AND revision = ?",
                params![final_revision, first.campaign_id, first.expected_revision],
            )?;
            if updated != 1 {
                return Err(authority(
                    "StaleRevision",
                    "Campaign continuity revision fence rejected admission",
                ));
            }
            if let Some(fault) = &self.faults.before_commit {
                fault()?;
            }
            connection.execute_batch("COMMIT")?;
            Ok(receipts)
        })();
        if !connection.is_autocommit() {
            let _ = connection.execute_batch("ROLLBACK");
        }
        result
    }

    /// The current continuity view of one campaign (`sqlite_continuity.read_view`, read alone).
    pub fn campaign_continuity_view(&self, campaign_id: &str) -> StoreResult<CampaignContinuityView> {
        let connection = self.connect()?;
        read_view(&connection, campaign_id)
    }
}

fn first_row(
    connection: &Connection,
    query: &str,
    key: &str,
) -> StoreResult<Option<(Vec<String>, Vec<Value>)>> {
    let (columns, mut rows) = named_rows(connection, query, &[&key])?;
    Ok(if rows.is_empty() { None } else { Some((columns, rows.swap_remove(0))) })
}

// ---- Plan-derived continuity: chapter markers, endings and the accepted initial state ----

/// `len(value)`, with Python's `TypeError` for a value that has no length.
fn py_len(value: &Json) -> StoreResult<i64> {
    let length = match value {
        Json::Array(items) => items.len(),
        Json::String(text) => sylvia_base::pystr::len(text),
        Json::Object(map) => map.len(),
        other => {
            return Err(StoreError::Other(format!("object of type '{}' has no len()", py_type_name(other))))
        }
    };
    i64::try_from(length)
        .map_err(|_| StoreError::Other("cannot fit 'int' into an index-sized integer".into()))
}

/// `json.loads(row['content_json']).get('campaign_plan', {}).get('structure', ())`.
fn plan_structure(row: &PlanRow) -> StoreResult<Json> {
    let content = row.content()?;
    let empty = Json::Object(JsonMap::new());
    let plan = py_get_attr(&content, "campaign_plan")?.unwrap_or(&empty);
    Ok(py_get_attr(plan, "structure")?.cloned().unwrap_or_else(|| Json::Array(Vec::new())))
}

/// The text of a SQLite value, the only kind that can equal a `str` key in Python.
fn text_of(value: &Value) -> Option<&str> {
    match value {
        Value::Text(text) => Some(text),
        _ => None,
    }
}

/// A SQLite result value as the JSON value Python's `sqlite3` gives it.
fn sql_json(value: &Value) -> StoreResult<Json> {
    Ok(match value {
        Value::Null => Json::Null,
        Value::Integer(number) => Json::from(*number),
        Value::Real(number) => Json::from(*number),
        Value::Text(text) => Json::String(text.clone()),
        Value::Blob(_) => {
            return Err(StoreError::Other("Safe payload values must be JSON compatible".into()))
        }
    })
}

/// `_act_marker_anchor`: the saved narration after a committed response basis, if any.
fn act_marker_anchor(
    connection: &Connection,
    session_id: &str,
    basis_json: &str,
) -> StoreResult<Option<String>> {
    let basis: Value = connection.query_row(
        "SELECT MAX(sequence) FROM agent_session_messages WHERE session_id = ? \
         AND message_id IN (SELECT value FROM json_each(?))",
        params![session_id, basis_json],
        |row| row.get(0),
    )?;
    if basis == Value::Null {
        return Ok(None);
    }
    let next_input: Value = connection.query_row(
        "SELECT MIN(sequence) FROM agent_session_messages WHERE session_id = ? \
         AND sequence > ? AND (kind = 'user' OR json_extract(content_json, '$.kind') \
         IN ('campaign_start', 'player_action_response'))",
        params![session_id, &basis],
        |row| row.get(0),
    )?;
    let anchor: Option<Value> = connection
        .query_row(
            "SELECT message_id FROM agent_session_messages WHERE session_id = ? \
             AND sequence >= ? AND (? IS NULL OR sequence < ?) \
             AND kind = 'canonical_reference' \
             AND json_extract(content_json, '$.event_type') = 'talk' \
             ORDER BY sequence DESC LIMIT 1",
            params![session_id, &basis, &next_input, &next_input],
            |row| row.get(0),
        )
        .optional()?;
    Ok(anchor.map(|value| py_str(&value)))
}

/// `read_act_markers`: saved chapter boundaries as ordinals, never private Plan titles or
/// summaries. Completion needs an explicit resolved act; beginning needs a saved scene in it.
pub fn read_act_markers(
    connection: &Connection,
    session_id: &str,
    campaign_id: &str,
) -> StoreResult<Vec<JsonMap>> {
    let mut structure = Vec::new();
    for row in adventure_rows(connection, campaign_id)? {
        structure.extend(py_iter(&plan_structure(&row)?)?);
    }
    // A dict comprehension: a repeated act id keeps its last ordinal.
    let mut ordinals: HashMap<String, i64> = HashMap::new();
    for (index, act) in structure.iter().enumerate() {
        if let Json::Object(map) = act {
            if let Some(Json::String(id)) = map.get("id") {
                let ordinal = i64::try_from(index).ok().and_then(|index| index.checked_add(1));
                ordinals.insert(
                    id.clone(),
                    ordinal.ok_or_else(|| StoreError::Other("act ordinal overflow".into()))?,
                );
            }
        }
    }
    if ordinals.is_empty() {
        return Ok(Vec::new());
    }
    let (columns, rows) = named_rows(
        connection,
        "SELECT kind, record_id, operation_id, basis_message_ids_json, \
         json_extract(payload_json, '$.act_id') AS act_id \
         FROM campaign_continuity_records WHERE campaign_id = ? AND status = 'active' \
         AND (kind = 'scene' OR (kind = 'plan_progress' \
         AND json_extract(payload_json, '$.status') = 'resolved')) ORDER BY sequence",
        &[&campaign_id],
    )?;
    let mut begun: HashSet<String> = HashSet::new();
    let mut completed: HashSet<String> = HashSet::new();
    let mut anchors: HashMap<String, Option<String>> = HashMap::new();
    let mut markers = Vec::new();
    for values in rows {
        let row = Named { columns: &columns, values };
        let is_scene = text_of(row.get("kind")?) == Some("scene");
        let act_id = row.get(if is_scene { "act_id" } else { "record_id" })?;
        let Some(act_id) = text_of(act_id).map(str::to_owned) else { continue };
        let Some(&ordinal) = ordinals.get(&act_id) else { continue };
        let seen = if is_scene { &mut begun } else { &mut completed };
        if seen.contains(&act_id) {
            continue;
        }
        // The initial seeded scene establishes the first act without an announcement.
        if row.get("operation_id")? == &Value::Null {
            if is_scene {
                seen.insert(act_id);
            }
            continue;
        }
        let basis_json = py_str(row.get("basis_message_ids_json")?);
        if !anchors.contains_key(&basis_json) {
            let anchor = act_marker_anchor(connection, session_id, &basis_json)?;
            anchors.insert(basis_json.clone(), anchor);
        }
        let Some(anchor) = anchors[&basis_json].clone() else { continue };
        seen.insert(act_id);
        let status = if is_scene { "begins" } else { "complete" };
        let mut marker = JsonMap::new();
        marker.insert("marker_id".into(), format!("act-{ordinal}-{status}").into());
        marker.insert("after_message_id".into(), anchor.into());
        marker.insert("act_number".into(), ordinal.into());
        marker.insert("status".into(), status.into());
        markers.push(marker);
    }
    Ok(markers)
}

/// `read_campaign_ending` for one adventure (`plan_row`, `act_offset`): the earned ending and
/// the player's choice, without private ending prose.
fn campaign_ending(
    connection: &Connection,
    session_id: &str,
    campaign_id: &str,
    plan_row: &PlanRow,
    act_offset: i64,
) -> StoreResult<Option<JsonMap>> {
    let content = plan_row.content()?;
    let Some(Json::Object(plan)) = py_get_attr(&content, "campaign_plan")? else { return Ok(None) };
    let plan_json = Json::Object(plan.clone());
    // A dict comprehension: a repeated progress id keeps its first position and last ending.
    let mut endings: Vec<(String, JsonMap)> = Vec::new();
    for item in intended_endings(plan) {
        let key = ending_progress_id(item)?;
        match endings.iter_mut().find(|(existing, _)| *existing == key) {
            Some(entry) => entry.1 = item.clone(),
            None => endings.push((key, item.clone())),
        }
    }
    if endings.is_empty() {
        return Ok(None);
    }
    let (columns, rows) = named_rows(
        connection,
        "SELECT record_id, payload_json, basis_message_ids_json FROM campaign_continuity_records \
         WHERE campaign_id = ? AND kind = 'plan_progress' AND status = 'active' ORDER BY sequence",
        &[&campaign_id],
    )?;
    let rows: Vec<Named<'_>> = rows.into_iter().map(|values| Named { columns: &columns, values }).collect();
    let progress = rows
        .iter()
        .map(|row| json_loads(&py_str(row.get("payload_json")?)))
        .collect::<StoreResult<Vec<_>>>()?;
    let resolved = resolved_progress(progress.iter())?;
    let ending_of = |id: &str| endings.iter().find(|(key, _)| key == id).map(|(_, ending)| ending);
    let mut reached = None;
    for row in rows.iter().rev() {
        if let Some(id) = text_of(row.get("record_id")?) {
            if ending_of(id).is_some() && resolved.contains(id) {
                reached = Some(id.to_owned());
                break;
            }
        }
    }
    let Some(reached) = reached else { return Ok(None) };
    let ending = ending_of(&reached).cloned().expect("the reached ending is declared");
    if !required_ending_items(plan, &ending)?.is_subset(&resolved) {
        return Ok(None);
    }
    let mut first = None;
    for row in &rows {
        if text_of(row.get("record_id")?) == Some(reached.as_str()) {
            let payload = json_loads(&py_str(row.get("payload_json")?))?;
            if py_get_attr(&payload, "status")?.is_some_and(|status| py_eq(status, &Json::from("resolved"))) {
                first = Some(row);
                break;
            }
        }
    }
    // `next()` over an exhausted generator raises a bare `StopIteration`.
    let Some(first) = first else { return Err(StoreError::Other(String::new())) };
    let Some(anchor) =
        act_marker_anchor(connection, session_id, &py_str(first.get("basis_message_ids_json")?))?
    else {
        return Ok(None);
    };
    let ending_json = Json::Object(ending);
    let ending_id = py_item(&ending_json, "id")?.clone();
    let choice: Option<Value> = connection
        .query_row(
            "SELECT json_extract(content_json, '$.campaign_ending_choice.choice') \
             FROM agent_session_messages WHERE session_id = ? AND kind = 'user' \
             AND json_extract(content_json, '$.campaign_ending_choice.ending_id') = ? \
             ORDER BY sequence DESC LIMIT 1",
            params![session_id, bind_json(&ending_id)?],
            |row| row.get(0),
        )
        .optional()?;
    let act_number = if required_story_items(plan)?.is_subset(&resolved) {
        let length = py_len(py_item(&plan_json, "structure")?)?;
        Json::from(
            act_offset.checked_add(length).ok_or_else(|| StoreError::Other("act number overflow".into()))?,
        )
    } else {
        Json::Null
    };
    let mut result = JsonMap::new();
    result.insert("ending_id".into(), ending_id);
    result.insert("after_message_id".into(), anchor.into());
    result.insert("act_number".into(), act_number);
    result.insert("plan_title".into(), py_item(&plan_json, "title")?.clone());
    result.insert("open_world".into(), py_item(&ending_json, "open_world")?.clone());
    result.insert("has_after_story".into(), Json::Bool(!py_item(&ending_json, "after_story")?.is_null()));
    result.insert(
        "choice".into(),
        match choice {
            Some(value) => sql_json(&value)?,
            None => Json::Null,
        },
    );
    Ok(Some(owned_payload(&Json::Object(result))?))
}

/// `read_campaign_ending`: the current adventure's ending, its acts numbered after the earlier
/// adventures'.
pub fn read_campaign_ending(
    connection: &Connection,
    session_id: &str,
    campaign_id: &str,
) -> StoreResult<Option<JsonMap>> {
    let adventures = adventure_rows(connection, campaign_id)?;
    let Some((current, earlier)) = adventures.split_last() else { return Ok(None) };
    let mut offset: i64 = 0;
    for row in earlier {
        offset = offset
            .checked_add(py_len(&plan_structure(row)?)?)
            .ok_or_else(|| StoreError::Other("act offset overflow".into()))?;
    }
    campaign_ending(connection, session_id, campaign_id, current, offset)
}

/// `read_campaign_endings`: earlier adventures' completion cards (marked `historical`) and the
/// current one's.
pub fn read_campaign_endings(
    connection: &Connection,
    session_id: &str,
    campaign_id: &str,
) -> StoreResult<Vec<JsonMap>> {
    let adventures = adventure_rows(connection, campaign_id)?;
    let mut result = Vec::new();
    let mut offset: i64 = 0;
    for (index, row) in adventures.iter().enumerate() {
        if let Some(mut ending) = campaign_ending(connection, session_id, campaign_id, row, offset)? {
            if index + 1 < adventures.len() {
                ending.insert("historical".into(), Json::Bool(true));
            }
            result.push(owned_payload(&Json::Object(ending))?);
        }
        offset = offset
            .checked_add(py_len(&plan_structure(row)?)?)
            .ok_or_else(|| StoreError::Other("act offset overflow".into()))?;
    }
    Ok(result)
}

/// `_initial_entry_id`.
fn initial_entry_id(campaign_id: &str, record_id: &str, kind: &str) -> String {
    let digest = sha256_hex(format!("{campaign_id}\0{kind}\0{record_id}").as_bytes());
    format!("initial-{}", &digest[..32])
}

fn append_initial(
    connection: &Connection,
    campaign_id: &str,
    record_id: &str,
    kind: &str,
    payload: &JsonMap,
    admitted_revision: i64,
) -> StoreResult<()> {
    append_record(
        connection,
        &ContinuityAppend {
            campaign_id,
            entry_id: &initial_entry_id(campaign_id, record_id, kind),
            record_id,
            kind,
            status: "active",
            payload,
            basis_message_ids: &[],
            operation_id: None,
            admitted_revision,
        },
    )
}

/// `seed_initial_state`: materialise the accepted Plan's initial facts without promoting future
/// preparation. A campaign that already has continuity is left as it is.
pub fn seed_initial_state(
    connection: &Connection,
    campaign_id: &str,
    plan: &JsonMap,
    admitted_revision: i64,
) -> StoreResult<()> {
    if connection
        .query_row(
            "SELECT 1 FROM campaign_continuity_records WHERE campaign_id = ? LIMIT 1",
            [campaign_id],
            |_| Ok(()),
        )
        .optional()?
        .is_some()
    {
        return Ok(());
    }
    let mut first_act_id = Json::Null;
    if let Some(Json::Array(items)) = plan.get("structure") {
        if let Some(Json::Object(first)) = items.iter().find(|item| item.is_object()) {
            if let Some(Json::String(id)) = first.get("id") {
                first_act_id = Json::String(id.clone());
            }
        }
    }
    if let Some(Json::Object(start)) = plan.get("starting_location") {
        let mut payload = JsonMap::new();
        payload.insert("act_id".into(), first_act_id);
        payload.insert("location_id".into(), start.get("id").cloned().unwrap_or(Json::Null));
        payload.insert("starting_location".into(), Json::Object(start.clone()));
        payload.insert("summary".into(), Json::Null);
        payload.insert("origin".into(), "accepted_initial_state".into());
        append_initial(connection, campaign_id, "current_scene", "scene", &payload, admitted_revision)?;
    }
    if is_no_ruleset_plan(plan) {
        for (record_id, payload) in no_ruleset_world_records(plan)? {
            append_initial(connection, campaign_id, &record_id, "entity", &payload, admitted_revision)?;
        }
    }
    let facts = match plan.get("initial_state") {
        Some(Json::Object(state)) => state.get("facts"),
        _ => None,
    };
    let Some(Json::Array(facts)) = facts else { return Ok(()) };
    for (index, raw) in facts.iter().enumerate() {
        let fallback = || format!("initial-fact-{index}");
        let (text, known_by, record_id) = match raw {
            Json::String(text) => (text.clone(), vec!["player".to_owned()], fallback()),
            Json::Object(fact) => match fact.get("fact") {
                Some(Json::String(text)) => {
                    let known_by = match fact.get("known_by") {
                        Some(Json::Array(items)) => {
                            items.iter().filter_map(|item| item.as_str().map(str::to_owned)).collect()
                        }
                        _ => Vec::new(),
                    };
                    let record_id = match fact.get("id") {
                        Some(Json::String(id)) => id.clone(),
                        _ => fallback(),
                    };
                    (text.clone(), known_by, record_id)
                }
                _ => continue,
            },
            _ => continue,
        };
        let visibility =
            if known_by.iter().any(|item| item == "player") { "player_known" } else { "gm_private" };
        let mut payload = JsonMap::new();
        payload.insert("category".into(), "continuity".into());
        payload.insert("text".into(), text.into());
        payload.insert("visibility".into(), visibility.into());
        payload.insert("known_by".into(), Json::Array(known_by.into_iter().map(Json::String).collect()));
        payload.insert("subjects".into(), Json::Array(Vec::new()));
        payload.insert("origin".into(), "accepted_initial_state".into());
        append_initial(connection, campaign_id, &record_id, "fact", &payload, admitted_revision)?;
    }
    Ok(())
}

/// The seeding step of `apply_migration`: each campaign's latest accepted Plan, if its content
/// holds a structured `campaign_plan`.
pub(crate) fn seed_accepted_plans(connection: &Connection) -> StoreResult<()> {
    let (columns, rows) = named_rows(
        connection,
        "SELECT plan.campaign_id, plan.content_json, plan.resulting_campaign_revision \
         FROM campaign_plans AS plan JOIN (\
         SELECT campaign_id, MAX(plan_revision) AS plan_revision FROM campaign_plans \
         WHERE acceptance_status = 'accepted' GROUP BY campaign_id\
         ) AS accepted ON accepted.campaign_id = plan.campaign_id \
         AND accepted.plan_revision = plan.plan_revision ORDER BY plan.campaign_id",
        &[],
    )?;
    for values in rows {
        let row = Named { columns: &columns, values };
        let Some(content) = load_mapping(&py_str(row.get("content_json")?)) else { continue };
        if let Some(Json::Object(plan)) = content.get("campaign_plan") {
            seed_initial_state(
                connection,
                &row.text("campaign_id")?,
                plan,
                row.int("resulting_campaign_revision")?,
            )?;
        }
    }
    Ok(())
}
