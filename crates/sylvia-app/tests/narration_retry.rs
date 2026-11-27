//! Manual Retry and redirected narration against Python
//! (`tests/vectors/narration_retry/narration_retry_runs.json`, written by
//! `generate_narration_retry_runs.py`): every step replayed through
//! `sylvia_app::authority::narration_attempts` on the same base, each answer and each attempt's
//! cancel count compared with Python's, then the final database row for row and table for table.
//! A Retry's narrator follows the recorded script. Interruption identities are compared by order
//! of first appearance (Python's generator counts them; the Rust owner's are random).

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::BTreeMap;
use std::future::Future;
use std::io::Read;
use std::path::Path;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use sylvia_app::authority::actions::ActionError;
use sylvia_app::authority::narration_attempts::{
    self as narration, ManualRetry, NarrationStart, RetryFailure, RetryNarration, RetryStream,
};
use sylvia_base::testing::json_difference;
use sylvia_base::Json;
use sylvia_domain::authority::{CanonicalEvent, NarrationInterruption, ProseChannel, RetryNarrationInput};
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

fn retry_input(input: &RetryNarrationInput) -> Json {
    json!({
        "__class__": "RetryNarrationInput",
        "campaign_id": input.campaign_id,
        "interruption_id": input.interruption_id,
        "retry_id": input.retry_id,
        "failed_attempt_id": input.failed_attempt_id,
        "canonical_high_water": input.canonical_high_water,
        "turn_id": input.turn_id,
        "action_receipt": input.action_receipt.as_ref().map(|receipt| json!({
            "__class__": "ActionReceipt",
            "campaign_id": receipt.campaign_id,
            "action_id": receipt.action_id,
            "status": receipt.status.as_str(),
            "result": Json::Object(receipt.result.clone()),
            "resulting_revision": receipt.resulting_revision,
        })),
    })
}

/// The recorded narrator: each item emits a token or raises.
struct Script(Vec<Json>);

impl RetryNarration for Script {
    type Error = String;

    fn narrate<'a>(
        &'a mut self,
        _input: RetryNarrationInput,
        stream: &'a mut RetryStream<'_>,
    ) -> Pin<Box<dyn Future<Output = Result<(), String>> + Send + 'a>> {
        Box::pin(async move {
            for item in &self.0 {
                if let Some(Json::String(message)) = item.get("raise") {
                    return Err(message.clone());
                }
                let emit = item["emit"].as_array().unwrap();
                let channel = ProseChannel::parse(emit[0].as_str().unwrap()).unwrap();
                let blocks = (!emit[2].is_null()).then_some(&emit[2]);
                stream
                    .emit(channel, emit[1].as_str().unwrap(), blocks)
                    .await
                    .map_err(|refused| refused.message)?;
            }
            Ok(())
        })
    }
}

fn resolve(value: &Json, ids: &[String]) -> Json {
    match value {
        Json::Object(map) if map.len() == 1 && map.get("interruption").is_some_and(Json::is_u64) => {
            // An owner that diverged may not have returned it; its steps then differ.
            Json::from(
                ids.get(map["interruption"].as_u64().unwrap() as usize)
                    .map_or("interruption-unreturned", String::as_str),
            )
        }
        Json::Object(map) => {
            Json::Object(map.iter().map(|(key, item)| (key.clone(), resolve(item, ids))).collect())
        }
        Json::Array(items) => Json::Array(items.iter().map(|item| resolve(item, ids)).collect()),
        other => other.clone(),
    }
}

fn note(value: &Json, ids: &mut Vec<String>) {
    match value {
        Json::Object(map) if map.get("__class__") == Some(&Json::from("NarrationInterruption")) => {
            let id = map["interruption_id"].as_str().unwrap().to_owned();
            if !ids.contains(&id) {
                ids.push(id);
            }
        }
        Json::Array(items) => items.iter().for_each(|item| note(item, ids)),
        _ => {}
    }
}

fn normalized(text: &str, ids: &[String]) -> String {
    let mut text = text.to_owned();
    for (index, id) in ids.iter().enumerate() {
        text = text.replace(id.as_str(), &format!("interruption-#{index}"));
    }
    text
}

