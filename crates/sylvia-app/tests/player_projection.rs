//! The player's projection and a session's stop against Python
//! (`tests/vectors/player_projection/player_projection_runs.json`, written by
//! `generate_player_projection_runs.py`): two scenarios, each step replayed through
//! `sylvia_app::authority::player_projection` and `StoreHandle::stop_agent_session`, each answer
//! compared with `json_difference` (interruption identities by order of appearance), then the
//! final database row for row.

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::actions::ActionError;
use sylvia_app::authority::narration_attempts::{self as narration, NarrationStart};
use sylvia_app::authority::player_projection::{player_projection, player_projection_page};
use sylvia_base::testing::json_difference;
use sylvia_base::Json;
use sylvia_domain::authority::{PlayerProjection, ProseChannel};
use sylvia_domain::session::SessionMessageKind;
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

/// `_json_value` of a projection: its fields, in order.
fn projection(item: &PlayerProjection) -> Json {
    json!({
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

/// `text` with each `interruption-<uuid>` replaced by its order of first appearance in `seen`.
fn normalized(text: &str, seen: &mut Vec<String>) -> String {
    let mut out = String::new();
    let mut rest = text;
    while let Some(at) = rest.find("interruption-") {
        let (before, after) = rest.split_at(at);
        out.push_str(before);
        let candidate = &after["interruption-".len()..];
        let id_len = candidate.chars().take_while(|c| c.is_ascii_hexdigit() || *c == '-').count();
        if id_len == 36 {
            let id = &after[..("interruption-".len() + 36)];
            let index = match seen.iter().position(|known| known == id) {
                Some(index) => index,
                None => {
                    seen.push(id.to_owned());
                    seen.len() - 1
                }
            };
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
            let mut rows: Vec<String> = statement
                .query_map([], |row| {
                    let values: Vec<rusqlite::types::Value> =
                        (0..width).map(|index| row.get(index)).collect::<Result<_, _>>()?;
                    Ok(format!("{values:?}"))
                })
                .unwrap()
                .collect::<Result<_, _>>()
                .unwrap();
            let mut rows: Vec<String> = rows.drain(..).map(|row| normalized(&row, seen)).collect();
            rows.sort();
            (table, rows)
        })
        .collect()
}

async fn run(store: &StoreHandle, step: &Json) -> Result<Json, ActionError> {
    let text = |key: &str| step[key].as_str().unwrap().to_owned();
    let list = |items: Vec<PlayerProjection>| Json::Array(items.iter().map(projection).collect());
    Ok(match step["op"].as_str().unwrap() {
        "projection" => list(player_projection(store, CAMPAIGN).await?),
        "projection_of" => list(player_projection(store, &text("campaign")).await?),
        "page" => list(player_projection_page(store, CAMPAIGN, step.get("after")).await?),
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
        "emit" => {
            let channel = ProseChannel::parse(step["channel"].as_str().unwrap()).unwrap();
            narration::emit_narration_token(store, CAMPAIGN, &text("attempt"), channel, &text("token"), None)
                .await?;
            Json::Null
        }
        "complete" => {
            narration::complete_narration_attempt(store, CAMPAIGN, &text("attempt")).await?.len().into()
        }
        "interrupt" => {
            narration::interrupt_narration(store, CAMPAIGN, &text("attempt")).await?.failed_attempt_id.into()
        }
        "append" => {
            let kind = SessionMessageKind::parse(step["kind"].as_str().unwrap()).unwrap();
            store
                .append_agent_message(
                    &text("session"),
                    &text("message"),
                    kind,
                    step["content"].as_object().unwrap(),
                )
                .await?
                .sequence()
                .into()
        }
        "stop" => {
            let record = store.stop_agent_session(&text("session")).await?;
            json!({"session_id": record.session_id(), "status": record.fields().status.as_str()})
        }
        other => panic!("unknown step {other}"),
    })
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn projections_and_stops_answer_as_pythons_owner() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/player_projection");
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors.join("player_projection_runs.json")).unwrap())
            .unwrap();
    let scenarios = record["scenarios"].as_array().unwrap();
    assert_eq!(scenarios.len(), 2);
    let mut failures = Vec::new();
    for scenario in scenarios {
        let name = scenario["name"].as_str().unwrap();
        let folder =
            Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join(format!("projection-{name}"));
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
        handle.narration().set_disconnect_grace(5.0).unwrap();
        let (mut rust_ids, mut python_ids) = (Vec::new(), Vec::new());
        for (index, item) in scenario["runs"].as_array().unwrap().iter().enumerate() {
            let got = match run(&handle, &item["step"]).await {
                Ok(value) => json!({"ok": value}),
                Err(error) => failure(error),
            };
            let parsed = |text: String| serde_json::from_str::<Json>(&text).unwrap();
            let got = parsed(normalized(&got.to_string(), &mut rust_ids));
            let want = parsed(normalized(&item["outcome"].to_string(), &mut python_ids));
            if let Some(difference) = json_difference(&got, &want) {
                failures.push(format!("{name} #{index} {}: {difference}", item["step"]));
            }
        }
        drop(handle);
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
        let python = folder.join("python.sqlite3");
        std::fs::write(&python, gunzip(&vectors.join(name).join("final.sqlite3.gz"))).unwrap();
        let (rust_rows, python_rows) =
            (table_rows(&database, &mut rust_ids), table_rows(&python, &mut python_ids));
        if rust_rows.keys().ne(python_rows.keys()) {
            failures.push(format!("{name}: the final tables differ from Python's"));
        }
        for (table, rows) in &python_rows {
            if rust_rows.get(table) != Some(rows) {
                failures.push(format!("{name}: the final {table} rows differ from Python's"));
            }
        }
    }
    assert!(failures.is_empty(), "{} projection steps differ:\n{}", failures.len(), failures.join("\n"));
}
