//! Finishing a construction one operation at a time against Python
//! (`tests/vectors/construction_handoff/runs.json`, written by
//! `generate_construction_handoff_runs.py` on its own `base.sqlite3.gz`): Builder evidence, Plan
//! admission, the Builder-to-DM handoff, their reads, and the prepared and legacy branch of
//! `finalize_campaign_construction`. Each answer is compared with `json_difference`, then the
//! final database row for row. The base holds the campaigns, so every identity is Python's.

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::BTreeMap;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::construction_handoff::{self as handoff, AdmitPlan, HandoffRequest};
use sylvia_app::authority::guided::{finalize_campaign_construction, FinalizeConstruction};
use sylvia_base::testing::json_difference;
use sylvia_base::Json;
use sylvia_domain::authority::WriterRole;
use sylvia_domain::model_capability::model_guidance_payload;
use sylvia_domain::session::{
    BuilderDMHandoff, BuilderEvidence, CampaignPlan, EvidenceSourceKind, PlanAcceptanceStatus,
    PlanValidationOutcome,
};
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

fn plan_json(plan: &CampaignPlan) -> Json {
    let fields = plan.fields();
    json!({
        "plan_id": fields.plan_id, "campaign_id": fields.campaign_id, "package_lock_id": fields.package_lock_id,
        "source_session_id": fields.source_session_id, "content_sha256": fields.content_sha256,
        "content": Json::Object(plan.content().clone()), "provenance_evidence_ids": fields.provenance_evidence_ids,
        "plan_revision": fields.plan_revision, "resulting_campaign_revision": fields.resulting_campaign_revision,
        "payload_sha256": fields.payload_sha256,
    })
}

fn handoff_json(handoff: &BuilderDMHandoff) -> Json {
    let fields = handoff.fields();
    json!({
        "handoff_id": fields.handoff_id, "campaign_id": fields.campaign_id,
        "builder_session_id": fields.builder_session_id, "dm_session_id": fields.dm_session_id,
        "package_lock_id": fields.package_lock_id, "package_candidate_id": fields.package_candidate_id,
        "plan_id": fields.plan_id, "plan_revision": fields.plan_revision, "expected_revision": fields.expected_revision,
        "resulting_campaign_revision": fields.resulting_campaign_revision,
        "completed_turn_id": fields.completed_turn_id, "payload_sha256": fields.payload_sha256,
    })
}

fn table_rows(path: &Path) -> BTreeMap<String, Vec<String>> {
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
            rows.sort();
            (table, rows)
        })
        .collect()
}

