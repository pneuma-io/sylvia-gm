//! Compare sylvia_app::builder_prompts with the frozen Python's `build_builder_system_prompt`.
//!
//! Regenerate with `tests/vectors/generate_builder_prompt_vectors.py` (needs the snapshot's
//! Python).

use serde_json::json;
use sylvia_app::builder_prompts::{build_builder_system_prompt, AvailableOptions};
use sylvia_base::hash::sha256_hex;
use sylvia_base::{pyjson, Json};

const VECTORS: &str = include_str!("vectors/builder_prompt_vectors.json");

#[test]
fn builder_prompt_matches_python() {
    let vectors = pyjson::loads(VECTORS).unwrap();
    let default = build_builder_system_prompt(None, "append", &AvailableOptions::default()).unwrap();
    assert_eq!(default, vectors["default_prompt"].as_str().unwrap(), "the default prompt differs");
    let mut failures = Vec::new();
    let cases = vectors["cases"].as_array().unwrap();
    for case in cases {
        let given = &case["options"];
        let option = |key: &str| given.get(key).filter(|value| !value.is_null());
        let options = AvailableOptions {
            rulesets: option("rulesets"),
            settings: option("settings"),
            plans: option("plans"),
            characters: option("characters"),
            companions: option("companions"),
        };
        let actual = match build_builder_system_prompt(
            case["override"].as_str(),
            case["mode"].as_str().unwrap(),
            &options,
        ) {
            Ok(text) => json!(["ok", sha256_hex(text.as_bytes()), text.chars().count()]),
            Err(error) => Json::from(vec![Json::from(error.python_class()), Json::from(error.message())]),
        };
        if actual != case["outcome"] {
            failures.push(format!(
                "{} {} {given}: python {}, rust {actual}",
                case["override"], case["mode"], case["outcome"]
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(8)]
    );
}
