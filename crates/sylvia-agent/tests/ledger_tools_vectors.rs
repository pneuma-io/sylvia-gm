//! The ledger's tool lists, the tool loader and the fixed projection (L2b) against Python's own
//! answers (`tests/vectors/ledger_tools_vectors.json`, written by
//! `generate_ledger_tools_vectors.py`): the calls real ledger-mode GM requests make with each
//! tool list, and hand-made cases. Tool definitions are stored once and referred to by index.

use serde_json::{json, Value as Json};
use std::collections::{BTreeSet, HashSet};
use sylvia_agent::context_ledger::tools::{
    addition_block, callable_record, canonical_tool, deferred_definitions, is_tool_change, merged_tool_list,
    prefix_fingerprint, saved_tool_list, surfaced, tools_fingerprint, toolset_fingerprint,
};
use sylvia_agent::fixed_tool_projection::{
    argument_choices_record, compact_schema, schema_choices, unavailable_values,
};
use sylvia_agent::tool_loader::{
    self, choices_record, compact_arguments, compact_definition, defined_in_result, definitions_record,
    project_conversation, read_result, text_call, unread_refusal, unwrap_mechanic_call,
    wrap_mechanic_arguments, MechanicNames,
};
use sylvia_base::testing::assert_same_json;
use sylvia_base::JsonMap;

fn vectors() -> Json {
    sylvia_base::pyjson::loads(include_str!("vectors/ledger_tools_vectors.json")).expect("vectors")
}

/// A case's value with every `{"__tool__": index}` replaced by its definition.
fn resolve(value: &Json, tools: &[Json]) -> Json {
    match value {
        Json::Object(map) => match map.get("__tool__").and_then(Json::as_u64) {
            Some(index) if map.len() == 1 => tools[index as usize].clone(),
            _ => Json::Object(map.iter().map(|(key, item)| (key.clone(), resolve(item, tools))).collect()),
        },
        Json::Array(items) => Json::Array(items.iter().map(|item| resolve(item, tools)).collect()),
        other => other.clone(),
    }
}

fn object(value: &Json) -> JsonMap {
    value.as_object().cloned().unwrap_or_else(|| panic!("an object: {value}"))
}

/// A message, plain or `{"__tagged__": ...}` (the tags do not matter to these functions).
fn message(value: &Json) -> JsonMap {
    match value.get("__tagged__") {
        Some(inner) => object(&inner["message"]),
        None => object(value),
    }
}

fn objects(value: &Json) -> Vec<JsonMap> {
    value.as_array().unwrap_or_else(|| panic!("a list: {value}")).iter().map(message).collect()
}

fn optional_objects(value: &Json) -> Option<Vec<JsonMap>> {
    (!value.is_null()).then(|| objects(value))
}

fn names(value: &Json) -> MechanicNames {
    let names = &value["__names__"];
    let list = |key: &str| -> Vec<String> {
        names[key].as_array().unwrap().iter().map(|item| item.as_str().unwrap().to_owned()).collect()
    };
    let (catalog, reserved) = (list("catalog"), list("reserved"));
    MechanicNames::new(catalog.iter().map(String::as_str), reserved.iter().map(String::as_str))
}

fn set(value: &Json) -> Vec<String> {
    value["__set__"].as_array().unwrap().iter().map(|item| item.as_str().unwrap().to_owned()).collect()
}

fn text(value: &Json) -> &str {
    value.as_str().unwrap_or_else(|| panic!("text: {value}"))
}

fn list(values: Vec<JsonMap>) -> Json {
    Json::Array(values.into_iter().map(Json::Object).collect())
}

fn pair(found: Option<(String, String)>) -> Json {
    found.map_or(Json::Null, |(name, arguments)| json!([name, arguments]))
}

/// A Python exception's class and message, from either error kind.
struct Raised {
    class: String,
    message: String,
}

impl From<sylvia_domain::error::DomainError> for Raised {
    fn from(error: sylvia_domain::error::DomainError) -> Self {
        Self { class: error.python_class().to_owned(), message: error.message().to_owned() }
    }
}

impl From<sylvia_base::CodedError> for Raised {
    fn from(error: sylvia_base::CodedError) -> Self {
        Self { class: error.error_type.unwrap_or("?").to_owned(), message: error.message }
    }
}

