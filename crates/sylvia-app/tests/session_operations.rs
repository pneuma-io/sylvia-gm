//! The owner's smaller session operations against Python
//! (`tests/vectors/session_operation/session_operation_runs.json`, written by
//! `generate_session_operation_runs.py`): every step replayed on the same base through the Rust
//! owner's functions, each answer or failure compared with Python's, then the final database
//! row for row and table for table.

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::actions::ActionError;
use sylvia_app::authority::narration_attempts::{self as narration, NarrationStart};
use sylvia_app::authority::{player_reads, turns};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{CanonicalEvent, ProseChannel};
use sylvia_domain::provider_records::ProviderPrivateRecord;
use sylvia_domain::session::{SessionMessage, SessionMessageKind};
use sylvia_store::{AuthorityStore, StoreError, StoreHandle};

const CAMPAIGN: &str = "campaign";

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

fn message(message: &SessionMessage) -> Json {
    json!({
        "__class__": "SessionMessage",
        "session_id": message.session_id(),
        "sequence": message.sequence(),
        "message_id": message.message_id(),
        "kind": message.kind().as_str(),
        "content": Json::Object(message.content().clone()),
        "canonical_sequence": message.canonical_sequence(),
    })
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

async fn run(store: &StoreHandle, step: &Json) -> Result<Json, ActionError> {
    let text = |key: &str| step[key].as_str().unwrap().to_owned();
    let kind = || SessionMessageKind::parse(step["kind"].as_str().unwrap());
    Ok(match step["op"].as_str().unwrap() {
        "basis" => Json::Object(
            player_reads::player_action_response_basis(
                store,
                &text("session"),
                &text("action"),
                step["automatic"].as_bool().unwrap(),
            )
            .await?,
        ),
        "receipt" => {
            let receipt = narration::saved_action_receipt(store, CAMPAIGN, &text("action")).await?;
            json!({
                "__class__": "ActionReceipt",
                "campaign_id": receipt.campaign_id,
                "action_id": receipt.action_id,
                "status": receipt.status.as_str(),
                "result": Json::Object(receipt.result),
                "resulting_revision": receipt.resulting_revision,
            })
        }
        "agent_work" | "builder_work" => {
            let held = if step["op"] == "agent_work" {
                store.agent_session_work(&text("session")).await?
            } else {
                store.builder_session_work(&text("session")).await?
            };
            json!({"session_id": held.context.session.session_id(), "messages": held.context.messages.len()})
        }
        "steering" => {
            let Some(kind) = kind() else { panic!("unknown kind") };
            let content = step["content"].as_object().unwrap();
            message(
                &store
                    .append_live_steering_message(&text("session"), &text("message"), kind, content)
                    .await?,
            )
        }
        "references" => {
            let wanted: Vec<i64> =
                step["sequences"].as_array().unwrap().iter().map(|item| item.as_i64().unwrap()).collect();
            let events: Vec<CanonicalEvent> = store
                .call(|store| store.snapshot(CAMPAIGN))
                .await?
                .events
                .into_iter()
                .filter(|event| wanted.contains(&event.sequence))
                .collect();
            let messages = store.append_session_canonical_references(&text("session"), &events).await?;
            Json::Array(messages.iter().map(message).collect())
        }
        "private" => {
            let record = &step["record"];
            let field = |key: &str| record[key].as_str().unwrap().to_owned();
            store
                .append_private_provider_record(&ProviderPrivateRecord {
                    session_id: field("session_id"),
                    attempt_id: field("attempt_id"),
                    response_id: field("response_id"),
                    turn_id: field("turn_id"),
                    basis_message_id: field("basis_message_id"),
                    source_order: record["source_order"].as_i64().unwrap(),
                    provider_id: field("provider_id"),
                    model_id: field("model_id"),
                    kind: field("kind"),
                    payload: field("payload"),
                })
                .await?;
            Json::Null
        }
        "finish" => {
            store
                .finish_private_provider_attempt(
                    &text("session"),
                    &text("attempt"),
                    step["completed"].as_bool().unwrap(),
                )
                .await?;
            Json::Null
        }
        "private_index" => Json::Array(
            narration::private_provider_index(store, &text("session"))
                .await?
                .into_iter()
                .map(Json::Object)
                .collect(),
        ),
        "append" => {
            let content: &JsonMap = step["content"].as_object().unwrap();
            message(
                &store
                    .append_agent_message(&text("session"), &text("message"), kind().unwrap(), content)
                    .await?,
            )
        }
        "narrate" => {
            let attempt = text("attempt");
            narration::start_narration_attempt(
                store,
                NarrationStart {
                    campaign_id: CAMPAIGN.into(),
                    attempt_id: attempt.clone(),
                    turn_id: "turn".into(),
                    cancel: Box::new(|| {}),
                    last_action_id: None,
                    interruption_id: None,
                },
            )
            .await?;
            narration::emit_narration_token(
                store,
                CAMPAIGN,
                &attempt,
                ProseChannel::Talk,
                &text("text"),
                None,
            )
            .await?;
            let events = narration::complete_narration_attempt(store, CAMPAIGN, &attempt).await?;
            Json::Array(events.iter().map(event).collect())
        }
        "archive" => {
            let record = store
                .set_builder_chat_archived(&text("session"), step["archived"].as_bool().unwrap())
                .await?;
            json!({
                "session_id": record.session().session_id(),
                "status": record.session().fields().status.as_str(),
                "display_name": record.display_name(),
                "created_ordinal": record.created_ordinal(),
            })
        }
        "complete_turn" => {
            turns::complete_turn(store, &text("campaign"), &text("turn")).await?;
            Json::Null
        }
        other => panic!("unknown step {other}"),
    })
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn session_operations_answer_and_store_as_pythons_owner() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/session_operation");
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors.join("session_operation_runs.json")).unwrap())
            .unwrap();
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 46);
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join("session-operations");
    if let Err(error) = std::fs::remove_dir_all(&folder) {
        assert_eq!(error.kind(), std::io::ErrorKind::NotFound, "cannot clear {}: {error}", folder.display());
    }
    std::fs::create_dir_all(&folder).unwrap();
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip(&vectors.join("base.sqlite3.gz"))).unwrap();
    let path = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    handle.narration().set_disconnect_grace(5.0).unwrap();
    let mut failures = Vec::new();
    for (index, item) in runs.iter().enumerate() {
        let step = &item["step"];
        let mut got = match run(&handle, step).await {
            Ok(value) => json!({"ok": value}),
            Err(error) => failure(error),
        };
        // A message kind the live steering write refuses is Python's `TypeError`; the store raises
        // it as a fault with Python's message (the owner's `internal_error` either way).
        if got["error"] == "RustStoreError"
            && item["outcome"]["error"] == "TypeError"
            && got["message"] == item["outcome"]["message"]
        {
            got["error"] = "TypeError".into();
        }
        let (got, want) = (got.to_string(), item["outcome"].to_string());
        if got != want {
            failures.push(format!("#{index} {step}\n  Rust   {got}\n  Python {want}"));
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
    assert!(failures.is_empty(), "{} session steps differ:\n{}", failures.len(), failures.join("\n"));
}
