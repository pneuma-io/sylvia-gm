"""Operating-system file primitives behind database backup and portable export.

Backup and export share one publication sequence: exclusively create a private
temporary file beside the destination, fill and verify it, flush it, then replace
the destination in one atomic step. That sequence and its failure codes live in
``sqlite_authority``; only the primitives it needs from the operating system live
here, behind one small interface with a Windows and a Linux implementation.

Windows keeps its existing handle semantics unchanged: fixed-disk admission,
reparse-point attributes, share modes that deny other writers and deletion,
handle-bound deletion and ``MoveFileExW`` write-through replacement.

Linux has no mandatory share modes, so its "handles" are file descriptors that
pin and re-prove ``(st_dev, st_ino)`` identity. They detect interference rather
than prevent it; the callers' supported precondition (they exclusively manage the
destination directory for the whole call) already excludes it. The Linux
implementation does not imitate Windows flags:

* Volumes are admitted by the mounted filesystem type from ``/proc/self/mountinfo``.
  Only local journalling filesystems (ext4, xfs, btrfs) are admitted; known network
  filesystems are "remote" and everything else, including tmpfs, is "unsupported".
* Every open refuses to follow a symbolic link at the final component.
* Cleanup unlinks a name only after proving, through the pinned parent directory,
  that the name still refers to the claimed file.
* Publication renames inside the pinned parent directory, then fsyncs that
  directory. ``rename(2)`` is atomic but the new directory entry is not durable
  until the directory itself is synced, so a failed directory sync after a
  successful rename is reported as ``DURABILITY_UNCERTAIN``, never as success and
  never as "the old destination is still present".

Any other platform reports every target as lexically unsupported, so backup and
export fail clearly before touching the filesystem.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from typing import Any

__all__ = (
    "LOCAL_FILESYSTEMS",
    "Publication",
    "SafeFiles",
    "classify_filesystem",
    "files",
    "mount_filesystem_type",
)


class Publication(StrEnum):
    """Outcome of the final atomic replacement of a destination."""

    PUBLISHED = "published"
    NOT_PUBLISHED = "not_published"
    DURABILITY_UNCERTAIN = "durability_uncertain"


# Volume admission results other than None (admitted).
VOLUME_UNKNOWN = "unknown"
VOLUME_REMOVABLE = "removable"
VOLUME_REMOTE = "remote"
VOLUME_UNSUPPORTED = "unsupported"

# Ancestor admission results other than None (admitted).
ANCESTOR_MISSING = "ancestor_missing"
ANCESTOR_ATTRIBUTES_UNSUPPORTED = "attributes_unsupported"

LOCAL_FILESYSTEMS = frozenset({"ext4", "xfs", "btrfs"})
_REMOTE_FILESYSTEMS = frozenset(
    {
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
    }
)
_MOUNTINFO_ESCAPE = re.compile(r"\\([0-7]{3})")


def _unescape_mount_field(value: str) -> str:
    return _MOUNTINFO_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), value)


def mount_filesystem_type(mountinfo: str, resolved_path: str) -> str | None:
    """Return the filesystem type of the mount containing an absolute resolved path.

    The longest matching mount point wins; among equal mount points the later line
    wins, because a later mount shadows an earlier one at the same place.
    """

    best_length = -1
    best_type: str | None = None
    for line in mountinfo.splitlines():
        fields = line.split(" ")
        try:
            separator = fields.index("-", 6)
            mount_point = _unescape_mount_field(fields[4])
            filesystem_type = fields[separator + 1]
        except (ValueError, IndexError):
            continue
        if mount_point == "/":
            matches = resolved_path.startswith("/")
        else:
            matches = resolved_path == mount_point or resolved_path.startswith(
                mount_point.rstrip("/") + "/"
            )
        if matches and len(mount_point) >= best_length:
            best_length = len(mount_point)
            best_type = filesystem_type
    return best_type


def classify_filesystem(filesystem_type: str | None) -> str | None:
    """Admit a local journalling filesystem; otherwise name why it is refused."""

    if filesystem_type is None:
        return VOLUME_UNKNOWN
    if filesystem_type in LOCAL_FILESYSTEMS:
        return None
    if filesystem_type in _REMOTE_FILESYSTEMS:
        return VOLUME_REMOTE
    return VOLUME_UNSUPPORTED


class SafeFiles:
    """Platform primitives used by backup and export publication.

    Handles are opaque integers. Every ``open_*`` raises ``FileNotFoundError`` when
    the path is absent and ``OSError`` for any other refusal. Attributes are opaque
    integers compared only for equality and passed back to this object.
    """

    name = "unsupported"

    def lexical_backup_target_supported(self, path: Path) -> bool:
        return False

    def lexical_export_target_supported(self, path: Path) -> bool:
        return False

    def volume_admission(self, path: Path) -> str | None:
        return VOLUME_UNSUPPORTED

    def entry_attributes(self, path: Path) -> int | None:
        raise OSError("safe file operations are unsupported on this platform")

    def attributes_supported(self, attributes: int) -> bool:
        return False

    def attributes_are_link(self, attributes: int) -> bool:
        return False

    def ancestors_admission(self, parent: Path) -> str | None:
        return ANCESTOR_ATTRIBUTES_UNSUPPORTED

    def create_exclusive(self, path: Path) -> int:
        raise OSError("safe file operations are unsupported on this platform")

    def descriptor_identity(self, descriptor: int) -> tuple[int, int]:
        raise OSError("safe file operations are unsupported on this platform")

    def open_write_guard(self, path: Path) -> int:
        raise OSError("safe file operations are unsupported on this platform")

    def open_content_seal(self, path: Path) -> int:
        raise OSError("safe file operations are unsupported on this platform")

    def open_identity_handle(self, path: Path) -> int:
        raise OSError("safe file operations are unsupported on this platform")

    def open_cleanup_handle(self, path: Path) -> int:
        raise OSError("safe file operations are unsupported on this platform")

    def handle_identity(self, handle: int) -> tuple[int, int]:
        raise OSError("safe file operations are unsupported on this platform")

    def flush_handle(self, handle: int) -> bool:
        return False

    def close_handle(self, handle: int) -> bool:
        return False

    def delete_by_handle(self, handle: int, path: Path) -> bool:
        return False

    def publish_replace(
        self,
        source: Path,
        target: Path,
        *,
        parent_identity: tuple[int, int],
    ) -> Publication:
        return Publication.NOT_PUBLISHED


if sys.platform == "win32":

    class _WindowsFiles(SafeFiles):
        """The established Windows behaviour, moved here unchanged."""

        name = "windows"

        _MOVEFILE_REPLACE_EXISTING = 0x00000001
        _MOVEFILE_WRITE_THROUGH = 0x00000008
        _DRIVE_UNKNOWN = 0
        _DRIVE_REMOVABLE = 2
        _DRIVE_FIXED = 3
        _DRIVE_REMOTE = 4
        _FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
        _UNSUPPORTED_ATTRIBUTES = (
            0x00000400  # reparse point
            | 0x00001000  # offline
            | 0x00040000  # recall on open
            | 0x00400000  # recall on data access
        )
        _INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
        _GENERIC_READ = 0x80000000
        _GENERIC_WRITE = 0x40000000
        _DELETE_ACCESS = 0x00010000
        _FILE_READ_ATTRIBUTES = 0x00000080
        _FILE_SHARE_READ = 0x00000001
        _FILE_SHARE_WRITE = 0x00000002
        _FILE_SHARE_DELETE = 0x00000004
        _OPEN_EXISTING = 3
        _FILE_ATTRIBUTE_NORMAL = 0x00000080
        _FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
        _FILE_DISPOSITION_INFO = 4
        _NOT_FOUND_ERRORS = frozenset({2, 3})

        def __init__(self) -> None:
            import ctypes

            self._ctypes = ctypes
            self._invalid_handle = int(ctypes.c_void_p(-1).value or -1)

        def _kernel32(self) -> Any:
            return self._ctypes.WinDLL("kernel32", use_last_error=True)

        def lexical_backup_target_supported(self, path: Path) -> bool:
            normalized = Path(os.path.abspath(os.fspath(path)))
            text = os.fspath(normalized)
            return not (text.startswith(("\\\\", "//")) or ":" in normalized.name)

        def lexical_export_target_supported(self, path: Path) -> bool:
            if os.fspath(path).startswith(("\\\\", "//", "\\?\\", "\\.\\")):
                return False
            return ":" not in Path(os.path.abspath(os.fspath(path))).name

        def volume_admission(self, path: Path) -> str | None:
            volume_root = Path(os.path.abspath(os.fspath(path))).anchor
            if not volume_root:
                return VOLUME_UNKNOWN
            get_drive_type: Any = self._kernel32().GetDriveTypeW
            get_drive_type.argtypes = [self._ctypes.c_wchar_p]
            get_drive_type.restype = self._ctypes.c_uint
            drive_type = int(get_drive_type(volume_root))
            if drive_type == self._DRIVE_FIXED:
                return None
            return {
                self._DRIVE_UNKNOWN: VOLUME_UNKNOWN,
                self._DRIVE_REMOVABLE: VOLUME_REMOVABLE,
                self._DRIVE_REMOTE: VOLUME_REMOTE,
            }.get(drive_type, VOLUME_UNSUPPORTED)

        def entry_attributes(self, path: Path) -> int | None:
            get_attributes: Any = self._kernel32().GetFileAttributesW
            get_attributes.argtypes = [self._ctypes.c_wchar_p]
            get_attributes.restype = self._ctypes.c_uint32
            attributes = int(get_attributes(os.fspath(path)))
            if attributes == self._INVALID_FILE_ATTRIBUTES:
                error = self._ctypes.get_last_error()
                if error in self._NOT_FOUND_ERRORS:
                    return None
                raise OSError(error)
            return attributes

        def attributes_supported(self, attributes: int) -> bool:
            return not attributes & self._UNSUPPORTED_ATTRIBUTES

        def attributes_are_link(self, attributes: int) -> bool:
            return bool(attributes & self._FILE_ATTRIBUTE_REPARSE_POINT)

        def ancestors_admission(self, parent: Path) -> str | None:
            current = parent
            while True:
                attributes = self.entry_attributes(current)
                if attributes is None:
                    return ANCESTOR_MISSING
                if not self.attributes_supported(attributes):
                    return ANCESTOR_ATTRIBUTES_UNSUPPORTED
                if current.parent == current:
                    return None
                current = current.parent

        def create_exclusive(self, path: Path) -> int:
            flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0)
            return os.open(path, flags, 0o600)

        def descriptor_identity(self, descriptor: int) -> tuple[int, int]:
            import msvcrt

            return self.handle_identity(int(msvcrt.get_osfhandle(descriptor)))

        def _create_handle(self, path: Path, access: int, share: int) -> int:
            create_file: Any = self._kernel32().CreateFileW
            c = self._ctypes
            create_file.argtypes = [
                c.c_wchar_p,
                c.c_uint32,
                c.c_uint32,
                c.c_void_p,
                c.c_uint32,
                c.c_uint32,
                c.c_void_p,
            ]
            create_file.restype = c.c_void_p
            handle = create_file(
                os.fspath(path),
                access,
                share,
                None,
                self._OPEN_EXISTING,
                self._FILE_ATTRIBUTE_NORMAL | self._FILE_FLAG_OPEN_REPARSE_POINT,
                None,
            )
            value = int(handle) if handle is not None else self._invalid_handle
            if value == self._invalid_handle:
                error = c.get_last_error()
                if error in self._NOT_FOUND_ERRORS:
                    raise FileNotFoundError(error, "file not found")
                raise OSError(error)
            return value

        def open_write_guard(self, path: Path) -> int:
            return self._create_handle(
                path, self._FILE_READ_ATTRIBUTES, self._FILE_SHARE_READ | self._FILE_SHARE_WRITE
            )

        def open_content_seal(self, path: Path) -> int:
            return self._create_handle(
                path, self._GENERIC_READ | self._GENERIC_WRITE, self._FILE_SHARE_READ
            )

        def open_identity_handle(self, path: Path) -> int:
            return self._create_handle(
                path,
                self._FILE_READ_ATTRIBUTES,
                self._FILE_SHARE_READ | self._FILE_SHARE_WRITE | self._FILE_SHARE_DELETE,
            )

        def open_cleanup_handle(self, path: Path) -> int:
            return self._create_handle(
                path,
                self._DELETE_ACCESS | self._FILE_READ_ATTRIBUTES,
                self._FILE_SHARE_READ | self._FILE_SHARE_WRITE | self._FILE_SHARE_DELETE,
            )

        def handle_identity(self, handle: int) -> tuple[int, int]:
            c = self._ctypes
            get_information: Any = self._kernel32().GetFileInformationByHandle
            get_information.argtypes = [c.c_void_p, c.c_void_p]
            get_information.restype = c.c_int
            information = c.create_string_buffer(52)
            if not get_information(c.c_void_p(handle), information):
                raise OSError(c.get_last_error())
            volume_serial = int(c.c_uint32.from_buffer(information, 28).value)
            file_index_high = int(c.c_uint32.from_buffer(information, 44).value)
            file_index_low = int(c.c_uint32.from_buffer(information, 48).value)
            return volume_serial, (file_index_high << 32) | file_index_low

        def flush_handle(self, handle: int) -> bool:
            flush: Any = self._kernel32().FlushFileBuffers
            flush.argtypes = [self._ctypes.c_void_p]
            flush.restype = self._ctypes.c_int
            return bool(flush(self._ctypes.c_void_p(handle)))

        def close_handle(self, handle: int) -> bool:
            close: Any = self._kernel32().CloseHandle
            close.argtypes = [self._ctypes.c_void_p]
            close.restype = self._ctypes.c_int
            return bool(close(self._ctypes.c_void_p(handle)))

        def delete_by_handle(self, handle: int, path: Path) -> bool:
            c = self._ctypes
            set_information: Any = self._kernel32().SetFileInformationByHandle
            set_information.argtypes = [c.c_void_p, c.c_int, c.c_void_p, c.c_uint32]
            set_information.restype = c.c_int
            delete_file = c.c_int(1)
            return bool(
                set_information(
                    c.c_void_p(handle),
                    self._FILE_DISPOSITION_INFO,
                    c.byref(delete_file),
                    c.sizeof(delete_file),
                )
            )

        def publish_replace(
            self,
            source: Path,
            target: Path,
            *,
            parent_identity: tuple[int, int],
        ) -> Publication:
            c = self._ctypes
            move: Any = self._kernel32().MoveFileExW
            move.argtypes = [c.c_wchar_p, c.c_wchar_p, c.c_uint32]
            move.restype = c.c_int
            flags = self._MOVEFILE_REPLACE_EXISTING | self._MOVEFILE_WRITE_THROUGH
            if move(os.fspath(source), os.fspath(target), flags):
                return Publication.PUBLISHED
            return Publication.NOT_PUBLISHED


if sys.platform.startswith("linux"):

    class _LinuxFiles(SafeFiles):
        """Descriptor-based primitives for local Linux filesystems."""

        name = "linux"

        _MOUNTINFO = Path("/proc/self/mountinfo")

        def _open_flags(self, access: int) -> int:
            return access | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK

        def _path_flags(self) -> int:
            # O_PATH|O_NOFOLLOW names the final component itself, link or not, without
            # needing read permission, like the Windows identity handle that opens a
            # reparse point rather than its target.
            return os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC

        def lexical_backup_target_supported(self, path: Path) -> bool:
            return "\0" not in os.fspath(path)

        def lexical_export_target_supported(self, path: Path) -> bool:
            return "\0" not in os.fspath(path)

        def _read_mountinfo(self) -> str:
            return self._MOUNTINFO.read_text(encoding="utf-8", errors="surrogateescape")

        def volume_admission(self, path: Path) -> str | None:
            try:
                mountinfo = self._read_mountinfo()
                resolved = os.path.realpath(Path(os.path.abspath(os.fspath(path))).parent)
            except OSError:
                return VOLUME_UNKNOWN
            return classify_filesystem(mount_filesystem_type(mountinfo, resolved))

        def entry_attributes(self, path: Path) -> int | None:
            try:
                return int(os.lstat(path).st_mode)
            except (FileNotFoundError, NotADirectoryError):
                return None

        def attributes_supported(self, attributes: int) -> bool:
            return not stat.S_ISLNK(attributes)

        def attributes_are_link(self, attributes: int) -> bool:
            return stat.S_ISLNK(attributes)

        def ancestors_admission(self, parent: Path) -> str | None:
            # The parent itself is checked by the caller and pinned by identity until
            # publication, which renames inside that pinned directory. Links above it
            # are resolved once by the kernel and cannot redirect the operation later.
            try:
                parent_stat = os.stat(parent)
            except (FileNotFoundError, NotADirectoryError):
                return ANCESTOR_MISSING
            if not stat.S_ISDIR(parent_stat.st_mode):
                return ANCESTOR_MISSING
            return None

        def create_exclusive(self, path: Path) -> int:
            flags = os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
            return os.open(path, flags, 0o600)

        def descriptor_identity(self, descriptor: int) -> tuple[int, int]:
            value = os.fstat(descriptor)
            return int(value.st_dev), int(value.st_ino)

        def _open_regular(self, path: Path, flags: int) -> int:
            descriptor = os.open(path, flags)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise OSError("not a regular file")
            except BaseException:
                os.close(descriptor)
                raise
            return descriptor

        def open_write_guard(self, path: Path) -> int:
            return self._open_regular(path, self._open_flags(os.O_RDONLY))

        def open_content_seal(self, path: Path) -> int:
            return self._open_regular(path, self._open_flags(os.O_RDWR))

        def open_identity_handle(self, path: Path) -> int:
            return os.open(path, self._path_flags())

        def open_cleanup_handle(self, path: Path) -> int:
            return os.open(path, self._path_flags())

        def handle_identity(self, handle: int) -> tuple[int, int]:
            return self.descriptor_identity(handle)

        def flush_handle(self, handle: int) -> bool:
            try:
                os.fsync(handle)
            except OSError:
                return False
            return True

        def close_handle(self, handle: int) -> bool:
            try:
                os.close(handle)
            except OSError:
                return False
            return True

        def _open_directory(self, path: Path) -> int:
            return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

        def _sync_directory(self, directory: int) -> None:
            os.fsync(directory)

        def delete_by_handle(self, handle: int, path: Path) -> bool:
            """Unlink ``path`` only while its name still refers to the held file."""

            directory = self._open_directory(path.parent)
            try:
                named = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
                held = os.fstat(handle)
                if (named.st_dev, named.st_ino) != (held.st_dev, held.st_ino):
                    return False
                os.unlink(path.name, dir_fd=directory)
                return True
            finally:
                with suppress(OSError):
                    os.close(directory)

        def publish_replace(
            self,
            source: Path,
            target: Path,
            *,
            parent_identity: tuple[int, int],
        ) -> Publication:
            """Atomically rename within the pinned parent, then make the entry durable."""

            source_parent = os.path.abspath(os.fspath(source.parent))
            if source_parent != os.path.abspath(os.fspath(target.parent)):
                return Publication.NOT_PUBLISHED
            try:
                directory = self._open_directory(target.parent)
            except OSError:
                return Publication.NOT_PUBLISHED
            try:
                held = os.fstat(directory)
                if (int(held.st_dev), int(held.st_ino)) != parent_identity:
                    return Publication.NOT_PUBLISHED
                try:
                    os.replace(
                        source.name,
                        target.name,
                        src_dir_fd=directory,
                        dst_dir_fd=directory,
                    )
                except OSError:
                    return Publication.NOT_PUBLISHED
                try:
                    self._sync_directory(directory)
                except OSError:
                    return Publication.DURABILITY_UNCERTAIN
                return Publication.PUBLISHED
            finally:
                with suppress(OSError):
                    os.close(directory)


def _select() -> SafeFiles:
    if sys.platform == "win32":
        return _WindowsFiles()
    if sys.platform.startswith("linux"):
        return _LinuxFiles()
    return SafeFiles()


files: SafeFiles = _select()
