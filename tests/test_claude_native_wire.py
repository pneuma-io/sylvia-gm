"""Opt-in wire check: Sylvia's real ClaudeProvider and a real Claude Code binary, offline.

Skipped unless ``SYLVIA_TEST_CLAUDE_WIRE_BINARY`` names a ``claude`` binary; it is run by hand
and is a declared product-case exclusion (``scripts/check_catalog.py``). The binary runs
against a local fake Messages endpoint (also its HTTP(S) proxy, so any other host is refused
and recorded) with a throwaway config directory and a dummy key. No real service is contacted
and no real credential is used. ``SYLVIA_TEST_CLAUDE_WIRE_REPORT`` optionally names a JSON file
for the per-pair results.

A chain like RECIPE 2.1 runs through the context ledger's native route. Every consecutive pair
of requests must have identical ``system`` and ``tools``, ``messages`` that extend (a string and
a one-text-block list count as equal only for the CLI's own ``messages[1]``), and a message-level
cache marker on a block that is still present, unchanged, in the next request.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from sylvia_gm.agent._context_ledger import ContextLedger
from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.provider import _claude, _claude_runtime
from sylvia_gm.provider._claude import ClaudeProvider
from sylvia_gm.provider._claude_native import CURRENT_NATIVE_ROUTE
from tests.gm_context_contract import provider_messages
from tests.test_claude_native_route import (
    OWNER,
    PREP,
    STABLE,
    TOOLS,
    call,
    claude_model,
    facts,
    player,
    result,
    state,
    talk,
)

BINARY = os.environ.get("SYLVIA_TEST_CLAUDE_WIRE_BINARY")
DUMMY_KEY = "sk-ant-api03-OFFLINE-WIRE-TEST-DUMMY-KEY-NOT-A-CREDENTIAL-0000000000000000-AA"


class FakeMessages:
    """127.0.0.1 only: records each request; replies with a tool call on request, else text."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.offsite: list[str] = []
        self.step = "unlabelled"
        self.lock = threading.Lock()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def do_CONNECT(self):
                fake.offsite.append(f"CONNECT {self.path}")
                self.send_response(403)
                self.end_headers()

            def _any(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if self.path.startswith(("http://", "https://")):
                    host = urlsplit(self.path).hostname or ""
                    if host not in {"127.0.0.1", "localhost"}:
                        fake.offsite.append(f"{self.command} {self.path}")
                        self._json(403, {"error": "offline"})
                        return
                path = urlsplit(self.path).path
                if self.command == "POST" and path == "/v1/messages/count_tokens":
                    self._json(200, {"input_tokens": 1000})
                    return
                if self.command == "POST" and path == "/v1/messages":
                    body = json.loads(raw)
                    with fake.lock:
                        fake.requests.append((fake.step, body))
                        number = len(fake.requests)
                    self._reply(body, number)
                    return
                self._json(200, {})

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _any  # noqa: N815

            def _json(self, code, value):
                data = json.dumps(value).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _reply(self, body, number):
                model = body.get("model", "claude-unknown")
                wanted = re.search(r"SENTINEL_REQUEST_TOOL=([A-Za-z0-9_-]+)", last_user(body))
                names = [t.get("name") for t in body.get("tools", [])]
                tool = next((n for n in names if wanted and n and n.endswith(wanted.group(1))),
                            None)
                usage = {"input_tokens": 11, "output_tokens": 1,
                         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
                events = [("message_start", {"type": "message_start", "message": {
                    "id": f"msg_wire_{number:04d}", "type": "message", "role": "assistant",
                    "model": model, "content": [], "stop_reason": None,
                    "stop_sequence": None, "usage": usage}})]
                if tool:
                    events += [
                        ("content_block_start", {"type": "content_block_start", "index": 0,
                         "content_block": {"type": "tool_use", "id": f"toolu_{fake.step}",
                                           "name": tool, "input": {}}}),
                        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                         "delta": {"type": "input_json_delta", "partial_json": '{"n": 2}'}}),
                    ]
                    stop = "tool_use"
                else:
                    events += [
                        ("content_block_start", {"type": "content_block_start", "index": 0,
                         "content_block": {"type": "text", "text": ""}}),
                        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                         "delta": {"type": "text_delta", "text": f"SENTINEL_REPLY {number}."}}),
                    ]
                    stop = "end_turn"
                events += [
                    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    ("message_delta", {"type": "message_delta", "delta": {
                        "stop_reason": stop, "stop_sequence": None},
                        "usage": {"output_tokens": 5}}),
                    ("message_stop", {"type": "message_stop"}),
                ]
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for name, data in events:
                    self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                    self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def last_user(body: dict[str, Any]) -> str:
    for message in reversed(body.get("messages", [])):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        return "\n".join(str(b.get("text") or b.get("content") or "") for b in content or [])
    return ""


