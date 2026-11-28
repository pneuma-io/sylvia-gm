//! `application/ready_campaigns.py`: load the immutable prepared parties and start copies
//! through ordinary campaign authority (`ready_campaign_catalog`, `start_ready_campaign`,
//! `customize_ready_campaign`).
//!
//! The selection runs over the owner with its errors read back as domain errors
//! ([`DomainOwner`]), so `resolve_ready_campaign` can sort them by Python's class as its
//! `except` clauses do. The start then calls the owner's async authority while the
//! prepared-start fence is held.

use crate::builder_references::{DomainOwner, OwnerError, ReferenceOwner};
use crate::bundled_packages::BundledPackages;
use crate::campaign_library_selection::{
    materialize_library_party, resolve_campaign_plan_selection, CampaignLibrarySelection, SelectionOwner,
};
use crate::compatible_character_copy::authored_character_state;
use crate::decision_scope::project_campaign_preparation_scope;
use crate::package_materializer::{reject_reparse_chain, resolve};
use crate::package_runtime::{
    package_model_decision_boundary, PACKAGE_RUNTIME_BYTE_LIMIT, PACKAGE_RUNTIME_PATH,
};
use crate::reusable_participants::{parse_setting_character, save_reusable_participant, ReusableParticipant};
use std::io::Write;
use std::path::Path;
use std::sync::Arc;
use std::time::Instant;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::authority::{
    CampaignState, LifecycleState, PackageLock, PackageLockEntry, PackageRole, Participant, StateValue,
    WriterRole,
};
use sylvia_domain::package_library::{
    canonical_json_bytes, PackageCandidate, ParticipantArtifactDeclaration,
};
use sylvia_domain::pyops::{py_eq, py_item};
use sylvia_domain::session::SessionMessageKind;
use sylvia_domain::{DomainError, DomainResult};
use sylvia_store::campaign_confirmation::ReadyStartWork;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle, StoreResult};

/// `_entries()`'s bound on the packaged inventory.
const INVENTORY_BYTE_LIMIT: usize = 1024 * 1024;

/// The members of one inventory row (`set(row) != {...}`).
const ROW_KEYS: [&str; 4] = ["bindings", "id", "party", "plan_content_sha256"];

fn command_rejected(message: &str, code: &str) -> DomainError {
    DomainError::command_rejected_with(message, code, Vec::new())
}

/// The exceptions `resolve_ready_campaign` catches, by Python's class.
enum Caught {
    /// `except OSError`.
    Os,
    /// `except (UnicodeError, ValueError, KeyError, TypeError, StopIteration)`.
    Content,
    /// Anything else, which passes through.
    Other,
}

fn caught(error: &DomainError) -> Caught {
    match error.python_class() {
        "OSError" | "FileNotFoundError" | "FileExistsError" | "PermissionError" | "IsADirectoryError"
        | "NotADirectoryError" => Caught::Os,
        "ValueError"
        | "JSONDecodeError"
        | "UnicodeError"
        | "UnicodeDecodeError"
        | "UnicodeEncodeError"
        | "StaleWorkspaceFileError"
        | "ContentShapeError"
        | "ParticipantValidationError"
        | "PackageRuntimeError"
        | "OwnerProtocolError"
        | "KeyError"
        | "TypeError"
        | "StopIteration" => Caught::Content,
        _ if error.is_value_error() => Caught::Content,
        _ => Caught::Other,
    }
}

/// `next(...)` over an empty generator.
fn stop_iteration() -> DomainError {
    DomainError::uncaught("StopIteration", "")
}

fn as_object(value: &Json) -> Option<&JsonMap> {
    value.as_object()
}

// ── The inventory ────────────────────────────────────────────────────────────────────────────

