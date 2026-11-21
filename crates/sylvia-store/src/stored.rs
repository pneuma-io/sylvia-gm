//! `_verify_stored_authority`: the stored-content verifier Python runs at startup and on
//! backups, before `verify_package_authority`.
//!
//! Python: `SQLiteAuthorityStore._verify_stored_authority` with `_verify_stored_identities`,
//! `_lock_representation`, `_read_lock` and `sqlite_state_ownership.verify_authority`
//! (`_historical_writers`).
//!
//! Parts 1 and 2. In Python's order it runs: SQLite's `integrity_check` and `foreign_key_check`,
//! the stored identities, the session graph (`crate::stored_sessions`), the state-ownership verifier (from schema 6), the `packages` rows, and
//! each campaign's lock binding. Still to come, each only making this less strict until it
//! lands: `sqlite_recovery.verify_recovery_receipts` (with
//! the archive family, which owns `campaign_archive.py`), and `_verify_campaign_authority` and
//! `_verify_construction_authority` (part 3).
//!
//! Only the failures Python raises itself become its `SchemaIdentityError`; anything else
//! escapes as it does in Python, and startup words it as `_migrate` does
//! ([`crate::StoreError::at_startup`]).

use crate::actions::{current_package_ids, dump, load_json};
use crate::campaigns::{current_lock_record, named_rows, Named};
use crate::error::{schema_identity, StoreError, StoreResult};
use crate::pytext::{py_int, py_str};
use rusqlite::types::Value;
use rusqlite::{Connection, OptionalExtension};
use sylvia_base::Json;
use sylvia_domain::authority::{
    validate_authority_identity, PackageDependency, PackageLock, PackageLockEntry, PackageRole,
};
use sylvia_domain::state_ownership::owner_matches_package_lock;

const CAMPAIGN_RECORDS_INVALID: &str = "Saved campaign records failed an integrity check";

/// `_UNBOUND_CAMPAIGN_LOCK_HASH`.
const UNBOUND_CAMPAIGN_LOCK_HASH: &str = "unbound";

fn invalid() -> StoreError {
    schema_identity(CAMPAIGN_RECORDS_INVALID)
}

fn int(value: &Value) -> StoreResult<i64> {
    py_int(value).ok_or_else(|| crate::error::value_error("invalid literal for int() with base 10"))
}

fn tables(connection: &Connection) -> StoreResult<Vec<String>> {
    Ok(named_rows(connection, "SELECT name FROM sqlite_master WHERE type = 'table'", &[])?
        .1
        .iter()
        .map(|row| py_str(&row[0]))
        .collect())
}

fn exists(connection: &Connection, query: &str, value: &str) -> StoreResult<bool> {
    Ok(connection.query_row(query, [value], |_| Ok(())).optional()?.is_some())
}

/// `validate_authority_identity(value, ...)` of a stored SQLite value: anything but text fails,
/// as Python's check of a non-`str` does.
fn admits(value: &Value, sha256: bool) -> bool {
    match value {
        Value::Text(text) => validate_authority_identity(text, "Stored authority identity", sha256).is_ok(),
        _ => false,
    }
}

