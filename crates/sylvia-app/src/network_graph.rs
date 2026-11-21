//! `application/network_graph.py`: safe projection and dynamic-intent binding for persistent
//! graph surfaces.
//!
//! The host understands only visible nodes, visible links, presentation state, and references
//! to already-visible tactical entities. Rulesets keep discovery, traversal, privilege,
//! detection, sessions and remote operations. Package state reaches these functions as untyped
//! JSON, so Python's operator errors on malformed values are raised with their text.

use serde_json::json;
use std::collections::BTreeSet;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_library::NETWORK_UI_DYNAMIC_SELECTIONS;
use sylvia_domain::pyops::{
    py_eq, py_get_attr, py_item, py_iter, py_require_hashable, py_set, py_str, py_type_name,
};

/// `NETWORK_SELECTION_BINDINGS`.
pub const NETWORK_SELECTION_BINDINGS: &[&str] = NETWORK_UI_DYNAMIC_SELECTIONS;

const MAX_GRAPH_ITEMS: usize = 512;
const MAX_FUNCTIONS_PER_NODE: usize = 64;
const MAX_VISIBLE_FUNCTIONS: usize = 2_048;
const MAX_LABEL_BYTES: usize = 256;
const NODE_FIELDS: [&str; 10] = [
    "label",
    "kind",
    "group_id",
    "status",
    "access",
    "control",
    "detection",
    "functions",
    "physical_binding",
    "visible",
];
const LINK_FIELDS: [&str; 7] = ["source", "target", "label", "kind", "status", "direction", "visible"];
const FUNCTION_FIELDS: [&str; 4] = ["label", "state", "enabled", "visible"];
const GROUP_FIELDS: [&str; 2] = ["label", "visible"];
const PHYSICAL_RENDERERS: [&str; 2] = ["tactical_grid", "coordinate_map"];

/// `NetworkGraphError` (a `ValueError` subclass): a safe rejection of a graph projection or
/// renderer selection.
fn graph_error(message: impl Into<String>) -> DomainError {
    DomainError::Diagnosed {
        error_type: "NetworkGraphError",
        message: message.into(),
        diagnostics: Vec::new(),
    }
}

/// `_small_text(value, label, maximum=...)`: trimmed, nonempty text of bounded UTF-8 size.
fn small_text(value: Option<&Json>, label: &str, maximum: usize) -> DomainResult<String> {
    match value {
        Some(Json::String(text))
            if !text.is_empty() && text == sylvia_base::pystr::strip(text) && text.len() <= maximum =>
        {
            Ok(text.clone())
        }
        _ => Err(graph_error(format!("Network graph {label} is invalid"))),
    }
}

/// `_records(value, label, maximum=...)`.
fn records<'a>(value: Option<&'a Json>, label: &str, maximum: usize) -> DomainResult<&'a JsonMap> {
    match value {
        Some(Json::Object(map)) if map.len() <= maximum => Ok(map),
        _ => Err(graph_error(format!("Network graph {label} are invalid"))),
    }
}

/// `sorted(records.items(), key=lambda item: str(item[0]))`.
fn sorted_items(map: &JsonMap) -> Vec<(&String, &Json)> {
    let mut items: Vec<(&String, &Json)> = map.iter().collect();
    items.sort_by(|a, b| a.0.cmp(b.0));
    items
}

/// `_visible_record(value, fields, label)`: a visible record with only declared fields.
fn visible_record<'a>(value: &'a Json, fields: &[&str], label: &str) -> DomainResult<Option<&'a JsonMap>> {
    let Json::Object(map) = value else { return Ok(None) };
    if map.get("visible") != Some(&Json::Bool(true)) {
        return Ok(None);
    }
    if map.keys().any(|key| !fields.contains(&key.as_str())) {
        return Err(graph_error(format!("Visible network {label} fields are invalid")));
    }
    Ok(Some(map))
}

