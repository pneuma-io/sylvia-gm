//! `builder/_ruleset_source.py`: compile compact Builder-authored Ruleset source into one
//! normalized package draft.
//!
//! Python works on the objects PyYAML built, and `yaml.safe_dump` writes a container reached
//! twice as an anchor and aliases (`&id001`, `*id001`). `copy.deepcopy` keeps sharing inside
//! what it copies, so a YAML alias inside `definitions` or one procedure reaches the hashed
//! `runtime/ruleset.yaml` as an anchor. This module therefore keeps Python's object graph
//! ([`Py`], containers shared by reference), with `deepcopy`'s memo, and converts to JSON only
//! where Python encodes JSON.

use crate::package_materialization::{NormalizedPackageDraft, Payloads};
use std::cell::RefCell;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::rc::Rc;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::float_repr;
use sylvia_base::pystr;
use sylvia_base::pyyaml::{
    self, Constructor, Hooks, Loaded, Loader, NodeId, Object, ObjectId, Value, YamlError,
};
use sylvia_base::Json;
use sylvia_domain::content::{validate_content_shape, RULESET_TESTS_PATH};
use sylvia_domain::error::{DomainError, DomainResult, RulesetDiagnostic};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::package_library::{
    ENGINE_FEATURE_PANEL_WORKSPACES, ENGINE_FEATURE_SHOT_EFFECTS, ENGINE_FEATURE_SPATIAL_CLEARANCE,
    ENGINE_FEATURE_SPATIAL_MUTATION, ENGINE_FEATURE_SPATIAL_PATH_MOVING_ENTITY, ENGINE_FEATURE_TACTICAL_GRID,
};
use sylvia_domain::panel_layout::PANEL_LAYOUT_PATH;
use sylvia_domain::ruleset_tests::validate_ruleset_tests;
use sylvia_domain::starting_state::{
    PRIVATE_STATE_FEATURE, REQUIRED_STARTING_STATE_FEATURE, STARTING_STATE_FEATURE,
};
use sylvia_domain::yaml_bounds::{validate_yaml_graph, YamlGraphLimits};

/// `COMPACT_RULESET_SOURCE`.
pub const COMPACT_RULESET_SOURCE: &str = "ruleset-source.yaml";
const SOURCE_DEPTH_LIMIT: usize = 64;
const SOURCE_NODE_LIMIT: usize = 250_000;
const SOURCE_KEYS: [&str; 11] = [
    "format",
    "id",
    "version",
    "label",
    "publisher",
    "mechanic_families",
    "public_actions",
    "private_mechanics",
    "definitions",
    "participant_artifacts",
    "operating_guide",
];
const RESOLUTIONS: [&str; 5] =
    ["engine_family", "engine_specialized", "hybrid", "gm_adjudicated", "unsupported"];
const INVALID: &str = "Builder Ruleset source is invalid";
const INVALID_CODE: &str = "builder_ruleset_source_invalid";

/// `CompiledRulesetSource`: the generated package draft plus the compatibility facts it selected.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledRulesetSource {
    pub draft: NormalizedPackageDraft,
    pub required_engine_features: Vec<String>,
}

// ── Python's object graph ──────────────────────────────────────────────────────────────────

type List = Rc<RefCell<Vec<Py>>>;
type Dict = Rc<RefCell<Vec<(String, Py)>>>;

/// A Python value of the source: JSON-compatible data, containers shared by reference.
#[derive(Debug, Clone)]
enum Py {
    None,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    List(List),
    Dict(Dict),
}

fn dict(pairs: Vec<(String, Py)>) -> Py {
    Py::Dict(Rc::new(RefCell::new(pairs)))
}

fn list(items: Vec<Py>) -> Py {
    Py::List(Rc::new(RefCell::new(items)))
}

fn text(value: &str) -> Py {
    Py::Str(value.to_owned())
}

impl Py {
    fn as_dict(&self) -> Option<&Dict> {
        match self {
            Py::Dict(map) => Some(map),
            _ => None,
        }
    }

    fn as_str(&self) -> Option<&str> {
        match self {
            Py::Str(value) => Some(value),
            _ => None,
        }
    }

