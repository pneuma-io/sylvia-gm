//! `application/result_cards.py`: resolve a Ruleset's card declarations once, when its mechanic
//! produces a result.
//!
//! Every failure Python raises here (`ValueError`, `KeyError` inside an equation) ends in a
//! `None` card or label, or in skipping an optional row, so one unit error type stands for them.
//! The inputs are JSON (the mechanic's parameters and its `_json_safe` public context), where
//! Python's `TypeError` paths cannot arise.

use sylvia_base::pystr::strip;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::canonical_payload_is_safe;
use sylvia_engine::ruleset::pydata::str_is_alnum;

/// Python's `ValueError` (or the equation's `KeyError`).
struct Invalid;

type Card<T> = Result<T, Invalid>;

/// `{"parameters": parameters, "context": public_context}`.
struct Values<'a> {
    parameters: &'a JsonMap,
    context: &'a JsonMap,
}

fn char_len(text: &str) -> usize {
    text.chars().count()
}

/// `type(value) is int` for a JSON number (bools are not numbers here).
fn is_int(value: &Json) -> bool {
    matches!(value, Json::Number(number) if !number.is_f64())
}

/// `type(value) in (int, float)`.
fn is_number(value: &Json) -> bool {
    matches!(value, Json::Number(_))
}

/// `_text(value, limit)`: non-blank text of at most `limit` characters, as given.
fn text(value: &Json, limit: usize) -> Card<String> {
    match value {
        Json::String(text) if !strip(text).is_empty() && char_len(text) <= limit => Ok(text.clone()),
        _ => Err(Invalid),
    }
}

/// `_scalar(value)`: JSON scalars, with text of at most 4,000 characters. JSON numbers are finite.
fn scalar(value: &Json) -> Card<Json> {
    match value {
        Json::String(text) if char_len(text) > 4000 => Err(Invalid),
        Json::Null | Json::Bool(_) | Json::Number(_) | Json::String(_) => Ok(value.clone()),
        _ => Err(Invalid),
    }
}

/// `str(value)` of a scalar.
fn py_str(value: &Json) -> String {
    match value {
        Json::Null => "None".into(),
        Json::Bool(true) => "True".into(),
        Json::Bool(false) => "False".into(),
        Json::Number(number) => match number.as_f64().filter(|_| number.is_f64()) {
            Some(float) => pyjson::float_repr(float),
            None => number.to_string(),
        },
        Json::String(text) => text.clone(),
        _ => unreachable!("a scalar"),
    }
}

/// `_public_value(path_value, values)`: one bounded public path.
fn public_value<'v>(path_value: &Json, values: &Values<'v>) -> Card<&'v Json> {
    let path = text(path_value, 256)?;
    let fields: Vec<&str> = path.split('.').collect();
    if fields.len() > 16 {
        return Err(Invalid);
    }
    let mut current: &JsonMap = match fields[0] {
        "parameters" => values.parameters,
        "context" => values.context,
        _ => return Err(Invalid),
    };
    let mut value: Option<&'v Json> = None;
    for (index, field) in fields[1..].iter().enumerate() {
        if field.is_empty() {
            return Err(Invalid);
        }
        let Some(child) = current.get(*field) else { return Err(Invalid) };
        value = Some(child);
        if index + 2 < fields.len() {
            let Json::Object(map) = child else { return Err(Invalid) };
            current = map;
        }
    }
    // A bare root names the whole mapping. Every caller then refuses it (`_scalar` takes no
    // mapping and rows must be a list), so it is refused here.
    value.ok_or(Invalid)
}

/// `_value(source, values)`: an explicit path or a literal, never an expression.
fn value(source: &Json, values: &Values<'_>) -> Card<Json> {
    let mut value = source.clone();
    if let Json::Object(reference) = source {
        if !reference.contains_key("path") || reference.keys().any(|key| key != "path" && key != "labels") {
            return Err(Invalid);
        }
        value = public_value(&reference["path"], values)?.clone();
        match reference.get("labels") {
            None | Some(Json::Null) => {}
            Some(Json::Object(labels)) if labels.len() <= 64 => {
                let mut key = py_str(&scalar(&value)?);
                if matches!(value, Json::Bool(_)) {
                    key = key.to_lowercase();
                }
                let Some(label) = labels.get(&key) else { return Err(Invalid) };
                value = Json::String(text(label, 4000)?);
            }
            Some(_) => return Err(Invalid),
        }
    }
    match &value {
        Json::Array(items) if items.len() > 64 => Err(Invalid),
        Json::Array(items) => Ok(Json::Array(items.iter().map(scalar).collect::<Card<_>>()?)),
        other => scalar(other),
    }
}

