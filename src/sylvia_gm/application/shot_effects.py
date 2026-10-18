"""Project resolved shot data into a bounded player-visible presentation cue."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import cast

from sylvia_gm.application.tactical_grid import player_known_spatial_scene
from sylvia_gm.spatial import SpatialValidationError, admit_spatial_scene

_REQUEST_FIELDS = frozenset(
    {"source_id", "target_id", "target_kind", "origin", "destination", "delay_ms", "miss",
     "contacts", "style"}
)
_CONTACT_FIELDS = frozenset({"kind", "id", "point", "cue"})
_CONTACT_KINDS = frozenset({"entity", "geometry"})
_CUE_KINDS = frozenset({"character", "cover", "stop", "penetration"})
_STYLE_FIELDS = frozenset({"color", "width", "travel_ms", "impact_ms", "fire_sound", "cues"})
_CUE_FIELDS = frozenset({"color", "sound"})
_SOUND_FIELDS = frozenset({"waveform", "frequency", "end_frequency", "duration_ms", "volume"})
_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")

_DEFAULT_STYLE: dict[str, object] = {
    "color": "#FFFFFF",
    "width": 2,
    "travel_ms": 90,
    "impact_ms": 120,
    "fire_sound": None,
    "cues": {kind: {"color": "#FFFFFF", "sound": None} for kind in sorted(_CUE_KINDS)},
}


def _number(value: object, minimum: float, maximum: float) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    if float(value) < minimum or float(value) > maximum:
        return None
    return value


def _point(value: object) -> list[int | float] | None:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        return None
    if len(value) != 3 or any(_number(item, -math.inf, math.inf) is None for item in value):
        return None
    return [cast(int | float, item) for item in value]


def _inside(point: Sequence[int | float], bounds: Mapping[str, object]) -> bool:
    lower = cast(Sequence[int | float], bounds["min"])
    upper = cast(Sequence[int | float], bounds["max"])
    return all(
        float(lower[index]) <= float(point[index]) <= float(upper[index]) for index in range(3)
    )


def _sound(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping) or set(value) != _SOUND_FIELDS:
        return None
    waveform = value.get("waveform")
    frequency = _number(value.get("frequency"), 40, 12_000)
    end_frequency = _number(value.get("end_frequency"), 40, 12_000)
    duration_ms = _number(value.get("duration_ms"), 10, 500)
    volume = _number(value.get("volume"), 0, 1)
    if (
        not isinstance(waveform, str)
        or waveform not in {"noise", "sine", "triangle"}
        or None
        in {
            frequency,
            end_frequency,
            duration_ms,
            volume,
        }
    ):
        return None
    return {
        "waveform": waveform,
        "frequency": frequency,
        "end_frequency": end_frequency,
        "duration_ms": duration_ms,
        "volume": volume,
    }


def _color(value: object) -> str | None:
    return value.upper() if isinstance(value, str) and _HEX_COLOR.fullmatch(value) else None


def _style(value: object) -> dict[str, object] | None:
    if value is None:
        return deepcopy(_DEFAULT_STYLE)
    if not isinstance(value, Mapping) or set(value) - _STYLE_FIELDS:
        return None
    result = deepcopy(_DEFAULT_STYLE)
    if "color" in value:
        color = _color(value["color"])
        if color is None:
            return None
        result["color"] = color
    for field, minimum, maximum in (
        ("width", 1, 8),
        ("travel_ms", 40, 250),
        ("impact_ms", 60, 400),
    ):
        if field in value:
            number = _number(value[field], minimum, maximum)
            if number is None:
                return None
            result[field] = number
    if "fire_sound" in value:
        if value["fire_sound"] is None:
            result["fire_sound"] = None
        else:
            sound = _sound(value["fire_sound"])
            if sound is None:
                return None
            result["fire_sound"] = sound
    if "cues" in value:
        raw_cues = value["cues"]
        if not isinstance(raw_cues, Mapping) or set(raw_cues) - _CUE_KINDS:
            return None
        cues = cast(dict[str, dict[str, object]], result["cues"])
        for kind, raw_cue in raw_cues.items():
            if not isinstance(raw_cue, Mapping) or set(raw_cue) - _CUE_FIELDS:
                return None
            cue = deepcopy(cues[cast(str, kind)])
            if "color" in raw_cue:
                color = _color(raw_cue["color"])
                if color is None:
                    return None
                cue["color"] = color
            if "sound" in raw_cue:
                if raw_cue["sound"] is None:
                    cue["sound"] = None
                else:
                    sound = _sound(raw_cue["sound"])
                    if sound is None:
                        return None
                    cue["sound"] = sound
            cues[cast(str, kind)] = cue
    return result


def _segment_box_entry(
    origin: Sequence[float], destination: Sequence[float], volume: Mapping[str, object]
) -> float | None:
    lower = cast(Sequence[int | float], volume["min"])
    upper = cast(Sequence[int | float], volume["max"])
    entry, exit_ = 0.0, 1.0
    for axis in range(3):
        delta = destination[axis] - origin[axis]
        if abs(delta) < 1e-12:
            if origin[axis] < float(lower[axis]) or origin[axis] > float(upper[axis]):
                return None
            continue
        first = (float(lower[axis]) - origin[axis]) / delta
        second = (float(upper[axis]) - origin[axis]) / delta
        entry = max(entry, min(first, second))
        exit_ = min(exit_, max(first, second))
        if entry > exit_:
            return None
    return entry if 0.0 <= entry <= 1.0 else None


def _entity_box(entity: Mapping[str, object]) -> dict[str, object]:
    """Return a conservative box used only to keep a tracer out of a visible bystander."""

    position = cast(Sequence[int | float], entity["position"])
    footprint = cast(Mapping[str, object], entity["footprint"])
    if footprint["kind"] == "circle":
        x_extent = y_extent = float(cast(int | float, footprint["radius"]))
    else:
        half = cast(Sequence[int | float], footprint["half_extents"])
        x_extent, y_extent = float(half[0]), float(half[1])
    return {
        "min": [float(position[0]) - x_extent, float(position[1]) - y_extent, float(position[2])],
        "max": [
            float(position[0]) + x_extent,
            float(position[1]) + y_extent,
            float(position[2]) + float(cast(int | float, entity["height"])),
        ],
    }


def _miss_destination(
    origin: Sequence[int | float],
    intended: Sequence[int | float],
    target: Mapping[str, object],
    bounds: Mapping[str, object],
    public_geometry: Sequence[Mapping[str, object]],
    public_entities: Sequence[Mapping[str, object]],
    source_id: str,
    target_id: str,
) -> list[float] | None:
    position = cast(Sequence[int | float], target["position"])
    dx = float(position[0]) - float(origin[0])
    dy = float(position[1]) - float(origin[1])
    center_distance = math.hypot(dx, dy)
    if center_distance < 1e-9:
        return None
    direction = [dx / center_distance, dy / center_distance]
    perpendicular = [-direction[1], direction[0]]
    footprint = cast(Mapping[str, object], target["footprint"])
    if footprint["kind"] == "circle":
        lateral = forward = float(cast(int | float, footprint["radius"]))
    else:
        half = cast(Sequence[int | float], footprint["half_extents"])
        lateral = abs(perpendicular[0]) * float(half[0]) + abs(perpendicular[1]) * float(half[1])
        forward = abs(direction[0]) * float(half[0]) + abs(direction[1]) * float(half[1])
    margin = min(0.15, max(0.05, lateral * 0.2))
    near_distance = center_distance - forward
    if near_distance <= 1e-7:
        return None
    endpoint_distance = center_distance + forward + 0.05
    # The offset is measured at the target's near face so a close shot cannot cut through the body.
    endpoint_lateral = (lateral + margin) * endpoint_distance / near_distance
    candidates: list[list[float]] = []
    for side in (1.0, -1.0):
        candidate = [
            float(origin[0])
            + direction[0] * endpoint_distance
            + perpendicular[0] * endpoint_lateral * side,
            float(origin[1])
            + direction[1] * endpoint_distance
            + perpendicular[1] * endpoint_lateral * side,
            float(intended[2]),
        ]
        if _inside(candidate, bounds):
            candidates.append(candidate)
    obstacles = [
        cast(Mapping[str, object], item["volume"]) for item in public_geometry if item["active"]
    ]
    obstacles.extend(
        _entity_box(item)
        for item in public_entities
        if item["active"] and item["id"] not in {source_id, target_id}
    )
    clipped_fallbacks: list[tuple[float, list[float]]] = []
    for candidate in candidates:
        start = [float(item) for item in origin]
        first_entry: float | None = None
        for volume in obstacles:
            entry = _segment_box_entry(start, candidate, volume)
            if entry is not None:
                first_entry = entry if first_entry is None else min(first_entry, entry)
        if first_entry is None:
            return candidate
        parameter = max(0.0, first_entry - 1e-4)
        endpoint = [
            start[index] + (candidate[index] - start[index]) * parameter for index in range(3)
        ]
        if parameter > 1e-7:
            clipped_fallbacks.append((parameter, endpoint))
    if not clipped_fallbacks:
        return None
    # An obstacle before the target makes the full near-miss impossible. Fade the longest safe
    # segment before that visible obstacle without inventing an impact or changing shot mechanics.
    return max(clipped_fallbacks, key=lambda item: item[0])[1]


def project_shot_effect(
    request: object, *, scene: Mapping[str, object]
) -> dict[str, object] | None:
    """Return a safe cue for one already-resolved shot, or suppress an invalid cue."""

    if not isinstance(request, Mapping):
        return None
    required = _REQUEST_FIELDS - {"style", "target_kind"}
    if set(request) - _REQUEST_FIELDS or not required.issubset(request):
        return None
    try:
        admitted = admit_spatial_scene(scene).as_payload()
        public = player_known_spatial_scene(admitted)
    except (SpatialValidationError, TypeError, ValueError):
        return None
    source_id, target_id = request["source_id"], request["target_id"]
    target_kind = request.get("target_kind", "entity")
    if not isinstance(source_id, str) or not isinstance(target_id, str):
        return None
    if target_kind not in ("entity", "zone"):
        return None
    entities = {
        cast(str, item["id"]): item
        for item in cast(Sequence[Mapping[str, object]], admitted["entities"])
    }
    geometry = {
        cast(str, item["id"]): item
        for item in cast(Sequence[Mapping[str, object]], admitted["geometry"])
    }
    visible = {
        "entity": {item["id"] for item in cast(Sequence[Mapping[str, object]], public["entities"])},
        "geometry": {
            item["id"] for item in cast(Sequence[Mapping[str, object]], public["geometry"])
        },
        "zone": {item["id"] for item in cast(Sequence[Mapping[str, object]], public["zones"])},
    }
    if source_id not in visible["entity"] or target_id not in visible[target_kind]:
        return None
    origin = _point(request["origin"])
    destination = _point(request["destination"])
    map_value = cast(Mapping[str, object], admitted["map"])
    bounds = cast(Mapping[str, object], map_value["bounds"])
    if (
        origin is None
        or destination is None
        or not _inside(origin, bounds)
        or not _inside(destination, bounds)
    ):
        return None
    delay_ms = _number(request["delay_ms"], 0, 60_000)
    miss = request["miss"]
    contacts_value = request["contacts"]
    if "style" in request and request["style"] is None:
        return None
    style = _style(request.get("style"))
    if (
        delay_ms is None
        or not isinstance(miss, bool)
        or isinstance(contacts_value, (str, bytes, bytearray))
        or not isinstance(contacts_value, Sequence)
        or len(contacts_value) > 64
        or style is None
    ):
        return None
    contacts: list[dict[str, object]] = []
    if target_kind == "zone" and (miss or contacts_value):
        return None
    for raw in contacts_value:
        if not isinstance(raw, Mapping) or set(raw) != _CONTACT_FIELDS:
            return None
        kind, item_id, cue = raw["kind"], raw["id"], raw["cue"]
        point = _point(raw["point"])
        lookup = entities if kind == "entity" else geometry if kind == "geometry" else None
        if (
            lookup is None
            or not isinstance(item_id, str)
            or item_id not in lookup
            or item_id not in visible[cast(str, kind)]
            or not isinstance(cue, str)
            or cue not in _CUE_KINDS
            or point is None
            or not _inside(point, bounds)
        ):
            return None
        contacts.append({"point": point, "cue": cue})
    if miss:
        if contacts:
            return None
        endpoint = _miss_destination(
            origin,
            destination,
            entities[target_id],
            bounds,
            cast(Sequence[Mapping[str, object]], public["geometry"]),
            cast(Sequence[Mapping[str, object]], public["entities"]),
            source_id,
            target_id,
        )
        if endpoint is None:
            return None
        destination = endpoint
    elif contacts:
        destination = cast(list[int | float], contacts[-1]["point"])
    return {
        "map_id": map_value["id"],
        "origin": origin,
        "destination": destination,
        "delay_ms": delay_ms,
        "miss": miss,
        "contacts": contacts,
        "style": style,
    }
