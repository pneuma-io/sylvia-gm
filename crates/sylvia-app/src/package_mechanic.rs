//! `application/package_runtime.py`'s execution: `materialize_package_mechanic`, the mechanic
//! closure it returns, and `_runtime_parameters` with `_bound_runtime_parameter`.
//!
//! The mechanic runs one admitted tool against an in-memory copy of the package's state roots
//! (`sylvia_engine::exec::MemoryStateBridge`) and returns a `MechanicOutcome`; it never touches
//! storage. Spatial effects (`spatial_query`, `spatial_mutation`, `shot_effect`, `aoe`) answer
//! [`MechanicFailure::PortPending`] until lane 2's spatial effect handlers land. Without them
//! no spatial engine is ever opened, so the bridge's spatial private variables, evidence and
//! shot presentation are empty, and the result carries none of them, as in Python.

use crate::package_runtime::{parse_runtime, validate_runtime_declaration, PackageRuntimeFailure};
use crate::result_cards::{project_activity_label, project_result_card};
use std::cell::Cell;
use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};
use sylvia_base::pyyaml::Data;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{MechanicContext, MechanicOutcome};
use sylvia_domain::error::DomainError;
use sylvia_domain::package_library::{canonical_json_bytes, ToolDeclaration};
use sylvia_engine::dice::Dice;
use sylvia_engine::exec::bridge::{json_safe, mutable_json, nested_get};
use sylvia_engine::exec::context::{deep_copy, ChoiceControl, NestedMode, ProcedureExecutor};
use sylvia_engine::exec::procedures::ExecProcedure;
use sylvia_engine::exec::{
    execute_tool, Combat, ExecError, ExecTool, MemoryStateBridge, StateBridge, ToolHost,
};
use sylvia_engine::expr::value::{Dict, Value};
use sylvia_engine::ruleset::runtime_tables::RuntimeTables;

/// Why one mechanic execution produced no outcome.
#[derive(Debug, Clone, PartialEq)]
pub enum MechanicFailure {
    /// `RulesetPreconditionRejected(safe_reason, reasons=...)`: the Ruleset refusing, which the
    /// owner re-raises.
    Rejected(DomainError),
    /// Any other exception: its class, `str(error)` and `str(error.__cause__)`, which the owner
    /// records in its `ruleset_execution_defect` audit.
    Failed { class: &'static str, message: String, cause: Option<String> },
    /// Code the port has not reached yet, named (`not yet ported: ...`); the owner answers
    /// `rust_port_pending`.
    PortPending(String),
}

impl MechanicFailure {
    fn package_runtime(message: &str, cause: Option<String>) -> Self {
        Self::Failed { class: "PackageRuntimeError", message: message.into(), cause }
    }
}

impl From<ExecError> for MechanicFailure {
    /// An exception leaving the mechanic as it was raised (no `__cause__`).
    fn from(error: ExecError) -> Self {
        match error {
            ExecError::PortPending(message) => Self::PortPending(message),
            other => Self::Failed { class: other.class_name(), message: other.message(), cause: None },
        }
    }
}

/// A Ruleset refusal, or the `ValueError` Python raises while constructing one whose reason is
/// not model-safe.
fn rejected(reason: &str, reasons: &[String]) -> MechanicFailure {
    match DomainError::ruleset_precondition_rejected(reason, reasons) {
        Ok(error) => MechanicFailure::Rejected(error),
        Err(error) => {
            MechanicFailure::Failed { class: "ValueError", message: error.message().into(), cause: None }
        }
    }
}

/// The tools, configuration, definitions and procedures a package mechanic executes with.
///
/// Python gives each mechanic a private deep copy of `ruleset_config` and `definitions` on its
/// first execution and keeps it for later executions. Execution never writes them here (values
/// are copy-on-write), so one host is shared by every mechanic of a runtime; the open ledger
/// item on list aliases inside definitions covers the one way Python could write its copy.
pub(crate) struct PackageHost {
    tools: HashMap<String, ExecTool>,
    ruleset_config: Value,
    definitions: Value,
    procedures: Vec<ExecProcedure>,
}

impl PackageHost {
    fn new(runtime: &RuntimeTables) -> Self {
        let section = |key: &str| match runtime
            .ruleset
            .iter()
            .find(|(k, _)| matches!(k, Data::Str(text) if text == key))
        {
            Some((_, value @ Data::Dict(_))) => sylvia_engine::exec::tool::data_value(value),
            _ => Value::dict(Dict::new()),
        };
        Self {
            tools: runtime
                .tools
                .iter()
                .filter_map(|(name, tool)| match name {
                    Data::Str(name) => Some((name.clone(), ExecTool::from_def(name, tool))),
                    _ => None,
                })
                .collect(),
            ruleset_config: section("features"),
            definitions: section("definitions"),
            procedures: runtime
                .procedures
                .iter()
                .filter_map(|(name, procedure)| match name {
                    Data::Str(name) => Some(ExecProcedure::from_def(name, procedure)),
                    _ => None,
                })
                .collect(),
        }
    }

