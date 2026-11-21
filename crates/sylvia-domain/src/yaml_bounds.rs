//! `application/_yaml_bounds.py`: check YAML's composed graph before aliases or merges can
//! expand into values.
//!
//! Callers run [`validate_yaml_graph`] from a `construct_document` hook
//! (`sylvia_base::pyyaml::Hooks`), as Python's bounded loaders do in their
//! `construct_document` override, then call `Constructor::base_construct_document`.
//! A [`YamlShapeError`] converts into `YamlError::Python { class: "YamlShapeError", .. }`, so a
//! caller can tell it apart by class as Python's `except YamlShapeError` does.

use std::collections::{HashMap, HashSet};
use sylvia_base::pyyaml::{NodeGraph, NodeId, NodeKind, YamlError};

/// `YamlShapeError`: a source graph exceeds its caller's size or nesting limits (a
/// `ValueError` subclass in Python).
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{0}")]
pub struct YamlShapeError(pub &'static str);

impl From<YamlShapeError> for YamlError {
    fn from(error: YamlShapeError) -> Self {
        YamlError::Python { class: "YamlShapeError", message: error.0.to_owned() }
    }
}

/// The limits of one `validate_yaml_graph` call.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct YamlGraphLimits {
    pub maximum_depth: usize,
    pub maximum_nodes: usize,
    pub maximum_object_members: Option<usize>,
    /// `count_mapping_keys`: count each key as a node (the default). When false, only the
    /// mapping and its values count, and every key must be a text, merge or value scalar.
    pub count_mapping_keys: bool,
}

const KEY_TAGS: [&str; 3] = ["tag:yaml.org,2002:str", "tag:yaml.org,2002:merge", "tag:yaml.org,2002:value"];

const TOO_DEEP: YamlShapeError = YamlShapeError("YAML nesting exceeds the supported limit");

struct Measure<'g> {
    graph: &'g NodeGraph,
    limits: YamlGraphLimits,
    measured: HashMap<NodeId, (usize, usize)>,
    active: HashSet<NodeId>,
}

impl Measure<'_> {
    /// `measure(node, depth)`: the expanded node count and height, each shared node measured once.
    fn measure(&mut self, node: NodeId, depth: usize) -> Result<(usize, usize), YamlShapeError> {
        let limits = self.limits;
        if depth > limits.maximum_depth {
            return Err(TOO_DEEP);
        }
        if self.active.contains(&node) {
            return Err(YamlShapeError("YAML contains a recursive alias"));
        }
        if let Some(&(count, height)) = self.measured.get(&node) {
            if depth + height - 1 > limits.maximum_depth {
                return Err(TOO_DEEP);
            }
            return Ok((count, height));
        }
        let graph = self.graph;
        let children: Vec<NodeId> = match &graph.node(node).kind {
            NodeKind::Mapping(pairs) => {
                if limits.maximum_object_members.is_some_and(|maximum| pairs.len() > maximum) {
                    return Err(YamlShapeError("YAML object exceeds the supported member limit"));
                }
                if limits.count_mapping_keys {
                    pairs.iter().flat_map(|&(key, value)| [key, value]).collect()
                } else {
                    // Runtime's decoded-object limit counts each mapping and value. Key type and
                    // depth are still checked before construction and alias expansion.
                    for &(key, _) in pairs {
                        let key_node = graph.node(key);
                        if !matches!(key_node.kind, NodeKind::Scalar(_))
                            || !KEY_TAGS.contains(&key_node.tag.as_str())
                        {
                            return Err(YamlShapeError("YAML object keys must be text"));
                        }
                        if depth + 1 > limits.maximum_depth {
                            return Err(TOO_DEEP);
                        }
                    }
                    pairs.iter().map(|&(_, value)| value).collect()
                }
            }
            NodeKind::Sequence(items) => items.clone(),
            NodeKind::Scalar(_) => Vec::new(),
        };
        self.active.insert(node);
        let (mut count, mut height) = (1usize, 1usize);
        for child in children {
            let (child_count, child_height) = self.measure(child, depth + 1)?;
            count += child_count;
            height = height.max(child_height + 1);
            if count > limits.maximum_nodes {
                return Err(YamlShapeError("YAML expansion exceeds the supported node limit"));
            }
        }
        self.active.remove(&node);
        self.measured.insert(node, (count, height));
        Ok((count, height))
    }
}

/// `validate_yaml_graph(root, maximum_depth=..., ...)`: count expanded alias occurrences without
/// allocating or walking the expanded tree. Recursion is bounded by `maximum_depth`.
pub fn validate_yaml_graph(
    graph: &NodeGraph,
    root: NodeId,
    limits: YamlGraphLimits,
) -> Result<(), YamlShapeError> {
    let mut measure = Measure { graph, limits, measured: HashMap::new(), active: HashSet::new() };
    measure.measure(root, 1).map(drop)
}
