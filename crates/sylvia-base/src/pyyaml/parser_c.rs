//! libyaml 0.2.5's parser (`src/parser.c`), and `_yaml.pyx`'s conversion of its events to
//! Python objects, for [`Loader::C`].
//!
//! The grammar is PyYAML's, but the states differ in detail: an indentless sequence is block
//! style, `%YAML` accepts only 1.1 and 1.2, a collection tagged `!` is not implicit, and every
//! message has libyaml's wording. Anchors and tags are C strings that `_yaml.pyx` decodes as
//! strict UTF-8 when it builds each event, so a tag whose URI escapes are not valid UTF-8
//! raises `UnicodeDecodeError` there.
//!
//! [`Loader::C`]: super::Loader::C

use super::error::{MarkedError, MarkedKind, YamlError};
use super::parser::{Event, EventKind};
use super::scanner::utf8_error_text;
use super::scanner_c::{CMark, CScanner, CTok, CToken};

type CResult<T> = Result<T, YamlError>;

/// libyaml's states, less the two it declares but never enters
/// (`BLOCK_NODE_OR_INDENTLESS_SEQUENCE`, `FLOW_NODE`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum State {
    StreamStart,
    ImplicitDocumentStart,
    DocumentStart,
    DocumentContent,
    DocumentEnd,
    BlockNode,
    BlockSequenceFirstEntry,
    BlockSequenceEntry,
    IndentlessSequenceEntry,
    BlockMappingFirstKey,
    BlockMappingKey,
    BlockMappingValue,
    FlowSequenceFirstEntry,
    FlowSequenceEntry,
    FlowSequenceEntryMappingKey,
    FlowSequenceEntryMappingValue,
    FlowSequenceEntryMappingEnd,
    FlowMappingFirstKey,
    FlowMappingKey,
    FlowMappingValue,
    FlowMappingEmptyValue,
    End,
}

/// A libyaml event before `_yaml.pyx` converts it: anchors and tags still bytes.
enum CEventKind {
    StreamStart,
    StreamEnd,
    DocumentStart {
        implicit: bool,
        version: Option<(i64, i64)>,
        tags: Vec<(Vec<u8>, Vec<u8>)>,
    },
    DocumentEnd {
        implicit: bool,
    },
    Alias(Vec<u8>),
    Scalar {
        anchor: Option<Vec<u8>>,
        tag: Option<Vec<u8>>,
        value: String,
        plain_implicit: bool,
        quoted_implicit: bool,
        style: Option<char>,
    },
    SequenceStart {
        anchor: Option<Vec<u8>>,
        tag: Option<Vec<u8>>,
        implicit: bool,
        flow: bool,
    },
    SequenceEnd,
    MappingStart {
        anchor: Option<Vec<u8>>,
        tag: Option<Vec<u8>>,
        implicit: bool,
        flow: bool,
    },
    MappingEnd,
}

/// `PyUnicode_FromYamlString`: strict UTF-8.
fn decode(bytes: &[u8]) -> CResult<String> {
    String::from_utf8(bytes.to_vec())
        .map_err(|error| YamlError::python("UnicodeDecodeError", utf8_error_text(bytes, &error)))
}

fn decode_opt(bytes: &Option<Vec<u8>>) -> CResult<Option<String>> {
    bytes.as_deref().map(decode).transpose()
}

pub(crate) struct CParser {
    scanner: CScanner,
    state: State,
    states: Vec<State>,
    marks: Vec<CMark>,
    /// `parser->tag_directives`: the current document's handles, then the defaults.
    tag_directives: Vec<(Vec<u8>, Vec<u8>)>,
    current: Option<Event>,
    /// Whether to decode a document's `%TAG` handles and prefixes: `yaml.parse` does
    /// (`_event_to_object`); the composer never looks at them.
    decode_document_tags: bool,
    failed: Option<YamlError>,
}

