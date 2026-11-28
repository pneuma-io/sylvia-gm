//! `application/import_repair.py`: Builder's format repair of a failed import.
//!
//! - Inspection: whether a failed selection is a safely readable archive Builder can repair, and
//!   its bounded diagnostics (`inspect_repair_source` with `_read_selected_source`,
//!   `_folded_portable_name`, `_read_safe_zip`, `_diagnostic`, `_plan_identity`, `_json_object`
//!   and `_validate_plan_source`).
//! - Staging: one editable copy under the chat's `repair-draft` (`stage_repair_copy`).
//! - Materializing: the edited draft read back, bounded, and admitted as packages or as a whole
//!   Plan with its snapshot, without publishing (`materialize_repair_draft` with
//!   `_read_draft_files`, `_package_drafts`, `_materialize_packages` and `_plan_result`).

use crate::bundled_packages::BundledPackages;
use crate::package_library::validate_admitted_package_runtimes;
use crate::package_materialization::{
    materialize_package_archive, MaterializedPackageArchive, NormalizedPackageDraft,
};
use crate::package_materializer::reject_reparse_chain;
use crate::plan_bundle::{read_bundle, read_member, zip_error, TemporaryDirectory, PLAN_BUNDLE_BYTE_LIMIT};
use crate::plan_snapshot::{validate_plan_snapshot, PLAN_SNAPSHOT_FILENAME};
use crate::reusable_plans::{
    load_reusable_campaign_plan, REUSABLE_PLAN_FORMAT_ID, REUSABLE_PLAN_FORMAT_VERSION,
};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;
use std::path::{Path, PathBuf};
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, HookPolicy};
use sylvia_base::{pystr, Json, JsonMap};
use sylvia_domain::authority::PackageRole;
use sylvia_domain::campaign_plan::ValidatedCampaignPlan;
use sylvia_domain::error::RulesetDiagnostic;
use sylvia_domain::interview_contribution::parse_interview_contribution_bytes;
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_archive::zip_bounds::{
    preflight_zip_directory, validate_zip_compression, ZIP_DIRECTORY_BYTE_LIMIT,
};
use sylvia_domain::package_archive::zipfile::ZipArchive;
use sylvia_domain::package_archive::zipfile_write::{ZipMember, ZipWriter};
use sylvia_domain::package_library::{
    campaign_setting_entries, canonical_json_bytes, validate_portable_path,
};
use sylvia_domain::pyops::py_eq;
use sylvia_domain::{DomainError, DomainResult};
use unicode_normalization::UnicodeNormalization;

/// `_SOURCE_BYTE_LIMIT`: the larger of a Plan bundle and 64 MiB.
const SOURCE_BYTE_LIMIT: usize =
    if PLAN_BUNDLE_BYTE_LIMIT > 64 * 1024 * 1024 { PLAN_BUNDLE_BYTE_LIMIT } else { 64 * 1024 * 1024 };
/// `_EXPANDED_BYTE_LIMIT`.
const EXPANDED_BYTE_LIMIT: u64 = 128 * 1024 * 1024;
/// `_MEMBER_BYTE_LIMIT`.
const MEMBER_BYTE_LIMIT: u64 = 64 * 1024 * 1024;
/// `_MEMBER_COUNT_LIMIT`.
const MEMBER_COUNT_LIMIT: usize = 4096;

/// `RepairKind`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RepairKind {
    Package,
    Plan,
}

impl RepairKind {
    pub fn as_str(self) -> &'static str {
        match self {
            RepairKind::Package => "package",
            RepairKind::Plan => "plan",
        }
    }
}

/// `RepairSourceInspection`: safe public facts about one repairable selected source.
#[derive(Debug, Clone, PartialEq)]
pub struct RepairSourceInspection {
    pub kind: RepairKind,
    pub source_sha256: String,
    pub file_name: String,
    pub diagnostics: Vec<RulesetDiagnostic>,
    pub identity: Option<String>,
}

fn os_error(error: &std::io::Error, path: &Path) -> DomainError {
    crate::workspace_files::os_error(error, path)
}

