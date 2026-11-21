//! `_LinuxFiles`: descriptor-based primitives for local Linux filesystems.
//!
//! Linux has no mandatory share modes, so handles are descriptors that pin and re-prove
//! `(st_dev, st_ino)`. Every open refuses to follow a link at the final component, cleanup
//! unlinks a name only after proving through the pinned parent that it still names the held
//! file, and publication renames inside the pinned parent and then syncs that directory.

use super::{
    classify_filesystem, mount_filesystem_type, Identity, Publication, ANCESTOR_MISSING, VOLUME_UNKNOWN,
};
use rustix::fs::{self as rfs, AtFlags, FileType, Mode, OFlags, CWD};
use std::ffi::OsStr;
use std::fs::File;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};

pub(super) const NAME: &str = "linux";

const MOUNTINFO: &str = "/proc/self/mountinfo";

fn open_flags(access: OFlags) -> OFlags {
    access | OFlags::NOFOLLOW | OFlags::CLOEXEC | OFlags::NONBLOCK
}

/// `O_PATH | O_NOFOLLOW` names the final component itself, link or not, without needing read
/// permission, like the Windows identity handle that opens a reparse point.
fn path_flags() -> OFlags {
    OFlags::PATH | OFlags::NOFOLLOW | OFlags::CLOEXEC
}

pub(super) fn lexical_backup_target_supported(path: &Path) -> bool {
    !path.as_os_str().as_bytes().contains(&0)
}

pub(super) fn lexical_export_target_supported(path: &Path) -> bool {
    !path.as_os_str().as_bytes().contains(&0)
}

/// `posixpath.abspath`: join with the working directory, then `normpath`.
pub(super) fn abspath(path: &Path) -> PathBuf {
    let bytes = path.as_os_str().as_bytes();
    let joined: Vec<u8> = if bytes.starts_with(b"/") {
        bytes.to_vec()
    } else {
        match std::env::current_dir() {
            Ok(cwd) => {
                let mut joined = cwd.as_os_str().as_bytes().to_vec();
                if !joined.ends_with(b"/") {
                    joined.push(b'/');
                }
                joined.extend_from_slice(bytes);
                joined
            }
            Err(_) => bytes.to_vec(),
        }
    };
    PathBuf::from(OsStr::from_bytes(&normpath(&joined)))
}

/// `posixpath.normpath` for bytes.
fn normpath(path: &[u8]) -> Vec<u8> {
    if path.is_empty() {
        return b".".to_vec();
    }
    let initial = if path.starts_with(b"/") {
        if path.starts_with(b"//") && !path.starts_with(b"///") {
            2
        } else {
            1
        }
    } else {
        0
    };
    let mut parts: Vec<&[u8]> = Vec::new();
    for part in path.split(|byte| *byte == b'/') {
        if part.is_empty() || part == b"." {
            continue;
        }
        if part != b".." || (initial == 0 && parts.is_empty()) || parts.last() == Some(&&b".."[..]) {
            parts.push(part);
        } else if !parts.is_empty() {
            parts.pop();
        }
    }
    let mut out = vec![b'/'; initial];
    out.extend_from_slice(&parts.join(&b'/'));
    if out.is_empty() {
        b".".to_vec()
    } else {
        out
    }
}

/// `os.path.realpath` (non-strict): resolve the longest existing prefix, append the rest.
fn realpath(path: &Path) -> std::io::Result<PathBuf> {
    let mut existing = path.to_path_buf();
    let mut rest = Vec::new();
    loop {
        match std::fs::canonicalize(&existing) {
            Ok(resolved) => {
                let mut out = resolved;
                for part in rest.iter().rev() {
                    out.push(part);
                }
                return Ok(PathBuf::from(OsStr::from_bytes(&normpath(out.as_os_str().as_bytes()))));
            }
            Err(error) => {
                let (Some(parent), Some(name)) = (existing.parent(), existing.file_name()) else {
                    return Err(error);
                };
                rest.push(name.to_os_string());
                existing = parent.to_path_buf();
            }
        }
    }
}

pub(super) fn volume_admission(path: &Path) -> Option<&'static str> {
    let Ok(bytes) = std::fs::read(MOUNTINFO) else {
        return Some(VOLUME_UNKNOWN);
    };
    // Python reads it as UTF-8 with surrogateescape; mountinfo octal-escapes the bytes that
    // matter (space, tab, newline, backslash), so a lossy decode only differs for mount
    // points that are not valid UTF-8, which never match a valid UTF-8 path either way.
    let mountinfo = String::from_utf8_lossy(&bytes);
    let absolute = abspath(path);
    let parent = absolute.parent().map_or_else(|| absolute.clone(), Path::to_path_buf);
    let Ok(resolved) = realpath(&parent) else {
        return Some(VOLUME_UNKNOWN);
    };
    let resolved = resolved.to_string_lossy();
    classify_filesystem(mount_filesystem_type(&mountinfo, &resolved).as_deref())
}

pub(super) fn entry_attributes(path: &Path) -> std::io::Result<Option<u32>> {
    match rfs::lstat(path) {
        Ok(stat) => Ok(Some(stat.st_mode)),
        Err(error) if error == rustix::io::Errno::NOENT || error == rustix::io::Errno::NOTDIR => Ok(None),
        Err(error) => Err(error.into()),
    }
}

