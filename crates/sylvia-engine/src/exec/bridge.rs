//! `application/package_runtime.py`'s `AuthorityMemoryStateBridge`, the non-spatial part: the
//! mutable package-local view of state one mechanic execution reads and writes.
//!
//! It lives here, below the application layer, because it is pure in-memory; `sylvia-app`
//! re-exports it. The spatial methods (`spatial_*`, `record_shot_effect`, the player's
//! spatial view) come with the spatial engine.

use super::error::ExecError;
use crate::expr::value::{py_eq, Dict, Object, Value};
use std::sync::Arc;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{ParticipantMutation, StateMutation};

/// What the effect handlers and the tool executor ask of state (Python's duck-typed
/// `state_bridge`).
pub trait StateBridge {
    /// `entity_exists(entity_id)`: `"state"` always exists.
    fn entity_exists(&self, entity_id: &str) -> bool;
    /// `get_entity(entity_id)`: a detached copy; `KeyError` for an unknown ID.
    fn get_entity(&self, entity_id: &str) -> Result<Value, ExecError>;
    /// `participant_exists(participant_id)`.
    fn participant_exists(&self, participant_id: &str) -> bool;
    /// `get_participant(participant_id)`: a detached copy; `KeyError` for an unknown ID.
    fn get_participant(&self, participant_id: &str) -> Result<Value, ExecError>;
    /// `get_position(entity_id)`.
    fn get_position(&self, entity_id: &str) -> Option<Value>;
    /// `read_field(entity_id, field_path)`.
    fn read_field(&self, entity_id: &str, field_path: &str) -> Result<Value, ExecError>;
    /// `write_field(entity_id, field_path, value)`.
    fn write_field(&mut self, entity_id: &str, field_path: &str, value: Value) -> Result<(), ExecError>;
    /// `modify_field(entity_id, field_path, delta)`.
    fn modify_field(&mut self, entity_id: &str, field_path: &str, delta: i64) -> Result<i64, ExecError>;
    /// `create_entity(entity_id, value)`.
    fn create_entity(&mut self, entity_id: &Value, value: &Value) -> Result<(), ExecError>;
    /// `write_participant_field(participant_id, field_path, value)`.
    fn write_participant_field(
        &mut self,
        participant_id: &str,
        field_path: &str,
        value: Value,
    ) -> Result<(), ExecError>;
    /// The live combat dict (`combat_state`), which Python hands to tools by reference.
    fn combat_mut(&mut self) -> &mut Dict;
    /// `combat_state`, read only.
    fn combat(&self) -> &Dict;
}

/// `_mutable_json(value)`: mappings to `dict` with `str` keys, sequences to `list`, the rest
/// as is. Dice results stay objects.
pub fn mutable_json(value: &Value) -> Value {
    match value {
        Value::Dict(map) => {
            Value::dict(map.iter().map(|(key, item)| (key.clone(), mutable_json(item))).collect())
        }
        Value::List(items) | Value::Tuple(items) => Value::list(items.iter().map(mutable_json).collect()),
        other => other.clone(),
    }
}

/// `_json_safe(value)` as `mutations()` applies it: dataclasses as their fields, finite numbers,
/// text keys. A failure is Python's `PackageRuntimeError`.
pub fn json_safe(value: &Value) -> Result<Json, ExecError> {
    Ok(match value {
        Value::None => Json::Null,
        Value::Bool(flag) => Json::Bool(*flag),
        Value::Int(number) => Json::from(*number),
        Value::Float(number) => match serde_json::Number::from_f64(*number) {
            Some(number) => Json::Number(number),
            None => {
                return Err(ExecError::package_runtime("Runtime result type is not canonical JSON: float"))
            }
        },
        Value::Str(text) => Json::String(text.to_string()),
        Value::List(items) | Value::Tuple(items) => {
            Json::Array(items.iter().map(json_safe).collect::<Result<_, _>>()?)
        }
        Value::Dict(map) => {
            let mut object = JsonMap::new();
            for (key, item) in map.iter() {
                object.insert(key.clone(), json_safe(item)?);
            }
            Json::Object(object)
        }
        Value::Object(object) => json_safe(&object_as_dict(object))?,
        Value::Method(_) => {
            return Err(ExecError::package_runtime(
                "Runtime result type is not canonical JSON: builtin_function_or_method",
            ))
        }
    })
}

