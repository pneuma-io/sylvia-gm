//! `application/decision_scope.py`: Ruleset-neutral projection of package-owned model decision
//! scopes.
//!
//! Every projection reads a campaign's state, participants and revision, so it takes the
//! [`CampaignState`] that both `CampaignSnapshot` and `CampaignDisplaySnapshot` hold.

use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    CampaignState, ModelDecisionActor, ModelDecisionBoundary, ModelDecisionOwner, Participant,
};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::pyops::{py_eq, py_str};

/// `DecisionScopeProjectionError`: a validated declaration could not be projected from
/// authoritative state (a `ValueError` subclass).
fn projection_error(message: &str) -> DomainError {
    DomainError::Diagnosed { error_type: "DecisionScopeProjectionError", message: message.into(), diagnostics: Vec::new() }
}

/// `declared[key]` of a validated declaration.
fn field<'a>(declared: &'a JsonMap, key: &str) -> &'a Json {
    declared.get(key).unwrap_or(&Json::Null)
}

/// `_state_reference(state, reference)`: a state value by `namespace` and dotted `key`, `None`
/// (`Json::Null`) when a step is missing.
fn state_reference(state: &JsonMap, reference: &Json) -> Json {
    let namespace = py_str(&reference["namespace"]);
    let key = py_str(&reference["key"]);
    if let Some(value) = state.get(&format!("{namespace}.{key}")) {
        return value.clone();
    }
    let (root, remainder) = match key.split_once('.') {
        Some((root, remainder)) => (root, Some(remainder)),
        None => (key.as_str(), None),
    };
    let mut current = state.get(&format!("{namespace}.{root}")).cloned().unwrap_or(Json::Null);
    let Some(remainder) = remainder else { return current };
    for step in remainder.split('.') {
        current = match current.as_object().and_then(|map| map.get(step)) {
            Some(value) => value.clone(),
            None => return Json::Null,
        };
    }
    current
}

fn has_text(value: Option<&Json>) -> Option<&str> {
    value.and_then(Json::as_str).filter(|text| !sylvia_base::pystr::strip(text).is_empty())
}

/// `_actor_name(actor_id, actor_state, participants)`.
fn actor_name(actor_id: &str, actor_state: &JsonMap, participants: &[Participant]) -> String {
    let participant = participants.iter().find(|item| item.participant_id() == actor_id);
    if let Some(name) = has_text(participant.and_then(|item| item.data().get("name"))) {
        return name.to_owned();
    }
    if let Some(name) = has_text(actor_state.get("name")) {
        return name.to_owned();
    }
    actor_id.to_owned()
}

/// `_actor_projection(actor_id, entities, participants)`.
fn actor_projection(actor_id: &str, entities: &JsonMap, participants: &[Participant]) -> DomainResult<ModelDecisionActor> {
    let Some(Json::Object(actor_state)) = entities.get(actor_id) else {
        return Err(projection_error("Ruleset eligible actor state is unavailable"));
    };
    Ok(ModelDecisionActor {
        actor_id: actor_id.to_owned(),
        actor_name: actor_name(actor_id, actor_state, participants),
        actor_state: actor_state.clone(),
    })
}

/// `ModelDecisionOwner(value)`.
fn owner_of(text: &str) -> Option<ModelDecisionOwner> {
    match text {
        "player" => Some(ModelDecisionOwner::Player),
        "gm" => Some(ModelDecisionOwner::Gm),
        _ => None,
    }
}

/// `_project_side_scope(snapshot, declared)`.
fn project_side_scope(snapshot: &CampaignState, declared: &JsonMap) -> DomainResult<Option<ModelDecisionBoundary>> {
    let owner_value = state_reference(&snapshot.state, field(declared, "decision_owner"));
    let Some(owner) = owner_of(&py_str(&owner_value)) else {
        return Err(projection_error("Ruleset decision owner is invalid"));
    };
    let active_side = state_reference(&snapshot.state, field(declared, "active_side"));
    if active_side.is_null() {
        return Ok(None);
    }
    let active_side = match active_side.as_str() {
        Some(side) if !side.is_empty() => side.to_owned(),
        _ => return Err(projection_error("Ruleset active decision side is invalid")),
    };
    let eligible_value = state_reference(&snapshot.state, field(declared, "eligible_actors"));
    let eligible: Vec<&str> = match &eligible_value {
        Json::Array(items) if items.len() <= 128 => {
            let texts: Vec<&str> = items.iter().filter_map(Json::as_str).filter(|item| !item.is_empty()).collect();
            let unique: std::collections::HashSet<&str> = texts.iter().copied().collect();
            if texts.len() != items.len() || unique.len() != texts.len() {
                return Err(projection_error("Ruleset eligible actor set is invalid"));
            }
            texts
        }
        _ => return Err(projection_error("Ruleset eligible actor set is invalid")),
    };
    let Json::Object(entities) = state_reference(&snapshot.state, field(declared, "actor_entities")) else {
        return Err(projection_error("Ruleset decision actor state is unavailable"));
    };
    let mut eligible_actors = Vec::new();
    for actor_id in &eligible {
        eligible_actors.push(actor_projection(actor_id, &entities, &snapshot.participants)?);
    }
    let focused_actor = state_reference(&snapshot.state, field(declared, "focused_actor"));
    let focused_actor = match &focused_actor {
        Json::Null => None,
        Json::String(focused) if eligible.contains(&focused.as_str()) => Some(focused.clone()),
        _ => return Err(projection_error("Ruleset focused actor is not eligible")),
    };
    let actor_id = focused_actor.clone().or_else(|| eligible_actors.first().map(|actor| actor.actor_id.clone()));
    let selected = eligible_actors.iter().find(|item| Some(&item.actor_id) == actor_id.as_ref());
    Ok(Some(ModelDecisionBoundary {
        owner,
        actor_name: selected.map(|actor| actor.actor_name.clone()),
        actor_state: selected.map(|actor| actor.actor_state.clone()).unwrap_or_default(),
        actor_id,
        campaign_revision: snapshot.revision,
        active_side_id: Some(active_side),
        focused_actor_id: focused_actor,
        eligible_actors,
        preparation: None,
    }))
}