/// `_entries()`: the packaged inventory read exclusively, each row with its `content_sha256`
/// (the row and the shared provenance, canonically encoded).
fn entries(bundled: &BundledPackages) -> DomainResult<Vec<JsonMap>> {
    let path = &bundled.sources.ready_campaigns;
    let raw = std::fs::read(path).map_err(|error| crate::workspace_files::os_error(&error, path))?;
    if raw.len() > INVENTORY_BYTE_LIMIT {
        return Err(DomainError::value("Prepared campaign inventory is too large"));
    }
    let payload = pyjson::loads_bytes(&raw).map_err(crate::reusable_plans::json_error)?;
    let compatible = as_object(&payload).is_some_and(|payload| {
        payload.get("format_version").is_some_and(|version| py_eq(version, &Json::from(1)))
            && payload.get("provenance").is_some_and(Json::is_object)
    });
    if !compatible {
        return Err(DomainError::value("Prepared campaign inventory is incompatible"));
    }
    let payload = as_object(&payload).expect("checked");
    let rows = match payload.get("campaigns") {
        Some(Json::Array(rows)) if rows.len() == 4 => rows,
        _ => return Err(DomainError::value("Prepared campaign inventory is incomplete")),
    };
    let valid = |row: &Json| {
        let Some(row) = as_object(row) else { return false };
        let mut keys: Vec<&str> = row.keys().map(String::as_str).collect();
        keys.sort_unstable();
        if keys != ROW_KEYS {
            return false;
        }
        let party = match &row["party"] {
            Json::Array(party) => party,
            _ => return false,
        };
        row["id"].is_string()
            && row["bindings"].is_object()
            && (1..=6).contains(&party.len())
            && party.iter().all(Json::is_object)
    };
    if !rows.iter().all(valid) {
        return Err(DomainError::value("Prepared campaign inventory is invalid"));
    }
    let mut identities: Vec<&str> = rows.iter().map(|row| row["id"].as_str().expect("checked")).collect();
    identities.sort_unstable();
    identities.dedup();
    if identities.len() != rows.len() {
        return Err(DomainError::value("Prepared campaign identities conflict"));
    }
    Ok(rows
        .iter()
        .map(|row| {
            let identity = serde_json::json!({"campaign": row, "provenance": payload["provenance"]});
            let mut entry = as_object(row).expect("checked").clone();
            entry.insert("content_sha256".into(), sha256_hex(canonical_json_bytes(&identity)).into());
            entry
        })
        .collect())
}

/// `_entry(template_id, expected_content_sha256)`: one row, bound to its exact bundled Plan.
fn entry(
    bundled: &BundledPackages,
    template_id: &str,
    expected_content_sha256: Option<&str>,
) -> DomainResult<JsonMap> {
    let row = entries(bundled)?
        .into_iter()
        .find(|row| row["id"] == template_id)
        .ok_or_else(|| DomainError::value("The prepared campaign is unavailable"))?;
    if expected_content_sha256.is_some_and(|expected| row["content_sha256"] != expected) {
        return Err(DomainError::value("The prepared campaign changed; refresh its preview before starting"));
    }
    let bound = bundled.bundled_plan(template_id)?.is_some_and(|plan| {
        py_eq(&Json::from(plan.content_sha256.as_str()), &row["plan_content_sha256"])
            && plan.plan.payload.get("bindings").is_some_and(|bindings| {
                canonical_json_bytes(bindings) == canonical_json_bytes(&row["bindings"])
            })
    });
    if !bound {
        return Err(DomainError::value(
            "The prepared party requires its exact bundled adventure and packages",
        ));
    }
    Ok(row)
}

