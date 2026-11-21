//! Python operators on the objects `json.loads` gives, where their meaning differs from Rust's.
//!
//! Parsers that run on untrusted records compare and probe values Python-style: `True == 1`,
//! `1 == 1.0`, `x in a_str` is a substring test, `set(a_str)` is its characters, `set(a_list)`
//! raises `TypeError` for a list holding a list or dict, and `.get` on a non-`dict` raises
//! `AttributeError`. Where Python code catches those exceptions (or lets them escape as an
//! internal error), the port must fail at the same point, so these helpers return
//! [`PyFault`] instead of guessing.

use sylvia_base::Json;

/// The exception a Python operator would raise.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PyFault {
    /// `KeyError`.
    Key,
    /// `TypeError` (unhashable, not subscriptable, not iterable).
    Type,
    /// `AttributeError` (for example `.get` on a `list`).
    Attribute,
    /// `IndexError`.
    Index,
}

/// Python `a == b` for JSON values: booleans are the integers 0 and 1, integers equal floats
/// of the same value, containers compare element-wise and `dict` order does not matter.
pub fn py_eq(a: &Json, b: &Json) -> bool {
    match (numeric(a), numeric(b)) {
        (Some(x), Some(y)) => return x == y,
        (Some(_), None) | (None, Some(_)) => return false,
        (None, None) => {}
    }
    match (a, b) {
        (Json::Null, Json::Null) => true,
        (Json::String(x), Json::String(y)) => x == y,
        (Json::Array(x), Json::Array(y)) => x.len() == y.len() && x.iter().zip(y).all(|(p, q)| py_eq(p, q)),
        (Json::Object(x), Json::Object(y)) => {
            x.len() == y.len()
                && x.iter().all(|(key, value)| y.get(key).is_some_and(|other| py_eq(value, other)))
        }
        _ => false,
    }
}

/// A JSON number or boolean as the number Python compares with `==`.
fn numeric(value: &Json) -> Option<NumericValue> {
    match value {
        Json::Bool(flag) => Some(NumericValue::Int(i128::from(*flag))),
        Json::Number(number) => Some(match (number.as_i64(), number.as_u64()) {
            (Some(integer), _) => NumericValue::Int(i128::from(integer)),
            (None, Some(integer)) => NumericValue::Int(i128::from(integer)),
            _ => NumericValue::Float(number.as_f64().expect("a JSON number is an integer or a float")),
        }),
        _ => None,
    }
}

/// A number as Python compares it: exact for integers, IEEE for floats, and exact between the
/// two (an integer equals a float only when the float is exactly that integer).
#[derive(Debug, Clone, Copy)]
enum NumericValue {
    Int(i128),
    Float(f64),
}

impl PartialEq for NumericValue {
    fn eq(&self, other: &Self) -> bool {
        match (*self, *other) {
            (NumericValue::Int(x), NumericValue::Int(y)) => x == y,
            (NumericValue::Float(x), NumericValue::Float(y)) => x == y,
            (NumericValue::Int(x), NumericValue::Float(y))
            | (NumericValue::Float(y), NumericValue::Int(x)) => {
                y.fract() == 0.0 && y.abs() < 2f64.powi(100) && (y as i128) == x
            }
        }
    }
}

/// Python's ordering of two numbers (`int` or `float`, never `bool`), exact between the two.
/// `None` unless both are numbers.
pub fn py_number_cmp(a: &Json, b: &Json) -> Option<std::cmp::Ordering> {
    let (Json::Number(_), Json::Number(_)) = (a, b) else { return None };
    match (numeric(a)?, numeric(b)?) {
        (NumericValue::Int(x), NumericValue::Int(y)) => Some(x.cmp(&y)),
        (NumericValue::Float(x), NumericValue::Float(y)) => x.partial_cmp(&y),
        (NumericValue::Int(x), NumericValue::Float(y)) => Some(int_float(x, y)),
        (NumericValue::Float(x), NumericValue::Int(y)) => Some(int_float(y, x).reverse()),
    }
}

/// The order of an integer against a finite float, without rounding the integer.
fn int_float(int: i128, float: f64) -> std::cmp::Ordering {
    use std::cmp::Ordering;
    if float >= 2f64.powi(100) {
        return Ordering::Less;
    }
    if float <= -(2f64.powi(100)) {
        return Ordering::Greater;
    }
    let floor = float.floor();
    match int.cmp(&(floor as i128)) {
        Ordering::Equal if float > floor => Ordering::Less,
        order => order,
    }
}

