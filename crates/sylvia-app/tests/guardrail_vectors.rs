//! Compare sylvia_app::path_guardrail with the frozen Python's `PathGuardrail`, on this platform.
//!
//! Regenerate with `tests/vectors/generate_guardrail_vectors.py` on each platform (needs the
//! snapshot's Python): Windows writes `guardrail_vectors.windows.json`, Linux
//! `guardrail_vectors.linux.json`.

use std::path::{Path, PathBuf};
use sylvia_app::path_guardrail::PathGuardrail;
use sylvia_base::{pyjson, Json};

#[cfg(windows)]
const VECTORS: &str = include_str!("vectors/guardrail_vectors.windows.json");
#[cfg(not(windows))]
const VECTORS: &str = include_str!("vectors/guardrail_vectors.linux.json");

/// A symbolic link, as the generator's `os.symlink` makes one where the platform allows it.
#[cfg(unix)]
fn link(target: &str, path: &Path) {
    std::os::unix::fs::symlink(target, path).unwrap();
}

#[cfg(not(unix))]
fn link(target: &str, path: &Path) {
    panic!("the Windows vectors record no links, but asked for {} -> {target}", path.display());
}

/// The generator's `build(base)`: the project tree, the outside folder and any links.
fn build(base: &Path, vectors: &Json) -> PathBuf {
    let root = base.join("project");
    for dir in vectors["dirs"].as_array().unwrap() {
        std::fs::create_dir_all(root.join(dir.as_str().unwrap())).unwrap();
    }
    for file in vectors["files"].as_array().unwrap() {
        std::fs::write(root.join(file.as_str().unwrap()), "x").unwrap();
    }
    std::fs::create_dir_all(base.join("outside")).unwrap();
    std::fs::write(base.join("outside").join("x.txt"), "x").unwrap();
    for name in vectors["links"].as_array().unwrap() {
        let name = name.as_str().unwrap();
        link(vectors["link_targets"][name].as_str().unwrap(), &root.join(name));
    }
    root
}

#[test]
fn guardrail_matches_python_on_this_platform() {
    let vectors = pyjson::loads(VECTORS).unwrap();
    let base = std::env::temp_dir().join(format!("sylvia-guardrail-rust-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base).unwrap();
    let root = build(&base, &vectors);
    let guardrail = PathGuardrail::new(&root);
    let root_text = guardrail.allowed_root().to_owned();
    let separator = if cfg!(windows) { '\\' } else { '/' };
    let parent_text = root_text[..root_text.rfind(separator).unwrap()].to_owned();
    let mask = |text: &str| text.replace(&root_text, "<ROOT>").replace(&parent_text, "<PARENT>");
    let expand = |raw: &str| {
        raw.replace("<ABSROOT-UPPER>", &root_text.to_uppercase())
            .replace("<ABSROOT>", &root_text)
            .replace("<ABSPARENT>", &parent_text)
    };
    let mut failures = Vec::new();
    let cases = vectors["cases"].as_array().unwrap();
    for case in cases {
        let raw = case["raw"].as_str().unwrap();
        let for_write = case["for_write"].as_bool().unwrap();
        let actual = match guardrail.validate(&expand(raw), for_write) {
            Ok(resolved) => Json::from(vec![Json::from("ok"), Json::from(mask(&resolved.to_string_lossy()))]),
            Err(error) => Json::from(vec![
                Json::from("blocked"),
                Json::from(mask(&error.to_string())),
                Json::from(mask(&error.resolved)),
                Json::from(error.reason.as_str()),
            ]),
        };
        if actual != case["outcome"] {
            failures.push(format!("{raw:?} write={for_write}: python {}, rust {actual}", case["outcome"]));
        }
    }
    let _ = std::fs::remove_dir_all(&base);
    assert!(
        failures.is_empty(),
        "{} of {} differ: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(8)]
    );
}