def strip_cc(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: strip_cc(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [strip_cc(v) for v in value]
    return value


def ser(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def one_block(message: dict[str, Any]) -> dict[str, Any]:
    if isinstance(message.get("content"), str):
        return {**message, "content": [{"type": "text", "text": message["content"]}]}
    return message


def marks(body: dict[str, Any]) -> list[int]:
    return [i for i, m in enumerate(body["messages"]) if isinstance(m.get("content"), list)
            and any("cache_control" in block for block in m["content"])]


def pair_report(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    first, second = strip_cc(a), strip_cc(b)
    report = {"system": ser(first["system"]) == ser(second["system"]),
              "tools": ser(first.get("tools")) == ser(second.get("tools")),
              "messages": (len(first["messages"]), len(second["messages"]))}
    leading = 0
    for index, (old, new) in enumerate(zip(first["messages"], second["messages"],
                                           strict=False)):
        if index == 1:  # the CLI's own environment message: list in N, string in N+1
            old, new = one_block(old), one_block(new)
        if ser(old) != ser(new):
            report["first_difference"] = {"index": index, "before": ser(old)[:300],
                                          "after": ser(new)[:300]}
            break
        leading += 1
    report["extends"] = leading == len(first["messages"])
    report["strict_prefix"] = all(
        ser(o) == ser(n) for o, n in zip(first["messages"], second["messages"], strict=False))
    report["marks"] = marks(a)
    report["marker_survives"] = bool(report["marks"]) and all(i < leading for i in marks(a))
    return report


def private_files(home: Path) -> list[str]:
    """Files under the runtime home that still hold any chain content."""
    found = []
    for path in home.rglob("*"):
        if not path.is_file() or path.suffix == ".exe" or path.stat().st_size > 50_000_000:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"SENTINEL_" in data or b"Glasswater" in data:
            found.append(str(path.relative_to(home)))
    return found


LONG = ("SENTINEL_C2_USER multi-line JSON-heavy prompt:\n" + json.dumps(
    {"state": {"hp": 7, "inventory": ["rope", "lantern \"brass\"", "\\path\\x"],
               "notes": "line1\nline2\ttab é中 \U0001F600"}}, indent=2,
    ensure_ascii=False) + "\n\n  trailing spaces   \n" + "x" * 3000)


def chain() -> list[tuple[str, list[dict[str, Any]], bool]]:
    """(step, canonical, expects a tool call) like RECIPE 2.1, through the ledger."""
    one = [player(1, "SENTINEL_C1_USER I open the cellar door.")]
    two = [*one, talk(1, "SENTINEL_REPLY 1."), player(2, LONG)]
    three = [*two, talk(2, "SENTINEL_REPLY 2."),
             player(3, "SENTINEL_C3_USER I attack. SENTINEL_REQUEST_TOOL=roll")]
    four = [*three, call("toolu_3_tool", 2), result("toolu_3_tool", "SENTINEL_TR 14")]
    five = [*four, talk(4, "SENTINEL_REPLY 4."),
            player(5, "SENTINEL_C5_USER I pick the lock. SENTINEL_REQUEST_TOOL=roll")]
    six = [*five, call("toolu_5_tool", 2), result("toolu_5_tool", "SENTINEL_TR 3")]
    seven = [*six, talk(6, "SENTINEL_REPLY 6."), player(7, "SENTINEL_C7_USER Quill keeps watch.")]
    eight = [*seven, talk(7, "SENTINEL_REPLY 7."), player(8, "SENTINEL_C8_USER We move on.")]
    return [
        ("1_fresh", [*STABLE, PREP, state(1), *one], False),
        ("2_player", [*STABLE, PREP, state(1), *two], False),
        ("3_tool", [*STABLE, PREP, state(1), *three], True),
        ("4_continue_records", [*STABLE, PREP, state(2, 6), *four, facts("14")], False),
        ("5_tool", [*STABLE, PREP, state(2, 6), *five, facts("14")], True),
        ("6_continue_no_records", [*STABLE, PREP, state(2, 6), *six, facts("14")], False),
        ("7_consecutive_user", [*STABLE, PREP, state(3, 5), *seven], False),
        ("8_player", [*STABLE, PREP, state(3, 5), *eight], False),
    ]


def test_real_claude_code_requests_extend_each_other(tmp_path, monkeypatch, caplog):
    # Opt-in prerequisite, run by hand. The case is a declared product-case exclusion in
    # scripts/check_catalog.py, so the gate never requires it.
    if not BINARY or not Path(BINARY).is_file():
        pytest.skip("SYLVIA_TEST_CLAUDE_WIRE_BINARY does not name a Claude Code binary")
    fake = FakeMessages()
    home = tmp_path / "claude-runtime"
    person = tmp_path / "person"
    for folder in (person / "AppData" / "Roaming", person / "AppData" / "Local"):
        folder.mkdir(parents=True)
    for name, value in (("USERPROFILE", person), ("HOME", person),
                        ("APPDATA", person / "AppData" / "Roaming"),
                        ("LOCALAPPDATA", person / "AppData" / "Local")):
        monkeypatch.setenv(name, str(value))
    sylvias_environment = _claude_runtime.native_environment

    def offline_environment(runtime_home):
        # Sylvia's own environment with only the endpoint and a dummy key substituted.
        environment = sylvias_environment(runtime_home)
        environment.update({
            "ANTHROPIC_BASE_URL": fake.base, "ANTHROPIC_API_KEY": DUMMY_KEY,
            "HTTP_PROXY": fake.base, "HTTPS_PROXY": fake.base,
            "http_proxy": fake.base, "https_proxy": fake.base,
            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        })
        return environment

    monkeypatch.setattr(_claude_runtime, "native_environment", offline_environment)
    monkeypatch.setattr(_claude, "native_environment", offline_environment)
    monkeypatch.setattr(_claude, "find_runtime", lambda _home: Path(BINARY))
    provider = ClaudeProvider(home, claude_model())
    ledger = ContextLedger(session_id="wire-session", context_owner=OWNER,
                           transport="claude_code")
    steps: list[dict[str, Any]] = []
    try:
        for name, canonical, expects_tool in chain():
            frame = ledger.plan(canonical, request_context_id="ctx", tools=TOOLS,
                                check_saved_tip=False)
            ledger.commit(frame)
            route = ProviderSessionAdapter._claude_native_route(ledger, frame, None, False)
            fake.step = name
            before = len(fake.requests)

            async def run(frame=frame, route=route):
                CURRENT_NATIVE_ROUTE.set(route)
                return [c async for c in provider.generate_stream(
                    provider_messages(frame.wire), TOOLS, timeout=60)]

            chunks = asyncio.run(run())
            bodies = [body for _step, body in fake.requests[before:]]
            assert bodies, (name, "no request reached the fake endpoint")
            finish = chunks[-1].finish_reason
            assert (finish == "tool_calls") == expects_tool, (name, finish)
            leftovers = private_files(home)
            assert leftovers == [], (name, "request content left on disk", leftovers)
            native = ledger.state.native.get(len(frame.state.entries))
            steps.append({"name": name, "requests": len(bodies), "finish": finish,
                          "reset": frame.reset, "native": native and {
                              "status": native["status"],
                              "types": [r["attachment"]["type"] for r in native["records"]]},
                          "body": bodies[-1]})
            unknown = [r.getMessage() for r in caplog.records
                       if "context records are unknown" in r.getMessage()]
            steps[-1]["unknown"] = unknown
            caplog.clear()
            assert frame.reset in (None, "journal_started"), (name, frame.reset, steps[-2:])
            assert not unknown, (name, unknown)
        assert fake.offsite == []
        statuses = [s["native"]["status"] for s in steps if s["native"]]
        assert statuses and all(status == "collected" for status in statuses), steps
        assert steps[0]["native"]["types"] == ["environment", "model", "date"], steps[0]
        pairs = []
        for a, b in zip(steps, steps[1:], strict=False):
            report = pair_report(a["body"], b["body"])
            pairs.append({"pair": f"{a['name']} -> {b['name']}", **report})
        report_path = os.environ.get("SYLVIA_TEST_CLAUDE_WIRE_REPORT")
        if report_path:
            Path(report_path).write_text(json.dumps({
                "binary_version": _claude_runtime.runtime_version(Path(BINARY), home),
                "steps": [{k: v for k, v in s.items() if k != "body"} for s in steps],
                "pairs": pairs, "offsite": fake.offsite,
                "trailing_roles": [s["body"]["messages"][-1]["role"] for s in steps],
            }, indent=1, ensure_ascii=False), encoding="utf-8")
        for pair in pairs:
            assert pair["system"] and pair["tools"], pair
            assert pair["extends"], pair
            assert pair["marker_survives"], pair
        # No CLI filler and no non-user prefix reached any request.
        for step in steps:
            text = ser(step["body"])
            assert "MESSAGE FROM NON-USER SOURCE" not in text
            assert text.count("No response requested.") == ser(
                step["body"]["messages"]).count("No response requested.")
    finally:
        asyncio.run(provider.close())
        fake.close()
