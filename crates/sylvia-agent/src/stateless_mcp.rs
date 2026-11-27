//! `agent/_stateless_mcp.py`: the package-bound tool a model provider may be shown.
//!
//! [`ModelMechanicsTool`], the action-sequence queue's tools ([`action_sequence_model_tools`])
//! a rejected call's correction contract ([`safe_error`]), the queue tools' dispatch
//! ([`call_action_sequence_tool`]) and the tool listing ([`listing`]) so far. The mechanics
//! calls and the Campaign File commit come with T3m; the projection a provider is shown is
//! [`crate::provider_session::tools`].

pub mod campaign_file;
pub mod execute;
pub mod listing;

use crate::provider_session::reads::TurnOwner;
use regex::Regex;
use serde_json::{json, Value as Json};
use std::collections::BTreeSet;
use std::sync::OnceLock;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{CodedError, JsonMap};
pub use sylvia_domain::authority::ModelToolBehavior;
use sylvia_domain::authority::WriterRole;
use sylvia_domain::error::{DomainError, MechanicsDetails};
use sylvia_domain::player_actions::player_control_origin;
use sylvia_domain::schema_admission::validate_parameters;

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

/// `_sequence_selection_contract(surface, action)`: the raw inputs a panel action needs,
/// described without granting control or legality.
pub fn sequence_selection_contract(surface: &JsonMap, action: &JsonMap) -> JsonMap {
    let mut contract = JsonMap::new();
    let Some(Json::Object(bindings)) = action.get("bindings") else {
        contract.insert("required_keys".into(), json!([]));
        contract.insert("context".into(), json!({}));
        return contract;
    };
    let mut keys: BTreeSet<String> = bindings.values().filter_map(Json::as_str).map(str::to_owned).collect();
    let mut context = JsonMap::new();
    let renderer = surface.get("renderer").and_then(Json::as_str);
    let data = surface.get("data").and_then(Json::as_object);
    if matches!(renderer, Some("tactical_grid" | "coordinate_map")) {
        keys.remove("path"); // The authoritative owner derives paths from raw intent.
        keys.insert("map_revision".into());
        if let Some(revision) = data.and_then(|data| data.get("map_revision")) {
            context.insert("map_revision".into(), revision.clone());
        }
    } else if renderer == Some("network_graph") {
        keys.insert("graph_revision".into());
        if let Some(revision) = data.and_then(|data| data.get("graph_revision")) {
            context.insert("graph_revision".into(), revision.clone());
        }
    }
    contract.insert("required_keys".into(), json!(keys));
    contract.insert("context".into(), Json::Object(context));
    contract
}

fn key_error(key: &str) -> CodedError {
    CodedError::new("internal_error", sylvia_base::pystr::repr(key)).with_error_type("KeyError")
}

/// A domain exception as the caller sees it: a refusal in its class, anything else unexpected.
fn domain_error(error: DomainError) -> CodedError {
    error.rejection().unwrap_or_else(|| {
        CodedError::new("internal_error", error.message()).with_error_type(error.python_class())
    })
}

