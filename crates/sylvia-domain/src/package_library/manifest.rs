//! `manifest_from_record` and the declarations a package manifest carries.

use super::record::{array, exact_keys, object, safe_small_label, sorted_unique};
use super::{
    canonical_json_bytes, content_root_for_record, participant_schema_sha256, validate_portable_path_value,
    ExactPackageDependency, PackageFile,
};
use crate::authority::{owned_payload, validate_authority_identity_value, PackageRole};
use crate::content::validate_content_shape;
use crate::error::{DomainError, DomainResult};
use crate::inventory::inventory_holding_schema;
use crate::model_capability::validate_capability_tier_id;
use crate::pyops::{
    py_eq, py_get, py_hashable, py_in, py_index, py_set, py_set_equals, py_subscript, py_truthy, PyFault,
};
use crate::starting_state::{
    parse_state_schemas, StateSchema, PRIVATE_STATE_FEATURE, REQUIRED_STARTING_STATE_FEATURE,
    STARTING_STATE_FEATURE,
};
use sylvia_base::pystr::{casefold, repr};
use sylvia_base::{Json, JsonMap};

/// `PACKAGE_HOST_CONTRACT`.
pub const PACKAGE_HOST_CONTRACT: i64 = 1;
/// `PACKAGE_RUNTIME_FORMAT`.
pub const PACKAGE_RUNTIME_FORMAT: i64 = 1;
pub const ENGINE_FEATURE_SPATIAL_MUTATION: &str = "spatial_mutation.v1";
pub const ENGINE_FEATURE_SPATIAL_PATH_MOVING_ENTITY: &str = "spatial_path_moving_entity.v1";
pub const ENGINE_FEATURE_SPATIAL_CLEARANCE: &str = "spatial_clearance.v1";
pub const ENGINE_FEATURE_SHOT_EFFECTS: &str = "shot_effects.v1";
pub const ENGINE_FEATURE_PANEL_WORKSPACES: &str = "panel_workspaces.v1";
pub const ENGINE_FEATURE_TACTICAL_GRID: &str = "tactical_grid.v1";

/// `SUPPORTED_ENGINE_FEATURES`.
pub const SUPPORTED_ENGINE_FEATURES: &[&str] = &[
    "spatial_circle_overlap.v1",
    "spatial_circle_route.v1",
    "player_action_preflight.v1",
    "player_action_preflight_path.v1",
    PRIVATE_STATE_FEATURE,
    "collection_mechanics.v1",
    "public_results.v1",
    "isolated_participants.v1",
    ENGINE_FEATURE_SPATIAL_MUTATION,
    ENGINE_FEATURE_SPATIAL_PATH_MOVING_ENTITY,
    ENGINE_FEATURE_SPATIAL_CLEARANCE,
    ENGINE_FEATURE_SHOT_EFFECTS,
    ENGINE_FEATURE_PANEL_WORKSPACES,
    ENGINE_FEATURE_TACTICAL_GRID,
    STARTING_STATE_FEATURE,
    REQUIRED_STARTING_STATE_FEATURE,
    "participant_mutation.v1",
    "runtime_require.v1",
    "runtime_choice_sequences.v1",
    "character_rule_results.v1",
    "campaign_starting_events.v1",
    "runtime_immutable_collections.v1",
    "entity_creation.v1",
];

const UI_RENDERERS: &[&str] = &[
    "party",
    "encounter",
    "inventory",
    "objectives",
    "unit_list",
    "key_value",
    "text",
    "tactical_grid",
    "coordinate_map",
    "network_graph",
    "piece_table",
];
const UI_PLACEMENTS: &[&str] = &["docked", "floating", "window"];
/// `TACTICAL_UI_DYNAMIC_SELECTIONS`.
pub const TACTICAL_UI_DYNAMIC_SELECTIONS: &[&str] = &[
    "actor",
    "zone",
    "device",
    "destination",
    "path",
    "target",
    "aim_point",
    "area",
    "orientation",
    "declared_action",
];
/// `NETWORK_UI_DYNAMIC_SELECTIONS`.
pub const NETWORK_UI_DYNAMIC_SELECTIONS: &[&str] =
    &["actor", "node", "link", "route_step", "remote_function", "declared_action"];
/// `UI_DYNAMIC_SELECTIONS`: the union of the tactical and network selections. Python keeps a
/// `frozenset`; this lists the tactical selections, then the network-only ones.
pub const UI_DYNAMIC_SELECTIONS: &[&str] = &[
    "actor",
    "zone",
    "device",
    "destination",
    "path",
    "target",
    "aim_point",
    "area",
    "orientation",
    "declared_action",
    "node",
    "link",
    "route_step",
    "remote_function",
];

/// `_UI_RENDERER_SELECTIONS[renderer]`.
fn renderer_selections(renderer: &str) -> Option<&'static [&'static str]> {
    Some(match renderer {
        "piece_table" => &["piece"],
        "party" | "unit_list" => &["actor"],
        "inventory" => &["actor", "declared_action"],
        "coordinate_map" => &["actor", "target", "aim_point", "declared_action"],
        "tactical_grid" => TACTICAL_UI_DYNAMIC_SELECTIONS,
        "network_graph" => NETWORK_UI_DYNAMIC_SELECTIONS,
        _ => return None,
    })
}

/// `value` when it is a Python `int` (not a `bool` and not a float), at any JSON size.
fn python_int(value: &Json) -> Option<i128> {
    match value {
        Json::Number(number) if number.is_i64() => number.as_i64().map(i128::from),
        Json::Number(number) if number.is_u64() => number.as_u64().map(i128::from),
        _ => None,
    }
}

/// A `ValueError` from a fixed message.
fn value(message: &str) -> DomainError {
    DomainError::value(message)
}

/// A `TypeError` Python raises outside any handler (unhashable values and the like).
fn fault(_: PyFault) -> DomainError {
    DomainError::type_error("Python operator failed on a value of the wrong type")
}