/// `ready_campaign_catalog()`: public scenario and character previews, without library
/// discovery or provider work.
pub fn ready_campaign_catalog(bundled: &BundledPackages) -> DomainResult<Vec<JsonMap>> {
    let mut result = Vec::new();
    for inventory in entries(bundled)? {
        let row = entry(bundled, inventory["id"].as_str().expect("checked"), None)?;
        let plan = bundled.bundled_plan(row["id"].as_str().expect("checked"))?.expect("bound by entry");
        let mut party = Vec::new();
        for member in row["party"].as_array().expect("checked") {
            let character = member.get("character").filter(|character| !character.is_null());
            let mut preview = JsonMap::new();
            match character {
                None => {
                    // Tavin's preview is deliberately public; the actual file is re-admitted at Start.
                    preview.insert("name".into(), "Tavin Bell".into());
                    preview.insert("role".into(), py_item(member, "role")?.clone());
                    preview.insert("level".into(), 1.into());
                    preview.insert("classes".into(), serde_json::json!([{"id": "fighter", "level": 1}]));
                    preview.insert(
                        "summary".into(),
                        "The adventure's included Fighter, with his authored equipment and history.".into(),
                    );
                }
                Some(character) => {
                    let participant = py_item(character, "participant")?;
                    let entity_state = || py_item(character, "entity_state");
                    preview.insert("name".into(), py_item(participant, "name")?.clone());
                    preview.insert("role".into(), py_item(member, "role")?.clone());
                    preview.insert("level".into(), py_item(participant, "level")?.clone());
                    preview.insert("classes".into(), py_item(participant, "classes")?.clone());
                    preview.insert("summary".into(), py_item(character, "summary")?.clone());
                    preview.insert("profile".into(), py_item(character, "authored_profile")?.clone());
                    preview.insert("hp".into(), py_item(entity_state()?, "max_hp")?.clone());
                    preview.insert("armor_class".into(), py_item(entity_state()?, "armor_class")?.clone());
                }
            }
            party.push(Json::Object(preview));
        }
        let mut item = JsonMap::new();
        item.insert("id".into(), row["id"].clone());
        item.insert("content_sha256".into(), row["content_sha256"].clone());
        item.insert("title".into(), plan.title.clone().into());
        item.insert("summary".into(), plan.summary.clone().into());
        item.insert("party".into(), Json::Array(party));
        result.push(item);
    }
    Ok(result)
}

// ── The owner the selection reads ───────────────────────────────────────────────────────────

impl<O: SelectionOwner> ReferenceOwner for DomainOwner<'_, O> {
    fn install_package_archive(&self, archive: &[u8], install_id: &str) -> DomainResult<PackageCandidate> {
        self.0.install_package_archive(archive, install_id).map_err(|error| error.as_domain())
    }
}

impl<O: SelectionOwner> SelectionOwner for DomainOwner<'_, O> {
    fn exact_participant_declarations(
        &self,
        package_id: &str,
        version: &str,
        sha256: &str,
    ) -> DomainResult<Vec<ParticipantArtifactDeclaration>> {
        self.0.exact_participant_declarations(package_id, version, sha256).map_err(|error| error.as_domain())
    }
    fn export_package_candidate(&self, candidate_id: &str) -> DomainResult<Vec<u8>> {
        self.0.export_package_candidate(candidate_id).map_err(|error| error.as_domain())
    }
}

/// The selection's primary package entry.
fn primary_entry(lock: &PackageLock) -> DomainResult<&PackageLockEntry> {
    lock.entries().iter().find(|entry| entry.role() == PackageRole::Primary).ok_or_else(stop_iteration)
}

/// `next(d for d in owner.exact_participant_declarations(...) if d.kind == "player_character")`.
fn player_declaration<O: SelectionOwner<Error = DomainError>>(
    owner: &O,
    primary: &PackageLockEntry,
) -> DomainResult<ParticipantArtifactDeclaration> {
    owner
        .exact_participant_declarations(primary.package_id(), primary.version(), primary.sha256())?
        .into_iter()
        .find(|declaration| declaration.kind == "player_character")
        .ok_or_else(stop_iteration)
}

/// A `str` argument Python would pass through to a checker that refuses it.
fn text<'a>(value: &'a Json, what: &str) -> DomainResult<&'a str> {
    value.as_str().ok_or_else(|| DomainError::type_error(format!("{what} must be a string")))
}

/// `resolve_ready_campaign(owner, storage_root, template_id, expected_content_sha256)`: only the
/// explicit party, admitted against the exact retained Plan and character declarations.
pub fn resolve_ready_campaign<'a, O: SelectionOwner<Error = DomainError>>(
    owner: &'a O,
    bundled: &BundledPackages,
    storage_root: &Path,
    template_id: &str,
    expected_content_sha256: &str,
) -> DomainResult<(CampaignLibrarySelection<'a, O>, Vec<ReusableParticipant>)> {
    resolve_party(owner, bundled, storage_root, template_id, expected_content_sha256).map_err(|error| {
        match caught(&error) {
            Caught::Os => command_rejected(
                "Prepared campaign content could not be read. Retry resumes the same start.",
                "ready_campaign_unavailable",
            ),
            Caught::Content => command_rejected(
                "The prepared campaign is unavailable or incompatible. Refresh its preview or use custom setup.",
                "ready_campaign_invalid",
            ),
            Caught::Other => error,
        }
    })
}

