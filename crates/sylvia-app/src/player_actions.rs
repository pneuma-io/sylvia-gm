//! `application/player_actions.py` above the store. The owner marker checks the store needs
//! are `sylvia_domain::player_actions`; this module has the Ruleset's direct-control policy.

use sylvia_base::pystr::strip;
use sylvia_base::{Json, JsonMap};

/// `PlayerControls`: the Ruleset policy for confirming one direct action and requesting its GM
/// response. `available` is false when the declaration is malformed.
#[derive(Debug, Clone, PartialEq)]
pub struct PlayerControls {
    pub confirmation: String,
    pub response: String,
    pub preview: Option<JsonMap>,
    pub actor_parameter: Option<String>,
    pub checks: Vec<JsonMap>,
    pub queries: Vec<JsonMap>,
    pub calculations: Vec<JsonMap>,
    pub map_preview: Option<JsonMap>,
    pub available: bool,
    pub preflight: Vec<String>,
    pub preflight_path: Option<JsonMap>,
}

impl Default for PlayerControls {
    fn default() -> Self {
        Self {
            confirmation: "none".into(),
            response: "on_request".into(),
            preview: None,
            actor_parameter: None,
            checks: Vec::new(),
            queries: Vec::new(),
            calculations: Vec::new(),
            map_preview: None,
            available: true,
            preflight: Vec::new(),
            preflight_path: None,
        }
    }
}

impl PlayerControls {
    fn unavailable() -> Self {
        Self { available: false, ..Self::default() }
    }
}

fn keys_are(object: &JsonMap, keys: &[&str]) -> bool {
    object.len() == keys.len() && keys.iter().all(|key| object.contains_key(*key))
}

fn ident_start(c: char) -> bool {
    c.is_ascii_alphabetic() || c == '_'
}

fn ident_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// `_LOCAL_NAME.fullmatch(name)`: `_[A-Za-z][A-Za-z0-9_]{0,63}`.
fn local_name(name: &str) -> bool {
    let mut chars = name.chars();
    chars.next() == Some('_')
        && chars.next().is_some_and(|c| c.is_ascii_alphabetic())
        && name.chars().count() <= 65
        && name.chars().skip(2).all(ident_char)
}

/// `re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', name)`.
fn preflight_name(name: &str) -> bool {
    name.chars().next().is_some_and(ident_start)
        && name.chars().count() <= 128
        && name.chars().all(ident_char)
}

/// `re.fullmatch(r'state\.[A-Za-z_][A-Za-z0-9_.]{0,127}', scene)`.
fn preflight_scene(scene: &str) -> bool {
    let Some(rest) = scene.strip_prefix("state.") else {
        return false;
    };
    rest.chars().next().is_some_and(ident_start)
        && rest.chars().count() <= 128
        && rest.chars().all(|c| ident_char(c) || c == '.')
}

/// `len(text)` in code points.
fn chars(text: &str) -> usize {
    text.chars().count()
}

/// `_valid_control_queries(queries, calculations)`.
fn valid_control_queries(queries: &Json, calculations: &Json) -> bool {
    let (Json::Array(queries), Json::Array(calculations)) = (queries, calculations) else {
        return false;
    };
    if queries.len() > 8 || calculations.len() > 24 {
        return false;
    }
    let mut names: Vec<&str> = Vec::new();
    for item in queries {
        let Json::Object(item) = item else { return false };
        if !keys_are(item, &["spatial_query", "store_as"]) {
            return false;
        }
        let (Json::String(name), Json::Object(body)) = (&item["store_as"], &item["spatial_query"]) else {
            return false;
        };
        if !local_name(name)
            || names.contains(&name.as_str())
            || !keys_are(body, &["scene", "operation", "query"])
        {
            return false;
        }
        let scene_ok = matches!(&body["scene"], Json::String(scene) if scene.starts_with("state."));
        let operation_ok = matches!(&body["operation"], Json::String(op)
            if matches!(op.as_str(), "path" | "ray" | "exposure" | "area" | "crossed_zones"));
        let query_ok = matches!(&body["query"], Json::Object(query) if !query.contains_key("kind"));
        if !(scene_ok && operation_ok && query_ok) {
            return false;
        }
        names.push(name);
    }
    for item in calculations {
        let Json::Object(item) = item else { return false };
        if !item.contains_key("set") || item.keys().any(|key| key != "set" && key != "condition") {
            return false;
        }
        let Json::Object(body) = &item["set"] else { return false };
        if !keys_are(body, &["field", "value"]) {
            return false;
        }
        let condition = item.get("condition").cloned().unwrap_or_else(|| Json::String("true".into()));
        let (Json::String(name), Json::String(expression), Json::String(condition)) =
            (&body["field"], &body["value"], &condition)
        else {
            return false;
        };
        if !local_name(name)
            || names.contains(&name.as_str())
            || !(1..=500).contains(&chars(expression))
            || !(1..=500).contains(&chars(condition))
        {
            return false;
        }
        names.push(name);
    }
    true
}

