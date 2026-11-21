//! `yaml/parser.py`: tokens to events, state for state.

use super::error::{Mark, MarkedError, MarkedKind, YamlError};
use super::scanner::{DirectiveValue, Scanner, Token, TokenClass, TokenKind};
use crate::pystr::repr;

/// One parse event (`yaml.events`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EventKind {
    StreamStart,
    StreamEnd,
    DocumentStart {
        explicit: bool,
        /// `%YAML` major and minor, as written.
        version: Option<(String, String)>,
        /// `%TAG` handles and prefixes, in directive order.
        tags: Option<Vec<(String, String)>>,
    },
    DocumentEnd {
        explicit: bool,
    },
    Alias {
        anchor: String,
    },
    Scalar {
        anchor: Option<String>,
        tag: Option<String>,
        /// `(plain_implicit, quoted_implicit)`.
        implicit: (bool, bool),
        value: String,
        /// `None` for plain (and empty) scalars, else the quote or block indicator.
        style: Option<char>,
    },
    SequenceStart {
        anchor: Option<String>,
        tag: Option<String>,
        implicit: bool,
        /// `Some(true)` flow, `Some(false)` block, `None` an indentless sequence.
        flow_style: Option<bool>,
    },
    SequenceEnd,
    MappingStart {
        anchor: Option<String>,
        tag: Option<String>,
        implicit: bool,
        flow_style: Option<bool>,
    },
    MappingEnd,
}

/// An event with its marks.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Event {
    pub kind: EventKind,
    pub start_mark: Mark,
    pub end_mark: Mark,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum State {
    StreamStart,
    ImplicitDocumentStart,
    DocumentStart,
    DocumentEnd,
    DocumentContent,
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
}

type ParseResult<T> = Result<T, YamlError>;

fn parser_error(
    context: Option<String>,
    context_mark: Option<Mark>,
    problem: String,
    problem_mark: Mark,
) -> YamlError {
    YamlError::Marked(MarkedError::new(
        MarkedKind::Parser,
        context,
        context_mark,
        problem,
        Some(problem_mark),
    ))
}

const DEFAULT_TAGS: [(&str, &str); 2] = [("!", "!"), ("!!", "tag:yaml.org,2002:")];

/// `Parser`.
pub(crate) struct Parser {
    scanner: Scanner,
    current_event: Option<Event>,
    tag_handles: Vec<(String, String)>,
    states: Vec<State>,
    marks: Vec<Mark>,
    state: Option<State>,
}

impl Parser {
    pub(crate) fn new(scanner: Scanner) -> Self {
        Self {
            scanner,
            current_event: None,
            tag_handles: Vec::new(),
            states: Vec::new(),
            marks: Vec::new(),
            state: Some(State::StreamStart),
        }
    }

    fn fill(&mut self) -> ParseResult<()> {
        if self.current_event.is_none() {
            if let Some(state) = self.state {
                self.current_event = Some(self.run(state)?);
            }
        }
        Ok(())
    }

    /// `peek_event()`.
    pub(crate) fn peek_event(&mut self) -> ParseResult<Option<&Event>> {
        self.fill()?;
        Ok(self.current_event.as_ref())
    }

    /// `get_event()`.
    pub(crate) fn get_event(&mut self) -> ParseResult<Option<Event>> {
        self.fill()?;
        Ok(self.current_event.take())
    }

    fn check(&mut self, choices: &[TokenClass]) -> ParseResult<bool> {
        self.scanner.check_token(choices)
    }

    fn peek_token(&mut self) -> ParseResult<Token> {
        Ok(self.scanner.peek_token()?.cloned().expect("the scanner always ends with a stream end token"))
    }

    fn get_token(&mut self) -> ParseResult<Token> {
        Ok(self.scanner.get_token()?.expect("the scanner always ends with a stream end token"))
    }

    fn pop_state(&mut self) -> Option<State> {
        self.states.pop()
    }

