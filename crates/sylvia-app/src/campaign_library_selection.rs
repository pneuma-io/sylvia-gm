//! `application/campaign_library_selection.py`: resolve saved campaign choices against their exact
//! installed source packages.
//!
//! Here: `participant_catalog_payload` (with `_character_state_reference` and
//! `_participant_choice`), and the selection: [`CampaignLibrarySelection`] with `party_options`,
//! `read_required_plan_characters`, `require_completed_plan_characters`,
//! `resolve_campaign_plan_selection` (with `_plan_party_template`),
//! `resolve_campaign_library_selection` and `plan_with_interview_intent`. The party
//! materialization (`materialize_library_party`, `materialize_template_party`) follows separately.
//!
//! The owner is [`SelectionOwner`]: the reference owner plus `exact_participant_declarations` and
//! `export_package_candidate`. `AuthorityStore` implements it; its export answers
//! `rust_port_pending` until lane 3's `export_package_candidate` lands, and the selection reaches
//! it only when an installed candidate matches the Plan's closure, which is the branch that does
//! not need a snapshot.
//!
//! Python's `except` clauses name exception families; [`OwnerError::as_domain`] reads an owner
//! error's class for them.

use crate::builder_references::{DomainOwner, OwnerError, ReferenceOwner};
use crate::bundled_packages::BundledPackages;
use crate::bundled_starter_characters::available_reusable_participants;
use crate::campaign_plan_sources::{
    campaign_plan_sources, validate_retained_campaign_plan, CampaignPlanToolAuthority,
};
use crate::campaign_rift::{rift_public_template, validate_rift_template, RIFT_TEMPLATE_ID};
use crate::interview_content::require_active_ruleset;
use crate::plan_snapshot::{retained_plan_snapshot, PlanSnapshotOwner};
use crate::reusable_participants::parse_setting_character;
use crate::reusable_plans::ReusableCampaignPlan;
use serde_json::json;
use std::path::Path;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pystr;
use sylvia_domain::authority::{PackageLockEntry, PackageRole};
use sylvia_domain::campaign_plan::{
    parse_starting_party_policy, CampaignPlanCharacterReference, StartingPartyPolicy, ValidatedCampaignPlan,
};
use sylvia_domain::package_library::{
    canonical_json_bytes, PackageCandidate, ParticipantArtifactDeclaration,
};
use sylvia_domain::pyops::{py_eq, py_str};
use sylvia_store::{AuthorityStore, StoreError};

use crate::library_sources::LibraryOwner;
use crate::package_runtime::{
    package_model_decision_boundary, PackageRuntimeFailure, PACKAGE_RUNTIME_BYTE_LIMIT, PACKAGE_RUNTIME_PATH,
};
use crate::reusable_participants::ReusableParticipant;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::error::{DomainError, DomainResult};

/// `AuthorityOwner.package_file_paths`' default `maximum_files`.
const FILE_LIMIT: i64 = sylvia_store::package_reads::DEFAULT_MAXIMUM_FILES;

/// A `PackageRuntimeError` (a `ValueError` with diagnostics), or the exception Python lets through.
fn runtime_error(failure: PackageRuntimeFailure) -> DomainError {
    match failure {
        PackageRuntimeFailure::Rejected(error) => DomainError::Diagnosed {
            error_type: "PackageRuntimeError",
            message: error.message,
            diagnostics: error.diagnostics,
        },
        PackageRuntimeFailure::Uncaught { class, message } => DomainError::uncaught(class, message),
    }
}

/// `_character_state_reference(owner, package_id, version, sha256)`: the exact Ruleset location
/// used by both readiness and party materialization.
pub fn character_state_reference<O: LibraryOwner>(
    owner: &O,
    package_id: &str,
    version: &str,
    sha256: &str,
) -> Result<Option<JsonMap>, O::Error> {
    let paths = owner.package_file_paths(package_id, version, sha256, FILE_LIMIT)?;
    if !paths.iter().any(|path| path == PACKAGE_RUNTIME_PATH) {
        return Ok(None);
    }
    let runtime = owner.package_text_file(
        package_id,
        version,
        sha256,
        PACKAGE_RUNTIME_PATH,
        PACKAGE_RUNTIME_BYTE_LIMIT as i64,
    )?;
    let boundary = package_model_decision_boundary(package_id, runtime.as_bytes()).map_err(runtime_error)?;
    Ok(boundary.and_then(|boundary| boundary.get("actor_entities").and_then(Json::as_object).cloned()))
}