/// `value in control["player_values"]`, with Python's equality.
fn is_player_value(value: Option<&Json>, control: &JsonMap) -> bool {
    let value = value.unwrap_or(&Json::Null);
    control.get("player_values").and_then(Json::as_array).is_some_and(|values| values.iter().any(|item| py_eq(item, value)))
}

/// `participant.data.get(str(control["field"]))`.
fn control_value<'p>(participant: &'p Participant, control: &JsonMap) -> Option<&'p Json> {
    participant.data().get(&py_str(field(control, "field")))
}

/// `_project_legacy_actor_boundary(snapshot, declared)`.
fn project_legacy_actor_boundary(snapshot: &CampaignState, declared: &JsonMap) -> DomainResult<Option<ModelDecisionBoundary>> {
    let actor_id = match state_reference(&snapshot.state, field(declared, "active_actor")) {
        Json::String(actor_id) if !actor_id.is_empty() => actor_id,
        _ => return Ok(None),
    };
    let entities = state_reference(&snapshot.state, field(declared, "actor_entities"));
    let actor_state = match entities.as_object().and_then(|map| map.get(&actor_id)) {
        Some(Json::Object(state)) => state.clone(),
        _ => JsonMap::new(),
    };
    let control = field(declared, "participant_control").as_object().cloned().unwrap_or_default();
    let participant_id = if control.contains_key("actor_participant_field") {
        actor_state.get(&py_str(&control["actor_participant_field"])).cloned().unwrap_or(Json::Null)
    } else {
        Json::String(actor_id.clone())
    };
    let participant = snapshot
        .participants
        .iter()
        .find(|item| participant_id.as_str() == Some(item.participant_id()));
    let default_owner = py_str(field(declared, "default_owner"));
    let Some(mut owner) = owner_of(&default_owner) else {
        return Err(DomainError::Value(format!("'{default_owner}' is not a valid ModelDecisionOwner")));
    };
    if participant.is_some_and(|participant| is_player_value(control_value(participant, &control), &control)) {
        owner = ModelDecisionOwner::Player;
    }
    let name = actor_name(&actor_id, &actor_state, &snapshot.participants);
    let actor = ModelDecisionActor { actor_id: actor_id.clone(), actor_name: name.clone(), actor_state: actor_state.clone() };
    Ok(Some(ModelDecisionBoundary {
        owner,
        actor_id: Some(actor_id.clone()),
        actor_name: Some(name),
        actor_state,
        campaign_revision: snapshot.revision,
        active_side_id: None,
        focused_actor_id: Some(actor_id),
        eligible_actors: vec![actor],
        preparation: None,
    }))
}

