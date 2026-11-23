//! `spatial/_engine.py`: `_placement_box` and `_resolve_placement`, the nearest clear box
//! placement.
//!
//! Python computes in floats, or, when the query names a reference face, in exact
//! `Fraction(str(v))` arithmetic. Here the same search is generic over [`PlacementNumber`]: `f64`
//! with Python's float rules, or `BigRational` built from the exact decimal text Python's `str`
//! gives each value (`Fraction(str(0.1))` is 1/10, not the binary value of `0.1`).

use super::engine::{clean, provenance, with, CompiledScene, ShapeKind, SpatialObject};
use crate::pymath::sum_floats;
use num_bigint::BigInt;
use num_rational::BigRational;
use num_traits::{ToPrimitive, Zero};
use serde_json::json;
use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashSet};
use sylvia_base::{pyjson, Json, JsonMap};

/// What the placement search computes with.
pub(crate) trait PlacementNumber: Clone + PartialEq + PartialOrd + std::fmt::Debug {
    fn minus(&self, other: &Self) -> Self;
    /// `x ** 2`.
    fn squared(&self) -> Self;
    /// `sum(values)`.
    fn total(values: Vec<Self>) -> Self;
    /// `float(x)`.
    fn float(&self) -> f64;
}

impl PlacementNumber for f64 {
    fn minus(&self, other: &Self) -> Self {
        self - other
    }
    fn squared(&self) -> Self {
        self * self
    }
    fn total(values: Vec<Self>) -> Self {
        sum_floats(values)
    }
    fn float(&self) -> f64 {
        *self
    }
}

impl PlacementNumber for BigRational {
    fn minus(&self, other: &Self) -> Self {
        self - other
    }
    fn squared(&self) -> Self {
        self * self
    }
    fn total(values: Vec<Self>) -> Self {
        values.into_iter().fold(BigRational::zero(), |sum, value| sum + value)
    }
    fn float(&self) -> f64 {
        self.to_f64().expect("a finite rational")
    }
}

/// `Fraction(text)` for the decimal text Python's `str` gives an int or float.
pub(crate) fn fraction_of_text(text: &str) -> BigRational {
    let (negative, body) = match text.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, text.strip_prefix('+').unwrap_or(text)),
    };
    let (mantissa, exponent) = match body.find(['e', 'E']) {
        Some(at) => (&body[..at], body[at + 1..].parse::<i64>().expect("a decimal exponent")),
        None => (body, 0),
    };
    let (whole, fraction) = mantissa.split_once('.').unwrap_or((mantissa, ""));
    let digits: BigInt = format!("{whole}{fraction}").parse().expect("decimal digits");
    let scale = exponent - fraction.len() as i64;
    let ten = BigInt::from(10);
    let mut value = if scale >= 0 {
        BigRational::from_integer(digits * num_traits::pow(ten, scale as usize))
    } else {
        BigRational::new(digits, num_traits::pow(ten, (-scale) as usize))
    };
    if negative {
        value = -value;
    }
    value
}

/// `Fraction(str(value))` for an admitted JSON number.
pub(crate) fn fraction_of_json(value: &Json) -> BigRational {
    let Json::Number(number) = value else { panic!("an admitted number") };
    if number.is_f64() {
        fraction_of_text(&pyjson::float_repr(number.as_f64().unwrap()))
    } else {
        fraction_of_text(&number.to_string())
    }
}

/// `Fraction(str(x))` for a Python float.
fn fraction_of_float(value: f64) -> BigRational {
    fraction_of_text(&pyjson::float_repr(value))
}

type Point<N> = [N; 3];

/// `max(a, b)`: `a` unless `b` is larger.
fn larger<N: PlacementNumber>(a: &N, b: &N) -> N {
    if b > a {
        b.clone()
    } else {
        a.clone()
    }
}

/// `min(a, b)`: `a` unless `b` is smaller.
fn smaller<N: PlacementNumber>(a: &N, b: &N) -> N {
    if b < a {
        b.clone()
    } else {
        a.clone()
    }
}

fn clamp<N: PlacementNumber>(value: &N, lower: &N, upper: &N) -> N {
    smaller(&larger(value, lower), upper)
}

fn point3<N>(make: impl Fn(usize) -> N) -> Point<N> {
    [make(0), make(1), make(2)]
}

/// One queued candidate: `(primary, secondary, point, index)`, ordered as Python's tuples.
struct Candidate<N> {
    primary: N,
    secondary: N,
    point: Point<N>,
    index: [usize; 3],
}

