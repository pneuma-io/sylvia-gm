//! `spatial/_clearance.py`: exact planar clearance for an axis-aligned box or circular moving
//! footprint.

use crate::pymath::{max_first, min_first, sum_floats};

const EPSILON: f64 = 1e-12;

type Point = [f64; 2];

/// `_segment_enters_box(start, end, lower, upper)`: interior overlap; touching a face leaves
/// enough clearance.
fn segment_enters_box(start: Point, end: Point, lower: Point, upper: Point) -> bool {
    let (mut entry, mut exit_time) = (0.0f64, 1.0f64);
    for axis in 0..2 {
        let delta = end[axis] - start[axis];
        if delta.abs() <= EPSILON {
            if !(lower[axis] + EPSILON < start[axis] && start[axis] < upper[axis] - EPSILON) {
                return false;
            }
            continue;
        }
        let first = (lower[axis] + EPSILON - start[axis]) / delta;
        let second = (upper[axis] - EPSILON - start[axis]) / delta;
        entry = max_first([entry, min_first([first, second])]);
        exit_time = min_first([exit_time, max_first([first, second])]);
        if entry > exit_time {
            return false;
        }
    }
    entry <= exit_time
}

/// `_point_segment_distance_squared(point, start, end)`.
fn point_segment_distance_squared(point: Point, start: Point, end: Point) -> f64 {
    let (dx, dy) = (end[0] - start[0], end[1] - start[1]);
    let length_squared = dx * dx + dy * dy;
    let fraction = if length_squared == 0.0 {
        0.0
    } else {
        max_first([
            0.0,
            min_first([1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared]),
        ])
    };
    let x = point[0] - start[0] - fraction * dx;
    let y = point[1] - start[1] - fraction * dy;
    x * x + y * y
}

/// `swept_footprint_hits_box(start, end, lower, upper, half_extents=..., radius=...)`: the
/// complete translation, including rounded corners for a circular footprint.
pub(crate) fn swept_footprint_hits_box(
    start: Point,
    end: Point,
    lower: Point,
    upper: Point,
    half_extents: Point,
    radius: Option<f64>,
) -> bool {
    let Some(radius) = radius else {
        return segment_enters_box(
            start,
            end,
            [lower[0] - half_extents[0], lower[1] - half_extents[1]],
            [upper[0] + half_extents[0], upper[1] + half_extents[1]],
        );
    };
    if segment_enters_box(start, end, lower, upper) {
        return true;
    }
    let endpoint_distance = min_first([start, end].map(|point| {
        sum_floats((0..2).map(|axis| {
            let gap = max_first([lower[axis] - point[axis], 0.0, point[axis] - upper[axis]]);
            gap * gap
        }))
    }));
    let corners = [[lower[0], lower[1]], [lower[0], upper[1]], [upper[0], lower[1]], [upper[0], upper[1]]];
    let corner_distance = min_first(corners.map(|corner| point_segment_distance_squared(corner, start, end)));
    let reach = max_first([0.0, radius - EPSILON]);
    min_first([endpoint_distance, corner_distance]) < reach * reach
}
