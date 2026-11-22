//! `spatial/_interface.py`: scene admission (`admit_spatial_scene` and the validators it uses).

use super::{SpatialError, SpatialValidationError, SPATIAL_ALGORITHM_VERSION};
use serde_json::json;
use sha2::{Digest, Sha256};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::package_library::canonical_json_bytes;

const MAX_ABSOLUTE_COORDINATE: f64 = 1_000_000.0;
const MAX_AXIS_CELLS: i64 = 2_048;
const MAX_TOTAL_CELLS: i64 = 4_000_000;
const MAX_GEOMETRY: usize = 20_000;
const MAX_ENTITIES: usize = 5_000;
const MAX_ZONES: usize = 5_000;
const MAX_TERRAIN: usize = 100_000;
const MAX_JSON_DEPTH: usize = 24;
const MAX_JSON_NODES: usize = 100_000;
const MAX_METADATA_BYTES: usize = 16_384;
/// `minimum=0.000001`, the smallest positive size a scene may declare.
const POSITIVE: f64 = 0.000001;

const SCENE_KEYS: &[&str] = &["format", "map", "geometry", "entities", "zones", "terrain"];
const MAP_KEYS: &[&str] = &["id", "revision", "bounds", "grid", "layers"];
const BOUNDS_KEYS: &[&str] = &["min", "max"];
const GRID_KEYS: &[&str] = &["cell_size", "unit", "origin"];
const LAYER_KEYS: &[&str] = &["id", "min_elevation", "max_elevation"];
const GEOMETRY_KEYS: &[&str] =
    &["id", "map_id", "volume", "active", "movement", "occupies_cells", "visibility", "metadata", "destroyed"];
const DESTROYED_KEYS: &[&str] = &["active", "movement", "visibility"];
const ENTITY_KEYS: &[&str] =
    &["id", "map_id", "position", "footprint", "height", "facing", "active", "movement", "visibility", "metadata"];
const ZONE_KEYS: &[&str] = &["id", "map_id", "shape", "metadata"];
const TERRAIN_KEYS: &[&str] = &["cell", "layer_id", "cost"];

const SCENE_INVALID: &str = "spatial_scene_invalid";
const LIMIT_EXCEEDED: &str = "spatial_limit_exceeded";
const OUT_OF_BOUNDS: &str = "spatial_out_of_bounds";
const DUPLICATE_ID: &str = "spatial_duplicate_id";
const CROSS_MAP: &str = "spatial_cross_map";

type Admit<T> = Result<T, SpatialValidationError>;

fn reject<T>(message: &'static str, code: &'static str, path: impl Into<String>) -> Admit<T> {
    Err(SpatialValidationError { message, code, path: path.into() })
}

/// `AdmittedSpatialScene`: an immutable authoritative scene with stable snapshot provenance.
#[derive(Debug, Clone, PartialEq)]
pub struct AdmittedSpatialScene {
    payload: JsonMap,
    /// `map_id`.
    pub map_id: String,
    /// `geometry_revision`.
    pub geometry_revision: i64,
    /// `snapshot_id`: the SHA-256 of the canonical scene and algorithm version.
    pub snapshot_id: String,
    /// `algorithm_version`.
    pub algorithm_version: &'static str,
}

impl AdmittedSpatialScene {
    /// `as_payload()`: the normalized scene.
    pub fn as_payload(&self) -> JsonMap {
        self.payload.clone()
    }

    /// The normalized scene, borrowed.
    pub fn payload(&self) -> &JsonMap {
        &self.payload
    }
}

