"""Resumable portable file replacement, used only after the native process barrier."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import uuid
from collections.abc import Callable
from pathlib import Path

from .manifest import (
    ARTIFACT_LIMIT,
    MANIFEST_LIMIT,
    Manifest,
    PayloadFile,
    UpdateError,
    bounded_json,
    payload_path,
)
from .storage import atomic_receipt, checked_file, directory_identity, ensure_directory


def file_matches(path: Path, row: PayloadFile) -> bool:
    """Match one regular, unaliased application file without loading its full contents."""
    if not path.parent.exists() and not path.parent.is_symlink():
        ancestor = path.parent
        while not ancestor.exists() and not ancestor.is_symlink():
            ancestor = ancestor.parent
        directory_identity(ancestor)
        return False
    directory_identity(path.parent)
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or getattr(before, "st_file_attributes", 0) & 0x400
            or before.st_size != row.size
        ):
            return False
        digest, count = hashlib.sha256(), 0
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                return False
            while chunk := stream.read(256 * 1024):
                count += len(chunk)
                if count > row.size:
                    return False
                digest.update(chunk)
        after = path.lstat()
        return (
            count == row.size
            and digest.hexdigest() == row.sha256
            and (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            == (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        )
    except FileNotFoundError:
        return False


def _require_file(path: Path, row: PayloadFile) -> None:
    if not file_matches(path, row):
        raise UpdateError(
            "application_changed", "Application files changed; keep this copy open for repair."
        )


def _copy(source: Path, destination: Path, row: PayloadFile) -> None:
    _require_file(source, row)
    ensure_directory(destination.parent)
    directory_identity(destination.parent)
    with source.open("rb") as incoming, destination.open("xb") as output:
        shutil.copyfileobj(incoming, output, 256 * 1024)
        output.flush()
        os.fsync(output.fileno())
    _require_file(destination, row)
    _require_file(source, row)


def _inventory(root: Path) -> tuple[PayloadFile, ...]:
    from sylvia_gm.portable_artifact import (
        ENTRY_MANIFEST,
        REQUIRED_PAYLOADS,
        _validate_payload_path,
    )

    raw = checked_file(root / ENTRY_MANIFEST, maximum=MANIFEST_LIMIT)
    entries = bounded_json(raw)
    if not isinstance(entries, dict) or not 3 <= len(entries) <= 4096:
        raise UpdateError(
            "invalid_installation", "This portable copy has no usable application inventory."
        )
    files: list[PayloadFile] = []
    names: set[str] = set()
    for name, record in entries.items():
        name = payload_path(name)
        _validate_payload_path(name, metadata=True)
        if (
            name.casefold() in names
            or name == ENTRY_MANIFEST
            or not isinstance(record, dict)
            or set(record) != {"bytes", "sha256"}
            or type(record["bytes"]) is not int
            or not 0 < record["bytes"] <= ARTIFACT_LIMIT
            or not isinstance(record["sha256"], str)
            or len(record["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in record["sha256"])
        ):
            raise UpdateError(
                "invalid_installation", "The current application inventory is invalid."
            )
        names.add(name.casefold())
        files.append(PayloadFile(name, record["bytes"], record["sha256"]))
    if not entries.keys() >= REQUIRED_PAYLOADS:
        raise UpdateError("invalid_installation", "The current portable copy is incomplete.")
    files.append(PayloadFile(ENTRY_MANIFEST, len(raw), hashlib.sha256(raw).hexdigest()))
    for row in files:
        _require_file(root / row.path, row)
    marker = bounded_json(checked_file(root / "sylvia-portable.json", maximum=128), maximum=128)
    if marker != {"version": 1} or type(marker["version"]) is not int:
        raise UpdateError("wrong_distribution", "The current portable marker is incompatible.")
    return tuple(sorted(files, key=lambda item: item.path))


class PortableReplacement:
    """Retain old files first; reconcile each atomic replacement by its exact byte identity."""

    def __init__(self, live: Path, operation: Path, manifest: Manifest) -> None:
        if manifest.distribution != "portable":
            raise UpdateError(
                "wrong_distribution", "An installed update cannot replace a portable copy."
            )
        self.live, self.operation, self.manifest = live, operation, manifest
        self._live_identity = directory_identity(live)
        self._operation_identity = directory_identity(operation)
        if live == operation or live.is_relative_to(operation):
            raise UpdateError(
                "unsafe_staging", "Application and update staging must be separate trees."
            )
        self.prepared = operation / "prepared"
        self.previous = operation / "previous"
        self._journal = operation / "replacement.json"
        self._old: tuple[PayloadFile, ...] = ()

    def _directories(self) -> None:
        if (
            directory_identity(self.live) != self._live_identity
            or directory_identity(self.operation) != self._operation_identity
        ):
            raise UpdateError("unsafe_staging", "The application or update folder changed.")

    def _save(self, phase: str) -> None:
        self._directories()
        atomic_receipt(
            self._journal,
            {
                "format_version": 1,
                "phase": phase,
                "version": self.manifest.version.text,
                "old": [
                    {"path": row.path, "size": row.size, "sha256": row.sha256} for row in self._old
                ],
            },
        )

    def prepare(self) -> None:
        """Retain a verified previous tree; this step never alters the application or its data."""
        self._directories()
        if self._journal.exists() or self.previous.exists():
            raise UpdateError("staging_exists", "Previous replacement state needs reconciliation.")
        self._old = _inventory(self.live)
        old_names = {row.path for row in self._old}
        for row in self.manifest.files:
            _require_file(self.prepared / row.path, row)
            target = self.live / row.path
            if row.path not in old_names and (target.exists() or target.is_symlink()):
                raise UpdateError(
                    "unowned_file",
                    "An update would overwrite a file outside the application inventory.",
                )
        needed = sum(row.size for row in self._old)
        if shutil.disk_usage(self.operation).free < needed + 16 * 1024 * 1024:
            raise UpdateError("disk_full", "There is not enough space to retain the previous app.")
        self.previous.mkdir()
        self._save("retaining")
        for row in self._old:
            _copy(self.live / row.path, self.previous / row.path, row)
        self._save("prepared")

    def _restore_receipt(self) -> str:
        self._directories()
        row = bounded_json(checked_file(self._journal, maximum=MANIFEST_LIMIT))
        if (
            not isinstance(row, dict)
            or set(row) != {"format_version", "phase", "version", "old"}
            or type(row["format_version"]) is not int
            or row["format_version"] != 1
            or row["version"] != self.manifest.version.text
            or row["phase"]
            not in (
                "prepared",
                "applying",
                "awaiting_restart",
                "rolling_back",
                "rolled_back",
                "confirmed",
            )
            or not isinstance(row["old"], list)
            or not 3 <= len(row["old"]) <= 4097
        ):
            raise UpdateError(
                "invalid_journal", "The replacement receipt needs manual reconciliation."
            )
        files: list[PayloadFile] = []
        for entry in row["old"]:
            if (
                not isinstance(entry, dict)
                or set(entry) != {"path", "size", "sha256"}
                or type(entry["size"]) is not int
                or not 0 < entry["size"] <= ARTIFACT_LIMIT
                or not isinstance(entry["sha256"], str)
                or len(entry["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in entry["sha256"])
            ):
                raise UpdateError(
                    "invalid_journal", "The retained application inventory is invalid."
                )
            files.append(PayloadFile(payload_path(entry["path"]), entry["size"], entry["sha256"]))
        if len({entry.path.casefold() for entry in files}) != len(files):
            raise UpdateError("invalid_journal", "The retained application inventory is ambiguous.")
        self._old = tuple(files)
        # The recipe's inventory and hash manifest remain the recovery authority.
        if self._old != _inventory(self.previous):
            raise UpdateError(
                "invalid_journal", "The retained application does not match its receipt."
            )
        return str(row["phase"])

    def _barrier(self, processes_exited: Callable[[], bool]) -> None:
        self._directories()
        if not processes_exited():
            raise UpdateError(
                "application_busy", "Close all owned Sylvia processes before applying the update."
            )

    def apply(self, processes_exited: Callable[[], bool]) -> None:
        """The native helper supplies retained process-handle checks, never renderer booleans."""
        self._barrier(processes_exited)
        phase = self._restore_receipt()
        if phase not in {"prepared", "applying", "awaiting_restart"}:
            raise UpdateError(
                "invalid_phase", "This replacement cannot be applied in its current state."
            )
        old = {row.path: row for row in self._old}
        new = {row.path: row for row in self.manifest.files}
        self._save("applying")
        for row in self.manifest.files:
            self._barrier(processes_exited)
            target = self.live / row.path
            ensure_directory(target.parent)
            if file_matches(target, row):
                continue
            if row.path in old:
                _require_file(target, old[row.path])
            elif target.exists() or target.is_symlink():
                raise UpdateError("unowned_file", "A new application path is no longer empty.")
            source = self.prepared / row.path
            _require_file(source, row)
            directory_identity(target.parent)
            os.replace(source, target)
            _require_file(target, row)
        for name, row in old.items():
            if name in new:
                continue
            self._barrier(processes_exited)
            target = self.live / name
            retired = self.operation / "retired" / name
            if not target.exists() and file_matches(retired, row):
                continue
            _require_file(target, row)
            ensure_directory(retired.parent)
            directory_identity(retired.parent)
            if retired.exists() or retired.is_symlink():
                raise UpdateError(
                    "staging_exists", "An obsolete-file receipt needs reconciliation."
                )
            os.replace(target, retired)
        for row in self.manifest.files:
            _require_file(self.live / row.path, row)
        self._save("awaiting_restart")

    def rollback(
        self, processes_exited: Callable[[], bool], check_data_compatibility: Callable[[], None]
    ) -> None:
        """Require the native checkpoint decision to admit compatibility or explicit restore."""
        self._barrier(processes_exited)
        phase = self._restore_receipt()
        check_data_compatibility()
        old = {row.path: row for row in self._old}
        new = {row.path: row for row in self.manifest.files}
        # A completed apply consumed every staged source. An absent new-only file at
        # that point is a later change, not an interrupted move we may silently ignore.
        if phase in {"awaiting_restart", "confirmed"}:
            for name, row in new.items():
                if name not in old:
                    _require_file(self.live / name, row)
        self._save("rolling_back")
        for row in self._old:
            self._barrier(processes_exited)
            target = self.live / row.path
            ensure_directory(target.parent)
            if file_matches(target, row):
                continue
            if target.exists() or target.is_symlink():
                if row.path not in new:
                    raise UpdateError("application_changed", "A retained application path changed.")
                _require_file(target, new[row.path])
            temporary = self.operation / "restoring" / uuid.uuid4().hex / row.path
            _copy(self.previous / row.path, temporary, row)
            os.replace(temporary, target)
        for name, row in new.items():
            if name in old:
                continue
            self._barrier(processes_exited)
            target = self.live / name
            retained = self.operation / "failed-new" / name
            if not target.exists() and not target.is_symlink():
                if file_matches(retained, row):
                    continue
                # os.replace consumes the verified source. Its exact remaining bytes
                # prove this new-only path was never installed during partial apply.
                if file_matches(self.prepared / name, row):
                    continue
            _require_file(target, row)
            ensure_directory(retained.parent)
            directory_identity(retained.parent)
            os.replace(target, retained)
        for row in self._old:
            _require_file(self.live / row.path, row)
        self._save("rolled_back")

    def confirm(self) -> None:
        """Record a separately verified startup without changing application or user files."""
        phase = self._restore_receipt()
        if phase not in {"awaiting_restart", "confirmed"}:
            raise UpdateError(
                "invalid_phase", "This replacement has not reached restart confirmation."
            )
        self._save("confirmed")

    def verify_rollback(self) -> None:
        """Confirm the retained old inventory, including absence of new-only live files."""
        if self._restore_receipt() != "rolled_back":
            raise UpdateError("recovery_incomplete", "Application recovery has not finished.")
        old = {row.path for row in self._old}
        for row in self._old:
            _require_file(self.live / row.path, row)
        for row in self.manifest.files:
            path = self.live / row.path
            if row.path not in old and (path.exists() or path.is_symlink()):
                raise UpdateError("application_changed", "A recovered application path changed.")

    def helper_payload(self) -> tuple[Path, PayloadFile]:
        if not self._old:
            self._restore_receipt()
        self._directories()
        row = next((row for row in self._old if row.path == "sylvia-gm-owner.exe"), None)
        if row is None:
            raise UpdateError("invalid_installation", "The retained helper is missing.")
        return self.previous / row.path, row
