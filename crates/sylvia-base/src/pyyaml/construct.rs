//! `yaml/constructor.py`: `BaseConstructor` and `SafeConstructor`, nodes to Python values.
//!
//! Python builds containers in two steps: the empty list, dict or set is created and recorded
//! first, and filled later (a generator resumed after the rest of the document, or at once for a
//! "deep" construction). That order decides which object an alias gets and which error is raised
//! first, so it is kept: [`Constructor`] queues the same fills and runs them in Python's order.
//!
//! The loader subclasses Sylvia defines override `construct_document`, `flatten_mapping` or
//! `construct_mapping`. [`Hooks`] holds those overrides as functions; each receives the
//! constructor and can call the base behaviour (`base_*`) as Python calls `super()`.

use super::compose::{Node, NodeGraph, NodeId, NodeKind, STR_TAG};
use super::error::{Mark, MarkedError, MarkedKind, YamlError};
use super::value::{fresh_float, key_eq, Date, DateTime, Loaded, Object, ObjectId, Value, SHARED_NAN};
use crate::pystr::{self, repr, IntParseError};
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

type ConstructResult<T> = Result<T, YamlError>;

/// Overrides of the `SafeConstructor` methods a Python loader subclass replaces. `None` keeps
/// the base behaviour.
#[derive(Clone, Copy, Default)]
pub struct Hooks {
    /// `construct_document(node)`. The bounded loaders check the node graph first, then call
    /// [`Constructor::base_construct_document`].
    pub construct_document: Option<fn(&mut Constructor, NodeId) -> ConstructResult<Option<Value>>>,
    /// `flatten_mapping(node)`. Call [`Constructor::base_flatten_mapping`] for `super()`.
    pub flatten_mapping: Option<fn(&mut Constructor, NodeId) -> ConstructResult<()>>,
    /// `construct_mapping(node, deep)`, returning the mapping's pairs in order (Python's dict
    /// with later duplicate keys replacing earlier values). Call
    /// [`Constructor::base_construct_mapping`] for `super()`.
    pub construct_mapping: Option<fn(&mut Constructor, NodeId, bool) -> ConstructResult<Vec<(Value, Value)>>>,
}

#[derive(Debug, Clone, Copy)]
enum Fill {
    Seq(NodeId, ObjectId),
    Map(NodeId, ObjectId),
    Set(NodeId, ObjectId),
    Omap(NodeId, ObjectId),
    Pairs(NodeId, ObjectId),
}

/// One document's construction (`SafeConstructor` state).
pub struct Constructor<'g> {
    /// The composed graph; `flatten_mapping` changes it, as Python's does.
    pub graph: &'g mut NodeGraph,
    objects: Vec<Object>,
    constructed: HashMap<NodeId, Value>,
    recursive: HashSet<NodeId>,
    pending: Vec<Fill>,
    deep: bool,
    hooks: Hooks,
    /// Per-node flags a hook can use, as Python hooks set attributes on nodes (for example
    /// `_unique_keys_checked`).
    pub flagged: HashSet<NodeId>,
}

fn constructor_error(context: Option<&str>, context_mark: Option<Mark>, problem: String, problem_mark: Mark) -> YamlError {
    YamlError::Marked(MarkedError::new(MarkedKind::Constructor, context.map(str::to_owned), context_mark, problem, Some(problem_mark)))
}

impl<'g> Constructor<'g> {
    /// A constructor over a composed graph.
    pub fn new(graph: &'g mut NodeGraph, hooks: Hooks) -> Self {
        Self {
            graph,
            objects: Vec::new(),
            constructed: HashMap::new(),
            recursive: HashSet::new(),
            pending: Vec::new(),
            deep: false,
            hooks,
            flagged: HashSet::new(),
        }
    }

    /// The containers built so far.
    pub fn into_objects(self) -> Vec<Object> {
        self.objects
    }

    /// A container built so far.
    pub fn object(&self, id: ObjectId) -> &Object {
        &self.objects[id.0]
    }

    fn node(&self, id: NodeId) -> &Node {
        self.graph.node(id)
    }

