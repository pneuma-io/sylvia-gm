//! `application/schema_admission.py`: the small deterministic JSON-schema subset Sylvia admits
//! for tool parameters, participant artifacts and starting state.
//!
//! It lives in `sylvia-domain` rather than `sylvia-app` because package archive admission
//! (also lane 1, in this crate) calls `validate_json_schema`, and the engine, store and agent
//! lanes validate parameters with it.
//!
//! - [`validate_json_schema`]: admit a schema to the subset (`CommandRejected` otherwise).
//! - [`validate_parameters`]: collect every model-correctable issue and raise one
//!   `MechanicsRejected` with them, sorted by path and kind.
//! - [`validate_json_value`] and [`validate_references`]: the first-failure checks behind
//!   stored values and participant references.
//!
//! Python's `==` and `in` decide enum membership and duplicates (`True == 1`, `1 == 1.0`), and
//! lengths count code points.

use crate::error::{DomainError, DomainResult, MechanicsDetails, MechanicsValidationIssue};
use crate::pyops::{py_eq, py_number_cmp};
use std::borrow::Cow;
use std::cmp::Ordering;
use sylvia_base::{pystr, Json, JsonMap};

const SUPPORTED_SCHEMA_FIELDS: &[&str] = &[
    "$id",
    "description",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "minItems",
    "maxItems",
    "uniqueItems",
    "allOf",
    "if",
    "then",
    "contains",
    "minContains",
    "x-sylvia-gm-reference",
    "x-sylvia-gm-derived",
    "x-sylvia-gm-model-hidden",
    "x-sylvia-gm-model-required",
];

const TYPES: &[&str] = &["string", "integer", "number", "boolean", "object", "array", "null"];

fn rejected(message: impl Into<String>) -> DomainError {
    DomainError::command_rejected(message)
}

fn rejected_code(message: impl Into<String>, code: &str) -> DomainError {
    DomainError::command_rejected_with(message, code, Vec::new())
}

/// `isinstance(value, int) and not isinstance(value, bool)`.
fn is_int(value: &Json) -> bool {
    value.as_number().is_some_and(|n| n.is_i64() || n.is_u64())
}

/// `isinstance(value, (int, float)) and not isinstance(value, bool)`.
fn is_number(value: &Json) -> bool {
    value.is_number()
}

/// `value < 0` for a JSON integer.
fn negative(value: &Json) -> bool {
    value.as_i64().is_some_and(|n| n < 0)
}

/// `len(value)` compared with a JSON integer bound: `Less`, `Equal` or `Greater`.
fn cmp_len(length: usize, bound: &Json) -> Ordering {
    py_number_cmp(&Json::from(length), bound).expect("bounds are checked numbers")
}

/// `_nullable_type(schema)`: the standard two-type nullable form.
fn nullable_type(schema: &JsonMap) -> DomainResult<Option<String>> {
    let Some(Json::Array(value)) = schema.get("type") else { return Ok(None) };
    let invalid = || rejected("Nullable schema type must pair one type with null");
    let has_null = value.iter().any(|item| item.as_str() == Some("null"));
    if value.len() != 2 || !has_null || value.iter().any(|item| !item.is_string()) {
        return Err(invalid());
    }
    let base = value.iter().filter_map(Json::as_str).find(|item| *item != "null");
    match base {
        Some(base) if ["string", "integer", "number", "boolean", "object", "array"].contains(&base) => {
            Ok(Some(base.to_owned()))
        }
        _ => Err(invalid()),
    }
}

/// `_value_schema(schema, value)`: type constraints apply only to a non-null nullable value.
fn value_schema<'s>(schema: &'s JsonMap, value: &Json) -> DomainResult<Cow<'s, JsonMap>> {
    let Some(base) = nullable_type(schema)? else { return Ok(Cow::Borrowed(schema)) };
    if !value.is_null() {
        let mut narrowed = schema.clone();
        narrowed.insert("type".into(), Json::from(base));
        return Ok(Cow::Owned(narrowed));
    }
    let mut null_schema = JsonMap::new();
    null_schema.insert("type".into(), Json::from("null"));
    for key in ["enum", "allOf", "if", "then"] {
        if let Some(item) = schema.get(key) {
            null_schema.insert(key.into(), item.clone());
        }
    }
    Ok(Cow::Owned(null_schema))
}

