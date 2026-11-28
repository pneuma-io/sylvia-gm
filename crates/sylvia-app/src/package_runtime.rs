//! `application/package_runtime.py`, validation only: `validate_package_runtime`, which every
//! exposed runtime tool must pass before a package candidate is published.
//!
//! The order of checks is Python's, because the first failure decides the message: byte limit,
//! the bounded and unique-key YAML load, the JSON shape, the shared `package_runtime` content
//! schema, the authoring shape, parameter declarations, the runtime descriptor, the
//! `RulesetFile` model, the executable layer, the loaded tables, trigger links, the static call
//! graph, and then each manifest declaration with its player controls, activity label and
//! result card. Python caches parsed runtimes; the cache never changes a result, so there is
//! none here.

use crate::player_actions::{parse_player_controls, PlayerControls};
use crate::result_card_admission::{validate_activity_label, validate_result_card_declaration};
use std::collections::HashSet;
use sylvia_base::pyyaml::{self, Constructor, Data, Hooks, Loader, NodeId, NodeKind, Value, YamlError};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::content::{validate_content_shape, ContentShapeError};
use sylvia_domain::error::RulesetDiagnostic;
use sylvia_domain::package_library::{canonical_json_bytes, ToolDeclaration};
use sylvia_domain::yaml_bounds::{validate_yaml_graph, YamlGraphLimits};
use sylvia_engine::ruleset::authoring_shape::runtime_authoring_shape_errors;
use sylvia_engine::ruleset::model::{check_ruleset_file, ModelRefusal};
use sylvia_engine::ruleset::parameter_declarations::{resolve_parameter_declarations_with, ResolveError};
use sylvia_engine::ruleset::pydata::{py_eq, str_is_alnum, truthy};
use sylvia_engine::ruleset::runtime_tables::{load_runtime_tables, RuntimeTables, ToolDef};
use sylvia_engine::ruleset::{validate_parameter_declarations, validate_ruleset_data};
use sylvia_engine::{PyError, PyErrorKind};

/// `PACKAGE_RUNTIME_PATH`.
pub const PACKAGE_RUNTIME_PATH: &str = "runtime/ruleset.yaml";
/// `PACKAGE_RUNTIME_BYTE_LIMIT`: 16 MiB.
pub const PACKAGE_RUNTIME_BYTE_LIMIT: usize = 16 * 1024 * 1024;
const RUNTIME_FORMAT: i64 = 1;
const MAX_RUNTIME_DEPTH: usize = 64;
const MAX_RUNTIME_NODES: usize = 501_000;
const MAX_OBJECT_MEMBERS: usize = 25_000;
const MERGE_TAG: &str = "tag:yaml.org,2002:merge";

/// `PackageRuntimeError`: an admitted package's executable bytes are invalid or inconsistent.
/// A `ValueError` in Python, with `.diagnostics`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PackageRuntimeError {
    /// `str(error)`.
    pub message: String,
    pub diagnostics: Vec<RulesetDiagnostic>,
}

impl PackageRuntimeError {
    fn new(message: impl Into<String>) -> Self {
        Self { message: message.into(), diagnostics: Vec::new() }
    }

    fn with(message: impl Into<String>, diagnostics: Vec<RulesetDiagnostic>) -> Self {
        Self { message: message.into(), diagnostics }
    }
}

/// What `validate_package_runtime` raises.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PackageRuntimeFailure {
    /// `PackageRuntimeError`.
    Rejected(PackageRuntimeError),
    /// Another exception Python lets through (a `TypeError` from validation, an
    /// `AttributeError` on a malformed table, a `KeyError` from the YAML bool table).
    Uncaught { class: &'static str, message: String },
}

type Outcome<T> = Result<T, PackageRuntimeFailure>;

fn reject<T>(message: impl Into<String>) -> Outcome<T> {
    Err(PackageRuntimeFailure::Rejected(PackageRuntimeError::new(message)))
}

impl From<PyError> for PackageRuntimeFailure {
    fn from(error: PyError) -> Self {
        Self::Uncaught { class: error.kind.name(), message: error.message }
    }
}

impl From<PackageRuntimeError> for PackageRuntimeFailure {
    fn from(error: PackageRuntimeError) -> Self {
        Self::Rejected(error)
    }
}

// ── Loading ────────────────────────────────────────────────────────────────────────────────

const GRAPH_LIMITS: YamlGraphLimits = YamlGraphLimits {
    maximum_depth: MAX_RUNTIME_DEPTH,
    maximum_nodes: MAX_RUNTIME_NODES,
    maximum_object_members: Some(MAX_OBJECT_MEMBERS),
    count_mapping_keys: false,
};

/// `_BoundedRuntimeLoader.construct_document`: check the source graph before construction.
fn bounded_document(constructor: &mut Constructor, root: NodeId) -> Result<Option<Value>, YamlError> {
    validate_yaml_graph(constructor.graph, root, GRAPH_LIMITS)?;
    constructor.base_construct_document(root)
}

/// `_UniqueRuntimeLoader.flatten_mapping`: every authored key is unique text, before merges.
fn unique_keys_flatten(constructor: &mut Constructor, node: NodeId) -> Result<(), YamlError> {
    if !constructor.flagged.contains(&node) {
        let NodeKind::Mapping(pairs) = constructor.graph.node(node).kind.clone() else {
            unreachable!("flatten_mapping is called on mappings")
        };
        let mut seen: Vec<String> = Vec::new();
        for (key_node, _) in pairs {
            let key = if constructor.graph.node(key_node).tag == MERGE_TAG {
                Value::Str("<<".into())
            } else {
                constructor.construct_object(key_node, false)?
            };
            match key {
                Value::Str(text) if !seen.contains(&text) => seen.push(text),
                _ => {
                    return Err(YamlError::Python {
                        class: "ValueError",
                        message: "Runtime fields must be unique text keys".into(),
                    })
                }
            }
        }
        constructor.flagged.insert(node);
    }
    constructor.base_flatten_mapping(node)
}

/// `yaml.load(runtime_bytes.decode("utf-8"), Loader=_BoundedRuntimeLoader)`: the raw runtime
/// document as the execution loader builds it, the graph checked first, for readers that need
/// the authored tables rather than [`RuntimeTables`] (`AuthorityOwner.inspect_package_candidate`).
pub fn load_bounded_runtime_yaml(runtime_bytes: &[u8]) -> Result<Data, PackageRuntimeFailure> {
    load_yaml(runtime_bytes, false)
}