    fn run(&mut self, state: State) -> ParseResult<Event> {
        match state {
            State::StreamStart => {
                let token = self.get_token()?;
                self.state = Some(State::ImplicitDocumentStart);
                Ok(Event {
                    kind: EventKind::StreamStart,
                    start_mark: token.start_mark,
                    end_mark: token.end_mark,
                })
            }
            State::ImplicitDocumentStart => self.parse_implicit_document_start(),
            State::DocumentStart => self.parse_document_start(),
            State::DocumentEnd => self.parse_document_end(),
            State::DocumentContent => self.parse_document_content(),
            State::BlockNode => self.parse_node(true, false),
            State::BlockSequenceFirstEntry => {
                let token = self.get_token()?;
                self.marks.push(token.start_mark);
                self.parse_block_sequence_entry()
            }
            State::BlockSequenceEntry => self.parse_block_sequence_entry(),
            State::IndentlessSequenceEntry => self.parse_indentless_sequence_entry(),
            State::BlockMappingFirstKey => {
                let token = self.get_token()?;
                self.marks.push(token.start_mark);
                self.parse_block_mapping_key()
            }
            State::BlockMappingKey => self.parse_block_mapping_key(),
            State::BlockMappingValue => self.parse_block_mapping_value(),
            State::FlowSequenceFirstEntry => {
                let token = self.get_token()?;
                self.marks.push(token.start_mark);
                self.parse_flow_sequence_entry(true)
            }
            State::FlowSequenceEntry => self.parse_flow_sequence_entry(false),
            State::FlowSequenceEntryMappingKey => self.parse_flow_sequence_entry_mapping_key(),
            State::FlowSequenceEntryMappingValue => self.parse_flow_sequence_entry_mapping_value(),
            State::FlowSequenceEntryMappingEnd => {
                self.state = Some(State::FlowSequenceEntry);
                let token = self.peek_token()?;
                Ok(Event {
                    kind: EventKind::MappingEnd,
                    start_mark: token.start_mark.clone(),
                    end_mark: token.start_mark,
                })
            }
            State::FlowMappingFirstKey => {
                let token = self.get_token()?;
                self.marks.push(token.start_mark);
                self.parse_flow_mapping_key(true)
            }
            State::FlowMappingKey => self.parse_flow_mapping_key(false),
            State::FlowMappingValue => self.parse_flow_mapping_value(),
            State::FlowMappingEmptyValue => {
                self.state = Some(State::FlowMappingKey);
                let mark = self.peek_token()?.start_mark;
                Ok(empty_scalar(mark))
            }
        }
    }

    fn parse_implicit_document_start(&mut self) -> ParseResult<Event> {
        if !self.check(&[TokenClass::Directive, TokenClass::DocumentStart, TokenClass::StreamEnd])? {
            self.tag_handles = DEFAULT_TAGS.iter().map(|(h, p)| ((*h).to_owned(), (*p).to_owned())).collect();
            let token = self.peek_token()?;
            let mark = token.start_mark;
            self.states.push(State::DocumentEnd);
            self.state = Some(State::BlockNode);
            return Ok(Event {
                kind: EventKind::DocumentStart { explicit: false, version: None, tags: None },
                start_mark: mark.clone(),
                end_mark: mark,
            });
        }
        self.parse_document_start()
    }

    fn parse_document_start(&mut self) -> ParseResult<Event> {
        while self.check(&[TokenClass::DocumentEnd])? {
            self.get_token()?;
        }
        if !self.check(&[TokenClass::StreamEnd])? {
            let start_mark = self.peek_token()?.start_mark;
            let (version, tags) = self.process_directives()?;
            if !self.check(&[TokenClass::DocumentStart])? {
                let token = self.peek_token()?;
                return Err(parser_error(
                    None,
                    None,
                    format!("expected '<document start>', but found {}", repr(token.kind.id())),
                    token.start_mark,
                ));
            }
            let token = self.get_token()?;
            self.states.push(State::DocumentEnd);
            self.state = Some(State::DocumentContent);
            return Ok(Event {
                kind: EventKind::DocumentStart { explicit: true, version, tags },
                start_mark,
                end_mark: token.end_mark,
            });
        }
        let token = self.get_token()?;
        self.state = None;
        Ok(Event { kind: EventKind::StreamEnd, start_mark: token.start_mark, end_mark: token.end_mark })
    }

