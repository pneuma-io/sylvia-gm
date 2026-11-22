//! Capturing one campaign for portable export, in the store: `SQLiteAuthorityStore.
//! _capture_campaign_export` with `_assert_export_source_schema`, `_export_relation_predicates`,
//! `_preflight_export_relations` and `_read_export_rows`, and the `_ExportFailure` model
//! (`_as_export_failure`, `_run_export_stage`). Writing the archive (`export_campaign`) is
//! [`crate::export_writer`].
//!
//! The fault seam `_export_failure_stage(stage)` is [`crate::store::Faults::export_stage`].
//!
//! Declared difference: an evidence message's content holding an integer past 64 bits, which
//! Python reads and rewrites, fails the capture's read here (`docs/rust-port/differences.md`).

use crate::campaign_archive::{
    self, canonical_authority_record, canonical_authority_record_size, checked_archive_ledger, limit,
    CapturedRows, TABLES,
};
use crate::campaigns::named_rows;
use crate::error::{StoreError, StoreResult};
use crate::pytext::{py_int, py_str};
use crate::schema::{self, Known, CAMPAIGN_SCHEMA_VERSION};
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use rusqlite::Connection;
use std::collections::HashSet;
use sylvia_base::pyjson::{self, Dump, HookPolicy};
use sylvia_base::{CodedError, Json, JsonMap};

/// Session evidence a portable export carries only as `{"kind", "omitted_from_export": true}`
/// (`_EXPORT_STUBBED_EVIDENCE_KINDS`).
pub const EXPORT_STUBBED_EVIDENCE_KINDS: [&str; 2] = ["gm_native_records", "gm_wire_journal"];

/// `_ExportFailure`: a path-free export failure with its cleanup diagnostics. Python keeps the
/// supplemental codes on the exception object only; no reply or record carries them, so
/// [`ExportFailure::into_store_error`] drops them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExportFailure {
    pub code: String,
    pub supplemental_codes: Vec<String>,
}

impl ExportFailure {
    pub fn new(code: impl Into<String>) -> Self {
        Self { code: code.into(), supplemental_codes: Vec::new() }
    }

    /// `with_supplemental(*codes)`: the codes added once each, in order.
    #[must_use]
    pub fn with_supplemental(mut self, code: &str) -> Self {
        if !self.supplemental_codes.iter().any(|known| known == code) {
            self.supplemental_codes.push(code.to_owned());
        }
        self
    }

    /// `str(error)`.
    pub fn message(&self) -> String {
        format!("Portable campaign export failed ({})", self.code)
    }

    /// The `CommandRejected` the owner reports.
    pub fn into_store_error(self) -> StoreError {
        StoreError::Coded(
            CodedError::new(self.code.clone(), self.message()).with_error_type("_ExportFailure"),
        )
    }
}

/// `_as_export_failure(error, default_stage=stage)`: a `CommandRejected` keeps its code; any other
/// error is the stage's own failure.
fn as_export_failure(error: StoreError, stage: &str) -> ExportFailure {
    match error.rejection() {
        Some(coded) if matches!(coded.error_type, None | Some("CommandRejected" | "_ExportFailure")) => {
            ExportFailure::new(coded.code)
        }
        _ => ExportFailure::new(format!("portable_export_{stage}_failed")),
    }
}

/// `_ExportCapture`: one consistent read of a campaign's portable closure.
#[derive(Debug, Clone, PartialEq)]
pub struct ExportCapture {
    pub campaign_id: String,
    pub captured_revision: i64,
    pub source_schema_version: i64,
    pub source_schema_identity_sha256: String,
    pub migration_provenance: Json,
    pub rows: CapturedRows,
    pub authority_bytes: Vec<u8>,
    pub private_bytes: Option<Vec<u8>>,
    pub blob_sha256s: Vec<String>,
}

/// One table's `WHERE` clause and its parameters.
type Predicate = (String, Vec<String>);