fn resolve_party<'a, O: SelectionOwner<Error = DomainError>>(
    owner: &'a O,
    bundled: &BundledPackages,
    storage_root: &Path,
    template_id: &str,
    expected_content_sha256: &str,
) -> DomainResult<(CampaignLibrarySelection<'a, O>, Vec<ReusableParticipant>)> {
    let row = entry(bundled, template_id, Some(expected_content_sha256))?;
    let bindings = Json::Object(row["bindings"].as_object().expect("checked").clone());
    let candidate_id =
        text(py_item(py_item(&bindings, "ruleset")?, "id")?, "The Ruleset identity")?.to_owned();
    let setting_id =
        text(py_item(py_item(&bindings, "campaign_setting")?, "id")?, "The Setting identity")?.to_owned();
    let mut selection = resolve_campaign_plan_selection(
        owner,
        bundled,
        storage_root,
        &candidate_id,
        &setting_id,
        template_id,
        None,
    )?;
    let primary = primary_entry(selection.candidate.package_lock())?.clone();
    let declaration = player_declaration(owner, &primary)?;
    // `{item.artifact_id: item for item in selection.required_participants}`.
    let required = |identity: &str| {
        selection.required_participants.iter().rev().find(|item| item.artifact_id == identity).cloned()
    };
    let mut party = Vec::new();
    for member in row["party"].as_array().expect("checked") {
        let fields = member.as_object().expect("checked");
        let character = if let Some(identity) = fields.get("required_character_id") {
            let character = identity
                .as_str()
                .and_then(required)
                .ok_or_else(|| DomainError::uncaught("KeyError", identity.to_string()))?;
            if !py_eq(&Json::from(character.role.as_str()), py_item(member, "role")?) {
                return Err(DomainError::value("Prepared character role is incompatible"));
            }
            character
        } else {
            let file = py_item(member, "character")?;
            let source = canonical_json_bytes(file);
            if !py_eq(&Json::from(sha256_hex(&source)), py_item(member, "content_sha256")?) {
                return Err(DomainError::value("Prepared Character File content hash is invalid"));
            }
            parse_setting_character(
                &String::from_utf8(source).expect("canonical JSON is UTF-8"),
                text(py_item(file, "id")?, "The character identity")?,
                text(py_item(member, "role")?, "The character role")?,
                &primary,
                &declaration,
                &selection.sources.setting,
                selection.sources.setting_character_schema.as_ref(),
            )?
        };
        party.push(character);
    }
    let mut identities: Vec<&str> = party.iter().map(|item| item.artifact_id.as_str()).collect();
    identities.sort_unstable();
    identities.dedup();
    if party.is_empty()
        || party[0].role != "player_character"
        || party[1..].iter().any(|item| item.role != "companion")
        || identities.len() != party.len()
    {
        return Err(DomainError::value("Prepared party identities or roles are invalid"));
    }
    // `replace(selection, participants=...)`: the party files the Plan does not require.
    let required_ids: Vec<String> =
        selection.required_participants.iter().map(|item| item.artifact_id.clone()).collect();
    selection.participants =
        party.iter().filter(|item| !required_ids.contains(&item.artifact_id)).cloned().collect();
    Ok((selection, party))
}

