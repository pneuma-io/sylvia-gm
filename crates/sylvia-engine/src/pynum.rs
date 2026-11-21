//! Python `int`/`float` arithmetic over JSON numbers, and the builtin `sum()`.
//!
//! `sum()` in CPython 3.12 (`Python/bltinmodule.c`, `builtin_sum_impl`) has three phases: an
//! integer fast path, a float fast path that uses Neumaier's compensated summation for floats
//! (and adds integers that fit a C `long` plainly), and a generic phase of ordinary `+` once an
//! item leaves a fast path. A float sum therefore depends on where its items fall, and so on
//! the size of C `long`: 64 bits on Linux and macOS, 32 bits on Windows. This module follows
//! CPython on each platform.

use crate::pyerror::{PyError, PyResult};
use sylvia_base::Json;

/// `LONG_MAX` of the platform's C compiler, which CPython's fast paths test against.
#[cfg(windows)]
const C_LONG_MAX: i128 = i32::MAX as i128;
#[cfg(not(windows))]
const C_LONG_MAX: i128 = i64::MAX as i128;
const C_LONG_MIN: i128 = -C_LONG_MAX - 1;
/// `_PyLong_IsCompact`: one 30-bit digit, read without the `long` conversion.
const COMPACT_LIMIT: i128 = 1 << 30;

/// A Python `int` (within `i128`) or `float`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum PyNumber {
    /// `int` (and `bool`, which Python adds as an `int`).
    Int(i128),
    /// `float`.
    Float(f64),
}

/// A JSON value as Python sees it when adding: a number, or the type name `+` reports.
fn operand(value: &Json) -> Result<PyNumber, &'static str> {
    match value {
        Json::Bool(flag) => Ok(PyNumber::Int(i128::from(*flag))),
        Json::Number(number) => {
            if let Some(int) = number.as_i64() {
                Ok(PyNumber::Int(i128::from(int)))
            } else if let Some(int) = number.as_u64() {
                Ok(PyNumber::Int(i128::from(int)))
            } else {
                Ok(PyNumber::Float(number.as_f64().expect("a JSON number")))
            }
        }
        Json::Null => Err("NoneType"),
        Json::String(_) => Err("str"),
        Json::Array(_) => Err("list"),
        Json::Object(_) => Err("dict"),
    }
}

impl PyNumber {
    /// `type(x).__name__`.
    pub fn type_name(self) -> &'static str {
        match self {
            Self::Int(_) => "int",
            Self::Float(_) => "float",
        }
    }

    /// `self + value` for a JSON value, with Python's `TypeError` for a non-number.
    pub fn add_json(self, value: &Json) -> PyResult<Self> {
        let right = operand(value).map_err(|right| unsupported(self.type_name(), right))?;
        Ok(self.plus(right))
    }

    /// `self + other`. An `int` meeting a `float` becomes the nearest `float`, as in Python.
    #[allow(clippy::cast_precision_loss)]
    pub fn plus(self, other: Self) -> Self {
        match (self, other) {
            (Self::Int(a), Self::Int(b)) => Self::Int(a.checked_add(b).expect("int sum fits in i128")),
            (Self::Int(a), Self::Float(b)) => Self::Float(a as f64 + b),
            (Self::Float(a), Self::Int(b)) => Self::Float(a + b as f64),
            (Self::Float(a), Self::Float(b)) => Self::Float(a + b),
        }
    }

    /// `self == 0`.
    pub fn is_zero(self) -> bool {
        match self {
            Self::Int(value) => value == 0,
            Self::Float(value) => value == 0.0,
        }
    }

    /// `left <= self` for an integer `left`, compared exactly as Python compares int and float.
    pub fn int_at_most(self, left: i128) -> bool {
        match self {
            Self::Int(value) => left <= value,
            Self::Float(value) => int_le_float(left, value),
        }
    }

    /// The JSON number Python's `json` module would write for this value.
    pub fn to_json(self) -> Json {
        match self {
            Self::Int(value) => i64::try_from(value)
                .map(Json::from)
                .or_else(|_| u64::try_from(value).map(Json::from))
                .expect("integer fits a JSON number"),
            Self::Float(value) => Json::from(value),
        }
    }
}

/// Exact `int <= float`.
fn int_le_float(left: i128, right: f64) -> bool {
    if right.is_nan() {
        return false;
    }
    if right.is_infinite() {
        return right > 0.0;
    }
    // Compare against the float's floor, which is an integer the i128 range holds for any
    // finite double below 2^127; larger doubles exceed every i128.
    let floor = right.floor();
    if floor >= 1.7e38 {
        return true;
    }
    if floor <= -1.7e38 {
        return false;
    }
    #[allow(clippy::cast_possible_truncation)]
    let floor_int = floor as i128;
    left <= floor_int
}

fn unsupported(left: &str, right: &str) -> PyError {
    PyError::type_error(format!("unsupported operand type(s) for +: '{left}' and '{right}'"))
}

