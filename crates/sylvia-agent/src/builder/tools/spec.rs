//! `tools/_spec.py`, `tools/_toolsets.py` and `tools/_converter.py`.

use super::super::frozen_type_name;
use super::super::mode::AgentMode;
use std::collections::BTreeSet;
use std::sync::Arc;
use sylvia_base::CodedError;
use sylvia_base::{CodedResult, Json, JsonMap};

/// `ToolHandler`: `(params) -> dict`. An `Err` is the exception the Python handler raised, as
/// `str(exception)`; the registry reports it as a tool execution error.
pub type ToolHandler = Arc<dyn Fn(&JsonMap) -> Result<Json, String> + Send + Sync>;

/// `precondition`: whether the tool is available now.
pub type Precondition = Arc<dyn Fn() -> bool + Send + Sync>;

/// `result_validator`: a soft check of a result (it only logs in Python).
pub type ResultValidator = Arc<dyn Fn(&JsonMap) -> bool + Send + Sync>;

/// `ToolsetName`.
pub struct ToolsetName;

impl ToolsetName {
    pub const DM_TOOLS: &'static str = "dm_tools";
    pub const CAMPAIGN_TOOLS: &'static str = "campaign_tools";
    pub const BUILDER_TOOLS: &'static str = "builder_tools";
    pub const INTERVIEW_TOOLS: &'static str = "interview_tools";
    pub const COMMON_TOOLS: &'static str = "common_tools";
}

/// `resolve_mode_toolsets(mode)`.
pub fn resolve_mode_toolsets(mode: AgentMode) -> &'static [&'static str] {
    match mode {
        AgentMode::DmSession => &[ToolsetName::DM_TOOLS, ToolsetName::CAMPAIGN_TOOLS],
        AgentMode::BuilderFreeform => &[ToolsetName::BUILDER_TOOLS, ToolsetName::INTERVIEW_TOOLS],
        AgentMode::BuilderInterview => &[ToolsetName::INTERVIEW_TOOLS],
    }
}

/// `runtime._types.ToolParameter`, as far as tool specs use it.
#[derive(Debug, Clone, PartialEq)]
pub struct ToolParameter {
    pub name: String,
    pub type_: String,
    pub required: bool,
    pub default: Json,
    pub enum_values: Json,
    pub description: Json,
}

/// `ToolSpec`: a registered tool, its schema, handler and metadata.
#[derive(Clone)]
pub struct ToolSpec {
    pub name: String,
    pub description: Json,
    pub parameters: Vec<ToolParameter>,
    pub handler: ToolHandler,
    pub toolsets: BTreeSet<&'static str>,
    pub precondition: Option<Precondition>,
    pub metadata: JsonMap,
    pub result_validator: Option<ResultValidator>,
    pub parameters_schema: Option<Json>,
}

impl std::fmt::Debug for ToolSpec {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ToolSpec")
            .field("name", &self.name)
            .field("toolsets", &self.toolsets)
            .finish_non_exhaustive()
    }
}

fn key_error(key: &str) -> CodedError {
    CodedError::rejected(format!("'{key}'")).with_error_type("KeyError")
}

fn no_get(value: &Json) -> CodedError {
    CodedError::rejected(format!("'{}' object has no attribute 'get'", plain_type_name(value)))
        .with_error_type("AttributeError")
}

/// `type(value).__name__` for plain (not frozen) JSON.
fn plain_type_name(value: &Json) -> &'static str {
    match value {
        Json::Array(_) => "list",
        Json::Object(_) => "dict",
        other => frozen_type_name(other),
    }
}

/// `mapping.get(key, default)`, refusing a non-mapping as Python's attribute lookup does.
fn get_or<'a>(mapping: &'a Json, key: &str, default: &'a Json) -> CodedResult<&'a Json> {
    match mapping {
        Json::Object(map) => Ok(map.get(key).unwrap_or(default)),
        other => Err(no_get(other)),
    }
}

/// `_json_type_to_tool_param_type(json_type)`: the type map hashes its key first.
fn tool_param_type(json_type: &Json) -> CodedResult<String> {
    if matches!(json_type, Json::Array(_) | Json::Object(_)) {
        return Err(CodedError::rejected(format!("unhashable type: '{}'", plain_type_name(json_type)))
            .with_error_type("TypeError"));
    }
    Ok(match json_type.as_str() {
        Some(kind @ ("string" | "integer" | "number" | "boolean" | "array" | "object")) => kind.to_owned(),
        _ => "string".to_owned(),
    })
}

