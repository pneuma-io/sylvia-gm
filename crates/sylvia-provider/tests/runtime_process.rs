//! The account runtimes' process layer (`claude_runtime`, `codex_rpc`, `runtime_process`)
//! against the frozen Python (`vectors/generate_runtime_vectors.py`), and the process
//! behaviour Python relies on: what reaches the child, how its output is read, and how it stops.
//!
//! This test runs without libtest's harness because it is also its own stand-in runtime: started
//! with `--fake-runtime` or `app-server` it follows the script `fake-script.json` in its working
//! folder (writing bytes, recording what it reads into `fake-record.bin`, hanging, exiting), and
//! it answers `--version` and `auth status` as Claude Code does. On Linux, `--fake-parent` starts
//! a Claude connection to a hanging stand-in and waits to be killed.

use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::ffi::OsString;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use sylvia_base::Json;
use sylvia_provider::claude_runtime::{self, ClaudeConnection, Platform, RuntimeEvent};
use sylvia_provider::codex_rpc::{self, CodexConnection, CodexError};
use sylvia_provider::runtime_process::{self, Captured, RunError, LINE_SEPARATOR};

const SCRIPT: &str = "fake-script.json";
const RECORD: &str = "fake-record.bin";

// ── The stand-in runtime ─────────────────────────────────────────────────────────────────

fn latin1(text: &str) -> Vec<u8> {
    text.chars().map(|c| c as u32 as u8).collect()
}

fn record(bytes: &[u8]) {
    let mut file = std::fs::OpenOptions::new().create(true).append(true).open(RECORD).expect("record");
    file.write_all(bytes).expect("record write");
    file.flush().expect("record flush");
}

fn fake_runtime() {
    let script: Value = serde_json::from_slice(&std::fs::read(SCRIPT).expect("script")).expect("script JSON");
    let mut stdout = std::io::stdout().lock();
    let mut stdin = std::io::stdin().lock();
    for step in script.as_array().expect("steps") {
        if let Some(text) = step.get("write").and_then(Value::as_str) {
            stdout.write_all(&latin1(text)).expect("write");
            stdout.flush().expect("flush");
        } else if step.get("env").is_some() {
            let mut environment: Vec<(String, String)> = std::env::vars().collect();
            environment.sort();
            writeln!(stdout, "{}", json!({"type": "env", "env": environment})).expect("env");
            stdout.flush().expect("flush");
        } else if let Some(count) = step.get("read").and_then(Value::as_u64) {
            for _ in 0..count {
                let mut line = Vec::new();
                let mut byte = [0u8; 1];
                while stdin.read(&mut byte).unwrap_or(0) == 1 {
                    line.push(byte[0]);
                    if byte[0] == b'\n' {
                        break;
                    }
                }
                record(&line);
            }
        } else if step.get("copy").is_some() {
            let mut buffer = [0u8; 4096];
            loop {
                match stdin.read(&mut buffer) {
                    Ok(0) | Err(_) => break,
                    Ok(read) => record(&buffer[..read]),
                }
            }
        } else if step.get("hang").is_some() {
            loop {
                std::thread::sleep(Duration::from_secs(60));
            }
        } else if let Some(code) = step.get("exit").and_then(Value::as_i64) {
            std::process::exit(code as i32);
        }
    }
}

fn fake_claude_cli(arguments: &[String]) -> Option<i32> {
    if arguments.get(1).map(String::as_str) == Some("--version") {
        println!("2.1.400 (Claude Code)");
        return Some(0);
    }
    if arguments.iter().any(|argument| argument == "--safe-mode")
        && arguments.last().map(String::as_str) == Some("status")
    {
        println!("{{\"loggedIn\": false, \"authMethod\": \"claude.ai\", \"email\": \"private\"}}");
        return Some(1);
    }
    None
}

// ── Helpers ──────────────────────────────────────────────────────────────────────────────

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/runtime_vectors.json")).expect("runtime vectors")
}

fn me() -> PathBuf {
    std::env::current_exe().expect("this test's path")
}