/// `data.decode("utf-8")`: the text, or the `str(UnicodeDecodeError)` Python gives.
pub fn py_utf8_decode(data: &[u8]) -> Result<String, String> {
    match std::str::from_utf8(data) {
        Ok(text) => Ok(text.to_owned()),
        Err(error) => {
            let start = error.valid_up_to();
            let (end, reason) = match error.error_len() {
                None => (data.len(), "unexpected end of data"),
                Some(1) if matches!(data[start], 0x80..=0xC1 | 0xF5..=0xFF) => {
                    (start + 1, "invalid start byte")
                }
                Some(length) => (start + length, "invalid continuation byte"),
            };
            Err(if end - start == 1 {
                format!("'utf-8' codec can't decode byte 0x{:02x} in position {start}: {reason}", data[start])
            } else {
                format!("'utf-8' codec can't decode bytes in position {start}-{}: {reason}", end - 1)
            })
        }
    }
}

/// `repr(data)` for Python `bytes`.
pub fn py_bytes_repr(data: &[u8]) -> String {
    let quote = if data.contains(&b'\'') && !data.contains(&b'"') { '"' } else { '\'' };
    let mut out = String::from("b");
    out.push(quote);
    for &byte in data {
        match byte {
            b'\\' => out.push_str("\\\\"),
            b'\t' => out.push_str("\\t"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            byte if byte == quote as u8 => {
                out.push('\\');
                out.push(quote);
            }
            0x20..=0x7E => out.push(byte as char),
            byte => out.push_str(&format!("\\x{byte:02x}")),
        }
    }
    out.push(quote);
    out
}

/// `type(value).__name__` of the object a JSON value parses to.
pub fn py_type_name(value: &Json) -> &'static str {
    match value {
        Json::Null => "NoneType",
        Json::Bool(_) => "bool",
        Json::Number(n) if n.is_i64() || n.is_u64() => "int",
        Json::Number(_) => "float",
        Json::String(_) => "str",
        Json::Array(_) => "list",
        Json::Object(_) => "dict",
    }
}

/// The `TypeError` Python raises when hashing `value` (for `set()` or a set membership test).
pub fn py_unhashable(value: &Json) -> crate::error::DomainError {
    crate::error::DomainError::type_error(format!("unhashable type: '{}'", py_type_name(value)))
}

/// `str(value)`: text as itself, anything else as its `repr`.
pub fn py_str(value: &Json) -> String {
    match value {
        Json::String(text) => text.clone(),
        other => sylvia_base::pystr::repr_json(other),
    }
}

/// `isinstance(value, Sequence)`: a `list` or a `str` (a `dict` is not a sequence).
pub fn py_is_sequence(value: &Json) -> bool {
    matches!(value, Json::Array(_) | Json::String(_))
}

/// `iter(value)`: a list's items, a string's characters or a dict's keys, else `TypeError`.
pub fn py_iter(value: &Json) -> crate::error::DomainResult<Vec<Json>> {
    match value {
        Json::Array(items) => Ok(items.clone()),
        Json::String(text) => Ok(text.chars().map(|c| Json::String(c.to_string())).collect()),
        Json::Object(map) => Ok(map.keys().map(|key| Json::String(key.clone())).collect()),
        other => Err(crate::error::DomainError::type_error(format!(
            "'{}' object is not iterable",
            py_type_name(other)
        ))),
    }
}

/// `value.get(key)`, with Python's `AttributeError` for a value that is not a `dict`.
pub fn py_get_attr<'a>(value: &'a Json, key: &str) -> crate::error::DomainResult<Option<&'a Json>> {
    match value {
        Json::Object(map) => Ok(map.get(key)),
        other => Err(crate::error::DomainError::uncaught(
            "AttributeError",
            format!("'{}' object has no attribute 'get'", py_type_name(other)),
        )),
    }
}

/// `value[key]` with a `str` key, with Python's `KeyError` or `TypeError` and its text.
pub fn py_item<'a>(value: &'a Json, key: &str) -> crate::error::DomainResult<&'a Json> {
    use crate::error::DomainError;
    match value {
        Json::Object(map) => {
            map.get(key).ok_or_else(|| DomainError::uncaught("KeyError", sylvia_base::pystr::repr(key)))
        }
        Json::Array(_) => Err(DomainError::type_error("list indices must be integers or slices, not str")),
        Json::String(_) => Err(DomainError::type_error("string indices must be integers, not 'str'")),
        other => {
            Err(DomainError::type_error(format!("'{}' object is not subscriptable", py_type_name(other))))
        }
    }
}

