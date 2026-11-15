"""Sylvia's own Claude Code copy as a streaming provider; Sylvia owns every game action."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
from sylvia_gm.provider._claude_native import (
    CURRENT_NATIVE_ROUTE,
    FILLER_TEXT,
    TRANSIENT_SESSION,
    NativeRoute,
    collect,
    delete_session,
    session_files,
    sweep,
    wait_for_flush,
)
from sylvia_gm.provider._claude_runtime import (
    ClaudeConnection,
    find_runtime,
    native_environment,
    runtime_version,
)
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    IncompleteProviderStreamError,
    InvalidResponseError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderError,
    RateLimitError,
    TokenLimitError,
    is_context_rejection,
)
from sylvia_gm.provider._hooks import HookContext

logger = logging.getLogger(__name__)

_PREFIX = "mcp__sylvia__"
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
    for tool in request.get("tools", []):
        name = tool["name"]
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) is None:
            raise InvalidResponseError("Claude cannot represent this Sylvia tool name.")
        native_tools.append(
            {
                "name": name,
                "description": tool["description"],
                "inputSchema": tool["input_schema"],
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
        records.append(
            {
                "type": native["role"],
                "uuid": identity,
                "parentUuid": parent,
                "sessionId": session,
                "isSidechain": False,
                "cwd": str(workspace),
                "timestamp": datetime.now(UTC).isoformat(),
                "message": native,
            }
        )
        parent = identity
    return str(system), records, prompt, native_tools


def _text_blocks(content: Any) -> list[dict[str, Any]]:
    """Every user record and prompt uses list content, so its form never depends on a marker."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list) and all(isinstance(block, dict) for block in content):
        return [dict(block) for block in content]
    raise InvalidResponseError("Claude cannot represent this Sylvia input.")


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
                add(
                    "attachment",
                    {
                        key: saved[key]
                        for key in ("attachment", "rendered", "renderedRole")
                        if key in saved
                    },
                )
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


