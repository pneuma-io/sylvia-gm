//! `ExecutionContext` from `runtime/_types.py`: the mutable context one tool invocation carries.

use super::bridge::StateBridge;
use crate::dice::Dice;
use crate::expr::eval::traverse;
use crate::expr::value::{Dict, Value};
use crate::expr::{EvalContext, ExecutionBudget};
use std::cell::Cell;
use std::collections::BTreeSet;
use std::sync::Arc;

/// A list value's shared storage (its Python identity).
pub type ListRef = Arc<Vec<Value>>;
use sylvia_domain::authority::MechanicsChoiceRequest;

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

/// `ChoiceResolutionControl`: the owner's authorized answer for one suspended package choice,
/// and the answers already given earlier in the same action. Shared by every nested call.
#[derive(Debug)]
pub struct ChoiceControl {
    pub choice_id: String,
    pub option_id: String,
    pub expected_request: MechanicsChoiceRequest,
    pub consumed: Cell<bool>,
    pub prior_answers: Vec<(MechanicsChoiceRequest, String)>,
    pub replayed_answers: Cell<usize>,
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
    /// The seeded roll stream (`dice`'s module state in Python).
    pub dice: &'a mut Dice,
    /// `extras["mechanics_choice_resolution"]`.
    pub choice: Option<&'a ChoiceControl>,
    /// Lists changed in place so far, each old list with the one that replaced it (kept alive so
    /// their addresses stay unique while the call runs).
    pub list_history: Vec<(ListRef, ListRef)>,
}

/// `copy.deepcopy(value)` for execution values: every container rebuilt, so nothing aliases the
/// original.
pub fn deep_copy(value: &Value) -> Value {
    match value {
        Value::List(items) => Value::list(items.iter().map(deep_copy).collect()),
        Value::Tuple(items) => Value::tuple(items.iter().map(deep_copy).collect()),
        Value::Dict(map) => {
            Value::dict(map.iter().map(|(key, item)| (key.clone(), deep_copy(item))).collect())
        }
        other => other.clone(),
    }
}

fn holds(value: &Value, old: &Arc<Vec<Value>>) -> bool {
    match value {
        Value::List(items) => Arc::ptr_eq(items, old) || items.iter().any(|item| holds(item, old)),
        Value::Tuple(items) => items.iter().any(|item| holds(item, old)),
        Value::Dict(map) => map.values().any(|item| holds(item, old)),
        Value::Object(object) => object.fields.iter().any(|(_, item)| holds(item, old)),
        _ => false,
    }
}

/// Point every reference to `old` at `new` (Python's aliases of a list changed in place).
fn repoint(value: &mut Value, old: &Arc<Vec<Value>>, new: &Arc<Vec<Value>>) {
    if !holds(value, old) {
        return;
    }
    match value {
        Value::List(items) if Arc::ptr_eq(items, old) => *value = Value::List(new.clone()),
        Value::List(items) | Value::Tuple(items) => {
            Arc::make_mut(items).iter_mut().for_each(|item| repoint(item, old, new))
        }
        Value::Dict(map) => Arc::make_mut(map).values_mut().for_each(|item| repoint(item, old, new)),
        Value::Object(object) => {
            Arc::make_mut(object).fields.iter_mut().for_each(|(_, item)| repoint(item, old, new))
        }
        _ => {}
    }
}

impl<'a> ExecutionContext<'a> {
    /// A list changed in place: the new contents replace `old` everywhere the context holds it,
    /// as every Python name bound to that list object sees the change.
    pub fn replace_list(&mut self, old: &Arc<Vec<Value>>, items: Vec<Value>) -> Arc<Vec<Value>> {
        let new = Arc::new(items);
        for map in [&mut self.params, &mut self.vars, &mut self.entities] {
            map.values_mut().for_each(|value| repoint(value, old, &new));
        }
        if let Some(published) = self.extras.published_result.as_mut() {
            repoint(published, old, &new);
        }
        self.list_history.push((old.clone(), new.clone()));
        new
    }

    /// The list object `list` is now (following its in-place changes).
    pub fn current_list(&self, list: &Arc<Vec<Value>>) -> Arc<Vec<Value>> {
        let mut current = list.clone();
        while let Some((_, next)) = self.list_history.iter().rev().find(|(old, _)| Arc::ptr_eq(old, &current))
        {
            current = next.clone();
        }
        current
    }

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
