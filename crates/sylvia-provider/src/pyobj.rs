//! Python's view of provider JSON, as the OpenAI SDK hands it to the transport.
//!
//! The SDK builds its response models with `construct` (no validation): every *declared* field
//! of a model exists (`None` when the JSON lacks it), undeclared keys are kept as extras, and a
//! value of the wrong type is kept as it is. Python code then reads them with `getattr`, which
//! falls back to its default only for an attribute that does not exist. [`attr`] reproduces that
//! from the JSON and the model's declared field list; the lists are checked against the SDK in
//! `tests/chat_vectors.rs`.
//!
//! The rest are the bits of Python semantics the transport relies on: truthiness, `int(x or 0)`,
//! `==` between JSON values, and `str()` of a parsed JSON body.

use sylvia_base::Json;

static NONE: Json = Json::Null;

/// Declared fields of the SDK models the Chat Completions transport reads (openai 2.53.0).
pub mod models {
    pub const CHUNK: &[&str] = &[
        "id",
        "choices",
        "created",
        "model",
        "object",
        "moderation",
        "service_tier",
        "system_fingerprint",
        "usage",
    ];
    pub const CHUNK_CHOICE: &[&str] = &["delta", "finish_reason", "index", "logprobs"];
    pub const DELTA: &[&str] = &["content", "function_call", "refusal", "role", "tool_calls"];
    pub const DELTA_TOOL_CALL: &[&str] = &["index", "id", "function", "type"];
    pub const DELTA_FUNCTION: &[&str] = &["arguments", "name"];
    pub const USAGE: &[&str] = &[
        "completion_tokens",
        "prompt_tokens",
        "total_tokens",
        "completion_tokens_details",
        "prompt_tokens_details",
    ];
    pub const COMPLETION_DETAILS: &[&str] =
        &["accepted_prediction_tokens", "audio_tokens", "reasoning_tokens", "rejected_prediction_tokens"];
    pub const PROMPT_DETAILS: &[&str] = &["audio_tokens", "cache_write_tokens", "cached_tokens"];
    pub const COMPLETION: &[&str] = CHUNK;
    pub const COMPLETION_CHOICE: &[&str] = &["finish_reason", "index", "logprobs", "message"];
    pub const MESSAGE: &[&str] =
        &["content", "refusal", "role", "annotations", "audio", "function_call", "tool_calls"];
    pub const FUNCTION_TOOL_CALL: &[&str] = &["id", "function", "type"];
    pub const FUNCTION: &[&str] = &["arguments", "name"];
    pub const CUSTOM_TOOL_CALL: &[&str] = &["id", "custom", "type"];
}

/// `getattr(obj, name)` where `obj` is held by Python as the SDK model with `declared` fields
/// when it is a JSON object (`construct` turns a dict into the model), and as a plain builtin
/// otherwise. `None` means the attribute does not exist, so a `getattr` default applies.
pub fn attr<'a>(value: &'a Json, declared: &[&str], name: &str) -> Option<&'a Json> {
    let map = value.as_object()?;
    match map.get(name) {
        Some(found) => Some(found),
        None if declared.contains(&name) => Some(&NONE),
        None => None,
    }
}

/// `getattr(obj, name, None)` for a model: a missing attribute reads as `None`.
pub fn attr_or_none<'a>(value: &'a Json, declared: &[&str], name: &str) -> &'a Json {
    attr(value, declared, name).unwrap_or(&NONE)
}

/// `_delta_value(obj, name, None)` from `_tool_delta.py`: `.get` on a dict, `getattr` otherwise.
/// `model` says whether Python holds `value` as an SDK model (declared fields) or a plain dict.
pub fn delta_value<'a>(value: &'a Json, model: Option<&[&str]>, name: &str) -> Option<&'a Json> {
    match model {
        Some(declared) => attr(value, declared, name),
        None => value.as_object().and_then(|map| map.get(name)),
    }
}

/// Python truthiness of a plain JSON value. An SDK model is always truthy; callers that hold one
/// check for an object first.
pub fn truthy(value: &Json) -> bool {
    match value {
        Json::Null => false,
        Json::Bool(flag) => *flag,
        Json::Number(number) => number.as_f64().is_some_and(|n| n != 0.0),
        Json::String(text) => !text.is_empty(),
        Json::Array(items) => !items.is_empty(),
        Json::Object(map) => !map.is_empty(),
    }
}

