//! `application/tactical_grid.py`: resolving a player-surface source, the player-known scene,
//! the tactical grid and coordinate map projections, and binding a renderer selection to a
//! tactical action's parameters.
//!
//! The action declarations come from the owner's admitted surfaces (`panel_declaration`), so
//! their references are text and their binding sources declared selections. The surface data
//! and the selection are Ruleset state and client input, and are read exactly as Python reads
//! them.

use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{owned_payload, Participant};
use sylvia_domain::error::DomainResult;
/// The player-known scene lives with the spatial engine, which the bridge's shot presentation needs.
pub use sylvia_engine::spatial::player_known::{player_known_spatial_scene, player_known_spatial_value};

/// What `resolve_surface_source` reads from a participant (`getattr(participant, ..., None)`).
pub trait SurfaceParticipant {
    /// `participant.kind`.
    fn kind(&self) -> Option<&str>;
    /// `participant.participant_id`.
    fn participant_id(&self) -> Option<&str>;
    /// `thaw_json_value(participant.data)`.
    fn data(&self) -> Json;
}

impl SurfaceParticipant for Participant {
    fn kind(&self) -> Option<&str> {
        Some(Participant::kind(self))
    }
    fn participant_id(&self) -> Option<&str> {
        Some(Participant::participant_id(self))
    }
    fn data(&self) -> Json {
        Json::Object(Participant::data(self).clone())
    }
}

/// No participants, for callers that pass `participants=()`.
pub enum NoParticipant {}

impl SurfaceParticipant for NoParticipant {
    fn kind(&self) -> Option<&str> {
        match *self {}
    }
    fn participant_id(&self) -> Option<&str> {
        match *self {}
    }
    fn data(&self) -> Json {
        match *self {}
    }
}

/// `_sequence(value)`: a list, else `None`.
fn sequence(value: Option<&Json>) -> Option<&Vec<Json>> {
    match value {
        Some(Json::Array(items)) => Some(items),
        _ => None,
    }
}

/// `resolve_surface_source(source, state=..., participants=...)`: resolve one admitted
/// player-surface query without reading private authorities. Python's `None` is `Json::Null`,
/// both as `source` and as the answer.
pub fn resolve_surface_source<P: SurfaceParticipant>(
    source: &Json,
    state: &JsonMap,
    participants: &[P],
) -> DomainResult<Json> {
    let Json::Object(source) = source else { return Ok(Json::Null) };
    let kind = source.get("kind").and_then(Json::as_str);
    Ok(match kind {
        Some("literal") => Json::Object(owned_payload(source.get("value").unwrap_or(&Json::Null))?),
        Some("state") => {
            let (Some(Json::String(namespace)), Some(Json::String(key))) =
                (source.get("namespace"), source.get("key"))
            else {
                return Ok(Json::Null);
            };
            if let Some(value) = state.get(&format!("{namespace}.{key}")) {
                return Ok(value.clone());
            }
            let (root, remainder) = match key.split_once('.') {
                Some((root, remainder)) => (root, Some(remainder)),
                None => (key.as_str(), None),
            };
            let mut current = state.get(&format!("{namespace}.{root}")).cloned().unwrap_or(Json::Null);
            let Some(remainder) = remainder else { return Ok(current) };
            for field in remainder.split('.') {
                current = match current {
                    Json::Object(map) => match map.get(field).cloned() {
                        Some(value) => value,
                        None => return Ok(Json::Null),
                    },
                    _ => return Ok(Json::Null),
                };
            }
            current
        }
        Some("participants") => {
            let kinds: Vec<&str> =
                sequence(source.get("kinds")).into_iter().flatten().filter_map(Json::as_str).collect();
            let mut result = Vec::new();
            for participant in participants {
                if let (Some(kind), Some(id)) = (participant.kind(), participant.participant_id()) {
                    if kinds.contains(&kind) {
                        let mut entry = JsonMap::new();
                        entry.insert("id".into(), Json::String(id.to_owned()));
                        entry.insert("kind".into(), Json::String(kind.to_owned()));
                        entry.insert("data".into(), participant.data());
                        result.push(Json::Object(entry));
                    }
                }
            }
            Json::Array(result)
        }
        Some("keyed_values") => {
            let mut keyed = JsonMap::new();
            for entry in sequence(source.get("entries")).into_iter().flatten() {
                let Json::Object(entry) = entry else { continue };
                let key =
                    resolve_surface_source(entry.get("key").unwrap_or(&Json::Null), state, participants)?;
                let value =
                    resolve_surface_source(entry.get("value").unwrap_or(&Json::Null), state, participants)?;
                if let Json::String(key) = key {
                    if !sylvia_base::pystr::strip(&key).is_empty() && !value.is_null() {
                        keyed.insert(key, value);
                    }
                }
            }
            Json::Object(keyed)
        }
        Some("object") => {
            let Some(Json::Object(fields)) = source.get("fields") else { return Ok(Json::Null) };
            let mut resolved = JsonMap::new();
            for (name, child) in fields {
                resolved.insert(name.clone(), resolve_surface_source(child, state, participants)?);
            }
            Json::Object(resolved)
        }
        _ => Json::Null,
    })
}

