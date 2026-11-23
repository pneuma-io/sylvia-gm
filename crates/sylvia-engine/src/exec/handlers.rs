//! `runtime/_effect_executor.py`, the second batch of handlers: dice and rolls, damage, healing
//! and conditions, saves, rerolls, hunger pools, table lookups, random tables, iteration,
//! sorting, context lists, hidden shuffles and package choices.

use super::context::{ExecutionContext, ListRef};
use super::effects::{
    execute_effect, iterate, refresh_entity_snapshots, resolve_entity_ref, resolve_int, resolve_value, to_int,
};
use super::error::ExecError;
use super::template::resolve_template;
use super::tool::ToolHost;
use crate::dice::RollResult;
use crate::dice_expr::parse_dice_expr;
use crate::expr::compare::{py_order, Order};
use crate::expr::value::{Dict, Value};
use std::sync::Arc;
use sylvia_domain::authority::{MechanicsChoiceOption, MechanicsChoiceRequest};

fn body_dict<'v>(body: &'v Value, effect_type: &str, message: &str) -> Result<&'v Dict, ExecError> {
    match body {
        Value::Dict(map) => Ok(map),
        _ => Err(ExecError::effect(effect_type, message)),
    }
}

fn get(map: &Dict, key: &str, default: Value) -> Value {
    map.get(key).cloned().unwrap_or(default)
}

fn text(value: &str) -> Value {
    Value::str(value)
}

/// `evaluate_expression(str(expr), ctx)`.
fn evaluate(expr: &Value, ctx: &ExecutionContext<'_>) -> Result<Value, ExecError> {
    let source = expr.to_str();
    Ok(ctx.with_eval(|eval| crate::expr::evaluate_expression(&source, eval))?)
}

/// `evaluate_condition(str(expr), ctx)`.
fn condition(expr: &Value, ctx: &ExecutionContext<'_>) -> Result<bool, ExecError> {
    let source = expr.to_str();
    Ok(ctx.with_eval(|eval| crate::expr::evaluate_condition(&source, eval))?)
}

/// `int(mod_val) if mod_val is not None else 0` after evaluating a truthy modifier expression.
fn modifier(map: &Dict, ctx: &ExecutionContext<'_>) -> Result<i64, ExecError> {
    match map.get("modifier") {
        Some(expr) if expr.truthy() => match evaluate(expr, ctx)? {
            Value::None => Ok(0),
            value => to_int(&value),
        },
        _ => Ok(0),
    }
}

/// `_resolve_entity_ref(target_ref, ctx)` for a body's `target`, which Python calls `.strip` on.
fn target(map: &Dict, ctx: &ExecutionContext<'_>, effect_type: &str) -> Result<String, ExecError> {
    let reference = get(map, "target", text(""));
    let Value::Str(reference_text) = &reference else {
        return Err(ExecError::Python {
            class: "AttributeError",
            message: format!("'{}' object has no attribute 'strip'", reference.type_name()),
        });
    };
    resolve_entity_ref(reference_text, ctx)
        .ok_or_else(|| ExecError::effect(effect_type, format!("Unknown target: {}", reference.to_str())))
}

fn store(ctx: &mut ExecutionContext<'_>, name: &Value, value: Value) {
    ctx.vars.insert(name.to_str(), value);
}

/// `_handle_dice`.
pub(crate) fn handle_dice(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let map = body_dict(body, "dice", "dice effect body must be a dict")?;
    let store_as = get(effect, "store_as", text("_roll_result"));
    let expression = resolve_template(&get(map, "expression", text("d20")).to_str(), ctx);
    let modifier = modifier(map, ctx)?;
    let mut advantage = match map.get("advantage") {
        Some(expr) if expr.truthy() => condition(expr, ctx)?,
        _ => false,
    };
    let mut disadvantage = match map.get("disadvantage") {
        Some(expr) if expr.truthy() => condition(expr, ctx)?,
        _ => false,
    };
    if advantage && disadvantage {
        advantage = false;
        disadvantage = false;
    }
    let critical = match map.get("critical") {
        Some(expr) if expr.truthy() => condition(expr, ctx)?,
        _ => false,
    };
    let parts = parse_dice_expr(&expression);
    // `count *= 2`: a count written beyond `i64` already compares above every limit.
    let count = if critical { parts.count.saturating_mul(2) } else { parts.count };
    let explode = match map.get("explode") {
        None | Some(Value::None) => false,
        Some(Value::Bool(flag)) => *flag,
        Some(expr) => condition(expr, ctx)?,
    };
    let explode_on = match map.get("explode_on") {
        None | Some(Value::None) => None,
        Some(raw) => Some(resolve_int(raw, ctx)?),
    };
    let max_explosions = resolve_int(&get(map, "max_explosions", Value::Int(100)), ctx)?;
    let result = if explode {
        ctx.dice.explode(parts.sides, count, modifier, explode_on, max_explosions)?
    } else if advantage {
        ctx.dice.advantage(modifier)?
    } else if disadvantage {
        ctx.dice.disadvantage(modifier)?
    } else {
        ctx.dice.roll(parts.sides, count, modifier)?
    };
    store(ctx, &store_as, Value::from(&result));
    Ok(())
}