/// `call_action_sequence_tool(owner, campaign_id=..., session_id=..., name=..., parameters=...)`:
/// dispatch queue metadata to the owning GM's shared application service, never raw mechanics.
pub async fn call_action_sequence_tool(
    owner: &dyn TurnOwner,
    campaign_id: &str,
    session_id: &str,
    name: &str,
    parameters: &JsonMap,
) -> Result<JsonMap, AuthorityFailure> {
    let tools = action_sequence_model_tools();
    let Some(tool) = tools.iter().find(|tool| tool.name == name) else {
        return Err(CodedError::new(
            "action_sequence_unavailable",
            "Action sequence operation is unavailable",
        )
        .with_error_type("CommandRejected")
        .into());
    };
    validate_parameters(parameters, &tool.input_schema)?;
    let context = owner.session_context(session_id).await?;
    if context.session.campaign_id() != Some(campaign_id) || context.session.role() != WriterRole::Dm {
        return Err(CodedError::rejected("Action sequence belongs to another GM campaign")
            .with_error_type("CommandRejected")
            .into());
    }
    let result = match name {
        "read_action_sequence_controls" => {
            let panels = owner.campaign_panels(campaign_id).await?;
            // The reply's dict literal reads the revision before it builds the controls.
            let revision = panels.get("revision").cloned().ok_or_else(|| key_error("revision"))?;
            let surfaces: Vec<Json> =
                panels.get("surfaces").and_then(Json::as_array).cloned().unwrap_or_default();
            let mut controls = Vec::new();
            for surface in &surfaces {
                let surface_map = surface.as_object().cloned().unwrap_or_default();
                let actions =
                    surface_map.get("actions").and_then(Json::as_array).cloned().unwrap_or_default();
                for action in actions {
                    let action_map = action.as_object().cloned().unwrap_or_default();
                    let surface_id = surface_map.get("id").cloned().ok_or_else(|| key_error("id"))?;
                    let action_id = action_map.get("id").cloned().ok_or_else(|| key_error("id"))?;
                    controls.push(json!({
                        "source": {"kind": "panel", "surface_id": surface_id, "action_id": action_id},
                        "declaration": action,
                        "selection_contract": sequence_selection_contract(&surface_map, &action_map),
                    }));
                }
            }
            let mut payload = JsonMap::new();
            payload.insert("kind".into(), json!("action_sequence_controls"));
            payload.insert("campaign_id".into(), json!(campaign_id));
            payload.insert("revision".into(), revision);
            payload
                .insert("player_control".into(), panels.get("player_control").cloned().unwrap_or(Json::Null));
            payload.insert(
                "direct_actions_available".into(),
                panels.get("direct_actions_available").cloned().unwrap_or(Json::Bool(false)),
            );
            payload.insert("controls".into(), Json::Array(controls));
            payload.insert("surfaces".into(), Json::Array(surfaces));
            return Ok(payload);
        }
        "read_action_sequence" => {
            owner.action_sequence(session_id, parameters.get("sequence_id").unwrap_or(&Json::Null)).await?
        }
        "compose_action_sequence" => {
            let field = |key: &str| parameters.get(key).cloned().ok_or_else(|| key_error(key));
            let expected_revision = field("expected_revision")?;
            for step in field("steps")?.as_array().cloned().unwrap_or_default() {
                let source = step.get("source").cloned().ok_or_else(|| key_error("source"))?;
                let selection = step.get("selection").cloned().ok_or_else(|| key_error("selection"))?;
                player_control_origin(
                    &source,
                    expected_revision.as_i64().unwrap_or_default(),
                    selection.as_object(),
                    Some(session_id),
                )?;
            }
            let mut arguments = JsonMap::new();
            arguments.insert("sequence_id".into(), field("sequence_id")?);
            arguments.insert("expected_revision".into(), expected_revision);
            arguments.insert(
                "expected_version".into(),
                parameters.get("expected_version").cloned().unwrap_or(json!(0)),
            );
            arguments.insert("steps".into(), field("steps")?);
            owner.compose_action_sequence_from_agent(campaign_id, session_id, &arguments).await?
        }
        _ => {
            let field = |key: &str| parameters.get(key).cloned().ok_or_else(|| key_error(key));
            let mut arguments = JsonMap::new();
            arguments.insert("sequence_id".into(), field("sequence_id")?);
            arguments.insert("expected_version".into(), field("expected_version")?);
            arguments
                .insert("confirmation".into(), parameters.get("confirmation").cloned().unwrap_or(Json::Null));
            owner.execute_action_sequence_from_agent(campaign_id, session_id, &arguments).await?
        }
    };
    let mut payload = JsonMap::new();
    payload.insert("kind".into(), json!("action_sequence_result"));
    payload.insert("operation".into(), json!(name));
    payload.insert("result".into(), result);
    Ok(payload)
}

/// An exception an owner operation raised, in its Python class. A domain error keeps a
/// `MechanicsRejected`'s fields, which a coded error cannot carry.
#[derive(Debug, Clone)]
pub enum AuthorityFailure {
    Domain(DomainError),
    Coded(CodedError),
}

impl AuthorityFailure {
    /// The parts `_safe_error` reads, when the exception is an `AuthorityError`.
    pub fn refusal(&self) -> Option<AuthorityRefusal<'_>> {
        match self {
            Self::Domain(error) => AuthorityRefusal::of_domain(error),
            Self::Coded(error) => is_authority_error(error).then(|| AuthorityRefusal::of_coded(error)),
        }
    }

    /// Whether Python's `except CommandRejected` catches it (a subclass included).
    pub fn is_command_rejected(&self) -> bool {
        self.refusal().is_some_and(|refusal| {
            matches!(
                refusal.error_type,
                "CommandRejected" | "MechanicsRejected" | "RulesetPreconditionRejected"
            )
        })
    }

    /// The error as a caller that does not catch it sees it.
    pub fn into_coded(self) -> CodedError {
        match self {
            Self::Domain(error) => domain_error(error),
            Self::Coded(error) => error,
        }
    }
}

impl From<CodedError> for AuthorityFailure {
    fn from(error: CodedError) -> Self {
        Self::Coded(error)
    }
}

impl From<DomainError> for AuthorityFailure {
    fn from(error: DomainError) -> Self {
        Self::Domain(error)
    }
}

/// Whether a coded error is a Python `AuthorityError` (a `RuntimeError` the turn catches).
pub fn is_authority_error(error: &CodedError) -> bool {
    matches!(
        error.error_type,
        None | Some(
            "AuthorityError"
                | "CommandRejected"
                | "MechanicsRejected"
                | "RulesetPreconditionRejected"
                | "StaleRevision"
                | "WrongOwner"
                | "WrongWriter"
                | "IdempotencyConflict"
                | "OwnerConflict"
                | "SchemaIdentityError"
                | "PortableRecoveryFailure"
        )
    )
}