/// Python's type name for a JSON value, as error messages show it.
pub fn type_name(value: &Json) -> &'static str {
    match value {
        Json::Null => "NoneType",
        Json::Bool(_) => "bool",
        Json::Number(number) if number.is_f64() => "float",
        Json::Number(_) => "int",
        Json::String(_) => "str",
        Json::Array(_) => "list",
        Json::Object(_) => "dict",
    }
}

/// A Python exception raised by accident of the data's shape (`AttributeError`, `TypeError`,
/// `KeyError`, `ValueError`), not a provider error. Callers convert it the way Python's
/// `except Exception` around them does. The message imitates Python's where it is cheap; it is
/// never stored or shown (the owner replaces it with a fixed sentence).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Incidental(pub String);

impl Incidental {
    /// `AttributeError: '<type>' object has no attribute '<name>'`.
    pub fn no_attribute(value: &Json, name: &str) -> Self {
        Self(format!("'{}' object has no attribute '{name}'", type_name(value)))
    }
}

/// The refusal for a provider number outside the 64-bit range. Python's integers are unbounded,
/// so Python would carry on; Rust refuses instead of storing a clamped value (`differences.md`).
pub const COUNT_OVERFLOW: &str = "Provider usage count is outside the supported integer range";

/// `int(value or 0)`, refusing a value outside the 64-bit range with [`COUNT_OVERFLOW`].
pub fn int_or_zero(value: &Json) -> Result<i64, Incidental> {
    if !truthy(value) {
        return Ok(0);
    }
    let overflow = || Incidental(COUNT_OVERFLOW.into());
    match value {
        Json::Bool(flag) => Ok(i64::from(*flag)),
        Json::Number(number) => {
            if let Some(whole) = number.as_i64() {
                return Ok(whole);
            }
            if !number.is_f64() {
                // An integer above i64::MAX.
                return Err(overflow());
            }
            let float = number.as_f64().unwrap_or(0.0);
            if !float.is_finite() {
                return Err(Incidental("cannot convert float infinity to integer".into()));
            }
            let whole = float.trunc();
            // i64 holds [-2^63, 2^63).
            if !(-9_223_372_036_854_775_808.0..9_223_372_036_854_775_808.0).contains(&whole) {
                return Err(overflow());
            }
            Ok(whole as i64)
        }
        Json::String(text) => match python_int_text(text) {
            IntText::Value(value) => Ok(value),
            IntText::Overflow => Err(overflow()),
            IntText::Invalid => {
                Err(Incidental(format!("invalid literal for int() with base 10: {}", py_repr_str(text))))
            }
        },
        other => Err(Incidental(format!(
            "int() argument must be a string, a bytes-like object or a real number, not '{}'",
            type_name(other)
        ))),
    }
}

/// What `int(text)` makes of a string.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IntText {
    Value(i64),
    /// Valid digits, but outside the 64-bit range.
    Overflow,
    Invalid,
}

/// `int(text)`, `None` where Python raises or the value does not fit in 64 bits.
pub(crate) fn parse_python_int(text: &str) -> Option<i64> {
    match python_int_text(text) {
        IntText::Value(value) => Some(value),
        _ => None,
    }
}

/// `int(text)` (`sylvia_base::pystr::parse_int`, Python's string rules).
pub fn python_int_text(text: &str) -> IntText {
    match sylvia_base::pystr::parse_int(text) {
        Ok(value) => IntText::Value(value),
        Err(sylvia_base::pystr::IntParseError::OutOfRange { .. }) => IntText::Overflow,
        Err(_) => IntText::Invalid,
    }
}

/// `type(value) is int and value >= 0` (a `bool` is not an `int` here).
pub fn observed_count(value: Option<&Json>) -> Option<i64> {
    match value? {
        Json::Number(number) if !number.is_f64() => number.as_i64().filter(|n| *n >= 0),
        _ => None,
    }
}

