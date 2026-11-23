//! Compare sylvia_app::campaign_library_selection's selection half with the frozen Python, over
//! the generator's fake owner: candidates from real admitted archives (bundled Plan snapshots,
//! the current bundled Rift archive, Riichi 0.1.1), recorded installs, archive-backed package
//! reads, participant declarations from the admitted manifests and exports of a candidate's bytes.
//!
//! The cases share one `BundledPackages`. Python clears its caches before each case; the only
//! cache with an effect outside its value is the starter roster's, which saves the same starter
//! files again, and the storage tree is not compared here.
//!
//! Regenerate with `tests/vectors/generate_selection_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use std::cell::RefCell;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use sylvia_app::builder_references::ReferenceOwner;
use sylvia_app::bundled_packages::{BundledPackages, BundledSources};
use sylvia_app::campaign_library_selection::{
    plan_with_interview_intent, require_completed_plan_characters, resolve_campaign_library_selection,
    resolve_campaign_plan_selection, CampaignLibrarySelection, SelectionOwner,
};
use sylvia_app::library_sources::LibraryOwner;
use sylvia_app::reusable_participants::save_reusable_participant;
use sylvia_app::reusable_plans::save_reusable_rift_plan;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::{PackageLock, PackageRole};
use sylvia_domain::campaign_plan::PackageBinding;
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_library::{
    canonical_json_bytes, closure_sha256, CandidateStatus, PackageCandidate, ParticipantArtifactDeclaration,
};
use sylvia_domain::{DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/selection_vectors.json")).expect("vector file is valid JSON")
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

/// The generator's `mask`: the work root as `<ROOT>`, with `/` separators after it.
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

type Identity = (String, String, String);

/// The generator's `Archive`: an admitted archive's bytes, lock, closure, records, files and
/// participant declarations.
struct Archive {
    data: Vec<u8>,
    lock: PackageLock,
    closure: String,
    records: HashMap<Identity, JsonMap>,
    files: HashMap<Identity, HashMap<String, Vec<u8>>>,
    declarations: HashMap<Identity, Vec<ParticipantArtifactDeclaration>>,
}

impl Archive {
    fn new(data: &[u8]) -> Self {
        let admitted = admit_package_archive(data).unwrap();
        let identity = |m: &sylvia_domain::package_library::PackageManifest| {
            let (a, b, c) = m.identity();
            (a.to_owned(), b.to_owned(), c.to_owned())
        };
        let mut files: HashMap<Identity, HashMap<String, Vec<u8>>> = HashMap::new();
        for file in &admitted.files {
            let owner = identity(&admitted.manifests[file.manifest]);
            files
                .entry(owner)
                .or_default()
                .insert(file.file.path().to_owned(), admitted.read_file(file).unwrap());
        }
        Self {
            data: data.to_vec(),
            closure: closure_sha256(&admitted.package_lock),
            lock: admitted.package_lock.clone(),
            records: admitted
                .manifests
                .iter()
                .map(|m| {
                    (identity(m), pyjson::loads(&m.canonical_json).unwrap().as_object().unwrap().clone())
                })
                .collect(),
            files,
            declarations: admitted
                .manifests
                .iter()
                .map(|m| (identity(m), m.participant_artifacts.clone()))
                .collect(),
        }
    }
}

/// The generator's fake `Owner`.
struct Owner {
    variant: Option<String>,
    archives: RefCell<HashMap<String, Rc<Archive>>>,
    candidates: RefCell<Vec<PackageCandidate>>,
    installs: RefCell<Vec<Json>>,
}

impl Owner {
    fn new(archives: &HashMap<String, Rc<Archive>>, names: &[String], variant: &Json) -> Self {
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
            variant: variant.as_str().map(str::to_owned),
            archives: RefCell::new(archives.clone()),
            candidates: RefCell::new(candidates),
            installs: RefCell::new(Vec::new()),
        }
    }

    fn find<T: Clone>(&self, pick: impl Fn(&Archive) -> Option<&T>) -> DomainResult<T> {
        let archives = self.archives.borrow();
        let mut names: Vec<&String> = archives.keys().collect();
        names.sort();
        names
            .into_iter()
            .find_map(|name| pick(&archives[name]).cloned())
            .ok_or_else(|| DomainError::command_rejected("Installed package is unavailable"))
    }
}