/// `admit_spatial_scene(value)`: validate and capture one complete owner-authoritative scene.
pub fn admit_spatial_scene(value: &Json) -> Result<AdmittedSpatialScene, SpatialError> {
    let captured = capture_payload(value, SCENE_INVALID)?;
    let payload = validate_scene(&captured)?;
    let map = payload["map"].as_object().expect("the normalized map");
    let map_id = map["id"].as_str().expect("an admitted identity").to_owned();
    let geometry_revision = map["revision"].as_i64().expect("an admitted revision");
    let identity = json!({"algorithm_version": SPATIAL_ALGORITHM_VERSION, "scene": Json::Object(payload.clone())});
    let snapshot_id = hex(&Sha256::digest(canonical_json_bytes(&identity)));
    Ok(AdmittedSpatialScene {
        payload,
        map_id,
        geometry_revision,
        snapshot_id,
        algorithm_version: SPATIAL_ALGORITHM_VERSION,
    })
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// `_capture_payload(value, code=...)`: the depth and node limits, walked in Python's stack
/// order (the last child first), then the top-level object check. A [`Json`] holds no
/// non-string keys and no non-finite numbers, so those refusals cannot arise.
fn capture_payload(value: &Json, code: &'static str) -> Admit<JsonMap> {
    let mut stack: Vec<(&Json, usize, String)> = vec![(value, 1, "$".to_owned())];
    let mut nodes = 0usize;
    while let Some((item, depth, path)) = stack.pop() {
        if depth > MAX_JSON_DEPTH {
            return reject("Spatial JSON nesting exceeds the limit", LIMIT_EXCEEDED, path);
        }
        nodes += 1;
        if nodes > MAX_JSON_NODES {
            return reject("Spatial JSON node count exceeds the limit", LIMIT_EXCEEDED, path);
        }
        match item {
            Json::Object(map) => {
                for (key, child) in map {
                    stack.push((child, depth + 1, format!("{path}.{key}")));
                }
            }
            Json::Array(items) => {
                for (index, child) in items.iter().enumerate() {
                    stack.push((child, depth + 1, format!("{path}[{index}]")));
                }
            }
            _ => {}
        }
    }
    match value {
        Json::Object(map) => Ok(map.clone()),
        _ => reject("Spatial payload must be an object", code, "$"),
    }
}

/// `type(value).__name__`.
fn type_name(value: &Json) -> &'static str {
    match value {
        Json::Null => "NoneType",
        Json::Bool(_) => "bool",
        Json::Number(number) if number.is_f64() => "float",
        Json::Number(_) => "int",
        Json::String(_) => "str",
        Json::Array(_) => "list",
        Json::Object(_) => "dict",
    }
}

/// `float(value)` of an admitted number.
fn float(value: &Json) -> f64 {
    value.as_f64().expect("an admitted number")
}

/// `value == 1` under Python's equality: `1`, `1.0` and `True`.
fn equals_one(value: &Json) -> bool {
    match value {
        Json::Bool(flag) => *flag,
        Json::Number(number) => number.as_f64() == Some(1.0),
        _ => false,
    }
}

/// `_object(value, keys, ...)`: an object with exactly these keys.
fn object<'v>(value: &'v Json, keys: &[&str], code: &'static str, path: &str) -> Admit<&'v JsonMap> {
    let Json::Object(map) = value else { return reject("Spatial object is required", code, path) };
    if map.len() != keys.len() || !keys.iter().all(|key| map.contains_key(*key)) {
        return reject("Spatial object uses a closed schema", code, path);
    }
    Ok(map)
}

/// `_array(value, minimum=..., maximum=..., ...)`.
fn array<'v>(value: &'v Json, minimum: usize, maximum: usize, code: &'static str, path: &str) -> Admit<&'v Vec<Json>> {
    match value {
        Json::Array(items) if (minimum..=maximum).contains(&items.len()) => Ok(items),
        _ => reject("Spatial array length is invalid", code, path),
    }
}

/// `_number(value, minimum=..., ...)`: the value as written.
fn number(value: &Json, minimum: Option<f64>, code: &'static str, path: &str) -> Admit<Json> {
    let Json::Number(written) = value else { return reject("Finite number is required", code, path) };
    let number = written.as_f64().expect("a JSON number");
    if !number.is_finite() || number.abs() > MAX_ABSOLUTE_COORDINATE {
        return reject("Spatial number is non-finite or outside limits", code, path);
    }
    if minimum.is_some_and(|minimum| number < minimum) {
        return reject("Spatial number is below its minimum", code, path);
    }
    Ok(value.clone())
}

