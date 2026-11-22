//! The GM context's content reads against Python (`tests/vectors/content_reads/
//! content_read_runs.json`, written by `generate_content_read_runs.py`): every
//! `ruleset_gm_operating_guide`, `catalog`, `model_context_owner`, `campaign_knowledge_catalog`
//! and `campaign_knowledge_catalog_for_plan` read on three Python-made profiles must be Python's
//! value, in Python's order, or Python's refusal. A catalog Python fills (Riichi's DM campaign,
//! whose two tools are admitted) waits on lane 2's tool materialization and must be refused as
//! not ported; every other catalog is Python's.

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_app::authority::content_reads;
use sylvia_base::Json;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle, StoreResult};

fn base(name: &str) -> Vec<u8> {
    let relative = match name {
        "cast" => "stored_authority/cast.sqlite3.gz",
        "campaigns" => "campaigns/seed.sqlite3.gz",
        "session_pages" => "session_pages/seed.sqlite3.gz",
        other => panic!("unknown base {other}"),
    };
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../sylvia-store/tests/vectors").join(relative);
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(path).unwrap()).read_to_end(&mut bytes).unwrap();
    bytes
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

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn content_reads_match_python() {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/content_reads/content_read_runs.json");
    let record: Json = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let runs = record["runs"].as_array().unwrap();
    assert_eq!(runs.len(), 117);
    let mut failures = Vec::new();
    let mut not_ported = Vec::new();
    let mut current: Option<(String, StoreHandle, sylvia_store::StoreThread)> = None;
    for (index, run) in runs.iter().enumerate() {
        let name = run["base"].as_str().unwrap();
        if current.as_ref().map(|(known, _, _)| known.as_str()) != Some(name) {
            if let Some((_, handle, thread)) = current.take() {
                drop(handle);
                tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
            }
            let folder = Path::new(env!("CARGO_TARGET_TMPDIR"))
                .join("sylvia-app")
                .join(format!("content-reads-{name}"));
            let _ = std::fs::remove_dir_all(&folder);
            std::fs::create_dir_all(&folder).unwrap();
            let database = folder.join("authority.sqlite3");
            std::fs::write(&database, base(name)).unwrap();
            let (handle, thread) =
                StoreHandle::spawn(move || AuthorityStore::open_for_application_owner(&database, None))
                    .unwrap();
            current = Some((name.to_owned(), handle, thread));
        }
        let handle = &current.as_ref().unwrap().1;
        let campaign = run["campaign_id"].as_str().unwrap_or_default();
        let got = match run["read"].as_str().unwrap() {
            "ruleset_gm_operating_guide" => outcome(
                content_reads::ruleset_gm_operating_guide(handle, campaign)
                    .await
                    .map(|guide| guide.map_or(Json::Null, Json::from)),
            ),
            "catalog" => match content_reads::catalog(handle, campaign, false).await {
                Err(StoreError::NotPorted(_)) => {
                    not_ported.push(format!("{name} {campaign}"));
                    continue;
                }
                read => {
                    outcome(read.map(|tools| tools.into_iter().map(|tool| Json::from(tool.name)).collect()))
                }
            },
            "model_context_owner" => outcome(
                content_reads::model_context_owner(handle, campaign)
                    .await
                    .map(|owner| owner.map_or(Json::Null, Json::Object)),
            ),
            "campaign_knowledge_catalog" => outcome(
                content_reads::campaign_knowledge_catalog(handle, run["session_id"].as_str().unwrap())
                    .await
                    .map(Json::Object),
            ),
            _ => outcome(
                content_reads::campaign_knowledge_catalog_for_plan(
                    handle,
                    campaign,
                    run["plan"].as_object().unwrap(),
                )
                .await
                .map(Json::Object),
            ),
        };
        // As text: object equality ignores key order, and Python's order is part of the value.
        let (got_text, want_text) = (got.to_string(), run["outcome"].to_string());
        if got_text != want_text {
            failures.push(format!(
                "#{index} {name} {} {campaign} {}:\n  Rust   {got_text}\n  Python {want_text}",
                run["read"], run["case"]
            ));
        }
    }
    if let Some((_, handle, thread)) = current.take() {
        drop(handle);
        tokio::task::spawn_blocking(move || thread.join()).await.unwrap();
    }
    assert!(
        failures.is_empty(),
        "{} of {} reads differ:\n{}",
        failures.len(),
        runs.len(),
        failures.join("\n")
    );
    assert_eq!(not_ported, ["cast campaign-table"]);
    // The one refused catalog is the one Python filled.
    let filled: Vec<&Json> = runs
        .iter()
        .filter(|run| {
            run["read"] == "catalog" && run["outcome"]["ok"].as_array().is_some_and(|tools| !tools.is_empty())
        })
        .collect();
    assert_eq!(filled.len(), 1);
    assert_eq!(filled[0]["campaign_id"], "campaign-table");
}
