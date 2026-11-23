//! `agent/_stateless_mcp.py`: the package-bound tool a model provider may be shown.
//!
//! [`ModelMechanicsTool`], the action-sequence queue's tools ([`action_sequence_model_tools`])
//! and a rejected call's correction contract ([`safe_error`]) so far. `StatelessMcpMechanics`'
//! turn calls come with the tool batch (T3b); the projection a provider is shown is
//! [`crate::provider_session::tools`].

use regex::Regex;
use serde_json::{json, Value as Json};
use std::collections::BTreeSet;
use std::sync::OnceLock;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{CodedError, JsonMap};
pub use sylvia_domain::authority::ModelToolBehavior;
use sylvia_domain::error::{DomainError, MechanicsDetails};

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

/// The parts of an `AuthorityError` that [`safe_error`] reads.
#[derive(Debug, Clone, Copy)]
pub struct AuthorityRefusal<'a> {
    /// `type(error).__name__`.
    pub error_type: &'a str,
    /// `error.code`.
    pub code: &'a str,
    /// `str(error)`: for a `RulesetPreconditionRejected`, its `safe_reason`.
    pub message: &'a str,
    /// The fields of a `MechanicsRejected` (or `RulesetPreconditionRejected`).
    pub mechanics: Option<&'a MechanicsDetails>,
}

impl<'a> AuthorityRefusal<'a> {
    /// An `AuthorityError` the domain raised; `None` for any other exception.
    pub fn of_domain(error: &'a DomainError) -> Option<Self> {
        match error {
            DomainError::Rejected { error_type, code, message, mechanics, .. } => {
                Some(Self { error_type, code, message, mechanics: mechanics.as_deref() })
            }
            _ => None,
        }
    }

    /// An `AuthorityError` reported as a coded error (its class kept in `error_type`).
    pub fn of_coded(error: &'a CodedError) -> Self {
        Self {
            error_type: error.error_type.unwrap_or("CommandRejected"),
            code: &error.code,
            message: &error.message,
            mechanics: None,
        }
    }

    /// `isinstance(error, MechanicsRejected)`.
    fn is_mechanics(&self) -> bool {
        matches!(self.error_type, "MechanicsRejected" | "RulesetPreconditionRejected")
    }
}

fn texts() -> &'static Json {
    static TEXTS: OnceLock<Json> = OnceLock::new();
    TEXTS.get_or_init(|| {
        serde_json::from_str(include_str!("provider_turn/tool_batch_texts.json"))
            .expect("exported texts are JSON")
    })
}

/// `_safe_error(error).structured_content`: the bounded correction contract a rejected tool
/// call returns, without forwarding arbitrary exception text. The call result's text is
/// [`tool_result_text`] of it, and it is an error result.
pub fn safe_error(error: AuthorityRefusal<'_>) -> JsonMap {
    static NAMED: OnceLock<[Regex; 2]> = OnceLock::new();
    let code = if error.code == "command_rejected" { "mechanics_validation_failed" } else { error.code };
    let details = error.mechanics.filter(|_| error.is_mechanics());
    let empty = MechanicsDetails::default();
    let field_paths: BTreeSet<String> = if error.is_mechanics() {
        details.unwrap_or(&empty).field_paths.iter().cloned().collect()
    } else {
        let named = NAMED.get_or_init(|| {
            [
                Regex::new(r"parameter '([A-Za-z][A-Za-z0-9_.-]{0,79})'").expect("static pattern"),
                Regex::new(r"field '([A-Za-z][A-Za-z0-9_.-]{0,79})'").expect("static pattern"),
            ]
        });
        named
            .iter()
            .flat_map(|pattern| pattern.captures_iter(error.message).map(|found| found[1].to_owned()))
            .collect()
    };
    let mut validation = JsonMap::new();
    validation.insert("field_paths".into(), json!(field_paths));
    if code == "campaign_knowledge_limit_invalid" {
        validation = JsonMap::new();
        validation.insert("field_paths".into(), json!(["limit"]));
        validation.insert("minimum".into(), json!(1));
        validation.insert("maximum".into(), json!(8));
        validation.insert("expected_type".into(), json!("integer"));
    } else if code == "campaign_reference_too_large" {
        validation = JsonMap::new();
        validation.insert("field_paths".into(), json!(["path"]));
    }
    if let Some(details) = details.filter(|details| !details.validation_issues.is_empty()) {
        validation.insert(
            "issues".into(),
            Json::Array(
                details.validation_issues.iter().map(|issue| Json::Object(issue.as_payload())).collect(),
            ),
        );
    }
    let reason = if error.error_type == "RulesetPreconditionRejected" {
        error.message.to_owned()
    } else {
        texts()["safe_error_reasons"][code]
            .as_str()
            .unwrap_or("Mechanics authority rejected the operation.")
            .to_owned()
    };
    let mut payload = JsonMap::new();
    payload.insert("status".into(), json!("rejected"));
    payload.insert("code".into(), Json::String(code.to_owned()));
    payload.insert("reason".into(), Json::String(reason));
    if let Some(details) = details.filter(|details| details.refusal_reasons.len() > 1) {
        payload.insert("reasons".into(), json!(details.refusal_reasons));
    }
    payload.insert("validation".into(), Json::Object(validation));
    payload.insert("state_changed".into(), Json::Bool(details.is_some_and(|details| details.state_changed)));
    payload
}

/// A tool result's text: `json.dumps(payload, sort_keys=True, separators=(",", ":"))`.
pub fn tool_result_text(payload: &JsonMap) -> String {
    pyjson::dumps(&Json::Object(payload.clone()), Dump::WIRE.sorted(true))
}