/// `_integer(value, minimum=..., maximum=..., ...)`.
fn integer(value: &Json, minimum: i64, maximum: i64, code: &'static str, path: &str) -> Admit<i64> {
    match value {
        Json::Number(number) if !number.is_f64() => match number.as_i64() {
            Some(integer) if (minimum..=maximum).contains(&integer) => Ok(integer),
            _ => reject("Bounded integer is required", code, path),
        },
        _ => reject("Bounded integer is required", code, path),
    }
}

/// `_boolean(value, ...)`.
fn boolean(value: &Json, code: &'static str, path: &str) -> Admit<bool> {
    match value {
        Json::Bool(flag) => Ok(*flag),
        _ => reject("Boolean is required", code, path),
    }
}

/// `_identity(value, ...)`: `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`, the whole string.
fn identity(value: &Json, code: &'static str, path: &str) -> Admit<String> {
    if let Json::String(text) = value {
        let mut bytes = text.bytes();
        let first_ok = bytes.next().is_some_and(|byte| byte.is_ascii_alphanumeric());
        let rest_ok = bytes.all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-'));
        if first_ok && rest_ok && text.len() <= 128 {
            return Ok(text.clone());
        }
    }
    reject("Stable spatial identity is invalid", code, path)
}

/// `_coordinate(value, dimensions, ...)`: the numbers as written.
fn coordinate(value: &Json, dimensions: usize, code: &'static str, path: &str) -> Admit<Vec<Json>> {
    let items = array(value, dimensions, dimensions, code, path)?;
    items.iter().enumerate().map(|(index, item)| number(item, None, code, &format!("{path}[{index}]"))).collect()
}

/// `_cell(value, ...)`.
fn cell(value: &Json, code: &'static str, path: &str) -> Admit<[i64; 2]> {
    let items = array(value, 2, 2, code, path)?;
    let x = integer(&items[0], 0, MAX_AXIS_CELLS - 1, code, &format!("{path}[0]"))?;
    let y = integer(&items[1], 0, MAX_AXIS_CELLS - 1, code, &format!("{path}[1]"))?;
    Ok([x, y])
}

/// `_enum(value, allowed, ...)`.
fn choice(value: &Json, allowed: &[&str], code: &'static str, path: &str) -> Admit<String> {
    match value {
        Json::String(text) if allowed.contains(&text.as_str()) => Ok(text.clone()),
        _ => reject("Spatial enum value is invalid", code, path),
    }
}

fn movement(value: &Json, code: &'static str, path: &str) -> Admit<String> {
    choice(value, &["passable", "blocking"], code, path)
}

fn visibility(value: &Json, code: &'static str, path: &str) -> Admit<String> {
    choice(value, &["clear", "opaque"], code, path)
}

/// `_metadata(value, ...)`. Python raises the byte-limit refusal inside a `try` that catches
/// `ValueError`, which `SpatialValidationError` is, so an oversized object is reported as
/// invalid metadata with the caller's code.
fn metadata(value: &Json, code: &'static str, path: &str) -> Admit<Json> {
    if !value.is_object() {
        return reject("Spatial metadata must be an object", code, path);
    }
    if canonical_json_bytes(value).len() > MAX_METADATA_BYTES {
        return reject("Spatial metadata is invalid", code, path);
    }
    Ok(value.clone())
}

/// The scene's map bounds, on `float(...)` values.
struct Bounds {
    lower: [f64; 3],
    upper: [f64; 3],
}

impl Bounds {
    /// `_inside(point, bounds)`.
    fn inside(&self, point: &[f64]) -> bool {
        point.iter().enumerate().all(|(index, value)| self.lower[index] <= *value && *value <= self.upper[index])
    }
}

