"""Prepared character art is reusable across mechanical editions and retained Plan pins."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sylvia_gm.application.image_hooks import MAX_IMAGE_BYTES, ImageData
from sylvia_gm.domain.image_art import validate_character_visuals
from sylvia_gm.domain.package_library import PackageFile, canonical_json_bytes


def _art_root() -> Path:
    """Keep the shared PNG library beside the frozen owner, outside onefile extraction."""
    frozen = getattr(sys, "_MEIPASS", None)
    root = Path(sys.executable).resolve().parent if isinstance(frozen, str) else (
        Path(__file__).resolve().parents[3]
    )
    return root / "artwork"


def artwork_payload_paths(catalogue_bytes: bytes) -> frozenset[str]:
    """List the exact immutable app resources named by a valid prepared-art catalogue."""
    catalogue = json.loads(catalogue_bytes)
    if (
        not isinstance(catalogue, dict)
        or type(catalogue.get("format")) is not int
        or catalogue["format"] != 1
        or not isinstance(catalogue.get("characters"), list)
    ):
        raise ValueError("The shared artwork catalogue is invalid")
    paths = {"artwork/character-art.json"}
    for character in catalogue["characters"]:
        visuals = validate_character_visuals(character["visuals"])
        if visuals is None or visuals.get("status") == "partial":
            raise ValueError("Prepared artwork requires complete character references")
        for purpose in ("full_body", "reference_sheet", "portrait"):
            reference = visuals[purpose]
            path = reference.get("path")
            if path != f"assets/characters/{reference['sha256']}.png":
                raise ValueError("Shared artwork must use a content-addressed PNG path")
            paths.add("artwork/" + path)
    return frozenset(paths)


def bundled_character_reference(portable_path: str, sha256: str) -> bool:
    """Recognize only exact catalogue references, independently of local PNG availability."""
    if (
        re.fullmatch(r"[0-9a-f]{64}", sha256) is None
        or portable_path != f"assets/characters/{sha256}.png"
    ):
        return False
    try:
        catalogue = (_art_root() / "character-art.json").read_bytes()
        return "artwork/" + portable_path in artwork_payload_paths(catalogue)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def bundled_character_image(portable_path: str, sha256: str) -> bytes | None:
    """Resolve only catalogue-listed first-party PNGs, never arbitrary files or remote assets."""
    if not bundled_character_reference(portable_path, sha256):
        return None
    root = _art_root()
    try:
        path = root / portable_path
        if (
            not path.resolve().is_relative_to(root.resolve())
            or path.stat().st_size > MAX_IMAGE_BYTES
        ):
            return None
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha256:
            return None
        _ = ImageData(raw).dimensions
        return raw
    except (OSError, ValueError, KeyError, TypeError):
        return None


def bundled_source_visuals(
    package_id: str, portable_path: str, source: bytes | PackageFile, *,
    subject_name: str | None = None, campaign_setting_ids: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    """Resolve exact admitted source/subject bindings, including Setting-specific inherited art."""
    if isinstance(source, PackageFile):
        if source.path not in {portable_path, portable_path.partition("#/")[0]}:
            return None
        digest = source.sha256
    else:
        digest = hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest()
    return _source_visuals(
        "package_id", package_id, portable_path, digest, subject_name, campaign_setting_ids,
    )


def plan_artwork_source(
    plan: Mapping[str, object], source_ref: str,
) -> tuple[str, str] | None:
    """Identify one exact narrative passage, preserving art across unrelated interview choices."""
    if not source_ref.startswith("campaign_plan#/"):
        return None
    pointer = source_ref.partition("#")[2]
    if pointer != "/opening_direction" and not pointer.startswith((
        "/structure/", "/side_content/",
    )):
        return None
    value: object = plan
    try:
        for token in pointer[1:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            if isinstance(value, Mapping):
                value = value[token]
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                if re.fullmatch(r"0|[1-9][0-9]*", token) is None:
                    return None
                value = value[int(token)]
            else:
                return None
        if isinstance(value, str) and value.strip():
            return value, hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    except (KeyError, IndexError, ValueError, TypeError):
        pass
    return None


def bundled_plan_visuals(
    plan: Mapping[str, object], source_ref: str, *,
    subject_name: str | None = None, campaign_setting_ids: frozenset[str] = frozenset(),
) -> dict[str, Any] | None:
    """Resolve a declared cast member inside the exact accepted structured Plan."""
    source = plan_artwork_source(plan, source_ref)
    plan_id = plan.get("artwork_source_plan_id", plan.get("id"))
    if source is None or not isinstance(plan_id, str):
        return None
    return _source_visuals(
        "plan_id", plan_id, source_ref, source[1], subject_name, campaign_setting_ids,
    )


def _source_visuals(
    identity_key: str, source_id: str, path: str, digest: str,
    subject_name: str | None, campaign_setting_ids: frozenset[str],
) -> dict[str, Any] | None:
    """Match one source namespace before applying Setting and subject specificity."""
    other_key = "plan_id" if identity_key == "package_id" else "package_id"
    try:
        catalogue = json.loads((_art_root() / "character-art.json").read_bytes())
        candidates: dict[tuple[int, int], dict[int, object]] = {}
        for index, character in enumerate(catalogue["characters"]):
            for reference in character["sources"]:
                if not (
                    isinstance(reference, Mapping)
                    and reference.get(identity_key) == source_id
                    and other_key not in reference
                    and reference.get("path") == path
                    and reference.get("sha256") == digest
                ):
                    continue
                setting = reference.get("campaign_setting_id")
                if setting is not None and (
                    not isinstance(setting, str) or setting not in campaign_setting_ids
                ):
                    continue
                subject = reference.get("subject_name")
                if subject is not None and not (
                    isinstance(subject, str)
                    and isinstance(subject_name, str)
                    and subject_name.strip().casefold() == subject.strip().casefold()
                ):
                    continue
                priority = (int(setting is not None), int(subject is not None))
                candidates.setdefault(priority, {})[index] = character["visuals"]
        if candidates:
            matches = candidates[max(candidates)]
            if len(matches) == 1:
                return validate_character_visuals(next(iter(matches.values())))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None
