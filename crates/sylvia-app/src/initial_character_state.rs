//! `application/initial_character_state.py`: materialize and verify Ruleset-declared initial
//! character state, and the admission checks for its declaration.
//!
//! The calculations evaluate with lane 2's expression evaluator. `_RequiredInputs` is the
//! evaluator's `required_inputs` flag: a missing key under the creation inputs raises
//! `FieldNotFoundError` instead of giving `None`. Only a `merge(...)` result differs, which Python
//! leaves a plain dict: differences row NEXT (merge results in creation calculations).

use crate::character_rules::{
    prepare_character_rules, runtime_json, validate_character_rules, verify_character_rules,
};
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::error::{DomainError, DomainResult, RulesetDiagnostic};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::package_library::ParticipantArtifactDeclaration;
use sylvia_domain::pyops::py_eq;
use sylvia_domain::schema_admission::{validate_json_schema, validate_parameters};
use sylvia_engine::expr::parser::{parse_expression, Node};
use sylvia_engine::expr::{
    evaluate, Dict, EvalContext, EvalError, ExecutionBudget, ExpressionErrorKind, Value,
};

const MAX_CALCULATIONS: usize = 128;
const READABLE_ROOTS: [&str; 5] = ["participant", "state", "setting_data", "rules", "campaign_state"];
const DECLARABLE_TYPES: [&str; 7] = ["integer", "number", "string", "boolean", "null", "array", "object"];

/// `_rejection(code, message, path=..., guidance=...)`.
fn rejection(code: &str, message: &str, path: &str, guidance: Option<&str>) -> DomainError {
    DomainError::command_rejected_with(
        "Initial character state is invalid",
        code,
        vec![RulesetDiagnostic {
            code: code.into(),
            phase: "character_initial_state".into(),
            message: message.into(),
            path: Some(path.into()),
            guidance: guidance.map(str::to_owned),
            ..Default::default()
        }],
    )
}

/// `_schema_rejection(error)`: field-level corrections from a `MechanicsRejected`, or one
/// schema correction.
fn schema_rejection(error: &DomainError) -> DomainError {
    const CODE: &str = "character_initial_state_schema_invalid";
    let issues = error.mechanics().map(|details| details.validation_issues.as_slice()).unwrap_or_default();
    let diagnostics = if error.python_class() == "MechanicsRejected" && !issues.is_empty() {
        issues
            .iter()
            .take(16)
            .map(|issue| RulesetDiagnostic {
                code: CODE.into(),
                phase: "character_initial_state".into(),
                message: format!("The value at {} does not match the declared state schema.", issue.path),
                path: Some(issue.path.clone()),
                expected: issue.expected_type.clone(),
                actual: issue.actual_type.clone(),
                guidance: Some("Supply the required authored input with the declared JSON type.".into()),
                ..Default::default()
            })
            .collect()
    } else {
        vec![RulesetDiagnostic {
            code: CODE.into(),
            phase: "character_initial_state".into(),
            message: "The Ruleset's entity state schema is invalid.".into(),
            path: Some("entity_state_schema".into()),
            guidance: Some("Correct the Ruleset's declared entity state schema.".into()),
            ..Default::default()
        }]
    };
    DomainError::command_rejected_with(
        "Initial character state does not match its Ruleset schema",
        CODE,
        diagnostics,
    )
}

/// `_FIELD_PATH.fullmatch(text)`: `[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*`.
fn is_field_path(text: &str) -> bool {
    text.split('.').all(|part| {
        let mut chars = part.chars();
        chars.next().is_some_and(|first| first.is_ascii_alphabetic() || first == '_')
            && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
    })
}

/// `_schema_declares_path(schema, path)`: an explicit, typed declaration of every part.
fn schema_declares_path(schema: &JsonMap, path: &str) -> bool {
    let mut current = schema;
    for part in path.split('.') {
        if !py_eq(current.get("type").unwrap_or(&Json::Null), &Json::from("object")) {
            return false;
        }
        let Some(next) = current
            .get("properties")
            .and_then(Json::as_object)
            .and_then(|p| p.get(part))
            .and_then(Json::as_object)
        else {
            return false;
        };
        current = next;
    }
    match current.get("type") {
        // The schema is already admitted. A calculation may replace a nullable field, but the
        // traversal above does not invent children inside an empty object.
        Some(Json::Array(kinds)) => {
            kinds.len() == 2 && kinds.iter().any(|kind| py_eq(kind, &Json::from("null")))
        }
        Some(Json::String(kind)) => DECLARABLE_TYPES.contains(&kind.as_str()),
        _ => false,
    }
}

