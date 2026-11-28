//! `builder/_plan_snapshot_materializer.py` and `builder/_plan_draft_check.py`: one Plan-owned
//! copy of its inherited Setting and Ruleset closure (`materialize_plan_snapshot_draft`), and the
//! same draft checked against its actual contained package bytes without installing it or saving
//! a Plan (`check_plan_draft`).

use crate::builder_references::{DomainOwner, OwnerError};
use crate::campaign_library_selection::{read_required_plan_characters, SelectionOwner};
use crate::campaign_plan_sources::CampaignPlanToolAuthority;
use crate::package_library::validate_admitted_package_runtimes;
use crate::package_materialization::{materialize_package_archive, NormalizedPackageDraft, Payloads};
use crate::package_materializer::{read_payloads, reject_reparse_chain, reparse_attribute, resolve};
use crate::package_record_edits::apply_package_record_edits;
use crate::package_snapshots::{retained_package_drafts, select_snapshot_sources};
use crate::plan_snapshot::validate_plan_snapshot;
use crate::plan_spatial_admission::plan_spatial_warnings;
use std::collections::HashSet;
use std::io::Read;
use std::path::Path;
use sylvia_base::pyjson::{loads_hooked, HookPolicy};
use sylvia_base::pystr::{casefold, utf8_decode};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{PackageLockEntry, PackageRole};
use sylvia_domain::campaign_plan::{
    fields_error, parse_starting_party_policy, PackageBinding, ValidatedCampaignPlan,
    CAMPAIGN_PLAN_BYTE_LIMIT,
};
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_library::{campaign_setting_entries, closure_sha256, validate_portable_path};
use sylvia_domain::pyops::{py_eq, py_item};
use sylvia_domain::{DomainError, DomainResult};

const DRAFT_DIRECTORY: &str = "campaign-plan-draft";
const REMOVED_FILES: &str = "removed-files.json";
const REMOVAL_LIMIT: usize = 512 * 1024;
const REMOVAL_COUNT_LIMIT: usize = 512;
/// The edit entries whose mere presence leaves the exported snapshot behind.
const PACKAGE_EDIT_ENTRIES: [&str; 3] = ["package-records", "ruleset-source.json", "setting-source.json"];

/// `MaterializedPlanSnapshotDraft`: a validated Plan and the exact package archive it owns.
#[derive(Debug, Clone)]
pub struct MaterializedPlanSnapshotDraft {
    pub archive: Vec<u8>,
    pub plan: ValidatedCampaignPlan,
    pub closure_sha256: String,
}

fn rejected(message: impl Into<String>) -> DomainError {
    DomainError::command_rejected(message)
}

fn is_command_rejected(error: &DomainError) -> bool {
    error.python_class() == "CommandRejected"
}

fn stop_iteration() -> DomainError {
    DomainError::uncaught("StopIteration", "")
}

/// `path.exists() or path.is_symlink()`.
fn present(path: &Path) -> bool {
    path.exists() || std::fs::symlink_metadata(path).is_ok_and(|metadata| metadata.file_type().is_symlink())
}

/// `_bounded_regular_bytes(workspace, path, label, maximum)`: one fixed regular file after
/// rejecting links anywhere in its workspace chain.
fn bounded_regular_bytes(
    workspace: &Path,
    path: &Path,
    label: &str,
    maximum: usize,
) -> DomainResult<Vec<u8>> {
    reject_reparse_chain(workspace, path)?;
    {
        let invalid = || rejected(format!("{label} is invalid"));
        // `path.lstat()` failing is an `OSError`; a link, reparse point or non-file is the
        // `ValueError` "{label} is unavailable"; both become "{label} is invalid".
        let metadata = std::fs::symlink_metadata(path).map_err(|_| invalid())?;
        if metadata.file_type().is_symlink() || reparse_attribute(&metadata) || !path.is_file() {
            return Err(invalid());
        }
        let mut value = Vec::new();
        std::fs::File::open(path)
            .and_then(|file| file.take(maximum as u64 + 1).read_to_end(&mut value))
            .map_err(|_| invalid())?;
        if value.len() > maximum {
            return Err(invalid());
        }
        Ok(value)
    }
}

/// `json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
/// parse_constant=_reject_constant)`, with every refusal Python's callers catch (`UnicodeError`,
/// `JSONDecodeError`, `ValueError`) as `None`.
///
/// Differences rows 1 to 4: an overflowing number, an integer beyond 64 bits, a lone surrogate or
/// nesting of 128 levels or more is refused here by the declared limits, under the same message.
fn strict_json(raw: &[u8]) -> Option<Json> {
    let text = utf8_decode(raw).ok()?;
    match loads_hooked(&text, HookPolicy { refuse_duplicates: true, refuse_constants: true }) {
        Ok(hooked) if !hooked.non_finite => Some(hooked.value),
        _ => None,
    }
}

