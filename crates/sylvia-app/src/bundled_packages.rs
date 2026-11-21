//! `application/bundled_packages.py`: deterministic archives for first-party declarative packages
//! bundled with Sylvia GM.
//!
//! Python finds the package sources beside its source tree, or under PyInstaller's `_MEIPASS`.
//! Here the caller names them in [`BundledSources`]; [`BundledSources::from_repository_root`] is
//! the source-checkout layout. Archives are materialized once per [`BundledPackages`], as
//! Python's `lru_cache` keeps them for the process.
//!
//! Not yet here: `bundled_rift_archive_for_closure` and `bundled_rift_campaign_plan` (they need
//! `plan_snapshot`) and `bundled_campaign_content_catalog` (it needs `bundled_campaign_plans`).

use crate::package_materialization::{
    materialize_package_archive, MaterializedPackageArchive, NormalizedPackageDraft,
};
use serde_json::json;
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, pystr, Json, JsonMap};
use sylvia_domain::campaign_plan::PackageBinding;
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::interview_contribution::{parse_interview_contribution_bytes, InterviewContribution};
use sylvia_domain::package_library::{
    canonical_json_bytes, manifest_from_record, parse_campaign_setting_knowledge_bytes,
    ParticipantArtifactDeclaration, CAMPAIGN_SETTING_KNOWLEDGE_PATH,
};

const SRD_PACKAGE_ID: &str = "sylvia.srd-lite.bg3";
const SEVERED_LANDS_PACKAGE_ID: &str = "pneuma.severed-lands";
/// `BUNDLED_RIICHI_PACKAGES`.
pub const BUNDLED_RIICHI_PACKAGES: [(&str, &str); 2] =
    [("pneuma.riichi-mahjong", "0.1.1"), ("pneuma.riichi-club", "0.1.1")];
const SRD_SHARED_NOTICE_FILES: [&str; 6] = [
    "ATTRIBUTION.md",
    "LICENSE",
    "COPYRIGHT",
    "LICENSING-STATUS.md",
    "SYLVIA-ADDITIONAL-PERMISSIONS.md",
    "MIT-PREVIOUS-GRANT.txt",
];
const TEXT_PACKAGE_SUFFIXES: [&str; 4] = [".json", ".md", ".yaml", ".yml"];
const SOURCE_NAMES: [&str; 13] = [
    "campaigns",
    "characters",
    "companions",
    "content",
    "dm-tools",
    "factions",
    "locations",
    "lore",
    "npcs",
    "prompts",
    "runtime",
    "support",
    "provenance",
];
const PARTY_TEMPLATE_PATH: &str = "campaigns/the-rift-awakening/party-template.json";

/// Where the bundled package sources are.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BundledSources {
    /// `rulesets/srd-lite`.
    pub srd_root: PathBuf,
    /// `campaign-settings/severed-lands`.
    pub setting_root: PathBuf,
    /// `campaign-plans/the-rift-awakening`.
    pub rift_plan_root: PathBuf,
    /// `rulesets/riichi-mahjong/riichi-mahjong-0.1.1.sylvia-gm-package`.
    pub riichi_archive: PathBuf,
    /// `sylvia_gm/campaign/the-rift-awakening.json`, the shipped Rift party template.
    pub rift_party_template: PathBuf,
}

impl BundledSources {
    /// The layout of a source checkout rooted at `root`.
    pub fn from_repository_root(root: &Path) -> Self {
        Self {
            srd_root: root.join("rulesets").join("srd-lite"),
            setting_root: root.join("campaign-settings").join("severed-lands"),
            rift_plan_root: root.join("campaign-plans").join("the-rift-awakening"),
            riichi_archive: root
                .join("rulesets")
                .join("riichi-mahjong")
                .join("riichi-mahjong-0.1.1.sylvia-gm-package"),
            rift_party_template: root
                .join("src")
                .join("sylvia_gm")
                .join("campaign")
                .join("the-rift-awakening.json"),
        }
    }
}