/// `yaml.load(text, Loader=_UniqueRuntimeLoader)` with `_load_runtime`'s exception handling.
/// `yaml.load(text, Loader=_UniqueRuntimeLoader if authoring else _BoundedRuntimeLoader)`: both
/// check the node graph first; only the authoring loader refuses duplicate keys.
fn load_yaml(runtime_bytes: &[u8], authoring: bool) -> Outcome<Data> {
    const INVALID: &str = "Package runtime YAML is invalid";
    let Ok(text) = std::str::from_utf8(runtime_bytes) else {
        return reject(INVALID);
    };
    let hooks = Hooks {
        construct_document: Some(bounded_document),
        flatten_mapping: if authoring { Some(unique_keys_flatten) } else { None },
        ..Hooks::default()
    };
    let loaded = match pyyaml::load(text, Loader::C, hooks) {
        Ok(loaded) => loaded,
        Err(YamlError::Python { class: "YamlShapeError", message }) => return reject(message),
        Err(error) if error.is_yaml_error() => return reject(INVALID),
        Err(YamlError::Python { class, message }) => {
            if matches!(class, "ValueError" | "TypeError" | "RecursionError" | "UnicodeDecodeError") {
                return reject(INVALID);
            }
            return Err(PackageRuntimeFailure::Uncaught { class, message });
        }
        Err(_) => return reject(INVALID),
    };
    // The graph check refused recursive aliases before construction, so the value is a tree.
    Ok(loaded
        .to_data()
        .map_err(|_| PackageRuntimeError::new("Package runtime contains a recursive alias"))?
        .unwrap_or(Data::None))
}

/// `_validate_shape(value)`: depth, node count, member count, text keys and JSON values, in
/// Python's visiting order (each key is checked just before its value is visited). `Data` is a
/// tree, so the recursive-alias case cannot arise.
fn validate_shape(value: &Data) -> Result<(), PackageRuntimeError> {
    fn visit(item: &Data, depth: usize, nodes: &mut usize) -> Result<(), PackageRuntimeError> {
        if depth > MAX_RUNTIME_DEPTH {
            return Err(PackageRuntimeError::new("Package runtime nesting is too deep"));
        }
        *nodes += 1;
        if *nodes > MAX_RUNTIME_NODES {
            return Err(PackageRuntimeError::new("Package runtime has too many nodes"));
        }
        match item {
            Data::None | Data::Str(_) | Data::Bool(_) | Data::Int(_) => Ok(()),
            Data::Dict(pairs) => {
                if pairs.len() > MAX_OBJECT_MEMBERS {
                    return Err(PackageRuntimeError::new("Package runtime object is too large"));
                }
                for (key, child) in pairs {
                    if !matches!(key, Data::Str(_)) {
                        return Err(PackageRuntimeError::new("Package runtime keys must be strings"));
                    }
                    visit(child, depth + 1, nodes)?;
                }
                Ok(())
            }
            Data::List(items) | Data::Tuple(items) => {
                for child in items {
                    visit(child, depth + 1, nodes)?;
                }
                Ok(())
            }
            Data::Float(number) if number.is_finite() => Ok(()),
            Data::Float(_) => Err(PackageRuntimeError::new("Package runtime contains a non-finite number")),
            Data::Bytes(_) | Data::Date(_) | Data::DateTime(_) | Data::Set(_) => {
                Err(PackageRuntimeError::new("Package runtime contains a non-JSON value"))
            }
        }
    }
    visit(value, 1, &mut 0)
}

/// A value already known to be JSON (it passed [`validate_shape`]).
fn json_of(value: &Data) -> Json {
    value.to_loaded().to_json().expect("shape-checked values are JSON")
}

// ── Static links between effects ───────────────────────────────────────────────────────────

/// What `for effect in effects` followed by `"name" in effect` and `effect.get(...)` raises for
/// an item that is not a mapping (`steps` the shared schema did not constrain).
fn non_mapping_effect(item: &Data, handler: &str) -> PyError {
    let type_name = sylvia_engine::ruleset::pydata::type_name(item);
    match item {
        Data::Str(text) if text.contains(handler) => {
            PyError::type_error("string indices must be integers, not 'str'")
        }
        Data::List(items) | Data::Tuple(items)
            if items.iter().any(|x| py_eq(x, &Data::Str(handler.into()))) =>
        {
            PyError::type_error(format!("{type_name} indices must be integers or slices, not str"))
        }
        Data::Str(_) | Data::List(_) | Data::Tuple(_) | Data::Bytes(_) | Data::Set(_) => {
            PyError::of(PyErrorKind::AttributeError, format!("'{type_name}' object has no attribute 'get'"))
        }
        _ => PyError::type_error(format!("argument of type '{type_name}' is not iterable")),
    }
}

/// The items `for effect in effects` visits: a list's items, a mapping's keys, a text's
/// characters. (`effects` and `steps` are `... or []`, so `None` never arrives here.)
fn effect_items(effects: &Data) -> Result<Vec<Data>, PyError> {
    match effects {
        Data::List(items) | Data::Tuple(items) => Ok(items.clone()),
        Data::Dict(pairs) => Ok(pairs.iter().map(|(key, _)| key.clone()).collect()),
        Data::Str(text) => Ok(text.chars().map(|c| Data::Str(c.to_string())).collect()),
        Data::None => Ok(Vec::new()),
        other => Err(PyError::type_error(format!(
            "'{}' object is not iterable",
            sylvia_engine::ruleset::pydata::type_name(other)
        ))),
    }
}

/// A nested list that `_trigger_names` and `_call_names` follow: a list of mappings only.
fn mapping_list(value: Option<&Data>) -> Option<&Data> {
    match value {
        Some(list @ Data::List(items)) if items.iter().all(|item| matches!(item, Data::Dict(_))) => {
            Some(list)
        }
        _ => None,
    }
}

/// `_trigger_names(effects)` (handler `"trigger"`, name from `name` then `trigger`) and
/// `_call_names(effects)` (handler `"call"`, name from `call` then `name`, which must also be
/// alphanumeric with underscores). Python's generators are lazy, so each name goes to `sink`
/// as it is found: a consumer that stops at an unknown name does so before later names are read.
fn linked_names(
    effects: &Data,
    handler: &str,
    keys: [&str; 2],
    alnum: bool,
    invalid: &str,
    sink: &mut dyn FnMut(String) -> Outcome<()>,
) -> Outcome<()> {
    for effect in effect_items(effects)? {
        let Data::Dict(pairs) = &effect else {
            return Err(non_mapping_effect(&effect, handler).into());
        };
        if let Some(body) = effect.get_str(handler) {
            let name = match body {
                Data::Str(text) => (!text.is_empty()).then(|| body.clone()),
                Data::Dict(_) => body.get_str(keys[0]).or_else(|| body.get_str(keys[1])).cloned(),
                _ => None,
            };
            match name {
                Some(Data::Str(name))
                    if !name.is_empty() && (!alnum || str_is_alnum(&name.replace('_', "a"))) =>
                {
                    sink(name)?
                }
                _ => return reject(invalid),
            }
        }
        for branch in ["on_true", "on_false", "do"] {
            if let Some(nested) = mapping_list(effect.get_str(branch)) {
                linked_names(nested, handler, keys, alnum, invalid, sink)?;
            }
        }
        for (_, body) in pairs {
            if !matches!(body, Data::Dict(_)) {
                continue;
            }
            for branch in ["on_true", "on_false", "do", "consequence"] {
                if let Some(nested) = mapping_list(body.get_str(branch)) {
                    linked_names(nested, handler, keys, alnum, invalid, sink)?;
                }
            }
        }
    }
    Ok(())
}

