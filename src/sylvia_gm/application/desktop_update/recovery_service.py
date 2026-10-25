"""Recovery-only private pipe: no authority, migration, campaign read or provider admission."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TextIO

from sylvia_gm import __version__
from sylvia_gm.domain.errors import OwnerConflict
from sylvia_gm.infrastructure.launch_handshake import derive_owner_response

from .checkpoint import RESTORE_CONFIRMATION
from .launch import Installation
from .manifest import KeyRing, UpdateError, Version, bounded_json
from .preparation import PreparedUpdate
from .recovery import KEEP_DATA, RecoveryOperation
from .storage import UpdateStore, checked_file, operation_id

# This is separate from public trust. Enable only after actual packaged recovery qualification.
_RECOVERY_QUALIFIED = False
_CHOICES = {"keep": KEEP_DATA, "restore": RESTORE_CONFIRMATION}


class RecoveryService:
    def __init__(
        self, installation: Installation, *, current: Version,
        verify: Callable[[str, bytes, bytes], None],
        native_version: str | None = None,
    ) -> None:
        self.installation, self.current, self.verify = installation, current, verify
        self.native_version = native_version if native_version is not None else current.text

    def _prepared(self) -> PreparedUpdate:
        native = self.installation
        native.check(native.target, native.distribution)
        root = native.data / ".application-updates"
        row = UpdateStore(root).load()
        if row is None:
            raise UpdateError("recovery_unavailable", "No retained update is selected.")
        identity = operation_id(row["operation_id"])
        base = root / identity
        selected = bounded_json(checked_file(base / "selected-attempt.json", maximum=4096))
        if (
            not isinstance(selected, dict) or set(selected) != {"format_version", "attempt"}
            or type(selected["format_version"]) is not int or selected["format_version"] != 1
        ):
            raise UpdateError("recovery_unavailable", "The retained attempt needs manual repair.")
        attempt = operation_id(selected["attempt"])
        binding = bounded_json(checked_file(
            base / "attempts" / attempt / "prepared-update.json", maximum=8192,
        ))
        if not isinstance(binding, dict):
            raise UpdateError("recovery_unavailable", "The retained preparation is invalid.")
        prepared = PreparedUpdate(
            native.data, native.live, identity, attempt,
            current=Version.parse(binding.get("current_version")), target=native.target,
            distribution=native.distribution, verify=self.verify,
        )
        versions = {prepared.current.text, prepared.manifest.version.text}
        if self.current.text not in versions or self.native_version not in versions:
            raise UpdateError("wrong_recovery_app", "Use the application matching this update.")
        RecoveryOperation(prepared)._phase()
        return prepared

    def handle(self, action: str, params: Mapping[str, object]) -> dict[str, object]:
        required = {"host"} if action == "state" else {
            "host", "operation_id", "choice", "restart_nonce",
        }
        if action not in {"state", "handoff"} or set(params) != required or params["host"] != {
            "version": self.native_version, "target": self.installation.target,
            "distribution": self.installation.distribution,
        }:
            raise UpdateError("invalid_update_request", "The native recovery context changed.")
        prepared = self._prepared()
        recovery = RecoveryOperation(prepared)
        state = recovery.inspect()
        if action == "state":
            enabled = _RECOVERY_QUALIFIED and state["restart_capacity"] is True
            message = "Choose a data recovery option before restarting."
            if state["restart_nonce"] is not None:
                message = "Review a retry of the restored application. Current data will be kept."
                enabled = enabled and state["can_keep_data"] is True
            if not state["restart_capacity"]:
                message = ("Retained recovery attempts need repair review. Keep saved data and "
                           "the recovery files intact.")
            elif not _RECOVERY_QUALIFIED:
                message = ("Automatic recovery is unavailable in this build. Recovery files and "
                           "saved data are retained. Reinstall the matching official application "
                           "to repair it.")
            return {
                **state, "can_recover": enabled, "message": message,
            }
        if not _RECOVERY_QUALIFIED:
            raise UpdateError(
                "recovery_unavailable", "Packaged recovery is not enabled in this build.",
            )
        choice = params["choice"]
        if (
            operation_id(params["operation_id"]) != prepared.identity
            or not isinstance(choice, str) or choice not in _CHOICES
        ):
            raise UpdateError("invalid_update_request", "The recovery selection is invalid.")
        restart_nonce = params["restart_nonce"]
        if restart_nonce is not None:
            operation_id(restart_nonce)
        if (
            restart_nonce != state["restart_nonce"] or not state["restart_capacity"]
            or (restart_nonce is not None and choice != "keep")
        ):
            raise UpdateError(
                "stale_restart", "Review the current recovery restart before retrying.",
            )
        if choice == "keep" and state["can_keep_data"] is not True:
            raise UpdateError("restore_choice_required", "Choose an explicit checkpoint restore.")
        _, helper = prepared.replacement().helper_payload()
        return {
            "operation_id": prepared.identity, "attempt": prepared.attempt,
            "helper": {"size": helper.size, "sha256": helper.sha256},
            "restart_nonce": restart_nonce,
        }


def _read(stream: TextIO) -> dict[str, object] | None:
    raw = stream.readline(65537)
    if not raw:
        return None
    if not raw.endswith("\n") or len(raw.encode()) > 65536:
        raise UpdateError("invalid_update_request", "The recovery request exceeds its bound.")
    row = bounded_json(raw.encode(), maximum=65536)
    if not isinstance(row, dict):
        raise UpdateError("invalid_update_request", "The recovery request is invalid.")
    return row


def _write(stream: TextIO, row: object) -> None:
    stream.write(json.dumps(row, separators=(",", ":")) + "\n")
    stream.flush()


def run_recovery(launch: Mapping[str, object], source: TextIO, output: TextIO) -> int:
    """The same native launch challenge authenticates a server that cannot open player data."""
    from .service import PUBLIC_KEYS

    raw_database, secret = launch.get("database_path"), launch.get("secret_hex")
    if launch.get("type") != "launch" or not isinstance(raw_database, str) or not isinstance(
        secret, str,
    ):
        raise UpdateError("invalid_update_request", "The native recovery launch is incomplete.")
    database = Path(raw_database)
    if not database.is_absolute() or database.name != "authority.sqlite3":
        raise UpdateError("invalid_update_request", "The selected data location is invalid.")
    context = launch.get("desktop_update_context")
    installation = Installation.for_recovery(context, database.parent)
    if installation is None:
        raise UpdateError(
            "invalid_launch_context", "Recovery needs the original native copy context.",
        )
    response = derive_owner_response(secret)
    service = RecoveryService(
        installation, current=Version.parse(__version__), verify=KeyRing(PUBLIC_KEYS).verify,
        native_version=str(context["version"]) if isinstance(context, dict) else None,
    )
    _write(output, {"ok": True, "type": "ready", "response_hex": response, "recovery_only": True})
    while (row := _read(source)) is not None:
        identity = row.get("id")
        try:
            if (
                not isinstance(identity, str) or not 0 < len(identity) <= 128
                or set(row) != {"id", "method", "params"}
                or not isinstance(row["params"], dict)
            ):
                raise UpdateError("invalid_update_request", "The recovery request is invalid.")
            method, params = row["method"], row["params"]
            if method == "shutdown" and not params:
                _write(output, {"id": identity, "ok": True, "result": {"status": "closed"}})
                return 0
            if not isinstance(method, str) or method not in {
                "desktop_update_recovery_state", "desktop_update_recovery_handoff",
            }:
                raise UpdateError(
                    "recovery_only", "Only update recovery is available in this window.",
                )
            action = "state" if method == "desktop_update_recovery_state" else "handoff"
            result = service.handle(action, params)
            _write(output, {"id": identity, "ok": True, "result": result})
        except (UpdateError, OwnerConflict) as error:
            if isinstance(error, UpdateError):
                code, message = error.code, str(error)
            else:
                code = "application_busy"
                message = "Close the other owned app or wait for its helper."
            _write(output, {
                "id": identity, "ok": False, "error": {"code": code, "message": message},
            })
    return 0
