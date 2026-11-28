//! `application/campaign_plan_sources.py`: exact Ruleset and Setting sources shared by Builder
//! authoring and local Plan validation.
//!
//! The owner reads are [`LibraryOwner`]'s (`AuthorityStore` implements it). Python's
//! `read_setting` closure is [`CampaignPlanToolAuthority::read_setting`], which keeps the owner,
//! or, for `check_plan_draft`'s authority, the checked archive's Setting files.

use crate::bundled_packages::BundledPackages;
use crate::library_sources::LibraryOwner;
use crate::plan_snapshot::validate_plan_snapshot;
use crate::plan_state_admission::validate_starting_declarations;
use crate::setting_character_requirements::{
    parse_setting_character_requirements, CHARACTER_REQUIREMENTS_PATH,
};
use std::collections::{BTreeMap, BTreeSet};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{PackageLock, PackageRole};
use sylvia_domain::campaign_plan::{
    validate_campaign_plan, CampaignPlanExpectations, PackageBinding, ValidatedCampaignPlan,
};
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_archive::{AdmittedPackageArchive, ArchiveFile};
use sylvia_domain::package_library::{
    campaign_setting_entries, closure_sha256, parse_campaign_setting_knowledge_bytes, PackageCandidate,
    ParticipantArtifactDeclaration, CAMPAIGN_SETTING_KNOWLEDGE_PATH,
};
use sylvia_domain::pyops::{py_eq, py_str};
use sylvia_domain::starting_state::parse_state_schemas;

/// `AuthorityOwner.package_text_file`'s default `maximum_bytes`.
const TEXT_LIMIT: i64 = sylvia_store::package_reads::DEFAULT_MAXIMUM_BYTES;

/// `CampaignPlanToolAuthority`: the exact package closure and a bounded Setting reader for one
/// campaign Builder.
pub struct CampaignPlanToolAuthority<'a, O: LibraryOwner> {
    pub ruleset: PackageBinding,
    pub setting: PackageBinding,
    pub closure_sha256: String,
    pub setting_paths: BTreeSet<String>,
    pub setting_knowledge_ids: BTreeSet<String>,
    pub participant_schemas: Vec<Json>,
    pub setting_character_schema: Option<JsonMap>,
    pub state_schemas: Vec<Json>,
    reader: SettingReader<'a, O>,
}

/// Where `read_setting` reads: the owner's installed Setting, or one admitted archive's Setting
/// files by path.
enum SettingReader<'a, O> {
    Owner(&'a O),
    Archive(&'a AdmittedPackageArchive, BTreeMap<String, &'a ArchiveFile>),
}

/// `check_plan_draft`'s `read_setting`: a file of at most 256 KiB, decoded as UTF-8.
const ARCHIVE_SETTING_LIMIT: i64 = 256 * 1024;

impl<O: LibraryOwner> CampaignPlanToolAuthority<'_, O> {
    /// `read_setting(path)`: one Setting source file, refused outside its paths.
    pub fn read_setting(&self, path: &str) -> Result<String, O::Error> {
        let owner = match &self.reader {
            SettingReader::Owner(owner) => owner,
            SettingReader::Archive(admitted, files) => {
                let Some(archive_file) =
                    files.get(path).filter(|item| item.file.byte_count() <= ARCHIVE_SETTING_LIMIT)
                else {
                    return Err(
                        DomainError::value("Required Character File is unavailable or oversized").into()
                    );
                };
                let bytes = admitted.read_file(archive_file)?;
                return Ok(
                    sylvia_base::pystr::utf8_decode(&bytes).map_err(crate::reusable_plans::unicode_error)?
                );
            }
        };
        if !self.setting_paths.contains(path) {
            return Err(DomainError::value("Campaign Setting source path is unavailable").into());
        }
        let setting = &self.setting;
        owner.package_text_file(
            &setting.package_id,
            &setting.version,
            &setting.content_sha256,
            path,
            TEXT_LIMIT,
        )
    }

    /// `character_declarations`: the exact contained declarations for required-character draft
    /// checking.
    pub fn character_declarations(&self) -> DomainResult<Vec<ParticipantArtifactDeclaration>> {
        self.participant_schemas
            .iter()
            .map(|item| {
                let calculations = match item.get("initial_state_calculations") {
                    Some(Json::Array(items)) => {
                        items.iter().map(|c| c.as_object().cloned().unwrap_or_default()).collect()
                    }
                    _ => Vec::new(),
                };
                ParticipantArtifactDeclaration::new(
                    &item["kind"],
                    &item["schema_id"],
                    &item["schema_sha256"],
                    item["schema"].as_object().cloned().unwrap_or_default(),
                    item.get("entity_state_schema").and_then(Json::as_object).cloned(),
                    calculations,
                    item.get("initial_state_validation").and_then(Json::as_object).cloned(),
                )
            })
            .collect()
    }

    /// `validate(payload, readable_markdown)`: authored content against these exact source
    /// identities and references.
    pub fn validate(&self, payload: &Json, readable_markdown: &str) -> DomainResult<ValidatedCampaignPlan> {
        let validated = validate_campaign_plan(
            payload,
            readable_markdown,
            CampaignPlanExpectations {
                expected_ruleset: Some(&self.ruleset),
                expected_setting: Some(&self.setting),
                expected_closure_sha256: &self.closure_sha256,
                setting_paths: &self.setting_paths,
                setting_knowledge_ids: &self.setting_knowledge_ids,
            },
        )?;
        if !self.state_schemas.is_empty() {
            let schemas = parse_state_schemas(&Json::Array(self.state_schemas.clone()))?;
            validate_starting_declarations(
                &validated.payload,
                &self.ruleset.package_id,
                &self.ruleset.version,
                &schemas,
            )?;
        }
        Ok(validated)
    }
}

