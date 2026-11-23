//! `application/package_runtime.py`'s execution: `materialize_package_mechanic`, the mechanic
//! closure it returns, and `_runtime_parameters` with `_bound_runtime_parameter`.
//!
//! The mechanic runs one admitted tool against an in-memory copy of the package's state roots
//! (`sylvia_engine::exec::MemoryStateBridge`) and returns a `MechanicOutcome`; it never touches
//! storage. `materialize_player_action_checker` and `materialize_player_action_previewer`
//! check and preview a player's direct action on copied state, the previewer through the
//! player-known scene boundary. Spatial effects (`spatial_query`, `spatial_mutation`,
//! `shot_effect`, `aoe`) and the player controls' spatial queries, preflight path and map
//! preview answer `PortPending` until lane 2's spatial effect handlers land. Without them
//! no spatial engine is ever opened, so the bridge's spatial private variables, evidence and
//! shot presentation are empty, and the result carries none of them, as in Python.

use crate::package_runtime::{parse_runtime, validate_runtime_declaration, PackageRuntimeFailure};
use crate::player_actions::PlayerControls;
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
use sylvia_engine::exec::tool::{prepare_tool_context, Prepared};
use sylvia_engine::exec::ExecutionContext;
use sylvia_engine::exec::{
    execute_tool, Combat, ExecError, ExecTool, MemoryStateBridge, StateBridge, ToolHost,
};
use sylvia_engine::expr::value::{Dict, Value};
use sylvia_engine::expr::ExecutionBudget;
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
        let published = match &result.public_context {
            None => result.context.clone(),
            Some(Value::Dict(published)) => (**published).clone(),
            Some(other) => {
                return Err(MechanicFailure::Failed {
                    class: "AttributeError",
                    message: format!("'{}' object has no attribute 'items'", other.type_name()),
                    cause: None,
                })
            }
        };
        // Spatial results, and what was copied from them, stay private.
        let private = bridge.spatial().private_variables();
        let visible_context: Dict = published.into_iter().filter(|(key, _)| !private.contains(key)).collect();
        let visible_context = Value::dict(visible_context);
        let evidence: Vec<JsonMap> = bridge.spatial().evidence().to_vec();
        let mut payload = Dict::new();
        payload.insert("status".into(), Value::str("committed"));
        payload.insert("tool".into(), Value::str(result.tool_name.as_str()));
        payload.insert("context".into(), visible_context);
        payload.insert("narrative_hooks".into(), Value::dict(result.narrative_hooks.clone()));
        payload.insert("narrative_directives".into(), Value::list(result.narrative_directives.clone()));
        payload.insert("logs".into(), result.logs.clone());
        let mut safe_result = object(json_safe(&Value::dict(payload))?);
        if !evidence.is_empty() {
            safe_result.insert(
                "spatial_evidence".into(),
                Json::Array(evidence.iter().cloned().map(Json::Object).collect()),
            );
        }
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
        for item in evidence {
            let mut consequence = JsonMap::new();
            consequence.insert("kind".into(), Json::from("spatial_query"));
            consequence.insert("evidence".into(), Json::Object(item));
            consequences.push(consequence);
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

// ── Player-action checks and previews ──────────────────────────────────────────────────────

/// Code the port has not reached yet, named (`not yet ported: ...`): a player check or preview
/// that reaches a spatial query, effect or projection. The owner answers `rust_port_pending`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PortPending(pub String);

/// How a check or preview stopped: a Ruleset refusal Python returns as its reason, code not
/// yet ported, or any other exception, which Python's catch-all turns into the generic reason
/// (a check) or no preview.
enum ControlError {
    Rejected(String),
    PortPending(String),
    Other,
}

impl From<ExecError> for ControlError {
    fn from(error: ExecError) -> Self {
        match error {
            ExecError::PortPending(message) => Self::PortPending(message),
            _ => Self::Other,
        }
    }
}

type Control<T> = Result<T, ControlError>;

/// `AuthorityMemoryStateBridge(..., player_spatial_view=True)`: entity, participant and combat
/// reads pass through the player-known scene boundary (`player_known_spatial_value`). Python
/// projects the combat state afresh on every read; it is read here once, and every top-level
/// call gets its own copy, so no write reaches it, as none reaches Python's fresh projection.
struct PlayerViewBridge {
    inner: MemoryStateBridge,
    combat: Dict,
}

/// `player_known_spatial_value(value)` over an execution value.
fn player_known(value: &Value) -> Result<Value, ExecError> {
    Ok(match value {
        Value::Dict(map) => {
            let format_is_one = match map.get("format") {
                Some(Value::Int(1) | Value::Bool(true)) => true,
                Some(Value::Float(number)) => *number == 1.0,
                _ => false,
            };
            if format_is_one
                && ["map", "entities", "geometry", "zones", "terrain"]
                    .iter()
                    .all(|key| map.contains_key(*key))
            {
                let scene = crate::tactical_grid::player_known_spatial_scene(&json_safe(value)?, true)
                    .map_err(|error| {
                        let class = match &error {
                            sylvia_engine::spatial::SpatialError::Validation(_) => "SpatialValidationError",
                            sylvia_engine::spatial::SpatialError::Uncaught { error_type, .. } => error_type,
                        };
                        ExecError::Python { class, message: error.to_string() }
                    })?;
                Value::from_json(&Json::Object(scene))
            } else {
                let mut projected = Dict::new();
                for (key, child) in map.iter() {
                    projected.insert(key.clone(), player_known(child)?);
                }
                Value::dict(projected)
            }
        }
        Value::List(items) | Value::Tuple(items) => {
            Value::list(items.iter().map(player_known).collect::<Result<_, _>>()?)
        }
        other => other.clone(),
    })
}

impl PlayerViewBridge {
    fn new(inner: MemoryStateBridge) -> Result<Self, ExecError> {
        let Value::Dict(combat) = player_known(&Value::dict(inner.combat().clone()))? else {
            unreachable!("a dict projects to a dict")
        };
        Ok(Self { inner, combat: (*combat).clone() })
    }
}

impl StateBridge for PlayerViewBridge {
    fn entity_exists(&self, entity_id: &str) -> bool {
        self.inner.entity_exists(entity_id)
    }
    fn get_entity(&self, entity_id: &str) -> Result<Value, ExecError> {
        player_known(&self.inner.get_entity(entity_id)?)
    }
    fn participant_exists(&self, participant_id: &str) -> bool {
        self.inner.participant_exists(participant_id)
    }
    fn get_participant(&self, participant_id: &str) -> Result<Value, ExecError> {
        player_known(&self.inner.get_participant(participant_id)?)
    }
    fn get_position(&self, entity_id: &str) -> Option<Value> {
        self.inner.get_position(entity_id)
    }
    /// `deepcopy(_nested_get(self.get_entity(entity_id), field_path))`.
    fn read_field(&self, entity_id: &str, field_path: &str) -> Result<Value, ExecError> {
        Ok(deep_copy(&nested_get(&self.get_entity(entity_id)?, field_path)))
    }
    fn write_field(&mut self, entity_id: &str, field_path: &str, value: Value) -> Result<(), ExecError> {
        self.inner.write_field(entity_id, field_path, value)
    }
    /// `modify_field` reads through the player view, as Python's `self.read_field` does.
    fn modify_field(&mut self, entity_id: &str, field_path: &str, delta: i64) -> Result<i64, ExecError> {
        let current = match self.read_field(entity_id, field_path)? {
            Value::None => Value::Int(0),
            other => other,
        };
        let replacement = match current {
            Value::Int(current) => match current.checked_add(delta) {
                Some(sum) => Value::Int(sum),
                None => return Err(ExecError::value_error(sylvia_base::pystr::INT_RANGE_MESSAGE)),
            },
            Value::Float(current) => Value::Float(current + delta as f64),
            _ => return Err(ExecError::package_runtime("Runtime numeric mutation targeted a non-number")),
        };
        self.write_field(entity_id, field_path, replacement.clone())?;
        match replacement {
            Value::Int(value) => Ok(value),
            Value::Float(value) => sylvia_engine::exec::error::float_to_int(value),
            _ => unreachable!("a number"),
        }
    }
    fn create_entity(&mut self, entity_id: &Value, value: &Value) -> Result<(), ExecError> {
        self.inner.create_entity(entity_id, value)
    }
    fn write_participant_field(
        &mut self,
        participant_id: &str,
        field_path: &str,
        value: Value,
    ) -> Result<(), ExecError> {
        self.inner.write_participant_field(participant_id, field_path, value)
    }
    fn combat_mut(&mut self) -> &mut Dict {
        &mut self.combat
    }
    fn combat(&self) -> &Dict {
        &self.combat
    }

    fn supports_spatial(&self) -> bool {
        true
    }

    fn spatial_snapshot(&mut self, scene_path: &str) -> Result<Value, ExecError> {
        let scene = self.player_scene(scene_path)?;
        Ok(Value::from_json(&self.inner.spatial_mut().snapshot(scene_path, &scene)?))
    }

    fn spatial_query(
        &mut self,
        scene_path: &str,
        operation: &str,
        query: &Value,
        snapshot: &Value,
    ) -> Result<Value, ExecError> {
        let Value::Dict(_) = query else {
            return Err(ExecError::package_runtime("Spatial query input must be an object"));
        };
        let query = json_safe(query)?;
        let scene = self.player_scene(scene_path)?;
        let Json::Object(query) = query else { unreachable!("a dict") };
        let snapshot = match snapshot {
            Value::None => Json::Null,
            Value::Dict(_) => json_safe(snapshot)?,
            _ => {
                self.inner.spatial_mut().engine(scene_path, &scene)?;
                return Err(ExecError::package_runtime("Spatial snapshot reference is invalid"));
            }
        };
        Ok(Value::from_json(
            &self.inner.spatial_mut().query(scene_path, &scene, operation, &query, &snapshot)?,
        ))
    }

    fn spatial_mutation(&mut self, scene_path: &str, request: &Value) -> Result<Value, ExecError> {
        let Value::Dict(_) = request else {
            return Err(ExecError::package_runtime("Spatial mutation input must be an object"));
        };
        let request = json_safe(request)?;
        let scene = self.player_scene(scene_path)?;
        let (advanced, result) = self.inner.spatial_mut().mutation(scene_path, &scene, &request)?;
        let field = scene_path.strip_prefix("state.").unwrap_or(scene_path);
        self.write_field("state", field, Value::from_json(&Json::Object(advanced)))?;
        Ok(Value::from_json(&result))
    }

    fn register_spatial_private_variable(&mut self, name: &str) {
        self.inner.spatial_mut().register_private(name);
    }

    fn register_state_parameter(&mut self, name: &str, source: &str) {
        self.inner.spatial_mut().register_state_parameter(name, source);
    }

    fn register_spatial_aggregate(&mut self, name: &str, source: &Value) {
        self.inner.spatial_mut().register_aggregate(name, source);
    }
}

impl PlayerViewBridge {
    /// `bridge._spatial_engine(scene_path)` alone: open (or advance) the scene's engine.
    fn open_scene(&mut self, scene_path: &str) -> Result<(), ExecError> {
        let scene = self.player_scene(scene_path)?;
        self.inner.spatial_mut().engine(scene_path, &scene)?;
        Ok(())
    }

    /// `_spatial_engine(scene_path)`'s read in the player view: the scene read through the
    /// player-known boundary, then filtered to its player-known membership again, keeping the
    /// mechanical metadata of the rows kept.
    fn player_scene(&self, scene_path: &str) -> Result<Json, ExecError> {
        let field = sylvia_engine::exec::spatial_bridge::scene_field(scene_path)?;
        let value = sylvia_engine::exec::spatial_bridge::scene_value(&self.read_field("state", field)?)?;
        crate::tactical_grid::player_known_spatial_scene(&value, true)
            .map(Json::Object)
            .map_err(sylvia_engine::exec::spatial_bridge::spatial_error)
    }
}

/// `evaluate_condition(expression, context)`.
fn condition_holds(expression: &str, ctx: &ExecutionContext<'_>) -> Control<bool> {
    ctx.with_eval(|eval| sylvia_engine::expr::evaluate_condition(expression, eval))
        .map_err(|_| ControlError::Other)
}

fn text<'j>(map: &'j JsonMap, key: &str) -> Control<&'j str> {
    map.get(key).and_then(Json::as_str).ok_or(ControlError::Other)
}

