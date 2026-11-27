//! Kept Claude Code processes (`claude_kept`), the native transcript (`claude_native`) and the
//! Claude settings action (`config::claude_settings`) against the frozen Python
//! (`vectors/generate_kept_vectors.py`).
//!
//! This test runs without libtest's harness because, for the settings cases, it is also the
//! stand-in for Sylvia's copy of Claude Code: copied into a runtime home as `claude`, it answers
//! `--version` and `auth status` from `fake-claude.json` in its `CLAUDE_CONFIG_DIR`, records each
//! `auth` action in `fake-calls.txt` there, waits to be stopped for `auth login`, and exits for
//! `auth logout`.

use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use sylvia_base::{Json, JsonMap};
use sylvia_provider::claude_install::InstallSystem;
use sylvia_provider::claude_kept::{
    self, ContinuationError, Delivery, KeptProcess, KeptProcesses, KeptState,
};
use sylvia_provider::claude_native;
use sylvia_provider::claude_runtime::{self, ClaudeConnection};
use sylvia_provider::config::claude_settings::{self, ClaudeSettingsError};
use sylvia_provider::config::env::Environment;
use sylvia_provider::config::protection::{self, Seal};
use sylvia_provider::config::provider_settings::ConfigSources;
use sylvia_provider::config::ConfigFailure;
use sylvia_provider::http::ScriptedTransport;

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/kept_vectors.json")).expect("kept vectors")
}

fn folder(name: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!("sylvia-kept-{}-{name}", std::process::id()));
    if let Err(error) = std::fs::remove_dir_all(&path) {
        assert_eq!(
            error.kind(),
            std::io::ErrorKind::NotFound,
            "cannot clear the old work folder {}: {error}",
            path.display()
        );
    }
    std::fs::create_dir_all(&path).unwrap();
    path
}

// ── The stand-in Claude Code ─────────────────────────────────────────────────────────────

fn fake_claude(arguments: &[String]) -> Option<i32> {
    let config = PathBuf::from(std::env::var_os("CLAUDE_CONFIG_DIR")?);
    let behaviour: Value =
        serde_json::from_slice(&std::fs::read(config.join("fake-claude.json")).ok()?).ok()?;
    if arguments.get(1).map(String::as_str) == Some("--version") {
        print!("{}", behaviour["version"].as_str().unwrap());
        return Some(0);
    }
    let auth = arguments.iter().position(|argument| argument == "auth")?;
    let action = arguments.last().unwrap().clone();
    let mut calls =
        std::fs::OpenOptions::new().create(true).append(true).open(config.join("fake-calls.txt")).unwrap();
    std::io::Write::write_all(&mut calls, format!("{action}\n").as_bytes()).unwrap();
    drop(calls);
    match arguments[auth + 1].as_str() {
        "status" => {
            print!("{}", behaviour["status"].as_str().unwrap());
            Some(behaviour["status_code"].as_i64().unwrap() as i32)
        }
        "login" => loop {
            std::thread::sleep(Duration::from_secs(60));
        },
        _ => Some(0),
    }
}

// ── Continuations and pools ──────────────────────────────────────────────────────────────

fn state_of(value: &Value) -> KeptState {
    KeptState {
        held: value["held"].as_array().unwrap().clone(),
        spoken: value["spoken"].as_str().unwrap().to_owned(),
        pending: value["pending"].as_object().cloned(),
        call_id: value["call_id"].as_str().unwrap().to_owned(),
        call_arguments: value["call_arguments"].clone(),
        model_usage: JsonMap::new(),
    }
}

