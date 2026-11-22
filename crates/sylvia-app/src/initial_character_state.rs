//! `application/initial_character_state.py`, its admission half: the checks a Ruleset's
//! initial character declaration must pass before its package is installed, and the authored
//! input schema.
//!
//! Ported: `validate_initial_character_contract` (`_calculation_contract`,
//! `_schema_declares_path`, `_guard_expression`, with `character_rules::validate_character_rules`)
//! and `initial_character_input_schema` (`_authored_schema`). Not yet ported: the
//! materialize and verify functions, which run the character rules through
//! `prepare_character_rules` and `verify_character_rules`, waiting on lane 2's effect executor.

use crate::character_rules::validate_character_rules;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::error::{DomainError, DomainResult, RulesetDiagnostic};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::py_eq;
use sylvia_domain::schema_admission::validate_json_schema;
use sylvia_engine::expr::parser::{parse_expression, Node};

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
fn guard_expression(expression: &str, path: &str) -> DomainResult<()> {
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
    Ok(())
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
