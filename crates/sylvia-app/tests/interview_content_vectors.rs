//! Compare sylvia_app::interview_content with the frozen Python's `resolve_interview_content`.
//!
//! Regenerate with `tests/vectors/generate_interview_content_vectors.py` (needs the snapshot's
//! Python). A `["pending", function]` outcome is where Python reaches code not yet ported; the
//! port must stop there with `rust_port_pending` naming the same function.

use serde_json::json;
use std::path::PathBuf;
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::interview_content::{resolve_interview_content, InterviewOwner, PORT_PENDING};
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::{PackageDependency, PackageLock, PackageLockEntry, PackageRole};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_library::{CandidateStatus, PackageCandidate};

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

/// The generator's fake owner.
struct Owner(Vec<PackageCandidate>);

impl InterviewOwner for Owner {
    fn package_candidate(&self, candidate_id: &str) -> DomainResult<PackageCandidate> {
        if candidate_id == "broken" {
            return Err(DomainError::command_rejected_with(
                "Package store is unavailable",
                "store_unavailable",
                Vec::new(),
            ));
        }
        self.0.iter().find(|item| item.candidate_id() == candidate_id).cloned().ok_or_else(|| {
            DomainError::command_rejected_with(
                "Package candidate is unavailable",
                "candidate_unavailable",
                Vec::new(),
            )
        })
    }

    fn package_candidates(&self) -> DomainResult<Vec<PackageCandidate>> {
        Ok(self.0.clone())
    }
}

#[test]
fn interview_content_matches_python() {
    let vectors = pyjson::loads(VECTORS).unwrap();
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let sha = vectors["sha"].as_str().unwrap();
    let mut failures = Vec::new();
    let cases = vectors["cases"].as_array().unwrap();
    for case in cases {
        let owner = Owner(
            case["owner"]
                .as_array()
                .unwrap()
                .iter()
                .map(|name| candidate(&vectors["candidates"][name.as_str().unwrap()], sha))
                .collect(),
        );
        let actual = match resolve_interview_content(&owner, &bundled, None, &case["selections"]) {
            Ok(content) => json!([
                "ok",
                content.candidate.as_ref().map(|item| item.candidate_id().to_owned()),
                content.setting_id,
                content.plan_contribution
            ]),
            Err(error) if error.code() == Some(PORT_PENDING) => {
                let function = error
                    .message()
                    .strip_prefix("The Rust port does not run ")
                    .and_then(|rest| rest.strip_suffix(" yet."))
                    .unwrap_or_default()
                    .to_owned();
                json!(["pending", function])
            }
            Err(error) => json!([error.python_class(), error.message(), error.code()]),
        };
        if actual != case["outcome"] {
            failures.push(format!(
                "{} {}: python {}, rust {actual}",
                case["owner"], case["selections"], case["outcome"]
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
