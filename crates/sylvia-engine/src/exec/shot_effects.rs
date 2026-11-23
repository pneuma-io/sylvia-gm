//! `application/shot_effects.py`: one resolved shot projected into a bounded, player-visible
//! presentation cue, or suppressed.
//!
//! The bridge's `record_shot_effect` calls it and marks the presentation unavailable when it
//! answers `None` or raises one of the exceptions it catches (`TypeError`, `ValueError`,
//! `KeyError`), so every failure here is `None`. It sits beside the bridge, below the
//! application layer, for that reason.

use crate::pymath::hypot;
use crate::spatial::admit_spatial_scene;
use crate::spatial::player_known::player_known_spatial_scene;
use std::collections::HashSet;
use sylvia_base::{Json, JsonMap};

const REQUEST_FIELDS: [&str; 9] = [
    "source_id",
    "target_id",
    "target_kind",
    "origin",
    "destination",
    "delay_ms",
    "miss",
    "contacts",
    "style",
];
const CONTACT_FIELDS: [&str; 4] = ["kind", "id", "point", "cue"];
const CUE_KINDS: [&str; 4] = ["character", "cover", "penetration", "stop"];
const STYLE_FIELDS: [&str; 6] = ["color", "width", "travel_ms", "impact_ms", "fire_sound", "cues"];
const CUE_FIELDS: [&str; 2] = ["color", "sound"];
const SOUND_FIELDS: [&str; 5] = ["waveform", "frequency", "end_frequency", "duration_ms", "volume"];

fn float(value: &Json) -> f64 {
    value.as_f64().expect("a number")
}

fn number_of(value: f64) -> Json {
    Json::from(serde_json::Number::from_f64(value).expect("a finite number"))
}

/// `_number(value, minimum, maximum)`: a finite number (not a bool) in range, as given.
fn number(value: Option<&Json>, minimum: f64, maximum: f64) -> Option<Json> {
    match value {
        Some(Json::Number(n)) => {
            let f = n.as_f64().expect("a JSON number");
            (f.is_finite() && f >= minimum && f <= maximum).then(|| Json::Number(n.clone()))
        }
        _ => None,
    }
}

/// `_point(value)`: three finite numbers.
fn point(value: Option<&Json>) -> Option<Vec<Json>> {
    let Some(Json::Array(items)) = value else { return None };
    if items.len() != 3
        || items.iter().any(|item| number(Some(item), f64::NEG_INFINITY, f64::INFINITY).is_none())
    {
        return None;
    }
    Some(items.clone())
}

/// `_inside(point, bounds)`.
fn inside(point: &[f64], bounds: &JsonMap) -> bool {
    let (lower, upper) = (bounds["min"].as_array().unwrap(), bounds["max"].as_array().unwrap());
    (0..3).all(|i| float(&lower[i]) <= point[i] && point[i] <= float(&upper[i]))
}

fn floats(point: &[Json]) -> Vec<f64> {
    point.iter().map(float).collect()
}

/// `_sound(value)`.
fn sound(value: &Json) -> Option<JsonMap> {
    let Json::Object(map) = value else { return None };
    if map.len() != SOUND_FIELDS.len() || !SOUND_FIELDS.iter().all(|key| map.contains_key(*key)) {
        return None;
    }
    let waveform = match map.get("waveform") {
        Some(Json::String(text)) if matches!(text.as_str(), "noise" | "sine" | "triangle") => text.clone(),
        _ => return None,
    };
    let mut result = JsonMap::new();
    result.insert("waveform".into(), Json::String(waveform));
    for (key, minimum, maximum) in [
        ("frequency", 40.0, 12_000.0),
        ("end_frequency", 40.0, 12_000.0),
        ("duration_ms", 10.0, 500.0),
        ("volume", 0.0, 1.0),
    ] {
        result.insert(key.into(), number(map.get(key), minimum, maximum)?);
    }
    Some(result)
}

/// `_color(value)`: `#` and six hex digits, upper-cased.
fn color(value: &Json) -> Option<Json> {
    match value {
        Json::String(text)
            if text.len() == 7
                && text.starts_with('#')
                && text[1..].bytes().all(|b| b.is_ascii_hexdigit()) =>
        {
            Some(Json::String(text.to_ascii_uppercase()))
        }
        _ => None,
    }
}