/// `parse_player_controls(value)`: disable malformed direct controls without withdrawing the
/// underlying chat mechanic. `None` stands for an absent declaration.
pub fn parse_player_controls(value: Option<&Json>) -> PlayerControls {
    let value = match value {
        None | Some(Json::Null) => return PlayerControls::default(),
        Some(Json::Object(value)) => value,
        Some(_) => return PlayerControls::unavailable(),
    };
    const ALLOWED: [&str; 10] = [
        "confirmation",
        "response",
        "preview",
        "actor_parameter",
        "checks",
        "queries",
        "calculations",
        "map_preview",
        "preflight",
        "preflight_path",
    ];
    if value.keys().any(|key| !ALLOWED.contains(&key.as_str())) {
        return PlayerControls::unavailable();
    }
    let field = |name: &str| value.get(name).filter(|item| !item.is_null());
    let empty = Json::Array(Vec::new());
    let confirmation = value.get("confirmation").cloned().unwrap_or_else(|| Json::String("none".into()));
    let response = value.get("response").cloned().unwrap_or_else(|| Json::String("on_request".into()));
    let checks = value.get("checks").unwrap_or(&empty);
    let queries = value.get("queries").unwrap_or(&empty);
    let calculations = value.get("calculations").unwrap_or(&empty);
    if !valid_control_queries(queries, calculations) {
        return PlayerControls::unavailable();
    }
    let map_preview = field("map_preview");
    let preflight = value.get("preflight").unwrap_or(&empty);
    let preflight_path = field("preflight_path");
    let Json::Array(preflight) = preflight else {
        return PlayerControls::unavailable();
    };
    if preflight.len() > 4
        || preflight.iter().any(|name| !matches!(name, Json::String(name) if preflight_name(name)))
    {
        return PlayerControls::unavailable();
    }
    if let Some(path) = preflight_path {
        let valid = !preflight.is_empty()
            && matches!(path, Json::Object(path)
                if keys_are(path, &["scene", "route", "cost"])
                && matches!(&path["scene"], Json::String(scene) if preflight_scene(scene))
                && ["route", "cost"].iter().all(|key| matches!(&path[*key], Json::String(name) if local_name(name))))
            && map_preview.is_none();
        if !valid {
            return PlayerControls::unavailable();
        }
    }
    let queries = queries.as_array().expect("checked above");
    if let Some(map_preview) = map_preview {
        let kind_of = |name: &str| {
            queries
                .iter()
                .rev()
                .find(|item| item["store_as"] == name)
                .map(|item| &item["spatial_query"]["operation"])
        };
        let valid = matches!(map_preview, Json::Object(map) if !map.is_empty()
            && map.keys().all(|key| matches!(key.as_str(), "path" | "ray" | "exposure" | "crossed_zones"))
            && map.iter().all(|(kind, name)| matches!(name, Json::String(name)
                if kind_of(name).is_some_and(|op| op == kind))));
        if !valid {
            return PlayerControls::unavailable();
        }
    }
    let Json::Array(checks) = checks else {
        return PlayerControls::unavailable();
    };
    let check_ok = |check: &Json| {
        matches!(check, Json::Object(check) if keys_are(check, &["condition", "reason"])
            && matches!(&check["condition"], Json::String(text) if !strip(text).is_empty() && chars(text) <= 4096)
            && matches!(&check["reason"], Json::String(text) if !strip(text).is_empty() && chars(text) <= 256))
    };
    if checks.len() > 32 || !checks.iter().all(check_ok) {
        return PlayerControls::unavailable();
    }
    let preview = field("preview");
    let actor_parameter = field("actor_parameter");
    let (Json::String(confirmation), Json::String(response)) = (&confirmation, &response) else {
        return PlayerControls::unavailable();
    };
    if !matches!(confirmation.as_str(), "none" | "required")
        || !matches!(response.as_str(), "on_request" | "automatic")
        || preview.is_some_and(|preview| !preview.is_object())
        || actor_parameter
            .is_some_and(|name| !matches!(name, Json::String(name) if !name.is_empty() && chars(name) <= 128))
    {
        return PlayerControls::unavailable();
    }
    let object = |value: &Json| value.as_object().cloned().unwrap_or_default();
    PlayerControls {
        confirmation: confirmation.clone(),
        response: response.clone(),
        preview: preview.map(object),
        actor_parameter: actor_parameter.and_then(Json::as_str).map(str::to_owned),
        checks: checks.iter().map(object).collect(),
        queries: queries.iter().map(object).collect(),
        calculations: calculations.as_array().expect("checked above").iter().map(object).collect(),
        map_preview: map_preview.map(object),
        available: true,
        preflight: preflight.iter().filter_map(Json::as_str).map(str::to_owned).collect(),
        preflight_path: preflight_path.map(object),
    }
}

