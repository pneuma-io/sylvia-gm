//! `sylvia_owner::session_reasoning` against the real `_session_reasoning.py`
//! (`tests/vectors/generate_session_reasoning_vectors.py`): 210 sessions (both roles, five
//! models, saved and next-reply efforts including the special and unsupported values), each with
//! `reasoning_state` for three next-reply models, `speed_state` for four saved speeds and the
//! same three models, and `validate_model_preferences` in both modes. A payload is compared as
//! text, so key order counts; a refusal by its message.

use sylvia_base::{CodedResult, Json, JsonMap};
use sylvia_domain::authority::WriterRole;
use sylvia_domain::session::SessionRecordFields;
use sylvia_owner::session_reasoning::{reasoning_state, speed_state, validate_model_preferences};

fn text(value: &Json) -> Option<String> {
    value.as_str().map(str::to_owned)
}

fn outcome<T: Into<Json>>(result: CodedResult<T>) -> Json {
    match result {
        Ok(value) => serde_json::json!({ "ok": value.into() }),
        Err(error) => serde_json::json!({ "error": error.message }),
    }
}

/// A next-reply model as `(provider_id, model_id)`.
fn once(model: &Option<(String, String)>) -> Option<(&str, &str)> {
    model.as_ref().map(|(provider, model)| (provider.as_str(), model.as_str()))
}

fn map(result: CodedResult<JsonMap>) -> Json {
    outcome(result.map(Json::Object))
}

#[test]
fn session_reasoning_matches_python() {
    let path =
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/session_reasoning_vectors.json");
    let source = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", path.display()));
    let vectors: Json = serde_json::from_str(&source).unwrap();
    let choices = vectors["choices"].as_array().unwrap();
    let once_models: Vec<Option<(String, String)>> = vectors["once_models"]
        .as_array()
        .unwrap()
        .iter()
        .map(|model| {
            model
                .as_object()
                .map(|model| (text(&model["provider_id"]).unwrap(), text(&model["model_id"]).unwrap()))
        })
        .collect();
    let speeds: Vec<Option<String>> = vectors["speeds"].as_array().unwrap().iter().map(text).collect();
    let cases = vectors["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 210);
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let spec = &case["session"];
        let session = SessionRecordFields {
            session_id: "session-1".into(),
            campaign_id: None,
            role: WriterRole::parse(spec["role"].as_str().unwrap()).unwrap(),
            provider_id: text(&spec["provider_id"]).unwrap(),
            model_id: text(&spec["model_id"]).unwrap(),
            reasoning_effort: text(&spec["reasoning_effort"]),
            reasoning_effort_once: text(&spec["reasoning_effort_once"]),
            ..SessionRecordFields::default()
        };
        let mut check = |label: String, got: Json, want: &Json| {
            // As text: `serde_json`'s equality ignores key order, which Python's payloads fix.
            let (got, want) = (got.to_string(), want.to_string());
            if got != want {
                failures.push(format!("#{index} {spec} {label}\n  Rust   {got}\n  Python {want}"));
            }
        };
        for (slot, model) in once_models.iter().enumerate() {
            check(
                format!("reasoning {slot}"),
                map(reasoning_state(&session, choices, once(model))),
                &case["reasoning"][slot],
            );
        }
        for (row, speed) in speeds.iter().enumerate() {
            for (slot, model) in once_models.iter().enumerate() {
                check(
                    format!("speed {row}/{slot}"),
                    map(speed_state(&session, choices, speed.as_deref(), once(model))),
                    &case["speed"][row][slot],
                );
            }
        }
        for (slot, flag) in [false, true].into_iter().enumerate() {
            check(
                format!("validate once={flag}"),
                outcome(validate_model_preferences(&session, choices, flag).map(|()| Json::Null)),
                &case["validate"][slot],
            );
        }
    }
    assert!(
        failures.is_empty(),
        "{} differ:\n{}",
        failures.len(),
        failures.iter().take(10).cloned().collect::<Vec<_>>().join("\n")
    );
}
