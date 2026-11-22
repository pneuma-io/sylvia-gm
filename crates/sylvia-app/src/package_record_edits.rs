//! `builder/_package_record_edits.py`: apply explicit package-record replacements to an owned
//! package snapshot.

use crate::package_materialization::NormalizedPackageDraft;
use crate::package_materializer::{bounded_json_shape, reject_reparse_chain, resolve};
use std::io::Read;
use std::path::Path;
use sylvia_base::pyjson::{loads_hooked, HookFailure, HookPolicy};
use sylvia_base::pystr::casefold;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::error::{DomainError, DomainResult, RulesetDiagnostic};
use sylvia_domain::pyops::{py_eq, py_require_hashable};

const RECORD_DIRECTORY: &str = "package-records";
const RECORD_BYTE_LIMIT: usize = 2 * 1024 * 1024;
const DERIVED_RECORD_KEYS: [&str; 2] = ["files", "content_sha256"];

fn rejected(message: impl Into<String>) -> DomainError {
    DomainError::command_rejected(message)
}

fn is_command_rejected(error: &DomainError) -> bool {
    error.python_class() == "CommandRejected"
}

/// The classes `apply_package_record_edits` turns into its generic rejection: `KeyError`,
/// `OSError` (and subclasses), `TypeError` and `ValueError` (and subclasses).
fn generic(error: &DomainError) -> bool {
    error.is_value_error()
        || matches!(
            error.python_class(),
            "KeyError"
                | "TypeError"
                | "OSError"
                | "FileNotFoundError"
                | "FileExistsError"
                | "PermissionError"
                | "IsADirectoryError"
                | "NotADirectoryError"
        )
}

/// The body of `_read_record` after the link check; `Err(None)` for the generic rejection.
fn record_value(workspace: &Path, path: &Path) -> Result<JsonMap, Option<DomainError>> {
    let size = std::fs::metadata(path).map(|metadata| metadata.len()).ok();
    if !path.is_file() || size.is_none_or(|size| size > RECORD_BYTE_LIMIT as u64) {
        return Err(None);
    }
    let mut raw = Vec::new();
    std::fs::File::open(path)
        .and_then(|file| file.take(RECORD_BYTE_LIMIT as u64 + 1).read_to_end(&mut raw))
        .map_err(|_| None)?;
    if raw.len() > RECORD_BYTE_LIMIT {
        return Err(None);
    }
    let text = std::str::from_utf8(&raw).map_err(|_| None)?;
    let hooked = match loads_hooked(text, HookPolicy { refuse_duplicates: true, refuse_constants: true }) {
        Ok(hooked) => hooked,
        Err(HookFailure::Decode(error)) => {
            let file = path.strip_prefix(workspace).unwrap_or(path).to_string_lossy().replace('\\', "/");
            let message = format!("Invalid JSON syntax at line {}, column {}.", error.lineno, error.colno);
            return Err(Some(DomainError::command_rejected_with(
                "Builder package record contains invalid JSON",
                "package_record_json_invalid",
                vec![RulesetDiagnostic {
                    code: "package_record_json_invalid".into(),
                    phase: "package_record".into(),
                    message,
                    file: Some(file),
                    guidance: Some(
                        "Read nearby lines and repair the JSON syntax with a bounded edit.".into(),
                    ),
                    ..Default::default()
                }],
            )));
        }
        // Duplicates and NaN/Infinity raise `ValueError`; nesting of 128 levels or more passes
        // Python's reader but not `_bounded_json_shape`; wider integers and lone surrogates
        // are the declared limits (differences rows 1 and 3).
        Err(_) => return Err(None),
    };
    bounded_json_shape(&hooked.value).map_err(|_| None)?;
    match hooked.value {
        // `thaw_json_value` refuses `inf` with `TypeError`.
        Json::Object(map) if !hooked.non_finite => Ok(map),
        _ => Err(None),
    }
}

/// `_read_record(workspace, path)`: one bounded regular JSON record, links refused.
fn read_record(workspace: &Path, path: &Path) -> DomainResult<JsonMap> {
    reject_reparse_chain(workspace, path)?;
    record_value(workspace, path)
        .map_err(|error| error.unwrap_or_else(|| rejected("Builder package record is invalid")))
}

fn has_exactly(keys: &[&String], expected: &[&String]) -> bool {
    keys.len() == expected.len() && expected.iter().all(|key| keys.contains(key))
}

