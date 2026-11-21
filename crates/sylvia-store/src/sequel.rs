//! Campaign adventures: the accepted Campaign Plan lineage of one campaign.
//!
//! Python: `sqlite_sequel.adventure_rows` and `verify_sequel`, on lane 1's `campaign_sequel`.
//! `commit_sequel` comes with the plan-admission family.

use crate::campaigns::{named_rows, Named};
use crate::error::{value_error, StoreError, StoreResult};
use rusqlite::types::Value;
use rusqlite::Connection;
use std::collections::HashSet;
use sylvia_base::Json;
use sylvia_base::JsonMap;
use sylvia_domain::pyops::{py_get_attr, py_item, py_type_name};

pub use sylvia_domain::campaign_sequel::MAX_ADVENTURES;

/// One `campaign_plans` row, as `sqlite3.Row` gives it.
#[derive(Debug, Clone, PartialEq)]
pub struct PlanRow {
    pub columns: Vec<String>,
    pub values: Vec<Value>,
}

impl PlanRow {
    pub(crate) fn named(&self) -> Named<'_> {
        Named { columns: &self.columns, values: self.values.clone() }
    }

    /// `row["plan_id"]`.
    pub fn plan_id(&self) -> StoreResult<String> {
        self.named().text("plan_id")
    }

    /// `json.loads(row["content_json"])`.
    pub fn content(&self) -> StoreResult<Json> {
        json_loads(&self.named().text("content_json")?)
    }
}

/// `json.loads(text)`: a `JSONDecodeError` is a `ValueError`.
pub(crate) fn json_loads(text: &str) -> StoreResult<Json> {
    sylvia_base::pyjson::loads(text).map_err(crate::error::json_error)
}

/// A JSON value bound as a SQLite parameter, as `sqlite3` binds the object it parses to.
pub(crate) fn bind_json(value: &Json) -> StoreResult<Value> {
    Ok(match value {
        Json::Null => Value::Null,
        Json::Bool(flag) => Value::Integer(i64::from(*flag)),
        Json::Number(number) => match (number.as_i64(), number.as_f64()) {
            (Some(integer), _) => Value::Integer(integer),
            (None, Some(_)) if number.is_u64() => {
                return Err(StoreError::Other("Python int too large to convert to SQLite INTEGER".into()))
            }
            (None, Some(float)) => Value::Real(float),
            (None, None) => return Err(StoreError::Other("unsupported number".into())),
        },
        Json::String(text) => Value::Text(text.clone()),
        other => {
            return Err(StoreError::Other(format!(
                "Error binding parameter 1: type '{}' is not supported",
                py_type_name(other)
            )))
        }
    })
}

fn first_plan(
    connection: &Connection,
    query: &str,
    values: &[&dyn rusqlite::ToSql],
) -> StoreResult<Option<PlanRow>> {
    let (columns, mut rows) = named_rows(connection, query, values)?;
    Ok(if rows.is_empty() { None } else { Some(PlanRow { columns, values: rows.swap_remove(0) }) })
}

/// `adventure_rows`: follow only the accepted append lineage, oldest first; earlier Builder
/// drafts are not adventures.
pub fn adventure_rows(connection: &Connection, campaign_id: &str) -> StoreResult<Vec<PlanRow>> {
    let mut row = first_plan(
        connection,
        "SELECT * FROM campaign_plans WHERE campaign_id = ? \
         AND acceptance_status = 'accepted' ORDER BY plan_revision DESC LIMIT 1",
        &[&campaign_id],
    )?;
    let mut result: Vec<PlanRow> = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    while let Some(current) = row {
        let named = current.named();
        let plan_id = named.text("plan_id")?;
        if seen.contains(&plan_id) || result.len() >= MAX_ADVENTURES {
            return Err(value_error("Campaign adventure lineage is invalid or exceeds its bound"));
        }
        seen.insert(plan_id);
        let content = current.content()?;
        let revision = named.int("plan_revision");
        result.push(current);
        let Some(Json::Object(metadata)) = py_get_attr(&content, "sequel")? else { break };
        let previous_id = bind_json(py_item(&Json::Object(metadata.clone()), "previous_plan_id")?)?;
        let previous = first_plan(
            connection,
            "SELECT * FROM campaign_plans WHERE plan_id = ? AND campaign_id = ?",
            &[&previous_id, &campaign_id],
        )?;
        let unavailable = || value_error("Campaign adventure predecessor is unavailable");
        let Some(previous) = previous else { return Err(unavailable()) };
        if previous.named().int("plan_revision")? >= revision? {
            return Err(unavailable());
        }
        row = Some(previous);
    }
    result.reverse();
    Ok(result)
}

