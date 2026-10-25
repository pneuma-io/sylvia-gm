"""Bounded signed desktop-release identity, independent of download or execution."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import total_ordering
from pathlib import PurePosixPath
from types import MappingProxyType
from urllib.parse import unquote, urlsplit

MANIFEST_LIMIT = 2 * 1024 * 1024
ARTIFACT_LIMIT = 512 * 1024 * 1024
EXPANDED_LIMIT = 2 * 1024 * 1024 * 1024
OFFICIAL_RELEASES = "https://api.github.com/repos/pneuma-io/sylvia-gm/releases?per_page=30"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(
    r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z"
)
_MUTABLE = frozenset(
    {
        "isolated-data",
        "campaigns",
        "participants",
        "builder-chats",
        "migration-sources",
        "webview",
        "diagnostics",
        "temp",
        "dictation-models",
        ".application-updates",
    }
)


class UpdateError(ValueError):
    """A stable updater outcome; messages never contain submitted values or credentials."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@total_ordering
@dataclass(frozen=True, eq=False)
class Version:
    text: str
    core: tuple[int, int, int]
    prerelease: tuple[str, ...]

    @classmethod
    def parse(cls, value: object) -> Version:
        if not isinstance(value, str) or len(value) > 128:
            raise UpdateError("invalid_version", "The release version is not valid SemVer.")
        match = _VERSION.fullmatch(value)
        if match is None:
            raise UpdateError("invalid_version", "The release version is not valid SemVer.")
        pre = tuple(match[4].split(".")) if match[4] else ()
        if any(part.isdigit() and len(part) > 1 and part.startswith("0") for part in pre):
            raise UpdateError("invalid_version", "The release version is not valid SemVer.")
        return cls(value, (int(match[1]), int(match[2]), int(match[3])), pre)

    @property
    def channel(self) -> str:
        return self.prerelease[0] if self.prerelease else "stable"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Version) and (self.core, self.prerelease) == (
            other.core,
            other.prerelease,
        )

    def __hash__(self) -> int:
        return hash((self.core, self.prerelease))

    def __lt__(self, other: Version) -> bool:
        if self.core != other.core:
            return self.core < other.core
        if not self.prerelease or not other.prerelease:
            return bool(self.prerelease) and not other.prerelease
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            if left.isdigit() and right.isdigit():
                return int(left) < int(right)
            if left.isdigit() != right.isdigit():
                return left.isdigit()
            return left < right
        return len(self.prerelease) < len(other.prerelease)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError("invalid_metadata", "Release metadata contains duplicate fields.")
        result[key] = value
    return result


def bounded_json(raw: bytes, *, maximum: int = MANIFEST_LIMIT) -> object:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= maximum:
        raise UpdateError("metadata_limit", "Release metadata is missing or too large.")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        pending = [(value, 1)]
        count = 0
        while pending:
            item, depth = pending.pop()
            count += 1
            if depth > 32 or count > 50_000:
                raise UpdateError(
                    "metadata_limit", "Release metadata exceeds its structural limits."
                )
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
            elif item is not None and type(item) not in (str, int, bool):
                raise UpdateError(
                    "invalid_metadata", "Release metadata contains unsupported values."
                )
        return value
    except UpdateError:
        raise
    except (UnicodeError, ValueError, RecursionError) as error:
        raise UpdateError("invalid_metadata", "Release metadata could not be read.") from error


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise UpdateError("invalid_metadata", "Release metadata could not be encoded.") from error


@dataclass(frozen=True)
class KeyRing:
    """Only public build-pinned keys; this module has no signing or key-generation path."""

    keys: Mapping[str, bytes]

    def __post_init__(self) -> None:
        if any(
            not isinstance(key_id, str) or type(key) is not bytes or len(key) != 32
            for key_id, key in self.keys.items()
        ):
            raise UpdateError(
                "trust_not_configured", "The built-in update trust configuration is invalid."
            )
        object.__setattr__(self, "keys", MappingProxyType(dict(self.keys)))

    def verify(self, key_id: str, message: bytes, signature: bytes) -> None:
        if not self.keys:
            raise UpdateError(
                "trust_not_configured",
                "Updates are not configured in this build. A manual bootstrap upgrade is required.",
            )
        key = self.keys.get(key_id)
        if key is None or len(key) != 32 or len(signature) != 64:
            raise UpdateError("invalid_signature", "The update signature is not trusted.")
        try:
            from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        except ImportError as error:
            raise UpdateError(
                "verification_unavailable", "Update verification is unavailable."
            ) from error

        try:
            Ed25519PublicKey.from_public_bytes(key).verify(signature, message)
        except (InvalidSignature, ValueError, UnsupportedAlgorithm) as error:
            raise UpdateError(
                "invalid_signature", "The update signature is not trusted."
            ) from error