/// `dataclasses.asdict(object)`.
fn object_as_dict(object: &Object) -> Value {
    Value::dict(
        object
            .fields
            .iter()
            .map(|(name, item)| {
                let item = match item {
                    Value::Object(inner) => object_as_dict(inner),
                    Value::List(items) => Value::list(
                        items
                            .iter()
                            .map(|x| if let Value::Object(o) = x { object_as_dict(o) } else { x.clone() })
                            .collect(),
                    ),
                    other => other.clone(),
                };
                ((*name).to_owned(), item)
            })
            .collect(),
    )
}

/// `_nested_get(value, path)`.
pub fn nested_get(value: &Value, path: &str) -> Value {
    if path.is_empty() {
        return value.clone();
    }
    let mut current = value.clone();
    for part in path.split('.') {
        current = match &current {
            Value::Dict(map) => map.get(part).cloned().unwrap_or(Value::None),
            _ => return Value::None,
        };
    }
    current
}

/// `_nested_set(value, path, replacement)`: missing or non-dict steps become new dicts.
pub fn nested_set(value: &mut Dict, path: &str, replacement: Value) -> Result<(), ExecError> {
    if path.is_empty() {
        return Err(ExecError::package_runtime("Runtime state writes require a field path"));
    }
    let parts: Vec<&str> = path.split('.').collect();
    let mut current = value;
    for part in &parts[..parts.len() - 1] {
        let child = current.entry((*part).to_owned()).or_insert(Value::None);
        if !matches!(child, Value::Dict(_)) {
            *child = Value::dict(Dict::new());
        }
        let Value::Dict(map) = child else { unreachable!() };
        current = Arc::make_mut(map);
    }
    current.insert(parts[parts.len() - 1].to_owned(), replacement);
    Ok(())
}

/// `AuthorityMemoryStateBridge(namespace, authority_state, participants)` without its spatial
/// part.
#[derive(Debug, Clone)]
pub struct MemoryStateBridge {
    pub namespace: String,
    state: Dict,
    entities: Dict,
    combat: Dict,
    participants: Dict,
    before_state: Dict,
    before_entities: Dict,
    before_combat: Dict,
    before_participants: Dict,
}

impl MemoryStateBridge {
    /// `authority_state` holds `"{namespace}.state"`, `".entities"` and `".combat"`.
    pub fn new(
        namespace: &str,
        authority_state: &Dict,
        participants: Option<&Dict>,
    ) -> Result<Self, ExecError> {
        let root = |key: &str| -> Result<Dict, ExecError> {
            match authority_state.get(&format!("{namespace}.{key}")).map(mutable_json) {
                None => Ok(Dict::new()),
                Some(Value::Dict(map)) => Ok((*map).clone()),
                Some(_) => {
                    Err(ExecError::package_runtime(format!("Package runtime root '{key}' must be an object")))
                }
            }
        };
        let (state, entities, combat) = (root("state")?, root("entities")?, root("combat")?);
        let participants = match participants.map(|p| mutable_json(&Value::dict(p.clone()))) {
            None => Dict::new(),
            Some(Value::Dict(map)) if map.values().all(|value| matches!(value, Value::Dict(_))) => {
                (*map).clone()
            }
            Some(_) => {
                return Err(ExecError::package_runtime("Participant context must be an object of artifacts"))
            }
        };
        Ok(Self {
            namespace: namespace.to_owned(),
            before_state: state.clone(),
            before_entities: entities.clone(),
            before_combat: combat.clone(),
            before_participants: participants.clone(),
            state,
            entities,
            combat,
            participants,
        })
    }

    fn entity(&self, entity_id: &str) -> Result<&Dict, ExecError> {
        if entity_id == "state" {
            return Ok(&self.state);
        }
        match self.entities.get(entity_id) {
            Some(Value::Dict(map)) => Ok(map),
            _ => Err(ExecError::key_error(entity_id)),
        }
    }

    fn entity_mut(&mut self, entity_id: &str) -> Result<&mut Dict, ExecError> {
        if entity_id == "state" {
            return Ok(&mut self.state);
        }
        match self.entities.get_mut(entity_id) {
            Some(Value::Dict(map)) => Ok(Arc::make_mut(map)),
            _ => Err(ExecError::key_error(entity_id)),
        }
    }

