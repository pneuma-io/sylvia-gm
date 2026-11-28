//! Action sequences on the authority against Python's real owner
//! (`tests/vectors/sequence_owner/sequence_owner_runs.json`, written by
//! `generate_sequence_owner_runs.py`): lane 2's `sylvia_app::action_sequences` run on
//! `AuthoritySequences`, each answer compared with `json_difference` (interruption identities by
//! order of appearance), then the final database row for row.
//!
//! The agent execution with the GM session's work held, while a narration attempt is open, binds
//! the attempt to the action it committed inside the held narration lock: the nested
//! `bind_narration_action` that `with_narration_lock`'s reentrancy exists for.

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::{BTreeMap, HashMap};
use std::io::Read;
use std::path::Path;
use sylvia_app::action_sequences::{
    action_sequence, compose_action_sequence, execute_action_sequence, last_attempt_commit, ComposeRequest,
    ExecuteRequest,
};
use sylvia_app::authority::actions::ActionError;
use sylvia_app::authority::narration_attempts::{self as narration, NarrationStart};
use sylvia_app::authority::sequence_owner::AuthoritySequences;
use sylvia_base::testing::json_difference;
use sylvia_base::Json;
use sylvia_domain::session::SessionMessageKind;
use sylvia_domain::DomainError;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle};

const CAMPAIGN: &str = "campaign";
const GM: &str = "gm";

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

/// `text` with each `interruption-<uuid>` replaced by its order of first appearance in `seen`.
fn normalized(text: &str, seen: &mut Vec<String>) -> String {
    let mut out = String::new();
    let mut rest = text;
    while let Some(at) = rest.find("interruption-") {
        let (before, after) = rest.split_at(at);
        out.push_str(before);
        let candidate = &after["interruption-".len()..];
        let length = candidate.chars().take_while(|c| c.is_ascii_hexdigit() || *c == '-').count();
        if length == 36 {
            let id = &after[..("interruption-".len() + 36)];
            let index = seen.iter().position(|known| known == id).unwrap_or_else(|| {
                seen.push(id.to_owned());
                seen.len() - 1
            });
            out.push_str(&format!("interruption-#{index}"));
            rest = &after[id.len()..];
        } else {
            out.push_str("interruption-");
            rest = candidate;
        }
    }
    out.push_str(rest);
    out
}

fn table_rows(path: &Path, seen: &mut Vec<String>) -> BTreeMap<String, Vec<String>> {
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
            let rows: Vec<String> = statement
                .query_map([], |row| {
                    let values: Vec<rusqlite::types::Value> =
                        (0..width).map(|index| row.get(index)).collect::<Result<_, _>>()?;
                    Ok(format!("{values:?}"))
                })
                .unwrap()
                .collect::<Result<_, _>>()
                .unwrap();
            let mut rows: Vec<String> = rows.iter().map(|row| normalized(row, seen)).collect();
            rows.sort();
            (table, rows)
        })
        .collect()
}

