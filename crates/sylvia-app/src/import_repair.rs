//! `application/import_repair.py`, its inspection half: whether a failed selection is a safely
//! readable archive Builder can repair, and its bounded diagnostics (`inspect_repair_source` with
//! `_read_selected_source`, `_folded_portable_name`, `_read_safe_zip`, `_diagnostic`,
//! `_plan_identity`, `_json_object` and `_validate_plan_source`).
//!
//! Staging the editable copy and materializing a repaired draft (`stage_repair_copy`,
//! `materialize_repair_draft`) follow with `import_repair_sessions.start_import_repair_chat`.

use crate::bundled_packages::BundledPackages;
use crate::package_library::validate_admitted_package_runtimes;
use crate::plan_bundle::{read_bundle, read_member, zip_error, TemporaryDirectory, PLAN_BUNDLE_BYTE_LIMIT};
use crate::plan_snapshot::PLAN_SNAPSHOT_FILENAME;
use crate::reusable_plans::load_reusable_campaign_plan;
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;
use std::path::Path;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, HookPolicy};
use sylvia_base::{pystr, Json, JsonMap};
use sylvia_domain::error::RulesetDiagnostic;
use sylvia_domain::package_archive::admit_package_archive;
use sylvia_domain::package_archive::zip_bounds::{
    preflight_zip_directory, validate_zip_compression, ZIP_DIRECTORY_BYTE_LIMIT,
};
use sylvia_domain::package_archive::zipfile::ZipArchive;
use sylvia_domain::package_archive::zipfile_write::{ZipMember, ZipWriter};
use sylvia_domain::package_library::validate_portable_path;
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
