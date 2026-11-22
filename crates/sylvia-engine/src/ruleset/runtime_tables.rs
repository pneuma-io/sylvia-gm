//! `RulesetRuntime(None, data)` from `runtime/_runtime.py`, its loading step only
//! (`_parse_ruleset`): the tool and procedure tables a package runtime is checked against.
//!
//! Python keeps whatever values the Ruleset holds (the dataclasses do no validation), so the
//! fields here are the loaded [`Data`] as is. Execution comes later in the port.

use super::parameter_declarations::resolve_parameter_declarations;
use super::pydata::{self, truthy};
use super::schema_validator::validate_parameter_declarations;
use crate::pyerror::{PyError, PyResult};
use sylvia_base::pyyaml::Data;

/// `ToolParameter`: one declared tool parameter, its fields as authored.
#[derive(Debug, Clone, PartialEq)]
pub struct ToolParameter {
    pub name: Data,
    pub kind: Data,
    pub resolve: Data,
    pub required: Data,
    pub default: Data,
    pub enum_values: Data,
    pub description: Data,
    pub minimum: Data,
    pub maximum: Data,
    pub items: Data,
    pub min_items: Data,
    pub max_items: Data,
    pub unique_items: Data,
    pub from_state: Data,
}

/// `ToolDef`.
#[derive(Debug, Clone, PartialEq)]
pub struct ToolDef {
    pub name: Data,
    pub description: Data,
    pub parameters: Vec<ToolParameter>,
    pub pre_conditions: Data,
    pub effects: Data,
    pub narrative: Data,
    pub metadata: Data,
    pub range: Data,
}

/// `ProcedureDef`.
#[derive(Debug, Clone, PartialEq)]
pub struct ProcedureDef {
    pub name: Data,
    pub trigger: Data,
    pub description: Data,
    pub condition: Data,
    pub steps: Data,
    pub narrative: Data,
}

/// The loaded tables, in the Ruleset's order.
#[derive(Debug, Clone, PartialEq)]
pub struct RuntimeTables {
    /// The resolved Ruleset data (`runtime._ruleset`).
    pub ruleset: Vec<(Data, Data)>,
    pub tools: Vec<(Data, ToolDef)>,
    pub procedures: Vec<(Data, ProcedureDef)>,
    /// `permanently_refused_tool_names`: tools whose first effect is a literal-false `require`.
    pub permanently_refused: Vec<Data>,
}

impl RuntimeTables {
    /// `runtime.tools.get(name)`.
    pub fn tool(&self, name: &str) -> Option<&ToolDef> {
        self.tools
            .iter()
            .find(|(key, _)| matches!(key, Data::Str(text) if text == name))
            .map(|(_, tool)| tool)
    }
}

/// `value.get(key, default) or default_when_falsy`.
fn get_or(map: &Data, key: &str, default: Data) -> Data {
    match map.get_str(key) {
        Some(value) if truthy(value) => value.clone(),
        _ => default,
    }
}

fn get(map: &Data, key: &str, default: Data) -> Data {
    map.get_str(key).cloned().unwrap_or(default)
}

/// `for pd in value` over what `defn.get("parameters", []) or []` gives: the dicts among a
/// list's items. A mapping iterates its keys and text its characters, neither of which is a
/// dict; anything else is not iterable.
fn parameter_items(value: &Data) -> PyResult<Vec<&Data>> {
    match value {
        Data::List(items) | Data::Tuple(items) | Data::Set(items) => {
            Ok(items.iter().filter(|item| matches!(item, Data::Dict(_))).collect())
        }
        Data::Dict(_) | Data::Str(_) | Data::Bytes(_) => Ok(Vec::new()),
        other => Err(PyError::type_error(format!("'{}' object is not iterable", pydata::type_name(other)))),
    }
}

