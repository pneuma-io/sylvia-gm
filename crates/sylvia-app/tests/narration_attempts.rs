//! The narration attempt lifecycle against Python
//! (`tests/vectors/narration_attempt/narration_attempt_runs.json`, written by
//! `generate_narration_attempt_runs.py`): every step of every owner phase replayed through
//! `sylvia_app::authority::narration_attempts` on the same base database, each outcome and each
//! attempt's cancel count compared with Python's, then the final database row for row.
//!
//! Interruption identities are random on both owners (Python's generator counts them instead), so
//! both sides' are compared by order of first appearance in the run: the n-th identity either
//! returns becomes `interruption-#n` in its outcomes and in its final rows.

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;
use std::sync::{Arc, Mutex};
use sylvia_app::authority::actions::{execute_action, ActionError, ActionExecution};
use sylvia_app::authority::narration_attempts as narration;
use sylvia_app::authority::player_reads;
use sylvia_base::Json;
use sylvia_domain::authority::{
    ActionCommand, CanonicalEvent, CanonicalReplay, NarrationInterruption, PlayerProjection, ProseChannel,
    WriterRole,
};
use sylvia_domain::session::{SessionContext, SessionMessage, SessionMessageKind};

use sylvia_store::{AuthorityStore, StoreError, StoreHandle};

const CAMPAIGN: &str = "campaign";

fn gunzip(path: &Path) -> Vec<u8> {
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

fn event(event: &CanonicalEvent) -> Json {
    json!({
        "__class__": "CanonicalEvent",
        "sequence": event.sequence,
        "event_type": event.event_type.as_str(),
        "turn_id": event.turn_id,
        "action_id": event.action_id,
        "safe_payload": Json::Object(event.safe_payload.clone()),
        "visibility": event.visibility.as_str(),
        "attempt_id": event.attempt_id,
        "attempt_ordinal": event.attempt_ordinal,
    })
}

fn projection(item: &PlayerProjection) -> Json {
    json!({
        "__class__": "PlayerProjection",
        "kind": item.kind.as_str(),
        "sequence": item.sequence,
        "text": item.text,
        "safe_payload": Json::Object(item.safe_payload.clone()),
        "collapsed_by_default": item.collapsed_by_default,
        "provisional": item.provisional,
        "action_id": item.action_id,
        "attempt_id": item.attempt_id,
    })
}

fn replay(value: &CanonicalReplay) -> Json {
    json!({
        "__class__": "CanonicalReplay",
        "canonical": value.canonical.iter().map(event).collect::<Vec<_>>(),
        "provisional": value.provisional.iter().map(projection).collect::<Vec<_>>(),
        "high_water": value.high_water,
    })
}

fn interruption(value: &NarrationInterruption) -> Json {
    json!({
        "__class__": "NarrationInterruption",
        "campaign_id": value.campaign_id,
        "interruption_id": value.interruption_id,
        "turn_id": value.turn_id,
        "last_action_id": value.last_action_id,
        "failed_attempt_id": value.failed_attempt_id,
        "retryable": value.retryable,
        "resolving_retry_id": value.resolving_retry_id,
        "resolved_sequence": value.resolved_sequence,
    })
}

/// The generator's failure record: class, code (none for a `ValueError` or `TypeError`) and
/// message.
fn failure(error: ActionError) -> Json {
    let (class, code, message) = match error {
        ActionError::Domain(error) => {
            let class = error.python_class();
            let code = error.rejection().map(|coded| coded.code);
            (class.to_owned(), code, error.message().to_owned())
        }
        ActionError::Store(StoreError::Coded(coded)) => {
            (coded.error_type.unwrap_or("CommandRejected").to_owned(), Some(coded.code), coded.message)
        }
        ActionError::Store(other) => ("RustStoreError".to_owned(), None, other.to_string()),
    };
    let code = if matches!(class.as_str(), "ValueError" | "TypeError") { None } else { code };
    json!({"error": class, "code": code, "message": message})
}

fn number(value: &Json) -> f64 {
    match value {
        Json::String(text) => match text.as_str() {
            "inf" => f64::INFINITY,
            "-inf" => f64::NEG_INFINITY,
            "nan" => f64::NAN,
            other => panic!("{other}"),
        },
        other => other.as_f64().unwrap(),
    }
}

/// `resolve(step)`: `{"interruption": n}` is the n-th interruption identity returned so far.
fn resolve(value: &Json, interruptions: &[String]) -> Json {
    match value {
        Json::Object(map)
            if map.len() == 1 && map.contains_key("interruption") && map["interruption"].is_u64() =>
        {
            Json::from(interruptions[map["interruption"].as_u64().unwrap() as usize].as_str())
        }
        Json::Object(map) => {
            Json::Object(map.iter().map(|(key, item)| (key.clone(), resolve(item, interruptions))).collect())
        }
        Json::Array(items) => Json::Array(items.iter().map(|item| resolve(item, interruptions)).collect()),
        other => other.clone(),
    }
}

/// `note(value)`: remember each interruption identity the step returned.
fn note(value: &Json, interruptions: &mut Vec<String>) {
    match value {
        Json::Object(map) if map.get("__class__") == Some(&Json::from("NarrationInterruption")) => {
            let id = map["interruption_id"].as_str().unwrap().to_owned();
            if !interruptions.contains(&id) {
                interruptions.push(id);
            }
        }
        Json::Array(items) => items.iter().for_each(|item| note(item, interruptions)),
        _ => {}
    }
}

/// `text` with each returned interruption identity replaced by its order of appearance.
fn normalized(text: &str, interruptions: &[String]) -> String {
    let mut text = text.to_owned();
    for (index, id) in interruptions.iter().enumerate() {
        text = text.replace(id.as_str(), &format!("interruption-#{index}"));
    }
    text
}

/// Every table's rows, sorted, for comparing a database with Python's.
fn table_rows(path: &Path, interruptions: &[String]) -> BTreeMap<String, Vec<String>> {
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
                    Ok(normalized(&format!("{values:?}"), interruptions))
                })
                .unwrap()
                .collect::<Result<_, _>>()
                .unwrap();
            rows.sort();
            (table, rows)
        })
        .collect()
}

