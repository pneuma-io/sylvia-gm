"""Pure grid-pattern authoring, shared by owner preview and committed scene mutation."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sylvia_gm.spatial._interface import AdmittedSpatialMutation, AdmittedSpatialScene

DIRECTIONS = {"east": (1, 0), "south": (0, 1), "west": (-1, 0), "north": (0, -1)}
LANE_FIELDS = frozenset({"pattern", "direction", "extent", "half_width", "origin", "facing"})
MAX_LANE_CELLS = 4096  # Execution bound, not a weapon range or class allowance.


def grid_lane_zone(scene: Mapping[str, Any], owner_id: str, selection: object) -> dict[str, Any]:
    """Rasterize cell centres, clip to map, and bind the declaration to the current pose.

    Forward steps 1..extent are included; the shooter's cell is the sole step-0 cell.
    Triangle centre inclusion is abs(side)*extent <= half_width*forward (closed boundary).
    Cells are closed volumes for physical crossing, matching existing box-zone boundaries.
    The layer containing the shooter's feet sets the vertical interval, not a new range.
    """
    from sylvia_gm.spatial._interface import SpatialValidationError

    def reject(message):
        raise SpatialValidationError(message, code="spatial_lane_invalid", path="$.lane")

    if isinstance(selection, (list, tuple)) and len(selection) == 1:
        selection = selection[0]
    if not isinstance(selection, Mapping) or set(selection) != LANE_FIELDS:
        reject("Choose a complete grid lane: pattern, direction, extent, width and current pose")
    if (
        not isinstance(selection["pattern"], str)
        or not isinstance(selection["direction"], str)
        or selection["pattern"] not in ("lane", "triangle")
        or selection["direction"] not in DIRECTIONS
    ):
        reject("Unsupported grid lane pattern or direction")
    extent, width = selection["extent"], selection["half_width"]
    if any(type(v) is not int or not 0 <= v <= 2048 for v in (extent, width)) or extent == 0:
        reject("Grid lane dimensions must be bounded whole cells; extent must be positive")
    if 1 + extent * (2 * width + 1) > MAX_LANE_CELLS:
        reject("Grid lane exceeds the supported cell budget")
    owner = next((e for e in scene["entities"] if e["id"] == owner_id), None)
    if owner is None or not owner["active"]:
        reject("Grid lane needs a current active shooter")
    for field, actual in [("origin", owner["position"]), ("facing", owner["facing"])]:
        given = selection[field]
        if (
            not isinstance(given, Sequence)
            or isinstance(given, (str, bytes))
            or any(type(v) not in (float, int) or not math.isfinite(v) for v in given)
            or list(given) != list(actual)
        ):
            reject("Grid lane selection is stale: shooter pose changed")
    board = scene["map"]
    size, origin = board["grid"]["cell_size"], board["grid"]["origin"]
    lower, upper = board["bounds"]["min"], board["bounds"]["max"]
    cell = [math.floor((owner["position"][i] - origin[i]) / size) for i in range(2)]
    layer = next(
        (
            v
            for v in board["layers"]
            if v["min_elevation"] <= owner["position"][2] < v["max_elevation"]
        ),
        None,
    )
    if layer is None:
        reject("Shooter is outside a supported map layer")
    dx, dy = DIRECTIONS[selection["direction"]]
    cells = [cell]
    for forward in range(1, extent + 1):
        for side in range(-width, width + 1):
            if selection["pattern"] == "triangle" and abs(side) * extent > width * forward:
                continue
            x, y = cell[0] + dx * forward - dy * side, cell[1] + dy * forward + dx * side
            if all(
                lower[i] <= origin[i] + c * size and origin[i] + (c + 1) * size <= upper[i]
                for i, c in enumerate((x, y))
            ):
                cells.append([x, y])
    cells = sorted(cells)
    if len(cells) == 1:
        reject("Grid lane has no forward cells inside the map")
    # Hashing keeps arbitrary valid entity identities inside the shared identity length limit.
    identity = "grid-lane-" + hashlib.sha256(owner_id.encode()).hexdigest()[:32]
    return {
        "id": identity,
        "map_id": board["id"],
        "shape": {
            "kind": "grid_cells",
            "cells": cells,
            "origin": list(origin),
            "cell_size": size,
            "min_elevation": layer["min_elevation"],
            "max_elevation": layer["max_elevation"],
        },
        "metadata": {
            "owner": owner_id,
            "lane": dict(selection),
            "display_anchor": owner_id,
            "player": {
                "kind": "overwatch",
                "label": f"{owner_id}: {selection['direction']} {selection['pattern']}, "
                f"{extent} forward / {2 * width + 1} cells wide",
            },
        },
    }


def mutate_grid_lane(
    scene: AdmittedSpatialScene, request: Mapping[str, Any]
) -> AdmittedSpatialMutation:
    """Upsert one owned declaration; existing authored zones are left byte-for-byte intact."""
    from sylvia_gm.domain.authority import owned_payload
    from sylvia_gm.domain.package_library import canonical_json_bytes
    from sylvia_gm.spatial._interface import (
        SPATIAL_ALGORITHM_VERSION,
        AdmittedSpatialMutation,
        admit_spatial_scene,
    )

    payload = scene.as_payload()
    zone = grid_lane_zone(payload, request["owner_id"], request["lane"])
    existing = next((z for z in payload["zones"] if z["id"] == zone["id"]), None)
    if existing is not None and existing["metadata"].get("owner") != request["owner_id"]:
        from sylvia_gm.spatial._interface import SpatialValidationError

        raise SpatialValidationError(
            "Lane identity belongs to another author",
            code="spatial_lane_invalid",
            path="$.owner_id",
        )
    payload["zones"] = [z for z in payload["zones"] if z["id"] != zone["id"]] + [zone]
    payload["map"]["revision"] += 1
    advanced = admit_spatial_scene(payload)

    def digest(value):
        return hashlib.sha256(canonical_json_bytes(value)).hexdigest()

    return AdmittedSpatialMutation(
        owned_payload(
            {
                "kind": "scene_mutation",
                "map_id": scene.map_id,
                "algorithm_version": SPATIAL_ALGORITHM_VERSION,
                "target": {"kind": "zone", "id": zone["id"]},
                "before": {
                    "geometry_revision": scene.geometry_revision,
                    "snapshot_id": scene.snapshot_id,
                    "scene_sha256": digest(scene.as_payload()),
                    "target_sha256": digest(existing),
                },
                "after": {
                    "geometry_revision": advanced.geometry_revision,
                    "snapshot_id": advanced.snapshot_id,
                    "scene_sha256": digest(advanced.as_payload()),
                    "target_sha256": digest(zone),
                },
                "geometry_revision": advanced.geometry_revision,
                "snapshot_id": advanced.snapshot_id,
                "zone": zone,
            }
        ),
        advanced,
    )