/// `RulesetRuntime(None, data)`: Python raises `ValueError` with the declaration errors joined
/// by `"; "`, or what building the tables raises.
pub fn load_runtime_tables(data: &[(Data, Data)]) -> PyResult<RuntimeTables> {
    let declaration_errors = validate_parameter_declarations(data)?;
    if !declaration_errors.is_empty() {
        return Err(PyError::value(declaration_errors.join("; ")));
    }
    let ruleset = resolve_parameter_declarations(data).map_err(|error| PyError::value(error.message))?;
    let root = Data::Dict(ruleset.clone());
    let mut tools = Vec::new();
    if let Some(Data::Dict(raw_tools)) = root.get_str("tools") {
        for (name, definition) in raw_tools {
            if !matches!(definition, Data::Dict(_)) {
                continue;
            }
            let mut parameters = Vec::new();
            for parameter in parameter_items(&get_or(definition, "parameters", Data::List(Vec::new())))? {
                parameters.push(ToolParameter {
                    name: get(parameter, "name", Data::Str(String::new())),
                    kind: get(parameter, "type", Data::Str("string".into())),
                    resolve: get(parameter, "resolve", Data::None),
                    required: get(parameter, "required", Data::Bool(true)),
                    default: get(parameter, "default", Data::None),
                    enum_values: get(parameter, "enum_values", Data::None),
                    description: get(parameter, "description", Data::None),
                    minimum: get(parameter, "minimum", Data::None),
                    maximum: get(parameter, "maximum", Data::None),
                    items: get(parameter, "items", Data::None),
                    min_items: get(parameter, "min_items", Data::None),
                    max_items: get(parameter, "max_items", Data::None),
                    unique_items: get(parameter, "unique_items", Data::None),
                    from_state: get(parameter, "from_state", Data::None),
                });
            }
            let tool = ToolDef {
                name: name.clone(),
                description: get(definition, "description", Data::Str(String::new())),
                parameters,
                pre_conditions: get_or(definition, "pre_conditions", Data::List(Vec::new())),
                effects: get_or(definition, "effects", Data::List(Vec::new())),
                narrative: get_or(definition, "narrative", Data::Dict(Vec::new())),
                metadata: get_or(definition, "metadata", Data::Dict(Vec::new())),
                range: get(definition, "range", Data::None),
            };
            // A later duplicate key replaces the earlier tool in place, as a dict assignment does.
            match tools
                .iter_mut()
                .find(|(key, _): &&mut (Data, ToolDef)| sylvia_base::pyyaml::data_key_eq(key, name))
            {
                Some(slot) => slot.1 = tool,
                None => tools.push((name.clone(), tool)),
            }
        }
    }
    let mut permanently_refused = Vec::new();
    for (name, tool) in &tools {
        if refused_from_the_start(&tool.effects)? {
            permanently_refused.push(name.clone());
        }
    }
    let mut procedures = Vec::new();
    if let Some(Data::Dict(raw_procedures)) = root.get_str("procedures") {
        for (name, definition) in raw_procedures {
            if !matches!(definition, Data::Dict(_)) {
                continue;
            }
            procedures.push((
                name.clone(),
                ProcedureDef {
                    name: name.clone(),
                    trigger: get(definition, "trigger", Data::Str(String::new())),
                    description: get(definition, "description", Data::Str(String::new())),
                    condition: get(definition, "condition", Data::None),
                    steps: get_or(definition, "steps", Data::List(Vec::new())),
                    narrative: get_or(definition, "narrative", Data::Dict(Vec::new())),
                },
            ));
        }
    }
    Ok(RuntimeTables { ruleset, tools, procedures, permanently_refused })
}

/// `tool.effects and isinstance(tool.effects[0].get("require"), dict) and (condition is False
/// or condition == "false")`.
fn refused_from_the_start(effects: &Data) -> PyResult<bool> {
    if !truthy(effects) {
        return Ok(false);
    }
    let first = match effects {
        Data::List(items) | Data::Tuple(items) => &items[0],
        Data::Dict(_) => return Err(PyError::of(crate::pyerror::PyErrorKind::KeyError, "0")),
        Data::Str(_) => {
            return Err(PyError::of(
                crate::pyerror::PyErrorKind::AttributeError,
                "'str' object has no attribute 'get'",
            ))
        }
        other => {
            return Err(PyError::type_error(format!(
                "'{}' object is not subscriptable",
                pydata::type_name(other)
            )))
        }
    };
    let Data::Dict(_) = first else {
        return Err(PyError::of(
            crate::pyerror::PyErrorKind::AttributeError,
            format!("'{}' object has no attribute 'get'", pydata::type_name(first)),
        ));
    };
    let Some(require @ Data::Dict(_)) = first.get_str("require") else {
        return Ok(false);
    };
    Ok(match require.get_str("condition") {
        Some(Data::Bool(false)) => true,
        Some(condition) => pydata::py_eq(condition, &Data::Str("false".into())),
        None => false,
    })
}