// ── Projections and tactical action parameters ────────────────────────────────────────────

use std::collections::HashSet;
use sylvia_base::pystr::strip;
use sylvia_domain::package_library::TACTICAL_UI_DYNAMIC_SELECTIONS;
use sylvia_domain::pyops::py_eq;
use sylvia_engine::spatial::player_known::{caught, public_display, visible_anchor_ids};
use sylvia_engine::spatial::{admit_spatial_scene, SpatialEngine, SpatialError};

/// `TACTICAL_SELECTION_BINDINGS`.
pub const TACTICAL_SELECTION_BINDINGS: &[&str] = TACTICAL_UI_DYNAMIC_SELECTIONS;
/// `_MAX_LABEL_BYTES`.
const MAX_LABEL_BYTES: usize = 256;
/// `_MAX_PATH_POINTS`.
const MAX_PATH_POINTS: usize = 256;

/// What the projections and the selection binding raise.
#[derive(Debug, Clone, PartialEq)]
pub enum TacticalGridError {
    /// `TacticalGridError(message)`, a safe rejection the owner reports as its own.
    Invalid(String),
    /// An exception Python lets through: a `SpatialValidationError` from a segment preview's
    /// ray, or a `TypeError` from an unhashable objective status.
    Uncaught(SpatialError),
}

impl TacticalGridError {
    fn invalid(message: impl Into<String>) -> Self {
        Self::Invalid(message.into())
    }

    /// `type(error).__name__`.
    pub fn python_class(&self) -> &'static str {
        match self {
            Self::Invalid(_) => "TacticalGridError",
            Self::Uncaught(SpatialError::Validation(_)) => "SpatialValidationError",
            Self::Uncaught(SpatialError::Uncaught { error_type, .. }) => error_type,
        }
    }
}

impl std::fmt::Display for TacticalGridError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(message) => formatter.write_str(message),
            Self::Uncaught(error) => error.fmt(formatter),
        }
    }
}

impl std::error::Error for TacticalGridError {}

impl From<SpatialError> for TacticalGridError {
    fn from(error: SpatialError) -> Self {
        Self::Uncaught(error)
    }
}

/// A `TypeError` Python raises outside the spatial engine.
fn type_error(message: String) -> TacticalGridError {
    TacticalGridError::Uncaught(SpatialError::Uncaught { error_type: "TypeError", message })
}

/// `_coordinate(value, dimensions, integer=...)`: a list of exactly `dimensions` numbers
/// (integers when `integer`), else `None`. JSON numbers are always finite.
fn coordinate(value: Option<&Json>, dimensions: usize, integer: bool) -> Option<&Vec<Json>> {
    let items = sequence(value)?;
    if items.len() != dimensions {
        return None;
    }
    let admitted = items.iter().all(|item| match item {
        Json::Number(number) => !integer || number.is_i64() || number.is_u64(),
        _ => false,
    });
    admitted.then_some(items)
}

/// `float(value)` of an admitted number.
fn float(value: &Json) -> f64 {
    value.as_f64().expect("an admitted number")
}

/// Not blank after `strip()`, within `_MAX_LABEL_BYTES`.
fn label_text(value: &str) -> bool {
    !strip(value).is_empty() && value.len() <= MAX_LABEL_BYTES
}

