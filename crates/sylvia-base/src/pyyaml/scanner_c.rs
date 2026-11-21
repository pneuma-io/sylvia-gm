//! libyaml 0.2.5's reader and scanner (`src/reader.c`, `src/scanner.c`), for [`Loader::C`].
//!
//! `yaml.CSafeLoader` hands a `str` to libyaml as UTF-8 bytes. libyaml reads them in 16 KiB
//! chunks, strips a leading byte order mark, and checks each character as it decodes it, so a
//! refused character is reported only when the scanner first needs the chunk holding it. The
//! scanner allows tabs where PyYAML refuses them, forces a final line break at the end of the
//! stream, and words its errors in its own way. This file follows the C source function for
//! function, including its `CACHE` calls, so that every value, mark and error matches.
//!
//! [`Loader::C`]: super::Loader::C

use super::error::{Mark, MarkedError, MarkedKind, YamlError};
use std::collections::VecDeque;
use std::rc::Rc;

/// `YAML_RAW_BUFFER_SIZE`.
const RAW_BUFFER_SIZE: usize = 16384;

type CResult<T> = Result<T, YamlError>;

/// `yaml_mark_t`: `index` counts characters (a CR LF pair counts two).
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub(crate) struct CMark {
    pub index: usize,
    pub line: usize,
    pub column: usize,
}

/// The token types of `yaml_token_type_t`, with their data.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum CTok {
    StreamStart,
    StreamEnd,
    VersionDirective {
        major: i64,
        minor: i64,
    },
    /// Handle and prefix as libyaml holds them: bytes, cut at the first NUL as C reads them.
    TagDirective {
        handle: Vec<u8>,
        prefix: Vec<u8>,
    },
    DocumentStart,
    DocumentEnd,
    BlockSequenceStart,
    BlockMappingStart,
    BlockEnd,
    FlowSequenceStart,
    FlowSequenceEnd,
    FlowMappingStart,
    FlowMappingEnd,
    BlockEntry,
    FlowEntry,
    Key,
    Value,
    Alias(Vec<u8>),
    Anchor(Vec<u8>),
    Tag {
        handle: Vec<u8>,
        suffix: Vec<u8>,
    },
    /// The value, and the style as `_yaml.pyx` names it (`None` for plain).
    Scalar {
        value: String,
        style: Option<char>,
    },
}

#[derive(Debug, Clone)]
pub(crate) struct CToken {
    pub kind: CTok,
    pub start: CMark,
    pub end: CMark,
}

#[derive(Debug, Clone, Copy, Default)]
struct SimpleKey {
    possible: bool,
    required: bool,
    token_number: usize,
    mark: CMark,
}

/// The characters libyaml's reader accepts.
fn printable(value: char) -> bool {
    matches!(value,
        '\u{9}' | '\u{A}' | '\u{D}' | '\u{20}'..='\u{7E}' | '\u{85}' | '\u{A0}'..='\u{D7FF}'
        | '\u{E000}'..='\u{FFFD}' | '\u{10000}'..='\u{10FFFF}')
}

/// A C string: the bytes up to the first NUL.
fn c_string(mut bytes: Vec<u8>) -> Vec<u8> {
    if let Some(nul) = bytes.iter().position(|b| *b == 0) {
        bytes.truncate(nul);
    }
    bytes
}

pub(crate) struct CScanner {
    name: Rc<str>,
    chars: Vec<char>,
    /// Byte length of each character in UTF-8.
    widths: Vec<usize>,
    total_bytes: usize,
    // Reader state (`yaml_parser_update_buffer`).
    encoding_known: bool,
    raw_read: usize,
    raw_pos: usize,
    eof: bool,
    decoded_end: usize,
    nul_appended: bool,
    pointer: usize,
    pub(crate) mark: CMark,
    // Scanner state.
    tokens: VecDeque<CToken>,
    pub(crate) tokens_parsed: usize,
    pub(crate) token_available: bool,
    stream_start_produced: bool,
    pub(crate) stream_end_produced: bool,
    indent: isize,
    indents: Vec<isize>,
    simple_key_allowed: bool,
    simple_keys: Vec<SimpleKey>,
    flow_level: usize,
    failed: Option<YamlError>,
}

impl CScanner {
    pub(crate) fn new(text: &str, stream_name: Option<&str>) -> Self {
        let chars: Vec<char> = text.chars().collect();
        let widths: Vec<usize> = chars.iter().map(|c| c.len_utf8()).collect();
        Self {
            name: Rc::from(stream_name.unwrap_or("<unicode string>")),
            total_bytes: text.len(),
            chars,
            widths,
            encoding_known: false,
            raw_read: 0,
            raw_pos: 0,
            eof: false,
            decoded_end: 0,
            nul_appended: false,
            pointer: 0,
            mark: CMark::default(),
            tokens: VecDeque::new(),
            tokens_parsed: 0,
            token_available: false,
            stream_start_produced: false,
            stream_end_produced: false,
            indent: -1,
            indents: Vec::new(),
            simple_key_allowed: false,
            simple_keys: Vec::new(),
            flow_level: 0,
            failed: None,
        }
    }

    /// A libyaml mark as `_yaml.pyx` turns it into a `yaml.Mark` (no snippet).
    pub(crate) fn py_mark(&self, mark: CMark) -> Mark {
        Mark {
            name: Rc::clone(&self.name),
            index: mark.index,
            line: mark.line,
            column: mark.column,
            buffer: None,
        }
    }

    // ── Reader ────────────────────────────────────────────────────────────────────────

    fn unread(&self) -> usize {
        self.decoded_end - self.pointer + usize::from(self.nul_appended)
    }

