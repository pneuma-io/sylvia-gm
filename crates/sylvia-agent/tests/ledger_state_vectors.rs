//! The context ledger's wire and state functions (L2a) against Python's own answers
//! (`tests/vectors/ledger_state_vectors.json`, written by `generate_ledger_state_vectors.py`):
//! the calls real ledger-mode GM requests make, and hand-made cases for the rules they do not
//! reach.

use serde_json::{json, Value as Json};
use sylvia_agent::context_ledger::state::{
    apply_state_update, comparable_facts, fact_key, narration_facts_given, parse_state, reconstruct_state,
    request_values, slim_narration_facts, slim_transition_result, split_narration_facts, state_covers,
    state_target, state_update, update_record_content,
};
use sylvia_agent::context_ledger::wire::{
    conversation_keys, is_data_record, is_host_record, label, wire_message,
};
use sylvia_agent::context_ledger::Tagged;
use sylvia_base::testing::assert_same_json;
use sylvia_base::JsonMap;
use sylvia_domain::error::DomainResult;

fn vectors() -> Json {
    sylvia_base::pyjson::loads(include_str!("vectors/ledger_state_vectors.json")).expect("vectors")
}

fn object(value: &Json) -> JsonMap {
    value.as_object().cloned().unwrap_or_else(|| panic!("an object: {value}"))
}

fn optional_object(value: &Json) -> Option<JsonMap> {
    (!value.is_null()).then(|| object(value))
}

fn tagged(value: &Json) -> Tagged {
    match value.get("__tagged__") {
        Some(inner) => Tagged { message: object(&inner["message"]), tags: object(&inner["tags"]) },
        None => Tagged::plain(object(value)),
    }
}

fn tagged_list(value: &Json) -> Vec<Tagged> {
    value.as_array().unwrap().iter().map(tagged).collect()
}

fn objects(value: &Json) -> Vec<JsonMap> {
    value.as_array().unwrap().iter().map(|item| tagged(item).message).collect()
}

fn text(value: &Json) -> &str {
    value.as_str().unwrap_or_else(|| panic!("text: {value}"))
}

/// A Python answer: `{"result": ...}` or `{"raised": {"class", "message"}}`.
fn answer(result: DomainResult<Json>) -> Json {
    match result {
        Ok(value) => json!({ "result": value }),
        Err(error) => json!({ "raised": { "class": error.python_class(), "message": error.message() } }),
    }
}

fn run(function: &str, args: &[Json], kwargs: &JsonMap) -> DomainResult<Json> {
    let arg = |index: usize, name: &str| -> Json {
        args.get(index).cloned().or_else(|| kwargs.get(name).cloned()).unwrap_or(Json::Null)
    };
    Ok(match function {
        "_label" => json!(label(text(&args[0]))),
        "is_host_record" => json!(is_host_record(&tagged(&args[0]))),
        "_is_data_record" => json!(is_data_record(&tagged(&args[0]).message)),
        "_wire_message" => Json::Object(wire_message(&tagged(&args[0]))),
        "conversation_keys" => json!(conversation_keys(&tagged_list(&args[0]))),
        "request_values" => {
            let (entities, namespaces) = request_values(optional_object(&arg(0, "state")).as_ref())?;
            json!([entities, namespaces])
        }
        "slim_narration_facts" => {
            json!(slim_narration_facts(args[0].as_array().unwrap(), &object(&args[1])))
        }
        "comparable_facts" => json!(comparable_facts(args[0].as_array().unwrap())),
        "slim_transition_result" => {
            Json::Object(slim_transition_result(&object(&args[0]), &object(&args[1]), &object(&args[2])))
        }
        "split_narration_facts" => match split_narration_facts(text(&args[0]))? {
            Some(record) => json!([record.added, record.facts, record.rest]),
            None => Json::Null,
        },
        "narration_facts_given" => json!(narration_facts_given(&objects(&args[0]), text(&arg(1, "seal")))?),
        "state_target" => Json::Object(state_target(
            optional_object(&args[0]).as_ref(),
            &object(&args[1]),
            optional_object(&arg(2, "active")).as_ref(),
        )),
        "state_covers" => json!(state_covers(
            optional_object(&args[0]).as_ref(),
            &object(&args[1]),
            optional_object(&arg(2, "active")).as_ref(),
        )),
        "state_update" => {
            state_update(&object(&args[0]), &object(&args[1]), optional_object(&arg(2, "active")).as_ref())
                .map_or(Json::Null, Json::Object)
        }
        "apply_state_update" => Json::Object(apply_state_update(&object(&args[0]), &object(&args[1]))?),
        "update_record_content" => json!(update_record_content(&object(&args[0]))),
        "reconstruct_state" => {
            reconstruct_state(&objects(&args[0]), text(&arg(1, "seal")))?.map_or(Json::Null, Json::Object)
        }
        "fact_key" => json!(fact_key(&args[0])),
        "_parse_state" => parse_state(text(&args[0])).map_or(Json::Null, Json::Object),
        other => panic!("no Rust function for {other}"),
    })
}

fn check(group: &str, minimum: usize) {
    let vectors = vectors();
    let cases = vectors[group].as_array().unwrap();
    assert!(cases.len() >= minimum, "{group}: {} cases", cases.len());
    for (index, case) in cases.iter().enumerate() {
        let function = text(&case["function"]);
        let args = case["args"].as_array().unwrap();
        let found = answer(run(function, args, case["kwargs"].as_object().unwrap()));
        let expected = match case.get("raised") {
            Some(raised) => json!({ "raised": raised }),
            None => json!({ "result": case["result"] }),
        };
        assert_same_json(&found, &expected, format!("{group} #{index} {function}"));
    }
}

#[test]
fn traced_ledger_calls_match_python() {
    check("traced", 85);
}

#[test]
fn synthetic_cases_match_python() {
    check("synthetic", 150);
}

#[test]
fn wire_messages_keep_their_tags_apart() {
    // The tags are never part of the wire form.
    let mut message = Tagged::plain(object(&json!({"role": "user", "content": "x"})));
    message.tags.insert("_ledger_key".into(), json!("user:1"));
    let wire = wire_message(&message);
    assert_same_json(&Json::Object(wire), &json!({"role": "user", "content": "x"}), "wire");
}