/// `_project_objectives(value)`: the visible, well-formed objectives, by first identity.
fn project_objectives(value: Option<&Json>) -> Result<Vec<Json>, TacticalGridError> {
    let Some(items) = sequence(value).filter(|items| items.len() <= 256) else { return Ok(Vec::new()) };
    let mut result = Vec::new();
    let mut seen = HashSet::new();
    for item in items {
        let Json::Object(item) = item else { continue };
        if item.get("visible") != Some(&Json::Bool(true)) {
            continue;
        }
        let keys: HashSet<&str> = item.keys().map(String::as_str).collect();
        if keys != HashSet::from(["id", "label", "status", "position", "visible"]) {
            continue;
        }
        let position = coordinate(item.get("position"), 3, false);
        let (Some(Json::String(identity)), Some(Json::String(label))) = (item.get("id"), item.get("label"))
        else {
            continue;
        };
        if identity.is_empty() || seen.contains(identity) || !label_text(label) {
            continue;
        }
        // `status not in {...}` hashes the status. The owner's state is frozen, so the types are
        // the frozen ones (differences row NEXT: a literal source is thawed).
        let status = &item["status"];
        match status {
            Json::Object(_) => return Err(type_error("unhashable type: 'FrozenJsonDict'".into())),
            Json::Array(_) => return Err(type_error("unhashable type: 'FrozenJsonList'".into())),
            _ => {}
        }
        if !matches!(status.as_str(), Some("active" | "complete" | "failed")) {
            continue;
        }
        let Some(position) = position else { continue };
        seen.insert(identity.clone());
        let mut objective = JsonMap::new();
        objective.insert("id".into(), Json::String(identity.clone()));
        objective.insert("label".into(), Json::String(strip(label).to_owned()));
        objective.insert("status".into(), status.clone());
        objective.insert("position".into(), Json::Array(position.clone()));
        result.push(Json::Object(objective));
    }
    Ok(result)
}

/// `_project_selection(value)`: the declared selection fields only.
fn project_selection(value: Option<&Json>) -> JsonMap {
    let Some(Json::Object(value)) = value else { return JsonMap::new() };
    value
        .iter()
        .filter(|(key, _)| TACTICAL_SELECTION_BINDINGS.contains(&key.as_str()))
        .map(|(key, item)| (key.clone(), item.clone()))
        .collect()
}

/// `_project_preview(value, map_revision)`: the Ruleset's preview facts for this revision.
fn project_preview(value: Option<&Json>, map_revision: &Json) -> JsonMap {
    let mut result = JsonMap::new();
    result.insert("authoritative".into(), Json::Bool(false));
    result.insert("label".into(), Json::from("Preview only — the owner resolves the committed action"));
    result.insert("map_revision".into(), map_revision.clone());
    result.insert("path_cost".into(), Json::Null);
    result.insert("los".into(), Json::Null);
    result.insert("exposure".into(), Json::Null);
    result.insert("crossed_zones".into(), Json::Array(Vec::new()));
    let Some(Json::Object(value)) = value else { return result };
    if !py_eq(value.get("map_revision").unwrap_or(&Json::Null), map_revision) {
        return result;
    }
    if let Some(cost @ Json::Number(number)) = value.get("path_cost") {
        if number.as_f64().expect("a JSON number") >= 0.0 {
            result.insert("path_cost".into(), cost.clone());
        }
    }
    if let Some(los @ Json::Bool(_)) = value.get("los") {
        result.insert("los".into(), los.clone());
    }
    if let Some(exposure @ Json::Number(number)) = value.get("exposure") {
        if (0.0..=1.0).contains(&number.as_f64().expect("a JSON number")) {
            result.insert("exposure".into(), exposure.clone());
        }
    }
    if let Some(zones) = sequence(value.get("crossed_zones")) {
        if zones.len() <= 256 && zones.iter().all(Json::is_string) {
            let mut unique: Vec<Json> = Vec::new();
            for zone in zones {
                if !unique.contains(zone) {
                    unique.push(zone.clone());
                }
            }
            result.insert("crossed_zones".into(), Json::Array(unique));
        }
    }
    result
}

/// Copy `fields` of an admitted scene row, then its public display.
fn displayed_row(item: &Json, fields: &[&str], display: JsonMap) -> Json {
    let mut row = JsonMap::new();
    for field in fields {
        row.insert((*field).into(), item[*field].clone());
    }
    row.insert("display".into(), Json::Object(display));
    Json::Object(row)
}