// Ported by lane 3, for lane 2's review (the manager's split, 23 Nov 2026): the saved-outcome
// readers the GM response context needs.

/// `player_action_origins(snapshot)`: each action's saved owner marker origin, by action, the
/// last marker of an action winning as a later dict entry does.
pub fn player_action_origins(
    snapshot: &sylvia_domain::authority::CampaignSnapshot,
) -> Vec<(String, JsonMap)> {
    let mut origins: Vec<(String, JsonMap)> = Vec::new();
    for record in &snapshot.consequences {
        let marker = record.payload.get("kind")
            == Some(&Json::from(sylvia_domain::player_actions::PLAYER_ACTION_ORIGIN));
        if let (true, Some(Json::Object(origin))) = (marker, record.payload.get("origin")) {
            match origins.iter_mut().find(|(action_id, _)| *action_id == record.action_id) {
                Some(entry) => entry.1 = origin.clone(),
                None => origins.push((record.action_id.clone(), origin.clone())),
            }
        }
    }
    origins
}

/// `player_action_records(snapshot)`: every action with a saved origin, in snapshot order, as
/// its saved outcome (its result without `spatial_evidence`).
pub fn player_action_records(snapshot: &sylvia_domain::authority::CampaignSnapshot) -> Vec<JsonMap> {
    let origins = player_action_origins(snapshot);
    snapshot
        .actions
        .iter()
        .filter_map(|action| {
            let (_, origin) = origins.iter().find(|(action_id, _)| *action_id == action.action_id)?;
            let mut record = JsonMap::new();
            record.insert("action_id".into(), action.action_id.as_str().into());
            record.insert("turn_id".into(), action.turn_id.as_str().into());
            record.insert("revision".into(), action.resulting_revision.into());
            record.insert("status".into(), action.status.as_str().into());
            record.insert("origin".into(), Json::Object(origin.clone()));
            record.insert("package_id".into(), action.package_id.as_str().into());
            record.insert("tool_name".into(), action.tool_name.as_str().into());
            record.insert("parameters".into(), Json::Object(action.parameters.clone()));
            let result: JsonMap = action
                .result
                .iter()
                .filter(|(key, _)| *key != "spatial_evidence")
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect();
            record.insert("result".into(), Json::Object(result));
            Some(record)
        })
        .collect()
}

/// `player_action_request_matches(stored, request)`: every requested item equals the stored
/// origin's, as Python compares (`1 == 1.0 == True`); a missing stored item is `None`, so it
/// matches only a `None` request.
pub fn player_action_request_matches(stored: Option<&JsonMap>, request: &JsonMap) -> bool {
    stored.is_some_and(|stored| {
        request
            .iter()
            .all(|(key, value)| sylvia_domain::pyops::py_eq(stored.get(key).unwrap_or(&Json::Null), value))
    })
}

