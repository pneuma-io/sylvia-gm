//! The legacy Builder chat migration's store half against Python
//! (`vectors/legacy_builder/generate_legacy_builder_store.py`): `retain_legacy_workspace` and
//! `detach_legacy_builder_chat`, replayed on the same Python-made bases with the calls Python's
//! migration made, and on the refusals; compared by outcome, the four session tables and the
//! storage tree.

mod common;

use flate2::read::GzDecoder;
use std::io::Read;
use std::path::Path;
use sylvia_base::{Json, JsonMap};
use sylvia_store::builder_launch_migration::retain_legacy_workspace;
use sylvia_store::{AuthorityStore, StoreError, StoreResult};

const TABLES: [&str; 4] = [
    "agent_sessions",
    "agent_session_messages",
    "standalone_builder_chats",
    "standalone_builder_chat_messages",
];

fn vectors(relative: &str) -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/legacy_builder").join(relative)
}

/// The generator's `skipped`: the database and its lock, the owner's pre-migration copies, and
/// the bundled starter characters the migration's catalog writes (ported with the catalog).
fn skipped(relative: &str) -> bool {
    ["authority.sqlite3", "migration-sources", "bundled-srd-starters"]
        .iter()
        .any(|prefix| relative.starts_with(prefix))
}

/// The generator's `tree`: every entry under `root`, relative and sorted, each file with its
/// SHA-256.
fn tree(root: &Path) -> Json {
    fn walk(root: &Path, folder: &Path, entries: &mut Vec<(String, Json)>) {
        for entry in std::fs::read_dir(folder).unwrap() {
            let path = entry.unwrap().path();
            let relative = path.strip_prefix(root).unwrap().to_string_lossy().replace('\\', "/");
            if skipped(&relative) {
                continue;
            }
            if path.is_dir() {
                entries.push((relative.clone(), serde_json::json!([relative, "dir", null])));
                walk(root, &path, entries);
            } else {
                let digest = sylvia_base::hash::sha256_hex(std::fs::read(&path).unwrap());
                entries.push((relative.clone(), serde_json::json!([relative, "file", digest])));
            }
        }
    }
    let mut entries = Vec::new();
    walk(root, root, &mut entries);
    // `sorted(root.rglob("*"))` sorts paths part by part.
    entries.sort_by(|left, right| left.0.split('/').cmp(right.0.split('/')));
    Json::Array(entries.into_iter().map(|(_, entry)| entry).collect())
}

fn tables(database: &Path) -> Json {
    let connection = rusqlite::Connection::open(database).unwrap();
    let mut out = JsonMap::new();
    for table in TABLES {
        out.insert(
            table.into(),
            Json::Array(common::rows(&connection, &format!("SELECT * FROM {table} ORDER BY rowid"))),
        );
    }
    Json::Object(out)
}

fn write_files(root: &Path, files: &Json) {
    for (relative, text) in files.as_object().unwrap() {
        let path = root.join(relative);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text.as_str().unwrap()).unwrap();
    }
}

/// Python's outcome record for a Rust result.
fn outcome(result: StoreResult<Json>) -> Json {
    match result {
        Ok(value) => serde_json::json!({"ok": value}),
        Err(error) => match error.rejection() {
            Some(coded) => {
                serde_json::json!({"error": coded.error_type, "code": coded.code, "message": coded.message})
            }
            // Python's own exceptions (`TypeError`, `ValueError`): the message is what Rust keeps.
            None => match error {
                StoreError::Other(message) => {
                    serde_json::json!({"error": "TypeError", "code": null, "message": message})
                }
                other => panic!("unexpected Rust error {other:?}"),
            },
        },
    }
}