/// `value[-1]`, with Python's `IndexError`, `KeyError` or `TypeError` and its text.
pub fn py_last(value: &Json) -> crate::error::DomainResult<Json> {
    use crate::error::DomainError;
    match value {
        Json::Array(items) => items
            .last()
            .cloned()
            .ok_or_else(|| DomainError::uncaught("IndexError", "list index out of range")),
        Json::String(text) => text
            .chars()
            .last()
            .map(|c| Json::String(c.to_string()))
            .ok_or_else(|| DomainError::uncaught("IndexError", "string index out of range")),
        Json::Object(_) => Err(DomainError::uncaught("KeyError", "-1")),
        other => {
            Err(DomainError::type_error(format!("'{}' object is not subscriptable", py_type_name(other))))
        }
    }
}

/// `item in container`, with the `TypeError` Python raises and its text.
pub fn py_contains(item: &Json, container: &Json) -> crate::error::DomainResult<bool> {
    use crate::error::DomainError;
    match container {
        Json::Object(map) => {
            py_require_hashable(item)?;
            Ok(item.as_str().is_some_and(|key| map.contains_key(key)))
        }
        Json::Array(items) => Ok(items.iter().any(|member| py_eq(member, item))),
        Json::String(text) => match item {
            Json::String(needle) => Ok(text.contains(needle.as_str())),
            other => Err(DomainError::type_error(format!(
                "'in <string>' requires string as left operand, not {}",
                py_type_name(other)
            ))),
        },
        other => Err(DomainError::type_error(format!(
            "argument of type '{}' is not iterable",
            py_type_name(other)
        ))),
    }
}

/// Hash `value` as a `set` or `dict` operation would: `TypeError` for a `list` or `dict`.
pub fn py_require_hashable(value: &Json) -> crate::error::DomainResult<()> {
    if py_hashable(value) {
        Ok(())
    } else {
        Err(py_unhashable(value))
    }
}

/// `bool(value)`.
pub fn py_truthy(value: &Json) -> bool {
    match value {
        Json::Null => false,
        Json::Bool(flag) => *flag,
        Json::Number(_) => numeric(value).is_some_and(|n| n != NumericValue::Int(0)),
        Json::String(text) => !text.is_empty(),
        Json::Array(items) => !items.is_empty(),
        Json::Object(map) => !map.is_empty(),
    }
}

/// Whether `value` is hashable in Python (`list` and `dict` are not).
pub fn py_hashable(value: &Json) -> bool {
    !matches!(value, Json::Array(_) | Json::Object(_))
}

/// The members of `set(value)`, de-duplicated with `==` (`True` and `1` are one member).
pub fn py_set(value: &Json) -> Result<Vec<Json>, PyFault> {
    let members: Vec<Json> = match value {
        Json::Object(map) => map.keys().map(|key| Json::String(key.clone())).collect(),
        Json::Array(items) => {
            if !items.iter().all(py_hashable) {
                return Err(PyFault::Type);
            }
            items.clone()
        }
        Json::String(text) => text.chars().map(|c| Json::String(c.to_string())).collect(),
        _ => return Err(PyFault::Type),
    };
    let mut unique: Vec<Json> = Vec::with_capacity(members.len());
    for member in members {
        if !unique.iter().any(|existing| py_eq(existing, &member)) {
            unique.push(member);
        }
    }
    Ok(unique)
}

/// `set(value) == {names...}` for a set of strings.
pub fn py_set_equals(value: &Json, names: &[&str]) -> Result<bool, PyFault> {
    let members = py_set(value)?;
    Ok(members.len() == names.len()
        && names.iter().all(|name| members.iter().any(|member| member.as_str() == Some(*name))))
}

/// `item in container`.
pub fn py_in(item: &Json, container: &Json) -> Result<bool, PyFault> {
    match container {
        Json::Object(map) => {
            if !py_hashable(item) {
                return Err(PyFault::Type);
            }
            // Only a `str` can equal a key.
            Ok(item.as_str().is_some_and(|key| map.contains_key(key)))
        }
        Json::Array(items) => Ok(items.iter().any(|member| py_eq(member, item))),
        Json::String(text) => match item {
            Json::String(needle) => Ok(text.contains(needle.as_str())),
            _ => Err(PyFault::Type),
        },
        _ => Err(PyFault::Type),
    }
}

/// `value.get(key)`: `AttributeError` unless `value` is a `dict`.
pub fn py_get<'a>(value: &'a Json, key: &str) -> Result<Option<&'a Json>, PyFault> {
    match value {
        Json::Object(map) => Ok(map.get(key)),
        _ => Err(PyFault::Attribute),
    }
}

/// `value[key]` with a `str` key: `KeyError` for a `dict` without it, `TypeError` otherwise.
pub fn py_index<'a>(value: &'a Json, key: &str) -> Result<&'a Json, PyFault> {
    match value {
        Json::Object(map) => map.get(key).ok_or(PyFault::Key),
        _ => Err(PyFault::Type),
    }
}