    /// `construct_document(node)`, through the hook when one is set.
    pub fn construct_document(&mut self, root: NodeId) -> ConstructResult<Option<Value>> {
        match self.hooks.construct_document {
            Some(hook) => hook(self, root),
            None => self.base_construct_document(root),
        }
    }

    /// `BaseConstructor.construct_document`.
    pub fn base_construct_document(&mut self, root: NodeId) -> ConstructResult<Option<Value>> {
        let data = self.construct_object(root, false)?;
        while !self.pending.is_empty() {
            let fills = std::mem::take(&mut self.pending);
            for fill in fills {
                self.run_fill(fill)?;
            }
        }
        self.constructed.clear();
        self.recursive.clear();
        self.deep = false;
        Ok(Some(data))
    }

    /// `construct_object(node, deep)`.
    pub fn construct_object(&mut self, node: NodeId, deep: bool) -> ConstructResult<Value> {
        if let Some(value) = self.constructed.get(&node) {
            return Ok(value.clone());
        }
        let old_deep = self.deep;
        if deep {
            self.deep = true;
        }
        if self.recursive.contains(&node) {
            return Err(constructor_error(None, None, "found unconstructable recursive node".into(), self.node(node).start_mark.clone()));
        }
        self.recursive.insert(node);
        let tag = self.node(node).tag.clone();
        let data = match tag.as_str() {
            "tag:yaml.org,2002:null" => {
                self.construct_scalar(node)?;
                Value::None
            }
            "tag:yaml.org,2002:bool" => self.construct_yaml_bool(node)?,
            "tag:yaml.org,2002:int" => self.construct_yaml_int(node)?,
            "tag:yaml.org,2002:float" => self.construct_yaml_float(node)?,
            "tag:yaml.org,2002:binary" => self.construct_yaml_binary(node)?,
            "tag:yaml.org,2002:timestamp" => self.construct_yaml_timestamp(node)?,
            "tag:yaml.org,2002:str" => Value::Str(self.construct_scalar(node)?),
            "tag:yaml.org,2002:omap" => self.start_fill(node, Object::List(Vec::new()), Fill::Omap)?,
            "tag:yaml.org,2002:pairs" => self.start_fill(node, Object::List(Vec::new()), Fill::Pairs)?,
            "tag:yaml.org,2002:set" => self.start_fill(node, Object::Set(Vec::new()), Fill::Set)?,
            "tag:yaml.org,2002:seq" => self.start_fill(node, Object::List(Vec::new()), Fill::Seq)?,
            "tag:yaml.org,2002:map" => self.start_fill(node, Object::Dict(Vec::new()), Fill::Map)?,
            other => {
                return Err(constructor_error(
                    None,
                    None,
                    format!("could not determine a constructor for the tag {}", repr(other)),
                    self.node(node).start_mark.clone(),
                ))
            }
        };
        self.constructed.insert(node, data.clone());
        self.recursive.remove(&node);
        if deep {
            self.deep = old_deep;
        }
        Ok(data)
    }

    /// The first step of a generator constructor: create the empty container, then fill it now
    /// (deep) or later.
    fn start_fill(&mut self, node: NodeId, empty: Object, fill: fn(NodeId, ObjectId) -> Fill) -> ConstructResult<Value> {
        let id = ObjectId(self.objects.len());
        let value = match &empty {
            Object::Set(_) => Value::Set(id),
            Object::Dict(_) => Value::Dict(id),
            _ => Value::List(id),
        };
        self.objects.push(empty);
        let task = fill(node, id);
        if self.deep {
            // Python resumes the generator at once, before `constructed_objects` records the
            // container, so an alias back to it is still a recursive node.
            self.run_fill(task)?;
        } else {
            self.pending.push(task);
        }
        Ok(value)
    }