/// `_expression_nodes(node)`: the nodes directly inside one node.
fn expression_nodes(node: &Node) -> Vec<&Node> {
    match node {
        Node::Unary(_, operand) => vec![operand],
        Node::Binary(_, left, right) => vec![left, right],
        Node::Call(_, args) => args.iter().collect(),
        Node::Field(segments) => segments.iter().filter_map(|(_, index)| index.as_deref()).collect(),
        Node::Literal(_) => Vec::new(),
    }
}

/// `_guard_expression(expression, path=...)`: the existing expression language with only
/// creation input roots readable.
fn guard_expression(expression: &str, path: &str) -> DomainResult<Node> {
    let root = parse_expression(expression).map_err(|_| {
        rejection(
            "character_initial_state_expression_invalid",
            "The Ruleset's initial state expression is invalid.",
            path,
            Some("Correct the expression using the existing Ruleset expression syntax."),
        )
    })?;
    let mut pending = vec![&root];
    while let Some(node) = pending.pop() {
        if let Node::Field(segments) = node {
            if !READABLE_ROOTS.contains(&segments[0].0.as_str()) {
                return Err(rejection(
                    "character_initial_state_expression_invalid",
                    "Initial state expressions may read only participant, state, setting_data, read-only \
                     campaign_state and rules results.",
                    path,
                    None,
                ));
            }
        }
        pending.extend(expression_nodes(node));
    }
    Ok(root)
}

/// `_calculation_contract(schema, calculations)`: the admitted `(field, value, condition)`
/// calculations, in order.
fn calculation_contract(
    schema: &JsonMap,
    calculations: &[Json],
) -> DomainResult<Vec<(String, String, Option<String>)>> {
    if let Err(error) = validate_json_schema(schema, "Initial entity state schema") {
        if matches!(error.python_class(), "CommandRejected" | "MechanicsRejected") {
            return Err(schema_rejection(&error));
        }
        return Err(error);
    }
    if !py_eq(schema.get("type").unwrap_or(&Json::Null), &Json::from("object")) {
        return Err(rejection(
            "character_initial_state_schema_invalid",
            "The initial entity state schema must describe an object.",
            "entity_state_schema.type",
            None,
        ));
    }
    if calculations.len() > MAX_CALCULATIONS {
        return Err(rejection(
            "character_initial_state_calculations_invalid",
            "Initial state calculations must be a bounded ordered array.",
            "initial_state_calculations",
            None,
        ));
    }
    let mut admitted = Vec::new();
    let mut targets: Vec<String> = Vec::new();
    for (index, raw) in calculations.iter().enumerate() {
        let base = format!("initial_state_calculations[{index}]");
        let invalid = |message: &str, path: &str| {
            rejection("character_initial_state_calculation_invalid", message, path, None)
        };
        let Some(raw) = raw.as_object().filter(|raw| {
            raw.contains_key("set") && (raw.len() == 1 || (raw.len() == 2 && raw.contains_key("condition")))
        }) else {
            return Err(invalid(
                "Each calculation must contain exactly set and an optional condition.",
                &base,
            ));
        };
        let setting = raw["set"].as_object().filter(|setting| {
            setting.len() == 2 && setting.contains_key("field") && setting.contains_key("value")
        });
        let field =
            setting.and_then(|setting| setting["field"].as_str()).filter(|field| is_field_path(field));
        let value = setting.and_then(|setting| setting["value"].as_str()).filter(|value| !value.is_empty());
        let condition = raw.get("condition");
        let condition_ok =
            condition.is_none_or(|condition| condition.as_str().is_some_and(|text| !text.is_empty()));
        let (Some(field), Some(value), true) = (field, value, condition_ok) else {
            return Err(invalid("The calculation field and expressions are invalid.", &base));
        };
        if !schema_declares_path(schema, field) {
            return Err(invalid(
                "A calculated field must be explicitly declared by the entity state schema.",
                &format!("{base}.set.field"),
            ));
        }
        if targets.iter().any(|target| {
            field.starts_with(&format!("{target}.")) || target.starts_with(&format!("{field}."))
        }) {
            return Err(invalid(
                "Calculated fields cannot overlap a calculated parent or child field.",
                &format!("{base}.set.field"),
            ));
        }
        if !targets.iter().any(|target| target == field) {
            targets.push(field.to_owned());
        }
        guard_expression(value, &format!("{base}.set.value"))?;
        let condition = condition.and_then(Json::as_str);
        if let Some(condition) = condition {
            guard_expression(condition, &format!("{base}.condition"))?;
        }
        admitted.push((field.to_owned(), value.to_owned(), condition.map(str::to_owned)));
    }
    Ok(admitted)
}

