//! Recovering one staged portable campaign, from `sqlite_recovery.py` (`semantic_payload`,
//! `lookup_receipt`, `RELATION_UNIQUE_KEYS`, `_row_status`, `_blob_chunk_rows`,
//! `_staging_chunk_rows`, `plan_recovery`, `_insert_row`, `insert_recovery_graph`,
//! `insert_receipt`, `plan_keys_absent`) and `sqlite_authority.py` (`campaign_recovery_hit`,
//! `recover_campaign`, `_resolve_recovery_commit_ambiguity` and the two commit seams).
//!
//! A row's values are compared with the stored row as Python compares them: `1 == 1.0 == True`,
//! text with text and bytes with bytes, and a list or object equals nothing SQLite returns. A
//! value is bound as Python's `sqlite3` binds it (a bool as an integer); a list or object is its
//! `ProgrammingError`. The archive validator admits neither, so no vector reaches that.

use crate::campaign_archive::{self, FORMAT_ID, FORMAT_VERSION, PRODUCT_GENERATION, PRODUCT_ID, TABLES};
use crate::campaigns::named_rows;
use crate::error::{schema_identity, StoreError, StoreResult};
use crate::pytext::py_str;
use crate::recovery::{receipt_result, result_json, verify_recovery_receipts, CampaignRecoveryResult};
use crate::recovery_staging::{uncaught, StagedCampaignArchive};
use crate::schema::{self, Known, CAMPAIGN_SCHEMA_VERSION};
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use rusqlite::Connection;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::package_library::canonical_json_bytes;

/// `RELATION_UNIQUE_KEYS`: every primary and unique identity of each recovered relation, the
/// primary first.
const RELATION_UNIQUE_KEYS: &[(&str, &[&[&str]])] = &[
    ("campaigns", &[&["campaign_id"]]),
    ("campaign_lifecycle_state", &[&["campaign_id"]]),
    ("campaign_lifecycle_transitions", &[&["operation_id"], &["campaign_id", "resulting_revision"]]),
    ("campaign_operations", &[&["operation_id"]]),
    (
        "campaign_constructions",
        &[
            &["campaign_id"],
            &["confirmation_operation_id"],
            &["begin_operation_id"],
            &["completion_operation_id"],
        ],
    ),
    (
        "campaign_continuity_records",
        &[&["campaign_id", "sequence"], &["campaign_id", "entry_id"], &["operation_id"]],
    ),
    ("campaign_revisions", &[&["campaign_id", "revision"]]),
    (
        "campaign_package_memberships",
        &[&["campaign_id", "package_id", "version", "sha256"], &["campaign_id", "load_order"]],
    ),
    ("participants", &[&["campaign_id", "participant_id"]]),
    ("participant_quarantines", &[&["campaign_id", "participant_id"]]),
    ("campaign_state_namespace_owners", &[&["campaign_id", "namespace"]]),
    ("campaign_state", &[&["campaign_id", "namespace", "state_key"]]),
    ("turns", &[&["campaign_id", "turn_id"]]),
    ("actions", &[&["campaign_id", "action_id"]]),
    ("idempotency_records", &[&["campaign_id", "idempotency_key"]]),
    ("pending_mechanics_actions", &[&["campaign_id"], &["campaign_id", "action_id"]]),
    ("pending_mechanics_action_aliases", &[&["campaign_id", "idempotency_key"]]),
    ("mechanics_choice_resolutions", &[&["campaign_id", "resolution_id"], &["campaign_id", "action_id"]]),
    ("canonical_events", &[&["campaign_id", "sequence"], &["campaign_id", "attempt_id", "attempt_ordinal"]]),
    (
        "narration_interruptions",
        &[
            &["campaign_id", "interruption_id"],
            &["campaign_id", "failed_attempt_id"],
            &["campaign_id", "resolving_retry_id"],
        ],
    ),
    ("mutations", &[&["campaign_id", "action_id", "ordinal"]]),
    ("consequences", &[&["campaign_id", "action_id", "ordinal"]]),
    ("campaign_locks", &[&["lock_id"]]),
    ("campaign_lock_memberships", &[&["lock_id", "closure_order"], &["lock_id", "package_id"]]),
    ("package_activations", &[&["activation_id"]]),
    (
        "campaign_plans",
        &[&["plan_id"], &["campaign_id", "plan_revision"], &["campaign_id", "resulting_campaign_revision"]],
    ),
    ("builder_dm_handoffs", &[&["handoff_id"], &["campaign_id"], &["dm_session_id"]]),
    ("agent_sessions", &[&["session_id"]]),
    ("agent_session_messages", &[&["session_id", "sequence"], &["session_id", "message_id"]]),
    ("package_candidates", &[&["candidate_id"], &["install_id"]]),
    ("package_candidate_memberships", &[&["candidate_id", "closure_order"], &["candidate_id", "package_id"]]),
    ("package_staging", &[&["install_id"]]),
    ("packages", &[&["package_id", "version", "sha256"]]),
    (
        "package_dependencies",
        &[
            &["package_id", "version", "sha256", "dependency_order"],
            &["package_id", "version", "sha256", "dependency_id"],
        ],
    ),
    ("package_manifests", &[&["package_id", "version", "content_sha256"]]),
    (
        "package_manifest_files",
        &[
            &["package_id", "version", "content_sha256", "file_order"],
            &["package_id", "version", "content_sha256", "portable_path"],
        ],
    ),
    (
        "package_exact_dependencies",
        &[
            &["package_id", "version", "content_sha256", "dependency_order"],
            &["package_id", "version", "content_sha256", "dependency_namespace"],
        ],
    ),
    (
        "package_capabilities",
        &[
            &["package_id", "version", "content_sha256", "capability_order"],
            &["package_id", "version", "content_sha256", "capability"],
        ],
    ),
    (
        "package_tools",
        &[
            &["package_id", "version", "content_sha256", "tool_order"],
            &["package_id", "version", "content_sha256", "tool_name"],
        ],
    ),
    (
        "package_prompt_fragments",
        &[
            &["package_id", "version", "content_sha256", "surface_order"],
            &["package_id", "version", "content_sha256", "fragment_id"],
        ],
    ),
    (
        "package_ui_surfaces",
        &[
            &["package_id", "version", "content_sha256", "surface_order"],
            &["package_id", "version", "content_sha256", "surface_id"],
        ],
    ),
    (
        "package_participant_artifacts",
        &[
            &["package_id", "version", "content_sha256", "artifact_order"],
            &["package_id", "version", "content_sha256", "kind"],
        ],
    ),
    ("package_blobs", &[&["blob_sha256"]]),
    ("package_blob_chunks", &[&["blob_sha256", "chunk_order"]]),
    ("package_staging_chunks", &[&["install_id", "file_order", "chunk_order"]]),
];

