//! Compare sylvia_base::pyyaml with vectors written by the frozen Python reference (PyYAML 6.0.3
//! with libyaml).
//!
//! Regenerate with `tests/vectors/generate_pyyaml_vectors.py` (needs the snapshot's Python).
//! Every document is loaded with both loaders: its events, its node graph, and its value or
//! error must match Python's record exactly.

use serde_json::{json, Value as J};
use std::collections::HashMap;
use sylvia_base::pyjson::float_repr;
use sylvia_base::pyyaml::{
    self, Date, DateTime, Event, EventKind, Hooks, Loaded, Loader, Mark, NodeGraph, NodeId, NodeKind, Object,
    ObjectId, Value, YamlError,
};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/pyyaml_vectors.json")).expect("vector file is valid JSON")
}

fn mark(mark: &Mark) -> J {
    json!([mark.line, mark.column, mark.index])
}

/// `loader` decides how a plain scalar's style shows: `None` in the pure parser, `""` in libyaml's.
fn event_record(event: &Event, loader: Loader) -> J {
    let base = |name: &str| vec![json!(name), mark(&event.start_mark), mark(&event.end_mark)];
    let mut record = match &event.kind {
        EventKind::StreamStart => base("StreamStartEvent"),
        EventKind::StreamEnd => base("StreamEndEvent"),
        EventKind::DocumentStart { explicit, version, tags } => {
            let mut record = base("DocumentStartEvent");
            record.push(json!(explicit));
            record.push(version.as_ref().map_or(J::Null, |(major, minor)| {
                json!([major.parse::<i64>().unwrap(), minor.parse::<i64>().unwrap()])
            }));
            record.push(tags.as_ref().map_or(J::Null, |tags| {
                let mut sorted = tags.clone();
                sorted.sort();
                json!(sorted.into_iter().map(|(h, p)| json!([h, p])).collect::<Vec<_>>())
            }));
            record
        }
        EventKind::DocumentEnd { explicit } => {
            let mut record = base("DocumentEndEvent");
            record.push(json!(explicit));
            record
        }
        EventKind::Alias { anchor } => {
            let mut record = base("AliasEvent");
            record.push(json!(anchor));
            record
        }
        EventKind::Scalar { anchor, tag, implicit, value, style } => {
            let mut record = base("ScalarEvent");
            record.extend([
                json!(anchor),
                json!(tag),
                json!([implicit.0, implicit.1]),
                json!(value),
                plain_style(*style, loader),
            ]);
            record
        }
        EventKind::SequenceStart { anchor, tag, implicit, flow_style } => {
            let mut record = base("SequenceStartEvent");
            record.extend([json!(anchor), json!(tag), json!(implicit), json!(flow_style)]);
            record
        }
        EventKind::MappingStart { anchor, tag, implicit, flow_style } => {
            let mut record = base("MappingStartEvent");
            record.extend([json!(anchor), json!(tag), json!(implicit), json!(flow_style)]);
            record
        }
        EventKind::SequenceEnd => base("SequenceEndEvent"),
        EventKind::MappingEnd => base("MappingEndEvent"),
    };
    J::Array(std::mem::take(&mut record))
}

fn plain_style(style: Option<char>, loader: Loader) -> J {
    match (style, loader) {
        (None, Loader::C) => json!(""),
        (style, _) => json!(style.map(String::from)),
    }
}

