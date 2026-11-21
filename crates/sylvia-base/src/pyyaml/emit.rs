//! `yaml/representer.py` (`SafeRepresenter`), `yaml/serializer.py` and `yaml/emitter.py`:
//! values to YAML text, as `yaml.safe_dump(value, sort_keys=False, allow_unicode=True)` writes
//! it. That is the only form Sylvia dumps (`config/_user_settings.py`,
//! `builder/_ruleset_source.py`), so the emitter's other options (canonical output, other
//! widths and indents, explicit documents, directives, encodings) are not ported.

use super::compose::{resolve_scalar, MAP_TAG, SEQ_TAG, STR_TAG};
use super::error::YamlError;
use super::parser::EventKind;
use super::value::{Loaded, Object, ObjectId, Value};
use crate::pyjson::float_repr;
use std::collections::HashMap;

// ── SafeRepresenter ───────────────────────────────────────────────────────────────────────

enum RepKind {
    Scalar { value: String, style: Option<char> },
    Sequence(Vec<usize>),
    Mapping(Vec<(usize, usize)>),
}

struct RepNode {
    tag: &'static str,
    kind: RepKind,
}

struct Representer<'a> {
    loaded: &'a Loaded,
    nodes: Vec<RepNode>,
    /// `represented_objects`: containers already represented, so a shared or recursive one is
    /// the same node.
    represented: HashMap<ObjectId, usize>,
}

/// `SafeRepresenter.represent_float`.
fn float_text(value: f64) -> String {
    if value.is_nan() {
        return ".nan".into();
    }
    if value == f64::INFINITY {
        return ".inf".into();
    }
    if value == f64::NEG_INFINITY {
        return "-.inf".into();
    }
    let text = float_repr(value).to_lowercase();
    if !text.contains('.') && text.contains('e') {
        text.replacen('e', ".0e", 1)
    } else {
        text
    }
}

impl Representer<'_> {
    fn push(&mut self, tag: &'static str, kind: RepKind) -> usize {
        self.nodes.push(RepNode { tag, kind });
        self.nodes.len() - 1
    }

    fn scalar(&mut self, tag: &'static str, value: String) -> usize {
        self.push(tag, RepKind::Scalar { value, style: None })
    }

    /// `represent_data(value)`.
    fn represent(&mut self, value: &Value) -> usize {
        match value {
            Value::None => self.scalar("tag:yaml.org,2002:null", "null".into()),
            Value::Bool(flag) => {
                self.scalar("tag:yaml.org,2002:bool", if *flag { "true" } else { "false" }.into())
            }
            Value::Int(number) => self.scalar("tag:yaml.org,2002:int", number.to_string()),
            Value::Float(number) => self.scalar("tag:yaml.org,2002:float", float_text(*number)),
            Value::Str(text) => self.scalar(STR_TAG, text.clone()),
            Value::Bytes(bytes) => self.push(
                "tag:yaml.org,2002:binary",
                RepKind::Scalar { value: crate::pybase64::encodebytes(bytes), style: Some('|') },
            ),
            Value::Date(date) => self.scalar("tag:yaml.org,2002:timestamp", date.isoformat()),
            Value::DateTime(datetime) => {
                self.scalar("tag:yaml.org,2002:timestamp", datetime.isoformat_with(' '))
            }
            Value::Tuple(items) => {
                let node = self.push(SEQ_TAG, RepKind::Sequence(Vec::new()));
                let children = items.iter().map(|item| self.represent(item)).collect();
                self.nodes[node].kind = RepKind::Sequence(children);
                node
            }
            Value::List(id) | Value::Dict(id) | Value::Set(id) => {
                if let Some(node) = self.represented.get(id) {
                    return *node;
                }
                let loaded = self.loaded;
                match loaded.object(*id) {
                    Object::List(items) => {
                        let node = self.push(SEQ_TAG, RepKind::Sequence(Vec::new()));
                        self.represented.insert(*id, node);
                        let children = items.iter().map(|item| self.represent(item)).collect();
                        self.nodes[node].kind = RepKind::Sequence(children);
                        node
                    }
                    Object::Dict(pairs) => {
                        let node = self.push(MAP_TAG, RepKind::Mapping(Vec::new()));
                        self.represented.insert(*id, node);
                        let children = pairs
                            .iter()
                            .map(|(key, item)| (self.represent(key), self.represent(item)))
                            .collect();
                        self.nodes[node].kind = RepKind::Mapping(children);
                        node
                    }
                    Object::Set(items) => {
                        let node = self.push("tag:yaml.org,2002:set", RepKind::Mapping(Vec::new()));
                        self.represented.insert(*id, node);
                        let children = items
                            .iter()
                            .map(|key| (self.represent(key), self.represent(&Value::None)))
                            .collect();
                        self.nodes[node].kind = RepKind::Mapping(children);
                        node
                    }
                    Object::Empty => {
                        let node = self.push(SEQ_TAG, RepKind::Sequence(Vec::new()));
                        self.represented.insert(*id, node);
                        node
                    }
                }
            }
        }
    }
}

