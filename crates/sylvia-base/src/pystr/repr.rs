//! `repr(text)` for a `str`, and `str.isprintable()`, which decides what `repr` escapes.

use std::fmt::Write as _;

// Generated; kept exactly as the generator writes it.
#[rustfmt::skip]
#[path = "printable_table.rs"]
mod table;

/// `c.isprintable()`.
pub fn is_printable(c: char) -> bool {
    let code = u32::from(c);
    let index = table::PRINTABLE.partition_point(|(start, _)| *start <= code);
    index > 0 && code <= table::PRINTABLE[index - 1].1
}

/// `repr(text)`.
///
/// Single quotes unless the text has a single quote and no double quote; backslash escapes
/// for the quote, `\`, tab, newline and carriage return; `\xNN` for other ASCII controls and
/// DEL; non-ASCII characters as they are when printable, otherwise `\xNN`, `\uNNNN` or
/// `\UNNNNNNNN`.
pub fn repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') { '"' } else { '\'' };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for c in text.chars() {
        let code = u32::from(c);
        match c {
            _ if c == quote || c == '\\' => {
                out.push('\\');
                out.push(c);
            }
            '\t' => out.push_str("\\t"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            _ if code < 0x20 || code == 0x7f => {
                let _ = write!(out, "\\x{code:02x}");
            }
            _ if code < 0x7f || is_printable(c) => out.push(c),
            _ if code <= 0xff => {
                let _ = write!(out, "\\x{code:02x}");
            }
            _ if code <= 0xffff => {
                let _ = write!(out, "\\u{code:04x}");
            }
            _ => {
                let _ = write!(out, "\\U{code:08x}");
            }
        }
    }
    out.push(quote);
    out
}

/// `repr(value)` for a JSON value as `json.loads` gives it to Python: `None`, `True`/`False`,
/// integers, floats as [`crate::pyjson::float_repr`] writes them (which is Python's `repr`),
/// strings as [`repr`], lists as `[a, b]` and dicts as `{'key': value}` in their key order.
pub fn repr_json(value: &crate::Json) -> String {
    let mut out = String::new();
    write_json(&mut out, value);
    out
}

fn write_json(out: &mut String, value: &crate::Json) {
    use crate::Json;
    match value {
        Json::Null => out.push_str("None"),
        Json::Bool(true) => out.push_str("True"),
        Json::Bool(false) => out.push_str("False"),
        Json::Number(number) => {
            if let Some(int) = number.as_i64() {
                let _ = write!(out, "{int}");
            } else if let Some(int) = number.as_u64() {
                let _ = write!(out, "{int}");
            } else {
                let float = number.as_f64().expect("a JSON number");
                out.push_str(&crate::pyjson::float_repr(float));
            }
        }
        Json::String(text) => out.push_str(&repr(text)),
        Json::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write_json(out, item);
            }
            out.push(']');
        }
        Json::Object(map) => {
            out.push('{');
            for (index, (key, item)) in map.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                out.push_str(&repr(key));
                out.push_str(": ");
                write_json(out, item);
            }
            out.push('}');
        }
    }
}

/// `"%.200R" % text`: the repr cut to its first 200 characters, as several CPython error
/// messages format their argument (the closing quote is lost when the cut falls before it).
pub fn repr_200(text: &str) -> String {
    repr(text).chars().take(200).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn repr_matches_python() {
        assert_eq!(table::UNICODE_VERSION, "15.0.0");
        assert_eq!(repr("plain"), "'plain'");
        assert_eq!(repr("5'"), "\"5'\"");
        assert_eq!(repr("a'b\"c"), "'a\\'b\"c'");
        assert_eq!(repr("\t\u{7f}\u{200b}\u{a0}é🎲"), "'\\t\\x7f\\u200b\\xa0é🎲'");
        assert!(is_printable('é') && !is_printable('\u{200b}') && !is_printable('\u{a0}'));
    }
}
