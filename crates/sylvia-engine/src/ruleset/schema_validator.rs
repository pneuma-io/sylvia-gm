//! `runtime/_schema_validator.py`: structural and cross-reference validation of a Ruleset's
//! executable layer (tools, procedures, definitions, model-facing declarations).
//!
//! Every function returns Python's list of messages, in Python's order, or the `TypeError`
//! Python raises on the same data (an unhashable value where a set lookup happens, keys that
//! cannot be sorted together, a non-text value given to `re.match`). Messages are copied from the
//! snapshot byte for byte. Two places depend on Python's per-process string hashing and are
//! fixed here (`differences.md`): the "Did you mean" suggestion breaks ties by sorted order, and
//! `_iter_effects` visits handler bodies in sorted handler order.

use super::parameter_declarations::{resolve_parameter_declarations, ParameterDeclarationError};
use super::pydata::{
    self, anchored_match, contains, has_keys, in_list, in_str_keys, in_str_set, is_int, keys, keys_equal,
    keys_except, not_text, number_lt, repr, sorted, sorted_repr, to_str, truthy, unhashable,
};
use crate::expr::parse_expression;
use crate::expr::text_cache::TextCache;
use crate::pyerror::{PyError, PyErrorKind, PyResult};
use sylvia_base::pystr::strip;
use sylvia_base::pyyaml::{self, Data, YamlError};

type Errors = Vec<String>;

/// `VALID_PARAM_TYPES`.
pub const VALID_PARAM_TYPES: &[&str] =
    &["entity_id", "string", "integer", "number", "boolean", "array", "object", "enum", "entity_id_list"];

/// `VALID_EFFECT_KEYS` (`frozenset(_EFFECT_HANDLERS)`), in sorted order.
pub const VALID_EFFECT_KEYS: &[&str] = &[
    "advance_initiative",
    "advance_time",
    "aoe",
    "apply_ability_damage",
    "apply_condition",
    "apply_damage",
    "call",
    "clock_create",
    "clock_tick",
    "condition",
    "count_successes",
    "create_entity",
    "decrement",
    "defer",
    "dice",
    "for_each",
    "heal",
    "hunger_dice",
    "increment",
    "initiative",
    "list_pop",
    "list_push",
    "log",
    "narrate",
    "publish",
    "random_table",
    "register_initiative_trigger",
    "remove_condition",
    "request_choice",
    "require",
    "reroll",
    "save_throw",
    "set",
    "set_flag",
    "set_participant",
    "shot_effect",
    "shuffle",
    "sort",
    "spatial_mutation",
    "spatial_query",
    "table_lookup",
    "transition_scene",
    "trigger",
];

/// `VALID_TRIGGERS`.
pub const VALID_TRIGGERS: &[&str] = &[
    "on_combat_start",
    "on_combat_end",
    "on_turn_start",
    "on_turn_end",
    "on_hp_zero",
    "on_hp_restored",
    "on_damage_taken",
    "on_condition_applied",
    "on_condition_removed",
    "on_ally_down",
    "on_critical",
    "on_rest",
    "on_level_up",
    "on_spell_cast",
    "on_death",
];

const BRANCHES: [&str; 4] = ["on_true", "on_false", "do", "consequence"];

fn is_effect_key(key: &Data) -> bool {
    matches!(key, Data::Str(text) if VALID_EFFECT_KEYS.contains(&text.as_str()))
}

// ── Patterns (all ASCII classes, as in Python's `re` for these literal ranges) ──────────

fn ident_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

fn ident_start(c: char) -> bool {
    c.is_ascii_alphabetic() || c == '_'
}

/// `[A-Za-z_][A-Za-z0-9_]*`, the whole text.
fn is_identifier(text: &str) -> bool {
    let mut chars = text.chars();
    chars.next().is_some_and(ident_start) && chars.all(ident_char)
}

/// `[A-Za-z_][A-Za-z0-9_]{0,63}`, the whole text.
fn is_short_identifier(text: &str) -> bool {
    is_identifier(text) && text.chars().count() <= 64
}

/// `_[A-Za-z0-9_]+`, the whole text.
fn is_private_identifier(text: &str) -> bool {
    text.len() > 1 && text.starts_with('_') && text.chars().all(ident_char)
}

/// `[A-Za-z0-9][A-Za-z0-9._:-]*`, the whole text.
fn is_choice_identity(text: &str) -> bool {
    let mut chars = text.chars();
    chars.next().is_some_and(|c| c.is_ascii_alphanumeric())
        && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | ':' | '-'))
}

/// `state\.[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*`, the whole text.
fn is_state_path(text: &str) -> bool {
    text.strip_prefix("state.").is_some_and(|rest| rest.split('.').all(is_identifier))
}

/// `_SPATIAL_TEMPLATE.fullmatch`: `{name}` or `{a.b.c}`.
fn is_spatial_template(text: &str) -> bool {
    text.strip_prefix('{')
        .and_then(|rest| rest.strip_suffix('}'))
        .is_some_and(|inner| inner.split('.').all(is_identifier))
}

/// `re.match(r"^[a-z_][a-z0-9_]*$", name)`.
fn is_snake_tool_name(text: &str) -> bool {
    anchored_match(text, |candidate| {
        let mut chars = candidate.chars();
        chars.next().is_some_and(|c| c.is_ascii_lowercase() || c == '_')
            && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
    })
}

/// `re.match(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", text)`.
fn is_kebab(text: &str) -> bool {
    anchored_match(text, |candidate| {
        let mut parts = candidate.split('-');
        let first = parts.next().unwrap_or("");
        let mut first_chars = first.chars();
        first_chars.next().is_some_and(|c| c.is_ascii_lowercase())
            && first_chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
            && parts.all(|part| {
                !part.is_empty() && part.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit())
            })
    })
}

/// `VALID_TRIGGER_PATTERN.fullmatch`: `on_[a-z][a-z0-9_]*`.
fn is_trigger_name(text: &str) -> bool {
    text.strip_prefix("on_").is_some_and(|rest| {
        let mut chars = rest.chars();
        chars.next().is_some_and(|c| c.is_ascii_lowercase())
            && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
    })
}

fn get<'a>(value: &'a Data, name: &str) -> Option<&'a Data> {
    pydata::get(value, name)
}

fn text(value: Option<&Data>) -> Option<&str> {
    pydata::text(value)
}

/// `value.strip()` is non-empty for a `str`.
fn nonblank(value: Option<&Data>) -> bool {
    text(value).is_some_and(|text| !strip(text).is_empty())
}

/// `for item in value`, as Python iterates it.
fn iterate(value: &Data) -> PyResult<Vec<Data>> {
    Ok(match value {
        Data::List(items) | Data::Tuple(items) | Data::Set(items) => items.clone(),
        Data::Dict(pairs) => keys(pairs),
        Data::Str(text) => text.chars().map(|c| Data::Str(c.to_string())).collect(),
        Data::Bytes(bytes) => bytes.iter().map(|b| Data::Int(i64::from(*b))).collect(),
        other => {
            return Err(PyError::type_error(format!("'{}' object is not iterable", pydata::type_name(other))))
        }
    })
}

/// `_validate_expression(expression)`: parse only, behind Python's `lru_cache(maxsize=1024)`,
/// which keeps the texts that parsed (a failure raises, so it is checked again next time).
fn expression_error(expression: &str) -> Option<String> {
    static VALIDATED: TextCache<()> = TextCache::new(1024);
    VALIDATED
        .get_or_try(expression, |text| parse_expression(text).map(|_| ()))
        .err()
        .map(|error| error.message)
}

// ── Top level ──────────────────────────────────────────────────────────────────

/// `validate_ruleset_data(data)`.
/// What [`validate_ruleset`] raises instead of returning findings.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RulesetTextError {
    /// An exception PyYAML lets through, which `except yaml.YAMLError` does not catch
    /// (`ValueError` from `!!int x` or an impossible `!!timestamp`).
    Load(YamlError),
    /// A `TypeError` from validation itself.
    Validation(PyError),
    /// Aliases that make the document contain itself (`differences.md` row 25).
    Recursive,
}

/// `validate_ruleset(yaml_content)`: `yaml.safe_load`, a YAML error reported as the one
/// finding `"YAML syntax error: {error}"`, then [`validate_ruleset_data`].
pub fn validate_ruleset(yaml_content: &str) -> Result<Errors, RulesetTextError> {
    let loaded = match pyyaml::safe_load(yaml_content) {
        Ok(loaded) => loaded,
        Err(error) if error.is_yaml_error() => return Ok(vec![format!("YAML syntax error: {error}")]),
        Err(error) => return Err(RulesetTextError::Load(error)),
    };
    let data = loaded.to_data().map_err(|_| RulesetTextError::Recursive)?.unwrap_or(Data::None);
    validate_ruleset_data(&data).map_err(RulesetTextError::Validation)
}

pub fn validate_ruleset_data(data: &Data) -> PyResult<Errors> {
    let Data::Dict(pairs) = data else {
        return Ok(vec!["Ruleset YAML must be a mapping (dict) at top level".into()]);
    };
    let mut errors = validate_parameter_declarations(pairs)?;
    let resolved = match resolve_parameter_declarations(pairs) {
        Ok(resolved) => resolved,
        Err(ParameterDeclarationError { message }) => {
            errors.push(message);
            return Ok(errors);
        }
    };
    let data = Data::Dict(resolved);
    let Data::Dict(pairs) = &data else { unreachable!() };
    let is_package_runtime = contains(pairs, "sylvia_gm_runtime");
    if is_package_runtime {
        if contains(pairs, "meta") {
            errors.push("'meta' is manifest-owned and is not allowed in package runtime YAML".into());
        }
    } else {
        match get(&data, "meta") {
            Some(meta @ Data::Dict(meta_pairs)) => match meta.get_str("id") {
                None if !contains(meta_pairs, "id") => {
                    errors.push("meta.id is required (canonical ruleset identifier)".into());
                }
                Some(id) if !is_kebab(&to_str(id)) => {
                    errors.push(format!("meta.id '{}' must be lowercase kebab-case", to_str(id)));
                }
                _ => {}
            },
            _ => errors.push("Missing required section: 'meta'".into()),
        }
    }

    let empty = Data::Dict(Vec::new());
    let tools = get(&data, "tools").unwrap_or(&empty);
    let mut all_tool_names: Vec<Data> = Vec::new();
    match tools {
        Data::Dict(tool_pairs) => {
            all_tool_names = keys(tool_pairs);
            errors.extend(validate_tools(tool_pairs)?);
        }
        _ => errors.push("'tools' section must be a mapping".into()),
    }

    if is_package_runtime && contains(pairs, "model_tool_availability") {
        errors.push("'model_tool_availability' is manifest-owned and is not allowed in runtime YAML".into());
    } else if !is_package_runtime {
        errors.extend(validate_model_tool_availability(
            get(&data, "model_tool_availability").unwrap_or(&empty),
            &all_tool_names,
        ));
    }
    let boundary = get(&data, "model_decision_boundary");
    let scope = get(&data, "model_decision_scope");
    let present = |value: Option<&Data>| value.is_some_and(|v| !matches!(v, Data::None));
    if present(boundary) && present(scope) {
        errors.push("Ruleset may declare only one model decision boundary form".into());
    }
    errors.extend(validate_model_decision_boundary(boundary.unwrap_or(&Data::None))?);
    errors.extend(validate_model_decision_scope(scope.unwrap_or(&Data::None)));

    let procedures = get(&data, "procedures").unwrap_or(&empty);
    match procedures {
        Data::Dict(procedure_pairs) => errors.extend(validate_procedures(procedure_pairs, &all_tool_names)?),
        _ => errors.push("'procedures' section must be a mapping".into()),
    }

    let definitions = match get(&data, "definitions") {
        None | Some(Data::None) => Vec::new(),
        Some(Data::Dict(definition_pairs)) => definition_pairs.clone(),
        Some(_) => {
            errors.push("'definitions' section must be a mapping or null".into());
            Vec::new()
        }
    };
    match get(&data, "definition_row_schemas").unwrap_or(&empty) {
        Data::Dict(schemas) => {
            errors.extend(validate_definition_row_schemas(&definitions, schemas)?);
            let mut owners: Vec<(String, Data, Data)> = Vec::new();
            if let Data::Dict(tool_pairs) = tools {
                for (name, definition) in tool_pairs {
                    if matches!(definition, Data::Dict(_)) {
                        let effects =
                            definition.get_str("effects").cloned().unwrap_or(Data::List(Vec::new()));
                        owners.push((format!("Tool '{}'", to_str(name)), definition.clone(), effects));
                    }
                }
            }
            if let Data::Dict(procedure_pairs) = procedures {
                for (name, definition) in procedure_pairs {
                    if matches!(definition, Data::Dict(_)) {
                        let steps = definition.get_str("steps").cloned().unwrap_or(Data::List(Vec::new()));
                        owners.push((format!("Procedure '{}'", to_str(name)), Data::Dict(Vec::new()), steps));
                    }
                }
            }
            errors.extend(validate_definition_lookups(&owners, &definitions, schemas)?);
        }
        _ => errors.push("'definition_row_schemas' section must be a mapping".into()),
    }

    if let Some(narrative) = get(&data, "narrative") {
        if !matches!(narrative, Data::None | Data::Dict(_)) {
            errors.push("'narrative' section must be a mapping or null".into());
        }
    }
    Ok(errors)
}