/// `sum(values)` with the default start `0`.
pub fn py_sum<'a>(values: impl IntoIterator<Item = &'a Json>) -> PyResult<PyNumber> {
    sum_operands(values.into_iter().map(operand))
}

/// `sum(values)` over numbers that need not be JSON (a float may be infinite or NaN).
pub fn py_sum_numbers(values: impl IntoIterator<Item = PyNumber>) -> PyNumber {
    sum_operands(values.into_iter().map(Ok)).expect("numbers always add")
}

/// The phases of `builtin_sum_impl` over operands: a number, or the type name `+` reports.
fn sum_operands(values: impl IntoIterator<Item = Result<PyNumber, &'static str>>) -> PyResult<PyNumber> {
    enum Phase {
        IntFast(i128),
        FloatFast { sum: f64, compensation: f64 },
        Generic(PyNumber),
    }
    let fits_long =
        |b: i128| (-COMPACT_LIMIT < b && b < COMPACT_LIMIT) || (C_LONG_MIN..=C_LONG_MAX).contains(&b);
    let mut phase = Phase::IntFast(0);
    for item in values {
        phase = match phase {
            Phase::IntFast(total) => {
                let right = item.map_err(|right| unsupported("int", right))?;
                match right {
                    // Py_ssize_t is 64 bits on every supported platform.
                    PyNumber::Int(b) if fits_long(b) && i64::try_from(total + b).is_ok() => {
                        Phase::IntFast(total + b)
                    }
                    PyNumber::Int(_) => Phase::Generic(PyNumber::Int(total).plus(right)),
                    PyNumber::Float(_) => match PyNumber::Int(total).plus(right) {
                        PyNumber::Float(sum) => Phase::FloatFast { sum, compensation: 0.0 },
                        PyNumber::Int(_) => unreachable!("int + float is a float"),
                    },
                }
            }
            Phase::FloatFast { sum, compensation } => {
                match item {
                    Ok(PyNumber::Float(x)) => {
                        // Neumaier's improvement of Kahan-Babuska summation, as CPython has it.
                        let t = sum + x;
                        let compensation = if sum.abs() >= x.abs() {
                            compensation + ((sum - t) + x)
                        } else {
                            compensation + ((x - t) + sum)
                        };
                        Phase::FloatFast { sum: t, compensation }
                    }
                    #[allow(clippy::cast_precision_loss)]
                    Ok(PyNumber::Int(value)) if (C_LONG_MIN..=C_LONG_MAX).contains(&value) => {
                        Phase::FloatFast { sum: sum + value as f64, compensation }
                    }
                    other => {
                        let settled = settle(sum, compensation);
                        let right = other.map_err(|right| unsupported("float", right))?;
                        Phase::Generic(PyNumber::Float(settled).plus(right))
                    }
                }
            }
            Phase::Generic(total) => {
                let right = item.map_err(|right| unsupported(total.type_name(), right))?;
                Phase::Generic(total.plus(right))
            }
        };
    }
    Ok(match phase {
        Phase::IntFast(total) => PyNumber::Int(total),
        Phase::FloatFast { sum, compensation } => PyNumber::Float(settle(sum, compensation)),
        Phase::Generic(total) => total,
    })
}

/// Add the compensation unless it is zero or not finite (CPython's guard).
fn settle(sum: f64, compensation: f64) -> f64 {
    if compensation != 0.0 && compensation.is_finite() {
        sum + compensation
    } else {
        sum
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn integer_sums_stay_integers() {
        assert_eq!(py_sum(&[json!(1), json!(true), json!(5)]).unwrap(), PyNumber::Int(7));
        let big = [json!(u64::MAX), json!(u64::MAX)];
        assert_eq!(py_sum(&big).unwrap(), PyNumber::Int(2 * i128::from(u64::MAX)));
    }

    #[test]
    fn float_sums_are_compensated() {
        // Plain addition gives 0.9999999999999999; CPython 3.12 gives 1.0.
        let tenths: Vec<Json> = (0..10).map(|_| json!(0.1)).collect();
        assert_eq!(py_sum(&tenths).unwrap(), PyNumber::Float(1.0));
    }

    #[test]
    fn type_errors_name_both_sides() {
        let error = py_sum(&[json!(1), json!("x")]).unwrap_err();
        assert_eq!(error.message, "unsupported operand type(s) for +: 'int' and 'str'");
        let error = py_sum(&[json!(1.5), json!(null)]).unwrap_err();
        assert_eq!(error.message, "unsupported operand type(s) for +: 'float' and 'NoneType'");
    }

    #[test]
    fn int_float_comparison_is_exact() {
        assert!(PyNumber::Float(2.5).int_at_most(2));
        assert!(!PyNumber::Float(2.5).int_at_most(3));
        assert!(PyNumber::Float(-0.0).int_at_most(0));
        assert!(!PyNumber::Float(-0.5).int_at_most(0));
    }
}