    fn run_fill(&mut self, fill: Fill) -> ConstructResult<()> {
        match fill {
            Fill::Seq(node, id) => {
                let items = self.construct_sequence(node, false)?;
                self.objects[id.0] = Object::List(items);
            }
            Fill::Map(node, id) => {
                let pairs = self.construct_mapping(node, false)?;
                self.objects[id.0] = Object::Dict(pairs);
            }
            Fill::Set(node, id) => {
                let pairs = self.construct_mapping(node, false)?;
                self.objects[id.0] = Object::Set(pairs.into_iter().map(|(key, _)| key).collect());
            }
            Fill::Omap(node, id) | Fill::Pairs(node, id) => {
                let context = if matches!(fill, Fill::Omap(..)) { "while constructing an ordered map" } else { "while constructing pairs" };
                let start = self.node(node).start_mark.clone();
                let NodeKind::Sequence(items) = self.node(node).kind.clone() else {
                    return Err(constructor_error(
                        Some(context),
                        Some(start.clone()),
                        format!("expected a sequence, but found {}", self.node(node).id()),
                        start,
                    ));
                };
                let mut entries = Vec::new();
                for item in items {
                    let item_node = self.node(item);
                    let NodeKind::Mapping(pairs) = item_node.kind.clone() else {
                        return Err(constructor_error(
                            Some(context),
                            Some(start),
                            format!("expected a mapping of length 1, but found {}", item_node.id()),
                            item_node.start_mark.clone(),
                        ));
                    };
                    if pairs.len() != 1 {
                        return Err(constructor_error(
                            Some(context),
                            Some(start),
                            format!("expected a single mapping item, but found {} items", pairs.len()),
                            item_node.start_mark.clone(),
                        ));
                    }
                    let key = self.construct_object(pairs[0].0, false)?;
                    let value = self.construct_object(pairs[0].1, false)?;
                    entries.push(Value::Tuple(vec![key, value]));
                    // Python appends as it goes; keep the partial list visible to aliases.
                    self.objects[id.0] = Object::List(entries.clone());
                }
                self.objects[id.0] = Object::List(entries);
            }
        }
        Ok(())
    }

    /// `SafeConstructor.construct_scalar`: a scalar's text, or a mapping's `=` value.
    pub fn construct_scalar(&mut self, node: NodeId) -> ConstructResult<String> {
        if let NodeKind::Mapping(pairs) = self.node(node).kind.clone() {
            for (key, value) in pairs {
                if self.node(key).tag == "tag:yaml.org,2002:value" {
                    return self.construct_scalar(value);
                }
            }
        }
        match &self.node(node).kind {
            NodeKind::Scalar(value) => Ok(value.clone()),
            _ => Err(constructor_error(
                None,
                None,
                format!("expected a scalar node, but found {}", self.node(node).id()),
                self.node(node).start_mark.clone(),
            )),
        }
    }

    /// `BaseConstructor.construct_sequence`.
    pub fn construct_sequence(&mut self, node: NodeId, deep: bool) -> ConstructResult<Vec<Value>> {
        let NodeKind::Sequence(items) = self.node(node).kind.clone() else {
            return Err(constructor_error(
                None,
                None,
                format!("expected a sequence node, but found {}", self.node(node).id()),
                self.node(node).start_mark.clone(),
            ));
        };
        items.into_iter().map(|item| self.construct_object(item, deep)).collect()
    }

    /// `construct_mapping(node, deep)`, through the hook when one is set.
    pub fn construct_mapping(&mut self, node: NodeId, deep: bool) -> ConstructResult<Vec<(Value, Value)>> {
        match self.hooks.construct_mapping {
            Some(hook) => hook(self, node, deep),
            None => self.base_construct_mapping(node, deep),
        }
    }