/// `_handle_save_throw`: a d20 check against the DC with the target's ability modifier.
pub(crate) fn handle_save_throw(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let map = body_dict(body, "save_throw", "body must be a dict")?;
    let ability = get(map, "ability", text("dex"));
    let store_as = get(effect, "store_as", text("_save_result"));
    let target_id = target(map, ctx, "save_throw")?;
    let dc = resolve_int(&get(map, "dc", text("10")), ctx)?;
    let mut ability_mod = Value::Int(0);
    if ctx.state_bridge.is_some() {
        if let Some(entity) = ctx.entities.get(&target_id) {
            let attribute_error = |value: &Value| ExecError::Python {
                class: "AttributeError",
                message: format!("'{}' object has no attribute 'get'", value.type_name()),
            };
            let Value::Dict(entity) = entity else { return Err(attribute_error(entity)) };
            let abilities = entity.get("abilities").cloned().unwrap_or_else(|| Value::dict(Dict::new()));
            let Value::Dict(abilities) = &abilities else { return Err(attribute_error(&abilities)) };
            if let Value::Dict(entry) =
                abilities.get(&ability.to_str()).cloned().unwrap_or_else(|| Value::dict(Dict::new()))
            {
                ability_mod = entry.get("modifier").cloned().unwrap_or(Value::Int(0));
            }
        }
    }
    let modifier = match ability_mod {
        Value::Int(n) => n,
        Value::Bool(flag) => i64::from(flag),
        other => {
            return Err(ExecError::type_error(format!(
                "unsupported ability modifier: '{}'",
                other.type_name()
            )))
        }
    };
    let check = ctx.dice.check(dc, modifier, false, false)?;
    let success = check.success;
    store(ctx, &store_as, Value::from(&check));
    ctx.vars.insert(format!("{}._save_success", store_as.to_str()), Value::Bool(success));
    Ok(())
}

/// `_handle_apply_damage`.
pub(crate) fn handle_apply_damage(body: &Value, ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let map = body_dict(body, "apply_damage", "body must be a dict")?;
    let target_id = target(map, ctx, "apply_damage")?;
    let amount = resolve_int(&get(map, "amount", text("0")), ctx)?;
    if let Some(bridge) = ctx.state_bridge.as_deref_mut() {
        bridge.take_damage(&target_id, amount)?;
        ctx.vars.insert("_damage_amount".into(), Value::Int(amount));
        refresh_entity_snapshots(ctx)?;
    }
    Ok(())
}

/// `_handle_heal`.
pub(crate) fn handle_heal(body: &Value, ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let map = body_dict(body, "heal", "body must be a dict")?;
    let target_id = target(map, ctx, "heal")?;
    let amount = resolve_int(&get(map, "amount", text("0")), ctx)?;
    if let Some(bridge) = ctx.state_bridge.as_deref_mut() {
        bridge.heal(&target_id, amount)?;
        refresh_entity_snapshots(ctx)?;
    }
    Ok(())
}