fn run(function: &str, args: &[Json], kwargs: &JsonMap) -> Result<Json, Raised> {
    let arg = |index: usize, name: &str| -> Json {
        args.get(index).cloned().or_else(|| kwargs.get(name).cloned()).unwrap_or(Json::Null)
    };
    Ok(match function {
        "loader:compact_arguments" => json!(compact_arguments(&object(&args[0]))?),
        "loader:compact_definition" => {
            let name = arg(1, "name");
            json!(compact_definition(&object(&args[0]), name.as_str())?)
        }
        "loader:definitions_record" => {
            let (content, defined) = definitions_record(&objects(&args[0]), &names(&args[1]))?;
            json!([content, defined])
        }
        "loader:read_result" => Json::Object(read_result(&object(&args[0]), text(&args[1]))?),
        "loader:unread_refusal" => Json::Object(unread_refusal(&object(&args[0]), text(&args[1]))?),
        "loader:defined_in_result" => json!(defined_in_result(&message(&args[0]))?),
        "loader:callable_record" => {
            let reading = arg(3, "reading").as_bool().unwrap_or(false);
            let offered = optional_objects(&arg(1, "offered"));
            json!(tool_loader::callable_record(
                &objects(&args[0]),
                offered.as_deref(),
                &names(&arg(2, "names")),
                reading
            ))
        }
        "loader:choices_record" => {
            let offered = optional_objects(&args[1]);
            json!(choices_record(&objects(&args[0]), offered.as_deref(), &names(&args[2]))?)
        }
        "loader:unwrap_mechanic_call" => pair(unwrap_mechanic_call(text(&args[0]))?),
        "loader:text_call" => {
            let callable: Vec<&str> =
                args[1].as_array().unwrap().iter().map(|item| item.as_str().unwrap()).collect();
            pair(text_call(text(&args[0]), &callable)?)
        }
        "loader:wrap_mechanic_arguments" => json!(wrap_mechanic_arguments(text(&args[0]), text(&args[1]))?),
        "loader:project_conversation" => {
            let declared: HashSet<String> = set(&args[1]).into_iter().collect();
            Json::Object(project_conversation(&message(&args[0]), &declared, &names(&args[2]))?)
        }
        "_prefix_fingerprint" => json!(prefix_fingerprint(&objects(&args[0]))),
        "_tools_fingerprint" => json!(tools_fingerprint(optional_objects(&args[0]).as_deref())),
        "toolset_fingerprint" => json!(toolset_fingerprint(optional_objects(&args[0]).as_deref())),
        "merged_tool_list" => {
            let merged = merged_tool_list(&objects(&args[0]), optional_objects(&args[1]).as_deref());
            json!([list(merged.merged), list(merged.added), list(merged.replaced)])
        }
        "_canonical_tool" => Json::Object(canonical_tool(&object(&args[0]))),
        "saved_tool_list" => {
            let owner = arg(1, "context_owner");
            list(saved_tool_list(args[0].as_array().unwrap(), (!owner.is_null()).then_some(&owner)))
        }
        "callable_record" => {
            json!(callable_record(&objects(&args[0]), optional_objects(&args[1]).as_deref()))
        }
        "deferred_definitions" => {
            let core: BTreeSet<String> = set(&args[1]).into_iter().collect();
            let deferral = arg(2, "deferral");
            list(deferred_definitions(&objects(&args[0]), &core, deferral.as_str().unwrap_or("native")))
        }
        "is_tool_change" => json!(is_tool_change(&message(&args[0]))),
        "_surfaced" => json!({ "__set__": surfaced(&objects(&args[0]))?.into_iter().collect::<Vec<_>>() }),
        "_addition_block" => Json::Object(addition_block(&object(&args[0]), args[1].as_bool().unwrap())),
        "projection:schema_choices" => {
            let path = arg(2, "path");
            Json::Object(schema_choices(&object(&args[0]), &args[1], path.as_str().unwrap_or("$"))?)
        }
        "projection:argument_choices_record" => {
            let offered: Option<Vec<Json>> = args[1].as_array().cloned();
            json!(argument_choices_record(args[0].as_array().unwrap(), offered.as_deref())?)
        }
        "projection:unavailable_values" => json!(unavailable_values(&args[0], &args[1], &args[2])?),
        "projection:compact_schema" => Json::Object(compact_schema(object(&args[0]))),
        other => panic!("no Rust function for {other}"),
    })
}

/// A set result's members in sorted order, so Python's repr order and Rust's byte order agree.
fn sorted_sets(value: &Json) -> Json {
    match value {
        Json::Object(map) if map.len() == 1 && map.contains_key("__set__") => {
            let mut members: Vec<Json> = map["__set__"].as_array().unwrap().clone();
            members.sort_by_key(|member| member.to_string());
            json!({ "__set__": members })
        }
        Json::Object(map) => {
            Json::Object(map.iter().map(|(key, item)| (key.clone(), sorted_sets(item))).collect())
        }
        Json::Array(items) => Json::Array(items.iter().map(sorted_sets).collect()),
        other => other.clone(),
    }
}

fn check(group: &str, minimum: usize) {
    let vectors = vectors();
    let tools = vectors["tools"].as_array().unwrap().clone();
    let cases = vectors[group].as_array().unwrap();
    assert!(cases.len() >= minimum, "{group}: {} cases", cases.len());
    for (index, case) in cases.iter().enumerate() {
        let case = resolve(case, &tools);
        let function = text(&case["function"]);
        let found = match run(function, case["args"].as_array().unwrap(), case["kwargs"].as_object().unwrap())
        {
            Ok(value) => json!({ "result": sorted_sets(&value) }),
            Err(raised) => json!({ "raised": { "class": raised.class, "message": raised.message } }),
        };
        let expected = match case.get("raised") {
            Some(raised) => json!({ "raised": raised }),
            None => json!({ "result": sorted_sets(&case["result"]) }),
        };
        assert_same_json(&found, &expected, format!("{group} #{index} {function}"));
    }
}

#[test]
fn traced_tool_list_calls_match_python() {
    check("traced", 380);
}

#[test]
fn synthetic_cases_match_python() {
    check("synthetic", 170);
}
