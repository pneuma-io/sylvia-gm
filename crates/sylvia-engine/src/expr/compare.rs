//! Python's `<` and `>` between values (`max`, `min`, `sorted`), and `list.sort`'s order.

use super::value::{numeric, numeric_cmp, py_eq, Value};
use crate::pyerror::PyError;
use std::cmp::Ordering;

/// `<` or `>`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Order {
    /// `<`.
    Lt,
    /// `>`.
    Gt,
}

impl Order {
    const fn symbol(self) -> &'static str {
        match self {
            Self::Lt => "<",
            Self::Gt => ">",
        }
    }

    fn holds(self, ordering: Ordering) -> bool {
        match self {
            Self::Lt => ordering == Ordering::Less,
            Self::Gt => ordering == Ordering::Greater,
        }
    }
}

/// `left < right` or `left > right` as Python evaluates it: numbers exactly across `int` and
/// `float` (a NaN compares false), strings by code point, lists and tuples lexicographically,
/// and anything else the `TypeError` CPython raises.
pub fn py_order(order: Order, left: &Value, right: &Value) -> Result<bool, PyError> {
    if let (Some(a), Some(b)) = (numeric(left), numeric(right)) {
        return Ok(numeric_cmp(a, b).is_some_and(|ordering| order.holds(ordering)));
    }
    match (left, right) {
        (Value::Str(a), Value::Str(b)) => Ok(order.holds(a.as_ref().cmp(b.as_ref()))),
        (Value::List(a), Value::List(b)) | (Value::Tuple(a), Value::Tuple(b)) => {
            for (x, y) in a.iter().zip(b.iter()) {
                if !py_eq(x, y) {
                    return py_order(order, x, y);
                }
            }
            Ok(order.holds(a.len().cmp(&b.len())))
        }
        _ => Err(PyError::type_error(format!(
            "'{}' not supported between instances of '{}' and '{}'",
            order.symbol(),
            left.type_name(),
            right.type_name()
        ))),
    }
}

/// `sorted(values)`: CPython's `list.sort` on the items.
///
/// Below 64 items CPython's timsort is one `count_run` and one `binarysort`, ported here
/// comparison for comparison, so even the first failing comparison (which names the two types)
/// is Python's. From 64 items a stable merge sort gives the same result for every consistent
/// order; when items cannot be ordered against each other, or a NaN breaks the order, the
/// result or the types named in the error may differ (`differences.md`).
pub fn sorted(values: &[Value]) -> Result<Vec<Value>, PyError> {
    let mut items = values.to_vec();
    let lt = |a: &Value, b: &Value| py_order(Order::Lt, a, b);
    if items.len() < 2 {
        return Ok(items);
    }
    if items.len() >= 64 {
        let mut failure = None;
        items.sort_by(|a, b| {
            if failure.is_some() {
                return Ordering::Equal;
            }
            match (lt(a, b), lt(b, a)) {
                (Ok(true), _) => Ordering::Less,
                (Ok(false), Ok(true)) => Ordering::Greater,
                (Ok(false), Ok(false)) => Ordering::Equal,
                (Err(error), _) | (_, Err(error)) => {
                    failure = Some(error);
                    Ordering::Equal
                }
            }
        });
        return failure.map_or(Ok(items), Err);
    }
    // count_run
    let mut run = 2;
    let descending = lt(&items[1], &items[0])?;
    while run < items.len() {
        let below = lt(&items[run], &items[run - 1])?;
        if below != descending {
            break;
        }
        run += 1;
    }
    if descending {
        items[..run].reverse();
    }
    // binarysort(lo, hi, lo + run)
    for start in run..items.len() {
        let pivot = items[start].clone();
        let (mut low, mut high) = (0, start);
        while low < high {
            let middle = low + ((high - low) >> 1);
            if lt(&pivot, &items[middle])? {
                high = middle;
            } else {
                low = middle + 1;
            }
        }
        items[low..=start].rotate_right(1);
    }
    Ok(items)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mixed_types_name_the_first_failing_pair() {
        let error = sorted(&[Value::Int(1), Value::str("a")]).unwrap_err();
        assert_eq!(error.message, "'<' not supported between instances of 'str' and 'int'");
        let ints: Vec<Value> = [3, 1, 2, 1].into_iter().map(Value::Int).collect();
        assert_eq!(sorted(&ints).unwrap(), [1, 1, 2, 3].map(Value::Int));
    }
}