/// `_iter_effects(effects)`: every effect dict, depth first.
fn iter_effects(effects: &Data, out: &mut Vec<Data>) {
    let Data::List(items) = effects else { return };
    for effect in items {
        if !matches!(effect, Data::Dict(_)) {
            continue;
        }
        out.push(effect.clone());
        for branch in BRANCHES {
            if let Some(nested) = effect.get_str(branch) {
                iter_effects(nested, out);
            }
        }
        for handler in VALID_EFFECT_KEYS {
            if let Some(body @ Data::Dict(_)) = effect.get_str(handler) {
                for branch in BRANCHES {
                    if let Some(nested) = body.get_str(branch) {
                        iter_effects(nested, out);
                    }
                }
            }
        }
    }
}

/// `_definition_table(definitions, path)`.
fn definition_table(definitions: &[(Data, Data)], path: &str) -> Option<Vec<(Data, Data)>> {
    let mut parts = path.split('.');
    if parts.next() != Some("definitions") || !path.contains('.') {
        return None;
    }
    let mut current = Data::Dict(definitions.to_vec());
    for part in parts {
        let next = match &current {
            Data::Dict(_) => current.get_str(part).cloned()?,
            _ => return None,
        };
        current = next;
    }
    match current {
        Data::Dict(pairs) => Some(pairs),
        _ => None,
    }
}

const DEFINITION_SCHEMA_FIELDS: &[&str] = &[
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "minimum",
    "maximum",
    "minLength",
    "uniqueItems",
];
const DEFINITION_SCHEMA_TYPES: &[&str] =
    &["string", "integer", "number", "boolean", "object", "array", "null"];

/// `_schema_value_matches(value, expected)`.
fn schema_value_matches(value: &Data, expected: Option<&Data>) -> bool {
    let Some(Data::Str(expected)) = expected else { return false };
    match expected.as_str() {
        "string" => matches!(value, Data::Str(_)),
        "integer" => matches!(value, Data::Int(_)),
        "number" => matches!(value, Data::Int(_) | Data::Float(_)),
        "boolean" => matches!(value, Data::Bool(_)),
        "object" => matches!(value, Data::Dict(_)),
        "array" => pydata::is_sequence(value),
        "null" => matches!(value, Data::None),
        _ => false,
    }
}

fn is_plain_number(value: Option<&Data>) -> bool {
    matches!(value, Some(Data::Int(_) | Data::Float(_)))
}

/// `_validate_definition_schema(schema, label=label)`.
fn validate_definition_schema(schema: &[(Data, Data)], label: &str) -> PyResult<Errors> {
    let mut errors = Errors::new();
    let as_data = Data::Dict(schema.to_vec());
    let unknown = keys_except(schema, DEFINITION_SCHEMA_FIELDS);
    if !unknown.is_empty() {
        errors.push(format!("{label} contains unsupported field '{}'", to_str(&sorted(&unknown)?[0])));
    }
    let expected = as_data.get_str("type");
    if !in_str_set(expected.unwrap_or(&Data::None), DEFINITION_SCHEMA_TYPES)? {
        errors.push(format!("{label} has an invalid type"));
        return Ok(errors);
    }
    let expected_text = text(expected).expect("a valid type is text");
    let mut object_only: Vec<&str> = ["additionalProperties", "properties", "required"]
        .into_iter()
        .filter(|name| contains(schema, name))
        .collect();
    object_only.sort_unstable();
    if expected_text != "object" && !object_only.is_empty() {
        errors.push(format!("{label} field '{}' is not valid for type '{expected_text}'", object_only[0]));
    }
    if expected_text != "array" && contains(schema, "items") {
        errors.push(format!("{label} field 'items' is not valid for type '{expected_text}'"));
    }
    if contains(schema, "uniqueItems")
        && (expected_text != "array" || !matches!(as_data.get_str("uniqueItems"), Some(Data::Bool(_))))
    {
        errors.push(format!("{label} uniqueItems requires an array and a boolean"));
    }
    if let Some(enum_value) = as_data.get_str("enum").filter(|v| !matches!(v, Data::None)) {
        let invalid = match enum_value {
            Data::List(items) => {
                let mut reprs: Vec<String> = items.iter().map(repr).collect();
                reprs.sort_unstable();
                reprs.dedup();
                items.is_empty()
                    || items.iter().any(|value| !schema_value_matches(value, expected))
                    || reprs.len() != items.len()
            }
            _ => true,
        };
        if invalid {
            errors.push(format!("{label} has an invalid enum"));
        }
    }
    let minimum = as_data.get_str("minimum");
    let maximum = as_data.get_str("maximum");
    for (bound_name, bound) in [("minimum", minimum), ("maximum", maximum)] {
        if contains(schema, bound_name)
            && (!matches!(expected_text, "integer" | "number") || !is_plain_number(bound))
        {
            errors.push(format!("{label} has an invalid {bound_name}"));
        }
    }
    if let (Some(low), Some(high)) = (minimum, maximum) {
        if is_plain_number(Some(low)) && is_plain_number(Some(high)) && number_lt(high, low) {
            errors.push(format!("{label} minimum exceeds maximum"));
        }
    }
    if contains(schema, "minLength")
        && (expected_text != "string"
            || !matches!(as_data.get_str("minLength"), Some(Data::Int(n)) if *n >= 0))
    {
        errors.push(format!("{label} has an invalid minimum length"));
    }
    if expected_text == "object" {
        let empty = Data::Dict(Vec::new());
        let Data::Dict(properties) = as_data.get_str("properties").unwrap_or(&empty) else {
            errors.push(format!("{label} properties are invalid"));
            return Ok(errors);
        };
        let no_required = Data::List(Vec::new());
        let required_ok = match as_data.get_str("required").unwrap_or(&no_required) {
            Data::List(items) => {
                let texts: Option<Vec<&str>> = items.iter().map(|item| text(Some(item))).collect();
                texts.is_some_and(|texts| {
                    let mut unique = texts.clone();
                    unique.sort_unstable();
                    unique.dedup();
                    unique.len() == texts.len() && texts.iter().all(|name| contains(properties, name))
                })
            }
            _ => false,
        };
        if !required_ok {
            errors.push(format!("{label} required fields are invalid"));
        }
        if !matches!(as_data.get_str("additionalProperties").unwrap_or(&Data::Bool(true)), Data::Bool(_)) {
            errors.push(format!("{label} additionalProperties must be a boolean"));
        }
        for (name, child) in properties {
            match (name, child) {
                (Data::Str(name), Data::Dict(child)) => {
                    errors.extend(validate_definition_schema(child, &format!("{label}.{name}"))?);
                }
                _ => errors.push(format!("{label} property schemas are invalid")),
            }
        }
    } else if expected_text == "array" {
        match as_data.get_str("items") {
            Some(Data::Dict(items)) => {
                errors.extend(validate_definition_schema(items, &format!("{label}.items"))?)
            }
            _ => errors.push(format!("{label} has invalid array items schema")),
        }
    }
    Ok(errors)
}

/// `==` as `item == previous` evaluates it: a float compares by value (NaN unequal).
fn plain_eq(a: &Data, b: &Data) -> bool {
    match (a, b) {
        (Data::Float(x), Data::Float(y)) => x == y,
        _ => pydata::py_eq(a, b),
    }
}

/// `_validate_definition_value(value, schema, label=label)`.
fn validate_definition_value(value: &Data, schema: &Data, label: &str) -> PyResult<Errors> {
    let expected = schema.get_str("type");
    if !schema_value_matches(value, expected) {
        return Ok(vec![format!("{label} has invalid type")]);
    }
    let mut errors = Errors::new();
    if let Some(Data::List(choices)) = schema.get_str("enum") {
        if !in_list(value, choices) {
            errors.push(format!("{label} is outside its enum"));
        }
    }
    let value_is_number = matches!(value, Data::Int(_) | Data::Float(_));
    if let Some(minimum) = schema.get_str("minimum").filter(|m| is_plain_number(Some(m))) {
        if value_is_number && number_lt(value, minimum) {
            errors.push(format!("{label} is below its minimum"));
        }
    }
    if let Some(maximum) = schema.get_str("maximum").filter(|m| is_plain_number(Some(m))) {
        if value_is_number && number_lt(maximum, value) {
            errors.push(format!("{label} is above its maximum"));
        }
    }
    if let (Some(minimum), Data::Str(text)) = (schema.get_str("minLength"), value) {
        let length = i64::try_from(text.chars().count()).expect("length fits");
        let short = match minimum {
            Data::Int(n) => length < *n,
            Data::Bool(flag) => length < i64::from(*flag),
            _ => false,
        };
        if short {
            errors.push(format!("{label} is shorter than its minimum length"));
        }
    }
    let expected_text = text(expected).unwrap_or("");
    if expected_text == "object" {
        if let Data::Dict(value_pairs) = value {
            let empty = Data::Dict(Vec::new());
            let no_required = Data::List(Vec::new());
            if let (Data::Dict(properties), Data::List(required)) = (
                schema.get_str("properties").unwrap_or(&empty),
                schema.get_str("required").unwrap_or(&no_required),
            ) {
                let mut missing = Vec::new();
                for name in required {
                    if let Some(error) = unhashable(name) {
                        return Err(error);
                    }
                    if !value_pairs.iter().any(|(key, _)| sylvia_base::pyyaml::data_key_eq(key, name)) {
                        missing.push(name.clone());
                    }
                }
                if let Some(first) = missing.first() {
                    errors.push(format!("{label} is missing required field '{}'", to_str(first)));
                }
                if matches!(schema.get_str("additionalProperties"), Some(Data::Bool(false))) {
                    let extra: Vec<Data> = value_pairs
                        .iter()
                        .filter(|(key, _)| {
                            !properties.iter().any(|(name, _)| sylvia_base::pyyaml::data_key_eq(name, key))
                        })
                        .map(|(key, _)| key.clone())
                        .collect();
                    let extra = sorted(&extra)?;
                    if let Some(first) = extra.first() {
                        errors.push(format!("{label} contains undeclared field '{}'", to_str(first)));
                    }
                }
                for (name, child_value) in value_pairs {
                    let child = properties
                        .iter()
                        .find(|(key, _)| sylvia_base::pyyaml::data_key_eq(key, name))
                        .map(|(_, child)| child);
                    if let Some(child @ Data::Dict(_)) = child {
                        errors.extend(validate_definition_value(
                            child_value,
                            child,
                            &format!("{label}.{}", to_str(name)),
                        )?);
                    }
                }
            }
        }
    } else if expected_text == "array" && pydata::is_sequence(value) {
        let items = match value {
            Data::List(items) | Data::Tuple(items) => items,
            _ => unreachable!(),
        };
        if matches!(schema.get_str("uniqueItems"), Some(Data::Bool(true)))
            && items
                .iter()
                .enumerate()
                .any(|(index, item)| items[..index].iter().any(|previous| plain_eq(item, previous)))
        {
            errors.push(format!("{label} items must be unique"));
        }
        if let Some(item_schema @ Data::Dict(_)) = schema.get_str("items") {
            for (index, item) in items.iter().enumerate() {
                errors.extend(validate_definition_value(item, item_schema, &format!("{label}[{index}]"))?);
            }
        }
    }
    Ok(errors)
}

