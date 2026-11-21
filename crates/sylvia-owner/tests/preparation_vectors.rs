//! Update preparation, portable replacement, the restart handshake and portable extraction
//! against scenarios recorded from the Python snapshot by
//! `vectors/generate_preparation_vectors.py`, on the same file trees.

mod support;

use serde_json::{json, Value as J};
use std::collections::{BTreeMap, VecDeque};
use std::path::Path;
use std::sync::Mutex;
use support::temporary_folder;
use sylvia_base::{CodedError, CodedResult};
use sylvia_owner::desktop_update::launch::Installation;
use sylvia_owner::desktop_update::manifest::{parse_manifest, Context, KeyRing, Version, APP_VERSION};
use sylvia_owner::desktop_update::payload::extract_portable;
use sylvia_owner::desktop_update::preparation::PreparedUpdate;
use sylvia_owner::desktop_update::recovery::RecoveryOperation;
use sylvia_owner::desktop_update::recovery_service::RecoveryService;
use sylvia_owner::desktop_update::restart::{RestartContext, RestartHandshake};
use sylvia_owner::desktop_update::startup::{authorize_startup, AuthorizedStartup};
use sylvia_owner::desktop_update::storage::{is_update_error, UpdateStore};

const OP: &str = "0123456789abcdef0123456789abcdef";
const ATTEMPT: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const NEW: &str = "0.4.2";
const TARGET: &str = "windows-x86_64";

fn vectors() -> &'static J {
    static VECTORS: std::sync::OnceLock<J> = std::sync::OnceLock::new();
    VECTORS.get_or_init(|| {
        serde_json::from_str(include_str!("vectors/preparation_vectors.json")).expect("valid JSON")
    })
}

fn keyring() -> KeyRing {
    let key: [u8; 32] = hex::decode(vectors()["public_key"].as_str().unwrap()).unwrap().try_into().unwrap();
    KeyRing::new(BTreeMap::from([(vectors()["key_id"].as_str().unwrap().to_string(), key)]))
}

fn blob(digest: &str) -> Vec<u8> {
    sylvia_owner::desktop_update::manifest::b64decode_strict(vectors()["blobs"][digest].as_str().unwrap())
        .unwrap()
}

/// Python's `outcome(call)`: an `UpdateError` by code and message, any other exception by class.
fn outcome(result: CodedResult<J>) -> J {
    match result {
        Ok(value) => json!({"ok": value}),
        Err(error) if is_update_error(&error) => json!({"error": [error.code, error.message]}),
        Err(error) => json!({"other": error.error_type.unwrap_or("Exception")}),
    }
}

fn listing(root: &Path) -> J {
    use sha2::Digest;
    // Each restored file gets its own random temporary folder; its name is folded as the
    // generator folds it.
    let temp = regex::Regex::new("((?:^|/)(?:restoring|restore-payload)/)[0-9a-f]{32}").unwrap();
    let mut rows = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(folder) = pending.pop() {
        for entry in std::fs::read_dir(&folder).unwrap() {
            let path = entry.unwrap().path();
            let relative = path.strip_prefix(root).unwrap().to_string_lossy().replace('\\', "/");
            let relative = temp.replace_all(&relative, "${1}<temp>").to_string();
            if path.is_dir() {
                rows.push(json!([relative, "dir"]));
                pending.push(path);
                continue;
            }
            let raw = std::fs::read(&path).unwrap();
            let parsed = (relative.ends_with(".json") && relative.contains(".application-updates/"))
                .then(|| serde_json::from_slice::<J>(&raw).ok())
                .flatten();
            rows.push(json!([
                relative,
                parsed.unwrap_or_else(|| json!(hex::encode(sha2::Sha256::digest(&raw))))
            ]));
        }
    }
    // Sorted by the relative path text before aliasing, as the generator does.
    rows.sort_by(|left, right| left[0].as_str().cmp(&right[0].as_str()));
    J::Array(rows)
}

