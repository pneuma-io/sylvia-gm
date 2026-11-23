//! `application/tactical_grid.py`: resolving a player-surface source, and the player-known scene.
//!
//! The projections and tactical action parameters follow with the spatial queries.

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