impl CParser {
    pub(crate) fn new(scanner: CScanner, decode_document_tags: bool) -> Self {
        Self {
            scanner,
            state: State::StreamStart,
            states: Vec::new(),
            marks: Vec::new(),
            tag_directives: Vec::new(),
            current: None,
            decode_document_tags,
            failed: None,
        }
    }

    /// `peek_event()`.
    pub(crate) fn peek_event(&mut self) -> CResult<Option<&Event>> {
        self.fill()?;
        Ok(self.current.as_ref())
    }

    /// `get_event()`.
    pub(crate) fn get_event(&mut self) -> CResult<Option<Event>> {
        self.fill()?;
        Ok(self.current.take())
    }

    fn fill(&mut self) -> CResult<()> {
        if let Some(error) = &self.failed {
            return Err(error.clone());
        }
        if self.current.is_none() {
            if self.scanner.stream_end_produced || self.state == State::End {
                return Ok(());
            }
            let result = self.state_machine().and_then(|(kind, start, end)| self.convert(kind, start, end));
            match result {
                Ok(event) => self.current = Some(event),
                Err(error) => {
                    self.failed = Some(error.clone());
                    return Err(error);
                }
            }
        }
        Ok(())
    }

    /// `_event_to_object`.
    fn convert(&self, kind: CEventKind, start: CMark, end: CMark) -> CResult<Event> {
        let kind = match kind {
            CEventKind::StreamStart => EventKind::StreamStart,
            CEventKind::StreamEnd => EventKind::StreamEnd,
            CEventKind::DocumentStart { implicit, version, tags } => {
                let tags = if tags.is_empty() {
                    None
                } else {
                    let mut out: Vec<(String, String)> = Vec::new();
                    for (handle, prefix) in &tags {
                        let (handle, prefix) = if self.decode_document_tags {
                            (decode(handle)?, decode(prefix)?)
                        } else {
                            (
                                String::from_utf8_lossy(handle).into_owned(),
                                String::from_utf8_lossy(prefix).into_owned(),
                            )
                        };
                        // A dict: a repeated handle keeps its first place and takes the last prefix.
                        match out.iter_mut().find(|(h, _)| *h == handle) {
                            Some(slot) => slot.1 = prefix,
                            None => out.push((handle, prefix)),
                        }
                    }
                    Some(out)
                };
                EventKind::DocumentStart {
                    explicit: !implicit,
                    version: version.map(|(major, minor)| (major.to_string(), minor.to_string())),
                    tags,
                }
            }
            CEventKind::DocumentEnd { implicit } => EventKind::DocumentEnd { explicit: !implicit },
            CEventKind::Alias(anchor) => EventKind::Alias { anchor: decode(&anchor)? },
            CEventKind::Scalar { anchor, tag, value, plain_implicit, quoted_implicit, style } => {
                EventKind::Scalar {
                    anchor: decode_opt(&anchor)?,
                    tag: decode_opt(&tag)?,
                    implicit: (plain_implicit, quoted_implicit),
                    value,
                    style,
                }
            }
            CEventKind::SequenceStart { anchor, tag, implicit, flow } => EventKind::SequenceStart {
                anchor: decode_opt(&anchor)?,
                tag: decode_opt(&tag)?,
                implicit,
                flow_style: Some(flow),
            },
            CEventKind::SequenceEnd => EventKind::SequenceEnd,
            CEventKind::MappingStart { anchor, tag, implicit, flow } => EventKind::MappingStart {
                anchor: decode_opt(&anchor)?,
                tag: decode_opt(&tag)?,
                implicit,
                flow_style: Some(flow),
            },
            CEventKind::MappingEnd => EventKind::MappingEnd,
        };
        Ok(Event { kind, start_mark: self.scanner.py_mark(start), end_mark: self.scanner.py_mark(end) })
    }

    fn peek(&mut self) -> CResult<CToken> {
        Ok(self.scanner.peek_token()?.clone())
    }

    fn skip(&mut self) {
        self.scanner.skip_token();
    }

    fn pop_state(&mut self) -> State {
        self.states.pop().expect("a state for each pushed level")
    }