fn is_discriminated(schema: &[(Data, Data)]) -> bool {
    contains(schema, "discriminator") || contains(schema, "variants")
}

/// `_validate_definition_row_schema(schema, label=label)`.
fn validate_definition_row_schema(schema: &[(Data, Data)], label: &str) -> PyResult<Errors> {
    let as_data = Data::Dict(schema.to_vec());
    if !is_discriminated(schema) {
        let mut errors = validate_definition_schema(schema, label)?;
        if !matches!(as_data.get_str("type"), Some(Data::Str(t)) if t == "object") {
            errors.push(format!("{label} must describe object rows"));
        }
        if !matches!(as_data.get_str("additionalProperties"), Some(Data::Bool(false))) {
            errors.push(format!("{label} must set additionalProperties to false"));
        }
        return Ok(errors);
    }
    let mut errors = Errors::new();
    let unknown = keys_except(schema, &["discriminator", "variants"]);
    if !unknown.is_empty() {
        errors.push(format!("{label} contains unsupported field '{}'", to_str(&sorted(&unknown)?[0])));
    }
    if !text(as_data.get_str("discriminator")).is_some_and(is_identifier) {
        errors.push(format!("{label} has an invalid discriminator"));
    }
    let Some(Data::Dict(variants)) = as_data.get_str("variants").filter(|v| truthy(v)) else {
        errors.push(format!("{label} has invalid variants"));
        return Ok(errors);
    };
    for (name, variant_schema) in variants {
        let variant_label = format!("{label} variant {}", repr(name));
        let (Data::Str(variant_name), Data::Dict(variant)) = (name, variant_schema) else {
            errors.push(format!("{label} has invalid variants"));
            continue;
        };
        if variant_name.is_empty() {
            errors.push(format!("{label} has invalid variants"));
            continue;
        }
        errors.extend(validate_definition_schema(variant, &variant_label)?);
        if !matches!(variant_schema.get_str("type"), Some(Data::Str(t)) if t == "object") {
            errors.push(format!("{variant_label} must describe an object payload"));
        }
        if !matches!(variant_schema.get_str("additionalProperties"), Some(Data::Bool(false))) {
            errors.push(format!("{variant_label} must set additionalProperties to false"));
        }
    }
    Ok(errors)
}

/// `_validate_definition_row(row, schema, label=label)`.
fn validate_definition_row(row: &Data, schema: &[(Data, Data)], label: &str) -> PyResult<Errors> {
    let schema_data = Data::Dict(schema.to_vec());
    if !is_discriminated(schema) {
        return validate_definition_value(row, &schema_data, label);
    }
    let Data::Dict(row_pairs) = row else {
        return Ok(vec![format!("{label} has invalid type")]);
    };
    let (Some(Data::Str(discriminator)), Some(Data::Dict(variants))) =
        (schema_data.get_str("discriminator"), schema_data.get_str("variants"))
    else {
        return Ok(Vec::new());
    };
    let Some(selected) = row.get_str(discriminator) else {
        return Ok(vec![format!("{label} is missing discriminator '{discriminator}'")]);
    };
    let variant = match selected {
        Data::Str(name) => variants.iter().find(|(key, _)| matches!(key, Data::Str(k) if k == name)),
        _ => None,
    };
    let Some((_, variant_schema)) = variant else {
        return Ok(vec![format!("{label} selects undeclared variant {}", repr(selected))]);
    };
    if !matches!(variant_schema, Data::Dict(_)) {
        return Ok(Vec::new());
    }
    let payload: Vec<(Data, Data)> = row_pairs
        .iter()
        .filter(|(key, _)| !matches!(key, Data::Str(k) if k == discriminator))
        .cloned()
        .collect();
    validate_definition_value(&Data::Dict(payload), variant_schema, label)
}

/// `_validate_definition_row_schemas(definitions, schemas)`.
fn validate_definition_row_schemas(
    definitions: &[(Data, Data)],
    schemas: &[(Data, Data)],
) -> PyResult<Errors> {
    let mut errors = Errors::new();
    for (table_path, schema) in schemas {
        let label = format!("Definition table '{}' schema", to_str(table_path));
        let Some(path) = text(Some(table_path)).filter(|path| path.starts_with("definitions.")) else {
            errors.push("Definition row schema paths must start with 'definitions.'".into());
            continue;
        };
        let Data::Dict(schema_pairs) = schema else {
            errors.push(format!("{label} must be a mapping"));
            continue;
        };
        let schema_errors = validate_definition_row_schema(schema_pairs, &label)?;
        let failed = !schema_errors.is_empty();
        errors.extend(schema_errors);
        let Some(table) = definition_table(definitions, path) else {
            errors.push(format!("{label} targets a missing table"));
            continue;
        };
        if failed {
            continue;
        }
        for (key, row) in &table {
            errors.extend(validate_definition_row(
                row,
                schema_pairs,
                &format!("Definition table '{path}' row {}", repr(key)),
            )?);
        }
    }
    Ok(errors)
}

/// `_validate_definition_lookups(owners, definitions, definition_row_schemas)`.
fn validate_definition_lookups(
    owners: &[(String, Data, Data)],
    definitions: &[(Data, Data)],
    row_schemas: &[(Data, Data)],
) -> PyResult<Errors> {
    let mut errors = Errors::new();
    let allowed = ["table", "key", "default", "required", "exact_selector"];
    for (owner_label, tool_def, effects) in owners {
        let parameters_value = tool_def.get_str("parameters").cloned().unwrap_or(Data::List(Vec::new()));
        let mut parameters: Vec<(Data, Data)> = Vec::new();
        for parameter in iterate(&parameters_value)? {
            if let Data::Dict(_) = parameter {
                if let Some(name @ Data::Str(_)) = parameter.get_str("name").cloned() {
                    match parameters.iter_mut().find(|(key, _)| sylvia_base::pyyaml::data_key_eq(key, &name))
                    {
                        Some((_, slot)) => *slot = parameter.clone(),
                        None => parameters.push((name, parameter.clone())),
                    }
                }
            }
        }
        let mut found = Vec::new();
        iter_effects(effects, &mut found);
        for effect in found {
            let Some(body @ Data::Dict(body_pairs)) = effect.get_str("table_lookup") else { continue };
            let location = format!("{owner_label}: table_lookup");
            let unknown = keys_except(body_pairs, &allowed);
            if !unknown.is_empty() {
                errors.push(format!("{location} has unsupported fields: {}", sorted_repr(&unknown)?));
            }
            let store_as = effect.get_str("store_as").cloned().unwrap_or(Data::Str("_table_result".into()));
            if !text(Some(&store_as)).is_some_and(is_private_identifier) {
                errors.push(format!("{location} store_as must be a private context identifier"));
            }
            let required = body.get_str("required").cloned().unwrap_or(Data::Bool(false));
            let exact_selector = body.get_str("exact_selector").cloned().unwrap_or(Data::Bool(false));
            let Data::Bool(required) = required else {
                errors.push(format!("{location} required must be a boolean"));
                continue;
            };
            let Data::Bool(exact_selector) = exact_selector else {
                errors.push(format!("{location} exact_selector must be a boolean"));
                continue;
            };
            if required && contains(body_pairs, "default") {
                errors.push(format!("{location} cannot combine required with default"));
            }
            if exact_selector && !required {
                errors.push(format!("{location} exact_selector requires required: true"));
            }
            let table_path = body.get_str("table").cloned().unwrap_or(Data::None);
            let table = text(Some(&table_path)).and_then(|path| definition_table(definitions, path));
            if required && table.is_none() {
                errors.push(format!("{location} required table '{}' does not exist", to_str(&table_path)));
            }
            let (true, Some(table)) = (exact_selector, table) else { continue };
            let Data::Str(path) = &table_path else { continue };
            let row_schema = row_schemas.iter().find(|(key, _)| matches!(key, Data::Str(k) if k == path));
            if !matches!(row_schema, Some((_, Data::Dict(_)))) {
                errors.push(format!("{location} exact selector requires a definition row schema"));
            }
            let selector_name = body.get_str("key").cloned().unwrap_or(Data::None);
            if let Some(error) = unhashable(&selector_name) {
                return Err(error);
            }
            let selector = parameters
                .iter()
                .find(|(key, _)| sylvia_base::pyyaml::data_key_eq(key, &selector_name))
                .map(|(_, p)| p);
            let selector_ok = matches!(selector_name, Data::Str(_))
                && selector.is_some_and(|selector| {
                    matches!(selector.get_str("type"), Some(Data::Str(t)) if t == "enum")
                        && matches!(
                            selector.get_str("required").unwrap_or(&Data::Bool(true)),
                            Data::Bool(true)
                        )
                });
            if !selector_ok {
                errors.push(format!("{location} exact selector must name a required enum parameter"));
                continue;
            }
            let enum_values = selector.and_then(|s| s.get_str("enum_values"));
            let values: Option<Vec<&str>> = match enum_values {
                Some(Data::List(items)) if !items.is_empty() => {
                    items.iter().map(|item| text(Some(item))).collect()
                }
                _ => None,
            };
            let unique = values.as_ref().is_some_and(|values| {
                let mut copy = values.clone();
                copy.sort_unstable();
                copy.dedup();
                copy.len() == values.len()
            });
            let Some(values) = values.filter(|_| unique) else {
                errors.push(format!("{location} selector enum values must be unique strings"));
                continue;
            };
            let same_keys = values
                .iter()
                .all(|value| table.iter().any(|(key, _)| matches!(key, Data::Str(k) if k == value)))
                && table.iter().all(|(key, _)| matches!(key, Data::Str(k) if values.contains(&k.as_str())));
            if !same_keys {
                errors.push(format!("{location} selector enum must exactly match table keys"));
            }
            for (key, row) in &table {
                if !matches!(row, Data::Dict(_)) {
                    errors.push(format!("{location} row {} must be a mapping", repr(key)));
                }
            }
        }
    }
    Ok(errors)
}