fn node_record(graph: &NodeGraph, id: NodeId, ids: &mut HashMap<NodeId, usize>) -> J {
    if let Some(seen) = ids.get(&id) {
        return json!(["ref", seen]);
    }
    let number = ids.len();
    ids.insert(id, number);
    let node = graph.node(id);
    let head = |kind: &str| {
        vec![json!(kind), json!(number), json!(node.tag), mark(&node.start_mark), mark(&node.end_mark)]
    };
    J::Array(match &node.kind {
        NodeKind::Scalar(value) => {
            let mut record = head("scalar");
            record.extend([json!(value), json!(node.style)]);
            record
        }
        NodeKind::Sequence(items) => {
            let mut record = head("seq");
            record.push(json!(node.flow_style));
            record.push(J::Array(items.iter().map(|item| node_record(graph, *item, ids)).collect()));
            record
        }
        NodeKind::Mapping(pairs) => {
            let mut record = head("map");
            record.push(json!(node.flow_style));
            record.push(J::Array(
                pairs
                    .iter()
                    .map(|(k, v)| json!([node_record(graph, *k, ids), node_record(graph, *v, ids)]))
                    .collect(),
            ));
            record
        }
    })
}

fn python_float(value: f64) -> String {
    if value.is_nan() {
        return "nan".into();
    }
    match float_repr(value).as_str() {
        "Infinity" => "inf".into(),
        "-Infinity" => "-inf".into(),
        other => other.into(),
    }
}

fn value_record(loaded: &Loaded, value: &Value, ids: &mut HashMap<ObjectId, usize>) -> J {
    match value {
        Value::None => json!(["none"]),
        Value::Bool(flag) => json!(["bool", flag]),
        Value::Int(number) => json!(["int", number.to_string()]),
        Value::Float(number) => json!(["float", python_float(*number)]),
        Value::Str(text) => json!(["str", text]),
        Value::Bytes(bytes) => json!(["bytes", bytes.iter().map(|b| format!("{b:02x}")).collect::<String>()]),
        Value::DateTime(datetime) => {
            json!(["datetime", datetime.isoformat(), datetime.offset_seconds.is_some()])
        }
        Value::Date(date) => json!(["date", date.isoformat()]),
        Value::Tuple(items) => {
            json!(["tuple", items.iter().map(|item| value_record(loaded, item, ids)).collect::<Vec<_>>()])
        }
        Value::List(id) | Value::Dict(id) | Value::Set(id) => {
            if let Some(seen) = ids.get(id) {
                return json!(["ref", seen]);
            }
            let number = ids.len();
            ids.insert(*id, number);
            match loaded.object(*id) {
                Object::List(items) => {
                    json!([
                        "list",
                        number,
                        items.iter().map(|item| value_record(loaded, item, ids)).collect::<Vec<_>>()
                    ])
                }
                Object::Dict(pairs) => json!([
                    "dict",
                    number,
                    pairs
                        .iter()
                        .map(|(k, v)| json!([value_record(loaded, k, ids), value_record(loaded, v, ids)]))
                        .collect::<Vec<_>>()
                ]),
                Object::Set(items) => {
                    let mut records: Vec<J> =
                        items.iter().map(|item| value_record(loaded, item, ids)).collect();
                    records.sort_by_key(python_json_dumps);
                    json!(["set", number, records])
                }
                Object::Empty => panic!("an unfinished container"),
            }
        }
    }
}

/// `json.dumps(record)` with its default separators, the sort key the generator used for sets.
fn python_json_dumps(record: &J) -> String {
    sylvia_base::pyjson::dumps(record, sylvia_base::pyjson::Dump::DEFAULT)
}

fn error_record(error: &YamlError) -> J {
    let mut record = json!({
        "class": error.class_name(),
        "text": error.to_string(),
        "context": null, "problem": null, "note": null, "context_mark": null, "problem_mark": null,
    });
    if let Some(marked) = error.marked() {
        record["context"] = json!(marked.context);
        record["problem"] = json!(marked.problem);
        record["note"] = json!(marked.note);
        record["context_mark"] = marked.context_mark.as_ref().map_or(J::Null, mark);
        record["problem_mark"] = marked.problem_mark.as_ref().map_or(J::Null, mark);
    }
    record
}

