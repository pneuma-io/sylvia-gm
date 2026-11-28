//! `application/import_repair_sessions.py`: remember a failed file selection privately
//! (`offer_import_repair` with `_check_private_path`), so the player can open an independent
//! repair chat on request (`start_import_repair_chat`), which stages the editable copy once.

use crate::bundled_packages::BundledPackages;
use crate::import_repair::{inspect_repair_source, stage_repair_copy, RepairKind, DRAFT_NAME};
use crate::package_materializer::reject_reparse_chain;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::package_library::canonical_json_bytes;
use sylvia_domain::pyops::py_eq;
use sylvia_domain::session::{BuilderChatRecord, SessionMessageKind};
use sylvia_domain::{DomainError, DomainResult};
use sylvia_store::{StoreHandle, StoreResult};

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

/// `CommandRejected("The failed import is unavailable; select the file again")`.
fn import_unavailable() -> DomainError {
    DomainError::command_rejected_with(
        "The failed import is unavailable; select the file again",
        "command_rejected",
        Vec::new(),
    )
}

/// `record[key]` of the receipt: a missing field is Python's uncaught `KeyError`.
fn receipt_field<'a>(record: &'a JsonMap, key: &str) -> DomainResult<&'a Json> {
    record.get(key).ok_or_else(|| DomainError::uncaught("KeyError", sylvia_base::pystr::repr(key)))
}

/// The private receipt `offer_import_repair` saved: read bounded, an object of a known kind.
/// Python's `except (OSError, ValueError)` turns any failure into "unavailable"; a kind that
/// cannot be hashed for `in {...}` is its uncaught `TypeError`.
fn read_receipt(receipt: &Path) -> DomainResult<(JsonMap, RepairKind)> {
    let read = (|| -> DomainResult<Json> {
        let mut raw = Vec::new();
        std::fs::File::open(receipt)
            .and_then(|file| file.take(128 * 1024 + 1).read_to_end(&mut raw))
            .map_err(|error| crate::workspace_files::os_error(&error, receipt))?;
        if raw.len() > 128 * 1024 {
            return Err(DomainError::value("oversized"));
        }
        sylvia_base::pyjson::loads_bytes(&raw).map_err(crate::reusable_plans::json_error)
    })();
    let caught = |error: &DomainError| {
        error.is_value_error()
            || matches!(
                error.python_class(),
                "OSError"
                    | "FileNotFoundError"
                    | "FileExistsError"
                    | "PermissionError"
                    | "IsADirectoryError"
                    | "NotADirectoryError"
                    | "ValueError"
                    | "JSONDecodeError"
                    | "UnicodeDecodeError"
            )
    };
    let record = match read {
        Ok(Json::Object(record)) => record,
        Ok(_) => return Err(import_unavailable()),
        Err(error) if caught(&error) => return Err(import_unavailable()),
        Err(error) => return Err(error),
    };
    let kind = match record.get("kind") {
        Some(Json::String(kind)) if kind == "package" => RepairKind::Package,
        Some(Json::String(kind)) if kind == "plan" => RepairKind::Plan,
        Some(value @ (Json::Array(_) | Json::Object(_))) => {
            return Err(DomainError::type_error(format!(
                "unhashable type: '{}'",
                sylvia_domain::pyops::py_type_name(value)
            )));
        }
        _ => return Err(import_unavailable()),
    };
    Ok((record, kind))
}

/// `start_import_repair_chat(owner, storage_root, repair_id=..., operation_id=...,
/// provider_id=..., model_id=...)`: the selected source copied once into a new Builder chat's
/// `repair-draft`; later turns use the saved chat and its edited workspace.
pub async fn start_import_repair_chat(
    store: &StoreHandle,
    storage_root: &Path,
    repair_id: &str,
    operation_id: &str,
    provider_id: &str,
    model_id: &str,
) -> StoreResult<BuilderChatRecord> {
    let is_digest =
        repair_id.len() == 64 && repair_id.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'));
    if !is_digest {
        return Err(import_unavailable().into());
    }
    let receipt = storage_root.join("import-repair-sources").join(format!("{repair_id}.json"));
    reject_reparse_chain(storage_root, &receipt)?;
    let (record, kind) = read_receipt(&receipt)?;
    let digest = sha256_hex(canonical_json_bytes(&serde_json::json!([operation_id, repair_id])));
    let session_id = format!("session-builder-{}", &digest[..24]);
    let workspace = storage_root.join("builder-chats").join(&session_id);
    check_private_path(storage_root, &workspace)?;
    let existing = {
        let _operation = store.operation()?;
        let wanted = session_id.clone();
        store
            .call(move |store| store.builder_chat_index())
            .await?
            .into_iter()
            .find(|chat| chat.session().fields().session_id == wanted)
    };
    let mut evidence = JsonMap::new();
    evidence.insert("kind".into(), "import_repair_context".into());
    evidence.insert("repair_id".into(), repair_id.into());
    evidence.insert("source_sha256".into(), receipt_field(&record, "source_sha256")?.clone());
    evidence.insert("import_kind".into(), receipt_field(&record, "kind")?.clone());
    evidence.insert("file_name".into(), receipt_field(&record, "file_name")?.clone());
    evidence.insert("workspace_path".into(), DRAFT_NAME.into());
    evidence.insert("message".into(), receipt_field(&record, "message")?.clone());
    evidence.insert("validation_errors".into(), receipt_field(&record, "validation_errors")?.clone());
    if let Some(existing) = existing {
        let context = store.session_context(&session_id).await?;
        let evidence = Json::Object(evidence);
        if context.messages.iter().any(|message| py_eq(&Json::Object(message.content().clone()), &evidence)) {
            return Ok(existing);
        }
        return Err(DomainError::command_rejected_with(
            "Repair chat source does not match this request",
            "command_rejected",
            Vec::new(),
        )
        .into());
    }
    // `stage_repair_copy(Path(record["source_path"]), record["source_sha256"], workspace, ...)`.
    let source_path = match receipt_field(&record, "source_path")? {
        Json::String(path) => PathBuf::from(path),
        other => {
            return Err(DomainError::type_error(format!(
                "expected str, bytes or os.PathLike object, not {}",
                sylvia_domain::pyops::py_type_name(other)
            ))
            .into());
        }
    };
    let source_sha256 = match receipt_field(&record, "source_sha256")? {
        Json::String(digest) => digest.clone(),
        other => {
            return Err(DomainError::type_error(format!(
                "expected string or bytes-like object, got '{}'",
                sylvia_domain::pyops::py_type_name(other)
            ))
            .into());
        }
    };
    stage_repair_copy(&source_path, &source_sha256, &workspace, kind, None)?;
    let file_name = sylvia_domain::pyops::py_str(receipt_field(&record, "file_name")?);
    let display_name: String = format!("Repair {file_name}").chars().take(120).collect();
    let chat = {
        let (session_id, provider_id, model_id) =
            (session_id.clone(), provider_id.to_owned(), model_id.to_owned());
        store
            .call(move |store| store.create_builder_chat(&session_id, &provider_id, &model_id, &display_name))
            .await?
    };
    store
        .append_agent_message(
            &session_id,
            &format!("repair-source-{}", &repair_id[..24]),
            SessionMessageKind::Evidence,
            &evidence,
        )
        .await?;
    Ok(chat)
}
