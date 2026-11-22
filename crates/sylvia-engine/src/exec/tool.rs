//! `runtime/_tool_executor.py`: one tool invocation, from parameters to `ToolResult`.

use super::bridge::StateBridge;
use super::context::{ChoiceControl, Combat, ExecutionContext, Extras, NestedMode, ProcedureExecutor};
use super::effects::execute_effects;
use super::error::ExecError;
use super::template::resolve_template;
use crate::dice::Dice;
use crate::expr::value::{py_eq, Dict, Object, Value};
use crate::expr::ExecutionBudget;
use crate::ruleset::runtime_tables::{ToolDef, ToolParameter};
use sylvia_base::pyyaml::Data;

/// `ToolResult`.
#[derive(Debug, Clone, PartialEq)]
pub struct ToolResult {
    pub tool_name: String,
    pub success: bool,
    pub context: Dict,
    pub narrative_hooks: Dict,
    pub narrative_directives: Vec<Value>,
    pub error: Option<String>,
    pub failure_kind: Option<String>,
    pub failure_reasons: Vec<String>,
    pub unknown_references: Vec<String>,
    pub logs: Value,
    pub public_context: Option<Value>,
    pub private_random_sampled: bool,
}

impl Default for ToolResult {
    fn default() -> Self {
        Self {
            tool_name: String::new(),
            success: true,
            context: Dict::new(),
            narrative_hooks: Dict::new(),
            narrative_directives: Vec::new(),
            error: None,
            failure_kind: None,
            failure_reasons: Vec::new(),
            unknown_references: Vec::new(),
            logs: Value::list(Vec::new()),
            public_context: None,
            private_random_sampled: false,
        }
    }
}

impl ToolResult {
    fn failure(tool_name: &str, error: String, kind: &str) -> Self {
        Self {
            tool_name: tool_name.into(),
            success: false,
            error: Some(error),
            failure_kind: Some(kind.into()),
            logs: Value::list(Vec::new()),
            ..Self::default()
        }
    }

    /// The `ToolResult` dataclass as a value, for `store_as` (`ctx.vars[store_as] = result`).
    pub fn to_value(&self) -> Value {
        let text = |item: &Option<String>| item.as_deref().map_or(Value::None, Value::str);
        let strings = |items: &[String]| Value::tuple(items.iter().map(|s| Value::str(s.as_str())).collect());
        Value::Object(std::sync::Arc::new(Object {
            class: "ToolResult",
            fields: vec![
                ("tool_name", Value::str(self.tool_name.as_str())),
                ("success", Value::Bool(self.success)),
                ("context", Value::dict(self.context.clone())),
                ("state_changes", Value::list(Vec::new())),
                ("narrative_hooks", Value::dict(self.narrative_hooks.clone())),
                ("narrative_directives", Value::list(self.narrative_directives.clone())),
                ("error", text(&self.error)),
                ("failure_kind", text(&self.failure_kind)),
                ("failure_reasons", strings(&self.failure_reasons)),
                ("unknown_references", strings(&self.unknown_references)),
                ("logs", self.logs.clone()),
                ("available_tools", Value::list(Vec::new())),
                ("public_context", self.public_context.clone().unwrap_or(Value::None)),
                ("private_random_sampled", Value::Bool(self.private_random_sampled)),
            ],
            methods: &[],
        }))
    }
}

/// A loaded value as an execution value. Runtime YAML is JSON once admitted; dates, bytes and
/// sets (which only an unadmitted legacy file could hold) become their `str()`.
pub fn data_value(data: &Data) -> Value {
    match data {
        Data::None => Value::None,
        Data::Bool(flag) => Value::Bool(*flag),
        Data::Int(number) => Value::Int(*number),
        Data::Float(number) => Value::Float(*number),
        Data::Str(text) => Value::str(text.as_str()),
        Data::List(items) => Value::list(items.iter().map(data_value).collect()),
        Data::Tuple(items) => Value::tuple(items.iter().map(data_value).collect()),
        Data::Dict(pairs) => Value::dict(
            pairs.iter().map(|(key, item)| (crate::ruleset::pydata::to_str(key), data_value(item))).collect(),
        ),
        other => Value::str(crate::ruleset::pydata::to_str(other)),
    }
}