def release_url(value: object, version: str, distribution: str | None = None) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 2048
        or any(ord(char) <= 32 for char in value)
        or "\\" in value
    ):
        raise UpdateError(
            "untrusted_origin", "The update does not use an official release location."
        )
    try:
        parsed = urlsplit(value)
        path = unquote(parsed.path, errors="strict")
    except (ValueError, UnicodeError) as error:
        raise UpdateError("untrusted_origin", "The update location is invalid.") from error
    expected = f"/pneuma-io/sylvia-gm/releases/download/v{version}/"
    if (
        parsed.scheme != "https"
        or parsed.netloc != "github.com"
        or parsed.query
        or parsed.fragment
        or re.search(r"%(?![0-9A-Fa-f]{2})", parsed.path)
        or not path.startswith(expected)
        or "\\" in path
        or any(ord(char) < 32 for char in path)
        or "/" in path[len(expected) :]
        or path[len(expected) :] in ("", ".", "..")
    ):
        raise UpdateError(
            "untrusted_origin", "The update does not use an official release location."
        )
    suffix = {"installed": "-setup.exe", "portable": "-portable.zip"}.get(distribution or "")
    if suffix and not path.lower().endswith(suffix):
        raise UpdateError(
            "wrong_distribution", "The update artifact does not match this distribution."
        )
    return value


def payload_path(value: object) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 240 or not value.isascii():
        raise UpdateError("unsafe_payload", "The update contains an unsafe application path.")
    path = PurePosixPath(value)
    parts = value.split("/")
    if (
        path.is_absolute()
        or str(path) != value
        or any(part in ("", ".", "..") for part in parts)
        or any(
            re.fullmatch(r"[A-Za-z0-9_. -]+", part) is None or part.endswith((".", " "))
            for part in parts
        )
        or any(
            part.split(".")[0].rstrip(" .").upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{i}" for i in range(1, 10)),
                *(f"LPT{i}" for i in range(1, 10)),
            }
            for part in parts
        )
        or parts[0].casefold() in _MUTABLE
        or value.casefold() in {"authority.sqlite3", "config.yaml"}
    ):
        raise UpdateError("unsafe_payload", "The update contains an unsafe application path.")
    return value


@dataclass(frozen=True)
class PayloadFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Manifest:
    version: Version
    channel: str
    target: str
    distribution: str
    notes: str
    artifact_url: str
    artifact_size: int
    artifact_sha256: str
    files: tuple[PayloadFile, ...]
    signed_bytes: bytes
    envelope_bytes: bytes


def _size(value: object, maximum: int) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        raise UpdateError("invalid_metadata", "The update contains an invalid size.")
    return value


def _hash(value: object) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        raise UpdateError("invalid_metadata", "The update contains an invalid content identity.")
    return value


def parse_manifest(
    raw: bytes,
    *,
    current: Version,
    target: str,
    distribution: str,
    verify: Callable[[str, bytes, bytes], None],
) -> Manifest:
    return _parse_manifest(
        raw,
        current=current,
        target=target,
        distribution=distribution,
        verify=verify,
        current_recovery=False,
    )


def parse_current_recovery_manifest(
    raw: bytes,
    *,
    current: Version,
    target: str,
    distribution: str,
    verify: Callable[[str, bytes, bytes], None],
) -> Manifest:
    """Authenticate this exact running version for retention, never offer it as an update."""
    return _parse_manifest(
        raw,
        current=current,
        target=target,
        distribution=distribution,
        verify=verify,
        current_recovery=True,
    )


