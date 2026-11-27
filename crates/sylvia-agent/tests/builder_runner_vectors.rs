//! Compare the Builder runner (B2b) with the frozen Python: each scenario's streaming turns with
//! the same scripted provider, then every request, event, activity call, outcome and the
//! history afterwards; and the construction refusals.
//!
//! Regenerate with `tests/vectors/generate_builder_runner_vectors.py` (needs the snapshot's
//! Python).

use futures_util::{stream, StreamExt};
use serde_json::json;
use std::collections::VecDeque;
use std::path::Path;
use std::sync::{Arc, Mutex};
use sylvia_agent::builder::mode::AgentMode;
use sylvia_agent::builder::runner::{AgentRunner, RunnerOptions, TurnError};
use sylvia_agent::builder::tools::{ToolHandler, ToolSpec, ToolsetName};
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_provider::error::{ErrorKind, ProviderError};
use sylvia_provider::types::{ModelInfo, ProviderResponse, ProviderStreamChunk, PyValue};
use sylvia_provider::{BoxFuture, ChunkStream, Provider, ProviderRequest, ProviderResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/builder_runner_vectors.json")).expect("vector file is valid JSON")
}

/// The generator's scripted provider: each request answers with the next scripted response.
struct Scripted {
    responses: Mutex<VecDeque<Json>>,
    requests: Mutex<Vec<Json>>,
    definitions: Mutex<Option<Json>>,
}

fn chunk(item: &JsonMap) -> ProviderResult<ProviderStreamChunk> {
    match item.get("raise").and_then(Json::as_str) {
        Some("output_limit") => {
            return Err(ProviderError::new(ErrorKind::OutputLimit, "Output allowance reached"))
        }
        Some("continuation") => {
            return Err(ProviderError::new(ErrorKind::ContinuationRequired, "Continuation required"))
        }
        _ => {}
    }
    let text = |key: &str| item.get(key).and_then(Json::as_str).unwrap_or_default().to_owned();
    Ok(ProviderStreamChunk {
        content: text("content"),
        reasoning_content: text("reasoning_content"),
        tool_call_delta: item.get("tool_call_delta").cloned(),
        finish_reason: item
            .get("finish_reason")
            .and_then(Json::as_str)
            .map(|reason| PyValue::Text(reason.into())),
        anthropic_content: item.get("anthropic_content").and_then(Json::as_array).cloned(),
        chat_completion_metadata: item.get("chat_completion_metadata").and_then(Json::as_object).cloned(),
        ..ProviderStreamChunk::default()
    })
}

impl Provider for Scripted {
    fn model_info(&self) -> Option<&ModelInfo> {
        None
    }
    fn generate(&self, _request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        unreachable!("the Builder streams")
    }
    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        let names = request
            .tools
            .as_ref()
            .map(|tools| Json::Array(tools.iter().map(|tool| tool["function"]["name"].clone()).collect()));
        self.requests.lock().unwrap().push(json!({
            "messages": request.messages, "tools": names.unwrap_or(Json::Null), "options": request.options,
        }));
        let mut definitions = self.definitions.lock().unwrap();
        if definitions.is_none() {
            *definitions = Some(request.tools.clone().map_or(Json::Null, Json::Array));
        }
        let response = self.responses.lock().unwrap().pop_front().expect("a scripted response");
        let mut items = Vec::new();
        let mut hang = false;
        for item in response.as_array().unwrap() {
            if item.get("hang").is_some() {
                hang = true;
                break;
            }
            let next = chunk(item.as_object().unwrap());
            let stop = next.is_err();
            items.push(next);
            if stop {
                break;
            }
        }
        if hang {
            // The response never ends; the turn is cancelled, as Stop cancels it.
            return Box::pin(stream::iter(items).chain(stream::pending()));
        }
        Box::pin(stream::iter(items))
    }
    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }
}

/// The generator's application tools: each answers from its own results, the last repeating.
fn application_tools(vectors: &Json) -> Vec<ToolSpec> {
    let mut tools = Vec::new();
    for (name, results) in vectors["application"].as_object().unwrap() {
        let state = Arc::new(Mutex::new(results.as_array().unwrap().clone()));
        let handler: ToolHandler = Arc::new(move |_params: &JsonMap| {
            let mut results = state.lock().unwrap();
            Ok(if results.len() == 1 { results[0].clone() } else { results.remove(0) })
        });
        let definition = json!({"type": "function", "function": {"name": name, "description": format!("{name} tool"),
            "parameters": {"type": "object", "properties": {}}}});
        tools
            .push(ToolSpec::from_openai_schema(&definition, handler, &[ToolsetName::BUILDER_TOOLS]).unwrap());
    }
    tools
}

