"""Bounded map drawings from queries made against the player's known physical scene."""

import math
from collections.abc import Mapping, Sequence
from typing import Any, cast

from sylvia_gm.domain.authority import SafePayload, owned_payload
from sylvia_gm.spatial import SpatialEngine

PREVIEW_NOTICE = "Based on the visible map. The action rechecks current game state."


def project_preflight_path(route: object, cost: object, scene: SafePayload) -> SafePayload:
    """Copy only a qualified public route's drawing fields, never its query provenance.

    The caller must evaluate the selected route and cost on the player-spatial bridge.
    1,024 points cover the bounded circle search's maximum 994 candidate vertices.
    """
    points = route.get("points") if isinstance(route, Mapping) else None
    bounds = scene["map"]["bounds"]
    if (not isinstance(route, Mapping) or route.get("found") is not True
            or route.get("truncated") is not False
            or not isinstance(points, Sequence) or isinstance(points, (str, bytes))
            or not 2 <= len(points) <= 1024
            or type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0
            or any(not isinstance(point, Sequence) or isinstance(point, (str, bytes))
                   or len(point) != 3
                   or any(type(value) not in (int, float) or not math.isfinite(value)
                          or not bounds["min"][axis] <= value <= bounds["max"][axis]
                          for axis, value in enumerate(point)) for point in points)):
        raise ValueError("The selected public route is unavailable")
    return owned_payload({
        "notice": PREVIEW_NOTICE, "map_id": scene["map"]["id"],
        "map_revision": scene["map"]["revision"],
        "path": {"found": True, "truncated": False, "points": points, "cost": cost},
    })


def project_spatial_preview(
    declaration: SafePayload,
    *,
    results: Mapping[str, object],
    queries: Mapping[str, SafePayload],
    scenes: Mapping[str, SafePayload],
) -> SafePayload:
    """Copy only selected drawing fields; private provenance and arbitrary metadata stay out."""

    output: dict[str, object] = {"notice": PREVIEW_NOTICE}
    for operation, declared_name in declaration.items():
        name = cast(str, declared_name)
        # Policy and spatial admission have already validated these closed structures.
        result = cast(Mapping[str, Any], results[name])
        query = cast(Mapping[str, Any], queries[name])
        scene = cast(Mapping[str, Any], scenes[name])
        identity = {"map_id": result["map_id"], "map_revision": result["geometry_revision"]}
        if "map_id" in output and any(output[key] != value for key, value in identity.items()):
            raise ValueError("A map preview cannot combine different maps or revisions")
        output.update(identity)
        if operation == "grid_lane":
            zone = result["zone"]
            output["grid_lane"] = {
                "shape": zone["shape"], "owner": query["owner_id"],
                "lane": query["lane"], "label": zone["metadata"]["player"]["label"],
            }
        elif operation == "path":
            grid = scene["map"]["grid"]
            points = [
                [
                    grid["origin"][0] + (cell[0] + 0.5) * grid["cell_size"],
                    grid["origin"][1] + (cell[1] + 0.5) * grid["cell_size"],
                    scene["map"]["bounds"]["min"][2],
                ]
                for cell in result["cells"]
            ]
            mover = next(
                (item for item in scene["entities"] if item["id"] == query.get("moving_entity_id")),
                None,
            )
            if mover is not None and points:
                points[0] = list(mover["position"])
                for point in points[1:]:
                    point[2] = mover["position"][2]
            output["path"] = {
                key: result[key] for key in ("found", "truncated", "cost", "cells")
            } | {"points": points}
            if len(points) >= 2 and "crossed_zones" not in declaration:
                crossings = cast(
                    Mapping[str, Any],
                    SpatialEngine(scene)
                    .query(
                        {
                            "kind": "crossed_zones",
                            "map_id": result["map_id"],
                            "path": points,
                            "max_crossings": 256,
                        }
                    )
                    .as_payload(),
                )
                output["crossed_zones"] = [
                    {"id": item["zone_id"], "point": item["point"]}
                    for item in crossings["crossings"]
                ]
        elif operation == "ray":
            output["ray"] = {
                "origin": query["origin"],
                "destination": query["destination"],
                "contacts": [
                    {key: item[key] for key in ("kind", "id", "point")}
                    for item in result["intersections"]
                ],
            }
        elif operation == "exposure":
            output["exposure"] = {
                "fraction": result["exposure"],
                "cover_ids": [
                    item["id"] for item in result["occluders"] if item["kind"] == "geometry"
                ],
            }
        elif operation == "crossed_zones":
            output["crossed_zones"] = [
                {"id": item["zone_id"], "point": item["point"]} for item in result["crossings"]
            ]
    return owned_payload(output)