/// `_export_relation_predicates(campaign_id)`, in `TABLES_IN_ORDER`.
fn export_relation_predicates(campaign_id: &str) -> Vec<(&'static str, Predicate)> {
    let one = || vec![campaign_id.to_owned()];
    let two = || vec![campaign_id.to_owned(), campaign_id.to_owned()];
    let selected_candidates = "SELECT candidate_id FROM package_activations \
        WHERE campaign_id = ? AND candidate_id IS NOT NULL \
        UNION SELECT package_candidate_id FROM builder_dm_handoffs \
        WHERE campaign_id = ? AND package_candidate_id IS NOT NULL";
    let candidate_predicate = format!("\"candidate_id\" IN ({selected_candidates})");
    let exact_identity = |table: &str, sha_column: &str| -> Predicate {
        let clause = format!(
            "(EXISTS (SELECT 1 FROM campaign_lock_memberships AS export_lock_member \
             JOIN campaign_locks AS export_lock \
             ON export_lock.lock_id = export_lock_member.lock_id \
             WHERE export_lock.campaign_id = ? \
             AND export_lock_member.package_id = \"{table}\".\"package_id\" \
             AND export_lock_member.version = \"{table}\".\"version\" \
             AND export_lock_member.content_sha256 = \"{table}\".\"{sha_column}\") \
             OR EXISTS (SELECT 1 FROM package_candidate_memberships AS export_candidate_member \
             WHERE export_candidate_member.candidate_id IN ({selected_candidates}) \
             AND export_candidate_member.package_id = \"{table}\".\"package_id\" \
             AND export_candidate_member.version = \"{table}\".\"version\" \
             AND export_candidate_member.content_sha256 = \"{table}\".\"{sha_column}\"))"
        );
        (clause, vec![campaign_id.to_owned(), campaign_id.to_owned(), campaign_id.to_owned()])
    };
    TABLES
        .iter()
        .map(|table| {
            let predicate = match table.name {
                "campaign_operations" => {
                    ("\"campaign_id\" = ? AND \"operation_kind\" != 'purge_campaign'".to_owned(), one())
                }
                "campaign_lock_memberships" => (
                    "\"lock_id\" IN (SELECT lock_id FROM campaign_locks WHERE campaign_id = ?)".to_owned(),
                    one(),
                ),
                "agent_session_messages" => (
                    "\"session_id\" IN (SELECT session_id FROM agent_sessions WHERE campaign_id = ?)".to_owned(),
                    one(),
                ),
                "package_candidates" | "package_candidate_memberships" => (candidate_predicate.clone(), two()),
                "package_staging" => (
                    format!(
                        "\"install_id\" IN (SELECT install_id FROM package_candidates WHERE {candidate_predicate})"
                    ),
                    two(),
                ),
                "package_manifests"
                | "package_manifest_files"
                | "package_exact_dependencies"
                | "package_capabilities"
                | "package_tools"
                | "package_prompt_fragments"
                | "package_ui_surfaces"
                | "package_participant_artifacts" => exact_identity(table.name, "content_sha256"),
                "packages" | "package_dependencies" => exact_identity(table.name, "sha256"),
                "package_blobs" => {
                    let (file_identity, parameters) = exact_identity("package_manifest_files", "content_sha256");
                    (
                        format!(
                            "\"blob_sha256\" IN (SELECT \"package_manifest_files\".\"blob_sha256\" \
                             FROM \"package_manifest_files\" WHERE {file_identity})"
                        ),
                        parameters,
                    )
                }
                _ => ("\"campaign_id\" = ?".to_owned(), one()),
            };
            (table.name, predicate)
        })
        .collect()
}

fn query_rows(connection: &Connection, query: &str, parameters: &[String]) -> StoreResult<Vec<Vec<Value>>> {
    let values: Vec<&dyn rusqlite::ToSql> =
        parameters.iter().map(|value| value as &dyn rusqlite::ToSql).collect();
    Ok(named_rows(connection, query, &values)?.1)
}

fn int_value(value: &Value) -> StoreResult<i64> {
    match value {
        Value::Null => Ok(0),
        other => py_int(other).ok_or_else(|| StoreError::Other("invalid literal for int()".into())),
    }
}