fn load_record(text: &str, loader: Loader) -> J {
    let mut out = serde_json::Map::new();
    match pyyaml::parse(text, loader) {
        Ok(events) => out.insert(
            "events".into(),
            J::Array(events.iter().map(|event| event_record(event, loader)).collect()),
        ),
        Err(error) => out.insert("events_error".into(), error_record(&error)),
    };
    match pyyaml::compose(text, loader) {
        Ok(composed) => out.insert(
            "node".into(),
            composed.root.map_or(J::Null, |root| node_record(&composed.graph, root, &mut HashMap::new())),
        ),
        Err(error) => out.insert("compose_error".into(), error_record(&error)),
    };
    match pyyaml::load(text, loader, Hooks::default()) {
        Ok(loaded) => out.insert(
            "value".into(),
            match &loaded.root {
                Some(root) => value_record(&loaded, root, &mut HashMap::new()),
                None => json!(["none"]),
            },
        ),
        Err(error) => out.insert("error".into(), error_record(&error)),
    };
    J::Object(out)
}

fn holds_wide_int(record: &J) -> bool {
    match record {
        J::Array(items) => {
            (items.len() == 2
                && items[0] == "int"
                && items[1].as_str().is_some_and(|text| text.parse::<i64>().is_err()))
                || items.iter().any(holds_wide_int)
        }
        _ => false,
    }
}

/// Python builds integers beyond i64 exactly; the port refuses them (`differences.md` row 5).
fn declare_int_refusal(record: &mut J) {
    if record.get("value").is_some_and(holds_wide_int) {
        let record = record.as_object_mut().unwrap();
        record.remove("value");
        record.insert(
            "error".into(),
            json!({
                "class": "ValueError", "text": sylvia_base::pystr::INT_RANGE_MESSAGE,
                "context": null, "problem": null, "note": null, "context_mark": null, "problem_mark": null,
            }),
        );
    }
}

fn first_difference(expected: &J, actual: &J, path: String) -> Option<String> {
    match (expected, actual) {
        (J::Object(e), J::Object(a)) => {
            for key in e.keys().chain(a.keys()) {
                let (x, y) = (e.get(key).unwrap_or(&J::Null), a.get(key).unwrap_or(&J::Null));
                if let Some(found) = first_difference(x, y, format!("{path}.{key}")) {
                    return Some(found);
                }
            }
            None
        }
        (J::Array(e), J::Array(a)) if e.len() == a.len() => {
            e.iter().zip(a).enumerate().find_map(|(i, (x, y))| first_difference(x, y, format!("{path}[{i}]")))
        }
        _ if expected == actual => None,
        _ => Some(format!("{path}: expected {expected} got {actual}")),
    }
}

#[test]
fn loading_matches_python_for_both_loaders() {
    let vectors = vectors();
    assert_eq!(vectors["pyyaml"], "6.0.3");
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 800);
    let mut failures = Vec::new();
    for case in cases {
        let text = case["text"].as_str().unwrap();
        for (name, loader) in [("pure", Loader::Pure), ("c", Loader::C)] {
            let actual = load_record(text, loader);
            let mut expected = case[name].clone();
            if expected.get("lone_surrogate").is_some() {
                // differences.md row 3: refused where Python builds a lone surrogate.
                if actual["events_error"]["class"] != "UnicodeError"
                    || actual["error"]["class"] != "UnicodeError"
                {
                    failures.push(format!(
                        "{} [{name}] {text:?}\n    expected the lone-surrogate refusal, got {actual}",
                        case["name"]
                    ));
                }
                continue;
            }
            declare_int_refusal(&mut expected);
            if let Some(difference) = first_difference(&expected, &actual, String::new()) {
                failures
                    .push(format!("{} [{name}] {text:?}\n    {difference}", case["name"].as_str().unwrap()));
            }
        }
    }
    let shown: Vec<&String> = failures.iter().take(40).collect();
    assert!(
        failures.is_empty(),
        "{} of {} loads differ:\n{}",
        failures.len(),
        cases.len() * 2,
        shown.iter().map(|s| s.as_str()).collect::<Vec<_>>().join("\n")
    );
}

