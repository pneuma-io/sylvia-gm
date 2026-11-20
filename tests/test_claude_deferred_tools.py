"""Deferred GM tool list on the Claude subscription route (``claude_code``, issue 204).

Sylvia declares one fixed catalog to Claude Code. The application's own tools are always
loaded (MCP ``_meta`` ``anthropic/alwaysLoad``); every Ruleset mechanic waits behind Claude
Code's tool search, and the model loads one with ``ToolSearch`` before its first call. Claude
Code answers that search itself, inside the step, so Sylvia keeps each round's messages with
the step's context records and replays them, and a fresh process sends what the earlier one
sent, with the same tools loaded.

The unit cases need no CLI. The session case is opt-in, like ``test_claude_native_wire``:
``SYLVIA_TEST_CLAUDE_WIRE_BINARY`` names a real ``claude`` binary, which runs against a local
fake Messages endpoint that plays a scripted GM. No real service is contacted and no real
credential is used.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from sylvia_gm.provider import _claude, _claude_kept, _claude_runtime
from sylvia_gm.provider._claude import (
    ALWAYS_LOAD,
    TOOL_SEARCH,
    ClaudeProvider,
    prepare_request,
    tool_search,
)
from sylvia_gm.provider._claude_native import (
    NativeRoute,
    collect,
    collect_answer,
)

MODEL = "claude-opus-5-5"
BINARY = os.environ.get("SYLVIA_TEST_CLAUDE_WIRE_BINARY")
DUMMY_KEY = "sk-ant-api03-OFFLINE-WIRE-TEST-DUMMY-KEY-NOT-A-CREDENTIAL-0000000000000000-AA"
SYLVIA_NAME = re.compile(r"mcp__sylvia__[A-Za-z0-9_-]+")
#: What the scripted GM says before each ToolSearch call; it must never reach the player.
SEARCH_TEXT = "SENTINEL_SEARCH_TEXT Loading that rule."


def _tool(name: str, deferred: bool) -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": name.replace("_", " ").title(),
        "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}}},
        **({"defer_loading": True} if deferred else {})}


DEFERRED_TOOLS = [_tool("read_reference", False), _tool("roll", True), _tool("cast", True)]
PLAIN_TOOLS = [_tool("read_reference", False), _tool("roll", False)]


# --- Unit cases: no CLI ----------------------------------------------------------------------


def test_deferred_tools_are_marked_for_claude_code_and_others_are_unchanged(tmp_path):
    messages = [{"role": "system", "content": "GM"}, {"role": "user", "content": "Hi."}]
    _system, _records, _prompt, native = prepare_request(
        messages, DEFERRED_TOOLS, MODEL, tmp_path)
    assert [(t["name"], t["_meta"]) for t in native] == [
        ("read_reference", {ALWAYS_LOAD: True}), ("roll", {ALWAYS_LOAD: False}),
        ("cast", {ALWAYS_LOAD: False})]
    assert tool_search(native)
    # Without a deferred tool nothing new is sent: no _meta, no tool search.
    _system, _records, _prompt, plain = prepare_request(messages, PLAIN_TOOLS, MODEL, tmp_path)
    assert all("_meta" not in tool for tool in plain)
    assert not tool_search(plain)
    assert set(plain[0]) == {"name", "description", "inputSchema"}


def _rows_file(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


PROMPT = [{"type": "text", "text": "I attack the guard."}]
ENVIRONMENT = {"type": "attachment", "attachment": {"type": "environment"},
               "rendered": [{"content": "<system-reminder>env</system-reminder>"}],
               "renderedRole": "system"}
DELTA = {"type": "attachment", "attachment": {"type": "deferred_tools_delta",
                                              "addedNames": ["mcp__sylvia__roll"]},
         "rendered": [{"content": "deferred tools: mcp__sylvia__roll"}],
         "renderedRole": "system"}
SEARCH = {"type": "assistant", "requestRef": "r1", "message": {
    "id": "msg_search", "role": "assistant", "model": MODEL, "type": "message",
    "content": [{"type": "tool_use", "id": "toolu_s", "name": TOOL_SEARCH,
                 "input": {"query": "select:mcp__sylvia__roll"}}], "stop_reason": "tool_use"}}
LOADED = {"type": "user", "sourceToolAssistantUUID": "x", "toolUseResult": {"matches": [
    "mcp__sylvia__roll"]}, "message": {"role": "user", "content": [{
        "type": "tool_result", "tool_use_id": "toolu_s",
        "content": [{"type": "tool_reference", "tool_name": "mcp__sylvia__roll"}]}]}}
FINAL = {"type": "assistant", "message": {
    "id": "msg_final", "role": "assistant", "model": MODEL, "type": "message",
    "content": [{"type": "tool_use", "id": "toolu_roll", "name": "mcp__sylvia__roll",
                 "input": {"n": 2}}], "stop_reason": "tool_use"}}


def test_collect_keeps_search_rounds_in_order_up_to_the_final_message(tmp_path):
    rows = [{"type": "user", "message": {"role": "user", "content": PROMPT}}, ENVIRONMENT,
            DELTA, {"type": "api-request"}, SEARCH, LOADED,
            {"type": "attachment", "attachment": {"type": "deferred_tools_record"}},
            {"type": "api-request"}, FINAL]
    session = _rows_file(tmp_path / "s.jsonl", rows)
    found = collect(session, PROMPT, final_message_id="msg_final")
    assert found.status == "collected"
    assert [r.get("type") or r["attachment"]["type"] for r in found.records] == [
        "environment", "deferred_tools_delta", "assistant", "user"]
    assert found.records[2] == {"type": "assistant", "message": SEARCH["message"]}
    assert found.records[3] == {"type": "user", "message": LOADED["message"],
                                "toolUseResult": LOADED["toolUseResult"]}
    # Without a search round (or the id) collection is what it was: rendered records only.
    assert [r["attachment"]["type"] for r in collect(session, PROMPT).records] == [
        "environment", "deferred_tools_delta"]


def test_collect_answer_keeps_rounds_after_the_answered_call(tmp_path):
    config = tmp_path / "profile"
    folder = config / "projects" / "ws"
    folder.mkdir(parents=True)
    answer = {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_prev", "content": "14"}]}}
    rows = [answer, {"type": "api-request"}, SEARCH, LOADED, {"type": "api-request"}, FINAL]
    _rows_file(folder / "sid.jsonl", rows)
    found = collect_answer(config, "sid", "toolu_prev", final_message_id="msg_final",
                           requests=2)
    assert found.status == "collected" and found.answer == answer["message"]
    assert [r["type"] for r in found.records] == ["assistant", "user"]
    # The CLI has not yet recorded the step's second request: unknown, never partial.
    _rows_file(folder / "sid.jsonl", rows[:3])
    assert collect_answer(config, "sid", "toolu_prev", 0.05, final_message_id="msg_final",
                          requests=2).status == "unknown"


def test_saved_rounds_are_replayed_after_the_prompt(tmp_path):
    messages = [
        {"role": "system", "content": "GM"},
        {"role": "user", "content": "I attack the guard."},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "toolu_roll", "type": "function",
            "function": {"name": "roll", "arguments": json.dumps({"n": 2})}}]},
        {"role": "tool", "tool_call_id": "toolu_roll", "content": "14"},
        {"role": "user", "content": "Continue."},
    ]
    saved = [{k: v for k, v in ENVIRONMENT.items() if k != "type"},
             {"type": "assistant", "message": SEARCH["message"]},
             {"type": "user", "message": LOADED["message"],
              "toolUseResult": LOADED["toolUseResult"]}]
    # The first request ended after the player's words (journal length 1) with the call.
    route = NativeRoute(stable_count=1, entry_count=4, boundaries=(1,),
                        native_records={1: saved})
    _system, records, _prompt, _tools = prepare_request(messages, DEFERRED_TOOLS, MODEL,
                                                        tmp_path, route)
    kinds = [(r["type"], (r.get("message") or {}).get("id")) for r in records]
    assert kinds[:5] == [("user", None), ("attachment", None), ("assistant", "msg_search"),
                         ("user", None), ("assistant", kinds[4][1])]
    assert records[3]["message"] == LOADED["message"]
    assert records[3]["toolUseResult"] == LOADED["toolUseResult"]
    assert records[4]["message"]["content"][0]["name"] == "mcp__sylvia__roll"
    # The chain stays linked: each record's parent is the one before it.
    assert all(b["parentUuid"] == a["uuid"] for a, b in zip(records, records[1:], strict=False))


class _Connection:
    """A Claude Code process at the stdio boundary that plays one scripted step."""

    instances: list[_Connection] = []

    def __init__(self, executable, workspace, arguments, environment, *, events):
        self.arguments, self.environment = arguments, environment
        self.events = iter(events)
        self.writes: list[dict[str, Any]] = []
        _Connection.instances.append(self)

    def start(self):
        pass

    def write(self, event):
        self.writes.append(event)

    def next_event(self, timeout):
        return next(self.events)

    def close(self):
        pass


def _stream(message_id: str, block: dict[str, Any], partial: str = "",
            said: str = "") -> list[dict[str, Any]]:
    """One native message: optional text said first, then *block*."""
    index = 1 if said else 0
    events = [
        {"type": "message_start", "message": {"id": message_id, "model": MODEL, "usage": {
            "input_tokens": 7, "cache_read_input_tokens": 90, "output_tokens": 0}}},
        *([{"type": "content_block_start", "index": 0,
            "content_block": {"type": "text", "text": ""}},
           {"type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": said}},
           {"type": "content_block_stop", "index": 0}] if said else []),
        {"type": "content_block_start", "index": index, "content_block": block},
        *([{"type": "content_block_delta", "index": index, "delta": {
            "type": "input_json_delta", "partial_json": partial}}] if partial else []),
        {"type": "content_block_stop", "index": index},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
         "usage": {"output_tokens": 4}},
        {"type": "message_stop"},
    ]
    return [{"type": "stream_event", "event": e} for e in events]


def test_a_search_round_is_no_sylvia_call_and_the_last_message_is_the_reply(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("SYLVIA_CLAUDE_PROCESS", "per-step")
    monkeypatch.setattr(_claude, "find_runtime", lambda _: Path(__file__))
    monkeypatch.setattr(_claude, "runtime_version", lambda *_: "2.1.327")
    init = {"type": "system", "subtype": "init", "model": MODEL,
            "tools": [TOOL_SEARCH, "mcp__sylvia__read_reference", "mcp__sylvia__roll",
                      "mcp__sylvia__cast"]}
    events = [
        init,
        *_stream("msg_search", {"type": "tool_use", "id": "toolu_s", "name": TOOL_SEARCH,
                                "input": {}}, json.dumps({"query": "select:mcp__sylvia__roll"}),
                 said="Loading the attack rules."),
        {"type": "assistant", "message": SEARCH["message"]},
        {"type": "user", "message": LOADED["message"]},
        *_stream("msg_final", {"type": "tool_use", "id": "toolu_roll",
                               "name": "mcp__sylvia__roll", "input": {}}, '{"n": 2}',
                 said="You swing."),
        {"type": "control_request", "request_id": "call", "request": {
            "subtype": "mcp_message", "server_name": "sylvia", "message": {
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "roll", "arguments": {"n": 2}}}}},
    ]
    _Connection.instances = []

    def factory(*args):
        return _Connection(*args, events=events)

    provider = ClaudeProvider(tmp_path / "runtime", _model(), connection_factory=factory)
    audits: list[dict[str, Any]] = []

    class Hook:
        async def on_request(self, context):
            audits.append(context.request_json)

        async def on_response(self, context):
            pass

        async def on_error(self, context):
            pass

    provider.hooks.register(Hook())
    messages = [{"role": "system", "content": "GM"},
                {"role": "user", "content": "I attack the guard."}]
    chunks = asyncio.run(_drain(provider, messages, DEFERRED_TOOLS))
    connection = _Connection.instances[-1]
    assert connection.environment["ENABLE_TOOL_SEARCH"] == "true"
    assert connection.arguments[connection.arguments.index("--tools") + 1] == TOOL_SEARCH
    allowed = connection.arguments[connection.arguments.index("--allowedTools") + 1]
    assert allowed.split(",")[0] == TOOL_SEARCH
    listed = json.loads(connection.arguments[connection.arguments.index("--mcp-config") + 1])
    assert listed == {"mcpServers": {"sylvia": {"type": "sdk", "name": "sylvia"}}}
    calls = [c.tool_call_delta for c in chunks if c.tool_call_delta]
    assert [c["function"]["name"] for c in calls] == ["roll"]
    # The text of the search round is never streamed; the reply's own text is.
    assert "".join(c.content for c in chunks if c.content) == "You swing."
    response = provider._last_response
    # The step's reply is its last model message; the search round is not part of it.
    assert response.content == "You swing."
    assert response.anthropic_content == [
        {"type": "text", "text": "You swing."},
        {"type": "tool_use", "id": "toolu_roll", "name": "roll", "input": {"n": 2}}]
    rounds = [a["params"]["rounds"] for a in audits if a.get("method") == "claude/tool_search"]
    assert rounds == [[{"loaded": ["roll"], "usage": {
        "input_tokens": 7, "cache_read_input_tokens": 90, "output_tokens": 4}}]]
    native = next(a for a in audits if a.get("method") == "claude/native_step")
    assert native["controls"]["builtin_tools"] == [TOOL_SEARCH]


def _model():
    from tests.test_claude_native_route import claude_model

    return claude_model()


async def _drain(provider, messages, tools):
    return [chunk async for chunk in provider.generate_stream(messages, tools)]


# --- Opt-in session case: a real Claude Code binary, a scripted GM endpoint --------------------


class ScriptedEndpoint:
    """127.0.0.1 only: a fake Messages endpoint that plays a scripted GM like ``ContractGM``.

    Each step is a tool (by name suffix) or prose. A tool the request has not loaded is first
    loaded with ``ToolSearch`` when the request offers it, as a model would; the step is
    taken by the call itself. Every request body is recorded.
    """

    def __init__(self) -> None:
        self.steps: list[tuple[str, Any]] = []
        self.position = 0
        self.requests: list[dict[str, Any]] = []
        self.offsite: list[str] = []
        self.failure: str | None = None
        self.searches = 0
        self.lock = threading.Lock()
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *args):
                pass

            def do_CONNECT(self):
                endpoint.offsite.append(f"CONNECT {self.path}")
                self.send_response(403)
                self.end_headers()

            def _any(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if self.path.startswith(("http://", "https://")):
                    host = urlsplit(self.path).hostname or ""
                    if host not in {"127.0.0.1", "localhost"}:
                        endpoint.offsite.append(f"{self.command} {self.path}")
                        self._json(403, {"error": "offline"})
                        return
                path = urlsplit(self.path).path
                if self.command == "POST" and path == "/v1/messages/count_tokens":
                    self._json(200, {"input_tokens": 1000})
                    return
                if self.command == "POST" and path == "/v1/messages":
                    body = json.loads(raw)
                    with endpoint.lock:
                        endpoint.requests.append(body)
                        number = len(endpoint.requests)
                        reply = endpoint.decide(body)
                    self._stream(body, number, reply)
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

            def _stream(self, body, number, reply):
                usage = {"input_tokens": 11, "output_tokens": 1,
                         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
                events = [("message_start", {"type": "message_start", "message": {
                    "id": f"msg_gm_{number:04d}", "type": "message", "role": "assistant",
                    "model": body.get("model"), "content": [], "stop_reason": None,
                    "stop_sequence": None, "usage": usage}})]
                if "tool" in reply:
                    index = 0
                    if reply["tool"] == TOOL_SEARCH:
                        # Models often say what they are about to load.
                        events += [
                            ("content_block_start", {"type": "content_block_start",
                             "index": 0, "content_block": {"type": "text", "text": ""}}),
                            ("content_block_delta", {"type": "content_block_delta",
                             "index": 0, "delta": {"type": "text_delta",
                                                   "text": SEARCH_TEXT}}),
                            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                        ]
                        index = 1
                    events += [
                        ("content_block_start", {"type": "content_block_start", "index": index,
                         "content_block": {"type": "tool_use", "id": f"toolu_gm_{number:04d}",
                                           "name": reply["tool"], "input": {}}}),
                        ("content_block_delta", {"type": "content_block_delta", "index": index,
                         "delta": {"type": "input_json_delta",
                                   "partial_json": json.dumps(reply["input"])}}),
                        ("content_block_stop", {"type": "content_block_stop", "index": index}),
                    ]
                    stop = "tool_use"
                else:
                    events += [
                        ("content_block_start", {"type": "content_block_start", "index": 0,
                         "content_block": {"type": "text", "text": ""}}),
                        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                         "delta": {"type": "text_delta", "text": reply["text"]}}),
                        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    ]
                    stop = "end_turn"
                events += [
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

    def decide(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.position >= len(self.steps):
            return {"text": "The scene holds."}
        kind, arguments = self.steps[self.position]
        if kind == "prose":
            self.position += 1
            return {"text": str(arguments)}
        text = json.dumps(body)
        names = sorted({n for n in SYLVIA_NAME.findall(text)
                        if n.removeprefix("mcp__sylvia__") == kind or n.endswith("_" + kind)})
        if len(names) != 1:
            self.failure = f"step {kind} needs one Sylvia tool; found {names}"
            return {"text": self.failure}
        name = names[0]
        tools = body.get("tools", [])
        loaded = any(t.get("name") == name and not t.get("defer_loading") for t in tools)
        referenced = f'"tool_name": "{name}"' in json.dumps(body.get("messages", []))
        if loaded or referenced:
            self.position += 1
            return {"tool": name, "input": arguments}
        if any(t.get("name") == TOOL_SEARCH for t in tools):
            self.searches += 1
            return {"tool": TOOL_SEARCH, "input": {"query": f"select:{name}"}}
        self.failure = f"step {kind}: {name} is neither loaded nor searchable"
        return {"text": self.failure}


class ScriptedClaude(ClaudeProvider):
    """Sylvia's real Claude provider; a step's late-bound arguments resolve before it runs."""

    def __init__(self, home: Path, endpoint: ScriptedEndpoint) -> None:
        super().__init__(home, _model())
        self.endpoint = endpoint

    async def generate_stream(self, messages, tools=None, timeout=120, **kwargs):
        endpoint = self.endpoint
        if endpoint.position < len(endpoint.steps) and callable(
                endpoint.steps[endpoint.position][1]):
            kind, arguments = endpoint.steps[endpoint.position]
            endpoint.steps[endpoint.position] = kind, arguments()
        async for chunk in super().generate_stream(messages, tools, timeout, **kwargs):
            yield chunk


def offline_runtime(monkeypatch, tmp_path: Path, endpoint: ScriptedEndpoint,
                    binary: str) -> None:
    """Sylvia's own Claude Code environment, with only the endpoint and a dummy key swapped."""
    person = tmp_path / "person"
    for folder in (person / "AppData" / "Roaming", person / "AppData" / "Local"):
        folder.mkdir(parents=True, exist_ok=True)
    for name, value in (("USERPROFILE", person), ("HOME", person),
                        ("APPDATA", person / "AppData" / "Roaming"),
                        ("LOCALAPPDATA", person / "AppData" / "Local")):
        monkeypatch.setenv(name, str(value))
    sylvias_environment = _claude_runtime.native_environment

    def offline_environment(runtime_home):
        environment = sylvias_environment(runtime_home)
        environment.update({
            "ANTHROPIC_BASE_URL": endpoint.base, "ANTHROPIC_API_KEY": DUMMY_KEY,
            "HTTP_PROXY": endpoint.base, "HTTPS_PROXY": endpoint.base,
            "http_proxy": endpoint.base, "https_proxy": endpoint.base,
            "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
        })
        return environment

    monkeypatch.setattr(_claude_runtime, "native_environment", offline_environment)
    monkeypatch.setattr(_claude, "native_environment", offline_environment)
    monkeypatch.setattr(_claude, "find_runtime", lambda _home: Path(binary))