/// `_handle_apply_condition` and `_handle_remove_condition`.
pub(crate) fn handle_condition_change(
    body: &Value,
    ctx: &mut ExecutionContext<'_>,
    add: bool,
) -> Result<(), ExecError> {
    let effect_type = if add { "apply_condition" } else { "remove_condition" };
    let map = body_dict(body, effect_type, "body must be a dict")?;
    let condition = resolve_template(&get(map, "condition", text("")).to_str(), ctx);
    let target_id = target(map, ctx, effect_type)?;
    if let Some(bridge) = ctx.state_bridge.as_deref_mut() {
        if add {
            bridge.add_condition(&target_id, &condition)?;
        } else {
            bridge.remove_condition(&target_id, &condition)?;
        }
        refresh_entity_snapshots(ctx)?;
    }
    Ok(())
}

/// `_handle_apply_ability_damage` (no snapshot refresh, as in Python).
pub(crate) fn handle_apply_ability_damage(
    body: &Value,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let map = body_dict(body, "apply_ability_damage", "body must be a dict")?;
    let ability = get(map, "ability", text("str"));
    let target_id = target(map, ctx, "apply_ability_damage")?;
    let amount = resolve_int(&get(map, "amount", text("0")), ctx)?;
    if let Some(bridge) = ctx.state_bridge.as_deref_mut() {
        bridge.damage_ability(&target_id, &ability.to_str(), amount)?;
    }
    Ok(())
}

/// `_handle_count_successes`.
pub(crate) fn handle_count_successes(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let map = body_dict(body, "count_successes", "body must be a dict")?;
    let store_as = get(effect, "store_as", text("_pool_result"));
    let pool = resolve_template(&get(map, "pool", text("1d10")).to_str(), ctx);
    let parts = parse_dice_expr(&pool);
    let threshold = resolve_int(&get(map, "threshold", Value::Int(6)), ctx)?;
    let modifier = modifier(map, ctx)?;
    let result = ctx.dice.count_successes(parts.sides, parts.count, threshold, modifier)?;
    store(ctx, &store_as, Value::from(&result));
    Ok(())
}

/// A context value as the `RollResult` `reroll` needs, rebuilding one from a serialized dict.
fn roll_result(value: &Value, source: &Value) -> Result<RollResult, ExecError> {
    let int = |value: &Value| match value {
        Value::Int(n) => Ok(*n),
        Value::Bool(flag) => Ok(i64::from(*flag)),
        other => Err(ExecError::type_error(format!("unsupported roll field: '{}'", other.type_name()))),
    };
    let field = |name: &str, default: Value| -> Value {
        match value {
            Value::Dict(map) => map.get(name).cloned().unwrap_or(default),
            Value::Object(object) => {
                object.fields.iter().find(|(n, _)| *n == name).map_or(default, |(_, v)| v.clone())
            }
            _ => default,
        }
    };
    match value {
        Value::Dict(_) => {}
        Value::Object(object) if object.class == "RollResult" => {}
        _ => {
            return Err(ExecError::effect(
                "reroll",
                format!("Source '{}' is not a RollResult", source.to_str()),
            ))
        }
    }
    let results = match field("results", Value::list(Vec::new())) {
        Value::List(items) | Value::Tuple(items) => items.iter().map(int).collect::<Result<Vec<_>, _>>()?,
        other => {
            return Err(ExecError::type_error(format!("'{}' object is not iterable", other.type_name())))
        }
    };
    Ok(RollResult {
        sides: int(&field("sides", Value::Int(20)))?,
        count: int(&field("count", Value::Int(1)))?,
        results,
        total: int(&field("total", Value::Int(0)))?,
        modifier: int(&field("modifier", Value::Int(0)))?,
    })
}

/// `_handle_reroll`: reroll a stored result, then run its consequences.
pub(crate) fn handle_reroll(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
    host: &dyn ToolHost,
) -> Result<(), ExecError> {
    let map = body_dict(body, "reroll", "body must be a dict")?;
    let source = get(map, "source", text("_roll_result"));
    let predicate = get(map, "predicate", text("all"));
    let consequence = get(map, "consequence", Value::list(Vec::new()));
    let store_as = get(effect, "store_as", text("_reroll_result"));
    let previous = match ctx.vars.get(&source.to_str()) {
        None | Some(Value::None) => {
            return Err(ExecError::effect("reroll", format!("Source var '{}' not found", source.to_str())));
        }
        Some(value) => value.clone(),
    };
    let previous = roll_result(&previous, &source)?;
    let modifier = modifier(map, ctx)?;
    let threshold = match map.get("threshold") {
        None | Some(Value::None) => None,
        Some(raw) => Some(resolve_int(raw, ctx)?),
    };
    let result = ctx.dice.reroll(&previous, &predicate.to_str(), modifier, threshold)?;
    store(ctx, &store_as, Value::from(&result));
    for item in iterate(&consequence)? {
        execute_effect(&item, ctx, host)?;
    }
    Ok(())
}

