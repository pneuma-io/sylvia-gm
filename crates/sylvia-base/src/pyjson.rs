//! JSON text exactly as Python's `json.dumps` writes it.
//!
//! Sylvia hashes and stores JSON in many places (package locks, closure IDs, action receipts,
//! install identities). Two encoders that agree on the parsed value can still disagree on the
//! bytes, and then every hash differs. This module reproduces CPython's encoder:
//!
//! - floats use Python's `repr` (`1e+16`, `1e-05`, `100.0`), not Rust's `Display`;
//! - integers and floats stay distinct (`1` and `1.0`);
//! - `ensure_ascii` escapes with lowercase `\uXXXX` and surrogate pairs;
//! - separators, key sorting and indentation follow the same rules as `json.dumps`.
//!
//! Reference vectors generated from the frozen Python snapshot are in `tests/vectors`.
//!
//! [`loads`] refuses text Python refuses with Python's `JSONDecodeError` text, located as
//! CPython's scanner locates it (`decode_error`).
//!
//! [`loads_hooked`] reads as `json.loads(text, object_pairs_hook=..., parse_constant=...)` with
//! hooks that refuse duplicate keys and the `NaN`/`Infinity` literals, reporting the first failure
//! in CPython's scan order ([`HookFailure`]).
//!
//! Known, declared limits (see `docs/rust-port/differences.md`):
//! - integers outside `i64`/`u64` are rejected by [`loads`]; Python accepts arbitrary size;
//! - lone UTF-16 surrogates cannot exist in a Rust `String`; Python strings can hold them;
//! - the `NaN`/`Infinity` literals Python accepts on input are rejected, and so is a number
//!   beyond the float range, which Python reads as infinity.
//! - JSON nested 128 or more arrays or objects deep is rejected by [`loads`] (`serde_json`'s
//!   recursion limit; 127 levels read); Python reads roughly a thousand, its interpreter stack.

mod decode_error;

pub use decode_error::{loads_hooked, HookFailure, HookPolicy, Hooked, JsonDecodeError};

use crate::{CodedError, Json};
use std::fmt::Write as _;

/// The options of one `json.dumps` call.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Dump {
    /// `sort_keys=`: order object keys by Unicode code point.
    pub sort_keys: bool,
    /// `ensure_ascii=`: escape every non-ASCII character.
    pub ensure_ascii: bool,
    /// First element of `separators=`.
    pub item_separator: &'static str,
    /// Second element of `separators=`.
    pub key_separator: &'static str,
    /// `indent=` as a number of spaces.
    pub indent: Option<usize>,
}

impl Dump {
    /// `json.dumps(value)`.
    pub const DEFAULT: Dump = Dump {
        sort_keys: false,
        ensure_ascii: true,
        item_separator: ", ",
        key_separator: ": ",
        indent: None,
    };

    /// `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`:
    /// the encoding of `domain.package_library.canonical_json_bytes`.
    pub const CANONICAL: Dump =
        Dump { sort_keys: true, ensure_ascii: false, item_separator: ",", key_separator: ":", indent: None };

    /// `json.dumps(value, ensure_ascii=True, separators=(",", ":"))`: one owner protocol line.
    pub const WIRE: Dump =
        Dump { sort_keys: false, ensure_ascii: true, item_separator: ",", key_separator: ":", indent: None };

    /// `json.dumps(value, indent=2)`. With `indent`, Python's default item separator is `","`.
    pub const INDENT2: Dump = Dump {
        sort_keys: false,
        ensure_ascii: true,
        item_separator: ",",
        key_separator: ": ",
        indent: Some(2),
    };

    /// Same options with `sort_keys` set.
    #[must_use]
    pub const fn sorted(mut self, sort_keys: bool) -> Self {
        self.sort_keys = sort_keys;
        self
    }

    /// Same options with `ensure_ascii` set.
    #[must_use]
    pub const fn ascii(mut self, ensure_ascii: bool) -> Self {
        self.ensure_ascii = ensure_ascii;
        self
    }

    /// Same options with `separators=(item, key)`.
    #[must_use]
    pub const fn separators(mut self, item: &'static str, key: &'static str) -> Self {
        self.item_separator = item;
        self.key_separator = key;
        self
    }
}

