//! The wire journal and Claude Code's native records (L3) against Python's own answers
//! (`tests/vectors/ledger_journal_vectors.json`, written by
//! `generate_ledger_journal_vectors.py`): the calls real ledger-mode GM requests make, the
//! `reopen` session's journals rebuilt after an owner restart among them, and hand-made saved
//! records.

use indexmap::IndexMap;
use serde_json::{json, Value as Json};
use std::collections::{BTreeMap, BTreeSet};
use sylvia_agent::context_ledger::journal::{
    chain, genesis, load_journal, native_answers, native_gap, native_inputs, native_record, saved_tip,
    valid_entry, JournalState,
};
use sylvia_base::testing::assert_same_json;
use sylvia_base::JsonMap;

fn vectors() -> Json {
    sylvia_base::pyjson::loads(include_str!("vectors/ledger_journal_vectors.json")).expect("vectors")
}

fn object(value: &Json) -> JsonMap {
    value.as_object().cloned().unwrap_or_else(|| panic!("an object: {value}"))
}

fn text(value: &Json) -> &str {
    value.as_str().unwrap_or_else(|| panic!("text: {value}"))
}

fn journal(value: &Json) -> JournalState {
    let fields = &value["__journal__"];
    JournalState {
        epoch: text(&fields["epoch"]).to_owned(),
        generation: fields["generation"].clone(),
        entries: fields["entries"].as_array().unwrap().iter().map(object).collect(),
        head: text(&fields["head"]).to_owned(),
        view: fields["view"].as_object().cloned(),
        keyframe_bytes: fields["keyframe_bytes"].as_i64().unwrap(),
        update_bytes: fields["update_bytes"].as_i64().unwrap(),
        heads: fields["heads"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(key, head)| (key.parse().unwrap(), text(head).to_owned()))
            .collect::<BTreeMap<usize, String>>(),
        present: fields["present"]
            .as_array()
            .unwrap()
            .iter()
            .map(|pair| (text(&pair[0]).to_owned(), text(&pair[1]).to_owned()))
            .collect::<BTreeSet<_>>(),
        native: fields["native"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(key, native)| (key.parse().unwrap(), object(native)))
            .collect::<IndexMap<i64, JsonMap>>(),
        thinking_floor: fields["thinking_floor"].as_i64().unwrap(),
        last_prefix: (!fields["last_prefix"].is_null()).then(|| fields["last_prefix"].clone()),
        last_toolset: (!fields["last_toolset"].is_null()).then(|| fields["last_toolset"].clone()),
    }
}

/// A `JournalState` as the vectors hold it.
fn journal_json(state: &JournalState) -> Json {
    let mut present: Vec<Json> = state.present.iter().map(|(label, digest)| json!([label, digest])).collect();
    present.sort_by_key(|pair| pair.to_string());
    json!({ "__journal__": {
        "epoch": state.epoch,
        "generation": state.generation,
        "entries": state.entries,
        "head": state.head,
        "view": state.view,
        "keyframe_bytes": state.keyframe_bytes,
        "update_bytes": state.update_bytes,
        "heads": state.heads.iter().map(|(key, head)| (key.to_string(), json!(head))).collect::<JsonMap>(),
        "present": present,
        "native": state.native.iter().map(|(key, native)| (key.to_string(), Json::Object(native.clone()))).collect::<JsonMap>(),
        "thinking_floor": state.thinking_floor,
        "last_prefix": state.last_prefix,
        "last_toolset": state.last_toolset,
    }})
}

fn keyed<V: serde::Serialize>(map: &IndexMap<i64, V>) -> Json {
    Json::Object(map.iter().map(|(key, value)| (key.to_string(), json!(value))).collect())
}

fn run(function: &str, args: &[Json], kwargs: &JsonMap, record_id: &str) -> Result<Json, (String, String)> {
    let arg = |index: usize, name: &str| -> Json {
        args.get(index).cloned().or_else(|| kwargs.get(name).cloned()).unwrap_or(Json::Null)
    };
    let raised = |error: sylvia_domain::error::DomainError| {
        (error.python_class().to_owned(), error.message().to_owned())
    };
    Ok(match function {
        "_genesis" => json!(genesis(text(&args[0]), &args[1])),
        "_chain" => json!(chain(text(&args[0]), args[1].as_array().unwrap())),
        "_valid_entry" => json!(valid_entry(&args[0]).map_err(raised)?),
        "saved_tip" => match saved_tip(args[0].as_array().unwrap(), text(&args[1])).map_err(raised)? {
            Some(tip) => json!([tip.generation, tip.count, tip.head]),
            None => Json::Null,
        },
        "load_journal" => {
            let owner = arg(2, "context_owner");
            let (state, reason) = load_journal(
                arg(0, "records").as_array().unwrap(),
                text(&arg(1, "epoch")),
                (!owner.is_null()).then_some(&owner),
                text(&arg(3, "seal")),
            )
            .map_err(raised)?;
            json!([journal_json(&state), reason])
        }
        "native_gap" => json!(native_gap(&journal(&args[0]), arg(1, "end").as_u64().unwrap() as usize)),
        "native_inputs" => {
            let inputs = native_inputs(&journal(&args[0])).map_err(raised)?;
            json!([inputs.boundaries, keyed(&inputs.records)])
        }
        "native_answers" => keyed(&native_answers(&journal(&args[0]))),
        "native_record" => {
            let owner = arg(1, "context_owner");
            let answer = arg(7, "answer");
            Json::Object(native_record(
                &journal(&args[0]),
                record_id,
                (!owner.is_null()).then_some(&owner),
                arg(2, "boundary").as_u64().unwrap() as usize,
                text(&arg(3, "status")),
                arg(4, "reason").as_str(),
                arg(5, "records").as_array().unwrap(),
                answer.as_object(),
            ))
        }
        other => panic!("no Rust function for {other}"),
    })
}

fn check(group: &str, minimum: usize) {
    let vectors = vectors();
    let record_id = text(&vectors["record_id"]).to_owned();
    let cases = vectors[group].as_array().unwrap();
    assert!(cases.len() >= minimum, "{group}: {} cases", cases.len());
    for (index, case) in cases.iter().enumerate() {
        let function = text(&case["function"]);
        let found = match run(
            function,
            case["args"].as_array().unwrap(),
            case["kwargs"].as_object().unwrap(),
            &record_id,
        ) {
            Ok(value) => json!({ "result": value }),
            Err((class, message)) => json!({ "raised": { "class": class, "message": message } }),
        };
        let expected = match case.get("raised") {
            Some(raised) => json!({ "raised": raised }),
            None => json!({ "result": case["result"] }),
        };
        assert_same_json(&found, &expected, format!("{group} #{index} {function}"));
    }
}

#[test]
fn traced_journal_calls_match_python() {
    check("traced", 5);
}

#[test]
fn synthetic_cases_match_python() {
    check("synthetic", 95);
}
