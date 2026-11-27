//! A player's panel control against Python (`tests/vectors/panel_action/panel_action_runs.json`,
//! written by `generate_panel_action_runs.py`): six scenarios (the synthetic coordinate-map
//! control plain, confirmed and with a choice, an exact coordinate map, one with a declared actor
//! reference, and a party control with a player control scope), each on its own base, with every
//! step of Python's `player_action` replayed through
//! `sylvia_app::authority::direct_actions::player_action` and the final database compared row for
//! row.

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::actions::ActionError;
use sylvia_app::authority::direct_actions::{player_action, PlayerActionAnswer, PlayerActionRequest};
use sylvia_base::Json;
use sylvia_domain::authority::MechanicsChoiceRequest;
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
        other => {
            let coded = other.into_coded();
            json!({"error": coded.error_type.unwrap_or("CommandRejected"), "code": coded.code, "message": coded.message})
        }
    }
}

fn choice(choice: &MechanicsChoiceRequest) -> Json {
    let options: Vec<Json> = choice
        .options
        .iter()
        .map(|option| json!({"__class__": "MechanicsChoiceOption", "option_id": option.option_id, "label": option.label}))
        .collect();
    json!({"__class__": "MechanicsChoiceRequest", "choice_id": choice.choice_id, "prompt": choice.prompt, "options": options})
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
            "choice": choice(&receipt.choice),
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
async fn panel_controls_answer_and_store_as_pythons_owner() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/panel_action");
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors.join("panel_action_runs.json")).unwrap())
            .unwrap();
    let owner_id = record["owner_id"].as_str().unwrap().to_owned();
    let scenarios = record["scenarios"].as_array().unwrap();
    assert_eq!(scenarios.len(), 6);
    let mut failures = Vec::new();
    for scenario in scenarios {
        let name = scenario["name"].as_str().unwrap();
        let folder =
            Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join(format!("panel-action-{name}"));
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
        for (index, run) in scenario["runs"].as_array().unwrap().iter().enumerate() {
            let got = match player_action(&handle, &owner_id, &request(&run["request"])).await {
                Ok(result) => answer(result),
                Err(error) => failure(error),
            };
            let (got, want) = (got.to_string(), run["outcome"].to_string());
            if got != want {
                failures.push(format!("{name} #{index} {}\n  Rust   {got}\n  Python {want}", run["name"]));
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
    assert!(failures.is_empty(), "{} panel steps differ:\n{}", failures.len(), failures.join("\n"));
}
