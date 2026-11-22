//! `application/builder_references.py`, its capture half: `capture_launch_references` and
//! `capture_content_references`, which resolve a Builder chat's content choices once and retain
//! the Plan text and immutable package identities. The reference readers follow separately.
//!
//! The owner is [`ReferenceOwner`]: `LibraryOwner`'s reads and `install_package_archive`.
//! `AuthorityStore` implements both, and its installs are `package_library`'s.

use crate::bundled_packages::BundledPackages;
use crate::campaign_plan_sources::validate_retained_campaign_plan;
use crate::library_sources::LibraryOwner;
use crate::package_library::install_package_archive;
use std::path::Path;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{PackageLockEntry, PackageRole};
use sylvia_domain::campaign_plan::ValidatedCampaignPlan;
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::package_library::{campaign_setting_entries, PackageCandidate};
use sylvia_domain::pyops::{py_contains, py_eq, py_get_attr, py_item, py_iter, py_str, py_truthy};
use sylvia_domain::session::{SessionContext, SessionMessageKind};
use sylvia_store::{AuthorityStore, StoreError};

/// An owner error read back as the domain error it stands for: its class, code and text.
pub trait OwnerError: From<DomainError> {
    fn as_domain(&self) -> DomainError;
}

impl OwnerError for DomainError {
    fn as_domain(&self) -> DomainError {
        self.clone()
    }
}

impl OwnerError for StoreError {
    fn as_domain(&self) -> DomainError {
        match self.rejection() {
            Some(coded) if coded.error_type == Some("ValueError") => DomainError::value(coded.message),
            Some(coded) => DomainError::Rejected {
                error_type: coded.error_type.unwrap_or("CommandRejected"),
                code: coded.code,
                message: coded.message,
                diagnostics: Vec::new(),
                mechanics: None,
            },
            None => DomainError::uncaught("StoreError", self.to_string()),
        }
    }
}

/// The owner `capture_content_references` uses: its reads, and `install_package_archive`.
pub trait ReferenceOwner: LibraryOwner<Error: OwnerError> {
    /// `owner.install_package_archive(io.BytesIO(archive), install_id=...)`.
    fn install_package_archive(
        &self,
        archive: &[u8],
        install_id: &str,
    ) -> Result<PackageCandidate, Self::Error>;
}

impl ReferenceOwner for AuthorityStore {
    fn install_package_archive(
        &self,
        archive: &[u8],
        install_id: &str,
    ) -> Result<PackageCandidate, StoreError> {
        install_package_archive(self, archive, install_id)
    }
}

/// [`LibraryOwner`] with the owner's errors read back as domain errors, for a Plan validator.
struct DomainOwner<'a, O>(&'a O);

impl<O: ReferenceOwner> LibraryOwner for DomainOwner<'_, O> {
    type Error = DomainError;
    fn package_candidates(&self) -> DomainResult<Vec<PackageCandidate>> {
        self.0.package_candidates().map_err(|error| error.as_domain())
    }
    fn package_candidate(&self, candidate_id: &str) -> DomainResult<PackageCandidate> {
        self.0.package_candidate(candidate_id).map_err(|error| error.as_domain())
    }
    fn package_file_paths(
        &self,
        id: &str,
        version: &str,
        sha256: &str,
        maximum_files: i64,
    ) -> DomainResult<Vec<String>> {
        self.0.package_file_paths(id, version, sha256, maximum_files).map_err(|error| error.as_domain())
    }
    fn package_text_file(
        &self,
        id: &str,
        version: &str,
        sha256: &str,
        path: &str,
        maximum_bytes: i64,
    ) -> DomainResult<String> {
        self.0.package_text_file(id, version, sha256, path, maximum_bytes).map_err(|error| error.as_domain())
    }
    fn package_record(&self, id: &str, version: &str, sha256: &str) -> DomainResult<JsonMap> {
        self.0.package_record(id, version, sha256).map_err(|error| error.as_domain())
    }
    fn package_label(&self, id: &str, version: &str, sha256: &str) -> DomainResult<String> {
        self.0.package_label(id, version, sha256).map_err(|error| error.as_domain())
    }
}

/// The content choices `capture_content_references` resolves; JSON `null` is Python's `None`.
#[derive(Debug, Clone, Default)]
pub struct ContentChoices {
    pub ruleset_id: Json,
    pub setting_id: Json,
    pub setting_candidate_id: Json,
    pub plan_id: Json,
}

