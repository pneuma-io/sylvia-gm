//! `runtime/_effect_executor.py`: the effect dispatcher and the handlers ported so far (state
//! and context writes, requirements, branches, logs, narration, the public result, entities,
//! participants and nested calls). The rest arrive in later pull requests; until then the
//! dispatcher refuses them by name.

use super::context::{Combat, ExecutionContext};
use super::error::{float_to_int, ExecError};
use super::handlers;
use super::template::{resolve_params, resolve_template};
use super::tool::{execute_tool, ToolHost};
use crate::expr::value::{Dict, Value};
use crate::expr::EvalError;
use crate::ruleset::schema_validator::VALID_EFFECT_KEYS;

/// The handlers this build runs.
pub const PORTED_HANDLERS: &[&str] = &[
    "set",
    "increment",
    "decrement",
    "require",
    "condition",
    "log",
    "narrate",
    "publish",
    "create_entity",
    "set_participant",
    "call",
];

/// The effects a deterministic preflight may run.
const PREFLIGHT_HANDLERS: &[&str] = &[
    "set",
    "require",
    "call",
    "condition",
    "for_each",
    "sort",
    "list_push",
    "list_pop",
    "table_lookup",
    "spatial_query",
];

/// `execute_effects(effects, context, tool_executor)`: each effect in turn.
pub fn execute_effects(
    effects: &Value,
    ctx: &mut ExecutionContext<'_>,
    host: &dyn ToolHost,
) -> Result<(), ExecError> {
    for effect in iterate(effects)? {
        execute_effect(&effect, ctx, host)?;
    }
    Ok(())
}

/// `for item in value` over what a body list holds.
pub(crate) fn iterate(value: &Value) -> Result<Vec<Value>, ExecError> {
    match value {
        Value::List(items) | Value::Tuple(items) => Ok((**items).clone()),
        Value::Dict(map) => Ok(map.keys().map(|key| Value::str(key.as_str())).collect()),
        Value::Str(text) => Ok(text.chars().map(|c| Value::str(c.to_string())).collect()),
        other => Err(ExecError::type_error(format!("'{}' object is not iterable", other.type_name()))),
    }
}

fn get<'v>(map: &'v Dict, key: &str) -> Option<&'v Value> {
    map.get(key)
}