    /// `SafeConstructor.construct_mapping`: flatten merges, then build the dict.
    pub fn base_construct_mapping(&mut self, node: NodeId, deep: bool) -> ConstructResult<Vec<(Value, Value)>> {
        if matches!(self.node(node).kind, NodeKind::Mapping(_)) {
            self.flatten_mapping(node)?;
        }
        let NodeKind::Mapping(pairs) = self.node(node).kind.clone() else {
            return Err(constructor_error(
                None,
                None,
                format!("expected a mapping node, but found {}", self.node(node).id()),
                self.node(node).start_mark.clone(),
            ));
        };
        let mut mapping: Vec<(Value, Value)> = Vec::new();
        for (key_node, value_node) in pairs {
            let key = self.construct_object(key_node, deep)?;
            if !key.hashable() {
                return Err(constructor_error(
                    Some("while constructing a mapping"),
                    Some(self.node(node).start_mark.clone()),
                    "found unhashable key".into(),
                    self.node(key_node).start_mark.clone(),
                ));
            }
            let value = self.construct_object(value_node, deep)?;
            match mapping.iter_mut().find(|(existing, _)| key_eq(existing, &key)) {
                Some(slot) => slot.1 = value,
                None => mapping.push((key, value)),
            }
        }
        Ok(mapping)
    }

    /// `BaseConstructor.construct_pairs`: every pair in order, duplicates kept.
    pub fn construct_pairs(&mut self, node: NodeId, deep: bool) -> ConstructResult<Vec<(Value, Value)>> {
        let NodeKind::Mapping(pairs) = self.node(node).kind.clone() else {
            return Err(constructor_error(
                None,
                None,
                format!("expected a mapping node, but found {}", self.node(node).id()),
                self.node(node).start_mark.clone(),
            ));
        };
        let mut out = Vec::new();
        for (key_node, value_node) in pairs {
            let key = self.construct_object(key_node, deep)?;
            let value = self.construct_object(value_node, deep)?;
            out.push((key, value));
        }
        Ok(out)
    }

    /// `flatten_mapping(node)`, through the hook when one is set.
    pub fn flatten_mapping(&mut self, node: NodeId) -> ConstructResult<()> {
        match self.hooks.flatten_mapping {
            Some(hook) => hook(self, node),
            None => self.base_flatten_mapping(node),
        }
    }

    /// `SafeConstructor.flatten_mapping`: move `<<` merges in front of the mapping's own keys.
    pub fn base_flatten_mapping(&mut self, node: NodeId) -> ConstructResult<()> {
        // A hook can pass any node; Python then unpacks the first item of `node.value`.
        match &self.node(node).kind {
            NodeKind::Scalar(text) if !text.is_empty() => {
                // `node.value[0]` is the first character, a one-character str.
                return Err(YamlError::python("ValueError", "not enough values to unpack (expected 2, got 1)"));
            }
            NodeKind::Sequence(items) if !items.is_empty() => {
                let class = match self.node(items[0]).kind {
                    NodeKind::Scalar(_) => "ScalarNode",
                    NodeKind::Sequence(_) => "SequenceNode",
                    NodeKind::Mapping(_) => "MappingNode",
                };
                return Err(YamlError::python("TypeError", format!("cannot unpack non-iterable {class} object")));
            }
            NodeKind::Scalar(_) | NodeKind::Sequence(_) => return Ok(()),
            NodeKind::Mapping(_) => {}
        }
        let mut merge: Vec<(NodeId, NodeId)> = Vec::new();
        let mut index = 0;
        loop {
            let NodeKind::Mapping(pairs) = &self.node(node).kind else { break };
            if index >= pairs.len() {
                break;
            }
            let (key_node, value_node) = pairs[index];
            let key_tag = self.node(key_node).tag.clone();
            if key_tag == "tag:yaml.org,2002:merge" {
                if let NodeKind::Mapping(pairs) = &mut self.graph.node_mut(node).kind {
                    pairs.remove(index);
                }
                match self.node(value_node).kind.clone() {
                    NodeKind::Mapping(_) => {
                        self.flatten_mapping(value_node)?;
                        if let NodeKind::Mapping(pairs) = &self.node(value_node).kind {
                            merge.extend(pairs.iter().copied());
                        }
                    }
                    NodeKind::Sequence(subnodes) => {
                        let mut submerge = Vec::new();
                        for subnode in subnodes {
                            if !matches!(self.node(subnode).kind, NodeKind::Mapping(_)) {
                                return Err(constructor_error(
                                    Some("while constructing a mapping"),
                                    Some(self.node(node).start_mark.clone()),
                                    format!("expected a mapping for merging, but found {}", self.node(subnode).id()),
                                    self.node(subnode).start_mark.clone(),
                                ));
                            }
                            self.flatten_mapping(subnode)?;
                            if let NodeKind::Mapping(pairs) = &self.node(subnode).kind {
                                submerge.push(pairs.clone());
                            }
                        }
                        submerge.reverse();
                        for pairs in submerge {
                            merge.extend(pairs);
                        }
                    }
                    NodeKind::Scalar(_) => {
                        return Err(constructor_error(
                            Some("while constructing a mapping"),
                            Some(self.node(node).start_mark.clone()),
                            format!(
                                "expected a mapping or list of mappings for merging, but found {}",
                                self.node(value_node).id()
                            ),
                            self.node(value_node).start_mark.clone(),
                        ))
                    }
                }
            } else if key_tag == "tag:yaml.org,2002:value" {
                self.graph.node_mut(key_node).tag = STR_TAG.to_owned();
                index += 1;
            } else {
                index += 1;
            }
        }
        if !merge.is_empty() {
            if let NodeKind::Mapping(pairs) = &mut self.graph.node_mut(node).kind {
                merge.append(pairs);
                *pairs = merge;
            }
        }
        Ok(())
    }

