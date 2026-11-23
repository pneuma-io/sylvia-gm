//! `application/spatial_preview.py`: bounded map drawings from queries made against the
//! player's known physical scene.
//!
//! Python raises `ValueError` (or lets a `KeyError` through) where a preview cannot be drawn;
//! its only caller, the player-action previewer, turns every one into no preview, so each is
//! `Err(Unavailable)` here.

use sylvia_base::{Json, JsonMap};
use sylvia_engine::spatial::SpatialEngine;

/// `PREVIEW_NOTICE`.
pub const PREVIEW_NOTICE: &str = "Based on the visible map. The action rechecks current game state.";

/// A preview that cannot be drawn.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Unavailable;

type Preview<T> = Result<T, Unavailable>;

fn number(value: &Json) -> Option<f64> {
    match value {
        Json::Number(n) => n.as_f64().filter(|f| f.is_finite()),
        _ => None,
    }
}

fn get<'j>(map: &'j JsonMap, key: &str) -> Preview<&'j Json> {
    map.get(key).ok_or(Unavailable)
}

fn object(value: &Json) -> Preview<&JsonMap> {
    value.as_object().ok_or(Unavailable)
}

fn array(value: &Json) -> Preview<&Vec<Json>> {
    value.as_array().ok_or(Unavailable)
}

/// `project_preflight_path(route, cost, scene)`: only a qualified public route's drawing fields.
/// `route` is `None` when the selected route is not a mapping; `cost` is `None` when it is not an
/// `int` or `float`.
pub fn project_preflight_path(
    route: Option<&JsonMap>,
    cost: Option<&Json>,
    scene: &JsonMap,
) -> Preview<JsonMap> {
    let map = object(get(scene, "map")?)?;
    let bounds = object(get(map, "bounds")?)?;
    let (lower, upper) = (array(get(bounds, "min")?)?, array(get(bounds, "max")?)?);
    let route = route.ok_or(Unavailable)?;
    let Some(Json::Array(points)) = route.get("points") else { return Err(Unavailable) };
    let valid_cost = cost.and_then(number).is_some_and(|cost| cost >= 0.0);
    if route.get("found") != Some(&Json::Bool(true))
        || route.get("truncated") != Some(&Json::Bool(false))
        || !(2..=1024).contains(&points.len())
        || !valid_cost
    {
        return Err(Unavailable);
    }
    for point in points {
        let Json::Array(values) = point else { return Err(Unavailable) };
        if values.len() != 3 {
            return Err(Unavailable);
        }
        for (axis, value) in values.iter().enumerate() {
            let Some(value) = number(value) else { return Err(Unavailable) };
            let (low, high) =
                (number(&lower[axis]).ok_or(Unavailable)?, number(&upper[axis]).ok_or(Unavailable)?);
            if !(low <= value && value <= high) {
                return Err(Unavailable);
            }
        }
    }
    let mut path = JsonMap::new();
    path.insert("found".into(), Json::Bool(true));
    path.insert("truncated".into(), Json::Bool(false));
    path.insert("points".into(), Json::Array(points.clone()));
    path.insert("cost".into(), cost.expect("checked").clone());
    let mut preview = JsonMap::new();
    preview.insert("notice".into(), Json::from(PREVIEW_NOTICE));
    preview.insert("map_id".into(), get(map, "id")?.clone());
    preview.insert("map_revision".into(), get(map, "revision")?.clone());
    preview.insert("path".into(), Json::Object(path));
    Ok(preview)
}

fn crossings(result: &JsonMap) -> Preview<Json> {
    let mut items = Vec::new();
    for item in array(get(result, "crossings")?)? {
        let item = object(item)?;
        let mut crossing = JsonMap::new();
        crossing.insert("id".into(), get(item, "zone_id")?.clone());
        crossing.insert("point".into(), get(item, "point")?.clone());
        items.push(Json::Object(crossing));
    }
    Ok(Json::Array(items))
}