    /// `type(value).__name__`.
    fn type_name(&self) -> &'static str {
        match self {
            Py::None => "NoneType",
            Py::Bool(_) => "bool",
            Py::Int(_) => "int",
            Py::Float(_) => "float",
            Py::Str(_) => "str",
            Py::List(_) => "list",
            Py::Dict(_) => "dict",
        }
    }

    /// `mapping.get(key)` (a reference to the same object).
    fn get(&self, key: &str) -> Option<Py> {
        let map = self.as_dict()?.borrow();
        map.iter().find(|(name, _)| name == key).map(|(_, value)| value.clone())
    }

    fn contains(&self, key: &str) -> bool {
        self.get(key).is_some()
    }

    /// `mapping[key] = value`: a present key keeps its place.
    fn set(&self, key: &str, value: Py) {
        let mut map = self.as_dict().expect("a dict").borrow_mut();
        match map.iter_mut().find(|(name, _)| name == key) {
            Some((_, slot)) => *slot = value,
            None => map.push((key.to_owned(), value)),
        }
    }

    /// `mapping.pop(key, None)`.
    fn pop(&self, key: &str) -> Py {
        let mut map = self.as_dict().expect("a dict").borrow_mut();
        match map.iter().position(|(name, _)| name == key) {
            Some(index) => map.remove(index).1,
            None => Py::None,
        }
    }

    /// `mapping[key]` for a key the caller has already required.
    fn item(&self, key: &str) -> Py {
        self.get(key).expect("a required field")
    }

    fn keys(&self) -> Vec<String> {
        self.as_dict()
            .map(|map| map.borrow().iter().map(|(key, _)| key.clone()).collect())
            .unwrap_or_default()
    }

    fn values(&self) -> Vec<Py> {
        match self {
            Py::Dict(map) => map.borrow().iter().map(|(_, value)| value.clone()).collect(),
            Py::List(items) => items.borrow().clone(),
            _ => Vec::new(),
        }
    }

    /// `value is True`.
    fn is_true(&self) -> bool {
        matches!(self, Py::Bool(true))
    }

    /// Python `==` against text.
    fn eq_str(&self, other: &str) -> bool {
        self.as_str() == Some(other)
    }

    /// `str(value)` for the scalars `_substitute` embeds.
    fn str_scalar(&self) -> String {
        match self {
            Py::Str(value) => value.clone(),
            Py::Int(value) => value.to_string(),
            Py::Float(value) => float_repr(*value),
            Py::Bool(true) => "True".into(),
            Py::Bool(false) => "False".into(),
            _ => unreachable!("only scalars are embedded"),
        }
    }

    /// `value in {...}` for a set of text: Python hashes first, so a list or dict raises.
    fn in_text_set(&self, members: &[&str]) -> DomainResult<bool> {
        match self {
            Py::List(_) | Py::Dict(_) => {
                Err(DomainError::type_error(format!("unhashable type: '{}'", self.type_name())))
            }
            Py::Str(value) => Ok(members.contains(&value.as_str())),
            _ => Ok(false),
        }
    }

    /// The JSON value, sharing expanded (the graph has no cycles by now).
    fn to_json(&self) -> Json {
        match self {
            Py::None => Json::Null,
            Py::Bool(flag) => Json::Bool(*flag),
            Py::Int(number) => Json::from(*number),
            Py::Float(number) => {
                serde_json::Number::from_f64(*number).map(Json::Number).unwrap_or(Json::Null)
            }
            Py::Str(value) => Json::String(value.clone()),
            Py::List(items) => Json::Array(items.borrow().iter().map(Py::to_json).collect()),
            Py::Dict(map) => {
                Json::Object(map.borrow().iter().map(|(key, value)| (key.clone(), value.to_json())).collect())
            }
        }
    }
}

/// `copy.deepcopy(value)`: containers copied once each, so sharing inside the value is kept.
fn deepcopy(value: &Py) -> Py {
    fn copy(value: &Py, memo: &mut HashMap<usize, Py>) -> Py {
        match value {
            Py::List(items) => {
                let key = Rc::as_ptr(items) as usize;
                if let Some(done) = memo.get(&key) {
                    return done.clone();
                }
                let fresh: List = Rc::new(RefCell::new(Vec::new()));
                memo.insert(key, Py::List(fresh.clone()));
                let copied: Vec<Py> = items.borrow().iter().map(|item| copy(item, memo)).collect();
                *fresh.borrow_mut() = copied;
                Py::List(fresh)
            }
            Py::Dict(map) => {
                let key = Rc::as_ptr(map) as usize;
                if let Some(done) = memo.get(&key) {
                    return done.clone();
                }
                let fresh: Dict = Rc::new(RefCell::new(Vec::new()));
                memo.insert(key, Py::Dict(fresh.clone()));
                let copied: Vec<(String, Py)> =
                    map.borrow().iter().map(|(name, item)| (name.clone(), copy(item, memo))).collect();
                *fresh.borrow_mut() = copied;
                Py::Dict(fresh)
            }
            scalar => scalar.clone(),
        }
    }
    copy(value, &mut HashMap::new())
}

/// The loaded document as Python objects, aliases as the same object.
fn from_loaded(loaded: &Loaded, value: &Value, memo: &mut HashMap<ObjectId, Py>) -> Py {
    match value {
        Value::None => Py::None,
        Value::Bool(flag) => Py::Bool(*flag),
        Value::Int(number) => Py::Int(*number),
        Value::Float(number) => Py::Float(*number),
        Value::Str(value) => text(value),
        Value::List(id) | Value::Dict(id) => {
            if let Some(done) = memo.get(id) {
                return done.clone();
            }
            match loaded.object(*id) {
                Object::List(items) => {
                    let fresh: List = Rc::new(RefCell::new(Vec::new()));
                    memo.insert(*id, Py::List(fresh.clone()));
                    let items: Vec<Py> = items.iter().map(|item| from_loaded(loaded, item, memo)).collect();
                    *fresh.borrow_mut() = items;
                    Py::List(fresh)
                }
                Object::Dict(pairs) => {
                    let fresh: Dict = Rc::new(RefCell::new(Vec::new()));
                    memo.insert(*id, Py::Dict(fresh.clone()));
                    let pairs: Vec<(String, Py)> = pairs
                        .iter()
                        .map(|(key, item)| match key {
                            Value::Str(key) => (key.clone(), from_loaded(loaded, item, memo)),
                            _ => unreachable!("the loader admits only text keys"),
                        })
                        .collect();
                    *fresh.borrow_mut() = pairs;
                    Py::Dict(fresh)
                }
                Object::Set(_) | Object::Empty => unreachable!("_read_source refuses sets"),
            }
        }
        _ => unreachable!("_read_source refuses other values"),
    }
}

/// The object graph as PyYAML's representer sees it: one object per Python container.
fn to_loaded(root: &Py) -> Loaded {
    fn value(item: &Py, objects: &mut Vec<Object>, ids: &mut HashMap<usize, ObjectId>) -> Value {
        match item {
            Py::None => Value::None,
            Py::Bool(flag) => Value::Bool(*flag),
            Py::Int(number) => Value::Int(*number),
            Py::Float(number) => Value::Float(*number),
            Py::Str(value) => Value::Str(value.clone()),
            Py::List(items) => {
                let key = Rc::as_ptr(items) as usize;
                if let Some(id) = ids.get(&key) {
                    return Value::List(*id);
                }
                let id = ObjectId(objects.len());
                objects.push(Object::List(Vec::new()));
                ids.insert(key, id);
                let filled: Vec<Value> =
                    items.borrow().iter().map(|item| value(item, objects, ids)).collect();
                objects[id.0] = Object::List(filled);
                Value::List(id)
            }
            Py::Dict(map) => {
                let key = Rc::as_ptr(map) as usize;
                if let Some(id) = ids.get(&key) {
                    return Value::Dict(*id);
                }
                let id = ObjectId(objects.len());
                objects.push(Object::Dict(Vec::new()));
                ids.insert(key, id);
                let filled: Vec<(Value, Value)> = map
                    .borrow()
                    .iter()
                    .map(|(name, item)| (Value::Str(name.clone()), value(item, objects, ids)))
                    .collect();
                objects[id.0] = Object::Dict(filled);
                Value::Dict(id)
            }
        }
    }
    let mut objects = Vec::new();
    let root = value(root, &mut objects, &mut HashMap::new());
    Loaded { root: Some(root), objects }
}