/// `_DEFAULT_STYLE`, freshly built.
fn default_style() -> JsonMap {
    let mut cues = JsonMap::new();
    for kind in CUE_KINDS {
        let mut cue = JsonMap::new();
        cue.insert("color".into(), Json::from("#FFFFFF"));
        cue.insert("sound".into(), Json::Null);
        cues.insert(kind.into(), Json::Object(cue));
    }
    let mut style = JsonMap::new();
    style.insert("color".into(), Json::from("#FFFFFF"));
    style.insert("width".into(), Json::from(2));
    style.insert("travel_ms".into(), Json::from(90));
    style.insert("impact_ms".into(), Json::from(120));
    style.insert("fire_sound".into(), Json::Null);
    style.insert("cues".into(), Json::Object(cues));
    style
}

/// `_style(value)`; `None` (absent) is the default style.
fn style(value: Option<&Json>) -> Option<JsonMap> {
    let Some(value) = value else { return Some(default_style()) };
    let Json::Object(map) = value else { return None };
    if map.keys().any(|key| !STYLE_FIELDS.contains(&key.as_str())) {
        return None;
    }
    let mut result = default_style();
    if let Some(raw) = map.get("color") {
        result.insert("color".into(), color(raw)?);
    }
    for (field, minimum, maximum) in
        [("width", 1.0, 8.0), ("travel_ms", 40.0, 250.0), ("impact_ms", 60.0, 400.0)]
    {
        if map.contains_key(field) {
            result.insert(field.into(), number(map.get(field), minimum, maximum)?);
        }
    }
    if let Some(raw) = map.get("fire_sound") {
        let value = if raw.is_null() { Json::Null } else { Json::Object(sound(raw)?) };
        result.insert("fire_sound".into(), value);
    }
    if let Some(raw_cues) = map.get("cues") {
        let Json::Object(raw_cues) = raw_cues else { return None };
        if raw_cues.keys().any(|key| !CUE_KINDS.contains(&key.as_str())) {
            return None;
        }
        let Some(Json::Object(cues)) = result.get_mut("cues") else { unreachable!("the default cues") };
        for (kind, raw_cue) in raw_cues {
            let Json::Object(raw_cue) = raw_cue else { return None };
            if raw_cue.keys().any(|key| !CUE_FIELDS.contains(&key.as_str())) {
                return None;
            }
            let Some(Json::Object(cue)) = cues.get(kind).cloned() else { unreachable!("a default cue") };
            let mut cue = cue;
            if let Some(raw) = raw_cue.get("color") {
                cue.insert("color".into(), color(raw)?);
            }
            if let Some(raw) = raw_cue.get("sound") {
                let value = if raw.is_null() { Json::Null } else { Json::Object(sound(raw)?) };
                cue.insert("sound".into(), value);
            }
            cues.insert(kind.clone(), Json::Object(cue));
        }
    }
    Some(result)
}

