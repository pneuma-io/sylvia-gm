//! `yaml/reader.py` and `yaml/scanner.py`: characters to tokens, ported method for method.

use super::error::{char_repr, Mark, MarkedError, MarkedKind, YamlError};
use std::sync::Arc;

// ── Reader ────────────────────────────────────────────────────────────────────────────────

/// `Reader`: the characters with a trailing NUL, and the position.
///
/// A `str` or `bytes` input is checked whole when the reader is made. A text file stream is
/// read as `Reader.update_raw` reads it, 4,096 characters at a time, and each piece is checked
/// only when the scanner first needs it ([`Reader::new_stream`]). Python then raises from the
/// middle of a scan; here the read records the error and returns NUL, and the scanner's entry
/// points return the recorded error instead of their result, which is what the caller of the
/// Python scanner sees.
pub(crate) struct Reader {
    buffer: Arc<[char]>,
    name: Arc<str>,
    /// Text input keeps its buffer for snippets; a file stream's marks have none.
    snippets: bool,
    stream: Option<StreamState>,
    pointer: usize,
    index: usize,
    line: usize,
    column: usize,
}

/// The lazy reading of a text file stream (`Reader.update`).
struct StreamState {
    /// Characters read from the stream so far (`stream_pointer`).
    read: std::cell::Cell<usize>,
    /// Characters checked and moved into the buffer.
    loaded: std::cell::Cell<usize>,
    eof: std::cell::Cell<bool>,
    /// The trailing NUL has been appended (`raw_buffer` is `None`).
    finished: std::cell::Cell<bool>,
    failed: std::cell::RefCell<Option<YamlError>>,
}

/// `Reader.update_raw`'s default size, in characters for a text stream.
const STREAM_READ_SIZE: usize = 4096;

/// `Reader.NON_PRINTABLE`'s complement: what YAML allows.
fn printable(character: char) -> bool {
    matches!(character,
        '\u{9}' | '\u{A}' | '\u{D}' | '\u{20}'..='\u{7E}' | '\u{85}' | '\u{A0}'..='\u{D7FF}'
        | '\u{E000}'..='\u{FFFD}' | '\u{10000}'..='\u{10FFFF}')
}

impl Reader {
    /// `Reader(stream)` for a `str` or `bytes` input (`stream_name` `None` gives
    /// `"<unicode string>"`). It checks every character before scanning starts, and its marks
    /// keep the input for snippets.
    pub(crate) fn new(text: &str, stream_name: Option<&str>) -> Result<Self, YamlError> {
        let name: Arc<str> = Arc::from(stream_name.unwrap_or("<unicode string>"));
        let mut buffer: Vec<char> = text.chars().collect();
        if let Some(position) = buffer.iter().position(|c| !printable(*c)) {
            let character = u32::from(buffer[position]);
            return Err(YamlError::Reader {
                name,
                position,
                character,
                reason: "special characters are not allowed",
            });
        }
        buffer.push('\0');
        let snippets = true;
        Ok(Self {
            buffer: buffer.into(),
            name,
            snippets,
            stream: None,
            pointer: 0,
            index: 0,
            line: 0,
            column: 0,
        })
    }

    /// `Reader(stream)` for a text file stream: `text` is what the stream's `read()` returns
    /// (with its newline translation already applied) and `name` is `stream.name`. Its marks
    /// carry no snippet. The first two pieces are checked here, as Python's constructor does.
    pub(crate) fn new_stream(text: &str, name: &str) -> Result<Self, YamlError> {
        let mut buffer: Vec<char> = text.chars().collect();
        buffer.push('\0');
        let state = StreamState {
            read: std::cell::Cell::new(0),
            loaded: std::cell::Cell::new(0),
            eof: std::cell::Cell::new(false),
            finished: std::cell::Cell::new(false),
            failed: std::cell::RefCell::new(None),
        };
        let reader = Self {
            buffer: buffer.into(),
            name: Arc::from(name),
            snippets: false,
            stream: Some(state),
            pointer: 0,
            index: 0,
            line: 0,
            column: 0,
        };
        // determine_encoding(): read until two characters are buffered, then update(1).
        let state = reader.stream.as_ref().expect("a stream");
        while !state.eof.get() && state.read.get() - state.loaded.get() < 2 {
            reader.update_raw(state);
        }
        reader.update(1);
        match reader.take_failure() {
            Some(error) => Err(error),
            None => Ok(reader),
        }
    }

    /// The characters a text stream holds, without the trailing NUL.
    fn text_len(&self) -> usize {
        self.buffer.len() - 1
    }

    /// `len(self.buffer)` in Python's terms: what is loaded, counted from the start.
    fn limit(&self) -> usize {
        match &self.stream {
            None => self.buffer.len(),
            Some(state) => state.loaded.get() + usize::from(state.finished.get()),
        }
    }

    fn update_raw(&self, state: &StreamState) {
        let size = STREAM_READ_SIZE.min(self.text_len() - state.read.get());
        state.read.set(state.read.get() + size);
        if size == 0 {
            state.eof.set(true);
        }
    }

    /// `Reader.update(length)`: load pieces until `length` characters follow the pointer.
    fn update(&self, length: usize) {
        let Some(state) = &self.stream else { return };
        if state.finished.get() || state.failed.borrow().is_some() {
            return;
        }
        while self.limit() - self.pointer < length {
            if !state.eof.get() {
                self.update_raw(state);
            }
            let (start, end) = (state.loaded.get(), state.read.get());
            if let Some(offset) = self.buffer[start..end].iter().position(|c| !printable(*c)) {
                *state.failed.borrow_mut() = Some(YamlError::Reader {
                    name: Arc::clone(&self.name),
                    position: start + offset,
                    character: u32::from(self.buffer[start + offset]),
                    reason: "special characters are not allowed",
                });
                return;
            }
            state.loaded.set(end);
            if state.eof.get() {
                state.finished.set(true);
                return;
            }
        }
    }

    /// A refused character a stream read met, once.
    pub(crate) fn take_failure(&self) -> Option<YamlError> {
        self.stream.as_ref().and_then(|state| state.failed.borrow().clone())
    }

    fn char_at(&self, position: usize) -> char {
        if position < self.limit() {
            self.buffer[position]
        } else {
            '\0'
        }
    }

    pub(crate) fn peek(&self, index: usize) -> char {
        if self.pointer + index >= self.limit() {
            self.update(index + 1);
        }
        self.char_at(self.pointer + index)
    }

    pub(crate) fn prefix(&self, length: usize) -> String {
        if self.pointer + length >= self.limit() {
            self.update(length);
        }
        let end = (self.pointer + length).min(self.limit());
        self.buffer[self.pointer.min(end)..end].iter().collect()
    }

