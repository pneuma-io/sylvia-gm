"""F6 second-instance probe — detect an existing owner process.

The desktop supervisor must answer one question on startup: *is another owner
process already live for this database?*  If yes, the new instance either
attaches to the existing owner (rendering projections against its local API) or
exits with a clear recovery action.  It must never become a second writer.

This module probes the owner lock file **without acquiring it**.  The live
``SQLiteAuthorityStore`` holds an OS lock (``msvcrt.locking`` on Windows,
``fcntl.flock`` on POSIX) on ``{database}.authority-owner`` for its entire
lifetime.  A second instance can test whether that lock is held by attempting
its own non-blocking acquire on the same file; success means no owner is live,
failure means one is.

The probe never touches the SQLite database itself — only the lock sidecar
file — so it honours the "no client database access" boundary: a second
instance that decides to attach as a client never opens the database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


@dataclass(frozen=True)
class OwnerProbeResult:
    """Outcome of a second-instance ownership probe."""

    #: ``True`` when another process holds the owner lock for this database.
    owner_live: bool

    #: Path to the lock sidecar file that was probed.
    lock_path: Path

    #: Diagnostic reason suitable for a recovery UI (never contains secrets).
    reason: str


def _probe_lock_file(lock_path: Path) -> tuple[bool, str]:
    """Return ``(held, reason)`` by attempting a non-blocking lock on the sidecar.

    This never *acquires* a lasting lock — on success the lock is released
    immediately (it was free, so no owner is live); on failure we learn an
    owner is live and leave the file untouched.
    """

    probe = lock_path.open("a+b")
    probe.seek(0, os.SEEK_END)
    if probe.tell() == 0:
        probe.write(b"0")
        probe.flush()
    probe.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(probe.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(probe.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(  # type: ignore[attr-defined]
                probe.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
            )
            fcntl.flock(probe.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]
    except OSError:
        probe.close()
        return True, "owner lock is held by a live process"
    probe.close()
    return False, "owner lock is free"


def probe_owner_live(database_path: Path) -> OwnerProbeResult:
    """Probe whether an owner process already holds the lock for ``database_path``.

    Uses the same sidecar path the live ``SQLiteAuthorityStore`` uses
    (``{database}.authority-owner``), so the probe reflects real ownership
    state.  The probe file handle is closed before returning; this function
    never holds a lock across the call.
    """

    lock_path = database_path.resolve().with_name(
        f"{database_path.resolve().name}.authority-owner.owner.lock"
    )
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held, reason = _probe_lock_file(lock_path)
    return OwnerProbeResult(owner_live=held, lock_path=lock_path, reason=reason)


def write_recover_hint(database_path: Path, message: str) -> Path:
    """Append a human-readable recovery hint beside the lock for a stale owner.

    The supervisor writes this when the port is unreachable but the lock is
    held — a likely crash.  It never contains secrets; the owner handshake
    secret is ephemeral and already erased.
    """

    hint_path = database_path.resolve().with_name(
        f"{database_path.resolve().name}.authority-owner.recover.txt"
    )
    hint_path.parent.mkdir(parents=True, exist_ok=True)
    hint_path.write_text(message, encoding="utf-8")
    return hint_path


__all__ = ("OwnerProbeResult", "probe_owner_live", "write_recover_hint")


def _unused_binaryio_marker() -> BinaryIO:  # pragma: no cover - typing guard
    """Kept so ``BinaryIO`` import is not flagged as unused on type-checkers
    that do not understand the module-level re-exports above."""

    raise NotImplementedError
