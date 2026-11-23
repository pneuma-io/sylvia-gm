//! `agent/_provider_session.py`, the exact facts a GM narrates from after committed work: an
//! executed action sequence's saved outcomes (`_action_sequence_narration_facts`).

use super::reads::TurnOwner;
use serde_json::{json, Value as Json};
use sylvia_base::{CodedError, CodedResult, JsonMap};
use sylvia_domain::authority::CanonicalEventType;

fn rejected(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("CommandRejected")
}

/// `_action_sequence_narration_facts(owner, campaign_id, result)`: the exact committed queue
/// outcomes, read back (their stored rolls and costs are never executed here). `None` when the
/// sequence committed nothing.
pub async fn action_sequence_narration_facts(
    owner: &dyn TurnOwner,
    campaign_id: &str,
    result: &JsonMap,
) -> CodedResult<Option<JsonMap>> {
    let action_ids = match result.get("committed_action_ids") {
        None => Vec::new(),
        Some(Json::Array(ids)) => ids.clone(),
        Some(_) => return Err(rejected("Action sequence committed identities are invalid")),
    };
    if action_ids.is_empty() {
        return Ok(None);
    }
    let snapshot = owner.snapshot(campaign_id).await?;
    let mut committed = Vec::with_capacity(action_ids.len());
    for action_id in action_ids {
        let found = |kind: CanonicalEventType| {
            snapshot.events.iter().find(|event| {
                // `event.action_id == action_id`: a JSON null equals a missing identity.
                event.event_type == kind
                    && match &action_id {
                        Json::String(id) => event.action_id.as_deref() == Some(id.as_str()),
                        Json::Null => event.action_id.is_none(),
                        _ => false,
                    }
            })
        };
        let (Some(terminal), Some(tool_result)) =
            (found(CanonicalEventType::ActionCommitted), found(CanonicalEventType::ToolResult))
        else {
            return Err(CodedError::new(
                "canonical_mechanic_result_unavailable",
                "Canonical action sequence result is unavailable",
            )
            .with_error_type("CommandRejected"));
        };
        committed.push(json!({
            "action_id": action_id,
            "status": "committed",
            "canonical_sequence": terminal.sequence,
            "result": tool_result.safe_payload.get("result").cloned().unwrap_or_else(|| json!({})),
        }));
    }
    let mut facts = JsonMap::new();
    facts.insert("tool".into(), json!("execute_action_sequence"));
    facts.insert("sequence_id".into(), result.get("sequence_id").cloned().unwrap_or(Json::Null));
    facts.insert("sequence_status".into(), result.get("status").cloned().unwrap_or(Json::Null));
    facts.insert("committed_actions".into(), Json::Array(committed));
    Ok(Some(facts))
}
