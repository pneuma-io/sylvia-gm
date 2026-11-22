//! Compare sylvia_engine::exec with the frozen Python's `execute_tool`.
//!
//! Regenerate with `tests/vectors/generate_execution_vectors.py` (needs the snapshot's Python).

use indexmap::IndexMap;
use serde_json::{json, Value as Json};
use sylvia_base::pyyaml::{self, Data};
use sylvia_engine::exec::tool::data_value;
use sylvia_engine::exec::{execute_tool, Combat, ExecTool, MemoryStateBridge, ToolHost};
use sylvia_engine::expr::value::{float_text, Dict, Value};
use sylvia_engine::ruleset::runtime_tables::load_runtime_tables;

fn vectors() -> Json {
    serde_json::from_str(include_str!("vectors/execution_vectors.json")).expect("valid JSON")
}

struct Host {
    tools: IndexMap<String, ExecTool>,
    ruleset_config: Value,
    definitions: Value,
}

impl ToolHost for Host {
    fn tool(&self, name: &str) -> Option<&ExecTool> {
        self.tools.get(name)
    }
    fn ruleset_config(&self) -> &Value {
        &self.ruleset_config
    }
    fn definitions(&self) -> &Value {
        &self.definitions
    }
}

/// The generator's `plain(value)`.
fn plain(value: &Value) -> Json {
    match value {
        Value::None => Json::Null,
        Value::Bool(flag) => json!(flag),
        Value::Int(number) => json!(number),
        Value::Float(number) if number.is_finite() => json!(number),
        Value::Float(number) => json!({"__float__": float_text(*number)}),
        Value::Str(text) => json!(&**text),
        Value::List(items) | Value::Tuple(items) => Json::Array(items.iter().map(plain).collect()),
        Value::Dict(map) => Json::Object(map.iter().map(|(key, item)| (key.clone(), plain(item))).collect()),
        Value::Object(object) => {
            let mut out = serde_json::Map::new();
            out.insert("__class__".into(), json!(object.class));
            for (name, item) in &object.fields {
                out.insert((*name).to_owned(), plain(item));
            }
            Json::Object(out)
        }
        Value::Method(_) => json!("<method>"),
    }
}

fn dict(json: &Json) -> Dict {
    match Value::from_json(json) {
        Value::Dict(map) => (*map).clone(),
        _ => panic!("expected an object"),
    }
}

/// `dict(value) if isinstance(value, dict) else {}`.
fn section(ruleset: &[(Data, Data)], key: &str) -> Value {
    match ruleset.iter().find(|(k, _)| matches!(k, Data::Str(text) if text == key)) {
        Some((_, value @ Data::Dict(_))) => data_value(value),
        _ => Value::dict(Dict::new()),
    }
}

fn outcome(case: &Json, namespace: &str, state: &Dict, participants: &Dict) -> Json {
    let text = serde_json::to_string(&case["document"]).unwrap();
    let data =
        pyyaml::load(&text, pyyaml::Loader::C, pyyaml::Hooks::default()).unwrap().to_data().unwrap().unwrap();
    let tables = match load_runtime_tables(data.as_dict().unwrap()) {
        Ok(tables) => tables,
        Err(error) => return json!({"load_error": [error.kind.name(), error.message]}),
    };
    let host = Host {
        tools: tables
            .tools
            .iter()
            .filter_map(|(name, tool)| match name {
                Data::Str(name) => Some((name.clone(), ExecTool::from_def(name, tool))),
                _ => None,
            })
            .collect(),
        ruleset_config: section(&tables.ruleset, "features"),
        definitions: section(&tables.ruleset, "definitions"),
    };
    let mut bridge = MemoryStateBridge::new(namespace, state, Some(participants)).unwrap();
    let params = dict(&case["params"]);
    let tool = case["tool"].as_str().unwrap();
    let result = match execute_tool(tool, &params, &host, Some(&mut bridge), Combat::Bridge, None) {
        Ok(result) => result,
        Err(error) => return json!({"raise": [error.class_name(), error.message()]}),
    };
    let mut record = json!({"result": plain(&result.to_value())});
    match (bridge.mutations(), bridge.participant_mutations()) {
        (Ok(mutations), Ok(participants)) => {
            record["mutations"] =
                Json::Array(mutations.into_iter().map(|m| json!([m.namespace, m.key, m.value])).collect());
            record["participant_mutations"] = Json::Array(
                participants.into_iter().map(|m| json!([m.participant_id, Json::Object(m.data)])).collect(),
            );
        }
        (Err(error), _) | (_, Err(error)) => {
            record["mutations_raise"] = json!([error.class_name(), error.message()])
        }
    }
    record
}

#[test]
fn tools_execute_as_python() {
    let vectors = vectors();
    let namespace = vectors["namespace"].as_str().unwrap();
    let state = dict(&vectors["state"]);
    let participants = dict(&vectors["participants"]);
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 2500);
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let actual = outcome(case, namespace, &state, &participants);
        if case.get("wide_int") == Some(&json!(true)) {
            // `differences.md` row 5: the integer Python stores is refused.
            let error = actual["result"]["error"].as_str().unwrap_or_default();
            if !(actual["result"]["success"] == json!(false)
                && error.contains(sylvia_base::pystr::INT_RANGE_MESSAGE))
            {
                failures.push(format!("case {index}: expected the integer-range refusal, rust {actual}"));
            }
            continue;
        }
        let mut wanted = case.clone();
        for key in ["document", "tool", "params"] {
            wanted.as_object_mut().unwrap().remove(key);
        }
        if actual != wanted {
            failures.push(format!(
                "case {index}: {}\n  python {wanted}\n  rust   {actual}",
                case["document"]["tools"]["main"]
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ, first: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(5)]
    );
}
