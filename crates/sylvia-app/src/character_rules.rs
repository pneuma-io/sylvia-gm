//! `application/character_rules.py`: read-only character constraints for the bounded Ruleset
//! runtime.
//!
//! `validate_character_rules` admits a program through lane 2's
//! `sylvia_engine::ruleset::validate_effects`; `prepare_character_rules` and
//! `verify_character_rules` run it with lane 2's `sylvia_engine::exec::execute_effects`.

use sylvia_base::pystr::INT_RANGE_MESSAGE;
use sylvia_base::pyyaml::Data;
use sylvia_base::{pyjson, pystr, Json, JsonMap};
use sylvia_domain::error::{DomainError, DomainResult, RulesetDiagnostic};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::{py_eq, py_get_attr, py_item, py_iter, py_str};
use sylvia_engine::exec::context::{Extras, NestedMode};
use sylvia_engine::exec::effects::execute_effects;
use sylvia_engine::exec::{Combat, ExecError, ExecTool, ExecutionContext, ToolHost};
use sylvia_engine::expr::{Dict, ExecutionBudget, Value};
use sylvia_engine::pyerror::{PyError, PyErrorKind};
use sylvia_engine::ruleset::validate_effects;
use sylvia_engine::Dice;

/// `_HANDLERS`: the read-only runtime effects a character validation may use.
const HANDLERS: [&str; 6] = ["set", "list_push", "require", "condition", "for_each", "table_lookup"];
const INVALID: &str = "character_initial_state_validation_invalid";

/// `_reject(code, message)`.
fn reject(code: &str, message: &str) -> DomainError {
    DomainError::command_rejected_with(
        message,
        code,
        vec![RulesetDiagnostic {
            code: code.into(),
            phase: "character_initial_state".into(),
            message: message.into(),
            path: Some("initial_state_validation".into()),
            guidance: Some(
                "Correct the indicated character choice or the Ruleset's validation declaration.".into(),
            ),
            ..Default::default()
        }],
    )
}

/// `_LOCAL.fullmatch(text)`: `_[A-Za-z0-9_]+`.
fn is_local(text: &str) -> bool {
    text.strip_prefix('_').is_some_and(|rest| {
        !rest.is_empty() && rest.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
    })
}

/// A JSON value as `json.loads` gives it to the runtime validator. An integer beyond `i64` is
/// refused by the engine's declared limit (differences row 5).
fn to_data(value: &Json) -> DomainResult<Data> {
    Ok(match value {
        Json::Null => Data::None,
        Json::Bool(flag) => Data::Bool(*flag),
        Json::Number(number) if !number.is_f64() => {
            Data::Int(number.as_i64().ok_or_else(|| DomainError::value(INT_RANGE_MESSAGE))?)
        }
        Json::Number(number) => Data::Float(number.as_f64().unwrap_or_default()),
        Json::String(text) => Data::Str(text.clone()),
        Json::Array(items) => Data::List(items.iter().map(to_data).collect::<DomainResult<_>>()?),
        Json::Object(map) => Data::Dict(
            map.iter()
                .map(|(key, item)| Ok((Data::Str(key.clone()), to_data(item)?)))
                .collect::<DomainResult<_>>()?,
        ),
    })
}

/// A built-in exception the runtime validator lets through, as the class Python raises.
fn engine_error(error: PyError) -> DomainError {
    match error.kind {
        PyErrorKind::ValueError => DomainError::value(error.message),
        PyErrorKind::TypeError => DomainError::type_error(error.message),
        kind => DomainError::uncaught(kind.name(), error.message),
    }
}

/// The keys of `effect` that are among `names`, in `names`' order.
fn keys_among<'a>(effect: &JsonMap, names: &[&'a str]) -> Vec<&'a str> {
    names.iter().copied().filter(|name| effect.contains_key(*name)).collect()
}

