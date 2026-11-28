//! `agent/_tool_loader.py`: Sylvia's own tool loader, one generic mechanic tool and compact
//! appended definitions.
//!
//! On a route whose provider cannot add tools mid-conversation (`SYLVIA_GM_TOOL_LIST=deferred`
//! outside the native and provider deferral routes), the declared list holds the application's
//! own tools and `call_mechanic` only, so the cached prefix never breaks.
//!
//! - A Ruleset mechanic is defined once, when the turn loop first offers it, by an appended
//!   `SYLVIA_GM_TOOL_DEFINITIONS` record in a terse TypeScript-like form.
//! - A `SYLVIA_GM_CALLABLE_TOOLS` record names the mechanics the engine accepts now, and the
//!   current argument choices follow in their own record.
//! - The model calls a mechanic through `call_mechanic`; the turn loop unwraps the call before
//!   admission ([`unwrap_mechanic_call`]), and the ledger renders canonical history back in the
//!   form the model used ([`project_conversation`]).
//!
//! Mechanics are shown by a short name, the operation part of the provider alias
//! (`tool_<digest>_<operation>`), unless two catalog mechanics share it. The long texts are
//! data (`tool_loader_texts.json`, exported by `generate_tool_loader_texts.py`).

use crate::fixed_tool_projection::{argument_choices_record, loader_text};
use regex::Regex;
use serde_json::Value as Json;
use std::collections::{BTreeSet, HashMap, HashSet};
use std::sync::OnceLock;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{pystr, CodedResult, JsonMap};
use sylvia_domain::error::DomainResult;
use sylvia_domain::pyops::{py_iter, py_require_hashable, py_str, py_truthy};

/// `MECHANIC_TOOL_NAME`.
pub const MECHANIC_TOOL_NAME: &str = "call_mechanic";
/// `DEFINITIONS_LABEL`.
pub const DEFINITIONS_LABEL: &str = "SYLVIA_GM_TOOL_DEFINITIONS";
/// `CALLABLE_LABEL`.
pub const CALLABLE_LABEL: &str = "SYLVIA_GM_CALLABLE_TOOLS";
/// `CHOICES_LABEL`.
pub const CHOICES_LABEL: &str = "SYLVIA_GM_ARGUMENT_CHOICES";
/// `READER_NAME`: the host-local reader of a listed mechanic's arguments.
pub const READER_NAME: &str = "read_tool_arguments";
/// `CONTRACT_SCHEMA_KEY`: the schema key of an argument contract.
pub const CONTRACT_SCHEMA_KEY: &str = "x-sylvia-gm-argument-contracts";
/// `INLINE_LIMIT`: a compact definition longer than this (characters) is listed by name and
/// summary only, and read on demand.
pub const INLINE_LIMIT: usize = 700;
/// `SUMMARY_LIMIT`: a listed mechanic's summary is its description cut to this.
pub const SUMMARY_LIMIT: usize = 240;
/// `LISTED_NOTE`.
pub const LISTED_NOTE: &str = "(read its arguments first)";
/// `DEFINED_KEY`: the field that marks a tool result as having given a mechanic's definition.
pub const DEFINED_KEY: &str = "mechanic_definition";
/// `UNREAD_CODE`.
pub const UNREAD_CODE: &str = "mechanic_arguments_unread";
const CONSTRAINTS: [(&str, &str); 10] = [
    ("minimum", ">="),
    ("maximum", "<="),
    ("exclusiveMinimum", ">"),
    ("exclusiveMaximum", "<"),
    ("minLength", "min length"),
    ("maxLength", "max length"),
    ("minItems", "min items"),
    ("maxItems", "max items"),
    ("pattern", "pattern"),
    ("default", "default"),
];

/// `MECHANIC_TOOL`: the one generic mechanic tool.
pub fn mechanic_tool() -> JsonMap {
    loader_text("mechanic_tool").as_object().expect("an exported tool").clone()
}

/// `READER_TOOL`: the argument reader.
pub fn reader_tool() -> JsonMap {
    loader_text("reader_tool").as_object().expect("an exported tool").clone()
}

