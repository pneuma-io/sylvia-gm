//! The desktop update controller and service against scenarios recorded from the Python
//! snapshot by `vectors/generate_controller_vectors.py` over the same scripted transport.
//! Operation identities are random in both, so 32-digit hex values are aliased in order of
//! first appearance before comparing.

mod support;

use serde_json::{json, Value as J};
use std::collections::{BTreeMap, HashSet};
use std::path::Path;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use support::{temporary_folder, Scripted};
use sylvia_base::{CodedResult, JsonMap};
use sylvia_owner::desktop_update::controller::{Setup, UpdateController};
use sylvia_owner::desktop_update::manifest::{KeyRing, Version, APP_VERSION};
use sylvia_owner::desktop_update::service::{DesktopUpdateService, Release, Reserved, TransportFactory};
use sylvia_owner::desktop_update::transport::Transport;
use sylvia_owner::desktop_update::update_error;

const STALE: &str = "55555555555555555555555555555555";
const ATTEMPT: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

fn vectors() -> &'static J {
    static VECTORS: std::sync::OnceLock<J> = std::sync::OnceLock::new();
    VECTORS.get_or_init(|| {
        serde_json::from_str(include_str!("vectors/controller_vectors.json")).expect("valid JSON")
    })
}

fn keys() -> BTreeMap<String, [u8; 32]> {
    let key: [u8; 32] = hex::decode(vectors()["public_key"].as_str().unwrap()).unwrap().try_into().unwrap();
    BTreeMap::from([(vectors()["key_id"].as_str().unwrap().to_string(), key)])
}

fn outcome(result: CodedResult<J>) -> J {
    match result {
        Ok(value) => json!({"ok": value}),
        Err(error) => json!({"error": [error.code, error.message]}),
    }
}

fn state(map: CodedResult<JsonMap>) -> J {
    outcome(map.map(J::Object))
}

/// `folder_listing(root)`: every entry under the update folder, sorted by relative path.
fn folder_listing(root: &Path) -> J {
    use sha2::Digest;
    let updates = root.join(".application-updates");
    let mut rows = Vec::new();
    let mut pending = vec![updates.clone()];
    while let Some(folder) = pending.pop() {
        let Ok(entries) = std::fs::read_dir(&folder) else { continue };
        for entry in entries {
            let path = entry.unwrap().path();
            let relative = path.strip_prefix(&updates).unwrap().to_string_lossy().replace('\\', "/");
            if path.is_dir() {
                rows.push((relative, json!("dir")));
                pending.push(path);
            } else if path.file_name().unwrap() == "current.json" {
                rows.push((relative, serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap()));
            } else {
                rows.push((
                    relative,
                    json!(hex::encode(sha2::Sha256::digest(std::fs::read(&path).unwrap()))),
                ));
            }
        }
    }
    rows.sort_by(|left, right| left.0.cmp(&right.0));
    J::Array(rows.into_iter().map(|(path, value)| json!([path, value])).collect())
}

/// `alias(value)`: each distinct 32-digit hex value becomes `<id:N>` in order of first
/// appearance in the canonical encoding.
fn alias(value: &J) -> J {
    let text = String::from_utf8(sylvia_base::pyjson::canonical_bytes(value)).unwrap();
    // Runs of hex digits; only a run of exactly 32 is an identity (a 64-digit hash is kept).
    let pattern = regex::Regex::new("[0-9a-f]+").unwrap();
    let mut seen: Vec<String> = Vec::new();
    let replaced = pattern.replace_all(&text, |found: &regex::Captures<'_>| {
        let found = found[0].to_string();
        if found.len() != 32 {
            return found;
        }
        let index = match seen.iter().position(|known| *known == found) {
            Some(index) => index,
            None => {
                seen.push(found);
                seen.len() - 1
            }
        };
        format!("<id:{}>", index + 1)
    });
    serde_json::from_str(&replaced).unwrap()
}

fn current() -> Version {
    Version::parse_text(APP_VERSION).unwrap()
}