/// `_validate_prepared_state(owner, selection, participants, initial_state)`: package-owned
/// preparation checked without clearing queues or changing starting state.
fn validate_prepared_state<O: SelectionOwner<Error = DomainError>>(
    owner: &O,
    selection: &CampaignLibrarySelection<'_, O>,
    participants: &[Participant],
    initial_state: &[StateValue],
) -> DomainResult<()> {
    let primary = primary_entry(selection.candidate.package_lock())?;
    let runtime = owner.package_text_file(
        primary.package_id(),
        primary.version(),
        primary.sha256(),
        PACKAGE_RUNTIME_PATH,
        PACKAGE_RUNTIME_BYTE_LIMIT as i64,
    )?;
    let declared = package_model_decision_boundary(primary.package_id(), runtime.as_bytes())
        .map_err(crate::campaign_library_selection::runtime_error)?;
    let Some(declared) = declared else { return Ok(()) };
    let preview = CampaignState {
        campaign_id: "ready-preview".into(),
        display_name: selection.plan.title.clone(),
        lifecycle: LifecycleState::Active,
        writer: WriterRole::Builder,
        revision: 0,
        package_lock: selection.candidate.package_lock().clone(),
        participants: participants.to_vec(),
        state: initial_state
            .iter()
            .map(|item| (format!("{}.{}", item.namespace(), item.key()), item.value().clone()))
            .collect(),
    };
    if project_campaign_preparation_scope(&preview, &declared)?.is_some() {
        return Err(command_rejected(
            "The prepared party still requires character preparation",
            "ready_campaign_preparation_pending",
        ));
    }
    Ok(())
}

// ── The Plan copies ─────────────────────────────────────────────────────────────────────────

/// `_check_plan_copy_path(storage_root, target)`: linked ancestors and paths outside the
/// selected storage refused before file IO.
fn check_plan_copy_path(storage_root: &Path, target: &Path) -> DomainResult<()> {
    let checked = (|| -> DomainResult<()> {
        let absolute = |path: &Path| {
            std::path::absolute(path).map_err(|error| crate::workspace_files::os_error(&error, path))
        };
        let (storage_root, target) = (absolute(storage_root)?, absolute(target)?);
        if !target.starts_with(&storage_root) {
            return Err(DomainError::value("Prepared Plan copy leaves application storage"));
        }
        let mut current = target.clone();
        // `while not os.path.lexists(current) and current != storage_root`.
        while std::fs::symlink_metadata(&current).is_err() && current != storage_root {
            match current.parent() {
                Some(parent) => current = parent.to_path_buf(),
                None => break,
            }
        }
        reject_reparse_chain(&storage_root, &current)?;
        if !resolve(&target).starts_with(resolve(&storage_root)) {
            return Err(DomainError::value("Prepared Plan copy leaves application storage"));
        }
        Ok(())
    })();
    checked.map_err(|error| match caught(&error) {
        Caught::Other if !is_command_rejected(&error) => error,
        _ => command_rejected(
            "The campaign is saved, but its Plan copy directory is unavailable or contains links",
            "ready_campaign_copy_invalid",
        ),
    })
}

/// `except CommandRejected`, by Python's class.
fn is_command_rejected(error: &DomainError) -> bool {
    matches!(
        error.python_class(),
        "CommandRejected"
            | "MechanicsRejected"
            | "RulesetPreconditionRejected"
            | "PortableRecoveryFailure"
            | "_BackupFailure"
            | "_ExportFailure"
    )
}

/// `_copy_ready_plan(storage_root, campaign_id, plan)`: owned convenience copies, never
/// overwriting edits or following linked directories.
fn copy_ready_plan(
    storage_root: &Path,
    campaign_id: &str,
    payload: &JsonMap,
    markdown: &str,
) -> DomainResult<()> {
    let root = storage_root.join("campaigns").join(campaign_id);
    let payloads = [
        ("campaign-plan.json", canonical_json_bytes(&Json::Object(payload.clone()))),
        ("campaign-plan.md", markdown.as_bytes().to_vec()),
    ];
    for (name, _) in &payloads {
        check_plan_copy_path(storage_root, &root.join(name))?;
    }
    std::fs::create_dir_all(&root).map_err(|error| crate::workspace_files::os_error(&error, &root))?;
    for (name, data) in &payloads {
        let target = root.join(name);
        check_plan_copy_path(storage_root, &target)?;
        if target.exists() {
            continue;
        }
        // `tempfile.NamedTemporaryFile(dir=root, prefix=".ready-plan-", delete=False)`.
        let temporary = root.join(crate::reusable_plans::temporary_name(".ready-plan-", ""));
        let written = (|| {
            let mut output = std::fs::OpenOptions::new().write(true).create_new(true).open(&temporary)?;
            output.write_all(data)?;
            output.flush()?;
            output.sync_all()
        })();
        if let Err(error) = written {
            // Python's `with` closes the file; `delete=False` leaves it, as here.
            return Err(crate::workspace_files::os_error(&error, &temporary));
        }
        let linked = check_plan_copy_path(storage_root, &target).and_then(|()| {
            match std::fs::hard_link(&temporary, &target) {
                Err(error) if error.kind() != std::io::ErrorKind::AlreadyExists => {
                    Err(crate::workspace_files::os_error(&error, &target))
                }
                _ => Ok(()),
            }
        });
        // `finally: temporary.unlink()`: its failure replaces the link's.
        std::fs::remove_file(&temporary)
            .map_err(|error| crate::workspace_files::os_error(&error, &temporary))?;
        linked?;
    }
    Ok(())
}

