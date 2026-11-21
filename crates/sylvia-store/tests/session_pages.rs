//! Paired runs for sessions part 2 (`tests/vectors/session_pages`, written by
//! `generate_session_page_runs.py`): history pages with chapter markers, the ending card and
//! mechanics activity, activity bases, plan reads, human review and Builder child sessions. From
//! Python's starting database, the Rust store runs each run's operations and must give Python's
//! outcomes and a database that dumps the same.

mod common;

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::WriterRole;
use sylvia_domain::model_capability::model_guidance_payload;
use sylvia_domain::session::{
    BuilderDMHandoff, CampaignPlan, ChildEvidence, SessionActivityBasis, SessionHistoryPage, SessionMessage,
    SessionRecord, SessionRecordFields, SessionStatus,
};
use sylvia_store::{AuthorityStore, StoreResult};

fn gunzip(name: &str) -> Vec<u8> {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join(format!("tests/vectors/session_pages/{name}.sqlite3.gz"));
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

fn message_json(message: &SessionMessage) -> Json {
    json!({
        "session_id": message.session_id(), "sequence": message.sequence(), "message_id": message.message_id(),
        "kind": message.kind().as_str(), "content": Json::Object(message.content().clone()),
        "canonical_sequence": message.canonical_sequence(),
    })
}

fn maps(items: &[JsonMap]) -> Json {
    Json::Array(items.iter().cloned().map(Json::Object).collect())
}

fn page_json(page: &SessionHistoryPage) -> Json {
    json!({
        "session": session_json(&page.session),
        "messages": page.messages.iter().map(message_json).collect::<Vec<_>>(),
        "next_before_sequence": page.next_before_sequence,
        "latest_sequence": page.latest_sequence,
        "latest_user_sequence": page.latest_user_sequence,
        "launch_messages": page.launch_messages.iter().map(message_json).collect::<Vec<_>>(),
        "latest_canonical_sequence": page.latest_canonical_sequence,
        "act_markers": maps(&page.act_markers),
        "campaign_ending": page.campaign_ending.clone().map(Json::Object),
        "campaign_endings": maps(&page.campaign_endings),
        "builder_question_messages": page.builder_question_messages.iter().map(message_json).collect::<Vec<_>>(),
    })
}

fn basis_json(basis: &SessionActivityBasis) -> Json {
    json!({
        "session": session_json(&basis.session),
        "accepted": basis.accepted.as_ref().map(message_json),
        "terminal": basis.terminal.as_ref().map(message_json),
        "has_compaction": basis.has_compaction,
    })
}

fn plan_json(plan: &CampaignPlan) -> Json {
    let f = plan.fields();
    json!({
        "plan_id": f.plan_id, "campaign_id": f.campaign_id, "package_lock_id": f.package_lock_id,
        "source_session_id": f.source_session_id, "content_sha256": f.content_sha256,
        "plan_revision": f.plan_revision, "resulting_campaign_revision": f.resulting_campaign_revision,
        "payload_sha256": f.payload_sha256, "content": Json::Object(plan.content().clone()),
        "provenance_evidence_ids": f.provenance_evidence_ids,
        "validation_outcome": f.validation_outcome.as_str(), "acceptance_status": f.acceptance_status.as_str(),
    })
}

fn handoff_json(handoff: &BuilderDMHandoff) -> Json {
    let h = handoff.fields();
    json!({
        "handoff_id": h.handoff_id, "campaign_id": h.campaign_id, "builder_session_id": h.builder_session_id,
        "dm_session_id": h.dm_session_id, "package_lock_id": h.package_lock_id,
        "package_candidate_id": h.package_candidate_id, "plan_id": h.plan_id, "plan_revision": h.plan_revision,
        "plan_content_sha256": h.plan_content_sha256, "expected_revision": h.expected_revision,
        "resulting_campaign_revision": h.resulting_campaign_revision, "chronology_ordinal": h.chronology_ordinal,
        "completed_turn_id": h.completed_turn_id, "payload_sha256": h.payload_sha256,
        "model_guidance": Json::Object(model_guidance_payload(&h.model_guidance)),
    })
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

fn evidence(op: &Json) -> StoreResult<ChildEvidence> {
    let e = &op["evidence"];
    Ok(ChildEvidence {
        evidence_id: text(e, "evidence_id"),
        child_session_id: text(e, "child_session_id"),
        parent_session_id: text(e, "parent_session_id"),
        content_sha256: text(e, "content_sha256"),
        safe_summary: text(e, "safe_summary"),
        claims: e["claims"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| item.as_str().unwrap().to_owned())
            .collect(),
        authoritative: false,
    }
    .validated()?)
}

fn run(store: &AuthorityStore, op: &Json) -> StoreResult<Json> {
    let session = || text(op, "session_id");
    match op["op"].as_str().unwrap() {
        "session_history_page" => store
            .session_history_page(&session(), op["before_sequence"].as_i64(), op["limit"].as_i64().unwrap())
            .map(|page| page_json(&page)),
        "session_activity_basis" => store
            .session_activity_basis(&session(), op["attempt_id"].as_str())
            .map(|basis| basis_json(&basis)),
        "campaign_plan_hit" => store
            .campaign_plan_hit(&text(op, "plan_id"))
            .map(|plan| plan.as_ref().map_or(Json::Null, plan_json)),
        "accepted_campaign_plan" => store
            .accepted_campaign_plan(&text(op, "campaign_id"))
            .map(|plan| plan.as_ref().map_or(Json::Null, plan_json)),
        "campaign_adventures" => store
            .campaign_adventures(&text(op, "campaign_id"))
            .map(|plans| Json::Array(plans.iter().map(plan_json).collect())),
        "handoff_hit" => store
            .handoff_hit(&text(op, "handoff_id"))
            .map(|handoff| handoff.as_ref().map_or(Json::Null, handoff_json)),
        "create_builder_child_session" => {
            store.create_builder_child_session(&record(op)?).map(|value| session_json(&value))
        }
        "return_builder_child_evidence" => store
            .return_builder_child_evidence(&evidence(op)?)
            .map(|(captured, completed)| json!([Json::Object(captured.payload()), session_json(&completed)])),
        "require_agent_human_review" => store
            .require_agent_human_review(
                &session(),
                &text(op, "review_id"),
                op["content"].as_object().unwrap(),
            )
            .map(|value| session_json(&value)),
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
fn every_session_page_run_matches_python() {
    let record = common::read_json("tests/vectors/session_pages/session_page_runs.json");
    let rules = sylvia_compare::diff::Rules::defaults();
    for (name, run_record) in record["runs"].as_object().unwrap() {
        let folder = common::work_folder(&format!("session-pages-{name}"));
        let database = folder.join("authority.sqlite3");
        std::fs::write(&database, gunzip("seed")).unwrap();
        let mut store = AuthorityStore::open_for_application_owner(&database, None).unwrap();
        let operations = run_record["operations"].as_array().unwrap();
        for (index, (op, want)) in
            operations.iter().zip(run_record["outcomes"].as_array().unwrap()).enumerate()
        {
            check(&format!("{name} #{index} {}", op["op"]), run(&store, op), want);
        }
        store.close();
        let python = folder.join("python.sqlite3");
        std::fs::write(&python, gunzip(name)).unwrap();
        let dump = |path: &Path| {
            sylvia_compare::diff::normalise(&sylvia_compare::dbdump::dump(path).unwrap(), &rules).unwrap()
        };
        let differences = sylvia_compare::diff::compare(&dump(&python), &dump(&database), 20);
        assert!(differences.is_empty(), "{name}: database differs from Python's: {differences:#?}");
    }
}