/// `_execute_effect(effect, context, tool_executor)`.
pub(crate) fn execute_effect(
    effect: &Value,
    ctx: &mut ExecutionContext<'_>,
    host: &dyn ToolHost,
) -> Result<(), ExecError> {
    ctx.budget.consume_operation().map_err(|error| ExecError::effect("budget", error.message))?;
    let Value::Dict(effect_map) = effect else {
        return Err(ExecError::Python {
            class: "AttributeError",
            message: format!("'{}' object has no attribute 'get'", effect.type_name()),
        });
    };
    let condition_value = get(effect_map, "condition").cloned().unwrap_or(Value::None);
    let mut handler_keys: Vec<&str> =
        effect_map.keys().map(String::as_str).filter(|key| VALID_EFFECT_KEYS.contains(key)).collect();
    let flat_conditional = matches!(condition_value, Value::Str(_))
        && (effect_map.contains_key("on_true") || effect_map.contains_key("on_false"))
        && handler_keys.iter().all(|key| *key == "condition");
    let empty = || Value::list(Vec::new());
    let (effect_type, body) = if flat_conditional {
        let mut body = Dict::new();
        body.insert("condition".into(), condition_value.clone());
        body.insert("on_true".into(), get(effect_map, "on_true").cloned().unwrap_or_else(empty));
        body.insert("on_false".into(), get(effect_map, "on_false").cloned().unwrap_or_else(empty));
        ("condition".to_owned(), Value::dict(body))
    } else {
        if matches!(condition_value, Value::Str(_)) {
            handler_keys.retain(|key| *key != "condition");
        }
        if handler_keys.len() != 1 {
            return Err(ExecError::effect(
                "unknown",
                "Effect declaration must contain exactly one recognized handler",
            ));
        }
        (handler_keys[0].to_owned(), effect_map[handler_keys[0]].clone())
    };
    if effect_type != "condition" && condition_value.truthy() {
        let expression = condition_value.to_str();
        match ctx.with_eval(|eval| crate::expr::evaluate_condition(&expression, eval)) {
            Ok(true) => {}
            Ok(false) => return Ok(()),
            Err(EvalError::Expression(error)) => return Err(ExecError::effect(&effect_type, error.message)),
            Err(error) => return Err(ExecError::from(error).in_effect(&effect_type)),
        }
    }
    if ctx.budget.preflight_only && !PREFLIGHT_HANDLERS.contains(&effect_type.as_str()) {
        return Err(ExecError::effect(
            &effect_type,
            "This effect is unavailable during a deterministic preflight",
        ));
    }
    let result = match effect_type.as_str() {
        "set" => handle_set(&body, ctx),
        "increment" => handle_modify_field(&body, ctx, 1),
        "decrement" => handle_modify_field(&body, ctx, -1),
        "require" => handle_require(&body, ctx),
        "condition" => handle_conditional(&body, ctx, host),
        "log" => handle_log(&body, ctx),
        "narrate" => handle_narrate(&body, effect_map, ctx),
        "publish" => handle_publish(&body, ctx),
        "create_entity" => handle_create_entity(&body, ctx),
        "set_participant" => handle_set_participant(&body, ctx),
        "call" => handle_nested_call(&body, effect_map, ctx, host),
        "dice" => handlers::handle_dice(&body, effect_map, ctx),
        "save_throw" => handlers::handle_save_throw(&body, effect_map, ctx),
        "apply_damage" => handlers::handle_apply_damage(&body, ctx),
        "heal" => handlers::handle_heal(&body, ctx),
        "apply_condition" => handlers::handle_condition_change(&body, ctx, true),
        "remove_condition" => handlers::handle_condition_change(&body, ctx, false),
        "apply_ability_damage" => handlers::handle_apply_ability_damage(&body, ctx),
        "count_successes" => handlers::handle_count_successes(&body, effect_map, ctx),
        "reroll" => handlers::handle_reroll(&body, effect_map, ctx, host),
        "hunger_dice" => handlers::handle_hunger_dice(&body, effect_map, ctx),
        "table_lookup" => handlers::handle_table_lookup(&body, effect_map, ctx),
        "random_table" => handlers::handle_random_table(&body, effect_map, ctx),
        "for_each" => handlers::handle_for_each(&body, ctx, host),
        "sort" => handlers::handle_sort(&body, ctx),
        "list_push" => handlers::handle_list_push(&body, effect_map, ctx),
        "list_pop" => handlers::handle_list_pop(&body, effect_map, ctx),
        "shuffle" => handlers::handle_shuffle(&body, effect_map, ctx),
        "request_choice" => handlers::handle_request_choice(&body, effect_map, ctx),
        other => Err(ExecError::effect(other, "This effect is not yet ported to Rust")),
    };
    result.map_err(|error| error.in_effect(&effect_type))
}

// ── Value resolution ──────────────────────────────────────────────────────────────────────

/// `_resolve_value(expr, ctx)`: mappings and lists recursively, `"true"`/`"false"`/`"null"`,
/// other text evaluated (a failed evaluation is the literal text; `None` stays `None` only for
/// a declared name or a scoped path), and typed YAML literals as they are.
pub fn resolve_value(expr: &Value, ctx: &ExecutionContext<'_>) -> Result<Value, ExecError> {
    match expr {
        Value::Dict(map) => {
            let mut out = Dict::new();
            for (key, item) in map.iter() {
                out.insert(key.clone(), resolve_value(item, ctx)?);
            }
            Ok(Value::dict(out))
        }
        Value::List(items) => {
            Ok(Value::list(items.iter().map(|item| resolve_value(item, ctx)).collect::<Result<_, _>>()?))
        }
        Value::Str(text) => {
            match &**text {
                "true" => return Ok(Value::Bool(true)),
                "false" => return Ok(Value::Bool(false)),
                "null" => return Ok(Value::None),
                _ => {}
            }
            match ctx.with_eval(|eval| crate::expr::evaluate_expression(text, eval)) {
                Err(EvalError::Expression(_)) => Ok(expr.clone()),
                Err(error) => Err(error.into()),
                Ok(Value::None) => {
                    let root: String = text
                        .chars()
                        .take_while(|c| c.is_ascii_alphanumeric() || *c == '_')
                        .collect::<String>();
                    let root = if root.chars().next().is_some_and(|c| c.is_ascii_alphabetic() || c == '_') {
                        root
                    } else {
                        String::new()
                    };
                    let declared = !root.is_empty()
                        && (ctx.params.contains_key(&root)
                            || ctx.vars.contains_key(&root)
                            || ctx.entities.contains_key(&root));
                    let scoped = matches!(root.as_str(), "state" | "combat" | "ruleset" | "definitions")
                        && (text.contains('.') || text.contains('['));
                    Ok(if declared || scoped { Value::None } else { expr.clone() })
                }
                Ok(value) => Ok(value),
            }
        }
        other => Ok(super::context::deep_copy(other)),
    }
}

