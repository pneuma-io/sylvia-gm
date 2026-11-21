//! `str.isspace()` and argument-less `str.strip()`, `str.lstrip()` and `str.rstrip()`.
//!
//! Python's whitespace is the characters with bidirectional class WS, B or S, or category Zs.
//! It includes the information separators U+001C to U+001F, which Rust's
//! `char::is_whitespace` (the Unicode `White_Space` property) does not, so `str::trim` strips
//! less than Python does. The table is generated from the reference interpreter by
//! `generate_whitespace_table.py`.

// Generated; kept exactly as the generator writes it.
#[rustfmt::skip]
#[path = "whitespace_table.rs"]
mod table;

/// `character.isspace()` for one character.
pub fn is_space(character: char) -> bool {
    table::WHITESPACE.binary_search(&character).is_ok()
}

/// `text.isspace()`: nonempty and every character is whitespace.
pub fn isspace(text: &str) -> bool {
    !text.is_empty() && text.chars().all(is_space)
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn table_is_sorted_python_3_12_whitespace() {
        assert!(table::WHITESPACE.windows(2).all(|pair| pair[0] < pair[1]));
        assert_eq!(table::UNICODE_VERSION, "15.0.0");
        assert_eq!(table::WHITESPACE.len(), 29);
    }

    #[test]
    fn python_whitespace_is_not_rust_whitespace() {
        assert!(!'\x1c'.is_whitespace() && is_space('\x1c'));
        assert_eq!(strip("\x1c a \x1f"), "a");
        assert_eq!(strip("\u{200b}a"), "\u{200b}a");
        assert_eq!(rstrip(" a\u{3000}"), " a");
        assert_eq!(lstrip("\u{85}a "), "a ");
        assert!(isspace("\x1e\u{2028}") && !isspace("") && !isspace(" a"));
    }
}
