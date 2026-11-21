//! `_WindowsFiles`: fixed-disk admission, reparse-point attributes, share modes that deny other
//! writers and deletion, handle-bound deletion and `MoveFileExW` write-through replacement.
//!
//! The opens go through `OpenOptionsExt` with Python's exact access, share and flag values, and
//! handle identity through `winapi_util`. Three kernel32 calls have no safe binding and are made
//! directly in [`ffi`], the only unsafe code in the store: `GetDriveTypeW`,
//! `SetFileInformationByHandle(FileDispositionInfo)` and `MoveFileExW`.

use super::{
    Identity, Publication, ANCESTOR_ATTRIBUTES_UNSUPPORTED, ANCESTOR_MISSING, VOLUME_REMOTE,
    VOLUME_REMOVABLE, VOLUME_UNKNOWN, VOLUME_UNSUPPORTED,
};
use std::fs::{File, OpenOptions};
use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Component, Path, PathBuf};

pub(super) const NAME: &str = "windows";

const DRIVE_UNKNOWN: u32 = 0;
const DRIVE_REMOVABLE: u32 = 2;
const DRIVE_FIXED: u32 = 3;
const DRIVE_REMOTE: u32 = 4;
const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
const UNSUPPORTED_ATTRIBUTES: u32 = 0x0000_0400 // reparse point
    | 0x0000_1000 // offline
    | 0x0004_0000 // recall on open
    | 0x0040_0000; // recall on data access
const GENERIC_READ: u32 = 0x8000_0000;
const GENERIC_WRITE: u32 = 0x4000_0000;
const DELETE_ACCESS: u32 = 0x0001_0000;
const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
const FILE_SHARE_READ: u32 = 0x0000_0001;
const FILE_SHARE_WRITE: u32 = 0x0000_0002;
const FILE_SHARE_DELETE: u32 = 0x0000_0004;
const FILE_ATTRIBUTE_NORMAL: u32 = 0x0000_0080;
const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
/// `ERROR_FILE_NOT_FOUND`, `ERROR_PATH_NOT_FOUND`.
const NOT_FOUND_ERRORS: [i32; 2] = [2, 3];

/// `os.path.abspath` on Windows: `GetFullPathNameW`, which also collapses `.` and `..`.
pub(super) fn abspath(path: &Path) -> PathBuf {
    std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf())
}

fn text(path: &Path) -> String {
    path.as_os_str().to_string_lossy().into_owned()
}

/// `Path(...).name`: the final component, empty for a root.
fn name(path: &Path) -> String {
    match path.components().next_back() {
        Some(Component::Normal(name)) => name.to_string_lossy().into_owned(),
        _ => String::new(),
    }
}

pub(super) fn lexical_backup_target_supported(path: &Path) -> bool {
    let normalized = abspath(path);
    let text = text(&normalized);
    !(text.starts_with("\\\\") || text.starts_with("//") || name(&normalized).contains(':'))
}

pub(super) fn lexical_export_target_supported(path: &Path) -> bool {
    let raw = text(path);
    if ["\\\\", "//", "\\?\\", "\\.\\"].iter().any(|prefix| raw.starts_with(prefix)) {
        return false;
    }
    !name(&abspath(path)).contains(':')
}

/// `Path(...).anchor`: drive and root, for example `C:\` or `\\server\share\`.
fn anchor(path: &Path) -> String {
    let mut out = String::new();
    for component in path.components() {
        match component {
            Component::Prefix(prefix) => out.push_str(&prefix.as_os_str().to_string_lossy()),
            Component::RootDir => {
                out.push('\\');
                break;
            }
            _ => break,
        }
    }
    out
}

pub(super) fn volume_admission(path: &Path) -> Option<&'static str> {
    let root = anchor(&abspath(path));
    if root.is_empty() {
        return Some(VOLUME_UNKNOWN);
    }
    match ffi::drive_type(&root) {
        DRIVE_FIXED => None,
        DRIVE_UNKNOWN => Some(VOLUME_UNKNOWN),
        DRIVE_REMOVABLE => Some(VOLUME_REMOVABLE),
        DRIVE_REMOTE => Some(VOLUME_REMOTE),
        _ => Some(VOLUME_UNSUPPORTED),
    }
}

/// `GetFileAttributesW` of the entry itself; `None` for file or path not found.
pub(super) fn entry_attributes(path: &Path) -> std::io::Result<Option<u32>> {
    match std::fs::symlink_metadata(path) {
        Ok(metadata) => Ok(Some(metadata.file_attributes())),
        Err(error) if error.raw_os_error().is_some_and(|code| NOT_FOUND_ERRORS.contains(&code)) => Ok(None),
        Err(error) => Err(error),
    }
}

pub(super) fn attributes_supported(attributes: u32) -> bool {
    attributes & UNSUPPORTED_ATTRIBUTES == 0
}