fn normalize(value: &J) -> J {
    match value {
        J::Object(map) => J::Object(
            map.iter()
                .map(|(key, item)| {
                    let item = if key == "data_identity" || key == "app_identity" {
                        json!("<identity>")
                    } else {
                        normalize(item)
                    };
                    (key.clone(), item)
                })
                .collect(),
        ),
        J::Array(items) => J::Array(items.iter().map(normalize).collect()),
        other => other.clone(),
    }
}

fn alias(value: &J) -> J {
    let text = String::from_utf8(sylvia_base::pyjson::canonical_bytes(value)).unwrap();
    let pattern = regex::Regex::new("[0-9a-f]+").unwrap();
    let mut seen: Vec<String> = Vec::new();
    let replaced = pattern.replace_all(&text, |found: &regex::Captures<'_>| {
        let found = found[0].to_string();
        if found.len() != 32 {
            return found;
        }
        let index = seen.iter().position(|known| *known == found).unwrap_or_else(|| {
            seen.push(found);
            seen.len() - 1
        });
        format!("<id:{}>", index + 1)
    });
    serde_json::from_str(&replaced).unwrap()
}

fn record(results: Vec<J>, files: J) -> J {
    let mut aliased = alias(&json!([normalize(&J::Array(results)), normalize(&files)]));
    let mut files = aliased[1].take();
    if let Some(rows) = files.as_array_mut() {
        rows.sort_by(|left, right| left[0].as_str().cmp(&right[0].as_str()));
    }
    json!({"results": aliased[0].take(), "files": files})
}

