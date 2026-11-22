//! `_verify_construction_authority`: campaign operation receipts, saved construction inputs and
//! lifecycle receipts, reconciled at startup and on backups when the profile has
//! `campaign_operations` (part 4 of `_verify_stored_authority`).
//!
//! Python: `SQLiteAuthorityStore._verify_construction_authority` with `_campaign_operation_result`
//! and `_operation_lock_payload`.
//!
//! Python wraps the whole check in `except (KeyError, TypeError, ValueError, sqlite3.Error)` and
//! raises its `SchemaIdentityError`. Anything else escapes as it does there: the
//! `SchemaIdentityError`s `_campaign_operation_result` raises with their own messages, and a
//! `CommandRejected` from a lock or candidate read.

use crate::actions::{load_json, load_payload};
use crate::campaigns::{json_str, named_rows, read_lock_record, Named};
use crate::error::{schema_identity, value_error, StoreError, StoreResult};
use crate::pytext::{py_int, py_str};
use rusqlite::types::Value;
use rusqlite::Connection;
use std::collections::{BTreeMap, BTreeSet, HashSet};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    canonical_payload_is_safe, validate_authority_identity, validate_authority_identity_value,
    CampaignConfirmation, CampaignConstructionBegun, CampaignConstructionCompleted,
    CampaignContinuityReceipt, CampaignLifecycleResult, CampaignPurgeResult, LifecycleState, PackageLock,
    WriterRole,
};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::py_eq;
use sylvia_domain::session::BUILDER_SKILLS;

const INVALID: &str = "Saved campaign construction records failed an integrity check";

const OPERATION_KINDS: [&str; 9] = [
    "confirm_new_campaign",
    "begin_campaign_construction",
    "complete_campaign_construction",
    "rename_campaign",
    "archive_campaign",
    "restore_campaign",
    "trash_campaign",
    "purge_campaign",
    "commit_campaign_continuity",
];

/// What `_campaign_operation_result` returns for one `campaign_operations` row.
#[derive(Debug, Clone, PartialEq)]
pub enum OperationResult {
    Confirmation(CampaignConfirmation),
    Begun(CampaignConstructionBegun),
    Completed(CampaignConstructionCompleted),
    Lifecycle(CampaignLifecycleResult),
    Purge(CampaignPurgeResult),
    Continuity(CampaignContinuityReceipt),
}

fn keys(map: &JsonMap) -> BTreeSet<&str> {
    map.keys().map(String::as_str).collect()
}

fn is_keys(map: &JsonMap, expected: &[&str]) -> bool {
    keys(map) == expected.iter().copied().collect()
}