/// `BundledRiftAuthority`: exact package identities and Setting paths accepted by Rift Plans.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BundledRiftAuthority {
    pub ruleset: PackageBinding,
    pub setting: PackageBinding,
    pub closure_sha256: String,
    pub setting_paths: BTreeSet<String>,
    pub setting_knowledge_ids: BTreeSet<String>,
}

fn runtime_error(message: &str) -> DomainError {
    DomainError::uncaught("RuntimeError", message)
}

fn read(path: &Path) -> DomainResult<Vec<u8>> {
    std::fs::read(path)
        .map_err(|error| DomainError::uncaught("OSError", format!("{}: {error}", path.display())))
}

fn read_json(path: &Path) -> DomainResult<Json> {
    let bytes = read(path)?;
    let text =
        String::from_utf8(bytes).map_err(|_| DomainError::value("A bundled package file is not UTF-8"))?;
    pyjson::loads(&text).map_err(|error| DomainError::value(error.to_string()))
}

fn json_object(value: Json) -> DomainResult<JsonMap> {
    match value {
        Json::Object(map) => Ok(map),
        _ => Err(DomainError::value("A bundled package record is not an object")),
    }
}

/// `Path.suffix`: the final `.ext` of a name, empty for `.hidden` or a trailing dot.
fn suffix(name: &str) -> &str {
    match name.rfind('.') {
        Some(at) if at > 0 && at < name.len() - 1 => &name[at..],
        _ => "",
    }
}

fn crlf_to_lf(value: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(value.len());
    let mut at = 0;
    while at < value.len() {
        if value[at] == b'\r' && value.get(at + 1) == Some(&b'\n') {
            out.push(b'\n');
            at += 2;
        } else {
            out.push(value[at]);
            at += 1;
        }
    }
    out
}

/// `root.rglob("*")` files, as `(relative posix path, absolute path)`.
fn files_under(base: &Path, root: &Path, found: &mut Vec<(String, PathBuf)>) -> DomainResult<()> {
    let entries =
        std::fs::read_dir(base).map_err(|error| DomainError::uncaught("OSError", error.to_string()))?;
    for entry in entries {
        let entry = entry.map_err(|error| DomainError::uncaught("OSError", error.to_string()))?;
        let path = entry.path();
        if path.is_dir() {
            files_under(&path, root, found)?;
        } else if path.is_file() {
            found.push((relative_posix(root, &path), path));
        }
    }
    Ok(())
}

fn relative_posix(root: &Path, path: &Path) -> String {
    let relative = path.strip_prefix(root).expect("under the root");
    relative
        .components()
        .map(|part| part.as_os_str().to_string_lossy().into_owned())
        .collect::<Vec<_>>()
        .join("/")
}

/// `_package_payload(root)`: one checked-in package with checkout-independent text bytes.
fn package_payload(root: &Path) -> DomainResult<Vec<(String, Vec<u8>)>> {
    let mut paths = Vec::new();
    for source_name in SOURCE_NAMES {
        let source_root = root.join(source_name);
        if source_root.is_dir() {
            files_under(&source_root, root, &mut paths)?;
        }
    }
    let entries =
        std::fs::read_dir(root).map_err(|error| DomainError::uncaught("OSError", error.to_string()))?;
    for entry in entries {
        let path = entry.map_err(|error| DomainError::uncaught("OSError", error.to_string()))?.path();
        if path.is_file() && path.file_name().is_some_and(|name| name != "package-record.json") {
            paths.push((relative_posix(root, &path), path));
        }
    }
    paths.sort_by(|a, b| a.0.cmp(&b.0));
    let mut payload = Vec::with_capacity(paths.len());
    for (relative, path) in paths {
        let mut value = read(&path)?;
        let name = relative.rsplit('/').next().unwrap_or_default();
        if TEXT_PACKAGE_SUFFIXES.contains(&pystr::casefold(suffix(name)).as_str()) {
            value = crlf_to_lf(&value);
        }
        payload.push((relative, value));
    }
    Ok(payload)
}

