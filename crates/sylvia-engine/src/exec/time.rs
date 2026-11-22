//! `runtime/_effect_executor.py`, the handlers for procedures, time, flags, initiative, scenes,
//! deferral and clocks, as the live package path runs them: no clock manager, deferred queue,
//! flag store, initiative-trigger manager or scene manager is ever present there (those modules
//! are retired), so each handler takes Python's "manager absent" branch.

use super::context::ExecutionContext;
use super::effects::{resolve_value, to_int};
use super::error::ExecError;
use super::handlers::{resolve_collection, sort_by_key};
use super::procedures::{trigger_procedure, TriggerCall};
use super::template::{resolve_params, resolve_template};
use super::tool::ToolHost;
use crate::expr::value::{py_eq, Dict, Value};
use std::sync::Arc;

fn get(map: &Dict, key: &str, default: Value) -> Value {
    map.get(key).cloned().unwrap_or(default)
}

fn text(value: &str) -> Value {
    Value::str(value)
}

fn body_dict<'v>(body: &'v Value, effect_type: &str, message: &str) -> Result<&'v Dict, ExecError> {
    match body {
        Value::Dict(map) => Ok(map),
        _ => Err(ExecError::effect(effect_type, message)),
    }
}

/// `_handle_trigger_procedure`: run the named trigger's procedures.
pub(crate) fn handle_trigger(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
    host: &dyn ToolHost,
) -> Result<(), ExecError> {
    let (name, context) = match body {
        Value::Str(_) => (body.clone(), get(effect, "context", Value::dict(Dict::new()))),
        Value::Dict(map) => (
            map.get("name").or_else(|| map.get("trigger")).cloned().unwrap_or_else(|| text("")),
            get(map, "context", Value::dict(Dict::new())),
        ),
        _ => return Err(ExecError::effect("trigger", "Trigger effect format is invalid")),
    };
    let Value::Str(name) = name.clone().filter_nonempty() else {
        return Err(ExecError::effect("trigger", "Trigger identity is required"));
    };
    let Value::Dict(context) = context else {
        return Err(ExecError::effect("trigger", "Trigger context must be an object"));
    };
    let Some(executor) = ctx.procedure_executor.clone() else {
        return Err(ExecError::effect("trigger", "Procedure executor is unavailable"));
    };
    let resolved = resolve_params(&context, ctx);
    ctx.budget.enter_call().map_err(|error| ExecError::effect("trigger", error.message))?;
    let budget = ctx.budget;
    let result = trigger_procedure(
        &name,
        &resolved,
        TriggerCall {
            host,
            bridge: ctx.state_bridge.as_deref_mut(),
            combat: executor.combat.clone(),
            budget,
            dice: &mut *ctx.dice,
            depth: executor.depth,
        },
    );
    budget.leave_call().map_err(ExecError::from)?;
    let result = result?;
    if !result.success {
        let message = result
            .error
            .clone()
            .filter(|e| !e.is_empty())
            .unwrap_or_else(|| format!("Procedure trigger '{name}' failed"));
        return Err(ExecError::effect("trigger", message));
    }
    if let Some(Value::Str(store_as)) = effect.get("store_as") {
        if !store_as.is_empty() {
            ctx.vars.insert(store_as.to_string(), result.to_value());
        }
    }
    Ok(())
}

trait NonEmpty {
    fn filter_nonempty(self) -> Value;
}

impl NonEmpty for Value {
    /// Text that is non-empty, else `None` (`not isinstance(name, str) or not name`).
    fn filter_nonempty(self) -> Value {
        match &self {
            Value::Str(text) if !text.is_empty() => self,
            _ => Value::None,
        }
    }
}

