//! `application/desktop_update/payload.py`: the stored artifact is checked again at each
//! trust boundary instead of trusting a journal label.
//!
//! Ported so far: `verify_artifact`. `extract_portable` comes with update preparation.

use super::manifest::Manifest;
use super::storage::{directory_identity, stable_digest};
use super::update_error;
use std::path::Path;
use sylvia_base::CodedResult;

/// `verify_artifact(path, manifest)`: the file is regular, unaliased, unchanged while read,
/// and has exactly the signed size and SHA-256.
pub fn verify_artifact(path: &Path, manifest: &Manifest) -> CodedResult<()> {
    directory_identity(path.parent().unwrap_or(Path::new("")))?;
    match stable_digest(path, manifest.artifact_size) {
        Ok(Some(digest)) if digest == manifest.artifact_sha256 => Ok(()),
        _ => {
            Err(update_error("artifact_mismatch", "The stored update no longer matches the signed release."))
        }
    }
}