/// `_read_plan(workspace)`: the structured artifact and its readable rendering.
fn read_plan(workspace: &Path) -> DomainResult<(Json, String)> {
    let root = workspace.join(DRAFT_DIRECTORY);
    let structured = bounded_regular_bytes(
        workspace,
        &root.join("plan.json"),
        "Campaign Plan structured artifact",
        CAMPAIGN_PLAN_BYTE_LIMIT,
    )?;
    let readable = bounded_regular_bytes(
        workspace,
        &root.join("plan.md"),
        "Campaign Plan readable rendering",
        CAMPAIGN_PLAN_BYTE_LIMIT,
    )?;
    let invalid = || rejected("Campaign Plan draft is invalid");
    let payload = strict_json(&structured).ok_or_else(invalid)?;
    let markdown = utf8_decode(&readable).map_err(|_| invalid())?;
    Ok((payload, markdown))
}

/// `_require_original_bindings(payload, expected)`: Plan JSON cannot select a different
/// inherited source closure.
fn require_original_bindings<O: crate::library_sources::LibraryOwner>(
    payload: &Json,
    expected: &CampaignPlanToolAuthority<'_, O>,
) -> DomainResult<()> {
    let fields = ["ruleset", "campaign_setting", "package_closure_sha256"];
    let bindings = payload.as_object().and_then(|payload| payload.get("bindings")).and_then(Json::as_object);
    let Some(bindings) = bindings.filter(|bindings| {
        bindings.len() == fields.len() && fields.iter().all(|field| bindings.contains_key(*field))
    }) else {
        let empty = JsonMap::new();
        return Err(fields_error(
            bindings.unwrap_or(&empty),
            &fields,
            "Campaign Plan bindings",
            &[],
            Some("$.bindings"),
        ));
    };
    if !py_eq(&bindings["ruleset"], &Json::Object(expected.ruleset.as_payload()))
        || !py_eq(&bindings["campaign_setting"], &Json::Object(expected.setting.as_payload()))
        || !py_eq(&bindings["package_closure_sha256"], &Json::from(expected.closure_sha256.as_str()))
    {
        return Err(rejected("Campaign Plan bindings no longer match its launch sources"));
    }
    Ok(())
}

/// `_optional_payloads(workspace, path)`: no edits for an absent or empty directory.
fn optional_payloads(workspace: &Path, path: &Path) -> DomainResult<Payloads> {
    if !present(path) {
        return Ok(Vec::new());
    }
    reject_reparse_chain(workspace, path)?;
    if path.is_dir() {
        let mut entries = std::fs::read_dir(path)
            .map_err(|_| rejected("Campaign Plan inherited-file edits are unavailable"))?;
        match entries.next() {
            None => return Ok(Vec::new()),
            Some(Err(_)) => return Err(rejected("Campaign Plan inherited-file edits are unavailable")),
            Some(Ok(_)) => {}
        }
    }
    read_payloads(path)
}

/// `_removed_paths(workspace, path)`: the explicit `setting/` and `ruleset/` files to drop.
fn removed_paths(workspace: &Path, path: &Path) -> DomainResult<Vec<String>> {
    if !present(path) {
        return Ok(Vec::new());
    }
    let raw = bounded_regular_bytes(workspace, path, "Campaign Plan removed-files list", REMOVAL_LIMIT)?;
    let invalid = || rejected("Campaign Plan removed-files list is invalid");
    let Some(Json::Array(items)) = strict_json(&raw) else {
        return Err(invalid());
    };
    if items.len() > REMOVAL_COUNT_LIMIT {
        return Err(invalid());
    }
    let mut result: Vec<String> = Vec::new();
    for item in &items {
        let Json::String(item) = item else {
            return Err(invalid());
        };
        result.push(item.clone());
    }
    if result.iter().collect::<HashSet<_>>().len() != result.len() {
        return Err(invalid());
    }
    for item in &result {
        let relative = match item.split_once('/') {
            Some(("setting" | "ruleset", relative)) => relative,
            _ => return Err(rejected("Campaign Plan removed-files paths require a package prefix")),
        };
        validate_portable_path(relative)?;
    }
    if result.iter().map(|item| casefold(item)).collect::<HashSet<_>>().len() != result.len() {
        return Err(rejected("Campaign Plan removed-files paths collide"));
    }
    Ok(result)
}

