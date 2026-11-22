//! Compare sylvia_app::library_sources with the frozen Python's `LibrarySourceReader`.
//!
//! Regenerate with `tests/vectors/generate_library_source_vectors.py` (needs the snapshot's
//! Python). The owner is the generator's fake, here and there.

use serde_json::json;
use sylvia_app::library_sources::{LibraryOwner, LibrarySourceReader};
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::{PackageDependency, PackageLock, PackageLockEntry, PackageRole};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_library::{CandidateStatus, PackageCandidate};

const VECTORS: &str = include_str!("vectors/library_source_vectors.json");

/// The generator's `sha(name)`.
fn sha(name: &str) -> String {
    let hex: String = name.bytes().map(|byte| format!("{byte:02x}")).collect();
    hex.repeat(64).chars().take(64).collect()
}

fn candidate(spec: &Json) -> PackageCandidate {
    let entries = spec["entries"]
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
        .map(|(order, item)| {
            let id = item["id"].as_str().unwrap();
            let dependencies = item["dependencies"]
                .as_array()
                .unwrap()
                .iter()
                .map(|d| {
                    let name = d[0].as_str().unwrap();
                    PackageDependency::new(
                        name,
                        "1.0.0",
                        sha(name),
                        d[1].as_str().unwrap(),
                        d[2].as_str().unwrap(),
                    )
                    .unwrap()
                })
                .collect();
            let role = if item["role"] == "primary" { PackageRole::Primary } else { PackageRole::Dependency };
            PackageLockEntry::new(
                id,
                item["version"].as_str().unwrap(),
                sha(id),
                role,
                order as i64,
                dependencies,
                Vec::new(),
            )
            .unwrap()
        })
        .collect();
    let id = spec["candidate_id"].as_str().unwrap();
    let status =
        CandidateStatus::parse(spec.get("status").and_then(Json::as_str).unwrap_or("validated")).unwrap();
    PackageCandidate::new(
        id,
        format!("install-{id}"),
        status,
        sha(id),
        PackageLock::new(entries).unwrap(),
        JsonMap::new(),
    )
    .unwrap()
}

/// The generator's fake owner.
struct Owner<'a> {
    candidates: Vec<PackageCandidate>,
    packages: &'a Json,
}

impl LibraryOwner for Owner<'_> {
    type Error = DomainError;

    fn package_candidates(&self) -> DomainResult<Vec<PackageCandidate>> {
        Ok(self.candidates.clone())
    }

    fn package_candidate(&self, candidate_id: &str) -> DomainResult<PackageCandidate> {
        self.candidates.iter().find(|item| item.candidate_id() == candidate_id).cloned().ok_or_else(|| {
            DomainError::command_rejected_with(
                "Package candidate is unavailable",
                "candidate_unavailable",
                Vec::new(),
            )
        })
    }

    fn package_file_paths(
        &self,
        package_id: &str,
        _: &str,
        _: &str,
        maximum_files: i64,
    ) -> DomainResult<Vec<String>> {
        let paths: Vec<String> = self.packages[package_id]["files"]
            .as_array()
            .unwrap()
            .iter()
            .map(|path| path.as_str().unwrap().to_owned())
            .collect();
        if paths.len() as i64 > maximum_files {
            return Err(DomainError::command_rejected("Package has too many files to inspect"));
        }
        Ok(paths)
    }

    fn package_text_file(
        &self,
        package_id: &str,
        _: &str,
        _: &str,
        path: &str,
        _: i64,
    ) -> DomainResult<String> {
        self.packages[package_id]["texts"]
            .get(path)
            .and_then(Json::as_str)
            .map(str::to_owned)
            .ok_or_else(|| DomainError::command_rejected("Package source path is unavailable"))
    }

    fn package_record(&self, package_id: &str, _: &str, _: &str) -> DomainResult<JsonMap> {
        Ok(self.packages[package_id]["record"].as_object().unwrap().clone())
    }

    fn package_label(&self, package_id: &str, _: &str, _: &str) -> DomainResult<String> {
        Ok(self.packages[package_id]["label"].as_str().unwrap().to_owned())
    }
}

#[test]
fn library_sources_match_python() {
    let vectors = pyjson::loads(VECTORS).unwrap();
    let mut failures = Vec::new();
    let cases = vectors["cases"].as_array().unwrap();
    for case in cases {
        let owner = Owner {
            candidates: case["owner"]
                .as_array()
                .unwrap()
                .iter()
                .map(|name| candidate(&vectors["candidates"][name.as_str().unwrap()]))
                .collect(),
            packages: &vectors["packages"],
        };
        let reader = LibrarySourceReader {
            owner: &owner,
            ruleset_id: case["ruleset_id"].as_str().unwrap().to_owned(),
            setting_id: case["setting_id"].as_str().map(str::to_owned),
        };
        let args = case["args"].as_array().unwrap();
        let arg = |index: usize| args[index].as_str().unwrap();
        let result = match case["method"].as_str().unwrap() {
            "candidates" => reader
                .candidates(arg(0))
                .map(|items| Json::Array(items.into_iter().map(Json::Object).collect())),
            "files" => reader.files(arg(0)).map(Json::Object),
            "read" => reader.read(arg(0), arg(1)).map(Json::Object),
            _ => reader.record(arg(0), arg(1)).map(Json::Object),
        };
        let actual = match result {
            Ok(value) => json!(["ok", value]),
            Err(error) => json!([error.python_class(), error.message(), error.code()]),
        };
        if actual != case["outcome"] {
            failures.push(format!(
                "{} {:?} {} {}: python {}, rust {actual}",
                case["ruleset_id"], case["setting_id"], case["method"], case["args"], case["outcome"]
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(6)]
    );
}
