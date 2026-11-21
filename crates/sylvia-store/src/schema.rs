//! Schema versions, migration identities, the migration steps and the exact schema identity.
//!
//! Python: the migration constants and helpers in `sqlite_authority.py`, `sqlite_packages.py`,
//! `sqlite_session.py`, `sqlite_campaign.py`, `sqlite_recovery.py`, `sqlite_state_ownership.py`,
//! `sqlite_mechanics_choice.py`, `sqlite_continuity.py`, `sqlite_draft_lifecycle.py`,
//! `sqlite_reasoning.py` and `sqlite_effort.py`. Each step runs the same statements in the same
//! order, so SQLite stores the same `sqlite_master` text.

use crate::error::{schema_identity, StoreResult};
use crate::pytext::{normalized_casefold, py_strip};
use crate::sql;
use rusqlite::types::Value;
use rusqlite::{params, Connection, OptionalExtension};
use std::sync::OnceLock;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{canonical_bytes, Dump};
use sylvia_base::{Json, JsonMap};

/// `_SCHEMA_VERSION`.
pub const SCHEMA_VERSION: i64 = 3;
/// `_SESSION_SCHEMA_VERSION`.
pub const SESSION_SCHEMA_VERSION: i64 = 4;
/// `_PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION`.
pub const PRE_OWNERSHIP_CAMPAIGN_SCHEMA_VERSION: i64 = 5;
/// `_STATE_OWNERSHIP_SCHEMA_VERSION`.
pub const STATE_OWNERSHIP_SCHEMA_VERSION: i64 = 6;
/// `_PRE_BUILDER_CHAT_SCHEMA_VERSION`.
pub const PRE_BUILDER_CHAT_SCHEMA_VERSION: i64 = 7;
/// `_PRE_CONTINUITY_SCHEMA_VERSION`.
pub const PRE_CONTINUITY_SCHEMA_VERSION: i64 = 8;
/// `_PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION`.
pub const PRE_DRAFT_LIFECYCLE_SCHEMA_VERSION: i64 = 9;
/// `_PRE_REASONING_SCHEMA_VERSION`.
pub const PRE_REASONING_SCHEMA_VERSION: i64 = 10;
/// `_PRE_EFFORT_SCHEMA_VERSION`.
pub const PRE_EFFORT_SCHEMA_VERSION: i64 = 11;
/// `_CAMPAIGN_SCHEMA_VERSION`: the current schema.
pub const CAMPAIGN_SCHEMA_VERSION: i64 = 12;

/// `_MIGRATION_1_NAME`.
pub const MIGRATION_1_NAME: &str = "f1_neutral_authority";
/// `_MIGRATION_2_NAME`.
pub const MIGRATION_2_NAME: &str = "f2_provisional_event_protocol";
/// `sqlite_packages.MIGRATION_3_NAME`.
pub const MIGRATION_3_NAME: &str = "f3_sqlite_package_library";
/// `sqlite_session.MIGRATION_4_NAME`.
pub const MIGRATION_4_NAME: &str = "f4_builder_dm_sessions";
/// `sqlite_campaign.MIGRATION_5_NAME`.
pub const MIGRATION_5_NAME: &str = "f5_campaign_lifecycle_recovery";
/// `sqlite_state_ownership.MIGRATION_6_NAME`.
pub const MIGRATION_6_NAME: &str = "f7_relational_state_namespace_ownership";
/// `sqlite_mechanics_choice.MIGRATION_7_NAME`.
pub const MIGRATION_7_NAME: &str = "mechanics_choice_reservations";
/// `sqlite_session.MIGRATION_8_NAME`.
pub const MIGRATION_8_NAME: &str = "standalone_builder_chats";
/// `sqlite_continuity.MIGRATION_9_NAME`.
pub const MIGRATION_9_NAME: &str = "campaign_state_continuity";
/// `sqlite_draft_lifecycle.MIGRATION_10_NAME`.
pub const MIGRATION_10_NAME: &str = "unbound_draft_lifecycle";
/// `sqlite_reasoning.MIGRATION_11_NAME`.
pub const MIGRATION_11_NAME: &str = "private_provider_records";
/// `sqlite_effort.MIGRATION_12_NAME`.
pub const MIGRATION_12_NAME: &str = "session_reasoning_preferences";
/// `sqlite_campaign.PREDECESSOR_MIGRATION_5_SHA256`: a literal in Python, not computed.
pub const PREDECESSOR_MIGRATION_5_SHA256: &str =
    "49d01f74743bd178ab23848c4493dda234bf51a26ad7115236c2356714c48802";
/// `sqlite_campaign.CAMPAIGN_IDENTITY_EXTENSION_NAME`.
pub const CAMPAIGN_IDENTITY_EXTENSION_NAME: &str = "f5_probe_01_stable_campaign_identity";
/// `sqlite_campaign.LIFECYCLE_EXTENSION_NAME`.
pub const LIFECYCLE_EXTENSION_NAME: &str = "f5_reversible_lifecycle_v3";
/// `sqlite_recovery.RECOVERY_EXTENSION_NAME`.
pub const RECOVERY_EXTENSION_NAME: &str = "f5_portable_campaign_recovery_v1";
/// `sqlite_campaign.PURGE_EXTENSION_NAME`.
pub const PURGE_EXTENSION_NAME: &str = "f5_campaign_purge_v1";