/// `_editable_record(original, replacement)`: a complete replacement, materializer-owned fields
/// removed.
fn editable_record(original: &NormalizedPackageDraft, replacement: JsonMap) -> DomainResult<JsonMap> {
    let original_record = &original.record;
    let editable: Vec<&String> =
        original_record.keys().filter(|key| !DERIVED_RECORD_KEYS.contains(&key.as_str())).collect();
    let supplied: Vec<&String> = replacement.keys().collect();
    let mut with_derived = editable.clone();
    let derived: Vec<String> = DERIVED_RECORD_KEYS.iter().map(|key| (*key).to_owned()).collect();
    with_derived.extend(derived.iter().filter(|key| !editable.contains(key)));
    if !has_exactly(&supplied, &editable) && !has_exactly(&supplied, &with_derived) {
        return Err(rejected("Builder package record fields do not match the saved package"));
    }
    let replacement: JsonMap =
        replacement.into_iter().filter(|(key, _)| !DERIVED_RECORD_KEYS.contains(&key.as_str())).collect();
    for key in ["id", "role", "content_kind"] {
        let (now, then) =
            (replacement.get(key).unwrap_or(&Json::Null), original_record.get(key).unwrap_or(&Json::Null));
        if !py_eq(now, then) {
            return Err(rejected(format!("Builder package record cannot change {key}")));
        }
    }
    Ok(replacement)
}

/// `_dependencies(record)`: a list of objects.
fn dependencies(record: &JsonMap) -> DomainResult<Vec<JsonMap>> {
    match record.get("dependencies") {
        Some(Json::Array(items)) if items.iter().all(Json::is_object) => {
            Ok(items.iter().filter_map(Json::as_object).cloned().collect())
        }
        _ => Err(rejected("Builder package dependencies are invalid")),
    }
}

fn get<'a>(map: &'a JsonMap, key: &str) -> &'a Json {
    map.get(key).unwrap_or(&Json::Null)
}

/// `_rebind_dependencies(records, originals)`: bind every dependency to its supplied local
/// draft without guessing rewires.
fn rebind_dependencies(
    records: &mut [(String, JsonMap)],
    originals: &[(String, &NormalizedPackageDraft)],
) -> DomainResult<()> {
    for index in 0..records.len() {
        let package_id = records[index].0.clone();
        let original = originals.iter().find(|(id, _)| *id == package_id).expect("an original").1;
        // `{dependency.get("namespace"): dependency for ...}`: Python hashes each key; a later
        // equal key replaces the value.
        let mut by_namespace: Vec<(Json, JsonMap)> = Vec::new();
        for dependency in dependencies(&original.record)? {
            let namespace = get(&dependency, "namespace").clone();
            py_require_hashable(&namespace)?;
            match by_namespace.iter_mut().find(|(key, _)| py_eq(key, &namespace)) {
                Some((_, slot)) => *slot = dependency,
                None => by_namespace.push((namespace, dependency)),
            }
        }
        let mut rebound = Vec::new();
        for dependency in dependencies(&records[index].1)? {
            let dependency_id = get(&dependency, "id");
            let mut dependency_version = get(&dependency, "version").clone();
            let namespace = get(&dependency, "namespace");
            let Some(target) =
                dependency_id.as_str().and_then(|id| records.iter().find(|(key, _)| key == id))
            else {
                return Err(rejected("Builder package dependency is absent from the saved copy"));
            };
            let Some(target_version) = target.1.get("version").and_then(Json::as_str).map(str::to_owned)
            else {
                return Err(rejected("Builder package dependency target version is invalid"));
            };
            py_require_hashable(namespace)?;
            let original = by_namespace.iter().find(|(key, _)| py_eq(key, namespace)).map(|(_, value)| value);
            let unchanged_identity = original.is_some_and(|original| {
                py_eq(dependency_id, get(original, "id"))
                    && py_eq(&dependency_version, get(original, "version"))
            });
            if !py_eq(&dependency_version, &Json::from(target_version.as_str())) {
                if !unchanged_identity {
                    return Err(rejected(
                        "Builder package dependency version does not match its supplied draft",
                    ));
                }
                dependency_version = Json::from(target_version);
            }
            let mut entry: JsonMap = dependency
                .into_iter()
                .filter(|(key, _)| key != "version" && key != "content_sha256")
                .collect();
            entry.insert("version".into(), dependency_version);
            rebound.push(Json::Object(entry));
        }
        records[index].1.insert("dependencies".into(), Json::Array(rebound));
    }
    Ok(())
}

