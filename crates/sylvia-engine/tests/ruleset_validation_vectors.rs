//! Compare sylvia_engine::ruleset's validators with the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_ruleset_validation_vectors.py` (needs the snapshot's
//! Python). Each case is YAML text, loaded here with `sylvia_base::pyyaml` as PyYAML's
//! `CSafeLoader` loads it in Python; the shipped SRD Lite runtime and the test fixture are read
//! from the repository itself.

use serde_json::{json, Value as Json};
use sha2::{Digest, Sha256};
use sylvia_base::pyyaml::{self, Data};
use sylvia_engine::ruleset::authoring_shape::runtime_authoring_shape_errors;
use sylvia_engine::ruleset::authoring_shape::{
    COMPATIBILITY_EFFECT_HANDLERS, EFFECT_BODY_FIELDS, NEUTRAL_EFFECT_HANDLERS, SCALAR_EFFECT_BODIES,
};
use sylvia_engine::ruleset::pydata::repr;
use sylvia_engine::ruleset::schema_validator::{VALID_EFFECT_KEYS, VALID_PARAM_TYPES, VALID_TRIGGERS};
use sylvia_engine::ruleset::{
    resolve_parameter_declarations, validate_effects, validate_ruleset, validate_ruleset_data,
    RulesetTextError,
};
use sylvia_engine::PyError;

const SRD_LITE: &str = include_str!("../../../rulesets/srd-lite/runtime/ruleset.yaml");
const FIXTURE: &str = include_str!("../../../tests/fixtures/rulesets/signal-and-static/runtime/ruleset.yaml");

fn vectors() -> Json {
    serde_json::from_str(include_str!("vectors/ruleset_validation_vectors.json")).expect("valid JSON")
}

fn outcome<T: serde::Serialize>(result: Result<T, PyError>) -> Json {
    match result {
        Ok(value) => json!({"ok": value}),
        Err(error) => json!({"raise": error.kind.name(), "message": error.message}),
    }
}

/// `validate_ruleset(text)` as the vectors record it.
fn text_outcome(text: &str) -> Json {
    match validate_ruleset(text) {
        Ok(errors) => json!({"ok": errors}),
        Err(RulesetTextError::Load(error)) => {
            json!({"raise": error.class_name(), "message": error.to_string()})
        }
        Err(RulesetTextError::Validation(error)) => {
            json!({"raise": error.kind.name(), "message": error.message})
        }
        Err(RulesetTextError::Recursive) => json!("recursive"),
    }
}

/// Everything the vectors record for one document.
fn check(text: &str, expected: &Json) -> Option<String> {
    let loaded = pyyaml::load(text, pyyaml::Loader::C, pyyaml::Hooks::default())
        .expect("the generator wrote loadable YAML");
    let data = loaded.to_data().expect("not recursive").unwrap_or(Data::None);
    let mut actual = json!({
        "validate": outcome(validate_ruleset_data(&data)),
        "shape": outcome(runtime_authoring_shape_errors(&data)
            .map(|pairs| pairs.into_iter().map(|(path, message)| vec![path, message]).collect::<Vec<_>>())),
    });
    let effects = match &data {
        Data::Dict(_) => match data.get_str("tools") {
            Some(Data::Dict(tools)) => {
                tools.iter().find_map(|(_, tool)| match (tool, tool.get_str("effects")) {
                    (Data::Dict(_), Some(Data::List(effects))) => Some(effects.clone()),
                    _ => None,
                })
            }
            _ => None,
        },
        _ => None,
    };
    if let Some(effects) = effects {
        actual["character_effects"] = outcome(validate_effects("character_validation", &effects));
    }
    if let Data::Dict(pairs) = &data {
        if data.get_str("parameter_declarations").is_some() {
            actual["resolved"] = match resolve_parameter_declarations(pairs) {
                Ok(resolved) => json!({"ok": hex::encode(Sha256::digest(repr(&Data::Dict(resolved))))}),
                Err(error) => json!({"raise": "ParameterDeclarationError", "message": error.message}),
            };
        }
    }
    if expected.get("validate_text").is_some() {
        actual["validate_text"] = text_outcome(text);
    }
    let mut wanted = expected.clone();
    for key in ["text", "source"] {
        wanted.as_object_mut().unwrap().remove(key);
    }
    (actual != wanted).then(|| format!("python {wanted}\n  rust {actual}"))
}

