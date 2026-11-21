//! `runtime/_authoring_shape.py` (with the tables of `runtime/_authoring_declarations.py`):
//! closed authoring shapes for runtime effects, reported as field paths.

use super::pydata::{self, is_sequence, sorted, to_str};
use super::schema_validator::validate_effects;
use crate::pyerror::PyResult;
use sylvia_base::pyyaml::Data;

/// `EFFECT_BODY_FIELDS`, in its declaration order.
pub const EFFECT_BODY_FIELDS: &[(&str, &[&str])] = &[
    ("publish", &["fields"]),
    ("shuffle", &["collection"]),
    (
        "dice",
        &[
            "expression",
            "modifier",
            "advantage",
            "disadvantage",
            "critical",
            "explode",
            "explode_on",
            "max_explosions",
        ],
    ),
    ("set", &["field", "value"]),
    ("set_participant", &["participant", "field", "value"]),
    ("create_entity", &["id", "value"]),
    ("require", &["condition", "error"]),
    ("request_choice", &["choice_id", "prompt", "options"]),
    ("apply_damage", &["target", "amount"]),
    ("apply_condition", &["target", "condition"]),
    ("remove_condition", &["target", "condition"]),
    ("call", &["call", "name", "params"]),
    ("trigger", &["trigger", "name", "context"]),
    ("save_throw", &["target", "ability", "dc", "on_save"]),
    ("heal", &["target", "amount"]),
    ("decrement", &["field", "amount"]),
    ("increment", &["field", "amount"]),
    ("condition", &["condition", "on_true", "on_false"]),
    ("log", &["level", "message"]),
    ("table_lookup", &["table", "key", "default", "required", "exact_selector"]),
    ("spatial_query", &["scene", "operation", "query", "snapshot"]),
    ("spatial_mutation", &["scene", "map_id", "expected_revision", "target", "patch"]),
    (
        "shot_effect",
        &[
            "scene",
            "snapshot",
            "source_id",
            "target_id",
            "origin",
            "destination",
            "delay_ms",
            "miss",
            "contacts",
            "style",
            "target_kind",
        ],
    ),
    ("for_each", &["collection", "as", "do"]),
    ("sort", &["collection", "by", "direction"]),
    ("apply_ability_damage", &["target", "ability", "amount"]),
    ("count_successes", &["pool", "modifier", "threshold"]),
    ("reroll", &["source", "predicate", "modifier", "threshold", "consequence"]),
    ("hunger_dice", &["pool", "hunger", "sides", "modifier", "threshold"]),
    ("set_flag", &["flag", "set_flag", "value"]),
    ("advance_time", &["advance_time", "duration"]),
    ("list_push", &["list", "value"]),
    ("list_pop", &["list"]),
    (
        "initiative",
        &[
            "combatants",
            "expression",
            "modifier_field",
            "modifier_minimum",
            "modifier_maximum",
            "required_truthy_field",
            "label_field",
            "roll",
        ],
    ),
    ("advance_initiative", &["skip_field", "skip_equals"]),
    ("random_table", &["table", "roll", "weight_field", "default"]),
    ("transition_scene", &["scene", "transition_scene"]),
    ("aoe", &["aoe", "as", "do"]),
    ("defer", &["tool", "call", "params", "priority", "reason", "context_vars"]),
    ("narrate", &["narrate", "text", "style", "priority"]),
    (
        "register_initiative_trigger",
        &["id", "trigger_id", "procedure", "procedure_name", "source", "count", "repeats", "context_data"],
    ),
    ("clock_create", &["id", "clock_id", "name", "max_segments", "thresholds", "metadata"]),
    ("clock_tick", &["id", "clock_id", "segments"]),
];

/// `SCALAR_EFFECT_BODIES`.
pub const SCALAR_EFFECT_BODIES: &[&str] = &["call", "trigger"];

/// `NEUTRAL_EFFECT_HANDLERS`: `sorted(_NEUTRAL_EFFECT_KEYS)` of `runtime/_effect_executor.py`.
pub const NEUTRAL_EFFECT_HANDLERS: &[&str] = &[
    "advance_initiative",
    "advance_time",
    "aoe",
    "apply_condition",
    "call",
    "clock_create",
    "clock_tick",
    "condition",
    "count_successes",
    "create_entity",
    "decrement",
    "defer",
    "dice",
    "for_each",
    "increment",
    "initiative",
    "list_pop",
    "list_push",
    "log",
    "narrate",
    "publish",
    "random_table",
    "register_initiative_trigger",
    "remove_condition",
    "request_choice",
    "require",
    "reroll",
    "set",
    "set_flag",
    "set_participant",
    "shot_effect",
    "shuffle",
    "sort",
    "spatial_mutation",
    "spatial_query",
    "table_lookup",
    "transition_scene",
    "trigger",
];

/// `COMPATIBILITY_EFFECT_HANDLERS`: `sorted(_COMPATIBILITY_EFFECT_KEYS)`.
pub const COMPATIBILITY_EFFECT_HANDLERS: &[&str] =
    &["apply_ability_damage", "apply_damage", "heal", "hunger_dice", "save_throw"];

fn body_fields(handler: &str) -> Option<&'static [&'static str]> {
    EFFECT_BODY_FIELDS.iter().find(|(name, _)| *name == handler).map(|(_, fields)| *fields)
}

/// `sorted(set(mapping) - allowed)`.
fn undeclared(pairs: &[(Data, Data)], allowed: &[&str]) -> PyResult<Vec<Data>> {
    sorted(&pydata::keys_except(pairs, allowed))
}

struct Collector {
    errors: Vec<(String, String)>,
}