    fn parse_document_end(&mut self) -> ParseResult<Event> {
        let token = self.peek_token()?;
        let start_mark = token.start_mark.clone();
        let mut end_mark = token.start_mark;
        let mut explicit = false;
        if self.check(&[TokenClass::DocumentEnd])? {
            end_mark = self.get_token()?.end_mark;
            explicit = true;
        }
        self.state = Some(State::DocumentStart);
        Ok(Event { kind: EventKind::DocumentEnd { explicit }, start_mark, end_mark })
    }

    fn parse_document_content(&mut self) -> ParseResult<Event> {
        if self.check(&[
            TokenClass::Directive,
            TokenClass::DocumentStart,
            TokenClass::DocumentEnd,
            TokenClass::StreamEnd,
        ])? {
            let mark = self.peek_token()?.start_mark;
            self.state = self.pop_state();
            return Ok(empty_scalar(mark));
        }
        self.parse_node(true, false)
    }

    #[allow(clippy::type_complexity)]
    fn process_directives(
        &mut self,
    ) -> ParseResult<(Option<(String, String)>, Option<Vec<(String, String)>>)> {
        let mut version: Option<(String, String)> = None;
        self.tag_handles = Vec::new();
        while self.check(&[TokenClass::Directive])? {
            let token = self.get_token()?;
            if let TokenKind::Directive { name, value } = &token.kind {
                match (name.as_str(), value) {
                    ("YAML", DirectiveValue::Yaml(major, minor)) => {
                        if version.is_some() {
                            return Err(YamlError::Marked(MarkedError::new(
                                MarkedKind::Parser,
                                None,
                                None,
                                "found duplicate YAML directive",
                                Some(token.start_mark),
                            )));
                        }
                        if major.trim_start_matches('0') != "1" {
                            return Err(YamlError::Marked(MarkedError::new(
                                MarkedKind::Parser,
                                None,
                                None,
                                "found incompatible YAML document (version 1.* is required)",
                                Some(token.start_mark),
                            )));
                        }
                        version = Some((major.clone(), minor.clone()));
                    }
                    ("TAG", DirectiveValue::Tag(handle, prefix)) => {
                        if self.tag_handles.iter().any(|(existing, _)| existing == handle) {
                            return Err(YamlError::Marked(MarkedError::new(
                                MarkedKind::Parser,
                                None,
                                None,
                                format!("duplicate tag handle {}", repr(handle)),
                                Some(token.start_mark),
                            )));
                        }
                        self.tag_handles.push((handle.clone(), prefix.clone()));
                    }
                    _ => {}
                }
            }
        }
        let tags = (!self.tag_handles.is_empty()).then(|| self.tag_handles.clone());
        for (handle, prefix) in DEFAULT_TAGS {
            if !self.tag_handles.iter().any(|(existing, _)| existing == handle) {
                self.tag_handles.push((handle.to_owned(), prefix.to_owned()));
            }
        }
        Ok((version, tags))
    }

