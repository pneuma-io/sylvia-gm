//! The GM's Campaign File against Python (`tests/vectors/campaign_file/`, written by
//! `generate_campaign_file_runs.py`): every step of Python's `update_campaign_file`, replayed
//! through `sylvia_app::authority::campaign_file::update_campaign_file` in order.
//!
//! - `campaign_file_runs.json` on its committed base: a Plan-less and a Plan campaign, each step's
//!   answer or refusal, then the final database row for row.
//! - `campaign_file_srd_runs.json` on a base built here ([`installed_base`]): character
//!   bookkeeping through the bundled SRD Lite package, each step, then each table it changed.

mod installed_base;

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::BTreeMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use sylvia_app::authority::actions::ActionError;
use sylvia_app::authority::campaign_file::{update_campaign_file, CampaignFileUpdate};
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_base::Json;
use sylvia_domain::DomainError;
use sylvia_store::{AuthorityStore, StoreHandle, StoreThread};

fn gunzip(path: &Path) -> Vec<u8> {
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

fn vectors() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/campaign_file")
}

fn record(name: &str) -> Json {
    serde_json::from_str(&std::fs::read_to_string(vectors().join(name)).unwrap()).unwrap()
}

fn work_folder(name: &str) -> PathBuf {
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join(name);
    if let Err(error) = std::fs::remove_dir_all(&folder) {
        assert_eq!(error.kind(), std::io::ErrorKind::NotFound, "cannot clear {}: {error}", folder.display());
    }
    std::fs::create_dir_all(&folder).unwrap();
    folder
}

fn open(path: &Path) -> (StoreHandle, StoreThread) {
    let path = path.to_path_buf();
    StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap()
}

fn update(value: &Json) -> CampaignFileUpdate {
    CampaignFileUpdate {
        session_id: value["session_id"].as_str().unwrap().to_owned(),
        update_id: value["update_id"].as_str().unwrap().to_owned(),
        updates: value["updates"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item.as_object().unwrap().clone())
            .collect(),
        basis_message_ids: value["basis_message_ids"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item.as_str().unwrap().to_owned())
            .collect(),
        turn_id: value["turn_id"].as_str().map(str::to_owned),
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
            if coded.code == "internal_error" {
                return json!({"uncaught": coded.message});
            }
            json!({"error": coded.error_type.unwrap_or("CommandRejected"), "code": coded.code, "message": coded.message})
        }
    }
}

/// Python's recorded failure as the owner treats it: a class it does not catch (anything but
/// `ValueError` and its subclasses, or the `AuthorityError` family) is its internal error.
fn expected(outcome: &Json) -> Json {
    let caught = [
        "ValueError",
        "JSONDecodeError",
        "AuthorityError",
        "SchemaIdentityError",
        "OwnerConflict",
        "WrongOwner",
        "WrongWriter",
        "StaleRevision",
        "IdempotencyConflict",
        "CommandRejected",
        "MechanicsRejected",
        "RulesetPreconditionRejected",
    ];
    match outcome["error"].as_str() {
        Some(class) if !caught.contains(&class) => json!({"uncaught": outcome["message"]}),
        _ => outcome.clone(),
    }
}

async fn replay(
    handle: &StoreHandle,
    database: &Path,
    owner_id: &str,
    runs: &[Json],
    failures: &mut Vec<String>,
) {
    for (index, run) in runs.iter().enumerate() {
        if run["kind"] == "sql" {
            // The generator's direct writes beside the open store, run the same way.
            let command = &run["command"];
            let parameters: Vec<String> = command["parameters"]
                .as_array()
                .unwrap()
                .iter()
                .map(|value| value.as_str().unwrap().into())
                .collect();
            let connection = rusqlite::Connection::open(database).unwrap();
            connection
                .execute(command["statement"].as_str().unwrap(), rusqlite::params_from_iter(parameters))
                .unwrap();
            continue;
        }
        let got = match update_campaign_file(handle, owner_id, &update(&run["command"])).await {
            Ok(answer) => json!({"ok": Json::Object(answer)}),
            Err(error) => failure(error),
        };
        let (got, want) = (got.to_string(), expected(&run["outcome"]).to_string());
        if got != want {
            failures.push(format!("#{index} {}\n  Rust   {got}\n  Python {want}", run["name"]));
        }
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
async fn campaign_file_updates_answer_and_store_as_pythons_owner() {
    let record = record("campaign_file_runs.json");
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 29);
    let folder = work_folder("campaign-file");
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip(&vectors().join("base.sqlite3.gz"))).unwrap();
    let (handle, thread) = open(&database);
    let mut failures = Vec::new();
    replay(&handle, &database, record["owner_id"].as_str().unwrap(), runs, &mut failures).await;
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip(&vectors().join("final.sqlite3.gz"))).unwrap();
    let (rust_rows, python_rows) = (table_rows(&database), table_rows(&python));
    for (table, rows) in &python_rows {
        if rust_rows.get(table) != Some(rows) {
            failures.push(format!("the final {table} rows differ from Python's"));
        }
    }
    assert!(failures.is_empty(), "{} Campaign File steps differ:\n{}", failures.len(), failures.join("\n"));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn character_bookkeeping_commits_with_its_notes_as_pythons_owner() {
    let record = record("campaign_file_srd_runs.json");
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 6);
    let folder = work_folder("campaign-file-srd");
    let database = folder.join("authority.sqlite3");
    installed_base::seed_copy(&database);
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let (handle, thread) = open(&database);
    installed_base::install(
        &handle,
        bundled.bundled_srd_archive().unwrap(),
        record["install_id"].as_str().unwrap(),
    )
    .await;
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let mismatched =
        installed_base::check_digests(&database, record["installed_table_digests"].as_object().unwrap());
    assert!(mismatched.is_empty(), "the Rust install differs from Python's:\n{}", mismatched.join("\n"));
    installed_base::write_rows(&database, record["setup_tables"].as_object().unwrap());
    let base = folder.join("base.sqlite3");
    std::fs::copy(&database, &base).unwrap();
    let (handle, thread) = open(&database);
    let mut failures = Vec::new();
    replay(&handle, &database, record["owner_id"].as_str().unwrap(), runs, &mut failures).await;
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    failures.extend(installed_base::changed_tables_differ(
        &database,
        &base,
        record["final_changed_tables"].as_object().unwrap(),
    ));
    assert!(failures.is_empty(), "{} bookkeeping steps differ:\n{}", failures.len(), failures.join("\n"));
}