    fn construct_yaml_bool(&mut self, node: NodeId) -> ConstructResult<Value> {
        let value = self.construct_scalar(node)?;
        match value.to_lowercase().as_str() {
            "yes" | "true" | "on" => Ok(Value::Bool(true)),
            "no" | "false" | "off" => Ok(Value::Bool(false)),
            other => Err(YamlError::python("KeyError", repr(other))),
        }
    }

    fn construct_yaml_int(&mut self, node: NodeId) -> ConstructResult<Value> {
        let text = self.construct_scalar(node)?.replace('_', "");
        let index_error = || YamlError::python("IndexError", "string index out of range");
        let mut chars = text.chars();
        let first = chars.next().ok_or_else(index_error)?;
        let negative = first == '-';
        let value: String = if first == '+' || first == '-' { chars.collect() } else { text.clone() };
        if value == "0" {
            return Ok(Value::Int(0));
        }
        let leading = value.chars().next().ok_or_else(index_error)?;
        // Magnitudes are computed in i128 so that -2**63 fits; the result must fit i64.
        let magnitude: i128 = if let Some(rest) = value.strip_prefix("0b") {
            int_with_base(rest, 2)?
        } else if let Some(rest) = value.strip_prefix("0x") {
            int_with_base(rest, 16)?
        } else if leading == '0' {
            int_with_base(&value, 8)?
        } else if value.contains(':') {
            let mut digits = Vec::new();
            for part in value.split(':') {
                digits.push(int_with_base(part, 10)?);
            }
            digits.reverse();
            let (mut base, mut total) = (1i128, 0i128);
            for (place, digit) in digits.into_iter().enumerate() {
                if place > 0 {
                    base = base.checked_mul(60).ok_or_else(int_refusal)?;
                }
                total = digit.checked_mul(base).and_then(|term| total.checked_add(term)).ok_or_else(int_refusal)?;
            }
            total
        } else {
            int_with_base(&value, 10)?
        };
        let signed = if negative { magnitude.checked_neg().ok_or_else(int_refusal)? } else { magnitude };
        i64::try_from(signed).map(Value::Int).map_err(|_| int_refusal())
    }