fn format_for(record: &JsonMap) -> i64 {
    if record.contains_key("content_kind") {
        4
    } else {
        3
    }
}

/// `_package_draft(root, dependencies=...)`: one trusted checked-in source tree as a draft.
fn package_draft(root: &Path, dependencies: Option<Json>) -> DomainResult<NormalizedPackageDraft> {
    let mut record = json_object(read_json(&root.join("package-record.json"))?)?;
    if let Some(dependencies) = dependencies {
        record.insert("dependencies".into(), dependencies);
    }
    let format_version = format_for(&record);
    Ok(NormalizedPackageDraft::new(record, package_payload(root)?, format_version))
}

fn payload<'a>(draft: &'a NormalizedPackageDraft, path: &str) -> Option<&'a Vec<u8>> {
    draft.payloads.iter().find(|(name, _)| name == path).map(|(_, value)| value)
}

/// The bundled packages from one set of sources.
pub struct BundledPackages {
    sources: BundledSources,
    srd: OnceLock<MaterializedPackageArchive>,
    rift: OnceLock<MaterializedPackageArchive>,
    authority: OnceLock<BundledRiftAuthority>,
}

impl BundledPackages {
    /// The bundled packages at `sources`. Nothing is read until a function needs it.
    pub fn new(sources: BundledSources) -> Self {
        Self { sources, srd: OnceLock::new(), rift: OnceLock::new(), authority: OnceLock::new() }
    }

    fn srd_root(&self) -> DomainResult<&Path> {
        let root = self.sources.srd_root.as_path();
        if !root.is_dir() {
            return Err(runtime_error("The bundled D&D SRD package is unavailable"));
        }
        Ok(root)
    }

    fn setting_root(&self) -> DomainResult<&Path> {
        let root = self.sources.setting_root.as_path();
        if !root.is_dir() {
            return Err(runtime_error("The bundled Severed Lands package is unavailable"));
        }
        Ok(root)
    }

    fn rift_plan_root(&self) -> DomainResult<&Path> {
        let root = self.sources.rift_plan_root.as_path();
        if !root.is_dir() {
            return Err(runtime_error("The bundled Rift Campaign Plan is unavailable"));
        }
        Ok(root)
    }

    /// `_srd_source_drafts()`: authored SRD inputs, retaining claims for record regeneration.
    fn srd_source_drafts(&self) -> DomainResult<(NormalizedPackageDraft, NormalizedPackageDraft)> {
        let root = self.srd_root()?;
        let reference_record = json_object(read_json(&root.join("reference-package-record.json"))?)?;
        let mut reference_paths = vec!["content/reference-content-declarations.json".to_owned()];
        reference_paths.extend(SRD_SHARED_NOTICE_FILES.iter().map(|name| (*name).to_owned()));
        reference_paths.push("content/corpus-index.json".into());
        let mut corpus = Vec::new();
        let corpus_root = root.join("content").join("corpus");
        for entry in std::fs::read_dir(&corpus_root)
            .map_err(|error| DomainError::uncaught("OSError", error.to_string()))?
        {
            let path = entry.map_err(|error| DomainError::uncaught("OSError", error.to_string()))?.path();
            if path.extension().is_some_and(|ext| ext == "json") {
                corpus.push(relative_posix(root, &path));
            }
        }
        corpus.sort();
        reference_paths.extend(corpus);
        let mut reference_payload = Vec::with_capacity(reference_paths.len());
        for relative in &reference_paths {
            let bytes = read(&root.join(relative))?;
            let value = if relative.ends_with("MIT-PREVIOUS-GRANT.txt") { bytes } else { crlf_to_lf(&bytes) };
            reference_payload.push((relative.clone(), value));
        }
        let primary_record = json_object(read_json(&root.join("package-record.json"))?)?;
        // Lite is the active launch package; original corpus bytes stay in the non-executable
        // exact-reference dependency.
        let mut primary_payload = package_payload(root)?;
        primary_payload.retain(|(path, _)| {
            let shared = SRD_SHARED_NOTICE_FILES.contains(&path.as_str());
            let reference = reference_paths.contains(path);
            (shared || !reference)
                && path != "reference-package-record.json"
                && path != "source-payload-manifest.json"
        });
        Ok((
            NormalizedPackageDraft::new(primary_record, primary_payload, 4),
            NormalizedPackageDraft::new(reference_record, reference_payload, 4),
        ))
    }