/// `_campaign_operation_result(row)`.
pub(crate) fn operation_result(row: &Named<'_>) -> StoreResult<OperationResult> {
    let result = load_payload(&row.text("result_json")?)?;
    let kind = row.text("operation_kind")?;
    let campaign_id = row.text("campaign_id")?;
    let resulting_revision = row.int("resulting_revision")?;
    if result.get("campaign_id") != Some(&Json::String(campaign_id.clone()))
        || !result
            .get("resulting_revision")
            .is_some_and(|value| py_eq(value, &Json::from(resulting_revision)))
    {
        return Err(schema_identity("Campaign operation result authority is invalid"));
    }
    let short = ["campaign_id", "resulting_revision"];
    if kind == "confirm_new_campaign" && is_keys(&result, &short) {
        return Ok(OperationResult::Confirmation(CampaignConfirmation { campaign_id, resulting_revision }));
    }
    if kind == "purge_campaign" && is_keys(&result, &short) {
        return Ok(OperationResult::Purge(CampaignPurgeResult { campaign_id, resulting_revision }));
    }
    if kind == "commit_campaign_continuity"
        && is_keys(&result, &["campaign_id", "operation_id", "record_id", "kind", "resulting_revision"])
    {
        return Ok(OperationResult::Continuity(CampaignContinuityReceipt {
            campaign_id,
            operation_id: json_str(&result["operation_id"]),
            record_id: json_str(&result["record_id"]),
            kind: json_str(&result["kind"]),
            resulting_revision,
        }));
    }
    if matches!(kind.as_str(), "rename_campaign" | "archive_campaign" | "restore_campaign" | "trash_campaign")
    {
        let invalid = || schema_identity("Campaign lifecycle result authority is invalid");
        if !is_keys(&result, &["campaign_id", "lifecycle", "writer", "resulting_revision", "session_id"]) {
            return Err(invalid());
        }
        let lifecycle = LifecycleState::parse(&json_str(&result["lifecycle"])).ok_or_else(invalid)?;
        let writer = WriterRole::parse(&json_str(&result["writer"])).ok_or_else(invalid)?;
        let session_id = match &result["session_id"] {
            Json::Null => None,
            Json::String(text) => Some(text.clone()),
            _ => return Err(schema_identity("Campaign lifecycle session authority is invalid")),
        };
        return Ok(OperationResult::Lifecycle(CampaignLifecycleResult {
            campaign_id,
            lifecycle,
            writer,
            resulting_revision,
            session_id,
        }));
    }
    let lane = match result.get("lane") {
        Some(Json::String(lane)) if lane == "quick_start" || lane == "customize" => lane.clone(),
        _ => return Err(schema_identity("Campaign operation lane authority is invalid")),
    };
    if kind == "begin_campaign_construction"
        && is_keys(&result, &["campaign_id", "lane", "resulting_revision"])
    {
        return Ok(OperationResult::Begun(CampaignConstructionBegun {
            campaign_id,
            lane,
            resulting_revision,
        }));
    }
    if kind == "complete_campaign_construction"
        && is_keys(&result, &["campaign_id", "lane", "canonical_representation_sha256", "resulting_revision"])
    {
        if let Some(Json::String(representation)) = result.get("canonical_representation_sha256") {
            validate_authority_identity(representation, "Canonical construction SHA-256", true)?;
            return Ok(OperationResult::Completed(CampaignConstructionCompleted {
                campaign_id,
                lane,
                canonical_representation_sha256: representation.clone(),
                resulting_revision,
            }));
        }
    }
    Err(schema_identity("Campaign operation result kind is invalid"))
}

/// `_operation_lock_payload`.
pub(crate) fn operation_lock_payload(package_lock: &PackageLock) -> Json {
    Json::Array(
        package_lock
            .entries()
            .iter()
            .map(|entry| {
                let dependencies = entry
                    .dependencies()
                    .iter()
                    .map(|dependency| {
                        object([
                            ("package_id", dependency.package_id().into()),
                            ("version", dependency.version().into()),
                            ("sha256", dependency.sha256().into()),
                            ("kind", dependency.kind().into()),
                            ("namespace", dependency.namespace().into()),
                        ])
                    })
                    .collect();
                object([
                    ("package_id", entry.package_id().into()),
                    ("version", entry.version().into()),
                    ("sha256", entry.sha256().into()),
                    ("role", entry.role().as_str().into()),
                    ("load_order", entry.load_order().into()),
                    ("dependencies", Json::Array(dependencies)),
                    (
                        "capabilities",
                        Json::Array(entry.capabilities().iter().map(|c| c.as_str().into()).collect()),
                    ),
                ])
            })
            .collect(),
    )
}

fn object<const N: usize>(pairs: [(&str, Json); N]) -> Json {
    Json::Object(pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect())
}

fn int(value: &Value) -> StoreResult<i64> {
    py_int(value).ok_or_else(|| value_error("invalid literal for int() with base 10"))
}

/// `value != text` for a stored SQLite value and a Python `str`.
fn differs(value: &Value, text: &str) -> bool {
    !matches!(value, Value::Text(stored) if stored == text)
}

fn optional_text(value: &Value) -> Json {
    match value {
        Value::Null => Json::Null,
        value => Json::String(py_str(value)),
    }
}

fn first_row(
    connection: &Connection,
    query: &str,
    values: &[&dyn rusqlite::ToSql],
) -> StoreResult<Option<Vec<Value>>> {
    Ok(named_rows(connection, query, values)?.1.into_iter().next())
}