// ── The owner's player-control checks and parameter bindings ───────────────────────────────

use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::pyops::py_type_name;

/// `TypeError: unhashable type` when Python cannot put `value` in a set. The owner's action
/// declarations, control scope and state are frozen, so the type is `FrozenJsonDict` or
/// `FrozenJsonList` (differences row 48: a literal surface source is thawed, so Python names
/// `dict` or `list` there).
fn hashable(value: &Json) -> DomainResult<()> {
    match value {
        Json::Object(_) => Err(DomainError::type_error("unhashable type: 'FrozenJsonDict'")),
        Json::Array(_) => Err(DomainError::type_error("unhashable type: 'FrozenJsonList'")),
        _ => Ok(()),
    }
}

/// `isinstance(value, Sequence)`: a list, or text (whose characters are never mappings).
fn sequence_items(value: Option<&Json>) -> Option<&[Json]> {
    match value {
        Some(Json::Array(items)) => Some(items),
        Some(Json::String(_)) => Some(&[]),
        _ => None,
    }
}

/// `{item.get("id") for item in participants if isinstance(item, Mapping)}`, as the text ids a
/// text identity can match.
fn participant_ids(participants: &[Json]) -> DomainResult<std::collections::HashSet<&str>> {
    let mut ids = std::collections::HashSet::new();
    for item in participants.iter().filter_map(Json::as_object) {
        if let Some(id) = item.get("id") {
            hashable(id)?;
            if let Json::String(id) = id {
                ids.insert(id.as_str());
            }
        }
    }
    Ok(ids)
}

/// `value["id"]` on one item of a scope's frozen `actors` (a `FrozenJsonList` is a tuple).
fn subscript_id(value: &Json) -> DomainResult<&Json> {
    match value {
        Json::Object(map) => map.get("id").ok_or_else(|| DomainError::uncaught("KeyError", "'id'")),
        Json::String(_) => Err(DomainError::type_error("string indices must be integers, not 'str'")),
        Json::Array(_) => Err(DomainError::type_error("tuple indices must be integers or slices, not str")),
        other => {
            Err(DomainError::type_error(format!("'{}' object is not subscriptable", py_type_name(other))))
        }
    }
}

/// `validate_player_control(scope, parameters, actor_parameter)`: direct actor selection stays
/// inside the Ruleset's current player decision scope (`project_player_control_scope`).
pub fn validate_player_control(
    scope: Option<&JsonMap>,
    parameters: &JsonMap,
    actor_parameter: Option<&str>,
) -> DomainResult<()> {
    let Some(scope) = scope else { return Ok(()) };
    if scope.get("available") != Some(&Json::Bool(true)) {
        return Err(DomainError::command_rejected("The Ruleset currently requires a GM decision"));
    }
    let Some(actor_parameter) = actor_parameter else { return Ok(()) };
    let mut allowed = std::collections::HashSet::new();
    let mut add = |actor: &Json| -> DomainResult<()> {
        let id = subscript_id(actor)?;
        hashable(id)?;
        if let Json::String(id) = id {
            allowed.insert(id.clone());
        }
        Ok(())
    };
    match scope.get("actors") {
        None => return Err(DomainError::uncaught("KeyError", "'actors'")),
        Some(Json::Array(items)) => items.iter().try_for_each(&mut add)?,
        // Iterating text or a mapping gives text, which `["id"]` refuses.
        Some(Json::String(text)) if !text.is_empty() => add(&Json::String(String::new()))?,
        Some(Json::Object(map)) if !map.is_empty() => add(&Json::String(String::new()))?,
        Some(Json::String(_) | Json::Object(_)) => {}
        Some(other) => {
            return Err(DomainError::type_error(format!("'{}' object is not iterable", py_type_name(other))))
        }
    }
    match parameters.get(actor_parameter) {
        Some(Json::String(actor_id)) if allowed.contains(actor_id) => Ok(()),
        _ => Err(DomainError::command_rejected("This actor is not currently controlled by the player")),
    }
}