/// `_optional_text(source, target, field)`.
fn optional_text(source: &JsonMap, target: &mut JsonMap, field: &str) -> DomainResult<()> {
    if let Some(value) = source.get(field).filter(|value| !value.is_null()) {
        target.insert(field.into(), Json::String(small_text(Some(value), field, MAX_LABEL_BYTES)?));
    }
    Ok(())
}

fn project_groups(value: Option<&Json>) -> DomainResult<(Vec<Json>, BTreeSet<String>)> {
    let mut groups = Vec::new();
    let mut visible = BTreeSet::new();
    for (identity, raw) in sorted_items(records(value, "groups", MAX_GRAPH_ITEMS)?) {
        let Some(record) = visible_record(raw, &GROUP_FIELDS, "group")? else { continue };
        let group_id = small_text(Some(&Json::from(identity.as_str())), "group identity", 128)?;
        groups.push(json!({"id": group_id, "label": small_text(record.get("label"), "group label", MAX_LABEL_BYTES)?}));
        visible.insert(group_id);
    }
    Ok((groups, visible))
}

fn project_functions(value: Option<&Json>) -> DomainResult<Vec<Json>> {
    let mut functions = Vec::new();
    for (identity, raw) in sorted_items(records(value, "functions", MAX_FUNCTIONS_PER_NODE)?) {
        let Some(record) = visible_record(raw, &FUNCTION_FIELDS, "function")? else { continue };
        let Some(Json::Bool(enabled)) = record.get("enabled") else {
            return Err(graph_error("Visible network function enabled state is invalid"));
        };
        let mut function = JsonMap::new();
        function.insert(
            "id".into(),
            Json::String(small_text(Some(&Json::from(identity.as_str())), "function identity", 128)?),
        );
        function.insert(
            "label".into(),
            Json::String(small_text(record.get("label"), "function label", MAX_LABEL_BYTES)?),
        );
        function.insert("enabled".into(), Json::Bool(*enabled));
        optional_text(record, &mut function, "state")?;
        functions.push(Json::Object(function));
    }
    Ok(functions)
}

type Physical = BTreeSet<(String, String)>;

fn physical_binding(value: Option<&Json>, visible: &Physical) -> DomainResult<Option<Json>> {
    let Some(value) = value.filter(|value| !value.is_null()) else { return Ok(None) };
    let Json::Object(map) = value else { return Err(graph_error("Network physical binding is invalid")) };
    if map.len() != 2 || !map.contains_key("surface_id") || !map.contains_key("entity_id") {
        return Err(graph_error("Network physical binding is invalid"));
    }
    let surface_id = small_text(map.get("surface_id"), "physical surface identity", 128)?;
    let entity_id = small_text(map.get("entity_id"), "physical entity identity", 128)?;
    if !visible.contains(&(surface_id.clone(), entity_id.clone())) {
        return Ok(None);
    }
    Ok(Some(json!({"surface_id": surface_id, "entity_id": entity_id})))
}

fn project_nodes(
    value: Option<&Json>,
    groups: &BTreeSet<String>,
    physical: &Physical,
) -> DomainResult<(Vec<Json>, BTreeSet<String>)> {
    let mut nodes = Vec::new();
    let mut visible = BTreeSet::new();
    for (identity, raw) in sorted_items(records(value, "nodes", MAX_GRAPH_ITEMS)?) {
        let Some(record) = visible_record(raw, &NODE_FIELDS, "node")? else { continue };
        let node_id = small_text(Some(&Json::from(identity.as_str())), "node identity", 128)?;
        let mut node = JsonMap::new();
        node.insert("id".into(), Json::String(node_id.clone()));
        node.insert(
            "label".into(),
            Json::String(small_text(record.get("label"), "node label", MAX_LABEL_BYTES)?),
        );
        node.insert("kind".into(), Json::String(small_text(record.get("kind"), "node kind", 128)?));
        let empty = Json::Object(JsonMap::new());
        node.insert(
            "functions".into(),
            Json::Array(project_functions(Some(record.get("functions").unwrap_or(&empty)))?),
        );
        if let Some(group_id) = record.get("group_id").filter(|group| !group.is_null()) {
            let group = small_text(Some(group_id), "node group identity", 128)?;
            if groups.contains(&group) {
                node.insert("group_id".into(), Json::String(group));
            }
        }
        for field in ["status", "access", "control", "detection"] {
            optional_text(record, &mut node, field)?;
        }
        if let Some(binding) = physical_binding(record.get("physical_binding"), physical)? {
            node.insert("physical_binding".into(), binding);
        }
        nodes.push(Json::Object(node));
        visible.insert(node_id);
    }
    Ok((nodes, visible))
}