/// One parameter, its fields as execution values.
#[derive(Debug, Clone)]
pub struct ExecParameter {
    pub name: Value,
    pub kind: Value,
    pub resolve: Value,
    pub required: Value,
    pub default: Value,
    pub enum_values: Value,
    pub minimum: Value,
    pub maximum: Value,
    pub min_items: Value,
    pub max_items: Value,
    pub unique_items: Value,
    pub from_state: Value,
}

impl ExecParameter {
    fn from_def(parameter: &ToolParameter) -> Self {
        Self {
            name: data_value(&parameter.name),
            kind: data_value(&parameter.kind),
            resolve: data_value(&parameter.resolve),
            required: data_value(&parameter.required),
            default: data_value(&parameter.default),
            enum_values: data_value(&parameter.enum_values),
            minimum: data_value(&parameter.minimum),
            maximum: data_value(&parameter.maximum),
            min_items: data_value(&parameter.min_items),
            max_items: data_value(&parameter.max_items),
            unique_items: data_value(&parameter.unique_items),
            from_state: data_value(&parameter.from_state),
        }
    }

    fn name_text(&self) -> String {
        self.name.to_str()
    }
}

/// A tool as execution sees it (`ToolDef` with execution values).
#[derive(Debug, Clone)]
pub struct ExecTool {
    pub name: String,
    pub parameters: Vec<ExecParameter>,
    pub pre_conditions: Value,
    pub effects: Value,
    pub range: Value,
}

impl ExecTool {
    pub fn from_def(name: &str, tool: &ToolDef) -> Self {
        Self {
            name: name.to_owned(),
            parameters: tool.parameters.iter().map(ExecParameter::from_def).collect(),
            pre_conditions: data_value(&tool.pre_conditions),
            effects: data_value(&tool.effects),
            range: data_value(&tool.range),
        }
    }
}

/// What a tool runs inside: the loaded tools, the Ruleset's configuration and definitions,
/// and how a nested call's parameters are prepared.
pub trait ToolHost {
    fn tool(&self, name: &str) -> Option<&ExecTool>;
    fn ruleset_config(&self) -> &Value;
    fn definitions(&self) -> &Value;
    /// The Ruleset's procedures, in order.
    fn procedures(&self) -> &[super::procedures::ExecProcedure] {
        &[]
    }
    /// The parameters a nested `call` passes on (the package host binds state parameters).
    fn nested_parameters(
        &self,
        _tool: &str,
        params: Dict,
        _bridge: Option<&dyn StateBridge>,
    ) -> Result<Dict, ExecError> {
        Ok(params)
    }
}

fn is_text(value: &Value, text: &str) -> bool {
    matches!(value, Value::Str(value) if &**value == text)
}

/// `value < bound` (`<` or `>`) for a numeric parameter value, with Python's `TypeError` for
/// a bound that is not a number.
fn below(value: &Value, bound: &Value, less: bool) -> Result<bool, ExecError> {
    let (Some(a), Some(b)) = (crate::expr::value::numeric(value), crate::expr::value::numeric(bound)) else {
        let op = if less { "<" } else { ">" };
        return Err(ExecError::type_error(format!(
            "'{op}' not supported between instances of '{}' and '{}'",
            value.type_name(),
            bound.type_name()
        )));
    };
    let order = crate::expr::value::numeric_cmp(a, b);
    Ok(match order {
        Some(order) => {
            if less {
                order.is_lt()
            } else {
                order.is_gt()
            }
        }
        None => false,
    })
}

fn count_bound(bound: &Value) -> Option<i64> {
    match bound {
        Value::Int(n) => Some(*n),
        Value::Bool(flag) => Some(i64::from(*flag)),
        _ => None,
    }
}