    fn pop_mark(&mut self) -> CMark {
        self.marks.pop().expect("a mark for each collection")
    }

    fn error(&self, context: Option<(&str, CMark)>, problem: &str, problem_mark: CMark) -> YamlError {
        YamlError::Marked(MarkedError::new(
            MarkedKind::Parser,
            context.map(|(text, _)| text.to_owned()),
            context.map(|(_, mark)| self.scanner.py_mark(mark)),
            problem,
            Some(self.scanner.py_mark(problem_mark)),
        ))
    }

    fn state_machine(&mut self) -> CResult<(CEventKind, CMark, CMark)> {
        match self.state {
            State::StreamStart => self.parse_stream_start(),
            State::ImplicitDocumentStart => self.parse_document_start(true),
            State::DocumentStart => self.parse_document_start(false),
            State::DocumentContent => self.parse_document_content(),
            State::DocumentEnd => self.parse_document_end(),
            State::BlockNode => self.parse_node(true, false),
            State::BlockSequenceFirstEntry => self.parse_block_sequence_entry(true),
            State::BlockSequenceEntry => self.parse_block_sequence_entry(false),
            State::IndentlessSequenceEntry => self.parse_indentless_sequence_entry(),
            State::BlockMappingFirstKey => self.parse_block_mapping_key(true),
            State::BlockMappingKey => self.parse_block_mapping_key(false),
            State::BlockMappingValue => self.parse_block_mapping_value(),
            State::FlowSequenceFirstEntry => self.parse_flow_sequence_entry(true),
            State::FlowSequenceEntry => self.parse_flow_sequence_entry(false),
            State::FlowSequenceEntryMappingKey => self.parse_flow_sequence_entry_mapping_key(),
            State::FlowSequenceEntryMappingValue => self.parse_flow_sequence_entry_mapping_value(),
            State::FlowSequenceEntryMappingEnd => self.parse_flow_sequence_entry_mapping_end(),
            State::FlowMappingFirstKey => self.parse_flow_mapping_key(true),
            State::FlowMappingKey => self.parse_flow_mapping_key(false),
            State::FlowMappingValue => self.parse_flow_mapping_value(false),
            State::FlowMappingEmptyValue => self.parse_flow_mapping_value(true),
            State::End => unreachable!("fill stops at the end state"),
        }
    }

    fn parse_stream_start(&mut self) -> CResult<(CEventKind, CMark, CMark)> {
        let token = self.peek()?;
        if token.kind != CTok::StreamStart {
            return Err(self.error(None, "did not find expected <stream-start>", token.start));
        }
        self.state = State::ImplicitDocumentStart;
        self.skip();
        Ok((CEventKind::StreamStart, token.start, token.start))
    }

    fn parse_document_start(&mut self, implicit: bool) -> CResult<(CEventKind, CMark, CMark)> {
        let mut token = self.peek()?;
        if !implicit {
            while token.kind == CTok::DocumentEnd {
                self.skip();
                token = self.peek()?;
            }
        }
        let directive_or_end = matches!(
            token.kind,
            CTok::VersionDirective { .. } | CTok::TagDirective { .. } | CTok::DocumentStart | CTok::StreamEnd
        );
        if implicit && !directive_or_end {
            self.process_directives()?;
            self.states.push(State::DocumentEnd);
            self.state = State::BlockNode;
            return Ok((
                CEventKind::DocumentStart { implicit: true, version: None, tags: Vec::new() },
                token.start,
                token.start,
            ));
        }
        if token.kind != CTok::StreamEnd {
            let start = token.start;
            let (version, tags) = self.process_directives()?;
            let token = self.peek()?;
            if token.kind != CTok::DocumentStart {
                return Err(self.error(None, "did not find expected <document start>", token.start));
            }
            self.states.push(State::DocumentEnd);
            self.state = State::DocumentContent;
            let end = token.end;
            self.skip();
            return Ok((CEventKind::DocumentStart { implicit: false, version, tags }, start, end));
        }
        self.state = State::End;
        self.skip();
        Ok((CEventKind::StreamEnd, token.start, token.end))
    }

