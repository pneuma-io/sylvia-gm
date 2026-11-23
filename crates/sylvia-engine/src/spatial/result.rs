//! `spatial/_interface.py`: result admission (`admit_spatial_result` and the intersection and
//! reference shapes it checks).

use super::query::{grid_cell_counts, scene_bounds};
use super::scene::{
    array, boolean, capture_payload, cell, choice, coordinate, float, floats, identity, integer, metadata,
    number, object, reject, Admit, CROSS_MAP, MAX_ENTITIES, MAX_GEOMETRY, MAX_TOTAL_CELLS,
};
use super::{AdmittedSpatialScene, SpatialError, SPATIAL_ALGORITHM_VERSION};
use serde_json::json;
use std::collections::HashSet;
use sylvia_base::{Json, JsonMap};

const RESULT_INVALID: &str = "spatial_result_invalid";
const PROVENANCE: [&str; 5] = ["kind", "map_id", "snapshot_id", "geometry_revision", "algorithm_version"];

/// `_RESULT_KEYS[kind]` beyond the provenance keys.
fn result_keys(kind: &str) -> Option<&'static [&'static str]> {
    Some(match kind {
        "circle_route" => &["found", "points", "distance", "truncated", "tests"],
        "circle_overlap" => &["hit", "entry", "exit", "starts_inside", "ends_inside"],
        "body_proximity" => &["hit", "entry", "exit", "minimum_distance"],
        "sweep" => &["supported", "clear", "distance", "blockers", "reason"],
        "placement" => &["supported", "found", "position", "distance", "reason", "examined"],
        "ray" => &["cells", "intersections", "truncated"],
        "exposure" => &["samples", "visible_samples", "exposure", "occluders"],
        "path" => &["found", "cells", "cost", "expanded", "truncated"],
        "crossed_zones" => &["crossings", "truncated"],
        "area" => &["results", "truncated"],
        _ => return None,
    })
}

/// `AdmittedSpatialResult`: one closed canonical result bound to an immutable scene identity.
#[derive(Debug, Clone, PartialEq)]
pub struct AdmittedSpatialResult {
    payload: JsonMap,
    /// `kind`.
    pub kind: String,
    /// `snapshot_id`.
    pub snapshot_id: String,
}

impl AdmittedSpatialResult {
    /// `as_payload()`: the canonical result.
    pub fn as_payload(&self) -> JsonMap {
        self.payload.clone()
    }
}

/// `value != integer` under Python's numeric equality (`True == 1`, `3.0 == 3`).
fn differs_from(value: &Json, integer: i64) -> bool {
    match value {
        Json::Bool(flag) => i64::from(*flag) != integer,
        Json::Number(number) => {
            if let Some(int) = number.as_i64() {
                int != integer
            } else if number.as_u64().is_some() {
                true
            } else {
                let x = number.as_f64().unwrap();
                !(x.fract() == 0.0 && (-9.3e18..=9.3e18).contains(&x) && x as i128 == i128::from(integer))
            }
        }
        _ => true,
    }
}

struct SceneIds {
    geometry: HashSet<String>,
    entity: HashSet<String>,
    zone: HashSet<String>,
}

impl SceneIds {
    fn of(&self, kind: &str) -> &HashSet<String> {
        match kind {
            "geometry" => &self.geometry,
            "entity" => &self.entity,
            _ => &self.zone,
        }
    }
}

fn ids(scene: &JsonMap, collection: &str) -> HashSet<String> {
    scene[collection]
        .as_array()
        .expect("admitted")
        .iter()
        .map(|entry| entry["id"].as_str().unwrap().to_owned())
        .collect()
}

