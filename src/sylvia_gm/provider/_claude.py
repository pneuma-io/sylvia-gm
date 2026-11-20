"""Sylvia's own Claude Code copy as a streaming provider; Sylvia owns every game action."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sylvia_gm.provider import _claude_kept
from sylvia_gm.provider._anthropic import AnthropicMessagesTransport, _AnthropicStreamContent
from sylvia_gm.provider._base import (
    CacheUsage,
    ModelInfo,
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
    TokenUsage,
    ToolCall,
)
from sylvia_gm.provider._claude_kept import (
    KEPT_SESSION,
    PERSISTENT,
    ContinuationMismatchError,
    KeptProcess,
    continuation,
    process_setting,
)
from sylvia_gm.provider._claude_native import (
    CURRENT_NATIVE_ROUTE,
    FILLER_TEXT,
    TRANSIENT_SESSION,
    NativeCollection,
    NativeRoute,
    collect,
    collect_answer,
    delete_session,
    remove_debug_logs,
    remove_step_folder,
    session_files,
    sweep,
    wait_for_flush,
)
from sylvia_gm.provider._claude_runtime import (
    DOCUMENTED_CONTEXT_WINDOW_AT,
    ClaudeConnection,
    documented_context_window,
    find_runtime,
    native_environment,
    runtime_version,
)
from sylvia_gm.provider._context_capacity import resolve_context_capacity
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    IncompleteProviderStreamError,
    InvalidResponseError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
    TokenLimitError,
    is_context_rejection,
)
from sylvia_gm.provider._hooks import HookContext

logger = logging.getLogger(__name__)

_PREFIX = "mcp__sylvia__"
#: Claude Code's own tool that loads a deferred tool's definition (deferred tool list, #204).
TOOL_SEARCH = "ToolSearch"
#: The MCP ``_meta`` key that keeps a tool loaded under Claude Code's tool search; ``False``
#: defers it. Checked offline on 2.1.322 (Sylvia's minimum and its installed stable release on
#: 20 November 2026) and 2.1.327.
ALWAYS_LOAD = "anthropic/alwaysLoad"
#: Session ids of steps running in this process, by runtime home (kept out of the sweep).
_ACTIVE_STEPS: dict[Path, set[str]] = {}
_ACTIVE_LOCK = threading.Lock()
_CONTINUE = (
    "SYLVIA_GM_RUNTIME_CONTINUE: Continue the response to the saved Sylvia conversation, "
    "using the committed tool results above. This is an application invocation, not player speech."
)


def prepare_request(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    model_id: str,
    workspace: Path,
    native: NativeRoute | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Project canonical roles and correlated tools into a disposable native transcript.

    With ``native`` (the context ledger's route for this wire) the transcript is the ledger's
    native projection: every earlier request's prompt group as one user record, the CLI's saved
    context records after it, a filler after any trailing user record, list content throughout.
    """
    request = AnthropicMessagesTransport().format_request(model_id, messages, tools, True)
    system = request.get("system", "")
    if isinstance(system, list):
        if any(block.get("type") != "text" for block in system):
            raise InvalidResponseError("Claude cannot represent this system input.")
        system = "\n\n".join(block["text"] for block in system)
    native_tools = []
    # Deferred tool list (#204): a tool marked ``defer_loading`` stays behind Claude Code's
    # tool search and every other tool is always loaded; each says which in its MCP ``_meta``.
    deferring = any(tool.get("defer_loading") is True for tool in request.get("tools", []))
    for tool in request.get("tools", []):
        name = tool["name"]
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) is None:
            raise InvalidResponseError("Claude cannot represent this Sylvia tool name.")
        native_tools.append(
            {
                "name": name,
                "description": tool["description"],
                "inputSchema": tool["input_schema"],
                **({"_meta": {ALWAYS_LOAD: tool.get("defer_loading") is not True}}
                   if deferring else {}),
            }
        )
    if len({tool["name"] for tool in native_tools}) != len(native_tools):
        raise InvalidResponseError("Claude received duplicate Sylvia tool names.")
    history = json.loads(json.dumps(request["messages"], allow_nan=False))
    for message in history:
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_use":
                    block["name"] = _PREFIX + block["name"]
    if native is not None:
        records, prompt = _native_projection(messages, history, native, model_id, workspace)
        return str(system), records, prompt, native_tools
    # A genuine final player input goes directly to stdin. A typed tool-result tail must
    # stay in the transcript: Claude's stdin prompt normalizer drops tool_result blocks.
    is_player = bool(messages) and messages[-1].get("role") == "user"
    if history and is_player:
        prompt = {"type": "user", "message": history.pop()}
        if messages[-1].get("_application_data"):
            prompt["isSynthetic"] = True
    else:
        prompt = {
            "type": "user",
            "isSynthetic": True,
            "message": {"role": "user", "content": _CONTINUE},
        }
    session, parent = str(uuid.uuid4()), None
    records = []
    for message in history:
        identity = str(uuid.uuid4())
        entry = dict(message)
        if entry["role"] == "assistant":
            entry.update(
                {
                    "id": "msg_" + uuid.uuid4().hex,
                    "type": "message",
                    "model": model_id,
                    "stop_reason": "tool_use"
                    if any(block.get("type") == "tool_use" for block in entry["content"])
                    else "end_turn",
                    "stop_sequence": None,
                }
            )
        records.append(
            {
                "type": entry["role"],
                "uuid": identity,
                "parentUuid": parent,
                "sessionId": session,
                "isSidechain": False,
                "cwd": str(workspace),
                "timestamp": datetime.now(UTC).isoformat(),
                "message": entry,
            }
        )
        parent = identity
    return str(system), records, prompt, native_tools


def tool_search(native_tools: list[dict[str, Any]]) -> bool:
    """Does this step defer tools behind Claude Code's tool search (deferred tool list)?"""
    return any(
        isinstance(tool.get("_meta"), dict) and tool["_meta"].get(ALWAYS_LOAD) is False
        for tool in native_tools
    )


def _replay_record(add: Callable[[str, dict[str, Any]], None], saved: Mapping[str, Any]) -> None:
    """Replay one saved CLI record: a rendered context record, or a tool-search round message.

    A round (the model's ``ToolSearch`` call and the CLI's result, deferred tool list) is kept
    as the CLI stored it, so a fresh process sends what the earlier one sent and the tools it
    loaded stay loaded.
    """
    if saved.get("type") in {"assistant", "user"} and isinstance(saved.get("message"), dict):
        body = {key: json.loads(json.dumps(saved[key]))
                for key in ("message", "toolUseResult") if key in saved}
        add(str(saved["type"]), body)
        return
    add("attachment", {key: saved[key] for key in ("attachment", "rendered", "renderedRole")
                       if key in saved})


