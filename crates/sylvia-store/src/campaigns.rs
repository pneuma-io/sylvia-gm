//! Campaign reads: the campaign rail (`campaign_index`) and the stored records it projects.
//!
//! Python: `SQLiteAuthorityStore.campaign_index` and `CampaignIndexRecord`;
//! `sqlite_packages` (`read_lock_record`, `current_lock_record`,
//! `_package_lock_from_membership_rows`, `_manifest_from_identity`, `exact_manifest`);
//! `sqlite_session` (`session_from_row`, `handoff_from_row`, `interview_name`).

use crate::actions::rejected;
use crate::error::{StoreError, StoreResult};
use crate::pytext::{py_int, py_str};
use crate::store::AuthorityStore;
use rusqlite::types::Value;
use rusqlite::{Connection, OptionalExtension, Row};
use sylvia_base::{pystr, Json, JsonMap};
use sylvia_domain::authority::{
    validate_authority_identity, CampaignRecord, LifecycleState, PackageDependency, PackageLock,
    PackageLockEntry, PackageRole, TurnStatus, WriterRole,
};
use sylvia_domain::model_capability::model_guidance_from_payload;
use sylvia_domain::package_library::{
    manifest_from_record, parse_canonical_json_bytes, JsonShapeLimits, PackageLockAvailability,
    PackageLockRecord, PackageManifest, DEFAULT_LABEL,
};
use sylvia_domain::session::{
    agent_session_payload_sha256, BuilderDMHandoff, BuilderDMHandoffFields, SessionRecord,
    SessionRecordFields, SessionStatus,
};

/// One validated campaign graph captured in a coherent read transaction (`CampaignIndexRecord`).
#[derive(Debug, Clone, PartialEq)]
pub struct CampaignIndexRecord {
    pub campaign: CampaignRecord,
    pub package_lock: Option<PackageLock>,
    pub package_labels: Vec<(String, String)>,
    pub session_activity_ordinal: i64,
    pub turns: Vec<(String, TurnStatus)>,
    pub top_level_sessions: Vec<SessionRecord>,
    pub handoffs: Vec<BuilderDMHandoff>,
    pub interview_name: Option<String>,
}

/// One row by column name.
pub(crate) struct Named<'a> {
    pub(crate) columns: &'a [String],
    pub(crate) values: Vec<Value>,
}

impl Named<'_> {
    pub(crate) fn get(&self, column: &str) -> StoreResult<&Value> {
        let index = self
            .columns
            .iter()
            .position(|name| name == column)
            .ok_or_else(|| StoreError::Other(format!("No item with that key: {column}")))?;
        Ok(&self.values[index])
    }

    pub(crate) fn has(&self, column: &str) -> bool {
        self.columns.iter().any(|name| name == column)
    }

    pub(crate) fn text(&self, column: &str) -> StoreResult<String> {
        Ok(py_str(self.get(column)?))
    }

    pub(crate) fn optional_text(&self, column: &str) -> StoreResult<Option<String>> {
        Ok(match self.get(column)? {
            Value::Null => None,
            value => Some(py_str(value)),
        })
    }

    pub(crate) fn int(&self, column: &str) -> StoreResult<i64> {
        py_int(self.get(column)?)
            .ok_or_else(|| crate::error::value_error("invalid literal for int() with base 10"))
    }
}

