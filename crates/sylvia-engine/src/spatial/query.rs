//! `spatial/_interface.py`: query admission (`admit_spatial_query` and the area shapes,
//! includes and metadata selectors it uses).

use super::scene::{
    array, boolean, capture_payload, cell, choice, coordinate, float, floats, identity, integer, number,
    object, reject, shape, Admit, Bounds, CROSS_MAP, MAX_ABSOLUTE_COORDINATE, MAX_TOTAL_CELLS, OUT_OF_BOUNDS,
    POSITIVE,
};
use super::{AdmittedSpatialScene, SpatialError};
use crate::pymath::{dist, hypot};
use serde_json::json;
use sylvia_base::{Json, JsonMap};

const QUERY_INVALID: &str = "spatial_query_invalid";
const INCLUDE_ORDER: [&str; 3] = ["geometry", "entities", "zones"];

/// `_QUERY_KEYS`.
fn query_keys(kind: &str) -> Option<&'static [&'static str]> {
    Some(match kind {
        "circle_route" => &[
            "kind",
            "map_id",
            "moving_entity_id",
            "destination",
            "moving_radius",
            "circles",
            "metadata_filter",
            "max_tests",
            "edge_costs",
        ],
        "circle_overlap" => &[
            "kind",
            "map_id",
            "moving_entity_id",
            "fixed_entity_id",
            "destination",
            "moving_radius",
            "fixed_radius",
            "origin",
        ],
        "body_proximity" => {
            &["kind", "map_id", "moving_entity_id", "fixed_entity_id", "destination", "radius"]
        }
        "sweep" => &[
            "kind",
            "map_id",
            "moving_entity_id",
            "destination",
            "include_entities",
            "exclude_entity_ids",
            "metadata_filter",
        ],
        "placement" => &[
            "kind",
            "map_id",
            "moving_entity_id",
            "origin",
            "exclude_entity_ids",
            "metadata_filter",
            "max_candidates",
            "containing_zone_id",
            "reference_face",
        ],
        "ray" => &["kind", "map_id", "origin", "destination", "include", "max_intersections"],
        "exposure" => &["kind", "map_id", "observer", "target", "samples", "include", "metadata_filter"],
        "path" => &[
            "kind",
            "map_id",
            "start",
            "goal",
            "allow_diagonal",
            "corner_policy",
            "include_entities",
            "moving_entity_id",
            "metadata_filter",
            "max_steps",
        ],
        "crossed_zones" => &["kind", "map_id", "path", "max_crossings"],
        "area" => &["kind", "map_id", "shape", "include", "max_results"],
        _ => return None,
    })
}

/// `AdmittedSpatialQuery`: one closed bounded query against an admitted scene.
#[derive(Debug, Clone, PartialEq)]
pub struct AdmittedSpatialQuery {
    payload: JsonMap,
    /// `kind`.
    pub kind: String,
    /// `map_id`.
    pub map_id: String,
}

impl AdmittedSpatialQuery {
    /// `as_payload()`: the normalized query.
    pub fn as_payload(&self) -> JsonMap {
        self.payload.clone()
    }

    /// The normalized query, borrowed.
    pub fn payload(&self) -> &JsonMap {
        &self.payload
    }
}

/// The scene's bounds, as `_inside` reads them.
fn scene_bounds(scene: &JsonMap) -> Bounds {
    let bounds = &scene["map"]["bounds"];
    let read = |key: &str| {
        let values = floats(bounds[key].as_array().expect("admitted bounds"));
        [values[0], values[1], values[2]]
    };
    Bounds { lower: read("min"), upper: read("max") }
}

/// `_grid_cell_counts(map)`.
fn grid_cell_counts(scene: &JsonMap) -> [i64; 2] {
    let bounds = scene_bounds(scene);
    let size = float(&scene["map"]["grid"]["cell_size"]);
    [0, 1].map(|index| ((bounds.upper[index] - bounds.lower[index]) / size).ceil() as i64)
}

