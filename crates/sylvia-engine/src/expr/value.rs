//! The Python values an expression reads and produces.
//!
//! The evaluator works on what the runtime puts in its context: JSON-shaped state and
//! definitions (all dict keys are strings in every shipped package), tuples (`RollResult.results`),
//! the dice result dataclasses, and the attributes `getattr` finds on `str`, `int`, `float` and
//! `bool` (`x.real`, `x.upper`). Containers are reference-counted, so passing a large definitions
//! table around does not copy it. Integers are `i64`; anything beyond is refused
//! (`differences.md` row 5).

use crate::dice::{CheckResult, HungerPoolResult, PoolResult, RollResult};
use indexmap::IndexMap;
use std::sync::Arc;
use sylvia_base::pyjson::float_repr;
use sylvia_base::pystr::repr as str_repr;
use sylvia_base::Json;

/// A Python `dict` with string keys, in insertion order.
pub type Dict = IndexMap<String, Value>;

/// One Python value.
#[derive(Debug, Clone)]
pub enum Value {
    /// `None`.
    None,
    /// `bool`.
    Bool(bool),
    /// `int` within `i64`.
    Int(i64),
    /// `float`.
    Float(f64),
    /// `str`.
    Str(Arc<str>),
    /// `list`.
    List(Arc<Vec<Value>>),
    /// `tuple`.
    Tuple(Arc<Vec<Value>>),
    /// `dict`.
    Dict(Arc<Dict>),
    /// A dataclass instance (the dice results).
    Object(Arc<Object>),
    /// A bound method found by attribute access. Python can only compare, test and print it.
    Method(Arc<Method>),
}

/// A frozen dataclass instance: its class name and fields in declaration order.
#[derive(Debug, Clone, PartialEq)]
pub struct Object {
    /// `type(value).__name__`.
    pub class: &'static str,
    /// `(name, value)` for each dataclass field.
    pub fields: Vec<(&'static str, Value)>,
    /// The class's public methods (`to_dict`).
    pub methods: &'static [&'static str],
}

/// A bound method: `<owner>.<name>`.
#[derive(Debug, Clone, PartialEq)]
pub struct Method {
    /// The value the method is bound to.
    pub owner: Value,
    /// The method's name.
    pub name: String,
}

impl PartialEq for Value {
    /// Structural equality for tests. Python's `==` is [`py_eq`].
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::None, Self::None) => true,
            (Self::Bool(a), Self::Bool(b)) => a == b,
            (Self::Int(a), Self::Int(b)) => a == b,
            (Self::Float(a), Self::Float(b)) => a.to_bits() == b.to_bits(),
            (Self::Str(a), Self::Str(b)) => a == b,
            (Self::List(a), Self::List(b)) | (Self::Tuple(a), Self::Tuple(b)) => a == b,
            (Self::Dict(a), Self::Dict(b)) => {
                a.len() == b.len() && a.iter().zip(b.iter()).all(|(x, y)| x == y)
            }
            (Self::Object(a), Self::Object(b)) => a == b,
            (Self::Method(a), Self::Method(b)) => a == b,
            _ => false,
        }
    }
}

impl Value {
    /// A `str`.
    pub fn str(text: impl Into<Arc<str>>) -> Self {
        Self::Str(text.into())
    }

    /// A `list`.
    pub fn list(items: Vec<Value>) -> Self {
        Self::List(Arc::new(items))
    }

    /// A `tuple`.
    pub fn tuple(items: Vec<Value>) -> Self {
        Self::Tuple(Arc::new(items))
    }

    /// A `dict`.
    pub fn dict(map: Dict) -> Self {
        Self::Dict(Arc::new(map))
    }

