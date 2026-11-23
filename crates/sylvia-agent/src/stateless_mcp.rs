//! `agent/_stateless_mcp.py`: the package-bound tool a model provider may be shown.
//!
//! Only [`ModelMechanicsTool`] is here so far. The catalog that builds these tools from the
//! MCP listing (`StatelessMcpMechanics`) is lane 3's port; the projection a provider is shown
//! is [`crate::provider_session::tools`].

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