async fn run_controller(scenario: &J, index: usize) -> J {
    let folder = temporary_folder(&format!("controller-{index}"));
    let root = folder.0.join("data");
    std::fs::create_dir(&root).unwrap();
    let transport = Arc::new(Scripted::new(&scenario["scripts"]));
    let distribution = scenario.get("distribution").and_then(J::as_str).unwrap_or("portable").to_string();
    let make = |transport: Arc<Scripted>| {
        let setup = Setup {
            current: current(),
            target: "windows-x86_64".into(),
            distribution: distribution.clone(),
            transport: transport as Arc<dyn Transport>,
            keyring: KeyRing::new(keys()),
        };
        UpdateController::new(&root.join(".application-updates"), setup)
    };
    let mut results = Vec::new();
    let mut controller = None;
    let renew = |controller: &mut Option<UpdateController>| -> J {
        *controller = None;
        match make(Arc::clone(&transport)) {
            Ok(made) => {
                let value = made.state();
                *controller = Some(made);
                json!({"ok": value})
            }
            Err(error) => json!({"error": [error.code, error.message]}),
        }
    };
    results.push(json!({"op": "new", "result": renew(&mut controller)}));
    let failures = Arc::new(AtomicUsize::new(0));
    for op in scenario["ops"].as_array().unwrap() {
        let name = op["op"].as_str().unwrap();
        let identity = if op.get("stale") == Some(&json!(true)) {
            Some(STALE.to_string())
        } else {
            controller.as_ref().and_then(|c| c.state()["operation_id"].as_str().map(str::to_string))
        };
        let id = identity.clone().unwrap_or_default();
        let result = match name {
            "new" => {
                if let Some(old) = controller.take() {
                    old.close();
                    old.wait().await;
                }
                renew(&mut controller)
            }
            "state" => json!({"ok": controller.as_ref().unwrap().state()}),
            "wait" => {
                let controller = controller.as_ref().unwrap();
                controller.wait().await;
                json!({"ok": controller.state()})
            }
            "check" => state(controller.as_ref().unwrap().check()),
            "download" => state(controller.as_ref().unwrap().download(&id)),
            "cancel" => state(controller.as_ref().unwrap().cancel(&id)),
            "defer" => state(controller.as_ref().unwrap().defer(&id)),
            "arm_shutdown" => outcome(controller.as_ref().unwrap().arm_shutdown(&id).map(|()| J::Null)),
            "prepare" => {
                let fail = op.get("fail").cloned();
                let attempt = op.get("attempt").and_then(J::as_str).unwrap_or(ATTEMPT).to_string();
                let counter = Arc::clone(&failures);
                state(controller.as_ref().unwrap().prepare(
                    &id,
                    move || match fail {
                        Some(fail) => Err(update_error(fail[0].as_str().unwrap(), fail[1].as_str().unwrap())),
                        None => Ok(attempt),
                    },
                    move || {
                        counter.fetch_add(1, Ordering::SeqCst);
                    },
                ))
            }
            "write" => {
                let relative = op["path"].as_str().unwrap().replace("<op>", &id);
                let path = root.join(".application-updates").join(relative);
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                if let Some(value) = op.get("json") {
                    let text = serde_json::to_string(value).unwrap().replace("<op>", &id);
                    let value: J = serde_json::from_str(&text).unwrap();
                    std::fs::write(&path, sylvia_base::pyjson::canonical_bytes(&value)).unwrap();
                } else {
                    std::fs::write(&path, hex::decode(op["hex"].as_str().unwrap()).unwrap()).unwrap();
                }
                json!({"ok": null})
            }
            other => panic!("unknown op {other}"),
        };
        results.push(json!({"op": name, "result": result}));
    }
    results.push(json!({"op": "on_failure_calls", "result": failures.load(Ordering::SeqCst)}));
    if let Some(controller) = controller.take() {
        controller.close();
        controller.wait().await;
    }
    let opened = transport.opened.lock().unwrap().clone();
    aliased_record(results, folder_listing(&root), opened)
}

async fn run_service(scenario: &J, index: usize) -> J {
    let folder = temporary_folder(&format!("service-{index}"));
    let root = folder.0.join("data");
    std::fs::create_dir(&root).unwrap();
    let transport = Arc::new(Scripted::new(&scenario["scripts"]));
    let factory_transport = Arc::clone(&transport);
    let factory: TransportFactory = Arc::new(move || Arc::clone(&factory_transport) as Arc<dyn Transport>);
    let reserved_set: Arc<Mutex<HashSet<String>>> = Arc::new(Mutex::new(HashSet::new()));
    let released: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let (check_set, released_list) = (Arc::clone(&reserved_set), Arc::clone(&released));
    let reserved: Reserved = Arc::new(move |identity: &str| check_set.lock().unwrap().contains(identity));
    let release: Release = Arc::new(move |identity: &str| {
        released_list.lock().unwrap().push(identity.to_string());
        Ok(())
    });
    let keys = if scenario.get("keys") == Some(&json!(false)) { BTreeMap::new() } else { keys() };
    let service = DesktopUpdateService::with_trust(&root, None, Some(reserved), Some(release), keys, factory);
    let mut results = Vec::new();
    for op in scenario["ops"].as_array().unwrap() {
        let name = op["op"].as_str().unwrap();
        if name == "wait" {
            service.wait_for_workers().await;
            results.push(json!({"op": "wait", "result": null}));
            continue;
        }
        let host = op.get("host").cloned().unwrap_or_else(
            || json!({"version": APP_VERSION, "target": "windows-x86_64", "distribution": "portable"}),
        );
        let mut params = JsonMap::new();
        params.insert("host".into(), host);
        if let Some(identity) = op.get("operation_id") {
            let identity = if identity == "<current>" {
                service.selected_operation().map_or(J::Null, J::String)
            } else {
                identity.clone()
            };
            if op.get("reserve") == Some(&json!(true)) {
                reserved_set.lock().unwrap().insert(identity.as_str().unwrap_or_default().to_string());
            }
            params.insert("operation_id".into(), identity);
        }
        results.push(json!({"op": name, "result": state(service.handle(name, &params))}));
    }
    results.push(json!({"op": "released", "result": *released.lock().unwrap()}));
    service.close().await;
    let opened = transport.opened.lock().unwrap().clone();
    aliased_record(results, folder_listing(&root), opened)
}