/// `int(value)` for a resolved amount.
pub(crate) fn to_int(value: &Value) -> Result<i64, ExecError> {
    match value {
        Value::Int(number) => Ok(*number),
        Value::Bool(flag) => Ok(i64::from(*flag)),
        Value::Float(number) => float_to_int(*number),
        Value::Str(text) => sylvia_base::pystr::parse_int(text)
            .map_err(|error| ExecError::value_error(error.refusal().message)),
        other => Err(ExecError::type_error(format!(
            "int() argument must be a string, a bytes-like object or a real number, not '{}'",
            other.type_name()
        ))),
    }
}

/// `_resolve_int(expr, ctx)`.
pub(crate) fn resolve_int(expr: &Value, ctx: &ExecutionContext<'_>) -> Result<i64, ExecError> {
    match expr {
        Value::Int(_) | Value::Float(_) | Value::Bool(_) => to_int(expr),
        other => {
            let text = other.to_str();
            match ctx.with_eval(|eval| crate::expr::evaluate_expression(&text, eval))? {
                Value::None => Ok(0),
                value => to_int(&value),
            }
        }
    }
}

/// `_resolve_entity_ref(ref, ctx)`.
pub(crate) fn resolve_entity_ref(reference: &str, ctx: &ExecutionContext<'_>) -> Option<String> {
    let clean = reference.trim_matches(|c| c == '{' || c == '}');
    if let Some(param) = ctx.params.get(clean) {
        match param {
            Value::Str(id) => return Some(id.to_string()),
            Value::Dict(map) if map.contains_key("id") => {
                return match &map["id"] {
                    Value::Str(id) => Some(id.to_string()),
                    _ => None,
                };
            }
            _ => {}
        }
    }
    if ctx.entities.contains_key(clean) {
        return Some(clean.to_owned());
    }
    if let Some(Value::Str(id)) = ctx.params.get(reference) {
        return Some(id.to_string());
    }
    Some(reference.to_owned())
}

/// `_refresh_entity_snapshots(ctx)`.
pub(crate) fn refresh_entity_snapshots(ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let Some(bridge) = ctx.state_bridge.as_deref() else {
        return Ok(());
    };
    let ids: Vec<String> = ctx.entities.keys().cloned().collect();
    for id in ids {
        if !bridge.entity_exists(&id) {
            continue;
        }
        let snapshot = bridge.get_entity(&id)?;
        ctx.entities.insert(id.clone(), snapshot.clone());
        let names: Vec<String> = ctx
            .params
            .iter()
            .filter(|(name, value)| {
                ctx.extras.entity_parameter_names.contains(*name)
                    && matches!(value, Value::Str(v) if **v == *id)
                    && ctx.vars.contains_key(*name)
            })
            .map(|(name, _)| name.clone())
            .collect();
        for name in names {
            ctx.vars.insert(name, snapshot.clone());
        }
    }
    refresh_participant_snapshots(ctx)
}

/// `_refresh_participant_snapshots(ctx)`.
fn refresh_participant_snapshots(ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let Some(bridge) = ctx.state_bridge.as_deref() else {
        return Ok(());
    };
    let names: Vec<String> = ctx.extras.participant_parameter_names.iter().cloned().collect();
    for name in names {
        let id = match ctx.params.get(&name) {
            None | Some(Value::None) => continue,
            Some(Value::Str(id)) => id.to_string(),
            Some(other) => {
                return Err(ExecError::type_error(format!("unhashable type: '{}'", other.type_name())))
            }
        };
        let snapshot = bridge.get_participant(&id)?;
        ctx.vars.insert(name, snapshot.clone());
        if !bridge.entity_exists(&id) {
            ctx.entities.insert(id, snapshot);
        }
    }
    Ok(())
}

// ── Handlers ──────────────────────────────────────────────────────────────────────────────

fn body_dict<'v>(body: &'v Value, effect_type: &str, message: &str) -> Result<&'v Dict, ExecError> {
    match body {
        Value::Dict(map) => Ok(map),
        _ => Err(ExecError::effect(effect_type, message)),
    }
}

