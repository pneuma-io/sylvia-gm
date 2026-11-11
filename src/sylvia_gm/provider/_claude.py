"""Claude's native runtime as a streaming provider; Sylvia owns every game action."""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
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
from sylvia_gm.provider._claude_runtime import (
    ClaudeConnection,
    find_runtime,
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

_PREFIX = "mcp__sylvia__"
_CONTINUE = (
    "SYLVIA_GM_RUNTIME_CONTINUE: Continue the response to the saved Sylvia conversation, "
    "using the committed tool results above. This is an application invocation, not player speech."
)


def prepare_request(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    model_id: str,
    workspace: Path,
) -> tuple[str, list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Project canonical roles and correlated tools into a disposable native transcript."""
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
        executable: Path | None = None,
        connection_factory: Callable[..., ClaudeConnection] = ClaudeConnection,
    ) -> None:
        super().__init__()
        self.home, self.model_info = home, model_info
        self._executable = executable
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
        async with self._lock:
            executable = find_runtime(self._executable)
            if executable is None:
                raise ProviderConnectionError(
                    "Install the official Claude Code runtime, "
                    "then select it in Settings > Accounts."
                )
            await asyncio.to_thread(runtime_version, executable)
            self.home.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="turn-", dir=self.home) as directory:
                workspace = Path(directory)
                system, records, prompt, native_tools = prepare_request(
                    messages,
                    tools,
                    self.model_info.wire_model_id,
                    workspace,
                )
                system_path = workspace / "system.txt"
                history_path = workspace / "history.jsonl"
                system_path.write_text(system, encoding="utf-8")
                history_path.write_text(
                    "".join(json.dumps(r, ensure_ascii=True) + "\n" for r in records),
                    encoding="utf-8",
                )
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
                    "--no-session-persistence",
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
                connection = self._connection_factory(executable, workspace, arguments)
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
                                    "persist_session": False,
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
                                await self._close_owned(connection)
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
                    await self._close_owned(connection)
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
