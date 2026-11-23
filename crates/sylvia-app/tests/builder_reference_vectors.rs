//! Compare sylvia_app::builder_references' capture functions with the frozen Python, over the
//! generator's fake owner (real archives' candidates, recorded installs, archive-backed reads).
//!
//! Regenerate with `tests/vectors/generate_builder_reference_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use std::cell::RefCell;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use sylvia_app::builder_references::{
    capture_content_references, capture_launch_references, ContentChoices, ReferenceOwner,
};
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::library_sources::LibraryOwner;
use sylvia_app::reusable_plans::save_reusable_rift_plan;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::{PackageLock, WriterRole};
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_library::{
    canonical_json_bytes, closure_sha256, CandidateStatus, PackageCandidate,
};
use sylvia_domain::session::{
    SessionContext, SessionMessage, SessionMessageKind, SessionRecord, SessionRecordFields,
};
use sylvia_domain::{DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/builder_reference_vectors.json")).expect("vector file is valid JSON")
}

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

type Identity = (String, String, String);

/// The generator's `Archive`: an admitted archive's lock, closure, records and files.
#[derive(Clone)]
struct Archive {
    lock: PackageLock,
    closure: String,
    records: HashMap<Identity, JsonMap>,
    files: HashMap<Identity, HashMap<String, Vec<u8>>>,
}

impl Archive {
    fn new(data: &[u8]) -> Self {
        let admitted = admit_package_archive(data).unwrap();
        let identity = |m: &sylvia_domain::package_library::PackageManifest| {
            let (a, b, c) = m.identity();
            (a.to_owned(), b.to_owned(), c.to_owned())
        };
        let records = admitted
            .manifests
            .iter()
            .map(|m| (identity(m), pyjson::loads(&m.canonical_json).unwrap().as_object().unwrap().clone()))
            .collect();
        let mut files: HashMap<Identity, HashMap<String, Vec<u8>>> = HashMap::new();
        for file in &admitted.files {
            let owner = identity(&admitted.manifests[file.manifest]);
            files
                .entry(owner)
                .or_default()
                .insert(file.file.path().to_owned(), admitted.read_file(file).unwrap());
        }
        Self {
            closure: closure_sha256(&admitted.package_lock),
            lock: admitted.package_lock.clone(),
            records,
            files,
        }
    }
}

/// The generator's fake `Owner`.
struct Owner {
    archives: RefCell<Vec<Archive>>,
    candidates: RefCell<Vec<PackageCandidate>>,
    installs: RefCell<Vec<Json>>,
}

impl Owner {
    fn new(archives: &HashMap<String, Archive>, names: &[String]) -> Self {
        let candidates = names
            .iter()
            .map(|name| {
                let archive = &archives[name];
                PackageCandidate::new(
                    format!("cand-{name}"),
                    format!("install-{name}"),
                    CandidateStatus::Activated,
                    archive.closure.clone(),
                    archive.lock.clone(),
                    JsonMap::new(),
                )
                .unwrap()
            })
            .collect();
        Self {
            archives: RefCell::new(archives.values().cloned().collect()),
            candidates: RefCell::new(candidates),
            installs: RefCell::new(Vec::new()),
        }
    }

    fn files(&self, id: &str, version: &str, sha256: &str) -> DomainResult<HashMap<String, Vec<u8>>> {
        let key = (id.to_owned(), version.to_owned(), sha256.to_owned());
        self.archives
            .borrow()
            .iter()
            .find_map(|archive| archive.files.get(&key).cloned())
            .ok_or_else(|| DomainError::command_rejected("Installed package is unavailable"))
    }
}

impl LibraryOwner for Owner {
    type Error = DomainError;
    fn package_candidates(&self) -> DomainResult<Vec<PackageCandidate>> {
        Ok(self.candidates.borrow().clone())
    }
    fn package_candidate(&self, candidate_id: &str) -> DomainResult<PackageCandidate> {
        if candidate_id == "cand-broken" {
            return Err(DomainError::command_rejected_with(
                "Package candidate record is invalid",
                "candidate_invalid",
                Vec::new(),
            ));
        }
        self.candidates.borrow().iter().find(|c| c.candidate_id() == candidate_id).cloned().ok_or_else(|| {
            DomainError::command_rejected_with(
                "Package candidate is unavailable",
                "candidate_unavailable",
                Vec::new(),
            )
        })
    }
    fn package_file_paths(&self, id: &str, version: &str, sha256: &str, _: i64) -> DomainResult<Vec<String>> {
        let mut paths: Vec<String> = self.files(id, version, sha256)?.into_keys().collect();
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
        let files = self.files(id, version, sha256)?;
        let data = files
            .get(path)
            .ok_or_else(|| DomainError::command_rejected("Package source path is unavailable"))?;
        Ok(String::from_utf8(data.clone()).unwrap())
    }
    fn package_record(&self, id: &str, version: &str, sha256: &str) -> DomainResult<JsonMap> {
        let key = (id.to_owned(), version.to_owned(), sha256.to_owned());
        self.archives
            .borrow()
            .iter()
            .find_map(|archive| archive.records.get(&key).cloned())
            .ok_or_else(|| DomainError::command_rejected("Installed package is unavailable"))
    }
    fn package_label(&self, _: &str, _: &str, _: &str) -> DomainResult<String> {
        unreachable!("not read")
    }
}