/// `validate_character_rules(program)`: admit only local calculations, table reads and
/// requirements, with no effectful callbacks. `None` without a program.
pub fn validate_character_rules(program: Option<&Json>) -> DomainResult<Option<JsonMap>> {
    let Some(program) = program else { return Ok(None) };
    let Some(_) = program.as_object().filter(|map| {
        map.len() == 3 && ["description", "definitions", "effects"].iter().all(|key| map.contains_key(*key))
    }) else {
        return Err(reject(INVALID, "Character validation needs description, definitions and effects."));
    };
    let raw = canonical_json_bytes(program);
    if raw.len() > 1024 * 1024 {
        return Err(reject(INVALID, "Character validation exceeds its declaration size limit."));
    }
    // `json.loads(raw)`: the same value with every object's keys in sorted order.
    let value = read_back(raw);
    let description_ok =
        value["description"].as_str().is_some_and(|text| (1..=2000).contains(&pystr::len(text)));
    let effects = value["effects"].as_array().filter(|effects| !effects.is_empty());
    let (true, true, Some(effects)) = (description_ok, value["definitions"].is_object(), effects) else {
        return Err(reject(INVALID, "Character validation declarations are invalid."));
    };
    let empty = Json::Array(Vec::new());
    let mut pending: Vec<(&Json, usize)> = effects.iter().map(|effect| (effect, 0)).collect();
    let mut count = 0;
    while let Some((effect, depth)) = pending.pop() {
        count += 1;
        let effect = match effect.as_object() {
            Some(effect) if count <= 1024 && depth <= 32 => effect,
            _ => return Err(reject(INVALID, "Character validation exceeds its effect limit.")),
        };
        let mut handlers = keys_among(effect, &HANDLERS);
        if effect.get("condition").is_some_and(Json::is_string) && handlers.len() > 1 {
            handlers.retain(|handler| *handler != "condition");
        }
        let [handler] = handlers[..] else {
            return Err(reject(INVALID, "Character validation permits only read-only runtime effects."));
        };
        let mut allowed = vec![handler, "condition"];
        let mut branches: Vec<&Json> = Vec::new();
        if handler == "condition" {
            allowed.extend(["on_true", "on_false"]);
            if !effect[handler].is_string() {
                return Err(reject(INVALID, "Character validation uses flat conditional branches."));
            }
            branches.push(effect.get("on_true").unwrap_or(&empty));
            branches.push(effect.get("on_false").unwrap_or(&empty));
        } else {
            let Some(body) = effect[handler].as_object() else {
                return Err(reject(INVALID, "Character validation effect bodies must be objects."));
            };
            let local = match handler {
                "set" => body.get("field"),
                "list_push" => body.get("list"),
                "for_each" => {
                    branches.push(body.get("do").unwrap_or(&empty));
                    body.get("as")
                }
                "table_lookup" => {
                    allowed.push("store_as");
                    if !body
                        .get("table")
                        .and_then(Json::as_str)
                        .is_some_and(|table| table.starts_with("definitions."))
                    {
                        return Err(reject(
                            INVALID,
                            "Character validation tables must use local definitions.",
                        ));
                    }
                    effect.get("store_as")
                }
                _ => None,
            };
            if handler != "require" && !local.and_then(Json::as_str).is_some_and(is_local) {
                return Err(reject(INVALID, "Character validation may write only private local variables."));
            }
        }
        if effect.keys().any(|key| !allowed.contains(&key.as_str()))
            || branches.iter().any(|branch| !branch.is_array())
        {
            return Err(reject(INVALID, "Character validation has unsupported effect fields."));
        }
        for branch in branches {
            pending.extend(branch.as_array().into_iter().flatten().map(|nested| (nested, depth + 1)));
        }
    }
    let data: Vec<Data> = effects.iter().map(to_data).collect::<DomainResult<_>>()?;
    if !validate_effects("character_validation", &data).map_err(engine_error)?.is_empty() {
        return Err(reject(INVALID, "Character validation contains invalid runtime effects."));
    }
    Ok(Some(value))
}

/// `campaign_character_state(plan, namespace)`: copy only the selected Ruleset's declared
/// starting keys for campaign calculations, in canonical (sorted) key order.
pub fn campaign_character_state(plan: &Json, namespace: &str) -> DomainResult<JsonMap> {
    let initial_state = py_item(plan, "initial_state")?;
    let mechanics = match py_get_attr(initial_state, "mechanics")? {
        Some(mechanics) => py_iter(mechanics)?,
        None => Vec::new(),
    };
    let wanted = Json::String(namespace.to_owned());
    let mut result = JsonMap::new();
    for item in &mechanics {
        if py_eq(py_item(item, "namespace")?, &wanted) {
            let key = py_str(py_item(item, "key")?);
            result.insert(key, py_item(item, "value")?.clone());
        }
    }
    Ok(read_back(canonical_json_bytes(&Json::Object(result))))
}

/// `json.loads(canonical_json_bytes(mapping))`: an object in sorted key order.
fn read_back(canonical: Vec<u8>) -> JsonMap {
    let text = String::from_utf8(canonical).expect("canonical JSON is UTF-8");
    match pyjson::loads(&text) {
        Ok(Json::Object(map)) => map,
        _ => unreachable!("canonical JSON of an object reads back as one"),
    }
}

/// No tools: an admitted character validation runs no `call` effect.
struct NoTools {
    config: Value,
    definitions: Value,
}

impl ToolHost for NoTools {
    fn tool(&self, _name: &str) -> Option<&ExecTool> {
        None
    }
    fn ruleset_config(&self) -> &Value {
        &self.config
    }
    fn definitions(&self) -> &Value {
        &self.definitions
    }
}

