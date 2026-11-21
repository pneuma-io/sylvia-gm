//! `runtime/_collections.py`: bounded operations on package-declared cards, pieces and
//! ordered collections. Every message is copied from the snapshot.

use super::compare::sorted;
use super::eval::ExecutionBudget;
use super::value::{py_eq, Value};
use crate::pyerror::{int_range_error, PyError};
use crate::pynum::{py_sum_numbers, PyNumber};
use indexmap::IndexMap;

/// `LIMIT`.
pub const LIMIT: usize = 1024;

fn value_error(message: &str) -> PyError {
    PyError::value(message)
}

/// `collection(value)`: a list or tuple of at most 1,024 items, copied.
pub fn collection(value: &Value) -> Result<Vec<Value>, PyError> {
    match value.sequence() {
        Some(items) if items.len() <= LIMIT => Ok(items.to_vec()),
        _ => Err(value_error("A bounded sequence is required")),
    }
}

/// `indices(stop)`.
pub fn indices(stop: &Value) -> Result<Value, PyError> {
    match stop {
        Value::Int(count) if (0..=1024).contains(count) => {
            Ok(Value::list((0..*count).map(Value::Int).collect()))
        }
        _ => Err(value_error("Sequence length is outside its bound")),
    }
}

/// Python's slice `[start:stop]` bounds for a sequence of `len` items.
fn slice_bounds(len: usize, start: i64, stop: i64) -> (usize, usize) {
    let len = i64::try_from(len).expect("bounded length");
    let clamp = |index: i64| {
        let index = if index < 0 { index.saturating_add(len) } else { index };
        usize::try_from(index.clamp(0, len)).expect("clamped")
    };
    let (start, stop) = (clamp(start), clamp(stop));
    (start, stop.max(start))
}

/// `portion(value, start, stop)`.
pub fn portion(value: &Value, start: &Value, stop: &Value) -> Result<Value, PyError> {
    let (Value::Int(start), Value::Int(stop)) = (start, stop) else {
        return Err(value_error("Sequence offsets must be integers"));
    };
    let items = collection(value)?;
    let (from, to) = slice_bounds(items.len(), *start, *stop);
    Ok(Value::list(items[from..to].to_vec()))
}

/// `joined(first, second)`.
pub fn joined(first: &Value, second: &Value) -> Result<Value, PyError> {
    let mut items = collection(first)?;
    items.extend(collection(second)?);
    if items.len() > LIMIT {
        return Err(value_error("Combined sequence exceeds its bound"));
    }
    Ok(Value::list(items))
}

/// `replaced(value, index, item)`.
pub fn replaced(value: &Value, index: &Value, item: &Value) -> Result<Value, PyError> {
    let mut items = collection(value)?;
    let slot = match index {
        Value::Int(index) => usize::try_from(*index).ok().filter(|slot| *slot < items.len()),
        _ => None,
    };
    let Some(slot) = slot else {
        return Err(value_error("Sequence index is outside its bound"));
    };
    items[slot] = item.clone();
    Ok(Value::list(items))
}

/// `without(value, removed)`: remove each item once, the first equal one.
pub fn without(value: &Value, removed: &Value) -> Result<Value, PyError> {
    let mut items = collection(value)?;
    for item in collection(removed)? {
        let Some(position) = items.iter().position(|candidate| py_eq(candidate, &item)) else {
            return Err(value_error("Removed item is not present"));
        };
        items.remove(position);
    }
    Ok(Value::list(items))
}

/// `occurrences(value, item)`.
pub fn occurrences(value: &Value, item: &Value) -> Result<Value, PyError> {
    let count = collection(value)?.iter().filter(|candidate| py_eq(candidate, item)).count();
    Ok(Value::Int(i64::try_from(count).expect("bounded count")))
}

/// `text(value)`.
pub fn text(value: &Value) -> Result<Value, PyError> {
    match value {
        Value::Str(_) | Value::Int(_) | Value::Float(_) | Value::Bool(_) => {
            let text = value.to_str();
            if text.chars().count() > 256 {
                return Err(value_error("A bounded scalar is required"));
            }
            Ok(Value::str(text))
        }
        _ => Err(value_error("A bounded scalar is required")),
    }
}

/// `total(value)`: Python's `sum()` of ints and floats (not bools).
pub fn total(value: &Value) -> Result<Value, PyError> {
    let items = collection(value)?;
    let mut numbers = Vec::with_capacity(items.len());
    for item in &items {
        numbers.push(match item {
            Value::Int(int) => PyNumber::Int(i128::from(*int)),
            Value::Float(float) => PyNumber::Float(*float),
            _ => return Err(value_error("Numeric sequence required")),
        });
    }
    match py_sum_numbers(numbers) {
        PyNumber::Int(int) => i64::try_from(int).map(Value::Int).map_err(|_| int_range_error()),
        PyNumber::Float(float) => Ok(Value::Float(float)),
    }
}

/// `one(value)`.
pub fn one(value: &Value) -> Value {
    Value::list(vec![value.clone()])
}

/// `ordered(value)`.
pub fn ordered(value: &Value) -> Result<Value, PyError> {
    Ok(Value::list(sorted(&collection(value)?)?))
}

/// A pattern item: `int` or `str` (not `bool`).
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
enum Identity {
    Int(i64),
    Str(String),
}

