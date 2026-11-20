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
import threading
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
    #: For a call a kept process answered in place: the CLI's own stored tool-result message.
    answer: dict[str, Any] | None = None


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
    #: For a boundary whose request a kept process served by answering its waiting call: the
    #: tool-result message that process stored, so a fresh process replays it exactly.
    native_answers: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)
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


#: Per runtime home, the session files and step folders this process failed to delete. They
#: hold full copies of requests, so they are retried at the start of every step whatever their
#: age; the age rule in ``sweep`` is only for leftovers of other processes or a crash.
_FAILED_DELETIONS: dict[Path, set[Path]] = {}
_FAILED_LOCK = threading.Lock()


def _remember_failed(home: Path, path: Path) -> None:
    with _FAILED_LOCK:
        _FAILED_DELETIONS.setdefault(home.resolve(), set()).add(path)


def retry_failed_deletions(home: Path) -> int:
    """Delete again what this process could not delete before; return how many went."""
    with _FAILED_LOCK:
        pending = set(_FAILED_DELETIONS.get(home.resolve(), ()))
    removed = 0
    for path in pending:
        if _remove(path):
            removed += 1
            with _FAILED_LOCK:
                _FAILED_DELETIONS.get(home.resolve(), set()).discard(path)
    return removed


def remove_step_folder(step: Path, home: Path) -> bool:
    """Remove one step's own folder; a failure is remembered and retried next step."""
    if _remove(step):
        return True
    _remember_failed(home, step)
    logger.warning(
        "A Claude runtime step folder could not be deleted yet; this process "
        "retries it at the start of its next step."
    )
    return False


def delete_session(config: Path, session_id: str, home: Path) -> bool:
    """Delete everything the CLI persisted for one step; False if something could not go.

    Never touches a config directory outside Sylvia's runtime home (it could be a person's
    own Claude Code profile with their saved sessions). Anything that cannot be deleted now is
    remembered and retried at the start of this process's next step.
    """
    if not inside(config, home):
        logger.warning("A Claude config directory outside Sylvia's runtime home was not touched.")
        return False
    removed = True
    for path in session_files(config, session_id):
        if not _remove(path):
            removed = False
            _remember_failed(home, path)
    if not removed:
        logger.warning(
            "A Claude runtime session file could not be deleted yet; this process retries it "
            "at the start of its next step."
        )
    return removed


def remove_debug_logs(cache: Path | None, home: Path) -> int:
    """Remove Claude Code's MCP debug logs kept under Sylvia's runtime home.

    Off Windows the runtime's cache folder is inside the runtime home, and the CLI writes one
    log per step under ``<cache>/claude-cli-nodejs/<working folder>/mcp-logs-*/``: the names
    of the tools it called, a session ID and the working folder's path. Nothing reads them and
    they are never trimmed, so they go with the step's other records. Only inside the runtime
    home; on Windows the CLI's cache is the person's own and is left alone.
    """
    if cache is None or not inside(cache, home):
        return 0
    root = cache / "claude-cli-nodejs"
    count = 0
    if root.is_dir():
        for folder in root.glob("*/mcp-logs-*"):
            if folder.is_dir() and not folder.is_symlink() and inside(folder, home):
                count += int(_remove(folder))
    return count


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
    # This process's own failed deletions first, whatever their age.
    count = retry_failed_deletions(home)
    cutoff = (time.time() if now is None else now) - STALE_AFTER_SECONDS
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


def _requests_after(rows: Sequence[Mapping[str, Any]], start: int) -> int:
    """How many model requests the CLI recorded after row ``start``."""
    return sum(1 for row in rows[start + 1:] if row.get("type") == "api-request")


#: What a replayed round's model message keeps: what Claude Code renders and merges rows by.
_ROUND_MESSAGE_KEYS = ("id", "type", "role", "model", "content", "stop_reason", "stop_sequence")
#: Never kept: the model's private reasoning stays out of session evidence, as the journal
#: leaves it out of every replayed assistant message.
_PRIVATE_BLOCKS = frozenset({"thinking", "redacted_thinking"})