/// `_handle_hunger_dice`.
pub(crate) fn handle_hunger_dice(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let map = body_dict(body, "hunger_dice", "body must be a dict")?;
    let store_as = get(effect, "store_as", text("_hunger_result"));
    let pool = resolve_int(&get(map, "pool", text("1")), ctx)?;
    let hunger = resolve_int(&get(map, "hunger", Value::Int(0)), ctx)?;
    let sides = resolve_int(&get(map, "sides", Value::Int(10)), ctx)?;
    let threshold = resolve_int(&get(map, "threshold", Value::Int(6)), ctx)?;
    let modifier = modifier(map, ctx)?;
    let result = ctx.dice.hunger_roll(pool, hunger, sides, threshold, modifier)?;
    store(ctx, &store_as, Value::from(&result));
    Ok(())
}

/// `_resolve_table(path, ctx)`: a `definitions.*` mapping.
fn resolve_table(path: &Value, ctx: &ExecutionContext<'_>) -> Result<Option<Dict>, ExecError> {
    let Value::Str(path) = path else {
        return Err(ExecError::Python {
            class: "AttributeError",
            message: format!("'{}' object has no attribute 'split'", path.type_name()),
        });
    };
    let parts: Vec<&str> = path.split('.').collect();
    if parts[0] != "definitions" {
        return Ok(None);
    }
    let mut current = ctx.definitions.clone();
    for part in &parts[1..] {
        current = match &current {
            Value::Dict(map) => map.get(*part).cloned().unwrap_or(Value::None),
            _ => return Ok(None),
        };
    }
    Ok(match current {
        Value::Dict(map) => Some((*map).clone()),
        _ => None,
    })
}

/// `table.get(key)` for a text-keyed table: only text finds a row; an unhashable key raises.
fn table_get(table: &Dict, key: &Value) -> Result<Value, ExecError> {
    match key {
        Value::Str(text) => Ok(table.get(&**text).cloned().unwrap_or(Value::None)),
        Value::List(_) | Value::Dict(_) => {
            Err(ExecError::type_error(format!("unhashable type: '{}'", key.type_name())))
        }
        _ => Ok(Value::None),
    }
}

/// `_handle_table_lookup`.
pub(crate) fn handle_table_lookup(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let map = body_dict(body, "table_lookup", "body must be a dict")?;
    let table_path = get(map, "table", text(""));
    let key_expr = get(map, "key", text(""));
    let store_as = get(effect, "store_as", text("_table_result"));
    let default = get(map, "default", Value::None);
    let required = get(map, "required", Value::Bool(false));
    let Value::Str(store_name) = &store_as else {
        return Err(ExecError::effect("table_lookup", "store_as must be a private context identifier"));
    };
    if !store_name.starts_with('_') {
        return Err(ExecError::effect("table_lookup", "store_as must be a private context identifier"));
    }
    let Value::Bool(required) = required else {
        return Err(ExecError::effect("table_lookup", "required must be a boolean"));
    };
    if required && default != Value::None {
        return Err(ExecError::effect("table_lookup", "a required lookup cannot declare a default"));
    }
    let Some(table) = resolve_table(&table_path, ctx)? else {
        if required {
            return Err(ExecError::effect(
                "table_lookup",
                format!("required table '{}' does not exist", table_path.to_str()),
            ));
        }
        if default != Value::None {
            store(ctx, &store_as, default);
        }
        return Ok(());
    };
    let mut key = evaluate(&key_expr, ctx)?;
    if let Value::Float(x) = key {
        let whole = super::error::float_to_int(x)?;
        if x == whole as f64 {
            key = Value::Int(whole);
        }
    }
    let mut value = table_get(&table, &key)?;
    if value == Value::None && required {
        return Err(ExecError::effect(
            "table_lookup",
            format!("required key {} does not exist in table '{}'", key.repr(), table_path.to_str()),
        ));
    }
    if value == Value::None && default != Value::None {
        value = default;
    }
    store(ctx, &store_as, value);
    Ok(())
}