// ── Reading ────────────────────────────────────────────────────────────────────────────────

fn python_error(class: &'static str, message: &str) -> YamlError {
    YamlError::Python { class, message: message.to_owned() }
}

/// `_SourceLoader.construct_document`: bound the node graph before constructing it.
fn bounded_document(constructor: &mut Constructor, root: NodeId) -> Result<Option<Value>, YamlError> {
    let limits = YamlGraphLimits {
        maximum_depth: SOURCE_DEPTH_LIMIT,
        maximum_nodes: SOURCE_NODE_LIMIT,
        maximum_object_members: None,
        count_mapping_keys: true,
    };
    validate_yaml_graph(constructor.graph, root, limits)?;
    constructor.base_construct_document(root)
}

/// `_SourceLoader.construct_mapping`: unique text field names only.
fn unique_text_mapping(
    constructor: &mut Constructor,
    node: NodeId,
    deep: bool,
) -> Result<Vec<(Value, Value)>, YamlError> {
    constructor.flatten_mapping(node)?;
    let pairs = constructor.construct_pairs(node, deep)?;
    let mut seen = HashSet::new();
    for (key, _) in &pairs {
        match key {
            Value::Str(name) if seen.insert(name.clone()) => {}
            _ => return Err(python_error("ValueError", "Ruleset source requires unique text field names")),
        }
    }
    Ok(pairs)
}

/// The classes `compile_ruleset_source` turns into its YAML diagnostic: `yaml.YAMLError`,
/// `ValueError` (and `YamlShapeError`), `UnicodeDecodeError` and `RecursionError`.
fn caught(error: &YamlError) -> bool {
    error.is_yaml_error()
        || matches!(
            error.class_name(),
            "ValueError" | "YamlShapeError" | "UnicodeDecodeError" | "RecursionError"
        )
}

/// `_read_source(source_bytes)`'s walk: bound the tree (aliases counted at every occurrence)
/// and refuse YAML-only values.
fn check_tree(loaded: &Loaded) -> bool {
    let root = loaded.root.clone().unwrap_or(Value::None);
    let mut pending = vec![(root, 0usize)];
    let mut nodes = 0usize;
    while let Some((value, depth)) = pending.pop() {
        nodes += 1;
        if depth > SOURCE_DEPTH_LIMIT || nodes > SOURCE_NODE_LIMIT {
            return false;
        }
        match &value {
            Value::Dict(id) | Value::List(id) => match loaded.object(*id) {
                Object::Dict(pairs) => {
                    pending.extend(pairs.iter().map(|(_, item)| (item.clone(), depth + 1)))
                }
                Object::List(items) => pending.extend(items.iter().map(|item| (item.clone(), depth + 1))),
                Object::Set(_) | Object::Empty => return false,
            },
            Value::None | Value::Str(_) | Value::Bool(_) | Value::Int(_) => {}
            Value::Float(number) if number.is_finite() => {}
            _ => return false,
        }
    }
    true
}

/// `_read_source`, then `compile_ruleset_source`'s refusal of anything it raises.
fn read_source(source_bytes: &[u8]) -> DomainResult<Py> {
    let refused = || {
        diagnostic(
            "Ruleset source must use bounded UTF-8 YAML with unique text fields and finite JSON values.",
            "ruleset_source_yaml_invalid",
            "$",
            None,
            None,
        )
    };
    let Ok(text) = std::str::from_utf8(source_bytes) else { return Err(refused()) };
    let hooks = Hooks {
        construct_document: Some(bounded_document),
        construct_mapping: Some(unique_text_mapping),
        ..Hooks::default()
    };
    let loaded = match pyyaml::load(text, Loader::Pure, hooks) {
        Ok(loaded) => loaded,
        Err(error) if caught(&error) => return Err(refused()),
        Err(error) => return Err(DomainError::uncaught(error.class_name(), error.to_string())),
    };
    if !check_tree(&loaded) {
        return Err(refused());
    }
    let root = loaded.root.clone().unwrap_or(Value::None);
    Ok(from_loaded(&loaded, &root, &mut HashMap::new()))
}

// ── Diagnostics ────────────────────────────────────────────────────────────────────────────

/// `_diagnostic(message, code=..., path=..., package_id=..., guidance=...)`.
fn diagnostic(
    message: &str,
    code: &str,
    path: &str,
    package_id: Option<&str>,
    guidance: Option<&str>,
) -> DomainError {
    DomainError::command_rejected_with(
        INVALID,
        INVALID_CODE,
        vec![RulesetDiagnostic {
            code: code.into(),
            phase: "source".into(),
            message: message.into(),
            file: Some(COMPACT_RULESET_SOURCE.into()),
            path: Some(path.into()),
            package_id: package_id.map(str::to_owned),
            guidance: guidance.map(str::to_owned),
            ..Default::default()
        }],
    )
}

/// `_mapping(value, path=..., package_id=...)`: a fresh dict of deep copies.
fn mapping(value: &Py, path: &str, package_id: Option<&str>) -> DomainResult<Py> {
    let Some(map) = value.as_dict() else {
        return Err(diagnostic(
            "Expected a YAML object.",
            "ruleset_source_expected_object",
            path,
            package_id,
            None,
        ));
    };
    let pairs = map.borrow().iter().map(|(key, item)| (key.clone(), deepcopy(item))).collect();
    Ok(dict(pairs))
}