async fn run(store: &StoreHandle, step: &Json, campaigns: &Json) -> Result<Json, StoreError> {
    let text = |key: &str| step[key].as_str().unwrap().to_owned();
    let campaign = |key: &str| campaigns[text("campaign")][key].as_str().unwrap().to_owned();
    Ok(match step["op"].as_str().unwrap() {
        "plan" => plan_json(&handoff::campaign_plan(store, &text("plan_id")).await?),
        "handoff_read" => handoff_json(&handoff::builder_dm_handoff(store, &text("handoff_id")).await?),
        "evidence" => {
            let evidence = BuilderEvidence {
                evidence_id: text("evidence_id"),
                source_kind: EvidenceSourceKind::parse(&text("source_kind")).unwrap(),
                content_sha256: text("content_sha256"),
                source_locator: step["source_locator"].as_str().map(str::to_owned),
                safe_excerpt: text("safe_excerpt"),
                claims: step["claims"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|c| c.as_str().unwrap().to_owned())
                    .collect(),
            };
            Json::Object(store.record_builder_evidence(evidence, &text("session")).await?.payload())
        }
        "admit" => {
            let lock = step["lock"].as_str().map_or_else(|| campaign("lock_id"), str::to_owned);
            let provenance: Vec<String> = step["provenance"]
                .as_array()
                .unwrap()
                .iter()
                .map(|id| id.as_str().unwrap().to_owned())
                .collect();
            let plan = handoff::admit_campaign_plan(
                store,
                &AdmitPlan {
                    plan_id: &text("plan_id"),
                    campaign_id: &campaign("campaign_id"),
                    builder_session_id: &text("session"),
                    expected_revision: step["expected_revision"].as_i64().unwrap(),
                    expected_package_lock_id: &lock,
                    content: step["content"].as_object().unwrap(),
                    content_sha256: &text("content_sha256"),
                    provenance_evidence_ids: &provenance,
                    validation_outcome: PlanValidationOutcome::Valid,
                    acceptance_status: PlanAcceptanceStatus::Accepted,
                },
            )
            .await?;
            plan_json(&plan)
        }
        "handoff" => {
            let handed = handoff::handoff_builder_to_dm(
                store,
                &HandoffRequest {
                    handoff_id: &text("handoff_id"),
                    campaign_id: &campaign("campaign_id"),
                    builder_session_id: &text("builder"),
                    dm_session_id: &text("dm_session"),
                    dm_provider_id: "standin",
                    dm_model_id: &text("dm_model"),
                    expected_writer: WriterRole::Builder,
                    expected_revision: step["expected_revision"].as_i64().unwrap(),
                    expected_package_lock_id: &campaign("lock_id"),
                    expected_package_candidate_id: None,
                    plan_id: &text("plan_id"),
                    expected_plan_revision: step["plan_revision"].as_i64().unwrap(),
                    expected_plan_content_sha256: &text("plan_sha"),
                    completed_turn_id: &text("turn"),
                    model_guidance_override: false,
                },
            )
            .await?;
            handoff_json(&handed)
        }
        "guidance" => {
            let campaign_id = match step.get("campaign_id") {
                Some(id) => id.as_str().unwrap().to_owned(),
                None => campaign("campaign_id"),
            };
            let guidance = handoff::model_guidance(
                store,
                &campaign_id,
                &text("model"),
                step["override"].as_bool().unwrap(),
            )
            .await?;
            json!({"payload": Json::Object(model_guidance_payload(&guidance)), "may_continue": guidance.may_continue()})
        }
        "complete_turn" => {
            sylvia_app::authority::turns::complete_turn(store, &campaign("campaign_id"), &text("turn"))
                .await?;
            Json::Null
        }
        "finalize" => {
            let (completed, plan, handed) = finalize_campaign_construction(
                store,
                &FinalizeConstruction {
                    campaign_id: &campaign("campaign_id"),
                    dm_provider_id: "standin",
                    dm_model_id: "standin/standin-narrator",
                    plan_artifact_text: &text("plan_text"),
                    structured_plan: None,
                    model_guidance_override: step["override"].as_bool().unwrap(),
                },
            )
            .await?;
            json!({
                "completed": {
                    "campaign_id": completed.campaign_id, "lane": completed.lane,
                    "canonical_representation_sha256": completed.canonical_representation_sha256,
                    "resulting_revision": completed.resulting_revision,
                },
                "plan": plan_json(&plan),
                "handoff": handoff_json(&handed),
            })
        }
        other => panic!("unknown step {other}"),
    })
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn constructions_finish_as_pythons_owner() {
    let vectors = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/construction_handoff");
    let record: Json =
        serde_json::from_str(&std::fs::read_to_string(vectors.join("runs.json")).unwrap()).unwrap();
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("sylvia-app").join("construction-handoff");
    if let Err(error) = std::fs::remove_dir_all(&folder) {
        assert_eq!(error.kind(), std::io::ErrorKind::NotFound, "cannot clear {}: {error}", folder.display());
    }
    std::fs::create_dir_all(&folder).unwrap();
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, gunzip(&vectors.join("base.sqlite3.gz"))).unwrap();
    let path = database.clone();
    let (store, thread) =
        StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&path, None)).unwrap();
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 46);
    let mut failures = Vec::new();
    for (index, item) in runs.iter().enumerate() {
        let got = match run(&store, &item["step"], &record["campaigns"]).await {
            Ok(value) => json!({"ok": value}),
            Err(error) => failure(error),
        };
        if let Some(difference) = json_difference(&got, &item["outcome"]) {
            failures.push(format!("#{index} {}: {difference}", item["step"]));
        }
    }
    drop(store);
    tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    let python = folder.join("python.sqlite3");
    std::fs::write(&python, gunzip(&vectors.join("final.sqlite3.gz"))).unwrap();
    let (rust_rows, python_rows) = (table_rows(&database), table_rows(&python));
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
    assert!(failures.is_empty(), "{} construction steps differ:\n{}", failures.len(), failures.join("\n"));
}
