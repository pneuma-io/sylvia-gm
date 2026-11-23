//! The model's `search_files` pattern: Python's `re.compile(pattern)` then `regex.search(line)`,
//! spelled for `fancy_regex`.
//!
//! `search_files` searches one line at a time, and `str.splitlines()` leaves no line break in a
//! line, so `$` is the end of the text and `.` any character but `\n` in both dialects. The
//! classes keep Python's meaning: `\w` is `str.isalnum()` or `_`, `\s` is `str.isspace()`, and
//! `\b`/`\B` are boundaries of that `\w`. Python's own errors (an unbalanced group, nothing to
//! repeat, a repeated repeat, a bad escape) refuse the pattern here too, worded differently.
//!
//! Some Python constructs have no `fancy_regex` spelling or a different meaning there, and this
//! port refuses them although Python accepts them (differences row, `search_files`): verbose and
//! ASCII flags, `\N{...}` names, conditionals, possessive repeats, octal escapes and back
//! references above `\99`.

use std::sync::OnceLock;
use sylvia_base::pystr::{is_alnum, is_space};

/// Why a pattern cannot be searched with.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PatternError {
    /// Python's `re` refuses it too.
    Invalid(String),
    /// Python accepts it, but this port does not translate it.
    Unsupported(String),
}

impl std::fmt::Display for PatternError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(message) => f.write_str(message),
            Self::Unsupported(what) => write!(f, "{what} is not supported in this search"),
        }
    }
}

/// The members of a class built from a predicate, as Rust class ranges.
fn ranges(member: impl Fn(char) -> bool) -> String {
    let mut out = String::new();
    let mut start: Option<u32> = None;
    let mut previous = 0u32;
    let push = |out: &mut String, low: u32, high: u32| {
        out.push_str(&format!("\\x{{{low:X}}}"));
        if high > low {
            out.push_str(&format!("-\\x{{{high:X}}}"));
        }
    };
    for code in 0..=0x10FFFFu32 {
        let inside = char::from_u32(code).is_some_and(&member);
        match (inside, start) {
            (true, None) => start = Some(code),
            (false, Some(low)) => {
                push(&mut out, low, previous);
                start = None;
            }
            _ => {}
        }
        if inside {
            previous = code;
        }
    }
    if let Some(low) = start {
        push(&mut out, low, previous);
    }
    out
}

/// Python's `\w` members.
fn word() -> &'static str {
    static WORD: OnceLock<String> = OnceLock::new();
    WORD.get_or_init(|| ranges(|c| c == '_' || is_alnum(c)))
}

/// Python's `\s` members.
fn space() -> &'static str {
    static SPACE: OnceLock<String> = OnceLock::new();
    SPACE.get_or_init(|| ranges(is_space))
}

fn invalid(message: &str, at: usize) -> PatternError {
    PatternError::Invalid(format!("{message} at position {at}"))
}

fn unsupported(what: &str) -> PatternError {
    PatternError::Unsupported(what.to_owned())
}

fn hex(c: char) -> String {
    format!("\\x{{{:X}}}", u32::from(c))
}

struct Translator {
    chars: Vec<char>,
    at: usize,
    out: String,
    /// Groups opened so far, for back references.
    groups: usize,
    /// Whether the last item can take a quantifier, and whether it already has one.
    repeatable: bool,
    repeated: bool,
}

impl Translator {
    fn peek(&self) -> Option<char> {
        self.chars.get(self.at).copied()
    }

    fn take_hex(&mut self, count: usize, start: usize, name: &str) -> Result<char, PatternError> {
        let digits: String = self.chars.iter().skip(self.at).take(count).collect();
        if digits.chars().count() != count || !digits.chars().all(|d| d.is_ascii_hexdigit()) {
            return Err(invalid(&format!("incomplete escape \\{name}{digits}"), start));
        }
        self.at += count;
        let code = u32::from_str_radix(&digits, 16).unwrap_or(u32::MAX);
        char::from_u32(code).ok_or_else(|| invalid(&format!("bad escape \\{name}{digits}"), start))
    }