pub(super) fn attributes_supported(attributes: u32) -> bool {
    FileType::from_raw_mode(attributes) != FileType::Symlink
}

pub(super) fn attributes_are_link(attributes: u32) -> bool {
    FileType::from_raw_mode(attributes) == FileType::Symlink
}

/// The parent itself is checked by the caller and pinned by identity until publication; links
/// above it are resolved once by the kernel and cannot redirect the operation later.
pub(super) fn ancestors_admission(parent: &Path) -> std::io::Result<Option<&'static str>> {
    match rfs::stat(parent) {
        Ok(stat) if FileType::from_raw_mode(stat.st_mode) == FileType::Directory => Ok(None),
        Ok(_) => Ok(Some(ANCESTOR_MISSING)),
        Err(error) if error == rustix::io::Errno::NOENT || error == rustix::io::Errno::NOTDIR => {
            Ok(Some(ANCESTOR_MISSING))
        }
        Err(error) => Err(error.into()),
    }
}

pub(super) fn create_exclusive(path: &Path) -> std::io::Result<File> {
    let flags = OFlags::CREATE | OFlags::EXCL | OFlags::RDWR | OFlags::NOFOLLOW | OFlags::CLOEXEC;
    Ok(File::from(rfs::open(path, flags, Mode::from_raw_mode(0o600))?))
}

pub(super) fn handle_identity(file: &File) -> std::io::Result<Identity> {
    let stat = rfs::fstat(file)?;
    Ok((stat.st_dev, stat.st_ino))
}

fn open_regular(path: &Path, flags: OFlags) -> std::io::Result<File> {
    let file = File::from(rfs::open(path, flags, Mode::empty())?);
    if FileType::from_raw_mode(rfs::fstat(&file)?.st_mode) != FileType::RegularFile {
        return Err(std::io::Error::other("not a regular file"));
    }
    Ok(file)
}

pub(super) fn open_write_guard(path: &Path) -> std::io::Result<File> {
    open_regular(path, open_flags(OFlags::RDONLY))
}

pub(super) fn open_content_seal(path: &Path) -> std::io::Result<File> {
    open_regular(path, open_flags(OFlags::RDWR))
}

pub(super) fn open_identity_handle(path: &Path) -> std::io::Result<File> {
    Ok(File::from(rfs::open(path, path_flags(), Mode::empty())?))
}

pub(super) fn open_cleanup_handle(path: &Path) -> std::io::Result<File> {
    Ok(File::from(rfs::open(path, path_flags(), Mode::empty())?))
}

pub(super) fn flush_handle(file: &File) -> bool {
    rfs::fsync(file).is_ok()
}

fn open_directory(path: &Path) -> std::io::Result<File> {
    let path = if path.as_os_str().is_empty() { Path::new(".") } else { path };
    Ok(File::from(rfs::openat(
        CWD,
        path,
        OFlags::RDONLY | OFlags::DIRECTORY | OFlags::CLOEXEC,
        Mode::empty(),
    )?))
}

/// Unlink `path` only while its name still refers to the held file.
pub(super) fn delete_by_handle(file: &File, path: &Path) -> bool {
    let (Some(parent), Some(name)) = (path.parent(), path.file_name()) else {
        return false;
    };
    let Ok(directory) = open_directory(parent) else {
        return false;
    };
    let Ok(named) = rfs::statat(&directory, name, AtFlags::SYMLINK_NOFOLLOW) else {
        return false;
    };
    let Ok(held) = rfs::fstat(file) else {
        return false;
    };
    if (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino) {
        return false;
    }
    rfs::unlinkat(&directory, name, AtFlags::empty()).is_ok()
}

/// Atomically rename within the pinned parent, then make the entry durable.
pub(super) fn publish_replace(source: &Path, target: &Path, parent_identity: Identity) -> Publication {
    let (Some(source_parent), Some(target_parent)) = (source.parent(), target.parent()) else {
        return Publication::NotPublished;
    };
    if abspath(source_parent) != abspath(target_parent) {
        return Publication::NotPublished;
    }
    let (Some(source_name), Some(target_name)) = (source.file_name(), target.file_name()) else {
        return Publication::NotPublished;
    };
    let Ok(directory) = open_directory(target_parent) else {
        return Publication::NotPublished;
    };
    match rfs::fstat(&directory) {
        Ok(held) if (held.st_dev, held.st_ino) == parent_identity => {}
        _ => return Publication::NotPublished,
    }
    if rfs::renameat(&directory, source_name, &directory, target_name).is_err() {
        return Publication::NotPublished;
    }
    if rfs::fsync(&directory).is_err() {
        return Publication::DurabilityUncertain;
    }
    Publication::Published
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normpath_matches_posixpath() {
        let cases: [(&str, &str); 9] = [
            ("/a/./b/../c", "/a/c"),
            ("//a/b", "//a/b"),
            ("///a/b", "/a/b"),
            ("/..", "/"),
            ("a/../..", ".."),
            ("", "."),
            ("/a//b/", "/a/b"),
            ("../x/../y", "../y"),
            ("a/./", "a"),
        ];
        for (input, expected) in cases {
            assert_eq!(normpath(input.as_bytes()), expected.as_bytes(), "{input}");
        }
    }
}
