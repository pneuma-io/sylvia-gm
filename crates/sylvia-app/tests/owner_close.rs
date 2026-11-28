//! The owner's shutdown, `sylvia_app::authority::owner_close::close_owner`:
//! - against Python (`tests/vectors/owner_close/owner_close_runs.json`, written by
//!   `generate_owner_close_runs.py`): a close during a live narration interrupts it once and
//!   discards its prose, a second close returns at once, and later operations are refused; each
//!   answer and cancel count compared, then the final database (interruption identities by order
//!   of appearance);
//! - on its own: a close waits for an operation still held, refuses (without counting) one tried
//!   while it drains, and finishes once the held one ends.

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;
use std::sync::{Arc, Mutex};
use sylvia_app::authority::actions::ActionError;
use sylvia_app::authority::narration_attempts::{self as narration, NarrationStart};
use sylvia_app::authority::owner_close::close_owner;
use sylvia_app::authority::turns;
use sylvia_base::testing::json_difference;
use sylvia_base::Json;
use sylvia_domain::authority::ProseChannel;
use sylvia_store::owner_lifecycle::OWNER_CLOSED;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle};

const CAMPAIGN: &str = "campaign";

fn gunzip(path: &Path) -> Vec<u8> {
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

/// The generator's failure. The closed owner's refusal is Python's uncaught `RuntimeError`, a
/// store fault here with Python's message (the owner's `internal_error` either way).
fn failure(error: ActionError) -> Json {
    let (class, code, message) = match error {
        ActionError::Domain(error) => (
            error.python_class().to_owned(),
            error.rejection().map(|coded| coded.code),
            error.message().to_owned(),
        ),
        ActionError::Store(StoreError::Coded(coded)) => {
            (coded.error_type.unwrap_or("CommandRejected").to_owned(), Some(coded.code), coded.message)
        }
        ActionError::Store(StoreError::Other(message)) if message == OWNER_CLOSED => {
            ("RuntimeError".to_owned(), None, message)
        }
        ActionError::Store(other) => ("RustStoreError".to_owned(), None, other.to_string()),
    };
    let authority = !matches!(class.as_str(), "ValueError" | "TypeError" | "RuntimeError");
    json!({"error": class, "code": if authority { code } else { None }, "message": message})
}

/// Every table's rows, sorted, with the interruption identities by order of appearance.
fn table_rows(path: &Path) -> BTreeMap<String, Vec<String>> {
    let connection = rusqlite::Connection::open(path).unwrap();
    let ids: Vec<String> = connection
        .prepare("SELECT interruption_id FROM narration_interruptions ORDER BY rowid")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .collect::<Result<_, _>>()
        .unwrap();
    let tables: Vec<String> = connection
        .prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .collect::<Result<_, _>>()
        .unwrap();
    tables
        .into_iter()
        .map(|table| {
            let mut statement = connection.prepare(&format!("SELECT * FROM \"{table}\"")).unwrap();
            let width = statement.column_count();
            let mut rows: Vec<String> = statement
                .query_map([], |row| {
                    let values: Vec<rusqlite::types::Value> =
                        (0..width).map(|index| row.get(index)).collect::<Result<_, _>>()?;
                    let mut text = format!("{values:?}");
                    for (index, id) in ids.iter().enumerate() {
                        text = text.replace(id.as_str(), &format!("interruption-#{index}"));
                    }
                    Ok(text)
                })
                .unwrap()
                .collect::<Result<_, _>>()
                .unwrap();
            rows.sort();
            (table, rows)
        })
        .collect()
}

fn work_folder(name: &str) -> std::path::PathBuf {
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join(name);
    if let Err(error) = std::fs::remove_dir_all(&folder) {
        assert_eq!(error.kind(), std::io::ErrorKind::NotFound, "cannot clear {}: {error}", folder.display());
    }
    std::fs::create_dir_all(&folder).unwrap();
    folder
}

type Cancels = Arc<Mutex<BTreeMap<String, usize>>>;

async fn run(store: &StoreHandle, step: &Json, cancels: &Cancels) -> Result<Json, ActionError> {
    let attempt = step["attempt"].as_str().unwrap_or_default().to_owned();
    Ok(match step["op"].as_str().unwrap() {
        "start" => {
            cancels.lock().unwrap().entry(attempt.clone()).or_insert(0);
            let (counts, key) = (Arc::clone(cancels), attempt.clone());
            narration::start_narration_attempt(
                store,
                NarrationStart {
                    campaign_id: CAMPAIGN.into(),
                    attempt_id: attempt,
                    turn_id: "turn".into(),
                    cancel: Box::new(move || *counts.lock().unwrap().get_mut(&key).unwrap() += 1),
                    last_action_id: None,
                    interruption_id: None,
                },
            )
            .await?;
            Json::Null
        }
        "emit" => {
            let token = step["token"].as_str().unwrap();
            narration::emit_narration_token(store, CAMPAIGN, &attempt, ProseChannel::Talk, token, None)
                .await?;
            Json::Null
        }
        "complete" => narration::complete_narration_attempt(store, CAMPAIGN, &attempt).await?.len().into(),
        "close" => {
            close_owner(store).await?;
            Json::Null
        }
        "status" => narration::narration_attempt_status(store, CAMPAIGN, &attempt).await?.into(),
        "current_open_turn" => {
            turns::current_open_turn(store, CAMPAIGN).await?.map_or(Json::Null, Json::from)
        }
        other => panic!("unknown step {other}"),
    })
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn closing_interrupts_live_narration_as_pythons_owner() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/owner_close");
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors.join("owner_close_runs.json")).unwrap())
            .unwrap();
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 10);
    let folder = work_folder("owner-close");
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip(&vectors.join("base.sqlite3.gz"))).unwrap();
    let path = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    handle.narration().set_disconnect_grace(5.0).unwrap();
    let cancels: Cancels = Arc::default();
    let mut failures = Vec::new();
    for (index, item) in runs.iter().enumerate() {
        let got = match run(&handle, &item["step"], &cancels).await {
            Ok(value) => json!({"ok": value}),
            Err(error) => failure(error),
        };
        let counts = cancels.lock().unwrap().clone();
        let got = json!({"outcome": got, "cancels": counts});
        let want = json!({"outcome": item["outcome"], "cancels": item["cancels"]});
        if let Some(difference) = json_difference(&got, &want) {
            failures.push(format!("#{index} {}: {difference}", item["step"]));
        }
    }
    assert!(handle.narration().with(|inner| inner.attempts.is_empty()), "the closed owner keeps no attempt");
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip(&vectors.join("final.sqlite3.gz"))).unwrap();
    let (rust_rows, python_rows) = (table_rows(&database), table_rows(&python));
    if rust_rows.keys().ne(python_rows.keys()) {
        failures.push("the final tables differ from Python's".to_owned());
    }
    for (table, rows) in &python_rows {
        if rust_rows.get(table) != Some(rows) {
            failures.push(format!("the final {table} rows differ from Python's"));
        }
    }
    assert!(failures.is_empty(), "{} close steps differ:\n{}", failures.len(), failures.join("\n"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_close_waits_for_a_held_operation_and_refuses_new_ones() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/owner_close");
    let folder = work_folder("owner-close-held");
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip(&vectors.join("base.sqlite3.gz"))).unwrap();
    let path = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    // A turn still running: it holds its operation across awaits.
    let held = handle.operation().unwrap();
    let closing = {
        let handle = handle.clone();
        tokio::spawn(async move { close_owner(&handle).await })
    };
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    assert!(!closing.is_finished(), "the close waits for the held operation");
    // Python's `_operation()` during the drain: refused and not counted, so the close still
    // finishes when the held operation ends.
    let refused = turns::current_open_turn(&handle, CAMPAIGN).await.unwrap_err();
    assert_eq!(refused.to_string(), OWNER_CLOSED);
    // The held operation still works while the owner drains.
    assert_eq!(
        handle.call(|store| store.current_open_turn(CAMPAIGN)).await.unwrap().as_deref(),
        Some("turn")
    );
    drop(held);
    tokio::time::timeout(std::time::Duration::from_secs(10), closing).await.unwrap().unwrap().unwrap();
    assert_eq!(handle.operation().unwrap_err().to_string(), OWNER_CLOSED);
    assert!(handle.call(|store| store.current_open_turn(CAMPAIGN)).await.is_err(), "the store is closed");
    close_owner(&handle).await.unwrap();
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
}