fn identity(value: &Value) -> Option<Identity> {
    match value {
        Value::Int(int) => Some(Identity::Int(*int)),
        Value::Str(text) => Some(Identity::Str(text.to_string())),
        _ => None,
    }
}

/// A `collections.Counter` in insertion order.
type Counter = IndexMap<Identity, i64>;

fn counter(items: &[Identity]) -> Counter {
    let mut counts = Counter::new();
    for item in items {
        *counts.entry(item.clone()).or_insert(0) += 1;
    }
    counts
}

/// `remaining - pattern`: positive differences only, in `remaining`'s order.
fn subtract(remaining: &Counter, pattern: &Counter) -> Counter {
    remaining
        .iter()
        .filter_map(|(key, count)| {
            let left = count - pattern.get(key).copied().unwrap_or(0);
            (left > 0).then(|| (key.clone(), left))
        })
        .collect()
}

struct Search<'a> {
    candidates: Vec<(i64, Counter)>,
    found: Vec<Vec<i64>>,
    work: usize,
    budget: Option<&'a ExecutionBudget>,
}

impl Search<'_> {
    fn visit(&mut self, remaining: &Counter, pending: &[i64], chosen: &[i64]) -> Result<(), PyError> {
        self.work += 1;
        if let Some(budget) = self.budget {
            budget.consume_operation()?;
        }
        if self.work > 16384 || self.found.len() > 256 {
            return Err(value_error("Pattern search exceeds its bound"));
        }
        if pending.is_empty() {
            if remaining.values().all(|count| *count <= 0) {
                let mut row = chosen.to_vec();
                row.sort_unstable();
                self.found.push(row);
            }
            return Ok(());
        }
        if remaining.values().sum::<i64>() != pending.iter().sum::<i64>() {
            return Ok(());
        }
        let size = pending[0];
        let first = if pending.iter().all(|p| *p == size) {
            remaining.iter().find(|(_, count)| **count > 0).map(|(key, _)| key.clone())
        } else {
            None
        };
        for index in 0..self.candidates.len() {
            let (candidate, pattern) = &self.candidates[index];
            if pattern.values().sum::<i64>() != size
                || pattern.iter().any(|(key, n)| remaining.get(key).copied().unwrap_or(0) < *n)
            {
                continue;
            }
            if first.as_ref().is_some_and(|first| !pattern.contains_key(first)) {
                continue;
            }
            let next = subtract(remaining, pattern);
            let mut path = chosen.to_vec();
            path.push(*candidate);
            self.visit(&next, &pending[1..], &path)?;
        }
        Ok(())
    }
}

/// `partitions(items, patterns, sizes, consume=...)`: every exact cover of the items by the
/// declared patterns, as sorted lists of pattern indices, first-found order, without repeats.
pub fn partitions(
    items: &Value,
    patterns: &Value,
    sizes: &Value,
    budget: Option<&ExecutionBudget>,
) -> Result<Value, PyError> {
    let values = collection(items)?;
    let rows = collection(patterns)?;
    let required = collection(sizes)?;
    if values.len() > 32 || rows.len() > 128 || required.len() > 8 {
        return Err(value_error("Pattern partition exceeds its bound"));
    }
    let Some(identities) = values.iter().map(identity).collect::<Option<Vec<_>>>() else {
        return Err(value_error("Pattern items must be scalar identities"));
    };
    let mut pending = Vec::with_capacity(required.len());
    for size in &required {
        match size {
            Value::Int(size) if (1..=8).contains(size) => pending.push(*size),
            _ => return Err(value_error("Pattern sizes must be positive bounded integers")),
        }
    }
    let mut candidates = Vec::with_capacity(rows.len());
    for (index, row) in rows.iter().enumerate() {
        let pattern = collection(row)?;
        let identities = pattern.iter().map(identity).collect::<Option<Vec<_>>>();
        let Some(identities) = identities.filter(|ids| !ids.is_empty()) else {
            return Err(value_error("Invalid pattern"));
        };
        candidates.push((i64::try_from(index).expect("bounded"), counter(&identities)));
    }
    let mut search = Search { candidates, found: Vec::new(), work: 0, budget };
    search.visit(&counter(&identities), &pending, &[])?;
    let mut seen: Vec<Vec<i64>> = Vec::new();
    for row in search.found {
        if !seen.contains(&row) {
            seen.push(row);
        }
    }
    Ok(Value::list(
        seen.into_iter().map(|row| Value::list(row.into_iter().map(Value::Int).collect())).collect(),
    ))
}

/// `completions(items, patterns, sizes, universe, consume=...)`.
pub fn completions(
    items: &Value,
    patterns: &Value,
    sizes: &Value,
    universe: &Value,
    budget: Option<&ExecutionBudget>,
) -> Result<Value, PyError> {
    let values = collection(items)?;
    let candidates = collection(universe)?;
    if candidates.len() > 128 {
        return Err(value_error("Completion universe exceeds its bound"));
    }
    let mut out = Vec::new();
    for item in candidates {
        let mut extended = values.clone();
        extended.push(item.clone());
        if partitions(&Value::list(extended), patterns, sizes, budget)?.truthy() {
            out.push(item);
        }
    }
    Ok(Value::list(out))
}
