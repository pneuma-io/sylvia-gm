//! The parts of `application/desktop_update/manifest.py` the drafts store uses.

use super::update_error;
use serde::de::{Deserialize, Deserializer, MapAccess, SeqAccess, Visitor};
use sylvia_base::pyjson;
use sylvia_base::{CodedResult, Json};

/// `MANIFEST_LIMIT`.
pub const MANIFEST_LIMIT: usize = 2 * 1024 * 1024;

/// `canonical_bytes(value)`: sorted, compact, UTF-8. A `Json` value always encodes, so
/// Python's `invalid_metadata` failure cannot happen here.
pub fn canonical_bytes(value: &Json) -> Vec<u8> {
    pyjson::canonical_bytes(value)
}

/// Fails when an object has a repeated key, checked when the object closes as Python's
/// `object_pairs_hook` is, so a syntax error earlier in the same object wins.
struct UniqueKeys;

const DUPLICATE: &str = "sylvia: duplicate field";

impl<'de> Deserialize<'de> for UniqueKeys {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        deserializer.deserialize_any(UniqueKeysVisitor)
    }
}

struct UniqueKeysVisitor;

impl<'de> Visitor<'de> for UniqueKeysVisitor {
    type Value = UniqueKeys;

    fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("JSON")
    }

    fn visit_bool<E>(self, _: bool) -> Result<UniqueKeys, E> {
        Ok(UniqueKeys)
    }
    fn visit_i64<E>(self, _: i64) -> Result<UniqueKeys, E> {
        Ok(UniqueKeys)
    }
    fn visit_u64<E>(self, _: u64) -> Result<UniqueKeys, E> {
        Ok(UniqueKeys)
    }
    fn visit_f64<E>(self, _: f64) -> Result<UniqueKeys, E> {
        Ok(UniqueKeys)
    }
    fn visit_str<E>(self, _: &str) -> Result<UniqueKeys, E> {
        Ok(UniqueKeys)
    }
    fn visit_unit<E>(self) -> Result<UniqueKeys, E> {
        Ok(UniqueKeys)
    }

    fn visit_seq<A: SeqAccess<'de>>(self, mut items: A) -> Result<UniqueKeys, A::Error> {
        while items.next_element::<UniqueKeys>()?.is_some() {}
        Ok(UniqueKeys)
    }

    fn visit_map<A: MapAccess<'de>>(self, mut entries: A) -> Result<UniqueKeys, A::Error> {
        let mut keys: Vec<String> = Vec::new();
        while let Some(key) = entries.next_key::<String>()? {
            entries.next_value::<UniqueKeys>()?;
            keys.push(key);
        }
        let mut sorted = keys.clone();
        sorted.sort_unstable();
        if sorted.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(serde::de::Error::custom(DUPLICATE));
        }
        Ok(UniqueKeys)
    }
}

/// `bounded_json(raw, maximum=MANIFEST_LIMIT)`.
pub fn bounded_json(raw: &[u8], maximum: usize) -> CodedResult<Json> {
    if raw.is_empty() || raw.len() > maximum {
        return Err(update_error("metadata_limit", "Release metadata is missing or too large."));
    }
    let unreadable = || update_error("invalid_metadata", "Release metadata could not be read.");
    let text = std::str::from_utf8(raw).map_err(|_| unreadable())?;
    if let Err(error) = serde_json::from_str::<UniqueKeys>(text) {
        if error.to_string().starts_with(DUPLICATE) {
            return Err(update_error("invalid_metadata", "Release metadata contains duplicate fields."));
        }
        return Err(unreadable());
    }
    // Python's integer and `NaN` rules: see differences 1 and 2.
    let value = pyjson::loads(text).map_err(|_| unreadable())?;
    // The same walk as Python, last pushed first, so the same limit is met first.
    let mut pending: Vec<(&Json, usize)> = vec![(&value, 1)];
    let mut count = 0usize;
    while let Some((item, depth)) = pending.pop() {
        count += 1;
        if depth > 32 || count > 50_000 {
            return Err(update_error("metadata_limit", "Release metadata exceeds its structural limits."));
        }
        match item {
            Json::Object(map) => pending.extend(map.values().map(|child| (child, depth + 1))),
            Json::Array(items) => pending.extend(items.iter().map(|child| (child, depth + 1))),
            Json::Number(number) if !(number.is_i64() || number.is_u64()) => {
                return Err(update_error(
                    "invalid_metadata",
                    "Release metadata contains unsupported values.",
                ));
            }
            _ => {}
        }
    }
    Ok(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn code(raw: &str) -> String {
        bounded_json(raw.as_bytes(), MANIFEST_LIMIT).unwrap_err().code
    }

    #[test]
    fn refuses_what_python_refuses() {
        assert_eq!(
            bounded_json(b"", MANIFEST_LIMIT).unwrap_err().message,
            "Release metadata is missing or too large."
        );
        assert_eq!(
            bounded_json(br#"{"a":1,"a":2}"#, MANIFEST_LIMIT).unwrap_err().message,
            "Release metadata contains duplicate fields."
        );
        assert_eq!(
            bounded_json(br#"{"a":1.5}"#, MANIFEST_LIMIT).unwrap_err().message,
            "Release metadata contains unsupported values."
        );
        assert_eq!(code("{"), "invalid_metadata");
        let deep = format!("{}{}", "[".repeat(33), "]".repeat(33));
        assert_eq!(bounded_json(deep.as_bytes(), MANIFEST_LIMIT).unwrap_err().code, "metadata_limit");
        let shallow = format!("{}{}", "[".repeat(32), "]".repeat(32));
        assert!(bounded_json(shallow.as_bytes(), MANIFEST_LIMIT).is_ok());
        assert_eq!(bounded_json(br#"{"a":[1,"x",true,null]}"#, MANIFEST_LIMIT).unwrap()["a"][1], "x");
    }
}
