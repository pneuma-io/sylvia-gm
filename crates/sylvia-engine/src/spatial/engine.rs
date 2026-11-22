//! `spatial/_engine.py`: the float geometry behind every query except placement.
//!
//! Each resolver takes an admitted query's payload and returns Python's result payload, in its
//! key order. Floats follow Python exactly: `sum()` is CPython's compensated float sum,
//! `math.hypot`/`math.dist`/`round` are `crate::pymath`, and `math.cos`/`sin`/`atan2` are the
//! platform C library's, so a few results are recorded per platform.

use super::clearance::swept_footprint_hits_box;
use super::convex::{convex_overlap, V3};
use super::{AdmittedSpatialScene, SpatialValidationError, SPATIAL_ALGORITHM_VERSION};
use crate::pymath::{
    atan2, cos, dist, float_mod, hypot, max_first, min_first, remainder, round_digits, sin, sum_floats,
};
use serde_json::json;
use std::cmp::Reverse;
use std::collections::{BTreeMap, BinaryHeap, HashMap, HashSet};
use sylvia_base::{Json, JsonMap};

pub(crate) const EPSILON: f64 = 1e-12;
const SQRT_TWO: f64 = std::f64::consts::SQRT_2;

type V2 = [f64; 2];
type Cell = (i64, i64);
type Resolved = Result<JsonMap, SpatialValidationError>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ShapeKind {
    Box,
    Cylinder,
    Sphere,
    Cone,
    Prism,
}

/// `_Shape`.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Shape {
    pub kind: ShapeKind,
    pub lower: V3,
    pub upper: V3,
    pub center: Option<V3>,
    pub radius: Option<f64>,
    pub axis: Option<V3>,
    pub length: Option<f64>,
    pub basis: Option<[V3; 3]>,
    pub half_extents: Option<V3>,
}

impl Shape {
    fn bounds(kind: ShapeKind, lower: V3, upper: V3) -> Self {
        Self {
            kind,
            lower,
            upper,
            center: None,
            radius: None,
            axis: None,
            length: None,
            basis: None,
            half_extents: None,
        }
    }
    fn center(&self) -> V3 {
        self.center.expect("a centred shape")
    }
    fn radius(&self) -> f64 {
        self.radius.expect("a round shape")
    }
}

/// `_SpatialObject`.
#[derive(Debug, Clone)]
pub(crate) struct SpatialObject {
    pub kind: &'static str,
    pub id: String,
    pub shape: Shape,
    pub active: bool,
    pub movement: String,
    pub visibility: String,
    pub metadata: Json,
    pub occupies_cells: bool,
}

/// `_CompiledScene`.
#[derive(Debug, Clone)]
pub(crate) struct CompiledScene {
    pub admitted: AdmittedSpatialScene,
    pub lower: V3,
    pub upper: V3,
    pub origin: V2,
    pub cell_size: f64,
    pub cell_counts: [i64; 2],
    pub objects: Vec<SpatialObject>,
    pub zones: Vec<SpatialObject>,
    pub terrain: HashMap<Cell, f64>,
    pub terrain_costs: Vec<f64>,
}

/// `float(x)` of an admitted number.
fn num(value: &Json) -> f64 {
    value.as_f64().expect("an admitted number")
}

/// `_vec3(value)`.
pub(crate) fn vec3(value: &Json) -> V3 {
    let items = value.as_array().expect("an admitted coordinate");
    [num(&items[0]), num(&items[1]), num(&items[2])]
}

/// `_clean(value)`: twelve decimals, and nothing smaller than the engine tolerance.
pub(crate) fn clean(value: f64) -> f64 {
    let rounded = round_digits(value, 12);
    if rounded.abs() < EPSILON {
        0.0
    } else {
        rounded
    }
}

fn clean_point(point: &[f64]) -> Json {
    json!(point.iter().map(|value| clean(*value)).collect::<Vec<f64>>())
}

fn kind_of(value: &Json) -> &str {
    value["kind"].as_str().unwrap_or_default()
}

/// `_shape_from_payload(value)`.
pub(crate) fn shape_from_payload(value: &Json) -> Shape {
    match kind_of(value) {
        "box" => Shape::bounds(ShapeKind::Box, vec3(&value["min"]), vec3(&value["max"])),
        "prism" => {
            let (origin, direction, up) =
                (vec3(&value["origin"]), vec3(&value["direction"]), vec3(&value["up"]));
            let magnitude = hypot(&direction);
            let axis = direction.map(|component| component / magnitude);
            let side = [
                up[1] * axis[2] - up[2] * axis[1],
                up[2] * axis[0] - up[0] * axis[2],
                up[0] * axis[1] - up[1] * axis[0],
            ];
            let side_norm = hypot(&side);
            let width_axis = side.map(|component| component / side_norm);
            let height_axis = [
                axis[1] * width_axis[2] - axis[2] * width_axis[1],
                axis[2] * width_axis[0] - axis[0] * width_axis[2],
                axis[0] * width_axis[1] - axis[1] * width_axis[0],
            ];
            let height_norm = hypot(&height_axis);
            let height_axis = height_axis.map(|component| component / height_norm);
            let basis = [axis, width_axis, height_axis];
            let half_extents = ["length", "width", "height"].map(|field| num(&value[field]) * 0.5);
            let center = [0, 1, 2].map(|i| origin[i] + axis[i] * half_extents[0]);
            let extent = [0, 1, 2].map(|i| sum_floats((0..3).map(|j| basis[j][i].abs() * half_extents[j])));
            let lower = [0, 1, 2].map(|i| center[i] - extent[i]);
            let upper = [0, 1, 2].map(|i| center[i] + extent[i]);
            Shape {
                center: Some(center),
                basis: Some(basis),
                half_extents: Some(half_extents),
                ..Shape::bounds(ShapeKind::Prism, lower, upper)
            }
        }
        "cone" => {
            let (apex, direction) = (vec3(&value["apex"]), vec3(&value["direction"]));
            let magnitude = hypot(&direction);
            let axis = direction.map(|component| component / magnitude);
            let (length, radius) = (num(&value["length"]), num(&value["radius"]));
            let base = [0, 1, 2].map(|i| apex[i] + axis[i] * length);
            let extents = [0, 1, 2].map(|i| radius * max_first([0.0, 1.0 - axis[i] * axis[i]]).sqrt());
            let lower = [0, 1, 2].map(|i| min_first([apex[i], base[i] - extents[i]]));
            let upper = [0, 1, 2].map(|i| max_first([apex[i], base[i] + extents[i]]));
            Shape {
                center: Some(apex),
                radius: Some(radius),
                axis: Some(axis),
                length: Some(length),
                ..Shape::bounds(ShapeKind::Cone, lower, upper)
            }
        }
        kind => {
            let center = vec3(&value["center"]);
            let radius = num(&value["radius"]);
            if kind == "sphere" {
                return Shape {
                    center: Some(center),
                    radius: Some(radius),
                    ..Shape::bounds(ShapeKind::Sphere, center.map(|c| c - radius), center.map(|c| c + radius))
                };
            }
            let height = num(&value["height"]);
            Shape {
                center: Some(center),
                radius: Some(radius),
                ..Shape::bounds(
                    ShapeKind::Cylinder,
                    [center[0] - radius, center[1] - radius, center[2]],
                    [center[0] + radius, center[1] + radius, center[2] + height],
                )
            }
        }
    }
}

/// `_entity_shape(value)`.
fn entity_shape(value: &Json) -> Shape {
    let position = vec3(&value["position"]);
    let height = num(&value["height"]);
    let footprint = &value["footprint"];
    if kind_of(footprint) == "box" {
        let extents = &footprint["half_extents"];
        let (half_x, half_y) = (num(&extents[0]), num(&extents[1]));
        return Shape::bounds(
            ShapeKind::Box,
            [position[0] - half_x, position[1] - half_y, position[2]],
            [position[0] + half_x, position[1] + half_y, position[2] + height],
        );
    }
    let radius = num(&footprint["radius"]);
    Shape {
        center: Some(position),
        radius: Some(radius),
        ..Shape::bounds(
            ShapeKind::Cylinder,
            [position[0] - radius, position[1] - radius, position[2]],
            [position[0] + radius, position[1] + radius, position[2] + height],
        )
    }
}

fn object(kind: &'static str, value: &Json, shape: Shape) -> SpatialObject {
    SpatialObject {
        kind,
        id: value["id"].as_str().expect("an admitted identity").to_owned(),
        shape,
        active: value.get("active").map_or(true, |flag| flag == &json!(true)),
        movement: value.get("movement").and_then(Json::as_str).unwrap_or("passable").to_owned(),
        visibility: value.get("visibility").and_then(Json::as_str).unwrap_or("clear").to_owned(),
        metadata: value["metadata"].clone(),
        occupies_cells: value.get("occupies_cells").map_or(true, |flag| flag == &json!(true)),
    }
}

