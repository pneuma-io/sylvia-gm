//! Compare `BundledPackages::bundled_campaign_content_catalog` with the frozen Python, on the
//! repository's bundled content and on synthetic bundled Plan collections (Python's frozen-build
//! `_MEIPASS` collection; here `BundledSources::campaign_plans_root`).
//!
//! Regenerate with `tests/vectors/generate_content_catalog_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use std::path::{Path, PathBuf};
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::Json;
use sylvia_domain::DomainResult;

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/content_catalog_vectors.json")).expect("vector file is valid JSON")
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

fn copy_tree(from: &Path, to: &Path) {
    std::fs::create_dir_all(to).unwrap();
    for entry in std::fs::read_dir(from).unwrap() {
        let path = entry.unwrap().path();
        let target = to.join(path.file_name().unwrap());
        if path.is_dir() {
            copy_tree(&path, &target);
        } else {
            std::fs::copy(&path, &target).unwrap();
        }
    }
}

/// The generator's `mask`: the scratch root as `<ROOT>`, with `/` separators after it.
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

fn outcome(result: DomainResult<Vec<sylvia_base::JsonMap>>, root: &Path) -> Json {
    match result {
        Ok(rows) => json!(["ok", rows]),
        Err(error) => json!([error.python_class(), mask(error.message(), root)]),
    }
}

/// The generator's run: the catalog, then again after the caller edits its copy (a success is
/// cached unchanged; a failure is raised again).
fn run(bundled: &BundledPackages, root: &Path) -> Json {
    let first = outcome(bundled.bundled_campaign_content_catalog(), root);
    if let Ok(mut rows) = bundled.bundled_campaign_content_catalog() {
        rows[0].insert("title".into(), json!("edited by the caller"));
        rows.last_mut().unwrap().clear();
    }
    let again = outcome(bundled.bundled_campaign_content_catalog(), root);
    assert_eq!(first, again, "a second call differs");
    first
}

#[test]
fn content_catalog_matches_python() {
    let vectors = vectors();
    let root = repository();
    let work = fresh_work("content-catalog-vectors");
    let mut failures = Vec::new();
    for case in vectors["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let mut sources = BundledSources::from_repository_root(&root);
        if let Some(collection) = case["collection"].as_object() {
            let plans = work.join(name).join("campaign-plans");
            std::fs::create_dir_all(&plans).unwrap();
            if !collection["catalogue"].is_null() {
                let text = pyjson::dumps(&collection["catalogue"], Dump::DEFAULT);
                std::fs::write(plans.join("catalogue.json"), text).unwrap();
            }
            for plan in collection["plans"].as_array().unwrap() {
                let plan = plan.as_str().unwrap();
                copy_tree(&root.join("campaign-plans").join(plan), &plans.join(plan));
            }
            sources.campaign_plans_root = plans;
        }
        let actual = run(&BundledPackages::new(sources), &work);
        if actual != case["outcome"] {
            let shown: String = actual.to_string().chars().take(600).collect();
            failures.push(format!("{name}: python {}, rust {shown}", case["outcome"]));
        }
    }
    let _ = std::fs::remove_dir_all(&work);
    assert_eq!(vectors["cases"].as_array().unwrap().len(), 7);
    assert!(failures.is_empty(), "{} differ: {failures:#?}", failures.len());
}