/// `_result_intersection(value, scene_ids=..., path=...)`.
fn intersection(value: &Json, scene_ids: &SceneIds, path: &str) -> Admit<Json> {
    let code = RESULT_INVALID;
    let item = object(value, &["kind", "id", "distance", "t", "point", "normal", "metadata"], code, path)?;
    let kind = choice(&item["kind"], &["geometry", "entity"], code, &format!("{path}.kind"))?;
    let id = identity(&item["id"], code, &format!("{path}.id"))?;
    if !scene_ids.of(&kind).contains(&id) {
        return reject("Intersection identity is unknown", code, format!("{path}.id"));
    }
    let distance = number(&item["distance"], Some(0.0), code, &format!("{path}.distance"))?;
    let parameter = number(&item["t"], Some(0.0), code, &format!("{path}.t"))?;
    if float(&parameter) > 1.0 {
        return reject("Intersection parameter exceeds the ray", code, format!("{path}.t"));
    }
    let point = coordinate(&item["point"], 3, code, &format!("{path}.point"))?;
    let normal = coordinate(&item["normal"], 3, code, &format!("{path}.normal"))?;
    let meta = metadata(&item["metadata"], code, &format!("{path}.metadata"))?;
    Ok(
        json!({"kind": kind, "id": id, "distance": distance, "t": parameter, "point": point, "normal": normal, "metadata": meta}),
    )
}

/// `_result_reference(value, scene_ids=..., allowed=..., path=...)`.
fn reference(value: &Json, scene_ids: &SceneIds, allowed: &[&str], path: &str) -> Admit<Json> {
    let code = RESULT_INVALID;
    let item = object(value, &["kind", "id", "metadata"], code, path)?;
    let kind = choice(&item["kind"], allowed, code, &format!("{path}.kind"))?;
    let id = identity(&item["id"], code, &format!("{path}.id"))?;
    if !scene_ids.of(&kind).contains(&id) {
        return reject("Spatial result identity is unknown", code, format!("{path}.id"));
    }
    let meta = metadata(&item["metadata"], code, &format!("{path}.metadata"))?;
    Ok(json!({"kind": kind, "id": id, "metadata": meta}))
}

fn text(value: &Json, key: &str) -> String {
    value[key].as_str().unwrap_or_default().to_owned()
}

fn sort_references(items: &mut [Json]) {
    items.sort_by_key(|item| (text(item, "kind"), text(item, "id")));
}

/// `admit_spatial_result(scene, value)`: a result's closed shape and snapshot provenance.
pub fn admit_spatial_result(
    scene: &AdmittedSpatialScene,
    value: &Json,
) -> Result<AdmittedSpatialResult, SpatialError> {
    Ok(admit(scene, value)?)
}