/// `project_tactical_grid(value)`: one bounded player-safe projection from package-owned
/// spatial state.
pub fn project_tactical_grid(value: &Json) -> Result<JsonMap, TacticalGridError> {
    let Json::Object(value) = value else {
        return Err(TacticalGridError::invalid("Tactical grid source must be an object"));
    };
    let admitted = match admit_spatial_scene(value.get("scene").unwrap_or(&Json::Null)) {
        Ok(admitted) => admitted,
        Err(error) if caught(&error) => {
            return Err(TacticalGridError::invalid("Tactical grid scene is invalid"))
        }
        Err(error) => return Err(error.into()),
    };
    let scene = admitted.payload();
    let map = &scene["map"];
    let revision = &map["revision"];
    let rows = |category: &str| scene[category].as_array().expect("admitted rows");

    let mut geometry = Vec::new();
    for item in rows("geometry") {
        if let Some(display) = public_display(&item["metadata"], None, None)? {
            let fields =
                ["id", "map_id", "volume", "active", "movement", "occupies_cells", "visibility", "destroyed"];
            geometry.push(displayed_row(item, &fields, display));
        }
    }
    let mut entities = Vec::new();
    for item in rows("entities") {
        if let Some(display) = public_display(&item["metadata"], None, None)? {
            let fields = [
                "id",
                "map_id",
                "position",
                "footprint",
                "height",
                "facing",
                "active",
                "movement",
                "visibility",
            ];
            entities.push(displayed_row(item, &fields, display));
        }
    }
    let anchors = visible_anchor_ids(scene)?;
    let mut zones = Vec::new();
    for item in rows("zones") {
        if let Some(display) = public_display(&item["metadata"], Some(&admitted), Some(&anchors))? {
            zones.push(displayed_row(item, &["id", "map_id", "shape"], display));
        }
    }

    let mut result = JsonMap::new();
    result.insert("format".into(), Json::from(1));
    result.insert("map_revision".into(), revision.clone());
    result.insert("map".into(), map.clone());
    result.insert("geometry".into(), Json::Array(geometry));
    result.insert("entities".into(), Json::Array(entities));
    result.insert("zones".into(), Json::Array(zones));
    result.insert("terrain".into(), scene["terrain"].clone());
    result.insert("objectives".into(), Json::Array(project_objectives(value.get("objectives"))?));
    result.insert("selection".into(), Json::Object(project_selection(value.get("selection"))));
    result.insert("preview".into(), Json::Object(project_preview(value.get("preview"), revision)));
    Ok(result)
}

/// `coordinate_actor_reference(actions)`: the actor metadata reference the map's actor-bound
/// actions consistently declare (admitted actions declare it as text).
pub fn coordinate_actor_reference(actions: &Json) -> Result<Option<String>, TacticalGridError> {
    let mut references: Vec<Option<&str>> = Vec::new();
    for action in sequence(Some(actions)).into_iter().flatten() {
        let Json::Object(action) = action else { continue };
        let Some(Json::Object(bindings)) = action.get("bindings") else { continue };
        if !bindings.values().any(|source| source == "actor") {
            continue;
        }
        let reference = action.get("actor_reference").and_then(Json::as_str);
        if !references.contains(&reference) {
            references.push(reference);
        }
    }
    if references.len() > 1 {
        return Err(TacticalGridError::invalid("Coordinate actions disagree on actor references"));
    }
    Ok(references.into_iter().next().flatten().map(str::to_owned))
}