/// `_handle_set_flag` without a flag store: the flag lives in the context only.
pub(crate) fn handle_set_flag(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let (name, value) = match body {
        Value::Str(name) => (name.to_string(), get(effect, "value", Value::Bool(true))),
        Value::Dict(map) => (
            map.get("set_flag").or_else(|| map.get("flag")).cloned().unwrap_or_else(|| text("")).to_str(),
            get(map, "value", Value::Bool(true)),
        ),
        _ => return Err(ExecError::effect("set_flag", "Invalid set_flag format")),
    };
    if !name.is_empty() {
        ctx.vars.insert(format!("_flag_{name}"), value);
    }
    Ok(())
}

const TIME_UNITS: [(&str, i64); 12] = [
    ("round", 1),
    ("rounds", 1),
    ("minute", 10),
    ("minutes", 10),
    ("hour", 600),
    ("hours", 600),
    ("day", 14400),
    ("days", 14400),
    ("month", 432_000),
    ("months", 432_000),
    ("year", 5_184_000),
    ("years", 5_184_000),
];

/// `parse_duration(duration_str)`: `^\s*(\d+(?:\.\d+)?)\s+(\w+)\s*$` with Python's Unicode
/// classes (`$` also matches before a final newline, which `\s*` covers), the amount read by
/// `float()`, the rounds `int(amount * multiplier)`.
fn parse_duration(duration: &str) -> Result<i64, ExecError> {
    let invalid = || {
        ExecError::value_error(format!(
            "Cannot parse duration: {}. Expected format: '<number> <unit>' (e.g., '1 round', '10 minutes').",
            Value::str(duration).repr()
        ))
    };
    let is_digit = |c: char| sylvia_base::pystr::decimal(c).is_some();
    let is_word = |c: char| c == '_' || crate::ruleset::pydata::is_alnum(c);
    let body = duration.trim_start_matches(sylvia_base::pystr::is_space);
    let whole = body.find(|c: char| !is_digit(c)).unwrap_or(body.len());
    if whole == 0 {
        return Err(invalid());
    }
    let mut number_end = whole;
    if let Some(after_dot) = body[whole..].strip_prefix('.') {
        let fraction = after_dot.find(|c: char| !is_digit(c)).unwrap_or(after_dot.len());
        if fraction > 0 {
            number_end = whole + 1 + fraction;
        }
    }
    let rest = &body[number_end..];
    let unit_start = rest.trim_start_matches(sylvia_base::pystr::is_space);
    if unit_start.len() == rest.len() {
        return Err(invalid());
    }
    let unit_end = unit_start.find(|c: char| !is_word(c)).unwrap_or(unit_start.len());
    if unit_end == 0 || !unit_start[unit_end..].chars().all(sylvia_base::pystr::is_space) {
        return Err(invalid());
    }
    let unit = &unit_start[..unit_end];
    let amount = sylvia_base::pystr::parse_float(&body[..number_end]).map_err(|_| invalid())?;
    let lowered = unit.to_lowercase();
    let Some((_, multiplier)) = TIME_UNITS.iter().find(|(name, _)| *name == lowered) else {
        let mut names: Vec<&str> = TIME_UNITS.iter().map(|(name, _)| *name).collect();
        names.sort_unstable();
        let listed = names.iter().map(|name| format!("'{name}'")).collect::<Vec<_>>().join(", ");
        return Err(ExecError::value_error(format!(
            "Unknown time unit: {}. Valid units: [{listed}]",
            Value::str(unit).repr()
        )));
    };
    super::error::float_to_int(amount * *multiplier as f64)
}

/// `_handle_advance_time`: the combat round advances; the package bridge keeps no campaign
/// clock (its missing `_ensure_campaign_entity` is swallowed) and `on_time_advanced` cannot be
/// called with the package's executor (a swallowed `TypeError`), so nothing else changes.
pub(crate) fn handle_advance_time(body: &Value, ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let duration = match body {
        Value::Str(text) => text.to_string(),
        Value::Dict(map) => map
            .get("advance_time")
            .or_else(|| map.get("duration"))
            .cloned()
            .unwrap_or_else(|| text("1 round"))
            .to_str(),
        _ => return Err(ExecError::effect("advance_time", "Invalid advance_time format")),
    };
    let rounds = parse_duration(&duration)?;
    let current = to_int(&ctx.combat_state().get("round").cloned().unwrap_or(Value::Int(0)))?;
    let round = current
        .checked_add(rounds)
        .ok_or_else(|| ExecError::value_error(sylvia_base::pystr::INT_RANGE_MESSAGE))?;
    ctx.set_combat("round", Value::Int(round));
    Ok(())
}

