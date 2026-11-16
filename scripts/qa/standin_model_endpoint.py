"""A stand-in model service for installed-application checks: no real model and no cost.

It speaks enough of the OpenAI-compatible chat API for Sylvia to connect, list one model and
complete Game Master turns, character chat replies and other requests, all with fixed text:

- ``GET /v1/models`` lists one model with a context size. Without a context size Sylvia
  assumes a small model and stops a full turn before sending it.
- ``POST /v1/chat/completions`` answers streaming or not. When the request offers Sylvia's
  Campaign File tool (named ``update_campaign_file`` or ending in ``_update_campaign_file``)
  and the last message is not a tool result, the reply calls that tool with an "unchanged"
  assessment and the player-facing narration, which is how a Game Master player turn ends.
  Otherwise the reply is plain narration: the opening request and character chat carry no
  tools, and a request that follows a tool result only needs prose.

Every request must carry the expected API key, so a passing turn also shows the saved key
reached the service. Each request is logged as one JSON line (method, path, whether the key
matched, how many tools were offered, which reply was sent) without any message content.

Usage: python3 standin_model_endpoint.py --port 8790 --key KEY --log requests.jsonl
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "standin-narrator"
# Sylvia sends a request only if its estimated size, plus the reply reserve, fits the context.
# A player turn offers about a hundred tools (94 in 0.4.x), about 300 KB of request, so the
# stand-in reports a context far larger than any real request.
CONTEXT_LENGTH = 4_000_000
# Fixed texts. The installed check looks for these on the page, so each kind of reply is
# recognisable: the opening and character chat use NARRATION, a player turn uses TURN_REPLY.
NARRATION = (
    "The stand-in narrator answers. A lantern sways over the crossroads inn as you arrive, "
    "and the keeper waves you toward the fire. What do you do?"
)
TURN_REPLY = (
    "The stand-in narrator resolves your action. The keeper nods, pours a cup of tea and "
    "slides it across the table. What do you do next?"
)
CAMPAIGN_FILE_TOOL = "update_campaign_file"


def campaign_file_tool_name(request: Mapping[str, object]) -> str | None:
    """Return the offered Campaign File tool's wire name, or None when it is not offered."""

    tools = request.get("tools")
    if not isinstance(tools, list):
        return None
    for tool in tools:
        function = tool.get("function") if isinstance(tool, Mapping) else None
        name = function.get("name") if isinstance(function, Mapping) else None
        if isinstance(name, str) and (
            name == CAMPAIGN_FILE_TOOL or name.endswith("_" + CAMPAIGN_FILE_TOOL)
        ):
            return name
    return None


def last_message_is_tool_result(request: Mapping[str, object]) -> bool:
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    last = messages[-1]
    return isinstance(last, Mapping) and last.get("role") == "tool"


def plan_reply(request: Mapping[str, object]) -> dict[str, object]:
    """Decide the reply for one chat request: a Campaign File tool call or plain narration."""

    tool = campaign_file_tool_name(request)
    if tool is not None and not last_message_is_tool_result(request):
        arguments = {
            "state_change": "unchanged",
            "state_change_summary": "The stand-in reply changes no durable campaign truth.",
            "revised_player_facing_response": [{"kind": "narration", "text": TURN_REPLY}],
        }
        return {"kind": "tool_call", "tool": tool, "arguments": json.dumps(arguments)}
    text = TURN_REPLY if tool is not None else NARRATION
    return {"kind": "text", "text": text}


def completion_body(reply: Mapping[str, object], created: int) -> dict[str, object]:
    """Build a non-streaming chat completion for a planned reply."""

    usage = {"prompt_tokens": 10, "completion_tokens": 30, "total_tokens": 40}
    if reply["kind"] == "tool_call":
        message: dict[str, object] = {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "standin-call-1",
                "type": "function",
                "function": {"name": reply["tool"], "arguments": reply["arguments"]},
            }],
        }
        finish = "tool_calls"
    else:
        message = {"role": "assistant", "content": reply["text"]}
        finish = "stop"
    return {
        "id": "standin-1", "object": "chat.completion", "created": created, "model": MODEL,
        "choices": [{"index": 0, "finish_reason": finish, "message": message}],
        "usage": usage,
    }


