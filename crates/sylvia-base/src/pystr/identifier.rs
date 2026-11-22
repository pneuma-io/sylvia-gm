//! `str.isidentifier()`, on CPython 3.12's Unicode 15.0 tables.
//!
//! Python accepts a first character with `XID_Start` (or `_`) and then `XID_Continue`
//! characters, testing each character without normalizing. The tables are generated from the
//! reference interpreter by `generate_identifier_table.py`.

// Generated; kept exactly as the generator writes it.
#[rustfmt::skip]
#[path = "identifier_table.rs"]
mod table;

fn contains(ranges: &[(u32, u32)], c: char) -> bool {
    let code = u32::from(c);
    let index = ranges.partition_point(|(start, _)| *start <= code);
    index > 0 && code <= ranges[index - 1].1
}

/// `text.isidentifier()`.
pub fn isidentifier(text: &str) -> bool {
    let mut chars = text.chars();
    chars.next().is_some_and(|first| contains(table::START, first))
        && chars.all(|c| contains(table::CONTINUE, c))
}

#[cfg(test)]
mod tests {
    use super::isidentifier;

    #[test]
    fn python_identifiers() {
        assert!(isidentifier("_name1"));
        assert!(isidentifier("Δx"));
        assert!(!isidentifier(""));
        assert!(!isidentifier("1x"));
        assert!(!isidentifier("a-b"));
        assert!(!isidentifier("٣"));
    }
}
