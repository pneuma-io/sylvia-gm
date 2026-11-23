//! `runtime/_effect_executor.py`'s `_handle_aoe` and `runtime/_aoe_resolver.py`: which entities
//! an area of effect reaches, by distance bands or grid squares, and its sub-effects for each.

use super::context::ExecutionContext;
use super::effects::{execute_effect, iterate};
use super::error::ExecError;
use super::tool::ToolHost;
use crate::expr::value::{Dict, Value};

const BAND_ORDER: [(&str, i64); 4] = [("close", 0), ("near", 1), ("far", 2), ("distant", 3)];
const BAND_FEET: [(&str, i64); 4] = [("close", 10), ("near", 30), ("far", 60), ("distant", 120)];

fn lookup(table: &[(&str, i64)], key: &str) -> Option<i64> {
    table.iter().find(|(name, _)| *name == key).map(|(_, value)| *value)
}

/// `AoESpec`.
struct Spec {
    shape: String,
    size: Value,
    origin: String,
    filter: Value,
}

/// `int(value)` as `resolve_aoe` reads a grid coordinate: `Ok(None)` for the `ValueError` and
/// `TypeError` it suppresses; an infinite float's `OverflowError` and the integer-range refusal
/// (differences row 5) are raised.
fn grid_int(value: &Value) -> Result<Option<i64>, ExecError> {
    Ok(match value {
        Value::Int(number) => Some(*number),
        Value::Bool(flag) => Some(i64::from(*flag)),
        Value::Float(number) if number.is_nan() => None,
        Value::Float(number) => Some(super::error::float_to_int(*number)?),
        Value::Str(text) => match sylvia_base::pystr::parse_int(text) {
            Ok(number) => Some(number),
            Err(sylvia_base::pystr::IntParseError::Invalid(_)) => None,
            Err(_) => return Err(ExecError::value_error(sylvia_base::pystr::INT_RANGE_MESSAGE)),
        },
        _ => None,
    })
}

/// `int(size)` for a number, as both resolvers read a numeric size.
fn size_int(value: &Value) -> Result<Option<i64>, ExecError> {
    Ok(match value {
        Value::Int(number) => Some(*number),
        Value::Bool(flag) => Some(i64::from(*flag)),
        Value::Float(number) => Some(super::error::float_to_int(*number)?),
        _ => None,
    })
}

/// `value.lower()`, or the `AttributeError` a value that is not text raises.
fn lower(value: &Value) -> Result<String, ExecError> {
    match value {
        Value::Str(text) => Ok(text.to_lowercase()),
        other => Err(ExecError::Python {
            class: "AttributeError",
            message: format!("'{}' object has no attribute 'lower'", other.type_name()),
        }),
    }
}

/// `_resolve_origin(origin, ctx)`: `self` is the `attacker` parameter, by itself or its
/// `id`/`entity_id`.
fn resolve_origin(origin: &str, ctx: &ExecutionContext<'_>) -> Option<Value> {
    if origin != "self" {
        return Some(Value::str(origin));
    }
    match ctx.params.get("attacker") {
        Some(attacker @ Value::Str(_)) => Some(attacker.clone()),
        Some(Value::Dict(map)) => Some(
            map.get("id").cloned().unwrap_or_else(|| map.get("entity_id").cloned().unwrap_or(Value::None)),
        ),
        _ => None,
    }
}

/// `state_bridge.get_position(entity_id)`: only a text identity can name an entity.
fn position(ctx: &ExecutionContext<'_>, id: &Value) -> Option<Dict> {
    let (Some(bridge), Value::Str(id)) = (ctx.state_bridge.as_deref(), id) else { return None };
    match bridge.get_position(id) {
        Some(Value::Dict(map)) => Some((*map).clone()),
        _ => None,
    }
}

/// `int(x), int(y)` in order: `y` is not read when `x` is suppressed.
fn grid_pair(x: &Value, y: &Value) -> Result<Option<(i64, i64)>, ExecError> {
    let Some(x) = grid_int(x)? else { return Ok(None) };
    Ok(grid_int(y)?.map(|y| (x, y)))
}