fn trigger_names(effects: &Data, sink: &mut dyn FnMut(String) -> Outcome<()>) -> Outcome<()> {
    linked_names(
        effects,
        "trigger",
        ["name", "trigger"],
        false,
        "Package runtime trigger identity is invalid",
        sink,
    )
}

fn call_names(effects: &Data, sink: &mut dyn FnMut(String) -> Outcome<()>) -> Outcome<()> {
    linked_names(
        effects,
        "call",
        ["call", "name"],
        true,
        "Package runtime static call identity is invalid",
        sink,
    )
}

/// Every name, as `set(generator)` or `referenced.update(generator)` consumes it.
/// A name sink: [`linked_names`] hands it each name as Python's generator yields it.
type Sink<'a> = &'a mut dyn FnMut(String) -> Outcome<()>;

fn all_names(walk: fn(&Data, Sink) -> Outcome<()>, effects: &Data) -> Outcome<Vec<String>> {
    let mut names = Vec::new();
    walk(effects, &mut |name| {
        names.push(name);
        Ok(())
    })?;
    Ok(names)
}

/// `_validate_trigger_links(runtime)`: every referenced trigger has a procedure.
fn validate_trigger_links(runtime: &RuntimeTables) -> Outcome<()> {
    let available: Vec<&Data> = runtime.procedures.iter().map(|(_, procedure)| &procedure.trigger).collect();
    let mut referenced = Vec::new();
    for (_, tool) in &runtime.tools {
        referenced.extend(all_names(trigger_names, &tool.effects)?);
    }
    for (_, procedure) in &runtime.procedures {
        referenced.extend(all_names(trigger_names, &procedure.steps)?);
    }
    let linked = |name: &String| available.iter().any(|trigger| matches!(trigger, Data::Str(t) if t == name));
    if referenced.iter().all(linked) {
        Ok(())
    } else {
        reject("Package runtime trigger has no matching procedure")
    }
}

/// `_validate_static_call_graph(runtime)`: known targets and no cycle. Python walks each
/// tool's targets in `set` order; Rust walks them sorted (`differences.md`).
fn validate_static_call_graph(runtime: &RuntimeTables) -> Outcome<()> {
    let tool_name = |key: &Data| match key {
        Data::Str(text) => Some(text.clone()),
        _ => None,
    };
    let names: Vec<Option<String>> = runtime.tools.iter().map(|(key, _)| tool_name(key)).collect();
    let known = |target: &str| names.iter().any(|name| name.as_deref() == Some(target));
    let mut graph: Vec<(usize, Vec<String>)> = Vec::new();
    for (index, (_, tool)) in runtime.tools.iter().enumerate() {
        let mut targets = all_names(call_names, &tool.effects)?;
        targets.sort();
        targets.dedup();
        graph.push((index, targets));
    }
    for (_, procedure) in &runtime.procedures {
        call_names(&procedure.steps, &mut |target| {
            if known(&target) {
                Ok(())
            } else {
                reject(format!("Package runtime static call references unknown tool '{target}'"))
            }
        })?;
    }
    for (index, targets) in &graph {
        if let Some(unknown) = targets.iter().find(|target| !known(target)) {
            let source = sylvia_engine::ruleset::pydata::to_str(&runtime.tools[*index].0);
            return reject(format!(
                "Package runtime static call from '{source}' references unknown tool '{unknown}'"
            ));
        }
    }
    let position =
        |target: &str| names.iter().position(|name| name.as_deref() == Some(target)).expect("known");
    let mut visiting: HashSet<usize> = HashSet::new();
    let mut visited: HashSet<usize> = HashSet::new();
    fn visit(
        node: usize,
        graph: &[(usize, Vec<String>)],
        position: &dyn Fn(&str) -> usize,
        runtime: &RuntimeTables,
        visiting: &mut HashSet<usize>,
        visited: &mut HashSet<usize>,
    ) -> Outcome<()> {
        if visiting.contains(&node) {
            let name = sylvia_engine::ruleset::pydata::to_str(&runtime.tools[node].0);
            return reject(format!("Package runtime static call cycle includes '{name}'"));
        }
        if visited.contains(&node) {
            return Ok(());
        }
        visiting.insert(node);
        for target in &graph[node].1 {
            visit(position(target), graph, position, runtime, visiting, visited)?;
        }
        visiting.remove(&node);
        visited.insert(node);
        Ok(())
    }
    for (index, _) in &graph {
        visit(*index, &graph, &position, runtime, &mut visiting, &mut visited)?;
    }
    Ok(())
}

// ── Manifest declarations ──────────────────────────────────────────────────────────────────

/// `list(value or [])` for `enum_values`.
fn listed(value: &Data) -> Outcome<Json> {
    if !truthy(value) {
        return Ok(Json::Array(Vec::new()));
    }
    Ok(Json::Array(match value {
        Data::List(items) | Data::Tuple(items) => items.iter().map(json_of).collect(),
        Data::Dict(pairs) => pairs.iter().map(|(key, _)| json_of(key)).collect(),
        Data::Str(text) => text.chars().map(|c| Json::String(c.to_string())).collect(),
        other => {
            return Err(PyError::type_error(format!(
                "'{}' object is not iterable",
                sylvia_engine::ruleset::pydata::type_name(other)
            ))
            .into())
        }
    }))
}

fn is_text(value: &Data, text: &str) -> bool {
    matches!(value, Data::Str(value) if value == text)
}