    fn construct_yaml_float(&mut self, node: NodeId) -> ConstructResult<Value> {
        let text = self.construct_scalar(node)?.replace('_', "").to_lowercase();
        let mut chars = text.chars();
        let first = chars.next().ok_or_else(|| YamlError::python("IndexError", "string index out of range"))?;
        let sign = if first == '-' { -1.0 } else { 1.0 };
        let value: String = if first == '+' || first == '-' { chars.collect() } else { text.clone() };
        if value == ".inf" {
            return Ok(Value::Float(sign * f64::INFINITY));
        }
        if value == ".nan" {
            // SafeConstructor.nan_value: one shared object, so `.nan` keys are equal.
            return Ok(Value::Float(SHARED_NAN));
        }
        let float = |part: &str| pystr::parse_float(part).map_err(|error| YamlError::python("ValueError", error.to_string()));
        if value.contains(':') {
            let mut digits = Vec::new();
            for part in value.split(':') {
                digits.push(float(part)?);
            }
            digits.reverse();
            // Python's base stays an exact int, converted to float for each product.
            let (mut exact, mut base, mut total) = (Some(1u128), 1.0f64, 0.0f64);
            for digit in digits {
                total += digit * base;
                exact = exact.and_then(|b| b.checked_mul(60));
                base = exact.map_or(base * 60.0, |b| b as f64);
            }
            return Ok(Value::Float(fresh_float(sign * total)));
        }
        Ok(Value::Float(fresh_float(sign * float(&value)?)))
    }

    fn construct_yaml_binary(&mut self, node: NodeId) -> ConstructResult<Value> {
        let text = self.construct_scalar(node)?;
        let mark = self.node(node).start_mark.clone();
        let chars: Vec<char> = text.chars().collect();
        if let Some(start) = chars.iter().position(|c| !c.is_ascii()) {
            // UnicodeEncodeError covers the whole run of characters the codec cannot encode.
            let end = chars[start..].iter().position(char::is_ascii).map_or(chars.len(), |n| start + n);
            let what = if end - start == 1 {
                let code = u32::from(chars[start]);
                let escaped = match code {
                    0..=0xFF => format!("\\x{code:02x}"),
                    0x100..=0xFFFF => format!("\\u{code:04x}"),
                    _ => format!("\\U{code:08x}"),
                };
                format!("character '{escaped}' in position {start}")
            } else {
                format!("characters in position {start}-{}", end - 1)
            };
            return Err(constructor_error(
                None,
                None,
                format!("failed to convert base64 data into ascii: 'ascii' codec can't encode {what}: ordinal not in range(128)"),
                mark,
            ));
        }
        a2b_base64(text.as_bytes())
            .map(Value::Bytes)
            .map_err(|reason| constructor_error(None, None, format!("failed to decode base64 data: {reason}"), mark))
    }

    fn construct_yaml_timestamp(&mut self, node: NodeId) -> ConstructResult<Value> {
        self.construct_scalar(node)?;
        // Python matches node.value, which for a mapping node is the list of pairs: re.match then
        // raises TypeError. A scalar that does not match makes groupdict fail on None.
        let NodeKind::Scalar(text) = self.node(node).kind.clone() else {
            return Err(YamlError::python("TypeError", "expected string or bytes-like object, got 'list'"));
        };
        let Some(captures) = timestamp_regexp().captures(&text) else {
            return Err(YamlError::python("AttributeError", "'NoneType' object has no attribute 'groupdict'"));
        };
        let number = |name: &str| captures.name(name).map(|m| m.as_str().parse::<i64>().unwrap_or(0));
        let year = number("year").unwrap_or(0);
        let month = number("month").unwrap_or(0);
        let day = number("day").unwrap_or(0);
        let Some(hour) = captures.name("hour").filter(|m| !m.as_str().is_empty()) else {
            return Ok(Value::Date(check_date(year, month, day)?));
        };
        let hour: i64 = hour.as_str().parse().unwrap_or(0);
        let minute = number("minute").unwrap_or(0);
        let second = number("second").unwrap_or(0);
        let mut microsecond = 0;
        if let Some(fraction) = captures.name("fraction").map(|m| m.as_str()).filter(|f| !f.is_empty()) {
            let mut digits: String = fraction.chars().take(6).collect();
            while digits.chars().count() < 6 {
                digits.push('0');
            }
            microsecond = digits.parse::<u32>().unwrap_or(0);
        }
        let mut offset_seconds = None;
        if let Some(sign) = captures.name("tz_sign") {
            let tz_hour = number("tz_hour").unwrap_or(0);
            let tz_minute = number("tz_minute").unwrap_or(0);
            let mut delta = tz_hour * 3600 + tz_minute * 60;
            if sign.as_str() == "-" {
                delta = -delta;
            }
            if delta.abs() >= 86_400 {
                return Err(YamlError::python(
                    "ValueError",
                    format!(
                        "offset must be a timedelta strictly between -timedelta(hours=24) and timedelta(hours=24), not {}.",
                        timedelta_repr(delta)
                    ),
                ));
            }
            offset_seconds = Some(delta as i32);
        } else if captures.name("tz").is_some() {
            offset_seconds = Some(0);
        }
        // datetime() checks the date, then the time; the timezone was built before it.
        let date = check_date(year, month, day)?;
        if !(0..=23).contains(&hour) {
            return Err(YamlError::python("ValueError", "hour must be in 0..23"));
        }
        if !(0..=59).contains(&minute) {
            return Err(YamlError::python("ValueError", "minute must be in 0..59"));
        }
        if !(0..=59).contains(&second) {
            return Err(YamlError::python("ValueError", "second must be in 0..59"));
        }
        Ok(Value::DateTime(DateTime {
            date,
            hour: hour as u32,
            minute: minute as u32,
            second: second as u32,
            microsecond,
            offset_seconds,
        }))
    }
}