impl ReferenceOwner for Owner {
    fn install_package_archive(&self, archive: &[u8], install_id: &str) -> DomainResult<PackageCandidate> {
        let admitted = Archive::new(archive);
        self.installs.borrow_mut().push(json!([install_id, sha256_hex(archive)]));
        let candidate = PackageCandidate::new(
            format!("cand-{install_id}"),
            install_id,
            CandidateStatus::Validated,
            admitted.closure.clone(),
            admitted.lock.clone(),
            JsonMap::new(),
        )?;
        self.archives.borrow_mut().push(admitted);
        self.candidates.borrow_mut().push(candidate.clone());
        Ok(candidate)
    }
}

fn result_shape(mut result: JsonMap) -> Json {
    if let Some(Json::Object(plan)) = result.get_mut("plan") {
        let payload = sha256_hex(canonical_json_bytes(&plan["payload"]));
        let markdown = sha256_hex(plan["readable_markdown"].as_str().unwrap().as_bytes());
        plan.insert("payload".into(), json!(payload));
        plan.insert("readable_markdown".into(), json!(markdown));
    }
    Json::Object(result)
}

fn outcome(result: DomainResult<JsonMap>, owner: &Owner) -> Json {
    let installs = Json::Array(owner.installs.borrow().clone());
    match result {
        Ok(value) => json!(["ok", result_shape(value), installs]),
        Err(error) => json!([error.python_class(), error.message(), error.code(), installs]),
    }
}

fn names(vectors: &Json, owner: &str) -> Vec<String> {
    vectors["owners"][owner].as_array().unwrap().iter().map(|n| n.as_str().unwrap().to_owned()).collect()
}

#[test]
fn builder_references_match_python() {
    let vectors = vectors();
    let root = repository();
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let mut archives = HashMap::new();
    archives.insert(
        "riichi".to_owned(),
        Archive::new(&std::fs::read(root.join(vectors["riichi"].as_str().unwrap())).unwrap()),
    );
    archives.insert(
        "retained".to_owned(),
        Archive::new(&std::fs::read(root.join(vectors["retained"].as_str().unwrap())).unwrap()),
    );
    archives.insert("current".to_owned(), Archive::new(&bundled.bundled_rift_archive().unwrap()));
    let work = Path::new(env!("CARGO_TARGET_TMPDIR")).join("builder-reference-vectors");
    let _ = std::fs::remove_dir_all(&work);
    let storage = work.join("storage");
    std::fs::create_dir_all(&storage).unwrap();
    let rift = bundled.bundled_rift_campaign_plan().unwrap();
    let sibling = save_reusable_rift_plan(
        &storage,
        &bundled,
        "builder-references",
        "Saved Rift",
        &rift.payload,
        &rift.readable_markdown,
        None,
    )
    .unwrap();
    assert_eq!(sibling.artifact_id, vectors["sibling"]);
    let catalog: Vec<JsonMap> =
        vectors["catalog"].as_array().unwrap().iter().map(|row| row.as_object().unwrap().clone()).collect();
    let mut failures = Vec::new();
    for case in vectors["cases"].as_array().unwrap() {
        let owner = Owner::new(&archives, &names(&vectors, case["owner"].as_str().unwrap()));
        let c = &case["choices"];
        let choices = ContentChoices {
            ruleset_id: c[0].clone(),
            setting_id: c[1].clone(),
            setting_candidate_id: c[2].clone(),
            plan_id: c[3].clone(),
        };
        let result = capture_content_references(&owner, &bundled, &storage, &catalog, &choices);
        let actual = outcome(result, &owner);
        if actual != case["outcome"] {
            failures.push(format!(
                "{} {}: python {}, rust {actual}",
                case["owner"], case["choices"], case["outcome"]
            ));
        }
    }
    let session = SessionRecord::new(SessionRecordFields {
        session_id: "builder-session".into(),
        campaign_id: None,
        role: WriterRole::Builder,
        provider_id: "provider".into(),
        model_id: "model".into(),
        ..Default::default()
    })
    .unwrap();
    for case in vectors["launches"].as_array().unwrap() {
        let mut messages = Vec::new();
        for (index, item) in vectors["interview"].as_array().unwrap().iter().enumerate() {
            let message = SessionMessage::new(
                "builder-session",
                index as i64 + 1,
                item[0].as_str().unwrap(),
                SessionMessageKind::User,
                &item[1],
                None,
            )
            .unwrap();
            messages.push(message);
        }
        for (index, state) in
            vectors["state_sets"][case["states"].as_str().unwrap()].as_array().unwrap().iter().enumerate()
        {
            let sequence = messages.len() as i64 + 1;
            let message = SessionMessage::new(
                "builder-session",
                sequence,
                format!("state-{index}"),
                SessionMessageKind::Evidence,
                state,
                None,
            )
            .unwrap();
            messages.push(message);
        }
        let context = SessionContext { session: session.clone(), messages };
        let owner = Owner::new(&archives, &names(&vectors, case["owner"].as_str().unwrap()));
        let result = capture_launch_references(&owner, &bundled, &storage, &context, &catalog);
        let actual = outcome(result, &owner);
        if actual != case["outcome"] {
            failures.push(format!(
                "launch {} {}: python {}, rust {actual}",
                case["states"], case["owner"], case["outcome"]
            ));
        }
    }
    let _ = std::fs::remove_dir_all(&work);
    let shown: Vec<String> = failures.iter().take(6).map(|f| f.chars().take(1000).collect()).collect();
    assert!(failures.is_empty(), "{} differ: {shown:#?}", failures.len());
}