/// `_function(tool)`: an OpenAI-shaped tool's function, else the tool itself.
pub fn function_of(tool: &JsonMap) -> &JsonMap {
    match tool.get("function") {
        Some(Json::Object(function)) => function,
        _ => tool,
    }
}

/// `tool_name(tool)`: `str(_function(tool).get("name", ""))`.
pub fn tool_name(tool: &JsonMap) -> String {
    function_of(tool).get("name").map_or_else(String::new, py_str)
}

/// `_literal(value)`: `json.dumps(value, ensure_ascii=False)`.
fn literal(value: &Json) -> String {
    pyjson::dumps(value, Dump::DEFAULT.ascii(false))
}

/// `_words(text)`: `" ".join(str(text or "").split())`.
fn words(text: Option<&Json>) -> String {
    let text = match text {
        Some(value) if py_truthy(value) => py_str(value),
        _ => String::new(),
    };
    text.split(pystr::is_space).filter(|word| !word.is_empty()).collect::<Vec<_>>().join(" ")
}

/// `_compact_json(value)`: `json.dumps(value, ensure_ascii=False, separators=(",", ":"))`.
fn compact_json(value: &Json) -> String {
    pyjson::dumps(value, Dump::WIRE.ascii(false))
}

/// `json.loads(text)`: `Ok(None)` where Python's decoder refuses the text (its `ValueError`),
/// an error where only the Rust decoder's declared JSON limits refuse it.
fn loads(text: &str) -> CodedResult<Option<Json>> {
    match pyjson::loads(text) {
        Ok(value) => Ok(Some(value)),
        Err(error) if error.error_type == Some("JSONDecodeError") => Ok(None),
        Err(error) => Err(error),
    }
}

// ── short names ────────────────────────────────────────────────────────────────────────────

/// `_ALIAS`: `^tool_[0-9a-f]{12}_(.+)$`, where Python's `$` also matches before a final newline.
fn alias_pattern() -> &'static Regex {
    static PATTERN: OnceLock<Regex> = OnceLock::new();
    PATTERN.get_or_init(|| Regex::new(r"^tool_[0-9a-f]{12}_(.+)\n?\z").expect("the alias pattern"))
}

/// `_operation(alias)`: the operation part of a provider alias, else the alias.
fn operation(alias: &str) -> String {
    alias_pattern().captures(alias).map_or_else(|| alias.to_owned(), |found| found[1].to_owned())
}

/// `MechanicNames`: short display names for a fixed catalog's mechanics, and back to provider
/// aliases.
#[derive(Debug, Clone)]
pub struct MechanicNames {
    reserved: HashSet<String>,
    catalog_short: HashMap<String, String>,
    taken: HashSet<String>,
}

impl MechanicNames {
    /// `MechanicNames(catalog, reserved)`.
    pub fn new<'a>(
        catalog: impl IntoIterator<Item = &'a str>,
        reserved: impl IntoIterator<Item = &'a str>,
    ) -> Self {
        let mut reserved: HashSet<String> = reserved.into_iter().map(str::to_owned).collect();
        reserved.insert(MECHANIC_TOOL_NAME.to_owned());
        let aliases: BTreeSet<String> = catalog.into_iter().map(str::to_owned).collect();
        let mut counts: HashMap<String, usize> = HashMap::new();
        for alias in &aliases {
            *counts.entry(operation(alias)).or_insert(0) += 1;
        }
        let catalog_short: HashMap<String, String> = aliases
            .iter()
            .map(|alias| {
                let operation = operation(alias);
                let short = if counts[&operation] == 1 && !reserved.contains(&operation) {
                    operation
                } else {
                    alias.clone()
                };
                (alias.clone(), short)
            })
            .collect();
        let taken = catalog_short.values().cloned().collect();
        Self { reserved, catalog_short, taken }
    }

    /// `short(alias)`: the name the model sees; a tool outside the catalog keeps a unique short
    /// form.
    pub fn short(&self, alias: &str) -> String {
        if let Some(known) = self.catalog_short.get(alias) {
            return known.clone();
        }
        if self.reserved.contains(alias) {
            return alias.to_owned();
        }
        let operation = operation(alias);
        if !self.taken.contains(&operation) && !self.reserved.contains(&operation) {
            operation
        } else {
            alias.to_owned()
        }
    }

    /// `alias(name, offered)`: the offered provider alias a model's name means, else the name.
    pub fn alias(&self, name: &str, offered: &[&str]) -> String {
        if offered.contains(&name) {
            return name.to_owned();
        }
        let matches: Vec<&&str> = offered.iter().filter(|alias| self.short(alias) == name).collect();
        match matches.as_slice() {
            [only] => (**only).to_owned(),
            _ => name.to_owned(),
        }
    }
}

