//! Guided Campaign Drafts through the store handle, against Python's `AuthorityOwner`.
//!
//! The paired run (`tests/vectors/guided`, written by `generate_guided_draft_runs.py`) replays
//! `create_guided_campaign_draft` on a new profile: identity refusals, a first Draft, an exact
//! replay, conflicting replays, a missing Builder session and a reused one. A second run
//! (`generate_guided_advance_runs.py`) answers a Draft's Interview with
//! `advance_guided_campaign_draft`. Each outcome must be Python's, and the database afterwards
//! must match Python's. Steps that need an Interview Contribution are refused as not yet ported.

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::guided;
use sylvia_base::Json;
use sylvia_store::{AuthorityStore, StoreHandle, StoreResult};

/// A fresh, empty folder under Cargo's per-test temporary root.
fn work_folder(name: &str) -> std::path::PathBuf {
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join(name);
    // A folder left by an earlier run must go, or its files would look like this run's.
    if let Err(error) = std::fs::remove_dir_all(&folder) {
        assert_eq!(
            error.kind(),
            std::io::ErrorKind::NotFound,
            "cannot clear the old work folder {}: {error}",
            folder.display()
        );
    }
    std::fs::create_dir_all(&folder).expect("work folder");
    folder
}

/// The bundled package sources of this checkout, as the owner builds them at launch.
fn bundled() -> std::sync::Arc<sylvia_app::bundled_packages::BundledPackages> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
    std::sync::Arc::new(sylvia_app::bundled_packages::BundledPackages::new(
        sylvia_app::bundled_packages::BundledSources::from_repository_root(&root),
    ))
}

fn read_json(relative: &str) -> Json {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join(relative);
    serde_json::from_str(&std::fs::read_to_string(&path).expect("vector file")).expect("valid JSON")
}

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
    let draft = guided::advance_guided_campaign_draft(
        handle,
        &bundled(),
        &guided::AdvanceAnswer {
            operation_id: text(op, "operation_id"),
            campaign_id: campaign,
            builder_session_id: text(op, "builder_session_id"),
            expected_construction_revision: op["expected_construction_revision"].as_i64().unwrap(),
            current_step: text(op, "current_step"),
            visible_answer: &visible(op),
            answer: &op["answer"],
        },
    )
    .await?;
    Ok(draft_json(&draft))
}

