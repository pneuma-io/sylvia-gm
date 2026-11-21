//! `parse_content_json_bytes` and `parse_canonical_json_bytes`: strict JSON readers for
//! package and content files.
//!
//! Python reads with `json.loads(object_pairs_hook=<refuse duplicates>,
//! parse_constant=<refuse NaN/Infinity>)`, optionally bounds the tree's shape, then captures
//! it. Every failure inside becomes one message, `"{label} is not strict JSON"`.
//!
//! Here the value comes from `sylvia_base::pyjson::loads` (Python's integer rules) and a second
//! pass over the same text refuses a repeated member name at any depth, because
//! `serde_json::Value` silently keeps the last value of a duplicate.

use crate::error::{DomainError, DomainResult};
use serde::de::{self, Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use std::collections::HashSet;
use sylvia_base::{pyjson, Json};

/// The optional shape budgets of one strict read. Python requires depth and nodes together
/// whenever any budget is given.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct JsonShapeLimits {
    pub maximum_depth: Option<usize>,
    pub maximum_nodes: Option<usize>,
    pub maximum_object_members: Option<usize>,
}

impl JsonShapeLimits {
    /// No budgets: the plain strict read.
    pub const NONE: JsonShapeLimits =
        JsonShapeLimits { maximum_depth: None, maximum_nodes: None, maximum_object_members: None };

    /// Depth and node budgets, without an object member budget.
    pub const fn new(maximum_depth: usize, maximum_nodes: usize) -> Self {
        Self {
            maximum_depth: Some(maximum_depth),
            maximum_nodes: Some(maximum_nodes),
            maximum_object_members: None,
        }
    }

    /// The same budgets with an object member budget.
    #[must_use]
    pub const fn with_object_members(mut self, maximum_object_members: usize) -> Self {
        self.maximum_object_members = Some(maximum_object_members);
        self
    }

    fn any(&self) -> bool {
        self.maximum_depth.is_some() || self.maximum_nodes.is_some() || self.maximum_object_members.is_some()
    }
}

/// Python's default label.
pub const DEFAULT_LABEL: &str = "Package manifest";

/// `parse_content_json_bytes(value, label=..., maximum_depth=..., ...)`.
pub fn parse_content_json_bytes(value: &[u8], label: &str, limits: JsonShapeLimits) -> DomainResult<Json> {
    read(value, limits).map_err(|()| DomainError::value(format!("{label} is not strict JSON")))
}

/// `parse_canonical_json_bytes(...)`: the strict read, and the bytes must be exactly the value's
/// canonical encoding.
pub fn parse_canonical_json_bytes(value: &[u8], label: &str, limits: JsonShapeLimits) -> DomainResult<Json> {
    let captured = read(value, limits)
        .map_err(|()| DomainError::value(format!("{label} is not strict canonical JSON")))?;
    if value != super::canonical_json_bytes(&captured).as_slice() {
        return Err(DomainError::value(format!("{label} bytes are not canonical")));
    }
    Ok(captured)
}

fn read(value: &[u8], limits: JsonShapeLimits) -> Result<Json, ()> {
    let text = std::str::from_utf8(value).map_err(drop)?;
    let parsed = pyjson::loads(text).map_err(drop)?;
    serde_json::from_str::<UniqueMembers>(text).map_err(drop)?;
    if limits.any() {
        let (Some(maximum_depth), Some(maximum_nodes)) = (limits.maximum_depth, limits.maximum_nodes) else {
            // "Canonical JSON shape budgets must be paired", raised inside Python's `try`.
            return Err(());
        };
        preflight_json_shape(&parsed, maximum_depth, maximum_nodes, limits.maximum_object_members)
            .map_err(drop)?;
    }
    Ok(parsed)
}