/// `_runtime_parameter_schema(tool)`. `None` when a parameter name is not text, which no
/// manifest's text-keyed schema can then agree with.
fn runtime_parameter_schema(tool: &ToolDef) -> Outcome<Option<(JsonMap, Vec<String>)>> {
    let mut properties = JsonMap::new();
    let mut required = Vec::new();
    for parameter in &tool.parameters {
        if !matches!(parameter.from_state, Data::None) {
            continue;
        }
        let kind = &parameter.kind;
        let mut child = JsonMap::new();
        if is_text(kind, "enum") {
            child.insert("type".into(), Json::String("string".into()));
            child.insert("enum".into(), listed(&parameter.enum_values)?);
        } else if is_text(kind, "entity_id") || is_text(kind, "string") {
            child.insert("type".into(), Json::String("string".into()));
        } else if is_text(kind, "entity_id_list") {
            child.insert("type".into(), Json::String("array".into()));
            child.insert("items".into(), serde_json::json!({"type": "string"}));
        } else {
            child.insert("type".into(), json_of(kind));
        }
        if is_text(kind, "array") && !matches!(parameter.items, Data::None) {
            child.insert("items".into(), json_of(&parameter.items));
        }
        if is_text(&parameter.resolve, "participant") {
            child.insert("x-sylvia-gm-reference".into(), Json::String("participant".into()));
        } else if is_text(&parameter.resolve, "entity") {
            child.insert("x-sylvia-gm-reference".into(), Json::String("entity".into()));
        } else if is_text(&parameter.resolve, "entity_list") {
            if let Some(Json::Object(items)) = child.get_mut("items") {
                items.insert("x-sylvia-gm-reference".into(), Json::String("entity".into()));
            }
        }
        if truthy(&parameter.description) {
            child.insert("description".into(), json_of(&parameter.description));
        }
        for (field, value) in [
            ("minimum", &parameter.minimum),
            ("maximum", &parameter.maximum),
            ("minItems", &parameter.min_items),
            ("maxItems", &parameter.max_items),
            ("uniqueItems", &parameter.unique_items),
        ] {
            if !matches!(value, Data::None) {
                child.insert(field.into(), json_of(value));
            }
        }
        let Data::Str(name) = &parameter.name else {
            return Ok(None);
        };
        properties.insert(name.clone(), Json::Object(child));
        if truthy(&parameter.required) {
            required.push(name.clone());
        }
    }
    Ok(Some((properties, required)))
}

/// `set(values)` compared as Python compares sets of JSON scalars.
fn same_set(a: &[Json], b: &[Json]) -> bool {
    let contains =
        |set: &[Json], item: &Json| set.iter().any(|other| sylvia_domain::pyops::py_eq(other, item));
    a.iter().all(|item| contains(b, item)) && b.iter().all(|item| contains(a, item))
}

/// `set(sequence)` of a declared `required`: a list's items or a text's characters.
fn required_items(value: Option<&Json>) -> Outcome<Vec<Json>> {
    match value {
        None => Ok(Vec::new()),
        Some(Json::Array(items)) => {
            if let Some(item) = items.iter().find(|item| item.is_array() || item.is_object()) {
                // A declaration's input schema is an owned payload, so its containers are frozen.
                let kind = if item.is_array() { "FrozenJsonList" } else { "FrozenJsonDict" };
                return Err(PyError::type_error(format!("unhashable type: '{kind}'")).into());
            }
            Ok(items.clone())
        }
        Some(Json::String(text)) => Ok(text.chars().map(|c| Json::String(c.to_string())).collect()),
        Some(Json::Object(map)) => Ok(map.keys().map(|key| Json::String(key.clone())).collect()),
        Some(other) => {
            let kind = match other {
                Json::Bool(_) => "bool",
                Json::Number(number) if number.is_f64() => "float",
                Json::Number(_) => "int",
                _ => "NoneType",
            };
            Err(PyError::type_error(format!("'{kind}' object is not iterable")).into())
        }
    }
}

/// `_schemas_agree(runtime_schema, declaration)`.
fn schemas_agree(runtime: Option<(JsonMap, Vec<String>)>, declaration: &ToolDeclaration) -> Outcome<bool> {
    let declared = &declaration.input_schema;
    if declared.get("type") != Some(&Json::String("object".into()))
        || declared.get("additionalProperties") != Some(&Json::Bool(false))
    {
        return Ok(false);
    }
    let Some((runtime_properties, runtime_required)) = runtime else {
        return Ok(false);
    };
    let Some(Json::Object(declared_properties)) = declared.get("properties") else {
        return Ok(false);
    };
    if runtime_properties.len() != declared_properties.len()
        || runtime_properties.keys().any(|key| !declared_properties.contains_key(key))
    {
        return Ok(false);
    }
    let runtime_required: Vec<Json> = runtime_required.into_iter().map(Json::String).collect();
    if !same_set(&runtime_required, &required_items(declared.get("required"))?) {
        return Ok(false);
    }
    for (name, expected) in &runtime_properties {
        let (Json::Object(expected), Json::Object(actual)) = (expected, &declared_properties[name]) else {
            return Ok(false);
        };
        for field in [
            "type",
            "enum",
            "items",
            "minimum",
            "maximum",
            "minItems",
            "maxItems",
            "uniqueItems",
            "x-sylvia-gm-reference",
        ] {
            match (expected.get(field), actual.get(field)) {
                (None, None) => {}
                (Some(a), Some(b)) if canonical_json_bytes(a) == canonical_json_bytes(b) => {}
                _ => return Ok(false),
            }
        }
    }
    Ok(true)
}

fn shape_error(message: &str, error: ContentShapeError) -> PackageRuntimeFailure {
    PackageRuntimeFailure::Rejected(PackageRuntimeError::with(message, error.diagnostics))
}

// ── The whole check ────────────────────────────────────────────────────────────────────────

