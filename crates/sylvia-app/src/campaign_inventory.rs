//! `application/campaign_inventory.py`: the fallback inventory ledger a Campaign File update
//! writes when the active Ruleset has no inventory of its own (`prepare_inventory_payload`,
//! with `inventory_holdings`, `_ledger_holdings` and `_holding_records`).
//!
//! `generic_inventory_surface` is the player panel `campaign_panels` adds when no Ruleset
//! surface shows the inventory.

use std::collections::BTreeSet;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{owned_payload, CampaignSnapshot, CampaignState};
use sylvia_domain::inventory::{transfer_holding, validate_holdings, HoldingTransfer};
use sylvia_domain::inventory::{INVENTORY_ENTITY_KIND, INVENTORY_RECORD_ID};
use sylvia_domain::pyops::{py_eq, py_str};
use sylvia_domain::{DomainError, DomainResult};

fn rejected(message: &str) -> DomainError {
    DomainError::command_rejected(message)
}

fn value(message: &str) -> DomainError {
    DomainError::value(message)
}

/// `_holding_records(value)`: a list of mappings, never scalar inventory values.
fn holding_records(value: Option<&Json>) -> DomainResult<Vec<JsonMap>> {
    match value {
        Some(Json::Array(items)) => items
            .iter()
            .map(|item| item.as_object().cloned().ok_or_else(|| rejected("Inventory holdings are malformed")))
            .collect(),
        _ => Err(rejected("Inventory holdings are malformed")),
    }
}

/// `_ledger_holdings(entities)`: the reserved ledger only; narrative inventory prose is never read.
fn ledger_holdings(entities: &[JsonMap]) -> DomainResult<Vec<JsonMap>> {
    let ledger = entities.iter().find(|item| {
        py_eq(item.get("record_id").unwrap_or(&Json::Null), &Json::from(INVENTORY_RECORD_ID))
            && py_eq(item.get("entity_kind").unwrap_or(&Json::Null), &Json::from(INVENTORY_ENTITY_KIND))
    });
    let Some(ledger) = ledger else { return Ok(Vec::new()) };
    let Some(Json::Object(attributes)) = ledger.get("attributes") else {
        return Err(rejected("Inventory ledger is malformed"));
    };
    holding_records(attributes.get("holdings"))
}

/// `inventory_holdings(snapshot)`: the latest committed fallback ledger.
pub fn inventory_holdings(snapshot: &CampaignSnapshot) -> DomainResult<Vec<JsonMap>> {
    ledger_holdings(&snapshot.continuity.facts)
}

/// `dict[key] = value` on an insertion-ordered mapping: an existing key keeps its place.
fn assign(map: &mut Vec<(String, String)>, key: String, value: String) {
    match map.iter_mut().find(|(known, _)| *known == key) {
        Some(entry) => entry.1 = value,
        None => map.push((key, value)),
    }
}

