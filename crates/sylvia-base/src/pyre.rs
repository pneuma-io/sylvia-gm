//! Python `re` replacement templates: CPython 3.12's `re._parser.parse_template`, its
//! `Tokenizer` positions and the `re.error` message format.
//!
//! `re.sub(pattern, repl, text)` parses a `str` template before it searches, so a bad template
//! fails even when nothing matches.

use crate::pystr;

/// `_sre.MAXGROUPS` on a 64-bit build.
const MAXGROUPS: u64 = 1_073_741_823;

/// One part of a parsed template.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Piece {
    Literal(String),
    Group(usize),
}

/// A template Python refuses: `re.error` (class `error`) or `IndexError`, with `str(error)`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TemplateError {
    pub class: &'static str,
    pub message: String,
}

/// `re.error(msg, pattern, pos)`: "msg at position N", with the line and column when the
/// pattern has a newline.
fn re_error(msg: &str, pattern: &[char], pos: usize) -> TemplateError {
    let mut message = format!("{msg} at position {pos}");
    if pattern.contains(&'\n') {
        let before = &pattern[..pos.min(pattern.len())];
        let lineno = before.iter().filter(|c| **c == '\n').count() + 1;
        let colno = match before.iter().rposition(|c| *c == '\n') {
            Some(newline) => pos - newline,
            None => pos + 1,
        };
        message = format!("{message} (line {lineno}, column {colno})");
    }
    TemplateError { class: "error", message }
}

/// `re._parser.Tokenizer` over a `str`.
struct Tokenizer<'a> {
    chars: &'a [char],
    index: usize,
    next: Option<String>,
}

impl<'a> Tokenizer<'a> {
    fn new(chars: &'a [char]) -> Result<Self, TemplateError> {
        let mut tokenizer = Self { chars, index: 0, next: None };
        tokenizer.advance()?;
        Ok(tokenizer)
    }

    /// `__next()`.
    fn advance(&mut self) -> Result<(), TemplateError> {
        let mut index = self.index;
        let Some(&first) = self.chars.get(index) else {
            self.next = None;
            return Ok(());
        };
        let mut token = first.to_string();
        if first == '\\' {
            index += 1;
            let Some(&second) = self.chars.get(index) else {
                return Err(re_error("bad escape (end of pattern)", self.chars, self.chars.len() - 1));
            };
            token.push(second);
        }
        self.index = index + 1;
        self.next = Some(token);
        Ok(())
    }

    fn get(&mut self) -> Result<Option<String>, TemplateError> {
        let this = self.next.take();
        self.advance()?;
        Ok(this)
    }

    fn matches(&mut self, c: char) -> Result<bool, TemplateError> {
        if self.next.as_deref() == Some(c.encode_utf8(&mut [0; 4])) {
            self.advance()?;
            return Ok(true);
        }
        Ok(false)
    }

    fn tell(&self) -> usize {
        self.index - self.next.as_ref().map_or(0, |token| token.chars().count())
    }

    fn error(&self, msg: &str, offset: usize) -> TemplateError {
        re_error(msg, self.chars, self.tell() - offset)
    }

    fn next_in(&self, set: &str) -> bool {
        self.next.as_deref().is_some_and(|token| token.chars().count() == 1 && set.contains(token))
    }

    /// `getuntil(terminator, name)`.
    fn getuntil(&mut self, terminator: char, name: &str) -> Result<String, TemplateError> {
        let mut result = String::new();
        loop {
            let c = self.next.clone();
            self.advance()?;
            let Some(c) = c else {
                if result.is_empty() {
                    return Err(self.error(&format!("missing {name}"), 0));
                }
                let length = result.chars().count();
                return Err(self.error(&format!("missing {terminator}, unterminated name"), length));
            };
            if c.chars().count() == 1 && c.starts_with(terminator) {
                if result.is_empty() {
                    return Err(self.error(&format!("missing {name}"), 1));
                }
                return Ok(result);
            }
            result.push_str(&c);
        }
    }
}

const OCTDIGITS: &str = "01234567";
const DIGITS: &str = "0123456789";

fn escape(token: &str) -> Option<char> {
    Some(match token {
        "\\a" => '\u{7}',
        "\\b" => '\u{8}',
        "\\f" => '\u{c}',
        "\\n" => '\n',
        "\\r" => '\r',
        "\\t" => '\t',
        "\\v" => '\u{b}',
        "\\\\" => '\\',
        _ => return None,
    })
}