    /// `_srd_drafts()`: reject stale checked-in SRD claims before the materializer replaces them.
    fn srd_drafts(&self) -> DomainResult<(NormalizedPackageDraft, NormalizedPackageDraft)> {
        let drafts = self.srd_source_drafts()?;
        for draft in [&drafts.0, &drafts.1] {
            let manifest =
                manifest_from_record(&Json::Object(draft.record.clone()), Some(draft.format_version))?;
            let declared: BTreeSet<&str> = manifest.files.iter().map(|item| item.path()).collect();
            let supplied: BTreeSet<&str> = draft.payloads.iter().map(|(path, _)| path.as_str()).collect();
            if declared != supplied {
                return Err(DomainError::value(
                    "Bundled SRD payload descriptor paths do not match source files",
                ));
            }
            for item in &manifest.files {
                let value = payload(draft, item.path()).expect("the paths match");
                if item.byte_count() != value.len() as i64 || item.sha256() != sha256_hex(value) {
                    return Err(DomainError::value(format!(
                        "Bundled SRD payload descriptor does not match source: {}",
                        item.path()
                    )));
                }
            }
        }
        Ok(drafts)
    }

    /// `_rift_drafts()`: the trusted Rift Setting with its contained Ruleset snapshot.
    fn rift_drafts(
        &self,
    ) -> DomainResult<(NormalizedPackageDraft, NormalizedPackageDraft, NormalizedPackageDraft)> {
        let (ruleset, reference) = self.srd_drafts()?;
        let version = sylvia_domain::pyops::py_str(&ruleset.record["version"]);
        let dependencies = json!([{
            "namespace": "ruleset_snapshot",
            "kind": "ruleset_snapshot",
            "id": SRD_PACKAGE_ID,
            "version": version,
        }]);
        let setting = package_draft(self.setting_root()?, Some(dependencies))?;
        let mut payloads = setting.payloads.clone();
        let template = read_json(&self.sources.rift_party_template)?;
        // `setting_payloads[party_template_path] = ...`: replaced in place if the Setting has one.
        match payloads.iter_mut().find(|(path, _)| path == PARTY_TEMPLATE_PATH) {
            Some(entry) => entry.1 = canonical_json_bytes(&template),
            None => payloads.push((PARTY_TEMPLATE_PATH.into(), canonical_json_bytes(&template))),
        }
        let at = payloads
            .iter()
            .position(|(path, _)| path == "content/content-declarations.json")
            .ok_or_else(|| DomainError::uncaught("KeyError", "'content/content-declarations.json'"))?;
        let text = String::from_utf8(payloads[at].1.clone())
            .map_err(|_| DomainError::value("Declarations are not UTF-8"))?;
        let mut declarations = pyjson::loads(&text).map_err(|error| DomainError::value(error.to_string()))?;
        let inclusion = json!({
            "id": "severed-lands-authored-setting-03",
            "kind": "campaign_setting",
            "family": "campaign-setting",
            "disposition": "included_nonmechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": [PARTY_TEMPLATE_PATH],
                "capabilities": [],
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "tools": [],
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["severed-lands-package-composition"]},
        });
        declarations["inclusions"]
            .as_array_mut()
            .ok_or_else(|| DomainError::value("Declarations have no inclusions"))?
            .push(inclusion);
        payloads[at].1 = canonical_json_bytes(&declarations);
        Ok((ruleset, reference, NormalizedPackageDraft::new(setting.record, payloads, 4)))
    }

    fn srd(&self) -> DomainResult<&MaterializedPackageArchive> {
        if let Some(archive) = self.srd.get() {
            return Ok(archive);
        }
        let (primary, reference) = self.srd_drafts()?;
        let materialized = materialize_package_archive(&[primary, reference])?;
        Ok(self.srd.get_or_init(|| materialized))
    }

    fn rift(&self) -> DomainResult<&MaterializedPackageArchive> {
        if let Some(archive) = self.rift.get() {
            return Ok(archive);
        }
        let (ruleset, reference, setting) = self.rift_drafts()?;
        let materialized = materialize_package_archive(&[ruleset, reference, setting])?;
        Ok(self.rift.get_or_init(|| materialized))
    }

    /// `bundled_srd_archive()`: the checked-in SRD package as one deterministic Format-4 archive.
    pub fn bundled_srd_archive(&self) -> DomainResult<Vec<u8>> {
        Ok(self.srd()?.archive.clone())
    }

    /// `bundled_srd_version()`: the bundled SRD's version without materializing its archive.
    pub fn bundled_srd_version(&self) -> DomainResult<String> {
        let record = read_json(&self.srd_root()?.join("package-record.json"))?;
        match record.get("version") {
            Some(Json::String(version)) if !version.is_empty() => Ok(version.clone()),
            _ => Err(runtime_error("The bundled D&D SRD package version is unavailable")),
        }
    }

    /// `bundled_rift_archive()`: Severed Lands with its exact contained D&D SRD snapshot.
    pub fn bundled_rift_archive(&self) -> DomainResult<Vec<u8>> {
        Ok(self.rift()?.archive.clone())
    }

    /// `bundled_riichi_archive()`: the validated Riichi and Setting archive, unchanged.
    pub fn bundled_riichi_archive(&self) -> DomainResult<Vec<u8>> {
        read(&self.sources.riichi_archive)
    }

    /// `bundled_ruleset_update_archive(package_id)`: a shipped Ruleset source, without choosing a
    /// campaign's Setting for it.
    pub fn bundled_ruleset_update_archive(&self, package_id: &str) -> DomainResult<Option<Vec<u8>>> {
        if package_id == SRD_PACKAGE_ID {
            return self.bundled_srd_archive().map(Some);
        }
        if package_id == BUNDLED_RIICHI_PACKAGES[0].0 {
            return self.bundled_riichi_archive().map(Some);
        }
        Ok(None)
    }

    /// `bundled_rift_authority()`: immutable bundled identities for Rift Campaign Plans.
    pub fn bundled_rift_authority(&self) -> DomainResult<&BundledRiftAuthority> {
        if let Some(authority) = self.authority.get() {
            return Ok(authority);
        }
        let (_, _, setting_draft) = self.rift_drafts()?;
        let knowledge_bytes = payload(&setting_draft, CAMPAIGN_SETTING_KNOWLEDGE_PATH).ok_or_else(|| {
            DomainError::uncaught("KeyError", format!("'{CAMPAIGN_SETTING_KNOWLEDGE_PATH}'"))
        })?;
        let knowledge = parse_campaign_setting_knowledge_bytes(knowledge_bytes)?;
        let materialized = self.rift()?;
        let binding = |package_id: &str| -> PackageBinding {
            let manifest =
                materialized.manifests.iter().find(|item| item.package_id == package_id).expect("bundled");
            PackageBinding {
                package_id: manifest.package_id.clone(),
                version: manifest.version.clone(),
                content_sha256: manifest.content_sha256.clone(),
            }
        };
        let authority = BundledRiftAuthority {
            ruleset: binding(SRD_PACKAGE_ID),
            setting: binding(SEVERED_LANDS_PACKAGE_ID),
            closure_sha256: materialized.closure_sha256.clone(),
            setting_paths: setting_draft.payloads.iter().map(|(path, _)| path.clone()).collect(),
            setting_knowledge_ids: knowledge.facts.iter().map(|fact| fact.identity.clone()).collect(),
        };
        Ok(self.authority.get_or_init(|| authority))
    }

    /// `bundled_rift_participant_declaration()`: the participant schema of the bundled Rift Ruleset.
    pub fn bundled_rift_participant_declaration(&self) -> DomainResult<ParticipantArtifactDeclaration> {
        let materialized = self.rift()?;
        let ruleset =
            materialized.manifests.iter().find(|item| item.package_id == SRD_PACKAGE_ID).expect("bundled");
        let declarations: Vec<&ParticipantArtifactDeclaration> =
            ruleset.participant_artifacts.iter().filter(|item| item.kind == "player_character").collect();
        match declarations.as_slice() {
            [only] => Ok((*only).clone()),
            _ => Err(runtime_error("The bundled D&D participant schema is unavailable")),
        }
    }

    /// `bundled_rift_interview_contribution()`: the Rift Plan's optional Campaign Interview questions.
    pub fn bundled_rift_interview_contribution(&self) -> DomainResult<Option<InterviewContribution>> {
        let path = self.rift_plan_root()?.join("interview-contribution.json");
        if !path.exists() {
            return Ok(None);
        }
        parse_interview_contribution_bytes(&read(&path)?, "campaign_plan").map(Some)
    }

    /// `bundled_rift_source_catalog(query)`: bounded virtual source paths for Campaign Plan Builder.
    pub fn bundled_rift_source_catalog(&self, query: &str) -> DomainResult<Vec<String>> {
        let authority = self.bundled_rift_authority()?;
        let mut values: Vec<String> =
            ["campaign-plan/example/plan.json", "campaign-plan/example/plan.md", "ruleset/identity.json"]
                .map(String::from)
                .to_vec();
        values.extend(authority.setting_paths.iter().map(|path| format!("setting/{path}")));
        let needle = pystr::casefold(pystr::strip(query));
        Ok(values
            .into_iter()
            .filter(|path| needle.is_empty() || pystr::casefold(path).contains(&needle))
            .take(512)
            .collect())
    }

    /// `read_bundled_rift_source(path)`: one package-relative Builder source, without a machine path.
    pub fn read_bundled_rift_source(&self, path: &str) -> DomainResult<String> {
        if path == "campaign-plan/example/plan.json" || path == "campaign-plan/example/plan.md" {
            let name = path.rsplit('/').next().unwrap_or_default();
            return String::from_utf8(read(&self.rift_plan_root()?.join(name))?)
                .map_err(|_| DomainError::value("A bundled Plan file is not UTF-8"));
        }
        if path == "ruleset/identity.json" {
            let record = self.srd_drafts()?.0.record;
            let authority = self.bundled_rift_authority()?;
            let tools: Vec<Json> =
                record["tools"].as_array().into_iter().flatten().map(|tool| tool["name"].clone()).collect();
            let identity = json!({
                "binding": authority.ruleset.as_payload(),
                "campaign_setting": authority.setting.as_payload(),
                "package_closure_sha256": authority.closure_sha256,
                "capabilities": record["capabilities"],
                "tools": tools,
            });
            // `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
            return Ok(String::from_utf8(canonical_json_bytes(&identity)).expect("UTF-8"));
        }
        let Some(relative) = path.strip_prefix("setting/") else {
            return Err(DomainError::value("Campaign source path is unavailable"));
        };
        if !self.bundled_rift_authority()?.setting_paths.contains(relative) {
            return Err(DomainError::value("Campaign source path is unavailable"));
        }
        let (_, _, setting) = self.rift_drafts()?;
        let bytes = payload(&setting, relative).expect("a listed Setting path");
        let value =
            String::from_utf8(bytes.clone()).map_err(|error| DomainError::value(error.to_string()))?;
        if pystr::len(&value) > 64 * 1024 {
            return Err(DomainError::value("Campaign source is too large for one Builder read"));
        }
        Ok(value)
    }
}