/// One SQLite value as Python's `json.dumps` would write it: a `bytes` value is its `TypeError`
/// and a non-finite float its `ValueError` (`allow_nan=False`).
fn json_value(value: &Value) -> StoreResult<Json> {
    Ok(match value {
        Value::Null => Json::Null,
        Value::Integer(number) => Json::from(*number),
        Value::Real(number) if number.is_finite() => Json::from(*number),
        Value::Real(_) => {
            return Err(StoreError::Other("Out of range float values are not JSON compliant".into()))
        }
        Value::Text(text) => Json::String(text.clone()),
        Value::Blob(_) => {
            return Err(StoreError::Other("Object of type bytes is not JSON serializable".into()))
        }
    })
}

/// One row as a JSON object in its columns' order.
fn row_object(columns: &[&str], values: &[Value]) -> StoreResult<JsonMap> {
    columns.iter().zip(values).map(|(column, value)| Ok(((*column).to_owned(), json_value(value)?))).collect()
}

/// The failure mapping of one stage.
fn fail(stage: &'static str) -> impl Fn(StoreError) -> ExportFailure {
    move |error| as_export_failure(error, stage)
}

/// The three bounds `_preflight_export_relations` returns.
struct Preflight {
    member_count: i64,
    single_blob_bytes: i64,
    total_uncompressed_bytes: i64,
}