    /// One escape, after its backslash; `in_class` changes `\b` and the class forms.
    fn escape(&mut self, in_class: bool) -> Result<String, PatternError> {
        let start = self.at - 1;
        let Some(c) = self.peek() else { return Err(invalid("bad escape (end of pattern)", start)) };
        self.at += 1;
        let w = word();
        let s = space();
        Ok(match c {
            'd' => r"\d".to_owned(),
            'D' => r"\D".to_owned(),
            'w' if in_class => w.to_owned(),
            'w' => format!("[{w}]"),
            'W' => format!("[^{w}]"),
            's' if in_class => s.to_owned(),
            's' => format!("[{s}]"),
            'S' => format!("[^{s}]"),
            'b' if in_class => r"\x08".to_owned(),
            'b' => format!("(?:(?<=[{w}])(?![{w}])|(?<![{w}])(?=[{w}]))"),
            'B' if !in_class => format!("(?:(?<=[{w}])(?=[{w}])|(?<![{w}])(?![{w}]))"),
            'A' if !in_class => r"\A".to_owned(),
            'Z' if !in_class => r"\z".to_owned(),
            'n' => r"\n".to_owned(),
            't' => r"\t".to_owned(),
            'r' => r"\r".to_owned(),
            'f' => r"\x0C".to_owned(),
            'v' => r"\x0B".to_owned(),
            'a' => r"\x07".to_owned(),
            'x' => hex(self.take_hex(2, start, "x")?),
            'u' => hex(self.take_hex(4, start, "u")?),
            'U' => hex(self.take_hex(8, start, "U")?),
            'N' => return Err(unsupported("A \\N{...} character name")),
            '0' => {
                if self.peek().is_some_and(|d| d.is_digit(8)) {
                    return Err(unsupported("An octal escape"));
                }
                r"\x00".to_owned()
            }
            '1'..='9' if !in_class => {
                let mut number = c.to_digit(10).unwrap_or(0) as usize;
                if let Some(d) = self.peek().filter(char::is_ascii_digit) {
                    if self.chars.get(self.at + 1).is_some_and(char::is_ascii_digit) {
                        return Err(unsupported("An octal escape"));
                    }
                    self.at += 1;
                    number = number * 10 + d.to_digit(10).unwrap_or(0) as usize;
                }
                if number > self.groups {
                    return Err(invalid(&format!("invalid group reference {number}"), start + 1));
                }
                format!("\\{number}")
            }
            '1'..='9' => return Err(unsupported("An octal escape")),
            c if c.is_ascii_alphanumeric() => return Err(invalid(&format!("bad escape \\{c}"), start)),
            other => hex(other),
        })
    }

    fn class(&mut self) -> Result<String, PatternError> {
        let start = self.at - 1;
        let mut out = String::from("[");
        if self.peek() == Some('^') {
            self.at += 1;
            out.push('^');
        }
        let mut first = true;
        loop {
            let Some(c) = self.peek() else { return Err(invalid("unterminated character set", start)) };
            self.at += 1;
            match c {
                ']' if !first => break,
                '\\' => {
                    let escaped = self.escape(true)?;
                    out.push_str(&escaped);
                }
                '-' => out.push('-'),
                // Literal in Python; a nested class, a set operation or an escape in Rust.
                '[' | ']' | '&' | '~' | '|' => out.push_str(&hex(c)),
                other => out.push(other),
            }
            first = false;
        }
        out.push(']');
        Ok(out)
    }