/// `_REUSABLE_TABLES`: package content a recovered campaign may share with existing authority.
const REUSABLE_TABLES: [&str; 16] = [
    "package_candidates",
    "package_candidate_memberships",
    "package_staging",
    "packages",
    "package_dependencies",
    "package_manifests",
    "package_manifest_files",
    "package_exact_dependencies",
    "package_capabilities",
    "package_tools",
    "package_prompt_fragments",
    "package_ui_surfaces",
    "package_participant_artifacts",
    "package_blobs",
    "package_blob_chunks",
    "package_staging_chunks",
];

/// `_PHYSICAL_COLUMNS`: the two relations rebuilt from the archive's blobs, in this order.
const PHYSICAL_COLUMNS: [(&str, &[&str]); 2] = [
    ("package_blob_chunks", &["blob_sha256", "chunk_order", "byte_count", "chunk_sha256", "data"]),
    (
        "package_staging_chunks",
        &[
            "install_id",
            "file_order",
            "chunk_order",
            "package_id",
            "version",
            "content_sha256",
            "portable_path",
            "chunk_sha256",
            "data",
        ],
    ),
];

/// `RecoveryPlan`: the collision plan, with each new row's primary identity.
#[derive(Debug, Clone)]
pub struct RecoveryPlan {
    pub inserted_count: u64,
    pub reused_count: u64,
    new_keys: Vec<(String, Vec<&'static str>, Vec<Cell>)>,
}

/// One value of a recovered row: a parsed JSON value or a rebuilt chunk's bytes.
#[derive(Debug, Clone)]
enum Cell {
    Json(Json),
    Bytes(Vec<u8>),
}

/// One recovered row: its members in their order.
#[derive(Debug, Clone, Default)]
struct Row(Vec<(String, Cell)>);

impl Row {
    fn from_json(row: JsonMap) -> Self {
        Self(row.into_iter().map(|(key, value)| (key, Cell::Json(value))).collect())
    }

    fn push(&mut self, key: &str, value: Cell) {
        self.0.push((key.to_owned(), value));
    }

    /// `row[column]`, or Python's `KeyError`.
    fn get(&self, column: &str) -> StoreResult<&Cell> {
        self.0
            .iter()
            .find(|(key, _)| key == column)
            .map(|(_, value)| value)
            .ok_or_else(|| uncaught("KeyError", format!("'{column}'")))
    }

    fn values(&self, columns: &[&str]) -> StoreResult<Vec<Cell>> {
        columns.iter().map(|column| self.get(column).cloned()).collect()
    }
}

fn unique_keys(table: &str) -> &'static [&'static [&'static str]] {
    RELATION_UNIQUE_KEYS.iter().find(|(name, _)| *name == table).map_or(&[], |(_, keys)| keys)
}

/// `_projected_columns(table)`.
fn projected_columns(table: &str) -> &'static [&'static str] {
    if let Some(table) = campaign_archive::table(table) {
        return table.columns;
    }
    PHYSICAL_COLUMNS.iter().find(|(name, _)| *name == table).map_or(&[], |(_, columns)| columns)
}

/// Python's exact comparison of an integer with a float.
fn int_float_eq(integer: i128, float: f64) -> bool {
    float.is_finite() && float.fract() == 0.0 && float.abs() < 1.8e19 && float as i128 == integer
}

