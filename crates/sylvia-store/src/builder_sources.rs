//! The filesystem half of a campaign purge, from `builder_source_ingestion.py`: the campaign's
//! Builder sources (`.builder-sources`) are moved into an operation-bound journal under
//! `.builder-sources-purge-pending` before the store commits, then deleted after it commits,
//! restored if it fails, and reconciled at launch against the durable purge receipts.
//!
//! Python: `stage_builder_sources_for_purge`, `restore_builder_sources_after_failed_purge`,
//! `finalize_builder_source_purge`, `finalize_existing_builder_source_purge`,
//! `reconcile_builder_source_purges`, and their helpers `_is_link_or_reparse`,
//! `_purge_operation_name`, `_purge_marker`, `_read_purge_marker`, `_remove_empty_purge_parents`
//! and `_cleanup_abandoned_purge_journals`.
//!
//! Declared difference: the marker is read with `sylvia_base::pyjson::loads`, which refuses a
//! few spellings Python's `json.loads` accepts (see that module). Only a hand-edited marker can
//! hold one, and either way it is refused as invalid or read the same.

use crate::error::{StoreError, StoreResult};
use std::collections::HashSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::pyops::py_eq;

/// `SOURCE_DIRECTORY`.
pub const SOURCE_DIRECTORY: &str = ".builder-sources";
/// `PURGE_PENDING_DIRECTORY`.
pub const PURGE_PENDING_DIRECTORY: &str = ".builder-sources-purge-pending";

const MARKER_FORMAT: &str = "sylvia-gm-builder-source-purge";

/// `BuilderSourcePurgeStage`: the recoverable filesystem half of one purge.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuilderSourcePurgeStage {
    pub workspace: PathBuf,
    pub campaign_id: String,
    pub operation_id: String,
    pub operation_path: PathBuf,
    pub sources_path: PathBuf,
    pub moved_this_call: bool,
    pub created_this_call: bool,
}

fn rejected(message: &str) -> StoreError {
    crate::actions::rejected(message, "command_rejected")
}

/// A failure inside one of Python's `try` blocks: a `CommandRejected` passes through, an
/// `OSError` is turned into that block's own refusal.
enum Failure {
    Rejected(StoreError),
    Os,
}

impl From<StoreError> for Failure {
    fn from(error: StoreError) -> Self {
        Self::Rejected(error)
    }
}

impl From<std::io::Error> for Failure {
    fn from(_: std::io::Error) -> Self {
        Self::Os
    }
}

fn os_error(error: std::io::Error) -> StoreError {
    StoreError::Other(error.to_string())
}

/// `_is_link_or_reparse(path)`: a symbolic link, or on Windows any reparse point.
fn is_link_or_reparse(path: &Path) -> StoreResult<bool> {
    let details =
        std::fs::symlink_metadata(path).map_err(|_| rejected("Selected Builder source is unavailable"))?;
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0400;
        Ok(details.file_type().is_symlink() || details.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0)
    }
    #[cfg(not(windows))]
    {
        Ok(details.file_type().is_symlink())
    }
}

/// `_purge_operation_name(operation_id)`.
pub fn purge_operation_name(operation_id: &str) -> String {
    format!("purge-{}", &sylvia_base::hash::sha256_hex(operation_id.as_bytes())[..24])
}

/// `json.dumps(_purge_marker(...), sort_keys=True, separators=(",", ":")).encode("utf-8")`.
fn marker_bytes(campaign_id: &str, operation_id: &str) -> Vec<u8> {
    let mut marker = JsonMap::new();
    marker.insert("format".into(), MARKER_FORMAT.into());
    marker.insert("version".into(), 1.into());
    marker.insert("campaign_id".into(), campaign_id.into());
    marker.insert("operation_id".into(), operation_id.into());
    pyjson::dump_bytes(&Json::Object(marker), Dump::CANONICAL.ascii(true))
}