impl<'a, O: LibraryOwner> CampaignPlanToolAuthority<'a, O> {
    /// `check_plan_draft`'s `CampaignPlanToolAuthority(ruleset=..., setting=..., closure_sha256=...,
    /// setting_paths=frozenset(setting_files), read_setting=read_setting,
    /// setting_character_schema=...)`: the Setting files of one admitted archive, with no
    /// knowledge, participant or state schemas.
    pub fn from_archive(
        ruleset: PackageBinding,
        setting: PackageBinding,
        closure_sha256: String,
        admitted: &'a AdmittedPackageArchive,
    ) -> Result<Self, O::Error> {
        let identity =
            (setting.package_id.as_str(), setting.version.as_str(), setting.content_sha256.as_str());
        let files: BTreeMap<String, &ArchiveFile> = admitted
            .files
            .iter()
            .filter(|item| admitted.manifests[item.manifest].identity() == identity)
            .map(|item| (item.file.path().to_owned(), item))
            .collect();
        let mut sources: Self = CampaignPlanToolAuthority {
            ruleset,
            setting,
            closure_sha256,
            setting_paths: files.keys().cloned().collect(),
            setting_knowledge_ids: BTreeSet::new(),
            participant_schemas: Vec::new(),
            setting_character_schema: None,
            state_schemas: Vec::new(),
            reader: SettingReader::Archive(admitted, files),
        };
        if sources.setting_paths.contains(CHARACTER_REQUIREMENTS_PATH) {
            let text = sources.read_setting(CHARACTER_REQUIREMENTS_PATH)?;
            sources.setting_character_schema = Some(parse_setting_character_requirements(&text)?);
        }
        Ok(sources)
    }
}

fn rejected(message: &str) -> DomainError {
    DomainError::command_rejected(message)
}