impl<N: PlacementNumber> Candidate<N> {
    fn order(&self, other: &Self) -> Ordering {
        let numbers = |a: &N, b: &N| a.partial_cmp(b).expect("comparable placement numbers");
        numbers(&self.primary, &other.primary)
            .then_with(|| numbers(&self.secondary, &other.secondary))
            .then_with(|| {
                (0..3)
                    .map(|i| numbers(&self.point[i], &other.point[i]))
                    .find(|o| o.is_ne())
                    .unwrap_or(Ordering::Equal)
            })
            .then_with(|| self.index.cmp(&other.index))
    }
}

impl<N: PlacementNumber> PartialEq for Candidate<N> {
    fn eq(&self, other: &Self) -> bool {
        self.order(other) == Ordering::Equal
    }
}
impl<N: PlacementNumber> Eq for Candidate<N> {}
impl<N: PlacementNumber> PartialOrd for Candidate<N> {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl<N: PlacementNumber> Ord for Candidate<N> {
    /// Reversed, so the standard max-heap pops Python's `heapq` minimum.
    fn cmp(&self, other: &Self) -> Ordering {
        other.order(self)
    }
}

/// Everything the search needs, in one arithmetic.
pub(crate) struct PlacementInputs<N> {
    pub origin: Point<N>,
    pub objective_lower: Point<N>,
    pub objective_upper: Point<N>,
    pub offset_lower: Point<N>,
    pub offset_upper: Point<N>,
    pub domain_lower: Point<N>,
    pub domain_upper: Point<N>,
    /// Each selected solid box, in scene object order.
    pub solids: Vec<(Point<N>, Point<N>)>,
}

/// The search after the inputs are known: from `lower = domain_lower - offset_lower` on.
fn search<N: PlacementNumber>(result: JsonMap, inputs: PlacementInputs<N>, maximum: usize) -> JsonMap {
    let PlacementInputs {
        origin,
        objective_lower,
        objective_upper,
        offset_lower,
        offset_upper,
        domain_lower,
        domain_upper,
        solids,
    } = inputs;
    let lower = point3(|i| domain_lower[i].minus(&offset_lower[i]));
    let upper = point3(|i| domain_upper[i].minus(&offset_upper[i]));
    if (0..3).any(|i| lower[i] > upper[i]) {
        return result;
    }
    let mut obstacles: Vec<(Point<N>, Point<N>)> = Vec::new();
    for (solid_lower, solid_upper) in solids {
        let obstacle_lower = point3(|i| solid_lower[i].minus(&offset_upper[i]));
        let obstacle_upper = point3(|i| solid_upper[i].minus(&offset_lower[i]));
        if (0..3).all(|i| obstacle_upper[i] > lower[i] && obstacle_lower[i] < upper[i]) {
            obstacles.push((obstacle_lower, obstacle_upper));
        }
    }
    let mut axes: Vec<Vec<N>> = Vec::new();
    for i in 0..3 {
        // The set, by value: the first of equal values stays.
        let mut values: Vec<N> = Vec::new();
        let mut add = |value: N| {
            if !values.contains(&value) {
                values.push(value);
            }
        };
        add(clamp(&origin[i], &lower[i], &upper[i]));
        add(lower[i].clone());
        add(upper[i].clone());
        for coordinate in [&objective_lower[i], &objective_upper[i]] {
            add(clamp(coordinate, &lower[i], &upper[i]));
        }
        for (obstacle_lower, obstacle_upper) in &obstacles {
            for coordinate in [&obstacle_lower[i], &obstacle_upper[i]] {
                if &lower[i] <= coordinate && coordinate <= &upper[i] {
                    add(coordinate.clone());
                }
            }
        }
        let key = |coordinate: &N| {
            (
                coordinate.minus(&clamp(coordinate, &objective_lower[i], &objective_upper[i])).squared(),
                coordinate.minus(&origin[i]).squared(),
                coordinate.clone(),
            )
        };
        values.sort_by(|a, b| {
            let (ka, kb) = (key(a), key(b));
            ka.0.partial_cmp(&kb.0)
                .expect("comparable")
                .then_with(|| ka.1.partial_cmp(&kb.1).expect("comparable"))
                .then_with(|| ka.2.partial_cmp(&kb.2).expect("comparable"))
        });
        axes.push(values);
    }
    let candidate = |index: [usize; 3]| {
        let point = point3(|i| axes[i][index[i]].clone());
        let primary = N::total(
            (0..3)
                .map(|i| {
                    point[i].minus(&clamp(&point[i], &objective_lower[i], &objective_upper[i])).squared()
                })
                .collect(),
        );
        let secondary = N::total((0..3).map(|i| point[i].minus(&origin[i]).squared()).collect());
        Candidate { primary, secondary, point, index }
    };
    let mut queue = BinaryHeap::from([candidate([0, 0, 0])]);
    let mut visited = HashSet::from([[0usize, 0, 0]]);
    let mut examined = 0usize;
    while examined < maximum {
        let Some(Candidate { primary, point, index, .. }) = queue.pop() else { break };
        examined += 1;
        if !obstacles.iter().any(|(lo, hi)| (0..3).all(|i| lo[i] < point[i] && point[i] < hi[i])) {
            let position: Vec<f64> = point.iter().map(|value| clean(value.float())).collect();
            return with(
                result,
                json!({
                    "found": true, "position": position, "distance": clean(primary.float().sqrt()),
                    "reason": "placed", "examined": examined,
                }),
            );
        }
        for axis in 0..3 {
            let mut following = index;
            following[axis] += 1;
            if following[axis] < axes[axis].len() && visited.insert(following) {
                queue.push(candidate(following));
            }
        }
    }
    let pending = !queue.is_empty();
    with(
        result,
        json!({
            "supported": !pending,
            "reason": if pending { "candidate_limit" } else { "no_placement" },
            "examined": examined,
        }),
    )
}

/// `_placement_box(value, kind)`: a geometry volume or a box entity's body, as exact fractions.
fn placement_box(value: &Json, kind: &str) -> (Point<BigRational>, Point<BigRational>) {
    if kind == "geometry" {
        let volume = &value["volume"];
        return (
            point3(|i| fraction_of_json(&volume["min"][i])),
            point3(|i| fraction_of_json(&volume["max"][i])),
        );
    }
    let position = point3(|i| fraction_of_json(&value["position"][i]));
    let half_x = fraction_of_json(&value["footprint"]["half_extents"][0]);
    let half_y = fraction_of_json(&value["footprint"]["half_extents"][1]);
    let height = fraction_of_json(&value["height"]);
    (
        [&position[0] - &half_x, &position[1] - &half_y, position[2].clone()],
        [&position[0] + &half_x, &position[1] + &half_y, &position[2] + &height],
    )
}

fn row<'p>(scene: &'p CompiledScene, kind: &str, id: &str) -> &'p Json {
    let collection = if kind == "geometry" { "geometry" } else { "entities" };
    scene.admitted.payload()[collection]
        .as_array()
        .expect("an admitted collection")
        .iter()
        .find(|item| item["id"].as_str() == Some(id))
        .expect("an admitted object")
}

