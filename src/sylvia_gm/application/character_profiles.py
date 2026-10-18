"""Validate and retain immutable, setting-independent character sources."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from sylvia_gm.application.reusable_library_deletion import (
    is_link_or_reparse,
    validate_owned_files,
    validated_library_entry,
)
from sylvia_gm.domain.authority import SafePayload
from sylvia_gm.domain.content_validation import ContentShapeError, validate_content_shape
from sylvia_gm.domain.errors import CommandRejected, RulesetDiagnostic
from sylvia_gm.domain.package_library import canonical_json_bytes

MAX_PROFILE_BYTES = 256 * 1024
PROFILE_ID = re.compile(r"character-profile-[0-9a-f]{24}-[0-9a-f]{16}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_LOCK = threading.Lock()
_FILES = frozenset({"profile.json", "record.json"})


def _profile_error(code: str, message: str, path: str = "$") -> ContentShapeError:
    """Give Builder procedural corrections through the shared diagnostic channel."""
    return ContentShapeError(
        (
            RulesetDiagnostic(
                code=code,
                phase="content",
                file="character-profile.json",
                path=path,
                message=message,
            ),
        )
    )


def _unique_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Reject duplicate keys without echoing their contents."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON members must be unique")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ValueError("JSON numbers must be finite")


def _parse(source: bytes | str) -> object:
    """Bound both decoding and recursive JSON before structural validation."""
    try:
        data = source.encode("utf-8") if isinstance(source, str) else source
    except UnicodeError as error:
        raise _profile_error("profile_encoding_invalid", "Use UTF-8 JSON text.") from error
    if not data or len(data) > MAX_PROFILE_BYTES:
        raise _profile_error(
            "profile_bytes_invalid",
            "Supply a nonempty profile of at most 256 KiB; do not truncate.",
        )
    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=_unique_members, parse_constant=_reject_constant
        )
    except json.JSONDecodeError as error:
        raise _profile_error(
            "profile_json_invalid",
            f"Correct malformed JSON at line {error.lineno}, column {error.colno}.",
        ) from error
    except (ValueError, UnicodeError, RecursionError) as error:
        raise _profile_error(
            "profile_json_invalid",
            "Use unique-member UTF-8 JSON, finite numbers and at most 32 nesting levels.",
        ) from error
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 32:
            raise _profile_error("profile_depth_invalid", "Use at most 32 nesting levels.")
        if isinstance(item, float) and not math.isfinite(item):
            raise _profile_error("profile_number_invalid", "Use finite JSON numbers.")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def validate_character_profile(source: bytes | str) -> SafePayload:
    """Apply the same published definition for checks, saves and retained-source reads."""
    value = _parse(source)
    definition = (
        "character_profile_v2"
        if isinstance(value, dict)
        and type(value.get("format_version")) is int
        and value["format_version"] == 2
        else "character_profile"
    )
    validate_content_shape(definition, value, file="character-profile.json")
    if definition == "character_profile_v2":
        sections = (
            ("relationships", value.get("relationships", [])),
            ("dialogue_examples", value.get("dialogue_examples", [])),
            ("assets", value.get("assets", [])),
            ("lore.entries", value.get("lore", {}).get("entries", [])),
        )
        for name, section in sections:
            seen = set()
            for index, item in enumerate(section):
                if item["id"] in seen:
                    raise _profile_error(
                        "profile_id_duplicate",
                        "Use a unique ID within this section.",
                        f"$.{name}[{index}].id",
                    )
                seen.add(item["id"])
        for index, asset in enumerate(value.get("assets", [])):
            uri = asset["uri"]
            path = f"$.assets[{index}].uri"
            if any(character.isspace() for character in uri):
                raise _profile_error(
                    "profile_asset_invalid",
                    "Remove whitespace from the asset reference.",
                    path,
                )
            if uri.startswith(("http://", "https://")):
                try:
                    parsed = urlsplit(uri)
                except ValueError as error:
                    raise _profile_error(
                        "profile_asset_invalid",
                        "Use a valid HTTP(S) asset reference.",
                        path,
                    ) from error
                if not parsed.hostname or parsed.username or parsed.password:
                    raise _profile_error(
                        "profile_asset_invalid",
                        "Supply a hostname and remove credentials from the asset reference.",
                        path,
                    )
            elif uri.startswith("asset:sha256:"):
                if _SHA256.fullmatch(uri[len("asset:sha256:") :]) is None:
                    raise _profile_error(
                        "profile_asset_invalid",
                        "Use asset:sha256: followed by exactly 64 lowercase hexadecimal digits.",
                        path,
                    )
            elif uri.startswith("card://"):
                path = uri[len("card://") :]
                if not path or "\\" in path or path.startswith("/") or ".." in path.split("/"):
                    raise _profile_error(
                        "profile_asset_invalid",
                        "Use a bounded relative card asset identity.",
                        f"$.assets[{index}].uri",
                    )
    if len(canonical_json_bytes(value)) > MAX_PROFILE_BYTES:
        raise _profile_error(
            "profile_bytes_invalid",
            "Canonical profile must fit within 256 KiB; do not truncate.",
        )
    return cast(SafePayload, value)


@dataclass(frozen=True)
class CharacterProfile:
    """One exact unbound source and its safe catalog projection."""

    artifact_id: str
    content_sha256: str
    profile: SafePayload

    def provenance(self) -> SafePayload:
        return {
            "artifact_id": self.artifact_id,
            "content_sha256": self.content_sha256,
            "name": self.profile["name"],
        }

    def snapshot(self) -> SafePayload:
        return {
            "artifact_id": self.artifact_id,
            "content_sha256": self.content_sha256,
            "profile": dict(self.profile),
        }

    def catalog_payload(self) -> SafePayload:
        return {
            "kind": "character_profile",
            "id": self.artifact_id,
            "name": self.profile["name"],
            "title": self.profile["name"],
            "summary": self.profile["summary"],
            "content_sha256": self.content_sha256,
            "reusable": True,
            "campaign_ready": False,
            "adaptation_required": True,
        }


def profile_from_snapshot(value: object) -> CharacterProfile:
    """Verify retained bytes without depending on a still-present library source."""
    if not isinstance(value, Mapping) or set(value) != {
        "artifact_id",
        "content_sha256",
        "profile",
    }:
        raise ValueError("Character profile snapshot is invalid")
    artifact_id, digest = value["artifact_id"], value["content_sha256"]
    if (
        not isinstance(artifact_id, str)
        or PROFILE_ID.fullmatch(artifact_id) is None
        or not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
    ):
        raise ValueError("Character profile identity is invalid")
    profile = validate_character_profile(canonical_json_bytes(value["profile"]))
    if hashlib.sha256(canonical_json_bytes(profile)).hexdigest() != digest:
        raise ValueError("Character profile digest is invalid")
    if not artifact_id.endswith("-" + digest[:16]):
        raise ValueError("Character profile identity conflicts with its digest")
    return CharacterProfile(artifact_id, digest, profile)


def validate_source_profile(value: object) -> SafePayload | None:
    """Check stored provenance without requiring an upstream artifact to exist."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"artifact_id", "content_sha256", "name"}:
        raise ValueError("Character source metadata is invalid")
    artifact_id, digest, name = value["artifact_id"], value["content_sha256"], value["name"]
    if (
        not isinstance(artifact_id, str)
        or PROFILE_ID.fullmatch(artifact_id) is None
        or not isinstance(digest, str)
        or _SHA256.fullmatch(digest) is None
        or not artifact_id.endswith("-" + digest[:16])
        or not isinstance(name, str)
        or not name
        or name != name.strip()
        or len(name) > 160
        or "\x00" in name
    ):
        raise ValueError("Character source metadata is invalid")
    return dict(value)