/// `resolve_party_action_parameters(action, selection, data, allow_entity_ids=...)`: the chosen
/// visible party member bound to the Ruleset's declared actor parameter.
pub fn resolve_party_action_parameters(
    action: &JsonMap,
    selection: &Json,
    data: &Json,
    allow_entity_ids: bool,
) -> DomainResult<JsonMap> {
    let invalid = || DomainError::command_rejected("Party action selection is invalid");
    let (Some(Json::Object(bindings)), Some(Json::Object(fixed))) =
        (action.get("bindings"), action.get("params"))
    else {
        return Err(invalid());
    };
    bindings.values().try_for_each(hashable)?;
    if bindings.len() != 1 || bindings.values().any(|value| value != "actor") {
        return Err(invalid());
    }
    let Some(selection) =
        selection.as_object().filter(|selection| selection.len() == 1 && selection.contains_key("actor"))
    else {
        return Err(invalid());
    };
    let Some(participants) = sequence_items(data.get("participants")) else { return Err(invalid()) };
    let mut visible = participant_ids(participants)?;
    if allow_entity_ids {
        if let Some(Json::Object(entities)) = data.get("entities") {
            visible.extend(entities.keys().map(String::as_str));
        }
    }
    let actor_id = match &selection["actor"] {
        Json::String(id) if visible.contains(id.as_str()) => id,
        _ => return Err(DomainError::command_rejected("The selected party member is unavailable")),
    };
    let mut parameters = fixed.clone();
    for key in bindings.keys() {
        parameters.insert(key.clone(), Json::String(actor_id.clone()));
    }
    Ok(parameters)
}

/// `retain_player_action_origin(outcome, origin)`: the UI provenance saved in the same
/// transaction as the real mechanics result.
pub fn retain_player_action_origin(
    mut outcome: sylvia_domain::authority::MechanicOutcome,
    origin: Option<&JsonMap>,
) -> sylvia_domain::authority::MechanicOutcome {
    if let Some(origin) = origin {
        let mut marker = JsonMap::new();
        marker.insert("kind".into(), Json::from(sylvia_domain::player_actions::PLAYER_ACTION_ORIGIN));
        marker.insert("origin".into(), Json::Object(origin.clone()));
        outcome.consequences.push(marker);
    }
    outcome
}

/// `resolve_inventory_action_parameters(action, selection, data)`: an owned visible actor and
/// bounded form data bound to a declared inventory operation.
pub fn resolve_inventory_action_parameters(
    action: &JsonMap,
    selection: &Json,
    data: &Json,
) -> DomainResult<JsonMap> {
    let invalid = || DomainError::command_rejected("Inventory action selection is invalid");
    let (Some(Json::Object(bindings)), Some(Json::Object(fixed))) =
        (action.get("bindings"), action.get("params"))
    else {
        return Err(invalid());
    };
    let Some(selection) = selection.as_object() else { return Err(invalid()) };
    let Some(participants) = sequence_items(data.get("participants")) else { return Err(invalid()) };
    bindings.values().try_for_each(hashable)?;
    let declared =
        bindings.values().all(|source| matches!(source.as_str(), Some("actor" | "declared_action")));
    let sources: std::collections::HashSet<&str> = bindings.values().filter_map(Json::as_str).collect();
    let selected: std::collections::HashSet<&str> = selection.keys().map(String::as_str).collect();
    if !declared || selected != sources || !selection.contains_key("actor") {
        return Err(invalid());
    }
    // `not isinstance(actor, str) or actor not in {...}`: the ids are read only for text.
    let available = match &selection["actor"] {
        Json::String(actor) => participant_ids(participants)?.contains(actor.as_str()),
        _ => false,
    };
    if !available {
        return Err(DomainError::command_rejected("The selected inventory owner is unavailable"));
    }
    let mut parameters = fixed.clone();
    for (parameter, source) in bindings {
        let source = source.as_str().expect("every binding source is checked as text");
        parameters.insert(parameter.clone(), selection[source].clone());
    }
    Ok(parameters)
}