/// `_compile(scene)`.
pub(crate) fn compile(scene: &AdmittedSpatialScene) -> CompiledScene {
    let payload = scene.payload();
    let map = &payload["map"];
    let (lower, upper) = (vec3(&map["bounds"]["min"]), vec3(&map["bounds"]["max"]));
    let origin = [num(&map["grid"]["origin"][0]), num(&map["grid"]["origin"][1])];
    let cell_size = num(&map["grid"]["cell_size"]);
    let cell_counts = [0, 1].map(|i| ((upper[i] - lower[i]) / cell_size).ceil() as i64);
    let mut objects = Vec::new();
    for value in payload["geometry"].as_array().expect("admitted geometry") {
        objects.push(object("geometry", value, shape_from_payload(&value["volume"])));
    }
    for value in payload["entities"].as_array().expect("admitted entities") {
        let mut entity = object("entity", value, entity_shape(value));
        entity.occupies_cells = true;
        objects.push(entity);
    }
    let zones = payload["zones"]
        .as_array()
        .expect("admitted zones")
        .iter()
        .map(|value| {
            let mut zone = object("zone", value, shape_from_payload(&value["shape"]));
            zone.active = true;
            zone.movement = "passable".into();
            zone.visibility = "clear".into();
            zone
        })
        .collect();
    let mut terrain = HashMap::new();
    let mut terrain_costs = Vec::new();
    for value in payload["terrain"].as_array().expect("admitted terrain") {
        let cell = (value["cell"][0].as_i64().unwrap(), value["cell"][1].as_i64().unwrap());
        terrain.insert(cell, num(&value["cost"]));
        terrain_costs.push(num(&value["cost"]));
    }
    CompiledScene {
        admitted: scene.clone(),
        lower,
        upper,
        origin,
        cell_size,
        cell_counts,
        objects,
        zones,
        terrain,
        terrain_costs,
    }
}

/// `math.floor(x)` as an integer.
fn floor(x: f64) -> i64 {
    x.floor() as i64
}

/// `math.ceil(x)` as an integer.
fn ceil(x: f64) -> i64 {
    x.ceil() as i64
}

/// `_cell_for(scene, point)`.
fn cell_for(scene: &CompiledScene, point: V3) -> Cell {
    (
        (scene.cell_counts[0] - 1).min(floor((point[0] - scene.origin[0]) / scene.cell_size)),
        (scene.cell_counts[1] - 1).min(floor((point[1] - scene.origin[1]) / scene.cell_size)),
    )
}

/// `_in_cells(scene, cell)`.
fn in_cells(scene: &CompiledScene, cell: Cell) -> bool {
    0 <= cell.0 && cell.0 < scene.cell_counts[0] && 0 <= cell.1 && cell.1 < scene.cell_counts[1]
}

/// `_supercover(scene, origin, destination)`: cells the segment touches, tied corners included.
fn supercover(scene: &CompiledScene, origin: V3, destination: V3) -> Json {
    let mut current = cell_for(scene, origin);
    let mut retained = vec![current];
    let mut seen = HashSet::from([current]);
    let (delta_x, delta_y) = (destination[0] - origin[0], destination[1] - origin[1]);
    let sign = |delta: f64| {
        if delta > 0.0 {
            1
        } else if delta < 0.0 {
            -1
        } else {
            0
        }
    };
    let (step_x, step_y) = (sign(delta_x), sign(delta_y));
    let start = |step: i64, index: i64, axis: usize, delta: f64| {
        if step == 0 {
            return (f64::INFINITY, f64::INFINITY);
        }
        let boundary = scene.origin[axis] + (index + i64::from(step > 0)) as f64 * scene.cell_size;
        ((boundary - origin[axis]) / delta, scene.cell_size / delta.abs())
    };
    let (mut maximum_x, increment_x) = start(step_x, current.0, 0, delta_x);
    let (mut maximum_y, increment_y) = start(step_y, current.1, 1, delta_y);
    let mut retain = |cell: Cell, retained: &mut Vec<Cell>| {
        if in_cells(scene, cell) && seen.insert(cell) {
            retained.push(cell);
        }
    };
    while min_first([maximum_x, maximum_y]) <= 1.0 + EPSILON {
        if maximum_x + EPSILON < maximum_y {
            current = (current.0 + step_x, current.1);
            maximum_x += increment_x;
            retain(current, &mut retained);
        } else if maximum_y + EPSILON < maximum_x {
            current = (current.0, current.1 + step_y);
            maximum_y += increment_y;
            retain(current, &mut retained);
        } else {
            let diagonal = (current.0 + step_x, current.1 + step_y);
            retain((current.0 + step_x, current.1), &mut retained);
            retain((current.0, current.1 + step_y), &mut retained);
            retain(diagonal, &mut retained);
            current = diagonal;
            maximum_x += increment_x;
            maximum_y += increment_y;
        }
    }
    json!(retained.iter().map(|cell| [cell.0, cell.1]).collect::<Vec<_>>())
}

/// `_point_at(origin, delta, parameter)`.
fn point_at(origin: V3, delta: V3, parameter: f64) -> V3 {
    [0, 1, 2].map(|i| origin[i] + delta[i] * parameter)
}

type Interval = Option<(f64, f64, V3)>;

/// `_box_interval(shape, origin, delta)`.
fn box_interval(shape: &Shape, origin: V3, delta: V3) -> Interval {
    let (mut entry, mut exit_value) = (f64::NEG_INFINITY, f64::INFINITY);
    let mut entry_normal = [0.0; 3];
    let mut inside = true;
    for axis in 0..3 {
        let coordinate = origin[axis];
        if coordinate < shape.lower[axis] - EPSILON || coordinate > shape.upper[axis] + EPSILON {
            inside = false;
        }
        let direction = delta[axis];
        if direction.abs() <= EPSILON {
            if coordinate < shape.lower[axis] || coordinate > shape.upper[axis] {
                return None;
            }
            continue;
        }
        let mut first = (shape.lower[axis] - coordinate) / direction;
        let mut second = (shape.upper[axis] - coordinate) / direction;
        let mut first_normal = [0.0; 3];
        first_normal[axis] = if direction > 0.0 { -1.0 } else { 1.0 };
        if first > second {
            std::mem::swap(&mut first, &mut second);
        }
        if first > entry {
            entry = first;
            entry_normal = first_normal;
        }
        exit_value = min_first([exit_value, second]);
        if entry - EPSILON > exit_value {
            return None;
        }
    }
    if exit_value < -EPSILON || entry > 1.0 + EPSILON {
        return None;
    }
    if inside {
        return Some((0.0, min_first([1.0, exit_value]), [0.0; 3]));
    }
    Some((max_first([0.0, entry]), min_first([1.0, exit_value]), entry_normal))
}

/// `_interval_intersection(first, second)`.
fn interval_intersection(first: (f64, f64), second: (f64, f64)) -> Option<(f64, f64)> {
    let (lower, upper) = (max_first([first.0, second.0]), min_first([first.1, second.1]));
    if lower - EPSILON > upper {
        None
    } else {
        Some((lower, upper))
    }
}

/// `_cylinder_interval(shape, origin, delta)`.
fn cylinder_interval(shape: &Shape, origin: V3, delta: V3) -> Interval {
    let (center, radius) = (shape.center(), shape.radius());
    let (offset_x, offset_y) = (origin[0] - center[0], origin[1] - center[1]);
    let coefficient = delta[0] * delta[0] + delta[1] * delta[1];
    let inside_horizontal = offset_x * offset_x + offset_y * offset_y <= radius * radius + EPSILON;
    let horizontal = if coefficient <= EPSILON {
        if !inside_horizontal {
            return None;
        }
        (f64::NEG_INFINITY, f64::INFINITY)
    } else {
        let linear = 2.0 * (offset_x * delta[0] + offset_y * delta[1]);
        let constant = offset_x * offset_x + offset_y * offset_y - radius * radius;
        let discriminant = linear * linear - 4.0 * coefficient * constant;
        if discriminant < -EPSILON {
            return None;
        }
        let root = max_first([0.0, discriminant]).sqrt();
        ((-linear - root) / (2.0 * coefficient), (-linear + root) / (2.0 * coefficient))
    };
    let vertical = if delta[2].abs() <= EPSILON {
        if origin[2] < shape.lower[2] || origin[2] > shape.upper[2] {
            return None;
        }
        (f64::NEG_INFINITY, f64::INFINITY)
    } else {
        let values = [(shape.lower[2] - origin[2]) / delta[2], (shape.upper[2] - origin[2]) / delta[2]];
        (min_first(values), max_first(values))
    };
    let interval = interval_intersection(horizontal, vertical)?;
    if interval.1 < -EPSILON || interval.0 > 1.0 + EPSILON {
        return None;
    }
    let inside = inside_horizontal && shape.lower[2] <= origin[2] && origin[2] <= shape.upper[2];
    let parameter = if inside { 0.0 } else { max_first([0.0, interval.0]) };
    let normal = if inside {
        [0.0; 3]
    } else {
        let point = point_at(origin, delta, parameter);
        if (point[2] - shape.lower[2]).abs() <= 1e-9 {
            [0.0, 0.0, -1.0]
        } else if (point[2] - shape.upper[2]).abs() <= 1e-9 {
            [0.0, 0.0, 1.0]
        } else {
            let magnitude = hypot(&[point[0] - center[0], point[1] - center[1]]);
            [(point[0] - center[0]) / magnitude, (point[1] - center[1]) / magnitude, 0.0]
        }
    };
    Some((parameter, min_first([1.0, interval.1]), normal))
}