    /// `mutations()`: each changed root as one `StateMutation`, in state, entities, combat order.
    pub fn mutations(&self) -> Result<Vec<StateMutation>, ExecError> {
        let mut changed = Vec::new();
        for (key, before, after) in [
            ("state", &self.before_state, &self.state),
            ("entities", &self.before_entities, &self.entities),
            ("combat", &self.before_combat, &self.combat),
        ] {
            let (before, after) = (Value::dict(before.clone()), Value::dict(after.clone()));
            if !py_eq(&after, &before) {
                changed.push(StateMutation {
                    namespace: self.namespace.clone(),
                    key: key.into(),
                    value: json_safe(&after)?,
                });
            }
        }
        Ok(changed)
    }

    /// `participant_mutations()`: each changed participant's `data`.
    pub fn participant_mutations(&self) -> Result<Vec<ParticipantMutation>, ExecError> {
        let mut changed = Vec::new();
        for (participant_id, participant) in &self.participants {
            let before = self.before_participants.get(participant_id).cloned().unwrap_or(Value::None);
            if py_eq(participant, &before) {
                continue;
            }
            let data = match participant {
                Value::Dict(map) => map.get("data").cloned().unwrap_or(Value::None),
                _ => Value::None,
            };
            match json_safe(&data)? {
                Json::Object(data) => {
                    changed.push(ParticipantMutation { participant_id: participant_id.clone(), data })
                }
                _ => return Err(ExecError::type_error("Safe payload must be a JSON object")),
            }
        }
        Ok(changed)
    }

    /// `take_damage(entity_id, amount)`.
    pub fn take_damage(&mut self, entity_id: &str, amount: i64) -> Result<i64, ExecError> {
        let amount = amount.max(0);
        let Value::Int(hp) = self.read_field(entity_id, "hp")? else {
            return Err(ExecError::package_runtime("Damage target has no integer hp"));
        };
        let replacement = (hp - amount).max(0);
        self.write_field(entity_id, "hp", Value::Int(replacement))?;
        Ok(replacement)
    }

    /// `heal(entity_id, amount)`.
    pub fn heal(&mut self, entity_id: &str, amount: i64) -> Result<i64, ExecError> {
        let (hp, maximum) = (self.read_field(entity_id, "hp")?, self.read_field(entity_id, "max_hp")?);
        let (Value::Int(hp), Value::Int(maximum)) = (hp, maximum) else {
            return Err(ExecError::package_runtime("Healing target has no integer hp/max_hp"));
        };
        let replacement = maximum.min(hp + amount.max(0));
        self.write_field(entity_id, "hp", Value::Int(replacement))?;
        Ok(replacement)
    }

    /// `add_condition(entity_id, condition)`.
    pub fn add_condition(&mut self, entity_id: &str, condition: &str) -> Result<(), ExecError> {
        let mut values = match self.read_field(entity_id, "conditions")? {
            Value::List(items) => (*items).clone(),
            _ => Vec::new(),
        };
        let item = Value::str(condition);
        if !values.iter().any(|value| py_eq(value, &item)) {
            values.push(item);
        }
        self.write_field(entity_id, "conditions", Value::list(values))
    }

    /// `remove_condition(entity_id, condition)`.
    pub fn remove_condition(&mut self, entity_id: &str, condition: &str) -> Result<(), ExecError> {
        let mut values = match self.read_field(entity_id, "conditions")? {
            Value::List(items) => (*items).clone(),
            _ => Vec::new(),
        };
        let item = Value::str(condition);
        if let Some(index) = values.iter().position(|value| py_eq(value, &item)) {
            values.remove(index);
        }
        self.write_field(entity_id, "conditions", Value::list(values))
    }

    /// `damage_ability(entity_id, ability, amount)`.
    pub fn damage_ability(&mut self, entity_id: &str, ability: &str, amount: i64) -> Result<i64, ExecError> {
        let path = format!("abilities.{ability}.score");
        let Value::Int(score) = self.read_field(entity_id, &path)? else {
            return Err(ExecError::package_runtime("Ability damage target has no integer score"));
        };
        let replacement = (score - amount.max(0)).max(0);
        self.write_field(entity_id, &path, Value::Int(replacement))?;
        Ok(replacement)
    }
}

