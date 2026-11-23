//! `application/tactical_grid.py`: resolving a player-surface source, and the player-known scene.
//!
//! The projections and tactical action parameters follow with the spatial queries.

use std::collections::HashSet;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{owned_payload, Participant};
use sylvia_domain::error::DomainResult;
use sylvia_engine::spatial::{admit_spatial_query, admit_spatial_scene, AdmittedSpatialScene, SpatialError};

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

// ── The player-known scene ─────────────────────────────────────────────────────────────────

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
fn caught(error: &SpatialError) -> bool {
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
fn public_display(
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
fn visible_anchor_ids(scene: &JsonMap) -> Result<HashSet<String>, SpatialError> {
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
