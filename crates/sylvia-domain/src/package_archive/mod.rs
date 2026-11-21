//! `infrastructure/package_archive.py`: strict bounded portable package archive admission.
//!
//! [`admit_package_archive`] reads one `.sylvia-gm-package` archive, checks its directory,
//! members, manifest, records, closure, file hashes and declaration files, and returns the
//! admitted manifests and files. Lane 3 writes them to the database.
//!
//! The failure classes are Python's:
//! - `CommandRejected` (`archive_limit`, `manifest_invalid`, `package_content_invalid`,
//!   `package_knowledge_invalid`, and any from `validate_json_schema`) passes through;
//! - `ValueError` (with `UnicodeDecodeError` and `ContentShapeError`), `TypeError`,
//!   `RuntimeError` (with `NotImplementedError`) and `BadZipFile` become one `archive_invalid`
//!   rejection, "Package archive admission failed", whose diagnostic carries `str(error)`;
//! - `KeyError`, `AttributeError`, `IndexError`, `zlib.error` and `EOFError` escape as the
//!   owner's internal error ([`DomainError::Uncaught`]).

mod cp437_table;
pub mod zip_bounds;
pub mod zipfile;

use crate::authority::{PackageLock, PackageRole};
use crate::content::validate_content_shape;
use crate::error::{DomainError, DomainResult, RulesetDiagnostic};
use crate::package_library::{
    manifest_from_record, parse_campaign_setting_knowledge_bytes, parse_canonical_json_bytes,
    parse_content_declarations_bytes, parse_gameplay_support_bytes, resolve_exact_closure,
    validate_campaign_setting_knowledge_links, validate_content_declaration_links, validate_portable_path,
    JsonShapeLimits, PackageFile, PackageManifest, CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT,
    CAMPAIGN_SETTING_KNOWLEDGE_PATH, CONTENT_DECLARATION_BYTE_LIMIT, DEFAULT_LABEL,
    ENGINE_FEATURE_PANEL_WORKSPACES, GAMEPLAY_SUPPORT_BYTE_LIMIT, GAMEPLAY_SUPPORT_PATH,
};
use crate::panel_layout::{parse_panel_layout, PANEL_LAYOUT_BYTE_LIMIT, PANEL_LAYOUT_PATH};
use crate::schema_admission::validate_json_schema;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use sylvia_base::{pystr, Json};
use unicode_normalization::UnicodeNormalization;
use zipfile::{ReadError, ZipArchive, ZipError, ZipInfo};

pub const PACKAGE_CHUNK_LIMIT: usize = 64 * 1024;
pub const PACKAGE_ARCHIVE_BYTE_LIMIT: usize = 64 * 1024 * 1024;
pub const PACKAGE_MEMBER_BYTE_LIMIT: u64 = 64 * 1024 * 1024;
pub const PACKAGE_EXPANDED_BYTE_LIMIT: u64 = 128 * 1024 * 1024;
pub const PACKAGE_MEMBER_COUNT_LIMIT: u64 = 4096;
const MANIFEST_LIMIT: usize = 2 * 1024 * 1024;

/// One indexed package file and the archive member that holds it (`ArchiveFile`).
#[derive(Debug, Clone, PartialEq)]
pub struct ArchiveFile {
    /// Index into [`AdmittedPackageArchive::manifests`].
    pub manifest: usize,
    pub file: PackageFile,
    pub member_name: String,
}

/// One validated archive (`AdmittedPackageArchive`); files are read back by [`ArchiveFile`].
#[derive(Debug)]
pub struct AdmittedPackageArchive {
    bytes: Vec<u8>,
    pub format_version: i64,
    pub manifests: Vec<PackageManifest>,
    pub files: Vec<ArchiveFile>,
    pub package_lock: PackageLock,
}