/// Rebuild a Python value from its record, containers shared where the record refers back.
fn value_from_record(record: &J, objects: &mut Vec<Object>, ids: &mut HashMap<u64, ObjectId>) -> Value {
    let items = record.as_array().unwrap();
    let text = |i: usize| items[i].as_str().unwrap();
    match text(0) {
        "none" => Value::None,
        "bool" => Value::Bool(items[1].as_bool().unwrap()),
        "int" => Value::Int(text(1).parse().unwrap()),
        "float" => Value::Float(match text(1) {
            "nan" => f64::NAN,
            "inf" => f64::INFINITY,
            "-inf" => f64::NEG_INFINITY,
            other => other.parse().unwrap(),
        }),
        "str" => Value::Str(text(1).to_owned()),
        "bytes" => Value::Bytes(
            (0..text(1).len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&text(1)[i..i + 2], 16).unwrap())
                .collect(),
        ),
        "date" => Value::Date(parse_date(text(1))),
        "datetime" => Value::DateTime(parse_datetime(text(1))),
        "tuple" => Value::Tuple(
            items[1].as_array().unwrap().iter().map(|item| value_from_record(item, objects, ids)).collect(),
        ),
        "ref" => {
            let id = ids[&items[1].as_u64().unwrap()];
            match &objects[id.0] {
                Object::Dict(_) => Value::Dict(id),
                Object::Set(_) => Value::Set(id),
                _ => Value::List(id),
            }
        }
        kind @ ("list" | "dict" | "set") => {
            let id = ObjectId(objects.len());
            objects.push(match kind {
                "list" => Object::List(Vec::new()),
                "dict" => Object::Dict(Vec::new()),
                _ => Object::Set(Vec::new()),
            });
            ids.insert(items[1].as_u64().unwrap(), id);
            let children = items[2].as_array().unwrap();
            match kind {
                "list" => {
                    let built = children.iter().map(|item| value_from_record(item, objects, ids)).collect();
                    objects[id.0] = Object::List(built);
                    Value::List(id)
                }
                "dict" => {
                    let built = children
                        .iter()
                        .map(|pair| {
                            (
                                value_from_record(&pair[0], objects, ids),
                                value_from_record(&pair[1], objects, ids),
                            )
                        })
                        .collect();
                    objects[id.0] = Object::Dict(built);
                    Value::Dict(id)
                }
                _ => {
                    let built = children.iter().map(|item| value_from_record(item, objects, ids)).collect();
                    objects[id.0] = Object::Set(built);
                    Value::Set(id)
                }
            }
        }
        other => panic!("unknown record kind {other}"),
    }
}

fn parse_date(text: &str) -> Date {
    Date {
        year: text[0..4].parse().unwrap(),
        month: text[5..7].parse().unwrap(),
        day: text[8..10].parse().unwrap(),
    }
}

/// `datetime.isoformat()` back to its fields.
fn parse_datetime(text: &str) -> DateTime {
    let date = parse_date(&text[..10]);
    let rest = &text[11..];
    let (clock, offset) = match rest.find(['+', '-']) {
        Some(at) => (&rest[..at], Some(&rest[at..])),
        None => (rest, None),
    };
    let (hms, micro) = clock.split_once('.').unwrap_or((clock, "0"));
    let parts: Vec<u32> = hms.split(':').map(|p| p.parse().unwrap()).collect();
    let offset_seconds = offset.map(|offset| {
        let sign = if offset.starts_with('-') { -1 } else { 1 };
        let fields: Vec<i32> = offset[1..].split(':').map(|p| p.parse().unwrap()).collect();
        sign * (fields[0] * 3600 + fields[1] * 60 + fields.get(2).copied().unwrap_or(0))
    });
    DateTime {
        date,
        hour: parts[0],
        minute: parts[1],
        second: parts[2],
        microsecond: micro.parse().unwrap(),
        offset_seconds,
    }
}