#[test]
fn shipped_rulesets_validate_as_python() {
    let vectors = vectors();
    for case in vectors["shipped"].as_array().unwrap() {
        let text = match case.get("source").and_then(Json::as_str) {
            Some("rulesets/srd-lite/runtime/ruleset.yaml") => SRD_LITE,
            Some("tests/fixtures/rulesets/signal-and-static/runtime/ruleset.yaml") => FIXTURE,
            Some(other) => panic!("unknown source {other}"),
            None => case["text"].as_str().unwrap(),
        };
        if let Some(failure) = check(text, case) {
            panic!("{}: {failure}", case.get("source").and_then(Json::as_str).unwrap_or("riichi"));
        }
    }
}

#[test]
fn declarations_match_python() {
    let vectors = vectors();
    let wanted = &vectors["declarations"];
    let sorted = |items: &[&str]| {
        let mut items = items.to_vec();
        items.sort_unstable();
        json!(items)
    };
    assert_eq!(wanted["VALID_PARAM_TYPES"], sorted(VALID_PARAM_TYPES));
    assert_eq!(wanted["VALID_EFFECT_KEYS"], json!(VALID_EFFECT_KEYS), "kept in sorted order");
    assert_eq!(wanted["VALID_TRIGGERS"], sorted(VALID_TRIGGERS));
    assert_eq!(wanted["SCALAR_EFFECT_BODIES"], sorted(SCALAR_EFFECT_BODIES));
    assert_eq!(wanted["NEUTRAL_EFFECT_HANDLERS"], json!(NEUTRAL_EFFECT_HANDLERS));
    assert_eq!(wanted["COMPATIBILITY_EFFECT_HANDLERS"], json!(COMPATIBILITY_EFFECT_HANDLERS));
    let fields: serde_json::Map<String, Json> =
        EFFECT_BODY_FIELDS.iter().map(|(name, fields)| ((*name).to_owned(), json!(fields))).collect();
    assert_eq!(wanted["EFFECT_BODY_FIELDS"], Json::Object(fields), "same handlers, fields in the same order");
}

#[test]
fn yaml_text_validates_as_python() {
    let vectors = vectors();
    let texts = vectors["texts"].as_array().unwrap();
    assert!(texts.len() >= 30);
    for case in texts {
        let text = case["text"].as_str().unwrap();
        assert_eq!(text_outcome(text), case["validate_text"], "{text:?}");
    }
    let checked =
        vectors["cases"].as_array().unwrap().iter().filter(|case| case.get("validate_text").is_some());
    assert!(checked.count() > 2000, "the small mutated documents also go through the text entry point");
}

/// `differences.md` row 24: a handler body's branch that is a YAML alias of its effect's own
/// branch validates exactly as a written-out copy would.
#[test]
fn aliased_branches_validate_as_copies() {
    let errors = |text: &str| {
        let data = pyyaml::load(text, pyyaml::Loader::C, pyyaml::Hooks::default())
            .unwrap()
            .to_data()
            .unwrap()
            .unwrap();
        validate_ruleset_data(&data).unwrap()
    };
    let aliased = "meta: {id: ok}\ntools:\n  t:\n    effects:\n      - do: &b [{bogus: 1}]\n        for_each: {do: *b}\n";
    let copied = "meta: {id: ok}\ntools:\n  t:\n    effects:\n      - do: [{bogus: 1}]\n        for_each: {do: [{bogus: 1}]}\n";
    let found = errors(aliased);
    assert_eq!(found, errors(copied));
    // Python skips the aliased list and reports one error; the copy reports two.
    assert_eq!(found.len(), 2, "{found:?}");
}

#[test]
fn every_mutated_ruleset_validates_as_python() {
    let vectors = vectors();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 3000, "expected the full corpus");
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        if let Some(failure) = check(case["text"].as_str().unwrap(), case) {
            failures.push(format!("case {index}: {failure}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ, first: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(6)]
    );
}
