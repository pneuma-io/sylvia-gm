//! JSON Schema Draft 2020-12 validation with the errors `jsonschema` 4.26 produces.
//!
//! Four Python callers use `jsonschema`: content validation (`validate_content_shape`, with
//! Sylvia's strict type checker), tool argument contracts (the GM model reads
//! `error.message`), campaign state validation (messages go into error text) and runtime
//! parameter declarations (verdicts only). This one engine serves all four:
//!
//! - keywords run in the schema object's key order, as `iter_errors` does;
//! - each keyword yields its errors the way `jsonschema/_keywords.py` does, with the same
//!   `validator`, `validator_value`, instance, paths, union `context` and `message` text;
//! - `is_valid` probes (`not`, `if`, `contains`, `oneOf`) stop at the first error;
//! - `$ref` resolves like `referencing`: a `#/...` pointer within the current resource, a URI
//!   to the resource with that `$id`, and a subschema with its own `$id` starts a resource.
//!
//! The keywords implemented are those the shipped content definitions, the admitted
//! authoring subset, the shipped packages' tool schemas and the state schema use. A schema
//! with another Draft 2020-12 validation keyword is refused when it is compiled, never
//! silently ignored. `format` does nothing, as in Python without a format checker.
//!
//! One declared difference (docs/rust-port/differences.md): for a schema-valued
//! `additionalProperties`, Python visits the extra members in `set` order, which depends on
//! hash randomisation, so its error order varies between runs. This engine visits them in the
//! instance's member order, one of the orders Python can produce.

use super::pyregex::PyRegex;
use crate::error::{DomainError, DomainResult};
use std::borrow::Cow;
use std::cmp::Ordering;
use std::collections::{HashMap, VecDeque};
use sylvia_base::pystr::{self, repr, repr_json};
use sylvia_base::{Json, JsonMap};

/// Which `jsonschema` type checker the Python caller uses.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TypeChecker {
    /// `Draft202012Validator`'s own: `1.0` is an `integer`.
    Draft202012,
    /// `content_validation._ContentValidator`'s: `integer` is exactly a Python `int`.
    Content,
}

/// One element of an instance path: a member name or an array index.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PathPart {
    Key(String),
    Index(usize),
}

impl std::fmt::Display for PathPart {
    /// `str(part)`, as callers join `absolute_path`.
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PathPart::Key(key) => formatter.write_str(key),
            PathPart::Index(index) => write!(formatter, "{index}"),
        }
    }
}

/// One element of a schema path: a keyword, a member name or an array index.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum SchemaPart {
    Name(String),
    Index(usize),
}

/// What a message needs beyond the keyword, its value and the instance.
#[derive(Debug, Clone)]
enum Detail<'a> {
    None,
    Required(&'a Json),
    Dependency {
        each: &'a Json,
        property: &'a str,
    },
    /// `oneOf` matched more than once: the other valid branches, then the first valid one.
    MoreValid(Vec<&'a Json>),
    Matches(usize),
}

/// One validation error, with the fields of `jsonschema.ValidationError`.
#[derive(Debug, Clone)]
pub struct SchemaError<'a> {
    /// `error.validator`: the keyword, or `None` for a `false` schema.
    pub keyword: Option<&'a str>,
    /// `error.validator_value`.
    pub validator_value: Cow<'a, Json>,
    /// `error.instance`.
    pub instance: Cow<'a, Json>,
    /// `error.schema`: the schema object (or boolean) holding the keyword.
    pub schema: &'a Json,
    /// `error.relative_path`.
    pub path: VecDeque<PathPart>,
    /// `error.relative_schema_path`.
    pub schema_path: VecDeque<SchemaPart>,
    /// `error.context`: the branch errors of `anyOf` / `oneOf`.
    pub context: Vec<SchemaError<'a>>,
    detail: Detail<'a>,
}

/// `str(value)` of a keyword's numeric bound in a message.
fn bound_text(value: &Json) -> String {
    repr_json(value)
}