/// Python's `min(a, b)` and `max(a, b)`: the first unless the second is strictly beyond it.
fn min2(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

fn max2(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

type BoxBounds = ([f64; 3], [f64; 3]);

/// A geometry volume's box, or Python's `KeyError` for a volume that is not one.
fn volume_box(volume: &Json) -> Option<BoxBounds> {
    let (Some(Json::Array(lower)), Some(Json::Array(upper))) = (volume.get("min"), volume.get("max")) else {
        return None;
    };
    Some((
        [float(&lower[0]), float(&lower[1]), float(&lower[2])],
        [float(&upper[0]), float(&upper[1]), float(&upper[2])],
    ))
}

/// `_segment_box_entry(origin, destination, volume)`.
fn segment_box_entry(origin: &[f64; 3], destination: &[f64], volume: &BoxBounds) -> Option<f64> {
    let (lower, upper) = volume;
    let (mut entry, mut exit) = (0.0_f64, 1.0_f64);
    for axis in 0..3 {
        let delta = destination[axis] - origin[axis];
        if delta.abs() < 1e-12 {
            if origin[axis] < lower[axis] || origin[axis] > upper[axis] {
                return None;
            }
            continue;
        }
        let first = (lower[axis] - origin[axis]) / delta;
        let second = (upper[axis] - origin[axis]) / delta;
        entry = max2(entry, min2(first, second));
        exit = min2(exit, max2(first, second));
        if entry > exit {
            return None;
        }
    }
    (0.0..=1.0).contains(&entry).then_some(entry)
}

/// `_entity_box(entity)`: the conservative box that keeps a tracer out of a visible bystander.
fn entity_box(entity: &Json) -> BoxBounds {
    let position = floats(entity["position"].as_array().unwrap());
    let footprint = &entity["footprint"];
    let (x, y) = if footprint["kind"] == "circle" {
        let radius = float(&footprint["radius"]);
        (radius, radius)
    } else {
        let half = footprint["half_extents"].as_array().unwrap();
        (float(&half[0]), float(&half[1]))
    };
    let height = float(&entity["height"]);
    (
        [position[0] - x, position[1] - y, position[2]],
        [position[0] + x, position[1] + y, position[2] + height],
    )
}

/// `_miss_destination(...)`: a near-miss endpoint beside the target, faded before the first
/// visible obstacle, or `None`. `Err` is Python's `KeyError` for a non-box geometry volume.
#[allow(clippy::too_many_arguments)]
fn miss_destination(
    origin: &[f64],
    intended: &[f64],
    target: &Json,
    bounds: &JsonMap,
    public_geometry: &[Json],
    public_entities: &[Json],
    source_id: &str,
    target_id: &str,
) -> Result<Option<Vec<f64>>, ()> {
    let position = floats(target["position"].as_array().unwrap());
    let (dx, dy) = (position[0] - origin[0], position[1] - origin[1]);
    let center_distance = hypot(&[dx, dy]);
    if center_distance < 1e-9 {
        return Ok(None);
    }
    let direction = [dx / center_distance, dy / center_distance];
    let perpendicular = [-direction[1], direction[0]];
    let footprint = &target["footprint"];
    let (lateral, forward) = if footprint["kind"] == "circle" {
        let radius = float(&footprint["radius"]);
        (radius, radius)
    } else {
        let half = footprint["half_extents"].as_array().unwrap();
        let (hx, hy) = (float(&half[0]), float(&half[1]));
        (
            perpendicular[0].abs() * hx + perpendicular[1].abs() * hy,
            direction[0].abs() * hx + direction[1].abs() * hy,
        )
    };
    let margin = min2(0.15, max2(0.05, lateral * 0.2));
    let near_distance = center_distance - forward;
    if near_distance <= 1e-7 {
        return Ok(None);
    }
    let endpoint_distance = center_distance + forward + 0.05;
    // The offset is measured at the target's near face, so a close shot cannot cut through it.
    let endpoint_lateral = (lateral + margin) * endpoint_distance / near_distance;
    let mut candidates = Vec::new();
    for side in [1.0, -1.0] {
        let candidate = vec![
            origin[0] + direction[0] * endpoint_distance + perpendicular[0] * endpoint_lateral * side,
            origin[1] + direction[1] * endpoint_distance + perpendicular[1] * endpoint_lateral * side,
            intended[2],
        ];
        if inside(&candidate, bounds) {
            candidates.push(candidate);
        }
    }
    let mut obstacles = Vec::new();
    for item in public_geometry.iter().filter(|item| item["active"] == Json::Bool(true)) {
        obstacles.push(volume_box(&item["volume"]).ok_or(())?);
    }
    for item in public_entities {
        let id = item["id"].as_str().unwrap_or_default();
        if item["active"] == Json::Bool(true) && id != source_id && id != target_id {
            obstacles.push(entity_box(item));
        }
    }
    let mut clipped: Vec<(f64, Vec<f64>)> = Vec::new();
    for candidate in candidates {
        let start = [origin[0], origin[1], origin[2]];
        let mut first_entry: Option<f64> = None;
        for volume in &obstacles {
            if let Some(entry) = segment_box_entry(&start, &candidate, volume) {
                first_entry = Some(first_entry.map_or(entry, |known| min2(known, entry)));
            }
        }
        let Some(first_entry) = first_entry else { return Ok(Some(candidate)) };
        let parameter = max2(0.0, first_entry - 1e-4);
        let endpoint: Vec<f64> = (0..3).map(|i| start[i] + (candidate[i] - start[i]) * parameter).collect();
        if parameter > 1e-7 {
            clipped.push((parameter, endpoint));
        }
    }
    // An obstacle before the target makes the full near-miss impossible: fade the longest safe
    // segment before it, without inventing an impact.
    let mut best: Option<(f64, Vec<f64>)> = None;
    for (parameter, endpoint) in clipped {
        if best.as_ref().is_none_or(|(known, _)| parameter > *known) {
            best = Some((parameter, endpoint));
        }
    }
    Ok(best.map(|(_, endpoint)| endpoint))
}

fn ids(rows: &Json) -> HashSet<String> {
    rows.as_array().unwrap().iter().filter_map(|row| row["id"].as_str().map(str::to_owned)).collect()
}

/// `project_shot_effect(request, scene=...)`: a safe cue for one already-resolved shot, or
/// `None` to suppress an invalid cue.
pub fn project_shot_effect(request: &JsonMap, scene: &Json) -> Option<JsonMap> {
    if request.keys().any(|key| !REQUEST_FIELDS.contains(&key.as_str())) {
        return None;
    }
    let required = REQUEST_FIELDS.iter().filter(|key| !matches!(**key, "style" | "target_kind"));
    if required.clone().any(|key| !request.contains_key(*key)) {
        return None;
    }
    let admitted = admit_spatial_scene(scene).ok()?.as_payload();
    let public = player_known_spatial_scene(&Json::Object(admitted.clone()), false).ok()?;
    let (Some(Json::String(source_id)), Some(Json::String(target_id))) =
        (request.get("source_id"), request.get("target_id"))
    else {
        return None;
    };
    let target_kind = match request.get("target_kind") {
        None => "entity".to_owned(),
        Some(Json::String(kind)) if kind == "entity" || kind == "zone" => kind.clone(),
        Some(_) => return None,
    };
    let visible_entities = ids(&public["entities"]);
    let visible_geometry = ids(&public["geometry"]);
    let visible_zones = ids(&public["zones"]);
    let visible_target = if target_kind == "zone" { &visible_zones } else { &visible_entities };
    if !visible_entities.contains(source_id) || !visible_target.contains(target_id) {
        return None;
    }
    let map = admitted["map"].as_object().unwrap();
    let bounds = map["bounds"].as_object().unwrap();
    let origin = point(request.get("origin"))?;
    let mut destination = point(request.get("destination"))?;
    if !inside(&floats(&origin), bounds) || !inside(&floats(&destination), bounds) {
        return None;
    }
    let delay_ms = number(request.get("delay_ms"), 0.0, 60_000.0);
    if request.get("style").is_some_and(Json::is_null) {
        return None;
    }
    let style = style(request.get("style"));
    let (Some(delay_ms), Some(Json::Bool(miss)), Some(Json::Array(contacts_value)), Some(style)) =
        (delay_ms, request.get("miss"), request.get("contacts"), style)
    else {
        return None;
    };
    let miss = *miss;
    if contacts_value.len() > 64 {
        return None;
    }
    if target_kind == "zone" && (miss || !contacts_value.is_empty()) {
        return None;
    }
    let entity_rows: Vec<&Json> = admitted["entities"].as_array().unwrap().iter().collect();
    let geometry_ids = ids(&admitted["geometry"]);
    let entity_ids = ids(&admitted["entities"]);
    let mut contacts = Vec::new();
    for raw in contacts_value {
        let Json::Object(raw) = raw else { return None };
        if raw.len() != CONTACT_FIELDS.len() || !CONTACT_FIELDS.iter().all(|key| raw.contains_key(*key)) {
            return None;
        }
        let point = point(raw.get("point"))?;
        let (kind, id, cue) = (&raw["kind"], &raw["id"], &raw["cue"]);
        let (known, visible) = match kind.as_str() {
            Some("entity") => (&entity_ids, &visible_entities),
            Some("geometry") => (&geometry_ids, &visible_geometry),
            _ => return None,
        };
        let Json::String(id) = id else { return None };
        let valid_cue = matches!(cue.as_str(), Some(cue) if CUE_KINDS.contains(&cue));
        if !known.contains(id) || !visible.contains(id) || !valid_cue || !inside(&floats(&point), bounds) {
            return None;
        }
        let mut contact = JsonMap::new();
        contact.insert("point".into(), Json::Array(point));
        contact.insert("cue".into(), cue.clone());
        contacts.push(contact);
    }
    if miss {
        if !contacts.is_empty() {
            return None;
        }
        let target = entity_rows.iter().find(|row| row["id"] == **target_id)?;
        let endpoint = miss_destination(
            &floats(&origin),
            &floats(&destination),
            target,
            bounds,
            public["geometry"].as_array().unwrap(),
            public["entities"].as_array().unwrap(),
            source_id,
            target_id,
        )
        .ok()??;
        destination = endpoint.into_iter().map(number_of).collect();
    } else if let Some(last) = contacts.last() {
        destination = last["point"].as_array().unwrap().clone();
    }
    let mut cue = JsonMap::new();
    cue.insert("map_id".into(), map["id"].clone());
    cue.insert("origin".into(), Json::Array(origin));
    cue.insert("destination".into(), Json::Array(destination));
    cue.insert("delay_ms".into(), delay_ms);
    cue.insert("miss".into(), Json::Bool(miss));
    cue.insert("contacts".into(), Json::Array(contacts.into_iter().map(Json::Object).collect()));
    cue.insert("style".into(), Json::Object(style));
    Some(cue)
}
