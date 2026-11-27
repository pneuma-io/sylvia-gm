//! The Ruleset test worker's pieces (B4a) against Python: `builder::ruleset_tests` and
//! `sylvia_app::ruleset_testing`.
//!
//! Regenerate with `tests/vectors/generate_ruleset_test_vectors.py`. Every expected value is
//! what Python's code returned or raised on the same input. `run_scenario` stops where the
//! campaign is created, so a scenario Python took past `create_campaign` must answer
//! `rust_port_pending` here, and one Python failed before it must fail the same way.
//!
//! The parent's worker is this test executable: [`stand_in_worker`] follows the protocol of the
//! generator's stand-in script (`--payload FILE`, `--exit CODE`, `--hang`, `--echo`), and
//! libtest leaves everything after `--` to it.

mod common;

use common::{session_context, session_record};
use futures_util::StreamExt;
use serde_json::{json, Value as J};
use std::collections::{BTreeSet, VecDeque};
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use sylvia_agent::builder::ruleset_tests::{
    assertions, checked_outcomes, failure_message, run_scenario, summarize_coverage, RulesetTestProvider,
    ScenarioDice, ScenarioError,
};
use sylvia_agent::provider_turn::TurnFailure;
use sylvia_agent::session_loop::{
    ProviderControl, ProviderEvent, SessionEvents, SessionFailure, SessionProvider, SessionProviderError,
    SessionProviderInput,
};
use sylvia_app::ruleset_testing::{run_ruleset_tests_with, WorkerCommand};
use sylvia_base::{CodedError, JsonMap};
use sylvia_engine::dice::RollSource;
use sylvia_provider::types::PyValue;
use sylvia_provider::{BoxFuture, Provider, ProviderRequest};

fn vectors() -> &'static J {
    static VECTORS: OnceLock<J> = OnceLock::new();
    VECTORS.get_or_init(|| {
        serde_json::from_str(include_str!("vectors/ruleset_test_vectors.json")).expect("the recorded vectors")
    })
}

fn map(value: &J) -> JsonMap {
    value.as_object().cloned().unwrap_or_default()
}

fn maps(value: &J) -> Vec<JsonMap> {
    value.as_array().into_iter().flatten().map(map).collect()
}

fn set(value: &J) -> BTreeSet<String> {
    value.as_array().into_iter().flatten().map(|item| item.as_str().unwrap().to_owned()).collect()
}

/// A fresh, empty folder for one case, removed when dropped.
struct Scratch(PathBuf);

