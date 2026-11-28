//! A player's click on a saved result card's button against Python
//! (`tests/vectors/card_action/card_action_runs.json`, written by
//! `generate_card_action_runs.py`): every step of Python's `player_action` with a `result_card`
//! source, replayed through `sylvia_app::authority::direct_actions::player_action` in order
//! (one while the GM session holds campaign work), then the final database row for row.

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::actions::ActionError;
use sylvia_app::authority::direct_actions::{player_action, PlayerActionAnswer, PlayerActionRequest};
use sylvia_base::Json;
use sylvia_domain::DomainError;
use sylvia_store::{AuthorityStore, StoreHandle};

fn gunzip(path: &Path) -> Vec<u8> {
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

fn request(value: &Json) -> PlayerActionRequest {
    PlayerActionRequest {
        campaign_id: value["campaign_id"].as_str().unwrap().to_owned(),
        source: value["source"].clone(),
        operation_id: value["operation_id"].as_str().unwrap().to_owned(),
        expected_revision: value["expected_revision"].as_i64().unwrap(),
        selection: value["selection"].clone(),
        session_id: value["session_id"].as_str().map(str::to_owned),
        confirmation: value["confirmation"].as_str().map(str::to_owned),
        preview_only: value["preview_only"].as_bool().unwrap(),
    }
}

/// The generator's `failure`: class, code and message.
fn failure(error: ActionError) -> Json {
    match error {
        ActionError::Domain(DomainError::Rejected { error_type, code, message, .. }) => {
            json!({"error": error_type, "code": code, "message": message})
        }
        // An exception Python's owner does not catch: no code of its own.
        ActionError::Domain(DomainError::Uncaught { error_type, message }) => {
            json!({"error": error_type, "code": null, "message": message})
        }
        other => {
            let coded = other.into_coded();
            json!({"error": coded.error_type.unwrap_or("CommandRejected"), "code": coded.code, "message": coded.message})
        }
    }
}

/// The generator's `plain` of an answer.
fn answer(answer: PlayerActionAnswer) -> Json {
    match answer {
        PlayerActionAnswer::Receipt(receipt) => json!({"ok": {
            "__class__": "ActionReceipt",
            "campaign_id": receipt.campaign_id,
            "action_id": receipt.action_id,
            "status": receipt.status.as_str(),
            "result": Json::Object(receipt.result),
            "resulting_revision": receipt.resulting_revision,
        }}),
        PlayerActionAnswer::AwaitingChoice(receipt) => json!({"ok": {
            "__class__": "AwaitingChoiceReceipt",
            "campaign_id": receipt.campaign_id,
            "action_id": receipt.action_id,
            "basis_revision": receipt.basis_revision,
            "turn_id": receipt.turn_id,
            "choice_sequence": receipt.choice_sequence,
        }}),
        PlayerActionAnswer::Preview(preview) => json!({"ok": Json::Object(preview)}),
    }
}

/// Every table's rows, sorted, for comparing a database with Python's.
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

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn card_clicks_answer_and_store_as_pythons_owner() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/card_action");
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors.join("card_action_runs.json")).unwrap())
            .unwrap();
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 12);
    let owner_id = record["owner_id"].as_str().unwrap().to_owned();
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join("card-action");
    if let Err(error) = std::fs::remove_dir_all(&folder) {
        assert_eq!(error.kind(), std::io::ErrorKind::NotFound, "cannot clear {}: {error}", folder.display());
    }
    std::fs::create_dir_all(&folder).unwrap();
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip(&vectors.join("base.sqlite3.gz"))).unwrap();
    let path = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    let mut failures = Vec::new();
    for (index, run) in runs.iter().enumerate() {
        // `with owner.agent_session_work(session)`: the GM session's campaign work held around it.
        let hold = match run["holding_session"].as_str() {
            Some(session) => Some(handle.session_campaign_work(session).await.unwrap()),
            None => None,
        };
        let got = match player_action(&handle, &owner_id, &request(&run["request"])).await {
            Ok(result) => answer(result),
            Err(error) => failure(error),
        };
        drop(hold);
        let (got, want) = (got.to_string(), run["outcome"].to_string());
        if got != want {
            failures.push(format!("#{index} {}\n  Rust   {got}\n  Python {want}", run["name"]));
        }
    }
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
    assert!(failures.is_empty(), "{} card steps differ:\n{}", failures.len(), failures.join("\n"));
}