    /// `yaml_parser_update_raw_buffer` over the string input handler.
    fn update_raw_buffer(&mut self) {
        let raw_len = self.raw_read - self.raw_pos;
        if raw_len == RAW_BUFFER_SIZE || self.eof {
            return;
        }
        let size = (RAW_BUFFER_SIZE - raw_len).min(self.total_bytes - self.raw_read);
        self.raw_read += size;
        if size == 0 {
            self.eof = true;
        }
    }

    fn determine_encoding(&mut self) {
        while !self.eof && self.raw_read - self.raw_pos < 3 {
            self.update_raw_buffer();
        }
        self.encoding_known = true;
        if self.raw_read - self.raw_pos >= 3 && self.chars.first() == Some(&'\u{FEFF}') {
            self.raw_pos += 3;
            self.decoded_end = 1;
            self.pointer = 1;
        }
    }

    /// `yaml_parser_update_buffer(parser, length)`.
    fn update_buffer(&mut self, length: usize) -> CResult<()> {
        if self.eof && self.raw_pos == self.raw_read && self.nul_appended {
            return Ok(());
        }
        if self.unread() >= length {
            return Ok(());
        }
        if !self.encoding_known {
            self.determine_encoding();
        }
        let mut first = true;
        while self.unread() < length {
            if !first || self.raw_pos == self.raw_read {
                self.update_raw_buffer();
            }
            first = false;
            while self.raw_pos != self.raw_read {
                let index = self.decoded_end;
                let width = self.widths[index];
                if self.raw_read - self.raw_pos < width {
                    // An incomplete character at the end of the raw buffer waits for more.
                    break;
                }
                let value = self.chars[index];
                if !printable(value) {
                    return Err(YamlError::Reader {
                        name: Rc::clone(&self.name),
                        position: self.raw_pos,
                        character: u32::from(value),
                        reason: "control characters are not allowed",
                    });
                }
                self.raw_pos += width;
                self.decoded_end += 1;
            }
            if self.eof {
                self.nul_appended = true;
                return Ok(());
            }
        }
        Ok(())
    }

    /// `CACHE(parser, length)`.
    fn cache(&mut self, length: usize) -> CResult<()> {
        if self.unread() >= length {
            Ok(())
        } else {
            self.update_buffer(length)
        }
    }

    fn at(&self, offset: usize) -> char {
        self.chars.get(self.pointer + offset).copied().unwrap_or('\0')
    }

    fn check_at(&self, ch: char, offset: usize) -> bool {
        self.at(offset) == ch
    }

    fn check(&self, ch: char) -> bool {
        self.at(0) == ch
    }

    fn is_alpha_at(&self, offset: usize) -> bool {
        let c = self.at(offset);
        c.is_ascii_alphanumeric() || c == '_' || c == '-'
    }

    fn is_digit(&self) -> bool {
        self.at(0).is_ascii_digit()
    }

    fn is_hex_at(&self, offset: usize) -> bool {
        self.at(offset).is_ascii_hexdigit()
    }

    fn as_hex_at(&self, offset: usize) -> u32 {
        self.at(offset).to_digit(16).unwrap_or(0)
    }

    fn is_z_at(&self, offset: usize) -> bool {
        self.at(offset) == '\0'
    }

    fn is_blank_at(&self, offset: usize) -> bool {
        matches!(self.at(offset), ' ' | '\t')
    }

    fn is_blank(&self) -> bool {
        self.is_blank_at(0)
    }

    fn is_break_at(&self, offset: usize) -> bool {
        matches!(self.at(offset), '\r' | '\n' | '\u{85}' | '\u{2028}' | '\u{2029}')
    }

    fn is_break(&self) -> bool {
        self.is_break_at(0)
    }

    fn is_breakz(&self) -> bool {
        self.is_break_at(0) || self.is_z_at(0)
    }

    fn is_blankz_at(&self, offset: usize) -> bool {
        self.is_blank_at(offset) || self.is_break_at(offset) || self.is_z_at(offset)
    }

    fn is_blankz(&self) -> bool {
        self.is_blankz_at(0)
    }

    fn skip(&mut self) {
        self.mark.index += 1;
        self.mark.column += 1;
        self.pointer += 1;
    }

    fn skip_line(&mut self) {
        if self.check_at('\r', 0) && self.check_at('\n', 1) {
            self.mark.index += 2;
            self.mark.column = 0;
            self.mark.line += 1;
            self.pointer += 2;
        } else if self.is_break() {
            self.mark.index += 1;
            self.mark.column = 0;
            self.mark.line += 1;
            self.pointer += 1;
        }
    }

    fn read(&mut self, string: &mut String) {
        string.push(self.at(0));
        self.skip();
    }

    fn read_bytes(&mut self, string: &mut Vec<u8>) {
        let mut buffer = [0u8; 4];
        string.extend_from_slice(self.at(0).encode_utf8(&mut buffer).as_bytes());
        self.skip();
    }

    /// `READ_LINE`: a line break into `string`, CR LF, CR and NEL as LF; elsewhere it does
    /// nothing.
    fn read_line(&mut self, string: &mut String) {
        let ch = self.at(0);
        if ch == '\r' && self.check_at('\n', 1) {
            string.push('\n');
            self.pointer += 2;
            self.mark.index += 2;
        } else if ch == '\r' || ch == '\n' || ch == '\u{85}' {
            string.push('\n');
            self.pointer += 1;
            self.mark.index += 1;
        } else if ch == '\u{2028}' || ch == '\u{2029}' {
            string.push(ch);
            self.pointer += 1;
            self.mark.index += 1;
        } else {
            return;
        }
        self.mark.column = 0;
        self.mark.line += 1;
    }