/// `capture_launch_references(owner, storage_root, context, catalog)`: resolve selected content
/// once from the chat's latest Campaign Interview state.
pub fn capture_launch_references<O: ReferenceOwner>(
    owner: &O,
    bundled: &BundledPackages,
    storage_root: &Path,
    context: &SessionContext,
    catalog: &[JsonMap],
) -> Result<JsonMap, O::Error> {
    let states: Vec<&JsonMap> = context
        .messages
        .iter()
        .filter(|message| {
            message.kind() == SessionMessageKind::Evidence
                && message
                    .content()
                    .get("kind")
                    .is_some_and(|kind| py_eq(kind, &Json::from("campaign_interview_state")))
        })
        .map(|message| message.content())
        .collect();
    let answers =
        states.last().and_then(|state| state.get("answers")).cloned().unwrap_or(Json::Object(JsonMap::new()));
    let selection = |step: &str, key: &str| -> Json {
        let Json::Object(answers) = &answers else { return Json::Null };
        let wanted = py_str(answers.get(step).unwrap_or(&Json::Null));
        let message = context.messages.iter().rev().find(|message| message.message_id() == wanted);
        let value = message.and_then(|message| message.content().get("interview_value"));
        match value {
            Some(Json::Object(value)) => value.get(key).cloned().unwrap_or(Json::Null),
            _ => Json::Null,
        }
    };
    let choices = ContentChoices {
        ruleset_id: selection("ruleset", "candidate_id"),
        setting_id: selection("setting", "setting_id"),
        setting_candidate_id: selection("setting", "candidate_id"),
        plan_id: selection("plan", "plan_id"),
    };
    capture_content_references(owner, bundled, storage_root, catalog, &choices)
}

fn rejected<E: From<DomainError>>(message: &str) -> E {
    DomainError::command_rejected(message).into()
}

/// `item.get(key)` of a catalog row or binding, as Python's `Mapping.get`.
fn get<'a>(value: &'a Json, key: &str) -> DomainResult<Option<&'a Json>> {
    py_get_attr(value, key)
}

fn primary_of(candidate: &PackageCandidate) -> Option<&PackageLockEntry> {
    candidate.package_lock().entries().iter().find(|entry| entry.role() == PackageRole::Primary)
}

/// `(entry.package_id, entry.version, entry.sha256) == identity`, with Python's `==` per item.
fn same_identity(entry: &PackageLockEntry, identity: &[&Json; 3]) -> bool {
    py_eq(&Json::from(entry.package_id()), identity[0])
        && py_eq(&Json::from(entry.version()), identity[1])
        && py_eq(&Json::from(entry.sha256()), identity[2])
}

fn install_bundled<O: ReferenceOwner>(owner: &O, archive: &[u8]) -> Result<PackageCandidate, O::Error> {
    let suffix = &sha256_hex(archive)[..24];
    owner.install_package_archive(archive, &format!("install-bundled-rift-{suffix}"))
}