/// `_handle_random_table`: a weighted pick, optionally driven by a roll.
pub(crate) fn handle_random_table(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let map = body_dict(body, "random_table", "body must be a dict")?;
    let table_path = get(map, "table", text(""));
    let weight_field = get(map, "weight_field", text("weight")).to_str();
    let store_as = get(effect, "store_as", text("_random_table_result"));
    let default = get(map, "default", Value::None);
    let Some(table) = resolve_table(&table_path, ctx)? else {
        if default != Value::None {
            store(ctx, &store_as, default);
        }
        return Ok(());
    };
    let mut items: Vec<Value> = Vec::new();
    for (key, value) in &table {
        let mut row = Dict::new();
        if let Value::Dict(fields) = value {
            row = (**fields).clone();
            row.entry(weight_field.clone()).or_insert(Value::Int(1));
            row.entry("_key".into()).or_insert_with(|| text(key));
        } else {
            row.insert("_key".into(), text(key));
            row.insert(weight_field.clone(), Value::Int(1));
            row.insert("value".into(), value.clone());
        }
        items.push(Value::dict(row));
    }
    if items.is_empty() {
        if default != Value::None {
            store(ctx, &store_as, default);
        }
        return Ok(());
    }
    let roll = match map.get("roll") {
        None | Some(Value::None) => None,
        Some(expr) => {
            let parts = parse_dice_expr(&resolve_template(&expr.to_str(), ctx));
            let delta = parts.modifier_delta()?;
            Some(ctx.dice.roll(parts.sides, parts.count, delta)?)
        }
    };
    let rows: Vec<sylvia_base::Json> =
        items.iter().map(super::bridge::json_safe).collect::<Result<_, _>>()?;
    match ctx.dice.weighted_pick(&rows, &weight_field, roll.as_ref())? {
        Some(index) => store(ctx, &store_as, items[index].clone()),
        None => {
            if default != Value::None {
                store(ctx, &store_as, default);
            }
        }
    }
    Ok(())
}

/// `_resolve_collection(ref, ctx)`: the list object itself (`live`), or a sequence's members
/// copied into a new list, or `None`.
pub(crate) fn resolve_collection(
    reference: &Value,
    ctx: &ExecutionContext<'_>,
) -> Result<Option<(ListRef, bool)>, ExecError> {
    let key = match reference {
        Value::Str(text) => text.to_string(),
        Value::List(_) | Value::Dict(_) => {
            return Err(ExecError::type_error(format!("unhashable type: '{}'", reference.type_name())))
        }
        _ => return Ok(None),
    };
    let value = ctx.params.get(&key).or_else(|| ctx.vars.get(&key)).cloned().unwrap_or(Value::None);
    Ok(match value {
        Value::List(items) => Some((items, true)),
        Value::Tuple(items) => Some((Arc::new((*items).clone()), false)),
        _ => None,
    })
}

/// `_handle_for_each`: the body's effects once per item, the item in `as`.
pub(crate) fn handle_for_each(
    body: &Value,
    ctx: &mut ExecutionContext<'_>,
    host: &dyn ToolHost,
) -> Result<(), ExecError> {
    let map = body_dict(body, "for_each", "body must be a dict")?;
    let name = get(map, "as", text("_item")).to_str();
    let effects = iterate(&get(map, "do", Value::list(Vec::new())))?;
    let Some((mut list, _)) = resolve_collection(&get(map, "collection", text("")), ctx)? else {
        return Ok(());
    };
    // Python iterates the list object by index, so items the body adds to it are visited too.
    let mut index = 0;
    loop {
        list = ctx.current_list(&list);
        let Some(item) = list.get(index).cloned() else { break };
        ctx.vars.insert(name.clone(), item);
        for effect in &effects {
            execute_effect(effect, ctx, host)?;
        }
        index += 1;
    }
    Ok(())
}

/// `item.get(by, 0)`, `getattr(item, by)` or `0`.
fn sort_key(item: &Value, by: &str) -> Value {
    match item {
        Value::Dict(map) => map.get(by).cloned().unwrap_or(Value::Int(0)),
        Value::Object(object) => {
            object.fields.iter().find(|(name, _)| *name == by).map_or(Value::Int(0), |(_, v)| v.clone())
        }
        other => match crate::expr::eval::traverse(other, by) {
            Value::None => Value::Int(0),
            value => value,
        },
    }
}