def _round_row(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """A ToolSearch round message, for replay (deferred tool list); None if nothing is kept.

    The model's message keeps its rendered blocks without thinking, as every assistant message
    of the journal is replayed; a row that held only thinking is dropped. The CLI's answer
    keeps its message.
    """
    message = row.get("message")
    if row.get("type") not in {"assistant", "user"} or not isinstance(message, dict):
        return None
    if row["type"] == "assistant":
        content = message.get("content")
        if not isinstance(content, list):
            return None
        kept = [block for block in content
                if not (isinstance(block, dict) and block.get("type") in _PRIVATE_BLOCKS)]
        if not kept:
            return None
        message = {key: message[key] for key in _ROUND_MESSAGE_KEYS if key in message}
        message["content"] = kept
        return {"type": "assistant", "message": json.loads(json.dumps(message))}
    return {"type": "user", "message": json.loads(json.dumps(
        {key: message[key] for key in ("role", "content") if key in message}))}


def _is_final(row: Mapping[str, Any], final_message_id: str | None) -> bool:
    message = row.get("message")
    return (
        row.get("type") == "assistant" and final_message_id is not None
        and isinstance(message, dict) and message.get("id") == final_message_id
    )


def wait_for_flush(
    config: Path, session_id: str, prompt_content: Any, limit: float = 2.0, *,
    requests: int | None = None,
) -> bool:
    """Before the step's process is closed, let the CLI finish writing this step's records.

    Claude Code writes the prompt and its context records before it sends the request, then
    its request bookkeeping (``api-request*``). Closing the process at a tool call can lose
    writes still queued, so wait until that bookkeeping follows the prompt record, or the file
    stops growing, for at most ``limit`` seconds. True when the records are complete. With
    ``requests`` (a step that also made ToolSearch rounds), wait for that many requests'
    bookkeeping, which the CLI writes after each earlier round's messages.
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
                if requests is not None:
                    if _requests_after(rows, prompt) >= requests:
                        return True
                elif any(
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


def collect(
    session_file: Path | None, prompt_content: Any, *, final_message_id: str | None = None,
) -> NativeCollection:
    """Copy the CLI's rendered context records that follow this step's prompt record.

    Returns ``unknown`` (never raises) when the file is missing or unreadable, when the prompt
    record is absent, or when the CLI inserted its own assistant turn before the prompt, which
    means Sylvia's transcript was not what the CLI needed. With ``final_message_id`` (a step
    that made ToolSearch rounds, deferred tool list) the rounds' messages are copied too, in
    order with the context records, up to the model message with that id, which ended the
    step and is in the journal.
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
        if final_message_id is not None:
            if _is_final(row, final_message_id):
                break
            round_row = _round_row(row)
            if round_row is not None:
                records.append(round_row)
                continue
        if row.get("type") != "attachment" or not row.get("rendered"):
            continue
        if not isinstance(row.get("attachment"), dict):
            return NativeCollection("unknown", reason="session_file_unreadable")
        copied = {"attachment": row["attachment"], "rendered": row["rendered"]}
        if "renderedRole" in row:
            copied["renderedRole"] = row["renderedRole"]
        records.append(copied)
    return NativeCollection("collected", tuple(records))


def _answer_row(rows: list[dict[str, Any]], call_id: str) -> int | None:
    for index in range(len(rows) - 1, -1, -1):
        message = rows[index].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if (
            rows[index].get("type") == "user"
            and isinstance(content, list)
            and content
            and isinstance(content[0], dict)
            and content[0].get("type") == "tool_result"
            and content[0].get("tool_use_id") == call_id
        ):
            return index
    return None


def collect_answer(
    config: Path, session_id: str, call_id: str, limit: float = 2.0, *,
    final_message_id: str | None = None, requests: int | None = None,
) -> NativeCollection:
    """The tool-result message a kept process stored when its waiting call was answered.

    A kept process answers a call in place (the MCP tool result carries the result and the
    sealed records after it), so its conversation holds one nested tool-result message where a
    per-step transcript has the result, a filler and a separate prompt. Copying the CLI's own
    stored message, and the rendered context records it wrote after it, lets a later fresh
    process replay exactly what the kept one sent. ``unknown`` (never raises) when it is
    missing. With ``final_message_id`` and ``requests`` (ToolSearch rounds followed the answer,
    deferred tool list), the rounds' messages are copied as well, up to the model message that
    ended the step, once the CLI has recorded that many requests after the answer.
    """
    deadline = time.monotonic() + limit
    while True:
        files = [p for p in session_files(config, session_id) if p.suffix == ".jsonl"]
        rows = _rows(files[0]) if files else []
        index = _answer_row(rows, call_id)
        if index is not None and requests is not None and _requests_after(rows, index) < requests:
            if time.monotonic() < deadline:
                time.sleep(0.02)
                continue
            return NativeCollection("unknown", reason="answer_record_missing")
        if index is not None:
            records = []
            for row in rows[index + 1:]:
                if final_message_id is not None:
                    if _is_final(row, final_message_id):
                        break
                    round_row = _round_row(row)
                    if round_row is not None:
                        records.append(round_row)
                        continue
                if row.get("type") in {"user", "assistant"}:
                    break
                if row.get("type") == "attachment" and row.get("rendered"):
                    copied = {"attachment": row.get("attachment"), "rendered": row["rendered"]}
                    if "renderedRole" in row:
                        copied["renderedRole"] = row["renderedRole"]
                    records.append(copied)
            return NativeCollection(
                "collected", tuple(records), answer=json.loads(json.dumps(rows[index]["message"])),
            )
        if time.monotonic() >= deadline:
            return NativeCollection("unknown", reason="answer_record_missing")
        time.sleep(0.02)