/// `validate_initial_character_contract(entity_state_schema, initial_state_calculations,
/// initial_state_validation)`: check the whole declaration before admitting a usable Ruleset
/// package.
pub fn validate_initial_character_contract(
    entity_state_schema: &JsonMap,
    initial_state_calculations: &[Json],
    initial_state_validation: Option<&Json>,
) -> DomainResult<()> {
    calculation_contract(entity_state_schema, initial_state_calculations)?;
    // JSON `null` is Python's `None`: no character rules.
    validate_character_rules(initial_state_validation.filter(|value| !value.is_null()))?;
    Ok(())
}

/// `_authored_schema(schema, calculated)`: calculated paths removed from `required` lists,
/// their type declarations kept; re-read from canonical bytes, so keys are sorted.
fn authored_schema(schema: &JsonMap, calculated: &[String]) -> JsonMap {
    let text = String::from_utf8(canonical_json_bytes(&Json::Object(schema.clone())))
        .expect("canonical JSON is UTF-8");
    let Ok(Json::Object(mut result)) = pyjson::loads(&text) else {
        unreachable!("an object reads back as one")
    };
    for path in calculated {
        let parts: Vec<&str> = path.split('.').collect();
        let mut current: Option<&mut JsonMap> = Some(&mut result);
        for (index, part) in parts.iter().enumerate() {
            let Some(object) = current.take() else { break };
            if let Some(Json::Array(required)) = object.get_mut("required") {
                if let Some(at) = required.iter().position(|item| py_eq(item, &Json::from(*part))) {
                    required.remove(at);
                }
            }
            if index == parts.len() - 1 {
                break;
            }
            current = object
                .get_mut("properties")
                .and_then(Json::as_object_mut)
                .and_then(|p| p.get_mut(*part))
                .and_then(Json::as_object_mut);
        }
    }
    result
}

/// `initial_character_input_schema(entity_state_schema, initial_state_calculations)`: the
/// authored input shape, without the fields Ruleset calculations own.
pub fn initial_character_input_schema(
    entity_state_schema: &JsonMap,
    initial_state_calculations: &[Json],
) -> DomainResult<JsonMap> {
    let calculations = calculation_contract(entity_state_schema, initial_state_calculations)?;
    let fields: Vec<String> = calculations.into_iter().map(|(field, _, _)| field).collect();
    let mut result = authored_schema(entity_state_schema, &fields);
    for field in &fields {
        let parts: Vec<&str> = field.split('.').collect();
        let mut current = &mut result;
        for part in &parts[..parts.len() - 1] {
            current = current
                .get_mut("properties")
                .and_then(Json::as_object_mut)
                .and_then(|p| p.get_mut(*part))
                .and_then(Json::as_object_mut)
                .ok_or_else(|| DomainError::uncaught("KeyError", sylvia_base::pystr::repr(part)))?;
        }
        if let Some(properties) = current.get_mut("properties").and_then(Json::as_object_mut) {
            properties.shift_remove(parts[parts.len() - 1]);
        }
    }
    Ok(result)
}

type Calculation = (String, String, Option<String>);

/// `json.loads(canonical_json_bytes(value))`: the value in canonical (sorted) key order.
fn canonical(value: &Json) -> Json {
    let text = String::from_utf8(canonical_json_bytes(value)).expect("canonical JSON is UTF-8");
    pyjson::loads(&text).expect("canonical JSON reads back")
}

fn canonical_object(value: &Json) -> JsonMap {
    match canonical(value) {
        Json::Object(map) => map,
        _ => JsonMap::new(),
    }
}

