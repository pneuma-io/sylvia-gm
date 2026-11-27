//! Python's set and iteration semantics for saved JSON values: what `set(...)`, `x in {...}`,
//! `dict.fromkeys(...)` and a `for` loop do with them, refusals included.

use serde_json::Value as Json;
use sylvia_base::CodedError;
use sylvia_domain::pyops::{py_eq, py_type_name};

/// An uncaught `TypeError`, as the owner reports it.
pub(crate) fn type_error(message: String) -> CodedError {
    CodedError::new("internal_error", message).with_error_type("TypeError")
}

/// Hashing `value` for a set, a dict key or a set membership test: an object or a list is
/// unhashable.
pub(crate) fn require_hashable(value: &Json) -> Result<(), CodedError> {
    match value {
        Json::Object(_) | Json::Array(_) => {
            Err(type_error(format!("unhashable type: '{}'", py_type_name(value))))
        }
        _ => Ok(()),
    }
}

/// [`require_hashable`] for a value read from a saved message's content, which Python keeps as
/// frozen JSON (`FrozenJsonList`, `FrozenJsonDict`).
pub(crate) fn require_hashable_frozen(value: &Json) -> Result<(), CodedError> {
    let name = match value {
        Json::Array(_) => "FrozenJsonList",
        Json::Object(_) => "FrozenJsonDict",
        _ => return Ok(()),
    };
    Err(type_error(format!("unhashable type: '{name}'")))
}

/// Iterating a saved value as Python does: a list's items, a text's characters, an object's
/// keys; anything else is not iterable.
pub(crate) fn py_iter(value: &Json) -> Result<Vec<Json>, CodedError> {
    match value {
        Json::Array(items) => Ok(items.clone()),
        Json::String(text) => Ok(text.chars().map(|c| Json::from(c.to_string())).collect()),
        Json::Object(map) => Ok(map.keys().map(|key| Json::from(key.as_str())).collect()),
        other => Err(type_error(format!("'{}' object is not iterable", py_type_name(other)))),
    }
}

/// `dict.fromkeys(...)`'s next key: kept unless an equal one (Python's `1 == 1.0 == True`) is
/// already there, which keeps its first form.
pub(crate) fn push_unique(keys: &mut Vec<Json>, value: Json) -> Result<(), CodedError> {
    require_hashable(&value)?;
    if !keys.iter().any(|key| py_eq(key, &value)) {
        keys.push(value);
    }
    Ok(())
}