    /// A `{` at the current position, after its brace: the quantifier text or `None` when
    /// Python reads the brace literally.
    fn braces(&mut self) -> Option<(Option<u64>, Option<u64>)> {
        let rest: String = self.chars[self.at..].iter().collect();
        let close = rest.find('}')?;
        let body = &rest[..close];
        let (low, high) = match body.split_once(',') {
            Some((low, high)) => (low, Some(high)),
            None => (body, None),
        };
        let digits = |text: &str| text.is_empty() || text.chars().all(|d| d.is_ascii_digit());
        if !digits(low) || !high.is_none_or(digits) || (low.is_empty() && high.is_none()) {
            return None;
        }
        self.at += body.chars().count() + 1;
        let low = (!low.is_empty()).then(|| low.parse().unwrap_or(u64::MAX));
        let high = match high {
            None => low,
            Some("") => None,
            Some(high) => Some(high.parse().unwrap_or(u64::MAX)),
        };
        Some((low, high))
    }

    fn quantifier(&mut self, text: String, start: usize) -> Result<(), PatternError> {
        if !self.repeatable {
            let message = if self.repeated { "multiple repeat" } else { "nothing to repeat" };
            return Err(invalid(message, start));
        }
        self.out.push_str(&text);
        match self.peek() {
            Some('?') => {
                self.at += 1;
                self.out.push('?');
            }
            Some('+') => return Err(unsupported("A possessive repeat")),
            _ => {}
        }
        self.repeatable = false;
        self.repeated = true;
        Ok(())
    }

    fn item(&mut self, text: String) {
        self.out.push_str(&text);
        self.repeatable = true;
        self.repeated = false;
    }

    fn group(&mut self, depth: &mut Vec<usize>) -> Result<(), PatternError> {
        let start = self.at - 1;
        if self.peek() != Some('?') {
            self.groups += 1;
            depth.push(start);
            self.out.push('(');
            self.repeatable = false;
            self.repeated = false;
            return Ok(());
        }
        self.at += 1;
        let rest: String = self.chars[self.at..].iter().collect();
        let open = |me: &mut Self, depth: &mut Vec<usize>, consumed: usize, text: &str| {
            me.at += consumed;
            depth.push(start);
            me.out.push_str(text);
            me.repeatable = false;
            me.repeated = false;
        };
        if let Some(after) = rest.strip_prefix("P<") {
            let Some(end) = after.find('>') else {
                return Err(invalid("missing >, unterminated name", start + 3));
            };
            let name = &after[..end];
            self.groups += 1;
            open(self, depth, 2 + end + 1, &format!("(?P<{name}>"));
            return Ok(());
        }
        if let Some(after) = rest.strip_prefix("P=") {
            let Some(end) = after.find(')') else {
                return Err(invalid("missing ), unterminated name", start + 3));
            };
            let name = &after[..end];
            self.at += 2 + end + 1;
            self.item(format!("\\k<{name}>"));
            return Ok(());
        }
        if let Some(after) = rest.strip_prefix('#') {
            let Some(end) = after.find(')') else {
                return Err(invalid("missing ), unterminated comment", start));
            };
            self.at += 1 + end + 1;
            return Ok(());
        }
        for prefix in [":", "=", "!", "<=", "<!", ">"] {
            if rest.starts_with(prefix) {
                open(self, depth, prefix.chars().count(), &format!("(?{prefix}"));
                return Ok(());
            }
        }
        if rest.starts_with('(') {
            return Err(unsupported("A conditional group"));
        }
        // Flags: global `(?flags)` at the start, or scoped `(?flags:...)`.
        let flags: String = rest.chars().take_while(|c| c.is_ascii_alphabetic() || *c == '-').collect();
        let after = rest[flags.len()..].chars().next();
        for flag in flags.chars() {
            match flag {
                'i' | 'm' | 's' | 'u' | '-' => {}
                'x' => return Err(unsupported("The verbose flag")),
                'a' => return Err(unsupported("The ASCII flag")),
                'L' => {
                    return Err(invalid(
                        "bad inline flags: cannot use 'L' flag with a str pattern",
                        start + 2,
                    ))
                }
                _ => return Err(invalid("unknown flag", start + 2)),
            }
        }
        let kept: String = flags.chars().filter(|c| *c != 'u').collect();
        match after {
            Some(')') if flags.contains('-') => Err(invalid("missing :", start + 2 + flags.len())),
            Some(')') => {
                if start != 0 && !self.out_is_flags_only() {
                    return Err(invalid("global flags not at the start of the expression", start));
                }
                self.at += flags.len() + 1;
                if !kept.is_empty() {
                    self.out.push_str(&format!("(?{kept})"));
                }
                Ok(())
            }
            Some(':') => {
                let text = if kept.is_empty() { "(?:".to_owned() } else { format!("(?{kept}:") };
                open(self, depth, flags.len() + 1, &text);
                Ok(())
            }
            _ => Err(invalid("unknown extension ?", start + 1)),
        }
    }

