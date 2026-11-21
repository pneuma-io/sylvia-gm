//! The Python values `SafeConstructor` builds, and their conversion to JSON.
//!
//! Containers live in an arena ([`Loaded::objects`]) and values refer to them by
//! [`ObjectId`], so an alias is the same container (as in Python, where it is the same object)
//! and a recursive document is a cycle without reference-counting leaks.

use crate::{Json, JsonMap};

/// An index into [`Loaded::objects`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct ObjectId(pub usize);

/// `datetime.date`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Date {
    pub year: i32,
    pub month: u32,
    pub day: u32,
}

/// `datetime.datetime`, naive or with a fixed offset.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DateTime {
    pub date: Date,
    pub hour: u32,
    pub minute: u32,
    pub second: u32,
    pub microsecond: u32,
    /// The UTC offset in seconds (`datetime.timezone(delta)`), `None` for a naive datetime.
    pub offset_seconds: Option<i32>,
}

impl Date {
    /// `date.isoformat()`.
    pub fn isoformat(&self) -> String {
        format!("{:04}-{:02}-{:02}", self.year, self.month, self.day)
    }
}

impl DateTime {
    /// `datetime.isoformat()`.
    pub fn isoformat(&self) -> String {
        self.isoformat_with('T')
    }

    /// `datetime.isoformat(sep)`.
    pub fn isoformat_with(&self, separator: char) -> String {
        let mut out = format!(
            "{}{separator}{:02}:{:02}:{:02}",
            self.date.isoformat(),
            self.hour,
            self.minute,
            self.second
        );
        if self.microsecond != 0 {
            out.push_str(&format!(".{:06}", self.microsecond));
        }
        if let Some(offset) = self.offset_seconds {
            let sign = if offset < 0 { '-' } else { '+' };
            let total = offset.unsigned_abs();
            out.push_str(&format!("{sign}{:02}:{:02}", total / 3600, total / 60 % 60));
            if total % 60 != 0 {
                out.push_str(&format!(":{:02}", total % 60));
            }
        }
        out
    }

    fn utc_micros(&self) -> Option<i128> {
        let offset = self.offset_seconds?;
        let days = days_from_civil(i64::from(self.date.year), i64::from(self.date.month), i64::from(self.date.day));
        let seconds = days * 86_400 + i64::from(self.hour) * 3600 + i64::from(self.minute) * 60 + i64::from(self.second)
            - i64::from(offset);
        Some(i128::from(seconds) * 1_000_000 + i128::from(self.microsecond))
    }
}

pub(crate) fn days_from_civil(year: i64, month: i64, day: i64) -> i64 {
    let year = if month <= 2 { year - 1 } else { year };
    let era = year.div_euclid(400);
    let yoe = year - era * 400;
    let mp = (month + 9) % 12;
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// `SafeConstructor.nan_value`, the one NaN object every `.nan` scalar gives.
pub const SHARED_NAN: f64 = f64::NAN;
/// A NaN computed anew (`float('nan')`, NaN arithmetic): a separate Python object, so never
/// equal to another key. Told apart from [`SHARED_NAN`] by its payload.
const FRESH_NAN_BITS: u64 = 0x7ff8_0000_0000_0001;

/// A float from arithmetic or `float()`: a NaN gets the fresh payload, keeping its sign.
pub fn fresh_float(value: f64) -> f64 {
    if value.is_nan() {
        f64::from_bits(FRESH_NAN_BITS | (value.to_bits() & (1 << 63)))
    } else {
        value
    }
}

fn is_shared_nan(value: f64) -> bool {
    value.is_nan() && value.to_bits() & !(1 << 63) == SHARED_NAN.to_bits() & !(1 << 63)
}

/// One constructed value.
#[derive(Debug, Clone, PartialEq)]
pub enum Value {
    None,
    Bool(bool),
    /// A Python `int`. Integers beyond `i64` are refused while constructing (`differences.md`).
    Int(i64),
    Float(f64),
    Str(String),
    Bytes(Vec<u8>),
    Date(Date),
    DateTime(DateTime),
    /// A `tuple` (the entries of `!!omap` and `!!pairs`).
    Tuple(Vec<Value>),
    List(ObjectId),
    Dict(ObjectId),
    Set(ObjectId),
}

/// A container in the arena.
#[derive(Debug, Clone, PartialEq, Default)]
pub enum Object {
    #[default]
    Empty,
    List(Vec<Value>),
    /// A `dict` in insertion order.
    Dict(Vec<(Value, Value)>),
    /// A `set`, in insertion order (Python's own order depends on hashing).
    Set(Vec<Value>),
}

/// A constructed document: its root and every container it holds.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Loaded {
    pub root: Option<Value>,
    pub objects: Vec<Object>,
}