/// `_load_runtime(package_id=..., runtime_bytes=..., authoring=...)`. The execution load
/// (`authoring=False`) skips the content schema and the authoring shape checks.
fn load_runtime(package_id: &str, runtime_bytes: &[u8], authoring: bool) -> Outcome<RuntimeTables> {
    if runtime_bytes.len() > PACKAGE_RUNTIME_BYTE_LIMIT {
        return reject("Package runtime exceeds the byte limit");
    }
    let raw = load_yaml(runtime_bytes, authoring)?;
    let Data::Dict(raw_pairs) = &raw else {
        return reject("Package runtime must be an object");
    };
    validate_shape(&raw)?;
    if authoring {
        validate_content_shape("package_runtime", &json_of(&raw), Some(PACKAGE_RUNTIME_PATH))
            .map_err(|error| shape_error("Package runtime fields are invalid", error))?;
        let executable_errors = runtime_authoring_shape_errors(&raw)?;
        if !executable_errors.is_empty() {
            let diagnostics = executable_errors
                .into_iter()
                .map(|(path, message)| RulesetDiagnostic {
                    code: "runtime_effect_field_invalid".into(),
                    phase: "runtime".into(),
                    message,
                    file: Some(PACKAGE_RUNTIME_PATH.into()),
                    path: Some(path),
                    package_id: None,
                    item_id: None,
                    expected: None,
                    actual: None,
                    guidance: Some("Read the package_runtime executable definition for this field.".into()),
                })
                .collect();
            return Err(
                PackageRuntimeError::with("Package runtime effect fields are invalid", diagnostics).into()
            );
        }
    }
    if !validate_parameter_declarations(raw_pairs)?.is_empty() {
        return reject("Package runtime parameter declarations are invalid");
    }
    let resolved =
        match resolve_parameter_declarations_with(raw_pairs, &mut |value: &Data| validate_shape(value)) {
            Ok(resolved) => resolved,
            Err(ResolveError::Declaration(error)) => return reject(error.message),
            Err(ResolveError::Shape(error)) => return Err(error.into()),
        };
    let resolved_data = Data::Dict(resolved.clone());
    let descriptor_ok = matches!(resolved_data.get_str("sylvia_gm_runtime"), Some(Data::Dict(pairs))
        if pairs.len() == 2
        && resolved_data.get_str("sylvia_gm_runtime").and_then(|d| d.get_str("format")).is_some()
        && resolved_data.get_str("sylvia_gm_runtime").and_then(|d| d.get_str("state_namespace")).is_some());
    if !descriptor_ok {
        return reject("Package runtime descriptor is invalid");
    }
    let descriptor = resolved_data.get_str("sylvia_gm_runtime").expect("checked above");
    let format = descriptor.get_str("format").expect("checked above");
    let namespace = descriptor.get_str("state_namespace").expect("checked above");
    if !py_eq(format, &Data::Int(RUNTIME_FORMAT)) || !is_text(namespace, package_id) {
        return reject("Package runtime contract does not match its manifest");
    }
    if resolved_data.get_str("meta").is_some() {
        return reject("Package runtime repeats manifest-owned identity metadata");
    }
    match check_ruleset_file(&json_of(&resolved_data)) {
        Ok(()) => {}
        Err(ModelRefusal::Invalid) => return reject("Package runtime configuration schema is invalid"),
        Err(ModelRefusal::CrossReference) => return reject("Package runtime cross-references are invalid"),
    }
    if !validate_ruleset_data(&resolved_data)?.is_empty() {
        return reject("Package runtime executable layer is invalid");
    }
    let runtime = match load_runtime_tables(&resolved) {
        Ok(runtime) => runtime,
        Err(error)
            if matches!(
                error.kind,
                PyErrorKind::KeyError | PyErrorKind::TypeError | PyErrorKind::ValueError
            ) =>
        {
            return reject("Package runtime executable layer could not load")
        }
        Err(error) => return Err(error.into()),
    };
    validate_trigger_links(&runtime)?;
    validate_static_call_graph(&runtime)?;
    Ok(runtime)
}

/// `validate_package_runtime(package_id=..., declarations=..., runtime_bytes=...)`: validate all
/// exposed runtime tools before package candidate publication.
pub fn validate_package_runtime(
    package_id: &str,
    declarations: &[ToolDeclaration],
    runtime_bytes: &(impl RuntimeBytes + ?Sized),
) -> Result<(), PackageRuntimeFailure> {
    let runtime = cached_parse(package_id, runtime_bytes, true)?;
    let declared: Vec<&str> = declarations.iter().map(|declaration| declaration.name.as_str()).collect();
    let unique: HashSet<&str> = declared.iter().copied().collect();
    if unique.len() != declarations.len() {
        return reject("Package runtime manifest tool identities are duplicated");
    }
    let mut declared_names: Vec<&str> = unique.into_iter().collect();
    declared_names.sort_unstable();
    for declaration in declarations {
        let tool = runtime.tool(&declaration.name);
        let agrees = match tool {
            Some(tool) => schemas_agree(runtime_parameter_schema(tool)?, declaration)?,
            None => false,
        };
        if !agrees {
            return reject("Package runtime tool does not match its manifest declaration");
        }
        let tool = tool.expect("agreeing tools exist");
        let Data::Dict(_) = &tool.metadata else {
            let type_name = sylvia_engine::ruleset::pydata::type_name(&tool.metadata);
            return Err(PackageRuntimeFailure::Uncaught {
                class: "AttributeError",
                message: format!("'{type_name}' object has no attribute 'get'"),
            });
        };
        let metadata = json_of(&tool.metadata);
        let policy = parse_player_controls(metadata.get("player_controls"));
        if policy.preflight.iter().any(|name| runtime.tool(name).is_none()) {
            return reject("Player preflight names an unavailable runtime tool");
        }
        if let Some(label) = metadata.get("activity_label") {
            let path = format!("$.tools.{}.metadata.activity_label", declaration.name);
            validate_activity_label(label, &path)
                .map_err(|error| shape_error("Package activity label is invalid", error))?;
        }
        if let Some(card) = metadata.get("result_card") {
            let path = format!("$.tools.{}.metadata.result_card", declaration.name);
            validate_result_card_declaration(card, &path, Some(&declared_names))
                .map_err(|error| shape_error("Package result-card declaration is invalid", error))?;
        }
    }
    Ok(())
}

/// A package runtime's bytes as a caller holds them: a slice, a `Vec`, or a shared
/// `Arc<Vec<u8>>`. Python keys its parse cache by the bytes object, and a dict lookup takes the
/// same object without comparing its contents. A caller that keeps its bytes in an `Arc` gets
/// that here: a kept entry holding the same `Arc` is found without comparing 14.5 MB.
pub trait RuntimeBytes {
    /// The bytes.
    fn runtime_bytes(&self) -> &[u8];
    /// The shared allocation, when the caller holds one.
    fn shared(&self) -> Option<&std::sync::Arc<Vec<u8>>> {
        None
    }
}

impl RuntimeBytes for [u8] {
    fn runtime_bytes(&self) -> &[u8] {
        self
    }
}

impl RuntimeBytes for Vec<u8> {
    fn runtime_bytes(&self) -> &[u8] {
        self
    }
}

impl RuntimeBytes for std::sync::Arc<Vec<u8>> {
    fn runtime_bytes(&self) -> &[u8] {
        self
    }
    fn shared(&self) -> Option<&std::sync::Arc<Vec<u8>>> {
        Some(self)
    }
}

