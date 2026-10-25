"""Update-owned staging and small durable receipts, never campaign or credential storage."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from .manifest import MANIFEST_LIMIT, UpdateError, bounded_json, canonical_bytes

PHASES = frozenset(
    {
        "checking",
        "available",
        "current",
        "downloading",
        "verified",
        "deferred",
        "cancelled",
        "interrupted",
        "error",
        "preparing",
        "prepared",
        "closing",
        "applying",
        "awaiting_restart",
        "recovery_required",
        "rolling_back",
        "rollback_ready",
        "rollback_awaiting_restart",
        "confirmed",
        "rolled_back",
    }
)
EMPTY_TERMINAL_PHASES = frozenset({"current", "error", "cancelled", "interrupted"})


def operation_id(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{32}", value) is None:
        raise UpdateError("invalid_operation", "The update operation is no longer available.")
    return value


def directory_identity(path: Path) -> tuple[int, int]:
    """Refuse aliases and reparse ancestors before using an application-owned directory."""
    from sylvia_gm.builder._package_materializer import _reject_reparse_chain

    try:
        if path.resolve(strict=True) != path.absolute() or not path.is_dir():
            raise ValueError("Invalid staging directory")
        _reject_reparse_chain(path, path)
        for parent in path.parents:
            _reject_reparse_chain(parent, parent)
        value = path.stat()
        return value.st_dev, value.st_ino
    except (OSError, ValueError) as error:
        raise UpdateError("unsafe_staging", "The update staging folder is unsafe.") from error


def checked_file(path: Path, *, maximum: int) -> bytes:
    directory_identity(path.parent)
    try:
        value = path.lstat()
        if (
            path.is_symlink()
            or not path.is_file()
            or value.st_nlink != 1
            or getattr(value, "st_file_attributes", 0) & 0x400
            or not 0 < value.st_size <= maximum
        ):
            raise ValueError("Invalid update file")
        with path.open("rb") as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (value.st_dev, value.st_ino):
                raise ValueError("Changed update file")
            raw = stream.read(maximum + 1)
        after = path.lstat()
        if len(raw) != value.st_size or (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) != (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns):
            raise ValueError("Changed update file")
        return raw
    except (OSError, ValueError) as error:
        raise UpdateError(
            "invalid_staging", "Stored update information could not be verified."
        ) from error


def ensure_directory(path: Path) -> None:
    """Check existing ancestry before creating each missing directory, never through an alias."""
    if not path.is_absolute() or ".." in path.parts:
        raise UpdateError("unsafe_staging", "An absolute update-owned folder is required.")
    missing: list[Path] = []
    ancestor = path
    while not ancestor.exists() and not ancestor.is_symlink():
        missing.append(ancestor)
        ancestor = ancestor.parent
    directory_identity(ancestor)
    for child in reversed(missing):
        parent = directory_identity(child.parent)
        child.mkdir(exist_ok=True)
        if directory_identity(child.parent) != parent:
            raise UpdateError("unsafe_staging", "The update staging parent changed.")
        directory_identity(child)


def atomic_receipt(path: Path, value: object) -> None:
    identity = directory_identity(path.parent)
    raw = canonical_bytes(value)
    if len(raw) > MANIFEST_LIMIT:
        raise UpdateError("metadata_limit", "Stored update information is too large.")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            if stream.write(raw) != len(raw):
                raise OSError("Incomplete update journal write")
            stream.flush()
            os.fsync(stream.fileno())
        if directory_identity(path.parent) != identity or path.is_symlink():
            raise UpdateError("unsafe_staging", "The update staging folder changed.")
        # Replacing the receipt replaces a directory entry; it never truncates an existing link.
        os.replace(temporary, path)
    except OSError as error:
        raise UpdateError(
            "journal_failed", "The update receipt could not be saved. No apply was started."
        ) from error


class UpdateStore:
    """One active receipt with separately retained operation directories; no automatic deletion."""

    def __init__(self, root: Path) -> None:
        self.root = root
        directory_identity(root.parent)
        root.mkdir(exist_ok=True)
        self.identity = directory_identity(root)

    def _check(self) -> None:
        if directory_identity(self.root) != self.identity:
            raise UpdateError("unsafe_staging", "The update staging folder changed.")

    def stage(self, identity: str) -> Path:
        self._check()
        stage = self.root / operation_id(identity)
        directory_identity(stage)
        return stage

    def create(self) -> tuple[str, Path]:
        self._check()
        # Retained interruptions are never silently deleted to make space for another download.
        if sum(1 for _ in self.root.iterdir()) >= 65:
            raise UpdateError(
                "staging_limit", "Retained update files need review before another download."
            )
        identity = uuid.uuid4().hex
        stage = self.root / identity
        stage.mkdir()
        directory_identity(stage)
        return identity, stage

    def save(
        self, identity: str, phase: str, version: str | None, error_code: str | None = None
    ) -> None:
        self.stage(identity)
        if phase not in PHASES or (
            error_code is not None and re.fullmatch(r"[a-z_]{1,64}", error_code) is None
        ):
            raise UpdateError("invalid_journal", "The update state cannot be recorded.")
        atomic_receipt(
            self.root / "current.json",
            {
                "format_version": 1,
                "operation_id": identity,
                "phase": phase,
                "version": version,
                "error_code": error_code,
            },
        )

    def load(self) -> dict[str, object] | None:
        self._check()
        path = self.root / "current.json"
        if not path.exists() and not path.is_symlink():
            return None
        row = bounded_json(checked_file(path, maximum=16 * 1024), maximum=16 * 1024)
        if (
            not isinstance(row, dict)
            or set(row) != {"format_version", "operation_id", "phase", "version", "error_code"}
            or type(row["format_version"]) is not int
            or row["format_version"] != 1
            or not isinstance(row["phase"], str)
            or row["phase"] not in PHASES
            or (row["version"] is not None and not isinstance(row["version"], str))
            or (
                row["error_code"] is not None
                and (
                    not isinstance(row["error_code"], str)
                    or re.fullmatch(r"[a-z_]{1,64}", row["error_code"]) is None
                )
            )
        ):
            raise UpdateError(
                "invalid_journal", "The update receipt is invalid; recovery needs review."
            )
        stage = self.root / operation_id(row["operation_id"])
        if row["phase"] not in EMPTY_TERMINAL_PHASES or stage.exists() or stage.is_symlink():
            self.stage(str(row["operation_id"]))
        return row

    def prune_empty_terminal(self, identity: str) -> None:
        """Remove only this operation's empty terminal directory, never any retained bytes."""
        row = self.load()
        if (row is None or row["operation_id"] != operation_id(identity)
                or row["phase"] not in EMPTY_TERMINAL_PHASES):
            return
        stage = self.root / identity
        if not stage.exists() and not stage.is_symlink():
            return
        self.stage(identity)
        try:
            # Nonrecursive: partials, manifests and recovery stay intact.
            stage.rmdir()
        except OSError:
            return
