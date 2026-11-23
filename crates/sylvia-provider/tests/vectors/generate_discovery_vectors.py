"""Generate reference vectors for provider model discovery (D1).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_discovery_vectors.py

Writes `discovery_vectors.json`: `discover_provider_models` and `detect_local_providers` over
scripted HTTP exchanges. `httpx.Client` is replaced by one whose transport is an
`httpx.MockTransport` serving each case's replies in order. For every case the generator records:
each client's `timeout` and `follow_redirects`; each request's method, URL, the headers Sylvia sets
(httpx's own `Host`, `Accept`, `Accept-Encoding`, `Connection`, `User-Agent` and `Content-Length`
left out, names lowercased) and body; and the reply (as the owner sends it) or the failure's class
and text. Settings files are written in a temporary folder; protection is the deterministic fake
seal of the other provider vectors; the clock is fixed. Every credential is a disposable test value.
Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import datetime as real_datetime
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
FIXED_NOW = "2026-11-22T08:00:00.123456+00:00"
LIBRARY_HEADERS = {"host", "accept", "accept-encoding", "connection", "user-agent", "content-length"}


def fake_seal(value: bytes, *, decrypt: bool) -> bytes:
    if decrypt:
        if not value.startswith(b"FAKE"):
            raise ConfigError(protection.UNAVAILABLE_MESSAGE)
        return value[4:][::-1]
    return b"FAKE" + value[::-1]


protection._platform_seal = fake_seal


class FixedDatetime(real_datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return real_datetime.datetime.fromisoformat(FIXED_NOW)


settings.datetime = FixedDatetime

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


class Exchange:
    """The scripted replies of one case and what the code under test sent."""

    def __init__(self, replies: list) -> None:
        self.replies = list(replies)
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
        if not self.replies:
            raise AssertionError(f"unscripted request {request.method} {request.url}")
        reply = self.replies.pop(0)
        if "error" in reply:
            raise httpx.ConnectError(reply["error"], request=request)
        content = bytes.fromhex(reply["hex"]) if "hex" in reply else json.dumps(reply.get("json"), ensure_ascii=False).encode()
        return httpx.Response(reply.get("status", 200), content=content, request=request)


REAL_CLIENT = httpx.Client
CURRENT: Exchange | None = None


def scripted_client(*args, **kwargs):
    assert CURRENT is not None
    CURRENT.clients.append({"timeout": kwargs.get("timeout"), "follow_redirects": kwargs.get("follow_redirects")})
    return REAL_CLIENT(*args, transport=httpx.MockTransport(CURRENT.handle), **kwargs)


settings.httpx.Client = scripted_client


def ok(document) -> dict:
    return {"status": 200, "json": document}


OPENAI_ROWS = {"data": [{"id": "gpt-6-sol", "object": "model"}, {"id": "gpt-6-luna"}, {"id": "gpt-6-sol"},
                        {"id": "text-embedding-3"}, "not-a-row", {"id": 5}]}
CASES = [
    {"name": "openai-needs-key", "request": {"preset_id": "openai"}, "replies": []},
    {"name": "openai", "request": {"preset_id": "openai", "credential": " sk-disposable "}, "replies": [ok(OPENAI_ROWS)]},
    {"name": "openai-search", "request": {"preset_id": "openai", "credential": "k", "query": "  LUNA "}, "replies": [ok(OPENAI_ROWS)]},
    {"name": "openai-page", "request": {"preset_id": "openai", "credential": "k", "offset": 1, "limit": 1}, "replies": [ok(OPENAI_ROWS)]},
    {"name": "openai-offset-past", "request": {"preset_id": "openai", "credential": "k", "offset": 9}, "replies": [ok(OPENAI_ROWS)]},
    {"name": "401", "request": {"preset_id": "openai", "credential": "k"}, "replies": [{"status": 401, "json": {}}]},
    {"name": "404", "request": {"preset_id": "openrouter", "credential": "k"}, "replies": [{"status": 404, "json": {}}]},
    {"name": "500", "request": {"preset_id": "openai", "credential": "k"}, "replies": [{"status": 503, "json": {}}]},
    {"name": "not-json", "request": {"preset_id": "openai", "credential": "k"}, "replies": [{"status": 200, "hex": "7b6e6f"}]},
    {"name": "utf16-json", "request": {"preset_id": "openai", "credential": "k"},
     "replies": [{"status": 200, "hex": "fffe" + json.dumps({"data": [{"id": "gpt-6-sol"}]}).encode("utf-16-le").hex()}]},
    {"name": "connect-error", "request": {"preset_id": "openai", "credential": "k"}, "replies": [{"error": "refused"}]},
    {"name": "bad-catalog", "request": {"preset_id": "openai", "credential": "k"}, "replies": [ok({"data": {"id": "x"}})]},
    {"name": "list-document", "request": {"preset_id": "openai", "credential": "k"}, "replies": [ok([{"id": "x"}])]},
    {"name": "together-list", "request": {"preset_id": "together", "credential": "k"},
     "replies": [ok([{"id": "meta/llama", "type": "chat", "context_length": 8192}, {"id": "img", "type": "image"}])]},
    {"name": "anthropic-pages", "request": {"preset_id": "anthropic", "credential": "sk-ant"},
     "replies": [ok({"data": [{"id": "claude-opus-4-6", "display_name": "Claude Opus 4.6"}], "has_more": True, "last_id": "a b/c+é"}),
                 ok({"data": [{"id": "claude-sonnet-4-5"}], "has_more": False})]},
    {"name": "anthropic-cursor-repeat", "request": {"preset_id": "anthropic", "credential": "sk-ant"},
     "replies": [ok({"data": [{"id": "a"}], "has_more": True, "last_id": "c1"}), ok({"data": [{"id": "b"}], "has_more": True, "last_id": "c1"})]},
    {"name": "anthropic-empty-page", "request": {"preset_id": "anthropic", "credential": "sk-ant"},
     "replies": [ok({"data": [], "has_more": True, "last_id": "c1"})]},
    {"name": "anthropic-headers", "request": {"preset_id": "anthropic", "credential": "sk-ant",
                                              "headers": {"x-api-key": "custom", "X-Extra": "e"}},
     "replies": [ok({"data": []})]},
    {"name": "featherless", "request": {"preset_id": "featherless", "credential": "k"},
     "replies": [ok({"data": [{"id": "f/one", "context_length": 32768}, {"id": "f/two"}]}), ok({"max_context_length": 16384})]},
    {"name": "featherless-no-plan", "request": {"preset_id": "featherless", "credential": "k"},
     "replies": [ok({"data": [{"id": "f/one", "context_length": 32768}]}), {"status": 500, "json": {}}]},
    {"name": "featherless-bad-limit", "request": {"preset_id": "featherless", "credential": "k"},
     "replies": [ok({"data": [{"id": "f/one"}]}), ok({"max_context_length": 100})]},
    {"name": "featherless-null-limit", "request": {"preset_id": "featherless", "credential": "k"},
     "replies": [ok({"data": [{"id": "f/one", "context_length": 4096}]}), ok({"max_context_length": None})]},
    {"name": "lm-studio", "request": {"preset_id": "lm_studio"},
     "replies": [ok({"data": [{"id": "qwen"}, {"id": "qwen:2"}, {"id": "other"}]}),
                 ok({"models": [{"type": "llm", "key": "qwen", "display_name": "Qwen", "max_context_length": 32768,
                                 "loaded_instances": [{"id": "qwen:2", "config": {"context_length": 8192}}],
                                 "capabilities": {"trained_for_tool_use": True}},
                                {"type": "llm", "key": "native-only", "max_context_length": 4096,
                                 "loaded_instances": [{"id": "n1", "config": {"context_length": 2048}}]},
                                {"type": "embedding", "key": "emb"}, {"type": "llm", "key": ""}]})]},
    {"name": "lm-studio-native-fails", "request": {"preset_id": "lm_studio", "base_url": "http://127.0.0.1:4321/v1"},
     "replies": [ok({"data": [{"id": "qwen"}]}), {"error": "refused"}]},
    {"name": "ollama", "request": {"preset_id": "ollama"},
     "replies": [ok({"data": [{"id": "llama3.1:8b"}, {"id": "mistral:7b"}]}),
                 ok({"models": [{"model": "llama3.1:8b", "context_length": 8192}, {"name": "mistral:7b", "context_length": 100}]}),
                 ok({"model_info": {"llama.context_length": 131072, "general.context_length": 4096, "other": 9}}),
                 {"status": 404, "json": {}}]},
    {"name": "ollama-show-fails", "request": {"preset_id": "ollama"},
     "replies": [ok({"data": [{"id": "m"}]}), {"error": "refused"}, {"error": "refused"}]},
    {"name": "claude", "request": {"preset_id": "claude_runtime"}, "replies": []},
    {"name": "chatgpt", "request": {"preset_id": "chatgpt_subscription"}, "replies": []},
    {"name": "unknown-preset", "request": {"preset_id": "nope"}, "replies": []},
    {"name": "no-preset", "request": {}, "replies": []},
    {"name": "bad-offset", "request": {"preset_id": "lm_studio", "offset": -1}, "replies": []},
    {"name": "bool-offset", "request": {"preset_id": "lm_studio", "offset": True}, "replies": []},
    {"name": "bad-limit", "request": {"preset_id": "lm_studio", "limit": 201}, "replies": []},
    {"name": "zero-limit", "request": {"preset_id": "lm_studio", "limit": 0}, "replies": []},
    {"name": "long-query", "request": {"preset_id": "lm_studio", "query": "q" * 257}, "replies": []},
    {"name": "bad-headers", "request": {"preset_id": "lm_studio", "headers": {"Bad Name": "1"}}, "replies": []},
    {"name": "fixed-endpoint", "request": {"preset_id": "openai", "credential": "k", "base_url": "https://elsewhere.test/v1"}, "replies": []},
    {"name": "custom", "request": {"preset_id": "custom", "credential": "k", "base_url": "https://models.example.test/v1/",
                                    "headers": {"X-Org": "o"}},
     "replies": [ok({"data": [{"id": "z-model"}, {"id": "a-model"}]})]},
    {"name": "saved-connection", "user": "providers:\n  openai:\n    enabled: true\n    protected_credentials: " + SEALED + "\n    models: []\n",
     "request": {"provider_id": "openai"}, "replies": [ok({"data": [{"id": "gpt-6-sol"}]})]},
    {"name": "saved-connection-new-key", "user": "providers:\n  openai:\n    enabled: true\n    protected_credentials: {version: 1, protection: nope, data: AAAA}\n    models: []\n",
     "request": {"provider_id": "openai", "credential": "sk-new"}, "replies": [ok({"data": []})]},
    {"name": "saved-connection-locked", "user": "providers:\n  openai:\n    enabled: true\n    protected_credentials: {version: 1, protection: nope, data: AAAA}\n    models: []\n",
     "request": {"provider_id": "openai"}, "replies": []},
    {"name": "saved-disabled", "user": "providers:\n  openai:\n    enabled: false\n    models: []\n",
     "request": {"provider_id": "openai", "credential": "k"}, "replies": [ok({"data": []})]},
]

DETECT_CASES = [
    {"name": "all-down", "replies": [{"error": "refused"}] * 5},
    {"name": "mixed", "replies": [ok({"data": [{"id": "a"}]}), ok({"data": [{"id": "a"}, {"id": "b"}]}), {"status": 401, "json": {}},
                                  {"status": 503, "json": {}}, {"status": 200, "hex": "6e6f"}]},
    {"name": "no-data", "replies": [ok({"other": 1}), ok({"data": []}), ok([1]), {"status": 404, "json": {}}, ok({"data": "x"})]},
]


def wire(reply: dict) -> dict:
    """A scripted reply as the bytes on the wire: an error, or a status and the exact body."""
    if "error" in reply:
        return {"error": reply["error"]}
    content = bytes.fromhex(reply["hex"]) if "hex" in reply else json.dumps(reply.get("json"), ensure_ascii=False).encode()
    return {"status": reply.get("status", 200), "hex": content.hex()}


def outcome(call, path: Path, case: dict) -> dict:
    global CURRENT
    CURRENT = Exchange(case["replies"])
    try:
        value = call()
        record = {"value": _json_value(value)}
    except ConfigError as error:
        record = {"error": {"class": "ConfigError", "message": str(error)}}
    except Exception as error:  # noqa: BLE001
        record = {"error": {"class": type(error).__name__, "message": str(error)}}
    record["clients"] = CURRENT.clients
    record["requests"] = CURRENT.requests
    record["unused_replies"] = len(CURRENT.replies)
    CURRENT = None
    return record


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
            call = lambda p=params: settings.discover_provider_models(
                path, provider_id=p.get("provider_id"), base_url=p.get("base_url"), credential=p.get("credential"),
                preset_id=p.get("preset_id"), query=p.get("query", ""), offset=p.get("offset", 0),
                limit=p.get("limit", 100), headers=p.get("headers"),
            )
            records.append(tokens({"name": case["name"], "user": case.get("user"), "request": params,
                                   "replies": [wire(reply) for reply in case["replies"]], **outcome(call, path, case)}))
        detect = []
        for case in DETECT_CASES:
            detect.append({"name": case["name"], "replies": [wire(reply) for reply in case["replies"]],
                           **outcome(lambda: settings.detect_local_providers(path), path, case)})
    output = HERE / "discovery_vectors.json"
    output.write_bytes((json.dumps({"now": FIXED_NOW, "discover": records, "detect": detect}, ensure_ascii=True, indent=0) + "\n").encode("ascii"))
    print(f"{len(records)} discovery and {len(detect)} detection cases -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