/// `_read_selected_source(source_path)`: one regular file, hard-bounded, and its digest.
fn read_selected_source(path: &Path) -> DomainResult<(String, Vec<u8>)> {
    let is_link = std::fs::symlink_metadata(path).is_ok_and(|metadata| metadata.file_type().is_symlink());
    if is_link || !path.is_file() {
        return Err(DomainError::value("Repair source must be one regular file"));
    }
    let mut data = Vec::new();
    std::fs::File::open(path)
        .and_then(|file| file.take(SOURCE_BYTE_LIMIT as u64 + 1).read_to_end(&mut data))
        .map_err(|error| os_error(&error, path))?;
    if data.len() > SOURCE_BYTE_LIMIT {
        return Err(DomainError::value("Repair source is oversized"));
    }
    if data.is_empty() {
        return Err(DomainError::value("Repair source is empty"));
    }
    Ok((sha256_hex(&data), data))
}

/// `_folded_portable_name(name)`: a portable, NFC name, case-folded for alias checks.
fn folded_portable_name(name: &str) -> DomainResult<String> {
    if validate_portable_path(name).is_err() {
        return Err(DomainError::value("Archive member path is unsafe or non-portable"));
    }
    if name.nfc().collect::<String>() != name {
        return Err(DomainError::value("Archive member name is not normalized"));
    }
    Ok(pystr::casefold(name))
}

/// `_read_safe_zip(data, source_sha256=...)`: bounded regular members read into memory, never
/// extracted. A file that is not a readable ZIP is a `ValueError`; a damaged member keeps the
/// `zipfile` class Python raises while reading it.
fn read_safe_zip(data: &[u8]) -> DomainResult<BTreeMap<String, Vec<u8>>> {
    let unreadable = |error: DomainError| match error.python_class() {
        "OSError" | "BadZipFile" => DomainError::value("Repair source is not a readable ZIP archive"),
        _ => error,
    };
    preflight_zip_directory(data, MEMBER_COUNT_LIMIT as u64, ZIP_DIRECTORY_BYTE_LIMIT).map_err(unreadable)?;
    let archive = ZipArchive::open(data).map_err(|error| unreadable(zip_error(error)))?;
    let infos = archive.infos();
    if infos.is_empty() || infos.len() > MEMBER_COUNT_LIMIT {
        return Err(DomainError::value("Repair archive member count is unsafe"));
    }
    let mut aliases: BTreeSet<String> = BTreeSet::new();
    let mut expanded: u64 = 0;
    for info in infos {
        validate_zip_compression(info)?;
        let folded = folded_portable_name(&info.filename)?;
        let file_type = (info.external_attr >> 16) & 0o170000;
        if info.is_dir()
            || info.flag_bits & 1 != 0
            || !matches!(file_type, 0 | 0o100000)
            || aliases.contains(&folded)
            || info.file_size > MEMBER_BYTE_LIMIT
        {
            return Err(DomainError::value("Repair archive contains an unsafe member"));
        }
        aliases.insert(folded);
        expanded += info.file_size;
        if expanded > EXPANDED_BYTE_LIMIT {
            return Err(DomainError::value("Repair archive expands beyond its safe limit"));
        }
    }
    let mut files = BTreeMap::new();
    for info in infos {
        let value = read_member(&archive, info, usize::try_from(info.file_size).unwrap_or(usize::MAX))?;
        if value.len() as u64 != info.file_size {
            return Err(DomainError::value("Repair archive member size is invalid"));
        }
        files.insert(info.filename.clone(), value);
    }
    Ok(files)
}

/// `_diagnostic(error, file_name=...)`: the error's own diagnostics, else one built from its text.
fn diagnostic(error: &DomainError, file_name: &str) -> Vec<RulesetDiagnostic> {
    let diagnostics = error.diagnostics();
    if !diagnostics.is_empty() {
        return diagnostics.iter().take(16).cloned().collect();
    }
    let message = pystr::strip(error.message());
    let message = if message.is_empty() { "Imported content has invalid format data" } else { message };
    vec![RulesetDiagnostic {
        code: "import_format_invalid".into(),
        phase: "import".into(),
        message: message.chars().take(500).collect(),
        file: Some(file_name.into()),
        guidance: Some("Repair the staged copy, then check it again before publishing.".into()),
        ..RulesetDiagnostic::default()
    }]
}

