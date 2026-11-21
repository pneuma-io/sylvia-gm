//! Compare sylvia_domain::content with vectors written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_content_vectors.py` (needs the snapshot's Python).

use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::Json;
use sylvia_domain::content::catalog::definition;
use sylvia_domain::content::pyregex::PyRegex;
use sylvia_domain::content::{get_content_definition, list_content_definitions, validate_content_shape};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/content_vectors.json")).expect("vector file is valid JSON")
}

fn step<'v>(node: &'v mut Json, part: &Json) -> &'v mut Json {
    match part {
        Json::String(key) => &mut node[key.as_str()],
        Json::Number(index) => &mut node[index.as_u64().unwrap() as usize],
        other => panic!("bad path part {other}"),
    }
}

/// The generator's `apply(example, edits)`.
fn apply(example: &Json, edits: &[Json]) -> Json {
    let mut document = example.clone();
    for edit in edits {
        let (op, path, value) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap(), &edit[2]);
        let Some((last, parents)) = path.split_last() else {
            document = value.clone();
            continue;
        };
        let mut parent = &mut document;
        for part in parents {
            parent = step(parent, part);
        }
        match op {
            "set" | "add" => match (parent, last) {
                (Json::Object(map), Json::String(key)) => {
                    map.insert(key.clone(), value.clone());
                }
                (Json::Array(items), Json::Number(index)) => {
                    items[index.as_u64().unwrap() as usize] = value.clone()
                }
                _ => panic!("bad set"),
            },
            "delete" => match (parent, last) {
                (Json::Object(map), Json::String(key)) => {
                    map.shift_remove(key);
                }
                (Json::Array(items), Json::Number(index)) => {
                    items.remove(index.as_u64().unwrap() as usize);
                }
                _ => panic!("bad delete"),
            },
            "repeat" => {
                let target = step(parent, last);
                let first = target[0].clone();
                *target = Json::Array(vec![first; value.as_u64().unwrap() as usize]);
            }
            other => panic!("unknown edit {other}"),
        }
    }
    document
}

#[test]
fn diagnostics_match_python_for_every_definition() {
    let vectors = vectors();
    let mut failures = Vec::new();
    let mut total = 0;
    for entry in vectors["definitions"].as_array().unwrap() {
        let name = entry["name"].as_str().unwrap();
        let example = &definition(name).expect("installed definition").example;
        for case in entry["cases"].as_array().unwrap() {
            total += 1;
            let document = apply(example, case["edits"].as_array().unwrap());
            let actual: Vec<Json> = match validate_content_shape(name, &document, case["file"].as_str()) {
                Ok(()) => Vec::new(),
                Err(error) => error.diagnostics.iter().map(|d| Json::Object(d.as_payload())).collect(),
            };
            let expected = case["diagnostics"].as_array().unwrap();
            if &actual != expected {
                failures.push(format!(
                    "{name} {}: python {}, rust {}",
                    case["edits"],
                    Json::Array(expected.clone()),
                    Json::Array(actual)
                ));
            }
        }
    }
    assert!(total > 3900, "expected the full corpus, got {total}");
    assert!(
        failures.is_empty(),
        "{} of {total} cases differ, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(3)]
    );
}

#[test]
fn patterns_search_like_python() {
    let vectors = vectors();
    let strings: Vec<&str> =
        vectors["pattern_strings"].as_array().unwrap().iter().map(|s| s.as_str().unwrap()).collect();
    let patterns = vectors["patterns"].as_array().unwrap();
    assert_eq!(patterns.len(), 25);
    for case in patterns {
        let source = case["pattern"].as_str().unwrap();
        let pattern = PyRegex::new(source).unwrap();
        for (text, expected) in strings.iter().zip(case["matches"].as_array().unwrap()) {
            assert_eq!(pattern.search(text), expected.as_bool().unwrap(), "{source:?} on {text:?}");
        }
    }
}

#[test]
fn catalogue_matches_python_bytes() {
    let vectors = vectors();
    let catalogue = &vectors["catalogue"];
    let listed = Json::Array(list_content_definitions().into_iter().map(Json::Object).collect());
    assert_eq!(sha256_hex(pyjson::dumps(&listed, Dump::DEFAULT)), catalogue["list_sha256"].as_str().unwrap());
    let hashes = catalogue["definition_sha256"].as_object().unwrap();
    assert_eq!(hashes.len(), 24);
    for (name, expected) in hashes {
        let full = Json::Object(get_content_definition(name).unwrap());
        assert_eq!(sha256_hex(pyjson::dumps(&full, Dump::DEFAULT)), expected.as_str().unwrap(), "{name}");
    }
    assert_eq!(get_content_definition("nope").unwrap_err().to_string(), "Unknown content definition");
}