/// `repr(datetime.timedelta(seconds=delta))`.
fn timedelta_repr(seconds: i64) -> String {
    let days = seconds.div_euclid(86_400);
    let rest = seconds.rem_euclid(86_400);
    match (days, rest) {
        (0, 0) => "datetime.timedelta(0)".into(),
        (0, rest) => format!("datetime.timedelta(seconds={rest})"),
        (days, 0) => format!("datetime.timedelta(days={days})"),
        (days, rest) => format!("datetime.timedelta(days={days}, seconds={rest})"),
    }
}

fn check_date(year: i64, month: i64, day: i64) -> ConstructResult<Date> {
    if !(1..=9999).contains(&year) {
        return Err(YamlError::python("ValueError", format!("year {year} is out of range")));
    }
    if !(1..=12).contains(&month) {
        return Err(YamlError::python("ValueError", "month must be in 1..12"));
    }
    let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
    let days = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ if leap => 29,
        _ => 28,
    };
    if !(1..=days).contains(&day) {
        return Err(YamlError::python("ValueError", "day is out of range for month"));
    }
    Ok(Date { year: year as i32, month: month as u32, day: day as u32 })
}

fn timestamp_regexp() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| {
        Regex::new(
            r"^(?P<year>[0-9][0-9][0-9][0-9])-(?P<month>[0-9][0-9]?)-(?P<day>[0-9][0-9]?)(?:(?:[Tt]|[ \t]+)(?P<hour>[0-9][0-9]?):(?P<minute>[0-9][0-9]):(?P<second>[0-9][0-9])(?:\.(?P<fraction>[0-9]*))?(?:[ \t]*(?P<tz>Z|(?P<tz_sign>[-+])(?P<tz_hour>[0-9][0-9]?)(?::(?P<tz_minute>[0-9][0-9]))?))?)?\n?\z",
        )
        .expect("static pattern")
    })
}

fn int_refusal() -> YamlError {
    YamlError::python("ValueError", pystr::INT_RANGE_MESSAGE)
}