/// `parse_template(source, pattern)` for a pattern with `groups` capturing groups and no names.
pub fn parse_template(source: &str, groups: usize) -> Result<Vec<Piece>, TemplateError> {
    let chars: Vec<char> = source.chars().collect();
    let mut s = Tokenizer::new(&chars)?;
    let mut result = Vec::new();
    let mut literal = String::new();
    let add_group = |s: &Tokenizer<'_>,
                     literal: &mut String,
                     result: &mut Vec<Piece>,
                     index: u64,
                     pos: usize|
     -> Result<(), TemplateError> {
        if index > groups as u64 {
            return Err(s.error(&format!("invalid group reference {index}"), pos));
        }
        result.push(Piece::Literal(std::mem::take(literal)));
        result.push(Piece::Group(index as usize));
        Ok(())
    };
    while let Some(mut this) = s.get()? {
        if !this.starts_with('\\') || this.chars().count() == 1 {
            literal.push_str(&this);
            continue;
        }
        let c = this.chars().nth(1).expect("an escape has two characters");
        if c == 'g' {
            if !s.matches('<')? {
                return Err(s.error("missing <", 0));
            }
            let name = s.getuntil('>', "group name")?;
            let length = name.chars().count();
            if !name.chars().all(|c| c.is_ascii_digit()) {
                if !pystr::isidentifier(&name) {
                    let message = format!("bad character in group name {}", pystr::repr(&name));
                    return Err(s.error(&message, length + 1));
                }
                // No pattern here has named groups.
                return Err(TemplateError {
                    class: "IndexError",
                    message: format!("unknown group name {}", pystr::repr(&name)),
                });
            }
            let digits = name.trim_start_matches('0');
            let digits = if digits.is_empty() { "0" } else { digits };
            let index = if digits.len() > 10 { u64::MAX } else { digits.parse::<u64>().expect("decimal") };
            if index >= MAXGROUPS {
                return Err(s.error(&format!("invalid group reference {digits}"), length + 1));
            }
            add_group(&s, &mut literal, &mut result, index, length + 1)?;
        } else if c == '0' {
            if s.next_in(OCTDIGITS) {
                this.push_str(&s.get()?.expect("a digit"));
                if s.next_in(OCTDIGITS) {
                    this.push_str(&s.get()?.expect("a digit"));
                }
            }
            let value = u32::from_str_radix(&this[1..], 8).expect("octal") & 0xFF;
            literal.push(char::from_u32(value).expect("a byte value"));
        } else if DIGITS.contains(c) {
            let mut is_octal = false;
            if s.next_in(DIGITS) {
                this.push_str(&s.get()?.expect("a digit"));
                let second = this.chars().nth(2).expect("two digits");
                if OCTDIGITS.contains(c) && OCTDIGITS.contains(second) && s.next_in(OCTDIGITS) {
                    this.push_str(&s.get()?.expect("a digit"));
                    is_octal = true;
                    let value = u32::from_str_radix(&this[1..], 8).expect("octal");
                    if value > 0o377 {
                        let message = format!("octal escape value {this} outside of range 0-0o377");
                        return Err(s.error(&message, this.chars().count()));
                    }
                    literal.push(char::from_u32(value).expect("a byte value"));
                }
            }
            if !is_octal {
                let index = this[1..].parse::<u64>().expect("decimal");
                add_group(&s, &mut literal, &mut result, index, this.chars().count() - 1)?;
            }
        } else if let Some(character) = escape(&this) {
            literal.push(character);
        } else if c.is_ascii_alphabetic() {
            return Err(s.error(&format!("bad escape {this}"), this.chars().count()));
        } else {
            literal.push_str(&this);
        }
    }
    result.push(Piece::Literal(literal));
    Ok(result)
}

/// The text a parsed template gives for one match, whose groups are `groups[0]` (the whole
/// match) onward.
pub fn expand(pieces: &[Piece], groups: &[&str]) -> String {
    pieces
        .iter()
        .map(|piece| match piece {
            Piece::Literal(text) => text.as_str(),
            Piece::Group(index) => groups[*index],
        })
        .collect()
}