/// `_shape_interval(shape, origin, delta)`.
fn shape_interval(shape: &Shape, origin: V3, delta: V3) -> Interval {
    if shape.kind == ShapeKind::Box {
        box_interval(shape, origin, delta)
    } else {
        cylinder_interval(shape, origin, delta)
    }
}

/// `_RayHit`.
#[derive(Debug, Clone)]
struct RayHit {
    kind: &'static str,
    id: String,
    distance: f64,
    parameter: f64,
    point: V3,
    normal: V3,
    metadata: Json,
}

fn length_of(delta: V3) -> f64 {
    sum_floats(delta.iter().map(|value| value * value)).sqrt()
}

fn plural(kind: &str) -> &'static str {
    match kind {
        "geometry" => "geometry",
        "entity" => "entities",
        _ => "zones",
    }
}

/// `_ray_hits(scene, origin, destination, include)`.
fn ray_hits(scene: &CompiledScene, origin: V3, destination: V3, include: &[&str]) -> Vec<RayHit> {
    let delta = [0, 1, 2].map(|i| destination[i] - origin[i]);
    let length = length_of(delta);
    let mut hits = Vec::new();
    for item in &scene.objects {
        if !item.active || !include.contains(&plural(item.kind)) {
            continue;
        }
        let Some((parameter, _exit, normal)) = shape_interval(&item.shape, origin, delta) else { continue };
        hits.push(RayHit {
            kind: item.kind,
            id: item.id.clone(),
            distance: parameter * length,
            parameter,
            point: point_at(origin, delta, parameter),
            normal,
            metadata: item.metadata.clone(),
        });
    }
    hits.sort_by(|a, b| {
        clean(a.parameter)
            .partial_cmp(&clean(b.parameter))
            .expect("finite parameters")
            .then_with(|| (a.kind != "geometry").cmp(&(b.kind != "geometry")))
            .then_with(|| a.id.cmp(&b.id))
    });
    hits
}

/// `_hit_payload(hit)`.
fn hit_payload(hit: &RayHit) -> Json {
    json!({
        "kind": hit.kind, "id": hit.id, "distance": clean(hit.distance), "t": clean(hit.parameter),
        "point": clean_point(&hit.point), "normal": clean_point(&hit.normal), "metadata": hit.metadata,
    })
}

/// `_provenance(scene, kind)`.
pub(crate) fn provenance(scene: &CompiledScene, kind: &str) -> JsonMap {
    let Json::Object(map) = json!({
        "kind": kind, "map_id": scene.admitted.map_id, "snapshot_id": scene.admitted.snapshot_id,
        "geometry_revision": scene.admitted.geometry_revision, "algorithm_version": SPATIAL_ALGORITHM_VERSION,
    }) else {
        unreachable!("an object literal")
    };
    map
}

pub(crate) fn with(mut map: JsonMap, entries: Json) -> JsonMap {
    if let Json::Object(entries) = entries {
        for (key, value) in entries {
            map.insert(key, value);
        }
    }
    map
}

fn strings(value: &Json) -> Vec<&str> {
    value.as_array().map(|items| items.iter().filter_map(Json::as_str).collect()).unwrap_or_default()
}

fn int(value: &Json) -> i64 {
    value.as_i64().expect("an admitted integer")
}

/// `_resolve_ray(scene, query)`.
fn resolve_ray(scene: &CompiledScene, query: &JsonMap) -> JsonMap {
    let (origin, destination) = (vec3(&query["origin"]), vec3(&query["destination"]));
    let maximum = int(&query["max_intersections"]) as usize;
    let hits = ray_hits(scene, origin, destination, &strings(&query["include"]));
    with(
        provenance(scene, "ray"),
        json!({
            "cells": supercover(scene, origin, destination),
            "intersections": hits.iter().take(maximum).map(hit_payload).collect::<Vec<_>>(),
            "truncated": hits.len() > maximum,
        }),
    )
}

/// `_target_samples(shape, observer, horizontal_count, vertical_count)`.
fn target_samples(shape: &Shape, observer: V3, horizontal_count: i64, vertical_count: i64) -> Vec<V3> {
    let center_x = (shape.lower[0] + shape.upper[0]) / 2.0;
    let center_y = (shape.lower[1] + shape.upper[1]) / 2.0;
    let (delta_x, delta_y) = (center_x - observer[0], center_y - observer[1]);
    let mut horizontal: Vec<V2> = Vec::new();
    let count = horizontal_count as f64;
    if shape.kind == ShapeKind::Cylinder {
        let radius = shape.radius();
        let distance = hypot(&[delta_x, delta_y]);
        let (normal_x, normal_y) =
            if distance > 0.0 { (delta_x / distance, delta_y / distance) } else { (1.0, 0.0) };
        let mut half_width = radius;
        if distance > radius {
            let ratio = radius / distance;
            half_width *= max_first([0.0, 1.0 - ratio * ratio]).sqrt();
        }
        for index in 0..horizontal_count {
            let lateral = half_width * (2.0 * (index as f64 + 0.5) / count - 1.0);
            let near = max_first([0.0, radius * radius - lateral * lateral]).sqrt();
            horizontal.push([
                center_x - near * normal_x - lateral * normal_y,
                center_y - near * normal_y + lateral * normal_x,
            ]);
        }
    } else {
        for index in 0..horizontal_count {
            let fraction = (index as f64 + 0.5) / count;
            if delta_x.abs() >= delta_y.abs() {
                let x = if delta_x >= 0.0 { shape.lower[0] } else { shape.upper[0] };
                horizontal.push([x, shape.lower[1] + (shape.upper[1] - shape.lower[1]) * fraction]);
            } else {
                let y = if delta_y >= 0.0 { shape.lower[1] } else { shape.upper[1] };
                horizontal.push([shape.lower[0] + (shape.upper[0] - shape.lower[0]) * fraction, y]);
            }
        }
    }
    let mut samples = Vec::new();
    for vertical in 0..vertical_count {
        for point in &horizontal {
            let height = (vertical as f64 + 0.5) / vertical_count as f64;
            samples.push([point[0], point[1], shape.lower[2] + (shape.upper[2] - shape.lower[2]) * height]);
        }
    }
    samples
}

/// `_crosses_shape_interior(shape, origin, destination)`.
fn crosses_shape_interior(shape: &Shape, origin: V3, destination: V3) -> bool {
    let delta = [0, 1, 2].map(|axis| destination[axis] - origin[axis]);
    let Some(interval) = shape_interval(shape, origin, delta) else { return false };
    if interval.1 - interval.0 <= EPSILON {
        return false;
    }
    let midpoint = point_at(origin, delta, (interval.0 + interval.1) / 2.0);
    if shape.kind == ShapeKind::Box {
        return (0..3).all(|axis| {
            shape.lower[axis] + EPSILON < midpoint[axis] && midpoint[axis] < shape.upper[axis] - EPSILON
        });
    }
    let (center, radius) = (shape.center(), shape.radius());
    shape.lower[2] + EPSILON < midpoint[2]
        && midpoint[2] < shape.upper[2] - EPSILON
        && hypot(&[midpoint[0] - center[0], midpoint[1] - center[1]]) < radius - EPSILON
}

/// `_matches_metadata(item, selector)`.
pub(crate) fn matches_metadata(item: &SpatialObject, selector: Option<&Json>) -> bool {
    let Some(selector) = selector else { return true };
    let key = selector["key"].as_str().unwrap_or_default();
    if key.is_empty() {
        return true;
    }
    match item.metadata.get(key).filter(|value| !value.is_null()) {
        Some(actual) => actual == &selector["value"],
        None => selector["missing_matches"] == json!(true),
    }
}

fn find<'s>(scene: &'s CompiledScene, kind: &str, id: &str) -> Option<&'s SpatialObject> {
    scene.objects.iter().find(|item| item.kind == kind && item.id == id)
}