/// `_participant_choice(item, entity_reference)`: keep reusable-save receipts and campaign
/// choices honest about playable state.
pub fn participant_choice(item: &ReusableParticipant, entity_reference: Option<&JsonMap>) -> JsonMap {
    let mut row = item.catalog_payload();
    let missing_location = !item.entity_state.is_empty() && entity_reference.is_none();
    row.insert("campaign_ready".into(), Json::Bool(item.authored_profile.is_some() && !missing_location));
    if missing_location {
        row.insert(
            "unavailable_reason".into(),
            Json::from("This Ruleset has no declared character state location."),
        );
    }
    row
}

/// `participant_catalog_payload(owner, item)`: a saved Character's readiness against its
/// immutable Ruleset, without a Plan.
pub fn participant_catalog_payload<O: LibraryOwner>(
    owner: &O,
    item: &ReusableParticipant,
) -> Result<JsonMap, O::Error> {
    let reference =
        character_state_reference(owner, &item.package_id, &item.package_version, &item.package_sha256)?;
    Ok(participant_choice(item, reference.as_ref()))
}

// ── The selection ────────────────────────────────────────────────────────────────────────────

/// The owner the selection uses.
pub trait SelectionOwner: ReferenceOwner {
    /// `owner.exact_participant_declarations(package_id, version, sha256)`.
    fn exact_participant_declarations(
        &self,
        package_id: &str,
        version: &str,
        sha256: &str,
    ) -> Result<Vec<ParticipantArtifactDeclaration>, Self::Error>;
    /// `owner.export_package_candidate(candidate_id, target)`: the written archive bytes.
    fn export_package_candidate(&self, candidate_id: &str) -> Result<Vec<u8>, Self::Error>;
}

impl SelectionOwner for AuthorityStore {
    fn exact_participant_declarations(
        &self,
        package_id: &str,
        version: &str,
        sha256: &str,
    ) -> Result<Vec<ParticipantArtifactDeclaration>, StoreError> {
        AuthorityStore::exact_participant_declarations(self, package_id, version, sha256)
    }
    fn export_package_candidate(&self, _candidate_id: &str) -> Result<Vec<u8>, StoreError> {
        Err(DomainError::command_rejected_with(
            "Package candidate export is not yet ported (lane 3's export_package_candidate)",
            "rust_port_pending",
            Vec::new(),
        )
        .into())
    }
}

/// `retained_plan_snapshot`'s two owner reads, from a [`SelectionOwner`].
struct SnapshotReads<'a, O>(&'a O);

impl<O: SelectionOwner> PlanSnapshotOwner for SnapshotReads<'_, O> {
    type Error = O::Error;
    fn package_candidates(&self) -> Result<Vec<PackageCandidate>, O::Error> {
        self.0.package_candidates()
    }
    fn export_package_candidate(&self, candidate_id: &str) -> Result<Vec<u8>, O::Error> {
        self.0.export_package_candidate(candidate_id)
    }
}

/// `isinstance(error, CommandRejected)`.
fn is_command_rejected(error: &DomainError) -> bool {
    matches!(
        error.python_class(),
        "CommandRejected"
            | "MechanicsRejected"
            | "PortableRecoveryFailure"
            | "_BackupFailure"
            | "_ExportFailure"
    )
}

/// `isinstance(error, OSError)`.
fn is_os_error(error: &DomainError) -> bool {
    matches!(
        error.python_class(),
        "OSError"
            | "FileNotFoundError"
            | "FileExistsError"
            | "PermissionError"
            | "IsADirectoryError"
            | "NotADirectoryError"
    )
}

/// `isinstance(error, UnicodeError)`.
fn is_unicode_error(error: &DomainError) -> bool {
    matches!(error.python_class(), "UnicodeError" | "UnicodeDecodeError" | "UnicodeEncodeError")
}