/// `_parse_runtime(package_id=..., runtime_bytes=...)`, the execution parse.
pub(crate) fn parse_runtime(
    package_id: &str,
    runtime_bytes: &(impl RuntimeBytes + ?Sized),
) -> Outcome<std::sync::Arc<RuntimeTables>> {
    cached_parse(package_id, runtime_bytes, false)
}

/// `_parse_runtime(package_id=..., runtime_bytes=..., authoring=...)`.
///
/// Python keeps the 16 most recently used parsed runtimes in the process, keyed by the package
/// id, the exact bytes and `authoring`. Package validation (`authoring=True`) reads and fills the
/// authoring entries. An execution parse first takes an authoring entry for the same bytes: an
/// authoring parse that succeeds has no duplicate keys, so the execution parse of those bytes
/// builds the same data. An authoring parse never takes an execution entry, since the execution
/// load skips the stricter checks. Neither the cache nor the reuse can change a result.
///
/// This keeps the same bound and the same key. A lookup matches as Python's dict does: the same
/// object first (here the same `Arc`, see [`RuntimeBytes`]), then equal bytes, by length and
/// then by content. It never hashes them: a SHA-256 of SRD Lite's 14.5 MB runtime took about
/// 64 ms on every call. The identity check is sound because an entry holds its own clone of the
/// `Arc`, so that allocation stays alive and unchanged while the entry exists: a caller's `Arc`
/// at the same address is the same bytes. Two different runtimes can never share an entry.
fn cached_parse(
    package_id: &str,
    runtime_bytes: &(impl RuntimeBytes + ?Sized),
    authoring: bool,
) -> Outcome<std::sync::Arc<RuntimeTables>> {
    use std::sync::{Arc, Mutex, OnceLock};
    static PARSED: OnceLock<Mutex<Vec<ParsedRuntime>>> = OnceLock::new();
    let parsed = PARSED.get_or_init(|| Mutex::new(Vec::new()));
    {
        let mut entries = parsed.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        // Execution accepts legacy files; it cannot attest the stricter authoring checks. Python
        // asks for the admitted key first, then its own: one scan for each finds the same entry.
        let admitted = || entries.iter().position(|entry| entry.is(package_id, runtime_bytes, true));
        let found = if authoring {
            admitted()
        } else {
            admitted().or_else(|| entries.iter().position(|entry| entry.is(package_id, runtime_bytes, false)))
        };
        if let Some(index) = found {
            let mut entry = entries.remove(index);
            // Keep the caller's allocation for this key from now on, so its next lookup is the
            // identity match (Python's dict keeps the first object, and compares contents for
            // every other one). The bytes are equal, so the key is unchanged.
            if let Some(shared) = runtime_bytes.shared() {
                if !Arc::ptr_eq(shared, &entry.bytes) {
                    entry.bytes = Arc::clone(shared);
                }
            }
            let runtime = entry.runtime.clone();
            entries.push(entry);
            return Ok(runtime);
        }
    }
    // A failed parse is not kept, as Python keeps only parsed runtimes.
    let runtime = Arc::new(load_runtime(package_id, runtime_bytes.runtime_bytes(), authoring)?);
    let mut entries = parsed.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
    entries.retain(|entry| !entry.is(package_id, runtime_bytes, authoring));
    entries.push(ParsedRuntime {
        package_id: package_id.to_owned(),
        // The caller's own allocation when it shares one, as Python's key holds the caller's
        // bytes object; otherwise a copy.
        bytes: runtime_bytes
            .shared()
            .cloned()
            .unwrap_or_else(|| Arc::new(runtime_bytes.runtime_bytes().to_vec())),
        authoring,
        runtime: runtime.clone(),
    });
    while entries.len() > PARSED_RUNTIME_LIMIT {
        entries.remove(0);
    }
    Ok(runtime)
}

/// One kept parse and its key: the package id, the exact bytes (kept alive here, as Python's key
/// holds the bytes object) and whether it was the authoring parse.
struct ParsedRuntime {
    package_id: String,
    bytes: std::sync::Arc<Vec<u8>>,
    authoring: bool,
    runtime: std::sync::Arc<RuntimeTables>,
}

impl ParsedRuntime {
    /// Whether this entry's key is `(package_id, runtime_bytes, authoring)`.
    fn is(&self, package_id: &str, runtime_bytes: &(impl RuntimeBytes + ?Sized), authoring: bool) -> bool {
        if self.authoring != authoring || self.package_id != package_id {
            return false;
        }
        if runtime_bytes.shared().is_some_and(|shared| std::sync::Arc::ptr_eq(shared, &self.bytes)) {
            return true;
        }
        let bytes = runtime_bytes.runtime_bytes();
        self.bytes.len() == bytes.len() && self.bytes[..] == *bytes
    }
}

/// `_PARSED_RUNTIMES`' bound: at most this many parsed runtimes stay in the process.
const PARSED_RUNTIME_LIMIT: usize = 16;

/// What the Ruleset test worker reads of the primary package's runtime
/// (`interfaces/ruleset_test_worker.py` `run_archive`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RulesetTestRuntimeFacts {
    /// `{name for name in required if runtime.tools[name].pre_conditions}`, in `required`'s order.
    pub required_preconditions: Vec<String>,
    /// `runtime.model_decision_scope is not None`: the Ruleset declares a `model_decision_scope`
    /// object (an empty one counts; no fallback to the boundary).
    pub requires_handoff: bool,
}

/// `ruleset_test_runtime_facts(package_id, runtime_bytes, required)`: the cached
/// `_parse_runtime`, then which required tools have pre-conditions (Python's truthiness) and
/// whether the Ruleset needs a decision handoff. A required name the runtime lacks is
/// `runtime.tools[name]`'s `KeyError`, for the first missing name in `required`'s order.
pub fn ruleset_test_runtime_facts(
    package_id: &str,
    runtime_bytes: &(impl RuntimeBytes + ?Sized),
    required: &[&str],
) -> Result<RulesetTestRuntimeFacts, PackageRuntimeFailure> {
    let runtime = parse_runtime(package_id, runtime_bytes)?;
    let mut required_preconditions = Vec::new();
    for name in required {
        let Some(tool) = runtime.tool(name) else {
            return Err(PackageRuntimeFailure::Uncaught {
                class: "KeyError",
                message: sylvia_base::pystr::repr(name),
            });
        };
        if sylvia_engine::ruleset::pydata::truthy(&tool.pre_conditions) {
            required_preconditions.push((*name).to_owned());
        }
    }
    let requires_handoff = runtime.ruleset.iter().any(|(key, value)| {
        matches!(key, Data::Str(text) if text == "model_decision_scope") && matches!(value, Data::Dict(_))
    });
    Ok(RulesetTestRuntimeFacts { required_preconditions, requires_handoff })
}