def stream_events(reply: Mapping[str, object], created: int) -> list[dict[str, object]]:
    """Build the chunks of a streaming chat completion for a planned reply."""

    base = {"id": "standin-1", "object": "chat.completion.chunk", "created": created,
            "model": MODEL}

    def chunk(delta: dict[str, object], finish: str | None = None) -> dict[str, object]:
        return {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

    events = [chunk({"role": "assistant", "content": ""})]
    if reply["kind"] == "tool_call":
        events.append(chunk({"tool_calls": [{
            "index": 0, "id": "standin-call-1", "type": "function",
            "function": {"name": reply["tool"], "arguments": ""},
        }]}))
        arguments = str(reply["arguments"])
        for start in range(0, len(arguments), 64):
            events.append(chunk({"tool_calls": [{
                "index": 0, "function": {"arguments": arguments[start:start + 64]},
            }]}))
        events.append(chunk({}, "tool_calls"))
    else:
        words = str(reply["text"]).split(" ")
        for position, word in enumerate(words):
            events.append(chunk({"content": word if position == len(words) - 1 else word + " "}))
        events.append(chunk({}, "stop"))
    events.append({**base, "choices": [],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 30, "total_tokens": 40}})
    return events


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    key = ""
    log_path = ""
    log_lock = threading.Lock()

    def log_message(self, *_arguments):  # keep the console quiet; the log file is the record
        return

    def _record(self, **facts: object) -> None:
        if not self.log_path:
            return
        entry = {"time": time.strftime("%H:%M:%S"), "method": self.command, "path": self.path,
                 **facts}
        with self.log_lock, open(self.log_path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry) + "\n")

    def _authorised(self) -> bool:
        return self.headers.get("Authorization", "") == f"Bearer {self.key}"

    def _json(self, status: int, payload: Mapping[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - the name is fixed by BaseHTTPRequestHandler
        ok = self._authorised()
        self._record(key_matched=ok)
        if not ok:
            return self._json(401, {"error": {"message": "stand-in: wrong or missing API key"}})
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"object": "list", "data": [
                {"id": MODEL, "object": "model", "context_length": CONTEXT_LENGTH},
            ]})
        return self._json(404, {"error": {"message": "stand-in: unknown path"}})

    def do_POST(self) -> None:  # noqa: N802 - the name is fixed by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        ok = self._authorised()
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            request = {}
        if not isinstance(request, dict):
            request = {}
        known_path = self.path.rstrip("/").endswith("/chat/completions")
        reply = plan_reply(request) if ok and known_path else None
        self._record(
            key_matched=ok,
            stream=bool(request.get("stream")),
            tools=len(request.get("tools") or []),
            messages=len(request.get("messages") or []),
            bytes=len(raw),
            reply=None if reply is None else reply["kind"],
        )
        if not ok:
            return self._json(401, {"error": {"message": "stand-in: wrong or missing API key"}})
        if reply is None:
            return self._json(404, {"error": {"message": "stand-in: unknown path"}})
        created = int(time.time())
        if not request.get("stream"):
            return self._json(200, completion_body(reply, created))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        for event in stream_events(reply, created):
            self.wfile.write(b"data: " + json.dumps(event).encode("utf-8") + b"\n\n")
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        self.close_connection = True


def make_server(port: int, key: str, log_path: str) -> ThreadingHTTPServer:
    """Create the stand-in on 127.0.0.1 (port 0 picks a free port); call serve_forever()."""

    handler = type("StandinHandler", (Handler,), {"key": key, "log_path": log_path})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--key", required=True, help="the API key the service accepts")
    parser.add_argument("--log", default="", help="request log (JSON lines, no message content)")
    arguments = parser.parse_args(argv)
    server = make_server(arguments.port, arguments.key, arguments.log)
    address = f"http://127.0.0.1:{server.server_address[1]}/v1"
    print(f"stand-in model endpoint on {address} (model {MODEL})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