/// `json.dumps(value, **options)`.
pub fn dumps(value: &Json, options: Dump) -> String {
    let mut out = String::new();
    write_value(&mut out, value, options, 0);
    out
}

/// `json.dumps(value, **options).encode("utf-8")`.
pub fn dump_bytes(value: &Json, options: Dump) -> Vec<u8> {
    dumps(value, options).into_bytes()
}

/// `canonical_json_bytes(value)`: the bytes Sylvia hashes for package and closure identity.
pub fn canonical_bytes(value: &Json) -> Vec<u8> {
    dump_bytes(value, Dump::CANONICAL)
}

/// `json.loads(text)` for the inputs Sylvia accepts.
///
/// Stricter than Python in the declared ways listed in the module documentation, so a value
/// never silently changes between the two implementations. Text Python refuses gets Python's
/// `JSONDecodeError` and its exact message (`"Expecting value: line 1 column 1 (char 0)"`);
/// only text Python reads and this refuses gets this module's own "Invalid JSON" message.
pub fn loads(text: &str) -> Result<Json, CodedError> {
    let parsed = python_integers(text).and_then(|rewritten| {
        serde_json::from_str(&rewritten)
            .map_err(|error| CodedError::rejected(format!("Invalid JSON: {error}")))
    });
    parsed.map_err(|error| match decode_error::first_error(text) {
        Some((message, position)) => CodedError::rejected(decode_error::message(text, message, position))
            .with_error_type("JSONDecodeError"),
        None => error,
    })
}

/// `repr(value)` for a Python float, which is also how `json.dumps` writes it.
///
/// Non-finite values give the literals `json.dumps` emits with `allow_nan=True`. A
/// `serde_json::Value` cannot hold them, so [`dumps`] never produces these.
pub fn float_repr(value: f64) -> String {
    if value.is_nan() {
        return "NaN".to_owned();
    }
    if value.is_infinite() {
        return if value > 0.0 { "Infinity" } else { "-Infinity" }.to_owned();
    }
    if value == 0.0 {
        return if value.is_sign_negative() { "-0.0" } else { "0.0" }.to_owned();
    }
    let (digits, exponent) = shortest_digits(value.abs());
    let count = digits.len() as i32;
    // The value is 0.d1d2...dn x 10^decimal_point, CPython's `decpt`.
    let decimal_point = exponent + 1;

    let mut out = String::with_capacity(digits.len() + 8);
    if value < 0.0 {
        out.push('-');
    }
    if decimal_point <= -4 || decimal_point > 16 {
        out.push_str(&digits[..1]);
        if count > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        let shown = decimal_point - 1;
        out.push('e');
        out.push(if shown < 0 { '-' } else { '+' });
        let _ = write!(out, "{:02}", shown.abs());
    } else if decimal_point <= 0 {
        out.push_str("0.");
        out.extend(std::iter::repeat_n('0', (-decimal_point) as usize));
        out.push_str(&digits);
    } else if decimal_point >= count {
        out.push_str(&digits);
        out.extend(std::iter::repeat_n('0', (decimal_point - count) as usize));
        out.push_str(".0");
    } else {
        let split = decimal_point as usize;
        out.push_str(&digits[..split]);
        out.push('.');
        out.push_str(&digits[split..]);
    }
    out
}