/// `value == stored`, as Python compares a row's value with what `sqlite3` returns.
fn cell_eq(cell: &Cell, stored: &Value) -> bool {
    match (cell, stored) {
        (Cell::Bytes(bytes), Value::Blob(stored)) => bytes == stored,
        (Cell::Bytes(_), _) => false,
        (Cell::Json(Json::Null), Value::Null) => true,
        (Cell::Json(Json::Bool(flag)), stored) => int_eq(i128::from(*flag), stored),
        (Cell::Json(Json::Number(number)), stored) => {
            if let Some(integer) = number.as_i64() {
                int_eq(i128::from(integer), stored)
            } else if let Some(integer) = number.as_u64() {
                int_eq(i128::from(integer), stored)
            } else {
                let float = number.as_f64().unwrap_or(f64::NAN);
                match stored {
                    Value::Integer(integer) => int_float_eq(i128::from(*integer), float),
                    Value::Real(real) => float == *real,
                    _ => false,
                }
            }
        }
        (Cell::Json(Json::String(text)), Value::Text(stored)) => text == stored,
        _ => false,
    }
}

fn int_eq(integer: i128, stored: &Value) -> bool {
    match stored {
        Value::Integer(stored) => i128::from(*stored) == integer,
        Value::Real(real) => int_float_eq(integer, *real),
        _ => false,
    }
}

/// Two stored values compared as Python compares them.
fn value_eq(left: &Value, right: &Value) -> bool {
    match (left, right) {
        (Value::Null, Value::Null) => true,
        (Value::Integer(left), Value::Integer(right)) => left == right,
        (Value::Real(left), Value::Real(right)) => left == right,
        (Value::Integer(integer), Value::Real(real)) | (Value::Real(real), Value::Integer(integer)) => {
            int_float_eq(i128::from(*integer), *real)
        }
        (Value::Text(left), Value::Text(right)) => left == right,
        (Value::Blob(left), Value::Blob(right)) => left == right,
        _ => false,
    }
}

/// The values bound for `cells`, as Python's `sqlite3` binds them.
fn bind(cells: &[Cell]) -> StoreResult<Vec<Value>> {
    cells
        .iter()
        .enumerate()
        .map(|(index, cell)| match cell {
            Cell::Bytes(bytes) => Ok(Value::Blob(bytes.clone())),
            Cell::Json(Json::Null) => Ok(Value::Null),
            Cell::Json(Json::Bool(flag)) => Ok(Value::Integer(i64::from(*flag))),
            Cell::Json(Json::Number(number)) => match (number.as_i64(), number.is_u64()) {
                (Some(integer), _) => Ok(Value::Integer(integer)),
                (None, true) => {
                    Err(uncaught("OverflowError", "Python int too large to convert to SQLite INTEGER"))
                }
                (None, false) => Ok(Value::Real(number.as_f64().unwrap_or(f64::NAN))),
            },
            Cell::Json(Json::String(text)) => Ok(Value::Text(text.clone())),
            Cell::Json(other) => Err(uncaught(
                "ProgrammingError",
                format!(
                    "Error binding parameter {}: type '{}' is not supported",
                    index + 1,
                    if other.is_array() { "list" } else { "dict" }
                ),
            )),
        })
        .collect()
}

fn quoted(columns: &[&str]) -> String {
    columns.iter().map(|column| format!("\"{column}\"")).collect::<Vec<_>>().join(", ")
}

/// `_row_status(connection, table, row)`: `insert` for a new identity, `reuse` for an identical
/// row of shareable package content, and a refusal for anything else.
fn row_is_new(connection: &Connection, table: &str, row: &Row) -> StoreResult<bool> {
    let columns = projected_columns(table);
    let keys = unique_keys(table);
    // `hits`, a dict keyed by each hit's primary identity, in first-hit order.
    let mut hits: Vec<(Vec<Value>, Vec<Value>)> = Vec::new();
    for key_columns in keys {
        let values = row.values(key_columns)?;
        // SQLite UNIQUE permits more than one row when any participating value is NULL.
        if values.iter().any(|value| matches!(value, Cell::Json(Json::Null))) {
            continue;
        }
        let bound = bind(&values)?;
        let where_clause =
            key_columns.iter().map(|column| format!("\"{column}\" = ?")).collect::<Vec<_>>().join(" AND ");
        let sql = format!("SELECT {} FROM \"{table}\" WHERE {where_clause}", quoted(columns));
        let mut statement = connection.prepare(&sql)?;
        let selected: Vec<Vec<Value>> = statement
            .query_map(rusqlite::params_from_iter(bound.iter()), |found| {
                (0..columns.len()).map(|index| found.get::<_, Value>(index)).collect()
            })?
            .collect::<Result<_, _>>()?;
        if selected.len() > 1 {
            return Err(crate::actions::rejected(
                "Portable recovery collision plan found ambiguous identity",
                "command_rejected",
            ));
        }
        if let Some(found) = selected.into_iter().next() {
            let primary: Vec<Value> = keys[0]
                .iter()
                .map(|column| found[columns.iter().position(|name| name == column).unwrap_or(0)].clone())
                .collect();
            let same = |known: &Vec<Value>| {
                known.len() == primary.len() && known.iter().zip(&primary).all(|(a, b)| value_eq(a, b))
            };
            match hits.iter_mut().find(|(known, _)| same(known)) {
                Some((_, projected)) => *projected = found,
                None => hits.push((primary, found)),
            }
        }
    }
    if hits.is_empty() {
        return Ok(true);
    }
    let expected = row.values(columns)?;
    let identical = hits.len() == 1
        && hits[0].1.len() == expected.len()
        && expected.iter().zip(&hits[0].1).all(|(cell, stored)| cell_eq(cell, stored));
    if !identical || !REUSABLE_TABLES.contains(&table) {
        return Err(crate::actions::rejected(
            "Portable recovery collision conflicts with existing authority",
            "portable_recovery_collision",
        ));
    }
    Ok(false)
}