    fn parse_node(&mut self, block: bool, indentless_sequence: bool) -> ParseResult<Event> {
        if self.check(&[TokenClass::Alias])? {
            let token = self.get_token()?;
            let TokenKind::Alias(anchor) = token.kind else { unreachable!() };
            self.state = self.pop_state();
            return Ok(Event {
                kind: EventKind::Alias { anchor },
                start_mark: token.start_mark,
                end_mark: token.end_mark,
            });
        }
        let mut anchor = None;
        let mut tag_token: Option<(Option<String>, String)> = None;
        let (mut start_mark, mut end_mark, mut tag_mark): (Option<Mark>, Option<Mark>, Option<Mark>) =
            (None, None, None);
        if self.check(&[TokenClass::Anchor])? {
            let token = self.get_token()?;
            start_mark = Some(token.start_mark);
            end_mark = Some(token.end_mark);
            if let TokenKind::Anchor(value) = token.kind {
                anchor = Some(value);
            }
            if self.check(&[TokenClass::Tag])? {
                let token = self.get_token()?;
                tag_mark = Some(token.start_mark);
                end_mark = Some(token.end_mark);
                if let TokenKind::Tag(handle, suffix) = token.kind {
                    tag_token = Some((handle, suffix));
                }
            }
        } else if self.check(&[TokenClass::Tag])? {
            let token = self.get_token()?;
            start_mark = Some(token.start_mark.clone());
            tag_mark = Some(token.start_mark);
            end_mark = Some(token.end_mark);
            if let TokenKind::Tag(handle, suffix) = token.kind {
                tag_token = Some((handle, suffix));
            }
            if self.check(&[TokenClass::Anchor])? {
                let token = self.get_token()?;
                end_mark = Some(token.end_mark);
                if let TokenKind::Anchor(value) = token.kind {
                    anchor = Some(value);
                }
            }
        }
        let mut tag: Option<String> = None;
        if let Some((handle, suffix)) = tag_token {
            tag = Some(match handle {
                Some(handle) => match self.tag_handles.iter().find(|(existing, _)| *existing == handle) {
                    Some((_, prefix)) => format!("{prefix}{suffix}"),
                    None => {
                        return Err(parser_error(
                            Some("while parsing a node".into()),
                            start_mark,
                            format!("found undefined tag handle {}", repr(&handle)),
                            tag_mark.expect("a tag has a mark"),
                        ))
                    }
                },
                None => suffix,
            });
        }
        let start_mark = match start_mark {
            Some(mark) => mark,
            None => {
                let mark = self.peek_token()?.start_mark;
                end_mark = Some(mark.clone());
                mark
            }
        };
        let mut end_mark = end_mark.expect("set above");
        let implicit = tag.is_none() || tag.as_deref() == Some("!");
        if indentless_sequence && self.check(&[TokenClass::BlockEntry])? {
            end_mark = self.peek_token()?.end_mark;
            self.state = Some(State::IndentlessSequenceEntry);
            return Ok(Event {
                kind: EventKind::SequenceStart { anchor, tag, implicit, flow_style: None },
                start_mark,
                end_mark,
            });
        }
        if self.check(&[TokenClass::Scalar])? {
            let token = self.get_token()?;
            end_mark = token.end_mark;
            let TokenKind::Scalar { value, plain, style } = token.kind else { unreachable!() };
            let implicit = if (plain && tag.is_none()) || tag.as_deref() == Some("!") {
                (true, false)
            } else if tag.is_none() {
                (false, true)
            } else {
                (false, false)
            };
            self.state = self.pop_state();
            return Ok(Event {
                kind: EventKind::Scalar { anchor, tag, implicit, value, style },
                start_mark,
                end_mark,
            });
        }
        if self.check(&[TokenClass::FlowSequenceStart])? {
            end_mark = self.peek_token()?.end_mark;
            self.state = Some(State::FlowSequenceFirstEntry);
            return Ok(Event {
                kind: EventKind::SequenceStart { anchor, tag, implicit, flow_style: Some(true) },
                start_mark,
                end_mark,
            });
        }
        if self.check(&[TokenClass::FlowMappingStart])? {
            end_mark = self.peek_token()?.end_mark;
            self.state = Some(State::FlowMappingFirstKey);
            return Ok(Event {
                kind: EventKind::MappingStart { anchor, tag, implicit, flow_style: Some(true) },
                start_mark,
                end_mark,
            });
        }
        if block && self.check(&[TokenClass::BlockSequenceStart])? {
            end_mark = self.peek_token()?.start_mark;
            self.state = Some(State::BlockSequenceFirstEntry);
            return Ok(Event {
                kind: EventKind::SequenceStart { anchor, tag, implicit, flow_style: Some(false) },
                start_mark,
                end_mark,
            });
        }
        if block && self.check(&[TokenClass::BlockMappingStart])? {
            end_mark = self.peek_token()?.start_mark;
            self.state = Some(State::BlockMappingFirstKey);
            return Ok(Event {
                kind: EventKind::MappingStart { anchor, tag, implicit, flow_style: Some(false) },
                start_mark,
                end_mark,
            });
        }
        if anchor.is_some() || tag.is_some() {
            self.state = self.pop_state();
            return Ok(Event {
                kind: EventKind::Scalar {
                    anchor,
                    tag,
                    implicit: (implicit, false),
                    value: String::new(),
                    style: None,
                },
                start_mark,
                end_mark,
            });
        }
        let node = if block { "block" } else { "flow" };
        let token = self.peek_token()?;
        Err(parser_error(
            Some(format!("while parsing a {node} node")),
            Some(start_mark),
            format!("expected the node content, but found {}", repr(token.kind.id())),
            token.start_mark,
        ))
    }

