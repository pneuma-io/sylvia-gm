//! CPython's `json.loads` scanner: where it fails, and the value it reads, with and without the
//! `object_pairs_hook` and `parse_constant` hooks Sylvia uses to refuse duplicate keys and the
//! `NaN`/`Infinity` literals.
//!
//! A translation of CPython 3.12's C scanner (`Modules/_json.c`: `scan_once_unicode`,
//! `_parse_object_unicode`, `_parse_array_unicode`, `scanstring_unicode`,
//! `_match_number_unicode`), `Lib/json/decoder.py` (`JSONDecoder.decode`, `raw_decode`,
//! `JSONDecodeError`'s line and column) and `Lib/json/__init__.py` (`loads`, the byte-order
//! mark). Positions are counted in code points, as Python indexes a `str`.
//!
//! Failures come in CPython's order: a `JSONDecodeError` where the scanner meets it, a refused
//! constant when the scanner reaches the literal (`parse_constant` is called there), and a
//! duplicate key when its object closes (`object_pairs_hook` is called there, after every member
//! and nested object is read). A value Rust cannot hold (an integer beyond 64 bits, a lone
//! surrogate) does not stop the scan, because CPython would carry on: only if CPython would
//! succeed is the text refused, by the declared limit.

use crate::{CodedError, Json, JsonMap};

/// Which of the two hooks are in force (`object_pairs_hook=<refuse duplicates>`,
/// `parse_constant=<refuse NaN/Infinity>`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct HookPolicy {
    pub refuse_duplicates: bool,
    pub refuse_constants: bool,
}

/// `json.JSONDecodeError(msg, doc, pos)`: its parts and, through `Display`, `str(error)`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JsonDecodeError {
    pub msg: &'static str,
    /// The code-point index in the text.
    pub pos: usize,
    pub lineno: usize,
    pub colno: usize,
}

impl std::fmt::Display for JsonDecodeError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}: line {} column {} (char {})", self.msg, self.lineno, self.colno, self.pos)
    }
}

/// The first failure of a hooked read, in CPython's order.
#[derive(Debug, Clone, PartialEq)]
pub enum HookFailure {
    /// `json.JSONDecodeError`.
    Decode(JsonDecodeError),
    /// `parse_constant` refused this literal (`"NaN"`, `"Infinity"` or `"-Infinity"`).
    Constant(&'static str),
    /// `object_pairs_hook` met this key twice in one object.
    Duplicate(String),
    /// Python reads the text but Rust cannot hold it: a declared limit of
    /// `docs/rust-port/differences.md` (rows 1, 3 and 4).
    Unsupported(CodedError),
}

/// A hooked read that succeeded.
#[derive(Debug, Clone, PartialEq)]
pub struct Hooked {
    pub value: Json,
    /// A number overflowed to infinity (`1e999`), or an accepted `NaN`/`Infinity` literal was
    /// read. Python holds `inf`/`nan` there; `value` holds `null` in its place, and the caller
    /// refuses it with its own message, as Python's callers do.
    pub non_finite: bool,
}

/// Deeper nesting is refused by the declared limit (row 4), as `loads` refuses it.
const NESTING_LIMIT: usize = 128;

/// `json.loads(text, object_pairs_hook=..., parse_constant=...)` with the hooks `policy` names.
pub fn loads_hooked(text: &str, policy: HookPolicy) -> Result<Hooked, HookFailure> {
    let s: Vec<char> = text.chars().collect();
    let decode = |msg: &'static str, pos: usize| HookFailure::Decode(decode_error(&s, msg, pos));
    if s.first() == Some(&'\u{feff}') {
        return Err(decode("Unexpected UTF-8 BOM (decode using utf-8-sig)", 0));
    }
    let mut parser = Parser { s: &s, policy, unsupported: None };
    let start = parser.skip_whitespace(0);
    let (value, end, non_finite) = match parser.scan_once(start, 0) {
        Ok(found) => found,
        Err(Failure::Stop(position)) => return Err(decode("Expecting value", position)),
        Err(Failure::Error(msg, position)) => return Err(decode(msg, position)),
        Err(Failure::Hook(failure)) => return Err(failure),
    };
    let end = parser.skip_whitespace(end);
    if end != s.len() {
        return Err(decode("Extra data", end));
    }
    if let Some(reason) = parser.unsupported {
        return Err(HookFailure::Unsupported(CodedError::rejected(format!("Invalid JSON: {reason}"))));
    }
    Ok(Hooked { value, non_finite: non_finite > 0 })
}

/// `json.JSONDecoder().raw_decode(text)`: the value at the very start of `text` (no leading
/// whitespace is skipped, and no byte-order mark is checked) and the code-point index after it.
/// Trailing text is left alone. No hooks are in force, as for a plain decoder.
pub fn raw_decode(text: &str) -> Result<(Hooked, usize), HookFailure> {
    let s: Vec<char> = text.chars().collect();
    let decode = |msg: &'static str, pos: usize| HookFailure::Decode(decode_error(&s, msg, pos));
    let mut parser = Parser { s: &s, policy: HookPolicy::default(), unsupported: None };
    let (value, end, non_finite) = match parser.scan_once(0, 0) {
        Ok(found) => found,
        Err(Failure::Stop(position)) => return Err(decode("Expecting value", position)),
        Err(Failure::Error(msg, position)) => return Err(decode(msg, position)),
        Err(Failure::Hook(failure)) => return Err(failure),
    };
    if let Some(reason) = parser.unsupported {
        return Err(HookFailure::Unsupported(CodedError::rejected(format!("Invalid JSON: {reason}"))));
    }
    Ok((Hooked { value, non_finite: non_finite > 0 }, end))
}

/// The first error CPython's plain `json.loads` reports for `text`: its message and position.
pub(super) fn first_error(text: &str) -> Option<(&'static str, usize)> {
    match loads_hooked(text, HookPolicy::default()) {
        Err(HookFailure::Decode(error)) => Some((error.msg, error.pos)),
        _ => None,
    }
}

/// `str(JSONDecodeError(message, text, position))`.
pub(super) fn message(text: &str, message: &str, position: usize) -> String {
    let s: Vec<char> = text.chars().collect();
    let JsonDecodeError { lineno, colno, .. } = decode_error(&s, "", position);
    format!("{message}: line {lineno} column {colno} (char {position})")
}

fn decode_error(s: &[char], msg: &'static str, pos: usize) -> JsonDecodeError {
    let before = &s[..pos.min(s.len())];
    let lineno = before.iter().filter(|&&c| c == '\n').count() + 1;
    let colno = match before.iter().rposition(|&c| c == '\n') {
        Some(newline) => pos - newline,
        None => pos + 1,
    };
    JsonDecodeError { msg, pos, lineno, colno }
}

/// How one scan step failed: `StopIteration(position)` from `scan_once` (reported by its
/// caller as "Expecting value"), a `JSONDecodeError` raised directly, or a hook's refusal.
enum Failure {
    Stop(usize),
    Error(&'static str, usize),
    Hook(HookFailure),
}

/// A scanned term: its value, the index after it, and how many non-finite numbers it holds.
type Scanned = Result<(Json, usize, usize), Failure>;

struct Parser<'a> {
    s: &'a [char],
    policy: HookPolicy,
    /// The first value Rust cannot hold; it only matters if CPython would succeed.
    unsupported: Option<String>,
}

fn is_whitespace(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\n' | '\r')
}

impl Parser<'_> {
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