    /// `type(value).__name__`.
    pub fn type_name(&self) -> &'static str {
        match self {
            Self::None => "NoneType",
            Self::Bool(_) => "bool",
            Self::Int(_) => "int",
            Self::Float(_) => "float",
            Self::Str(_) => "str",
            Self::List(_) => "list",
            Self::Tuple(_) => "tuple",
            Self::Dict(_) => "dict",
            Self::Object(object) => object.class,
            Self::Method(method) => match method.owner {
                Self::Object(_) => "method",
                _ => "builtin_function_or_method",
            },
        }
    }

    /// The items of a `list` or `tuple`.
    pub fn sequence(&self) -> Option<&[Value]> {
        match self {
            Self::List(items) | Self::Tuple(items) => Some(items),
            _ => None,
        }
    }

    /// Whether the value is `int` or `float` (not `bool`).
    pub fn is_number(&self) -> bool {
        matches!(self, Self::Int(_) | Self::Float(_))
    }

    /// `repr(value)`.
    pub fn repr(&self) -> String {
        let mut out = String::new();
        self.write_repr(&mut out);
        out
    }

    fn write_repr(&self, out: &mut String) {
        match self {
            Self::None => out.push_str("None"),
            Self::Bool(true) => out.push_str("True"),
            Self::Bool(false) => out.push_str("False"),
            Self::Int(value) => out.push_str(&value.to_string()),
            Self::Float(value) => out.push_str(&float_text(*value)),
            Self::Str(text) => out.push_str(&str_repr(text)),
            Self::List(items) => {
                out.push('[');
                write_items(out, items);
                out.push(']');
            }
            Self::Tuple(items) => {
                out.push('(');
                write_items(out, items);
                if items.len() == 1 {
                    out.push(',');
                }
                out.push(')');
            }
            Self::Dict(map) => {
                out.push('{');
                for (index, (key, item)) in map.iter().enumerate() {
                    if index > 0 {
                        out.push_str(", ");
                    }
                    out.push_str(&str_repr(key));
                    out.push_str(": ");
                    item.write_repr(out);
                }
                out.push('}');
            }
            Self::Object(object) => {
                out.push_str(object.class);
                out.push('(');
                for (index, (name, item)) in object.fields.iter().enumerate() {
                    if index > 0 {
                        out.push_str(", ");
                    }
                    out.push_str(name);
                    out.push('=');
                    item.write_repr(out);
                }
                out.push(')');
            }
            Self::Method(method) => match &method.owner {
                Self::Object(object) => {
                    out.push_str(&format!("<bound method {}.{} of ", object.class, method.name));
                    method.owner.write_repr(out);
                    out.push('>');
                }
                // CPython prints the object's address here, which no two runs share
                // (`differences.md`).
                owner => out.push_str(&format!(
                    "<built-in method {} of {} object>",
                    method.name,
                    owner.type_name()
                )),
            },
        }
    }

    /// `str(value)`: the text itself for a `str`, otherwise `repr`.
    pub fn to_str(&self) -> String {
        match self {
            Self::Str(text) => text.to_string(),
            other => other.repr(),
        }
    }

    /// `bool(value)` as `_to_bool` decides it.
    pub fn truthy(&self) -> bool {
        match self {
            Self::None => false,
            Self::Bool(flag) => *flag,
            Self::Int(value) => *value != 0,
            Self::Float(value) => *value != 0.0,
            Self::Str(text) => !text.is_empty(),
            Self::List(items) | Self::Tuple(items) => !items.is_empty(),
            Self::Dict(map) => !map.is_empty(),
            Self::Object(_) | Self::Method(_) => true,
        }
    }

    /// The value of a JSON document as `json.loads` gives it to Python.
    pub fn from_json(json: &Json) -> Self {
        match json {
            Json::Null => Self::None,
            Json::Bool(flag) => Self::Bool(*flag),
            Json::Number(number) => match number.as_i64() {
                Some(int) => Self::Int(int),
                None => Self::Float(number.as_f64().expect("a JSON number")),
            },
            Json::String(text) => Self::str(text.as_str()),
            Json::Array(items) => Self::list(items.iter().map(Self::from_json).collect()),
            Json::Object(map) => {
                Self::dict(map.iter().map(|(key, item)| (key.clone(), Self::from_json(item))).collect())
            }
        }
    }
}

fn write_items(out: &mut String, items: &[Value]) {
    for (index, item) in items.iter().enumerate() {
        if index > 0 {
            out.push_str(", ");
        }
        item.write_repr(out);
    }
}

/// `repr(float)`, including `inf`, `-inf` and `nan`.
pub fn float_text(value: f64) -> String {
    if value.is_nan() {
        "nan".to_owned()
    } else if value.is_infinite() {
        if value > 0.0 { "inf" } else { "-inf" }.to_owned()
    } else {
        float_repr(value)
    }
}

fn ints(values: &[i64]) -> Value {
    Value::tuple(values.iter().map(|v| Value::Int(*v)).collect())
}

const TO_DICT: &[&str] = &["to_dict"];

impl From<&RollResult> for Value {
    fn from(roll: &RollResult) -> Self {
        Self::Object(Arc::new(Object {
            class: "RollResult",
            fields: vec![
                ("sides", Self::Int(roll.sides)),
                ("count", Self::Int(roll.count)),
                ("results", ints(&roll.results)),
                ("total", Self::Int(roll.total)),
                ("modifier", Self::Int(roll.modifier)),
            ],
            methods: TO_DICT,
        }))
    }
}

impl From<&CheckResult> for Value {
    fn from(check: &CheckResult) -> Self {
        Self::Object(Arc::new(Object {
            class: "CheckResult",
            fields: vec![
                ("success", Self::Bool(check.success)),
                ("roll_result", Self::from(&check.roll_result)),
                ("dc", Self::Int(check.dc)),
                ("margin", Self::Int(check.margin)),
            ],
            methods: TO_DICT,
        }))
    }
}