/// `int(text, base)` as CPython parses a `str`: other scripts' decimal digits and whitespace
/// become ASCII first, then surrounding whitespace, one sign, the base's own prefix (`0b`, `0o`,
/// `0x`, with one underscore after it), and single underscores between digits. A result beyond
/// the signed 64-bit range is refused (`differences.md` row 5), with room for `-2**63`.
fn int_with_base(text: &str, base: u32) -> ConstructResult<i128> {
    if base == 10 {
        return match pystr::parse_int(text) {
            Ok(value) => Ok(i128::from(value)),
            // 2**63 itself, which only fits once the caller negates it.
            Err(IntParseError::OutOfRange { negative: false }) if text.starts_with(|c: char| c.is_ascii_digit()) => {
                match pystr::parse_int(&format!("-{text}")) {
                    Ok(value) => Ok(-i128::from(value)),
                    Err(_) => Err(int_refusal()),
                }
            }
            Err(IntParseError::OutOfRange { .. }) => Err(int_refusal()),
            Err(IntParseError::Invalid(error)) => Err(YamlError::python("ValueError", error.to_string())),
        };
    }
    let invalid = || YamlError::python("ValueError", format!("invalid literal for int() with base {base}: {}", pystr::repr_200(text)));
    let ascii: String = text
        .chars()
        .map(|c| {
            if c.is_ascii() {
                c
            } else if pystr::is_space(c) {
                ' '
            } else if let Some(value) = pystr::decimal(c) {
                char::from_digit(value, 10).unwrap_or('?')
            } else {
                '?'
            }
        })
        .collect();
    let trimmed = ascii.trim_matches(|c| matches!(c, ' ' | '\t' | '\n' | '\x0b' | '\x0c' | '\r'));
    let (negative, rest) = match trimmed.strip_prefix('-') {
        Some(rest) => (true, rest),
        None => (false, trimmed.strip_prefix('+').unwrap_or(trimmed)),
    };
    let prefix = match base {
        2 => ["0b", "0B"],
        8 => ["0o", "0O"],
        _ => ["0x", "0X"],
    };
    let digits = match prefix.iter().find_map(|p| rest.strip_prefix(p)) {
        Some(digits) => digits.strip_prefix('_').unwrap_or(digits),
        None => rest,
    };
    if digits.is_empty() || digits.starts_with('_') || digits.ends_with('_') || digits.contains("__") {
        return Err(invalid());
    }
    let mut value: i128 = 0;
    for character in digits.chars().filter(|c| *c != '_') {
        let digit = character.to_digit(base).ok_or_else(invalid)?;
        value = value.checked_mul(i128::from(base)).and_then(|v| v.checked_add(i128::from(digit))).ok_or_else(int_refusal)?;
    }
    Ok(if negative { -value } else { value })
}

/// `binascii.a2b_base64(data)` without strict mode, as `base64.decodebytes` calls it: characters
/// outside the alphabet are skipped, and decoding stops at the padding.
fn a2b_base64(data: &[u8]) -> Result<Vec<u8>, String> {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = Vec::new();
    let mut quad_pos = 0;
    let mut left_char: u32 = 0;
    let mut pads = 0;
    let mut data_chars = 0usize;
    for &byte in data {
        if byte == b'=' {
            if quad_pos >= 2 {
                pads += 1;
                if quad_pos + pads >= 4 {
                    return Ok(out);
                }
            }
            continue;
        }
        let Some(value) = ALPHABET.iter().position(|c| *c == byte) else { continue };
        let value = value as u32;
        pads = 0;
        data_chars += 1;
        match quad_pos {
            0 => {
                quad_pos = 1;
                left_char = value;
            }
            1 => {
                quad_pos = 2;
                out.push(((left_char << 2) | (value >> 4)) as u8);
                left_char = value & 0x0F;
            }
            2 => {
                quad_pos = 3;
                out.push(((left_char << 4) | (value >> 2)) as u8);
                left_char = value & 0x03;
            }
            _ => {
                quad_pos = 0;
                out.push(((left_char << 6) | value) as u8);
                left_char = 0;
            }
        }
    }
    match quad_pos {
        0 => Ok(out),
        1 => Err(format!(
            "Invalid base64-encoded string: number of data characters ({data_chars}) cannot be 1 more than a multiple of 4"
        )),
        _ => Err("Incorrect padding".into()),
    }
}

/// Construct a composed document with the given hooks.
pub fn construct(composed: &mut super::compose::Composed, hooks: Hooks) -> ConstructResult<Loaded> {
    let Some(root) = composed.root else { return Ok(Loaded::default()) };
    let mut constructor = Constructor::new(&mut composed.graph, hooks);
    let root_value = constructor.construct_document(root)?;
    Ok(Loaded { root: root_value, objects: constructor.into_objects() })
}