    fn unsupported(&mut self, reason: String) {
        self.unsupported.get_or_insert(reason);
    }

    /// `parse_constant(literal)`: refused, or read as a non-finite number.
    fn constant(&mut self, literal: &'static str, end: usize) -> Scanned {
        if self.policy.refuse_constants {
            return Err(Failure::Hook(HookFailure::Constant(literal)));
        }
        Ok((Json::Null, end, 1))
    }

    /// `scan_once_unicode`: the term at `index` and the index after it.
    fn scan_once(&mut self, index: usize, depth: usize) -> Scanned {
        let Some(c) = self.at(index) else { return Err(Failure::Stop(index)) };
        let length = self.s.len();
        match c {
            '"' => {
                let (text, end) = self.scan_string(index + 1)?;
                return Ok((Json::String(text), end, 0));
            }
            '{' | '[' if depth + 1 >= NESTING_LIMIT => {
                return Err(Failure::Hook(HookFailure::Unsupported(CodedError::rejected(
                    "Invalid JSON: recursion limit exceeded",
                ))))
            }
            '{' => return self.parse_object(index + 1, depth + 1),
            '[' => return self.parse_array(index + 1, depth + 1),
            'n' if index + 3 < length && self.starts_with(index, "null") => {
                return Ok((Json::Null, index + 4, 0))
            }
            't' if index + 3 < length && self.starts_with(index, "true") => {
                return Ok((Json::Bool(true), index + 4, 0))
            }
            'f' if index + 4 < length && self.starts_with(index, "false") => {
                return Ok((Json::Bool(false), index + 5, 0))
            }
            'N' if index + 2 < length && self.starts_with(index, "NaN") => {
                return self.constant("NaN", index + 3)
            }
            'I' if index + 7 < length && self.starts_with(index, "Infinity") => {
                return self.constant("Infinity", index + 8)
            }
            '-' if index + 8 < length && self.starts_with(index, "-Infinity") => {
                return self.constant("-Infinity", index + 9)
            }
            _ => {}
        }
        self.match_number(index)
    }