fn nested_field(entity: &Value, path: &str) -> Value {
    let mut current = entity.clone();
    for segment in path.split('.') {
        current = match &current {
            Value::Dict(map) => map.get(segment).cloned().unwrap_or(Value::None),
            _ => return Value::None,
        };
    }
    current
}

/// `_handle_initiative`: roll each combatant's initiative and set the order.
pub(crate) fn handle_initiative(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let map = body_dict(body, "initiative", "body must be a dict")?;
    let combatants = get(map, "combatants", text("combatants"));
    let expression =
        map.get("expression").or_else(|| map.get("roll")).cloned().unwrap_or_else(|| text("d20"));
    let store_as = get(effect, "store_as", text("_initiative_order")).to_str();
    let mut fields = Vec::new();
    for option in ["modifier_field", "required_truthy_field", "label_field"] {
        let value = get(map, option, Value::None);
        match &value {
            Value::None => fields.push(None),
            Value::Str(name) if !sylvia_base::pystr::strip(name).is_empty() => {
                fields.push(Some(name.to_string()))
            }
            _ => return Err(ExecError::effect("initiative", format!("{option} must be a field name"))),
        }
    }
    let mut bounds = Vec::new();
    for option in ["modifier_minimum", "modifier_maximum"] {
        match get(map, option, Value::None) {
            Value::None => bounds.push(None),
            Value::Int(n) => bounds.push(Some(n)),
            _ => return Err(ExecError::effect("initiative", format!("{option} must be an integer"))),
        }
    }
    let (modifier_field, required_field, label_field) =
        (fields[0].clone(), fields[1].clone(), fields[2].clone());
    let (minimum, maximum) = (bounds[0], bounds[1]);
    if let (Some(minimum), Some(maximum)) = (minimum, maximum) {
        if minimum > maximum {
            return Err(ExecError::effect("initiative", "modifier_minimum cannot exceed modifier_maximum"));
        }
    }
    let Some((list, _)) = resolve_collection(&combatants, ctx)?.filter(|(list, _)| !list.is_empty()) else {
        ctx.vars.insert(store_as, Value::list(Vec::new()));
        return Ok(());
    };
    let mut ids: Vec<String> = Vec::new();
    for item in list.iter() {
        match item {
            Value::Str(id) => ids.push(id.to_string()),
            Value::Dict(entry) => {
                if let Some(Value::Str(id)) = entry.get("entity_id").or_else(|| entry.get("id")) {
                    if !id.is_empty() {
                        ids.push(id.to_string());
                    }
                }
            }
            _ => {}
        }
    }
    let mut results = Vec::new();
    for id in ids {
        if !ctx.entities.contains_key(&id) {
            if let Some(bridge) = ctx.state_bridge.as_deref() {
                if let Ok(snapshot) = bridge.get_entity(&id) {
                    ctx.entities.insert(id.clone(), snapshot);
                }
            }
        }
        let entity = ctx.entities.get(&id).cloned().unwrap_or_else(|| Value::dict(Dict::new()));
        let id_repr = Value::str(id.as_str()).repr();
        if let Some(field) = &required_field {
            if !nested_field(&entity, field).truthy() {
                return Err(ExecError::value_error(format!(
                    "Initiative combatant {id_repr} must have a truthy {} field",
                    Value::str(field.as_str()).repr()
                )));
            }
        }
        let mut modifier = 0;
        if let Some(field) = &modifier_field {
            let field_repr = Value::str(field.as_str()).repr();
            modifier = match nested_field(&entity, field) {
                Value::Int(n) => n,
                Value::Float(x) => super::error::float_to_int(x)?,
                _ => {
                    return Err(ExecError::value_error(format!(
                        "Initiative combatant {id_repr} requires a numeric {field_repr} field"
                    )))
                }
            };
            if minimum.is_some_and(|minimum| modifier < minimum) {
                return Err(ExecError::value_error(format!(
                    "Initiative combatant {id_repr} has {field_repr} below {}",
                    minimum.unwrap()
                )));
            }
            if maximum.is_some_and(|maximum| modifier > maximum) {
                return Err(ExecError::value_error(format!(
                    "Initiative combatant {id_repr} has {field_repr} above {}",
                    maximum.unwrap()
                )));
            }
        }
        let (value, roll) = crate::dice_expr::roll_initiative_expr(ctx.dice, &expression.to_str())?;
        let roll_modifier = roll.as_ref().and_then(|roll| roll["modifier"].as_i64()).unwrap_or(0);
        let mut entry = Dict::new();
        entry.insert("entity_id".into(), Value::str(id.as_str()));
        let overflow = || ExecError::value_error(sylvia_base::pystr::INT_RANGE_MESSAGE);
        entry.insert(
            "initiative_result".into(),
            Value::Int(value.checked_add(modifier).ok_or_else(overflow)?),
        );
        entry.insert("roll".into(), roll.as_ref().map_or(Value::None, Value::from_json));
        entry
            .insert("modifier".into(), Value::Int(modifier.checked_add(roll_modifier).ok_or_else(overflow)?));
        if let Some(field) = &label_field {
            match nested_field(&entity, field) {
                Value::Str(label)
                    if !sylvia_base::pystr::strip(&label).is_empty() && label.chars().count() <= 200 =>
                {
                    entry.insert("label".into(), Value::Str(label));
                }
                _ => {
                    return Err(ExecError::value_error(
                        "Initiative requires the declared entity display label",
                    ))
                }
            }
        }
        results.push(Value::dict(entry));
    }
    let keys: Vec<Value> = results
        .iter()
        .map(|entry| match entry {
            Value::Dict(map) => map["initiative_result"].clone(),
            _ => unreachable!(),
        })
        .collect();
    sort_by_key(&mut results, keys, true)?;
    let results = Value::list(results);
    ctx.vars.insert(store_as, results.clone());
    ctx.set_combat("order", results);
    ctx.set_combat("turn_index", Value::Int(-1));
    Ok(())
}