impl ToolSpec {
    /// `ToolSpec.from_openai_schema(definition, handler=..., toolsets=...)`: the full model schema
    /// kept beside the parameter descriptors.
    pub fn from_openai_schema(
        definition: &Json,
        handler: ToolHandler,
        toolsets: &[&'static str],
    ) -> CodedResult<Self> {
        let function = match definition {
            Json::Object(map) => map.get("function").ok_or_else(|| key_error("function"))?,
            _ => {
                return Err(CodedError::rejected("list indices must be integers or slices, not str")
                    .with_error_type("TypeError"))
            }
        };
        let Json::Object(function_map) = function else { return Err(no_get(function)) };
        let name = function_map.get("name").ok_or_else(|| key_error("name"))?;
        let description = function_map.get("description").cloned().unwrap_or_else(|| Json::from(""));
        let default_schema = serde_json::json!({"type": "object", "properties": {}});
        let schema = function_map.get("parameters").unwrap_or(&default_schema).clone();
        let empty = Json::Object(JsonMap::new());
        let properties = get_or(&schema, "properties", &empty)?.clone();
        let empty_list = Json::Array(Vec::new());
        let required: Vec<Json> = match get_or(&schema, "required", &empty_list)? {
            Json::Array(items) => items.clone(),
            Json::Object(map) => map.keys().map(|key| Json::from(key.as_str())).collect(),
            Json::String(text) => text.chars().map(|c| Json::from(c.to_string())).collect(),
            other => {
                return Err(CodedError::rejected(format!(
                    "'{}' object is not iterable",
                    plain_type_name(other)
                ))
                .with_error_type("TypeError"))
            }
        };
        let mut parameters = Vec::new();
        let Json::Object(properties) = properties else { return Err(no_get(&properties)) };
        for (parameter, parameter_schema) in &properties {
            let string = Json::from("string");
            let type_ = tool_param_type(get_or(parameter_schema, "type", &string)?)?;
            parameters.push(ToolParameter {
                name: parameter.clone(),
                type_,
                required: required.iter().any(|item| item.as_str() == Some(parameter.as_str())),
                default: get_or(parameter_schema, "default", &Json::Null)?.clone(),
                enum_values: get_or(parameter_schema, "enum", &Json::Null)?.clone(),
                description: get_or(parameter_schema, "description", &Json::from(""))?.clone(),
            });
        }
        Ok(Self {
            name: name.as_str().map(str::to_owned).unwrap_or_else(|| sylvia_domain::pyops::py_str(name)),
            description,
            parameters,
            handler,
            toolsets: toolsets.iter().copied().collect(),
            precondition: None,
            metadata: JsonMap::new(),
            result_validator: None,
            parameters_schema: Some(schema),
        })
    }
}

/// `_param_to_json_schema(param)`.
fn parameter_schema(parameter: &ToolParameter) -> Json {
    let mut schema = JsonMap::new();
    schema.insert("type".into(), Json::from(parameter.type_.as_str()));
    let description = if sylvia_domain::pyops::py_truthy(&parameter.description) {
        parameter.description.clone()
    } else {
        Json::from("")
    };
    schema.insert("description".into(), description);
    if parameter.type_ == "array" {
        schema.insert("items".into(), serde_json::json!({"type": "string"}));
    }
    if sylvia_domain::pyops::py_truthy(&parameter.enum_values) {
        schema.insert("enum".into(), parameter.enum_values.clone());
    }
    Json::Object(schema)
}

/// `tool_spec_to_openai(spec)`: the OpenAI function-calling definition.
pub fn tool_spec_to_openai(spec: &ToolSpec) -> Json {
    let parameters = match &spec.parameters_schema {
        Some(schema) => schema.clone(),
        None => {
            let mut properties = JsonMap::new();
            let mut required = Vec::new();
            for parameter in &spec.parameters {
                properties.insert(parameter.name.clone(), parameter_schema(parameter));
                if parameter.required && parameter.default.is_null() {
                    required.push(Json::from(parameter.name.as_str()));
                }
            }
            serde_json::json!({"type": "object", "properties": properties, "required": required})
        }
    };
    serde_json::json!({
        "type": "function",
        "function": {"name": spec.name, "description": spec.description, "parameters": parameters},
    })
}
