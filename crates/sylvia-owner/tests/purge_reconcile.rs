//! Launch-time Builder-source purge reconciliation against Python's owner.
//!
//! Lane 3's generator (`sylvia-app/tests/vectors/generate_purge_runs.py`) leaves purge journals
//! on disk in storage roots on two Python-made databases and records what Python's launch-time
//! `reconcile_builder_source_purges` makes of each. Here the built owner launches on the same
//! storage root: it must reach `ready` exactly when Python's reconciliation succeeded, and leave
//! the campaign workspaces as Python's did.

use flate2::read::GzDecoder;
use serde_json::{json, Value as Json};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

fn workspace(relative: &str) -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").join(relative)
}

fn gunzip_to(relative: &str, target: &Path) {
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(workspace(relative)).unwrap()).read_to_end(&mut bytes).unwrap();
    std::fs::write(target, bytes).unwrap();
}

/// The generator's `tree`: every entry under `campaigns/`, relative, with each file's SHA-256.
fn tree(root: &Path) -> Json {
    fn walk(root: &Path, folder: &Path, entries: &mut Vec<(String, Json)>) {
        for entry in std::fs::read_dir(folder).unwrap() {
            let path = entry.unwrap().path();
            let relative = path.strip_prefix(root).unwrap().to_string_lossy().replace('\\', "/");
            if path.is_dir() {
                entries.push((relative.clone(), json!([relative, "dir", null])));
                walk(root, &path, entries);
            } else {
                let digest = sylvia_base::hash::sha256_hex(std::fs::read(&path).unwrap());
                entries.push((relative.clone(), json!([relative, "file", digest])));
            }
        }
    }
    let mut entries = Vec::new();
    if root.join("campaigns").exists() {
        walk(root, &root.join("campaigns"), &mut entries);
    }
    entries.sort_by(|left, right| left.0.cmp(&right.0));
    Json::Array(entries.into_iter().map(|(_, entry)| entry).collect())
}

/// The generator's `journal`: a purge journal as `stage_builder_sources_for_purge` leaves it.
fn journal(root: &Path, item: &Json) {
    let campaign_id = item["campaign"].as_str().unwrap();
    let operation_id = item["operation"].as_str().unwrap();
    let marker_campaign = item["marker"].as_str().unwrap_or(campaign_id);
    let name = format!("purge-{}", &sylvia_base::hash::sha256_hex(operation_id.as_bytes())[..24]);
    let operation =
        root.join("campaigns").join(campaign_id).join(".builder-sources-purge-pending").join(name);
    std::fs::create_dir_all(&operation).unwrap();
    // `json.dumps(marker, sort_keys=True, separators=(",", ":"))`.
    let marker = format!(
        r#"{{"campaign_id":{},"format":"sylvia-gm-builder-source-purge","operation_id":{},"version":1}}"#,
        json!(marker_campaign),
        json!(operation_id)
    );
    std::fs::write(operation.join("purge.json"), marker).unwrap();
    for (relative, text) in item["sources"].as_object().unwrap() {
        let path = operation.join("sources").join(relative);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text.as_str().unwrap()).unwrap();
    }
}

#[test]
fn launch_reconciles_purge_journals_as_pythons_owner_does() {
    let path = workspace("crates/sylvia-app/tests/vectors/purge/reconcile_runs.json");
    let record: Json = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    for state in record["states"].as_array().unwrap() {
        let name = state["name"].as_str().unwrap();
        let root = std::env::temp_dir().join(format!("sylvia-owner-reconcile-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let database = root.join("authority.sqlite3");
        let base = match state["base"].as_str().unwrap() {
            "seed" => "seed",
            "purged" => "purge_runs",
            other => panic!("unknown base {other}"),
        };
        gunzip_to(&format!("crates/sylvia-app/tests/vectors/purge/{base}.sqlite3.gz"), &database);
        for item in state["journals"].as_array().unwrap() {
            journal(&root, item);
        }
        for relative in state["files"].as_array().unwrap() {
            let path = root.join(relative.as_str().unwrap());
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, b"{}").unwrap();
        }
        assert_eq!(tree(&root), state["before"], "{name}: the storage root before launch");

        let mut child = Command::new(env!("CARGO_BIN_EXE_sylvia-gm-owner"))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("the owner starts");
        let mut stdin = child.stdin.take().unwrap();
        let envelope = json!({
            "type": "launch", "secret_hex": "00".repeat(32),
            "database_path": database, "owner_id": "reconcile-test",
        });
        stdin.write_all(format!("{envelope}\n").as_bytes()).unwrap();
        stdin.flush().unwrap();
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut first = String::new();
        stdout.read_line(&mut first).unwrap();
        drop(stdin);
        let status = child.wait().unwrap();
        let ready = first.contains(r#""type":"ready""#);
        let python_ok = state["outcome"].get("ok").is_some();
        assert_eq!(
            ready, python_ok,
            "{name}: Rust reached ready {ready}, Python's reconciliation {}",
            state["outcome"]
        );
        if !python_ok {
            assert!(
                first.is_empty() && !status.success(),
                "{name}: a failed launch writes nothing and fails"
            );
        }
        assert_eq!(tree(&root), state["after"], "{name}: the storage root after launch");
        let _ = std::fs::remove_dir_all(&root);
    }
}