def _text_blocks(content: Any) -> list[dict[str, Any]]:
    """Every user record and prompt uses list content, so its form never depends on a marker."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list) and all(isinstance(block, dict) for block in content):
        return [dict(block) for block in content]
    raise InvalidResponseError("Claude cannot represent this Sylvia input.")


def _answers(message: dict[str, Any] | None, answer: Mapping[str, Any]) -> bool:
    """Is ``answer`` (a kept process's stored tool-result message) the answer to ``message``?"""
    if message is None or message.get("role") != "user":
        return False
    content, stored = message.get("content"), answer.get("content")
    return (
        isinstance(content, list) and bool(content) and isinstance(content[0], dict)
        and content[0].get("type") == "tool_result"
        and isinstance(stored, list) and bool(stored) and isinstance(stored[0], dict)
        and stored[0].get("type") == "tool_result"
        and stored[0].get("tool_use_id") == content[0].get("tool_use_id")
    )


def _is_prompt_entry(message: dict[str, Any]) -> bool:
    content = message.get("content")
    return message.get("role") == "user" and not (
        isinstance(content, list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    )


def _native_projection(
    messages: list[dict[str, Any]],
    history: list[dict[str, Any]],
    route: NativeRoute,
    model_id: str,
    workspace: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The ledger journal as Claude Code's transcript plus this step's stdin prompt.

    The transcript is a pure function of the journal entries, the boundaries at which earlier
    requests ended and the CLI records saved for them, so an earlier step's transcript plus its
    prompt is always a prefix of every later transcript.
    """
    leading = 0
    while leading < len(messages) and messages[leading].get("role") == "system":
        leading += 1
    if (
        leading != route.stable_count
        or len(history) != route.entry_count
        or len(messages) - leading != route.entry_count
        or any(m.get("role") == "system" for m in messages[leading:])
        or any(not 0 < b < route.entry_count for b in route.boundaries)
        or list(route.boundaries) != sorted(set(route.boundaries))
    ):
        raise InvalidResponseError("Claude's native transcript does not match this request.")
    session, timestamp = str(uuid.uuid4()), datetime.now(UTC).isoformat()
    records: list[dict[str, Any]] = []
    parent: list[str | None] = [None]

    def add(kind: str, body: dict[str, Any]) -> None:
        identity = str(uuid.uuid4())
        records.append(
            {
                "type": kind,
                "uuid": identity,
                "parentUuid": parent[0],
                "sessionId": session,
                "isSidechain": False,
                "cwd": str(workspace),
                "timestamp": timestamp,
                **body,
            }
        )
        parent[0] = identity

    def add_message(message: dict[str, Any]) -> None:
        native = dict(message)
        if native["role"] == "assistant":
            native.update(
                {
                    "id": "msg_" + uuid.uuid4().hex,
                    "type": "message",
                    "model": model_id,
                    "stop_reason": "tool_use"
                    if any(block.get("type") == "tool_use" for block in native["content"])
                    else "end_turn",
                    "stop_sequence": None,
                }
            )
        else:
            native["content"] = _text_blocks(native.get("content"))
        add(native["role"], {"message": native})

    def needs_filler() -> bool:
        # Claude Code inserts its own unrecorded filler after a trailing user record.
        last = next((r["type"] for r in reversed(records) if r["type"] != "attachment"), None)
        return last == "user"

    prompt: dict[str, Any] | None = None
    ends = [*route.boundaries, len(history)]
    start = 0
    for end in ends:
        group = end
        while group > start and _is_prompt_entry(history[group - 1]):
            group -= 1
        answer = route.native_answers.get(end) if end != len(history) else None
        if answer is not None and _answers(history[group - 1] if group > start else None, answer):
            # A kept process answered this request's call in place: its conversation holds
            # the one tool-result message it stored (the result and the sealed records), not
            # the result, a filler and a separate prompt. Replay exactly that.
            for message in history[start:group - 1]:
                add_message(message)
            add("user", {"message": json.loads(json.dumps(answer))})
            for saved in route.native_records.get(end, ()):
                _replay_record(add, saved)
            start = end
            continue
        for message in history[start:group]:
            add_message(message)
        content = [block for m in history[group:end] for block in _text_blocks(m["content"])]
        start = end
        if not content:
            if end == len(history):
                raise InvalidResponseError("Claude has no prompt for this request.")
            continue
        if needs_filler():
            add(
                "assistant",
                {
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": FILLER_TEXT}],
                        "id": "msg_" + uuid.uuid4().hex,
                        "type": "message",
                        "model": model_id,
                        "stop_reason": "end_turn",
                        "stop_sequence": None,
                    }
                },
            )
        if end == len(history):
            prompt = {"type": "user", "message": {"role": "user", "content": content}}
        else:
            add("user", {"message": {"role": "user", "content": content}})
            for saved in route.native_records.get(end, ()):
                _replay_record(add, saved)
    assert prompt is not None
    return records, prompt


def runtime_failure(event: dict[str, Any]) -> ProviderError:
    """Classify native failures without reflecting errors that may echo private inputs."""
    status = event.get("api_error_status")
    text = " ".join(str(event.get(k, "")) for k in ("result", "errors", "error", "subtype")).lower()
    if status in (401, 403) or any(
        x in text for x in ("not logged in", "authentication", "login required", "oauth")
    ):
        return AuthenticationError("Sign in again through the Claude runtime. Saved work is kept.")
    if status == 429 or any(
        x in text for x in ("rate_limit", "rate limit", "usage limit", "quota")
    ):
        return RateLimitError(
            "Claude reports that your account allowance is unavailable. "
            "Check Claude for its reset time."
        )
    if is_context_rejection(text):
        return TokenLimitError.provider_rejection()
    if status == 404 or any(
        x in text
        for x in ("model_not_found", "model not found", "invalid model", "not have access to")
    ):
        return ModelNotFoundError(
            "This model is unavailable to the selected Claude account. Choose another model."
        )
    return ProviderError(
        "Claude did not complete its response. Saved work is kept; no API fallback was started."
    )


#: The adapter's own refusals of a response, by their message, as codes for the audit.
_INVALID_REASONS = {
    "Claude requested an unavailable or uncorrelated Sylvia operation.": "uncorrelated_call",
    "Claude did not expose exactly this request's Sylvia tools.": "tools_not_exposed",
    "Claude could not admit the requested operation under its native permissions.": (
        "operation_not_admitted"
    ),
    "Claude returned invalid text.": "invalid_text",
}

#: The whole-number fields of a result record's per-model usage that are kept for the audit.
_MODEL_USAGE_COUNTS = (
    "inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens",
    "contextWindow", "maxOutputTokens",
)
#: Those that add up over a process's life (the others are limits, not counts).
_MODEL_USAGE_TOTALS = frozenset(_MODEL_USAGE_COUNTS[:4])


def _check_still_waiting(kept: KeptProcess) -> None:
    """Read what a kept process wrote while it was idle; refuse one that moved on or exited.

    Frames that only finish the reply it was left on are dropped. A result, a further call or
    the start of another model message means its turn went on without the answer; the end of
    its output means it exited.
    """
    drain = getattr(kept.connection, "drain", None)
    if not callable(drain):
        return
    for event in drain():
        if event is None:
            raise ContinuationMismatchError("runtime_exited")
        if isinstance(event, Exception) or event.get("type") == "result":
            raise ContinuationMismatchError("turn_moved_on")
        if event.get("type") == "control_request":
            raise ContinuationMismatchError("turn_moved_on")
        if event.get("type") == "stream_event" and (
            (event.get("event") or {}).get("type") == "message_start"
        ):
            # A new model request began while the process was idle (an MCP timeout, say).
            raise ContinuationMismatchError("turn_moved_on")


def _failure_code(error: BaseException) -> str:
    """A content-free code for why a step failed, for the reuse audit."""
    if isinstance(error, asyncio.CancelledError):
        return "cancelled"
    if isinstance(error, ProviderTimeoutError):
        return "timeout"
    if isinstance(error, (IncompleteProviderStreamError, ProviderConnectionError)):
        return "connection_lost"
    if isinstance(error, InvalidResponseError):
        return _INVALID_REASONS.get(str(error), "invalid_response")
    if isinstance(error, ProviderError):
        return "provider_error"
    return "local_error"


@dataclass
class _Step:
    """One native reply as it is read: its response, the calls it streamed and what it said."""

    response: ProviderResponse
    allowed: set[str]
    tools: list[dict[str, Any]] = field(default_factory=list)
    pending: list[dict[str, Any]] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)
    #: The ``tools/call`` request a kept process is left waiting on.
    call_event: dict[str, Any] | None = None
    call_id: str = ""
    call_arguments: Any = None
    #: False when the process must not be kept after this reply (``not_kept`` says why).
    keepable: bool = True
    not_kept: str | None = None
    #: The process's cumulative per-model usage before and after this step (kept processes).
    model_usage_before: dict[str, dict[str, int]] = field(default_factory=dict)
    model_usage_after: dict[str, dict[str, int]] | None = None
    #: Deferred tool list: the step's ``ToolSearch`` rounds (tools loaded and each round's
    #: request usage), and the id of the model message that ended the step.
    searches: list[dict[str, Any]] = field(default_factory=list)
    final_message_id: str | None = None

    def native_tools(self) -> list[dict[str, Any]]:
        return self.tools


@dataclass
class _KeepPlan:
    """What a kept process must hold to serve one ledger request, and its reuse audit."""

    home: Path
    model: str
    system: str
    tools_key: str
    history: list[dict[str, Any]]
    generation: int
    reuse: dict[str, Any]


class ClaudeProvider(Provider):
    """Use an ephemeral native conversation for each step of Sylvia's existing tool loop."""

    model_info: ModelInfo

    def __init__(
        self,
        home: Path,
        model_info: ModelInfo,
        *,
        connection_factory: Callable[..., ClaudeConnection] = ClaudeConnection,
    ) -> None:
        super().__init__()
        self.home, self.model_info = home, model_info
        self._connection_factory = connection_factory
        self._active: ClaudeConnection | None = None
        self._lock = asyncio.Lock()
        self._last_response = ProviderResponse()
        # Set when Claude refused a request planned within a documented or reported window.
        self._reported_window_refused = False
        if model_info.runtime_context_window is None:
            # A model saved before this figure existed, or by a caller that named none.
            documented = documented_context_window(model_info.wire_model_id)
            if documented is not None:
                self._apply_runtime_window(
                    documented, "model_catalog", DOCUMENTED_CONTEXT_WINDOW_AT,
                )

    async def _close_owned(self, connection: ClaudeConnection) -> None:
        """Finish child collection even when the caller cancels more than once."""
        closing = asyncio.create_task(asyncio.to_thread(connection.close))
        interrupted = False
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                interrupted = True
        closing.result()
        if interrupted:
            raise asyncio.CancelledError

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60,
        **kwargs: object,
    ) -> ProviderResponse:
        async for _ in self.generate_stream(messages, tools, timeout, **kwargs):
            pass
        return self._last_response

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        # The context ledger's route belongs to exactly the request it was planned for.
        route = CURRENT_NATIVE_ROUTE.get()
        CURRENT_NATIVE_ROUTE.set(None)
        async with self._lock:
            keep = route is not None and process_setting() == PERSISTENT
            plan: _KeepPlan | None = None
            if keep:
                assert route is not None
                plan = self._keep_plan(messages, tools, route)
                kept, stale = _claude_kept.KEPT_PROCESSES.take(
                    plan.home, plan.model, plan.system, plan.tools_key, plan.history,
                )
                await self._close_kept(*stale)
                if kept is not None:
                    from sylvia_gm.agent._context_ledger import CONTINUE_RECORD_TEXT

                    yielded = False
                    try:
                        _check_still_waiting(kept)
                        delivery = continuation(kept, plan.history, CONTINUE_RECORD_TEXT)
                    except ContinuationMismatchError as mismatch:
                        plan.reuse["fresh_reason"] = mismatch.reason
                        await self._close_kept(kept)
                    else:
                        try:
                            async for chunk in self._continue(kept, delivery, route, plan, timeout):
                                yielded = True
                                yield chunk
                            return
                        except (IncompleteProviderStreamError, ProviderConnectionError) as error:
                            # The process had gone: serve the request once on a fresh one, but
                            # only before the caller saw anything.
                            if yielded:
                                await self._audit_failure(plan, error)
                                raise
                            plan.reuse.update(continued=False, answered_in_place=False,
                                              retried_fresh=True, fresh_reason="runtime_error")
                        except GeneratorExit:
                            raise
                        except BaseException as error:
                            await self._audit_failure(plan, error)
                            raise
            async for chunk in self._fresh(messages, tools, route, timeout, plan):
                yield chunk

    def _keep_plan(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None,
        route: NativeRoute,
    ) -> _KeepPlan:
        """What a kept process must hold to serve this request, and the reuse audit."""
        model = self.model_info.wire_model_id
        workspace = self.home / "workspace"
        system, _records, _prompt, native_tools = prepare_request(
            messages, tools, model, workspace, route,
        )
        history = AnthropicMessagesTransport().format_request(model, messages, tools, True)[
            "messages"]
        home = self.home.resolve()
        return _KeepPlan(
            home=home, model=model, system=system,
            tools_key=json.dumps(native_tools, sort_keys=True), history=history,
            generation=_claude_kept.KEPT_PROCESSES.generation(home),
            reuse={"continued": False, "answered_in_place": False, "fresh_reason": None,
                   "kept": False},
        )

    async def _close_kept(self, *kept: KeptProcess) -> None:
        for each in kept:
            await asyncio.to_thread(each.close)

    async def release_idle(self) -> None:
        """Close the processes kept for this home and model (Stop, provider close)."""
        released = _claude_kept.KEPT_PROCESSES.release(
            self.home.resolve(), self.model_info.wire_model_id)
        await self._close_kept(*released)

    async def _audit_failure(self, plan: _KeepPlan, error: BaseException) -> None:
        """The reuse record of a continued step that failed, with a code for what failed."""
        plan.reuse.update(kept=False, failure=_failure_code(error))
        await self._audit_reuse(plan.reuse)

    async def _audit_reuse(self, reuse: dict[str, Any]) -> None:
        await self.hooks.fire_on_request(
            HookContext(
                provider_key="claude_runtime",
                model_id=self.model_info.id,
                stream=True,
                request_json={"method": "claude/process_reuse", "params": dict(reuse)},
            )
        )

    def _closer(
        self, config: Path, session_id: str, step: Path, cache: Path | None,
    ) -> Callable[[KeptProcess], None]:
        """Remove a kept process's private files once the process itself has closed."""
        home = self.home

        def remove(_kept: KeptProcess) -> None:
            delete_session(config, session_id, home)
            if step.exists():
                remove_step_folder(step, home)
            remove_debug_logs(cache, home)
            with _ACTIVE_LOCK:
                _ACTIVE_STEPS.get(home.resolve(), set()).discard(session_id)

        return remove

    async def _continue(
        self, kept: KeptProcess, delivery: tuple[str, Any], route: NativeRoute,
        plan: _KeepPlan, timeout: float,
    ) -> AsyncIterator[ProviderStreamChunk]:
        """Serve the request in the kept process: a new prompt, or the waiting call's answer."""
        kind, payload = delivery
        connection = kept.connection
        self._active = connection
        step = _Step(
            response=ProviderResponse(model=self.model_info.wire_model_id, usage_reported=False),
            allowed={tool["name"] for tool in json.loads(kept.tools_key)},
            tools=json.loads(kept.tools_key),
            model_usage_before=kept.model_usage,
        )
        prompt: dict[str, Any] | None = None
        plan.reuse.update(continued=True, answered_in_place=kind == "answer")
        finished = False
        try:
            if kind == "prompt":
                prompt = {"type": "user", "message": {"role": "user", "content": payload}}
                await self._audit_continued(prompt, tool_search(step.tools))
                connection.write(prompt)
            else:
                pending = kept.pending
                assert pending is not None
                await self._audit_continued({"tool_result": payload}, tool_search(step.tools))
                connection.write(
                    {
                        "type": "control_response",
                        "response": {
                            "subtype": "success",
                            "request_id": pending["request_id"],
                            "response": {
                                "mcp_response": {
                                    "jsonrpc": "2.0",
                                    "id": pending.get("request", {}).get("message", {}).get(
                                        "id", 0),
                                    "result": payload,
                                }
                            },
                        },
                    }
                )
            async for chunk in self._run_events(
                connection, step, route, kept.config, kept.session_id, prompt, timeout,
                keep=True, answered_call=kept.call_id if kind == "answer" else None,
            ):
                yield chunk
            finished = True
        finally:
            self._active = None
            if finished and step.keepable:
                kept.held, kept.spoken = plan.history, "".join(step.text_parts)
                kept.pending, kept.call_id, kept.call_arguments = (
                    step.call_event, step.call_id, step.call_arguments)
                if step.model_usage_after is not None:
                    kept.model_usage = step.model_usage_after
                evicted = _claude_kept.KEPT_PROCESSES.keep(kept, plan.generation)
                plan.reuse["kept"] = kept not in evicted
                await self._close_kept(*evicted)
                await self._audit_reuse(plan.reuse)
            elif finished:
                plan.reuse.update(kept=False, not_kept=step.not_kept)
                await self._close_kept(kept)
                await self._audit_reuse(plan.reuse)
            else:
                # An error, a cancellation or a closed stream: the process's state is unknown.
                # The caller records the request (a fresh retry writes its own record).
                await self._close_kept(kept)

    async def _audit_continued(self, given: dict[str, Any], searching: bool = False) -> None:
        await self.hooks.fire_on_request(
            HookContext(
                provider_key="claude_runtime",
                model_id=self.model_info.id,
                stream=True,
                request_json={
                    "method": "claude/native_step",
                    "continued": True,
                    "input": given,
                    "controls": {"safe_mode": True,
                                 "builtin_tools": [TOOL_SEARCH] if searching else [],
                                 "persist_session": KEPT_SESSION,
                                 "output_allowance": "runtime_managed",
                                 "tool_choice": "runtime_managed"},
                    "downstream_model_request": "not_exposed_by_runtime",
                },
            )
        )

    async def _fresh(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        route: NativeRoute | None,
        timeout: float,
        plan: _KeepPlan | None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        """Start a process from the saved transcript; keep it afterwards when ``plan`` says so."""
        executable = find_runtime(self.home)
        if executable is None:
            raise ProviderConnectionError(
                "Set up Claude Code for Sylvia in Settings > Accounts, then try again."
            )
        await asyncio.to_thread(runtime_version, executable, self.home)
        self.home.mkdir(parents=True, exist_ok=True)
        # One fixed, empty working directory: Claude Code names it in every request. The
        # system and transcript files live in a per-step folder that is always removed.
        workspace = self.home / "workspace"
        workspace.mkdir(exist_ok=True)
        steps = self.home / "steps"
        steps.mkdir(exist_ok=True)
        environment = native_environment(self.home)
        config = Path(environment.get("CLAUDE_CONFIG_DIR") or self.home / "profile")
        cache = Path(environment["XDG_CACHE_HOME"]) if environment.get(
            "XDG_CACHE_HOME") else None
        session_id = str(uuid.uuid4())
        step_folder = steps / session_id
        with _ACTIVE_LOCK:
            active = _ACTIVE_STEPS.setdefault(self.home.resolve(), set())
            running = set(active)
            active.add(session_id)
        kept: KeptProcess | None = None
        try:
            # Earlier session files hold copies of earlier requests: never leave them.
            sweep(config, steps, running | {session_id}, self.home)
            step_folder.mkdir()
            system, records, prompt, native_tools = prepare_request(
                messages,
                tools,
                self.model_info.wire_model_id,
                workspace,
                route,
            )
            searching = tool_search(native_tools)
            if searching:
                # Deferred tool list (#204): Claude Code's own tool search loads deferred tools.
                environment["ENABLE_TOOL_SEARCH"] = "true"
            system_path = step_folder / "system.txt"
            history_path = step_folder / "history.jsonl"
            system_path.write_bytes(system.encode("utf-8"))
            transcript = "".join(json.dumps(r, ensure_ascii=True) + "\n" for r in records)
            if route is None:
                history_path.write_text(transcript, encoding="utf-8")
            else:
                history_path.write_bytes(transcript.encode("utf-8"))
            arguments = [
                "--print",
                "--safe-mode",
                "--restricted",
                "--tools",
                TOOL_SEARCH if searching else "",
                "--disable-slash-commands",
                "--setting-sources",
                "",
                "--settings",
                '{"disableAllHooks":true,"autoMemoryEnabled":false}',
                "--strict-mcp-config",
                *(["--no-session-persistence"] if route is None else []),
                "--no-chrome",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
                "--verbose",
                "--include-partial-messages",
                "--system-prompt-file",
                str(system_path),
                "--model",
                self.model_info.wire_model_id,
            ]
            if records:
                arguments.extend(["--resume", str(history_path), "--fork-session"])
            if route is not None:
                # Persisted only so Sylvia can copy the CLI's context records; deleted below
                # (or, for a kept process, when that process closes).
                arguments.extend(["--session-id", session_id])
            if native_tools:
                arguments.extend(
                    [
                        "--mcp-config",
                        '{"mcpServers":{"sylvia":{"type":"sdk","name":"sylvia"}}}',
                        "--allowedTools",
                        ",".join([*([TOOL_SEARCH] if searching else []),
                                  *(_PREFIX + tool["name"] for tool in native_tools)]),
                    ]
                )
            else:
                arguments.extend(["--mcp-config", '{"mcpServers":{}}'])
            connection = self._connection_factory(executable, workspace, arguments, environment)
            self._active = connection
            step = _Step(
                response=ProviderResponse(
                    model=self.model_info.wire_model_id, usage_reported=False
                ),
                allowed={tool["name"] for tool in native_tools},
                tools=native_tools,
            )
            finished = False
            try:
                await self.hooks.fire_on_request(
                    HookContext(
                        provider_key="claude_runtime",
                        model_id=self.model_info.id,
                        stream=True,
                        request_json={
                            "method": "claude/native_step",
                            "system": system,
                            "transcript": records,
                            "input": prompt,
                            "tools": native_tools,
                            "controls": {
                                "safe_mode": True,
                                "builtin_tools": [TOOL_SEARCH] if searching else [],
                                "persist_session": (
                                    False if route is None
                                    else KEPT_SESSION if plan is not None
                                    else TRANSIENT_SESSION
                                ),
                                "output_allowance": "runtime_managed",
                                "tool_choice": "runtime_managed",
                            },
                            "downstream_model_request": "not_exposed_by_runtime",
                        },
                    )
                )
                await asyncio.to_thread(connection.start)
                connection.write(
                    {
                        "type": "control_request",
                        "request_id": "initialize",
                        "request": {
                            "subtype": "initialize",
                            "sdkMcpServers": ["sylvia"] if native_tools else [],
                        },
                    }
                )
                connection.write(prompt)
                async for chunk in self._run_events(
                    connection, step, route, config, session_id, prompt, timeout,
                    keep=plan is not None,
                ):
                    yield chunk
                finished = True
            finally:
                try:
                    if finished and plan is not None and not step.keepable:
                        plan.reuse.update(kept=False, not_kept=step.not_kept)
                    if finished and plan is not None and step.keepable:
                        kept = KeptProcess(
                            connection=connection, home=plan.home, model=plan.model,
                            system=plan.system, tools_key=plan.tools_key,
                            held=plan.history, session_id=session_id, step=step_folder,
                            config=config, cache=cache, spoken="".join(step.text_parts),
                            pending=step.call_event, call_id=step.call_id,
                            call_arguments=step.call_arguments,
                            model_usage=step.model_usage_after or {},
                        )
                        kept.on_close.append(
                            self._closer(config, session_id, step_folder, cache))
                        evicted = _claude_kept.KEPT_PROCESSES.keep(kept, plan.generation)
                        plan.reuse["kept"] = kept not in evicted
                        if kept in evicted:
                            kept = None
                        await self._close_kept(*[k for k in evicted if k is not kept])
                    if kept is None:
                        await self._close_owned(connection)
                finally:
                    self._active = None
                    if plan is not None:
                        await self._audit_reuse(plan.reuse)
        finally:
            if kept is None:
                # Success, error, cancel or timeout: the CLI's session file and the step's
                # files hold full copies of the request and never outlive the step.
                delete_session(config, session_id, self.home)
                if step_folder.exists():
                    remove_step_folder(step_folder, self.home)
                # The CLI's MCP debug log (tool names, session ID, working folder path).
                remove_debug_logs(cache, self.home)
                with _ACTIVE_LOCK:
                    active.discard(session_id)

    async def _run_events(
        self,
        connection: ClaudeConnection,
        step: _Step,
        route: NativeRoute | None,
        config: Path,
        session_id: str,
        prompt: dict[str, Any] | None,
        timeout: float,
        *,
        keep: bool,
        answered_call: str | None = None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        """Read one native reply to its end, a Sylvia tool call or a result.

        ``prompt`` is what was written to stdin for this step, if anything was (it is absent
        when a waiting call was answered); the CLI's context records follow it in the session
        file and are collected. ``answered_call`` names the call a kept process's waiting turn
        was answered on; the message it stored for that answer is collected instead. With
        ``keep`` the process is left open for the next request.
        """
        response = step.response
        native_state = _AnthropicStreamContent()
        native_content: list[dict[str, Any]] | None = None
        has_native_stream = False
        event: dict[str, Any] = {}
        deferred_call: dict[str, Any] | None = None
        blocks: dict[int, dict[str, Any]] = {}
        input_usage: dict[str, Any] = {}
        streamed_text = False
        resolved_model: str | None = None
        allowed = step.allowed
        pending = step.pending
        text_parts = step.text_parts
        # Deferred tool list (#204): a step may hold several model requests, each ToolSearch
        # call answered by Claude Code itself before the model calls a Sylvia tool or narrates.
        searching = tool_search(step.tools)
        searched_here = False
        held_text: list[str] = []
        try:
            while True:
                if deferred_call is not None and native_state.completed:
                    event, deferred_call = deferred_call, None
                else:
                    event = await asyncio.to_thread(connection.next_event, timeout)
                kind = event.get("type")
                if kind == "control_request":
                    request = event.get("request", {})
                    if (
                        request.get("subtype") != "mcp_message"
                        or request.get("server_name") != "sylvia"
                    ):
                        connection.write(
                            {
                                "type": "control_response",
                                "response": {
                                    "subtype": "error",
                                    "request_id": event.get("request_id"),
                                    "error": "This operation is unavailable in Sylvia.",
                                },
                            }
                        )
                        continue
                    message = request.get("message", {})
                    method = message.get("method")
                    if method == "tools/call":
                        if has_native_stream and not native_state.completed:
                            if deferred_call is None:
                                deferred_call = event
                            continue
                        params = message.get("params", {})
                        original = params.get("name")
                        candidate = next(
                            (
                                call
                                for call in pending
                                if call.get("name") == _PREFIX + str(original)
                                and call.get("input") == params.get("arguments", {})
                            ),
                            None,
                        )
                        if (
                            original not in allowed
                            or candidate is None
                            or not isinstance(candidate.get("id"), str)
                            or not candidate["id"]
                            or not isinstance(candidate.get("input"), dict)
                        ):
                            raise InvalidResponseError(
                                "Claude requested an unavailable or uncorrelated "
                                "Sylvia operation."
                            )
                        call = ToolCall(
                            candidate["id"],
                            original,
                            json.dumps(candidate["input"], allow_nan=False),
                        )
                        # Reaching the actual MCP call means native permissions admitted it.
                        # No result is sent to Claude and no game action runs in this child:
                        # a kept process waits on the call until the next request answers it.
                        flushed = await self._await_flush(route, config, session_id, prompt,
                                                          len(step.searches))
                        # Claude Code can report one call twice (its stream and an assistant
                        # event), so the calls of the reply are counted by id.
                        if keep and len({str(b.get("id")) for b in pending}) > 1:
                            # Sylvia takes one call; a kept process would later send the
                            # dropped ones. Close it here, as a per-step process is.
                            step.keepable, step.not_kept = False, "parallel_calls"
                        elif keep and searched_here:
                            # A ToolSearch call beside this one stays in the process's own
                            # message, which the journal does not keep (it holds the call).
                            step.keepable, step.not_kept = False, "search_beside_call"
                        if keep and step.keepable:
                            step.call_event = event
                            step.call_id = candidate["id"]
                            step.call_arguments = candidate["input"]
                        else:
                            await self._close_owned(connection)
                        await self._collect(
                            route, config, session_id, prompt, flushed, delete=not keep,
                            step=step,
                        )
                        await self._collect_answer(route, config, session_id, answered_call,
                                                   step)
                        await self._audit_searches(step)
                        response.tool_calls = [call]
                        response.finish_reason = "tool_calls"
                        if native_content is not None:
                            continuation_blocks = [
                                dict(block)
                                for block in native_content
                                if block.get("type") != "tool_use"
                                or block.get("id") == call.id
                            ]
                            for block in continuation_blocks:
                                if block.get("type") == "tool_use":
                                    block["name"] = call.name
                            response.anthropic_content = continuation_blocks
                            yield ProviderStreamChunk(anthropic_content=continuation_blocks)
                        yield ProviderStreamChunk(
                            tool_call_delta={
                                "index": 0,
                                "id": call.id,
                                "type": "function",
                                "function": {
                                    "name": call.name,
                                    "arguments": call.arguments,
                                },
                            }
                        )
                        break
                    result: dict[str, Any] = {}
                    if method == "initialize":
                        result = {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "sylvia", "version": "1"},
                        }
                    elif method == "tools/list":
                        result = {"tools": step.native_tools()}
                    elif method not in {"notifications/initialized", "ping"}:
                        raise InvalidResponseError(
                            "Claude requested an unsupported runtime operation."
                        )
                    connection.write(
                        {
                            "type": "control_response",
                            "response": {
                                "subtype": "success",
                                "request_id": event["request_id"],
                                "response": {
                                    "mcp_response": {
                                        "jsonrpc": "2.0",
                                        "id": message.get("id", 0),
                                        "result": result,
                                    }
                                },
                            },
                        }
                    )
                elif kind == "system" and event.get("subtype") == "init":
                    exposed = event.get("tools")
                    expected = {_PREFIX + original for original in allowed} | (
                        {TOOL_SEARCH} if searching else set())
                    if (
                        not isinstance(exposed, list)
                        or any(not isinstance(name, str) for name in exposed)
                        or set(exposed) != expected
                    ):
                        raise InvalidResponseError(
                            "Claude did not expose exactly this request's Sylvia tools."
                        )
                    resolved_model = event.get("model")
                    selected = self.model_info.wire_model_id
                    if selected.startswith("claude-") and resolved_model != selected:
                        raise ModelNotFoundError("Claude did not select the requested model.")
                    if isinstance(resolved_model, str):
                        response.model = resolved_model
                elif kind == "stream_event" and not event.get("parent_tool_use_id"):
                    native = event.get("event", {})
                    subtype = native.get("type")
                    has_native_stream = True
                    if searching and subtype == "message_start":
                        # Each model request of the step is its own native message; only
                        # the last one is the step's reply.
                        native_state, native_content = _AnthropicStreamContent(), None
                        searched_here = False
                        message_id = (native.get("message") or {}).get("id")
                        step.final_message_id = message_id if isinstance(message_id, str) else None
                    values = dict(native)
                    if isinstance(values.get("delta"), dict):
                        values["delta"] = SimpleNamespace(**values["delta"])
                    completed_content = native_state.accept(SimpleNamespace(**values))
                    if completed_content is not None:
                        native_content = completed_content
                    if subtype == "message_start":
                        message = native.get("message", {})
                        actual_model = message.get("model")
                        if resolved_model and actual_model != resolved_model:
                            raise ModelNotFoundError(
                                "Claude changed models during this response."
                            )
                        input_usage = dict(message.get("usage", {}))
                    elif subtype == "content_block_start":
                        blocks[native["index"]] = dict(native["content_block"])
                    elif subtype == "content_block_delta":
                        delta = native.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text = delta.get("text", "")
                            if not isinstance(text, str):
                                raise InvalidResponseError("Claude returned invalid text.")
                            if searching:
                                # Held until the message ends: a search round's text is
                                # Claude Code's own round, not the step's reply.
                                held_text.append(text)
                            else:
                                text_parts.append(text)
                                streamed_text = True
                                yield ProviderStreamChunk(content=text)
                        elif delta.get("type") == "thinking_delta":
                            yield ProviderStreamChunk(
                                reasoning_content=delta.get("thinking", "")
                            )
                        elif delta.get("type") == "input_json_delta":
                            block = blocks[native["index"]]
                            block["partial_json"] = block.get(
                                "partial_json", ""
                            ) + delta.get("partial_json", "")
                    elif subtype == "content_block_stop":
                        block = blocks.pop(native["index"], {})
                        if block.get("type") == "tool_use":
                            if "partial_json" in block:
                                # An operation without arguments streams an empty
                                # string; its input stays the start block's {}.
                                fragments = block.pop("partial_json")
                                if fragments:
                                    block["input"] = json.loads(fragments)
                            if searching and block.get("name") == TOOL_SEARCH:
                                # Claude Code answers it itself; it is no Sylvia call.
                                if not searched_here:
                                    step.searches.append({"loaded": [], "usage": {}})
                                searched_here = True
                            else:
                                pending.append(block)
                    elif subtype == "message_stop" and searching:
                        if not searched_here and held_text:
                            text_parts.extend(held_text)
                            streamed_text = True
                            yield ProviderStreamChunk(content="".join(held_text))
                        held_text = []
                    elif subtype == "message_delta":
                        input_usage.update(native.get("usage", {}))
                        self._usage(response, input_usage)
                        if searched_here and step.searches:
                            step.searches[-1]["usage"] = {
                                key: value for key, value in input_usage.items()
                                if type(value) is int}
                elif (kind == "user" and searching and not event.get("parent_tool_use_id")
                      and step.searches):
                    # Claude Code's answer to a ToolSearch call: the tools it loaded.
                    content = (event.get("message") or {}).get("content")
                    for block in content if isinstance(content, list) else ():
                        found = block.get("content") if isinstance(block, dict) else None
                        for item in found if isinstance(found, list) else ():
                            if isinstance(item, dict) and item.get("type") == "tool_reference":
                                name = str(item.get("tool_name", ""))
                                step.searches[-1]["loaded"].append(name.removeprefix(_PREFIX))
                elif kind == "assistant" and not event.get("parent_tool_use_id"):
                    for block in event.get("message", {}).get("content", []):
                        if searching and block.get("name") == TOOL_SEARCH:
                            continue
                        if block.get("type") == "tool_use" and not any(
                            p.get("id") == block.get("id") for p in pending
                        ):
                            pending.append(block)
                elif kind == "result":
                    if event.get("is_error") or event.get("subtype") != "success":
                        failure = runtime_failure(event)
                        if isinstance(failure, TokenLimitError):
                            self._refuse_reported_window()
                        raise failure
                    if event.get("permission_denials") or pending:
                        raise InvalidResponseError(
                            "Claude could not admit the requested operation "
                            "under its native permissions."
                        )
                    if not streamed_text and isinstance(event.get("result"), str):
                        text_parts.append(event["result"])
                        yield ProviderStreamChunk(content=event["result"])
                    if not (keep and response.usage_reported):
                        # A kept process's result reports the whole native turn, which can
                        # span calls answered in place: its step's own usage is the one its
                        # message stream reported.
                        self._usage(response, event.get("usage", {}))
                    step.model_usage_after = await self._audit_model_usage(
                        event.get("modelUsage"), response.model,
                        since=step.model_usage_before if keep else None,
                    )
                    self._observe_context_window(event.get("modelUsage"), response.model)
                    break
                elif (
                    kind == "rate_limit_event"
                    and event.get("rate_limit_info", {}).get("status") == "rejected"
                ):
                    raise RateLimitError(
                        "Claude reports that this account has reached its usage limit."
                    )
            if not response.tool_calls:
                flushed = await self._await_flush(route, config, session_id, prompt,
                                                  len(step.searches))
                if not keep:
                    await self._close_owned(connection)
                await self._collect(route, config, session_id, prompt, flushed, delete=not keep,
                                    step=step)
                await self._collect_answer(route, config, session_id, answered_call, step)
                await self._audit_searches(step)
            response.content = "".join(text_parts) or None
            self._last_response = response
            await self.hooks.fire_on_response(
                HookContext(
                    provider_key="claude_runtime",
                    model_id=self.model_info.id,
                    stream=True,
                    response=response,
                )
            )
            yield ProviderStreamChunk(finish_reason=response.finish_reason)
        except InvalidResponseError as error:
            await self._audit_invalid_response(
                _INVALID_REASONS.get(str(error), "invalid_response"), event, pending,
            )
            raise
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            await self._audit_invalid_response("malformed_event", event, pending, error)
            raise InvalidResponseError("Claude returned an invalid runtime response.") from None
        except ProviderConnectionError as error:
            raise IncompleteProviderStreamError(str(error)) from None

    @staticmethod
    async def _await_flush(
        route: NativeRoute | None, config: Path, session_id: str,
        prompt: dict[str, Any] | None, searches: int = 0,
    ) -> bool:
        """Let the CLI finish writing this step's records before its process is closed.

        False when the wait ended before the records were complete. Nothing to wait for when
        no prompt was written (a kept process's waiting call was answered). After ``searches``
        ToolSearch rounds the step made that many more model requests, each recorded first.
        """
        if route is None or prompt is None:
            return True
        if not searches:
            return bool(
                await asyncio.to_thread(
                    wait_for_flush, config, session_id, prompt["message"]["content"]
                )
            )
        return bool(
            await asyncio.to_thread(
                wait_for_flush, config, session_id, prompt["message"]["content"],
                requests=searches + 1,
            )
        )

    async def _audit_searches(self, step: _Step) -> None:
        """Record a step's ToolSearch rounds: tools loaded and each round's usage, no content."""
        if not step.searches:
            return
        await self.hooks.fire_on_request(
            HookContext(
                provider_key="claude_runtime",
                model_id=self.model_info.id,
                stream=True,
                request_json={
                    "direction": "receive",
                    "method": "claude/tool_search",
                    "params": {"rounds": [dict(s) for s in step.searches]},
                },
            )
        )

    async def _collect_answer(
        self, route: NativeRoute | None, config: Path, session_id: str, call_id: str | None,
        step: _Step | None = None,
    ) -> None:
        """Save the tool-result message a kept process stored for the call it was answered on.

        A later fresh process replays it in place of the per-step form, so its request extends
        what the kept process sent. Never fatal; when it cannot be found nothing is saved, and
        a fresh start replays the per-step form (one cache miss from that point).
        """
        if route is None or route.on_collected is None or not call_id:
            return
        if step is not None and step.searches:
            # The ToolSearch rounds after the answer are replayed with it.
            result = await asyncio.to_thread(
                collect_answer, config, session_id, call_id,
                final_message_id=step.final_message_id, requests=len(step.searches) + 1,
            )
        else:
            result = await asyncio.to_thread(collect_answer, config, session_id, call_id)
        if result.status != "collected":
            logger.warning(
                "The message Claude Code stored for an answered call was not found (%s); a "
                "fresh start replays the per-step form.", result.reason,
            )
            return
        try:
            await route.on_collected(result)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - saving evidence never fails the player's step
            logger.warning("Claude runtime answer record could not be saved.", exc_info=True)

    async def _collect(
        self,
        route: NativeRoute | None,
        config: Path,
        session_id: str,
        prompt: dict[str, Any] | None,
        flushed: bool = True,
        *,
        delete: bool = True,
        step: _Step | None = None,
    ) -> None:
        """Copy the CLI's context records for the ledger, then delete its session file.

        Never fatal: an unknown result is reported to the ledger, which starts a new journal
        generation for this route on the next request. A kept process keeps its session file
        (``delete=False``) until it closes; an answered call wrote no prompt, so nothing new is
        collected for it. A step's ToolSearch rounds (deferred tool list) are collected with
        its context records, in order, up to the model message that ended the step.
        """
        if route is None or prompt is None:
            return
        files = [p for p in session_files(config, session_id) if p.suffix == ".jsonl"]
        if step is not None and step.searches:
            result = collect(files[0] if files else None, prompt["message"]["content"],
                             final_message_id=step.final_message_id)
        else:
            result = collect(files[0] if files else None, prompt["message"]["content"])
        if not flushed and result.status == "collected":
            # The CLI may still have had records queued: what was read may be incomplete.
            result = NativeCollection("unknown", reason="flush_incomplete")
        if delete:
            delete_session(config, session_id, self.home)
        if result.status != "collected":
            logger.warning(
                "Claude runtime context records are unknown for this step (%s); the next "
                "request starts a new context journal generation.",
                result.reason,
            )
        if route.on_collected is None or not (
            result.records or result.status != "collected" or route.first_step
        ):
            return
        try:
            await route.on_collected(result)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - saving evidence never fails the player's step
            logger.warning("Claude runtime context records could not be saved.", exc_info=True)

    async def _audit_invalid_response(
        self, reason: str, event: dict[str, Any], pending: list[dict[str, Any]],
        error: BaseException | None = None,
    ) -> None:
        """Say which check refused a response: kinds, codes and tool names, never content."""

        def part(value: object) -> dict[str, Any]:
            return value if isinstance(value, dict) else {}

        request = part(event.get("request"))
        subtype = next(
            (
                value
                for value in (
                    event.get("subtype"), part(event.get("event")).get("type"),
                    part(request.get("message")).get("method"), request.get("subtype"),
                )
                if isinstance(value, str)
            ),
            None,
        )
        denials = event.get("permission_denials")
        facts = {
            "reason": reason,
            "event": event.get("type") if isinstance(event.get("type"), str) else None,
            "subtype": subtype,
            "exception": type(error).__name__ if error is not None else None,
            "permission_denials": [
                str(denial.get("tool_name")) for denial in denials if isinstance(denial, dict)
            ] if isinstance(denials, list) else [],
            "pending_tool_uses": [str(block.get("name")) for block in pending],
        }
        logger.warning("The Claude adapter refused a response: %s", facts)
        await self.hooks.fire_on_request(
            HookContext(
                provider_key="claude_runtime",
                model_id=self.model_info.id,
                stream=True,
                request_json={
                    "direction": "receive",
                    "method": "claude/invalid_response",
                    "params": facts,
                },
            )
        )

    async def _audit_model_usage(
        self, usage_by_model: object, resolved_model: str | None,
        since: dict[str, dict[str, int]] | None = None,
    ) -> dict[str, dict[str, int]] | None:
        """Record the counts and windows Claude Code reports per model, never its price estimate.

        A kept process reports its whole life's counts; with ``since`` (what it had reported
        before this step) the audit records this step's share. Returns the reported counts.
        """
        if not isinstance(usage_by_model, dict):
            return None
        counts = {
            str(name): {key: row[key] for key in _MODEL_USAGE_COUNTS if type(row.get(key)) is int}
            for name, row in usage_by_model.items()
            if isinstance(row, dict)
        }
        recorded = counts
        if since is not None:
            recorded = {
                name: {
                    key: value - since.get(name, {}).get(key, 0)
                    if key in _MODEL_USAGE_TOTALS else value
                    for key, value in row.items()
                }
                for name, row in counts.items()
            }
        await self.hooks.fire_on_request(
            HookContext(
                provider_key="claude_runtime",
                model_id=self.model_info.id,
                stream=True,
                request_json={
                    "direction": "receive",
                    "method": "claude/result_model_usage",
                    "params": {"model": resolved_model, "model_usage": recorded,
                               **({"since_previous_step": True} if since is not None else {})},
                },
            )
        )
        return counts

    def _apply_runtime_window(
        self, value: int | None, source: str = "runtime_reported",
        observed_at: str | None = None,
    ) -> None:
        """Set or clear the window planned within; a saved allowance can only lower it.

        ``source`` says where the figure comes from: ``runtime_reported`` for the one Claude
        Code names in a result record, ``model_catalog`` for the documented one.
        """
        model = self.model_info
        if value is None:
            observed_at = None
        elif observed_at is None:
            observed_at = datetime.now(UTC).isoformat()
        capacity = resolve_context_capacity(
            declared_context_window=model.declared_context_window,
            declared_context_source=model.declared_context_source,
            runtime_context_window=value,
            runtime_context_source=source if value is not None else "unknown",
            configured_context_window=model.configured_context_window,
            declared_observed_at=model.declared_context_observed_at,
            runtime_observed_at=observed_at,
            configured_observed_at=model.configured_context_observed_at,
        )
        # Wrappers retain this ModelInfo object, so update it rather than replacing it.
        model.runtime_context_window = capacity.runtime_context_window
        model.runtime_context_source = capacity.runtime_context_source
        model.runtime_context_observed_at = capacity.runtime_observed_at
        model.context_observed_at = observed_at or model.configured_context_observed_at
        model.context_window = capacity.effective_context_window
        model.context_window_source = capacity.effective_context_source
        model.configured_runtime_mismatch = capacity.configured_runtime_mismatch

    def _observe_context_window(self, usage_by_model: object, resolved_model: str | None) -> None:
        """Plan later requests within the window Claude Code reports for this model.

        The result record that ends a narrated reply carries the figure; a step that ends in
        an operation has none. It is what Claude Code uses for the model, which an account may
        not be given in full, so a later refusal withdraws it (``_refuse_reported_window``).
        """
        if self._reported_window_refused or not isinstance(usage_by_model, dict):
            return
        rows = {key: row for key, row in usage_by_model.items() if isinstance(row, dict)}
        row = rows.get(resolved_model or "") or rows.get(self.model_info.wire_model_id)
        if row is None:
            if len(rows) != 1:
                return
            row = next(iter(rows.values()))
        value = row.get("contextWindow")
        if type(value) is not int or not 512 <= value <= 10_000_000:
            return
        if value != self.model_info.runtime_context_window:
            self._apply_runtime_window(value)

    def _refuse_reported_window(self) -> None:
        """Claude refused a request as too large: stop planning within that window.

        The window is the documented one for the model or the one Claude Code reported; a plan
        may allow less. Planning returns to the saved allowance, or to the figure used when
        none is known, for as long as this provider lives, so the shorten-and-retry step that
        follows works against the smaller figure and does not send the same oversized request
        again.
        """
        if self._reported_window_refused or self.model_info.runtime_context_window is None:
            return
        self._reported_window_refused = True
        logger.warning(
            "Claude refused a request planned within a context window of %s tokens (%s); "
            "planning within the saved or default allowance from now on.",
            self.model_info.runtime_context_window, self.model_info.runtime_context_source,
        )
        self._apply_runtime_window(None)

    @staticmethod
    def _usage(response: ProviderResponse, usage: dict[str, Any]) -> None:
        """Keep only measured counts; never turn CLI list-price estimates into a bill."""
        if not isinstance(usage, dict):
            return
        prompt, output = usage.get("input_tokens"), usage.get("output_tokens")
        if (
            not isinstance(prompt, int)
            or isinstance(prompt, bool)
            or prompt < 0
            or not isinstance(output, int)
            or isinstance(output, bool)
            or output < 0
        ):
            return
        read, write = usage.get("cache_read_input_tokens"), usage.get("cache_creation_input_tokens")
        read = read if type(read) is int and read >= 0 else None
        write = write if type(write) is int and write >= 0 else None
        total_prompt = prompt + (read or 0) + (write or 0)
        response.usage = TokenUsage(total_prompt, output, total_prompt + output)
        response.usage_reported = True
        response.cache_usage = CacheUsage(read, write, prompt, total_prompt)

    async def close(self) -> None:
        if self._active is not None:
            await self._close_owned(self._active)
        await self.release_idle()