    fn parse_block_sequence_entry(&mut self) -> ParseResult<Event> {
        if self.check(&[TokenClass::BlockEntry])? {
            let token = self.get_token()?;
            if !self.check(&[TokenClass::BlockEntry, TokenClass::BlockEnd])? {
                self.states.push(State::BlockSequenceEntry);
                return self.parse_node(true, false);
            }
            self.state = Some(State::BlockSequenceEntry);
            return Ok(empty_scalar(token.end_mark));
        }
        if !self.check(&[TokenClass::BlockEnd])? {
            let token = self.peek_token()?;
            return Err(parser_error(
                Some("while parsing a block collection".into()),
                self.marks.last().cloned(),
                format!("expected <block end>, but found {}", repr(token.kind.id())),
                token.start_mark,
            ));
        }
        let token = self.get_token()?;
        self.state = self.pop_state();
        self.marks.pop();
        Ok(Event { kind: EventKind::SequenceEnd, start_mark: token.start_mark, end_mark: token.end_mark })
    }

    fn parse_indentless_sequence_entry(&mut self) -> ParseResult<Event> {
        if self.check(&[TokenClass::BlockEntry])? {
            let token = self.get_token()?;
            if !self.check(&[
                TokenClass::BlockEntry,
                TokenClass::Key,
                TokenClass::Value,
                TokenClass::BlockEnd,
            ])? {
                self.states.push(State::IndentlessSequenceEntry);
                return self.parse_node(true, false);
            }
            self.state = Some(State::IndentlessSequenceEntry);
            return Ok(empty_scalar(token.end_mark));
        }
        let token = self.peek_token()?;
        self.state = self.pop_state();
        Ok(Event {
            kind: EventKind::SequenceEnd,
            start_mark: token.start_mark.clone(),
            end_mark: token.start_mark,
        })
    }

    fn parse_block_mapping_key(&mut self) -> ParseResult<Event> {
        if self.check(&[TokenClass::Key])? {
            let token = self.get_token()?;
            if !self.check(&[TokenClass::Key, TokenClass::Value, TokenClass::BlockEnd])? {
                self.states.push(State::BlockMappingValue);
                return self.parse_node(true, true);
            }
            self.state = Some(State::BlockMappingValue);
            return Ok(empty_scalar(token.end_mark));
        }
        if !self.check(&[TokenClass::BlockEnd])? {
            let token = self.peek_token()?;
            return Err(parser_error(
                Some("while parsing a block mapping".into()),
                self.marks.last().cloned(),
                format!("expected <block end>, but found {}", repr(token.kind.id())),
                token.start_mark,
            ));
        }
        let token = self.get_token()?;
        self.state = self.pop_state();
        self.marks.pop();
        Ok(Event { kind: EventKind::MappingEnd, start_mark: token.start_mark, end_mark: token.end_mark })
    }

    fn parse_block_mapping_value(&mut self) -> ParseResult<Event> {
        if self.check(&[TokenClass::Value])? {
            let token = self.get_token()?;
            if !self.check(&[TokenClass::Key, TokenClass::Value, TokenClass::BlockEnd])? {
                self.states.push(State::BlockMappingKey);
                return self.parse_node(true, true);
            }
            self.state = Some(State::BlockMappingKey);
            return Ok(empty_scalar(token.end_mark));
        }
        self.state = Some(State::BlockMappingKey);
        let mark = self.peek_token()?.start_mark;
        Ok(empty_scalar(mark))
    }

    fn parse_flow_sequence_entry(&mut self, first: bool) -> ParseResult<Event> {
        if !self.check(&[TokenClass::FlowSequenceEnd])? {
            if !first {
                if self.check(&[TokenClass::FlowEntry])? {
                    self.get_token()?;
                } else {
                    let token = self.peek_token()?;
                    return Err(parser_error(
                        Some("while parsing a flow sequence".into()),
                        self.marks.last().cloned(),
                        format!("expected ',' or ']', but got {}", repr(token.kind.id())),
                        token.start_mark,
                    ));
                }
            }
            if self.check(&[TokenClass::Key])? {
                let token = self.peek_token()?;
                self.state = Some(State::FlowSequenceEntryMappingKey);
                return Ok(Event {
                    kind: EventKind::MappingStart {
                        anchor: None,
                        tag: None,
                        implicit: true,
                        flow_style: Some(true),
                    },
                    start_mark: token.start_mark,
                    end_mark: token.end_mark,
                });
            } else if !self.check(&[TokenClass::FlowSequenceEnd])? {
                self.states.push(State::FlowSequenceEntry);
                return self.parse_node(false, false);
            }
        }
        let token = self.get_token()?;
        self.state = self.pop_state();
        self.marks.pop();
        Ok(Event { kind: EventKind::SequenceEnd, start_mark: token.start_mark, end_mark: token.end_mark })
    }

