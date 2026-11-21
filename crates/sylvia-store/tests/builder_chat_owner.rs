//! Owner-level Builder chat rename and delete on the lane registry.
//!
//! The paired run (`tests/vectors/builder_chat_owner`, written by
//! `generate_builder_chat_owner_runs.py`) replays Python's `AuthorityOwner` operations through the
//! store handle: identity and confirmation refusals, the busy owner lane, and the store's own
//! outcomes, then compares the database with Python's. The concurrency tests show the lanes'
//! waiting behaviour, which a recorded run cannot: a held owner lane makes a delete refuse at
//! once, and a held session lane makes a rename or delete wait until it is released.

mod common;

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::Duration;
use sylvia_base::Json;
use sylvia_domain::session::{BuilderChatRecord, SessionRecord};
use sylvia_store::lanes::builder_chat_lane;
use sylvia_store::{AuthorityStore, LaneHold, LaneRegistry, StoreHandle, StoreResult, StoreThread};

fn gunzip(name: &str) -> Vec<u8> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(format!("tests/vectors/builder_chat_owner/{name}.sqlite3.gz"));
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(&path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

fn text(op: &Json, key: &str) -> String {
    op[key].as_str().unwrap_or_else(|| panic!("{key} in {op}")).to_owned()
}

fn session_json(record: &SessionRecord) -> Json {
    let s = record.fields();
    json!({
        "session_id": s.session_id, "campaign_id": s.campaign_id, "role": s.role.as_str(),
        "provider_id": s.provider_id, "model_id": s.model_id, "status": s.status.as_str(),
        "parent_session_id": s.parent_session_id, "selected_skill": s.selected_skill,
        "scope": s.scope, "reasoning_effort": s.reasoning_effort, "reasoning_effort_once": s.reasoning_effort_once,
    })
}

fn chat_json(chat: &BuilderChatRecord) -> Json {
    json!({"session": session_json(chat.session()), "display_name": chat.display_name(), "created_ordinal": chat.created_ordinal()})
}

/// A store thread on a copy of the seed, in a fresh folder.
fn open_seed(name: &str) -> (PathBuf, StoreHandle, StoreThread) {
    let folder = common::work_folder(name);
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip("seed")).unwrap();
    let path = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    (folder, handle, thread)
}

async fn run(handle: &StoreHandle, root: &Path, op: &Json) -> StoreResult<Json> {
    let session_id = op["session_id"].as_str().unwrap_or_default().to_owned();
    match op["op"].as_str().unwrap() {
        "make_workspace" => {
            let folder = root.join("builder-chats").join(&session_id).join("notes");
            std::fs::create_dir_all(&folder).unwrap();
            std::fs::write(folder.join("draft.txt"), "draft").unwrap();
            return Ok(Json::Null);
        }
        "workspace_exists" => return Ok(Json::Bool(root.join("builder-chats").join(&session_id).exists())),
        "builder_chat_index" => {
            return handle
                .call(|store| store.builder_chat_index())
                .await
                .map(|chats| Json::Array(chats.iter().map(chat_json).collect()));
        }
        _ => {}
    }
    // A running Builder turn holds the chat's owner lane.
    let _busy = if op["busy"].as_bool().unwrap() {
        Some(handle.lanes().owner.acquire(&builder_chat_lane(&session_id)).await)
    } else {
        None
    };
    match op["op"].as_str().unwrap() {
        "delete_builder_chat" => handle
            .delete_builder_chat(&session_id, &text(op, "confirmation"))
            .await
            .map(|chat| chat_json(&chat)),
        "rename_builder_chat" => handle
            .rename_builder_chat(&session_id, &text(op, "display_name"), op["automatic"].as_bool().unwrap())
            .await
            .map(|chat| chat_json(&chat)),
        other => panic!("unknown operation {other}"),
    }
}