    fn parse_document_content(&mut self) -> CResult<(CEventKind, CMark, CMark)> {
        let token = self.peek()?;
        if matches!(
            token.kind,
            CTok::VersionDirective { .. }
                | CTok::TagDirective { .. }
                | CTok::DocumentStart
                | CTok::DocumentEnd
                | CTok::StreamEnd
        ) {
            self.state = self.pop_state();
            return Ok(Self::empty_scalar(token.start));
        }
        self.parse_node(true, false)
    }

    fn parse_document_end(&mut self) -> CResult<(CEventKind, CMark, CMark)> {
        let token = self.peek()?;
        let start = token.start;
        let mut end = token.start;
        let mut implicit = true;
        if token.kind == CTok::DocumentEnd {
            end = token.end;
            self.skip();
            implicit = false;
        }
        self.tag_directives.clear();
        self.state = State::DocumentStart;
        Ok((CEventKind::DocumentEnd { implicit }, start, end))
    }

    fn parse_node(&mut self, block: bool, indentless_sequence: bool) -> CResult<(CEventKind, CMark, CMark)> {
        let mut token = self.peek()?;
        if let CTok::Alias(anchor) = &token.kind {
            self.state = self.pop_state();
            self.skip();
            return Ok((CEventKind::Alias(anchor.clone()), token.start, token.end));
        }
        let mut anchor: Option<Vec<u8>> = None;
        let mut tag_parts: Option<(Vec<u8>, Vec<u8>)> = None;
        let mut start = token.start;
        let mut end = token.start;
        let mut tag_mark = token.start;
        if let CTok::Anchor(value) = &token.kind {
            anchor = Some(value.clone());
            start = token.start;
            end = token.end;
            self.skip();
            token = self.peek()?;
            if let CTok::Tag { handle, suffix } = &token.kind {
                tag_parts = Some((handle.clone(), suffix.clone()));
                tag_mark = token.start;
                end = token.end;
                self.skip();
                token = self.peek()?;
            }
        } else if let CTok::Tag { handle, suffix } = &token.kind {
            tag_parts = Some((handle.clone(), suffix.clone()));
            start = token.start;
            tag_mark = token.start;
            end = token.end;
            self.skip();
            token = self.peek()?;
            if let CTok::Anchor(value) = &token.kind {
                anchor = Some(value.clone());
                end = token.end;
                self.skip();
                token = self.peek()?;
            }
        }
        let mut tag: Option<Vec<u8>> = None;
        if let Some((handle, suffix)) = tag_parts {
            if handle.is_empty() {
                tag = Some(suffix);
            } else {
                match self.tag_directives.iter().find(|(h, _)| *h == handle) {
                    Some((_, prefix)) => {
                        let mut full = prefix.clone();
                        full.extend_from_slice(&suffix);
                        tag = Some(full);
                    }
                    None => {
                        return Err(self.error(
                            Some(("while parsing a node", start)),
                            "found undefined tag handle",
                            tag_mark,
                        ));
                    }
                }
            }
        }
        let implicit = tag.as_ref().is_none_or(Vec::is_empty);
        if indentless_sequence && token.kind == CTok::BlockEntry {
            end = token.end;
            self.state = State::IndentlessSequenceEntry;
            return Ok((CEventKind::SequenceStart { anchor, tag, implicit, flow: false }, start, end));
        }
        match &token.kind {
            CTok::Scalar { value, style } => {
                end = token.end;
                let (mut plain_implicit, mut quoted_implicit) = (false, false);
                if (style.is_none() && tag.is_none()) || tag.as_deref() == Some(b"!") {
                    plain_implicit = true;
                } else if tag.is_none() {
                    quoted_implicit = true;
                }
                self.state = self.pop_state();
                let event = CEventKind::Scalar {
                    anchor,
                    tag,
                    value: value.clone(),
                    plain_implicit,
                    quoted_implicit,
                    style: *style,
                };
                self.skip();
                Ok((event, start, end))
            }
            CTok::FlowSequenceStart => {
                end = token.end;
                self.state = State::FlowSequenceFirstEntry;
                Ok((CEventKind::SequenceStart { anchor, tag, implicit, flow: true }, start, end))
            }
            CTok::FlowMappingStart => {
                end = token.end;
                self.state = State::FlowMappingFirstKey;
                Ok((CEventKind::MappingStart { anchor, tag, implicit, flow: true }, start, end))
            }
            CTok::BlockSequenceStart if block => {
                end = token.end;
                self.state = State::BlockSequenceFirstEntry;
                Ok((CEventKind::SequenceStart { anchor, tag, implicit, flow: false }, start, end))
            }
            CTok::BlockMappingStart if block => {
                end = token.end;
                self.state = State::BlockMappingFirstKey;
                Ok((CEventKind::MappingStart { anchor, tag, implicit, flow: false }, start, end))
            }
            _ if anchor.is_some() || tag.is_some() => {
                self.state = self.pop_state();
                let event = CEventKind::Scalar {
                    anchor,
                    tag,
                    value: String::new(),
                    plain_implicit: implicit,
                    quoted_implicit: false,
                    style: None,
                };
                Ok((event, start, end))
            }
            _ => {
                let context = if block { "while parsing a block node" } else { "while parsing a flow node" };
                Err(self.error(Some((context, start)), "did not find expected node content", token.start))
            }
        }
    }