pub(super) fn attributes_are_link(attributes: u32) -> bool {
    attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
}

pub(super) fn ancestors_admission(parent: &Path) -> std::io::Result<Option<&'static str>> {
    let mut current = parent.to_path_buf();
    loop {
        let Some(attributes) = entry_attributes(&current)? else {
            return Ok(Some(ANCESTOR_MISSING));
        };
        if !attributes_supported(attributes) {
            return Ok(Some(ANCESTOR_ATTRIBUTES_UNSUPPORTED));
        }
        match current.parent() {
            Some(parent) if !parent.as_os_str().is_empty() && parent != current => {
                current = parent.to_path_buf()
            }
            _ => return Ok(None),
        }
    }
}

/// `os.open(path, O_CREAT | O_EXCL | O_RDWR | O_BINARY, 0o600)`: the C runtime's
/// `CreateFileW(CREATE_NEW)` with read and write sharing.
pub(super) fn create_exclusive(path: &Path) -> std::io::Result<File> {
    OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .open(path)
}

pub(super) fn handle_identity(file: &File) -> std::io::Result<Identity> {
    let information = winapi_util::file::information(file)?;
    Ok((information.volume_serial_number(), information.file_index()))
}

/// `CreateFileW(path, access, share, NULL, OPEN_EXISTING, NORMAL | OPEN_REPARSE_POINT)`.
fn create_handle(path: &Path, access: u32, share: u32) -> std::io::Result<File> {
    OpenOptions::new()
        .access_mode(access)
        .share_mode(share)
        .attributes(FILE_ATTRIBUTE_NORMAL)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
}

pub(super) fn open_write_guard(path: &Path) -> std::io::Result<File> {
    create_handle(path, FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE)
}

pub(super) fn open_content_seal(path: &Path) -> std::io::Result<File> {
    create_handle(path, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ)
}

pub(super) fn open_identity_handle(path: &Path) -> std::io::Result<File> {
    create_handle(path, FILE_READ_ATTRIBUTES, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
}

pub(super) fn open_cleanup_handle(path: &Path) -> std::io::Result<File> {
    create_handle(
        path,
        DELETE_ACCESS | FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
    )
}

/// `FlushFileBuffers`.
pub(super) fn flush_handle(file: &File) -> bool {
    file.sync_all().is_ok()
}

pub(super) fn delete_by_handle(file: &File, _path: &Path) -> bool {
    ffi::set_delete_disposition(file)
}

pub(super) fn publish_replace(source: &Path, target: &Path, _parent_identity: Identity) -> Publication {
    if ffi::move_replace_write_through(source, target) {
        Publication::Published
    } else {
        Publication::NotPublished
    }
}

/// The three kernel32 calls with no safe binding. Each takes only values this module built
/// (a NUL-terminated wide string it owns, or a handle borrowed from a live `File`), so the
/// pointers are valid for the length of the call.
#[allow(unsafe_code)]
mod ffi {
    use std::ffi::OsStr;
    use std::fs::File;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::AsRawHandle;
    use std::path::Path;
    use windows_sys::Win32::Storage::FileSystem::{
        FileDispositionInfo, GetDriveTypeW, MoveFileExW, SetFileInformationByHandle, FILE_DISPOSITION_INFO,
        MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    fn wide(text: &OsStr) -> Vec<u16> {
        text.encode_wide().chain(std::iter::once(0)).collect()
    }

    /// `GetDriveTypeW(volume_root)`.
    pub(super) fn drive_type(root: &str) -> u32 {
        let root = wide(OsStr::new(root));
        // SAFETY: `root` is a NUL-terminated UTF-16 buffer that lives until the call returns.
        unsafe { GetDriveTypeW(root.as_ptr()) }
    }

    /// `SetFileInformationByHandle(handle, FileDispositionInfo, {DeleteFile: TRUE})`.
    pub(super) fn set_delete_disposition(file: &File) -> bool {
        let information = FILE_DISPOSITION_INFO { DeleteFile: true };
        // SAFETY: the handle is borrowed from a live `File`; the pointer and size describe
        // `information`, which outlives the call.
        unsafe {
            SetFileInformationByHandle(
                file.as_raw_handle(),
                FileDispositionInfo,
                std::ptr::from_ref(&information).cast(),
                u32::try_from(std::mem::size_of::<FILE_DISPOSITION_INFO>()).unwrap_or(u32::MAX),
            ) != 0
        }
    }

    /// `MoveFileExW(source, target, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)`.
    pub(super) fn move_replace_write_through(source: &Path, target: &Path) -> bool {
        let source = wide(source.as_os_str());
        let target = wide(target.as_os_str());
        // SAFETY: both are NUL-terminated UTF-16 buffers that live until the call returns.
        unsafe {
            MoveFileExW(source.as_ptr(), target.as_ptr(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)
                != 0
        }
    }
}