/// `except (OSError, UnicodeError, ValueError)`.
fn content_failure(error: &DomainError) -> bool {
    is_os_error(error) || is_unicode_error(error) || error.is_value_error()
}

fn rejected(message: &str) -> DomainError {
    DomainError::command_rejected(message)
}

fn selection_invalid() -> DomainError {
    DomainError::command_rejected_with(
        "The selected campaign content is unavailable or incompatible; refresh its choices",
        "campaign_library_selection_invalid",
        Vec::new(),
    )
}

fn primary_entry(candidate: &PackageCandidate) -> Option<&PackageLockEntry> {
    candidate.package_lock().entries().iter().find(|entry| entry.role() == PackageRole::Primary)
}

/// `CampaignLibrarySelection`: validated sources shared by party discovery and the final campaign
/// handoff.
pub struct CampaignLibrarySelection<'a, O: LibraryOwner> {
    pub candidate: PackageCandidate,
    pub sources: CampaignPlanToolAuthority<'a, O>,
    pub plan: ReusableCampaignPlan,
    pub participants: Vec<ReusableParticipant>,
    pub entity_reference: Option<JsonMap>,
    pub required_participants: Vec<ReusableParticipant>,
    /// An authored party template the Plan's Setting supplies as campaign data.
    pub party_template: Option<Json>,
}

impl<O: LibraryOwner> CampaignLibrarySelection<'_, O> {
    /// `party_options()`: compatible authored files, and the required Setting files that are
    /// unavailable, explained.
    pub fn party_options(&self, bundled: &BundledPackages) -> DomainResult<JsonMap> {
        let starting_party = self.plan.plan.payload["starting_party"].clone();
        if let Some(template) = &self.party_template {
            let mut options = rift_public_template(bundled, &self.participants, Some(template))?;
            options.insert("template_id".into(), Json::from(self.plan.artifact_id.as_str()));
            options.insert("starting_party".into(), starting_party);
            return Ok(options);
        }
        let row_for = |item: &ReusableParticipant| -> JsonMap {
            let mut row = participant_choice(item, self.entity_reference.as_ref());
            row.insert("label".into(), Json::from(item.name.as_str()));
            if item.builtin_starter {
                row.insert(
                    "starter_preview".into(),
                    json!({
                        "authored_profile": item.authored_profile,
                        "participant": item.participant,
                        "entity_state": item.entity_state,
                    }),
                );
            }
            row
        };
        let policy = parse_starting_party_policy(&starting_party)?;
        let mut players: Vec<Json> = Vec::new();
        let mut companions: Vec<Json> = Vec::new();
        for item in &self.participants {
            if item.role == "player_character" && policy.fixed_player_character.is_some() {
                continue;
            }
            match item.role.as_str() {
                "player_character" => players.push(Json::Object(row_for(item))),
                "companion" => companions.push(Json::Object(row_for(item))),
                other => return Err(DomainError::uncaught("KeyError", pystr::repr(other))),
            }
        }
        for (role, reference) in required_character_references(&policy) {
            // `{item.artifact_id: item for item in ...}`: a later file replaces an earlier one.
            let item = self
                .required_participants
                .iter()
                .rev()
                .find(|item| item.artifact_id == reference.character_id);
            let mut row = match item {
                Some(item) => row_for(item),
                None => object(json!({
                    "id": reference.character_id,
                    "label": reference.character_id,
                    "name": reference.character_id,
                    "summary": "The required Setting character has no playable artifact available.",
                    "profile_status": "unavailable",
                    "campaign_ready": false,
                })),
            };
            row.insert("source_ref".into(), json!({"id": reference.character_id, "path": reference.path}));
            row.insert("reusable".into(), Json::Bool(false));
            if role == "player_character" {
                players.push(Json::Object(row));
            } else {
                companions.push(Json::Object(row));
            }
        }
        Ok(object(json!({
            "template_id": self.plan.artifact_id,
            "title": self.plan.title,
            "description": self.plan.summary,
            "starting_party": starting_party,
            "player_archetypes": players,
            "companions": companions,
        })))
    }
}

