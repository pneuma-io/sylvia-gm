//! `float(text)` for a `str`, as CPython 3.12 parses it.
//!
//! `PyFloat_FromString` → `_Py_string_to_number_with_underscores` → `float_from_string_inner`
//! → `PyOS_string_to_double` (`_Py_dg_strtod`, then `_Py_parse_inf_or_nan`). Every failure is
//! `could not convert string to float: %R` of the original text, untruncated.

use super::int_parse::{decimal_and_space_to_ascii, is_ascii_c_space, ValueError};
use super::repr::repr;

/// `float(text)`.
///
/// After the same digit and whitespace conversion `int()` uses: underscores only between two
/// digits, ASCII whitespace stripped, then a decimal (`[+-]digits[.digits][(e|E)[+-]digits]`,
/// with a digit on at least one side of the point) or `[+-]inf`, `[+-]infinity`, `[+-]nan` in
/// any case. The value is correctly rounded, overflowing to an infinity and underflowing to a
/// zero of the same sign, as `_Py_dg_strtod` does.
pub fn parse_float(text: &str) -> Result<f64, ValueError> {
    let error = || ValueError::new(format!("could not convert string to float: {}", repr(text)));
    let ascii = decimal_and_space_to_ascii(text);
    // A NUL ends the C string early, which every path then rejects.
    if ascii.contains(&0) {
        return Err(error());
    }
    let without_underscores = if ascii.contains(&b'_') {
        let mut out = Vec::with_capacity(ascii.len());
        let mut previous = 0u8;
        for byte in &ascii {
            if *byte == b'_' {
                if !previous.is_ascii_digit() {
                    return Err(error());
                }
            } else {
                if previous == b'_' && !byte.is_ascii_digit() {
                    return Err(error());
                }
                out.push(*byte);
            }
            previous = *byte;
        }
        if previous == b'_' {
            return Err(error());
        }
        out
    } else {
        ascii
    };
    let mut body = without_underscores.as_slice();
    while let Some((first, rest)) = body.split_first() {
        if !is_ascii_c_space(*first) {
            break;
        }
        body = rest;
    }
    if body.is_empty() {
        return Err(error());
    }
    while body.len() > 1 && is_ascii_c_space(body[body.len() - 1]) {
        body = &body[..body.len() - 1];
    }
    match strtod(body) {
        Some((value, used)) if used == body.len() => Ok(value),
        _ => Err(error()),
    }
}

/// `_PyOS_ascii_strtod`: the value and how many bytes it read, or `None` when nothing parsed.
fn strtod(text: &[u8]) -> Option<(f64, usize)> {
    decimal(text).or_else(|| inf_or_nan(text))
}

/// `_Py_dg_strtod` on Sylvia's inputs: a decimal number at the start of `text`.
fn decimal(text: &[u8]) -> Option<(f64, usize)> {
    let mut i = 0;
    let negative = match text.first() {
        Some(b'-') => {
            i += 1;
            true
        }
        Some(b'+') => {
            i += 1;
            false
        }
        _ => false,
    };
    let digits_from = |start: usize| text[start..].iter().take_while(|b| b.is_ascii_digit()).count();
    let whole = &text[i..i + digits_from(i)];
    i += whole.len();
    let mut fraction: &[u8] = &[];
    if text.get(i) == Some(&b'.') {
        let count = digits_from(i + 1);
        if !whole.is_empty() || count > 0 {
            fraction = &text[i + 1..i + 1 + count];
            i += 1 + count;
        }
    }
    if whole.is_empty() && fraction.is_empty() {
        return None;
    }
    let mut exponent: i64 = 0;
    if matches!(text.get(i), Some(b'e' | b'E')) {
        let mut j = i + 1;
        let exponent_negative = match text.get(j) {
            Some(b'-') => {
                j += 1;
                true
            }
            Some(b'+') => {
                j += 1;
                false
            }
            _ => false,
        };
        let count = digits_from(j);
        if count > 0 {
            // A clamp at 10^6 decides nothing for mantissas shorter than ~10^6 digits: those
            // have overflowed or underflowed long before.
            let magnitude = text[j..j + count]
                .iter()
                .fold(0i64, |acc, b| (acc * 10 + i64::from(b - b'0')).min(1_000_000));
            exponent = if exponent_negative { -magnitude } else { magnitude };
            i = j + count;
        }
    }
    // Rust's parser is correctly rounded for any length, as `_Py_dg_strtod` is.
    let mut normal = String::with_capacity(whole.len() + fraction.len() + 16);
    if negative {
        normal.push('-');
    }
    normal.push_str(if whole.is_empty() { "0" } else { std::str::from_utf8(whole).ok()? });
    normal.push('.');
    normal.push_str(if fraction.is_empty() { "0" } else { std::str::from_utf8(fraction).ok()? });
    normal.push('e');
    normal.push_str(&exponent.to_string());
    Some((normal.parse().ok()?, i))
}

/// `_Py_parse_inf_or_nan`.
fn inf_or_nan(text: &[u8]) -> Option<(f64, usize)> {
    let (negative, start) = match text.first() {
        Some(b'-') => (true, 1),
        Some(b'+') => (false, 1),
        _ => (false, 0),
    };
    let rest = &text[start..];
    let starts = |word: &[u8]| rest.len() >= word.len() && rest[..word.len()].eq_ignore_ascii_case(word);
    let (magnitude, used) = if starts(b"infinity") {
        (f64::INFINITY, 8)
    } else if starts(b"inf") {
        (f64::INFINITY, 3)
    } else if starts(b"nan") {
        (f64::NAN, 3)
    } else {
        return None;
    };
    Some((if negative { -magnitude } else { magnitude }, start + used))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_cases() {
        assert_eq!(parse_float(" 1_0.5 "), Ok(10.5));
        assert_eq!(parse_float("1.e5"), Ok(100000.0));
        assert_eq!(parse_float(".5"), Ok(0.5));
        assert_eq!(parse_float("-InFiNiTy"), Ok(f64::NEG_INFINITY));
        assert!(parse_float("-nan").unwrap().is_nan());
        assert!(parse_float("-0").unwrap().is_sign_negative());
        assert_eq!(parse_float("1e999999999999"), Ok(f64::INFINITY));
        assert_eq!(parse_float("\u{0663}.\u{0665}"), Ok(3.5));
        for bad in
            ["", " ", ".", "e5", "1_", "_1", "1__0", "1_.5", "1._5", "infin", "nanx", "0x10", "1e", "1\x1c"]
        {
            assert_eq!(
                parse_float(bad).unwrap_err().message,
                format!("could not convert string to float: {}", repr(bad)),
                "{bad:?}"
            );
        }
    }
}
