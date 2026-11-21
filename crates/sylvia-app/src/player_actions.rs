//! `application/player_actions.py`, first part: the owner's marker for a direct player action.
//!
//! [`player_control_origin`] identifies the declared UI control a player used (a panel action, a
//! saved result-card button or a mechanics choice), and [`valid_player_action_consequence`]
//! recognises the marker that may survive a failed, fully rolled-back mechanic. The stored
//! authority verifier uses the second to refuse a damaged profile, so both are ported ahead of
//! the rest of the file (`parse_player_controls` and the action helpers follow).

use serde_json::json;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    owned_payload, validate_authority_identity, validate_authority_identity_value,
};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::pyops::py_eq;

/// `PLAYER_ACTION_ORIGIN`: the consequence kind of a player action's owner marker.
pub const PLAYER_ACTION_ORIGIN: &str = "sylvia_player_action";

/// `type(value) is int` for the values these checks then bound: a JSON integer within `i64`,
/// never a float or a boolean. A wider integer (Python's unbounded `int`) is outside every
/// bound used here and never equals an `i64` revision, so treating it as "not an int" refuses
/// exactly what Python refuses.
fn exact_int(value: &Json) -> Option<i64> {
    value.as_i64()
}

/// Whether `object`'s keys are exactly `keys`.
fn keys_are(object: &JsonMap, keys: &[&str]) -> bool {
    object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key))
}

/// `player_control_origin(source, expected_revision=..., selection=..., session_id=...)`.
///
/// Identify a declared UI control; the caller cannot supply replacement tool arguments. A
/// malformed identity fails as Python's `ValueError`, every other refusal as `CommandRejected`.
pub fn player_control_origin(
    source: &Json,
    expected_revision: i64,
    selection: Option<&JsonMap>,
    session_id: Option<&str>,
) -> DomainResult<JsonMap> {
    let Json::Object(source) = source else {
        return Err(DomainError::command_rejected("Player control source is invalid"));
    };
    // `selection or {}`: an empty selection is replaced by an equal empty one.
    let selection = selection.cloned().unwrap_or_default();
    let mut origin = JsonMap::new();
    origin.insert("expected_revision".into(), json!(expected_revision));
    origin.insert("selection".into(), Json::Object(selection));
    origin.insert("session_id".into(), session_id.map_or(Json::Null, |id| json!(id)));
    let kind = source.get("kind");
    let is_kind = |name: &str| matches!(kind, Some(Json::String(text)) if text == name);
    let mut extend = |pairs: Vec<(&str, Json)>| -> DomainResult<JsonMap> {
        for (key, value) in pairs {
            origin.insert(key.into(), value);
        }
        owned_payload(&Json::Object(origin.clone()))
    };
    if is_kind("panel") && keys_are(source, &["kind", "surface_id", "action_id"]) {
        validate_authority_identity_value(&source["surface_id"], "UI surface identity", false)?;
        validate_authority_identity_value(&source["action_id"], "UI surface action identity", false)?;
        return extend(vec![
            ("control", json!("panel")),
            ("surface_id", source["surface_id"].clone()),
            ("panel_action_id", source["action_id"].clone()),
        ]);
    }
    if is_kind("result_card") && keys_are(source, &["kind", "action_id", "button_index"]) {
        validate_authority_identity_value(&source["action_id"], "Saved card action identity", false)?;
        let index = &source["button_index"];
        if !exact_int(index).is_some_and(|index| (0..8).contains(&index)) {
            return Err(DomainError::command_rejected("Saved card button index is invalid"));
        }
        return extend(vec![
            ("control", json!("result_card")),
            ("source_action_id", source["action_id"].clone()),
            ("button_index", index.clone()),
        ]);
    }
    if is_kind("mechanics_choice")
        && keys_are(
            source,
            &["kind", "action_id", "choice_id", "choice_sequence", "option_id", "resolution_id"],
        )
    {
        for key in ["action_id", "choice_id", "option_id", "resolution_id"] {
            validate_authority_identity_value(&source[key], &format!("Mechanics choice {key}"), false)?;
        }
        let sequence = &source["choice_sequence"];
        if !exact_int(sequence).is_some_and(|sequence| (0..64).contains(&sequence)) {
            return Err(DomainError::command_rejected("Mechanics choice sequence is invalid"));
        }
        return extend(vec![
            ("control", json!("mechanics_choice")),
            ("source_action_id", source["action_id"].clone()),
            ("choice_id", source["choice_id"].clone()),
            ("choice_sequence", sequence.clone()),
            ("option_id", source["option_id"].clone()),
            ("resolution_id", source["resolution_id"].clone()),
        ]);
    }
    Err(DomainError::command_rejected("Player control source is invalid"))
}

/// `valid_player_action_consequence(payload, expected_revision=...)`: recognise the owner
/// marker that may survive a failed, fully rolled-back mechanic.
///
/// The marker must be exactly what [`player_control_origin`] builds for the control it names,
/// plus the Ruleset's confirmation and response policy.
pub fn valid_player_action_consequence(payload: &JsonMap, expected_revision: i64) -> bool {
    let Some(Json::Object(origin)) = payload.get("origin") else {
        return false;
    };
    if !keys_are(payload, &["kind", "origin"]) || payload.get("kind") != Some(&json!(PLAYER_ACTION_ORIGIN)) {
        return false;
    }
    let get = |key: &str| origin.get(key).unwrap_or(&Json::Null);
    let (Some(revision), Json::Object(selection), Json::Object(policy)) =
        (exact_int(get("expected_revision")), get("selection"), get("policy"))
    else {
        return false;
    };
    let one_of = |value: &Json, allowed: &[&str]| matches!(value, Json::String(text) if allowed.contains(&text.as_str()));
    if revision != expected_revision
        || revision < 0
        || !keys_are(policy, &["confirmation", "response"])
        || !one_of(&policy["confirmation"], &["none", "required"])
        || !one_of(&policy["response"], &["on_request", "automatic"])
    {
        return false;
    }
    let session_id = get("session_id");
    let control = get("control");
    let source = if control == &json!("mechanics_choice") {
        json!({
            "kind": "mechanics_choice", "action_id": get("source_action_id"), "choice_id": get("choice_id"),
            "choice_sequence": get("choice_sequence"), "option_id": get("option_id"),
            "resolution_id": get("resolution_id"),
        })
    } else if control == &json!("panel") {
        json!({"kind": "panel", "surface_id": get("surface_id"), "action_id": get("panel_action_id")})
    } else {
        json!({"kind": "result_card", "action_id": get("source_action_id"), "button_index": get("button_index")})
    };
    let session_id = match session_id {
        Json::Null => None,
        Json::String(text) => {
            match validate_authority_identity(text, "Player control session identity", false) {
                Ok(text) => Some(text),
                Err(_) => return false,
            }
        }
        _ => return false,
    };
    let Ok(mut expected) = player_control_origin(&source, expected_revision, Some(selection), session_id)
    else {
        return false;
    };
    expected.insert("policy".into(), Json::Object(policy.clone()));
    py_eq(&Json::Object(origin.clone()), &Json::Object(expected))
}