    /// `_match_number_unicode`, then `int(text)` or `float(text)`.
    fn match_number(&mut self, start: usize) -> Scanned {
        let Some(end_index) = self.s.len().checked_sub(1) else { return Err(Failure::Stop(start)) };
        let digit = |index: usize| self.at(index).is_some_and(|c| c.is_ascii_digit());
        let mut index = start;
        let mut is_float = false;
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
            is_float = true;
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
            if digit(index - 1) {
                is_float = true;
            } else {
                index = exponent_start;
            }
        }
        let token: String = self.s[start..index].iter().collect();
        if is_float {
            let value: f64 = token.parse().unwrap_or(f64::NAN);
            if !value.is_finite() {
                return Ok((Json::Null, index, 1));
            }
            return Ok((serde_json::Number::from_f64(value).map_or(Json::Null, Json::Number), index, 0));
        }
        let value = match token.parse::<i64>() {
            // `int("-0")` is 0.
            Ok(integer) => Json::from(integer),
            Err(_) => match token.parse::<u64>() {
                Ok(integer) => Json::from(integer),
                Err(_) => {
                    self.unsupported(format!("integer {token} is outside the supported 64-bit range"));
                    Json::Null
                }
            },
        };
        Ok((value, index, 0))
    }

    /// `scanstring_unicode`, strict: `end` is the index after the opening quote.
    fn scan_string(&mut self, mut end: usize) -> Result<(String, usize), Failure> {
        let begin = end - 1;
        let length = self.s.len();
        let mut out = String::new();
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
            out.extend(&self.s[end..next]);
            next += 1;
            if c == '"' {
                return Ok((out, next));
            }
            if next == length {
                return Err(Failure::Error("Unterminated string starting at", begin));
            }
            let escape = self.s[next];
            if escape != 'u' {
                end = next + 1;
                out.push(match escape {
                    '"' => '"',
                    '\\' => '\\',
                    '/' => '/',
                    'b' => '\u{8}',
                    'f' => '\u{c}',
                    'n' => '\n',
                    'r' => '\r',
                    't' => '\t',
                    _ => return Err(Failure::Error("Invalid \\escape", end - 2)),
                });
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
                if (0xdc00..=0xdfff).contains(&low) {
                    code = 0x10000 + ((code - 0xd800) << 10) + (low - 0xdc00);
                } else {
                    end -= 6;
                }
            }
            match char::from_u32(code) {
                Some(character) => out.push(character),
                None => {
                    self.unsupported("lone surrogate in hex escape".into());
                    out.push('\u{fffd}');
                }
            }
        }
    }

    /// `_parse_object_unicode`: `index` is the index after `{`. The pairs go to
    /// `object_pairs_hook` when the object closes.
    fn parse_object(&mut self, index: usize, depth: usize) -> Scanned {
        let mut index = self.skip_whitespace(index);
        let mut pairs: Vec<(String, Json, usize)> = Vec::new();
        if self.at(index) != Some('}') {
            loop {
                if self.at(index) != Some('"') {
                    return Err(Failure::Error("Expecting property name enclosed in double quotes", index));
                }
                let (key, after) = self.scan_string(index + 1)?;
                index = self.skip_whitespace(after);
                if self.at(index) != Some(':') {
                    return Err(Failure::Error("Expecting ':' delimiter", index));
                }
                index = self.skip_whitespace(index + 1);
                let (value, after, non_finite) = self.scan_once(index, depth)?;
                pairs.push((key, value, non_finite));
                index = self.skip_whitespace(after);
                if self.at(index) == Some('}') {
                    break;
                }
                if self.at(index) != Some(',') {
                    return Err(Failure::Error("Expecting ',' delimiter", index));
                }
                index = self.skip_whitespace(index + 1);
            }
        }
        let mut map = JsonMap::new();
        let mut counts: std::collections::HashMap<String, usize> = std::collections::HashMap::new();
        for (key, value, non_finite) in pairs {
            if self.policy.refuse_duplicates && map.contains_key(&key) {
                return Err(Failure::Hook(HookFailure::Duplicate(key)));
            }
            // A plain `dict`: a repeated key keeps its first position and its last value, so the
            // value it replaces no longer counts.
            counts.insert(key.clone(), non_finite);
            map.insert(key, value);
        }
        Ok((Json::Object(map), index + 1, counts.values().sum()))
    }

    /// `_parse_array_unicode`: `index` is the index after `[`.
    fn parse_array(&mut self, index: usize, depth: usize) -> Scanned {
        let mut index = self.skip_whitespace(index);
        let mut items = Vec::new();
        let mut count = 0;
        if self.at(index) != Some(']') {
            loop {
                let (value, after, non_finite) = self.scan_once(index, depth)?;
                items.push(value);
                count += non_finite;
                index = self.skip_whitespace(after);
                if self.at(index) == Some(']') {
                    break;
                }
                if self.at(index) != Some(',') {
                    return Err(Failure::Error("Expecting ',' delimiter", index));
                }
                index = self.skip_whitespace(index + 1);
            }
        }
        Ok((Json::Array(items), index + 1, count))
    }
}