// ── Serializer ────────────────────────────────────────────────────────────────────────────

struct Serializer<'a> {
    nodes: &'a [RepNode],
    anchors: HashMap<usize, Option<String>>,
    last_anchor_id: usize,
    serialized: std::collections::HashSet<usize>,
    events: Vec<EventKind>,
}

impl Serializer<'_> {
    fn anchor_node(&mut self, node: usize) {
        if let Some(anchor) = self.anchors.get_mut(&node) {
            if anchor.is_none() {
                self.last_anchor_id += 1;
                *anchor = Some(format!("id{:03}", self.last_anchor_id));
            }
            return;
        }
        self.anchors.insert(node, None);
        match &self.nodes[node].kind {
            RepKind::Scalar { .. } => {}
            RepKind::Sequence(items) => {
                for item in items.clone() {
                    self.anchor_node(item);
                }
            }
            RepKind::Mapping(pairs) => {
                for (key, value) in pairs.clone() {
                    self.anchor_node(key);
                    self.anchor_node(value);
                }
            }
        }
    }

    fn serialize_node(&mut self, node: usize) {
        let alias = self.anchors.get(&node).cloned().flatten();
        if !self.serialized.insert(node) {
            self.events.push(EventKind::Alias { anchor: alias.unwrap_or_default() });
            return;
        }
        let tag = self.nodes[node].tag;
        match &self.nodes[node].kind {
            RepKind::Scalar { value, style } => {
                let detected = resolve_scalar(value, (true, false));
                let implicit = (tag == detected, tag == STR_TAG);
                self.events.push(EventKind::Scalar {
                    anchor: alias,
                    tag: Some(tag.to_owned()),
                    implicit,
                    value: value.clone(),
                    style: *style,
                });
            }
            RepKind::Sequence(items) => {
                let items = items.clone();
                self.events.push(EventKind::SequenceStart {
                    anchor: alias,
                    tag: Some(tag.to_owned()),
                    implicit: tag == SEQ_TAG,
                    flow_style: Some(false),
                });
                for item in items {
                    self.serialize_node(item);
                }
                self.events.push(EventKind::SequenceEnd);
            }
            RepKind::Mapping(pairs) => {
                let pairs = pairs.clone();
                self.events.push(EventKind::MappingStart {
                    anchor: alias,
                    tag: Some(tag.to_owned()),
                    implicit: tag == MAP_TAG,
                    flow_style: Some(false),
                });
                for (key, value) in pairs {
                    self.serialize_node(key);
                    self.serialize_node(value);
                }
                self.events.push(EventKind::MappingEnd);
            }
        }
    }
}

// ── Emitter ───────────────────────────────────────────────────────────────────────────────

