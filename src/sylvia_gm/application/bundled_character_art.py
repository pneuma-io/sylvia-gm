"""Prepared character art is reusable across mechanical editions and retained Plan pins."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from sylvia_gm.application.image_hooks import MAX_IMAGE_BYTES, ImageData


def bundled_character_image(portable_path: str, sha256: str) -> bytes | None:
    """Resolve only catalogue-listed first-party PNGs, never arbitrary files or remote assets."""
    if re.fullmatch(r"assets/characters/[0-9a-f]{64}\.png", portable_path) is None:
        return None
    frozen = getattr(sys, "_MEIPASS", None)
    root = (Path(frozen) if isinstance(frozen, str) else Path(__file__).resolve().parents[3])
    root = root / "campaign-settings" / "severed-lands"
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