/// `campaign_plan_sources(owner, lock, setting_id=...)`: an exact retained package closure for
/// campaign or independent Plan authoring; `None` without a primary package.
pub fn campaign_plan_sources<'a, O: LibraryOwner>(
    owner: &'a O,
    lock: &PackageLock,
    setting_id: Option<&str>,
) -> Result<Option<CampaignPlanToolAuthority<'a, O>>, O::Error> {
    let Some(primary) = lock.entries().iter().find(|entry| entry.role() == PackageRole::Primary) else {
        return Ok(None);
    };
    let settings: Vec<_> = campaign_setting_entries(lock)
        .into_iter()
        .filter(|setting| setting_id.is_none_or(|id| setting.package_id() == id))
        .collect();
    let [setting] = settings.as_slice() else {
        return Err(
            rejected("Campaign Plan construction requires exactly one Campaign Setting snapshot").into()
        );
    };
    let setting_paths: BTreeSet<String> = owner
        .package_file_paths(setting.package_id(), setting.version(), setting.sha256(), 512)?
        .into_iter()
        .collect();
    if setting_paths.is_empty() {
        return Err(rejected("Campaign Setting package has no readable source files").into());
    }
    let mut setting_knowledge_ids = BTreeSet::new();
    if setting_paths.contains(CAMPAIGN_SETTING_KNOWLEDGE_PATH) {
        let text = owner.package_text_file(
            setting.package_id(),
            setting.version(),
            setting.sha256(),
            CAMPAIGN_SETTING_KNOWLEDGE_PATH,
            TEXT_LIMIT,
        )?;
        let knowledge = parse_campaign_setting_knowledge_bytes(text.as_bytes()).map_err(|error| {
            if error.is_value_error() {
                rejected("Campaign Setting knowledge is invalid")
            } else {
                error
            }
        })?;
        setting_knowledge_ids = knowledge.facts.into_iter().map(|item| item.identity).collect();
    }
    let primary_record = owner.package_record(primary.package_id(), primary.version(), primary.sha256())?;
    let list = |key: &str| match primary_record.get(key) {
        Some(Json::Array(items)) => items.clone(),
        _ => Vec::new(),
    };
    let mut sources = CampaignPlanToolAuthority {
        ruleset: PackageBinding {
            package_id: primary.package_id().into(),
            version: primary.version().into(),
            content_sha256: primary.sha256().into(),
        },
        setting: PackageBinding {
            package_id: setting.package_id().into(),
            version: setting.version().into(),
            content_sha256: setting.sha256().into(),
        },
        closure_sha256: closure_sha256(lock),
        setting_paths,
        setting_knowledge_ids,
        participant_schemas: list("participant_artifacts"),
        setting_character_schema: None,
        state_schemas: list("state_schemas"),
        reader: SettingReader::Owner(owner),
    };
    if sources.setting_paths.contains(CHARACTER_REQUIREMENTS_PATH) {
        let text = sources.read_setting(CHARACTER_REQUIREMENTS_PATH)?;
        sources.setting_character_schema = Some(parse_setting_character_requirements(&text)?);
    }
    Ok(Some(sources))
}

/// `validate_retained_campaign_plan(owner, payload, readable_markdown, candidates=...)`: saved
/// bindings are lookup hints; the Plan is validated against the exact retained packages.
pub fn validate_retained_campaign_plan<O: LibraryOwner>(
    owner: &O,
    bundled: &BundledPackages,
    payload: &Json,
    readable_markdown: &str,
    candidates: Option<&[PackageCandidate]>,
) -> Result<ValidatedCampaignPlan, O::Error> {
    let Some(Json::Object(bindings)) = payload.as_object().and_then(|map| map.get("bindings")) else {
        return Err(DomainError::value("Reusable Campaign Plan bindings are invalid").into());
    };
    let Some(Json::Object(setting)) = bindings.get("campaign_setting") else {
        return Err(DomainError::value("Reusable Campaign Plan Setting binding is invalid").into());
    };
    let closure = bindings.get("package_closure_sha256").unwrap_or(&Json::Null);
    let owned;
    let available = match candidates {
        Some(candidates) => candidates,
        None => {
            owned = owner.package_candidates()?;
            &owned
        }
    };
    let candidate = available.iter().find(|item| py_eq(&Json::from(item.closure_sha256()), closure));
    if let Some(candidate) = candidate {
        let setting_id = py_str(setting.get("id").unwrap_or(&Json::Null));
        let Some(sources) = campaign_plan_sources(owner, candidate.package_lock(), Some(&setting_id))? else {
            return Err(DomainError::value("Reusable Campaign Plan Ruleset is unavailable").into());
        };
        return Ok(sources.validate(payload, readable_markdown)?);
    }
    let authority = bundled.bundled_rift_authority()?;
    if !py_eq(closure, &Json::from(authority.closure_sha256.as_str())) {
        let Some(snapshot) = bundled.bundled_rift_archive_for_closure(closure)? else {
            return Err(DomainError::value("Reusable Campaign Plan exact packages are unavailable").into());
        };
        return Ok(validate_plan_snapshot(&snapshot, payload, readable_markdown)?);
    }
    Ok(validate_campaign_plan(
        payload,
        readable_markdown,
        CampaignPlanExpectations {
            expected_ruleset: Some(&authority.ruleset),
            expected_setting: Some(&authority.setting),
            expected_closure_sha256: &authority.closure_sha256,
            setting_paths: &authority.setting_paths,
            setting_knowledge_ids: &authority.setting_knowledge_ids,
        },
    )?)
}
