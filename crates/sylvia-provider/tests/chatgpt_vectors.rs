//! The ChatGPT account (`chatgpt_account`, `config::chatgpt_settings`) and Python's
//! `datetime.fromisoformat` (`pydatetime`) against the frozen Python
//! (`vectors/generate_chatgpt_vectors.py`).
//!
//! This test runs without libtest's harness because it is also the stand-in Codex app-server:
//! started as `<this binary> app-server ...` in a runtime home's workspace, it plays
//! `vectors/chatgpt_standin.py`'s script the same way, so the account runs on the real
//! `CodexConnection` as Python's does.

use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_provider::chatgpt_account::{subscription_model_facts, ChatGPTAccount};
use sylvia_provider::codex_rpc::{self, python_resolve, CodexConnection, CodexError};
use sylvia_provider::config::chatgpt_settings::{self, ChatGPTSettingsError};
use sylvia_provider::config::env::Environment;
use sylvia_provider::config::protection::{self, Seal};
use sylvia_provider::config::provider_settings::ConfigSources;
use sylvia_provider::config::ConfigFailure;
use sylvia_provider::pydatetime;

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/chatgpt_vectors.json")).expect("chatgpt vectors")
}

/// A fresh work folder; an old one that cannot be removed fails here, with its path.
fn folder(name: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!("sylvia-chatgpt-{}-{name}", std::process::id()));
    if path.exists() {
        std::fs::remove_dir_all(&path)
            .unwrap_or_else(|error| panic!("cannot clear {}: {error}", path.display()));
    }
    std::fs::create_dir_all(&path).unwrap();
    python_resolve(&path)
}

// ── The stand-in app-server ──────────────────────────────────────────────────────────────

fn stand_in() -> i32 {
    let home = std::env::current_dir().unwrap().parent().unwrap().to_owned();
    let script: Value = serde_json::from_slice(&std::fs::read(home.join("standin.json")).unwrap()).unwrap();
    let steps = script["steps"].as_array().unwrap();
    let position_path = home.join("standin-position");
    let mut position: usize =
        std::fs::read_to_string(&position_path).ok().and_then(|text| text.parse().ok()).unwrap_or(0);
    let stdout = std::io::stdout();
    let write = |message: &Json| {
        let mut out = stdout.lock();
        out.write_all(format!("{}\n", pyjson::dumps(message, Dump::DEFAULT)).as_bytes()).unwrap();
        out.flush().unwrap();
    };
    for line in std::io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        let message = pyjson::loads(&line).unwrap();
        let mut log = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(home.join("standin-log.jsonl"))
            .unwrap();
        log.write_all(
            format!("{}\n", pyjson::dumps(&message, Dump { sort_keys: true, ..Dump::DEFAULT })).as_bytes(),
        )
        .unwrap();
        let Some(id) = message.get("id").cloned() else { continue };
        if message.get("method").and_then(Json::as_str) == Some("initialize") {
            write(&json!({"id": id, "result": {"userAgent": "standin"}}));
            continue;
        }
        let fallback = json!({"method": null, "error": {"message": "no step"}});
        let step = steps.get(position).unwrap_or(&fallback);
        position += 1;
        std::fs::write(&position_path, position.to_string()).unwrap();
        for notification in step.get("notify").and_then(Value::as_array).into_iter().flatten() {
            write(notification);
        }
        if step["method"] != message["method"] {
            write(&json!({"id": id, "error": {"message": "unexpected"}}));
        } else if let Some(error) = step.get("error") {
            write(&json!({"id": id, "error": error}));
        } else {
            write(&json!({"id": id, "result": step["result"]}));
        }
    }
    0
}

/// A runtime home with the stand-in's script (and catalog), and the stand-in as its runtime.
fn standin_home(home: &Path, case: &Value) -> PathBuf {
    std::fs::create_dir_all(home.join("workspace")).unwrap();
    std::fs::write(home.join("standin.json"), json!({"steps": case["steps"]}).to_string()).unwrap();
    if !case["catalog"].is_null() {
        std::fs::write(home.join("models_cache.json"), case["catalog"].to_string()).unwrap();
    }
    let runtime = home.parent().unwrap().join("runtime");
    std::fs::create_dir_all(&runtime).unwrap();
    let executable = runtime.join(if cfg!(windows) { "codex.exe" } else { "codex" });
    std::fs::copy(std::env::current_exe().unwrap(), &executable).unwrap();
    executable
}