#[test]
fn dumping_matches_python() {
    let vectors = vectors();
    let dumps = vectors["dumps"].as_array().unwrap();
    assert!(dumps.len() > 600);
    let mut failures = Vec::new();
    let mut wide = 0;
    for dump in dumps {
        if holds_wide_int(&dump["value"]) {
            // differences.md row 5: such a value cannot be loaded, so it is never dumped.
            wide += 1;
            continue;
        }
        let mut objects = Vec::new();
        let root = value_from_record(&dump["value"], &mut objects, &mut HashMap::new());
        let loaded = Loaded { root: Some(root), objects };
        let expected = dump["text"].as_str().expect("every vector dump succeeded in Python");
        match pyyaml::safe_dump(&loaded) {
            Ok(text) if text == expected => {}
            other => {
                failures.push(format!("{}\n  expected {expected:?}\n  got      {other:?}", dump["value"]))
            }
        }
    }
    assert!(wide < 20, "{wide} dumps hold integers beyond i64");
    let shown = failures.iter().take(20).cloned().collect::<Vec<_>>().join("\n");
    assert!(failures.is_empty(), "{} of {} dumps differ:\n{shown}", failures.len(), dumps.len());
}

#[test]
fn a_dumped_document_loads_back_to_the_same_value() {
    let vectors = vectors();
    for dump in vectors["dumps"].as_array().unwrap().iter().take(200) {
        let text = dump["text"].as_str().unwrap();
        let first = pyyaml::safe_load(text).unwrap();
        let again = pyyaml::safe_load(&pyyaml::safe_dump(&first).unwrap()).unwrap();
        assert_eq!(value_record_root(&first), value_record_root(&again), "{text:?}");
    }
}

fn value_record_root(loaded: &Loaded) -> J {
    match &loaded.root {
        Some(root) => value_record(loaded, root, &mut HashMap::new()),
        None => json!(["none"]),
    }
}

// ── Loader hooks: the overrides Sylvia's loader subclasses make ─────────────────────────────

/// `_UniqueRuntimeLoader.flatten_mapping` (CSafeLoader): every authored key is unique text.
fn unique_keys_flatten(constructor: &mut pyyaml::Constructor, node: NodeId) -> Result<(), YamlError> {
    if !constructor.flagged.contains(&node) {
        let NodeKind::Mapping(pairs) = constructor.graph.node(node).kind.clone() else {
            unreachable!("called on mappings")
        };
        let mut seen: Vec<String> = Vec::new();
        for (key_node, _) in pairs {
            let key = if constructor.graph.node(key_node).tag == "tag:yaml.org,2002:merge" {
                Value::Str("<<".into())
            } else {
                constructor.construct_object(key_node, false)?
            };
            match key {
                Value::Str(text) if !seen.contains(&text) => seen.push(text),
                _ => return Err(python_error("ValueError", "Runtime fields must be unique text keys")),
            }
        }
        constructor.flagged.insert(node);
    }
    constructor.base_flatten_mapping(node)
}

/// `_SourceLoader.construct_mapping` (SafeLoader): unique text field names, from the pairs.
fn text_keys_mapping(
    constructor: &mut pyyaml::Constructor,
    node: NodeId,
    deep: bool,
) -> Result<Vec<(Value, Value)>, YamlError> {
    constructor.flatten_mapping(node)?;
    let mut result: Vec<(Value, Value)> = Vec::new();
    for (key, value) in constructor.construct_pairs(node, deep)? {
        let duplicate = result.iter().any(|(existing, _)| pyyaml::key_eq(existing, &key));
        if !matches!(key, Value::Str(_)) || duplicate {
            return Err(python_error("ValueError", "Ruleset source requires unique text field names"));
        }
        result.push((key, value));
    }
    Ok(result)
}

