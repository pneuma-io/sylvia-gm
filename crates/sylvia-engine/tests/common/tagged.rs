//! The tagged JSON the expression vector generators write for Python values.
//!
//! Shared by several test binaries, each of which uses only part of it.
#![allow(dead_code)]

use serde_json::{json, Value as Json};
use std::sync::Arc;
use sylvia_engine::expr::value::{float_text, Method, Object};
use sylvia_engine::expr::{Dict, Value};

const TO_DICT: &[&str] = &["to_dict"];

fn class_name(name: &str) -> &'static str {
    match name {
        "RollResult" => "RollResult",
        "CheckResult" => "CheckResult",
        "PoolResult" => "PoolResult",
        "HungerPoolResult" => "HungerPoolResult",
        other => panic!("vector holds an unknown class {other}"),
    }
}

/// Parse Python's float `repr`.
pub fn parse_float(text: &str) -> f64 {
    match text {
        "inf" => f64::INFINITY,
        "-inf" => f64::NEG_INFINITY,
        "nan" => f64::NAN,
        other => other.parse().expect("a float repr"),
    }
}

/// The Python value a tagged JSON value stands for.
pub fn decode(json: &Json) -> Value {
    match json {
        Json::Null => Value::None,
        Json::Bool(flag) => Value::Bool(*flag),
        Json::Number(number) => Value::Int(number.as_i64().expect("an i64 in the vectors")),
        Json::String(text) => Value::str(text.as_str()),
        Json::Array(items) => Value::list(items.iter().map(decode).collect()),
        Json::Object(map) => {
            if let Some(text) = map.get("$float") {
                return Value::Float(parse_float(text.as_str().unwrap()));
            }
            if let Some(items) = map.get("$tuple") {
                return Value::tuple(items.as_array().unwrap().iter().map(decode).collect());
            }
            if let Some(class) = map.get("$object") {
                let class = class_name(class.as_str().unwrap());
                let fields = map["fields"]
                    .as_object()
                    .unwrap()
                    .iter()
                    .map(|(name, value)| (&*Box::leak(name.clone().into_boxed_str()), decode(value)))
                    .collect();
                return Value::Object(Arc::new(Object { class, fields, methods: TO_DICT }));
            }
            if let Some(name) = map.get("$method") {
                return Value::Method(Arc::new(Method {
                    owner: decode(&map["owner"]),
                    name: name.as_str().unwrap().to_owned(),
                }));
            }
            let dict: Dict = map.iter().map(|(key, value)| (key.clone(), decode(value))).collect();
            Value::dict(dict)
        }
    }
}

/// The tagged JSON for a Rust value.
pub fn encode(value: &Value) -> Json {
    match value {
        Value::None => Json::Null,
        Value::Bool(flag) => json!(flag),
        Value::Int(int) => json!(int),
        Value::Float(float) => json!({"$float": float_text(*float)}),
        Value::Str(text) => json!(text.as_ref()),
        Value::List(items) => Json::Array(items.iter().map(encode).collect()),
        Value::Tuple(items) => json!({"$tuple": items.iter().map(encode).collect::<Vec<_>>()}),
        Value::Dict(map) => Json::Object(map.iter().map(|(key, item)| (key.clone(), encode(item))).collect()),
        Value::Object(object) => {
            let fields: serde_json::Map<String, Json> =
                object.fields.iter().map(|(name, item)| ((*name).to_owned(), encode(item))).collect();
            json!({"$object": object.class, "fields": fields})
        }
        Value::Method(method) => json!({"$method": method.name, "owner": encode(&method.owner)}),
    }
}

/// A decoded dict.
pub fn decode_dict(json: &Json) -> Dict {
    match decode(json) {
        Value::Dict(map) => (*map).clone(),
        other => panic!("expected a dict, got {other:?}"),
    }
}