/// `_reads_root(node, name)`: whether any field read starts at `name`.
fn reads_root(node: &Node, name: &str) -> bool {
    let mut pending = vec![node];
    while let Some(current) = pending.pop() {
        if matches!(current, Node::Field(segments) if segments[0].0 == name) {
            return true;
        }
        pending.extend(expression_nodes(current));
    }
    false
}

/// `_read_path(root, path)`, with `None` for Python's missing sentinel; `_path_present` is
/// whether it is `Some`.
fn read_path<'a>(root: &'a JsonMap, path: &str) -> Option<&'a Json> {
    let mut parts = path.split('.');
    let mut current = root.get(parts.next()?)?;
    for part in parts {
        current = current.as_object()?.get(part)?;
    }
    Some(current)
}

/// `_remove_path(root, path)`.
fn remove_path(root: &mut JsonMap, path: &str) {
    let parts: Vec<&str> = path.split('.').collect();
    let mut current = root;
    for part in &parts[..parts.len() - 1] {
        match current.get_mut(*part) {
            Some(Json::Object(next)) => current = next,
            _ => return,
        }
    }
    current.shift_remove(parts[parts.len() - 1]);
}

/// `_set_path(root, path, value)`: missing or null parents become objects.
fn set_path(root: &mut JsonMap, path: &str, value: Json) -> DomainResult<()> {
    let parts: Vec<&str> = path.split('.').collect();
    let mut current = root;
    for part in &parts[..parts.len() - 1] {
        let existing = current.entry(part.to_string()).or_insert(Json::Null);
        if existing.is_null() {
            *existing = Json::Object(JsonMap::new());
        }
        let Json::Object(next) = existing else {
            return Err(rejection(
                "character_initial_state_calculation_failed",
                "A calculated field's parent is not an object.",
                &format!("entity_state.{path}"),
                None,
            ));
        };
        current = next;
    }
    current.insert(parts[parts.len() - 1].to_owned(), value);
    Ok(())
}

/// How `_calculate` treats an exception: `CommandRejected` passes through, a missing input and
/// the calculation classes (`ExpressionError`, `TypeError`, `ValueError`, `ArithmeticError`)
/// become rejections, anything else escapes.
enum Failure {
    Missing,
    Calculation,
    Escape,
}

fn failure_of(class: &str, is_value_error: bool) -> Failure {
    match class {
        "FieldNotFoundError" => Failure::Missing,
        "ExpressionError" | "TypeError" | "ValueError" | "ZeroDivisionError" | "OverflowError"
        | "FloatingPointError" | "ArithmeticError" => Failure::Calculation,
        _ if is_value_error => Failure::Calculation,
        _ => Failure::Escape,
    }
}

fn calculation_error(failure: Failure, path: &str, escape: DomainError) -> DomainError {
    match failure {
        Failure::Missing => rejection(
            "character_initial_state_input_missing",
            "A required expression input is unavailable.",
            path,
            Some("Supply the missing authored input or correct the calculation."),
        ),
        Failure::Calculation => rejection(
            "character_initial_state_calculation_failed",
            "The Ruleset could not calculate the declared starting value.",
            path,
            Some("Supply every required input and correct the calculation."),
        ),
        Failure::Escape => escape,
    }
}

fn is_command_rejected(error: &DomainError) -> bool {
    matches!(error.python_class(), "CommandRejected" | "MechanicsRejected")
}

/// A character-rules check inside a calculation: `CommandRejected` as it is, other classes as
/// `_calculate` catches them.
fn rules_error(error: DomainError, path: &str) -> DomainError {
    if is_command_rejected(&error) {
        return error;
    }
    let failure = failure_of(error.python_class(), error.is_value_error());
    calculation_error(failure, path, error)
}

fn eval_error(error: EvalError, path: &str) -> DomainError {
    let escape = DomainError::uncaught(error.class_name(), error.to_string());
    let failure = match &error {
        EvalError::Expression(inner) if inner.kind == ExpressionErrorKind::FieldNotFound => Failure::Missing,
        EvalError::Expression(_) => Failure::Calculation,
        EvalError::Py(inner) => failure_of(inner.kind.name(), false),
    };
    calculation_error(failure, path, escape)
}

