//! `builder/_package_materializer.py`: deterministically materialize one guarded Builder
//! Ruleset draft, export the admitted archive, and name its install.

use crate::package_materialization::{
    materialize_package_archive, MaterializedPackageArchive, NormalizedPackageDraft, Payloads,
};
use crate::path_guardrail::platform;
use crate::ruleset_source::{compile_ruleset_source, COMPACT_RULESET_SOURCE};
use std::collections::HashSet;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{loads_hooked, HookPolicy};
use sylvia_base::pystr::casefold;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::error::{DomainError, DomainResult, RulesetDiagnostic};
use sylvia_domain::package_library::validate_portable_path;
use sylvia_domain::ruleset_tests::{parse_ruleset_tests, RULESET_TESTS_PATH};

/// `RULESET_DRAFT_DIRECTORY`.
pub const RULESET_DRAFT_DIRECTORY: &str = "ruleset-draft";
/// `RULESET_DRAFT_DESCRIPTOR`.
pub const RULESET_DRAFT_DESCRIPTOR: &str = "package.json";
/// `RULESET_DRAFT_FILES_DIRECTORY`.
pub const RULESET_DRAFT_FILES_DIRECTORY: &str = "files";
/// `RULESET_OUTPUT_DIRECTORY`.
pub const RULESET_OUTPUT_DIRECTORY: &str = "output";

const DESCRIPTOR_BYTE_LIMIT: usize = 2 * 1024 * 1024;
const PAYLOAD_BYTE_LIMIT: usize = 32 * 1024 * 1024;
const PAYLOAD_FILE_LIMIT: usize = 256;
const PAYLOAD_ENTRY_LIMIT: usize = 1024;
const PAYLOAD_PATH_DEPTH_LIMIT: usize = 12;
const JSON_DEPTH_LIMIT: usize = 24;
const JSON_NODE_LIMIT: usize = 100_000;
#[cfg(windows)]
const REPARSE_POINT: u32 = 0x400;

const FORMAT_3_DRAFT_KEYS: [&str; 14] = [
    "id",
    "version",
    "role",
    "metadata",
    "provenance",
    "compatibility",
    "minimum_model_capability_tier",
    "dependencies",
    "capabilities",
    "tools",
    "prompt_fragments",
    "ui_surfaces",
    "participant_artifacts",
    "content_declarations",
];

fn rejected(message: &str, code: &str) -> DomainError {
    DomainError::command_rejected_with(message, code, Vec::new())
}

fn is_command_rejected(error: &DomainError) -> bool {
    error.python_class() == "CommandRejected"
}

/// Whether Python would catch the error as `OSError` (or one of its subclasses).
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

fn os_error(error: &std::io::Error, path: &Path) -> DomainError {
    crate::workspace_files::os_error(error, path)
}

/// `Path.resolve()` (non-strict).
fn resolve(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    let absolute = if platform::is_absolute(&text) {
        text.into_owned()
    } else {
        platform::join(&platform::cwd(), &text)
    };
    PathBuf::from(platform::resolve(&absolute))
}

// ── Descriptor ─────────────────────────────────────────────────────────────────────────────

/// `_bounded_json_shape(value)`.
fn bounded_json_shape(value: &Json) -> DomainResult<()> {
    let mut stack = vec![(value, 1usize)];
    let mut nodes = 0usize;
    while let Some((item, depth)) = stack.pop() {
        if depth > JSON_DEPTH_LIMIT {
            return Err(DomainError::value("Builder package descriptor is too deeply nested"));
        }
        nodes += 1;
        if nodes > JSON_NODE_LIMIT {
            return Err(DomainError::value("Builder package descriptor has too many values"));
        }
        match item {
            Json::Object(map) => stack.extend(map.values().map(|child| (child, depth + 1))),
            Json::Array(items) => stack.extend(items.iter().map(|child| (child, depth + 1))),
            _ => {}
        }
    }
    Ok(())
}