/// `project_coordinate_map(value, actor_reference=...)`: the public active combat bodies, each
/// with its Ruleset-selected mechanical actor reference and declared occupied space.
pub fn project_coordinate_map(
    value: &Json,
    actor_reference: Option<&str>,
) -> Result<JsonMap, TacticalGridError> {
    if let Json::Object(map) = value {
        if map.get("enabled").is_some_and(|enabled| enabled != &Json::Bool(true)) {
            return Err(TacticalGridError::invalid("Coordinate map is not active"));
        }
    }
    let mut projected = project_tactical_grid(value)?;
    let Json::Array(entities) = &mut projected["entities"] else { unreachable!("projected entities") };
    entities.retain(|item| item["active"] == Json::Bool(true));
    if let Some(reference_field) = actor_reference {
        let scene = admit_spatial_scene(&value["scene"])?.as_payload();
        let bodies = scene["entities"].as_array().expect("admitted entities");
        let mut references: std::collections::HashMap<&str, Vec<&str>> = std::collections::HashMap::new();
        for item in bodies {
            if let Some(Json::String(reference)) = item["metadata"].get(reference_field) {
                if item["active"] == Json::Bool(true)
                    && strip(reference) == reference
                    && !reference.is_empty()
                    && reference.len() <= MAX_LABEL_BYTES
                {
                    references.entry(reference).or_default().push(item["id"].as_str().expect("an identity"));
                }
            }
        }
        let body = |id: &Json| bodies.iter().find(|item| &item["id"] == id).expect("a projected body");
        let mut assigned = Vec::new();
        for item in projected["entities"].as_array().expect("projected entities") {
            let reference = body(&item["id"])["metadata"].get(reference_field);
            let id = item["id"].as_str().expect("an identity");
            assigned.push(match reference {
                Some(Json::String(reference)) if references.get(reference.as_str()) == Some(&vec![id]) => {
                    Json::String(reference.clone())
                }
                _ => Json::Null,
            });
        }
        projected.insert("actor_reference".into(), Json::from(reference_field));
        let Json::Array(entities) = &mut projected["entities"] else { unreachable!("projected entities") };
        for (item, actor_ref) in entities.iter_mut().zip(assigned) {
            item.as_object_mut().expect("a projected entity").insert("actor_ref".into(), actor_ref);
        }
    }
    if let Some(declaration) = value.get("occupied_space") {
        let Json::Array(entities) = &mut projected["entities"] else { unreachable!("projected entities") };
        for item in entities {
            let item = item.as_object_mut().expect("a projected entity");
            let space = project_occupied_space(declaration, item.get("actor_ref"));
            item.insert("occupied_space".into(), Json::Object(space));
        }
    }
    Ok(projected)
}

/// `_project_occupied_space(value, actor_ref)`: a package-declared category/diameter table,
/// without inferring a body or category. `widths` is read as SRD Lite's literal declaration is
/// (differences row NEXT: Python's `isinstance(widths, (list, tuple))` refuses a frozen list, so
/// widths read from state are `unavailable` there).
fn project_occupied_space(value: &Json, actor_ref: Option<&Json>) -> JsonMap {
    let mut unavailable = JsonMap::new();
    unavailable.insert("status".into(), Json::from("unavailable"));
    unavailable
        .insert("reason".into(), Json::from("Occupied space needs an explicit supported size category."));
    let exact = |map: &JsonMap, keys: &[&str]| {
        map.len() == keys.len() && keys.iter().all(|key| map.contains_key(*key))
    };
    let Json::Object(value) = value else { return unavailable };
    if !exact(value, &["declaration", "actors"]) {
        return unavailable;
    }
    let Json::Object(declaration) = &value["declaration"] else { return unavailable };
    if !exact(declaration, &["convention", "widths", "category_field"]) {
        return unavailable;
    }
    let (Json::Array(widths), Json::String(field), Json::Object(actors), Json::String(convention)) = (
        &declaration["widths"],
        &declaration["category_field"],
        &value["actors"],
        &declaration["convention"],
    ) else {
        return unavailable;
    };
    let widths_valid = (1..=32).contains(&widths.len())
        && widths.iter().all(|width| width.as_f64().is_some_and(|width| 0.0 < width && width <= 1_000_000.0));
    if !widths_valid || field.is_empty() || !(1..=128).contains(&convention.chars().count()) {
        return unavailable;
    }
    let actor = match actor_ref {
        Some(Json::String(actor_ref)) => actors.get(actor_ref),
        _ => None,
    };
    let rank = match actor {
        Some(Json::Object(actor)) => actor.get(field),
        _ => None,
    };
    let Some(rank) = rank.and_then(Json::as_u64).filter(|rank| *rank < widths.len() as u64) else {
        return unavailable;
    };
    let mut declared = JsonMap::new();
    declared.insert("status".into(), Json::from("declared"));
    declared.insert("kind".into(), Json::from("circle"));
    declared.insert("diameter".into(), widths[rank as usize].clone());
    declared.insert("convention".into(), Json::String(convention.clone()));
    declared
}

/// `_map_bounds(projection)`: the lower and upper bounds and the cell size.
fn map_bounds(map: &Json) -> ([f64; 3], [f64; 3], f64) {
    let read = |key: &str| {
        let values = map["bounds"][key].as_array().expect("admitted bounds");
        [float(&values[0]), float(&values[1]), float(&values[2])]
    };
    (read("min"), read("max"), float(&map["grid"]["cell_size"]))
}

/// `math.ceil((upper - lower) / cell_size)` per axis; admission keeps the grid within four
/// million cells.
fn cell_counts(map: &Json) -> [i64; 2] {
    let (lower, upper, cell_size) = map_bounds(map);
    [0, 1].map(|index| ((upper[index] - lower[index]) / cell_size).ceil() as i64)
}

