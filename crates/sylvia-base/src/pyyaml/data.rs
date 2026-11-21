//! [`Data`]: a loaded YAML value as an owned tree, for code that reads, edits and writes back
//! documents (Sylvia's `config.yaml`), as Python code handles the dicts `yaml.safe_load` returns.
//!
//! [`Loaded`] keeps containers in an arena so that an alias is the same object, as in Python.
//! [`Data`] does not: a container the document reaches twice becomes two equal copies, and a
//! document that contains itself is refused (`Loaded::to_data`).

use super::value::{key_eq, Date, DateTime, Loaded, Object, ObjectId, Value};

/// One owned value, with Python's types.
#[derive(Debug, Clone, PartialEq)]
pub enum Data {
    None,
    Bool(bool),
    Int(i64),
    Float(f64),
    Str(String),
    Bytes(Vec<u8>),
    Date(Date),
    DateTime(DateTime),
    Tuple(Vec<Data>),
    List(Vec<Data>),
    /// A `dict` in insertion order.
    Dict(Vec<(Data, Data)>),
    /// A `set` in its iteration order.
    Set(Vec<Data>),
}

/// A document that contains itself, which an owned tree cannot hold.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("the document contains itself")]
pub struct Recursive;

impl Loaded {
    /// The root as an owned tree (`None` for an empty document).
    pub fn to_data(&self) -> Result<Option<Data>, Recursive> {
        self.root.as_ref().map(|root| self.data_of(root, &mut Vec::new())).transpose()
    }

    fn data_of(&self, value: &Value, path: &mut Vec<ObjectId>) -> Result<Data, Recursive> {
        Ok(match value {
            Value::None => Data::None,
            Value::Bool(flag) => Data::Bool(*flag),
            Value::Int(number) => Data::Int(*number),
            Value::Float(number) => Data::Float(*number),
            Value::Str(text) => Data::Str(text.clone()),
            Value::Bytes(bytes) => Data::Bytes(bytes.clone()),
            Value::Date(date) => Data::Date(*date),
            Value::DateTime(datetime) => Data::DateTime(*datetime),
            Value::Tuple(items) => {
                Data::Tuple(items.iter().map(|item| self.data_of(item, path)).collect::<Result<_, _>>()?)
            }
            Value::List(id) | Value::Dict(id) | Value::Set(id) => {
                if path.contains(id) {
                    return Err(Recursive);
                }
                path.push(*id);
                let data = match self.object(*id) {
                    Object::Set(items) => Data::Set(
                        items.iter().map(|item| self.data_of(item, path)).collect::<Result<_, _>>()?,
                    ),
                    Object::Dict(pairs) => Data::Dict(
                        pairs
                            .iter()
                            .map(|(key, item)| Ok((self.data_of(key, path)?, self.data_of(item, path)?)))
                            .collect::<Result<_, _>>()?,
                    ),
                    Object::List(items) => Data::List(
                        items.iter().map(|item| self.data_of(item, path)).collect::<Result<_, _>>()?,
                    ),
                    Object::Empty => Data::List(Vec::new()),
                };
                path.pop();
                data
            }
        })
    }
}

impl Data {
    /// The tree as a [`Loaded`] document, for [`super::safe_dump`]. Every container is its own
    /// object, so nothing is anchored.
    pub fn to_loaded(&self) -> Loaded {
        let mut objects = Vec::new();
        let root = Self::value_into(self, &mut objects);
        Loaded { root: Some(root), objects }
    }

    fn value_into(data: &Data, objects: &mut Vec<Object>) -> Value {
        fn container(object: Object, objects: &mut Vec<Object>) -> ObjectId {
            objects.push(object);
            ObjectId(objects.len() - 1)
        }
        match data {
            Data::None => Value::None,
            Data::Bool(flag) => Value::Bool(*flag),
            Data::Int(number) => Value::Int(*number),
            Data::Float(number) => Value::Float(*number),
            Data::Str(text) => Value::Str(text.clone()),
            Data::Bytes(bytes) => Value::Bytes(bytes.clone()),
            Data::Date(date) => Value::Date(*date),
            Data::DateTime(datetime) => Value::DateTime(*datetime),
            Data::Tuple(items) => {
                Value::Tuple(items.iter().map(|item| Self::value_into(item, objects)).collect())
            }
            Data::List(items) => {
                let built = items.iter().map(|item| Self::value_into(item, objects)).collect();
                Value::List(container(Object::List(built), objects))
            }
            Data::Set(items) => {
                let built = items.iter().map(|item| Self::value_into(item, objects)).collect();
                Value::Set(container(Object::Set(built), objects))
            }
            Data::Dict(pairs) => {
                let built = pairs
                    .iter()
                    .map(|(key, item)| (Self::value_into(key, objects), Self::value_into(item, objects)))
                    .collect();
                Value::Dict(container(Object::Dict(built), objects))
            }
        }
    }

    /// The text, if it is a `str`.
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Data::Str(text) => Some(text),
            _ => None,
        }
    }

    /// The pairs, if it is a `dict`.
    pub fn as_dict(&self) -> Option<&[(Data, Data)]> {
        match self {
            Data::Dict(pairs) => Some(pairs),
            _ => None,
        }
    }

    /// The pairs to change, if it is a `dict`.
    pub fn as_dict_mut(&mut self) -> Option<&mut Vec<(Data, Data)>> {
        match self {
            Data::Dict(pairs) => Some(pairs),
            _ => None,
        }
    }

    /// `mapping.get(key)` for a `dict` (Python's key equality).
    pub fn get(&self, key: &Data) -> Option<&Data> {
        self.as_dict()?.iter().find(|(existing, _)| data_key_eq(existing, key)).map(|(_, value)| value)
    }

    /// `mapping.get("key")`.
    pub fn get_str(&self, key: &str) -> Option<&Data> {
        self.get(&Data::Str(key.to_owned()))
    }

    /// Whether it is hashable (`collections.abc.Hashable`).
    pub fn hashable(&self) -> bool {
        match self {
            Data::List(_) | Data::Dict(_) | Data::Set(_) => false,
            Data::Tuple(items) => items.iter().all(Data::hashable),
            _ => true,
        }
    }
}

/// Python's `==` for two hashable values, as a `dict` compares keys ([`key_eq`]).
pub fn data_key_eq(a: &Data, b: &Data) -> bool {
    fn scalar(data: &Data) -> Option<Value> {
        Some(match data {
            Data::None => Value::None,
            Data::Bool(flag) => Value::Bool(*flag),
            Data::Int(number) => Value::Int(*number),
            Data::Float(number) => Value::Float(*number),
            Data::Str(text) => Value::Str(text.clone()),
            Data::Bytes(bytes) => Value::Bytes(bytes.clone()),
            Data::Date(date) => Value::Date(*date),
            Data::DateTime(datetime) => Value::DateTime(*datetime),
            _ => return None,
        })
    }
    match (a, b) {
        (Data::Tuple(x), Data::Tuple(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(p, q)| data_key_eq(p, q))
        }
        _ => match (scalar(a), scalar(b)) {
            (Some(x), Some(y)) => key_eq(&x, &y),
            _ => false,
        },
    }
}