/// `_prepare_player_control_values(policy, context, bridge)`: the declared physical queries and
/// local scalar calculations, without effects or dice. Each query's resolved input is returned,
/// for a map preview.
fn prepare_player_control_values(
    policy: &PlayerControls,
    ctx: &mut ExecutionContext<'_>,
) -> Control<JsonMap> {
    let mut resolved_queries = JsonMap::new();
    for declaration in &policy.queries {
        let Some(Json::Object(body)) = declaration.get("spatial_query") else {
            return Err(ControlError::Other);
        };
        let name = text(declaration, "store_as")?;
        if ctx.vars.contains_key(name) {
            // "Player query would replace a bound variable".
            return Err(ControlError::Other);
        }
        let query = sylvia_engine::exec::effects::resolve_spatial_input(
            &Value::from_json(body.get("query").unwrap_or(&Json::Null)),
            ctx,
        );
        let Value::Dict(query_map) = &query else {
            // "Player query is unavailable".
            return Err(ControlError::Other);
        };
        let operation = text(body, "operation")?;
        if operation == "path"
            && !matches!(query_map.get("max_steps"), Some(Value::Int(steps)) if (1..=4096).contains(steps))
        {
            // "Player path query exceeds the selection limit".
            return Err(ControlError::Other);
        }
        let bridge = ctx.state_bridge.as_deref_mut().ok_or(ControlError::Other)?;
        let result = bridge.spatial_query(text(body, "scene")?, operation, &query, &Value::None)?;
        ctx.vars.insert(name.to_owned(), result);
        ctx.state_bridge.as_deref_mut().ok_or(ControlError::Other)?.register_spatial_private_variable(name);
        resolved_queries.insert(name.to_owned(), json_safe(&query).map_err(|_| ControlError::Other)?);
    }
    for calculation in &policy.calculations {
        let Some(Json::Object(body)) = calculation.get("set") else { return Err(ControlError::Other) };
        let (name, expression) = (text(body, "field")?, text(body, "value")?);
        if ctx.vars.contains_key(name) {
            // "Player calculation would replace a bound variable".
            return Err(ControlError::Other);
        }
        let condition = match calculation.get("condition") {
            None => "true",
            Some(Json::String(condition)) => condition.as_str(),
            Some(_) => return Err(ControlError::Other),
        };
        let mut value = Value::None;
        if condition_holds(condition, ctx)? {
            value = ctx
                .with_eval(|eval| sylvia_engine::expr::evaluate_expression(expression, eval))
                .map_err(|_| ControlError::Other)?;
            let scalar = match &value {
                Value::None | Value::Bool(_) | Value::Int(_) | Value::Str(_) => true,
                Value::Float(number) => number.is_finite(),
                _ => false,
            };
            if !scalar {
                // "Player calculation did not return a scalar".
                return Err(ControlError::Other);
            }
        }
        ctx.vars.insert(name.to_owned(), value);
    }
    Ok(resolved_queries)
}

