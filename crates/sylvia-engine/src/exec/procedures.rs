//! `runtime/_procedure_executor.py`: running the procedures a trigger names.
//!
//! As the package runtime runs them: a trigger from a tool starts at depth 0 against the
//! bridge's combat state; a trigger from a procedure step goes one level deeper against that
//! procedure's combat state. Tools a procedure calls get a copy of its combat state and no
//! package choice control, as `_make_nested_executor` gives them.

use super::bridge::StateBridge;
use super::context::{Combat, ExecutionContext, Extras, NestedMode, ProcedureExecutor};
use super::effects::execute_effects;
use super::error::ExecError;
use super::tool::{data_value, ToolHost};
use crate::dice::Dice;
use crate::expr::value::{py_eq, Dict, Object, Value};
use crate::expr::ExecutionBudget;
use std::sync::Arc;

/// `MAX_PROCEDURE_DEPTH`.
pub const MAX_PROCEDURE_DEPTH: usize = 10;

/// `ProcedureResult`.
#[derive(Debug, Clone, PartialEq)]
pub struct ProcedureResult {
    pub procedure_name: String,
    pub trigger: String,
    pub success: bool,
    pub narrative_hooks: Dict,
    pub logs: Vec<Value>,
    pub skipped_steps: Vec<Value>,
    pub error: Option<String>,
}

impl ProcedureResult {
    fn new(name: &str, trigger: &str) -> Self {
        Self {
            procedure_name: name.into(),
            trigger: trigger.into(),
            success: true,
            narrative_hooks: Dict::new(),
            logs: Vec::new(),
            skipped_steps: Vec::new(),
            error: None,
        }
    }

    /// The dataclass as a value, for `store_as`.
    pub fn to_value(&self) -> Value {
        Value::Object(Arc::new(Object {
            class: "ProcedureResult",
            fields: vec![
                ("procedure_name", Value::str(self.procedure_name.as_str())),
                ("trigger", Value::str(self.trigger.as_str())),
                ("success", Value::Bool(self.success)),
                ("tool_results", Value::list(Vec::new())),
                ("narrative_hooks", Value::dict(self.narrative_hooks.clone())),
                ("narrative_directives", Value::list(Vec::new())),
                ("logs", Value::list(self.logs.clone())),
                ("skipped_steps", Value::list(self.skipped_steps.clone())),
                ("error", self.error.as_deref().map_or(Value::None, Value::str)),
            ],
            methods: &[],
        }))
    }
}

/// The procedures a host's Ruleset declares, in order, as execution values.
pub struct ExecProcedure {
    pub name: String,
    pub trigger: Value,
    pub condition: Value,
    pub steps: Value,
}

impl ExecProcedure {
    pub fn from_def(name: &str, procedure: &crate::ruleset::runtime_tables::ProcedureDef) -> Self {
        Self {
            name: name.to_owned(),
            trigger: data_value(&procedure.trigger),
            condition: data_value(&procedure.condition),
            steps: data_value(&procedure.steps),
        }
    }
}

fn attribute_error(value: &Value, attribute: &str) -> ExecError {
    ExecError::Python {
        class: "AttributeError",
        message: format!("'{}' object has no attribute '{attribute}'", value.type_name()),
    }
}

/// `name in step` for any step value: a mapping's keys, text's substrings, a list's members.
fn contains(step: &Value, name: &str) -> Result<bool, ExecError> {
    match step {
        Value::Dict(map) => Ok(map.contains_key(name)),
        Value::Str(text) => Ok(text.contains(name)),
        Value::List(items) | Value::Tuple(items) => Ok(items.iter().any(|item| py_eq(item, &Value::str(name)))),
        other => Err(ExecError::type_error(format!("argument of type '{}' is not iterable", other.type_name()))),
    }
}