/// Python `value == number` for a JSON number bound (`1.0 == 1`).
fn equals_number(value: &Json, number: u64) -> bool {
    value.as_f64() == Some(number as f64)
}

impl SchemaError<'_> {
    /// `error.message`, the text `jsonschema` 4.26 gives.
    pub fn message(&self) -> String {
        let instance = self.instance.as_ref();
        let value = self.validator_value.as_ref();
        let shown = || repr_json(instance);
        let Some(keyword) = self.keyword else {
            return format!("False schema does not allow {}", shown());
        };
        match keyword {
            "type" => {
                let names: Vec<String> = match value {
                    Json::Array(names) => names.iter().map(repr_json).collect(),
                    other => vec![repr_json(other)],
                };
                format!("{} is not of type {}", shown(), names.join(", "))
            }
            "required" => match self.detail {
                Detail::Required(property) => format!("{} is a required property", repr_json(property)),
                _ => unreachable!("a required error names its property"),
            },
            "additionalProperties" => {
                let declared = self.schema.get("properties").and_then(Json::as_object);
                let mut extras: Vec<&String> = instance
                    .as_object()
                    .map(|members| {
                        members.keys().filter(|key| declared.is_none_or(|d| !d.contains_key(*key))).collect()
                    })
                    .unwrap_or_default();
                extras.sort();
                let verb = if extras.len() == 1 { "was" } else { "were" };
                let joined: Vec<String> = extras.iter().map(|extra| repr(extra)).collect();
                format!("Additional properties are not allowed ({} {verb} unexpected)", joined.join(", "))
            }
            "items" => {
                let items = instance.as_array().map(Vec::as_slice).unwrap_or_default();
                let rest = if items.len() != 1 { instance.clone() } else { items[0].clone() };
                format!("Expected at most 0 items but found {} extra: {}", items.len(), repr_json(&rest))
            }
            "const" => format!("{} was expected", repr_json(value)),
            "enum" => format!("{} is not one of {}", shown(), repr_json(value)),
            "contains" => format!("{} does not contain items matching the given schema", shown()),
            "minContains" => match self.detail {
                Detail::Matches(matches) => format!(
                    "Too few items match the given schema (expected at least {} but only {matches} matched)",
                    bound_text(value)
                ),
                _ => unreachable!("a minContains error counts its matches"),
            },
            "maxContains" => {
                format!("Too many items match the given schema (expected at most {})", bound_text(value))
            }
            "minimum" => format!("{} is less than the minimum of {}", shown(), repr_json(value)),
            "maximum" => format!("{} is greater than the maximum of {}", shown(), repr_json(value)),
            "exclusiveMinimum" => {
                format!("{} is less than or equal to the minimum of {}", shown(), repr_json(value))
            }
            "exclusiveMaximum" => {
                format!("{} is greater than or equal to the maximum of {}", shown(), repr_json(value))
            }
            "minItems" | "minLength" => {
                let text = if equals_number(value, 1) { "should be non-empty" } else { "is too short" };
                format!("{} {text}", shown())
            }
            "maxItems" | "maxLength" => {
                let text = if equals_number(value, 0) { "is expected to be empty" } else { "is too long" };
                format!("{} {text}", shown())
            }
            "minProperties" => {
                let text = if equals_number(value, 1) {
                    "should be non-empty"
                } else {
                    "does not have enough properties"
                };
                format!("{} {text}", shown())
            }
            "maxProperties" => {
                let text = if equals_number(value, 0) {
                    "is expected to be empty"
                } else {
                    "has too many properties"
                };
                format!("{} {text}", shown())
            }
            "uniqueItems" => format!("{} has non-unique elements", shown()),
            "pattern" => format!("{} does not match {}", shown(), repr_json(value)),
            "dependentRequired" => match self.detail {
                Detail::Dependency { each, property } => {
                    format!("{} is a dependency of {}", repr_json(each), repr(property))
                }
                _ => unreachable!("a dependentRequired error names its dependency"),
            },
            "anyOf" | "oneOf" => match &self.detail {
                Detail::MoreValid(schemas) => {
                    let shown_schemas: Vec<String> = schemas.iter().map(|schema| repr_json(schema)).collect();
                    format!("{} is valid under each of {}", shown(), shown_schemas.join(", "))
                }
                _ => format!("{} is not valid under any of the given schemas", shown()),
            },
            "not" => format!("{} should not be valid under {}", shown(), repr_json(value)),
            other => unreachable!("no message for keyword {other}"),
        }
    }
}

