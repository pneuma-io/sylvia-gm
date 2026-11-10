"""Explicit wall intent over the existing revision-guarded scene mutation seam."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Never, cast
from uuid import uuid4

from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.spatial._interface import (
    SPATIAL_ALGORITHM_VERSION,
    AdmittedSpatialMutation,
    AdmittedSpatialScene,
    SpatialValidationError,
    _array,
    _enum,
    _identity,
    _scene_sha256,
    admit_spatial_scene,
)

WALL_SELECTION_MESSAGE = (
    "This wall edit cannot be applied to the selection. Select a connected wall, including "
    "all its segments when joining walls. Choose Connect or Keep separate, then retry."
)


def _refuse() -> Never:
    raise SpatialValidationError(
        WALL_SELECTION_MESSAGE, code="spatial_wall_selection_required", path="$.geometry_ids"
    )


def _ends(item: Mapping[str, object]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    volume = cast(Mapping[str, list[float]], item["volume"])
    return tuple(volume["min"][:2]), tuple(volume["max"][:2])


def _touches(first: Mapping[str, object], second: Mapping[str, object]) -> bool:
    a = cast(Mapping[str, list[float]], first["volume"])
    b = cast(Mapping[str, list[float]], second["volume"])
    return bool(set(_ends(first)) & set(_ends(second))) and max(a["min"][2], b["min"][2]) <= min(
        a["max"][2], b["max"][2]
    )


def _components(items: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    """Partition an explicitly declared wall; adjacency never merges independent layers."""
    endpoints: dict[tuple[float, ...], list[int]] = {}
    for index, item in enumerate(items):
        for point in _ends(item):
            endpoints.setdefault(point, []).append(index)
    remaining = set(range(len(items)))
    components = []
    for seed in range(len(items)):
        if seed not in remaining:
            continue
        remaining.remove(seed)
        members, pending = [items[seed]], [seed]
        while pending and remaining:
            current = pending.pop()
            for point in _ends(items[current]):
                for index in endpoints[point]:
                    if index in remaining and _touches(items[current], items[index]):
                        remaining.remove(index)
                        members.append(items[index])
                        pending.append(index)
        components.append(members)
    return components


def _connected(items: list[dict[str, object]]) -> bool:
    return len(_components(items)) == 1


def _overlaps(first: Mapping[str, object], second: Mapping[str, object]) -> bool:
    a = cast(Mapping[str, list[float]], first["volume"])
    b = cast(Mapping[str, list[float]], second["volume"])
    axis = 0 if a["min"][0] == a["max"][0] else 1
    along = 1 - axis
    return (
        a["min"][axis] == a["max"][axis] == b["min"][axis] == b["max"][axis]
        and max(a["min"][along], b["min"][along]) < min(a["max"][along], b["max"][along])
        and max(a["min"][2], b["min"][2]) <= min(a["max"][2], b["max"][2])
    )


def mutate_wall_scene(
    scene: AdmittedSpatialScene, request: Mapping[str, object]
) -> AdmittedSpatialMutation:
    """Allocate identities only for deliberate edits; never infer a join from appearance.

    The caller has checked map identity and revision. All validation and replacement
    happen on an owned copy, before the owner stages one scene write.
    """
    code = "spatial_mutation_invalid"
    operation = _enum(
        request["operation"],
        frozenset({"draw", "extend", "connect", "separate"}),
        code=code,
        path="$.operation",
    )
    ids = [
        _identity(value, code=code, path="$.geometry_ids")
        for value in _array(
            request["geometry_ids"], minimum=0, maximum=256, code=code, path="$.geometry_ids"
        )
    ]
    segments = _array(request["segments"], minimum=0, maximum=256, code=code, path="$.segments")
    if len(ids) != len(set(ids)):
        _refuse()
    if (
        (operation == "draw" and (ids or not segments))
        or (operation == "extend" and (len(ids) != 1 or not segments))
        or (operation == "connect" and (len(ids) < 2 or segments))
        or (operation == "separate" and (not ids or segments))
    ):
        _refuse()
    payload = scene.as_payload()
    geometry = cast(list[dict[str, object]], payload["geometry"])
    by_id = {cast(str, item["id"]): item for item in geometry}
    if any(identity not in by_id for identity in ids):
        _refuse()
    selected = [by_id[identity] for identity in ids]
    if any(cast(Mapping[str, object], item["volume"])["kind"] != "grid_edge" for item in selected):
        _refuse()
    layers = {item["edge_layer_id"] for item in selected if "edge_layer_id" in item}
    if operation == "connect" and any(
        item.get("edge_layer_id") in layers and item["id"] not in ids for item in geometry
    ):
        _refuse()
    occupied = (
        set(by_id)
        | {cast(str, item["id"]) for item in cast(list[dict[str, object]], payload["entities"])}
        | {cast(str, item["id"]) for item in cast(list[dict[str, object]], payload["zones"])}
        | {cast(str, item["edge_layer_id"]) for item in geometry if "edge_layer_id" in item}
    )

    def allocate(prefix: str) -> str:
        identity = prefix + uuid4().hex
        while identity in occupied:
            identity = prefix + uuid4().hex
        occupied.add(identity)
        return identity

    created: list[str] = []
    for segment in segments:
        if not isinstance(segment, Mapping) or set(segment) & {"id", "map_id", "edge_layer_id"}:
            _refuse()
        identity = allocate("edge-")
        created.append(identity)
        geometry.append({**segment, "id": identity, "map_id": scene.map_id})
    # Reuse the complete geometry/content contract for every new segment.
    validated = admit_spatial_scene(payload).as_payload()
    geometry = cast(list[dict[str, object]], validated["geometry"])
    by_id = {cast(str, item["id"]): item for item in geometry}
    new = [by_id[identity] for identity in created]
    if any(cast(Mapping[str, object], item["volume"])["kind"] != "grid_edge" for item in new):
        _refuse()
    selected = [by_id[identity] for identity in ids]
    members = selected + new
    if operation != "separate":
        if not _connected(members):
            _refuse()
        layer = next(iter(layers)) if len(layers) == 1 else allocate("wall-")
        # Extending one chosen segment reuses its whole wall's identity without
        # rewriting the other members. Reject overlapping new pieces in that wall.
        existing = [
            item
            for item in geometry
            if item.get("edge_layer_id") == layer
            and item["id"] not in ids
            and item["id"] not in created
        ]
        if any(
            _overlaps(a, b)
            for index, a in enumerate(members)
            for b in members[index + 1 :] + existing
        ):
            _refuse()
        for item in members:
            item["edge_layer_id"] = layer
    else:
        for item in selected:
            item["edge_layer_id"] = allocate("wall-")
        # Removing selected members can divide their old wall. Keep one component's
        # existing identity and allocate identities only for its disconnected remnants.
        for layer in sorted(cast(set[str], layers)):
            remnants = [item for item in geometry if item.get("edge_layer_id") == layer]
            for component in _components(remnants)[1:]:
                identity = allocate("wall-")
                for item in component:
                    item["edge_layer_id"] = identity
                    members.append(item)
                    ids.append(cast(str, item["id"]))
    cast(dict[str, object], validated["map"])["revision"] = scene.geometry_revision + 1
    advanced = admit_spatial_scene(validated)
    target = {"kind": "geometry", "ids": ids + created}
    result = {
        "kind": "scene_mutation",
        "operation": operation,
        "map_id": scene.map_id,
        "geometry_revision": advanced.geometry_revision,
        "snapshot_id": advanced.snapshot_id,
        "algorithm_version": SPATIAL_ALGORITHM_VERSION,
        "target": target,
        "created_ids": created,
        "before": {
            "geometry_revision": scene.geometry_revision,
            "snapshot_id": scene.snapshot_id,
            "scene_sha256": _scene_sha256(scene),
        },
        "after": {
            "geometry_revision": advanced.geometry_revision,
            "snapshot_id": advanced.snapshot_id,
            "scene_sha256": _scene_sha256(advanced),
            "targets_sha256": hashlib.sha256(canonical_json_bytes(members)).hexdigest(),
        },
    }
    return AdmittedSpatialMutation(owned_payload(result), advanced)