    /// One host per parsed runtime, for the 16 most recently used runtimes. The host is
    /// immutable, so sharing it cannot change a result.
    fn shared(runtime: &Arc<RuntimeTables>) -> Arc<Self> {
        type Entries = Vec<(Arc<RuntimeTables>, Arc<PackageHost>)>;
        static HOSTS: OnceLock<Mutex<Entries>> = OnceLock::new();
        let hosts = HOSTS.get_or_init(|| Mutex::new(Vec::new()));
        let mut entries = hosts.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some(index) = entries.iter().position(|(known, _)| Arc::ptr_eq(known, runtime)) {
            let entry = entries.remove(index);
            let host = entry.1.clone();
            entries.push(entry);
            return host;
        }
        let host = Arc::new(Self::new(runtime));
        entries.push((runtime.clone(), host.clone()));
        while entries.len() > 16 {
            entries.remove(0);
        }
        host
    }
}

impl ToolHost for PackageHost {
    fn tool(&self, name: &str) -> Option<&ExecTool> {
        self.tools.get(name)
    }
    fn ruleset_config(&self) -> &Value {
        &self.ruleset_config
    }
    fn definitions(&self) -> &Value {
        &self.definitions
    }
    fn procedures(&self) -> &[ExecProcedure] {
        &self.procedures
    }
    /// The package's `nested` executor: a declared tool's state-bound parameters are bound,
    /// keeping the arguments the Ruleset passed explicitly.
    fn nested_parameters(
        &self,
        tool: &str,
        params: Dict,
        bridge: Option<&dyn StateBridge>,
    ) -> Result<Dict, ExecError> {
        match (self.tools.get(tool), bridge) {
            (Some(tool), Some(bridge)) => runtime_parameters(tool, &params, bridge, true),
            _ => Ok(params),
        }
    }
}

/// `_bound_runtime_parameter(bridge, path)`: one Ruleset-owned parameter read from package state.
fn bound_runtime_parameter(bridge: &dyn StateBridge, path: &str) -> Result<Value, ExecError> {
    let (root, child_path) = path.split_once('.').unwrap_or((path, ""));
    if child_path.is_empty() {
        return Err(ExecError::package_runtime("Runtime state-bound parameter path is invalid"));
    }
    let value = match root {
        "combat" => nested_get(&Value::dict(bridge.combat().clone()), child_path),
        "state" => bridge.read_field("state", child_path)?,
        // Rejected by runtime validation before materialization.
        _ => return Err(ExecError::package_runtime("Runtime state-bound parameter root is unsupported")),
    };
    if value == Value::None {
        return Err(ExecError::package_runtime(format!(
            "Runtime state-bound parameter '{path}' is unavailable"
        )));
    }
    Ok(deep_copy(&value))
}

/// `_runtime_parameters(tool, parameters, bridge, preserve_explicit=...)`: bind public inputs to
/// authority, and fill declared defaults; internal Ruleset calls keep their explicit arguments.
pub(crate) fn runtime_parameters(
    tool: &ExecTool,
    parameters: &Dict,
    bridge: &dyn StateBridge,
    preserve_explicit: bool,
) -> Result<Dict, ExecError> {
    let Value::Dict(bound) = mutable_json(&Value::dict(parameters.clone())) else { unreachable!("a dict") };
    let mut bound = (*bound).clone();
    for parameter in &tool.parameters {
        let name = parameter.name.to_str();
        if parameter.from_state != Value::None && !(preserve_explicit && bound.contains_key(&name)) {
            let path = match &parameter.from_state {
                Value::Str(path) => path.to_string(),
                other => other.to_str(),
            };
            bound.insert(name, bound_runtime_parameter(bridge, &path)?);
        } else if !bound.contains_key(&name) && parameter.default != Value::None {
            bound.insert(name, deep_copy(&parameter.default));
        }
    }
    Ok(bound)
}

/// `_unknown_reference_reason(references)`: bounded model-safe text saying a chosen ID has no
/// saved game state.
fn unknown_reference_reason(references: &[String]) -> String {
    // `[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}`, fully matched.
    let safe = |reference: &str| {
        let mut chars = reference.chars();
        chars.next().is_some_and(|c| c.is_ascii_alphanumeric())
            && reference.chars().count() <= 64
            && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | ':' | '-'))
    };
    let named: Vec<&str> = references.iter().map(String::as_str).filter(|r| safe(r)).take(3).collect();
    let opening = if !named.is_empty() && named.len() == references.len() {
        let subject = named.iter().map(|r| format!("'{r}'")).collect::<Vec<_>>().join(", ");
        format!("This campaign has no saved game statistics for {subject}.")
    } else {
        "This campaign has no saved game statistics for a chosen target.".to_owned()
    };
    format!(
        "{opening} This action only works on creatures and objects in the campaign's game state. Choose one that is listed there, add the missing one first if a tool for that is offered, or narrate the outcome without this action."
    )
}

