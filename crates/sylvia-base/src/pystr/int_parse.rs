//! `int(text)` for a `str`, base 10, as CPython 3.12 parses it (`PyLong_FromUnicodeObject`),
//! and the conversion step it shares with `float(text)`.

use super::repr::repr_200;
use super::whitespace::is_space;

// Generated; kept exactly as the generator writes it.
#[rustfmt::skip]
#[path = "digit_table.rs"]
mod table;

/// A `ValueError` from `int(text)` or `float(text)`; `message` is its `str()`, as CPython words it.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{message}")]
pub struct ValueError {
    /// `str(error)`.
    pub message: String,
}

impl ValueError {
    pub(super) fn new(message: impl Into<String>) -> Self {
        Self { message: message.into() }
    }
}

/// `_PyUnicode_TransformDecimalAndSpaceToASCII`, the first step of both `int(text)` and
/// `float(text)`: ASCII stays as it is, other whitespace becomes a space, a decimal digit of any
/// script becomes its ASCII digit, and anything else becomes `?` (which neither parser accepts).
pub(super) fn decimal_and_space_to_ascii(text: &str) -> Vec<u8> {
    text.chars()
        .map(|c| {
            if c.is_ascii() {
                u8::try_from(c).expect("ASCII")
            } else if is_space(c) {
                b' '
            } else if let Some(value) = decimal(c) {
                b'0' + u8::try_from(value).expect("a digit value")
            } else {
                b'?'
            }
        })
        .collect()
}

/// `Py_ISSPACE`: the ASCII whitespace C's `isspace` knows, which the number parsers strip. It
/// leaves out `\x1c`-`\x1f`, which `str.isspace()` accepts.
pub(super) fn is_ascii_c_space(byte: u8) -> bool {
    matches!(byte, b' ' | b'\t' | b'\n' | 0x0b | 0x0c | b'\r')
}

/// `sys.get_int_max_str_digits()` default: the most decimal digits `int()` converts.
pub const INT_MAX_STR_DIGITS: usize = 4300;

/// `unicodedata.decimal(c, None)`: the value of a decimal digit (category Nd) of any script.
pub fn decimal(c: char) -> Option<u32> {
    let code = u32::from(c);
    let index = table::DECIMAL_ZEROS.partition_point(|zero| *zero <= code);
    if index == 0 {
        return None;
    }
    let offset = code - table::DECIMAL_ZEROS[index - 1];
    (offset < 10).then_some(offset)
}

/// The one message the port gives when an integer from content, the model or the player, or a
/// result computed from one, lies outside the signed 64-bit range (`differences.md` row 5).
/// Python computes such integers exactly.
pub const INT_RANGE_MESSAGE: &str = "integer result exceeds the supported 64-bit range";

/// Why [`parse_int`] gave no `i64`.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum IntParseError {
    /// Python raises this `ValueError` too.
    #[error("{0}")]
    Invalid(ValueError),
    /// Python returns an exact integer beyond the `i64` range, below zero when `negative`.
    /// A caller that only compares the value with a limit takes Python's branch by treating it
    /// as beyond every limit on that side; a caller that stores it or computes with it refuses
    /// it with [`IntParseError::refusal`].
    #[error("{INT_RANGE_MESSAGE}")]
    OutOfRange {
        /// The integer is below `i64::MIN` (otherwise above `i64::MAX`).
        negative: bool,
    },
}

impl IntParseError {
    /// The error a storing or computing caller raises: Python's `ValueError` for invalid text,
    /// [`INT_RANGE_MESSAGE`] for an integer beyond `i64`.
    pub fn refusal(&self) -> ValueError {
        match self {
            Self::Invalid(error) => error.clone(),
            Self::OutOfRange { .. } => ValueError::new(INT_RANGE_MESSAGE),
        }
    }

