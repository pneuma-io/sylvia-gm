//! The few Python text and value conversions the store's checks depend on.

use rusqlite::types::Value;

/// The code points Python's `str.isspace()` accepts (CPython 3.12, Unicode 15.0), which is
/// what `str.split()` and `str.strip()` without arguments use. Rust's `char::is_whitespace`
/// differs: it leaves out U+001C to U+001F. Checked against `sql/schema_vectors.json`.
pub const PY_WHITESPACE: [char; 29] = [
    '\u{9}', '\u{a}', '\u{b}', '\u{c}', '\u{d}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{1f}', '\u{20}', '\u{85}',
    '\u{a0}', '\u{1680}', '\u{2000}', '\u{2001}', '\u{2002}', '\u{2003}', '\u{2004}', '\u{2005}', '\u{2006}',
    '\u{2007}', '\u{2008}', '\u{2009}', '\u{200a}', '\u{2028}', '\u{2029}', '\u{202f}', '\u{205f}',
    '\u{3000}',
];

/// Python's `str.isspace()` for one character.
pub fn is_py_whitespace(c: char) -> bool {
    PY_WHITESPACE.contains(&c)
}

/// Python's `str.strip()` with no arguments.
pub fn py_strip(text: &str) -> &str {
    text.trim_matches(is_py_whitespace)
}

/// Non-ASCII characters whose `str.casefold()` is entirely ASCII (CPython 3.12). Every other
/// non-ASCII character folds to something non-ASCII. Checked against `sql/schema_vectors.json`.
const CASEFOLD_TO_ASCII: [(char, &str); 11] = [
    ('\u{df}', "ss"),
    ('\u{17f}', "s"),
    ('\u{1e9e}', "ss"),
    ('\u{212a}', "k"),
    ('\u{fb00}', "ff"),
    ('\u{fb01}', "fi"),
    ('\u{fb02}', "fl"),
    ('\u{fb03}', "ffi"),
    ('\u{fb04}', "ffl"),
    ('\u{fb05}', "st"),
    ('\u{fb06}', "st"),
];

/// The ASCII text a non-ASCII character casefolds to, when it folds to ASCII at all.
pub fn casefold_to_ascii(c: char) -> Option<&'static str> {
    CASEFOLD_TO_ASCII.iter().find(|(from, _)| *from == c).map(|(_, to)| *to)
}

/// `" ".join(text.split()).casefold()`, exact wherever the result is compared with ASCII text.
///
/// The store only ever compares this against identities built from its own ASCII SQL, so a
/// non-ASCII character matters only when it folds to ASCII; those are folded exactly and any
/// other non-ASCII character is kept, which can never equal ASCII text.
pub fn normalized_casefold(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for word in text.split(is_py_whitespace).filter(|word| !word.is_empty()) {
        if !out.is_empty() {
            out.push(' ');
        }
        for c in word.chars() {
            if c.is_ascii() {
                out.push(c.to_ascii_lowercase());
            } else if let Some(folded) = casefold_to_ascii(c) {
                out.push_str(folded);
            } else {
                out.push(c);
            }
        }
    }
    out
}

/// Python's `str(value)` of a value read from SQLite, exact wherever it is compared with
/// identity text (names and hex digests). Text and integers are exact; anything else renders
/// with a prefix no stored identity can have.
pub fn py_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_owned(),
        Value::Integer(number) => number.to_string(),
        Value::Real(number) => sylvia_base::pyjson::float_repr(*number),
        Value::Text(text) => text.clone(),
        Value::Blob(bytes) => format!("b'{}'", String::from_utf8_lossy(bytes)),
    }
}

/// Python's `int(value)` of a value read from SQLite, or `None` where Python raises.
///
/// Integers are exact and floats truncate toward zero. Text and blobs accept what `int()`
/// accepts for ASCII input: surrounding whitespace, a sign, and digits with single
/// underscores between them.
pub fn py_int(value: &Value) -> Option<i64> {
    match value {
        Value::Null => None,
        Value::Integer(number) => Some(*number),
        Value::Real(number) => {
            if number.is_finite() {
                // `int()` truncates toward zero; values outside i64 are refused here.
                let truncated = number.trunc();
                (-9.223_372_036_854_776e18..9.223_372_036_854_776e18)
                    .contains(&truncated)
                    .then_some(truncated as i64)
            } else {
                None
            }
        }
        Value::Text(text) => py_int_text(text),
        Value::Blob(bytes) => std::str::from_utf8(bytes).ok().and_then(py_int_text),
    }
}

fn py_int_text(text: &str) -> Option<i64> {
    let text = py_strip(text);
    let (negative, digits) = match text.as_bytes().first() {
        Some(b'-') => (true, &text[1..]),
        Some(b'+') => (false, &text[1..]),
        _ => (false, text),
    };
    if digits.is_empty() || digits.starts_with('_') || digits.ends_with('_') || digits.contains("__") {
        return None;
    }
    let cleaned: String = digits.chars().filter(|c| *c != '_').collect();
    if !cleaned.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let magnitude: i128 = cleaned.parse().ok()?;
    i64::try_from(if negative { -magnitude } else { magnitude }).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn strip_and_split_follow_python() {
        assert_eq!(py_strip("\u{1c} x \u{85}"), "x");
        assert_eq!(normalized_casefold("CREATE\u{1f}TABLE  T (\n A )"), "create table t ( a )");
        assert_eq!(normalized_casefold("Stra\u{df}e \u{212a}"), "strasse k");
        assert_eq!(normalized_casefold("\u{e9}"), "\u{e9}");
    }

    #[test]
    fn int_and_str_follow_python() {
        assert_eq!(py_int(&Value::Text(" 1_2 ".into())), Some(12));
        assert_eq!(py_int(&Value::Text("1__2".into())), None);
        assert_eq!(py_int(&Value::Real(-2.9)), Some(-2));
        assert_eq!(py_int(&Value::Null), None);
        assert_eq!(py_str(&Value::Null), "None");
        assert_eq!(py_str(&Value::Real(5.0)), "5.0");
    }
}