impl From<&PoolResult> for Value {
    fn from(pool: &PoolResult) -> Self {
        Self::Object(Arc::new(Object {
            class: "PoolResult",
            fields: vec![
                ("sides", Self::Int(pool.sides)),
                ("count", Self::Int(pool.count)),
                ("results", ints(&pool.results)),
                ("threshold", Self::Int(pool.threshold)),
                ("successes", Self::Int(pool.successes)),
                ("failures", Self::Int(pool.failures)),
                ("botches", Self::Int(pool.botches)),
                ("modifier", Self::Int(pool.modifier)),
            ],
            methods: TO_DICT,
        }))
    }
}

impl From<&HungerPoolResult> for Value {
    fn from(pool: &HungerPoolResult) -> Self {
        Self::Object(Arc::new(Object {
            class: "HungerPoolResult",
            fields: vec![
                ("regular_results", ints(&pool.regular_results)),
                ("hunger_results", ints(&pool.hunger_results)),
                ("successes", Self::Int(pool.successes)),
                ("failures", Self::Int(pool.failures)),
                ("bestial_failures", Self::Int(pool.bestial_failures)),
                ("messy_criticals", Self::Int(pool.messy_criticals)),
                ("threshold", Self::Int(pool.threshold)),
                ("modifier", Self::Int(pool.modifier)),
            ],
            methods: TO_DICT,
        }))
    }
}

/// Python's `==`.
///
/// `int`, `float` and `bool` compare by numeric value (`1 == 1.0 == True`); a `list` never
/// equals a `tuple`; dicts compare by keys and values regardless of order; dataclasses compare
/// field by field within one class. Python's `in` also matches the very same object first,
/// which only differs for a NaN.
pub fn py_eq(left: &Value, right: &Value) -> bool {
    use Value as V;
    match (left, right) {
        (V::None, V::None) => true,
        (V::Str(a), V::Str(b)) => a == b,
        (V::List(a), V::List(b)) | (V::Tuple(a), V::Tuple(b)) => {
            a.len() == b.len() && a.iter().zip(b.iter()).all(|(x, y)| py_eq(x, y))
        }
        (V::Dict(a), V::Dict(b)) => {
            a.len() == b.len() && a.iter().all(|(key, x)| b.get(key).is_some_and(|y| py_eq(x, y)))
        }
        (V::Object(a), V::Object(b)) => {
            a.class == b.class && a.fields.iter().zip(b.fields.iter()).all(|((_, x), (_, y))| py_eq(x, y))
        }
        (V::Method(a), V::Method(b)) => a.name == b.name && py_eq(&a.owner, &b.owner),
        _ => match (numeric(left), numeric(right)) {
            (Some(Numeric::Int(a)), Some(Numeric::Int(b))) => a == b,
            (Some(a), Some(b)) => numeric_eq(a, b),
            _ => false,
        },
    }
}

/// An `int` (or `bool`) or `float`, for arithmetic and comparison.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Numeric {
    /// `int` / `bool`.
    Int(i64),
    /// `float`.
    Float(f64),
}

/// The number a value stands for: `bool` counts as `int`.
pub fn numeric(value: &Value) -> Option<Numeric> {
    match value {
        Value::Bool(flag) => Some(Numeric::Int(i64::from(*flag))),
        Value::Int(int) => Some(Numeric::Int(*int)),
        Value::Float(float) => Some(Numeric::Float(*float)),
        _ => None,
    }
}

/// Exact `==` between an int and a float, as Python compares them.
fn numeric_eq(a: Numeric, b: Numeric) -> bool {
    numeric_cmp(a, b) == Some(std::cmp::Ordering::Equal)
}

/// Exact ordering between numbers, as Python compares `int` with `float` (no rounding of the
/// integer); `None` when a NaN is involved.
pub fn numeric_cmp(a: Numeric, b: Numeric) -> Option<std::cmp::Ordering> {
    use std::cmp::Ordering;
    match (a, b) {
        (Numeric::Int(x), Numeric::Int(y)) => Some(x.cmp(&y)),
        (Numeric::Float(x), Numeric::Float(y)) => x.partial_cmp(&y),
        (Numeric::Int(x), Numeric::Float(y)) => int_float_cmp(x, y),
        (Numeric::Float(x), Numeric::Int(y)) => int_float_cmp(y, x).map(Ordering::reverse),
    }
}

fn int_float_cmp(int: i64, float: f64) -> Option<std::cmp::Ordering> {
    use std::cmp::Ordering;
    if float.is_nan() {
        return None;
    }
    if float >= 9.223_372_036_854_776e18 {
        return Some(Ordering::Less);
    }
    if float < -9.223_372_036_854_776e18 {
        return Some(Ordering::Greater);
    }
    // `float` lies inside the i64 range: compare with its integer part, then its fraction.
    let whole = float.trunc();
    #[allow(clippy::cast_possible_truncation)]
    let whole_int = whole as i64;
    match int.cmp(&whole_int) {
        Ordering::Equal => {
            let fraction = float - whole;
            Some(if fraction > 0.0 {
                Ordering::Less
            } else if fraction < 0.0 {
                Ordering::Greater
            } else {
                Ordering::Equal
            })
        }
        other => Some(other),
    }
}
