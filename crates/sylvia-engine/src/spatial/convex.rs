//! `spatial/_convex.py`: bounded convex intersection (GJK) for query volumes and bodies.

use super::SpatialValidationError;
use crate::pymath::{hypot, sum_floats};

pub(crate) type V3 = [f64; 3];

pub(crate) fn dot(first: V3, second: V3) -> f64 {
    sum_floats((0..3).map(|index| first[index] * second[index]))
}

pub(crate) fn cross(first: V3, second: V3) -> V3 {
    [
        first[1] * second[2] - first[2] * second[1],
        first[2] * second[0] - first[0] * second[2],
        first[0] * second[1] - first[1] * second[0],
    ]
}

/// `itertools.combinations(range(n), count)`, in its order.
fn combinations(n: usize, count: usize) -> Vec<Vec<usize>> {
    let mut out = Vec::new();
    let mut indices: Vec<usize> = (0..count).collect();
    if count > n {
        return out;
    }
    loop {
        out.push(indices.clone());
        let Some(position) = (0..count).rev().find(|&i| indices[i] != i + n - count) else { return out };
        indices[position] += 1;
        for later in position + 1..count {
            indices[later] = indices[later - 1] + 1;
        }
    }
}

/// `_closest_simplex(points)`: project the origin onto every simplex face; keep the nearest.
fn closest_simplex(points: &[V3]) -> (V3, Vec<V3>) {
    let (mut best_point, mut best_face) = (points[0], vec![points[0]]);
    let mut best_squared = dot(best_point, best_point);
    for count in 2..=points.len().min(4) {
        for indices in combinations(points.len(), count) {
            let face: Vec<V3> = indices.iter().map(|&i| points[i]).collect();
            let origin = face[0];
            let edges: Vec<V3> = face[1..].iter().map(|p| [p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]]).collect();
            let coefficients: Vec<f64> = match count {
                2 => {
                    let denominator = dot(edges[0], edges[0]);
                    if denominator == 0.0 {
                        continue;
                    }
                    vec![-dot(origin, edges[0]) / denominator]
                }
                3 => {
                    let normal = cross(edges[0], edges[1]);
                    let denominator = dot(normal, normal);
                    if denominator == 0.0 {
                        continue;
                    }
                    vec![
                        -dot(cross(origin, edges[1]), normal) / denominator,
                        -dot(cross(edges[0], origin), normal) / denominator,
                    ]
                }
                _ => {
                    let denominator = dot(edges[0], cross(edges[1], edges[2]));
                    if denominator == 0.0 {
                        continue;
                    }
                    vec![
                        -dot(origin, cross(edges[1], edges[2])) / denominator,
                        -dot(edges[0], cross(origin, edges[2])) / denominator,
                        -dot(edges[0], cross(edges[1], origin)) / denominator,
                    ]
                }
            };
            let mut weights = vec![1.0 - sum_floats(coefficients.iter().copied())];
            weights.extend(coefficients);
            if weights.iter().any(|weight| *weight < 0.0 || !weight.is_finite()) {
                continue;
            }
            let point: V3 = [0, 1, 2].map(|i| sum_floats(weights.iter().zip(&face).map(|(w, p)| w * p[i])));
            let squared = dot(point, point);
            if squared < best_squared {
                best_point = point;
                best_face = weights.iter().zip(&face).filter(|(w, _)| **w > 0.0).map(|(_, p)| *p).collect();
                best_squared = squared;
            }
        }
    }
    for point in &points[1..] {
        let squared = dot(*point, *point);
        if squared < best_squared {
            best_point = *point;
            best_face = vec![*point];
            best_squared = squared;
        }
    }
    (best_point, best_face)
}

fn negate(vector: V3) -> V3 {
    vector.map(|value| -value)
}

/// `convex_overlap(first, second, tolerance=...)`: GJK separation; an unresolved precision
/// limit is refused rather than guessed.
pub(crate) fn convex_overlap(
    first: impl Fn(V3) -> V3,
    second: impl Fn(V3) -> V3,
    tolerance: f64,
) -> Result<bool, SpatialValidationError> {
    let difference = |direction: V3| {
        let (a, b) = (first(direction), second(negate(direction)));
        [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
    };
    let mut nearest = difference([1.0, 0.0, 0.0]);
    let mut simplex = vec![nearest];
    for _ in 0..320 {
        let distance = hypot(&nearest);
        if distance <= tolerance {
            return Ok(true);
        }
        let candidate = difference(negate(nearest));
        if dot(nearest, candidate) / distance > tolerance {
            return Ok(false);
        }
        if simplex.contains(&candidate) {
            break;
        }
        simplex.push(candidate);
        let (point, face) = closest_simplex(&simplex);
        nearest = point;
        simplex = face;
    }
    Err(SpatialValidationError {
        message: "Area intersection did not resolve within its numerical precision limit",
        code: "spatial_query_invalid",
        path: "$.shape".into(),
    })
}