/// `list.sort(key=..., reverse=...)` with CPython's comparisons: reverse, a stable sort by key
/// (one `count_run` and one `binarysort` below 64 items), reverse again.
pub(crate) fn sort_by_key(
    items: &mut Vec<Value>,
    keys: Vec<Value>,
    reverse: bool,
) -> Result<(), crate::pyerror::PyError> {
    let mut pairs: Vec<(Value, Value)> = keys.into_iter().zip(items.drain(..)).collect();
    if reverse {
        pairs.reverse();
    }
    let lt = |a: &(Value, Value), b: &(Value, Value)| py_order(Order::Lt, &a.0, &b.0);
    if pairs.len() >= 2 {
        if pairs.len() >= 64 {
            let mut failure = None;
            pairs.sort_by(|a, b| {
                if failure.is_some() {
                    return std::cmp::Ordering::Equal;
                }
                match (lt(a, b), lt(b, a)) {
                    (Ok(true), _) => std::cmp::Ordering::Less,
                    (Ok(false), Ok(true)) => std::cmp::Ordering::Greater,
                    (Ok(false), Ok(false)) => std::cmp::Ordering::Equal,
                    (Err(error), _) | (_, Err(error)) => {
                        failure = Some(error);
                        std::cmp::Ordering::Equal
                    }
                }
            });
            if let Some(error) = failure {
                return Err(error);
            }
        } else {
            let mut run = 2;
            let descending = lt(&pairs[1], &pairs[0])?;
            while run < pairs.len() && lt(&pairs[run], &pairs[run - 1])? == descending {
                run += 1;
            }
            if descending {
                pairs[..run].reverse();
            }
            for start in run..pairs.len() {
                let (mut low, mut high) = (0, start);
                while low < high {
                    let middle = low + ((high - low) >> 1);
                    if lt(&pairs[start], &pairs[middle])? {
                        high = middle;
                    } else {
                        low = middle + 1;
                    }
                }
                pairs[low..=start].rotate_right(1);
            }
        }
    }
    if reverse {
        pairs.reverse();
    }
    items.extend(pairs.into_iter().map(|(_, value)| value));
    Ok(())
}

/// `_handle_sort`: sort the collection in place by a field; an unorderable sort leaves it.
pub(crate) fn handle_sort(body: &Value, ctx: &mut ExecutionContext<'_>) -> Result<(), ExecError> {
    let Value::Dict(map) = body else {
        return Ok(());
    };
    let by = get(map, "by", text(""));
    let direction = get(map, "direction", text("desc"));
    let Some((items, live)) = resolve_collection(&get(map, "collection", text("")), ctx)? else {
        return Ok(());
    };
    if items.is_empty() || !by.truthy() {
        return Ok(());
    }
    let Value::Str(direction) = direction else {
        return Err(ExecError::Python {
            class: "AttributeError",
            message: format!("'{}' object has no attribute 'lower'", direction.type_name()),
        });
    };
    let reverse = direction.to_lowercase() == "desc";
    let by = by.to_str();
    let keys: Vec<Value> = items.iter().map(|item| sort_key(item, &by)).collect();
    let mut sorted = (*items).clone();
    if sort_by_key(&mut sorted, keys, reverse).is_ok() && live {
        ctx.replace_list(&items, sorted);
    }
    Ok(())
}

/// `ctx.vars.get(name, [])` as the list handlers read it: the list object itself, or a tuple's
/// members (a new list), or a new empty list.
fn context_list(ctx: &ExecutionContext<'_>, name: &str) -> (Arc<Vec<Value>>, bool) {
    match ctx.vars.get(name) {
        Some(Value::List(items)) => (items.clone(), true),
        Some(Value::Tuple(items)) => (Arc::new((**items).clone()), false),
        _ => (Arc::new(Vec::new()), false),
    }
}