// ── compact definitions ────────────────────────────────────────────────────────────────────

/// `_note(schema)`: constraints a type expression cannot show, as a short trailing comment.
fn note(schema: &JsonMap) -> String {
    let mut parts: Vec<String> = CONSTRAINTS
        .iter()
        .filter_map(|(key, label)| {
            let value = schema.get(*key)?;
            let shown = if ["pattern", "default"].contains(key) { literal(value) } else { py_str(value) };
            Some(format!("{label} {shown}"))
        })
        .collect();
    if schema.get("uniqueItems") == Some(&Json::Bool(true)) {
        parts.push("unique".into());
    }
    if let Some(Json::String(reference)) = schema.get("x-sylvia-gm-reference") {
        parts.push(format!("a {reference} id"));
    }
    if schema.get("x-sylvia-gm-castable-spells").is_some_and(py_truthy) {
        parts.push("a castable spell".into());
    }
    if let Some(Json::Object(aliases)) = schema.get("x-sylvia-gm-value-aliases") {
        if !aliases.is_empty() {
            let mut sorted: Vec<(&String, &Json)> = aliases.iter().collect();
            sorted.sort_by(|a, b| a.0.cmp(b.0));
            let shown: Vec<String> =
                sorted.iter().map(|(key, value)| format!("{key}={}", py_str(value))).collect();
            parts.push(format!("aliases {}", shown.join(", ")));
        }
    }
    parts.join("; ")
}

/// `_Renderer`: a schema's shape in a TypeScript-like form, `$ref`s resolved through its `$defs`.
struct Renderer<'a> {
    defs: Option<&'a JsonMap>,
}

impl<'a> Renderer<'a> {
    fn new(root: &'a JsonMap) -> Self {
        Self { defs: root.get("$defs").and_then(Json::as_object) }
    }

    /// `resolve(schema)`: a `$ref` replaced by its definition (the referring schema's other
    /// keys win), until a missing or repeated name; anything but a mapping is `{}`.
    fn resolve(&self, schema: &Json) -> JsonMap {
        let mut seen: HashSet<String> = HashSet::new();
        let mut current = schema.clone();
        while let Json::Object(map) = &current {
            let Some(Json::String(reference)) = map.get("$ref") else {
                break;
            };
            let name = reference.rsplit('/').next().unwrap_or("").to_owned();
            let target = self.defs.and_then(|defs| defs.get(&name));
            let Some(Json::Object(target)) = target else {
                break;
            };
            if seen.contains(&name) {
                break;
            }
            seen.insert(name);
            let mut merged = target.clone();
            for (key, value) in map {
                if key != "$ref" {
                    merged.insert(key.clone(), value.clone());
                }
            }
            current = Json::Object(merged);
        }
        match current {
            Json::Object(map) => map,
            _ => JsonMap::new(),
        }
    }