/// Shortest decimal digits that read back as `value` (positive, finite, non-zero), and the
/// decimal exponent of the first digit.
///
/// Rust's `{:e}` and CPython's `repr` both print the shortest such digits. They differ in one
/// case: when the exact value lies exactly halfway between two equally short candidates. Rust
/// takes the upper one; CPython's dtoa rounds half to even. `104902988213992.125` prints as
/// `...992.13` in Rust and `...992.12` in Python. This function follows Python.
fn shortest_digits(value: f64) -> (String, i32) {
    let split = |text: &str| -> (String, i32) {
        let (mantissa, exponent) = text.split_once('e').expect("LowerExp always has an exponent");
        (
            mantissa.chars().filter(|c| *c != '.').collect(),
            exponent.parse().expect("LowerExp exponent is an integer"),
        )
    };
    let (digits, exponent) = split(&format!("{value:e}"));
    let count = digits.len();
    // Cheap test first: one more correctly rounded digit. Only a 5 there can be a tie.
    let (wider, _) = split(&format!("{value:.count$e}"));
    if wider.as_bytes()[count] != b'5' {
        return (digits, exponent);
    }
    // The exact decimal expansion of a double has at most 767 significant digits.
    let (exact, exact_exponent) = split(&format!("{value:.780e}"));
    let exact = exact.trim_end_matches('0');
    if exact.len() != count + 1 || !exact.ends_with('5') {
        return (digits, exponent);
    }
    let lower = &exact[..count];
    let last_is_even = (lower.as_bytes()[count - 1] - b'0').is_multiple_of(2);
    let reads_back = format!("{}.{}e{exact_exponent}", &lower[..1], &lower[1..])
        .parse::<f64>()
        .is_ok_and(|parsed| parsed == value);
    if last_is_even && reads_back {
        (lower.to_owned(), exact_exponent)
    } else {
        (digits, exponent)
    }
}

fn write_value(out: &mut String, value: &Json, options: Dump, level: usize) {
    match value {
        Json::Null => out.push_str("null"),
        Json::Bool(true) => out.push_str("true"),
        Json::Bool(false) => out.push_str("false"),
        Json::Number(number) => write_number(out, number),
        Json::String(text) => write_string(out, text, options.ensure_ascii),
        Json::Array(items) => {
            if items.is_empty() {
                out.push_str("[]");
                return;
            }
            out.push('[');
            write_newline(out, options, level + 1);
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(options.item_separator);
                    write_newline(out, options, level + 1);
                }
                write_value(out, item, options, level + 1);
            }
            write_newline(out, options, level);
            out.push(']');
        }
        Json::Object(map) => {
            if map.is_empty() {
                out.push_str("{}");
                return;
            }
            let mut entries: Vec<(&String, &Json)> = map.iter().collect();
            if options.sort_keys {
                // Python compares str by code point; UTF-8 byte order is the same order.
                entries.sort_by(|left, right| left.0.as_bytes().cmp(right.0.as_bytes()));
            }
            out.push('{');
            write_newline(out, options, level + 1);
            for (index, (key, item)) in entries.into_iter().enumerate() {
                if index > 0 {
                    out.push_str(options.item_separator);
                    write_newline(out, options, level + 1);
                }
                write_string(out, key, options.ensure_ascii);
                out.push_str(options.key_separator);
                write_value(out, item, options, level + 1);
            }
            write_newline(out, options, level);
            out.push('}');
        }
    }
}

fn write_newline(out: &mut String, options: Dump, level: usize) {
    if let Some(width) = options.indent {
        out.push('\n');
        out.extend(std::iter::repeat_n(' ', width * level));
    }
}

fn write_number(out: &mut String, number: &serde_json::Number) {
    if let Some(value) = number.as_i64() {
        let _ = write!(out, "{value}");
    } else if let Some(value) = number.as_u64() {
        let _ = write!(out, "{value}");
    } else {
        out.push_str(&float_repr(number.as_f64().expect("a JSON number is an integer or a float")));
    }
}

fn write_string(out: &mut String, text: &str, ensure_ascii: bool) {
    out.push('"');
    for character in text.chars() {
        match character {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            control if (control as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", control as u32);
            }
            // CPython's ASCII encoder treats everything outside space..tilde as escapable,
            // which includes DEL (0x7f).
            other if ensure_ascii && (other as u32) > 0x7e => {
                let mut units = [0u16; 2];
                for unit in other.encode_utf16(&mut units) {
                    let _ = write!(out, "\\u{unit:04x}");
                }
            }
            other => out.push(other),
        }
    }
    out.push('"');
}