fn folder(name: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!("sylvia-runtime-{}-{name}", std::process::id()));
    if let Err(error) = std::fs::remove_dir_all(&path) {
        assert_eq!(
            error.kind(),
            std::io::ErrorKind::NotFound,
            "cannot clear the old work folder {}: {error}",
            path.display()
        );
    }
    std::fs::create_dir_all(&path).expect("folder");
    path
}

fn write_script(workspace: &Path, script: Value) {
    std::fs::write(workspace.join(SCRIPT), serde_json::to_vec(&script).unwrap()).expect("script");
}

fn base_environment() -> Vec<(OsString, OsString)> {
    ["SYSTEMROOT", "SystemRoot", "PATH"]
        .iter()
        .filter_map(|name| std::env::var_os(name).map(|value| (OsString::from(*name), value)))
        .collect()
}

fn claude(workspace: &Path, script: Value) -> ClaudeConnection {
    write_script(workspace, script);
    ClaudeConnection::new(&me(), workspace, vec!["--fake-runtime".into()], base_environment())
}

fn pairs(value: &Value) -> Vec<(OsString, OsString)> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|pair| (OsString::from(pair[0].as_str().unwrap()), OsString::from(pair[1].as_str().unwrap())))
        .collect()
}

fn wait_until(limit: Duration, mut done: impl FnMut() -> bool) -> bool {
    let started = Instant::now();
    while started.elapsed() < limit {
        if done() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    done()
}

fn outcome_of(outcome: &Value) -> Result<Captured, RunError> {
    match outcome.as_str() {
        Some("oserror") => Err(RunError::Start(std::io::Error::other("no such file"))),
        Some("timeout") => Err(RunError::Timeout),
        _ => {
            let code = outcome[1].as_i64().unwrap() as i32;
            // A negative return code is a signal, which Rust reports as no code.
            Ok(Captured {
                code: (code >= 0).then_some(code),
                stdout: outcome[0].as_str().unwrap().to_owned(),
            })
        }
    }
}

fn class_of(error: &sylvia_provider::ProviderError) -> &'static str {
    match error.kind.class_name() {
        "Exception" => "TypeError",
        other => other,
    }
}

// ── Pure parts ───────────────────────────────────────────────────────────────────────────

fn context_windows() {
    for case in vectors()["context_windows"].as_array().unwrap() {
        assert_eq!(
            claude_runtime::documented_context_window(&case["model"]),
            case["window"].as_i64(),
            "{case}"
        );
    }
}

fn environments() {
    let vectors = vectors();
    for case in vectors["native_environments"].as_array().unwrap() {
        let platform =
            Platform { linux: case["linux"].as_bool().unwrap(), windows: case["windows"].as_bool().unwrap() };
        let ours = claude_runtime::native_environment_for(
            &pairs(&case["environ"]),
            OsString::from(case["home"].as_str().unwrap()).as_os_str(),
            case["separator"].as_str().unwrap(),
            platform,
        );
        assert_eq!(ours, pairs(&case["environment"]), "native environment {}", case["name"]);
    }
    for case in vectors["isolated_environments"].as_array().unwrap() {
        let ours = codex_rpc::isolated_environment_for(
            &pairs(&case["environ"]),
            OsString::from(case["home"].as_str().unwrap()).as_os_str(),
            OsString::from(case["drive"].as_str().unwrap()).as_os_str(),
            case["separator"].as_str().unwrap(),
        );
        assert_eq!(ours, pairs(&case["environment"]), "isolated environment {}", case["name"]);
    }
    if cfg!(windows) {
        assert_eq!(codex_rpc::path_drive(Path::new(r"C:\Users\dev")), "C:");
        assert_eq!(codex_rpc::path_drive(Path::new(r"\\server\share\x")), r"\\server\share");
    }
}