/// `generic_inventory_surface(snapshot, entities, binding=..., state_target=...)`: one player-safe
/// inventory surface over the selected authoritative representation (the fallback ledger, or the
/// Ruleset's own state field when it binds one).
pub fn generic_inventory_surface(
    snapshot: &CampaignState,
    entities: &[JsonMap],
    binding: Option<&JsonMap>,
    state_target: Option<&JsonMap>,
) -> DomainResult<JsonMap> {
    let text = |item: &JsonMap, key: &str| -> DomainResult<String> {
        Ok(py_str(item.get(key).ok_or_else(|| key_error(key))?))
    };
    let mut visible: Vec<(String, String)> = Vec::new();
    for item in entities {
        if item.get("visibility") == Some(&Json::from("player_known"))
            && !py_eq(item.get("record_id").unwrap_or(&Json::Null), &Json::from(INVENTORY_RECORD_ID))
        {
            assign(&mut visible, text(item, "record_id")?, text(item, "name")?);
        }
    }
    let mut characters: Vec<(String, String)> = Vec::new();
    for participant in &snapshot.participants {
        let fields = participant.fields();
        if fields.kind == "player_character" {
            let name = fields.data.get("name").map_or_else(|| fields.participant_id.clone(), py_str);
            assign(&mut characters, fields.participant_id.clone(), name);
        }
    }
    if binding.is_none() {
        for item in entities {
            let kind = item.get("entity_kind").and_then(Json::as_str);
            let record = py_str(item.get("record_id").unwrap_or(&Json::Null));
            if matches!(kind, Some("person" | "character" | "player_character" | "companion" | "npc")) {
                if let Some((_, name)) = visible.iter().find(|(known, _)| *known == record) {
                    let name = name.clone();
                    assign(&mut characters, record, name);
                }
            }
        }
    }
    for (identity, name) in &characters {
        assign(&mut visible, identity.clone(), name.clone());
    }
    let visible_map: JsonMap =
        visible.iter().map(|(key, value)| (key.clone(), Json::from(value.as_str()))).collect();
    let holdings: Vec<Json> = ledger_holdings(entities)?.into_iter().map(Json::Object).collect();
    let mut projections = Vec::new();
    for (identity, name) in &characters {
        let items = match (binding, state_target) {
            (None, _) => player_inventory_items(&holdings, &visible_map, identity)?,
            (Some(binding), state_target) => {
                let Some(target) = state_target else {
                    return Err(DomainError::uncaught("AssertionError", ""));
                };
                let key = format!("{}.{}", text(target, "namespace")?, text(target, "key")?);
                let state = snapshot.state.get(&key).cloned().unwrap_or_else(|| Json::Object(JsonMap::new()));
                let Json::Object(state) = state else {
                    return Err(rejected("Ruleset inventory state is malformed"));
                };
                let character_state = match state.get(identity.as_str()) {
                    None => JsonMap::new(),
                    Some(Json::Object(map)) => map.clone(),
                    Some(_) => return Err(rejected("Ruleset inventory state is malformed")),
                };
                let field = text(binding, "field")?;
                let values = character_state.get(&field).cloned().unwrap_or_else(|| Json::Array(Vec::new()));
                let format = binding.get("format").ok_or_else(|| key_error("format"))?;
                if format == "holdings" {
                    let records: Vec<Json> =
                        holding_records(Some(&values))?.into_iter().map(Json::Object).collect();
                    player_inventory_items(&records, &visible_map, identity)?
                } else {
                    let Json::Array(values) = values else {
                        return Err(rejected("Ruleset inventory list is malformed"));
                    };
                    if values.iter().any(|value| !value.is_string()) {
                        return Err(rejected("Ruleset inventory list is malformed"));
                    }
                    values
                        .iter()
                        .map(|value| {
                            serde_json::json!({
                                "item_id": value, "name": value, "quantity": 1, "kind": "item",
                                "holder": name, "owner": "Unspecified", "carried": true, "owned": false,
                            })
                        })
                        .collect()
                }
            }
        };
        projections.push(serde_json::json!({"id": identity, "name": name, "items": items}));
    }
    owned_payload(&serde_json::json!({
        "id": "sylvia-inventory",
        "title": "Inventory",
        "icon": "\u{25a3}",
        "renderer": "generic_inventory",
        "group": null,
        "order": 30,
        "default_size": 220,
        "minimum_size": 120,
        "default_placement": "docked",
        "allowed_placements": ["docked", "floating", "window"],
        "actions": [],
        "data": {"characters": projections},
    }))
}

/// `player_inventory(holdings, visible_entities=..., character_id=...)["items"]`.
fn player_inventory_items(
    holdings: &[Json],
    visible: &JsonMap,
    character_id: &str,
) -> DomainResult<Vec<Json>> {
    let inventory = sylvia_domain::inventory::player_inventory(holdings, visible, character_id)?;
    match inventory.get("items") {
        Some(Json::Array(items)) => Ok(items.clone()),
        _ => Err(key_error("items")),
    }
}

fn key_error(key: &str) -> DomainError {
    DomainError::uncaught("KeyError", format!("'{key}'"))
}

/// `prepare_inventory_payload(snapshot, updates, operation_id=..., request_hash=...,
/// pending_entities=...)`: the original prepared result again on a replay, or a new
/// revision-fenced holding update validated against the ledger, with the revision it names.
pub fn prepare_inventory_payload(
    snapshot: &CampaignSnapshot,
    updates: &[&JsonMap],
    operation_id: &str,
    request_hash: &str,
    pending_entities: &[&JsonMap],
) -> DomainResult<(i64, JsonMap)> {
    let [update] = updates else { return Err(rejected("Supply exactly one inventory update batch")) };
    let allowed = ["operation", "expected_revision", "holdings", "transfers"];
    // `type(expected_revision) is not int`: a bool or a float is refused.
    let expected_revision =
        update.get("expected_revision").filter(|value| value.is_i64()).and_then(Json::as_i64);
    let Some(expected_revision) = expected_revision
        .filter(|revision| *revision >= 0 && update.keys().all(|key| allowed.contains(&key.as_str())))
    else {
        return Err(rejected("Inventory update fields or revision are invalid"));
    };
    if let Some(previous) =
        snapshot.continuity.records.iter().find(|record| record.operation_id.as_deref() == Some(operation_id))
    {
        if !py_eq(previous.payload.get("request_hash").unwrap_or(&Json::Null), &Json::from(request_hash)) {
            return Err(DomainError::authority(
                "IdempotencyConflict",
                "Inventory update identity was reused with different content",
            ));
        }
        return Ok((expected_revision, previous.payload.clone()));
    }
    let mut entity_ids: BTreeSet<String> = snapshot
        .state
        .participants
        .iter()
        .map(|participant| participant.participant_id().to_owned())
        .collect();
    for item in &snapshot.continuity.facts {
        if item.contains_key("entity_kind") {
            let record_id = item.get("record_id").ok_or_else(|| key_error("record_id"))?;
            if !py_eq(record_id, &Json::from(INVENTORY_RECORD_ID)) {
                entity_ids.insert(py_str(record_id));
            }
        }
    }
    for item in pending_entities {
        entity_ids.insert(py_str(item.get("entity_id").unwrap_or(&Json::Null)));
    }
    let current = prepare(update, snapshot, &entity_ids).map_err(|error| {
        // `except (ValueError, TypeError, KeyError) as error`.
        if matches!(error.python_class(), "ValueError" | "TypeError" | "KeyError") {
            DomainError::command_rejected_with(error.message(), "inventory_update_invalid", Vec::new())
        } else {
            error
        }
    })?;
    let mut attributes = JsonMap::new();
    attributes.insert("holdings".into(), Json::Array(current.into_iter().map(Json::Object).collect()));
    let mut payload = JsonMap::new();
    payload.insert("entity_kind".into(), INVENTORY_ENTITY_KIND.into());
    payload.insert("name".into(), "Fallback inventory".into());
    payload.insert("attributes".into(), Json::Object(attributes));
    payload.insert("visibility".into(), "gm_private".into());
    payload.insert("known_by".into(), Json::Array(Vec::new()));
    payload.insert("source_ref".into(), Json::Null);
    payload.insert("origin".into(), "gm_commit".into());
    payload.insert("request_hash".into(), request_hash.into());
    Ok((expected_revision, owned_payload(&Json::Object(payload))?))
}