fn text_in(value: &Json, options: &[&str]) -> bool {
    value.as_str().is_some_and(|text| options.contains(&text))
}

/// One state precondition of a package tool (`StatePredicate`).
#[derive(Debug, Clone, PartialEq)]
pub struct StatePredicate {
    pub namespace: String,
    pub key: String,
    pub equals: Json,
    pub negated: bool,
}

/// One package tool (`ToolDeclaration`).
#[derive(Debug, Clone, PartialEq)]
pub struct ToolDeclaration {
    pub name: String,
    pub capability: String,
    pub input_schema: JsonMap,
    pub state_predicates: Vec<StatePredicate>,
    pub turn_statuses: Vec<String>,
    pub writers: Vec<String>,
}

/// One prompt fragment or UI surface (`SurfaceDeclaration`). Prompt fragments use only the
/// identity and capability; the other fields keep Python's defaults.
#[derive(Debug, Clone, PartialEq)]
pub struct SurfaceDeclaration {
    pub identity: String,
    pub capability: String,
    pub title: Option<String>,
    pub icon: Option<String>,
    pub renderer: Option<String>,
    pub source: Option<JsonMap>,
    pub group: Option<String>,
    pub order: i64,
    pub default_size: i64,
    pub minimum_size: i64,
    pub default_placement: String,
    pub allowed_placements: Vec<String>,
    pub actions: Vec<JsonMap>,
}

impl SurfaceDeclaration {
    /// `SurfaceDeclaration(identity, capability)` with Python's defaults.
    pub fn plain(identity: String, capability: String) -> Self {
        Self {
            identity,
            capability,
            title: None,
            icon: None,
            renderer: None,
            source: None,
            group: None,
            order: 0,
            default_size: 180,
            minimum_size: 96,
            default_placement: "docked".into(),
            allowed_placements: vec!["docked".into()],
            actions: Vec::new(),
        }
    }
}

/// One participant artifact schema a package declares (`ParticipantArtifactDeclaration`).
#[derive(Debug, Clone, PartialEq)]
pub struct ParticipantArtifactDeclaration {
    pub kind: String,
    pub schema_id: String,
    pub schema_sha256: String,
    pub schema: JsonMap,
    pub entity_state_schema: Option<JsonMap>,
    pub initial_state_calculations: Vec<JsonMap>,
    pub initial_state_validation: Option<JsonMap>,
}

impl ParticipantArtifactDeclaration {
    /// `ParticipantArtifactDeclaration(...)` with Python's checks, in Python's order.
    pub fn new(
        kind: &Json,
        schema_id: &Json,
        schema_sha256: &Json,
        schema: JsonMap,
        entity_state_schema: Option<JsonMap>,
        initial_state_calculations: Vec<JsonMap>,
        initial_state_validation: Option<JsonMap>,
    ) -> DomainResult<Self> {
        let kind = validate_authority_identity_value(kind, "Participant artifact kind", false)?;
        let schema_id =
            validate_authority_identity_value(schema_id, "Participant artifact schema identity", false)?;
        let schema_sha256 =
            validate_authority_identity_value(schema_sha256, "Participant artifact schema SHA-256", true)?;
        if entity_state_schema.is_none()
            && (!initial_state_calculations.is_empty() || initial_state_validation.is_some())
        {
            return Err(value("Character calculations require a complete entity-state schema"));
        }
        Ok(Self {
            kind: kind.to_owned(),
            schema_id: schema_id.to_owned(),
            schema_sha256: schema_sha256.to_owned(),
            schema,
            entity_state_schema,
            initial_state_calculations,
            initial_state_validation,
        })
    }
}

/// One validated package record (`PackageManifest`).
#[derive(Debug, Clone, PartialEq)]
pub struct PackageManifest {
    pub package_id: String,
    pub version: String,
    pub role: PackageRole,
    pub content_sha256: String,
    /// `canonical_json_bytes(record)` as text.
    pub canonical_json: String,
    pub metadata: JsonMap,
    pub provenance: JsonMap,
    pub compatibility: JsonMap,
    pub minimum_model_capability_tier: String,
    pub dependencies: Vec<ExactPackageDependency>,
    pub capabilities: Vec<String>,
    pub tools: Vec<ToolDeclaration>,
    pub prompt_fragments: Vec<SurfaceDeclaration>,
    pub ui_surfaces: Vec<SurfaceDeclaration>,
    pub files: Vec<PackageFile>,
    pub format_version: i64,
    pub participant_artifacts: Vec<ParticipantArtifactDeclaration>,
    pub content_declarations_path: Option<String>,
    pub state_schemas: Vec<StateSchema>,
}

impl PackageManifest {
    /// `(package_id, version, content_sha256)`.
    pub fn identity(&self) -> (&str, &str, &str) {
        (&self.package_id, &self.version, &self.content_sha256)
    }
}

/// `_validate_mechanical_inventory_declaration(inventory, package_id)`.
fn validate_mechanical_inventory(inventory: &JsonMap, package_id: &str) -> DomainResult<()> {
    if inventory.len() != 4
        || !["format", "state", "surface_id", "tools"].iter().all(|k| inventory.contains_key(*k))
    {
        return Err(value("Mechanical inventory must declare only its state, surface and tools"));
    }
    let target = object(&inventory["state"], "Inventory state")?;
    if target.len() != 2
        || !target.contains_key("namespace")
        || !target.contains_key("key")
        || !py_eq(&target["namespace"], &Json::from(package_id))
    {
        return Err(value("Mechanical inventory must address the declaring package's state"));
    }
    validate_authority_identity_value(&target["key"], "Inventory state key", false)?;
    validate_authority_identity_value(&inventory["surface_id"], "Inventory surface", false)?;
    let names = array(&inventory["tools"], "Inventory tools")?;
    if !(1..=32).contains(&names.len()) {
        return Err(value("Mechanical inventory requires a bounded tool list"));
    }
    let mut identities = Vec::with_capacity(names.len());
    for name in names {
        identities.push(validate_authority_identity_value(name, "Inventory tool", false)?);
    }
    let mut unique = identities.clone();
    unique.sort_unstable();
    unique.dedup();
    if unique.len() != identities.len() {
        return Err(value("Mechanical inventory tool names must be unique"));
    }
    Ok(())
}

