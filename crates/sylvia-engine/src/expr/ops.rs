//! Python's `+ - * // % **` and unary `-` on expression values, after the evaluator's own checks.
//!
//! Numbers follow CPython 3.12: `int` with `int` stays an `int` (refused beyond `i64`,
//! `differences.md` row 5), a `float` on either side makes a `float`, and `//`, `%` and `**` on
//! floats are ports of `_float_div_mod`, `float_rem` and `float_pow` in `Objects/floatobject.c`.
//! Lists and tuples concatenate and repeat. Everything else is the `TypeError` CPython raises,
//! with its message.

use super::value::{numeric, Numeric, Value};
use crate::pyerror::{int_range_error, PyError, PyErrorKind};

/// A sequence repetition longer than this raises `MemoryError` instead of exhausting memory
/// (`differences.md`). CPython would try to allocate it.
pub const MAX_REPEATED_ITEMS: usize = 1 << 26;

fn unsupported(symbol: &str, left: &Value, right: &Value) -> PyError {
    PyError::type_error(format!(
        "unsupported operand type(s) for {symbol}: '{}' and '{}'",
        left.type_name(),
        right.type_name()
    ))
}

#[allow(clippy::cast_precision_loss)]
fn to_float(number: Numeric) -> f64 {
    match number {
        Numeric::Int(value) => value as f64,
        Numeric::Float(value) => value,
    }
}

fn concat(left: &Value, right: &Value) -> Option<Result<Value, PyError>> {
    let (kind, items) = match left {
        Value::List(items) => ("list", items),
        Value::Tuple(items) => ("tuple", items),
        _ => return None,
    };
    let same = matches!((left, right), (Value::List(_), Value::List(_)) | (Value::Tuple(_), Value::Tuple(_)));
    if !same {
        return Some(Err(PyError::type_error(format!(
            "can only concatenate {kind} (not \"{}\") to {kind}",
            right.type_name()
        ))));
    }
    let other = right.sequence().expect("same kind");
    let mut joined = Vec::with_capacity(items.len() + other.len());
    joined.extend(items.iter().cloned());
    joined.extend(other.iter().cloned());
    Some(Ok(if kind == "list" { Value::list(joined) } else { Value::tuple(joined) }))
}

/// `left + right`.
pub fn add(left: &Value, right: &Value) -> Result<Value, PyError> {
    match (numeric(left), numeric(right)) {
        (Some(Numeric::Int(a)), Some(Numeric::Int(b))) => {
            return a.checked_add(b).map(Value::Int).ok_or_else(int_range_error)
        }
        (Some(a), Some(b)) => return Ok(Value::Float(to_float(a) + to_float(b))),
        _ => {}
    }
    if let Value::Str(a) = left {
        if let Value::Str(b) = right {
            return Ok(Value::str(format!("{a}{b}")));
        }
    }
    concat(left, right).unwrap_or_else(|| Err(unsupported("+", left, right)))
}

/// `left - right`.
pub fn sub(left: &Value, right: &Value) -> Result<Value, PyError> {
    match (numeric(left), numeric(right)) {
        (Some(Numeric::Int(a)), Some(Numeric::Int(b))) => {
            a.checked_sub(b).map(Value::Int).ok_or_else(int_range_error)
        }
        (Some(a), Some(b)) => Ok(Value::Float(to_float(a) - to_float(b))),
        _ => Err(unsupported("-", left, right)),
    }
}

fn repeat(sequence: &Value, times: i64) -> Result<Value, PyError> {
    let items = sequence.sequence().expect("a sequence");
    let count = if items.is_empty() { 0 } else { usize::try_from(times.max(0)).unwrap_or(usize::MAX) };
    let total = items.len().checked_mul(count).filter(|n| *n <= MAX_REPEATED_ITEMS);
    let Some(total) = total else {
        return Err(PyError::of(PyErrorKind::MemoryError, ""));
    };
    let mut out = Vec::with_capacity(total);
    for _ in 0..count {
        out.extend(items.iter().cloned());
    }
    Ok(match sequence {
        Value::List(_) => Value::list(out),
        _ => Value::tuple(out),
    })
}

fn not_int_multiplier(other: &Value) -> PyError {
    PyError::type_error(format!("can't multiply sequence by non-int of type '{}'", other.type_name()))
}

/// `left * right`.
pub fn mul(left: &Value, right: &Value) -> Result<Value, PyError> {
    match (numeric(left), numeric(right)) {
        (Some(Numeric::Int(a)), Some(Numeric::Int(b))) => {
            return a.checked_mul(b).map(Value::Int).ok_or_else(int_range_error)
        }
        (Some(a), Some(b)) => return Ok(Value::Float(to_float(a) * to_float(b))),
        _ => {}
    }
    match (left, right) {
        (Value::List(_) | Value::Tuple(_), _) => match numeric(right) {
            Some(Numeric::Int(times)) => repeat(left, times),
            _ => Err(not_int_multiplier(right)),
        },
        (_, Value::List(_) | Value::Tuple(_)) => match numeric(left) {
            Some(Numeric::Int(times)) => repeat(right, times),
            _ => Err(not_int_multiplier(left)),
        },
        _ => Err(unsupported("*", left, right)),
    }
}

