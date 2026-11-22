//! Portable campaign export against Python (`tests/vectors/export_writer/export_runs.json`,
//! written by `generate_export_writer_runs.py`): each case copies a Python-made profile, exports
//! one campaign with the export fault seam recording every stage and acting where the case says,
//! and must match Python's outcome, the stages in order, what is left under `exports/`, the
//! published archive's bytes and the package read leases left.

mod common;

use flate2::read::GzDecoder;
use serde_json::json;
use std::collections::HashMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{CodedError, Json};
use sylvia_store::export_writer::ExportError;
use sylvia_store::store::Faults;
use sylvia_store::{AuthorityStore, StoreError};

fn base(name: &str) -> Vec<u8> {
    let relative = match name {
        "story" => "tests/vectors/export_capture/story.sqlite3.gz",
        "constructed" => "tests/vectors/campaigns/seed.sqlite3.gz",
        "cast" => "tests/vectors/stored_authority/cast.sqlite3.gz",
        other => panic!("unknown base {other}"),
    };
    let mut bytes = Vec::new();
    GzDecoder::new(std::fs::File::open(Path::new(env!("CARGO_MANIFEST_DIR")).join(relative)).unwrap())
        .read_to_end(&mut bytes)
        .unwrap();
    bytes
}

/// A case's `/`-separated relative path under `folder`.
fn under(folder: &Path, relative: &str) -> PathBuf {
    relative.split('/').fold(folder.to_path_buf(), |path, part| path.join(part))
}

/// The private temporary `.<name>.backup-<32 hex>.tmp` in one path component, as `<temporary>`.
fn normalized(component: &str) -> String {
    if let Some(start) = component.find(".backup-") {
        let hex = &component[start + 8..];
        if component.starts_with('.')
            && hex.len() >= 36
            && hex[..32].bytes().all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            && &hex[32..36] == ".tmp"
        {
            return format!("<temporary>{}", &hex[36..]);
        }
    }
    component.to_owned()
}

/// The generator's `listing`: everything under `exports/`, with each file's SHA-256.
fn listing(folder: &Path, links: &[String]) -> Json {
    fn walk(folder: &Path, at: &Path, entries: &mut Vec<String>) {
        for entry in std::fs::read_dir(at).unwrap() {
            let path = entry.unwrap().path();
            let relative: Vec<String> = path
                .strip_prefix(folder)
                .unwrap()
                .components()
                .map(|part| part.as_os_str().to_string_lossy().into_owned())
                .collect();
            entries.push(relative.join("/"));
            if path.is_dir() {
                walk(folder, &path, entries);
            }
        }
    }
    let mut paths = Vec::new();
    if folder.join("exports").exists() {
        walk(folder, &folder.join("exports"), &mut paths);
    }
    paths.sort();
    Json::Array(
        paths
            .into_iter()
            .map(|relative| {
                let path = under(folder, &relative);
                let shown: Vec<String> = relative.split('/').map(normalized).collect();
                let shown = shown.join("/");
                if links.contains(&shown) {
                    json!([shown, "link", null])
                } else if path.is_dir() {
                    json!([shown, "dir", null])
                } else {
                    json!([shown, "file", sha256_hex(std::fs::read(&path).unwrap())])
                }
            })
            .collect(),
    )
}

fn prepare(folder: &Path, case: &Json) {
    for step in case["setup"].as_array().unwrap() {
        if let Some(directory) = step["mkdir"].as_str() {
            std::fs::create_dir_all(under(folder, directory)).unwrap();
        } else if let Some(file) = step["write"].as_str() {
            let path = under(folder, file);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, step["text"].as_str().unwrap()).unwrap();
        } else {
            let link = step["link"].as_str().unwrap();
            std::fs::hard_link(under(folder, step["to"].as_str().unwrap()), under(folder, link)).unwrap();
        }
    }
    std::fs::create_dir_all(folder.join("exports")).unwrap();
}

/// The fault seam: every stage recorded, and the case's actions taken.
fn seam(folder: &Path, case: &Json, stages: Arc<Mutex<Vec<String>>>) -> Faults {
    let faults = case["faults"].as_array().unwrap().clone();
    let statements: Vec<String> =
        case["sql"].as_array().unwrap().iter().map(|item| item.as_str().unwrap().to_owned()).collect();
    let target = under(folder, case["target"].as_str().unwrap());
    let database = folder.join("authority.sqlite3");
    let seen: Mutex<HashMap<String, u64>> = Mutex::default();
    Faults {
        export_stage: Some(Box::new(move |stage: &str| {
            stages.lock().unwrap().push(stage.to_owned());
            let occurrence = {
                let mut seen = seen.lock().unwrap();
                let count = seen.entry(stage.to_owned()).or_insert(0);
                *count += 1;
                *count - 1
            };
            for armed in &faults {
                if armed["stage"] != stage
                    || !(armed["occurrence"].is_null() || armed["occurrence"] == occurrence)
                {
                    continue;
                }
                match armed["action"].as_str().unwrap() {
                    "raise" => return Err(StoreError::Other(format!("fault at {stage}"))),
                    "export_failure" => {
                        return Err(StoreError::Coded(
                            CodedError::new(
                                "portable_export_injected",
                                "Portable campaign export failed (portable_export_injected)",
                            )
                            .with_error_type("_ExportFailure"),
                        ))
                    }
                    "create_target" => std::fs::write(&target, b"an intruder").unwrap(),
                    "recreate_target" => {
                        std::fs::remove_file(&target).unwrap();
                        std::fs::write(&target, b"an intruder").unwrap();
                    }
                    "damage" => {
                        let connection = rusqlite::Connection::open(&database).unwrap();
                        for statement in &statements {
                            connection.execute_batch(statement).unwrap();
                        }
                    }
                    "block_journal" => {
                        let parent = target.parent().unwrap();
                        let temporaries: Vec<PathBuf> = std::fs::read_dir(parent)
                            .unwrap()
                            .map(|entry| entry.unwrap().path())
                            .filter(|path| {
                                let name = path.file_name().unwrap().to_string_lossy().into_owned();
                                normalized(&name) == "<temporary>"
                            })
                            .collect();
                        assert_eq!(temporaries.len(), 1, "one claimed temporary");
                        let mut journal = temporaries[0].as_os_str().to_os_string();
                        journal.push("-journal");
                        std::fs::create_dir_all(PathBuf::from(journal).join("held")).unwrap();
                    }
                    other => panic!("unknown action {other}"),
                }
            }
            Ok(())
        })),
        ..Faults::default()
    }
}

