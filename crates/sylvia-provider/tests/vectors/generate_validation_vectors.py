"""Generate reference vectors for the provider compatibility probe (D2).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_validation_vectors.py

Writes `validation_vectors.json`: `validate_provider_model` over scripted HTTP exchanges.
`httpx.Client` is replaced by one on an `httpx.MockTransport` that serves each case's replies in
order. A streamed reply arrives in the scripted pieces, so lines, `\\r\\n` and multi-byte
characters can be split across reads. For every case it records the clients, each request's method,
URL, Sylvia's headers and body (httpx's own `Host`, `Accept`, `Accept-Encoding`, `Connection`,
`User-Agent` and `Content-Length` left out), and the report or the failure. Settings files are
written in a temporary folder under the deterministic fake seal; every credential is a disposable
test value. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import httpx

from sylvia_gm.config import _credential_protection as protection
from sylvia_gm.config import provider_settings as settings
from sylvia_gm.config._resolver import ConfigError
from sylvia_gm.interfaces.owner_process import _json_value

HERE = Path(__file__).parent
LIBRARY_HEADERS = {"host", "accept", "accept-encoding", "connection", "user-agent", "content-length"}


def fake_seal(value: bytes, *, decrypt: bool) -> bytes:
    if decrypt:
        if not value.startswith(b"FAKE"):
            raise ConfigError(protection.UNAVAILABLE_MESSAGE)
        return value[4:][::-1]
    return b"FAKE" + value[::-1]


protection._platform_seal = fake_seal

TOKENS = {protection.UNAVAILABLE_MESSAGE: "<UNAVAILABLE>", protection._PROTECTION: "<PROTECTION>"}


def tokens(value):
    text = json.dumps(value, ensure_ascii=False)
    for original, token in TOKENS.items():
        text = text.replace(json.dumps(original, ensure_ascii=False)[1:-1], token)
    return json.loads(text)


def envelope_yaml(key: str, headers: dict) -> str:
    envelope = protection.protect_credentials(key, headers)
    return "{" + ", ".join(f"{k}: {json.dumps(v)}" for k, v in envelope.items()) + "}"


SEALED = envelope_yaml("sk-saved-disposable", {"X-Saved": "1"})


def wire(reply: dict) -> dict:
    """A scripted reply as bytes on the wire: an error, or status, headers and body pieces."""
    if "error" in reply:
        return {"error": reply["error"]}
    filler = None
    if "pieces" in reply:
        pieces = []
        for piece in reply["pieces"]:
            if isinstance(piece, dict) and "filler" in piece:
                filler = piece["filler"]
            else:
                pieces.append(piece.encode("utf-8") if isinstance(piece, str) else bytes.fromhex(piece["hex"]))
    elif "hex" in reply:
        pieces = [bytes.fromhex(reply["hex"])]
    else:
        pieces = [json.dumps(reply.get("json"), ensure_ascii=False).encode()]
    record = {"status": reply.get("status", 200), "headers": reply.get("headers", []), "pieces": [piece.hex() for piece in pieces]}
    if filler is not None:
        # A first piece of `filler` bytes of "x", kept out of the file as a count.
        record["filler"] = filler
    return record


class Exchange:
    def __init__(self, replies: list) -> None:
        self.replies = [wire(reply) for reply in replies]
        self.pending = list(self.replies)
        self.requests: list[dict] = []
        self.clients: list[dict] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({
            "method": request.method,
            "url": str(request.url),
            "headers": [[name.decode().lower(), value.decode()] for name, value in request.headers.raw
                        if name.decode().lower() not in LIBRARY_HEADERS],
            "body": request.content.decode("utf-8", "replace"),
        })
        if not self.pending:
            raise AssertionError(f"unscripted request {request.method} {request.url}")
        reply = self.pending.pop(0)
        if "error" in reply:
            raise httpx.ConnectError(reply["error"], request=request)
        pieces = [bytes.fromhex(piece) for piece in reply["pieces"]]
        if "filler" in reply:
            pieces.insert(0, b"x" * reply["filler"])
        return httpx.Response(reply["status"], headers=reply["headers"], content=iter(pieces), request=request)


REAL_CLIENT = httpx.Client
CURRENT: Exchange | None = None


def scripted_client(*args, **kwargs):
    assert CURRENT is not None
    CURRENT.clients.append({"timeout": kwargs.get("timeout"), "follow_redirects": kwargs.get("follow_redirects")})
    return REAL_CLIENT(*args, transport=httpx.MockTransport(CURRENT.handle), **kwargs)


settings.httpx.Client = scripted_client

SSE = [["content-type", "text/event-stream"]]


def ok(document) -> dict:
    return {"status": 200, "json": document}


def stream(*pieces, headers=None) -> dict:
    return {"status": 200, "headers": SSE if headers is None else headers, "pieces": list(pieces)}


def event(value) -> str:
    return "data: " + json.dumps(value) + "\n\n"


CHAT_CALL = event({"choices": [{"delta": {"tool_calls": [{"function": {"name": "sylvia_compatibility_probe", "arguments": "{}"}}]}}]})
LISTED = ok({"data": [{"id": "gpt-6-sol"}, {"id": "m"}]})
CHAT = {"preset_id": "custom", "base_url": "https://probe.example.test/v1", "credential": "k", "model_id": "m",
        "transport": "chat_completions"}

CASES = [
    {"name": "chat-ready", "request": CHAT, "replies": [LISTED, stream(CHAT_CALL, "data: [DONE]\n\n")]},
    {"name": "chat-no-tool", "request": CHAT, "replies": [LISTED, stream(event({"choices": [{"delta": {"content": "hi"}}]}))]},
    {"name": "chat-no-events", "request": CHAT, "replies": [LISTED, stream(": keep-alive\n\n", "data: not json\n\n", "data: [1]\n\n")]},
    {"name": "not-listed", "request": {**CHAT, "model_id": "absent"}, "replies": [LISTED, stream(CHAT_CALL)]},
    {"name": "catalog-invalid", "request": CHAT, "replies": [{"status": 200, "hex": "7b78"}, stream(CHAT_CALL)]},
    {"name": "catalog-not-dict", "request": CHAT, "replies": [ok([1]), stream(CHAT_CALL)]},
    {"name": "catalog-404", "request": CHAT, "replies": [{"status": 404, "json": {}}, stream(CHAT_CALL)]},
    {"name": "catalog-401", "request": CHAT, "replies": [{"status": 401, "json": {}}]},
    {"name": "catalog-503", "request": CHAT, "replies": [{"status": 503, "json": {}}]},
    {"name": "catalog-down", "request": CHAT, "replies": [{"error": "refused"}]},
    {"name": "stream-401", "request": CHAT, "replies": [LISTED, {"status": 403, "json": {}}]},
    {"name": "stream-400", "request": CHAT, "replies": [LISTED, {"status": 400, "json": {}}]},
    {"name": "stream-down", "request": CHAT, "replies": [LISTED, {"error": "reset"}]},
    {"name": "error-event", "request": CHAT, "replies": [LISTED, stream(event({"type": "error", "error": {}}))]},
    {"name": "unhashable-type", "request": CHAT, "replies": [LISTED, stream(event({"type": ["x"]}))]},
    {"name": "choices-int", "request": CHAT, "replies": [LISTED, stream(event({"choices": 5}))]},
    {"name": "choices-none", "request": CHAT, "replies": [LISTED, stream(event({"choices": None}))]},
    {"name": "choices-odd", "request": CHAT,
     "replies": [LISTED, stream(event({"choices": ["s", {"delta": 3}, {"delta": {"tool_calls": "ab"}}, {"delta": {"tool_calls": 0}}]}), CHAT_CALL)]},
    {"name": "tool-calls-int", "request": CHAT, "replies": [LISTED, stream(event({"choices": [{"delta": {"tool_calls": 7}}]}))]},
    {"name": "split-pieces", "request": CHAT,
     "replies": [LISTED, stream("da", "ta: {\"choices\": [{\"delta\": {\"tool_calls\": [{\"function\": {\"na", "me\": \"sylvia_compatibility_probe\"}}]}}]}\r",
                                "\n\r\ndata: {\"x\": \"é", {"hex": "e282"}, {"hex": "ac22"}, "}\n")]},
    {"name": "bad-utf8", "request": CHAT, "replies": [LISTED, stream({"hex": "646174613a207b2278223a2022ff227d0a"}, CHAT_CALL)]},
    {"name": "latin1", "request": CHAT,
     "replies": [LISTED, stream({"hex": "646174613a207b2278223a2022e9227d0a"}, CHAT_CALL, headers=[["content-type", "text/event-stream; charset=ISO-8859-1"]])]},
    {"name": "other-breaks", "request": CHAT, "replies": [LISTED, stream("data: {\"a\": 1} data: [DONE]\u0085" + CHAT_CALL)]},
    {"name": "no-trailing-newline", "request": CHAT, "replies": [LISTED, stream(CHAT_CALL.strip())]},
    {"name": "too-long", "request": CHAT, "replies": [LISTED, stream({"filler": 1024 * 1024 + 10}, "\n", CHAT_CALL)]},
    {"name": "responses", "request": {**CHAT, "transport": "responses"},
     "replies": [LISTED, stream(event({"type": "response.output_item.added", "item": {"type": "function_call", "name": "sylvia_compatibility_probe"}}))]},
    {"name": "responses-failed", "request": {**CHAT, "transport": "responses"},
     "replies": [LISTED, stream(event({"type": "response.failed"}))]},
    {"name": "anthropic", "request": {"preset_id": "anthropic", "credential": "sk-ant", "model_id": "claude-opus-4-6"},
     "replies": [ok({"data": [{"id": "claude-opus-4-6"}]}),
                 stream(event({"type": "content_block_start", "content_block": {"type": "tool_use", "name": "sylvia_compatibility_probe"}}))]},
    {"name": "anthropic-other-model", "request": {"preset_id": "anthropic", "credential": "sk-ant", "model_id": "claude-fable-5-2"},
     "replies": [ok({"data": []}), stream(event({"type": "message_start"}))]},
    {"name": "gemini", "request": {"preset_id": "gemini", "credential": "g-key", "transport": "gemini_generate_content", "model_id": "models/gemini 3/pro"},
     "replies": [ok({"data": []}), stream(event({"candidates": [{"content": {"parts": [{"functionCall": {"name": "sylvia_compatibility_probe"}}]}}]}))]},
    {"name": "gemini-odd", "request": {"preset_id": "gemini", "credential": "g-key", "transport": "gemini_generate_content", "model_id": "gemini-3-pro"},
     "replies": [ok({"data": []}), stream(event({"candidates": "x"}), event({"candidates": ["bad"]}))]},
    {"name": "gemini-parts-int", "request": {"preset_id": "gemini", "credential": "g-key", "transport": "gemini_generate_content", "model_id": "gemini-3-pro"},
     "replies": [ok({"data": []}), stream(event({"candidates": [{"content": {"parts": 3}}]}))]},
    {"name": "gemini-call-str", "request": {"preset_id": "gemini", "credential": "g-key", "transport": "gemini_generate_content", "model_id": "gemini-3-pro"},
     "replies": [ok({"data": []}), stream(event({"candidates": [{"content": {"parts": [{"functionCall": "f"}]}}]}))]},
    {"name": "opencode", "request": {"preset_id": "opencode_zen", "credential": "k", "model_id": "q", "headers": {"User-Agent": "mine", "X-Keep": "1"}},
     "replies": [ok({"data": []}), stream(CHAT_CALL)]},
    {"name": "local-no-key", "request": {"preset_id": "lm_studio", "model_id": "qwen"}, "replies": [ok({"data": [{"id": "qwen"}]}), stream(CHAT_CALL)]},
    {"name": "claude", "request": {"preset_id": "claude_runtime", "model_id": "x"}, "replies": []},
    {"name": "chatgpt", "request": {"preset_id": "chatgpt_subscription", "model_id": "x"}, "replies": []},
    {"name": "no-model", "request": {"preset_id": "lm_studio"}, "replies": []},
    {"name": "bad-model", "request": {"preset_id": "lm_studio", "model_id": "a\tb"}, "replies": []},
    {"name": "bad-transport", "request": {"preset_id": "lm_studio", "model_id": "m", "transport": "pigeon"}, "replies": []},
    {"name": "codex-transport", "request": {"preset_id": "lm_studio", "model_id": "m", "transport": "codex_app_server"},
     "replies": [ok({"data": []}), stream(CHAT_CALL)]},
    {"name": "saved", "user": "providers:\n  openai:\n    enabled: true\n    protected_credentials: " + SEALED + "\n    models: []\n",
     "request": {"provider_id": "openai", "model_id": "gpt-6-sol", "transport": "responses"},
     "replies": [ok({"data": [{"id": "gpt-6-sol"}]}), stream(event({"item": {"type": "function_call", "name": "sylvia_compatibility_probe"}}))]},
]


def outcome(call, case: dict) -> dict:
    global CURRENT
    CURRENT = Exchange(case["replies"])
    try:
        record = {"value": _json_value(call())}
    except ConfigError as error:
        record = {"error": {"class": "ConfigError", "message": str(error)}}
    except Exception as error:  # noqa: BLE001
        record = {"error": {"class": type(error).__name__, "message": str(error)}}
    record["clients"] = CURRENT.clients
    record["requests"] = CURRENT.requests
    record["unused_replies"] = len(CURRENT.pending)
    replies = CURRENT.replies
    CURRENT = None
    return replies, record


def main() -> int:
    for name in list(os.environ):
        if name.upper().startswith("SYLVIA_GM_") or name.upper().endswith("_PROXY") or name.upper() in {"SSL_CERT_FILE", "SSL_CERT_DIR"}:
            del os.environ[name]
    records = []
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "config.yaml"
        for case in CASES:
            if path.exists():
                path.unlink()
            if case.get("user") is not None:
                path.write_bytes(case["user"].encode("utf-8"))
            params = case["request"]
            call = lambda p=params: settings.validate_provider_model(
                path, provider_id=p.get("provider_id"), base_url=p.get("base_url"), credential=p.get("credential"),
                model_id=p.get("model_id"), transport=p.get("transport"), preset_id=p.get("preset_id"), headers=p.get("headers"),
            )
            replies, record = outcome(call, case)
            records.append(tokens({"name": case["name"], "user": case.get("user"), "request": params, "replies": replies, **record}))
    output = HERE / "validation_vectors.json"
    output.write_bytes((json.dumps({"cases": records}, ensure_ascii=True, indent=0) + "\n").encode("ascii"))
    print(f"{len(records)} cases -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