fn check(label: &str, got: StoreResult<Json>, want: &Json) {
    match (got, want.get("error")) {
        (Ok(value), None) => assert_eq!(value, want["ok"], "{label}"),
        (Err(error), Some(kind)) => {
            let Some(coded) = error.rejection() else {
                panic!("{label}: Rust fault {error:?}, Python {want}")
            };
            assert_eq!(coded.error_type, kind.as_str(), "{label}: type");
            // Python's ValueError carries no code; its owner replies `command_rejected`.
            let code = want["code"].as_str().unwrap_or("command_rejected");
            assert_eq!(coded.code, code, "{label}: code");
            assert_eq!(coded.message, want["message"].as_str().unwrap(), "{label}: message");
        }
        (got, _) => panic!("{label}: Rust {got:?}, Python {want}"),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn owner_rename_and_delete_match_python() {
    let record = common::read_json("tests/vectors/builder_chat_owner/builder_chat_owner_runs.json");
    let (folder, handle, thread) = open_seed("builder-chat-owner-runs");
    let operations = record["operations"].as_array().unwrap();
    for (index, (op, want)) in operations.iter().zip(record["outcomes"].as_array().unwrap()).enumerate() {
        let outcome = run(&handle, &folder, op).await;
        check(&format!("#{index} {} {}", op["op"], op["session_id"]), outcome, want);
    }
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let rules = sylvia_compare::diff::Rules::defaults();
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip("owner_runs")).unwrap();
    let dump = |path: &Path| {
        sylvia_compare::diff::normalise(&sylvia_compare::dbdump::dump(path).unwrap(), &rules).unwrap()
    };
    let differences =
        sylvia_compare::diff::compare(&dump(&python), &dump(&folder.join("authority.sqlite3")), 20);
    assert!(differences.is_empty(), "database differs from Python's: {differences:#?}");
}

/// How long the session-lane test waits before checking its tasks are still blocked.
const SHORT: Duration = Duration::from_millis(200);
/// A bound for work that would never finish if it waited for a lane the test still holds, so
/// passing it within the bound proves no waiting. It is generous because the store write
/// itself can be slow on a loaded machine.
const NEVER: Duration = Duration::from_secs(30);

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn held_owner_lane_refuses_delete_and_does_not_hold_up_rename() {
    let (_folder, handle, _thread) = open_seed("builder-chat-owner-lane");
    let held = handle.lanes().owner.acquire(&builder_chat_lane("chat-a")).await;
    let refused =
        tokio::time::timeout(NEVER, handle.delete_builder_chat("chat-a", "permanently delete chat-a"))
            .await
            .expect("a busy delete is refused at once, never queued")
            .unwrap_err();
    assert_eq!(refused.rejection().unwrap().code, "builder_chat_work_in_progress");
    // Rename takes only the session lane, as Python's does.
    let renamed = tokio::time::timeout(NEVER, handle.rename_builder_chat("chat-a", "While busy", false))
        .await
        .expect("rename does not wait for the owner lane")
        .unwrap();
    assert_eq!(renamed.display_name(), "While busy");
    drop(held);
    let deleted = handle.delete_builder_chat("chat-a", "permanently delete chat-a").await.unwrap();
    assert_eq!(deleted.session().fields().session_id, "chat-a");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn held_session_lane_makes_rename_and_delete_wait() {
    let (_folder, handle, _thread) = open_seed("builder-chat-session-lane");
    let held = handle.lanes().sessions.acquire("chat-b").await;
    let other = handle.lanes().sessions.acquire("chat-c").await;
    let rename = tokio::spawn({
        let handle = handle.clone();
        async move { handle.rename_builder_chat("chat-b", "After the lane", false).await }
    });
    let delete = tokio::spawn({
        let handle = handle.clone();
        async move { handle.delete_builder_chat("chat-c", "permanently delete chat-c").await }
    });
    tokio::time::sleep(SHORT).await;
    assert!(!rename.is_finished(), "rename must wait for the session lane");
    assert!(!delete.is_finished(), "delete must wait for the session lane");
    // The waiting delete already holds chat-c's owner lane, so a second delete is refused.
    let second = handle.delete_builder_chat("chat-c", "permanently delete chat-c").await.unwrap_err();
    assert_eq!(second.rejection().unwrap().code, "builder_chat_work_in_progress");
    drop(held);
    assert_eq!(rename.await.unwrap().unwrap().display_name(), "After the lane");
    drop(other);
    assert_eq!(delete.await.unwrap().unwrap().session().fields().session_id, "chat-c");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn lane_registry_acquisition() {
    let registry = LaneRegistry::new();
    let clone = registry.clone();
    let guard = registry.acquire("campaign-1").await;
    assert_eq!(guard.key(), "campaign-1");
    // Clones share lanes; other keys are independent.
    assert!(clone.try_acquire("campaign-1").is_none());
    assert!(clone.try_acquire("campaign-2").is_some());
    // The held guard is the proof for re-entry: reused for its own key and registry only.
    assert!(guard.is_for(&clone, "campaign-1"));
    assert!(!guard.is_for(&LaneRegistry::new(), "campaign-1"));
    assert!(matches!(clone.acquire_unless_held(Some(&guard), "campaign-1").await, LaneHold::Reused(_)));
    let other = clone.acquire_unless_held(Some(&guard), "campaign-2").await;
    assert!(matches!(other, LaneHold::Acquired(_)));
    assert_eq!(other.guard().key(), "campaign-2");
    // A guard can be held across an await on another task.
    let moved = tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(20)).await;
        drop(guard);
    });
    let waited = tokio::time::timeout(Duration::from_secs(5), registry.acquire("campaign-1")).await;
    assert!(waited.is_ok(), "the lane is released when the guard is dropped");
    moved.await.unwrap();
    drop(waited);
    // Blocking acquisition for a plain thread.
    let blocking = registry.clone();
    let taken = std::thread::spawn(move || blocking.acquire_blocking("campaign-3").key().to_owned());
    assert_eq!(taken.join().unwrap(), "campaign-3");
}