static NULL: Json = Json::Null;

/// A compiled root schema.
#[derive(Debug)]
pub struct Validator<'a> {
    root: &'a Json,
    checker: TypeChecker,
    resources: HashMap<&'a str, &'a Json>,
    patterns: HashMap<&'a str, PyRegex>,
}

const SUBSCHEMA_MAPS: &[&str] = &["properties", "$defs", "dependentSchemas", "patternProperties"];
const SUBSCHEMA_LISTS: &[&str] = &["allOf", "anyOf", "oneOf", "prefixItems"];
const SUBSCHEMA_SINGLE: &[&str] =
    &["items", "additionalProperties", "not", "if", "then", "else", "contains", "propertyNames"];
/// Draft 2020-12 validation keywords this engine does not implement.
const UNSUPPORTED: &[&str] = &[
    "$dynamicRef",
    "dependentSchemas",
    "multipleOf",
    "patternProperties",
    "prefixItems",
    "unevaluatedItems",
    "unevaluatedProperties",
];

impl<'a> Validator<'a> {
    /// Compile `root`: index its resources and patterns, and refuse unsupported keywords.
    pub fn new(root: &'a Json, checker: TypeChecker) -> DomainResult<Self> {
        let mut validator = Self { root, checker, resources: HashMap::new(), patterns: HashMap::new() };
        validator.index(root)?;
        Ok(validator)
    }

    fn index(&mut self, schema: &'a Json) -> DomainResult<()> {
        let Json::Object(map) = schema else { return Ok(()) };
        if let Some(Json::String(id)) = map.get("$id") {
            self.resources.insert(id, schema);
        }
        for (key, value) in map {
            if UNSUPPORTED.contains(&key.as_str()) {
                return Err(DomainError::value(format!("Schema keyword {key} is not supported by the port")));
            }
            if key == "pattern" {
                let Json::String(pattern) = value else {
                    return Err(DomainError::value("Schema pattern must be text"));
                };
                if !self.patterns.contains_key(pattern.as_str()) {
                    self.patterns.insert(pattern, PyRegex::new(pattern)?);
                }
            }
            if SUBSCHEMA_MAPS.contains(&key.as_str()) {
                if let Json::Object(children) = value {
                    for child in children.values() {
                        self.index(child)?;
                    }
                }
            } else if SUBSCHEMA_LISTS.contains(&key.as_str()) {
                if let Json::Array(children) = value {
                    for child in children {
                        self.index(child)?;
                    }
                }
            } else if SUBSCHEMA_SINGLE.contains(&key.as_str()) {
                self.index(value)?;
            }
        }
        Ok(())
    }