fn mask(value: &Json, roots: &[String]) -> Json {
    let mut text = serde_json::to_string(value).unwrap();
    for root in roots {
        for form in [root.replace('\\', "\\\\\\\\"), root.replace('\\', "\\\\"), root.replace('\\', "/")] {
            text = text.replace(&form, "<ROOT>");
        }
    }
    pyjson::loads(&text).unwrap()
}

fn outcome(result: Result<(), TurnError>) -> Json {
    match result {
        Ok(()) => json!(["ok"]),
        Err(TurnError::Loop(error)) => json!(["AgentLoopError", error.user_message]),
        Err(TurnError::Provider(error)) => json!([error.kind.class_name(), error.message]),
        Err(TurnError::Coded(error)) => json!([error.error_type.unwrap_or("CommandRejected"), error.message]),
    }
}

fn fresh_work(name: &str) -> std::path::PathBuf {
    let work = Path::new(env!("CARGO_TARGET_TMPDIR")).join(name);
    match std::fs::remove_dir_all(&work) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => panic!("cannot clear the old work folder {}: {error}", work.display()),
    }
    work
}

#[test]
fn builder_runner_matches_python() {
    let vectors = vectors();
    let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
    let mut failures = Vec::new();
    let mut check = |what: String, python: &Json, rust: Json| {
        if &rust != python {
            let (python, rust) = (python.to_string(), rust.to_string());
            failures.push(format!(
                "{what}: python {}, rust {}",
                &python[..python.len().min(900)],
                &rust[..rust.len().min(900)]
            ));
        }
    };
    for scenario in vectors["scenarios"].as_array().unwrap() {
        let name = scenario["name"].as_str().unwrap();
        let options = &scenario["options"];
        let work = fresh_work(&format!("builder-runner-{name}"));
        let workspace = work.join("workspace");
        std::fs::create_dir_all(&workspace).unwrap();
        let roots = vec![
            sylvia_app::path_guardrail::PathGuardrail::new(&workspace).allowed_root().to_owned(),
            workspace.display().to_string(),
            sylvia_app::path_guardrail::PathGuardrail::new(&work).allowed_root().to_owned(),
            work.display().to_string(),
        ];
        let responses: VecDeque<Json> = scenario["script"]
            .as_array()
            .unwrap()
            .iter()
            .flat_map(|script| script.as_array().unwrap().clone())
            .collect();
        let provider = Arc::new(Scripted {
            responses: Mutex::new(responses),
            requests: Mutex::new(Vec::new()),
            definitions: Mutex::new(None),
        });
        let activity = Arc::new(Mutex::new(Vec::<Json>::new()));
        let recorder = Arc::clone(&activity);
        let runner_options = RunnerOptions {
            ruleset_name: Some("campaign".into()),
            system_prompt_override: Some(
                options["override"].as_str().unwrap_or("You are the test Builder.").into(),
            ),
            prompt_mode: "replace".into(),
            trusted_system_override: options["trusted"].as_bool().unwrap_or(true),
            builder_application_tools: application_tools(&vectors),
            allow_public_web: options["allow_public_web"].as_bool().unwrap_or(false),
            activity_progress: Some(Arc::new(move |phase: &str, tool: Option<&str>| {
                recorder.lock().unwrap().push(json!([phase, tool]));
            })),
            ..RunnerOptions::default()
        };
        let initial: Vec<JsonMap> = options["initial_history"]
            .as_array()
            .map(|history| history.iter().map(|item| item.as_object().unwrap().clone()).collect())
            .unwrap_or_default();
        let mut runner = AgentRunner::new(
            AgentMode::BuilderFreeform,
            Arc::clone(&provider) as Arc<dyn Provider>,
            initial,
            &workspace,
            runner_options,
        )
        .unwrap();
        let mut counter = 0u32;
        runner.set_response_ids(Box::new(move || {
            counter += 1;
            format!("{counter:032x}")
        }));
        let mut steer: VecDeque<Option<String>> = options["steer"]
            .as_array()
            .map(|items| items.iter().map(|item| item.as_str().map(str::to_owned)).collect())
            .unwrap_or_default();
        let steering = options.get("steer").is_some();
        for (index, turn) in scenario["turns"].as_array().unwrap().iter().enumerate() {
            let before = provider.requests.lock().unwrap().len();
            activity.lock().unwrap().clear();
            let (sender, mut receiver) = tokio::sync::mpsc::unbounded_channel();
            let mut between = || {
                let next = steer.pop_front().flatten();
                Box::pin(async move { next })
                    as std::pin::Pin<Box<dyn std::future::Future<Output = Option<String>> + Send>>
            };
            let turn_future = runner.turn_stream(
                turn["text"].as_str().unwrap(),
                &sender,
                if steering { Some(&mut between) } else { None },
                options["application_prompt"].as_str(),
            );
            let timed = runtime.block_on(async {
                tokio::time::timeout(std::time::Duration::from_secs(5), turn_future).await
            });
            let result = match timed {
                Ok(result) => outcome(result),
                Err(_) => json!(["TimeoutError", ""]),
            };
            let mut events = Vec::new();
            while let Ok(event) = receiver.try_recv() {
                events.push(json!([
                    event.kind.as_str(),
                    event.data,
                    event.meta.map_or(Json::Null, Json::Object)
                ]));
            }
            let requests = provider.requests.lock().unwrap()[before..].to_vec();
            let label = |what: &str| format!("{name} turn {index} {what}");
            check(label("outcome"), &turn["outcome"], result);
            check(label("requests"), &turn["requests"], mask(&Json::Array(requests), &roots));
            check(label("events"), &turn["events"], mask(&Json::Array(events), &roots));
            check(label("activity"), &turn["activity"], Json::Array(activity.lock().unwrap().clone()));
            let history = Json::Array(runner.history_snapshot().into_iter().map(Json::Object).collect());
            check(label("history"), &turn["history"], mask(&history, &roots));
        }
        let definitions = provider.definitions.lock().unwrap().clone().unwrap_or(Json::Null);
        check(format!("{name} definitions"), &scenario["definitions"], mask(&definitions, &roots));
        let _ = std::fs::remove_dir_all(&work);
    }

    let work = fresh_work("builder-runner-construction");
    std::fs::create_dir_all(&work).unwrap();
    let plain = |name: &str, toolsets: &[&'static str]| {
        let handler: ToolHandler = Arc::new(|_params: &JsonMap| Ok(json!({})));
        ToolSpec::from_openai_schema(
            &json!({"type": "function", "function": {"name": name}}),
            handler,
            toolsets,
        )
        .unwrap()
    };
    for case in vectors["construction"].as_array().unwrap() {
        let name = case[0].as_str().unwrap();
        let (mode, tools) = match name {
            "dm" => (AgentMode::DmSession, Vec::new()),
            "collision" => {
                (AgentMode::BuilderFreeform, vec![plain("read_file", &[ToolsetName::BUILDER_TOOLS])])
            }
            "toolset" => (AgentMode::BuilderFreeform, vec![plain("other", &[ToolsetName::INTERVIEW_TOOLS])]),
            "two_toolsets" => (
                AgentMode::BuilderFreeform,
                vec![plain("other", &[ToolsetName::INTERVIEW_TOOLS, ToolsetName::BUILDER_TOOLS])],
            ),
            other => panic!("construction case {other}"),
        };
        let provider: Arc<dyn Provider> = Arc::new(Scripted {
            responses: Mutex::new(VecDeque::new()),
            requests: Mutex::new(Vec::new()),
            definitions: Mutex::new(None),
        });
        let options = RunnerOptions { builder_application_tools: tools, ..RunnerOptions::default() };
        let rust = match AgentRunner::new(mode, provider, Vec::new(), &work, options) {
            Ok(_) => json!(["ok"]),
            Err(error) => json!([error.error_type.unwrap_or("CommandRejected"), error.message]),
        };
        check(format!("construction {name}"), &case[1], rust);
    }
    let _ = std::fs::remove_dir_all(&work);
    assert!(failures.is_empty(), "{} checks differ:\n{}", failures.len(), failures.join("\n"));
}