fn outcome(result: Result<sylvia_domain::authority::CampaignExportResult, ExportError>) -> Json {
    match result {
        Ok(result) => json!({"ok": {
            "campaign_id": result.campaign_id,
            "captured_revision": result.captured_revision,
            "archive_sha256": result.archive_sha256,
            "byte_count": result.byte_count,
        }}),
        Err(ExportError::Failure(failure)) => json!({
            "error": "_ExportFailure",
            "code": failure.code,
            "message": failure.message(),
            "supplemental_codes": failure.supplemental_codes,
        }),
        Err(ExportError::Store(error)) => {
            let coded = error.rejection().unwrap_or_else(|| panic!("an unexpected failure: {error}"));
            json!({
                "error": coded.error_type.unwrap_or("CommandRejected"),
                "code": coded.code,
                "message": coded.message,
                "supplemental_codes": [],
            })
        }
    }
}

#[test]
fn exports_match_python() {
    let record = common::read_json("tests/vectors/export_writer/export_runs.json");
    let cases = record["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 67);
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let label = format!("#{index} {}", case["name"]);
        let folder = common::work_folder(&format!("export-writer-{index}"));
        let database = folder.join("authority.sqlite3");
        std::fs::write(&database, base(case["base"].as_str().unwrap())).unwrap();
        prepare(&folder, case);
        let stages: Arc<Mutex<Vec<String>>> = Arc::default();
        let mut store = AuthorityStore::open_for_application_owner(&database, None).unwrap();
        store.set_faults(seam(&folder, case, Arc::clone(&stages)));
        let target = under(&folder, case["target"].as_str().unwrap());
        let got_outcome = outcome(store.export_campaign(
            case["campaign_id"].as_str().unwrap(),
            &target,
            case["include_thinking"].as_bool().unwrap(),
        ));
        store.close();
        drop(store);
        let leases: i64 = rusqlite::Connection::open(&database)
            .unwrap()
            .query_row("SELECT COUNT(*) FROM package_read_leases", [], |row| row.get(0))
            .unwrap();
        let links: Vec<String> = case["setup"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|step| step["link"].as_str().map(str::to_owned))
            .collect();
        let linked = links.iter().any(|link| Json::from(link.as_str()) == case["target"]);
        let in_exports = target.parent().and_then(Path::file_name).is_some_and(|name| name == "exports");
        let published =
            (target.is_file() && in_exports && !linked).then(|| sha256_hex(std::fs::read(&target).unwrap()));
        let got = json!({
            "outcome": got_outcome,
            "stages": *stages.lock().unwrap(),
            "exports": listing(&folder, &links),
            "target_sha256": published,
            "leases": leases,
        });
        let want = json!({
            "outcome": case["outcome"],
            "stages": case["stages"],
            "exports": case["exports"],
            "target_sha256": case["target_sha256"],
            "leases": case["leases"],
        });
        if got != want {
            failures.push(format!("{label}:\n  Rust   {got}\n  Python {want}"));
        }
        let _ = std::fs::remove_dir_all(&folder);
    }
    assert!(
        failures.is_empty(),
        "{} of {} cases differ:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}

/// A published archive is exactly the bytes Python's owner exported for the archive validation
/// vectors: the same profile and campaign give the same archive.
#[test]
fn published_archives_are_the_validation_vectors_own() {
    let exports = common::read_json("tests/vectors/export_writer/export_runs.json");
    let validation = common::read_json("tests/vectors/archive_validation/validation_runs.json");
    let mut archives = Vec::new();
    GzDecoder::new(
        std::fs::File::open(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/archive_validation/archives.bin.gz"),
        )
        .unwrap(),
    )
    .read_to_end(&mut archives)
    .unwrap();
    let digest_of = |name: &str| {
        let entry = validation["cases"].as_array().unwrap().iter().find(|case| case["name"] == name).unwrap();
        let (offset, length) =
            (entry["offset"].as_u64().unwrap() as usize, entry["length"].as_u64().unwrap() as usize);
        sha256_hex(&archives[offset..offset + length])
    };
    for (export, validated) in [
        ("story_thinking", "story_exported"),
        ("clockwork", "clockwork_exported"),
        ("no_blobs", "plain_exported"),
        ("workshop", "workshop_exported"),
    ] {
        let case = exports["cases"].as_array().unwrap().iter().find(|case| case["name"] == export).unwrap();
        assert_eq!(case["outcome"]["ok"]["archive_sha256"], digest_of(validated), "{export}");
    }
}