    /// `iter_errors(instance)`, collected in order.
    pub fn iter_errors(&self, instance: &'a Json) -> Vec<SchemaError<'a>> {
        let mut out = Vec::new();
        self.descend(Cow::Borrowed(instance), self.root, self.root, false, &mut out);
        out
    }

    /// `is_valid(instance)`.
    pub fn is_valid(&self, instance: &'a Json) -> bool {
        self.valid_under(Cow::Borrowed(instance), self.root, self.root)
    }

    fn valid_under(&self, instance: Cow<'a, Json>, schema: &'a Json, resource: &'a Json) -> bool {
        let mut out = Vec::new();
        self.descend(instance, schema, resource, true, &mut out);
        out.is_empty()
    }

    /// Validate `instance` against `schema` inside `resource`, appending errors to `out`.
    /// With `first`, stop at the first error, as `is_valid` does.
    fn descend(
        &self,
        instance: Cow<'a, Json>,
        schema: &'a Json,
        resource: &'a Json,
        first: bool,
        out: &mut Vec<SchemaError<'a>>,
    ) {
        let map = match schema {
            Json::Bool(true) => return,
            Json::Object(map) => map,
            _ => {
                out.push(SchemaError {
                    keyword: None,
                    validator_value: Cow::Borrowed(&NULL),
                    instance,
                    schema,
                    path: VecDeque::new(),
                    schema_path: VecDeque::new(),
                    context: Vec::new(),
                    detail: Detail::None,
                });
                return;
            }
        };
        let resource = if matches!(map.get("$id"), Some(Json::String(_))) { schema } else { resource };
        for (keyword, value) in map {
            if first && !out.is_empty() {
                return;
            }
            let start = out.len();
            let call = KeywordCall { keyword, value, instance: &instance, schema, map, resource, first };
            self.keyword(call, out);
            if !matches!(keyword.as_str(), "if" | "$ref") {
                for error in &mut out[start..] {
                    error.schema_path.push_front(SchemaPart::Name(keyword.clone()));
                }
            }
        }
    }

    /// `descend(instance, schema, path=..., schema_path=...)`: the new errors gain `path` and
    /// `schema_path` in front, except an error from a `false` schema, which Python's
    /// `descend` yields and returns before adding them.
    #[allow(clippy::too_many_arguments)]
    fn descend_at(
        &self,
        instance: Cow<'a, Json>,
        schema: &'a Json,
        resource: &'a Json,
        first: bool,
        out: &mut Vec<SchemaError<'a>>,
        path: Option<PathPart>,
        schema_path: Option<SchemaPart>,
    ) {
        let start = out.len();
        self.descend(instance, schema, resource, first, out);
        if matches!(schema, Json::Bool(false)) {
            return;
        }
        for error in &mut out[start..] {
            if let Some(part) = &path {
                error.path.push_front(part.clone());
            }
            if let Some(part) = &schema_path {
                error.schema_path.push_front(part.clone());
            }
        }
    }

    /// Run one keyword. Errors it creates are pushed with their fields set; errors from nested
    /// schemas arrive set by the nested keyword, as `ValidationError._set` leaves them.
    fn keyword(&self, call: KeywordCall<'a, '_>, out: &mut Vec<SchemaError<'a>>) {
        let KeywordCall { keyword, value, instance, schema, map, resource, first } = call;
        let own = |out: &mut Vec<SchemaError<'a>>, context: Vec<SchemaError<'a>>, detail: Detail<'a>| {
            out.push(SchemaError {
                keyword: Some(keyword),
                validator_value: Cow::Borrowed(value),
                instance: instance.clone(),
                schema,
                path: VecDeque::new(),
                schema_path: VecDeque::new(),
                context,
                detail,
            });
        };
        let item = instance.as_ref();
        match keyword {
            "$ref" => {
                if let Json::String(reference) = value {
                    let (target, target_resource) = self.resolve(reference, resource);
                    self.descend(instance.clone(), target, target_resource, first, out);
                }
            }
            "type" => {
                let names: Vec<&str> = match value {
                    Json::String(name) => vec![name.as_str()],
                    Json::Array(names) => names.iter().filter_map(Json::as_str).collect(),
                    _ => Vec::new(),
                };
                if !names.iter().any(|name| self.is_type(item, name)) {
                    own(out, Vec::new(), Detail::None);
                }
            }
            "properties" => {
                if let (Json::Object(members), Json::Object(properties)) = (item, value) {
                    for (property, subschema) in properties {
                        if first && !out.is_empty() {
                            return;
                        }
                        if members.contains_key(property) {
                            self.descend_at(
                                child(instance, property),
                                subschema,
                                resource,
                                first,
                                out,
                                Some(PathPart::Key(property.clone())),
                                Some(SchemaPart::Name(property.clone())),
                            );
                        }
                    }
                }
            }
            "additionalProperties" => {
                if let Json::Object(members) = item {
                    let declared = map.get("properties").and_then(Json::as_object);
                    let extras: Vec<&String> =
                        members.keys().filter(|key| declared.is_none_or(|d| !d.contains_key(*key))).collect();
                    match value {
                        Json::Object(_) => {
                            for extra in extras {
                                if first && !out.is_empty() {
                                    return;
                                }
                                let path = Some(PathPart::Key(extra.clone()));
                                self.descend_at(
                                    child(instance, extra),
                                    value,
                                    resource,
                                    first,
                                    out,
                                    path,
                                    None,
                                );
                            }
                        }
                        Json::Bool(false) if !extras.is_empty() => own(out, Vec::new(), Detail::None),
                        _ => {}
                    }
                }
            }
            "items" => {
                if let Json::Array(items) = item {
                    if items.is_empty() {
                        return;
                    }
                    if value == &Json::Bool(false) {
                        own(out, Vec::new(), Detail::None);
                    } else {
                        for index in 0..items.len() {
                            if first && !out.is_empty() {
                                return;
                            }
                            let path = Some(PathPart::Index(index));
                            self.descend_at(
                                index_child(instance, index),
                                value,
                                resource,
                                first,
                                out,
                                path,
                                None,
                            );
                        }
                    }
                }
            }
            "const" => {
                if !equal(item, value) {
                    own(out, Vec::new(), Detail::None);
                }
            }
            "enum" => {
                if let Json::Array(options) = value {
                    if options.iter().all(|option| !equal(option, item)) {
                        own(out, Vec::new(), Detail::None);
                    }
                }
            }
            "contains" => {
                if let Json::Array(items) = item {
                    let min_contains = map.get("minContains").cloned().unwrap_or(Json::from(1));
                    let max_contains = map.get("maxContains").cloned().unwrap_or(Json::from(items.len()));
                    let mut matches = 0usize;
                    for index in 0..items.len() {
                        if self.valid_under(index_child(instance, index), value, resource) {
                            matches += 1;
                            if number_gt(matches, &max_contains) {
                                out.push(self.with_keyword(
                                    "maxContains",
                                    max_contains,
                                    instance,
                                    schema,
                                    Detail::None,
                                ));
                                return;
                            }
                        }
                    }
                    if number_lt(matches, &min_contains) {
                        if matches == 0 {
                            own(out, Vec::new(), Detail::None);
                        } else {
                            out.push(self.with_keyword(
                                "minContains",
                                min_contains,
                                instance,
                                schema,
                                Detail::Matches(matches),
                            ));
                        }
                    }
                }
            }
            "minimum" | "maximum" | "exclusiveMinimum" | "exclusiveMaximum" => {
                if let (Json::Number(number), Json::Number(bound)) = (item, value) {
                    let order = compare_numbers(number, bound);
                    let fails = match keyword {
                        "minimum" => order == Ordering::Less,
                        "maximum" => order == Ordering::Greater,
                        "exclusiveMinimum" => order != Ordering::Greater,
                        _ => order != Ordering::Less,
                    };
                    if fails {
                        own(out, Vec::new(), Detail::None);
                    }
                }
            }
            "minItems" | "maxItems" => {
                if let Json::Array(items) = item {
                    let fails = if keyword == "minItems" {
                        number_gt_len(value, items.len())
                    } else {
                        number_lt_len(value, items.len())
                    };
                    if fails {
                        own(out, Vec::new(), Detail::None);
                    }
                }
            }
            "uniqueItems" => {
                if let (Json::Bool(true), Json::Array(items)) = (value, item) {
                    let duplicated = items
                        .iter()
                        .enumerate()
                        .any(|(index, left)| items[index + 1..].iter().any(|right| equal(left, right)));
                    if duplicated {
                        own(out, Vec::new(), Detail::None);
                    }
                }
            }
            "pattern" => {
                if let (Json::String(text), Json::String(pattern)) = (item, value) {
                    if !self.patterns[pattern.as_str()].search(text) {
                        own(out, Vec::new(), Detail::None);
                    }
                }
            }
            "minLength" | "maxLength" => {
                if let Json::String(text) = item {
                    let count = pystr::len(text);
                    let fails = if keyword == "minLength" {
                        number_gt_len(value, count)
                    } else {
                        number_lt_len(value, count)
                    };
                    if fails {
                        own(out, Vec::new(), Detail::None);
                    }
                }
            }
            "minProperties" | "maxProperties" => {
                if let Json::Object(members) = item {
                    let fails = if keyword == "minProperties" {
                        number_gt_len(value, members.len())
                    } else {
                        number_lt_len(value, members.len())
                    };
                    if fails {
                        own(out, Vec::new(), Detail::None);
                    }
                }
            }
            "required" => {
                if let (Json::Object(members), Json::Array(required)) = (item, value) {
                    for property in required {
                        if first && !out.is_empty() {
                            return;
                        }
                        if property.as_str().is_some_and(|name| !members.contains_key(name)) {
                            own(out, Vec::new(), Detail::Required(property));
                        }
                    }
                }
            }
            "dependentRequired" => {
                if let (Json::Object(members), Json::Object(dependencies)) = (item, value) {
                    for (property, dependency) in dependencies {
                        if !members.contains_key(property) {
                            continue;
                        }
                        for each in dependency.as_array().into_iter().flatten() {
                            if first && !out.is_empty() {
                                return;
                            }
                            if each.as_str().is_some_and(|name| !members.contains_key(name)) {
                                own(out, Vec::new(), Detail::Dependency { each, property });
                            }
                        }
                    }
                }
            }
            "propertyNames" => {
                if let Json::Object(members) = item {
                    for property in members.keys() {
                        if first && !out.is_empty() {
                            return;
                        }
                        self.descend(Cow::Owned(Json::String(property.clone())), value, resource, first, out);
                    }
                }
            }
            "allOf" => {
                for (index, subschema) in value.as_array().into_iter().flatten().enumerate() {
                    if first && !out.is_empty() {
                        return;
                    }
                    let part = Some(SchemaPart::Index(index));
                    self.descend_at(instance.clone(), subschema, resource, first, out, None, part);
                }
            }
            "anyOf" | "oneOf" => {
                let branches = value.as_array().map(Vec::as_slice).unwrap_or_default();
                let mut all_errors = Vec::new();
                let mut first_valid = None;
                for (index, subschema) in branches.iter().enumerate() {
                    // Python lists each branch's errors in full for the context.
                    let start = all_errors.len();
                    let part = Some(SchemaPart::Index(index));
                    self.descend_at(
                        instance.clone(),
                        subschema,
                        resource,
                        false,
                        &mut all_errors,
                        None,
                        part,
                    );
                    if all_errors.len() == start {
                        first_valid = Some(index);
                        break;
                    }
                }
                match first_valid {
                    None => own(out, all_errors, Detail::None),
                    Some(index) if keyword == "oneOf" => {
                        let mut more_valid: Vec<&'a Json> = branches[index + 1..]
                            .iter()
                            .filter(|subschema| self.valid_under(instance.clone(), subschema, resource))
                            .collect();
                        if !more_valid.is_empty() {
                            more_valid.push(&branches[index]);
                            own(out, Vec::new(), Detail::MoreValid(more_valid));
                        }
                    }
                    Some(_) => {}
                }
            }
            "not" => {
                if self.valid_under(instance.clone(), value, resource) {
                    own(out, Vec::new(), Detail::None);
                }
            }
            "if" => {
                let branch =
                    if self.valid_under(instance.clone(), value, resource) { "then" } else { "else" };
                if let Some(subschema) = map.get(branch) {
                    let part = Some(SchemaPart::Name(branch.to_owned()));
                    self.descend_at(instance.clone(), subschema, resource, first, out, None, part);
                }
            }
            // Annotations, `then`/`else` (read by `if`), `minContains`/`maxContains` (read by
            // `contains`), `$defs`, `format` without a checker, and non-validator keywords.
            _ => {}
        }
    }

