//! `str.casefold()` and `str.upper()` on CPython 3.12's Unicode 15.0 tables.
//!
//! Both use Python's full mappings, so one character can become several (`ß` folds to `ss`,
//! `ﬁ` uppercases to `FI`). Rust's `to_lowercase` is not case folding and its tables follow a
//! newer Unicode version, so the table is generated from the reference interpreter by
//! `generate_casefold_table.py`.

// Generated; kept exactly as the generator writes it.
#[rustfmt::skip]
#[path = "casefold_table.rs"]
mod table;

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
    mapped(table::CASEFOLD, text)
}

/// `text.upper()`.
pub fn upper(text: &str) -> String {
    mapped(table::UPPER, text)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tables_are_sorted_python_3_12_mappings() {
        assert!(table::CASEFOLD.windows(2).all(|pair| pair[0].0 < pair[1].0));
        assert!(table::UPPER.windows(2).all(|pair| pair[0].0 < pair[1].0));
        assert_eq!(table::UNICODE_VERSION, "15.0.0");
    }

    #[test]
    fn folding_uses_full_mappings() {
        assert_eq!(casefold("Straße ǅ ﬁ İ"), "strasse ǆ fi i\u{307}");
        assert_eq!(upper("straße ı ﬀ"), "STRASSE I FF");
    }
}
