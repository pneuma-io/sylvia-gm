//! `participant_history.participant_data_history`: former live character data, recovered from
//! committed `participant_updated` consequences.

use crate::campaigns::named_rows;
use crate::error::{json_error, value_error, StoreError, StoreResult};
use crate::pytext::py_str;
use rusqlite::Connection;
use sylvia_base::{CodedError, Json, JsonMap};
use sylvia_domain::pyops::py_eq;

/// `participant_data_history(connection, campaign_id, participant_id, current)`: `current` first,
/// then each earlier value, walking the contiguous reverse history.
pub(crate) fn participant_data_history(
    connection: &Connection,
    campaign_id: &str,
    participant_id: &str,
    current: JsonMap,
) -> StoreResult<Vec<Json>> {
    let mut values = vec![Json::Object(current)];
    let rows = named_rows(
        connection,
        "SELECT e.safe_payload_json FROM canonical_events e JOIN actions a \
         ON a.campaign_id = e.campaign_id AND a.action_id = e.action_id \
         WHERE e.campaign_id = ? AND e.event_type = 'consequence' \
         AND a.status = 'committed' ORDER BY e.sequence DESC",
        &[&campaign_id],
    )?
    .1;
    for row in rows {
        let payload = sylvia_base::pyjson::loads(&py_str(&row[0])).map_err(json_error)?;
        // Python calls `payload.get`: an `AttributeError` on anything but an object, which no
        // caller catches. (The stored-authority verifier refuses such event payloads first.)
        let Json::Object(payload) = payload else {
            let class = match payload {
                Json::Array(_) => "list",
                Json::String(_) => "str",
                Json::Bool(_) => "bool",
                Json::Null => "NoneType",
                Json::Number(ref number) if number.is_f64() => "float",
                Json::Number(_) => "int",
                Json::Object(_) => unreachable!(),
            };
            return Err(StoreError::Coded(
                CodedError::new("internal_error", format!("'{class}' object has no attribute 'get'"))
                    .with_error_type("AttributeError"),
            ));
        };
        if payload.get("kind") != Some(&Json::String("participant_updated".into()))
            || payload.get("participant_id") != Some(&Json::String(participant_id.into()))
        {
            continue;
        }
        let last = values.last().cloned().unwrap_or(Json::Null);
        let after_matches = payload.get("after").is_some_and(|after| py_eq(after, &last));
        let Some(before @ Json::Object(_)) = payload.get("before") else {
            return Err(value_error("Participant update history is discontinuous"));
        };
        if !after_matches {
            return Err(value_error("Participant update history is discontinuous"));
        }
        values.push(before.clone());
    }
    Ok(values)
}