/// The body of `_read_descriptor`: any failure is the descriptor's one rejection.
fn descriptor_value(path: &Path) -> Result<JsonMap, ()> {
    let size = std::fs::metadata(path).map_err(drop)?.len();
    if size > DESCRIPTOR_BYTE_LIMIT as u64 {
        return Err(());
    }
    let mut raw = Vec::new();
    std::fs::File::open(path)
        .and_then(|file| file.take(DESCRIPTOR_BYTE_LIMIT as u64 + 1).read_to_end(&mut raw))
        .map_err(drop)?;
    if raw.len() > DESCRIPTOR_BYTE_LIMIT {
        return Err(());
    }
    let text = std::str::from_utf8(&raw).map_err(drop)?;
    // Duplicates and NaN/Infinity raise `ValueError`; an overflowing number is `inf`, which
    // `thaw_json_value` refuses with `TypeError`; nesting of 128 levels or more passes
    // Python's reader but not `_bounded_json_shape` (24 levels).
    let hooked =
        loads_hooked(text, HookPolicy { refuse_duplicates: true, refuse_constants: true }).map_err(drop)?;
    bounded_json_shape(&hooked.value).map_err(drop)?;
    let Json::Object(value) = hooked.value else { return Err(()) };
    let base = value.len() == FORMAT_3_DRAFT_KEYS.len()
        && FORMAT_3_DRAFT_KEYS.iter().all(|key| value.contains_key(*key));
    let with_state = value.len() == FORMAT_3_DRAFT_KEYS.len() + 1
        && value.contains_key("state_schemas")
        && FORMAT_3_DRAFT_KEYS.iter().all(|key| value.contains_key(*key));
    if !(base || with_state) || value.get("role") != Some(&Json::from("primary")) || hooked.non_finite {
        return Err(());
    }
    Ok(value)
}

/// `_read_descriptor(path)`.
fn read_descriptor(path: &Path) -> DomainResult<JsonMap> {
    descriptor_value(path)
        .map_err(|()| rejected("Builder ruleset descriptor is invalid", "builder_descriptor_invalid"))
}

// ── Links ──────────────────────────────────────────────────────────────────────────────────

/// `_is_reparse_point(path)`: a symbolic link, or a Windows reparse point such as a junction.
fn is_reparse_point(path: &Path) -> DomainResult<bool> {
    let metadata = std::fs::symlink_metadata(path)
        .map_err(|_| rejected("Builder ruleset payload is unavailable", "builder_payload_invalid"))?;
    Ok(metadata.file_type().is_symlink() || reparse_attribute(&metadata))
}

#[cfg(windows)]
fn reparse_attribute(metadata: &std::fs::Metadata) -> bool {
    use std::os::windows::fs::MetadataExt;
    metadata.file_attributes() & REPARSE_POINT != 0
}

#[cfg(not(windows))]
fn reparse_attribute(_metadata: &std::fs::Metadata) -> bool {
    false
}

/// `_reject_reparse_chain(root, path)`: no link on the way from `path` up to `root`.
fn reject_reparse_chain(root: &Path, path: &Path) -> DomainResult<()> {
    let mut current = path.to_path_buf();
    loop {
        if is_reparse_point(&current)? {
            return Err(rejected("Builder ruleset payload cannot contain links", "builder_payload_invalid"));
        }
        if current == root {
            return Ok(());
        }
        if !current.starts_with(root) || current.as_os_str().len() <= root.as_os_str().len() {
            return Err(rejected(
                "Builder ruleset payload escaped its draft directory",
                "builder_payload_invalid",
            ));
        }
        let Some(parent) = current.parent() else {
            return Err(rejected(
                "Builder ruleset payload escaped its draft directory",
                "builder_payload_invalid",
            ));
        };
        current = parent.to_path_buf();
    }
}

// ── Payloads ───────────────────────────────────────────────────────────────────────────────

/// `files_root.rglob("*")`: every entry below the root, not following directory links.
fn every_entry(root: &Path, limit: usize) -> Result<Vec<PathBuf>, ()> {
    let mut entries = Vec::new();
    let mut directories = vec![root.to_path_buf()];
    while let Some(directory) = directories.pop() {
        for entry in std::fs::read_dir(&directory).map_err(drop)? {
            let entry = entry.map_err(drop)?;
            if entries.len() >= limit {
                return Err(());
            }
            let path = entry.path();
            if entry.file_type().map_err(drop)?.is_dir() {
                directories.push(path.clone());
            }
            entries.push(path);
        }
    }
    Ok(entries)
}