/// `_resolve_exposure(scene, query)`.
fn resolve_exposure(scene: &CompiledScene, query: &JsonMap) -> JsonMap {
    let observer = vec3(&query["observer"]["point"]);
    let target_id = query["target"]["entity_id"].as_str().expect("an admitted target");
    let target = find(scene, "entity", target_id).expect("an admitted target entity");
    let samples = target_samples(
        &target.shape,
        observer,
        int(&query["samples"]["horizontal"]),
        int(&query["samples"]["vertical"]),
    );
    let include = strings(&query["include"]);
    let selector = query.get("metadata_filter");
    let mut sample_results = Vec::new();
    let mut references: BTreeMap<(String, String), Json> = BTreeMap::new();
    let mut visible_count = 0i64;
    for sample in &samples {
        let occluding: Vec<RayHit> = ray_hits(scene, observer, *sample, &include)
            .into_iter()
            .filter(|hit| {
                let item = find(scene, hit.kind, &hit.id).expect("a hit object");
                hit.id != target_id
                    && hit.parameter < 1.0 - EPSILON
                    && item.visibility == "opaque"
                    && matches_metadata(item, selector)
                    && crosses_shape_interior(&item.shape, observer, *sample)
            })
            .take(1)
            .collect();
        let visible = occluding.is_empty();
        visible_count += i64::from(visible);
        for hit in &occluding {
            references.insert(
                (hit.kind.to_owned(), hit.id.clone()),
                json!({"kind": hit.kind, "id": hit.id, "metadata": hit.metadata}),
            );
        }
        sample_results.push(json!({
            "point": clean_point(sample), "visible": visible,
            "occluders": occluding.iter().map(hit_payload).collect::<Vec<_>>(),
        }));
    }
    with(
        provenance(scene, "exposure"),
        json!({
            "samples": sample_results, "visible_samples": visible_count,
            "exposure": clean(visible_count as f64 / samples.len() as f64),
            "occluders": references.into_values().collect::<Vec<_>>(),
        }),
    )
}

/// `_cell_span(scene, shape)`: inclusive ranges, possibly empty.
fn cell_span(scene: &CompiledScene, shape: &Shape) -> ((i64, i64), (i64, i64)) {
    let minimum_x = 0.max(floor((shape.lower[0] - scene.origin[0]) / scene.cell_size));
    let minimum_y = 0.max(floor((shape.lower[1] - scene.origin[1]) / scene.cell_size));
    let maximum_x =
        (scene.cell_counts[0] - 1).min(ceil((shape.upper[0] - scene.origin[0]) / scene.cell_size) - 1);
    let maximum_y =
        (scene.cell_counts[1] - 1).min(ceil((shape.upper[1] - scene.origin[1]) / scene.cell_size) - 1);
    ((minimum_x, maximum_x), (minimum_y, maximum_y))
}

fn span_cells(span: ((i64, i64), (i64, i64))) -> impl Iterator<Item = Cell> {
    let ((x0, x1), (y0, y1)) = span;
    (y0..=y1).flat_map(move |y| (x0..=x1).map(move |x| (x, y)))
}

/// `_blocked_cells(scene, include_entities, moving_entity_id, metadata_filter)`.
fn blocked_cells(
    scene: &CompiledScene,
    include_entities: bool,
    moving: Option<&str>,
    selector: Option<&Json>,
) -> HashSet<Cell> {
    let mut blocked = HashSet::new();
    for item in &scene.objects {
        if !item.active
            || item.movement != "blocking"
            || !item.occupies_cells
            || (item.kind == "entity" && !include_entities)
            || (item.kind == "entity" && Some(item.id.as_str()) == moving)
            || !matches_metadata(item, selector)
        {
            continue;
        }
        blocked.extend(span_cells(cell_span(scene, &item.shape)));
    }
    blocked
}

/// `_path_clearance(...)`'s closure: barriers indexed once, the mover's footprint swept per step.
struct Clearance<'s> {
    scene: &'s CompiledScene,
    blocked: &'s HashSet<Cell>,
    half: V2,
    radius: Option<f64>,
    start_point: Option<V2>,
    start: Cell,
    edge_cells: HashMap<Cell, Vec<usize>>,
    edge_shapes: Vec<Shape>,
}

impl<'s> Clearance<'s> {
    fn new(
        scene: &'s CompiledScene,
        blocked: &'s HashSet<Cell>,
        moving: Option<&SpatialObject>,
        start: Cell,
        selector: Option<&Json>,
    ) -> Self {
        let (half, radius, start_point) = match moving {
            None => ([0.0, 0.0], None, None),
            Some(moving) => {
                let shape = &moving.shape;
                (
                    [0, 1].map(|axis| (shape.upper[axis] - shape.lower[axis]) / 2.0),
                    if shape.kind == ShapeKind::Cylinder { shape.radius } else { None },
                    Some([0, 1].map(|axis| (shape.upper[axis] + shape.lower[axis]) / 2.0)),
                )
            }
        };
        let mut edge_cells: HashMap<Cell, Vec<usize>> = HashMap::new();
        let mut edge_shapes = Vec::new();
        for item in &scene.objects {
            if item.kind != "geometry"
                || item.occupies_cells
                || !item.active
                || item.movement != "blocking"
                || !matches_metadata(item, selector)
            {
                continue;
            }
            if let Some(moving) = moving {
                if item.shape.upper[2] <= moving.shape.lower[2] + EPSILON
                    || item.shape.lower[2] >= moving.shape.upper[2] - EPSILON
                {
                    continue;
                }
            }
            edge_shapes.push(item.shape.clone());
            let index = edge_shapes.len() - 1;
            for cell in span_cells(cell_span(scene, &item.shape)) {
                edge_cells.entry(cell).or_default().push(index);
            }
        }
        Self { scene, blocked, half, radius, start_point, start, edge_cells, edge_shapes }
    }

    fn point(&self, cell: Cell) -> V2 {
        if cell == self.start {
            if let Some(point) = self.start_point {
                return point;
            }
        }
        let scene = self.scene;
        [
            scene.origin[0] + (cell.0 as f64 + 0.5) * scene.cell_size,
            scene.origin[1] + (cell.1 as f64 + 0.5) * scene.cell_size,
        ]
    }

    fn clear(&self, first: Cell, second: Cell) -> bool {
        let scene = self.scene;
        let (origin, destination) = (self.point(first), self.point(second));
        let half = self.half;
        for value in [origin, destination] {
            for axis in 0..2 {
                if value[axis] - half[axis] < scene.lower[axis] - EPSILON
                    || value[axis] + half[axis] > scene.upper[axis] + EPSILON
                {
                    return false;
                }
            }
        }
        let swept = Shape::bounds(
            ShapeKind::Box,
            [
                min_first([origin[0], destination[0]]) - half[0],
                min_first([origin[1], destination[1]]) - half[1],
                scene.lower[2],
            ],
            [
                max_first([origin[0], destination[0]]) + half[0],
                max_first([origin[1], destination[1]]) + half[1],
                scene.upper[2],
            ],
        );
        let mut edges: Vec<usize> = Vec::new();
        for (x, y) in span_cells(cell_span(scene, &swept)) {
            if self.blocked.contains(&(x, y)) {
                let lower = [
                    scene.origin[0] + x as f64 * scene.cell_size,
                    scene.origin[1] + y as f64 * scene.cell_size,
                ];
                let upper = [lower[0] + scene.cell_size, lower[1] + scene.cell_size];
                if swept_footprint_hits_box(origin, destination, lower, upper, half, self.radius) {
                    return false;
                }
            }
            if let Some(found) = self.edge_cells.get(&(x, y)) {
                edges.extend(found);
            }
        }
        !edges.iter().any(|&index| {
            let shape = &self.edge_shapes[index];
            swept_footprint_hits_box(
                origin,
                destination,
                [shape.lower[0], shape.lower[1]],
                [shape.upper[0], shape.upper[1]],
                half,
                self.radius,
            )
        })
    }
}

/// `_heuristic(first, second, diagonal, minimum_cost)`.
fn heuristic(first: Cell, second: Cell, diagonal: bool, minimum_cost: f64) -> f64 {
    let (delta_x, delta_y) = ((first.0 - second.0).abs(), (first.1 - second.1).abs());
    if !diagonal {
        return (delta_x + delta_y) as f64 * minimum_cost;
    }
    let shared = delta_x.min(delta_y) as f64;
    (shared * SQRT_TWO + delta_x.max(delta_y) as f64 - shared) * minimum_cost
}

/// An `f64` heap key (no NaN reaches the frontier).
#[derive(Debug, Clone, Copy, PartialEq)]
struct Key(f64);

impl Eq for Key {}

impl PartialOrd for Key {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Key {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.0.partial_cmp(&other.0).expect("no NaN on the frontier")
    }
}

