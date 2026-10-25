"""Bind one verified download, immutable application copy and coherent checkpoint to an attempt."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from pathlib import Path

from .checkpoint import DataCheckpoint, acquire_update_data_lock
from .installed import InstalledRegistration, InstalledReplacement, WindowsRegistration, run_nsis
from .manifest import (
    MANIFEST_LIMIT,
    Manifest,
    UpdateError,
    Version,
    bounded_json,
    parse_current_recovery_manifest,
    parse_manifest,
)
from .payload import extract_portable, verify_artifact
from .replacement import PortableReplacement, _copy, file_matches
from .restart import RestartHandshake
from .storage import UpdateStore, atomic_receipt, checked_file, directory_identity, operation_id


class PreparedUpdate:
    def __init__(
        self,
        data_root: Path,
        live: Path,
        identity: str,
        attempt: str,
        *,
        current: Version,
        target: str,
        distribution: str,
        verify: Callable[[str, bytes, bytes], None],
        registration: InstalledRegistration | None = None,
        run_installer: Callable[[Path, Path], int] = run_nsis,
    ) -> None:
        self.root, self.live = data_root, live
        self.identity, self.attempt = operation_id(identity), operation_id(attempt)
        self.base = data_root / ".application-updates" / self.identity
        self.stage = self.base / "attempts" / self.attempt
        self.current, self.target, self.distribution = current, target, distribution
        self._root_identity, self._live_identity = (
            directory_identity(data_root),
            directory_identity(live),
        )
        directory_identity(self.base)
        self.store = UpdateStore(data_root / ".application-updates")
        self.manifest = parse_manifest(
            checked_file(self.base / "manifest.json", maximum=MANIFEST_LIMIT),
            current=current,
            target=target,
            distribution=distribution,
            verify=verify,
        )
        verify_artifact(self.base / "artifact.verified", self.manifest)
        if distribution == "portable":
            if data_root != live / "isolated-data":
                raise UpdateError(
                    "wrong_data_root", "Portable updates preserve adjacent isolated-data."
                )
            self.previous: Manifest | None = None
        elif distribution == "installed":
            self.previous = parse_current_recovery_manifest(
                checked_file(self.base / "previous-manifest.json", maximum=MANIFEST_LIMIT),
                current=current,
                target=target,
                distribution=distribution,
                verify=verify,
            )
            verify_artifact(self.base / "previous-installer/artifact.verified", self.previous)
        else:
            raise UpdateError("wrong_distribution", "This update distribution is unsupported.")
        self.registration = registration
        self.run_installer = run_installer
        self._replacement: PortableReplacement | InstalledReplacement | None = None

    def _check(self) -> None:
        if (
            directory_identity(self.root) != self._root_identity
            or directory_identity(self.live) != self._live_identity
        ):
            raise UpdateError(
                "changed_update_root", "The selected application or data folder changed."
            )

    def replacement(self) -> PortableReplacement | InstalledReplacement:
        self._check()
        if self._replacement is not None:
            return self._replacement
        if self.previous is None:
            self._replacement = PortableReplacement(self.live, self.stage, self.manifest)
        else:
            self._replacement = InstalledReplacement(
                self.live,
                self.stage,
                self.manifest,
                self.previous,
                self.registration if self.registration is not None else WindowsRegistration(),
                self.run_installer,
                artifacts=self.base,
            )
        return self._replacement

    def _selected(self, phases: set[str]) -> None:
        self._check()
        row = self.store.load()
        if (
            row is None
            or row["operation_id"] != self.identity
            or row["phase"] not in phases
            or row["version"] != self.manifest.version.text
        ):
            raise UpdateError("stale_operation", "This prepared update is no longer selected.")

    def _checkpoint_binding(self) -> dict[str, object]:
        raw = checked_file(self.stage / "data-checkpoint/checkpoint.json", maximum=16 * 1024)
        row = bounded_json(raw, maximum=16 * 1024)
        if (
            not isinstance(row, dict)
            or type(row.get("storage_compatibility")) is not int
            or not 0 < row["storage_compatibility"] < 2**31
        ):
            raise UpdateError("invalid_checkpoint", "The retained storage contract is invalid.")
        return {
            "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "storage_compatibility": row["storage_compatibility"],
        }

    def prepare(
        self, backup_database: Callable[[Path], None], quiesced: Callable[[], bool]
    ) -> None:
        """Called only while the native parent holds the owner/worker admission reservation."""
        self._selected({"verified", "deferred", "preparing"})
        if not quiesced():
            raise UpdateError(
                "application_busy", "Finish admitted work before preparing an update."
            )
        self.store.save(self.identity, "preparing", self.manifest.version.text)
        try:
            self.stage.parent.mkdir(exist_ok=True)
            directory_identity(self.stage.parent)
            self.stage.mkdir()  # Every retry uses a fresh attempt; old checkpoints stay retained.
            directory_identity(self.stage)
            if self.distribution == "portable":
                extract_portable(
                    self.base / "artifact.verified", self.manifest, self.stage / "prepared"
                )
            self.replacement().prepare()
            source, helper = self.replacement().helper_payload()
            _copy(source, self.stage / "helper/sylvia-gm-owner.exe", helper)
            checkpoint = DataCheckpoint(self.root / "authority.sqlite3", self.stage)
            checkpoint.prepare(backup_database, quiesced, self.current.text)
            if not quiesced():
                raise UpdateError("application_busy", "New work prevents a safe update close.")
            self._check()
            atomic_receipt(
                self.stage / "prepared-update.json",
                {
                    "format_version": 2,
                    "operation_id": self.identity,
                    "attempt": self.attempt,
                    "current_version": self.current.text,
                    "version": self.manifest.version.text,
                    "target": self.target,
                    "distribution": self.distribution,
                    "data_identity": list(self._root_identity),
                    "app_identity": list(self._live_identity),
                    "helper": {"size": helper.size, "sha256": helper.sha256},
                    "checkpoint": self._checkpoint_binding(),
                },
            )
            atomic_receipt(
                self.base / "selected-attempt.json",
                {
                    "format_version": 1,
                    "attempt": self.attempt,
                },
            )
            self.store.save(self.identity, "prepared", self.manifest.version.text)
        except Exception:
            # No application/data file has been replaced. Retain the failed attempt and let
            # an explicit retry take a fresh checkpoint; never reuse a stale saved database.
            self.store.save(self.identity, "verified", self.manifest.version.text)
            raise

    def require_selected(self, *, checkpoint_compatibility: int | None = None) -> None:
        self._selected(
            {
                "prepared",
                "closing",
                "applying",
                "awaiting_restart",
                "recovery_required",
                "rolling_back",
                "rollback_ready",
                "rollback_awaiting_restart",
                "rolled_back",
                "confirmed",
            }
        )
        selection = bounded_json(checked_file(self.base / "selected-attempt.json", maximum=4096))
        if (
            not isinstance(selection, dict)
            or selection != {"format_version": 1, "attempt": self.attempt}
            or type(selection["format_version"]) is not int
        ):
            raise UpdateError(
                "stale_attempt", "Another update preparation superseded this attempt."
            )
        row = bounded_json(checked_file(self.stage / "prepared-update.json", maximum=8192))
        _, helper = self.replacement().helper_payload()
        expected = {
            "format_version": 2,
            "operation_id": self.identity,
            "attempt": self.attempt,
            "current_version": self.current.text,
            "version": self.manifest.version.text,
            "target": self.target,
            "distribution": self.distribution,
            "data_identity": list(self._root_identity),
            "app_identity": list(self._live_identity),
            "helper": {"size": helper.size, "sha256": helper.sha256},
            "checkpoint": self._checkpoint_binding(),
        }
        if (
            not isinstance(row, dict)
            or row != expected
            or type(row["format_version"]) is not int
            or any(
                type(part) is not int
                for name in ("data_identity", "app_identity")
                for part in row[name]
            )
        ):
            raise UpdateError(
                "changed_preparation", "The prepared application/data context changed."
            )
        if not file_matches(self.stage / "helper/sylvia-gm-owner.exe", helper):
            raise UpdateError("changed_helper", "The retained update helper changed.")
        checkpoint = DataCheckpoint(self.root / "authority.sqlite3", self.stage)
        if checkpoint_compatibility is None:
            checkpoint._load()
        else:
            # A new owner verifies the retained old checkpoint's bytes before migration.
            # Old-helper rollback still uses its own compiled contract by default.
            checkpoint._load(expected_storage_compatibility=checkpoint_compatibility)

    def apply(self, processes_exited: Callable[[], bool]) -> None:
        self.require_selected()
        row = self.store.load()
        if row is None or row["phase"] not in {
            "prepared", "closing", "applying", "awaiting_restart",
        }:
            raise UpdateError(
                "invalid_phase", "This update cannot be applied in its current state.",
            )
        if not processes_exited():
            raise UpdateError("application_busy", "Owned app processes have not all exited.")
        lock = acquire_update_data_lock(self.root / "authority.sqlite3")
        try:
            self.require_selected()
            self.store.save(self.identity, "applying", self.manifest.version.text)
            self.replacement().apply(processes_exited)
            self.store.save(self.identity, "awaiting_restart", self.manifest.version.text)
        finally:
            lock.close()

    def create_restart(self, owner_identity: object) -> RestartHandshake:
        self.require_selected()
        if (
            not isinstance(owner_identity, dict)
            or set(owner_identity)
            != {
                "format_version",
                "product",
                "component",
                "version",
                "storage_compatibility",
                "target",
            }
            or type(owner_identity["format_version"]) is not int
            or owner_identity["format_version"] != 1
            or owner_identity["product"] != "Sylvia GM"
            or owner_identity["component"] != "owner"
            or owner_identity["version"] != self.manifest.version.text
            or owner_identity["target"] != self.target
            or type(owner_identity["storage_compatibility"]) is not int
            or owner_identity["storage_compatibility"] < 1
        ):
            raise UpdateError(
                "wrong_owner_identity", "The updated sidecar identity is inconsistent."
            )
        row = self.store.load()
        if row is None or row["phase"] != "awaiting_restart":
            raise UpdateError("invalid_phase", "Apply must finish before restarting this copy.")
        handshake = RestartHandshake(self.root, self.identity, self.attempt)
        handshake.create(
            live=self.live,
            version=self.manifest.version,
            target=self.target,
            distribution=self.distribution,
            owner_storage_compatibility=owner_identity["storage_compatibility"],
        )
        return handshake

    def confirm(self, *, checkpoint_compatibility: int | None = None) -> None:
        self.require_selected(checkpoint_compatibility=checkpoint_compatibility)
        row = self.store.load()
        if row is None or row["phase"] not in {"awaiting_restart", "confirmed"}:
            raise UpdateError("invalid_phase", "The update is no longer awaiting confirmation.")
        if not RestartHandshake(self.root, self.identity, self.attempt).confirmed():
            raise UpdateError("startup_unconfirmed", "The updated app has not confirmed its data.")
        self.replacement().confirm()
        self.store.save(self.identity, "confirmed", self.manifest.version.text)


def new_attempt() -> str:
    return uuid.uuid4().hex