/// `value in a_set_of_text`: `TypeError` for an unhashable value.
fn in_texts(value: &Json, texts: &BTreeSet<String>) -> DomainResult<bool> {
    py_require_hashable(value)?;
    Ok(value.as_str().is_some_and(|text| texts.contains(text)))
}

fn project_links(value: Option<&Json>, visible_nodes: &BTreeSet<String>) -> DomainResult<Vec<Json>> {
    let mut links = Vec::new();
    for (identity, raw) in sorted_items(records(value, "links", MAX_GRAPH_ITEMS)?) {
        let Json::Object(map) = raw else { continue };
        if map.get("visible") != Some(&Json::Bool(true)) {
            continue;
        }
        let null = Json::Null;
        let source = map.get("source").unwrap_or(&null);
        let target = map.get("target").unwrap_or(&null);
        if !in_texts(source, visible_nodes)? || !in_texts(target, visible_nodes)? {
            continue;
        }
        let record = visible_record(raw, &LINK_FIELDS, "link")?.expect("visible, checked above");
        let direction = record.get("direction").cloned().unwrap_or_else(|| json!("bidirectional"));
        py_require_hashable(&direction)?;
        if !matches!(direction.as_str(), Some("directed" | "bidirectional")) {
            return Err(graph_error("Visible network link direction is invalid"));
        }
        let mut link = JsonMap::new();
        link.insert(
            "id".into(),
            Json::String(small_text(Some(&Json::from(identity.as_str())), "link identity", 128)?),
        );
        link.insert("source".into(), source.clone());
        link.insert("target".into(), target.clone());
        link.insert("direction".into(), direction);
        for field in ["label", "kind", "status"] {
            optional_text(record, &mut link, field)?;
        }
        links.push(Json::Object(link));
    }
    Ok(links)
}

fn get<'a>(map: &'a JsonMap, key: &str) -> &'a Json {
    map.get(key).unwrap_or(&Json::Null)
}

/// The `nodes` or `links` list of a projection (`projection.get(name, ())`).
fn listed<'a>(projection: &'a JsonMap, name: &str) -> Vec<&'a JsonMap> {
    projection.get(name).and_then(Json::as_array).into_iter().flatten().filter_map(Json::as_object).collect()
}

/// `_link_by_id(projection, link_id)`.
fn link_by_id<'a>(projection: &'a JsonMap, link_id: &str) -> Option<&'a JsonMap> {
    listed(projection, "links").into_iter().find(|item| py_eq(get(item, "id"), &Json::from(link_id)))
}

fn node_has_enabled_function(node: &JsonMap, function_id: &Json) -> bool {
    node.get("functions").and_then(Json::as_array).into_iter().flatten().filter_map(Json::as_object).any(
        |function| {
            py_eq(get(function, "id"), function_id) && function.get("enabled") == Some(&Json::Bool(true))
        },
    )
}