/// The launch vectors name Windows paths (`<tmp>\app`), so they are checked on Windows.
#[cfg(windows)]
#[test]
fn installation_contexts_are_read_like_python() {
    use sylvia_owner::desktop_update::launch::Installation;
    let folder = temporary_folder("launch");
    for name in ["app", "data", "portable", "portable/isolated-data"] {
        std::fs::create_dir_all(folder.0.join(name)).unwrap();
    }
    let tmp = folder.0.to_string_lossy().to_string();
    fn fill(value: &J, tmp: &str) -> J {
        match value {
            J::String(text) => J::String(text.replace("<tmp>", tmp)),
            J::Array(items) => J::Array(items.iter().map(|item| fill(item, tmp)).collect()),
            J::Object(map) => {
                J::Object(map.iter().map(|(key, item)| (key.clone(), fill(item, tmp))).collect())
            }
            other => other.clone(),
        }
    }
    let summary = |found: Option<Installation>| match found {
        None => J::Null,
        Some(found) => json!({
            "live": found.live.to_string_lossy().replace(&tmp, "<tmp>"),
            "data": found.data.to_string_lossy().replace(&tmp, "<tmp>"),
            "distribution": found.distribution,
            "target": found.target,
        }),
    };
    for case in vectors()["launch"].as_array().unwrap() {
        let context = fill(&case["context"], &tmp);
        let context = (!context.is_null()).then_some(&context);
        let data = std::path::PathBuf::from(case["data"].as_str().unwrap().replace("<tmp>", &tmp));
        let got = outcome(Installation::from_launch(context, &data).map(summary));
        assert_eq!(got, case["from_launch"], "{} (from_launch)", case["name"]);
        let got = outcome(Installation::for_recovery(context, &data).map(summary));
        assert_eq!(got, case["for_recovery"], "{} (for_recovery)", case["name"]);
    }
}

#[test]
fn the_reference_is_the_windows_snapshot() {
    assert_eq!(vectors()["python"], "3.12.11");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn the_controller_moves_like_python() {
    for (index, scenario) in vectors()["controller"].as_array().unwrap().iter().enumerate() {
        let got = run_controller(scenario, index).await;
        assert!(
            got == scenario["recorded"],
            "{}: {}",
            scenario["name"],
            first_difference(&got, &scenario["recorded"], "")
        );
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn the_service_answers_like_python() {
    for (index, scenario) in vectors()["service"].as_array().unwrap().iter().enumerate() {
        let got = run_service(scenario, index).await;
        assert!(
            got == scenario["recorded"],
            "{}: {}",
            scenario["name"],
            first_difference(&got, &scenario["recorded"], "")
        );
    }
}

/// The generator's `aliased_record`: alias the identities, then sort the folder listing by its
/// aliased paths (the raw paths begin with random identities).
fn aliased_record(results: Vec<J>, files: J, opened: Vec<String>) -> J {
    // Identities are numbered in this fixed order (the results first), as the generator does.
    let mut aliased = alias(&json!([results, opened, files]));
    let mut files = aliased[2].take();
    if let Some(rows) = files.as_array_mut() {
        rows.sort_by(|left, right| left[0].as_str().cmp(&right[0].as_str()));
    }
    json!({"results": aliased[0].take(), "files": files, "opened": aliased[1].take()})
}

/// Where two values first differ, as a JSON pointer with both values.
fn first_difference(got: &J, expected: &J, path: &str) -> String {
    match (got, expected) {
        (J::Object(left), J::Object(right)) => {
            for key in left.keys().chain(right.keys()) {
                let (a, b) = (left.get(key).unwrap_or(&J::Null), right.get(key).unwrap_or(&J::Null));
                if a != b {
                    return first_difference(a, b, &format!("{path}/{key}"));
                }
            }
        }
        (J::Array(left), J::Array(right)) if left.len() == right.len() => {
            for (index, (a, b)) in left.iter().zip(right).enumerate() {
                if a != b {
                    return first_difference(a, b, &format!("{path}/{index}"));
                }
            }
        }
        _ => {}
    }
    format!("{path}: got {got}, expected {expected}")
}
