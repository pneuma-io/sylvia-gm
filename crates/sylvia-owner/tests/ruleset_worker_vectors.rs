//! The owner's `--ruleset-test-worker` mode against Python's `interfaces/ruleset_test_worker.py`
//! (`vectors/generate_ruleset_worker_vectors.py`): the result file's exact text, and lane 1's
//! parent (`sylvia_app::ruleset_testing`) admitting it from the real owner executable.

use std::path::PathBuf;
use std::process::Command;
use sylvia_app::ruleset_testing::{run_ruleset_tests_with, WorkerCommand};
use sylvia_base::testing::assert_same_json;
use sylvia_base::Json;

fn vectors() -> Json {
    serde_json::from_str(include_str!("vectors/ruleset_worker_vectors.json")).expect("the recorded vectors")
}

/// A scratch folder, removed afterwards; a stale one is cleared loudly first.
struct Scratch(PathBuf);

impl Scratch {
    fn new(name: &str) -> Self {
        let path = std::env::temp_dir().join(format!("sylvia-ruleset-worker-{name}-{}", std::process::id()));
        if path.exists() {
            std::fs::remove_dir_all(&path)
                .unwrap_or_else(|error| panic!("could not clear {}: {error}", path.display()));
        }
        std::fs::create_dir_all(&path).unwrap();
        Self(path)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn archive_of(case: &Json) -> Option<Vec<u8>> {
    case["archive_base64"].as_str().map(|text| sylvia_base::pybase64::b64decode_validate(text).unwrap())
}

#[test]
fn worker_writes_what_python_writes() {
    let vectors = vectors();
    let cases = vectors["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 5);
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let scratch = Scratch::new(name);
        let archive = scratch.0.join("candidate.sylvia-gm-package");
        if let Some(bytes) = archive_of(case) {
            std::fs::write(&archive, bytes).unwrap();
        }
        let result = scratch.0.join("result.json");
        let status = Command::new(env!("CARGO_BIN_EXE_sylvia-gm-owner"))
            .arg("--ruleset-test-worker")
            .arg("--archive")
            .arg(&archive)
            .arg("--result")
            .arg(&result)
            .status()
            .unwrap();
        assert_eq!(status.code(), Some(0), "{name}");
        let written = std::fs::read_to_string(&result).unwrap();
        // The exact bytes: key order, separators and ASCII escapes, as `json.dumps` writes them.
        assert_eq!(written, case["port"].as_str().unwrap(), "{name}");
        // Its scenario folders under the result's parent are gone again.
        let mut left: Vec<String> = std::fs::read_dir(&scratch.0)
            .unwrap()
            .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
            .filter(|entry| entry != "candidate.sylvia-gm-package" && entry != "result.json")
            .collect();
        left.sort();
        assert!(left.is_empty(), "{name}: left {left:?}");
    }
}

#[test]
fn lane_one_parent_admits_the_owner_worker() {
    let worker = WorkerCommand {
        program: PathBuf::from(env!("CARGO_BIN_EXE_sylvia-gm-owner")),
        args: vec!["--ruleset-test-worker".into()],
    };
    let vectors = vectors();
    for case in vectors["cases"].as_array().unwrap() {
        let Some(archive) = archive_of(case) else { continue };
        let name = case["name"].as_str().unwrap();
        let found = run_ruleset_tests_with(&worker, &archive, 120.0, None).unwrap();
        let expected: Json = serde_json::from_str(case["port"].as_str().unwrap()).unwrap();
        assert_same_json(&Json::Object(found), &expected, name);
    }
}