impl Value {
    /// Whether Python can use it as a `dict` key (`collections.abc.Hashable`).
    pub fn hashable(&self) -> bool {
        match self {
            Self::List(_) | Self::Dict(_) | Self::Set(_) => false,
            Self::Tuple(items) => items.iter().all(Value::hashable),
            _ => true,
        }
    }

}

/// `int == float` as Python compares them: exactly, never through a rounded conversion.
fn int_float_eq(int: i64, float: f64) -> bool {
    // -2**63 <= float < 2**63, and whole: then the conversion to i64 is exact.
    float.fract() == 0.0 && (-9_223_372_036_854_775_808.0..9_223_372_036_854_775_808.0).contains(&float) && float as i64 == int
}

/// `a == b` for two hashable keys, as a `dict` compares them: `True == 1 == 1.0`, the shared
/// `.nan` equal to itself (a `dict` checks identity first) but no other NaN, an aware and a naive
/// datetime never equal.
pub fn key_eq(a: &Value, b: &Value) -> bool {
    let as_int = |value: &Value| match value {
        Value::Bool(flag) => Some(i64::from(*flag)),
        Value::Int(number) => Some(*number),
        _ => None,
    };
    match (a, b) {
        (Value::None, Value::None) => true,
        (Value::Str(x), Value::Str(y)) => x == y,
        (Value::Bytes(x), Value::Bytes(y)) => x == y,
        (Value::Date(x), Value::Date(y)) => x == y,
        (Value::DateTime(x), Value::DateTime(y)) => match (x.utc_micros(), y.utc_micros()) {
            (Some(p), Some(q)) => p == q,
            (None, None) => x == y,
            _ => false,
        },
        (Value::Tuple(x), Value::Tuple(y)) => x.len() == y.len() && x.iter().zip(y).all(|(p, q)| key_eq(p, q)),
        (Value::Float(x), Value::Float(y)) => x == y || (is_shared_nan(*x) && is_shared_nan(*y)),
        (Value::Float(x), other) | (other, Value::Float(x)) => as_int(other).is_some_and(|int| int_float_eq(int, *x)),
        _ => match (as_int(a), as_int(b)) {
            (Some(x), Some(y)) => x == y,
            _ => false,
        },
    }
}

/// Why a loaded value has no JSON form.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NotJson(pub String);

impl Loaded {
    /// A container.
    pub fn object(&self, id: ObjectId) -> &Object {
        &self.objects[id.0]
    }

    /// The root as JSON, where every value has a JSON form: `None`, `bool`, `int`, finite
    /// `float`, `str`, lists and dicts with `str` keys. Cycles, other keys and other types are
    /// refused; callers that accept more read [`Loaded`] directly.
    pub fn to_json(&self) -> Result<Json, NotJson> {
        match &self.root {
            None => Ok(Json::Null),
            Some(value) => self.value_json(value, &mut Vec::new()),
        }
    }

    /// One value as JSON (see [`Loaded::to_json`]).
    pub fn value_json(&self, value: &Value, path: &mut Vec<ObjectId>) -> Result<Json, NotJson> {
        Ok(match value {
            Value::None => Json::Null,
            Value::Bool(flag) => Json::Bool(*flag),
            Value::Int(number) => Json::from(*number),
            Value::Float(number) => {
                serde_json::Number::from_f64(*number).map(Json::Number).ok_or_else(|| NotJson("a non-finite float".into()))?
            }
            Value::Str(text) => Json::String(text.clone()),
            Value::List(id) | Value::Dict(id) | Value::Set(id) => {
                if path.contains(id) {
                    return Err(NotJson("a recursive structure".into()));
                }
                path.push(*id);
                let result = match self.object(*id) {
                    Object::List(items) => {
                        Json::Array(items.iter().map(|item| self.value_json(item, path)).collect::<Result<_, _>>()?)
                    }
                    Object::Dict(pairs) => {
                        let mut map = JsonMap::new();
                        for (key, item) in pairs {
                            let Value::Str(key) = key else { return Err(NotJson("a non-text key".into())) };
                            map.insert(key.clone(), self.value_json(item, path)?);
                        }
                        Json::Object(map)
                    }
                    Object::Set(_) => return Err(NotJson("a set".into())),
                    Object::Empty => return Err(NotJson("an unfinished container".into())),
                };
                path.pop();
                result
            }
            Value::Bytes(_) => return Err(NotJson("bytes".into())),
            Value::Date(_) | Value::DateTime(_) => return Err(NotJson("a date".into())),
            Value::Tuple(_) => return Err(NotJson("a tuple".into())),
        })
    }
}