    /// The value for a caller that only compares it with limits: an out-of-range integer as the
    /// `i64` bound on its side, which lies beyond every limit Sylvia compares with. Invalid text
    /// stays an error.
    pub fn bound_for_comparison(&self) -> Result<i64, ValueError> {
        match self {
            Self::Invalid(error) => Err(error.clone()),
            Self::OutOfRange { negative: true } => Ok(i64::MIN),
            Self::OutOfRange { negative: false } => Ok(i64::MAX),
        }
    }
}

/// `int(text)`.
///
/// After the digit and whitespace conversion it shares with `float()`, CPython accepts ASCII
/// whitespace, one sign, digits with single underscores between them, and ASCII whitespace.
/// Anything else is `invalid literal for int() with base 10: %.200R` of the original text. A
/// valid string of more than 4,300 digits (underscores not counted) is refused with the
/// int-max-str-digits message; an invalid one gets the invalid-literal message first. A valid
/// integer outside `i64` is [`IntParseError::OutOfRange`], never a clamped value.
pub fn parse_int(text: &str) -> Result<i64, IntParseError> {
    let invalid = || {
        IntParseError::Invalid(ValueError::new(format!(
            "invalid literal for int() with base 10: {}",
            repr_200(text)
        )))
    };
    let ascii = decimal_and_space_to_ascii(text);
    let start = ascii.iter().position(|b| !is_ascii_c_space(*b)).unwrap_or(ascii.len());
    let end = ascii.iter().rposition(|b| !is_ascii_c_space(*b)).map_or(start, |last| last + 1);
    let mut body = &ascii[start..end];
    let mut negative = false;
    if let Some(sign @ (b'+' | b'-')) = body.first() {
        negative = *sign == b'-';
        body = &body[1..];
    }
    let mut digits = 0usize;
    let mut value: Option<i64> = Some(0);
    let mut previous_underscore = true; // An underscore first is invalid too.
    for byte in body {
        if *byte == b'_' {
            if previous_underscore {
                return Err(invalid());
            }
            previous_underscore = true;
            continue;
        }
        if !byte.is_ascii_digit() {
            return Err(invalid());
        }
        previous_underscore = false;
        digits += 1;
        let digit = i64::from(byte - b'0');
        value = value
            .and_then(|v| v.checked_mul(10))
            .and_then(|v| v.checked_add(if negative { -digit } else { digit }));
    }
    if digits == 0 || previous_underscore {
        return Err(invalid());
    }
    if digits > INT_MAX_STR_DIGITS {
        return Err(IntParseError::Invalid(ValueError::new(format!(
            "Exceeds the limit ({INT_MAX_STR_DIGITS} digits) for integer string conversion: value \
             has {digits} digits; use sys.set_int_max_str_digits() to increase the limit"
        ))));
    }
    value.ok_or(IntParseError::OutOfRange { negative })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_cases() {
        assert_eq!(table::UNICODE_VERSION, "15.0.0");
        assert_eq!(decimal('\u{0663}'), Some(3));
        assert_eq!(decimal('a'), None);
        assert_eq!(parse_int(" 1_0 "), Ok(10));
        assert_eq!(parse_int("\u{ff12}\u{ff10}"), Ok(20));
        assert_eq!(parse_int("1\u{a0}"), Ok(1));
        assert_eq!(
            parse_int("\x1c5\x1c").unwrap_err().to_string(),
            "invalid literal for int() with base 10: '\\x1c5\\x1c'"
        );
        assert!(parse_int(&"1".repeat(4301)).unwrap_err().to_string().starts_with("Exceeds the limit"));
        assert!(parse_int(&format!("{}x", "1".repeat(4301))).unwrap_err().to_string().starts_with("invalid"));
        assert_eq!(parse_int("9223372036854775807"), Ok(i64::MAX));
        assert_eq!(parse_int("-9223372036854775808"), Ok(i64::MIN));
        assert_eq!(parse_int("9223372036854775808"), Err(IntParseError::OutOfRange { negative: false }));
        assert_eq!(parse_int("-9223372036854775809"), Err(IntParseError::OutOfRange { negative: true }));
        assert!(parse_int("99999999999999999999x").unwrap_err().to_string().starts_with("invalid"));
    }
}
