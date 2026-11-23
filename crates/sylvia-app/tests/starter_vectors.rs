//! Compare sylvia_app::bundled_starter_characters with the frozen Python: each scenario replays
//! the generator's steps on one fresh `BundledPackages`, so the roster cache behaves as Python's
//! process cache did, and compares every call's participants or error, the `on_invalid` reports
//! and the storage tree after each step.
//!
//! Regenerate with `tests/vectors/generate_starter_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::bundled_starter_characters::{available_reusable_participants, bundled_starter_participants};
use sylvia_app::reusable_participants::{save_reusable_participant, ReusableParticipant};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::{PackageLockEntry, PackageRole};
use sylvia_domain::campaign_plan::PackageBinding;
use sylvia_domain::{DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/starter_vectors.json")).expect("vector file is valid JSON")
}

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// A fresh work folder: one left by a killed run must not show up later as an unrelated error.
fn fresh_work(name: &str) -> PathBuf {
    let work = Path::new(env!("CARGO_TARGET_TMPDIR")).join(name);
    match std::fs::remove_dir_all(&work) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => panic!("cannot clear the old work folder {}: {error}", work.display()),
    }
    work
}

/// The generator's `mask`: the work root as `<ROOT>`, with `/` separators after it.
fn mask(text: &str, root: &Path) -> String {
    let root = root.display().to_string();
    let mut text = text.to_owned();
    for form in [root.replace('\\', "\\\\"), root.clone(), root.replace('\\', "/")] {
        text = text.replace(&form, "<ROOT>");
    }
    let mut parts: Vec<String> = text.split("<ROOT>").map(str::to_owned).collect();
    for part in parts.iter_mut().skip(1) {
        let (path, rest) = match part.find('\'') {
            Some(index) => part.split_at(index),
            None => (part.as_str(), ""),
        };
        *part = format!("{}{rest}", path.replace("\\\\", "/").replace('\\', "/"));
    }
    parts.join("<ROOT>")
}

/// The generator's `tree`: every entry under `root`, sorted part by part, files by SHA-256.
fn tree(root: &Path) -> Json {
    fn walk(root: &Path, folder: &Path, entries: &mut Vec<(String, Json)>) {
        for entry in std::fs::read_dir(folder).unwrap() {
            let path = entry.unwrap().path();
            let relative = path.strip_prefix(root).unwrap().to_string_lossy().replace('\\', "/");
            if path.is_dir() {
                entries.push((relative.clone(), json!([relative, null])));
                walk(root, &path, entries);
            } else {
                let digest = sha256_hex(std::fs::read(&path).unwrap());
                entries.push((relative.clone(), json!([relative, digest])));
            }
        }
    }
    if !root.exists() {
        return Json::Null;
    }
    let mut entries = Vec::new();
    walk(root, root, &mut entries);
    entries.sort_by(|left, right| left.0.split('/').cmp(right.0.split('/')));
    Json::Array(entries.into_iter().map(|(_, entry)| entry).collect())
}

fn participant_payload(item: &ReusableParticipant) -> Json {
    json!({
        "artifact_id": item.artifact_id, "role": item.role, "name": item.name, "summary": item.summary,
        "authored_profile": item.authored_profile, "content_sha256": item.content_sha256,
        "package_id": item.package_id, "package_version": item.package_version,
        "package_sha256": item.package_sha256, "schema_id": item.schema_id, "schema_sha256": item.schema_sha256,
        "campaign_setting_id": item.campaign_setting_id, "campaign_setting_version": item.campaign_setting_version,
        "campaign_setting_sha256": item.campaign_setting_sha256, "participant": item.participant,
        "entity_state": item.entity_state, "setting_data": item.setting_data,
        "source_profile": item.source_profile, "source_migration": item.source_migration,
        "builtin_starter": item.builtin_starter,
    })
}

fn binding(value: &Json) -> PackageBinding {
    PackageBinding {
        package_id: value["id"].as_str().unwrap().to_owned(),
        version: value["version"].as_str().unwrap().to_owned(),
        content_sha256: value["content_sha256"].as_str().unwrap().to_owned(),
    }
}

fn lock_entry(value: &Json) -> PackageLockEntry {
    let field = |key: &str| value[key].as_str().unwrap().to_owned();
    PackageLockEntry::new(
        field("id"),
        field("version"),
        field("content_sha256"),
        PackageRole::Primary,
        0,
        Vec::new(),
        Vec::new(),
    )
    .unwrap()
}

fn call_outcome(result: DomainResult<Vec<ReusableParticipant>>, work: &Path) -> Json {
    match result {
        Ok(items) => json!(["ok", items.iter().map(participant_payload).collect::<Vec<_>>()]),
        Err(error) => json!([error.python_class(), mask(error.message(), work), error.code()]),
    }
}

