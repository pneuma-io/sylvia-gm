//! Compare sylvia_app::package_record_edits with the frozen Python's `apply_package_record_edits`.
//!
//! Regenerate with `tests/vectors/generate_package_record_edit_vectors.py` (needs the snapshot's
//! Python). One file serves Windows and Linux: links are junctions on Windows and symbolic
//! links elsewhere.

use serde_json::json;
use std::path::{Path, PathBuf};
use sylvia_app::package_materialization::NormalizedPackageDraft;
use sylvia_app::package_record_edits::apply_package_record_edits;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::Json;

const VECTORS: &str = include_str!("vectors/package_record_edit_vectors.json");

fn drafts_from(spec: &Json, vectors: &Json) -> Vec<NormalizedPackageDraft> {
    let names: Vec<String> = match spec.get("names") {
        Some(names) => {
            names.as_array().unwrap().iter().map(|name| name.as_str().unwrap().to_owned()).collect()
        }
        None => {
            vectors["base"].as_array().unwrap().iter().map(|name| name.as_str().unwrap().to_owned()).collect()
        }
    };
    names
        .iter()
        .map(|name| {
            let mut draft = vectors["drafts"][name.as_str()].clone();
            for edit in spec
                .get("edits")
                .and_then(|edits| edits.get(name.as_str()))
                .and_then(Json::as_array)
                .into_iter()
                .flatten()
            {
                let path = edit[0].as_array().unwrap();
                let mut target = &mut draft;
                for part in &path[..path.len() - 1] {
                    target = target.get_mut(part.as_str().unwrap()).unwrap();
                }
                target[path[path.len() - 1].as_str().unwrap()] = edit[1].clone();
            }
            let payloads = draft["payloads"]
                .as_object()
                .unwrap()
                .iter()
                .map(|(path, text)| (path.clone(), text.as_str().unwrap().as_bytes().to_vec()))
                .collect();
            NormalizedPackageDraft::new(
                draft["record"].as_object().unwrap().clone(),
                payloads,
                draft["format_version"].as_i64().unwrap(),
            )
        })
        .collect()
}

fn file_content(spec: &Json) -> Vec<u8> {
    if let Some(value) = spec.get("json") {
        return pyjson::dumps(value, Dump::DEFAULT).into_bytes();
    }
    if let Some(text) = spec.get("latin1") {
        return text.as_str().unwrap().chars().map(|c| u8::try_from(u32::from(c)).unwrap()).collect();
    }
    if let Some(repeat) = spec.get("repeat") {
        return repeat[0].as_str().unwrap().repeat(repeat[1].as_u64().unwrap() as usize).into_bytes();
    }
    spec["text"].as_str().unwrap().as_bytes().to_vec()
}

#[cfg(windows)]
fn make_link(path: &Path, target: &Path) {
    let status = std::process::Command::new("cmd")
        .args(["/C", "mklink", "/J"])
        .arg(path)
        .arg(target)
        .stdout(std::process::Stdio::null())
        .status()
        .unwrap();
    assert!(status.success(), "mklink /J failed");
}

#[cfg(not(windows))]
fn make_link(path: &Path, target: &Path) {
    std::os::unix::fs::symlink(target, path).unwrap();
}

fn relative(base: &Path, text: &str) -> PathBuf {
    text.split('/').fold(base.to_path_buf(), |path, part| path.join(part))
}

fn run(base: &Path, script: &Json, vectors: &Json) -> Json {
    let workspace = base.join("workspace");
    std::fs::create_dir_all(workspace.join("draft")).unwrap();
    for step in script.get("files").and_then(Json::as_array).into_iter().flatten() {
        let path = relative(&workspace, step["path"].as_str().unwrap());
        match step["op"].as_str().unwrap() {
            "write" => {
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                std::fs::write(&path, file_content(&step["content"])).unwrap();
            }
            "mkdir" => std::fs::create_dir_all(&path).unwrap(),
            "link" => {
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                make_link(&path, &relative(&workspace, step["target"].as_str().unwrap()));
            }
            "remove" => std::fs::remove_dir_all(&path).unwrap(),
            other => panic!("unknown step {other}"),
        }
    }
    let outside = script.get("outside").is_some_and(|flag| flag == &Json::Bool(true));
    let draft_root = if outside { base.join("outside") } else { PathBuf::from("draft") };
    if outside {
        std::fs::create_dir_all(&draft_root).unwrap();
    }
    let drafts = drafts_from(script.get("drafts").unwrap_or(&json!({})), vectors);
    match apply_package_record_edits(&workspace, &draft_root, &drafts) {
        Ok(drafts) => {
            let items: Vec<Json> = drafts
                .iter()
                .map(|draft| {
                    let dependencies: Vec<Json> = draft
                        .record
                        .get("dependencies")
                        .and_then(Json::as_array)
                        .into_iter()
                        .flatten()
                        .map(|item| json!(item.as_object().unwrap().keys().collect::<Vec<_>>()))
                        .collect();
                    json!({
                        "record": draft.record,
                        "order": draft.record.keys().collect::<Vec<_>>(),
                        "dependency_order": dependencies,
                        "payloads": draft.payloads.iter().map(|(path, _)| path).collect::<Vec<_>>(),
                        "format_version": draft.format_version,
                    })
                })
                .collect();
            json!(["ok", items])
        }
        Err(error) => {
            let diagnostics: Vec<Json> =
                error.diagnostics().iter().map(|item| Json::Object(item.as_payload())).collect();
            json!([error.python_class(), error.message(), diagnostics, error.code()])
        }
    }
}

#[test]
fn package_record_edits_match_python() {
    let vectors = pyjson::loads(VECTORS).unwrap();
    let mut failures = Vec::new();
    let scripts = vectors["scripts"].as_object().unwrap();
    for (name, script) in scripts {
        let base =
            std::env::temp_dir().join(format!("sylvia-record-edits-rust-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        let actual = run(&base, script, &vectors);
        let _ = std::fs::remove_dir_all(&base);
        if actual != script["outcome"] {
            failures.push(format!("{name}: python {}, rust {actual}", script["outcome"]));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ: {:#?}",
        failures.len(),
        scripts.len(),
        &failures[..failures.len().min(6)]
    );
}
