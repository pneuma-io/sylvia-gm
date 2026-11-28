//! `builder/_package_snapshots.py`: copy exact library packages as editable drafts, and
//! deliberately select another inherited source (`retained_package_drafts`, `ruleset_copy`,
//! `select_snapshot_sources`, `rebind_ruleset_snapshot`).

use crate::builder_references::OwnerError;
use crate::campaign_library_selection::SelectionOwner;
use crate::package_materialization::{snapshot_record, NormalizedPackageDraft};
use crate::package_materializer::{bounded_json_shape, reject_reparse_chain};
use std::io::Read;
use std::path::Path;
use sylvia_base::pyjson::{self, HookFailure, HookPolicy};
use sylvia_base::Json;
use sylvia_domain::authority::PackageRole;
use sylvia_domain::error::RulesetDiagnostic;
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_library::{campaign_setting_entries, CandidateStatus, PackageCandidate};
use sylvia_domain::pyops::py_item;
use sylvia_domain::{DomainError, DomainResult};

fn command_rejected(message: &str) -> DomainError {
    DomainError::command_rejected_with(message, "command_rejected", Vec::new())
}

fn stop_iteration() -> DomainError {
    DomainError::uncaught("StopIteration", "")
}

/// A draft's `record["id"]`, which every caller's draft holds as a string.
fn draft_id(draft: &NormalizedPackageDraft) -> DomainResult<String> {
    Ok(sylvia_domain::pyops::py_str(py_item(&Json::Object(draft.record.clone()), "id")?))
}

/// `draft.record["content_kind"] == kind`.
fn is_kind(draft: &NormalizedPackageDraft, kind: &str) -> bool {
    draft.record.get("content_kind").is_some_and(|value| value == kind)
}

/// `_reachable(drafts, roots, exclude=...)`: the declared dependencies reached from `roots`, in
/// the order they are first reached, leaving graph validation to package admission.
fn reachable(
    drafts: &[(String, NormalizedPackageDraft)],
    roots: &[String],
    exclude: &[String],
) -> DomainResult<Vec<(String, NormalizedPackageDraft)>> {
    fn include(
        identity: &str,
        drafts: &[(String, NormalizedPackageDraft)],
        exclude: &[String],
        reached: &mut Vec<(String, NormalizedPackageDraft)>,
    ) -> DomainResult<()> {
        if reached.iter().any(|(id, _)| id == identity) || exclude.iter().any(|id| id == identity) {
            return Ok(());
        }
        let Some((_, draft)) = drafts.iter().find(|(id, _)| id == identity) else {
            return Err(command_rejected("Inherited package dependency is unavailable"));
        };
        reached.push((identity.to_owned(), draft.clone()));
        let record = Json::Object(draft.record.clone());
        let dependencies = py_item(&record, "dependencies")?;
        for dependency in sylvia_domain::pyops::py_iter(dependencies)? {
            let id = py_item(&dependency, "id")?;
            include(&sylvia_domain::pyops::py_str(id), drafts, exclude, reached)?;
        }
        Ok(())
    }
    let mut reached = Vec::new();
    for root in roots {
        include(root, drafts, exclude, &mut reached)?;
    }
    Ok(reached)
}

