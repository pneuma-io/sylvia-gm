"""Generate reference vectors for sylvia_owner::desktop_update from the frozen Python snapshot.

Run with the snapshot's own Windows interpreter (the only platform the updater supports),
from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_update_vectors.py

The two signing keys are derived from fixed, test-only seeds (`test_key` below). Ed25519
signing is deterministic, so regenerating reproduces the file byte for byte. Only their public
keys and the envelopes they signed are written. The Rust tests that need to sign make their
own keys.

Covered: SemVer parsing and order, strict base64, URL splitting and unquoting, release and
payload paths, the official request and redirect rules, Ed25519 verification edge cases,
every manifest refusal, discovery and metadata reads over a scripted transport, and artifact
downloads into a temporary folder. This script is reference tooling for the port and is
retired with the Python source.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import json
import random
import shutil
import sys
import tempfile
import threading
from email.message import Message
from urllib.request import Request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

from sylvia_gm import __release_channel__, __version__  # noqa: E402
from sylvia_gm.application.desktop_update import discovery, manifest, transport  # noqa: E402
from sylvia_gm.application.desktop_update.manifest import (  # noqa: E402
    KeyRing,
    UpdateError,
    Version,
    canonical_bytes,
)
from sylvia_gm.portable_artifact import LEGAL_FILES, REQUIRED_PAYLOADS  # noqa: E402

OUTPUT = Path(__file__).with_name("update_vectors.json")
KEY_ID = "test-key"
ED25519_L = 2**252 + 27742317777372353535851937790883648493


def test_key(name: str) -> Ed25519PrivateKey:
    """A TEST-ONLY signing key from a fixed, public seed, so the vectors reproduce exactly.

    Anyone can derive it from this file. Its public key must never be added to any production
    KeyRing or to `PUBLIC_KEYS`; a release signed with it must never be trusted.
    """
    seed = hashlib.sha256(f"sylvia-update-vectors test key {name}".encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def outcome(call):
    try:
        return {"ok": call()}
    except UpdateError as error:
        return {"error": [error.code, str(error)]}
    except ValueError as error:  # urlsplit and unquote raise plain ValueError
        return {"value_error": type(error).__name__}


def encode_raw(raw: bytes) -> dict[str, str]:
    try:
        return {"text": raw.decode("utf-8")}
    except UnicodeDecodeError:
        return {"b64": base64.b64encode(raw).decode()}


# ---------------------------------------------------------------- small functions


def version_vectors():
    texts = [
        "0.4.1", "0.4.2", "0.4.1-alpha", "0.4.1-alpha.1", "0.4.1-alpha.10", "0.4.1-alpha.2",
        "0.4.1-beta", "0.4.1-alpha.beta", "0.4.1-1", "0.4.1-01", "0.4.1-alpha.01", "0.4.1+build",
        "0.4.1+build.7", "0.4.1-alpha+x", "01.4.1", "0.04.1", "1.0", "1.0.0.0", "", "v1.0.0",
        "1.0.0-", "1.0.0-a..b", "1.0.0+", "1.0.0-α", "1.0.0 ", " 1.0.0", "1.0.0\n",
        "999999999.0.0", "1000000000.0.0", "0.0.0", "1.0.0-0", "1.0.0-0a", "1.0.0-a-b",
        "1.0.0-99999999999999999999", "1.0.0-100000000000000000000", "1.0.0-x." + "y" * 120,
        "1.0.0-" + "1" * 122, "1.0.0-" + "1" * 123, "1.2.3-rc.1.2", "1.2.3-rc.1", "1.2.3-rc",
        "1.2.3-RC", "1.2.3-rc-1", "１.0.0",
    ]
    parsed = []
    for text in texts:
        parsed.append({"text": text, "result": outcome(lambda text=text: (
            lambda v: {"text": v.text, "core": list(v.core), "prerelease": list(v.prerelease),
                       "channel": v.channel})(Version.parse(text)))})
    parsed.append({"value": 1, "result": outcome(lambda: Version.parse(1).text)})
    valid = [Version.parse(text) for text in texts if "error" not in outcome(lambda t=text: Version.parse(t).text)]
    order = []
    for left in valid:
        for right in valid:
            order.append([left.text, right.text, left < right, left == right, left <= right, left > right])
    return parsed, order


def b64_vectors():
    good = base64.b64encode(bytes(range(64))).decode()
    cases = ["", "QQ==", "QR==", "QQ=", "QQ", "Q===", "=QQ=", "QQ==QQ==", "QUFB", "QU FB", "QUFB=",
             "QUF=", "QQ=A", "QUF", "Q", "QUFBQQ", "QUFB\n", "QUFBé", "-_-_", "QUFB====", "QQ===",
             good, good[:-2] + "==", good[:-3] + "B==", good[:-1], good + "A", good.replace("A", "*", 1)]
    result = []
    for text in cases:
        try:
            result.append({"text": text, "bytes": base64.b64decode(text, validate=True).hex()})
        except (ValueError, TypeError):
            result.append({"text": text, "bytes": None})
    return result


def url_strings():
    base = "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/"
    hosts = ["github.com", "GitHub.com", "api.github.com", "release-assets.githubusercontent.com",
             "github.com:443", "user@github.com", "[::1]", "[::1", "::1]", "[v1.a]", "[v1.]", "[vz.a]",
             "[127.0.0.1]", "[fe80::1%eth0]", "[fe80::1%]", "[::ffff:1.2.3.4]", "[::ffff:1.2.3.04]",
             "[1:2:3:4:5:6:7:8]", "[1:2:3:4:5:6:7:8:9]", "[1::2::3]", "[:1::2]", "[1::2:]", "[::]",
             "[12345::]", "[g::]", "a[::1]", "[::1]x", "[::1]:80", "github.com\u2100", "github.co\uff4d",
             "gith\u00fcb.com", "\u2474github.com", "github.com#", ""]
    urls = [base + "sylvia-gm-0.4.2-windows-x86_64-portable.zip",
            base + "sylvia-gm-0.4.2-windows-x86_64-setup.exe",
            base + "sylvia-gm-0.4.2-windows-x86_64-SETUP.EXE",
            base + "sylvia-gm-0.4.2-windows-x86_64-portable.zip?x=1",
            base + "sylvia-gm-0.4.2-windows-x86_64-portable.zip#frag",
            base + "a%2Fb-portable.zip", base + "a%zz-portable.zip", base + "a%2-portable.zip",
            base + "a%ff-portable.zip", base + "a%C3%A9-portable.zip", base + "a%00-portable.zip",
            base + "a%5Cb-portable.zip", base + ".", base + "..", base + "%2E%2E", base,
            base + "a/b-portable.zip", base + "x\u00e9-portable.zip", base + "x\u00e9%ff-portable.zip",
            base + "x-PORTABLE.zip", base + "x-portable.ZIP", base + "x-Portable.Zip",
            "HTTPS://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/x-portable.zip",
            "http://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/x-portable.zip",
            "https:github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/x-portable.zip",
            "https:///pneuma-io/sylvia-gm/releases/download/v0.4.2/x-portable.zip",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.3/x-portable.zip",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2x/y-portable.zip",
            "https://github.com/PNEUMA-io/sylvia-gm/releases/download/v0.4.2/x-portable.zip",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2//x-portable.zip",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/x-portable.zip ",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/x\\-portable.zip",
            " https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/x-portable.zip",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/x\t-portable.zip",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/x\x7f-portable.zip",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/" + "a" * 2000 + "-portable.zip",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/" + "a" * 1900 + "-portable.zip",
            "1https://github.com/x", "h+t.p-s://github.com/x", "https", "", "//github.com/x",
            "https://github.com", "https://github.com?", "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/a/b",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/v0.4.2/a",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/x",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/x/",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/x/.",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/x/%2e%2e",
            "https://github.com/pneuma-io/sylvia-gm/releases/download/x/y%0a",
            "https://release-assets.githubusercontent.com/github-production/abc?sig=1",
            "https://release-assets.githubusercontent.com/x#f", "https://evil.com/x",
            transport.OFFICIAL_RELEASES, transport.OFFICIAL_RELEASES + "&x",
            "https://api.github.com/repos/pneuma-io/sylvia-gm/releases?per_page=30"]
    for host in hosts:
        urls.append(f"https://{host}/pneuma-io/sylvia-gm/releases/download/v0.4.2/x-portable.zip")
    # Seeded random locations built from the pieces the checks look at.
    rng = random.Random(20261121)
    pieces = ["https://", "HTTPS://", "http://", "https:", "//", "github.com", "api.github.com", "[", "]", "::1",
              "v1.x", "1.2.3.4", "@", ":", ":443", "%", "%2F", "%2f", "%zz", "%C3%A9", "%ff", "/", "?", "#", ".",
              "..", "\\", " ", "	", "", "é", "℀", "／", "pneuma-io/sylvia-gm/releases/download/",
              "v0.4.2/", "x-portable.zip", "-setup.exe", "a", "fe80::1%25eth0"]
    for _ in range(600):
        urls.append("".join(rng.choice(pieces) for _ in range(rng.randint(1, 9))))
    return urls


def url_vectors():
    from urllib.parse import unquote, urlsplit

    splits, unquotes, releases, officials, redirects, finals = [], [], [], [], [], []
    for url in url_strings():
        try:
            parts = urlsplit(url)
            splits.append({"url": url, "split": [parts.scheme, parts.netloc, parts.path, parts.query,
                                                  parts.fragment]})
        except ValueError:
            splits.append({"url": url, "split": None})
        for distribution in (None, "installed", "portable"):
            releases.append({"url": url, "distribution": distribution,
                             "result": outcome(lambda u=url, d=distribution: manifest.release_url(u, "0.4.2", d))})
        officials.append({"url": url, "result": outcome(lambda u=url: transport.official_request(u))})
        redirects.append({"url": url, "result": outcome(
            lambda u=url: transport.OfficialRedirects().redirect_request(
                Request(transport.OFFICIAL_RELEASES), None, 302, "Found", Message(), u) is None)})
    for text in ["", "abc", "a%2Fb", "%", "%%", "%2", "%2g", "%zz%41", "%ff", "%C3%A9", "%C3",
                 "\u00e9%C3%A9", "%C3\u00e9%A9", "%e2%82%ac", "%E2%82", "%41%42%43", "a%", "%00", "%f0%9f%98%80",
                 "%ED%A0%80", "x%C0%AF"]:
        try:
            unquotes.append({"text": text, "result": unquote(text, errors="strict")})
        except ValueError:
            unquotes.append({"text": text, "result": None})
    # The final-location rule, as HttpsTransport.open applies it to response.geturl().
    for url in url_strings():
        try:
            final = urlsplit(url)
        except ValueError:
            finals.append({"url": url, "trusted": None})
            continue
        finals.append({"url": url, "trusted": final.scheme == "https" and final.netloc in (
            transport._REDIRECT_HOSTS | {"api.github.com"})})
    return splits, unquotes, releases, officials, redirects, finals


def payload_path_vectors():
    cases = ["sylvia-gm.exe", "licenses/LICENSE", "a/b/c.txt", "", "a" * 240, "a" * 241, "/abs", "a//b",
             "a/./b", "a/../b", "./a", "a/", "a\\b", "a:b", "a b", "a.", "a ", " a", "a.b.", "CON",
             "con.txt", "Con.Txt", "nul", "COM1", "COM0", "COM10", "LPT9", "LPT1.x", "CON .txt", "CON.",
             "x/AUX/y", "aux.", "auxx", "conx.txt", "isolated-data/x", "Campaigns/x", "temp", "TEMP/x",
             ".application-updates/x", "authority.sqlite3", "AUTHORITY.SQLITE3", "config.yaml",
             "x/config.yaml", "é", "a\x00b", "a+b", "a~b", "-", "_", "...", "a..b", "diagnostics2/x",
             "dictation-models/m", "webview/x", "x/isolated-data", "COM¹"]
    rng = random.Random(20261122)
    parts = ["a", "B", "0", "_", "-", ".", " ", "/", "CON", "lpt3", "nul.txt", "temp", "campaigns", "config.yaml",
             "authority.sqlite3", "..", "é", ":", "+"]
    for _ in range(500):
        cases.append("".join(rng.choice(parts) for _ in range(rng.randint(1, 6))))
    result = [{"path": case, "result": outcome(lambda c=case: manifest.payload_path(c))} for case in cases]
    result.append({"path": 7, "result": outcome(lambda: manifest.payload_path(7))})
    return result


# ---------------------------------------------------------------- signatures


def public_bytes(key) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def signature_vectors(key):
    message = b"sylvia update vector"
    valid = key.sign(message)
    s = int.from_bytes(valid[32:], "little")
    identity = bytes([1]) + bytes(31)
    p = 2**255 - 19
    non_canonical_identity = (p + 1).to_bytes(32, "little")
    cases = [
        ("valid", public_bytes(key), message, valid),
        ("wrong message", public_bytes(key), message + b"!", valid),
        ("flipped bit", public_bytes(key), message, bytes([valid[0] ^ 1]) + valid[1:]),
        ("S plus L", public_bytes(key), message, valid[:32] + (s + ED25519_L).to_bytes(32, "little")),
        ("S equals L", public_bytes(key), message, valid[:32] + ED25519_L.to_bytes(32, "little")),
        ("small-order key, identity signature", identity, message, identity + bytes(32)),
        ("small-order key, other message", identity, b"", identity + bytes(32)),
        ("non-canonical identity key", non_canonical_identity, message, identity + bytes(32)),
        ("non-canonical identity R", identity, message, non_canonical_identity + bytes(32)),
        ("key not on the curve", bytes([2]) + bytes(31), message, valid),
        ("all-zero key", bytes(32), message, valid),
        ("all-0xff key", bytes([0xFF]) * 32, message, valid),
    ]
    result = []
    for name, public, data, signature in cases:
        ring = KeyRing({"k": public})
        result.append({"name": name, "key": public.hex(), "message": data.hex(), "signature": signature.hex(),
                       "result": outcome(lambda r=ring, d=data, sig=signature: r.verify("k", d, sig))})
    ring = KeyRing({"k": public_bytes(key)})
    result.append({"name": "unknown key id", "key": public_bytes(key).hex(), "key_id": "other",
                   "message": message.hex(), "signature": valid.hex(),
                   "result": outcome(lambda: ring.verify("other", message, valid))})
    result.append({"name": "short signature", "key": public_bytes(key).hex(), "message": message.hex(),
                   "signature": valid[:63].hex(), "result": outcome(lambda: ring.verify("k", message, valid[:63]))})
    result.append({"name": "no keys", "key": None, "message": message.hex(), "signature": valid.hex(),
                   "result": outcome(lambda: KeyRing({}).verify("k", message, valid))})
    return result


# ---------------------------------------------------------------- manifests


def base_signed(distribution: str, version: str = "0.4.2") -> dict:
    suffix = "-portable.zip" if distribution == "portable" else "-setup.exe"
    if distribution == "portable":
        names = sorted(REQUIRED_PAYLOADS | {"ENTRY-HASHES.json", "BUILD-IDENTITY.json", "SOURCE-INPUTS.json"})
    else:
        names = ["sylvia-gm.exe", "sylvia-gm-owner.exe", *(f"licenses/{n}" for n in sorted(LEGAL_FILES)),
                 "licenses/SRD-5.2.1.md", "licenses/Severed-Lands.md", "licenses/dependency-notices.txt"]
    files = []
    for index, name in enumerate(names):
        size = 13 if name == "sylvia-portable.json" else 1000 + index
        files.append({"path": name, "size": size, "sha256": hashlib.sha256(name.encode()).hexdigest()})
    return {
        "format_version": 1,
        "version": version,
        "owner_version": version,
        "minimum_app_version": "0.4.0",
        "channel": __release_channel__,
        "target": "windows-x86_64",
        "distribution": distribution,
        "notes": "Release notes \u00e9",
        "artifact": {
            "url": f"https://github.com/pneuma-io/sylvia-gm/releases/download/v{version}/"
                   f"sylvia-gm-{version}-windows-x86_64{suffix}",
            "size": 123456,
            "sha256": hashlib.sha256(b"artifact").hexdigest(),
        },
        "files": files,
    }


def envelope(key, signed, *, key_id=KEY_ID, signature=None, extra=None, drop=None) -> bytes:
    if signature is None:
        signature = base64.b64encode(key.sign(canonical_bytes(signed))).decode()
    value = {"signed": signed, "key_id": key_id, "signature": signature}
    if extra:
        value.update(extra)
    if drop:
        value.pop(drop)
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def manifest_summary(result) -> dict:
    return {
        "version": result.version.text,
        "channel": result.channel,
        "target": result.target,
        "distribution": result.distribution,
        "notes": result.notes,
        "artifact_url": result.artifact_url,
        "artifact_size": result.artifact_size,
        "artifact_sha256": result.artifact_sha256,
        "files": [[row.path, row.size, row.sha256] for row in result.files],
        "signed_sha256": hashlib.sha256(result.signed_bytes).hexdigest(),
        "envelope_sha256": hashlib.sha256(result.envelope_bytes).hexdigest(),
    }


def manifest_vectors(key, other):
    ring = KeyRing({KEY_ID: public_bytes(key)})
    cases = []

    def add(name, raw, *, distribution="portable", target="windows-x86_64", current=__version__, recovery=False):
        parse = manifest.parse_current_recovery_manifest if recovery else manifest.parse_manifest
        result = outcome(lambda: manifest_summary(parse(
            raw, current=Version.parse(current), target=target, distribution=distribution, verify=ring.verify)))
        cases.append({"name": name, "raw": encode_raw(raw), "distribution": distribution, "target": target,
                      "current": current, "recovery": recovery, "result": result})

    def signed_case(name, mutate, distribution="portable", **options):
        signed = base_signed(distribution)
        mutate(signed)
        add(name, envelope(key, signed), distribution=distribution, **options)

    for distribution in ("portable", "installed"):
        signed_case(f"valid {distribution}", lambda s: None, distribution)
    add("valid but other distribution asked", envelope(key, base_signed("portable")), distribution="installed")
    add("recovery manifest of the running version", envelope(key, base_signed("installed", __version__)),
        distribution="installed", recovery=True)
    add("recovery manifest of another version", envelope(key, base_signed("installed")), distribution="installed",
        recovery=True)
    # Envelope shape and signature.
    signed = base_signed("portable")
    add("not JSON", b"{")
    add("empty", b"")
    add("not an object", b"[]")
    add("extra envelope field", envelope(key, signed, extra={"x": 1}))
    add("missing signature field", envelope(key, signed, drop="signature"))
    add("signed is a list", json.dumps({"signed": [], "key_id": KEY_ID, "signature": "AA=="}).encode())
    add("key id not a string", json.dumps({"signed": signed, "key_id": 7, "signature": "AA=="}).encode())
    add("key id with a space", envelope(key, signed, key_id="test key"))
    add("key id too long", envelope(key, signed, key_id="k" * 65))
    add("key id empty", envelope(key, signed, key_id=""))
    add("unknown key id", envelope(key, signed, key_id="other-key"))
    add("signature not a string", json.dumps({"signed": signed, "key_id": KEY_ID, "signature": 5}).encode())
    add("signature too long", envelope(key, signed, signature="A" * 101))
    add("signature not base64", envelope(key, signed, signature="!" * 88))
    add("signature wrong length", envelope(key, signed, signature=base64.b64encode(bytes(63)).decode()))
    add("signature by another key", envelope(key, signed, signature=base64.b64encode(
        other.sign(canonical_bytes(signed))).decode()))
    good = key.sign(canonical_bytes(signed))
    add("signature with nonzero trailing bits", envelope(key, signed, signature=base64.b64encode(good).decode()[:-3]
                                                          + chr(ord(base64.b64encode(good).decode()[-3]) | 1) + "=="))
    s = int.from_bytes(good[32:], "little")
    add("signature S plus L", envelope(key, signed, signature=base64.b64encode(
        good[:32] + (s + ED25519_L).to_bytes(32, "little")).decode()))
    tampered = copy.deepcopy(signed)
    tampered["notes"] = "Tampered after signing"
    add("payload changed after signing", envelope(key, tampered, signature=base64.b64encode(good).decode()))
    reordered = json.dumps({"signature": base64.b64encode(good).decode(), "key_id": KEY_ID,
                            "signed": dict(reversed(list(signed.items())))}, indent=2).encode()
    add("reordered and indented envelope", reordered)
    add("duplicate envelope field", b'{"signed":{},"signed":{},"key_id":"k","signature":"AA=="}')
    floaty = copy.deepcopy(signed)
    floaty["format_version"] = 1.0
    add("float format version", envelope(key, floaty))
    # Manifest fields, each mutation signed.
    mutations = {
        "extra field": lambda s: s.update(extra=1),
        "missing notes": lambda s: s.pop("notes"),
        "format version 2": lambda s: s.update(format_version=2),
        "format version true": lambda s: s.update(format_version=True),
        "format version string": lambda s: s.update(format_version="1"),
        "version not semver": lambda s: s.update(version="0.4"),
        "version not a string": lambda s: s.update(version=42),
        "minimum not semver": lambda s: s.update(minimum_app_version="x"),
        "same version": lambda s: s.update(version=__version__, owner_version=__version__),
        "older version": lambda s: s.update(version="0.4.0", owner_version="0.4.0"),
        "prerelease of the current version": lambda s: s.update(version="0.4.1-alpha.1", owner_version="0.4.1-alpha.1"),
        "newer alpha prerelease": lambda s: s.update(version="0.4.2-alpha.1", owner_version="0.4.2-alpha.1"),
        "newer beta prerelease": lambda s: s.update(version="0.4.2-beta.1", owner_version="0.4.2-beta.1"),
        "build metadata only": lambda s: s.update(version="0.4.1+b", owner_version="0.4.1+b"),
        "newer with build metadata": lambda s: s.update(version="0.4.2+b", owner_version="0.4.2+b"),
        "minimum above current": lambda s: s.update(minimum_app_version="0.4.2"),
        "minimum above version": lambda s: s.update(version="0.5.0", owner_version="0.5.0", minimum_app_version="0.6.0"),
        "minimum equal current": lambda s: s.update(minimum_app_version=__version__),
        "owner version differs": lambda s: s.update(owner_version="0.4.3"),
        "owner version not a string": lambda s: s.update(owner_version=None),
        "stable channel": lambda s: s.update(channel="stable"),
        "channel not a string": lambda s: s.update(channel=["alpha"]),
        "wrong target": lambda s: s.update(target="windows-aarch64"),
        "wrong distribution field": lambda s: s.update(distribution="installed"),
        "notes not a string": lambda s: s.update(notes=None),
        "notes at the limit": lambda s: s.update(notes="n" * 65536),
        "notes over the limit": lambda s: s.update(notes="\u00e9" * 32769),
        "artifact not an object": lambda s: s.update(artifact=[]),
        "artifact extra field": lambda s: s["artifact"].update(x=1),
        "files not a list": lambda s: s.update(files={}),
        "files too few": lambda s: s.update(files=s["files"][:2]),
        "file row not an object": lambda s: s["files"].append([1]),
        "file row extra field": lambda s: s["files"][0].update(x=1),
        "file unsafe path": lambda s: s["files"][0].update(path="../x"),
        "file path in data": lambda s: s["files"].append({"path": "campaigns/x", "size": 1, "sha256": "0" * 64}),
        "duplicate file path": lambda s: s["files"].append(dict(s["files"][0])),
        "duplicate file path by case": lambda s: s["files"].append(dict(s["files"][0], path=s["files"][0]["path"].upper())),
        "file size zero": lambda s: s["files"][0].update(size=0),
        "file size negative": lambda s: s["files"][0].update(size=-1),
        "file size boolean": lambda s: s["files"][0].update(size=True),
        "file size string": lambda s: s["files"][0].update(size="1"),
        "file size over artifact limit": lambda s: s["files"][0].update(size=512 * 1024 * 1024 + 1),
        "file size at artifact limit": lambda s: s["files"][0].update(size=512 * 1024 * 1024),
        "portable marker too large": lambda s: [r.update(size=129) for r in s["files"] if r["path"] == "sylvia-portable.json"],
        "portable marker at limit": lambda s: [r.update(size=128) for r in s["files"] if r["path"] == "sylvia-portable.json"],
        "file hash uppercase": lambda s: s["files"][0].update(sha256="A" * 64),
        "file hash short": lambda s: s["files"][0].update(sha256="a" * 63),
        "file hash not a string": lambda s: s["files"][0].update(sha256=0),
        "expanded too large": lambda s: [r.update(size=512 * 1024 * 1024) for r in s["files"][:5]],
        "required file missing": lambda s: s.update(files=[r for r in s["files"] if r["path"] != "sylvia-gm-owner.exe"]),
        "marker missing": lambda s: s.update(files=[r for r in s["files"] if r["path"] != "sylvia-portable.json"]),
        "artifact url wrong version": lambda s: s["artifact"].update(url=s["artifact"]["url"].replace("v0.4.2", "v0.4.3")),
        "artifact url wrong suffix": lambda s: s["artifact"].update(url=s["artifact"]["url"].replace("-portable.zip", "-setup.exe")),
        "artifact url other host": lambda s: s["artifact"].update(url=s["artifact"]["url"].replace("github.com", "evil.com")),
        "artifact url broken bracket": lambda s: s["artifact"].update(url="https://[github.com/x"),
        "artifact size zero": lambda s: s["artifact"].update(size=0),
        "artifact size over limit": lambda s: s["artifact"].update(size=512 * 1024 * 1024 + 1),
        "artifact hash invalid": lambda s: s["artifact"].update(sha256="x"),
        "big integer size": lambda s: s["artifact"].update(size=2**64),
    }
    for name, mutate in mutations.items():
        signed_case(name, mutate)
    for name in ("marker missing", "required file missing", "file path in data"):
        signed_case(f"{name} (installed)", mutations[name], "installed")
    signed_case("installed with a marker", lambda s: s["files"].append(
        {"path": "sylvia-portable.json", "size": 13, "sha256": "0" * 64}), "installed")
    signed_case("installed missing a notice", lambda s: s.update(
        files=[r for r in s["files"] if r["path"] != "licenses/LICENSE"]), "installed")
    add("deep nesting", envelope(key, dict(base_signed("portable"), notes=None) | {"notes": "x"}).replace(
        b'"notes": "x"', b'"notes": ' + b"[" * 40 + b"]" * 40))
    add("other target asked", envelope(key, base_signed("portable")), target="linux-x86_64")
    add("current is a prerelease", envelope(key, base_signed("portable")), current="0.4.2-alpha.1")
    return cases


# ---------------------------------------------------------------- transport scenarios


class ScriptedResponse:
    def __init__(self, script, cancel):
        self.headers = Message()
        for name, value in script.get("headers", []):
            self.headers[name] = value
        self._chunks = [segment_bytes(chunk) for chunk in script.get("chunks", [])]
        self._fail_at = script.get("fail_at")
        self._cancel_at = script.get("cancel_at")
        self._reads = 0
        self._cancel = cancel
        self.requests = []

    def read(self, maximum):
        self.requests.append(maximum)
        index = self._reads
        self._reads += 1
        if self._cancel_at == index:
            self._cancel.set()
        if self._fail_at == index:
            raise ConnectionResetError("dropped")
        if not self._chunks:
            return b""
        chunk = self._chunks[0]
        if len(chunk) > maximum:
            self._chunks[0] = chunk[maximum:]
            return chunk[:maximum]
        self._chunks.pop(0)
        return chunk


class ScriptedTransport:
    def __init__(self, scripts):
        self.scripts = scripts
        self.opened = []
        self.reads = []

    @contextlib.contextmanager
    def open(self, url, cancel=None):
        self.opened.append(url)
        script = self.scripts.get(url)
        if script is None:
            raise UpdateError("service_unavailable", "The official release service is unavailable. Try again later.")
        if "open_error" in script:
            raise UpdateError(*script["open_error"])
        response = ScriptedResponse(script, cancel)
        try:
            yield response
        finally:
            self.reads.append(response.requests)


def segment_bytes(segment) -> bytes:
    if "fill" in segment:
        return bytes.fromhex(segment["fill"]) * segment["count"]
    return bytes.fromhex(segment["hex"])


def body(value) -> list[dict]:
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return [{"hex": raw[index:index + 4000].hex()} for index in range(0, len(raw), 4000)]


def metadata_vectors():
    url = transport.OFFICIAL_RELEASES
    limit = manifest.MANIFEST_LIMIT
    scripts = {
        "plain": {"chunks": body(b"[]")},
        "with length": {"headers": [["Content-Length", "2"]], "chunks": body(b"[]")},
        "short body": {"headers": [["Content-Length", "3"]], "chunks": body(b"[]")},
        "long body": {"headers": [["Content-Length", "1"]], "chunks": body(b"[]")},
        "declared too large": {"headers": [["Content-Length", str(limit + 1)]], "chunks": body(b"[]")},
        "declared at limit": {"headers": [["Content-Length", str(limit)]], "chunks": body(b"[]")},
        "body over limit": {"chunks": [{"fill": "20", "count": limit + 1}]},
        "body at limit": {"chunks": [{"fill": "20", "count": limit}]},
        "body at limit in pieces": {"chunks": [{"fill": "20", "count": 300000}] * 6 + [{"fill": "20", "count": limit - 1800000}]},
        "two lengths": {"headers": [["Content-Length", "2"], ["content-length", "2"]], "chunks": body(b"[]")},
        "length with sign": {"headers": [["Content-Length", "+2"]], "chunks": body(b"[]")},
        "length empty": {"headers": [["Content-Length", ""]], "chunks": body(b"[]")},
        "length 21 digits": {"headers": [["Content-Length", "1" + "0" * 20]], "chunks": body(b"[]")},
        "length 20 digits": {"headers": [["Content-Length", "9" * 20]], "chunks": body(b"[]")},
        "length unicode digits": {"headers": [["Content-Length", "\u0662"]], "chunks": body(b"[]")},
        "gzip": {"headers": [["Content-Encoding", "gzip"]], "chunks": body(b"[]")},
        "identity upper": {"headers": [["content-encoding", "IDENTITY"]], "chunks": body(b"[]")},
        "read fails": {"chunks": body(b"[]"), "fail_at": 0},
        "read fails later": {"chunks": [{"hex": "5b"}, {"fill": "20", "count": 300000}, {"hex": "5d"}], "fail_at": 1},
        "cancelled while reading": {"chunks": [{"hex": "5b"}, {"fill": "20", "count": 300000}, {"hex": "5d"}], "cancel_at": 0},
        "open refused": {"open_error": ["rate_limited", "The release service is rate limited. Try again later."]},
    }
    result = []
    for name, script in scripts.items():
        scripted = ScriptedTransport({url: script})
        cancel = threading.Event()
        value = outcome(lambda: hashlib.sha256(transport.metadata_bytes(scripted, url, cancel)).hexdigest())
        result.append({"name": name, "script": script, "result": value, "reads": scripted.reads})
    cancelled = threading.Event()
    cancelled.set()
    scripted = ScriptedTransport({url: scripts["plain"]})
    result.append({"name": "cancelled before", "script": scripts["plain"], "cancelled": True,
                   "result": outcome(lambda: transport.metadata_bytes(scripted, url, cancelled)), "reads": []})
    return result


def release_row(version, *, draft=False, prerelease=True, assets=None, tag=None, extra=None):
    row = {"tag_name": f"v{version}" if tag is None else tag, "draft": draft, "prerelease": prerelease,
           "assets": assets if assets is not None else []}
    if extra:
        row.update(extra)
    return row


def asset(version, distribution="portable", url=None):
    name = f"sylvia-gm-{version}-windows-x86_64-{distribution}-update.json"
    return {"name": name, "browser_download_url": url or
            f"https://github.com/pneuma-io/sylvia-gm/releases/download/v{version}/{name}"}


def discovery_vectors(key):
    ring = KeyRing({KEY_ID: public_bytes(key)})
    result = []

    def manifest_url(version, distribution="portable"):
        return asset(version, distribution)["browser_download_url"]

    def run(name, releases, extra_scripts=None, distribution="portable", releases_script=None, retain=False):
        scripts = {transport.OFFICIAL_RELEASES: releases_script or {"chunks": body(releases)}}
        scripts.update(extra_scripts or {})
        scripted = ScriptedTransport(scripts)
        cancel = threading.Event()

        def call():
            function = discovery.retain_current_manifest if retain else discovery.discover
            found = function(scripted, cancel, current=Version.parse(__version__), target="windows-x86_64",
                             distribution=distribution, verify=ring.verify)
            return None if found is None else manifest_summary(found)

        value = outcome(call)
        result.append({"name": name, "distribution": distribution, "retain": retain, "scripts": scripts,
                       "result": value, "opened": scripted.opened})

    def signed(version, distribution="portable", **changes):
        value = base_signed(distribution, version)
        value.update(changes)
        return {"chunks": body(envelope(key, value))}

    run("no releases", [])
    run("only older", [release_row("0.4.0", assets=[asset("0.4.0")]), release_row("0.4.1")])
    run("one newer", [release_row("0.4.2", assets=[asset("0.4.2")])], {manifest_url("0.4.2"): signed("0.4.2")})
    run("newest of several", [release_row("0.4.2", assets=[asset("0.4.2")]), release_row("0.5.0", assets=[asset("0.5.0")]),
                              release_row("0.4.3", assets=[asset("0.4.3")])], {manifest_url("0.5.0"): signed("0.5.0")})
    run("draft skipped", [release_row("0.5.0", draft=True), release_row("0.4.2", assets=[asset("0.4.2")])],
        {manifest_url("0.4.2"): signed("0.4.2")})
    run("stable release skipped", [release_row("0.4.2", prerelease=False, assets=[asset("0.4.2")])])
    run("beta skipped", [release_row("0.4.2-beta.1", assets=[asset("0.4.2-beta.1")])])
    run("alpha prerelease tag", [release_row("0.4.2-alpha.1", assets=[asset("0.4.2-alpha.1")])],
        {manifest_url("0.4.2-alpha.1"): signed("0.4.2-alpha.1")})
    run("alpha tag not flagged", [release_row("0.4.2-alpha.1", prerelease=False)])
    run("bad tag skipped", [release_row("x", tag="nightly"), release_row("x", tag="v0.4"), release_row("x", tag=7)])
    run("draft not boolean", [release_row("0.4.2", extra={"draft": 0})])
    run("prerelease missing", [{"tag_name": "v0.4.2", "draft": False}])
    run("release not an object", [[]])
    run("index not a list", {"releases": []})
    run("index too long", [release_row("0.4.0")] * 31)
    run("index of 30", [release_row("0.4.0")] * 30)
    run("ambiguous version", [release_row("0.4.2", assets=[asset("0.4.2")]), release_row("0.4.2+b", tag="v0.4.2+b")])
    run("ambiguous older ignored", [release_row("0.4.2"), release_row("0.4.2"), release_row("0.4.3", assets=[asset("0.4.3")])],
        {manifest_url("0.4.3"): signed("0.4.3")})
    run("assets missing", [{"tag_name": "v0.4.2", "draft": False, "prerelease": True}])
    run("assets too many", [release_row("0.4.2", assets=[{"name": "x"}] * 129)])
    run("manifest asset missing", [release_row("0.4.2", assets=[asset("0.4.2", "installed")])])
    run("manifest asset twice", [release_row("0.4.2", assets=[asset("0.4.2"), asset("0.4.2")])])
    run("manifest asset off site", [release_row("0.4.2", assets=[asset("0.4.2", url="https://evil.com/x")])])
    run("manifest asset url missing", [release_row("0.4.2", assets=[{"name": asset("0.4.2")["name"]}])])
    run("manifest signs another version", [release_row("0.4.2", assets=[asset("0.4.2")])],
        {manifest_url("0.4.2"): signed("0.4.3")})
    run("manifest build metadata differs", [release_row("0.4.2", assets=[asset("0.4.2")])],
        {manifest_url("0.4.2"): signed("0.4.2+b", owner_version="0.4.2+b")})
    run("manifest unavailable", [release_row("0.4.2", assets=[asset("0.4.2")])])
    run("index unavailable", [], releases_script={"open_error": ["network_unavailable",
                                                                  "The update connection failed. You can retry safely."]})
    run("index not JSON", [], releases_script={"chunks": body(b"{")})
    run("installed newer", [release_row("0.4.2", assets=[asset("0.4.2", "installed")])],
        {manifest_url("0.4.2", "installed"): signed("0.4.2", "installed")}, distribution="installed")
    current_name = f"sylvia-gm-{__version__}-windows-x86_64-installed-update.json"
    current_url = f"https://github.com/pneuma-io/sylvia-gm/releases/download/v{__version__}/{current_name}"
    run("retain current", [], {current_url: {"chunks": body(envelope(key, base_signed("installed", __version__)))}},
        distribution="installed", retain=True)
    run("retain current wrong version", [], {current_url: {"chunks": body(envelope(key, base_signed("installed")))}},
        distribution="installed", retain=True)
    run("retain current for portable", [], distribution="portable", retain=True)
    return result


def download_vectors(key):
    segments = [{"fill": "61", "count": 100000}, {"fill": "62", "count": 200000}, {"fill": "63", "count": 99999}, {"hex": "64"}]
    payload = b"".join(segment_bytes(segment) for segment in segments)
    digest = hashlib.sha256(payload).hexdigest()
    ring = KeyRing({KEY_ID: public_bytes(key)})
    result = []

    def scenario(name, script, *, size=len(payload), sha=digest, preexisting=(), cancel_first=False):
        signed = base_signed("portable")
        signed["artifact"].update(size=size, sha256=sha)
        parsed = manifest.parse_manifest(envelope(key, signed), current=Version.parse(__version__),
                                         target="windows-x86_64", distribution="portable", verify=ring.verify)
        folder = Path(tempfile.mkdtemp(prefix="sylvia-update-vectors-"))
        try:
            stage = folder / "stage"
            stage.mkdir()
            for entry in preexisting:
                (stage / entry).write_bytes(b"x")
            scripted = ScriptedTransport({parsed.artifact_url: script})
            cancel = threading.Event()
            if cancel_first:
                cancel.set()
            progress = []
            value = outcome(lambda: transport.download_artifact(
                scripted, parsed, stage, cancel, lambda received, total: progress.append([received, total])).name)
            files = {entry.name: hashlib.sha256(entry.read_bytes()).hexdigest() for entry in sorted(stage.iterdir())}
        finally:
            shutil.rmtree(folder)
        result.append({"name": name, "envelope": encode_raw(envelope(key, signed)), "script": script,
                       "preexisting": list(preexisting), "cancelled": cancel_first, "result": value,
                       "progress": progress, "files": files, "reads": scripted.reads})

    chunks = segments
    scenario("complete", {"chunks": chunks, "headers": [["Content-Length", str(len(payload))]]})
    scenario("complete without length", {"chunks": chunks})
    scenario("length differs", {"chunks": chunks, "headers": [["Content-Length", str(len(payload) - 1)]]})
    scenario("body too long", {"chunks": chunks + [{"hex": "41"}]})
    scenario("body too short", {"chunks": chunks[:-1]})
    scenario("tampered byte", {"chunks": [*chunks[:-1], {"hex": "00"}]})
    scenario("wrong hash signed", {"chunks": chunks}, sha="0" * 64)
    scenario("connection drops", {"chunks": chunks, "fail_at": 1})
    scenario("cancelled midway", {"chunks": chunks, "cancel_at": 1})
    scenario("cancelled before", {"chunks": chunks}, cancel_first=True)
    scenario("partial exists", {"chunks": chunks}, preexisting=["artifact.partial"])
    scenario("verified exists", {"chunks": chunks}, preexisting=["artifact.verified"])
    scenario("encoded response", {"chunks": chunks, "headers": [["Content-Encoding", "gzip"]]})
    scenario("open refused", {"open_error": ["service_unavailable",
                                             "The official release service is unavailable. Try again later."]})
    return result


def main() -> None:
    key, other = test_key("A"), test_key("B")
    versions, order = version_vectors()
    splits, unquotes, releases, officials, redirects, finals = url_vectors()
    vectors = {
        "python": sys.version.split()[0],
        "app_version": __version__,
        "release_channel": __release_channel__,
        "public_key": public_bytes(key).hex(),
        "key_id": KEY_ID,
        "versions": versions,
        "version_order": order,
        "base64": b64_vectors(),
        "urlsplit": splits,
        "unquote": unquotes,
        "release_url": releases,
        "official_request": officials,
        "redirects": redirects,
        "final_location": finals,
        "payload_path": payload_path_vectors(),
        "signatures": signature_vectors(key),
        "manifests": manifest_vectors(key, other),
        "metadata": metadata_vectors(),
        "discovery": discovery_vectors(key),
        "downloads": download_vectors(key),
    }
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
