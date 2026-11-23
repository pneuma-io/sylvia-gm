//! Compare the argument-contract reads and call flattening (T3a) with Python.
//!
//! Regenerate with `tests/vectors/generate_contract_vectors.py`. The cases run on the SRD Lite
//! package's two spell tools, read here from the same package record, and on a small schema
//! with `$defs` the vector file carries.

use serde_json::{json, Value as J};
use std::collections::HashMap;
use sylvia_agent::argument_contracts::{
    argument_contract, read_tool_arguments, selected_schema, unwrap_arguments, ArgumentContract, ContractTool,
};
use sylvia_base::{CodedResult, JsonMap};

/// The small schema, as the generator declares it (`SMALL`).
fn small() -> JsonMap {
    let small = json!({
        "type": "object",
        "properties": {
            "mode": {"type": "string", "x-sylvia-gm-value-aliases": {"quick": "fast"}},
            "actor": {"type": "string"},
            "speed": {"$ref": "#/$defs/speed"},
            "label": {"$ref": "#/$defs/label", "description": "kept as written"},
            "count": {"type": "integer", "minimum": 1},
        },
        "required": ["mode", "speed", "count"],
        "$defs": {"speed": {"$ref": "#/$defs/number"}, "number": {"type": "number", "maximum": 30},
                  "label": {"type": "string"}},
        "x-sylvia-gm-argument-contracts": {
            "version": 1, "selector": "mode", "field": "mode_arguments", "common": ["actor"],
            "by_value": {"fast": ["speed", "label", "count"], "slow": ["count"], "none": []},
        },
    });
    small.as_object().cloned().expect("an object")
}

fn schemas() -> HashMap<String, (JsonMap, ArgumentContract)> {
    let record: J = serde_json::from_str(include_str!("../../../rulesets/srd-lite/package-record.json"))
        .expect("the package record is JSON");
    let mut found = HashMap::new();
    for (name, index) in [("begin_srd_spell_action", 16), ("cast_spell_outside_combat", 19)] {
        let tool = &record["tools"][index];
        assert_eq!(tool["name"], name);
        found.insert(name.to_owned(), tool["input_schema"].as_object().cloned().expect("an object"));
    }
    found.insert("small".to_owned(), small());
    found
        .into_iter()
        .map(|(name, schema)| {
            let contract = argument_contract(&schema).expect("a valid contract").expect("a contract");
            (name, (schema, contract))
        })
        .collect()
}

fn outcome(result: CodedResult<J>) -> J {
    match result {
        Ok(value) => json!({"ok": value}),
        Err(error) => json!({"error": {"type": error.error_type, "message": error.message}}),
    }
}

/// The text of a value with its key order, so order differences fail too.
fn ordered(value: &J) -> String {
    serde_json::to_string(value).expect("serialisable")
}

#[test]
fn contract_reads_and_flattening_match_python() {
    let vectors: J = serde_json::from_str(include_str!("vectors/contract_vectors.json"))
        .expect("vector file is valid JSON");
    let schemas = schemas();
    let cases = vectors["cases"].as_array().expect("cases");
    assert_eq!(cases.len(), 38);
    for (index, case) in cases.iter().enumerate() {
        let actual = match case["function"].as_str().expect("a function") {
            "selected_schema" => {
                let (schema, contract) = &schemas[case["schema"].as_str().expect("a schema")];
                outcome(selected_schema(schema, contract, &case["value"]).map(|found| json!(found)))
            }
            "unwrap_arguments" => {
                let (schema, contract) = &schemas[case["schema"].as_str().expect("a schema")];
                let arguments = case["arguments"].as_object().expect("arguments");
                outcome(
                    unwrap_arguments(arguments, schema, contract).map(|(flat, issues)| json!([flat, issues])),
                )
            }
            "read_tool_arguments" => {
                let tools: Vec<ContractTool> = case["tools"]
                    .as_array()
                    .expect("tools")
                    .iter()
                    .map(|pair| {
                        let listed = pair[0].as_str().expect("a listed name").to_owned();
                        (listed, schemas[pair[1].as_str().expect("a schema")].clone())
                    })
                    .collect();
                outcome(read_tool_arguments(&case["parameters"], &tools).map(J::Object))
            }
            other => panic!("unknown function {other}"),
        };
        assert_eq!(ordered(&actual), ordered(&case["expected"]), "case {index}: {}", case["function"]);
    }
}