fn floats(values: &[Json]) -> Vec<f64> {
    values.iter().map(float).collect()
}

/// `_box(value, bounds=..., ...)`.
fn volume_box(value: &Json, bounds: &Bounds, code: &'static str, path: &str) -> Admit<Json> {
    let item = object(value, &["kind", "min", "max"], code, path)?;
    if item["kind"] != json!("box") {
        return reject("Box volume kind is required", code, format!("{path}.kind"));
    }
    let lower = coordinate(&item["min"], 3, code, &format!("{path}.min"))?;
    let upper = coordinate(&item["max"], 3, code, &format!("{path}.max"))?;
    let (low, high) = (floats(&lower), floats(&upper));
    if (0..3).any(|index| low[index] >= high[index]) {
        return reject("Box volume must have positive extent", code, path);
    }
    if !bounds.inside(&low) || !bounds.inside(&high) {
        return reject("Box volume is outside map bounds", OUT_OF_BOUNDS, path);
    }
    Ok(json!({"kind": "box", "min": lower, "max": upper}))
}

/// `_shape(value, bounds=..., ...)`: a box or an upright cylinder.
fn shape(value: &Json, bounds: &Bounds, code: &'static str, path: &str) -> Admit<Json> {
    let Json::Object(map) = value else { return reject("Spatial shape must be an object", code, path) };
    if map.get("kind") == Some(&json!("box")) {
        return volume_box(value, bounds, code, path);
    }
    let item = object(value, &["kind", "center", "radius", "height"], code, path)?;
    if item["kind"] != json!("cylinder") {
        return reject("Spatial shape kind is invalid", code, format!("{path}.kind"));
    }
    let center = coordinate(&item["center"], 3, code, &format!("{path}.center"))?;
    let radius = number(&item["radius"], Some(POSITIVE), code, &format!("{path}.radius"))?;
    let height = number(&item["height"], Some(POSITIVE), code, &format!("{path}.height"))?;
    let (c, r, h) = (floats(&center), float(&radius), float(&height));
    let minimum = [c[0] - r, c[1] - r, c[2]];
    let maximum = [c[0] + r, c[1] + r, c[2] + h];
    if !bounds.inside(&minimum) || !bounds.inside(&maximum) {
        return reject("Cylinder is outside map bounds", OUT_OF_BOUNDS, path);
    }
    Ok(json!({"kind": "cylinder", "center": center, "radius": radius, "height": height}))
}

/// The map-id check every placed object makes.
fn same_map(item: &JsonMap, map_id: &str, path: &str) -> Admit<()> {
    if item["map_id"].as_str() != Some(map_id) {
        return reject("Spatial object belongs to another map", CROSS_MAP, format!("{path}.map_id"));
    }
    Ok(())
}

/// The identity check every placed object makes: valid and unique across the scene.
fn unique_identity(item: &JsonMap, seen: &mut std::collections::HashSet<String>, path: &str) -> Admit<String> {
    let id = identity(&item["id"], SCENE_INVALID, &format!("{path}.id"))?;
    if !seen.insert(id.clone()) {
        return reject("Spatial identities must be unique", DUPLICATE_ID, format!("{path}.id"));
    }
    Ok(id)
}

fn sort_by_id(items: &mut [Json]) {
    items.sort_by(|a, b| a["id"].as_str().cmp(&b["id"].as_str()));
}