fn log_of(home: &Path) -> Vec<Value> {
    let text = std::fs::read_to_string(home.join("standin-log.jsonl")).unwrap_or_default();
    text.lines().map(|line| serde_json::from_str(line).unwrap()).collect()
}

fn sorted(value: &Value) -> String {
    pyjson::dumps(value, Dump { sort_keys: true, ..Dump::DEFAULT })
}

fn codex_error(error: &CodexError) -> Value {
    json!({"error": [error.class_name(), error.message()]})
}

static CHANGES: AtomicUsize = AtomicUsize::new(0);

fn now_micros(now: &str) -> i128 {
    pydatetime::fromisoformat(now).unwrap().utc_micros().unwrap()
}

// ── Cases ────────────────────────────────────────────────────────────────────────────────

fn fromisoformat() {
    let mut mismatches = Vec::new();
    for case in vectors()["isoformat"].as_array().unwrap() {
        let text = case["text"].as_str().unwrap();
        let ours = pydatetime::fromisoformat(text);
        let same = match (&ours, case["parsed"].as_str()) {
            (None, None) => true,
            (Some(parsed), Some(theirs)) => {
                parsed.isoformat() == theirs && parsed.offset == case["offset_micros"].as_i64()
            }
            _ => false,
        };
        if !same {
            mismatches.push(format!(
                "{text:?}: Rust {:?}, Python {}",
                ours.map(|p| p.isoformat()),
                case["parsed"]
            ));
        }
    }
    assert!(mismatches.is_empty(), "{}", mismatches.join("\n"));
}

fn facts(now: i128) {
    for case in vectors()["facts"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let home = folder(&format!("facts-{name}"));
        let content = &case["content"];
        if let Some(text) = content.as_str() {
            let bytes = match (name, text) {
                ("utf16", hex) => (0..hex.len())
                    .step_by(2)
                    .map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap())
                    .collect(),
                (_, "@too-large") => vec![b' '; 8 * 1024 * 1024 + 1],
                (_, text) => text.as_bytes().to_vec(),
            };
            std::fs::write(home.join("models_cache.json"), bytes).unwrap();
        }
        let ids: BTreeSet<String> =
            case["ids"].as_array().unwrap().iter().map(|id| id.as_str().unwrap().to_owned()).collect();
        let found = subscription_model_facts(&home, &ids, now);
        let ours: JsonMap = found.into_iter().map(|(key, value)| (key, Json::Object(value))).collect();
        assert_eq!(
            pyjson::dumps(&Json::Object(ours), Dump::DEFAULT),
            pyjson::dumps(&case["facts"], Dump::DEFAULT),
            "facts {name}"
        );
        let _ = std::fs::remove_dir_all(&home);
    }
}

fn accounts(now: i128) {
    for case in vectors()["accounts"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let home = folder(&format!("account-{name}")).join("chatgpt-runtime");
        let executable = standin_home(&home, case);
        let account = ChatGPTAccount::new(CodexConnection::new(&home, Some(&executable), BTreeSet::new()));
        CHANGES.store(0, Ordering::SeqCst);
        let mut results = Vec::new();
        for op in case["ops"].as_array().unwrap() {
            let outcome = match op.as_str().unwrap() {
                "status" => account.status().map(Json::Object),
                "begin_login" => account.begin_login().map(Json::Object),
                "cancel_login" => account.cancel_login().map(Json::Object),
                "logout" => account.logout().map(Json::Object),
                "models" => account
                    .models_at(now)
                    .map(|models| Json::Array(models.into_iter().map(Json::Object).collect())),
                other => panic!("{other}"),
            };
            results.push(match outcome {
                Ok(value) => json!({"value": value}),
                Err(error) => codex_error(&error),
            });
        }
        account.connection.close();
        assert_eq!(
            pyjson::dumps(&Value::Array(results), Dump::DEFAULT),
            pyjson::dumps(&case["results"], Dump::DEFAULT),
            "account {name}"
        );
        assert_eq!(
            CHANGES.load(Ordering::SeqCst),
            case["changes"].as_u64().unwrap() as usize,
            "account changes {name}"
        );
        let (ours, theirs) = (log_of(&home), case["log"].as_array().unwrap().clone());
        assert_eq!(
            ours.iter().map(sorted).collect::<Vec<_>>(),
            theirs.iter().map(sorted).collect::<Vec<_>>(),
            "account log {name}"
        );
        let _ = std::fs::remove_dir_all(home.parent().unwrap());
        println!("account {name} ... ok");
    }
}

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

