//! Game Master tool actions against Python (`tests/vectors/action_execution/
//! action_execution_runs.json`, written by `generate_action_execution_runs.py`): every step of
//! Python's `execute_action` on the Riichi game in play, replayed through
//! `sylvia_app::authority::actions::execute_action` in order. Each receipt or refusal must be
//! Python's (a `MechanicsRejected` with its details), and the final database must be Python's,
//! row for row.

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::actions::{execute_action, ActionError, ActionExecution};
use sylvia_base::Json;
use sylvia_domain::authority::{ActionCommand, WriterRole};
use sylvia_domain::DomainError;
use sylvia_store::{AuthorityStore, StoreHandle};

fn gunzip(path: &Path) -> Vec<u8> {
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

fn command(value: &Json) -> ActionCommand {
    let text = |key: &str| value[key].as_str().unwrap().to_owned();
    ActionCommand {
        campaign_id: text("campaign_id"),
        action_id: text("action_id"),
        idempotency_key: text("idempotency_key"),
        owner_id: text("owner_id"),
        writer: WriterRole::parse(value["writer"].as_str().unwrap()).unwrap(),
        expected_revision: value["expected_revision"].as_i64().unwrap(),
        turn_id: text("turn_id"),
        package_id: text("package_id"),
        tool_name: text("tool_name"),
        parameters: value["parameters"].as_object().unwrap().clone(),
        initiating_session_id: None,
    }
}

/// The generator's `failure`: class, code and message, with a `MechanicsRejected`'s details.
fn failure(error: ActionError) -> Json {
    match error {
        ActionError::Domain(DomainError::Rejected { error_type, code, message, mechanics, .. }) => {
            let mut out = json!({"error": error_type, "code": code, "message": message});
            if let Some(details) = mechanics {
                let issues: Vec<Json> =
                    details.validation_issues.iter().map(|issue| Json::Object(issue.as_payload())).collect();
                out["mechanics"] = json!({
                    "field_paths": details.field_paths,
                    "validation_issues": issues,
                    "state_changed": details.state_changed,
                    "refusal_reasons": details.refusal_reasons,
                });
            }
            out
        }
        other => {
            let coded = other.into_coded();
            json!({"error": coded.error_type.unwrap_or("CommandRejected"), "code": coded.code, "message": coded.message})
        }
    }
}

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
        ActionExecution::AwaitingChoice(_) => json!({"ok": "AwaitingChoiceReceipt"}),
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
async fn actions_execute_as_pythons_owner_executes_them() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors");
    let record: Json = serde_json::from_str(
        &std::fs::read_to_string(vectors.join("action_execution/action_execution_runs.json")).unwrap(),
    )
    .unwrap();
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 24);
    let owner_id = record["owner_id"].as_str().unwrap().to_owned();
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join("action-execution");
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
    std::fs::write(&database, gunzip(&vectors.join("snapshots/riichi_started.sqlite3.gz"))).unwrap();
    let path = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    // Each move's shuffles leave their collection in order, as the generator's do. This file is a
    // binary of its own, so the switch reaches only this test.
    sylvia_engine::exec::handlers::set_identity_shuffle_everywhere_for_vectors(true);
    let mut failures = Vec::new();
    for (index, run) in runs.iter().enumerate() {
        let got = match execute_action(&handle, &owner_id, &command(&run["command"])).await {
            Ok(execution) => receipt(execution),
            Err(error) => failure(error),
        };
        let (got, want) = (got.to_string(), run["outcome"].to_string());
        if got != want {
            failures.push(format!("#{index} {}\n  Rust   {got}\n  Python {want}", run["name"]));
        }
    }
    sylvia_engine::exec::handlers::set_identity_shuffle_everywhere_for_vectors(false);
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip(&vectors.join("action_execution/final.sqlite3.gz"))).unwrap();
    let (rust_rows, python_rows) = (table_rows(&database), table_rows(&python));
    for (table, rows) in &python_rows {
        if rust_rows.get(table) != Some(rows) {
            failures.push(format!("the final {table} rows differ from Python's"));
        }
    }
    assert!(failures.is_empty(), "{} action steps differ:\n{}", failures.len(), failures.join("\n"));
}
