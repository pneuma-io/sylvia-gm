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

/// `text.casefold()`. An ASCII character folds to its lowercase (the table's own mapping, which
/// a test checks), so only other characters are looked up.
pub fn casefold(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for character in text.chars() {
        if character.is_ascii() {
            out.push(character.to_ascii_lowercase());
            continue;
        }
        match table::CASEFOLD.binary_search_by_key(&character, |(from, _)| *from) {
            Ok(index) => out.push_str(table::CASEFOLD[index].1),
            Err(_) => out.push(character),
        }
    }
    out
}

/// What `str.casefold()` turns one non-ASCII character into, or `None` when it is unchanged (an
/// ASCII character folds to its lowercase).
pub fn casefold_char(character: char) -> Option<&'static str> {
    table::CASEFOLD
        .binary_search_by_key(&character, |(from, _)| *from)
        .ok()
        .map(|index| table::CASEFOLD[index].1)
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
    fn ascii_folds_to_its_lowercase_as_the_table_does() {
        for code in 0u8..128 {
            let character = char::from(code);
            let text = character.to_string();
            assert_eq!(casefold(&text), mapped(table::CASEFOLD, &text), "{code}");
        }
    }

    #[test]
    fn folding_uses_full_mappings() {
        assert_eq!(casefold("Straße ǅ ﬁ İ"), "strasse ǆ fi i\u{307}");
        assert_eq!(upper("straße ı ﬀ"), "STRASSE I FF");
    }
}
