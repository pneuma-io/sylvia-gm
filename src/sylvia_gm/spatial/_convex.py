"""Bounded convex intersection for query volumes and occupied bodies."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from itertools import combinations
from typing import cast

from sylvia_gm.spatial._interface import SpatialValidationError

Vec3 = tuple[float, float, float]
Support = Callable[[Vec3], Vec3]


def _dot(first: Vec3, second: Vec3) -> float:
    return sum(first[index] * second[index] for index in range(3))


def _cross(first: Vec3, second: Vec3) -> Vec3:
    return (
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    )


def _closest_simplex(points: Sequence[Vec3]) -> tuple[Vec3, list[Vec3]]:
    """Project the origin onto every simplex face, retaining its nearest face."""
    best_point, best_face = points[0], [points[0]]
    best_squared = _dot(best_point, best_point)
    for count in range(2, min(len(points), 4) + 1):
        for face in combinations(points, count):
            origin = face[0]
            edges = [cast(Vec3, tuple(p[i] - origin[i] for i in range(3))) for p in face[1:]]
            if count == 2:
                denominator = _dot(edges[0], edges[0])
                if denominator == 0:
                    continue
                coefficients = [-_dot(origin, edges[0]) / denominator]
            elif count == 3:
                normal = _cross(edges[0], edges[1])
                denominator = _dot(normal, normal)
                if denominator == 0:
                    continue
                coefficients = [
                    -_dot(_cross(origin, edges[1]), normal) / denominator,
                    -_dot(_cross(edges[0], origin), normal) / denominator,
                ]
            else:
                denominator = _dot(edges[0], _cross(edges[1], edges[2]))
                if denominator == 0:
                    continue
                coefficients = [
                    -_dot(origin, _cross(edges[1], edges[2])) / denominator,
                    -_dot(edges[0], _cross(origin, edges[2])) / denominator,
                    -_dot(edges[0], _cross(edges[1], origin)) / denominator,
                ]
            weights = [1 - sum(coefficients), *coefficients]
            if any(weight < 0 or not math.isfinite(weight) for weight in weights):
                continue
            point = cast(
                Vec3,
                tuple(sum(w * p[i] for w, p in zip(weights, face, strict=True)) for i in range(3)),
            )
            squared = _dot(point, point)
            if squared < best_squared:
                best_point = point
                best_face = [p for weight, p in zip(weights, face, strict=True) if weight > 0]
                best_squared = squared
    for point in points[1:]:
        squared = _dot(point, point)
        if squared < best_squared:
            best_point, best_face, best_squared = point, [point], squared
    return best_point, best_face


def convex_overlap(first: Support, second: Support, *, tolerance: float) -> bool:
    """Use GJK separation; report unresolved precision rather than guessing a hit."""

    def difference(direction: Vec3) -> Vec3:
        first_point = first(direction)
        second_point = second(cast(Vec3, tuple(-value for value in direction)))
        return cast(Vec3, tuple(first_point[i] - second_point[i] for i in range(3)))

    nearest = difference((1.0, 0.0, 0.0))
    simplex = [nearest]
    for _ in range(320):
        distance = math.hypot(*nearest)
        if distance <= tolerance:
            return True
        candidate = difference(cast(Vec3, tuple(-value for value in nearest)))
        if _dot(nearest, candidate) / distance > tolerance:
            return False
        if candidate in simplex:
            break
        nearest, simplex = _closest_simplex([*simplex, candidate])
    raise SpatialValidationError(
        "Area intersection did not resolve within its numerical precision limit",
        code="spatial_query_invalid",
        path="$.shape",
    )