fn text_of(cell: &Cell) -> String {
    match cell {
        Cell::Json(value) => crate::campaigns::json_str(value),
        Cell::Bytes(bytes) => sylvia_domain::pyops::py_bytes_repr(bytes),
    }
}

/// Python's `int(value)` of a parsed JSON value, or its `TypeError`/`ValueError`.
fn int_of(cell: &Cell) -> StoreResult<i128> {
    let refused =
        || uncaught("TypeError", "int() argument must be a string, a bytes-like object or a real number");
    match cell {
        Cell::Json(Json::Bool(flag)) => Ok(i128::from(*flag)),
        Cell::Json(Json::Number(number)) => number
            .as_i64()
            .map(i128::from)
            .or_else(|| number.as_u64().map(i128::from))
            .or_else(|| number.as_f64().filter(|float| float.is_finite()).map(|float| float.trunc() as i128))
            .ok_or_else(refused),
        Cell::Json(Json::String(text)) => {
            crate::pytext::py_int(&Value::Text(text.clone())).map(i128::from).ok_or_else(|| {
                uncaught(
                    "ValueError",
                    format!("invalid literal for int() with base 10: {}", sylvia_base::pystr::repr(text)),
                )
            })
        }
        _ => Err(refused()),
    }
}

/// `_blob_chunk_rows(staged)`, given to `each`.
fn blob_chunk_rows(
    staged: &StagedCampaignArchive,
    each: &mut dyn FnMut(Row) -> StoreResult<()>,
) -> StoreResult<()> {
    staged.for_each_row("package_blobs", |blob| {
        let blob = Row::from_json(blob);
        let blob_sha256 = text_of(blob.get("blob_sha256")?);
        let (mut chunk_order, mut byte_count) = (0i64, 0i128);
        staged.for_each_blob_chunk(&blob_sha256, |chunk| {
            byte_count += chunk.len() as i128;
            let mut row = Row::default();
            row.push("blob_sha256", Cell::Json(Json::from(blob_sha256.as_str())));
            row.push("chunk_order", Cell::Json(Json::from(chunk_order)));
            row.push("byte_count", Cell::Json(Json::from(chunk.len() as i64)));
            row.push("chunk_sha256", Cell::Json(Json::from(sha256_hex(&chunk))));
            row.push("data", Cell::Bytes(chunk));
            chunk_order += 1;
            each(row)
        })?;
        if byte_count != int_of(blob.get("byte_count")?)?
            || i128::from(chunk_order) != int_of(blob.get("chunk_count")?)?
        {
            return Err(crate::actions::rejected(
                "Portable recovery blob reconstruction is invalid",
                "portable_export_invalid_blob_closure",
            ));
        }
        Ok(())
    })
}