/// `_query_include(value, allowed, path=...)`: unique names, in `_INCLUDE_ORDER`.
fn query_include(value: &Json, allowed: &[&str], path: &str) -> Admit<Vec<String>> {
    let items = array(value, 1, allowed.len(), QUERY_INVALID, path)?;
    let mut normalized = Vec::new();
    for (index, item) in items.iter().enumerate() {
        normalized.push(choice(item, allowed, QUERY_INVALID, &format!("{path}[{index}]"))?);
    }
    let unique: std::collections::HashSet<&String> = normalized.iter().collect();
    if unique.len() != normalized.len() {
        return reject("Query includes must be unique", QUERY_INVALID, path);
    }
    Ok(INCLUDE_ORDER
        .iter()
        .filter(|name| normalized.iter().any(|item| item == *name))
        .map(|name| (*name).to_owned())
        .collect())
}

/// A text of at most 128 characters (`len(text) <= 128`).
fn bounded_text(value: &Json) -> bool {
    value.as_str().is_some_and(|text| text.chars().count() <= 128)
}

/// `_metadata_selector(value, ...)`.
fn metadata_selector(value: &Json, code: &'static str, path: &str) -> Admit<Json> {
    let selector = object(value, &["key", "value", "missing_matches"], code, path)?;
    if !bounded_text(&selector["key"]) || !bounded_text(&selector["value"]) {
        return reject("Spatial metadata filter requires bounded strings", code, path);
    }
    let missing = boolean(&selector["missing_matches"], code, &format!("{path}.missing_matches"))?;
    Ok(json!({"key": selector["key"], "value": selector["value"], "missing_matches": missing}))
}

/// `max(abs(component) for component in vector)`, by value.
fn largest_magnitude(values: &[f64]) -> f64 {
    values.iter().fold(f64::NEG_INFINITY, |max, value| if value.abs() > max { value.abs() } else { max })
}

/// `_area_prism(value, bounds=..., ...)`: a rectangular volume from the centre of its origin face.
fn area_prism(value: &Json, bounds: &Bounds, code: &'static str, path: &str) -> Admit<Json> {
    let item =
        object(value, &["kind", "origin", "direction", "up", "length", "width", "height"], code, path)?;
    let origin = coordinate(&item["origin"], 3, code, &format!("{path}.origin"))?;
    if !bounds.inside(&floats(&origin)) {
        return reject("Area origin is outside map bounds", OUT_OF_BOUNDS, format!("{path}.origin"));
    }
    let mut vectors = Vec::new();
    for field in ["direction", "up"] {
        let vector = floats(&coordinate(&item[field], 3, code, &format!("{path}.{field}"))?);
        let scale = largest_magnitude(&vector);
        if scale == 0.0 {
            return reject("Prism direction and up must be nonzero", code, format!("{path}.{field}"));
        }
        let scaled: Vec<f64> = vector.iter().map(|component| component / scale).collect();
        let magnitude = hypot(&scaled);
        vectors.push(scaled.iter().map(|component| component / magnitude).collect::<Vec<f64>>());
    }
    let (axis, reference) = (&vectors[0], &vectors[1]);
    let perpendicular = [
        reference[1] * axis[2] - reference[2] * axis[1],
        reference[2] * axis[0] - reference[0] * axis[2],
        reference[0] * axis[1] - reference[1] * axis[0],
    ];
    if hypot(&perpendicular) <= 1e-12 {
        return reject("Prism up must be distinguishable from its direction", code, format!("{path}.up"));
    }
    let mut dimensions = Vec::new();
    for field in ["length", "width", "height"] {
        dimensions.push(number(&item[field], Some(POSITIVE), code, &format!("{path}.{field}"))?);
    }
    Ok(json!({
        "kind": "prism", "origin": origin, "direction": vectors[0], "up": vectors[1],
        "length": dimensions[0], "width": dimensions[1], "height": dimensions[2],
    }))
}