/// The outcome of the `try` block in `_validate_character_update_declaration`.
enum Bookkeeping {
    Raise(DomainError),
    /// `KeyError`, `TypeError` or `AttributeError`, which Python turns into "malformed".
    Caught,
}

impl From<PyFault> for Bookkeeping {
    fn from(error: PyFault) -> Self {
        match error {
            // `IndexError` is not in the `except (KeyError, TypeError, AttributeError)` tuple.
            PyFault::Index => Bookkeeping::Raise(fault(error)),
            _ => Bookkeeping::Caught,
        }
    }
}

impl From<DomainError> for Bookkeeping {
    fn from(error: DomainError) -> Self {
        Bookkeeping::Raise(error)
    }
}

/// `_validate_character_update_declaration(schema, package_id)`.
fn validate_character_update_declaration(schema: &JsonMap, package_id: &str) -> DomainResult<()> {
    const MARKER: &str = "x-sylvia-gm-character-update";
    let annotation = schema.get("x-sylvia-gm-inventory");
    if let Some(Json::Object(inventory)) = annotation {
        if inventory.get("format").is_some_and(|format| py_eq(format, &Json::from("mechanics"))) {
            if schema.contains_key(MARKER) {
                return Err(value("Mechanical inventory cannot also declare character bookkeeping"));
            }
            return validate_mechanical_inventory(inventory, package_id);
        }
    }
    if !schema.contains_key(MARKER) {
        if schema.contains_key("x-sylvia-gm-inventory") {
            return Err(value("Inventory must use the shared character bookkeeping declaration"));
        }
        return Ok(());
    }
    match character_update_body(schema, package_id) {
        Ok(()) => Ok(()),
        Err(Bookkeeping::Raise(error)) => Err(error),
        Err(Bookkeeping::Caught) => Err(value("Character bookkeeping declaration is malformed")),
    }
}

/// The `try` body of `_validate_character_update_declaration`.
fn character_update_body(schema: &JsonMap, package_id: &str) -> Result<(), Bookkeeping> {
    let schema_json = Json::Object(schema.clone());
    let target = &schema["x-sylvia-gm-character-update"];
    if !py_set_equals(target, &["namespace", "key"])?
        || !py_eq(py_index(target, "namespace")?, &Json::from(package_id))
    {
        return Err(value("Bookkeeping must address the declaring package's state").into());
    }
    validate_authority_identity_value(py_index(target, "key")?, "Character state key", false)?;
    let properties = py_index(&schema_json, "properties")?;
    let updates = py_index(properties, "updates")?;
    let item = py_index(updates, "items")?;
    let fields = py_index(py_index(item, "properties")?, "fields")?;
    let get = |value: &Json, key: &str| -> Result<Json, PyFault> {
        Ok(py_get(value, key)?.cloned().unwrap_or(Json::Null))
    };
    let no_required = Json::Array(Vec::new());
    // Python evaluates this `or` chain left to right and stops at the first true test, so a
    // later operand that would raise is only reached when every earlier one is false.
    let malformed_shape = !py_eq(&get(&schema_json, "type")?, &Json::from("object"))
        || schema.get("additionalProperties") != Some(&Json::Bool(false))
        || !py_set_equals(properties, &["updates"])?
        || !py_eq(&get(&schema_json, "required")?, &Json::Array(vec![Json::from("updates")]))
        || !py_eq(&get(updates, "type")?, &Json::from("array"))
        || !py_eq(&get(item, "type")?, &Json::from("object"))
        || !py_set_equals(py_index(item, "properties")?, &["participant_id", "fields"])?
        || !py_set_equals(py_get(item, "required")?.unwrap_or(&no_required), &["participant_id", "fields"])?
        || py_get(item, "additionalProperties")? != Some(&Json::Bool(false))
        || !py_eq(
            &get(py_index(py_index(item, "properties")?, "participant_id")?, "type")?,
            &Json::from("string"),
        )
        || !py_eq(&get(fields, "type")?, &Json::from("object"))
        || !py_truthy(&get(fields, "properties")?)
        || py_get(fields, "additionalProperties")? != Some(&Json::Bool(false));
    if malformed_shape {
        return Err(value("Character bookkeeping schema must declare bounded editable fields").into());
    }
    let Some(inventory) = schema.get("x-sylvia-gm-inventory").filter(|v| !v.is_null()) else { return Ok(()) };
    let field_properties = py_index(fields, "properties")?;
    let bad_inventory = match inventory {
        Json::Object(map) => {
            let keys_ok = py_set_equals(inventory, &["field", "format"])?
                || py_set_equals(inventory, &["field", "format", "surface_id"])?;
            let format = map.get("format").cloned().unwrap_or(Json::Null);
            let field = map.get("field").cloned().unwrap_or(Json::Null);
            !keys_ok
                || {
                    if !py_hashable(&format) {
                        return Err(Bookkeeping::Caught);
                    }
                    !text_in(&format, &["string_list", "holdings"])
                }
                || !py_in(&field, field_properties)?
        }
        _ => true,
    };
    if bad_inventory {
        return Err(value("Ruleset inventory must identify a declared editable inventory field").into());
    }
    let map = inventory.as_object().expect("checked above");
    if map.contains_key("surface_id") {
        validate_authority_identity_value(&map["surface_id"], "Inventory surface", false)?;
    }
    let inventory_field = py_subscript(field_properties, &map["field"])?;
    let items = match py_get(inventory_field, "items")? {
        Some(items) => items.clone(),
        None => Json::Object(JsonMap::new()),
    };
    let expected_type = if py_eq(&map["format"], &Json::from("string_list")) { "string" } else { "object" };
    if !py_eq(&get(inventory_field, "type")?, &Json::from("array"))
        || !py_eq(&get(&items, "type")?, &Json::from(expected_type))
    {
        return Err(value("Inventory projection format must match the editable field schema").into());
    }
    if py_eq(&map["format"], &Json::from("holdings"))
        && !py_eq(py_index(inventory_field, "items")?, inventory_holding_schema())
    {
        return Err(value("Structured inventory must use the shared inventory holding schema").into());
    }
    Ok(())
}