/// `validate_json_schema(schema, label=...)`: the subset shared by tool and participant admission.
pub fn validate_json_schema(schema: &JsonMap, label: &str) -> DomainResult<()> {
    let mut extra: Vec<&String> =
        schema.keys().filter(|key| !SUPPORTED_SCHEMA_FIELDS.contains(&key.as_str())).collect();
    extra.sort();
    if let Some(first) = extra.first() {
        return Err(rejected(format!("{label} contains unsupported field '{first}'")));
    }
    if schema.get("description").is_some_and(|d| !d.is_string()) {
        return Err(rejected(format!("{label} has an invalid description")));
    }
    let expected: Option<String> = match nullable_type(schema)? {
        Some(base) => Some(base),
        None => schema.get("type").and_then(Json::as_str).map(str::to_owned),
    };
    let Some(expected) = expected.filter(|t| TYPES.contains(&t.as_str())) else {
        return Err(rejected(format!("{label} has an invalid type")));
    };
    if let Some(branches) = schema.get("allOf") {
        let branches = match branches {
            Json::Array(items) if (1..=256).contains(&items.len()) && items.iter().all(Json::is_object) => {
                items
            }
            _ => return Err(rejected(format!("{label} allOf must contain 1 to 256 schemas"))),
        };
        for (index, branch) in branches.iter().enumerate() {
            validate_json_schema(branch.as_object().expect("checked"), &format!("{label}.allOf[{index}]"))?;
        }
    }
    if schema.contains_key("if") != schema.contains_key("then") {
        return Err(rejected(format!("{label} requires both if and then")));
    }
    for field in ["if", "then", "contains"] {
        if let Some(child) = schema.get(field) {
            let Json::Object(child) = child else {
                return Err(rejected(format!("{label}.{field} must be a schema")));
            };
            validate_json_schema(child, &format!("{label}.{field}"))?;
        }
    }
    if schema.contains_key("contains") || schema.contains_key("minContains") {
        if expected != "array" || !schema.contains_key("contains") {
            return Err(rejected(format!("{label} contains constraints require an array and contains")));
        }
        let count = schema.get("minContains").cloned().unwrap_or(Json::from(1));
        if !is_int(&count) || negative(&count) {
            return Err(rejected(format!("{label} has invalid minContains")));
        }
    }
    if let Some(identity) = schema.get("$id").filter(|v| !v.is_null()) {
        if !identity.as_str().is_some_and(|text| !text.is_empty()) {
            return Err(rejected(format!("{label} has an invalid identity")));
        }
    }
    if schema.get("enum").is_some_and(|e| !e.is_null() && !e.is_array()) {
        return Err(rejected(format!("{label} has an invalid enum")));
    }
    for (field, name) in [("minimum", "minimum"), ("maximum", "maximum")] {
        if schema.get(field).is_some_and(|v| !v.is_null() && !is_number(v)) {
            return Err(rejected(format!("{label} has an invalid {name}")));
        }
    }
    for (field, name) in [("minLength", "minimum length"), ("maxLength", "maximum length")] {
        if schema.get(field).is_some_and(|v| !v.is_null() && (!is_int(v) || negative(v))) {
            return Err(rejected(format!("{label} has an invalid {name}")));
        }
    }
    if expected == "object" {
        let empty = Json::Object(JsonMap::new());
        let Json::Object(properties) = schema.get("properties").unwrap_or(&empty) else {
            return Err(rejected(format!("{label} properties are invalid")));
        };
        let no_required = Json::Array(Vec::new());
        let required_ok = match schema.get("required").unwrap_or(&no_required) {
            Json::Array(items) => {
                let names: Vec<&str> = items.iter().filter_map(Json::as_str).collect();
                let mut unique = names.clone();
                unique.sort_unstable();
                unique.dedup();
                names.len() == items.len()
                    && unique.len() == names.len()
                    && names.iter().all(|name| properties.contains_key(*name))
            }
            _ => false,
        };
        if !required_ok {
            return Err(rejected(format!("{label} required fields are invalid")));
        }
        let additional = schema.get("additionalProperties").cloned().unwrap_or(Json::Bool(true));
        if !additional.is_boolean() && !additional.is_object() {
            return Err(rejected(format!("{label} additional properties are invalid")));
        }
        for (name, child) in properties {
            let Json::Object(child) = child else {
                return Err(rejected(format!("{label} property schemas are invalid")));
            };
            validate_json_schema(child, &format!("{label}.{name}"))?;
        }
        if let Json::Object(additional) = &additional {
            validate_json_schema(additional, &format!("{label}.additionalProperties"))?;
        }
    } else if expected == "array" {
        let min_items = schema.get("minItems").filter(|v| !v.is_null());
        let max_items = schema.get("maxItems").filter(|v| !v.is_null());
        for (field, bound) in [("minimum items", min_items), ("maximum items", max_items)] {
            if bound.is_some_and(|b| !is_int(b) || negative(b)) {
                return Err(rejected(format!("{label} has invalid {field}")));
            }
        }
        if let (Some(min), Some(max)) = (min_items, max_items) {
            if py_number_cmp(min, max) == Some(Ordering::Greater) {
                return Err(rejected(format!("{label} minimum items exceed maximum items")));
            }
        }
        if schema.get("uniqueItems").is_some_and(|v| !v.is_null() && !v.is_boolean()) {
            return Err(rejected(format!("{label} has invalid unique items")));
        }
        let Some(Json::Object(items)) = schema.get("items") else {
            return Err(rejected(format!("{label} has invalid array items schema")));
        };
        validate_json_schema(items, &format!("{label}.items"))?;
    } else if ["minItems", "maxItems", "uniqueItems"].iter().any(|field| schema.contains_key(*field)) {
        return Err(rejected(format!("{label} uses array constraints on a non-array schema")));
    }
    Ok(())
}