/// `_area_shape(value, bounds=..., ...)`: the query-only volumes, or a scene shape.
fn area_shape(value: &Json, bounds: &Bounds, code: &'static str, path: &str) -> Admit<Json> {
    let kind = value.as_object().and_then(|map| map.get("kind"));
    if kind == Some(&json!("prism")) {
        return area_prism(value, bounds, code, path);
    }
    let kind = match kind.and_then(Json::as_str) {
        Some(kind @ ("sphere" | "cone")) => kind,
        _ => return shape(value, bounds, code, path),
    };
    let fields: &[&str] = if kind == "sphere" {
        &["kind", "center", "radius"]
    } else {
        &["kind", "apex", "direction", "length", "radius"]
    };
    let item = object(value, fields, code, path)?;
    let origin_field = if kind == "sphere" { "center" } else { "apex" };
    let origin = coordinate(&item[origin_field], 3, code, &format!("{path}.{origin_field}"))?;
    if !bounds.inside(&floats(&origin)) {
        return reject("Area origin is outside map bounds", OUT_OF_BOUNDS, format!("{path}.{origin_field}"));
    }
    let radius = number(&item["radius"], Some(POSITIVE), code, &format!("{path}.radius"))?;
    let mut normalized = JsonMap::new();
    normalized.insert("kind".into(), json!(kind));
    normalized.insert(origin_field.into(), json!(origin));
    normalized.insert("radius".into(), radius.clone());
    let mut extent = float(&radius);
    if kind == "cone" {
        let direction = coordinate(&item["direction"], 3, code, &format!("{path}.direction"))?;
        let magnitude = hypot(&floats(&direction));
        if magnitude == 0.0 || !magnitude.is_finite() {
            return reject("Cone direction must be finite and nonzero", code, format!("{path}.direction"));
        }
        let length = number(&item["length"], Some(POSITIVE), code, &format!("{path}.length"))?;
        extent += float(&length);
        normalized.insert("direction".into(), json!(direction));
        normalized.insert("length".into(), length);
    }
    if !floats(&origin)
        .iter()
        .all(|component| (component + extent).is_finite() && (component - extent).is_finite())
    {
        return reject("Area extent must be finite", code, path);
    }
    Ok(Json::Object(normalized))
}

/// The ids of a scene collection, optionally only the active ones.
fn ids(scene: &JsonMap, collection: &str, active_only: bool) -> Vec<String> {
    scene[collection]
        .as_array()
        .expect("an admitted collection")
        .iter()
        .filter(|entry| !active_only || entry["active"] == json!(true))
        .map(|entry| entry["id"].as_str().expect("an admitted identity").to_owned())
        .collect()
}

/// `selector["key"] and not (actual == selector["value"] or (actual is None and missing_matches is True))`.
fn misses_selector(metadata: &Json, selector: &JsonMap) -> bool {
    let key = selector["key"].as_str().unwrap_or_default();
    if key.is_empty() {
        return false;
    }
    let actual = metadata.get(key).filter(|value| !value.is_null());
    let matches = match actual {
        Some(actual) => actual == &selector["value"],
        None => selector["missing_matches"] == json!(true),
    };
    !matches
}

/// Unique entity ids from `exclude_entity_ids`, each naming an active entity.
fn exclusions(value: &Json, active: &[String], message: &'static str) -> Admit<Vec<String>> {
    let items = array(value, 0, 64, QUERY_INVALID, "$.exclude_entity_ids")?;
    let mut names = Vec::new();
    for item in items {
        names.push(identity(item, QUERY_INVALID, "$.exclude_entity_ids")?);
    }
    let unique: std::collections::HashSet<&String> = names.iter().collect();
    if unique.len() != names.len() || names.iter().any(|name| !active.contains(name)) {
        return reject(message, QUERY_INVALID, "$.exclude_entity_ids");
    }
    Ok(names)
}

/// `admit_spatial_query(scene, value)`: validate a bounded query against one admitted scene.
pub fn admit_spatial_query(
    scene: &AdmittedSpatialScene,
    value: &Json,
) -> Result<AdmittedSpatialQuery, SpatialError> {
    Ok(admit(scene, value)?)
}

