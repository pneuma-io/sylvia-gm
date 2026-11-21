//! The few Python text and value conversions the store's checks depend on.
//!
//! Python's whitespace set, `strip` and `casefold` are the shared ones in `sylvia_base::pystr`;
//! only the conversions of values read from SQLite (`str(value)`, `int(value)`) live here.

use rusqlite::types::Value;
use sylvia_base::pystr;

/// Python's `str.strip()` with no arguments.
pub fn py_strip(text: &str) -> &str {
    pystr::strip(text)
}

/// `" ".join(text.split()).casefold()`.
pub fn normalized_casefold(text: &str) -> String {
    let words: Vec<&str> = text.split(pystr::is_space).filter(|word| !word.is_empty()).collect();
    pystr::casefold(&words.join(" "))
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
        assert_eq!(normalized_casefold("\u{c9}"), "\u{e9}");
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