/// `_validate_inventory_targets(tools, surfaces)`.
fn validate_inventory_targets(
    tools: &[ToolDeclaration],
    surfaces: &[SurfaceDeclaration],
) -> DomainResult<()> {
    let declarations: Vec<&ToolDeclaration> =
        tools.iter().filter(|tool| tool.input_schema.contains_key("x-sylvia-gm-inventory")).collect();
    if declarations.len() > 1 {
        return Err(value("Ruleset inventory declaration is ambiguous"));
    }
    let Some(declaration) = declarations.first() else { return Ok(()) };
    let inventory = object(&declaration.input_schema["x-sylvia-gm-inventory"], "Ruleset inventory")?;
    if !inventory.get("format").is_some_and(|format| py_eq(format, &Json::from("mechanics"))) {
        return Ok(());
    }
    let raw_names = inventory.get("tools").ok_or_else(|| DomainError::type_error("KeyError: 'tools'"))?;
    let mut names = Vec::new();
    for name in array(raw_names, "Inventory tools")? {
        names.push(validate_authority_identity_value(name, "Inventory tool", false)?);
    }
    for name in &names {
        let tool = tools.iter().rev().find(|tool| tool.name == *name);
        match tool {
            Some(tool) if !tool.input_schema.contains_key("x-sylvia-gm-character-update") => {
                if !tool.writers.iter().any(|writer| writer == "dm") {
                    return Err(value("Mechanical inventory tools must allow the GM writer"));
                }
            }
            _ => return Err(value("Mechanical inventory must reference declared mechanical tools")),
        }
    }
    if !names.contains(&declaration.name.as_str()) {
        return Err(value("Mechanical inventory must include its declaring tool"));
    }
    let surface_id = inventory.get("surface_id").cloned().unwrap_or(Json::Null);
    if !surfaces.iter().any(|surface| py_eq(&Json::from(surface.identity.as_str()), &surface_id)) {
        return Err(value("Mechanical inventory must reference a declared presentation surface"));
    }
    Ok(())
}

/// `_surface_source(value, depth=...)`: one bounded owner-projection query.
fn surface_source(value: &Json, depth: usize) -> DomainResult<JsonMap> {
    if depth > 3 {
        return Err(self::value("UI surface source nesting is too deep"));
    }
    let source = object(value, "UI surface source")?;
    let kind = source.get("kind").cloned().unwrap_or(Json::Null);
    let is = |name: &str| py_eq(&kind, &Json::from(name));
    if is("literal") {
        exact_keys(source, &["kind", "value"], "UI literal source")?;
        owned_payload(&source["value"])?;
    } else if is("state") {
        exact_keys(source, &["kind", "namespace", "key"], "UI state source")?;
        validate_authority_identity_value(&source["namespace"], "UI state namespace", false)?;
        validate_authority_identity_value(&source["key"], "UI state key", false)?;
    } else if is("participants") {
        exact_keys(source, &["kind", "kinds"], "UI participant source")?;
        let kinds = array(&source["kinds"], "UI participant kinds")?;
        if kinds.is_empty() || kinds.len() > 16 || kinds.iter().any(|item| !item.is_string()) {
            return Err(self::value("UI participant source kinds are invalid"));
        }
        for item in kinds {
            validate_authority_identity_value(item, "UI participant kind", false)?;
        }
        let mut unique: Vec<&str> = kinds.iter().filter_map(Json::as_str).collect();
        unique.sort_unstable();
        unique.dedup();
        if unique.len() != kinds.len() {
            return Err(self::value("UI participant source kinds must be unique"));
        }
    } else if is("campaign_plan") {
        exact_keys(source, &["kind", "path"], "UI Campaign Plan source")?;
        let path = array(&source["path"], "UI Campaign Plan path")?;
        if path.is_empty() || path.len() > 16 || path.iter().any(|item| !item.is_string()) {
            return Err(self::value("UI Campaign Plan path is invalid"));
        }
        for item in path {
            validate_authority_identity_value(item, "UI Campaign Plan path component", false)?;
        }
    } else if is("keyed_values") {
        exact_keys(source, &["kind", "entries"], "UI keyed values source")?;
        let entries = array(&source["entries"], "UI keyed values entries")?;
        if entries.is_empty() || entries.len() > 16 {
            return Err(self::value("UI keyed values entry count is invalid"));
        }
        for entry in entries {
            let entry = object(entry, "UI keyed values entry")?;
            exact_keys(entry, &["key", "value"], "UI keyed values entry")?;
            surface_source(&entry["key"], depth + 1)?;
            surface_source(&entry["value"], depth + 1)?;
        }
    } else if is("object") {
        exact_keys(source, &["kind", "fields"], "UI object source")?;
        let fields = object(&source["fields"], "UI object source fields")?;
        if fields.is_empty() || fields.len() > 16 {
            return Err(self::value("UI object source field count is invalid"));
        }
        for (name, field_source) in fields {
            validate_authority_identity_value(&Json::from(name.as_str()), "UI object source field", false)?;
            surface_source(field_source, depth + 1)?;
        }
    } else {
        return Err(self::value("UI surface source kind is unsupported"));
    }
    Ok(source.clone())
}

/// Python `value in a_frozenset` of strings: `TypeError` for an unhashable value.
fn in_names(value: &Json, names: &[&str]) -> DomainResult<bool> {
    if !py_hashable(value) {
        return Err(fault(PyFault::Type));
    }
    Ok(text_in(value, names))
}

/// `set(values)` of hashable values, `TypeError` otherwise; de-duplicated with `==`.
fn hashable_set(values: &[&Json]) -> DomainResult<Vec<Json>> {
    py_set(&Json::Array(values.iter().map(|v| (*v).clone()).collect())).map_err(fault)
}