fn admit(scene: &AdmittedSpatialScene, value: &Json) -> Admit<AdmittedSpatialQuery> {
    let code = QUERY_INVALID;
    let payload = capture_payload(value, code)?;
    let Some(kind) = payload.get("kind").and_then(Json::as_str).filter(|kind| query_keys(kind).is_some())
    else {
        return reject("Spatial query kind is invalid", code, "$.kind");
    };
    let kind = kind.to_owned();
    let mut expected: Vec<&str> = query_keys(&kind).expect("a known kind").to_vec();
    let drop_unless_given = |names: &[&str], expected: &mut Vec<&str>| {
        for name in names {
            if !payload.contains_key(*name) {
                expected.retain(|key| key != name);
            }
        }
    };
    match kind.as_str() {
        "circle_overlap" => drop_unless_given(&["origin"], &mut expected),
        "circle_route" => drop_unless_given(&["edge_costs"], &mut expected),
        "path" => drop_unless_given(&["moving_entity_id", "metadata_filter"], &mut expected),
        "exposure" => drop_unless_given(&["metadata_filter"], &mut expected),
        "sweep" => drop_unless_given(&["metadata_filter", "exclude_entity_ids"], &mut expected),
        "placement" => drop_unless_given(&["containing_zone_id", "reference_face"], &mut expected),
        _ => {}
    }
    let root = Json::Object(payload.clone());
    let item = object(&root, &expected, code, "$")?;
    if item["map_id"].as_str() != Some(scene.map_id.as_str()) {
        return reject("Spatial query belongs to another map", CROSS_MAP, "$.map_id");
    }
    let admitted = scene.payload();
    let bounds = scene_bounds(admitted);
    let mut normalized = JsonMap::new();
    normalized.insert("kind".into(), json!(kind));
    normalized.insert("map_id".into(), json!(scene.map_id));
    let mut put = |key: &str, value: Json| {
        normalized.insert(key.to_owned(), value);
    };
    match kind.as_str() {
        "ray" => {
            let origin = coordinate(&item["origin"], 3, code, "$.origin")?;
            let destination = coordinate(&item["destination"], 3, code, "$.destination")?;
            if !bounds.inside(&floats(&origin)) || !bounds.inside(&floats(&destination)) {
                return reject("Ray endpoint is outside map bounds", OUT_OF_BOUNDS, "$");
            }
            let include = query_include(&item["include"], &["geometry", "entities"], "$.include")?;
            let maximum = integer(&item["max_intersections"], 1, 1024, code, "$.max_intersections")?;
            put("origin", json!(origin));
            put("destination", json!(destination));
            put("include", json!(include));
            put("max_intersections", json!(maximum));
        }
        "exposure" => {
            let observer = object(&item["observer"], &["point"], code, "$.observer")?;
            let point = coordinate(&observer["point"], 3, code, "$.observer.point")?;
            if !bounds.inside(&floats(&point)) {
                return reject("Observer is outside map bounds", OUT_OF_BOUNDS, "$.observer.point");
            }
            let target = object(&item["target"], &["entity_id"], code, "$.target")?;
            let entity_id = identity(&target["entity_id"], code, "$.target.entity_id")?;
            if !ids(admitted, "entities", false).contains(&entity_id) {
                return reject("Exposure target entity is unknown", code, "$.target.entity_id");
            }
            let samples = object(&item["samples"], &["horizontal", "vertical"], code, "$.samples")?;
            let horizontal = integer(&samples["horizontal"], 1, 9, code, "$.samples.horizontal")?;
            let vertical = integer(&samples["vertical"], 1, 9, code, "$.samples.vertical")?;
            let include = query_include(&item["include"], &["geometry", "entities"], "$.include")?;
            put("observer", json!({"point": point}));
            put("target", json!({"entity_id": entity_id}));
            put("samples", json!({"horizontal": horizontal, "vertical": vertical}));
            put("include", json!(include));
            if item.contains_key("metadata_filter") {
                put(
                    "metadata_filter",
                    metadata_selector(&item["metadata_filter"], code, "$.metadata_filter")?,
                );
            }
        }
        "circle_route" => {
            let active = ids(admitted, "entities", true);
            let moving = identity(&item["moving_entity_id"], code, "$.moving_entity_id")?;
            if !active.contains(&moving) {
                return reject("Moving entity is unavailable", code, "$.moving_entity_id");
            }
            let destination = coordinate(&item["destination"], 3, code, "$.destination")?;
            if !bounds.inside(&floats(&destination)) {
                return reject("Destination is outside map bounds", code, "$.destination");
            }
            let radius = number(&item["moving_radius"], Some(0.0), code, "$.moving_radius")?;
            if !(0.0 < float(&radius) && float(&radius) <= MAX_ABSOLUTE_COORDINATE) {
                return reject("Occupied radius must be positive and bounded", code, "$.moving_radius");
            }
            let mut circles = Vec::new();
            let mut seen = vec![moving.clone()];
            for (index, raw) in array(&item["circles"], 0, 64, code, "$.circles")?.iter().enumerate() {
                let path = format!("$.circles[{index}]");
                let circle = object(raw, &["entity_id", "radius", "blocking"], code, &path)?;
                let id = identity(&circle["entity_id"], code, &format!("{path}.entity_id"))?;
                if !active.contains(&id) || seen.contains(&id) {
                    return reject("Route circles must name distinct active entities", code, path);
                }
                seen.push(id.clone());
                let fixed = number(&circle["radius"], Some(0.0), code, &format!("{path}.radius"))?;
                if !(0.0 < float(&fixed) && float(&fixed) <= MAX_ABSOLUTE_COORDINATE) {
                    return reject(
                        "Occupied radius must be positive and bounded",
                        code,
                        format!("{path}.radius"),
                    );
                }
                let blocking = boolean(&circle["blocking"], code, &format!("{path}.blocking"))?;
                circles.push((id.clone(), json!({"entity_id": id, "radius": fixed, "blocking": blocking})));
            }
            circles.sort_by(|a, b| a.0.cmp(&b.0));
            let filter = metadata_selector(&item["metadata_filter"], code, "$.metadata_filter")?;
            let max_tests = integer(&item["max_tests"], 1, 16384, code, "$.max_tests")?;
            put("moving_entity_id", json!(moving));
            put("destination", json!(destination));
            put("moving_radius", radius);
            put("circles", Json::Array(circles.into_iter().map(|(_, circle)| circle).collect()));
            put("metadata_filter", filter);
            put("max_tests", json!(max_tests));
            let empty = Json::Array(Vec::new());
            let mut edge_costs = Vec::new();
            for (index, raw) in
                array(item.get("edge_costs").unwrap_or(&empty), 0, 16384, code, "$.edge_costs")?
                    .iter()
                    .enumerate()
            {
                let path = format!("$.edge_costs[{index}]");
                let edge = object(raw, &["start", "end", "cost"], code, &path)?;
                let start = coordinate(&edge["start"], 3, code, &format!("{path}.start"))?;
                let end = coordinate(&edge["end"], 3, code, &format!("{path}.end"))?;
                let cost = number(&edge["cost"], Some(0.0), code, &format!("{path}.cost"))?;
                if float(&cost) + 1e-9 < dist(&floats(&start), &floats(&end)) {
                    return reject("Route edge cost must cover its geometric distance", code, path);
                }
                edge_costs.push(json!({"start": start, "end": end, "cost": cost}));
            }
            put("edge_costs", Json::Array(edge_costs));
        }
        "body_proximity" | "circle_overlap" => {
            let active = ids(admitted, "entities", true);
            let mut names = Vec::new();
            for name in ["moving_entity_id", "fixed_entity_id"] {
                let id = identity(&item[name], code, &format!("$.{name}"))?;
                if !active.contains(&id) {
                    return reject("Proximity entity is unavailable", code, format!("$.{name}"));
                }
                put(name, json!(id));
                names.push(id);
            }
            let destination = coordinate(&item["destination"], 3, code, "$.destination")?;
            if !bounds.inside(&floats(&destination)) {
                return reject("Destination is outside map bounds", OUT_OF_BOUNDS, "$.destination");
            }
            put("destination", json!(destination));
            if kind == "circle_overlap" && item.contains_key("origin") {
                let origin = coordinate(&item["origin"], 3, code, "$.origin")?;
                if !bounds.inside(&floats(&origin)) {
                    return reject("Origin is outside map bounds", code, "$.origin");
                }
                put("origin", json!(origin));
            }
            if kind == "body_proximity" {
                put("radius", number(&item["radius"], Some(0.0), code, "$.radius")?);
            } else {
                if names[0] == names[1] {
                    return reject("Overlap requires two distinct entities", code, "$");
                }
                for name in ["moving_radius", "fixed_radius"] {
                    let radius = number(&item[name], Some(0.0), code, &format!("$.{name}"))?;
                    if float(&radius) <= 0.0 || float(&radius) > MAX_ABSOLUTE_COORDINATE {
                        return reject(
                            "Occupied radius must be positive and bounded",
                            code,
                            format!("$.{name}"),
                        );
                    }
                    put(name, radius);
                }
            }
        }
        "sweep" => {
            let moving = identity(&item["moving_entity_id"], code, "$.moving_entity_id")?;
            let active = ids(admitted, "entities", true);
            if !active.contains(&moving) {
                return reject("Moving entity is unavailable", code, "$.moving_entity_id");
            }
            let empty = Json::Array(Vec::new());
            let excluded = exclusions(
                item.get("exclude_entity_ids").unwrap_or(&empty),
                &active,
                "Sweep exclusions must name unique active entities",
            )?;
            let destination = coordinate(&item["destination"], 3, code, "$.destination")?;
            let include_entities = boolean(&item["include_entities"], code, "$.include_entities")?;
            let default_filter = json!({"key": "", "value": "", "missing_matches": false});
            let filter = metadata_selector(
                item.get("metadata_filter").unwrap_or(&default_filter),
                code,
                "$.metadata_filter",
            )?;
            put("moving_entity_id", json!(moving));
            put("destination", json!(destination));
            put("include_entities", json!(include_entities));
            put("exclude_entity_ids", json!(excluded));
            put("metadata_filter", filter);
        }
        "placement" => {
            let moving = identity(&item["moving_entity_id"], code, "$.moving_entity_id")?;
            let active = ids(admitted, "entities", true);
            if !active.contains(&moving) {
                return reject("Moving entity is unavailable", code, "$.moving_entity_id");
            }
            let excluded = exclusions(
                &item["exclude_entity_ids"],
                &active,
                "Placement exclusions must name unique active entities",
            )?;
            let selector = object(
                &item["metadata_filter"],
                &["key", "value", "missing_matches"],
                code,
                "$.metadata_filter",
            )?;
            if !bounded_text(&selector["key"]) || !bounded_text(&selector["value"]) {
                return reject(
                    "Placement metadata filter requires bounded strings",
                    code,
                    "$.metadata_filter",
                );
            }
            let origin = coordinate(&item["origin"], 3, code, "$.origin")?;
            let missing = boolean(&selector["missing_matches"], code, "$.metadata_filter.missing_matches")?;
            let max_candidates = integer(&item["max_candidates"], 1, 100_000, code, "$.max_candidates")?;
            put("moving_entity_id", json!(moving));
            put("origin", json!(origin));
            put("exclude_entity_ids", json!(excluded));
            put(
                "metadata_filter",
                json!({"key": selector["key"], "value": selector["value"], "missing_matches": missing}),
            );
            put("max_candidates", json!(max_candidates));
            if item.contains_key("containing_zone_id") {
                let zone_id = identity(&item["containing_zone_id"], code, "$.containing_zone_id")?;
                let zones = admitted["zones"].as_array().expect("admitted zones");
                let Some(zone) = zones.iter().find(|entry| entry["id"].as_str() == Some(zone_id.as_str()))
                else {
                    return reject("Containing zone is unavailable", code, "$.containing_zone_id");
                };
                if misses_selector(&zone["metadata"], selector) {
                    return reject(
                        "Containing zone does not match the placement metadata filter",
                        code,
                        "$.containing_zone_id",
                    );
                }
                put("containing_zone_id", json!(zone_id));
            }
            if item.contains_key("reference_face") {
                let path = "$.reference_face";
                let reference = object(&item["reference_face"], &["target", "face"], code, path)?;
                let target = object(&reference["target"], &["kind", "id"], code, &format!("{path}.target"))?;
                let target_kind =
                    choice(&target["kind"], &["geometry", "entity"], code, &format!("{path}.target.kind"))?;
                let target_id = identity(&target["id"], code, &format!("{path}.target.id"))?;
                let collection = if target_kind == "geometry" { "geometry" } else { "entities" };
                let entries = admitted[collection].as_array().expect("an admitted collection");
                let Some(source) =
                    entries.iter().find(|entry| entry["id"].as_str() == Some(target_id.as_str()))
                else {
                    return reject("Placement reference is unavailable", code, format!("{path}.target.id"));
                };
                if misses_selector(&source["metadata"], selector) {
                    return reject(
                        "Placement reference does not match the placement metadata filter",
                        code,
                        format!("{path}.target"),
                    );
                }
                let face = choice(
                    &reference["face"],
                    &["east", "west", "north", "south", "up", "down"],
                    code,
                    &format!("{path}.face"),
                )?;
                put(
                    "reference_face",
                    json!({"target": {"kind": target_kind, "id": target_id}, "face": face}),
                );
            }
        }
        "path" => {
            let start = cell(&item["start"], code, "$.start")?;
            let goal = cell(&item["goal"], code, "$.goal")?;
            let axis_cells = grid_cell_counts(admitted);
            if (0..2).any(|index| start[index] >= axis_cells[index]) {
                return reject("Path start is outside map bounds", OUT_OF_BOUNDS, "$.start");
            }
            if (0..2).any(|index| goal[index] >= axis_cells[index]) {
                return reject("Path goal is outside map bounds", OUT_OF_BOUNDS, "$.goal");
            }
            let include_entities = boolean(&item["include_entities"], code, "$.include_entities")?;
            let mut moving = None;
            if item.contains_key("moving_entity_id") {
                let id = identity(&item["moving_entity_id"], code, "$.moving_entity_id")?;
                if !include_entities || !ids(admitted, "entities", true).contains(&id) {
                    return reject("Moving entity is unavailable", code, "$.moving_entity_id");
                }
                moving = Some(id);
            }
            let diagonal = boolean(&item["allow_diagonal"], code, "$.allow_diagonal")?;
            let corner = choice(
                &item["corner_policy"],
                &["allow_cutting", "forbid_cutting"],
                code,
                "$.corner_policy",
            )?;
            let max_steps = integer(&item["max_steps"], 1, MAX_TOTAL_CELLS, code, "$.max_steps")?;
            put("start", json!(start));
            put("goal", json!(goal));
            put("allow_diagonal", json!(diagonal));
            put("corner_policy", json!(corner));
            put("include_entities", json!(include_entities));
            put("max_steps", json!(max_steps));
            if let Some(id) = moving {
                put("moving_entity_id", json!(id));
            }
            if item.contains_key("metadata_filter") {
                put(
                    "metadata_filter",
                    metadata_selector(&item["metadata_filter"], code, "$.metadata_filter")?,
                );
            }
        }
        "crossed_zones" => {
            let points = array(&item["path"], 2, 256, code, "$.path")?;
            let mut path = Vec::new();
            for (index, point) in points.iter().enumerate() {
                path.push(coordinate(point, 3, code, &format!("$.path[{index}]"))?);
            }
            if path.iter().any(|point| !bounds.inside(&floats(point))) {
                return reject("Zone path is outside map bounds", OUT_OF_BOUNDS, "$.path");
            }
            let max_crossings = integer(&item["max_crossings"], 1, 4096, code, "$.max_crossings")?;
            put("path", json!(path));
            put("max_crossings", json!(max_crossings));
        }
        _ => {
            let area = area_shape(&item["shape"], &bounds, code, "$.shape")?;
            let include = query_include(&item["include"], &["geometry", "entities", "zones"], "$.include")?;
            let max_results = integer(&item["max_results"], 1, 4096, code, "$.max_results")?;
            put("shape", area);
            put("include", json!(include));
            put("max_results", json!(max_results));
        }
    }
    Ok(AdmittedSpatialQuery { payload: normalized, kind, map_id: scene.map_id.clone() })
}
