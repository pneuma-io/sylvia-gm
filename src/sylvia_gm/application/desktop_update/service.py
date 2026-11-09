"""Lazy owner boundary for the native desktop's fixed updater operations."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType

from sylvia_gm import __version__

from .controller import UpdateController
from .launch import Installation
from .manifest import KeyRing, UpdateError, Version
from .storage import operation_id
from .transport import HttpsTransport

# Release bootstrap pins approved public keys here. Never load trust or private keys from IPC,
# environment variables, downloaded metadata or a user-selected file.
PUBLIC_KEYS: Mapping[str, bytes] = MappingProxyType({})


class DesktopUpdateService:
    def __init__(
        self,
        storage_root: Path,
        *,
        installation: Installation | None = None,
        backup_database: Callable[[Path], None] | None = None,
        reserved: Callable[[str], bool] | None = None,
        release: Callable[[str], None] | None = None,
    ) -> None:
        self._storage = storage_root
        self._root = storage_root / ".application-updates"
        self._installation = installation
        self._backup_database, self._reserved, self._release = backup_database, reserved, release
        self._controller: UpdateController | None = None
        self._context: tuple[str, str] | None = None
        self._lock = threading.RLock()
        self._restore_thread: threading.Thread | None = None
        self._restore_error: UpdateError | None = None
        self._closed = False
        self._generation = 0

    def _restore(self, target: str, distribution: str, keyring: KeyRing) -> None:
        generation = self._generation
        def initialize() -> None:
            try:
                controller = UpdateController(
                    root=self._root,
                    current=Version.parse(__version__),
                    target=target,
                    distribution=distribution,
                    transport=HttpsTransport(),
                    verify=keyring.verify,
                )
            except Exception as cause:
                error = (
                    cause
                    if isinstance(cause, UpdateError)
                    else UpdateError(
                        "recovery_unavailable",
                        "Stored update status could not be checked. You can retry explicitly.",
                    )
                )
                with self._lock:
                    if generation == self._generation:
                        self._restore_error = error
                return
            with self._lock:
                if self._closed or generation != self._generation:
                    controller.close()
                else:
                    self._controller = controller

        self._restore_error = None
        self._restore_thread = threading.Thread(
            target=initialize, name="sylvia-update-recovery", daemon=True
        )
        try:
            self._restore_thread.start()
        except RuntimeError:
            self._restore_error = UpdateError(
                "worker_unavailable",
                "The update recovery worker could not start. You can retry.",
            )

    def handle(self, action: str, params: Mapping[str, object]) -> dict[str, object]:
        with self._lock:
            if self._closed:
                raise UpdateError("updater_closed", "The desktop updater is closing.")
        required = {"host"} if action in {"state", "check"} else {"host", "operation_id"}
        if (
            action not in {"state", "check", "download", "cancel", "defer", "prepare", "handoff"}
            or set(params) != required
        ):
            raise UpdateError(
                "invalid_update_request", "This desktop update request is unsupported."
            )
        if action not in {"state", "check"}:
            operation_id(params["operation_id"])
        host = params["host"]
        if (
            not isinstance(host, dict)
            or set(host) != {"version", "target", "distribution"}
            or host["version"] != __version__
            or not isinstance(host["target"], str)
            or len(host["target"]) > 64
            or host["distribution"] not in ("installed", "portable")
        ):
            raise UpdateError(
                "wrong_component_version", "The desktop and owner update context is inconsistent."
            )
        target, distribution = str(host["target"]), str(host["distribution"])
        context = target, distribution
        with self._lock:
            if self._context is not None and self._context != context:
                raise UpdateError(
                    "changed_update_context", "Restart this copy before using its updater."
                )
            self._context = context
        keyring = KeyRing(PUBLIC_KEYS)
        supported = target == "windows-x86_64"
        base_state = {
            "operation_id": None,
            "current_version": __version__,
            "version": None,
            "channel": Version.parse(__version__).channel,
            "target": target,
            "distribution": distribution,
            "notes": "",
            "received": 0,
            "total": None,
            "downloaded": False,
            "can_apply": False,
            "source": "Official pneuma-io/sylvia-gm GitHub releases",
        }
        if not supported or not keyring.keys:
            code = "unsupported_target" if not supported else "trust_not_configured"
            message = (
                "In-app updating is not supported for this platform."
                if not supported
                else "This build has no trusted update key. Install an approved "
                "updater-capable bootstrap before updating."
            )
            if action != "state":
                raise UpdateError(code, message)
            return {
                **base_state,
                "phase": "unavailable",
                "busy": False,
                "error_code": code,
                "message": message,
            }
        with self._lock:
            if self._controller is None:
                if self._restore_thread is None or (
                    action == "check" and not self._restore_thread.is_alive()
                ):
                    self._restore(target, distribution, keyring)
                error = self._restore_error
                return {
                    **base_state,
                    "phase": "error" if error else "recovering",
                    "busy": error is None,
                    "error_code": error.code if error else None,
                    "message": str(error)
                    if error
                    else "Reading previous update status. Check for releases when this finishes.",
                }
            controller = self._controller
        if action == "state":
            result = controller.state()
        elif action == "check":
            result = controller.check()
        elif action == "prepare":
            identity = operation_id(params["operation_id"])
            result = self._prepare(controller, identity, keyring, target, distribution)
        elif action == "handoff":
            return self._handoff(
                controller, operation_id(params["operation_id"]), keyring, target, distribution,
            )
        else:
            identity = operation_id(params["operation_id"])
            result = getattr(controller, action)(identity)
            if action == "defer":
                self._release_if_reserved(identity)
        # Apply remains unavailable until its separate native checkpoint/shutdown boundary exists.
        return {**result, "can_apply": False}

    def _release_if_reserved(self, identity: str) -> None:
        if self._reserved is not None and self._reserved(identity) and self._release is not None:
            self._release(identity)

    def _prepare(
        self, controller: UpdateController, identity: str, keyring: KeyRing,
        target: str, distribution: str,
    ) -> dict[str, object]:
        from .preparation import PreparedUpdate, new_attempt
        from .runtime import child_environment

        installation, backup, reserved = self._installation, self._backup_database, self._reserved
        if installation is None or backup is None or reserved is None or self._release is None:
            raise UpdateError(
                "unsupported_launch_context", "This launch cannot prepare an application update."
            )
        installation.check(target, distribution)
        child_environment()  # Refuse engineering overrides before checkpoint or file preparation.
        if not reserved(identity):
            raise UpdateError("update_busy", "Reserve an idle application before preparing.")

        def quiesced() -> bool:
            with self._lock:
                return not self._closed and reserved(identity)

        def action() -> str:
            update = PreparedUpdate(
                self._storage, installation.live, identity, new_attempt(),
                current=Version.parse(__version__), target=target,
                distribution=distribution, verify=keyring.verify,
            )
            update.prepare(backup, quiesced)
            return update.attempt

        return controller.prepare(identity, action, lambda: self._release_if_reserved(identity))

    def abort_preparation(self, identity: str) -> None:
        """A matching native abort cannot release admission while checkpointing still runs."""
        with self._lock:
            controller = self._controller
        if controller is not None:
            state = controller.state()
            if state["busy"]:
                raise UpdateError("update_busy", "Wait for update preparation to finish.")
            if state["operation_id"] == identity and state["phase"] == "prepared":
                controller.defer(identity)

    def _handoff(
        self, controller: UpdateController, identity: str, keyring: KeyRing,
        target: str, distribution: str,
    ) -> dict[str, object]:
        """Private owner-pipe metadata; never a renderer-selected path or executable."""
        from .preparation import PreparedUpdate
        from .runtime import child_environment

        state = controller.state()
        installation = self._installation
        if (
            installation is None or self._reserved is None or not self._reserved(identity)
            or state["operation_id"] != identity or state["phase"] != "prepared"
            or state["busy"]
        ):
            raise UpdateError("update_busy", "A matching prepared owner reservation is required.")
        installation.check(target, distribution)
        child_environment()
        attempt = operation_id(state["prepared_attempt"])
        prepared = PreparedUpdate(
            self._storage, installation.live, identity, attempt,
            current=Version.parse(__version__), target=target, distribution=distribution,
            verify=keyring.verify,
        )
        prepared.require_selected()
        _, helper = prepared.replacement().helper_payload()
        return {"operation_id": identity, "attempt": attempt,
                "helper": {"size": helper.size, "sha256": helper.sha256}}

    def preparing(self) -> bool:
        with self._lock:
            controller = self._controller
        return controller is not None and bool(controller.state()["busy"])

    def startup_completed(self) -> None:
        """Discard a boot-time recovery projection after the helper's final commit."""
        with self._lock:
            self._generation += 1
            controller = self._controller
            self._controller = None
            self._restore_thread = None
            self._restore_error = None
        if controller is not None:
            controller.close()
            controller.wait()

    def arm_shutdown(self, identity: str) -> None:
        with self._lock:
            controller = self._controller
        if controller is None:
            raise UpdateError("invalid_phase", "The update is not prepared for shutdown.")
        state = controller.state()
        self._handoff(
            controller, identity, KeyRing(PUBLIC_KEYS), str(state["target"]),
            str(state["distribution"]),
        )
        controller.arm_shutdown(identity)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            controller = self._controller
        if controller is not None:
            controller.close()
            controller.wait()
