//! `application/import_repair_sessions.py`: remember a failed file selection privately, so the
//! player can open an independent repair chat on request (`offer_import_repair` with
//! `_check_private_path`).
//!
//! Starting the repair chat (`start_import_repair_chat`) follows with the rest of
//! `import_repair.py`, which stages the editable copy.

use crate::bundled_packages::BundledPackages;
use crate::import_repair::inspect_repair_source;
use crate::package_materializer::reject_reparse_chain;
use std::io::Write;
use std::path::Path;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::{DomainError, DomainResult};

/// `_check_private_path(storage_root, target)`: check the existing ancestors before creating an
/// owner-private receipt or workspace.
pub fn check_private_path(storage_root: &Path, target: &Path) -> DomainResult<()> {
    let mut current = target.to_path_buf();
    while !current.exists() && std::fs::symlink_metadata(&current).is_err() && current != storage_root {
        match current.parent() {
            Some(parent) => current = parent.to_path_buf(),
            None => break,
        }
    }
    reject_reparse_chain(storage_root, &current)
}

/// The failed import `offer_import_repair` is given: `str(error)` and `error.diagnostics` as
/// payloads.
#[derive(Debug, Clone, Copy)]
pub struct FailedImport<'a> {
    pub message: &'a str,
    pub diagnostics: &'a [Json],
}

/// Which of the `except` clauses around an import catches an error, by Python's class.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CaughtAs {
    /// `ValueError` and its subclasses.
    Value,
    /// `OSError` and its subclasses.
    Os,
    /// `AuthorityError` and its subclasses.
    Authority,
    /// Anything else (`TypeError`, `RuntimeError`, `zipfile.BadZipFile`, ...).
    Other,
}

/// The clause that catches an exception of Python class `class`.
pub fn caught_as(class: &str) -> CaughtAs {
    match class {
        "ValueError"
        | "JSONDecodeError"
        | "UnicodeError"
        | "UnicodeDecodeError"
        | "UnicodeEncodeError"
        | "StaleWorkspaceFileError"
        | "ContentShapeError"
        | "OwnerProtocolError" => CaughtAs::Value,
        "OSError" | "FileNotFoundError" | "FileExistsError" | "PermissionError" | "IsADirectoryError"
        | "NotADirectoryError" => CaughtAs::Os,
        "AuthorityError"
        | "SchemaIdentityError"
        | "OwnerConflict"
        | "WrongOwner"
        | "WrongWriter"
        | "StaleRevision"
        | "IdempotencyConflict"
        | "CommandRejected"
        | "MechanicsRejected"
        | "RulesetPreconditionRejected"
        | "PortableRecoveryFailure"
        | "_BackupFailure"
        | "_ExportFailure" => CaughtAs::Authority,
        _ => CaughtAs::Other,
    }
}

/// [`caught_as`] for a domain error.
pub fn domain_caught_as(error: &DomainError) -> CaughtAs {
    if error.is_value_error() {
        CaughtAs::Value
    } else {
        caught_as(error.python_class())
    }
}

/// `offer_import_repair(storage_root, source, install_id=..., error=...)`: offer a repair only for
/// a safely readable archive, keeping its machine path out of the frontend and chat. `None`
/// when the source cannot be inspected (the caller then re-raises the failure).
pub fn offer_import_repair(
    storage_root: &Path,
    source: &Path,
    install_id: &str,
    error: FailedImport<'_>,
    bundled: &BundledPackages,
) -> DomainResult<Option<JsonMap>> {
    let inspection = match inspect_repair_source(source, bundled) {
        Ok(inspection) => inspection,
        // `except (AuthorityError, OSError, ValueError)`: not repairable.
        Err(failure) if domain_caught_as(&failure) != CaughtAs::Other => return Ok(None),
        Err(failure) => return Err(failure),
    };
    let source_text = source.to_string_lossy().into_owned();
    let repair_id = sha256_hex(canonical_json_bytes(&serde_json::json!([
        install_id,
        inspection.source_sha256,
        source_text
    ])));
    let mut errors: Vec<Json> = error.diagnostics.iter().take(16).cloned().collect();
    if errors.is_empty() {
        errors = inspection.diagnostics.iter().take(16).map(|item| Json::Object(item.as_payload())).collect();
    }
    let message: String = error.message.chars().take(1000).collect();
    let record = serde_json::json!({
        "source_path": source_text,
        "source_sha256": inspection.source_sha256,
        "kind": inspection.kind.as_str(),
        "file_name": inspection.file_name,
        "message": message,
        "validation_errors": errors,
    });
    let root = storage_root.join("import-repair-sources");
    let target = root.join(format!("{repair_id}.json"));
    check_private_path(storage_root, &target)?;
    std::fs::create_dir_all(&root).map_err(|failure| crate::workspace_files::os_error(&failure, &root))?;
    let encoded = canonical_json_bytes(&record);
    match std::fs::OpenOptions::new().write(true).create_new(true).open(&target) {
        Ok(mut output) => {
            output
                .write_all(&encoded)
                .map_err(|failure| crate::workspace_files::os_error(&failure, &target))?;
        }
        Err(failure) if failure.kind() == std::io::ErrorKind::AlreadyExists => {
            let existing = std::fs::read(&target)
                .map_err(|failure| crate::workspace_files::os_error(&failure, &target))?;
            if existing != encoded {
                return Err(DomainError::command_rejected_with(
                    "The failed import receipt changed; select the file again",
                    "command_rejected",
                    Vec::new(),
                ));
            }
        }
        Err(failure) => return Err(crate::workspace_files::os_error(&failure, &target)),
    }
    let mut offer = JsonMap::new();
    offer.insert("status".into(), "repairable".into());
    offer.insert("repair_id".into(), repair_id.into());
    offer.insert("file_name".into(), inspection.file_name.into());
    offer.insert("message".into(), message.into());
    offer.insert("validation_errors".into(), Json::Array(errors));
    Ok(Some(offer))
}
