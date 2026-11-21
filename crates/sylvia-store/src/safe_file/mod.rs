//! `infrastructure/safe_file.py`: the operating-system primitives behind database backup and
//! portable export.
//!
//! Backup and export share one publication sequence (in [`crate::backup`]): exclusively create
//! a private temporary file beside the destination, fill and verify it, flush it, then replace
//! the destination in one atomic step. Only the primitives that sequence needs from the
//! operating system live here, with a Windows and a Linux implementation of the same functions.
//! Any other platform reports every target as lexically unsupported, as Python does.
//!
//! Handles are owned [`std::fs::File`]s. Python's handles are integers whose `close` reports
//! failure; dropping a `File` cannot, so [`close_handle`] reports success once the handle is
//! released.

use std::fs::File;
use std::path::Path;

#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "linux")]
use linux as sys;
#[cfg(windows)]
mod windows;
#[cfg(windows)]
use windows as sys;
#[cfg(not(any(windows, target_os = "linux")))]
mod unsupported;
#[cfg(not(any(windows, target_os = "linux")))]
use unsupported as sys;

/// The platform's name (`SafeFiles.name`).
pub const PLATFORM: &str = sys::NAME;

/// Outcome of the final atomic replacement of a destination (`Publication`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Publication {
    /// The destination now holds the new file, durably.
    Published,
    /// Nothing was replaced.
    NotPublished,
    /// The replacement happened but its directory entry could not be made durable.
    DurabilityUncertain,
}

/// `VOLUME_UNKNOWN`.
pub const VOLUME_UNKNOWN: &str = "unknown";
/// `VOLUME_REMOVABLE`.
pub const VOLUME_REMOVABLE: &str = "removable";
/// `VOLUME_REMOTE`.
pub const VOLUME_REMOTE: &str = "remote";
/// `VOLUME_UNSUPPORTED`.
pub const VOLUME_UNSUPPORTED: &str = "unsupported";
/// `ANCESTOR_MISSING`.
pub const ANCESTOR_MISSING: &str = "ancestor_missing";
/// `ANCESTOR_ATTRIBUTES_UNSUPPORTED`.
pub const ANCESTOR_ATTRIBUTES_UNSUPPORTED: &str = "attributes_unsupported";

/// `LOCAL_FILESYSTEMS`.
pub const LOCAL_FILESYSTEMS: [&str; 3] = ["btrfs", "ext4", "xfs"];
/// `_REMOTE_FILESYSTEMS`.
pub const REMOTE_FILESYSTEMS: [&str; 16] = [
    "9p",
    "afs",
    "beegfs",
    "ceph",
    "cifs",
    "davfs",
    "fuse.rclone",
    "fuse.sshfs",
    "glusterfs",
    "gpfs",
    "lustre",
    "nfs",
    "nfs4",
    "smb3",
    "smbfs",
    "virtiofs",
];

/// Python's `str.splitlines()` separators.
fn is_line_boundary(c: char) -> bool {
    matches!(
        c,
        '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}'
    )
}

/// `str.splitlines()`: `\r\n` is one boundary and a final boundary adds no empty line.
pub(crate) fn py_splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((index, c)) = chars.next() {
        if is_line_boundary(c) {
            lines.push(&text[start..index]);
            let mut end = index + c.len_utf8();
            if c == '\r' {
                if let Some(&(next, '\n')) = chars.peek() {
                    chars.next();
                    end = next + 1;
                }
            }
            start = end;
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// `_unescape_mount_field`: `\NNN` octal escapes become the character they name.
fn unescape_mount_field(value: &str) -> String {
    let bytes = value.as_bytes();
    let mut out = String::with_capacity(value.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'\\'
            && index + 4 <= bytes.len()
            && bytes[index + 1..index + 4].iter().all(|digit| (b'0'..=b'7').contains(digit))
        {
            let code = u32::from(bytes[index + 1] - b'0') * 64
                + u32::from(bytes[index + 2] - b'0') * 8
                + u32::from(bytes[index + 3] - b'0');
            out.push(char::from_u32(code).unwrap_or('\u{fffd}'));
            index += 4;
        } else {
            let c = value[index..].chars().next().unwrap_or('\u{fffd}');
            out.push(c);
            index += c.len_utf8();
        }
    }
    out
}

/// `mount_filesystem_type`: the filesystem type of the mount containing an absolute resolved
/// path. The longest matching mount point wins; among equal ones the later line wins.
pub fn mount_filesystem_type(mountinfo: &str, resolved_path: &str) -> Option<String> {
    let mut best_length: isize = -1;
    let mut best_type = None;
    for line in py_splitlines(mountinfo) {
        let fields: Vec<&str> = line.split(' ').collect();
        let Some(separator) = fields.iter().skip(6).position(|field| *field == "-").map(|offset| offset + 6)
        else {
            continue;
        };
        let (Some(mount_point), Some(filesystem_type)) = (fields.get(4), fields.get(separator + 1)) else {
            continue;
        };
        let mount_point = unescape_mount_field(mount_point);
        let matches = if mount_point == "/" {
            resolved_path.starts_with('/')
        } else {
            resolved_path == mount_point
                || resolved_path.starts_with(&format!("{}/", mount_point.trim_end_matches('/')))
        };
        let length = isize::try_from(mount_point.chars().count()).unwrap_or(isize::MAX);
        if matches && length >= best_length {
            best_length = length;
            best_type = Some((*filesystem_type).to_owned());
        }
    }
    best_type
}

/// `classify_filesystem`: `None` admits a local journalling filesystem; otherwise the reason.
pub fn classify_filesystem(filesystem_type: Option<&str>) -> Option<&'static str> {
    match filesystem_type {
        None => Some(VOLUME_UNKNOWN),
        Some(name) if LOCAL_FILESYSTEMS.contains(&name) => None,
        Some(name) if REMOTE_FILESYSTEMS.contains(&name) => Some(VOLUME_REMOTE),
        Some(_) => Some(VOLUME_UNSUPPORTED),
    }
}

/// A file identity: `(st_dev, st_ino)` on Linux, `(volume serial, file index)` on Windows.
pub type Identity = (u64, u64);

/// `os.stat` identity: `(st_dev, st_ino)`, following links like `Path.stat()`.
pub fn stat_identity(path: &Path) -> std::io::Result<Identity> {
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::fs::MetadataExt;
        let metadata = std::fs::metadata(path)?;
        Ok((metadata.dev(), metadata.ino()))
    }
    #[cfg(windows)]
    {
        let handle = winapi_util::Handle::from_path_any(path)?;
        let information = winapi_util::file::information(&handle)?;
        Ok((information.volume_serial_number(), information.file_index()))
    }
    #[cfg(not(any(windows, target_os = "linux")))]
    {
        let _ = path;
        Err(std::io::Error::other("safe file operations are unsupported on this platform"))
    }
}