    fn error(&self, context: Option<&str>, context_mark: CMark, problem: &str) -> YamlError {
        YamlError::Marked(MarkedError::new(
            MarkedKind::Scanner,
            context.map(str::to_owned),
            context.map(|_| self.py_mark(context_mark)),
            problem,
            Some(self.py_mark(self.mark)),
        ))
    }

    // ── Token queue ───────────────────────────────────────────────────────────────────

    fn push(&mut self, kind: CTok, start: CMark, end: CMark) {
        self.tokens.push_back(CToken { kind, start, end });
    }

    /// `PEEK_TOKEN`.
    pub(crate) fn peek_token(&mut self) -> CResult<&CToken> {
        if let Some(error) = &self.failed {
            return Err(error.clone());
        }
        if !self.token_available {
            if let Err(error) = self.fetch_more_tokens() {
                self.failed = Some(error.clone());
                return Err(error);
            }
        }
        Ok(self.tokens.front().expect("fetch_more_tokens leaves a token"))
    }

    /// `SKIP_TOKEN`.
    pub(crate) fn skip_token(&mut self) {
        self.token_available = false;
        self.tokens_parsed += 1;
        let token = self.tokens.pop_front().expect("a peeked token");
        self.stream_end_produced = matches!(token.kind, CTok::StreamEnd);
    }

    fn fetch_more_tokens(&mut self) -> CResult<()> {
        loop {
            let need_more = if self.tokens.is_empty() {
                true
            } else {
                self.stale_simple_keys()?;
                self.simple_keys.iter().any(|key| key.possible && key.token_number == self.tokens_parsed)
            };
            if !need_more {
                break;
            }
            self.fetch_next_token()?;
        }
        self.token_available = true;
        Ok(())
    }

    fn fetch_next_token(&mut self) -> CResult<()> {
        self.cache(1)?;
        if !self.stream_start_produced {
            self.fetch_stream_start();
            return Ok(());
        }
        self.scan_to_next_token()?;
        self.stale_simple_keys()?;
        self.unroll_indent(self.mark.column as isize);
        self.cache(4)?;
        if self.is_z_at(0) {
            return self.fetch_stream_end();
        }
        let column = self.mark.column;
        if column == 0 && self.check('%') {
            return self.fetch_directive();
        }
        if column == 0
            && self.check_at('-', 0)
            && self.check_at('-', 1)
            && self.check_at('-', 2)
            && self.is_blankz_at(3)
        {
            return self.fetch_document_indicator(CTok::DocumentStart);
        }
        if column == 0
            && self.check_at('.', 0)
            && self.check_at('.', 1)
            && self.check_at('.', 2)
            && self.is_blankz_at(3)
        {
            return self.fetch_document_indicator(CTok::DocumentEnd);
        }
        match self.at(0) {
            '[' => return self.fetch_flow_collection_start(CTok::FlowSequenceStart),
            '{' => return self.fetch_flow_collection_start(CTok::FlowMappingStart),
            ']' => return self.fetch_flow_collection_end(CTok::FlowSequenceEnd),
            '}' => return self.fetch_flow_collection_end(CTok::FlowMappingEnd),
            ',' => return self.fetch_flow_entry(),
            _ => {}
        }
        if self.check('-') && self.is_blankz_at(1) {
            return self.fetch_block_entry();
        }
        if self.check('?') && (self.flow_level > 0 || self.is_blankz_at(1)) {
            return self.fetch_key();
        }
        if self.check(':') && (self.flow_level > 0 || self.is_blankz_at(1)) {
            return self.fetch_value();
        }
        match self.at(0) {
            '*' => return self.fetch_anchor(false),
            '&' => return self.fetch_anchor(true),
            '!' => return self.fetch_tag(),
            '|' if self.flow_level == 0 => return self.fetch_block_scalar(true),
            '>' if self.flow_level == 0 => return self.fetch_block_scalar(false),
            '\'' => return self.fetch_flow_scalar(true),
            '"' => return self.fetch_flow_scalar(false),
            _ => {}
        }
        let ch = self.at(0);
        if !(self.is_blankz() || "-?:,[]{}#&*!|>'\"%@`".contains(ch))
            || (ch == '-' && !self.is_blank_at(1))
            || (self.flow_level == 0 && (ch == '?' || ch == ':') && !self.is_blankz_at(1))
        {
            return self.fetch_plain_scalar();
        }
        Err(self.error(
            Some("while scanning for the next token"),
            self.mark,
            "found character that cannot start any token",
        ))
    }

    fn stale_simple_keys(&mut self) -> CResult<()> {
        let mark = self.mark;
        for index in 0..self.simple_keys.len() {
            let key = self.simple_keys[index];
            if key.possible && (key.mark.line < mark.line || key.mark.index + 1024 < mark.index) {
                if key.required {
                    return Err(self.error(
                        Some("while scanning a simple key"),
                        key.mark,
                        "could not find expected ':'",
                    ));
                }
                self.simple_keys[index].possible = false;
            }
        }
        Ok(())
    }

    fn save_simple_key(&mut self) -> CResult<()> {
        let required = self.flow_level == 0 && self.indent == self.mark.column as isize;
        if self.simple_key_allowed {
            let key = SimpleKey {
                possible: true,
                required,
                token_number: self.tokens_parsed + self.tokens.len(),
                mark: self.mark,
            };
            self.remove_simple_key()?;
            *self.simple_keys.last_mut().expect("the stream start pushes a key") = key;
        }
        Ok(())
    }

    fn remove_simple_key(&mut self) -> CResult<()> {
        let key = *self.simple_keys.last().expect("the stream start pushes a key");
        if key.possible && key.required {
            return Err(self.error(
                Some("while scanning a simple key"),
                key.mark,
                "could not find expected ':'",
            ));
        }
        self.simple_keys.last_mut().expect("present").possible = false;
        Ok(())
    }