fn auth_commands() {
    for case in vectors()["auth_commands"].as_array().unwrap() {
        let ours = claude_runtime::native_auth_command(
            Path::new(case["executable"].as_str().unwrap()),
            case["action"].as_str().unwrap(),
        );
        let theirs: Vec<OsString> =
            case["command"].as_array().unwrap().iter().map(|part| part.as_str().unwrap().into()).collect();
        assert_eq!(ours, theirs, "{case}");
    }
}

fn versions_and_statuses() {
    let vectors = vectors();
    for case in vectors["versions"].as_array().unwrap() {
        let ours = claude_runtime::parse_runtime_version(outcome_of(&case["outcome"]));
        match (ours, case.get("value"), case.get("error")) {
            (Ok(version), Some(value), None) => assert_eq!(version, value.as_str().unwrap(), "{case}"),
            (Err(error), None, Some(expected)) => {
                assert_eq!(class_of(&error), expected[0].as_str().unwrap(), "{case}");
                assert_eq!(error.message, expected[1].as_str().unwrap(), "{case}");
            }
            (ours, ..) => panic!("{case}: Rust {ours:?}"),
        }
    }
    for case in vectors["auth_statuses"].as_array().unwrap() {
        let ours = claude_runtime::parse_authentication_status(outcome_of(&case["outcome"]));
        match (ours, case.get("value"), case.get("error")) {
            (Ok(status), Some(value), None) => assert_eq!(Json::Object(status), *value, "{case}"),
            (Err(error), None, Some(expected)) => {
                assert_eq!(class_of(&error), expected[0].as_str().unwrap(), "{case}");
                assert_eq!(error.message, expected[1].as_str().unwrap(), "{case}");
            }
            (ours, ..) => panic!("{case}: Rust {ours:?}"),
        }
    }
    assert_eq!(claude_runtime::MINIMUM_VERSION_TEXT, vectors["minimum_version"].as_str().unwrap());
    assert_eq!(claude_runtime::MAX_FRAME_BYTES as u64, vectors["max_frame_bytes"].as_u64().unwrap());
    let missing = &vectors["missing_runtime"];
    assert_eq!(codex_rpc::missing_runtime_message(true), missing["windows"].as_str().unwrap());
    assert_eq!(codex_rpc::missing_runtime_message(false), missing["other"].as_str().unwrap());
}

fn runtimes() {
    for (index, case) in vectors()["runtimes"].as_array().unwrap().iter().enumerate() {
        let home = folder(&format!("runtimes-{index}"));
        for entry in case["entries"].as_array().unwrap() {
            let directory = home.join("install").join(entry[0].as_str().unwrap());
            std::fs::create_dir_all(&directory).unwrap();
            let target = directory.join(claude_runtime::executable_name());
            match entry[1].as_str().unwrap() {
                "file" => std::fs::write(&target, b"").unwrap(),
                "dir" => std::fs::create_dir(&target).unwrap(),
                _ => {}
            }
        }
        let found = claude_runtime::find_runtime(&home);
        let chosen = found
            .as_ref()
            .map(|path| path.parent().unwrap().file_name().unwrap().to_string_lossy().into_owned());
        assert_eq!(chosen.as_deref(), case["chosen"].as_str(), "{case}");
        if let Some(found) = found {
            assert!(found.is_absolute(), "{}", found.display());
        }
        let _ = std::fs::remove_dir_all(&home);
    }
}

fn which_and_codex_lookup() {
    let directory = folder("which");
    let name = if cfg!(windows) { "codex.EXE" } else { "codex" };
    let program = directory.join(name);
    std::fs::write(&program, b"").unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let other = folder("which-plain");
        std::fs::write(other.join("codex"), b"").unwrap();
        // Not executable: skipped, as `os.access(X_OK)` skips it.
        let path = std::env::join_paths([other.clone(), directory.clone()]).unwrap();
        assert_eq!(codex_rpc::which("codex", Some(&path), None), None);
        std::fs::set_permissions(&program, std::fs::Permissions::from_mode(0o755)).unwrap();
        assert_eq!(codex_rpc::which("codex", Some(&path), None), Some(program.clone()));
        let _ = std::fs::remove_dir_all(&other);
    }
    #[cfg(windows)]
    {
        let path = OsString::from(directory.as_os_str());
        assert_eq!(
            codex_rpc::which("codex", Some(&path), Some(".COM;.EXE")),
            Some(directory.join("codex.EXE"))
        );
        assert_eq!(codex_rpc::which("codex", Some(&path), Some(".BAT")), None);
    }
    assert_eq!(codex_rpc::which("codex", Some(OsString::new().as_os_str()), None), None);
    let _ = std::fs::remove_dir_all(&directory);
}

