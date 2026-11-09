"""One restart intent and exact app/owner/data acknowledgement; never replay saved work."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from .checkpoint import schema_identity
from .manifest import UpdateError, Version, bounded_json
from .storage import (
    atomic_receipt,
    checked_file,
    directory_identity,
    ensure_directory,
    operation_id,
)

Launched = TypeVar("Launched")
MAX_RECOVERY_STARTS = 64


class RestartHandshake:
    def __init__(
        self, data_root: Path, identity: str, attempt: str, *, recovery: bool = False,
    ) -> None:
        self.root = data_root
        self.identity, self.attempt = operation_id(identity), operation_id(attempt)
        self.stage = data_root / ".application-updates" / self.identity / "attempts" / self.attempt
        self._root_identity = directory_identity(data_root)
        self._stage_identity = directory_identity(self.stage)
        self.recovery = recovery
        self.selection = self.stage / "rollback-restart.json"

    def exists(self) -> bool:
        path = self.selection if self.recovery else self.stage / "restart.json"
        return path.exists() or path.is_symlink()

    def _generation(self) -> Path:
        selected = bounded_json(checked_file(self.selection, maximum=4096))
        if (
            not isinstance(selected, dict) or set(selected) != {"format_version", "nonce"}
            or type(selected["format_version"]) is not int or selected["format_version"] != 1
        ):
            raise UpdateError("invalid_restart", "The selected recovery restart is invalid.")
        path = self.stage / "rollback-starts" / operation_id(selected["nonce"])
        directory_identity(path)
        return path

    @property
    def ticket(self) -> Path:
        return self._generation() / "restart.json" if self.recovery else self.stage / "restart.json"

    @property
    def receipt(self) -> Path:
        return self._generation() / "startup-confirmed.json" if self.recovery else (
            self.stage / "startup-confirmed.json"
        )

    @property
    def error_receipt(self) -> Path:
        return self._generation() / "helper-error.json" if self.recovery else (
            self.stage / "helper-error.json"
        )

    def _select_recovery(self, row: dict[str, object]) -> None:
        self._check()
        generations = self.stage / "rollback-starts"
        ensure_directory(generations)
        # Only explicit failed/ambiguous starts retain generations. Ordinary checks do not.
        for index, _ in enumerate(generations.iterdir(), 1):
            if index >= MAX_RECOVERY_STARTS:
                raise UpdateError("restart_limit", "Retained recovery attempts need repair review.")
        nonce = operation_id(row["nonce"])
        generation = generations / nonce
        generation.mkdir()
        directory_identity(generation)
        atomic_receipt(generation / "restart.json", row)
        self._check()
        # Select last. An interrupted write leaves the earlier intent selected and unchanged.
        atomic_receipt(self.selection, {"format_version": 1, "nonce": nonce})

    def restart_capacity(self) -> bool:
        path = self.stage / "rollback-starts"
        if not path.exists() and not path.is_symlink():
            return True
        directory_identity(path)
        return all(index < MAX_RECOVERY_STARTS for index, _ in enumerate(path.iterdir(), 1))

    def _check(self) -> None:
        if (
            directory_identity(self.root) != self._root_identity
            or directory_identity(self.stage) != self._stage_identity
        ):
            raise UpdateError("changed_restart_root", "The intended update data location changed.")

    def create(
        self,
        *,
        live: Path,
        version: Version,
        target: str,
        distribution: str,
        owner_storage_compatibility: int,
    ) -> str:
        self._check()
        if (
            target != "windows-x86_64"
            or distribution not in {"installed", "portable"}
            or type(owner_storage_compatibility) is not int
            or owner_storage_compatibility < 1
        ):
            raise UpdateError("invalid_restart", "The expected owner identity is unsupported.")
        if self.exists():
            raise UpdateError("restart_exists", "An existing restart intent must be reconciled.")
        nonce = uuid.uuid4().hex
        row: dict[str, object] = {
            "format_version": 1,
            "operation_id": self.identity,
            "attempt": self.attempt,
            "nonce": nonce,
            "phase": "pending",
            "version": version.text,
            "target": target,
            "distribution": distribution,
            "storage_compatibility": owner_storage_compatibility,
            "data_identity": list(self._root_identity),
            "app_identity": list(directory_identity(live)),
        }
        if self.recovery:
            self._select_recovery(row)
        else:
            atomic_receipt(self.ticket, row)
        return nonce

    def retry_recovery(self, expected_nonce: str) -> str:
        """The recovery owner holds operation and real data locks before this explicit retry."""
        if not self.recovery:
            raise UpdateError("invalid_restart", "Only recovery has an explicit restart retry.")
        row = self._load()
        if operation_id(expected_nonce) != row["nonce"]:
            raise UpdateError("stale_restart", "The reviewed recovery restart changed.")
        # The previous generation, acknowledgement and failure stay immutable in their folder.
        nonce = uuid.uuid4().hex
        self._select_recovery({**row, "nonce": nonce, "phase": "pending"})
        return nonce

    def _load(self) -> dict[str, object]:
        self._check()
        row = bounded_json(checked_file(self.ticket, maximum=8192))
        if (
            not isinstance(row, dict)
            or set(row)
            != {
                "format_version",
                "operation_id",
                "attempt",
                "nonce",
                "phase",
                "version",
                "target",
                "distribution",
                "storage_compatibility",
                "data_identity",
                "app_identity",
            }
            or type(row["format_version"]) is not int
            or row["format_version"] != 1
            or row["operation_id"] != self.identity
            or row["attempt"] != self.attempt
            or row["phase"] not in {"pending", "launching", "launched", "launch_failed"}
            or row["target"] != "windows-x86_64"
            or row["distribution"] not in {"installed", "portable"}
            or type(row["storage_compatibility"]) is not int
            or row["storage_compatibility"] < 1
            or row["data_identity"] != list(self._root_identity)
        ):
            raise UpdateError("invalid_restart", "The retained restart intent is inconsistent.")
        operation_id(row["nonce"])
        if self.recovery and self._generation().name != row["nonce"]:
            raise UpdateError(
                "invalid_restart", "The recovery generation does not match its ticket.",
            )
        Version.parse(row["version"])
        for name in ("data_identity", "app_identity"):
            value = row[name]
            if (
                not isinstance(value, list)
                or len(value) != 2
                or any(type(part) is not int or part < 0 for part in value)
            ):
                raise UpdateError("invalid_restart", "A retained restart location is inconsistent.")
        return row

    def launch_once(self, launch: Callable[[tuple[str, ...]], Launched]) -> Launched:
        row = self._load()
        if row["phase"] != "pending":
            raise UpdateError(
                "restart_already_attempted",
                "This restart has already been attempted. "
                "Reconcile its result before starting another process.",
            )
        row["phase"] = "launching"
        atomic_receipt(self.ticket, row)
        try:
            process = launch(
                ("--sylvia-update-recovery-start" if self.recovery else "--sylvia-update-start",
                 self.identity, self.attempt, str(row["nonce"]))
            )
        except OSError as error:
            row["phase"] = "launch_failed"
            atomic_receipt(self.ticket, row)
            raise UpdateError(
                "restart_failed", "The updated app could not start. Recovery is retained."
            ) from error
        # If this write fails the launch remains ambiguous. No automatic second launch is allowed.
        row["phase"] = "launched"
        atomic_receipt(self.ticket, row)
        return process

    def authorize(
        self,
        *,
        nonce: str,
        live: Path,
        desktop_version: str,
        owner_version: str,
        target: str,
        distribution: str,
        storage_compatibility: int,
    ) -> dict[str, object]:
        """Check the exact native/owner context before migrations, without acknowledging startup."""
        row = self._load()
        if (
            row["phase"] not in {"launching", "launched"}
            or operation_id(nonce) != row["nonce"]
            or desktop_version != row["version"]
            or owner_version != row["version"]
            or target != row["target"]
            or distribution != row["distribution"]
            or type(storage_compatibility) is not int
            or storage_compatibility != row["storage_compatibility"]
            or list(directory_identity(live)) != row["app_identity"]
        ):
            raise UpdateError(
                "wrong_restart", "The restarted app, owner or data context did not match."
            )
        return row

    def confirm(
        self,
        *,
        nonce: str,
        live: Path,
        desktop_version: str,
        owner_version: str,
        target: str,
        distribution: str,
        storage_compatibility: int,
    ) -> None:
        """Called after the actual app/owner have opened usable storage, never before migration."""
        row = self.authorize(
            nonce=nonce, live=live, desktop_version=desktop_version,
            owner_version=owner_version, target=target, distribution=distribution,
            storage_compatibility=storage_compatibility,
        )
        database = self.root / "authority.sqlite3"
        schema_version, schema_sha256 = schema_identity(database)
        value = {
            **row,
            "phase": "confirmed",
            "desktop_version": desktop_version,
            "owner_version": owner_version,
            "schema_version": schema_version,
            "schema_sha256": schema_sha256,
        }
        if self.receipt.exists() or self.receipt.is_symlink():
            prior = bounded_json(checked_file(self.receipt, maximum=8192))
            if prior != value:
                raise UpdateError(
                    "changed_startup_receipt", "An earlier startup result is preserved."
                )
            return
        atomic_receipt(self.receipt, value)

    def confirmed(self) -> bool:
        row = self._load()
        if not self.receipt.exists() and not self.receipt.is_symlink():
            return False
        receipt = bounded_json(checked_file(self.receipt, maximum=8192))
        if (
            row["phase"] not in {"launching", "launched"}
            or not isinstance(receipt, dict)
            or set(receipt)
            != row.keys() | {"schema_version", "schema_sha256", "desktop_version", "owner_version"}
            or receipt.get("phase") != "confirmed"
            or receipt["desktop_version"] != row["version"]
            or receipt["owner_version"] != row["version"]
            or any(receipt[key] != value for key, value in row.items() if key != "phase")
            or type(receipt["schema_version"]) is not int
            or receipt["schema_version"] < 0
            or not isinstance(receipt["schema_sha256"], str)
            or len(receipt["schema_sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in receipt["schema_sha256"])
        ):
            raise UpdateError(
                "wrong_startup_receipt", "The startup acknowledgement is inconsistent."
            )
        return True