/// `_require_fields(value, required, optional=..., path=..., package_id=...)`.
fn require_fields(
    value: &Py,
    required: &[&str],
    optional: &[&str],
    path: &str,
    package_id: Option<&str>,
) -> DomainResult<()> {
    let keys = value.keys();
    let mut missing: Vec<&str> =
        required.iter().copied().filter(|field| !keys.iter().any(|key| key == field)).collect();
    let mut extra: Vec<&str> = keys
        .iter()
        .map(String::as_str)
        .filter(|key| !required.contains(key) && !optional.contains(key))
        .collect();
    missing.sort_unstable();
    extra.sort_unstable();
    let diagnostics: Vec<RulesetDiagnostic> = [("missing", missing), ("undeclared", extra)]
        .into_iter()
        .flat_map(|(kind, fields)| {
            fields.into_iter().map(move |field| RulesetDiagnostic {
                code: format!("ruleset_source_{kind}_field"),
                phase: "source".into(),
                message: format!("Field '{field}' is {kind}."),
                file: Some(COMPACT_RULESET_SOURCE.into()),
                path: Some(format!("{path}.{field}")),
                package_id: package_id.map(str::to_owned),
                guidance: Some(
                    if kind == "missing" {
                        "Supply the required field."
                    } else {
                        "Remove the undeclared field."
                    }
                    .into(),
                ),
                ..Default::default()
            })
        })
        .collect();
    if diagnostics.is_empty() {
        return Ok(());
    }
    Err(DomainError::command_rejected_with(INVALID, INVALID_CODE, diagnostics))
}

/// `_sequence(value, path=..., package_id=...)`: `list(value)` of a list.
fn sequence(value: &Py, path: &str, package_id: &str) -> DomainResult<Vec<Py>> {
    match value {
        Py::List(items) => Ok(items.borrow().clone()),
        _ => Err(diagnostic(
            "Expected a YAML list.",
            "ruleset_source_expected_list",
            path,
            Some(package_id),
            None,
        )),
    }
}

/// `list(value)`, with Python's `TypeError` for a value that is not iterable.
fn py_list(value: &Py) -> DomainResult<Vec<Py>> {
    match value {
        Py::List(items) => Ok(items.borrow().clone()),
        Py::Dict(map) => Ok(map.borrow().iter().map(|(key, _)| text(key)).collect()),
        Py::Str(value) => Ok(value.chars().map(|c| Py::Str(c.to_string())).collect()),
        other => Err(DomainError::type_error(format!("'{}' object is not iterable", other.type_name()))),
    }
}

// ── Compilation ────────────────────────────────────────────────────────────────────────────

/// `_substitute(value, configuration)`: expand whole or embedded `{name}` tokens.
fn substitute(value: &Py, configuration: &[(String, Py)]) -> DomainResult<Py> {
    match value {
        Py::Str(original) => {
            let mut current = original.clone();
            for (name, replacement) in configuration {
                let token = format!("{{{name}}}");
                if current == token {
                    return Ok(deepcopy(replacement));
                }
                if current.contains(&token) {
                    if !matches!(replacement, Py::Str(_) | Py::Int(_) | Py::Float(_) | Py::Bool(_)) {
                        return Err(DomainError::value(
                            "Embedded family configuration values must be scalar",
                        ));
                    }
                    current = current.replace(&token, &replacement.str_scalar());
                }
            }
            Ok(Py::Str(current))
        }
        Py::Dict(map) => {
            let pairs = map
                .borrow()
                .iter()
                .map(|(key, item)| Ok((key.clone(), substitute(item, configuration)?)))
                .collect::<DomainResult<Vec<_>>>()?;
            Ok(dict(pairs))
        }
        Py::List(items) => {
            let items = items
                .borrow()
                .iter()
                .map(|item| substitute(item, configuration))
                .collect::<DomainResult<Vec<_>>>()?;
            Ok(list(items))
        }
        scalar => Ok(deepcopy(scalar)),
    }
}

/// `_runtime_parameter(raw, path=..., package_id=...)`: the runtime declaration and, for a
/// model-supplied input, its JSON Schema.
fn runtime_parameter(raw: &Py, path: &str, package_id: &str) -> DomainResult<(Py, Option<Py>)> {
    const ALLOWED: [&str; 15] = [
        "name",
        "type",
        "source",
        "required",
        "description",
        "enum_values",
        "minimum",
        "maximum",
        "min_items",
        "max_items",
        "unique_items",
        "items",
        "resolve",
        "from_state",
        "default",
    ];
    let parameter = mapping(raw, path, Some(package_id))?;
    if parameter.keys().iter().any(|key| !ALLOWED.contains(&key.as_str())) {
        return Err(diagnostic(
            "Parameter contains unsupported fields.",
            "ruleset_source_parameter_fields",
            path,
            Some(package_id),
            Some("Use choice, gm_judgment, or state as the input source."),
        ));
    }
    let name = parameter.get("name");
    let parameter_type = parameter.get("type");
    let source = parameter.pop("source");
    let named = name.as_ref().and_then(Py::as_str).is_some_and(|name| !name.is_empty());
    let Some(parameter_type) =
        parameter_type.as_ref().and_then(Py::as_str).filter(|_| named).map(str::to_owned)
    else {
        return Err(diagnostic(
            "Parameter name and type are required.",
            "ruleset_source_parameter_identity",
            path,
            Some(package_id),
            None,
        ));
    };
    if !source.in_text_set(&["choice", "gm_judgment", "state"])? {
        return Err(diagnostic(
            "Parameter input source is unsupported.",
            "ruleset_source_parameter_authority",
            &format!("{path}.source"),
            Some(package_id),
            Some("Choose choice, gm_judgment, or state."),
        ));
    }
    if source.eq_str("state") {
        if !parameter.get("from_state").is_some_and(|value| value.as_str().is_some()) {
            return Err(diagnostic(
                "State-owned parameters require from_state.",
                "ruleset_source_state_path_missing",
                &format!("{path}.from_state"),
                Some(package_id),
                None,
            ));
        }
        return Ok((parameter, None));
    }
    if parameter.contains("from_state") {
        return Err(diagnostic(
            "Only a state-owned parameter may declare from_state.",
            "ruleset_source_state_path_conflict",
            &format!("{path}.from_state"),
            Some(package_id),
            None,
        ));
    }
    let schema = match parameter_type.as_str() {
        "enum" => {
            let values = parameter.get("enum_values").unwrap_or_else(|| list(Vec::new()));
            dict(vec![("type".into(), text("string")), ("enum".into(), list(py_list(&values)?))])
        }
        "entity_id_list" => dict(vec![
            ("type".into(), text("array")),
            ("items".into(), dict(vec![("type".into(), text("string"))])),
        ]),
        "entity_id" | "string" => dict(vec![("type".into(), text("string"))]),
        other => dict(vec![("type".into(), text(other))]),
    };
    let resolve = parameter.get("resolve").unwrap_or(Py::None);
    if resolve.in_text_set(&["participant", "entity"])? {
        schema.set("x-sylvia-gm-reference", resolve);
    } else if resolve.eq_str("entity_list") {
        if let Some(items) = schema.get("items").filter(|items| items.as_dict().is_some()) {
            items.set("x-sylvia-gm-reference", text("entity"));
        }
    }
    if let Some(description) = parameter.get("description").filter(|value| value.as_str().is_some()) {
        schema.set("description", description);
    }
    for (source_name, schema_name) in [
        ("minimum", "minimum"),
        ("maximum", "maximum"),
        ("min_items", "minItems"),
        ("max_items", "maxItems"),
        ("unique_items", "uniqueItems"),
        ("items", "items"),
    ] {
        if let Some(value) = parameter.get(source_name) {
            schema.set(schema_name, deepcopy(&value));
        }
    }
    schema.set("x-sylvia-gm-input-source", source);
    Ok((parameter, Some(schema)))
}