// ── Through a real child ─────────────────────────────────────────────────────────────────

fn events_until_exit(connection: &ClaudeConnection) -> Vec<Value> {
    let mut events = Vec::new();
    loop {
        match connection.next_event(Duration::from_secs(20)) {
            Ok(event) => events.push(json!({"event": Json::Object(event)})),
            Err(error) if error.kind.class_name() == "ProviderConnectionError" => {
                events.push(json!({"exit": true}));
                return events;
            }
            Err(error) => events.push(json!({"error": [error.kind.class_name(), error.message]})),
        }
    }
}

fn claude_frames() {
    for case in vectors()["claude_frames"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let workspace = folder(&format!("frames-{name}"));
        let connection = claude(&workspace, json!([{"write": case["data"]}, {"exit": 0}]));
        connection.start().expect("starts");
        let events = events_until_exit(&connection);
        assert_eq!(Value::Array(events), case["events"], "frames {name}");
        connection.close();
        let _ = std::fs::remove_dir_all(&workspace);
    }
}

fn claude_writes() {
    for (index, case) in vectors()["claude_writes"].as_array().unwrap().iter().enumerate() {
        let workspace = folder(&format!("writes-{index}"));
        let connection = claude(&workspace, json!([{"read": 1}, {"hang": true}]));
        connection.start().expect("starts");
        connection.write(&case["message"]).expect("writes");
        let expected = case["text"].as_str().unwrap().replace('\n', LINE_SEPARATOR);
        assert!(
            wait_until(Duration::from_secs(20), || std::fs::read(workspace.join(RECORD))
                .is_ok_and(|bytes| bytes.len() >= expected.len())),
            "the stand-in read the frame"
        );
        assert_eq!(
            String::from_utf8(std::fs::read(workspace.join(RECORD)).unwrap()).unwrap(),
            expected,
            "{case}"
        );
        connection.close();
        let _ = std::fs::remove_dir_all(&workspace);
    }
}

fn claude_process_behaviour() {
    // Only the environment given reaches the child.
    let workspace = folder("claude-env");
    let mut environment = base_environment();
    environment.push(("SYLVIA_TEST_ONLY".into(), "yes".into()));
    write_script(&workspace, json!([{"env": true}, {"hang": true}]));
    let connection =
        ClaudeConnection::new(&me(), &workspace, vec!["--fake-runtime".into()], environment.clone());
    connection.start().expect("starts");
    let event = connection.next_event(Duration::from_secs(20)).expect("the environment event");
    let seen: BTreeSet<String> =
        event["env"].as_array().unwrap().iter().map(|pair| pair[0].as_str().unwrap().to_owned()).collect();
    let given: BTreeSet<String> =
        environment.iter().map(|(name, _)| name.to_string_lossy().into_owned()).collect();
    if cfg!(windows) {
        let lower =
            |names: &BTreeSet<String>| names.iter().map(|name| name.to_lowercase()).collect::<BTreeSet<_>>();
        assert_eq!(lower(&seen), lower(&given));
    } else {
        assert_eq!(seen, given);
    }
    // Inactivity is a timeout; close stops the child; a write afterwards is refused.
    let quiet = connection.next_event(Duration::from_millis(200)).unwrap_err();
    assert_eq!(
        (quiet.kind.class_name(), quiet.message.as_str()),
        ("ProviderTimeoutError", "Claude stopped responding. Saved work is kept.")
    );
    let pid = connection.process_id().expect("a running child");
    connection.close();
    assert!(wait_until(Duration::from_secs(10), || !process_alive(pid)), "close stopped the child");
    let refused = connection.write(&json!({"type": "user"})).unwrap_err();
    assert_eq!(refused.message, "The Claude runtime has stopped.");
    assert_eq!(connection.drain(), vec![RuntimeEvent::Exited]);
    let _ = std::fs::remove_dir_all(&workspace);

    // `--version` and `auth status` through captured runs.
    let home = folder("claude-cli");
    assert_eq!(claude_runtime::runtime_version(&me(), &home).unwrap(), "2.1.400");
    let status = claude_runtime::authentication_status(&me(), &home).unwrap();
    assert_eq!(
        Json::Object(status),
        json!({"signed_in": false, "auth_method": "claude.ai", "billing": "subscription"})
    );
    let missing = claude_runtime::runtime_version(&home.join("missing"), &home).unwrap_err();
    assert_eq!(missing.message, "The Claude executable could not report its version.");
    let _ = std::fs::remove_dir_all(&home);
}