/// A bounded `construct_document`: count the distinct nodes before constructing.
fn node_count_document(
    constructor: &mut pyyaml::Constructor,
    root: NodeId,
) -> Result<Option<Value>, YamlError> {
    let (mut stack, mut seen) = (vec![root], std::collections::HashSet::new());
    while let Some(current) = stack.pop() {
        if !seen.insert(current) {
            continue;
        }
        match &constructor.graph.node(current).kind {
            NodeKind::Scalar(_) => {}
            NodeKind::Sequence(items) => stack.extend(items.iter().copied()),
            NodeKind::Mapping(pairs) => stack.extend(pairs.iter().flat_map(|(k, v)| [*k, *v])),
        }
    }
    if seen.len() > 12 {
        return Err(python_error("ValueError", "too many nodes"));
    }
    constructor.base_construct_document(root)
}

fn python_error(class: &'static str, message: &str) -> YamlError {
    YamlError::Python { class, message: message.to_owned() }
}

#[test]
fn loader_hooks_match_python_subclasses() {
    let vectors = vectors();
    let cases = vectors["hooks"].as_array().unwrap();
    assert!(cases.len() >= 20);
    let loaders: [(&str, Loader, Hooks); 3] = [
        ("unique_keys", Loader::C, Hooks { flatten_mapping: Some(unique_keys_flatten), ..Hooks::default() }),
        ("text_keys", Loader::Pure, Hooks { construct_mapping: Some(text_keys_mapping), ..Hooks::default() }),
        (
            "node_count",
            Loader::Pure,
            Hooks { construct_document: Some(node_count_document), ..Hooks::default() },
        ),
    ];
    for case in cases {
        let text = case["text"].as_str().unwrap();
        for (name, loader, hooks) in &loaders {
            let actual = match pyyaml::load(text, *loader, *hooks) {
                Ok(loaded) => json!({"value": value_record_root(&loaded)}),
                Err(error) => json!({"error": error_record(&error)}),
            };
            assert_eq!(actual, case[*name], "{name} {text:?}");
        }
    }
}

/// differences.md: Python anchors a date it reaches twice (the same object); the port, whose
/// dates carry no identity, writes it in full each time. Both load back to equal values.
#[test]
fn a_date_reached_twice_is_written_in_full() {
    let vectors = vectors();
    let expected_rust = [
        "a: 2026-01-01\nb: 2026-01-01\n",
        "- 2026-01-01 12:00:00\n- 2026-01-01 12:00:00\n- 2026-01-01 12:00:00\n",
    ];
    for (case, rust_text) in vectors["date_alias"].as_array().unwrap().iter().zip(expected_rust) {
        let python_text = case["python"].as_str().unwrap();
        assert!(python_text.contains("&id001") && python_text.contains("*id001"), "{python_text:?}");
        let loaded = pyyaml::safe_load(case["text"].as_str().unwrap()).unwrap();
        let dumped = pyyaml::safe_dump(&loaded).unwrap();
        assert_eq!(dumped, rust_text);
        let ours = pyyaml::safe_load(&dumped).unwrap();
        let theirs = pyyaml::safe_load(python_text).unwrap();
        assert_eq!(value_record_root(&ours), value_record_root(&theirs));
    }
}

/// `yaml.load(bytes, Loader=...)`: valid UTF-8 bytes load as the text does, marks named
/// `<byte string>`.
#[test]
fn byte_input_marks_carry_the_byte_string_name() {
    let vectors = vectors();
    for case in vectors["byte_inputs"].as_array().unwrap() {
        let text = case["text"].as_str().unwrap();
        for (name, loader) in [("pure", Loader::Pure), ("c", Loader::C)] {
            let actual = match pyyaml::load_named(text, "<byte string>", loader, Hooks::default()) {
                Ok(loaded) => json!({"value": value_record_root(&loaded)}),
                Err(error) => json!({"error": error_record(&error)}),
            };
            let expected = &case[name];
            let key = if expected.get("value").is_some() { "value" } else { "error" };
            assert_eq!(actual[key], expected[key], "{name} {text:?}");
        }
    }
}