fn dict_of(map: &JsonMap) -> Dict {
    match Value::from_json(&Json::Object(map.clone())) {
        Value::Dict(map) => (*map).clone(),
        _ => unreachable!("an object"),
    }
}

fn object(value: Json) -> JsonMap {
    match value {
        Json::Object(map) => map,
        _ => unreachable!("a dict is an object"),
    }
}

struct Inner {
    package_id: String,
    package_version: String,
    package_sha256: String,
    tool_name: String,
    runtime: Arc<RuntimeTables>,
    host: OnceLock<Arc<PackageHost>>,
}

/// The mechanic `materialize_package_mechanic` returns: one exact package declaration compiled
/// into a data-only closure. Cloning shares it; two mechanics are equal when they are the same
/// materialization.
#[derive(Clone)]
pub struct PackageMechanic(Arc<Inner>);

impl std::fmt::Debug for PackageMechanic {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("PackageMechanic")
            .field("package_id", &self.0.package_id)
            .field("package_version", &self.0.package_version)
            .field("tool_name", &self.0.tool_name)
            .finish()
    }
}

impl PartialEq for PackageMechanic {
    fn eq(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }
}

/// `materialize_package_mechanic(package_id=..., package_version=..., package_sha256=...,
/// declaration=..., runtime_bytes=...)`.
pub fn materialize_package_mechanic(
    package_id: &str,
    package_version: &str,
    package_sha256: &str,
    declaration: &ToolDeclaration,
    runtime_bytes: &[u8],
) -> Result<PackageMechanic, PackageRuntimeFailure> {
    if package_id == "wotc.srd.5.2.1" {
        return Err(crate::package_runtime::rejection(
            "Full is archived; migrate this executable binding to SRD Lite before launch.",
        ));
    }
    let runtime = parse_runtime(package_id, runtime_bytes)?;
    validate_runtime_declaration(&runtime, declaration)?;
    Ok(PackageMechanic(Arc::new(Inner {
        package_id: package_id.to_owned(),
        package_version: package_version.to_owned(),
        package_sha256: package_sha256.to_owned(),
        tool_name: declaration.name.clone(),
        runtime,
        host: OnceLock::new(),
    })))
}