fn oversized_frame() {
    let workspace = folder("oversized");
    let line =
        format!("{{\"type\": \"big\", \"text\": \"{}\"}}\n", "x".repeat(claude_runtime::MAX_FRAME_BYTES));
    let connection = claude(&workspace, json!([{"write": line}, {"exit": 0}]));
    connection.start().expect("starts");
    let events = events_until_exit(&connection);
    assert_eq!(
        events,
        vec![
            json!({"error": ["InvalidResponseError", "Claude returned an invalid runtime stream."]}),
            json!({"exit": true})
        ]
    );
    connection.close();
    let _ = std::fs::remove_dir_all(&workspace);
}

fn process_alive(pid: u32) -> bool {
    #[cfg(target_os = "linux")]
    {
        let status = std::fs::read_to_string(format!("/proc/{pid}/stat")).unwrap_or_default();
        // Gone, or a zombie waiting to be collected.
        !status.is_empty() && !status.split(')').nth(1).is_some_and(|rest| rest.trim_start().starts_with('Z'))
    }
    #[cfg(windows)]
    {
        let output = std::process::Command::new("tasklist")
            .args(["/FI", &format!("PID eq {pid}"), "/NH"])
            .output()
            .expect("tasklist");
        String::from_utf8_lossy(&output.stdout).contains(&pid.to_string())
    }
    #[cfg(not(any(target_os = "linux", windows)))]
    {
        let _ = pid;
        false
    }
}

fn codex(home: &Path, script: Value, tools: &[&str]) -> CodexConnection {
    std::fs::create_dir_all(home.join("workspace")).unwrap();
    write_script(&home.join("workspace"), script);
    CodexConnection::new(home, Some(&me()), tools.iter().map(|tool| (*tool).to_owned()).collect())
}

fn codex_reads() {
    for case in vectors()["codex_reads"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let home = folder(&format!("codex-{name}"));
        let connection = codex(&home, json!([{"write": case["data"]}, {"copy": true}]), &["roll", "look"]);
        connection.start_process().expect("starts");
        let record = connection.home().join("workspace").join(RECORD);
        let written = case["written"].as_str().unwrap().replace('\n', LINE_SEPARATOR);
        assert!(
            wait_until(Duration::from_secs(20), || std::fs::read(&record).unwrap_or_default().len()
                >= written.len()),
            "{name}: the denials were written"
        );
        let mut notifications = Vec::new();
        let expected = case["notifications"].as_array().unwrap();
        wait_until(Duration::from_secs(20), || {
            notifications.extend(connection.notifications().unwrap().into_iter().map(Json::Object));
            notifications.len() >= expected.len()
        });
        assert_eq!(&notifications, expected, "{name}: notifications");
        // Each response, in order, answers the call with its id.
        let mut calls = 0;
        for response in case["responses"].as_array().unwrap() {
            if response.is_null() {
                break;
            }
            calls += 1;
            let outcome = connection.call("probe", json!({}), Duration::from_secs(20));
            match response.get("result") {
                Some(Value::Object(result)) => assert_eq!(outcome.unwrap(), *result, "{name}"),
                _ => assert_eq!(
                    outcome.unwrap_err().message(),
                    "The Codex runtime rejected this operation. Check sign-in and runtime compatibility.",
                    "{name}"
                ),
            }
        }
        let requests: String = (1..=calls)
            .map(|id| {
                format!(
                    "{}{LINE_SEPARATOR}",
                    sylvia_base::pyjson::dumps(
                        &json!({"id": id, "method": "probe", "params": {}}),
                        sylvia_base::pyjson::Dump::DEFAULT
                    )
                )
            })
            .collect();
        let expected_record = format!("{written}{requests}");
        assert!(wait_until(Duration::from_secs(20), || std::fs::read(&record).unwrap_or_default().len()
            >= expected_record.len()));
        assert_eq!(
            String::from_utf8(std::fs::read(&record).unwrap_or_default()).unwrap(),
            expected_record,
            "{name}: what Sylvia wrote"
        );
        connection.close();
        let _ = std::fs::remove_dir_all(&home);
    }
}

