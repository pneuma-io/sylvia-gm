"""Ruleset-neutral image purposes, framing and agent instructions."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

IMAGE_PURPOSES = frozenset({"portrait", "full_body", "reference_sheet", "map_underlay"})
CHARACTER_IMAGE_PURPOSES = IMAGE_PURPOSES - {"map_underlay"}
IMAGE_MODES = frozenset({"off", "review", "auto"})
IMAGE_SIZES = {
    "portrait": (1024, 1024),
    "full_body": (1024, 1536),
    "reference_sheet": (1536, 1024),
    "map_underlay": (1536, 1024),
}

_ASSET_REFERENCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sha256"],
    "anyOf": [{"required": ["operation_id"]}, {"required": ["path"]}],
    "properties": {
        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "operation_id": {"type": "string", "minLength": 1, "maxLength": 128},
        "path": {"type": "string", "minLength": 1, "maxLength": 512},
    },
}
CHARACTER_VISUALS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["appearance", "full_body", "reference_sheet"],
    "properties": {
        "appearance": {"type": "string", "minLength": 1, "maxLength": 16000},
        "full_body": _ASSET_REFERENCE_SCHEMA,
        "reference_sheet": _ASSET_REFERENCE_SCHEMA,
        "portrait": _ASSET_REFERENCE_SCHEMA,
        "frame": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "x": {"type": "number", "minimum": 0, "maximum": 1},
                "y": {"type": "number", "minimum": 0, "maximum": 1},
                "zoom": {"type": "number", "minimum": 1, "maximum": 4},
            },
        },
    },
}


def validate_character_visuals(value: object) -> dict[str, Any] | None:
    """Retain canonical references with local receipts or hash-verified package asset paths."""
    if value is None:
        return None
    required = {"appearance", "full_body", "reference_sheet"}
    if not isinstance(value, Mapping) or not required <= set(value) <= required | {
        "portrait",
        "frame",
    }:
        raise ValueError("Character visuals require both full-body and multi-view references")
    appearance = value["appearance"]
    if not isinstance(appearance, str) or not 0 < len(appearance) <= 16000 or "\x00" in appearance:
        raise ValueError("Character visuals require an established appearance description")
    result: dict[str, Any] = {"appearance": appearance}
    for purpose in ("full_body", "reference_sheet", "portrait"):
        if purpose not in value:
            continue
        reference = value[purpose]
        if (
            not isinstance(reference, Mapping)
            or not {"sha256"} < set(reference) <= {"sha256", "operation_id", "path"}
            or not isinstance(reference["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", reference["sha256"]) is None
        ):
            raise ValueError("Character visual reference is invalid")
        if "operation_id" in reference and (
            not isinstance(reference["operation_id"], str)
            or not 0 < len(reference["operation_id"]) <= 128
            or "\x00" in reference["operation_id"]
        ):
            raise ValueError("Character visual operation identity is invalid")
        if "path" in reference:
            from sylvia_gm.domain.package_library import validate_portable_path

            path = reference["path"]
            if not isinstance(path, str) or len(path) > 512 or not path.endswith(".png"):
                raise ValueError("Character visual asset must be a package PNG")
            validate_portable_path(path)
        result[purpose] = dict(reference)
    if "frame" in value:
        frame = value["frame"]
        if not isinstance(frame, Mapping) or not set(frame) <= {"x", "y", "zoom"}:
            raise ValueError("Character portrait framing is invalid")
        result["frame"] = PortraitFrame(**frame).as_payload()
    return result


def character_visuals_from_selections(
    rows: Sequence[Mapping[str, Any]], target_id: str
) -> dict[str, Any] | None:
    """Project normalized saved art into the portable Character File visual bundle."""
    selected = {row["purpose"]: row for row in rows if row["target_id"] == target_id}
    if not {"full_body", "reference_sheet"} <= set(selected):
        return None
    missing = missing_character_art(list(selected.values()))
    if "reference_sheet" in missing:
        return None
    appearance = selected["full_body"]["metadata"].get("appearance")
    if not appearance:
        return None
    value = {"appearance": appearance}
    for purpose in ("full_body", "reference_sheet", "portrait"):
        if purpose in selected and purpose not in missing:
            value[purpose] = {
                **{key: selected[purpose][key] for key in ("sha256", "operation_id")},
                "path": f"assets/characters/{selected[purpose]['sha256']}.png",
            }
    if "portrait" in selected and "portrait" not in missing:
        value["frame"] = selected["portrait"]["frame"]
    return validate_character_visuals(value)


def missing_character_art(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Flag derived references when their recorded canonical inputs have changed."""
    selected = {row["purpose"]: row for row in rows}
    missing = []
    for purpose in ("full_body", "reference_sheet", "portrait"):
        if purpose not in selected:
            missing.append(purpose)
            continue
        dependencies = (
            ()
            if purpose == "full_body"
            else (
                ("full_body",) if purpose == "reference_sheet" else ("full_body", "reference_sheet")
            )
        )
        references = selected[purpose]["metadata"].get("references", {})
        if any(
            dependency in missing
            or dependency not in selected
            or (
                dependency in references
                and references[dependency] != selected[dependency]["sha256"]
            )
            for dependency in dependencies
        ):
            missing.append(purpose)
    return missing


