//! Compare `sylvia_app::package_snapshots`, `setting_materializer` and `builder_source_selection`
//! with the frozen Python, on owners holding the same installed candidates.
//!
//! Regenerate with `tests/vectors/generate_setting_snapshot_vectors.py` (needs the snapshot's
//! Python).

use std::path::{Path, PathBuf};
use sylvia_app::builder_source_selection::BuilderSourceSelection;
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::package_materialization::{MaterializedPackageArchive, NormalizedPackageDraft};
use sylvia_app::package_snapshots::{retained_package_drafts, ruleset_copy, select_snapshot_sources};
use sylvia_app::setting_materializer::{check_setting_draft, materialize_setting_draft};
use sylvia_base::hash::sha256_hex;
use sylvia_base::testing::json_difference;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::session::SessionMessageKind;
use sylvia_store::{AuthorityStore, StoreError};

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/setting_snapshots/vectors.json")).expect("vector file is valid JSON")
}

/// The generator's `failure`: class, message, code and diagnostics (a `ValueError` has no code).
fn failure(error: &StoreError) -> Json {
    match error.rejection() {
        Some(coded) => {
            let class = coded.error_type.unwrap_or("CommandRejected");
            let value_error = matches!(
                class,
                "ValueError" | "JSONDecodeError" | "ContentShapeError" | "UnicodeDecodeError"
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

fn drafts_payload(drafts: &[NormalizedPackageDraft]) -> Json {
    Json::Array(
        drafts
            .iter()
            .map(|draft| {
                serde_json::json!({
                    "record": draft.record,
                    "format_version": draft.format_version,
                    "payloads": draft.payloads.iter().map(|(path, value)| serde_json::json!([path, sha256_hex(value)])).collect::<Vec<_>>(),
                })
            })
            .collect(),
    )
}

fn materialized_payload(materialized: &MaterializedPackageArchive) -> Json {
    serde_json::json!({
        "closure_sha256": materialized.closure_sha256,
        "archive_sha256": sha256_hex(&materialized.archive),
        "lock": materialized.package_lock.entries().iter()
            .map(|item| serde_json::json!([item.package_id(), item.version(), item.sha256()])).collect::<Vec<_>>(),
    })
}

fn write_files(workspace: &Path, files: &Json) {
    for (relative, text) in files.as_object().unwrap() {
        let path = workspace.join(relative);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, text.as_str().unwrap()).unwrap();
    }
}

fn fresh_store(path: &Path) -> AuthorityStore {
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    AuthorityStore::open_for_application_owner(path, None).unwrap()
}

#[test]
fn snapshots_and_setting_drafts_match_python() {
    let record = vectors();
    let root = std::env::temp_dir().join(format!("sgss-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let store = fresh_store(&root.join("snapshots.sqlite3"));
    let kept = repository().join("crates/sylvia-app/tests/vectors/setting_snapshots/candidates");
    for install_id in record["install_order"].as_array().unwrap() {
        let install_id = install_id.as_str().unwrap();
        let archive = std::fs::read(kept.join(format!("{install_id}.archive"))).unwrap();
        sylvia_app::package_library::install_package_archive(&store, &archive, install_id).unwrap();
    }
    let original = record["ids"]["original"].as_str().unwrap();
    let riichi = record["ids"]["riichi"].as_str().unwrap();
    let mut failures = Vec::new();
    for (index, step) in record["snapshots"].as_array().unwrap().iter().enumerate() {
        let name = step["name"].as_str().unwrap();
        let workspace = root.join("workspaces").join(index.to_string());
        let got = match step["op"].as_str().unwrap() {
            "drafts" => outcome(
                match name {
                    "retained without setting" => retained_package_drafts(&store, original, false),
                    "retained with setting" => retained_package_drafts(&store, original, true),
                    "retained riichi" => retained_package_drafts(&store, riichi, true),
                    "ruleset copy" => ruleset_copy(&store, original),
                    _ => ruleset_copy(&store, "candidate-unknown"),
                }
                .map(|drafts| drafts_payload(&drafts)),
            ),
            "setting" => {
                write_files(&workspace, &step["files"]);
                outcome(
                    materialize_setting_draft(
                        &store,
                        &workspace,
                        original,
                        "local.relay-island",
                        "1.0.0",
                        "Relay Island",
                    )
                    .map(|materialized| materialized_payload(&materialized)),
                )
            }
            "check" => {
                write_files(&workspace, &step["files"]);
                outcome(
                    check_setting_draft(
                        &store,
                        &workspace,
                        original,
                        "local.relay-island",
                        "1.0.0",
                        "Relay Island",
                    )
                    .map(Json::Object),
                )
            }
            _ => {
                write_files(&workspace, &step["files"]);
                let with_setting = retained_package_drafts(&store, original, true).unwrap();
                outcome(
                    select_snapshot_sources(&store, &workspace, &workspace.join("draft"), with_setting)
                        .map(|drafts| drafts_payload(&drafts)),
                )
            }
        };
        if let Some(difference) = json_difference(&got, &step["outcome"]) {
            failures.push(format!("{name}: {difference}"));
        }
    }
    drop(store);
    let _ = std::fs::remove_dir_all(&root);
    assert!(failures.is_empty(), "{} snapshot steps differ:\n{}", failures.len(), failures.join("\n"));
}

#[test]
fn source_selection_matches_python() {
    let record = vectors();
    let root = std::env::temp_dir().join(format!("sgsel-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let storage = root.join("selection-storage");
    let store = fresh_store(&storage.join("authority.sqlite3"));
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&repository()));
    let catalog: Vec<JsonMap> =
        record["catalog"].as_array().unwrap().iter().map(|item| item.as_object().unwrap().clone()).collect();
    store.create_builder_chat("session-builder-plain", "standin", "standin/model", "Plain").unwrap();
    store.create_builder_chat("session-builder-repair", "standin", "standin/model", "Repair").unwrap();
    let mut evidence = JsonMap::new();
    evidence.insert("kind".into(), "import_repair_context".into());
    store
        .append_agent_message(
            "session-builder-repair",
            "repair-source-x",
            SessionMessageKind::Evidence,
            &evidence,
            None,
        )
        .unwrap();
    let plain_context = store.session_context("session-builder-plain").unwrap();
    let repair_context = store.session_context("session-builder-repair").unwrap();
    let listed = catalog.clone();
    let mut plain_chat = BuilderSourceSelection::new(
        &store,
        &bundled,
        &storage,
        &plain_context,
        Box::new(move || Ok(listed.clone())),
    )
    .unwrap();
    let listed = catalog.clone();
    let mut repair_chat = BuilderSourceSelection::new(
        &store,
        &bundled,
        &storage,
        &repair_context,
        Box::new(move || Ok(listed.clone())),
    )
    .unwrap();
    let ruleset = catalog
        .iter()
        .find(|item| {
            item.get("kind").is_some_and(|kind| kind == "ruleset")
                && item
                    .get("compatible_settings")
                    .and_then(Json::as_array)
                    .is_some_and(|settings| !settings.is_empty())
        })
        .unwrap()
        .clone();
    let other = catalog
        .iter()
        .find(|item| item.get("kind").is_some_and(|kind| kind == "ruleset") && **item != ruleset)
        .unwrap()
        .clone();
    let selection_id =
        |item: &JsonMap| item.get("selection_id").unwrap_or(&item["id"]).as_str().unwrap().to_owned();
    let setting = ruleset["compatible_settings"][0].clone();
    let setting_candidate = setting.get("candidate_id").and_then(Json::as_str).map(str::to_owned);
    let setting_id = setting["id"].as_str().unwrap().to_owned();
    let mut failures = Vec::new();
    for step in record["selection"].as_array().unwrap() {
        let name = step["name"].as_str().unwrap();
        let got = outcome(match name {
            "select unadvertised" => plain_chat.select("no-such-ruleset").map(Json::Object),
            "select by alias" => plain_chat.select("ruleset-alias").map(Json::Object),
            "select setting first unadvertised" => {
                plain_chat.select_setting(None, "unadvertised.setting").map(Json::Object)
            }
            "select ruleset" | "select ruleset again" => {
                plain_chat.select(&selection_id(&ruleset)).map(Json::Object)
            }
            "select other ruleset" => plain_chat.select(&selection_id(&other)).map(Json::Object),
            "candidate" => plain_chat.candidate().map(|candidate| {
                candidate.map_or(Json::Null, |candidate| Json::from(candidate.candidate_id()))
            }),
            "select setting" | "select setting again" => {
                plain_chat.select_setting(setting_candidate.as_deref(), &setting_id).map(Json::Object)
            }
            "reader" => plain_chat
                .reader()
                .map(|reader| reader.map_or(Json::Null, |reader| Json::from(reader.paths.len()))),
            "repair chat select" => repair_chat.select(&selection_id(&ruleset)).map(Json::Object),
            _ => repair_chat.select_setting(setting_candidate.as_deref(), &setting_id).map(Json::Object),
        });
        if let Some(difference) = json_difference(&got, &step["outcome"]) {
            failures.push(format!("{name}: {difference}"));
        }
        let references = plain_chat.references.clone().map_or(Json::Null, Json::Object);
        if let Some(difference) = json_difference(&references, &step["references"]) {
            failures.push(format!("{name} references: {difference}"));
        }
        let selectable = serde_json::json!([plain_chat.selectable, repair_chat.selectable]);
        if let Some(difference) = json_difference(&selectable, &step["selectable"]) {
            failures.push(format!("{name} selectable: {difference}"));
        }
    }
    let mut candidates: Vec<(String, String)> =
        sylvia_app::library_sources::LibraryOwner::package_candidates(&store)
            .unwrap()
            .iter()
            .map(|item| (item.candidate_id().to_owned(), item.closure_sha256().to_owned()))
            .collect();
    candidates.sort();
    let candidates =
        Json::Array(candidates.into_iter().map(|(id, closure)| serde_json::json!([id, closure])).collect());
    if let Some(difference) = json_difference(&candidates, &record["selection_candidates"]) {
        failures.push(format!("candidates: {difference}"));
    }
    drop((plain_chat, repair_chat));
    drop(store);
    let _ = std::fs::remove_dir_all(&root);
    assert!(failures.is_empty(), "{} selection steps differ:\n{}", failures.len(), failures.join("\n"));
}