/// `_preflight_json_shape`: bound a materialised JSON tree. The messages are Python's, though
/// its only caller replaces them with the label's message.
pub fn preflight_json_shape(
    value: &Json,
    maximum_depth: usize,
    maximum_nodes: usize,
    maximum_object_members: Option<usize>,
) -> DomainResult<()> {
    let too_deep = || DomainError::value("Canonical JSON nesting is too deep");
    let too_many = || DomainError::value("Canonical JSON node count is too large");
    let mut stack: Vec<(&Json, usize)> = vec![(value, 1)];
    let mut nodes = 0usize;
    while let Some((item, depth)) = stack.pop() {
        if depth > maximum_depth {
            return Err(too_deep());
        }
        nodes += 1;
        if nodes > maximum_nodes {
            return Err(too_many());
        }
        match item {
            Json::Object(map) => {
                if maximum_object_members.is_some_and(|limit| map.len() > limit) {
                    return Err(DomainError::value("Canonical JSON object member count is too large"));
                }
                let child_depth = depth + 1;
                for child in map.values() {
                    if child_depth > maximum_depth {
                        return Err(too_deep());
                    }
                    nodes += 1; // Object-member names are materialised nodes too.
                    if nodes > maximum_nodes {
                        return Err(too_many());
                    }
                    stack.push((child, child_depth));
                }
            }
            Json::Array(items) => {
                let child_depth = depth + 1;
                if !items.is_empty() && child_depth > maximum_depth {
                    return Err(too_deep());
                }
                stack.extend(items.iter().map(|child| (child, child_depth)));
            }
            _ => {}
        }
    }
    Ok(())
}

/// Parses any JSON and fails on a member name repeated within one object.
struct UniqueMembers;

impl<'de> Deserialize<'de> for UniqueMembers {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        deserializer.deserialize_any(UniqueVisitor)
    }
}

struct UniqueVisitor;

impl<'de> Visitor<'de> for UniqueVisitor {
    type Value = UniqueMembers;

    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("JSON with unique object members")
    }
    fn visit_bool<E: de::Error>(self, _: bool) -> Result<Self::Value, E> {
        Ok(UniqueMembers)
    }
    fn visit_i64<E: de::Error>(self, _: i64) -> Result<Self::Value, E> {
        Ok(UniqueMembers)
    }
    fn visit_u64<E: de::Error>(self, _: u64) -> Result<Self::Value, E> {
        Ok(UniqueMembers)
    }
    fn visit_f64<E: de::Error>(self, _: f64) -> Result<Self::Value, E> {
        Ok(UniqueMembers)
    }
    fn visit_str<E: de::Error>(self, _: &str) -> Result<Self::Value, E> {
        Ok(UniqueMembers)
    }
    fn visit_unit<E: de::Error>(self) -> Result<Self::Value, E> {
        Ok(UniqueMembers)
    }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Self::Value, A::Error> {
        while seq.next_element::<UniqueMembers>()?.is_some() {}
        Ok(UniqueMembers)
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
        let mut seen = HashSet::new();
        while let Some(key) = map.next_key::<String>()? {
            if !seen.insert(key) {
                return Err(de::Error::custom("Canonical JSON members must be unique"));
            }
            map.next_value::<UniqueMembers>()?;
        }
        Ok(UniqueMembers)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn content(text: &str) -> DomainResult<Json> {
        parse_content_json_bytes(text.as_bytes(), "X", JsonShapeLimits::NONE)
    }

    #[test]
    fn duplicates_are_refused_at_every_depth() {
        assert!(content(r#"{"a":1,"b":{"c":[{"d":1,"d":2}]}}"#).is_err());
        assert!(content(r#"[{"a":1},{"a":1}]"#).is_ok());
        assert_eq!(content(r#"{"a":1,"a":1}"#).unwrap_err().to_string(), "X is not strict JSON");
        // Escapes are compared after decoding, as Python does.
        assert!(content(r#"{"a":1,"a":2}"#).is_err());
    }

    #[test]
    fn unpaired_budgets_fail_inside() {
        let limits = JsonShapeLimits { maximum_depth: Some(4), ..JsonShapeLimits::NONE };
        assert!(parse_content_json_bytes(b"{}", "X", limits).is_err());
    }

    #[test]
    fn canonical_requires_exact_bytes() {
        assert!(parse_canonical_json_bytes(br#"{"a":1,"b":2}"#, "X", JsonShapeLimits::NONE).is_ok());
        assert_eq!(
            parse_canonical_json_bytes(br#"{"b":2,"a":1}"#, "X", JsonShapeLimits::NONE)
                .unwrap_err()
                .to_string(),
            "X bytes are not canonical"
        );
        assert_eq!(
            parse_canonical_json_bytes(b"{", "X", JsonShapeLimits::NONE).unwrap_err().to_string(),
            "X is not strict canonical JSON"
        );
    }
}
