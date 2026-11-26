//! `application/piece_table.py`: a Ruleset-declared table projected without exposing another
//! owner's pieces, and a piece selection bound to an advertised action.
//!
//! This is presentation data, not a game engine. The caller supplies viewer identities from the
//! admitted player-control scope, never from a renderer request or table data. Every refusal
//! is Python's one `PieceTableError` message, so the order of the checks cannot show.

use std::collections::{BTreeSet, HashSet};
use sylvia_base::pystr::strip;
use sylvia_base::{Json, JsonMap};

/// `PieceTableError`: a table declaration or selection cannot be safely presented.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PieceTableError(pub &'static str);

impl PieceTableError {
    /// `type(error).__name__`.
    pub fn python_class(&self) -> &'static str {
        "PieceTableError"
    }
}

impl std::fmt::Display for PieceTableError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for PieceTableError {}

const INVALID: PieceTableError = PieceTableError("Invalid piece table");
const INVALID_SELECTION: PieceTableError = PieceTableError("Invalid piece selection");

type Table<T> = Result<T, PieceTableError>;

/// `_object(value, fields)`: a mapping with no other fields.
fn object<'v>(value: Option<&'v Json>, fields: &[&str]) -> Table<&'v JsonMap> {
    match value {
        Some(Json::Object(map)) if map.keys().all(|key| fields.contains(&key.as_str())) => Ok(map),
        _ => Err(INVALID),
    }
}

/// `_items(value, maximum)`: a list of at most `maximum` items.
fn items(value: Option<&Json>, maximum: usize) -> Table<&[Json]> {
    match value {
        Some(Json::Array(items)) if items.len() <= maximum => Ok(items),
        _ => Err(INVALID),
    }
}

/// `_label(value)`: visible text without control characters, within 256 UTF-8 bytes.
fn label(value: Option<&Json>) -> Table<String> {
    match value {
        Some(Json::String(text))
            if !strip(text).is_empty() && !text.chars().any(|c| (c as u32) < 32) && text.len() <= 256 =>
        {
            Ok(text.clone())
        }
        _ => Err(INVALID),
    }
}

/// `type(value) is int`.
fn integer(value: Option<&Json>) -> Option<i64> {
    match value {
        Some(Json::Number(number)) if number.is_i64() || number.is_u64() => {
            Some(number.as_i64().unwrap_or(i64::MAX))
        }
        _ => None,
    }
}

/// `_face(value)`: a bounded, declarative tile face; artwork is never parsed from labels or ids.
fn face(value: Option<&Json>) -> Table<Json> {
    let face = object(value, &["family", "kind", "red"])?;
    let kind = integer(face.get("kind")).filter(|kind| (0..34).contains(kind)).ok_or(INVALID)?;
    let red = match face.get("red") {
        None => false,
        Some(Json::Bool(red)) => *red,
        Some(_) => return Err(INVALID),
    };
    if face.get("family").and_then(Json::as_str) != Some("mahjong") || (red && ![4, 13, 22].contains(&kind)) {
        return Err(INVALID);
    }
    Ok(serde_json::json!({"family": "mahjong", "kind": kind, "red": red}))
}

/// `value in {...}` for text.
fn one_of<'v>(value: Option<&'v Json>, allowed: &[&str]) -> Table<&'v str> {
    value.and_then(Json::as_str).filter(|text| allowed.contains(text)).ok_or(INVALID)
}

