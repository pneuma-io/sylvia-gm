"""Consistent authority backup and explicit, resumable pre-update data recovery."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .identity import STORAGE_COMPATIBILITY
from .manifest import (
    MANIFEST_LIMIT,
    PayloadFile,
    UpdateError,
    Version,
    bounded_json,
    canonical_bytes,
)
from .replacement import _copy, file_matches
from .storage import (
    atomic_receipt,
    checked_file,
    directory_identity,
    ensure_directory,
    operation_id,
)

RESTORE_CONFIRMATION = "Restore pre-update data"

if TYPE_CHECKING:
    from sylvia_gm.infrastructure.owner_lock import DatabaseOwnerLock


def acquire_update_data_lock(database: Path) -> DatabaseOwnerLock:
    """Refuse an aliased update lock before the authority lock may create or append a byte."""
    from sylvia_gm.infrastructure.owner_lock import DatabaseOwnerLock, authority_lock_identity

    directory_identity(database.parent)
    identity = authority_lock_identity(database)
    path = identity.with_name(identity.name + ".owner.lock")
    if path.exists() or path.is_symlink():
        value = path.lstat()
        if (
            not stat.S_ISREG(value.st_mode) or value.st_nlink != 1
            or getattr(value, "st_file_attributes", 0) & 0x400
        ):
            raise UpdateError("unsafe_data_lock", "The selected data ownership lock is unsafe.")
    return DatabaseOwnerLock(identity)


def _fingerprint(path: Path) -> PayloadFile:
    directory_identity(path.parent)
    value = path.lstat()
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_nlink != 1
        or getattr(value, "st_file_attributes", 0) & 0x400
    ):
        raise UpdateError("unsafe_checkpoint", "A checkpoint input is not a regular private file.")
    digest, count = hashlib.sha256(), 0
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (value.st_dev, value.st_ino):
            raise UpdateError("checkpoint_changed", "A checkpoint input changed before reading.")
        while chunk := stream.read(256 * 1024):
            count += len(chunk)
            if count > value.st_size:
                raise UpdateError(
                    "checkpoint_changed", "Checkpoint inputs changed during preparation."
                )
            digest.update(chunk)
    row = PayloadFile(path.name, count, digest.hexdigest())
    if not file_matches(path, row):
        raise UpdateError("checkpoint_changed", "Checkpoint inputs changed during preparation.")
    return row


def _record(row: PayloadFile) -> dict[str, object]:
    return {"path": row.path, "size": row.size, "sha256": row.sha256}


def _parse_record(value: object, name: str) -> PayloadFile:
    if (
        not isinstance(value, dict)
        or set(value) != {"path", "size", "sha256"}
        or value["path"] != name
        or type(value["size"]) is not int
        or value["size"] < 0
        or not isinstance(value["sha256"], str)
        or len(value["sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in value["sha256"])
    ):
        raise UpdateError("invalid_checkpoint", "The update checkpoint receipt is invalid.")
    return PayloadFile(name, value["size"], value["sha256"])


def schema_identity(database: Path) -> tuple[int, str]:
    """Read structure only; never export rows, content, provider data or raw schema SQL."""
    directory_identity(database.parent)
    value = database.lstat()
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_nlink != 1
        or getattr(value, "st_file_attributes", 0) & 0x400
    ):
        raise UpdateError("unsafe_checkpoint", "The intended database is an alias or reparse file.")
    try:
        connection = sqlite3.connect(database.absolute().as_uri() + "?mode=ro", uri=True)
        try:
            connection.execute("BEGIN")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            schema = connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
            ).fetchmany(5001)
            if len(schema) > 5000:
                raise UpdateError(
                    "checkpoint_limit", "The storage schema exceeds the supported checkpoint limit."
                )
            raw = canonical_bytes(schema)
            if len(raw) > MANIFEST_LIMIT:
                raise UpdateError(
                    "checkpoint_limit", "The storage schema exceeds the supported checkpoint limit."
                )
            return version, hashlib.sha256(raw).hexdigest()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise UpdateError(
            "checkpoint_unavailable", "The intended storage could not be checked safely."
        ) from error


class DataCheckpoint:
    """Authority-backed DB copy; local config bytes retain their existing DPAPI binding."""

    def __init__(self, database: Path, operation: Path) -> None:
        self.database, self.operation = database, operation
        self.root = operation / "data-checkpoint"
        self._data_identity = directory_identity(database.parent)
        self._operation_identity = directory_identity(operation)
        if not database.is_absolute() or database.name in {"config.yaml", "", ".", ".."}:
            raise UpdateError("unsafe_checkpoint", "The intended database location is invalid.")

    def _check(self) -> None:
        if (
            directory_identity(self.database.parent) != self._data_identity
            or directory_identity(self.operation) != self._operation_identity
        ):
            raise UpdateError("unsafe_checkpoint", "The checkpoint or data location changed.")

    def prepare(
        self,
        backup_database: Callable[[Path], None],
        work_quiesced: Callable[[], bool],
        app_version: str,
    ) -> None:
        self._check()
        Version.parse(app_version)
        if not work_quiesced():
            raise UpdateError("application_busy", "Finish active work before preparing an update.")
        if self.root.exists() or self.root.is_symlink():
            raise UpdateError(
                "checkpoint_exists", "An existing checkpoint must be retained or reconciled."
            )
        self.root.mkdir()
        # Reuse the owning authority's coherent, verified online-backup implementation.
        backup_database(self.root / "authority.sqlite3")
        database = _fingerprint(self.root / "authority.sqlite3")
        schema_version, schema_sha256 = schema_identity(self.root / database.path)
        if not work_quiesced():
            raise UpdateError(
                "application_busy", "New work began; this update cannot close the app."
            )
        config = self.database.parent / "config.yaml"
        config_row = None
        if config.exists() or config.is_symlink():
            if config.lstat().st_size > 8 * 1024 * 1024:
                raise UpdateError(
                    "checkpoint_limit", "The settings file is too large to checkpoint safely."
                )
            config_row = _fingerprint(config)
            _copy(config, self.root / config_row.path, config_row)
        atomic_receipt(
            self.root / "checkpoint.json",
            {
                "format_version": 1,
                "app_version": app_version,
                "storage_compatibility": STORAGE_COMPATIBILITY,
                "database": _record(database),
                "config": _record(config_row) if config_row else None,
                "schema_version": schema_version,
                "schema_sha256": schema_sha256,
            },
        )

    def _load(
        self, *, expected_storage_compatibility: int = STORAGE_COMPATIBILITY
    ) -> dict[str, object]:
        if (
            type(expected_storage_compatibility) is not int
            or not 0 < expected_storage_compatibility < 2**31
        ):
            raise UpdateError("invalid_checkpoint", "The retained storage contract is invalid.")
        self._check()
        value = bounded_json(checked_file(self.root / "checkpoint.json", maximum=16 * 1024))
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "format_version",
                "app_version",
                "storage_compatibility",
                "database",
                "config",
                "schema_version",
                "schema_sha256",
            }
            or type(value["format_version"]) is not int
            or value["format_version"] != 1
            or type(value["storage_compatibility"]) is not int
            or value["storage_compatibility"] != expected_storage_compatibility
            or type(value["schema_version"]) is not int
            or value["schema_version"] < 0
        ):
            raise UpdateError("invalid_checkpoint", "The update checkpoint is incompatible.")
        Version.parse(value["app_version"])
        database = _parse_record(value["database"], "authority.sqlite3")
        if not file_matches(self.root / database.path, database):
            raise UpdateError("invalid_checkpoint", "The retained database checkpoint changed.")
        if schema_identity(self.root / database.path) != (
            value["schema_version"],
            value["schema_sha256"],
        ):
            raise UpdateError("invalid_checkpoint", "The retained database schema changed.")
        if value["config"] is not None:
            config = _parse_record(value["config"], "config.yaml")
            if not file_matches(self.root / config.path, config):
                raise UpdateError("invalid_checkpoint", "The retained settings checkpoint changed.")
        return value

    def require_compatible(
        self, started_storage_compatibility: int, *,
        expected_storage_compatibility: int = STORAGE_COMPATIBILITY,
    ) -> None:
        row = self._load(expected_storage_compatibility=expected_storage_compatibility)
        if (
            type(started_storage_compatibility) is not int
            or started_storage_compatibility != row["storage_compatibility"]
            or schema_identity(self.database) != (row["schema_version"], row["schema_sha256"])
        ):
            raise UpdateError(
                "restore_choice_required",
                "The new data format is incompatible. Restoring the checkpoint would lose "
                "progress made since the update; choose explicitly before restoring.",
            )

    def _paths(self) -> dict[str, Path]:
        return {
            "database": self.database,
            "wal": self.database.with_name(self.database.name + "-wal"),
            "shm": self.database.with_name(self.database.name + "-shm"),
            "config": self.database.parent / "config.yaml",
        }

    def restore(self, confirmation: str, processes_exited: Callable[[], bool]) -> None:
        """Retain newer data before an explicitly chosen restore; never replay work."""
        if confirmation != RESTORE_CONFIRMATION:
            raise UpdateError(
                "restore_choice_required",
                "Confirm the loss of post-checkpoint progress before restoring.",
            )
        self._check()
        if not processes_exited():
            raise UpdateError(
                "application_busy", "Close all owned app processes before restoring data."
            )
        lock = acquire_update_data_lock(self.database)
        try:
            self._restore_locked(processes_exited)
        finally:
            lock.close()

    def _restore_locked(self, processes_exited: Callable[[], bool]) -> None:
        checkpoint = self._load()
        receipt = self.root / "restore.json"
        paths = self._paths()
        if receipt.exists() or receipt.is_symlink():
            row = bounded_json(checked_file(receipt, maximum=16 * 1024))
            if (
                not isinstance(row, dict)
                or set(row) != {"format_version", "phase", "generation", "before"}
                or type(row["format_version"]) is not int
                or row["format_version"] != 1
                or row["phase"] not in ("restoring", "restored")
                or not isinstance(row["before"], dict)
                or set(row["before"]) != paths.keys()
            ):
                raise UpdateError(
                    "invalid_checkpoint", "The interrupted restore needs manual reconciliation."
                )
            generation = operation_id(row["generation"])
            if row["phase"] == "restored":
                return  # A duplicate request must never discard subsequent player progress.
        else:
            generation = uuid.uuid4().hex
            row = {
                "format_version": 1,
                "phase": "restoring",
                "generation": generation,
                "before": {
                    key: _record(_fingerprint(path)) if path.exists() or path.is_symlink() else None
                    for key, path in paths.items()
                },
            }
            atomic_receipt(receipt, row)
        before = row["before"]
        assert isinstance(before, dict)
        retained = self.root / "post-checkpoint" / generation
        ensure_directory(retained)
        directory_identity(retained)
        desired = {
            "database": _parse_record(checkpoint["database"], "authority.sqlite3"),
            "config": _parse_record(checkpoint["config"], "config.yaml")
            if checkpoint["config"] is not None
            else None,
        }
        for key, target in paths.items():
            self._check()
            if not processes_exited():
                raise UpdateError("application_busy", "A process is still using the intended data.")
            record = before[key]
            if record is None:
                if target.exists() or target.is_symlink():
                    expected = desired.get(key)
                    if expected is None or not file_matches(target, expected):
                        raise UpdateError(
                            "checkpoint_changed", "Data changed during the interrupted restore."
                        )
                continue
            original = _parse_record(record, target.name)
            saved = retained / target.name
            if saved.exists() or saved.is_symlink():
                if not file_matches(saved, original):
                    raise UpdateError(
                        "checkpoint_changed", "Retained post-checkpoint data changed."
                    )
                if not target.exists() and not target.is_symlink():
                    continue
                expected = desired.get(key)
                if expected is not None and file_matches(target, expected):
                    continue
            if not file_matches(target, original):
                raise UpdateError(
                    "checkpoint_changed", "Data changed during the interrupted restore."
                )
            os.replace(target, saved)
        for key in ("database", "config"):
            record = desired[key]
            if record is None:
                continue
            target = paths[key]
            if file_matches(target, record):
                continue
            temporary = self.root / "restore-payload" / uuid.uuid4().hex / record.path
            _copy(self.root / record.path, temporary, record)
            self._check()
            if not processes_exited() or target.exists() or target.is_symlink():
                raise UpdateError("checkpoint_changed", "The intended restore location changed.")
            os.replace(temporary, target)
        row["phase"] = "restored"
        atomic_receipt(receipt, row)