/// `a == b` between two JSON values as Python compares the parsed objects: `1 == 1.0` and
/// `True == 1`; dicts compare without regard to order.
pub fn py_eq(a: &Json, b: &Json) -> bool {
    fn number(value: &Json) -> Option<f64> {
        match value {
            Json::Bool(flag) => Some(f64::from(u8::from(*flag))),
            Json::Number(n) => n.as_f64(),
            _ => None,
        }
    }
    match (a, b) {
        (Json::Null, Json::Null) => true,
        (Json::String(x), Json::String(y)) => x == y,
        (Json::Array(x), Json::Array(y)) => x.len() == y.len() && x.iter().zip(y).all(|(p, q)| py_eq(p, q)),
        (Json::Object(x), Json::Object(y)) => {
            x.len() == y.len()
                && x.iter().all(|(key, value)| y.get(key).is_some_and(|other| py_eq(value, other)))
        }
        _ => match (number(a), number(b)) {
            (Some(x), Some(y)) => match (a, b) {
                // Exact integer comparison where both are integers.
                (Json::Number(p), Json::Number(q)) if !p.is_f64() && !q.is_f64() => {
                    p.as_i64() == q.as_i64() && p.as_u64() == q.as_u64()
                }
                _ => x == y,
            },
            _ => false,
        },
    }
}

/// `repr(text)` for a `str` (`sylvia_base::pystr::repr`: Python's quote choice and escapes,
/// non-printable characters included).
pub fn py_repr_str(text: &str) -> String {
    sylvia_base::pystr::repr(text)
}

/// `str(value)` of a parsed JSON value: a `str` as itself, containers as their `repr`.
pub fn py_str(value: &Json) -> String {
    match value {
        Json::String(text) => text.clone(),
        other => py_repr(other),
    }
}

/// `repr(value)` of a parsed JSON value (see [`py_repr_str`] for the string caveat).
pub fn py_repr(value: &Json) -> String {
    match value {
        Json::Null => "None".into(),
        Json::Bool(true) => "True".into(),
        Json::Bool(false) => "False".into(),
        Json::Number(number) if number.is_f64() => {
            sylvia_base::pyjson::float_repr(number.as_f64().unwrap_or(0.0))
        }
        Json::Number(number) => number.to_string(),
        Json::String(text) => py_repr_str(text),
        Json::Array(items) => format!("[{}]", items.iter().map(py_repr).collect::<Vec<_>>().join(", ")),
        Json::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(key, value)| format!("{}: {}", py_repr_str(key), py_repr(value)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

/// Iterate a value the way a Python `for` loop does over `value or []`: a list by item, a dict
/// by key, a string by character. Anything else that is truthy raises `TypeError`.
pub fn py_iter(value: &Json) -> Result<Vec<Json>, Incidental> {
    if !truthy(value) {
        return Ok(Vec::new());
    }
    match value {
        Json::Array(items) => Ok(items.clone()),
        Json::Object(map) => Ok(map.keys().map(|key| Json::String(key.clone())).collect()),
        Json::String(text) => Ok(text.chars().map(|c| Json::String(c.to_string())).collect()),
        other => Err(Incidental(format!("'{}' object is not iterable", type_name(other)))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn python_equality_and_repr() {
        assert!(py_eq(&json!(1), &json!(1.0)));
        assert!(py_eq(&json!(true), &json!(1)));
        assert!(py_eq(&json!({"a": 1, "b": 2}), &json!({"b": 2, "a": 1})));
        assert!(!py_eq(&json!("1"), &json!(1)));
        assert_eq!(py_repr(&json!({"error": {"message": "it's"}})), "{'error': {'message': \"it's\"}}");
        assert_eq!(int_or_zero(&json!("12")), Ok(12));
        assert_eq!(int_or_zero(&json!(3.9)), Ok(3));
        assert_eq!(int_or_zero(&json!(null)), Ok(0));
    }

    #[test]
    fn counts_outside_64_bits_are_refused_not_clamped() {
        let refused = Err(Incidental(COUNT_OVERFLOW.into()));
        assert_eq!(int_or_zero(&json!(18_446_744_073_709_551_615u64)), refused);
        assert_eq!(int_or_zero(&json!(1e19)), refused);
        assert_eq!(int_or_zero(&json!(-1e19)), refused);
        assert_eq!(int_or_zero(&json!("99999999999999999999")), refused);
        assert_eq!(int_or_zero(&json!(9.2e18)), Ok(9_200_000_000_000_000_000));
        assert_eq!(int_or_zero(&json!("-9223372036854775808")), Ok(i64::MIN));
        assert_eq!(int_or_zero(&json!(i64::MAX)), Ok(i64::MAX));
    }
}
