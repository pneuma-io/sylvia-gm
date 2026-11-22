//! `str.isalnum()` for one character, on CPython 3.12's Unicode 15.0 table.
//!
//! Python's alphanumerics are the letters (categories `Lu`, `Ll`, `Lt`, `Lm`, `Lo`) and every
//! character with a numeric value; Rust's `char::is_alphanumeric` also counts the marks with the
//! `Other_Alphabetic` property and follows a newer Unicode. The table is generated from the
//! reference interpreter by `generate_alnum_table.py`.

// Generated; kept exactly as the generator writes it.
#[rustfmt::skip]
#[path = "alnum_table.rs"]
mod table;

/// `c.isalnum()`.
pub fn is_alnum(c: char) -> bool {
    let code = u32::from(c);
    let index = table::ALNUM.partition_point(|(start, _)| *start <= code);
    index > 0 && code <= table::ALNUM[index - 1].1
}

#[cfg(test)]
mod tests {
    #[test]
    fn the_generated_table_is_present() {
        assert!(super::table::ALNUM.len() > 100);
    }
}