// ── Tools ──────────────────────────────────────────────────────────────────────

/// `_validate_tools(tools)`.
fn validate_tools(tools: &[(Data, Data)]) -> PyResult<Errors> {
    let mut errors = Errors::new();
    for (tool_name, tool_def) in tools {
        let name = to_str(tool_name);
        let Data::Dict(_) = tool_def else {
            errors.push(format!("Tool '{name}': must be a mapping"));
            continue;
        };
        let Data::Str(tool_text) = tool_name else { return Err(not_text(tool_name)) };
        if !is_snake_tool_name(tool_text) {
            errors.push(format!("Tool '{name}': invalid name (must be snake_case)"));
        }
        match tool_def.get_str("parameters").cloned().unwrap_or(Data::List(Vec::new())) {
            Data::List(params) => errors.extend(validate_tool_params(&name, &params)?),
            Data::None => {}
            _ => errors.push(format!("Tool '{name}': 'parameters' must be a list")),
        }
        if let Data::List(pre_conditions) =
            tool_def.get_str("pre_conditions").cloned().unwrap_or(Data::List(Vec::new()))
        {
            errors.extend(validate_pre_conditions(&name, &pre_conditions));
        }
        match tool_def.get_str("effects").cloned().unwrap_or(Data::List(Vec::new())) {
            Data::List(effects) => errors.extend(validate_effects(&name, &effects)?),
            Data::None => {}
            _ => errors.push(format!("Tool '{name}': 'effects' must be a list")),
        }
        if let Some(metadata @ Data::Dict(_)) = tool_def.get_str("metadata").or(Some(&Data::Dict(Vec::new())))
        {
            if let Some(category) = metadata.get_str("category") {
                if truthy(category) && !matches!(category, Data::Str(_)) {
                    errors.push(format!("Tool '{name}': metadata.category must be a string"));
                }
            }
            let model_behavior = metadata.get_str("model_behavior").cloned().unwrap_or(Data::None);
            let behavior_kind = match &model_behavior {
                Data::Dict(_) => model_behavior.get_str("kind").cloned().unwrap_or(Data::None),
                other => other.clone(),
            };
            if let Some(error) = pydata::set_membership_error(&behavior_kind) {
                return Err(error);
            }
            let known = matches!(&behavior_kind, Data::None)
                || matches!(&behavior_kind, Data::Str(kind) if [
                    "optional",
                    "required_engine_transition",
                    "required_ruleset_decision",
                    "leaves_player_intent_pending",
                    "resolves_player_intent",
                    "supports_player_intent",
                ].contains(&kind.as_str()));
            if !known {
                errors.push(format!("Tool '{name}': metadata.model_behavior is unsupported"));
            }
            if let Data::Dict(behavior_pairs) = &model_behavior {
                if keys_equal(behavior_pairs, &["kind", "state_predicates"]) {
                    let predicates =
                        model_behavior.get_str("state_predicates").cloned().unwrap_or(Data::None);
                    let declaration = Data::Dict(vec![(Data::Str("state_predicates".into()), predicates)]);
                    let availability = Data::Dict(vec![(tool_name.clone(), declaration)]);
                    let prefix = format!("Model tool '{name}'");
                    let replacement = format!("Tool '{name}' metadata.model_behavior");
                    errors.extend(
                        validate_model_tool_availability(&availability, std::slice::from_ref(tool_name))
                            .into_iter()
                            .map(|error| error.replace(&prefix, &replacement)),
                    );
                } else {
                    errors.push(format!("Tool '{name}': metadata.model_behavior fields are invalid"));
                }
            }
        }
    }
    Ok(errors)
}

/// `_validate_model_tool_availability(value, tool_names)`.
fn validate_model_tool_availability(value: &Data, tool_names: &[Data]) -> Errors {
    let Data::Dict(pairs) = value else {
        return vec!["'model_tool_availability' section must be a mapping".into()];
    };
    let mut errors = Errors::new();
    for (tool_name, declaration) in pairs {
        let name = to_str(tool_name);
        if !tool_names.iter().any(|known| sylvia_base::pyyaml::data_key_eq(known, tool_name)) {
            errors.push(format!("Model tool availability references unknown runtime tool '{name}'"));
            continue;
        }
        let Data::Dict(declaration_pairs) = declaration else {
            errors.push(format!("Model tool '{name}' availability must contain only state_predicates"));
            continue;
        };
        if !keys_equal(declaration_pairs, &["state_predicates"]) {
            errors.push(format!("Model tool '{name}' availability must contain only state_predicates"));
            continue;
        }
        let Some(Data::List(predicates)) = declaration.get_str("state_predicates") else {
            errors.push(format!("Model tool '{name}' state_predicates must be a list"));
            continue;
        };
        for (index, predicate) in predicates.iter().enumerate() {
            let shape_ok = match predicate {
                Data::Dict(predicate_pairs) => {
                    keys_equal(predicate_pairs, &["namespace", "key", "equals"])
                        || keys_equal(predicate_pairs, &["namespace", "key", "equals", "negated"])
                }
                _ => false,
            };
            if !shape_ok {
                errors.push(format!("Model tool '{name}' state predicate {index} has invalid fields"));
                continue;
            }
            if !nonblank(predicate.get_str("namespace")) {
                errors.push(format!("Model tool '{name}' state predicate {index} requires a namespace"));
            }
            if !nonblank(predicate.get_str("key")) {
                errors.push(format!("Model tool '{name}' state predicate {index} requires a key"));
            }
            if let Some(negated) = predicate.get_str("negated") {
                if !matches!(negated, Data::Bool(_)) {
                    errors
                        .push(format!("Model tool '{name}' state predicate {index} negated must be boolean"));
                }
            }
        }
    }
    errors
}

/// `_validate_campaign_preparation(value)`.
fn validate_campaign_preparation(value: Option<&Data>) -> Errors {
    let Some(value) = value.filter(|v| !matches!(v, Data::None)) else { return Vec::new() };
    let fields = ["queue_field", "intent", "actor_parameter", "prompt"];
    let valid_shape = match value {
        Data::Dict(pairs) => {
            keys_equal(pairs, &fields)
                && fields.iter().all(|name| {
                    text(value.get_str(name)).is_some_and(|t| (1..=2000).contains(&t.chars().count()))
                })
        }
        _ => false,
    };
    if !valid_shape {
        return vec!["Model decision preparation fields are invalid".into()];
    }
    if ["queue_field", "intent", "actor_parameter"]
        .iter()
        .any(|name| !pydata::str_is_alnum(&text(value.get_str(name)).unwrap_or("").replace('_', "")))
    {
        return vec!["Model decision preparation identifiers are invalid".into()];
    }
    Vec::new()
}

fn is_state_reference(reference: &Data) -> Option<bool> {
    let Data::Dict(pairs) = reference else { return None };
    if !keys_equal(pairs, &["namespace", "key"]) {
        return None;
    }
    Some(nonblank(reference.get_str("namespace")) && nonblank(reference.get_str("key")))
}

/// `_validate_model_decision_boundary(value)`.
fn validate_model_decision_boundary(value: &Data) -> PyResult<Errors> {
    if matches!(value, Data::None) {
        return Ok(Vec::new());
    }
    let required = ["active_actor", "actor_entities", "participant_control", "default_owner"];
    let shape_ok = match value {
        Data::Dict(pairs) => {
            has_keys(pairs, &required)
                && keys_except(
                    pairs,
                    &[
                        "active_actor",
                        "actor_entities",
                        "participant_control",
                        "default_owner",
                        "preparation",
                        "isolated_participant",
                    ],
                )
                .is_empty()
        }
        _ => false,
    };
    if !shape_ok {
        return Ok(vec!["'model_decision_boundary' fields are invalid".into()]);
    }
    let field = |name: &str| value.get_str(name).expect("required field present");
    let mut errors = Errors::new();
    for name in ["active_actor", "actor_entities"] {
        match is_state_reference(field(name)) {
            None => errors.push(format!("Model decision boundary {name} must be a state reference")),
            Some(false) => errors.push(format!("Model decision boundary {name} reference is invalid")),
            Some(true) => {}
        }
    }
    let control = field("participant_control");
    let control_ok = match control {
        Data::Dict(pairs) => {
            has_keys(pairs, &["field", "player_values"])
                && keys_except(pairs, &["field", "player_values", "actor_participant_field"]).is_empty()
        }
        _ => false,
    };
    if control_ok {
        let Data::Dict(control_pairs) = control else { unreachable!() };
        if contains(control_pairs, "actor_participant_field")
            && !text(control.get_str("actor_participant_field")).is_some_and(is_short_identifier)
        {
            errors.push("Model decision boundary actor participant field is invalid".into());
        }
        if !nonblank(control.get_str("field")) {
            errors.push("Model decision boundary participant control field is invalid".into());
        }
        let values_ok = match control.get_str("player_values") {
            Some(Data::List(items)) if !items.is_empty() => {
                let texts: Option<Vec<&str>> =
                    items.iter().map(|item| text(Some(item)).filter(|t| !strip(t).is_empty())).collect();
                texts.is_some_and(|texts| {
                    let mut unique = texts.clone();
                    unique.sort_unstable();
                    unique.dedup();
                    unique.len() == texts.len()
                })
            }
            _ => false,
        };
        if !values_ok {
            errors.push("Model decision boundary player values are invalid".into());
        }
    } else {
        errors.push("Model decision boundary participant_control fields are invalid".into());
    }
    if !in_str_set(field("default_owner"), &["player", "gm"])? {
        errors.push("Model decision boundary default owner is invalid".into());
    }
    errors.extend(validate_campaign_preparation(value.get_str("preparation")));
    let Data::Dict(pairs) = value else { unreachable!() };
    if contains(pairs, "isolated_participant") {
        let isolated = value.get_str("isolated_participant").expect("present");
        let fields = ["instructions", "observation_path", "actions_path", "public_observation"];
        let shape_ok = match isolated {
            Data::Dict(isolated_pairs) => {
                has_keys(isolated_pairs, &fields)
                    && keys_except(
                        isolated_pairs,
                        &[
                            "instructions",
                            "observation_path",
                            "actions_path",
                            "public_observation",
                            "persona_path",
                        ],
                    )
                    .is_empty()
            }
            _ => false,
        };
        if shape_ok {
            let Data::Dict(isolated_pairs) = isolated else { unreachable!() };
            let instructions_ok = text(isolated.get_str("instructions"))
                .is_some_and(|text| !strip(text).is_empty() && text.len() <= 8192);
            if !instructions_ok {
                errors.push("Isolated participant instructions are invalid".into());
            }
            for key in ["observation_path", "actions_path", "persona_path"] {
                if !contains(isolated_pairs, key) {
                    continue;
                }
                let path_ok = match isolated.get_str(key) {
                    Some(Data::List(parts)) => {
                        parts.len() <= 8
                            && parts.iter().all(|part| text(Some(part)).is_some_and(is_short_identifier))
                    }
                    _ => false,
                };
                if !path_ok {
                    errors.push("Isolated participant path is invalid".into());
                }
            }
            if is_state_reference(isolated.get_str("public_observation").expect("present")) != Some(true) {
                errors.push("Isolated participant public observation is invalid".into());
            }
        } else {
            errors.push("Isolated participant fields are invalid".into());
        }
    }
    Ok(errors)
}