/// `re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", entity_id)`.
fn entity_identity(text: &str) -> bool {
    let mut chars = text.chars();
    chars.next().is_some_and(|c| c.is_ascii_alphanumeric())
        && text.chars().count() <= 128
        && chars.all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | ':' | '-'))
}

impl StateBridge for MemoryStateBridge {
    fn entity_exists(&self, entity_id: &str) -> bool {
        entity_id == "state" || matches!(self.entities.get(entity_id), Some(Value::Dict(_)))
    }

    fn get_entity(&self, entity_id: &str) -> Result<Value, ExecError> {
        Ok(Value::dict(self.entity(entity_id)?.clone()))
    }

    fn participant_exists(&self, participant_id: &str) -> bool {
        matches!(self.participants.get(participant_id), Some(Value::Dict(_)))
    }

    fn get_participant(&self, participant_id: &str) -> Result<Value, ExecError> {
        match self.participants.get(participant_id) {
            Some(value @ Value::Dict(_)) => Ok(value.clone()),
            _ => Err(ExecError::key_error(participant_id)),
        }
    }

    fn get_position(&self, entity_id: &str) -> Option<Value> {
        match self.entities.get(entity_id) {
            Some(Value::Dict(map)) => match map.get("position") {
                Some(position @ Value::Dict(_)) => Some(position.clone()),
                _ => None,
            },
            _ => None,
        }
    }

    fn read_field(&self, entity_id: &str, field_path: &str) -> Result<Value, ExecError> {
        Ok(nested_get(&Value::dict(self.entity(entity_id)?.clone()), field_path))
    }

    fn write_field(&mut self, entity_id: &str, field_path: &str, value: Value) -> Result<(), ExecError> {
        let value = mutable_json(&value);
        nested_set(self.entity_mut(entity_id)?, field_path, value)
    }

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
        // `int(replacement)`.
        match replacement {
            Value::Int(value) => Ok(value),
            Value::Float(value) => super::error::float_to_int(value),
            _ => unreachable!(),
        }
    }

    fn create_entity(&mut self, entity_id: &Value, value: &Value) -> Result<(), ExecError> {
        let valid = matches!(entity_id, Value::Str(text)
            if entity_identity(text) && !matches!(&**text, "state" | "combat" | "entities" | "participants"));
        if !valid {
            return Err(ExecError::precondition(
                "valid_entity_identity",
                "A valid new entity ID is required.",
            ));
        }
        let Value::Str(entity_id) = entity_id else { unreachable!() };
        if self.entities.contains_key(&**entity_id) || self.participants.contains_key(&**entity_id) {
            return Err(ExecError::precondition(
                "entity_identity_absent",
                "This entity or participant already exists. Reuse its ID; its state was preserved.",
            ));
        }
        if !matches!(value, Value::Dict(_)) {
            return Err(ExecError::package_runtime("New entity state must be an object"));
        }
        super::shape::validate_shape(value)?;
        let stored = Value::from_json(&json_safe(value)?);
        self.entities.insert(entity_id.to_string(), mutable_json(&stored));
        Ok(())
    }

    fn write_participant_field(
        &mut self,
        participant_id: &str,
        field_path: &str,
        value: Value,
    ) -> Result<(), ExecError> {
        let namespace = self.namespace.clone();
        let Some(Value::Dict(participant)) = self.participants.get_mut(participant_id) else {
            return Err(ExecError::package_runtime("Participant mutation requires the owning Ruleset"));
        };
        if !matches!(participant.get("package_id"), Some(Value::Str(id)) if **id == *namespace) {
            return Err(ExecError::package_runtime("Participant mutation requires the owning Ruleset"));
        }
        let participant = Arc::make_mut(participant);
        let Some(Value::Dict(data)) = participant.get_mut("data").filter(|_| !field_path.is_empty()) else {
            return Err(ExecError::package_runtime("Participant mutation requires a data field"));
        };
        nested_set(Arc::make_mut(data), field_path, mutable_json(&value))
    }

    fn combat_mut(&mut self) -> &mut Dict {
        &mut self.combat
    }

    fn combat(&self) -> &Dict {
        &self.combat
    }
}