/// `_cell_valid(value, projection)`.
fn cell_valid(value: Option<&Json>, map: &Json) -> bool {
    let Some(cell) = coordinate(value, 2, true) else { return false };
    let counts = cell_counts(map);
    cell.iter().zip(counts).all(|(item, count)| item.as_i64().is_some_and(|item| 0 <= item && item < count))
}

/// `_point_valid(value, projection)`.
fn point_valid(value: Option<&Json>, map: &Json) -> bool {
    let Some(point) = coordinate(value, 3, false) else { return false };
    let (lower, upper, _) = map_bounds(map);
    point.iter().enumerate().all(|(index, item)| (lower[index]..=upper[index]).contains(&float(item)))
}

/// `_selection_value_valid(source, value, projection, action_id=...)`.
fn selection_value_valid(source: &str, value: Option<&Json>, projection: &JsonMap, action_id: &str) -> bool {
    let listed = |key: &str, id: &str| {
        let rows = projection.get(key).and_then(Json::as_array).map(Vec::as_slice).unwrap_or(&[]);
        rows.iter().any(|item| item.get("id").and_then(Json::as_str) == Some(id))
    };
    let map = &projection["map"];
    match source {
        "actor" | "target" | "device" => matches!(value, Some(Json::String(id)) if listed("entities", id)),
        "zone" => matches!(value, Some(Json::String(id)) if listed("zones", id)),
        "destination" => cell_valid(value, map),
        "path" => sequence(value).is_some_and(|items| {
            (2..=MAX_PATH_POINTS).contains(&items.len())
                && items.iter().all(|item| cell_valid(Some(item), map))
        }),
        "aim_point" => point_valid(value, map),
        "orientation" => coordinate(value, 2, false)
            .is_some_and(|items| float(&items[0]) != 0.0 || float(&items[1]) != 0.0),
        "declared_action" => value.and_then(Json::as_str) == Some(action_id),
        "area" => {
            let Some(Json::Object(area)) = value else { return false };
            let keys: HashSet<&str> = area.keys().map(String::as_str).collect();
            let positive =
                |key: &str| area.get(key).and_then(Json::as_f64).is_some_and(|number| number > 0.0);
            match area.get("kind").and_then(Json::as_str) {
                Some("box") if keys == HashSet::from(["kind", "min", "max"]) => {
                    point_valid(area.get("min"), map) && point_valid(area.get("max"), map)
                }
                Some("cylinder") if keys == HashSet::from(["kind", "center", "radius", "height"]) => {
                    point_valid(area.get("center"), map) && positive("radius") && positive("height")
                }
                _ => false,
            }
        }
        _ => false,
    }
}

/// `_owner_path(authoritative_scene, actor_id=..., destination=...)`: a bounded route recomputed
/// from raw intent against the complete owner scene.
fn owner_path(
    authoritative_scene: &Json,
    actor_id: Option<&Json>,
    destination: Option<&Json>,
) -> Result<Json, TacticalGridError> {
    let Some(Json::String(actor_id)) = actor_id else {
        return Err(TacticalGridError::invalid("Tactical path actor is invalid"));
    };
    // `except (KeyError, TypeError, ValueError)`.
    let resolved = |error: SpatialError| match error {
        SpatialError::Validation(_) => {
            TacticalGridError::invalid("Tactical owner path could not be resolved")
        }
        SpatialError::Uncaught { error_type: "KeyError" | "TypeError" | "ValueError", .. } => {
            TacticalGridError::invalid("Tactical owner path could not be resolved")
        }
        other => TacticalGridError::Uncaught(other),
    };
    let scene = admit_spatial_scene(authoritative_scene).map_err(resolved)?;
    let payload = scene.payload();
    let actor = payload["entities"]
        .as_array()
        .expect("admitted entities")
        .iter()
        .find(|item| item["id"] == *actor_id);
    let cell = coordinate(destination, 2, true);
    let (Some(actor), Some(cell)) = (actor, cell) else {
        return Err(TacticalGridError::invalid("Tactical path endpoints are invalid"));
    };
    let map = &payload["map"];
    let (lower, _, cell_size) = map_bounds(map);
    let position = actor["position"].as_array().expect("an admitted position");
    let start: Vec<Json> = (0..2)
        .map(|index| Json::from(((float(&position[index]) - lower[index]) / cell_size).floor() as i64))
        .collect();
    let maximum: i64 = cell_counts(map).iter().product();
    let query = serde_json::json!({
        "kind": "path", "map_id": scene.map_id, "start": start, "goal": cell,
        "allow_diagonal": true, "corner_policy": "forbid_cutting", "include_entities": true,
        "moving_entity_id": actor_id, "max_steps": maximum,
    });
    let result = SpatialEngine::new(authoritative_scene)
        .and_then(|mut engine| engine.query(&query, None))
        .map_err(resolved)?
        .as_payload();
    if result["found"] != Json::Bool(true) {
        return Err(TacticalGridError::invalid("Tactical owner path is unavailable"));
    }
    Ok(result["cells"].clone())
}