/// `_read_purge_marker(operation_path)`: the marker's campaign and operation identities.
fn read_purge_marker(operation_path: &Path) -> StoreResult<(String, String)> {
    let invalid = || rejected("Builder source purge marker is invalid");
    let bytes = std::fs::read(operation_path.join("purge.json")).map_err(|_| invalid())?;
    let text = String::from_utf8(bytes).map_err(|_| invalid())?;
    // `read_text` reads with universal newlines.
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    let Ok(Json::Object(payload)) = pyjson::loads(&text) else { return Err(invalid()) };
    let keys: HashSet<&str> = payload.keys().map(String::as_str).collect();
    let (Some(Json::String(campaign_id)), Some(Json::String(operation_id))) =
        (payload.get("campaign_id"), payload.get("operation_id"))
    else {
        return Err(invalid());
    };
    if payload.get("format") != Some(&Json::String(MARKER_FORMAT.into()))
        || !payload.get("version").is_some_and(|version| py_eq(version, &Json::from(1)))
        || keys != HashSet::from(["format", "version", "campaign_id", "operation_id"])
    {
        return Err(invalid());
    }
    Ok((campaign_id.clone(), operation_id.clone()))
}

/// `_remove_empty_purge_parents(operation_path)`: the pending root, once it is empty.
fn remove_empty_purge_parents(operation_path: &Path) -> std::io::Result<()> {
    let Some(pending_root) = operation_path.parent() else { return Ok(()) };
    if pending_root.exists() && std::fs::read_dir(pending_root)?.next().is_none() {
        std::fs::remove_dir(pending_root)?;
    }
    Ok(())
}

/// `_cleanup_abandoned_purge_journals(pending_root)`: only unpublished temporary journals.
fn cleanup_abandoned_purge_journals(pending_root: &Path) -> Result<(), Failure> {
    if !pending_root.exists() {
        return Ok(());
    }
    let entries: Vec<PathBuf> = std::fs::read_dir(pending_root)?
        .map(|entry| entry.map(|entry| entry.path()))
        .collect::<Result<_, _>>()?;
    for entry in entries {
        let name = entry.file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default();
        if !(name.starts_with(".purge-") && name.ends_with(".tmp")) {
            continue;
        }
        if is_link_or_reparse(&entry)? || !entry.is_dir() {
            return Err(rejected("Builder source purge temporary journal is unsafe").into());
        }
        std::fs::remove_dir_all(&entry)
            .map_err(|_| rejected("Builder source purge journal could not be recovered"))?;
    }
    Ok(())
}