/// `_retained_package_ids(records)`: the packages reachable from the primary and the Setting.
fn retained_package_ids(records: &[(String, JsonMap)]) -> DomainResult<Vec<String>> {
    let primaries: Vec<&JsonMap> = records
        .iter()
        .map(|(_, record)| record)
        .filter(|record| py_eq(get(record, "role"), &Json::from("primary")))
        .collect();
    let settings: Vec<&JsonMap> = records
        .iter()
        .map(|(_, record)| record)
        .filter(|record| py_eq(get(record, "content_kind"), &Json::from("campaign_setting")))
        .collect();
    if primaries.len() != 1 || settings.len() > 1 {
        return Err(rejected("Owned package copy has invalid primary or Setting roots"));
    }
    let key_error = |key: &str| DomainError::uncaught("KeyError", sylvia_base::pystr::repr(key));
    let mut pending: Vec<Json> = Vec::new();
    for root in primaries.iter().chain(settings.iter()) {
        let id = root.get("id").ok_or_else(|| key_error("id"))?;
        py_require_hashable(id)?;
        pending.push(id.clone());
    }
    let mut reached: Vec<String> = Vec::new();
    while let Some(package_id) = pending.pop() {
        let Some(id) = package_id.as_str() else {
            return Err(DomainError::uncaught("KeyError", sylvia_base::pystr::repr_json(&package_id)));
        };
        if reached.iter().any(|known| known == id) {
            continue;
        }
        let record = records
            .iter()
            .find(|(key, _)| key == id)
            .map(|(_, record)| record)
            .ok_or_else(|| DomainError::uncaught("KeyError", sylvia_base::pystr::repr(id)))?;
        reached.push(id.to_owned());
        for dependency in dependencies(record)? {
            pending.push(dependency.get("id").cloned().ok_or_else(|| key_error("id"))?);
        }
    }
    Ok(reached)
}

fn apply_edits(
    workspace: &Path,
    draft_root: &Path,
    originals: &[(String, &NormalizedPackageDraft)],
) -> DomainResult<Vec<NormalizedPackageDraft>> {
    reject_reparse_chain(workspace, draft_root)?;
    let record_root = draft_root.join(RECORD_DIRECTORY);
    let mut records: Vec<(String, JsonMap)> = Vec::new();
    if std::fs::symlink_metadata(&record_root).is_err() {
        for (package_id, draft) in originals {
            records.push((package_id.clone(), draft.record.clone()));
        }
    } else {
        reject_reparse_chain(workspace, &record_root)?;
        if !record_root.is_dir() {
            return Err(rejected("Builder package-records path must be a directory"));
        }
        let expected: Vec<String> =
            originals.iter().map(|(package_id, _)| format!("{package_id}.json")).collect();
        let mut folded: Vec<String> = expected.iter().map(|name| casefold(name)).collect();
        folded.sort();
        folded.dedup();
        if folded.len() != expected.len() {
            return Err(rejected("Owned package identities collide as record paths"));
        }
        let entries = std::fs::read_dir(&record_root)
            .map_err(|error| crate::workspace_files::os_error(&error, &record_root))?;
        for entry in entries {
            let entry = entry.map_err(|error| crate::workspace_files::os_error(&error, &record_root))?;
            if !expected.iter().any(|name| entry.file_name().to_string_lossy() == name.as_str()) {
                return Err(rejected("Builder package-records directory contains an unknown entry"));
            }
        }
        for (package_id, draft) in originals {
            let path = record_root.join(format!("{package_id}.json"));
            let record = if std::fs::symlink_metadata(&path).is_ok() {
                editable_record(draft, read_record(workspace, &path)?)?
            } else {
                draft.record.clone()
            };
            records.push((package_id.clone(), record));
        }
    }
    for (_, record) in records.iter_mut() {
        record.shift_remove("files");
        record.shift_remove("content_sha256");
    }
    rebind_dependencies(&mut records, originals)?;
    let retained = retained_package_ids(&records)?;
    Ok(originals
        .iter()
        .filter(|(package_id, _)| retained.contains(package_id))
        .map(|(package_id, draft)| {
            let record = records.iter().find(|(key, _)| key == package_id).expect("a record").1.clone();
            NormalizedPackageDraft::new(record, draft.payloads.clone(), 4)
        })
        .collect())
}

/// `apply_package_record_edits(workspace, draft_root, drafts)`: apply whole-record edits and
/// retain the exact reachable owned closure.
pub fn apply_package_record_edits(
    workspace: &Path,
    draft_root: &Path,
    drafts: &[NormalizedPackageDraft],
) -> DomainResult<Vec<NormalizedPackageDraft>> {
    let workspace = resolve(workspace);
    let draft_root =
        if draft_root.is_absolute() { draft_root.to_path_buf() } else { workspace.join(draft_root) };
    if drafts.is_empty() || drafts.iter().any(|draft| draft.format_version != 4) {
        return Err(rejected("Package record edits require one format-4 saved package copy"));
    }
    let mut originals: Vec<(String, &NormalizedPackageDraft)> = Vec::new();
    for draft in drafts {
        let Some(package_id) = draft.record.get("id").and_then(Json::as_str) else {
            return Err(rejected("Owned package copy has invalid package identities"));
        };
        if originals.iter().any(|(id, _)| id == package_id) {
            return Err(rejected("Owned package copy has invalid package identities"));
        }
        originals.push((package_id.to_owned(), draft));
    }
    match apply_edits(&workspace, &draft_root, &originals) {
        Err(error) if !is_command_rejected(&error) && generic(&error) => {
            Err(rejected("Builder package record edits are invalid"))
        }
        other => other,
    }
}