fn admit(scene: &AdmittedSpatialScene, value: &Json) -> Admit<AdmittedSpatialResult> {
    let code = RESULT_INVALID;
    let payload = capture_payload(value, code)?;
    let Some((kind, keys)) = payload
        .get("kind")
        .and_then(Json::as_str)
        .and_then(|kind| result_keys(kind).map(|keys| (kind, keys)))
    else {
        return reject("Spatial result kind is invalid", code, "$.kind");
    };
    let kind = kind.to_owned();
    let expected: Vec<&str> = PROVENANCE.iter().chain(keys).copied().collect();
    let root = Json::Object(payload.clone());
    let item = object(&root, &expected, code, "$")?;
    if item["map_id"].as_str() != Some(scene.map_id.as_str()) {
        return reject("Spatial result belongs to another map", CROSS_MAP, "$.map_id");
    }
    if item["snapshot_id"].as_str() != Some(scene.snapshot_id.as_str()) {
        return reject("Spatial result snapshot is stale", code, "$.snapshot_id");
    }
    if differs_from(&item["geometry_revision"], scene.geometry_revision) {
        return reject("Spatial result revision is stale", code, "$.geometry_revision");
    }
    if item["algorithm_version"].as_str() != Some(SPATIAL_ALGORITHM_VERSION) {
        return reject("Spatial result algorithm version is invalid", code, "$.algorithm_version");
    }
    let admitted = scene.payload();
    let bounds = scene_bounds(admitted);
    let axis_cells = grid_cell_counts(admitted);
    let scene_ids = SceneIds {
        geometry: ids(admitted, "geometry"),
        entity: ids(admitted, "entities"),
        zone: ids(admitted, "zones"),
    };
    let mut normalized = JsonMap::new();
    normalized.insert("kind".into(), json!(kind));
    normalized.insert("map_id".into(), json!(scene.map_id));
    normalized.insert("snapshot_id".into(), json!(scene.snapshot_id));
    normalized.insert("geometry_revision".into(), json!(scene.geometry_revision));
    normalized.insert("algorithm_version".into(), json!(SPATIAL_ALGORITHM_VERSION));
    let mut put = |key: &str, value: Json| {
        normalized.insert(key.to_owned(), value);
    };
    let cells_of = |value: &Json, maximum: usize, message: &'static str| -> Admit<Vec<[i64; 2]>> {
        let input = array(value, if message.starts_with("Ray") { 1 } else { 0 }, maximum, code, "$.cells")?;
        let mut cells = Vec::new();
        for (index, raw) in input.iter().enumerate() {
            cells.push(cell(raw, code, &format!("$.cells[{index}]"))?);
        }
        if cells.iter().any(|c| (0..2).any(|i| c[i] >= axis_cells[i])) {
            return reject(message, code, "$.cells");
        }
        Ok(cells)
    };
    match kind.as_str() {
        "ray" => {
            let cells = cells_of(
                &item["cells"],
                (axis_cells[0] + axis_cells[1] + 2) as usize,
                "Ray traversal cell is outside map bounds",
            )?;
            let input = array(&item["intersections"], 0, 1024, code, "$.intersections")?;
            let mut hits = Vec::new();
            for (index, raw) in input.iter().enumerate() {
                hits.push(intersection(raw, &scene_ids, &format!("$.intersections[{index}]"))?);
            }
            hits.sort_by(|a, b| {
                float(&a["t"])
                    .partial_cmp(&float(&b["t"]))
                    .expect("finite")
                    .then_with(|| (text(a, "kind"), text(a, "id")).cmp(&(text(b, "kind"), text(b, "id"))))
            });
            let truncated = boolean(&item["truncated"], code, "$.truncated")?;
            put("cells", json!(cells));
            put("intersections", Json::Array(hits));
            put("truncated", json!(truncated));
        }
        "exposure" => {
            let input = array(&item["samples"], 1, 81, code, "$.samples")?;
            let mut samples = Vec::new();
            for (index, raw) in input.iter().enumerate() {
                let path = format!("$.samples[{index}]");
                let sample = object(raw, &["point", "visible", "occluders"], code, &path)?;
                let point = coordinate(&sample["point"], 3, code, &format!("{path}.point"))?;
                if !bounds.inside(&floats(&point)) {
                    return reject("Exposure sample is outside map bounds", code, format!("{path}.point"));
                }
                let occluders_input =
                    array(&sample["occluders"], 0, 1024, code, &format!("{path}.occluders"))?;
                let visible = boolean(&sample["visible"], code, &format!("{path}.visible"))?;
                let mut occluders = Vec::new();
                for (occluder_index, raw) in occluders_input.iter().enumerate() {
                    occluders.push(intersection(
                        raw,
                        &scene_ids,
                        &format!("{path}.occluders[{occluder_index}]"),
                    )?);
                }
                samples.push(json!({"point": point, "visible": visible, "occluders": occluders}));
            }
            let visible_samples =
                integer(&item["visible_samples"], 0, samples.len() as i64, code, "$.visible_samples")?;
            let exposure = number(&item["exposure"], Some(0.0), code, "$.exposure")?;
            if float(&exposure) > 1.0 {
                return reject("Exposure fraction exceeds one", code, "$.exposure");
            }
            let input = array(&item["occluders"], 0, 1024, code, "$.occluders")?;
            let mut occluders = Vec::new();
            for (index, raw) in input.iter().enumerate() {
                occluders.push(reference(
                    raw,
                    &scene_ids,
                    &["geometry", "entity"],
                    &format!("$.occluders[{index}]"),
                )?);
            }
            sort_references(&mut occluders);
            put("samples", Json::Array(samples));
            put("visible_samples", json!(visible_samples));
            put("exposure", exposure);
            put("occluders", Json::Array(occluders));
        }
        "circle_route" => {
            let found = boolean(&item["found"], code, "$.found")?;
            let (minimum, maximum) = if found { (2, 258) } else { (0, 0) };
            let mut points = Vec::new();
            for point in array(&item["points"], minimum, maximum, code, "$.points")? {
                points.push(coordinate(point, 3, code, "$.points")?);
            }
            let mut distance = item["distance"].clone();
            if found {
                distance = number(&distance, Some(0.0), code, "$.distance")?;
            } else if !distance.is_null() {
                return reject("Missing route distance must be null", code, "$.distance");
            }
            let truncated = boolean(&item["truncated"], code, "$.truncated")?;
            if found && truncated {
                return reject("A retained route cannot be truncated", code, "$.truncated");
            }
            let tests = integer(&item["tests"], 0, 16384, code, "$.tests")?;
            put("found", json!(found));
            put("points", json!(points));
            put("distance", distance);
            put("truncated", json!(truncated));
            put("tests", json!(tests));
        }
        "body_proximity" | "circle_overlap" => {
            let hit = boolean(&item["hit"], code, "$.hit")?;
            let mut interval = Vec::new();
            for name in ["entry", "exit"] {
                let mut value = item[name].clone();
                if hit {
                    value = number(&value, Some(0.0), code, &format!("$.{name}"))?;
                    if float(&value) > 1.0 {
                        return reject("Proximity parameter exceeds one", code, format!("$.{name}"));
                    }
                } else if !value.is_null() {
                    return reject("Missed proximity interval must be null", code, format!("$.{name}"));
                }
                interval.push(value);
            }
            if hit && float(&interval[0]) > float(&interval[1]) {
                return reject("Proximity interval is reversed", code, "$");
            }
            put("hit", json!(hit));
            put("entry", interval[0].clone());
            put("exit", interval[1].clone());
            if kind == "body_proximity" {
                put(
                    "minimum_distance",
                    number(&item["minimum_distance"], Some(0.0), code, "$.minimum_distance")?,
                );
            } else {
                for name in ["starts_inside", "ends_inside"] {
                    let flag = boolean(&item[name], code, &format!("$.{name}"))?;
                    put(name, json!(flag));
                    if flag && !hit {
                        return reject("Occupied endpoint requires an overlap", code, format!("$.{name}"));
                    }
                }
            }
        }
        "sweep" => {
            let supported = boolean(&item["supported"], code, "$.supported")?;
            let clear = boolean(&item["clear"], code, "$.clear")?;
            let reason = choice(
                &item["reason"],
                &["clear", "blocked", "out_of_bounds", "unsupported_geometry"],
                code,
                "$.reason",
            )?;
            let mut blockers = Vec::new();
            for (index, raw) in array(&item["blockers"], 0, MAX_GEOMETRY + MAX_ENTITIES, code, "$.blockers")?
                .iter()
                .enumerate()
            {
                let path = format!("$.blockers[{index}]");
                let blocker = object(raw, &["kind", "id"], code, &path)?;
                let blocker_kind =
                    choice(&blocker["kind"], &["geometry", "entity"], code, &format!("{path}.kind"))?;
                let blocker_id = identity(&blocker["id"], code, &format!("{path}.id"))?;
                if !scene_ids.of(&blocker_kind).contains(&blocker_id) {
                    return reject("Sweep blocker is unknown", code, format!("{path}.id"));
                }
                blockers.push((blocker_kind, blocker_id));
            }
            if blockers.iter().collect::<HashSet<_>>().len() != blockers.len() {
                return reject("Sweep blockers must be unique", code, "$.blockers");
            }
            if clear != (reason == "clear")
                || supported != (reason != "unsupported_geometry")
                || blockers.is_empty() == (reason == "blocked")
            {
                return reject("Sweep result is inconsistent", code, "$");
            }
            blockers.sort();
            let distance = number(&item["distance"], Some(0.0), code, "$.distance")?;
            put("supported", json!(supported));
            put("clear", json!(clear));
            put("reason", json!(reason));
            put(
                "blockers",
                Json::Array(blockers.iter().map(|(kind, id)| json!({"kind": kind, "id": id})).collect()),
            );
            put("distance", distance);
        }
        "placement" => {
            let supported = boolean(&item["supported"], code, "$.supported")?;
            let found = boolean(&item["found"], code, "$.found")?;
            let reason = choice(
                &item["reason"],
                &["placed", "no_placement", "unsupported_geometry", "candidate_limit"],
                code,
                "$.reason",
            )?;
            let position = if item["position"].is_null() {
                None
            } else {
                Some(coordinate(&item["position"], 3, code, "$.position")?)
            };
            let distance = if item["distance"].is_null() {
                None
            } else {
                Some(number(&item["distance"], Some(0.0), code, "$.distance")?)
            };
            if found != (position.is_some() && distance.is_some())
                || (found && (!supported || reason != "placed"))
                || supported != (reason == "placed" || reason == "no_placement")
            {
                return reject("Placement result is inconsistent", code, "$");
            }
            if let Some(point) = &position {
                if !bounds.inside(&floats(point)) {
                    return reject("Placement position is outside map bounds", code, "$.position");
                }
            }
            let examined = integer(&item["examined"], 0, 100_000, code, "$.examined")?;
            put("supported", json!(supported));
            put("found", json!(found));
            put("position", position.map_or(Json::Null, |point| json!(point)));
            put("distance", distance.unwrap_or(Json::Null));
            put("reason", json!(reason));
            put("examined", json!(examined));
        }
        "path" => {
            let cells =
                cells_of(&item["cells"], MAX_TOTAL_CELLS as usize, "Path cell is outside map bounds")?;
            let found = boolean(&item["found"], code, "$.found")?;
            let cost = if item["cost"].is_null() {
                None
            } else {
                Some(number(&item["cost"], Some(0.0), code, "$.cost")?)
            };
            if found != !cells.is_empty() || found != cost.is_some() {
                return reject("Path found state is inconsistent", code, "$");
            }
            let expanded = integer(&item["expanded"], 0, MAX_TOTAL_CELLS, code, "$.expanded")?;
            let truncated = boolean(&item["truncated"], code, "$.truncated")?;
            put("found", json!(found));
            put("cells", json!(cells));
            put("cost", cost.unwrap_or(Json::Null));
            put("expanded", json!(expanded));
            put("truncated", json!(truncated));
        }
        "crossed_zones" => {
            let input = array(&item["crossings"], 0, 4096, code, "$.crossings")?;
            let mut crossings = Vec::new();
            for (index, raw) in input.iter().enumerate() {
                let path = format!("$.crossings[{index}]");
                let crossing = object(
                    raw,
                    &["zone_id", "segment_index", "t", "point", "distance", "metadata"],
                    code,
                    &path,
                )?;
                let zone_id = identity(&crossing["zone_id"], code, &format!("{path}.zone_id"))?;
                if !scene_ids.zone.contains(&zone_id) {
                    return reject("Zone crossing identity is unknown", code, format!("{path}.zone_id"));
                }
                let parameter = number(&crossing["t"], Some(0.0), code, &format!("{path}.t"))?;
                if float(&parameter) > 1.0 {
                    return reject("Zone crossing parameter exceeds segment", code, format!("{path}.t"));
                }
                let segment =
                    integer(&crossing["segment_index"], 0, 254, code, &format!("{path}.segment_index"))?;
                let point = coordinate(&crossing["point"], 3, code, &format!("{path}.point"))?;
                let distance = number(&crossing["distance"], Some(0.0), code, &format!("{path}.distance"))?;
                let meta = metadata(&crossing["metadata"], code, &format!("{path}.metadata"))?;
                crossings.push(json!({
                    "zone_id": zone_id, "segment_index": segment, "t": parameter, "point": point, "distance": distance, "metadata": meta,
                }));
            }
            crossings.sort_by(|a, b| {
                a["segment_index"]
                    .as_i64()
                    .cmp(&b["segment_index"].as_i64())
                    .then_with(|| float(&a["t"]).partial_cmp(&float(&b["t"])).expect("finite"))
                    .then_with(|| text(a, "zone_id").cmp(&text(b, "zone_id")))
            });
            let truncated = boolean(&item["truncated"], code, "$.truncated")?;
            put("crossings", Json::Array(crossings));
            put("truncated", json!(truncated));
        }
        _ => {
            let input = array(&item["results"], 0, 4096, code, "$.results")?;
            let mut results = Vec::new();
            for (index, raw) in input.iter().enumerate() {
                results.push(reference(
                    raw,
                    &scene_ids,
                    &["geometry", "entity", "zone"],
                    &format!("$.results[{index}]"),
                )?);
            }
            sort_references(&mut results);
            let truncated = boolean(&item["truncated"], code, "$.truncated")?;
            put("results", Json::Array(results));
            put("truncated", json!(truncated));
        }
    }
    Ok(AdmittedSpatialResult { payload: normalized, kind, snapshot_id: scene.snapshot_id.clone() })
}