/// `_handle_list_push`: append to a context list (in place when it is one) and store it.
pub(crate) fn handle_list_push(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let map = body_dict(body, "list_push", "body must be a dict")?;
    let list_ref = get(map, "list", text("")).to_str();
    if list_ref.is_empty() {
        return Ok(());
    }
    let store_as = effect.get("store_as").map_or_else(|| list_ref.clone(), Value::to_str);
    let value = match map.get("value") {
        None | Some(Value::None) => Value::None,
        Some(expr) => resolve_value(expr, ctx)?,
    };
    let (list, live) = context_list(ctx, &list_ref);
    let mut grown = (*list).clone();
    grown.push(value);
    let list = if live { ctx.replace_list(&list, grown) } else { Arc::new(grown) };
    ctx.vars.insert(store_as, Value::List(list));
    Ok(())
}

/// `_handle_list_pop`: take the last item of a context list.
pub(crate) fn handle_list_pop(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let map = body_dict(body, "list_pop", "body must be a dict")?;
    let list_ref = get(map, "list", text("")).to_str();
    let store_as = effect.get("store_as").map_or_else(|| "_popped".to_owned(), Value::to_str);
    let (list, live) = context_list(ctx, &list_ref);
    let mut shrunk = (*list).clone();
    let Some(value) = shrunk.pop() else {
        ctx.vars.insert(store_as, Value::None);
        return Ok(());
    };
    let list = if live { ctx.replace_list(&list, shrunk) } else { Arc::new(shrunk) };
    ctx.vars.insert(list_ref, Value::List(list));
    ctx.vars.insert(store_as, value);
    Ok(())
}

/// `re.fullmatch(r"_[A-Za-z0-9_]+", name)`.
fn private_name(name: &str) -> bool {
    name.len() > 1 && name.starts_with('_') && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
}

#[cfg(feature = "vector-hooks")]
thread_local! {
    static IDENTITY_SHUFFLE: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Vector tooling only: run `body` with `shuffle` leaving its collection in order, as the
/// generators replace Python's `SystemRandom.shuffle`, so outcomes that store a shuffled
/// collection compare exactly. Only the `vector-hooks` feature, which only the vector tests
/// enable, compiles it: no product build contains it (`docs/rust-port/check.sh` checks the owner).
#[cfg(feature = "vector-hooks")]
#[doc(hidden)]
pub fn with_identity_shuffle_for_vectors<R>(body: impl FnOnce() -> R) -> R {
    let previous = IDENTITY_SHUFFLE.with(|flag| flag.replace(true));
    let result = body();
    IDENTITY_SHUFFLE.with(|flag| flag.set(previous));
    result
}

#[cfg(feature = "vector-hooks")]
static IDENTITY_SHUFFLE_EVERYWHERE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// Vector tooling only: every thread's `shuffle` leaves its collection in order until this is
/// set back to `false`, for code that runs a mechanic on another thread (the owner's store
/// thread). Set it only in a test binary of its own, and reset it when the test ends.
#[cfg(feature = "vector-hooks")]
#[doc(hidden)]
pub fn set_identity_shuffle_everywhere_for_vectors(enabled: bool) {
    IDENTITY_SHUFFLE_EVERYWHERE.store(enabled, std::sync::atomic::Ordering::Relaxed);
}

/// `_handle_shuffle`: an unguessable permutation (OS randomness, as in Python), kept as state.
pub(crate) fn handle_shuffle(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let collection = match body {
        Value::Dict(map) if map.len() == 1 && map.contains_key("collection") => map["collection"].clone(),
        _ => return Err(ExecError::effect("shuffle", "A collection is required")),
    };
    let destination = match effect.get("store_as") {
        Some(Value::Str(name)) if private_name(name) => name.to_string(),
        _ => return Err(ExecError::effect("shuffle", "A context destination is required")),
    };
    let mut values: Vec<Value> = crate::expr::collections::collection(&resolve_value(&collection, ctx)?)?
        .iter()
        .map(super::context::deep_copy)
        .collect();
    // `secrets.SystemRandom().shuffle(values)`: OS randomness, as in Python.
    #[cfg(feature = "vector-hooks")]
    let identity = IDENTITY_SHUFFLE.with(std::cell::Cell::get)
        || IDENTITY_SHUFFLE_EVERYWHERE.load(std::sync::atomic::Ordering::Relaxed);
    #[cfg(not(feature = "vector-hooks"))]
    let identity = false;
    if !identity {
        crate::pyrandom::PyRandom::from_entropy().shuffle(&mut values);
    }
    ctx.vars.insert(destination, Value::list(values));
    ctx.budget.private_random_sampled.set(true);
    Ok(())
}

/// `re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", option_id)`.
fn option_identity(text: &str) -> bool {
    let mut chars = text.chars();
    chars.next().is_some_and(|c| c.is_ascii_alphanumeric())
        && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | ':' | '-'))
}