const BEST_INDENT: usize = 2;
const BEST_WIDTH: usize = 80;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum State {
    StreamStart,
    DocumentStart { first: bool },
    DocumentEnd,
    DocumentRoot,
    Nothing,
    FlowSequenceItem { first: bool },
    FlowMappingKey { first: bool },
    FlowMappingSimpleValue,
    FlowMappingValue,
    BlockSequenceItem { first: bool },
    BlockMappingKey { first: bool },
    BlockMappingSimpleValue,
    BlockMappingValue,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Style {
    Plain,
    Single,
    Double,
    Literal,
    Folded,
}

struct Analysis {
    scalar: Vec<char>,
    empty: bool,
    multiline: bool,
    allow_flow_plain: bool,
    allow_block_plain: bool,
    allow_single_quoted: bool,
    allow_block: bool,
}

fn is_break(ch: char) -> bool {
    matches!(ch, '\n' | '\u{85}' | '\u{2028}' | '\u{2029}')
}

fn is_blank_or_end(ch: char) -> bool {
    matches!(ch, '\0' | ' ' | '\t' | '\r' | '\n' | '\u{85}' | '\u{2028}' | '\u{2029}')
}

/// `Emitter.analyze_scalar` with `allow_unicode=True`.
fn analyze_scalar(text: &str) -> Analysis {
    let scalar: Vec<char> = text.chars().collect();
    if scalar.is_empty() {
        return Analysis {
            scalar,
            empty: true,
            multiline: false,
            allow_flow_plain: false,
            allow_block_plain: true,
            allow_single_quoted: true,
            allow_block: false,
        };
    }
    let (mut block_indicators, mut flow_indicators, mut line_breaks, mut special_characters) =
        (false, false, false, false);
    let (mut leading_space, mut leading_break, mut trailing_space, mut trailing_break) =
        (false, false, false, false);
    let (mut break_space, mut space_break) = (false, false);
    if text.starts_with("---") || text.starts_with("...") {
        block_indicators = true;
        flow_indicators = true;
    }
    let mut preceded_by_whitespace = true;
    let mut followed_by_whitespace = scalar.len() == 1 || is_blank_or_end(scalar[1]);
    let (mut previous_space, mut previous_break) = (false, false);
    let last = scalar.len() - 1;
    for (index, &ch) in scalar.iter().enumerate() {
        if index == 0 {
            if "#,[]{}&*!|>'\"%@`".contains(ch) {
                flow_indicators = true;
                block_indicators = true;
            }
            if ch == '?' || ch == ':' {
                flow_indicators = true;
                if followed_by_whitespace {
                    block_indicators = true;
                }
            }
            if ch == '-' && followed_by_whitespace {
                flow_indicators = true;
                block_indicators = true;
            }
        } else {
            if ",?[]{}".contains(ch) {
                flow_indicators = true;
            }
            if ch == ':' {
                flow_indicators = true;
                if followed_by_whitespace {
                    block_indicators = true;
                }
            }
            if ch == '#' && preceded_by_whitespace {
                flow_indicators = true;
                block_indicators = true;
            }
        }
        if is_break(ch) {
            line_breaks = true;
        }
        if !(ch == '\n' || ('\x20'..='\x7E').contains(&ch)) {
            let unicode = (ch == '\u{85}'
                || ('\u{A0}'..='\u{D7FF}').contains(&ch)
                || ('\u{E000}'..='\u{FFFD}').contains(&ch)
                || ('\u{10000}'..'\u{10FFFF}').contains(&ch))
                && ch != '\u{FEFF}';
            if !unicode {
                special_characters = true;
            }
        }
        if ch == ' ' {
            if index == 0 {
                leading_space = true;
            }
            if index == last {
                trailing_space = true;
            }
            if previous_break {
                break_space = true;
            }
            previous_space = true;
            previous_break = false;
        } else if is_break(ch) {
            if index == 0 {
                leading_break = true;
            }
            if index == last {
                trailing_break = true;
            }
            if previous_space {
                space_break = true;
            }
            previous_space = false;
            previous_break = true;
        } else {
            previous_space = false;
            previous_break = false;
        }
        preceded_by_whitespace = is_blank_or_end(ch);
        followed_by_whitespace = index + 2 >= scalar.len() || is_blank_or_end(scalar[index + 2]);
    }
    let (mut allow_flow_plain, mut allow_block_plain, mut allow_single_quoted, mut allow_block) =
        (true, true, true, true);
    if leading_space || leading_break || trailing_space || trailing_break {
        allow_flow_plain = false;
        allow_block_plain = false;
    }
    if trailing_space {
        allow_block = false;
    }
    if break_space {
        allow_flow_plain = false;
        allow_block_plain = false;
        allow_single_quoted = false;
    }
    if space_break || special_characters {
        allow_flow_plain = false;
        allow_block_plain = false;
        allow_single_quoted = false;
        allow_block = false;
    }
    if line_breaks {
        allow_flow_plain = false;
        allow_block_plain = false;
    }
    if flow_indicators {
        allow_flow_plain = false;
    }
    if block_indicators {
        allow_block_plain = false;
    }
    Analysis {
        scalar,
        empty: false,
        multiline: line_breaks,
        allow_flow_plain,
        allow_block_plain,
        allow_single_quoted,
        allow_block,
    }
}

fn anchor_ok(anchor: &str) -> Result<String, YamlError> {
    let emitter = |message: String| YamlError::Dump { class: "EmitterError", message };
    if anchor.is_empty() {
        return Err(emitter("anchor must not be empty".into()));
    }
    if let Some(ch) = anchor.chars().find(|c| !(c.is_ascii_alphanumeric() || *c == '-' || *c == '_')) {
        return Err(emitter(format!(
            "invalid character {} in the anchor: {}",
            crate::pystr::repr(&ch.to_string()),
            crate::pystr::repr(anchor)
        )));
    }
    Ok(anchor.to_owned())
}

/// `Emitter.prepare_tag` with the default tag prefixes.
fn prepare_tag(tag: &str) -> String {
    if tag == "!" {
        return tag.to_owned();
    }
    let mut handle: Option<&str> = None;
    let mut suffix = tag;
    for (prefix, prefix_handle) in [("!", "!"), ("tag:yaml.org,2002:", "!!")] {
        if tag.starts_with(prefix) && (prefix == "!" || prefix.len() < tag.len()) {
            handle = Some(prefix_handle);
            suffix = &tag[prefix.len()..];
        }
    }
    let mut out = String::new();
    for ch in suffix.chars() {
        if ch.is_ascii_alphanumeric()
            || "-;/?:@&=+$,_.~*'()[]".contains(ch)
            || (ch == '!' && handle != Some("!"))
        {
            out.push(ch);
        } else {
            let mut buffer = [0u8; 4];
            for byte in ch.encode_utf8(&mut buffer).bytes() {
                out.push_str(&format!("%{byte:02X}"));
            }
        }
    }
    match handle {
        Some(handle) if !handle.is_empty() => format!("{handle}{out}"),
        _ => format!("!<{out}>"),
    }
}

struct Emitter<'e> {
    events: &'e [EventKind],
    position: usize,
    out: String,
    states: Vec<State>,
    state: State,
    indents: Vec<Option<usize>>,
    indent: Option<usize>,
    flow_level: usize,
    root_context: bool,
    simple_key_context: bool,
    mapping_context: bool,
    column: usize,
    whitespace: bool,
    indention: bool,
    open_ended: bool,
    prepared_anchor: Option<String>,
    prepared_tag: Option<String>,
    analysis: Option<Analysis>,
    style: Option<Style>,
}