    /// An error whose `validator` is not the running keyword (`minContains`, `maxContains`).
    fn with_keyword(
        &self,
        keyword: &'static str,
        value: Json,
        instance: &Cow<'a, Json>,
        schema: &'a Json,
        detail: Detail<'a>,
    ) -> SchemaError<'a> {
        SchemaError {
            keyword: Some(keyword),
            validator_value: Cow::Owned(value),
            instance: instance.clone(),
            schema,
            path: VecDeque::new(),
            schema_path: VecDeque::new(),
            context: Vec::new(),
            detail,
        }
    }

    fn is_type(&self, value: &Json, name: &str) -> bool {
        match name {
            "object" => value.is_object(),
            "array" => value.is_array(),
            "string" => value.is_string(),
            "boolean" => value.is_boolean(),
            "null" => value.is_null(),
            "number" => value.is_number(),
            "integer" => match value.as_number() {
                Some(number) if number.is_i64() || number.is_u64() => true,
                // Draft 6 onwards: a float with no fractional part is an integer.
                Some(number) => {
                    self.checker == TypeChecker::Draft202012
                        && number.as_f64().is_some_and(|f| f.fract() == 0.0)
                }
                None => false,
            },
            other => panic!("schema names an unknown type {other:?}"),
        }
    }

    /// `referencing`'s lookup: a fragment-only reference is a JSON pointer in the current
    /// resource; any other reference names a resource by `$id`, optionally with a pointer.
    /// Walking a pointer through a subschema with its own `$id` enters that resource.
    fn resolve(&self, reference: &str, resource: &'a Json) -> (&'a Json, &'a Json) {
        let (uri, fragment) = reference.split_once('#').unwrap_or((reference, ""));
        let mut base = if uri.is_empty() {
            resource
        } else {
            *self.resources.get(uri).unwrap_or_else(|| panic!("unresolvable $ref {reference:?}"))
        };
        let mut node = base;
        for raw in fragment.split('/').skip(1) {
            let segment = raw.replace("~1", "/").replace("~0", "~");
            node = match node {
                Json::Object(map) => &map[&segment],
                Json::Array(items) => &items[segment.parse::<usize>().expect("array pointer index")],
                _ => panic!("unresolvable $ref {reference:?}"),
            };
            if matches!(node.get("$id"), Some(Json::String(_))) {
                base = node;
            }
        }
        (node, base)
    }
}