def run_session(tmp_path: Path, monkeypatch, *, binary: str, seed: tuple[Path, dict],
                mode: str, process: str, refused_call: bool) -> dict[str, Any]:
    """The scripted D&D session of PR 208 on ``claude_code``; every request body is kept.

    Exploration (optionally a refused call first), a fight with a summoned dragon, an owner
    restart, exploration, a second fight and a rest.
    """
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter, _plain_json
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
    from sylvia_gm.application.authority import AuthorityOwner
    from tests.test_gm_context_contract import settle
    from tests.test_srd_public_spell_casting import SRD

    monkeypatch.setenv("SYLVIA_GM_TOOL_LIST", mode)
    monkeypatch.setenv("SYLVIA_CLAUDE_PROCESS", process)
    endpoint = ScriptedEndpoint()
    offline_runtime(monkeypatch, tmp_path, endpoint, binary)
    home = tmp_path / "claude-runtime"
    database = tmp_path / "session.db"
    shutil.copy2(seed[0], database)
    owner = AuthorityOwner.open(database, "cc-owner", disconnect_grace_seconds=1.0)
    provider = ScriptedClaude(home, endpoint)
    audits: list[dict[str, Any]] = []

    class Audit:
        async def on_request(self, context):
            request = context.request_json or {}
            if request.get("method") in {"claude/tool_search", "claude/process_reuse",
                                         "claude/invalid_response"}:
                audits.append(json.loads(json.dumps(request)))

        async def on_response(self, context):
            pass

        async def on_error(self, context):
            pass

    provider.hooks.register(Audit())
    refusals: list[str] = []
    committed: list[str] = []
    turn_ends: list[int] = []

    def turn(text, steps):
        endpoint.steps.extend(steps)
        identity = f"turn-{len(turn_ends)}"
        result = CampaignSessionLoop(owner).run(SessionRunRequest(
            session_id="cc-session", turn_id="casting-turn", attempt_id=identity,
            message_id=identity, user_text=text,
        ), ProviderSessionAdapter(owner, provider, owner_id="cc-owner").for_turn("casting-turn"))
        assert result.interruption is None, (result.interruption, endpoint.failure)
        assert endpoint.failure is None, endpoint.failure
        assert endpoint.position == len(endpoint.steps), (endpoint.position, endpoint.steps)
        for message in owner.session_context("cc-session").messages:
            evidence = message.content
            if (evidence.get("kind") == "provider_tool_results"
                    and evidence.get("basis_message_id") == identity):
                for record in evidence["results"]:
                    outcome = json.loads(record["content"])
                    if outcome.get("status") == "action_committed":
                        committed.append(record["name"])
                    if outcome.get("fixed_tool_refusal"):
                        refusals.append(outcome["fixed_tool_refusal"])
        turn_ends.append(len(endpoint.requests))

    try:
        first = [settle("The clearing is quiet.")]
        if refused_call:
            # A call to a tool the game state has not offered: refused, counted, corrected.
            first.insert(0, ("end_combat_encounter", {"end_reason": "fighting_stopped",
                                                      "known_spell_effect": False}))
        turn("We look around the clearing.", first)
        scene = _plain_json(owner.snapshot("casting").state[f"{SRD}.state"]["spatial_scene"])
        scene["zones"] = [{"id": "daylight", "map_id": "fixture",
                           "shape": {"kind": "box", "min": [-100, -100, -10],
                                     "max": [100, 100, 20]},
                           "metadata": {"lighting": "bright", "plane": "material"}}]
        summon = dict(seed[1])
        summon.pop("target")
        summon.update(summon_id="dragon_one", summon_distance=30,
                      caster_can_see_summon_space=True, summon_space_unoccupied=True,
                      selected_damage_type="fire", created_actor_position=[0, 30, 0],
                      created_actor_height=6, created_actor_lighting="bright")
        current = {"owner": owner}

        def completion():
            snapshot = current["owner"].snapshot("casting")
            pending = snapshot.state[f"{SRD}.state"]["pending_srd_action_stack"]
            return {"pending_action_id": pending[-1]}

        turn("Begin a practice fight, summon a dragon, then stop the fight.", [
            ("start_group_combat_encounter",
             {"combatants": ["hero", "guard", "other"], "combat_scenes": [scene]}),
            ("begin_srd_spell_action", summon),
            ("complete_srd_pending_action", completion),
            ("end_combat_encounter", {"end_reason": "fighting_stopped"}),
            settle("The summoned dragon remains as the practice fight ends."),
        ])
        # An owner restart: its Claude Code processes end with it; a new provider starts.
        owner.close()
        asyncio.run(provider.close())
        _claude_kept.KEPT_PROCESSES.close_all()
        restart_at = len(endpoint.requests)
        owner = AuthorityOwner.open(database, "cc-owner", disconnect_grace_seconds=1.0)
        current["owner"] = owner
        provider = ScriptedClaude(home, endpoint)
        provider.hooks.register(Audit())
        turn("Look around again.", [settle("The clearing is quiet again.")])
        turn("Begin another practice fight, then stop it.", [
            ("start_group_combat_encounter",
             {"combatants": ["hero", "guard", "other", "dragon_one"]}),
            ("end_combat_encounter", {"end_reason": "fighting_stopped",
                                      "known_spell_effect": False}),
            settle("The second practice fight ends."),
        ])
        turn("We rest by the stream.", [settle("You rest by the stream.")])
    finally:
        owner.close()
        asyncio.run(provider.close())
        endpoint.close()
    return {"mode": mode, "process": process, "requests": endpoint.requests,
            "turn_ends": turn_ends, "restart_at": restart_at, "refusals": refusals,
            "committed": committed, "audits": audits, "searches": endpoint.searches,
            "offsite": endpoint.offsite}


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _one_block(message: dict[str, Any]) -> dict[str, Any]:
    if isinstance(message.get("content"), str):
        return {**message, "content": [{"type": "text", "text": message["content"]}]}
    return message


