//! `runtime/_parameter_declarations.py`: package-local literal parameter declarations.
//!
//! A Ruleset may pool parameter descriptors under `parameter_declarations` and name them by ID in
//! a tool's `parameters` list. Resolving replaces each ID with a copy of its descriptor and leaves
//! everything else as it was. Each pooled descriptor must pass the fixed descriptor schema
//! (`jsonschema.Draft202012Validator(...).is_valid`, through lane 1's validator).

use super::pydata;
use serde_json::{json, Value as Json};
use std::sync::OnceLock;
use sylvia_base::pyyaml::Data;
use sylvia_domain::content::schema::{TypeChecker, Validator};

/// `PARAMETER_DECLARATION_ID_PATTERN` (`re.fullmatch`).
fn is_declaration_id(name: &str) -> bool {
    let mut chars = name.chars();
    chars.next().is_some_and(|c| c.is_ascii_lowercase())
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
}

/// `PARAMETER_DESCRIPTOR_SCHEMA`.
fn descriptor_schema() -> &'static Json {
    static SCHEMA: OnceLock<Json> = OnceLock::new();
    SCHEMA.get_or_init(|| {
        json!({
            "type": "object",
            "required": ["name"],
            "properties": {
                "name": {"type": "string", "minLength": 1},
                "type": {"enum": ["array", "boolean", "entity_id", "entity_id_list", "enum", "integer",
                                  "number", "object", "string"]},
                "description": {"type": "string"},
                "required": {"type": "boolean"},
                "enum_values": {"type": "array"},
                "minimum": {"type": "number"},
                "maximum": {"type": "number"},
                "items": {"type": "object", "additionalProperties": true},
                "from_state": {"type": "string"},
                "default": {},
                "resolve": {"enum": ["entity", "participant", "entity_list"]},
                "model_derive": {
                    "oneOf": [
                        {"type": "boolean"},
                        {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string"},
                                "source": {"type": "string"},
                                "field": {"type": "string"},
                                "participant": {"type": "string"},
                                "mapping": {"type": "object", "additionalProperties": true},
                                "present": {},
                                "absent": {},
                                "value": {}
                            },
                            "required": ["kind"],
                            "additionalProperties": false
                        }
                    ]
                },
                "model_hidden": {"type": "boolean"},
                "model_required": {"type": "boolean"},
                "min_items": {"type": "integer", "minimum": 0},
                "max_items": {"type": "integer", "minimum": 0},
                "unique_items": {"type": "boolean"}
            },
            "additionalProperties": false
        })
    })
}

/// `PARAMETER_DESCRIPTOR_FIELDS`.
pub const PARAMETER_DESCRIPTOR_FIELDS: &[&str] = &[
    "name",
    "type",
    "description",
    "required",
    "enum_values",
    "minimum",
    "maximum",
    "items",
    "from_state",
    "default",
    "resolve",
    "model_derive",
    "model_hidden",
    "model_required",
    "min_items",
    "max_items",
    "unique_items",
];

/// The descriptor fields whose schema constrains the value's type (all but `default`), and the
/// same for `model_derive`'s object form (all but `present`, `absent` and `value`).
const TYPED_FIELDS: &[&str] = &[
    "name",
    "type",
    "description",
    "required",
    "enum_values",
    "minimum",
    "maximum",
    "items",
    "from_state",
    "resolve",
    "model_derive",
    "model_hidden",
    "model_required",
    "min_items",
    "max_items",
    "unique_items",
];
const TYPED_DERIVE_FIELDS: &[&str] = &["kind", "source", "field", "participant", "mapping"];

/// A YAML value as JSON for the schema check: JSON types map across; a key that is not text,
/// a non-finite float, a date, bytes, a set or a tuple has no JSON form, and the caller has
/// already decided every place where that matters to the verdict.
fn as_json(value: &Data) -> Json {
    match value {
        Data::None => Json::Null,
        Data::Bool(flag) => json!(flag),
        Data::Int(number) => json!(number),
        Data::Float(number) => serde_json::Number::from_f64(*number).map_or(json!(0.0), Json::Number),
        Data::Str(text) => json!(text),
        Data::List(items) | Data::Tuple(items) | Data::Set(items) => {
            Json::Array(items.iter().map(as_json).collect())
        }
        Data::Dict(pairs) => {
            Json::Object(pairs.iter().map(|(key, item)| (pydata::to_str(key), as_json(item))).collect())
        }
        Data::Bytes(_) | Data::Date(_) | Data::DateTime(_) => Json::Null,
    }
}

