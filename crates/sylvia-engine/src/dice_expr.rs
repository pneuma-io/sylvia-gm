//! The two dice-expression readers the runtime uses.
//!
//! - [`parse_dice_expr`] is `_parse_dice_expr` in `runtime/_effect_executor.py`, used by the
//!   `dice`, `dice_pool` and `random_table` effects. It never fails: anything it cannot read
//!   falls back to one die, twenty sides, no modifier.
//! - [`roll_initiative_expr`] is `_roll_initiative_expr` in `runtime/_initiative.py`. It reads a
//!   different grammar (the modifier after the last `+`, no ` - `) and lets `int()` errors
//!   escape, so their messages are part of its behaviour.
//!
//! Both follow Python's `str.strip`, `str.lower` and `int()` exactly (`sylvia_base::pystr` and
//! [`lower`]). Integer text beyond `i64` (`differences.md` row 5): a die count or side count is
//! only compared with limits, so it is the bound on its side and takes Python's branch; a
//! modifier is stored, so it is refused with the fixed message where Python keeps the exact
//! value.

use crate::dice::Dice;
use crate::pyerror::{int_range_error, PyError, PyResult};
use sylvia_base::pystr::{strip, IntParseError};
use sylvia_base::Json;

/// `text.lower()`.
///
/// Rust's full lowercase mapping, final-sigma rule included, which is what Python applies. The
/// vectors compare it with Python for every character that has a lowercase form in Unicode 15.
pub fn lower(text: &str) -> String {
    text.to_lowercase()
}

/// `int(text)` for a value that is stored or computed with: beyond `i64` is refused.
fn int_value(text: &str) -> Result<i64, IntParseError> {
    sylvia_base::pystr::parse_int(text)
}

/// `int(text)` for a value only compared with limits: beyond `i64` is the bound on its side.
fn int_bound(text: &str) -> PyResult<i64> {
    match sylvia_base::pystr::parse_int(text) {
        Ok(value) => Ok(value),
        Err(error) => error.bound_for_comparison().map_err(|e| PyError::value(e.message)),
    }
}

/// `-int(text)`: exact even for `int(text) == 2**63`, whose negation is `i64::MIN`.
fn negated_int(text: &str) -> Result<i64, IntParseError> {
    match int_value(text) {
        Ok(value) => value.checked_neg().ok_or(IntParseError::OutOfRange { negative: false }),
        Err(IntParseError::OutOfRange { negative: false }) => {
            // Parse the negated text instead; only 2**63 itself fits after negation.
            let negated = match text.strip_prefix('+') {
                Some(rest) => format!("-{rest}"),
                None => format!("-{text}"),
            };
            int_value(&negated)
        }
        Err(IntParseError::OutOfRange { negative: true }) => {
            Err(IntParseError::OutOfRange { negative: false })
        }
        Err(invalid) => Err(invalid),
    }
}

/// `_parse_dice_expr`'s result: `{"count", "sides", "modifier_delta"}`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DiceParts {
    /// At least 1. Compare-only: a count written beyond `i64` is `i64::MAX`, above every limit.
    pub count: i64,
    /// At least 2. Compare-only, as `count`.
    pub sides: i64,
    /// The flat modifier written in the expression; `None` when it lies beyond `i64`.
    modifier_delta: Option<i64>,
}

impl DiceParts {
    /// The modifier, refused with the fixed message when it was written beyond `i64`
    /// (Python keeps the exact integer).
    pub fn modifier_delta(&self) -> PyResult<i64> {
        self.modifier_delta.ok_or_else(int_range_error)
    }

    /// The dict `_parse_dice_expr` returns, in its key order.
    pub fn to_dict(&self) -> PyResult<Json> {
        let mut map = sylvia_base::JsonMap::new();
        map.insert("count".into(), self.count.into());
        map.insert("sides".into(), self.sides.into());
        map.insert("modifier_delta".into(), self.modifier_delta()?.into());
        Ok(Json::Object(map))
    }
}

/// `int(text)` with `ValueError` suppressed (`contextlib.suppress(ValueError)`), for a
/// compared count or side count.
fn bound_or(text: &str, fallback: i64) -> i64 {
    int_bound(text).unwrap_or(fallback)
}

/// `int(text)` with `ValueError` suppressed, for a stored modifier: invalid text gives the
/// fallback, as in Python; text beyond `i64` gives `None`.
fn value_or(parsed: Result<i64, IntParseError>, fallback: i64) -> Option<i64> {
    match parsed {
        Ok(value) => Some(value),
        Err(IntParseError::Invalid(_)) => Some(fallback),
        Err(IntParseError::OutOfRange { .. }) => None,
    }
}

/// `_parse_dice_expr(expr)`: `"d20"`, `"2d6"`, `"d20 + 5"`, `"3d4 - 1"`.
pub fn parse_dice_expr(expr: &str) -> DiceParts {
    let lowered = lower(strip(expr));
    let mut expr = lowered.as_str();
    let mut modifier_delta = Some(0);
    if let Some((left, right)) = expr.split_once('+') {
        expr = strip(left);
        modifier_delta = value_or(int_value(strip(right)), 0);
    } else if let Some((left, right)) = expr.split_once(" - ") {
        expr = strip(left);
        modifier_delta = value_or(negated_int(strip(right)), 0);
    }
    let (count, sides_text) = if let Some(rest) = expr.strip_prefix('d') {
        (1, rest)
    } else if let Some((count_text, sides_text)) = expr.split_once('d') {
        (bound_or(count_text, 1), sides_text)
    } else {
        (1, "20")
    };
    let sides = bound_or(sides_text, 20);
    DiceParts { count: count.max(1), sides: sides.max(2), modifier_delta }
}

/// `_roll_initiative_expr(expr, ctx)`: the initiative total and the roll's `to_dict()`, or no
/// roll for a fixed value. `int()` and `roll()` errors propagate as in Python; a modifier or
/// total beyond `i64` is refused with the fixed message.
pub fn roll_initiative_expr(dice: &mut Dice, expr: &str) -> PyResult<(i64, Option<Json>)> {
    let mut expr = strip(expr);
    let mut modifier = 0;
    if let Some((left, right)) = expr.rsplit_once('+') {
        expr = strip(left);
        modifier = value_or(int_value(strip(right)), 0).ok_or_else(int_range_error)?;
    }
    let expr_lower = lower(expr);
    let (count, sides) = if let Some(rest) = expr_lower.strip_prefix('d') {
        (1, if rest.is_empty() { 20 } else { int_bound(rest)? })
    } else if let Some((count_text, sides_text)) = expr_lower.split_once('d') {
        let count = if count_text.is_empty() { 1 } else { int_bound(count_text)? };
        let sides = if sides_text.is_empty() { 20 } else { int_bound(sides_text)? };
        (count, sides)
    } else {
        return match int_value(expr) {
            Ok(value) => Ok((value.checked_add(modifier).ok_or_else(int_range_error)?, None)),
            Err(IntParseError::Invalid(_)) => Ok((modifier, None)),
            Err(IntParseError::OutOfRange { .. }) => Err(int_range_error()),
        };
    };
    let result = dice.roll(sides, count, modifier)?;
    let total = result.total.checked_add(result.modifier).ok_or_else(int_range_error)?;
    Ok((total, Some(result.to_dict())))
}