fn path_not_found(scene: &CompiledScene, expanded: i64, truncated: bool) -> JsonMap {
    with(
        provenance(scene, "path"),
        json!({"found": false, "cells": [], "cost": null, "expanded": expanded, "truncated": truncated}),
    )
}

/// `_resolve_path(scene, query)`.
fn resolve_path(scene: &CompiledScene, query: &JsonMap) -> Resolved {
    let start = (int(&query["start"][0]), int(&query["start"][1]));
    let goal = (int(&query["goal"][0]), int(&query["goal"][1]));
    let allow_diagonal = query["allow_diagonal"] == json!(true);
    let forbid_cutting = query["corner_policy"] == json!("forbid_cutting");
    let moving_id = query.get("moving_entity_id").and_then(Json::as_str);
    let mut moving = None;
    if let Some(id) = moving_id {
        let Some(entity) =
            scene.objects.iter().find(|item| item.kind == "entity" && item.id == id && item.active)
        else {
            return Err(SpatialValidationError {
                message: "Moving entity is unavailable",
                code: "spatial_query_invalid",
                path: "$.moving_entity_id".into(),
            });
        };
        if !span_cells(cell_span(scene, &entity.shape)).any(|cell| cell == start) {
            return Err(SpatialValidationError {
                message: "Path start is outside the moving entity footprint",
                code: "spatial_query_invalid",
                path: "$.start".into(),
            });
        }
        moving = Some(entity);
    }
    let selector = query.get("metadata_filter");
    let blocked = blocked_cells(scene, query["include_entities"] == json!(true), moving_id, selector);
    let clearance = Clearance::new(scene, &blocked, moving, start, selector);
    if blocked.contains(&start)
        || blocked.contains(&goal)
        || !clearance.clear(start, start)
        || !clearance.clear(goal, goal)
    {
        return Ok(path_not_found(scene, 0, false));
    }
    let mut directions: Vec<(i64, i64, f64)> = vec![(-1, 0, 1.0), (0, -1, 1.0), (1, 0, 1.0), (0, 1, 1.0)];
    if allow_diagonal {
        directions.extend([(-1, -1, SQRT_TWO), (1, -1, SQRT_TWO), (-1, 1, SQRT_TWO), (1, 1, SQRT_TWO)]);
    }
    let minimum_cost = min_first(std::iter::once(1.0).chain(scene.terrain_costs.iter().copied()));
    let mut costs: HashMap<Cell, f64> = HashMap::from([(start, 0.0)]);
    let mut parents: HashMap<Cell, Cell> = HashMap::new();
    let mut frontier = BinaryHeap::new();
    frontier.push(Reverse((
        Key(heuristic(start, goal, allow_diagonal, minimum_cost)),
        Key(0.0),
        start.1,
        start.0,
    )));
    let mut expanded = 0i64;
    let maximum = int(&query["max_steps"]);
    let mut found = false;
    while expanded < maximum {
        let Some(Reverse((_priority, Key(cost), y, x))) = frontier.pop() else { break };
        let current = (x, y);
        if cost > costs.get(&current).copied().unwrap_or(f64::INFINITY) + EPSILON {
            continue;
        }
        expanded += 1;
        if current == goal {
            found = true;
            break;
        }
        for &(delta_x, delta_y, step_cost) in &directions {
            let neighbor = (current.0 + delta_x, current.1 + delta_y);
            if !in_cells(scene, neighbor) || blocked.contains(&neighbor) {
                continue;
            }
            if !clearance.clear(current, neighbor) {
                continue;
            }
            if delta_x != 0
                && delta_y != 0
                && forbid_cutting
                && (blocked.contains(&(current.0 + delta_x, current.1))
                    || blocked.contains(&(current.0, current.1 + delta_y)))
            {
                continue;
            }
            let candidate = cost + step_cost * scene.terrain.get(&neighbor).copied().unwrap_or(1.0);
            if candidate + EPSILON >= costs.get(&neighbor).copied().unwrap_or(f64::INFINITY) {
                continue;
            }
            costs.insert(neighbor, candidate);
            parents.insert(neighbor, current);
            let priority = candidate + heuristic(neighbor, goal, allow_diagonal, minimum_cost);
            frontier.push(Reverse((Key(priority), Key(candidate), neighbor.1, neighbor.0)));
        }
    }
    if !found {
        let truncated = !frontier.is_empty() && expanded >= maximum;
        return Ok(path_not_found(scene, expanded, truncated));
    }
    let mut retained = vec![goal];
    while *retained.last().unwrap() != start {
        retained.push(parents[retained.last().unwrap()]);
    }
    retained.reverse();
    Ok(with(
        provenance(scene, "path"),
        json!({
            "found": true, "cells": retained.iter().map(|cell| [cell.0, cell.1]).collect::<Vec<_>>(),
            "cost": clean(costs[&goal]), "expanded": expanded, "truncated": false,
        }),
    ))
}

/// `_inside_shape(shape, point)`.
fn inside_shape(shape: &Shape, point: V3) -> bool {
    if !(0..3).all(|i| shape.lower[i] - EPSILON <= point[i] && point[i] <= shape.upper[i] + EPSILON) {
        return false;
    }
    if shape.kind == ShapeKind::Box {
        return true;
    }
    let (center, radius) = (shape.center(), shape.radius());
    let (dx, dy) = (point[0] - center[0], point[1] - center[1]);
    dx * dx + dy * dy <= radius * radius + EPSILON
}

/// `_resolve_zones(scene, query)`.
fn resolve_zones(scene: &CompiledScene, query: &JsonMap) -> JsonMap {
    let path: Vec<V3> = query["path"].as_array().expect("an admitted path").iter().map(vec3).collect();
    let mut candidates: Vec<(f64, String, Json)> = Vec::new();
    for zone in &scene.zones {
        let mut cumulative = 0.0;
        for index in 0..path.len() - 1 {
            let (start, end) = (path[index], path[index + 1]);
            let delta = [0, 1, 2].map(|i| end[i] - start[i]);
            let length = length_of(delta);
            if let Some(interval) = shape_interval(&zone.shape, start, delta) {
                let parameter = if index == 0 && inside_shape(&zone.shape, start) { 0.0 } else { interval.0 };
                if parameter >= -EPSILON {
                    let along = max_first([0.0, parameter]);
                    let distance = cumulative + along * length;
                    let point = point_at(start, delta, along);
                    candidates.push((
                        distance,
                        zone.id.clone(),
                        json!({
                            "zone_id": zone.id, "segment_index": index, "t": clean(along),
                            "point": clean_point(&point), "distance": clean(distance), "metadata": zone.metadata,
                        }),
                    ));
                    break;
                }
            }
            cumulative += length;
        }
    }
    candidates
        .sort_by(|a, b| clean(a.0).partial_cmp(&clean(b.0)).expect("finite").then_with(|| a.1.cmp(&b.1)));
    let maximum = int(&query["max_crossings"]) as usize;
    with(
        provenance(scene, "crossed_zones"),
        json!({
            "crossings": candidates.iter().take(maximum).map(|candidate| candidate.2.clone()).collect::<Vec<_>>(),
            "truncated": candidates.len() > maximum,
        }),
    )
}

/// `_box_overlap(first, second)`.
fn box_overlap(first: &Shape, second: &Shape) -> bool {
    (0..3).all(|i| first.lower[i] <= second.upper[i] + EPSILON && second.lower[i] <= first.upper[i] + EPSILON)
}

/// `_cylinder_box_overlap(cylinder, box)`.
fn cylinder_box_overlap(cylinder: &Shape, body: &Shape) -> bool {
    if cylinder.upper[2] < body.lower[2] || body.upper[2] < cylinder.lower[2] {
        return false;
    }
    let center = cylinder.center();
    let closest_x = min_first([max_first([center[0], body.lower[0]]), body.upper[0]]);
    let closest_y = min_first([max_first([center[1], body.lower[1]]), body.upper[1]]);
    let radius = cylinder.radius();
    let (dx, dy) = (center[0] - closest_x, center[1] - closest_y);
    dx * dx + dy * dy <= radius * radius + EPSILON
}

/// `_sphere_overlap(sphere, body)`.
fn sphere_overlap(sphere: &Shape, body: &Shape) -> bool {
    let center = sphere.center();
    let vertical = max_first([body.lower[2] - center[2], center[2] - body.upper[2], 0.0]);
    let distance = if body.kind == ShapeKind::Box {
        let gaps = [0, 1].map(|i| max_first([body.lower[i] - center[i], center[i] - body.upper[i], 0.0]));
        hypot(&[gaps[0], gaps[1], vertical])
    } else {
        let body_center = body.center();
        let horizontal = max_first([
            hypot(&[center[0] - body_center[0], center[1] - body_center[1]]) - body.radius(),
            0.0,
        ]);
        hypot(&[horizontal, vertical])
    };
    distance <= sphere.radius() + EPSILON
}