/// The scene objects placement treats as solids, or `None` when one is not a box.
fn solids<'s>(scene: &'s CompiledScene, query: &JsonMap, moving: &str) -> Option<Vec<&'s SpatialObject>> {
    let excluded: Vec<&str> =
        query["exclude_entity_ids"].as_array().into_iter().flatten().filter_map(Json::as_str).collect();
    let selector = &query["metadata_filter"];
    let mut selected = Vec::new();
    for item in &scene.objects {
        if !item.active
            || item.movement != "blocking"
            || (item.kind == "entity" && (item.id == moving || excluded.contains(&item.id.as_str())))
        {
            continue;
        }
        if !super::engine::matches_metadata(item, Some(selector)) {
            continue;
        }
        if item.shape.kind != ShapeKind::Box {
            return None;
        }
        selected.push(item);
    }
    Some(selected)
}

/// `_resolve_placement(scene, query)`.
pub(crate) fn resolve_placement(scene: &CompiledScene, query: &JsonMap) -> JsonMap {
    let result = with(
        provenance(scene, "placement"),
        json!({
            "supported": true, "found": false, "position": null, "distance": null,
            "reason": "no_placement", "examined": 0,
        }),
    );
    let unsupported = |result: &JsonMap| {
        with(result.clone(), json!({"supported": false, "reason": "unsupported_geometry"}))
    };
    let moving = query["moving_entity_id"].as_str().expect("an admitted mover");
    let mover = scene
        .objects
        .iter()
        .find(|item| item.kind == "entity" && item.id == moving)
        .expect("an admitted mover");
    let maximum = query["max_candidates"].as_u64().expect("an admitted limit") as usize;
    let zone = query
        .get("containing_zone_id")
        .and_then(Json::as_str)
        .map(|id| scene.zones.iter().find(|item| item.id == id).expect("an admitted zone"));
    if let Some(reference) = query.get("reference_face") {
        // Exact arithmetic.
        let target = &reference["target"];
        let (kind, id) = (target["kind"].as_str().unwrap(), target["id"].as_str().unwrap());
        let source = scene
            .objects
            .iter()
            .find(|item| item.kind == kind && item.id == id)
            .expect("an admitted reference");
        if source.shape.kind != ShapeKind::Box {
            return unsupported(&result);
        }
        let (axis, use_upper) = match reference["face"].as_str().unwrap() {
            "west" => (0, false),
            "east" => (0, true),
            "south" => (1, false),
            "north" => (1, true),
            "down" => (2, false),
            _ => (2, true),
        };
        let (source_lower, source_upper) = placement_box(row(scene, source.kind, &source.id), source.kind);
        let face = if use_upper { source_upper[axis].clone() } else { source_lower[axis].clone() };
        let objective_lower = point3(|i| if i == axis { face.clone() } else { source_lower[i].clone() });
        let objective_upper = point3(|i| if i == axis { face.clone() } else { source_upper[i].clone() });
        if mover.shape.kind != ShapeKind::Box {
            return unsupported(&result);
        }
        let mover_value = row(scene, "entity", moving);
        let current = point3(|i| fraction_of_json(&mover_value["position"][i]));
        let (mover_lower, mover_upper) = placement_box(mover_value, "entity");
        let offset_lower = point3(|i| &mover_lower[i] - &current[i]);
        let offset_upper = point3(|i| &mover_upper[i] - &current[i]);
        let mut domain_lower = point3(|i| fraction_of_float(scene.lower[i]));
        let mut domain_upper = point3(|i| fraction_of_float(scene.upper[i]));
        if let Some(zone) = zone {
            if zone.shape.kind != ShapeKind::Box {
                return unsupported(&result);
            }
            domain_lower = point3(|i| larger(&domain_lower[i], &fraction_of_float(zone.shape.lower[i])));
            domain_upper = point3(|i| smaller(&domain_upper[i], &fraction_of_float(zone.shape.upper[i])));
        }
        let origin = point3(|i| fraction_of_json(&query["origin"][i]));
        let Some(solids) = solids_after_bounds(
            scene,
            query,
            moving,
            &domain_lower,
            &domain_upper,
            &offset_lower,
            &offset_upper,
        ) else {
            return unsupported(&result);
        };
        let solids = solids
            .into_iter()
            .map(|item| placement_box(row(scene, item.kind, &item.id), item.kind))
            .collect();
        return search(
            result,
            PlacementInputs {
                origin,
                objective_lower,
                objective_upper,
                offset_lower,
                offset_upper,
                domain_lower,
                domain_upper,
                solids,
            },
            maximum,
        );
    }
    // Float arithmetic.
    if mover.shape.kind != ShapeKind::Box {
        return unsupported(&result);
    }
    let mover_value = row(scene, "entity", moving);
    let current = point3(|i| mover_value["position"][i].as_f64().unwrap());
    let offset_lower = point3(|i| mover.shape.lower[i] - current[i]);
    let offset_upper = point3(|i| mover.shape.upper[i] - mover.shape.lower[i] + offset_lower[i]);
    let mut domain_lower = scene.lower;
    let mut domain_upper = scene.upper;
    if let Some(zone) = zone {
        if zone.shape.kind != ShapeKind::Box {
            return unsupported(&result);
        }
        domain_lower = point3(|i| larger(&domain_lower[i], &zone.shape.lower[i]));
        domain_upper = point3(|i| smaller(&domain_upper[i], &zone.shape.upper[i]));
    }
    let origin = point3(|i| query["origin"][i].as_f64().unwrap());
    let Some(solids) =
        solids_after_bounds(scene, query, moving, &domain_lower, &domain_upper, &offset_lower, &offset_upper)
    else {
        return unsupported(&result);
    };
    let solids = solids.into_iter().map(|item| (item.shape.lower, item.shape.upper)).collect();
    search(
        result,
        PlacementInputs {
            origin,
            objective_lower: origin,
            objective_upper: origin,
            offset_lower,
            offset_upper,
            domain_lower,
            domain_upper,
            solids,
        },
        maximum,
    )
}

/// Python checks the search bounds before it walks the solids, and a non-box solid ends the
/// walk with `unsupported_geometry`; this keeps that order.
fn solids_after_bounds<'s, N: PlacementNumber>(
    scene: &'s CompiledScene,
    query: &JsonMap,
    moving: &str,
    domain_lower: &Point<N>,
    domain_upper: &Point<N>,
    offset_lower: &Point<N>,
    offset_upper: &Point<N>,
) -> Option<Vec<&'s SpatialObject>> {
    let lower = point3(|i| domain_lower[i].minus(&offset_lower[i]));
    let upper = point3(|i| domain_upper[i].minus(&offset_upper[i]));
    if (0..3).any(|i| lower[i] > upper[i]) {
        // The search returns the unplaced result before looking at solids.
        return Some(Vec::new());
    }
    solids(scene, query, moving)
}