    fn parse_flow_sequence_entry_mapping_key(&mut self) -> ParseResult<Event> {
        let token = self.get_token()?;
        if !self.check(&[TokenClass::Value, TokenClass::FlowEntry, TokenClass::FlowSequenceEnd])? {
            self.states.push(State::FlowSequenceEntryMappingValue);
            return self.parse_node(false, false);
        }
        self.state = Some(State::FlowSequenceEntryMappingValue);
        Ok(empty_scalar(token.end_mark))
    }

    fn parse_flow_sequence_entry_mapping_value(&mut self) -> ParseResult<Event> {
        if self.check(&[TokenClass::Value])? {
            let token = self.get_token()?;
            if !self.check(&[TokenClass::FlowEntry, TokenClass::FlowSequenceEnd])? {
                self.states.push(State::FlowSequenceEntryMappingEnd);
                return self.parse_node(false, false);
            }
            self.state = Some(State::FlowSequenceEntryMappingEnd);
            return Ok(empty_scalar(token.end_mark));
        }
        self.state = Some(State::FlowSequenceEntryMappingEnd);
        let mark = self.peek_token()?.start_mark;
        Ok(empty_scalar(mark))
    }

    fn parse_flow_mapping_key(&mut self, first: bool) -> ParseResult<Event> {
        if !self.check(&[TokenClass::FlowMappingEnd])? {
            if !first {
                if self.check(&[TokenClass::FlowEntry])? {
                    self.get_token()?;
                } else {
                    let token = self.peek_token()?;
                    return Err(parser_error(
                        Some("while parsing a flow mapping".into()),
                        self.marks.last().cloned(),
                        format!("expected ',' or '}}', but got {}", repr(token.kind.id())),
                        token.start_mark,
                    ));
                }
            }
            if self.check(&[TokenClass::Key])? {
                let token = self.get_token()?;
                if !self.check(&[TokenClass::Value, TokenClass::FlowEntry, TokenClass::FlowMappingEnd])? {
                    self.states.push(State::FlowMappingValue);
                    return self.parse_node(false, false);
                }
                self.state = Some(State::FlowMappingValue);
                return Ok(empty_scalar(token.end_mark));
            } else if !self.check(&[TokenClass::FlowMappingEnd])? {
                self.states.push(State::FlowMappingEmptyValue);
                return self.parse_node(false, false);
            }
        }
        let token = self.get_token()?;
        self.state = self.pop_state();
        self.marks.pop();
        Ok(Event { kind: EventKind::MappingEnd, start_mark: token.start_mark, end_mark: token.end_mark })
    }

    fn parse_flow_mapping_value(&mut self) -> ParseResult<Event> {
        if self.check(&[TokenClass::Value])? {
            let token = self.get_token()?;
            if !self.check(&[TokenClass::FlowEntry, TokenClass::FlowMappingEnd])? {
                self.states.push(State::FlowMappingKey);
                return self.parse_node(false, false);
            }
            self.state = Some(State::FlowMappingKey);
            return Ok(empty_scalar(token.end_mark));
        }
        self.state = Some(State::FlowMappingKey);
        let mark = self.peek_token()?.start_mark;
        Ok(empty_scalar(mark))
    }
}

/// `process_empty_scalar(mark)`.
fn empty_scalar(mark: Mark) -> Event {
    Event {
        kind: EventKind::Scalar {
            anchor: None,
            tag: None,
            implicit: (true, false),
            value: String::new(),
            style: None,
        },
        start_mark: mark.clone(),
        end_mark: mark,
    }
}
