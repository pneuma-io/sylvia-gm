//! `yaml/nodes.py`, `yaml/composer.py` and `yaml/resolver.py`: events to a node graph with
//! resolved tags.
//!
//! Nodes live in an arena ([`NodeGraph`]) and refer to each other by [`NodeId`]. An alias is the
//! same id as its anchor's node, as in Python, where it is the same object; a graph can therefore
//! contain cycles. The constructor may change nodes (`flatten_mapping` moves merge keys), which
//! is why it takes the graph mutably.

use super::error::{Mark, MarkedError, MarkedKind, YamlError};
use super::parser::{Event, EventKind};
use super::{EventSource, Loader};
use crate::pystr::repr;
use regex::Regex;
use std::sync::OnceLock;

/// An index into a [`NodeGraph`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct NodeId(pub usize);

/// What a node holds (`ScalarNode`, `SequenceNode`, `MappingNode`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NodeKind {
    Scalar(String),
    Sequence(Vec<NodeId>),
    Mapping(Vec<(NodeId, NodeId)>),
}

/// One node.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Node {
    pub tag: String,
    pub kind: NodeKind,
    pub start_mark: Mark,
    pub end_mark: Mark,
    /// A scalar's style as Python holds it: `None` for plain in the pure loader, `""` for plain
    /// in the C loader, else the quote or block indicator.
    pub style: Option<String>,
    /// A collection's `flow_style`.
    pub flow_style: Option<bool>,
}

impl Node {
    /// `node.id`: `"scalar"`, `"sequence"` or `"mapping"`.
    pub fn id(&self) -> &'static str {
        match self.kind {
            NodeKind::Scalar(_) => "scalar",
            NodeKind::Sequence(_) => "sequence",
            NodeKind::Mapping(_) => "mapping",
        }
    }
}

/// The nodes of one document.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct NodeGraph {
    pub nodes: Vec<Node>,
}

impl NodeGraph {
    /// The node behind an id.
    pub fn node(&self, id: NodeId) -> &Node {
        &self.nodes[id.0]
    }

    /// The node behind an id, to change it.
    pub fn node_mut(&mut self, id: NodeId) -> &mut Node {
        &mut self.nodes[id.0]
    }

    fn push(&mut self, node: Node) -> NodeId {
        self.nodes.push(node);
        NodeId(self.nodes.len() - 1)
    }
}

/// A composed document: its graph and root, `None` for an empty stream.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Composed {
    pub graph: NodeGraph,
    pub root: Option<NodeId>,
}

// ── Resolver ──────────────────────────────────────────────────────────────────────────────

pub const STR_TAG: &str = "tag:yaml.org,2002:str";
pub const SEQ_TAG: &str = "tag:yaml.org,2002:seq";
pub const MAP_TAG: &str = "tag:yaml.org,2002:map";

struct ImplicitResolver {
    tag: &'static str,
    pattern: Regex,
    first: &'static str,
}

/// `Resolver`'s implicit resolvers, in the order Python adds them. `first` holds the characters a
/// value must start with; `\0` stands for the empty value.
fn implicit_resolvers() -> &'static [ImplicitResolver] {
    static RESOLVERS: OnceLock<Vec<ImplicitResolver>> = OnceLock::new();
    RESOLVERS.get_or_init(|| {
        // Python's `$` (without MULTILINE) also matches before a final newline.
        let make = |tag, pattern: &str, first| {
            let pattern = format!(r"{}\n?\z", pattern.strip_suffix('$').expect("patterns end with $"));
            ImplicitResolver { tag, pattern: Regex::new(&pattern).expect("static pattern"), first }
        };
        vec![
            make(
                "tag:yaml.org,2002:bool",
                r"^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$",
                "yYnNtTfFoO",
            ),
            make(
                "tag:yaml.org,2002:float",
                r"^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?|\.[0-9][0-9_]*(?:[eE][-+][0-9]+)?|[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$",
                "-+0123456789.",
            ),
            make(
                "tag:yaml.org,2002:int",
                r"^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)|[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$",
                "-+0123456789",
            ),
            make("tag:yaml.org,2002:merge", r"^(?:<<)$", "<"),
            make("tag:yaml.org,2002:null", r"^(?:~|null|Null|NULL|)$", "~nN\0"),
            make(
                "tag:yaml.org,2002:timestamp",
                r"^(?:[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]|[0-9][0-9][0-9][0-9]-[0-9][0-9]?-[0-9][0-9]?(?:[Tt]|[ \t]+)[0-9][0-9]?:[0-9][0-9]:[0-9][0-9](?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9][0-9])?))?)$",
                "0123456789",
            ),
            make("tag:yaml.org,2002:value", r"^(?:=)$", "="),
            make("tag:yaml.org,2002:yaml", r"^(?:!|&|\*)$", "!&*"),
        ]
    })
}