/// The body of `_read_payloads`: `Err(None)` for the payload's one rejection, `Err(Some)` for
/// a `CommandRejected` raised on the way.
fn payloads_from(files_root: &Path) -> Result<Payloads, Option<DomainError>> {
    if !files_root.is_dir() {
        return Err(None);
    }
    reject_reparse_chain(files_root, files_root).map_err(Some)?;
    let mut entries = every_entry(files_root, PAYLOAD_ENTRY_LIMIT).map_err(|()| None)?;
    let posix = |path: &Path| path.to_string_lossy().replace('\\', "/");
    entries.sort_by_cached_key(|entry| casefold(&posix(entry)));
    let mut payloads: Payloads = Vec::new();
    let mut folded = HashSet::new();
    let mut total = 0usize;
    for entry in entries {
        reject_reparse_chain(files_root, &entry).map_err(Some)?;
        if entry.is_dir() {
            continue;
        }
        if !entry.is_file() {
            return Err(None);
        }
        let relative = posix(entry.strip_prefix(files_root).map_err(|_| None)?);
        validate_portable_path(&relative).map_err(|_| None)?;
        if relative.split('/').filter(|part| !part.is_empty()).count() > PAYLOAD_PATH_DEPTH_LIMIT {
            return Err(None);
        }
        if !folded.insert(casefold(&relative)) {
            return Err(None);
        }
        if payloads.len() >= PAYLOAD_FILE_LIMIT {
            return Err(None);
        }
        let mut value = Vec::new();
        std::fs::File::open(&entry)
            .and_then(|file| file.take(PAYLOAD_BYTE_LIMIT as u64 + 1).read_to_end(&mut value))
            .map_err(|_| None)?;
        total += value.len();
        if value.len() > PAYLOAD_BYTE_LIMIT || total > PAYLOAD_BYTE_LIMIT {
            return Err(None);
        }
        payloads.push((relative, value));
    }
    if payloads.is_empty() {
        return Err(None);
    }
    Ok(payloads)
}

/// `_read_payloads(files_root)`.
fn read_payloads(files_root: &Path) -> DomainResult<Payloads> {
    payloads_from(files_root).map_err(|error| {
        error.unwrap_or_else(|| rejected("Builder ruleset payload is invalid", "builder_payload_invalid"))
    })
}

/// `_reject_derived_participant_hashes(record)`: generated schema hashes stay out of the
/// untrusted descriptor.
fn reject_derived_participant_hashes(record: &JsonMap) -> DomainResult<()> {
    let Some(Json::Array(artifacts)) = record.get("participant_artifacts") else { return Ok(()) };
    if artifacts.iter().any(|raw| raw.as_object().is_some_and(|raw| raw.contains_key("schema_sha256"))) {
        return Err(rejected(
            "Builder participant artifact declaration is invalid",
            "builder_descriptor_invalid",
        ));
    }
    Ok(())
}

// ── Materialization ────────────────────────────────────────────────────────────────────────

fn materialize_draft(
    workspace: &Path,
    draft_root: &Path,
    compact_source_path: &Path,
) -> DomainResult<MaterializedPackageArchive> {
    reject_reparse_chain(workspace, draft_root)?;
    if compact_source_path.is_file() {
        reject_reparse_chain(workspace, compact_source_path)?;
        let source =
            std::fs::read(compact_source_path).map_err(|error| os_error(&error, compact_source_path))?;
        if source.len() > DESCRIPTOR_BYTE_LIMIT {
            return Err(rejected("Builder Ruleset source is too large", "builder_ruleset_source_invalid"));
        }
        let compiled = compile_ruleset_source(&source)?;
        return materialize_package_archive(&[compiled.draft]);
    }
    let descriptor_path = draft_root.join(RULESET_DRAFT_DESCRIPTOR);
    reject_reparse_chain(workspace, &descriptor_path)?;
    let mut record = read_descriptor(&descriptor_path)?;
    let payloads = read_payloads(&draft_root.join(RULESET_DRAFT_FILES_DIRECTORY))?;
    if let Some((_, tests)) = payloads.iter().find(|(path, _)| path == RULESET_TESTS_PATH) {
        parse_ruleset_tests(tests)?;
    }
    if !payloads.iter().any(|(path, _)| path == "runtime/ruleset.yaml") {
        return Err(rejected("Builder ruleset runtime is missing", "builder_payload_invalid"));
    }
    let declared = record
        .get("content_declarations")
        .and_then(Json::as_str)
        .is_some_and(|declarations| payloads.iter().any(|(path, _)| path == declarations));
    if !declared {
        return Err(rejected("Builder content declarations are missing", "builder_payload_invalid"));
    }
    reject_derived_participant_hashes(&record)?;
    record.insert("content_kind".into(), Json::from("ruleset"));
    materialize_package_archive(&[NormalizedPackageDraft::new(record, payloads, 4)])
}

