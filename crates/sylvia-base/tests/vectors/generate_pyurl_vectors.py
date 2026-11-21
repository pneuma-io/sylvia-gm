"""Generate reference vectors for sylvia_base::pyurl from the frozen Python reference.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-base/tests/vectors/generate_pyurl_vectors.py

For each URL: `urlsplit`'s five parts or its `ValueError` text, then `hostname`, `port` (or its
error), `username`, `password` and `urlunsplit`. For each text: `unquote(text, errors="strict")`
or its error. The corpus joins lane 5's updater locations (`generate_update_vectors.py`) and
lane 4's provider endpoints (`generate_http_vectors.py`), with seeded random locations built
from the pieces the checks look at. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import importlib.util
import json
import random
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

OUTPUT = Path(__file__).parent / "pyurl_vectors.json"

PROVIDER_URLS = [
    "http://127.0.0.1:1234/v1", "https://openrouter.ai/api/v1", "https://OpenRouter.AI/api/v1/",
    "HTTPS://API.GROQ.COM/openai/v1", " \t https://api.x.ai/v1", "https://api.openai.com/v1?x=1#f",
    "http://user:pw@Host.Example:8080/p", "http://[::1]:11434/v1", "http://[fe80::1%25Eth0]:80/",
    "http://[::1/v1", "http://::1]/v1", "http://[1.2.3.4]/", "http://[v1.fe]/", "http://[vz.x]/",
    "localhost:1234/v1", "//host/path", "/relative/path", "", "http://host:notaport/", "http://host:99999/",
    "mailto:someone@example.com", "1http://x/", "ht tp://x/", "http://a\nb.com/x\ty", "http://h/a?b?c#d#e",
    "https://opencode.ai/zen/v1", "https://opencode.ai/inference/go/openai/v1", "http://[::1]x/",
    "http://host:/", "http://@host/", "http://h%41st/", "http://HOST.%e9.com/",
    "http://user@host/", "http://user:@host/", "http://:pw@host/", "http://a:b:c@host:1/", "http://a@b@host/",
    "http://host:0080/", "http://host:00000/", "http://host:65535/", "http://host:65536/", "http://host:-1/",
    "http://host:+1/", "http://host:\u0661/", "http://host:" + "0" * 4300 + "1/", "http://host:" + "9" * 4301 + "/",
    "http://host: 1/", "http://[fe80::1%Zone]:1/", "http://[FE80::1%ZONE]/", "http://\u0130STANBUL.example/",
    "http://\u212akelvin.example/", "http://\u2100.example/", "http://a\u2100b@host/", "http://\uff0fslash.example/",
    "http://\u00e9.example/", "http://caf\u00e9.example:\u0663/", "http://x\u2474y/", "http://host/\u2100",
    "s3://bucket/key", "file:///tmp/x", "file:/tmp/x", "svn+ssh://h/p", "ws://h", "itms-services://h",
    "custom-scheme://h/p", "custom:path", "HTTP+X://h", "ht%74p://h", "\x00\x1f http://h/",
]

HOSTS = [
    "github.com", "GitHub.com", "api.github.com", "github.com:443", "user@github.com", "[::1]", "[::1", "::1]",
    "[v1.a]", "[v1.]", "[vz.a]", "[v1.a\n]", "[127.0.0.1]", "[fe80::1%eth0]", "[fe80::1%]", "[fe80::1%a%b]",
    "[::ffff:1.2.3.4]", "[::ffff:1.2.3.04]", "[1:2:3:4:5:6:7:8]", "[1:2:3:4:5:6:7:8:9]", "[1::2::3]", "[:1::2]",
    "[1::2:]", "[::]", "[12345::]", "[g::]", "a[::1]", "[::1]x", "[::1]:80", "github.com\u2100", "github.co\uff4d",
    "gith\u00fcb.com", "\u2474github.com", "github.com#", "", "[::1:2:3:4:5:6:7]", "[1:2:3:4:5:6:7::]",
    "[::1.2.3.4]", "[1.2.3.4::]", "[" + "1:" * 7 + "1.2.3.4]",
]

UNQUOTES = [
    "", "plain", "a%2Fb%zz%", "%ff", "%C3%A9\u00e9", "%c3%a9", "%e2%82", "%e2%82%ac", "%%", "%2", "%2g", "a%00b",
    "%F0%9F%8E%B2", "%ED%A0%80", "x\u00e9%ff", "%C3", "\u00e9%C3%A9\u00e9%41", "%41%42%43",
]


def updater_corpus() -> tuple[list[str], list[str]]:
    """Lane 5's updater locations and unquote texts, exactly as its update vectors build them,
    so every case the desktop updater was checked on stays in the shared corpus."""
    source = Path(__file__).resolve().parents[3] / "sylvia-owner/tests/vectors/generate_update_vectors.py"
    spec = importlib.util.spec_from_file_location("lane5_update_vectors", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    splits, unquotes, *_ = module.url_vectors()
    return [case["url"] for case in splits], [case["text"] for case in unquotes]


def split_case(url: str) -> dict:
    try:
        parts = urlsplit(url)
    except ValueError as error:
        return {"url": url, "error": str(error)}
    record = {"url": url, "parts": list(parts), "hostname": parts.hostname, "username": parts.username,
              "password": parts.password, "unsplit": urlunsplit(parts)}
    try:
        record["port"] = parts.port
    except ValueError as error:
        record["port_error"] = str(error)
    return record


def unquote_case(text: str) -> dict:
    try:
        return {"text": text, "result": unquote(text, errors="strict")}
    except ValueError as error:
        return {"text": text, "error": str(error)}


def main() -> int:
    base = "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/"
    urls = list(PROVIDER_URLS)
    urls += [f"https://{host}/pneuma-io/sylvia-gm/releases/download/v0.4.2/x-portable.zip" for host in HOSTS]
    urls += [base + tail for tail in ("a%2Fb-portable.zip", "a%ff-portable.zip", ".", "..", "x\u00e9-portable.zip", "")]
    rng = random.Random(20261121)
    pieces = ["https://", "HTTPS://", "http://", "https:", "//", "github.com", "api.github.com", "[", "]", "::1",
              "v1.x", "1.2.3.4", "@", ":", ":443", "%", "%2F", "%zz", "%C3%A9", "%ff", "/", "?", "#", ".", "..",
              "\\", " ", "\t", "", "\u00e9", "\u2100", "\uff0f", "pneuma-io/", "a", "fe80::1%25eth0", "user:pw@"]
    for _ in range(800):
        urls.append("".join(rng.choice(pieces) for _ in range(rng.randint(1, 9))))
    updater_urls, updater_unquotes = updater_corpus()
    urls += [url for url in updater_urls if url not in set(urls)]
    texts = UNQUOTES + [text for text in updater_unquotes if text not in UNQUOTES]
    document = {
        "python": sys.version.split()[0],
        "urlsplit": [split_case(url) for url in urls],
        "unquote": [unquote_case(text) for text in texts],
    }
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True, indent=0) + "\n").encode("ascii"))
    print(f"{len(document['urlsplit'])} URLs, {len(texts)} unquotes -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
