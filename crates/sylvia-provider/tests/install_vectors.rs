//! The Rust Claude Code installer against the frozen Python (`vectors/generate_install_vectors.py`,
//! lane 5): the same URL answers and the same stand-in checks, then the URLs requested, the checks
//! that ran, `progress()` and the install folder compared. The install cases for the other
//! platform's binary name run on that platform. Also `_version`, `_dearmor` and the two signature
//! verdicts.

use serde_json::{json, Value};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use sylvia_base::Json;
use sylvia_provider::claude_install::{self, ClaudeInstaller, InstallSystem};
use sylvia_provider::http::{
    HttpRequest, HttpResponse, HttpTransport, Piece, ScriptedReply, ScriptedTransport, TransportError,
};
use sylvia_provider::provider::BoxFuture;
use sylvia_provider::runtime_process::{Captured, RunError};
use sylvia_provider::ProviderError;

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/install_vectors.json")).expect("install vectors")
}

fn b64(text: &str) -> Vec<u8> {
    sylvia_base::pybase64::b64decode_validate(text).expect("base64")
}

fn connection(message: &str) -> ProviderError {
    ProviderError::connection(message, None)
}

const DAMAGED: &str = "Sylvia's copy of Claude Code is damaged. Install it again in Settings > Accounts.";

/// Answers each URL from the case, records it, and cancels the install when the binary is asked
/// for (`cancel_on_binary`).
struct Routes {
    routes: Value,
    requested: Mutex<Vec<String>>,
    binary: String,
    cancel: Mutex<Option<Arc<ClaudeInstaller>>>,
}

impl HttpTransport for Routes {
    fn send(&self, request: HttpRequest) -> BoxFuture<'_, Result<HttpResponse, TransportError>> {
        Box::pin(async move {
            assert_eq!(request.method, "GET");
            self.requested.lock().unwrap().push(request.url.clone());
            if request.url.ends_with(&self.binary) {
                if let Some(installer) = self.cancel.lock().unwrap().as_ref() {
                    installer.cancel();
                }
            }
            let reply = match &self.routes[&request.url] {
                Value::String(kind) if kind == "connect-error" => {
                    ScriptedReply::Fail(TransportError::Connection("refused".into()))
                }
                Value::Array(reply) => ScriptedReply::Respond {
                    status: reply[0].as_u64().unwrap() as u16,
                    headers: Vec::new(),
                    pieces: vec![Piece::Bytes(b64(reply[1].as_str().unwrap()))],
                },
                _ => ScriptedReply::Respond { status: 404, headers: Vec::new(), pieces: Vec::new() },
            };
            ScriptedTransport::new(vec![reply]).send(request).await
        })
    }
}

fn files(install: &Path) -> Vec<Value> {
    fn walk(root: &Path, folder: &Path, out: &mut Vec<(Vec<String>, Value)>) {
        let Ok(entries) = std::fs::read_dir(folder) else { return };
        for entry in entries.flatten() {
            let path = entry.path();
            let parts: Vec<String> = path
                .strip_prefix(root)
                .unwrap()
                .components()
                .map(|part| part.as_os_str().to_string_lossy().into_owned())
                .collect();
            if path.is_dir() {
                out.push((parts, Value::Null));
                walk(root, &path, out);
            } else {
                let bytes = std::fs::read(&path).unwrap();
                out.push((parts, Value::String(bytes.iter().map(|b| *b as char).collect())));
            }
        }
    }
    let mut out = Vec::new();
    walk(install, install, &mut out);
    // `sorted(Path.rglob("*"))`: by parts, case-insensitively on Windows.
    let key = |parts: &Vec<String>| -> Vec<String> {
        parts.iter().map(|part| if cfg!(windows) { part.to_lowercase() } else { part.clone() }).collect()
    };
    out.sort_by_key(|entry| key(&entry.0));
    out.into_iter().map(|(parts, content)| json!([parts.join("/"), content])).collect()
}