/// `_initiative.advance_turn`: move the combat state's turn on, wrapping into a new round.
fn advance_turn(ctx: &mut ExecutionContext<'_>) -> Result<Value, ExecError> {
    let combat = ctx.combat_state();
    let order = match combat.get("order") {
        None => return Ok(Value::None),
        Some(order) if !order.truthy() => return Ok(Value::None),
        Some(order) => order.clone(),
    };
    let items = match &order {
        Value::List(items) | Value::Tuple(items) => items.clone(),
        other => {
            return Err(ExecError::type_error(format!("object of type '{}' has no len()", other.type_name())))
        }
    };
    let index = combat.get("turn_index").cloned().unwrap_or(Value::Int(-1));
    let last = Value::Int(items.len() as i64 - 1);
    let at_end = match (&index, &last) {
        (Value::Int(n), Value::Int(l)) => n >= l,
        (Value::Bool(flag), Value::Int(l)) => i64::from(*flag) >= *l,
        (Value::Float(n), Value::Int(l)) => *n >= *l as f64,
        (other, _) => {
            return Err(ExecError::type_error(format!(
                "'>=' not supported between instances of '{}' and 'int'",
                other.type_name()
            )))
        }
    };
    let index = if at_end {
        let round = combat.get("round").cloned().unwrap_or(Value::Int(0));
        ctx.set_combat("round", crate::expr::ops::add(&round, &Value::Int(1))?);
        ctx.set_combat("turn_index", Value::Int(0));
        ctx.set_combat("_round_advanced", Value::Bool(true));
        Value::Int(0)
    } else {
        let next = crate::expr::ops::add(&index, &Value::Int(1))?;
        ctx.set_combat("turn_index", next.clone());
        ctx.set_combat("_round_advanced", Value::Bool(false));
        next
    };
    let position = match index {
        Value::Int(n) => n,
        Value::Bool(flag) => i64::from(flag),
        other => {
            return Err(ExecError::type_error(format!(
                "list indices must be integers or slices, not {}",
                other.type_name()
            )))
        }
    };
    let length = items.len() as i64;
    let position = if position < 0 { position + length } else { position };
    let Some(current) = usize::try_from(position).ok().and_then(|p| items.get(p)) else {
        return Err(ExecError::Python { class: "IndexError", message: "list index out of range".into() });
    };
    let current = match current {
        Value::Dict(entry) => entry
            .get("entity_id")
            .cloned()
            .unwrap_or_else(|| entry.get("id").cloned().unwrap_or_else(|| text(""))),
        other => Value::str(other.to_str()),
    };
    ctx.set_combat("current_actor", current.clone());
    Ok(current)
}