    fn type_of(&self, raw: &Json, depth: usize) -> DomainResult<String> {
        let schema = self.resolve(raw);
        if let Some(constant) = schema.get("const") {
            return Ok(literal(constant));
        }
        if let Some(Json::Array(values)) = schema.get("enum") {
            let joined = values.iter().map(literal).collect::<Vec<_>>().join(" | ");
            return Ok(if joined.is_empty() { "never".into() } else { joined });
        }
        for key in ["oneOf", "anyOf"] {
            if let Some(Json::Array(options)) = schema.get(key) {
                let parts = options
                    .iter()
                    .map(|option| self.type_of(option, depth))
                    .collect::<DomainResult<Vec<_>>>()?;
                return Ok(parts.join(" | "));
            }
        }
        let kind = schema.get("type");
        if let Some(Json::Array(kinds)) = kind {
            let mut parts = Vec::with_capacity(kinds.len());
            for item in kinds {
                let mut variant = schema.clone();
                variant.insert("type".into(), item.clone());
                parts.push(self.type_of(&Json::Object(variant), depth)?);
            }
            return Ok(parts.join(" | "));
        }
        let kind = kind.and_then(Json::as_str);
        if kind == Some("array") || schema.contains_key("items") {
            let empty = Json::Object(JsonMap::new());
            let inner = self.type_of(schema.get("items").unwrap_or(&empty), depth)?;
            return Ok(if inner.contains(" | ") { format!("({inner})[]") } else { format!("{inner}[]") });
        }
        if kind == Some("object") || schema.contains_key("properties") {
            return self.object(&schema, depth);
        }
        Ok(kind.unwrap_or("any").to_owned())
    }

    fn object(&self, schema: &JsonMap, depth: usize) -> DomainResult<String> {
        let extra = schema.get("additionalProperties");
        let properties = match schema.get("properties") {
            Some(Json::Object(properties)) if !properties.is_empty() => properties,
            _ => {
                return Ok(match extra {
                    Some(extra @ Json::Object(_)) => {
                        format!("{{[key: string]: {}}}", self.type_of(extra, depth + 1)?)
                    }
                    Some(Json::Bool(false)) => "{}".into(),
                    _ => "object".into(),
                })
            }
        };
        // `set(schema.get("required") or ())`.
        let required: Vec<Json> = match schema.get("required") {
            Some(value) if py_truthy(value) => py_iter(value)?,
            _ => Vec::new(),
        };
        for name in &required {
            py_require_hashable(name)?;
        }
        let pad = " ".repeat(depth + 1);
        let mut lines = vec!["{".to_owned()];
        for (name, raw) in properties {
            let child = self.resolve(raw);
            let description = words(child.get("description"));
            if !description.is_empty() {
                lines.push(format!("{pad}// {description}"));
            }
            let note = note(&child);
            let optional = if required.iter().any(|item| item.as_str() == Some(name)) { "" } else { "?" };
            let shape = self.type_of(&Json::Object(child), depth + 1)?;
            let comment = if note.is_empty() { String::new() } else { format!(" // {note}") };
            lines.push(format!("{pad}{name}{optional}: {shape}{comment}"));
        }
        if let Some(extra @ Json::Object(_)) = extra {
            lines.push(format!("{pad}[key: string]: {}", self.type_of(extra, depth + 1)?));
        }
        lines.push(format!("{}}}", " ".repeat(depth)));
        Ok(lines.join("\n"))
    }
}

/// `compact_arguments(schema)`: an object schema's argument shape.
pub fn compact_arguments(schema: &JsonMap) -> DomainResult<String> {
    Renderer::new(schema).object(schema, 0)
}

/// `compact_definition(tool, name)`: one mechanic, its name, description and argument shape.
pub fn compact_definition(tool: &JsonMap, name: Option<&str>) -> DomainResult<String> {
    let function = function_of(tool);
    let default = {
        let mut object = JsonMap::new();
        object.insert("type".into(), Json::String("object".into()));
        object
    };
    let schema = match function.get("parameters") {
        Some(Json::Object(parameters)) => parameters,
        _ => &default,
    };
    let description = words(function.get("description"));
    let shown = match name {
        Some(name) if !name.is_empty() => name.to_owned(),
        _ => function.get("name").map_or_else(String::new, py_str),
    };
    let mut text = format!("## {shown}\n");
    if !description.is_empty() {
        text.push_str(&description);
        text.push('\n');
    }
    text.push_str("arguments: ");
    text.push_str(&Renderer::new(schema).object(schema, 0)?);
    Ok(text)
}