/// `package_model_decision_boundary(package_id=..., runtime_bytes=...)`: the validated
/// Ruleset-owned active-actor control projection, `model_decision_scope or
/// model_decision_boundary`, as mutable JSON. An empty scope falls back to the boundary, and a
/// value that is not an object counts as absent.
pub fn package_model_decision_boundary(
    package_id: &str,
    runtime_bytes: &(impl RuntimeBytes + ?Sized),
) -> Result<Option<JsonMap>, PackageRuntimeFailure> {
    let runtime = parse_runtime(package_id, runtime_bytes)?;
    let declared = |name: &str| {
        runtime.ruleset.iter().find_map(|(key, value)| match (key, value) {
            (Data::Str(key), Data::Dict(_)) if key == name => Some(value),
            _ => None,
        })
    };
    let chosen = match declared("model_decision_scope") {
        Some(Data::Dict(pairs)) if !pairs.is_empty() => declared("model_decision_scope"),
        _ => declared("model_decision_boundary"),
    };
    Ok(chosen.map(|value| match json_of(value) {
        Json::Object(map) => map,
        _ => unreachable!("an object"),
    }))
}

/// `_validate_runtime_declaration(runtime, declaration)`: the runtime has the declared tool and
/// its parameters agree with the declared schema.
pub(crate) fn validate_runtime_declaration<'r>(
    runtime: &'r RuntimeTables,
    declaration: &ToolDeclaration,
) -> Outcome<&'r ToolDef> {
    match runtime.tool(&declaration.name) {
        Some(tool) if schemas_agree(runtime_parameter_schema(tool)?, declaration)? => Ok(tool),
        _ => reject("Package runtime tool does not match its manifest declaration"),
    }
}

/// `runtime.tools[name]`: an undeclared tool is Python's `KeyError`.
fn runtime_tool<'r>(runtime: &'r RuntimeTables, name: &str) -> Outcome<&'r ToolDef> {
    runtime
        .tool(name)
        .ok_or_else(|| PackageRuntimeFailure::Uncaught { class: "KeyError", message: format!("'{name}'") })
}

/// `tool.metadata`, which Python reads with `.get`: anything but a mapping is its `AttributeError`.
fn tool_metadata(tool: &ToolDef) -> Outcome<JsonMap> {
    match json_of(&tool.metadata) {
        Json::Object(map) => Ok(map),
        _ => Err(PackageRuntimeFailure::Uncaught {
            class: "AttributeError",
            message: format!(
                "'{}' object has no attribute 'get'",
                sylvia_engine::ruleset::pydata::type_name(&tool.metadata)
            ),
        }),
    }
}

/// `package_tool_permanently_refused(...)`: the tool is in the runtime's literal-false refusal set.
pub fn package_tool_permanently_refused(
    package_id: &str,
    declaration: &ToolDeclaration,
    runtime_bytes: &(impl RuntimeBytes + ?Sized),
) -> Result<bool, PackageRuntimeFailure> {
    let runtime = parse_runtime(package_id, runtime_bytes)?;
    Ok(runtime
        .permanently_refused
        .iter()
        .any(|name| matches!(name, Data::Str(text) if *text == declaration.name)))
}

/// `package_tool_description(...)`: the package-authored model-facing purpose,
/// `str(description).strip()`.
pub fn package_tool_description(
    package_id: &str,
    declaration: &ToolDeclaration,
    runtime_bytes: &(impl RuntimeBytes + ?Sized),
) -> Result<String, PackageRuntimeFailure> {
    let runtime = parse_runtime(package_id, runtime_bytes)?;
    let tool = validate_runtime_declaration(&runtime, declaration)?;
    let text = sylvia_domain::pyops::py_str(&json_of(&tool.description));
    Ok(sylvia_base::pystr::strip(&text).to_owned())
}

/// `package_tool_model_behavior(...)`: the Ruleset-authored model behavior and its state
/// predicates. A malformed declaration is the `PackageRuntimeError` Python raises.
pub fn package_tool_model_behavior(
    package_id: &str,
    declaration: &ToolDeclaration,
    runtime_bytes: &(impl RuntimeBytes + ?Sized),
) -> Result<
    (sylvia_domain::authority::ModelToolBehavior, Vec<sylvia_domain::package_library::StatePredicate>),
    PackageRuntimeFailure,
> {
    use sylvia_domain::pyops::{py_str, py_truthy};
    let runtime = parse_runtime(package_id, runtime_bytes)?;
    let tool = validate_runtime_declaration(&runtime, declaration)?;
    let metadata = tool_metadata(tool)?;
    let unsupported = || reject("Package runtime model behavior is unsupported");
    let mut raw = metadata.get("model_behavior").cloned().unwrap_or_else(|| Json::from("optional"));
    let mut raw_predicates = Json::Array(Vec::new());
    if let Json::Object(map) = &raw {
        raw_predicates = map.get("state_predicates").cloned().unwrap_or_else(|| Json::Array(Vec::new()));
        raw = map.get("kind").cloned().unwrap_or(Json::Null);
    }
    let Json::String(kind) = &raw else { return unsupported() };
    let Json::Array(items) = &raw_predicates else { return unsupported() };
    let Some(behavior) = sylvia_domain::authority::ModelToolBehavior::parse(kind) else {
        return unsupported();
    };
    let mut predicates = Vec::new();
    for item in items {
        let Json::Object(predicate) = item else { continue };
        let (Some(namespace), Some(key), Some(equals)) =
            (predicate.get("namespace"), predicate.get("key"), predicate.get("equals"))
        else {
            return unsupported();
        };
        predicates.push(sylvia_domain::package_library::StatePredicate {
            namespace: py_str(namespace),
            key: py_str(key),
            equals: equals.clone(),
            negated: predicate.get("negated").is_some_and(py_truthy),
        });
    }
    Ok((behavior, predicates))
}

/// `package_tool_decision_title(...)`: an authored result-card title of 1 to 256 characters,
/// stripped, or "".
pub fn package_tool_decision_title(
    package_id: &str,
    declaration: &ToolDeclaration,
    runtime_bytes: &(impl RuntimeBytes + ?Sized),
) -> Result<String, PackageRuntimeFailure> {
    let runtime = parse_runtime(package_id, runtime_bytes)?;
    let tool = validate_runtime_declaration(&runtime, declaration)?;
    let metadata = tool_metadata(tool)?;
    let title = match metadata.get("result_card") {
        Some(Json::Object(card)) => card.get("title"),
        _ => None,
    };
    Ok(match title {
        Some(Json::String(title)) if (1..=256).contains(&title.chars().count()) => {
            sylvia_base::pystr::strip(title).to_owned()
        }
        _ => String::new(),
    })
}