fn field_text(map: &Dict, ctx: &ExecutionContext<'_>) -> String {
    let field = map.get("field").map_or_else(String::new, Value::to_str);
    resolve_template(&field, ctx)
}

/// `_handle_set`: a context variable (`_name`), an entity field, or a combat field.
fn handle_set(body: &Value, ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let map = body_dict(body, "set", "set effect body must be a dict")?;
    let field = field_text(map, ctx);
    if field.is_empty() {
        return Ok(());
    }
    let value = match map.get("value") {
        None | Some(Value::None) => Value::None,
        Some(expr) => resolve_value(expr, ctx)?,
    };
    if field.starts_with('_') {
        ctx.vars.insert(field, value);
        return Ok(());
    }
    let (head, path) = field.split_once('.').map_or((field.as_str(), ""), |(a, b)| (a, b));
    let entity_id = resolve_entity_ref(head, ctx);
    let exists = entity_id
        .as_deref()
        .is_some_and(|id| ctx.state_bridge.as_deref().is_some_and(|b| b.entity_exists(id)));
    if exists {
        let bridge = ctx.state_bridge.as_deref_mut().expect("checked above");
        bridge.write_field(entity_id.as_deref().unwrap(), path, value)?;
        refresh_entity_snapshots(ctx)
    } else {
        ctx.set_combat(path, value);
        Ok(())
    }
}

/// `_handle_modify_field` (increment and decrement).
fn handle_modify_field(
    body: &Value,
    ctx: &mut ExecutionContext<'_>,
    multiplier: i64,
) -> Result<(), ExecError> {
    let map = body_dict(body, "modify_field", "body must be a dict")?;
    let field = field_text(map, ctx);
    let amount = resolve_int(map.get("amount").unwrap_or(&Value::Int(1)), ctx)?;
    let amount = amount
        .checked_mul(multiplier)
        .ok_or_else(|| ExecError::value_error(sylvia_base::pystr::INT_RANGE_MESSAGE))?;
    if field.is_empty() {
        return Ok(());
    }
    let (head, path) = field.split_once('.').map_or((field.as_str(), ""), |(a, b)| (a, b));
    let entity_id = resolve_entity_ref(head, ctx);
    let exists = entity_id
        .as_deref()
        .is_some_and(|id| ctx.state_bridge.as_deref().is_some_and(|b| b.entity_exists(id)));
    if exists {
        let bridge = ctx.state_bridge.as_deref_mut().expect("checked above");
        bridge.modify_field(entity_id.as_deref().unwrap(), path, amount)?;
        refresh_entity_snapshots(ctx)?;
    }
    Ok(())
}

/// `_handle_require`: refuse the mechanic when the condition is false.
fn handle_require(body: &Value, ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let condition = match body {
        Value::Dict(map) => match map.get("condition") {
            Some(Value::Str(text)) => text.to_string(),
            _ => return Err(ExecError::effect("require", "A condition is required")),
        },
        _ => return Err(ExecError::effect("require", "A condition is required")),
    };
    if !ctx.with_eval(|eval| crate::expr::evaluate_condition(&condition, eval))? {
        let Value::Dict(map) = body else { unreachable!() };
        let message = map.get("error").map_or_else(|| "Requirement not met".to_owned(), Value::to_str);
        return Err(ExecError::precondition(condition, message));
    }
    Ok(())
}

/// `_handle_conditional`: run `on_true` or `on_false`.
fn handle_conditional(
    body: &Value,
    ctx: &mut ExecutionContext<'_>,
    host: &dyn ToolHost,
) -> Result<(), ExecError> {
    let map = body_dict(body, "condition", "body must be a dict")?;
    let expression = map.get("condition").map_or_else(|| "true".to_owned(), Value::to_str);
    let passed = match ctx.with_eval(|eval| crate::expr::evaluate_condition(&expression, eval)) {
        Ok(passed) => passed,
        Err(EvalError::Expression(error)) => return Err(ExecError::effect("condition", error.message)),
        Err(error) => return Err(error.into()),
    };
    let branch = if passed { "on_true" } else { "on_false" };
    let effects = map.get(branch).cloned().unwrap_or_else(|| Value::list(Vec::new()));
    for effect in iterate(&effects)? {
        execute_effect(&effect, ctx, host)?;
    }
    Ok(())
}