fn item<'a>(value: &'a Json, key: &str) -> StoreResult<&'a Json> {
    Ok(sylvia_domain::pyops::py_item(value, key)?)
}

/// `value + 1` for a stored JSON number, with Python's `TypeError` for anything else.
fn plus_one(value: &Json) -> StoreResult<Json> {
    match value {
        Json::Bool(flag) => Ok(Json::from(i64::from(*flag) + 1)),
        Json::Number(number) => match (number.as_i64(), number.as_f64()) {
            (Some(integer), _) => integer
                .checked_add(1)
                .map(Json::from)
                .ok_or_else(|| StoreError::Other("integer result exceeds the supported 64-bit range".into())),
            (None, Some(float)) => Ok(Json::from(float + 1.0)),
            _ => Err(StoreError::Other("unsupported operand type(s) for +".into())),
        },
        other => Err(StoreError::Other(format!(
            "unsupported operand type(s) for +: '{}' and 'int'",
            py_type_name(other)
        ))),
    }
}

/// `list(dict.fromkeys(items))`: first occurrences in order, by Python equality; an unhashable
/// item is a `TypeError`.
fn unique(items: Vec<Json>) -> StoreResult<Vec<Json>> {
    let mut kept: Vec<Json> = Vec::new();
    for value in items {
        sylvia_domain::pyops::py_require_hashable(&value)?;
        if !kept.iter().any(|existing| sylvia_domain::pyops::py_eq(existing, &value)) {
            kept.push(value);
        }
    }
    Ok(kept)
}