/// `row.get(key, False)`, which Python requires to be a bool.
fn optional_bool(row: &JsonMap, key: &str) -> Card<bool> {
    match row.get(key) {
        None => Ok(false),
        Some(Json::Bool(flag)) => Ok(*flag),
        Some(_) => Err(Invalid),
    }
}

/// `_rows(raw, values, limit)`.
fn rows(raw: &Json, values: &Values<'_>, limit: usize) -> Card<Vec<Json>> {
    let recorded = matches!(raw, Json::Object(_));
    let raw = match raw {
        Json::Object(reference) => {
            if reference.len() != 1 || !reference.contains_key("path") {
                return Err(Invalid);
            }
            public_value(&reference["path"], values)?
        }
        other => other,
    };
    let Json::Array(raw) = raw else { return Err(Invalid) };
    if raw.len() > limit {
        return Err(Invalid);
    }
    let mut rows = Vec::new();
    for row in raw {
        let Json::Object(row) = row else { return Err(Invalid) };
        let Some(row_value) = row.get("value") else { return Err(Invalid) };
        if row.keys().any(|key| {
            !matches!(key.as_str(), "label" | "value" | "emphasis" | "optional" | "omit_zero" | "equation")
        }) {
            return Err(Invalid);
        }
        if recorded && matches!(row_value, Json::Object(_)) {
            return Err(Invalid);
        }
        let optional = optional_bool(row, "optional")?;
        let emphasis = optional_bool(row, "emphasis")?;
        let omit_zero = optional_bool(row, "omit_zero")?;
        let mut resolved = JsonMap::new();
        if let Some(label) = row.get("label") {
            resolved.insert("label".into(), Json::String(text(label, 200)?));
        }
        let resolved_value = match value(row_value, values) {
            Ok(resolved_value) => resolved_value,
            Err(_) if optional => continue,
            Err(error) => return Err(error),
        };
        if optional && resolved_value.is_null() {
            continue;
        }
        if omit_zero && is_number(&resolved_value) && resolved_value.as_f64() == Some(0.0) {
            continue;
        }
        resolved.insert("value".into(), resolved_value);
        resolved.insert("emphasis".into(), Json::Bool(emphasis));
        if let Some(raw_equation) = row.get("equation") {
            if let Some(equation) = equation(raw_equation, values, recorded)? {
                resolved.insert("equation".into(), Json::Object(equation));
            }
        }
        rows.push(Json::Object(resolved));
    }
    Ok(rows)
}

/// `_equation(raw, values, recorded=...)`: recorded numbers, without arithmetic.
fn equation(raw: &Json, values: &Values<'_>, recorded: bool) -> Card<Option<JsonMap>> {
    let Json::Object(raw) = raw else { return Err(Invalid) };
    if raw.keys().any(|key| {
        !matches!(key.as_str(), "die_sides" | "roll" | "modifier" | "total" | "optional" | "rolls")
    }) {
        return Err(Invalid);
    }
    let optional = optional_bool(raw, "optional")?;
    if recorded && raw.values().any(|item| matches!(item, Json::Object(_))) {
        return Err(Invalid);
    }
    let resolve = || -> Card<Option<JsonMap>> {
        let sides = value(raw.get("die_sides").unwrap_or(&Json::Null), values)?;
        let sides_number = match sides.as_i64() {
            Some(number) if is_int(&sides) && (2..=1000).contains(&number) => number,
            _ => return Err(Invalid),
        };
        let mut resolved = JsonMap::new();
        resolved.insert("die_sides".into(), sides);
        for key in ["roll", "modifier", "total"] {
            let item = value(raw.get(key).ok_or(Invalid)?, values)?;
            if item.is_null() && optional {
                return Ok(None);
            }
            if !is_number(&item) {
                return Err(Invalid);
            }
            resolved.insert(key.into(), item);
        }
        if let Some(raw_rolls) = raw.get("rolls") {
            let rolls = value(raw_rolls, values)?;
            let valid = match &rolls {
                Json::Array(items) => {
                    (1..=64).contains(&items.len())
                        && items.iter().all(|roll| {
                            is_int(roll)
                                && roll.as_i64().is_some_and(|roll| (1..=sides_number).contains(&roll))
                        })
                }
                _ => false,
            };
            if !valid {
                return Err(Invalid);
            }
            resolved.insert("rolls".into(), rolls);
        }
        Ok(Some(resolved))
    };
    match resolve() {
        Ok(resolved) => Ok(resolved),
        Err(_) if optional => Ok(None),
        Err(error) => Err(error),
    }
}