/// `_selection_value_valid(source, value, projection, action_id=..., controlled_actor_ids=...)`.
fn selection_value_valid(
    source: &str,
    value: &Json,
    projection: &JsonMap,
    action_id: Option<&str>,
    controlled_actor_ids: &BTreeSet<String>,
) -> bool {
    if source == "actor" {
        return value.as_str().is_some_and(|text| controlled_actor_ids.contains(text));
    }
    let nodes = listed(projection, "nodes");
    let node_ids: Vec<&Json> = nodes.iter().map(|item| get(item, "id")).collect();
    let is_node = |text: &str| node_ids.iter().any(|id| py_eq(id, &Json::from(text)));
    match source {
        "node" => value.as_str().is_some_and(is_node),
        "link" => value.as_str().is_some_and(|text| link_by_id(projection, text).is_some()),
        "route_step" => {
            let Some(parts) =
                value.as_array().filter(|parts| parts.len() == 3 && parts.iter().all(Json::is_string))
            else {
                return false;
            };
            let (start, end, link_id) = (
                parts[0].as_str().unwrap_or_default(),
                parts[1].as_str().unwrap_or_default(),
                parts[2].as_str().unwrap_or_default(),
            );
            let Some(link) = link_by_id(projection, link_id) else { return false };
            if !is_node(start) || !is_node(end) {
                return false;
            }
            let (start, end) = (Json::from(start), Json::from(end));
            let direct = py_eq(&start, get(link, "source")) && py_eq(&end, get(link, "target"));
            let reverse = py_eq(get(link, "direction"), &json!("bidirectional"))
                && py_eq(&start, get(link, "target"))
                && py_eq(&end, get(link, "source"));
            direct || reverse
        }
        "remote_function" => {
            value.is_string() && nodes.iter().any(|node| node_has_enabled_function(node, value))
        }
        "declared_action" => action_id.is_some_and(|id| py_eq(value, &Json::from(id))),
        _ => false,
    }
}

fn project_selection(value: Option<&Json>, projection: &JsonMap) -> Json {
    let Some(Json::Object(selection)) = value else { return json!({}) };
    let mut sources: Vec<&str> =
        NETWORK_SELECTION_BINDINGS.iter().copied().filter(|s| *s != "declared_action").collect();
    sources.sort_unstable();
    let mut result = JsonMap::new();
    for source in sources {
        if let Some(selected) = selection.get(source).filter(|selected| !selected.is_null()) {
            if selection_value_valid(source, selected, projection, None, &BTreeSet::new()) {
                result.insert(source.into(), selected.clone());
            }
        }
    }
    Json::Object(result)
}

/// `project_network_graph(value, visible_physical_entities=...)`: only explicitly visible graph
/// topology and safe physical bindings.
pub fn project_network_graph(value: &Json, visible_physical_entities: &Physical) -> DomainResult<JsonMap> {
    let Json::Object(source) = value else {
        return Err(graph_error("Network graph source must be an object"));
    };
    let fields = ["graph_id", "revision", "groups", "nodes", "links", "selection"];
    if source.len() != fields.len() || fields.iter().any(|field| !source.contains_key(*field)) {
        return Err(graph_error("Network graph source fields are invalid"));
    }
    let graph_id = small_text(source.get("graph_id"), "identity", 128)?;
    let revision = match &source["revision"] {
        Json::Number(number) if number.is_u64() => source["revision"].clone(),
        _ => return Err(graph_error("Network graph revision is invalid")),
    };
    let (groups, visible_groups) = project_groups(source.get("groups"))?;
    let (nodes, visible_nodes) =
        project_nodes(source.get("nodes"), &visible_groups, visible_physical_entities)?;
    let functions: usize = nodes.iter().map(|node| node["functions"].as_array().map_or(0, Vec::len)).sum();
    if functions > MAX_VISIBLE_FUNCTIONS {
        return Err(graph_error("Network graph exposes too many functions"));
    }
    let links = project_links(source.get("links"), &visible_nodes)?;
    let mut projection = JsonMap::new();
    projection.insert("format".into(), json!(1));
    projection.insert("graph_id".into(), Json::String(graph_id));
    projection.insert("graph_revision".into(), revision);
    projection.insert("groups".into(), Json::Array(groups));
    projection.insert("nodes".into(), Json::Array(nodes));
    projection.insert("links".into(), Json::Array(links));
    let selection = project_selection(source.get("selection"), &projection);
    projection.insert("selection".into(), selection);
    Ok(projection)
}