    fn increase_flow_level(&mut self) {
        self.simple_keys.push(SimpleKey::default());
        self.flow_level += 1;
    }

    fn decrease_flow_level(&mut self) {
        if self.flow_level > 0 {
            self.flow_level -= 1;
            self.simple_keys.pop();
        }
    }

    fn roll_indent(&mut self, column: isize, number: Option<usize>, kind: CTok, mark: CMark) {
        if self.flow_level > 0 {
            return;
        }
        if self.indent < column {
            self.indents.push(self.indent);
            self.indent = column;
            let token = CToken { kind, start: mark, end: mark };
            match number {
                None => self.tokens.push_back(token),
                Some(number) => self.tokens.insert(number - self.tokens_parsed, token),
            }
        }
    }

    fn unroll_indent(&mut self, column: isize) {
        if self.flow_level > 0 {
            return;
        }
        while self.indent > column {
            let mark = self.mark;
            self.push(CTok::BlockEnd, mark, mark);
            self.indent = self.indents.pop().expect("an indent for each level");
        }
    }

    fn fetch_stream_start(&mut self) {
        self.indent = -1;
        self.simple_keys.push(SimpleKey::default());
        self.simple_key_allowed = true;
        self.stream_start_produced = true;
        let mark = self.mark;
        self.push(CTok::StreamStart, mark, mark);
    }

    fn fetch_stream_end(&mut self) -> CResult<()> {
        if self.mark.column != 0 {
            self.mark.column = 0;
            self.mark.line += 1;
        }
        self.unroll_indent(-1);
        self.remove_simple_key()?;
        self.simple_key_allowed = false;
        let mark = self.mark;
        self.push(CTok::StreamEnd, mark, mark);
        Ok(())
    }

    fn fetch_directive(&mut self) -> CResult<()> {
        self.unroll_indent(-1);
        self.remove_simple_key()?;
        self.simple_key_allowed = false;
        let token = self.scan_directive()?;
        self.tokens.push_back(token);
        Ok(())
    }

    fn fetch_document_indicator(&mut self, kind: CTok) -> CResult<()> {
        self.unroll_indent(-1);
        self.remove_simple_key()?;
        self.simple_key_allowed = false;
        let start = self.mark;
        self.skip();
        self.skip();
        self.skip();
        let end = self.mark;
        self.push(kind, start, end);
        Ok(())
    }

    fn fetch_flow_collection_start(&mut self, kind: CTok) -> CResult<()> {
        self.save_simple_key()?;
        self.increase_flow_level();
        self.simple_key_allowed = true;
        let start = self.mark;
        self.skip();
        let end = self.mark;
        self.push(kind, start, end);
        Ok(())
    }

    fn fetch_flow_collection_end(&mut self, kind: CTok) -> CResult<()> {
        self.remove_simple_key()?;
        self.decrease_flow_level();
        self.simple_key_allowed = false;
        let start = self.mark;
        self.skip();
        let end = self.mark;
        self.push(kind, start, end);
        Ok(())
    }

    fn fetch_flow_entry(&mut self) -> CResult<()> {
        self.remove_simple_key()?;
        self.simple_key_allowed = true;
        let start = self.mark;
        self.skip();
        let end = self.mark;
        self.push(CTok::FlowEntry, start, end);
        Ok(())
    }

    fn fetch_block_entry(&mut self) -> CResult<()> {
        if self.flow_level == 0 {
            if !self.simple_key_allowed {
                return Err(self.error(
                    None,
                    self.mark,
                    "block sequence entries are not allowed in this context",
                ));
            }
            let mark = self.mark;
            self.roll_indent(mark.column as isize, None, CTok::BlockSequenceStart, mark);
        }
        self.remove_simple_key()?;
        self.simple_key_allowed = true;
        let start = self.mark;
        self.skip();
        let end = self.mark;
        self.push(CTok::BlockEntry, start, end);
        Ok(())
    }

    fn fetch_key(&mut self) -> CResult<()> {
        if self.flow_level == 0 {
            if !self.simple_key_allowed {
                return Err(self.error(None, self.mark, "mapping keys are not allowed in this context"));
            }
            let mark = self.mark;
            self.roll_indent(mark.column as isize, None, CTok::BlockMappingStart, mark);
        }
        self.remove_simple_key()?;
        self.simple_key_allowed = self.flow_level == 0;
        let start = self.mark;
        self.skip();
        let end = self.mark;
        self.push(CTok::Key, start, end);
        Ok(())
    }

    fn fetch_value(&mut self) -> CResult<()> {
        let key = *self.simple_keys.last().expect("the stream start pushes a key");
        if key.possible {
            self.tokens.insert(
                key.token_number - self.tokens_parsed,
                CToken { kind: CTok::Key, start: key.mark, end: key.mark },
            );
            self.roll_indent(
                key.mark.column as isize,
                Some(key.token_number),
                CTok::BlockMappingStart,
                key.mark,
            );
            self.simple_keys.last_mut().expect("present").possible = false;
            self.simple_key_allowed = false;
        } else {
            if self.flow_level == 0 {
                if !self.simple_key_allowed {
                    return Err(self.error(
                        None,
                        self.mark,
                        "mapping values are not allowed in this context",
                    ));
                }
                let mark = self.mark;
                self.roll_indent(mark.column as isize, None, CTok::BlockMappingStart, mark);
            }
            self.simple_key_allowed = self.flow_level == 0;
        }
        let start = self.mark;
        self.skip();
        let end = self.mark;
        self.push(CTok::Value, start, end);
        Ok(())
    }

    fn fetch_anchor(&mut self, anchor: bool) -> CResult<()> {
        self.save_simple_key()?;
        self.simple_key_allowed = false;
        let token = self.scan_anchor(anchor)?;
        self.tokens.push_back(token);
        Ok(())
    }

