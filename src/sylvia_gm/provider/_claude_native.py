"""Claude Code's native transcript under the context ledger: projection inputs, collection, cleanup.

Claude Code builds each request from Sylvia's transcript file, its stdin prompt and the context
records it writes itself (environment, model, date). Under the context ledger Sylvia runs each
step with a fresh ``--session-id`` and session persistence on, copies those context records from
the session file the CLI wrote, and replays them in every later transcript, so consecutive
requests extend each other. The session file holds a full copy of the request, including
GM-private state: it is deleted after every step, and leftovers are swept before each step.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The text Claude Code itself inserts after a trailing user record; Sylvia writes it instead.
FILLER_TEXT = "No response requested."
#: The CLI marks the assistant turn it inserts on resume with this model name.
CLI_INSERTED_MODEL = "<synthetic>"
#: The audit description of the CLI session file under the context ledger.
TRANSIENT_SESSION = "transient_deleted_after_step"

#: A session file or step folder another process may still be using is only swept once it has
#: not been modified for this long; this process deletes its own finished steps directly.
STALE_AFTER_SECONDS = 15 * 60

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


@dataclass(frozen=True)
class NativeCollection:
    """What one step's session file said: the CLI's context records, or that they are unknown."""

    status: str  # "collected" or "unknown"
    records: tuple[dict[str, Any], ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class NativeRoute:
    """The ledger's per-request boundaries and saved native records for one ``claude_code`` wire.

    The wire is ``stable_count`` leading system messages followed by ``entry_count`` journal
    entries. ``boundaries`` are the journal lengths at which earlier requests of this journal
    generation ended (each strictly between 0 and ``entry_count``); ``native_records`` maps such
    a boundary to the context records the CLI wrote after that request's prompt.
    """

    stable_count: int
    entry_count: int
    boundaries: tuple[int, ...] = ()
    native_records: Mapping[int, Sequence[Mapping[str, Any]]] = field(default_factory=dict)
    #: Called once after the step with what collection found (only on a completed step).
    on_collected: Callable[[NativeCollection], Awaitable[None]] | None = None

    @property
    def first_step(self) -> bool:
        return not self.boundaries


#: Set by the GM adapter for the provider request it is about to send on ``claude_code``.
CURRENT_NATIVE_ROUTE: ContextVar[NativeRoute | None] = ContextVar(
    "sylvia_gm_claude_native_route", default=None
)


def session_files(config: Path, session_id: str) -> list[Path]:
    """The session file (and any per-session folder) the CLI wrote for ``session_id``."""
    projects = config / "projects"
    if not projects.is_dir():
        return []
    return [*projects.glob(f"*/{session_id}.jsonl"), *projects.glob(f"*/{session_id}")]


def _remove(path: Path) -> bool:
    # Windows can hold a file briefly after the child exits; retry before giving up.
    for attempt in range(5):
        try:
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt == 4:
                return False
            time.sleep(0.05 * (attempt + 1))
    return False


def inside(path: Path, home: Path) -> bool:
    """Is ``path`` (resolved) inside Sylvia's runtime home ``home`` (resolved)?"""
    try:
        path.resolve().relative_to(home.resolve())
    except (OSError, ValueError):
        return False
    return True


def _newest_change(path: Path) -> float:
    """The latest modification time of ``path`` and, for a folder, of anything inside it."""
    try:
        newest = path.stat().st_mtime
        if path.is_dir() and not path.is_symlink():
            for child in path.rglob("*"):
                try:
                    newest = max(newest, child.stat().st_mtime)
                except OSError:
                    continue
    except OSError:
        return float("inf")
    return newest


def delete_session(config: Path, session_id: str, home: Path) -> bool:
    """Delete everything the CLI persisted for one step; False if something could not go.

    Never touches a config directory outside Sylvia's runtime home (it could be a person's
    own Claude Code profile with their saved sessions).
    """
    if not inside(config, home):
        logger.warning("A Claude config directory outside Sylvia's runtime home was not touched.")
        return False
    removed = True
    for path in session_files(config, session_id):
        removed = _remove(path) and removed
    if not removed:
        logger.warning(
            "A Claude runtime session file could not be deleted yet; it is swept "
            "before the next step."
        )
    return removed