/// `package_tool_player_controls(...)`: the direct-input policy from the same bytes. Python reads
/// the tool without checking the declaration, so an undeclared tool is its `KeyError`.
pub fn package_tool_player_controls(
    package_id: &str,
    declaration: &ToolDeclaration,
    runtime_bytes: &(impl RuntimeBytes + ?Sized),
) -> Result<PlayerControls, PackageRuntimeFailure> {
    let runtime = parse_runtime(package_id, runtime_bytes)?;
    let metadata = tool_metadata(runtime_tool(&runtime, &declaration.name)?)?;
    Ok(parse_player_controls(metadata.get("player_controls")))
}

/// A `PackageRuntimeError(message)`.
pub(crate) fn rejection(message: &str) -> PackageRuntimeFailure {
    PackageRuntimeError::new(message).into()
}

/// `runtime.tools[name].metadata`, read with `.get`.
pub(crate) fn tool_metadata_of(runtime: &RuntimeTables, name: &str) -> Outcome<JsonMap> {
    tool_metadata(runtime_tool(runtime, name)?)
}

pub use crate::package_mechanic::{
    materialize_package_mechanic, materialize_player_action_checker, materialize_player_action_previewer,
    MechanicFailure, PackageMechanic, PlayerActionChecker, PlayerActionPreviewer, PortPending,
};

#[cfg(test)]
mod parse_cache_tests {
    use super::*;
    use std::sync::Arc;

    /// A one-tool runtime for `package_id` (its state namespace) whose description is `description`.
    fn runtime(package_id: &str, description: &str) -> Vec<u8> {
        format!(
            "sylvia_gm_runtime:
  format: 1
  state_namespace: {package_id}
tools:
  wave:
    description: {description}
    parameters:
    - name: pressure
      type: integer
      required: true
    effects: []
"
        )
        .into_bytes()
    }

    fn declaration() -> ToolDeclaration {
        let schema = serde_json::json!({
            "additionalProperties": false, "properties": {"pressure": {"type": "integer"}},
            "required": ["pressure"], "type": "object",
        });
        ToolDeclaration {
            name: "wave".into(),
            capability: "test.cache".into(),
            input_schema: schema.as_object().unwrap().clone(),
            state_predicates: Vec::new(),
            turn_statuses: vec!["open".into()],
            writers: vec!["dm".into()],
        }
    }

    fn description(package_id: &str, bytes: &[u8]) -> String {
        package_tool_description(package_id, &declaration(), bytes).expect("a valid runtime")
    }

    #[test]
    fn different_runtimes_of_the_same_length_never_share_an_entry() {
        let package = "test.cache.lengths";
        let (first, second) = (runtime(package, "Wave hello."), runtime(package, "Bow, hello."));
        assert_eq!(first.len(), second.len());
        assert_eq!(description(package, &first), "Wave hello.");
        assert_eq!(description(package, &second), "Bow, hello.");
        assert_eq!(description(package, &first), "Wave hello.");
        assert_eq!(description(package, &second), "Bow, hello.");
    }

    #[test]
    fn the_package_id_is_part_of_the_key() {
        // A runtime names its package's state namespace, so these bytes parse only for their own
        // package: a key without the package id would hand the other package this parse.
        let bytes = runtime("test.cache.owner", "Wave hello.");
        assert!(parse_runtime("test.cache.owner", &bytes).is_ok());
        match parse_runtime("test.cache.other", &bytes) {
            Err(PackageRuntimeFailure::Rejected(error)) => {
                assert_eq!(error.message, "Package runtime contract does not match its manifest");
            }
            Err(other) => panic!("another failure: {other:?}"),
            Ok(_) => panic!("another package's parse was reused"),
        }
    }

    #[test]
    fn equal_bytes_reuse_the_parse() {
        let package = "test.cache.reuse";
        let bytes = runtime(package, "Nod once.");
        let parsed = parse_runtime(package, &bytes).unwrap();
        assert!(Arc::ptr_eq(&parsed, &parse_runtime(package, &bytes).unwrap()));
        // Another allocation of the same bytes is the same key, as Python's bytes equality is.
        let copy = bytes.clone();
        assert!(Arc::ptr_eq(&parsed, &parse_runtime(package, &copy).unwrap()));
    }

    #[test]
    fn a_shared_runtime_is_kept_as_the_callers_own_allocation() {
        let package = "test.cache.shared";
        let bytes = Arc::new(runtime(package, "Shrug."));
        let parsed = parse_runtime(package, &bytes).unwrap();
        // The entry holds the caller's Arc, not a copy, so its address names these bytes for
        // as long as the entry exists.
        assert_eq!(Arc::strong_count(&bytes), 2);
        assert!(Arc::ptr_eq(&parsed, &parse_runtime(package, &bytes).unwrap()));
        // Equal bytes in another allocation still find the entry, by content, and the entry then
        // keeps that allocation, so its next lookup is by identity.
        let other = Arc::new(bytes.to_vec());
        assert!(Arc::ptr_eq(&parsed, &parse_runtime(package, &other).unwrap()));
        assert_eq!((Arc::strong_count(&bytes), Arc::strong_count(&other)), (1, 2));
        assert!(Arc::ptr_eq(&parsed, &parse_runtime(package, &bytes[..]).unwrap()));
    }

    #[test]
    fn validation_fills_the_authoring_entry_and_execution_takes_it() {
        let package = "test.cache.authoring";
        let bytes = runtime(package, "Clap twice.");
        validate_package_runtime(package, &[declaration()], &bytes).unwrap();
        // Both lookups find the one entry validation kept: no parse runs for either.
        let execution = parse_runtime(package, &bytes).unwrap();
        assert!(Arc::ptr_eq(&execution, &cached_parse(package, &bytes, true).unwrap()));
    }

    #[test]
    fn an_authoring_parse_never_takes_an_execution_entry() {
        // A duplicate field loads for execution but not for authoring.
        let package = "test.cache.strict";
        let bytes = String::from_utf8(runtime(package, "Spin."))
            .unwrap()
            .replace(
                "    effects: []
",
                "    effects: []
    effects: []
",
            )
            .into_bytes();
        assert!(parse_runtime(package, &bytes).is_ok());
        match validate_package_runtime(package, &[declaration()], &bytes) {
            Err(PackageRuntimeFailure::Rejected(error)) => {
                assert_eq!(error.message, "Package runtime YAML is invalid");
            }
            Err(other) => panic!("another failure: {other:?}"),
            Ok(()) => panic!("the execution parse was reused for validation"),
        }
    }
}