/// `resolve_network_action_parameters(action, selection, projection, controlled_actor_ids=...)`:
/// bind a current visible graph selection to an ordinary package operation.
pub fn resolve_network_action_parameters(
    action: &Json,
    selection: &Json,
    projection: &JsonMap,
    controlled_actor_ids: &BTreeSet<String>,
) -> DomainResult<JsonMap> {
    let (bindings, fixed, action_id) =
        (py_get_attr(action, "bindings")?, py_get_attr(action, "params")?, py_get_attr(action, "id")?);
    let (Some(Json::Object(bindings)), Some(Json::Object(fixed))) = (bindings, fixed) else {
        return Err(graph_error("Network action declaration is invalid"));
    };
    let (Some(Json::String(action_id)), Json::Object(selection)) = (action_id, selection) else {
        return Err(graph_error("Network selection is invalid"));
    };
    if !py_eq(get(selection, "graph_revision"), get(projection, "graph_revision")) {
        return Err(graph_error("Network selection is stale"));
    }
    let values: Vec<Json> = bindings.values().cloned().collect();
    let sources = py_set(&Json::Array(values.clone())).map_err(|_| {
        let unhashable =
            values.iter().find(|value| matches!(value, Json::Array(_) | Json::Object(_))).expect("one is");
        DomainError::type_error(format!("unhashable type: '{}'", py_type_name(unhashable)))
    })?;
    let declared =
        |value: &Json| value.as_str().is_some_and(|text| NETWORK_SELECTION_BINDINGS.contains(&text));
    let mut expected: Vec<&Json> = sources.iter().collect();
    let revision = json!("graph_revision");
    if !expected.iter().any(|value| py_eq(value, &revision)) {
        expected.push(&revision);
    }
    let keys_match = selection.len() == expected.len()
        && expected.iter().all(|value| value.as_str().is_some_and(|key| selection.contains_key(key)));
    if !sources.iter().all(declared) || !keys_match {
        return Err(graph_error("Network selection contains undeclared input"));
    }
    let has = |name: &str| sources.iter().any(|value| value.as_str() == Some(name));
    if has("node") && has("remote_function") {
        let selected_node = get(selection, "node");
        let selected_function = get(selection, "remote_function");
        let node = listed(projection, "nodes").into_iter().find(|item| py_eq(get(item, "id"), selected_node));
        if !node.is_some_and(|node| node_has_enabled_function(node, selected_function)) {
            return Err(graph_error("Network remote_function selection is invalid"));
        }
    }
    let mut parameters: JsonMap = fixed.iter().map(|(key, value)| (key.clone(), value.clone())).collect();
    if parameters.keys().any(|key| bindings.contains_key(key)) {
        return Err(graph_error("Network action binds a fixed parameter"));
    }
    for (parameter, raw_source) in bindings {
        let Json::String(raw_source) = raw_source else {
            return Err(graph_error("Network selection binding is invalid"));
        };
        let selected = get(selection, raw_source);
        if !selection_value_valid(raw_source, selected, projection, Some(action_id), controlled_actor_ids) {
            return Err(graph_error(format!("Network {raw_source} selection is invalid")));
        }
        parameters.insert(parameter.clone(), selected.clone());
    }
    Ok(parameters)
}

/// `surface.get("renderer") in {"tactical_grid", "coordinate_map"}` and `data` is a mapping.
fn physical_surface(surface: &Json) -> DomainResult<bool> {
    let renderer = py_get_attr(surface, "renderer")?.cloned().unwrap_or(Json::Null);
    py_require_hashable(&renderer)?;
    let physical = renderer.as_str().is_some_and(|name| PHYSICAL_RENDERERS.contains(&name));
    Ok(physical && matches!(py_get_attr(surface, "data")?, Some(Json::Object(_))))
}