    /// Only global flag groups have been written so far.
    fn out_is_flags_only(&self) -> bool {
        let mut rest = self.out.as_str();
        while let Some(after) = rest.strip_prefix("(?") {
            let Some(end) = after.find(')') else { return false };
            if !after[..end].chars().all(|c| matches!(c, 'i' | 'm' | 's')) {
                return false;
            }
            rest = &after[end + 1..];
        }
        rest.is_empty()
    }
}

/// `re.compile(pattern)`, spelled for `fancy_regex`.
pub fn translate(pattern: &str) -> Result<String, PatternError> {
    let mut me = Translator {
        chars: pattern.chars().collect(),
        at: 0,
        out: String::new(),
        groups: 0,
        repeatable: false,
        repeated: false,
    };
    let mut depth: Vec<usize> = Vec::new();
    while let Some(c) = me.peek() {
        let start = me.at;
        me.at += 1;
        match c {
            '\\' => {
                let anchor = matches!(me.peek(), Some('b' | 'B' | 'A' | 'Z'));
                let text = me.escape(false)?;
                me.item(text);
                // Python cannot repeat a position (`nothing to repeat`).
                me.repeatable = !anchor;
            }
            '[' => {
                let text = me.class()?;
                me.item(text);
            }
            '(' => me.group(&mut depth)?,
            ')' => {
                if depth.pop().is_none() {
                    return Err(invalid("unbalanced parenthesis", start));
                }
                me.item(")".to_owned());
            }
            '*' | '+' | '?' => me.quantifier(c.to_string(), start)?,
            '{' => match me.braces() {
                Some((low, high)) => {
                    if let (Some(low), Some(high)) = (low, high) {
                        if low > high {
                            return Err(invalid("min repeat greater than max repeat", start + 1));
                        }
                    }
                    let text = match (low, high) {
                        (low, Some(high)) if low == Some(high) => format!("{{{high}}}"),
                        (low, Some(high)) => format!("{{{},{high}}}", low.unwrap_or(0)),
                        (low, None) => format!("{{{},}}", low.unwrap_or(0)),
                    };
                    me.quantifier(text, start)?;
                }
                None => me.item(hex('{')),
            },
            '}' => me.item(hex('}')),
            '$' | '^' => {
                me.item(if c == '$' { r"\z".to_owned() } else { "^".to_owned() });
                me.repeatable = false;
            }
            '.' => me.item(".".to_owned()),
            '|' => {
                me.out.push('|');
                me.repeatable = false;
                me.repeated = false;
            }
            other => me.item(hex(other)),
        }
    }
    if let Some(open) = depth.pop() {
        return Err(invalid("missing ), unterminated subpattern", open));
    }
    Ok(me.out)
}

/// A compiled search pattern.
pub struct SearchPattern(fancy_regex::Regex);

impl SearchPattern {
    /// `re.compile(pattern)`.
    pub fn new(pattern: &str) -> Result<Self, PatternError> {
        let translated = translate(pattern)?;
        fancy_regex::Regex::new(&translated)
            .map(Self)
            .map_err(|error| PatternError::Invalid(error.to_string()))
    }

    /// `regex.search(line) is not None`. A line that reaches the backtracking limit counts as no
    /// match.
    pub fn search(&self, line: &str) -> bool {
        self.0.is_match(line).unwrap_or(false)
    }
}