/// The arguments of one keyword run.
struct KeywordCall<'a, 'i> {
    keyword: &'a str,
    value: &'a Json,
    instance: &'i Cow<'a, Json>,
    schema: &'a Json,
    map: &'a JsonMap,
    resource: &'a Json,
    first: bool,
}

fn child<'a>(instance: &Cow<'a, Json>, key: &str) -> Cow<'a, Json> {
    match instance {
        Cow::Borrowed(Json::Object(map)) => Cow::Borrowed(&map[key]),
        other => Cow::Owned(other.as_ref()[key].clone()),
    }
}

fn index_child<'a>(instance: &Cow<'a, Json>, index: usize) -> Cow<'a, Json> {
    match instance {
        Cow::Borrowed(Json::Array(items)) => Cow::Borrowed(&items[index]),
        other => Cow::Owned(other.as_ref()[index].clone()),
    }
}

fn as_number(count: usize) -> serde_json::Number {
    serde_json::Number::from(count as u64)
}

/// Python `count > bound` with an integer count and a JSON number bound.
fn number_gt(count: usize, bound: &Json) -> bool {
    bound.as_number().is_some_and(|bound| compare_numbers(&as_number(count), bound) == Ordering::Greater)
}

/// Python `count < bound`.
fn number_lt(count: usize, bound: &Json) -> bool {
    bound.as_number().is_some_and(|bound| compare_numbers(&as_number(count), bound) == Ordering::Less)
}

