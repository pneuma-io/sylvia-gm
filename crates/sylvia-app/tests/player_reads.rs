//! The GM response context's player-action reads against Python
//! (`tests/vectors/player_reads/player_read_runs.json`, written by
//! `generate_player_read_runs.py`): on the three saved states of a played story campaign, every
//! `canonical_replay`, `player_action_context`, `player_action_response_context` and
//! `action_sequence_response_results` read must be Python's value, in Python's order, or
//! Python's refusal. Last, a narration state holding live attempts and pending tools must project
//! into `canonical_replay` exactly as Python's owner projected the same state.

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::narration::{NarrationAttempt, NarrationState};
use sylvia_app::authority::player_reads;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    CanonicalEvent, CanonicalReplay, PlayerProjection, PlayerProjectionKind, ProseChannel,
};
use sylvia_store::{AuthorityStore, StoreHandle, StoreResult};

const STORY: &str = "campaign-story";

fn vectors(relative: &str) -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/player_reads").join(relative)
}

fn object(class: &str, fields: Vec<(&str, Json)>) -> Json {
    let mut map = JsonMap::new();
    map.insert("__class__".into(), class.into());
    for (name, value) in fields {
        map.insert(name.into(), value);
    }
    Json::Object(map)
}

fn optional(value: Option<&str>) -> Json {
    value.map_or(Json::Null, Json::from)
}

fn event(event: &CanonicalEvent) -> Json {
    object(
        "CanonicalEvent",
        vec![
            ("sequence", event.sequence.into()),
            ("event_type", event.event_type.as_str().into()),
            ("turn_id", event.turn_id.as_str().into()),
            ("action_id", optional(event.action_id.as_deref())),
            ("safe_payload", Json::Object(event.safe_payload.clone())),
            ("visibility", event.visibility.as_str().into()),
            ("attempt_id", optional(event.attempt_id.as_deref())),
            ("attempt_ordinal", event.attempt_ordinal.map_or(Json::Null, Json::from)),
        ],
    )
}

fn projection(item: &PlayerProjection) -> Json {
    object(
        "PlayerProjection",
        vec![
            ("kind", item.kind.as_str().into()),
            ("sequence", item.sequence.map_or(Json::Null, Json::from)),
            ("text", optional(item.text.as_deref())),
            ("safe_payload", Json::Object(item.safe_payload.clone())),
            ("collapsed_by_default", item.collapsed_by_default.into()),
            ("provisional", item.provisional.into()),
            ("action_id", optional(item.action_id.as_deref())),
            ("attempt_id", optional(item.attempt_id.as_deref())),
        ],
    )
}

fn replay(replay: &CanonicalReplay) -> Json {
    object(
        "CanonicalReplay",
        vec![
            ("canonical", replay.canonical.iter().map(event).collect()),
            ("provisional", replay.provisional.iter().map(projection).collect()),
            ("high_water", replay.high_water.into()),
        ],
    )
}

fn outcome(read: StoreResult<Json>) -> Json {
    match read {
        Ok(value) => json!({ "ok": value }),
        Err(error) => {
            let coded = error.rejection().unwrap_or_else(|| panic!("an unexpected failure: {error}"));
            json!({"error": coded.error_type.unwrap_or("CommandRejected"), "code": coded.code, "message": coded.message})
        }
    }
}

/// Python's failure record has `code: null` for a `ValueError`; the Rust rejection's default code
/// is `command_rejected`.
fn comparable(mut value: Json) -> Json {
    if value["error"] == "ValueError" && value["code"].is_null() {
        value["code"] = "command_rejected".into();
    }
    value
}

fn records(rows: Vec<JsonMap>) -> Json {
    Json::Array(rows.into_iter().map(Json::Object).collect())
}

async fn open(name: &str) -> (StoreHandle, sylvia_store::StoreThread) {
    let folder =
        Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join(format!("player-reads-{name}"));
    // A folder left by an earlier run must go, or its files would look like this run's.
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
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(vectors(&format!("{name}.sqlite3.gz"))).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    std::fs::write(&database, bytes).unwrap();
    StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&database, None)).unwrap()
}

async fn close(handle: StoreHandle, thread: sylvia_store::StoreThread) {
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
}