// ── Starting a copy ─────────────────────────────────────────────────────────────────────────

/// `start_ready_campaign`'s keyword arguments.
#[derive(Debug, Clone)]
pub struct ReadyStart {
    pub operation_id: String,
    pub template_id: String,
    pub expected_content_sha256: String,
    pub display_name: String,
    pub provider_id: String,
    pub model_id: String,
    pub model_guidance_override: bool,
}

/// The selection's facts the start uses after the store thread returns.
struct Prepared {
    package_lock: PackageLock,
    candidate_id: String,
    participants: Vec<Participant>,
    initial_state: Vec<StateValue>,
    selected_party: Vec<Json>,
    plan_title: String,
    plan_content_sha256: String,
    setting_id: String,
    payload: JsonMap,
    markdown: String,
}

/// `_start_ready_campaign`'s first half, on the store thread: the party resolved and
/// materialized, and its preparation checked.
fn prepare(
    store: &AuthorityStore,
    bundled: &BundledPackages,
    storage_root: &Path,
    request: &ReadyStart,
) -> DomainResult<Prepared> {
    let owner = DomainOwner(store);
    let (selection, party) = resolve_ready_campaign(
        &owner,
        bundled,
        storage_root,
        &request.template_id,
        &request.expected_content_sha256,
    )?;
    let companions: Vec<String> = party[1..].iter().map(|item| item.artifact_id.clone()).collect();
    let (participants, initial_state, selected_party) = materialize_library_party(
        &owner,
        &selection,
        &request.operation_id,
        &party[0].artifact_id,
        &companions,
    )?;
    validate_prepared_state(&owner, &selection, &participants, &initial_state)?;
    Ok(Prepared {
        package_lock: selection.candidate.package_lock().clone(),
        candidate_id: selection.candidate.candidate_id().to_owned(),
        participants,
        initial_state,
        selected_party,
        plan_title: selection.plan.title.clone(),
        plan_content_sha256: selection.plan.content_sha256.clone(),
        setting_id: selection.sources.setting.package_id.clone(),
        payload: selection.plan.plan.payload.clone(),
        markdown: selection.plan.plan.readable_markdown.clone(),
    })
}

/// `hashlib.sha256(value.encode()).hexdigest()[:24]`.
fn suffix(value: &str) -> String {
    sha256_hex(value.as_bytes())[..24].to_owned()
}

/// `asdict(ModelGuidanceDecision)`: the dataclass fields in declaration order.
fn model_guidance_value(decision: &sylvia_domain::model_capability::ModelGuidanceDecision) -> Json {
    let fields = decision.fields();
    serde_json::json!({
        "registry_version": fields.registry_version,
        "model_id": fields.model_id,
        "minimum_tier_id": fields.minimum_tier_id,
        "placement_tier_id": fields.placement_tier_id,
        "placement_evidence_sha256": fields.placement_evidence_sha256,
        "status": fields.status.as_str(),
        "warning_code": fields.warning_code,
        "override_accepted": fields.override_accepted,
    })
}

/// The store's `CommandRejected` (`except CommandRejected`).
fn store_command_rejected(error: &StoreError) -> bool {
    match error {
        StoreError::Coded(coded) => matches!(
            coded.error_type.unwrap_or("CommandRejected"),
            "CommandRejected"
                | "MechanicsRejected"
                | "RulesetPreconditionRejected"
                | "PortableRecoveryFailure"
                | "_BackupFailure"
                | "_ExportFailure"
        ),
        StoreError::Backup(_) => true,
        _ => false,
    }
}