    fn parse_block_sequence_entry(&mut self, first: bool) -> CResult<(CEventKind, CMark, CMark)> {
        if first {
            let token = self.peek()?;
            self.marks.push(token.start);
            self.skip();
        }
        let token = self.peek()?;
        match token.kind {
            CTok::BlockEntry => {
                let mark = token.end;
                self.skip();
                let token = self.peek()?;
                if !matches!(token.kind, CTok::BlockEntry | CTok::BlockEnd) {
                    self.states.push(State::BlockSequenceEntry);
                    self.parse_node(true, false)
                } else {
                    self.state = State::BlockSequenceEntry;
                    Ok(Self::empty_scalar(mark))
                }
            }
            CTok::BlockEnd => {
                self.state = self.pop_state();
                self.pop_mark();
                self.skip();
                Ok((CEventKind::SequenceEnd, token.start, token.end))
            }
            _ => {
                let mark = self.pop_mark();
                Err(self.error(
                    Some(("while parsing a block collection", mark)),
                    "did not find expected '-' indicator",
                    token.start,
                ))
            }
        }
    }

    fn parse_indentless_sequence_entry(&mut self) -> CResult<(CEventKind, CMark, CMark)> {
        let token = self.peek()?;
        if token.kind == CTok::BlockEntry {
            let mark = token.end;
            self.skip();
            let token = self.peek()?;
            if !matches!(token.kind, CTok::BlockEntry | CTok::Key | CTok::Value | CTok::BlockEnd) {
                self.states.push(State::IndentlessSequenceEntry);
                return self.parse_node(true, false);
            }
            self.state = State::IndentlessSequenceEntry;
            return Ok(Self::empty_scalar(mark));
        }
        self.state = self.pop_state();
        Ok((CEventKind::SequenceEnd, token.start, token.start))
    }

    fn parse_block_mapping_key(&mut self, first: bool) -> CResult<(CEventKind, CMark, CMark)> {
        if first {
            let token = self.peek()?;
            self.marks.push(token.start);
            self.skip();
        }
        let token = self.peek()?;
        match token.kind {
            CTok::Key => {
                let mark = token.end;
                self.skip();
                let token = self.peek()?;
                if !matches!(token.kind, CTok::Key | CTok::Value | CTok::BlockEnd) {
                    self.states.push(State::BlockMappingValue);
                    self.parse_node(true, true)
                } else {
                    self.state = State::BlockMappingValue;
                    Ok(Self::empty_scalar(mark))
                }
            }
            CTok::BlockEnd => {
                self.state = self.pop_state();
                self.pop_mark();
                self.skip();
                Ok((CEventKind::MappingEnd, token.start, token.end))
            }
            _ => {
                let mark = self.pop_mark();
                Err(self.error(
                    Some(("while parsing a block mapping", mark)),
                    "did not find expected key",
                    token.start,
                ))
            }
        }
    }