/// `_apply_plan_file_edits(drafts, setting_changes, ruleset_changes, removals)`: explicit file
/// changes to the selected owned world and rules.
fn apply_plan_file_edits(
    drafts: Vec<NormalizedPackageDraft>,
    setting_changes: &Payloads,
    ruleset_changes: &Payloads,
    removals: &[String],
) -> DomainResult<Vec<NormalizedPackageDraft>> {
    let mut result = Vec::new();
    for draft in drafts {
        let record = Json::Object(draft.record.clone());
        let kind = py_item(&record, "content_kind")?;
        let (changed, prefix) = match kind.as_str() {
            Some("campaign_setting") => (setting_changes, "setting/"),
            Some("ruleset") => (ruleset_changes, "ruleset/"),
            _ => {
                result.push(draft);
                continue;
            }
        };
        let mut payloads = draft.payloads;
        let removed: Vec<&str> =
            removals.iter().filter_map(|path| path.strip_prefix(prefix)).collect::<Vec<_>>();
        if removed.iter().any(|path| !payloads.iter().any(|(existing, _)| existing == path)) {
            return Err(rejected("Campaign Plan cannot remove an unknown inherited file"));
        }
        let changed_folded: HashSet<String> = changed.iter().map(|(path, _)| casefold(path)).collect();
        if removed.iter().any(|path| changed_folded.contains(&casefold(path))) {
            return Err(rejected("Campaign Plan file cannot be replaced and removed"));
        }
        payloads.retain(|(path, _)| !removed.contains(&path.as_str()));
        // `payloads.update(changed)`: a replaced file keeps its place, a new one comes last.
        for (path, value) in changed {
            match payloads.iter_mut().find(|(existing, _)| existing == path) {
                Some(entry) => entry.1 = value.clone(),
                None => payloads.push((path.clone(), value.clone())),
            }
        }
        result.push(NormalizedPackageDraft::new(draft.record, payloads, 4));
    }
    Ok(result)
}

/// `_rebind_plan_to_snapshot(snapshot, payload)`: only the derived bindings replaced, after the
/// raw draft selected its original authority.
fn rebind_plan_to_snapshot(snapshot: &[u8], payload: &Json) -> DomainResult<Json> {
    let invalid = || rejected("Campaign Plan bindings are invalid");
    let mut rebound = payload.as_object().ok_or_else(invalid)?.clone();
    let bindings = rebound.get_mut("bindings").and_then(Json::as_object_mut).ok_or_else(invalid)?;
    let admitted = admit_package_archive(snapshot)?;
    let primary = admitted.manifests.iter().find(|item| item.role == PackageRole::Primary);
    let settings = campaign_setting_entries(&admitted.package_lock);
    let (Some(primary), [setting]) = (primary, settings.as_slice()) else {
        return Err(rejected("Materialized Plan packages are invalid"));
    };
    bindings.insert(
        "ruleset".into(),
        serde_json::json!({"id": primary.package_id, "version": primary.version, "content_sha256": primary.content_sha256}),
    );
    bindings.insert(
        "campaign_setting".into(),
        serde_json::json!({"id": setting.package_id(), "version": setting.version(), "content_sha256": setting.sha256()}),
    );
    bindings.insert("package_closure_sha256".into(), closure_sha256(&admitted.package_lock).into());
    Ok(Json::Object(rebound))
}

/// `materialize_plan_snapshot_draft(owner, workspace, candidate_id=..., expected_authority=...)`:
/// a validated Plan and its exact original or newly owned package snapshot.
pub fn materialize_plan_snapshot_draft<O: SelectionOwner, A: crate::library_sources::LibraryOwner>(
    owner: &O,
    workspace: &Path,
    candidate_id: &str,
    expected_authority: &CampaignPlanToolAuthority<'_, A>,
) -> Result<MaterializedPlanSnapshotDraft, O::Error> {
    let workspace = resolve(workspace);
    let (payload, readable) = read_plan(&workspace)?;
    require_original_bindings(&payload, expected_authority)?;
    let candidate = owner.package_candidate(candidate_id)?;
    if candidate.closure_sha256() != expected_authority.closure_sha256 {
        return Err(rejected("Plan launch closure no longer matches its authority").into());
    }
    let root = workspace.join(DRAFT_DIRECTORY);
    let setting_changes = optional_payloads(&workspace, &root.join("setting-files"))?;
    let ruleset_changes = optional_payloads(&workspace, &root.join("ruleset-files"))?;
    let removals = removed_paths(&workspace, &root.join(REMOVED_FILES))?;
    let has_package_edits = PACKAGE_EDIT_ENTRIES.iter().any(|name| present(&root.join(name)));
    let exported = owner.export_package_candidate(candidate_id)?;
    if setting_changes.is_empty() && ruleset_changes.is_empty() && removals.is_empty() && !has_package_edits {
        let plan = validate_plan_snapshot(&exported, &payload, &readable)?;
        return Ok(MaterializedPlanSnapshotDraft {
            archive: exported,
            plan,
            closure_sha256: expected_authority.closure_sha256.clone(),
        });
    }
    let drafts = retained_package_drafts(owner, candidate_id, true)?;
    let drafts = select_snapshot_sources(owner, &workspace, &root, drafts)?;
    let drafts = apply_plan_file_edits(drafts, &setting_changes, &ruleset_changes, &removals)?;
    let drafts = apply_package_record_edits(&workspace, &root, &drafts)?;
    let materialized = materialize_package_archive(&drafts)?;
    let rebound = rebind_plan_to_snapshot(&materialized.archive, &payload)?;
    let plan = validate_plan_snapshot(&materialized.archive, &rebound, &readable)?;
    Ok(MaterializedPlanSnapshotDraft {
        archive: materialized.archive,
        plan,
        closure_sha256: materialized.closure_sha256,
    })
}