/// `Resolver.resolve(ScalarNode, value, implicit)` for a scalar.
pub fn resolve_scalar(value: &str, implicit: (bool, bool)) -> &'static str {
    if implicit.0 {
        let first = value.chars().next().unwrap_or('\0');
        for resolver in implicit_resolvers() {
            if resolver.first.contains(first) && resolver.pattern.is_match(value) {
                return resolver.tag;
            }
        }
    }
    STR_TAG
}

// ── Composer ──────────────────────────────────────────────────────────────────────────────

pub(crate) struct Composer {
    pub(crate) parser: EventSource,
    loader: Loader,
    graph: NodeGraph,
    anchors: Vec<(String, NodeId)>,
}

fn composer_error(context: Option<String>, context_mark: Option<Mark>, problem: String, problem_mark: Mark) -> YamlError {
    YamlError::Marked(MarkedError::new(MarkedKind::Composer, context, context_mark, problem, Some(problem_mark)))
}

impl Composer {
    pub(crate) fn new(parser: EventSource, loader: Loader) -> Self {
        Self { parser, loader, graph: NodeGraph::default(), anchors: Vec::new() }
    }

    fn get_event(&mut self) -> Result<Event, YamlError> {
        Ok(self.parser.get_event()?.expect("the parser ends with a stream end event"))
    }

    fn peek_kind(&mut self) -> Result<EventKind, YamlError> {
        Ok(self.parser.peek_event()?.expect("the parser ends with a stream end event").kind.clone())
    }

    /// `get_single_node()`.
    pub(crate) fn single_node(mut self) -> Result<Composed, YamlError> {
        self.get_event()?;
        let mut document = None;
        if !matches!(self.peek_kind()?, EventKind::StreamEnd) {
            document = Some(self.compose_document()?);
        }
        if !matches!(self.peek_kind()?, EventKind::StreamEnd) {
            let event = self.get_event()?;
            let first = document.map(|id| self.graph.node(id).start_mark.clone());
            return Err(composer_error(
                Some("expected a single document in the stream".into()),
                first,
                "but found another document".into(),
                event.start_mark,
            ));
        }
        self.get_event()?;
        Ok(Composed { graph: self.graph, root: document })
    }

    fn compose_document(&mut self) -> Result<NodeId, YamlError> {
        self.get_event()?;
        let node = self.compose_node()?;
        self.get_event()?;
        self.anchors.clear();
        Ok(node)
    }

    fn anchor(&self, name: &str) -> Option<NodeId> {
        self.anchors.iter().find(|(existing, _)| existing == name).map(|(_, id)| *id)
    }

