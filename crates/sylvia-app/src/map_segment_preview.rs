//! `application/map_segment_preview.py`: a declared physical segment preview. No action costs or
//! mechanics are inferred; the owner lets every exception through, so they all keep their class.

use crate::spatial_preview::project_spatial_preview;
use crate::tactical_grid::{project_coordinate_map, project_tactical_grid, StatePaths, TacticalGridError};
use sylvia_base::{Json, JsonMap};
use sylvia_engine::spatial::{SpatialEngine, SpatialError};

/// `surface[key]`, a `KeyError` when it is missing.
fn item<'v>(map: &'v JsonMap, key: &str) -> Result<&'v Json, TacticalGridError> {
    map.get(key).ok_or_else(|| {
        TacticalGridError::Uncaught(SpatialError::Uncaught {
            error_type: "KeyError",
            message: format!("'{key}'"),
        })
    })
}

/// The numbers of an admitted point.
fn point(value: &Json) -> Vec<f64> {
    value.as_array().expect("an admitted point").iter().map(|item| item.as_f64().expect("a number")).collect()
}

/// `segment_preview(surface, data, selection)`: raw intent measured against the safe known
/// scene through the shared spatial engine, or `None` when the action declares no segment
/// preview or the selection has no endpoints.
pub fn segment_preview(
    surface: &JsonMap,
    data: &Json,
    selection: &Json,
) -> Result<Option<JsonMap>, TacticalGridError> {
    let Json::Object(selection) = selection else { return Ok(None) };
    let Json::Object(action) = item(surface, "action")? else { unreachable!("an admitted surface action") };
    if action.get("spatial_preview").and_then(Json::as_str) != Some("segment") {
        return Ok(None);
    }
    // Without an actor reference every body's occupied space is unavailable, whatever the widths'
    // source, and the presentation never shows it, so the coordinate map needs no state paths.
    let projected = if item(surface, "renderer")? == "coordinate_map" {
        project_coordinate_map(data, None, &StatePaths::none())?
    } else {
        project_tactical_grid(data)?
    };
    let entities = projected["entities"].as_array().expect("projected entities");
    let find = |key: &str| {
        let wanted = selection.get(key);
        entities.iter().find(|row| Some(&row["id"]) == wanted)
    };
    let (actor, target) = (find("actor"), find("target"));
    let aims = action
        .get("bindings")
        .and_then(Json::as_object)
        .is_some_and(|bindings| bindings.values().any(|source| source == "aim_point"));
    let endpoint = if aims { selection.get("aim_point") } else { target.map(|target| &target["position"]) };
    let (Some(actor), Some(endpoint)) = (actor, endpoint.filter(|endpoint| !endpoint.is_null())) else {
        return Ok(None);
    };
    let scene = &data["scene"];
    let query = serde_json::json!({
        "kind": "ray", "map_id": projected["map"]["id"], "origin": actor["position"],
        "destination": endpoint, "include": ["geometry"], "max_intersections": 256,
    });
    let result = SpatialEngine::new(scene)?.query(&query, None)?.as_payload();
    let one = |value: Json| {
        let mut map = JsonMap::new();
        map.insert("segment".into(), value);
        map
    };
    let mut declaration = JsonMap::new();
    declaration.insert("ray".into(), Json::from("segment"));
    // One fresh ray on one scene always draws: its map and revision agree with themselves.
    let drawing = project_spatial_preview(
        &declaration,
        &one(Json::Object(result.clone())),
        &one(query.clone()),
        &one(scene.clone()),
    )
    .map_err(|_| {
        TacticalGridError::Uncaught(SpatialError::Uncaught {
            error_type: "ValueError",
            message: "A map preview cannot combine different maps or revisions".into(),
        })
    })?;
    let distance = sylvia_engine::pymath::dist(&point(&actor["position"]), &point(endpoint));
    let distance = sylvia_base::pyjson::float_repr(sylvia_engine::pymath::round_digits(distance, 2));
    let unit = projected["map"]["grid"]["unit"].as_str().expect("an admitted unit");
    let contacts = result["intersections"].as_array().expect("admitted intersections").len();
    let mut presentation = JsonMap::new();
    presentation.insert("map_preview".into(), Json::Object(drawing));
    presentation.insert(
        "preview".into(),
        serde_json::json!({
            "version": 1, "footprint": "compact", "title": action["label"],
            "summary": [
                {"label": "Distance", "value": format!("{distance} {unit}")},
                {"label": "Geometry contacts", "value": contacts},
            ],
        }),
    );
    Ok(Some(presentation))
}
