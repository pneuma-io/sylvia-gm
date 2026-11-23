//! Mechanics choices against Python (`tests/vectors/choice_resolution/
//! choice_resolution_runs.json`, written by `generate_choice_resolution_runs.py`): every step of
//! Python's `execute_action` and `resolve_mechanics_choice` on the F3 clockwork package, a
//! one-choice campaign and a two-choice one, replayed through
//! `sylvia_app::authority::actions::{execute_action, resolve_mechanics_choice}` in order. Each
//! receipt, awaiting-choice receipt or refusal must be Python's (with the generator's one direct
//! SQL step, a second transport key, run the same way), and the final database must be
//! Python's, row for row.

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::actions::{
    execute_action, resolve_mechanics_choice, ActionError, ActionExecution,
};
use sylvia_base::Json;
use sylvia_domain::authority::{
    ActionCommand, MechanicsChoiceRequest, ResolveMechanicsChoiceCommand, WriterRole,
};
use sylvia_domain::DomainError;
use sylvia_store::{AuthorityStore, StoreHandle};

fn gunzip(path: &Path) -> Vec<u8> {
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

fn text(value: &Json, key: &str) -> String {
    value[key].as_str().unwrap().to_owned()
}

fn action(value: &Json) -> ActionCommand {
    ActionCommand {
        campaign_id: text(value, "campaign_id"),
        action_id: text(value, "action_id"),
        idempotency_key: text(value, "idempotency_key"),
        owner_id: text(value, "owner_id"),
        writer: WriterRole::parse(value["writer"].as_str().unwrap()).unwrap(),
        expected_revision: value["expected_revision"].as_i64().unwrap(),
        turn_id: text(value, "turn_id"),
        package_id: text(value, "package_id"),
        tool_name: text(value, "tool_name"),
        parameters: value["parameters"].as_object().unwrap().clone(),
        initiating_session_id: None,
    }
}

fn resolution(value: &Json) -> ResolveMechanicsChoiceCommand {
    ResolveMechanicsChoiceCommand {
        campaign_id: text(value, "campaign_id"),
        action_id: text(value, "action_id"),
        resolution_id: text(value, "resolution_id"),
        owner_id: text(value, "owner_id"),
        expected_revision: value["expected_revision"].as_i64().unwrap(),
        choice_id: text(value, "choice_id"),
        option_id: text(value, "option_id"),
        session_id: value["session_id"].as_str().map(str::to_owned),
        choice_sequence: value["choice_sequence"].as_i64().unwrap(),
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

fn request(choice: &MechanicsChoiceRequest) -> Json {
    let options: Vec<Json> = choice
        .options
        .iter()
        .map(|option| {
            json!({"__class__": "MechanicsChoiceOption", "option_id": option.option_id, "label": option.label})
        })
        .collect();
    json!({"__class__": "MechanicsChoiceRequest", "choice_id": choice.choice_id, "prompt": choice.prompt, "options": options})
}

/// The generator's `plain` of a receipt.
fn receipt(execution: ActionExecution) -> Json {
    match execution {
        ActionExecution::Receipt(receipt) => json!({"ok": {
            "__class__": "ActionReceipt",
            "campaign_id": receipt.campaign_id,
            "action_id": receipt.action_id,
            "status": receipt.status.as_str(),
            "result": Json::Object(receipt.result),
            "resulting_revision": receipt.resulting_revision,
        }}),
        ActionExecution::AwaitingChoice(receipt) => json!({"ok": {
            "__class__": "AwaitingChoiceReceipt",
            "campaign_id": receipt.campaign_id,
            "action_id": receipt.action_id,
            "basis_revision": receipt.basis_revision,
            "turn_id": receipt.turn_id,
            "choice": request(&receipt.choice),
            "choice_sequence": receipt.choice_sequence,
        }}),
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
async fn mechanics_choices_resolve_as_pythons_owner_resolves_them() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/choice_resolution");
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors.join("choice_resolution_runs.json")).unwrap())
            .unwrap();
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 27);
    let owner_id = record["owner_id"].as_str().unwrap().to_owned();
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join("choice-resolution");
    if let Err(error) = std::fs::remove_dir_all(&folder) {
        assert_eq!(
            error.kind(),
            std::io::ErrorKind::NotFound,
            "cannot clear the old work folder {}: {error}",
            folder.display()
        );
    }
    std::fs::create_dir_all(&folder).unwrap();
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip(&vectors.join("base.sqlite3.gz"))).unwrap();
    let path = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    let mut failures = Vec::new();
    for (index, run) in runs.iter().enumerate() {
        let command = &run["command"];
        if run["kind"] == "sql" {
            // The generator's second transport key, written beside the open store as Python's is.
            let parameters: Vec<String> = command["parameters"]
                .as_array()
                .unwrap()
                .iter()
                .map(|value| value.as_str().unwrap().into())
                .collect();
            let connection = rusqlite::Connection::open(&database).unwrap();
            connection
                .execute(command["statement"].as_str().unwrap(), rusqlite::params_from_iter(parameters))
                .unwrap();
            continue;
        }
        let outcome = match run["kind"].as_str().unwrap() {
            "act" => execute_action(&handle, &owner_id, &action(command)).await,
            "resolve" => resolve_mechanics_choice(&handle, &owner_id, &resolution(command)).await,
            kind => panic!("unknown step kind {kind}"),
        };
        let got = match outcome {
            Ok(execution) => receipt(execution),
            Err(error) => failure(error),
        };
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
    for (table, rows) in &python_rows {
        if rust_rows.get(table) != Some(rows) {
            failures.push(format!("the final {table} rows differ from Python's"));
        }
    }
    if rust_rows.len() != python_rows.len() {
        failures.push(format!("Rust has tables {:?}, Python {:?}", rust_rows.keys(), python_rows.keys()));
    }
    assert!(failures.is_empty(), "{} choice steps differ:\n{}", failures.len(), failures.join("\n"));
}