/// `_player_preflight(runtime, policy, parameters, bridge)`: the policy's deterministic owners
/// on the disposable bridge, without rolls, relocation, commit or choices; their scalar results.
/// Only the player view projects a preflight path, which is spatial and not yet ported.
fn player_preflight(
    host: &PackageHost,
    policy: &PlayerControls,
    parameters: &Dict,
    bridge: &mut (dyn StateBridge + 'static),
    player_view: bool,
) -> Control<Dict> {
    let budget = ExecutionBudget { preflight_only: true, ..ExecutionBudget::default() };
    let mut dice = Dice::new();
    let mut scalars = Dict::new();
    for name in &policy.preflight {
        // `runtime.tools[name]`.
        let target = host.tool(name).ok_or(ControlError::Other)?;
        let names: Vec<String> = target.parameters.iter().map(|parameter| parameter.name.to_str()).collect();
        let values: Dict = parameters
            .iter()
            .filter(|(key, _)| names.contains(key))
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect();
        // `combat_state=bridge.combat_state`: the live dict, or the player view's fresh projection.
        let combat = if player_view { Combat::own(bridge.combat().clone()) } else { Combat::Bridge };
        let result = execute_tool(
            name,
            &values,
            host,
            Some(&mut *bridge),
            combat,
            Some(&budget),
            &mut dice,
            None,
            None,
            NestedMode::Preflight,
        )?;
        if !result.success {
            // Authored refusals are preserved; unexpected runtime failures stay private.
            if let (Some("precondition"), Some(error)) = (result.failure_kind.as_deref(), &result.error) {
                return match DomainError::ruleset_precondition_rejected(error, &result.failure_reasons) {
                    Ok(error) => Err(ControlError::Rejected(error.message().to_owned())),
                    Err(_) => Err(ControlError::Other),
                };
            }
            return Err(ControlError::Other);
        }
        for (key, value) in &result.context {
            if matches!(value, Value::None | Value::Str(_) | Value::Bool(_) | Value::Int(_) | Value::Float(_))
            {
                scalars.insert(key.clone(), value.clone());
            }
        }
    }
    let _ = player_view;
    Ok(scalars)
}

struct ControlInner {
    package_id: String,
    tool_name: String,
    runtime: Arc<RuntimeTables>,
    policy: PlayerControls,
    host: OnceLock<Arc<PackageHost>>,
}

impl ControlInner {
    fn new(
        package_id: &str,
        declaration: &ToolDeclaration,
        runtime_bytes: &[u8],
    ) -> Result<Self, PackageRuntimeFailure> {
        let runtime = parse_runtime(package_id, runtime_bytes)?;
        let metadata = crate::package_runtime::tool_metadata_of(&runtime, &declaration.name)?;
        let policy = crate::player_actions::parse_player_controls(metadata.get("player_controls"));
        Ok(Self {
            package_id: package_id.to_owned(),
            tool_name: declaration.name.clone(),
            runtime,
            policy,
            host: OnceLock::new(),
        })
    }

    fn host(&self) -> Arc<PackageHost> {
        self.host.get_or_init(|| PackageHost::shared(&self.runtime)).clone()
    }

    fn fmt(&self, name: &str, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct(name)
            .field("package_id", &self.package_id)
            .field("tool_name", &self.tool_name)
            .finish()
    }
}

/// `materialize_player_action_checker(...)`'s closure: player prerequisites on copied state,
/// without effects or raw context output. Cloning shares it; equality is identity.
#[derive(Clone)]
pub struct PlayerActionChecker(Arc<ControlInner>);

/// `materialize_player_action_previewer(...)`'s closure: the declared calculations with
/// player-known geometry, without revealing hidden detours. Cloning shares it; equality is
/// identity.
#[derive(Clone)]
pub struct PlayerActionPreviewer(Arc<ControlInner>);

impl std::fmt::Debug for PlayerActionChecker {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt("PlayerActionChecker", formatter)
    }
}

