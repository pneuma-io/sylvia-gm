//! CPython's `json.JSONDecodeError` for text that `json.loads` refuses.
//!
//! A translation of how CPython 3.12's C scanner (`Modules/_json.c`: `scan_once_unicode`,
//! `_parse_object_unicode`, `_parse_array_unicode`, `scanstring_unicode`,
//! `_match_number_unicode`) and `Lib/json/decoder.py` (`JSONDecoder.decode`, `raw_decode`,
//! `JSONDecodeError`) and `Lib/json/__init__.py` (`loads`, the byte-order mark) find the first
//! error. It only locates the error; values are still read by `serde_json`. Positions are
//! counted in code points, as Python indexes a `str`.
//!
//! Text CPython reads but [`super::loads`] refuses (the declared limits: integers beyond 64
//! bits, `NaN` and `Infinity`, lone surrogates, deep nesting, floats beyond the `f64` range)
//! scans as valid here, so its own refusal stands.

/// The first error CPython's `json.loads` reports for `text`: its message and position.
pub(super) fn first_error(text: &str) -> Option<(&'static str, usize)> {
    let s: Vec<char> = text.chars().collect();
    if s.first() == Some(&'\u{feff}') {
        return Some(("Unexpected UTF-8 BOM (decode using utf-8-sig)", 0));
    }
    let scanner = Scanner { s: &s };
    let start = scanner.skip_whitespace(0);
    let end = match scanner.scan_once(start) {
        Ok(end) => end,
        Err(Failure::Stop(position)) => return Some(("Expecting value", position)),
        Err(Failure::Error(message, position)) => return Some((message, position)),
    };
    let end = scanner.skip_whitespace(end);
    if end != s.len() {
        return Some(("Extra data", end));
    }
    None
}

/// `str(JSONDecodeError(message, text, position))`.
pub(super) fn message(text: &str, message: &str, position: usize) -> String {
    let before: Vec<char> = text.chars().take(position).collect();
    let line = before.iter().filter(|&&c| c == '\n').count() + 1;
    let column = match before.iter().rposition(|&c| c == '\n') {
        Some(newline) => position - newline,
        None => position + 1,
    };
    format!("{message}: line {line} column {column} (char {position})")
}

/// How one scan step failed: `StopIteration(position)` from `scan_once` (reported by its
/// caller as "Expecting value"), or a `JSONDecodeError` raised directly.
enum Failure {
    Stop(usize),
    Error(&'static str, usize),
}

struct Scanner<'a> {
    s: &'a [char],
}

fn is_whitespace(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\n' | '\r')
}

