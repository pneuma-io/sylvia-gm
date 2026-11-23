//! Launch on profiles with and without former campaign-creation content chats
//! (`vectors/generate_legacy_builder_profiles.py`, built with Python's own test set-up).
//!
//! The owner migrates a profile that has them at launch (lane 1's `migrate_legacy_builder_chats`
//! against the campaign creation catalog), and starts every other profile as before. The read
//! that finds them must find exactly what Python's `legacy_builder_contexts()` finds, and must
//! not create session tables in a fresh database. The migration's own results are proven in
//! `creation_catalog_vectors`.

use flate2::read::GzDecoder;
use serde_json::{json, Value as Json};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use sylvia_store::AuthorityStore;

fn vectors() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/legacy_builder")
}

/// A fresh work folder; an old one that cannot be removed fails here, with its path.
fn folder(name: &str) -> PathBuf {
    let root = std::env::temp_dir().join(format!("sylvia-legacy-builder-{name}-{}", std::process::id()));
    if root.exists() {
        std::fs::remove_dir_all(&root)
            .unwrap_or_else(|error| panic!("cannot clear {}: {error}", root.display()));
    }
    std::fs::create_dir_all(&root).unwrap();
    root
}

fn profile(name: &str, database: &Path) {
    let mut bytes = Vec::new();
    let file = std::fs::File::open(vectors().join(format!("{name}.sqlite3.gz"))).unwrap();
    GzDecoder::new(file).read_to_end(&mut bytes).unwrap();
    std::fs::write(database, bytes).unwrap();
}

fn legacy_ids(database: &Path) -> Vec<String> {
    let store = AuthorityStore::open_for_application_owner(database, None).unwrap();
    store
        .legacy_builder_contexts()
        .unwrap()
        .iter()
        .map(|context| context.session.session_id().to_string())
        .collect()
}

/// Launch the built owner on `database`: its first stdout line, its stderr and whether it
/// exited successfully after stdin closed.
fn launch(database: &Path) -> (String, String, bool) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_sylvia-gm-owner"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("the owner starts");
    let mut stdin = child.stdin.take().unwrap();
    let envelope = json!({
        "type": "launch", "secret_hex": "00".repeat(32),
        "database_path": database, "owner_id": "legacy-builder-test",
    });
    stdin.write_all(format!("{envelope}\n").as_bytes()).unwrap();
    stdin.flush().unwrap();
    let mut stdout = BufReader::new(child.stdout.take().unwrap());
    let mut first = String::new();
    stdout.read_line(&mut first).unwrap();
    drop(stdin);
    let mut stderr = String::new();
    child.stderr.take().unwrap().read_to_string(&mut stderr).unwrap();
    let status = child.wait().unwrap();
    (first, stderr, status.success())
}

#[test]
fn the_read_finds_what_python_finds_and_launch_migrates_those_profiles() {
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors().join("profiles.json")).unwrap()).unwrap();
    for (name, expected) in record["profiles"].as_object().unwrap() {
        let root = folder(name);
        let database = root.join("authority.sqlite3");
        profile(name, &database);
        let python: Vec<String> =
            serde_json::from_value(expected["legacy_builder_contexts"].clone()).unwrap();
        assert_eq!(legacy_ids(&database), python, "{name}: the chats Python's read finds");

        let (first, stderr, _) = launch(&database);
        assert!(first.contains(r#""type":"ready""#), "{name}: starts: {first} {stderr}");
        // A migrated profile holds no former content chats, and starts again as any other.
        assert_eq!(legacy_ids(&database), Vec::<String>::new(), "{name}: the chats are migrated");
        if !python.is_empty() {
            let (again, stderr, _) = launch(&database);
            assert!(again.contains(r#""type":"ready""#), "{name}: starts again: {again} {stderr}");
        }
        let _ = std::fs::remove_dir_all(&root);
    }
}

#[test]
fn the_read_creates_no_session_tables_in_a_fresh_database() {
    let root = folder("fresh");
    let database = root.join("authority.sqlite3");
    let store = AuthorityStore::open_for_application_owner(&database, None).unwrap();
    let schema = |database: &Path| {
        let connection = rusqlite::Connection::open(database).unwrap();
        let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0)).unwrap();
        let sessions: i64 = connection
            .query_row("SELECT count(*) FROM sqlite_master WHERE name = 'agent_sessions'", [], |row| {
                row.get(0)
            })
            .unwrap();
        (version, sessions)
    };
    let before = schema(&database);
    assert!(store.legacy_builder_contexts().unwrap().is_empty());
    assert_eq!(schema(&database), before);
    assert_eq!(before.1, 0, "no session tables");
    drop(store);
    let (first, stderr, _) = launch(&database);
    assert!(first.contains(r#""type":"ready""#), "a fresh profile starts: {first} {stderr}");
    let _ = std::fs::remove_dir_all(&root);
}
