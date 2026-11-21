//! `application/campaign_starting_world.py`: copy a Plan's authored game state and place the
//! selected party in its declared state location.
//!
//! Python copies values through `json.loads(canonical_json_bytes(...))`, which also sorts every
//! object's keys; the port copies the same way, so key order matches.

use std::collections::BTreeMap;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::StateValue;
use sylvia_domain::campaign_plan::ValidatedCampaignPlan;
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::{py_eq, py_get_attr, py_in, py_item, py_str, py_truthy, py_type_name};

type World = BTreeMap<(String, String), Json>;

fn rejected(message: &str) -> DomainError {
    DomainError::command_rejected(message)
}

/// `json.loads(canonical_json_bytes(value))`.
fn canonical_copy(value: &Json) -> Json {
    pyjson::loads(std::str::from_utf8(&canonical_json_bytes(value)).expect("UTF-8"))
        .expect("canonical JSON reads back")
}

/// `_bind_placement(value, participant_id, name)`: substitute only explicit participant references.
fn bind_placement(value: &Json, participant_id: &str, name: &Json) -> Json {
    match value {
        Json::Object(map) if map.len() == 1 && map.contains_key("$participant") => {
            if py_eq(&map["$participant"], &Json::from("id")) {
                Json::from(participant_id)
            } else {
                name.clone()
            }
        }
        Json::Object(map) => Json::Object(
            map.iter().map(|(key, item)| (key.clone(), bind_placement(item, participant_id, name))).collect(),
        ),
        Json::Array(items) => {
            Json::Array(items.iter().map(|item| bind_placement(item, participant_id, name)).collect())
        }
        other => other.clone(),
    }
}

/// `world.get((namespace, fields[0]))` then `.get(field)` through the rest, as a mutable list.
fn authored_list<'w>(world: &'w mut World, namespace: &str, key: &str) -> Option<&'w mut Vec<Json>> {
    let mut fields = key.split('.');
    let mut current = world.get_mut(&(namespace.to_owned(), fields.next().unwrap_or_default().to_owned()))?;
    for field in fields {
        current = current.as_object_mut()?.get_mut(field)?;
    }
    current.as_array_mut()
}

/// `_place_party(world, initial_state, party_entities, party_names)`: apply ordered Plan slots.
fn place_party(
    world: &mut World,
    initial_state: &JsonMap,
    party_entities: &Json,
    party_names: &Json,
) -> DomainResult<JsonMap> {
    let Json::Object(party_entities) = party_entities else {
        return Err(DomainError::uncaught(
            "AttributeError",
            format!("'{}' object has no attribute 'items'", py_type_name(party_entities)),
        ));
    };
    let mut entities: JsonMap =
        party_entities.iter().map(|(id, entity)| (id.clone(), canonical_copy(entity))).collect();
    let slots = match initial_state.get("party_placements") {
        None | Some(Json::Null) => return Ok(entities),
        Some(Json::Array(slots)) => slots,
        Some(_) => unreachable!("the Plan validator admitted the placements as a list"),
    };
    if slots.len() < entities.len() {
        return Err(rejected("The Plan has too few starting placements for the selected party"));
    }
    for ((participant_id, entity), slot) in entities.iter_mut().zip(slots) {
        let Json::Object(entity) = entity else {
            return Err(rejected("The selected character's game state must be an object"));
        };
        // `party_names.get(participant_id, participant_id)`: the name is used as it is, text or not.
        let name = py_get_attr(party_names, participant_id)?
            .cloned()
            .unwrap_or_else(|| Json::from(participant_id.as_str()));
        let bound = bind_placement(slot, participant_id, &name);
        for (key, value) in bound["entity_state"].as_object().into_iter().flatten() {
            entity.insert(key.clone(), value.clone());
        }
        for entry in bound["append"].as_array().into_iter().flatten() {
            let (namespace, key) =
                (entry["namespace"].as_str().unwrap_or_default(), entry["key"].as_str().unwrap_or_default());
            let Some(current) = authored_list(world, namespace, key) else {
                return Err(rejected("A Plan party placement must append to an authored list"));
            };
            let new = &entry["value"];
            let same_id = new.as_object().and_then(|object| object.get("id")).is_some_and(|id| {
                current.iter().any(|item| {
                    item.as_object().and_then(|item| item.get("id")).is_some_and(|other| py_eq(other, id))
                })
            });
            if same_id || py_in(new, &Json::Array(current.clone())).unwrap_or(false) {
                return Err(rejected("The Plan's world and party placements have conflicting identities"));
            }
            current.push(new.clone());
        }
    }
    Ok(entities)
}

/// `materialize_starting_world(plan, entity_reference=..., party_entities=..., party_names=...)`:
/// preserve world actors and adjacent state; reject collisions instead of overwriting either.
pub fn materialize_starting_world(
    plan: &ValidatedCampaignPlan,
    entity_reference: Option<&Json>,
    party_entities: &Json,
    party_names: Option<&Json>,
) -> DomainResult<Vec<StateValue>> {
    let initial_state =
        plan.payload["initial_state"].as_object().expect("a validated Plan has an initial state");
    let mut world = World::new();
    for item in initial_state.get("mechanics").and_then(Json::as_array).into_iter().flatten() {
        let key = (py_str(&item["namespace"]), py_str(&item["key"]));
        world.insert(key, canonical_copy(&item["value"]));
    }
    // `party_names or {}`.
    let empty = Json::Object(JsonMap::new());
    let names = party_names.filter(|names| py_truthy(names)).unwrap_or(&empty);
    let party_entities = place_party(&mut world, initial_state, party_entities, names)?;
    let placements = initial_state.get("party_placements").is_some_and(py_truthy);
    if placements && entity_reference.is_none() {
        return Err(rejected("Party placements require a Ruleset character state location"));
    }
    if let Some(reference) = entity_reference {
        let key = py_str(py_item(reference, "key")?);
        let mut fields = key.split('.');
        let identity =
            (py_str(py_item(reference, "namespace")?), fields.next().unwrap_or_default().to_owned());
        let not_object = || rejected("The Plan's character state location must be an object");
        let mut current = world.entry(identity).or_insert_with(|| Json::Object(JsonMap::new()));
        for field in fields {
            let Json::Object(map) = current else { return Err(not_object()) };
            current = map.entry(field.to_owned()).or_insert_with(|| Json::Object(JsonMap::new()));
        }
        let Json::Object(location) = current else { return Err(not_object()) };
        if location.keys().any(|key| party_entities.contains_key(key)) {
            return Err(rejected("The Plan's world and selected party have conflicting identities"));
        }
        if let Json::Object(copied) = canonical_copy(&Json::Object(party_entities)) {
            for (key, value) in copied {
                location.insert(key, value);
            }
        }
    }
    world.into_iter().map(|((namespace, key), value)| StateValue::new(namespace, key, value)).collect()
}