    fn fetch_tag(&mut self) -> CResult<()> {
        self.save_simple_key()?;
        self.simple_key_allowed = false;
        let token = self.scan_tag()?;
        self.tokens.push_back(token);
        Ok(())
    }

    fn fetch_block_scalar(&mut self, literal: bool) -> CResult<()> {
        self.remove_simple_key()?;
        self.simple_key_allowed = true;
        let token = self.scan_block_scalar(literal)?;
        self.tokens.push_back(token);
        Ok(())
    }

    fn fetch_flow_scalar(&mut self, single: bool) -> CResult<()> {
        self.save_simple_key()?;
        self.simple_key_allowed = false;
        let token = self.scan_flow_scalar(single)?;
        self.tokens.push_back(token);
        Ok(())
    }

    fn fetch_plain_scalar(&mut self) -> CResult<()> {
        self.save_simple_key()?;
        self.simple_key_allowed = false;
        let token = self.scan_plain_scalar()?;
        self.tokens.push_back(token);
        Ok(())
    }

    // ── Scanners ──────────────────────────────────────────────────────────────────────

    fn scan_to_next_token(&mut self) -> CResult<()> {
        loop {
            self.cache(1)?;
            if self.mark.column == 0 && self.check('\u{FEFF}') {
                self.skip();
            }
            self.cache(1)?;
            while self.check(' ') || ((self.flow_level > 0 || !self.simple_key_allowed) && self.check('\t')) {
                self.skip();
                self.cache(1)?;
            }
            if self.check('#') {
                while !self.is_breakz() {
                    self.skip();
                    self.cache(1)?;
                }
            }
            if self.is_break() {
                self.cache(2)?;
                self.skip_line();
                if self.flow_level == 0 {
                    self.simple_key_allowed = true;
                }
            } else {
                break;
            }
        }
        Ok(())
    }

    fn eat_line_end(&mut self, context: &str, start: CMark) -> CResult<()> {
        self.cache(1)?;
        while self.is_blank() {
            self.skip();
            self.cache(1)?;
        }
        if self.check('#') {
            while !self.is_breakz() {
                self.skip();
                self.cache(1)?;
            }
        }
        if !self.is_breakz() {
            return Err(self.error(Some(context), start, "did not find expected comment or line break"));
        }
        if self.is_break() {
            self.cache(2)?;
            self.skip_line();
        }
        Ok(())
    }

    fn scan_directive(&mut self) -> CResult<CToken> {
        let start = self.mark;
        self.skip();
        let name = self.scan_directive_name(start)?;
        let kind = if name == "YAML" {
            let (major, minor) = self.scan_version_directive_value(start)?;
            CTok::VersionDirective { major, minor }
        } else if name == "TAG" {
            let (handle, prefix) = self.scan_tag_directive_value(start)?;
            CTok::TagDirective { handle, prefix }
        } else {
            return Err(self.error(
                Some("while scanning a directive"),
                start,
                "found unknown directive name",
            ));
        };
        let end = self.mark;
        self.eat_line_end("while scanning a directive", start)?;
        Ok(CToken { kind, start, end })
    }

    fn scan_directive_name(&mut self, start: CMark) -> CResult<String> {
        let mut string = String::new();
        self.cache(1)?;
        while self.is_alpha_at(0) {
            self.read(&mut string);
            self.cache(1)?;
        }
        if string.is_empty() {
            return Err(self.error(
                Some("while scanning a directive"),
                start,
                "could not find expected directive name",
            ));
        }
        if !self.is_blankz() {
            return Err(self.error(
                Some("while scanning a directive"),
                start,
                "found unexpected non-alphabetical character",
            ));
        }
        Ok(string)
    }

    fn scan_version_directive_value(&mut self, start: CMark) -> CResult<(i64, i64)> {
        self.cache(1)?;
        while self.is_blank() {
            self.skip();
            self.cache(1)?;
        }
        let major = self.scan_version_directive_number(start)?;
        if !self.check('.') {
            return Err(self.error(
                Some("while scanning a %YAML directive"),
                start,
                "did not find expected digit or '.' character",
            ));
        }
        self.skip();
        let minor = self.scan_version_directive_number(start)?;
        Ok((major, minor))
    }

    fn scan_version_directive_number(&mut self, start: CMark) -> CResult<i64> {
        let mut value: i64 = 0;
        let mut length = 0;
        self.cache(1)?;
        while self.is_digit() {
            length += 1;
            if length > 9 {
                return Err(self.error(
                    Some("while scanning a %YAML directive"),
                    start,
                    "found extremely long version number",
                ));
            }
            value = value * 10 + i64::from(self.at(0).to_digit(10).unwrap_or(0));
            self.skip();
            self.cache(1)?;
        }
        if length == 0 {
            return Err(self.error(
                Some("while scanning a %YAML directive"),
                start,
                "did not find expected version number",
            ));
        }
        Ok(value)
    }

    fn scan_tag_directive_value(&mut self, start: CMark) -> CResult<(Vec<u8>, Vec<u8>)> {
        self.cache(1)?;
        while self.is_blank() {
            self.skip();
            self.cache(1)?;
        }
        let handle = self.scan_tag_handle(true, start)?;
        self.cache(1)?;
        if !self.is_blank() {
            return Err(self.error(
                Some("while scanning a %TAG directive"),
                start,
                "did not find expected whitespace",
            ));
        }
        while self.is_blank() {
            self.skip();
            self.cache(1)?;
        }
        let prefix = self.scan_tag_uri(true, true, None, start)?;
        self.cache(1)?;
        if !self.is_blankz() {
            return Err(self.error(
                Some("while scanning a %TAG directive"),
                start,
                "did not find expected whitespace or line break",
            ));
        }
        Ok((handle, prefix))
    }