fn json_kind(value: &Data) -> bool {
    !matches!(value, Data::Bytes(_) | Data::Date(_) | Data::DateTime(_) | Data::Set(_) | Data::Tuple(_))
}

/// `_PARAMETER_DESCRIPTOR_VALIDATOR.is_valid(descriptor)` for a dict descriptor.
fn descriptor_is_valid(descriptor: &[(Data, Data)]) -> bool {
    // jsonschema would see these as keys outside `properties` (refused by
    // additionalProperties) or values of no JSON type at a typed field (refused by `type` or
    // `enum`); the JSON form cannot carry either, so decide them here.
    for (key, value) in descriptor {
        let Data::Str(name) = key else { return false };
        if TYPED_FIELDS.contains(&name.as_str()) && !json_kind(value) {
            return false;
        }
        if matches!(value, Data::Float(number) if !number.is_finite())
            && matches!(name.as_str(), "minimum" | "maximum")
        {
            continue; // `inf` and `nan` are still numbers to jsonschema; 0.0 stands in.
        }
        if name == "model_derive" {
            if let Data::Dict(derive) = value {
                for (derive_key, derive_value) in derive {
                    let Data::Str(derive_name) = derive_key else { return false };
                    if TYPED_DERIVE_FIELDS.contains(&derive_name.as_str()) && !json_kind(derive_value) {
                        return false;
                    }
                }
            }
        }
    }
    let instance = as_json(&Data::Dict(descriptor.to_vec()));
    let validator = Validator::new(descriptor_schema(), TypeChecker::Draft202012)
        .expect("the descriptor schema compiles");
    validator.is_valid(&instance)
}

/// `ParameterDeclarationError(ValueError)`: its `str()`.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{message}")]
pub struct ParameterDeclarationError {
    /// `str(error)`.
    pub message: String,
}

fn declaration_error(message: impl Into<String>) -> ParameterDeclarationError {
    ParameterDeclarationError { message: message.into() }
}

/// What `resolve_parameter_declarations` can raise: its own error, or whatever the caller's
/// `validate_shape` raises.
#[derive(Debug, Clone, PartialEq)]
pub enum ResolveError<E> {
    /// `ParameterDeclarationError`.
    Declaration(ParameterDeclarationError),
    /// The `validate_shape` callback's error.
    Shape(E),
}

/// `resolve_parameter_declarations(data)` without a shape check.
pub fn resolve_parameter_declarations(
    data: &[(Data, Data)],
) -> Result<Vec<(Data, Data)>, ParameterDeclarationError> {
    resolve_parameter_declarations_with(data, &mut |_| Ok::<(), ()>(())).map_err(|error| match error {
        ResolveError::Declaration(error) => error,
        ResolveError::Shape(()) => unreachable!("no shape check"),
    })
}