type EmitResult = Result<(), YamlError>;

impl<'e> Emitter<'e> {
    fn event(&self) -> &'e EventKind {
        &self.events[self.position]
    }

    fn next_event(&self) -> Option<&'e EventKind> {
        self.events.get(self.position + 1)
    }

    fn run(&mut self) -> EmitResult {
        for position in 0..self.events.len() {
            self.position = position;
            self.step()?;
        }
        Ok(())
    }

    fn pop_state(&mut self) -> State {
        self.states.pop().expect("the emitter's state stack follows the events")
    }

    fn pop_indent(&mut self) {
        self.indent = self.indents.pop().expect("the indent stack follows the events");
    }

    fn step(&mut self) -> EmitResult {
        let event = self.event();
        match self.state {
            State::StreamStart => {
                self.state = State::DocumentStart { first: true };
            }
            State::DocumentStart { first } => match event {
                EventKind::DocumentStart { explicit, .. } => {
                    let implicit = first && !explicit && !self.check_empty_document();
                    if !implicit {
                        self.write_indent();
                        self.write_indicator("---", true, false, false);
                    }
                    self.state = State::DocumentRoot;
                }
                EventKind::StreamEnd => {
                    if self.open_ended {
                        self.write_indicator("...", true, false, false);
                        self.write_indent();
                    }
                    self.state = State::Nothing;
                }
                _ => unreachable!("the serializer writes documents"),
            },
            State::DocumentEnd => {
                self.write_indent();
                self.state = State::DocumentStart { first: false };
            }
            State::DocumentRoot => {
                self.states.push(State::DocumentEnd);
                self.expect_node(true, false, false)?;
            }
            State::Nothing => unreachable!("the serializer ends the stream"),
            State::FlowSequenceItem { first } => {
                if matches!(event, EventKind::SequenceEnd) {
                    self.pop_indent();
                    self.flow_level -= 1;
                    self.write_indicator("]", false, false, false);
                    self.state = self.pop_state();
                } else {
                    if !first {
                        self.write_indicator(",", false, false, false);
                    }
                    if self.column > BEST_WIDTH {
                        self.write_indent();
                    }
                    self.states.push(State::FlowSequenceItem { first: false });
                    self.expect_node(false, false, false)?;
                }
            }
            State::FlowMappingKey { first } => {
                if matches!(event, EventKind::MappingEnd) {
                    self.pop_indent();
                    self.flow_level -= 1;
                    self.write_indicator("}", false, false, false);
                    self.state = self.pop_state();
                } else {
                    if !first {
                        self.write_indicator(",", false, false, false);
                    }
                    if self.column > BEST_WIDTH {
                        self.write_indent();
                    }
                    if self.check_simple_key() {
                        self.states.push(State::FlowMappingSimpleValue);
                        self.expect_node(false, true, true)?;
                    } else {
                        self.write_indicator("?", true, false, false);
                        self.states.push(State::FlowMappingValue);
                        self.expect_node(false, true, false)?;
                    }
                }
            }
            State::FlowMappingSimpleValue => {
                self.write_indicator(":", false, false, false);
                self.states.push(State::FlowMappingKey { first: false });
                self.expect_node(false, true, false)?;
            }
            State::FlowMappingValue => {
                if self.column > BEST_WIDTH {
                    self.write_indent();
                }
                self.write_indicator(":", true, false, false);
                self.states.push(State::FlowMappingKey { first: false });
                self.expect_node(false, true, false)?;
            }
            State::BlockSequenceItem { first } => {
                if !first && matches!(event, EventKind::SequenceEnd) {
                    self.pop_indent();
                    self.state = self.pop_state();
                } else {
                    self.write_indent();
                    self.write_indicator("-", true, false, true);
                    self.states.push(State::BlockSequenceItem { first: false });
                    self.expect_node(false, false, false)?;
                }
            }
            State::BlockMappingKey { first } => {
                if !first && matches!(event, EventKind::MappingEnd) {
                    self.pop_indent();
                    self.state = self.pop_state();
                } else {
                    self.write_indent();
                    if self.check_simple_key() {
                        self.states.push(State::BlockMappingSimpleValue);
                        self.expect_node(false, true, true)?;
                    } else {
                        self.write_indicator("?", true, false, true);
                        self.states.push(State::BlockMappingValue);
                        self.expect_node(false, true, false)?;
                    }
                }
            }
            State::BlockMappingSimpleValue => {
                self.write_indicator(":", false, false, false);
                self.states.push(State::BlockMappingKey { first: false });
                self.expect_node(false, true, false)?;
            }
            State::BlockMappingValue => {
                self.write_indent();
                self.write_indicator(":", true, false, true);
                self.states.push(State::BlockMappingKey { first: false });
                self.expect_node(false, true, false)?;
            }
        }
        Ok(())
    }

    fn increase_indent(&mut self, flow: bool, indentless: bool) {
        self.indents.push(self.indent);
        match self.indent {
            None => self.indent = Some(if flow { BEST_INDENT } else { 0 }),
            Some(indent) if !indentless => self.indent = Some(indent + BEST_INDENT),
            Some(_) => {}
        }
    }

    fn expect_node(&mut self, root: bool, mapping: bool, simple_key: bool) -> EmitResult {
        self.root_context = root;
        self.mapping_context = mapping;
        self.simple_key_context = simple_key;
        let event = self.event();
        match event {
            EventKind::Alias { .. } => {
                self.process_anchor("*")?;
                self.state = self.pop_state();
            }
            EventKind::Scalar { .. } => {
                self.process_anchor("&")?;
                self.process_tag();
                self.increase_indent(true, false);
                self.process_scalar();
                self.pop_indent();
                self.state = self.pop_state();
            }
            EventKind::SequenceStart { flow_style, .. } => {
                self.process_anchor("&")?;
                self.process_tag();
                if self.flow_level > 0 || *flow_style == Some(true) || self.check_empty_sequence() {
                    self.write_indicator("[", true, true, false);
                    self.flow_level += 1;
                    self.increase_indent(true, false);
                    self.state = State::FlowSequenceItem { first: true };
                } else {
                    let indentless = self.mapping_context && !self.indention;
                    self.increase_indent(false, indentless);
                    self.state = State::BlockSequenceItem { first: true };
                }
            }
            EventKind::MappingStart { flow_style, .. } => {
                self.process_anchor("&")?;
                self.process_tag();
                if self.flow_level > 0 || *flow_style == Some(true) || self.check_empty_mapping() {
                    self.write_indicator("{", true, true, false);
                    self.flow_level += 1;
                    self.increase_indent(true, false);
                    self.state = State::FlowMappingKey { first: true };
                } else {
                    self.increase_indent(false, false);
                    self.state = State::BlockMappingKey { first: true };
                }
            }
            _ => unreachable!("the serializer writes a node here"),
        }
        Ok(())
    }

    fn check_empty_sequence(&self) -> bool {
        matches!(self.event(), EventKind::SequenceStart { .. })
            && matches!(self.next_event(), Some(EventKind::SequenceEnd))
    }

    fn check_empty_mapping(&self) -> bool {
        matches!(self.event(), EventKind::MappingStart { .. })
            && matches!(self.next_event(), Some(EventKind::MappingEnd))
    }

    fn check_empty_document(&self) -> bool {
        // `event.implicit` is a non-empty tuple, so only the other fields decide.
        matches!(
            self.next_event(),
            Some(EventKind::Scalar { anchor: None, tag: None, value, .. }) if value.is_empty()
        )
    }

    fn check_simple_key(&mut self) -> bool {
        let mut length = 0;
        let event = self.event();
        let (anchor, tag) = match event {
            EventKind::Alias { anchor } => (Some(anchor), None),
            EventKind::Scalar { anchor, tag, .. }
            | EventKind::SequenceStart { anchor, tag, .. }
            | EventKind::MappingStart { anchor, tag, .. } => (anchor.as_ref(), tag.as_ref()),
            _ => (None, None),
        };
        if let Some(anchor) = anchor {
            if self.prepared_anchor.is_none() {
                self.prepared_anchor = Some(anchor.clone());
            }
            length += self.prepared_anchor.as_deref().map_or(0, |a| a.chars().count());
        }
        if let Some(tag) = tag {
            if self.prepared_tag.is_none() {
                self.prepared_tag = Some(prepare_tag(tag));
            }
            length += self.prepared_tag.as_deref().map_or(0, |t| t.chars().count());
        }
        if let EventKind::Scalar { value, .. } = event {
            if self.analysis.is_none() {
                self.analysis = Some(analyze_scalar(value));
            }
            length += self.analysis.as_ref().map_or(0, |a| a.scalar.len());
        }
        let analysis_ok = || self.analysis.as_ref().is_some_and(|a| !a.empty && !a.multiline);
        length < 128
            && (matches!(event, EventKind::Alias { .. })
                || (matches!(event, EventKind::Scalar { .. }) && analysis_ok())
                || self.check_empty_sequence()
                || self.check_empty_mapping())
    }

    fn process_anchor(&mut self, indicator: &str) -> EmitResult {
        let anchor = match self.event() {
            EventKind::Alias { anchor } => Some(anchor),
            EventKind::Scalar { anchor, .. }
            | EventKind::SequenceStart { anchor, .. }
            | EventKind::MappingStart { anchor, .. } => anchor.as_ref(),
            _ => None,
        };
        let Some(anchor) = anchor else {
            self.prepared_anchor = None;
            return Ok(());
        };
        let prepared = match self.prepared_anchor.take() {
            Some(prepared) => prepared,
            None => anchor_ok(anchor)?,
        };
        if !prepared.is_empty() {
            self.write_indicator(&format!("{indicator}{prepared}"), true, false, false);
        }
        Ok(())
    }

    fn process_tag(&mut self) {
        let event = self.event();
        let tag = match event {
            EventKind::Scalar { tag, implicit, .. } => {
                if self.style.is_none() {
                    self.style = Some(self.choose_scalar_style());
                }
                let plain = self.style == Some(Style::Plain);
                if (plain && implicit.0) || (!plain && implicit.1) {
                    self.prepared_tag = None;
                    return;
                }
                match tag {
                    None if implicit.0 => {
                        self.prepared_tag = None;
                        "!".to_owned()
                    }
                    None => unreachable!("the serializer tags every scalar"),
                    Some(tag) => tag.clone(),
                }
            }
            EventKind::SequenceStart { tag, implicit, .. }
            | EventKind::MappingStart { tag, implicit, .. } => {
                if *implicit {
                    self.prepared_tag = None;
                    return;
                }
                tag.clone().expect("the serializer tags every collection")
            }
            _ => return,
        };
        let prepared = self.prepared_tag.take().unwrap_or_else(|| prepare_tag(&tag));
        if !prepared.is_empty() {
            self.write_indicator(&prepared, true, false, false);
        }
    }

    fn choose_scalar_style(&mut self) -> Style {
        let EventKind::Scalar { implicit, style, value, .. } = self.event() else { unreachable!() };
        if self.analysis.is_none() {
            self.analysis = Some(analyze_scalar(value));
        }
        let analysis = self.analysis.as_ref().expect("analysed");
        if *style == Some('"') {
            return Style::Double;
        }
        if style.is_none() && implicit.0 {
            let plain_ok =
                if self.flow_level > 0 { analysis.allow_flow_plain } else { analysis.allow_block_plain };
            if !(self.simple_key_context && (analysis.empty || analysis.multiline)) && plain_ok {
                return Style::Plain;
            }
        }
        if let Some(block @ ('|' | '>')) = style {
            if self.flow_level == 0 && !self.simple_key_context && analysis.allow_block {
                return if *block == '|' { Style::Literal } else { Style::Folded };
            }
        }
        if (style.is_none() || *style == Some('\''))
            && analysis.allow_single_quoted
            && !(self.simple_key_context && analysis.multiline)
        {
            return Style::Single;
        }
        Style::Double
    }

    fn process_scalar(&mut self) {
        if self.analysis.is_none() {
            let EventKind::Scalar { value, .. } = self.event() else { unreachable!() };
            self.analysis = Some(analyze_scalar(value));
        }
        if self.style.is_none() {
            self.style = Some(self.choose_scalar_style());
        }
        let split = !self.simple_key_context;
        let text = self.analysis.take().expect("analysed").scalar;
        match self.style.take().expect("chosen") {
            Style::Double => self.write_double_quoted(&text, split),
            Style::Single => self.write_single_quoted(&text, split),
            Style::Folded => self.write_folded(&text),
            Style::Literal => self.write_literal(&text),
            Style::Plain => self.write_plain(&text, split),
        }
    }

    // Writers.

    fn write(&mut self, data: &str) {
        self.out.push_str(data);
    }

    fn write_chars(&mut self, data: &[char]) {
        self.column += data.len();
        self.out.extend(data.iter());
    }

    fn write_indicator(&mut self, indicator: &str, need_whitespace: bool, whitespace: bool, indention: bool) {
        let data =
            if self.whitespace || !need_whitespace { indicator.to_owned() } else { format!(" {indicator}") };
        self.whitespace = whitespace;
        self.indention = self.indention && indention;
        self.column += data.chars().count();
        self.open_ended = false;
        self.write(&data);
    }

    fn write_indent(&mut self) {
        let indent = self.indent.unwrap_or(0);
        if !self.indention || self.column > indent || (self.column == indent && !self.whitespace) {
            self.write_line_break(None);
        }
        if self.column < indent {
            self.whitespace = true;
            let data = " ".repeat(indent - self.column);
            self.column = indent;
            self.write(&data);
        }
    }

    fn write_line_break(&mut self, data: Option<char>) {
        self.whitespace = true;
        self.indention = true;
        self.column = 0;
        self.out.push(data.unwrap_or('\n'));
    }

    fn write_breaks(&mut self, breaks: &[char]) {
        for &br in breaks {
            self.write_line_break(if br == '\n' { None } else { Some(br) });
        }
    }

    fn write_single_quoted(&mut self, text: &[char], split: bool) {
        self.write_indicator("'", true, false, false);
        let (mut spaces, mut breaks) = (false, false);
        let (mut start, mut end) = (0, 0);
        while end <= text.len() {
            let ch = text.get(end).copied();
            if spaces {
                if ch != Some(' ') {
                    if start + 1 == end
                        && self.column > BEST_WIDTH
                        && split
                        && start != 0
                        && end != text.len()
                    {
                        self.write_indent();
                    } else {
                        self.write_chars(&text[start..end]);
                    }
                    start = end;
                }
            } else if breaks {
                if ch.is_none_or(|c| !is_break(c)) {
                    if text[start] == '\n' {
                        self.write_line_break(None);
                    }
                    self.write_breaks(&text[start..end]);
                    self.write_indent();
                    start = end;
                }
            } else if ch.is_none_or(|c| c == ' ' || is_break(c) || c == '\'') && start < end {
                self.write_chars(&text[start..end]);
                start = end;
            }
            if ch == Some('\'') {
                self.column += 2;
                self.write("''");
                start = end + 1;
            }
            if let Some(c) = ch {
                spaces = c == ' ';
                breaks = is_break(c);
            }
            end += 1;
        }
        self.write_indicator("'", false, false, false);
    }

    fn write_double_quoted(&mut self, text: &[char], split: bool) {
        self.write_indicator("\"", true, false, false);
        let (mut start, mut end) = (0usize, 0usize);
        while end <= text.len() {
            let ch = text.get(end).copied();
            let escape = match ch {
                None => true,
                Some(c) => {
                    "\"\\\u{85}\u{2028}\u{2029}\u{FEFF}".contains(c)
                        || !(('\x20'..='\x7E').contains(&c)
                            || ('\u{A0}'..='\u{D7FF}').contains(&c)
                            || ('\u{E000}'..='\u{FFFD}').contains(&c))
                }
            };
            if escape {
                if start < end {
                    self.write_chars(&text[start..end]);
                    start = end;
                }
                if let Some(c) = ch {
                    let data = match c {
                        '\0' => "\\0".to_owned(),
                        '\x07' => "\\a".to_owned(),
                        '\x08' => "\\b".to_owned(),
                        '\x09' => "\\t".to_owned(),
                        '\x0A' => "\\n".to_owned(),
                        '\x0B' => "\\v".to_owned(),
                        '\x0C' => "\\f".to_owned(),
                        '\x0D' => "\\r".to_owned(),
                        '\x1B' => "\\e".to_owned(),
                        '"' => "\\\"".to_owned(),
                        '\\' => "\\\\".to_owned(),
                        '\u{85}' => "\\N".to_owned(),
                        '\u{A0}' => "\\_".to_owned(),
                        '\u{2028}' => "\\L".to_owned(),
                        '\u{2029}' => "\\P".to_owned(),
                        c if u32::from(c) <= 0xFF => format!("\\x{:02X}", u32::from(c)),
                        c if u32::from(c) <= 0xFFFF => format!("\\u{:04X}", u32::from(c)),
                        c => format!("\\U{:08X}", u32::from(c)),
                    };
                    self.column += data.chars().count();
                    self.write(&data);
                    start = end + 1;
                }
            }
            // `end - start` is -1 right after an escape, as in Python.
            if 0 < end
                && end + 1 < text.len()
                && (ch == Some(' ') || start >= end)
                && self.column as isize + end as isize - start as isize > BEST_WIDTH as isize
                && split
            {
                // Python's text[start:end] is empty when start > end.
                let mut data: String = text[start.min(end)..end].iter().collect();
                data.push('\\');
                if start < end {
                    start = end;
                }
                self.column += data.chars().count();
                self.write(&data);
                self.write_indent();
                self.whitespace = false;
                self.indention = false;
                if text[start] == ' ' {
                    self.column += 1;
                    self.write("\\");
                }
            }
            end += 1;
        }
        self.write_indicator("\"", false, false, false);
    }

    fn block_hints(text: &[char]) -> String {
        let mut hints = String::new();
        if let (Some(&first), Some(&last)) = (text.first(), text.last()) {
            if first == ' ' || is_break(first) {
                hints.push_str(&BEST_INDENT.to_string());
            }
            if !is_break(last) {
                hints.push('-');
            } else if text.len() == 1 || is_break(text[text.len() - 2]) {
                hints.push('+');
            }
        }
        hints
    }

    fn write_folded(&mut self, text: &[char]) {
        let hints = Self::block_hints(text);
        self.write_indicator(&format!(">{hints}"), true, false, false);
        if hints.ends_with('+') {
            self.open_ended = true;
        }
        self.write_line_break(None);
        let (mut leading_space, mut spaces, mut breaks) = (true, false, true);
        let (mut start, mut end) = (0, 0);
        while end <= text.len() {
            let ch = text.get(end).copied();
            if breaks {
                if ch.is_none_or(|c| !is_break(c)) {
                    if !leading_space && ch.is_some_and(|c| c != ' ') && text[start] == '\n' {
                        self.write_line_break(None);
                    }
                    leading_space = ch == Some(' ');
                    self.write_breaks(&text[start..end]);
                    if ch.is_some() {
                        self.write_indent();
                    }
                    start = end;
                }
            } else if spaces {
                if ch != Some(' ') {
                    if start + 1 == end && self.column > BEST_WIDTH {
                        self.write_indent();
                    } else {
                        self.write_chars(&text[start..end]);
                    }
                    start = end;
                }
            } else if ch.is_none_or(|c| c == ' ' || is_break(c)) {
                self.write_chars(&text[start..end]);
                if ch.is_none() {
                    self.write_line_break(None);
                }
                start = end;
            }
            if let Some(c) = ch {
                breaks = is_break(c);
                spaces = c == ' ';
            }
            end += 1;
        }
    }

    fn write_literal(&mut self, text: &[char]) {
        let hints = Self::block_hints(text);
        self.write_indicator(&format!("|{hints}"), true, false, false);
        if hints.ends_with('+') {
            self.open_ended = true;
        }
        self.write_line_break(None);
        let mut breaks = true;
        let (mut start, mut end) = (0, 0);
        while end <= text.len() {
            let ch = text.get(end).copied();
            if breaks {
                if ch.is_none_or(|c| !is_break(c)) {
                    self.write_breaks(&text[start..end]);
                    if ch.is_some() {
                        self.write_indent();
                    }
                    start = end;
                }
            } else if ch.is_none_or(is_break) {
                // Python writes the line without counting its columns.
                self.out.extend(text[start..end].iter());
                if ch.is_none() {
                    self.write_line_break(None);
                }
                start = end;
            }
            if let Some(c) = ch {
                breaks = is_break(c);
            }
            end += 1;
        }
    }

    fn write_plain(&mut self, text: &[char], split: bool) {
        if self.root_context {
            self.open_ended = true;
        }
        if text.is_empty() {
            return;
        }
        if !self.whitespace {
            self.column += 1;
            self.write(" ");
        }
        self.whitespace = false;
        self.indention = false;
        let (mut spaces, mut breaks) = (false, false);
        let (mut start, mut end) = (0, 0);
        while end <= text.len() {
            let ch = text.get(end).copied();
            if spaces {
                if ch != Some(' ') {
                    if start + 1 == end && self.column > BEST_WIDTH && split {
                        self.write_indent();
                        self.whitespace = false;
                        self.indention = false;
                    } else {
                        self.write_chars(&text[start..end]);
                    }
                    start = end;
                }
            } else if breaks {
                if ch.is_none_or(|c| !is_break(c)) {
                    if text[start] == '\n' {
                        self.write_line_break(None);
                    }
                    self.write_breaks(&text[start..end]);
                    self.write_indent();
                    self.whitespace = false;
                    self.indention = false;
                    start = end;
                }
            } else if ch.is_none_or(|c| c == ' ' || is_break(c)) {
                self.write_chars(&text[start..end]);
                start = end;
            }
            if let Some(c) = ch {
                spaces = c == ' ';
                breaks = is_break(c);
            }
            end += 1;
        }
    }
}