impl std::fmt::Debug for PlayerActionPreviewer {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt("PlayerActionPreviewer", formatter)
    }
}

impl PartialEq for PlayerActionChecker {
    fn eq(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }
}

impl PartialEq for PlayerActionPreviewer {
    fn eq(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }
}

/// `materialize_player_action_checker(package_id=..., declaration=..., runtime_bytes=...)`.
/// Python reads the tool without checking the declaration, so an undeclared tool is its
/// `KeyError`.
pub fn materialize_player_action_checker(
    package_id: &str,
    declaration: &ToolDeclaration,
    runtime_bytes: &[u8],
) -> Result<PlayerActionChecker, PackageRuntimeFailure> {
    Ok(PlayerActionChecker(Arc::new(ControlInner::new(package_id, declaration, runtime_bytes)?)))
}

/// `materialize_player_action_previewer(package_id=..., declaration=..., runtime_bytes=...)`.
pub fn materialize_player_action_previewer(
    package_id: &str,
    declaration: &ToolDeclaration,
    runtime_bytes: &[u8],
) -> Result<PlayerActionPreviewer, PackageRuntimeFailure> {
    Ok(PlayerActionPreviewer(Arc::new(ControlInner::new(package_id, declaration, runtime_bytes)?)))
}

impl PlayerActionChecker {
    /// `check(parameters, state, participants)`: `None` when the action is available, or the
    /// reason it is not.
    pub fn check(
        &self,
        parameters: &JsonMap,
        state: &JsonMap,
        participants: &JsonMap,
    ) -> Result<Option<String>, PortPending> {
        if !self.0.policy.available {
            return Ok(Some("This Ruleset's player controls are unavailable".into()));
        }
        match self.run(parameters, state, participants) {
            Ok(reason) => Ok(reason),
            Err(ControlError::Rejected(reason)) => Ok(Some(reason)),
            Err(ControlError::PortPending(message)) => Err(PortPending(message)),
            // An invalid expression or missing state cannot enable a control or expose its inputs.
            Err(ControlError::Other) => Ok(Some("This action's availability could not be determined".into())),
        }
    }