fn record() -> Json {
    serde_json::from_str(&std::fs::read_to_string(vectors("player_read_runs.json")).unwrap()).unwrap()
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn player_reads_match_python() {
    let record = record();
    let narration = NarrationState::new();
    let mut failures = Vec::new();
    let mut count = 0;
    for phase in record["phases"].as_array().unwrap() {
        let name = phase["database"].as_str().unwrap();
        let (handle, thread) = open(name).await;
        for (index, run) in phase["runs"].as_array().unwrap().iter().enumerate() {
            count += 1;
            let got = match run["read"].as_str().unwrap() {
                "canonical_replay" => outcome(
                    player_reads::canonical_replay(
                        &handle,
                        &narration,
                        STORY,
                        run["acknowledged_sequence"].as_i64().unwrap(),
                    )
                    .await
                    .map(|value| replay(&value)),
                ),
                "player_action_context" => {
                    outcome(player_reads::player_action_context(&handle, STORY).await.map(records))
                }
                "player_action_response_context" => outcome(
                    player_reads::player_action_response_context(
                        &handle,
                        STORY,
                        run["basis"].as_object().unwrap(),
                    )
                    .await
                    .map(records),
                ),
                _ => outcome(
                    player_reads::action_sequence_response_results(
                        &handle,
                        run["session_id"].as_str().unwrap(),
                        run["basis_message_id"].as_str(),
                    )
                    .await
                    .map(records),
                ),
            };
            let want = comparable(run["outcome"].clone());
            // As text: object equality ignores key order, and Python's order is part of the value.
            let (got_text, want_text) = (got.to_string(), want.to_string());
            if got_text != want_text {
                failures.push(format!(
                    "{name} #{index} {}:\n  Rust   {got_text}\n  Python {want_text}",
                    run["read"]
                ));
            }
        }
        close(handle, thread).await;
    }
    assert_eq!(count, 54);
    assert!(failures.is_empty(), "{} of {count} reads differ:\n{}", failures.len(), failures.join("\n"));
}

/// The owner's live narration state reaches `canonical_replay` as Python's does: the campaign's
/// pending tools first, then the prose of its live attempts with a channel and a buffer.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn provisional_state_projects_as_pythons() {
    let record = record();
    let live = &record["provisional"];
    let narration = NarrationState::new();
    narration.with(|inner| {
        for item in live["pending_tools"].as_array().unwrap() {
            let (key, value) = (&item[0], &item[1]);
            let projection = PlayerProjection {
                kind: PlayerProjectionKind::parse(value["kind"].as_str().unwrap()).unwrap(),
                sequence: value["sequence"].as_i64(),
                text: value["text"].as_str().map(str::to_owned),
                safe_payload: value["safe_payload"].as_object().unwrap().clone(),
                collapsed_by_default: value["collapsed_by_default"].as_bool().unwrap(),
                provisional: value["provisional"].as_bool().unwrap(),
                action_id: value["action_id"].as_str().map(str::to_owned),
                attempt_id: value["attempt_id"].as_str().map(str::to_owned),
            };
            inner
                .pending_tools
                .push(((key[0].as_str().unwrap().into(), key[1].as_str().unwrap().into()), projection));
        }
        for item in live["attempts"].as_array().unwrap() {
            let (key, value) = (&item[0], &item[1]);
            let attempt = NarrationAttempt {
                campaign_id: value["campaign_id"].as_str().unwrap().into(),
                attempt_id: value["attempt_id"].as_str().unwrap().into(),
                turn_id: value["turn_id"].as_str().unwrap().into(),
                last_action_id: value["last_action_id"].as_str().map(str::to_owned),
                channel: value["channel"].as_str().map(|channel| ProseChannel::parse(channel).unwrap()),
                buffer: value["buffer"].as_str().unwrap().into(),
                terminal: value["terminal"].as_bool().unwrap(),
            };
            inner
                .attempts
                .push(((key[0].as_str().unwrap().into(), key[1].as_str().unwrap().into()), attempt));
        }
    });
    let (handle, thread) = open("mismatch").await;
    let high = live["outcome"]["ok"]["high_water"].as_i64().unwrap();
    let got = outcome(
        player_reads::canonical_replay(&handle, &narration, STORY, high).await.map(|value| replay(&value)),
    );
    close(handle, thread).await;
    assert_eq!(got.to_string(), live["outcome"].to_string());
    assert_eq!(
        got["ok"]["provisional"].as_array().unwrap().len(),
        3,
        "one pending tool and two live attempts"
    );
}