fn binding(entry: &PackageLockEntry) -> PackageBinding {
    PackageBinding {
        package_id: entry.package_id().into(),
        version: entry.version().into(),
        content_sha256: entry.sha256().into(),
    }
}

/// `check_plan_draft(owner, workspace, candidate_id=..., expected_authority=...,
/// require_complete_characters=...)`: publication and party validators against the draft's
/// actual contained package bytes, without installing anything or saving a Plan.
pub fn check_plan_draft<O: SelectionOwner, A: crate::library_sources::LibraryOwner>(
    owner: &O,
    workspace: &Path,
    candidate_id: &str,
    expected_authority: &CampaignPlanToolAuthority<'_, A>,
    require_complete_characters: bool,
) -> Result<JsonMap, O::Error> {
    let materialized = materialize_plan_snapshot_draft(owner, workspace, candidate_id, expected_authority)?;
    let admitted = admit_package_archive(&materialized.archive)?;
    validate_admitted_package_runtimes(&admitted)?;
    // `(setting,) = campaign_setting_entries(admitted.package_lock)`.
    let setting = match campaign_setting_entries(&admitted.package_lock).as_slice() {
        [setting] => *setting,
        [] => return Err(DomainError::value("not enough values to unpack (expected 1, got 0)").into()),
        many => {
            return Err(DomainError::value(format!(
                "too many values to unpack (expected 1, got {})",
                many.len()
            ))
            .into())
        }
    };
    let primary = admitted
        .package_lock
        .entries()
        .iter()
        .find(|entry| entry.role() == PackageRole::Primary)
        .ok_or_else(stop_iteration)?;
    let manifest = admitted
        .manifests
        .iter()
        .find(|item| item.role == PackageRole::Primary)
        .ok_or_else(stop_iteration)?;
    // The archive's Setting reader never reads through an owner; its errors stay domain errors so
    // the refusal's diagnostics survive.
    let sources = CampaignPlanToolAuthority::<DomainOwner<'_, O>>::from_archive(
        binding(primary),
        binding(setting),
        materialized.closure_sha256.clone(),
        &admitted,
    )?;
    let mut ready = true;
    let mut character_errors: Vec<Json> = Vec::new();
    let mut character_message = "Required Character Files pass the exact snapshot requirements.".to_owned();
    let plan_payload = Json::Object(materialized.plan.payload.clone());
    let policy = parse_starting_party_policy(py_item(&plan_payload, "starting_party")?)?;
    if let Err(error) =
        read_required_plan_characters(&sources, &policy, primary, &manifest.participant_artifacts, true)
    {
        if !is_command_rejected(&error.as_domain()) || require_complete_characters {
            return Err(error.into());
        }
        ready = false;
        character_message = error.message().to_owned();
        character_errors =
            error.diagnostics().iter().take(16).map(|item| Json::Object(item.as_payload())).collect();
    }
    let warnings = plan_spatial_warnings(&materialized.plan.payload, &manifest.ui_surfaces)?;
    let mut answer = JsonMap::new();
    answer.insert("published".into(), false.into());
    answer.insert("state_changed".into(), false.into());
    answer.insert("closure_sha256".into(), materialized.closure_sha256.into());
    answer.insert(
        "checked".into(),
        serde_json::json!(["plan", "package", "references", "runtime", "required_characters"]),
    );
    answer.insert("required_characters_ready".into(), ready.into());
    answer.insert("character_message".into(), character_message.into());
    answer.insert("character_validation_errors".into(), Json::Array(character_errors));
    answer.insert("authoring_warnings".into(), Json::Array(warnings.into_iter().map(Json::Object).collect()));
    Ok(answer)
}