/// The body side of a support pair, in the frame `(point - frame_origin) / scale`.
fn body_support(body: &Shape, frame_origin: V3, scale: f64, lower: V3, upper: V3, direction: V3) -> V3 {
    if body.kind == ShapeKind::Box {
        return [0, 1, 2].map(|i| if direction[i] >= 0.0 { upper[i] } else { lower[i] });
    }
    let center = body.center();
    let magnitude = hypot(&[direction[0], direction[1]]);
    let body_radius = body.radius() / scale;
    let along = |i: usize| if magnitude != 0.0 { body_radius * direction[i] / magnitude } else { 0.0 };
    [
        (center[0] - frame_origin[0]) / scale + along(0),
        (center[1] - frame_origin[1]) / scale + along(1),
        if direction[2] >= 0.0 { upper[2] } else { lower[2] },
    ]
}

/// `_cone_overlap(cone, body)`.
fn cone_overlap(cone: &Shape, body: &Shape) -> Result<bool, SpatialValidationError> {
    if !box_overlap(cone, body) {
        return Ok(false);
    }
    let (apex, axis) = (cone.center(), cone.axis.expect("a cone axis"));
    let (length, radius) = (cone.length.expect("a cone length"), cone.radius());
    let scale = max_first([
        length,
        radius,
        body.upper[0] - body.lower[0],
        body.upper[1] - body.lower[1],
        body.upper[2] - body.lower[2],
        1.0,
    ]);
    let lower = [0, 1, 2].map(|i| (body.lower[i] - apex[i]) / scale);
    let upper = [0, 1, 2].map(|i| (body.upper[i] - apex[i]) / scale);
    let base = axis.map(|component| component * length / scale);
    let cone_support = |direction: V3| {
        let axial = sum_floats((0..3).map(|i| direction[i] * axis[i]));
        let radial = [0, 1, 2].map(|i| direction[i] - axial * axis[i]);
        let magnitude = hypot(&radial);
        let point = [0, 1, 2]
            .map(|i| base[i] + if magnitude != 0.0 { radius / scale * radial[i] / magnitude } else { 0.0 });
        if sum_floats((0..3).map(|i| point[i] * direction[i])) > 0.0 {
            point
        } else {
            [0.0; 3]
        }
    };
    convex_overlap(
        cone_support,
        |direction| body_support(body, apex, scale, lower, upper, direction),
        max_first([1e-12, 1e-9 / scale]),
    )
}

/// `_prism_overlap(prism, body)`.
fn prism_overlap(prism: &Shape, body: &Shape) -> Result<bool, SpatialValidationError> {
    if !box_overlap(prism, body) {
        return Ok(false);
    }
    let center = prism.center();
    let basis = prism.basis.expect("a prism basis");
    let extents = prism.half_extents.expect("prism extents");
    if body.kind == ShapeKind::Cylinder {
        if let Some(vertical) = basis.iter().position(|axis| axis[0] == 0.0 && axis[1] == 0.0) {
            let body_center = body.center();
            let squared = sum_floats((0..3).filter(|j| *j != vertical).map(|j| {
                let projected = sum_floats((0..2).map(|i| (body_center[i] - center[i]) * basis[j][i]));
                let gap = max_first([projected.abs() - extents[j], 0.0]);
                gap * gap
            }));
            let radius = body.radius();
            return Ok(squared <= radius * radius + EPSILON);
        }
    }
    let scale = max_first([
        extents[0],
        extents[1],
        extents[2],
        body.upper[0] - body.lower[0],
        body.upper[1] - body.lower[1],
        body.upper[2] - body.lower[2],
        1.0,
    ]);
    let lower = [0, 1, 2].map(|i| (body.lower[i] - center[i]) / scale);
    let upper = [0, 1, 2].map(|i| (body.upper[i] - center[i]) / scale);
    let prism_support = |direction: V3| {
        let signed = [0, 1, 2].map(|j| {
            let sign = if sum_floats((0..3).map(|i| direction[i] * basis[j][i])) >= 0.0 { 1.0 } else { -1.0 };
            extents[j] / scale * sign
        });
        [0, 1, 2].map(|i| sum_floats((0..3).map(|j| signed[j] * basis[j][i])))
    };
    convex_overlap(
        prism_support,
        |direction| body_support(body, center, scale, lower, upper, direction),
        max_first([1e-12, 1e-9 / scale]),
    )
}

/// `_overlap(first, second)`.
fn overlap(first: &Shape, second: &Shape) -> Result<bool, SpatialValidationError> {
    Ok(match (first.kind, second.kind) {
        (ShapeKind::Sphere, _) => sphere_overlap(first, second),
        (ShapeKind::Cone, _) => return cone_overlap(first, second),
        (ShapeKind::Prism, _) => return prism_overlap(first, second),
        (ShapeKind::Box, ShapeKind::Box) => box_overlap(first, second),
        (ShapeKind::Cylinder, ShapeKind::Box) => cylinder_box_overlap(first, second),
        (ShapeKind::Box, ShapeKind::Cylinder) => cylinder_box_overlap(second, first),
        _ => {
            if first.upper[2] < second.lower[2] || second.upper[2] < first.lower[2] {
                return Ok(false);
            }
            let (a, b) = (first.center(), second.center());
            hypot(&[a[0] - b[0], a[1] - b[1]]) <= first.radius() + second.radius() + EPSILON
        }
    })
}

/// `_resolve_area(scene, query)`.
fn resolve_area(scene: &CompiledScene, query: &JsonMap) -> Resolved {
    let area = shape_from_payload(&query["shape"]);
    let include = strings(&query["include"]);
    let mut retained = Vec::new();
    for item in scene.objects.iter().chain(&scene.zones) {
        if !include.contains(&plural(item.kind)) || !item.active || !overlap(&area, &item.shape)? {
            continue;
        }
        retained.push((
            item.kind,
            item.id.clone(),
            json!({"kind": item.kind, "id": item.id, "metadata": item.metadata}),
        ));
    }
    retained.sort_by(|a, b| (a.0, &a.1).cmp(&(b.0, &b.1)));
    let maximum = int(&query["max_results"]) as usize;
    Ok(with(
        provenance(scene, "area"),
        json!({
            "results": retained.iter().take(maximum).map(|item| item.2.clone()).collect::<Vec<_>>(),
            "truncated": retained.len() > maximum,
        }),
    ))
}

/// `_segment_enters_box(origin, destination, lower, upper)` in three dimensions: strict
/// interiors, so face, edge and corner contact stay clear.
fn segment_enters_solid_box(origin: V3, destination: V3, lower: V3, upper: V3) -> bool {
    let (mut near, mut far) = (0.0f64, 1.0f64);
    for axis in 0..3 {
        let delta = destination[axis] - origin[axis];
        if delta == 0.0 {
            if !(lower[axis] < origin[axis] && origin[axis] < upper[axis]) {
                return false;
            }
            continue;
        }
        let first = (lower[axis] - origin[axis]) / delta;
        let second = (upper[axis] - origin[axis]) / delta;
        near = max_first([near, min_first([first, second])]);
        far = min_first([far, max_first([first, second])]);
        if near >= far {
            return false;
        }
    }
    near < far
}

/// `_sweep_enters_solid(mover, solid, origin, destination, half, height)`.
fn sweep_enters_solid(
    mover: &Shape,
    solid: &Shape,
    origin: V3,
    destination: V3,
    half: V2,
    height: f64,
) -> bool {
    let (lower_z, upper_z) = (solid.lower[2] - height, solid.upper[2]);
    if mover.kind == ShapeKind::Box && solid.kind == ShapeKind::Box {
        return segment_enters_solid_box(
            origin,
            destination,
            [solid.lower[0] - half[0], solid.lower[1] - half[1], lower_z],
            [solid.upper[0] + half[0], solid.upper[1] + half[1], upper_z],
        );
    }
    let (mut near, mut far) = (0.0f64, 1.0f64);
    let delta_z = destination[2] - origin[2];
    if delta_z == 0.0 {
        if !(lower_z < origin[2] && origin[2] < upper_z) {
            return false;
        }
    } else {
        let (first, second) = ((lower_z - origin[2]) / delta_z, (upper_z - origin[2]) / delta_z);
        near = max_first([near, min_first([first, second])]);
        far = min_first([far, max_first([first, second])]);
        if near >= far {
            return false;
        }
    }
    let start = [0, 1].map(|i| origin[i] + near * (destination[i] - origin[i]));
    let end = [0, 1].map(|i| origin[i] + far * (destination[i] - origin[i]));
    let (lower, upper, radius) = if solid.kind == ShapeKind::Box {
        ([solid.lower[0], solid.lower[1]], [solid.upper[0], solid.upper[1]], mover.radius())
    } else {
        let center = solid.center();
        if mover.kind == ShapeKind::Box {
            (
                [center[0] - half[0], center[1] - half[1]],
                [center[0] + half[0], center[1] + half[1]],
                solid.radius(),
            )
        } else {
            ([center[0], center[1]], [center[0], center[1]], solid.radius() + mover.radius())
        }
    };
    swept_footprint_hits_box(start, end, lower, upper, [0.0, 0.0], Some(radius))
}