/// Append to a context list variable as `ctx.vars.setdefault(name, []).append(item)` does, in
/// place: every alias of that list sees the new item.
pub(crate) fn append_var(ctx: &mut ExecutionContext<'_>, name: &str, item: Value) -> Result<(), ExecError> {
    let slot = ctx.vars.entry(name.to_owned()).or_insert_with(|| Value::list(Vec::new()));
    match slot.clone() {
        Value::List(items) => {
            let mut grown = (*items).clone();
            grown.push(item);
            ctx.replace_list(&items, grown);
            Ok(())
        }
        other => Err(ExecError::Python {
            class: "AttributeError",
            message: format!("'{}' object has no attribute 'append'", other.type_name()),
        }),
    }
}

/// `_handle_log`: record a log entry.
fn handle_log(body: &Value, ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let Value::Dict(map) = body else {
        return Ok(());
    };
    let message = map.get("message").map_or_else(String::new, Value::to_str);
    let level = map.get("level").cloned().unwrap_or_else(|| Value::str("combat"));
    let resolved = resolve_template(&message, ctx);
    let mut entry = Dict::new();
    entry.insert("message".into(), Value::str(resolved));
    entry.insert("level".into(), level);
    append_var(ctx, "_logs", Value::dict(entry))
}

/// `_handle_narrate`: emit a narrative directive.
fn handle_narrate(body: &Value, effect: &Dict, ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let (text, style, priority) = match body {
        Value::Str(_) => (
            body.clone(),
            effect.get("style").cloned().unwrap_or_else(|| Value::str("combat")),
            effect.get("priority").cloned().unwrap_or_else(|| Value::str("normal")),
        ),
        Value::Dict(map) => (
            map.get("text").or_else(|| map.get("narrate")).cloned().unwrap_or_else(|| Value::str("")),
            map.get("style").cloned().unwrap_or_else(|| Value::str("combat")),
            map.get("priority").cloned().unwrap_or_else(|| Value::str("normal")),
        ),
        _ => return Err(ExecError::effect("narrate", "Invalid narrate format")),
    };
    let text = resolve_template(&text.to_str(), ctx);
    let tool = if ctx.current_tool.is_empty() { "unknown".to_owned() } else { ctx.current_tool.clone() };
    let mut directive = Dict::new();
    directive.insert("text".into(), Value::str(text));
    directive.insert("style".into(), Value::str(style.to_str()));
    directive.insert("priority".into(), Value::str(priority.to_str()));
    directive.insert("source".into(), Value::str(format!("tool:{tool}")));
    append_var(ctx, "_narrative_directives", Value::dict(directive))
}

/// `_handle_publish`: the public result fields.
fn handle_publish(body: &Value, ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let fields = match body {
        Value::Dict(map) if map.len() == 1 => match map.get("fields") {
            Some(fields @ Value::Dict(_)) => fields,
            _ => return Err(ExecError::effect("publish", "Published fields must be an object")),
        },
        _ => return Err(ExecError::effect("publish", "Published fields must be an object")),
    };
    if matches!(fields, Value::Dict(map) if map.len() > 32) {
        return Err(ExecError::effect("publish", "Too many published fields"));
    }
    ctx.extras.published_result = Some(resolve_value(fields, ctx)?);
    Ok(())
}

/// `_handle_create_entity`.
fn handle_create_entity(body: &Value, ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let map = match (body, ctx.state_bridge.is_some()) {
        (Value::Dict(map), true) => map,
        _ => return Err(ExecError::effect("create_entity", "Entity creation is unavailable")),
    };
    let id = resolve_value(map.get("id").unwrap_or(&Value::None), ctx)?;
    let value = resolve_value(map.get("value").unwrap_or(&Value::None), ctx)?;
    let bridge = ctx.state_bridge.as_deref_mut().expect("checked above");
    bridge.create_entity(&id, &value)?;
    let Value::Str(id) = id else { unreachable!("create_entity refuses a non-text identity") };
    let snapshot = bridge.get_entity(&id)?;
    ctx.entities.insert(id.to_string(), snapshot);
    Ok(())
}