/// `resolve_aoe_bands(...)`.
fn resolve_bands(
    origin_id: &Option<Value>,
    origin_band: &Value,
    shape: &str,
    size: &Value,
    positioned: &[(String, Dict)],
) -> Result<Vec<String>, ExecError> {
    let origin_index = lookup(&BAND_ORDER, &lower(origin_band)?).unwrap_or(0);
    let size_index = match size_int(size)? {
        Some(number) => number,
        None => lookup(&BAND_ORDER, &size.to_str().to_lowercase()).unwrap_or(1),
    };
    let shape = shape.to_lowercase();
    let mut hits = Vec::new();
    for (id, place) in positioned {
        if origin_id.as_ref().is_some_and(|origin| *origin == Value::str(id.as_str())) {
            continue;
        }
        let band = place.get("band").cloned().unwrap_or_else(|| Value::str("close"));
        let band_index = lookup(&BAND_ORDER, &lower(&band)?).unwrap_or(0);
        let hit = match shape.as_str() {
            "burst" => band_index <= size_index,
            "cone" => origin_index < band_index && band_index <= origin_index + size_index,
            "line" => band_index <= origin_index + size_index,
            _ => false,
        };
        if hit {
            hits.push(id.clone());
        }
    }
    Ok(hits)
}

/// `resolve_aoe_grid(...)`: five feet a square.
fn resolve_grid(
    origin_x: i64,
    origin_y: i64,
    shape: &str,
    size: &Value,
    entities: &[(String, i64, i64)],
) -> Result<Vec<String>, ExecError> {
    let radius = match size_int(size)? {
        Some(number) => number,
        None => lookup(&BAND_FEET, &size.to_str().to_lowercase()).unwrap_or(30),
    } as f64;
    let shape = shape.to_lowercase();
    let mut hits = Vec::new();
    for (id, x, y) in entities {
        let (dx, dy) = (i128::from(*x) - i128::from(origin_x), i128::from(*y) - i128::from(origin_y));
        let distance = ((dx * dx + dy * dy) as f64).sqrt() * 5.0;
        let hit = match shape.as_str() {
            "sphere" | "cylinder" => distance <= radius,
            "cube" => {
                let half = radius / 5.0 / 2.0;
                (dx.abs() as f64) <= half && (dy.abs() as f64) <= half
            }
            "cone" => {
                distance <= radius
                    && dx >= 0
                    && crate::pymath::atan2(dy as f64, dx as f64).abs() <= std::f64::consts::PI / 4.0
            }
            "line" => distance <= radius && dy.abs() * 5 <= 5 && dx >= 0,
            _ => false,
        };
        if hit {
            hits.push(id.clone());
        }
    }
    Ok(hits)
}

/// `_apply_filter(entity_ids, filter_expr, ctx)`: each entity's fields and `_aoe_target` are
/// put into the context variables for the filter, then put back.
fn apply_filter(
    ids: Vec<String>,
    filter: &Value,
    ctx: &mut ExecutionContext<'_>,
) -> Result<Vec<String>, ExecError> {
    if !filter.truthy() {
        return Ok(ids);
    }
    let mut kept = Vec::new();
    for id in ids {
        let entity = match ctx.entities.get(&id) {
            None => Dict::new(),
            Some(Value::Dict(map)) => (**map).clone(),
            Some(other) => {
                return Err(ExecError::Python {
                    class: "AttributeError",
                    message: format!("'{}' object has no attribute 'items'", other.type_name()),
                })
            }
        };
        let mut saved = Dict::new();
        for (field, value) in &entity {
            if let Some(previous) = ctx.vars.get(field) {
                saved.insert(field.clone(), previous.clone());
            }
            ctx.vars.insert(field.clone(), value.clone());
        }
        if let Some(previous) = ctx.vars.get("_aoe_target") {
            saved.insert("_aoe_target".into(), previous.clone());
        }
        ctx.vars.insert("_aoe_target".into(), Value::str(id.as_str()));
        // A filter that cannot be evaluated (or is not text) skips the entity.
        let passes = match filter {
            Value::Str(expression) => {
                ctx.with_eval(|eval| crate::expr::evaluate_condition(expression, eval)).unwrap_or(false)
            }
            _ => false,
        };
        if passes {
            kept.push(id.clone());
        }
        for field in entity.keys() {
            match saved.get(field) {
                Some(previous) => {
                    ctx.vars.insert(field.clone(), previous.clone());
                }
                None => {
                    ctx.vars.shift_remove(field);
                }
            }
        }
        match saved.get("_aoe_target") {
            Some(previous) => {
                ctx.vars.insert("_aoe_target".into(), previous.clone());
            }
            None => {
                ctx.vars.shift_remove("_aoe_target");
            }
        }
    }
    Ok(kept)
}

