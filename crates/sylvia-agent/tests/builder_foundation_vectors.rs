//! Compare the Builder foundations (B1) with the frozen Python: the mode, the tracked skills,
//! the first-reply title, the question form, the provider history projection and native
//! continuation, the Builder prompt and the tool evidence.
//!
//! Regenerate with `tests/vectors/generate_builder_foundation_vectors.py` (needs the snapshot's
//! Python).

use serde_json::json;
use sylvia_agent::builder::evidence::{
    builder_durable_tool_result, builder_tool_evidence, tool_call_details,
};
use sylvia_agent::builder::history::{
    builder_native_request, coalesce_builder_responses, provider_history, NativeReader,
};
use sylvia_agent::builder::mode::AgentMode;
use sylvia_agent::builder::prompt::{builder_prompt, creation_subflow_purpose, effective_tracked_skill};
use sylvia_agent::builder::question_answers::{builder_question_answer_content, builder_question_details};
use sylvia_agent::builder::skills::{builder_skill_references, load_builder_skill};
use sylvia_agent::builder::title::{opening_title, FirstReplyTitle};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, CodedError, CodedResult, Json, JsonMap};
use sylvia_domain::authority::WriterRole;
use sylvia_domain::session::{
    SessionContext, SessionMessage, SessionMessageKind, SessionRecord, SessionRecordFields,
};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/builder_foundation_vectors.json")).expect("vector file is valid JSON")
}

fn text(value: &Json) -> Option<String> {
    value.as_str().map(str::to_owned)
}

fn context(spec: &Json) -> SessionContext {
    let session = &spec["session"];
    let role = match session["role"].as_str().unwrap() {
        "builder" => WriterRole::Builder,
        "dm" => WriterRole::Dm,
        other => panic!("role {other}"),
    };
    let session = SessionRecord::new(SessionRecordFields {
        session_id: text(&session["session_id"]).unwrap(),
        campaign_id: text(&session["campaign_id"]),
        role,
        provider_id: text(&session["provider_id"]).unwrap(),
        model_id: text(&session["model_id"]).unwrap(),
        selected_skill: text(&session["selected_skill"]),
        ..Default::default()
    })
    .unwrap();
    let messages = spec["messages"]
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
        .map(|(index, row)| {
            SessionMessage::new(
                session.session_id(),
                index as i64 + 1,
                row[1].as_str().unwrap(),
                SessionMessageKind::parse(row[0].as_str().unwrap()).unwrap(),
                &row[2],
                None,
            )
            .unwrap()
        })
        .collect();
    SessionContext { session, messages }
}

/// Python's `[class, message, code]`; a built-in exception has no code, so Rust's is not compared.
fn outcome<T>(result: CodedResult<T>, python: &Json, ok: impl FnOnce(T) -> Json) -> Json {
    match result {
        Ok(value) => json!(["ok", ok(value)]),
        Err(CodedError { code, message, error_type, .. }) => {
            let code = if python.get(2).is_some_and(Json::is_null) { Json::Null } else { json!(code) };
            json!([error_type.unwrap_or("CommandRejected"), message, code])
        }
    }
}

fn maps(value: &Json) -> Vec<JsonMap> {
    value.as_array().unwrap().iter().map(|item| item.as_object().unwrap().clone()).collect()
}

fn list(items: Vec<JsonMap>) -> Json {
    Json::Array(items.into_iter().map(Json::Object).collect())
}

struct Failures(Vec<String>, usize);

impl Failures {
    fn check(&mut self, what: impl std::fmt::Display, python: &Json, rust: Json) {
        self.1 += 1;
        if &rust != python {
            let (python, rust) = (python.to_string(), rust.to_string());
            self.0.push(format!(
                "{what}: python {}, rust {}",
                &python[..python.len().min(500)],
                &rust[..rust.len().min(500)]
            ));
        }
    }
}