/// `_step_references_missing_entity(step, ctx, state_bridge)`.
fn references_missing_entity(step: &Value, ctx: &ExecutionContext<'_>) -> Result<bool, ExecError> {
    let bridge = ctx.state_bridge.as_deref();
    if contains(step, "set")? {
        let Value::Dict(map) = step else { return Err(attribute_error(step, "get")) };
        let body = map.get("set").cloned().unwrap_or_else(|| Value::dict(Dict::new()));
        let Value::Dict(body) = &body else { return Err(attribute_error(&body, "get")) };
        let field = body.get("field").cloned().unwrap_or_else(|| Value::str(""));
        if field.truthy() {
            let Value::Str(field) = &field else { return Err(attribute_error(&field, "split")) };
            let entity_id = field.split('.').next().unwrap_or_default();
            if ctx.entities.contains_key(entity_id) && bridge.is_some_and(|b| !b.entity_exists(entity_id)) {
                return Ok(true);
            }
        }
    }
    for key in ["call", "apply_damage", "apply_condition", "remove_condition", "heal", "save_throw", "decrement", "increment"] {
        if !contains(step, key)? {
            continue;
        }
        let Value::Dict(map) = step else { return Err(attribute_error(step, "get")) };
        if let Some(Value::Dict(params)) = map.get(key) {
            for name in ["target", "entity", "attacker", "caster", "actor"] {
                if let Some(Value::Str(id)) = params.get(name) {
                    if !id.is_empty() && bridge.is_some_and(|b| !b.entity_exists(id)) {
                        return Ok(true);
                    }
                }
            }
        }
    }
    Ok(false)
}

/// `_inject_flags_into_context(ctx, state_bridge)`: active `_campaign` flags, any failure ignored.
fn inject_flags(ctx: &mut ExecutionContext<'_>) {
    let Some(bridge) = ctx.state_bridge.as_deref() else { return };
    if let Ok(Value::Dict(flags)) = bridge.read_field("_campaign", "flags") {
        for (name, value) in flags.iter() {
            if value.truthy() {
                ctx.vars.insert(format!("_flag_{name}"), Value::Bool(true));
            }
        }
    }
}

/// `combat_state or {}`: an empty combat dict is replaced by a new one.
fn combat_or_new(combat: &Combat, bridge: Option<&dyn StateBridge>) -> (Combat, bool) {
    let current = match (combat, bridge) {
        (Combat::Own(map), _) => map.clone(),
        (Combat::Bridge, Some(bridge)) => bridge.combat().clone(),
        (Combat::Bridge, None) => Dict::new(),
    };
    if current.is_empty() {
        (Combat::Own(Dict::new()), false)
    } else {
        (combat.clone(), true)
    }
}

/// Everything a procedure's steps share with the call that triggered them.
pub struct TriggerCall<'a, 'b> {
    pub host: &'a dyn ToolHost,
    pub bridge: Option<&'b mut (dyn StateBridge + 'static)>,
    pub combat: Combat,
    pub budget: &'b ExecutionBudget,
    pub dice: &'b mut Dice,
    pub depth: usize,
}

