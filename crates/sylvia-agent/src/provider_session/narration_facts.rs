//! `agent/_provider_session.py`, the exact facts a GM narrates from after committed work: an
//! executed action sequence's saved outcomes (`_action_sequence_narration_facts`), one
//! mechanic's receipt (`_mechanics_narration_facts`) and the public result of a participant's
//! isolated action (`_public_action_result`).

use super::reads::TurnOwner;
use indexmap::IndexMap;
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

/// The context keys a mechanic's receipt exposes to post-tool narration, in Python's order;
/// each is written without its leading `_`.
const CONTEXT_KEYS: [&str; 25] = [
    "_test_kind",
    "_ability_modifier",
    "_proficiency_bonus",
    "_proficiency_modifier",
    "_modifier",
    "_d20_test",
    "_adjusted_total",
    "_outcome",
    "_effective_advantage",
    "_effective_disadvantage",
    "_automatic_failure_reason",
    "_exhaustion_penalty",
    "_guidance_bonus",
    "_lasting_d20_modifier",
    "_attack_outcome",
    "_attack_roll",
    "_attack_adjusted_total",
    "_effective_target_armor_class",
    "_critical_hit",
    "_damage_expression",
    "_damage_roll",
    "_damage_modifier",
    "_graze_damage",
    "_weapon",
    "_weapon_damage_type",
];

/// `isinstance(value, int)`: a JSON integer, or a boolean (a Python `int` subclass).
fn python_int(value: Option<&Json>) -> Option<i64> {
    match value? {
        Json::Bool(flag) => Some(i64::from(*flag)),
        Json::Number(number) => number.as_i64(),
        _ => None,
    }
}

/// A non-empty string, as `isinstance(value, str) and value` reads it.
fn nonempty(value: Option<&Json>) -> Option<&str> {
    value.and_then(Json::as_str).filter(|text| !text.is_empty())
}

/// `{first_actor: ..., second_actor: ...}`: building the dict refuses an unhashable key.
fn hashable_key(value: &Json) -> CodedResult<()> {
    let name = match value {
        Json::Object(_) => "dict",
        Json::Array(_) => "list",
        _ => return Ok(()),
    };
    Err(CodedError::new("internal_error", format!("unhashable type: '{name}'")).with_error_type("TypeError"))
}

/// `_mechanics_narration_facts(tool_name, result, current_entities, previous_entities,
/// parameters, current_combat, entity_display_names)`: a compact exact basis for post-tool
/// narration.
pub fn mechanics_narration_facts(
    tool_name: &str,
    result: &Json,
    current_entities: &JsonMap,
    previous_entities: &JsonMap,
    parameters: Option<&JsonMap>,
    current_combat: Option<&JsonMap>,
    entity_display_names: Option<&IndexMap<String, String>>,
) -> CodedResult<JsonMap> {
    let empty = IndexMap::new();
    let display_names = entity_display_names.unwrap_or(&empty);
    let mut facts = JsonMap::new();
    facts.insert("tool".into(), json!(tool_name));
    facts.insert("current_entities".into(), Json::Object(current_entities.clone()));
    if let Some(actor_id) = nonempty(current_combat.and_then(|combat| combat.get("current_actor"))) {
        facts.insert("current_actor_id".into(), json!(actor_id));
        let actor_name = display_names.get(actor_id).cloned().or_else(|| {
            current_entities
                .get(actor_id)
                .and_then(Json::as_object)
                .and_then(|actor| actor.get("name"))
                .and_then(Json::as_str)
                .map(str::to_owned)
        });
        if let Some(name) = actor_name.filter(|name| !name.is_empty()) {
            facts.insert("current_actor_name".into(), json!(name));
        }
    }
    if let Some(combat) = current_combat {
        let null = Json::Null;
        let first_actor = combat.get("initiative_first_actor").unwrap_or(&null);
        let second_actor = combat.get("initiative_second_actor").unwrap_or(&null);
        hashable_key(first_actor)?;
        hashable_key(second_actor)?;
        // `totals.get(actor_id)`: an equal second key replaces the first total.
        let total_of = |actor_id: &str| -> Option<&Json> {
            if second_actor.as_str() == Some(actor_id) {
                combat.get("initiative_second_total")
            } else if first_actor.as_str() == Some(actor_id) {
                combat.get("initiative_first_total")
            } else {
                None
            }
        };
        let order = [nonempty(combat.get("order_first")), nonempty(combat.get("order_second"))];
        if let [Some(first), Some(second)] = order {
            let mut initiative_order = Vec::new();
            for actor_id in [first, second] {
                let mut item = JsonMap::new();
                item.insert("actor_id".into(), json!(actor_id));
                if let Some(name) = display_names.get(actor_id).filter(|name| !name.is_empty()) {
                    item.insert("name".into(), json!(name));
                }
                if let Some(total) = total_of(actor_id).filter(|total| python_int(Some(total)).is_some()) {
                    item.insert("total".into(), total.clone());
                }
                initiative_order.push(Json::Object(item));
            }
            facts.insert("initiative_order".into(), Json::Array(initiative_order));
        }
    }
    let Json::Object(result) = result else { return Ok(facts) };
    facts.insert("status".into(), result.get("status").cloned().unwrap_or(Json::Null));
    let Some(Json::Object(context)) = result.get("context") else { return Ok(facts) };
    if let Some(parameters) = parameters {
        for key in ["ability", "skill", "tool", "kind", "target_number"] {
            if let Some(value) = parameters.get(key) {
                facts.insert(key.into(), value.clone());
            }
        }
    }
    for key in CONTEXT_KEYS {
        if let Some(value) = context.get(key) {
            facts.insert(key[1..].into(), value.clone());
        }
    }
    let target_id = context.get("_target_id").and_then(Json::as_str);
    let target_before = match target_id {
        Some(target_id) => previous_entities.get(target_id),
        None => context.get("target"),
    };
    let target_after = target_id.and_then(|target_id| current_entities.get(target_id));
    if let Some(target_id) = target_id {
        let target_name = match display_names.get(target_id) {
            Some(name) => Some(name.clone()),
            None => target_after
                .and_then(Json::as_object)
                .and_then(|target| target.get("name"))
                .and_then(Json::as_str)
                .map(str::to_owned),
        };
        if let Some(name) = target_name.filter(|name| !name.is_empty()) {
            facts.insert("target_name".into(), json!(name));
        }
    }
    if let (Some(Json::Object(before)), Some(Json::Object(after))) = (target_before, target_after) {
        let (before_hp, after_hp) = (before.get("hp"), after.get("hp"));
        if let (Some(before_value), Some(after_value)) = (python_int(before_hp), python_int(after_hp)) {
            facts.insert("target_id".into(), target_id.map_or(Json::Null, Json::from));
            facts.insert("target_hit_points_before".into(), before_hp.cloned().unwrap_or(Json::Null));
            facts.insert("target_hit_points_after".into(), after_hp.cloned().unwrap_or(Json::Null));
            facts.insert("hit_point_change".into(), json!(after_value - before_value));
        }
    }
    Ok(facts)
}

/// `_public_action_result(action_id, result)`: a participant's private engine receipt kept out
/// of GM recovery context.
pub fn public_action_result(action_id: Option<&str>, result: &Json) -> CodedResult<Json> {
    if action_id.is_some_and(|action_id| action_id.starts_with("actor-action-")) {
        let Some(Json::Object(public)) = result.as_object().and_then(|result| result.get("context")) else {
            return Err(rejected("Isolated participant action has no published public result"));
        };
        return Ok(json!({"context": public}));
    }
    Ok(result.clone())
}