/// One visible piece of a group.
fn piece(value: &Json, piece_ids: &mut HashSet<String>, action_ids: &BTreeSet<String>) -> Table<Json> {
    let piece =
        object(Some(value), &["id", "label", "detail", "emphasis", "action_id", "face", "faces", "flags"])?;
    let piece_id = label(piece.get("id"))?;
    if !piece_ids.insert(piece_id.clone()) {
        return Err(INVALID);
    }
    let mut safe = JsonMap::new();
    safe.insert("id".into(), Json::String(piece_id));
    safe.insert("label".into(), Json::String(label(piece.get("label"))?));
    if piece.contains_key("detail") {
        safe.insert("detail".into(), Json::String(label(piece.get("detail"))?));
    }
    if piece.contains_key("face") {
        safe.insert("face".into(), face(piece.get("face"))?);
    }
    if piece.contains_key("faces") {
        let faces =
            items(piece.get("faces"), 4)?.iter().map(|item| face(Some(item))).collect::<Table<Vec<_>>>()?;
        safe.insert("faces".into(), Json::Array(faces));
    }
    if piece.contains_key("flags") {
        let flags = items(piece.get("flags"), 6)?;
        let allowed = ["drawn", "sideways", "stacked", "back", "claimed", "latest"];
        let mut seen = HashSet::new();
        for flag in flags {
            if !seen.insert(one_of(Some(flag), &allowed)?) {
                return Err(INVALID);
            }
        }
        safe.insert("flags".into(), Json::Array(flags.to_vec()));
    }
    if piece.contains_key("emphasis") {
        safe.insert(
            "emphasis".into(),
            Json::from(one_of(piece.get("emphasis"), &["normal", "accent", "muted"])?),
        );
    }
    if piece.contains_key("action_id") {
        let action_id = label(piece.get("action_id"))?;
        if !action_ids.contains(&action_id) {
            return Err(INVALID);
        }
        safe.insert("action_id".into(), Json::String(action_id));
    }
    Ok(Json::Object(safe))
}

/// `project_piece_table(value, viewer_ids=..., action_ids=...)`: only declared public fields
/// and the pieces this viewer owns. A hidden group may disclose its count, never its pieces'
/// identities, labels, action references or metadata.
pub fn project_piece_table(
    value: &Json,
    viewer_ids: &BTreeSet<String>,
    action_ids: &BTreeSet<String>,
) -> Table<JsonMap> {
    let table = object(Some(value), &["format", "title", "status", "areas", "layout"])?;
    if integer(table.get("format")) != Some(1) {
        return Err(INVALID);
    }
    let mut result = JsonMap::new();
    result.insert("format".into(), Json::from(1));
    result.insert("title".into(), Json::String(label(table.get("title"))?));
    if let Some(layout) = table.get("layout") {
        if layout != "four_sided" {
            return Err(INVALID);
        }
        result.insert("layout".into(), Json::from("four_sided"));
    }
    let mut status = Vec::new();
    let no_status = Json::Array(Vec::new());
    for raw in items(Some(table.get("status").unwrap_or(&no_status)), 16)? {
        let row = object(Some(raw), &["label", "value"])?;
        status
            .push(serde_json::json!({"label": label(row.get("label"))?, "value": label(row.get("value"))?}));
    }
    result.insert("status".into(), Json::Array(status));
    let mut areas = Vec::new();
    let (mut identities, mut positions) = (HashSet::new(), HashSet::new());
    let (mut piece_total, mut group_total) = (0usize, 0usize);
    for raw in items(table.get("areas"), 5)? {
        let area = object(Some(raw), &["id", "label", "position", "groups", "badge", "score", "active"])?;
        let identity = label(area.get("id"))?;
        let position = one_of(area.get("position"), &["top", "left", "center", "right", "bottom"])?;
        if identities.contains(&identity) || positions.contains(position) {
            return Err(INVALID);
        }
        identities.insert(identity.clone());
        positions.insert(position);
        let mut groups = Vec::new();
        for group_raw in items(area.get("groups"), 64)? {
            group_total += 1;
            if group_total > 64 {
                return Err(INVALID);
            }
            let group = object(
                Some(group_raw),
                &["id", "label", "visibility", "owner", "show_count", "pieces", "role", "source"],
            )?;
            let visibility = one_of(group.get("visibility"), &["public", "owner"])?;
            let owner = if visibility == "owner" { Some(label(group.get("owner"))?) } else { None };
            let show_count = match group.get("show_count") {
                None => false,
                Some(Json::Bool(flag)) => *flag,
                Some(_) => return Err(INVALID),
            };
            let pieces = items(group.get("pieces"), 256)?;
            piece_total += pieces.len();
            if piece_total > 1024 {
                return Err(INVALID);
            }
            let mut safe = JsonMap::new();
            safe.insert("id".into(), Json::String(label(group.get("id"))?));
            safe.insert("label".into(), Json::String(label(group.get("label"))?));
            if group.contains_key("role") {
                let roles = ["rack", "pond", "meld", "indicators", "actions", "message"];
                safe.insert("role".into(), Json::from(one_of(group.get("role"), &roles)?));
            }
            let concealed = owner.is_some_and(|owner| !viewer_ids.contains(&owner));
            if concealed {
                safe.insert("concealed".into(), Json::Bool(true));
                if show_count {
                    safe.insert("count".into(), Json::from(pieces.len()));
                }
            } else {
                let mut piece_ids = HashSet::new();
                let safe_pieces = pieces
                    .iter()
                    .map(|item| piece(item, &mut piece_ids, action_ids))
                    .collect::<Table<Vec<_>>>()?;
                safe.insert("pieces".into(), Json::Array(safe_pieces));
            }
            if group.contains_key("source") && !concealed {
                safe.insert(
                    "source".into(),
                    Json::from(one_of(group.get("source"), &["left", "across", "right", "self"])?),
                );
            }
            groups.push(Json::Object(safe));
        }
        let mut safe_area = JsonMap::new();
        safe_area.insert("id".into(), Json::String(identity));
        safe_area.insert("label".into(), Json::String(label(area.get("label"))?));
        safe_area.insert("position".into(), Json::from(position));
        safe_area.insert("groups".into(), Json::Array(groups));
        for field in ["badge", "score"] {
            if area.contains_key(field) {
                safe_area.insert(field.into(), Json::String(label(area.get(field))?));
            }
        }
        match area.get("active") {
            None => {}
            Some(Json::Bool(active)) => {
                safe_area.insert("active".into(), Json::Bool(*active));
            }
            Some(_) => return Err(INVALID),
        }
        areas.push(Json::Object(safe_area));
    }
    result.insert("areas".into(), Json::Array(areas));
    Ok(result)
}