/// `_summary(tool)`: a listed mechanic's description, cut at a word before the limit.
fn summary(tool: &JsonMap) -> String {
    let description = words(function_of(tool).get("description"));
    if description.chars().count() <= SUMMARY_LIMIT {
        return description;
    }
    let head: String = description.chars().take(SUMMARY_LIMIT).collect();
    let cut = head.rsplit_once(' ').map_or(head.as_str(), |(before, _)| before);
    format!("{} …", cut.trim_end_matches([',', ';', ':']))
}

/// `definitions_record(tools, names)`: the appended definitions of mechanics first offered at
/// this step, and which of them are defined in full (the rest are listed, read on demand).
pub fn definitions_record(tools: &[JsonMap], names: &MechanicNames) -> DomainResult<(String, Vec<String>)> {
    let mut full = Vec::new();
    let mut listed = Vec::new();
    let mut defined = Vec::new();
    for tool in tools {
        let short = names.short(&tool_name(tool));
        let text = compact_definition(tool, Some(&short))?;
        if text.chars().count() <= INLINE_LIMIT {
            full.push(text);
            defined.push(tool_name(tool));
        } else {
            listed.push(format!("## {short} {LISTED_NOTE}\n{}", summary(tool)));
        }
    }
    let preamble = loader_text("definitions_preamble").as_str().expect("an exported text");
    full.extend(listed);
    Ok((format!("{preamble}{}", full.join("\n\n")), defined))
}

/// `read_result(tool, short)`: `read_tool_arguments` for a listed mechanic, its definition.
/// Nothing runs.
pub fn read_result(tool: &JsonMap, short: &str) -> DomainResult<JsonMap> {
    let mut result = JsonMap::new();
    result.insert("status".into(), "ok".into());
    result.insert("tool".into(), short.into());
    result.insert(DEFINED_KEY.into(), short.into());
    result.insert("definition".into(), compact_definition(tool, Some(short))?.into());
    result.insert(
        "instruction".into(),
        format!("Call {MECHANIC_TOOL_NAME} for {short} with arguments of this shape.").into(),
    );
    Ok(result)
}

/// `unread_refusal(tool, short)`: a call to a listed mechanic before its arguments were read,
/// refused with them, so the model can correct it in one round.
pub fn unread_refusal(tool: &JsonMap, short: &str) -> DomainResult<JsonMap> {
    let mut refusal = JsonMap::new();
    refusal.insert("status".into(), "rejected".into());
    refusal.insert("code".into(), UNREAD_CODE.into());
    refusal.insert("state_changed".into(), false.into());
    refusal.insert(DEFINED_KEY.into(), short.into());
    refusal.insert("definition".into(), compact_definition(tool, Some(short))?.into());
    refusal.insert(
        "instruction".into(),
        format!(
            "Nothing ran. Call {MECHANIC_TOOL_NAME} again for {short} with arguments of this shape, if it still \
             fits; retry once."
        )
        .into(),
    );
    Ok(refusal)
}

/// `defined_in_result(message)`: the short name a tool result gave the definition of.
pub fn defined_in_result(message: &JsonMap) -> CodedResult<Option<String>> {
    let Some(Json::String(content)) = message.get("content") else {
        return Ok(None);
    };
    if message.get("role").and_then(Json::as_str) != Some("tool") || !content.contains(DEFINED_KEY) {
        return Ok(None);
    }
    Ok(match loads(content)? {
        Some(Json::Object(value)) => value.get(DEFINED_KEY).and_then(Json::as_str).map(str::to_owned),
        _ => None,
    })
}

// ── what the engine accepts now ────────────────────────────────────────────────────────────

fn callable_head() -> String {
    format!("{CALLABLE_LABEL} (application control, not player speech; the latest record with this label applies): ")
}

