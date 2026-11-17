"""A bounded character transfer contains one source and only its referenced artwork."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, BinaryIO, cast

from sylvia_gm.application.character_card_import import MAX_CARD_BYTES, preview_character_card
from sylvia_gm.application.character_profiles import (
    validate_character_profile,
    validate_source_profile,
)
from sylvia_gm.application.image_hooks import MAX_IMAGE_BYTES, ImageData
from sylvia_gm.application.reusable_participants import ReusableParticipant
from sylvia_gm.domain.authority import SafePayload, thaw_json_value
from sylvia_gm.domain.content_validation import validate_content_shape
from sylvia_gm.domain.image_art import validate_character_visuals
from sylvia_gm.domain.package_library import canonical_json_bytes, parse_canonical_json_bytes
from sylvia_gm.infrastructure.zip_bounds import preflight_zip_directory, validate_zip_compression

CHARACTER_BUNDLE_BYTE_LIMIT = 64 * 1024 * 1024
_JSON_LIMIT = 256 * 1024
_MANIFEST_LIMIT = 32 * 1024
_PURPOSES = ("full_body", "reference_sheet", "portrait")
_SHA = re.compile(r"[0-9a-f]{64}")
ImageReader = Callable[[Mapping[str, Any]], bytes]


@dataclass(frozen=True)
class CharacterBundle:
    """Validated transfer bytes, ready for preview or an owner-controlled import."""

    manifest: dict[str, Any]
    character: dict[str, Any]
    files: dict[str, bytes]
    content_sha256: str

    def preview(self) -> dict[str, Any]:
        data = self.character
        profile = self.manifest["kind"] == "character_profile"
        return {
            "status": "preview",
            "source_sha256": self.content_sha256,
            "kind": self.manifest["kind"],
            "name": data["name"] if profile else data["participant"]["name"],
            "summary": data["summary"],
            "role": self.manifest["role"],
            "bindings": self.manifest["bindings"],
            "image_count": len(
                {
                    ref["path"]
                    for ref in _references(
                        {**data, "visuals": data.get("visuals") or self.manifest["visuals"]}
                    )
                }
            ),
            "original_card_included": "original-card.bin" in self.files,
            "adaptation_required": profile,
        }


def _references(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    visuals = validate_character_visuals(data.get("visuals"))
    return [visuals[purpose] for purpose in _PURPOSES if purpose in visuals] if visuals else []


def _binding(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {"id", "version", "content_sha256"}:
        raise ValueError("Character package binding is invalid")
    if (
        any(
            not isinstance(value[key], str)
            or not 0 < len(value[key]) <= 160
            or value[key] != value[key].strip()
            or "\x00" in value[key]
            for key in ("id", "version")
        )
        or not isinstance(value["content_sha256"], str)
        or not _SHA.fullmatch(value["content_sha256"])
    ):
        raise ValueError("Character package binding is invalid")


def _validate(manifest: object, payloads: dict[str, bytes]) -> CharacterBundle:
    if not isinstance(manifest, dict) or set(manifest) != {
        "format_id",
        "format_version",
        "kind",
        "role",
        "bindings",
        "source_profile",
        "source_artifact",
        "files",
        "visuals",
    }:
        raise ValueError("This is not a Sylvia character bundle")
    if (
        manifest["format_id"] != "sylvia-gm-character-bundle"
        or type(manifest["format_version"]) is not int
        or manifest["format_version"] != 1
        or not isinstance(manifest["kind"], str)
        or manifest["kind"] not in {"character_file", "character_profile"}
    ):
        raise ValueError("This character bundle format is unsupported")
    source = manifest["source_artifact"]
    if (
        not isinstance(source, dict)
        or set(source) != {"id", "content_sha256"}
        or not isinstance(source["id"], str)
        or not 0 < len(source["id"]) <= 160
        or "\x00" in source["id"]
        or not isinstance(source["content_sha256"], str)
        or not _SHA.fullmatch(source["content_sha256"])
    ):
        raise ValueError("Character source identity is invalid")
    records = manifest["files"]
    if not isinstance(records, dict) or set(records) != set(payloads):
        raise ValueError("Character bundle file inventory is incomplete")
    for path, raw in payloads.items():
        if records[path] != {"sha256": hashlib.sha256(raw).hexdigest(), "byte_count": len(raw)}:
            raise ValueError("Character bundle file checksum is invalid")
    data = cast(
        dict[str, Any], thaw_json_value(parse_canonical_json_bytes(payloads["character.json"]))
    )
    profile = manifest["kind"] == "character_profile"
    if profile:
        validate_character_profile(payloads["character.json"])
        if any(manifest[key] is not None for key in ("role", "bindings", "source_profile")):
            raise ValueError("Universal character profiles must remain unbound")
    else:
        validate_content_shape("character_file", data, file="character.json")
        if not isinstance(manifest["role"], str) or manifest["role"] not in {
            "player_character",
            "companion",
        }:
            raise ValueError("Character role is invalid")
        bindings = manifest["bindings"]
        if not isinstance(bindings, dict) or set(bindings) != {"ruleset", "campaign_setting"}:
            raise ValueError("Character package bindings are invalid")
        for binding in bindings.values():
            _binding(binding)
        validate_source_profile(manifest["source_profile"])
    if manifest["visuals"] is not None and (
        not profile or data.get("format_version") != 1 or "visuals" in data
    ):
        raise ValueError("Bundle-level artwork is reserved for legacy character profiles")
    expected = {"character.json"}
    for reference in _references({**data, "visuals": data.get("visuals") or manifest["visuals"]}):
        if set(reference) != {"path", "sha256"}:
            raise ValueError(
                "Portable artwork must use PNG paths instead of local request identities"
            )
        path = f"assets/characters/{reference['sha256']}.png"
        if reference["path"] != path or path not in payloads:
            raise ValueError("Character artwork is missing from this bundle")
        image = ImageData(payloads[path])
        _ = image.dimensions
        if image.sha256 != reference["sha256"]:
            raise ValueError("Character artwork does not match its saved identity")
        expected.add(path)
    if "original-card.bin" in payloads:
        original = preview_character_card(payloads["original-card.bin"])
        if not profile or data.get("import_source") != original.profile["import_source"]:
            raise ValueError("The original card does not match this character's source")
        expected.add("original-card.bin")
    elif profile and data.get("import_source") is not None:
        raise ValueError("The imported character is missing its original card")
    if set(payloads) != expected:
        raise ValueError("Character bundle contains unreferenced artwork or extra files")
    return CharacterBundle(
        manifest, data, payloads, hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
    )


def read_character_bundle(source: BinaryIO) -> CharacterBundle:
    """Validate a closed, bounded ZIP without extracting any archive-provided path."""
    raw = source.read(CHARACTER_BUNDLE_BYTE_LIMIT + 1)
    if not raw or len(raw) > CHARACTER_BUNDLE_BYTE_LIMIT:
        raise ValueError("Character bundle must fit within 64 MiB")
    stream = io.BytesIO(raw)
    preflight_zip_directory(stream, maximum_members=6)
    try:
        with zipfile.ZipFile(stream) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(set(names)) != len(names) or not {"manifest.json", "character.json"} <= set(
                names
            ):
                raise ValueError("Character bundle members are invalid")
            files = {}
            for info in infos:
                path = info.filename
                maximum = (
                    _MANIFEST_LIMIT
                    if path == "manifest.json"
                    else _JSON_LIMIT
                    if path == "character.json"
                    else MAX_CARD_BYTES
                    if path == "original-card.bin"
                    else MAX_IMAGE_BYTES
                    if re.fullmatch(r"assets/characters/[0-9a-f]{64}\.png", path)
                    else 0
                )
                validate_zip_compression(info)
                if (
                    not maximum
                    or info.is_dir()
                    or info.flag_bits & 1
                    or info.file_size > maximum
                    or (info.external_attr >> 16) & 0o170000 not in {0, 0o100000}
                ):
                    raise ValueError("Character bundle member is unsafe or too large")
                with archive.open(info) as member:
                    value = member.read(maximum + 1)
                if len(value) != info.file_size or len(value) > maximum:
                    raise ValueError("Character bundle member size is invalid")
                files[path] = value
    except (zipfile.BadZipFile, EOFError, RuntimeError) as error:
        raise ValueError("Character bundle is damaged or unsupported") from error
    manifest = thaw_json_value(parse_canonical_json_bytes(files.pop("manifest.json")))
    return _validate(manifest, files)


def write_character_bundle(
    source: object,
    *,
    image_reader: ImageReader,
    target: BinaryIO,
    visuals: object = None,
    original_card: bytes | None = None,
) -> dict[str, Any]:
    """Export a validated source, portable PNG references and no campaign or chat history."""
    from sylvia_gm.application.character_profiles import CharacterProfile

    role: str | None
    bindings: dict[str, Any] | None
    source_profile: SafePayload | None
    if isinstance(source, CharacterProfile):
        data = cast(dict[str, Any], thaw_json_value(source.profile))
        kind, role, bindings, source_profile = "character_profile", None, None, None
    elif isinstance(source, ReusableParticipant):
        if source.authored_profile is None:
            raise ValueError("Open this character in Builder and add its profile before exporting")
        kind, role, source_profile = "character_file", source.role, source.source_profile
        bindings = {
            "ruleset": {
                "id": source.package_id,
                "version": source.package_version,
                "content_sha256": source.package_sha256,
            },
            "campaign_setting": {
                "id": source.campaign_setting_id,
                "version": source.campaign_setting_version,
                "content_sha256": source.campaign_setting_sha256,
            },
        }
        data = {
            "format_id": "sylvia-gm-character-file",
            "format_version": 2,
            "id": source.artifact_id,
            "schema": {"id": source.schema_id, "content_sha256": source.schema_sha256},
            "summary": source.summary,
            "authored_profile": source.authored_profile,
            "participant": thaw_json_value(source.participant),
            "entity_state": thaw_json_value(source.entity_state),
            "setting_data": thaw_json_value(source.setting_data),
            **({"visuals": thaw_json_value(source.visuals)} if source.visuals is not None else {}),
        }
    else:
        raise ValueError("Choose a saved Character File or Universal character profile")
    if visuals is not None:
        visuals = validate_character_visuals(visuals)
    payloads = {}
    bundle_visuals = validate_character_visuals(visuals or data.get("visuals"))
    if bundle_visuals is not None:
        for purpose in _PURPOSES:
            reference = bundle_visuals.get(purpose)
            if reference is None:
                continue
            image = image_reader(reference)
            path = f"assets/characters/{reference['sha256']}.png"
            payloads[path] = image
            bundle_visuals[purpose] = {"path": path, "sha256": reference["sha256"]}
        if data.get("format_id") != "sylvia-gm-character-profile" or data["format_version"] != 1:
            data["visuals"] = bundle_visuals
    payloads["character.json"] = canonical_json_bytes(data)
    if len(payloads["character.json"]) > _JSON_LIMIT:
        raise ValueError("Character data exceeds the 256 KiB limit")
    if original_card is not None:
        payloads["original-card.bin"] = original_card
    manifest = {
        "format_id": "sylvia-gm-character-bundle",
        "format_version": 1,
        "kind": kind,
        "role": role,
        "bindings": bindings,
        "source_profile": thaw_json_value(source_profile),
        "visuals": bundle_visuals if "visuals" not in data else None,
        "source_artifact": {"id": source.artifact_id, "content_sha256": source.content_sha256},
        "files": {
            path: {"sha256": hashlib.sha256(raw).hexdigest(), "byte_count": len(raw)}
            for path, raw in sorted(payloads.items())
        },
    }
    bundle = _validate(manifest, payloads)
    payloads["manifest.json"] = canonical_json_bytes(manifest)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path, raw in sorted(payloads.items()):
            info = zipfile.ZipInfo(path, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, raw)
    encoded = output.getvalue()
    if len(encoded) > CHARACTER_BUNDLE_BYTE_LIMIT:
        raise ValueError("Character bundle must fit within 64 MiB")
    target.write(encoded)
    return {
        **bundle.preview(),
        "status": "exported",
        "byte_count": len(encoded),
        "archive_sha256": hashlib.sha256(encoded).hexdigest(),
    }
