//! `len(text)` for a Python `str`.

/// `len(text)`: the number of code points. Rust's `str::len` counts UTF-8 bytes; use that
/// only where Python measured `bytes` (`len(text.encode("utf-8"))`).
pub fn len(text: &str) -> usize {
    text.chars().count()
}

#[cfg(test)]
mod tests {
    #[test]
    fn counts_code_points() {
        assert_eq!(super::len("Straße"), 6);
        assert_eq!(super::len("😀"), 1);
        assert_eq!(super::len(""), 0);
    }
}
