//! What the execution vector tests share: the host, the generator's `plain(value)` and running
//! one case as `generate_execution_vectors.py`'s `run(...)` does.

use indexmap::IndexMap;
use serde_json::{json, Value as Json};
use std::cell::Cell;
use sylvia_base::pyyaml::{self, Data};
use sylvia_domain::authority::{MechanicsChoiceOption, MechanicsChoiceRequest};
use sylvia_engine::dice::Dice;
use sylvia_engine::exec::context::ChoiceControl;
use sylvia_engine::exec::tool::data_value;
use sylvia_engine::exec::{execute_tool, Combat, ExecError, ExecTool, MemoryStateBridge, ToolHost};
use sylvia_engine::expr::value::{float_text, Dict, Value};
use sylvia_engine::ruleset::runtime_tables::load_runtime_tables;

pub struct Host {
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
pub fn plain(value: &Value) -> Json {
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

fn request_plain(request: &MechanicsChoiceRequest) -> Json {
    json!({
        "__class__": "RuntimeChoiceRequest",
        "choice_id": request.choice_id,
        "prompt": request.prompt,
        "options": request.options.iter().map(|o| json!({"__class__": "RuntimeChoiceOption", "option_id": o.option_id, "label": o.label})).collect::<Vec<_>>(),
    })
}

fn request_of(json: &Json) -> MechanicsChoiceRequest {
    MechanicsChoiceRequest {
        choice_id: json["choice_id"].as_str().unwrap().into(),
        prompt: json["prompt"].as_str().unwrap().into(),
        options: json["options"]
            .as_array()
            .unwrap()
            .iter()
            .map(|o| MechanicsChoiceOption {
                option_id: o["option_id"].as_str().unwrap().into(),
                label: o["label"].as_str().unwrap().into(),
            })
            .collect(),
    }
}

pub fn dict(json: &Json) -> Dict {
    match Value::from_json(json) {
        Value::Dict(map) => (*map).clone(),
        _ => panic!("expected an object"),
    }
}

fn section(ruleset: &[(Data, Data)], key: &str) -> Value {
    match ruleset.iter().find(|(k, _)| matches!(k, Data::Str(text) if text == key)) {
        Some((_, value @ Data::Dict(_))) => data_value(value),
        _ => Value::dict(Dict::new()),
    }
}

/// One case's record, in the generator's shape.
pub fn outcome(case: &Json, namespace: &str, state: &Dict, participants: &Dict) -> Json {
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
    let control = case.get("choice").map(|choice| ChoiceControl {
        choice_id: choice["choice_id"].as_str().unwrap().into(),
        option_id: choice["option_id"].as_str().unwrap().into(),
        expected_request: request_of(&choice["expected_request"]),
        consumed: Cell::new(false),
        prior_answers: choice["prior_answers"]
            .as_array()
            .unwrap()
            .iter()
            .map(|pair| (request_of(&pair[0]), pair[1].as_str().unwrap().to_owned()))
            .collect(),
        replayed_answers: Cell::new(0),
    });
    let mut dice = Dice::new();
    let seed = case.get("seed").and_then(Json::as_i64).map(i128::from);
    let result = dice.seeded_rolls(seed, |dice| {
        execute_tool(tool, &params, &host, Some(&mut bridge), Combat::Bridge, None, dice, control.as_ref())
    });
    let control_record = control.as_ref().map(|c| json!([c.consumed.get(), c.replayed_answers.get()]));
    let result = match result {
        Ok(result) => result,
        Err(ExecError::Suspended(request)) => {
            let mut record = json!({"raise": ["MechanicsChoiceSuspended", "Package mechanic is awaiting an authorized choice"],
                                    "request": request_plain(&request)});
            if let Some(control) = control_record {
                record["control"] = control;
            }
            return record;
        }
        Err(error) => return json!({"raise": [error.class_name(), error.message()]}),
    };
    let mut record = json!({"result": plain(&result.to_value())});
    if let Some(control) = control_record {
        record["control"] = control;
    }
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

/// Compare every case of a vector file; `wide_int` cases expect the integer-range refusal and
/// shuffled lists compare as multisets.
pub fn check_all(vectors: &Json, minimum: usize) {
    let namespace = vectors["namespace"].as_str().unwrap();
    let state = dict(&vectors["state"]);
    let participants = dict(&vectors["participants"]);
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > minimum);
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let mut actual = outcome(case, namespace, &state, &participants);
        if case.get("wide_int") == Some(&json!(true)) {
            let error = actual["result"]["error"].as_str().unwrap_or_default();
            if !(actual["result"]["success"] == json!(false)
                && error.contains(sylvia_base::pystr::INT_RANGE_MESSAGE))
            {
                failures.push(format!("case {index}: expected the integer-range refusal, rust {actual}"));
            }
            continue;
        }
        let mut wanted = case.clone();
        for key in ["document", "tool", "params", "seed", "choice", "shuffled"] {
            wanted.as_object_mut().unwrap().remove(key);
        }
        if let Some(names) = case.get("shuffled").and_then(Json::as_array) {
            for name in names.iter().filter_map(Json::as_str) {
                for record in [&mut actual, &mut wanted] {
                    if let Some(Json::Array(items)) = record.pointer_mut(&format!("/result/context/{name}")) {
                        items.sort_by_key(|item| item.to_string());
                    }
                }
            }
        }
        if actual != wanted {
            failures.push(format!(
                "case {index}: {}\n  python {wanted}\n  rust   {actual}",
                case["document"]["tools"]["main"]["effects"]
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