/// `_calculate(...)`: apply each calculation in order to a copy of the authored state.
fn calculate(
    participant: &Json,
    authored_state: &Json,
    calculations: &[Calculation],
    setting_data: Option<&Json>,
    validation: Option<&Json>,
    campaign_state: Option<&Json>,
) -> DomainResult<JsonMap> {
    let mut state = canonical_object(authored_state);
    let participant_inputs = Value::from_json(&canonical(participant));
    let empty = Json::Object(JsonMap::new());
    let setting_inputs = Value::from_json(&canonical(setting_data.unwrap_or(&empty)));
    let campaign_inputs = Value::from_json(&canonical(campaign_state.unwrap_or(&empty)));
    let check_rules = prepare_character_rules(participant, validation, setting_data, campaign_state)?;
    let (vars, entities, none) = (Dict::new(), Dict::new(), Value::None);
    for (index, (field, expression, condition)) in calculations.iter().enumerate() {
        let base = format!("initial_state_calculations[{index}]");
        let value_path = format!("{base}.set.value");
        let condition_path = format!("{base}.condition");
        let state_json = Json::Object(state.clone());
        let mut params = Dict::new();
        params.insert("participant".into(), participant_inputs.clone());
        params.insert("state".into(), Value::from_json(&state_json));
        params.insert("setting_data".into(), setting_inputs.clone());
        params.insert("rules".into(), Value::dict(Dict::new()));
        params.insert("campaign_state".into(), campaign_inputs.clone());
        let rules = |path: &str| -> DomainResult<Value> {
            let results = check_rules.check(&state_json).map_err(|error| rules_error(error, path))?;
            Ok(Value::from_json(&Json::Object(results)))
        };
        let budget = ExecutionBudget::default();
        let evaluate_in = |node: &Node, params: &Dict, path: &str| -> DomainResult<Value> {
            let context = EvalContext {
                params,
                vars: &vars,
                entities: &entities,
                combat_state: &none,
                ruleset_config: &none,
                definitions: &none,
                budget: Some(&budget),
                required_inputs: true,
            };
            evaluate(node, &context).map_err(|error| eval_error(error, path))
        };
        if let Some(condition) = condition {
            let node = guard_expression(condition, &condition_path)?;
            if reads_root(&node, "rules") {
                params.insert("rules".into(), rules(&condition_path)?);
            }
            if !evaluate_in(&node, &params, &condition_path)?.truthy() {
                continue;
            }
        }
        let node = guard_expression(expression, &value_path)?;
        if reads_root(&node, "rules") && !params["rules"].truthy() {
            params.insert("rules".into(), rules(&value_path)?);
        }
        let value = evaluate_in(&node, &params, &value_path)?;
        let finite = || {
            rejection(
                "character_initial_state_calculation_failed",
                "A calculation must produce finite JSON data within the Character File limit.",
                &value_path,
                None,
            )
        };
        let json = runtime_json(&value).map_err(|_| finite())?;
        if canonical_json_bytes(&json).len() > 256 * 1024 {
            return Err(finite());
        }
        set_path(&mut state, field, canonical(&json))?;
    }
    Ok(state)
}

/// `validate_parameters(value, schema)` with `CommandRejected` reported as `_schema_rejection`.
fn validate_state(value: &JsonMap, schema: &JsonMap) -> DomainResult<()> {
    validate_parameters(value, schema).map_err(|error| {
        if is_command_rejected(&error) {
            schema_rejection(&error)
        } else {
            error
        }
    })
}

/// The declaration's validation program; JSON `null` is Python's `None`.
fn program(validation: Option<&Json>) -> Option<&Json> {
    validation.filter(|value| !value.is_null())
}

/// `materialize_initial_character_state(participant, authored_state, ...)`: calculate
/// Ruleset-owned outputs and validate one complete initial state.
pub fn materialize_initial_character_state(
    participant: &Json,
    authored_state: &JsonMap,
    entity_state_schema: &JsonMap,
    initial_state_calculations: &[Json],
    initial_state_validation: Option<&Json>,
    setting_data: Option<&Json>,
) -> DomainResult<JsonMap> {
    let calculations = calculation_contract(entity_state_schema, initial_state_calculations)?;
    for (field, _, _) in &calculations {
        if read_path(authored_state, field).is_some() {
            return Err(rejection(
                "character_initial_state_calculated_field_authored",
                "A Ruleset-calculated field was supplied as authored input.",
                &format!("entity_state.{field}"),
                Some("Remove the calculated field and let the Ruleset produce it."),
            ));
        }
    }
    let fields: Vec<String> = calculations.iter().map(|(field, _, _)| field.clone()).collect();
    validate_state(authored_state, &authored_schema(entity_state_schema, &fields))?;
    let validation = program(initial_state_validation);
    let authored = Json::Object(authored_state.clone());
    let completed = calculate(participant, &authored, &calculations, setting_data, validation, None)?;
    validate_state(&completed, entity_state_schema)?;
    verify_character_rules(participant, &Json::Object(completed.clone()), validation, setting_data, None)?;
    Ok(completed)
}