def sweep(
    config: Path, steps: Path, active: set[str], home: Path, *, now: float | None = None
) -> int:
    """Remove leftover session files and step folders.

    Only inside Sylvia's runtime home, only entries this process is not running, and only
    those unchanged for ``STALE_AFTER_SECONDS``: another owner process may share the home and
    be mid-step.
    """
    if not (inside(config, home) and inside(steps, home)):
        logger.warning(
            "A Claude config or step directory outside Sylvia's runtime home was not swept."
        )
        return 0
    cutoff = (time.time() if now is None else now) - STALE_AFTER_SECONDS
    count = 0
    projects = config / "projects"
    candidates = [*projects.glob("*/*")] if projects.is_dir() else []
    for path in candidates:
        name = path.name[:-6] if path.name.endswith(".jsonl") else path.name
        if (
            _UUID.fullmatch(name)
            and name not in active
            and _newest_change(path) < cutoff
            and _remove(path)
        ):
            count += 1
    if steps.is_dir():
        for path in steps.iterdir():
            if path.name not in active and _newest_change(path) < cutoff and _remove(path):
                count += 1
    return count


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a line the CLI is still writing
        if isinstance(row, dict):
            rows.append(row)
    return rows


def wait_for_flush(config: Path, session_id: str, prompt_content: Any, limit: float = 2.0) -> bool:
    """Before the step's process is closed, let the CLI finish writing this step's records.

    Claude Code writes the prompt and its context records before it sends the request, then
    its request bookkeeping (``api-request*``). Closing the process at a tool call can lose
    writes still queued, so wait until that bookkeeping follows the prompt record, or the file
    stops growing, for at most ``limit`` seconds. True when the records are complete.
    """
    deadline = time.monotonic() + limit
    last_size, quiet_since = -1, time.monotonic()
    while True:
        files = [p for p in session_files(config, session_id) if p.suffix == ".jsonl"]
        if files:
            rows = _rows(files[0])
            prompt = next(
                (
                    i
                    for i in range(len(rows) - 1, -1, -1)
                    if rows[i].get("type") == "user"
                    and isinstance(rows[i].get("message"), dict)
                    and rows[i]["message"].get("content") == prompt_content
                ),
                None,
            )
            if prompt is not None:
                if any(
                    str(row.get("type", "")).startswith("api-request") for row in rows[prompt + 1 :]
                ):
                    return True
                size = files[0].stat().st_size if files[0].exists() else -1
                if size != last_size:
                    last_size, quiet_since = size, time.monotonic()
                elif time.monotonic() - quiet_since >= 0.25:
                    return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


def collect(session_file: Path | None, prompt_content: Any) -> NativeCollection:
    """Copy the CLI's rendered context records that follow this step's prompt record.

    Returns ``unknown`` (never raises) when the file is missing or unreadable, when the prompt
    record is absent, or when the CLI inserted its own assistant turn before the prompt, which
    means Sylvia's transcript was not what the CLI needed.
    """
    if session_file is None or not session_file.is_file():
        return NativeCollection("unknown", reason="session_file_missing")
    try:
        rows = [
            json.loads(line)
            for line in session_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, UnicodeDecodeError, ValueError):
        return NativeCollection("unknown", reason="session_file_unreadable")
    if not all(isinstance(row, dict) for row in rows):
        return NativeCollection("unknown", reason="session_file_unreadable")
    prompt_index = None
    for index, row in enumerate(rows):
        message = row.get("message")
        if (
            row.get("type") == "user"
            and isinstance(message, dict)
            and message.get("content") == prompt_content
        ):
            prompt_index = index
    if prompt_index is None:
        return NativeCollection("unknown", reason="prompt_record_missing")
    for row in rows[:prompt_index]:
        message = row.get("message")
        if (
            row.get("type") == "assistant"
            and isinstance(message, dict)
            and message.get("model") == CLI_INSERTED_MODEL
        ):
            return NativeCollection("unknown", reason="cli_inserted_turn")
    records = []
    for row in rows[prompt_index + 1 :]:
        if row.get("type") != "attachment" or not row.get("rendered"):
            continue
        if not isinstance(row.get("attachment"), dict):
            return NativeCollection("unknown", reason="session_file_unreadable")
        copied = {"attachment": row["attachment"], "rendered": row["rendered"]}
        if "renderedRole" in row:
            copied["renderedRole"] = row["renderedRole"]
        records.append(copied)
    return NativeCollection("collected", tuple(records))