/// `_validate_params(tool_def, params, state_bridge, unknown_references)`.
fn validate_params(
    tool: &ExecTool,
    params: &Dict,
    bridge: Option<&dyn StateBridge>,
    unknown: &mut Vec<String>,
) -> Result<Vec<String>, ExecError> {
    let mut errors = Vec::new();
    for parameter in &tool.parameters {
        let name = parameter.name_text();
        let value = params.get(&name).cloned().unwrap_or(Value::None);
        if parameter.required.truthy() && (value == Value::None || is_text(&value, "")) {
            errors.push(format!("Missing required parameter '{name}'"));
            continue;
        }
        if value == Value::None {
            continue;
        }
        let (kind, resolve) = (&parameter.kind, &parameter.resolve);
        let items_bounds = |items: &[Value], errors: &mut Vec<String>| {
            if let Some(min) = count_bound(&parameter.min_items) {
                if (items.len() as i64) < min {
                    errors.push(format!(
                        "Parameter '{name}' must contain at least {} items",
                        parameter.min_items.to_str()
                    ));
                }
            }
            if let Some(max) = count_bound(&parameter.max_items) {
                if (items.len() as i64) > max {
                    errors.push(format!(
                        "Parameter '{name}' must contain at most {} items",
                        parameter.max_items.to_str()
                    ));
                }
            }
        };
        if is_text(resolve, "entity") {
            match &value {
                Value::Str(id) => {
                    if bridge.is_some_and(|b| !b.entity_exists(id)) {
                        errors.push(format!("Entity '{id}' not found for parameter '{name}'"));
                        unknown.push(id.to_string());
                    }
                }
                _ => errors.push(format!("Parameter '{name}' must be an entity ID string")),
            }
        } else if is_text(resolve, "participant") {
            match &value {
                Value::Str(id) => {
                    if bridge.is_some_and(|b| !b.participant_exists(id)) {
                        errors.push(format!("Participant '{id}' not found for parameter '{name}'"));
                        unknown.push(id.to_string());
                    }
                }
                _ => errors.push(format!("Parameter '{name}' must be a participant ID string")),
            }
        } else if is_text(kind, "string") {
            if !matches!(value, Value::Str(_)) {
                errors.push(format!("Parameter '{name}' must be a string"));
            }
        } else if is_text(kind, "integer") {
            match &value {
                Value::Int(_) => {}
                Value::Float(x) => {
                    if x.fract() != 0.0 || !x.is_finite() {
                        // `value != int(value)`; JSON parameters are finite.
                        errors.push(format!("Parameter '{name}' must be an integer, got float"));
                    }
                }
                _ => errors.push(format!("Parameter '{name}' must be an integer")),
            }
        } else if is_text(kind, "number") {
            if !matches!(value, Value::Int(_) | Value::Float(_) | Value::Bool(_)) {
                errors.push(format!("Parameter '{name}' must be a number"));
            }
        } else if is_text(kind, "boolean") {
            if !matches!(value, Value::Bool(_)) {
                errors.push(format!("Parameter '{name}' must be a boolean"));
            }
        } else if is_text(kind, "object") {
            if !matches!(value, Value::Dict(_)) {
                errors.push(format!("State parameter '{name}' must be an object"));
            }
        } else if is_text(kind, "enum") {
            let options = match &parameter.enum_values {
                Value::List(items) | Value::Tuple(items) => (**items).clone(),
                _ => Vec::new(),
            };
            if !options.iter().any(|option| py_eq(option, &value)) {
                let mut texts = Vec::new();
                for (index, option) in options.iter().enumerate() {
                    match option {
                        Value::Str(text) => texts.push(text.to_string()),
                        other => {
                            return Err(ExecError::type_error(format!(
                                "sequence item {index}: expected str instance, {} found",
                                other.type_name()
                            )))
                        }
                    }
                }
                errors.push(format!("Parameter '{name}' must be one of: {}", texts.join(", ")));
            }
        } else if is_text(kind, "entity_id_list") {
            match &value {
                Value::List(items) => {
                    let all_strings = items.iter().all(|item| matches!(item, Value::Str(_)));
                    if !all_strings {
                        errors.push(format!("Parameter '{name}' must contain only entity ID strings"));
                    }
                    items_bounds(items, &mut errors);
                    if parameter.unique_items.truthy() && all_strings {
                        let mut seen: Vec<&Value> = Vec::new();
                        for item in items.iter() {
                            if !seen.contains(&item) {
                                seen.push(item);
                            }
                        }
                        if seen.len() != items.len() {
                            errors.push(format!("Parameter '{name}' must contain unique items"));
                        }
                    }
                    if is_text(resolve, "entity_list") {
                        if let Some(bridge) = bridge {
                            for item in items.iter() {
                                if let Value::Str(id) = item {
                                    if !bridge.entity_exists(id) {
                                        errors.push(format!("Entity '{id}' not found in parameter '{name}'"));
                                        unknown.push(id.to_string());
                                    }
                                }
                            }
                        }
                    }
                }
                _ => errors.push(format!("Parameter '{name}' must be a list of entity IDs")),
            }
        } else if is_text(kind, "array") {
            match &value {
                Value::List(items) => {
                    items_bounds(items, &mut errors);
                    if parameter.unique_items.truthy() {
                        let mut seen: Vec<String> = Vec::new();
                        for item in items.iter() {
                            let repr = item.repr();
                            if !seen.contains(&repr) {
                                seen.push(repr);
                            }
                        }
                        if seen.len() != items.len() {
                            errors.push(format!("Parameter '{name}' must contain unique items"));
                        }
                    }
                }
                _ => errors.push(format!("Parameter '{name}' must be a list")),
            }
        }
        if matches!(value, Value::Int(_) | Value::Float(_)) {
            if parameter.minimum != Value::None && below(&value, &parameter.minimum, true)? {
                errors.push(format!("Parameter '{name}' must be at least {}", parameter.minimum.to_str()));
            }
            if parameter.maximum != Value::None && below(&value, &parameter.maximum, false)? {
                errors.push(format!("Parameter '{name}' must be at most {}", parameter.maximum.to_str()));
            }
        }
    }
    Ok(errors)
}