fn key(id: &str, version: &str, sha256: &str) -> Identity {
    (id.to_owned(), version.to_owned(), sha256.to_owned())
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
        let identity = key(id, version, sha256);
        let files = self.find(|archive| archive.files.get(&identity))?;
        let mut paths: Vec<String> = files.into_keys().collect();
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
        if self.variant.as_deref() == Some("broken_runtime") && path == "runtime/ruleset.yaml" {
            return Ok("rules: [unclosed".to_owned());
        }
        let identity = key(id, version, sha256);
        let files = self.find(|archive| archive.files.get(&identity))?;
        let data = files
            .get(path)
            .ok_or_else(|| DomainError::command_rejected("Package source path is unavailable"))?;
        Ok(String::from_utf8(data.clone()).unwrap())
    }
    fn package_record(&self, id: &str, version: &str, sha256: &str) -> DomainResult<JsonMap> {
        let identity = key(id, version, sha256);
        self.find(|archive| archive.records.get(&identity))
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
        self.archives.borrow_mut().insert(install_id.to_owned(), Rc::new(admitted));
        self.candidates.borrow_mut().push(candidate.clone());
        Ok(candidate)
    }
}

impl SelectionOwner for Owner {
    fn exact_participant_declarations(
        &self,
        id: &str,
        version: &str,
        sha256: &str,
    ) -> DomainResult<Vec<ParticipantArtifactDeclaration>> {
        match self.variant.as_deref() {
            Some("none") => return Ok(Vec::new()),
            Some("refuse") => {
                return Err(DomainError::command_rejected_with(
                    "Participant declarations are unavailable",
                    "declarations_unavailable",
                    Vec::new(),
                ))
            }
            Some("foreign") => {
                let archives = self.archives.borrow();
                let riichi = &archives["riichi"];
                let mut identities: Vec<&Identity> = riichi.declarations.keys().collect();
                identities.sort();
                return Ok(identities
                    .into_iter()
                    .map(|identity| &riichi.declarations[identity])
                    .find(|value| !value.is_empty())
                    .cloned()
                    .unwrap());
            }
            _ => {}
        }
        let identity = key(id, version, sha256);
        self.find(|archive| archive.declarations.get(&identity))
    }
    fn export_package_candidate(&self, candidate_id: &str) -> DomainResult<Vec<u8>> {
        if self.candidates.borrow().iter().any(|item| item.candidate_id() == candidate_id) {
            let name = candidate_id.strip_prefix("cand-").unwrap();
            return Ok(self.archives.borrow()[name].data.clone());
        }
        Err(DomainError::command_rejected_with(
            "Package candidate is unavailable",
            "candidate_unavailable",
            Vec::new(),
        ))
    }
}

fn error_of(error: &DomainError, root: &Path) -> Json {
    let diagnostics: Vec<Json> =
        error.diagnostics().iter().map(|item| Json::Object(item.as_payload())).collect();
    json!([error.python_class(), mask(error.message(), root), error.code(), diagnostics])
}

fn digest_json(value: &Json) -> String {
    sha256_hex(canonical_json_bytes(value))
}

fn selection_shape(
    result: &CampaignLibrarySelection<'_, Owner>,
    bundled: &BundledPackages,
) -> DomainResult<Json> {
    let payload = Json::Object(result.plan.plan.payload.clone());
    Ok(json!({
        "candidate_id": result.candidate.candidate_id(),
        "closure_sha256": result.candidate.closure_sha256(),
        "sources": {
            "ruleset": result.sources.ruleset.as_payload(),
            "setting": result.sources.setting.as_payload(),
            "closure_sha256": result.sources.closure_sha256,
        },
        "plan": {"artifact_id": result.plan.artifact_id, "setup_id": result.plan.setup_id,
                 "payload_sha256": digest_json(&payload)},
        "participants": result.participants.iter().map(|item| item.artifact_id.clone()).collect::<Vec<_>>(),
        "builtin": result.participants.iter().map(|item| item.builtin_starter).collect::<Vec<_>>(),
        "entity_reference": result.entity_reference,
        "required": result.required_participants.iter()
            .map(|item| json!([item.artifact_id, item.role, item.name, item.content_sha256]))
            .collect::<Vec<_>>(),
        "party_template_sha256": result.party_template.as_ref().map(digest_json),
        "party_options": result.party_options(bundled)?,
    }))
}

