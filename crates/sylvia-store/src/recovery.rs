//! Durable campaign recovery receipts, from `sqlite_recovery.py`: `verify_recovery_receipts`,
//! which the stored-authority verifier runs at startup and on backups, with `receipt_result`
//! and `result_json`.
//!
//! Python turns every failure inside the verifier's `try` (SQLite errors, `TypeError`,
//! `ValueError`, `KeyError`, `JSONDecodeError` and `CommandRejected`) into one
//! `SchemaIdentityError`, "Authority stored recovery receipt integrity is invalid". Only
//! `receipt_result`'s own `SchemaIdentityError`, "Recovery receipt result is corrupt", passes
//! through it, as here.
//!
//! Declared difference: an infinite number in the stored result's revision or byte count is
//! "corrupt" here. Python's `int(inf)` raises `OverflowError`, which neither `except` catches, so
//! its startup reports only "Authority stored integrity could not be verified".

use crate::campaign_archive::{
    self, FORMAT_ID, FORMAT_VERSION, PRODUCT_GENERATION, PRODUCT_ID, SOURCE_AUTHORITY_SCHEMA_VERSIONS,
};
use crate::campaigns::{named_rows, Named};
use crate::error::{schema_identity, StoreError, StoreResult};
use crate::pytext::{py_int, py_str};
use rusqlite::types::Value;
use rusqlite::Connection;
use std::collections::HashSet;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{canonical_payload_is_safe, validate_authority_identity};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::py_eq;

/// `CampaignRecoveryResult`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CampaignRecoveryResult {
    pub campaign_id: String,
    pub captured_revision: i64,
    pub archive_sha256: String,
    pub byte_count: i64,
}

/// The semantic payload's members.
const PAYLOAD_FIELDS: [&str; 13] = [
    "operation_id",
    "operation_kind",
    "campaign_id",
    "captured_revision",
    "archive_sha256",
    "archive_byte_count",
    "format_id",
    "format_version",
    "product_id",
    "product_generation",
    "source_authority_schema_version",
    "source_authority_schema_identity_sha256",
    "migration_provenance",
];

/// `result_json(result)`: the public result in its field order, compact, not ASCII-escaped.
pub fn result_json(result: &CampaignRecoveryResult) -> String {
    let mut value = JsonMap::new();
    value.insert("campaign_id".into(), result.campaign_id.as_str().into());
    value.insert("captured_revision".into(), result.captured_revision.into());
    value.insert("archive_sha256".into(), result.archive_sha256.as_str().into());
    value.insert("byte_count".into(), result.byte_count.into());
    pyjson::dumps(&Json::Object(value), Dump::CANONICAL.sorted(false))
}

/// `str(value)` of a parsed JSON value.
fn json_text(value: &Json) -> String {
    crate::campaigns::json_str(value)
}

/// `int(value)` of a parsed JSON value: an integer, a bool, a finite float truncated, or a
/// string of digits; anything else is Python's `TypeError` or `ValueError`.
fn json_int(value: &Json) -> Option<i64> {
    match value {
        Json::Bool(flag) => Some(i64::from(*flag)),
        Json::Number(number) => number.as_i64().or_else(|| {
            number
                .as_f64()
                .filter(|float| float.is_finite())
                .map(f64::trunc)
                .filter(|float| float.abs() < 9.2e18)
                .map(|float| float as i64)
        }),
        Json::String(text) => py_int(&Value::Text(text.clone())),
        _ => None,
    }
}

/// `receipt_result(row, ...)`: the stored result, after the semantic identity is compared.
/// The verifier passes the row's own payload, so only a corrupt result can fail here.
pub(crate) fn receipt_result(
    row: &Named<'_>,
    payload_json: &str,
    payload_sha256: &str,
) -> StoreResult<CampaignRecoveryResult> {
    if row.text("semantic_payload_json")? != payload_json
        || row.text("semantic_payload_sha256")? != payload_sha256
    {
        return Err(StoreError::Coded(crate::error::authority_coded(
            "IdempotencyConflict",
            "Recovery identity was reused with different archive semantics",
        )));
    }
    let corrupt = || schema_identity("Recovery receipt result is corrupt");
    // Read as Python's plain `json.loads` reads: `NaN` and `Infinity` are accepted (held as
    // `null` here, which `int()` refuses as Python refuses `nan`). A value only Python can hold
    // (an integer past 64 bits) can never match the receipt's columns: the verifier's refusal.
    let value = match pyjson::loads_hooked(&row.text("result_json")?, pyjson::HookPolicy::default()) {
        Ok(read) => read.value,
        Err(pyjson::HookFailure::Unsupported(error)) => return Err(StoreError::Other(error.message)),
        Err(_) => return Err(corrupt()),
    };
    let field = |key: &str| -> StoreResult<&Json> {
        match &value {
            Json::Object(object) => object.get(key).ok_or_else(corrupt),
            _ => Err(corrupt()),
        }
    };
    Ok(CampaignRecoveryResult {
        campaign_id: json_text(field("campaign_id")?),
        captured_revision: json_int(field("captured_revision")?).ok_or_else(corrupt)?,
        archive_sha256: json_text(field("archive_sha256")?),
        byte_count: json_int(field("byte_count")?).ok_or_else(corrupt)?,
    })
}

/// A failure inside the verifier's `try`.
enum Refusal {
    /// Anything Python's `except` catches: the one integrity refusal.
    Invalid,
    /// `receipt_result`'s `SchemaIdentityError`, which passes through.
    Escapes(StoreError),
}