def loaded_tools(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The tools in the model's context and cached prefix (deferred ones are outside both)."""
    return sorted((_clean(t) for t in body.get("tools", []) if not t.get("defer_loading")),
                  key=lambda t: t["name"])


def extends(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Does ``after`` keep ``before``'s whole prefix: system, loaded tools and messages?"""
    if _clean(before["system"]) != _clean(after["system"]):
        return False
    if loaded_tools(before) != loaded_tools(after):
        return False
    old, new = _clean(before["messages"]), _clean(after["messages"])
    if len(new) < len(old):
        return False
    # Claude Code sends its own environment message as a list once and a string later.
    return all(_one_block(a) == _one_block(b) for a, b in zip(old, new, strict=False))


@pytest.fixture(scope="module")
def claude_seed(tmp_path_factory):
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import WriterRole
    from tests.test_srd_public_spell_casting import _create_spell_campaign

    if not BINARY or not Path(BINARY).is_file():
        pytest.skip("SYLVIA_TEST_CLAUDE_WIRE_BINARY does not name a Claude Code binary")
    database = tmp_path_factory.mktemp("claude-deferred-seed") / "seed.db"
    with AuthorityOwner.open(database, "cc-seed") as owner:
        _state, _data, parameters = _create_spell_campaign(owner, "summon_dragon")
        owner.create_agent_session(campaign_id="casting", session_id="cc-session",
                                   role=WriterRole.DM, provider_id="claude_runtime",
                                   model_id=f"claude-runtime/{MODEL}")
    return database, parameters


@pytest.mark.parametrize("process", ["persistent", "per-step"])
def test_real_claude_code_deferred_session_keeps_one_tool_list(
    tmp_path, monkeypatch, claude_seed, process,
):
    # Opt-in prerequisite, run by hand, like tests/test_claude_native_wire.py.
    assert BINARY
    run = run_session(tmp_path, monkeypatch, binary=BINARY, seed=claude_seed,
                      mode="deferred", process=process, refused_call=True)
    requests = run["requests"]
    assert run["offsite"] == []
    assert len(requests) >= 12
    # One loaded tool list for the whole session: across exploration, both fights and the
    # owner restart. Only ToolSearch and the application's own tools are loaded.
    loaded = loaded_tools(requests[0])
    names = {tool["name"] for tool in loaded}
    assert TOOL_SEARCH in names
    assert all(not name.endswith("_begin_srd_spell_action") for name in names)
    assert len(names) < 20
    for body in requests:
        assert loaded_tools(body) == loaded
    # Every request extends the one before it, the restart included.
    breaks = [n for n, (a, b) in enumerate(zip(requests, requests[1:], strict=False), 2)
              if not extends(a, b)]
    assert breaks == []
    # What the GM said before a search stays in that search round, never in a reply.
    holding = [m for m in requests[-1]["messages"] if SEARCH_TEXT in json.dumps(m)]
    assert len(holding) == run["searches"]
    assert all(m["role"] == "assistant" and any(
        b.get("name") == TOOL_SEARCH for b in m["content"]) for m in holding)
    # Mechanics were loaded by the model through ToolSearch, each once: after the restart
    # the replayed rounds keep them loaded, so the second fight searches nothing again.
    rounds = [r for a in run["audits"] if a["method"] == "claude/tool_search"
              for r in a["params"]["rounds"]]
    loaded_names = [name for r in rounds for name in r["loaded"]]
    assert len(loaded_names) == len(set(loaded_names)) == run["searches"]
    assert any(name.endswith("_begin_srd_spell_action") for name in loaded_names)
    # The turn loop stays authoritative: the call it had not offered was refused and counted.
    assert run["refusals"].count("not_callable") == 1
    assert sum(name.endswith("_start_group_combat_encounter") for name in run["committed"]) == 2
    assert sum(name.endswith("_begin_srd_spell_action") for name in run["committed"]) == 1
    assert not [a for a in run["audits"] if a["method"] == "claude/invalid_response"]
    report = os.environ.get("SYLVIA_TEST_CLAUDE_WIRE_REPORT")
    if report:
        Path(report).with_stem(f"{Path(report).stem}-deferred-{process}").write_text(
            json.dumps(run), encoding="utf-8")
