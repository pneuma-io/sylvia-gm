//! Python `str` behaviour that Rust's `str` does not share.
//!
//! - [`is_space`] / [`strip`]: `str.isspace()` and argument-less `str.strip()`. Python's set
//!   includes the information separators `\x1c`–`\x1f`; Rust's `char::is_whitespace` does not.
//! - [`casefold`] / [`upper`]: `str.casefold()` and `str.upper()` with CPython 3.12's
//!   Unicode 15.0 tables, not Rust's newer ones.
//! - [`len`]: `len(str)` counts code points, not UTF-8 bytes.
//!
//! The tables are generated from the reference interpreter by `generate_unicode_tables.py`.

// Generated; kept exactly as the generator writes it.
#[rustfmt::skip]
mod unicode_tables;

use unicode_tables::{CASEFOLD, UPPER, WHITESPACE};

/// The Unicode version of the tables (`unicodedata.unidata_version` of the reference).
pub const UNICODE_VERSION: &str = unicode_tables::UNICODE_VERSION;

/// `character.isspace()`.
pub fn is_space(character: char) -> bool {
    WHITESPACE.binary_search(&character).is_ok()
}

/// `text.strip()` with no argument.
pub fn strip(text: &str) -> &str {
    text.trim_matches(is_space)
}

/// `text.lstrip()` with no argument.
pub fn lstrip(text: &str) -> &str {
    text.trim_start_matches(is_space)
}

/// `text.rstrip()` with no argument.
pub fn rstrip(text: &str) -> &str {
    text.trim_end_matches(is_space)
}

/// `len(text)` for a Python `str`: the number of code points.
pub fn len(text: &str) -> usize {
    text.chars().count()
}

fn mapped(table: &[(char, &'static str)], text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for character in text.chars() {
        match table.binary_search_by_key(&character, |(from, _)| *from) {
            Ok(index) => out.push_str(table[index].1),
            Err(_) => out.push(character),
        }
    }
    out
}

/// `text.casefold()`.
pub fn casefold(text: &str) -> String {
    mapped(CASEFOLD, text)
}

/// `text.upper()`.
pub fn upper(text: &str) -> String {
    mapped(UPPER, text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tables_are_sorted_for_binary_search() {
        assert!(WHITESPACE.windows(2).all(|pair| pair[0] < pair[1]));
        assert!(CASEFOLD.windows(2).all(|pair| pair[0].0 < pair[1].0));
        assert!(UPPER.windows(2).all(|pair| pair[0].0 < pair[1].0));
        assert_eq!(UNICODE_VERSION, "15.0.0");
    }

    #[test]
    fn python_whitespace_is_not_rust_whitespace() {
        assert_eq!(strip("\x1c a \x1f"), "a");
        assert_eq!(strip("\u{200b}a"), "\u{200b}a");
        assert!(!'\x1c'.is_whitespace() && is_space('\x1c'));
        assert_eq!(rstrip(" a\u{3000}"), " a");
        assert_eq!(lstrip("\u{85}a "), "a ");
    }

    #[test]
    fn folding_uses_full_mappings() {
        assert_eq!(casefold("Straße ǅ ﬁ İ"), "strasse ǆ fi i\u{307}");
        assert_eq!(upper("straße ı ﬀ"), "STRASSE I FF");
        assert_eq!(len("Straße"), 6);
    }
}