/// `_validate_model_decision_scope(value)`.
fn validate_model_decision_scope(value: &Data) -> Errors {
    if matches!(value, Data::None) {
        return Vec::new();
    }
    let fields = ["decision_owner", "active_side", "eligible_actors", "focused_actor", "actor_entities"];
    let shape_ok = match value {
        Data::Dict(pairs) => {
            has_keys(pairs, &fields)
                && keys_except(
                    pairs,
                    &[
                        "decision_owner",
                        "active_side",
                        "eligible_actors",
                        "focused_actor",
                        "actor_entities",
                        "preparation",
                    ],
                )
                .is_empty()
        }
        _ => false,
    };
    if !shape_ok {
        return vec!["'model_decision_scope' fields are invalid".into()];
    }
    let mut errors = Errors::new();
    let mut names = fields;
    names.sort_unstable();
    for name in names {
        match is_state_reference(value.get_str(name).expect("present")) {
            None => errors.push(format!("Model decision scope {name} must be a state reference")),
            Some(false) => errors.push(format!("Model decision scope {name} reference is invalid")),
            Some(true) => {}
        }
    }
    errors.extend(validate_campaign_preparation(value.get_str("preparation")));
    errors
}

/// `validate_parameter_declarations(data)`: every pooled descriptor through the tool-parameter
/// checks, a `TypeError` or `ValueError` there becoming one "is invalid" message.
pub fn validate_parameter_declarations(data: &[(Data, Data)]) -> PyResult<Errors> {
    let empty = Data::Dict(Vec::new());
    let pool = data
        .iter()
        .find(|(key, _)| matches!(key, Data::Str(k) if k == "parameter_declarations"))
        .map_or(&empty, |(_, value)| value);
    let Data::Dict(pool) = pool else {
        return Ok(vec!["parameter_declarations must be a mapping".into()]);
    };
    let mut errors = Errors::new();
    for (name, descriptor) in pool {
        match validate_tool_params(
            &format!("parameter_declarations.{}", to_str(name)),
            std::slice::from_ref(descriptor),
        ) {
            Ok(found) => errors.extend(found),
            Err(error) if matches!(error.kind, PyErrorKind::TypeError | PyErrorKind::ValueError) => {
                errors.push(format!("Parameter declaration '{}' is invalid", to_str(name)));
            }
            Err(error) => return Err(error),
        }
    }
    Ok(errors)
}

/// `_validate_tool_params(tool_name, params)`.
fn validate_tool_params(tool_name: &str, params: &[Data]) -> PyResult<Errors> {
    let mut errors = Errors::new();
    for (i, param) in params.iter().enumerate() {
        let Data::Dict(pairs) = param else {
            errors.push(format!("Tool '{tool_name}': parameter {i} must be a mapping"));
            continue;
        };
        let name_value = param.get_str("name").cloned().unwrap_or(Data::None);
        if !truthy(&name_value) {
            errors.push(format!("Tool '{tool_name}': parameter {i} missing 'name'"));
        } else if !matches!(name_value, Data::Str(_)) {
            errors.push(format!("Tool '{tool_name}': parameter {i} name must be a string"));
        }
        let name = to_str(&name_value);
        let ptype = param.get_str("type").cloned().unwrap_or(Data::Str("string".into()));
        if !truthy(&ptype) {
            errors.push(format!("Tool '{tool_name}': parameter '{name}' missing 'type'"));
        } else if !in_str_set(&ptype, VALID_PARAM_TYPES)? {
            let mut valid = VALID_PARAM_TYPES.to_vec();
            valid.sort_unstable();
            errors.push(format!(
                "Tool '{tool_name}': parameter '{name}' has invalid type '{}'. Valid: {}",
                to_str(&ptype),
                valid.join(", ")
            ));
        }
        let ptype_is = |kind: &str| matches!(&ptype, Data::Str(t) if t == kind);
        if ptype_is("enum") && !contains(pairs, "enum_values") {
            errors.push(format!(
                "Tool '{tool_name}': parameter '{name}' is enum type but missing 'enum_values'"
            ));
        }
        if ptype_is("array") {
            match param.get_str("items") {
                Some(Data::Dict(items)) => errors.extend(validate_definition_schema(
                    items,
                    &format!("Tool '{tool_name}' parameter '{name}' items"),
                )?),
                _ => errors
                    .push(format!("Tool '{tool_name}': array parameter '{name}' requires an items schema")),
            }
        } else if contains(pairs, "items") {
            errors.push(format!("Tool '{tool_name}': parameter '{name}' items are only valid for array"));
        }
        let from_state = param.get_str("from_state").filter(|v| !matches!(v, Data::None));
        if ptype_is("object") && from_state.is_none() {
            errors.push(format!("Tool '{tool_name}': object parameter '{name}' requires from_state"));
        }
        if let Some(from_state) = from_state {
            let ok = text(Some(from_state)).is_some_and(|t| {
                !strip(t).is_empty() && (t.starts_with("state.") || t.starts_with("combat."))
            });
            if !ok {
                errors.push(format!(
                    "Tool '{tool_name}': parameter '{name}' from_state must name a package state.* or combat.* field"
                ));
            }
            if contains(pairs, "default") {
                errors.push(format!(
                    "Tool '{tool_name}': parameter '{name}' cannot define both from_state and default"
                ));
            }
        }
        let list_constraints = ["min_items", "max_items", "unique_items"];
        let list_type = in_str_set(&ptype, &["array", "entity_id_list"])?;
        if !list_type && list_constraints.iter().any(|field| contains(pairs, field)) {
            errors.push(format!(
                "Tool '{tool_name}': list constraints on parameter '{name}' are only valid for entity_id_list or array parameters"
            ));
            continue;
        }
        if list_type {
            if ptype_is("entity_id_list")
                && !matches!(param.get_str("resolve"), Some(Data::Str(r)) if r == "entity_list")
            {
                errors.push(format!(
                    "Tool '{tool_name}': parameter '{name}' entity_id_list requires resolve: entity_list"
                ));
            }
            for field in ["min_items", "max_items"] {
                if !contains(pairs, field) {
                    continue;
                }
                if !matches!(param.get_str(field), Some(Data::Int(n)) if *n >= 0) {
                    errors.push(format!(
                        "Tool '{tool_name}': parameter '{name}' {field} must be an integer at least zero"
                    ));
                }
            }
            if contains(pairs, "unique_items")
                && !matches!(param.get_str("unique_items"), Some(Data::Bool(_)))
            {
                errors.push(format!("Tool '{tool_name}': parameter '{name}' unique_items must be a boolean"));
            }
            if let (Some(Data::Int(low)), Some(Data::Int(high))) =
                (param.get_str("min_items"), param.get_str("max_items"))
            {
                if low > high {
                    errors.push(format!(
                        "Tool '{tool_name}': parameter '{name}' min_items cannot exceed max_items"
                    ));
                }
            }
        }
    }
    Ok(errors)
}

/// `_validate_pre_conditions(tool_name, pre_conds)`.
fn validate_pre_conditions(tool_name: &str, pre_conditions: &[Data]) -> Errors {
    let mut errors = Errors::new();
    for (i, pre_condition) in pre_conditions.iter().enumerate() {
        if !matches!(pre_condition, Data::Dict(_)) {
            errors.push(format!("Tool '{tool_name}': pre_condition {i} must be a mapping"));
            continue;
        }
        let condition = pre_condition.get_str("condition").cloned().unwrap_or(Data::None);
        if !truthy(&condition) {
            errors.push(format!("Tool '{tool_name}': pre_condition {i} missing 'condition'"));
        } else if let Data::Str(expression) = &condition {
            if let Some(error) = expression_error(expression) {
                errors.push(format!(
                    "Tool '{tool_name}': pre_condition {i} invalid expression '{expression}': {error}"
                ));
            }
        }
    }
    errors
}

/// `_validate_effects(tool_name, effects)`: each effect, recursively.
pub fn validate_effects(tool_name: &str, effects: &[Data]) -> PyResult<Errors> {
    let mut errors = Errors::new();
    for (i, effect) in effects.iter().enumerate() {
        let Data::Dict(_) = effect else {
            errors.push(format!("Tool '{tool_name}': effect {i} must be a mapping"));
            continue;
        };
        errors.extend(validate_single_effect(tool_name, effect, &format!("effect {i}"))?);
    }
    Ok(errors)
}

/// `_validate_single_effect(tool_name, effect, path)`.
fn validate_single_effect(tool_name: &str, effect: &Data, path: &str) -> PyResult<Errors> {
    let mut errors = Errors::new();
    let Data::Dict(effect_pairs) = effect else { unreachable!("callers pass dicts") };
    let condition = effect.get_str("condition");
    let condition_text = text(condition);
    let mut handler_keys: Vec<String> = effect_pairs
        .iter()
        .filter(|(key, _)| is_effect_key(key))
        .filter_map(|(key, _)| text(Some(key)).map(str::to_owned))
        .collect();
    let flat_conditional = condition_text.is_some()
        && (contains(effect_pairs, "on_true") || contains(effect_pairs, "on_false"))
        && handler_keys.iter().all(|key| key == "condition");
    let body: Option<Data> = if flat_conditional {
        handler_keys = vec!["condition".into()];
        Some(Data::Dict(vec![
            (Data::Str("condition".into()), condition.cloned().expect("text condition")),
            (
                Data::Str("on_true".into()),
                effect.get_str("on_true").cloned().unwrap_or(Data::List(Vec::new())),
            ),
            (
                Data::Str("on_false".into()),
                effect.get_str("on_false").cloned().unwrap_or(Data::List(Vec::new())),
            ),
        ]))
    } else {
        if condition_text.is_some() {
            handler_keys.retain(|key| key != "condition");
        }
        (handler_keys.len() == 1).then(|| effect.get_str(&handler_keys[0]).cloned().expect("handler present"))
    };
    if handler_keys.len() != 1 {
        let mut found = handler_keys.clone();
        found.sort_unstable();
        let listed = repr(&Data::List(found.into_iter().map(Data::Str).collect()));
        errors.push(format!(
            "Tool '{tool_name}': {path} must contain exactly one effect handler; found {listed}"
        ));
        return Ok(errors);
    }
    let handler = handler_keys[0].as_str();
    if let Some(expression) = condition_text {
        if let Some(error) = expression_error(expression) {
            errors.push(format!("Tool '{tool_name}': {path} condition invalid expression: {error}"));
        }
    }
    if handler == "call" {
        if let Some(exports) = effect.get_str("export_context").filter(|v| !matches!(v, Data::None)) {
            if !matches!(exports, Data::Str(star) if star == "*") {
                match exports {
                    Data::Dict(export_pairs) => {
                        let private = |value: &Data| text(Some(value)).is_some_and(|t| t.starts_with('_'));
                        if export_pairs
                            .iter()
                            .any(|(destination, source)| !private(destination) || !private(source))
                        {
                            errors.push(format!(
                                "Tool '{tool_name}': {path} context exports require private variable names"
                            ));
                        }
                    }
                    _ => errors
                        .push(format!("Tool '{tool_name}': {path} context exports must be an object or '*'")),
                }
            }
        }
    }
    for branch in BRANCHES {
        if let Some(Data::List(nested)) = effect.get_str(branch).or(Some(&Data::List(Vec::new()))) {
            errors.extend(validate_effects(tool_name, nested)?);
        }
    }
    let body = body.expect("one handler");
    if handler == "shot_effect" && !matches!(body, Data::Dict(_)) {
        errors.push(format!("Tool '{tool_name}': {path} shot_effect must be an object"));
    }
    if let Data::Dict(body_pairs) = &body {
        match handler {
            "request_choice" => {
                errors.extend(validate_request_choice_effect(tool_name, effect, &body, path)?)
            }
            "spatial_query" => errors.extend(validate_spatial_query_effect(tool_name, effect, &body, path)?),
            "spatial_mutation" => {
                errors.extend(validate_spatial_mutation_effect(tool_name, effect, &body, path)?)
            }
            "shot_effect" => {
                let required = [
                    "scene",
                    "snapshot",
                    "source_id",
                    "target_id",
                    "origin",
                    "destination",
                    "delay_ms",
                    "miss",
                    "contacts",
                ];
                let mut allowed = required.to_vec();
                allowed.extend(["style", "target_kind"]);
                if !keys_except(effect_pairs, &["shot_effect", "condition"]).is_empty()
                    || !has_keys(body_pairs, &required)
                    || !keys_except(body_pairs, &allowed).is_empty()
                    || !text(body.get_str("scene")).is_some_and(|scene| scene.starts_with("state."))
                {
                    errors.push(format!("Tool '{tool_name}': {path} invalid shot_effect declaration"));
                }
            }
            _ => {}
        }
        for branch in BRANCHES {
            // `nested is not effect.get(branch)`: the flat form's body holds the effect's own
            // `on_true`/`on_false` lists, which Python skips here by identity. `Data` has no
            // identity, so a YAML alias between a handler body and its effect is not skipped
            // (`differences.md` row 24).
            if flat_conditional && matches!(branch, "on_true" | "on_false") && contains(effect_pairs, branch)
            {
                continue;
            }
            if let Some(Data::List(nested)) = body.get_str(branch).or(Some(&Data::List(Vec::new()))) {
                errors.extend(validate_effects(tool_name, nested)?);
            }
        }
    }
    Ok(errors)
}

