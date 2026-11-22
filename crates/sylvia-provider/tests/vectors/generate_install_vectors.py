"""Generate reference vectors for sylvia-provider's Claude Code installer (lane 5).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_install_vectors.py

The frozen `provider/_claude_install.py` runs unchanged. What depends on the machine or the
network is supplied: `httpx.Client` sends through a mock transport answering each URL from the
case, and `release_platform`, `executable_name`, `verify_manifest`, `verify_publisher`,
`runtime_version` and `shutil.disk_usage` are stand-ins. The stand-in `runtime_version` reads
`claude-version:<version>` from the file it is given and calls anything else damaged. Recorded
for each case: the URLs requested, which checks ran, `progress()` after `run()` (or the
exception `run()` let escape), and the install folder afterwards.

Also recorded: `_version`, `release_platform`'s choices, `_dearmor`, and the verdicts
`verify_manifest` and `verify_publisher` reach on given `gpgv` and PowerShell runs.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import base64
import collections
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from sylvia_gm.provider import _claude_install
from sylvia_gm.provider._claude_install import ClaudeInstaller
from sylvia_gm.provider._exceptions import ProviderConnectionError

HERE = Path(__file__).parent
OUTPUT = HERE / "install_vectors.json"
R = _claude_install.RELEASES
DAMAGED = "Sylvia's copy of Claude Code is damaged. Install it again in Settings > Accounts."


def binary(version: str) -> bytes:
    return f"claude-version:{version}".encode()


def manifest(build: str, executable: str, body: bytes, **override) -> bytes:
    entry = {"binary": executable, "checksum": hashlib.sha256(body).hexdigest(), "size": len(body), **override}
    return json.dumps({"version": "2.1.400", "platforms": {build: entry, "other-platform": {"size": 1}}}).encode()


def routes(build: str, executable: str, version: str = "2.1.400", body: bytes | None = None, **override):
    body = binary(version) if body is None else body
    table = {
        f"{R}/stable": [200, f"{version}\n".encode()],
        f"{R}/{version}/manifest.json": [200, manifest(build, executable, body, **override)],
        f"{R}/{version}/{build}/{executable}": [200, body],
    }
    if build.startswith("linux-"):
        table[f"{R}/{version}/manifest.json.sig"] = [200, b"signature-bytes"]
        table[_claude_install.SIGNING_KEY] = [200, b"key-bytes"]
    return table


def cases() -> list[dict]:
    out = []
    for build, executable in (("win32-x64", "claude.exe"), ("linux-x64", "claude")):
        def case(name, table=None, **extra):
            out.append({"name": f"{build}/{name}", "build": build, "executable": executable,
                        "routes": table if table is not None else routes(build, executable), **extra})

        body = binary("2.1.400")
        case("fresh")
        case("older-than-minimum", routes(build, executable, version="2.1.322") | {f"{R}/stable": [200, b"2.1.100"]})
        case("stable-not-a-version", routes(build, executable) | {f"{R}/stable": [200, b"<html>maintenance</html>"]})
        case("stable-unicode-digits", routes(build, executable) | {f"{R}/stable": [200, " ٢.١.٤٠٠ \n".encode()]})
        case("up-to-date", existing=[[f"2.1.400/{executable}", "claude-version:2.1.400"]])
        case("existing-other-version", existing=[[f"2.1.400/{executable}", "claude-version:2.1.300"]])
        case("existing-damaged", existing=[[f"2.1.400/{executable}", "garbage"]])
        case("leftovers", existing=[[f"2.1.100/{executable}", "claude-version:2.1.100"], ["notes.txt", "keep"],
                                    ["old.partial/x", "x"], [".hidden.partial/x", "x"]])
        case("failure-keeps-hidden-partial", routes(build, executable) | {f"{R}/stable": [404, b""]},
             existing=[[".hidden.partial/x", "x"], ["seen.partial/x", "x"]])
        case("checksum-mismatch", routes(build, executable, checksum="0" * 64))
        case("longer-than-size", routes(build, executable, size=len(body) - 3))
        case("shorter-than-size", routes(build, executable, size=len(body) + 3))
        case("no-platform-entry", routes(build, executable) | {
            f"{R}/2.1.400/manifest.json": [200, json.dumps({"platforms": {}}).encode()]})
        case("platforms-not-a-dict", routes(build, executable) | {
            f"{R}/2.1.400/manifest.json": [200, json.dumps({"platforms": [1]}).encode()]})
        case("entry-not-a-dict", routes(build, executable) | {
            f"{R}/2.1.400/manifest.json": [200, json.dumps({"platforms": {build: "x"}}).encode()]})
        case("missing-size", routes(build, executable) | {
            f"{R}/2.1.400/manifest.json": [200, json.dumps({"platforms": {build: {"checksum": "a" * 64}}}).encode()]})
        case("wrong-binary-name", routes(build, executable, binary="other"))
        case("uppercase-checksum", routes(build, executable, checksum=hashlib.sha256(body).hexdigest().upper()))
        case("size-bool", routes(build, executable, size=True))
        case("size-zero", routes(build, executable, size=0))
        case("size-too-big", routes(build, executable, size=2 * 1024**3 + 1))
        case("size-float", routes(build, executable, size=22.0))
        case("invalid-manifest-json", routes(build, executable) | {f"{R}/2.1.400/manifest.json": [200, b"{oops"]})
        case("little-disk", free=len(body) + 64 * 1024**2 - 1)
        case("just-enough-disk", free=len(body) + 64 * 1024**2)
        case("reports-other-version", routes(build, executable, body=binary("2.1.399")))
        case("reports-damaged", routes(build, executable, body=b"no version here"))
        case("stable-404", routes(build, executable) | {f"{R}/stable": [404, b"missing"]})
        case("manifest-unreachable", routes(build, executable) | {f"{R}/2.1.400/manifest.json": "connect-error"})
        case("binary-500", routes(build, executable) | {f"{R}/2.1.400/{build}/{executable}": [500, b""]})
        case("binary-redirect", routes(build, executable) | {f"{R}/2.1.400/{build}/{executable}": [302, b""]})
        case("cancelled", cancel_on_binary=True)
        if build.startswith("win32-"):
            case("publisher-refused", publisher="The Claude Code download is not signed by Anthropic, so Sylvia discarded it.")
        else:
            case("manifest-signature-refused", manifest_check=_claude_install._UNSIGNED_MANIFEST)
            case("signature-404", routes(build, executable) | {f"{R}/2.1.400/manifest.json.sig": [404, b""]})
    out.append({"name": "unsupported-platform", "build": None, "executable": "claude", "routes": {},
                "platform_error": "Sylvia can set up Claude Code on Windows and Linux only in this version."})
    return out


Usage = collections.namedtuple("Usage", "total used free")


def run_case(case: dict) -> dict:
    requested, checks = [], []
    installer_box = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if case.get("cancel_on_binary") and url.endswith("/" + case["executable"]):
            installer_box[0].cancel()
        reply = case["routes"].get(url, [404, b""])
        if reply == "connect-error":
            raise httpx.ConnectError("refused", request=request)
        status, body = reply
        return httpx.Response(status, content=body)

    original_client = httpx.Client
    patched = {
        "release_platform": _claude_install.release_platform,
        "executable_name": _claude_install.executable_name,
        "verify_manifest": _claude_install.verify_manifest,
        "verify_publisher": _claude_install.verify_publisher,
        "runtime_version": _claude_install.runtime_version,
    }
    original_usage = shutil.disk_usage

    def release_platform():
        if case.get("platform_error"):
            raise ProviderConnectionError(case["platform_error"])
        return case["build"]

    def verify_manifest(listing, signature, key):
        checks.append(["manifest", len(listing), signature.decode(), key.decode()])
        if case.get("manifest_check"):
            raise ProviderConnectionError(case["manifest_check"])

    def verify_publisher(path):
        checks.append(["publisher", path.name])
        if case.get("publisher"):
            raise ProviderConnectionError(case["publisher"])

    def runtime_version(executable, home):
        content = executable.read_bytes()
        checks.append(["version", executable.parent.name])
        if content.startswith(b"claude-version:"):
            return content.removeprefix(b"claude-version:").decode()
        raise ProviderConnectionError(DAMAGED)

    httpx.Client = lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs)
    _claude_install.release_platform = release_platform
    _claude_install.executable_name = lambda: case["executable"]
    _claude_install.verify_manifest = verify_manifest
    _claude_install.verify_publisher = verify_publisher
    _claude_install.runtime_version = runtime_version
    shutil.disk_usage = lambda path: Usage(0, 0, case.get("free", 10 * 1024**3))
    try:
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            for relative, content in case.get("existing", []):
                target = home / "install" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            installer = ClaudeInstaller(home)
            installer_box.append(installer)
            escaped = None
            try:
                installer.run()
            except Exception as exc:  # noqa: BLE001
                escaped = [type(exc).__name__, str(exc)]
            files = []
            install = home / "install"
            for path in sorted(install.rglob("*")) if install.exists() else []:
                relative = path.relative_to(install).as_posix()
                files.append([relative, None if path.is_dir() else path.read_bytes().decode("latin-1")])
            progress = installer.progress()
    finally:
        httpx.Client = original_client
        for name, value in patched.items():
            setattr(_claude_install, name, value)
        shutil.disk_usage = original_usage
    encoded = {url: reply if reply == "connect-error" else [reply[0], base64.b64encode(reply[1]).decode()]
               for url, reply in case["routes"].items()}
    return {**case, "routes": encoded, "requested": requested, "checks": checks, "progress": progress,
            "escaped": escaped, "files": files}


class Completed:
    def __init__(self, stdout, returncode):
        self.stdout, self.returncode = stdout, returncode


def verdicts(function, outcomes, *args) -> list[dict]:
    out = []
    original_run, original_which = subprocess.run, shutil.which
    for outcome in outcomes:
        def run(*_args, **_kwargs):
            if outcome == "oserror":
                raise OSError("no")
            if outcome == "timeout":
                raise subprocess.TimeoutExpired("x", 1)
            return Completed(outcome[0], outcome[1])

        subprocess.run = run
        shutil.which = lambda name: "/usr/bin/gpgv"
        try:
            function(*args)
            out.append({"outcome": outcome, "ok": True})
        except ProviderConnectionError as exc:
            out.append({"outcome": outcome, "error": str(exc)})
        finally:
            subprocess.run, shutil.which = original_run, original_which
    return out


KEY = (
    "-----BEGIN PGP PUBLIC KEY BLOCK-----\nComment: Anthropic\n\n"
    + base64.b64encode(b"key packets here").decode() + "\n=abcd\n-----END PGP PUBLIC KEY BLOCK-----\n"
)


def main() -> int:
    fingerprint = _claude_install.SIGNING_FINGERPRINT
    good = f"[GNUPG:] NEWSIG\n[GNUPG:] GOODSIG ABC Anthropic\n[GNUPG:] VALIDSIG SUBKEY 2026-01-01 1 0 4 0 1 8 00 {fingerprint}\n"
    gpgv = [
        (good, 0), (good, 1), (good.replace(fingerprint, fingerprint.lower()), 0),
        (good.replace(fingerprint, "0" * 40), 0), (good + "[GNUPG:] EXPKEYSIG ABC\n", 0),
        ("[GNUPG:] GOODSIG ABC\n[GNUPG:] VALIDSIG " + fingerprint + "\n", 0),
        ("[GNUPG:] GOODSIG ABC\n", 0), ("", 0), ("gpgv: Good signature\n", 0),
        (good + "[GNUPG:] VALIDSIG X 2026 1 0 4 0 1 8 00 " + "1" * 40 + "\n", 0),
        (good.replace("\n", "\r\n"), 0), (good.replace(" ", "\x1c"), 0), "oserror", "timeout",
    ]
    publisher = [("Valid\nAnthropic, PBC", 0), ("Valid\nAnthropic, PBC\n", 0), ("NotSigned\n", 0),
                 ("Valid\nSomeone Else", 0), ("Valid\nAnthropic, PBC", 1), ("", 0), "oserror", "timeout"]
    armors = {
        "plain": KEY, "crlf": KEY.replace("\n", "\r\n"),
        "no-headers": KEY.replace("Comment: Anthropic\n\n", ""),
        "missing-end": KEY.replace("-----END PGP PUBLIC KEY BLOCK-----", ""),
        "bad-base64": KEY.replace(base64.b64encode(b"key packets here").decode(), "not*base64"),
        "non-ascii": KEY.replace("Anthropic", "Anthröpic"),
        "spaced": KEY.replace(base64.b64encode(b"key packets here").decode(), "  " + base64.b64encode(b"key packets here").decode() + "  "),
    }
    dearmored = {}
    for name, text in armors.items():
        try:
            dearmored[name] = {"text": text, "value": base64.b64encode(_claude_install._dearmor(text.encode())).decode()}
        except (UnicodeDecodeError, ValueError) as exc:
            dearmored[name] = {"text": text, "error": type(exc).__name__}
    versions = {}
    for text in ["2.1.400", " 2.1.400\n", "02.010.0400", "2.1", "2.1.4.5", "v2.1.4", "٢.١.٤", "", "2..1"]:
        try:
            versions[text] = list(_claude_install._version(text))
        except ProviderConnectionError as exc:
            versions[text] = str(exc)
    vectors = {
        "python": sys.version.split()[0],
        "versions": versions,
        "dearmor": dearmored,
        "gpgv": verdicts(lambda: _claude_install.verify_manifest(b"m", b"s", KEY.encode()), gpgv),
        "publisher": verdicts(lambda: _claude_install.verify_publisher(Path("claude.exe")), publisher),
        "installs": [run_case(case) for case in cases()],
    }
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