/// `_staging_chunk_rows(staged)`, given to `each`.
fn staging_chunk_rows(
    staged: &StagedCampaignArchive,
    each: &mut dyn FnMut(Row) -> StoreResult<()>,
) -> StoreResult<()> {
    let graph = || {
        crate::actions::rejected(
            "Portable recovery staging reconstruction is invalid",
            "portable_export_invalid_package_graph",
        )
    };
    staged.for_each_row("package_staging", |staging| {
        let staging = Row::from_json(staging);
        // `json.loads(str(...))["packages"]`: a missing member or a non-container is refused.
        let manifest =
            sylvia_base::pyjson::loads(&text_of(staging.get("manifest_json")?)).map_err(|_| graph())?;
        let packages = match &manifest {
            Json::Object(object) => object.get("packages").cloned().ok_or_else(graph)?,
            _ => return Err(graph()),
        };
        let Json::Array(packages) = packages else { return Err(graph()) };
        let install_id = staging.get("install_id")?.clone();
        let mut file_order = 0i64;
        for package in &packages {
            let Json::Object(package) = package else {
                return Err(uncaught("AttributeError", "object has no attribute 'get'"));
            };
            let Some(Json::Array(files)) = package.get("files") else { return Err(graph()) };
            let member = |key: &str| {
                package
                    .get(key)
                    .cloned()
                    .map(Cell::Json)
                    .ok_or_else(|| uncaught("KeyError", format!("'{key}'")))
            };
            for value in files {
                let Json::Object(value) = value else { return Err(graph()) };
                let field = |key: &str| {
                    value
                        .get(key)
                        .cloned()
                        .map(Cell::Json)
                        .ok_or_else(|| uncaught("KeyError", format!("'{key}'")))
                };
                let blob_sha256 = text_of(&field("sha256")?);
                let mut byte_count = 0i128;
                let mut chunk_order = 0i64;
                staged.for_each_blob_chunk(&blob_sha256, |chunk| {
                    byte_count += chunk.len() as i128;
                    let mut row = Row::default();
                    row.push("install_id", install_id.clone());
                    row.push("file_order", Cell::Json(Json::from(file_order)));
                    row.push("chunk_order", Cell::Json(Json::from(chunk_order)));
                    row.push("package_id", member("id")?);
                    row.push("version", member("version")?);
                    row.push("content_sha256", member("content_sha256")?);
                    row.push("portable_path", field("path")?);
                    row.push("chunk_sha256", Cell::Json(Json::from(sha256_hex(&chunk))));
                    row.push("data", Cell::Bytes(chunk));
                    chunk_order += 1;
                    each(row)
                })?;
                if byte_count != int_of(&field("bytes")?)? {
                    return Err(crate::actions::rejected(
                        "Portable recovery staging reconstruction is invalid",
                        "portable_export_invalid_blob_closure",
                    ));
                }
                file_order += 1;
            }
        }
        Ok(())
    })
}

/// `_physical_rows(staged, table)`, given to `each`.
fn physical_rows(
    staged: &StagedCampaignArchive,
    table: &str,
    each: &mut dyn FnMut(Row) -> StoreResult<()>,
) -> StoreResult<()> {
    match table {
        "package_blob_chunks" => blob_chunk_rows(staged, each),
        _ => staging_chunk_rows(staged, each),
    }
}

/// The present tables.
fn present_tables(connection: &Connection) -> StoreResult<Vec<String>> {
    let rows = named_rows(connection, "SELECT name FROM sqlite_master WHERE type = 'table'", &[])?.1;
    Ok(rows.iter().map(|row| py_str(&row[0])).collect())
}

/// The primary identity of a new row, for `plan_keys_absent`.
fn primary_key(table: &str, row: &Row) -> StoreResult<(String, Vec<&'static str>, Vec<Cell>)> {
    let keys = unique_keys(table)[0];
    Ok((table.to_owned(), keys.to_vec(), row.values(keys)?))
}

/// `semantic_payload(recovery_id, staged)`: the recovery's canonical semantic payload and its
/// SHA-256.
pub fn semantic_payload(recovery_id: &str, staged: &StagedCampaignArchive) -> StoreResult<(String, String)> {
    let manifest =
        |key: &str| staged.manifest.get(key).cloned().ok_or_else(|| uncaught("KeyError", format!("'{key}'")));
    let mut payload = JsonMap::new();
    payload.insert("operation_id".into(), recovery_id.into());
    payload.insert("operation_kind".into(), "recover_campaign".into());
    payload.insert("campaign_id".into(), staged.campaign_id.as_str().into());
    payload.insert("captured_revision".into(), staged.captured_revision.into());
    payload.insert("archive_sha256".into(), staged.archive_sha256.as_str().into());
    payload.insert("archive_byte_count".into(), staged.byte_count.into());
    payload.insert("format_id".into(), FORMAT_ID.into());
    payload.insert("format_version".into(), FORMAT_VERSION.into());
    payload.insert("product_id".into(), PRODUCT_ID.into());
    payload.insert("product_generation".into(), PRODUCT_GENERATION.into());
    for key in
        ["source_authority_schema_version", "source_authority_schema_identity_sha256", "migration_provenance"]
    {
        payload.insert(key.into(), manifest(key)?);
    }
    let encoded = canonical_json_bytes(&Json::Object(payload));
    let text = String::from_utf8(encoded).map_err(|error| StoreError::Other(error.to_string()))?;
    let sha256 = sha256_hex(text.as_bytes());
    Ok((text, sha256))
}

/// `lookup_receipt(connection, ...)`: the durable result of an earlier recovery with this
/// identity, refused if its archive semantics differ.
fn lookup_receipt(
    connection: &Connection,
    recovery_id: &str,
    payload_json: &str,
    payload_sha256: &str,
) -> StoreResult<Option<CampaignRecoveryResult>> {
    if !present_tables(connection)?.iter().any(|name| name == "campaign_recovery_receipts") {
        return Ok(None);
    }
    let (columns, rows) = named_rows(
        connection,
        "SELECT * FROM campaign_recovery_receipts WHERE recovery_id = ?",
        &[&recovery_id],
    )?;
    match rows.into_iter().next() {
        None => Ok(None),
        Some(values) => receipt_result(
            &crate::campaigns::Named { columns: &columns, values },
            payload_json,
            payload_sha256,
        )
        .map(Some),
    }
}

