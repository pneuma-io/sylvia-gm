"""Bounded, content-free owner diagnostics and deliberate local report snapshots.

Only the constants below admit text into automatic records. Exceptions, request
IDs, arguments, names, paths and model/tool payloads never cross this boundary.
The writer owns two files: a 512 KiB snapshot and its 512 KiB atomic replacement.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from sylvia_gm import __version__

MAX_SNAPSHOT_BYTES = 512 * 1024
MAX_STORAGE_BYTES = 2 * MAX_SNAPSHOT_BYTES
MAX_RECORDS = 512
MAX_AGE_SECONDS = 7 * 24 * 60 * 60
WRITE_INTERVAL_SECONDS = 1.0
RETRY_INTERVAL_SECONDS = 30.0
MAX_REPORT_TEXT = 4000
MAX_REPORT_BYTES = 2 * 1024 * 1024
MAX_CLIENT_FAULT_DELTA = 65535

# Group owner commands by fixed product actions; custom tool/model/package names
# are deliberately absent. Unknown methods become the literal "other_operation".
OPERATIONS = frozenset(
    [
        "handshake_status",
        "campaign_index",
        "campaign_creation_catalog",
        "delete_reusable_library_entry",
        "builder_chat_index",
        "start_import_repair",
        "create_builder_chat",
        "rename_builder_chat",
        "delete_builder_chat",
        "archive_builder_chat",
        "restore_builder_chat",
        "snapshot",
        "display_snapshot",
        "player_projection",
        "player_projection_page",
        "campaign_panels",
        "action_sequence",
        "compose_action_sequence",
        "execute_action_sequence",
        "player_action_availability",
        "preview_player_action",
        "execute_player_action",
        "preview_panel_action",
        "execute_panel_action",
        "panel_action_receipt",
        "campaign_sequel_context",
        "preview_campaign_sequel",
        "append_campaign_sequel",
        "package_candidates",
        "inspect_package_candidate",
        "package_candidate_text",
        "compare_package_candidates",
        "rift_template",
        "campaign_party_options",
        "model_choices",
        "provider_settings",
        "open_provider_setup",
        "chatgpt_connection",
        "admin_full_audit_status",
        "admin_full_audit_record",
        "export_admin_full_audit",
        "set_admin_full_audit",
        "detect_local_providers",
        "discover_provider_models",
        "validate_provider_model",
        "save_provider_settings",
        "remove_provider_settings",
        "set_default_model",
        "switch_session_model",
        "switch_session_reasoning",
        "inspect_provider_thinking",
        "session_context",
        "session_history_page",
        "ingest_builder_sources",
        "steer_session",
        "submit_session_input",
        "interrupt_stream",
        "interrupt_provider_stream",
        "retryable_interruption",
        "manual_retry_session",
        "disconnect_stream",
        "disconnect_provider_stream",
        "stream_heartbeat",
        "provider_stream_heartbeat",
        "narration_attempt_status",
        "provider_stream_status",
        "reconnect_stream",
        "reconnect_provider_stream",
        "install_package",
        "preview_character_card_file",
        "confirm_character_card_import",
        "install_package_file",
        "start_rift_campaign",
        "start_guided_campaign",
        "create_guided_campaign_draft",
        "navigate_guided_campaign_draft",
        "advance_guided_campaign_draft",
        "guided_campaign_contribution",
        "save_reusable_campaign_plan",
        "start_guided_creation_subflow",
        "return_guided_creation_subflow",
        "confirm_new_campaign",
        "begin_campaign_construction",
        "complete_campaign_construction",
        "model_guidance",
        "finalize_campaign_construction",
        "start_dm_session",
        "catalog",
        "execute_action",
        "resolve_mechanics_choice",
        "open_turn",
        "complete_turn",
        "stop_agent_session",
        "resume_agent_session",
        "rename_campaign",
        "archive_campaign",
        "restore_archived_campaign",
        "trash_campaign",
        "restore_trashed_campaign",
        "purge_campaign",
        "backup_database",
        "export_campaign_plan_file",
        "export_package_candidate",
        "export_package_candidate_file",
        "export_campaign",
        "export_campaign_file",
        "recover_campaign",
        "recover_campaign_file",
        "preview_local_report",
        "cancel_local_report",
        "save_local_report",
        "record_local_fault",
        "renderer",
    ]
)
EVENTS = frozenset(
    {
        "owner_started",
        "owner_stopped",
        "owner_interrupted",
        "owner_failed",
        "command_completed",
        "request_rejected",
        "renderer_error",
        "renderer_rejection",
    }
)
ERROR_CODES = frozenset(
    {
        "command_rejected",
        "internal_error",
        "provider_error",
        "context_compaction_failed",
        "stale_character",
        "display_load_cancelled",
        "other_rejection",
        "none",
    }
)
ERROR_TYPES = frozenset(
    [
        "OwnerProtocolError",
        "CommandRejected",
        "AuthorityError",
        "ConfigError",
        "ValueError",
        "TypeError",
        "RuntimeError",
        "KeyError",
        "OSError",
        "PermissionError",
        "FileNotFoundError",
        "TimeoutError",
        "MemoryError",
        "ContextCompactionError",
        "TokenLimitError",
        "ProviderContinuationRequiredError",
        "ProviderRefusalError",
        "ProviderPausedError",
        "ProviderOutputLimitError",
        "ProviderTimeoutError",
        "ModelCapacityError",
        "AuthenticationError",
        "RateLimitError",
        "ModelNotFoundError",
        "ProviderConnectionError",
        "InvalidRequestError",
        "IncompleteProviderStreamError",
        "InvalidToolArgumentsError",
        "InvalidResponseError",
        "ProviderUnavailableError",
        "ProviderError",
        "OtherError",
        "none",
    ]
)


def _choice(value: object, choices: frozenset[str], fallback: str) -> str:
    return value if type(value) is str and value in choices else fallback


def _number(value: object, maximum: float) -> float:
    if not isinstance(value, (float, int)) or isinstance(value, bool):
        return 0.0
    try:
        numeric = float(value)
    except OverflowError:
        return 0.0
    if not math.isfinite(numeric):
        return 0.0
    return round(max(0.0, min(maximum, numeric)), 1)


def _platform() -> dict[str, object]:
    """Use numeric OS versions, never host names or user-controlled platform strings."""
    result: dict[str, object] = {"os": "windows" if sys.platform == "win32" else "other"}
    if sys.platform == "win32":
        version = sys.getwindowsversion()
        result.update(major=version.major, minor=version.minor, build=version.build)
    return result


def _encode(records: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n").encode("ascii")
        for row in records
    )


class LocalDiagnostics:
    """Keep capture independent of disk I/O, with one bounded asynchronous snapshot writer."""

    def __init__(self, storage_root: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.path = storage_root / "diagnostics" / "runtime.jsonl"
        self.pending_path = self.path.with_name("runtime.pending.jsonl")
        self._clock = clock
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._records: OrderedDict[tuple[object, ...], dict[str, Any]] = OrderedDict()
        self._session = uuid.uuid4().hex
        self._generation = 0
        self._persisted = -1
        self._storage = "available"
        self._pending_report: tuple[str, str] | None = None
        self._closed = False
        self._load()
        previous = next(reversed(self._records.values()), None) if self._records else None
        if previous and previous["event"] != "owner_stopped":
            self.record("owner_interrupted")
        self.record("owner_started")
        self._thread: threading.Thread | None = threading.Thread(
            target=self._writer,
            name="local-diagnostics",
            daemon=True,
        )
        try:
            self._thread.start()
        except RuntimeError:
            self._thread = None
            self._storage = "unavailable"

    def _load(self) -> None:
        """Read a bounded current-format snapshot; legacy/raw objects are never exported."""
        try:
            if self.path.stat().st_size > MAX_SNAPSHOT_BYTES:
                return
            with self.path.open("rb") as stream:
                data = stream.read(MAX_SNAPSHOT_BYTES + 1)
            if len(data) > MAX_SNAPSHOT_BYTES:
                return
            now = self._clock()
            for line in data.splitlines()[-MAX_RECORDS:]:
                try:
                    row = json.loads(line)
                    if (
                        not isinstance(row, dict)
                        or row.get("format") != 1
                        or row.get("app_version") != __version__
                    ):
                        continue
                    first, last = row.get("first_seen"), row.get("last_seen")
                    if (
                        not isinstance(first, (int, float))
                        or not isinstance(last, (int, float))
                        or isinstance(first, bool)
                        or isinstance(last, bool)
                        or not now - MAX_AGE_SECONDS <= first <= last <= now
                    ):
                        continue
                    identity = row.get("id")
                    session = row.get("session")
                    if not all(
                        type(value) is str
                        and len(value) == 32
                        and all(c in "0123456789abcdef" for c in value)
                        for value in (identity, session)
                    ):
                        continue
                    projected = self._project(row)
                    projected.update(
                        id=identity,
                        session=session,
                        first_seen=first,
                        last_seen=last,
                        count=max(1, int(_number(row.get("count"), 2**31 - 1))),
                    )
                    self._records[(identity,)] = projected
                except (ValueError, TypeError, OverflowError, RecursionError):
                    continue
        except OSError:
            return

    @staticmethod
    def _project(fields: Mapping[str, Any]) -> dict[str, Any]:
        """Construct every stored field explicitly, including fixed failure categories."""
        event = _choice(fields.get("event"), EVENTS, "request_rejected")
        outcome = _choice(fields.get("outcome"), frozenset({"ok", "error"}), "ok")
        error_type = _choice(fields.get("error_type"), ERROR_TYPES, "OtherError")
        return {
            "format": 1,
            "event": event,
            "app_version": __version__,
            "build_version": __version__,
            "runtime": "packaged" if getattr(sys, "frozen", False) else "source",
            "platform": _platform(),
            "operation": _choice(fields.get("operation"), OPERATIONS, "other_operation"),
            "outcome": outcome,
            "severity": "error"
            if outcome == "error"
            else ("warning" if event == "owner_interrupted" else "info"),
            "error_code": _choice(fields.get("error_code"), ERROR_CODES, "other_rejection"),
            "error_type": error_type,
            "duration_ms": _number(fields.get("duration_ms"), 24 * 60 * 60 * 1000),
        }

    def record(
        self,
        event: str,
        *,
        operation: str | None = None,
        outcome: str = "ok",
        error_code: str | None = None,
        error_type: str | None = None,
        duration_ms: float | None = None,
        count_delta: int = 1,
    ) -> str:
        """Aggregate one minute of matching outcomes without inspecting request/error objects."""
        now = self._clock()
        row = self._project(
            {
                "event": event,
                "operation": operation,
                "outcome": outcome,
                "error_code": "none" if error_code is None else error_code,
                "error_type": "none" if error_type is None else error_type,
                "duration_ms": duration_ms,
            }
        )
        key = (
            self._session,
            int(now // 60),
            *(
                row[field]
                for field in ("event", "operation", "outcome", "error_code", "error_type")
            ),
        )
        with self._lock:
            if self._closed:
                return self._session
            if key in self._records:
                retained = self._records[key]
                retained["count"] = min(2**31 - 1, retained["count"] + count_delta)
                retained["last_seen"] = now
                retained["duration_ms"] = max(row["duration_ms"], retained["duration_ms"])
            else:
                retained = {
                    **row,
                    "id": uuid.uuid4().hex,
                    "session": self._session,
                    "first_seen": now,
                    "last_seen": now,
                    "count": count_delta,
                }
                self._records[key] = retained
                while len(self._records) > MAX_RECORDS:
                    self._records.popitem(last=False)
            self._generation += 1
            return str(retained["id"])

    def _snapshot(self) -> tuple[int, list[dict[str, Any]]]:
        with self._lock:
            cutoff = self._clock() - MAX_AGE_SECONDS
            expired = [key for key, row in self._records.items() if row["first_seen"] < cutoff]
            for key in expired:
                del self._records[key]
            if expired:
                self._generation += 1
            return self._generation, [dict(row) for row in self._records.values()]

    def _write_snapshot(self, rows: list[dict[str, Any]]) -> None:
        """Flush a replacement before atomic publication; an interrupted replacement is ignored."""
        data = _encode(rows)
        while len(data) > MAX_SNAPSHOT_BYTES:
            rows.pop(0)
            data = _encode(rows)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.pending_path.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(self.pending_path, self.path)
        # This is the prior journal owner's sole backup, never a campaign/audit export.
        self.path.with_name("runtime.previous.jsonl").unlink(missing_ok=True)

    def _writer(self) -> None:
        while True:
            generation, rows = self._snapshot()
            failed = False
            if generation != self._persisted:
                try:
                    self._write_snapshot(rows)
                    self._persisted = generation
                    self._storage = "available"
                except OSError:
                    self._storage = "unavailable"
                    failed = True
                    # No stderr/raw exception fallback, recursive logging or retry queue.
            if self._stop.is_set():
                with self._lock:
                    if failed or generation == self._generation:
                        return
                continue
            self._wake.wait(RETRY_INTERVAL_SECONDS if failed else WRITE_INTERVAL_SECONDS)
            self._wake.clear()

    def preview(self, *, kind: object, reproduction: object, feedback: object) -> dict[str, object]:
        """Freeze exact export bytes with text deliberately entered by the player."""
        if kind not in ("app_problem", "model_feedback"):
            raise ValueError("Choose an app problem or model feedback.")
        if any(
            type(value) is not str or len(value) > MAX_REPORT_TEXT
            for value in (reproduction, feedback)
        ):
            raise ValueError("Report fields must each contain at most 4,000 characters.")
        _, rows = self._snapshot()
        payload = {
            "format": "sylvia-local-report-v1",
            "application": {
                "version": __version__,
                "build_version": __version__,
                "runtime": "packaged" if getattr(sys, "frozen", False) else "source",
                "platform": _platform(),
            },
            "retention": {"max_storage_bytes": MAX_STORAGE_BYTES, "max_age_days": 7},
            "storage_status": self._storage,
            "report_kind": kind,
            "player_entered": {"reproduction_steps": reproduction, "feedback": feedback},
            "diagnostics": rows,
        }
        content = json.dumps(payload, ensure_ascii=True, indent=2, allow_nan=False) + "\n"
        if len(content.encode("utf-8")) > MAX_REPORT_BYTES:
            raise ValueError("The local report exceeds its size limit.")
        identity = uuid.uuid4().hex
        with self._lock:
            self._pending_report = (identity, content)
        return {"preview_id": identity, "content": content, "bytes": len(content.encode("utf-8"))}

    def client_fault(self, category: object, delta: object) -> dict[str, str]:
        """Accept a renderer failure category without accepting its Error or rejection value."""
        if category not in ("renderer_error", "renderer_rejection"):
            raise ValueError("Unknown app failure category.")
        if type(delta) is not int or not 1 <= delta <= MAX_CLIENT_FAULT_DELTA:
            raise ValueError("App failure count must be an integer from 1 to 65535.")
        identity = self.record(
            str(category),
            operation="renderer",
            outcome="error",
            error_code="internal_error",
            count_delta=delta,
        )
        return {"diagnostic_id": identity}

    def cancel(self, preview_id: str) -> None:
        with self._lock:
            if self._pending_report and self._pending_report[0] == preview_id:
                self._pending_report = None

    def save(self, preview_id: str, destination: Path) -> dict[str, object]:
        with self._lock:
            pending = self._pending_report
        if pending is None or pending[0] != preview_id:
            raise ValueError("Preview this report again before saving.")
        data = pending[1].encode("utf-8")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=".sylvia-report-",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except OSError:
            raise ValueError("Could not save the report. Choose a writable local file.") from None
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink(missing_ok=True)
        self.cancel(preview_id)
        return {"status": "saved", "bytes": len(data)}

    def close(self) -> None:
        """Request one final write; a stuck filesystem never holds owner shutdown indefinitely."""
        self.record("owner_stopped")
        with self._lock:
            self._closed = True
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