/// `yaml.safe_dump(value, sort_keys=False, allow_unicode=True)` of a loaded value.
///
/// Containers reached twice are anchored (`&id001`) and aliased, as PyYAML does for the same
/// object. A date or datetime has no identity here, so one reached twice is written twice where
/// PyYAML would anchor it (`docs/rust-port/differences.md`).
pub fn safe_dump(loaded: &Loaded) -> Result<String, YamlError> {
    let root = loaded.root.clone().unwrap_or(Value::None);
    let mut representer = Representer { loaded, nodes: Vec::new(), represented: HashMap::new() };
    let root_node = representer.represent(&root);
    let mut serializer = Serializer {
        nodes: &representer.nodes,
        anchors: HashMap::new(),
        last_anchor_id: 0,
        serialized: std::collections::HashSet::new(),
        events: vec![
            EventKind::StreamStart,
            EventKind::DocumentStart { explicit: false, version: None, tags: None },
        ],
    };
    serializer.anchor_node(root_node);
    serializer.serialize_node(root_node);
    serializer.events.push(EventKind::DocumentEnd { explicit: false });
    serializer.events.push(EventKind::StreamEnd);
    let events = serializer.events;
    let mut emitter = Emitter {
        events: &events,
        position: 0,
        out: String::new(),
        states: Vec::new(),
        state: State::StreamStart,
        indents: Vec::new(),
        indent: None,
        flow_level: 0,
        root_context: false,
        simple_key_context: false,
        mapping_context: false,
        column: 0,
        whitespace: true,
        indention: true,
        open_ended: false,
        prepared_anchor: None,
        prepared_tag: None,
        analysis: None,
        style: None,
    };
    emitter.run()?;
    Ok(emitter.out)
}