fn continuations() {
    let vectors = vectors();
    let continue_text = vectors["continue_text"].as_str().unwrap();
    for case in vectors["continuations"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let ours = claude_kept::continuation(
            &state_of(&case["state"]),
            case["history"].as_array().unwrap(),
            continue_text,
        );
        match (ours, case.get("delivery"), case.get("mismatch"), case.get("raw")) {
            (Ok(Delivery::Prompt(content)), Some(expected), None, None) => {
                sylvia_base::testing::assert_same_json(
                    &json!(["prompt", content]),
                    &*expected,
                    format!("{name}"),
                )
            }
            (Ok(Delivery::Answer(answer)), Some(expected), None, None) => {
                sylvia_base::testing::assert_same_json(
                    &json!(["answer", Json::Object(answer)]),
                    &*expected,
                    format!("{name}"),
                )
            }
            (Err(ContinuationError::Mismatch(mismatch)), None, Some(reason), None) => {
                assert_eq!(mismatch.reason, reason.as_str().unwrap(), "{name}")
            }
            (Err(ContinuationError::Raw(message)), None, None, Some(raw)) => {
                assert_eq!(message, raw[1].as_str().unwrap(), "{name}")
            }
            (ours, ..) => panic!("{name}: Rust {ours:?}, Python {case}"),
        }
    }
    for case in vectors["extends"].as_array().unwrap() {
        let ours =
            claude_kept::extends(case["history"].as_array().unwrap(), case["held"].as_array().unwrap());
        assert_eq!(ours, case["extends"].as_bool().unwrap(), "{case}");
    }
    for (value, expected) in vectors["process_settings"].as_object().unwrap() {
        assert_eq!(claude_kept::process_setting(Some(value)), expected.as_str().unwrap(), "{value:?}");
    }
    assert_eq!(claude_kept::process_setting(None), "persistent");
}

fn pools() {
    for script in vectors()["pools"].as_array().unwrap() {
        let name = script["name"].as_str().unwrap();
        let pool =
            KeptProcesses::new(script["limit"].as_u64().unwrap() as usize, script["idle"].as_f64().unwrap());
        let mut named: Vec<(String, Arc<KeptProcess>)> = Vec::new();
        let name_of = |named: &Vec<(String, Arc<KeptProcess>)>, kept: &Arc<KeptProcess>| {
            named.iter().find(|(_, each)| Arc::ptr_eq(each, kept)).map(|(name, _)| name.clone()).unwrap()
        };
        let names = |named: &Vec<(String, Arc<KeptProcess>)>, list: &[Arc<KeptProcess>]| {
            let mut out: Vec<String> = list.iter().map(|kept| name_of(named, kept)).collect();
            out.sort();
            json!(out)
        };
        let mut results = Vec::new();
        for op in script["ops"].as_array().unwrap() {
            let text = |index: usize| op[index].as_str().unwrap();
            match text(0) {
                "keep" => {
                    let connection =
                        ClaudeConnection::new(Path::new("claude"), Path::new("."), Vec::new(), Vec::new());
                    let state = KeptState { held: op[6].as_array().unwrap().clone(), ..KeptState::default() };
                    let kept = KeptProcess::new(
                        connection,
                        Path::new(text(2)),
                        text(3),
                        text(4),
                        text(5),
                        "s",
                        Path::new("step"),
                        Path::new("cfg"),
                        None,
                        state,
                    );
                    named.push((text(1).to_owned(), kept.clone()));
                    let evicted = pool.keep(kept, op[7].as_u64().unwrap());
                    results.push(names(&named, &evicted));
                }
                "take" => {
                    let (chosen, close) =
                        pool.take(Path::new(text(1)), text(2), text(3), text(4), op[5].as_array().unwrap());
                    results.push(json!([chosen.map(|kept| name_of(&named, &kept)), names(&named, &close)]));
                }
                "release" => {
                    let released = pool.release(op[1].as_str().map(Path::new), op[2].as_str());
                    results.push(names(&named, &released));
                }
                "generation" => results.push(json!(pool.generation(Path::new(text(1))))),
                "models" => results.push(json!(pool.models().into_iter().collect::<Vec<_>>())),
                "len" => results.push(json!(pool.len())),
                other => panic!("unknown operation {other}"),
            }
        }
        pool.close_all();
        sylvia_base::testing::assert_same_json(
            &Value::Array(results),
            &script["results"],
            format!("pool {name}"),
        );
    }
}