/// Make integer literals mean what they mean to Python before `serde_json` reads them.
///
/// - Literals that do not fit `i64`/`u64` are refused. `serde_json` would silently read one as
///   a float and print it back with an exponent; Python keeps every digit.
/// - `-0` is the integer zero in Python and prints as `0`. `serde_json` reads it as the float
///   `-0.0`. It is rewritten to `0`.
fn python_integers(text: &str) -> Result<std::borrow::Cow<'_, str>, CodedError> {
    let bytes = text.as_bytes();
    let mut rewritten: Option<String> = None;
    let mut copied = 0;
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'"' => {
                index += 1;
                while index < bytes.len() && bytes[index] != b'"' {
                    index += if bytes[index] == b'\\' { 2 } else { 1 };
                }
                index += 1;
            }
            b'-' | b'0'..=b'9' => {
                let start = index;
                index += 1;
                while index < bytes.len()
                    && matches!(bytes[index], b'0'..=b'9' | b'.' | b'e' | b'E' | b'+' | b'-')
                {
                    index += 1;
                }
                let token = &text[start..index];
                if token.contains(['.', 'e', 'E']) {
                    continue;
                }
                if token == "-0" {
                    let out = rewritten.get_or_insert_with(String::new);
                    out.push_str(&text[copied..start]);
                    out.push('0');
                    copied = index;
                } else if token.parse::<i64>().is_err() && token.parse::<u64>().is_err() {
                    return Err(CodedError::rejected(format!(
                        "Invalid JSON: integer {token} is outside the supported 64-bit range"
                    )));
                }
            }
            _ => index += 1,
        }
    }
    Ok(match rewritten {
        Some(mut out) => {
            out.push_str(&text[copied..]);
            std::borrow::Cow::Owned(out)
        }
        None => std::borrow::Cow::Borrowed(text),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    // The tie cases are written out exactly on purpose.
    #[allow(clippy::excessive_precision)]
    fn float_notation_switches_where_python_does() {
        for (value, expected) in [
            (1.0, "1.0"),
            (0.1, "0.1"),
            (1e15, "1000000000000000.0"),
            (1e16, "1e+16"),
            (1.5e16, "1.5e+16"),
            (1e-4, "0.0001"),
            (1e-5, "1e-05"),
            (1.5e-5, "1.5e-05"),
            (-2.5, "-2.5"),
            (123456789.125, "123456789.125"),
            (5e-324, "5e-324"),
            (1.7976931348623157e308, "1.7976931348623157e+308"),
            (0.1 + 0.2, "0.30000000000000004"),
            (-0.0, "-0.0"),
            // Exactly halfway between two 17-digit candidates: Python rounds half to even.
            (104902988213992.125, "104902988213992.12"),
            (1782137017160129.25, "1782137017160129.2"),
        ] {
            assert_eq!(float_repr(value), expected);
        }
    }

    #[test]
    fn wide_integers_are_refused_not_rounded() {
        assert!(loads("18446744073709551615").is_ok());
        assert!(loads("-9223372036854775808").is_ok());
        assert!(loads("18446744073709551616").is_err());
        assert!(loads(r#"{"a":[1,123456789012345678901234567890]}"#).is_err());
        // The same digits inside a string or as a float are fine.
        assert!(loads(r#""123456789012345678901234567890""#).is_ok());
        assert!(loads("123456789012345678901234567890.0").is_ok());
        assert!(loads(r#""escaped \" 99999999999999999999999""#).is_ok());
    }

    #[test]
    fn nesting_is_limited_to_127_levels() {
        let nested = |depth: usize| format!("{}{}", "[".repeat(depth), "]".repeat(depth));
        assert!(loads(&nested(127)).is_ok());
        assert!(loads(&nested(128)).is_err());
        let objects = |depth: usize| format!("{}1{}", "{\"a\":".repeat(depth), "}".repeat(depth));
        assert!(loads(&objects(127)).is_ok());
        assert!(loads(&objects(128)).is_err());
    }

    #[test]
    fn negative_zero_integer_is_zero() {
        assert_eq!(
            dumps(&loads("[-0, -0.0, \"-0\", {\"k\":-0}]").unwrap(), Dump::WIRE),
            r#"[0,-0.0,"-0",{"k":0}]"#
        );
    }

    #[test]
    fn duplicate_keys_keep_first_position_and_last_value() {
        let value = loads(r#"{"dup":1,"other":2,"dup":3}"#).unwrap();
        assert_eq!(dumps(&value, Dump::WIRE), r#"{"dup":3,"other":2}"#);
    }
}