    fn scan_anchor(&mut self, anchor: bool) -> CResult<CToken> {
        let mut string = Vec::new();
        let start = self.mark;
        self.skip();
        self.cache(1)?;
        let mut length = 0;
        while self.is_alpha_at(0) {
            self.read_bytes(&mut string);
            self.cache(1)?;
            length += 1;
        }
        let end = self.mark;
        if length == 0 || !(self.is_blankz() || "?:,]}%@`".contains(self.at(0))) {
            let context = if anchor { "while scanning an anchor" } else { "while scanning an alias" };
            return Err(self.error(
                Some(context),
                start,
                "did not find expected alphabetic or numeric character",
            ));
        }
        let kind = if anchor { CTok::Anchor(string) } else { CTok::Alias(string) };
        Ok(CToken { kind, start, end })
    }

    fn scan_tag(&mut self) -> CResult<CToken> {
        let start = self.mark;
        self.cache(2)?;
        let (handle, suffix);
        if self.check_at('<', 1) {
            handle = Vec::new();
            self.skip();
            self.skip();
            suffix = self.scan_tag_uri(true, false, None, start)?;
            if !self.check('>') {
                return Err(self.error(Some("while scanning a tag"), start, "did not find the expected '>'"));
            }
            self.skip();
        } else {
            let scanned = self.scan_tag_handle(false, start)?;
            if scanned.first() == Some(&b'!') && scanned.len() > 1 && scanned.last() == Some(&b'!') {
                handle = scanned;
                suffix = self.scan_tag_uri(false, false, None, start)?;
            } else {
                let rest = self.scan_tag_uri(false, false, Some(&scanned), start)?;
                if rest.is_empty() {
                    // The '!' tag: the handle is '' and the suffix '!'.
                    handle = Vec::new();
                    suffix = b"!".to_vec();
                } else {
                    handle = b"!".to_vec();
                    suffix = rest;
                }
            }
        }
        self.cache(1)?;
        if !self.is_blankz() && (self.flow_level == 0 || !self.check(',')) {
            return Err(self.error(
                Some("while scanning a tag"),
                start,
                "did not find expected whitespace or line break",
            ));
        }
        let end = self.mark;
        Ok(CToken { kind: CTok::Tag { handle, suffix }, start, end })
    }

    fn scan_tag_handle(&mut self, directive: bool, start: CMark) -> CResult<Vec<u8>> {
        let mut string = Vec::new();
        self.cache(1)?;
        if !self.check('!') {
            let context = if directive { "while scanning a tag directive" } else { "while scanning a tag" };
            return Err(self.error(Some(context), start, "did not find expected '!'"));
        }
        self.read_bytes(&mut string);
        self.cache(1)?;
        while self.is_alpha_at(0) {
            self.read_bytes(&mut string);
            self.cache(1)?;
        }
        if self.check('!') {
            self.read_bytes(&mut string);
        } else if directive && string != b"!" {
            return Err(self.error(
                Some("while parsing a tag directive"),
                start,
                "did not find expected '!'",
            ));
        }
        Ok(string)
    }

    fn scan_tag_uri(
        &mut self,
        uri_char: bool,
        directive: bool,
        head: Option<&[u8]>,
        start: CMark,
    ) -> CResult<Vec<u8>> {
        let head = head.unwrap_or(&[]);
        let mut length = head.len();
        let mut string: Vec<u8> = if head.len() > 1 { head[1..].to_vec() } else { Vec::new() };
        self.cache(1)?;
        loop {
            let ch = self.at(0);
            let allowed =
                self.is_alpha_at(0) || ";/?:@&=+$.%!~*'()".contains(ch) || (uri_char && ",[]".contains(ch));
            if !allowed || ch == '\0' {
                break;
            }
            if ch == '%' {
                self.scan_uri_escapes(directive, start, &mut string)?;
            } else {
                self.read_bytes(&mut string);
            }
            length += 1;
            self.cache(1)?;
        }
        if length == 0 {
            let context = if directive { "while parsing a %TAG directive" } else { "while parsing a tag" };
            return Err(self.error(Some(context), start, "did not find expected tag URI"));
        }
        Ok(c_string(string))
    }

    fn scan_uri_escapes(&mut self, directive: bool, start: CMark, string: &mut Vec<u8>) -> CResult<()> {
        let context = if directive { "while parsing a %TAG directive" } else { "while parsing a tag" };
        let mut width = 0u32;
        loop {
            self.cache(3)?;
            if !(self.check('%') && self.is_hex_at(1) && self.is_hex_at(2)) {
                return Err(self.error(Some(context), start, "did not find URI escaped octet"));
            }
            let octet = ((self.as_hex_at(1) << 4) + self.as_hex_at(2)) as u8;
            if width == 0 {
                width = if octet & 0x80 == 0 {
                    1
                } else if octet & 0xE0 == 0xC0 {
                    2
                } else if octet & 0xF0 == 0xE0 {
                    3
                } else if octet & 0xF8 == 0xF0 {
                    4
                } else {
                    0
                };
                if width == 0 {
                    return Err(self.error(Some(context), start, "found an incorrect leading UTF-8 octet"));
                }
            } else if octet & 0xC0 != 0x80 {
                return Err(self.error(Some(context), start, "found an incorrect trailing UTF-8 octet"));
            }
            string.push(octet);
            self.skip();
            self.skip();
            self.skip();
            width -= 1;
            if width == 0 {
                break;
            }
        }
        Ok(())
    }