/// `materialize_ruleset_draft(workspace)`: build a Ruleset package without attaching a world
/// or acquiring owner authority.
pub fn materialize_ruleset_draft(workspace: &Path) -> DomainResult<MaterializedPackageArchive> {
    let workspace = resolve(workspace);
    let draft_root = workspace.join(RULESET_DRAFT_DIRECTORY);
    let compact_source_path = draft_root.join(COMPACT_RULESET_SOURCE);
    match materialize_draft(&workspace, &draft_root, &compact_source_path) {
        Ok(archive) => Ok(archive),
        Err(DomainError::Diagnosed { error_type: "ContentShapeError", diagnostics, .. }) => {
            Err(DomainError::command_rejected_with(
                "Builder Ruleset scenarios are invalid",
                "ruleset_tests_invalid",
                diagnostics,
            ))
        }
        Err(error) if is_command_rejected(&error) => Err(error),
        Err(error) if error.is_value_error() || error.python_class() == "TypeError" => {
            let file =
                if compact_source_path.is_file() { COMPACT_RULESET_SOURCE } else { RULESET_DRAFT_DESCRIPTOR };
            Err(DomainError::command_rejected_with(
                "Builder ruleset draft is invalid",
                "builder_package_invalid",
                vec![RulesetDiagnostic {
                    code: "builder_materialization_invalid".into(),
                    phase: "materialization".into(),
                    message: error.message().to_owned(),
                    file: Some(file.into()),
                    path: Some("$".into()),
                    ..Default::default()
                }],
            ))
        }
        Err(error) if is_os_error(&error) => {
            Err(rejected("Builder ruleset draft is unavailable", "builder_package_unavailable"))
        }
        Err(error) => Err(error),
    }
}

// ── Export ─────────────────────────────────────────────────────────────────────────────────

/// `tempfile.NamedTemporaryFile(prefix=".candidate-", suffix=".tmp", dir=..., delete=False)`.
fn temporary_file(directory: &Path) -> std::io::Result<(PathBuf, std::fs::File)> {
    const CHARACTERS: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789_";
    let mut seed = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_nanos() as u64)
        .unwrap_or_default()
        ^ (u64::from(std::process::id()) << 32);
    for _ in 0..100 {
        let name: String = (0..8)
            .map(|_| {
                seed = seed.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1_442_695_040_888_963_407);
                CHARACTERS[(seed >> 33) as usize % CHARACTERS.len()] as char
            })
            .collect();
        let path = directory.join(format!(".candidate-{name}.tmp"));
        match std::fs::OpenOptions::new().write(true).read(true).create_new(true).open(&path) {
            Ok(file) => return Ok((path, file)),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    Err(std::io::Error::new(std::io::ErrorKind::AlreadyExists, "No usable temporary file name found"))
}

/// The path relative to the workspace, with `/` separators (`relative_to(...).as_posix()`).
fn relative_posix(workspace: &Path, path: &Path) -> String {
    path.strip_prefix(workspace).unwrap_or(path).to_string_lossy().replace('\\', "/")
}

fn export_candidate(workspace: &Path, materialized: &MaterializedPackageArchive) -> DomainResult<String> {
    let draft_root = workspace.join(RULESET_DRAFT_DIRECTORY);
    let output_root = draft_root.join(RULESET_OUTPUT_DIRECTORY);
    reject_reparse_chain(workspace, &draft_root)?;
    match std::fs::create_dir(&output_root) {
        Ok(()) => {}
        Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists && output_root.is_dir() => {}
        Err(error) => return Err(os_error(&error, &output_root)),
    }
    reject_reparse_chain(workspace, &output_root)?;
    let target = output_root.join(format!("candidate-{}.sylvia-gm-package", materialized.closure_sha256));
    if target.exists() {
        reject_reparse_chain(workspace, &target)?;
    }
    let value = &materialized.archive;
    if target.is_file() && std::fs::read(&target).map_err(|error| os_error(&error, &target))? == *value {
        return Ok(relative_posix(workspace, &target));
    }
    let mut temporary: Option<PathBuf> = None;
    let written = (|| -> std::io::Result<()> {
        let (path, mut file) = temporary_file(&output_root)?;
        temporary = Some(path.clone());
        file.write_all(value)?;
        file.flush()?;
        file.sync_all()?;
        drop(file);
        std::fs::rename(&path, &target)
    })();
    if let Some(path) = &temporary {
        if path.exists() {
            let _ = std::fs::remove_file(path);
        }
    }
    written.map_err(|error| os_error(&error, &target))?;
    Ok(relative_posix(workspace, &target))
}

/// `export_ruleset_candidate(workspace, materialized)`: persist one admitted archive at a
/// stable workspace-relative artifact path.
pub fn export_ruleset_candidate(
    workspace: &Path,
    materialized: &MaterializedPackageArchive,
) -> DomainResult<String> {
    let workspace = resolve(workspace);
    match export_candidate(&workspace, materialized) {
        Err(error) if !is_command_rejected(&error) && is_os_error(&error) => {
            Err(rejected("Builder ruleset artifact could not be persisted", "builder_artifact_unavailable"))
        }
        other => other,
    }
}

/// `ruleset_install_id(session_id, closure)`: a stable, content-bound publication identity
/// for lost-acknowledgement replay.
pub fn ruleset_install_id(session_id: &str, closure: &str) -> String {
    format!("builder-{}", sha256_hex(format!("{session_id}\0{closure}").as_bytes()))
}