impl Scanner<'_> {
    fn at(&self, index: usize) -> Option<char> {
        self.s.get(index).copied()
    }

    fn skip_whitespace(&self, mut index: usize) -> usize {
        while self.at(index).is_some_and(is_whitespace) {
            index += 1;
        }
        index
    }

    fn starts_with(&self, index: usize, word: &str) -> bool {
        word.chars().enumerate().all(|(offset, c)| self.at(index + offset) == Some(c))
    }

    /// `scan_once_unicode`: the index after the term at `index`.
    fn scan_once(&self, index: usize) -> Result<usize, Failure> {
        let Some(c) = self.at(index) else { return Err(Failure::Stop(index)) };
        let length = self.s.len();
        match c {
            '"' => return self.scan_string(index + 1),
            '{' => return self.parse_object(index + 1),
            '[' => return self.parse_array(index + 1),
            'n' if index + 3 < length && self.starts_with(index, "null") => return Ok(index + 4),
            't' if index + 3 < length && self.starts_with(index, "true") => return Ok(index + 4),
            'f' if index + 4 < length && self.starts_with(index, "false") => return Ok(index + 5),
            'N' if index + 2 < length && self.starts_with(index, "NaN") => return Ok(index + 3),
            'I' if index + 7 < length && self.starts_with(index, "Infinity") => return Ok(index + 8),
            '-' if index + 8 < length && self.starts_with(index, "-Infinity") => return Ok(index + 9),
            _ => {}
        }
        self.match_number(index)
    }

    /// `_match_number_unicode`.
    fn match_number(&self, start: usize) -> Result<usize, Failure> {
        let Some(end_index) = self.s.len().checked_sub(1) else { return Err(Failure::Stop(start)) };
        let digit = |index: usize| self.at(index).is_some_and(|c| c.is_ascii_digit());
        let mut index = start;
        if self.at(index) == Some('-') {
            index += 1;
            if index > end_index {
                return Err(Failure::Stop(start));
            }
        }
        match self.at(index) {
            Some('1'..='9') => {
                index += 1;
                while index <= end_index && digit(index) {
                    index += 1;
                }
            }
            Some('0') => index += 1,
            _ => return Err(Failure::Stop(start)),
        }
        if index < end_index && self.at(index) == Some('.') && digit(index + 1) {
            index += 2;
            while index <= end_index && digit(index) {
                index += 1;
            }
        }
        if index < end_index && matches!(self.at(index), Some('e' | 'E')) {
            let exponent_start = index;
            index += 1;
            if index < end_index && matches!(self.at(index), Some('-' | '+')) {
                index += 1;
            }
            while index <= end_index && digit(index) {
                index += 1;
            }
            if !digit(index - 1) {
                index = exponent_start;
            }
        }
        Ok(index)
    }

    /// `scanstring_unicode`, strict: `end` is the index after the opening quote.
    fn scan_string(&self, mut end: usize) -> Result<usize, Failure> {
        let begin = end - 1;
        let length = self.s.len();
        loop {
            let mut next = end;
            let mut c = '\0';
            while next < length {
                c = self.s[next];
                if c == '"' || c == '\\' {
                    break;
                }
                if c <= '\u{1f}' {
                    return Err(Failure::Error("Invalid control character at", next));
                }
                next += 1;
            }
            if next >= length || !(c == '"' || c == '\\') {
                return Err(Failure::Error("Unterminated string starting at", begin));
            }
            next += 1;
            if c == '"' {
                return Ok(next);
            }
            if next == length {
                return Err(Failure::Error("Unterminated string starting at", begin));
            }
            let escape = self.s[next];
            if escape != 'u' {
                end = next + 1;
                if !matches!(escape, '"' | '\\' | '/' | 'b' | 'f' | 'n' | 'r' | 't') {
                    return Err(Failure::Error("Invalid \\escape", end - 2));
                }
                continue;
            }
            next += 1;
            end = next + 4;
            if end >= length {
                return Err(Failure::Error("Invalid \\uXXXX escape", next - 1));
            }
            let mut code: u32 = 0;
            while next < end {
                let Some(value) = self.s[next].to_digit(16) else {
                    return Err(Failure::Error("Invalid \\uXXXX escape", end - 5));
                };
                code = (code << 4) | value;
                next += 1;
            }
            // A high surrogate may be followed by an escaped low one.
            if (0xd800..=0xdbff).contains(&code)
                && end + 6 < length
                && self.s[next] == '\\'
                && self.s[next + 1] == 'u'
            {
                next += 2;
                end += 6;
                let mut low: u32 = 0;
                while next < end {
                    let Some(value) = self.s[next].to_digit(16) else {
                        return Err(Failure::Error("Invalid \\uXXXX escape", end - 5));
                    };
                    low = (low << 4) | value;
                    next += 1;
                }
                if !(0xdc00..=0xdfff).contains(&low) {
                    end -= 6;
                }
            }
        }
    }

    /// `_parse_object_unicode`: `index` is the index after `{`.
    fn parse_object(&self, index: usize) -> Result<usize, Failure> {
        let mut index = self.skip_whitespace(index);
        if self.at(index) != Some('}') {
            loop {
                if self.at(index) != Some('"') {
                    return Err(Failure::Error("Expecting property name enclosed in double quotes", index));
                }
                index = self.skip_whitespace(self.scan_string(index + 1)?);
                if self.at(index) != Some(':') {
                    return Err(Failure::Error("Expecting ':' delimiter", index));
                }
                index = self.skip_whitespace(index + 1);
                index = self.skip_whitespace(self.scan_once(index)?);
                if self.at(index) == Some('}') {
                    break;
                }
                if self.at(index) != Some(',') {
                    return Err(Failure::Error("Expecting ',' delimiter", index));
                }
                index = self.skip_whitespace(index + 1);
            }
        }
        Ok(index + 1)
    }

    /// `_parse_array_unicode`: `index` is the index after `[`.
    fn parse_array(&self, index: usize) -> Result<usize, Failure> {
        let mut index = self.skip_whitespace(index);
        if self.at(index) != Some(']') {
            loop {
                index = self.skip_whitespace(self.scan_once(index)?);
                if self.at(index) == Some(']') {
                    break;
                }
                if self.at(index) != Some(',') {
                    return Err(Failure::Error("Expecting ',' delimiter", index));
                }
                index = self.skip_whitespace(index + 1);
            }
        }
        Ok(index + 1)
    }
}