impl AdmittedPackageArchive {
    /// The bytes of one admitted file (`iter_file_chunks`, joined).
    pub fn read_file(&self, file: &ArchiveFile) -> DomainResult<Vec<u8>> {
        let archive = ZipArchive::open(&self.bytes).map_err(zip_fault)?;
        let info =
            archive.infos().iter().rev().find(|info| info.filename == file.member_name).ok_or_else(|| {
                DomainError::uncaught(
                    "KeyError",
                    format!("There is no item named {} in the archive", pystr::repr(&file.member_name)),
                )
            })?;
        let mut bytes = Vec::new();
        archive
            .read_chunks(info, PACKAGE_CHUNK_LIMIT, |chunk| {
                bytes.extend_from_slice(chunk);
                Ok::<(), DomainError>(())
            })
            .map_err(read_fault)?;
        Ok(bytes)
    }
}

/// A failure inside admission, before Python's final handlers sort it.
enum Fault {
    Domain(DomainError),
    Zip(ZipError),
}

impl From<DomainError> for Fault {
    fn from(error: DomainError) -> Self {
        Fault::Domain(error)
    }
}

impl From<ZipError> for Fault {
    fn from(error: ZipError) -> Self {
        Fault::Zip(error)
    }
}

impl From<ReadError<DomainError>> for Fault {
    fn from(error: ReadError<DomainError>) -> Self {
        match error {
            ReadError::Zip(error) => Fault::Zip(error),
            ReadError::Caller(error) => Fault::Domain(error),
        }
    }
}

/// A `zipfile` error after admission, in `DomainError` form. Nothing catches it there: the
/// owner reports it as an internal error, except a `UnicodeDecodeError`, which is a `ValueError`.
fn zip_fault(error: ZipError) -> DomainError {
    match error {
        ZipError::UnicodeDecode(message) => DomainError::value(message),
        other => DomainError::uncaught(other.python_class(), other.message()),
    }
}

fn read_fault(error: ReadError<DomainError>) -> DomainError {
    match error {
        ReadError::Zip(error) => zip_fault(error),
        ReadError::Caller(error) => error,
    }
}

/// `_normalized_name(name)`: a portable, NFC, case-folded member name.
fn normalized_name(name: &str) -> DomainResult<String> {
    validate_portable_path(name)?;
    let normalized: String = name.nfc().collect();
    if normalized != name {
        return Err(DomainError::value("Archive member name is not normalized"));
    }
    Ok(pystr::casefold(&normalized))
}

/// `_read_member(archive, name, maximum=...)`.
fn read_member(archive: &ZipArchive<'_>, info: &ZipInfo, maximum: Option<usize>) -> Result<Vec<u8>, Fault> {
    let mut result = Vec::new();
    archive.read_chunks(info, PACKAGE_CHUNK_LIMIT, |chunk| {
        result.extend_from_slice(chunk);
        if maximum.is_some_and(|maximum| result.len() > maximum) {
            return Err(DomainError::command_rejected_with(
                "Package manifest is too large",
                "manifest_invalid",
                Vec::new(),
            ));
        }
        Ok(())
    })?;
    Ok(result)
}

/// `admit_package_archive(source)`: validate one archive without retaining a caller path.
pub fn admit_package_archive(source: &[u8]) -> DomainResult<AdmittedPackageArchive> {
    if source.len() > PACKAGE_ARCHIVE_BYTE_LIMIT {
        return Err(DomainError::command_rejected_with(
            "Package archive is too large",
            "archive_limit",
            Vec::new(),
        ));
    }
    admit(source).map_err(classify)
}

/// Python's final handlers: `except CommandRejected: raise` (with its subclasses), then
/// `except (OSError, ValueError, TypeError, RuntimeError, BadZipFile, LargeZipFile)`, which wraps
/// the error as `archive_invalid`. Any other `AuthorityError` is a `RuntimeError` and is wrapped.
/// Everything else escapes to the owner's internal error.
fn classify(fault: Fault) -> DomainError {
    match fault {
        Fault::Domain(
            error @ DomainError::Rejected {
                error_type: "CommandRejected" | "MechanicsRejected" | "RulesetPreconditionRejected",
                ..
            },
        ) => error,
        Fault::Domain(error @ DomainError::Uncaught { .. }) => error,
        Fault::Domain(error) => admission_failed(error.message()),
        Fault::Zip(error @ (ZipError::Eof | ZipError::Zlib(_))) => {
            DomainError::uncaught(error.python_class(), error.message())
        }
        Fault::Zip(error) => admission_failed(error.message()),
    }
}

