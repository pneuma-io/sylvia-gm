//! `application/plan_network_admission.py`: check present Plan network graphs against their
//! renderer shape and declared tactical maps.

use crate::network_graph::project_network_graph;
use crate::plan_spatial_admission::{is_map_renderer, resolve, source_of, state_source_path};
use crate::plan_state_admission::plan_state;
use std::collections::BTreeSet;
use sylvia_base::{pystr, Json, JsonMap};
use sylvia_domain::content::ContentShapeError;
use sylvia_domain::error::{DomainError, DomainResult, RulesetDiagnostic};
use sylvia_domain::package_library::SurfaceDeclaration;
use sylvia_domain::pyops::{py_item, py_unhashable};

/// `_reject(path, message)`: authoring locations and fixed explanations, never private values.
fn reject(path: &str, message: &str) -> DomainError {
    ContentShapeError::new(vec![RulesetDiagnostic {
        code: "plan_network_graph_invalid".into(),
        phase: "content".into(),
        file: Some("plan.json".into()),
        path: Some(path.into()),
        message: message.into(),
        guidance: Some("Correct the network_graph presentation or its declared map/entity reference.".into()),
        ..Default::default()
    }])
    .into()
}

/// `_reveal_records(value, path, maximum)`: validate future-visible records in the local copy,
/// marking each visible.
fn reveal_records(value: Option<&mut Json>, path: &str, maximum: usize) -> DomainResult<()> {
    let Some(Json::Object(records)) = value else {
        return Err(reject(path, "Network records must be a bounded object."));
    };
    if records.len() > maximum {
        return Err(reject(path, "Network records must be a bounded object."));
    }
    for (identity, record) in records.iter_mut() {
        if identity.is_empty() || identity.as_str() != pystr::strip(identity) || identity.len() > 128 {
            return Err(reject(path, "Network record identity is invalid."));
        }
        let Some(record) =
            record.as_object_mut().filter(|record| record.get("visible").is_some_and(Json::is_boolean))
        else {
            return Err(reject(
                &format!("{path}.{identity}"),
                "Network record requires an explicit visible boolean.",
            ));
        };
        record.insert("visible".into(), Json::Bool(true));
    }
    Ok(())
}

fn keys(graph: &JsonMap, field: &str) -> Vec<String> {
    graph.get(field).and_then(Json::as_object).map(|map| map.keys().cloned().collect()).unwrap_or_default()
}

/// `key in mapping` for a JSON value against text keys; Python hashes it first.
fn text_member(value: &Json, members: impl IntoIterator<Item = impl AsRef<str>>) -> DomainResult<bool> {
    match value {
        Json::Array(_) | Json::Object(_) => Err(py_unhashable(value)),
        Json::String(text) => Ok(members.into_iter().any(|member| member.as_ref() == text)),
        _ => Ok(false),
    }
}

type Maps = Vec<(String, Option<BTreeSet<String>>)>;

