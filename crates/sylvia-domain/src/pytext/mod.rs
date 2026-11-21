//! Python `str` behaviour that Rust's `str` does not share, beyond whitespace.
//!
//! Whitespace (`str.isspace`, `str.strip`) is `sylvia_base::pystr`, shared by every lane.
//!
//! - [`casefold`] / [`upper`]: `str.casefold()` and `str.upper()` with CPython 3.12's
//!   Unicode 15.0 tables, not Rust's newer ones.
//! - [`len`]: `len(str)` counts code points, not UTF-8 bytes.
//!
//! The tables are generated from the reference interpreter by `generate_unicode_tables.py`.
//! Lane 4 is adding `casefold` to `sylvia_base::pystr`; when it lands, [`casefold`] moves there.

// Generated; kept exactly as the generator writes it.
#[rustfmt::skip]
mod unicode_tables;

use unicode_tables::{CASEFOLD, UPPER};

/// The Unicode version of the tables (`unicodedata.unidata_version` of the reference).
pub const UNICODE_VERSION: &str = unicode_tables::UNICODE_VERSION;

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
        assert!(CASEFOLD.windows(2).all(|pair| pair[0].0 < pair[1].0));
        assert!(UPPER.windows(2).all(|pair| pair[0].0 < pair[1].0));
        assert_eq!(UNICODE_VERSION, "15.0.0");
    }

    #[test]
    fn folding_uses_full_mappings() {
        assert_eq!(casefold("Straße ǅ ﬁ İ"), "strasse ǆ fi i\u{307}");
        assert_eq!(upper("straße ı ﬀ"), "STRASSE I FF");
        assert_eq!(len("Straße"), 6);
    }
}
