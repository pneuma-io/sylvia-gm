//! `domain/panel_layout.py`: a Ruleset's declarative starting arrangement for its game panels.

use crate::content::validate_content_shape;
use crate::error::{DomainError, DomainResult};
use crate::package_library::{
    parse_canonical_json_bytes, JsonShapeLimits, SurfaceDeclaration, DEFAULT_LABEL,
};
use crate::pyops::py_eq;
use serde_json::json;
use std::collections::BTreeMap;
use sylvia_base::{Json, JsonMap};

/// `PANEL_LAYOUT_PATH`.
pub const PANEL_LAYOUT_PATH: &str = "ui/panel-layout.json";
/// `PANEL_LAYOUT_BYTE_LIMIT`.
pub const PANEL_LAYOUT_BYTE_LIMIT: usize = 64 * 1024;

fn value(message: &str) -> DomainError {
    DomainError::value(message)
}

/// The state Python's nested helpers share.
struct Layout<'s> {
    panels: BTreeMap<&'s str, &'s SurfaceDeclaration>,
    used_panels: Vec<String>,
    node_ids: Vec<String>,
}

/// `identity(value, used)`: unique within its set (the schema has made it text).
fn identity(value: &Json, used: &mut Vec<String>) -> DomainResult<String> {
    let text = value.as_str().unwrap_or_default().to_owned();
    if used.contains(&text) {
        return Err(DomainError::value("Panel layout identities must be unique"));
    }
    used.push(text.clone());
    Ok(text)
}

impl Layout<'_> {
    /// `panel(value, placement)`.
    fn panel(&mut self, item: &Json, placement: Option<&str>) -> DomainResult<String> {
        let Some(name) = item
            .as_str()
            .filter(|name| self.panels.contains_key(name) && !self.used_panels.iter().any(|u| u == name))
        else {
            return Err(value("Panel layout references an unknown or repeated panel"));
        };
        if let Some(placement) = placement {
            if !self.panels[name].allowed_placements.iter().any(|allowed| allowed == placement) {
                return Err(value("Panel layout uses a placement the panel does not allow"));
            }
        }
        self.used_panels.push(name.to_owned());
        Ok(name.to_owned())
    }

    /// `tree(value, placement, depth)`.
    fn tree(&mut self, node: &Json, placement: &str, depth: usize) -> DomainResult<Option<Json>> {
        if node.is_null() {
            return Ok(None);
        }
        let Some(map) = node.as_object().filter(|_| depth <= 16 && self.node_ids.len() < 256) else {
            return Err(value("Panel layout tree is invalid or too deep"));
        };
        let kind = map.get("kind").cloned().unwrap_or(Json::Null);
        if py_eq(&kind, &Json::from("tabs")) {
            let node_id = identity(&map["id"], &mut self.node_ids)?;
            let mut members = Vec::new();
            for item in map["panels"].as_array().into_iter().flatten() {
                members.push(self.panel(item, Some(placement))?);
            }
            let active = &map["active"];
            if !members.iter().any(|member| py_eq(&Json::from(member.as_str()), active)) {
                return Err(value("Panel tab group must select one of its own panels"));
            }
            return Ok(Some(json!({"kind": kind, "id": node_id, "panels": members, "active": active})));
        }
        if py_eq(&kind, &Json::from("split")) {
            let node_id = identity(&map["id"], &mut self.node_ids)?;
            let ratio = map["ratio"].clone();
            let first = self.tree(&map["first"], placement, depth + 1)?;
            let second = self.tree(&map["second"], placement, depth + 1)?;
            let (Some(first), Some(second)) = (first, second) else {
                return Err(value("Panel split must contain two groups"));
            };
            return Ok(Some(json!({
                "kind": kind, "id": node_id, "axis": map["axis"], "ratio": ratio, "first": first, "second": second,
            })));
        }
        Err(value("Panel layout tree kind is unsupported"))
    }
}

/// `parse_panel_layout(value, surfaces)`: closed layout data with exact panel references.
pub fn parse_panel_layout(bytes: &[u8], surfaces: &[SurfaceDeclaration]) -> DomainResult<JsonMap> {
    if bytes.len() > PANEL_LAYOUT_BYTE_LIMIT {
        return Err(value("Panel starting layout exceeds the byte limit"));
    }
    let limits = JsonShapeLimits::new(20, 4096).with_object_members(8);
    let raw = parse_canonical_json_bytes(bytes, DEFAULT_LABEL, limits)?;
    validate_content_shape("panel_layout", &raw, Some(PANEL_LAYOUT_PATH))?;
    // `{item.identity: item for item in surfaces if item.renderer is not None}`: a later
    // surface with the same identity wins.
    let mut panels = BTreeMap::new();
    for surface in surfaces.iter().filter(|surface| surface.renderer.is_some()) {
        panels.insert(surface.identity.as_str(), surface);
    }
    let mut layout = Layout { panels, used_panels: Vec::new(), node_ids: Vec::new() };
    let sidebar = layout.tree(&raw["sidebar"], "docked", 0)?;
    let mut window_ids = Vec::new();
    let mut windows = Vec::new();
    for window in raw["windows"].as_array().into_iter().flatten() {
        let window_id = identity(&window["id"], &mut window_ids)?;
        let title = window["title"].as_str().unwrap_or_default();
        if title.len() > 256 {
            return Err(value("Panel starting window title is invalid"));
        }
        let Some(window_tree) = layout.tree(&window["tree"], "window", 0)? else {
            return Err(value("Panel starting window must contain a panel"));
        };
        windows.push(json!({"id": window_id, "title": title, "tree": window_tree}));
    }
    let mut hidden = Vec::new();
    for item in raw["hidden"].as_array().into_iter().flatten() {
        hidden.push(layout.panel(item, None)?);
    }
    let known: Vec<&str> = layout.panels.keys().copied().collect();
    let result = json!({
        "version": 1,
        "sidebar": sidebar,
        "windows": windows,
        "hidden": hidden,
        "known": known,
        "panel_sizes": {},
        "sidebar_width": null,
        "views": {},
    });
    Ok(result.as_object().cloned().expect("an object"))
}