/// `callable_record(declared, offered, names, reading=...)`: the mechanics (and which declared
/// tools) the engine accepts at this step. With `reading`, an offered mechanic is still to be
/// read, so the reader can be called.
pub fn callable_record(
    declared: &[JsonMap],
    offered: Option<&[JsonMap]>,
    names: &MechanicNames,
    reading: bool,
) -> String {
    let mut listed: BTreeSet<String> = declared.iter().map(tool_name).collect();
    listed.remove(MECHANIC_TOOL_NAME);
    let mut allowed: BTreeSet<String> = offered.unwrap_or_default().iter().map(tool_name).collect();
    if reading && listed.contains(READER_NAME) {
        allowed.insert(READER_NAME.to_owned());
    }
    let mut mechanics: Vec<String> = allowed.difference(&listed).map(|name| names.short(name)).collect();
    mechanics.sort();
    let refused: Vec<&String> = listed.difference(&allowed).collect();
    let head = callable_head();
    if mechanics.is_empty() && listed.intersection(&allowed).next().is_none() {
        return format!(
            "{head}no tool can be called at this step. Reply without a tool call; a tool call now is refused."
        );
    }
    let mut text = if mechanics.is_empty() {
        format!("{head}no mechanic can be used now; do not call {MECHANIC_TOOL_NAME}.")
    } else {
        format!("{head}mechanics you can use now with {MECHANIC_TOOL_NAME}: {}.", mechanics.join(", "))
    };
    if !refused.is_empty() {
        let refused: Vec<&str> = refused.iter().map(|name| name.as_str()).collect();
        text.push_str(&format!(" These listed tools cannot be called now: {}.", refused.join(", ")));
    }
    text + " Any other call is refused."
}

/// A tool as `{"type": "function", "function": {**function, "name": short}}`.
fn renamed(tool: &JsonMap, names: &MechanicNames) -> Json {
    let mut function = function_of(tool).clone();
    let name = function.get("name").map_or_else(String::new, py_str);
    function.insert("name".into(), Json::String(names.short(&name)));
    let mut wrapped = JsonMap::new();
    wrapped.insert("type".into(), "function".into());
    wrapped.insert("function".into(), Json::Object(function));
    Json::Object(wrapped)
}

/// `choices_record(fixed, offered, names)`: the current argument choices, keyed by the names
/// the model sees.
pub fn choices_record(
    fixed: &[JsonMap],
    offered: Option<&[JsonMap]>,
    names: &MechanicNames,
) -> DomainResult<String> {
    let offered: Vec<Json> = offered.unwrap_or_default().iter().map(|tool| renamed(tool, names)).collect();
    let catalog: Vec<Json> = fixed.iter().map(|tool| renamed(tool, names)).collect();
    Ok(format!(
        "{CHOICES_LABEL} (application control, not player speech):{}",
        argument_choices_record(&catalog, Some(&offered))?
    ))
}

// ── the call itself ────────────────────────────────────────────────────────────────────────

/// `unwrap_mechanic_call(arguments)`: the mechanic name and its arguments (JSON) inside a
/// `call_mechanic` call. Arguments given as a JSON string are accepted; a missing arguments
/// field is an empty object; anything else is `None`.
pub fn unwrap_mechanic_call(arguments: &str) -> CodedResult<Option<(String, String)>> {
    let text = if arguments.is_empty() { "{}" } else { arguments };
    let Some(Json::Object(value)) = loads(text)? else {
        return Ok(None);
    };
    let mut inner = value.get("arguments").cloned().unwrap_or_else(|| Json::Object(JsonMap::new()));
    if let Json::String(encoded) = &inner {
        match loads(if encoded.is_empty() { "{}" } else { encoded })? {
            Some(decoded) => inner = decoded,
            None => return Ok(None),
        }
    }
    match (value.get("name"), &inner) {
        (Some(Json::String(name)), Json::Object(_)) if !pystr::strip(name).is_empty() => {
            Ok(Some((pystr::strip(name).to_owned(), compact_json(&inner))))
        }
        _ => Ok(None),
    }
}