impl PackageMechanic {
    /// The mechanic closure: run the tool once against `context`.
    pub fn execute(&self, context: &MechanicContext) -> Result<MechanicOutcome, MechanicFailure> {
        let inner = &*self.0;
        let host = inner.host.get_or_init(|| PackageHost::shared(&inner.runtime)).clone();
        let name = inner.tool_name.as_str();
        let tool = host.tool(name).expect("a validated declaration names a runtime tool");
        let state = dict_of(&context.state);
        let participants = dict_of(&context.participants);
        let mut bridge = MemoryStateBridge::new(&inner.package_id, &state, Some(&participants))?;
        let parameters = runtime_parameters(tool, &dict_of(&context.parameters), &bridge, false)?;
        let control = context.choice_resolution.as_ref().map(|resolution| ChoiceControl {
            choice_id: resolution.choice_id.clone(),
            option_id: resolution.option_id.clone(),
            expected_request: resolution.expected_request.clone(),
            consumed: Cell::new(false),
            prior_answers: resolution
                .prior_answers
                .iter()
                .map(|answer| (answer.request.clone(), answer.option_id.clone()))
                .collect(),
            replayed_answers: Cell::new(0),
        });
        let parameters_json = json_safe(&Value::dict(parameters.clone()))?;
        let mut basis = JsonMap::new();
        for (key, item) in [
            ("campaign_id", &context.campaign_id),
            ("turn_id", &context.turn_id),
            ("action_id", &context.action_id),
            ("package_id", &inner.package_id),
            ("package_version", &inner.package_version),
            ("package_sha256", &inner.package_sha256),
            ("tool_name", &inner.tool_name),
        ] {
            basis.insert(key.into(), Json::String(item.clone()));
        }
        basis.insert("parameters".into(), parameters_json.clone());
        let digest = sylvia_base::hash::sha256(canonical_json_bytes(&Json::Object(basis)));
        let seed = u64::from_be_bytes(digest[..8].try_into().expect("eight bytes"));
        let mut dice = Dice::new();
        let executed = dice.seeded_rolls(Some(i128::from(seed)), |dice| {
            execute_tool(
                name,
                &parameters,
                &*host,
                Some(&mut bridge),
                Combat::Bridge,
                None,
                dice,
                control.as_ref(),
                Some(ProcedureExecutor { depth: 0, combat: Combat::Bridge }),
                NestedMode::Package,
            )
        });
        let result = match executed {
            Ok(result) => result,
            Err(ExecError::Suspended(request)) => {
                if control.as_ref().is_some_and(|control| !control.consumed.get()) {
                    return Err(MechanicFailure::package_runtime(
                        "Package did not consume the authorized choice",
                        Some("Package mechanic is awaiting an authorized choice".into()),
                    ));
                }
                let mut result = JsonMap::new();
                result.insert("status".into(), Json::from("awaiting_choice"));
                result.insert("tool".into(), Json::from(name));
                return Ok(MechanicOutcome { result, choice: Some(*request), ..MechanicOutcome::default() });
            }
            Err(error) => return Err(error.into()),
        };
        if !result.success {
            let kind = result.failure_kind.as_deref();
            if kind == Some("precondition") {
                if let Some(error) = &result.error {
                    return Err(rejected(error, &result.failure_reasons));
                }
            }
            if kind == Some("parameter_validation") && !result.unknown_references.is_empty() {
                // The model chose a target the campaign holds no game state for: a refusal it
                // can act on, not a Ruleset defect.
                return Err(rejected(&unknown_reference_reason(&result.unknown_references), &[]));
            }
            let cause = result.error.clone().filter(|e| !e.is_empty());
            return Err(MechanicFailure::package_runtime(
                "Package mechanic execution failed",
                Some(cause.unwrap_or_else(|| "Unknown Ruleset execution failure".into())),
            ));
        }
        if control.as_ref().is_some_and(|control| !control.consumed.get()) {
            return Err(MechanicFailure::package_runtime(
                "Package mechanic did not consume the authorized choice",
                None,
            ));
        }
        if result.private_random_sampled && result.public_context.is_none() {
            return Err(MechanicFailure::package_runtime(
                "Hidden random results require explicit publication",
                None,
            ));
        }
        let isolated = inner.runtime.ruleset.iter().any(|(key, value)| {
            matches!(key, Data::Str(key) if key == "model_decision_boundary")
                && matches!(value, Data::Dict(_))
                && value.get_str("isolated_participant").is_some_and(sylvia_engine::ruleset::pydata::truthy)
        });
        if isolated && result.public_context.is_none() {
            return Err(MechanicFailure::package_runtime(
                "Isolated participant actions require explicit publication",
                None,
            ));
        }
        // No spatial engine opened (its effects are not yet ported), so no published name is a
        // spatial private variable.
        let visible_context = match &result.public_context {
            None => Value::dict(result.context.clone()),
            Some(published @ Value::Dict(_)) => published.clone(),
            Some(other) => {
                return Err(MechanicFailure::Failed {
                    class: "AttributeError",
                    message: format!("'{}' object has no attribute 'items'", other.type_name()),
                    cause: None,
                })
            }
        };
        let mut payload = Dict::new();
        payload.insert("status".into(), Value::str("committed"));
        payload.insert("tool".into(), Value::str(result.tool_name.as_str()));
        payload.insert("context".into(), visible_context);
        payload.insert("narrative_hooks".into(), Value::dict(result.narrative_hooks.clone()));
        payload.insert("narrative_directives".into(), Value::list(result.narrative_directives.clone()));
        payload.insert("logs".into(), result.logs.clone());
        let mut safe_result = object(json_safe(&Value::dict(payload))?);
        let metadata = match crate::package_runtime::tool_metadata_of(&inner.runtime, name) {
            Ok(metadata) => metadata,
            Err(PackageRuntimeFailure::Uncaught { class, message }) => {
                return Err(MechanicFailure::Failed { class, message, cause: None })
            }
            Err(PackageRuntimeFailure::Rejected(error)) => {
                return Err(MechanicFailure::package_runtime(&error.message, None))
            }
        };
        let parameters_json = object(parameters_json);
        let public_context = match &safe_result["context"] {
            Json::Object(map) => map.clone(),
            _ => unreachable!("the context is a dict"),
        };
        let mut card = project_result_card(metadata.get("result_card"), &parameters_json, &public_context);
        let activity_label =
            project_activity_label(metadata.get("activity_label"), &parameters_json, &public_context);
        if let Some(label) = activity_label {
            safe_result.insert("activity_label".into(), Json::String(label));
            let full = |card: &JsonMap| {
                ["summary", "details", "actions", "equation", "equations", "subtitle"].iter().any(|key| {
                    match card.get(*key) {
                        Some(Json::Array(items)) => !items.is_empty(),
                        Some(Json::Object(map)) => !map.is_empty(),
                        Some(Json::String(text)) => !text.is_empty(),
                        _ => false,
                    }
                })
            };
            if card.as_ref().is_some_and(|card| !full(card)) {
                // A quiet lifecycle transition is already represented by its activity label.
                card = None;
            }
        }
        if metadata.contains_key("result_card") {
            safe_result.insert("result_card".into(), card.map_or(Json::Null, Json::Object));
        }
        let mut consequences = Vec::new();
        // `for log in result.logs`.
        let logs: Vec<Value> = match &result.logs {
            Value::List(items) | Value::Tuple(items) => (**items).clone(),
            Value::Dict(map) => map.keys().map(|key| Value::str(key.as_str())).collect(),
            Value::Str(text) => text.chars().map(|c| Value::str(c.to_string())).collect(),
            other => {
                return Err(MechanicFailure::Failed {
                    class: "TypeError",
                    message: format!("'{}' object is not iterable", other.type_name()),
                    cause: None,
                })
            }
        };
        for log in &logs {
            let Value::Dict(log) = log else {
                return Err(MechanicFailure::Failed {
                    class: "TypeError",
                    message: format!("'{}' object is not a mapping", log.type_name()),
                    cause: None,
                });
            };
            let mut consequence = Dict::new();
            consequence.insert("kind".into(), Value::str("runtime_log"));
            for (key, item) in log.iter() {
                consequence.insert(key.clone(), item.clone());
            }
            consequences.push(object(json_safe(&Value::dict(consequence))?));
        }
        Ok(MechanicOutcome {
            result: safe_result,
            mutations: bridge.mutations()?,
            participant_mutations: bridge.participant_mutations()?,
            consequences,
            choice: None,
        })
    }
}
