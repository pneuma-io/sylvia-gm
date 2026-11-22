//! Compare sylvia_app::library_sources over the real `AuthorityStore` with Python's reader over
//! its `AuthorityOwner`, on lane 3's package-read base.
//!
//! Regenerate with `tests/vectors/generate_library_store_vectors.py` (needs the snapshot's Python).

use flate2::read::GzDecoder;
use serde_json::json;
use std::io::Read;
use std::path::Path;
use sylvia_app::library_sources::LibrarySourceReader;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_store::{AuthorityStore, StoreError};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/library_store_vectors.json")).expect("vector file is valid JSON")
}

/// The generator's `shrink`: strings over 256 characters as their length and SHA-256.
fn shrink(value: Json) -> Json {
    match value {
        Json::String(text) if text.chars().count() > 256 => {
            json!({"length": text.chars().count(), "sha256": sha256_hex(text.as_bytes())})
        }
        Json::Object(map) => Json::Object(map.into_iter().map(|(key, item)| (key, shrink(item))).collect()),
        Json::Array(items) => Json::Array(items.into_iter().map(shrink).collect()),
        other => other,
    }
}

fn observed(result: Result<Json, StoreError>) -> Json {
    match result {
        Ok(value) => json!({"ok": shrink(value)}),
        Err(error) => match error.rejection() {
            Some(coded) => json!({"error": coded.error_type, "code": coded.code, "message": coded.message}),
            None => json!({"error": "uncoded", "code": null, "message": error.to_string()}),
        },
    }
}

fn object(map: JsonMap) -> Json {
    Json::Object(map)
}

#[test]
fn library_sources_over_the_store_match_python() {
    let vectors = vectors();
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let mut base = Vec::new();
    GzDecoder::new(std::fs::File::open(root.join(vectors["base"].as_str().unwrap())).unwrap())
        .read_to_end(&mut base)
        .unwrap();
    let folder = Path::new(env!("CARGO_TARGET_TMPDIR")).join("library-store-vectors");
    let _ = std::fs::remove_dir_all(&folder);
    std::fs::create_dir_all(&folder).unwrap();
    let database = folder.join("authority.sqlite3");
    std::fs::write(&database, base).unwrap();
    let store = AuthorityStore::open_for_application_owner(&database, None).unwrap();
    let operations = vectors["operations"].as_array().unwrap();
    assert!(operations.len() > 50);
    let mut failures = Vec::new();
    for op in operations {
        let reader = LibrarySourceReader {
            owner: &store,
            ruleset_id: op["reader"][0].as_str().unwrap().into(),
            setting_id: op["reader"][1].as_str().map(str::to_owned),
        };
        let text = |key: &str| op[key].as_str().unwrap();
        let result = match text("op") {
            "candidates" => reader
                .candidates(text("query"))
                .map(|items| Json::Array(items.into_iter().map(object).collect())),
            "files" => reader.files(text("candidate_id")).map(object),
            "read" => reader.read(text("candidate_id"), text("path")).map(object),
            "record" => reader.record(text("candidate_id"), text("prefix")).map(object),
            other => panic!("{other}"),
        };
        let actual = observed(result);
        if actual != op["outcome"] {
            failures.push(format!("{} {}: python {}, rust {actual}", op["reader"], op["op"], op["outcome"]));
        }
    }
    let shown: Vec<String> = failures.iter().take(6).map(|f| f.chars().take(900).collect()).collect();
    assert!(failures.is_empty(), "{} of {} differ: {:#?}", failures.len(), operations.len(), shown);
}