/// `project_activity_label(declaration, parameters=..., public_context=...)`: a bounded routine
/// label of at most 80 characters, or `None`. `None` is an absent declaration.
pub fn project_activity_label(
    declaration: Option<&Json>,
    parameters: &JsonMap,
    public_context: &JsonMap,
) -> Option<String> {
    let values = Values { parameters, context: public_context };
    let resolved = value(declaration.unwrap_or(&Json::Null), &values).ok()?;
    text(&resolved, 80).ok()
}

/// `project_result_card(declaration, parameters=..., public_context=...)`: a detached
/// presentation, or `None` when the declaration is absent, unsupported or its values are
/// unavailable. Invalid display data never changes the mechanic's outcome.
pub fn project_result_card(
    declaration: Option<&Json>,
    parameters: &JsonMap,
    public_context: &JsonMap,
) -> Option<JsonMap> {
    let Some(Json::Object(declaration)) = declaration else { return None };
    match declaration.get("version") {
        Some(version) if is_int(version) && version.as_i64() == Some(1) => {}
        _ => return None,
    }
    let known = [
        "version",
        "title",
        "summary",
        "details",
        "actions",
        "footprint",
        "details_label",
        "subtitle",
        "equation",
        "equations",
    ];
    if declaration.keys().any(|key| !known.contains(&key.as_str())) {
        return None;
    }
    let values = Values { parameters, context: public_context };
    let card = (|| -> Card<JsonMap> {
        let empty = Json::Array(Vec::new());
        let mut card = JsonMap::new();
        card.insert("version".into(), Json::from(1));
        card.insert(
            "summary".into(),
            Json::Array(rows(declaration.get("summary").unwrap_or(&empty), &values, 16)?),
        );
        card.insert(
            "details".into(),
            Json::Array(rows(declaration.get("details").unwrap_or(&empty), &values, 128)?),
        );
        for key in ["title", "subtitle"] {
            if let Some(source) = declaration.get(key) {
                card.insert(key.into(), Json::String(text(&value(source, &values)?, 200)?));
            }
        }
        if let Some(raw) = declaration.get("equation") {
            if let Some(resolved) = equation(raw, &values, false)? {
                card.insert("equation".into(), Json::Object(resolved));
            }
        }
        if let Some(raw) = declaration.get("equations") {
            let featured = rows(raw, &values, 16)?;
            if featured.iter().any(|row| !matches!(row, Json::Object(row) if row.contains_key("equation"))) {
                return Err(Invalid);
            }
            card.insert("equations".into(), Json::Array(featured));
        }
        if let Some(footprint) = declaration.get("footprint") {
            if !matches!(footprint.as_str(), Some("inline" | "compact")) {
                return Err(Invalid);
            }
            card.insert("footprint".into(), footprint.clone());
        }
        if let Some(label) = declaration.get("details_label") {
            card.insert("details_label".into(), Json::String(text(label, 200)?));
        }
        let raw_actions = match declaration.get("actions") {
            None => &empty,
            Some(raw) => raw,
        };
        let Json::Array(raw_actions) = raw_actions else { return Err(Invalid) };
        if raw_actions.len() > 8 {
            return Err(Invalid);
        }
        let mut actions = Vec::new();
        for action in raw_actions {
            let Json::Object(action) = action else { return Err(Invalid) };
            if action.len() != 3
                || !["label", "tool", "parameters"].iter().all(|key| action.contains_key(*key))
            {
                return Err(Invalid);
            }
            let tool = text(&action["tool"], 128)?;
            if !str_is_alnum(&tool.replace('_', "a")) {
                return Err(Invalid);
            }
            let Json::Object(raw_parameters) = &action["parameters"] else { return Err(Invalid) };
            if raw_parameters.len() > 64 {
                return Err(Invalid);
            }
            let label = text(&action["label"], 200)?;
            let mut resolved = JsonMap::new();
            for (key, item) in raw_parameters {
                let key = text(&Json::String(key.clone()), 128)?;
                resolved.insert(key, value(item, &values)?);
            }
            let mut entry = JsonMap::new();
            entry.insert("label".into(), Json::String(label));
            entry.insert("tool".into(), Json::String(tool));
            entry.insert("parameters".into(), Json::Object(resolved));
            actions.push(Json::Object(entry));
        }
        card.insert("actions".into(), Json::Array(actions));
        Ok(card)
    })()
    .ok()?;
    let present = |key: &str| match card.get(key) {
        Some(Json::String(text)) => !text.is_empty(),
        Some(Json::Array(items)) => !items.is_empty(),
        _ => false,
    };
    if !["title", "summary", "details", "actions"].iter().any(|key| present(key)) {
        return None;
    }
    if !canonical_payload_is_safe(&Json::Object(card.clone())) {
        return None;
    }
    Some(card)
}