/// `tempfile.mkdtemp(dir=parent, prefix=prefix, suffix=".tmp")`: a new directory with a random
/// name, readable only by its owner. Its name never outlives this call.
fn make_temporary_directory(parent: &Path, prefix: &str) -> std::io::Result<PathBuf> {
    for _ in 0..100 {
        let mut random = [0u8; 6];
        getrandom::getrandom(&mut random).map_err(|error| std::io::Error::other(error.to_string()))?;
        let path = parent.join(format!("{prefix}{}.tmp", hex::encode(random)));
        #[cfg_attr(not(unix), allow(unused_mut))]
        let mut builder = std::fs::DirBuilder::new();
        #[cfg(unix)]
        {
            use std::os::unix::fs::DirBuilderExt;
            builder.mode(0o700);
        }
        match builder.create(&path) {
            Ok(()) => return Ok(path),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    Err(std::io::Error::new(std::io::ErrorKind::AlreadyExists, "No usable temporary directory name found"))
}

/// `stage_builder_sources_for_purge`: quarantine the campaign's sources under an
/// operation-bound journal before the store commits.
pub fn stage_builder_sources_for_purge(
    workspace: &Path,
    campaign_id: &str,
    operation_id: &str,
) -> StoreResult<BuilderSourcePurgeStage> {
    std::fs::create_dir_all(workspace).map_err(os_error)?;
    if is_link_or_reparse(workspace)? {
        return Err(rejected("Builder workspace cannot be purged safely"));
    }
    let workspace_root = crate::store::resolve(workspace);
    let source_root = workspace_root.join(SOURCE_DIRECTORY);
    let pending_root = workspace_root.join(PURGE_PENDING_DIRECTORY);
    let name = purge_operation_name(operation_id);
    let operation_path = pending_root.join(&name);
    let sources_path = operation_path.join("sources");
    let mut created_this_call = false;
    let mut moved_this_call = false;
    let mut staging_operation: Option<PathBuf> = None;
    let mut attempt = || -> Result<(), Failure> {
        match std::fs::create_dir(&pending_root) {
            Err(error) if !(error.kind() == std::io::ErrorKind::AlreadyExists && pending_root.is_dir()) => {
                return Err(Failure::Os)
            }
            _ => {}
        }
        if is_link_or_reparse(&pending_root)? {
            return Err(rejected("Builder source purge quarantine is unsafe").into());
        }
        cleanup_abandoned_purge_journals(&pending_root)?;
        let mut others = false;
        for entry in std::fs::read_dir(&pending_root)? {
            others |= entry?.path() != operation_path;
        }
        if others {
            return Err(rejected("Another Builder source purge requires reconciliation").into());
        }
        if operation_path.exists() {
            if is_link_or_reparse(&operation_path)? || !operation_path.is_dir() {
                return Err(rejected("Builder source purge operation is unsafe").into());
            }
            let (marker_campaign_id, marker_operation_id) = read_purge_marker(&operation_path)?;
            if marker_campaign_id != campaign_id || marker_operation_id != operation_id {
                return Err(rejected("Builder source purge identity was reused differently").into());
            }
        } else {
            let staging = make_temporary_directory(&pending_root, &format!(".{name}-"))?;
            staging_operation = Some(staging.clone());
            let mut marker =
                std::fs::OpenOptions::new().write(true).create_new(true).open(staging.join("purge.json"))?;
            marker.write_all(&marker_bytes(campaign_id, operation_id))?;
            marker.flush()?;
            marker.sync_all()?;
            drop(marker);
            std::fs::rename(&staging, &operation_path)?;
            staging_operation = None;
            created_this_call = true;
        }
        if sources_path.exists() && source_root.exists() {
            return Err(rejected("Builder source purge has conflicting filesystem state").into());
        }
        if source_root.exists() {
            if is_link_or_reparse(&source_root)? {
                return Err(rejected("Builder source workspace cannot be purged safely").into());
            }
            std::fs::rename(&source_root, &sources_path)?;
            moved_this_call = true;
        }
        Ok(())
    };
    let outcome = attempt();
    if let Some(staging) = &staging_operation {
        let _ = std::fs::remove_dir_all(staging);
    }
    match outcome {
        Ok(()) => {}
        Err(failure) => {
            if created_this_call && !moved_this_call {
                let _ = std::fs::remove_dir_all(&operation_path);
                remove_empty_purge_parents(&operation_path).map_err(os_error)?;
            }
            return Err(match failure {
                Failure::Rejected(error) => error,
                Failure::Os => rejected("Builder source workspace could not be quarantined"),
            });
        }
    }
    Ok(BuilderSourcePurgeStage {
        workspace: workspace_root,
        campaign_id: campaign_id.to_owned(),
        operation_id: operation_id.to_owned(),
        operation_path,
        sources_path,
        moved_this_call,
        created_this_call,
    })
}

/// `restore_builder_sources_after_failed_purge`: undo a quarantine whose store purge failed.
pub fn restore_builder_sources_after_failed_purge(stage: &BuilderSourcePurgeStage) -> StoreResult<()> {
    let source_root = stage.workspace.join(SOURCE_DIRECTORY);
    if stage.sources_path.exists() && source_root.exists() {
        return Err(rejected("Builder source purge could not be rolled back safely"));
    }
    let attempt = || -> Result<(), Failure> {
        if stage.sources_path.exists() {
            if is_link_or_reparse(&stage.sources_path)? {
                return Err(rejected("Builder source purge could not be rolled back safely").into());
            }
            std::fs::rename(&stage.sources_path, &source_root)?;
        }
        if stage.operation_path.exists() {
            std::fs::remove_dir_all(&stage.operation_path)?;
        }
        remove_empty_purge_parents(&stage.operation_path)?;
        Ok(())
    };
    attempt().map_err(|failure| match failure {
        Failure::Rejected(error) => error,
        Failure::Os => rejected("Builder source purge could not be rolled back"),
    })
}

/// `finalize_builder_source_purge`: delete the quarantine after the store purge commits.
pub fn finalize_builder_source_purge(stage: &BuilderSourcePurgeStage) -> StoreResult<()> {
    if !stage.operation_path.exists() {
        return Ok(());
    }
    if is_link_or_reparse(&stage.operation_path)? {
        return Err(rejected("Builder source purge quarantine is unsafe"));
    }
    std::fs::remove_dir_all(&stage.operation_path)
        .and_then(|()| remove_empty_purge_parents(&stage.operation_path))
        .map_err(|_| rejected("Builder source workspace could not be purged"))
}

/// `finalize_existing_builder_source_purge`: finalize only this exact earlier purge's quarantine.
pub fn finalize_existing_builder_source_purge(
    workspace: &Path,
    campaign_id: &str,
    operation_id: &str,
) -> StoreResult<()> {
    let operation_path = workspace.join(PURGE_PENDING_DIRECTORY).join(purge_operation_name(operation_id));
    if !operation_path.exists() {
        return Ok(());
    }
    let (marker_campaign_id, marker_operation_id) = read_purge_marker(&operation_path)?;
    if marker_campaign_id != campaign_id || marker_operation_id != operation_id {
        return Err(rejected("Builder source purge identity was reused differently"));
    }
    finalize_builder_source_purge(&BuilderSourcePurgeStage {
        workspace: workspace.to_path_buf(),
        campaign_id: campaign_id.to_owned(),
        operation_id: operation_id.to_owned(),
        sources_path: operation_path.join("sources"),
        operation_path,
        moved_this_call: false,
        created_this_call: false,
    })
}

/// `reconcile_builder_source_purges`: resolve every purge journal against the durable receipts.
/// A committed purge's quarantine is deleted, a live campaign's is restored, and anything else
/// is refused. Python's owner runs it at launch.
pub fn reconcile_builder_source_purges(
    storage_root: &Path,
    live_campaign_ids: &HashSet<String>,
    purge_receipt_exists: &mut dyn FnMut(&str, &str) -> StoreResult<bool>,
) -> StoreResult<()> {
    let campaigns_root = storage_root.join("campaigns");
    if !campaigns_root.exists() {
        return Ok(());
    }
    if is_link_or_reparse(&campaigns_root)? {
        return Err(rejected("Campaign workspace root is unsafe"));
    }
    let workspaces: Vec<PathBuf> = std::fs::read_dir(&campaigns_root)
        .map_err(os_error)?
        .map(|entry| entry.map(|entry| entry.path()))
        .collect::<Result<_, _>>()
        .map_err(os_error)?;
    for workspace in workspaces {
        if !workspace.is_dir() || is_link_or_reparse(&workspace)? {
            continue;
        }
        let pending_root = workspace.join(PURGE_PENDING_DIRECTORY);
        if !pending_root.exists() {
            continue;
        }
        if is_link_or_reparse(&pending_root)? {
            return Err(rejected("Builder source purge quarantine is unsafe"));
        }
        cleanup_abandoned_purge_journals(&pending_root).map_err(|failure| match failure {
            Failure::Rejected(error) => error,
            Failure::Os => rejected("Builder source purge journal could not be recovered"),
        })?;
        let operations: Vec<PathBuf> = std::fs::read_dir(&pending_root)
            .map_err(os_error)?
            .map(|entry| entry.map(|entry| entry.path()))
            .collect::<Result<_, _>>()
            .map_err(os_error)?;
        for operation_path in operations {
            if !operation_path.is_dir() || is_link_or_reparse(&operation_path)? {
                return Err(rejected("Builder source purge operation is unsafe"));
            }
            let (campaign_id, operation_id) = read_purge_marker(&operation_path)?;
            let workspace_name = workspace.file_name().map(|name| name.to_string_lossy().into_owned());
            if workspace_name.as_deref() != Some(campaign_id.as_str()) {
                return Err(rejected("Builder source purge marker names the wrong campaign"));
            }
            let stage = BuilderSourcePurgeStage {
                workspace: workspace.clone(),
                campaign_id: campaign_id.clone(),
                operation_id: operation_id.clone(),
                sources_path: operation_path.join("sources"),
                operation_path,
                moved_this_call: false,
                created_this_call: false,
            };
            if purge_receipt_exists(&operation_id, &campaign_id)? {
                finalize_builder_source_purge(&stage)?;
            } else if live_campaign_ids.contains(&campaign_id) {
                restore_builder_sources_after_failed_purge(&stage)?;
            } else {
                return Err(rejected("Builder source purge outcome cannot be reconciled"));
            }
        }
    }
    Ok(())
}
