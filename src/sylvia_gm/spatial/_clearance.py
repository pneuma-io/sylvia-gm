"""Exact planar clearance for an axis-aligned box or circular moving footprint."""

from __future__ import annotations

Point = tuple[float, float]
_EPSILON = 1e-12


def _segment_enters_box(start: Point, end: Point, lower: Point, upper: Point) -> bool:
    """Detect interior overlap; touching a face without entering it leaves enough clearance."""

    entry, exit_time = 0.0, 1.0
    for axis in range(2):
        delta = end[axis] - start[axis]
        if abs(delta) <= _EPSILON:
            if not lower[axis] + _EPSILON < start[axis] < upper[axis] - _EPSILON:
                return False
            continue
        first = (lower[axis] + _EPSILON - start[axis]) / delta
        second = (upper[axis] - _EPSILON - start[axis]) / delta
        entry = max(entry, min(first, second))
        exit_time = min(exit_time, max(first, second))
        if entry > exit_time:
            return False
    return entry <= exit_time


def _point_segment_distance_squared(point: Point, start: Point, end: Point) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    fraction = (
        0.0
        if length_squared == 0
        else max(
            0.0,
            min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared),
        )
    )
    return (point[0] - start[0] - fraction * dx) ** 2 + (point[1] - start[1] - fraction * dy) ** 2


def swept_footprint_hits_box(
    start: Point,
    end: Point,
    lower: Point,
    upper: Point,
    *,
    half_extents: Point,
    radius: float | None,
) -> bool:
    """Check the complete translation, including rounded corners for a circular footprint."""

    if radius is None:
        return _segment_enters_box(
            start,
            end,
            (lower[0] - half_extents[0], lower[1] - half_extents[1]),
            (upper[0] + half_extents[0], upper[1] + half_extents[1]),
        )
    if _segment_enters_box(start, end, lower, upper):
        return True
    endpoint_distance = min(
        sum(
            max(lower[axis] - point[axis], 0.0, point[axis] - upper[axis]) ** 2 for axis in range(2)
        )
        for point in (start, end)
    )
    corner_distance = min(
        _point_segment_distance_squared((x, y), start, end)
        for x in (lower[0], upper[0])
        for y in (lower[1], upper[1])
    )
    return min(endpoint_distance, corner_distance) < max(0.0, radius - _EPSILON) ** 2