#[test]
fn builder_foundations_match_python() {
    let vectors = vectors();
    let mut failures = Failures(Vec::new(), 0);

    let modes: Vec<Json> = [AgentMode::BuilderInterview, AgentMode::BuilderFreeform, AgentMode::DmSession]
        .map(|mode| json!(mode.as_str()))
        .to_vec();
    failures.check("modes", &vectors["modes"], Json::Array(modes));
    for value in vectors["modes"].as_array().unwrap() {
        assert_eq!(AgentMode::parse(value.as_str().unwrap()).map(AgentMode::as_str), value.as_str());
    }

    for case in vectors["skills"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let references = outcome(builder_skill_references(name), &case["references"], |list| json!(list));
        failures.check(format!("skill {name} references"), &case["references"], references);
        for (reference, python) in case["loads"].as_object().unwrap() {
            let chosen = (!reference.is_empty()).then_some(reference.as_str());
            let rust = outcome(load_builder_skill(name, chosen), python, |skill| {
                json!(sha256_hex(skill.text.as_bytes()))
            });
            failures.check(format!("skill {name} load {reference}"), python, rust);
        }
    }

    for pair in vectors["titles"]["openings"].as_array().unwrap() {
        failures.check(
            format!("opening {}", pair[0]),
            &pair[1],
            json!(opening_title(pair[0].as_str().unwrap())),
        );
    }
    for case in vectors["titles"]["feeds"].as_array().unwrap() {
        let mut stripper = FirstReplyTitle::new();
        let outputs: Vec<Json> = case["chunks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|chunk| json!(stripper.feed(chunk[0].as_str().unwrap(), chunk[1].as_bool().unwrap())))
            .collect();
        failures.check(
            format!("feed {}", case["chunks"]),
            &json!([case["outputs"], case["title"]]),
            json!([outputs, stripper.title]),
        );
    }

    for pair in vectors["questions"]["details"].as_array().unwrap() {
        let rust = builder_question_details(pair[0].as_object().unwrap()).map_or(Json::Null, Json::Object);
        failures.check(format!("details {}", pair[0]), &pair[1], json!(["ok", rust]));
    }
    for case in vectors["questions"]["answers"].as_array().unwrap() {
        let result = builder_question_answer_content(
            &context(&case["context"]),
            &case["answers"],
            case["text"].as_str().unwrap(),
        );
        let rust = outcome(result, &case["outcome"], Json::Object);
        failures.check(format!("answers {}", case["name"]), &case["outcome"], rust);
    }

    for case in vectors["histories"].as_array().unwrap() {
        let exclude = case["exclude"].as_str();
        let rust = outcome(provider_history(&context(&case["context"]), exclude), &case["outcome"], list);
        failures.check(format!("history {} without {:?}", case["name"], exclude), &case["outcome"], rust);
    }
    for pair in vectors["coalesce"].as_array().unwrap() {
        failures.check(
            format!("coalesce {}", pair[0]),
            &pair[1],
            list(coalesce_builder_responses(maps(&pair[0]))),
        );
    }

    let native = &vectors["native"];
    let messages = maps(&native["messages"]);
    for case in native["cases"].as_array().unwrap() {
        let retained = native["natives"][case["native"].as_str().unwrap()].as_object().unwrap().clone();
        let read = move |id: &str| retained.get(id).and_then(Json::as_object).cloned();
        let identity =
            case["identity"].as_array().map(|pair| (pair[0].as_str().unwrap(), pair[1].as_str().unwrap()));
        let reader: Option<NativeReader<'_>> =
            if case["read"].as_bool().unwrap() { Some(&read) } else { None };
        let result = builder_native_request(
            &messages,
            case["transport"].as_str(),
            reader,
            case["resend"].as_bool().unwrap(),
            identity,
        );
        let rust = outcome(result, &case["outcome"], list);
        let what = format!(
            "native {} {} resend={} {} read={}",
            case["transport"], case["native"], case["resend"], case["identity"], case["read"]
        );
        failures.check(what, &case["outcome"], rust);
    }

    for case in vectors["prompts"].as_array().unwrap() {
        let context = context(&case["context"]);
        let prompt = builder_prompt(&context).unwrap();
        let rust = json!({
            "purpose": creation_subflow_purpose(&context), "skill": effective_tracked_skill(&context),
            "sha256": sha256_hex(prompt.as_bytes()), "length": prompt.chars().count(),
        });
        let python = json!({"purpose": case["purpose"], "skill": case["skill"], "sha256": case["sha256"], "length": case["length"]});
        failures.check(format!("prompt {}", case["name"]), &python, rust);
    }

    let evidence = &vectors["evidence"];
    let details = tool_call_details(&maps(&evidence["messages"]));
    let keys: Vec<&String> = details.keys().collect();
    let python_keys: Vec<&String> = evidence["details"].as_object().unwrap().keys().collect();
    failures.check("detail order", &json!(python_keys), json!(keys));
    let details_json =
        Json::Object(details.iter().map(|(id, call)| (id.clone(), Json::Object(call.clone()))).collect());
    failures.check("details", &evidence["details"], details_json);
    for pair in evidence["tools"].as_array().unwrap() {
        let rust =
            outcome(builder_tool_evidence(pair[0].as_object().unwrap(), &details), &pair[1], Json::Object);
        failures.check(format!("evidence {}", pair[0]), &pair[1], rust);
    }
    for case in evidence["durable"].as_array().unwrap() {
        let rust = builder_durable_tool_result(case[0].as_str().unwrap(), &case[1]);
        failures.check(format!("durable {}", case[1]), &case[2], rust);
    }

    let Failures(failures, total) = failures;
    assert!(failures.is_empty(), "{} of {total} checks differ:\n{}", failures.len(), failures.join("\n"));
}
