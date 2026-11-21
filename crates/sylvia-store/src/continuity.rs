//! Campaign continuity, part 1: the Campaign State records, their view, their verifier, the
//! legacy-evidence backfill of migration 9 and the revision-fenced commit batch.
//!
//! Python: `sqlite_continuity.py` (`append_record`, `read_view`, `verify_authority`,
//! `_backfill_legacy_messages`, `_load_mapping`, `_next_sequence`) and
//! `SQLiteAuthorityStore.commit_campaign_continuity(_batch)` with the campaign-operation
//! receipt helpers it uses (`_campaign_operation_hit`, `_insert_campaign_operation`, the
//! continuity branch of `_campaign_operation_result`).
//!
//! Still to come, with sessions part 2: `read_act_markers`, `read_campaign_ending(s)` and
//! `seed_initial_state`. They read accepted Campaign Plans, and the last two use lane 1's pending
//! `domain/campaign_ending.py` and `domain/no_ruleset_plan.py`.

use crate::actions::{dump, load_payload, rejected};
use crate::campaigns::{named_rows, Named};
use crate::error::{schema_identity, value_error, StoreError, StoreResult};
use crate::pytext::{py_int, py_str};
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use rusqlite::{params, Connection, OptionalExtension};
use std::collections::{BTreeMap, BTreeSet};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    canonical_payload_is_safe, owned_payload, validate_authority_identity, CampaignContinuityCommand,
    CampaignContinuityReceipt, CampaignContinuityRecord, CampaignContinuityView, LifecycleState, WriterRole,
};
use sylvia_domain::package_library::canonical_json_bytes;
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