/// `verify_sequel`: the exact retained source, reviewed mapping and predecessor of a sequel
/// Plan, without library access.
pub fn verify_sequel(
    plan: &sylvia_domain::session::CampaignPlan,
    previous: &sylvia_domain::session::CampaignPlan,
) -> StoreResult<()> {
    use sylvia_domain::campaign_sequel::{digest, json_object, prepare_story, review_payload};
    use sylvia_domain::pyops::{py_eq, py_get_attr, py_iter};
    let (f, p) = (plan.fields(), previous.fields());
    let content = Json::Object(plan.content().clone());
    let previous_content = Json::Object(previous.content().clone());
    let metadata = Json::Object(json_object(item(&content, "sequel")?)?.clone());
    let previous_story = Json::Object(json_object(item(&previous_content, "campaign_plan")?)?.clone());
    let empty = Json::Object(JsonMap::new());
    let previous_metadata =
        Json::Object(json_object(py_get_attr(&previous_content, "sequel")?.unwrap_or(&empty))?.clone());
    let (basis, request) = (item(&metadata, "preview")?, item(&metadata, "request")?);
    review_payload(item(request, "review")?)?;
    let text = |value: &str| Json::from(value);
    let previous_number =
        py_get_attr(py_get_attr(&previous_metadata, "preview")?.unwrap_or(&empty), "number")?
            .cloned()
            .unwrap_or_else(|| Json::from(1));
    let differs = !py_eq(item(&metadata, "previous_plan_id")?, &text(&p.plan_id))
        || f.campaign_id != p.campaign_id
        || f.package_lock_id != p.package_lock_id
        || !py_eq(item(basis, "previous_plan_sha256")?, &text(&p.content_sha256))
        || !py_eq(item(basis, "previous_plan_id")?, &text(&p.plan_id))
        || !py_eq(item(basis, "expected_revision")?, &Json::from(f.resulting_campaign_revision - 1))
        || !py_eq(item(request, "expected_revision")?, item(basis, "expected_revision")?)
        || !py_eq(item(request, "source_id")?, item(basis, "source_id")?)
        || !py_eq(item(&metadata, "source_id")?, item(basis, "source_id")?)
        || !py_eq(item(request, "session_id")?, &text(&f.source_session_id))
        || !py_eq(item(basis, "session_id")?, &text(&f.source_session_id))
        || !py_eq(item(basis, "campaign_id")?, &text(&f.campaign_id))
        || !py_eq(item(basis, "number")?, &plus_one(&previous_number)?)
        || f.plan_revision != p.plan_revision + 1
        || !py_eq(item(request, "preview_sha256")?, &text(&digest(basis)))
        || !py_eq(&text(&digest(item(&metadata, "source_plan")?)), item(basis, "source_plan_sha256")?)
        || !py_eq(
            &text(&digest(item(&content, "campaign_plan_markdown")?)),
            item(basis, "source_markdown_sha256")?,
        )
        || !py_eq(item(item(&metadata, "source_plan")?, "bindings")?, item(&previous_story, "bindings")?);
    if differs {
        return Err(value_error("Campaign sequel source or review identity is invalid"));
    }
    let mut identity = JsonMap::new();
    identity.insert("campaign".into(), text(&f.campaign_id));
    identity.insert("operation".into(), item(request, "operation_id")?.clone());
    if f.plan_id != format!("campaign-sequel-{}", &digest(&Json::Object(identity))[..32]) {
        return Err(value_error("Campaign sequel request identity is invalid"));
    }
    let number = match item(basis, "number")? {
        Json::Number(number) if number.is_i64() => number.as_i64().unwrap_or_default(),
        other => {
            return Err(StoreError::Other(format!(
                "prepare_story needs an integer number, not {}",
                py_type_name(other)
            )))
        }
    };
    let (prepared, mapping) = prepare_story(json_object(item(&metadata, "source_plan")?)?, number)?;
    let mut expected = prepared.clone();
    let prepared_json = Json::Object(prepared);
    let mut boundaries =
        py_iter(py_get_attr(&previous_story, "boundaries")?.unwrap_or(&Json::Array(Vec::new())))?;
    boundaries
        .extend(py_iter(py_get_attr(&prepared_json, "boundaries")?.unwrap_or(&Json::Array(Vec::new())))?);
    expected.insert("boundaries".into(), Json::Array(unique(boundaries)?));
    let identity_mapping = python_dict(item(&metadata, "identity_mapping")?)?;
    if digest(&Json::Object(expected)) != digest(item(&content, "campaign_plan")?)
        || identity_mapping.is_none_or(|stored| !py_eq(&Json::Object(stored), &Json::Object(mapping)))
    {
        return Err(value_error("Campaign sequel changed its reviewed preparation or identity mapping"));
    }
    Ok(())
}

/// `dict(value)` of a stored JSON value: an object as it is, or a list of key-value pairs (a
/// two-item list, or a two-character string); anything else fails as Python's `dict()` does.
/// A key that is not text cannot equal a stored mapping's key, so it is kept apart.
fn python_dict(value: &Json) -> StoreResult<Option<JsonMap>> {
    let items = match value {
        Json::Object(map) => return Ok(Some(map.clone())),
        Json::Array(items) => items,
        other => return Err(StoreError::Other(format!("'{}' object is not iterable", py_type_name(other)))),
    };
    let mut map = JsonMap::new();
    let mut comparable = true;
    for (index, element) in items.iter().enumerate() {
        let (key, entry) = match element {
            Json::Array(pair) if pair.len() == 2 => (pair[0].clone(), pair[1].clone()),
            Json::String(text) if text.chars().count() == 2 => {
                let mut chars = text.chars();
                (
                    Json::from(chars.next().unwrap_or_default().to_string()),
                    Json::from(chars.next().unwrap_or_default().to_string()),
                )
            }
            Json::Array(pair) => {
                return Err(value_error(format!(
                    "dictionary update sequence element #{index} has length {}; 2 is required",
                    pair.len()
                )))
            }
            Json::String(text) => {
                return Err(value_error(format!(
                    "dictionary update sequence element #{index} has length {}; 2 is required",
                    text.chars().count()
                )))
            }
            other => {
                return Err(StoreError::Other(format!(
                    "cannot convert dictionary update sequence element #{index} to a sequence ({})",
                    py_type_name(other)
                )))
            }
        };
        sylvia_domain::pyops::py_require_hashable(&key)?;
        match key {
            Json::String(key) => {
                map.insert(key, entry);
            }
            _ => comparable = false,
        }
    }
    Ok(comparable.then_some(map))
}