/// Entity snapshots fetched by these parameter names even without a `resolve` (Python's set
/// literal; the order they are fetched in is not observable).
const NAMED_ENTITY_PARAMETERS: [&str; 8] =
    ["actor", "against_target", "ally", "attacker", "caster", "entity", "helper", "target"];

/// `_build_context(...)`.
// The arguments are Python's own (`execute_tool`'s and `prepare_tool_context`'s parameter lists).
#[allow(clippy::too_many_arguments)]
fn build_context<'a>(
    tool: &ExecTool,
    params: &Dict,
    bridge: Option<&'a mut (dyn StateBridge + 'static)>,
    host: &dyn ToolHost,
    combat: Combat,
    budget: &'a ExecutionBudget,
    dice: &'a mut Dice,
    choice: Option<&'a ChoiceControl>,
    procedure: Option<ProcedureExecutor>,
    mode: NestedMode,
) -> ExecutionContext<'a> {
    let mut ctx = ExecutionContext {
        params: params.clone(),
        vars: Dict::new(),
        entities: Dict::new(),
        ruleset_config: host.ruleset_config().clone(),
        definitions: host.definitions().clone(),
        combat,
        state_bridge: bridge,
        current_tool: tool.name.clone(),
        extras: Extras::default(),
        budget,
        dice,
        choice,
        list_history: Vec::new(),
        procedure_executor: procedure,
        nested_mode: mode,
    };
    if let Some(bridge) = ctx.state_bridge.as_deref() {
        if bridge.entity_exists("state") {
            if let Ok(snapshot) = bridge.get_entity("state") {
                ctx.entities.insert("state".into(), snapshot);
            }
        }
    }
    for parameter in &tool.parameters {
        let name = parameter.name_text();
        let id = match params.get(&name) {
            Some(Value::Str(id)) if !id.is_empty() => Some(id.to_string()),
            _ => None,
        };
        if is_text(&parameter.resolve, "entity") {
            ctx.extras.entity_parameter_names.insert(name.clone());
            if let (Some(id), Some(bridge)) = (id, ctx.state_bridge.as_deref()) {
                if let Ok(snapshot) = bridge.get_entity(&id) {
                    ctx.entities.insert(id.clone(), snapshot.clone());
                    ctx.vars.insert(name.clone(), snapshot);
                    ctx.vars.insert(format!("_{name}_id"), Value::str(id));
                }
            }
        } else if is_text(&parameter.resolve, "participant") {
            ctx.extras.participant_parameter_names.insert(name.clone());
            if let (Some(id), Some(bridge)) = (id, ctx.state_bridge.as_deref()) {
                if let Ok(snapshot) = bridge.get_participant(&id) {
                    ctx.entities.insert(id.clone(), snapshot.clone());
                    ctx.vars.insert(name.clone(), snapshot);
                    ctx.vars.insert(format!("_{name}_id"), Value::str(id));
                }
            }
        }
    }
    for name in NAMED_ENTITY_PARAMETERS {
        if let (Some(Value::Str(id)), Some(bridge)) = (params.get(name), ctx.state_bridge.as_deref()) {
            if !id.is_empty() && !ctx.entities.contains_key(&**id) {
                if let Ok(snapshot) = bridge.get_entity(id) {
                    ctx.entities.insert(id.to_string(), snapshot);
                }
            }
        }
    }
    let weapons = match &ctx.definitions {
        Value::Dict(map) => map.get("weapons").cloned().unwrap_or_else(|| Value::dict(Dict::new())),
        _ => Value::dict(Dict::new()),
    };
    let weapon = |key: &str| match (params.get(key), &weapons) {
        (Some(Value::Str(name)), Value::Dict(map)) if !name.is_empty() => {
            Some(map.get(&**name).cloned().unwrap_or(Value::None))
        }
        _ => None,
    };
    if let Some(info) = weapon("weapon") {
        let info = if info.truthy() { info } else { Value::dict(Dict::new()) };
        ctx.vars.insert("weapon_info".into(), info);
    }
    if let Some(info) = weapon("offhand_weapon") {
        if info.truthy() {
            ctx.vars.insert("offhand_weapon_info".into(), info);
        }
    }
    ctx
}