fn codex_handshake_and_calls() {
    let home = folder("codex-handshake");
    let script = json!([
        {"read": 1},
        {"write": "{\"id\": 1, \"result\": {\"userAgent\": \"codex/1\"}}\n"},
        {"read": 1},
        {"read": 1},
        {"write": "{\"id\": 2, \"result\": {\"ok\": true}}\n"},
        {"copy": true}
    ]);
    let connection = codex(&home, script, &[]);
    let seen = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let observer = seen.clone();
    connection.set_lifecycle_observer(std::sync::Arc::new(
        move |name: &str, start: u128, end: u128, status: &str| {
            assert!(end >= start);
            observer.lock().unwrap().push((name.to_owned(), status.to_owned()));
        },
    ));
    connection.start().expect("the handshake");
    assert_eq!(
        *seen.lock().unwrap(),
        vec![
            ("process_boot".to_owned(), "completed".to_owned()),
            ("initialize".to_owned(), "completed".to_owned())
        ]
    );
    let result = connection.call("thread/start", json!({"model": "gpt"}), Duration::from_secs(20)).unwrap();
    assert_eq!(Json::Object(result), json!({"ok": true}));
    let record = home.join("workspace").join(RECORD);
    let expected = [
        json!({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "sylvia_gm", "title": "Sylvia GM", "version": "0.3.0"}, "capabilities": {"experimentalApi": true}}}),
        json!({"method": "initialized"}),
        json!({"id": 2, "method": "thread/start", "params": {"model": "gpt"}}),
    ]
    .iter()
    .map(|message| String::from_utf8(runtime_process::frame(message)).unwrap())
    .collect::<String>();
    assert!(wait_until(Duration::from_secs(20), || std::fs::read(&record).unwrap_or_default().len()
        >= expected.len()));
    assert_eq!(String::from_utf8(std::fs::read(&record).unwrap()).unwrap(), expected);

    // A call with no reply times out and closes the connection.
    let silent = connection.call("thread/read", json!({}), Duration::from_millis(300)).unwrap_err();
    assert_eq!(
        silent,
        CodexError::Connection("The ChatGPT connection timed out. Reconnect to retry.".into())
    );
    let closed = connection.respond(&json!(5), Default::default()).unwrap_err();
    assert_eq!(closed.message(), "The ChatGPT connection has closed. Reconnect to continue.");
    let stopped = connection.next_event(Duration::from_millis(50)).unwrap_err();
    assert_eq!(stopped.message(), "The ChatGPT runtime stopped before completing its response.");
    let _ = std::fs::remove_dir_all(&home);

    // request_stop wakes a pending call, which reports a cancellation.
    let home = folder("codex-stop");
    let connection = std::sync::Arc::new(codex(&home, json!([{"copy": true}]), &[]));
    connection.start_process().unwrap();
    let caller = connection.clone();
    let pending = std::thread::spawn(move || caller.call("turn/start", json!({}), Duration::from_secs(60)));
    std::thread::sleep(Duration::from_millis(300));
    connection.request_stop();
    let cancelled = pending.join().unwrap().unwrap_err();
    assert_eq!(cancelled, CodexError::Cancelled("The ChatGPT response was cancelled.".into()));
    assert_eq!(
        connection.next_event(Duration::from_millis(10)).unwrap_err().class_name(),
        "CodexCancelledError"
    );
    connection.close();
    let _ = std::fs::remove_dir_all(&home);

    // Account changes are announced before the request goes out, once per listener.
    let heard = std::sync::Arc::new(std::sync::Mutex::new(Vec::<PathBuf>::new()));
    let ear = heard.clone();
    let listener: codex_rpc::AccountListener =
        std::sync::Arc::new(move |home: &Path| ear.lock().unwrap().push(home.to_owned()));
    codex_rpc::add_account_change_listener(listener.clone());
    codex_rpc::add_account_change_listener(listener);
    let home = folder("codex-account");
    let connection = codex(&home, json!([{"copy": true}]), &[]);
    let unstarted = connection.call("account/logout", json!({}), Duration::from_millis(50)).unwrap_err();
    assert_eq!(unstarted.message(), "The ChatGPT connection has closed. Reconnect to continue.");
    assert_eq!(*heard.lock().unwrap(), vec![connection.home().to_owned()]);
    let _ = std::fs::remove_dir_all(&home);
}