/// `container[key]` for any JSON key, as Python subscripts a `dict` or a `list`.
///
/// A `list` takes an integer (or boolean) index, negative from the end. Subscripting text or
/// a scalar is refused as a `TypeError`: no caller relies on indexing text.
pub fn py_subscript<'a>(container: &'a Json, key: &Json) -> Result<&'a Json, PyFault> {
    match container {
        Json::Object(map) => {
            if !py_hashable(key) {
                return Err(PyFault::Type);
            }
            key.as_str().and_then(|name| map.get(name)).ok_or(PyFault::Key)
        }
        Json::Array(items) => {
            let index = match key {
                Json::Bool(flag) => i128::from(*flag),
                Json::Number(number) if number.is_i64() => {
                    number.as_i64().map(i128::from).unwrap_or_default()
                }
                Json::Number(number) if number.is_u64() => {
                    number.as_u64().map(i128::from).unwrap_or_default()
                }
                _ => return Err(PyFault::Type),
            };
            let length = items.len() as i128;
            let position = if index < 0 { index + length } else { index };
            if (0..length).contains(&position) {
                Ok(&items[position as usize])
            } else {
                Err(PyFault::Index)
            }
        }
        _ => Err(PyFault::Type),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn equality_follows_python() {
        assert!(py_eq(&json!(true), &json!(1)));
        assert!(py_eq(&json!(1), &json!(1.0)));
        assert!(py_eq(&json!({"a": [true]}), &json!({"a": [1.0]})));
        assert!(!py_eq(&json!("1"), &json!(1)));
        assert!(!py_eq(&json!(null), &json!(false)));
        assert!(py_eq(&json!(9007199254740992_i64), &json!(9007199254740992.0)));
        assert!(!py_eq(&json!(9007199254740993_i64), &json!(9007199254740992.0)));
    }

    #[test]
    fn membership_and_sets_follow_python() {
        assert_eq!(py_in(&json!("ab"), &json!("xaby")), Ok(true));
        assert_eq!(py_in(&json!(1), &json!("x")), Err(PyFault::Type));
        assert_eq!(py_in(&json!([1]), &json!({"a": 1})), Err(PyFault::Type));
        assert_eq!(py_in(&json!(true), &json!([1])), Ok(true));
        assert_eq!(py_set_equals(&json!(["key", "namespace", "key"]), &["namespace", "key"]), Ok(true));
        assert_eq!(py_set_equals(&json!([{}]), &["a"]), Err(PyFault::Type));
        assert_eq!(py_set_equals(&json!(3), &["a"]), Err(PyFault::Type));
        assert_eq!(py_set(&json!([true, 1, 1.0])).unwrap().len(), 1);
        assert!(!py_truthy(&json!(0.0)) && py_truthy(&json!(" ")) && !py_truthy(&json!({})));
    }

    fn raised<T>(result: crate::error::DomainResult<T>) -> (&'static str, String) {
        let error = result.err().expect("an exception");
        (error.python_class(), error.message().to_owned())
    }

    #[test]
    fn operations_raise_pythons_exceptions() {
        assert_eq!(py_str(&json!("x")), "x");
        assert_eq!(py_str(&json!([1, true, null, 1.5, {"a": "b"}])), "[1, True, None, 1.5, {'a': 'b'}]");
        assert_eq!(py_iter(&json!("ab")).unwrap(), vec![json!("a"), json!("b")]);
        assert_eq!(py_iter(&json!({"k": 1})).unwrap(), vec![json!("k")]);
        assert_eq!(raised(py_iter(&json!(1))), ("TypeError", "'int' object is not iterable".into()));
        assert_eq!(
            raised(py_get_attr(&json!([]), "id")),
            ("AttributeError", "'list' object has no attribute 'get'".into())
        );
        assert_eq!(raised(py_item(&json!({}), "id")), ("KeyError", "'id'".into()));
        assert_eq!(
            raised(py_item(&json!([]), "id")),
            ("TypeError", "list indices must be integers or slices, not str".into())
        );
        assert_eq!(
            raised(py_item(&json!("x"), "id")),
            ("TypeError", "string indices must be integers, not 'str'".into())
        );
        assert_eq!(
            raised(py_item(&json!(null), "id")),
            ("TypeError", "'NoneType' object is not subscriptable".into())
        );
        assert_eq!(py_last(&json!("ab")).unwrap(), json!("b"));
        assert_eq!(raised(py_last(&json!([]))), ("IndexError", "list index out of range".into()));
        assert_eq!(raised(py_last(&json!({}))), ("KeyError", "-1".into()));
        assert_eq!(raised(py_require_hashable(&json!({}))), ("TypeError", "unhashable type: 'dict'".into()));
    }
}