/// `resolve_tactical_action_parameters(action, selection, projection, authoritative_scene=...)`:
/// raw renderer intent bound to the action's parameters, with a derived path recomputed under
/// owner authority. Python's `None` scene is `None` or `Some(Json::Null)`.
pub fn resolve_tactical_action_parameters(
    action: &JsonMap,
    selection: &Json,
    projection: &JsonMap,
    authoritative_scene: Option<&Json>,
) -> Result<JsonMap, TacticalGridError> {
    let authoritative_scene = authoritative_scene.filter(|scene| !scene.is_null());
    let (Some(Json::Object(bindings)), Some(Json::Object(fixed))) =
        (action.get("bindings"), action.get("params"))
    else {
        return Err(TacticalGridError::invalid("Tactical action declaration is invalid"));
    };
    let (Some(Json::String(action_id)), Json::Object(selection)) = (action.get("id"), selection) else {
        return Err(TacticalGridError::invalid("Tactical selection is invalid"));
    };
    if !py_eq(
        selection.get("map_revision").unwrap_or(&Json::Null),
        projection.get("map_revision").unwrap_or(&Json::Null),
    ) {
        return Err(TacticalGridError::invalid("Tactical selection is stale"));
    }
    let declared = bindings
        .values()
        .all(|source| source.as_str().is_some_and(|source| TACTICAL_SELECTION_BINDINGS.contains(&source)));
    let mut expected: HashSet<&str> =
        bindings.values().filter_map(Json::as_str).filter(|source| *source != "path").collect();
    expected.insert("map_revision");
    let selected_keys: HashSet<&str> = selection.keys().map(String::as_str).collect();
    if !declared || selected_keys != expected {
        return Err(TacticalGridError::invalid("Tactical selection contains undeclared input"));
    }
    let mut parameters = fixed.clone();
    if bindings.keys().any(|key| parameters.contains_key(key)) {
        return Err(TacticalGridError::invalid("Tactical action binds a fixed parameter"));
    }
    for (parameter, source) in bindings {
        let Json::String(source) = source else {
            return Err(TacticalGridError::invalid("Tactical selection binding is invalid"));
        };
        let mut selected = match (source.as_str(), authoritative_scene) {
            ("path", Some(scene)) => {
                Some(owner_path(scene, selection.get("actor"), selection.get("destination"))?)
            }
            ("path", None) => return Err(TacticalGridError::invalid("Tactical owner path is unavailable")),
            _ => selection.get(source).cloned(),
        };
        if !selection_value_valid(source, selected.as_ref(), projection, action_id) {
            return Err(TacticalGridError::invalid(format!("Tactical {source} selection is invalid")));
        }
        let reference = action.get(if source == "actor" { "actor_reference" } else { "target_reference" });
        if matches!(source.as_str(), "actor" | "target")
            && reference.is_some_and(|reference| !reference.is_null())
        {
            if reference != projection.get("actor_reference").or(Some(&Json::Null)) {
                return Err(TacticalGridError::invalid("Coordinate actor reference is unavailable"));
            }
            let id = selected.as_ref().expect("a valid entity selection");
            let entities = projection["entities"].as_array().expect("projected entities");
            let entity = entities.iter().find(|item| &item["id"] == id).expect("a valid entity selection");
            selected = entity.get("actor_ref").cloned();
            if !matches!(&selected, Some(Json::String(reference)) if !reference.is_empty()) {
                return Err(TacticalGridError::invalid("Coordinate actor reference is invalid or ambiguous"));
            }
        }
        parameters.insert(parameter.clone(), selected.expect("a valid selection"));
    }
    Ok(parameters)
}