fn object(value: Json) -> JsonMap {
    match value {
        Json::Object(map) => map,
        _ => unreachable!("an object"),
    }
}

/// `_required_character_references(policy)`: the Plan's exact files and party roles together.
fn required_character_references(
    policy: &StartingPartyPolicy,
) -> Vec<(&'static str, &CampaignPlanCharacterReference)> {
    let mut references: Vec<(&'static str, &CampaignPlanCharacterReference)> =
        policy.required_companions.iter().map(|reference| ("companion", reference)).collect();
    if let Some(fixed) = &policy.fixed_player_character {
        references.push(("player_character", fixed));
    }
    references
}

/// `read_required_plan_characters(sources, policy, primary, declarations, require_complete=...)`:
/// complete Setting files admitted against the selected Ruleset's participant schemas.
pub fn read_required_plan_characters<O: LibraryOwner>(
    sources: &CampaignPlanToolAuthority<'_, O>,
    policy: &StartingPartyPolicy,
    primary: &PackageLockEntry,
    declarations: &[ParticipantArtifactDeclaration],
    require_complete: bool,
) -> Result<Vec<ReusableParticipant>, O::Error>
where
    O::Error: OwnerError,
{
    let references = required_character_references(policy);
    let mut identities: Vec<&str> = Vec::new();
    for (_, reference) in &references {
        if !identities.contains(&reference.character_id.as_str()) {
            identities.push(&reference.character_id);
        }
    }
    if identities.len() != references.len() {
        return Err(DomainError::value("Required Setting character identities must be distinct").into());
    }
    let mut participants = Vec::new();
    for (role, reference) in references {
        let mut problem = "No compatible participant schema is declared".to_owned();
        let mut validation_errors = Vec::new();
        let source = match sources.read_setting(&reference.path) {
            Ok(source) => source,
            Err(error) => {
                let domain = error.as_domain();
                if !(content_failure(&domain) || is_command_rejected(&domain)) {
                    return Err(error);
                }
                if require_complete {
                    return Err(rejected(&format!(
                        "Required {role} {} at {} is unavailable",
                        reference.character_id, reference.path
                    ))
                    .into());
                }
                continue;
            }
        };
        let mut found = false;
        for declaration in declarations {
            match parse_setting_character(
                &source,
                &reference.character_id,
                role,
                primary,
                declaration,
                &sources.setting,
                sources.setting_character_schema.as_ref(),
            ) {
                Ok(participant) => {
                    participants.push(participant);
                    found = true;
                    break;
                }
                Err(error) if error.is_value_error() || is_command_rejected(&error) => {
                    problem = error.message().to_owned();
                    validation_errors = error.diagnostics().to_vec();
                }
                Err(error) => return Err(error.into()),
            }
        }
        if !found && require_complete {
            return Err(DomainError::command_rejected_with(
                format!("Required {role} {} at {}: {problem}", reference.character_id, reference.path),
                "command_rejected",
                validation_errors,
            )
            .into());
        }
    }
    Ok(participants)
}

/// `require_completed_plan_characters(owner, candidate_id, plan)`: usable fixed and required files
/// when Builder explicitly completes the Plan's party.
pub fn require_completed_plan_characters<O: SelectionOwner>(
    owner: &O,
    candidate_id: &str,
    plan: &ValidatedCampaignPlan,
) -> Result<(), O::Error> {
    let candidate = owner.package_candidate(candidate_id)?;
    let bindings = &plan.payload["bindings"];
    let setting_id = py_str(&bindings["campaign_setting"]["id"]);
    let sources = campaign_plan_sources(owner, candidate.package_lock(), Some(&setting_id))?;
    let Some(sources) = sources.filter(|sources| {
        py_eq(&Json::from(sources.closure_sha256.as_str()), &bindings["package_closure_sha256"])
    }) else {
        return Err(rejected("Required Character Files need the Plan's exact package snapshot").into());
    };
    let primary = primary_entry(&candidate).expect("sources need a primary package");
    let policy = parse_starting_party_policy(&plan.payload["starting_party"])?;
    let declarations =
        owner.exact_participant_declarations(primary.package_id(), primary.version(), primary.sha256())?;
    read_required_plan_characters(&sources, &policy, primary, &declarations, true)?;
    Ok(())
}

/// `_plan_party_template(plan, candidate, sources)`: the party template a campaign setup's
/// Setting supplies as campaign data.
fn plan_party_template<O: LibraryOwner>(
    bundled: &BundledPackages,
    plan: &ReusableCampaignPlan,
    candidate: &PackageCandidate,
    sources: &CampaignPlanToolAuthority<'_, O>,
) -> Result<Option<Json>, O::Error>
where
    O::Error: OwnerError,
{
    let Some(setup_id) = &plan.setup_id else {
        return Ok(None);
    };
    if setup_id != RIFT_TEMPLATE_ID {
        return Err(rejected("The selected Plan uses an unsupported campaign setup").into());
    }
    let template_path = format!("campaigns/{setup_id}/party-template.json");
    if sources.setting_paths.contains(&template_path) {
        let invalid = || {
            DomainError::command_rejected_with(
                "The selected Plan's party template is invalid",
                "campaign_library_selection_invalid",
                Vec::new(),
            )
        };
        let text = match sources.read_setting(&template_path) {
            Ok(text) => text,
            Err(error) if content_failure(&error.as_domain()) => return Err(invalid().into()),
            Err(error) => return Err(error),
        };
        let parsed = crate::reusable_plans::loads(&text).and_then(|value| validate_rift_template(&value));
        return match parsed {
            Ok(value) => Ok(Some(value)),
            Err(error) if content_failure(&error) => Err(invalid().into()),
            Err(error) => Err(error.into()),
        };
    }
    // Copies saved before Settings carried the template keep the shipped one; a Plan that retains
    // its own Ruleset snapshot must also contain its template.
    let snapshot =
        candidate.package_lock().entries().iter().any(|entry| {
            entry.dependencies().iter().any(|dependency| dependency.kind() == "ruleset_snapshot")
        });
    if snapshot {
        return Err(rejected("The selected Plan has no contained party template").into());
    }
    Ok(Some(bundled.bundled_rift_template()?))
}

/// `resolve_campaign_plan_selection(owner, storage_root, candidate_id=..., setting_id=...,
/// plan_id=..., required_setup_id=...)`: exact Plan sources and required files, without
/// discovering the character library.
pub fn resolve_campaign_plan_selection<'a, O: SelectionOwner>(
    owner: &'a O,
    bundled: &BundledPackages,
    storage_root: &Path,
    candidate_id: &str,
    setting_id: &str,
    plan_id: &str,
    required_setup_id: Option<&str>,
) -> Result<CampaignLibrarySelection<'a, O>, O::Error> {
    let domain_owner = DomainOwner(owner);
    let validate = |payload: &Json, markdown: &str| {
        validate_retained_campaign_plan(&domain_owner, bundled, payload, markdown, None)
    };
    let retained = bundled.selected_campaign_plan(storage_root, plan_id, Some(&validate))?;
    if let Some(required) = required_setup_id {
        if retained.setup_id.as_deref() != Some(required) {
            return Err(rejected("The selected Plan does not use this campaign setup").into());
        }
    }
    let bindings = Json::Object(retained.plan.payload["bindings"].as_object().cloned().unwrap_or_default());
    let selected_ruleset_id = match owner.package_candidate(candidate_id) {
        Ok(selected) => primary_entry(&selected)
            .map(|entry| entry.package_id().to_owned())
            .ok_or_else(|| DomainError::uncaught("StopIteration", ""))?,
        Err(error) => {
            let domain = error.as_domain();
            if !is_command_rejected(&domain) || domain.code() != Some("candidate_unavailable") {
                return Err(error);
            }
            candidate_id.to_owned()
        }
    };
    require_active_ruleset(&selected_ruleset_id)?;
    require_active_ruleset(&py_str(&bindings["ruleset"]["id"]))?;
    if !py_eq(&Json::from(selected_ruleset_id.as_str()), &bindings["ruleset"]["id"])
        || !py_eq(&Json::from(setting_id), &bindings["campaign_setting"]["id"])
    {
        return Err(rejected("The selected Ruleset or Setting does not match this saved Plan").into());
    }
    // A saved Plan keeps its inherited bytes even when the source library has newer versions.
    let closure = &bindings["package_closure_sha256"];
    let existing = owner
        .package_candidates()?
        .into_iter()
        .find(|item| py_eq(&Json::from(item.closure_sha256()), closure));
    let candidate = match existing {
        Some(candidate) => candidate,
        None => {
            let snapshot = match retained.package_snapshot.as_ref().filter(|snapshot| !snapshot.is_empty()) {
                Some(snapshot) => snapshot.clone(),
                None => {
                    let payload = Json::Object(retained.plan.payload.clone());
                    match retained_plan_snapshot(&SnapshotReads(owner), bundled, &payload) {
                        Ok(snapshot) => snapshot,
                        Err(error) if error.as_domain().is_value_error() => {
                            return Err(rejected("The selected Campaign Plan snapshot is unavailable").into())
                        }
                        Err(error) => return Err(error),
                    }
                }
            };
            let install_id = format!("plan-snapshot-{}", &sha256_hex(&snapshot)[..24]);
            owner.install_package_archive(&snapshot, &install_id)?
        }
    };
    let sources = campaign_plan_sources(owner, candidate.package_lock(), Some(setting_id))?
        .ok_or_else(|| rejected("A saved Campaign Plan requires its installed Ruleset and Setting"))?;
    let read = (|| -> Result<(Vec<ReusableParticipant>, Option<JsonMap>), O::Error> {
        let primary = primary_entry(&candidate).ok_or_else(|| DomainError::uncaught("StopIteration", ""))?;
        let declarations = owner.exact_participant_declarations(
            primary.package_id(),
            primary.version(),
            primary.sha256(),
        )?;
        let policy = parse_starting_party_policy(&retained.plan.payload["starting_party"])?;
        let required = read_required_plan_characters(&sources, &policy, primary, &declarations, false)?;
        let reference =
            character_state_reference(owner, primary.package_id(), primary.version(), primary.sha256())?;
        Ok((required, reference))
    })();
    let (required_participants, entity_reference) = match read {
        Ok(read) => read,
        Err(error) if content_failure(&error.as_domain()) => return Err(selection_invalid().into()),
        Err(error) => return Err(error),
    };
    let party_template = plan_party_template(bundled, &retained, &candidate, &sources)?;
    Ok(CampaignLibrarySelection {
        candidate,
        sources,
        plan: retained,
        participants: Vec::new(),
        entity_reference,
        required_participants,
        party_template,
    })
}