/// `validate_json_value(value, schema, label=...)`.
pub fn validate_json_value(value: &Json, schema: &JsonMap, label: &str) -> DomainResult<()> {
    validate_json_schema(schema, &format!("{label} schema"))?;
    validate_value(label, value, schema)
}

const LEGACY_CONSTRAINT_KINDS: &[&str] =
    &["above_maximum", "below_minimum", "duplicate_items", "too_few_items", "too_many_items"];

/// `validate_parameters(parameters, schema)`: the explicit object schemas of the tool catalogue.
pub fn validate_parameters(parameters: &JsonMap, schema: &JsonMap) -> DomainResult<()> {
    if !schema.get("type").is_some_and(|t| py_eq(t, &Json::from("object"))) {
        return Err(rejected("Tool schema must declare an object"));
    }
    if schema.get("properties").is_some_and(|p| !p.is_object()) {
        return Err(rejected("Tool schema properties are invalid"));
    }
    let required_ok = match schema.get("required") {
        None => true,
        Some(Json::Array(items)) => items.iter().all(Json::is_string),
        Some(_) => false,
    };
    if !required_ok {
        return Err(rejected("Tool schema required fields are invalid"));
    }
    let value = Json::Object(parameters.clone());
    let mut issues = parameter_value_issues(&value, schema, "$", "$")?;
    if issues.is_empty() {
        return Ok(());
    }
    // `sorted(issues, key=lambda issue: (issue.path, issue.kind))`: stable, by code point.
    issues.sort_by(|a, b| (a.path.as_str(), a.kind.as_str()).cmp(&(b.path.as_str(), b.kind.as_str())));
    let code = if issues.iter().any(|issue| LEGACY_CONSTRAINT_KINDS.contains(&issue.kind.as_str())) {
        "invalid_schema"
    } else {
        "mechanics_validation_failed"
    };
    let mut field_paths: Vec<String> = Vec::new();
    for issue in &issues {
        if !field_paths.contains(&issue.path) {
            field_paths.push(issue.path.clone());
        }
    }
    let details = MechanicsDetails {
        field_paths,
        validation_issues: issues,
        state_changed: false,
        refusal_reasons: Vec::new(),
    };
    Err(DomainError::mechanics_rejected(
        "Tool parameters do not match the exposed operation schema",
        code,
        details,
    )?)
}