/// `retained_package_drafts(owner, candidate_id, include_setting=...)`: a retained source copied
/// as drafts, with its entire mechanics dependency closure.
pub fn retained_package_drafts<O: SelectionOwner>(
    owner: &O,
    candidate_id: &str,
    include_setting: bool,
) -> Result<Vec<NormalizedPackageDraft>, O::Error> {
    let candidate = owner.package_candidate(candidate_id)?;
    let lock = candidate.package_lock();
    let settings = campaign_setting_entries(lock);
    let primary =
        lock.entries().iter().find(|item| item.role() == PackageRole::Primary).ok_or_else(stop_iteration)?;
    let exported = owner.export_package_candidate(candidate_id)?;
    let admitted = admit_package_archive(&exported)?;
    if admitted.format_version < 3 {
        return Err(command_rejected("This legacy source needs a current package before editing").into());
    }
    let mut drafts: Vec<(String, NormalizedPackageDraft)> = Vec::new();
    for manifest in &admitted.manifests {
        if !include_setting && settings.iter().any(|item| item.package_id() == manifest.package_id) {
            continue;
        }
        let parsed = pyjson::loads(&manifest.canonical_json).map_err(crate::reusable_plans::json_error)?;
        let Json::Object(parsed) = parsed else {
            return Err(DomainError::value("Package manifest is invalid").into());
        };
        let record = snapshot_record(&parsed, primary, &settings);
        let mut files = Vec::new();
        for item in admitted
            .files
            .iter()
            .filter(|item| admitted.manifests[item.manifest].identity() == manifest.identity())
        {
            files.push((item.file.path().to_owned(), admitted.read_file(item)?));
        }
        let draft = NormalizedPackageDraft::new(record, files, 4);
        match drafts.iter_mut().find(|(id, _)| *id == manifest.package_id) {
            Some(entry) => entry.1 = draft,
            None => drafts.push((manifest.package_id.clone(), draft)),
        }
    }
    let mut roots = vec![primary.package_id().to_owned()];
    if include_setting {
        roots.extend(settings.iter().map(|item| item.package_id().to_owned()));
    }
    Ok(reachable(&drafts, &roots, &[])?.into_iter().map(|(_, draft)| draft).collect())
}

/// `ruleset_copy(owner, candidate_id)`: the mechanics closure, without bringing a source
/// Setting into another world.
pub fn ruleset_copy<O: SelectionOwner>(
    owner: &O,
    candidate_id: &str,
) -> Result<Vec<NormalizedPackageDraft>, O::Error> {
    retained_package_drafts(owner, candidate_id, false)
}

/// The `except` clauses around one source selection's read, by Python's class.
enum Caught {
    /// `except (OSError, UnicodeError, RecursionError)`: "could not be read".
    Unreadable,
    /// `except ValueError`: refused with its own text.
    Value,
    /// Anything else, which passes through.
    Other,
}

fn caught(error: &DomainError) -> Caught {
    match error.python_class() {
        "OSError" | "FileNotFoundError" | "FileExistsError" | "PermissionError" | "IsADirectoryError"
        | "NotADirectoryError" | "UnicodeError" | "UnicodeDecodeError" | "UnicodeEncodeError"
        | "RecursionError" => Caught::Unreadable,
        "ValueError" | "JSONDecodeError" | "ContentShapeError" | "StaleWorkspaceFileError" => Caught::Value,
        _ if error.is_value_error() => Caught::Value,
        _ => Caught::Other,
    }
}

/// The selection file's JSON: `json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
/// parse_constant=_reject_constant)` and `_bounded_json_shape`.
fn selection_json(raw: &[u8]) -> DomainResult<Json> {
    let text = sylvia_base::pystr::utf8_decode(raw).map_err(crate::reusable_plans::unicode_error)?;
    let hooked =
        match pyjson::loads_hooked(&text, HookPolicy { refuse_duplicates: true, refuse_constants: true }) {
            Ok(hooked) => hooked,
            Err(HookFailure::Decode(error)) => {
                return Err(DomainError::Diagnosed {
                    error_type: "JSONDecodeError",
                    message: error.to_string(),
                    diagnostics: Vec::new(),
                });
            }
            Err(HookFailure::Constant(_)) => {
                return Err(DomainError::value("JSON constants must be finite and standard"))
            }
            Err(HookFailure::Duplicate(_)) => {
                return Err(DomainError::value("JSON object members must be unique"))
            }
            Err(HookFailure::Unsupported(error)) => return Err(DomainError::value(error.message)),
        };
    bounded_json_shape(&hooked.value)?;
    Ok(hooked.value)
}

