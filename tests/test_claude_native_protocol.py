"""Installed official CLI against local scripted inference; no real account or vendor calls."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from sylvia_gm.provider import _claude_runtime
from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._claude import ClaudeProvider


@pytest.mark.asyncio
async def test_installed_claude_returns_tools_and_replays_committed_results(tmp_path, monkeypatch):
    executable = _claude_runtime.find_runtime()
    if executable is None:
        pytest.skip("Official native Claude Code is not installed")
    requests: list[dict[str, Any]] = []
    errors: list[BaseException] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                self.respond()
            except BaseException as error:
                errors.append(error)
                raise

        def respond(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
            if not isinstance(body, dict) or "messages" not in body:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")
                return
            if self.path.endswith("count_tokens"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"input_tokens":100}')
                return
            requests.append(body)
            assert body.get("model") == "claude-sonnet-4-6"
            text_response = any(
                block.get("type") == "tool_result" and block.get("content") == "Opened once."
                for message in body["messages"]
                for block in message.get("content", [])
                if isinstance(block, dict)
            )
            block = (
                {"type": "text", "text": ""}
                if text_response
                else {
                    "type": "tool_use",
                    "id": "call_native",
                    "name": "mcp__sylvia__open_door",
                    "input": {},
                }
            )
            delta = (
                {"type": "text_delta", "text": "The door opens."}
                if text_response
                else {
                    "type": "input_json_delta",
                    "partial_json": '{"target":"door"}',
                }
            )
            events = [
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg_native",
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": "claude-sonnet-4-6",
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 100, "output_tokens": 0},
                    },
                },
                {"type": "content_block_start", "index": 0, "content_block": block},
                {"type": "content_block_delta", "index": 0, "delta": delta},
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {
                        "stop_reason": "end_turn" if text_response else "tool_use",
                        "stop_sequence": None,
                    },
                    "usage": {"output_tokens": 5},
                },
                {"type": "message_stop"},
            ]
            payload = "".join(
                "event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    environment = _claude_runtime.native_environment()
    environment.update(
        {
            "ANTHROPIC_API_KEY": "synthetic-local-only",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "CLAUDE_CONFIG_DIR": str(tmp_path / "synthetic-native-profile"),
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
    )
    monkeypatch.setattr(_claude_runtime, "native_environment", lambda: dict(environment))
    selected = ModelInfo(
        "claude-runtime/claude-sonnet-4-6",
        "Synthetic native CLI",
        "Claude",
        "claude_runtime",
        "subscription",
        transport="claude_code",
        provider_model_id="claude-sonnet-4-6",
        max_output_tokens=None,
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "open_door",
                "description": "Open the selected door using Sylvia's engine.",
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                },
            },
        }
    ]
    canonical = [
        {"role": "system", "content": "You are Sylvia."},
        {"role": "user", "content": "Open the door."},
    ]
    adapter = ClaudeProvider(tmp_path / "runtime", selected, executable=executable)
    try:
        first = await adapter.generate(canonical, tools, timeout=15)
        assert len(first.tool_calls) == 1
        call = first.tool_calls[0]
        assert (call.id, call.name, json.loads(call.arguments)) == (
            "call_native",
            "open_door",
            {"target": "door"},
        )
        assert adapter._active is None
        assert len(requests) == 1, (
            "the native runtime must stop before executing or continuing the tool"
        )
        canonical.extend(
            [
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": call.id, "content": "Opened once."},
            ]
        )
        # A fresh provider proves that continuation depends on Sylvia history, not a live process.
        reopened = ClaudeProvider(tmp_path / "runtime", selected, executable=executable)
        chunks = [chunk async for chunk in reopened.generate_stream(canonical, tools, timeout=15)]
        assert "".join(c.content for c in chunks) == "The door opens."
        assert chunks[-1].finish_reason == "stop"
        assert len(requests) == 2 and not errors
        assert not list((tmp_path / "runtime").iterdir())
        assert [t["name"] for t in requests[0]["tools"]] == ["mcp__sylvia__open_door"]
        final_history = requests[1]["messages"]
        assert final_history[0] == {"role": "user", "content": "Open the door."}
        assert final_history[1]["content"][0]["id"] == "call_native"
        assert final_history[2]["content"][0]["tool_use_id"] == "call_native"
        assert final_history[2]["content"][0]["content"] == "Opened once."
        assert len(canonical) == 4, "runtime invocation framing must never enter canonical history"
    finally:
        await adapter.close()
        server.shutdown()
        server.server_close()
