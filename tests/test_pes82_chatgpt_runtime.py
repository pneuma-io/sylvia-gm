"""Exercise the installed App Server against synthetic local inference, without signing in."""

from __future__ import annotations

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.provider._admin_audit import AdminAuditedProvider
from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._chatgpt import ChatGPTProvider
from sylvia_gm.provider._codex_rpc import CodexConnection


@pytest.mark.asyncio
async def test_real_runtime_returns_tool_to_sylvia_and_retains_history(tmp_path: Path) -> None:
    executable = shutil.which("codex")
    if executable is None:
        pytest.skip("Codex runtime is not installed")
    requests: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_POST(self) -> None:
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            if len(requests) == 1:
                item = {
                    "type": "function_call",
                    "id": "fc_synthetic",
                    "call_id": "call_synthetic",
                    "namespace": "sylvia",
                    "name": "synthetic_action",
                    "arguments": '{"value":7}',
                    "status": "completed",
                }
            else:
                item = {
                    "type": "message",
                    "id": "msg_synthetic",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": "Action complete.", "annotations": []}
                    ],
                }
            events = [
                {
                    "type": "response.created",
                    "response": {"id": "resp_synthetic", "status": "in_progress", "output": []},
                },
                {"type": "response.output_item.added", "output_index": 0, "item": item},
            ]
            if len(requests) > 1:
                events.append(
                    {
                        "type": "response.output_text.delta",
                        "item_id": "msg_synthetic",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": "Action complete.",
                    }
                )
            events.extend(
                [
                    {"type": "response.output_item.done", "output_index": 0, "item": item},
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_synthetic",
                            "status": "completed",
                            "output": [item],
                            "usage": {"input_tokens": 100, "output_tokens": 5, "total_tokens": 105},
                        },
                    },
                ]
            )
            body = "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    class SyntheticConnection(CodexConnection):
        def call(self, method: str, params: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            # Only this test supplies an account and local endpoint. Production uses managed login.
            if method == "account/read":
                return {"account": {"type": "chatgpt"}}
            if method == "thread/start":
                params = {
                    **params,
                    "modelProvider": "pes82",
                    "config": {
                        "model_providers.pes82": {
                            "name": "Synthetic PES82",
                            "wire_api": "responses",
                            "base_url": f"http://127.0.0.1:{server.server_port}/v1",
                            "requires_openai_auth": False,
                            "supports_websockets": False,
                        },
                    },
                }
            return super().call(method, params, **kwargs)

    provider = ChatGPTProvider(
        tmp_path / "isolated-runtime",
        ModelInfo(
            id="chatgpt_subscription/gpt-5.6-sol",
            name="Sol",
            provider_name="ChatGPT",
            provider_key="chatgpt_subscription",
            provider_mode="subscription",
            transport="codex_app_server",
        ),
        executable=Path(executable),
        connection_factory=SyntheticConnection,
    )
    audit: list[Any] = []
    audited = AdminAuditedProvider(
        provider,
        sink=audit.append,
        session_id="synthetic",
        role="builder",
        provider_id="chatgpt_subscription",
        model_id="chatgpt-subscription/gpt-5.6-sol",
    )
    history = [
        {"role": "system", "content": "Synthetic system instruction."},
        {"role": "developer", "content": "Synthetic developer instruction."},
        {"role": "user", "content": "Perform synthetic action."},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "synthetic_action",
                "description": "Synthetic operation.",
                "parameters": {"type": "object", "properties": {"value": {"type": "integer"}}},
            },
        }
    ]
    try:
        first = await audited.generate(history, tools, timeout=20)
        assert len(first.tool_calls) == 1
        call = first.tool_calls[0]
        assert (call.id, call.name, json.loads(call.arguments)) == (
            "call_synthetic",
            "synthetic_action",
            {"value": 7},
        )
        assert provider._active is None
        assert len(requests) == 1, "Runtime must not continue or execute the Sylvia operation"
        history.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": call.id, "content": '{"applied":7}'},
            ]
        )
        second = await audited.generate(history, tools, timeout=20)
        assert second.content == "Action complete."
        assert not second.tool_calls
        assert len(requests) == 2
        final_input = requests[1]["input"]
        assert any(
            item.get("role") in {"system", "developer"}
            and any(
                "Synthetic system instruction." in part.get("text", "")
                for part in item.get("content", [])
            )
            for item in final_input
        )
        for original in history[1:3]:
            assert any(
                item.get("role") == original["role"]
                and any(part.get("text") == original["content"] for part in item.get("content", []))
                for item in final_input
            ), [
                (
                    item.get("role"),
                    [
                        part.get("text")
                        for part in item.get("content", [])
                        if part.get("text", "").startswith("Synthetic")
                    ],
                )
                for item in final_input
                if "role" in item
            ]
        assert any(
            item.get("type") == "function_call_output"
            and item.get("call_id") == call.id
            and item.get("output") == '{"applied":7}'
            for item in final_input
        )
        namespaces = [tool.get("name") for item in final_input for tool in item.get("tools", [])]
        assert "sylvia" in namespaces
        assert set(namespaces) <= {"sylvia", "functions"}
        exchanges = [
            record for record in audit if record.get("kind") == "provider_runtime_exchange"
        ]
        assert exchanges
        assert all(record["input_stage"] == "codex_app_server_boundary" for record in exchanges)
        assert any(record["request"]["method"] == "item/tool/call" for record in exchanges)
        assert any(record["request"]["method"] == "thread/start/result" for record in exchanges)
        assert not any(record.get("kind") == "provider_wire_request" for record in audit)
    finally:
        await provider.close()
        server.shutdown()
        server.server_close()