/// `_source_selection(owner, workspace, path, ruleset_id=..., setting_id=...)`: the exact
/// candidate an optional `*-source.json` file selects, still holding the same logical Ruleset
/// (and Setting), or `None` when there is no such file.
fn source_selection<O: SelectionOwner>(
    owner: &O,
    workspace: &Path,
    path: &Path,
    ruleset_id: &str,
    setting_id: Option<&str>,
) -> Result<Option<PackageCandidate>, O::Error> {
    let linked = std::fs::symlink_metadata(path).is_ok_and(|metadata| metadata.file_type().is_symlink());
    if !path.exists() && !linked {
        return Ok(None);
    }
    let selected = (|| -> Result<PackageCandidate, O::Error> {
        reject_reparse_chain(workspace, path)?;
        let mut raw = Vec::new();
        std::fs::File::open(path)
            .and_then(|file| file.take(65537).read_to_end(&mut raw))
            .map_err(|error| crate::workspace_files::os_error(&error, path))?;
        if raw.len() > 65536 {
            return Err(DomainError::value("Source selection is too large").into());
        }
        let value = selection_json(&raw)?;
        let Some(value) = value.as_object().filter(|value| {
            value.len() == 2
                && value.contains_key("candidate_id")
                && value.contains_key("expected_closure_sha256")
        }) else {
            return Err(DomainError::value("Source selection requires an exact candidate and closure").into());
        };
        if value.values().any(|item| item.as_str().is_none_or(str::is_empty)) {
            return Err(DomainError::value("Source selection identities are invalid").into());
        }
        let candidate = owner.package_candidate(value["candidate_id"].as_str().expect("checked"))?;
        if !matches!(candidate.status(), CandidateStatus::Validated | CandidateStatus::Activated) {
            return Err(DomainError::value("Source candidate is unavailable").into());
        }
        if candidate.closure_sha256() != value["expected_closure_sha256"].as_str().expect("checked") {
            let file = path.file_name().map(|name| name.to_string_lossy().into_owned());
            return Err(DomainError::command_rejected_with(
                "Source selection changed",
                "builder_source_selection_invalid",
                vec![RulesetDiagnostic {
                    code: "source_selection_changed".into(),
                    phase: "source_selection".into(),
                    message: "The saved source identity does not match the selected candidate.".into(),
                    file,
                    path: Some("$.expected_closure_sha256".into()),
                    guidance: Some(
                        "Inspect library sources and use the candidate's current closure_sha256, or remove this \
                         optional file to keep the source inherited at launch."
                            .into(),
                    ),
                    ..RulesetDiagnostic::default()
                }],
            )
            .into());
        }
        let lock = candidate.package_lock();
        let primary = lock
            .entries()
            .iter()
            .find(|item| item.role() == PackageRole::Primary)
            .ok_or_else(stop_iteration)?;
        if primary.package_id() != ruleset_id {
            return Err(DomainError::value("Source update must retain the logical Ruleset").into());
        }
        if let Some(setting_id) = setting_id {
            let settings = campaign_setting_entries(lock);
            if settings.len() != 1 || settings[0].package_id() != setting_id {
                return Err(DomainError::value("Source update must retain the logical Setting").into());
            }
        }
        Ok(candidate)
    })();
    match selected {
        Ok(candidate) => Ok(Some(candidate)),
        Err(error) => {
            let domain = error.as_domain();
            match caught(&domain) {
                Caught::Unreadable => {
                    Err(command_rejected("Builder source selection could not be read").into())
                }
                Caught::Value => Err(command_rejected(domain.message()).into()),
                Caught::Other => Err(error),
            }
        }
    }
}

/// `select_snapshot_sources(owner, workspace, draft_root, drafts)`: explicitly selected source
/// copies (`setting-source.json`, then `ruleset-source.json`), applied before the author's
/// retained edits.
pub fn select_snapshot_sources<O: SelectionOwner>(
    owner: &O,
    workspace: &Path,
    draft_root: &Path,
    drafts: Vec<NormalizedPackageDraft>,
) -> Result<Vec<NormalizedPackageDraft>, O::Error> {
    let mut drafts = drafts;
    let primary = drafts.iter().find(|item| is_kind(item, "ruleset")).ok_or_else(stop_iteration)?.clone();
    let ruleset_id = draft_id(&primary)?;
    let setting = drafts.iter().find(|item| is_kind(item, "campaign_setting")).cloned();
    let setting_path = draft_root.join("setting-source.json");
    let present = |path: &Path| {
        path.exists()
            || std::fs::symlink_metadata(path).is_ok_and(|metadata| metadata.file_type().is_symlink())
    };
    if setting.is_none() && present(&setting_path) {
        return Err(command_rejected("This draft has no inherited Setting to update").into());
    }
    if let Some(setting) = &setting {
        let setting_id = draft_id(setting)?;
        if let Some(selected) =
            source_selection(owner, workspace, &setting_path, &ruleset_id, Some(&setting_id))?
        {
            drafts = retained_package_drafts(owner, selected.candidate_id(), true)?;
            // `next(item for item in drafts if ...)`: the selected source holds its Setting.
            drafts.iter().find(|item| is_kind(item, "campaign_setting")).ok_or_else(stop_iteration)?;
        }
    }
    let Some(selected) =
        source_selection(owner, workspace, &draft_root.join("ruleset-source.json"), &ruleset_id, None)?
    else {
        return Ok(drafts);
    };
    let incoming = ruleset_copy(owner, selected.candidate_id())?;
    Ok(rebind_ruleset_snapshot(&drafts, incoming)?)
}