fn settings(now: &str) {
    let env = Environment::default();
    let default_config = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../config/config.yaml");
    for case in vectors()["settings"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let root = folder(&format!("settings-{name}")).join("owner");
        std::fs::create_dir_all(&root).unwrap();
        let home = root.join("chatgpt-runtime");
        let executable = standin_home(&home, case);
        if let Some(seed) = case["seed"].as_str() {
            std::fs::write(root.join("config.yaml"), seed).unwrap();
        }
        let opens = case["browser"].as_bool().unwrap_or(true);
        let opened = Mutex::new(Vec::<String>::new());
        let open = |url: &str| {
            opened.lock().unwrap().push(url.to_owned());
            Ok(opens)
        };
        CHANGES.store(0, Ordering::SeqCst);
        let mut results = Vec::new();
        for call in case["calls"].as_array().unwrap() {
            let sources = ConfigSources { default_config: &default_config, seal: &FakeSeal, env: &env };
            let outcome = chatgpt_settings::chatgpt_settings_action(
                sources,
                &root,
                &call[0],
                &call[1],
                Some(&executable),
                &open,
                &|| now.to_owned(),
            );
            results.push(match outcome {
                Ok(status) => json!({"value": Json::Object(status)}),
                Err(ChatGPTSettingsError::Codex(error)) => codex_error(&error),
                Err(other) => panic!("{name}: {other:?}"),
            });
        }
        chatgpt_settings::account(&root, None).connection.close();
        assert_eq!(
            pyjson::dumps(&Value::Array(results), Dump::DEFAULT),
            pyjson::dumps(&case["results"], Dump::DEFAULT),
            "settings {name}"
        );
        assert_eq!(json!(*opened.lock().unwrap()), case["opened"], "browser {name}");
        assert_eq!(
            CHANGES.load(Ordering::SeqCst),
            case["changes"].as_u64().unwrap() as usize,
            "settings changes {name}"
        );
        let (ours, theirs) = (log_of(&home), case["log"].as_array().unwrap().clone());
        assert_eq!(
            ours.iter().map(sorted).collect::<Vec<_>>(),
            theirs.iter().map(sorted).collect::<Vec<_>>(),
            "settings log {name}"
        );
        let config = std::fs::read_to_string(root.join("config.yaml")).ok();
        assert_eq!(json!(config), case["config"], "config {name}");
        let _ = std::fs::remove_dir_all(root.parent().unwrap());
        println!("settings {name} ... ok");
    }
}

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.get(1).map(String::as_str) == Some("app-server") {
        std::process::exit(stand_in());
    }
    if arguments.iter().any(|argument| argument == "--list") {
        println!("chatgpt_vectors: test");
        return;
    }
    codex_rpc::add_account_change_listener(Arc::new(|_home: &Path| {
        CHANGES.fetch_add(1, Ordering::SeqCst);
    }));
    let vectors = vectors();
    let now = vectors["now"].as_str().unwrap().to_owned();
    fromisoformat();
    println!("fromisoformat ... ok");
    facts(now_micros(&now));
    println!("facts ... ok");
    accounts(now_micros(&now));
    settings(&now);
    println!("\ntest result: ok. The ChatGPT account matches Python");
}