/// The guarded part of `prepare_inventory_payload`: the replacements and transfers applied to
/// the current ledger.
fn prepare(
    update: &JsonMap,
    snapshot: &CampaignSnapshot,
    entity_ids: &BTreeSet<String>,
) -> DomainResult<Vec<JsonMap>> {
    // `{str(item["holding_id"]): item ...}`: insertion order, a later holding replacing in place.
    let mut holdings: Vec<(String, JsonMap)> = Vec::new();
    for item in inventory_holdings(snapshot)? {
        let identity = py_str(item.get("holding_id").ok_or_else(|| key_error("holding_id"))?);
        match holdings.iter_mut().find(|(known, _)| *known == identity) {
            Some(entry) => entry.1 = item,
            None => holdings.push((identity, item)),
        }
    }
    let empty = Json::Array(Vec::new());
    let replacements = holding_records(Some(update.get("holdings").unwrap_or(&empty)))?;
    let transfers = holding_records(Some(update.get("transfers").unwrap_or(&empty)))?;
    if replacements.len() > 64 || transfers.len() > 64 {
        return Err(value("Inventory updates must be bounded lists"));
    }
    if replacements.is_empty() && transfers.is_empty() {
        return Err(value("Inventory update is empty"));
    }
    let mut replaced: BTreeSet<String> = BTreeSet::new();
    for replacement in replacements {
        let Some(Json::String(identity)) = replacement.get("holding_id") else {
            return Err(value("Inventory holding update identity is invalid or repeated"));
        };
        let identity = identity.clone();
        if !replaced.insert(identity.clone()) {
            return Err(value("Inventory holding update identity is invalid or repeated"));
        }
        if let Some((_, old)) = holdings.iter().find(|(known, _)| *known == identity) {
            for field in ["item_id", "kind", "stackable"] {
                let before = old.get(field).ok_or_else(|| key_error(field))?;
                if !py_eq(before, replacement.get(field).unwrap_or(&Json::Null)) {
                    return Err(value("An existing holding cannot become a different item or currency"));
                }
            }
        }
        match holdings.iter_mut().find(|(known, _)| *known == identity) {
            Some(entry) => entry.1 = replacement,
            None => holdings.push((identity, replacement)),
        }
    }
    let values: Vec<Json> = holdings.into_iter().map(|(_, item)| Json::Object(item)).collect();
    let mut current = validate_holdings(&values, entity_ids)?;
    for transfer in &transfers {
        let required = ["source_id", "destination_id", "quantity", "holder_id"];
        let keys_ok = required.iter().all(|key| transfer.contains_key(*key))
            && transfer.keys().all(|key| required.contains(&key.as_str()) || key == "owner_id");
        if !keys_ok {
            return Err(value("Inventory transfer fields are invalid"));
        }
        let holdings: Vec<Json> = current.into_iter().map(Json::Object).collect();
        current = transfer_holding(
            &holdings,
            HoldingTransfer {
                source_id: &transfer["source_id"],
                destination_id: &transfer["destination_id"],
                quantity: &transfer["quantity"],
                holder_id: &transfer["holder_id"],
                // `transfer.get("owner_id")`: absent or null keeps the source's owner.
                owner_id: transfer.get("owner_id").filter(|owner| !owner.is_null()),
            },
            entity_ids,
        )?;
    }
    Ok(current)
}