/// `start_ready_campaign(owner, storage_root, ...)`: start or resume one independent prepared
/// copy without resolving or calling a provider. A template that turns out invalid is refused
/// as "not created" only once the owner fences the identity against a lost earlier reply.
pub async fn start_ready_campaign(
    store: &StoreHandle,
    bundled: &Arc<BundledPackages>,
    storage_root: &Path,
    request: &ReadyStart,
) -> StoreResult<JsonMap> {
    let confirmation_id = format!("ready-confirm-{}", suffix(&request.operation_id));
    let mut work = store.ready_campaign_start_work(&confirmation_id).await?;
    let outcome = start_held(store, bundled, storage_root, request, &work).await;
    match outcome {
        Err(error)
            if store_command_rejected(&error)
                && error.rejection().is_some_and(|coded| coded.code == "ready_campaign_invalid") =>
        {
            if !store.reject_uncreated_ready_campaign_held(&mut work, &confirmation_id).await? {
                // An earlier reply may have been lost after creation; never attest absence.
                return Err(error);
            }
            Err(command_rejected(
                "The prepared template is unavailable or changed; no campaign was created. \
                 Review the refreshed choices or use custom setup.",
                "ready_campaign_not_created",
            )
            .into())
        }
        other => other,
    }
}

/// `_start_ready_campaign`: the explicit copy admitted while the prepared-start fence is held.
async fn start_held(
    store: &StoreHandle,
    bundled: &Arc<BundledPackages>,
    storage_root: &Path,
    request: &ReadyStart,
    work: &ReadyStartWork,
) -> StoreResult<JsonMap> {
    use crate::authority::{campaign_creation, construction_handoff, guided, snapshots, turns};
    let started = Instant::now();
    let prepared = {
        let (bundled, storage_root, request) =
            (Arc::clone(bundled), storage_root.to_path_buf(), request.clone());
        store.call(move |store| Ok(prepare(store, &bundled, &storage_root, &request))).await??
    };
    let resolved = Instant::now();
    let operation_suffix = suffix(&request.operation_id);
    let builder_session_id = format!("ready-source-{operation_suffix}");
    let confirmed = campaign_creation::confirm_new_campaign(
        store,
        &campaign_creation::ConfirmRequest {
            operation_id: &format!("ready-confirm-{operation_suffix}"),
            display_name: &request.display_name,
            package_lock: &prepared.package_lock,
            turn_id: &format!("ready-construction-{operation_suffix}"),
            package_candidate_id: Some(&prepared.candidate_id),
            builder_session_id: Some(&builder_session_id),
            builder_provider_id: Some(&request.provider_id),
            builder_model_id: Some(&request.model_id),
            builder_selected_skill: "BUILD CAMPAIGN PLAN",
            participants: &prepared.participants,
            initial_state: &prepared.initial_state,
        },
        Some(work),
    )
    .await?;
    let campaign_id = confirmed.campaign_id.clone();
    let campaign_suffix = suffix(&campaign_id);
    let handoff_id = format!("builder-dm-handoff-{campaign_suffix}");
    let dm_turn_id = format!("turn-dm-{campaign_suffix}");
    let handoff = match construction_handoff::builder_dm_handoff(store, &handoff_id).await {
        Ok(handoff) => handoff,
        Err(error) if store_command_rejected(&error) => {
            let mut content = JsonMap::new();
            content.insert(
                "text".into(),
                format!("Start {} with its prepared party.", prepared.plan_title).into(),
            );
            content.insert("ready_campaign_id".into(), request.template_id.clone().into());
            content.insert("ready_campaign_sha256".into(), request.expected_content_sha256.clone().into());
            store
                .append_agent_message(
                    &builder_session_id,
                    &format!("ready-selection-{operation_suffix}"),
                    SessionMessageKind::User,
                    &content,
                )
                .await?;
            let canonical_input = serde_json::json!({
                "plan_id": request.template_id,
                "plan_content_sha256": prepared.plan_content_sha256,
                "setting_id": prepared.setting_id,
                "ready_campaign_sha256": request.expected_content_sha256,
                "selected_party": prepared.selected_party,
            });
            guided::begin_campaign_construction(
                store,
                &format!("ready-begin-{operation_suffix}"),
                &campaign_id,
                "quick_start",
                confirmed.resulting_revision,
                &canonical_input,
            )
            .await?;
            let guidance = construction_handoff::model_guidance(
                store,
                &campaign_id,
                &request.model_id,
                request.model_guidance_override,
            )
            .await?;
            if !guidance.may_continue() {
                let mut reply = JsonMap::new();
                reply.insert("campaign_id".into(), campaign_id.into());
                reply.insert("local_ready".into(), false.into());
                reply.insert("model_guidance".into(), model_guidance_value(&guidance));
                return Ok(reply);
            }
            let (_, _, handoff) = guided::finalize_campaign_construction(
                store,
                &guided::FinalizeConstruction {
                    campaign_id: &campaign_id,
                    dm_provider_id: &request.provider_id,
                    dm_model_id: &request.model_id,
                    plan_artifact_text: &prepared.markdown,
                    structured_plan: Some(&prepared.payload),
                    model_guidance_override: request.model_guidance_override,
                },
            )
            .await?;
            handoff
        }
        Err(error) => return Err(error),
    };
    if !snapshots::snapshot(store, &campaign_id).await?.turns.iter().any(|(turn, _)| *turn == dm_turn_id) {
        turns::open_turn(store, &campaign_id, &dm_turn_id).await?;
    }
    // The owner retains the accepted Plan in SQLite. Files are independent convenience copies;
    // ordinary reopening and first play use the accepted content even after interrupted file IO.
    // Two small files, written as Python writes them, on this task.
    copy_ready_plan(storage_root, &campaign_id, &prepared.payload, &prepared.markdown)?;
    let mut timing = JsonMap::new();
    timing.insert("resolve_party".into(), Json::from(milliseconds(resolved - started)));
    timing.insert("local_start".into(), Json::from(milliseconds(started.elapsed())));
    let mut reply = JsonMap::new();
    reply.insert("campaign_id".into(), campaign_id.into());
    reply.insert("dm_session_id".into(), handoff.fields().dm_session_id.clone().into());
    reply.insert("dm_turn_id".into(), dm_turn_id.into());
    reply.insert("dm_attempt_id".into(), format!("attempt-campaign-start-{campaign_suffix}").into());
    reply.insert("local_ready".into(), true.into());
    reply.insert("template_id".into(), request.template_id.clone().into());
    reply.insert("timing_ms".into(), Json::Object(timing));
    Ok(reply)
}

