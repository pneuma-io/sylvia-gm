//! Compare `sylvia_app::import_repair`'s staging and materializing with the frozen Python: the
//! generator's steps on two workspaces, each outcome and every workspace file after it.
//!
//! Regenerate with `tests/vectors/generate_import_repair_vectors.py` (needs the snapshot's Python).

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::import_repair::{materialize_repair_draft, stage_repair_copy, RepairKind};
use sylvia_base::hash::sha256_hex;
use sylvia_base::testing::json_difference;
use sylvia_base::{pybase64, pyjson, Json};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::DomainError;

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/import_repair/repair_vectors.json"))
        .expect("vector file is valid JSON")
}

fn kind(text: &str) -> RepairKind {
    if text == "plan" {
        RepairKind::Plan
    } else {
        RepairKind::Package
    }
}

fn failure(error: &DomainError) -> Json {
    let class = error.python_class();
    if matches!(
        class,
        "OSError"
            | "FileNotFoundError"
            | "FileExistsError"
            | "PermissionError"
            | "IsADirectoryError"
            | "NotADirectoryError"
    ) {
        return serde_json::json!({ "error": class });
    }
    let diagnostics: Vec<Json> =
        error.diagnostics().iter().map(|item| Json::Object(item.as_payload())).collect();
    serde_json::json!({"error": class, "message": error.message(), "diagnostics": diagnostics})
}

fn tree(workspace: &Path) -> Json {
    fn walk(folder: &Path, root: &Path, found: &mut Vec<(Vec<String>, String)>) {
        let Ok(entries) = std::fs::read_dir(folder) else { return };
        for entry in entries {
            let path = entry.unwrap().path();
            if path.is_dir() {
                walk(&path, root, found);
            } else {
                let parts = path
                    .strip_prefix(root)
                    .unwrap()
                    .components()
                    .map(|part| part.as_os_str().to_string_lossy().into_owned())
                    .collect();
                found.push((parts, sha256_hex(std::fs::read(&path).unwrap())));
            }
        }
    }
    let mut found = Vec::new();
    walk(workspace, workspace, &mut found);
    found.sort();
    Json::Array(
        found.into_iter().map(|(parts, digest)| serde_json::json!([parts.join("/"), digest])).collect(),
    )
}

#[test]
fn staging_and_materializing_match_python() {
    let record = vectors();
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&repository()));
    let root = std::env::temp_dir().join(format!("sgir-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    let sources = root.join("sources");
    std::fs::create_dir_all(&sources).unwrap();
    let kept_sources = repository().join("crates/sylvia-app/tests/vectors/import_repair/sources");
    for entry in std::fs::read_dir(&kept_sources).unwrap() {
        let path = entry.unwrap().path();
        std::fs::copy(&path, sources.join(path.file_name().unwrap())).unwrap();
    }
    let mut kept: BTreeMap<(String, String), Vec<u8>> = BTreeMap::new();
    let mut failures = Vec::new();
    for (index, step) in record["steps"].as_array().unwrap().iter().enumerate() {
        let text = |key: &str| step[key].as_str().unwrap_or_default().to_owned();
        let name = text("workspace");
        let workspace = root.join(&name);
        let op = text("op");
        let path = workspace.join(text("path"));
        let got = match op.as_str() {
            "stage" => {
                let bound = step["bound"].as_str();
                match stage_repair_copy(
                    &sources.join(text("source")),
                    &text("expected"),
                    &workspace,
                    kind(&text("kind")),
                    bound,
                ) {
                    Ok(staged) => {
                        let draft = staged
                            .draft_root
                            .strip_prefix(&workspace)
                            .unwrap()
                            .to_string_lossy()
                            .replace('\\', "/");
                        serde_json::json!({"ok": {"kind": staged.kind.as_str(), "source_sha256": staged.source_sha256, "draft_root": draft}})
                    }
                    Err(error) => failure(&error),
                }
            }
            "materialize" => match materialize_repair_draft(&workspace, kind(&text("kind")), &bundled) {
                Ok(materialized) => {
                    let plan = materialized.plan.as_ref().map_or(Json::Null, |plan| {
                        serde_json::json!({
                            "artifact_id": plan.artifact_id,
                            "content_sha256": plan.content_sha256,
                            "title": plan.title,
                            "payload_sha256": sha256_hex(canonical_json_bytes(&Json::Object(plan.payload.clone()))),
                            "markdown_sha256": sha256_hex(plan.readable_markdown.as_bytes()),
                        })
                    });
                    serde_json::json!({"ok": {
                        "kind": materialized.kind.as_str(),
                        "archive_sha256": sha256_hex(&materialized.archive),
                        "closure_sha256": materialized.closure_sha256,
                        "plan": plan,
                        "interview_contribution": materialized.interview_contribution,
                        "setup_id": materialized.setup_id,
                    }})
                }
                Err(error) => failure(&error),
            },
            "write" => {
                std::fs::write(&path, pybase64::a2b_base64(text("data").as_bytes(), true).unwrap()).unwrap();
                serde_json::json!({ "ok": null })
            }
            "delete" => {
                std::fs::remove_file(&path).unwrap();
                serde_json::json!({ "ok": null })
            }
            "delete_tree" => {
                std::fs::remove_dir_all(&path).unwrap();
                serde_json::json!({ "ok": null })
            }
            "restore" => {
                std::fs::write(&path, &kept[&(name.clone(), text("path"))]).unwrap();
                serde_json::json!({ "ok": null })
            }
            "edit_json" => {
                // Python's `json.dumps(value)` after one assignment; the text is compared by digest.
                let mut value: Json = pyjson::loads_bytes(&std::fs::read(&path).unwrap()).unwrap();
                let (keys, item) = (step["set"][0].as_array().unwrap(), &step["set"][1]);
                let mut target = &mut value;
                // A key is a member name or a list index.
                fn member<'a>(target: &'a mut Json, key: &Json) -> &'a mut Json {
                    match key {
                        Json::Number(index) => &mut target[index.as_u64().unwrap() as usize],
                        name => &mut target[name.as_str().unwrap()],
                    }
                }
                for key in &keys[..keys.len() - 1] {
                    target = member(target, key);
                }
                *member(target, &keys[keys.len() - 1]) = item.clone();
                std::fs::write(&path, pyjson::dumps(&value, pyjson::Dump::DEFAULT)).unwrap();
                serde_json::json!({ "ok": null })
            }
            other => panic!("unknown step {other}"),
        };
        if op == "stage" && got.get("ok").is_some() {
            let draft = workspace.join("repair-draft");
            if let Json::Array(files) = tree(&draft) {
                for file in files {
                    let relative = format!("repair-draft/{}", file[0].as_str().unwrap());
                    kept.entry((name.clone(), relative.clone()))
                        .or_insert_with(|| std::fs::read(workspace.join(&relative)).unwrap());
                }
            }
        }
        let label = format!(
            "#{index} {op} {name} {}",
            step.get("path").or(step.get("kind")).cloned().unwrap_or(Json::Null)
        );
        if let Some(difference) = json_difference(&got, &step["outcome"]) {
            failures.push(format!("{label}: {difference}"));
        }
        if let Some(difference) = json_difference(&tree(&workspace), &step["tree"]) {
            failures.push(format!("{label} files: {difference}"));
        }
    }
    let _ = std::fs::remove_dir_all(&root);
    assert!(failures.is_empty(), "{} steps differ:\n{}", failures.len(), failures.join("\n"));
}