/// `_validate_graph(value, path, maps)`: the closed renderer shape, then references, before any
/// visibility filtering.
fn validate_graph(value: &Json, path: &str, maps: &Maps) -> DomainResult<()> {
    let Json::Object(original) = value else {
        return Err(reject(path, "Network graph source must be an object."));
    };
    let mut graph = original.clone();
    reveal_records(graph.get_mut("groups"), &format!("{path}.groups"), 512)?;
    reveal_records(graph.get_mut("nodes"), &format!("{path}.nodes"), 512)?;
    reveal_records(graph.get_mut("links"), &format!("{path}.links"), 512)?;
    let node_ids = keys(&graph, "nodes");
    if let Some(Json::Object(nodes)) = graph.get_mut("nodes") {
        for (identity, node) in nodes.iter_mut() {
            let functions_path = format!("{path}.nodes.{identity}.functions");
            let node = node.as_object_mut().expect("revealed records are objects");
            match node.get_mut("functions") {
                Some(functions) => reveal_records(Some(functions), &functions_path, 64)?,
                None => reveal_records(Some(&mut Json::Object(JsonMap::new())), &functions_path, 64)?,
            }
        }
    }
    if let Some(Json::Object(links)) = graph.get("links") {
        for (identity, link) in links {
            for endpoint in ["source", "target"] {
                let target = link.get(endpoint).and_then(Json::as_str);
                if !target.is_some_and(|target| node_ids.iter().any(|id| id == target)) {
                    return Err(reject(
                        &format!("{path}.links.{identity}.{endpoint}"),
                        "Network link must reference an authored node.",
                    ));
                }
            }
        }
    }
    match project_network_graph(&Json::Object(graph.clone()), &BTreeSet::new()) {
        Ok(_) => {}
        Err(DomainError::Diagnosed { error_type: "NetworkGraphError", message, .. }) => {
            return Err(reject(path, &message))
        }
        Err(other) => return Err(other),
    }
    let group_ids = keys(&graph, "groups");
    for (identity, node) in graph.get("nodes").and_then(Json::as_object).into_iter().flatten() {
        let node_path = format!("{path}.nodes.{identity}");
        let group = node.get("group_id").unwrap_or(&Json::Null);
        if !group.is_null() && !text_member(group, &group_ids)? {
            return Err(reject(
                &format!("{node_path}.group_id"),
                "Network group must reference an authored group.",
            ));
        }
        let binding = node.get("physical_binding").unwrap_or(&Json::Null);
        if binding.is_null() {
            continue;
        }
        let (surface_id, entity_id) = (py_item(binding, "surface_id")?, py_item(binding, "entity_id")?);
        if !text_member(surface_id, maps.iter().map(|(id, _)| id))? {
            return Err(reject(
                &format!("{node_path}.physical_binding.surface_id"),
                "Network physical binding must reference a declared tactical map surface.",
            ));
        }
        let surface = surface_id.as_str().expect("a text member");
        let entities = maps.iter().find(|(id, _)| id == surface).and_then(|(_, entities)| entities.as_ref());
        if let Some(entities) = entities {
            if !text_member(entity_id, entities)? {
                return Err(reject(
                    &format!("{node_path}.physical_binding.entity_id"),
                    "Network physical binding must reference an entity in the authored map.",
                ));
            }
        }
    }
    Ok(())
}

/// `validate_plan_networks(payload, surfaces)`: check present Plan graphs; absent state can still
/// be created by ordinary runtime tools.
pub fn validate_plan_networks(payload: &JsonMap, surfaces: &[SurfaceDeclaration]) -> DomainResult<()> {
    let (state, paths) = plan_state(payload);
    let mut maps: Maps = Vec::new();
    for surface in surfaces.iter().filter(|surface| is_map_renderer(surface)) {
        let data = resolve(&source_of(surface), &state)?;
        let entities = data.get("scene").and_then(|scene| scene.get("entities")).and_then(Json::as_array);
        let ids = entities.map(|entities| {
            entities
                .iter()
                .filter_map(|item| item.as_object().and_then(|item| item.get("id")).and_then(Json::as_str))
                .map(str::to_owned)
                .collect::<BTreeSet<_>>()
        });
        match maps.iter_mut().find(|(id, _)| *id == surface.identity) {
            Some((_, slot)) => *slot = ids,
            None => maps.push((surface.identity.clone(), ids)),
        }
    }
    for surface in surfaces {
        if surface.renderer.as_deref() != Some("network_graph") {
            continue;
        }
        let source = source_of(surface);
        let mut path = state_source_path(&source, &state, &paths);
        if path.is_none() {
            let fields = source.get("fields").and_then(Json::as_object);
            if fields.is_some_and(|fields| {
                fields.values().any(|item| state_source_path(item, &state, &paths).is_some())
            }) {
                path = Some("$.initial_state.mechanics".into());
            }
        }
        if let Some(path) = path {
            validate_graph(&resolve(&source, &state)?, &path, &maps)?;
        }
    }
    Ok(())
}