/// `project_campaign_preparation_scope(snapshot, declared)`: pending package-owned character
/// events, before ordinary play decisions.
pub fn project_campaign_preparation_scope(snapshot: &CampaignState, declared: &JsonMap) -> DomainResult<Option<ModelDecisionBoundary>> {
    let Some(Json::Object(preparation)) = declared.get("preparation") else { return Ok(None) };
    let actor_reference = field(declared, "actor_entities");
    let Json::Object(entities) = state_reference(&snapshot.state, actor_reference) else { return Ok(None) };
    let namespace = &actor_reference["namespace"];
    let queue_field = py_str(field(preparation, "queue_field"));
    let mut actors = Vec::new();
    let mut events = Vec::new();
    for participant in &snapshot.participants {
        if namespace.as_str() != Some(participant.package_id()) {
            continue;
        }
        let Some(Json::Object(entity)) = entities.get(participant.participant_id()) else { continue };
        let queue = match entity.get(&queue_field) {
            None | Some(Json::Null) => continue,
            Some(Json::Array(queue)) if queue.len() <= 64 && queue.iter().all(Json::is_object) => queue,
            Some(_) => return Err(projection_error("Ruleset character preparation queue is invalid")),
        };
        if queue.is_empty() {
            continue;
        }
        let actor = actor_projection(participant.participant_id(), &entities, &snapshot.participants)?;
        let mut entry = JsonMap::new();
        entry.insert("actor_id".into(), Json::String(actor.actor_id.clone()));
        entry.insert("actor_name".into(), Json::String(actor.actor_name.clone()));
        entry.insert("events".into(), Json::Array(queue.clone()));
        events.push(Json::Object(entry));
        actors.push(actor);
    }
    let Some(first) = actors.first().cloned() else { return Ok(None) };
    let mut projected = preparation.clone();
    projected.insert("events".into(), Json::Array(events));
    projected.insert("package_id".into(), namespace.clone());
    Ok(Some(ModelDecisionBoundary {
        owner: ModelDecisionOwner::Player,
        actor_id: Some(first.actor_id.clone()),
        actor_name: Some(first.actor_name.clone()),
        actor_state: first.actor_state.clone(),
        campaign_revision: snapshot.revision,
        active_side_id: None,
        focused_actor_id: Some(first.actor_id),
        eligible_actors: actors,
        preparation: Some(projected),
    }))
}

/// `project_model_decision_scope(snapshot, declared)`: the preparation scope, the side scope or
/// its legacy actor form.
pub fn project_model_decision_scope(snapshot: &CampaignState, declared: &JsonMap) -> DomainResult<Option<ModelDecisionBoundary>> {
    if let Some(preparation) = project_campaign_preparation_scope(snapshot, declared)? {
        return Ok(Some(preparation));
    }
    if declared.contains_key("eligible_actors") {
        return project_side_scope(snapshot, declared);
    }
    project_legacy_actor_boundary(snapshot, declared)
}

fn actor_entry(id: &str, name: &str) -> Json {
    let mut entry = JsonMap::new();
    entry.insert("id".into(), Json::String(id.to_owned()));
    entry.insert("name".into(), Json::String(name.to_owned()));
    Json::Object(entry)
}

/// `project_player_control_scope(snapshot, declared)`: the controlled actors' identities,
/// without the GM's actor state.
pub fn project_player_control_scope(snapshot: &CampaignState, declared: &JsonMap) -> DomainResult<JsonMap> {
    let mut scope = JsonMap::new();
    if let Some(boundary) = project_model_decision_scope(snapshot, declared)? {
        if let Some(preparation) = boundary.preparation {
            scope.insert("available".into(), Json::Bool(false));
            scope.insert("focused_actor_id".into(), Json::Null);
            scope.insert("actors".into(), Json::Array(Vec::new()));
            scope.insert("preparation".into(), Json::Object(preparation));
            return Ok(scope);
        }
        let player_owned = boundary.owner == ModelDecisionOwner::Player;
        scope.insert("available".into(), Json::Bool(player_owned));
        scope.insert(
            "focused_actor_id".into(),
            if player_owned { boundary.focused_actor_id.map_or(Json::Null, Json::String) } else { Json::Null },
        );
        let actors = if player_owned {
            boundary.eligible_actors.iter().map(|actor| actor_entry(&actor.actor_id, &actor.actor_name)).collect()
        } else {
            Vec::new()
        };
        scope.insert("actors".into(), Json::Array(actors));
        return Ok(scope);
    }
    if let Some(Json::Object(control)) = declared.get("participant_control") {
        // An inactive encounter has no active actor; the Ruleset role declaration still names
        // the party members the player controls outside it.
        let actors = snapshot
            .participants
            .iter()
            .filter(|participant| is_player_value(control_value(participant, control), control))
            .map(|participant| {
                let name = participant.data().get("name").map_or_else(|| participant.participant_id().to_owned(), py_str);
                actor_entry(participant.participant_id(), &name)
            })
            .collect();
        scope.insert("available".into(), Json::Bool(true));
        scope.insert("focused_actor_id".into(), Json::Null);
        scope.insert("actors".into(), Json::Array(actors));
        return Ok(scope);
    }
    scope.insert("available".into(), Json::Bool(false));
    scope.insert("focused_actor_id".into(), Json::Null);
    scope.insert("actors".into(), Json::Array(Vec::new()));
    Ok(scope)
}

/// `player_viewer_ids(snapshot, declared)`: the player's participants, whoever's turn it is.
pub fn player_viewer_ids(snapshot: &CampaignState, declared: Option<&JsonMap>) -> std::collections::BTreeSet<String> {
    let Some(Json::Object(control)) = declared.and_then(|declared| declared.get("participant_control")) else {
        return Default::default();
    };
    snapshot
        .participants
        .iter()
        .filter(|participant| is_player_value(control_value(participant, control), control))
        .map(|participant| participant.participant_id().to_owned())
        .collect()
}