/// `TABLES_IN_ORDER`.
fn tables_in_order() -> Vec<&'static str> {
    TABLES.iter().map(|table| table.name).collect()
}

/// `plan_recovery(connection, staged)`: every collision and reuse decision, before any write.
pub fn plan_recovery(connection: &Connection, staged: &StagedCampaignArchive) -> StoreResult<RecoveryPlan> {
    let exists =
        named_rows(connection, "SELECT 1 FROM campaigns WHERE campaign_id = ?", &[&staged.campaign_id])?.1;
    if !exists.is_empty() {
        return Err(crate::actions::rejected(
            "Portable recovery campaign identity collision: campaign already exists",
            "portable_recovery_campaign_collision",
        ));
    }
    let present = present_tables(connection)?;
    let mut plan = RecoveryPlan { inserted_count: 0, reused_count: 0, new_keys: Vec::new() };
    for table in tables_in_order() {
        let is_present = present.iter().any(|name| name == table);
        staged.for_each_row(table, |row| {
            let row = Row::from_json(row);
            if !is_present || row_is_new(connection, table, &row)? {
                plan.inserted_count += 1;
                plan.new_keys.push(primary_key(table, &row)?);
            } else {
                plan.reused_count += 1;
            }
            Ok(())
        })?;
    }
    for (table, _) in PHYSICAL_COLUMNS {
        if !present.iter().any(|name| name == table) {
            continue;
        }
        physical_rows(staged, table, &mut |row| {
            if row_is_new(connection, table, &row)? {
                plan.inserted_count += 1;
                plan.new_keys.push(primary_key(table, &row)?);
            } else {
                plan.reused_count += 1;
            }
            Ok(())
        })?;
    }
    let private_present = present.iter().any(|name| name == "provider_private_records");
    for row in staged.private_records()? {
        let row = Row::from_json(row);
        let keys = ["session_id", "attempt_id", "source_order"];
        let identity = row.values(&keys)?;
        if private_present {
            let bound = bind(&identity)?;
            let found = named_rows(
                connection,
                "SELECT 1 FROM provider_private_records WHERE session_id = ? AND attempt_id = ? AND source_order = ?",
                &[&bound[0], &bound[1], &bound[2]],
            )?
            .1;
            if !found.is_empty() {
                return Err(crate::actions::rejected(
                    "Portable recovery private record identity collision",
                    "portable_recovery_private_record_collision",
                ));
            }
        }
        plan.inserted_count += 1;
        plan.new_keys.push(("provider_private_records".into(), keys.to_vec(), identity));
    }
    Ok(plan)
}

/// `_insert_row(connection, table, row)`: whether the row was new (and inserted).
fn insert_row(connection: &Connection, table: &str, row: &Row) -> StoreResult<bool> {
    if !row_is_new(connection, table, row)? {
        return Ok(false);
    }
    let columns = projected_columns(table);
    let placeholders = vec!["?"; columns.len()].join(", ");
    let bound = bind(&row.values(columns)?)?;
    let sql = format!("INSERT INTO \"{table}\" ({}) VALUES ({placeholders})", quoted(columns));
    match connection.execute(&sql, rusqlite::params_from_iter(bound.iter())) {
        Ok(_) => Ok(true),
        Err(rusqlite::Error::SqliteFailure(error, _))
            if error.code == rusqlite::ErrorCode::ConstraintViolation =>
        {
            Err(schema_identity(&format!("Portable recovery relation failed: {table}")))
        }
        Err(error) => Err(error.into()),
    }
}

/// `insert_recovery_graph`'s table order: package prerequisites before the first table that
/// names them, and the relations that refer back to the campaign's operations last.
fn insertion_order() -> Vec<&'static str> {
    let order = tables_in_order();
    let owner_index = order.iter().position(|table| *table == "campaign_state_namespace_owners").unwrap_or(0);
    let prerequisites = ["campaign_locks", "campaign_lock_memberships"];
    let mut provisional: Vec<&str> = order[..owner_index].to_vec();
    provisional.extend(prerequisites);
    provisional.extend(order[owner_index..].iter().filter(|table| !prerequisites.contains(table)));
    let package_prerequisites = [
        "package_blobs",
        "packages",
        "package_dependencies",
        "package_manifests",
        "package_manifest_files",
        "package_exact_dependencies",
        "package_capabilities",
        "package_tools",
        "package_prompt_fragments",
        "package_ui_surfaces",
        "package_participant_artifacts",
    ];
    let mut deferred: Vec<&str> = package_prerequisites.to_vec();
    deferred.extend([
        "campaign_package_memberships",
        "campaign_lifecycle_transitions",
        "campaign_operations",
        "campaign_continuity_records",
        "pending_mechanics_actions",
        "pending_mechanics_action_aliases",
        "mechanics_choice_resolutions",
    ]);
    let insertion: Vec<&str> = provisional.into_iter().filter(|table| !deferred.contains(table)).collect();
    let campaigns_index = insertion.iter().position(|table| *table == "campaigns").unwrap_or(0) + 1;
    let mut package_index = campaigns_index;
    for table in ["package_candidates", "package_staging", "package_activations", "campaign_locks"] {
        if let Some(index) = insertion.iter().position(|name| *name == table) {
            package_index = package_index.min(index);
        }
    }
    let mut result: Vec<&str> = insertion[..package_index].to_vec();
    result.extend(package_prerequisites);
    result.push("campaign_package_memberships");
    result.extend(&insertion[package_index..]);
    result.extend([
        "campaign_operations",
        "campaign_continuity_records",
        "campaign_lifecycle_transitions",
        "pending_mechanics_actions",
        "pending_mechanics_action_aliases",
        "mechanics_choice_resolutions",
    ]);
    result
}

