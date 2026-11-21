"""Generate reference vectors for sylvia-provider's URL handling and request headers.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-provider/tests/vectors/generate_http_vectors.py

- `urlsplit`: `urllib.parse.urlsplit` with `hostname` and `port`, and `urlunsplit` of the parts.
- `inference_base_url`: `provider/_protocol_routes.py` for OpenCode and other bases.
- `sdk_urls`: the URL the OpenAI SDK sends a chat request to for a configured base URL.
- `headers`: the headers it sends, for configured headers, per-call extra headers and the
  `OPENAI_ORG_ID` / `OPENAI_PROJECT_ID` environment variables.

No network is used. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from sylvia_gm.provider._base import ModelInfo, RetryConfig
from sylvia_gm.provider._openai_compat import OpenAICompatProvider
from sylvia_gm.provider._protocol_routes import inference_base_url, is_opencode_inference

OUTPUT = Path(__file__).with_name("http_vectors.json")

URLS = [
    "http://127.0.0.1:1234/v1", "https://openrouter.ai/api/v1", "https://OpenRouter.AI/api/v1/",
    "HTTPS://API.GROQ.COM/openai/v1", " \t https://api.x.ai/v1", "https://api.openai.com/v1?x=1#f",
    "http://user:pw@Host.Example:8080/p", "http://[::1]:11434/v1", "http://[fe80::1%25Eth0]:80/",
    "http://[::1/v1", "http://::1]/v1", "http://[1.2.3.4]/", "http://[v1.fe]/", "http://[vz.x]/",
    "localhost:1234/v1", "//host/path", "/relative/path", "", "http://host:notaport/", "http://host:99999/",
    "mailto:someone@example.com", "1http://x/", "ht tp://x/", "http://a\nb.com/x\ty", "http://h/a?b?c#d#e",
    "https://opencode.ai/zen/v1", "https://opencode.ai/zen/go/v1/", "https://opencode.ai/inference/v1",
    "https://opencode.ai/inference/go/openai/v1", "https://opencode.ai/inference/anthropic/v1?k=1",
    "http://opencode.ai/zen/v1", "https://opencode.ai/inference/unknown/v1", "http://[::1]x/",
    "http://host:/", "http://@host/", "http://h%41st/", "http://HOST.%e9.com/",
]

PROTOCOLS = ["chat_completions", "responses", "anthropic_messages", "gemini_generate_content",
             "codex_app_server", "claude_code"]

SDK_BASES = [
    "http://127.0.0.1:1234/v1", "http://127.0.0.1:1234/v1/", "https://openrouter.ai/api/v1",
    "http://localhost:11434", "http://localhost:11434/", "https://api.example.com:443/v1",
    "http://api.example.com:80/v1", "https://Example.COM/V1", "http://h/v1?key=1", "http://h/a b/v1",
    "http://h/café/v1", "http://h/v1#frag", "http://h:8080", "https://opencode.ai/inference/go/openai/v1",
]


def split_case(url: str) -> dict:
    try:
        parts = urlsplit(url)
    except ValueError as error:
        return {"url": url, "error": str(error)}
    try:
        port = parts.port
        port_error = None
    except ValueError as error:
        port, port_error = None, str(error)
    return {"url": url, "parts": list(parts), "hostname": parts.hostname, "port": port,
            "port_error": port_error, "unsplit": urlunsplit(parts)}


class Capture(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"choices":[{"delta":{"content":"x"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'


async def sent_request(base_url: str, *, headers=None, extra_headers=None, api_key="k") -> dict:
    sent = []

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append({"url": str(request.url),
                     "headers": [[k.decode("latin-1"), v.decode("latin-1")] for k, v in request.headers.raw]})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Capture())

    info = ModelInfo(id="t/m", name="M", provider_name="P", provider_key="byok", provider_mode="byok")
    provider = OpenAICompatProvider(base_url=base_url, api_key=api_key, model_info=info,
                                    retry_config=RetryConfig(max_retries=0),
                                    http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                                    default_headers=headers)
    kwargs = {} if extra_headers is None else {"extra_headers": extra_headers}
    async for _ in provider.generate_stream([{"role": "user", "content": "hi"}], **kwargs):
        pass
    await provider.close()
    return sent[0]


async def header_cases() -> list[dict]:
    cases = [
        {"name": "plain"},
        {"name": "no-key", "api_key": ""},
        {"name": "configured", "headers": {"HTTP-Referer": "https://example.test", "X-Title": "Sylvia"}},
        {"name": "configured-overrides", "headers": {"Accept": "text/plain", "User-Agent": "Custom/1",
                                                      "Authorization": "Bearer other"}},
        {"name": "configured-case", "headers": {"content-type": "text/x", "x-one": "1"}},
        {"name": "extra", "extra_headers": {"x-opencode-session": "sylvia_abc", "x-opencode-client": "sylvia-gm",
                                            "User-Agent": "Sylvia-GM/0.4.1"}},
        {"name": "extra-lowercase-user-agent", "extra_headers": {"user-agent": "Sylvia-GM/0.4.1"}},
        {"name": "configured-and-extra", "headers": {"X-Title": "Sylvia", "x-two": "a"},
         "extra_headers": {"X-Title": "Other", "x-three": "b"}},
        {"name": "organization", "env": {"OPENAI_ORG_ID": "org-1", "OPENAI_PROJECT_ID": "proj-1"}},
        {"name": "organization-configured", "env": {"OPENAI_ORG_ID": "org-1"},
         "headers": {"OpenAI-Organization": "org-2"}},
    ]
    out = []
    for case in cases:
        saved = {key: os.environ.pop(key, None) for key in ("OPENAI_ORG_ID", "OPENAI_PROJECT_ID")}
        os.environ.update(case.get("env", {}))
        try:
            sent = await sent_request("http://127.0.0.1:1234/v1", headers=case.get("headers"),
                                      extra_headers=case.get("extra_headers"), api_key=case.get("api_key", "k"))
        finally:
            for key in ("OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
                os.environ.pop(key, None)
                if saved[key] is not None:
                    os.environ[key] = saved[key]
        out.append({**case, "sent_headers": sent["headers"]})
    return out


async def main_async() -> dict:
    return {
        "python": sys.version.split()[0],
        "httpx": httpx.__version__,
        "urlsplit": [split_case(url) for url in URLS],
        "inference_base_url": [[url, protocol, is_opencode_inference(url) if url else False,
                                inference_base_url(url, protocol)]
                               for url in URLS if url.startswith(("https://opencode", "http://opencode", "http://127"))
                               for protocol in PROTOCOLS],
        "sdk_urls": [[base, (await sent_request(base))["url"]] for base in SDK_BASES],
        "headers": await header_cases(),
    }


def main() -> int:
    document = asyncio.run(main_async())
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True, indent=1) + "\n").encode("ascii"))
    print(f"{len(document['urlsplit'])} urls, {len(document['headers'])} header cases -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