/// `validate_references(parameters, schema, participant_ids=...)`: schema-declared references
/// only within the command campaign.
pub fn validate_references(
    parameters: &JsonMap,
    schema: &JsonMap,
    participant_ids: &[&str],
) -> DomainResult<()> {
    validate_reference_value(&Json::Object(parameters.clone()), schema, participant_ids)
}

/// `for branch in _applied_branches(value, schema): apply(branch)`.
///
/// Python's generator yields the `allOf` branches first and only then checks the `if`
/// condition, so an error from applying a branch comes before one from the condition.
fn for_applied_branches(
    value: &Json,
    schema: &JsonMap,
    mut apply: impl FnMut(&JsonMap) -> DomainResult<()>,
) -> DomainResult<()> {
    if let Some(Json::Array(items)) = schema.get("allOf") {
        for branch in items.iter().filter_map(Json::as_object) {
            apply(branch)?;
        }
    }
    if let (Some(Json::Object(condition)), Some(Json::Object(consequence))) =
        (schema.get("if"), schema.get("then"))
    {
        if parameter_value_issues(value, condition, "$", "$")?.is_empty() {
            apply(consequence)?;
        }
    }
    Ok(())
}

/// `_contains_count(value, schema)`.
fn contains_count(items: &[Json], schema: &JsonMap) -> DomainResult<usize> {
    let mut count = 0;
    for item in items {
        if parameter_value_issues(item, schema, "$", "$")?.is_empty() {
            count += 1;
        }
    }
    Ok(count)
}

/// `_applicable_child_schema(schema, child_name)`.
fn applicable_child_schema<'s>(schema: &'s JsonMap, child_name: &str) -> Option<&'s JsonMap> {
    let properties = match schema.get("properties") {
        None => None,
        Some(Json::Object(properties)) => Some(properties),
        Some(_) => return None,
    };
    if let Some(declared) = properties.and_then(|p| p.get(child_name)) {
        return declared.as_object();
    }
    match schema.get("additionalProperties") {
        Some(Json::Object(additional)) => Some(additional),
        _ => None,
    }
}

/// `_json_type_name(value)`.
fn json_type_name(value: &Json) -> &'static str {
    match value {
        Json::Null => "null",
        Json::Bool(_) => "boolean",
        Json::String(_) => "string",
        Json::Number(n) if n.is_i64() || n.is_u64() => "integer",
        Json::Number(_) => "number",
        Json::Object(_) => "object",
        Json::Array(_) => "array",
    }
}

/// `str(item)` for an enum member, as `allowed_values` records it.
fn python_str(item: &Json) -> String {
    match item {
        Json::String(text) => text.clone(),
        other => pystr::repr_json(other),
    }
}