    fn parse_block_mapping_value(&mut self) -> CResult<(CEventKind, CMark, CMark)> {
        let token = self.peek()?;
        if token.kind == CTok::Value {
            let mark = token.end;
            self.skip();
            let token = self.peek()?;
            if !matches!(token.kind, CTok::Key | CTok::Value | CTok::BlockEnd) {
                self.states.push(State::BlockMappingKey);
                return self.parse_node(true, true);
            }
            self.state = State::BlockMappingKey;
            return Ok(Self::empty_scalar(mark));
        }
        self.state = State::BlockMappingKey;
        Ok(Self::empty_scalar(token.start))
    }

    fn parse_flow_sequence_entry(&mut self, first: bool) -> CResult<(CEventKind, CMark, CMark)> {
        if first {
            let token = self.peek()?;
            self.marks.push(token.start);
            self.skip();
        }
        let mut token = self.peek()?;
        if token.kind != CTok::FlowSequenceEnd {
            if !first {
                if token.kind == CTok::FlowEntry {
                    self.skip();
                    token = self.peek()?;
                } else {
                    let mark = self.pop_mark();
                    return Err(self.error(
                        Some(("while parsing a flow sequence", mark)),
                        "did not find expected ',' or ']'",
                        token.start,
                    ));
                }
            }
            if token.kind == CTok::Key {
                self.state = State::FlowSequenceEntryMappingKey;
                self.skip();
                return Ok((
                    CEventKind::MappingStart { anchor: None, tag: None, implicit: true, flow: true },
                    token.start,
                    token.end,
                ));
            } else if token.kind != CTok::FlowSequenceEnd {
                self.states.push(State::FlowSequenceEntry);
                return self.parse_node(false, false);
            }
        }
        self.state = self.pop_state();
        self.pop_mark();
        self.skip();
        Ok((CEventKind::SequenceEnd, token.start, token.end))
    }

    fn parse_flow_sequence_entry_mapping_key(&mut self) -> CResult<(CEventKind, CMark, CMark)> {
        let token = self.peek()?;
        if !matches!(token.kind, CTok::Value | CTok::FlowEntry | CTok::FlowSequenceEnd) {
            self.states.push(State::FlowSequenceEntryMappingValue);
            return self.parse_node(false, false);
        }
        let mark = token.end;
        self.skip();
        self.state = State::FlowSequenceEntryMappingValue;
        Ok(Self::empty_scalar(mark))
    }

    fn parse_flow_sequence_entry_mapping_value(&mut self) -> CResult<(CEventKind, CMark, CMark)> {
        let mut token = self.peek()?;
        if token.kind == CTok::Value {
            self.skip();
            token = self.peek()?;
            if !matches!(token.kind, CTok::FlowEntry | CTok::FlowSequenceEnd) {
                self.states.push(State::FlowSequenceEntryMappingEnd);
                return self.parse_node(false, false);
            }
        }
        self.state = State::FlowSequenceEntryMappingEnd;
        Ok(Self::empty_scalar(token.start))
    }

    fn parse_flow_sequence_entry_mapping_end(&mut self) -> CResult<(CEventKind, CMark, CMark)> {
        let token = self.peek()?;
        self.state = State::FlowSequenceEntry;
        Ok((CEventKind::MappingEnd, token.start, token.start))
    }