/// `(perf_counter() - started) * 1000`.
fn milliseconds(duration: std::time::Duration) -> f64 {
    duration.as_secs_f64() * 1000.0
}

// ── Customizing ─────────────────────────────────────────────────────────────────────────────

/// `customize_ready_campaign(owner, storage_root, ...)`: independent editable party files for
/// the ordinary custom campaign flow.
pub fn customize_ready_campaign(
    store: &AuthorityStore,
    bundled: &BundledPackages,
    storage_root: &Path,
    operation_id: &str,
    template_id: &str,
    expected_content_sha256: &str,
) -> DomainResult<JsonMap> {
    let owner = DomainOwner(store);
    let (selection, party) =
        resolve_ready_campaign(&owner, bundled, storage_root, template_id, expected_content_sha256)?;
    let primary = primary_entry(selection.candidate.package_lock())?.clone();
    let declaration = player_declaration(&owner, &primary)?;
    let mut copies = Vec::new();
    for (index, item) in party.iter().enumerate() {
        let saved = save_reusable_participant(
            storage_root,
            &format!("{operation_id}-{index}"),
            &item.role,
            &item.summary,
            &Json::from(item.authored_profile.clone()),
            &Json::Object(item.participant.clone()),
            &Json::Object(authored_character_state(&item.entity_state, &declaration)?),
            &primary,
            &declaration,
            &selection.sources.setting,
            Some(&Json::Object(item.setting_data.clone())),
            selection.sources.setting_character_schema.as_ref(),
            None,
        )?;
        copies.push(Json::Object(saved.catalog_payload()));
    }
    let mut reply = JsonMap::new();
    reply.insert("plan_id".into(), template_id.into());
    reply.insert("characters".into(), Json::Array(copies));
    Ok(reply)
}
