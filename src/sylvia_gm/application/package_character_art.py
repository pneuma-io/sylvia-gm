"""Keep Character File images inside the same immutable package as their visual references."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from sylvia_gm.application.character_profiles import _parse
from sylvia_gm.application.image_hooks import ImageData, _declare_images
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.image_art import validate_character_visuals
from sylvia_gm.domain.package_library import canonical_json_bytes

ImageAssetReader = Callable[[Mapping[str, Any]], bytes]


def include_character_art(
    draft: NormalizedPackageDraft,
    read_asset: ImageAssetReader | None = None,
) -> NormalizedPackageDraft:
    """Resolve only explicit character references; never generate images or read arbitrary files."""
    payloads = dict(draft.payloads)
    assets = set()
    for path, data in draft.payloads.items():
        if not path.endswith(".json") or len(data) > 256 * 1024:
            continue
        try:
            character = json.loads(data)
        except (ValueError, UnicodeError):
            continue  # The ordinary content validator owns non-character JSON diagnostics.
        if (
            not isinstance(character, dict)
            or character.get("format_id")
            not in {
                "sylvia-gm-character-file",
                "sylvia-gm-character-profile",
            }
            or "visuals" not in character
        ):
            continue
        _parse(data)  # Do not normalize duplicate members or nonfinite source values away.
        visuals = validate_character_visuals(character["visuals"])
        if visuals is None:
            continue
        for purpose in ("full_body", "reference_sheet", "portrait"):
            reference = visuals.get(purpose)
            if reference is None:
                continue
            asset_path = reference.get("path", f"assets/characters/{reference['sha256']}.png")
            if any(
                member.casefold() == asset_path.casefold() and member != asset_path
                for member in payloads
            ):
                raise ValueError("Character artwork has conflicting package filenames")
            image = payloads.get(asset_path)
            if image is None:
                if read_asset is None:
                    raise ValueError(
                        "Character artwork is missing; include the referenced PNG assets"
                    )
                image = read_asset(reference)
            _ = ImageData(image).dimensions
            if hashlib.sha256(image).hexdigest() != reference["sha256"]:
                raise ValueError("Character artwork does not match its saved identity")
            payloads[asset_path] = image
            assets.add(asset_path)
            reference["path"] = asset_path
        if visuals != character["visuals"]:
            payloads[path] = canonical_json_bytes({**character, "visuals": visuals})
    if not assets:
        return draft
    _declare_images(draft, payloads, character_assets=frozenset(assets))
    return replace(draft, payloads=payloads)