fn replay(store: &AuthorityStore, root: &Path, call: &Json) -> StoreResult<Json> {
    let text = |key: &str| call[key].as_str().unwrap();
    match text("call") {
        "retain" => retain_legacy_workspace(root, text("campaign_id"), text("session_id")).map(|path| {
            Json::String(
                path.strip_prefix(sylvia_store_resolve(root)).unwrap().to_string_lossy().replace('\\', "/"),
            )
        }),
        "detach" => store
            .detach_legacy_builder_chat(
                text("session_id"),
                call["expected_message_count"].as_u64().unwrap() as usize,
                call["launch_context"].as_object().unwrap(),
                text("display_name"),
            )
            .map(|()| Json::Null),
        other => panic!("unknown call {other}"),
    }
}

/// The storage root as `Path.resolve()` gives it.
fn sylvia_store_resolve(root: &Path) -> std::path::PathBuf {
    dunce_like(std::fs::canonicalize(root).unwrap())
}

fn dunce_like(path: std::path::PathBuf) -> std::path::PathBuf {
    let text = path.to_string_lossy();
    match text.strip_prefix(r"\\?\") {
        Some(rest) if !rest.starts_with("UNC\\") => std::path::PathBuf::from(rest),
        _ => path,
    }
}

fn base(root: &Path, record: &Json, name: &str) {
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(vectors(&format!("base-{name}.sqlite3.gz"))).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    std::fs::write(root.join("authority.sqlite3"), bytes).unwrap();
    write_files(root, &record["bases"][name]["files"]);
}

#[test]
fn detach_matches_python_on_every_case() {
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors("store_runs.json")).unwrap()).unwrap();
    let cases = record["detach"].as_array().unwrap();
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let root = common::work_folder(&format!("legacy-detach-{name}"));
        base(&root, &record, case["base"].as_str().unwrap());
        let store =
            AuthorityStore::open_for_application_owner(&root.join("authority.sqlite3"), None).unwrap();
        if let Some(after) = case.get("after").and_then(Json::as_str) {
            let first = cases.iter().find(|other| other["name"] == after).unwrap();
            for call in first["calls"].as_array().unwrap() {
                replay(&store, &root, call).unwrap_or_else(|error| panic!("{name}: {after}: {error:?}"));
            }
        }
        let calls = case["calls"].as_array().unwrap();
        if case.get("migration").is_some() {
            // The calls Python's migration made, which all succeeded.
            for call in calls {
                replay(&store, &root, call).unwrap_or_else(|error| panic!("{name}: {call}: {error:?}"));
            }
        } else {
            assert_eq!(calls.len(), 1, "{name}");
            sylvia_base::testing::assert_same_json(
                &outcome(replay(&store, &root, &calls[0])),
                &case["outcome"],
                format!("{name}: outcome"),
            );
        }
        drop(store);
        sylvia_base::testing::assert_same_json(
            &tables(&root.join("authority.sqlite3")),
            &case["tables"],
            format!("{name}: the session tables"),
        );
        sylvia_base::testing::assert_same_json(
            &tree(&root),
            &case["tree"],
            format!("{name}: the storage tree"),
        );
    }
}

#[test]
fn retain_legacy_workspace_matches_python_on_every_case() {
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors("store_runs.json")).unwrap()).unwrap();
    for (index, case) in record["workspace"].as_array().unwrap().iter().enumerate() {
        let name = case["name"].as_str().unwrap();
        let root = common::work_folder(&format!("legacy-workspace-{index}"));
        write_files(&root, &case["files"]);
        let (campaign_id, session_id) =
            (case["campaign_id"].as_str().unwrap(), case["session_id"].as_str().unwrap());
        for (attempt, expected) in case["outcomes"].as_array().unwrap().iter().enumerate() {
            let result = retain_legacy_workspace(&root, campaign_id, session_id).map(|path| {
                Json::String(
                    path.strip_prefix(sylvia_store_resolve(&root))
                        .unwrap()
                        .to_string_lossy()
                        .replace('\\', "/"),
                )
            });
            sylvia_base::testing::assert_same_json(
                &outcome(result),
                &*expected,
                format!("{name}: attempt {attempt}"),
            );
        }
        if !case["tree"].as_array().unwrap().is_empty() {
            sylvia_base::testing::assert_same_json(
                &tree(&root),
                &case["tree"],
                format!("{name}: the storage tree"),
            );
        }
    }
}