fn run_case(case: &Value) {
    let name = case["name"].as_str().unwrap();
    let home = std::env::temp_dir().join(format!(
        "sylvia-install-{}-{}",
        std::process::id(),
        name.replace('/', "-")
    ));
    let _ = std::fs::remove_dir_all(&home);
    std::fs::create_dir_all(&home).unwrap();
    for existing in case["existing"].as_array().into_iter().flatten() {
        let target = home.join("install").join(existing[0].as_str().unwrap());
        std::fs::create_dir_all(target.parent().unwrap()).unwrap();
        std::fs::write(&target, existing[1].as_str().unwrap()).unwrap();
    }
    let executable = case["executable"].as_str().unwrap().to_owned();
    let routes = Arc::new(Routes {
        routes: case["routes"].clone(),
        requested: Mutex::new(Vec::new()),
        binary: format!("/{executable}"),
        cancel: Mutex::new(None),
    });
    let checks = Arc::new(Mutex::new(Vec::<Value>::new()));
    let free = case["free"].as_u64().unwrap_or(10 * 1024 * 1024 * 1024);
    let build = case["build"].as_str().map(str::to_owned);
    let platform_error = case["platform_error"].as_str().map(str::to_owned);
    let manifest_error = case["manifest_check"].as_str().map(str::to_owned);
    let publisher_error = case["publisher"].as_str().map(str::to_owned);
    let (manifest_checks, publisher_checks, version_checks) =
        (checks.clone(), checks.clone(), checks.clone());
    let system = InstallSystem {
        transport: routes.clone(),
        disk_free: Arc::new(move |_: &Path| Ok(free)),
        release_platform: Arc::new(move || match (&build, &platform_error) {
            (_, Some(message)) => Err(connection(message)),
            (Some(build), None) => Ok(build.clone()),
            _ => unreachable!(),
        }),
        executable_name: sylvia_provider::claude_runtime::executable_name(),
        runtime_version: Arc::new(move |path: &Path, _: &Path| {
            let content = std::fs::read(path).unwrap();
            version_checks
                .lock()
                .unwrap()
                .push(json!(["version", path.parent().unwrap().file_name().unwrap().to_string_lossy()]));
            match content.strip_prefix(b"claude-version:") {
                Some(version) => Ok(String::from_utf8(version.to_vec()).unwrap()),
                None => Err(connection(DAMAGED)),
            }
        }),
        verify_manifest: Arc::new(move |listing: &[u8], signature: &[u8], key: &[u8]| {
            manifest_checks.lock().unwrap().push(json!([
                "manifest",
                listing.len(),
                String::from_utf8_lossy(signature),
                String::from_utf8_lossy(key)
            ]));
            manifest_error.as_deref().map_or(Ok(()), |message| Err(connection(message)))
        }),
        verify_publisher: Arc::new(move |path: &Path| {
            publisher_checks
                .lock()
                .unwrap()
                .push(json!(["publisher", path.file_name().unwrap().to_string_lossy()]));
            publisher_error.as_deref().map_or(Ok(()), |message| Err(connection(message)))
        }),
    };
    let installer = Arc::new(ClaudeInstaller::new(&home, system));
    if case["cancel_on_binary"].as_bool().unwrap_or(false) {
        *routes.cancel.lock().unwrap() = Some(installer.clone());
    }
    let escaped = installer.run().err();
    assert_eq!(
        escaped.map(|message| json!(["RuntimeError", message])).unwrap_or(Value::Null),
        case["escaped"],
        "{name}"
    );
    assert_eq!(Json::Object(installer.progress()), case["progress"], "progress for {name}");
    assert_eq!(json!(*routes.requested.lock().unwrap()), case["requested"], "requests for {name}");
    assert_eq!(json!(*checks.lock().unwrap()), case["checks"], "checks for {name}");
    assert_eq!(Value::Array(files(&home.join("install"))), case["files"], "files for {name}");
    *routes.cancel.lock().unwrap() = None;
    let _ = std::fs::remove_dir_all(&home);
}

#[test]
fn installs_match_python() {
    let mine = sylvia_provider::claude_runtime::executable_name();
    let mut ran = 0;
    for case in vectors()["installs"].as_array().unwrap() {
        if case["executable"].as_str() == Some(mine) || case["build"].is_null() {
            run_case(case);
            ran += 1;
        }
    }
    assert!(ran > 30, "{ran} cases for {mine}");
}

#[test]
fn versions_and_armor_match_python() {
    let vectors = vectors();
    for (text, expected) in vectors["versions"].as_object().unwrap() {
        match claude_install::release_version(text) {
            Ok(parts) => {
                let numbers: Vec<Value> =
                    parts.iter().map(|part| json!(part.parse::<u64>().unwrap())).collect();
                assert_eq!(Value::Array(numbers), *expected, "{text:?}");
            }
            Err(error) => assert_eq!(error.message, expected.as_str().unwrap(), "{text:?}"),
        }
    }
    for (name, case) in vectors["dearmor"].as_object().unwrap() {
        let ours = claude_install::dearmor(case["text"].as_str().unwrap().as_bytes());
        match (ours, case.get("value")) {
            (Ok(bytes), Some(value)) => {
                assert_eq!(sylvia_base::pybase64::b64encode(&bytes), value.as_str().unwrap(), "{name}")
            }
            (Err(_), None) => {}
            (ours, _) => panic!("{name}: Rust {ours:?}, Python {case}"),
        }
    }
}

fn outcome(value: &Value) -> Result<Captured, RunError> {
    match value.as_str() {
        Some("oserror") => Err(RunError::Start(std::io::Error::other("no"))),
        Some("timeout") => Err(RunError::Timeout),
        _ => Ok(Captured {
            code: Some(value[1].as_i64().unwrap() as i32),
            stdout: value[0].as_str().unwrap().to_owned(),
        }),
    }
}

#[test]
fn signature_verdicts_match_python() {
    let vectors = vectors();
    let check = |cases: &Value, verdict: fn(Result<Captured, RunError>) -> Result<(), ProviderError>| {
        for case in cases.as_array().unwrap() {
            match (verdict(outcome(&case["outcome"])), case.get("ok")) {
                (Ok(()), Some(_)) => {}
                (Err(error), None) => assert_eq!(error.message, case["error"].as_str().unwrap(), "{case}"),
                (ours, _) => panic!("{case}: Rust {ours:?}"),
            }
        }
    };
    check(&vectors["gpgv"], claude_install::gpgv_verdict);
    check(&vectors["publisher"], claude_install::publisher_verdict);
}

#[test]
fn the_release_platform_names_the_published_builds() {
    use claude_install::release_platform_for as build;
    assert_eq!(build("AMD64", true, false, false).unwrap(), "win32-x64");
    assert_eq!(build("ARM64", true, false, false).unwrap(), "win32-arm64");
    assert_eq!(build("x86_64", false, true, false).unwrap(), "linux-x64");
    assert_eq!(build("aarch64", false, true, true).unwrap(), "linux-arm64-musl");
    assert_eq!(
        build("riscv64", false, true, false).unwrap_err().message,
        "Sylvia can set up Claude Code on Windows and Linux only in this version."
    );
    assert_eq!(
        build("x86_64", false, false, false).unwrap_err().kind.class_name(),
        "ProviderConnectionError"
    );
    let ours = claude_install::release_platform().unwrap();
    assert!(ours == if cfg!(windows) { "win32-x64" } else { "linux-x64" }, "{ours}");
    let _: PathBuf = PathBuf::new();
}
