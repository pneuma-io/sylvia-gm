//! Compare the provider tool projection with vectors written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_tool_projection_vectors.py` (needs the snapshot's
//! Python). The tools are every SRD Lite package tool as the catalog projects it for a model,
//! the application's own tools and synthetic tools for each branch and refusal; aliases,
//! descriptions, schemas (with and without the argument-contract envelope), whole provider tool
//! lists and alias resolution must all be Python's, key order included.

use serde_json::{json, Value as J};
use sylvia_agent::provider_session::tools::{
    model_tool_schema, package_tool_alias, provider_tools, resolve_provider_tool_alias, tool_description,
    ToolAliases,
};
use sylvia_agent::stateless_mcp::{ModelMechanicsTool, ModelToolBehavior};
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::testing::assert_same_json;
use sylvia_base::{CodedError, CodedResult};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/tool_projection_vectors.json"))
        .expect("vector file is valid JSON")
}

fn text(value: &J) -> String {
    serde_json::to_string(value).unwrap()
}

fn tool(fields: &J) -> ModelMechanicsTool {
    let get = |key: &str| fields[key].as_str().unwrap().to_owned();
    ModelMechanicsTool {
        package_id: get("package_id"),
        package_version: get("package_version"),
        package_sha256: get("package_sha256"),
        name: get("name"),
        capability: get("capability"),
        input_schema: fields["input_schema"].as_object().unwrap().clone(),
        parameter_derivations: fields["parameter_derivations"].as_object().cloned(),
        description: get("description"),
        category: get("category"),
        model_behavior: ModelToolBehavior::parse(fields["model_behavior"].as_str().unwrap()).unwrap(),
    }
}

fn outcome<T>(result: CodedResult<T>, value: impl FnOnce(T) -> J) -> J {
    match result {
        Ok(found) => json!({"value": value(found)}),
        Err(CodedError { code, message, error_type, .. }) => {
            let class = error_type.unwrap_or("CommandRejected");
            let code = (class == "CommandRejected").then_some(code);
            json!({"error": {"class": class, "message": message, "code": code}})
        }
    }
}

fn alias_pairs(aliases: &ToolAliases) -> J {
    J::Array(aliases.iter().map(|(alias, tool)| json!([alias, tool.name])).collect())
}

#[test]
fn package_tool_aliases_match_python() {
    let vectors = vectors();
    for case in vectors["aliases"].as_array().unwrap() {
        let input: Vec<&str> =
            case["input"].as_array().unwrap().iter().map(|item| item.as_str().unwrap()).collect();
        let alias = package_tool_alias(input[0], input[1], input[2], input[3], input[4]);
        assert_eq!(alias, case["alias"].as_str().unwrap(), "alias of {input:?}");
    }
}

#[test]
fn descriptions_and_schemas_match_python() {
    let vectors = vectors();
    let mut failures = Vec::new();
    for case in vectors["tools"].as_array().unwrap() {
        let projected = tool(&case["tool"]);
        let mut actual = json!({
            "tool": case["tool"],
            "description": {"value": tool_description(&projected)},
            "schema": outcome(model_tool_schema(&projected, true), J::Object),
        });
        if case.get("full_schema").is_some() {
            actual["full_schema"] = outcome(model_tool_schema(&projected, false), J::Object);
        }
        if text(&actual) != text(case) {
            failures.push(format!(
                "{}\n  expected {}\n  got      {}",
                projected.name,
                text(case),
                text(&actual)
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} differ:\n{}",
        failures.len(),
        failures[..failures.len().min(4)].join("\n")
    );
}

#[test]
fn provider_tool_lists_match_python() {
    let vectors = vectors();
    let listed: Vec<ModelMechanicsTool> =
        vectors["tools"].as_array().unwrap().iter().map(|case| tool(&case["tool"])).collect();
    for case in vectors["catalogs"].as_array().unwrap() {
        let catalog: Vec<ModelMechanicsTool> = match case.get("first_tools") {
            Some(count) => listed[..count.as_u64().unwrap() as usize].to_vec(),
            None => case["tools"].as_array().unwrap().iter().map(tool).collect(),
        };
        let digest = case["digest"].as_bool().unwrap();
        let actual = outcome(provider_tools(&catalog), |(tools, aliases)| {
            let tools = J::Array(tools);
            let tools = if digest {
                json!({
                    "count": tools.as_array().unwrap().len(),
                    "sha256": sha256_hex(pyjson::dumps(&tools, Dump::WIRE).as_bytes()),
                })
            } else {
                tools
            };
            json!({"tools": tools, "aliases": alias_pairs(&aliases)})
        });
        let mut expected = case.clone();
        let expected_map = expected.as_object_mut().unwrap();
        for key in ["name", "tools", "first_tools", "digest"] {
            expected_map.shift_remove(key);
        }
        assert_eq!(text(&actual), text(&expected), "catalog {}", case["name"]);
    }
}

#[test]
fn alias_resolution_matches_python() {
    let vectors = vectors();
    let resolve = &vectors["resolve"];
    let catalog: Vec<ModelMechanicsTool> = resolve["catalog"].as_array().unwrap().iter().map(tool).collect();
    let (_, aliases) = provider_tools(&catalog).unwrap();
    for case in resolve["cases"].as_array().unwrap() {
        let requested = case["requested"].as_str().unwrap();
        let actual = resolve_provider_tool_alias(requested, &aliases)
            .map_or(J::Null, |(alias, found)| json!([alias, found.name]));
        assert_same_json(&actual, &case["resolved"], format!("resolving {requested:?}"));
    }
}