/// `_json_object(value, label=...)`: unique members, an object.
pub(crate) fn json_object(value: &[u8], label: &str) -> DomainResult<JsonMap> {
    let invalid = || DomainError::value(format!("{label} JSON is invalid"));
    let text = std::str::from_utf8(value).map_err(|_| invalid())?;
    let parsed =
        match pyjson::loads_hooked(text, HookPolicy { refuse_duplicates: true, refuse_constants: false }) {
            Ok(hooked) => hooked.value,
            Err(pyjson::HookFailure::Duplicate(_)) => {
                return Err(DomainError::value("JSON object contains a duplicate field"));
            }
            Err(pyjson::HookFailure::Unsupported(error)) => return Err(DomainError::value(error.message)),
            Err(_) => return Err(invalid()),
        };
    match parsed {
        Json::Object(map) => Ok(map),
        _ => Err(DomainError::value(format!("{label} must be an object"))),
    }
}

/// `_plan_identity(files)`: the record's artifact identity, when it can be read.
fn plan_identity(files: &BTreeMap<String, Vec<u8>>) -> Option<String> {
    let raw = files.get("record.json").filter(|raw| raw.len() <= 64 * 1024)?;
    let value = json_object(raw, "Campaign Plan record").ok()?;
    value.get("artifact_id").and_then(Json::as_str).map(str::to_owned)
}

/// `_zip_info(name)`: a deflated regular member at the fixed timestamp.
pub(crate) fn zip_member(name: &str) -> ZipMember {
    ZipMember {
        filename: name.to_owned(),
        date_time: (1980, 1, 1, 0, 0, 0),
        compress_type: 8,
        create_system: 3,
        external_attr: 0o100644 << 16,
    }
}

/// `_validate_plan_source(files)`: the complete Plan loader, run in a disposable library.
fn validate_plan_source(files: &BTreeMap<String, Vec<u8>>, bundled: &BundledPackages) -> DomainResult<()> {
    let mut writer = ZipWriter::new();
    for (name, value) in files {
        writer.writestr(zip_member(name), value)?;
    }
    let (identity, closed) = read_bundle(&writer.finish())?;
    let admitted = admit_package_archive(&closed[PLAN_SNAPSHOT_FILENAME])?;
    validate_admitted_package_runtimes(&admitted)?;
    let temporary = TemporaryDirectory::new(&std::env::temp_dir(), ".import-repair-inspect-")?;
    let target = temporary.path.join("campaign-plans").join(&identity);
    std::fs::create_dir_all(&target).map_err(|error| os_error(&error, &target))?;
    for (name, value) in &closed {
        let path = target.join(name);
        std::fs::write(&path, value).map_err(|error| os_error(&error, &path))?;
    }
    load_reusable_campaign_plan(&temporary.path, &identity, None, bundled).map(drop)
}

/// The errors `inspect_repair_source` reports as the source's diagnostics:
/// `except (CommandRejected, KeyError, OSError, UnicodeError, ValueError)`.
fn repairable(error: &DomainError) -> bool {
    let class = error.python_class();
    error.is_value_error()
        || matches!(
            class,
            "CommandRejected"
                | "MechanicsRejected"
                | "RulesetPreconditionRejected"
                | "PortableRecoveryFailure"
                | "KeyError"
                | "OSError"
                | "FileNotFoundError"
                | "FileExistsError"
                | "PermissionError"
                | "IsADirectoryError"
                | "NotADirectoryError"
                | "ValueError"
                | "JSONDecodeError"
                | "UnicodeError"
                | "UnicodeDecodeError"
                | "UnicodeEncodeError"
                | "StaleWorkspaceFileError"
                | "ContentShapeError"
        )
}

/// `inspect_repair_source(source_path)`: confirm the archive is safe and return its bounded
/// format diagnostics without staging a copy. A source that is already valid is a `ValueError`.
pub fn inspect_repair_source(
    source_path: &Path,
    bundled: &BundledPackages,
) -> DomainResult<RepairSourceInspection> {
    let (source_sha256, data) = read_selected_source(source_path)?;
    let files = read_safe_zip(&data)?;
    let file_name =
        source_path.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default();
    // `Path(source_path).name.lower().endswith(".sylvia-gm-plan")`.
    let kind = if file_name.to_lowercase().ends_with(".sylvia-gm-plan") {
        RepairKind::Plan
    } else {
        RepairKind::Package
    };
    if kind == RepairKind::Plan {
        if let Some(snapshot) = files.get(PLAN_SNAPSHOT_FILENAME) {
            read_safe_zip(snapshot)?;
        }
    }
    let mut identity = None;
    let checked = match kind {
        RepairKind::Package => admit_package_archive(&data).and_then(|admitted| {
            validate_admitted_package_runtimes(&admitted)?;
            identity = admitted.manifests.first().map(|manifest| manifest.package_id.clone());
            Ok(())
        }),
        RepairKind::Plan => {
            identity = plan_identity(&files);
            validate_plan_source(&files, bundled)
        }
    };
    match checked {
        Err(error) if repairable(&error) => Ok(RepairSourceInspection {
            kind,
            source_sha256,
            diagnostics: diagnostic(&error, &file_name),
            file_name,
            identity,
        }),
        Err(error) => Err(error),
        Ok(()) => {
            Err(DomainError::value("Selected content is already valid and does not need format repair"))
        }
    }
}

