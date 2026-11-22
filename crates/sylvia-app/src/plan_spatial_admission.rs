//! `application/plan_spatial_admission.py`: validate explicitly authored Plan scenes selected by
//! declared tactical surfaces, and explain offset multi-cell props.
//!
//! `_plan_state` is `plan_state_admission::plan_state`.

use crate::plan_state_admission::plan_state;
use crate::tactical_grid::{resolve_surface_source, NoParticipant};
use std::collections::HashMap;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::content::ContentShapeError;
use sylvia_domain::error::{DomainError, DomainResult, RulesetDiagnostic};
use sylvia_domain::package_library::SurfaceDeclaration;
use sylvia_engine::spatial::{admit_spatial_scene, SpatialError};

const MAP_RENDERERS: [&str; 2] = ["tactical_grid", "coordinate_map"];

/// The surface's source as Python holds it: `None` or a mapping.
pub(crate) fn source_of(surface: &SurfaceDeclaration) -> Json {
    surface.source.clone().map(Json::Object).unwrap_or(Json::Null)
}

pub(crate) fn resolve(source: &Json, state: &JsonMap) -> DomainResult<Json> {
    resolve_surface_source(source, state, &[] as &[NoParticipant])
}

pub(crate) fn is_map_renderer(surface: &SurfaceDeclaration) -> bool {
    surface.renderer.as_deref().is_some_and(|renderer| MAP_RENDERERS.contains(&renderer))
}

/// `_state_source_path(source, state, paths)`: the authored value path when a state source
/// exists, including an explicit null.
pub(crate) fn state_source_path(
    source: &Json,
    state: &JsonMap,
    paths: &HashMap<String, String>,
) -> Option<String> {
    let source = source.as_object()?;
    if source.get("kind") != Some(&Json::from("state")) {
        return None;
    }
    let (namespace, key) = (source.get("namespace")?.as_str()?, source.get("key")?.as_str()?);
    let exact = format!("{namespace}.{key}");
    if state.contains_key(&exact) {
        return paths.get(&exact).cloned();
    }
    let (root, remainder) = match key.split_once('.') {
        Some((root, remainder)) => (root, Some(remainder)),
        None => (key, None),
    };
    let root_identity = format!("{namespace}.{root}");
    let mut current = state.get(&root_identity)?;
    let mut path = paths.get(&root_identity)?.clone();
    let Some(remainder) = remainder else { return Some(path) };
    for field in remainder.split('.') {
        // The referenced branch exists but cannot contain the declared path.
        let Some(map) = current.as_object() else { return Some(path) };
        current = map.get(field)?;
        path.push('.');
        path.push_str(field);
    }
    Some(path)
}

/// `validate_plan_spatial_scenes(payload, surfaces)`: admit each present Plan-authored scene a
/// tactical-grid declaration reaches.
pub fn validate_plan_spatial_scenes(payload: &JsonMap, surfaces: &[SurfaceDeclaration]) -> DomainResult<()> {
    let (state, paths) = plan_state(payload);
    for surface in surfaces {
        let Some(source) = surface.source.as_ref().filter(|_| is_map_renderer(surface)) else { continue };
        let source_value = Json::Object(source.clone());
        let kind = source.get("kind");
        let (authored_path, scene) = if kind == Some(&Json::from("object")) {
            let scene_source =
                source.get("fields").and_then(Json::as_object).and_then(|f| f.get("scene")).cloned();
            let scene_source = scene_source.unwrap_or(Json::Null);
            (state_source_path(&scene_source, &state, &paths), resolve(&scene_source, &state)?)
        } else if kind == Some(&Json::from("state")) {
            let authored_path = state_source_path(&source_value, &state, &paths);
            let resolved = resolve(&source_value, &state)?;
            match &resolved {
                Json::Object(map) => {
                    let Some(scene) = map.get("scene") else { continue };
                    (authored_path.map(|path| format!("{path}.scene")), scene.clone())
                }
                _ => (authored_path, resolved.clone()),
            }
        } else {
            continue;
        };
        let Some(authored_path) = authored_path else { continue };
        match admit_spatial_scene(&scene) {
            Ok(_) => {}
            Err(SpatialError::Validation(error)) => {
                let suffix = if error.path == "$" { "" } else { &error.path[1..] };
                return Err(ContentShapeError::new(vec![RulesetDiagnostic {
                    code: "plan_spatial_scene_invalid".into(),
                    phase: "content".into(),
                    file: Some("plan.json".into()),
                    path: Some(format!("{authored_path}{suffix}")),
                    message: error.to_string(),
                    guidance: Some(
                        "Read the spatial_scene content definition and correct the indicated field.".into(),
                    ),
                    ..Default::default()
                }])
                .into());
            }
            Err(other) => return Err(DomainError::from(other)),
        }
    }
    Ok(())
}

fn number(value: &Json) -> f64 {
    value.as_f64().unwrap_or_default()
}

/// `plan_spatial_warnings(payload, surfaces)`: offset multi-cell props in an admitted draft,
/// explained without changing or rejecting them; at most 16.
pub fn plan_spatial_warnings(
    payload: &JsonMap,
    surfaces: &[SurfaceDeclaration],
) -> DomainResult<Vec<JsonMap>> {
    let (state, _) = plan_state(payload);
    let mut warnings = Vec::new();
    for surface in surfaces {
        if !is_map_renderer(surface) {
            continue;
        }
        let data = resolve(&source_of(surface), &state)?;
        let Some(scene) = data.as_object().and_then(|map| map.get("scene")).filter(|scene| scene.is_object())
        else {
            continue;
        };
        let admitted = admit_spatial_scene(scene).map_err(DomainError::from)?.as_payload();
        let grid = &admitted["map"]["grid"];
        let (size, origin) = (number(&grid["cell_size"]), &grid["origin"]);
        for geometry in admitted["geometry"].as_array().into_iter().flatten() {
            let active = geometry["active"].as_bool().unwrap_or(false);
            let blocking = geometry["movement"] == "blocking";
            let occupies = geometry["occupies_cells"].as_bool().unwrap_or(false);
            if !active || !blocking || !occupies {
                continue;
            }
            let volume = &geometry["volume"];
            let axis =
                |bound: &str, index: usize| (number(&volume[bound][index]) - number(&origin[index])) / size;
            let lower = [axis("min", 0), axis("min", 1)];
            let upper = [axis("max", 0), axis("max", 1)];
            let cells: i64 = (0..2).map(|i| upper[i].ceil() as i64 - lower[i].floor() as i64).product();
            let aligned =
                lower.iter().chain(&upper).all(|value| (value - value.round_ties_even()).abs() < 1e-9);
            if cells < 2 || aligned {
                continue;
            }
            let id = geometry["id"].as_str().unwrap_or_default();
            let mut warning = JsonMap::new();
            warning.insert("code".into(), Json::from("offset_prop_reserves_multiple_cells"));
            warning.insert("surface".into(), Json::from(surface.identity.as_str()));
            warning.insert("geometry".into(), geometry["id"].clone());
            warning.insert(
                "message".into(),
                Json::from(format!(
                    "{id} reserves {cells} grid cells because its box crosses cell boundaries. For a one-cell prop, \
                     align both its origin and dimensions to that cell. Keep intentional larger/offset props; use \
                     occupies_cells: false for a thin physical barrier. This is advice, not a validation failure."
                )),
            );
            warnings.push(warning);
            if warnings.len() == 16 {
                return Ok(warnings);
            }
        }
    }
    Ok(warnings)
}
