"""Packaged two-phase helper: authenticate/acknowledge, commit, await owned exits, apply once."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO, Protocol, TextIO

from sylvia_gm import __version__

from .checkpoint import RESTORE_CONFIRMATION, acquire_update_data_lock
from .identity import runtime_identity
from .manifest import KeyRing, UpdateError, Version, bounded_json
from .preparation import PreparedUpdate
from .processes import OwnedProcesses, ProcessReceipt, WindowsHandles
from .progress import Progress, start_progress
from .recovery import KEEP_DATA, RecoveryOperation
from .restart import RestartHandshake
from .runtime import child_environment, system_child_libraries
from .storage import atomic_receipt, operation_id


class Child(Protocol):
    def poll(self) -> int | None: ...


class ProcessBarrier(Protocol):
    def all_exited(self) -> bool: ...


def _no_progress(_phase: str) -> None:
    pass


def probe_owner(executable: Path) -> object:
    with (
        system_child_libraries(),
        subprocess.Popen(
            [str(executable), "--desktop-update-identity"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=executable.parent,
            env=child_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW,
        ) as child,
    ):
        assert child.stdout is not None
        raw = child.stdout.read(4097)
        child.stdout.close()
        if child.wait() != 0:
            raise UpdateError(
                "wrong_owner_identity", "The updated owner could not report its identity."
            )
    return bounded_json(raw, maximum=4096)


def launch_app(executable: Path, arguments: tuple[str, ...]) -> Child:
    with system_child_libraries():
        return subprocess.Popen(
            [str(executable), *arguments],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=executable.parent,
            env=child_environment(),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )


def apply_and_restart(
    prepared: PreparedUpdate,
    processes: ProcessBarrier,
    *,
    probe: Callable[[Path], object] = probe_owner,
    launch: Callable[[Path, tuple[str, ...]], Child] = launch_app,
    pause: Callable[[float], None] = time.sleep,
    notify: Callable[[str], None] = _no_progress,
) -> None:
    child_environment()
    prepared.require_selected()
    row = prepared.store.load()
    if row is None or row["phase"] not in {"closing", "applying", "awaiting_restart", "confirmed"}:
        raise UpdateError("recovery_required", "The native owner has not armed this update.")
    lock = acquire_update_data_lock(prepared.stage / "helper-runtime")
    try:
        _apply_and_restart_locked(
            prepared, processes, probe=probe, launch=launch, pause=pause, notify=notify,
        )
    finally:
        lock.close()


def _apply_and_restart_locked(
    prepared: PreparedUpdate,
    processes: ProcessBarrier,
    *,
    probe: Callable[[Path], object] = probe_owner,
    launch: Callable[[Path, tuple[str, ...]], Child] = launch_app,
    pause: Callable[[float], None] = time.sleep,
    notify: Callable[[str], None] = _no_progress,
) -> None:
    child_environment()
    prepared.require_selected()
    row = prepared.store.load()
    if row is not None and row["phase"] in {"awaiting_restart", "confirmed"}:
        handshake = RestartHandshake(prepared.root, prepared.identity, prepared.attempt)
        if handshake.ticket.exists() and handshake.confirmed():
            if row["phase"] != "confirmed":
                prepared.confirm()
            return
        raise UpdateError(
            "recovery_required", "Reconcile the previous startup before another launch."
        )
    if row is None or row["phase"] not in {"closing", "applying"}:
        raise UpdateError(
            "recovery_required", "Choose an explicit recovery for this interrupted update."
        )
    notify("waiting")
    while not processes.all_exited():
        pause(0.05)  # No kill, unrelated process search or artificial whole-update cutoff.
    notify("applying")
    prepared.apply(processes.all_exited)
    notify("verifying")
    handshake = prepared.create_restart(probe(prepared.live / "sylvia-gm-owner.exe"))
    executable = prepared.live / (
        "Sylvia GM.exe" if prepared.distribution == "portable" else "sylvia-gm.exe"
    )
    notify("starting")
    child = handshake.launch_once(lambda arguments: launch(executable, arguments))
    notify("confirming")
    while not handshake.confirmed():
        if child.poll() is not None:
            raise UpdateError(
                "startup_failed", "The updated app exited without confirming its data."
            )
        pause(0.05)
    prepared.confirm()


def _read(stream: BinaryIO) -> object:
    raw = stream.readline(65537)
    if not raw or not raw.endswith(b"\n"):
        raise UpdateError("invalid_helper_request", "The bounded helper request is incomplete.")
    return bounded_json(raw, maximum=65536)


def recover_and_restart(
    prepared: PreparedUpdate,
    processes: ProcessBarrier,
    choice: str,
    *,
    probe: Callable[[Path], object] = probe_owner,
    launch: Callable[[Path, tuple[str, ...]], Child] = launch_app,
    pause: Callable[[float], None] = time.sleep,
    restart_nonce: str | None = None,
    notify: Callable[[str], None] = _no_progress,
) -> None:
    child_environment()
    prepared.require_selected()
    lock = acquire_update_data_lock(prepared.stage / "helper-runtime")
    try:
        _recover_and_restart_locked(
            prepared, processes, choice, probe=probe, launch=launch, pause=pause,
            restart_nonce=restart_nonce, notify=notify,
        )
    finally:
        lock.close()


def _recover_and_restart_locked(
    prepared: PreparedUpdate,
    processes: ProcessBarrier,
    choice: str,
    *,
    probe: Callable[[Path], object] = probe_owner,
    launch: Callable[[Path, tuple[str, ...]], Child] = launch_app,
    pause: Callable[[float], None] = time.sleep,
    restart_nonce: str | None = None,
    notify: Callable[[str], None] = _no_progress,
) -> None:
    child_environment()
    recovery = RecoveryOperation(prepared)
    phase = recovery._phase()
    if phase in {"rollback_awaiting_restart", "rolled_back"} or (
        phase == "rollback_ready" and recovery.handshake.exists()
    ):
        if restart_nonce is None and phase != "rollback_ready" and recovery.handshake.confirmed():
            if phase != "rolled_back":
                recovery.confirm()
            return
        if restart_nonce is None or phase == "rolled_back":
            raise UpdateError("recovery_required", "Reconcile the earlier recovery startup.")
    elif restart_nonce is not None:
        raise UpdateError("stale_restart", "The reviewed recovery restart changed.")
    notify("waiting")
    while not processes.all_exited():
        pause(0.05)
    if restart_nonce is not None:
        if choice != KEEP_DATA:
            raise UpdateError("invalid_restart", "A restart retry cannot restore data again.")
        notify("verifying")
        handshake = recovery.retry_restart(
            restart_nonce, probe(prepared.live / "sylvia-gm-owner.exe"), processes.all_exited,
        )
    else:
        notify("restoring")
        recovery._rollback_locked(choice, processes.all_exited)
        notify("verifying")
        handshake = recovery.create_restart(probe(prepared.live / "sylvia-gm-owner.exe"))
    executable = prepared.live / (
        "Sylvia GM.exe" if prepared.distribution == "portable" else "sylvia-gm.exe"
    )
    notify("starting")
    child = handshake.launch_once(lambda arguments: launch(executable, arguments))
    notify("confirming")
    while not handshake.confirmed():
        if child.poll() is not None:
            raise UpdateError("startup_failed", "The restored app did not confirm startup.")
        pause(0.05)
    recovery.confirm()


def _write(stream: TextIO, value: object) -> None:
    try:
        stream.write(json.dumps(value, separators=(",", ":")) + "\n")
        stream.flush()
    except OSError:
        if stream is sys.stdout:
            # The native parent may already have closed. Redirect only this helper's
            # failed reply descriptor so Python's final flush cannot change its exit to 120.
            with open(os.devnull, "w") as sink:
                os.dup2(sink.fileno(), stream.fileno())
        raise


def _failure_code(error: Exception) -> str:
    if isinstance(error, OSError) and getattr(error, "winerror", None) in {32, 33}:
        return "file_locked"
    if isinstance(error, OSError) and getattr(error, "winerror", None) == 5:
        return "file_access"
    code = getattr(error, "code", "helper_failed")
    if not isinstance(code, str) or re.fullmatch(r"[a-z_]{1,64}", code) is None:
        return "helper_failed"
    return code


def main(
    input_stream: BinaryIO | None = None, output_stream: TextIO | None = None, *,
    presentation_factory: Callable[[], Progress] = start_progress,
) -> int:
    # Only the packaged public pin supplies trust. No CLI/env/IPC key override exists.
    from .service import PUBLIC_KEYS

    source, output = input_stream or sys.stdin.buffer, output_stream or sys.stdout
    processes: OwnedProcesses | None = None
    prepared: PreparedUpdate | None = None
    operation_lock = None
    committed = False
    recovering = False
    presentation: Progress | None = None
    try:
        keyring = KeyRing(PUBLIC_KEYS)
        if not keyring.keys:
            raise UpdateError("trust_not_configured", "An approved updater bootstrap is required.")
        child_environment()
        row = _read(source)
        recovering = isinstance(row, dict) and row.get("type") == "desktop_update_recover"
        keys = {"type", "operation_id", "attempt", "data_root", "live", "distribution", "processes"}
        if (
            not isinstance(row, dict)
            or set(row) != keys | ({"choice", "restart_nonce"} if recovering else set())
            or not isinstance(row["type"], str)
            or row["type"] not in {"desktop_update_apply", "desktop_update_recover"}
            or (recovering and (
                not isinstance(row["choice"], str)
                or row["choice"] not in {KEEP_DATA, RESTORE_CONFIRMATION}
            ))
            or (recovering and row["restart_nonce"] is not None and (
                not isinstance(row["restart_nonce"], str)
                or re.fullmatch(r"[0-9a-f]{32}", row["restart_nonce"]) is None
            ))
            or not isinstance(row["distribution"], str)
            or row["distribution"] not in {"installed", "portable"}
            or not isinstance(row["processes"], list)
            or not 2 <= len(row["processes"]) <= 3
        ):
            raise UpdateError("invalid_helper_request", "The helper launch context is invalid.")
        for name in ("data_root", "live"):
            if (
                not isinstance(row[name], str)
                or not 0 < len(row[name]) <= 4096
                or not Path(row[name]).is_absolute()
            ):
                raise UpdateError(
                    "invalid_helper_request", "The selected helper location is invalid."
                )
        processes = OwnedProcesses(
            [ProcessReceipt.parse(item) for item in row["processes"]], WindowsHandles()
        )
        prepared = PreparedUpdate(
            Path(row["data_root"]),
            Path(row["live"]),
            operation_id(row["operation_id"]),
            operation_id(row["attempt"]),
            current=Version.parse(__version__),
            target=str(runtime_identity()["target"]),
            distribution=str(row["distribution"]),
            verify=keyring.verify,
        )
        prepared.require_selected()
        operation_lock = acquire_update_data_lock(prepared.stage / "helper-runtime")
        prepared.require_selected()
        presentation = presentation_factory()  # Prove presentation before the parent may close.
        nonce = uuid.uuid4().hex
        _write(
            output,
            {
                "type": "ready",
                "operation_id": prepared.identity,
                "attempt": prepared.attempt,
                "helper_nonce": nonce,
            },
        )
        commit = _read(source)
        if commit != {
            "action": "commit",
            "operation_id": prepared.identity,
            "attempt": prepared.attempt,
            "helper_nonce": nonce,
        }:
            raise UpdateError(
                "helper_not_committed", "The native parent did not commit this update."
            )
        committed = True
        if recovering:
            restart_nonce = row["restart_nonce"] if isinstance(row["restart_nonce"], str) else None
            _recover_and_restart_locked(
                prepared, processes, str(row["choice"]), restart_nonce=restart_nonce,
                notify=presentation.stage,
            )
        else:
            _apply_and_restart_locked(prepared, processes, notify=presentation.stage)
        return 0
    except Exception as error:
        code = _failure_code(error)
        if prepared is not None and committed:
            with suppress(Exception):
                prepared.require_selected()
                # Failure belongs to this attempt. Never overwrite a newer global selection
                # after an explicit recovery has fenced this helper or prepared a new attempt.
                handshake = RestartHandshake(
                    prepared.root, prepared.identity, prepared.attempt, recovery=recovering,
                )
                path = handshake.error_receipt if not recovering or handshake.exists() else (
                    prepared.stage / "rollback-helper-error.json"
                )
                atomic_receipt(path, {"code": code})
        # The old app may have closed its reply pipe. The durable status does not depend on it.
        with suppress(OSError):
            _write(output, {"type": "error", "code": code})
        if committed and presentation is not None:
            # No global writes follow: let recovery proceed even while the user reads this error.
            if operation_lock is not None:
                operation_lock.close()
                operation_lock = None
            if processes is not None:
                with suppress(UpdateError):
                    processes.close()
                processes = None
            presentation.fail(code)
        return 2
    finally:
        try:
            if presentation is not None:
                presentation.close()
        finally:
            if operation_lock is not None:
                operation_lock.close()
            if processes is not None:
                with suppress(UpdateError):
                    processes.close()
