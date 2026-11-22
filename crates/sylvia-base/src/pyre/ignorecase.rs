//! `re.compile(re.escape(literal), re.IGNORECASE).search(text, pos)`: CPython 3.12's
//! case-insensitive match of a literal `str` pattern.
//!
//! `re._compiler._compile` turns each pattern character `c` into one of:
//! - an exact `LITERAL c` when `_sre.unicode_iscased(c)` is false;
//! - `LITERAL_UNI_IGNORE lo`, with `lo = _sre.unicode_tolower(c)`, matching a text character
//!   whose `unicode_tolower` is `lo`;
//! - `IN_UNI_IGNORE` over `lo` and `re._casefix._EXTRA_CASES[lo]` when `lo` shares its
//!   lowercase form with other characters (`i` and `ı`, `s` and `ſ`, ...), matching a text
//!   character whose `unicode_tolower` is any of them.
//!
//! The case tables are Python's (`ignorecase_table.rs`), not Rust's newer ones.

use super::ignorecase_table::{CASED, EXTRA_CASES, LOWER};

/// `_sre.unicode_tolower(c)`: the simple lowercase mapping.
pub fn unicode_tolower(c: char) -> char {
    LOWER.binary_search_by_key(&c, |(from, _)| *from).map_or(c, |index| LOWER[index].1)
}

/// `_sre.unicode_iscased(c)`.
pub fn unicode_iscased(c: char) -> bool {
    CASED
        .binary_search_by(|(start, end)| {
            if *end < c {
                std::cmp::Ordering::Less
            } else if *start > c {
                std::cmp::Ordering::Greater
            } else {
                std::cmp::Ordering::Equal
            }
        })
        .is_ok()
}

/// How one pattern character matches.
enum Matcher {
    Exact(char),
    Lower(Vec<char>),
}

impl Matcher {
    fn new(c: char) -> Self {
        if !unicode_iscased(c) {
            return Self::Exact(c);
        }
        let lower = unicode_tolower(c);
        let mut set = vec![lower];
        if let Ok(index) = EXTRA_CASES.binary_search_by_key(&lower, |(key, _)| *key) {
            set.extend_from_slice(EXTRA_CASES[index].1);
        }
        Self::Lower(set)
    }

    fn matches(&self, c: char) -> bool {
        match self {
            Self::Exact(expected) => c == *expected,
            Self::Lower(set) => set.contains(&unicode_tolower(c)),
        }
    }
}

/// The code-point index where `literal` first matches `text` case-insensitively at or after the
/// code point `pos`, as `re.compile(re.escape(literal), re.IGNORECASE).search(text, pos)`
/// reports it (`match.start()`); `None` when it does not match.
pub fn ignorecase_search(text: &str, literal: &str, pos: usize) -> Option<usize> {
    let pattern: Vec<Matcher> = literal.chars().map(Matcher::new).collect();
    let text: Vec<char> = text.chars().collect();
    if pos > text.len() {
        return None;
    }
    let last = text.len().checked_sub(pattern.len())?;
    (pos..=last).find(|&start| pattern.iter().zip(&text[start..]).all(|(matcher, &c)| matcher.matches(c)))
}

#[cfg(test)]
mod tests {
    use super::super::ignorecase_table::UNICODE_VERSION;
    use super::ignorecase_search;

    #[test]
    fn tables_are_python_s() {
        assert_eq!(UNICODE_VERSION, "15.0.0");
    }

    /// Answers from CPython 3.12.11's `re.compile(re.escape(q), re.IGNORECASE).search(t, s)`.
    #[test]
    fn matches_python() {
        let cases: [(&str, &str, usize, Option<usize>); 12] = [
            ("The Kelvin \u{212a} is cold", "kelvin k", 0, Some(4)),
            ("STRASSE stra\u{df}e", "SS", 0, Some(4)),
            ("\u{17f}top sTop", "STOP", 0, Some(0)),
            ("\u{130}stanbul istanbul", "ISTANBUL", 0, Some(0)),
            ("dotless \u{131} I i", "I", 8, Some(8)),
            ("\u{b5} micro \u{3bc}", "\u{39c}", 0, Some(0)),
            ("\u{1c5} \u{1c6} \u{1c4}", "\u{1c6}", 0, Some(0)),
            ("no match here", "zzz", 0, None),
            ("Ab aB ab", "AB", 3, Some(3)),
            ("x", "x", 5, None),
            ("\u{a7b5} \u{a7b4}", "\u{a7b5}", 1, Some(2)),
            ("a.b a-b", "A-B", 0, Some(4)),
        ];
        for (text, literal, pos, expected) in cases {
            assert_eq!(ignorecase_search(text, literal, pos), expected, "{text:?} {literal:?} {pos}");
        }
    }
}