/// `insert_recovery_graph(connection, staged)`: every staged row inserted or reused, then the
/// rebuilt chunks and the private records; the inserted and reused counts.
fn insert_recovery_graph(connection: &Connection, staged: &StagedCampaignArchive) -> StoreResult<(u64, u64)> {
    let (mut inserted, mut reused) = (0u64, 0u64);
    let mut count = |new: bool| if new { inserted += 1 } else { reused += 1 };
    for table in insertion_order() {
        staged.for_each_row(table, |row| {
            count(insert_row(connection, table, &Row::from_json(row))?);
            Ok(())
        })?;
    }
    for (table, _) in PHYSICAL_COLUMNS {
        physical_rows(staged, table, &mut |row| {
            count(insert_row(connection, table, &row)?);
            Ok(())
        })?;
    }
    for row in staged.private_records()? {
        let row = Row::from_json(row);
        let fields = crate::private_records::FIELDS;
        let mut values = row.values(&fields)?;
        let status = match row.get("status")? {
            Cell::Json(Json::String(status)) if status == "partial" => Cell::Json(Json::from("interrupted")),
            other => other.clone(),
        };
        let last = values.len() - 1;
        values[last] = status;
        values.push(row.get("session_id")?.clone());
        let bound = bind(&values)?;
        let sql = format!(
            "INSERT INTO provider_private_records ({}, agent_session_id, builder_session_id) VALUES ({}, ?, NULL)",
            fields.join(", "),
            vec!["?"; fields.len()].join(", ")
        );
        connection.execute(&sql, rusqlite::params_from_iter(bound.iter()))?;
        inserted += 1;
    }
    Ok((inserted, reused))
}

/// `insert_receipt(connection, ...)`.
fn insert_receipt(
    connection: &Connection,
    recovery_id: &str,
    payload_json: &str,
    payload_sha256: &str,
    result: &CampaignRecoveryResult,
) -> StoreResult<()> {
    connection.execute(
        "INSERT INTO campaign_recovery_receipts (recovery_id, campaign_id, semantic_payload_json, \
         semantic_payload_sha256, archive_sha256, archive_byte_count, captured_revision, result_json) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rusqlite::params![
            recovery_id,
            result.campaign_id,
            payload_json,
            payload_sha256,
            result.archive_sha256,
            result.byte_count,
            result.captured_revision,
            result_json(result),
        ],
    )?;
    Ok(())
}

/// `plan_keys_absent(connection, plan)`: whether none of the plan's new identities exists.
fn plan_keys_absent(connection: &Connection, plan: &RecoveryPlan) -> StoreResult<bool> {
    let present = present_tables(connection)?;
    for (table, columns, values) in &plan.new_keys {
        if !present.iter().any(|name| name == table) {
            continue;
        }
        let where_clause =
            columns.iter().map(|column| format!("\"{column}\" = ?")).collect::<Vec<_>>().join(" AND ");
        let bound = bind(values)?;
        let mut statement = connection.prepare(&format!("SELECT 1 FROM \"{table}\" WHERE {where_clause}"))?;
        if statement.exists(rusqlite::params_from_iter(bound.iter()))? {
            return Ok(false);
        }
    }
    Ok(true)
}

/// `CommandRejected("Portable recovery commit outcome is uncertain")`.
fn outcome_uncertain() -> StoreError {
    crate::actions::rejected(
        "Portable recovery commit outcome is uncertain",
        "portable_recovery_commit_outcome_uncertain",
    )
}

impl AuthorityStore {
    /// `campaign_recovery_hit(recovery_id=..., staged=...)`: the side-effect-free receipt lookup
    /// before the campaign's lane is taken.
    pub fn campaign_recovery_hit(
        &self,
        recovery_id: &str,
        staged: &StagedCampaignArchive,
    ) -> StoreResult<Option<CampaignRecoveryResult>> {
        let (payload_json, payload_sha256) = semantic_payload(recovery_id, staged)?;
        let connection = self.connect_read_only()?;
        lookup_receipt(&connection, recovery_id, &payload_json, &payload_sha256)
    }

