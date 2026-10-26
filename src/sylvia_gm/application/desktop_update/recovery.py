"""Deliberate rollback holds data ownership across checkpoint and application replacement."""

from __future__ import annotations

from collections.abc import Callable

from .checkpoint import RESTORE_CONFIRMATION, DataCheckpoint, acquire_update_data_lock
from .identity import STORAGE_COMPATIBILITY
from .manifest import UpdateError, bounded_json
from .preparation import PreparedUpdate
from .restart import RestartHandshake
from .storage import atomic_receipt, checked_file

KEEP_DATA = "Keep current data"
_RECOVERABLE = frozenset({
    "prepared", "closing", "applying", "awaiting_restart", "recovery_required", "confirmed",
    "rolling_back", "rollback_ready", "rollback_awaiting_restart", "rolled_back",
})


class RecoveryOperation:
    """Uses the retained old helper's storage contract and already authenticated preparation."""

    def __init__(self, prepared: PreparedUpdate) -> None:
        self.prepared = prepared
        self.checkpoint = DataCheckpoint(prepared.root / "authority.sqlite3", prepared.stage)
        compatibility = prepared._checkpoint_binding()["storage_compatibility"]
        assert type(compatibility) is int
        self.compatibility = compatibility
        self.choice = prepared.stage / "recovery-choice.json"
        self.handshake = RestartHandshake(
            prepared.root, prepared.identity, prepared.attempt, recovery=True,
        )

    def _phase(self) -> str:
        self.prepared.require_selected(checkpoint_compatibility=self.compatibility)
        row = self.prepared.store.load()
        if row is None or row["phase"] not in _RECOVERABLE:
            raise UpdateError(
                "invalid_phase", "This update does not have a recoverable preparation.",
            )
        return str(row["phase"])

    def _started_compatibility(self) -> int:
        original = RestartHandshake(
            self.prepared.root, self.prepared.identity, self.prepared.attempt,
        )
        if original.ticket.exists() or original.ticket.is_symlink():
            row = original._load()
            if (
                row["version"] != self.prepared.manifest.version.text
                or row["target"] != self.prepared.target
                or row["distribution"] != self.prepared.distribution
            ):
                raise UpdateError("wrong_restart", "The retained new-owner identity changed.")
            compatibility = row["storage_compatibility"]
            assert type(compatibility) is int
            return compatibility
        # Ordinary starts are fenced throughout apply, before a restart intent exists.
        compatibility = self.checkpoint._load(
            expected_storage_compatibility=self.compatibility,
        )["storage_compatibility"]
        assert type(compatibility) is int
        return compatibility

    def inspect(self) -> dict[str, object]:
        """No SQL migration or launch. A live owner/helper prevents a misleading repair choice."""
        operation_lock = acquire_update_data_lock(self.prepared.stage / "helper-runtime")
        try:
            phase = self._phase()
            data_lock = acquire_update_data_lock(self.checkpoint.database)
            try:
                restart_nonce = None
                started = self._started_compatibility()
                if phase == "rollback_awaiting_restart" or (
                    phase == "rollback_ready" and self.handshake.exists()
                ):
                    restart_nonce = self.handshake._load()["nonce"]
                    # Retry opens restored data; it never restores again.
                    started = self.compatibility
                if self.checkpoint.restore_pending():
                    checkpoint = self.checkpoint._load(
                        expected_storage_compatibility=self.compatibility,
                    )
                    expected = {
                        "format_version": 1, "operation_id": self.prepared.identity,
                        "attempt": self.prepared.attempt, "choice": RESTORE_CONFIRMATION,
                        "checkpoint_version": checkpoint["app_version"],
                        "started_storage_compatibility": started,
                    }
                    if phase != "rolling_back" or bounded_json(
                        checked_file(self.choice, maximum=4096)
                    ) != expected:
                        raise UpdateError(
                            "invalid_checkpoint", "The interrupted restore choice changed."
                        )
                    # Retention can leave the live DB absent, or DB/config partly restored.
                    # Only the original explicit restore can resume; keeping this mixture is unsafe.
                    compatible = False
                else:
                    compatible = True
                    try:
                        self.checkpoint.require_compatible(
                            started,
                            expected_storage_compatibility=self.compatibility,
                        )
                    except UpdateError as error:
                        if error.code != "restore_choice_required":
                            raise
                        compatible = False
                return {
                    "operation_id": self.prepared.identity, "phase": phase,
                    "previous_version": self.prepared.current.text,
                    "can_keep_data": compatible,
                    "restart_nonce": restart_nonce,
                    "restart_capacity": self.handshake.restart_capacity(),
                    "restore_warning": "Restoring pre-update data loses progress since the "
                    "checkpoint. The newer data will be retained locally for recovery.",
                }
            finally:
                data_lock.close()
        finally:
            operation_lock.close()

    def rollback(self, choice: str, processes_exited: Callable[[], bool]) -> None:
        """The helper uses captured process handles, never a renderer-provided exit claim."""
        if choice not in {KEEP_DATA, RESTORE_CONFIRMATION}:
            raise UpdateError("restore_choice_required", "Choose how to preserve or restore data.")
        operation_lock = acquire_update_data_lock(self.prepared.stage / "helper-runtime")
        try:
            self._rollback_locked(choice, processes_exited)
        finally:
            operation_lock.close()

    def _rollback_locked(self, choice: str, processes_exited: Callable[[], bool]) -> None:
        if choice not in {KEEP_DATA, RESTORE_CONFIRMATION}:
            raise UpdateError("restore_choice_required", "Choose how to preserve or restore data.")
        if self.compatibility != STORAGE_COMPATIBILITY:
            raise UpdateError(
                "wrong_recovery_helper", "Use the retained helper matching this checkpoint.",
            )
        phase = self._phase()
        if phase in {"rolled_back", "rollback_ready", "rollback_awaiting_restart"}:
            self.prepared.replacement().verify_rollback()
            return  # An acknowledged or pending restart must not roll back later player progress.
        if not processes_exited():
            raise UpdateError("application_busy", "Close the owned application before recovery.")
        data_lock = acquire_update_data_lock(self.checkpoint.database)
        try:
            self._phase()
            checkpoint = self.checkpoint._load()
            started = self._started_compatibility()
            expected = {
                "format_version": 1, "operation_id": self.prepared.identity,
                "attempt": self.prepared.attempt, "choice": choice,
                "checkpoint_version": checkpoint["app_version"],
                "started_storage_compatibility": started,
            }
            if self.choice.exists() or self.choice.is_symlink():
                if bounded_json(checked_file(self.choice, maximum=4096)) != expected:
                    raise UpdateError(
                        "changed_recovery_choice", "The interrupted recovery choice is preserved.",
                    )
            else:
                if choice == KEEP_DATA:
                    self.checkpoint.require_compatible(started)
                atomic_receipt(self.choice, expected)
            self.prepared.store.save(
                self.prepared.identity, "rolling_back", self.prepared.manifest.version.text,
            )
            if choice == RESTORE_CONFIRMATION:
                self.checkpoint._restore_locked(processes_exited)
            compatibility = checkpoint["storage_compatibility"] if (
                choice == RESTORE_CONFIRMATION
            ) else started
            assert type(compatibility) is int
            self.prepared.replacement().rollback(
                processes_exited, lambda: self.checkpoint.require_compatible(compatibility),
            )
            self.prepared.replacement().verify_rollback()
            self.prepared.store.save(
                self.prepared.identity, "rollback_ready", self.prepared.manifest.version.text,
            )
        finally:
            data_lock.close()

    def create_restart(self, owner_identity: object) -> RestartHandshake:
        """Probe only the restored fixed sidecar before issuing a separate one-shot intent."""
        if self._phase() != "rollback_ready":
            raise UpdateError("invalid_phase", "Finish application recovery before restarting.")
        self.prepared.replacement().verify_rollback()
        compatibility = self._owner_compatibility(owner_identity)
        if self.handshake.exists():
            # Selection can survive a failed global save. Its retained error must never be
            # reused by a later launch, even when this intent still says pending.
            raise UpdateError("recovery_required", "Review the earlier recovery restart first.")
        self.handshake.create(
            live=self.prepared.live, version=self.prepared.current, target=self.prepared.target,
            distribution=self.prepared.distribution,
            owner_storage_compatibility=compatibility,
        )
        self.prepared.store.save(
            self.prepared.identity, "rollback_awaiting_restart",
            self.prepared.manifest.version.text,
        )
        return self.handshake

    def _owner_compatibility(self, owner_identity: object) -> int:
        checkpoint = self.checkpoint._load()
        if (
            not isinstance(owner_identity, dict)
            or owner_identity != {
                "format_version": 1, "product": "Sylvia GM", "component": "owner",
                "version": self.prepared.current.text, "target": self.prepared.target,
                "storage_compatibility": checkpoint["storage_compatibility"],
            }
            or type(owner_identity.get("format_version")) is not int
            or type(owner_identity.get("storage_compatibility")) is not int
        ):
            raise UpdateError(
                "wrong_owner_identity", "The restored owner identity is inconsistent.",
            )
        compatibility = checkpoint["storage_compatibility"]
        assert type(compatibility) is int
        return compatibility

    def retry_restart(
        self, expected_nonce: str, owner_identity: object, processes_exited: Callable[[], bool],
    ) -> RestartHandshake:
        """An explicit consent fences the old startup under the same lock used before migrations."""
        if not processes_exited():
            raise UpdateError("application_busy", "Close the owned application before retrying.")
        lock = acquire_update_data_lock(self.checkpoint.database)
        try:
            if self._phase() not in {"rollback_ready", "rollback_awaiting_restart"}:
                raise UpdateError("stale_restart", "The reviewed recovery is no longer pending.")
            self.prepared.replacement().verify_rollback()
            compatibility = self._owner_compatibility(owner_identity)
            self.checkpoint.require_compatible(compatibility)
            if not processes_exited():
                raise UpdateError("application_busy", "An owned process is still active.")
            self.handshake.retry_recovery(expected_nonce)
            self.prepared.store.save(
                self.prepared.identity, "rollback_awaiting_restart",
                self.prepared.manifest.version.text,
            )
        finally:
            lock.close()
        return self.handshake

    def confirm(self) -> None:
        if self._phase() not in {"rollback_awaiting_restart", "rolled_back"}:
            raise UpdateError("invalid_phase", "Recovery is no longer awaiting confirmation.")
        self.prepared.replacement().verify_rollback()
        if not self.handshake.confirmed():
            raise UpdateError("startup_unconfirmed", "The restored app has not confirmed its data.")
        self.prepared.store.save(
            self.prepared.identity, "rolled_back", self.prepared.manifest.version.text,
        )
