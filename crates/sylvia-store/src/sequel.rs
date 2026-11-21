//! Campaign adventures: the accepted Campaign Plan lineage of one campaign.
//!
//! Python: `sqlite_sequel.adventure_rows`. The rest of `sqlite_sequel.py` (sequel admission and
//! `verify_sequel`) comes with the plan-admission family.

use crate::campaigns::{named_rows, Named};
use crate::error::{value_error, StoreError, StoreResult};
use rusqlite::types::Value;
use rusqlite::Connection;
use std::collections::HashSet;
use sylvia_base::Json;
use sylvia_domain::pyops::{py_get_attr, py_item, py_type_name};

/// `domain/campaign_sequel.MAX_ADVENTURES`. Lane 1 has not ported `campaign_sequel.py` yet; this
/// is its one constant the lineage needs.
pub const MAX_ADVENTURES: usize = 32;

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
