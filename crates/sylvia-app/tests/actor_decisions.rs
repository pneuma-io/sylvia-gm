//! An isolated participant's decision against Python's owner
//! (`tests/vectors/actor_decisions/actor_decision_runs.json`, written by
//! `generate_actor_decision_runs.py` on Python's own synthetic hidden-token Ruleset): each step
//! replayed through `sylvia_app::authority::actor_decisions` and `actions::execute_action`, each
//! answer compared with `json_difference`, then the final database row for row.

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::{BTreeMap, HashMap};
use std::io::Read;
use std::path::Path;
use sylvia_app::actor_decisions::ActorDecisionRequest;
use sylvia_app::authority::actions::{execute_action, ActionError, ActionExecution};
use sylvia_app::authority::actor_decisions::{actor_decision_command, actor_decision_request};
use sylvia_base::testing::json_difference;
use sylvia_base::Json;
use sylvia_domain::authority::ActionCommand;
use sylvia_domain::DomainError;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle};

const OWNER: &str = "isolated-test";
const CAMPAIGN: &str = "tokens";
const SESSION: &str = "gm-tokens";

fn gunzip(path: &Path) -> Vec<u8> {
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

fn failure(error: ActionError) -> Json {
    let (class, code, message) = match error {
        ActionError::Domain(DomainError::Uncaught { error_type, message }) => {
            (error_type.to_owned(), None, message)
        }
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

/// `ActorDecisionRequest`'s fields in order, as the generator's `plain` writes a dataclass.
fn request_json(request: &ActorDecisionRequest) -> Json {
    let actions: Vec<Json> = request
        .actions
        .iter()
        .map(|action| {
            json!({"identity": action.identity, "label": action.label, "operation": action.operation,
                   "arguments": Json::Object(action.arguments.clone())})
        })
        .collect();
    json!({
        "campaign_id": request.campaign_id, "turn_id": request.turn_id, "package_id": request.package_id,
        "package_version": request.package_version, "package_sha256": request.package_sha256,
        "actor_id": request.actor_id, "actor_name": request.actor_name, "revision": request.revision,
        "decision_id": request.decision_id, "instructions": request.instructions, "persona": request.persona,
        "observation": Json::Object(request.observation.clone()), "actions": actions,
    })
}

/// `ActionCommand`'s fields in order.
fn command_json(command: &ActionCommand) -> Json {
    json!({
        "campaign_id": command.campaign_id, "action_id": command.action_id,
        "idempotency_key": command.idempotency_key, "owner_id": command.owner_id,
        "writer": command.writer.as_str(), "expected_revision": command.expected_revision,
        "turn_id": command.turn_id, "package_id": command.package_id, "tool_name": command.tool_name,
        "parameters": Json::Object(command.parameters.clone()),
        "initiating_session_id": command.initiating_session_id,
    })
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

#[derive(Default)]
struct Kept {
    requests: HashMap<String, ActorDecisionRequest>,
    commands: HashMap<String, ActionCommand>,
}

async fn run(store: &StoreHandle, step: &Json, kept: &mut Kept) -> Result<Json, ActionError> {
    let text = |key: &str| step[key].as_str().unwrap().to_owned();
    Ok(match step["op"].as_str().unwrap() {
        "request" => {
            let request = actor_decision_request(store, CAMPAIGN, &text("turn")).await?;
            let value = request.as_ref().map_or(Json::Null, request_json);
            if let (Some(keep), Some(request)) = (step["keep"].as_str(), request) {
                kept.requests.insert(keep.to_owned(), request);
            }
            value
        }
        "command" => {
            let mut request = kept.requests[&text("request")].clone();
            if let Some(actor) = step["actor"].as_str() {
                request.actor_id = actor.to_owned();
            }
            let command = actor_decision_command(
                store,
                &request,
                &text("choice"),
                step["session"].as_str().unwrap_or(SESSION),
                OWNER,
                step["provider"].as_str(),
                step["model"].as_str(),
            )
            .await?;
            let value = command_json(&command);
            if let Some(keep) = step["keep"].as_str() {
                kept.commands.insert(keep.to_owned(), command);
            }
            value
        }
        "execute" => match execute_action(store, OWNER, &kept.commands[&text("command")]).await? {
            ActionExecution::Receipt(receipt) => json!({
                "campaign_id": receipt.campaign_id, "action_id": receipt.action_id,
                "status": receipt.status.as_str(), "result": Json::Object(receipt.result),
                "resulting_revision": receipt.resulting_revision,
            }),
            ActionExecution::AwaitingChoice(_) => panic!("the token Ruleset asks no choice"),
        },
        other => panic!("unknown step {other}"),
    })
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn actor_decisions_answer_as_pythons_owner() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/actor_decisions");
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors.join("actor_decision_runs.json")).unwrap())
            .unwrap();
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 13);
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join("actor-decisions");
    if let Err(error) = std::fs::remove_dir_all(&folder) {
        assert_eq!(error.kind(), std::io::ErrorKind::NotFound, "cannot clear {}: {error}", folder.display());
    }
    std::fs::create_dir_all(&folder).unwrap();
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip(&vectors.join("base.sqlite3.gz"))).unwrap();
    let path = database.clone();
    let (store, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    let mut kept = Kept::default();
    let mut failures = Vec::new();
    for (index, item) in runs.iter().enumerate() {
        let got = match run(&store, &item["step"], &mut kept).await {
            Ok(value) => json!({"ok": value}),
            Err(error) => failure(error),
        };
        if let Some(difference) = json_difference(&got, &item["outcome"]) {
            failures.push(format!("#{index} {}: {difference}", item["step"]));
        }
    }
    drop(store);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip(&vectors.join("final.sqlite3.gz"))).unwrap();
    let (rust_rows, python_rows) = (table_rows(&database), table_rows(&python));
    if rust_rows.keys().ne(python_rows.keys()) {
        failures.push("the final tables differ from Python's".into());
    }
    for (table, rows) in &python_rows {
        let empty = Vec::new();
        let found = rust_rows.get(table).unwrap_or(&empty);
        if found != rows {
            let python_only = rows.iter().find(|row| !found.contains(row));
            let rust_only = found.iter().find(|row| !rows.contains(row));
            failures.push(format!(
                "the final {table} rows differ from Python's: Python {python_only:?}, Rust {rust_only:?}"
            ));
        }
    }
    assert!(failures.is_empty(), "{} actor decision steps differ:\n{}", failures.len(), failures.join("\n"));
}