/// `rebind_ruleset_snapshot(drafts, incoming)`: incoming mechanics, retaining every owned
/// Setting and its own dependencies, each Setting pointed at the incoming Ruleset version.
pub fn rebind_ruleset_snapshot(
    drafts: &[NormalizedPackageDraft],
    incoming: Vec<NormalizedPackageDraft>,
) -> DomainResult<Vec<NormalizedPackageDraft>> {
    let primary = drafts.iter().find(|item| is_kind(item, "ruleset")).ok_or_else(stop_iteration)?;
    let new_primary = incoming.iter().find(|item| is_kind(item, "ruleset")).ok_or_else(stop_iteration)?;
    let (primary_id, new_primary_id) = (draft_id(primary)?, draft_id(new_primary)?);
    if primary_id != new_primary_id {
        return Err(command_rejected("Incoming rules must preserve the Ruleset identity"));
    }
    let settings: Vec<&NormalizedPackageDraft> =
        drafts.iter().filter(|item| is_kind(item, "campaign_setting")).collect();
    if settings.is_empty() {
        return Ok(incoming);
    }
    // `{item.record["id"]: item for item in drafts}`: a later draft replaces an earlier one.
    let mut original: Vec<(String, NormalizedPackageDraft)> = Vec::new();
    for item in drafts {
        let id = draft_id(item)?;
        match original.iter_mut().find(|(existing, _)| *existing == id) {
            Some(entry) => entry.1 = item.clone(),
            None => original.push((id, item.clone())),
        }
    }
    let roots: Vec<String> = settings.iter().map(|item| draft_id(item)).collect::<DomainResult<_>>()?;
    let mut world = reachable(&original, &roots, std::slice::from_ref(&primary_id))?;
    for item in &incoming {
        let id = draft_id(item)?;
        if let Some(position) = world.iter().position(|(existing, _)| *existing == id) {
            if world[position].1 != *item {
                return Err(command_rejected("Incoming rules conflict with a Setting-owned dependency"));
            }
            world.remove(position);
        }
    }
    let new_version = py_item(&Json::Object(new_primary.record.clone()), "version")?.clone();
    for setting in settings {
        let id = draft_id(setting)?;
        let mut record = setting.record.clone();
        let dependencies = py_item(&Json::Object(record.clone()), "dependencies")?.clone();
        let mut rebound = Vec::new();
        for dependency in sylvia_domain::pyops::py_iter(&dependencies)? {
            let kind = py_item(&dependency, "kind")?;
            let is_ruleset = kind == "ruleset_snapshot" && py_item(&dependency, "id")? == primary_id.as_str();
            rebound.push(match dependency {
                Json::Object(mut fields) if is_ruleset => {
                    fields.insert("version".into(), new_version.clone());
                    Json::Object(fields)
                }
                other => other,
            });
        }
        record.insert("dependencies".into(), Json::Array(rebound));
        let replaced = NormalizedPackageDraft::new(record, setting.payloads.clone(), setting.format_version);
        // `world[setting.record["id"]] = ...`: an existing key keeps its place.
        match world.iter_mut().find(|(existing, _)| *existing == id) {
            Some(entry) => entry.1 = replaced,
            None => world.push((id, replaced)),
        }
    }
    let mut result = incoming;
    result.extend(world.into_iter().map(|(_, draft)| draft));
    Ok(result)
}