#[cfg(target_os = "linux")]
fn fake_parent() {
    let workspace = PathBuf::from(std::env::var_os("SYLVIA_FAKE_WORKSPACE").expect("workspace"));
    let connection = claude(&workspace, json!([{"hang": true}]));
    connection.start().expect("starts");
    println!("{}", connection.process_id().unwrap());
    loop {
        std::thread::sleep(Duration::from_secs(60));
    }
}

/// A Claude Code child dies with the owner, however the owner dies.
#[cfg(target_os = "linux")]
fn dies_with_owner() {
    use std::io::BufRead;
    let workspace = folder("parent");
    let mut parent = std::process::Command::new(me())
        .arg("--fake-parent")
        .env("SYLVIA_FAKE_WORKSPACE", &workspace)
        .stdout(std::process::Stdio::piped())
        .spawn()
        .expect("the stand-in owner starts");
    let mut line = String::new();
    std::io::BufReader::new(parent.stdout.take().unwrap()).read_line(&mut line).unwrap();
    let child: u32 = line.trim().parse().expect("the child's id");
    assert!(process_alive(child));
    parent.kill().unwrap();
    parent.wait().unwrap();
    assert!(wait_until(Duration::from_secs(10), || !process_alive(child)), "the runtime died with its owner");
    let _ = std::fs::remove_dir_all(&workspace);
}

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if let Some(code) = fake_claude_cli(&arguments) {
        std::process::exit(code);
    }
    if arguments.iter().any(|argument| argument == "--fake-runtime" || argument == "app-server") {
        fake_runtime();
        return;
    }
    #[cfg(target_os = "linux")]
    if arguments.iter().any(|argument| argument == "--fake-parent") {
        fake_parent();
        return;
    }
    #[allow(unused_mut)]
    let mut tests: Vec<(&str, fn())> = vec![
        ("context_windows", context_windows),
        ("environments", environments),
        ("auth_commands", auth_commands),
        ("versions_and_statuses", versions_and_statuses),
        ("runtimes", runtimes),
        ("which_and_codex_lookup", which_and_codex_lookup),
        ("claude_frames", claude_frames),
        ("claude_writes", claude_writes),
        ("claude_process_behaviour", claude_process_behaviour),
        ("oversized_frame", oversized_frame),
        ("codex_reads", codex_reads),
        ("codex_handshake_and_calls", codex_handshake_and_calls),
    ];
    #[cfg(target_os = "linux")]
    tests.push(("dies_with_owner", dies_with_owner));
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