fn entity_row<'p>(scene: &'p CompiledScene, id: &str) -> &'p Json {
    scene.admitted.payload()["entities"]
        .as_array()
        .expect("admitted entities")
        .iter()
        .find(|row| row["id"].as_str() == Some(id))
        .expect("an admitted entity")
}

/// `_resolve_body_proximity(scene, query)`.
fn resolve_body_proximity(scene: &CompiledScene, query: &JsonMap) -> JsonMap {
    let moving = entity_row(scene, query["moving_entity_id"].as_str().unwrap());
    let fixed = entity_row(scene, query["fixed_entity_id"].as_str().unwrap());
    let (origin, destination, center) =
        (vec3(&moving["position"]), vec3(&query["destination"]), vec3(&fixed["position"]));
    let mut half = [0.0, 0.0];
    let mut rounding = 0.0;
    for row in [moving, fixed] {
        let footprint = &row["footprint"];
        if kind_of(footprint) == "circle" {
            rounding += num(&footprint["radius"]);
        } else {
            half = [0, 1].map(|i| half[i] + num(&footprint["half_extents"][i]));
        }
    }
    let lower_z = center[2] - num(&moving["height"]);
    let upper_z = center[2] + num(&fixed["height"]);
    let delta = [0, 1, 2].map(|i| destination[i] - origin[i]);
    let distance_squared = |t: f64| {
        let point = [0, 1, 2].map(|i| origin[i] + delta[i] * t);
        let gaps = [0, 1].map(|i| max_first([0.0, (point[i] - center[i]).abs() - half[i]]));
        let horizontal = max_first([0.0, hypot(&gaps) - rounding]);
        let vertical = max_first([0.0, lower_z - point[2], point[2] - upper_z]);
        horizontal * horizontal + vertical * vertical
    };
    let (mut left, mut right) = (0.0f64, 1.0f64);
    for _ in 0..90 {
        let (a, b) = (left + (right - left) / 3.0, right - (right - left) / 3.0);
        if distance_squared(a) <= distance_squared(b) {
            right = b;
        } else {
            left = a;
        }
    }
    let mut closest = 0.0;
    for candidate in [(left + right) / 2.0, 1.0] {
        if distance_squared(candidate) < distance_squared(closest) {
            closest = candidate;
        }
    }
    let minimum = distance_squared(closest).sqrt();
    let radius = num(&query["radius"]);
    let hit = minimum <= radius + EPSILON;
    let mut result = with(
        provenance(scene, "body_proximity"),
        json!({"hit": hit, "entry": null, "exit": null, "minimum_distance": clean(minimum)}),
    );
    if !hit {
        return result;
    }
    let boundary = |mut outside: f64| {
        let mut inside = closest;
        for _ in 0..70 {
            let midpoint = (outside + inside) / 2.0;
            if distance_squared(midpoint) <= radius * radius {
                inside = midpoint;
            } else {
                outside = midpoint;
            }
        }
        clean(inside)
    };
    let edge = |t: f64, at: f64| {
        if distance_squared(t).sqrt() <= radius + EPSILON {
            at
        } else if minimum <= radius {
            boundary(t)
        } else {
            clean(closest)
        }
    };
    result.insert("entry".into(), json!(edge(0.0, 0.0)));
    result.insert("exit".into(), json!(edge(1.0, 1.0)));
    result
}

/// The fields `_resolve_circle_overlap` reads from its query.
struct OverlapQuery<'q> {
    moving: &'q str,
    fixed: &'q str,
    origin: Option<V3>,
    destination: V3,
    moving_radius: f64,
    fixed_radius: f64,
}

/// `_resolve_circle_overlap(scene, query, entities=...)`.
fn circle_overlap(scene: &CompiledScene, query: &OverlapQuery<'_>) -> JsonMap {
    let moving = entity_row(scene, query.moving);
    let fixed = entity_row(scene, query.fixed);
    let start = query.origin.unwrap_or_else(|| vec3(&moving["position"]));
    let (end, center) = (query.destination, vec3(&fixed["position"]));
    let delta = [0, 1, 2].map(|i| end[i] - start[i]);
    let radius = query.moving_radius + query.fixed_radius;
    let lower = center[2] - num(&moving["height"]);
    let upper = center[2] + num(&fixed["height"]);
    let result = with(
        provenance(scene, "circle_overlap"),
        json!({"hit": false, "entry": null, "exit": null, "starts_inside": false, "ends_inside": false}),
    );
    let offset = [start[0] - center[0], start[1] - center[1]];
    let a = delta[0] * delta[0] + delta[1] * delta[1];
    let b = offset[0] * delta[0] + offset[1] * delta[1];
    let c = offset[0] * offset[0] + offset[1] * offset[1] - radius * radius;
    let (mut entry, mut exit) = (0.0f64, 1.0f64);
    if a <= EPSILON * EPSILON {
        if c >= -EPSILON {
            return result;
        }
    } else {
        let closest = -b / a;
        let gap = hypot(&[offset[0] + delta[0] * closest, offset[1] + delta[1] * closest]);
        if gap >= radius - EPSILON {
            return result;
        }
        let span = ((radius - gap) * (radius + gap) / a).sqrt();
        entry = max_first([entry, closest - span]);
        exit = min_first([exit, closest + span]);
    }
    if delta[2].abs() <= EPSILON {
        if start[2] <= lower + EPSILON || start[2] >= upper - EPSILON {
            return result;
        }
    } else {
        let (z1, z2) = ((lower - start[2]) / delta[2], (upper - start[2]) / delta[2]);
        entry = max_first([entry, min_first([z1, z2])]);
        exit = min_first([exit, max_first([z1, z2])]);
    }
    if exit - entry <= EPSILON {
        return result;
    }
    let inside = |point: V3| {
        hypot(&[point[0] - center[0], point[1] - center[1]]) < radius - EPSILON
            && lower + EPSILON < point[2]
            && point[2] < upper - EPSILON
    };
    with(
        result,
        json!({"hit": true, "entry": clean(entry), "exit": clean(exit), "starts_inside": inside(start), "ends_inside": inside(end)}),
    )
}

fn resolve_circle_overlap(scene: &CompiledScene, query: &JsonMap) -> JsonMap {
    circle_overlap(
        scene,
        &OverlapQuery {
            moving: query["moving_entity_id"].as_str().unwrap(),
            fixed: query["fixed_entity_id"].as_str().unwrap(),
            origin: query.get("origin").map(vec3),
            destination: vec3(&query["destination"]),
            moving_radius: num(&query["moving_radius"]),
            fixed_radius: num(&query["fixed_radius"]),
        },
    )
}

/// The fields `_resolve_sweep` reads from its query.
struct SweepQuery<'q> {
    moving: &'q str,
    destination: V3,
    include_entities: bool,
    excluded: Vec<&'q str>,
    selector: &'q Json,
}

/// `_resolve_sweep(scene, query, start=...)`.
fn sweep(scene: &CompiledScene, query: &SweepQuery<'_>, start: Option<V3>) -> JsonMap {
    let mover = find(scene, "entity", query.moving).expect("an admitted mover");
    let mover_value = entity_row(scene, query.moving);
    let origin = start.unwrap_or_else(|| vec3(&mover_value["position"]));
    let destination = query.destination;
    let result = with(
        provenance(scene, "sweep"),
        json!({
            "supported": true, "clear": false, "distance": clean(dist(&origin, &destination)),
            "blockers": [], "reason": "out_of_bounds",
        }),
    );
    let selected: Vec<&SpatialObject> = scene
        .objects
        .iter()
        .filter(|item| {
            item.active
                && item.movement == "blocking"
                && (item.kind != "entity"
                    || (query.include_entities
                        && item.id != query.moving
                        && !query.excluded.contains(&item.id.as_str())))
                && matches_metadata(item, Some(query.selector))
        })
        .collect();
    let footprint = &mover_value["footprint"];
    let half = if kind_of(footprint) == "circle" {
        [mover.shape.radius(), mover.shape.radius()]
    } else {
        [num(&footprint["half_extents"][0]), num(&footprint["half_extents"][1])]
    };
    let height = num(&mover_value["height"]);
    let (offset_lower, offset_upper) = ([-half[0], -half[1], 0.0], [half[0], half[1], height]);
    let lower = [0, 1, 2].map(|i| scene.lower[i] - offset_lower[i]);
    let upper = [0, 1, 2].map(|i| scene.upper[i] - offset_upper[i]);
    if [origin, destination]
        .iter()
        .any(|point| (0..3).any(|i| !(lower[i] <= point[i] && point[i] <= upper[i])))
    {
        return result;
    }
    let mut blockers: Vec<(&str, &str)> = selected
        .iter()
        .filter(|item| sweep_enters_solid(&mover.shape, &item.shape, origin, destination, half, height))
        .map(|item| (item.kind, item.id.as_str()))
        .collect();
    blockers.sort();
    let clear = blockers.is_empty();
    with(
        result,
        json!({
            "clear": clear,
            "blockers": blockers.iter().map(|(kind, id)| json!({"kind": kind, "id": id})).collect::<Vec<_>>(),
            "reason": if clear { "clear" } else { "blocked" },
        }),
    )
}

