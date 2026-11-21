//! Python `re.search(pattern, text)` for the patterns in the content definitions.
//!
//! The patterns are written for Python's `re`. Three things mean something different to Rust's
//! engines, so a pattern is translated before `fancy_regex` compiles it:
//!
//! - `\s` and `\S` are Python's `str.isspace()` set, which includes U+001C to U+001F; Rust's
//!   `\s` is the Unicode `White_Space` property, which does not.
//! - `$` (no `MULTILINE`) also matches just before a final `\n`; Rust's `$` only at the end.
//! - `\Z` is Python's end of text; Rust spells it `\z`.
//!
//! Everything else the definitions use (`^`, groups, lookahead, classes with ranges, `\x00`,
//! `\u0000`, `.` excluding only `\n`, quantifiers) means the same in both. A construct this
//! translator does not know is refused rather than guessed, so a new pattern cannot silently
//! change meaning; `content_validation_vectors` checks every shipped pattern against Python.

use crate::error::{DomainError, DomainResult};

/// The members of Python's `\s`, written for use inside a Rust character class. The test
/// `whitespace_class_is_python_isspace` checks it against `sylvia_base::pystr::is_space`.
const PY_SPACE: &str =
    r"\t\n\x0B\x0C\r\x1C-\x20\x{85}\x{A0}\x{1680}\x{2000}-\x{200A}\x{2028}\x{2029}\x{202F}\x{205F}\x{3000}";

/// A compiled Python pattern.
#[derive(Debug, Clone)]
pub struct PyRegex {
    source: String,
    compiled: fancy_regex::Regex,
}

impl PyRegex {
    /// Translate and compile `pattern`.
    pub fn new(pattern: &str) -> DomainResult<Self> {
        let translated = translate(pattern)?;
        let compiled = fancy_regex::Regex::new(&translated)
            .map_err(|error| DomainError::value(format!("Pattern {pattern:?} does not compile: {error}")))?;
        Ok(Self { source: pattern.to_owned(), compiled })
    }

    /// `re.search(pattern, text) is not None`.
    pub fn search(&self, text: &str) -> bool {
        // fancy_regex reports a backtracking limit as an error; the shipped patterns are
        // bounded, and treating a limit as "no match" would hide it, so it panics in tests.
        self.compiled
            .is_match(text)
            .unwrap_or_else(|error| panic!("pattern {:?} failed: {error}", self.source))
    }

    /// The Python source of the pattern.
    pub fn source(&self) -> &str {
        &self.source
    }
}

/// The Rust spelling of a Python pattern.
pub fn translate(pattern: &str) -> DomainResult<String> {
    let unsupported = |what: &str| DomainError::value(format!("Pattern {pattern:?} uses {what}"));
    let mut out = String::with_capacity(pattern.len() + 16);
    let mut chars = pattern.chars().peekable();
    let mut in_class = false;
    let mut class_start = false;
    while let Some(c) = chars.next() {
        let at_class_start = std::mem::take(&mut class_start);
        match c {
            '\\' => {
                let Some(escaped) = chars.next() else { return Err(unsupported("a trailing backslash")) };
                match escaped {
                    's' if in_class => out.push_str(PY_SPACE),
                    'S' if in_class => {
                        out.push_str("[^");
                        out.push_str(PY_SPACE);
                        out.push(']');
                    }
                    's' => {
                        out.push('[');
                        out.push_str(PY_SPACE);
                        out.push(']');
                    }
                    'S' => {
                        out.push_str("[^");
                        out.push_str(PY_SPACE);
                        out.push(']');
                    }
                    'Z' if !in_class => out.push_str(r"\z"),
                    'A' if !in_class => out.push_str(r"\A"),
                    'x' => {
                        let digits: String = (0..2).filter_map(|_| chars.next()).collect();
                        if digits.len() != 2 || !digits.chars().all(|d| d.is_ascii_hexdigit()) {
                            return Err(unsupported("a short \\x escape"));
                        }
                        out.push_str(r"\x");
                        out.push_str(&digits);
                    }
                    'u' => {
                        let digits: String = (0..4).filter_map(|_| chars.next()).collect();
                        if digits.len() != 4 || !digits.chars().all(|d| d.is_ascii_hexdigit()) {
                            return Err(unsupported("a short \\u escape"));
                        }
                        out.push_str(r"\x{");
                        out.push_str(&digits);
                        out.push('}');
                    }
                    // Escaped punctuation is a literal in both dialects.
                    p if p.is_ascii_punctuation() => {
                        out.push('\\');
                        out.push(p);
                    }
                    other => return Err(unsupported(&format!("the escape \\{other}"))),
                }
            }
            '[' if !in_class => {
                in_class = true;
                class_start = true;
                out.push('[');
                if chars.peek() == Some(&'^') {
                    chars.next();
                    out.push('^');
                }
            }
            // `]` first in a Python class is a literal.
            ']' if in_class && at_class_start => out.push_str(r"\]"),
            ']' if in_class => {
                in_class = false;
                out.push(']');
            }
            '[' if in_class => out.push_str(r"\["),
            // Python reads these literally inside a class; Rust gives them set meanings.
            '&' | '~' | '-' if in_class && chars.peek() == Some(&c) => {
                out.push('\\');
                out.push(c);
            }
            '$' if !in_class => out.push_str(r"(?=\n?\z)"),
            '(' if !in_class && chars.peek() == Some(&'?') => {
                out.push('(');
                let mut lookahead = chars.clone();
                lookahead.next();
                match lookahead.next() {
                    Some(':' | '=' | '!') => {}
                    Some('<') if matches!(lookahead.next(), Some('=' | '!')) => {}
                    _ => return Err(unsupported("a group extension other than (?: (?= (?! (?<= (?<!")),
                }
            }
            other => out.push(other),
        }
    }
    if in_class {
        return Err(unsupported("an unterminated class"));
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn whitespace_class_is_python_isspace() {
        let class = fancy_regex::Regex::new(&format!("^[{PY_SPACE}]$")).unwrap();
        for code in 0..=0x10FFFF_u32 {
            let Some(c) = char::from_u32(code) else { continue };
            assert_eq!(
                class.is_match(&c.to_string()).unwrap(),
                sylvia_base::pystr::is_space(c),
                "U+{code:04X}"
            );
        }
    }

    #[test]
    fn dollar_matches_before_a_final_newline() {
        let pattern = PyRegex::new("^[0-9a-f]{4}$").unwrap();
        assert!(pattern.search("abcd"));
        assert!(pattern.search("abcd\n"));
        assert!(!pattern.search("abcd\n\n"));
        let strict = PyRegex::new(r"^[0-9a-f]{4}$(?![\s\S])").unwrap();
        assert!(!strict.search("abcd\n"));
    }

    #[test]
    fn python_s_includes_information_separators() {
        let pattern = PyRegex::new(r".*\S.*").unwrap();
        assert!(!pattern.search("\x1c\x1d"));
        assert!(pattern.search("\x1ca"));
        assert!(PyRegex::new(r"^[^\u0000\s]+$").unwrap().search("ab"));
        assert!(!PyRegex::new(r"^[^\u0000\s]+$").unwrap().search("a\x1fb"));
    }

    #[test]
    fn unknown_constructs_are_refused() {
        assert!(translate(r"\d").is_err());
        assert!(translate(r"(?i)a").is_err());
        assert!(translate(r"(?P<x>a)").is_err());
        assert!(translate("[a").is_err());
    }
}