/// `resolve_piece_action_parameters(action, selection, projected_table)`: an advertised piece
/// identity bound only from the viewer's current safe table.
pub fn resolve_piece_action_parameters(
    action: &JsonMap,
    selection: &Json,
    projected_table: &JsonMap,
) -> Table<JsonMap> {
    let (Some(Json::Object(bindings)), Some(Json::Object(fixed)), Json::Object(selection)) =
        (action.get("bindings"), action.get("params"), selection)
    else {
        return Err(INVALID_SELECTION);
    };
    let binding = bindings.iter().next().filter(|(_, source)| bindings.len() == 1 && *source == "piece");
    let Some((parameter, _)) = binding else { return Err(INVALID_SELECTION) };
    if fixed.contains_key(parameter) || selection.len() != 1 || !selection.contains_key("piece") {
        return Err(INVALID_SELECTION);
    }
    let piece_id = label(selection.get("piece"))?;
    let action_id = action.get("id").unwrap_or(&Json::Null);
    let pieces = projected_table["areas"]
        .as_array()
        .into_iter()
        .flatten()
        .flat_map(|area| area["groups"].as_array().into_iter().flatten())
        .flat_map(|group| group.get("pieces").and_then(Json::as_array).into_iter().flatten());
    let mut permitted = false;
    for item in pieces {
        if item["id"] == *piece_id && item.get("action_id").unwrap_or(&Json::Null) == action_id {
            permitted = true;
            break;
        }
    }
    if !permitted {
        return Err(INVALID_SELECTION);
    }
    let mut parameters = fixed.clone();
    parameters.insert(parameter.clone(), Json::String(piece_id));
    Ok(parameters)
}
