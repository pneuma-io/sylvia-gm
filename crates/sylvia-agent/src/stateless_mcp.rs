//! `agent/_stateless_mcp.py`: the package-bound tool a model provider may be shown.
//!
//! [`ModelMechanicsTool`] and the action-sequence queue's tools ([`action_sequence_model_tools`])
//! so far. `StatelessMcpMechanics`' turn calls come with the turn loop (T3); the projection a
//! provider is shown is [`crate::provider_session::tools`].

use sylvia_base::JsonMap;
pub use sylvia_domain::authority::ModelToolBehavior;

/// `ModelMechanicsTool`: a package-bound tool projection safe to advertise to a model provider.
#[derive(Debug, Clone, PartialEq)]
pub struct ModelMechanicsTool {
    pub package_id: String,
    pub package_version: String,
    pub package_sha256: String,
    pub name: String,
    pub capability: String,
    pub input_schema: JsonMap,
    /// `parameter_derivations`, `None` by default.
    pub parameter_derivations: Option<JsonMap>,
    /// `description`, `""` by default.
    pub description: String,
    /// `category`, `"mechanic"` by default.
    pub category: String,
    /// `model_behavior`, [`ModelToolBehavior::Optional`] by default.
    pub model_behavior: ModelToolBehavior,
}

impl ModelMechanicsTool {
    /// The tool with the dataclass's defaults for every optional field.
    pub fn new(
        package_id: impl Into<String>,
        package_version: impl Into<String>,
        package_sha256: impl Into<String>,
        name: impl Into<String>,
        capability: impl Into<String>,
        input_schema: JsonMap,
    ) -> Self {
        Self {
            package_id: package_id.into(),
            package_version: package_version.into(),
            package_sha256: package_sha256.into(),
            name: name.into(),
            capability: capability.into(),
            input_schema,
            parameter_derivations: None,
            description: String::new(),
            category: "mechanic".into(),
            model_behavior: ModelToolBehavior::Optional,
        }
    }
}

/// `ACTION_SEQUENCE_TOOL_NAMES`.
pub const ACTION_SEQUENCE_TOOL_NAMES: [&str; 4] = [
    "read_action_sequence_controls",
    "read_action_sequence",
    "compose_action_sequence",
    "execute_action_sequence",
];

impl ModelMechanicsTool {
    /// A tool from its exported fields (`dataclasses.asdict`, `model_behavior` as its value).
    fn from_export(value: &serde_json::Value) -> Self {
        let text = |key: &str| value[key].as_str().expect("an exported text").to_owned();
        let mut tool = Self::new(
            text("package_id"),
            text("package_version"),
            text("package_sha256"),
            text("name"),
            text("capability"),
            value["input_schema"].as_object().cloned().expect("an exported schema"),
        );
        tool.parameter_derivations = value["parameter_derivations"].as_object().cloned();
        tool.description = text("description");
        tool.category = text("category");
        tool.model_behavior =
            ModelToolBehavior::parse(&text("model_behavior")).expect("an exported behavior");
        tool
    }
}

/// `action_sequence_model_tools(owner)`: the application queue's four tools. Python advertises
/// them only when the owner has its queue services, which every real `AuthorityOwner` has, so
/// they are always offered here. Their model-read descriptions and schemas are exported from
/// the Python function to `action_sequence_tools.json` by `generate_action_sequence_tools.py`.
pub fn action_sequence_model_tools() -> Vec<ModelMechanicsTool> {
    static TOOLS: std::sync::OnceLock<Vec<ModelMechanicsTool>> = std::sync::OnceLock::new();
    TOOLS
        .get_or_init(|| {
            let exported: serde_json::Value =
                sylvia_base::pyjson::loads(include_str!("action_sequence_tools.json"))
                    .expect("an exported JSON list");
            exported
                .as_array()
                .expect("an exported JSON list")
                .iter()
                .map(ModelMechanicsTool::from_export)
                .collect()
        })
        .clone()
}