    fn compose_node(&mut self) -> Result<NodeId, YamlError> {
        if let EventKind::Alias { anchor } = self.peek_kind()? {
            let event = self.get_event()?;
            return match self.anchor(&anchor) {
                Some(id) => Ok(id),
                None => Err(composer_error(
                    None,
                    None,
                    match self.loader {
                        Loader::Pure => format!("found undefined alias {}", repr(&anchor)),
                        Loader::C => "found undefined alias".into(),
                    },
                    event.start_mark,
                )),
            };
        }
        let anchor = match self.peek_kind()? {
            EventKind::Scalar { anchor, .. } | EventKind::SequenceStart { anchor, .. } | EventKind::MappingStart { anchor, .. } => anchor,
            _ => None,
        };
        if let Some(name) = &anchor {
            if let Some(existing) = self.anchor(name) {
                let first = self.graph.node(existing).start_mark.clone();
                let event_mark = self.parser.peek_event()?.expect("peeked").start_mark.clone();
                return Err(composer_error(
                    Some(match self.loader {
                        Loader::Pure => format!("found duplicate anchor {}; first occurrence", repr(name)),
                        Loader::C => "found duplicate anchor; first occurrence".into(),
                    }),
                    Some(first),
                    "second occurrence".into(),
                    event_mark,
                ));
            }
        }
        match self.peek_kind()? {
            EventKind::Scalar { .. } => self.compose_scalar_node(anchor),
            EventKind::SequenceStart { .. } => self.compose_sequence_node(anchor),
            _ => self.compose_mapping_node(anchor),
        }
    }

    fn remember(&mut self, anchor: Option<String>, id: NodeId) {
        if let Some(anchor) = anchor {
            match self.anchors.iter_mut().find(|(existing, _)| *existing == anchor) {
                Some(slot) => slot.1 = id,
                None => self.anchors.push((anchor, id)),
            }
        }
    }

    fn compose_scalar_node(&mut self, anchor: Option<String>) -> Result<NodeId, YamlError> {
        let event = self.get_event()?;
        let EventKind::Scalar { tag, implicit, value, style, .. } = event.kind else { unreachable!() };
        let tag = match tag {
            Some(tag) if tag != "!" => tag,
            _ => resolve_scalar(&value, implicit).to_owned(),
        };
        let style = match (style, self.loader) {
            (Some(style), _) => Some(style.to_string()),
            (None, Loader::Pure) => None,
            (None, Loader::C) => Some(String::new()),
        };
        let id = self.graph.push(Node {
            tag,
            kind: NodeKind::Scalar(value),
            start_mark: event.start_mark,
            end_mark: event.end_mark,
            style,
            flow_style: None,
        });
        self.remember(anchor, id);
        Ok(id)
    }

    fn compose_sequence_node(&mut self, anchor: Option<String>) -> Result<NodeId, YamlError> {
        let start = self.get_event()?;
        let EventKind::SequenceStart { tag, flow_style, .. } = start.kind else { unreachable!() };
        let tag = match tag {
            Some(tag) if tag != "!" => tag,
            _ => SEQ_TAG.to_owned(),
        };
        let id = self.graph.push(Node {
            tag,
            kind: NodeKind::Sequence(Vec::new()),
            start_mark: start.start_mark.clone(),
            end_mark: start.start_mark,
            style: None,
            flow_style,
        });
        self.remember(anchor, id);
        while !matches!(self.peek_kind()?, EventKind::SequenceEnd) {
            let item = self.compose_node()?;
            if let NodeKind::Sequence(items) = &mut self.graph.node_mut(id).kind {
                items.push(item);
            }
        }
        let end = self.get_event()?;
        self.graph.node_mut(id).end_mark = end.end_mark;
        Ok(id)
    }

    fn compose_mapping_node(&mut self, anchor: Option<String>) -> Result<NodeId, YamlError> {
        let start = self.get_event()?;
        let EventKind::MappingStart { tag, flow_style, .. } = start.kind else { unreachable!() };
        let tag = match tag {
            Some(tag) if tag != "!" => tag,
            _ => MAP_TAG.to_owned(),
        };
        let id = self.graph.push(Node {
            tag,
            kind: NodeKind::Mapping(Vec::new()),
            start_mark: start.start_mark.clone(),
            end_mark: start.start_mark,
            style: None,
            flow_style,
        });
        self.remember(anchor, id);
        while !matches!(self.peek_kind()?, EventKind::MappingEnd) {
            let key = self.compose_node()?;
            let value = self.compose_node()?;
            if let NodeKind::Mapping(pairs) = &mut self.graph.node_mut(id).kind {
                pairs.push((key, value));
            }
        }
        let end = self.get_event()?;
        self.graph.node_mut(id).end_mark = end.end_mark;
        Ok(id)
    }
}