fn identity(value: &Value, label: &str, sha256: bool) -> Result<String, Refusal> {
    // A non-string is Python's `TypeError`; either way, refused.
    let Value::Text(text) = value else { return Err(Refusal::Invalid) };
    validate_authority_identity(text, label, sha256).map_err(|_| Refusal::Invalid)?;
    Ok(text.clone())
}

fn int_of(value: &Value) -> Result<i64, Refusal> {
    py_int(value).ok_or(Refusal::Invalid)
}

/// `type(value) is int`.
fn is_int(value: &Json) -> bool {
    matches!(value, Json::Number(number) if number.is_i64() || number.is_u64())
}

fn verify_receipt(connection: &Connection, row: &Named<'_>) -> Result<(), Refusal> {
    let get = |column: &str| row.get(column).map_err(|_| Refusal::Invalid);
    let recovery_id = identity(get("recovery_id")?, "Recovery identity", false)?;
    let campaign_id = identity(get("campaign_id")?, "Recovery campaign identity", false)?;
    let archive_sha256 = identity(get("archive_sha256")?, "Recovery archive SHA-256", true)?;
    let payload_json = py_str(get("semantic_payload_json")?);
    let payload_sha256 = identity(get("semantic_payload_sha256")?, "Recovery semantic SHA-256", true)?;
    let Ok(Json::Object(payload)) = pyjson::loads(&payload_json) else { return Err(Refusal::Invalid) };
    let as_json = Json::Object(payload.clone());
    let keys: HashSet<&str> = payload.keys().map(String::as_str).collect();
    if !canonical_payload_is_safe(&as_json)
        || canonical_json_bytes(&as_json) != payload_json.as_bytes()
        || sha256_hex(payload_json.as_bytes()) != payload_sha256
        || keys != PAYLOAD_FIELDS.into_iter().collect::<HashSet<_>>()
    {
        return Err(Refusal::Invalid);
    }
    let member = |key: &str| &payload[key];
    let text = |value: &str| Json::String(value.to_owned());
    let integers =
        ["captured_revision", "archive_byte_count", "format_version", "source_authority_schema_version"];
    let invalid = !py_eq(member("operation_id"), &text(&recovery_id))
        || !py_eq(member("operation_kind"), &text("recover_campaign"))
        || !py_eq(member("campaign_id"), &text(&campaign_id))
        || integers.iter().any(|key| !is_int(member(key)))
        || !py_eq(member("archive_sha256"), &text(&archive_sha256))
        || !py_eq(member("archive_byte_count"), &Json::from(int_of(get("archive_byte_count")?)?))
        || !py_eq(member("captured_revision"), &Json::from(int_of(get("captured_revision")?)?))
        || !py_eq(member("format_id"), &text(FORMAT_ID))
        || !py_eq(member("format_version"), &Json::from(FORMAT_VERSION))
        || !py_eq(member("product_id"), &text(PRODUCT_ID))
        || !py_eq(member("product_generation"), &text(PRODUCT_GENERATION));
    if invalid {
        return Err(Refusal::Invalid);
    }
    let version = member("source_authority_schema_version").as_i64();
    let Some(version) = version.filter(|version| SOURCE_AUTHORITY_SCHEMA_VERSIONS.contains(version)) else {
        return Err(Refusal::Invalid);
    };
    if !py_eq(
        member("source_authority_schema_identity_sha256"),
        &text(&campaign_archive::source_authority_schema_claim_sha256(version)),
    ) {
        return Err(Refusal::Invalid);
    }
    campaign_archive::validate_migration_provenance(
        member("migration_provenance"),
        false,
        member("source_authority_schema_version"),
    )
    .map_err(|_| Refusal::Invalid)?;
    let result = receipt_result(row, &payload_json, &payload_sha256).map_err(|error| match error {
        error @ StoreError::Coded(_) if error.is_schema_identity() => Refusal::Escapes(error),
        _ => Refusal::Invalid,
    })?;
    if result_json(&result) != py_str(get("result_json")?)
        || result.campaign_id != campaign_id
        || result.captured_revision != int_of(get("captured_revision")?)?
        || result.archive_sha256 != archive_sha256
        || result.byte_count != int_of(get("archive_byte_count")?)?
        || result.captured_revision < 0
        || result.byte_count < 0
    {
        return Err(Refusal::Invalid);
    }
    let campaign =
        named_rows(connection, "SELECT revision FROM campaigns WHERE campaign_id = ?", &[&campaign_id])
            .map_err(|_| Refusal::Invalid)?
            .1;
    match campaign.first() {
        Some(row) if int_of(&row[0])? >= result.captured_revision => Ok(()),
        _ => Err(Refusal::Invalid),
    }
}

/// `verify_recovery_receipts`: every durable recovery receipt reconciled with its campaign.
pub fn verify_recovery_receipts(connection: &Connection) -> StoreResult<()> {
    let tables = named_rows(connection, "SELECT name FROM sqlite_master WHERE type = 'table'", &[])?.1;
    if !tables.iter().any(|row| py_str(&row[0]) == "campaign_recovery_receipts") {
        return Ok(());
    }
    let invalid = || schema_identity("Authority stored recovery receipt integrity is invalid");
    let (columns, rows) =
        named_rows(connection, "SELECT * FROM campaign_recovery_receipts ORDER BY recovery_id", &[])
            .map_err(|_| invalid())?;
    for values in rows {
        match verify_receipt(connection, &Named { columns: &columns, values }) {
            Ok(()) => {}
            Err(Refusal::Invalid) => return Err(invalid()),
            Err(Refusal::Escapes(error)) => return Err(error),
        }
    }
    Ok(())
}