/// A kept process left idle past the pool's limit closes on its own, once, with its callbacks.
fn expiry() {
    let pool = KeptProcesses::new(4, 0.2);
    let connection = ClaudeConnection::new(Path::new("claude"), Path::new("."), Vec::new(), Vec::new());
    let kept = KeptProcess::new(
        connection,
        Path::new("home"),
        "m",
        "s",
        "t",
        "s",
        Path::new("step"),
        Path::new("cfg"),
        None,
        KeptState::default(),
    );
    let closed = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let counter = closed.clone();
    kept.on_close(Box::new(move |_| {
        counter.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    }));
    assert!(pool.keep(kept.clone(), 0).is_empty());
    assert_eq!(pool.len(), 1);
    // The reaper runs on its own thread; under load (other checks on the same machine) it can be
    // scheduled late, so wait for it with a generous deadline instead of a fixed sleep. As in
    // Python's `KeptProcess.close`, the closed flag is set first and the callbacks run after it,
    // on the reaper's thread, so wait for the callback too.
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    let reaped =
        || pool.is_empty() && kept.is_closed() && closed.load(std::sync::atomic::Ordering::SeqCst) == 1;
    while !reaped() && std::time::Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(20));
    }
    assert_eq!(pool.len(), 0, "the idle process was not reaped within 10 s");
    assert!(kept.is_closed());
    assert_eq!(closed.load(std::sync::atomic::Ordering::SeqCst), 1, "its callback did not run within 10 s");
    kept.close();
    assert_eq!(closed.load(std::sync::atomic::Ordering::SeqCst), 1);
    // A process taken back before its time does not expire.
    let connection = ClaudeConnection::new(Path::new("claude"), Path::new("."), Vec::new(), Vec::new());
    let held = vec![json!({"role": "user", "content": "a"})];
    let kept = KeptProcess::new(
        connection,
        Path::new("home"),
        "m",
        "s",
        "t",
        "s",
        Path::new("step"),
        Path::new("cfg"),
        None,
        KeptState { held: held.clone(), ..KeptState::default() },
    );
    pool.keep(kept.clone(), 0);
    let mut longer = held.clone();
    longer.push(json!({"role": "assistant", "content": "b"}));
    let (chosen, _) = pool.take(Path::new("home"), "m", "s", "t", &longer);
    assert!(chosen.is_some_and(|chosen| Arc::ptr_eq(&chosen, &kept)));
    std::thread::sleep(Duration::from_millis(500));
    assert!(!kept.is_closed());
}

// ── The native transcript ────────────────────────────────────────────────────────────────

fn native() {
    let vectors = vectors();
    let prompt = &vectors["prompt"];
    let session = vectors["session_id"].as_str().unwrap();
    for case in vectors["native"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let root = folder(&format!("native-{name}"));
        let config = root.join("claude-runtime").join("profile");
        let project = config.join("projects").join("-workspace");
        std::fs::create_dir_all(&project).unwrap();
        let file = project.join(format!("{session}.jsonl"));
        if let Some(rows) = case["rows"].as_str() {
            std::fs::write(&file, rows).unwrap();
        }
        let requests = case["requests"].as_u64().map(|count| count as usize);
        let final_id = case["final"].as_str();
        let found = match case["op"].as_str().unwrap() {
            "collect" => {
                claude_native::collect(case["rows"].is_string().then_some(file.as_path()), prompt, final_id)
            }
            "answer" => claude_native::collect_answer(&config, session, "toolu_1", 0.1, final_id, requests),
            _ => {
                let flushed = claude_native::wait_for_flush(&config, session, prompt, 0.5, requests);
                assert_eq!(flushed, case["flushed"].as_bool().unwrap(), "{name}");
                continue;
            }
        };
        assert_eq!(found.status, case["status"].as_str().unwrap(), "{name}");
        sylvia_base::testing::assert_same_json(
            &Value::Array(found.records),
            &case["records"],
            format!("{name}"),
        );
        assert_eq!(found.reason, case["reason"].as_str(), "{name}");
        sylvia_base::testing::assert_same_json(
            &found.answer.unwrap_or(Value::Null),
            &case["answer"],
            format!("{name}"),
        );
        let _ = std::fs::remove_dir_all(&root);
    }
}