    fn run(&self, parameters: &JsonMap, state: &JsonMap, participants: &JsonMap) -> Control<Option<String>> {
        let inner = &*self.0;
        let host = inner.host();
        let tool = host.tool(&inner.tool_name).ok_or(ControlError::Other)?;
        let mut bridge =
            MemoryStateBridge::new(&inner.package_id, &dict_of(state), Some(&dict_of(participants)))?;
        let bound = runtime_parameters(tool, &dict_of(parameters), &bridge, false)?;
        let budget = ExecutionBudget::default();
        let mut dice = Dice::new();
        {
            let prepared = prepare_tool_context(
                &inner.tool_name,
                &bound,
                &*host,
                Some(&mut bridge),
                Combat::Bridge,
                &budget,
                &mut dice,
                None,
                None,
                NestedMode::Preflight,
            )?;
            let mut ctx = match prepared {
                Prepared::Result(_) => {
                    return Ok(Some("This action's prerequisites are not currently met".into()))
                }
                Prepared::Context(ctx) => ctx,
            };
            prepare_player_control_values(&inner.policy, &mut ctx)?;
            for item in &inner.policy.checks {
                if !condition_holds(text(item, "condition")?, &ctx)? {
                    return Ok(Some(text(item, "reason")?.to_owned()));
                }
            }
        }
        let bound = runtime_parameters(tool, &dict_of(parameters), &bridge, false)?;
        player_preflight(&host, &inner.policy, &bound, &mut bridge, false)?;
        Ok(None)
    }
}

