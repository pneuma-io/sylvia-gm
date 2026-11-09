"""Explicit asynchronous check/download lifecycle with durable interruption recovery."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from .discovery import discover, retain_current_manifest
from .manifest import (
    MANIFEST_LIMIT,
    Manifest,
    UpdateError,
    Version,
    bounded_json,
    parse_current_recovery_manifest,
    parse_manifest,
)
from .payload import verify_artifact
from .storage import UpdateStore, atomic_receipt, checked_file, operation_id
from .transport import Transport, download_artifact

_APPLY_PHASES = frozenset(
    {"preparing", "prepared", "closing", "applying", "awaiting_restart", "recovery_required",
     "rolling_back", "rollback_ready", "rollback_awaiting_restart"}
)


class UpdateController:
    """Native-owned context in; operation IDs out. No renderer-selected URLs, paths or commands."""

    def __init__(
        self,
        *,
        root: Path,
        current: Version,
        target: str,
        distribution: str,
        transport: Transport,
        verify: Callable[[str, bytes, bytes], None],
    ) -> None:
        self.store = UpdateStore(root)
        self.current, self.target, self.distribution = current, target, distribution
        self.transport, self.verify = transport, verify
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._identity: str | None = None
        self._phase = "idle"
        self._candidate: Manifest | None = None
        self._error: UpdateError | None = None
        self._received = 0
        self._total: int | None = None
        self._downloaded = False
        self._transfer_kind = "update"
        self._prepared_attempt: str | None = None
        self._restore()

    def _restore(self) -> None:
        row = self.store.load()
        if row is None:
            return
        self._identity = operation_id(row["operation_id"])
        phase = str(row["phase"])
        if phase in _APPLY_PHASES:
            self._phase = "recovery_required"
            return
        if phase in {"available", "verified", "deferred"}:
            try:
                raw = checked_file(
                    self.store.stage(self._identity) / "manifest.json", maximum=MANIFEST_LIMIT
                )
                self._candidate = parse_manifest(
                    raw,
                    current=self.current,
                    target=self.target,
                    distribution=self.distribution,
                    verify=self.verify,
                )
                if row["version"] != self._candidate.version.text:
                    raise UpdateError(
                        "invalid_journal", "The stored update identity is inconsistent."
                    )
                artifact = self.store.stage(self._identity) / "artifact.verified"
                if phase == "verified" or artifact.exists() or artifact.is_symlink():
                    verify_artifact(artifact, self._candidate)
                    if self.distribution == "installed":
                        previous = parse_current_recovery_manifest(
                            checked_file(
                                self.store.stage(self._identity) / "previous-manifest.json",
                                maximum=MANIFEST_LIMIT,
                            ),
                            current=self.current,
                            target=self.target,
                            distribution=self.distribution,
                            verify=self.verify,
                        )
                        verify_artifact(
                            self.store.stage(self._identity)
                            / "previous-installer/artifact.verified",
                            previous,
                        )
                    self._downloaded = True
                self._phase = phase
            except UpdateError as error:
                self._phase, self._error = "error", error
            return
        self._phase = "interrupted" if phase in {"checking", "downloading"} else phase

    def state(self) -> dict[str, object]:
        with self._lock:
            candidate = self._candidate
            return {
                "phase": self._phase,
                "operation_id": self._identity,
                "current_version": self.current.text,
                "channel": self.current.channel,
                "target": self.target,
                "distribution": self.distribution,
                "source": "Official pneuma-io/sylvia-gm GitHub releases",
                "version": candidate.version.text if candidate else None,
                "notes": candidate.notes if candidate else "",
                "received": self._received,
                "total": self._total,
                "downloaded": self._downloaded,
                "transfer_kind": self._transfer_kind,
                "prepared_attempt": self._prepared_attempt,
                "busy": self._thread is not None and self._thread.is_alive(),
                "error_code": self._error.code if self._error else None,
                "message": str(self._error) if self._error else None,
            }

    def _phase_to(self, phase: str, error: UpdateError | None = None) -> None:
        assert self._identity is not None
        self.store.save(
            self._identity,
            phase,
            self._candidate.version.text if self._candidate else None,
            error.code if error else None,
        )
        self._phase, self._error = phase, error
        self.store.prune_empty_terminal(self._identity)

    def _require(self, identity: str) -> None:
        if operation_id(identity) != self._identity:
            raise UpdateError("stale_operation", "This update is no longer selected.")

    def _idle_worker(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            raise UpdateError("update_busy", "The current update operation is still finishing.")

    def _start(self, action: Callable[[], None]) -> None:
        identity = self._identity
        cancel = self._cancel

        def work() -> None:
            try:
                action()
            except Exception as cause:
                error = (
                    cause
                    if isinstance(cause, UpdateError)
                    else UpdateError(
                        "update_failed",
                        "The update operation failed. The current application is unchanged.",
                    )
                )
                with self._lock:
                    if identity != self._identity:
                        return
                    phase = "cancelled" if cancel.is_set() else "error"
                    try:
                        self._phase_to(phase, error)
                    except UpdateError as journal_error:
                        self._phase, self._error = "error", journal_error

        self._thread = threading.Thread(target=work, name="sylvia-desktop-update", daemon=True)
        try:
            self._thread.start()
        except RuntimeError as cause:
            error = UpdateError(
                "worker_unavailable", "The update worker could not start. You can retry."
            )
            self._phase_to("error", error)
            raise error from cause

    def check(self) -> dict[str, object]:
        with self._lock:
            self._idle_worker()
            if self._phase in _APPLY_PHASES:
                raise UpdateError(
                    "recovery_required", "Finish update recovery before checking again."
                )
            self._identity, stage = self.store.create()
            self._cancel = threading.Event()
            self._candidate, self._error = None, None
            self._downloaded = False
            self._transfer_kind = "update"
            self._received, self._total = 0, None
            self._phase_to("checking")

            def action() -> None:
                candidate = discover(
                    self.transport,
                    self._cancel,
                    current=self.current,
                    target=self.target,
                    distribution=self.distribution,
                    verify=self.verify,
                )
                with self._lock:
                    if self._cancel.is_set():
                        raise UpdateError("cancelled", "The update check was cancelled.")
                    if candidate is not None:
                        # This is signed metadata, never campaign data or provider credentials.
                        atomic_receipt(
                            stage / "manifest.json", bounded_json(candidate.envelope_bytes)
                        )
                    self._candidate = candidate
                    self._phase_to("available" if candidate else "current")

            self._start(action)
            return self.state()

    def download(self, identity: str) -> dict[str, object]:
        with self._lock:
            self._require(identity)
            self._idle_worker()
            if (
                self._phase not in {"available", "deferred"}
                or self._candidate is None
                or self._downloaded
            ):
                raise UpdateError(
                    "invalid_phase", "Check for an available update before downloading."
                )
            manifest = self._candidate
            stage = self.store.stage(identity)
            self._cancel = threading.Event()
            self._phase_to("downloading")

            def progress(received: int, total: int | None) -> None:
                with self._lock:
                    self._received, self._total = received, total

            def action() -> None:
                if self.distribution == "installed":
                    with self._lock:
                        self._transfer_kind = "previous_installer"
                        self._received, self._total = 0, None
                    previous = retain_current_manifest(
                        self.transport,
                        self._cancel,
                        current=self.current,
                        target=self.target,
                        distribution=self.distribution,
                        verify=self.verify,
                    )
                    atomic_receipt(
                        stage / "previous-manifest.json", bounded_json(previous.envelope_bytes)
                    )
                    retained = stage / "previous-installer"
                    retained.mkdir()
                    download_artifact(self.transport, previous, retained, self._cancel, progress)
                    with self._lock:
                        self._transfer_kind = "update"
                        self._received, self._total = 0, None
                download_artifact(self.transport, manifest, stage, self._cancel, progress)
                with self._lock:
                    if self._cancel.is_set():
                        raise UpdateError("cancelled", "The update download was cancelled.")
                    self._downloaded = True
                    self._phase_to("verified")

            self._start(action)
            return self.state()

    def cancel(self, identity: str) -> dict[str, object]:
        with self._lock:
            self._require(identity)
            if self._phase not in {"checking", "downloading"}:
                raise UpdateError(
                    "invalid_phase", "This update can no longer be cancelled as a download."
                )
            self._cancel.set()
            return self.state()

    def prepare(
        self, identity: str, action: Callable[[], str], on_failure: Callable[[], None]
    ) -> dict[str, object]:
        """Run a checkpoint only after the owning service reserves the idle owner."""
        with self._lock:
            self._require(identity)
            self._idle_worker()
            if not self._downloaded or self._phase not in {"verified", "deferred", "error"}:
                raise UpdateError("invalid_phase", "Download and verify this update first.")
            self._prepared_attempt = None
            self._phase_to("preparing")

            def work() -> None:
                try:
                    attempt = operation_id(action())
                    with self._lock:
                        self._prepared_attempt = attempt
                        self._phase_to("prepared")
                except Exception:
                    on_failure()
                    raise

            try:
                self._start(work)
            except Exception:
                on_failure()
                raise
            return self.state()

    def defer(self, identity: str) -> dict[str, object]:
        with self._lock:
            self._require(identity)
            self._idle_worker()
            if self._phase not in {"available", "verified", "deferred", "prepared"}:
                raise UpdateError("invalid_phase", "This update cannot be deferred at this stage.")
            self._prepared_attempt = None
            self._phase_to("deferred")
            return self.state()

    def arm_shutdown(self, identity: str) -> None:
        """Block ordinary data startup before the reserved owner releases its ownership lock."""
        with self._lock:
            self._require(identity)
            self._idle_worker()
            if self._phase != "prepared":
                raise UpdateError("invalid_phase", "The update is not prepared for shutdown.")
            self._phase_to("closing")

    def close(self) -> None:
        # Network reads have a bounded transport timeout. Closing never starts apply or a retry.
        self._cancel.set()

    def wait(self) -> None:
        """Keep the owner's database alive until an admitted checkpoint finishes."""
        worker = self._thread
        if worker is not None and worker is not threading.current_thread():
            worker.join()