/// `_handle_advance_initiative`.
pub(crate) fn handle_advance_initiative(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let map = body_dict(body, "advance_initiative", "body must be a dict")?;
    let skip_field = match get(map, "skip_field", Value::None) {
        Value::None => None,
        Value::Str(field) if !sylvia_base::pystr::strip(&field).is_empty() => Some(field.to_string()),
        _ => return Err(ExecError::effect("advance_initiative", "skip_field must be a field name")),
    };
    let skip_equals = resolve_value(&get(map, "skip_equals", Value::None), ctx)?;
    let combat = ctx.combat_state();
    let length = match combat.get("order").cloned().unwrap_or_else(|| Value::list(Vec::new())) {
        Value::List(items) => items.len(),
        _ => return Err(ExecError::effect("advance_initiative", "combat order must be a list")),
    };
    let mut selected = Value::None;
    for _ in 0..length {
        let candidate = advance_turn(ctx)?;
        if candidate == Value::None {
            break;
        }
        let Some(field) = &skip_field else {
            selected = candidate;
            break;
        };
        let key = match &candidate {
            Value::Str(id) => id.to_string(),
            Value::List(_) | Value::Dict(_) => {
                return Err(ExecError::type_error(format!("unhashable type: '{}'", candidate.type_name())))
            }
            other => other.to_str(),
        };
        let mut entity = ctx.entities.get(&key).cloned();
        if entity.is_none() {
            if let (Value::Str(id), Some(bridge)) = (&candidate, ctx.state_bridge.as_deref()) {
                if let Ok(snapshot) = bridge.get_entity(id) {
                    ctx.entities.insert(id.to_string(), snapshot.clone());
                    entity = Some(snapshot);
                }
            }
        }
        let value = nested_field(&entity.unwrap_or(Value::None), field);
        if !py_eq(&value, &skip_equals) {
            selected = candidate;
            break;
        }
    }
    if selected == Value::None {
        ctx.set_combat("current_actor", Value::None);
    }
    ctx.vars.insert(get(effect, "store_as", text("_current_actor")).to_str(), selected);
    Ok(())
}

/// `_handle_transition_scene` without a scene manager: the scene lives in the context only.
pub(crate) fn handle_transition_scene(body: &Value, ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let scene = match body {
        Value::Str(text) => text.to_string(),
        Value::Dict(map) => map
            .get("transition_scene")
            .or_else(|| map.get("scene"))
            .cloned()
            .unwrap_or_else(|| text(""))
            .to_str(),
        _ => return Err(ExecError::effect("transition_scene", "Invalid transition_scene format")),
    };
    if !scene.is_empty() {
        ctx.vars.insert("_current_scene".into(), Value::str(scene.as_str()));
        ctx.vars.insert("_current_scene_name".into(), Value::str(scene));
    }
    Ok(())
}

