"""Generate reference vectors for sylvia_base::pyurl's urlparse, urlunparse, urljoin and quote.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-base/tests/vectors/generate_pyurl_join_vectors.py

These are the steps urllib's `HTTPRedirectHandler.http_error_302` applies to a `Location` header
before any redirect check sees it (`urlparse`, an empty path made `/`, `urlunparse`,
`quote(..., encoding="iso-8859-1", safe=string.punctuation)`, `urljoin`), so the corpus is
hostile on purpose: backslashes, whitespace and control characters, `@` and credentials,
percent-encodings, IDNA and non-ASCII hosts, dot segments, parameters and fragments. Each case
records the result or the exception's class and message. Reference tooling only; retired with
the Python source.
"""

from __future__ import annotations

import json
import random
import string
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse, urlunparse

OUTPUT = Path(__file__).parent / "pyurl_join_vectors.json"

BASES = [
    "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.1/manifest.json",
    "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.1/",
    "https://huggingface.co/Systran/faster-whisper-base/resolve/ebe41f70/model.bin?download=true",
    "https://h/a/b/c/d;p?q#f", "https://h", "https://h/", "http://h/a", "ftp://h/x/y", "https://user:pw@h:8443/p",
    "file:///tmp/x", "mailto:a@b", "", "https://h/a/./b/../c",
]

LOCATIONS = [
    "https://release-assets.githubusercontent.com/github-production-release-asset/1/2?sp=r&sig=abc%2Fdef",
    "https://objects.githubusercontent.com/x", "/pneuma-io/sylvia-gm/releases/download/v0.4.2/x.zip",
    "relative/file.bin", "../up", "../../../../../../top", "./same", ".", "..", "", "?q=1", "#frag", ";params",
    "//other.host/path", "//github.com", "https://github.com", "https:other", "http://downgrade.example/",
    "ftp://ftp.example/file", "javascript:alert(1)", "data:text/plain,x", "file:///etc/passwd",
    "https://github.com\\@evil.example/", "https:\\\\evil.example\\x", "\\\\evil.example\\share",
    "https://evil.example\\.github.com/", "https://github.com/a\\b", "https://github.com/a b",
    " https://github.com/x", "https://github.com/x ", "\thttps://github.com/x", "https://github.com/x\r\n",
    "https://git\nhub.com/x", "https://github.com/\x00x", "https://github.com/\x7fx", "https://github.com/\x1fx",
    "https://user:pass@github.com/x", "https://github.com@evil.example/x", "https://evil.example#@github.com/",
    "https://github.com:443/x", "https://github.com:8443/x", "https://github.com:/x", "https://github.com:abc/x",
    "https://GITHUB.COM/X", "HTTPS://github.com/x", "https://github.com./x", "https://xn--github-n3a.com/x",
    "https://githüb.com/x", "https://ɡithub.com/x", "https://github.com/é", "https://github.com/€",
    "https://github.com/%2e%2e/x", "https://github.com/%zz", "https://github.com/%", "https://github.com/%2F%5C",
    "https://github.com/a;b/c;d?e#f", "https://[::1]/x", "https://[::1/x", "https://[v1.x]/", "https://h]/",
    "https://release-assets.githubusercontent.com", "https://release-assets.githubusercontent.com?x",
    "/a/b/../../c/./d/", "a/../../b", "/..", "/.", "//", "///x", "https:///x", "https://", "https:",
]

QUOTE_TEXTS = [
    "", "plain", "a b", "tab\there", "line\nbreak", "\x00\x01\x1f\x7f", "\x80\xff", "café", "€",
    "https://github.com/a b?c=d e#f", string.punctuation, "%41%zz%", "\\back\\slash", "Ā", "x퟿y",
]


def outcome(call):
    try:
        return {"ok": call()}
    except Exception as error:  # the class and message are the behaviour
        return {"error": [type(error).__name__, str(error)]}


def parsed(url, scheme=""):
    return list(urlparse(url, scheme))


def corpus():
    rng = random.Random(20261122)
    pieces = ["https://", "//", "/", "\\", "@", ":", "github.com", "evil.example", "..", ".", "%2e", "%5c", "?", "#",
              ";", " ", "\t", "é", "x", "443"]
    extra = ["".join(rng.choice(pieces) for _ in range(rng.randint(1, 7))) for _ in range(120)]
    return LOCATIONS + extra


def main() -> None:
    locations = corpus()
    vectors = {
        "punctuation": string.punctuation,
        "urlparse": [{"url": url, "scheme": scheme, **outcome(lambda: parsed(url, scheme))}
                     for url in locations + BASES for scheme in ("", "https")],
        "urlunparse": [{"parts": parts, "url": urlunparse(parts)}
                       for parts in (parsed(url) for url in locations + BASES if outcome(lambda: parsed(url)).get("ok"))],
        "urljoin": [{"base": base, "url": url, **outcome(lambda: urljoin(base, url))}
                    for base in BASES for url in locations],
        "quote_latin1": [{"text": text, **outcome(lambda: quote(text, encoding="iso-8859-1", safe=string.punctuation))}
                         for text in QUOTE_TEXTS + locations],
    }
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