/// `_handle_request_choice`: suspend until the owner answers, or replay and consume the answer.
pub(crate) fn handle_request_choice(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
) -> Result<(), ExecError> {
    let invalid = |message: &str| ExecError::effect("request_choice", message);
    if ctx.budget.private_random_sampled.get() {
        return Err(invalid("Commit a hidden shuffle before requesting a later choice"));
    }
    let map = body_dict(body, "request_choice", "request body must be an object")?;
    let (choice_id, prompt) = (get(map, "choice_id", Value::None), get(map, "prompt", Value::None));
    let mut raw_options = get(map, "options", Value::None);
    if let Value::Str(name) = &raw_options {
        if private_name(name) {
            raw_options = match resolve_collection(&raw_options, ctx)? {
                Some((items, _)) => Value::List(items),
                None => Value::None,
            };
        }
    }
    let store_as = get(effect, "store_as", Value::None);
    let (Value::Str(choice_id), Value::Str(prompt), Value::List(raw_options), Value::Str(store_as)) =
        (&choice_id, &prompt, &raw_options, &store_as)
    else {
        return Err(invalid("request declaration is invalid"));
    };
    if choice_id.is_empty() || prompt.is_empty() || !store_as.starts_with('_') {
        return Err(invalid("request declaration is invalid"));
    }
    let rendered_prompt = resolve_template(prompt, ctx);
    let mut options: Vec<MechanicsChoiceOption> = Vec::new();
    for raw in raw_options.iter() {
        let Value::Dict(option) = raw else { return Err(invalid("choice option is invalid")) };
        if option.len() != 2 || !option.contains_key("id") || !option.contains_key("label") {
            return Err(invalid("choice option is invalid"));
        }
        let (Value::Str(id), Value::Str(label)) = (&option["id"], &option["label"]) else {
            return Err(invalid("choice option is invalid"));
        };
        if !option_identity(id) {
            return Err(invalid("choice option is invalid"));
        }
        let rendered = resolve_template(label, ctx);
        if options.iter().any(|o| o.option_id == **id) || rendered.is_empty() {
            return Err(invalid("choice option is invalid"));
        }
        options.push(MechanicsChoiceOption { option_id: id.to_string(), label: rendered });
    }
    if !(2..=16).contains(&options.len()) {
        return Err(invalid("choice option count is out of bounds"));
    }
    if rendered_prompt.len() > 4096 || options.iter().any(|o| o.label.len() > 1024) {
        return Err(invalid("choice text is out of bounds"));
    }
    let request =
        MechanicsChoiceRequest { choice_id: choice_id.to_string(), prompt: rendered_prompt, options };
    let Some(control) = ctx.choice else {
        return Err(ExecError::Suspended(Box::new(request)));
    };
    if control.prior_answers.len() >= 64 {
        return Err(invalid("choice sequence exceeds its bound"));
    }
    let replayed = control.replayed_answers.get();
    if replayed < control.prior_answers.len() {
        let (previous_request, previous_option) = &control.prior_answers[replayed];
        if *previous_request != request || !request.options.iter().any(|o| o.option_id == *previous_option) {
            return Err(invalid("prior choice resolution does not match"));
        }
        control.replayed_answers.set(replayed + 1);
        ctx.vars.insert(store_as.to_string(), text(previous_option));
        return Ok(());
    }
    if control.consumed.get() {
        if control.prior_answers.len() >= 63 {
            return Err(invalid("choice sequence exceeds its bound"));
        }
        return Err(ExecError::Suspended(Box::new(request)));
    }
    if control.choice_id != **choice_id
        || !request.options.iter().any(|o| o.option_id == control.option_id)
        || control.expected_request != request
    {
        return Err(invalid("choice resolution does not match"));
    }
    control.consumed.set(true);
    ctx.vars.insert(store_as.to_string(), text(&control.option_id));
    Ok(())
}