fn run(scenario: &J, index: usize) -> J {
    let folder = temporary_folder(&format!("prep-{index}"));
    let root = folder.0.clone();
    for (relative, digest) in scenario["tree"].as_object().unwrap() {
        let path = root.join(relative);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, blob(digest.as_str().unwrap())).unwrap();
    }
    let live = root.join("app");
    let data = live.join("isolated-data");
    let current = Version::parse_text(APP_VERSION).unwrap();
    let ring = keyring();
    let quiesced: Mutex<VecDeque<bool>> = Mutex::new(
        scenario
            .get("quiesced")
            .and_then(J::as_array)
            .map(|values| values.iter().map(|v| v == true).collect())
            .unwrap_or_default(),
    );
    let quiet = || quiesced.lock().unwrap().pop_front().unwrap_or(true);
    let backup_source = root.join("backup-source.sqlite3");
    let backup = |target: &Path| -> CodedResult<()> {
        std::fs::copy(&backup_source, target)
            .map(|_| ())
            .map_err(|error| CodedError::new("internal_error", error.to_string()))
    };
    let mut prepared: Option<PreparedUpdate> = None;
    let mut handshake: Option<RestartHandshake> = None;
    let mut nonce = String::new();
    let mut startup: Option<AuthorizedStartup> = None;
    let mut recovery: Option<RecoveryOperation> = None;
    let mut results = Vec::new();
    for op in scenario["ops"].as_array().unwrap() {
        let name = op["op"].as_str().unwrap();
        let result = match name {
            "new" => {
                let data_root = match op.get("data").and_then(J::as_str) {
                    Some(other) => live.join(other),
                    None => data.clone(),
                };
                let attempt = op.get("attempt").and_then(J::as_str).unwrap_or(ATTEMPT);
                outcome(
                    PreparedUpdate::new(&data_root, &live, OP, attempt, &current, TARGET, "portable", &ring)
                        .map(|made| {
                            prepared = Some(made);
                            J::Null
                        }),
                )
            }
            "prepare" => outcome(prepared.as_mut().unwrap().prepare(&backup, &quiet).map(|()| J::Null)),
            "require_selected" => {
                outcome(prepared.as_mut().unwrap().require_selected(None).map(|()| J::Null))
            }
            "apply" => {
                let exited = op.get("exited").is_none_or(|value| value == true);
                outcome(prepared.as_mut().unwrap().apply(&|| Ok(exited)).map(|()| J::Null))
            }
            "create_restart" => {
                outcome(prepared.as_mut().unwrap().create_restart(&op["identity"]).and_then(|made| {
                    nonce = made.load()?["nonce"].as_str().unwrap().to_string();
                    handshake = Some(made);
                    Ok(json!(nonce))
                }))
            }
            "launch" => outcome(handshake.as_ref().unwrap().launch_once(|arguments| Ok(json!(arguments)))),
            "authorize" | "confirm_handshake" => {
                let version = op.get("version").and_then(J::as_str).unwrap_or(NEW);
                let given = op.get("nonce").and_then(J::as_str).unwrap_or(&nonce).to_string();
                let context = RestartContext {
                    nonce: &given,
                    live: &live,
                    desktop_version: version,
                    owner_version: version,
                    target: TARGET,
                    distribution: "portable",
                    storage_compatibility: 1,
                };
                let handshake = handshake.as_ref().unwrap();
                if name == "authorize" {
                    outcome(handshake.authorize(&context).map(|_| J::Null))
                } else {
                    outcome(handshake.confirm(&context).map(|()| J::Null))
                }
            }
            "confirmed" => outcome(handshake.as_ref().unwrap().confirmed().map(J::Bool)),
            "confirm" => outcome(prepared.as_mut().unwrap().confirm(None).map(|()| J::Null)),
            "save_phase" => outcome(
                UpdateStore::new(&data.join(".application-updates"))
                    .and_then(|store| store.save(OP, op["phase"].as_str().unwrap(), Some(NEW), None))
                    .map(|()| J::Null),
            ),
            "write" => {
                let path = root.join(op["path"].as_str().unwrap());
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                let raw =
                    sylvia_owner::desktop_update::manifest::b64decode_strict(op["b64"].as_str().unwrap())
                        .unwrap();
                std::fs::write(path, raw).unwrap();
                json!({"ok": null})
            }
            "delete" => {
                std::fs::remove_file(root.join(op["path"].as_str().unwrap())).unwrap();
                json!({"ok": null})
            }
            "authorize_startup" => {
                let running_text = op.get("running").and_then(J::as_str).unwrap_or(NEW);
                let running = Version::parse_text(running_text).unwrap();
                let context = json!({
                    "live": live.to_string_lossy(), "data_root": data.to_string_lossy(), "version": APP_VERSION,
                    "target": TARGET, "distribution": "portable",
                });
                let given = op.get("nonce").and_then(J::as_str).unwrap_or(&nonce).to_string();
                let request = json!({"operation_id": OP, "attempt": ATTEMPT, "nonce": given});
                outcome(Installation::from_launch(Some(&context), &data).and_then(|installation| {
                    let found = authorize_startup(
                        &data,
                        installation.as_ref(),
                        Some(&request),
                        &running,
                        running_text,
                        1,
                        &ring,
                    )?;
                    startup = found;
                    Ok(J::Null)
                }))
            }
            "startup_confirm" => outcome(startup.as_mut().unwrap().confirm().map(J::Bool)),
            "recovery_new" => outcome(RecoveryOperation::new(prepared.clone().unwrap()).map(|made| {
                recovery = Some(made);
                J::Null
            })),
            "recovery_inspect" => outcome(recovery.as_mut().unwrap().inspect().map(J::Object)),
            "recovery_rollback" => {
                let exited = op.get("exited").is_none_or(|value| value == true);
                outcome(
                    recovery
                        .as_mut()
                        .unwrap()
                        .rollback(op["choice"].as_str().unwrap(), &|| Ok(exited))
                        .map(|()| J::Null),
                )
            }
            "recovery_create_restart" => {
                outcome(recovery.as_mut().unwrap().create_restart(&op["identity"]).and_then(|made| {
                    nonce = made.load()?["nonce"].as_str().unwrap().to_string();
                    Ok(json!(nonce))
                }))
            }
            "recovery_retry" => {
                let exited = op.get("exited").is_none_or(|value| value == true);
                let expected = op.get("nonce").and_then(J::as_str).unwrap_or(&nonce).to_string();
                outcome(
                    recovery
                        .as_mut()
                        .unwrap()
                        .retry_restart(&expected, &op["identity"], &|| Ok(exited))
                        .and_then(|made| {
                            nonce = made.load()?["nonce"].as_str().unwrap().to_string();
                            Ok(json!(nonce))
                        }),
                )
            }
            "recovery_launch" => {
                outcome(recovery.as_ref().unwrap().handshake.launch_once(|arguments| Ok(json!(arguments))))
            }
            "recovery_confirm_handshake" => {
                let context = RestartContext {
                    nonce: &nonce,
                    live: &live,
                    desktop_version: APP_VERSION,
                    owner_version: APP_VERSION,
                    target: TARGET,
                    distribution: "portable",
                    storage_compatibility: 1,
                };
                outcome(recovery.as_ref().unwrap().handshake.confirm(&context).map(|()| J::Null))
            }
            "recovery_confirmed" => outcome(recovery.as_ref().unwrap().handshake.confirmed().map(J::Bool)),
            "recovery_confirm" => outcome(recovery.as_mut().unwrap().confirm().map(|()| J::Null)),
            "service_state" | "service_handoff" => {
                let native = op.get("native").and_then(J::as_str).unwrap_or(APP_VERSION);
                let current_text = op.get("current").and_then(J::as_str).unwrap_or(APP_VERSION);
                let context = json!({
                    "live": live.to_string_lossy(), "data_root": data.to_string_lossy(), "version": native,
                    "target": TARGET, "distribution": "portable",
                });
                let host = json!({"version": native, "target": TARGET, "distribution": "portable"});
                outcome(Installation::for_recovery(Some(&context), &data).and_then(|installation| {
                    let service = RecoveryService {
                        installation: installation.expect("a context"),
                        current: Version::parse_text(current_text).unwrap(),
                        keyring: ring.clone(),
                        native_version: native.to_string(),
                    };
                    let mut params = serde_json::Map::new();
                    params.insert("host".into(), host);
                    if name == "service_state" {
                        service.handle("state", &params).map(J::Object)
                    } else {
                        params.insert("operation_id".into(), json!(OP));
                        params.insert(
                            "choice".into(),
                            json!(op.get("choice").and_then(J::as_str).unwrap_or("keep")),
                        );
                        params.insert("restart_nonce".into(), J::Null);
                        service.handle("handoff", &params).map(J::Object)
                    }
                }))
            }
            "authorize_recovery_startup" => {
                let context = json!({
                    "live": live.to_string_lossy(), "data_root": data.to_string_lossy(), "version": APP_VERSION,
                    "target": TARGET, "distribution": "portable",
                });
                let request =
                    json!({"operation_id": OP, "attempt": ATTEMPT, "nonce": nonce, "recovery": true});
                let running = Version::parse_text(APP_VERSION).unwrap();
                outcome(Installation::from_launch(Some(&context), &data).and_then(|installation| {
                    startup = authorize_startup(
                        &data,
                        installation.as_ref(),
                        Some(&request),
                        &running,
                        APP_VERSION,
                        1,
                        &ring,
                    )?;
                    Ok(J::Null)
                }))
            }
            "extract" => {
                let base = data.join(".application-updates").join(OP);
                let raw = std::fs::read(base.join("manifest.json")).unwrap();
                let context =
                    Context { current: &current, target: TARGET, distribution: "portable", keyring: &ring };
                outcome(parse_manifest(&raw, context).and_then(|manifest| {
                    std::fs::create_dir_all(base.join("attempts").join(ATTEMPT)).unwrap();
                    extract_portable(
                        &base.join("artifact.verified"),
                        &manifest,
                        &base.join("attempts").join(ATTEMPT).join("prepared"),
                    )
                    .map(|_| J::Null)
                }))
            }
            other => panic!("unknown op {other}"),
        };
        results.push(json!({"op": name, "result": result}));
    }
    drop(prepared);
    record(results, listing(&root))
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

#[test]
fn preparation_moves_like_python() {
    assert_eq!(vectors()["python"], "3.12.11");
    for (index, scenario) in vectors()["scenarios"].as_array().unwrap().iter().enumerate() {
        let got = run(scenario, index);
        assert!(
            got == scenario["recorded"],
            "{}: {}",
            scenario["name"],
            first_difference(&got, &scenario["recorded"], "")
        );
    }
}