/// A preview's public value as card JSON. Preflight scalars may be non-finite floats, which
/// JSON cannot hold and `_scalar` refuses; an empty object stands for them, which every card
/// path refuses in the same way.
fn card_value(value: &Value) -> Json {
    json_safe(value).unwrap_or_else(|_| Json::Object(JsonMap::new()))
}

impl PlayerActionPreviewer {
    /// `preview(parameters, state, participants)`: `{"preview": card, "map_preview": ...}`, or
    /// `None` when there is nothing to preview or the selection cannot be previewed.
    pub fn preview(
        &self,
        parameters: &JsonMap,
        state: &JsonMap,
        participants: &JsonMap,
    ) -> Result<Option<JsonMap>, PortPending> {
        let policy = &self.0.policy;
        if !policy.available
            || (policy.map_preview.is_none() && policy.preview.is_none() && policy.preflight_path.is_none())
        {
            return Ok(None);
        }
        match self.run(parameters, state, participants) {
            Ok(preview) => Ok(preview),
            Err(ControlError::PortPending(message)) => Err(PortPending(message)),
            // Selection changes and unsupported declarations cannot expose a private exception.
            Err(_) => Ok(None),
        }
    }

    fn run(&self, parameters: &JsonMap, state: &JsonMap, participants: &JsonMap) -> Control<Option<JsonMap>> {
        let inner = &*self.0;
        let policy = &inner.policy;
        let host = inner.host();
        let tool = host.tool(&inner.tool_name).ok_or(ControlError::Other)?;
        let memory =
            MemoryStateBridge::new(&inner.package_id, &dict_of(state), Some(&dict_of(participants)))?;
        let mut bridge = PlayerViewBridge::new(memory)?;
        let bound = runtime_parameters(tool, &dict_of(parameters), &bridge, false)?;
        let budget = ExecutionBudget::default();
        let mut dice = Dice::new();
        let calculated: Vec<(String, Value)> = {
            let combat = Combat::own(bridge.combat().clone());
            let prepared = prepare_tool_context(
                &inner.tool_name,
                &bound,
                &*host,
                Some(&mut bridge),
                combat,
                &budget,
                &mut dice,
                None,
                None,
                NestedMode::Preflight,
            )?;
            let mut ctx = match prepared {
                Prepared::Result(_) => return Ok(None),
                Prepared::Context(ctx) => ctx,
            };
            prepare_player_control_values(policy, &mut ctx)?;
            let mut calculated = Vec::new();
            for calculation in &policy.calculations {
                let Some(Json::Object(body)) = calculation.get("set") else {
                    return Err(ControlError::Other);
                };
                let name = text(body, "field")?;
                calculated.push((name.to_owned(), ctx.vars.get(name).cloned().unwrap_or(Value::None)));
            }
            calculated
        };
        let bound = runtime_parameters(tool, &dict_of(parameters), &bridge, false)?;
        let preflight = player_preflight(&host, policy, &bound, &mut bridge, true)?;
        // Python opens the scenes' engines before projecting a path or map preview, which
        // lane 2's C3b ports with `spatial_preview.py`.
        if let Some(path) = &policy.preflight_path {
            bridge.open_scene(text(path, "scene")?)?;
            return Err(ControlError::PortPending(
                "not yet ported: the player controls' preflight path preview (lane 2's spatial effect handlers)".into(),
            ));
        }
        if policy.map_preview.is_some() {
            for item in &policy.queries {
                let Some(Json::Object(body)) = item.get("spatial_query") else {
                    return Err(ControlError::Other);
                };
                bridge.open_scene(text(body, "scene")?)?;
            }
            return Err(ControlError::PortPending(
                "not yet ported: the player controls' map preview (lane 2's spatial effect handlers)".into(),
            ));
        }
        let mut public_context = JsonMap::new();
        for (key, value) in preflight.iter() {
            public_context.insert(key.clone(), card_value(value));
        }
        for (key, value) in &calculated {
            public_context.insert(key.clone(), card_value(value));
        }
        let declaration = policy.preview.clone().map(Json::Object);
        let card = project_result_card(declaration.as_ref(), parameters, &public_context);
        let mut preview = JsonMap::new();
        preview.insert("preview".into(), card.map_or(Json::Null, Json::Object));
        preview.insert("map_preview".into(), Json::Null);
        Ok(Some(preview))
    }
}
