//! `str.splitlines()`: Python's line boundaries, wider than Rust's `lines()`.

/// Whether `c` ends a line for `str.splitlines()`. `\r\n` is one boundary.
fn is_boundary(c: char) -> bool {
    matches!(
        c,
        '\n' | '\r' | '\u{b}' | '\u{c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}'
    )
}

/// `text.splitlines()` (without `keepends`): no empty last line after a final boundary.
pub fn splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((index, c)) = chars.next() {
        if !is_boundary(c) {
            continue;
        }
        lines.push(&text[start..index]);
        start = index + c.len_utf8();
        if c == '\r' && chars.peek().is_some_and(|(_, next)| *next == '\n') {
            chars.next();
            start += 1;
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// `text.splitlines(keepends=True)`: each line with its boundary; the pieces join back to
/// `text`.
pub fn splitlines_keepends(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((index, c)) = chars.next() {
        if !is_boundary(c) {
            continue;
        }
        let mut end = index + c.len_utf8();
        if c == '\r' && chars.peek().is_some_and(|(_, next)| *next == '\n') {
            chars.next();
            end += 1;
        }
        lines.push(&text[start..end]);
        start = end;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::{splitlines, splitlines_keepends};

    #[test]
    fn python_boundaries_kept() {
        assert_eq!(splitlines_keepends(""), Vec::<&str>::new());
        assert_eq!(
            splitlines_keepends("a\r\nb\rc\nd\u{2028}e\u{1c}f"),
            ["a\r\n", "b\r", "c\n", "d\u{2028}", "e\u{1c}", "f"]
        );
        assert_eq!(splitlines_keepends("a\n\nb\n"), ["a\n", "\n", "b\n"]);
    }

    #[test]
    fn python_boundaries() {
        assert_eq!(splitlines(""), Vec::<&str>::new());
        assert_eq!(splitlines("a\r\nb\rc\nd\u{2028}e\u{1c}"), ["a", "b", "c", "d", "e"]);
        assert_eq!(splitlines("a\n\nb\n"), ["a", "", "b"]);
    }
}
