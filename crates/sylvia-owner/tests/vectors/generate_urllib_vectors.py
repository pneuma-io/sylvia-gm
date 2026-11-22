"""Generate reference vectors for sylvia_owner::urllib: Python's urllib on the wire.

Run with the snapshot's own Windows interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_urllib_vectors.py

Two kinds of vector:
- `bypass`, `fnmatch`, `parse_proxy`, `request_headers`: urllib's pure proxy and header steps
  (`proxy_bypass_environment`, `_proxy_bypass_winreg_override`, `fnmatch`, `_parse_proxy`, and
  the header lines `do_request_`, `do_open` and `putrequest` produce, captured from a socket).
- `scenarios`: the desktop updater's real `HttpsTransport` (through `metadata_bytes`) and
  dictation's real `prepare_model`, each against a scripted local CONNECT proxy and TLS server,
  with `https_proxy` pointing at the proxy and `SSL_CERT_FILE` at a test-only certificate
  authority. Each scenario scripts what every connection meets, and records the exact bytes the
  client sent (the CONNECT head, then the request head inside TLS) and the outcome.

The test certificates are made here from fixed, public, test-only Ed25519 seeds, in a
temporary folder, and never written to the repository; the Rust test makes its own the same
way. No real network is used. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import datetime
import fnmatch
import hashlib
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # noqa: E402

from sylvia_gm import local_dictation  # noqa: E402
from sylvia_gm.application.desktop_update import transport  # noqa: E402
from sylvia_gm.application.desktop_update.manifest import OFFICIAL_RELEASES, UpdateError  # noqa: E402

OUTPUT = Path(__file__).with_name("urllib_vectors.json")
HOSTS = ["api.github.com", "github.com", "release-assets.githubusercontent.com", "huggingface.co",
         "cdn-lfs.huggingface.co", "evil.example"]
ASSET = "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/manifest.json"
MODEL = "https://huggingface.co/Systran/faster-whisper-base/resolve/ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66/model.bin?download=true"


def test_key(label: str) -> Ed25519PrivateKey:
    """A TEST-ONLY key from a fixed, public seed. Never trust it anywhere."""
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(f"sylvia-test-only-{label}".encode()).digest())


def certificates(folder: Path) -> dict[str, Path]:
    start = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    end = datetime.datetime(2036, 1, 1, tzinfo=datetime.timezone.utc)
    ca_key = test_key("ca")
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Sylvia test-only CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(1).not_valid_before(start).not_valid_after(end)
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(ca_key, None))
    (folder / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths = {"ca": folder / "ca.pem"}
    for serial, host in enumerate(HOSTS, start=2):
        key = test_key(host)
        leaf = (x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])).issuer_name(ca_name)
                .public_key(key.public_key()).serial_number(serial).not_valid_before(start).not_valid_after(end)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .sign(ca_key, None))
        pem = leaf.public_bytes(serialization.Encoding.PEM) + key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        (folder / f"{host}.pem").write_bytes(pem)
        paths[host] = folder / f"{host}.pem"
    return paths


def head(stream) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = stream.recv(1)
        if not chunk:
            break
        data += chunk
    return data


class Server:
    """A CONNECT proxy whose tunnels end at a TLS server; each connection follows the next step."""

    def __init__(self, steps, paths, events):
        self.steps = list(steps)
        self.paths = paths
        self.events = events
        self.seen = []
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(16)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def context(self, host):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.paths[host])
        return context

    def run(self):
        while self.steps:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            step = self.steps.pop(0)
            first = head(connection).decode("latin-1")
            if not first.startswith("CONNECT "):
                # A plain-HTTP request through the proxy (`http_proxy`): the absolute-URI form.
                self.seen.append({"plain": first})
                for part in step["response"]:
                    connection.sendall(part.encode("latin-1"))
                connection.close()
                continue
            record = {"connect": first}
            self.seen.append(record)
            reply = step.get("connect", "HTTP/1.1 200 Connection established\r\n\r\n")
            connection.sendall(reply.encode("latin-1"))
            if not reply.startswith("HTTP/1.1 200"):
                connection.close()
                continue
            target = record["connect"].split(" ")[1].split(":")[0]
            try:
                tls = self.context(step.get("certificate", target)).wrap_socket(connection, server_side=True)
            except (ssl.SSLError, OSError) as error:
                record["tls"] = "failed"
                connection.close()
                continue
            record["request"] = head(tls).decode("latin-1")
            for part in step["response"]:
                if part == "cancel":
                    self.events["cancel"].set()
                    threading.Event().wait(0.3)
                else:
                    tls.sendall(part.encode("latin-1"))
            try:
                tls.close()
            except OSError:
                pass

    def stop(self):
        self.steps.clear()
        self.listener.close()
        self.thread.join(5)


def body(status: str, content: str, extra: str = "") -> str:
    return f"HTTP/1.1 {status}\r\nContent-Length: {len(content.encode('latin-1'))}\r\n{extra}\r\n{content}"


def redirect(status: str, location: str | None, extra: str = "") -> str:
    lines = f"Location: {location}\r\n" if location is not None else ""
    return f"HTTP/1.1 {status}\r\n{lines}Content-Length: 0\r\n{extra}\r\n"


UPDATE = [
    {"name": "release index", "url": OFFICIAL_RELEASES, "steps": [{"response": [body("200 OK", "[]")]}]},
    {"name": "asset via release-assets", "url": ASSET, "steps": [
        {"response": [redirect("302 Found", "https://release-assets.githubusercontent.com/a/b?sig=x%2Fy")]},
        {"response": [body("200 OK", '{"ok": true}')]}]},
    {"name": "relative redirect", "url": ASSET, "steps": [
        {"response": [redirect("301 Moved", "/pneuma-io/sylvia-gm/releases/download/v0.4.2/other.json")]},
        {"response": [body("200 OK", "x")]}]},
    {"name": "redirect to an untrusted host", "url": ASSET,
     "steps": [{"response": [redirect("302 Found", "https://evil.example/x")]}]},
    {"name": "redirect with a backslash", "url": ASSET,
     "steps": [{"response": [redirect("302 Found", "https://github.com\\@evil.example/x")]}]},
    {"name": "redirect with a space", "url": ASSET,
     "steps": [{"response": [redirect("302 Found", "https://release-assets.githubusercontent.com/a b")]},
               {"response": [body("200 OK", "y")]}]},
    {"name": "redirect with non-ASCII", "url": ASSET,
     "steps": [{"response": [redirect("307 Temporary", "https://release-assets.githubusercontent.com/café")]},
               {"response": [body("200 OK", "z")]}]},
    {"name": "redirect downgrade", "url": ASSET,
     "steps": [{"response": [redirect("302 Found", "http://release-assets.githubusercontent.com/x")]}]},
    {"name": "redirect to ftp", "url": ASSET,
     "steps": [{"response": [redirect("302 Found", "ftp://release-assets.githubusercontent.com/x")]}]},
    {"name": "redirect to javascript", "url": ASSET,
     "steps": [{"response": [redirect("302 Found", "javascript:alert(1)")]}]},
    {"name": "302 without Location", "url": ASSET, "steps": [{"response": [redirect("302 Found", None)]}]},
    {"name": "300 is not followed", "url": ASSET,
     "steps": [{"response": [redirect("300 Multiple", "https://release-assets.githubusercontent.com/x")]}]},
    {"name": "redirect repeated past max_repeats", "url": ASSET, "steps": [
        {"response": [redirect("302 Found", "https://release-assets.githubusercontent.com/loop")]}] + [
        {"response": [redirect("302 Found", "https://release-assets.githubusercontent.com/loop")]}] * 3},
    {"name": "redirect chain past max_redirections", "url": ASSET, "steps": [
        {"response": [redirect("302 Found", f"https://release-assets.githubusercontent.com/{n}")]} for n in range(7)]},
    {"name": "404", "url": ASSET, "steps": [{"response": [body("404 Not Found", "no")]}]},
    {"name": "429", "url": ASSET, "steps": [{"response": [body("429 Too Many Requests", "")]}]},
    {"name": "403 rate limited", "url": ASSET,
     "steps": [{"response": [body("403 Forbidden", "", "X-RateLimit-Remaining: 0\r\n")]}]},
    {"name": "403 other", "url": ASSET,
     "steps": [{"response": [body("403 Forbidden", "", "X-RateLimit-Remaining: 7\r\n")]}]},
    {"name": "500", "url": ASSET, "steps": [{"response": [body("500 Internal Server Error", "")]}]},
    {"name": "proxy refuses", "url": ASSET,
     "steps": [{"connect": "HTTP/1.1 407 Proxy Authentication Required\r\n\r\n", "response": []}]},
    {"name": "wrong certificate", "url": ASSET, "steps": [{"certificate": "evil.example", "response": []}]},
    {"name": "closed before a response", "url": ASSET, "steps": [{"response": []}]},
    {"name": "malformed status line", "url": ASSET, "steps": [{"response": ["NOT HTTP\r\n\r\n"]}]},
    {"name": "chunked body", "url": ASSET,
     "steps": [{"response": ["HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n2\r\nde\r\n0\r\n\r\n"]}]},
    {"name": "short body", "url": ASSET,
     "steps": [{"response": ["HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc"]}]},
    {"name": "encoded body", "url": ASSET,
     "steps": [{"response": [body("200 OK", "x", "Content-Encoding: gzip\r\n")]}]},
    {"name": "cancelled mid-body", "url": ASSET,
     "steps": [{"response": ["HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc", "cancel"]}]},
]

DOWNLOAD = [
    {"name": "model via the CDN", "steps": [
        {"response": [redirect("302 Found", "https://cdn-lfs.huggingface.co/repos/x/model.bin?Expires=1")]},
        {"response": [body("200 OK", "controlled model bytes")]}]},
    {"name": "model relative redirect", "steps": [
        {"response": [redirect("307 Temporary", "/api/resolve-cache/models/x/model.bin")]},
        {"response": [body("200 OK", "controlled model bytes")]}]},
    {"name": "model redirect loop", "steps": [
        {"response": [redirect("302 Found", "https://cdn-lfs.huggingface.co/loop")]}] * 6},
    {"name": "model 404", "steps": [{"response": [body("404 Not Found", "")]}]},
    {"name": "model wrong bytes", "steps": [{"response": [body("200 OK", "controlled model bytez")]}]},
    {"name": "model short body", "steps": [{"response": ["HTTP/1.1 200 OK\r\nContent-Length: 30\r\n\r\ncontrolled"]}]},
    {"name": "model proxy refuses", "steps": [{"connect": "HTTP/1.1 403 Forbidden\r\n\r\n", "response": []}]},
    {"name": "model malformed status", "steps": [{"response": ["HTTP/1.0\r\n\r\n"]}]},
    {"name": "model redirect to http", "steps": [
        {"response": [redirect("302 Found", "http://cdn-lfs.huggingface.co/x")]},
        {"response": [body("200 OK", "controlled model bytes")]}]},
]


def run_update(scenario, paths, credentials):
    events = {"cancel": threading.Event()}
    server = Server(scenario["steps"], paths, events)
    os.environ["https_proxy"] = os.environ["http_proxy"] = f"http://{credentials}127.0.0.1:{server.port}"
    try:
        result = {"ok": transport.metadata_bytes(transport.HttpsTransport(), scenario["url"], events["cancel"]).decode("latin-1")}
    except UpdateError as error:
        result = {"error": error.code}
    except Exception as error:  # escapes the transport; the controller reports update_failed
        result = {"escaped": type(error).__name__}
    server.stop()
    return {"seen": server.seen, **result}


def run_download(scenario, paths, folder):
    events = {"cancel": threading.Event()}
    server = Server(scenario["steps"], paths, events)
    os.environ["https_proxy"] = os.environ["http_proxy"] = f"http://127.0.0.1:{server.port}"
    urllib.request._opener = None  # urlopen builds its process opener again for this proxy
    target = folder / scenario["name"].replace(" ", "-")
    target.mkdir()
    payload = b"controlled model bytes"
    local_dictation.MODEL_FILES = {"model.bin": (len(payload), hashlib.sha256(payload).hexdigest())}
    try:
        local_dictation.prepare_model(target)
        result = {"ok": "ready"}
    except local_dictation.SpeechError as error:
        result = {"error": str(error)}
    except Exception as error:  # escapes prepare_model; the worker answers provider
        result = {"escaped": type(error).__name__}
    server.stop()
    return {"seen": server.seen, **result}


def capture_headers(headers):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    seen = []

    def serve():
        connection, _ = listener.accept()
        seen.append(head(connection).decode("latin-1"))
        connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        connection.close()

    thread = threading.Thread(target=serve)
    thread.start()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    host = f"127.0.0.1:{listener.getsockname()[1]}"
    with opener.open(urllib.request.Request(f"http://{host}/p?q", headers=headers), timeout=5) as response:
        response.read()
    thread.join()
    listener.close()
    lines = seen[0].split("\r\n")[1:-2]
    return [[name, value.replace(host, "<host>")] for name, value in (line.split(": ", 1) for line in lines)]


def main() -> None:
    hosts = ["github.com", "GitHub.com:443", "release-assets.githubusercontent.com", "api.github.com",
             "intranet", "intranet:8080", "10.1.2.3", "a.b.example", "example", "x.example.org", "[::1]:443"]
    no_proxies = ["", "*", "github.com", ".github.com", "githubusercontent.com", " github.com , example ",
                  "GITHUB.COM", "hub.com", "github.com:443", "*.github.com", "intranet,10.1.2.3"]
    overrides = ["<local>", "*.github.com;<local>", "github.com", "*.githubusercontent.com", "GITHUB.COM",
                 "10.*", "[abc]xample", "a?b.example", "*", "", " github.com ; api.github.com", "x[!a-c].example",
                 "*.example.org;*.example", "[", "intranet:8080"]
    from urllib.request import _parse_proxy, _proxy_bypass_winreg_override, proxy_bypass_environment
    proxies_forms = ["http://proxy:8080", "http://u:p@proxy:8080", "http://u%40x:p%3Ay@proxy:3128/", "proxy:8080",
                     "https://proxy", "http://u@proxy", "socks5://proxy:1080", "http:/proxy", "http://p:8080/path",
                     "http://a@b@proxy:1", "http://u:@proxy", "http://:p@proxy"]

    def parse(form):
        try:
            return {"ok": list(_parse_proxy(form))}
        except ValueError as error:
            return {"error": str(error)}

    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        paths = certificates(folder)
        for name in ("http_proxy", "HTTP_PROXY", "HTTPS_PROXY", "no_proxy", "NO_PROXY", "all_proxy", "ALL_PROXY"):
            os.environ.pop(name, None)
        os.environ["SSL_CERT_FILE"] = str(paths["ca"])
        scenarios = []
        for scenario in UPDATE:
            scenarios.append({"kind": "update", **scenario, "expect": run_update(scenario, paths, "")})
        scenarios.append({"kind": "update", "name": "proxy credentials", "url": OFFICIAL_RELEASES,
                          "credentials": "us%3Aer:p%40ss@", "steps": [{"response": [body("200 OK", "[]")]}],
                          "expect": run_update({"steps": [{"response": [body("200 OK", "[]")]}],
                                                "url": OFFICIAL_RELEASES}, paths, "us%3Aer:p%40ss@")})
        downloads = folder / "downloads"
        downloads.mkdir()
        for scenario in DOWNLOAD:
            scenarios.append({"kind": "download", "url": MODEL, **scenario,
                              "expect": run_download(scenario, paths, downloads)})
        os.environ.pop("https_proxy", None)
        os.environ.pop("http_proxy", None)
    vectors = {
        "python": sys.version.split()[0],
        "openssl": ssl.OPENSSL_VERSION,
        "bypass_environment": [{"host": h, "no_proxy": n,
                                "bypass": proxy_bypass_environment(h, {"no": n} if n else {})}
                               for h in hosts for n in no_proxies],
        "bypass_override": [{"host": h, "override": o, "bypass": _proxy_bypass_winreg_override(h, o)}
                            for h in hosts for o in overrides],
        "fnmatch": [{"name": n, "pattern": p, "match": fnmatch.fnmatch(n, p)}
                    for n in ["github.com", "API.GitHub.com", "a/b", "a\\b", "x[y", "abc", ""]
                    for p in ["*", "*.com", "github.???", "[ga]*", "[!g]*", "[a-c]bc", "a/b", "a\\b", "x[y", "x[[]y",
                              "[]]", "[!]]x", "*[", "", "?"]],
        "parse_proxy": [{"proxy": form, **parse(form)} for form in proxies_forms],
        "request_headers": [
            {"given": given, "sent": capture_headers(dict(given))}
            for given in ([["User-Agent", "Sylvia-GM-updater/1"], ["Accept-Encoding", "identity"],
                           ["Accept", "application/octet-stream"]],
                          [["User-Agent", "Sylvia-GM-local-dictation"]], [], [["connection", "keep-alive"]],
                          [["X-Custom", "1"], ["x-custom", "2"]], [["HOST", "other.example"]],
                          [["accept-encoding", "gzip"]])],
        "scenarios": scenarios,
    }
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