async fn run(
    store: &StoreHandle,
    step: &Json,
    last: &mut HashMap<String, Json>,
) -> Result<Json, ActionError> {
    let owner = AuthoritySequences::new(store, "owner");
    let text = |key: &str| step[key].as_str().unwrap().to_owned();
    let agent = step["agent"].as_bool().unwrap_or(false);
    Ok(match step["op"].as_str().unwrap() {
        "read" => action_sequence(&owner, &text("session"), step["sequence"].as_str())
            .await?
            .map_or(Json::Null, Json::Object),
        "compose" => {
            let id = CAMPAIGN.to_owned();
            let revision = store.call(move |store| store.snapshot(&id)).await?.state.revision;
            let request = ComposeRequest {
                campaign_id: CAMPAIGN.into(),
                session_id: GM.into(),
                sequence_id: text("sequence"),
                expected_revision: Json::from(revision),
                steps: step["steps"].clone(),
                expected_version: step["version"].clone(),
            };
            let queue = Json::Object(compose_action_sequence(&owner, &request, agent).await?);
            last.insert(text("sequence"), queue.clone());
            queue
        }
        "execute" => {
            let saved = last.get(&text("sequence")).cloned().unwrap_or(Json::Null);
            let version =
                if step["version"] == "last" { saved["version"].clone() } else { step["version"].clone() };
            let request = ExecuteRequest {
                campaign_id: CAMPAIGN.into(),
                session_id: GM.into(),
                sequence_id: text("sequence"),
                expected_version: version,
                confirmation: saved["confirmation"].as_str().map(str::to_owned),
            };
            let _held = match step["holding"].as_str() {
                Some(session) => Some(store.agent_session_work(session).await?),
                None => None,
            };
            Json::Object(execute_action_sequence(&owner, &request, agent).await?)
        }
        "start" => {
            narration::start_narration_attempt(
                store,
                NarrationStart {
                    campaign_id: CAMPAIGN.into(),
                    attempt_id: text("attempt"),
                    turn_id: "turn".into(),
                    cancel: Box::new(|| {}),
                    last_action_id: None,
                    interruption_id: None,
                },
            )
            .await?;
            Json::Null
        }
        "append" => store
            .append_agent_message(
                GM,
                &text("message"),
                SessionMessageKind::User,
                step["content"].as_object().unwrap(),
            )
            .await?
            .sequence()
            .into(),
        "last_attempt_commit" => {
            last_attempt_commit(&owner, CAMPAIGN, &text("attempt")).await?.map_or(Json::Null, Json::from)
        }
        "interrupt" => {
            let interruption = narration::interrupt_narration(store, CAMPAIGN, &text("attempt")).await?;
            json!({
                "interruption_id": interruption.interruption_id,
                "last_action_id": interruption.last_action_id,
                "failed_attempt_id": interruption.failed_attempt_id,
            })
        }
        other => panic!("unknown step {other}"),
    })
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn sequences_run_on_the_authority_as_on_pythons_owner() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/sequence_owner");
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors.join("sequence_owner_runs.json")).unwrap())
            .unwrap();
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 18);
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join("sequence-owner");
    if let Err(error) = std::fs::remove_dir_all(&folder) {
        assert_eq!(error.kind(), std::io::ErrorKind::NotFound, "cannot clear {}: {error}", folder.display());
    }
    std::fs::create_dir_all(&folder).unwrap();
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip(&vectors.join("base.sqlite3.gz"))).unwrap();
    let path = database.clone();
    let (store, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    store.narration().set_disconnect_grace(5.0).unwrap();
    let (mut rust_ids, mut python_ids) = (Vec::new(), Vec::new());
    let mut last = HashMap::new();
    let mut failures = Vec::new();
    for (index, item) in runs.iter().enumerate() {
        // A lock the authority fails to re-enter would wait on itself: fail the step, not the run.
        let step =
            tokio::time::timeout(std::time::Duration::from_secs(120), run(&store, &item["step"], &mut last));
        let got = match step.await {
            Ok(Ok(value)) => json!({"ok": value}),
            Ok(Err(error)) => failure(error),
            Err(_) => panic!("#{index} {} did not finish: a lock waits on itself", item["step"]),
        };
        let parsed = |text: String| serde_json::from_str::<Json>(&text).unwrap();
        let got = parsed(normalized(&got.to_string(), &mut rust_ids));
        let want = parsed(normalized(&item["outcome"].to_string(), &mut python_ids));
        if let Some(difference) = json_difference(&got, &want) {
            failures.push(format!("#{index} {}: {difference}", item["step"]));
        }
    }
    drop(store);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip(&vectors.join("final.sqlite3.gz"))).unwrap();
    let (rust_rows, python_rows) =
        (table_rows(&database, &mut rust_ids), table_rows(&python, &mut python_ids));
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
    assert!(failures.is_empty(), "{} sequence steps differ:\n{}", failures.len(), failures.join("\n"));
}