def _parse_manifest(
    raw: bytes,
    *,
    current: Version,
    target: str,
    distribution: str,
    verify: Callable[[str, bytes, bytes], None],
    current_recovery: bool,
) -> Manifest:
    envelope = bounded_json(raw)
    if not isinstance(envelope, dict) or set(envelope) != {"signed", "key_id", "signature"}:
        raise UpdateError("invalid_metadata", "The signed update envelope is incomplete.")
    signed, key_id, encoded = (envelope[key] for key in ("signed", "key_id", "signature"))
    if (
        not isinstance(signed, dict)
        or not isinstance(key_id, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key_id) is None
        or not isinstance(encoded, str)
        or len(encoded) > 100
    ):
        raise UpdateError("invalid_signature", "The update signature is missing or malformed.")
    try:
        signature = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise UpdateError(
            "invalid_signature", "The update signature is missing or malformed."
        ) from error
    if len(signature) != 64:
        raise UpdateError("invalid_signature", "The update signature is missing or malformed.")
    message = canonical_bytes(signed)
    verify(key_id, message, signature)
    fields = {
        "format_version",
        "version",
        "owner_version",
        "minimum_app_version",
        "channel",
        "target",
        "distribution",
        "notes",
        "artifact",
        "files",
    }
    if (
        set(signed) != fields
        or type(signed["format_version"]) is not int
        or signed["format_version"] != 1
    ):
        raise UpdateError("invalid_metadata", "The signed update manifest is incompatible.")
    version = Version.parse(signed["version"])
    minimum = Version.parse(signed["minimum_app_version"])
    if current_recovery and version.text != current.text:
        raise UpdateError(
            "wrong_recovery_version", "Recovery must retain the exact currently running release."
        )
    if not current_recovery and version <= current:
        raise UpdateError("not_newer", "This release is not newer than the current application.")
    if minimum > current or minimum > version:
        raise UpdateError(
            "bootstrap_required", "This release needs a newer manual bootstrap installation."
        )
    if signed["owner_version"] != version.text:
        raise UpdateError(
            "wrong_component_version", "Desktop and owner versions must update together."
        )
    if signed["channel"] != current.channel or version.channel != current.channel:
        raise UpdateError("wrong_channel", "This release is for a different update channel.")
    if signed["target"] != target or signed["distribution"] != distribution:
        raise UpdateError(
            "wrong_target", "This release is not for this architecture and distribution."
        )
    notes = signed["notes"]
    if not isinstance(notes, str) or len(notes.encode("utf-8")) > 64 * 1024:
        raise UpdateError("invalid_metadata", "Release notes are missing or too large.")
    artifact = signed["artifact"]
    if not isinstance(artifact, dict) or set(artifact) != {"url", "size", "sha256"}:
        raise UpdateError("invalid_metadata", "The update artifact is incomplete.")
    rows = signed["files"]
    if not isinstance(rows, list) or not 3 <= len(rows) <= 4096:
        raise UpdateError("invalid_metadata", "The application file inventory is incomplete.")
    files = []
    names = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"path", "size", "sha256"}:
            raise UpdateError("invalid_metadata", "The application file inventory is invalid.")
        name = payload_path(row["path"])
        if name.casefold() in names:
            raise UpdateError("unsafe_payload", "The update contains duplicate application paths.")
        names.add(name.casefold())
        maximum = 128 if name.casefold() == "sylvia-portable.json" else ARTIFACT_LIMIT
        files.append(PayloadFile(name, _size(row["size"], maximum), _hash(row["sha256"])))
    if sum(row.size for row in files) > EXPANDED_LIMIT:
        raise UpdateError("payload_limit", "The expanded update is too large.")
    from sylvia_gm.portable_artifact import LEGAL_FILES, REQUIRED_PAYLOADS

    required = (
        REQUIRED_PAYLOADS | {"ENTRY-HASHES.json", "BUILD-IDENTITY.json", "SOURCE-INPUTS.json"}
        if distribution == "portable"
        else {
            "sylvia-gm.exe",
            "sylvia-gm-owner.exe",
            *(f"licenses/{name}" for name in LEGAL_FILES),
            "licenses/SRD-5.2.1.md",
            "licenses/Severed-Lands.md",
            "licenses/dependency-notices.txt",
        }
    )
    if not {name.casefold() for name in required} <= names:
        raise UpdateError(
            "invalid_metadata", "The update omits required application files or notices."
        )
    if ("sylvia-portable.json" in names) != (distribution == "portable"):
        raise UpdateError("wrong_distribution", "The update would change this distribution mode.")
    return Manifest(
        version,
        current.channel,
        target,
        distribution,
        notes,
        release_url(artifact["url"], version.text, distribution),
        _size(artifact["size"], ARTIFACT_LIMIT),
        _hash(artifact["sha256"]),
        tuple(files),
        message,
        raw,
    )
