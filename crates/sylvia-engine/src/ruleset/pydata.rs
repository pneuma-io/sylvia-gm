//! Python behaviour over YAML-loaded values (`sylvia_base::pyyaml::Data`), for the validators.
//!
//! The Ruleset validators read whatever PyYAML built: any scalar may be a key, dates and bytes
//! occur, and the messages print values with `str()` and `repr()`. Python also raises on some of
//! that data (a list is unhashable, `int` and `str` cannot be ordered, `re.match` refuses a non
//! string), and those `TypeError`s are behaviour, since callers catch some and not others.

use crate::pyerror::{PyError, PyResult};
use sylvia_base::pyjson::float_repr;
use sylvia_base::pystr::repr as str_repr;
use sylvia_base::pyyaml::{data_key_eq, Data, Date, DateTime};

// Generated; kept exactly as the generator writes it.
#[rustfmt::skip]
#[path = "alnum_table.rs"]
mod alnum;

/// `type(value).__name__`.
pub fn type_name(value: &Data) -> &'static str {
    match value {
        Data::None => "NoneType",
        Data::Bool(_) => "bool",
        Data::Int(_) => "int",
        Data::Float(_) => "float",
        Data::Str(_) => "str",
        Data::Bytes(_) => "bytes",
        Data::Date(_) => "date",
        Data::DateTime(_) => "datetime",
        Data::Tuple(_) => "tuple",
        Data::List(_) => "list",
        Data::Dict(_) => "dict",
        Data::Set(_) => "set",
    }
}

/// `repr(float)`, including `inf` and `nan`.
pub fn float_text(value: f64) -> String {
    if value.is_nan() {
        "nan".to_owned()
    } else if value.is_infinite() {
        if value > 0.0 { "inf" } else { "-inf" }.to_owned()
    } else {
        float_repr(value)
    }
}