class ClaudeProvider(Provider):
    """Use an ephemeral native conversation for each step of Sylvia's existing tool loop."""

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
            session_id = str(uuid.uuid4())
            step = steps / session_id
            with _ACTIVE_LOCK:
                active = _ACTIVE_STEPS.setdefault(self.home.resolve(), set())
                running = set(active)
                active.add(session_id)
            try:
                # Earlier session files hold copies of earlier requests: never leave them.
                sweep(config, steps, running | {session_id})
                step.mkdir()
                system, records, prompt, native_tools = prepare_request(
                    messages,
                    tools,
                    self.model_info.wire_model_id,
                    workspace,
                    route,
                )
                system_path = step / "system.txt"
                history_path = step / "history.jsonl"
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
                    "",
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
                    # Persisted only so Sylvia can copy the CLI's context records; deleted below.
                    arguments.extend(["--session-id", session_id])
                if native_tools:
                    arguments.extend(
                        [
                            "--mcp-config",
                            '{"mcpServers":{"sylvia":{"type":"sdk","name":"sylvia"}}}',
                            "--allowedTools",
                            ",".join(_PREFIX + tool["name"] for tool in native_tools),
                        ]
                    )
                else:
                    arguments.extend(["--mcp-config", '{"mcpServers":{}}'])
                connection = self._connection_factory(executable, workspace, arguments, environment)
                self._active = connection
                response = ProviderResponse(
                    model=self.model_info.wire_model_id, usage_reported=False
                )
                text_parts: list[str] = []
                pending: list[dict[str, Any]] = []
                blocks: dict[int, dict[str, Any]] = {}
                allowed = {tool["name"] for tool in native_tools}
                input_usage: dict[str, Any] = {}
                streamed_text = False
                resolved_model: str | None = None
                native_state = _AnthropicStreamContent()
                native_content: list[dict[str, Any]] | None = None
                has_native_stream = False
                deferred_call: dict[str, Any] | None = None
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
                                    "builtin_tools": [],
                                    "persist_session": (
                                        False if route is None else TRANSIENT_SESSION
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
                                # No result is sent to Claude and no game action runs in this child.
                                await self._await_flush(route, config, session_id, prompt)
                                await self._close_owned(connection)
                                await self._collect(route, config, session_id, prompt)
                                response.tool_calls = [call]
                                response.finish_reason = "tool_calls"
                                if native_content is not None:
                                    continuation = [
                                        dict(block)
                                        for block in native_content
                                        if block.get("type") != "tool_use"
                                        or block.get("id") == call.id
                                    ]
                                    for block in continuation:
                                        if block.get("type") == "tool_use":
                                            block["name"] = call.name
                                    response.anthropic_content = continuation
                                    yield ProviderStreamChunk(anthropic_content=continuation)
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
                                result = {"tools": native_tools}
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
                            expected = {_PREFIX + original for original in allowed}
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
                                raise ModelNotFoundError(
                                    "Claude did not select the requested model."
                                )
                            if isinstance(resolved_model, str):
                                response.model = resolved_model
                        elif kind == "stream_event" and not event.get("parent_tool_use_id"):
                            native = event.get("event", {})
                            subtype = native.get("type")
                            has_native_stream = True
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
                                        block["input"] = json.loads(block.pop("partial_json"))
                                    pending.append(block)
                            elif subtype == "message_delta":
                                input_usage.update(native.get("usage", {}))
                                self._usage(response, input_usage)
                        elif kind == "assistant" and not event.get("parent_tool_use_id"):
                            for block in event.get("message", {}).get("content", []):
                                if block.get("type") == "tool_use" and not any(
                                    p.get("id") == block.get("id") for p in pending
                                ):
                                    pending.append(block)
                        elif kind == "result":
                            if event.get("is_error") or event.get("subtype") != "success":
                                raise runtime_failure(event)
                            if event.get("permission_denials") or pending:
                                raise InvalidResponseError(
                                    "Claude could not admit the requested operation "
                                    "under its native permissions."
                                )
                            if not streamed_text and isinstance(event.get("result"), str):
                                text_parts.append(event["result"])
                                yield ProviderStreamChunk(content=event["result"])
                            self._usage(response, event.get("usage", {}))
                            break
                        elif (
                            kind == "rate_limit_event"
                            and event.get("rate_limit_info", {}).get("status") == "rejected"
                        ):
                            raise RateLimitError(
                                "Claude reports that this account has reached its usage limit."
                            )
                    if not response.tool_calls:
                        await self._await_flush(route, config, session_id, prompt)
                    await self._close_owned(connection)
                    if not response.tool_calls:
                        await self._collect(route, config, session_id, prompt)
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
                except (AttributeError, KeyError, TypeError, ValueError):
                    raise InvalidResponseError(
                        "Claude returned an invalid runtime response."
                    ) from None
                except ProviderConnectionError as error:
                    raise IncompleteProviderStreamError(str(error)) from None
                finally:
                    try:
                        await self._close_owned(connection)
                    finally:
                        self._active = None
            finally:
                # Success, error, cancel or timeout: the CLI's session file and the step's
                # files hold full copies of the request and never outlive the step.
                delete_session(config, session_id)
                shutil.rmtree(step, ignore_errors=True)
                with _ACTIVE_LOCK:
                    active.discard(session_id)

    @staticmethod
    async def _await_flush(
        route: NativeRoute | None, config: Path, session_id: str, prompt: dict[str, Any]
    ) -> None:
        """Let the CLI finish writing this step's records before its process is closed."""
        if route is not None:
            await asyncio.to_thread(
                wait_for_flush, config, session_id, prompt["message"]["content"]
            )

    async def _collect(
        self, route: NativeRoute | None, config: Path, session_id: str, prompt: dict[str, Any]
    ) -> None:
        """Copy the CLI's context records for the ledger, then delete its session file.

        Never fatal: an unknown result is reported to the ledger, which starts a new journal
        generation for this route on the next request.
        """
        if route is None:
            return
        files = [p for p in session_files(config, session_id) if p.suffix == ".jsonl"]
        result = collect(files[0] if files else None, prompt["message"]["content"])
        delete_session(config, session_id)
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