/// `_validate_scene(payload)`.
fn validate_scene(payload: &JsonMap) -> Result<JsonMap, SpatialError> {
    let code = SCENE_INVALID;
    let root = Json::Object(payload.clone());
    let scene = object(&root, SCENE_KEYS, code, "$")?;
    if !equals_one(&scene["format"]) {
        reject::<()>("Spatial scene format is unsupported", code, "$.format")?;
    }
    let map_value = object(&scene["map"], MAP_KEYS, code, "$.map")?;
    let map_id = identity(&map_value["id"], code, "$.map.id")?;
    let revision = integer(&map_value["revision"], 0, i64::MAX, code, "$.map.revision")?;
    let bounds_value = object(&map_value["bounds"], BOUNDS_KEYS, code, "$.map.bounds")?;
    let lower = coordinate(&bounds_value["min"], 3, code, "$.map.bounds.min")?;
    let upper = coordinate(&bounds_value["max"], 3, code, "$.map.bounds.max")?;
    let (low, high) = (floats(&lower), floats(&upper));
    if (0..3).any(|index| low[index] >= high[index]) {
        reject::<()>("Map bounds must have positive extent", code, "$.map.bounds")?;
    }
    let bounds = Bounds { lower: [low[0], low[1], low[2]], upper: [high[0], high[1], high[2]] };

    let grid_value = object(&map_value["grid"], GRID_KEYS, code, "$.map.grid")?;
    let cell_size = number(&grid_value["cell_size"], Some(POSITIVE), code, "$.map.grid.cell_size")?;
    let unit = identity(&grid_value["unit"], code, "$.map.grid.unit")?;
    let origin = coordinate(&grid_value["origin"], 2, code, "$.map.grid.origin")?;
    if floats(&origin) != [low[0], low[1]] {
        reject::<()>("Grid origin must match the lower map bounds", code, "$.map.grid.origin")?;
    }
    // `math.ceil((upper - lower) / cell_size)` per axis; the product only when both fit.
    let size = float(&cell_size);
    let axis_cells: Vec<f64> = (0..2).map(|index| ((high[index] - low[index]) / size).ceil()).collect();
    if axis_cells.iter().any(|count| *count > MAX_AXIS_CELLS as f64)
        || axis_cells[0] as i64 * axis_cells[1] as i64 > MAX_TOTAL_CELLS
    {
        reject::<()>("Map cell count exceeds spatial limits", LIMIT_EXCEEDED, "$.map")?;
    }
    let axis_cells = [axis_cells[0] as i64, axis_cells[1] as i64];

    let layers_input = array(&map_value["layers"], 1, 128, code, "$.map.layers")?;
    let mut layers = Vec::new();
    let mut layer_ids = std::collections::HashSet::new();
    for (index, raw) in layers_input.iter().enumerate() {
        let path = format!("$.map.layers[{index}]");
        let item = object(raw, LAYER_KEYS, code, &path)?;
        let layer_id = identity(&item["id"], code, &format!("{path}.id"))?;
        if !layer_ids.insert(layer_id.clone()) {
            reject::<()>("Layer identities must be unique", DUPLICATE_ID, format!("{path}.id"))?;
        }
        let minimum = number(&item["min_elevation"], None, code, &format!("{path}.min_elevation"))?;
        let maximum = number(&item["max_elevation"], None, code, &format!("{path}.max_elevation"))?;
        let (bottom, top) = (float(&minimum), float(&maximum));
        if bottom >= top || !bounds.inside(&[low[0], low[1], bottom]) || !bounds.inside(&[high[0], high[1], top]) {
            reject::<()>("Layer elevation is outside map bounds", OUT_OF_BOUNDS, path)?;
        }
        layers.push(json!({"id": layer_id, "min_elevation": minimum, "max_elevation": maximum}));
    }
    sort_by_id(&mut layers);

    let mut seen_ids = std::collections::HashSet::new();
    let geometry_input = array(&scene["geometry"], 0, MAX_GEOMETRY, code, "$.geometry")?;
    let mut geometry = Vec::new();
    for (index, raw) in geometry_input.iter().enumerate() {
        let path = format!("$.geometry[{index}]");
        // `{"occupies_cells": True, **raw}`.
        let raw = match raw {
            Json::Object(map) if !map.contains_key("occupies_cells") => {
                let mut defaulted = JsonMap::new();
                defaulted.insert("occupies_cells".into(), Json::Bool(true));
                defaulted.extend(map.clone());
                Json::Object(defaulted)
            }
            other => other.clone(),
        };
        let item = object(&raw, GEOMETRY_KEYS, code, &path)?;
        let item_id = unique_identity(item, &mut seen_ids, &path)?;
        same_map(item, &map_id, &path)?;
        let destroyed = object(&item["destroyed"], DESTROYED_KEYS, code, &format!("{path}.destroyed"))?;
        let volume = volume_box(&item["volume"], &bounds, code, &format!("{path}.volume"))?;
        let active = boolean(&item["active"], code, &format!("{path}.active"))?;
        let blocks = movement(&item["movement"], code, &format!("{path}.movement"))?;
        let occupies = boolean(&item["occupies_cells"], code, &format!("{path}.occupies_cells"))?;
        let sight = visibility(&item["visibility"], code, &format!("{path}.visibility"))?;
        let meta = metadata(&item["metadata"], code, &format!("{path}.metadata"))?;
        let destroyed_active = boolean(&destroyed["active"], code, &format!("{path}.destroyed.active"))?;
        let destroyed_movement = movement(&destroyed["movement"], code, &format!("{path}.destroyed.movement"))?;
        let destroyed_visibility = visibility(&destroyed["visibility"], code, &format!("{path}.destroyed.visibility"))?;
        geometry.push(json!({
            "id": item_id, "map_id": map_id, "volume": volume, "active": active, "movement": blocks,
            "occupies_cells": occupies, "visibility": sight, "metadata": meta,
            "destroyed": {"active": destroyed_active, "movement": destroyed_movement, "visibility": destroyed_visibility},
        }));
    }
    sort_by_id(&mut geometry);

    let entities_input = array(&scene["entities"], 0, MAX_ENTITIES, code, "$.entities")?;
    let mut entities = Vec::new();
    for (index, raw) in entities_input.iter().enumerate() {
        let path = format!("$.entities[{index}]");
        let item = object(raw, ENTITY_KEYS, code, &path)?;
        let item_id = unique_identity(item, &mut seen_ids, &path)?;
        same_map(item, &map_id, &path)?;
        let position = coordinate(&item["position"], 3, code, &format!("{path}.position"))?;
        let footprint_raw = &item["footprint"];
        let Json::Object(footprint_map) = footprint_raw else {
            // `footprint_raw.get("kind")` on a non-mapping.
            return Err(SpatialError::Uncaught {
                error_type: "AttributeError",
                message: format!("'{}' object has no attribute 'get'", type_name(footprint_raw)),
            });
        };
        let (normalized_footprint, extents) = if footprint_map.get("kind") == Some(&json!("box")) {
            let footprint = object(footprint_raw, &["kind", "half_extents"], code, &format!("{path}.footprint"))?;
            let half_extents = coordinate(&footprint["half_extents"], 2, code, &format!("{path}.footprint.half_extents"))?;
            let half = floats(&half_extents);
            if half.iter().any(|value| *value <= 0.0) {
                reject::<()>("Footprint extent must be positive", code, format!("{path}.footprint"))?;
            }
            (json!({"kind": "box", "half_extents": half_extents}), [half[0], half[1]])
        } else {
            let footprint = object(footprint_raw, &["kind", "radius"], code, &format!("{path}.footprint"))?;
            if footprint["kind"] != json!("circle") {
                reject::<()>("Footprint kind is invalid", code, format!("{path}.footprint.kind"))?;
            }
            let radius = number(&footprint["radius"], Some(POSITIVE), code, &format!("{path}.footprint.radius"))?;
            let r = float(&radius);
            (json!({"kind": "circle", "radius": radius}), [r, r])
        };
        let height = number(&item["height"], Some(POSITIVE), code, &format!("{path}.height"))?;
        let p = floats(&position);
        let entity_min = [p[0] - extents[0], p[1] - extents[1], p[2]];
        let entity_max = [p[0] + extents[0], p[1] + extents[1], p[2] + float(&height)];
        if !bounds.inside(&entity_min) || !bounds.inside(&entity_max) {
            reject::<()>("Entity volume is outside map bounds", OUT_OF_BOUNDS, path.as_str())?;
        }
        let facing = coordinate(&item["facing"], 2, code, &format!("{path}.facing"))?;
        if float(&facing[0]).hypot(float(&facing[1])) == 0.0 {
            reject::<()>("Entity facing must be nonzero", code, format!("{path}.facing"))?;
        }
        let active = boolean(&item["active"], code, &format!("{path}.active"))?;
        let blocks = movement(&item["movement"], code, &format!("{path}.movement"))?;
        let sight = visibility(&item["visibility"], code, &format!("{path}.visibility"))?;
        let meta = metadata(&item["metadata"], code, &format!("{path}.metadata"))?;
        entities.push(json!({
            "id": item_id, "map_id": map_id, "position": position, "footprint": normalized_footprint,
            "height": height, "facing": facing, "active": active, "movement": blocks,
            "visibility": sight, "metadata": meta,
        }));
    }
    sort_by_id(&mut entities);

    let zones_input = array(&scene["zones"], 0, MAX_ZONES, code, "$.zones")?;
    let mut zones = Vec::new();
    for (index, raw) in zones_input.iter().enumerate() {
        let path = format!("$.zones[{index}]");
        let item = object(raw, ZONE_KEYS, code, &path)?;
        let item_id = unique_identity(item, &mut seen_ids, &path)?;
        same_map(item, &map_id, &path)?;
        let zone_shape = shape(&item["shape"], &bounds, code, &format!("{path}.shape"))?;
        let meta = metadata(&item["metadata"], code, &format!("{path}.metadata"))?;
        zones.push(json!({"id": item_id, "map_id": map_id, "shape": zone_shape, "metadata": meta}));
    }
    sort_by_id(&mut zones);

    let terrain_input = array(&scene["terrain"], 0, MAX_TERRAIN, code, "$.terrain")?;
    let mut terrain = Vec::new();
    let mut terrain_cells = std::collections::HashSet::new();
    for (index, raw) in terrain_input.iter().enumerate() {
        let path = format!("$.terrain[{index}]");
        let item = object(raw, TERRAIN_KEYS, code, &path)?;
        let [x, y] = cell(&item["cell"], code, &format!("{path}.cell"))?;
        if x >= axis_cells[0] || y >= axis_cells[1] {
            reject::<()>("Terrain cell is outside map bounds", OUT_OF_BOUNDS, format!("{path}.cell"))?;
        }
        let layer_id = identity(&item["layer_id"], code, &format!("{path}.layer_id"))?;
        if !layer_ids.contains(&layer_id) {
            reject::<()>("Terrain layer is unknown", code, format!("{path}.layer_id"))?;
        }
        if !terrain_cells.insert((x, y, layer_id.clone())) {
            reject::<()>("Terrain cells must be unique per layer", DUPLICATE_ID, path.as_str())?;
        }
        let cost = number(&item["cost"], Some(POSITIVE), code, &format!("{path}.cost"))?;
        terrain.push((y, x, layer_id.clone(), json!({"cell": [x, y], "layer_id": layer_id, "cost": cost})));
    }
    terrain.sort_by(|a, b| (a.0, a.1, &a.2).cmp(&(b.0, b.1, &b.2)));
    let terrain: Vec<Json> = terrain.into_iter().map(|(_, _, _, item)| item).collect();

    let normalized = json!({
        "format": 1,
        "map": {
            "id": map_id,
            "revision": revision,
            "bounds": {"min": lower, "max": upper},
            "grid": {"cell_size": cell_size, "unit": unit, "origin": origin},
            "layers": layers,
        },
        "geometry": geometry,
        "entities": entities,
        "zones": zones,
        "terrain": terrain,
    });
    match normalized {
        Json::Object(map) => Ok(map),
        _ => unreachable!("an object literal"),
    }
}