/// `_verify_stored_identities`: re-admit every string-backed canonical identity.
fn verify_stored_identities(connection: &Connection) -> StoreResult<()> {
    let mut checks: Vec<(&str, Vec<(&str, bool)>)> = vec![
        ("SELECT * FROM packages", vec![("package_id", false), ("version", false), ("sha256", true)]),
        (
            "SELECT * FROM package_dependencies",
            vec![
                ("package_id", false),
                ("version", false),
                ("sha256", true),
                ("dependency_id", false),
                ("dependency_version", false),
                ("dependency_sha256", true),
                ("dependency_kind", false),
                ("dependency_namespace", false),
            ],
        ),
        ("SELECT * FROM campaigns", vec![("campaign_id", false), ("display_name", false)]),
        (
            "SELECT * FROM campaign_package_memberships",
            vec![
                ("campaign_id", false),
                ("package_id", false),
                ("version", false),
                ("sha256", true),
                ("role", false),
            ],
        ),
        (
            "SELECT * FROM participants",
            vec![
                ("campaign_id", false),
                ("participant_id", false),
                ("kind", false),
                ("package_id", false),
                ("package_version", false),
            ],
        ),
        (
            "SELECT * FROM campaign_state",
            vec![("campaign_id", false), ("namespace", false), ("state_key", false)],
        ),
        ("SELECT * FROM turns", vec![("campaign_id", false), ("turn_id", false), ("status", false)]),
        (
            "SELECT * FROM actions",
            vec![
                ("campaign_id", false),
                ("action_id", false),
                ("turn_id", false),
                ("writer", false),
                ("package_id", false),
                ("package_version", false),
                ("package_sha256", true),
                ("tool_name", false),
                ("status", false),
                ("payload_hash", true),
            ],
        ),
        (
            "SELECT * FROM canonical_events",
            vec![("campaign_id", false), ("event_type", false), ("turn_id", false), ("visibility", false)],
        ),
        (
            "SELECT * FROM narration_interruptions",
            vec![
                ("campaign_id", false),
                ("interruption_id", false),
                ("turn_id", false),
                ("failed_attempt_id", false),
                ("status", false),
            ],
        ),
        (
            "SELECT * FROM mutations",
            vec![("campaign_id", false), ("action_id", false), ("namespace", false), ("state_key", false)],
        ),
        ("SELECT * FROM consequences", vec![("campaign_id", false), ("action_id", false)]),
        (
            "SELECT * FROM idempotency_records",
            vec![
                ("campaign_id", false),
                ("idempotency_key", false),
                ("payload_hash", true),
                ("action_id", false),
                ("status", false),
            ],
        ),
        ("SELECT * FROM campaign_revisions", vec![("campaign_id", false)]),
    ];
    let present = tables(connection)?;
    let has_construction = present.iter().any(|name| name == "campaign_operations");
    let has_continuity = present.iter().any(|name| name == "campaign_continuity_records");
    if has_construction {
        checks.extend([
            (
                "SELECT * FROM authority_schema_extensions",
                vec![
                    ("extension_name", false),
                    ("extension_sha256", true),
                    ("predecessor_migration_name", false),
                    ("predecessor_sql_sha256", true),
                ],
            ),
            (
                "SELECT * FROM campaign_operations",
                vec![
                    ("operation_id", false),
                    ("operation_kind", false),
                    ("campaign_id", false),
                    ("semantic_payload_sha256", true),
                ],
            ),
            (
                "SELECT * FROM campaign_constructions",
                vec![
                    ("campaign_id", false),
                    ("confirmation_operation_id", false),
                    ("authority_turn_id", false),
                    ("authority_lock_id", false),
                ],
            ),
        ]);
    }
    if has_continuity {
        checks.push((
            "SELECT * FROM campaign_continuity_records",
            vec![
                ("campaign_id", false),
                ("entry_id", false),
                ("record_id", false),
                ("kind", false),
                ("status", false),
            ],
        ));
    }
    for (query, columns) in &checks {
        let (names, rows) = named_rows(connection, query, &[])?;
        for values in rows {
            let row = Named { columns: &names, values };
            for (column, sha256) in columns {
                // A missing column is Python's `KeyError`, caught as a refusal like the rest.
                if !row.has(column) || !admits(row.get(column)?, *sha256) {
                    return Err(invalid());
                }
            }
        }
    }
    let mut optional: Vec<(&str, bool)> = vec![
        ("SELECT action_id AS value FROM campaign_revisions", false),
        ("SELECT activation_id AS value FROM campaign_revisions", false),
        ("SELECT action_id AS value FROM canonical_events", false),
        ("SELECT attempt_id AS value FROM canonical_events", false),
        ("SELECT last_action_id AS value FROM narration_interruptions", false),
        ("SELECT resolving_retry_id AS value FROM narration_interruptions", false),
    ];
    if has_construction {
        optional.extend([
            ("SELECT basis_turn_id AS value FROM campaign_operations", false),
            ("SELECT basis_lock_id AS value FROM campaign_operations", false),
            ("SELECT construction_operation_id AS value FROM campaign_revisions", false),
            ("SELECT lane AS value FROM campaign_constructions", false),
            ("SELECT begin_operation_id AS value FROM campaign_constructions", false),
            ("SELECT completion_operation_id AS value FROM campaign_constructions", false),
            ("SELECT canonical_representation_sha256 AS value FROM campaign_constructions", true),
        ]);
    }
    if has_continuity {
        optional.extend([
            ("SELECT continuity_operation_id AS value FROM campaign_revisions", false),
            ("SELECT operation_id AS value FROM campaign_continuity_records", false),
        ]);
    }
    for (query, sha256) in optional {
        for row in named_rows(connection, query, &[])?.1 {
            if row[0] != Value::Null && !admits(&row[0], sha256) {
                return Err(invalid());
            }
        }
    }
    Ok(())
}