/// `lexical_backup_target_supported`.
pub fn lexical_backup_target_supported(path: &Path) -> bool {
    sys::lexical_backup_target_supported(path)
}

/// `lexical_export_target_supported`.
pub fn lexical_export_target_supported(path: &Path) -> bool {
    sys::lexical_export_target_supported(path)
}

/// `volume_admission`: `None` when the volume is admitted, otherwise the reason.
pub fn volume_admission(path: &Path) -> Option<&'static str> {
    sys::volume_admission(path)
}

/// `entry_attributes`: the entry's own attributes (not a link's target), `None` when absent.
pub fn entry_attributes(path: &Path) -> std::io::Result<Option<u32>> {
    sys::entry_attributes(path)
}

/// `attributes_supported`.
pub fn attributes_supported(attributes: u32) -> bool {
    sys::attributes_supported(attributes)
}

/// `attributes_are_link`.
pub fn attributes_are_link(attributes: u32) -> bool {
    sys::attributes_are_link(attributes)
}

/// `ancestors_admission`: `None` when every ancestor is admitted, otherwise the reason.
pub fn ancestors_admission(parent: &Path) -> std::io::Result<Option<&'static str>> {
    sys::ancestors_admission(parent)
}

/// `create_exclusive`: create a new private file, failing with `AlreadyExists` if present.
pub fn create_exclusive(path: &Path) -> std::io::Result<File> {
    sys::create_exclusive(path)
}

/// `descriptor_identity`.
pub fn descriptor_identity(file: &File) -> std::io::Result<Identity> {
    sys::handle_identity(file)
}

/// `open_write_guard`: a handle that denies other writers and deletion while held (Windows) or
/// pins the identity (Linux).
pub fn open_write_guard(path: &Path) -> std::io::Result<File> {
    sys::open_write_guard(path)
}

/// `open_content_seal`.
pub fn open_content_seal(path: &Path) -> std::io::Result<File> {
    sys::open_content_seal(path)
}

/// `open_identity_handle`: names the final component itself, link or not.
pub fn open_identity_handle(path: &Path) -> std::io::Result<File> {
    sys::open_identity_handle(path)
}

/// `open_cleanup_handle`.
pub fn open_cleanup_handle(path: &Path) -> std::io::Result<File> {
    sys::open_cleanup_handle(path)
}

/// `handle_identity`.
pub fn handle_identity(file: &File) -> std::io::Result<Identity> {
    sys::handle_identity(file)
}

/// `flush_handle`.
pub fn flush_handle(file: &File) -> bool {
    sys::flush_handle(file)
}

/// `close_handle`: release the handle.
pub fn close_handle(file: File) -> bool {
    drop(file);
    true
}

/// `delete_by_handle`: delete the file the handle holds, only while `path` still names it.
pub fn delete_by_handle(file: &File, path: &Path) -> bool {
    sys::delete_by_handle(file, path)
}

/// `publish_replace`: atomically replace `target` with `source` inside the pinned parent.
pub fn publish_replace(source: &Path, target: &Path, parent_identity: Identity) -> Publication {
    sys::publish_replace(source, target, parent_identity)
}

/// `os.path.abspath`: absolute and lexically normalised (`.` and `..` collapsed).
pub fn abspath(path: &Path) -> std::path::PathBuf {
    sys::abspath(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splitlines_matches_python() {
        assert_eq!(py_splitlines("a\r\nb\rc\n"), ["a", "b", "c"]);
        assert_eq!(py_splitlines("a\n\nb"), ["a", "", "b"]);
        assert_eq!(py_splitlines("a\u{2028}b\u{85}c"), ["a", "b", "c"]);
        assert!(py_splitlines("").is_empty());
    }

    #[test]
    fn unescapes_octal_fields() {
        assert_eq!(unescape_mount_field("/my\\040disk"), "/my disk");
        assert_eq!(unescape_mount_field("/x\\04"), "/x\\04");
        assert_eq!(unescape_mount_field("/x\\0400"), "/x 0");
    }
}