/// `_spatial_literal_or_template(value, int)` (an `int`, `bool` included, or a template).
fn int_or_template(value: Option<&Data>) -> bool {
    match value {
        Some(Data::Str(text)) => is_spatial_template(text),
        Some(value) => is_int(value),
        None => false,
    }
}

/// `_spatial_literal_or_template(value, bool)`.
fn bool_or_template(value: Option<&Data>) -> bool {
    match value {
        Some(Data::Bool(_)) => true,
        Some(Data::Str(text)) => is_spatial_template(text),
        _ => false,
    }
}

fn template(value: Option<&Data>) -> bool {
    text(value).is_some_and(is_spatial_template)
}

fn finite_number(value: &Data) -> Option<f64> {
    match value {
        Data::Int(_) | Data::Float(_) => pydata::number(value).filter(|n| n.is_finite()),
        _ => None,
    }
}

/// `_spatial_positive_number_or_template(value)`.
fn positive_number_or_template(value: Option<&Data>) -> bool {
    match value {
        Some(Data::Str(text)) => is_spatial_template(text),
        Some(value) => finite_number(value).is_some_and(|n| n > 0.0),
        None => false,
    }
}

/// `_spatial_coordinate(value, dimensions, integer=...)`.
fn coordinate(value: Option<&Data>, dimensions: usize, integer: bool) -> bool {
    match value {
        Some(Data::Str(text)) => is_spatial_template(text),
        Some(Data::List(items)) if items.len() == dimensions => items.iter().all(|item| match item {
            Data::Str(text) => is_spatial_template(text),
            Data::Int(_) => true,
            Data::Float(number) => !integer && number.is_finite(),
            _ => false,
        }),
        _ => false,
    }
}

/// The closed `{"key", "value", "missing_matches"}` metadata filter.
fn metadata_filter_ok(selector: &Data) -> bool {
    matches!(selector, Data::Dict(pairs) if keys_equal(pairs, &["key", "value", "missing_matches"]))
        && matches!(selector.get_str("key"), Some(Data::Str(_)))
        && matches!(selector.get_str("value"), Some(Data::Str(_)))
        && bool_or_template(selector.get_str("missing_matches"))
}

/// `_validate_spatial_mutation_effect(...)`.
fn validate_spatial_mutation_effect(
    tool_name: &str,
    effect: &Data,
    body: &Data,
    path: &str,
) -> PyResult<Errors> {
    let label = format!("Tool '{tool_name}': {path} spatial_mutation");
    let mut errors = Errors::new();
    let (Data::Dict(body_pairs), Data::Dict(effect_pairs)) = (body, effect) else { unreachable!() };
    if !keys_equal(body_pairs, &["scene", "map_id", "expected_revision", "target", "patch"]) {
        errors.push(format!("{label} uses an invalid closed body"));
    }
    if !keys_except(effect_pairs, &["spatial_mutation", "store_as", "condition"]).is_empty() {
        errors.push(format!("{label} effect contains unsupported fields"));
    }
    if !text(body.get_str("scene")).is_some_and(is_state_path) {
        errors.push(format!("{label} scene must be a package-owned state path"));
    }
    if !text(body.get_str("map_id")).is_some_and(|t| !t.is_empty()) {
        errors.push(format!("{label} map_id must be text or a context template"));
    }
    let revision = body.get_str("expected_revision");
    if !int_or_template(revision) || matches!(revision, Some(Data::Bool(_))) {
        errors.push(format!("{label} expected_revision must be an integer or context template"));
    }
    if !text(effect.get_str("store_as")).is_some_and(is_private_identifier) {
        errors.push(format!("{label} requires a private store_as"));
    }
    let target = body.get_str("target").cloned().unwrap_or(Data::None);
    let Data::Dict(target_pairs) = &target else {
        errors.push(format!("{label} target is invalid"));
        return Ok(errors);
    };
    if !keys_equal(target_pairs, &["kind", "id"]) {
        errors.push(format!("{label} target is invalid"));
        return Ok(errors);
    }
    let kind = target.get_str("kind").cloned().unwrap_or(Data::None);
    let allowed: &[&str] = if in_str_keys(&kind, &["geometry"])? {
        &["volume", "active", "movement", "occupies_cells", "visibility", "metadata"]
    } else if in_str_keys(&kind, &["entity"])? {
        &["position", "footprint", "height", "facing", "active", "movement", "visibility", "metadata"]
    } else {
        errors.push(format!("{label} target kind is invalid"));
        return Ok(errors);
    };
    if !text(target.get_str("id")).is_some_and(|t| !t.is_empty()) {
        errors.push(format!("{label} target id must be text or a context template"));
    }
    let patch_ok = matches!(body.get_str("patch"), Some(Data::Dict(patch)) if !patch.is_empty() && keys_except(patch, allowed).is_empty());
    if !patch_ok {
        errors.push(format!("{label} patch uses unsupported fields"));
    }
    Ok(errors)
}

fn spatial_query_fields(operation: &str) -> Option<&'static [&'static str]> {
    Some(match operation {
        "circle_route" => &[
            "map_id",
            "moving_entity_id",
            "destination",
            "moving_radius",
            "circles",
            "metadata_filter",
            "max_tests",
            "edge_costs",
        ],
        "circle_overlap" => &[
            "map_id",
            "moving_entity_id",
            "fixed_entity_id",
            "destination",
            "moving_radius",
            "fixed_radius",
            "origin",
        ],
        "body_proximity" => &["map_id", "moving_entity_id", "fixed_entity_id", "destination", "radius"],
        "sweep" => &[
            "map_id",
            "moving_entity_id",
            "destination",
            "include_entities",
            "exclude_entity_ids",
            "metadata_filter",
        ],
        "placement" => &[
            "map_id",
            "moving_entity_id",
            "origin",
            "exclude_entity_ids",
            "metadata_filter",
            "max_candidates",
            "containing_zone_id",
            "reference_face",
        ],
        "ray" => &["map_id", "origin", "destination", "include", "max_intersections"],
        "exposure" => &["map_id", "observer", "target", "samples", "include", "metadata_filter"],
        "path" => &[
            "map_id",
            "start",
            "goal",
            "allow_diagonal",
            "corner_policy",
            "include_entities",
            "moving_entity_id",
            "metadata_filter",
            "max_steps",
        ],
        "crossed_zones" => &["map_id", "path", "max_crossings"],
        "area" => &["map_id", "shape", "include", "max_results"],
        _ => return None,
    })
}

/// `_validate_spatial_include(errors, label, value, allowed)`.
fn spatial_include(errors: &mut Errors, label: &str, value: Option<&Data>, allowed: &[&str]) {
    if template(value) {
        return;
    }
    let ok = match value {
        Some(Data::List(items)) if !items.is_empty() => {
            let texts: Option<Vec<&str>> =
                items.iter().map(|item| text(Some(item)).filter(|t| allowed.contains(t))).collect();
            texts.is_some_and(|texts| {
                let mut unique = texts.clone();
                unique.sort_unstable();
                unique.dedup();
                unique.len() == texts.len()
            })
        }
        _ => false,
    };
    if !ok {
        errors.push(format!("{label} include list is invalid"));
    }
}

/// `_validate_spatial_limit(errors, label, value, field, minimum, maximum)`.
fn spatial_limit(
    errors: &mut Errors,
    label: &str,
    value: Option<&Data>,
    field: &str,
    minimum: i64,
    maximum: i64,
) {
    if template(value) {
        return;
    }
    if !matches!(value, Some(Data::Int(n)) if (minimum..=maximum).contains(n)) {
        errors.push(format!("{label} {field} is outside bounds"));
    }
}

fn text_nonempty(value: Option<&Data>) -> bool {
    text(value).is_some_and(|t| !t.is_empty())
}

