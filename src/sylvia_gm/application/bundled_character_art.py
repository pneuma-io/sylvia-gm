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


def _world_root() -> Path:
    frozen = getattr(sys, "_MEIPASS", None)
    root = Path(frozen) if isinstance(frozen, str) else Path(__file__).resolve().parents[3]
    return root / "campaign-settings" / "severed-lands"


def bundled_character_image(portable_path: str, sha256: str) -> bytes | None:
    """Resolve only catalogue-listed first-party PNGs, never arbitrary files or remote assets."""
    if re.fullmatch(r"assets/characters/[0-9a-f]{64}\.png", portable_path) is None:
        return None
    root = _world_root()
    try:
        catalogue = json.loads((root / "provenance/character-art.json").read_bytes())
        if not any(
            reference.get("path") == portable_path and reference.get("sha256") == sha256
            for character in catalogue["characters"]
            for purpose in ("full_body", "reference_sheet", "portrait")
            if (reference := character["visuals"].get(purpose)) is not None
        ):
            return None
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
        catalogue = json.loads((_world_root() / "provenance/character-art.json").read_bytes())
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