/// Python `len(instance) < bound` (a `min*` keyword fails).
fn number_gt_len(bound: &Json, length: usize) -> bool {
    number_lt(length, bound)
}

/// Python `len(instance) > bound` (a `max*` keyword fails).
fn number_lt_len(bound: &Json, length: usize) -> bool {
    number_gt(length, bound)
}

/// `jsonschema._utils.equal`: JSON equality where booleans are never numbers and `1 == 1.0`.
pub fn equal(one: &Json, two: &Json) -> bool {
    match (one, two) {
        (Json::String(a), Json::String(b)) => a == b,
        (Json::String(_), _) | (_, Json::String(_)) => false,
        (Json::Array(a), Json::Array(b)) => a.len() == b.len() && a.iter().zip(b).all(|(x, y)| equal(x, y)),
        (Json::Object(a), Json::Object(b)) => {
            a.len() == b.len()
                && a.iter().all(|(key, value)| b.get(key).is_some_and(|other| equal(value, other)))
        }
        (Json::Bool(a), Json::Bool(b)) => a == b,
        (Json::Null, Json::Null) => true,
        (Json::Number(a), Json::Number(b)) => compare_numbers(a, b) == Ordering::Equal,
        _ => false,
    }
}

/// Python's exact comparison of two numbers, either of which may be an `int` or a `float`.
fn compare_numbers(a: &serde_json::Number, b: &serde_json::Number) -> Ordering {
    fn int(n: &serde_json::Number) -> Option<i128> {
        n.as_i64().map(i128::from).or_else(|| n.as_u64().map(i128::from))
    }
    match (int(a), int(b), a.as_f64(), b.as_f64()) {
        (Some(x), Some(y), _, _) => x.cmp(&y),
        (Some(x), None, _, Some(y)) => compare_int_float(x, y),
        (None, Some(y), Some(x), _) => compare_int_float(y, x).reverse(),
        (_, _, Some(x), Some(y)) => x.partial_cmp(&y).expect("JSON numbers are finite"),
        _ => unreachable!("a JSON number is an integer or a float"),
    }
}