fn check(failures: &mut Vec<String>, label: String, actual: Json, expected: &Json) {
    if actual != *expected {
        let shown: String = actual.to_string().chars().take(900).collect();
        let wanted: String = expected.to_string().chars().take(900).collect();
        failures.push(format!("{label}: python {wanted}, rust {shown}"));
    }
}

#[test]
fn selection_matches_python() {
    let vectors = vectors();
    let root = repository();
    let bundled = BundledPackages::new(BundledSources::from_repository_root(&root));
    let snapshot = |plan_id: &str| bundled.bundled_plan(plan_id).unwrap().unwrap().package_snapshot.unwrap();
    let mut archives = HashMap::new();
    archives.insert("cart".to_owned(), Rc::new(Archive::new(&snapshot("last-cart-through"))));
    archives.insert("debt".to_owned(), Rc::new(Archive::new(&snapshot("a-debt-in-iron"))));
    archives.insert("retained".to_owned(), Rc::new(Archive::new(&snapshot("the-rift-awakening"))));
    archives.insert("current".to_owned(), Rc::new(Archive::new(&bundled.bundled_rift_archive().unwrap())));
    let riichi = vectors["archives"]["riichi"].as_str().unwrap();
    archives.insert("riichi".to_owned(), Rc::new(Archive::new(&std::fs::read(root.join(riichi)).unwrap())));
    assert_eq!(archives["cart"].closure, vectors["cart_closure"].as_str().unwrap());

    let work = fresh_work("selection-vectors");
    let storage = work.join("storage");
    std::fs::create_dir_all(&storage).unwrap();
    let rift = bundled.bundled_rift_campaign_plan().unwrap();
    let sibling = save_reusable_rift_plan(
        &storage,
        &bundled,
        "selection-vectors",
        "Saved Rift",
        &rift.payload,
        &rift.readable_markdown,
        None,
    )
    .unwrap();
    assert_eq!(sibling.artifact_id, vectors["sibling"]);
    let rows = pyjson::loads(
        &std::fs::read_to_string(root.join("src/sylvia_gm/campaign/srd-starters.json")).unwrap(),
    )
    .unwrap();
    let fighter = rows.as_array().unwrap().iter().find(|row| row["id"] == "fighter").unwrap().clone();
    let cart = &archives["cart"];
    let primary =
        cart.lock.entries().iter().find(|entry| entry.role() == PackageRole::Primary).unwrap().clone();
    let declaration =
        cart.declarations[&key(primary.package_id(), primary.version(), primary.sha256())][0].clone();
    let cart_plan = bundled.bundled_plan("last-cart-through").unwrap().unwrap();
    let setting = &cart_plan.plan.payload["bindings"]["campaign_setting"];
    let binding = PackageBinding {
        package_id: setting["id"].as_str().unwrap().to_owned(),
        version: setting["version"].as_str().unwrap().to_owned(),
        content_sha256: setting["content_sha256"].as_str().unwrap().to_owned(),
    };
    for (index, name) in ["Saved Hero", "Saved Friend"].into_iter().enumerate() {
        let mut participant = fighter["participant"].clone();
        participant["name"] = json!(name);
        let role = if name == "Saved Hero" { "player_character" } else { "companion" };
        let saved = save_reusable_participant(
            &storage,
            &format!("selection-{name}"),
            role,
            &format!("{name}."),
            &json!(format!("{name} is saved.")),
            &participant,
            &fighter["entity_state"],
            &primary,
            &declaration,
            &binding,
            None,
            None,
            None,
        )
        .unwrap();
        assert_eq!(saved.artifact_id, vectors["saved"][index]);
    }

    let names = |owner: &str| -> Vec<String> {
        vectors["owners"][owner].as_array().unwrap().iter().map(|n| n.as_str().unwrap().to_owned()).collect()
    };
    let mut failures = Vec::new();
    for case in vectors["resolve"].as_array().unwrap() {
        let owner = Owner::new(&archives, &names(case["owner"].as_str().unwrap()), &case["variant"]);
        let candidate_id = case["candidate_id"].as_str().unwrap();
        let setting_id = case["setting_id"].as_str().unwrap();
        let plan_id = case["plan_id"].as_str().unwrap();
        let result = if case["kind"] == "plan" {
            resolve_campaign_plan_selection(
                &owner,
                &bundled,
                &storage,
                candidate_id,
                setting_id,
                plan_id,
                case["setup"].as_str(),
            )
        } else {
            resolve_campaign_library_selection(&owner, &bundled, &storage, candidate_id, setting_id, plan_id)
        };
        let outcome = match result.and_then(|selection| selection_shape(&selection, &bundled)) {
            Ok(shape) => json!(["ok", shape]),
            Err(error) => error_of(&error, &work),
        };
        let label = format!(
            "{} {} {} {plan_id} {candidate_id} {setting_id} {}",
            case["kind"], case["owner"], case["variant"], case["setup"]
        );
        check(&mut failures, label.clone(), outcome, &case["outcome"]);
        check(
            &mut failures,
            format!("{label} installs"),
            Json::Array(owner.installs.borrow().clone()),
            &case["installs"],
        );
    }

    for case in vectors["complete"].as_array().unwrap() {
        let owner = Owner::new(&archives, &names(case["owner"].as_str().unwrap()), &case["variant"]);
        let plan = bundled.bundled_plan(case["plan_id"].as_str().unwrap()).unwrap().unwrap().plan;
        let outcome =
            match require_completed_plan_characters(&owner, case["candidate_id"].as_str().unwrap(), &plan) {
                Ok(()) => json!(["ok", null]),
                Err(error) => error_of(&error, &work),
            };
        check(
            &mut failures,
            format!("complete {} {}", case["candidate_id"], case["plan_id"]),
            outcome,
            &case["outcome"],
        );
    }

    let intent_owner = Owner::new(&archives, &names("all"), &Json::Null);
    let mut resolved = HashMap::new();
    for case in vectors["intent"].as_array().unwrap() {
        let plan_id = case["plan_id"].as_str().unwrap().to_owned();
        if !resolved.contains_key(&plan_id) {
            let selection = resolve_campaign_plan_selection(
                &intent_owner,
                &bundled,
                &storage,
                case["candidate_id"].as_str().unwrap(),
                "pneuma.severed-lands",
                &plan_id,
                None,
            )
            .unwrap();
            resolved.insert(plan_id.clone(), selection);
        }
        let selection = &resolved[&plan_id];
        let markdown = case["markdown"].as_str();
        let outcome =
            match plan_with_interview_intent(selection, case["intent"].as_object().unwrap(), markdown) {
                Ok(plan) => {
                    let payload = Json::Object(plan.payload.clone());
                    let original = Json::Object(selection.plan.plan.payload.clone());
                    let others = |value: &Json| -> JsonMap {
                        value
                            .as_object()
                            .unwrap()
                            .iter()
                            .filter(|(key, _)| *key != "boundaries" && *key != "gm_guidance")
                            .map(|(key, value)| (key.clone(), value.clone()))
                            .collect()
                    };
                    let base = markdown.unwrap_or(&selection.plan.plan.readable_markdown);
                    json!(["ok", {
                        "boundaries": payload["boundaries"],
                        "instructions": payload["gm_guidance"]["instructions"],
                        "markdown_sha256": sha256_hex(plan.readable_markdown.as_bytes()),
                        "markdown_tail": plan.readable_markdown[base.len()..],
                        "same_otherwise": others(&payload) == others(&original),
                        "content_sha256": plan.content_sha256,
                    }])
                }
                Err(error) => error_of(&error, &work),
            };
        check(
            &mut failures,
            format!("intent {plan_id} {} {:?}", case["intent"], markdown),
            outcome,
            &case["outcome"],
        );
    }
    let _ = std::fs::remove_dir_all(&work);
    assert!(failures.is_empty(), "{} differ: {:#?}", failures.len(), &failures[..failures.len().min(6)]);
}