type Cancels = Arc<Mutex<BTreeMap<String, usize>>>;

async fn run(
    store: &StoreHandle,
    owner_id: &str,
    database: &Path,
    step: &Json,
    cancels: &Cancels,
) -> Result<Json, ActionError> {
    let text = |key: &str| step[key].as_str().unwrap().to_owned();
    let attempt = step["attempt"].as_str().unwrap_or_default().to_owned();
    Ok(match step["op"].as_str().unwrap() {
        "start" => {
            cancels.lock().unwrap().entry(attempt.clone()).or_insert(0);
            let (counts, key) = (Arc::clone(cancels), attempt.clone());
            narration::start_narration_attempt(
                store,
                narration::NarrationStart {
                    campaign_id: CAMPAIGN.into(),
                    attempt_id: attempt,
                    turn_id: step["turn"].as_str().unwrap_or("turn").to_owned(),
                    cancel: Box::new(move || *counts.lock().unwrap().get_mut(&key).unwrap() += 1),
                    last_action_id: step["last_action"].as_str().map(str::to_owned),
                    interruption_id: step["retry_of"].as_str().map(str::to_owned),
                },
            )
            .await?;
            Json::Null
        }
        "emit" => {
            let channel = ProseChannel::parse(step["channel"].as_str().unwrap()).unwrap();
            let blocks = step.get("blocks");
            narration::emit_narration_token(store, CAMPAIGN, &attempt, channel, &text("token"), blocks)
                .await?;
            Json::Null
        }
        "flush" => {
            narration::flush_narration(store, CAMPAIGN, &attempt).await?;
            Json::Null
        }
        "bind" => {
            narration::bind_narration_action(store, CAMPAIGN, &attempt, &text("action")).await?;
            Json::Null
        }
        "complete" => {
            let events = narration::complete_narration_attempt(store, CAMPAIGN, &attempt).await?;
            Json::Array(events.iter().map(event).collect())
        }
        "disconnect" => {
            narration::disconnect_narration(store, CAMPAIGN, &attempt, number(&step["now"])).await?.into()
        }
        "reconnect" => {
            let ack = step["ack"].as_i64().unwrap();
            replay(
                &narration::reconnect_narration(store, CAMPAIGN, &attempt, number(&step["now"]), ack).await?,
            )
        }
        "status" => narration::narration_attempt_status(store, CAMPAIGN, &attempt).await?.into(),
        "expire" => {
            interruption(&narration::expire_narration(store, CAMPAIGN, &attempt, number(&step["now"])).await?)
        }
        "interrupt" => interruption(&narration::interrupt_narration(store, CAMPAIGN, &attempt).await?),
        "replay" => replay(
            &player_reads::canonical_replay(
                store,
                store.narration(),
                CAMPAIGN,
                step["ack"].as_i64().unwrap(),
            )
            .await?,
        ),
        "execute" => {
            let action_id = text("action_id");
            let mut parameters = sylvia_base::JsonMap::new();
            parameters.insert("actor".into(), "operator".into());
            parameters.insert("destination".into(), step["destination"].clone());
            parameters.insert("mode".into(), "careful".into());
            let command = ActionCommand {
                campaign_id: CAMPAIGN.into(),
                action_id: action_id.clone(),
                idempotency_key: format!("idem-{action_id}"),
                owner_id: owner_id.into(),
                writer: WriterRole::Dm,
                expected_revision: step["revision"].as_i64().unwrap(),
                turn_id: "turn".into(),
                package_id: "synthetic.coordinate".into(),
                tool_name: "move_unit".into(),
                parameters,
                initiating_session_id: None,
            };
            match execute_action(store, owner_id, &command).await? {
                ActionExecution::Receipt(receipt) => json!({
                    "__class__": "ActionReceipt",
                    "campaign_id": receipt.campaign_id,
                    "action_id": receipt.action_id,
                    "status": receipt.status.as_str(),
                    "result": Json::Object(receipt.result),
                    "resulting_revision": receipt.resulting_revision,
                }),
                ActionExecution::AwaitingChoice(_) => panic!("no choice here"),
            }
        }
        "retryable" => narration::retryable_interruption(store, CAMPAIGN, &text("failed"))
            .await?
            .map_or(Json::Null, |value| interruption(&value)),
        "interruption" => {
            interruption(&narration::narration_interruption(store, CAMPAIGN, &text("interruption")).await?)
        }
        "interruptions" => {
            let all = store.call(|store| store.interruptions(CAMPAIGN)).await?;
            Json::Array(all.iter().map(interruption).collect())
        }
        "unresolved" => {
            let session = text("session");
            let base = store.call(move |store| store.session_context(&session)).await?;
            let mut messages = base.messages.clone();
            for (index, item) in step["messages"].as_array().unwrap().iter().enumerate() {
                let kind = SessionMessageKind::parse(item["kind"].as_str().unwrap()).unwrap();
                let sequence = (base.messages.len() + index + 1) as i64;
                messages.push(SessionMessage::new(
                    base.session.session_id(),
                    sequence,
                    format!("extra-{index}"),
                    kind,
                    &item["content"],
                    None,
                )?);
            }
            let context = SessionContext { session: base.session.clone(), messages };
            match narration::unresolved_session_interruption(store, &context).await? {
                None => Json::Null,
                Some((value, redirects)) => json!([interruption(&value), redirects]),
            }
        }
        "private_index" => {
            let index = narration::private_provider_index(store, &text("session")).await?;
            Json::Array(index.into_iter().map(Json::Object).collect())
        }
        "sql" => {
            let connection = rusqlite::Connection::open(database).unwrap();
            connection.execute_batch(&text("query")).unwrap();
            Json::Null
        }
        other => panic!("unknown step {other}"),
    })
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn narration_attempts_answer_and_store_as_pythons_owner() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/narration_attempt");
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors.join("narration_attempt_runs.json")).unwrap())
            .unwrap();
    let owner_id = record["owner_id"].as_str().unwrap().to_owned();
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join("narration-attempts");
    if let Err(error) = std::fs::remove_dir_all(&folder) {
        assert_eq!(error.kind(), std::io::ErrorKind::NotFound, "cannot clear {}: {error}", folder.display());
    }
    std::fs::create_dir_all(&folder).unwrap();
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip(&vectors.join("base.sqlite3.gz"))).unwrap();
    let (mut python_ids, mut rust_ids) = (Vec::new(), Vec::new());
    let cancels: Cancels = Arc::default();
    let mut failures = Vec::new();
    let mut count = 0;
    for (phase_index, phase) in record["phases"].as_array().unwrap().iter().enumerate() {
        let path = database.clone();
        let (handle, thread) =
            StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
        if let Some(grace) = phase["grace"].as_f64() {
            handle.narration().set_disconnect_grace(grace).unwrap();
        }
        for (index, item) in phase["runs"].as_array().unwrap().iter().enumerate() {
            count += 1;
            let step = resolve(&item["step"], &rust_ids);
            let got = match run(&handle, &owner_id, &database, &step, &cancels).await {
                Ok(value) => {
                    note(&value, &mut rust_ids);
                    json!({"ok": value})
                }
                Err(error) => failure(error),
            };
            if let Some(value) = item["outcome"].get("ok") {
                note(value, &mut python_ids);
            }
            let counts: BTreeMap<String, usize> = cancels.lock().unwrap().clone();
            let got = json!({"outcome": got, "cancels": counts});
            let want = json!({"outcome": item["outcome"], "cancels": item["cancels"]});
            let (got, want) =
                (normalized(&got.to_string(), &rust_ids), normalized(&want.to_string(), &python_ids));
            if got != want {
                failures.push(format!(
                    "phase {phase_index} #{index} {}\n  Rust   {got}\n  Python {want}",
                    item["step"]
                ));
            }
        }
        drop(handle);
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    }
    assert_eq!(count, 114, "the record's step count");
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip(&vectors.join("final.sqlite3.gz"))).unwrap();
    let (rust_rows, python_rows) = (table_rows(&database, &rust_ids), table_rows(&python, &python_ids));
    if rust_rows.keys().ne(python_rows.keys()) {
        failures.push("the final tables differ from Python's".to_owned());
    }
    for (table, rows) in &python_rows {
        if rust_rows.get(table) != Some(rows) {
            failures.push(format!("the final {table} rows differ from Python's"));
        }
    }
    assert!(failures.is_empty(), "{} narration steps differ:\n{}", failures.len(), failures.join("\n"));
}
