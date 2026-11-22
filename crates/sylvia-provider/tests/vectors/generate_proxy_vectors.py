"""Generate reference vectors for sylvia-provider's proxy selection and header decoding.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-provider/tests/vectors/generate_proxy_vectors.py

For each scenario the environment (and, for the Windows cases, a stand-in `winreg` module with
the given Internet Settings values) is replaced, then the real `urllib.request.getproxies`,
`httpx._utils.get_environment_proxies` and an `httpx.AsyncClient(trust_env=True)` decide which
proxy each target URL uses. Nothing touches the real registry or the network. Also recorded:
how httpx decodes response header bytes. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
import os
import sys
import types
import urllib.request
from pathlib import Path

import httpx
import httpx._utils as httpx_utils

OUTPUT = Path(__file__).with_name("proxy_vectors.json")

TARGETS = [
    "http://127.0.0.1:1234/v1/chat/completions", "http://localhost:11434/v1/chat/completions",
    "https://openrouter.ai/api/v1/chat/completions", "https://api.openai.com/v1/responses",
    "http://192.168.1.20:8080/v1/chat/completions", "http://[::1]:1234/v1/chat/completions",
    "https://sub.example.com/v1/x", "https://example.com/v1/x", "https://notexample.com/v1/x",
    "http://example.com:8080/x", "https://LOCALHOST:443/x",
]

ENVIRONMENTS = [
    {},
    {"HTTP_PROXY": "proxy.local:3128"},
    {"HTTPS_PROXY": "http://secure.proxy:8443", "HTTP_PROXY": "http://plain.proxy:3128"},
    {"ALL_PROXY": "http://all.proxy:1080"},
    {"ALL_PROXY": "http://all.proxy:1080", "HTTPS_PROXY": "https://tls.proxy:443"},
    {"HTTP_PROXY": "http://p:1", "HTTPS_PROXY": "http://p:1", "NO_PROXY": "localhost,127.0.0.1"},
    {"HTTPS_PROXY": "http://p:1", "NO_PROXY": "example.com"},
    {"HTTPS_PROXY": "http://p:1", "NO_PROXY": ".example.com"},
    {"HTTPS_PROXY": "http://p:1", "NO_PROXY": "*"},
    {"HTTP_PROXY": "http://p:1", "NO_PROXY": " 192.168.0.0/16 , ::1 ,, http://example.com:8080"},
    {"HTTP_PROXY": "http://user:secret@p:1"},
    {"http_proxy": "http://lower:1", "HTTP_PROXY": "http://upper:1"},
    {"http_proxy": "", "HTTP_PROXY": "http://upper:1"},
    {"HTTP_PROXY": "http://p:1", "REQUEST_METHOD": "GET"},
    {"FTP_PROXY": "http://ftp.proxy:21", "no_proxy": "localhost"},
    {"HTTPS_PROXY": "socks5://s:1080"},
    {"HTTPS_PROXY": "ftp://weird:1"},
    {"HTTP_PROXY": "http://p:1", "NO_PROXY": "LOCALHOST"},
]

# (ProxyEnable, ProxyServer) pairs for the Windows registry path, used when the environment
# has no proxies at all.
REGISTRY = [
    None,
    (0, "corp.proxy:8080"),
    (1, "corp.proxy:8080"),
    (1, "http=web.proxy:80;https=tls.proxy:443"),
    (1, "https=https://tls.proxy:443;ftp=ftp.proxy:21"),
    (1, "socks=sock.proxy:1080"),
    (1, "http=web.proxy:80;socks=sock.proxy:1080"),
    (1, "broken"),
    (1, "http=a:1;bad"),
]


def fake_winreg(value):
    module = types.ModuleType("winreg")
    module.HKEY_CURRENT_USER = object()

    class Key:
        def Close(self):
            pass

    def open_key(root, path):
        if value is None:
            raise OSError("no key")
        return Key()

    def query(key, name):
        if name == "ProxyEnable":
            return (value[0], 4)
        if name == "ProxyServer":
            return (value[1], 1)
        raise OSError(name)

    module.OpenKey = open_key
    module.QueryValueEx = query
    return module


def decide(environment: dict, registry, *, windows: bool) -> dict:
    saved_environ = os.environ
    saved_winreg = sys.modules.get("winreg")
    # A plain dict keeps the case of the names, as a Linux environment does. On Windows Python
    # uppercases them, so the Windows scenarios pass uppercased names.
    os.environ = dict((k.upper(), v) for k, v in environment.items()) if windows else dict(environment)
    urllib.request.os.environ = os.environ
    httpx_utils.getproxies = urllib.request.getproxies
    try:
        if windows:
            sys.modules["winreg"] = fake_winreg(registry)
            proxies = urllib.request.getproxies_environment() or urllib.request.getproxies_registry()
        else:
            proxies = urllib.request.getproxies_environment()
        httpx_utils.getproxies = lambda: proxies
        try:
            mounts = httpx_utils.get_environment_proxies()
        except Exception as error:  # noqa: BLE001 - vectors record what Python raises
            return {"proxies": proxies, "error": f"{type(error).__name__}: {error}"}
        try:
            client = httpx.AsyncClient(trust_env=True)
        except Exception as error:  # noqa: BLE001
            return {"proxies": proxies, "mounts": mounts, "error": f"{type(error).__name__}: {error}"}
        chosen = {}
        for target in TARGETS:
            transport = client._transport_for_url(httpx.URL(target))
            pool = getattr(transport, "_pool", None)
            proxy = getattr(pool, "_proxy_url", None)
            if proxy is not None:
                proxy = str(httpx.URL(scheme=proxy.scheme.decode(), host=proxy.host.decode(),
                                      port=proxy.port, path=proxy.target.decode()))
            chosen[target] = proxy
        return {"proxies": proxies, "mounts": mounts, "chosen": chosen}
    finally:
        os.environ = saved_environ
        urllib.request.os.environ = saved_environ
        httpx_utils.getproxies = urllib.request.getproxies
        if saved_winreg is not None:
            sys.modules["winreg"] = saved_winreg


def header_cases() -> list[dict]:
    raws = [
        [(b"retry-after", b"5")],
        [(b"retry-after", "٥".encode()), (b"x", b"plain")],
        [(b"x", b"\xa0latin"), (b"retry-after", b"7")],
        [(b"x", "café".encode()), (b"y", b"\xff")],
    ]
    out = []
    for raw in raws:
        headers = httpx.Headers(raw)
        out.append({"raw": [[k.hex(), v.hex()] for k, v in raw], "encoding": headers.encoding,
                    "decoded": [[k, v] for k, v in headers.multi_items()]})
    return out


def main() -> int:
    scenarios = []
    for environment in ENVIRONMENTS:
        scenarios.append({"platform": "linux", "environment": environment, "registry": None,
                          **decide(environment, None, windows=False)})
    for environment in ENVIRONMENTS[:3] + [{}]:
        for registry in REGISTRY:
            scenarios.append({"platform": "windows", "environment": environment, "registry": registry,
                              **decide(environment, registry, windows=True)})
    document = {"python": sys.version.split()[0], "httpx": httpx.__version__, "targets": TARGETS,
                "scenarios": scenarios, "headers": header_cases()}
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True, indent=1) + "\n").encode("ascii"))
    print(f"{len(scenarios)} scenarios -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