fn compare_int_float(int: i128, float: f64) -> Ordering {
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn equality_keeps_booleans_apart_from_numbers() {
        assert!(equal(&json!(1), &json!(1.0)));
        assert!(!equal(&json!(true), &json!(1)));
        assert!(!equal(&json!([false]), &json!([0])));
        assert!(equal(&json!({"a": [1, 2.0]}), &json!({"a": [1.0, 2]})));
        assert!(!equal(&json!("1"), &json!(1)));
    }

    #[test]
    fn integers_compare_exactly_with_floats() {
        let n = |v: Json| v.as_number().unwrap().clone();
        assert_eq!(
            compare_numbers(&n(json!(9007199254740993_i64)), &n(json!(9007199254740992.0))),
            Ordering::Greater
        );
        assert_eq!(compare_numbers(&n(json!(2)), &n(json!(2.5))), Ordering::Less);
        assert_eq!(compare_numbers(&n(json!(-3)), &n(json!(-2.5))), Ordering::Less);
        assert_eq!(compare_numbers(&n(json!(3)), &n(json!(3.0))), Ordering::Equal);
    }

    #[test]
    fn unsupported_keywords_are_refused() {
        assert!(Validator::new(&json!({"multipleOf": 2}), TypeChecker::Draft202012).is_err());
        assert!(
            Validator::new(&json!({"properties": {"a": {"prefixItems": []}}}), TypeChecker::Content).is_err()
        );
        assert!(Validator::new(&json!({"type": "object"}), TypeChecker::Content).is_ok());
    }

    #[test]
    fn the_two_type_checkers_differ_only_on_integral_floats() {
        let schema = json!({"type": "integer"});
        let draft = Validator::new(&schema, TypeChecker::Draft202012).unwrap();
        let content = Validator::new(&schema, TypeChecker::Content).unwrap();
        let value = json!(1.0);
        assert!(draft.is_valid(&value));
        assert!(!content.is_valid(&value));
        let message = content.iter_errors(&value)[0].message();
        assert_eq!(message, "1.0 is not of type 'integer'");
    }
}
