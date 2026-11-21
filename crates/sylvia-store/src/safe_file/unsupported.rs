//! `SafeFiles` on any other platform: every target is lexically unsupported, so backup and
//! export fail clearly before touching the filesystem.

use super::{Identity, Publication, ANCESTOR_ATTRIBUTES_UNSUPPORTED, VOLUME_UNSUPPORTED};
use std::fs::File;
use std::path::{Path, PathBuf};

pub(super) const NAME: &str = "unsupported";

fn unsupported() -> std::io::Error {
    std::io::Error::other("safe file operations are unsupported on this platform")
}

pub(super) fn abspath(path: &Path) -> PathBuf {
    std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf())
}
pub(super) fn lexical_backup_target_supported(_path: &Path) -> bool {
    false
}
pub(super) fn lexical_export_target_supported(_path: &Path) -> bool {
    false
}
pub(super) fn volume_admission(_path: &Path) -> Option<&'static str> {
    Some(VOLUME_UNSUPPORTED)
}
pub(super) fn entry_attributes(_path: &Path) -> std::io::Result<Option<u32>> {
    Err(unsupported())
}
pub(super) fn attributes_supported(_attributes: u32) -> bool {
    false
}
pub(super) fn attributes_are_link(_attributes: u32) -> bool {
    false
}
pub(super) fn ancestors_admission(_parent: &Path) -> std::io::Result<Option<&'static str>> {
    Ok(Some(ANCESTOR_ATTRIBUTES_UNSUPPORTED))
}
pub(super) fn create_exclusive(_path: &Path) -> std::io::Result<File> {
    Err(unsupported())
}
pub(super) fn handle_identity(_file: &File) -> std::io::Result<Identity> {
    Err(unsupported())
}
pub(super) fn open_write_guard(_path: &Path) -> std::io::Result<File> {
    Err(unsupported())
}
pub(super) fn open_content_seal(_path: &Path) -> std::io::Result<File> {
    Err(unsupported())
}
pub(super) fn open_identity_handle(_path: &Path) -> std::io::Result<File> {
    Err(unsupported())
}
pub(super) fn open_cleanup_handle(_path: &Path) -> std::io::Result<File> {
    Err(unsupported())
}
pub(super) fn flush_handle(_file: &File) -> bool {
    false
}
pub(super) fn delete_by_handle(_file: &File, _path: &Path) -> bool {
    false
}
pub(super) fn publish_replace(_source: &Path, _target: &Path, _parent_identity: Identity) -> Publication {
    Publication::NotPublished
}
