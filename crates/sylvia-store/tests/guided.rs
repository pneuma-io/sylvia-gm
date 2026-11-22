//! Guided Campaign Drafts through the store handle, against Python's `AuthorityOwner`.
//!
//! The paired run (`tests/vectors/guided`, written by `generate_guided_draft_runs.py`) replays
//! `create_guided_campaign_draft` on a new profile: identity refusals, a first Draft, an exact
//! replay, conflicting replays, a missing Builder session and a reused one. A second run
//! (`generate_guided_advance_runs.py`) answers a Draft's Interview with
//! `advance_guided_campaign_draft`. Each outcome must be Python's, and the database afterwards
//! must match Python's. Steps that need an Interview Contribution are refused as not yet ported.

mod common;

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_base::Json;
use sylvia_store::{AuthorityStore, StoreHandle, StoreResult};

fn gunzip(name: &str) -> Vec<u8> {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(format!("tests/vectors/guided/{name}.sqlite3.gz"));
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(&path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
}

fn text<'a>(op: &'a Json, key: &str) -> &'a str {
    op[key].as_str().unwrap_or_else(|| panic!("{key} in {op}"))
}

fn draft_json(draft: &sylvia_domain::authority::GuidedCampaignDraft) -> Json {
    json!({
        "campaign_id": draft.campaign_id, "builder_session_id": draft.builder_session_id,
        "current_step": draft.current_step, "construction_revision": draft.construction_revision,
        "resulting_revision": draft.resulting_revision, "answer_message_id": draft.answer_message_id,
    })
}

/// The visible answer an operation names (a long one is stored as a repeat count).
fn visible(op: &Json) -> String {
    match &op["visible_answer"] {
        Json::String(text) => text.clone(),
        value => value["repeat"].as_str().unwrap().repeat(value["count"].as_u64().unwrap() as usize),
    }
}

async fn advance(handle: &StoreHandle, op: &Json, campaign_id: &str) -> StoreResult<Json> {
    let campaign = op["campaign_id"].as_str().unwrap_or(campaign_id);
    let draft = handle
        .advance_guided_campaign_draft(&sylvia_store::guided_advance::AdvanceAnswer {
            operation_id: text(op, "operation_id"),
            campaign_id: campaign,
            builder_session_id: text(op, "builder_session_id"),
            expected_construction_revision: op["expected_construction_revision"].as_i64().unwrap(),
            current_step: text(op, "current_step"),
            visible_answer: &visible(op),
            answer: &op["answer"],
        })
        .await?;
    Ok(draft_json(&draft))
}

async fn run(handle: &StoreHandle, op: &Json) -> StoreResult<Json> {
    assert_eq!(text(op, "op"), "create_guided_campaign_draft");
    let draft = handle
        .create_guided_campaign_draft(
            text(op, "operation_id"),
            text(op, "display_name"),
            text(op, "builder_session_id"),
            text(op, "builder_provider_id"),
            text(op, "builder_model_id"),
        )
        .await?;
    Ok(json!({
        "campaign_id": draft.campaign_id, "builder_session_id": draft.builder_session_id,
        "current_step": draft.current_step, "construction_revision": draft.construction_revision,
        "resulting_revision": draft.resulting_revision, "answer_message_id": draft.answer_message_id,
    }))
}

fn check(label: &str, got: StoreResult<Json>, want: &Json) {
    match (got, want.get("error")) {
        (Ok(value), None) => assert_eq!(value, want["ok"], "{label}"),
        (Err(error), Some(kind)) => {
            let Some(coded) = error.rejection() else {
                panic!("{label}: Rust fault {error:?}, Python {want}")
            };
            assert_eq!(coded.error_type, kind.as_str(), "{label}: type");
            assert_eq!(coded.code, want["code"].as_str().unwrap_or("command_rejected"), "{label}: code");
            assert_eq!(coded.message, want["message"].as_str().unwrap(), "{label}: message");
        }
        (got, _) => panic!("{label}: Rust {got:?}, Python {want}"),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn guided_drafts_match_python() {
    let record = common::read_json("tests/vectors/guided/draft_runs.json");
    let folder = common::work_folder("guided-draft-runs");
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip("seed")).unwrap();
    let path = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    let operations = record["operations"].as_array().unwrap();
    for (index, (op, want)) in operations.iter().zip(record["outcomes"].as_array().unwrap()).enumerate() {
        let outcome = run(&handle, op).await;
        check(&format!("#{index} {}", op["operation_id"]), outcome, want);
    }
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let rules = sylvia_compare::diff::Rules::defaults();
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip("draft_runs")).unwrap();
    let dump = |path: &Path| {
        sylvia_compare::diff::normalise(&sylvia_compare::dbdump::dump(path).unwrap(), &rules).unwrap()
    };
    let differences = sylvia_compare::diff::compare(&dump(&python), &dump(&database), 20);
    assert!(differences.is_empty(), "database differs from Python's: {differences:#?}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn guided_interview_answers_match_python() {
    let record = common::read_json("tests/vectors/guided/advance_runs.json");
    let folder = common::work_folder("guided-advance-runs");
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip("seed")).unwrap();
    let path = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    let mut campaign_id = String::new();
    let operations = record["operations"].as_array().unwrap();
    for (index, (op, want)) in operations.iter().zip(record["outcomes"].as_array().unwrap()).enumerate() {
        let label = format!("#{index} {}", op["operation_id"]);
        if op["op"] == "create_guided_campaign_draft" {
            let outcome = run(&handle, op).await;
            campaign_id = outcome.as_ref().unwrap()["campaign_id"].as_str().unwrap().to_owned();
            check(&label, outcome, want);
            continue;
        }
        let outcome = advance(&handle, op, &campaign_id).await;
        if op["not_ported"].as_bool().unwrap() {
            assert!(
                matches!(outcome, Err(sylvia_store::StoreError::NotPorted(_))),
                "{label}: a Contribution step must be refused as not yet ported, got {outcome:?}"
            );
            continue;
        }
        check(&label, outcome, want);
    }
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let rules = sylvia_compare::diff::Rules::defaults();
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip("advance_runs")).unwrap();
    let dump = |path: &Path| {
        sylvia_compare::diff::normalise(&sylvia_compare::dbdump::dump(path).unwrap(), &rules).unwrap()
    };
    let differences = sylvia_compare::diff::compare(&dump(&python), &dump(&database), 20);
    assert!(differences.is_empty(), "database differs from Python's: {differences:#?}");
}
