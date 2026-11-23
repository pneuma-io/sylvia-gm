//! Compare sylvia_app::interview_content with the frozen Python's `resolve_interview_content`.
//!
//! Regenerate with `tests/vectors/generate_interview_content_vectors.py` (needs the snapshot's
//! Python). The fake owner installs archives as the generator's does (admitted, its candidate
//! `cand-<install id>`) and serves package reads from what it has installed.

use serde_json::json;
use std::cell::RefCell;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use sylvia_app::builder_references::ReferenceOwner;
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::interview_content::resolve_interview_content;
use sylvia_app::library_sources::LibraryOwner;
use sylvia_app::reusable_plans::save_reusable_rift_plan;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::{PackageDependency, PackageLock, PackageLockEntry, PackageRole};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_library::{closure_sha256, CandidateStatus, PackageCandidate};

const VECTORS: &str = include_str!("vectors/interview_content_vectors.json");

fn candidate(spec: &Json, sha: &str) -> PackageCandidate {
    let entries = spec["entries"]
        .as_array()
        .unwrap()
        .iter()
        .enumerate()
        .map(|(order, item)| {
            let dependencies = item["dependencies"]
                .as_array()
                .unwrap()
                .iter()
                .map(|d| {
                    PackageDependency::new(
                        d[0].as_str().unwrap(),
                        d[1].as_str().unwrap(),
                        sha,
                        d[2].as_str().unwrap(),
                        d[3].as_str().unwrap(),
                    )
                    .unwrap()
                })
                .collect();
            let role = if item["role"] == "primary" { PackageRole::Primary } else { PackageRole::Dependency };
            PackageLockEntry::new(
                item["id"].as_str().unwrap(),
                item["version"].as_str().unwrap(),
                sha,
                role,
                order as i64,
                dependencies,
                Vec::new(),
            )
            .unwrap()
        })
        .collect();
    let id = spec["candidate_id"].as_str().unwrap();
    PackageCandidate::new(
        id,
        format!("install-{id}"),
        CandidateStatus::Validated,
        spec["closure"].as_str().unwrap(),
        PackageLock::new(entries).unwrap(),
        JsonMap::new(),
    )
    .unwrap()
}

type Identity = (String, String, String);

/// The generator's fake owner.
struct Owner {
    candidates: RefCell<Vec<PackageCandidate>>,
    installs: RefCell<Vec<Json>>,
    records: RefCell<HashMap<Identity, JsonMap>>,
    files: RefCell<HashMap<Identity, HashMap<String, Vec<u8>>>>,
}

fn identity(id: &str, version: &str, sha256: &str) -> Identity {
    (id.to_owned(), version.to_owned(), sha256.to_owned())
}

fn unavailable() -> DomainError {
    DomainError::command_rejected("Installed package is unavailable")
}

impl LibraryOwner for Owner {
    type Error = DomainError;
    fn package_candidates(&self) -> DomainResult<Vec<PackageCandidate>> {
        Ok(self.candidates.borrow().clone())
    }
    fn package_candidate(&self, candidate_id: &str) -> DomainResult<PackageCandidate> {
        if candidate_id == "broken" {
            return Err(DomainError::command_rejected_with(
                "Package store is unavailable",
                "store_unavailable",
                Vec::new(),
            ));
        }
        self.candidates.borrow().iter().find(|item| item.candidate_id() == candidate_id).cloned().ok_or_else(
            || {
                DomainError::command_rejected_with(
                    "Package candidate is unavailable",
                    "candidate_unavailable",
                    Vec::new(),
                )
            },
        )
    }
    fn package_file_paths(&self, id: &str, version: &str, sha256: &str, _: i64) -> DomainResult<Vec<String>> {
        let files = self.files.borrow();
        let files = files.get(&identity(id, version, sha256)).ok_or_else(unavailable)?;
        let mut paths: Vec<String> = files.keys().cloned().collect();
        paths.sort();
        Ok(paths)
    }
    fn package_text_file(
        &self,
        id: &str,
        version: &str,
        sha256: &str,
        path: &str,
        _: i64,
    ) -> DomainResult<String> {
        let files = self.files.borrow();
        let files = files.get(&identity(id, version, sha256)).ok_or_else(unavailable)?;
        let data = files
            .get(path)
            .ok_or_else(|| DomainError::command_rejected("Package source path is unavailable"))?;
        Ok(String::from_utf8(data.clone()).unwrap())
    }
    fn package_record(&self, id: &str, version: &str, sha256: &str) -> DomainResult<JsonMap> {
        self.records.borrow().get(&identity(id, version, sha256)).cloned().ok_or_else(unavailable)
    }
    fn package_label(&self, _: &str, _: &str, _: &str) -> DomainResult<String> {
        unreachable!("not read")
    }
}

