"""Locate the first existing box-exposure sample that opens along a continuous path.

Visibility changes only at near-face changes, endpoint/face intersections or
ray/box-edge tangencies. Partition there, rather than stepping through grid cells
or assuming that concealed endpoints imply a concealed segment. Curved bodies
and occluders are explicitly unsupported by this locator; ordinary exposure is
unchanged. The query projects translated poses without mutating its input scene.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from itertools import combinations
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from sylvia_gm.spatial._engine import Vec3, _CompiledScene, _SpatialObject


class _TestLimitError(Exception):
    pass


def _root(constant: float, slope: float, lower: float, upper: float) -> list[float]:
    if slope == 0:
        return []
    value = -constant / slope
    return [value] if lower < value < upper else []


def resolve_exposure_path(scene: _CompiledScene, query: Mapping[str, object]) -> dict[str, object]:
    """Return an earliest verified exposure witness or a truthful unsupported result."""
    from sylvia_gm.spatial._engine import (
        _inside_shape,
        _matches_metadata,
        _point_at,
        _provenance,
        _resolve_exposure,
        _shape_interval,
        _target_samples,
        _vec3,
    )

    observer = _vec3(cast(Mapping[str, object], query["observer"])["point"])
    target_id = cast(str, cast(Mapping[str, object], query["target"])["entity_id"])
    target = next(
        item for item in scene.objects if item.kind == "entity" and item.item_id == target_id
    )
    zone = next(item for item in scene.zones if item.item_id == query["zone_id"])
    path = [_vec3(value) for value in cast(list[object], query["path"])]
    include = cast(list[str], query["include"])
    center = cast(
        "Vec3", tuple((target.shape.lower[i] + target.shape.upper[i]) / 2 for i in range(3))
    )
    envelope_lower = tuple(
        min(observer[i], *(point[i] - center[i] + target.shape.lower[i] for point in path))
        for i in range(3)
    )
    envelope_upper = tuple(
        max(observer[i], *(point[i] - center[i] + target.shape.upper[i] for point in path))
        for i in range(3)
    )
    occluders = [
        item
        for item in scene.objects
        if item.active
        and item.visibility == "opaque"
        and item.item_id != target_id
        and ("geometry" if item.kind == "geometry" else "entities") in include
        and _matches_metadata(item, cast(Mapping[str, object] | None, query.get("metadata_filter")))
        and all(
            item.shape.upper[i] >= envelope_lower[i] and item.shape.lower[i] <= envelope_upper[i]
            for i in range(3)
        )
    ]
    tests = 0

    def result(
        reason: str,
        point: Sequence[float] | None = None,
        distance: float | None = None,
        exposure: float = 0,
    ) -> dict[str, object]:
        return {
            **_provenance(scene, "exposure_path"),
            "supported": reason in {"FOUND", "NO_EXPOSURE"},
            "found": point is not None,
            "point": list(point) if point is not None else None,
            "distance": distance,
            "exposure": exposure,
            "reason": reason,
            "tests": tests,
        }

    if zone.shape.kind == "grid_cells":
        # Each covered cell uses the existing exact box/edge visibility solver.
        # Never run the bounding rectangle as though it were covered ground.
        witnesses = []
        remaining = cast(int, query["max_tests"])
        for part in zone.shape.parts:
            if remaining <= 0:
                return result("TEST_LIMIT")
            cell_scene = replace(
                scene,
                zones=tuple(replace(z, shape=part) if z is zone else z for z in scene.zones),
            )
            witness = resolve_exposure_path(cell_scene, {**query, "max_tests": remaining})
            witness_tests = cast(int, witness["tests"])
            tests += witness_tests
            remaining -= witness_tests
            if not witness["supported"]:
                return result(cast(str, witness["reason"]))
            if witness["found"]:
                witnesses.append(witness)
        if witnesses:
            witness = min(witnesses, key=lambda v: cast(float, v["distance"]))
            return result(
                "FOUND",
                cast(Sequence[float], witness["point"]),
                cast(float, witness["distance"]),
                cast(float, witness["exposure"]),
            )
        return result("NO_EXPOSURE")
    if (
        target.shape.kind != "box"
        or zone.shape.kind != "box"
        or any(item.shape.kind not in ("box", "grid_edge") for item in occluders)
    ):
        return result("UNSUPPORTED_SHAPE")
    samples = cast(Mapping[str, int], query["samples"])
    maximum = cast(int, query["max_tests"])
    exposure_query = {key: query[key] for key in ("observer", "target", "samples", "include")}
    if "metadata_filter" in query:
        exposure_query["metadata_filter"] = query["metadata_filter"]

    def projected(point: Vec3) -> _SpatialObject:
        shift = tuple(point[i] - center[i] for i in range(3))
        shape = replace(
            target.shape,
            lower=cast("Vec3", tuple(target.shape.lower[i] + shift[i] for i in range(3))),
            upper=cast("Vec3", tuple(target.shape.upper[i] + shift[i] for i in range(3))),
        )
        return replace(target, shape=shape)

    def consume_test() -> None:
        """Bound both box-event construction and full visibility witness checks."""
        nonlocal tests
        if tests >= maximum:
            raise _TestLimitError
        tests += 1

    def exposure_at(point: Vec3) -> float:
        consume_test()
        moved = projected(point)
        trial = replace(
            scene, objects=tuple(moved if item is target else item for item in scene.objects)
        )
        return cast(float, _resolve_exposure(trial, exposure_query)["exposure"])

    cumulative = 0.0
    try:
        for start, end in zip(path, path[1:], strict=False):
            delta = cast("Vec3", tuple(end[i] - start[i] for i in range(3)))
            length = math.dist(start, end)
            interval = _shape_interval(zone.shape, start, delta)
            if interval is None or length == 0:
                cumulative += length
                continue
            lower, upper = max(0.0, interval[0]), min(1.0, interval[1])
            if lower > upper:
                cumulative += length
                continue
            # Existing box sample grids switch near faces at these linear bearings.
            bearing = [lower, upper]
            dx, dy = start[0] - observer[0], start[1] - observer[1]
            for constant, slope in (
                (dx, delta[0]),
                (dy, delta[1]),
                (dx - dy, delta[0] - delta[1]),
                (dx + dy, delta[0] + delta[1]),
            ):
                bearing.extend(_root(constant, slope, lower, upper))
            cuts = set(bearing)
            ordered = sorted(cuts)
            for left, right in zip(ordered, ordered[1:], strict=False):
                middle = (left + right) / 2
                pose = projected(_point_at(start, delta, middle))
                for sample in _target_samples(
                    pose.shape, observer, samples["horizontal"], samples["vertical"]
                ):
                    base = tuple(sample[i] - middle * delta[i] - observer[i] for i in range(3))
                    for item in occluders:
                        consume_test()
                        planes = [
                            (axis, face - observer[axis])
                            for axis in range(3)
                            for face in (item.shape.lower[axis], item.shape.upper[axis])
                        ]
                        for axis, plane in planes:
                            cuts.update(_root(base[axis] - plane, delta[axis], left, right))
                        for (a, first), (b, second) in combinations(planes, 2):
                            if a != b:
                                cuts.update(
                                    _root(
                                        first * base[b] - second * base[a],
                                        first * delta[b] - second * delta[a],
                                        left,
                                        right,
                                    )
                                )
                if len(cuts) > 20_000:
                    return result("TEST_LIMIT")
            ordered_cuts = sorted(cuts)
            for index, left in enumerate(ordered_cuts):
                point = _point_at(start, delta, left)
                fraction = exposure_at(point)
                if fraction > 0 and _inside_shape(zone.shape, point):
                    return result("FOUND", point, cumulative + left * length, fraction)
                if index + 1 == len(ordered_cuts):
                    continue
                right = (left + ordered_cuts[index + 1]) / 2
                fraction = exposure_at(_point_at(start, delta, right))
                if fraction <= 0:
                    continue
                # This open interval contains no visibility transition. Refine its
                # first verified witness to 1e-10 map units using the same exposure
                # predicate, including its existing surface-contact tolerance.
                near = left
                for _ in range(64):
                    if (right - near) * length <= 1e-10:
                        break
                    middle = (near + right) / 2
                    observed = exposure_at(_point_at(start, delta, middle))
                    if observed > 0:
                        right, fraction = middle, observed
                    else:
                        near = middle
                point = _point_at(start, delta, right)
                return result("FOUND", point, cumulative + right * length, fraction)
            cumulative += length
    except _TestLimitError:
        return result("TEST_LIMIT")
    return result("NO_EXPOSURE")
