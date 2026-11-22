//! Compare sylvia_app::plan_snapshot and the bundled Rift snapshot functions with the frozen
//! Python.
//!
//! Regenerate with `tests/vectors/generate_plan_snapshot_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use std::path::PathBuf;
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::plan_snapshot::{
    admitted_snapshot_facts, retained_plan_snapshot, validate_plan_snapshot, PlanSnapshotOwner,
    SnapshotFacts, PLAN_SNAPSHOT_BYTE_LIMIT,
};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::campaign_plan::ValidatedCampaignPlan;
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_library::{CandidateStatus, PackageCandidate};
use sylvia_domain::{DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/plan_snapshot_vectors.json")).expect("vector file is valid JSON")
}

fn repository_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

/// The generator's `apply(document, edits)`; an empty path replaces the document.
fn apply(mut document: Json, edits: &[Json]) -> Json {
    for edit in edits {
        let (op, path, value) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap(), edit[2].clone());
        let Some((last, parents)) = path.split_last() else {
            document = value;
            continue;
        };
        let parent = parents.iter().fold(&mut document, |node, part| match part {
            Json::String(key) => &mut node[key.as_str()],
            index => &mut node[index.as_u64().unwrap() as usize],
        });
        match (op, last) {
            ("delete", Json::String(key)) => {
                parent.as_object_mut().unwrap().shift_remove(key.as_str());
            }
            ("delete", index) => {
                parent.as_array_mut().unwrap().remove(index.as_u64().unwrap() as usize);
            }
            (_, Json::String(key)) => {
                parent.as_object_mut().unwrap().insert(key.clone(), value);
            }
            (_, index) => parent[index.as_u64().unwrap() as usize] = value,
        }
    }
    document
}

fn observed<T>(result: DomainResult<T>, shape: impl FnOnce(T) -> Json) -> Json {
    match result {
        Ok(value) => json!(["ok", shape(value)]),
        Err(error @ DomainError::Diagnosed { .. }) | Err(error @ DomainError::Rejected { .. }) => json!([
            error.python_class(),
            error.message(),
            error.diagnostics().iter().map(|d| Json::Object(d.as_payload())).collect::<Vec<_>>()
        ]),
        Err(error) => json!([error.python_class(), error.message(), []]),
    }
}

fn validated_shape(plan: ValidatedCampaignPlan) -> Json {
    json!({
        "content_sha256": plan.content_sha256,
        "artifact_id": plan.artifact_id,
        "title": plan.title,
        "markdown_sha256": sha256_hex(plan.readable_markdown.as_bytes()),
        "referenced_setting_paths": plan.referenced_setting_paths,
    })
}

fn facts_shape(facts: SnapshotFacts) -> Json {
    let (package_id, version, content_sha256) = facts.primary.identity();
    json!({
        "primary": [package_id, version, content_sha256],
        "closure_sha256": facts.closure_sha256,
        "settings": facts.settings.iter().map(|setting| json!({
            "binding": [setting.binding.package_id, setting.binding.version, setting.binding.content_sha256],
            "paths": setting.paths,
            "knowledge_ids": setting.knowledge_ids,
        })).collect::<Vec<_>>(),
    })
}

fn digest(value: Option<Vec<u8>>) -> Json {
    value.map(|bytes| Json::from(sha256_hex(&bytes))).unwrap_or(Json::Null)
}

/// The generator's `Owner`: fixed candidates; export writes `export:<candidate id>`.
struct Owner(Vec<PackageCandidate>);

impl PlanSnapshotOwner for Owner {
    type Error = DomainError;
    fn package_candidates(&self) -> DomainResult<Vec<PackageCandidate>> {
        Ok(self.0.clone())
    }
    fn export_package_candidate(&self, candidate_id: &str) -> DomainResult<Vec<u8>> {
        Ok(format!("export:{candidate_id}").into_bytes())
    }
}

#[test]
fn plan_snapshot_matches_python() {
    let vectors = vectors();
    assert_eq!(vectors["limit"], json!(PLAN_SNAPSHOT_BYTE_LIMIT));
    let root = repository_root();
    let rift = root.join(vectors["rift"].as_str().unwrap());
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let retained = std::fs::read(rift.join("setting.sylvia-gm-package")).unwrap();
    let current = bundled.bundled_rift_archive().unwrap();
    let plan = pyjson::loads(&std::fs::read_to_string(rift.join("plan.json")).unwrap()).unwrap();
    let markdown = std::fs::read_to_string(rift.join("plan.md")).unwrap().replace("\r\n", "\n");
    let lock = admit_package_archive(&retained).unwrap().package_lock;
    let snapshot = |spec: &Json| -> Vec<u8> {
        match spec["kind"].as_str().unwrap() {
            "retained" => retained.clone(),
            "current" => current.clone(),
            "empty" => Vec::new(),
            "oversize" => vec![0; PLAN_SNAPSHOT_BYTE_LIMIT + 1],
            "truncate" => retained[..spec["length"].as_u64().unwrap() as usize].to_vec(),
            "flip" => {
                let mut changed = retained.clone();
                changed[spec["offset"].as_u64().unwrap() as usize] ^= 0xFF;
                changed
            }
            other => panic!("{other}"),
        }
    };
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 200);
    let mut failures = Vec::new();
    for case in cases {
        let actual = match case["kind"].as_str().unwrap() {
            "validate" => {
                let payload = apply(plan.clone(), case["edits"].as_array().unwrap());
                let markdown = case["markdown"].as_str().unwrap_or(&markdown);
                observed(
                    validate_plan_snapshot(&snapshot(&case["snapshot"]), &payload, markdown),
                    validated_shape,
                )
            }
            "facts" => observed(admitted_snapshot_facts(&snapshot(&case["snapshot"])), facts_shape),
            "rift_closure" => observed(bundled.bundled_rift_archive_for_closure(&case["expected"]), digest),
            "rift_plan" => observed(bundled.bundled_rift_campaign_plan(), validated_shape),
            "retained" => {
                let candidates = case["candidates"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|item| {
                        let (id, closure) = (item[0].as_str().unwrap(), item[1].as_str().unwrap());
                        PackageCandidate::new(
                            id,
                            "install",
                            CandidateStatus::Activated,
                            closure,
                            lock.clone(),
                            JsonMap::new(),
                        )
                        .unwrap()
                    })
                    .collect();
                observed(
                    retained_plan_snapshot(&Owner(candidates), &bundled, &case["payload"]).map(Some),
                    digest,
                )
            }
            other => panic!("{other}"),
        };
        if actual != case["outcome"] {
            let label = json!([case["kind"], case.get("snapshot"), case.get("edits"), case.get("expected")]);
            failures.push(format!("{label}: python {}, rust {actual}", case["outcome"]));
        }
    }
    let shown: Vec<String> = failures.iter().take(6).map(|f| f.chars().take(900).collect()).collect();
    assert!(failures.is_empty(), "{} of {} differ: {:#?}", failures.len(), cases.len(), shown);
}