fn files_under(root: &Path) -> Vec<String> {
    let mut out = Vec::new();
    let mut pending = vec![root.to_owned()];
    while let Some(folder) = pending.pop() {
        for entry in std::fs::read_dir(&folder).into_iter().flatten().flatten() {
            let path = entry.path();
            if path.is_dir() {
                pending.push(path);
            } else {
                let parts: Vec<String> = path
                    .strip_prefix(root)
                    .unwrap()
                    .components()
                    .map(|part| part.as_os_str().to_string_lossy().into_owned())
                    .collect();
                out.push(parts.join("/"));
            }
        }
    }
    out.sort();
    out
}

fn cleanup() {
    let case = &vectors()["cleanup"];
    let root = folder("cleanup");
    let home = root.join("claude-runtime");
    let config = home.join("profile");
    for (relative, _) in case["layout"].as_object().unwrap() {
        let target = home.join(relative);
        std::fs::create_dir_all(target.parent().unwrap()).unwrap();
        std::fs::write(&target, "x").unwrap();
    }
    for (relative, mtime) in case["layout"].as_object().unwrap() {
        let when = UNIX_EPOCH + Duration::from_secs_f64(mtime.as_f64().unwrap());
        let mut target = home.join(relative);
        while target != home {
            claude_kept::set_times(&target, when).unwrap();
            target = target.parent().unwrap().to_owned();
        }
    }
    let session = "0f8fad5b-d9cb-469f-a165-70867728950e";
    let mut listing: Vec<String> = claude_native::session_files(&config, session)
        .iter()
        .map(|path| {
            path.strip_prefix(&config)
                .unwrap()
                .components()
                .map(|part| part.as_os_str().to_string_lossy().into_owned())
                .collect::<Vec<_>>()
                .join("/")
        })
        .collect();
    listing.sort();
    sylvia_base::testing::assert_same_json(
        &json!(listing),
        &case["session_files"],
        concat!(file!(), ":", line!()),
    );
    let active: BTreeSet<String> = ["step-active", "22222222-2222-3333-4444-555555555555"]
        .iter()
        .map(|name| (*name).to_owned())
        .collect();
    let swept = claude_native::sweep(&config, &home.join("steps"), &active, &home, case["now"].as_f64());
    sylvia_base::testing::assert_same_json(&json!(swept), &case["swept"], concat!(file!(), ":", line!()));
    sylvia_base::testing::assert_same_json(
        &json!(files_under(&home)),
        &case["after_sweep"],
        concat!(file!(), ":", line!()),
    );
    sylvia_base::testing::assert_same_json(
        &json!(claude_native::remove_debug_logs(Some(&home.join("cache")), &home)),
        &case["debug_logs"],
        concat!(file!(), ":", line!()),
    );
    sylvia_base::testing::assert_same_json(
        &json!(claude_native::delete_session(&root.join("elsewhere"), session, &home)),
        &case["outside"],
        concat!(file!(), ":", line!()),
    );
    sylvia_base::testing::assert_same_json(
        &json!(claude_native::delete_session(&config, "22222222-2222-3333-4444-555555555555", &home)),
        &case["deleted"],
        concat!(file!(), ":", line!()),
    );
    sylvia_base::testing::assert_same_json(
        &json!(files_under(&home)),
        &case["after"],
        concat!(file!(), ":", line!()),
    );
    let _ = std::fs::remove_dir_all(&root);
    let _ = SystemTime::now();
}

// ── Settings ─────────────────────────────────────────────────────────────────────────────

struct FakeSeal;

impl Seal for FakeSeal {
    fn seal(&self, value: &[u8], decrypt: bool) -> Result<Vec<u8>, ConfigFailure> {
        if decrypt {
            let Some(rest) = value.strip_prefix(b"FAKE") else {
                return Err(ConfigFailure::Config(protection::UNAVAILABLE_MESSAGE.into()));
            };
            Ok(rest.iter().rev().copied().collect())
        } else {
            let mut out = b"FAKE".to_vec();
            out.extend(value.iter().rev());
            Ok(out)
        }
    }
}