/// `canonical_json_bytes(value)` of a runtime value, read back: finite JSON data only, else
/// `thaw_json_value`'s `TypeError`.
pub(crate) fn runtime_json(value: &Value) -> DomainResult<Json> {
    let refuse = || DomainError::type_error("Value is not JSON-compatible");
    let json = match value {
        Value::None => Json::Null,
        Value::Bool(flag) => Json::Bool(*flag),
        Value::Int(number) => Json::from(*number),
        Value::Float(number) => Json::Number(serde_json::Number::from_f64(*number).ok_or_else(refuse)?),
        Value::Str(text) => Json::String(text.to_string()),
        Value::List(items) | Value::Tuple(items) => {
            Json::Array(items.iter().map(runtime_json).collect::<DomainResult<_>>()?)
        }
        Value::Dict(map) => Json::Object(
            map.iter()
                .map(|(key, item)| Ok((key.clone(), runtime_json(item)?)))
                .collect::<DomainResult<_>>()?,
        ),
        Value::Object(_) | Value::Method(_) => return Err(refuse()),
    };
    Ok(json)
}

/// The runtime's dict of a JSON object.
pub(crate) fn runtime_dict(value: &Json) -> Dict {
    match Value::from_json(value) {
        Value::Dict(map) => (*map).clone(),
        _ => Dict::new(),
    }
}

/// `prepare_character_rules(...)`'s `check`: an admitted program with its fixed inputs.
pub struct PreparedCharacterRules {
    checked: Option<JsonMap>,
    participant: Json,
    setting_data: Json,
    campaign_state: Json,
}

impl PreparedCharacterRules {
    /// `check(state)`: run the program on fresh private copies and return its local results,
    /// in canonical key order. Without a program, an empty result.
    pub fn check(&self, state: &Json) -> DomainResult<JsonMap> {
        let Some(checked) = &self.checked else { return Ok(JsonMap::new()) };
        let mut inputs = JsonMap::new();
        inputs.insert("participant".into(), self.participant.clone());
        inputs.insert("state".into(), state.clone());
        inputs.insert("setting_data".into(), self.setting_data.clone());
        inputs.insert("campaign_state".into(), self.campaign_state.clone());
        let inputs = Json::Object(read_back(canonical_json_bytes(&Json::Object(inputs))));
        // Local lists can alias table values or literal effects. Keep every execution owned.
        let definitions = Value::from_json(&checked["definitions"]);
        let effects = Value::from_json(&checked["effects"]);
        let budget = ExecutionBudget::default();
        let mut dice = Dice::default();
        let host = NoTools { config: Value::dict(Dict::new()), definitions: definitions.clone() };
        let mut context = ExecutionContext {
            params: runtime_dict(&inputs),
            vars: Dict::new(),
            entities: Dict::new(),
            ruleset_config: Value::dict(Dict::new()),
            definitions,
            combat: Combat::own(Dict::new()),
            state_bridge: None,
            current_tool: String::new(),
            extras: Extras::default(),
            budget: &budget,
            dice: &mut dice,
            choice: None,
            list_history: Vec::new(),
            procedure_executor: None,
            nested_mode: NestedMode::Package,
        };
        match execute_effects(&effects, &mut context, &host) {
            Ok(()) => {}
            Err(ExecError::PreCondition { message, .. }) => {
                return Err(reject("character_initial_state_rule_rejected", &message))
            }
            Err(ExecError::Effect { .. }) => {
                return Err(reject(
                    "character_initial_state_validation_failed",
                    "The Ruleset could not validate the character's declared choices.",
                ))
            }
            Err(other) => return Err(DomainError::uncaught(other.class_name(), other.message())),
        }
        let vars = runtime_json(&Value::dict(context.vars))?;
        Ok(read_back(canonical_json_bytes(&vars)))
    }
}

/// `prepare_character_rules(participant, program, setting_data, campaign_state=...)`: admit a
/// program once; check each changing state with fresh private runtime data.
pub fn prepare_character_rules(
    participant: &Json,
    program: Option<&Json>,
    setting_data: Option<&Json>,
    campaign_state: Option<&Json>,
) -> DomainResult<PreparedCharacterRules> {
    let empty = || Json::Object(JsonMap::new());
    Ok(PreparedCharacterRules {
        checked: validate_character_rules(program)?,
        participant: participant.clone(),
        setting_data: setting_data.cloned().unwrap_or_else(empty),
        campaign_state: campaign_state.cloned().unwrap_or_else(empty),
    })
}

/// `verify_character_rules(participant, state, program, setting_data, campaign_state=...)`:
/// check private copies and return local results without mutation or external access.
pub fn verify_character_rules(
    participant: &Json,
    state: &Json,
    program: Option<&Json>,
    setting_data: Option<&Json>,
    campaign_state: Option<&Json>,
) -> DomainResult<JsonMap> {
    prepare_character_rules(participant, program, setting_data, campaign_state)?.check(state)
}