    fn scan_block_scalar(&mut self, literal: bool) -> CResult<CToken> {
        let mut string = String::new();
        let mut leading_break = String::new();
        let mut trailing_breaks = String::new();
        let mut chomping = 0i32;
        let mut increment = 0isize;
        let mut indent = 0isize;
        let mut leading_blank = false;
        let start = self.mark;
        self.skip();
        self.cache(1)?;
        let zero_indicator = |scanner: &Self| {
            scanner.error(
                Some("while scanning a block scalar"),
                start,
                "found an indentation indicator equal to 0",
            )
        };
        if self.check('+') || self.check('-') {
            chomping = if self.check('+') { 1 } else { -1 };
            self.skip();
            self.cache(1)?;
            if self.is_digit() {
                if self.check('0') {
                    return Err(zero_indicator(self));
                }
                increment = self.at(0).to_digit(10).unwrap_or(0) as isize;
                self.skip();
            }
        } else if self.is_digit() {
            if self.check('0') {
                return Err(zero_indicator(self));
            }
            increment = self.at(0).to_digit(10).unwrap_or(0) as isize;
            self.skip();
            self.cache(1)?;
            if self.check('+') || self.check('-') {
                chomping = if self.check('+') { 1 } else { -1 };
                self.skip();
            }
        }
        self.cache(1)?;
        while self.is_blank() {
            self.skip();
            self.cache(1)?;
        }
        if self.check('#') {
            while !self.is_breakz() {
                self.skip();
                self.cache(1)?;
            }
        }
        if !self.is_breakz() {
            return Err(self.error(
                Some("while scanning a block scalar"),
                start,
                "did not find expected comment or line break",
            ));
        }
        if self.is_break() {
            self.cache(2)?;
            self.skip_line();
        }
        let mut end = self.mark;
        if increment != 0 {
            indent = if self.indent >= 0 { self.indent + increment } else { increment };
        }
        self.scan_block_scalar_breaks(&mut indent, &mut trailing_breaks, start, &mut end)?;
        self.cache(1)?;
        while self.mark.column as isize == indent && !self.is_z_at(0) {
            let trailing_blank = self.is_blank();
            if !literal && leading_break.starts_with('\n') && !leading_blank && !trailing_blank {
                if trailing_breaks.is_empty() {
                    string.push(' ');
                }
                leading_break.clear();
            } else {
                string.push_str(&leading_break);
                leading_break.clear();
            }
            string.push_str(&trailing_breaks);
            trailing_breaks.clear();
            leading_blank = self.is_blank();
            while !self.is_breakz() {
                self.read(&mut string);
                self.cache(1)?;
            }
            self.cache(2)?;
            self.read_line(&mut leading_break);
            self.scan_block_scalar_breaks(&mut indent, &mut trailing_breaks, start, &mut end)?;
        }
        if chomping != -1 {
            string.push_str(&leading_break);
        }
        if chomping == 1 {
            string.push_str(&trailing_breaks);
        }
        let style = if literal { '|' } else { '>' };
        Ok(CToken { kind: CTok::Scalar { value: string, style: Some(style) }, start, end })
    }

    fn scan_block_scalar_breaks(
        &mut self,
        indent: &mut isize,
        breaks: &mut String,
        start: CMark,
        end: &mut CMark,
    ) -> CResult<()> {
        let mut max_indent = 0isize;
        *end = self.mark;
        loop {
            self.cache(1)?;
            while (*indent == 0 || (self.mark.column as isize) < *indent) && self.check(' ') {
                self.skip();
                self.cache(1)?;
            }
            if self.mark.column as isize > max_indent {
                max_indent = self.mark.column as isize;
            }
            if (*indent == 0 || (self.mark.column as isize) < *indent) && self.check('\t') {
                return Err(self.error(
                    Some("while scanning a block scalar"),
                    start,
                    "found a tab character where an indentation space is expected",
                ));
            }
            if !self.is_break() {
                break;
            }
            self.cache(2)?;
            self.read_line(breaks);
            *end = self.mark;
        }
        if *indent == 0 {
            *indent = max_indent;
            if *indent < self.indent + 1 {
                *indent = self.indent + 1;
            }
            if *indent < 1 {
                *indent = 1;
            }
        }
        Ok(())
    }

    fn at_document_indicator(&self) -> bool {
        self.mark.column == 0
            && ((self.check_at('-', 0) && self.check_at('-', 1) && self.check_at('-', 2))
                || (self.check_at('.', 0) && self.check_at('.', 1) && self.check_at('.', 2)))
            && self.is_blankz_at(3)
    }

    fn join_breaks(string: &mut String, leading_break: &mut String, trailing_breaks: &mut String) {
        if leading_break.starts_with('\n') {
            if trailing_breaks.is_empty() {
                string.push(' ');
            } else {
                string.push_str(trailing_breaks);
                trailing_breaks.clear();
            }
            leading_break.clear();
        } else {
            string.push_str(leading_break);
            string.push_str(trailing_breaks);
            leading_break.clear();
            trailing_breaks.clear();
        }
    }