impl ReferenceOwner for Owner {
    fn install_package_archive(&self, archive: &[u8], install_id: &str) -> DomainResult<PackageCandidate> {
        let admitted = admit_package_archive(archive).unwrap();
        self.installs.borrow_mut().push(json!([install_id, sha256_hex(archive)]));
        for manifest in &admitted.manifests {
            let (id, version, sha256) = manifest.identity();
            let record = pyjson::loads(&manifest.canonical_json).unwrap().as_object().unwrap().clone();
            self.records.borrow_mut().insert(identity(id, version, sha256), record);
        }
        for file in &admitted.files {
            let (id, version, sha256) = admitted.manifests[file.manifest].identity();
            self.files
                .borrow_mut()
                .entry(identity(id, version, sha256))
                .or_default()
                .insert(file.file.path().to_owned(), admitted.read_file(file).unwrap());
        }
        let candidate = PackageCandidate::new(
            format!("cand-{install_id}"),
            install_id,
            CandidateStatus::Validated,
            closure_sha256(&admitted.package_lock),
            admitted.package_lock.clone(),
            JsonMap::new(),
        )?;
        self.candidates.borrow_mut().push(candidate.clone());
        Ok(candidate)
    }
}

#[test]
fn interview_content_matches_python() {
    let vectors = pyjson::loads(VECTORS).unwrap();
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let sha = vectors["sha"].as_str().unwrap();
    let work = Path::new(env!("CARGO_TARGET_TMPDIR")).join("interview-content-vectors");
    match std::fs::remove_dir_all(&work) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => panic!("cannot clear the old work folder {}: {error}", work.display()),
    }
    let storage = work.join("storage");
    std::fs::create_dir_all(&storage).unwrap();
    let rift = bundled.bundled_rift_campaign_plan().unwrap();
    let sibling = save_reusable_rift_plan(
        &storage,
        &bundled,
        "interview-vectors",
        "Saved Rift",
        &rift.payload,
        &rift.readable_markdown,
        None,
    )
    .unwrap();
    assert_eq!(sibling.artifact_id, vectors["sibling"]);
    let mut failures = Vec::new();
    let cases = vectors["cases"].as_array().unwrap();
    for case in cases {
        let owner = Owner {
            candidates: RefCell::new(
                case["owner"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|name| candidate(&vectors["candidates"][name.as_str().unwrap()], sha))
                    .collect(),
            ),
            installs: RefCell::new(Vec::new()),
            records: RefCell::new(HashMap::new()),
            files: RefCell::new(HashMap::new()),
        };
        let root = (case["storage"] == "saved").then_some(storage.as_path());
        let result = resolve_interview_content(&owner, &bundled, root, &case["selections"]);
        let installs = Json::Array(owner.installs.borrow().clone());
        let actual = match result {
            Ok(content) => json!([
                "ok",
                content.candidate.as_ref().map(|item| item.candidate_id().to_owned()),
                content.setting_id,
                content.plan_contribution,
                installs
            ]),
            Err(error) => json!([error.python_class(), error.message(), error.code(), installs]),
        };
        if actual != case["outcome"] {
            failures.push(format!(
                "{} {} {}: python {}, rust {actual}",
                case["owner"], case["storage"], case["selections"], case["outcome"]
            ));
        }
    }
    let _ = std::fs::remove_dir_all(&work);
    assert!(
        failures.is_empty(),
        "{} of {} differ: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(6)]
    );
}