/// `_float_div_mod(vx, wx)`: Python's float floor quotient and remainder (`wx != 0`).
pub fn float_div_mod(vx: f64, wx: f64) -> (f64, f64) {
    let mut modulo = vx % wx;
    let mut div = (vx - modulo) / wx;
    if modulo != 0.0 {
        if (wx < 0.0) != (modulo < 0.0) {
            modulo += wx;
            div -= 1.0;
        }
    } else {
        modulo = 0.0f64.copysign(wx);
    }
    let floordiv = if div != 0.0 {
        let mut floor = div.floor();
        if div - floor > 0.5 {
            floor += 1.0;
        }
        floor
    } else {
        0.0f64.copysign(vx / wx)
    };
    (floordiv, modulo)
}

/// `int(x)` for a float: refused beyond `i64`; `inf` and `nan` are CPython's errors.
pub fn float_to_int(value: f64) -> Result<i64, PyError> {
    if value.is_nan() {
        return Err(PyError::value("cannot convert float NaN to integer"));
    }
    if value.is_infinite() {
        return Err(PyError::of(PyErrorKind::OverflowError, "cannot convert float infinity to integer"));
    }
    let truncated = value.trunc();
    if !(-9.223_372_036_854_776e18..9.223_372_036_854_776e18).contains(&truncated) {
        return Err(int_range_error());
    }
    #[allow(clippy::cast_possible_truncation)]
    Ok(truncated as i64)
}

/// The evaluator's `int(left // right)` (the caller has already refused a zero divisor).
pub fn floor_div_int(left: &Value, right: &Value) -> Result<Value, PyError> {
    match (numeric(left), numeric(right)) {
        (Some(Numeric::Int(a)), Some(Numeric::Int(b))) => {
            let quotient = a.checked_div(b).ok_or_else(int_range_error)?;
            let floored = if (a % b != 0) && ((a < 0) != (b < 0)) { quotient - 1 } else { quotient };
            Ok(Value::Int(floored))
        }
        (Some(a), Some(b)) => {
            let (floordiv, _) = float_div_mod(to_float(a), to_float(b));
            float_to_int(floordiv).map(Value::Int)
        }
        _ => Err(unsupported("//", left, right)),
    }
}

/// `left % right` (the caller has already refused a zero divisor).
pub fn modulo(left: &Value, right: &Value) -> Result<Value, PyError> {
    match (numeric(left), numeric(right)) {
        (Some(Numeric::Int(a)), Some(Numeric::Int(b))) => {
            // `i64::MIN % -1` overflows in Rust; Python's answer is 0.
            let remainder = a.checked_rem(b).unwrap_or(0);
            let floored =
                if remainder != 0 && ((remainder < 0) != (b < 0)) { remainder + b } else { remainder };
            Ok(Value::Int(floored))
        }
        (Some(a), Some(b)) => {
            let (vx, wx) = (to_float(a), to_float(b));
            let mut remainder = vx % wx;
            if remainder != 0.0 {
                if (wx < 0.0) != (remainder < 0.0) {
                    remainder += wx;
                }
            } else {
                remainder = 0.0f64.copysign(wx);
            }
            Ok(Value::Float(remainder))
        }
        _ => Err(unsupported("%", left, right)),
    }
}

/// What `**` gives besides a value.
#[derive(Debug, Clone, PartialEq)]
pub enum PowError {
    /// CPython's `OverflowError` from the platform `pow`; the evaluator words it itself.
    Overflow,
    /// Any other exception, as CPython raises it.
    Raised(PyError),
}

fn is_odd_integer(value: f64) -> bool {
    (value.abs() % 2.0) == 1.0
}