    pub(crate) fn forward(&mut self, mut length: usize) {
        if self.pointer + length + 1 >= self.limit() {
            self.update(length + 1);
        }
        while length > 0 && self.pointer < self.limit() {
            let ch = self.buffer[self.pointer];
            self.pointer += 1;
            self.index += 1;
            let next = self.char_at(self.pointer);
            if matches!(ch, '\n' | '\u{85}' | '\u{2028}' | '\u{2029}') || (ch == '\r' && next != '\n') {
                self.line += 1;
                self.column = 0;
            } else if ch != '\u{FEFF}' {
                self.column += 1;
            }
            length -= 1;
        }
    }

    pub(crate) fn get_mark(&self) -> Mark {
        Mark {
            name: Arc::clone(&self.name),
            index: self.index,
            line: self.line,
            column: self.column,
            buffer: self.snippets.then(|| Arc::clone(&self.buffer)),
        }
    }
}

// ── Tokens ────────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum TokenKind {
    /// `%YAML major.minor` (digits as written) or `%TAG handle prefix`, or another directive.
    Directive {
        name: String,
        value: DirectiveValue,
    },
    DocumentStart,
    DocumentEnd,
    StreamStart,
    StreamEnd,
    BlockSequenceStart,
    BlockMappingStart,
    BlockEnd,
    FlowSequenceStart,
    FlowMappingStart,
    FlowSequenceEnd,
    FlowMappingEnd,
    Key,
    Value,
    BlockEntry,
    FlowEntry,
    Alias(String),
    Anchor(String),
    /// `(handle, suffix)`.
    Tag(Option<String>, String),
    /// `style` is `None` for plain, else the indicator character.
    Scalar {
        value: String,
        plain: bool,
        style: Option<char>,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum DirectiveValue {
    Yaml(String, String),
    Tag(String, String),
    None,
}

impl TokenKind {
    /// The token's `id`, as parser messages name it.
    pub(crate) fn id(&self) -> &'static str {
        match self {
            Self::Directive { .. } => "<directive>",
            Self::DocumentStart => "<document start>",
            Self::DocumentEnd => "<document end>",
            Self::StreamStart => "<stream start>",
            Self::StreamEnd => "<stream end>",
            Self::BlockSequenceStart => "<block sequence start>",
            Self::BlockMappingStart => "<block mapping start>",
            Self::BlockEnd => "<block end>",
            Self::FlowSequenceStart => "[",
            Self::FlowMappingStart => "{",
            Self::FlowSequenceEnd => "]",
            Self::FlowMappingEnd => "}",
            Self::Key => "?",
            Self::Value => ":",
            Self::BlockEntry => "-",
            Self::FlowEntry => ",",
            Self::Alias(_) => "<alias>",
            Self::Anchor(_) => "<anchor>",
            Self::Tag(..) => "<tag>",
            Self::Scalar { .. } => "<scalar>",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Token {
    pub kind: TokenKind,
    pub start_mark: Mark,
    pub end_mark: Mark,
}

/// Which token classes `check_token` asks about.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum TokenClass {
    Directive,
    DocumentStart,
    DocumentEnd,
    StreamEnd,
    BlockSequenceStart,
    BlockMappingStart,
    BlockEnd,
    FlowSequenceStart,
    FlowMappingStart,
    FlowSequenceEnd,
    FlowMappingEnd,
    Key,
    Value,
    BlockEntry,
    FlowEntry,
    Alias,
    Anchor,
    Tag,
    Scalar,
}

impl TokenClass {
    fn matches(self, kind: &TokenKind) -> bool {
        matches!(
            (self, kind),
            (Self::Directive, TokenKind::Directive { .. })
                | (Self::DocumentStart, TokenKind::DocumentStart)
                | (Self::DocumentEnd, TokenKind::DocumentEnd)
                | (Self::StreamEnd, TokenKind::StreamEnd)
                | (Self::BlockSequenceStart, TokenKind::BlockSequenceStart)
                | (Self::BlockMappingStart, TokenKind::BlockMappingStart)
                | (Self::BlockEnd, TokenKind::BlockEnd)
                | (Self::FlowSequenceStart, TokenKind::FlowSequenceStart)
                | (Self::FlowMappingStart, TokenKind::FlowMappingStart)
                | (Self::FlowSequenceEnd, TokenKind::FlowSequenceEnd)
                | (Self::FlowMappingEnd, TokenKind::FlowMappingEnd)
                | (Self::Key, TokenKind::Key)
                | (Self::Value, TokenKind::Value)
                | (Self::BlockEntry, TokenKind::BlockEntry)
                | (Self::FlowEntry, TokenKind::FlowEntry)
                | (Self::Alias, TokenKind::Alias(_))
                | (Self::Anchor, TokenKind::Anchor(_))
                | (Self::Tag, TokenKind::Tag(..))
                | (Self::Scalar, TokenKind::Scalar { .. })
        )
    }
}

// ── Scanner ───────────────────────────────────────────────────────────────────────────────

const BREAKS_Z: &str = "\0\r\n\u{85}\u{2028}\u{2029}";
const BLANK_BREAKS_Z: &str = "\0 \t\r\n\u{85}\u{2028}\u{2029}";
const SPACE_BREAKS_Z: &str = "\0 \r\n\u{85}\u{2028}\u{2029}";
const BREAKS: &str = "\r\n\u{85}\u{2028}\u{2029}";

fn is_in(character: char, set: &str) -> bool {
    set.contains(character)
}

fn is_word(character: char) -> bool {
    character.is_ascii_alphanumeric() || character == '-' || character == '_'
}

#[derive(Debug, Clone)]
struct SimpleKey {
    token_number: usize,
    required: bool,
    index: usize,
    line: usize,
    mark: Mark,
    column: usize,
}

type ScanResult<T> = Result<T, YamlError>;

fn scanner_error(
    context: Option<&str>,
    context_mark: Option<Mark>,
    problem: String,
    problem_mark: Mark,
) -> YamlError {
    YamlError::Marked(MarkedError::new(
        MarkedKind::Scanner,
        context.map(str::to_owned),
        context_mark,
        problem,
        Some(problem_mark),
    ))
}

pub(crate) struct Scanner {
    reader: Reader,
    done: bool,
    flow_level: usize,
    tokens: std::collections::VecDeque<Token>,
    tokens_taken: usize,
    indent: isize,
    indents: Vec<isize>,
    allow_simple_key: bool,
    /// `possible_simple_keys`, a dict keyed by flow level, in insertion order.
    possible_simple_keys: Vec<(usize, SimpleKey)>,
}

impl Scanner {
    pub(crate) fn new(reader: Reader) -> Self {
        let mut scanner = Self {
            reader,
            done: false,
            flow_level: 0,
            tokens: std::collections::VecDeque::new(),
            tokens_taken: 0,
            indent: -1,
            indents: Vec::new(),
            allow_simple_key: true,
            possible_simple_keys: Vec::new(),
        };
        let mark = scanner.reader.get_mark();
        scanner.tokens.push_back(Token {
            kind: TokenKind::StreamStart,
            start_mark: mark.clone(),
            end_mark: mark,
        });
        scanner
    }

    fn peek(&self, index: usize) -> char {
        self.reader.peek(index)
    }

    fn prefix(&self, length: usize) -> String {
        self.reader.prefix(length)
    }

    fn forward(&mut self, length: usize) {
        self.reader.forward(length);
    }

    fn mark(&self) -> Mark {
        self.reader.get_mark()
    }

    // Public methods.

    /// Fetch until the next token is decided. A refused character a stream read met wins over
    /// whatever the scan made of the NUL it read instead: Python raised at that read.
    fn fill_tokens(&mut self) -> ScanResult<()> {
        let mut filled = Ok(());
        loop {
            match self.need_more_tokens() {
                Ok(true) => {}
                Ok(false) => break,
                Err(error) => {
                    filled = Err(error);
                    break;
                }
            }
            if let Err(error) = self.fetch_more_tokens() {
                filled = Err(error);
                break;
            }
        }
        match self.reader.take_failure() {
            Some(error) => Err(error),
            None => filled,
        }
    }

    pub(crate) fn check_token(&mut self, choices: &[TokenClass]) -> ScanResult<bool> {
        self.fill_tokens()?;
        Ok(match self.tokens.front() {
            Some(token) => choices.is_empty() || choices.iter().any(|choice| choice.matches(&token.kind)),
            None => false,
        })
    }

    pub(crate) fn peek_token(&mut self) -> ScanResult<Option<&Token>> {
        self.fill_tokens()?;
        Ok(self.tokens.front())
    }

    pub(crate) fn get_token(&mut self) -> ScanResult<Option<Token>> {
        self.fill_tokens()?;
        let token = self.tokens.pop_front();
        if token.is_some() {
            self.tokens_taken += 1;
        }
        Ok(token)
    }

    // Private methods.

    fn need_more_tokens(&mut self) -> ScanResult<bool> {
        if self.done {
            return Ok(false);
        }
        if self.tokens.is_empty() {
            return Ok(true);
        }
        self.stale_possible_simple_keys()?;
        Ok(self.next_possible_simple_key() == Some(self.tokens_taken))
    }

    fn fetch_more_tokens(&mut self) -> ScanResult<()> {
        self.scan_to_next_token();
        self.stale_possible_simple_keys()?;
        self.unwind_indent(self.reader.column as isize);
        let ch = self.peek(0);
        match ch {
            '\0' => return self.fetch_stream_end(),
            '%' if self.check_directive() => return self.fetch_directive(),
            '-' if self.check_document_start() => {
                return self.fetch_document_indicator(TokenKind::DocumentStart)
            }
            '.' if self.check_document_end() => return self.fetch_document_indicator(TokenKind::DocumentEnd),
            '[' => return self.fetch_flow_collection_start(TokenKind::FlowSequenceStart),
            '{' => return self.fetch_flow_collection_start(TokenKind::FlowMappingStart),
            ']' => return self.fetch_flow_collection_end(TokenKind::FlowSequenceEnd),
            '}' => return self.fetch_flow_collection_end(TokenKind::FlowMappingEnd),
            ',' => return self.fetch_flow_entry(),
            _ => {}
        }
        if ch == '-' && self.check_block_entry() {
            return self.fetch_block_entry();
        }
        if ch == '?' && self.check_key() {
            return self.fetch_key();
        }
        if ch == ':' && self.check_value() {
            return self.fetch_value();
        }
        match ch {
            '*' => return self.fetch_alias_or_anchor(true),
            '&' => return self.fetch_alias_or_anchor(false),
            '!' => return self.fetch_tag(),
            '|' if self.flow_level == 0 => return self.fetch_block_scalar('|'),
            '>' if self.flow_level == 0 => return self.fetch_block_scalar('>'),
            '\'' => return self.fetch_flow_scalar('\''),
            '"' => return self.fetch_flow_scalar('"'),
            _ => {}
        }
        if self.check_plain() {
            return self.fetch_plain();
        }
        Err(scanner_error(
            Some("while scanning for the next token"),
            None,
            format!("found character {} that cannot start any token", char_repr(ch)),
            self.mark(),
        ))
    }

    fn next_possible_simple_key(&self) -> Option<usize> {
        self.possible_simple_keys.iter().map(|(_, key)| key.token_number).min()
    }

    fn stale_possible_simple_keys(&mut self) -> ScanResult<()> {
        let mut index = 0;
        while index < self.possible_simple_keys.len() {
            let key = &self.possible_simple_keys[index].1;
            if key.line != self.reader.line || self.reader.index - key.index > 1024 {
                if key.required {
                    return Err(scanner_error(
                        Some("while scanning a simple key"),
                        Some(key.mark.clone()),
                        "could not find expected ':'".into(),
                        self.mark(),
                    ));
                }
                self.possible_simple_keys.remove(index);
            } else {
                index += 1;
            }
        }
        Ok(())
    }

    fn save_possible_simple_key(&mut self) -> ScanResult<()> {
        let required = self.flow_level == 0 && self.indent == self.reader.column as isize;
        if self.allow_simple_key {
            self.remove_possible_simple_key()?;
            let token_number = self.tokens_taken + self.tokens.len();
            let key = SimpleKey {
                token_number,
                required,
                index: self.reader.index,
                line: self.reader.line,
                column: self.reader.column,
                mark: self.mark(),
            };
            match self.possible_simple_keys.iter_mut().find(|(level, _)| *level == self.flow_level) {
                Some(slot) => slot.1 = key,
                None => self.possible_simple_keys.push((self.flow_level, key)),
            }
        }
        Ok(())
    }

    fn remove_possible_simple_key(&mut self) -> ScanResult<()> {
        if let Some(position) =
            self.possible_simple_keys.iter().position(|(level, _)| *level == self.flow_level)
        {
            let key = &self.possible_simple_keys[position].1;
            if key.required {
                return Err(scanner_error(
                    Some("while scanning a simple key"),
                    Some(key.mark.clone()),
                    "could not find expected ':'".into(),
                    self.mark(),
                ));
            }
            self.possible_simple_keys.remove(position);
        }
        Ok(())
    }

    fn unwind_indent(&mut self, column: isize) {
        if self.flow_level > 0 {
            return;
        }
        while self.indent > column {
            let mark = self.mark();
            self.indent = self.indents.pop().unwrap_or(-1);
            self.tokens.push_back(Token {
                kind: TokenKind::BlockEnd,
                start_mark: mark.clone(),
                end_mark: mark,
            });
        }
    }

    fn add_indent(&mut self, column: isize) -> bool {
        if self.indent < column {
            self.indents.push(self.indent);
            self.indent = column;
            return true;
        }
        false
    }

    fn push(&mut self, kind: TokenKind, start_mark: Mark, end_mark: Mark) {
        self.tokens.push_back(Token { kind, start_mark, end_mark });
    }

    fn fetch_stream_end(&mut self) -> ScanResult<()> {
        self.unwind_indent(-1);
        self.remove_possible_simple_key()?;
        self.allow_simple_key = false;
        self.possible_simple_keys.clear();
        let mark = self.mark();
        self.push(TokenKind::StreamEnd, mark.clone(), mark);
        self.done = true;
        Ok(())
    }

    fn fetch_directive(&mut self) -> ScanResult<()> {
        self.unwind_indent(-1);
        self.remove_possible_simple_key()?;
        self.allow_simple_key = false;
        let token = self.scan_directive()?;
        self.tokens.push_back(token);
        Ok(())
    }

    fn fetch_document_indicator(&mut self, kind: TokenKind) -> ScanResult<()> {
        self.unwind_indent(-1);
        self.remove_possible_simple_key()?;
        self.allow_simple_key = false;
        let start_mark = self.mark();
        self.forward(3);
        let end_mark = self.mark();
        self.push(kind, start_mark, end_mark);
        Ok(())
    }

    fn fetch_flow_collection_start(&mut self, kind: TokenKind) -> ScanResult<()> {
        self.save_possible_simple_key()?;
        self.flow_level = self.flow_level.wrapping_add(1);
        self.allow_simple_key = true;
        let start_mark = self.mark();
        self.forward(1);
        let end_mark = self.mark();
        self.push(kind, start_mark, end_mark);
        Ok(())
    }

    fn fetch_flow_collection_end(&mut self, kind: TokenKind) -> ScanResult<()> {
        self.remove_possible_simple_key()?;
        // Python decrements an int; below zero it goes negative and stays truthy. Track it the
        // same way: a stray closing bracket at level 0 leaves flow context "on".
        self.flow_level = self.flow_level.wrapping_sub(1);
        self.allow_simple_key = false;
        let start_mark = self.mark();
        self.forward(1);
        let end_mark = self.mark();
        self.push(kind, start_mark, end_mark);
        Ok(())
    }

    fn fetch_flow_entry(&mut self) -> ScanResult<()> {
        self.allow_simple_key = true;
        self.remove_possible_simple_key()?;
        let start_mark = self.mark();
        self.forward(1);
        let end_mark = self.mark();
        self.push(TokenKind::FlowEntry, start_mark, end_mark);
        Ok(())
    }

    fn fetch_block_entry(&mut self) -> ScanResult<()> {
        if self.flow_level == 0 {
            if !self.allow_simple_key {
                return Err(scanner_error(
                    None,
                    None,
                    "sequence entries are not allowed here".into(),
                    self.mark(),
                ));
            }
            if self.add_indent(self.reader.column as isize) {
                let mark = self.mark();
                self.push(TokenKind::BlockSequenceStart, mark.clone(), mark);
            }
        }
        self.allow_simple_key = true;
        self.remove_possible_simple_key()?;
        let start_mark = self.mark();
        self.forward(1);
        let end_mark = self.mark();
        self.push(TokenKind::BlockEntry, start_mark, end_mark);
        Ok(())
    }

    fn fetch_key(&mut self) -> ScanResult<()> {
        if self.flow_level == 0 {
            if !self.allow_simple_key {
                return Err(scanner_error(
                    None,
                    None,
                    "mapping keys are not allowed here".into(),
                    self.mark(),
                ));
            }
            if self.add_indent(self.reader.column as isize) {
                let mark = self.mark();
                self.push(TokenKind::BlockMappingStart, mark.clone(), mark);
            }
        }
        self.allow_simple_key = self.flow_level == 0;
        self.remove_possible_simple_key()?;
        let start_mark = self.mark();
        self.forward(1);
        let end_mark = self.mark();
        self.push(TokenKind::Key, start_mark, end_mark);
        Ok(())
    }

    fn fetch_value(&mut self) -> ScanResult<()> {
        if let Some(position) =
            self.possible_simple_keys.iter().position(|(level, _)| *level == self.flow_level)
        {
            let (_, key) = self.possible_simple_keys.remove(position);
            let at = key.token_number - self.tokens_taken;
            self.tokens.insert(
                at,
                Token { kind: TokenKind::Key, start_mark: key.mark.clone(), end_mark: key.mark.clone() },
            );
            if self.flow_level == 0 && self.add_indent(key.column as isize) {
                self.tokens.insert(
                    at,
                    Token {
                        kind: TokenKind::BlockMappingStart,
                        start_mark: key.mark.clone(),
                        end_mark: key.mark,
                    },
                );
            }
            self.allow_simple_key = false;
        } else {
            if self.flow_level == 0 && !self.allow_simple_key {
                return Err(scanner_error(
                    None,
                    None,
                    "mapping values are not allowed here".into(),
                    self.mark(),
                ));
            }
            if self.flow_level == 0 && self.add_indent(self.reader.column as isize) {
                let mark = self.mark();
                self.push(TokenKind::BlockMappingStart, mark.clone(), mark);
            }
            self.allow_simple_key = self.flow_level == 0;
            self.remove_possible_simple_key()?;
        }
        let start_mark = self.mark();
        self.forward(1);
        let end_mark = self.mark();
        self.push(TokenKind::Value, start_mark, end_mark);
        Ok(())
    }

    fn fetch_alias_or_anchor(&mut self, alias: bool) -> ScanResult<()> {
        self.save_possible_simple_key()?;
        self.allow_simple_key = false;
        let token = self.scan_anchor(alias)?;
        self.tokens.push_back(token);
        Ok(())
    }

    fn fetch_tag(&mut self) -> ScanResult<()> {
        self.save_possible_simple_key()?;
        self.allow_simple_key = false;
        let token = self.scan_tag()?;
        self.tokens.push_back(token);
        Ok(())
    }

    fn fetch_block_scalar(&mut self, style: char) -> ScanResult<()> {
        self.allow_simple_key = true;
        self.remove_possible_simple_key()?;
        let token = self.scan_block_scalar(style)?;
        self.tokens.push_back(token);
        Ok(())
    }

    fn fetch_flow_scalar(&mut self, style: char) -> ScanResult<()> {
        self.save_possible_simple_key()?;
        self.allow_simple_key = false;
        let token = self.scan_flow_scalar(style)?;
        self.tokens.push_back(token);
        Ok(())
    }

    fn fetch_plain(&mut self) -> ScanResult<()> {
        self.save_possible_simple_key()?;
        self.allow_simple_key = false;
        let token = self.scan_plain();
        self.tokens.push_back(token);
        Ok(())
    }

    // Checkers.

    fn check_directive(&self) -> bool {
        self.reader.column == 0
    }

    fn check_document_start(&self) -> bool {
        self.reader.column == 0 && self.prefix(3) == "---" && is_in(self.peek(3), BLANK_BREAKS_Z)
    }

    fn check_document_end(&self) -> bool {
        self.reader.column == 0 && self.prefix(3) == "..." && is_in(self.peek(3), BLANK_BREAKS_Z)
    }

    fn check_block_entry(&self) -> bool {
        is_in(self.peek(1), BLANK_BREAKS_Z)
    }

    fn check_key(&self) -> bool {
        self.flow_level > 0 || is_in(self.peek(1), BLANK_BREAKS_Z)
    }

    fn check_value(&self) -> bool {
        self.flow_level > 0 || is_in(self.peek(1), BLANK_BREAKS_Z)
    }

    fn check_plain(&self) -> bool {
        let ch = self.peek(0);
        !is_in(ch, "\0 \t\r\n\u{85}\u{2028}\u{2029}-?:,[]{}#&*!|>'\"%@`")
            || (!is_in(self.peek(1), BLANK_BREAKS_Z)
                && (ch == '-' || (self.flow_level == 0 && is_in(ch, "?:"))))
    }

    // Scanners.

    fn scan_to_next_token(&mut self) {
        if self.reader.index == 0 && self.peek(0) == '\u{FEFF}' {
            self.forward(1);
        }
        loop {
            while self.peek(0) == ' ' {
                self.forward(1);
            }
            if self.peek(0) == '#' {
                while !is_in(self.peek(0), BREAKS_Z) {
                    self.forward(1);
                }
            }
            if self.scan_line_break().is_empty() {
                break;
            }
            if self.flow_level == 0 {
                self.allow_simple_key = true;
            }
        }
    }

    fn scan_directive(&mut self) -> ScanResult<Token> {
        let start_mark = self.mark();
        self.forward(1);
        let name = self.scan_directive_name(&start_mark)?;
        let (value, end_mark) = if name == "YAML" {
            let value = self.scan_yaml_directive_value(&start_mark)?;
            (value, self.mark())
        } else if name == "TAG" {
            let value = self.scan_tag_directive_value(&start_mark)?;
            (value, self.mark())
        } else {
            let end_mark = self.mark();
            while !is_in(self.peek(0), BREAKS_Z) {
                self.forward(1);
            }
            (DirectiveValue::None, end_mark)
        };
        self.scan_directive_ignored_line(&start_mark)?;
        Ok(Token { kind: TokenKind::Directive { name, value }, start_mark, end_mark })
    }

    fn directive_error(&self, start_mark: &Mark, problem: String) -> YamlError {
        scanner_error(Some("while scanning a directive"), Some(start_mark.clone()), problem, self.mark())
    }

    fn scan_directive_name(&mut self, start_mark: &Mark) -> ScanResult<String> {
        let mut length = 0;
        let mut ch = self.peek(length);
        while is_word(ch) {
            length += 1;
            ch = self.peek(length);
        }
        if length == 0 {
            return Err(self.directive_error(
                start_mark,
                format!("expected alphabetic or numeric character, but found {}", char_repr(ch)),
            ));
        }
        let value = self.prefix(length);
        self.forward(length);
        let ch = self.peek(0);
        if !is_in(ch, SPACE_BREAKS_Z) {
            return Err(self.directive_error(
                start_mark,
                format!("expected alphabetic or numeric character, but found {}", char_repr(ch)),
            ));
        }
        Ok(value)
    }

    fn scan_yaml_directive_value(&mut self, start_mark: &Mark) -> ScanResult<DirectiveValue> {
        while self.peek(0) == ' ' {
            self.forward(1);
        }
        let major = self.scan_yaml_directive_number(start_mark)?;
        if self.peek(0) != '.' {
            return Err(self.directive_error(
                start_mark,
                format!("expected a digit or '.', but found {}", char_repr(self.peek(0))),
            ));
        }
        self.forward(1);
        let minor = self.scan_yaml_directive_number(start_mark)?;
        if !is_in(self.peek(0), SPACE_BREAKS_Z) {
            return Err(self.directive_error(
                start_mark,
                format!("expected a digit or ' ', but found {}", char_repr(self.peek(0))),
            ));
        }
        Ok(DirectiveValue::Yaml(major, minor))
    }

    fn scan_yaml_directive_number(&mut self, start_mark: &Mark) -> ScanResult<String> {
        let ch = self.peek(0);
        if !ch.is_ascii_digit() {
            return Err(
                self.directive_error(start_mark, format!("expected a digit, but found {}", char_repr(ch)))
            );
        }
        let mut length = 0;
        while self.peek(length).is_ascii_digit() {
            length += 1;
        }
        let value = self.prefix(length);
        self.forward(length);
        Ok(value)
    }

    fn scan_tag_directive_value(&mut self, start_mark: &Mark) -> ScanResult<DirectiveValue> {
        while self.peek(0) == ' ' {
            self.forward(1);
        }
        let handle = self.scan_tag_handle("directive", start_mark)?;
        if self.peek(0) != ' ' {
            return Err(self.directive_error(
                start_mark,
                format!("expected ' ', but found {}", char_repr(self.peek(0))),
            ));
        }
        while self.peek(0) == ' ' {
            self.forward(1);
        }
        let prefix = self.scan_tag_uri("directive", start_mark)?;
        if !is_in(self.peek(0), SPACE_BREAKS_Z) {
            return Err(self.directive_error(
                start_mark,
                format!("expected ' ', but found {}", char_repr(self.peek(0))),
            ));
        }
        Ok(DirectiveValue::Tag(handle, prefix))
    }

    fn scan_directive_ignored_line(&mut self, start_mark: &Mark) -> ScanResult<()> {
        while self.peek(0) == ' ' {
            self.forward(1);
        }
        if self.peek(0) == '#' {
            while !is_in(self.peek(0), BREAKS_Z) {
                self.forward(1);
            }
        }
        let ch = self.peek(0);
        if !is_in(ch, BREAKS_Z) {
            return Err(self.directive_error(
                start_mark,
                format!("expected a comment or a line break, but found {}", char_repr(ch)),
            ));
        }
        self.scan_line_break();
        Ok(())
    }

    fn scan_anchor(&mut self, alias: bool) -> ScanResult<Token> {
        let start_mark = self.mark();
        let name = if alias { "alias" } else { "anchor" };
        self.forward(1);
        let mut length = 0;
        let mut ch = self.peek(length);
        while is_word(ch) {
            length += 1;
            ch = self.peek(length);
        }
        let error = |scanner: &Self, ch: char| {
            scanner_error(
                Some(&format!("while scanning an {name}")),
                Some(start_mark.clone()),
                format!("expected alphabetic or numeric character, but found {}", char_repr(ch)),
                scanner.mark(),
            )
        };
        if length == 0 {
            return Err(error(self, ch));
        }
        let value = self.prefix(length);
        self.forward(length);
        let ch = self.peek(0);
        if !is_in(ch, "\0 \t\r\n\u{85}\u{2028}\u{2029}?:,]}%@`") {
            return Err(error(self, ch));
        }
        let end_mark = self.mark();
        let kind = if alias { TokenKind::Alias(value) } else { TokenKind::Anchor(value) };
        Ok(Token { kind, start_mark, end_mark })
    }

    fn scan_tag(&mut self) -> ScanResult<Token> {
        let start_mark = self.mark();
        let mut ch = self.peek(1);
        let (handle, suffix);
        if ch == '<' {
            handle = None;
            self.forward(2);
            suffix = self.scan_tag_uri("tag", &start_mark)?;
            if self.peek(0) != '>' {
                return Err(scanner_error(
                    Some("while parsing a tag"),
                    Some(start_mark.clone()),
                    format!("expected '>', but found {}", char_repr(self.peek(0))),
                    self.mark(),
                ));
            }
            self.forward(1);
        } else if is_in(ch, BLANK_BREAKS_Z) {
            handle = None;
            suffix = "!".to_owned();
            self.forward(1);
        } else {
            let mut length = 1;
            let mut use_handle = false;
            while !is_in(ch, SPACE_BREAKS_Z) {
                if ch == '!' {
                    use_handle = true;
                    break;
                }
                length += 1;
                ch = self.peek(length);
            }
            if use_handle {
                handle = Some(self.scan_tag_handle("tag", &start_mark)?);
            } else {
                handle = Some("!".to_owned());
                self.forward(1);
            }
            suffix = self.scan_tag_uri("tag", &start_mark)?;
        }
        let ch = self.peek(0);
        if !is_in(ch, SPACE_BREAKS_Z) {
            return Err(scanner_error(
                Some("while scanning a tag"),
                Some(start_mark),
                format!("expected ' ', but found {}", char_repr(ch)),
                self.mark(),
            ));
        }
        let end_mark = self.mark();
        Ok(Token { kind: TokenKind::Tag(handle, suffix), start_mark, end_mark })
    }

    fn scan_block_scalar(&mut self, style: char) -> ScanResult<Token> {
        let folded = style == '>';
        let mut chunks: Vec<String> = Vec::new();
        let start_mark = self.mark();
        self.forward(1);
        let (chomping, increment) = self.scan_block_scalar_indicators(&start_mark)?;
        self.scan_block_scalar_ignored_line(&start_mark)?;
        let min_indent = (self.indent + 1).max(1) as usize;
        let (mut breaks, mut end_mark, indent);
        match increment {
            None => {
                let (found, max_indent, mark) = self.scan_block_scalar_indentation();
                breaks = found;
                end_mark = mark;
                indent = min_indent.max(max_indent);
            }
            Some(increment) => {
                indent = min_indent + increment - 1;
                let (found, mark) = self.scan_block_scalar_breaks(indent);
                breaks = found;
                end_mark = mark;
            }
        }
        let mut line_break = String::new();
        while self.reader.column == indent && self.peek(0) != '\0' {
            chunks.append(&mut breaks);
            let leading_non_space = !is_in(self.peek(0), " \t");
            let mut length = 0;
            while !is_in(self.peek(length), BREAKS_Z) {
                length += 1;
            }
            chunks.push(self.prefix(length));
            self.forward(length);
            line_break = self.scan_line_break();
            let (found, mark) = self.scan_block_scalar_breaks(indent);
            breaks = found;
            end_mark = mark;
            if self.reader.column == indent && self.peek(0) != '\0' {
                if folded && line_break == "\n" && leading_non_space && !is_in(self.peek(0), " \t") {
                    if breaks.is_empty() {
                        chunks.push(" ".into());
                    }
                } else {
                    chunks.push(line_break.clone());
                }
            } else {
                break;
            }
        }
        if chomping != Some(false) {
            chunks.push(line_break);
        }
        if chomping == Some(true) {
            chunks.append(&mut breaks);
        }
        Ok(Token {
            kind: TokenKind::Scalar { value: chunks.concat(), plain: false, style: Some(style) },
            start_mark,
            end_mark,
        })
    }

    fn block_scalar_error(&self, start_mark: &Mark, problem: String) -> YamlError {
        scanner_error(Some("while scanning a block scalar"), Some(start_mark.clone()), problem, self.mark())
    }

    fn scan_block_scalar_indicators(
        &mut self,
        start_mark: &Mark,
    ) -> ScanResult<(Option<bool>, Option<usize>)> {
        let mut chomping = None;
        let mut increment = None;
        let mut ch = self.peek(0);
        if is_in(ch, "+-") {
            chomping = Some(ch == '+');
            self.forward(1);
            ch = self.peek(0);
            if ch.is_ascii_digit() {
                let value = ch as usize - '0' as usize;
                if value == 0 {
                    return Err(self.block_scalar_error(
                        start_mark,
                        "expected indentation indicator in the range 1-9, but found 0".into(),
                    ));
                }
                increment = Some(value);
                self.forward(1);
            }
        } else if ch.is_ascii_digit() {
            let value = ch as usize - '0' as usize;
            if value == 0 {
                return Err(self.block_scalar_error(
                    start_mark,
                    "expected indentation indicator in the range 1-9, but found 0".into(),
                ));
            }
            increment = Some(value);
            self.forward(1);
            ch = self.peek(0);
            if is_in(ch, "+-") {
                chomping = Some(ch == '+');
                self.forward(1);
            }
        }
        let ch = self.peek(0);
        if !is_in(ch, SPACE_BREAKS_Z) {
            return Err(self.block_scalar_error(
                start_mark,
                format!("expected chomping or indentation indicators, but found {}", char_repr(ch)),
            ));
        }
        Ok((chomping, increment))
    }

    fn scan_block_scalar_ignored_line(&mut self, start_mark: &Mark) -> ScanResult<()> {
        while self.peek(0) == ' ' {
            self.forward(1);
        }
        if self.peek(0) == '#' {
            while !is_in(self.peek(0), BREAKS_Z) {
                self.forward(1);
            }
        }
        let ch = self.peek(0);
        if !is_in(ch, BREAKS_Z) {
            return Err(self.block_scalar_error(
                start_mark,
                format!("expected a comment or a line break, but found {}", char_repr(ch)),
            ));
        }
        self.scan_line_break();
        Ok(())
    }

    fn scan_block_scalar_indentation(&mut self) -> (Vec<String>, usize, Mark) {
        let mut chunks = Vec::new();
        let mut max_indent = 0;
        let mut end_mark = self.mark();
        while is_in(self.peek(0), " \r\n\u{85}\u{2028}\u{2029}") && self.peek(0) != '\0' {
            if self.peek(0) != ' ' {
                chunks.push(self.scan_line_break());
                end_mark = self.mark();
            } else {
                self.forward(1);
                if self.reader.column > max_indent {
                    max_indent = self.reader.column;
                }
            }
        }
        (chunks, max_indent, end_mark)
    }

    fn scan_block_scalar_breaks(&mut self, indent: usize) -> (Vec<String>, Mark) {
        let mut chunks = Vec::new();
        let mut end_mark = self.mark();
        while self.reader.column < indent && self.peek(0) == ' ' {
            self.forward(1);
        }
        while is_in(self.peek(0), BREAKS) && self.peek(0) != '\0' {
            chunks.push(self.scan_line_break());
            end_mark = self.mark();
            while self.reader.column < indent && self.peek(0) == ' ' {
                self.forward(1);
            }
        }
        (chunks, end_mark)
    }

    fn scan_flow_scalar(&mut self, style: char) -> ScanResult<Token> {
        let double = style == '"';
        let mut chunks: Vec<String> = Vec::new();
        let start_mark = self.mark();
        let quote = self.peek(0);
        self.forward(1);
        chunks.extend(self.scan_flow_scalar_non_spaces(double, &start_mark)?);
        while self.peek(0) != quote {
            chunks.extend(self.scan_flow_scalar_spaces(double, &start_mark)?);
            chunks.extend(self.scan_flow_scalar_non_spaces(double, &start_mark)?);
        }
        self.forward(1);
        let end_mark = self.mark();
        Ok(Token {
            kind: TokenKind::Scalar { value: chunks.concat(), plain: false, style: Some(style) },
            start_mark,
            end_mark,
        })
    }

    fn scan_flow_scalar_non_spaces(&mut self, double: bool, start_mark: &Mark) -> ScanResult<Vec<String>> {
        let mut chunks = Vec::new();
        loop {
            let mut length = 0;
            while !is_in(self.peek(length), "'\"\\\0 \t\r\n\u{85}\u{2028}\u{2029}") {
                length += 1;
            }
            if length > 0 {
                chunks.push(self.prefix(length));
                self.forward(length);
            }
            let ch = self.peek(0);
            if !double && ch == '\'' && self.peek(1) == '\'' {
                chunks.push("'".into());
                self.forward(2);
            } else if (double && ch == '\'') || (!double && is_in(ch, "\"\\")) {
                chunks.push(ch.to_string());
                self.forward(1);
            } else if double && ch == '\\' {
                self.forward(1);
                let ch = self.peek(0);
                if let Some(replacement) = escape_replacement(ch) {
                    chunks.push(replacement.to_string());
                    self.forward(1);
                } else if let Some(length) = escape_code_length(ch) {
                    self.forward(1);
                    for k in 0..length {
                        if !self.peek(k).is_ascii_hexdigit() {
                            return Err(scanner_error(
                                Some("while scanning a double-quoted scalar"),
                                Some(start_mark.clone()),
                                format!(
                                    "expected escape sequence of {length} hexadecimal numbers, but found {}",
                                    char_repr(self.peek(k))
                                ),
                                self.mark(),
                            ));
                        }
                    }
                    let code = u32::from_str_radix(&self.prefix(length), 16).unwrap_or(u32::MAX);
                    let character = match char::from_u32(code) {
                        Some(character) => character,
                        None if code <= 0x10FFFF => {
                            // A lone surrogate: Python makes a `str` that Rust cannot hold
                            // (differences.md row 3).
                            return Err(YamlError::python(
                                "UnicodeError",
                                format!("the escape \\{ch}{:0length$x} is a lone surrogate", code),
                            ));
                        }
                        // chr() takes a C int: beyond it, the conversion overflows first.
                        None if code > 0x7FFF_FFFF => {
                            return Err(YamlError::python(
                                "OverflowError",
                                "Python int too large to convert to C int",
                            ))
                        }
                        None => {
                            return Err(YamlError::python("ValueError", "chr() arg not in range(0x110000)"))
                        }
                    };
                    chunks.push(character.to_string());
                    self.forward(length);
                } else if is_in(ch, BREAKS) && ch != '\0' {
                    self.scan_line_break();
                    chunks.extend(self.scan_flow_scalar_breaks(start_mark)?);
                } else {
                    return Err(scanner_error(
                        Some("while scanning a double-quoted scalar"),
                        Some(start_mark.clone()),
                        format!("found unknown escape character {}", char_repr(ch)),
                        self.mark(),
                    ));
                }
            } else {
                return Ok(chunks);
            }
        }
    }

    fn scan_flow_scalar_spaces(&mut self, _double: bool, start_mark: &Mark) -> ScanResult<Vec<String>> {
        let mut chunks = Vec::new();
        let mut length = 0;
        while is_in(self.peek(length), " \t") {
            length += 1;
        }
        let whitespaces = self.prefix(length);
        self.forward(length);
        let ch = self.peek(0);
        if ch == '\0' {
            return Err(scanner_error(
                Some("while scanning a quoted scalar"),
                Some(start_mark.clone()),
                "found unexpected end of stream".into(),
                self.mark(),
            ));
        } else if is_in(ch, BREAKS) {
            let line_break = self.scan_line_break();
            let breaks = self.scan_flow_scalar_breaks(start_mark)?;
            if line_break != "\n" {
                chunks.push(line_break);
            } else if breaks.is_empty() {
                chunks.push(" ".into());
            }
            chunks.extend(breaks);
        } else {
            chunks.push(whitespaces);
        }
        Ok(chunks)
    }

    fn scan_flow_scalar_breaks(&mut self, start_mark: &Mark) -> ScanResult<Vec<String>> {
        let mut chunks = Vec::new();
        loop {
            let prefix = self.prefix(3);
            if (prefix == "---" || prefix == "...") && is_in(self.peek(3), BLANK_BREAKS_Z) {
                return Err(scanner_error(
                    Some("while scanning a quoted scalar"),
                    Some(start_mark.clone()),
                    "found unexpected document separator".into(),
                    self.mark(),
                ));
            }
            while is_in(self.peek(0), " \t") {
                self.forward(1);
            }
            if is_in(self.peek(0), BREAKS) && self.peek(0) != '\0' {
                chunks.push(self.scan_line_break());
            } else {
                return Ok(chunks);
            }
        }
    }

    fn scan_plain(&mut self) -> Token {
        let mut chunks: Vec<String> = Vec::new();
        let start_mark = self.mark();
        let mut end_mark = start_mark.clone();
        let indent = self.indent + 1;
        let mut spaces: Vec<String> = Vec::new();
        loop {
            let mut length = 0;
            if self.peek(0) == '#' {
                break;
            }
            loop {
                let ch = self.peek(length);
                let after = self.peek(length + 1);
                let ends_value = ch == ':'
                    && (is_in(after, BLANK_BREAKS_Z) || (self.flow_level > 0 && is_in(after, ",[]{}")));
                if is_in(ch, BLANK_BREAKS_Z) || ends_value || (self.flow_level > 0 && is_in(ch, ",?[]{}")) {
                    break;
                }
                length += 1;
            }
            if length == 0 {
                break;
            }
            self.allow_simple_key = false;
            chunks.append(&mut spaces);
            chunks.push(self.prefix(length));
            self.forward(length);
            end_mark = self.mark();
            match self.scan_plain_spaces() {
                None => break,
                Some(found) => {
                    spaces = found;
                    if spaces.is_empty()
                        || self.peek(0) == '#'
                        || (self.flow_level == 0 && (self.reader.column as isize) < indent)
                    {
                        break;
                    }
                }
            }
        }
        Token {
            kind: TokenKind::Scalar { value: chunks.concat(), plain: true, style: None },
            start_mark,
            end_mark,
        }
    }

    /// `scan_plain_spaces`; `None` where Python returns `None` (a document separator).
    fn scan_plain_spaces(&mut self) -> Option<Vec<String>> {
        let mut chunks = Vec::new();
        let mut length = 0;
        while self.peek(length) == ' ' {
            length += 1;
        }
        let whitespaces = self.prefix(length);
        self.forward(length);
        let ch = self.peek(0);
        if is_in(ch, BREAKS) && ch != '\0' {
            let line_break = self.scan_line_break();
            self.allow_simple_key = true;
            let prefix = self.prefix(3);
            if (prefix == "---" || prefix == "...") && is_in(self.peek(3), BLANK_BREAKS_Z) {
                return None;
            }
            let mut breaks = Vec::new();
            while is_in(self.peek(0), " \r\n\u{85}\u{2028}\u{2029}") && self.peek(0) != '\0' {
                if self.peek(0) == ' ' {
                    self.forward(1);
                } else {
                    breaks.push(self.scan_line_break());
                    let prefix = self.prefix(3);
                    if (prefix == "---" || prefix == "...") && is_in(self.peek(3), BLANK_BREAKS_Z) {
                        return None;
                    }
                }
            }
            if line_break != "\n" {
                chunks.push(line_break);
            } else if breaks.is_empty() {
                chunks.push(" ".into());
            }
            chunks.extend(breaks);
        } else if !whitespaces.is_empty() {
            chunks.push(whitespaces);
        }
        Some(chunks)
    }

    fn scan_tag_handle(&mut self, name: &str, start_mark: &Mark) -> ScanResult<String> {
        let mut ch = self.peek(0);
        if ch != '!' {
            return Err(scanner_error(
                Some(&format!("while scanning a {name}")),
                Some(start_mark.clone()),
                format!("expected '!', but found {}", char_repr(ch)),
                self.mark(),
            ));
        }
        let mut length = 1;
        ch = self.peek(length);
        if ch != ' ' {
            while is_word(ch) {
                length += 1;
                ch = self.peek(length);
            }
            if ch != '!' {
                self.forward(length);
                return Err(scanner_error(
                    Some(&format!("while scanning a {name}")),
                    Some(start_mark.clone()),
                    format!("expected '!', but found {}", char_repr(ch)),
                    self.mark(),
                ));
            }
            length += 1;
        }
        let value = self.prefix(length);
        self.forward(length);
        Ok(value)
    }

    fn scan_tag_uri(&mut self, name: &str, start_mark: &Mark) -> ScanResult<String> {
        let mut chunks = Vec::new();
        let mut length = 0;
        let mut ch = self.peek(length);
        while ch.is_ascii_alphanumeric() || is_in(ch, "-;/?:@&=+$,_.!~*'()[]%") && ch != '\0' {
            if ch == '%' {
                chunks.push(self.prefix(length));
                self.forward(length);
                length = 0;
                chunks.push(self.scan_uri_escapes(name, start_mark)?);
            } else {
                length += 1;
            }
            ch = self.peek(length);
        }
        if length > 0 {
            chunks.push(self.prefix(length));
            self.forward(length);
        }
        if chunks.is_empty() {
            return Err(scanner_error(
                Some(&format!("while parsing a {name}")),
                Some(start_mark.clone()),
                format!("expected URI, but found {}", char_repr(ch)),
                self.mark(),
            ));
        }
        Ok(chunks.concat())
    }

    fn scan_uri_escapes(&mut self, name: &str, start_mark: &Mark) -> ScanResult<String> {
        let mut codes = Vec::new();
        let mark = self.mark();
        while self.peek(0) == '%' {
            self.forward(1);
            for k in 0..2 {
                if !self.peek(k).is_ascii_hexdigit() {
                    return Err(scanner_error(
                        Some(&format!("while scanning a {name}")),
                        Some(start_mark.clone()),
                        format!(
                            "expected URI escape sequence of 2 hexadecimal numbers, but found {}",
                            char_repr(self.peek(k))
                        ),
                        self.mark(),
                    ));
                }
            }
            codes.push(u8::from_str_radix(&self.prefix(2), 16).unwrap_or(0));
            self.forward(2);
        }
        String::from_utf8(codes.clone()).map_err(|error| {
            scanner_error(
                Some(&format!("while scanning a {name}")),
                Some(start_mark.clone()),
                utf8_error_text(&codes, &error),
                mark,
            )
        })
    }

    fn scan_line_break(&mut self) -> String {
        let ch = self.peek(0);
        if is_in(ch, "\r\n\u{85}") && ch != '\0' {
            if self.prefix(2) == "\r\n" {
                self.forward(2);
            } else {
                self.forward(1);
            }
            return "\n".into();
        } else if is_in(ch, "\u{2028}\u{2029}") {
            self.forward(1);
            return ch.to_string();
        }
        String::new()
    }
}

fn escape_replacement(ch: char) -> Option<char> {
    Some(match ch {
        '0' => '\0',
        'a' => '\u{7}',
        'b' => '\u{8}',
        't' | '\t' => '\u{9}',
        'n' => '\u{A}',
        'v' => '\u{B}',
        'f' => '\u{C}',
        'r' => '\u{D}',
        'e' => '\u{1B}',
        ' ' => ' ',
        '"' => '"',
        '\\' => '\\',
        '/' => '/',
        'N' => '\u{85}',
        '_' => '\u{A0}',
        'L' => '\u{2028}',
        'P' => '\u{2029}',
        _ => return None,
    })
}

fn escape_code_length(ch: char) -> Option<usize> {
    match ch {
        'x' => Some(2),
        'u' => Some(4),
        'U' => Some(8),
        _ => None,
    }
}

/// `str(UnicodeDecodeError)` for `bytes(codes).decode('utf-8')`.
pub(crate) fn utf8_error_text(codes: &[u8], error: &std::string::FromUtf8Error) -> String {
    let start = error.utf8_error().valid_up_to();
    let byte = codes[start];
    match error.utf8_error().error_len() {
        None if codes.len() - start > 1 => {
            format!(
                "'utf-8' codec can't decode bytes in position {start}-{}: unexpected end of data",
                codes.len() - 1
            )
        }
        None => format!(
            "'utf-8' codec can't decode byte 0x{byte:02x} in position {start}: unexpected end of data"
        ),
        Some(_) => {
            let reason = if (0x80..=0xC1).contains(&byte) || byte >= 0xF5 {
                "invalid start byte"
            } else {
                "invalid continuation byte"
            };
            format!("'utf-8' codec can't decode byte 0x{byte:02x} in position {start}: {reason}")
        }
    }
}