    fn scan_flow_scalar(&mut self, single: bool) -> CResult<CToken> {
        let mut string = String::new();
        let mut leading_break = String::new();
        let mut trailing_breaks = String::new();
        let mut whitespaces = String::new();
        let start = self.mark;
        let quote = if single { '\'' } else { '"' };
        self.skip();
        loop {
            self.cache(4)?;
            if self.at_document_indicator() {
                return Err(self.error(
                    Some("while scanning a quoted scalar"),
                    start,
                    "found unexpected document indicator",
                ));
            }
            if self.is_z_at(0) {
                return Err(self.error(
                    Some("while scanning a quoted scalar"),
                    start,
                    "found unexpected end of stream",
                ));
            }
            self.cache(2)?;
            let mut leading_blanks = false;
            while !self.is_blankz() {
                if single && self.check_at('\'', 0) && self.check_at('\'', 1) {
                    string.push('\'');
                    self.skip();
                    self.skip();
                } else if self.check(quote) {
                    break;
                } else if !single && self.check('\\') && self.is_break_at(1) {
                    self.cache(3)?;
                    self.skip();
                    self.skip_line();
                    leading_blanks = true;
                    break;
                } else if !single && self.check('\\') {
                    let mut code_length = 0usize;
                    let simple = match self.at(1) {
                        '0' => Some('\0'),
                        'a' => Some('\x07'),
                        'b' => Some('\x08'),
                        't' | '\t' => Some('\x09'),
                        'n' => Some('\x0A'),
                        'v' => Some('\x0B'),
                        'f' => Some('\x0C'),
                        'r' => Some('\x0D'),
                        'e' => Some('\x1B'),
                        ' ' => Some(' '),
                        '"' => Some('"'),
                        '/' => Some('/'),
                        '\\' => Some('\\'),
                        'N' => Some('\u{85}'),
                        '_' => Some('\u{A0}'),
                        'L' => Some('\u{2028}'),
                        'P' => Some('\u{2029}'),
                        'x' => {
                            code_length = 2;
                            None
                        }
                        'u' => {
                            code_length = 4;
                            None
                        }
                        'U' => {
                            code_length = 8;
                            None
                        }
                        _ => {
                            return Err(self.error(
                                Some("while parsing a quoted scalar"),
                                start,
                                "found unknown escape character",
                            ));
                        }
                    };
                    if let Some(ch) = simple {
                        string.push(ch);
                    }
                    self.skip();
                    self.skip();
                    if code_length > 0 {
                        let mut value: u32 = 0;
                        self.cache(code_length)?;
                        for k in 0..code_length {
                            if !self.is_hex_at(k) {
                                return Err(self.error(
                                    Some("while parsing a quoted scalar"),
                                    start,
                                    "did not find expected hexdecimal number",
                                ));
                            }
                            value = (value << 4) + self.as_hex_at(k);
                        }
                        let Some(ch) = char::from_u32(value) else {
                            return Err(self.error(
                                Some("while parsing a quoted scalar"),
                                start,
                                "found invalid Unicode character escape code",
                            ));
                        };
                        string.push(ch);
                        for _ in 0..code_length {
                            self.skip();
                        }
                    }
                } else {
                    self.read(&mut string);
                }
                self.cache(2)?;
            }
            self.cache(1)?;
            if self.check(quote) {
                break;
            }
            self.cache(1)?;
            while self.is_blank() || self.is_break() {
                if self.is_blank() {
                    if !leading_blanks {
                        self.read(&mut whitespaces);
                    } else {
                        self.skip();
                    }
                } else {
                    self.cache(2)?;
                    if !leading_blanks {
                        whitespaces.clear();
                        self.read_line(&mut leading_break);
                        leading_blanks = true;
                    } else {
                        self.read_line(&mut trailing_breaks);
                    }
                }
                self.cache(1)?;
            }
            if leading_blanks {
                Self::join_breaks(&mut string, &mut leading_break, &mut trailing_breaks);
            } else {
                string.push_str(&whitespaces);
                whitespaces.clear();
            }
        }
        self.skip();
        let end = self.mark;
        Ok(CToken { kind: CTok::Scalar { value: string, style: Some(quote) }, start, end })
    }

    fn scan_plain_scalar(&mut self) -> CResult<CToken> {
        let mut string = String::new();
        let mut leading_break = String::new();
        let mut trailing_breaks = String::new();
        let mut whitespaces = String::new();
        let mut leading_blanks = false;
        let indent = self.indent + 1;
        let start = self.mark;
        let mut end = self.mark;
        loop {
            self.cache(4)?;
            if self.at_document_indicator() {
                break;
            }
            if self.check('#') {
                break;
            }
            while !self.is_blankz() {
                if self.flow_level > 0
                    && self.check(':')
                    && ",?[]{}".contains(self.at(1))
                    && self.at(1) != '\0'
                {
                    return Err(self.error(
                        Some("while scanning a plain scalar"),
                        start,
                        "found unexpected ':'",
                    ));
                }
                if (self.check(':') && self.is_blankz_at(1))
                    || (self.flow_level > 0 && ",[]{}".contains(self.at(0)))
                {
                    break;
                }
                if leading_blanks || !whitespaces.is_empty() {
                    if leading_blanks {
                        Self::join_breaks(&mut string, &mut leading_break, &mut trailing_breaks);
                        leading_blanks = false;
                    } else {
                        string.push_str(&whitespaces);
                        whitespaces.clear();
                    }
                }
                self.read(&mut string);
                end = self.mark;
                self.cache(2)?;
            }
            if !(self.is_blank() || self.is_break()) {
                break;
            }
            self.cache(1)?;
            while self.is_blank() || self.is_break() {
                if self.is_blank() {
                    if leading_blanks && (self.mark.column as isize) < indent && self.check('\t') {
                        return Err(self.error(
                            Some("while scanning a plain scalar"),
                            start,
                            "found a tab character that violates indentation",
                        ));
                    }
                    if !leading_blanks {
                        self.read(&mut whitespaces);
                    } else {
                        self.skip();
                    }
                } else {
                    self.cache(2)?;
                    if !leading_blanks {
                        whitespaces.clear();
                        self.read_line(&mut leading_break);
                        leading_blanks = true;
                    } else {
                        self.read_line(&mut trailing_breaks);
                    }
                }
                self.cache(1)?;
            }
            if self.flow_level == 0 && (self.mark.column as isize) < indent {
                break;
            }
        }
        if leading_blanks {
            self.simple_key_allowed = true;
        }
        Ok(CToken { kind: CTok::Scalar { value: string, style: None }, start, end })
    }
}