/// Whether Python's `except (KeyError, TypeError, ValueError, sqlite3.Error)` catches it.
fn caught(error: &StoreError) -> bool {
    match error {
        StoreError::Sqlite(_) | StoreError::Other(_) => true,
        StoreError::Coded(coded) => {
            matches!(coded.error_type, Some("ValueError" | "TypeError" | "KeyError" | "JSONDecodeError"))
        }
        StoreError::Backup(_) | StoreError::NotPorted(_) => false,
    }
}

/// `SQLiteAuthorityStore._verify_construction_authority`.
pub(crate) fn verify_construction_authority(connection: &Connection) -> StoreResult<()> {
    match check(connection) {
        Err(error) if caught(&error) => Err(schema_identity(INVALID)),
        other => other,
    }
}

fn check(connection: &Connection) -> StoreResult<()> {
    let (columns, rows) =
        named_rows(connection, "SELECT * FROM campaign_operations ORDER BY operation_id", &[])?;
    let mut operations: BTreeMap<String, Named<'_>> = BTreeMap::new();
    let mut order: Vec<String> = Vec::new();
    for values in rows {
        let row = Named { columns: &columns, values };
        let id = row.text("operation_id")?;
        if operations.insert(id.clone(), row).is_none() {
            order.push(id);
        }
    }
    let count: i64 =
        connection.query_row("SELECT COUNT(*) FROM campaign_operations", [], |row| row.get(0))?;
    if operations.len() as i64 != count {
        return Err(value_error("Campaign operation identities are duplicated"));
    }
    let mut seen: HashSet<String> = HashSet::new();
    for operation_id in &order {
        let operation = &operations[operation_id];
        let payload = load_payload(&operation.text("semantic_payload_json")?)?;
        let operation_kind = operation.text("operation_kind")?;
        if payload.get("operation_id") != Some(&Json::String(operation_id.clone()))
            || payload.get("operation_kind") != Some(&Json::String(operation_kind.clone()))
            || !OPERATION_KINDS.contains(&operation_kind.as_str())
            || sha256_hex(canonical_json_bytes(&Json::Object(payload.clone())))
                != operation.text("semantic_payload_sha256")?
            || !canonical_payload_is_safe(&Json::Object(payload.clone()))
        {
            return Err(value_error("Campaign operation semantic identity is invalid"));
        }
        let result = operation_result(operation)?;
        let campaign_id = operation.text("campaign_id")?;
        if operation_kind == "purge_campaign" {
            let refused = !matches!(result, OperationResult::Purge(_))
                || !is_keys(&payload, &["operation_id", "operation_kind", "campaign_id", "confirmation"])
                || payload.get("campaign_id") != Some(&Json::String(campaign_id.clone()))
                || payload.get("confirmation")
                    != Some(&Json::String(format!("permanently purge {campaign_id}")))
                || operation.get("basis_turn_id")? != &Value::Null
                || operation.get("basis_lock_id")? != &Value::Null
                || int(operation.get("resulting_revision")?)?
                    != int(operation.get("basis_revision")?)?
                        .checked_add(1)
                        .ok_or_else(|| value_error("overflow"))?;
            if refused {
                return Err(value_error("Campaign purge receipt authority is invalid"));
            }
            seen.insert(operation_id.clone());
            continue;
        }
        let turn = first_row(
            connection,
            "SELECT created_revision FROM turns WHERE campaign_id = ? AND turn_id = ?",
            &[&campaign_id, &operation.text("basis_turn_id")?],
        )?;
        let lock = first_row(
            connection,
            "SELECT campaign_id, created_revision FROM campaign_locks WHERE lock_id = ?",
            &[&operation.text("basis_lock_id")?],
        )?;
        let draft_operation = crate::packages::DRAFT_LIFECYCLE_KINDS.contains(&operation_kind.as_str())
            && operation.get("basis_turn_id")? == &Value::Null
            && operation.get("basis_lock_id")? == &Value::Null
            && crate::packages::has_only_draft_lifecycle_history(
                connection,
                &campaign_id,
                int(operation.get("resulting_revision")?)?,
            )?;
        if !draft_operation {
            let basis = || int(operation.get("basis_revision")?);
            let refused = match (&turn, &lock) {
                (Some(turn), Some(lock)) => {
                    py_str(&lock[0]) != campaign_id
                        || int(&turn[0])? > basis()?
                        || int(&lock[1])? > basis()?
                        || int(operation.get("resulting_revision")?)? < basis()?
                }
                _ => true,
            };
            if refused {
                return Err(value_error("Campaign operation authority basis is invalid"));
            }
        }
        if operation_kind == "commit_campaign_continuity" {
            let (record_columns, record_rows) = named_rows(
                connection,
                "SELECT record_id, kind, status, payload_json, basis_message_ids_json, \
                 admitted_revision FROM campaign_continuity_records WHERE operation_id = ?",
                &[operation_id],
            )?;
            let record =
                record_rows.into_iter().next().map(|values| Named { columns: &record_columns, values });
            let revision = first_row(
                connection,
                "SELECT continuity_operation_id FROM campaign_revisions WHERE campaign_id = ? AND revision = ?",
                &[operation.get("campaign_id")?, operation.get("resulting_revision")?],
            )?;
            let refused = match (&result, &record, &revision) {
                (OperationResult::Continuity(receipt), Some(record), Some(revision)) => {
                    py_str(&revision[0]) != *operation_id
                        || int(operation.get("resulting_revision")?)?
                            != int(operation.get("basis_revision")?)?
                                .checked_add(1)
                                .ok_or_else(|| value_error("overflow"))?
                        || receipt.operation_id != *operation_id
                        || receipt.record_id != record.text("record_id")?
                        || receipt.kind != record.text("kind")?
                        || !py_eq(
                            &Json::Object(payload.clone()),
                            &object([
                                ("operation_id", operation_id.as_str().into()),
                                ("operation_kind", "commit_campaign_continuity".into()),
                                ("campaign_id", campaign_id.as_str().into()),
                                ("session_id", payload.get("session_id").cloned().unwrap_or(Json::Null)),
                                ("turn_id", operation.text("basis_turn_id")?.into()),
                                ("kind", record.text("kind")?.into()),
                                ("record_id", record.text("record_id")?.into()),
                                ("status", record.text("status")?.into()),
                                ("payload", Json::Object(load_payload(&record.text("payload_json")?)?)),
                                ("basis_message_ids", load_json(&record.text("basis_message_ids_json")?)?),
                            ]),
                        )
                }
                _ => true,
            };
            if refused {
                return Err(value_error("Campaign continuity receipt authority is invalid"));
            }
            seen.insert(operation_id.clone());
            continue;
        }
    }

    let (construction_columns, construction_rows) =
        named_rows(connection, "SELECT * FROM campaign_constructions ORDER BY campaign_id", &[])?;
    for values in construction_rows {
        let construction = Named { columns: &construction_columns, values };
        let campaign_id = construction.text("campaign_id")?;
        if first_row(connection, "SELECT display_name FROM campaigns WHERE campaign_id = ?", &[&campaign_id])?
            .is_none()
        {
            return Err(value_error("Campaign construction is orphaned"));
        }
        let confirmation_id = construction.text("confirmation_operation_id")?;
        let Some(confirmation) = operations.get(&confirmation_id) else {
            return Err(value_error("Campaign confirmation receipt is unavailable"));
        };
        let authority_turn_id = construction.text("authority_turn_id")?;
        let authority_lock_id = construction.text("authority_lock_id")?;
        let confirmation_revision = construction.int("confirmation_revision")?;
        let lock_record = read_lock_record(connection, &authority_lock_id)?;
        let confirmation_payload = load_payload(&confirmation.text("semantic_payload_json")?)?;
        let confirmed_display_name = confirmation_payload.get("display_name").cloned().unwrap_or(Json::Null);
        validate_authority_identity_value(
            &confirmed_display_name,
            "Campaign confirmation display name",
            false,
        )?;
        let mut expected = JsonMap::new();
        expected.insert("operation_id".into(), confirmation_id.as_str().into());
        expected.insert("operation_kind".into(), "confirm_new_campaign".into());
        expected.insert("display_name".into(), confirmed_display_name);
        expected.insert("package_lock".into(), operation_lock_payload(&lock_record.package_lock));
        expected.insert("turn_id".into(), authority_turn_id.as_str().into());
        let (participant_columns, participant_rows) = named_rows(
            connection,
            "SELECT * FROM participants WHERE campaign_id = ? ORDER BY participant_id",
            &[&campaign_id],
        )?;
        let mut stored_participants = Vec::new();
        for values in participant_rows {
            let row = Named { columns: &participant_columns, values };
            let participant_id = row.text("participant_id")?;
            let mut item = JsonMap::new();
            item.insert("participant_id".into(), participant_id.as_str().into());
            item.insert("kind".into(), row.text("kind")?.into());
            item.insert("package_id".into(), row.text("package_id")?.into());
            item.insert("package_version".into(), row.text("package_version")?.into());
            item.insert("package_sha256".into(), optional_text(row.get("package_sha256")?));
            item.insert("schema_id".into(), optional_text(row.get("schema_id")?));
            item.insert("schema_sha256".into(), optional_text(row.get("schema_sha256")?));
            item.insert("validation_status".into(), row.text("validation_status")?.into());
            item.insert("adaptation_id".into(), optional_text(row.get("adaptation_id")?));
            let current = load_payload(&row.text("data_json")?)?;
            let history = crate::participant_history::participant_data_history(
                connection,
                &campaign_id,
                &participant_id,
                current,
            )?;
            item.insert("data".into(), history.last().cloned().unwrap_or(Json::Null));
            stored_participants.push(Json::Object(item));
        }
        if confirmation_payload.contains_key("participants") {
            expected.insert("participants".into(), Json::Array(stored_participants));
        } else if !stored_participants.is_empty() {
            return Err(value_error("Campaign confirmation participants are unsigned"));
        }
        if let Some(initial_state) = confirmation_payload.get("initial_state") {
            if !matches!(initial_state, Json::Array(_)) {
                return Err(value_error("Campaign confirmation initial state is invalid"));
            }
            expected.insert("initial_state".into(), initial_state.clone());
        }
        match confirmation_payload.get("package_candidate_id") {
            None | Some(Json::Null) => {}
            Some(Json::String(candidate_id)) => {
                let candidate = crate::packages::read_candidate(connection, candidate_id)?;
                if !crate::stored_sessions::handoff_candidate_matches_lock(&lock_record, Some(&candidate)) {
                    return Err(value_error("Campaign confirmation package candidate is inexact"));
                }
                expected.insert("package_candidate_id".into(), candidate_id.as_str().into());
            }
            Some(_) => return Err(value_error("Campaign confirmation package candidate is invalid")),
        }
        match confirmation_payload.get("builder_session") {
            None | Some(Json::Null) => {}
            Some(Json::Object(builder)) => {
                let invalid = || value_error("Campaign confirmation Builder session is invalid");
                let text = |key: &str| match builder.get(key) {
                    Some(Json::String(value)) => Some(value.clone()),
                    _ => None,
                };
                let (Some(session_id), Some(provider_id), Some(model_id), Some(skill)) =
                    (text("session_id"), text("provider_id"), text("model_id"), text("selected_skill"))
                else {
                    return Err(invalid());
                };
                if !BUILDER_SKILLS.contains(&skill.as_str()) {
                    return Err(invalid());
                }
                for (value, label) in [
                    (&session_id, "Campaign confirmation Builder session"),
                    (&provider_id, "Campaign confirmation Builder provider"),
                    (&model_id, "Campaign confirmation Builder model"),
                ] {
                    validate_authority_identity(value, label, false)?;
                }
                let (session_columns, session_rows) = named_rows(
                    connection,
                    "SELECT * FROM agent_sessions WHERE session_id = ?",
                    &[&session_id],
                )?;
                let refused = match session_rows.into_iter().next() {
                    None => true,
                    Some(values) => {
                        let session = Named { columns: &session_columns, values };
                        session.text("campaign_id")? != campaign_id
                            || session.text("role")? != WriterRole::Builder.as_str()
                            || session.get("parent_session_id")? != &Value::Null
                            || session.text("selected_skill")? != skill
                    }
                };
                if refused {
                    return Err(invalid());
                }
                expected.insert(
                    "builder_session".into(),
                    object([
                        ("session_id", session_id.into()),
                        ("provider_id", provider_id.into()),
                        ("model_id", model_id.into()),
                        ("selected_skill", skill.into()),
                    ]),
                );
            }
            Some(_) => return Err(value_error("Campaign confirmation Builder session is invalid")),
        }
        let refused = confirmation.text("operation_kind")? != "confirm_new_campaign"
            || confirmation.text("campaign_id")? != campaign_id
            || confirmation.text("basis_turn_id")? != authority_turn_id
            || confirmation.text("basis_lock_id")? != authority_lock_id
            || confirmation.int("basis_revision")? != confirmation_revision
            || confirmation.int("resulting_revision")? != confirmation_revision
            || lock_record.created_revision != confirmation_revision
            || !crate::packages::has_only_draft_lifecycle_history(
                connection,
                &campaign_id,
                confirmation_revision,
            )?
            || !py_eq(&Json::Object(confirmation_payload.clone()), &Json::Object(expected));
        if refused {
            return Err(value_error("Campaign confirmation authority is invalid"));
        }
        seen.insert(confirmation_id.clone());

        let lane_value = construction.get("lane")?.clone();
        let Value::Null = construction.get("begin_operation_id")? else {
            let lane = py_str(&lane_value);
            let begin_id = construction.text("begin_operation_id")?;
            let begin = operations.get(&begin_id);
            let canonical_input = load_payload(&construction.text("canonical_input_json")?)?;
            let begin_revision = construction.int("begin_revision")?;
            let Some(begin) = begin else {
                return Err(value_error("Campaign construction begin receipt is unavailable"));
            };
            let begin_payload = load_payload(&begin.text("semantic_payload_json")?)?;
            let begin_result = operation_result(begin)?;
            let refused = match &begin_result {
                OperationResult::Begun(begun) => {
                    begun.lane != lane
                        || begun.resulting_revision != begin_revision
                        || begin.text("operation_kind")? != "begin_campaign_construction"
                        || begin.text("campaign_id")? != campaign_id
                        || begin.text("basis_turn_id")? != authority_turn_id
                        || begin.text("basis_lock_id")? != authority_lock_id
                        || begin.int("basis_revision")? != confirmation_revision
                        || begin.int("resulting_revision")? != begin_revision
                        || Some(begin_revision) != confirmation_revision.checked_add(1)
                        || !py_eq(
                            &Json::Object(begin_payload),
                            &object([
                                ("operation_id", begin_id.as_str().into()),
                                ("operation_kind", "begin_campaign_construction".into()),
                                ("campaign_id", campaign_id.as_str().into()),
                                ("lane", lane.as_str().into()),
                                ("canonical_input", Json::Object(canonical_input.clone())),
                            ]),
                        )
                        || differs(&revision_operation(connection, &campaign_id, begin_revision)?, &begin_id)
                }
                _ => true,
            };
            if refused {
                return Err(value_error("Campaign construction begin authority is invalid"));
            }
            seen.insert(begin_id.clone());

            if construction.get("completion_operation_id")? == &Value::Null {
                for column in
                    ["canonical_plan_json", "completion_revision", "canonical_representation_sha256"]
                {
                    if construction.get(column)? != &Value::Null {
                        return Err(value_error("Incomplete construction has completion authority"));
                    }
                }
                continue;
            }
            let completion_id = construction.text("completion_operation_id")?;
            let completion = operations.get(&completion_id);
            let canonical_plan = load_payload(&construction.text("canonical_plan_json")?)?;
            let completion_revision = construction.int("completion_revision")?;
            let representation_sha256 = sha256_hex(canonical_json_bytes(&object([
                ("canonical_input", Json::Object(canonical_input)),
                ("canonical_plan", Json::Object(canonical_plan.clone())),
            ])));
            let Some(completion) = completion else {
                return Err(value_error("Campaign construction completion receipt is unavailable"));
            };
            let completion_payload = load_payload(&completion.text("semantic_payload_json")?)?;
            let completion_result = operation_result(completion)?;
            let refused = match &completion_result {
                OperationResult::Completed(completed) => {
                    completion.text("operation_kind")? != "complete_campaign_construction"
                        || completion.text("campaign_id")? != campaign_id
                        || completion.text("basis_turn_id")? != authority_turn_id
                        || completion.text("basis_lock_id")? != authority_lock_id
                        || completion.int("basis_revision")? != begin_revision
                        || completion.int("resulting_revision")? != completion_revision
                        || Some(completion_revision) != begin_revision.checked_add(1)
                        || !py_eq(
                            &Json::Object(completion_payload),
                            &object([
                                ("operation_id", completion_id.as_str().into()),
                                ("operation_kind", "complete_campaign_construction".into()),
                                ("campaign_id", campaign_id.as_str().into()),
                                ("canonical_plan", Json::Object(canonical_plan)),
                            ]),
                        )
                        || construction.text("canonical_representation_sha256")? != representation_sha256
                        || completed.lane != lane
                        || completed.canonical_representation_sha256 != representation_sha256
                        || differs(
                            &revision_operation(connection, &campaign_id, completion_revision)?,
                            &completion_id,
                        )
                }
                _ => true,
            };
            if refused {
                return Err(value_error("Campaign construction completion authority is invalid"));
            }
            seen.insert(completion_id);
            continue;
        };
        for column in [
            "lane",
            "canonical_input_json",
            "begin_revision",
            "canonical_plan_json",
            "completion_operation_id",
            "completion_revision",
            "canonical_representation_sha256",
        ] {
            if construction.get(column)? != &Value::Null {
                return Err(value_error("Unbegun campaign construction has staged authority"));
            }
        }
    }

    let (transition_columns, transition_rows) = named_rows(
        connection,
        "SELECT * FROM campaign_lifecycle_transitions ORDER BY campaign_id, resulting_revision",
        &[],
    )?;
    for values in transition_rows {
        let transition = Named { columns: &transition_columns, values };
        let operation_id = transition.text("operation_id")?;
        let operation = operations.get(&operation_id);
        let revision = first_row(
            connection,
            "SELECT lifecycle_operation_id FROM campaign_revisions WHERE campaign_id = ? AND revision = ?",
            &[&transition.text("campaign_id")?, &transition.int("resulting_revision")?],
        )?;
        let (Some(operation), Some(revision)) = (operation, revision) else {
            return Err(value_error("Campaign lifecycle operation authority is unavailable"));
        };
        let result = operation_result(operation)?;
        let refused = match &result {
            OperationResult::Lifecycle(lifecycle) => {
                operation.text("operation_kind")? != transition.text("operation_kind")?
                    || operation.text("campaign_id")? != transition.text("campaign_id")?
                    || operation.int("basis_revision")? != transition.int("basis_revision")?
                    || operation.int("resulting_revision")? != transition.int("resulting_revision")?
                    || lifecycle.lifecycle.as_str() != transition.text("to_lifecycle")?
                    || lifecycle.writer.as_str() != transition.text("to_writer")?
                    || lifecycle.session_id != transition.optional_text("designated_session_id")?
                    || py_str(&revision[0]) != operation_id
            }
            _ => true,
        };
        if refused {
            return Err(value_error("Campaign lifecycle operation authority is invalid"));
        }
        seen.insert(operation_id);
    }
    if seen.len() != operations.len() || operations.keys().any(|id| !seen.contains(id)) {
        return Err(value_error("Campaign operation receipt is orphaned"));
    }
    Ok(())
}

/// `connection.execute("SELECT construction_operation_id ...").fetchone()[0]`: a missing row is
/// Python's `TypeError` (subscripting `None`).
fn revision_operation(connection: &Connection, campaign_id: &str, revision: i64) -> StoreResult<Value> {
    first_row(
        connection,
        "SELECT construction_operation_id FROM campaign_revisions WHERE campaign_id = ? AND revision = ?",
        &[&campaign_id, &revision],
    )?
    .and_then(|row| row.into_iter().next())
    .ok_or_else(|| StoreError::Other("'NoneType' object is not subscriptable".into()))
}