/// `_required_engine_features(value)`: the host capabilities the compiled runtime data uses.
fn required_engine_features(value: &Py) -> BTreeSet<String> {
    const COLLECTION_CALLS: [&str; 12] = [
        "indices",
        "portion",
        "joined",
        "replaced",
        "without",
        "partitions",
        "completions",
        "occurrences",
        "one",
        "ordered",
        "text",
        "total",
    ];
    fn collect(item: &Py, features: &mut BTreeSet<String>) {
        match item {
            Py::Dict(_) => {
                let mut add = |feature: &str| {
                    features.insert(feature.to_owned());
                };
                if item.contains("isolated_participant") {
                    add("isolated_participants.v1");
                }
                let calls = item.values().iter().any(|value| {
                    value.as_str().is_some_and(|text| {
                        COLLECTION_CALLS.iter().any(|name| text.contains(&format!("{name}(")))
                    })
                });
                if item.contains("shuffle") || calls {
                    add("collection_mechanics.v1");
                }
                if item.contains("publish") {
                    add("public_results.v1");
                }
                if item.contains("shot_effect") {
                    add(ENGINE_FEATURE_SHOT_EFFECTS);
                    add(ENGINE_FEATURE_TACTICAL_GRID);
                }
                if item.contains("occupies_cells") {
                    add(ENGINE_FEATURE_SPATIAL_CLEARANCE);
                }
                if item.contains("spatial_mutation") {
                    add(ENGINE_FEATURE_SPATIAL_MUTATION);
                    add(ENGINE_FEATURE_TACTICAL_GRID);
                }
                if let Some(query) = item.get("spatial_query").filter(|query| query.as_dict().is_some()) {
                    add(ENGINE_FEATURE_TACTICAL_GRID);
                    let is_path = query.get("operation").is_some_and(|operation| operation.eq_str("path"));
                    if is_path {
                        add(ENGINE_FEATURE_SPATIAL_CLEARANCE);
                    }
                    let path_query = query.get("query");
                    if is_path && path_query.is_some_and(|path_query| path_query.contains("moving_entity_id"))
                    {
                        add(ENGINE_FEATURE_SPATIAL_PATH_MOVING_ENTITY);
                    }
                }
                for nested in item.values() {
                    collect(&nested, features);
                }
            }
            Py::List(items) => {
                for nested in items.borrow().iter() {
                    collect(nested, features);
                }
            }
            _ => {}
        }
    }
    let mut features = BTreeSet::new();
    collect(value, &mut features);
    features
}

/// The state collected across operations.
#[derive(Default)]
struct Operations {
    runtime_tools: Vec<(String, Py)>,
    manifest_tools: Vec<Py>,
    capabilities: BTreeSet<String>,
    support_records: Vec<Py>,
}