/// `_preflight_export_relations`: every bound checked from SQL before any row is read whole.
fn preflight_export_relations(
    connection: &Connection,
    predicates: &[(&'static str, Predicate)],
) -> Result<Preflight, ExportFailure> {
    let fail = |code: &str| ExportFailure::new(code);
    let stage = |error: StoreError| as_export_failure(error, "capture_preflight");
    let mut counts: Vec<(&str, i64)> = Vec::new();
    let (mut authority_count, mut authority_bound, mut maximum_row_bound, mut maximum_value) =
        (0i64, 0i64, 0i64, 0i64);
    for table in TABLES {
        let (clause, parameters) =
            &predicates.iter().find(|(name, _)| *name == table.name).expect("every table").1;
        let count_rows = query_rows(
            connection,
            &format!("SELECT COUNT(*) FROM \"{}\" WHERE {clause}", table.name),
            parameters,
        )
        .map_err(stage)?;
        let count = int_value(&count_rows[0][0]).map_err(stage)?;
        if count > limit("single_table_row_count") {
            return Err(fail("portable_export_limit_single_table_row_count"));
        }
        counts.push((table.name, count));
        authority_count += count;
        if authority_count > limit("authority_record_count") {
            return Err(fail("portable_export_limit_authority_record_count"));
        }
        for column in table.columns {
            let rows = query_rows(
                connection,
                &format!(
                    "SELECT MAX(COALESCE(length(CAST(\"{column}\" AS BLOB)), 0)) FROM \"{}\" WHERE {clause}",
                    table.name
                ),
                parameters,
            )
            .map_err(stage)?;
            let bound = int_value(&rows[0][0]).map_err(stage)?;
            if bound > limit("single_text_or_json_value_bytes") {
                return Err(fail("portable_export_limit_single_text_or_json_value_bytes"));
            }
            maximum_value = maximum_value.max(bound);
        }
        let raw_size = table
            .columns
            .iter()
            .map(|column| format!("COALESCE(length(CAST(\"{column}\" AS BLOB)), 0)"))
            .collect::<Vec<_>>()
            .join(" + ");
        let largest = query_rows(
            connection,
            &format!("SELECT MAX({raw_size}) FROM \"{}\" WHERE {clause}", table.name),
            parameters,
        )
        .map_err(stage)?;
        if int_value(&largest[0][0]).map_err(stage)? > limit("single_authority_row_bytes") {
            return Err(fail("portable_export_limit_single_authority_row_bytes"));
        }
        let selected =
            table.columns.iter().map(|column| format!("\"{column}\"")).collect::<Vec<_>>().join(", ");
        for values in query_rows(
            connection,
            &format!("SELECT {selected} FROM \"{}\" WHERE {clause}", table.name),
            parameters,
        )
        .map_err(stage)?
        {
            let row = row_object(table.columns, &values).map_err(stage)?;
            let row_size = canonical_authority_record_size(table.name, &row) as i64;
            if row_size > limit("single_authority_row_bytes") {
                return Err(fail("portable_export_limit_single_authority_row_bytes"));
            }
            maximum_row_bound = maximum_row_bound.max(row_size);
            authority_bound += row_size;
            if authority_bound > limit("authority_jsonl_bytes") {
                return Err(fail("portable_export_limit_authority_jsonl_bytes"));
            }
        }
    }
    let (blob_clause, blob_parameters) =
        &predicates.iter().find(|(name, _)| *name == "package_blobs").expect("blobs").1;
    let ledger = query_rows(
        connection,
        &format!(
            "SELECT COALESCE(MAX(\"byte_count\"), 0), COALESCE(SUM(\"byte_count\"), 0), \
             COALESCE(SUM(CASE WHEN \"byte_count\" < 0 OR \"chunk_count\" < 0 \
             OR \"chunk_count\" != ((\"byte_count\" + 65535) / 65536) THEN 1 ELSE 0 END), 0) \
             FROM \"package_blobs\" WHERE {blob_clause}"
        ),
        blob_parameters,
    )
    .map_err(stage)?;
    let single_blob = int_value(&ledger[0][0]).map_err(stage)?;
    let blob_total = int_value(&ledger[0][1]).map_err(stage)?;
    if int_value(&ledger[0][2]).map_err(stage)? != 0 {
        return Err(fail("portable_export_package_corrupt"));
    }
    let count_of =
        |name: &str| counts.iter().find(|(table, _)| *table == name).map_or(0, |(_, count)| *count);
    let member_count = count_of("package_blobs") + 2;
    let total_bound = authority_bound + limit("manifest_bytes") + blob_total;
    checked_archive_ledger(&[
        ("member_count", member_count),
        ("authority_record_count", authority_count),
        ("single_table_row_count", counts.iter().map(|(_, count)| *count).max().unwrap_or(0)),
        ("manifest_bytes", limit("manifest_bytes")),
        ("authority_jsonl_bytes", authority_bound),
        ("single_authority_row_bytes", maximum_row_bound),
        ("single_text_or_json_value_bytes", maximum_value),
        ("single_blob_bytes", single_blob),
        ("total_uncompressed_bytes", total_bound),
    ])
    .map_err(stage)?;
    if count_of("campaigns") != 1 {
        return Err(fail("command_rejected"));
    }
    Ok(Preflight { member_count, single_blob_bytes: single_blob, total_uncompressed_bytes: total_bound })
}

/// `_read_export_rows`: every table's rows in key order, with stubbed and path-bearing evidence
/// rewritten.
fn read_export_rows(
    connection: &Connection,
    predicates: &[(&'static str, Predicate)],
) -> StoreResult<CapturedRows> {
    let read_failed =
        || StoreError::Coded(ExportFailure::new("portable_export_capture_read_failed").into_coded());
    let mut captured = Vec::new();
    for table in TABLES {
        let (clause, parameters) =
            &predicates.iter().find(|(name, _)| *name == table.name).expect("every table").1;
        let selected =
            table.columns.iter().map(|column| format!("\"{column}\"")).collect::<Vec<_>>().join(", ");
        let order = table.keys.iter().map(|column| format!("\"{column}\"")).collect::<Vec<_>>().join(", ");
        let mut rows = Vec::new();
        for values in query_rows(
            connection,
            &format!("SELECT {selected} FROM \"{}\" WHERE {clause} ORDER BY {order}", table.name),
            parameters,
        )? {
            let mut row = row_object(table.columns, &values)?;
            if table.name == "agent_session_messages"
                && row.get("kind") == Some(&Json::String("evidence".into()))
            {
                let text = match &row["content_json"] {
                    Json::String(text) => text.clone(),
                    other => crate::campaigns::json_str(other),
                };
                let policy = HookPolicy { refuse_duplicates: true, refuse_constants: true };
                let Ok(read) = pyjson::loads_hooked(&text, policy) else { return Err(read_failed()) };
                let mut content = read.value;
                let mut rewritten = false;
                if let Json::Object(object) = &mut content {
                    match object.get("kind") {
                        // `content.get("kind") in frozenset(...)`: an unhashable kind is a `TypeError`.
                        Some(Json::Array(_) | Json::Object(_)) => return Err(read_failed()),
                        Some(Json::String(kind))
                            if EXPORT_STUBBED_EVIDENCE_KINDS.contains(&kind.as_str()) =>
                        {
                            let mut stub = JsonMap::new();
                            stub.insert("kind".into(), Json::String(kind.clone()));
                            stub.insert("omitted_from_export".into(), Json::Bool(true));
                            content = Json::Object(stub);
                            rewritten = true;
                        }
                        _ if object.contains_key("source_locator") => {
                            object.insert("source_locator".into(), Json::Null);
                            rewritten = true;
                        }
                        _ => {}
                    }
                }
                if rewritten {
                    row.insert(
                        "content_json".into(),
                        Json::String(pyjson::dumps(&content, Dump::CANONICAL.ascii(false))),
                    );
                }
            }
            rows.push(row);
        }
        captured.push((table.name, rows));
    }
    Ok(CapturedRows(captured))
}

impl ExportFailure {
    fn into_coded(self) -> CodedError {
        CodedError::new(self.code.clone(), self.message()).with_error_type("_ExportFailure")
    }
}

impl AuthorityStore {
    /// `_export_failure_stage(stage)`, then the stage's own failure for anything it raises.
    fn export_stage(&self, stage: &str) -> StoreResult<()> {
        match &self.faults.export_stage {
            Some(fault) => fault(stage),
            None => Ok(()),
        }
    }

    /// `_run_export_stage(stage)`.
    pub(crate) fn run_export_stage(&self, stage: &str) -> Result<(), ExportFailure> {
        self.export_stage(stage).map_err(|error| match error.rejection() {
            Some(coded) if coded.error_type == Some("_ExportFailure") => ExportFailure::new(coded.code),
            _ => ExportFailure::new(format!("portable_export_{stage}_failed")),
        })
    }

    /// `_assert_export_source_schema`: only exact current authority, without migration.
    fn assert_export_source_schema(connection: &Connection) -> StoreResult<()> {
        let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
        if version != CAMPAIGN_SCHEMA_VERSION
            || schema::schema_identity_of(connection)? != *Known::Effort.identity()
            || schema::trigger_identity_of(connection)? != *schema::expected_trigger_identity()
        {
            return Err(StoreError::Coded(
                ExportFailure::new("portable_export_source_schema_unsupported").into_coded(),
            ));
        }
        Ok(())
    }

    /// `_capture_campaign_export(campaign_id, include_thinking=...)`: one consistent read of the
    /// campaign's portable closure, its authority member and, when asked, its private provider
    /// member.
    pub fn capture_campaign_export(
        &self,
        campaign_id: &str,
        include_thinking: bool,
    ) -> Result<ExportCapture, ExportFailure> {
        let mut stage = "capture_open";
        let mut connection: Option<Connection> = None;
        let outcome = self.capture_stages(campaign_id, include_thinking, &mut stage, &mut connection);
        let mut failure = outcome.as_ref().err().cloned();
        let capture = outcome.ok();
        if let Some(open) = &connection {
            if !open.is_autocommit() {
                let rolled_back = self.run_export_stage("capture_rollback").is_ok()
                    && open.execute_batch("ROLLBACK").is_ok();
                if !rolled_back {
                    failure = failure
                        .map(|failure| failure.with_supplemental("portable_export_capture_rollback_failed"));
                }
            }
        }
        if let Some(open) = connection.take() {
            let closed = self.run_export_stage("capture_close").is_ok() && open.close().is_ok();
            if !closed {
                failure = Some(match failure {
                    None => ExportFailure::new("portable_export_capture_close_failed"),
                    Some(failure) => failure.with_supplemental("portable_export_capture_close_failed"),
                });
            }
        }
        match (failure, capture) {
            (Some(failure), _) => Err(failure),
            (None, Some(capture)) => Ok(capture),
            (None, None) => Err(ExportFailure::new("portable_export_capture_read_failed")),
        }
    }

    fn capture_stages(
        &self,
        campaign_id: &str,
        include_thinking: bool,
        stage: &mut &'static str,
        slot: &mut Option<Connection>,
    ) -> Result<ExportCapture, ExportFailure> {
        self.export_stage(stage).map_err(fail(stage))?;
        let connection = self.connect().map_err(fail(stage))?;
        connection
            .execute_batch("PRAGMA query_only = ON")
            .map_err(|error| as_export_failure(error.into(), stage))?;
        let connection = slot.insert(connection);
        *stage = "capture_begin";
        self.export_stage(stage).map_err(fail(stage))?;
        connection.execute_batch("BEGIN").map_err(|error| as_export_failure(error.into(), stage))?;
        *stage = "capture_preflight";
        self.export_stage(stage).map_err(fail(stage))?;
        Self::assert_export_source_schema(connection).map_err(fail(stage))?;
        let predicates = export_relation_predicates(campaign_id);
        let preflight = preflight_export_relations(connection, &predicates)?;
        let (participant_clause, participant_parameters) =
            &predicates.iter().find(|(name, _)| *name == "participants").expect("participants").1;
        let adapted = query_rows(
            connection,
            &format!("SELECT COUNT(*) FROM \"participants\" WHERE ({participant_clause}) AND adaptation_id IS NOT NULL"),
            participant_parameters,
        )
        .and_then(|rows| int_value(&rows[0][0]))
        .map_err(fail(stage))?;
        if adapted != 0 {
            return Err(ExportFailure::new("portable_export_adaptation_ineligible"));
        }
        *stage = "capture_read";
        self.export_stage(stage).map_err(fail(stage))?;
        let rows = read_export_rows(connection, &predicates).map_err(fail(stage))?;
        *stage = "capture_encode";
        self.export_stage(stage).map_err(fail(stage))?;
        let capture = self
            .encode_capture(connection, campaign_id, include_thinking, rows, &preflight)
            .map_err(fail(stage))?;
        *stage = "capture_commit";
        self.export_stage(stage).map_err(fail(stage))?;
        connection.execute_batch("COMMIT").map_err(|error| as_export_failure(error.into(), stage))?;
        Ok(capture)
    }

    /// The capture's encode stage: the authority member under its limits, the migration
    /// provenance and the private provider member.
    fn encode_capture(
        &self,
        connection: &Connection,
        campaign_id: &str,
        include_thinking: bool,
        rows: CapturedRows,
        preflight: &Preflight,
    ) -> StoreResult<ExportCapture> {
        let limit_failure = |code: &str| StoreError::Coded(ExportFailure::new(code).into_coded());
        let mut output: Vec<u8> = Vec::new();
        let (mut record_count, mut maximum_row, mut maximum_value) = (0i64, 0usize, 0usize);
        for table in TABLES {
            for row in rows.get(table.name) {
                for value in row.values() {
                    if let Json::String(text) = value {
                        maximum_value = maximum_value.max(text.len());
                    }
                }
                let encoded = canonical_authority_record(table.name, row);
                maximum_row = maximum_row.max(encoded.len());
                if maximum_row as i64 > limit("single_authority_row_bytes") {
                    return Err(limit_failure("portable_export_limit_single_authority_row_bytes"));
                }
                if maximum_value as i64 > limit("single_text_or_json_value_bytes") {
                    return Err(limit_failure("portable_export_limit_single_text_or_json_value_bytes"));
                }
                if (output.len() + encoded.len()) as i64 > limit("authority_jsonl_bytes") {
                    return Err(limit_failure("portable_export_limit_authority_jsonl_bytes"));
                }
                output.extend(encoded);
                record_count += 1;
            }
        }
        let maximum_table = TABLES.iter().map(|table| rows.get(table.name).len() as i64).max().unwrap_or(0);
        checked_archive_ledger(&[
            ("authority_record_count", record_count),
            ("single_table_row_count", maximum_table),
            ("authority_jsonl_bytes", output.len() as i64),
            ("single_authority_row_bytes", maximum_row as i64),
            ("single_text_or_json_value_bytes", maximum_value as i64),
            ("single_blob_bytes", preflight.single_blob_bytes),
            ("member_count", preflight.member_count),
            ("total_uncompressed_bytes", preflight.total_uncompressed_bytes),
        ])?;
        let campaign_rows = rows.get("campaigns");
        if campaign_rows.len() != 1 {
            return Err(crate::actions::rejected("Campaign is unavailable", "command_rejected"));
        }
        let captured_revision = match &campaign_rows[0]["revision"] {
            Json::Number(number) => number.as_i64(),
            Json::String(text) => py_int(&Value::Text(text.clone())),
            _ => None,
        }
        .ok_or_else(|| StoreError::Other("invalid literal for int()".into()))?;
        let migrations: Vec<Json> = named_rows(
            connection,
            "SELECT version, applied_name, sql_sha256 FROM schema_migrations ORDER BY version",
            &[],
        )?
        .1
        .iter()
        .map(|row| {
            let mut item = JsonMap::new();
            item.insert("version".into(), Json::from(int_value(&row[0])?));
            item.insert("applied_name".into(), Json::String(py_str(&row[1])));
            item.insert("sql_sha256".into(), Json::String(py_str(&row[2])));
            Ok(Json::Object(item))
        })
        .collect::<StoreResult<_>>()?;
        let extensions: Vec<Json> = named_rows(
            connection,
            "SELECT extension_name, extension_sha256, predecessor_migration_name, predecessor_sql_sha256 \
             FROM authority_schema_extensions ORDER BY extension_name",
            &[],
        )?
        .1
        .iter()
        .map(|row| {
            let mut item = JsonMap::new();
            for (index, key) in campaign_archive::SCHEMA_EXTENSION_FIELDS.iter().enumerate() {
                item.insert((*key).to_owned(), Json::String(py_str(&row[index])));
            }
            Json::Object(item)
        })
        .collect();
        let mut provenance = JsonMap::new();
        provenance.insert("schema_migrations".into(), Json::Array(migrations));
        provenance.insert("schema_extensions".into(), Json::Array(extensions));
        let migration_provenance = Json::Object(provenance);
        campaign_archive::validate_migration_provenance(
            &migration_provenance,
            true,
            &Json::from(campaign_archive::SOURCE_AUTHORITY_SCHEMA_VERSION),
        )?;
        let private_bytes = if include_thinking {
            let fields = crate::private_records::FIELDS
                .iter()
                .map(|name| format!("p.{name}"))
                .collect::<Vec<_>>()
                .join(", ");
            let private_rows = named_rows(
                connection,
                &format!(
                    "SELECT {fields} FROM provider_private_records p JOIN agent_sessions s \
                     ON s.session_id = p.agent_session_id WHERE s.campaign_id = ? ORDER BY p.rowid"
                ),
                &[&campaign_id],
            )?
            .1;
            let session_ids: HashSet<String> = rows
                .get("agent_sessions")
                .iter()
                .map(|row| crate::campaigns::json_str(&row["session_id"]))
                .collect();
            Some(crate::private_records::encode(&private_rows, &session_ids)?)
        } else {
            None
        };
        let blob_sha256s = rows
            .get("package_blobs")
            .iter()
            .map(|row| crate::campaigns::json_str(&row["blob_sha256"]))
            .collect();
        Ok(ExportCapture {
            campaign_id: campaign_id.to_owned(),
            captured_revision,
            source_schema_version: campaign_archive::SOURCE_AUTHORITY_SCHEMA_VERSION,
            source_schema_identity_sha256: campaign_archive::source_authority_schema_claim_sha256(
                campaign_archive::SOURCE_AUTHORITY_SCHEMA_VERSION,
            ),
            migration_provenance,
            rows,
            authority_bytes: output,
            private_bytes,
            blob_sha256s,
        })
    }
}