/// `_validate_spatial_query_effect(...)`.
#[allow(clippy::too_many_lines)]
fn validate_spatial_query_effect(
    tool_name: &str,
    effect: &Data,
    body: &Data,
    path: &str,
) -> PyResult<Errors> {
    let label = format!("Tool '{tool_name}': {path} spatial_query");
    let mut errors = Errors::new();
    let (Data::Dict(body_pairs), Data::Dict(effect_pairs)) = (body, effect) else { unreachable!() };
    let operation = text(body.get_str("operation"));
    let fields = operation.and_then(spatial_query_fields);
    let expected_body: Vec<&str> = match (operation, fields) {
        (Some("snapshot"), _) => vec!["scene", "operation"],
        (Some(_), Some(_)) => {
            let mut expected = vec!["scene", "operation", "query"];
            if contains(body_pairs, "snapshot") {
                expected.push("snapshot");
            }
            expected
        }
        _ => {
            errors.push(format!("{label} operation is invalid"));
            return Ok(errors);
        }
    };
    let operation = operation.expect("checked");
    if !keys_equal(body_pairs, &expected_body) {
        errors.push(format!("{label} uses an invalid closed body"));
    }
    if !keys_except(effect_pairs, &["spatial_query", "store_as", "condition"]).is_empty() {
        errors.push(format!("{label} effect contains unsupported fields"));
    }
    if !text(body.get_str("scene")).is_some_and(is_state_path) {
        errors.push(format!("{label} scene must be a package-owned state path"));
    }
    if !text(effect.get_str("store_as")).is_some_and(is_private_identifier) {
        errors.push(format!("{label} requires a private store_as"));
    }
    if operation == "snapshot" {
        return Ok(errors);
    }
    if let Some(snapshot) = body.get_str("snapshot").filter(|v| !matches!(v, Data::None)) {
        if !template(Some(snapshot)) {
            errors.push(format!("{label} snapshot must reference private execution context"));
        }
    }
    let query = body.get_str("query").cloned().unwrap_or(Data::None);
    let Data::Dict(query_pairs) = &query else {
        errors.push(format!("{label} query must be an object"));
        return Ok(errors);
    };
    let mut expected: Vec<&str> = fields.expect("checked").to_vec();
    let drop = |expected: &mut Vec<&str>, name: &str| expected.retain(|field| *field != name);
    if operation == "circle_overlap" && !contains(query_pairs, "origin") {
        drop(&mut expected, "origin");
    }
    if operation == "circle_route" && !contains(query_pairs, "edge_costs") {
        drop(&mut expected, "edge_costs");
    }
    if operation == "path" && !contains(query_pairs, "moving_entity_id") {
        drop(&mut expected, "moving_entity_id");
    }
    if matches!(operation, "path" | "exposure") && !contains(query_pairs, "metadata_filter") {
        drop(&mut expected, "metadata_filter");
    }
    if operation == "sweep" {
        for name in ["metadata_filter", "exclude_entity_ids"] {
            if !contains(query_pairs, name) {
                drop(&mut expected, name);
            }
        }
    }
    if operation == "placement" {
        for name in ["containing_zone_id", "reference_face"] {
            if !contains(query_pairs, name) {
                drop(&mut expected, name);
            }
        }
    }
    if !keys_equal(query_pairs, &expected) {
        errors.push(format!("{label} {operation} query uses invalid fields"));
        return Ok(errors);
    }
    let q = |name: &str| query.get_str(name);
    if !text_nonempty(q("map_id")) {
        errors.push(format!("{label} map_id must be text or a context template"));
    }
    match operation {
        "ray" => {
            for name in ["origin", "destination"] {
                if !coordinate(q(name), 3, false) {
                    errors.push(format!("{label} {name} must be a three-coordinate value"));
                }
            }
            spatial_include(&mut errors, &label, q("include"), &["geometry", "entities"]);
            spatial_limit(&mut errors, &label, q("max_intersections"), "max_intersections", 1, 1024);
        }
        "exposure" => {
            match q("observer") {
                Some(observer @ Data::Dict(pairs)) if keys_equal(pairs, &["point"]) => {
                    if !coordinate(observer.get_str("point"), 3, false) {
                        errors.push(format!("{label} observer point must contain three coordinates"));
                    }
                }
                _ => errors.push(format!("{label} observer is invalid")),
            }
            let target_ok = matches!(q("target"), Some(target @ Data::Dict(pairs)) if keys_equal(pairs, &["entity_id"]) && matches!(target.get_str("entity_id"), Some(Data::Str(_))));
            if !target_ok {
                errors.push(format!("{label} target is invalid"));
            }
            match q("samples") {
                Some(samples @ Data::Dict(pairs)) if keys_equal(pairs, &["horizontal", "vertical"]) => {
                    spatial_limit(
                        &mut errors,
                        &label,
                        samples.get_str("horizontal"),
                        "horizontal samples",
                        1,
                        9,
                    );
                    spatial_limit(&mut errors, &label, samples.get_str("vertical"), "vertical samples", 1, 9);
                }
                _ => errors.push(format!("{label} samples are invalid")),
            }
            spatial_include(&mut errors, &label, q("include"), &["geometry", "entities"]);
            if let Some(selector) = q("metadata_filter").filter(|v| !matches!(v, Data::None)) {
                if !metadata_filter_ok(selector) {
                    errors.push(format!("{label} metadata filter is invalid"));
                }
            }
        }
        "circle_route" => {
            if !coordinate(q("destination"), 3, false) {
                errors.push(format!("{label} destination must contain three coordinates"));
            }
            if !text_nonempty(q("moving_entity_id")) {
                errors.push(format!("{label} moving_entity_id must be text or a template"));
            }
            let radius = q("moving_radius");
            if !template(radius) && !radius.and_then(finite_number).is_some_and(|r| r > 0.0) {
                errors.push(format!("{label} moving_radius must be positive or a template"));
            }
            let circles = q("circles");
            let circles_ok = template(circles)
                || matches!(circles, Some(Data::List(rows)) if rows.len() <= 64 && rows.iter().all(|row| matches!(row, Data::Dict(pairs) if keys_equal(pairs, &["entity_id", "radius", "blocking"]))));
            if !circles_ok {
                errors.push(format!("{label} circles must be a bounded list or a template"));
            }
            if !matches!(q("metadata_filter"), Some(Data::Dict(pairs)) if keys_equal(pairs, &["key", "value", "missing_matches"]))
            {
                errors.push(format!("{label} metadata filter is invalid"));
            }
            let no_costs = Data::List(Vec::new());
            let edge_costs = q("edge_costs").unwrap_or(&no_costs);
            let costs_ok = template(Some(edge_costs))
                || matches!(edge_costs, Data::List(rows) if rows.len() <= 16384 && rows.iter().all(|row| matches!(row, Data::Dict(pairs) if keys_equal(pairs, &["start", "end", "cost"]))));
            if !costs_ok {
                errors.push(format!("{label} edge_costs must be a bounded list or a template"));
            }
            spatial_limit(&mut errors, &label, q("max_tests"), "max_tests", 1, 16384);
        }
        "body_proximity" | "circle_overlap" => {
            if contains(query_pairs, "origin") && !coordinate(q("origin"), 3, false) {
                errors.push(format!("{label} origin must contain three coordinates"));
            }
            if !coordinate(q("destination"), 3, false) {
                errors.push(format!("{label} destination must contain three coordinates"));
            }
            for name in ["moving_entity_id", "fixed_entity_id"] {
                if !text_nonempty(q(name)) {
                    errors.push(format!("{label} {name} must be text or a template"));
                }
            }
            let names: &[&str] =
                if operation == "body_proximity" { &["radius"] } else { &["moving_radius", "fixed_radius"] };
            for name in names {
                let radius = q(name);
                let ok = template(radius)
                    || radius.and_then(finite_number).is_some_and(|r| {
                        if *name == "radius" {
                            r >= 0.0
                        } else {
                            r > 0.0
                        }
                    });
                if !ok {
                    errors.push(format!("{label} {name} must be a finite valid radius or a template"));
                }
            }
        }
        "sweep" => {
            if !coordinate(q("destination"), 3, false) {
                errors.push(format!("{label} destination must contain three coordinates"));
            }
            if !text_nonempty(q("moving_entity_id")) {
                errors.push(format!("{label} moving_entity_id must be text or a template"));
            }
            if !bool_or_template(q("include_entities")) {
                errors.push(format!("{label} include_entities must be boolean"));
            }
            let none_excluded = Data::List(Vec::new());
            let excluded = q("exclude_entity_ids").unwrap_or(&none_excluded);
            let excluded_ok = template(Some(excluded))
                || matches!(excluded, Data::List(items) if items.len() <= 64 && items.iter().all(|item| matches!(item, Data::Str(_))));
            if !excluded_ok {
                errors.push(format!("{label} excluded entities are invalid"));
            }
            if let Some(selector) = q("metadata_filter").filter(|v| !matches!(v, Data::None)) {
                if !metadata_filter_ok(selector) {
                    errors.push(format!("{label} metadata filter is invalid"));
                }
            }
        }
        "placement" => {
            if contains(query_pairs, "reference_face") {
                let reference = q("reference_face").expect("present");
                match reference {
                    Data::Dict(pairs) if keys_equal(pairs, &["target", "face"]) => {
                        let target = reference.get_str("target").expect("present");
                        let target_ok = matches!(target, Data::Dict(pairs) if keys_equal(pairs, &["kind", "id"]))
                            && matches!(target.get_str("kind"), Some(Data::Str(kind)) if kind == "geometry" || kind == "entity")
                            && text_nonempty(target.get_str("id"));
                        if !target_ok {
                            errors.push(format!("{label} reference_face target is invalid"));
                        }
                        let face_ok = match reference.get_str("face") {
                            Some(Data::Str(face)) => {
                                ["east", "west", "north", "south", "up", "down"].contains(&face.as_str())
                                    || is_spatial_template(face)
                            }
                            _ => false,
                        };
                        if !face_ok {
                            errors.push(format!("{label} reference_face face is invalid"));
                        }
                    }
                    _ => errors.push(format!("{label} reference_face is invalid")),
                }
            }
            if contains(query_pairs, "containing_zone_id") && !text_nonempty(q("containing_zone_id")) {
                errors.push(format!("{label} containing_zone_id must be text or a template"));
            }
            if !coordinate(q("origin"), 3, false) {
                errors.push(format!("{label} origin must contain three coordinates"));
            }
            if !matches!(q("moving_entity_id"), Some(Data::Str(_))) {
                errors.push(format!("{label} moving_entity_id must be text or a template"));
            }
            let excluded = q("exclude_entity_ids");
            let excluded_ok = template(excluded)
                || matches!(excluded, Some(Data::List(items)) if items.len() <= 64 && items.iter().all(|item| matches!(item, Data::Str(_))));
            if !excluded_ok {
                errors.push(format!("{label} excluded entities are invalid"));
            }
            if !q("metadata_filter").is_some_and(metadata_filter_ok) {
                errors.push(format!("{label} metadata filter is invalid"));
            }
            spatial_limit(&mut errors, &label, q("max_candidates"), "max_candidates", 1, 100_000);
        }
        "path" => {
            if let Some(selector) = q("metadata_filter").filter(|v| !matches!(v, Data::None)) {
                if !metadata_filter_ok(selector) {
                    errors.push(format!("{label} metadata filter is invalid"));
                }
            }
            for name in ["start", "goal"] {
                if !coordinate(q(name), 2, true) {
                    errors.push(format!("{label} {name} must be a two-cell value"));
                }
            }
            if !bool_or_template(q("allow_diagonal")) {
                errors.push(format!("{label} allow_diagonal must be boolean"));
            }
            let corner = q("corner_policy").cloned().unwrap_or(Data::None);
            if !in_str_set(&corner, &["allow_cutting", "forbid_cutting"])? && !template(Some(&corner)) {
                errors.push(format!("{label} corner_policy is invalid"));
            }
            if !bool_or_template(q("include_entities")) {
                errors.push(format!("{label} include_entities must be boolean"));
            }
            if let Some(moving) = q("moving_entity_id").filter(|v| !matches!(v, Data::None)) {
                if !text_nonempty(Some(moving)) {
                    errors.push(format!("{label} moving_entity_id must be text or a context template"));
                }
            }
            spatial_limit(&mut errors, &label, q("max_steps"), "max_steps", 1, 4_000_000);
        }
        "crossed_zones" => {
            let path_valid = match q("path") {
                Some(Data::Str(text)) => is_spatial_template(text),
                Some(Data::List(points)) => {
                    (2..=256).contains(&points.len())
                        && points.iter().all(|point| coordinate(Some(point), 3, false))
                }
                _ => false,
            };
            if !path_valid {
                errors.push(format!("{label} path must contain 2 to 256 points"));
            }
            spatial_limit(&mut errors, &label, q("max_crossings"), "max_crossings", 1, 4096);
        }
        _ => {
            let shape = q("shape").cloned().unwrap_or(Data::None);
            let fields: &[(&str, &[&str])] = &[
                ("box", &["kind", "min", "max"]),
                ("cylinder", &["kind", "center", "radius", "height"]),
                ("sphere", &["kind", "center", "radius"]),
                ("cone", &["kind", "apex", "direction", "length", "radius"]),
                ("prism", &["kind", "origin", "direction", "up", "length", "width", "height"]),
            ];
            let kind = text(shape.get_str("kind")).filter(|_| matches!(shape, Data::Dict(_)));
            let entry = kind.and_then(|kind| fields.iter().find(|(name, _)| *name == kind));
            let s = |name: &str| shape.get_str(name);
            match (&shape, entry) {
                (Data::Dict(pairs), Some((kind, names))) => {
                    if !keys_equal(pairs, names) {
                        errors.push(format!("{label} area shape uses invalid fields"));
                    } else {
                        let invalid = match *kind {
                            "box" => (!coordinate(s("min"), 3, false) || !coordinate(s("max"), 3, false))
                                .then_some("area box coordinates are invalid"),
                            "cylinder" => (!coordinate(s("center"), 3, false)
                                || !positive_number_or_template(s("radius"))
                                || !positive_number_or_template(s("height")))
                            .then_some("area cylinder values are invalid"),
                            "sphere" => (!coordinate(s("center"), 3, false)
                                || !positive_number_or_template(s("radius")))
                            .then_some("area sphere values are invalid"),
                            "cone" => (!coordinate(s("apex"), 3, false)
                                || !coordinate(s("direction"), 3, false)
                                || !positive_number_or_template(s("length"))
                                || !positive_number_or_template(s("radius")))
                            .then_some("area cone values are invalid"),
                            _ => (!["origin", "direction", "up"].iter().all(|f| coordinate(s(f), 3, false))
                                || !["length", "width", "height"]
                                    .iter()
                                    .all(|f| positive_number_or_template(s(f))))
                            .then_some("area prism values are invalid"),
                        };
                        if let Some(message) = invalid {
                            errors.push(format!("{label} {message}"));
                        }
                    }
                }
                _ => errors.push(format!("{label} area shape is invalid")),
            }
            spatial_include(&mut errors, &label, q("include"), &["geometry", "entities", "zones"]);
            spatial_limit(&mut errors, &label, q("max_results"), "max_results", 1, 4096);
        }
    }
    Ok(errors)
}