/// Every SHA-256 the schema identity names, computed as each Python constant is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Digests {
    /// `sql_sha256` of migrations 1 to 12, by version (index 0 is migration 1).
    pub migrations: [String; 12],
    /// `sqlite_campaign.CAMPAIGN_IDENTITY_DELTA_SHA256`.
    pub campaign_identity_delta: String,
    /// `sqlite_campaign.LIFECYCLE_EXTENSION_SHA256`.
    pub lifecycle_extension: String,
    /// `sqlite_recovery.RECOVERY_EXTENSION_SHA256`.
    pub recovery_extension: String,
    /// `sqlite_campaign.PURGE_EXTENSION_SHA256`.
    pub purge_extension: String,
}

/// The digests, computed once.
pub fn digests() -> &'static Digests {
    static DIGESTS: OnceLock<Digests> = OnceLock::new();
    DIGESTS.get_or_init(|| {
        let stripped = |text: &str| sha256_hex(py_strip(text).as_bytes());
        let raw = |text: &str| sha256_hex(text.as_bytes());
        Digests {
            migrations: [
                stripped(sql::MIGRATION_1),
                stripped(sql::MIGRATION_2),
                stripped(sql::MIGRATION_3),
                stripped(sql::MIGRATION_4),
                stripped(&sql::migration_5()),
                stripped(sql::MIGRATION_6),
                stripped(sql::MIGRATION_7),
                stripped(sql::MIGRATION_8),
                stripped(sql::MIGRATION_9),
                stripped(sql::MIGRATION_10),
                raw(sql::MIGRATION_11),
                raw(sql::MIGRATION_12),
            ],
            campaign_identity_delta: stripped(sql::CAMPAIGN_IDENTITY_DELTA),
            lifecycle_extension: raw(sql::LIFECYCLE_EXTENSION),
            recovery_extension: stripped(sql::RECOVERY_EXTENSION),
            purge_extension: raw(sql::PURGE_EXTENSION),
        }
    })
}

/// The `schema_migrations` row Python inserts for `version` (1 to 12).
pub fn migration_row(version: i64) -> (i64, &'static str, &'static str) {
    let names = [
        MIGRATION_1_NAME,
        MIGRATION_2_NAME,
        MIGRATION_3_NAME,
        MIGRATION_4_NAME,
        MIGRATION_5_NAME,
        MIGRATION_6_NAME,
        MIGRATION_7_NAME,
        MIGRATION_8_NAME,
        MIGRATION_9_NAME,
        MIGRATION_10_NAME,
        MIGRATION_11_NAME,
        MIGRATION_12_NAME,
    ];
    let index = usize::try_from(version - 1).expect("migration versions start at 1");
    (version, names[index], digests().migrations[index].as_str())
}

/// One `authority_schema_extensions` row.
pub type ExtensionRow = [String; 4];

fn extension_row(name: &str, sha256: &str, predecessor: &str, predecessor_sha256: &str) -> ExtensionRow {
    [name.to_owned(), sha256.to_owned(), predecessor.to_owned(), predecessor_sha256.to_owned()]
}

/// The campaign identity extension row (`_insert_campaign_identity_extension`).
pub fn campaign_identity_extension_row() -> ExtensionRow {
    extension_row(
        CAMPAIGN_IDENTITY_EXTENSION_NAME,
        &digests().campaign_identity_delta,
        MIGRATION_5_NAME,
        PREDECESSOR_MIGRATION_5_SHA256,
    )
}

/// The lifecycle extension row.
pub fn lifecycle_extension_row() -> ExtensionRow {
    extension_row(
        LIFECYCLE_EXTENSION_NAME,
        &digests().lifecycle_extension,
        CAMPAIGN_IDENTITY_EXTENSION_NAME,
        &digests().campaign_identity_delta,
    )
}

/// The portable recovery extension row, after the lifecycle extension.
pub fn recovery_extension_row() -> ExtensionRow {
    extension_row(
        RECOVERY_EXTENSION_NAME,
        &digests().recovery_extension,
        LIFECYCLE_EXTENSION_NAME,
        &digests().lifecycle_extension,
    )
}

/// The purge extension row, after the recovery extension.
pub fn purge_extension_row() -> ExtensionRow {
    extension_row(
        PURGE_EXTENSION_NAME,
        &digests().purge_extension,
        RECOVERY_EXTENSION_NAME,
        &digests().recovery_extension,
    )
}

/// `_expected_campaign_extension_rows`: sorted, as Python sorts the tuples.
pub fn expected_campaign_extension_rows(include_recovery: bool, include_purge: bool) -> Vec<ExtensionRow> {
    let mut rows = vec![campaign_identity_extension_row(), lifecycle_extension_row()];
    if include_recovery {
        rows.push(recovery_extension_row());
    }
    if include_purge {
        rows.push(purge_extension_row());
    }
    rows.sort();
    rows
}

/// Insert one extension row with the statement Python uses.
pub fn insert_extension(connection: &Connection, row: &ExtensionRow) -> StoreResult<()> {
    connection.execute(
        "INSERT INTO authority_schema_extensions \
         (extension_name, extension_sha256, predecessor_migration_name, \
         predecessor_sql_sha256) VALUES (?, ?, ?, ?)",
        [&row[0], &row[1], &row[2], &row[3]],
    )?;
    Ok(())
}