    /// `recover_campaign(recovery_id=..., staged=...)`: the staged graph recovered in one
    /// `BEGIN IMMEDIATE` transaction with its receipt. A recovery that may have committed is
    /// resolved from durable authority, or refused as uncertain.
    pub fn recover_campaign(
        &self,
        recovery_id: &str,
        staged: &StagedCampaignArchive,
    ) -> StoreResult<CampaignRecoveryResult> {
        let (payload_json, payload_sha256) = semantic_payload(recovery_id, staged)?;
        let connection = self.connect()?;
        let mut plan = None;
        let mut commit_started = false;
        let outcome = self.recovery_transaction(
            &connection,
            recovery_id,
            staged,
            &payload_json,
            &payload_sha256,
            &mut plan,
            &mut commit_started,
        );
        let error = match outcome {
            Ok(result) => return Ok(result),
            Err(error) => error,
        };
        if !commit_started {
            if !connection.is_autocommit() {
                let _ = connection.execute_batch("ROLLBACK");
            }
            return Err(error);
        }
        // Commit and acknowledgement failures are resolved only from durable read-only
        // authority, after the uncertain connection is closed.
        drop(connection);
        match self.resolve_recovery_commit(recovery_id, &payload_json, &payload_sha256, staged, plan.as_ref())
        {
            Ok(Some(result)) => Ok(result),
            Ok(None) => Err(error),
            Err(error) => Err(error),
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn recovery_transaction(
        &self,
        connection: &Connection,
        recovery_id: &str,
        staged: &StagedCampaignArchive,
        payload_json: &str,
        payload_sha256: &str,
        plan_out: &mut Option<RecoveryPlan>,
        commit_started: &mut bool,
    ) -> StoreResult<CampaignRecoveryResult> {
        connection.execute_batch("BEGIN IMMEDIATE")?;
        let version = crate::verify::verify_exact_schema_identity(connection, None)?;
        if let Some(replay) = lookup_receipt(connection, recovery_id, payload_json, payload_sha256)? {
            connection.execute_batch("ROLLBACK")?;
            return Ok(replay);
        }
        crate::stored::verify_stored_authority(connection)?;
        crate::packages::verify_package_authority(connection)?;
        if version == CAMPAIGN_SCHEMA_VERSION {
            crate::f5::verify_f5_campaign_authority(connection)?;
        }
        plan_recovery(connection, staged)?;
        self.ensure_campaign_schema(connection)?;
        if schema::schema_identity_of(connection)? != *Known::Effort.identity() {
            return Err(schema_identity("Portable recovery schema ownership is not exact"));
        }
        let plan = plan_out.insert(plan_recovery(connection, staged)?);
        // The migration and every semantic insert share this same transaction.
        connection.execute_batch("PRAGMA defer_foreign_keys = ON")?;
        let (inserted, reused) = insert_recovery_graph(connection, staged)?;
        if inserted != plan.inserted_count || reused != plan.reused_count {
            return Err(schema_identity("Portable recovery collision plan drifted"));
        }
        let result = CampaignRecoveryResult {
            campaign_id: staged.campaign_id.clone(),
            captured_revision: staged.captured_revision,
            archive_sha256: staged.archive_sha256.clone(),
            byte_count: staged.byte_count,
        };
        insert_receipt(connection, recovery_id, payload_json, payload_sha256, &result)?;
        crate::verify::verify_exact_schema_identity(connection, Some(CAMPAIGN_SCHEMA_VERSION))?;
        crate::stored::verify_stored_authority(connection)?;
        crate::packages::verify_package_authority(connection)?;
        crate::f5::verify_f5_campaign_authority(connection)?;
        crate::continuity::verify_authority(connection)?;
        if let Some(fault) = &self.faults.before_recovery_commit {
            fault()?;
        }
        *commit_started = true;
        connection.execute_batch("COMMIT")?;
        if let Some(fault) = &self.faults.after_recovery_commit {
            fault()?;
        }
        Ok(result)
    }

    /// `_resolve_recovery_commit_ambiguity`: the receipt's result if the recovery committed,
    /// `None` if nothing it would have written exists, and otherwise the uncertain refusal. A
    /// failure while reading is uncertain too.
    fn resolve_recovery_commit(
        &self,
        recovery_id: &str,
        payload_json: &str,
        payload_sha256: &str,
        staged: &StagedCampaignArchive,
        plan: Option<&RecoveryPlan>,
    ) -> StoreResult<Option<CampaignRecoveryResult>> {
        let read = || -> StoreResult<Option<CampaignRecoveryResult>> {
            let connection = self.connect_read_only()?;
            if let Some(receipt) = lookup_receipt(&connection, recovery_id, payload_json, payload_sha256)? {
                verify_recovery_receipts(&connection)?;
                return Ok(Some(receipt));
            }
            let campaign_absent = named_rows(
                &connection,
                "SELECT 1 FROM campaigns WHERE campaign_id = ?",
                &[&staged.campaign_id],
            )?
            .1
            .is_empty();
            if campaign_absent {
                if let Some(plan) = plan {
                    if plan_keys_absent(&connection, plan)? {
                        return Ok(None);
                    }
                }
            }
            Err(outcome_uncertain())
        };
        // Python re-raises only its own `CommandRejected`, which here is the uncertain refusal.
        read().map_err(|_| outcome_uncertain())
    }
}
