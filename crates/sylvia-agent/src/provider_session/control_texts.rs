//! The GM turn's application-owned control messages, from `agent/_provider_session.py`:
//! `_engine_turn_control_message`, `_campaign_preparation_message`,
//! `_gm_decision_control_message`, `_player_boundary_narration_message`,
//! `_preparation_keeps_intent_pending`, `_campaign_file_disposition_message` and
//! `_campaign_file_updated_narration_message`.
//!
//! Each message is a fixed text around one JSON part. The texts are model-read, so they are
//! exported from the Python functions into `gm_texts.json` (`<name>_prefix`, `<name>_suffix`)
//! by `generate_gm_texts.py`; only the JSON part is built here.

use super::texts::{gm_text, system};
use crate::stateless_mcp::ModelMechanicsTool;
use indexmap::IndexMap;
use serde_json::json;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_domain::authority::{ModelDecisionBoundary, ModelToolBehavior};

/// `_PREPARATION_INTENT_ARGUMENT`.
pub const PREPARATION_INTENT_ARGUMENT: &str = "player_intent_after_preparation";

/// `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
fn compact(value: &Json) -> String {
    pyjson::dumps(value, Dump::CANONICAL)
}

/// The message `name` around its JSON part.
fn control(name: &str, data: &str) -> JsonMap {
    let prefix = gm_text(&format!("{name}_prefix"));
    let suffix = gm_text(&format!("{name}_suffix"));
    system(format!("{prefix}{data}{suffix}"))
}

fn text_or_null(value: Option<&str>) -> Json {
    value.map_or(Json::Null, Json::from)
}

/// `_engine_turn_control_message(aliases)`: the Ruleset-required lifecycle operations beside
/// the current inference, or `None` when there are none.
pub fn engine_turn_control_message(aliases: &IndexMap<String, ModelMechanicsTool>) -> Option<JsonMap> {
    let required: Vec<Json> = aliases
        .iter()
        .filter(|(_, tool)| tool.model_behavior == ModelToolBehavior::RequiredEngineTransition)
        .map(|(alias, tool)| {
            json!({"provider_tool_name": alias, "operation": tool.name, "description": tool.description})
        })
        .collect();
    if required.is_empty() {
        return None;
    }
    Some(control("engine_turn_control", &compact(&json!({"required_before_narration": required}))))
}

/// `_campaign_preparation_message(boundary)`: required starting choices, kept apart from story
/// narration and player speech.
pub fn campaign_preparation_message(boundary: &ModelDecisionBoundary) -> JsonMap {
    let preparation = boundary.preparation.clone().map_or(Json::Null, Json::Object);
    control("campaign_preparation", &pyjson::dumps(&preparation, Dump::DEFAULT.sorted(true).ascii(false)))
}

/// `_gm_decision_control_message(boundary)`: one GM-owned decision, without fabricated player
/// speech.
pub fn gm_decision_control_message(boundary: &ModelDecisionBoundary) -> JsonMap {
    let eligible: Vec<Json> = boundary
        .eligible_actors
        .iter()
        .map(|actor| {
            json!({"actor_id": actor.actor_id, "actor_name": actor.actor_name, "actor_state": actor.actor_state})
        })
        .collect();
    let data = json!({
        "decision_owner": boundary.owner.as_str(),
        "actor_id": text_or_null(boundary.actor_id.as_deref()),
        "actor_name": text_or_null(boundary.actor_name.as_deref()),
        "actor_state": boundary.actor_state,
        "active_side_id": text_or_null(boundary.active_side_id.as_deref()),
        "focused_actor_id": text_or_null(boundary.focused_actor_id.as_deref()),
        "eligible_actors": eligible,
        "campaign_revision": boundary.campaign_revision,
    });
    control("gm_decision", &compact(&data))
}

/// `_player_boundary_narration_message(boundary, current_player_action=...,
/// committed_operations=..., player_intent_pending=...)`: Player control kept while an
/// already declared action finishes.
pub fn player_boundary_narration_message(
    boundary: &ModelDecisionBoundary,
    current_player_action: Option<&str>,
    committed_operations: &[String],
    player_intent_pending: bool,
) -> JsonMap {
    let data = json!({
        "decision_owner": boundary.owner.as_str(),
        "actor_id": text_or_null(boundary.actor_id.as_deref()),
        "actor_name": text_or_null(boundary.actor_name.as_deref()),
        "active_side_id": text_or_null(boundary.active_side_id.as_deref()),
        "focused_actor_id": text_or_null(boundary.focused_actor_id.as_deref()),
        "eligible_actor_ids": boundary.eligible_actors.iter().map(|actor| actor.actor_id.as_str()).collect::<Vec<_>>(),
        "campaign_revision": boundary.campaign_revision,
        "current_player_action": text_or_null(current_player_action),
        "committed_operations_for_this_request": committed_operations,
        "declared_player_intent_pending": player_intent_pending,
    });
    control("player_boundary", &compact(&data))
}

/// `_preparation_keeps_intent_pending(scope, pending=...)`: validates the GM's request scope
/// without interpreting the player's prose. `None` is `_UNSPECIFIED_PREPARATION_INTENT`.
pub fn preparation_keeps_intent_pending(scope: Option<&Json>, pending: bool) -> CodedResult<bool> {
    let text = scope.map(Json::as_str);
    if scope.is_none() || text == Some(Some("unresolved_action")) {
        return Ok(true);
    }
    if text == Some(Some("await_player_choice")) && !pending {
        return Ok(false);
    }
    Err(CodedError::new("provider_player_intent_scope_invalid", gm_text("preparation_scope_invalid"))
        .with_error_type("CommandRejected"))
}

/// The task data of the Campaign File messages.
fn campaign_file_task(opening_footing: Option<&JsonMap>, current_player_action: Option<&str>) -> String {
    let task = match opening_footing {
        Some(footing) => json!({"opening_footing": footing}),
        None => json!({"current_player_action": current_player_action.unwrap_or("")}),
    };
    compact(&task)
}

/// `_campaign_file_disposition_message(opening_footing=None, current_player_action=None)`: the
/// same GM settles the durable claims in its pending response.
pub fn campaign_file_disposition_message(
    opening_footing: Option<&JsonMap>,
    current_player_action: Option<&str>,
) -> JsonMap {
    control("campaign_file_disposition", &campaign_file_task(opening_footing, current_player_action))
}

/// `_campaign_file_updated_narration_message(opening_footing=None, current_player_action=None)`:
/// the same GM narrates only after its tool-only Campaign File update succeeds.
pub fn campaign_file_updated_narration_message(
    opening_footing: Option<&JsonMap>,
    current_player_action: Option<&str>,
) -> JsonMap {
    control("campaign_file_updated_narration", &campaign_file_task(opening_footing, current_player_action))
}