@dataclass(frozen=True)
class PortraitFrame:
    """Normalized focal point and zoom shared by every portrait presentation."""

    x: float = 0.5
    y: float = 0.35
    zoom: float = 1.0

    def __post_init__(self) -> None:
        for value in (self.x, self.y, self.zoom):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError("Portrait framing requires finite numbers")
        if not 0 <= self.x <= 1 or not 0 <= self.y <= 1 or not 1 <= self.zoom <= 4:
            raise ValueError("Portrait framing is outside the supported range")

    def as_payload(self) -> dict[str, float]:
        return asdict(self)


PURPOSE_INSTRUCTIONS = {
    "portrait": (
        "Create one square character portrait. For humanoids, frame the head and upper shoulders, "
        "facing the viewer. Keep both eyes level, the face centered horizontally and its eye line "
        "about 35 percent down the image. Leave headroom and room around the shoulders for "
        "a circular crop. For other creatures, frame the recognizable head or defining body "
        "feature at the same focal point; preserve its actual anatomy and never invent a face. "
        "Preserve the identity, outfit, colors and distinguishing features "
        "of the supplied full-body and multi-view references. No border, lettering or UI."
    ),
    "full_body": (
        "Create a canonical full-body character or creature reference, entire silhouette visible, "
        "in a neutral natural pose, plain background, consistent costume and equipment. "
        "Preserve every established appearance detail and supplied character reference. "
        "This image will guide subsequent portraits and edits. No UI, border or lettering."
    ),
    "reference_sheet": (
        "Create one multi-view character reference sheet with front, side and back views of "
        "the same character and a clear detail of its face or defining anatomical feature. "
        "Preserve nonhumanoid anatomy. Keep physique, face, hairstyle, outfit, "
        "equipment and colors identical across all views. Use the supplied canonical full-body "
        "reference. Plain background, coherent scale, no UI or decorative border."
    ),
    "map_underlay": (
        "Create an orthographic, straight-down tactical map underlay using the supplied "
        "map-data reference as the exact layout guide. Preserve map bounds, orientation, "
        "room proportions, wall positions, openings and terrain footprints. Do not add, "
        "remove, rotate or rearrange playable structures. Match the supplied image aspect "
        "ratio and fill the canvas edge to edge. Add environmental materials, lighting and "
        "surface detail inside that layout. Do not draw tokens, characters, labels, grid "
        "lines, measurements, borders, legends or UI. Mechanics and the grid are drawn above "
        "this image by the game; the artwork never defines collision or visibility."
    ),
}

IMAGE_AGENT_INSTRUCTIONS = """Image generation is an optional Sylvia capability. Check image_art
before planning images. Respect its off/review/auto mode and connection status. In review mode,
prepare requests for user approval; never report a proposed image as generated. In auto mode,
recognize missing art during ordinary GM or Builder work and request it yourself, using a stable
operation ID. Do not repeatedly regenerate an existing image or retry a failed or interrupted
generation. Cancellation, refusal and usage limits are final until a new deliberate request.

For each character, preserve a complete appearance description plus BOTH a canonical full-body
image and a multi-view reference sheet. Generate the full-body reference first, the sheet using
that reference second, and the portrait using both references third. Use the saved character
identity and established appearance; do not invent replacements for authored details. Subsequent
edits must include these canonical references. Keep new references associated with the same
character. Save the returned visual references in character data when publishing a Character File.
Portraits share consistent face framing and one saved focal point/zoom across Map, Party, character
and dialogue presentation. An image generation is visual content, never a mechanics mutation.

For map_underlay, select the actual authored map by ID. Sylvia supplies a raster reference rendered
from its geometry and terrain, with the same orientation and bounds as the playable map. Use that
reference, describe the environment and visual style, and preserve its layout. Never substitute a
freehand scene illustration for a map underlay. Do not include tokens, grid or UI in the artwork.
Generated structure is guidance only; saved game geometry remains authoritative. Reuse prepared
art after reopen and when revisiting maps. Do not generate scene illustrations or app-wide ambient
backgrounds in this phase. Preserve selected assets when a request fails or is cancelled.
"""
