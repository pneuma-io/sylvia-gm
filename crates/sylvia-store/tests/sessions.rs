//! Paired runs for sessions and messages, part 1 (`tests/vectors/sessions`, written by
//! `generate_session_runs.py`): from Python's starting database, the Rust store runs each run's
//! operations and must give Python's outcomes and a database that dumps the same.

mod common;

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::WriterRole;
use sylvia_domain::session::{
    BuilderChatRecord, SessionContext, SessionMessage, SessionMessageKind, SessionRecord,
    SessionRecordFields, SessionStatus,
};
use sylvia_store::{AuthorityStore, StoreResult};

fn gunzip(name: &str) -> Vec<u8> {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join(format!("tests/vectors/sessions/{name}.sqlite3.gz"));
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(&path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

fn text(op: &Json, key: &str) -> String {
    op[key].as_str().unwrap_or_else(|| panic!("{key} in {op}")).to_owned()
}

fn optional(op: &Json, key: &str) -> Option<String> {
    op[key].as_str().map(str::to_owned)
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

fn message_json(message: &SessionMessage) -> Json {
    json!({
        "session_id": message.session_id(), "sequence": message.sequence(), "message_id": message.message_id(),
        "kind": message.kind().as_str(), "content": Json::Object(message.content().clone()),
        "canonical_sequence": message.canonical_sequence(),
    })
}

fn context_json(context: &SessionContext) -> Json {
    json!({"session": session_json(&context.session), "messages": context.messages.iter().map(message_json).collect::<Vec<_>>()})
}

fn record(op: &Json) -> StoreResult<SessionRecord> {
    let r = &op["record"];
    Ok(SessionRecord::new(SessionRecordFields {
        session_id: text(r, "session_id"),
        campaign_id: optional(r, "campaign_id"),
        role: WriterRole::parse(&text(r, "role")).unwrap(),
        provider_id: text(r, "provider_id"),
        model_id: text(r, "model_id"),
        status: SessionStatus::parse(&text(r, "status")).unwrap(),
        parent_session_id: optional(r, "parent_session_id"),
        selected_skill: optional(r, "selected_skill"),
        scope: r["scope"].as_array().unwrap().iter().map(|item| item.as_str().unwrap().to_owned()).collect(),
        reasoning_effort: optional(r, "reasoning_effort"),
        reasoning_effort_once: optional(r, "reasoning_effort_once"),
    })?)
}

fn run(store: &AuthorityStore, root: &Path, op: &Json) -> StoreResult<Json> {
    let session = || text(op, "session_id");
    match op["op"].as_str().unwrap() {
        "create_agent_session" => store.create_agent_session(&record(op)?).map(|value| session_json(&value)),
        "append_agent_message" => store
            .append_agent_message(
                &session(),
                &text(op, "message_id"),
                SessionMessageKind::parse(&text(op, "kind")).unwrap(),
                &op["content"].as_object().cloned().unwrap_or_else(JsonMap::new),
                op["canonical_sequence"].as_i64(),
            )
            .map(|value| message_json(&value)),
        "update_agent_session_status" => store
            .update_agent_session_status(&session(), SessionStatus::parse(&text(op, "status")).unwrap())
            .map(|value| session_json(&value)),
        "resume_agent_session" => store.resume_agent_session(&session()).map(|value| session_json(&value)),
        "update_agent_session_model" => store
            .update_agent_session_model(&session(), &text(op, "provider_id"), &text(op, "model_id"))
            .map(|value| session_json(&value)),
        "update_agent_session_reasoning" => store
            .update_agent_session_reasoning(&session(), op["effort"].as_str(), op["once"].as_str())
            .map(|value| session_json(&value)),
        "session_context" => store.session_context(&session()).map(|value| context_json(&value)),
        "create_builder_chat" => store
            .create_builder_chat(
                &session(),
                &text(op, "provider_id"),
                &text(op, "model_id"),
                &text(op, "display_name"),
            )
            .map(|value| chat_json(&value)),
        "builder_chat_index" => {
            store.builder_chat_index().map(|chats| Json::Array(chats.iter().map(chat_json).collect()))
        }
        "rename_builder_chat" => store
            .rename_builder_chat(&session(), &text(op, "display_name"), op["automatic"].as_bool().unwrap())
            .map(|value| chat_json(&value)),
        "delete_builder_chat" => store.delete_builder_chat(&session()).map(|value| chat_json(&value)),
        "make_workspace" => {
            let folder = root.join("builder-chats").join(session()).join("notes");
            std::fs::create_dir_all(&folder).unwrap();
            std::fs::write(folder.join("draft.txt"), "draft").unwrap();
            Ok(Json::Null)
        }
        "workspace_exists" => Ok(Json::Bool(root.join("builder-chats").join(session()).exists())),
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

#[test]
fn every_session_run_matches_python() {
    let record = common::read_json("tests/vectors/sessions/session_runs.json");
    let rules = sylvia_compare::diff::Rules::defaults();
    for (name, run_record) in record["runs"].as_object().unwrap() {
        let folder = common::work_folder(&format!("sessions-{name}"));
        let database = folder.join("authority.sqlite3");
        std::fs::write(&database, gunzip("seed")).unwrap();
        let mut store = AuthorityStore::open_for_application_owner(&database, None).unwrap();
        let operations = run_record["operations"].as_array().unwrap();
        for (index, (op, want)) in
            operations.iter().zip(run_record["outcomes"].as_array().unwrap()).enumerate()
        {
            check(&format!("{name} #{index} {}", op["op"]), run(&store, &folder, op), want);
        }
        store.close();
        let python = folder.join("python.sqlite3");
        std::fs::write(&python, gunzip(name)).unwrap();
        let reference =
            sylvia_compare::diff::normalise(&sylvia_compare::dbdump::dump(&python).unwrap(), &rules).unwrap();
        let candidate =
            sylvia_compare::diff::normalise(&sylvia_compare::dbdump::dump(&database).unwrap(), &rules)
                .unwrap();
        let differences = sylvia_compare::diff::compare(&reference, &candidate, 20);
        assert!(differences.is_empty(), "{name}: database differs from Python's: {differences:#?}");
    }
}