/// `float_pow(iv, iw)`.
pub fn float_pow(iv: f64, iw: f64) -> Result<f64, PowError> {
    if iw == 0.0 {
        return Ok(1.0);
    }
    if iv.is_nan() {
        return Ok(iv);
    }
    if iw.is_nan() {
        return Ok(if iv == 1.0 { 1.0 } else { iw });
    }
    if iw.is_infinite() {
        let magnitude = iv.abs();
        return Ok(if magnitude == 1.0 {
            1.0
        } else if (iw > 0.0) == (magnitude > 1.0) {
            iw.abs()
        } else {
            0.0
        });
    }
    if iv.is_infinite() {
        let odd = is_odd_integer(iw);
        return Ok(if iw > 0.0 {
            if odd {
                iv
            } else {
                iv.abs()
            }
        } else if odd {
            0.0f64.copysign(iv)
        } else {
            0.0
        });
    }
    if iv == 0.0 {
        if iw < 0.0 {
            return Err(PowError::Raised(PyError::of(
                PyErrorKind::ZeroDivisionError,
                "0.0 cannot be raised to a negative power",
            )));
        }
        return Ok(if is_odd_integer(iw) { iv } else { 0.0 });
    }
    let mut base = iv;
    let mut negate = false;
    if base < 0.0 {
        if iw != iw.floor() {
            // CPython returns a complex number here (`differences.md`).
            return Err(PowError::Raised(PyError::of(
                PyErrorKind::ValueError,
                "complex results are not supported",
            )));
        }
        base = -base;
        negate = is_odd_integer(iw);
    }
    if base == 1.0 {
        return Ok(if negate { -1.0 } else { 1.0 });
    }
    let result = base.powf(iw);
    if result.is_infinite() {
        return Err(PowError::Overflow);
    }
    Ok(if negate { -result } else { result })
}

/// `left ** right`.
pub fn pow(left: &Value, right: &Value) -> Result<Value, PowError> {
    match (numeric(left), numeric(right)) {
        (Some(Numeric::Int(base)), Some(Numeric::Int(exponent))) if exponent >= 0 => {
            int_pow(base, exponent).map(Value::Int).ok_or_else(|| PowError::Raised(int_range_error()))
        }
        (Some(a), Some(b)) => float_pow(to_float(a), to_float(b)).map(Value::Float),
        _ => Err(PowError::Raised(PyError::type_error(format!(
            "unsupported operand type(s) for ** or pow(): '{}' and '{}'",
            left.type_name(),
            right.type_name()
        )))),
    }
}

fn int_pow(base: i64, exponent: i64) -> Option<i64> {
    match base {
        0 => Some(i64::from(exponent == 0)),
        1 => Some(1),
        -1 => Some(if exponent % 2 == 0 { 1 } else { -1 }),
        _ => base.checked_pow(u32::try_from(exponent).ok()?),
    }
}

/// `-operand` (after the evaluator's own bool, str and None checks).
pub fn neg(operand: &Value) -> Result<Value, PyError> {
    match operand {
        Value::Int(value) => value.checked_neg().map(Value::Int).ok_or_else(int_range_error),
        Value::Float(value) => Ok(Value::Float(-value)),
        other => Err(PyError::type_error(format!("bad operand type for unary -: '{}'", other.type_name()))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn float_floor_division_and_modulo_match_python() {
        assert_eq!(float_div_mod(7.5, 2.0), (3.0, 1.5));
        assert_eq!(float_div_mod(-7.5, 2.0), (-4.0, 0.5));
        assert_eq!(float_div_mod(7.5, -2.0), (-4.0, -0.5));
        assert!(float_div_mod(0.0, -3.0).0.is_sign_negative());
        assert_eq!(modulo(&Value::Int(-7), &Value::Int(2)), Ok(Value::Int(1)));
        assert_eq!(modulo(&Value::Int(i64::MIN), &Value::Int(-1)), Ok(Value::Int(0)));
        assert_eq!(floor_div_int(&Value::Int(-7), &Value::Int(2)), Ok(Value::Int(-4)));
    }

    #[test]
    fn huge_repetition_is_a_memory_error() {
        let one = Value::list(vec![Value::Int(1)]);
        let error = mul(&one, &Value::Int(i64::try_from(MAX_REPEATED_ITEMS).unwrap() + 1)).unwrap_err();
        assert_eq!((error.kind, error.message.as_str()), (PyErrorKind::MemoryError, ""));
        assert_eq!(mul(&Value::list(Vec::new()), &Value::Int(i64::MAX)), Ok(Value::list(Vec::new())));
        assert_eq!(mul(&one, &Value::Int(-3)), Ok(Value::list(Vec::new())));
        assert!(pow(&Value::Int(7), &Value::Int(i64::MAX)).is_err());
    }

    #[test]
    fn pow_special_cases() {
        assert_eq!(float_pow(f64::NAN, 0.0), Ok(1.0));
        assert_eq!(float_pow(1.0, f64::NAN), Ok(1.0));
        assert_eq!(float_pow(-2.0, 3.0), Ok(-8.0));
        assert_eq!(float_pow(10.0, 400.0), Err(PowError::Overflow));
        assert!(matches!(float_pow(0.0, -1.0), Err(PowError::Raised(_))));
        assert_eq!(pow(&Value::Int(2), &Value::Int(-1)), Ok(Value::Float(0.5)));
        assert_eq!(pow(&Value::Int(2), &Value::Int(62)), Ok(Value::Int(1 << 62)));
        assert!(pow(&Value::Int(2), &Value::Int(63)).is_err());
    }
}