impl Scratch {
    fn new(name: &str) -> Self {
        let path = std::env::temp_dir().join(format!("sylvia-ruleset-vectors-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).unwrap();
        Self(path)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

#[test]
fn checked_outcomes_match_python() {
    for case in vectors()["checked_outcomes"].as_array().unwrap() {
        let receipt = case["receipt"].as_object();
        let actual =
            checked_outcomes(case["assertions"].as_array().unwrap(), receipt, &maps(&case["tool_results"]));
        assert_eq!(J::Object(actual), case["expected"], "{}", case["name"]);
    }
}

#[test]
fn summarize_coverage_matches_python() {
    for case in vectors()["summarize_coverage"].as_array().unwrap() {
        let optional = |key: &str| (!case[key].is_null()).then(|| set(&case[key]));
        let (rejections, preconditions) =
            (optional("required_rejections"), optional("required_preconditions"));
        let actual = summarize_coverage(
            &set(&case["required"]),
            &maps(&case["results"]),
            &maps(&case["scenarios"]),
            rejections.as_ref(),
            preconditions.as_ref(),
            case["requires_handoff"].as_bool().unwrap(),
        );
        assert_eq!(J::Object(actual), case["expected"], "{}", case["name"]);
    }
}

#[test]
#[should_panic(expected = "one result per scenario")]
fn summarize_coverage_needs_one_result_per_scenario() {
    summarize_coverage(&BTreeSet::new(), &[], &[JsonMap::new()], None, None, false);
}

#[test]
fn assertions_match_python() {
    let recorded = &vectors()["assertions"];
    let observations = map(&recorded["observations"]);
    for case in recorded["cases"].as_array().unwrap() {
        let failures = assertions(case["expected"].as_array().unwrap(), &observations).unwrap();
        assert_eq!(J::Array(failures), case["failures"], "{}", case["name"]);
    }
    let unknown = assertions(&[json!({"subject": "unknown", "path": [], "equals": 1})], &observations);
    assert_eq!(unknown, Err(ScenarioError::raised(recorded["unknown_subject"].as_str().unwrap())));
}

#[test]
fn dice_match_python() {
    for case in vectors()["dice"].as_array().unwrap() {
        let dice = ScenarioDice::new(case["rolls"].as_array().unwrap());
        // The dice streams own the source they are given; the run keeps a clone to read back.
        let mut source: Box<dyn RollSource> = Box::new(dice.clone());
        let outcomes: Vec<J> = case["calls"]
            .as_array()
            .unwrap()
            .iter()
            .map(|call| {
                match source
                    .randint(i128::from(call[0].as_i64().unwrap()), i128::from(call[1].as_i64().unwrap()))
                {
                    Ok(value) => json!({"value": i64::try_from(value).unwrap()}),
                    Err(error) => json!({"raised": "ScenarioScriptError", "message": error.message}),
                }
            })
            .collect();
        assert_eq!(J::Array(outcomes), case["outcomes"], "{}", case["name"]);
        assert_eq!(dice.failure().map(J::String).unwrap_or(J::Null), case["failure"], "{}", case["name"]);
        let finished = match dice.assert_finished() {
            Ok(()) => J::Null,
            Err(ScenarioError::Script(message)) => {
                json!({"raised": "ScenarioScriptError", "message": message})
            }
            Err(other) => panic!("{other:?}"),
        };
        assert_eq!(finished, case["finished"], "{}", case["name"]);
    }
}

#[test]
fn failure_messages_match_python() {
    let recorded = &vectors()["failure_message"];
    for case in recorded["raised"].as_array().unwrap() {
        let class: &'static str = Box::leak(case["class"].as_str().unwrap().to_owned().into_boxed_str());
        let error = CodedError::new(case["code"].as_str().unwrap_or_default(), "private runtime detail")
            .with_error_type(class);
        assert_eq!(failure_message(&error.into()), case["message"], "{case}");
    }
    let script = &recorded["script"];
    let message = failure_message(&ScenarioError::Script(script["message"].as_str().unwrap().into()));
    assert_eq!(message, script["expected"]);
    // A refusal the port names no class for is a `CommandRejected`, as the owner reports it.
    let pending = CodedError::new("rust_port_pending", "not yet ported: x");
    assert_eq!(failure_message(&pending.into()), "CommandRejected (rust_port_pending)");
}

// ── The scripted provider ─────────────────────────────────────────────────────────────────

/// The generator's driver: discovers, emits evidence and streams requests, recording each.
struct Driver {
    ops: Vec<J>,
    provider: Arc<RulesetTestProvider>,
    outputs: Arc<Mutex<Vec<J>>>,
}

fn raised(message: &str) -> J {
    json!({"raised": "ScenarioScriptError", "message": message})
}

impl SessionProvider for Driver {
    fn run<'a>(
        &'a self,
        input: SessionProviderInput,
        events: &'a mut dyn SessionEvents,
    ) -> BoxFuture<'a, Result<(), SessionProviderError>> {
        Box::pin(async move {
            for op in &self.ops {
                let request = || {
                    let mut request = ProviderRequest::new(op["messages"].as_array().unwrap().clone());
                    request.tools = op["tools"].as_array().cloned();
                    request
                };
                let output = match op["op"].as_str().unwrap() {
                    "discover" => {
                        let catalog = input.current_catalog(op["id"].as_str().unwrap()).await?;
                        J::Array(catalog.into_iter().map(|tool| J::String(tool.name)).collect())
                    }
                    "evidence" => {
                        self.outputs.lock().unwrap().push("emitted".into());
                        let payload = map(&op["payload"]);
                        events
                            .emit(ProviderEvent::Evidence { message_id: "scenario-message".into(), payload })
                            .await?;
                        continue;
                    }
                    "stream" => {
                        let mut chunks = Vec::new();
                        let mut stream = self.provider.generate_stream(request());
                        let mut failed = None;
                        while let Some(item) = stream.next().await {
                            match item {
                                Ok(chunk) => chunks.push(json!({
                                    "content": chunk.content,
                                    "tool_call_delta": chunk.tool_call_delta,
                                    "finish_reason": match chunk.finish_reason {
                                        Some(PyValue::Text(reason)) => J::String(reason),
                                        None => J::Null,
                                        Some(other) => panic!("{other:?}"),
                                    },
                                })),
                                Err(error) => {
                                    failed = Some(raised(&error.message));
                                    break;
                                }
                            }
                        }
                        failed.unwrap_or(J::Array(chunks))
                    }
                    "generate" => match self.provider.generate(request()).await {
                        Ok(_) => panic!("generate answers"),
                        Err(error) => raised(&error.message),
                    },
                    "finish" => match self.provider.assert_finished() {
                        Ok(()) => "finished".into(),
                        Err(ScenarioError::Script(message)) => raised(&message),
                        Err(other) => panic!("{other:?}"),
                    },
                    other => panic!("unknown op {other}"),
                };
                self.outputs.lock().unwrap().push(output);
            }
            Ok(())
        })
    }
}

/// The session loop's side: every event is accepted.
struct Accepting;

impl SessionEvents for Accepting {
    fn emit(&mut self, _event: ProviderEvent) -> BoxFuture<'_, Result<(), SessionProviderError>> {
        Box::pin(async { Ok(()) })
    }
}

fn provider_input(catalog: &J) -> SessionProviderInput {
    let session = json!({
        "session_id": "scenario-session", "campaign_id": "scenario-campaign", "role": "dm",
        "provider_id": "ruleset-test", "model_id": "ruleset-test/scripted", "status": "active",
        "parent_session_id": null, "selected_skill": null, "scope": [], "reasoning_effort": null,
        "reasoning_effort_once": null,
    });
    SessionProviderInput::new(
        session_record(&session),
        session_context(&json!({"session": session, "messages": []})),
        catalog.as_array().unwrap().iter().map(common::tool).collect(),
        Arc::new(ProviderControl::new()),
    )
}

#[tokio::test]
async fn provider_matches_python() {
    for case in vectors()["provider"].as_array().unwrap() {
        let name = &case["name"];
        let owners: Arc<Mutex<VecDeque<String>>> =
            Arc::new(Mutex::new(set_list(&case["owners"]).into_iter().collect()));
        let provider = RulesetTestProvider::new(
            &maps(&case["replies"]),
            Arc::new(move || {
                let owner = owners.lock().unwrap().pop_front().expect("a scripted owner");
                Box::pin(async move { Ok(owner) })
            }),
        );
        let outputs = Arc::new(Mutex::new(Vec::new()));
        let driver = Driver {
            ops: case["ops"].as_array().unwrap().clone(),
            provider: Arc::clone(&provider),
            outputs: Arc::clone(&outputs),
        };
        let observed = provider.observe(Arc::new(driver));
        let terminal = match observed.run(provider_input(&case["catalog"]), &mut Accepting).await {
            Ok(()) => J::Null,
            Err(SessionProviderError { error: SessionFailure::Turn(TurnFailure::Coded(error)), .. }) => {
                json!({"raised": error.error_type, "message": error.message})
            }
            Err(other) => panic!("{name}: {other:?}"),
        };
        assert_eq!(J::Array(outputs.lock().unwrap().clone()), case["outputs"], "{name}");
        assert_eq!(terminal, case["terminal"], "{name}");
        assert_eq!(
            J::Array(provider.requests().into_iter().map(J::Object).collect()),
            case["requests"],
            "{name}"
        );
        assert_eq!(
            J::Array(provider.tool_results().into_iter().map(J::Object).collect()),
            case["tool_results"],
            "{name}"
        );
        assert_eq!(provider.position() as u64, case["position"].as_u64().unwrap(), "{name}");
        assert_eq!(provider.failure().map(J::String).unwrap_or(J::Null), case["failure"], "{name}");
    }
}

/// A JSON list of text, in order.
fn set_list(value: &J) -> Vec<String> {
    value.as_array().into_iter().flatten().map(|item| item.as_str().unwrap().to_owned()).collect()
}

// ── run_scenario ──────────────────────────────────────────────────────────────────────────

fn archive(key: &str) -> Vec<u8> {
    sylvia_base::pybase64::b64decode_validate(vectors()["archives"][key].as_str().unwrap()).unwrap()
}

#[tokio::test]
async fn run_scenario_matches_python_until_the_campaign() {
    for case in vectors()["run_scenario"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let scratch = Scratch::new(&format!("scenario-{name}"));
        let actual =
            run_scenario(&archive(case["archive"].as_str().unwrap()), &map(&case["scenario"]), &scratch.0)
                .await;
        let expected = if case["reached_campaign"].as_bool().unwrap() {
            json!({"id": case["result"]["id"], "status": "failed", "steps": [],
                   "failures": [{"message": "CommandRejected (rust_port_pending)"}]})
        } else {
            case["result"].clone()
        };
        assert_eq!(J::Object(actual), expected, "{name}");
    }
}

// ── run_ruleset_tests with this executable as the worker ──────────────────────────────────

/// The value after `flag` in this process's arguments.
fn argument(args: &[String], flag: &str) -> Option<String> {
    args.iter().position(|arg| arg == flag).and_then(|index| args.get(index + 1)).cloned()
}

/// The stand-in worker. An ordinary test run passes no `--result`, so it does nothing.
#[test]
fn stand_in_worker() {
    let args: Vec<String> = std::env::args().collect();
    let (Some(archive), Some(result)) = (argument(&args, "--archive"), argument(&args, "--result")) else {
        return;
    };
    if args.iter().any(|arg| arg == "--hang") {
        std::thread::sleep(std::time::Duration::from_secs(60));
    }
    if args.iter().any(|arg| arg == "--echo") {
        let digest = sylvia_base::hash::sha256_hex(std::fs::read(&archive).unwrap());
        std::fs::write(&result, format!("{{\"status\": \"passed\", \"archive_sha256\": \"{digest}\"}}"))
            .unwrap();
    }
    if args.iter().any(|arg| arg == "--where") {
        let folder = Path::new(&archive).parent().unwrap().to_str().unwrap().to_owned();
        std::fs::write(
            &result,
            serde_json::to_string(&json!({"status": "passed", "folder": folder})).unwrap(),
        )
        .unwrap();
    }
    if let Some(payload) = argument(&args, "--payload") {
        std::fs::copy(payload, &result).unwrap();
    }
    std::process::exit(argument(&args, "--exit").map_or(0, |code| code.parse().unwrap()));
}

/// This executable, running only [`stand_in_worker`], with `extra` after libtest's `--`.
fn stand_in(extra: Vec<OsString>) -> WorkerCommand {
    let mut args: Vec<OsString> = ["stand_in_worker", "--exact", "--nocapture", "--test-threads=1", "--"]
        .into_iter()
        .map(OsString::from)
        .collect();
    args.extend(extra);
    WorkerCommand { program: std::env::current_exe().unwrap(), args }
}

#[test]
fn run_ruleset_tests_matches_python() {
    let scratch = Scratch::new("worker");
    let plain = archive("plain");
    for case in vectors()["run_ruleset_tests"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let mut extra: Vec<OsString> = Vec::new();
        let payload = if let Some(text) = case["payload"].as_str() {
            Some(text.as_bytes().to_vec())
        } else if let Some(padded) = case["payload_padded"].as_array() {
            let width = usize::try_from(padded[1].as_u64().unwrap()).unwrap();
            // `str.ljust(width)`: the text, then spaces.
            let mut text = padded[0].as_str().unwrap().as_bytes().to_vec();
            text.resize(width, b' ');
            Some(text)
        } else {
            case["payload_base64"]
                .as_str()
                .map(|text| sylvia_base::pybase64::b64decode_validate(text).unwrap())
        };
        if let Some(payload) = payload {
            let path = scratch.0.join(format!("{name}.payload"));
            std::fs::write(&path, payload).unwrap();
            extra.extend(["--payload".into(), path.into_os_string()]);
        }
        if let Some(code) = case["exit"].as_i64() {
            extra.extend(["--exit".into(), code.to_string().into()]);
        }
        for flag in ["hang", "echo"] {
            if case[flag].as_bool() == Some(true) {
                extra.push(format!("--{flag}").into());
            }
        }
        let data = match case["archive_size"].as_u64() {
            Some(size) => vec![0u8; usize::try_from(size).unwrap()],
            None => plain.clone(),
        };
        let cancel = case["cancel"].as_bool() == Some(true);
        let cancelled = || true;
        let outcome = run_ruleset_tests_with(
            &stand_in(extra),
            &data,
            case["timeout"].as_f64().unwrap_or(60.0),
            cancel.then_some(&cancelled as &dyn Fn() -> bool),
        );
        let outcome = match outcome {
            Ok(result) => J::Object(result),
            Err(error) => json!({"raised": error.python_class(), "message": error.message()}),
        };
        assert_eq!(outcome, case["outcome"], "{name}");
    }
}

#[test]
fn run_ruleset_tests_removes_its_folder() {
    let result = run_ruleset_tests_with(&stand_in(vec!["--where".into()]), b"archive", 60.0, None).unwrap();
    assert_eq!(result["status"], "passed");
    let folder = PathBuf::from(result["folder"].as_str().unwrap());
    assert!(folder.file_name().unwrap().to_str().unwrap().starts_with("sylvia-ruleset-test-"), "{folder:?}");
    assert!(!folder.exists(), "{folder:?} was left behind");
}