// ── Staging the editable copy ───────────────────────────────────────────────────────────────

/// `_DRAFT_NAME`.
pub const DRAFT_NAME: &str = "repair-draft";
/// `_MANIFEST_LIMIT`.
const MANIFEST_LIMIT: usize = 2 * 1024 * 1024;

/// `StagedRepairCopy`: the fixed editable location populated after the player selects Fix.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StagedRepairCopy {
    pub kind: RepairKind,
    pub source_sha256: String,
    pub draft_root: PathBuf,
}

fn is_hex64(text: &str) -> bool {
    text.len() == 64 && text.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

/// The kind a selected file's name declares (`.sylvia-gm-plan`, case-insensitive, else a package).
fn kind_of(source_path: &Path) -> RepairKind {
    let name = source_path.file_name().map(|name| name.to_string_lossy().to_lowercase()).unwrap_or_default();
    if name.ends_with(".sylvia-gm-plan") {
        RepairKind::Plan
    } else {
        RepairKind::Package
    }
}

/// `_write_staged_files(target, files)`.
fn write_staged_files(target: &Path, files: &BTreeMap<String, Vec<u8>>) -> DomainResult<()> {
    for (name, value) in files {
        let destination = name.split('/').fold(target.to_path_buf(), |path, part| path.join(part));
        if let Some(parent) = destination.parent() {
            std::fs::create_dir_all(parent).map_err(|error| os_error(&error, parent))?;
        }
        std::fs::write(&destination, value).map_err(|error| os_error(&error, &destination))?;
    }
    Ok(())
}

/// `stage_repair_copy(source_path, expected_sha256, workspace, expected_kind=...,
/// bound_source_sha256=...)`: one editable copy under `repair-draft`, or its edits kept on a
/// proven idempotent replay.
pub fn stage_repair_copy(
    source_path: &Path,
    expected_sha256: &str,
    workspace: &Path,
    expected_kind: RepairKind,
    bound_source_sha256: Option<&str>,
) -> DomainResult<StagedRepairCopy> {
    if !is_hex64(expected_sha256) {
        return Err(DomainError::value("Repair source hash is invalid"));
    }
    if bound_source_sha256.is_some_and(|bound| bound != expected_sha256) {
        return Err(DomainError::value("Repair workspace bound source differs"));
    }
    let (source_hash, data) = read_selected_source(source_path)?;
    if source_hash != expected_sha256 {
        return Err(DomainError::value("Selected repair source changed"));
    }
    let files = read_safe_zip(&data)?;
    let actual_kind = kind_of(source_path);
    if actual_kind != expected_kind {
        return Err(DomainError::value("Selected repair source kind changed"));
    }
    if actual_kind == RepairKind::Plan {
        if let Some(snapshot) = files.get(PLAN_SNAPSHOT_FILENAME) {
            read_safe_zip(snapshot)?;
        }
    }
    let target = workspace.join(DRAFT_NAME);
    std::fs::create_dir_all(workspace).map_err(|error| os_error(&error, workspace))?;
    reject_reparse_chain(workspace, workspace)?;
    if target.exists()
        || std::fs::symlink_metadata(&target).is_ok_and(|metadata| metadata.file_type().is_symlink())
    {
        reject_reparse_chain(workspace, &target)?;
        if bound_source_sha256.is_none() {
            return Err(DomainError::value("Existing repair workspace has no proven bound source"));
        }
        return Ok(StagedRepairCopy { kind: actual_kind, source_sha256: source_hash, draft_root: target });
    }
    // `tempfile.mkdtemp(prefix=".repair-stage-", dir=workspace)`, removed unless it becomes the draft.
    let temporary = TemporaryDirectory::new(workspace, ".repair-stage-")?;
    match actual_kind {
        RepairKind::Package => write_staged_files(&temporary.path, &files)?,
        RepairKind::Plan => {
            let mut outer = files.clone();
            let snapshot = outer.remove(PLAN_SNAPSHOT_FILENAME);
            write_staged_files(&temporary.path, &outer)?;
            if let Some(snapshot) = snapshot {
                let nested = read_safe_zip(&snapshot)?;
                write_staged_files(&temporary.path.join("snapshot"), &nested)?;
            }
        }
    }
    // `os.replace(temporary, target)`.
    std::fs::rename(&temporary.path, &target).map_err(|error| os_error(&error, &target))?;
    Ok(StagedRepairCopy { kind: actual_kind, source_sha256: source_hash, draft_root: target })
}

// ── Reading and materializing a repaired draft ──────────────────────────────────────────────

/// `_read_draft_files(workspace, root)`: only bounded regular files inside the fixed draft, by
/// their relative path, walked top-down as `os.walk(root, followlinks=False)` walks.
fn read_draft_files(workspace: &Path, root: &Path) -> DomainResult<BTreeMap<String, Vec<u8>>> {
    let linked =
        |path: &Path| std::fs::symlink_metadata(path).is_ok_and(|metadata| metadata.file_type().is_symlink());
    if linked(root) || !root.is_dir() {
        return Err(DomainError::value("Repair draft is missing"));
    }
    reject_reparse_chain(workspace, workspace)?;
    reject_reparse_chain(workspace, root)?;
    let mut files = BTreeMap::new();
    let mut aliases: BTreeSet<String> = BTreeSet::new();
    let (mut total, mut entries) = (0u64, 0usize);
    let mut pending = vec![root.to_path_buf()];
    while let Some(current) = pending.pop() {
        let mut listed: Vec<PathBuf> = std::fs::read_dir(&current)
            .map_err(|error| os_error(&error, &current))?
            .map(|entry| entry.map(|entry| entry.path()).map_err(|error| os_error(&error, &current)))
            .collect::<DomainResult<_>>()?;
        listed.sort();
        // `os.walk` lists directories (a link to one included) apart from everything else.
        let (directories, names): (Vec<PathBuf>, Vec<PathBuf>) =
            listed.into_iter().partition(|path| path.is_dir());
        for directory in &directories {
            entries += 1;
            if entries > MEMBER_COUNT_LIMIT {
                return Err(DomainError::value("Repair draft has too many entries"));
            }
            reject_reparse_chain(workspace, directory)?;
        }
        for path in &names {
            entries += 1;
            if entries > MEMBER_COUNT_LIMIT {
                return Err(DomainError::value("Repair draft has too many entries"));
            }
            reject_reparse_chain(workspace, path)?;
            if linked(path) || !path.is_file() {
                return Err(DomainError::value("Repair draft contains an unsafe file"));
            }
            let relative = path
                .strip_prefix(root)
                .expect("inside the draft")
                .components()
                .map(|part| part.as_os_str().to_string_lossy().into_owned())
                .collect::<Vec<_>>()
                .join("/");
            let folded = folded_portable_name(&relative)?;
            if !aliases.insert(folded) {
                return Err(DomainError::value("Repair draft paths collide"));
            }
            let mut value = Vec::new();
            std::fs::File::open(path)
                .and_then(|file| file.take(MEMBER_BYTE_LIMIT + 1).read_to_end(&mut value))
                .map_err(|error| os_error(&error, path))?;
            if value.len() as u64 > MEMBER_BYTE_LIMIT {
                return Err(DomainError::value("Repair draft file is oversized"));
            }
            total += value.len() as u64;
            if total > EXPANDED_BYTE_LIMIT || files.len() >= MEMBER_COUNT_LIMIT {
                return Err(DomainError::value("Repair draft is oversized"));
            }
            files.insert(relative, value);
        }
        // Top-down, in listing order: the first directory is walked next.
        pending.extend(directories.into_iter().filter(|directory| !linked(directory)).rev());
    }
    Ok(files)
}

/// `dependency.get("id") not in package_ids`: a string's membership; an unhashable identity is
/// Python's `TypeError`.
fn not_in(identity: Option<&Json>, package_ids: &BTreeSet<String>) -> DomainResult<bool> {
    match identity {
        Some(Json::String(text)) => Ok(!package_ids.contains(text)),
        Some(value @ (Json::Array(_) | Json::Object(_))) => Err(DomainError::type_error(format!(
            "unhashable type: '{}'",
            sylvia_domain::pyops::py_type_name(value)
        ))),
        _ => Ok(true),
    }
}

/// `_package_drafts(files)`: the draft's manifest and each package's own files, as normalized
/// drafts whose generated fields are recomputed.
fn package_drafts(files: &BTreeMap<String, Vec<u8>>) -> DomainResult<Vec<NormalizedPackageDraft>> {
    let Some(manifest_bytes) = files.get("manifest.json").filter(|bytes| bytes.len() <= MANIFEST_LIMIT)
    else {
        return Err(DomainError::value("Repair draft manifest is missing or oversized"));
    };
    let manifest = json_object(manifest_bytes, "Package manifest")?;
    let keys: BTreeSet<&str> = manifest.keys().map(String::as_str).collect();
    let expected: BTreeSet<&str> = ["format", "packages"].into();
    if keys != expected {
        let extra: Vec<&str> = keys.difference(&expected).copied().collect();
        let missing: Vec<&str> = expected.difference(&keys).copied().collect();
        let diagnostics = extra
            .iter()
            .chain(&missing)
            .take(16)
            .map(|field| RulesetDiagnostic {
                code: "content_field_invalid".into(),
                phase: "import".into(),
                file: Some("manifest.json".into()),
                path: Some(format!("$.{field}")),
                message: if extra.contains(field) {
                    "Remove this undeclared field"
                } else {
                    "Add this required field"
                }
                .into(),
                ..RulesetDiagnostic::default()
            })
            .collect();
        return Err(DomainError::command_rejected_with(
            "Package manifest fields are invalid",
            "command_rejected",
            diagnostics,
        ));
    }
    // `isinstance(raw_format, int) and not isinstance(raw_format, bool)`.
    let raw_format = match &manifest["format"] {
        Json::Number(number) if number.is_i64() => number.as_i64().expect("an i64"),
        // A larger integer is still an `int`; no archive format reaches it.
        Json::Number(number) if number.is_u64() => i64::MAX,
        _ => return Err(DomainError::value("Package manifest format is invalid")),
    };
    let packages = match &manifest["packages"] {
        Json::Array(packages) if !packages.is_empty() => packages,
        _ => return Err(DomainError::value("Package manifest packages are invalid")),
    };
    let package_ids: BTreeSet<String> =
        packages.iter().filter_map(|item| item.as_object()?.get("id")?.as_str().map(str::to_owned)).collect();
    let mut claimed: BTreeSet<String> = ["manifest.json".to_owned()].into();
    let mut drafts = Vec::new();
    for record_value in packages {
        let Some(record_value) = record_value.as_object() else {
            return Err(DomainError::value("Package record is invalid"));
        };
        let package_id = match record_value.get("id") {
            Some(Json::String(id)) if !id.is_empty() => id.clone(),
            _ => return Err(DomainError::value("Package identity is invalid")),
        };
        let prefix = format!("packages/{package_id}/");
        let payloads: Vec<(String, Vec<u8>)> = files
            .iter()
            .filter_map(|(name, value)| {
                name.strip_prefix(&prefix).map(|rest| (rest.to_owned(), value.clone()))
            })
            .collect();
        claimed.extend(payloads.iter().map(|(name, _)| format!("{prefix}{name}")));
        let mut record = record_value.clone();
        record.shift_remove("files");
        record.shift_remove("content_sha256");
        if let Some(Json::Array(dependencies)) = record.get("dependencies").cloned() {
            let mut kept = Vec::new();
            for dependency in dependencies {
                kept.push(match dependency {
                    Json::Object(fields) => {
                        let drop = !not_in(fields.get("id"), &package_ids)?;
                        Json::Object(
                            fields.into_iter().filter(|(key, _)| key != "content_sha256" || !drop).collect(),
                        )
                    }
                    other => other,
                });
            }
            record.insert("dependencies".into(), Json::Array(kept));
        }
        drafts.push(NormalizedPackageDraft::new(record, payloads, raw_format));
    }
    let names: BTreeSet<String> = files.keys().cloned().collect();
    if names != claimed {
        return Err(DomainError::value("Repair draft contains files outside its package records"));
    }
    Ok(drafts)
}

/// `_materialize_packages(files)`: the draft's packages archived and admitted with their runtimes.
fn materialize_packages(files: &BTreeMap<String, Vec<u8>>) -> DomainResult<MaterializedPackageArchive> {
    let materialized = materialize_package_archive(&package_drafts(files)?)?;
    let admitted = admit_package_archive(&materialized.archive)?;
    validate_admitted_package_runtimes(&admitted)?;
    Ok(materialized)
}

/// `MaterializedRepairDraft`: one pre-admitted repaired artifact, its archive in memory.
#[derive(Debug, Clone, PartialEq)]
pub struct MaterializedRepairDraft {
    pub kind: RepairKind,
    pub archive: Vec<u8>,
    pub closure_sha256: String,
    pub plan: Option<ValidatedCampaignPlan>,
    pub interview_contribution: Option<JsonMap>,
    pub setup_id: Option<String>,
}

/// `_PLAN_FILES`.
const PLAN_FILES: [&str; 4] = ["record.json", "plan.json", "plan.md", "interview-contribution.json"];

/// `_plan_result(files)`: the repaired Plan's snapshot materialized, its bindings recomputed
/// from it, and the whole Plan loaded from a disposable library.
fn plan_result(
    files: &BTreeMap<String, Vec<u8>>,
    bundled: &BundledPackages,
) -> DomainResult<MaterializedRepairDraft> {
    if files.keys().any(|name| !PLAN_FILES.contains(&name.as_str()) && !name.starts_with("snapshot/")) {
        return Err(DomainError::value("Repair Plan contains files outside its fixed draft"));
    }
    if !["record.json", "plan.json", "plan.md"].iter().all(|name| files.contains_key(*name)) {
        return Err(DomainError::value("Repair Plan files are incomplete"));
    }
    let snapshot_files: BTreeMap<String, Vec<u8>> = files
        .iter()
        .filter_map(|(name, value)| {
            name.strip_prefix("snapshot/").map(|rest| (rest.to_owned(), value.clone()))
        })
        .collect();
    if snapshot_files.is_empty() {
        return Err(DomainError::value("Repair Plan snapshot is missing"));
    }
    let packages = materialize_packages(&snapshot_files)?;
    let snapshot = packages.archive.clone();
    let mut payload = json_object(&files["plan.json"], "Campaign Plan")?;
    let Some(Json::Object(bindings)) = payload.get_mut("bindings") else {
        return Err(DomainError::value("Campaign Plan bindings are invalid"));
    };
    if !matches!(bindings.get("ruleset"), Some(Json::Object(_)))
        || !matches!(bindings.get("campaign_setting"), Some(Json::Object(_)))
    {
        return Err(DomainError::value("Campaign Plan package bindings are invalid"));
    }
    let primary = packages
        .manifests
        .iter()
        .find(|manifest| manifest.role == PackageRole::Primary)
        .ok_or_else(|| DomainError::uncaught("StopIteration", ""))?;
    let setting = bindings["campaign_setting"].as_object().expect("checked").clone();
    let settings = campaign_setting_entries(&packages.package_lock);
    let matched: Vec<_> = settings
        .iter()
        .filter(|item| {
            setting.get("id").is_some_and(|id| id == item.package_id())
                && setting.get("version").is_some_and(|version| version == item.version())
        })
        .collect();
    let ruleset = bindings["ruleset"].as_object().expect("checked");
    let (primary_id, primary_version, _) = primary.identity();
    let same = |value: Option<&Json>, text: &str| value.is_some_and(|value| value == text);
    if !same(ruleset.get("id"), primary_id) || !same(ruleset.get("version"), primary_version) {
        return Err(DomainError::value("Campaign Plan Ruleset identity does not match its snapshot"));
    }
    if matched.len() != 1 {
        return Err(DomainError::value("Campaign Plan Setting identity does not match its snapshot"));
    }
    let setting_sha256 = matched[0].sha256().to_owned();
    if let Some(Json::Object(ruleset)) = bindings.get_mut("ruleset") {
        ruleset.insert("content_sha256".into(), primary.content_sha256.clone().into());
    }
    if let Some(Json::Object(setting)) = bindings.get_mut("campaign_setting") {
        setting.insert("content_sha256".into(), setting_sha256.into());
    }
    bindings.insert("package_closure_sha256".into(), packages.closure_sha256.clone().into());
    let markdown = pystr::utf8_decode(&files["plan.md"]).map_err(crate::reusable_plans::unicode_error)?;
    let validated = validate_plan_snapshot(&snapshot, &Json::Object(payload), &markdown)?;
    let contribution = match files.get("interview-contribution.json").filter(|bytes| !bytes.is_empty()) {
        None => None,
        Some(bytes) => Some(parse_interview_contribution_bytes(bytes, "campaign_plan")?),
    };
    let mut record = json_object(&files["record.json"], "Campaign Plan record")?;
    let identity = record.get("artifact_id").and_then(Json::as_str).map(str::to_owned);
    let format_ok = record.get("format_id").is_some_and(|id| id == REUSABLE_PLAN_FORMAT_ID)
        && record
            .get("format_version")
            .is_some_and(|version| py_eq(version, &Json::from(REUSABLE_PLAN_FORMAT_VERSION)));
    let Some(identity) = identity.filter(|identity| format_ok && is_plan_id(identity)) else {
        return Err(DomainError::value("Campaign Plan record identity or format is invalid"));
    };
    if !record.get("package_snapshot").is_some_and(|name| name == PLAN_SNAPSHOT_FILENAME) {
        return Err(DomainError::value("Campaign Plan snapshot filename is invalid"));
    }
    let canonical_plan = canonical_json_bytes(&Json::Object(validated.payload.clone()));
    let canonical_contribution = contribution
        .as_ref()
        .map_or_else(Vec::new, |contribution| canonical_json_bytes(&Json::Object(contribution.as_payload())));
    record.insert("plan_content_sha256".into(), validated.content_sha256.clone().into());
    record.insert("package_snapshot_sha256".into(), sha256_hex(&snapshot).into());
    let mut content = canonical_plan.clone();
    content.extend(markdown.as_bytes());
    content.extend(&canonical_contribution);
    content.extend(&snapshot);
    record.insert("content_sha256".into(), sha256_hex(&content).into());
    let mut closed: Vec<(&str, Vec<u8>)> = vec![
        ("record.json", canonical_json_bytes(&Json::Object(record))),
        ("plan.json", canonical_plan),
        ("plan.md", markdown.into_bytes()),
        (PLAN_SNAPSHOT_FILENAME, snapshot.clone()),
    ];
    if contribution.is_some() {
        closed.push(("interview-contribution.json", canonical_contribution));
    }
    // `tempfile.TemporaryDirectory(prefix=".import-repair-check-")`.
    let temporary = TemporaryDirectory::new(&std::env::temp_dir(), ".import-repair-check-")?;
    let target = temporary.path.join("campaign-plans").join(&identity);
    std::fs::create_dir_all(&target).map_err(|error| os_error(&error, &target))?;
    for (name, value) in &closed {
        let path = target.join(name);
        std::fs::write(&path, value).map_err(|error| os_error(&error, &path))?;
    }
    let loaded = load_reusable_campaign_plan(&temporary.path, &identity, None, bundled)?;
    Ok(MaterializedRepairDraft {
        kind: RepairKind::Plan,
        archive: snapshot,
        closure_sha256: packages.closure_sha256,
        plan: Some(loaded.plan),
        interview_contribution: loaded.interview_contribution,
        setup_id: loaded.setup_id,
    })
}

/// `_PLAN_ID_PATTERN.fullmatch(identity)`.
fn is_plan_id(identity: &str) -> bool {
    let Some(rest) = identity.strip_prefix("campaign-plan-reusable-") else { return false };
    let hex = |text: &str, length: usize| {
        text.len() == length && text.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
    };
    rest.split_once('-').is_some_and(|(first, second)| hex(first, 24) && hex(second, 16))
}

/// `materialize_repair_draft(workspace, expected_kind=...)`: the repaired draft materialized
/// through the ordinary admission and runtime validators, without publishing.
pub fn materialize_repair_draft(
    workspace: &Path,
    expected_kind: RepairKind,
    bundled: &BundledPackages,
) -> DomainResult<MaterializedRepairDraft> {
    let files = read_draft_files(workspace, &workspace.join(DRAFT_NAME))?;
    if expected_kind == RepairKind::Plan {
        return plan_result(&files, bundled);
    }
    let package = materialize_packages(&files)?;
    Ok(MaterializedRepairDraft {
        kind: RepairKind::Package,
        archive: package.archive,
        closure_sha256: package.closure_sha256,
        plan: None,
        interview_contribution: None,
        setup_id: None,
    })
}