/// `sqlite_state_ownership.verify_authority`: ownership evidence that is complete, canonical
/// and consistent with the current lock and the mutation history.
fn verify_state_ownership(connection: &Connection) -> StoreResult<()> {
    if &crate::schema::trigger_identity_of(connection)? != crate::schema::expected_trigger_identity() {
        return Err(schema_identity("Campaign state namespace ownership triggers are invalid"));
    }
    let orphan = connection
        .query_row(
            "SELECT 1 FROM campaign_state AS state \
             LEFT JOIN campaign_state_namespace_owners AS owner \
             ON owner.campaign_id = state.campaign_id AND owner.namespace = state.namespace \
             WHERE owner.campaign_id IS NULL LIMIT 1",
            [],
            |_| Ok(()),
        )
        .optional()?;
    if orphan.is_some() {
        return Err(schema_identity("Campaign state namespace ownership is incomplete"));
    }
    let stateless = connection
        .query_row(
            "SELECT 1 FROM campaign_state_namespace_owners AS owner \
             LEFT JOIN campaign_state AS state ON state.campaign_id = owner.campaign_id \
             AND state.namespace = owner.namespace WHERE state.campaign_id IS NULL LIMIT 1",
            [],
            |_| Ok(()),
        )
        .optional()?;
    if stateless.is_some() {
        return Err(schema_identity("Campaign state namespace ownership is not canonical"));
    }
    let owners = named_rows(
        connection,
        "SELECT campaign_id, namespace, owner_package_id \
         FROM campaign_state_namespace_owners ORDER BY campaign_id, namespace",
        &[],
    )?
    .1;
    for row in owners {
        let (campaign_id, namespace) = (py_str(&row[0]), py_str(&row[1]));
        let owner = match &row[2] {
            Value::Null => None,
            value => Some(py_str(value)),
        };
        let package_ids = current_package_ids(connection, &campaign_id)?;
        if !owner_matches_package_lock(package_ids.iter().map(String::as_str), &namespace, owner.as_deref()) {
            return Err(schema_identity("Campaign state namespace owner conflicts with current lock"));
        }
        // `_historical_writers`.
        let writers = named_rows(
            connection,
            "SELECT DISTINCT action.package_id FROM mutations AS mutation \
             JOIN actions AS action ON action.campaign_id = mutation.campaign_id \
             AND action.action_id = mutation.action_id \
             WHERE mutation.campaign_id = ? AND mutation.namespace = ? \
             ORDER BY action.package_id",
            &[&campaign_id, &namespace],
        )?
        .1;
        if writers.iter().any(|writer| Some(py_str(&writer[0])) != owner) {
            return Err(schema_identity("Campaign state namespace owner conflicts with mutation history"));
        }
    }
    Ok(())
}

/// `_lock_representation`.
fn lock_representation(package_lock: &PackageLock) -> String {
    let entries: Vec<Json> = package_lock
        .entries()
        .iter()
        .map(|entry| {
            let mut item = sylvia_base::JsonMap::new();
            item.insert("id".into(), entry.package_id().into());
            item.insert("version".into(), entry.version().into());
            item.insert("sha256".into(), entry.sha256().into());
            item.insert("role".into(), entry.role().as_str().into());
            item.insert("load_order".into(), entry.load_order().into());
            Json::Object(item)
        })
        .collect();
    dump(&Json::Array(entries))
}