/// `_ui_surface(value, capabilities, tools)`.
fn ui_surface(value: &Json, capabilities: &[String], tools: &[String]) -> DomainResult<SurfaceDeclaration> {
    let item = object(value, "UI surface")?;
    validate_content_shape("panel_declaration", value, Some("package.json:ui_surfaces"))?;
    exact_keys(
        item,
        &[
            "id",
            "capability",
            "title",
            "icon",
            "renderer",
            "source",
            "group",
            "order",
            "default_size",
            "minimum_size",
            "default_placement",
            "allowed_placements",
            "actions",
        ],
        "UI surface",
    )?;
    let identity = validate_authority_identity_value(&item["id"], "UI surface identity", false)?;
    let capability = validate_authority_identity_value(&item["capability"], "UI surface capability", false)?;
    if !capabilities.iter().any(|known| known == capability) {
        return Err(self::value("Package surface requires one declared capability"));
    }
    let title = safe_small_label(&item["title"], "UI surface title")?;
    let icon = safe_small_label(&item["icon"], "UI surface icon")?;
    let renderer_value = &item["renderer"];
    if !in_names(renderer_value, UI_RENDERERS)? {
        return Err(self::value("UI surface renderer is unsupported"));
    }
    let renderer = renderer_value.as_str().expect("a listed renderer is text");
    let group = match &item["group"] {
        Json::Null => None,
        other => Some(validate_authority_identity_value(other, "UI group", false)?.to_owned()),
    };
    let (Some(order), Some(default_size), Some(minimum_size)) =
        (python_int(&item["order"]), python_int(&item["default_size"]), python_int(&item["minimum_size"]))
    else {
        return Err(self::value("UI surface sizes and order must be integers"));
    };
    if !(0..=10_000).contains(&order)
        || !(96 <= minimum_size && minimum_size <= default_size && default_size <= 2_000)
    {
        return Err(self::value("UI surface sizes or order are outside supported bounds"));
    }
    // In bounds, so each fits an i64.
    let (order, default_size, minimum_size) = (order as i64, default_size as i64, minimum_size as i64);
    let default_placement = &item["default_placement"];
    let placements = array(&item["allowed_placements"], "UI allowed placements")?;
    let placement_refs: Vec<&Json> = placements.iter().collect();
    let invalid_placements = placements.is_empty()
        || hashable_set(&placement_refs)?.len() != placements.len()
        || {
            let mut any_bad = false;
            for placement in placements {
                if !in_names(placement, UI_PLACEMENTS)? {
                    any_bad = true;
                    break;
                }
            }
            any_bad
        }
        || !placements.iter().any(|placement| py_eq(placement, default_placement));
    if invalid_placements {
        return Err(self::value("UI surface placement declaration is invalid"));
    }
    let mut actions: Vec<JsonMap> = Vec::new();
    for action_value in array(&item["actions"], "UI surface actions")? {
        let action = object(action_value, "UI surface action")?;
        let mut action_fields = vec!["id", "label", "intent", "params"];
        let binding_values: Option<Vec<&Json>> = match action.get("bindings") {
            Some(Json::Object(bindings)) => Some(bindings.values().collect()),
            _ => None,
        };
        let bound = |name: &str| {
            binding_values.as_ref().is_some_and(|values| values.iter().any(|v| py_eq(v, &Json::from(name))))
        };
        if action.contains_key("bindings") {
            action_fields.push("bindings");
        }
        if action.contains_key("actor_reference") {
            action_fields.push("actor_reference");
            if renderer != "coordinate_map" || binding_values.is_none() || !bound("actor") {
                return Err(self::value("Actor references require a coordinate map actor binding"));
            }
        }
        if action.contains_key("target_reference") {
            action_fields.push("target_reference");
            let same_reference = py_eq(
                action.get("target_reference").unwrap_or(&Json::Null),
                action.get("actor_reference").unwrap_or(&Json::Null),
            );
            if renderer != "coordinate_map" || binding_values.is_none() || !bound("target") || !same_reference
            {
                return Err(self::value("Target references require the coordinate map actor reference"));
            }
        }
        if action.contains_key("spatial_preview") {
            action_fields.push("spatial_preview");
            // `set(action.get('bindings', {}).values())`.
            let sources = match action.get("bindings") {
                None => Vec::new(),
                Some(Json::Object(bindings)) => hashable_set(&bindings.values().collect::<Vec<_>>())?,
                Some(_) => return Err(DomainError::type_error("'bindings' has no values()")),
            };
            let has = |name: &str| sources.iter().any(|source| py_eq(source, &Json::from(name)));
            if !["coordinate_map", "tactical_grid"].contains(&renderer)
                || !has("actor")
                || !(has("target") || has("aim_point"))
            {
                return Err(self::value("Segment previews require declared spatial endpoints"));
            }
        }
        exact_keys(action, &action_fields, "UI surface action")?;
        validate_authority_identity_value(&action["id"], "UI surface action identity", false)?;
        safe_small_label(&action["label"], "UI surface action label")?;
        let intent = validate_authority_identity_value(&action["intent"], "UI surface action intent", false)?;
        if !tools.iter().any(|tool| tool == intent) {
            return Err(self::value("UI surface action references an undeclared package tool"));
        }
        let params = object(&action["params"], "UI surface action parameters")?;
        if action.contains_key("bindings") {
            let Some(selections) = renderer_selections(renderer) else {
                return Err(self::value("UI selection bindings require an interactive renderer"));
            };
            let bindings = object(&action["bindings"], "UI surface selection bindings")?;
            let values: Vec<&Json> = bindings.values().collect();
            let mut invalid = bindings.is_empty()
                || bindings.len() > 16
                || bindings.keys().any(|key| params.contains_key(key));
            if !invalid {
                for source in &values {
                    if !source.is_string() || !in_names(source, selections)? {
                        invalid = true;
                        break;
                    }
                }
            }
            if !invalid && hashable_set(&values)?.len() != bindings.len() {
                let actor_exception = renderer == "coordinate_map"
                    && action.get("actor_reference").is_some_and(py_truthy)
                    && values.iter().all(|source| {
                        py_eq(source, &Json::from("actor"))
                            || values.iter().filter(|other| py_eq(other, source)).count() == 1
                    });
                invalid = !actor_exception;
            }
            if invalid {
                return Err(self::value("UI surface selection binding is invalid"));
            }
            for parameter in bindings.keys() {
                validate_authority_identity_value(
                    &Json::from(parameter.as_str()),
                    "UI surface bound parameter",
                    false,
                )?;
            }
        }
        actions.push(action.clone());
    }
    if actions.len() > 16 {
        return Err(self::value("UI surface declares too many actions"));
    }
    let mut actor_references: Vec<Json> = Vec::new();
    for action in &actions {
        let binds_actor = match action.get("bindings") {
            Some(Json::Object(bindings)) => bindings.values().any(|v| py_eq(v, &Json::from("actor"))),
            _ => false,
        };
        if binds_actor {
            let reference = action.get("actor_reference").cloned().unwrap_or(Json::Null);
            if !py_hashable(&reference) {
                return Err(fault(PyFault::Type));
            }
            if !actor_references.iter().any(|existing| py_eq(existing, &reference)) {
                actor_references.push(reference);
            }
        }
    }
    if actor_references.len() > 1 {
        return Err(self::value("Coordinate map actions must share one actor reference"));
    }
    Ok(SurfaceDeclaration {
        identity: identity.to_owned(),
        capability: capability.to_owned(),
        title: Some(title.to_owned()),
        icon: Some(icon.to_owned()),
        renderer: Some(renderer.to_owned()),
        source: Some(surface_source(&item["source"], 0)?),
        group,
        order,
        default_size,
        minimum_size,
        default_placement: default_placement.as_str().unwrap_or_default().to_owned(),
        allowed_placements: placements.iter().filter_map(Json::as_str).map(str::to_owned).collect(),
        actions,
    })
}