/// `_handle_set_participant`.
fn handle_set_participant(body: &Value, ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let map = match (body, ctx.state_bridge.is_some()) {
        (Value::Dict(map), true) => map,
        _ => return Err(ExecError::effect("set_participant", "Participant writes are unavailable")),
    };
    let participant = resolve_value(map.get("participant").unwrap_or(&Value::None), ctx)?;
    let field = field_text(map, ctx);
    let Value::Str(participant) = participant else {
        return Err(ExecError::effect("set_participant", "Participant identity and field are required"));
    };
    if field.is_empty() {
        return Err(ExecError::effect("set_participant", "Participant identity and field are required"));
    }
    let value = resolve_value(map.get("value").unwrap_or(&Value::None), ctx)?;
    ctx.state_bridge.as_deref_mut().expect("checked above").write_participant_field(
        &participant,
        &field,
        value,
    )?;
    refresh_participant_snapshots(ctx)
}

/// `_handle_nested_call`: run another tool under the same budget, then optionally export its
/// private context and store its result.
fn handle_nested_call(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
    host: &dyn ToolHost,
) -> Result<(), ExecError> {
    let empty = Value::dict(Dict::new());
    let (tool_name, params) = match body {
        Value::Str(name) => (name.to_string(), effect.get("params").cloned().unwrap_or(empty)),
        Value::Dict(map) => {
            let raw = map.get("call").or_else(|| map.get("name")).cloned().unwrap_or_else(|| Value::str(""));
            let Value::Str(name) = raw else {
                return Err(ExecError::effect("call", "Nested tool name must be text"));
            };
            (name.to_string(), map.get("params").cloned().unwrap_or(empty))
        }
        _ => return Err(ExecError::effect("call", "Invalid call effect format")),
    };
    let Value::Dict(params) = params else {
        return Err(ExecError::effect("call", "Nested tool parameters must be an object"));
    };
    if tool_name.is_empty() {
        return Err(ExecError::effect("call", "Nested tool name is required"));
    }
    let resolved = resolve_params(&params, ctx);
    let resolved = host.nested_parameters(&tool_name, resolved, ctx.state_bridge.as_deref())?;
    let store_as = effect.get("store_as").cloned().unwrap_or(Value::None);
    ctx.budget.enter_call().map_err(|error| ExecError::effect("call", error.message))?;
    let combat = match &ctx.combat {
        Combat::Bridge => Combat::Bridge,
        Combat::Own(map) => Combat::Own(map.clone()),
    };
    let budget = ctx.budget;
    let choice = ctx.choice;
    let result = execute_tool(
        &tool_name,
        &resolved,
        host,
        ctx.state_bridge.as_deref_mut(),
        combat,
        Some(budget),
        &mut *ctx.dice,
        choice,
    );
    budget.leave_call().map_err(ExecError::from)?;
    let result = result?;
    if !result.success {
        let kind = result.failure_kind.as_deref();
        if matches!(kind, Some("precondition" | "parameter_validation"))
            && result.error.as_deref().is_some_and(|e| !e.is_empty())
        {
            return Err(ExecError::precondition(tool_name, result.error.unwrap()));
        }
        let message = result
            .error
            .filter(|e| !e.is_empty())
            .unwrap_or_else(|| format!("Nested tool '{tool_name}' failed"));
        return Err(ExecError::effect("call", message));
    }
    refresh_entity_snapshots(ctx)?;
    if let Some(export) = effect.get("export_context").filter(|value| **value != Value::None) {
        let all = matches!(export, Value::Str(text) if &**text == "*");
        if !all && !matches!(export, Value::Dict(_)) {
            return Err(ExecError::effect("call", "Nested context exports must be an object or '*'"));
        }
        if all {
            for (name, value) in &result.context {
                if name.starts_with('_') {
                    ctx.vars.insert(name.clone(), super::context::deep_copy(value));
                }
            }
        } else if let Value::Dict(pairs) = export {
            for (destination, source) in pairs.iter() {
                let Value::Str(source) = source else {
                    return Err(ExecError::effect(
                        "call",
                        "Nested context exports require private context-variable names",
                    ));
                };
                if !destination.starts_with('_') || !source.starts_with('_') {
                    return Err(ExecError::effect(
                        "call",
                        "Nested context exports require private context-variable names",
                    ));
                }
                let Some(value) = result.context.get(&**source) else {
                    return Err(ExecError::effect(
                        "call",
                        format!("Nested tool context does not contain '{source}'"),
                    ));
                };
                ctx.vars.insert(destination.clone(), super::context::deep_copy(value));
            }
        }
    }
    if store_as.truthy() {
        ctx.vars.insert(store_as.to_str(), result.to_value());
    }
    Ok(())
}