/// Every row of a query, by column name.
pub(crate) fn named_rows(
    connection: &Connection,
    query: &str,
    values: &[&dyn rusqlite::ToSql],
) -> StoreResult<(Vec<String>, Vec<Vec<Value>>)> {
    let mut statement = connection.prepare(query)?;
    let columns: Vec<String> = statement.column_names().iter().map(|name| (*name).to_owned()).collect();
    let width = columns.len();
    let rows = statement
        .query_map(values, |row: &Row<'_>| (0..width).map(|index| row.get::<_, Value>(index)).collect())?;
    let rows = rows.collect::<Result<Vec<Vec<Value>>, _>>()?;
    Ok((columns, rows))
}

pub(crate) fn parse<T>(text: &str, parse: fn(&str) -> Option<T>, name: &str) -> StoreResult<T> {
    parse(text).ok_or_else(|| crate::error::value_error(format!("'{text}' is not a valid {name}")))
}

/// `sqlite_packages._parse`: `parse_canonical_json_bytes` with its default label.
fn parse_canonical(text: &str) -> StoreResult<Json> {
    Ok(parse_canonical_json_bytes(text.as_bytes(), DEFAULT_LABEL, JsonShapeLimits::NONE)?)
}

/// `_manifest_from_identity`: the exact published manifest, parsed as `manifest_from_record`.
pub(crate) fn manifest_from_identity(
    connection: &Connection,
    identity: (&str, &str, &str),
) -> StoreResult<PackageManifest> {
    let row: Option<(Value, Value)> = connection
        .query_row(
            "SELECT canonical_json, status FROM package_manifests \
             WHERE package_id = ? AND version = ? AND content_sha256 = ?",
            [identity.0, identity.1, identity.2],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()?;
    match row {
        Some((canonical, status)) if py_str(&status) == "published" => {
            Ok(manifest_from_record(&parse_canonical(&py_str(&canonical))?, None)?)
        }
        _ => Err(rejected("Exact package manifest is unavailable", "package_unavailable")),
    }
}

/// `_package_lock_from_membership_rows`.
fn package_lock_from_memberships(connection: &Connection, rows: &[Vec<Value>]) -> StoreResult<PackageLock> {
    let mut entries = Vec::with_capacity(rows.len());
    for row in rows {
        // closure_order, package_id, version, content_sha256, role
        let identity = (py_str(&row[1]), py_str(&row[2]), py_str(&row[3]));
        let published = connection
            .query_row(
                "SELECT 1 FROM package_manifests WHERE package_id = ? AND version = ? \
                 AND content_sha256 = ? AND status = 'published'",
                [&identity.0, &identity.1, &identity.2],
                |_| Ok(()),
            )
            .optional()?
            .is_some();
        let (dependencies, capabilities) = if published {
            let manifest = manifest_from_identity(connection, (&identity.0, &identity.1, &identity.2))?;
            let dependencies = manifest
                .dependencies
                .iter()
                .map(|dependency| {
                    PackageDependency::new(
                        dependency.package_id(),
                        dependency.version(),
                        dependency.content_sha256(),
                        dependency.kind(),
                        dependency.namespace(),
                    )
                })
                .collect::<Result<Vec<_>, _>>()?;
            (dependencies, manifest.capabilities.clone())
        } else {
            let capabilities_json: Value = connection.query_row(
                "SELECT capabilities_json FROM packages WHERE package_id = ? AND version = ? AND sha256 = ?",
                [&identity.0, &identity.1, &identity.2],
                |row| row.get(0),
            )?;
            let capabilities = match parse_canonical(&py_str(&capabilities_json))? {
                Json::Array(items) => items.iter().map(json_str).collect(),
                other => {
                    return Err(StoreError::Other(format!("Stored capabilities are not a sequence: {other}")))
                }
            };
            let (columns, dependency_rows) = named_rows(
                connection,
                "SELECT * FROM package_dependencies WHERE package_id = ? AND version = ? \
                 AND sha256 = ? ORDER BY dependency_order",
                &[&identity.0, &identity.1, &identity.2],
            )?;
            let dependencies = dependency_rows
                .into_iter()
                .map(|values| {
                    let dependency = Named { columns: &columns, values };
                    Ok(PackageDependency::new(
                        dependency.text("dependency_id")?,
                        dependency.text("dependency_version")?,
                        dependency.text("dependency_sha256")?,
                        dependency.text("dependency_kind")?,
                        dependency.text("dependency_namespace")?,
                    )?)
                })
                .collect::<StoreResult<Vec<_>>>()?;
            (dependencies, capabilities)
        };
        let role = parse(&py_str(&row[4]), PackageRole::parse, "PackageRole")?;
        let load_order = py_int(&row[0])
            .ok_or_else(|| crate::error::value_error("invalid literal for int() with base 10"))?;
        entries.push(PackageLockEntry::new(
            identity.0,
            identity.1,
            identity.2,
            role,
            load_order,
            dependencies,
            capabilities,
        )?);
    }
    Ok(PackageLock::new(entries)?)
}

/// Python's `str(item)` of a parsed JSON value, exact for the strings stored here.
fn json_str(value: &Json) -> String {
    match value {
        Json::String(text) => text.clone(),
        Json::Bool(true) => "True".into(),
        Json::Bool(false) => "False".into(),
        Json::Null => "None".into(),
        Json::Number(number) => number.as_i64().map_or_else(
            || number.as_f64().map_or_else(|| number.to_string(), sylvia_base::pyjson::float_repr),
            |integer| integer.to_string(),
        ),
        // Python would print its own repr of a container; no stored capability or scope is one.
        other => sylvia_base::pyjson::dumps(other, sylvia_base::pyjson::Dump::DEFAULT),
    }
}

/// `sqlite_packages.read_lock_record`.
pub fn read_lock_record(connection: &Connection, lock_id: &str) -> StoreResult<PackageLockRecord> {
    let (columns, rows) =
        named_rows(connection, "SELECT * FROM campaign_locks WHERE lock_id = ?", &[&lock_id])?;
    let Some(values) = rows.into_iter().next() else {
        return Err(rejected("Campaign package lock is unavailable", "package_unavailable"));
    };
    let row = Named { columns: &columns, values };
    let (_, memberships) = named_rows(
        connection,
        "SELECT closure_order, package_id, version, content_sha256, role \
         FROM campaign_lock_memberships WHERE lock_id = ? ORDER BY closure_order",
        &[&lock_id],
    )?;
    let package_lock = package_lock_from_memberships(connection, &memberships)?;
    Ok(PackageLockRecord {
        lock_id: lock_id.to_owned(),
        campaign_id: row.text("campaign_id")?,
        closure_sha256: row.text("closure_sha256")?,
        package_lock,
        availability: parse(
            &row.text("availability")?,
            PackageLockAvailability::parse,
            "PackageLockAvailability",
        )?,
        predecessor_lock_id: row.optional_text("predecessor_lock_id")?,
        created_revision: row.int("created_revision")?,
    })
}

/// `sqlite_packages.current_lock_record`.
pub fn current_lock_record(connection: &Connection, campaign_id: &str) -> StoreResult<PackageLockRecord> {
    let lock: Option<Value> = connection
        .query_row("SELECT current_lock_id FROM campaigns WHERE campaign_id = ?", [campaign_id], |row| {
            row.get(0)
        })
        .optional()?;
    match lock {
        Some(Value::Null) | None => {
            Err(rejected("Campaign package lock is unavailable", "package_unavailable"))
        }
        Some(lock_id) => read_lock_record(connection, &py_str(&lock_id)),
    }
}

/// `sqlite_session._load_value` / `_load_payload`: canonical session JSON, an object.
pub(crate) fn session_payload(text: &str) -> StoreResult<JsonMap> {
    let parsed =
        sylvia_base::pyjson::loads(text).map_err(|error| crate::error::value_error(error.message))?;
    if crate::actions::dump(&parsed) != text {
        return Err(crate::error::value_error("Stored session JSON is not canonical"));
    }
    match parsed {
        Json::Object(map) => Ok(map),
        _ => Err(crate::error::value_error("Stored session message content is not an object")),
    }
}

/// `sqlite_session.session_from_row`.
pub(crate) fn session_from_row(row: &Named<'_>) -> StoreResult<SessionRecord> {
    let scope = match sylvia_base::pyjson::loads(&row.text("scope_json")?)
        .map_err(|error| crate::error::value_error(error.message))?
    {
        Json::Array(items) => items.iter().map(json_str).collect(),
        Json::String(text) => text.chars().map(String::from).collect(),
        Json::Object(map) => map.keys().cloned().collect(),
        other => return Err(StoreError::Other(format!("'{}' object is not iterable", python_type(&other)))),
    };
    let preference = |column: &str| -> StoreResult<Option<String>> {
        if row.has(column) {
            row.optional_text(column)
        } else {
            Ok(None)
        }
    };
    let record = SessionRecord::new(SessionRecordFields {
        session_id: row.text("session_id")?,
        campaign_id: Some(row.text("campaign_id")?),
        role: parse(&row.text("role")?, WriterRole::parse, "WriterRole")?,
        provider_id: row.text("provider_id")?,
        model_id: row.text("model_id")?,
        status: parse(&row.text("status")?, SessionStatus::parse, "SessionStatus")?,
        parent_session_id: row.optional_text("parent_session_id")?,
        selected_skill: row.optional_text("selected_skill")?,
        scope,
        reasoning_effort: preference("reasoning_effort")?,
        reasoning_effort_once: preference("reasoning_effort_once")?,
    })?;
    if row.text("payload_sha256")? != agent_session_payload_sha256(&record) {
        return Err(crate::error::value_error("Stored agent session payload identity is invalid"));
    }
    Ok(record)
}

pub(crate) fn python_type(value: &Json) -> &'static str {
    match value {
        Json::Null => "NoneType",
        Json::Bool(_) => "bool",
        Json::Number(number) if number.is_f64() => "float",
        Json::Number(_) => "int",
        Json::String(_) => "str",
        Json::Array(_) => "list",
        Json::Object(_) => "dict",
    }
}

/// `sqlite_session.handoff_from_row`.
fn handoff_from_row(row: &Named<'_>) -> StoreResult<BuilderDMHandoff> {
    let safe_payload = session_payload(&row.text("safe_payload_json")?)?;
    let mut expected = JsonMap::new();
    expected.insert("campaign_id".into(), row.text("campaign_id")?.into());
    expected.insert("builder_session_id".into(), row.text("builder_session_id")?.into());
    expected.insert("dm_session_id".into(), row.text("dm_session_id")?.into());
    expected.insert("package_lock_id".into(), row.text("package_lock_id")?.into());
    expected.insert(
        "package_candidate_id".into(),
        row.optional_text("package_candidate_id")?.map_or(Json::Null, Json::String),
    );
    expected.insert("plan_id".into(), row.text("plan_id")?.into());
    expected.insert("resulting_campaign_revision".into(), row.int("resulting_campaign_revision")?.into());
    expected.insert("completed_turn_id".into(), row.text("completed_turn_id")?.into());
    expected
        .insert("model_guidance".into(), safe_payload.get("model_guidance").cloned().unwrap_or(Json::Null));
    if let Some(provider) = safe_payload.get("dm_provider_id") {
        let Json::String(provider) = provider else {
            return Err(crate::error::value_error("Stored Builder-to-DM provider identity is invalid"));
        };
        validate_authority_identity(provider, "Handoff DM provider", false)?;
        expected.insert("dm_provider_id".into(), Json::String(provider.clone()));
    }
    if safe_payload != expected {
        return Err(crate::error::value_error("Stored Builder-to-DM handoff chronology is invalid"));
    }
    let guidance =
        safe_payload.get("model_guidance").ok_or_else(|| StoreError::Other("'model_guidance'".into()))?;
    Ok(BuilderDMHandoff::new(BuilderDMHandoffFields {
        handoff_id: row.text("handoff_id")?,
        campaign_id: row.text("campaign_id")?,
        builder_session_id: row.text("builder_session_id")?,
        dm_session_id: row.text("dm_session_id")?,
        package_lock_id: row.text("package_lock_id")?,
        package_candidate_id: row.optional_text("package_candidate_id")?,
        plan_id: row.text("plan_id")?,
        plan_revision: row.int("plan_revision")?,
        plan_content_sha256: row.text("plan_content_sha256")?,
        expected_revision: row.int("expected_revision")?,
        resulting_campaign_revision: row.int("resulting_campaign_revision")?,
        chronology_ordinal: row.int("chronology_ordinal")?,
        completed_turn_id: row.text("completed_turn_id")?,
        model_guidance: model_guidance_from_payload(guidance)?,
        payload_sha256: row.text("payload_sha256")?,
    })?)
}

/// `sqlite_session.interview_name`: the accepted campaign name of a default-titled Interview.
fn interview_name(
    connection: &Connection,
    session_id: &str,
    campaign_id: &str,
) -> StoreResult<Option<String>> {
    let content: Option<Value> = connection
        .query_row(
            "SELECT answer.content_json FROM agent_session_messages AS answer \
             WHERE answer.session_id = ? AND answer.kind = 'user' \
             AND NOT EXISTS (SELECT 1 FROM campaign_lifecycle_transitions \
             WHERE campaign_id = ? AND operation_kind = 'rename_campaign') \
             AND answer.message_id = (\
             SELECT json_extract(state.content_json, '$.answers.name') \
             FROM agent_session_messages AS state WHERE state.session_id = ? \
             AND state.kind = 'evidence' \
             AND json_extract(state.content_json, '$.kind') = 'campaign_interview_state' \
             ORDER BY state.sequence DESC LIMIT 1)",
            [session_id, campaign_id, session_id],
            |row| row.get(0),
        )
        .optional()?;
    let Some(content) = content else {
        return Ok(None);
    };
    let parsed = sylvia_base::pyjson::loads(&py_str(&content))
        .map_err(|error| crate::error::value_error(error.message))?;
    let Json::Object(content) = parsed else {
        return Err(StoreError::Other(format!("'{}' object has no attribute 'get'", python_type(&parsed))));
    };
    let answer = content.get("interview_value").cloned().unwrap_or_else(|| Json::Object(JsonMap::new()));
    let name = match &answer {
        Json::Object(answer) => answer.get("display_name"),
        _ => None,
    };
    Ok(match name {
        Some(Json::String(name)) => {
            let stripped = pystr::strip(name);
            (0 < pystr::len(stripped) && pystr::len(stripped) <= 120).then(|| stripped.to_owned())
        }
        _ => None,
    })
}

impl AuthorityStore {
    /// `campaign_index`: the campaign rail by case-insensitive name, exact name, then identity.
    pub fn campaign_index(&self) -> StoreResult<Vec<CampaignIndexRecord>> {
        let connection = self.connect_read_only()?;
        connection.execute_batch("BEGIN")?;
        let result = campaign_index_in(&connection);
        let _ = connection.execute_batch(if result.is_ok() { "COMMIT" } else { "ROLLBACK" });
        result
    }
}

fn campaign_index_in(connection: &Connection) -> StoreResult<Vec<CampaignIndexRecord>> {
    let session_schema_available = connection
        .query_row("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'agent_sessions'", [], |_| {
            Ok(())
        })
        .optional()?
        .is_some();
    let (columns, campaigns) = named_rows(
        connection,
        "SELECT campaign.* FROM campaigns AS campaign \
         LEFT JOIN campaign_locks AS current_lock \
         ON current_lock.campaign_id = campaign.campaign_id \
         AND current_lock.lock_id = campaign.current_lock_id \
         ORDER BY campaign.display_name COLLATE NOCASE, \
         campaign.display_name, campaign.campaign_id",
        &[],
    )?;
    let mut records = Vec::with_capacity(campaigns.len());
    for values in campaigns {
        let row = Named { columns: &columns, values };
        let campaign = CampaignRecord {
            campaign_id: row.text("campaign_id")?,
            display_name: row.text("display_name")?,
            lifecycle: parse(&row.text("lifecycle")?, LifecycleState::parse, "LifecycleState")?,
            writer: parse(&row.text("writer")?, WriterRole::parse, "WriterRole")?,
            revision: row.int("revision")?,
        };
        let campaign_id = campaign.campaign_id.clone();
        let package_lock = match current_lock_record(connection, &campaign_id) {
            Ok(record) => Some(record.package_lock),
            Err(StoreError::Coded(error)) if error.error_type == Some("CommandRejected") => None,
            Err(error) => return Err(error),
        };
        let mut package_labels = Vec::new();
        if let Some(lock) = &package_lock {
            for entry in lock.entries() {
                let metadata: Option<Value> = connection
                    .query_row(
                        "SELECT metadata_json FROM package_manifests \
                         WHERE package_id = ? AND version = ? AND content_sha256 = ?",
                        [entry.package_id(), entry.version(), entry.sha256()],
                        |row| row.get(0),
                    )
                    .optional()?;
                let metadata = match metadata {
                    Some(text) => sylvia_base::pyjson::loads(&py_str(&text))
                        .map_err(|error| crate::error::value_error(error.message))?,
                    None => Json::Object(JsonMap::new()),
                };
                let label = match &metadata {
                    Json::Object(metadata) => match metadata.get("label") {
                        Some(Json::String(label)) if !pystr::strip(label).is_empty() => {
                            pystr::strip(label).to_owned()
                        }
                        _ => entry.package_id().to_owned(),
                    },
                    _ => entry.package_id().to_owned(),
                };
                package_labels.push((entry.package_id().to_owned(), label));
            }
        }
        let (_, turn_rows) = named_rows(
            connection,
            "SELECT turn.turn_id, turn.status FROM turns AS turn \
             JOIN campaigns AS campaign ON campaign.campaign_id = turn.campaign_id \
             WHERE campaign.campaign_id = ? ORDER BY turn.created_revision, turn.turn_id",
            &[&campaign_id],
        )?;
        let turns = turn_rows
            .iter()
            .map(|row| Ok((py_str(&row[0]), parse(&py_str(&row[1]), TurnStatus::parse, "TurnStatus")?)))
            .collect::<StoreResult<Vec<_>>>()?;
        let (top_level_sessions, handoffs, session_activity_ordinal) = if session_schema_available {
            let (session_columns, session_rows) = named_rows(
                connection,
                "SELECT session.* FROM agent_sessions AS session \
                 JOIN campaigns AS campaign ON campaign.campaign_id = session.campaign_id \
                 WHERE campaign.campaign_id = ? AND session.parent_session_id IS NULL \
                 ORDER BY session.session_id",
                &[&campaign_id],
            )?;
            let sessions = session_rows
                .into_iter()
                .map(|values| session_from_row(&Named { columns: &session_columns, values }))
                .collect::<StoreResult<Vec<_>>>()?;
            let (handoff_columns, handoff_rows) = named_rows(
                connection,
                "SELECT handoff.* FROM builder_dm_handoffs AS handoff \
                 WHERE handoff.campaign_id = ? ORDER BY handoff.handoff_id",
                &[&campaign_id],
            )?;
            let handoffs = handoff_rows
                .into_iter()
                .map(|values| handoff_from_row(&Named { columns: &handoff_columns, values }))
                .collect::<StoreResult<Vec<_>>>()?;
            let ordinal: Value = connection.query_row(
                "SELECT COALESCE(MAX(message.rowid), 0) FROM agent_session_messages AS message \
                 JOIN agent_sessions AS session ON session.session_id = message.session_id \
                 WHERE session.campaign_id = ?",
                [&campaign_id],
                |row| row.get(0),
            )?;
            let ordinal = py_int(&ordinal)
                .ok_or_else(|| crate::error::value_error("invalid literal for int() with base 10"))?;
            (sessions, handoffs, ordinal)
        } else {
            (Vec::new(), Vec::new(), 0)
        };
        let mut name = None;
        if handoffs.is_empty() && campaign.display_name == "New campaign interview" {
            for session in &top_level_sessions {
                if session.role() == WriterRole::Builder {
                    name = Some(interview_name(connection, session.session_id(), &campaign_id)?);
                    break;
                }
            }
        }
        records.push(CampaignIndexRecord {
            campaign,
            package_lock,
            package_labels,
            session_activity_ordinal,
            turns,
            top_level_sessions,
            handoffs,
            interview_name: name.flatten(),
        });
    }
    Ok(records)
}
