"""Small, private-content-free snapshot of one admitted response attempt."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable


def operation_category(name: str | None) -> str:
    """Reduce tool names to a fixed vocabulary before exposing live activity."""
    name = re.sub(r"^tool_[a-f0-9]{12}_", "", name or "")
    if name in {
        "read_file", "read_reference", "read_campaign_file", "read_campaign_reference",
        "get_content_definition", "inspect_builder_references", "read_session_history",
        "list_files", "list_directory", "search_files", "search_knowledge",
        "search_campaign_knowledge", "search_library", "discover_library", "get_library_item",
    }:
        return "read"
    if name in {"write_file", "edit_file", "create_file", "copy_builder_reference"}:
        return "write"
    if name in {
        "check_participant_draft", "check_participant_draft_file", "check_character_profile_draft",
        "test_ruleset_draft", "check_campaign_plan", "check_campaign_setting",
    }:
        return "validate"
    if name in {
        "publish_participant_artifact", "publish_participant_artifact_file",
        "publish_character_profile", "publish_campaign_plan", "publish_campaign_setting",
        "publish_ruleset_candidate",
    }:
        return "save"
    if name == "ask_question":
        return "question"
    return "action"


class AttemptProgress:
    """Track observed transitions; polling never changes the last-activity clock."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._started = self._last = clock()
        self._ended: float | None = None
        self._phase = "queued"
        self._operation: str | None = None
        self._last_operation: str | None = None
        self._correction = False
        self._revision = 0

    def update(self, phase: str, tool: str | None = None) -> None:
        if phase not in {
            "queued", "working", "provider", "tool", "retrying", "compacting", "stopping",
        }:
            raise ValueError("Unsupported activity phase")
        with self._lock:
            if self._ended is not None or self._phase == "stopping":
                return
            self._last = self._clock()
            self._revision += 1
            if phase == "retrying":
                self._correction = True
            if phase == "provider" and self._correction:
                phase = "retrying"
            elif phase == "working":
                self._correction = False
            self._phase = phase
            self._operation = operation_category(tool) if phase == "tool" else None
            if self._operation:
                self._last_operation = self._operation

    def observe(self) -> None:
        """Record receipt without exposing provider text or restarting the phase."""
        with self._lock:
            if self._ended is not None or self._phase == "stopping":
                return
            self._last = self._clock()
            if self._phase in {"provider", "retrying", "queued"}:
                self._phase = "working"
                self._correction = False
                self._revision += 1

    def expect_correction(self) -> None:
        """Remember a rejected call; Retrying starts only when another request is admitted."""
        with self._lock:
            if self._ended is None:
                self._correction = True

    def finish(self, status: str) -> dict[str, object]:
        if status not in {"completed", "stopped", "failed", "interrupted"}:
            raise ValueError("Unsupported terminal activity status")
        with self._lock:
            if self._ended is None:
                self._ended = self._clock()
                self._phase = status
                self._operation = None
                self._revision += 1
            return self._snapshot(self._ended)

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return self._snapshot(self._ended if self._ended is not None else self._clock())

    def _snapshot(self, now: float) -> dict[str, object]:
        return {
            "phase": self._phase,
            "operation": self._operation,
            "last_operation": self._last_operation,
            "elapsed_ms": max(0, int((now - self._started) * 1000)),
            "quiet_ms": max(0, int((now - self._last) * 1000)),
            "revision": self._revision,
        }