def _read(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(MAX_PROFILE_BYTES + 1)


def _root(storage_root: Path) -> Path:
    """Reject linked roots before traversing or writing owner-local content."""
    root = storage_root / "character-profiles"
    if os.path.lexists(root) and (is_link_or_reparse(root) or not root.is_dir()):
        raise ValueError("Character profile library path is unsafe")
    return root


def load_character_profile(
    storage_root: Path,
    artifact_id: str,
    expected_content_sha256: str | None = None,
) -> CharacterProfile:
    """Load only an exact, safe library entry and verify all retained bytes."""
    if not isinstance(artifact_id, str) or PROFILE_ID.fullmatch(artifact_id) is None:
        raise ValueError("Character profile identity is invalid")
    _root(storage_root)
    target = validated_library_entry(
        storage_root,
        library_name="character-profiles",
        artifact_id=artifact_id,
    )
    if target is None:
        raise CommandRejected(
            "Refresh the library and select a saved profile", code="character_profile_unavailable"
        )
    validate_owned_files(target, _FILES)
    record = _parse(_read(target / "record.json"))
    if (
        not isinstance(record, dict)
        or set(record)
        != {
            "format_id",
            "format_version",
            "artifact_id",
            "content_sha256",
        }
        or record["format_id"] != "sylvia-gm-character-profile-record"
        or type(record["format_version"]) is not int
        or record["format_version"] != 1
        or record["artifact_id"] != artifact_id
    ):
        raise ValueError("Character profile record is invalid")
    source = profile_from_snapshot(
        {
            "artifact_id": artifact_id,
            "content_sha256": record["content_sha256"],
            "profile": validate_character_profile(_read(target / "profile.json")),
        }
    )
    if expected_content_sha256 is not None and source.content_sha256 != expected_content_sha256:
        raise CommandRejected(
            "Refresh the library; the selected profile does not match",
            code="stale_character_profile",
        )
    return source


def save_character_profile(
    storage_root: Path,
    *,
    operation_id: str,
    source: bytes | str,
) -> CharacterProfile:
    """Publish a complete immutable entry atomically; exact retries reuse it."""
    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError("Character profile save identity is required")
    profile = validate_character_profile(source)
    encoded = canonical_json_bytes(profile)
    digest = hashlib.sha256(encoded).hexdigest()
    operation_hash = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
    artifact_id = f"character-profile-{operation_hash}-{digest[:16]}"
    with _LOCK:
        root = _root(storage_root)
        target = root / artifact_id
        if os.path.lexists(target):
            return load_character_profile(storage_root, artifact_id, digest)
        root.mkdir(parents=True, exist_ok=True)
        if len(tuple(root.iterdir())) >= 256:
            raise ValueError("Character profile library has reached its 256-entry limit")
        with tempfile.TemporaryDirectory(
            prefix=".character-profile-save-", dir=storage_root
        ) as temp:
            staged = Path(temp) / artifact_id
            staged.mkdir()
            for filename, data in (
                ("profile.json", encoded),
                (
                    "record.json",
                    canonical_json_bytes(
                        {
                            "format_id": "sylvia-gm-character-profile-record",
                            "format_version": 1,
                            "artifact_id": artifact_id,
                            "content_sha256": digest,
                        }
                    ),
                ),
            ):
                with (staged / filename).open("wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            staged.rename(target)
        return load_character_profile(storage_root, artifact_id, digest)


def character_profile_catalog(
    storage_root: Path,
    *,
    on_invalid: Callable[[str, Exception], None],
) -> tuple[CharacterProfile, ...]:
    """Keep good entries discoverable while reporting unsafe or malformed sources."""
    root = _root(storage_root)
    if not root.exists():
        return ()
    paths = sorted(root.iterdir(), key=lambda item: item.name)
    if len(paths) > 256:
        raise ValueError("Character profile library exceeds its 256-entry limit")
    result = []
    for path in paths:
        try:
            result.append(load_character_profile(storage_root, path.name))
        except (OSError, UnicodeError, ValueError, CommandRejected) as error:
            on_invalid(path.name if PROFILE_ID.fullmatch(path.name) else "invalid-entry", error)
    return tuple(result)