fn truncate(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

/// `_parameter_issue(path=..., kind=..., schema=..., actual=..., parent_path=...)`.
fn parameter_issue(
    path: &str,
    kind: &str,
    schema: Option<&JsonMap>,
    actual: Option<&Json>,
    parent_path: Option<&str>,
) -> MechanicsValidationIssue {
    let expected_type = schema.and_then(|s| s.get("type")).and_then(Json::as_str).map(str::to_owned);
    let allowed_values = match schema.and_then(|s| s.get("enum")) {
        Some(Json::Array(items)) => items.iter().take(64).map(python_str).collect(),
        _ => Vec::new(),
    };
    let first_int = |names: [&str; 3]| -> Option<serde_json::Number> {
        let schema = schema?;
        names
            .iter()
            .find_map(|name| schema.get(*name).filter(|v| is_int(v)).and_then(Json::as_number).cloned())
    };
    MechanicsValidationIssue {
        path: truncate(path, 240),
        kind: kind.to_owned(),
        expected_type,
        actual_type: actual.map(|value| json_type_name(value).to_owned()),
        parent_path: Some(truncate(parent_path.filter(|p| !p.is_empty()).unwrap_or("$"), 240)),
        allowed_values,
        minimum: first_int(["minimum", "minLength", "minItems"]),
        maximum: first_int(["maximum", "maxLength", "maxItems"]),
        actual_length: actual.and_then(Json::as_str).map(|text| pystr::len(text) as i64),
    }
}

/// `_value_matches_type(value, expected)`.
fn value_matches_type(value: &Json, expected: &str) -> bool {
    match expected {
        "string" => value.is_string(),
        "integer" => is_int(value),
        "number" => value.is_number(),
        "boolean" => value.is_boolean(),
        "object" => value.is_object(),
        "array" => value.is_array(),
        "null" => value.is_null(),
        _ => false,
    }
}

/// `value in enum` for a JSON list: `==` against each member.
fn in_enum(value: &Json, members: &[Json]) -> bool {
    members.iter().any(|member| py_eq(member, value))
}

/// `any(item == prior for index, item in enumerate(value) for prior in value[:index])`.
fn has_duplicates(items: &[Json]) -> bool {
    items.iter().enumerate().any(|(index, item)| items[..index].iter().any(|prior| py_eq(item, prior)))
}

/// `_parameter_value_issues(value, schema, path=..., parent_path=...)`: every actionable
/// mismatch, without copying parameter values.
fn parameter_value_issues(
    value: &Json,
    schema: &JsonMap,
    path: &str,
    parent_path: &str,
) -> DomainResult<Vec<MechanicsValidationIssue>> {
    let schema = value_schema(schema, value)?;
    let schema = schema.as_ref();
    let Some(expected) = schema.get("type").and_then(Json::as_str) else {
        return Err(rejected(format!("Tool parameter schema for '{path}' has an invalid type")));
    };
    let issue = |kind: &str| parameter_issue(path, kind, Some(schema), Some(value), Some(parent_path));
    if !value_matches_type(value, expected) {
        return Ok(vec![issue("wrong_type")]);
    }
    if let Some(enum_value) = schema.get("enum").filter(|v| !v.is_null()) {
        let Json::Array(members) = enum_value else {
            return Err(rejected(format!("Tool parameter schema for '{path}' has an invalid enum")));
        };
        if !in_enum(value, members) {
            return Ok(vec![issue("invalid_enum_value")]);
        }
    }
    let mut issues = Vec::new();
    for (field, kind, failing) in
        [("minimum", "below_minimum", Ordering::Less), ("maximum", "above_maximum", Ordering::Greater)]
    {
        if let Some(bound) = schema.get(field).filter(|v| !v.is_null()) {
            if !is_number(bound) {
                return Err(rejected(format!("Tool parameter schema for '{path}' has an invalid {field}")));
            }
            if py_number_cmp(value, bound) == Some(failing) {
                issues.push(issue(kind));
            }
        }
    }
    for (field, name, kind, failing) in [
        ("minLength", "minimum length", "string_too_short", Ordering::Less),
        ("maxLength", "maximum length", "string_too_long", Ordering::Greater),
    ] {
        if let Some(bound) = schema.get(field).filter(|v| !v.is_null()) {
            if !is_int(bound) || negative(bound) {
                return Err(rejected(format!("Tool parameter schema for '{path}' has an invalid {name}")));
            }
            if let Json::String(text) = value {
                if cmp_len(pystr::len(text), bound) == failing {
                    issues.push(issue(kind));
                }
            }
        }
    }
    if expected == "object" {
        issues.extend(parameter_object_issues(value.as_object().expect("type checked"), schema, path)?);
    } else if expected == "array" {
        let items = value.as_array().expect("type checked");
        for (field, kind, failing) in
            [("minItems", "too_few_items", Ordering::Less), ("maxItems", "too_many_items", Ordering::Greater)]
        {
            let bound = schema.get(field).filter(|v| !v.is_null());
            if bound.is_some_and(|b| !is_int(b) || negative(b)) {
                return Err(rejected(format!("Tool parameter schema for '{path}' has invalid {field}")));
            }
            if let Some(bound) = bound {
                if cmp_len(items.len(), bound) == failing {
                    issues.push(issue(kind));
                }
            }
        }
        if schema.get("uniqueItems") == Some(&Json::Bool(true)) && has_duplicates(items) {
            issues.push(issue("duplicate_items"));
        }
        let Some(Json::Object(item_schema)) = schema.get("items") else {
            return Err(rejected(format!("Tool parameter schema for '{path}' has invalid items")));
        };
        for (index, item) in items.iter().enumerate() {
            issues.extend(parameter_value_issues(item, item_schema, &format!("{path}[{index}]"), path)?);
        }
        let count = schema.get("minContains").cloned().unwrap_or(Json::from(1));
        if let Some(Json::Object(contains)) = schema.get("contains") {
            // `isinstance(count, int)` admits `True` and `False` as 1 and 0.
            let threshold = match &count {
                Json::Bool(flag) => Some(Json::from(u8::from(*flag))),
                other if is_int(other) => Some(other.clone()),
                _ => None,
            };
            if let Some(threshold) = threshold {
                if cmp_len(contains_count(items, contains)?, &threshold) == Ordering::Less {
                    let mut issue_schema = JsonMap::new();
                    issue_schema.insert("type".into(), Json::from("array"));
                    issue_schema.insert("minItems".into(), count.clone());
                    issue_schema.insert(
                        "enum".into(),
                        contains.get("enum").cloned().unwrap_or(Json::Array(Vec::new())),
                    );
                    issues.push(parameter_issue(
                        path,
                        "too_few_matching_items",
                        Some(&issue_schema),
                        None,
                        Some(parent_path),
                    ));
                }
            }
        }
    }
    for_applied_branches(value, schema, |branch| {
        issues.extend(parameter_value_issues(value, branch, path, parent_path)?);
        Ok(())
    })?;
    Ok(issues)
}

/// `_parameter_object_issues(value, schema, path=...)`.
fn parameter_object_issues(
    value: &JsonMap,
    schema: &JsonMap,
    path: &str,
) -> DomainResult<Vec<MechanicsValidationIssue>> {
    let empty = JsonMap::new();
    let properties = match schema.get("properties") {
        None => &empty,
        Some(Json::Object(properties)) => properties,
        Some(_) => {
            return Err(rejected(format!("Tool parameter schema for '{path}' has invalid properties")))
        }
    };
    let required: Vec<&str> = match schema.get("required") {
        None => Vec::new(),
        Some(Json::Array(items)) if items.iter().all(Json::is_string) => {
            items.iter().filter_map(Json::as_str).collect()
        }
        Some(_) => {
            return Err(rejected(format!("Tool parameter schema for '{path}' has invalid required fields")))
        }
    };
    let parent = if path == "$" { "$".to_owned() } else { path.to_owned() };
    let prefix = if path == "$" { String::new() } else { format!("{path}.") };
    let mut issues: Vec<MechanicsValidationIssue> = required
        .iter()
        .filter(|name| !value.contains_key(**name))
        .map(|name| {
            let field_schema = properties.get(*name).and_then(Json::as_object);
            parameter_issue(
                &format!("{prefix}{name}"),
                "missing_required_field",
                field_schema,
                None,
                Some(&parent),
            )
        })
        .collect();
    if schema.get("additionalProperties") == Some(&Json::Bool(false)) {
        let mut extra: Vec<&String> = value.keys().filter(|name| !properties.contains_key(*name)).collect();
        extra.sort();
        for name in extra {
            issues.push(parameter_issue(
                &format!("{prefix}{name}"),
                "unexpected_field",
                None,
                Some(&value[name]),
                Some(&parent),
            ));
        }
    }
    for (name, child) in value {
        let Some(child_schema) = applicable_child_schema(schema, name) else {
            if properties.contains_key(name) {
                return Err(rejected(format!("Tool parameter schema for '{prefix}{name}' is invalid")));
            }
            continue;
        };
        issues.extend(parameter_value_issues(child, child_schema, &format!("{prefix}{name}"), &parent)?);
    }
    Ok(issues)
}

/// `f"{count}"` for a `minContains` value (`True` prints as `True`).
fn count_text(count: &Json) -> String {
    match count {
        Json::Bool(true) => "True".into(),
        Json::Bool(false) => "False".into(),
        other => other.to_string(),
    }
}

/// `_validate_value(name, value, schema)`: the first failure, as `CommandRejected`.
fn validate_value(name: &str, value: &Json, schema: &JsonMap) -> DomainResult<()> {
    let schema = value_schema(schema, value)?;
    let schema = schema.as_ref();
    let valid =
        schema.get("type").and_then(Json::as_str).is_some_and(|expected| value_matches_type(value, expected));
    if !valid {
        return Err(rejected(format!("Tool parameter '{name}' has invalid type")));
    }
    let expected = schema["type"].as_str().expect("checked");
    if let Some(enum_value) = schema.get("enum").filter(|v| !v.is_null()) {
        if !enum_value.as_array().is_some_and(|members| in_enum(value, members)) {
            return Err(rejected(format!("Tool parameter '{name}' is outside its enum")));
        }
    }
    if let Some(minimum) = schema.get("minimum").filter(|v| !v.is_null()) {
        if !is_number(minimum) {
            return Err(rejected(format!("Tool parameter '{name}' has an invalid minimum schema")));
        }
        if !is_number(value) || py_number_cmp(value, minimum) == Some(Ordering::Less) {
            return Err(rejected_code(
                format!("Tool parameter '{name}' is below its minimum"),
                "invalid_schema",
            ));
        }
    }
    if let Some(maximum) = schema.get("maximum").filter(|v| !v.is_null()) {
        if !is_number(maximum) {
            return Err(rejected(format!("Tool parameter '{name}' has an invalid maximum schema")));
        }
        if !is_number(value) || py_number_cmp(value, maximum) == Some(Ordering::Greater) {
            return Err(rejected_code(
                format!("Tool parameter '{name}' is above its maximum"),
                "invalid_schema",
            ));
        }
    }
    if let Some(min_length) = schema.get("minLength").filter(|v| !v.is_null()) {
        if !is_int(min_length) || negative(min_length) {
            return Err(rejected(format!("Tool parameter '{name}' has an invalid minimum length schema")));
        }
        if !value.as_str().is_some_and(|text| cmp_len(pystr::len(text), min_length) != Ordering::Less) {
            return Err(rejected(format!("Tool parameter '{name}' is shorter than its minimum length")));
        }
    }
    if let Some(max_length) = schema.get("maxLength").filter(|v| !v.is_null()) {
        if !is_int(max_length) || negative(max_length) {
            return Err(rejected(format!("Tool parameter '{name}' has an invalid maximum length schema")));
        }
        if !value.as_str().is_some_and(|text| cmp_len(pystr::len(text), max_length) != Ordering::Greater) {
            return Err(rejected(format!("Tool parameter '{name}' is longer than its maximum length")));
        }
    }
    if expected == "object" {
        validate_object_value(name, value.as_object().expect("type checked"), schema)?;
    } else if expected == "array" {
        let items = value.as_array().expect("type checked");
        if let Some(min_items) = schema.get("minItems").filter(|v| is_int(v)) {
            if cmp_len(items.len(), min_items) == Ordering::Less {
                return Err(rejected_code(
                    format!("Tool parameter '{name}' contains fewer than {min_items} items"),
                    "invalid_schema",
                ));
            }
        }
        if let Some(max_items) = schema.get("maxItems").filter(|v| is_int(v)) {
            if cmp_len(items.len(), max_items) == Ordering::Greater {
                return Err(rejected_code(
                    format!("Tool parameter '{name}' contains more than {max_items} items"),
                    "invalid_schema",
                ));
            }
        }
        if schema.get("uniqueItems") == Some(&Json::Bool(true)) && has_duplicates(items) {
            return Err(rejected_code(
                format!("Tool parameter '{name}' contains duplicate items"),
                "invalid_schema",
            ));
        }
        let Some(Json::Object(item_schema)) = schema.get("items") else {
            return Err(rejected(format!("Tool parameter '{name}' has invalid array items schema")));
        };
        for (index, item) in items.iter().enumerate() {
            validate_value(&format!("{name}[{index}]"), item, item_schema)?;
        }
        let count = schema.get("minContains").cloned().unwrap_or(Json::from(1));
        if let Some(Json::Object(contains)) = schema.get("contains") {
            let threshold = match &count {
                Json::Bool(flag) => Some(Json::from(u8::from(*flag))),
                other if is_int(other) => Some(other.clone()),
                _ => None,
            };
            if let Some(threshold) = threshold {
                if cmp_len(contains_count(items, contains)?, &threshold) == Ordering::Less {
                    return Err(rejected_code(
                        format!(
                            "Tool parameter '{name}' has fewer than {} matching items",
                            count_text(&count)
                        ),
                        "invalid_schema",
                    ));
                }
            }
        }
    }
    for_applied_branches(value, schema, |branch| validate_value(name, value, branch))
}

/// `_validate_object_value(name, value, schema)`.
fn validate_object_value(name: &str, value: &JsonMap, schema: &JsonMap) -> DomainResult<()> {
    let empty = JsonMap::new();
    let properties = match schema.get("properties") {
        None => &empty,
        Some(Json::Object(properties)) => properties,
        Some(_) => {
            return Err(rejected(format!("Tool parameter '{name}' has invalid object properties schema")))
        }
    };
    let required: Vec<&str> = match schema.get("required") {
        None => Vec::new(),
        Some(Json::Array(items)) if items.iter().all(Json::is_string) => {
            items.iter().filter_map(Json::as_str).collect()
        }
        Some(_) => return Err(rejected(format!("Tool parameter '{name}' has invalid required schema"))),
    };
    if let Some(missing) = required.iter().find(|field| !value.contains_key(**field)) {
        return Err(rejected(format!("Tool parameter '{name}' is missing required field '{missing}'")));
    }
    if schema.get("additionalProperties") == Some(&Json::Bool(false)) {
        let mut extra: Vec<&String> = value.keys().filter(|field| !properties.contains_key(*field)).collect();
        extra.sort();
        if let Some(first) = extra.first() {
            return Err(rejected(format!("Tool parameter '{name}' contains undeclared field '{first}'")));
        }
    }
    for (child_name, child_value) in value {
        if let Some(child_schema) = applicable_child_schema(schema, child_name) {
            validate_value(&format!("{name}.{child_name}"), child_value, child_schema)?;
        }
    }
    Ok(())
}

/// `_validate_reference_value(value, schema, participant_ids=...)`.
fn validate_reference_value(value: &Json, schema: &JsonMap, participant_ids: &[&str]) -> DomainResult<()> {
    let schema = value_schema(schema, value)?;
    let schema = schema.as_ref();
    if schema.get("x-sylvia-gm-reference").is_some_and(|r| py_eq(r, &Json::from("participant")))
        && !value.as_str().is_some_and(|id| participant_ids.contains(&id))
    {
        return Err(rejected_code(
            "Participant reference is outside the command campaign",
            "cross_campaign_reference",
        ));
    }
    let expected = schema.get("type");
    if expected.is_some_and(|t| py_eq(t, &Json::from("object"))) {
        if let Json::Object(members) = value {
            if schema.get("properties").is_some_and(|p| !p.is_object()) {
                return Ok(());
            }
            for (child_name, child_value) in members {
                if let Some(child_schema) = applicable_child_schema(schema, child_name) {
                    validate_reference_value(child_value, child_schema, participant_ids)?;
                }
            }
        }
    } else if expected.is_some_and(|t| py_eq(t, &Json::from("array"))) {
        if let Json::Array(items) = value {
            if let Some(Json::Object(item_schema)) = schema.get("items") {
                for item in items {
                    validate_reference_value(item, item_schema, participant_ids)?;
                }
            }
            if let Some(Json::Object(contains)) = schema.get("contains") {
                for item in items {
                    if parameter_value_issues(item, contains, "$", "$")?.is_empty() {
                        validate_reference_value(item, contains, participant_ids)?;
                    }
                }
            }
        }
    }
    for_applied_branches(value, schema, |branch| validate_reference_value(value, branch, participant_ids))
}
