//! `application/result_card_admission.py`: authoring-time result-card and activity-label
//! checks, with field-level diagnostics that never copy the rejected value.

use sylvia_base::pystr::strip;
use sylvia_base::Json;
use sylvia_domain::content::{validate_content_shape, ContentShapeError};
use sylvia_domain::error::RulesetDiagnostic;
use sylvia_engine::ruleset::pydata::str_is_alnum;

const FILE: &str = "runtime/ruleset.yaml";

fn diagnostic(path: &str, message: &str, guidance: &str) -> RulesetDiagnostic {
    RulesetDiagnostic {
        code: "result_card_declaration_invalid".into(),
        phase: "runtime".into(),
        message: message.into(),
        file: Some(FILE.into()),
        path: Some(path.into()),
        package_id: None,
        item_id: None,
        expected: None,
        actual: None,
        guidance: Some(guidance.into()),
    }
}

/// `validate_activity_label(value, path=...)`: a short literal label or one public path.
pub fn validate_activity_label(value: &Json, path: &str) -> Result<(), ContentShapeError> {
    if let Json::String(text) = value {
        if !strip(text).is_empty() && text.chars().count() <= 80 {
            return Ok(());
        }
    }
    if let Json::Object(object) = value {
        if object.len() == 1 {
            if let Some(Json::String(source)) = object.get("path") {
                if (1..=256).contains(&source.chars().count()) {
                    let issues = value_issues(value, path);
                    return if issues.is_empty() { Ok(()) } else { Err(ContentShapeError::new(issues)) };
                }
            }
        }
    }
    Err(ContentShapeError::new(vec![diagnostic(
        path,
        "An activity label is 1–80 characters or a public result/parameter path.",
        "Supply a neutral routine-operation label; use a result card for meaningful outcomes.",
    )]))
}

/// `_full_path(prefix, local)`.
fn full_path(prefix: &str, local: &str) -> String {
    match local.strip_prefix('$') {
        Some(rest) => format!("{prefix}{rest}"),
        None => format!("{prefix}.{local}"),
    }
}

/// `_value_issues(value, path)`. JSON holds no non-finite number, so that branch cannot occur.
fn value_issues(value: &Json, path: &str) -> Vec<RulesetDiagnostic> {
    match value {
        Json::Array(items) => items
            .iter()
            .enumerate()
            .flat_map(|(index, item)| value_issues(item, &format!("{path}[{index}]")))
            .collect(),
        Json::Object(object) => {
            let Some(Json::String(source)) = object.get("path") else {
                return Vec::new();
            };
            let parts: Vec<&str> = source.split('.').collect();
            if parts.len() > 16 || parts.iter().any(|part| part.is_empty()) {
                return vec![diagnostic(
                    &format!("{path}.path"),
                    "Card paths use one to 16 nonempty dot-separated segments.",
                    "Shorten the path and remove empty segments.",
                )];
            }
            if !matches!(parts[0], "parameters" | "context") {
                return vec![diagnostic(
                    &format!("{path}.path"),
                    "Card paths begin with parameters or context.",
                    "Reference an admitted tool parameter or public result-context field.",
                )];
            }
            Vec::new()
        }
        _ => Vec::new(),
    }
}

/// `_equation_issues(equation, path)`.
fn equation_issues(equation: Option<&Json>, path: &str) -> Vec<RulesetDiagnostic> {
    let Some(Json::Object(equation)) = equation else {
        return Vec::new();
    };
    ["die_sides", "roll", "modifier", "total", "rolls"]
        .iter()
        .flat_map(|field| {
            value_issues(equation.get(*field).unwrap_or(&Json::Null), &format!("{path}.{field}"))
        })
        .collect()
}

/// Python truthiness of a JSON value.
fn truthy(value: &Json) -> bool {
    match value {
        Json::Null => false,
        Json::Bool(flag) => *flag,
        Json::Number(number) => number.as_f64().is_some_and(|x| x != 0.0),
        Json::String(text) => !text.is_empty(),
        Json::Array(items) => !items.is_empty(),
        Json::Object(object) => !object.is_empty(),
    }
}

/// `validate_result_card_declaration(value, path=..., tool_names=...)`.
pub fn validate_result_card_declaration(
    value: &Json,
    path: &str,
    tool_names: Option<&[&str]>,
) -> Result<(), ContentShapeError> {
    if let Err(error) = validate_content_shape("result_card", value, Some(FILE)) {
        return Err(ContentShapeError::new(
            error
                .diagnostics
                .into_iter()
                .map(|item| RulesetDiagnostic {
                    code: item.code,
                    phase: "runtime".into(),
                    message: item.message,
                    file: Some(FILE.into()),
                    path: Some(full_path(
                        path,
                        item.path.as_deref().filter(|p| !p.is_empty()).unwrap_or("$"),
                    )),
                    package_id: None,
                    item_id: None,
                    expected: None,
                    actual: None,
                    guidance: item.guidance,
                })
                .collect(),
        ));
    }
    let Json::Object(declaration) = value else {
        unreachable!("the shared result_card schema admits only an object");
    };
    let mut issues = Vec::new();
    for field in ["title", "subtitle"] {
        if let Some(item) = declaration.get(field) {
            issues.extend(value_issues(item, &format!("{path}.{field}")));
        }
    }
    issues.extend(equation_issues(declaration.get("equation"), &format!("{path}.equation")));
    if !["title", "summary", "details", "actions"]
        .iter()
        .any(|field| declaration.get(*field).is_some_and(truthy))
    {
        issues.push(diagnostic(
            path,
            "A result card must declare visible content or an action.",
            "Supply a title, row, or action, or remove the result_card declaration.",
        ));
    }
    for section in ["summary", "details", "equations"] {
        match declaration.get(section) {
            Some(rows @ Json::Object(_)) => issues.extend(value_issues(rows, &format!("{path}.{section}"))),
            Some(Json::Array(rows)) => {
                for (index, row) in rows.iter().enumerate() {
                    if let Json::Object(row) = row {
                        if let Some(item) = row.get("value") {
                            issues.extend(value_issues(item, &format!("{path}.{section}[{index}].value")));
                            issues.extend(equation_issues(
                                row.get("equation"),
                                &format!("{path}.{section}[{index}].equation"),
                            ));
                        }
                    }
                }
            }
            _ => {}
        }
    }
    if let Some(Json::Array(actions)) = declaration.get("actions") {
        for (index, action) in actions.iter().enumerate() {
            let Json::Object(action) = action else {
                continue;
            };
            if let Some(Json::String(tool)) = action.get("tool") {
                if !str_is_alnum(&tool.replace('_', "a")) {
                    issues.push(diagnostic(
                        &format!("{path}.actions[{index}].tool"),
                        "Use only alphanumeric characters and underscores in card tool names.",
                        "Use the exact declared Ruleset tool name.",
                    ));
                } else if tool_names.is_some_and(|names| !names.contains(&tool.as_str())) {
                    issues.push(diagnostic(
                        &format!("{path}.actions[{index}].tool"),
                        "Card action names an undeclared Ruleset tool.",
                        "Use the exact name of a tool declared in this runtime and manifest.",
                    ));
                }
            }
            if let Some(Json::Object(parameters)) = action.get("parameters") {
                for (name, parameter) in parameters {
                    issues.extend(value_issues(
                        parameter,
                        &format!("{path}.actions[{index}].parameters.{name}"),
                    ));
                }
            }
        }
    }
    if issues.is_empty() {
        Ok(())
    } else {
        issues.truncate(16);
        Err(ContentShapeError::new(issues))
    }
}