async fn run(handle: &StoreHandle, op: &Json) -> StoreResult<Json> {
    assert_eq!(text(op, "op"), "create_guided_campaign_draft");
    let draft = guided::create_guided_campaign_draft(
        handle,
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
    let record = read_json("tests/vectors/guided/draft_runs.json");
    let folder = work_folder("guided-draft-runs");
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
    let record = read_json("tests/vectors/guided/advance_runs.json");
    let folder = work_folder("guided-advance-runs");
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

fn basis_json(basis: &sylvia_domain::authority::CampaignConstructionBasis) -> Json {
    json!({
        "campaign_id": basis.campaign_id, "display_name": basis.display_name, "lane": basis.lane,
        "canonical_input": basis.canonical_input, "canonical_plan": basis.canonical_plan,
        "authority_turn_id": basis.authority_turn_id, "package_lock_id": basis.package_lock_id,
        "package_candidate_id": basis.package_candidate_id, "builder_session_id": basis.builder_session_id,
        "begin_revision": basis.begin_revision, "completion_revision": basis.completion_revision,
        "canonical_representation_sha256": basis.canonical_representation_sha256,
    })
}

async fn start_step(
    handle: &StoreHandle,
    op: &Json,
    campaigns: &mut std::collections::HashMap<String, String>,
) -> StoreResult<Json> {
    let key = op["campaign"].as_str().unwrap_or_default();
    let campaign = campaigns.get(key).cloned().unwrap_or_else(|| key.to_owned());
    match text(op, "op") {
        "create_guided_campaign_draft" => {
            let value = run(handle, op).await?;
            campaigns.insert(
                text(op, "operation_id").to_owned(),
                value["campaign_id"].as_str().unwrap().to_owned(),
            );
            Ok(value)
        }
        "advance_guided_campaign_draft" => {
            let mut step = op.clone();
            step["campaign_id"] = Json::String(campaign);
            advance(handle, &step, "").await
        }
        "append_agent_message" => {
            let message = handle
                .append_agent_message(
                    text(op, "session_id"),
                    text(op, "message_id"),
                    sylvia_domain::session::SessionMessageKind::User,
                    op["content"].as_object().unwrap(),
                )
                .await?;
            Ok(json!({"message_id": message.message_id(), "sequence": message.sequence()}))
        }
        "bind_guided_campaign_basis" => {
            let lock = sylvia_domain::authority::PackageLock::new(Vec::new()).unwrap();
            let confirmation = guided::bind_guided_campaign_basis(
                handle,
                &bundled(),
                &guided::BindRequest {
                    operation_id: text(op, "operation_id"),
                    campaign_id: &campaign,
                    display_name: text(op, "display_name"),
                    package_lock: &lock,
                    turn_id: text(op, "turn_id"),
                    builder_session_id: text(op, "builder_session_id"),
                    package_candidate_id: None,
                },
            )
            .await?;
            Ok(
                json!({"campaign_id": confirmation.campaign_id, "resulting_revision": confirmation.resulting_revision}),
            )
        }
        "begin_campaign_construction" => {
            let begun = guided::begin_campaign_construction(
                handle,
                text(op, "operation_id"),
                &campaign,
                text(op, "lane"),
                op["expected_revision"].as_i64().unwrap(),
                &op["canonical_input"],
            )
            .await?;
            Ok(
                json!({"campaign_id": begun.campaign_id, "lane": begun.lane, "resulting_revision": begun.resulting_revision}),
            )
        }
        "campaign_construction_basis" => {
            Ok(basis_json(&guided::campaign_construction_basis(handle, &campaign).await?))
        }
        other => panic!("unknown operation {other}"),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn guided_start_matches_python() {
    let record = read_json("tests/vectors/guided/start_runs.json");
    let folder = work_folder("guided-start-runs");
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip("seed")).unwrap();
    let path = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    let mut campaigns = std::collections::HashMap::new();
    let operations = record["operations"].as_array().unwrap();
    for (index, (op, want)) in operations.iter().zip(record["outcomes"].as_array().unwrap()).enumerate() {
        let outcome = start_step(&handle, op, &mut campaigns).await;
        check(&format!("#{index} {} {}", op["op"], op["operation_id"]), outcome, want);
    }
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let rules = sylvia_compare::diff::Rules::defaults();
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip("start_runs")).unwrap();
    let dump = |path: &Path| {
        sylvia_compare::diff::normalise(&sylvia_compare::dbdump::dump(path).unwrap(), &rules).unwrap()
    };
    let differences = sylvia_compare::diff::compare(&dump(&python), &dump(&database), 20);
    assert!(differences.is_empty(), "database differs from Python's: {differences:#?}");
}

async fn finalize_step(
    handle: &StoreHandle,
    op: &Json,
    campaigns: &mut std::collections::HashMap<String, String>,
) -> StoreResult<Json> {
    if op["op"] != "finalize_campaign_construction" {
        return start_step(handle, op, campaigns).await;
    }
    let key = op["campaign"].as_str().unwrap();
    let campaign = campaigns.get(key).cloned().unwrap_or_else(|| key.to_owned());
    let (completed, plan, handoff) = guided::finalize_campaign_construction(
        handle,
        &guided::FinalizeConstruction {
            campaign_id: &campaign,
            dm_provider_id: text(op, "dm_provider_id"),
            dm_model_id: text(op, "dm_model_id"),
            plan_artifact_text: text(op, "plan_artifact_text"),
            structured_plan: op["structured_plan"].as_object(),
            model_guidance_override: op["model_guidance_override"].as_bool().unwrap(),
        },
    )
    .await?;
    let plan_fields = plan.fields();
    let handoff_fields = handoff.fields();
    Ok(json!({
        "completed": {"campaign_id": completed.campaign_id, "lane": completed.lane,
                      "canonical_representation_sha256": completed.canonical_representation_sha256,
                      "resulting_revision": completed.resulting_revision},
        "plan": {"plan_id": plan_fields.plan_id, "content_sha256": plan_fields.content_sha256,
                 "plan_revision": plan_fields.plan_revision,
                 "resulting_campaign_revision": plan_fields.resulting_campaign_revision,
                 "payload_sha256": plan_fields.payload_sha256},
        "handoff": {"handoff_id": handoff_fields.handoff_id, "dm_session_id": handoff_fields.dm_session_id,
                    "resulting_campaign_revision": handoff_fields.resulting_campaign_revision,
                    "payload_sha256": handoff_fields.payload_sha256},
    }))
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn guided_finalize_matches_python() {
    let record = read_json("tests/vectors/guided/finalize_runs.json");
    let folder = work_folder("guided-finalize-runs");
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip("seed")).unwrap();
    let path = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    let mut campaigns = std::collections::HashMap::new();
    let operations = record["operations"].as_array().unwrap();
    for (index, (op, want)) in operations.iter().zip(record["outcomes"].as_array().unwrap()).enumerate() {
        let outcome = finalize_step(&handle, op, &mut campaigns).await;
        check(&format!("#{index} {} {}", op["op"], op["operation_id"]), outcome, want);
    }
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let rules = sylvia_compare::diff::Rules::defaults();
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip("finalize_runs")).unwrap();
    let dump = |path: &Path| {
        sylvia_compare::diff::normalise(&sylvia_compare::dbdump::dump(path).unwrap(), &rules).unwrap()
    };
    let differences = sylvia_compare::diff::compare(&dump(&python), &dump(&database), 20);
    assert!(differences.is_empty(), "database differs from Python's: {differences:#?}");
}

async fn ruleset_step(
    handle: &StoreHandle,
    op: &Json,
    campaigns: &mut std::collections::HashMap<String, String>,
) -> StoreResult<Json> {
    let key = op["campaign"].as_str().unwrap_or_default();
    let campaign = campaigns.get(key).cloned().unwrap_or_else(|| key.to_owned());
    match text(op, "op") {
        "create_guided_campaign_draft" => {
            let value = run(handle, op).await?;
            campaigns.insert(
                text(op, "operation_id").to_owned(),
                value["campaign_id"].as_str().unwrap().to_owned(),
            );
            Ok(value)
        }
        "advance_guided_campaign_draft" => {
            let mut step = op.clone();
            step["campaign_id"] = Json::String(campaign);
            advance(handle, &step, "").await
        }
        "append_agent_message" => start_step(handle, op, campaigns).await,
        "bind_guided_campaign_basis" => {
            let candidate_id = text(op, "candidate").to_owned();
            let candidate = {
                let id = candidate_id.clone();
                handle.call(move |store| store.package_candidate(&id)).await?
            };
            let confirmation = guided::bind_guided_campaign_basis(
                handle,
                &bundled(),
                &guided::BindRequest {
                    operation_id: text(op, "operation_id"),
                    campaign_id: &campaign,
                    display_name: text(op, "display_name"),
                    package_lock: candidate.package_lock(),
                    turn_id: text(op, "turn_id"),
                    builder_session_id: text(op, "builder_session_id"),
                    package_candidate_id: Some(&candidate_id),
                },
            )
            .await?;
            Ok(
                json!({"campaign_id": confirmation.campaign_id, "resulting_revision": confirmation.resulting_revision}),
            )
        }
        "begin_campaign_construction" => start_step(handle, op, campaigns).await,
        "finalize_campaign_construction" => {
            let (completed, plan, handoff) = guided::finalize_campaign_construction(
                handle,
                &guided::FinalizeConstruction {
                    campaign_id: &campaign,
                    dm_provider_id: text(op, "dm_provider_id"),
                    dm_model_id: text(op, "dm_model_id"),
                    plan_artifact_text: text(op, "plan_artifact_text"),
                    structured_plan: op["structured_plan"].as_object(),
                    model_guidance_override: op["model_guidance_override"].as_bool().unwrap(),
                },
            )
            .await?;
            Ok(json!({
                "completion_revision": completed.resulting_revision, "plan_id": plan.fields().plan_id,
                "plan_payload_sha256": plan.fields().payload_sha256, "handoff_id": handoff.fields().handoff_id,
                "handoff_payload_sha256": handoff.fields().payload_sha256,
                "handoff_revision": handoff.fields().resulting_campaign_revision,
            }))
        }
        other => panic!("unknown operation {other}"),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn guided_ruleset_matches_python() {
    let record = read_json("tests/vectors/guided/ruleset_runs.json");
    let folder = work_folder("guided-ruleset-runs");
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip("ruleset_seed")).unwrap();
    let path = database.clone();
    let (handle, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    let mut campaigns = std::collections::HashMap::new();
    let operations = record["operations"].as_array().unwrap();
    for (index, (op, want)) in operations.iter().zip(record["outcomes"].as_array().unwrap()).enumerate() {
        let outcome = ruleset_step(&handle, op, &mut campaigns).await;
        check(&format!("#{index} {} {}", op["op"], op["operation_id"]), outcome, want);
    }
    drop(handle);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let rules = sylvia_compare::diff::Rules::defaults();
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip("ruleset_runs")).unwrap();
    let dump = |path: &Path| {
        sylvia_compare::diff::normalise(&sylvia_compare::dbdump::dump(path).unwrap(), &rules).unwrap()
    };
    let differences = sylvia_compare::diff::compare(&dump(&python), &dump(&database), 20);
    assert!(differences.is_empty(), "database differs from Python's: {differences:#?}");
}