/// `resolve_parameter_declarations(data, validate_shape=...)`: expand tool parameter IDs into
/// copies of their pooled descriptors, checking the pool first.
pub fn resolve_parameter_declarations_with<E>(
    data: &[(Data, Data)],
    validate_shape: &mut dyn FnMut(&Data) -> Result<(), E>,
) -> Result<Vec<(Data, Data)>, ResolveError<E>> {
    let fail = |message: String| ResolveError::Declaration(declaration_error(message));
    let empty = Data::Dict(Vec::new());
    let pool_value = lookup(data, "parameter_declarations").unwrap_or(&empty);
    let Data::Dict(pool) = pool_value else {
        return Err(fail("parameter_declarations must be a mapping".into()));
    };
    for (name, descriptor) in pool {
        let Data::Str(id) = name else {
            return Err(fail("Parameter declaration IDs must be literal snake-case text".into()));
        };
        if !is_declaration_id(id) {
            return Err(fail("Parameter declaration IDs must be literal snake-case text".into()));
        }
        let Data::Dict(fields) = descriptor else {
            return Err(fail(format!("Parameter declaration '{id}' must be a literal descriptor")));
        };
        if !descriptor_is_valid(fields) {
            return Err(fail(format!("Parameter declaration '{id}' has invalid descriptor fields")));
        }
        if !pydata::keys_except(fields, PARAMETER_DESCRIPTOR_FIELDS).is_empty() {
            return Err(fail(format!("Parameter declaration '{id}' has unknown descriptor fields")));
        }
        if !matches!(descriptor.get_str("name"), Some(Data::Str(text)) if !text.is_empty()) {
            return Err(fail(format!("Parameter declaration '{id}' requires a parameter name")));
        }
    }
    let empty_tools = Data::Dict(Vec::new());
    let tools_value = lookup(data, "tools").unwrap_or(&empty_tools);
    let Data::Dict(tools) = tools_value else {
        return Ok(data.to_vec());
    };
    let has_references = tools.iter().any(|(_, tool)| {
        matches!(tool.get_str("parameters"), Some(Data::List(parameters)) if parameters.iter().any(|p| matches!(p, Data::Str(_))))
            && matches!(tool, Data::Dict(_))
    });
    if lookup(data, "parameter_declarations").is_none() && !has_references {
        return Ok(data.to_vec());
    }
    let mut normalized: Vec<(Data, Data)> = data
        .iter()
        .filter(
            |(key, _)| !matches!(key, Data::Str(text) if text == "parameter_declarations" || text == "tools"),
        )
        .cloned()
        .collect();
    let mut resolved_tools = Vec::with_capacity(tools.len());
    for (name, tool) in tools {
        let mut copied = tool.clone();
        if let Data::Dict(fields) = tool {
            if let Some(parameters) = tool.get_str("parameters").filter(|p| !matches!(p, Data::None)) {
                let Data::List(parameters) = parameters else {
                    return Err(fail(format!(
                        "Tool '{}' parameters must be an ordered list",
                        pydata::to_str(name)
                    )));
                };
                let mut resolved = Vec::with_capacity(parameters.len());
                for parameter in parameters {
                    match parameter {
                        Data::Str(id) => {
                            match pool.iter().find(|(key, _)| matches!(key, Data::Str(k) if k == id)) {
                                Some((_, descriptor)) => resolved.push(descriptor.clone()),
                                None => {
                                    return Err(fail(format!(
                                        "Tool '{}' has unknown parameter declaration ID '{id}'",
                                        pydata::to_str(name)
                                    )))
                                }
                            }
                        }
                        Data::Dict(_) => resolved.push(parameter.clone()),
                        _ => {
                            return Err(fail(format!(
                                "Tool '{}' parameter must be a descriptor or ID",
                                pydata::to_str(name)
                            )))
                        }
                    }
                }
                let mut fields = fields.clone();
                set_field(&mut fields, "parameters", Data::List(resolved));
                copied = Data::Dict(fields);
            }
        }
        resolved_tools.push((name.clone(), copied));
    }
    normalized.push((Data::Str("tools".into()), Data::Dict(resolved_tools)));
    validate_shape(&Data::Dict(normalized.clone())).map_err(ResolveError::Shape)?;
    // Python's final copy puts `tools` last and, in each tool whose `parameters` is a list,
    // moves `parameters` to the end of the tool.
    if let Some((_, Data::Dict(tools))) = normalized.last_mut() {
        for (_, tool) in tools.iter_mut() {
            if let Data::Dict(fields) = tool {
                if let Some(position) = fields.iter().position(|(key, value)| {
                    matches!(key, Data::Str(text) if text == "parameters") && matches!(value, Data::List(_))
                }) {
                    let parameters = fields.remove(position);
                    fields.push(parameters);
                }
            }
        }
    }
    Ok(normalized)
}

fn lookup<'a>(pairs: &'a [(Data, Data)], name: &str) -> Option<&'a Data> {
    pairs.iter().find(|(key, _)| matches!(key, Data::Str(text) if text == name)).map(|(_, value)| value)
}

/// `mapping[name] = value`, keeping an existing key's position.
fn set_field(pairs: &mut Vec<(Data, Data)>, name: &str, value: Data) {
    match pairs.iter_mut().find(|(key, _)| matches!(key, Data::Str(text) if text == name)) {
        Some((_, slot)) => *slot = value,
        None => pairs.push((Data::Str(name.to_owned()), value)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identifiers_are_snake_case() {
        assert!(is_declaration_id("target_1"));
        assert!(!is_declaration_id("Target") && !is_declaration_id("1a") && !is_declaration_id(""));
        assert!(!is_declaration_id("a\n"));
    }
}