/// Insert the `schema_migrations` row for `version` and set `user_version`, as every Python
/// step does.
pub fn record_migration(connection: &Connection, version: i64) -> StoreResult<()> {
    let (version, name, sha256) = migration_row(version);
    connection.execute(
        "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
        rusqlite::params![version, name, sha256],
    )?;
    connection.execute_batch(&format!("PRAGMA user_version = {version}"))?;
    Ok(())
}

/// `for statement in sql.split(";"): if statement.strip(): connection.execute(statement)`.
pub fn execute_statements(connection: &Connection, sql: &str) -> StoreResult<()> {
    for statement in sql.split(';') {
        if !py_strip(statement).is_empty() {
            connection.execute(statement, [])?;
        }
    }
    Ok(())
}

/// `connection.executescript(sql)` without Python's implicit COMMIT, which no caller relies on:
/// every Python call site runs it outside a transaction or as the transaction's first text.
pub fn execute_script(connection: &Connection, sql: &str) -> StoreResult<()> {
    connection.execute_batch(sql)?;
    Ok(())
}

/// Read every row of a query as owned values, for the before-and-after checks.
pub(crate) fn all_rows(connection: &Connection, query: &str) -> StoreResult<Vec<Vec<Value>>> {
    let mut statement = connection.prepare(query)?;
    let width = statement.column_count();
    let rows = statement.query_map([], |row| (0..width).map(|index| row.get::<_, Value>(index)).collect())?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

fn column_names(connection: &Connection, table: &str) -> StoreResult<Vec<(String, bool)>> {
    let mut statement = connection.prepare(&format!("PRAGMA table_info({table})"))?;
    let rows = statement.query_map([], |row| {
        let name: Value = row.get(1)?;
        let not_null: Value = row.get(3)?;
        Ok((crate::pytext::py_str(&name), matches!(not_null, Value::Integer(value) if value != 0)))
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

/// `sqlite_campaign.apply_lifecycle_extension`.
pub fn apply_lifecycle_extension(connection: &Connection) -> StoreResult<()> {
    if column_names(connection, "campaign_revisions")?
        .iter()
        .any(|(name, _)| name == "lifecycle_operation_id")
    {
        return Ok(());
    }
    execute_statements(connection, sql::LIFECYCLE_EXTENSION)
}

/// `sqlite_recovery.apply_recovery_extension`.
pub fn apply_recovery_extension(
    connection: &Connection,
    predecessor_name: &str,
    predecessor_sha256: &str,
) -> StoreResult<()> {
    connection.execute(sql::RECOVERY_EXTENSION, [])?;
    insert_extension(
        connection,
        &extension_row(
            RECOVERY_EXTENSION_NAME,
            &digests().recovery_extension,
            predecessor_name,
            predecessor_sha256,
        ),
    )
}

/// `sqlite_authority._apply_portable_recovery_extension`.
pub fn apply_portable_recovery_extension(connection: &Connection) -> StoreResult<()> {
    apply_recovery_extension(connection, LIFECYCLE_EXTENSION_NAME, &digests().lifecycle_extension)
}

const PURGE_OPERATION_COLUMNS: &str =
    "SELECT operation_id, operation_kind, campaign_id, semantic_payload_json, \
     semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, \
     basis_revision, resulting_revision FROM campaign_operations";

/// `sqlite_campaign.apply_purge_extension`.
pub fn apply_purge_extension(connection: &Connection) -> StoreResult<()> {
    let columns = column_names(connection, "campaign_operations")?;
    let not_null =
        |name: &str| columns.iter().find(|(column, _)| column == name).map(|(_, not_null)| *not_null);
    if not_null("basis_turn_id") == Some(false) && not_null("basis_lock_id") == Some(false) {
        return Ok(());
    }
    let ordered = format!("{PURGE_OPERATION_COLUMNS} ORDER BY operation_id");
    let before = all_rows(connection, &ordered)?;
    connection.execute("PRAGMA defer_foreign_keys = ON", [])?;
    connection.execute(
        "CREATE TEMP TABLE campaign_operations_purge_shadow AS \
         SELECT operation_id, operation_kind, campaign_id, semantic_payload_json, \
         semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, \
         basis_revision, resulting_revision FROM campaign_operations",
        [],
    )?;
    connection.execute("DROP TABLE campaign_operations", [])?;
    connection.execute(sql::PURGE_CAMPAIGN_OPERATIONS, [])?;
    connection.execute(
        "INSERT INTO campaign_operations \
         (operation_id, operation_kind, campaign_id, semantic_payload_json, \
         semantic_payload_sha256, result_json, basis_turn_id, basis_lock_id, \
         basis_revision, resulting_revision) SELECT operation_id, operation_kind, \
         campaign_id, semantic_payload_json, semantic_payload_sha256, result_json, \
         basis_turn_id, basis_lock_id, basis_revision, resulting_revision \
         FROM campaign_operations_purge_shadow",
        [],
    )?;
    connection.execute("DROP TABLE campaign_operations_purge_shadow", [])?;
    if all_rows(connection, &ordered)? != before {
        return Err(crate::error::value_error("Campaign operation receipt rebuild changed existing rows"));
    }
    if !all_rows(connection, "PRAGMA foreign_key_check")?.is_empty() {
        return Err(crate::error::value_error("Campaign purge extension has invalid foreign keys"));
    }
    if !integrity_ok(connection)? {
        return Err(crate::error::value_error("Campaign purge extension failed integrity verification"));
    }
    Ok(())
}

/// `tuple(str(row[0]).casefold() for row in PRAGMA integrity_check) == ("ok",)`.
fn integrity_ok(connection: &Connection) -> StoreResult<bool> {
    let rows = all_rows(connection, "PRAGMA integrity_check")?;
    Ok(rows.len() == 1 && casefold_equals(&crate::pytext::py_str(&rows[0][0]), "ok"))
}

/// `str(text).casefold() == ascii`, as Python compares `PRAGMA journal_mode`.
pub(crate) fn casefold_equals(text: &str, ascii: &str) -> bool {
    sylvia_base::pystr::casefold(text) == ascii
}

/// `sqlite_state_ownership._TRIGGER_NAMES`.
pub const TRIGGER_NAMES: [&str; 6] = [
    "campaign_state_owner_delete_requires_empty",
    "campaign_state_owner_immutable",
    "campaign_state_owner_matches_current_lock",
    "campaign_state_owner_required_insert",
    "campaign_state_owner_required_update",
    "mutation_requires_matching_namespace_owner",
];

/// `sqlite_state_ownership.apply_migration`: the schema-6 DDL, then the deterministic owner
/// backfill for every namespace already in `campaign_state`.
pub fn apply_state_ownership_migration(connection: &Connection) -> StoreResult<()> {
    // Python feeds the script line by line to `sqlite3.complete_statement` and executes each
    // complete statement; `execute_batch` runs the same statements in the same order.
    execute_script(connection, sql::MIGRATION_6)?;
    let namespaces = all_rows(
        connection,
        "SELECT DISTINCT campaign_id, namespace FROM campaign_state ORDER BY campaign_id, namespace",
    )?;
    for row in namespaces {
        let campaign_id = crate::pytext::py_str(&row[0]);
        let namespace = crate::pytext::py_str(&row[1]);
        let owner = backfill_owner(connection, &campaign_id, &namespace)?;
        connection.execute(
            "INSERT INTO campaign_state_namespace_owners \
             (campaign_id, namespace, owner_package_id) VALUES (?, ?, ?)",
            rusqlite::params![campaign_id, namespace, owner],
        )?;
    }
    Ok(())
}

fn string_column(connection: &Connection, query: &str, params: &[&str]) -> StoreResult<Vec<String>> {
    let mut statement = connection.prepare(query)?;
    let rows =
        statement.query_map(rusqlite::params_from_iter(params.iter()), |row| row.get::<_, Value>(0))?;
    Ok(rows.map(|value| value.map(|value| crate::pytext::py_str(&value))).collect::<Result<Vec<_>, _>>()?)
}

/// `sqlite_state_ownership._backfill_owner`.
fn backfill_owner(
    connection: &Connection,
    campaign_id: &str,
    namespace: &str,
) -> StoreResult<Option<String>> {
    let package_ids = string_column(
        connection,
        "SELECT membership.package_id FROM campaigns AS campaign \
         JOIN campaign_lock_memberships AS membership \
         ON membership.lock_id = campaign.current_lock_id \
         WHERE campaign.campaign_id = ? ORDER BY membership.closure_order",
        &[campaign_id],
    )?;
    let writers = string_column(
        connection,
        "SELECT DISTINCT action.package_id FROM mutations AS mutation \
         JOIN actions AS action ON action.campaign_id = mutation.campaign_id \
         AND action.action_id = mutation.action_id \
         WHERE mutation.campaign_id = ? AND mutation.namespace = ? \
         ORDER BY action.package_id",
        &[campaign_id, namespace],
    )?;
    let matches: Vec<&String> = package_ids
        .iter()
        .filter(|package_id| {
            namespace == package_id.as_str() || namespace.starts_with(&format!("{package_id}."))
        })
        .collect();
    if writers.iter().any(|writer| !package_ids.contains(writer)) {
        return Err(schema_identity("Campaign state namespace ownership writer is absent from current lock"));
    }
    if writers.len() > 1 || matches.len() > 1 {
        return Err(schema_identity("Campaign state namespace ownership is ambiguous"));
    }
    if let Some(writer) = writers.first() {
        if let Some(prefix) = matches.first() {
            if *prefix != writer {
                return Err(schema_identity("Campaign state namespace ownership conflicts with its prefix"));
            }
        }
        return Ok(Some(writer.clone()));
    }
    if let Some(prefix) = matches.first() {
        return Ok(Some((*prefix).clone()));
    }
    if package_ids.len() == 1 {
        return Ok(Some(package_ids[0].clone()));
    }
    let primary = string_column(
        connection,
        "SELECT membership.package_id FROM campaigns AS campaign \
         JOIN campaign_lock_memberships AS membership \
         ON membership.lock_id = campaign.current_lock_id \
         WHERE campaign.campaign_id = ? AND membership.role = 'primary' \
         ORDER BY membership.package_id",
        &[campaign_id],
    )?;
    if primary.len() == 1 {
        return Ok(Some(primary[0].clone()));
    }
    if package_ids.is_empty() {
        return Ok(None);
    }
    Err(schema_identity("Campaign state namespace ownership is ambiguous"))
}

/// `sqlite_mechanics_choice.apply_migration`.
pub fn apply_mechanics_choice_migration(connection: &Connection) -> StoreResult<()> {
    execute_statements(connection, sql::MIGRATION_7)
}

/// `sqlite_continuity.apply_migration`: the schema-9 DDL, then continuity seeded from accepted
/// campaign plans (each campaign's latest) and legacy DM evidence.
pub fn apply_continuity_migration(connection: &Connection) -> StoreResult<()> {
    execute_statements(connection, sql::MIGRATION_9)?;
    crate::continuity::seed_accepted_plans(connection)?;
    crate::continuity::backfill_legacy_messages(connection)?;
    Ok(())
}

/// `sqlite_draft_lifecycle.apply_migration`.
pub fn apply_draft_lifecycle_migration(connection: &Connection) -> StoreResult<()> {
    let before_operations = all_rows(connection, "SELECT * FROM campaign_operations ORDER BY operation_id")?;
    let before_constructions =
        all_rows(connection, "SELECT * FROM campaign_constructions ORDER BY campaign_id")?;
    execute_statements(connection, sql::MIGRATION_10)?;
    if all_rows(connection, "SELECT * FROM campaign_operations ORDER BY operation_id")? != before_operations {
        return Err(crate::error::value_error(
            "Draft lifecycle migration changed campaign operation history",
        ));
    }
    if all_rows(connection, "SELECT * FROM campaign_constructions ORDER BY campaign_id")?
        != before_constructions
    {
        return Err(crate::error::value_error(
            "Draft lifecycle migration changed campaign construction history",
        ));
    }
    if !all_rows(connection, "PRAGMA foreign_key_check")?.is_empty() {
        return Err(crate::error::value_error("Draft lifecycle migration has invalid foreign keys"));
    }
    Ok(())
}

/// `sqlite_reasoning.apply_migration`.
pub fn apply_reasoning_migration(connection: &Connection) -> StoreResult<()> {
    connection.execute(sql::MIGRATION_11, [])?;
    Ok(())
}

/// `sqlite_effort.apply_migration`.
pub fn apply_effort_migration(connection: &Connection) -> StoreResult<()> {
    execute_statements(connection, sql::MIGRATION_12)
}

/// `sqlite_packages.migrate_legacy_campaigns`: schema-1/2 campaigns become explicit
/// metadata-only unavailable locks.
pub fn migrate_legacy_campaigns(connection: &Connection) -> StoreResult<()> {
    let campaigns = all_rows(connection, "SELECT campaign_id, revision FROM campaigns ORDER BY campaign_id")?;
    for campaign in campaigns {
        let campaign_id = crate::pytext::py_str(&campaign[0]);
        let rows = {
            let mut statement = connection.prepare(
                "SELECT package_id, version, sha256, role, load_order \
                 FROM campaign_package_memberships WHERE campaign_id = ? ORDER BY load_order",
            )?;
            let rows = statement
                .query_map([&campaign_id], |row| (0..5).map(|index| row.get::<_, Value>(index)).collect())?;
            rows.collect::<Result<Vec<Vec<Value>>, _>>()?
        };
        let mut representation = Vec::with_capacity(rows.len());
        for row in &rows {
            let load_order = crate::pytext::py_int(&row[4])
                .ok_or_else(|| crate::error::value_error("invalid literal for int() with base 10"))?;
            let mut item = JsonMap::new();
            item.insert("id".into(), Json::String(crate::pytext::py_str(&row[0])));
            item.insert("version".into(), Json::String(crate::pytext::py_str(&row[1])));
            item.insert("content_sha256".into(), Json::String(crate::pytext::py_str(&row[2])));
            item.insert("role".into(), Json::String(crate::pytext::py_str(&row[3])));
            item.insert("load_order".into(), Json::from(load_order));
            representation.push(Json::Object(item));
        }
        let closure = sha256_hex(canonical_bytes(&Json::Array(representation)));
        let lock_id = format!("legacy-lock-{}", &sha256_hex(campaign_id.as_bytes())[..32]);
        connection.execute(
            "INSERT INTO campaign_locks VALUES (?, ?, ?, 'legacy_unavailable', NULL, 0)",
            rusqlite::params![lock_id, campaign_id, closure],
        )?;
        for row in &rows {
            let load_order = crate::pytext::py_int(&row[4])
                .ok_or_else(|| crate::error::value_error("invalid literal for int() with base 10"))?;
            connection.execute(
                "INSERT INTO campaign_lock_memberships VALUES (?, ?, ?, ?, ?, ?)",
                rusqlite::params![
                    lock_id,
                    load_order,
                    crate::pytext::py_str(&row[0]),
                    crate::pytext::py_str(&row[1]),
                    crate::pytext::py_str(&row[2]),
                    crate::pytext::py_str(&row[3]),
                ],
            )?;
        }
        connection.execute(
            "UPDATE campaigns SET current_lock_id = ? WHERE campaign_id = ?",
            rusqlite::params![lock_id, campaign_id],
        )?;
        connection.execute(
            "UPDATE actions SET lock_id = ? WHERE campaign_id = ?",
            rusqlite::params![lock_id, campaign_id],
        )?;
    }
    Ok(())
}

/// `sqlite_campaign._parse_mapping`.
fn parse_mapping(text: &str) -> StoreResult<sylvia_base::JsonMap> {
    match crate::campaigns::parse_canonical(text)? {
        Json::Object(map) => Ok(map),
        _ => Err(crate::error::value_error("Stored canonical JSON must be an object")),
    }
}

/// `sqlite_campaign.migrate_v4_participants`: declaration proof backfilled from every published
/// manifest, then each participant validated against its uniquely reachable revision-0
/// declaration, or quarantined.
pub fn migrate_v4_participants(connection: &Connection) -> StoreResult<()> {
    for row in all_rows(
        connection,
        "SELECT canonical_json FROM package_manifests          WHERE status = 'published' ORDER BY package_id, version, content_sha256",
    )? {
        let record = Json::Object(parse_mapping(&crate::pytext::py_str(&row[0]))?);
        let manifest = sylvia_domain::package_library::manifest_from_record(&record, None)?;
        crate::packages::insert_or_compare_declarations(connection, &manifest)?;
    }
    let text = |value: &Value| crate::pytext::py_str(value);
    for row in all_rows(
        connection,
        "SELECT campaign_id, participant_id, kind, package_id, package_version, data_json          FROM participants ORDER BY campaign_id, participant_id",
    )? {
        let (campaign_id, participant_id) = (text(&row[0]), text(&row[1]));
        let (package_id, package_version) = (text(&row[3]), text(&row[4]));
        let content_rows: Vec<Vec<Value>> = {
            let mut statement = connection.prepare(
                "SELECT DISTINCT membership.content_sha256                  FROM campaign_locks AS lock                  JOIN campaign_lock_memberships AS membership ON membership.lock_id = lock.lock_id                  WHERE lock.campaign_id = ? AND lock.created_revision = 0                  AND membership.package_id = ? AND membership.version = ?",
            )?;
            let rows = statement.query_map(params![campaign_id, package_id, package_version], |row| {
                Ok(vec![row.get::<_, Value>(0)?])
            })?;
            rows.collect::<Result<_, _>>()?
        };
        let mut package_sha256: Option<String> = None;
        let mut declaration: Option<(Value, Value, Value)> = None;
        if content_rows.len() == 1 {
            let sha256 = text(&content_rows[0][0]);
            declaration = connection
                .query_row(
                    "SELECT schema_id, schema_sha256, schema_json                      FROM package_participant_artifacts WHERE package_id = ? AND version = ?                      AND content_sha256 = ? AND kind = ?",
                    params![package_id, package_version, sha256, text(&row[2])],
                    |found| Ok((found.get(0)?, found.get(1)?, found.get(2)?)),
                )
                .optional()?;
            package_sha256 = Some(sha256);
        }
        let mut validated = false;
        if let Some((schema_id, schema_sha256, schema_json)) = &declaration {
            let data = parse_mapping(&text(&row[5]))?;
            let schema = parse_mapping(&text(schema_json))?;
            match sylvia_domain::schema_admission::validate_json_value(
                &Json::Object(data),
                &schema,
                &format!("Participant {participant_id}"),
            ) {
                Ok(()) => {
                    connection.execute(
                        "UPDATE participants SET package_sha256 = ?, schema_id = ?,                          schema_sha256 = ?, validation_status = 'validated'                          WHERE campaign_id = ? AND participant_id = ?",
                        params![package_sha256, text(schema_id), text(schema_sha256), campaign_id, participant_id],
                    )?;
                    validated = true;
                }
                // Only `CommandRejected` is caught; anything else escapes, as in Python.
                Err(sylvia_domain::DomainError::Rejected { error_type: "CommandRejected", .. }) => {}
                Err(error) => return Err(error.into()),
            }
        }
        if validated {
            connection.execute(
                "DELETE FROM participant_quarantines WHERE campaign_id = ? AND participant_id = ?",
                params![campaign_id, participant_id],
            )?;
        } else {
            connection.execute(
                "UPDATE participants SET package_sha256 = ?, schema_id = NULL,                  schema_sha256 = NULL, validation_status = 'quarantined'                  WHERE campaign_id = ? AND participant_id = ?",
                params![package_sha256, campaign_id, participant_id],
            )?;
            connection.execute(
                "INSERT INTO participant_quarantines(campaign_id, participant_id, reason_code)                  VALUES (?, ?, 'exact_schema_proof_unavailable')",
                params![campaign_id, participant_id],
            )?;
        }
    }
    Ok(())
}

/// One table's normalized identity: `(name, " ".join(sql.split()).casefold())`.
pub type SchemaIdentity = Vec<(String, String)>;

/// `sqlite_authority._schema_identity`.
pub fn schema_identity_of(connection: &Connection) -> StoreResult<SchemaIdentity> {
    let mut statement = connection.prepare(
        "SELECT name, sql FROM sqlite_master \
         WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
    )?;
    let rows = statement.query_map([], |row| {
        let name: Value = row.get(0)?;
        let sql: Value = row.get(1)?;
        Ok((crate::pytext::py_str(&name), normalized_casefold(&crate::pytext::py_str(&sql))))
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

/// `sqlite_state_ownership.trigger_identity`.
pub fn trigger_identity_of(connection: &Connection) -> StoreResult<SchemaIdentity> {
    let placeholders = vec!["?"; TRIGGER_NAMES.len()].join(",");
    let mut statement = connection.prepare(&format!(
        "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name IN ({placeholders}) ORDER BY name"
    ))?;
    let rows = statement.query_map(rusqlite::params_from_iter(TRIGGER_NAMES.iter()), |row| {
        let name: Value = row.get(0)?;
        let sql: Value = row.get(1)?;
        Ok((crate::pytext::py_str(&name), normalized_casefold(&crate::pytext::py_str(&sql))))
    })?;
    Ok(rows.collect::<Result<Vec<_>, _>>()?)
}

/// `sqlite_state_ownership.EXPECTED_TRIGGER_IDENTITY`.
pub fn expected_trigger_identity() -> &'static SchemaIdentity {
    static IDENTITY: OnceLock<SchemaIdentity> = OnceLock::new();
    IDENTITY.get_or_init(|| {
        let connection = Connection::open_in_memory().expect("an in-memory database opens");
        execute_script(&connection, sql::TRIGGER_FIXTURE).expect("the trigger fixture applies");
        execute_script(&connection, sql::MIGRATION_6).expect("migration 6 applies to its fixture");
        trigger_identity_of(&connection).expect("the trigger identity reads")
    })
}

/// The schema states `_verify_exact_schema_identity` recognises, one per Python
/// `_EXPECTED_*_SCHEMA_IDENTITY` constant.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Known {
    /// `_EXPECTED_SCHEMA_IDENTITY` (schema 3).
    Base,
    /// `_EXPECTED_SESSION_SCHEMA_IDENTITY` (schema 4).
    Session,
    /// `_EXPECTED_PREDECESSOR_CAMPAIGN_SCHEMA_IDENTITY`.
    PredecessorCampaign,
    /// `_EXPECTED_PROBE01_CAMPAIGN_SCHEMA_IDENTITY`.
    Probe01Campaign,
    /// `_EXPECTED_CAMPAIGN_SCHEMA_IDENTITY` (W11 schema 5).
    Campaign,
    /// `_EXPECTED_W12_CAMPAIGN_SCHEMA_IDENTITY`.
    W12Campaign,
    /// `_EXPECTED_PURGE_CAMPAIGN_SCHEMA_IDENTITY`.
    PurgeCampaign,
    /// `_EXPECTED_STATE_OWNERSHIP_SCHEMA_IDENTITY`.
    StateOwnership,
    /// `_EXPECTED_PURGE_STATE_OWNERSHIP_SCHEMA_IDENTITY`.
    PurgeStateOwnership,
    /// `_EXPECTED_MECHANICS_CHOICE_SCHEMA_IDENTITY`.
    MechanicsChoice,
    /// `_EXPECTED_PURGE_MECHANICS_CHOICE_SCHEMA_IDENTITY`.
    PurgeMechanicsChoice,
    /// `_EXPECTED_BUILDER_CHAT_SCHEMA_IDENTITY`.
    BuilderChat,
    /// `_EXPECTED_PURGE_BUILDER_CHAT_SCHEMA_IDENTITY`.
    PurgeBuilderChat,
    /// `_EXPECTED_CONTINUITY_SCHEMA_IDENTITY`.
    Continuity,
    /// `_EXPECTED_PURGE_CONTINUITY_SCHEMA_IDENTITY`.
    PurgeContinuity,
    /// `_EXPECTED_DRAFT_LIFECYCLE_SCHEMA_IDENTITY`.
    DraftLifecycle,
    /// `_EXPECTED_REASONING_SCHEMA_IDENTITY`.
    Reasoning,
    /// `_EXPECTED_EFFORT_SCHEMA_IDENTITY` (schema 12, current).
    Effort,
}

impl Known {
    /// Every state, in the order of the vectors file's keys.
    pub const ALL: [Known; 18] = [
        Known::Base,
        Known::Session,
        Known::PredecessorCampaign,
        Known::Probe01Campaign,
        Known::Campaign,
        Known::W12Campaign,
        Known::PurgeCampaign,
        Known::StateOwnership,
        Known::PurgeStateOwnership,
        Known::MechanicsChoice,
        Known::PurgeMechanicsChoice,
        Known::BuilderChat,
        Known::PurgeBuilderChat,
        Known::Continuity,
        Known::PurgeContinuity,
        Known::DraftLifecycle,
        Known::Reasoning,
        Known::Effort,
    ];

    /// The key `sql/generate_sql.py` gives this state.
    pub fn vector_key(self) -> &'static str {
        match self {
            Known::Base => "base",
            Known::Session => "session",
            Known::PredecessorCampaign => "predecessor_campaign",
            Known::Probe01Campaign => "probe01_campaign",
            Known::Campaign => "campaign",
            Known::W12Campaign => "w12_campaign",
            Known::PurgeCampaign => "purge_campaign",
            Known::StateOwnership => "state_ownership",
            Known::PurgeStateOwnership => "purge_state_ownership",
            Known::MechanicsChoice => "mechanics_choice",
            Known::PurgeMechanicsChoice => "purge_mechanics_choice",
            Known::BuilderChat => "builder_chat",
            Known::PurgeBuilderChat => "purge_builder_chat",
            Known::Continuity => "continuity",
            Known::PurgeContinuity => "purge_continuity",
            Known::DraftLifecycle => "draft_lifecycle",
            Known::Reasoning => "reasoning",
            Known::Effort => "effort",
        }
    }

    /// The `_expected_schema_identity(...)` arguments behind this state.
    fn include(self) -> Include {
        let sessions = Include { sessions: true, ..Include::default() };
        let campaign = Include { campaign: true, lifecycle: true, ..sessions };
        let w12 = Include { recovery: true, ..campaign };
        let ownership = Include { state_ownership: true, ..w12 };
        let mechanics = Include { mechanics_choice: true, ..ownership };
        let builder = Include { builder_chats: true, ..mechanics };
        let continuity = Include { continuity: true, ..builder };
        let draft = Include { draft_lifecycle: true, ..continuity };
        let reasoning = Include { reasoning: true, ..draft };
        let purge = |include: Include| Include { purge: true, ..include };
        match self {
            Known::Base => Include::default(),
            Known::Session => sessions,
            Known::PredecessorCampaign => Include { campaign: true, predecessor: true, ..sessions },
            Known::Probe01Campaign => Include { campaign: true, ..sessions },
            Known::Campaign => campaign,
            Known::W12Campaign => w12,
            Known::PurgeCampaign => purge(w12),
            Known::StateOwnership => ownership,
            Known::PurgeStateOwnership => purge(ownership),
            Known::MechanicsChoice => mechanics,
            Known::PurgeMechanicsChoice => purge(mechanics),
            Known::BuilderChat => builder,
            Known::PurgeBuilderChat => purge(builder),
            Known::Continuity => continuity,
            Known::PurgeContinuity => purge(continuity),
            Known::DraftLifecycle => draft,
            Known::Reasoning => reasoning,
            Known::Effort => Include { effort: true, ..reasoning },
        }
    }

    /// The identity, built once in an in-memory database as Python builds it at import.
    pub fn identity(self) -> &'static SchemaIdentity {
        static IDENTITIES: OnceLock<Vec<SchemaIdentity>> = OnceLock::new();
        let all = IDENTITIES.get_or_init(|| {
            Known::ALL
                .iter()
                .map(|known| {
                    expected_schema_identity(known.include()).expect("the frozen migrations apply in memory")
                })
                .collect()
        });
        let index = Known::ALL.iter().position(|known| *known == self).expect("every state is listed");
        &all[index]
    }
}

#[derive(Debug, Clone, Copy, Default)]
struct Include {
    sessions: bool,
    campaign: bool,
    predecessor: bool,
    lifecycle: bool,
    recovery: bool,
    purge: bool,
    state_ownership: bool,
    mechanics_choice: bool,
    builder_chats: bool,
    continuity: bool,
    draft_lifecycle: bool,
    reasoning: bool,
    effort: bool,
}

/// `sqlite_authority._expected_schema_identity`.
fn expected_schema_identity(include: Include) -> StoreResult<SchemaIdentity> {
    let connection = Connection::open_in_memory()?;
    execute_script(&connection, sql::MIGRATION_1)?;
    execute_script(&connection, sql::MIGRATION_2)?;
    execute_script(&connection, sql::MIGRATION_3)?;
    if include.sessions {
        execute_script(&connection, sql::MIGRATION_4)?;
    }
    if include.campaign {
        if include.predecessor {
            execute_script(&connection, sql::PREDECESSOR_MIGRATION_5)?;
        } else {
            execute_script(&connection, &sql::migration_5())?;
        }
    }
    if include.lifecycle {
        apply_lifecycle_extension(&connection)?;
    }
    if include.recovery {
        connection.execute(sql::RECOVERY_EXTENSION, [])?;
    }
    if include.purge {
        apply_purge_extension(&connection)?;
    }
    if include.state_ownership {
        execute_script(&connection, sql::MIGRATION_6)?;
    }
    if include.mechanics_choice {
        execute_script(&connection, sql::MIGRATION_7)?;
    }
    if include.builder_chats {
        execute_script(&connection, sql::MIGRATION_8)?;
    }
    if include.continuity {
        execute_script(&connection, sql::MIGRATION_9)?;
    }
    if include.draft_lifecycle {
        apply_draft_lifecycle_migration(&connection)?;
    }
    if include.reasoning {
        apply_reasoning_migration(&connection)?;
    }
    if include.effort {
        apply_effort_migration(&connection)?;
    }
    schema_identity_of(&connection)
}

/// The vectors' digest of an identity: table count and SHA-256 of `json.dumps(identity)`.
pub fn identity_digest(identity: &SchemaIdentity) -> (usize, String) {
    let value = Json::Array(
        identity
            .iter()
            .map(|(name, sql)| Json::Array(vec![Json::String(name.clone()), Json::String(sql.clone())]))
            .collect(),
    );
    (identity.len(), sha256_hex(sylvia_base::pyjson::dumps(&value, Dump::DEFAULT).as_bytes()))
}