/// `_validate_request_choice_effect(...)`.
fn validate_request_choice_effect(
    tool_name: &str,
    effect: &Data,
    body: &Data,
    path: &str,
) -> PyResult<Errors> {
    let mut errors = Errors::new();
    let Data::Dict(body_pairs) = body else { unreachable!() };
    let unknown = keys_except(body_pairs, &["choice_id", "prompt", "options"]);
    if !unknown.is_empty() {
        errors.push(format!(
            "Tool '{tool_name}': {path} request_choice contains unknown field '{}'",
            to_str(&sorted(&unknown)?[0])
        ));
    }
    if !text(body.get_str("choice_id")).is_some_and(is_choice_identity) {
        errors.push(format!("Tool '{tool_name}': {path} choice_id is invalid"));
    }
    if !text(body.get_str("prompt")).is_some_and(|prompt| !prompt.is_empty() && prompt.len() <= 4096) {
        errors.push(format!("Tool '{tool_name}': {path} choice prompt is invalid"));
    }
    if !text(effect.get_str("store_as")).is_some_and(|store| store.starts_with('_')) {
        errors.push(format!("Tool '{tool_name}': {path} request_choice requires private store_as"));
    }
    let options = body.get_str("options");
    if text(options).is_some_and(is_private_identifier) {
        return Ok(errors);
    }
    let Some(Data::List(options)) =
        options.filter(|o| matches!(o, Data::List(items) if (2..=16).contains(&items.len())))
    else {
        errors.push(format!("Tool '{tool_name}': {path} choice options must contain 2 to 16 items"));
        return Ok(errors);
    };
    let mut option_ids: Vec<String> = Vec::new();
    for (index, option) in options.iter().enumerate() {
        let shape_ok = matches!(option, Data::Dict(pairs) if keys_equal(pairs, &["id", "label"]));
        if !shape_ok {
            errors.push(format!("Tool '{tool_name}': {path} option {index} is invalid"));
            continue;
        }
        match text(option.get_str("id")) {
            Some(id) if is_choice_identity(id) && !option_ids.iter().any(|known| known == id) => {
                option_ids.push(id.to_owned())
            }
            _ => errors.push(format!("Tool '{tool_name}': {path} option {index} identity is invalid")),
        }
        if !text(option.get_str("label")).is_some_and(|label| !label.is_empty() && label.len() <= 1024) {
            errors.push(format!("Tool '{tool_name}': {path} option {index} label is invalid"));
        }
    }
    Ok(errors)
}

// ── Procedures ─────────────────────────────────────────────────────────────────

/// `_validate_procedures(procedures, all_tool_names)`.
fn validate_procedures(procedures: &[(Data, Data)], all_tool_names: &[Data]) -> PyResult<Errors> {
    let mut errors = Errors::new();
    let triggers: Vec<Data> = VALID_TRIGGERS.iter().map(|name| Data::Str((*name).to_owned())).collect();
    for (proc_name, proc_def) in procedures {
        let name = to_str(proc_name);
        let Data::Dict(_) = proc_def else {
            errors.push(format!("Procedure '{name}': must be a mapping"));
            continue;
        };
        let trigger = proc_def.get_str("trigger").cloned().unwrap_or(Data::None);
        if !truthy(&trigger) {
            errors.push(format!("Procedure '{name}': missing 'trigger'"));
        } else if !text(Some(&trigger)).is_some_and(is_trigger_name) {
            let target = Data::Str(to_str(&trigger));
            let hint =
                suggest(&target, &triggers)?.map_or(String::new(), |near| format!(" Did you mean '{near}'?"));
            errors.push(format!(
                "Procedure '{name}': unknown trigger '{}'.{hint} Triggers must use neutral on_* snake_case identity.",
                to_str(&trigger)
            ));
        }
        if let Some(Data::Str(condition)) = proc_def.get_str("condition") {
            if let Some(error) = expression_error(condition) {
                errors
                    .push(format!("Procedure '{name}': invalid condition expression '{condition}': {error}"));
            }
        }
        if let Some(Data::List(steps)) = proc_def.get_str("steps").or(Some(&Data::List(Vec::new()))) {
            for (index, step) in steps.iter().enumerate() {
                if !matches!(step, Data::Dict(_)) {
                    continue;
                }
                let mut found = Vec::new();
                iter_effects(&Data::List(vec![step.clone()]), &mut found);
                let spatial = found.iter().any(|effect| {
                    ["spatial_query", "spatial_mutation", "shot_effect"]
                        .iter()
                        .any(|key| effect.get_str(key).is_some())
                });
                if spatial {
                    errors.extend(validate_single_effect(
                        &format!("procedure {name}"),
                        step,
                        &format!("step {index}"),
                    )?);
                }
                if let Some(call @ Data::Str(call_name)) = step.get_str("call") {
                    if truthy(call)
                        && !all_tool_names.iter().any(|known| sylvia_base::pyyaml::data_key_eq(known, call))
                    {
                        let hint = suggest(call, all_tool_names)?
                            .map_or(String::new(), |near| format!(" Did you mean '{near}'?"));
                        errors.push(format!("Procedure '{name}': calls undefined tool '{call_name}'.{hint}"));
                    }
                }
            }
        }
    }
    Ok(errors)
}

/// `_suggest(target, options)`: the closest option within an edit distance of 3, as `str()`.
///
/// Python takes the first minimum in set order, which hash randomisation varies between runs;
/// ties here go to the first in sorted order of `str()` (`differences.md`). An option without a
/// length raises CPython's `TypeError`, as `_levenshtein` does.
pub fn suggest(target: &Data, options: &[Data]) -> PyResult<Option<String>> {
    let target_items = pydata::sized_items(target)?;
    let mut scored = Vec::with_capacity(options.len());
    for option in options {
        scored.push((levenshtein_items(&target_items, &pydata::sized_items(option)?), to_str(option)));
    }
    scored.sort();
    Ok(scored.into_iter().next().filter(|(distance, _)| *distance <= 3).map(|(_, text)| text))
}

/// `_levenshtein(a, b)` over code points.
pub fn levenshtein(a: &str, b: &str) -> usize {
    let a: Vec<Data> = a.chars().map(|c| Data::Str(c.to_string())).collect();
    let b: Vec<Data> = b.chars().map(|c| Data::Str(c.to_string())).collect();
    levenshtein_items(&a, &b)
}

/// `_levenshtein` over the items Python iterates (characters, bytes as ints, tuple items).
fn levenshtein_items(a: &[Data], b: &[Data]) -> usize {
    let (a, b) = if a.len() < b.len() { (b, a) } else { (a, b) };
    if b.is_empty() {
        return a.len();
    }
    let mut previous: Vec<usize> = (0..=b.len()).collect();
    for (i, ca) in a.iter().enumerate() {
        let mut current = vec![i + 1];
        for (j, cb) in b.iter().enumerate() {
            let insert = previous[j + 1] + 1;
            let delete = current[j] + 1;
            let substitute = previous[j] + usize::from(!pydata::py_eq(ca, cb));
            current.push(insert.min(delete).min(substitute));
        }
        previous = current;
    }
    previous[b.len()]
}

#[cfg(test)]
mod tests {
    #[test]
    fn recursive_yaml_is_refused() {
        assert_eq!(super::validate_ruleset("&a {x: *a}"), Err(super::RulesetTextError::Recursive));
        assert_eq!(
            super::validate_ruleset("&a [*a]"),
            Err(super::RulesetTextError::Recursive),
            "refused before the top-level type check"
        );
    }

    use super::*;

    #[test]
    fn patterns_follow_python_re() {
        assert!(
            is_snake_tool_name("roll_check") && is_snake_tool_name("roll\n") && !is_snake_tool_name("Roll")
        );
        assert!(is_kebab("srd-lite") && is_kebab("a1-b2\n") && !is_kebab("a--b") && !is_kebab("1a"));
        assert!(is_trigger_name("on_turn_start") && !is_trigger_name("on_turn_start\n"));
        assert!(is_spatial_template("{a.b}") && !is_spatial_template("{a.}") && !is_spatial_template("{}"));
        assert!(is_state_path("state.map.grid") && !is_state_path("state."));
        assert!(is_choice_identity("a.b:c-d_1") && !is_choice_identity(".a"));
        assert!(is_short_identifier(&"a".repeat(64)) && !is_short_identifier(&"a".repeat(65)));
    }

    #[test]
    fn levenshtein_matches_python() {
        assert_eq!(levenshtein("kitten", "sitting"), 3);
        assert_eq!(levenshtein("", "abc"), 3);
        let triggers: Vec<Data> = VALID_TRIGGERS.iter().map(|t| Data::Str((*t).into())).collect();
        let near = suggest(&Data::Str("on_turn_strat".into()), &triggers).unwrap();
        assert_eq!(near.as_deref(), Some("on_turn_start"));
        assert!(suggest(&Data::Str("x".into()), &[Data::Int(1)]).is_err());
    }
}