fn resolve_sweep(scene: &CompiledScene, query: &JsonMap) -> JsonMap {
    sweep(
        scene,
        &SweepQuery {
            moving: query["moving_entity_id"].as_str().unwrap(),
            destination: vec3(&query["destination"]),
            include_entities: query["include_entities"] == json!(true),
            excluded: strings(&query["exclude_entity_ids"]),
            selector: &query["metadata_filter"],
        },
        None,
    )
}

/// A point as an exact dictionary key (`-0.0` and `0.0` are equal keys in Python).
fn point_key(point: V3) -> [u64; 3] {
    point.map(|value| if value == 0.0 { 0 } else { value.to_bits() })
}

/// `_resolve_circle_route(scene, query)`.
fn resolve_circle_route(scene: &CompiledScene, query: &JsonMap) -> JsonMap {
    let moving_id = query["moving_entity_id"].as_str().unwrap();
    let moving = entity_row(scene, moving_id);
    let origin = vec3(&moving["position"]);
    let destination = vec3(&query["destination"]);
    let circles = query["circles"].as_array().expect("admitted circles");
    let moving_radius = num(&query["moving_radius"]);
    let maximum = int(&query["max_tests"]);
    let mut edge_costs: HashMap<([u64; 3], [u64; 3]), f64> = HashMap::new();
    for row in query["edge_costs"].as_array().expect("admitted edge costs") {
        edge_costs.insert((point_key(vec3(&row["start"])), point_key(vec3(&row["end"]))), num(&row["cost"]));
    }
    let base = with(
        provenance(scene, "circle_route"),
        json!({"found": false, "points": [], "distance": null, "truncated": false, "tests": 0}),
    );
    let tests = std::cell::Cell::new(0i64);
    let outcome = |extra: Json| {
        let mut result = with(base.clone(), extra);
        result.insert("tests".into(), json!(tests.get()));
        result
    };
    let clear = |first: V3, second: V3| {
        tests.set(tests.get() + 1);
        let swept = sweep(
            scene,
            &SweepQuery {
                moving: moving_id,
                destination: second,
                include_entities: false,
                excluded: Vec::new(),
                selector: &query["metadata_filter"],
            },
            Some(first),
        );
        if swept["supported"] != json!(true) || swept["clear"] != json!(true) {
            return false;
        }
        for circle in circles {
            let overlap = circle_overlap(
                scene,
                &OverlapQuery {
                    moving: moving_id,
                    fixed: circle["entity_id"].as_str().unwrap(),
                    origin: Some(first),
                    destination: second,
                    moving_radius,
                    fixed_radius: num(&circle["radius"]),
                },
            );
            if overlap["ends_inside"] == json!(true)
                || (circle["blocking"] == json!(true)
                    && overlap["hit"] == json!(true)
                    && !(first == origin && overlap["starts_inside"] == json!(true)))
            {
                return false;
            }
        }
        true
    };
    if clear(origin, destination) {
        return outcome(
            json!({"found": true, "points": [origin, destination], "distance": clean(dist(&origin, &destination))}),
        );
    }
    let blocking: Vec<&Json> = circles.iter().filter(|circle| circle["blocking"] == json!(true)).collect();
    if blocking.len() > 16 {
        return outcome(json!({"truncated": true}));
    }
    let mut points = vec![origin, destination];
    let mut elevations = vec![origin[2]];
    if (origin[2] - destination[2]).abs() > EPSILON {
        elevations.push(destination[2]);
    }
    let tau = std::f64::consts::TAU;
    let pi = std::f64::consts::PI;
    for circle in &blocking {
        let fixed = entity_row(scene, circle["entity_id"].as_str().unwrap());
        let center = vec3(&fixed["position"]);
        if min_first([origin[2], destination[2]]) >= center[2] + num(&fixed["height"]) - EPSILON
            || max_first([origin[2], destination[2]]) + num(&moving["height"]) <= center[2] + EPSILON
        {
            continue;
        }
        let occupied_radius = moving_radius + num(&circle["radius"]);
        let mut normals: Vec<f64> = (0..16).map(|index| f64::from(index) * tau / 16.0).collect();
        for other in &blocking {
            let other_center = vec3(&entity_row(scene, other["entity_id"].as_str().unwrap())["position"]);
            let (dx, dy) = (other_center[0] - center[0], other_center[1] - center[1]);
            if hypot(&[dx, dy]) < occupied_radius + moving_radius + num(&other["radius"]) - EPSILON {
                continue;
            }
            let angle = float_mod(atan2(dy, dx), tau);
            if normals.iter().all(|existing| remainder(angle - existing, tau).abs() > EPSILON) {
                normals.push(angle);
            }
        }
        let vertices: Vec<(f64, f64)> = if normals.len() == 16 {
            (0..16)
                .map(|index| (f64::from(index * 2 + 1) * pi / 16.0, occupied_radius / cos(pi / 16.0)))
                .collect()
        } else {
            normals.sort_by(|a, b| a.partial_cmp(b).expect("finite angles"));
            let mut following: Vec<f64> = normals[1..].to_vec();
            following.push(normals[0] + tau);
            normals
                .iter()
                .zip(&following)
                .map(|(first, second)| {
                    ((first + second) / 2.0, occupied_radius / cos((second - first) / 2.0))
                })
                .collect()
        };
        for (angle, radius) in vertices {
            for elevation in &elevations {
                points.push([center[0] + radius * cos(angle), center[1] + radius * sin(angle), *elevation]);
            }
        }
    }
    let mut costs: HashMap<usize, f64> = HashMap::new();
    let mut parents: HashMap<usize, usize> = HashMap::new();
    let mut frontier = BinaryHeap::new();
    frontier.push(Reverse((Key(dist(&origin, &destination)), Key(0.0), 0usize, -1i64)));
    while let Some(Reverse((_, Key(cost), current, parent))) = frontier.pop() {
        if costs.contains_key(&current) {
            continue;
        }
        if parent != -1 {
            if tests.get() >= maximum {
                return outcome(json!({"truncated": true}));
            }
            if !clear(points[parent as usize], points[current]) {
                continue;
            }
            parents.insert(current, parent as usize);
        }
        costs.insert(current, cost);
        if current == 1 {
            let mut retained = vec![1usize];
            while *retained.last().unwrap() != 0 {
                retained.push(parents[retained.last().unwrap()]);
            }
            let path: Vec<V3> = retained.iter().rev().map(|&index| points[index]).collect();
            let distance = clean(sum_floats(path.windows(2).map(|pair| dist(&pair[0], &pair[1]))));
            return outcome(json!({"found": true, "points": path, "distance": distance}));
        }
        for neighbor in 1..points.len() {
            if costs.contains_key(&neighbor) {
                continue;
            }
            let step = edge_costs
                .get(&(point_key(points[current]), point_key(points[neighbor])))
                .copied()
                .unwrap_or_else(|| dist(&points[current], &points[neighbor]));
            let candidate = cost + step;
            frontier.push(Reverse((
                Key(candidate + dist(&points[neighbor], &destination)),
                Key(candidate),
                neighbor,
                current as i64,
            )));
        }
    }
    outcome(json!({}))
}

/// The resolver for an admitted query's kind.
pub(crate) fn resolve(scene: &CompiledScene, query: &JsonMap) -> Resolved {
    Ok(match query["kind"].as_str().unwrap_or_default() {
        "ray" => resolve_ray(scene, query),
        "exposure" => resolve_exposure(scene, query),
        "path" => return resolve_path(scene, query),
        "crossed_zones" => resolve_zones(scene, query),
        "area" => return resolve_area(scene, query),
        "body_proximity" => resolve_body_proximity(scene, query),
        "circle_overlap" => resolve_circle_overlap(scene, query),
        "sweep" => resolve_sweep(scene, query),
        "circle_route" => resolve_circle_route(scene, query),
        "placement" => super::placement::resolve_placement(scene, query),
        other => unreachable!("no resolver for {other}"),
    })
}