/// `resolve_campaign_library_selection(owner, storage_root, candidate_id=..., setting_id=...,
/// plan_id=...)`: ordinary picker choices revalidated, including compatible saved files and
/// starters.
pub fn resolve_campaign_library_selection<'a, O: SelectionOwner>(
    owner: &'a O,
    bundled: &BundledPackages,
    storage_root: &Path,
    candidate_id: &str,
    setting_id: &str,
    plan_id: &str,
) -> Result<CampaignLibrarySelection<'a, O>, O::Error> {
    let mut selection = resolve_campaign_plan_selection(
        owner,
        bundled,
        storage_root,
        candidate_id,
        setting_id,
        plan_id,
        None,
    )?;
    let primary = primary_entry(&selection.candidate)
        .ok_or_else(|| DomainError::uncaught("StopIteration", ""))?
        .clone();
    let found = (|| -> Result<Vec<ReusableParticipant>, O::Error> {
        let declarations = owner.exact_participant_declarations(
            primary.package_id(),
            primary.version(),
            primary.sha256(),
        )?;
        let mut participants = Vec::new();
        for declaration in &declarations {
            // A party-template setup also accepts files saved against its shipped Setting.
            let legacy = match &selection.party_template {
                Some(_) => Some(bundled.bundled_rift_authority()?.setting.clone()),
                None => None,
            };
            participants.extend(available_reusable_participants(
                storage_root,
                bundled,
                &primary,
                declaration,
                &selection.sources.setting,
                legacy.as_ref(),
                selection.sources.setting_character_schema.as_ref(),
                None,
            )?);
        }
        let conflict = participants.iter().any(|item| {
            selection.required_participants.iter().any(|required| required.artifact_id == item.artifact_id)
        });
        if conflict {
            return Err(DomainError::value("Setting and local Character File identities conflict").into());
        }
        Ok(participants)
    })();
    selection.participants = match found {
        Ok(participants) => participants,
        Err(error) if content_failure(&error.as_domain()) => return Err(selection_invalid().into()),
        Err(error) => return Err(error),
    };
    Ok(selection)
}