/// The stand-in's answers for a case's runtime: what `runtime_version` and
/// `authentication_status` reported in Python.
fn behaviour(runtime: &Value) -> Value {
    if runtime == "damaged" {
        return json!({"version": "garbage\n", "status": "{}", "status_code": 0});
    }
    let signed_in = runtime["signed_in"].as_bool().unwrap();
    let status = json!({"loggedIn": signed_in, "authMethod": runtime["auth_method"]});
    json!({"version": "2.1.400 (Claude Code)\n", "status": status.to_string(), "status_code": if signed_in { 0 } else { 1 }})
}

fn settings() {
    let vectors = vectors();
    let now = vectors["now"].as_str().unwrap().to_owned();
    let env = Environment::default();
    let default_config = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../config/config.yaml");
    let me = std::env::current_exe().unwrap();
    for case in vectors["settings"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let root = folder(&format!("settings-{name}")).join("owner");
        std::fs::create_dir_all(&root).unwrap();
        let home = sylvia_provider::codex_rpc::python_resolve(&root).join("claude-runtime");
        if !case["runtime"].is_null() {
            let install = home.join("install").join("2.1.400");
            std::fs::create_dir_all(&install).unwrap();
            std::fs::copy(&me, install.join(claude_runtime::executable_name())).unwrap();
            std::fs::create_dir_all(home.join("profile")).unwrap();
            std::fs::write(
                home.join("profile").join("fake-claude.json"),
                behaviour(&case["runtime"]).to_string(),
            )
            .unwrap();
        }
        let system = || {
            InstallSystem::new(
                Arc::new(ScriptedTransport::new(Vec::new())),
                Arc::new(|_: &Path| Ok(u64::MAX)),
            )
        };
        let mut results = Vec::new();
        for call in case["calls"].as_array().unwrap() {
            let sources = ConfigSources { default_config: &default_config, seal: &FakeSeal, env: &env };
            let outcome =
                claude_settings::claude_settings_action(sources, &root, &call[0], &call[1], &system, &|| {
                    now.clone()
                });
            results.push(match outcome {
                Ok(mut status) => {
                    if status.get("executable").is_some_and(|path| !path.is_null()) {
                        status.insert("executable".into(), json!("<executable>"));
                    }
                    json!({"status": Json::Object(status)})
                }
                Err(ClaudeSettingsError::Connection(message)) => json!({"error": message}),
                Err(other) => panic!("{name}: {other:?}"),
            });
            std::thread::sleep(Duration::from_millis(50));
        }
        claude_settings::cancel_all();
        sylvia_base::testing::assert_same_json(
            &Value::Array(results),
            &case["results"],
            format!("settings {name}"),
        );
        let calls = std::fs::read_to_string(home.join("profile").join("fake-calls.txt")).unwrap_or_default();
        let ours: Vec<&str> = calls.lines().filter(|line| *line != "status").collect();
        let theirs: Vec<&str> = case["launched"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|entry| entry[0] != "release")
            .map(|entry| entry[1].as_str().unwrap())
            .collect();
        assert_eq!(ours, theirs, "child processes for {name}");
        let config = std::fs::read_to_string(root.join("config.yaml")).ok();
        sylvia_base::testing::assert_same_json(&json!(config), &case["config"], format!("config for {name}"));
        let _ = std::fs::remove_dir_all(root.parent().unwrap());
    }
}

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if let Some(code) = fake_claude(&arguments) {
        std::process::exit(code);
    }
    let tests: Vec<(&str, fn())> = vec![
        ("continuations", continuations),
        ("pools", pools),
        ("expiry", expiry),
        ("native", native),
        ("cleanup", cleanup),
        ("settings", settings),
    ];
    let filter = arguments.iter().skip(1).find(|argument| !argument.starts_with('-')).cloned();
    let mut failed = Vec::new();
    for (name, test) in tests {
        if filter.as_deref().is_some_and(|filter| !name.contains(filter)) {
            continue;
        }
        let outcome = std::panic::catch_unwind(test);
        println!("test {name} ... {}", if outcome.is_ok() { "ok" } else { "FAILED" });
        if outcome.is_err() {
            failed.push(name);
        }
    }
    if !failed.is_empty() {
        println!("test result: FAILED. {} failed: {}", failed.len(), failed.join(", "));
        std::process::exit(1);
    }
    println!("test result: ok.");
}
