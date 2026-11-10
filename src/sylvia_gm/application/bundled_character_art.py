"""Prepared character art is reusable across mechanical editions and retained Plan pins."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sylvia_gm.application.image_hooks import MAX_IMAGE_BYTES, ImageData
from sylvia_gm.domain.image_art import validate_character_visuals


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
    package_id: str, portable_path: str, source: bytes,
) -> dict[str, Any] | None:
    """Reuse an authored identity only for its exact admitted source, never a name match."""
    digest = hashlib.sha256(source.replace(b"\r\n", b"\n")).hexdigest()
    try:
        catalogue = json.loads((_art_root() / "character-art.json").read_bytes())
        for character in catalogue["characters"]:
            if any(
                isinstance(reference, Mapping)
                and reference.get("package_id") == package_id
                and reference.get("path") == portable_path
                and reference.get("sha256") == digest
                for reference in character["sources"]
            ):
                return validate_character_visuals(character["visuals"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None