/// `capture_content_references(owner, storage_root, catalog, ruleset_id=..., setting_id=...,
/// setting_candidate_id=..., plan_id=...)`: resolve explicit content choices through the same
/// snapshot checks as an Interview.
pub fn capture_content_references<O: ReferenceOwner>(
    owner: &O,
    bundled: &BundledPackages,
    storage_root: &Path,
    catalog: &[JsonMap],
    choices: &ContentChoices,
) -> Result<JsonMap, O::Error> {
    let ContentChoices { ruleset_id, setting_id, setting_candidate_id, plan_id } = choices;
    let mut selected: Option<Json> = None;
    for item in catalog {
        let item_json = Json::Object(item.clone());
        if !item.get("kind").is_some_and(|kind| py_eq(kind, &Json::from("ruleset"))) {
            continue;
        }
        let selection_id = match item.get("selection_id") {
            Some(value) => value.clone(),
            None => item.get("id").cloned().unwrap_or(Json::Null),
        };
        let empty = Json::Array(Vec::new());
        let aliases = item.get("selection_aliases").unwrap_or(&empty);
        if py_eq(&selection_id, ruleset_id) || py_contains(ruleset_id, aliases)? {
            selected = Some(item_json);
            break;
        }
    }
    // An explicitly retained candidate owns the Setting identity across catalog refreshes.
    let mut selected_setting: Option<Json> = None;
    if !setting_candidate_id.is_string() {
        let empty = Json::Object(JsonMap::new());
        let settings = match get(selected.as_ref().unwrap_or(&empty), "compatible_settings")? {
            Some(settings) => py_iter(settings)?,
            None => Vec::new(),
        };
        for item in settings {
            if get(&item, "id")?.is_some_and(|id| py_eq(id, setting_id)) {
                selected_setting = Some(item);
                break;
            }
        }
    }
    let mut candidate: Option<PackageCandidate> = if let Json::String(id) = setting_candidate_id {
        Some(owner.package_candidate(id)?)
    } else if let Some(setting) = &selected_setting {
        match get(setting, "candidate_id")? {
            Some(id) if py_truthy(id) => {
                Some(owner.package_candidate(&py_str(py_item(setting, "candidate_id")?))?)
            }
            _ => None,
        }
    } else {
        None
    };
    if let (None, Json::String(id)) = (&selected, ruleset_id) {
        match owner.package_candidate(id) {
            Ok(ruleset_candidate) => {
                let primary = primary_of(&ruleset_candidate)
                    .ok_or_else(|| DomainError::uncaught("StopIteration", ""))?;
                let mut identity = JsonMap::new();
                identity.insert("id".into(), Json::from(primary.package_id()));
                identity.insert("version".into(), Json::from(primary.version()));
                identity.insert("content_sha256".into(), Json::from(primary.sha256()));
                selected = Some(Json::Object(identity));
                candidate = candidate.or(Some(ruleset_candidate));
            }
            Err(error) => {
                let domain = error.as_domain();
                if domain.python_class() != "CommandRejected"
                    || domain.code() != Some("candidate_unavailable")
                {
                    return Err(error);
                }
            }
        }
    }
    if let (Some(candidate), Some(selected)) = (&candidate, &selected) {
        let id = py_item(selected, "id")?;
        let owns =
            candidate.package_lock().entries().iter().any(|entry| {
                entry.role() == PackageRole::Primary && py_eq(&Json::from(entry.package_id()), id)
            });
        if !owns {
            return Err(rejected("Selected Builder Setting belongs to another Ruleset"));
        }
    }
    if let Some(selected) = &selected {
        let null = Json::Null;
        let identity = [
            py_item(selected, "id")?,
            py_item(selected, "version")?,
            get(selected, "content_sha256")?.unwrap_or(&null),
        ];
        if candidate.is_none() {
            let setting_identity = match &selected_setting {
                Some(setting) => Some([
                    py_item(setting, "id")?,
                    py_item(setting, "version")?,
                    py_item(setting, "content_sha256")?,
                ]),
                None => None,
            };
            candidate = owner.package_candidates()?.into_iter().find(|item| {
                let lock = item.package_lock();
                lock.entries()
                    .iter()
                    .any(|entry| entry.role() == PackageRole::Primary && same_identity(entry, &identity))
                    && setting_identity.as_ref().is_none_or(|setting| {
                        campaign_setting_entries(lock).iter().any(|entry| same_identity(entry, setting))
                    })
            });
        }
        if candidate.is_none() {
            let ruleset = &bundled.bundled_rift_authority()?.ruleset;
            let bundled_identity = [
                &Json::from(ruleset.package_id.as_str()),
                &Json::from(ruleset.version.as_str()),
                &Json::from(ruleset.content_sha256.as_str()),
            ];
            if (0..3).all(|index| py_eq(identity[index], bundled_identity[index])) {
                candidate = Some(install_bundled(owner, &bundled.bundled_rift_archive()?)?);
            } else {
                return Err(rejected("Selected Builder Ruleset references are unavailable"));
            }
        }
    } else if !ruleset_id.is_null() {
        return Err(rejected("Selected Builder Ruleset is unavailable"));
    }

    let mut plan = Json::Null;
    let mut selected_plan: Option<JsonMap> = catalog
        .iter()
        .find(|item| {
            item.get("kind").is_some_and(|kind| py_eq(kind, &Json::from("campaign_plan")))
                && item.get("id").is_some_and(|id| py_eq(id, plan_id))
        })
        .cloned();
    if selected_plan.is_none() && py_eq(plan_id, &Json::from("the-rift-awakening")) {
        // Older Interviews may retain this hidden selection. Resolve its owned snapshot without
        // adding it to the current discovery catalogue.
        if let Some(retained) = bundled.bundled_plan("the-rift-awakening")? {
            let mut row = retained.catalog_payload();
            row.insert("bundled".into(), Json::Bool(true));
            selected_plan = Some(row);
        }
    }
    if let Some(selected_plan) = &selected_plan {
        let plan_key = py_str(plan_id);
        let flagged = |key: &str| selected_plan.get(key).is_some_and(py_truthy);
        let (reusable, validated): (_, ValidatedCampaignPlan) = if flagged("reusable") || flagged("bundled") {
            let domain_owner = DomainOwner(owner);
            let validate = |payload: &Json, markdown: &str| {
                validate_retained_campaign_plan(&domain_owner, bundled, payload, markdown, None)
            };
            let reusable = bundled.selected_campaign_plan(storage_root, &plan_key, Some(&validate))?;
            if !selected_plan
                .get("content_sha256")
                .is_some_and(|value| py_eq(value, &Json::from(reusable.content_sha256.as_str())))
            {
                return Err(rejected("Selected Builder Campaign Plan reference changed"));
            }
            let validated = reusable.plan.clone();
            (reusable, validated)
        } else {
            let reusable = bundled.selected_campaign_plan(storage_root, &plan_key, None)?;
            let validated = reusable.plan.clone();
            if !py_eq(&validated.payload["id"], plan_id) {
                return Err(rejected("Selected Builder Campaign Plan reference is unavailable"));
            }
            (reusable, validated)
        };
        let bindings = Json::Object(
            py_item(&Json::Object(validated.payload.clone()), "bindings")?
                .as_object()
                .cloned()
                .unwrap_or_default(),
        );
        // A retained Plan owns its exact Setting snapshot, including plan-specific characters and
        // secrets. The explicit Setting ID still must match below.
        selected_setting = Some(py_item(&bindings, "campaign_setting")?.clone());
        let ruleset_matches = match &selected {
            Some(selected) => py_eq(py_item(py_item(&bindings, "ruleset")?, "id")?, py_item(selected, "id")?),
            None => false,
        };
        if !ruleset_matches || !py_eq(py_item(py_item(&bindings, "campaign_setting")?, "id")?, setting_id) {
            return Err(rejected("Selected Builder Campaign Plan does not belong to these sources"));
        }
        let closure = py_item(&bindings, "package_closure_sha256")?;
        candidate = owner
            .package_candidates()?
            .into_iter()
            .find(|item| py_eq(&Json::from(item.closure_sha256()), closure));
        if candidate.is_none() {
            let archive = match reusable.package_snapshot.clone() {
                Some(archive) => archive,
                None => bundled.bundled_rift_archive_for_closure(closure)?.ok_or_else(|| {
                    rejected::<DomainError>("Selected Builder Campaign Plan snapshot is unavailable")
                })?,
            };
            candidate = Some(install_bundled(owner, &archive)?);
        }
        let mut row = JsonMap::new();
        row.insert("id".into(), Json::from(validated.artifact_id.as_str()));
        row.insert("selection_id".into(), plan_id.clone());
        row.insert("content_sha256".into(), Json::from(validated.content_sha256.as_str()));
        row.insert("title".into(), Json::from(validated.title.as_str()));
        row.insert("payload".into(), Json::Object(validated.payload.clone()));
        row.insert("readable_markdown".into(), Json::from(validated.readable_markdown.as_str()));
        plan = Json::Object(row);
    } else if !plan_id.is_null() {
        return Err(rejected("Selected Builder Campaign Plan reference is unavailable"));
    }

    if !setting_id.is_null() {
        let available = match &candidate {
            None => false,
            Some(candidate) => {
                let mut found = false;
                for entry in campaign_setting_entries(candidate.package_lock()) {
                    if !py_eq(&Json::from(entry.package_id()), setting_id) {
                        continue;
                    }
                    let exact = match &selected_setting {
                        None => true,
                        Some(setting) => {
                            py_eq(&Json::from(entry.version()), py_item(setting, "version")?)
                                && py_eq(&Json::from(entry.sha256()), py_item(setting, "content_sha256")?)
                        }
                    };
                    if exact {
                        found = true;
                        break;
                    }
                }
                found
            }
        };
        if !available {
            return Err(rejected("Selected Builder Campaign Setting snapshot is unavailable"));
        }
    }
    let mut packages = Vec::new();
    if let Some(candidate) = &candidate {
        for entry in candidate.package_lock().entries() {
            let prefix = if entry.role() == PackageRole::Primary {
                "ruleset".to_owned()
            } else if py_eq(&Json::from(entry.package_id()), setting_id) {
                "setting".to_owned()
            } else {
                format!("dependencies/{}", entry.package_id())
            };
            let mut row = JsonMap::new();
            row.insert("prefix".into(), Json::from(prefix));
            row.insert("id".into(), Json::from(entry.package_id()));
            row.insert("version".into(), Json::from(entry.version()));
            row.insert("content_sha256".into(), Json::from(entry.sha256()));
            packages.push(Json::Object(row));
        }
    }
    let mut result = JsonMap::new();
    result.insert(
        "candidate_id".into(),
        candidate.as_ref().map_or(Json::Null, |c| Json::from(c.candidate_id())),
    );
    result.insert("packages".into(), Json::Array(packages));
    result.insert("plan".into(), plan);
    Ok(result)
}