#[test]
fn starters_match_python() {
    let vectors = vectors();
    let work_root = fresh_work("starter-vectors");
    let mut failures = Vec::new();
    for case in vectors["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let work = work_root.join(name);
        let bundled = BundledPackages::new(BundledSources::from_repository_root(&repository()));
        let declaration = bundled.bundled_rift_participant_declaration().unwrap();
        let mut roots: HashMap<String, PathBuf> = HashMap::new();
        let steps = case["steps"].as_array().unwrap();
        for (index, (step, expected)) in steps.iter().zip(case["results"].as_array().unwrap()).enumerate() {
            let root = roots
                .entry(step["root"].as_str().unwrap().to_owned())
                .or_insert_with(|| work.join(step["root"].as_str().unwrap()))
                .clone();
            std::fs::create_dir_all(&root).unwrap();
            let actual = match step["step"].as_str().unwrap() {
                "remove" => {
                    let _ = std::fs::remove_dir_all(root.join(step["path"].as_str().unwrap()));
                    json!({"tree": tree(&root)})
                }
                "corrupt" => {
                    let library = root.join(step["path"].as_str().unwrap());
                    let mut entries: Vec<PathBuf> =
                        std::fs::read_dir(&library).unwrap().map(|entry| entry.unwrap().path()).collect();
                    entries.sort();
                    let target = entries[0].join("record.json");
                    std::fs::write(&target, step["text"].as_str().unwrap()).unwrap();
                    let relative = target.strip_prefix(&root).unwrap().to_string_lossy().replace('\\', "/");
                    json!({"corrupted": relative, "tree": tree(&root)})
                }
                "save" => {
                    let package = lock_entry(&json!({
                        "id": declaration_package(&bundled).0, "version": declaration_package(&bundled).1,
                        "content_sha256": declaration_package(&bundled).2,
                    }));
                    let setting_data = (!step["setting_data"].is_null()).then_some(&step["setting_data"]);
                    let result = save_reusable_participant(
                        &root,
                        step["operation_id"].as_str().unwrap(),
                        step["role"].as_str().unwrap(),
                        step["summary"].as_str().unwrap(),
                        &step["authored_profile"],
                        &step["participant"],
                        &step["entity_state"],
                        &package,
                        &declaration,
                        &binding(&step["binding"]),
                        setting_data,
                        None,
                        None,
                    );
                    let outcome = match result {
                        Ok(saved) => json!(["ok", participant_payload(&saved)]),
                        Err(error) => json!([error.python_class(), mask(error.message(), &work)]),
                    };
                    json!({"outcome": outcome, "tree": tree(&root)})
                }
                kind => {
                    let package = lock_entry(&step["ruleset"]);
                    let mut chosen = declaration.clone();
                    if let Some(kind) = step["declaration_kind"].as_str() {
                        chosen.kind = kind.to_owned();
                    }
                    let legacy = (!step["legacy"].is_null()).then(|| binding(&step["legacy"]));
                    let schema: Option<JsonMap> = step["schema"].as_object().cloned();
                    let mut invalid: Vec<Json> = Vec::new();
                    let result = if kind == "starters" {
                        bundled_starter_participants(
                            &root,
                            &bundled,
                            &package,
                            &chosen,
                            &binding(&step["binding"]),
                            schema.as_ref(),
                        )
                    } else {
                        let mut record = |name: &str, error: &DomainError| {
                            invalid.push(json!([name, error.python_class(), mask(error.message(), &work)]));
                        };
                        available_reusable_participants(
                            &root,
                            &bundled,
                            &package,
                            &chosen,
                            &binding(&step["binding"]),
                            legacy.as_ref(),
                            schema.as_ref(),
                            Some(&mut record),
                        )
                    };
                    let outcome = call_outcome(result, &work);
                    json!({"outcome": outcome, "invalid": invalid, "tree": tree(&root)})
                }
            };
            if actual != *expected {
                let shown: String = actual.to_string().chars().take(800).collect();
                let wanted: String = expected.to_string().chars().take(800).collect();
                failures.push(format!("{name} step {index} {}: python {wanted}, rust {shown}", step["step"]));
            }
        }
    }
    let _ = std::fs::remove_dir_all(&work_root);
    assert!(failures.is_empty(), "{} differ: {:#?}", failures.len(), &failures[..failures.len().min(6)]);
}

/// The bundled Rift Ruleset's exact identity.
fn declaration_package(bundled: &BundledPackages) -> (String, String, String) {
    let ruleset = &bundled.bundled_rift_authority().unwrap().ruleset;
    (ruleset.package_id.clone(), ruleset.version.clone(), ruleset.content_sha256.clone())
}
