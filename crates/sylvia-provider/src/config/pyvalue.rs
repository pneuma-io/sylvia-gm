//! Python's view of configuration values ([`Data`]): `type(x).__name__`, `str()`, `repr()`,
//! truthiness, `json.dumps` and JSON to Python.

use super::ConfigFailure;
use sylvia_base::pyjson::float_repr;
use sylvia_base::pystr::{repr, INT_RANGE_MESSAGE};
use sylvia_base::pyyaml::Data;
use sylvia_base::{Json, JsonMap};

/// `type(value).__name__`.
pub(crate) fn type_name(value: &Data) -> &'static str {
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

/// `bool(value)`.
pub(crate) fn truthy(value: &Data) -> bool {
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

/// `repr(float)` (`inf`, `nan`, `1e+16`, ...).
fn float_text(value: f64) -> String {
    if value.is_nan() {
        return "nan".into();
    }
    if value.is_infinite() {
        return if value > 0.0 { "inf" } else { "-inf" }.into();
    }
    float_repr(value)
}

/// `repr(bytes)`.
fn bytes_repr(bytes: &[u8]) -> String {
    let quote = if bytes.contains(&b'\'') && !bytes.contains(&b'"') { '"' } else { '\'' };
    let mut out = format!("b{quote}");
    for &byte in bytes {
        match byte {
            b'\\' => out.push_str("\\\\"),
            b'\t' => out.push_str("\\t"),
            b'\n' => out.push_str("\\n"),
            b'\r' => out.push_str("\\r"),
            b if b == quote as u8 => {
                out.push('\\');
                out.push(quote);
            }
            0x20..=0x7E => out.push(char::from(byte)),
            _ => out.push_str(&format!("\\x{byte:02x}")),
        }
    }
    out.push(quote);
    out
}

/// `repr(value)`.
pub(crate) fn py_repr(value: &Data) -> String {
    match value {
        Data::Str(text) => repr(text),
        Data::Bytes(bytes) => bytes_repr(bytes),
        Data::Date(date) => format!("datetime.date({}, {}, {})", date.year, date.month, date.day),
        Data::DateTime(datetime) => datetime_repr(datetime),
        Data::Tuple(items) if items.len() == 1 => format!("({},)", py_repr(&items[0])),
        Data::Tuple(items) => format!("({})", items.iter().map(py_repr).collect::<Vec<_>>().join(", ")),
        Data::List(items) => format!("[{}]", items.iter().map(py_repr).collect::<Vec<_>>().join(", ")),
        Data::Set(items) if items.is_empty() => "set()".into(),
        Data::Set(items) => format!("{{{}}}", items.iter().map(py_repr).collect::<Vec<_>>().join(", ")),
        Data::Dict(pairs) => format!(
            "{{{}}}",
            pairs
                .iter()
                .map(|(k, v)| format!("{}: {}", py_repr(k), py_repr(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        other => py_str(other),
    }
}

fn datetime_repr(datetime: &sylvia_base::pyyaml::DateTime) -> String {
    let mut fields = vec![
        datetime.date.year.to_string(),
        datetime.date.month.to_string(),
        datetime.date.day.to_string(),
        datetime.hour.to_string(),
        datetime.minute.to_string(),
    ];
    if datetime.second != 0 || datetime.microsecond != 0 {
        fields.push(datetime.second.to_string());
    }
    if datetime.microsecond != 0 {
        fields.push(datetime.microsecond.to_string());
    }
    let mut out = format!("datetime.datetime({}", fields.join(", "));
    if let Some(offset) = datetime.offset_seconds {
        if offset == 0 {
            out.push_str(", tzinfo=datetime.timezone.utc");
        } else {
            let days = i64::from(offset).div_euclid(86_400);
            let seconds = i64::from(offset).rem_euclid(86_400);
            let delta = match (days, seconds) {
                (0, s) => format!("datetime.timedelta(seconds={s})"),
                (d, 0) => format!("datetime.timedelta(days={d})"),
                (d, s) => format!("datetime.timedelta(days={d}, seconds={s})"),
            };
            out.push_str(&format!(", tzinfo=datetime.timezone({delta})"));
        }
    }
    out.push(')');
    out
}

/// `str(value)`.
pub(crate) fn py_str(value: &Data) -> String {
    match value {
        Data::None => "None".into(),
        Data::Bool(flag) => if *flag { "True" } else { "False" }.into(),
        Data::Int(number) => number.to_string(),
        Data::Float(number) => float_text(*number),
        Data::Str(text) => text.clone(),
        Data::Date(date) => date.isoformat(),
        Data::DateTime(datetime) => datetime.isoformat_with(' '),
        other => py_repr(other),
    }
}

/// `json.dumps(value)` with its defaults (`ensure_ascii=True`, `allow_nan=True`, separators
/// `", "` and `": "`), or `ensure_ascii=False`. A value JSON cannot hold raises `TypeError`.
pub(crate) fn json_dumps(value: &Data, ensure_ascii: bool) -> Result<String, ConfigFailure> {
    let mut out = String::new();
    write_json(value, ensure_ascii, &mut out)?;
    Ok(out)
}

fn json_string(text: &str, ensure_ascii: bool) -> String {
    let options = sylvia_base::pyjson::Dump::DEFAULT.ascii(ensure_ascii);
    sylvia_base::pyjson::dumps(&Json::String(text.to_owned()), options)
}

fn json_float(value: f64) -> String {
    if value.is_nan() {
        "NaN".into()
    } else if value.is_infinite() {
        if value > 0.0 { "Infinity" } else { "-Infinity" }.into()
    } else {
        float_repr(value)
    }
}

fn write_json(value: &Data, ensure_ascii: bool, out: &mut String) -> Result<(), ConfigFailure> {
    match value {
        Data::None => out.push_str("null"),
        Data::Bool(flag) => out.push_str(if *flag { "true" } else { "false" }),
        Data::Int(number) => out.push_str(&number.to_string()),
        Data::Float(number) => out.push_str(&json_float(*number)),
        Data::Str(text) => out.push_str(&json_string(text, ensure_ascii)),
        Data::List(items) | Data::Tuple(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                write_json(item, ensure_ascii, out)?;
            }
            out.push(']');
        }
        Data::Dict(pairs) => {
            out.push('{');
            for (index, (key, item)) in pairs.iter().enumerate() {
                if index > 0 {
                    out.push_str(", ");
                }
                let key_text = match key {
                    Data::Str(text) => text.clone(),
                    Data::Float(number) => json_float(*number),
                    Data::Bool(flag) => if *flag { "true" } else { "false" }.into(),
                    Data::Int(number) => number.to_string(),
                    Data::None => "null".into(),
                    other => {
                        return Err(ConfigFailure::python(
                            "TypeError",
                            format!("keys must be str, int, float, bool or None, not {}", type_name(other)),
                        ))
                    }
                };
                out.push_str(&json_string(&key_text, ensure_ascii));
                out.push_str(": ");
                write_json(item, ensure_ascii, out)?;
            }
            out.push('}');
        }
        other => {
            return Err(ConfigFailure::python(
                "TypeError",
                format!("Object of type {} is not JSON serializable", type_name(other)),
            ))
        }
    }
    Ok(())
}

/// A JSON value as Python's `json.loads` returns it.
pub(crate) fn from_json(value: &Json) -> Result<Data, ConfigFailure> {
    Ok(match value {
        Json::Null => Data::None,
        Json::Bool(flag) => Data::Bool(*flag),
        Json::Number(number) => match (number.as_i64(), number.as_f64()) {
            (Some(int), _) if !number.is_f64() => Data::Int(int),
            (None, _) if number.is_u64() => {
                return Err(ConfigFailure::python("ValueError", INT_RANGE_MESSAGE))
            }
            (_, Some(float)) => Data::Float(float),
            _ => return Err(ConfigFailure::python("ValueError", INT_RANGE_MESSAGE)),
        },
        Json::String(text) => Data::Str(text.clone()),
        Json::Array(items) => Data::List(items.iter().map(from_json).collect::<Result<_, _>>()?),
        Json::Object(map) => Data::Dict(
            map.iter()
                .map(|(key, item)| Ok((Data::Str(key.clone()), from_json(item)?)))
                .collect::<Result<_, _>>()?,
        ),
    })
}

/// A JSON value from a dict whose keys and values are all text, for callers that need one.
pub(crate) fn text_map_json(pairs: &[(String, String)]) -> Json {
    let mut map = JsonMap::new();
    for (key, value) in pairs {
        map.insert(key.clone(), Json::String(value.clone()));
    }
    Json::Object(map)
}
