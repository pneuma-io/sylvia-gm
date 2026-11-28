//! Compare `sylvia_app::plan_snapshot_materializer` with the frozen Python, on an owner holding
//! the same installed candidates.
//!
//! Regenerate with `tests/vectors/generate_plan_snapshot_draft_vectors.py` (needs the snapshot's
//! Python).

use std::path::{Path, PathBuf};
use sylvia_app::campaign_plan_sources::campaign_plan_sources;
use sylvia_app::plan_snapshot_materializer::{
    check_plan_draft, materialize_plan_snapshot_draft, MaterializedPlanSnapshotDraft,
};
use sylvia_base::hash::sha256_hex;
use sylvia_base::testing::json_difference;
use sylvia_base::{pyjson, Json};
use sylvia_store::{AuthorityStore, StoreError};

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// The generator's `failure`: class, message, code and diagnostics (a `ValueError` has no code).
fn failure(error: &StoreError) -> Json {
    match error.rejection() {
        Some(coded) => {
            let class = coded.error_type.unwrap_or("CommandRejected");
            let value_error = matches!(
                class,
                "ValueError"
                    | "JSONDecodeError"
                    | "ContentShapeError"
                    | "UnicodeDecodeError"
                    | "CampaignPlanFieldsError"
            );
            serde_json::json!({
                "error": class,
                "message": coded.message,
                "code": if value_error { Json::Null } else { Json::from(coded.code) },
                "diagnostics": coded.diagnostics,
            })
        }
        None => serde_json::json!({ "error": format!("{error}") }),
    }
}

fn outcome(result: Result<Json, StoreError>) -> Json {
    match result {
        Ok(value) => serde_json::json!({ "ok": value }),
        Err(error) => failure(&error),
    }
}

fn materialized_payload(materialized: &MaterializedPlanSnapshotDraft) -> Json {
    serde_json::json!({
        "closure_sha256": materialized.closure_sha256,
        "archive_sha256": sha256_hex(&materialized.archive),
        "plan": {
            "content_sha256": materialized.plan.content_sha256,
            "artifact_id": materialized.plan.artifact_id,
            "bindings": materialized.plan.payload["bindings"],
        },
    })
}

/// The generator's `write_files`: text, `hex` bytes, a `repeat`, a `pad_to`, or a folder.
fn write_files(workspace: &Path, files: &Json) {
    for (relative, value) in files.as_object().unwrap() {
        let path = workspace.join(relative);
        if value.is_null() {
            std::fs::create_dir_all(&path).unwrap();
            continue;
        }
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        let bytes = match value {
            Json::String(text) => text.as_bytes().to_vec(),
            Json::Object(form) if form.contains_key("hex") => {
                let hex = form["hex"].as_str().unwrap();
                (0..hex.len())
                    .step_by(2)
                    .map(|at| u8::from_str_radix(&hex[at..at + 2], 16).unwrap())
                    .collect()
            }
            Json::Object(form) if form.contains_key("pad_to") => {
                let text = form["text"].as_str().unwrap();
                let width = form["pad_to"].as_u64().unwrap() as usize;
                // `text.ljust(width)`: padded to `width` characters.
                let padding = width.saturating_sub(text.chars().count());
                format!("{text}{}", " ".repeat(padding)).into_bytes()
            }
            Json::Object(form) => {
                form["repeat"].as_str().unwrap().repeat(form["count"].as_u64().unwrap() as usize).into_bytes()
            }
            other => panic!("unexpected workspace file {other}"),
        };
        std::fs::write(&path, bytes).unwrap();
    }
}

#[test]
fn plan_snapshot_drafts_and_checks_match_python() {
    let record = pyjson::loads(include_str!("vectors/plan_snapshot_drafts/vectors.json"))
        .expect("vector file is valid JSON");
    let root = std::env::temp_dir().join(format!("sgps-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&root).unwrap();
    let store = AuthorityStore::open_for_application_owner(&root.join("plan.sqlite3"), None).unwrap();
    let kept = repository().join("crates/sylvia-app/tests/vectors/plan_snapshot_drafts/candidates");
    for install_id in record["install_order"].as_array().unwrap() {
        let install_id = install_id.as_str().unwrap();
        let archive = std::fs::read(kept.join(format!("{install_id}.archive"))).unwrap();
        sylvia_app::package_library::install_package_archive(&store, &archive, install_id).unwrap();
    }
    let setting = record["ids"]["setting"].as_str().unwrap();
    let candidate = store.package_candidate(setting).unwrap();
    let authority = campaign_plan_sources(&store, candidate.package_lock(), None).unwrap().unwrap();
    let mut failures = Vec::new();
    for (index, step) in record["steps"].as_array().unwrap().iter().enumerate() {
        let name = step["name"].as_str().unwrap();
        let workspace = root.join("workspaces").join(index.to_string());
        std::fs::create_dir_all(&workspace).unwrap();
        write_files(&workspace, &step["files"]);
        let candidate_id = step["candidate"].as_str().unwrap();
        let got = if step["op"] == "check" {
            outcome(
                check_plan_draft(&store, &workspace, candidate_id, &authority, step["complete"] == true)
                    .map(Json::Object),
            )
        } else {
            outcome(
                materialize_plan_snapshot_draft(&store, &workspace, candidate_id, &authority)
                    .map(|materialized| materialized_payload(&materialized)),
            )
        };
        if let Some(difference) = json_difference(&got, &step["outcome"]) {
            failures.push(format!("{name}: {difference}"));
        }
    }
    let mut after: Vec<(String, String)> = store
        .package_candidates()
        .unwrap()
        .iter()
        .map(|item| (item.candidate_id().to_owned(), item.closure_sha256().to_owned()))
        .collect();
    after.sort();
    let after =
        Json::Array(after.into_iter().map(|(id, closure)| serde_json::json!([id, closure])).collect());
    if let Some(difference) = json_difference(&after, &record["candidates_after"]) {
        failures.push(format!("candidates after: {difference}"));
    }
    drop(authority);
    drop(store);
    let _ = std::fs::remove_dir_all(&root);
    assert!(failures.is_empty(), "{} plan snapshot steps differ:\n{}", failures.len(), failures.join("\n"));
}
