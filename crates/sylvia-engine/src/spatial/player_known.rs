//! `application/tactical_grid.py`'s player-known scene: `player_known_spatial_scene`,
//! `player_known_spatial_value`, `_public_display` and `_visible_anchor_ids`. It sits with the
//! spatial engine because the bridge's shot presentation (`shot_effects.py`) needs it too;
//! `sylvia-app::tactical_grid` re-exports it.

use super::{admit_spatial_query, admit_spatial_scene, AdmittedSpatialScene, SpatialError};
use std::collections::HashSet;
use sylvia_base::{Json, JsonMap};

/// `_DISPLAY_FIELDS`: the closed public presentation record.
const DISPLAY_FIELDS: [&str; 11] = [
    "label",
    "kind",
    "state",
    "stance",
    "side",
    "color",
    "icon",
    "interactive",
    "height_label",
    "material_label",
    "display_geometry",
];
/// `_MAX_LABEL_BYTES`.
const MAX_LABEL_BYTES: usize = 256;

/// `except (TypeError, ValueError)` around a spatial admission: whether Python catches it.
pub fn caught(error: &SpatialError) -> bool {
    match error {
        SpatialError::Validation(_) => true,
        SpatialError::Uncaught { error_type, .. } => matches!(*error_type, "TypeError" | "ValueError"),
    }
}

/// Not blank, equal to its own strip, within `limit` UTF-8 bytes.
fn trimmed_text(text: &str, limit: usize) -> bool {
    let stripped = sylvia_base::pystr::strip(text);
    !stripped.is_empty() && stripped == text && text.len() <= limit
}

/// `_public_display(metadata, scene=..., visible_anchors=...)`: the closed public presentation
/// record, or `None` (hidden). A zone's display geometry is admitted against `scene`.
pub fn public_display(
    metadata: &Json,
    scene: Option<&AdmittedSpatialScene>,
    visible_anchors: Option<&HashSet<String>>,
) -> Result<Option<JsonMap>, SpatialError> {
    let Json::Object(metadata) = metadata else { return Ok(None) };
    match metadata.get("display_anchor") {
        None | Some(Json::Null) => {}
        Some(Json::String(anchor)) if visible_anchors.is_some_and(|anchors| anchors.contains(anchor)) => {}
        Some(_) => return Ok(None),
    }
    let Some(Json::Object(raw)) = metadata.get("player") else { return Ok(None) };
    if raw.keys().any(|key| !DISPLAY_FIELDS.contains(&key.as_str())) {
        return Ok(None);
    }
    let (Some(Json::String(label)), Some(Json::String(kind))) = (raw.get("label"), raw.get("kind")) else {
        return Ok(None);
    };
    if !trimmed_text(label, MAX_LABEL_BYTES) || !trimmed_text(kind, 64) {
        return Ok(None);
    }
    let mut result = JsonMap::new();
    result.insert("label".into(), Json::String(label.clone()));
    result.insert("kind".into(), Json::String(kind.clone()));
    for field in ["state", "stance", "side", "color", "icon", "height_label", "material_label"] {
        match raw.get(field) {
            None | Some(Json::Null) => {}
            Some(Json::String(item)) if trimmed_text(item, MAX_LABEL_BYTES) => {
                result.insert(field.into(), Json::String(item.clone()));
            }
            Some(_) => return Ok(None),
        }
    }
    match raw.get("interactive") {
        None | Some(Json::Null) => {}
        Some(Json::Bool(flag)) => {
            result.insert("interactive".into(), Json::Bool(*flag));
        }
        Some(_) => return Ok(None),
    }
    if let Some(geometry) = raw.get("display_geometry") {
        let Some(scene) = scene else { return Ok(None) };
        let Json::Object(shape) = geometry else { return Ok(None) };
        if !matches!(shape.get("kind").and_then(Json::as_str), Some("cone" | "prism")) {
            return Ok(None);
        }
        // Admission validates a closed public shape without executing a query or reading
        // recipients.
        let query = serde_json::json!({
            "kind": "area", "map_id": scene.map_id, "shape": geometry, "include": ["zones"], "max_results": 1,
        });
        match admit_spatial_query(scene, &query) {
            Ok(admitted) => {
                let shape = admitted.as_payload().get("shape").cloned().unwrap_or(Json::Null);
                result.insert("display_geometry".into(), shape);
            }
            Err(error) if caught(&error) => return Ok(None),
            Err(error) => return Err(error),
        }
    }
    Ok(Some(result))
}

/// `_visible_anchor_ids(scene)`: active entities with a public display.
pub fn visible_anchor_ids(scene: &JsonMap) -> Result<HashSet<String>, SpatialError> {
    let mut anchors = HashSet::new();
    for item in scene["entities"].as_array().expect("admitted entities") {
        if item["active"] == Json::Bool(true) && public_display(&item["metadata"], None, None)?.is_some() {
            anchors.insert(item["id"].as_str().expect("an admitted identity").to_owned());
        }
    }
    Ok(anchors)
}

/// `player_known_spatial_scene(value, retain_metadata=...)`: the admitted scene with only its
/// publicly displayed geometry, entities and zones. Kept rows carry their mechanical metadata
/// (`retain_metadata`, for internal Ruleset previews) or `{"player": display}`.
pub fn player_known_spatial_scene(value: &Json, retain_metadata: bool) -> Result<JsonMap, SpatialError> {
    let admitted = admit_spatial_scene(value)?;
    let mut scene = admitted.as_payload();
    let anchors = visible_anchor_ids(&scene)?;
    for category in ["geometry", "entities", "zones"] {
        let zones = category == "zones";
        let mut kept = Vec::new();
        for item in scene[category].as_array().expect("admitted rows") {
            let Json::Object(row) = item else { unreachable!("admitted rows are objects") };
            let display =
                public_display(&row["metadata"], zones.then_some(&admitted), zones.then_some(&anchors))?;
            if let Some(display) = display {
                let mut row = row.clone();
                if !retain_metadata {
                    let mut player = JsonMap::new();
                    player.insert("player".into(), Json::Object(display));
                    row.insert("metadata".into(), Json::Object(player));
                }
                kept.push(Json::Object(row));
            }
        }
        scene.insert(category.into(), Json::Array(kept));
    }
    Ok(scene)
}

/// The sections every spatial scene has.
const SCENE_SECTIONS: [&str; 5] = ["map", "entities", "geometry", "zones", "terrain"];

/// `player_known_spatial_value(value)`: the same scene membership boundary applied to every
/// nested scene, before Ruleset preview evaluation.
pub fn player_known_spatial_value(value: &Json) -> Result<Json, SpatialError> {
    Ok(match value {
        Json::Object(map) => {
            // `value.get("format") == 1`: Python's `==` counts `1.0` and `True` as `1`.
            let format_is_one = match map.get("format") {
                Some(Json::Number(number)) => number.as_f64() == Some(1.0),
                Some(Json::Bool(flag)) => *flag,
                _ => false,
            };
            if format_is_one && SCENE_SECTIONS.iter().all(|key| map.contains_key(*key)) {
                Json::Object(player_known_spatial_scene(value, true)?)
            } else {
                let mut projected = JsonMap::new();
                for (key, child) in map {
                    projected.insert(key.clone(), player_known_spatial_value(child)?);
                }
                Json::Object(projected)
            }
        }
        Json::Array(items) => {
            Json::Array(items.iter().map(player_known_spatial_value).collect::<Result<_, _>>()?)
        }
        other => other.clone(),
    })
}