/// `visible_physical_entity_ids(surfaces)`: active entity identities already admitted to
/// tactical projections.
pub fn visible_physical_entity_ids(surfaces: &[Json]) -> DomainResult<Physical> {
    let mut result = Physical::new();
    for surface in surfaces {
        if !physical_surface(surface)? {
            continue;
        }
        let data = surface["data"].as_object().expect("checked");
        let entities = match data.get("entities") {
            Some(Json::Array(items)) => items.as_slice(),
            _ => &[],
        };
        for entity in entities {
            if let Json::Object(entity) = entity {
                if entity.get("active") == Some(&Json::Bool(true)) {
                    let surface_id = py_str(py_get_attr(surface, "id")?.unwrap_or(&Json::Null));
                    result.insert((surface_id, py_str(get(entity, "id"))));
                }
            }
        }
    }
    Ok(result)
}

/// `bind_physical_network_surfaces(surfaces)`: add reverse navigation using only identities
/// visible on both projected surfaces.
pub fn bind_physical_network_surfaces(surfaces: &[Json]) -> DomainResult<Vec<JsonMap>> {
    let mut copied = Vec::with_capacity(surfaces.len());
    for item in surfaces {
        copied.push(
            item.as_object()
                .cloned()
                .map(Json::Object)
                .ok_or_else(|| DomainError::type_error("Safe payload must be a JSON object"))?,
        );
    }
    let visible = visible_physical_entity_ids(&copied)?;
    // `tactical_entities[key] = entity`: where each visible entity lives, the last one winning.
    let mut tactical: Vec<((String, String), (usize, usize))> = Vec::new();
    for (surface_at, surface) in copied.iter().enumerate() {
        if !physical_surface(surface)? {
            continue;
        }
        let data = &surface["data"];
        let empty = json!([]);
        let entities = py_get_attr(data, "entities")?.unwrap_or(&empty);
        for (entity_at, entity) in py_iter(entities)?.iter().enumerate() {
            let key = (py_str(&surface["id"]), py_str(py_get_attr(entity, "id")?.unwrap_or(&Json::Null)));
            if visible.contains(&key) {
                match tactical.iter_mut().find(|(existing, _)| *existing == key) {
                    Some(entry) => entry.1 = (surface_at, entity_at),
                    None => tactical.push((key, (surface_at, entity_at))),
                }
            }
        }
    }
    for surface_at in 0..copied.len() {
        let surface = &copied[surface_at];
        let renderer = py_get_attr(surface, "renderer")?.cloned().unwrap_or(Json::Null);
        if !py_eq(&renderer, &json!("network_graph"))
            || !matches!(py_get_attr(surface, "data")?, Some(Json::Object(_)))
        {
            continue;
        }
        let empty = json!([]);
        let nodes = py_iter(py_get_attr(&surface["data"], "nodes")?.unwrap_or(&empty))?;
        for (node_at, node) in nodes.iter().enumerate() {
            // A node that is not a dict raises here, so the indexing below only meets dicts in a list.
            let Some(Json::Object(binding)) = py_get_attr(node, "physical_binding")?.cloned() else {
                continue;
            };
            let key = (py_str(get(&binding, "surface_id")), py_str(get(&binding, "entity_id")));
            let Some(&(_, (target_surface, target_entity))) =
                tactical.iter().find(|(existing, _)| *existing == key)
            else {
                copied[surface_at]["data"]["nodes"][node_at]
                    .as_object_mut()
                    .expect("a dict")
                    .shift_remove("physical_binding");
                continue;
            };
            let surface_id = py_str(py_item(&copied[surface_at], "id")?);
            let node_id = py_str(py_item(node, "id")?);
            let entity =
                copied[target_surface]["data"]["entities"][target_entity].as_object_mut().expect("a dict");
            let references = entity.entry("network_bindings").or_insert_with(|| json!([]));
            let Json::Array(references) = references else {
                return Err(DomainError::uncaught(
                    "AttributeError",
                    format!("'{}' object has no attribute 'append'", py_type_name(references)),
                ));
            };
            references.push(json!({"surface_id": surface_id, "node_id": node_id}));
        }
    }
    Ok(copied.into_iter().map(|surface| surface.as_object().cloned().expect("a dict")).collect())
}