/// Append to a context list in place (`ctx.vars.setdefault(name, []).append(item)`).
fn append(ctx: &mut ExecutionContext<'_>, name: &str, item: Value) -> Result<(), ExecError> {
    super::effects::append_var(ctx, name, item)
}

/// `str(uuid.uuid4())[:8]`: eight random lowercase hex digits.
fn short_uuid() -> String {
    let mut bytes = [0u8; 4];
    getrandom::getrandom(&mut bytes).expect("the operating system supplies random bytes");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// `_handle_defer` without a deferred queue: the call is recorded in the context.
pub(crate) fn handle_defer(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let map = body_dict(body, "defer", "defer effect body must be a dict")?;
    let call = map.get("call").or_else(|| map.get("tool")).cloned().unwrap_or_else(|| text(""));
    if !call.truthy() {
        return Err(ExecError::effect("defer", "defer requires a 'call' field"));
    }
    let params = match get(map, "params", Value::dict(Dict::new())) {
        Value::Dict(params) => Value::dict((*params).clone()),
        other => {
            return Err(ExecError::type_error(format!("'{}' object is not iterable", other.type_name())))
        }
    };
    let call_id = short_uuid();
    // `ctx.vars["_deferred_calls"] = ctx.vars.get("_deferred_calls", [])`.
    if !ctx.vars.contains_key("_deferred_calls") {
        ctx.vars.insert("_deferred_calls".into(), Value::list(Vec::new()));
    }
    let mut entry = Dict::new();
    entry.insert("call_id".into(), Value::str(call_id.as_str()));
    entry.insert("tool_name".into(), call);
    entry.insert("params".into(), params);
    entry.insert("reason".into(), get(map, "reason", text("end_of_turn")));
    entry.insert("priority".into(), get(map, "priority", Value::Int(100)));
    append(ctx, "_deferred_calls", Value::dict(entry))?;
    ctx.vars.insert(get(effect, "store_as", text("_deferred_id")).to_str(), Value::str(call_id));
    Ok(())
}

/// `int(value)` for these handlers' counts, with Python's fallback on `ValueError`/`TypeError`.
fn int_or(value: &Value, fallback: i64) -> Result<i64, ExecError> {
    match to_int(value) {
        Ok(n) => Ok(n),
        Err(ExecError::Python { class: "ValueError" | "TypeError", message })
            if message != sylvia_base::pystr::INT_RANGE_MESSAGE =>
        {
            Ok(fallback)
        }
        Err(error) => Err(error),
    }
}

/// `_handle_register_initiative_trigger` without its manager: recorded in the context.
pub(crate) fn handle_register_initiative_trigger(
    body: &Value,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let map = body_dict(body, "register_initiative_trigger", "body must be a dict")?;
    let trigger_id = map.get("trigger_id").or_else(|| map.get("id")).cloned().unwrap_or_else(|| text(""));
    if !trigger_id.truthy() {
        return Err(ExecError::effect("register_initiative_trigger", "trigger_id is required"));
    }
    let procedure =
        map.get("procedure").or_else(|| map.get("procedure_name")).cloned().unwrap_or_else(|| text(""));
    if !procedure.truthy() {
        return Err(ExecError::effect("register_initiative_trigger", "procedure is required"));
    }
    let count = int_or(&get(map, "count", Value::Int(0)), 0)?;
    let mut entry = Dict::new();
    entry.insert("trigger_id".into(), Value::str(trigger_id.to_str()));
    entry.insert("count".into(), Value::Int(count));
    entry.insert("procedure_name".into(), Value::str(procedure.to_str()));
    entry.insert("source".into(), Value::str(get(map, "source", text("")).to_str()));
    entry.insert("repeats".into(), Value::Bool(get(map, "repeats", Value::Bool(true)).truthy()));
    append(ctx, "_initiative_triggers", Value::dict(entry))
}

/// `_handle_clock_create` without a clock manager: the clock lives in the context.
pub(crate) fn handle_clock_create(body: &Value, ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let map = body_dict(body, "clock_create", "body must be a dict")?;
    let clock_id = map.get("clock_id").or_else(|| map.get("id")).cloned().unwrap_or_else(|| text(""));
    if !clock_id.truthy() {
        return Err(ExecError::effect("clock_create", "clock_id is required"));
    }
    let name = get(map, "name", clock_id.clone());
    let max_segments = int_or(&get(map, "max_segments", Value::Int(4)), 4)?;
    let thresholds_raw = match get(map, "thresholds", Value::dict(Dict::new())) {
        value if !value.truthy() => Dict::new(),
        Value::Dict(map) => (*map).clone(),
        other => {
            return Err(ExecError::Python {
                class: "AttributeError",
                message: format!("'{}' object has no attribute 'items'", other.type_name()),
            })
        }
    };
    let mut thresholds = Dict::new();
    for (key, value) in &thresholds_raw {
        if let Ok(number) = sylvia_base::pystr::parse_int(key) {
            thresholds.insert(number.to_string(), Value::str(resolve_template(&value.to_str(), ctx)));
        }
    }
    let metadata_raw = match get(map, "metadata", Value::dict(Dict::new())) {
        value if !value.truthy() => Dict::new(),
        Value::Dict(map) => (*map).clone(),
        other => {
            return Err(ExecError::Python {
                class: "AttributeError",
                message: format!("'{}' object has no attribute 'items'", other.type_name()),
            })
        }
    };
    let metadata: Dict = metadata_raw
        .into_iter()
        .map(|(key, value)| {
            let value = match &value {
                Value::Str(text) => Value::str(resolve_template(text, ctx)),
                other => other.clone(),
            };
            (key, value)
        })
        .collect();
    let mut clock = Dict::new();
    clock.insert("clock_id".into(), Value::str(clock_id.to_str()));
    clock.insert("name".into(), Value::str(name.to_str()));
    clock.insert("max_segments".into(), Value::Int(max_segments));
    clock.insert("filled_segments".into(), Value::Int(0));
    clock.insert("thresholds".into(), Value::dict(thresholds));
    clock.insert("metadata".into(), Value::dict(metadata));
    ctx.vars.insert(format!("_clock_{}", clock_id.to_str()), Value::dict(clock));
    Ok(())
}

/// `_handle_clock_tick` without a clock manager: fills the context clock in place.
pub(crate) fn handle_clock_tick(body: &Value, ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let (clock_id, segments) = match body {
        Value::Str(text) => (text.to_string(), 1),
        Value::Dict(map) => (
            map.get("clock_id").or_else(|| map.get("id")).cloned().unwrap_or_else(|| text("")).to_str(),
            int_or(&get(map, "segments", Value::Int(1)), 1)?,
        ),
        _ => return Err(ExecError::effect("clock_tick", "Invalid clock_tick format")),
    };
    if clock_id.is_empty() {
        return Ok(());
    }
    let key = format!("_clock_{clock_id}");
    let clock = match ctx.vars.get(&key) {
        None => Arc::new(Dict::new()),
        Some(Value::Dict(map)) => map.clone(),
        Some(_) => return Ok(()),
    };
    let old = clock.get("filled_segments").cloned().unwrap_or(Value::Int(0));
    let maximum = clock.get("max_segments").cloned().unwrap_or(Value::Int(4));
    let sum = crate::expr::ops::add(&old, &Value::Int(segments))?;
    let filled = if crate::expr::compare::py_order(crate::expr::compare::Order::Lt, &sum, &maximum)? {
        sum
    } else {
        maximum
    };
    let mut updated = (*clock).clone();
    updated.insert("filled_segments".into(), filled);
    let updated = ctx.replace_dict(&clock, updated);
    ctx.vars.insert(key, Value::Dict(updated));
    Ok(())
}