/// `repr(bytes)`.
pub fn bytes_repr(bytes: &[u8]) -> String {
    let quote = if bytes.contains(&b'\'') && !bytes.contains(&b'"') { '"' } else { '\'' };
    let mut out = String::from("b");
    out.push(quote);
    for byte in bytes {
        match *byte {
            b'\\' => out.push_str("\\\\"),
            b'\t' => out.push_str("\\t"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            b if char::from(b) == quote => {
                out.push('\\');
                out.push(quote);
            }
            b if !(0x20..0x7f).contains(&b) => out.push_str(&format!("\\x{b:02x}")),
            b => out.push(char::from(b)),
        }
    }
    out.push(quote);
    out
}

fn date_repr(date: &Date) -> String {
    format!("datetime.date({}, {}, {})", date.year, date.month, date.day)
}

fn timezone_repr(offset_seconds: i32) -> String {
    if offset_seconds == 0 {
        return "datetime.timezone.utc".to_owned();
    }
    let days = offset_seconds.div_euclid(86_400);
    let seconds = offset_seconds.rem_euclid(86_400);
    let delta = match (days, seconds) {
        (0, seconds) => format!("seconds={seconds}"),
        (days, 0) => format!("days={days}"),
        (days, seconds) => format!("days={days}, seconds={seconds}"),
    };
    format!("datetime.timezone(datetime.timedelta({delta}))")
}

fn datetime_repr(datetime: &DateTime) -> String {
    let date = &datetime.date;
    let mut out = format!(
        "datetime.datetime({}, {}, {}, {}, {}",
        date.year, date.month, date.day, datetime.hour, datetime.minute
    );
    if datetime.microsecond != 0 {
        out.push_str(&format!(", {}, {}", datetime.second, datetime.microsecond));
    } else if datetime.second != 0 {
        out.push_str(&format!(", {}", datetime.second));
    }
    if let Some(offset) = datetime.offset_seconds {
        out.push_str(&format!(", tzinfo={}", timezone_repr(offset)));
    }
    out.push(')');
    out
}

/// `repr(value)`.
pub fn repr(value: &Data) -> String {
    match value {
        Data::None => "None".to_owned(),
        Data::Bool(true) => "True".to_owned(),
        Data::Bool(false) => "False".to_owned(),
        Data::Int(number) => number.to_string(),
        Data::Float(number) => float_text(*number),
        Data::Str(text) => str_repr(text),
        Data::Bytes(bytes) => bytes_repr(bytes),
        Data::Date(date) => date_repr(date),
        Data::DateTime(datetime) => datetime_repr(datetime),
        Data::Tuple(items) => {
            let inner = items.iter().map(repr).collect::<Vec<_>>().join(", ");
            if items.len() == 1 {
                format!("({inner},)")
            } else {
                format!("({inner})")
            }
        }
        Data::List(items) => format!("[{}]", items.iter().map(repr).collect::<Vec<_>>().join(", ")),
        Data::Dict(pairs) => format!(
            "{{{}}}",
            pairs
                .iter()
                .map(|(key, item)| format!("{}: {}", repr(key), repr(item)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Data::Set(items) if items.is_empty() => "set()".to_owned(),
        Data::Set(items) => format!("{{{}}}", items.iter().map(repr).collect::<Vec<_>>().join(", ")),
    }
}

/// `str(value)` (what an f-string prints).
pub fn to_str(value: &Data) -> String {
    match value {
        Data::Str(text) => text.clone(),
        Data::Date(date) => date.isoformat(),
        Data::DateTime(datetime) => datetime.isoformat_with(' '),
        other => repr(other),
    }
}

/// `bool(value)`.
pub fn truthy(value: &Data) -> bool {
    match value {
        Data::None => false,
        Data::Bool(flag) => *flag,
        Data::Int(number) => *number != 0,
        Data::Float(number) => *number != 0.0,
        Data::Str(text) => !text.is_empty(),
        Data::Bytes(bytes) => !bytes.is_empty(),
        Data::Date(_) | Data::DateTime(_) => true,
        Data::Tuple(items) | Data::List(items) | Data::Set(items) => !items.is_empty(),
        Data::Dict(pairs) => !pairs.is_empty(),
    }
}

/// Python's `==`. Scalars compare as dict keys do (`1 == 1.0 == True`); containers compare
/// item by item, a list never equals a tuple, and dicts and sets ignore order.
pub fn py_eq(a: &Data, b: &Data) -> bool {
    match (a, b) {
        (Data::List(x), Data::List(y)) | (Data::Tuple(x), Data::Tuple(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(p, q)| py_eq(p, q))
        }
        (Data::Dict(x), Data::Dict(y)) => {
            x.len() == y.len()
                && x.iter().all(|(key, value)| y.iter().any(|(k, v)| data_key_eq(key, k) && py_eq(value, v)))
        }
        (Data::Set(x), Data::Set(y)) => {
            x.len() == y.len() && x.iter().all(|item| y.iter().any(|other| data_key_eq(item, other)))
        }
        (Data::List(_) | Data::Dict(_) | Data::Set(_), _)
        | (_, Data::List(_) | Data::Dict(_) | Data::Set(_)) => false,
        _ => data_key_eq(a, b),
    }
}

/// `value in sequence` for a list: the same object or an equal one (Python's `in`).
pub fn in_list(value: &Data, items: &[Data]) -> bool {
    items.iter().any(|item| py_eq(item, value))
}

/// The `TypeError` Python raises when the value must be hashed (`x in a_set`, a dict key).
pub fn unhashable(value: &Data) -> Option<PyError> {
    match value {
        Data::List(_) | Data::Dict(_) | Data::Set(_) => {
            Some(PyError::type_error(format!("unhashable type: '{}'", type_name(value))))
        }
        Data::Tuple(items) => items.iter().find_map(unhashable),
        _ => None,
    }
}

/// The `TypeError` from `value in some_set` (a `set` or `frozenset`). CPython's
/// `set.__contains__` retries an unhashable `set` key as a `frozenset`, so only lists,
/// mappings and tuples holding them raise.
pub fn set_membership_error(value: &Data) -> Option<PyError> {
    match value {
        Data::Set(_) => None,
        other => unhashable(other),
    }
}

/// `value in {"a", "b", ...}` for a set of strings (see [`set_membership_error`]).
pub fn in_str_set(value: &Data, members: &[&str]) -> PyResult<bool> {
    if let Some(error) = set_membership_error(value) {
        return Err(error);
    }
    Ok(matches!(value, Data::Str(text) if members.contains(&text.as_str())))
}

/// `value in mapping` for a mapping with string keys: `TypeError` for any unhashable value.
pub fn in_str_keys(value: &Data, keys: &[&str]) -> PyResult<bool> {
    if let Some(error) = unhashable(value) {
        return Err(error);
    }
    Ok(matches!(value, Data::Str(text) if keys.contains(&text.as_str())))
}

/// `isinstance(value, int)`, which includes `bool`.
pub fn is_int(value: &Data) -> bool {
    matches!(value, Data::Int(_) | Data::Bool(_))
}

/// `isinstance(value, (int, float))`, which includes `bool`.
pub fn is_number(value: &Data) -> bool {
    matches!(value, Data::Int(_) | Data::Bool(_) | Data::Float(_))
}

/// The value of an `int`, `bool` or `float` as a float.
#[allow(clippy::cast_precision_loss)]
pub fn number(value: &Data) -> Option<f64> {
    match value {
        Data::Int(n) => Some(*n as f64),
        Data::Bool(flag) => Some(f64::from(u8::from(*flag))),
        Data::Float(f) => Some(*f),
        _ => None,
    }
}

/// `a < b` (or `a > b`) between two numbers, exactly as Python compares `int` with `float`.
pub fn number_lt(a: &Data, b: &Data) -> bool {
    match (a, b) {
        (Data::Int(x), Data::Int(y)) => x < y,
        _ => match (number(a), number(b)) {
            (Some(x), Some(y)) => x < y,
            _ => false,
        },
    }
}

/// `isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))`.
pub fn is_sequence(value: &Data) -> bool {
    matches!(value, Data::List(_) | Data::Tuple(_))
}

/// `c.isalnum()`.
pub fn is_alnum(c: char) -> bool {
    let code = u32::from(c);
    let index = alnum::ALNUM.partition_point(|(start, _)| *start <= code);
    index > 0 && code <= alnum::ALNUM[index - 1].1
}

/// `text.isalnum()`: nonempty and every character alphanumeric.
pub fn str_is_alnum(text: &str) -> bool {
    !text.is_empty() && text.chars().all(is_alnum)
}

/// The `TypeError` `re.match` with a text pattern raises for a value that is not text.
pub fn not_text(value: &Data) -> PyError {
    match value {
        Data::Bytes(_) => PyError::type_error("cannot use a string pattern on a bytes-like object"),
        other => {
            PyError::type_error(format!("expected string or bytes-like object, got '{}'", type_name(other)))
        }
    }
}

/// The items `len()` and iteration see in a value, or CPython's `TypeError` for one without a
/// length (`object of type 'int' has no len()`).
pub fn sized_items(value: &Data) -> PyResult<Vec<Data>> {
    match value {
        Data::Str(text) => Ok(text.chars().map(|c| Data::Str(c.to_string())).collect()),
        Data::Bytes(bytes) => Ok(bytes.iter().map(|b| Data::Int(i64::from(*b))).collect()),
        Data::List(items) | Data::Tuple(items) | Data::Set(items) => Ok(items.clone()),
        Data::Dict(pairs) => Ok(keys(pairs)),
        other => Err(PyError::type_error(format!("object of type '{}' has no len()", type_name(other)))),
    }
}

/// `re.match(r"^...$", text)`: `$` also matches before one final newline.
pub fn anchored_match(text: &str, full: impl Fn(&str) -> bool) -> bool {
    full(text) || text.strip_suffix('\n').is_some_and(full)
}

/// `a < b` for `sorted()`: numbers, strings, bytes, dates and tuples or lists compare; any
/// other pair is CPython's `TypeError`.
fn py_lt(a: &Data, b: &Data) -> PyResult<bool> {
    let refuse = || {
        PyError::type_error(format!(
            "'<' not supported between instances of '{}' and '{}'",
            type_name(a),
            type_name(b)
        ))
    };
    match (a, b) {
        _ if is_number(a) && is_number(b) => Ok(number_lt(a, b)),
        (Data::Str(x), Data::Str(y)) => Ok(x < y),
        (Data::Bytes(x), Data::Bytes(y)) => Ok(x < y),
        (Data::Date(x), Data::Date(y)) => Ok((x.year, x.month, x.day) < (y.year, y.month, y.day)),
        (Data::List(x), Data::List(y)) | (Data::Tuple(x), Data::Tuple(y)) => {
            for (p, q) in x.iter().zip(y) {
                if !py_eq(p, q) {
                    return py_lt(p, q);
                }
            }
            Ok(x.len() < y.len())
        }
        _ => Err(refuse()),
    }
}

/// `sorted(values)`: CPython's `count_run` and `binarysort` below 64 items (so the first
/// failing comparison, which the `TypeError` names, is Python's); a stable merge sort above.
pub fn sorted(values: &[Data]) -> PyResult<Vec<Data>> {
    let mut items = values.to_vec();
    if items.len() < 2 {
        return Ok(items);
    }
    if items.len() >= 64 {
        let mut failure = None;
        items.sort_by(|a, b| {
            if failure.is_some() {
                return std::cmp::Ordering::Equal;
            }
            match (py_lt(a, b), py_lt(b, a)) {
                (Ok(true), _) => std::cmp::Ordering::Less,
                (Ok(false), Ok(true)) => std::cmp::Ordering::Greater,
                (Ok(false), Ok(false)) => std::cmp::Ordering::Equal,
                (Err(error), _) | (_, Err(error)) => {
                    failure = Some(error);
                    std::cmp::Ordering::Equal
                }
            }
        });
        return failure.map_or(Ok(items), Err);
    }
    let mut run = 2;
    let descending = py_lt(&items[1], &items[0])?;
    while run < items.len() {
        if py_lt(&items[run], &items[run - 1])? != descending {
            break;
        }
        run += 1;
    }
    if descending {
        items[..run].reverse();
    }
    for start in run..items.len() {
        let pivot = items[start].clone();
        let (mut low, mut high) = (0, start);
        while low < high {
            let middle = low + ((high - low) >> 1);
            if py_lt(&pivot, &items[middle])? {
                high = middle;
            } else {
                low = middle + 1;
            }
        }
        items[low..=start].rotate_right(1);
    }
    Ok(items)
}

/// `repr(sorted(values))` for a list of keys.
pub fn sorted_repr(values: &[Data]) -> PyResult<String> {
    Ok(repr(&Data::List(sorted(values)?)))
}

/// A dict's keys, in order (`set(mapping)` before set operations).
pub fn keys(pairs: &[(Data, Data)]) -> Vec<Data> {
    pairs.iter().map(|(key, _)| key.clone()).collect()
}

/// `set(mapping) - {names}`: the keys that are not one of the names, in dict order.
pub fn keys_except(pairs: &[(Data, Data)], names: &[&str]) -> Vec<Data> {
    pairs
        .iter()
        .filter(|(key, _)| !matches!(key, Data::Str(text) if names.contains(&text.as_str())))
        .map(|(key, _)| key.clone())
        .collect()
}

/// `set(mapping) == {names}`.
pub fn keys_equal(pairs: &[(Data, Data)], names: &[&str]) -> bool {
    keys_except(pairs, names).is_empty()
        && names
            .iter()
            .all(|name| pairs.iter().any(|(key, _)| matches!(key, Data::Str(text) if text == name)))
}

/// `{names} <= set(mapping)`.
pub fn has_keys(pairs: &[(Data, Data)], names: &[&str]) -> bool {
    names.iter().all(|name| pairs.iter().any(|(key, _)| matches!(key, Data::Str(text) if text == name)))
}

/// `mapping.get("name")` on a dict, `None` for anything else.
pub fn get<'a>(value: &'a Data, name: &str) -> Option<&'a Data> {
    match value {
        Data::Dict(_) => value.get_str(name),
        _ => None,
    }
}

/// `"name" in mapping`.
pub fn contains(pairs: &[(Data, Data)], name: &str) -> bool {
    pairs.iter().any(|(key, _)| matches!(key, Data::Str(text) if text == name))
}

/// The text of a `str`.
pub fn text(value: Option<&Data>) -> Option<&str> {
    match value {
        Some(Data::Str(text)) => Some(text),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reprs_match_python() {
        assert_eq!(bytes_repr(b"it's"), "b\"it's\"");
        assert_eq!(bytes_repr(b"a'b\"c"), "b'a\\'b\"c'");
        assert_eq!(bytes_repr(&[0, 9, 0x7f]), "b'\\x00\\t\\x7f'");
        assert_eq!(timezone_repr(3600), "datetime.timezone(datetime.timedelta(seconds=3600))");
        assert_eq!(timezone_repr(-19_800), "datetime.timezone(datetime.timedelta(days=-1, seconds=66600))");
        assert_eq!(repr(&Data::Set(Vec::new())), "set()");
        assert_eq!(repr(&Data::Tuple(vec![Data::Int(1)])), "(1,)");
        assert!(alnum::ALNUM.len() > 100);
    }

    #[test]
    fn sorting_mixed_keys_names_the_first_failing_pair() {
        let error = sorted(&[Data::Str("a".into()), Data::Int(1)]).unwrap_err();
        assert_eq!(error.message, "'<' not supported between instances of 'int' and 'str'");
    }
}
