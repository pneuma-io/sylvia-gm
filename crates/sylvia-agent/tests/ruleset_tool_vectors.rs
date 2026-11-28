//! The Builder's Ruleset draft tools (B4b) against Python: `check_ruleset_draft` and
//! `test_ruleset_draft`, each case's handler called on the workspace Python's was.
//!
//! Regenerate with `tests/vectors/generate_ruleset_tool_vectors.py`. The test tool's worker is
//! this test executable: [`stand_in_worker`] follows the protocol of the generator's stand-in
//! script, and libtest leaves everything after `--` to it.

use serde_json::{json, Value as J};
use std::ffi::OsString;
use std::path::PathBuf;
use std::sync::{Arc, OnceLock};
use sylvia_agent::builder::ruleset_tools::{ruleset_draft_check_tool, ruleset_test_tool};
use sylvia_agent::builder::tools::ToolSpec;
use sylvia_app::ruleset_testing::WorkerCommand;
use sylvia_base::testing::assert_same_json;

fn vectors() -> &'static J {
    static VECTORS: OnceLock<J> = OnceLock::new();
    VECTORS.get_or_init(|| {
        serde_json::from_str(include_str!("vectors/ruleset_tool_vectors.json")).expect("the recorded vectors")
    })
}

/// A fresh workspace holding a case's files, removed when dropped.
struct Workspace(PathBuf);

impl Workspace {
    fn new(kind: &str, case: &J) -> Self {
        let path = std::env::temp_dir().join(format!(
            "sylvia-ruleset-tools-{}-{kind}-{}",
            std::process::id(),
            case["name"].as_str().unwrap()
        ));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).unwrap();
        for (relative, text) in case["files"].as_object().unwrap() {
            let file = path.join(relative);
            std::fs::create_dir_all(file.parent().unwrap()).unwrap();
            std::fs::write(file, text.as_str().unwrap()).unwrap();
        }
        Self(path)
    }
}

impl Drop for Workspace {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// The handler's result, or the exception it raised as Python's generator records it (the
/// class is not kept by a handler's error, so only the text is compared).
fn call(tool: &ToolSpec, params: &J) -> J {
    match (tool.handler)(params.as_object().unwrap()) {
        Ok(result) => result,
        Err(message) => json!({"message": message}),
    }
}

fn expected(case: &J) -> J {
    let result = &case["result"];
    match result.get("raised") {
        Some(_) => json!({"message": result["message"]}),
        None => result.clone(),
    }
}

#[test]
fn check_ruleset_draft_matches_python() {
    for case in vectors()["check_ruleset_draft"].as_array().unwrap() {
        let workspace = Workspace::new("check", case);
        let tool = ruleset_draft_check_tool(&workspace.0).unwrap();
        assert_same_json(&call(&tool, &case["params"]), &expected(case), format!("{}", case["name"]));
    }
}

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
fn test_ruleset_draft_matches_python() {
    let payloads = std::env::temp_dir().join(format!("sylvia-ruleset-tools-{}-payloads", std::process::id()));
    std::fs::create_dir_all(&payloads).unwrap();
    for case in vectors()["test_ruleset_draft"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let worker = &case["worker"];
        let mut extra: Vec<OsString> = Vec::new();
        if let Some(payload) = worker["payload"].as_str() {
            let path = payloads.join(format!("{name}.payload"));
            std::fs::write(&path, payload).unwrap();
            extra.extend(["--payload".into(), path.into_os_string()]);
        }
        if let Some(code) = worker["exit"].as_i64() {
            extra.extend(["--exit".into(), code.to_string().into()]);
        }
        for flag in ["hang", "echo"] {
            if worker[flag].as_bool() == Some(true) {
                extra.push(format!("--{flag}").into());
            }
        }
        let cancel = worker["cancel"].as_bool() == Some(true);
        let workspace = Workspace::new("test", case);
        let tool = ruleset_test_tool(&workspace.0, Arc::new(move || cancel), Some(stand_in(extra))).unwrap();
        assert_same_json(&call(&tool, &case["params"]), &expected(case), name);
    }
    let _ = std::fs::remove_dir_all(&payloads);
}