/// `_inject_distance_vars(tool_def, params, ctx, state_bridge)`.
fn inject_distance_vars(tool: &ExecTool, params: &Dict, ctx: &mut ExecutionContext<'_>) {
    if !tool.range.truthy() {
        return;
    }
    let field = |key: &str| match &tool.range {
        Value::Dict(map) => map.get(key).cloned().unwrap_or(Value::None),
        _ => Value::None,
    };
    ctx.vars.insert("tool_range_type".into(), field("type"));
    ctx.vars.insert("tool_range_value".into(), field("value"));
    if let (Some(Value::Str(target)), Some(bridge)) = (params.get("target"), ctx.state_bridge.as_deref()) {
        if !target.is_empty() {
            if let Some(Value::Dict(position)) = bridge.get_position(target) {
                if let Some(band) = position.get("band").filter(|band| band.truthy()) {
                    let band = band.clone();
                    ctx.vars.insert("target_band".into(), band);
                    return;
                }
            }
        }
    }
    ctx.vars.insert("target_band".into(), Value::None);
}

/// `_collect_narrative(ctx)`: every `_narrative_*` variable as text.
fn collect_narrative(ctx: &ExecutionContext<'_>) -> Dict {
    ctx.vars
        .iter()
        .filter_map(|(key, value)| {
            key.strip_prefix("_narrative_").map(|name| {
                (name.to_owned(), Value::str(if value.truthy() { value.to_str() } else { String::new() }))
            })
        })
        .collect()
}