/// The `archive_invalid` rejection Python's final handler raises.
fn admission_failed(message: &str) -> DomainError {
    DomainError::command_rejected_with(
        "Package archive admission failed",
        "archive_invalid",
        vec![RulesetDiagnostic {
            code: "archive_invalid".into(),
            phase: "archive".into(),
            message: message.into(),
            file: Some("manifest.json".into()),
            ..Default::default()
        }],
    )
}

fn value(message: &str) -> Fault {
    Fault::Domain(DomainError::value(message))
}

fn admit(source: &[u8]) -> Result<AdmittedPackageArchive, Fault> {
    zip_bounds::preflight_zip_directory(
        source,
        PACKAGE_MEMBER_COUNT_LIMIT,
        zip_bounds::ZIP_DIRECTORY_BYTE_LIMIT,
    )?;
    let archive = ZipArchive::open(source)?;
    let infos = archive.infos();
    if infos.is_empty() {
        return Err(value("Archive is empty"));
    }
    let mut names: BTreeMap<String, &ZipInfo> = BTreeMap::new();
    let mut expanded: u64 = 0;
    for info in infos {
        zip_bounds::validate_zip_compression(info)?;
        if info.is_dir() {
            return Err(value("Archive directories are not portable members"));
        }
        if !matches!((info.external_attr >> 16) & 0o170000, 0 | 0o100000) {
            return Err(value("Archive members must be regular files"));
        }
        if info.flag_bits & 1 != 0 {
            return Err(value("Encrypted package members are unsupported"));
        }
        expanded = expanded.saturating_add(info.file_size);
        if info.file_size > PACKAGE_MEMBER_BYTE_LIMIT || expanded > PACKAGE_EXPANDED_BYTE_LIMIT {
            return Err(Fault::Domain(DomainError::command_rejected_with(
                "Package contents are too large",
                "archive_limit",
                Vec::new(),
            )));
        }
        let normalized = normalized_name(&info.filename)?;
        if names.contains_key(&normalized) {
            return Err(value("Archive member aliases collide"));
        }
        names.insert(normalized, info);
    }
    let Some(manifest_info) = names.get("manifest.json").copied() else {
        return Err(value("Archive manifest is missing"));
    };
    let manifest_bytes = read_member(&archive, manifest_info, Some(MANIFEST_LIMIT))?;
    let parsed = parse_canonical_json_bytes(&manifest_bytes, DEFAULT_LABEL, JsonShapeLimits::NONE)?;
    let Json::Object(parsed_map) = &parsed else {
        return Err(value("Archive manifest must be an object"));
    };
    let raw_format = parsed_map.get("format").and_then(|f| f.as_i64().filter(|_| f.is_i64() || f.is_u64()));
    let format_ok =
        parsed_map.len() == 2 && parsed_map.contains_key("format") && parsed_map.contains_key("packages");
    let Some(format_version) = raw_format.filter(|f| format_ok && (1..=4).contains(f)) else {
        return Err(value("Archive manifest format is unsupported"));
    };
    if let Err(error) = validate_content_shape("package_archive", &parsed, Some("manifest.json")) {
        return Err(Fault::Domain(DomainError::command_rejected_with(
            "Package manifest fields are invalid",
            "manifest_invalid",
            error.diagnostics,
        )));
    }
    let Json::Array(packages) = &parsed_map["packages"] else {
        return Err(value("Archive packages must be an array"));
    };
    let mut manifests = Vec::with_capacity(packages.len());
    for record in packages {
        manifests.push(manifest_from_record(record, Some(format_version))?);
    }
    for manifest in &manifests {
        for state_schema in &manifest.state_schemas {
            validate_json_schema(&state_schema.schema, "Starting-state schema")?;
        }
        for declaration in &manifest.participant_artifacts {
            validate_json_schema(&declaration.schema, &format!("Participant artifact {}", declaration.kind))?;
        }
    }
    resolve_exact_closure(&manifests)?;
    let mut expected_names: BTreeSet<String> = BTreeSet::from(["manifest.json".to_owned()]);
    let mut files: Vec<ArchiveFile> = Vec::new();
    for (manifest_index, manifest) in manifests.iter().enumerate() {
        let mut manifest_files: BTreeMap<&str, &ZipInfo> = BTreeMap::new();
        for file in &manifest.files {
            let member_name = format!("packages/{}/{}", manifest.package_id, file.path());
            expected_names.insert(normalized_name(&member_name)?);
            let Some(indexed_info) = names.get(&normalized_name(&member_name)?).copied() else {
                return Err(value("Indexed package file is absent"));
            };
            if indexed_info.file_size as i128 != i128::from(file.byte_count()) {
                return Err(value("Indexed package file size does not match"));
            }
            let mut digest = Sha256::new();
            let mut count: i64 = 0;
            archive.read_chunks(indexed_info, PACKAGE_CHUNK_LIMIT, |chunk| {
                count += chunk.len() as i64;
                if count > file.byte_count() {
                    return Err(DomainError::value("Indexed package file is larger than declared"));
                }
                digest.update(chunk);
                Ok(())
            })?;
            if count != file.byte_count() || hex::encode(digest.finalize()) != file.sha256() {
                return Err(value("Indexed package file hash does not match"));
            }
            files.push(ArchiveFile {
                manifest: manifest_index,
                file: file.clone(),
                member_name: indexed_info.filename.clone(),
            });
            manifest_files.insert(file.path(), indexed_info);
        }
        if let Some(path) = &manifest.content_declarations_path {
            let Some(info) = manifest_files.get(path.as_str()).copied() else {
                return Err(value("Package content declarations are unavailable"));
            };
            let bytes = read_member(&archive, info, Some(CONTENT_DECLARATION_BYTE_LIMIT))?;
            let checked = parse_content_declarations_bytes(&bytes)
                .and_then(|declarations| validate_content_declaration_links(manifest, &declarations));
            if let Err(error) = checked {
                if !error.is_value_error() {
                    return Err(Fault::Domain(error));
                }
                return Err(Fault::Domain(DomainError::command_rejected_with(
                    "Package content declarations are invalid",
                    "package_content_invalid",
                    vec![RulesetDiagnostic {
                        code: "package_content_invalid".into(),
                        phase: "archive".into(),
                        message: error.message().into(),
                        file: Some(path.clone()),
                        package_id: Some(manifest.package_id.clone()),
                        guidance: Some(
                            "Edit this package's content declarations. Link retained local artifacts and remove links \
                             to absent files. Save canonical JSON: sorted keys, compact separators, no trailing newline."
                                .into(),
                        ),
                        ..Default::default()
                    }],
                )));
            }
        }
        if let Some(info) = manifest_files.get(CAMPAIGN_SETTING_KNOWLEDGE_PATH).copied() {
            let bytes = read_member(&archive, info, Some(CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT))?;
            let checked = parse_campaign_setting_knowledge_bytes(&bytes)
                .and_then(|knowledge| validate_campaign_setting_knowledge_links(manifest, &knowledge));
            if let Err(error) = checked {
                if !error.is_value_error() {
                    return Err(Fault::Domain(error));
                }
                return Err(Fault::Domain(DomainError::command_rejected_with(
                    "Campaign Setting knowledge is invalid",
                    "package_knowledge_invalid",
                    vec![RulesetDiagnostic {
                        code: "package_knowledge_invalid".into(),
                        phase: "archive".into(),
                        message: error.message().into(),
                        file: Some(CAMPAIGN_SETTING_KNOWLEDGE_PATH.into()),
                        package_id: Some(manifest.package_id.clone()),
                        guidance: Some(
                            "Edit setting-knowledge.json in the setting draft. Save canonical JSON: sorted keys, \
                             compact separators, no trailing newline. Reference only files included in this package."
                                .into(),
                        ),
                        ..Default::default()
                    }],
                )));
            }
        }
        if let Some(info) = manifest_files.get(GAMEPLAY_SUPPORT_PATH).copied() {
            let bytes = read_member(&archive, info, Some(GAMEPLAY_SUPPORT_BYTE_LIMIT))?;
            parse_gameplay_support_bytes(&bytes)?;
        }
        if let Some(info) = manifest_files.get(PANEL_LAYOUT_PATH).copied() {
            let requires = manifest.compatibility.get("requires").and_then(Json::as_array);
            let supports = requires.is_some_and(|items| {
                items.iter().any(|item| item.as_str() == Some(ENGINE_FEATURE_PANEL_WORKSPACES))
            });
            if manifest.role != PackageRole::Primary || !supports {
                return Err(value("Panel layouts require a supporting primary Ruleset"));
            }
            let bytes = read_member(&archive, info, Some(PANEL_LAYOUT_BYTE_LIMIT))?;
            parse_panel_layout(&bytes, &manifest.ui_surfaces)?;
        }
    }
    let actual: BTreeSet<String> = names.keys().cloned().collect();
    if actual != expected_names {
        return Err(value("Archive contains unindexed or extra members"));
    }
    let package_lock = resolve_exact_closure(&manifests)?;
    Ok(AdmittedPackageArchive { bytes: source.to_vec(), format_version, manifests, files, package_lock })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use sylvia_base::pyjson;

    /// The fault Rust raises where Python raises `class(message)`.
    fn fault(class: &str, message: &str) -> Fault {
        let domain = |error| Fault::Domain(error);
        match class {
            "ValueError" => domain(DomainError::value(message)),
            "TypeError" => domain(DomainError::type_error(message)),
            "RuntimeError" => Fault::Zip(ZipError::Runtime(message.into())),
            "NotImplementedError" => Fault::Zip(ZipError::NotImplemented(message.into())),
            "BadZipFile" => Fault::Zip(ZipError::BadZip(message.into())),
            "UnicodeDecodeError" => Fault::Zip(ZipError::UnicodeDecode(message.into())),
            "OwnerConflict" => domain(DomainError::authority("OwnerConflict", message)),
            "StaleRevision" => domain(DomainError::authority("StaleRevision", message)),
            "CommandRejected" => {
                domain(DomainError::command_rejected_with(message, "archive_limit", Vec::new()))
            }
            "RulesetPreconditionRejected" => {
                domain(DomainError::ruleset_precondition_rejected(message, &[]).unwrap())
            }
            "KeyError" => domain(DomainError::uncaught("KeyError", message)),
            "AttributeError" => domain(DomainError::uncaught("AttributeError", message)),
            "IndexError" => domain(DomainError::uncaught("IndexError", message)),
            "error" => Fault::Zip(ZipError::Zlib(message.into())),
            "EOFError" => Fault::Zip(ZipError::Eof),
            other => panic!("no Rust fault for {other}"),
        }
    }

    /// `generate_archive_vectors.py` raises each class from inside Python's admission; the same
    /// fault through `classify` reaches the same handler with the same text.
    #[test]
    fn each_exception_class_reaches_pythons_handler() {
        let vectors =
            pyjson::loads(include_str!("../../tests/vectors/archive_handler_vectors.json")).unwrap();
        let cases = vectors["cases"].as_array().unwrap();
        assert_eq!(cases.len(), 16);
        for case in cases {
            let class = case["class"].as_str().unwrap();
            let error = classify(fault(class, case["message"].as_str().unwrap()));
            let actual = match &error {
                DomainError::Uncaught { error_type, .. } => json!(["uncaught", error_type]),
                _ => json!([
                    error.python_class(),
                    error.code(),
                    error.message(),
                    error.diagnostics().iter().map(|d| Json::Object(d.as_payload())).collect::<Vec<_>>(),
                ]),
            };
            assert_eq!(actual, case["outcome"], "{class}");
        }
    }
}