/// `_read_lock`: an unbound draft has the empty lock; any other campaign its current one.
fn read_lock(connection: &Connection, campaign_id: &str) -> StoreResult<PackageLock> {
    let row: Option<(Value, Value)> = connection
        .query_row(
            "SELECT current_lock_id, lock_hash FROM campaigns WHERE campaign_id = ?",
            [campaign_id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()?;
    if let Some((Value::Null, hash)) = &row {
        if py_str(hash) == UNBOUND_CAMPAIGN_LOCK_HASH {
            return Ok(PackageLock::new(Vec::new())?);
        }
    }
    Ok(current_lock_record(connection, campaign_id)?.package_lock)
}

/// `_verify_stored_authority`, part 1 (see the module documentation).
pub fn verify_stored_authority(connection: &Connection) -> StoreResult<()> {
    let integrity: Vec<String> = named_rows(connection, "PRAGMA integrity_check", &[])?
        .1
        .iter()
        .map(|row| sylvia_base::pystr::casefold(&py_str(&row[0])))
        .collect();
    if integrity != ["ok"] {
        return Err(invalid());
    }
    if connection.query_row("PRAGMA foreign_key_check", [], |_| Ok(())).optional()?.is_some() {
        return Err(invalid());
    }
    // Its refusals are its own; a SQLite error escapes, as Python catches only `KeyError`,
    // `TypeError` and `ValueError` there.
    verify_stored_identities(connection)?;
    crate::stored_sessions::verify_session_identities(connection)?;
    // `sqlite_recovery.verify_recovery_receipts` runs here in Python; it comes with the archive
    // family (see the module documentation).
    let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    if matches!(version, 6 | 10 | 11 | 12) {
        verify_state_ownership(connection)?;
    }
    let (columns, packages) = named_rows(
        connection,
        "SELECT package_id, version, sha256, capabilities_json FROM packages \
         ORDER BY package_id, version, sha256",
        &[],
    )?;
    for values in packages {
        let package = Named { columns: &columns, values };
        let capabilities = match load_json(&package.text("capabilities_json")?)? {
            Json::Array(items) if items.iter().all(Json::is_string) => {
                items.iter().map(|item| item.as_str().unwrap_or_default().to_owned()).collect::<Vec<_>>()
            }
            _ => return Err(invalid()),
        };
        let (dependency_columns, dependency_rows) = named_rows(
            connection,
            "SELECT dependency_order, dependency_id, dependency_version, \
             dependency_sha256, dependency_kind, dependency_namespace \
             FROM package_dependencies \
             WHERE package_id = ? AND version = ? AND sha256 = ? \
             ORDER BY dependency_order",
            &[package.get("package_id")?, package.get("version")?, package.get("sha256")?],
        )?;
        let dependency_rows: Vec<Named<'_>> = dependency_rows
            .into_iter()
            .map(|values| Named { columns: &dependency_columns, values })
            .collect();
        for (order, row) in dependency_rows.iter().enumerate() {
            if int(row.get("dependency_order")?)? != order as i64 {
                return Err(invalid());
            }
        }
        let dependencies = dependency_rows
            .iter()
            .map(|row| {
                Ok(PackageDependency::new(
                    row.text("dependency_id")?,
                    row.text("dependency_version")?,
                    row.text("dependency_sha256")?,
                    row.text("dependency_kind")?,
                    row.text("dependency_namespace")?,
                )?)
            })
            .collect::<StoreResult<Vec<_>>>()?;
        PackageLockEntry::new(
            package.text("package_id")?,
            package.text("version")?,
            package.text("sha256")?,
            PackageRole::Dependency,
            0,
            dependencies,
            capabilities,
        )?;
    }
    let (columns, campaigns) = named_rows(
        connection,
        "SELECT campaign_id, lifecycle, revision, writer, lock_hash, current_lock_id \
         FROM campaigns ORDER BY campaign_id",
        &[],
    )?;
    for values in campaigns {
        let campaign = Named { columns: &columns, values };
        let campaign_id = campaign.text("campaign_id")?;
        if campaign.get("current_lock_id")? == &Value::Null {
            let draft = || -> StoreResult<bool> {
                Ok(campaign.text("lock_hash")? == UNBOUND_CAMPAIGN_LOCK_HASH
                    && crate::packages::has_only_draft_lifecycle_history(
                        connection,
                        &campaign_id,
                        int(campaign.get("revision")?)?,
                    )?
                    && matches!(
                        (campaign.text("lifecycle")?.as_str(), campaign.text("writer")?.as_str()),
                        ("active", "builder") | ("archived", "none") | ("tombstoned", "none")
                    )
                    && !exists(
                        connection,
                        "SELECT 1 FROM turns WHERE campaign_id = ? LIMIT 1",
                        &campaign_id,
                    )?
                    && !exists(
                        connection,
                        "SELECT 1 FROM campaign_locks WHERE campaign_id = ? LIMIT 1",
                        &campaign_id,
                    )?
                    && exists(
                        connection,
                        "SELECT 1 FROM agent_session_messages AS message \
                         JOIN agent_sessions AS session ON session.session_id = message.session_id \
                         WHERE session.campaign_id = ? AND message.kind = 'evidence' \
                         AND message.content_json LIKE '%campaign_interview_state%' LIMIT 1",
                        &campaign_id,
                    )?)
            };
            if !draft()? {
                return Err(invalid());
            }
        } else {
            let package_lock = read_lock(connection, &campaign_id)?;
            if lock_representation(&package_lock) != campaign.text("lock_hash")? {
                return Err(invalid());
            }
        }
        // `_verify_campaign_authority` runs here in Python (part 3).
    }
    // `_verify_construction_authority` runs here in Python when `campaign_operations` exists
    // (part 3).
    Ok(())
}