const BASE_KEYS: &[&str] = &[
    "id",
    "version",
    "role",
    "metadata",
    "provenance",
    "compatibility",
    "minimum_model_capability_tier",
    "dependencies",
    "capabilities",
    "tools",
    "prompt_fragments",
    "ui_surfaces",
    "files",
    "content_sha256",
];

/// `manifest_from_record(record, format_version=...)`.
pub fn manifest_from_record(
    record_value: &Json,
    format_version: Option<i64>,
) -> DomainResult<PackageManifest> {
    let record = object(record_value, "Package record")?;
    let format_version = format_version.unwrap_or(if record.contains_key("content_kind") {
        4
    } else if record.contains_key("content_declarations") {
        3
    } else if record.contains_key("participant_artifacts") {
        2
    } else {
        1
    });
    if !(1..=4).contains(&format_version) {
        return Err(value("Package record format is unsupported"));
    }
    let mut expected: Vec<&str> = BASE_KEYS.to_vec();
    if format_version >= 3 {
        expected.extend(["participant_artifacts", "content_declarations"]);
        if format_version == 4 {
            expected.push("content_kind");
            if record.contains_key("state_schemas") {
                expected.push("state_schemas");
            }
        }
    } else if format_version == 2 && record.contains_key("participant_artifacts") {
        expected.push("participant_artifacts");
    }
    exact_keys(record, &expected, "Package record")?;
    let package_id = validate_authority_identity_value(&record["id"], "Package identity", false)?.to_owned();
    let version = validate_authority_identity_value(&record["version"], "Package version", false)?.to_owned();
    let content_sha =
        validate_authority_identity_value(&record["content_sha256"], "Package content SHA-256", true)?;
    if content_root_for_record(record) != content_sha {
        return Err(value("Package content-root claim does not match its canonical record"));
    }
    let role_text = validate_authority_identity_value(&record["role"], "Package role", false)?;
    let role = PackageRole::parse(role_text)
        .ok_or_else(|| DomainError::value(format!("{} is not a valid PackageRole", repr(role_text))))?;
    if format_version == 4 {
        let content_kind = &record["content_kind"];
        let is_ruleset = py_eq(content_kind, &Json::from("ruleset"));
        if !text_in(content_kind, &["ruleset", "campaign_setting", "support"])
            || (role == PackageRole::Primary) != is_ruleset
        {
            return Err(value("Package content kind does not match its role"));
        }
    }
    let metadata = object(&record["metadata"], "Package metadata")?;
    exact_keys(metadata, &["label"], "Package metadata")?;
    safe_small_label(&metadata["label"], "Package label")?;
    let provenance = object(&record["provenance"], "Package provenance")?;
    exact_keys(provenance, &["publisher", "source_sha256"], "Package provenance")?;
    safe_small_label(&provenance["publisher"], "Package publisher")?;
    validate_authority_identity_value(&provenance["source_sha256"], "Package source SHA-256", true)?;
    let compatibility = object(&record["compatibility"], "Package compatibility")?;
    exact_keys(compatibility, &["host_contract", "runtime_format", "requires"], "Package compatibility")?;
    if !py_eq(&compatibility["host_contract"], &Json::from(PACKAGE_HOST_CONTRACT)) {
        return Err(value("Package host contract is unsupported"));
    }
    if !py_eq(&compatibility["runtime_format"], &Json::from(PACKAGE_RUNTIME_FORMAT)) {
        return Err(value("Package runtime format is unsupported"));
    }
    let required_features = array(&compatibility["requires"], "Package engine requirements")?;
    let feature_texts: Vec<&str> = required_features.iter().filter_map(Json::as_str).collect();
    let mut unique_features = feature_texts.clone();
    unique_features.sort_unstable();
    unique_features.dedup();
    if feature_texts.len() != required_features.len()
        || feature_texts.iter().any(|item| item.is_empty())
        || unique_features.len() != required_features.len()
    {
        return Err(value("Package engine requirements are invalid"));
    }
    if let Some(unsupported) = unique_features.iter().find(|item| !SUPPORTED_ENGINE_FEATURES.contains(item)) {
        return Err(DomainError::value(format!("Package engine feature is unsupported: {unsupported}")));
    }
    let requires = |feature: &str| feature_texts.contains(&feature);
    let mut state_schemas = Vec::new();
    if let Some(declared) = record.get("state_schemas") {
        if role != PackageRole::Primary || !requires(STARTING_STATE_FEATURE) {
            return Err(value("Starting-state schemas require a Ruleset and declared host support"));
        }
        state_schemas = parse_state_schemas(declared)?;
        if state_schemas.iter().any(|item| item.visibility == "engine") && !requires(PRIVATE_STATE_FEATURE) {
            return Err(value("Private state needs declared host support"));
        }
        if state_schemas.iter().any(|item| item.required) && !requires(REQUIRED_STARTING_STATE_FEATURE) {
            return Err(value("Required starting-state keys need declared host support"));
        }
    }
    let minimum_model_capability_tier =
        validate_capability_tier_id(&record["minimum_model_capability_tier"])?.to_owned();

    let mut dependencies: Vec<ExactPackageDependency> = Vec::new();
    for dependency in array(&record["dependencies"], "Package dependencies")? {
        let item = object(dependency, "Package dependency")?;
        exact_keys(item, &["namespace", "kind", "id", "version", "content_sha256"], "Package dependency")?;
        let text = |name: &str, label: &str| -> DomainResult<String> {
            Ok(validate_authority_identity_value(&item[name], label, name == "content_sha256")?.to_owned())
        };
        let namespace = text("namespace", "Dependency namespace")?;
        let kind = text("kind", "Dependency kind")?;
        let id = text("id", "Dependency package identity")?;
        let dependency_version = text("version", "Dependency version")?;
        let sha = text("content_sha256", "Dependency content SHA-256")?;
        dependencies.push(ExactPackageDependency::new(namespace, kind, id, dependency_version, sha)?);
    }
    let mut namespaces: Vec<&str> = dependencies.iter().map(ExactPackageDependency::namespace).collect();
    namespaces.sort_unstable();
    namespaces.dedup();
    if namespaces.len() != dependencies.len() {
        return Err(value("Dependency namespaces must be unique within a package"));
    }

    let raw_capabilities = array(&record["capabilities"], "Capabilities")?;
    if raw_capabilities.iter().any(|item| !item.is_string()) {
        return Err(value("Capabilities must be strings"));
    }
    let mut capabilities = Vec::with_capacity(raw_capabilities.len());
    for capability in raw_capabilities {
        capabilities
            .push(validate_authority_identity_value(capability, "Package capability", false)?.to_owned());
    }
    if !sorted_unique(&capabilities) {
        return Err(value("Capabilities must be sorted and unique"));
    }

    let mut tools: Vec<ToolDeclaration> = Vec::new();
    for tool in array(&record["tools"], "Package tools")? {
        let item = object(tool, "Package tool")?;
        exact_keys(
            item,
            &["name", "capability", "input_schema", "state_predicates", "turn_statuses", "writers"],
            "Package tool",
        )?;
        let name = validate_authority_identity_value(&item["name"], "Package tool name", false)?.to_owned();
        let capability =
            validate_authority_identity_value(&item["capability"], "Tool capability", false)?.to_owned();
        if !capabilities.contains(&capability) {
            return Err(value("Every tool requires one declared package capability"));
        }
        let mut predicates = Vec::new();
        for predicate_value in array(&item["state_predicates"], "State predicates")? {
            let predicate = object(predicate_value, "State predicate")?;
            let shape_ok = |keys: &[&str]| {
                predicate.len() == keys.len() && keys.iter().all(|k| predicate.contains_key(*k))
            };
            if !shape_ok(&["namespace", "key", "equals"])
                && !shape_ok(&["namespace", "key", "equals", "negated"])
            {
                return Err(value("State predicate fields do not match the portable format"));
            }
            let negated = match predicate.get("negated") {
                None => false,
                Some(Json::Bool(flag)) => *flag,
                Some(_) => return Err(value("State predicate negated flag must be boolean")),
            };
            predicates.push(StatePredicate {
                namespace: validate_authority_identity_value(
                    &predicate["namespace"],
                    "State namespace",
                    false,
                )?
                .to_owned(),
                key: validate_authority_identity_value(&predicate["key"], "State key", false)?.to_owned(),
                equals: predicate["equals"].clone(),
                negated,
            });
        }
        let turns = array(&item["turn_statuses"], "TURN statuses")?;
        let writers = array(&item["writers"], "Writers")?;
        if turns.iter().any(|value| !text_in(value, &["open", "completed"])) {
            return Err(value("Tool TURN predicate is unsupported"));
        }
        if writers.iter().any(|value| !text_in(value, &["builder", "dm"])) {
            return Err(value("Tool writer predicate is unsupported"));
        }
        let input_schema = owned_payload(&item["input_schema"])?;
        validate_character_update_declaration(&input_schema, &package_id)?;
        tools.push(ToolDeclaration {
            name,
            capability,
            input_schema,
            state_predicates: predicates,
            turn_statuses: turns.iter().filter_map(Json::as_str).map(str::to_owned).collect(),
            writers: writers.iter().filter_map(Json::as_str).map(str::to_owned).collect(),
        });
    }
    let mut tool_names: Vec<&str> = tools.iter().map(|tool| tool.name.as_str()).collect();
    tool_names.sort_unstable();
    tool_names.dedup();
    if tool_names.len() != tools.len() {
        return Err(value("Package tool names must be unique"));
    }

    let no_artifacts = Json::Array(Vec::new());
    let mut participant_artifacts: Vec<ParticipantArtifactDeclaration> = Vec::new();
    for artifact in
        array(record.get("participant_artifacts").unwrap_or(&no_artifacts), "Participant artifacts")?
    {
        let item = object(artifact, "Participant artifact")?;
        let has_state_contract =
            item.contains_key("entity_state_schema") || item.contains_key("initial_state_calculations");
        let validation = match item.get("initial_state_validation") {
            Some(raw) => Some(object(raw, "Character starting-state validation")?.clone()),
            None => None,
        };
        let mut expected = vec!["kind", "schema_id", "schema_sha256", "schema"];
        if has_state_contract {
            expected.extend(["entity_state_schema", "initial_state_calculations"]);
        }
        if validation.is_some() {
            expected.push("initial_state_validation");
        }
        exact_keys(item, &expected, "Participant artifact")?;
        let schema = object(&item["schema"], "Participant artifact schema")?;
        let entity_state_schema = if has_state_contract {
            Some(object(&item["entity_state_schema"], "Character entity-state schema")?.clone())
        } else {
            None
        };
        let no_steps = Json::Array(Vec::new());
        let mut calculations = Vec::new();
        for step in array(
            item.get("initial_state_calculations").unwrap_or(&no_steps),
            "Character starting-state calculations",
        )? {
            calculations.push(object(step, "Character starting-state calculation")?.clone());
        }
        let schema_id = validate_authority_identity_value(
            &item["schema_id"],
            "Participant artifact schema identity",
            false,
        )?;
        if !py_eq(schema.get("$id").unwrap_or(&Json::Null), &Json::from(schema_id)) {
            return Err(value("Participant artifact schema identity does not match its schema"));
        }
        let schema_sha256 = validate_authority_identity_value(
            &item["schema_sha256"],
            "Participant artifact schema SHA-256",
            true,
        )?;
        let calculation_values: Vec<Json> = calculations.iter().cloned().map(Json::Object).collect();
        if participant_schema_sha256(
            schema,
            entity_state_schema.as_ref(),
            &calculation_values,
            validation.as_ref(),
        )? != schema_sha256
        {
            return Err(value("Participant artifact schema hash does not match canonical schema"));
        }
        participant_artifacts.push(ParticipantArtifactDeclaration::new(
            &item["kind"],
            &item["schema_id"],
            &item["schema_sha256"],
            schema.clone(),
            entity_state_schema,
            calculations,
            validation,
        )?);
    }
    let mut kinds: Vec<&str> = participant_artifacts.iter().map(|item| item.kind.as_str()).collect();
    kinds.sort_unstable();
    kinds.dedup();
    if kinds.len() != participant_artifacts.len() {
        return Err(value("Participant artifact kinds must be unique"));
    }

    let content_declarations_path = if format_version >= 3 {
        Some(validate_portable_path_value(&record["content_declarations"])?.to_owned())
    } else {
        None
    };

    let mut files: Vec<PackageFile> = Vec::new();
    for file in array(&record["files"], "Package files")? {
        let item = object(file, "Package file")?;
        exact_keys(item, &["path", "bytes", "sha256"], "Package file")?;
        let Some(byte_count) = python_int(&item["bytes"]) else {
            return Err(value("Package file byte count must be an integer"));
        };
        files.push(PackageFile::from_record_values(&item["path"], byte_count, &item["sha256"])?);
    }
    let paths: Vec<&str> = files.iter().map(PackageFile::path).collect();
    if !paths.windows(2).all(|pair| pair[0] <= pair[1]) {
        return Err(value("Package file index must be sorted"));
    }
    let mut folded: Vec<String> = paths.iter().map(|path| casefold(path)).collect();
    folded.sort_unstable();
    folded.dedup();
    if folded.len() != files.len() {
        return Err(value("Package file paths collide"));
    }
    if let Some(declarations) = &content_declarations_path {
        if !paths.contains(&declarations.as_str()) {
            return Err(value("Package content declarations are not indexed as a package file"));
        }
    }

    let tool_names: Vec<String> = tools.iter().map(|tool| tool.name.clone()).collect();
    let ui_surfaces = surfaces(record, "ui_surfaces", &capabilities, &tool_names)?;
    validate_inventory_targets(&tools, &ui_surfaces)?;
    let prompt_fragments = surfaces(record, "prompt_fragments", &capabilities, &tool_names)?;
    Ok(PackageManifest {
        package_id,
        version,
        role,
        content_sha256: content_sha.to_owned(),
        canonical_json: String::from_utf8(canonical_json_bytes(record_value))
            .expect("canonical bytes are UTF-8"),
        metadata: metadata.clone(),
        provenance: provenance.clone(),
        compatibility: compatibility.clone(),
        minimum_model_capability_tier,
        dependencies,
        capabilities,
        tools,
        prompt_fragments,
        ui_surfaces,
        files,
        format_version,
        participant_artifacts,
        content_declarations_path,
        state_schemas,
    })
}

/// `surfaces(field)` inside `manifest_from_record`.
fn surfaces(
    record: &JsonMap,
    field: &str,
    capabilities: &[String],
    tools: &[String],
) -> DomainResult<Vec<SurfaceDeclaration>> {
    let mut values: Vec<SurfaceDeclaration> = Vec::new();
    for raw in array(&record[field], field)? {
        if field == "ui_surfaces" {
            values.push(ui_surface(raw, capabilities, tools)?);
            continue;
        }
        let item = object(raw, field)?;
        exact_keys(item, &["id", "capability"], field)?;
        let identity = validate_authority_identity_value(&item["id"], &format!("{field} identity"), false)?;
        let capability =
            validate_authority_identity_value(&item["capability"], &format!("{field} capability"), false)?;
        if !capabilities.iter().any(|known| known == capability) {
            return Err(value("Package surface requires one declared capability"));
        }
        values.push(SurfaceDeclaration::plain(identity.to_owned(), capability.to_owned()));
    }
    let mut identities: Vec<&str> = values.iter().map(|value| value.identity.as_str()).collect();
    identities.sort_unstable();
    identities.dedup();
    if identities.len() != values.len() {
        return Err(value("Package surface identities must be unique"));
    }
    Ok(values)
}
