"""Generate reference vectors for `sylvia_provider::net::wire_headers`: httpx's header order.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_wire_header_vectors.py

Each case is one `httpx.Client().build_request(...)` call. The vector records:

- `request`: the method, URL, body and headers as Sylvia's Rust code hands them to the transport.
  These are the call's headers; for a `json=` body, Sylvia adds `Content-Length` and then
  `Content-Type` itself, as httpx's JSON encoding does.
- `wire`: the header list httpx builds, in order, less the client defaults the Rust client does
  not send (differences row 8: `Accept-Encoding`, `Connection` and httpx's own `User-Agent`).

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

OUTPUT = Path(__file__).with_name("wire_header_vectors.json")
ROW_8 = {b"accept-encoding", b"connection"}
DEFAULT_AGENT = httpx.Client().headers["User-Agent"]

CASES: list[dict[str, object]] = [
    {"name": "discovery", "method": "GET", "url": "http://127.0.0.1:5000/v1/models",
     "headers": [["User-Agent", "SylviaGM/0.4.1"], ["Authorization", "Bearer k"],
                 ["X-Example", "d"]]},
    {"name": "no_headers", "method": "GET", "url": "https://api.example.test/v1/models"},
    {"name": "default_port", "method": "GET", "url": "https://api.example.test:443/v1/models"},
    {"name": "other_port", "method": "GET", "url": "http://H.Example:8080/x"},
    {"name": "ipv6", "method": "GET", "url": "http://[::1]:8080/x"},
    {"name": "userinfo", "method": "GET", "url": "http://user:pass@h:81/x"},
    {"name": "accept_given", "method": "GET", "url": "http://h/x",
     "headers": [["Authorization", "Bearer k"], ["Accept", "application/json"]]},
    {"name": "accept_lowercase", "method": "GET", "url": "http://h/x",
     "headers": [["accept", "text/plain"], ["X-A", "1"]]},
    {"name": "agent_given_later", "method": "GET", "url": "http://h/x",
     "headers": [["Authorization", "Bearer k"], ["User-Agent", "Mine"]]},
    {"name": "host_given", "method": "GET", "url": "http://h/x",
     "headers": [["X-A", "1"], ["Host", "other"]]},
    {"name": "duplicates", "method": "GET", "url": "http://h/x",
     "headers": [["X-A", "1"], ["x-a", "2"]]},
    {"name": "json_post", "method": "POST", "url": "http://h:11434/api/show",
     "headers": [["User-Agent", "SylviaGM/0.4.1"]], "json": {"model": "qwen", "verbose": False}},
    {"name": "json_post_private", "method": "POST", "url": "http://h/v1/chat/completions",
     "headers": [["User-Agent", "SylviaGM/0.4.1"], ["Authorization", "Bearer k"],
                 ["X-Example", "d"]], "json": {"model": "m", "messages": [], "stream": True}},
    {"name": "sdk_post", "method": "POST", "url": "http://h/v1/chat/completions",
     "headers": [["Authorization", "Bearer k"], ["Accept", "application/json"],
                 ["Content-Type", "application/json"], ["User-Agent", "SylviaGM/0.4.1"]],
     "content": "{\"model\":\"m\"}"},
    {"name": "empty_post", "method": "POST", "url": "http://h/x",
     "headers": [["Authorization", "Bearer k"]]},
    {"name": "empty_put", "method": "PUT", "url": "http://h/x"},
    {"name": "empty_patch", "method": "PATCH", "url": "http://h/x"},
    {"name": "empty_delete", "method": "DELETE", "url": "http://h/x"},
    {"name": "content_length_given", "method": "POST", "url": "http://h/x",
     "headers": [["Content-Length", "2"], ["X-A", "1"]], "content": "{}"},
]


def main() -> None:
    client = httpx.Client()
    vectors = []
    for case in CASES:
        headers = [tuple(pair) for pair in case.get("headers", [])]
        kwargs: dict[str, object] = {"headers": headers}
        body = b""
        given = list(headers)
        if "json" in case:
            kwargs["json"] = case["json"]
        elif "content" in case:
            kwargs["content"] = str(case["content"]).encode()
        request = client.build_request(str(case["method"]), str(case["url"]), **kwargs)
        if "json" in case or "content" in case:
            body = request.read()
        if "json" in case:
            given += [("Content-Length", str(len(body))), ("Content-Type", "application/json")]
        wire = [
            [name.decode("latin-1"), value.decode("latin-1")]
            for name, value in request.headers.raw
            if name.lower() not in ROW_8
            and not (name.lower() == b"user-agent" and value.decode() == DEFAULT_AGENT)
        ]
        vectors.append({
            "name": case["name"],
            "request": {"method": case["method"], "url": case["url"],
                        "headers": [list(pair) for pair in given],
                        "body": body.decode("utf-8")},
            "wire": wire,
        })
    text = json.dumps({"httpx": httpx.__version__, "cases": vectors}, indent=1,
                      ensure_ascii=False) + "\n"
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(len(vectors), "cases")


if __name__ == "__main__":
    main()