fn table_rows(path: &Path, ids: &[String]) -> BTreeMap<String, Vec<String>> {
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
                    Ok(normalized(&format!("{values:?}"), ids))
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

fn counter(cancels: &Cancels, attempt: &str) -> Box<dyn FnMut() + Send> {
    cancels.lock().unwrap().entry(attempt.to_owned()).or_insert(0);
    let (cancels, key) = (Arc::clone(cancels), attempt.to_owned());
    Box::new(move || *cancels.lock().unwrap().get_mut(&key).unwrap() += 1)
}

async fn run(store: &StoreHandle, step: &Json, cancels: &Cancels) -> Json {
    let text = |key: &str| step[key].as_str().unwrap().to_owned();
    let answer: Result<Json, ActionError> = async {
        Ok(match step["op"].as_str().unwrap() {
            "start" => {
                narration::start_narration_attempt(
                    store,
                    NarrationStart {
                        campaign_id: CAMPAIGN.into(),
                        attempt_id: text("attempt"),
                        turn_id: "turn".into(),
                        cancel: counter(cancels, &text("attempt")),
                        last_action_id: None,
                        interruption_id: None,
                    },
                )
                .await?;
                Json::Null
            }
            "emit" => {
                let channel = ProseChannel::parse(step["channel"].as_str().unwrap()).unwrap();
                narration::emit_narration_token(
                    store,
                    CAMPAIGN,
                    &text("attempt"),
                    channel,
                    &text("token"),
                    None,
                )
                .await?;
                Json::Null
            }
            "bind" => {
                narration::bind_narration_action(store, CAMPAIGN, &text("attempt"), &text("action")).await?;
                Json::Null
            }
            "interrupt" => {
                interruption(&narration::interrupt_narration(store, CAMPAIGN, &text("attempt")).await?)
            }
            "status" => narration::narration_attempt_status(store, CAMPAIGN, &text("attempt")).await?.into(),
            "retryable" => narration::retryable_interruption(store, CAMPAIGN, &text("failed"))
                .await?
                .map_or(Json::Null, |value| interruption(&value)),
            "interruptions" => {
                let all = store.call(|store| store.interruptions(CAMPAIGN)).await?;
                Json::Array(all.iter().map(interruption).collect())
            }
            "append" => {
                let kind = SessionMessageKind::parse(step["kind"].as_str().unwrap()).unwrap();
                message(
                    &store
                        .append_agent_message(
                            &text("session"),
                            &text("message"),
                            kind,
                            step["content"].as_object().unwrap(),
                        )
                        .await?,
                )
            }
            "unresolved" => {
                let session = text("session");
                let context = store.session_context(&session).await?;
                match narration::unresolved_session_interruption(store, &context).await? {
                    None => Json::Null,
                    Some((value, redirects)) => json!([interruption(&value), redirects]),
                }
            }
            "redirect" => interruption(
                &narration::redirect_narration(
                    store,
                    &text("session"),
                    &text("message"),
                    step["content"].as_object().unwrap(),
                    &text("interruption"),
                )
                .await?,
            ),
            other => panic!("unknown step {other}"),
        })
    }
    .await;
    match answer {
        Ok(value) => json!({"ok": value}),
        Err(error) => failure(error),
    }
}

async fn retry(store: &StoreHandle, step: &Json, cancels: &Cancels) -> Json {
    let started = Arc::new(Mutex::new(0usize));
    let seen = Arc::clone(&started);
    let request = ManualRetry {
        campaign_id: CAMPAIGN.into(),
        interruption_id: step["interruption"].as_str().unwrap().to_owned(),
        retry_id: step["retry"].as_str().unwrap().to_owned(),
        cancel: counter(cancels, step["retry"].as_str().unwrap()),
    };
    let script = Script(step["script"].as_array().unwrap().clone());
    match narration::manual_retry(store, request, move || *seen.lock().unwrap() += 1, script).await {
        Ok(result) => json!({
            "ok": {
                "__class__": "ManualRetryResult",
                "input": retry_input(&result.input),
                "canonical": result.canonical.iter().map(event).collect::<Vec<_>>(),
                "replayed": result.replayed,
            },
            "started": *started.lock().unwrap(),
        }),
        Err(RetryFailure::Owner(error)) => failure(error),
        Err(RetryFailure::Narrator(message)) => {
            json!({"error": "RuntimeError", "code": null, "message": message})
        }
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn manual_retry_and_redirection_answer_and_store_as_pythons_owner() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/narration_retry");
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors.join("narration_retry_runs.json")).unwrap())
            .unwrap();
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 39);
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join("narration-retry");
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
    let cancels: Cancels = Arc::default();
    let (mut python_ids, mut rust_ids) = (Vec::new(), Vec::new());
    let mut failures = Vec::new();
    for (index, item) in runs.iter().enumerate() {
        let step = resolve(&item["step"], &rust_ids);
        let got = if step["op"] == "retry" {
            retry(&handle, &step, &cancels).await
        } else {
            run(&handle, &step, &cancels).await
        };
        if let Some(value) = got.get("ok") {
            note(value, &mut rust_ids);
        }
        if let Some(value) = item["outcome"].get("ok") {
            note(value, &mut python_ids);
        }
        let counts = cancels.lock().unwrap().clone();
        let got = normalized(&json!({"outcome": got, "cancels": counts}).to_string(), &rust_ids);
        let want = normalized(
            &json!({"outcome": item["outcome"], "cancels": item["cancels"]}).to_string(),
            &python_ids,
        );
        // `assert_same_json`'s comparison, on the normalized texts read back.
        let parsed = |text: &str| serde_json::from_str::<Json>(text).unwrap();
        if let Some(difference) = json_difference(&parsed(&got), &parsed(&want)) {
            failures
                .push(format!("#{index} {}: {difference}\n  Rust   {got}\n  Python {want}", item["step"]));
        }
    }
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
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
    assert!(failures.is_empty(), "{} retry steps differ:\n{}", failures.len(), failures.join("\n"));
}
