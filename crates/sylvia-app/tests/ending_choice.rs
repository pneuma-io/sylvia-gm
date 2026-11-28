//! A player's ending button against Python (`tests/vectors/ending_choice/ending_choice_runs.json`,
//! written by `generate_ending_choice_runs.py`): two scenarios, each on its own base, every step of
//! Python's `campaign_ending_choice_text` replayed through
//! `sylvia_app::authority::narration_attempts::campaign_ending_choice_text`, each answer compared
//! with `json_difference`, then the final database row for row.

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::actions::ActionError;
use sylvia_app::authority::narration_attempts::campaign_ending_choice_text;
use sylvia_base::testing::json_difference;
use sylvia_base::Json;
use sylvia_domain::session::SessionMessageKind;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle};

fn gunzip(path: &Path) -> Vec<u8> {
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

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
        ActionError::Store(other) => ("RustStoreError".to_owned(), None, other.to_string()),
    };
    let authority = !matches!(class.as_str(), "ValueError" | "TypeError" | "KeyError" | "AttributeError");
    json!({"error": class, "code": if authority { code } else { None }, "message": message})
}

fn table_rows(path: &Path) -> BTreeMap<String, Vec<String>> {
    let connection = rusqlite::Connection::open(path).unwrap();
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
                    Ok(format!("{values:?}"))
                })
                .unwrap()
                .collect::<Result<_, _>>()
                .unwrap();
            rows.sort();
            (table, rows)
        })
        .collect()
}

async fn run(store: &StoreHandle, step: &Json) -> Json {
    let text = |key: &str| step[key].as_str().unwrap().to_owned();
    if step["op"] == "choice" {
        let choice = step["choice"].as_object().unwrap();
        return match campaign_ending_choice_text(store, &text("session"), &text("message"), choice).await {
            Ok(value) => json!({"ok": value}),
            Err(error) => failure(error),
        };
    }
    let kind = SessionMessageKind::parse(step["kind"].as_str().unwrap()).unwrap();
    match store
        .append_agent_message(&text("session"), &text("message"), kind, step["content"].as_object().unwrap())
        .await
    {
        Ok(message) => json!({"ok": {"message_id": message.message_id(), "sequence": message.sequence()}}),
        Err(error) => failure(ActionError::Store(error)),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn ending_choices_answer_as_pythons_owner() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/ending_choice");
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors.join("ending_choice_runs.json")).unwrap())
            .unwrap();
    let scenarios = record["scenarios"].as_array().unwrap();
    assert_eq!(scenarios.len(), 2);
    let mut failures = Vec::new();
    for scenario in scenarios {
        let name = scenario["name"].as_str().unwrap();
        let folder =
            Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join(format!("ending-choice-{name}"));
        if let Err(error) = std::fs::remove_dir_all(&folder) {
            assert_eq!(
                error.kind(),
                std::io::ErrorKind::NotFound,
                "cannot clear {}: {error}",
                folder.display()
            );
        }
        std::fs::create_dir_all(&folder).unwrap();
        let database = folder.join("authority.sqlite3");
        std::fs::write(&database, gunzip(&vectors.join(name).join("base.sqlite3.gz"))).unwrap();
        let path = database.clone();
        let (handle, thread) =
            StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
        let runs = scenario["runs"].as_array().unwrap();
        assert_eq!(runs.len(), 14, "{name}'s step count");
        for (index, item) in runs.iter().enumerate() {
            let got = run(&handle, &item["step"]).await;
            if let Some(difference) = json_difference(&got, &item["outcome"]) {
                failures.push(format!("{name} #{index} {}: {difference}", item["step"]));
            }
        }
        drop(handle);
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
        let python = folder.join("python.sqlite3");
        std::fs::write(&python, gunzip(&vectors.join(name).join("final.sqlite3.gz"))).unwrap();
        let (rust_rows, python_rows) = (table_rows(&database), table_rows(&python));
        if rust_rows.keys().ne(python_rows.keys()) {
            failures.push(format!("{name}: the final tables differ from Python's"));
        }
        for (table, rows) in &python_rows {
            if rust_rows.get(table) != Some(rows) {
                failures.push(format!("{name}: the final {table} rows differ from Python's"));
            }
        }
    }
    assert!(failures.is_empty(), "{} ending steps differ:\n{}", failures.len(), failures.join("\n"));
}