/// `_collect_narrative_directives(ctx)`: `list(...)` of the directives variable.
fn collect_directives(ctx: &ExecutionContext<'_>) -> Result<Vec<Value>, ExecError> {
    match ctx.vars.get("_narrative_directives") {
        None => Ok(Vec::new()),
        Some(Value::List(items) | Value::Tuple(items)) => Ok((**items).clone()),
        Some(Value::Str(text)) => Ok(text.chars().map(|c| Value::str(c.to_string())).collect()),
        Some(Value::Dict(map)) => Ok(map.keys().map(|key| Value::str(key.as_str())).collect()),
        Some(other) => Err(ExecError::type_error(format!("'{}' object is not iterable", other.type_name()))),
    }
}

fn logs(ctx: &ExecutionContext<'_>) -> Value {
    ctx.vars.get("_logs").cloned().unwrap_or_else(|| Value::list(Vec::new()))
}

/// A failure that still reports the context, narrative and logs.
fn failure_with_context(
    ctx: &ExecutionContext<'_>,
    error: String,
    kind: &str,
) -> Result<ToolResult, ExecError> {
    Ok(ToolResult {
        context: ctx.vars.clone(),
        narrative_hooks: collect_narrative(ctx),
        narrative_directives: collect_directives(ctx)?,
        logs: logs(ctx),
        ..ToolResult::failure(&ctx.current_tool, error, kind)
    })
}

/// Either a ready context or the result that ends the call before effects run.
pub enum Prepared<'a> {
    Context(Box<ExecutionContext<'a>>),
    Result(Box<ToolResult>),
}

/// `prepare_tool_context(...)`: bind the context and check prerequisites without running
/// effects.
// The arguments are Python's own (`execute_tool`'s and `prepare_tool_context`'s parameter lists).
#[allow(clippy::too_many_arguments)]
pub fn prepare_tool_context<'a>(
    tool_name: &str,
    params: &Dict,
    host: &dyn ToolHost,
    bridge: Option<&'a mut (dyn StateBridge + 'static)>,
    combat: Combat,
    budget: &'a ExecutionBudget,
    dice: &'a mut Dice,
    choice: Option<&'a ChoiceControl>,
    procedure: Option<ProcedureExecutor>,
    mode: NestedMode,
) -> Result<Prepared<'a>, ExecError> {
    let Some(tool) = host.tool(tool_name) else {
        return Ok(Prepared::Result(Box::new(ToolResult::failure(
            tool_name,
            format!("Tool '{tool_name}' not found."),
            "tool_unavailable",
        ))));
    };
    let mut merged = Dict::new();
    for parameter in &tool.parameters {
        let name = parameter.name_text();
        if !params.contains_key(&name) && (parameter.default != Value::None || !parameter.required.truthy()) {
            merged.insert(name, super::context::deep_copy(&parameter.default));
        }
    }
    for (key, value) in params {
        merged.insert(key.clone(), value.clone());
    }
    let mut unknown = Vec::new();
    let errors = validate_params(tool, &merged, bridge.as_deref(), &mut unknown)?;
    if !errors.is_empty() {
        let mut unique: Vec<String> = Vec::new();
        for id in unknown {
            if !unique.contains(&id) {
                unique.push(id);
            }
        }
        return Ok(Prepared::Result(Box::new(ToolResult {
            unknown_references: unique,
            ..ToolResult::failure(
                tool_name,
                format!("Parameter validation failed: {}", errors.join("; ")),
                "parameter_validation",
            )
        })));
    }
    let mut ctx = build_context(tool, &merged, bridge, host, combat, budget, dice, choice, procedure, mode);
    inject_distance_vars(tool, &merged, &mut ctx);
    let mut failures: Vec<String> = Vec::new();
    let mut evaluation_error: Option<String> = None;
    let conditions = match &tool.pre_conditions {
        Value::List(items) | Value::Tuple(items) => (**items).clone(),
        _ => Vec::new(),
    };
    for condition in &conditions {
        let (expression, template) = match condition {
            Value::Str(text) => (text.to_string(), "Pre-condition failed".to_owned()),
            Value::Dict(map) => (
                map.get("condition").map_or_else(String::new, Value::to_str),
                map.get("error").map_or_else(|| "Pre-condition failed".to_owned(), Value::to_str),
            ),
            other => {
                return Err(ExecError::Python {
                    class: "AttributeError",
                    message: format!("'{}' object has no attribute 'get'", other.type_name()),
                })
            }
        };
        match ctx.with_eval(|eval| crate::expr::evaluate_condition(&expression, eval)) {
            Ok(true) => {}
            Ok(false) => {
                let message = resolve_template(&template, &ctx);
                if !failures.contains(&message) && failures.len() < 64 {
                    failures.push(message);
                }
            }
            Err(error) => {
                if evaluation_error.is_none() {
                    evaluation_error = Some(format!("Pre-condition evaluation error: {}", error.message()));
                }
            }
        }
    }
    if !failures.is_empty() {
        if evaluation_error.is_some() && failures.len() < 64 {
            failures.push(
                "The Ruleset could not evaluate another precondition. If the declared inputs are complete, report a Ruleset defect."
                    .into(),
            );
        }
        let first = failures[0].clone();
        let mut result = failure_with_context(&ctx, first, "precondition")?;
        result.failure_reasons = failures;
        return Ok(Prepared::Result(Box::new(result)));
    }
    if let Some(error) = evaluation_error {
        return Ok(Prepared::Result(Box::new(ToolResult::failure(
            tool_name,
            error,
            "precondition_evaluation",
        ))));
    }
    Ok(Prepared::Context(Box::new(ctx)))
}