/// `compile_operation(raw, path=..., public=...)`.
fn compile_operation(
    raw: &Py,
    path: &str,
    public: bool,
    families: &Py,
    package_id: &str,
    state: &mut Operations,
) -> DomainResult<()> {
    let operation = mapping(raw, path, Some(package_id))?;
    let mut expected = vec!["name", "family", "description", "configure"];
    if public {
        expected.extend([
            "capability",
            "state_predicates",
            "turn_statuses",
            "writers",
            "reference_availability",
            "gameplay_resolution",
            "deterministic_part",
            "gm_part",
        ]);
    }
    require_fields(&operation, &expected, &[], path, Some(package_id))?;
    let name = operation.item("name");
    let family_id = operation.item("family");
    let (Some(name), Some(family_id)) =
        (name.as_str(), family_id.as_str().filter(|id| families.contains(id)))
    else {
        return Err(diagnostic(
            "Operation names an unknown mechanic family.",
            "ruleset_source_family_unknown",
            &format!("{path}.family"),
            Some(package_id),
            None,
        ));
    };
    let family = mapping(&families.item(family_id), &format!("$.mechanic_families.{family_id}"), None)?;
    if state.runtime_tools.iter().any(|(existing, _)| existing == name) {
        return Err(diagnostic(
            "Operation names must be unique across public actions and private mechanics.",
            "ruleset_source_operation_duplicate",
            &format!("{path}.name"),
            Some(package_id),
            Some("Give each operation a distinct name."),
        ));
    }
    let configuration =
        mapping(&operation.item("configure"), &format!("{path}.configure"), Some(package_id))?;
    let configuration_pairs = configuration.as_dict().expect("a dict").borrow().clone();
    let expanded = substitute(&family, &configuration_pairs)?;
    let mut runtime_parameters = Vec::new();
    let model_properties = dict(Vec::new());
    let mut model_required = Vec::new();
    let parameters = sequence(&expanded.item("parameters"), &format!("{path}.parameters"), package_id)?;
    for (index, raw_parameter) in parameters.iter().enumerate() {
        let (parameter, schema) =
            runtime_parameter(raw_parameter, &format!("{path}.parameters[{index}]"), package_id)?;
        if let Some(schema) = schema {
            let parameter_name = parameter.item("name").as_str().expect("checked").to_owned();
            model_properties.set(&parameter_name, schema);
            if parameter.get("required").is_none_or(|required| required.is_true()) {
                model_required.push(text(&parameter_name));
            }
        }
        runtime_parameters.push(parameter);
    }
    let runtime_tool = dict(vec![
        ("description".into(), operation.item("description")),
        ("parameters".into(), list(runtime_parameters)),
        ("pre_conditions".into(), expanded.item("pre_conditions")),
        ("effects".into(), expanded.item("effects")),
        ("narrative".into(), expanded.item("narrative")),
    ]);
    if let Some(metadata) = expanded.get("metadata") {
        runtime_tool.set("metadata", metadata);
    }
    match state.runtime_tools.iter_mut().find(|(existing, _)| existing == name) {
        Some((_, slot)) => *slot = runtime_tool,
        None => state.runtime_tools.push((name.to_owned(), runtime_tool)),
    }
    if !public {
        return Ok(());
    }
    let capability = operation.item("capability");
    let resolution = operation.item("gameplay_resolution");
    let reference = operation.item("reference_availability");
    // `not isinstance(capability, str) or resolution not in _RESOLUTIONS`: the membership test
    // (which raises for an unhashable value) runs only for a text capability.
    let known = match capability.as_str() {
        Some(_) => resolution.in_text_set(&RESOLUTIONS)?,
        None => false,
    };
    let (Some(capability), true) = (capability.as_str(), known) else {
        return Err(diagnostic(
            "Public action capability or Gameplay Resolution is invalid.",
            "ruleset_source_public_claim_invalid",
            path,
            Some(package_id),
            None,
        ));
    };
    state.capabilities.insert(capability.to_owned());
    state.manifest_tools.push(dict(vec![
        ("name".into(), text(name)),
        ("capability".into(), text(capability)),
        (
            "input_schema".into(),
            dict(vec![
                ("type".into(), text("object")),
                ("properties".into(), model_properties),
                ("required".into(), list(model_required)),
                ("additionalProperties".into(), Py::Bool(false)),
            ]),
        ),
        ("state_predicates".into(), operation.item("state_predicates")),
        ("turn_statuses".into(), operation.item("turn_statuses")),
        ("writers".into(), operation.item("writers")),
    ]));
    let engine_backed = resolution.in_text_set(&["engine_family", "hybrid", "gm_adjudicated"])?;
    state.support_records.push(dict(vec![
        ("id".into(), text(&format!("action.{name}"))),
        ("reference_availability".into(), reference),
        ("gameplay_resolution".into(), resolution),
        ("mechanic_family".into(), if engine_backed { text(family_id) } else { Py::None }),
        ("deterministic_part".into(), operation.item("deterministic_part")),
        ("gm_part".into(), operation.item("gm_part")),
    ]));
    Ok(())
}

/// `item.get(key)` on each member of a list, with Python's `AttributeError` for a non-dict.
fn any_member(items: &Py, test: impl Fn(&Py) -> bool) -> DomainResult<bool> {
    let members = match items {
        Py::Dict(_) => items.keys().into_iter().map(Py::Str).collect(),
        other => py_list(other)?,
    };
    for member in members {
        if member.as_dict().is_none() {
            return Err(DomainError::uncaught(
                "AttributeError",
                format!("'{}' object has no attribute 'get'", member.type_name()),
            ));
        }
        if test(&member) {
            return Ok(true);
        }
    }
    Ok(false)
}

fn json_bytes(value: &Py) -> Vec<u8> {
    canonical_json_bytes(&value.to_json())
}

fn texts(values: impl IntoIterator<Item = String>) -> Py {
    list(values.into_iter().map(Py::Str).collect())
}

