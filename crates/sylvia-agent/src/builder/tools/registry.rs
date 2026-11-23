//! `tools/_registry.py`: the tool registry with toolset gating and dynamic availability.

use super::super::mode::AgentMode;
use super::spec::{resolve_mode_toolsets, tool_spec_to_openai, ToolSpec, ToolsetName};
use indexmap::IndexMap;
use sylvia_base::{Json, JsonMap};

/// `ToolRegistry`. Gating applies to the model's definitions only; `execute` dispatches any
/// registered tool, as in Python.
#[derive(Debug, Clone)]
pub struct ToolRegistry {
    tools: IndexMap<String, ToolSpec>,
    toolsets: IndexMap<&'static str, bool>,
}

impl Default for ToolRegistry {
    fn default() -> Self {
        Self::new()
    }
}

fn failure(error: String) -> JsonMap {
    let mut result = JsonMap::new();
    result.insert("success".into(), Json::Bool(false));
    result.insert("error".into(), Json::from(error));
    result
}

impl ToolRegistry {
    pub fn new() -> Self {
        let toolsets = [
            ToolsetName::DM_TOOLS,
            ToolsetName::CAMPAIGN_TOOLS,
            ToolsetName::BUILDER_TOOLS,
            ToolsetName::INTERVIEW_TOOLS,
            ToolsetName::COMMON_TOOLS,
        ]
        .into_iter()
        .map(|name| (name, true))
        .collect();
        Self { tools: IndexMap::new(), toolsets }
    }

    /// `register(tool)`: last write wins, keeping the first registration's place.
    pub fn register(&mut self, tool: ToolSpec) {
        self.tools.insert(tool.name.clone(), tool);
    }

    /// `unregister(name)`.
    pub fn unregister(&mut self, name: &str) {
        self.tools.shift_remove(name);
    }

    /// `get_tool(name)`.
    pub fn get_tool(&self, name: &str) -> Option<&ToolSpec> {
        self.tools.get(name)
    }

    fn enabled(&self, mode: AgentMode) -> Vec<&'static str> {
        resolve_mode_toolsets(mode)
            .iter()
            .copied()
            .filter(|name| self.toolsets.get(name) == Some(&true))
            .collect()
    }

    /// `get_definitions(mode)`: the model's definitions of every available tool in `mode`.
    pub fn get_definitions(&self, mode: AgentMode) -> Vec<Json> {
        let enabled = self.enabled(mode);
        self.tools
            .values()
            .filter(|tool| tool.toolsets.iter().any(|name| enabled.contains(name)))
            .filter(|tool| tool.precondition.as_ref().is_none_or(|available| available()))
            .map(tool_spec_to_openai)
            .collect()
    }

    /// `list_tools(mode)`.
    pub fn list_tools(&self, mode: Option<AgentMode>) -> Vec<&ToolSpec> {
        let Some(mode) = mode else { return self.tools.values().collect() };
        let enabled = self.enabled(mode);
        self.tools.values().filter(|tool| tool.toolsets.iter().any(|name| enabled.contains(name))).collect()
    }

    /// `execute(name, params, mode)`: the handler's dict, with at least `success`.
    pub fn execute(&self, name: &str, params: &JsonMap) -> JsonMap {
        let Some(tool) = self.tools.get(name) else {
            return failure(format!("Tool '{name}' not found in registry."));
        };
        let result = match (tool.handler)(params) {
            Ok(result) => result,
            Err(error) => return failure(format!("Tool execution error: {error}")),
        };
        let result = match result {
            Json::Object(result) if result.contains_key("success") => result,
            Json::Object(result) => {
                let mut assumed = JsonMap::new();
                assumed.insert("success".into(), Json::Bool(true));
                assumed.extend(result);
                assumed
            }
            _ => failure("Handler returned non-dict result".into()),
        };
        // The custom validator only logs in Python; it never changes the result.
        if let Some(validator) = &tool.result_validator {
            let _ = validator(&result);
        }
        result
    }

    /// `enable_toolset(name)`.
    pub fn enable_toolset(&mut self, name: &str) {
        if let Some(enabled) = self.toolsets.get_mut(name) {
            *enabled = true;
        }
    }

    /// `disable_toolset(name)`.
    pub fn disable_toolset(&mut self, name: &str) {
        if let Some(enabled) = self.toolsets.get_mut(name) {
            *enabled = false;
        }
    }

    /// `is_toolset_enabled(name)`.
    pub fn is_toolset_enabled(&self, name: &str) -> bool {
        self.toolsets.get(name) == Some(&true)
    }

    /// `validate_result(name, result)`.
    pub fn validate_result(&self, name: &str, result: &JsonMap) -> bool {
        self.tools
            .get(name)
            .and_then(|tool| tool.result_validator.as_ref())
            .is_none_or(|validator| validator(result))
    }
}
