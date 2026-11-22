//! `ExecutionContext` from `runtime/_types.py`: the mutable context one tool invocation carries.

use super::bridge::StateBridge;
use crate::expr::eval::traverse;
use crate::expr::value::{Dict, Value};
use crate::expr::{EvalContext, ExecutionBudget};
use std::collections::BTreeSet;

/// Where `ctx.combat_state` lives. Python hands a tool the bridge's own combat dict, so writes
/// through the context change state; a caller without a bridge passes its own dict.
#[derive(Debug, Clone)]
pub enum Combat {
    /// The bridge's combat dict (`state_bridge.combat_state`).
    Bridge,
    /// A dict of the caller's.
    Own(Dict),
}

/// `ctx.extras`: the runtime managers and per-call bookkeeping Python keeps in a free dict.
#[derive(Debug, Clone, Default)]
pub struct Extras {
    /// `extras["published_result"]`.
    pub published_result: Option<Value>,
    /// `extras["entity_parameter_names"]`.
    pub entity_parameter_names: BTreeSet<String>,
    /// `extras["participant_parameter_names"]`.
    pub participant_parameter_names: BTreeSet<String>,
}

/// `ExecutionContext`.
pub struct ExecutionContext<'a> {
    pub params: Dict,
    pub vars: Dict,
    pub entities: Dict,
    pub ruleset_config: Value,
    pub definitions: Value,
    pub combat: Combat,
    pub state_bridge: Option<&'a mut (dyn StateBridge + 'static)>,
    pub current_tool: String,
    pub extras: Extras,
    pub budget: &'a ExecutionBudget,
}

impl<'a> ExecutionContext<'a> {
    /// `ctx.combat_state`.
    pub fn combat_state(&self) -> Dict {
        match (&self.combat, &self.state_bridge) {
            (Combat::Own(map), _) => map.clone(),
            (Combat::Bridge, Some(bridge)) => bridge.combat().clone(),
            (Combat::Bridge, None) => Dict::new(),
        }
    }

    /// `ctx.combat_state[key] = value`.
    pub fn set_combat(&mut self, key: &str, value: Value) {
        match (&mut self.combat, &mut self.state_bridge) {
            (Combat::Own(map), _) => {
                map.insert(key.to_owned(), value);
            }
            (Combat::Bridge, Some(bridge)) => {
                bridge.combat_mut().insert(key.to_owned(), value);
            }
            (Combat::Bridge, None) => {}
        }
    }

    /// Run `f` with the expression evaluator's view of this context.
    pub fn with_eval<T>(&self, f: impl FnOnce(&EvalContext<'_>) -> T) -> T {
        let combat = Value::dict(self.combat_state());
        let eval = EvalContext {
            params: &self.params,
            vars: &self.vars,
            entities: &self.entities,
            combat_state: &combat,
            ruleset_config: &self.ruleset_config,
            definitions: &self.definitions,
            budget: Some(self.budget),
        };
        f(&eval)
    }

    /// `resolve_field(path)`: parameters (a context variable of the same name wins), context
    /// variables, `combat.*`, `combatants` and `party_members` (through the parameters),
    /// entity snapshots, `ruleset.*` and `definitions.*`.
    pub fn resolve_field(&self, path: &str) -> Value {
        let parts: Vec<&str> = path.split('.').collect();
        let root = parts[0];
        let walk =
            |start: Value, rest: &[&str]| rest.iter().fold(start, |current, part| traverse(&current, part));
        if self.params.contains_key(root) {
            let start = self.vars.get(root).or_else(|| self.params.get(root)).cloned().unwrap_or(Value::None);
            return walk(start, &parts[1..]);
        }
        if let Some(value) = self.vars.get(root) {
            return walk(value.clone(), &parts[1..]);
        }
        if root == "combat" && parts.len() > 1 {
            return walk(Value::dict(self.combat_state()), &parts[1..]);
        }
        if root == "combatants" || root == "party_members" {
            return walk(Value::dict(self.params.clone()), &parts);
        }
        if let Some(entity) = self.entities.get(root) {
            return walk(entity.clone(), &parts[1..]);
        }
        if root == "ruleset" {
            return walk(self.ruleset_config.clone(), &parts[1..]);
        }
        if root == "definitions" {
            return walk(self.definitions.clone(), &parts[1..]);
        }
        Value::None
    }
}