/// `project_spatial_preview(declaration, results=..., queries=..., scenes=...)`: only the
/// selected drawing fields; private provenance and arbitrary metadata stay out.
pub fn project_spatial_preview(
    declaration: &JsonMap,
    results: &JsonMap,
    queries: &JsonMap,
    scenes: &JsonMap,
) -> Preview<JsonMap> {
    let mut output = JsonMap::new();
    output.insert("notice".into(), Json::from(PREVIEW_NOTICE));
    for (operation, declared_name) in declaration {
        let name = declared_name.as_str().ok_or(Unavailable)?;
        let result = object(get(results, name)?)?;
        let query = object(get(queries, name)?)?;
        let scene = object(get(scenes, name)?)?;
        let identity = [
            ("map_id", get(result, "map_id")?.clone()),
            ("map_revision", get(result, "geometry_revision")?.clone()),
        ];
        if output.contains_key("map_id")
            && identity.iter().any(|(key, value)| output.get(*key) != Some(value))
        {
            // "A map preview cannot combine different maps or revisions".
            return Err(Unavailable);
        }
        for (key, value) in identity {
            output.insert(key.into(), value);
        }
        match operation.as_str() {
            "path" => {
                let map = object(get(scene, "map")?)?;
                let grid = object(get(map, "grid")?)?;
                let origin = array(get(grid, "origin")?)?;
                let cell_size = number(get(grid, "cell_size")?).ok_or(Unavailable)?;
                let floor = array(get(object(get(map, "bounds")?)?, "min")?)?[2].clone();
                let (origin_x, origin_y) =
                    (number(&origin[0]).ok_or(Unavailable)?, number(&origin[1]).ok_or(Unavailable)?);
                let mut points = Vec::new();
                for cell in array(get(result, "cells")?)? {
                    let cell = array(cell)?;
                    let (x, y) = (number(&cell[0]).ok_or(Unavailable)?, number(&cell[1]).ok_or(Unavailable)?);
                    points.push(vec![
                        Json::from(
                            serde_json::Number::from_f64(origin_x + (x + 0.5) * cell_size)
                                .ok_or(Unavailable)?,
                        ),
                        Json::from(
                            serde_json::Number::from_f64(origin_y + (y + 0.5) * cell_size)
                                .ok_or(Unavailable)?,
                        ),
                        floor.clone(),
                    ]);
                }
                let moving = query.get("moving_entity_id").cloned().unwrap_or(Json::Null);
                let mover = array(get(scene, "entities")?)?.iter().find(|item| item["id"] == moving);
                if let (Some(mover), false) = (mover, points.is_empty()) {
                    let position = array(&mover["position"])?;
                    points[0] = position.clone();
                    for point in points.iter_mut().skip(1) {
                        point[2] = position[2].clone();
                    }
                }
                let mut path = JsonMap::new();
                for key in ["found", "truncated", "cost", "cells"] {
                    path.insert(key.into(), get(result, key)?.clone());
                }
                let drawn = Json::Array(points.iter().cloned().map(Json::Array).collect());
                path.insert("points".into(), drawn.clone());
                output.insert("path".into(), Json::Object(path));
                if points.len() >= 2 && !declaration.contains_key("crossed_zones") {
                    let mut request = JsonMap::new();
                    request.insert("kind".into(), Json::from("crossed_zones"));
                    request.insert("map_id".into(), get(result, "map_id")?.clone());
                    request.insert("path".into(), drawn);
                    request.insert("max_crossings".into(), Json::from(256));
                    let mut engine =
                        SpatialEngine::new(&Json::Object(scene.clone())).map_err(|_| Unavailable)?;
                    let answer =
                        engine.query(&Json::Object(request), None).map_err(|_| Unavailable)?.as_payload();
                    output.insert("crossed_zones".into(), crossings(&answer)?);
                }
            }
            "ray" => {
                let mut contacts = Vec::new();
                for item in array(get(result, "intersections")?)? {
                    let item = object(item)?;
                    let mut contact = JsonMap::new();
                    for key in ["kind", "id", "point"] {
                        contact.insert(key.into(), get(item, key)?.clone());
                    }
                    contacts.push(Json::Object(contact));
                }
                let mut ray = JsonMap::new();
                ray.insert("origin".into(), get(query, "origin")?.clone());
                ray.insert("destination".into(), get(query, "destination")?.clone());
                ray.insert("contacts".into(), Json::Array(contacts));
                output.insert("ray".into(), Json::Object(ray));
            }
            "exposure" => {
                let mut cover = Vec::new();
                for item in array(get(result, "occluders")?)? {
                    let item = object(item)?;
                    if get(item, "kind")? == "geometry" {
                        cover.push(get(item, "id")?.clone());
                    }
                }
                let mut exposure = JsonMap::new();
                exposure.insert("fraction".into(), get(result, "exposure")?.clone());
                exposure.insert("cover_ids".into(), Json::Array(cover));
                output.insert("exposure".into(), Json::Object(exposure));
            }
            "crossed_zones" => {
                output.insert("crossed_zones".into(), crossings(result)?);
            }
            _ => {}
        }
    }
    Ok(output)
}
