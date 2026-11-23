//! The selection and party vectors' fake owner and fixtures: builder_references' fake owner over
//! real admitted archives, with participant declarations and candidate exports, the archives the
//! generators use, and the storage root they prepare (a saved Rift sibling and two Characters).

#![allow(dead_code)]

use serde_json::json;
use std::cell::RefCell;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use sylvia_app::builder_references::ReferenceOwner;
use sylvia_app::bundled_packages::BundledPackages;
use sylvia_app::campaign_library_selection::SelectionOwner;
use sylvia_app::library_sources::LibraryOwner;
use sylvia_app::reusable_participants::save_reusable_participant;
use sylvia_app::reusable_plans::save_reusable_rift_plan;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::{PackageLock, PackageRole};
use sylvia_domain::campaign_plan::PackageBinding;
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_library::{
    closure_sha256, CandidateStatus, PackageCandidate, ParticipantArtifactDeclaration,
};
use sylvia_domain::{DomainError, DomainResult};

/// A fresh work folder: one left by a killed run must not show up later as an unrelated error.
pub fn fresh_work(name: &str) -> PathBuf {
    let work = Path::new(env!("CARGO_TARGET_TMPDIR")).join(name);
    match std::fs::remove_dir_all(&work) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => panic!("cannot clear the old work folder {}: {error}", work.display()),
    }
    work
}

/// The generator's `mask`: the work root as `<ROOT>`, with `/` separators after it.
pub fn mask(text: &str, root: &Path) -> String {
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

pub type Identity = (String, String, String);

/// The generator's `Archive`: an admitted archive's bytes, lock, closure, records, files and
/// participant declarations.
pub struct Archive {
    pub data: Vec<u8>,
    pub lock: PackageLock,
    pub closure: String,
    pub records: HashMap<Identity, JsonMap>,
    pub files: HashMap<Identity, HashMap<String, Vec<u8>>>,
    pub declarations: HashMap<Identity, Vec<ParticipantArtifactDeclaration>>,
}

impl Archive {
    pub fn new(data: &[u8]) -> Self {
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
pub struct Owner {
    variant: Option<String>,
    archives: RefCell<HashMap<String, Rc<Archive>>>,
    candidates: RefCell<Vec<PackageCandidate>>,
    pub installs: RefCell<Vec<Json>>,
}

impl Owner {
    pub fn new(archives: &HashMap<String, Rc<Archive>>, names: &[String], variant: &Json) -> Self {
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

pub fn key(id: &str, version: &str, sha256: &str) -> Identity {
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

pub fn error_of(error: &DomainError, root: &Path) -> Json {
    let diagnostics: Vec<Json> =
        error.diagnostics().iter().map(|item| Json::Object(item.as_payload())).collect();
    json!([error.python_class(), mask(error.message(), root), error.code(), diagnostics])
}

/// The generators' archives, by name.
pub fn archives(bundled: &BundledPackages, root: &Path, riichi: &str) -> HashMap<String, Rc<Archive>> {
    let snapshot = |plan_id: &str| bundled.bundled_plan(plan_id).unwrap().unwrap().package_snapshot.unwrap();
    let mut archives = HashMap::new();
    archives.insert("cart".to_owned(), Rc::new(Archive::new(&snapshot("last-cart-through"))));
    archives.insert("debt".to_owned(), Rc::new(Archive::new(&snapshot("a-debt-in-iron"))));
    archives.insert("retained".to_owned(), Rc::new(Archive::new(&snapshot("the-rift-awakening"))));
    archives.insert("current".to_owned(), Rc::new(Archive::new(&bundled.bundled_rift_archive().unwrap())));
    archives.insert("riichi".to_owned(), Rc::new(Archive::new(&std::fs::read(root.join(riichi)).unwrap())));
    archives
}

/// The generators' storage root: a saved Rift sibling and two saved Characters. Returns the
/// sibling's id and the Characters' ids.
pub fn storage(
    bundled: &BundledPackages,
    root: &Path,
    storage: &Path,
    cart: &Archive,
) -> (String, Vec<String>) {
    std::fs::create_dir_all(storage).unwrap();
    let rift = bundled.bundled_rift_campaign_plan().unwrap();
    let sibling = save_reusable_rift_plan(
        storage,
        bundled,
        "selection-vectors",
        "Saved Rift",
        &rift.payload,
        &rift.readable_markdown,
        None,
    )
    .unwrap();
    let rows = pyjson::loads(
        &std::fs::read_to_string(root.join("src/sylvia_gm/campaign/srd-starters.json")).unwrap(),
    )
    .unwrap();
    let fighter = rows.as_array().unwrap().iter().find(|row| row["id"] == "fighter").unwrap().clone();
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
    let mut saved = Vec::new();
    for name in ["Saved Hero", "Saved Friend"] {
        let mut participant = fighter["participant"].clone();
        participant["name"] = json!(name);
        let role = if name == "Saved Hero" { "player_character" } else { "companion" };
        let item = save_reusable_participant(
            storage,
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
        saved.push(item.artifact_id);
    }
    (sibling.artifact_id, saved)
}