/// `trigger_procedure(trigger_name, ..., context=..., combat_state=..., budget=..., _depth=...)`.
pub fn trigger_procedure(trigger_name: &str, context: &Dict, call: TriggerCall<'_, '_>) -> Result<ProcedureResult, ExecError> {
    let TriggerCall { host, mut bridge, combat, budget, dice, depth } = call;
    if depth > MAX_PROCEDURE_DEPTH {
        return Err(ExecError::Python {
            class: "ProcedureDepthError",
            message: format!(
                "Procedure chain exceeded depth limit ({depth} > {MAX_PROCEDURE_DEPTH}) at trigger '{trigger_name}'. Possible circular definition."
            ),
        });
    }
    let trigger = Value::str(trigger_name);
    let matching: Vec<&ExecProcedure> = host.procedures().iter().filter(|p| py_eq(&p.trigger, &trigger)).collect();
    if matching.is_empty() {
        return Ok(ProcedureResult::new(trigger_name, trigger_name));
    }
    let mut result = ProcedureResult::new(&matching.iter().map(|p| p.name.as_str()).collect::<Vec<_>>().join(","), trigger_name);
    let failure = |result: &ProcedureResult, name: &str, error: String| ProcedureResult {
        procedure_name: name.into(),
        success: false,
        error: Some(error),
        ..result.clone()
    };
    for procedure in matching {
        if procedure.condition.truthy() {
            let (condition_combat, _) = combat_or_new(&combat, bridge.as_deref());
            let mut ctx = context_for(context, host, bridge.as_deref_mut(), condition_combat, budget, &mut *dice, "");
            inject_flags(&mut ctx);
            let expression = procedure.condition.to_str();
            match ctx.with_eval(|eval| crate::expr::evaluate_condition(&expression, eval)) {
                Ok(true) => {}
                Ok(false) => continue,
                Err(error) => {
                    let message = format!("Procedure condition evaluation failed: {}", error.message());
                    return Ok(failure(&result, &procedure.name, message));
                }
            }
        }
        let (procedure_combat, truthy) = combat_or_new(&combat, bridge.as_deref());
        let mut ctx = context_for(context, host, bridge.as_deref_mut(), procedure_combat, budget, &mut *dice, &procedure.name);
        for key in ["actor", "attacker", "target", "entity", "caster", "current_actor", "member", "combatant"] {
            if let (Some(Value::Str(id)), Some(bridge)) = (context.get(key), ctx.state_bridge.as_deref()) {
                if !id.is_empty() {
                    if let Ok(snapshot) = bridge.get_entity(id) {
                        ctx.entities.insert(id.to_string(), snapshot);
                    }
                }
            }
        }
        if truthy {
            let order = ctx.combat_state().get("order").cloned().unwrap_or_else(|| Value::list(Vec::new()));
            let length = match &order {
                Value::List(items) | Value::Tuple(items) => items.len(),
                Value::Str(text) => text.chars().count(),
                Value::Dict(map) => map.len(),
                other => return Err(ExecError::type_error(format!("object of type '{}' has no len()", other.type_name()))),
            };
            ctx.set_combat("order_length", Value::Int(length as i64));
        }
        ctx.procedure_executor = Some(ProcedureExecutor { depth: depth + 1, combat: ctx.combat.clone() });
        ctx.nested_mode = NestedMode::Procedure;
        let steps = match &procedure.steps {
            Value::List(items) | Value::Tuple(items) => (**items).clone(),
            Value::Dict(map) => map.keys().map(|key| Value::str(key.as_str())).collect(),
            Value::Str(text) => text.chars().map(|c| Value::str(c.to_string())).collect(),
            other => return Err(ExecError::type_error(format!("'{}' object is not iterable", other.type_name()))),
        };
        let outcome: Result<(), ExecError> = (|| {
            for step in steps {
                if references_missing_entity(&step, &ctx)? {
                    let mut skipped = Dict::new();
                    skipped.insert("step".into(), step.clone());
                    skipped.insert("reason".into(), Value::str("Referenced entity no longer exists"));
                    result.skipped_steps.push(Value::dict(skipped));
                    continue;
                }
                execute_effects(&Value::list(vec![step]), &mut ctx, host)?;
            }
            Ok(())
        })();
        match outcome {
            Ok(()) => {}
            Err(error @ ExecError::Suspended(_)) => return Err(error),
            Err(error) if error.class_name() == "ProcedureDepthError" => return Err(error),
            Err(error) => return Ok(failure(&result, &procedure.name, error.message())),
        }
        if let Some(logs) = ctx.vars.get("_logs") {
            match logs {
                Value::List(items) | Value::Tuple(items) => result.logs.extend(items.iter().cloned()),
                Value::Dict(map) => result.logs.extend(map.keys().map(|key| Value::str(key.as_str()))),
                Value::Str(text) => result.logs.extend(text.chars().map(|c| Value::str(c.to_string()))),
                other => return Err(ExecError::type_error(format!("'{}' object is not iterable", other.type_name()))),
            }
        }
        for (key, value) in &ctx.vars {
            if let Some(name) = key.strip_prefix("_narrative_") {
                let text = if value.truthy() { value.to_str() } else { String::new() };
                result.narrative_hooks.insert(name.to_owned(), Value::str(text));
            }
        }
    }
    Ok(result)
}

/// The `ExecutionContext` a procedure (or its condition) runs in.
fn context_for<'a>(
    context: &Dict,
    host: &dyn ToolHost,
    bridge: Option<&'a mut (dyn StateBridge + 'static)>,
    combat: Combat,
    budget: &'a ExecutionBudget,
    dice: &'a mut Dice,
    name: &str,
) -> ExecutionContext<'a> {
    ExecutionContext {
        params: context.clone(),
        vars: Dict::new(),
        entities: Dict::new(),
        ruleset_config: host.ruleset_config().clone(),
        definitions: host.definitions().clone(),
        combat,
        state_bridge: bridge,
        current_tool: name.to_owned(),
        extras: Extras::default(),
        budget,
        dice,
        choice: None,
        list_history: Vec::new(),
        procedure_executor: None,
        nested_mode: NestedMode::Procedure,
    }
}
