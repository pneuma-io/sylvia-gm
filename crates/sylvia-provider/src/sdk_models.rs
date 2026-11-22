//! The Stainless-generated SDKs' models (OpenAI's and Anthropic's): how a provider transport
//! reads a response the way the Python SDK built it. Lane 5.
//!
//! Both SDKs turn response JSON into their models leniently (`construct_type`, `construct`):
//! - a declared field the JSON lacks reads as `None`;
//! - a name the model does not declare reads as an extra field the JSON carries, or else raises
//!   `AttributeError` (so `getattr(value, name, default)` gives the default);
//! - a value whose declared type is not a model stays a plain value: a dict has no attributes;
//! - a union of models is resolved by its `type` field, else to its first variant.
//!
//! `model_dump(mode="json", exclude_none=True)` of such a model leaves out only the model's own
//! keys whose value is `None` (declared or extra), writes declared fields first, in declaration
//! order and under their names, then extra fields as sent, and an integer in a `float` field as a
//! float. Values that are not models (list items, `Dict` and `Any` values, extras) are kept as
//! sent, `None`s included.
//!
//! A [`Table`] holds one SDK's models, generated from the locked SDK (`generate_*_models.py`).
//! Each SDK's validation step, which can coerce a mistyped value before construction, is not
//! reproduced (differences row 39).

use sylvia_base::{Json, JsonMap};

/// A declared type's shape.
#[derive(Debug, Clone)]
pub(crate) enum Shape {
    Model(String),
    List(Box<Shape>),
    Dict(Box<Shape>),
    Union { variants: Vec<(String, String)>, first: String },
    Either(Vec<Shape>),
    Float,
    Any,
}

/// One declared field: its name, the JSON key it is read from, and its shape.
#[derive(Debug)]
struct Field {
    name: String,
    key: String,
    shape: Shape,
}

/// One SDK's model table: every model's declared fields, and the named roots the transport
/// starts from.
pub struct Table {
    models: std::collections::HashMap<String, Vec<Field>>,
    roots: std::collections::HashMap<String, Shape>,
    metadata: JsonMap,
}

fn shape(value: &Json) -> Shape {
    match value {
        Json::String(kind) if kind == "float" => Shape::Float,
        Json::Object(map) => {
            if let Some(Json::String(name)) = map.get("model") {
                Shape::Model(name.clone())
            } else if let Some(inner) = map.get("list") {
                Shape::List(Box::new(shape(inner)))
            } else if let Some(inner) = map.get("dict") {
                Shape::Dict(Box::new(shape(inner)))
            } else if let Some(Json::Object(variants)) = map.get("union") {
                Shape::Union {
                    variants: variants
                        .iter()
                        .map(|(kind, model)| (kind.clone(), model.as_str().unwrap_or_default().to_owned()))
                        .collect(),
                    first: map.get("first").and_then(Json::as_str).unwrap_or_default().to_owned(),
                }
            } else if let Some(Json::Array(options)) = map.get("either") {
                Shape::Either(options.iter().map(shape).collect())
            } else {
                Shape::Any
            }
        }
        _ => Shape::Any,
    }
}

impl Table {
    /// A generated table: `{"models": {...}, "<root>": shape, ..., "<package>": "<version>"}`.
    pub fn parse(text: &str) -> Self {
        let table: Json = serde_json::from_str(text).expect("the model table is JSON");
        let table = table.as_object().expect("the model table is an object");
        let models = table["models"]
            .as_object()
            .expect("models")
            .iter()
            .map(|(name, fields)| {
                let fields = fields
                    .as_array()
                    .expect("fields")
                    .iter()
                    .map(|field| Field {
                        name: field[0].as_str().expect("name").to_owned(),
                        key: field[1].as_str().expect("key").to_owned(),
                        shape: shape(&field[2]),
                    })
                    .collect();
                (name.clone(), fields)
            })
            .collect();
        let roots = table
            .iter()
            .filter(|(key, value)| *key != "models" && value.is_object())
            .map(|(key, value)| (key.clone(), shape(value)))
            .collect();
        let metadata = table
            .iter()
            .filter(|(_, value)| value.is_string())
            .map(|(k, v)| (k.clone(), v.clone()))
            .collect();
        Self { models, roots, metadata }
    }

    /// A version the table records (`openai`, `anthropic`, `pydantic`, `python`).
    pub fn version(&self, name: &str) -> &str {
        self.metadata.get(name).and_then(Json::as_str).unwrap_or_default()
    }

    pub(crate) fn root(&self, name: &str) -> &Shape {
        self.roots.get(name).unwrap_or_else(|| panic!("the model table has no root {name}"))
    }
}

static NULL: Json = Json::Null;

/// A JSON value as the SDK constructed it: the model it became, if any.
#[derive(Clone, Copy)]
pub(crate) struct Node<'a> {
    table: &'a Table,
    /// The JSON value.
    pub(crate) value: &'a Json,
    shape: Option<&'a Shape>,
}

impl<'a> Node<'a> {
    /// `value` constructed as the table's root `root`.
    pub(crate) fn root(table: &'a Table, value: &'a Json, root: &str) -> Self {
        Self { table, value, shape: Some(table.root(root)) }
    }

