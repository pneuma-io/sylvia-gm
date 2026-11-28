//! Confirming a NEW CAMPAIGN and completing its construction against Python
//! (`tests/vectors/campaign_creation/runs.json`, written by `generate_campaign_creation_runs.py`
//! on `guided/ruleset_seed.sqlite3.gz`): each step replayed through
//! `sylvia_app::authority::campaign_creation`, `guided::begin_campaign_construction` and the store
//! handle's prepared-start fence, each answer compared with `json_difference`, then the final
//! database row for row.
//!
//! A new campaign's identity is a random UUID (a counter in the generator) and its lock identity
//! hashes it, so both are compared by order of first appearance. Two stored digests hash it too
//! (a session's `payload_sha256`, an operation's `semantic_payload_sha256`): each is checked
//! against the row it hashes in its own database instead.

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::{BTreeMap, HashMap};
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::campaign_creation::{self, ConfirmRequest};
use sylvia_app::authority::guided;
use sylvia_base::testing::json_difference;
use sylvia_base::Json;
use sylvia_domain::authority::{PackageLock, StateValue};
use sylvia_store::campaign_confirmation::ReadyStartWork;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle};

fn gunzip(path: &Path) -> Vec<u8> {
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

fn failure(error: StoreError) -> Json {
    let Some(coded) = error.rejection() else {
        return json!({"error": "RustFault", "code": null, "message": error.to_string()});
    };
    let class = coded.error_type.unwrap_or("CommandRejected").to_owned();
    let authority = !matches!(class.as_str(), "ValueError" | "TypeError" | "KeyError" | "AttributeError");
    json!({"error": class, "code": if authority { Some(coded.code) } else { None }, "message": coded.message})
}

/// The identities a new campaign gets, by order of first appearance in `seen`.
#[derive(Default)]
struct Seen {
    uuids: Vec<String>,
    locks: Vec<String>,
}

fn is_hex(byte: u8) -> bool {
    byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
}

fn uuid_at(bytes: &[u8], at: usize) -> bool {
    bytes.len() >= at + 36
        && (0..36).all(|index| match index {
            8 | 13 | 18 | 23 => bytes[at + index] == b'-',
            _ => is_hex(bytes[at + index]),
        })
        && (at == 0 || !is_hex(bytes[at - 1]))
}

fn lock_at(bytes: &[u8], at: usize) -> bool {
    bytes[at..].starts_with(b"lock-")
        && bytes.len() >= at + 45
        && bytes[at + 5..at + 45].iter().all(|byte| is_hex(*byte))
}

fn index_of(known: &mut Vec<String>, id: &str) -> usize {
    match known.iter().position(|item| item == id) {
        Some(index) => index,
        None => {
            known.push(id.to_owned());
            known.len() - 1
        }
    }
}

/// `text` with each UUID and each `lock-<40 hex>` replaced by its order of first appearance.
fn normalized(text: &str, seen: &mut Seen) -> String {
    let bytes = text.as_bytes();
    let mut out = String::new();
    let mut at = 0;
    while at < bytes.len() {
        if uuid_at(bytes, at) {
            out.push_str(&format!("uuid#{}", index_of(&mut seen.uuids, &text[at..at + 36])));
            at += 36;
        } else if lock_at(bytes, at) {
            out.push_str(&format!("lock#{}", index_of(&mut seen.locks, &text[at..at + 45])));
            at += 45;
        } else {
            let next = text[at..].chars().next().unwrap();
            out.push(next);
            at += next.len_utf8();
        }
    }
    out
}

fn text_at(values: &[rusqlite::types::Value], index: usize) -> Option<String> {
    match &values[index] {
        rusqlite::types::Value::Text(text) => Some(text.clone()),
        _ => None,
    }
}

/// `agent_session_payload_sha256` of an `agent_sessions` row, as Python's `json.dumps` writes it.
fn session_digest(values: &[rusqlite::types::Value]) -> String {
    let mut payload = sylvia_base::JsonMap::new();
    let scope: Json = serde_json::from_str(&text_at(values, 8).unwrap()).unwrap();
    for (key, value) in [
        ("campaign_id", text_at(values, 1).map(Json::from)),
        ("model_id", text_at(values, 4).map(Json::from)),
        ("parent_session_id", Some(text_at(values, 6).map_or(Json::Null, Json::from))),
        ("provider_id", text_at(values, 3).map(Json::from)),
        ("reasoning_effort", text_at(values, 10).map(Json::from)),
        ("reasoning_effort_once", text_at(values, 11).map(Json::from)),
        ("role", text_at(values, 2).map(Json::from)),
        ("scope", Some(scope)),
        ("selected_skill", Some(text_at(values, 7).map_or(Json::Null, Json::from))),
        ("session_id", text_at(values, 0).map(Json::from)),
        ("status", text_at(values, 5).map(Json::from)),
    ] {
        match (key, value) {
            (_, Some(value)) => payload.insert(key.into(), value),
            ("campaign_id", None) => payload.insert(key.into(), Json::Null),
            _ => None,
        };
    }
    sylvia_base::hash::sha256_hex(Json::Object(payload).to_string().as_bytes())
}

/// The digest columns that hash a new campaign's identity, which differs between the two runs:
/// each is checked against what it hashes in its own database, then left out of the comparison.
fn checked_digest(table: &str, values: &mut [rusqlite::types::Value]) {
    let (column, expected) = match table {
        "agent_sessions" => (9, session_digest(values)),
        "campaign_operations" => (4, sylvia_base::hash::sha256_hex(text_at(values, 3).unwrap().as_bytes())),
        _ => return,
    };
    assert_eq!(text_at(values, column).as_deref(), Some(expected.as_str()), "{table} digest of {values:?}");
    values[column] = rusqlite::types::Value::Text("<digest checked>".into());
}

fn table_rows(path: &Path, seen: &mut Seen) -> BTreeMap<String, Vec<String>> {
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
                    let mut values: Vec<rusqlite::types::Value> =
                        (0..width).map(|index| row.get(index)).collect::<Result<_, _>>()?;
                    checked_digest(&table, &mut values);
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

fn confirmation_json(confirmation: &sylvia_domain::authority::CampaignConfirmation) -> Json {
    json!({"campaign_id": confirmation.campaign_id, "resulting_revision": confirmation.resulting_revision})
}

async fn confirm(
    store: &StoreHandle,
    step: &Json,
    campaigns: &mut HashMap<String, String>,
    held: Option<&ReadyStartWork>,
) -> Result<Json, StoreError> {
    let candidate = step["candidate"].as_str();
    let package_lock = match candidate {
        Some(candidate) if !candidate.trim().is_empty() => {
            let candidate = candidate.to_owned();
            store.call(move |store| store.package_candidate(&candidate)).await?.package_lock().clone()
        }
        _ => PackageLock::new(Vec::new()).unwrap(),
    };
    let session = |key: &str| step.get("session").and_then(|session| session[key].as_str());
    let state: Vec<StateValue> = step
        .get("state")
        .and_then(Json::as_array)
        .map(|items| {
            items
                .iter()
                .map(|item| {
                    StateValue::new(item[0].as_str().unwrap(), item[1].as_str().unwrap(), item[2].clone())
                })
                .collect::<Result<_, _>>()
                .unwrap()
        })
        .unwrap_or_default();
    let request = ConfirmRequest {
        operation_id: step["operation_id"].as_str().unwrap(),
        display_name: step["display_name"].as_str().unwrap(),
        package_lock: &package_lock,
        turn_id: step["turn_id"].as_str().unwrap(),
        package_candidate_id: candidate,
        builder_session_id: session("session"),
        builder_provider_id: session("provider"),
        builder_model_id: session("model"),
        builder_selected_skill: step.get("skill").and_then(Json::as_str).unwrap_or("BUILD CAMPAIGN PLAN"),
        participants: &[],
        initial_state: &state,
    };
    let confirmation = campaign_creation::confirm_new_campaign(store, &request, held).await?;
    campaigns.insert(request.operation_id.to_owned(), confirmation.campaign_id.clone());
    Ok(confirmation_json(&confirmation))
}

async fn run(
    store: &StoreHandle,
    step: &Json,
    campaigns: &mut HashMap<String, String>,
) -> Result<Json, StoreError> {
    let text = |key: &str| step[key].as_str().unwrap().to_owned();
    let campaign = |key: &str| {
        let name = text(key);
        campaigns.get(&name).cloned().unwrap_or(name)
    };
    Ok(match step["op"].as_str().unwrap() {
        "confirmation" => match store.campaign_confirmation(&text("operation_id")).await? {
            Some(found) => confirmation_json(&found),
            None => Json::Null,
        },
        "confirm" => confirm(store, step, campaigns, None).await?,
        "reject" => store.reject_uncreated_ready_campaign(&text("operation_id")).await?.into(),
        "start" => {
            store.ready_campaign_start_work(&text("operation_id")).await?;
            Json::Null
        }
        "start_and_confirm" => {
            let inner = &step["confirm"];
            let held = store.ready_campaign_start_work(inner["operation_id"].as_str().unwrap()).await?;
            confirm(store, inner, campaigns, Some(&held)).await?
        }
        "begin" => {
            let begun = guided::begin_campaign_construction(
                store,
                &text("operation_id"),
                &campaign("campaign"),
                "quick_start",
                step["expected_revision"].as_i64().unwrap(),
                &step["input"],
            )
            .await?;
            json!({"campaign_id": begun.campaign_id, "lane": begun.lane, "resulting_revision": begun.resulting_revision})
        }
        "complete" => {
            let done = campaign_creation::complete_campaign_construction(
                store,
                &text("operation_id"),
                &campaign("campaign"),
                step["expected_revision"].as_i64().unwrap(),
                &step["plan"],
            )
            .await?;
            json!({
                "campaign_id": done.campaign_id,
                "lane": done.lane,
                "canonical_representation_sha256": done.canonical_representation_sha256,
                "resulting_revision": done.resulting_revision,
            })
        }
        other => panic!("unknown step {other}"),
    })
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn confirmations_and_completions_answer_as_pythons_owner() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors");
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors.join("campaign_creation/runs.json")).unwrap())
            .unwrap();
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join("campaign-creation");
    if let Err(error) = std::fs::remove_dir_all(&folder) {
        assert_eq!(error.kind(), std::io::ErrorKind::NotFound, "cannot clear {}: {error}", folder.display());
    }
    std::fs::create_dir_all(&folder).unwrap();
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip(&vectors.join("guided/ruleset_seed.sqlite3.gz"))).unwrap();
    let path = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    let (mut rust_seen, mut python_seen) = (Seen::default(), Seen::default());
    let mut campaigns = HashMap::new();
    let mut failures = Vec::new();
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 40);
    for (index, item) in runs.iter().enumerate() {
        let got = match run(&handle, &item["step"], &mut campaigns).await {
            Ok(value) => json!({"ok": value}),
            Err(error) => failure(error),
        };
        let parsed = |text: String| serde_json::from_str::<Json>(&text).unwrap();
        let got = parsed(normalized(&got.to_string(), &mut rust_seen));
        let want = parsed(normalized(&item["outcome"].to_string(), &mut python_seen));
        if let Some(difference) = json_difference(&got, &want) {
            failures.push(format!("#{index} {}: {difference}", item["step"]));
        }
    }
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip(&vectors.join("campaign_creation/final.sqlite3.gz"))).unwrap();
    let (rust_rows, python_rows) =
        (table_rows(&database, &mut rust_seen), table_rows(&python, &mut python_seen));
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
    assert!(
        failures.is_empty(),
        "{} campaign creation steps differ:\n{}",
        failures.len(),
        failures.join("\n")
    );
}