/// `verify_initial_character_state(participant, complete_state, ...)`: recompute calculated
/// fields and reject a stale or inconsistent saved state.
pub fn verify_initial_character_state(
    participant: &Json,
    complete_state: &JsonMap,
    entity_state_schema: &JsonMap,
    initial_state_calculations: &[Json],
    initial_state_validation: Option<&Json>,
    setting_data: Option<&Json>,
) -> DomainResult<JsonMap> {
    let calculations = calculation_contract(entity_state_schema, initial_state_calculations)?;
    let original = canonical_object(&Json::Object(complete_state.clone()));
    let mut authored = original.clone();
    for (field, _, _) in &calculations {
        remove_path(&mut authored, field);
    }
    let validation = program(initial_state_validation);
    let expected =
        calculate(participant, &Json::Object(authored), &calculations, setting_data, validation, None)?;
    for (field, _, _) in &calculations {
        let matches = match (read_path(&original, field), read_path(&expected, field)) {
            (None, None) => true,
            (Some(saved), Some(recomputed)) => py_eq(saved, recomputed),
            _ => false,
        };
        if !matches {
            return Err(rejection(
                "character_initial_state_calculated_value_mismatch",
                "A saved Ruleset-calculated field does not match its declared calculation.",
                &format!("entity_state.{field}"),
                Some("Repair the Character File through Builder before using it."),
            ));
        }
    }
    validate_state(&original, entity_state_schema)?;
    verify_character_rules(participant, &Json::Object(original.clone()), validation, setting_data, None)?;
    Ok(original)
}

/// `initialize_campaign_character_state(participant, state, declaration, campaign_state,
/// setting_data)`: apply only the calculations that read `campaign_state`, preserving injuries
/// and source cards.
pub fn initialize_campaign_character_state(
    participant: &Json,
    state: &JsonMap,
    declaration: &ParticipantArtifactDeclaration,
    campaign_state: &Json,
    setting_data: Option<&Json>,
) -> DomainResult<JsonMap> {
    let Some(schema) = &declaration.entity_state_schema else {
        return Ok(canonical_object(&Json::Object(state.clone())));
    };
    let calculations: Vec<Json> =
        declaration.initial_state_calculations.iter().cloned().map(Json::Object).collect();
    let selected: Vec<Calculation> = calculation_contract(schema, &calculations)?
        .into_iter()
        .filter(|(_, value, condition)| {
            [Some(value.as_str()), condition.as_deref()].into_iter().flatten().any(|expression| {
                parse_expression(expression).is_ok_and(|node| reads_root(&node, "campaign_state"))
            })
        })
        .collect();
    let validation = declaration.initial_state_validation.clone().map(Json::Object);
    calculate(
        participant,
        &Json::Object(state.clone()),
        &selected,
        setting_data,
        validation.as_ref(),
        Some(campaign_state),
    )
}

/// `verify_placed_character(participant, state, declaration, setting_data)`: recheck fields and
/// choices after placement, preserving authored injuries and positions.
pub fn verify_placed_character(
    participant: &Json,
    state: &JsonMap,
    declaration: &ParticipantArtifactDeclaration,
    setting_data: Option<&Json>,
) -> DomainResult<()> {
    if let Some(schema) = &declaration.entity_state_schema {
        // Placement may add runtime fields such as token IDs and positions. Existing character
        // fields still retain their declared types, ranges and nested extension policies.
        let mut schema = schema.clone();
        schema.insert("additionalProperties".into(), Json::Bool(true));
        validate_state(state, &schema)?;
    }
    let validation = declaration.initial_state_validation.clone().map(Json::Object);
    verify_character_rules(
        participant,
        &Json::Object(state.clone()),
        validation.as_ref(),
        setting_data,
        None,
    )?;
    Ok(())
}