impl Collector {
    fn push(&mut self, path: String, message: &str) {
        self.errors.push((path, message.to_owned()));
    }

    fn effects(&mut self, value: Option<&Data>, path: &str) -> PyResult<()> {
        let Some(value) = value.filter(|v| is_sequence(v)) else { return Ok(()) };
        let (Data::List(items) | Data::Tuple(items)) = value else { unreachable!() };
        for (index, effect) in items.iter().enumerate() {
            let item_path = format!("{path}[{index}]");
            let Data::Dict(pairs) = effect else { continue };
            let condition = effect.get_str("condition");
            let handlers: Vec<&str> = pairs
                .iter()
                .filter_map(|(key, _)| pydata::text(Some(key)))
                .filter(|name| body_fields(name).is_some())
                .collect();
            let flat = matches!(condition, Some(Data::Str(_)))
                && (pydata::contains(pairs, "on_true") || pydata::contains(pairs, "on_false"))
                && handlers.iter().all(|name| *name == "condition");
            if flat {
                for name in undeclared(pairs, &["condition", "on_true", "on_false"])? {
                    self.push(format!("{item_path}.{}", to_str(&name)), "undeclared effect-envelope field");
                }
                self.effects(
                    Some(effect.get_str("on_true").unwrap_or(&Data::List(Vec::new()))),
                    &format!("{item_path}.on_true"),
                )?;
                self.effects(
                    Some(effect.get_str("on_false").unwrap_or(&Data::List(Vec::new()))),
                    &format!("{item_path}.on_false"),
                )?;
                continue;
            }
            if handlers == ["condition"] {
                for name in undeclared(pairs, &["condition"])? {
                    self.push(format!("{item_path}.{}", to_str(&name)), "undeclared effect-envelope field");
                }
                let body = effect.get_str("condition").expect("handler present");
                match body {
                    Data::Dict(body_pairs) => {
                        for name in undeclared(body_pairs, body_fields("condition").expect("declared"))? {
                            self.push(
                                format!("{item_path}.condition.{}", to_str(&name)),
                                "undeclared condition-body field",
                            );
                        }
                        let empty = Data::List(Vec::new());
                        self.effects(
                            Some(body.get_str("on_true").unwrap_or(&empty)),
                            &format!("{item_path}.condition.on_true"),
                        )?;
                        self.effects(
                            Some(body.get_str("on_false").unwrap_or(&empty)),
                            &format!("{item_path}.condition.on_false"),
                        )?;
                    }
                    Data::Str(_) => {}
                    _ => self
                        .push(format!("{item_path}.condition"), "condition body must be text or an object"),
                }
                continue;
            }
            let non_condition: Vec<&str> =
                handlers.iter().copied().filter(|name| *name != "condition").collect();
            let [handler] = non_condition.as_slice() else { continue };
            let mut envelope = vec![*handler, "condition", "store_as"];
            match *handler {
                "call" => envelope.extend(["params", "export_context"]),
                "trigger" => envelope.push("context"),
                _ => {}
            }
            for name in undeclared(pairs, &envelope)? {
                self.push(format!("{item_path}.{}", to_str(&name)), "undeclared effect-envelope field");
            }
            let body = effect.get_str(handler).expect("handler present");
            match body {
                Data::Dict(body_pairs) => {
                    for name in undeclared(body_pairs, body_fields(handler).expect("declared"))? {
                        self.push(
                            format!("{item_path}.{handler}.{}", to_str(&name)),
                            "undeclared effect-body field",
                        );
                    }
                    for branch in ["on_true", "on_false", "do", "consequence"] {
                        if let Some(nested) = body.get_str(branch) {
                            self.effects(Some(nested), &format!("{item_path}.{handler}.{branch}"))?;
                        }
                    }
                }
                Data::Str(_) if SCALAR_EFFECT_BODIES.contains(handler) => {}
                _ => self.push(format!("{item_path}.{handler}"), "effect body must be an object"),
            }
        }
        Ok(())
    }
}

/// `runtime_authoring_shape_errors(raw)`: `(path, message)` for executable keys the runtime
/// would ignore, at most 16.
pub fn runtime_authoring_shape_errors(raw: &Data) -> PyResult<Vec<(String, String)>> {
    let mut collector = Collector { errors: Vec::new() };
    let Data::Dict(_) = raw else { return Ok(collector.errors) };
    if let Some(Data::Dict(tools)) = raw.get_str("tools").or(Some(&Data::Dict(Vec::new()))) {
        for (name, tool) in tools {
            if let (Data::Str(name), Data::Dict(_)) = (name, tool) {
                let effects = tool.get_str("effects").cloned().unwrap_or(Data::List(Vec::new()));
                collector.effects(Some(&effects), &format!("$.tools.{name}.effects"))?;
            }
        }
    }
    if let Some(Data::Dict(procedures)) = raw.get_str("procedures").or(Some(&Data::Dict(Vec::new()))) {
        for (name, procedure) in procedures {
            if let (Data::Str(name), Data::Dict(_)) = (name, procedure) {
                let steps = procedure.get_str("steps").cloned().unwrap_or(Data::List(Vec::new()));
                let path = format!("$.procedures.{name}.steps");
                if let Data::List(step_list) = &steps {
                    if !validate_effects(&format!("procedure {name}"), step_list)?.is_empty() {
                        collector.push(
                            path.clone(),
                            "Procedure steps must use registered effects and valid declarations.",
                        );
                    }
                }
                collector.effects(Some(&steps), &path)?;
            }
        }
    }
    collector.errors.truncate(16);
    Ok(collector.errors)
}