    fn parse_flow_mapping_key(&mut self, first: bool) -> CResult<(CEventKind, CMark, CMark)> {
        if first {
            let token = self.peek()?;
            self.marks.push(token.start);
            self.skip();
        }
        let mut token = self.peek()?;
        if token.kind != CTok::FlowMappingEnd {
            if !first {
                if token.kind == CTok::FlowEntry {
                    self.skip();
                    token = self.peek()?;
                } else {
                    let mark = self.pop_mark();
                    return Err(self.error(
                        Some(("while parsing a flow mapping", mark)),
                        "did not find expected ',' or '}'",
                        token.start,
                    ));
                }
            }
            if token.kind == CTok::Key {
                self.skip();
                let token = self.peek()?;
                if !matches!(token.kind, CTok::Value | CTok::FlowEntry | CTok::FlowMappingEnd) {
                    self.states.push(State::FlowMappingValue);
                    return self.parse_node(false, false);
                }
                self.state = State::FlowMappingValue;
                return Ok(Self::empty_scalar(token.start));
            } else if token.kind != CTok::FlowMappingEnd {
                self.states.push(State::FlowMappingEmptyValue);
                return self.parse_node(false, false);
            }
        }
        self.state = self.pop_state();
        self.pop_mark();
        self.skip();
        Ok((CEventKind::MappingEnd, token.start, token.end))
    }

    fn parse_flow_mapping_value(&mut self, empty: bool) -> CResult<(CEventKind, CMark, CMark)> {
        let mut token = self.peek()?;
        if empty {
            self.state = State::FlowMappingKey;
            return Ok(Self::empty_scalar(token.start));
        }
        if token.kind == CTok::Value {
            self.skip();
            token = self.peek()?;
            if !matches!(token.kind, CTok::FlowEntry | CTok::FlowMappingEnd) {
                self.states.push(State::FlowMappingKey);
                return self.parse_node(false, false);
            }
        }
        self.state = State::FlowMappingKey;
        Ok(Self::empty_scalar(token.start))
    }

    fn empty_scalar(mark: CMark) -> (CEventKind, CMark, CMark) {
        (
            CEventKind::Scalar {
                anchor: None,
                tag: None,
                value: String::new(),
                plain_implicit: true,
                quoted_implicit: false,
                style: None,
            },
            mark,
            mark,
        )
    }

    /// `yaml_parser_process_directives`: the document's `%YAML` version and `%TAG` pairs, with
    /// the default handles appended to the parser's table.
    #[allow(clippy::type_complexity)]
    fn process_directives(&mut self) -> CResult<(Option<(i64, i64)>, Vec<(Vec<u8>, Vec<u8>)>)> {
        let mut version: Option<(i64, i64)> = None;
        let mut tags = Vec::new();
        let mut token = self.peek()?;
        loop {
            match &token.kind {
                CTok::VersionDirective { major, minor } => {
                    if version.is_some() {
                        return Err(self.error(None, "found duplicate %YAML directive", token.start));
                    }
                    if *major != 1 || (*minor != 1 && *minor != 2) {
                        return Err(self.error(None, "found incompatible YAML document", token.start));
                    }
                    version = Some((*major, *minor));
                }
                CTok::TagDirective { handle, prefix } => {
                    self.append_tag_directive((handle.clone(), prefix.clone()), false, token.start)?;
                    tags.push((handle.clone(), prefix.clone()));
                }
                _ => break,
            }
            self.skip();
            token = self.peek()?;
        }
        for default in [(b"!".to_vec(), b"!".to_vec()), (b"!!".to_vec(), b"tag:yaml.org,2002:".to_vec())] {
            self.append_tag_directive(default, true, token.start)?;
        }
        Ok((version, tags))
    }

    fn append_tag_directive(
        &mut self,
        value: (Vec<u8>, Vec<u8>),
        allow_duplicates: bool,
        mark: CMark,
    ) -> CResult<()> {
        if self.tag_directives.iter().any(|(handle, _)| *handle == value.0) {
            if allow_duplicates {
                return Ok(());
            }
            return Err(self.error(None, "found duplicate %TAG directive", mark));
        }
        self.tag_directives.push(value);
        Ok(())
    }
}
