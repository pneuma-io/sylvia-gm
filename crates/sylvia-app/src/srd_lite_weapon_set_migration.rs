//! `application/srd_lite_weapon_set_migration.py`: Lite 0.2 to 0.3 migration for
//! `AuthorityOwner.activate_package`.
//!
//! The callback returns detached state. The activation transaction changes the pin and commits
//! it atomically; this module never edits a live database. Saved state reaches it as untyped
//! JSON, so Python's operator errors on a malformed value are raised with their text.

use serde_json::json;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::pyops::{py_contains, py_eq, py_get_attr, py_item, py_iter, py_truthy, py_type_name};

/// `PACKAGE_ID`.
pub const PACKAGE_ID: &str = "sylvia.srd-lite.bg3";
/// `MIGRATION_ID`.
pub const MIGRATION_ID: &str = "srd-lite-saved-weapon-sets-v1";

/// `mapping.values()`, with Python's `AttributeError` for a value that is not a `dict`.
fn values_of(value: &mut Json) -> DomainResult<Vec<&mut Json>> {
    match value {
        Json::Object(map) => Ok(map.values_mut().collect()),
        other => Err(DomainError::uncaught(
            "AttributeError",
            format!("'{}' object has no attribute 'values'", py_type_name(other)),
        )),
    }
}

/// `[item + ":1" for item in sources if item in weapons]`.
fn light_attack_items(sources: &Json, weapons: &Json) -> DomainResult<Json> {
    let mut items = Vec::new();
    for item in py_iter(sources)? {
        if !py_contains(&item, weapons)? {
            continue;
        }
        match &item {
            Json::String(text) => items.push(Json::String(format!("{text}:1"))),
            other => {
                return Err(DomainError::type_error(format!(
                    "unsupported operand type(s) for +: '{}' and 'str'",
                    py_type_name(other)
                )))
            }
        }
    }
    Ok(Json::Array(items))
}

/// Migrate one character actor in place, as the loop body does.
fn migrate_actor(actor: &mut JsonMap, weapons: &Json) -> DomainResult<()> {
    if let Some(sets) = actor.get("weapon_sets").filter(|sets| !sets.is_null()) {
        if !py_get_attr(sets, "format")?.is_some_and(|format| py_eq(format, &json!(1))) {
            return Err(DomainError::value("Unsupported saved equipment format."));
        }
        return Ok(());
    }
    // `(actor.get("worn_shield") or {}).get("inventory_item")`.
    let worn =
        actor.get("worn_shield").filter(|shield| py_truthy(shield)).cloned().unwrap_or_else(|| json!({}));
    let shield = py_get_attr(&worn, "inventory_item")?.cloned().unwrap_or(Json::Null);
    let mut pairs = json!({
        "format": 1,
        "active_set": null,
        "melee": {"main_hand": null, "off_hand": shield},
        "ranged": {"main_hand": null, "off_hand": null},
    });
    let inventory = actor.get("inventory").cloned().unwrap_or_else(|| json!([]));
    let held = actor.get("held_items").cloned().unwrap_or_else(|| json!([]));
    let mut actual = Vec::new();
    for item in py_iter(&held)? {
        if py_contains(&item, &inventory)? && py_contains(&item, weapons)? {
            actual.push(item);
        }
    }
    if let [item] = actual.as_slice() {
        let info = py_item(weapons, item.as_str().expect("a dict key is text"))?;
        let two = py_contains(&json!("two_handed"), py_item(info, "properties")?)?;
        let name = if py_eq(py_item(info, "kind")?, &json!("ranged")) { "ranged" } else { "melee" };
        if name == "ranged" || !(two && py_truthy(&shield)) {
            let off_hand = if two {
                item.clone()
            } else if name == "melee" {
                shield.clone()
            } else {
                Json::Null
            };
            pairs[name] = json!({"main_hand": item, "off_hand": off_hand});
            pairs["active_set"] = Json::from(name);
        }
    }
    actor.insert("weapon_sets".into(), pairs);
    // Preserve existing per-turn eligibility; this adds a receipt rather than restoring
    // Light/Nick, actions, ammunition or resources. The default is built before `setdefault`.
    let sources = actor.get("light_attack_source_weapons").cloned().unwrap_or_else(|| json!([]));
    let default = light_attack_items(&sources, weapons)?;
    actor.entry("light_attack_source_items").or_insert(default);
    Ok(())
}

/// `migrate_state(state, weapons)`: the migrated detached copy of `state`.
pub fn migrate_state(state: &JsonMap, weapons: &Json) -> DomainResult<JsonMap> {
    let mut copied = state.clone();
    let story = copied.get(&format!("{PACKAGE_ID}.state")).cloned().unwrap_or_else(|| json!({}));
    if py_get_attr(&story, "pending_srd_action_stack")?.is_some_and(py_truthy) {
        return Err(DomainError::value("Complete the saved pending mechanic before upgrading equipment."));
    }
    if let Some(entities) = copied.get_mut(&format!("{PACKAGE_ID}.entities")) {
        for actor in values_of(entities)? {
            let Json::Object(actor) = actor else { continue };
            if !actor.get("creature_kind").is_some_and(|kind| py_eq(kind, &json!("character"))) {
                continue;
            }
            migrate_actor(actor, weapons)?;
        }
    }
    Ok(copied)
}