/// `text_call(text, callable_names)`: a whole reply that is one JSON call written as text,
/// `(name, arguments JSON)`, when its name is callable; anything else stays prose.
pub fn text_call(text: &str, callable_names: &[&str]) -> CodedResult<Option<(String, String)>> {
    let mut body = pystr::strip(text).to_owned();
    if body.starts_with("```") {
        body = pystr::strip(body.trim_matches('`')).to_owned();
        if let Some(rest) = body.strip_prefix("json") {
            body = pystr::lstrip(rest).to_owned();
        }
    }
    let Some(Json::Object(value)) = loads(&body)? else {
        return Ok(None);
    };
    let Some(Json::String(name)) = value.get("name") else {
        return Ok(None);
    };
    let name = pystr::strip(name).to_owned();
    let empty = Json::Object(JsonMap::new());
    let mut arguments = match value.get("arguments") {
        Some(arguments) => arguments.clone(),
        None => value.get("parameters").cloned().unwrap_or(empty),
    };
    if let Json::String(encoded) = &arguments {
        match loads(if encoded.is_empty() { "{}" } else { encoded })? {
            Some(decoded) => arguments = decoded,
            None => return Ok(None),
        }
    }
    if !callable_names.contains(&name.as_str()) || !arguments.is_object() {
        return Ok(None);
    }
    Ok(Some((name, compact_json(&arguments))))
}

/// `wrap_mechanic_arguments(name, arguments)`: the `call_mechanic` arguments for a canonical
/// mechanic call, byte-stable. Arguments that are not JSON stay their text.
pub fn wrap_mechanic_arguments(name: &str, arguments: &str) -> CodedResult<String> {
    let inner = loads(if arguments.is_empty() { "{}" } else { arguments })?
        .unwrap_or_else(|| Json::String(arguments.to_owned()));
    let mut call = JsonMap::new();
    call.insert("name".into(), name.into());
    call.insert("arguments".into(), inner);
    Ok(compact_json(&Json::Object(call)))
}

/// `project_conversation(message, declared, names)`: a canonical conversation message as the
/// model sent and sees it on a loader route. An assistant call to a mechanic (any name not
/// declared) becomes a `call_mechanic` call with the mechanic's short name; its result is named
/// after `call_mechanic` too.
pub fn project_conversation(
    message: &JsonMap,
    declared: &HashSet<String>,
    names: &MechanicNames,
) -> CodedResult<JsonMap> {
    let mut projected = message.clone();
    let role = projected.get("role").and_then(Json::as_str).map(str::to_owned);
    if let (Some("assistant"), Some(Json::Array(calls))) = (role.as_deref(), projected.get("tool_calls")) {
        let mut wrapped = Vec::with_capacity(calls.len());
        for call in calls {
            let function = match call {
                Json::Object(call) => call.get("function").and_then(Json::as_object),
                _ => None,
            };
            let Some((call, function)) = call.as_object().zip(function) else {
                wrapped.push(call.clone());
                continue;
            };
            match function.get("name") {
                Some(Json::String(name)) if !declared.contains(name) => {
                    let arguments = match function.get("arguments") {
                        Some(value) if py_truthy(value) => py_str(value),
                        _ => "{}".to_owned(),
                    };
                    let mut inner = function.clone();
                    inner.insert("name".into(), MECHANIC_TOOL_NAME.into());
                    inner.insert(
                        "arguments".into(),
                        wrap_mechanic_arguments(&names.short(name), &arguments)?.into(),
                    );
                    let mut call = call.clone();
                    call.insert("function".into(), Json::Object(inner));
                    wrapped.push(Json::Object(call));
                }
                _ => wrapped.push(Json::Object(call.clone())),
            }
        }
        projected.insert("tool_calls".into(), Json::Array(wrapped));
    } else if role.as_deref() == Some("tool") {
        if let Some(Json::String(name)) = projected.get("name") {
            if !declared.contains(name) {
                projected.insert("name".into(), MECHANIC_TOOL_NAME.into());
            }
        }
    }
    Ok(projected)
}
