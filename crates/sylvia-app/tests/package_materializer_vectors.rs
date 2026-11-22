//! Compare sylvia_app::package_materializer with the frozen Python's `materialize_ruleset_draft`,
//! `export_ruleset_candidate` and `ruleset_install_id`.
//!
//! Regenerate with `tests/vectors/generate_package_materializer_vectors.py` (needs the
//! snapshot's Python). One file serves Windows and Linux: links are junctions on Windows and
//! symbolic links elsewhere.

use serde_json::json;
use std::path::{Path, PathBuf};
use sylvia_app::package_materialization::MaterializedPackageArchive;
use sylvia_app::package_materializer::{
    export_ruleset_candidate, materialize_ruleset_draft, ruleset_install_id,
};
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::Json;
use sylvia_domain::error::DomainError;

const VECTORS: &str = include_str!("vectors/package_materializer_vectors.json");

fn step<'a>(value: &'a mut Json, part: &Json) -> &'a mut Json {
    match part {
        Json::String(key) => value.get_mut(key.as_str()).unwrap(),
        index => value.get_mut(index.as_u64().unwrap() as usize).unwrap(),
    }
}

fn apply(document: &Json, edits: &Json) -> Json {
    let mut document = document.clone();
    for edit in edits.as_array().into_iter().flatten() {
        let (op, path, value) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap(), &edit[2]);
        let mut parent = &mut document;
        for part in &path[..path.len() - 1] {
            parent = step(parent, part);
        }
        match (op, &path[path.len() - 1]) {
            ("delete", Json::String(key)) => {
                parent.as_object_mut().unwrap().shift_remove(key.as_str());
            }
            (_, Json::String(key)) => {
                parent.as_object_mut().unwrap().insert(key.clone(), value.clone());
            }
            (_, index) => *step(parent, index) = value.clone(),
        }
    }
    document
}

fn content(spec: &Json, vectors: &Json, root: &Path) -> Vec<u8> {
    if let Some(text) = spec.get("text") {
        return text.as_str().unwrap().as_bytes().to_vec();
    }
    if let Some(edits) = spec.get("descriptor") {
        return pyjson::dumps(&apply(&vectors["descriptor"], edits), Dump::DEFAULT).into_bytes();
    }
    if let Some(name) = spec.get("payload") {
        return vectors["payloads"][name.as_str().unwrap()].as_str().unwrap().as_bytes().to_vec();
    }
    if spec.get("riichi").is_some() {
        return std::fs::read(root.join(vectors["riichi"].as_str().unwrap())).unwrap();
    }
    if let Some(edits) = spec.get("small") {
        return pyjson::dumps(&apply(&vectors["small"], edits), Dump::DEFAULT).into_bytes();
    }
    if let Some(text) = spec.get("latin1") {
        return text.as_str().unwrap().chars().map(|c| u8::try_from(u32::from(c)).unwrap()).collect();
    }
    let repeat = spec["repeat"].as_array().unwrap();
    repeat[0].as_str().unwrap().repeat(repeat[1].as_u64().unwrap() as usize).into_bytes()
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

fn error_outcome(error: &DomainError) -> Json {
    let diagnostics: Vec<Json> =
        error.diagnostics().iter().map(|item| Json::Object(item.as_payload())).collect();
    json!([error.python_class(), error.message(), diagnostics, error.code()])
}

/// The generator's `run(base, steps)`.
fn run(base: &Path, steps: &[Json], vectors: &Json, root: &Path, failures: &mut Vec<String>, name: &str) {
    let workspace = base.join("workspace");
    std::fs::create_dir_all(&workspace).unwrap();
    let mut last: Option<MaterializedPackageArchive> = None;
    for (index, step) in steps.iter().enumerate() {
        let path = step.get("path").map(|path| relative(&workspace, path.as_str().unwrap()));
        let outcome = match step["op"].as_str().unwrap() {
            "write" => {
                let path = path.unwrap();
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                std::fs::write(&path, content(&step["content"], vectors, root)).unwrap();
                Json::Null
            }
            "payloads" => {
                let skip: Vec<&str> = step
                    .get("skip")
                    .and_then(Json::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(Json::as_str)
                    .collect();
                for (file, data) in vectors["payloads"].as_object().unwrap() {
                    if !skip.contains(&file.as_str()) {
                        let target = relative(path.as_ref().unwrap(), file);
                        std::fs::create_dir_all(target.parent().unwrap()).unwrap();
                        std::fs::write(&target, data.as_str().unwrap()).unwrap();
                    }
                }
                Json::Null
            }
            "mkdir" => {
                std::fs::create_dir_all(path.unwrap()).unwrap();
                Json::Null
            }
            "link" => {
                let path = path.unwrap();
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                make_link(&path, &relative(&workspace, step["target"].as_str().unwrap()));
                Json::Null
            }
            "fill" => {
                let path = path.unwrap();
                std::fs::create_dir_all(&path).unwrap();
                for item in 0..step["count"].as_u64().unwrap() {
                    if step.get("directories").is_some_and(|flag| flag == &Json::Bool(true)) {
                        std::fs::create_dir(path.join(format!("d{item:04}"))).unwrap();
                    } else {
                        std::fs::write(path.join(format!("f{item:04}.txt")), "x").unwrap();
                    }
                }
                Json::Null
            }
            "materialize" => match materialize_ruleset_draft(&workspace) {
                Ok(archive) => {
                    let outcome = json!([
                        "ok",
                        sha256_hex(&archive.archive),
                        archive.archive.len(),
                        archive.closure_sha256
                    ]);
                    last = Some(archive);
                    outcome
                }
                Err(error) => {
                    last = None;
                    error_outcome(&error)
                }
            },
            "export" => match &last {
                None => json!(["skipped"]),
                Some(archive) => match export_ruleset_candidate(&workspace, archive) {
                    Ok(path) => json!(["ok", path]),
                    Err(error) => error_outcome(&error),
                },
            },
            other => panic!("unknown step {other}"),
        };
        if outcome != step["outcome"] {
            failures
                .push(format!("{name}[{index}] {}: python {}, rust {outcome}", step["op"], step["outcome"]));
        }
    }
}

#[test]
fn package_materializer_matches_python() {
    let vectors = pyjson::loads(VECTORS).unwrap();
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
    let mut failures = Vec::new();
    let mut steps = 0;
    for (name, script) in vectors["scripts"].as_object().unwrap() {
        let base =
            std::env::temp_dir().join(format!("sylvia-materializer-rust-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        std::fs::create_dir_all(&base).unwrap();
        let script = script.as_array().unwrap();
        steps += script.len();
        run(&base, script, &vectors, &root, &mut failures, name);
        let _ = std::fs::remove_dir_all(&base);
    }
    for case in vectors["install_ids"].as_array().unwrap() {
        let actual = ruleset_install_id(case[0].as_str().unwrap(), case[1].as_str().unwrap());
        if actual != case[2] {
            failures.push(format!("install id {case}: rust {actual}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {steps} steps differ: {:#?}",
        failures.len(),
        &failures[..failures.len().min(8)]
    );
}