/// `resolve_aoe(spec, ctx)`: grid squares when most positioned entities have grid coordinates,
/// otherwise distance bands.
fn resolve(spec: &Spec, ctx: &mut ExecutionContext<'_>) -> Result<Vec<String>, ExecError> {
    let origin_id = resolve_origin(&spec.origin, ctx);
    let candidates: Vec<String> = ctx
        .entities
        .keys()
        .filter(|id| origin_id.as_ref().is_none_or(|origin| *origin != Value::str(id.as_str())))
        .cloned()
        .collect();
    let mut positioned = Vec::new();
    let mut grid = Vec::new();
    for id in candidates {
        if ctx.state_bridge.is_none() {
            continue;
        }
        let Some(place) = position(ctx, &Value::str(id.as_str())) else { continue };
        if let (Some(x), Some(y)) = (place.get("x"), place.get("y")) {
            if let Some((x, y)) = grid_pair(x, y)? {
                grid.push((id.clone(), x, y));
            }
        }
        positioned.push((id, place));
    }
    if grid.len() as f64 >= positioned.len() as f64 * 0.5 && grid.len() >= 2 {
        // `_get_origin_grid`.
        let origin = match &origin_id {
            Some(origin) if ctx.state_bridge.is_some() => position(ctx, origin),
            _ => None,
        };
        let Some(origin) = origin else { return Ok(Vec::new()) };
        let Some(x) = origin.get("x") else { return Ok(Vec::new()) };
        let Some(x) = grid_int(x)? else { return Ok(Vec::new()) };
        let Some(y) = origin.get("y") else { return Ok(Vec::new()) };
        let Some(y) = grid_int(y)? else { return Ok(Vec::new()) };
        let hits = resolve_grid(x, y, &spec.shape, &spec.size, &grid)?;
        return apply_filter(hits, &spec.filter, ctx);
    }
    // `_get_origin_band`.
    let (band, band_origin) = match &origin_id {
        Some(origin) if ctx.state_bridge.is_some() => {
            let band = match position(ctx, origin) {
                Some(place) if !place.is_empty() => {
                    place.get("band").cloned().unwrap_or_else(|| Value::str("close"))
                }
                _ => Value::str("close"),
            };
            (band, Some(origin.clone()))
        }
        _ => (Value::str("close"), None),
    };
    let hits = resolve_bands(&band_origin, &band, &spec.shape, &spec.size, &positioned)?;
    apply_filter(hits, &spec.filter, ctx)
}

/// `_handle_aoe`: resolve the area's targets, then run the `do` effects once per target with
/// the target in the loop variable.
pub(crate) fn handle_aoe(
    body: &Value,
    effect: &Dict,
    ctx: &mut ExecutionContext<'_>,
    host: &dyn ToolHost,
) -> Result<(), ExecError> {
    let Value::Dict(map) = body else {
        return Err(ExecError::effect("aoe", "body must be a dict"));
    };
    let raw = map.get("aoe").cloned().unwrap_or_else(|| body.clone());
    let sub_effects = map
        .get("do")
        .cloned()
        .unwrap_or_else(|| effect.get("do").cloned().unwrap_or_else(|| Value::list(Vec::new())));
    let as_var = map
        .get("as")
        .cloned()
        .unwrap_or_else(|| effect.get("as").cloned().unwrap_or_else(|| Value::str("_aoe_target")))
        .to_str();
    let Value::Dict(raw) = &raw else {
        return Err(ExecError::Python {
            class: "AttributeError",
            message: format!("'{}' object has no attribute 'get'", raw.type_name()),
        });
    };
    let spec = Spec {
        shape: raw.get("shape").cloned().unwrap_or_else(|| Value::str("sphere")).to_str(),
        size: raw.get("size").cloned().unwrap_or(Value::Int(30)),
        origin: raw.get("origin").cloned().unwrap_or_else(|| Value::str("self")).to_str(),
        filter: raw.get("filter").cloned().unwrap_or(Value::None),
    };
    let targets = resolve(&spec, ctx)?;
    for target in targets {
        ctx.vars.insert(as_var.clone(), Value::str(target.as_str()));
        // Python iterates the sub-effects afresh for each target.
        for sub in &iterate(&sub_effects)? {
            execute_effect(sub, ctx, host)?;
        }
    }
    Ok(())
}