/// `compile_ruleset_source(source_bytes)`: compile readable family-based YAML into the
/// existing portable runtime representation.
pub fn compile_ruleset_source(source_bytes: &[u8]) -> DomainResult<CompiledRulesetSource> {
    let loaded = read_source(source_bytes)?;
    let source = mapping(&loaded, "$", None)?;
    let package_id = source.get("id").and_then(|id| id.as_str().map(str::to_owned));
    let package = package_id.as_deref();
    let optional =
        ["ui_surfaces", "panel_layout", "tests", "state_schemas", "model_decision_boundary", "procedures"];
    require_fields(&source, &SOURCE_KEYS, &optional, "$", package)?;
    if !matches!(source.get("format"), Some(Py::Int(1))) {
        return Err(diagnostic(
            "Ruleset source format must be the integer 1.",
            "ruleset_source_format_invalid",
            "$.format",
            package,
            None,
        ));
    }
    let Some(package_id) = package_id else {
        return Err(diagnostic(
            "Ruleset identity is required.",
            "ruleset_source_identity_missing",
            "$.id",
            None,
            None,
        ));
    };
    let package = package_id.as_str();
    for field in ["version", "label", "publisher"] {
        if !source.item(field).as_str().is_some_and(|value| !pystr::strip(value).is_empty()) {
            return Err(diagnostic(
                &format!("Ruleset {field} is required."),
                "ruleset_source_identity_missing",
                &format!("$.{field}"),
                Some(package),
                None,
            ));
        }
    }
    let families = mapping(&source.item("mechanic_families"), "$.mechanic_families", Some(package))?;
    for (family_id, raw_family) in families.as_dict().expect("a dict").borrow().iter() {
        let family_path = format!("$.mechanic_families.{family_id}");
        let family = mapping(raw_family, &family_path, Some(package))?;
        require_fields(
            &family,
            &["parameters", "pre_conditions", "effects", "narrative"],
            &["metadata"],
            &family_path,
            Some(package),
        )?;
    }
    let source_json = source.to_json();
    let shape = validate_content_shape("ruleset_source", &source_json, Some(COMPACT_RULESET_SOURCE))
        .map_err(DomainError::from)
        .and_then(|()| match source_json.get("tests") {
            Some(tests) => validate_ruleset_tests(tests).map(drop),
            None => Ok(()),
        });
    match shape {
        Ok(()) => {}
        Err(DomainError::Diagnosed { error_type: "ContentShapeError", diagnostics, .. }) => {
            let diagnostics = diagnostics
                .into_iter()
                .map(|item| {
                    if item.file.as_deref() != Some(RULESET_TESTS_PATH) {
                        return Ok(item);
                    }
                    let Some(path) = item.path.as_deref() else {
                        return Err(DomainError::type_error("'NoneType' object is not subscriptable"));
                    };
                    let rest: String = path.chars().skip(1).collect();
                    Ok(RulesetDiagnostic {
                        file: Some(COMPACT_RULESET_SOURCE.into()),
                        path: Some(format!("$.tests{rest}")),
                        ..item
                    })
                })
                .collect::<DomainResult<Vec<_>>>()?;
            return Err(DomainError::command_rejected_with(INVALID, INVALID_CODE, diagnostics));
        }
        Err(error) => return Err(error),
    }

    let mut state = Operations::default();
    let private_mechanics = sequence(&source.item("private_mechanics"), "$.private_mechanics", package)?;
    for (index, operation) in private_mechanics.iter().enumerate() {
        compile_operation(
            operation,
            &format!("$.private_mechanics[{index}]"),
            false,
            &families,
            package,
            &mut state,
        )?;
    }
    let public_actions = sequence(&source.item("public_actions"), "$.public_actions", package)?;
    for (index, operation) in public_actions.iter().enumerate() {
        compile_operation(
            operation,
            &format!("$.public_actions[{index}]"),
            true,
            &families,
            package,
            &mut state,
        )?;
    }
    let mut surfaces = Vec::new();
    let raw_surfaces = source.get("ui_surfaces").unwrap_or_else(|| list(Vec::new()));
    for (index, raw) in sequence(&raw_surfaces, "$.ui_surfaces", package)?.iter().enumerate() {
        let surface = mapping(raw, &format!("$.ui_surfaces[{index}]"), Some(package))?;
        let (Some(id), Some(capability)) = (
            surface.get("id").and_then(|id| id.as_str().map(str::to_owned)),
            surface.get("capability").and_then(|capability| capability.as_str().map(str::to_owned)),
        ) else {
            return Err(diagnostic(
                "Each game panel needs an identity and capability.",
                "ruleset_source_panel_invalid",
                &format!("$.ui_surfaces[{index}]"),
                Some(package),
                None,
            ));
        };
        state.capabilities.insert(capability);
        let merged = dict(vec![
            ("title".into(), text(&id)),
            ("icon".into(), text("\u{25c7}")),
            ("group".into(), Py::None),
            ("order".into(), Py::Int(index as i64)),
            ("default_size".into(), Py::Int(180)),
            ("minimum_size".into(), Py::Int(96)),
            ("default_placement".into(), text("docked")),
            ("allowed_placements".into(), list(vec![text("docked"), text("window")])),
            ("actions".into(), list(Vec::new())),
        ]);
        for (key, value) in surface.as_dict().expect("a dict").borrow().iter() {
            merged.set(key, value.clone());
        }
        surfaces.push(merged);
    }
    let procedures_source = source.get("procedures").unwrap_or_else(|| dict(Vec::new()));
    let procedures = mapping(&procedures_source, "$.procedures", Some(package))?;
    let runtime_tools = dict(state.runtime_tools.clone());
    let mut required = required_engine_features(&dict(vec![
        ("tools".into(), runtime_tools.clone()),
        ("procedures".into(), procedures.clone()),
    ]));
    if surfaces
        .iter()
        .any(|surface| surface.get("renderer").is_some_and(|renderer| renderer.eq_str("tactical_grid")))
    {
        required.insert(ENGINE_FEATURE_TACTICAL_GRID.into());
    }
    if source.contains("panel_layout") {
        required.insert(ENGINE_FEATURE_PANEL_WORKSPACES.into());
    }
    if let Some(schemas) = source.get("state_schemas") {
        required.insert(STARTING_STATE_FEATURE.into());
        if any_member(&schemas, |item| item.get("required").is_some_and(|value| value.is_true()))? {
            required.insert(REQUIRED_STARTING_STATE_FEATURE.into());
        }
        if any_member(&schemas, |item| item.get("visibility").is_some_and(|value| value.eq_str("engine")))? {
            required.insert(PRIVATE_STATE_FEATURE.into());
        }
    }
    let required_features: Vec<String> = required.into_iter().collect();

    let runtime = dict(vec![
        (
            "sylvia_gm_runtime".into(),
            dict(vec![("format".into(), Py::Int(1)), ("state_namespace".into(), text(package))]),
        ),
        ("features".into(), dict(Vec::new())),
        ("abilities".into(), dict(Vec::new())),
        ("tools".into(), runtime_tools),
        ("procedures".into(), procedures.clone()),
        ("definitions".into(), source.item("definitions")),
        ("narrative".into(), dict(Vec::new())),
    ]);
    if let Some(boundary) = source.get("model_decision_boundary") {
        runtime.set("model_decision_boundary", boundary);
    }
    let mut support_records = state.support_records.clone();
    support_records.sort_by_key(|record| record.item("id").as_str().unwrap_or_default().to_owned());
    let support = dict(vec![
        ("format_id".into(), text("sylvia-gm-gameplay-support")),
        ("format_version".into(), Py::Int(1)),
        ("records".into(), list(support_records)),
    ]);
    let dumped = pyyaml::safe_dump(&to_loaded(&runtime))
        .map_err(|error| DomainError::uncaught(error.class_name(), error.to_string()))?;
    let mut payloads: Payloads = vec![
        ("runtime/ruleset.yaml".into(), dumped.into_bytes()),
        ("support/gameplay-resolution.json".into(), json_bytes(&support)),
    ];
    if let Some(layout) = source.get("panel_layout") {
        payloads.push((PANEL_LAYOUT_PATH.into(), json_bytes(&layout)));
    }
    if let Some(tests) = source.get("tests") {
        payloads.push((RULESET_TESTS_PATH.into(), json_bytes(&tests)));
    }
    let mut prompt_fragments = Vec::new();
    let guide = source.item("operating_guide");
    if !matches!(guide, Py::None) {
        let Some(guide) = guide.as_str().filter(|guide| !pystr::strip(guide).is_empty()) else {
            return Err(diagnostic(
                "Operating guide must be non-empty text or null.",
                "ruleset_source_operating_guide_invalid",
                "$.operating_guide",
                Some(package),
                None,
            ));
        };
        let capability = format!("{package}.guidance");
        state.capabilities.insert(capability.clone());
        prompt_fragments.push(dict(vec![
            ("id".into(), text("gm-operating-guide")),
            ("capability".into(), text(&capability)),
        ]));
        payloads.push(("prompts/gm-operating-guide.md".into(), guide.as_bytes().to_vec()));
    }
    let source_sha = sha256_hex(source_bytes);
    let mut artifacts: Vec<String> = payloads.iter().map(|(path, _)| path.clone()).collect();
    artifacts.sort();
    let mut tool_names: Vec<String> = state
        .manifest_tools
        .iter()
        .map(|tool| tool.item("name").as_str().unwrap_or_default().to_owned())
        .collect();
    tool_names.sort();
    let mut surface_ids: Vec<String> =
        surfaces.iter().map(|surface| surface.item("id").as_str().unwrap_or_default().to_owned()).collect();
    surface_ids.sort();
    let mut procedure_names = procedures.keys();
    procedure_names.sort();
    let schema_ids: Vec<String> = py_list(&source.item("participant_artifacts"))?
        .iter()
        .filter(|item| item.as_dict().is_some())
        .filter_map(|item| item.get("schema_id").and_then(|id| id.as_str().map(str::to_owned)))
        .collect();
    let fragment_ids: Vec<String> =
        prompt_fragments.iter().map(|item| item.item("id").as_str().unwrap_or_default().to_owned()).collect();
    let capabilities = texts(state.capabilities.iter().cloned());
    let declarations = Json::from(serde_json::json!({
        "format_id": "sylvia-gm-package-content-declarations",
        "format_version": 1,
        "scope": {
            "scope_id": format!("{package}.gameplay"),
            "scope_kind": "bounded_package",
            "claim_status": "NOT_COMPLETE",
            "source_boundary_refs": [],
        },
        "source_authorities": [{
            "id": format!("{package}.source"),
            "document_id": format!("{package}.ruleset-source"),
            "version": source.item("version").as_str().unwrap_or_default(),
            "media_type": "application-yaml",
            "content_sha256": source_sha,
            "license_id": "NOASSERTION",
        }],
        "inclusions": [{
            "id": format!("{package}.gameplay-runtime"),
            "kind": "ruleset_runtime",
            "family": "ruleset-runtime",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": artifacts,
                "capabilities": capabilities.to_json(),
                "mechanics": [],
                "procedures": procedure_names,
                "prompt_fragments": fragment_ids,
                "schemas": schema_ids,
                "state_namespaces": [],
                "tools": tool_names,
                "ui_surfaces": surface_ids,
            },
            "evidence": {"tests": []},
        }],
        "exclusions": [],
    }));
    payloads.push(("content/content-declarations.json".into(), canonical_json_bytes(&declarations)));
    let mut manifest_tools = state.manifest_tools.clone();
    manifest_tools.sort_by_key(|tool| tool.item("name").as_str().unwrap_or_default().to_owned());
    let record = dict(vec![
        ("id".into(), text(package)),
        ("version".into(), source.item("version")),
        ("role".into(), text("primary")),
        ("content_kind".into(), text("ruleset")),
        ("metadata".into(), dict(vec![("label".into(), source.item("label"))])),
        (
            "provenance".into(),
            dict(vec![
                ("publisher".into(), source.item("publisher")),
                ("source_sha256".into(), text(&source_sha)),
            ]),
        ),
        (
            "compatibility".into(),
            dict(vec![
                ("host_contract".into(), Py::Int(1)),
                ("runtime_format".into(), Py::Int(1)),
                ("requires".into(), texts(required_features.iter().cloned())),
            ]),
        ),
        ("minimum_model_capability_tier".into(), text("sylvia.capability.t00")),
        ("dependencies".into(), list(Vec::new())),
        ("capabilities".into(), capabilities),
        ("tools".into(), list(manifest_tools)),
        ("prompt_fragments".into(), list(prompt_fragments)),
        ("ui_surfaces".into(), list(surfaces)),
        ("participant_artifacts".into(), source.item("participant_artifacts")),
        ("content_declarations".into(), text("content/content-declarations.json")),
    ]);
    if let Some(schemas) = source.get("state_schemas") {
        record.set("state_schemas", schemas);
    }
    let Json::Object(record) = record.to_json() else { unreachable!("a dict") };
    Ok(CompiledRulesetSource {
        draft: NormalizedPackageDraft::new(record, payloads, 4),
        required_engine_features: required_features,
    })
}