    /// `value`, a list, constructed as a list of the table's root `root`.
    pub(crate) fn list_of(table: &'a Table, value: &'a Json, root: &str) -> Option<Vec<Node<'a>>> {
        let Json::Array(items) = value else { return None };
        let shape = table.root(root);
        Some(items.iter().map(|value| Node { table, value, shape: Some(shape) }).collect())
    }

    /// The shape that applies to this value: an `Either` matched by the value's own kind.
    fn resolved(shape: &'a Shape, value: &'a Json) -> Option<&'a Shape> {
        match shape {
            Shape::Either(options) => options
                .iter()
                .find(|option| match (option, value) {
                    (Shape::List(_), Json::Array(_)) => true,
                    (Shape::Model(_) | Shape::Union { .. } | Shape::Dict(_), Json::Object(_)) => true,
                    (Shape::Float, Json::Number(_)) => true,
                    (Shape::Any, _) => !matches!(value, Json::Array(_) | Json::Object(_)),
                    _ => false,
                })
                .and_then(|option| Self::resolved(option, value)),
            other => Some(other),
        }
    }

    /// The model this value was constructed as: a declared model or union on a JSON object.
    pub(crate) fn model(&self) -> Option<&'a str> {
        let Json::Object(map) = self.value else { return None };
        match self.shape.and_then(|shape| Self::resolved(shape, self.value))? {
            Shape::Model(name) => Some(name),
            Shape::Union { variants, first } => Some(match map.get("type") {
                Some(Json::String(kind)) => variants
                    .iter()
                    .find(|(variant, _)| variant == kind)
                    .map_or(first.as_str(), |(_, model)| model),
                _ => first,
            }),
            _ => None,
        }
    }

    /// Whether the value is `None`.
    pub(crate) fn is_none(&self) -> bool {
        self.value.is_null()
    }

    /// `getattr(value, name)`: a declared field (`None` when the JSON lacks it), an extra field
    /// the JSON carries, or `None` for an `AttributeError` (a plain dict, string or list, or a
    /// name the model does not have).
    pub(crate) fn attr(&self, name: &str) -> Option<Node<'a>> {
        let model = self.model()?;
        let map = self.value.as_object()?;
        let fields = self.table.models.get(model)?;
        if let Some(field) = fields.iter().find(|field| field.name == name) {
            return Some(Node {
                table: self.table,
                value: map.get(&field.key).unwrap_or(&NULL),
                shape: Some(&field.shape),
            });
        }
        if fields.iter().any(|field| field.key == name) {
            return None;
        }
        map.get(name).map(|value| Node { table: self.table, value, shape: None })
    }

    /// `getattr(value, name, default)` as a JSON value.
    pub(crate) fn attr_or(&self, name: &str, default: &'a Json) -> &'a Json {
        self.attr(name).map_or(default, |node| node.value)
    }

    /// The items `for item in value` gives for a list, each with its declared shape.
    pub(crate) fn items(&self) -> Option<Vec<Node<'a>>> {
        let Json::Array(items) = self.value else { return None };
        let inner = match self.shape.and_then(|shape| Self::resolved(shape, self.value)) {
            Some(Shape::List(inner)) => Some(inner.as_ref()),
            _ => None,
        };
        Some(items.iter().map(|value| Node { table: self.table, value, shape: inner }).collect())
    }

    /// `model_dump(mode="json", exclude_none=True)` of this value as constructed (see the module).
    pub(crate) fn dump(&self) -> Json {
        if let Some(model) = self.model() {
            return self.dump_model(self.value.as_object().expect("a model is an object"), model);
        }
        match (self.shape.and_then(|shape| Self::resolved(shape, self.value)), self.value) {
            (Some(Shape::List(inner)), Json::Array(items)) => Json::Array(
                items
                    .iter()
                    .map(|value| Node { table: self.table, value, shape: Some(inner) }.dump())
                    .collect(),
            ),
            (Some(Shape::Dict(inner)), Json::Object(map)) => Json::Object(
                map.iter()
                    .map(|(key, value)| {
                        (key.clone(), Node { table: self.table, value, shape: Some(inner) }.dump())
                    })
                    .collect(),
            ),
            (Some(Shape::Float), Json::Number(number)) if !number.is_f64() => number
                .as_f64()
                .and_then(serde_json::Number::from_f64)
                .map_or_else(|| self.value.clone(), Json::Number),
            _ => self.value.clone(),
        }
    }

    fn dump_model(&self, map: &JsonMap, model: &str) -> Json {
        let Some(fields) = self.table.models.get(model) else { return Json::Object(map.clone()) };
        let mut out = JsonMap::new();
        for field in fields {
            if let Some(value) = map.get(&field.key).filter(|value| !value.is_null()) {
                out.insert(
                    field.name.clone(),
                    Node { table: self.table, value, shape: Some(&field.shape) }.dump(),
                );
            }
        }
        for (key, value) in map {
            if !fields.iter().any(|field| &field.key == key) && !value.is_null() {
                out.insert(key.clone(), value.clone());
            }
        }
        Json::Object(out)
    }
}