/// `plan_with_interview_intent(selection, intent, readable_markdown=...)`: the selected story
/// with the player's accepted campaign instructions.
pub fn plan_with_interview_intent<O: LibraryOwner>(
    selection: &CampaignLibrarySelection<'_, O>,
    intent: &JsonMap,
    readable_markdown: Option<&str>,
) -> DomainResult<ValidatedCampaignPlan> {
    let mut payload = canonical(&Json::Object(selection.plan.plan.payload.clone()));
    let mut lines: Vec<String> = Vec::new();
    for (key, label) in [
        ("boundaries", "Hard Boundaries"),
        ("preferences", "Preferences"),
        ("specific_requests", "Specific Requests"),
    ] {
        let empty = Json::from("");
        let value = intent.get(key).unwrap_or(&empty);
        let Some(value) = value.as_str().filter(|value| value.chars().count() <= 131_072) else {
            return Err(rejected("Campaign instructions must be bounded text"));
        };
        let value = pystr::strip(value);
        if !value.is_empty() {
            lines.push(format!("{label}: {value}"));
            if key == "boundaries" {
                payload["boundaries"]
                    .as_array_mut()
                    .expect("a validated Plan has boundaries")
                    .push(Json::from(format!("Player Boundary: {value}")));
            } else {
                append_instructions(&mut payload, &format!("{NEWLINE}Player {label}: {value}"));
            }
        }
    }
    let empty = Json::Object(JsonMap::new());
    let Json::Object(contributions) = intent.get("contributions").unwrap_or(&empty) else {
        return Err(rejected("Campaign contributions must be an object"));
    };
    let mut steps: Vec<(&String, &Json)> = contributions.iter().collect();
    steps.sort_by(|left, right| left.0.cmp(right.0));
    for (step, answer) in steps {
        let Some(Json::Object(answers)) = answer.as_object().and_then(|answer| answer.get("answers")) else {
            continue;
        };
        let mut pairs: Vec<(&String, &Json)> = answers.iter().collect();
        pairs.sort_by(|left, right| left.0.cmp(right.0));
        let rendered = pairs
            .iter()
            .map(|(key, value)| format!("{key}={}", py_str(value)))
            .collect::<Vec<_>>()
            .join(", ");
        if !rendered.is_empty() {
            let line = format!("Accepted {}: {rendered}", step.replace('_', " "));
            append_instructions(&mut payload, &format!("{NEWLINE}{line}"));
            lines.push(line);
        }
    }
    let mut markdown = readable_markdown.unwrap_or(&selection.plan.plan.readable_markdown).to_owned();
    if !lines.is_empty() {
        markdown.push_str(&format!("{NEWLINE}{NEWLINE}## Accepted Campaign Construction{NEWLINE}{NEWLINE}"));
        let listed: Vec<String> = lines.iter().map(|line| format!("- {line}")).collect();
        markdown.push_str(&listed.join(NEWLINE));
        markdown.push_str(NEWLINE);
    }
    selection.sources.validate(&payload, &markdown)
}

const NEWLINE: &str = "\n";

/// `json.loads(canonical_json_bytes(value))`.
fn canonical(value: &Json) -> Json {
    let text = String::from_utf8(canonical_json_bytes(value)).expect("canonical JSON is UTF-8");
    sylvia_base::pyjson::loads(&text).expect("canonical JSON reads back")
}

/// `payload["gm_guidance"]["instructions"] += text`.
fn append_instructions(payload: &mut Json, text: &str) {
    let instructions = &mut payload["gm_guidance"]["instructions"];
    let current = instructions.as_str().expect("validated instructions are text").to_owned();
    *instructions = Json::from(current + text);
}
