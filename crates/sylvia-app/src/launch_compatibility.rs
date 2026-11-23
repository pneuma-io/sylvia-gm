//! `application/launch_compatibility.py`: first-party launch policy. `ARCHIVED_RULESET_IDS` and
//! `require_active_ruleset` live in [`crate::interview_content`] (lane 1 needed them first);
//! this module holds `player_ui_surfaces`.

use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{PackageLock, PackageRole};
use sylvia_domain::error::DomainResult;
use sylvia_domain::pyops::{py_get_attr, py_item, py_iter};

/// The Ruleset whose player encounter-ending control is withdrawn.
const SRD_LITE_PACKAGE_ID: &str = "sylvia.srd-lite.bg3";

/// `player_ui_surfaces(surfaces, package_lock)`: withdraw Lite's player encounter-ending
/// control without rewriting saved package pins. When SRD Lite is the lock's primary package,
/// each surface keeps its keys in order, with `actions` replaced by the actions whose `intent`
/// is not `end_combat_encounter`; otherwise the surfaces are returned unchanged.
///
/// As in Python, a surface without `actions` is a `KeyError`, `actions` that are not iterable a
/// `TypeError`, and an action that is not a mapping an `AttributeError`.
pub fn player_ui_surfaces(surfaces: &[JsonMap], package_lock: &PackageLock) -> DomainResult<Vec<JsonMap>> {
    let lite = package_lock
        .entries()
        .iter()
        .any(|entry| entry.role() == PackageRole::Primary && entry.package_id() == SRD_LITE_PACKAGE_ID);
    if !lite {
        return Ok(surfaces.to_vec());
    }
    surfaces
        .iter()
        .map(|surface| {
            let as_value = Json::Object(surface.clone());
            let mut kept = Vec::new();
            for action in py_iter(py_item(&as_value, "actions")?)? {
                if py_get_attr(&action, "intent")?.and_then(Json::as_str) != Some("end_combat_encounter") {
                    kept.push(action);
                }
            }
            // `{**surface, "actions": [...]}`: the key keeps its place.
            let mut replaced = surface.clone();
            replaced.insert("actions".into(), Json::Array(kept));
            Ok(replaced)
        })
        .collect()
}