/// `execute_tool(...)`. A suspended choice is the only exception that leaves it.
// The arguments are Python's own (`execute_tool`'s and `prepare_tool_context`'s parameter lists).
#[allow(clippy::too_many_arguments)]
pub fn execute_tool(
    tool_name: &str,
    params: &Dict,
    host: &dyn ToolHost,
    bridge: Option<&mut (dyn StateBridge + 'static)>,
    combat: Combat,
    budget: Option<&ExecutionBudget>,
    dice: &mut Dice,
    choice: Option<&ChoiceControl>,
    procedure: Option<ProcedureExecutor>,
    mode: NestedMode,
) -> Result<ToolResult, ExecError> {
    let local = ExecutionBudget::default();
    let budget = budget.unwrap_or(&local);
    let mut ctx = match prepare_tool_context(
        tool_name, params, host, bridge, combat, budget, dice, choice, procedure, mode,
    )? {
        Prepared::Result(result) => return Ok(*result),
        Prepared::Context(ctx) => ctx,
    };
    let effects = host.tool(tool_name).expect("prepared tools exist").effects.clone();
    match execute_effects(&effects, &mut ctx, host) {
        Ok(()) => {}
        Err(error @ ExecError::Suspended(_)) => return Err(error),
        Err(ExecError::PreCondition { message, .. }) => {
            return Ok(ToolResult::failure(tool_name, message, "precondition"));
        }
        Err(error) => {
            return failure_with_context(
                &ctx,
                format!("Effect execution error: {}", error.message()),
                "effect_execution",
            );
        }
    }
    Ok(ToolResult {
        tool_name: tool_name.into(),
        success: true,
        context: ctx.vars.clone(),
        public_context: ctx.extras.published_result.clone(),
        private_random_sampled: ctx.budget.private_random_sampled.get(),
        narrative_hooks: collect_narrative(&ctx),
        narrative_directives: collect_directives(&ctx)?,
        logs: logs(&ctx),
        ..ToolResult::default()
    })
}
