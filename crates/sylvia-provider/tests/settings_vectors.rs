//! Compare the read-only provider settings (config C2) with vectors written by the frozen
//! Python reference.
//!
//! Regenerate with `tests/vectors/generate_settings_vectors.py` (needs the snapshot's Python).
//! Protection is the same deterministic fake seal on both sides and the clock is fixed; every
//! credential is a disposable test value.

use serde_json::{json, Value as J};
use std::path::PathBuf;
use sylvia_provider::config::env::Environment;
use sylvia_provider::config::protection::{self, Seal};
use sylvia_provider::config::provider_settings::{self as settings, ConfigSources};
use sylvia_provider::config::{data_from_json, reply_json, ConfigFailure, Data};
use sylvia_provider::context_capacity::{resolve_context_capacity, CapacityInputs};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/settings_vectors.json")).expect("vector file is valid JSON")
}

struct FakeSeal;

impl Seal for FakeSeal {
    fn seal(&self, value: &[u8], decrypt: bool) -> Result<Vec<u8>, ConfigFailure> {
        if decrypt {
            let Some(rest) = value.strip_prefix(b"FAKE") else {
                return Err(ConfigFailure::Config(protection::UNAVAILABLE_MESSAGE.into()));
            };
            Ok(rest.iter().rev().copied().collect())
        } else {
            let mut out = b"FAKE".to_vec();
            out.extend(value.iter().rev());
            Ok(out)
        }
    }
}

fn token(value: J) -> J {
    let text = value
        .to_string()
        .replace(&json_inner(protection::UNAVAILABLE_MESSAGE), "<UNAVAILABLE>")
        .replace(&json_inner(protection::PROTECTION), "<PROTECTION>");
    serde_json::from_str(&text).unwrap()
}

fn json_inner(text: &str) -> String {
    let quoted = J::String(text.to_owned()).to_string();
    quoted[1..quoted.len() - 1].to_owned()
}

fn reply(result: Result<Data, ConfigFailure>) -> J {
    let failure = |error: ConfigFailure| {
        let class = match &error {
            ConfigFailure::Config(_) => "ConfigError",
            ConfigFailure::Python { class, .. } => class,
        };
        json!({"error": {"class": class, "message": error.to_string()}})
    };
    match result.and_then(|value| reply_json(&value)) {
        Ok(value) => token(json!({"value": value})),
        Err(error) => token(failure(error)),
    }
}

/// Equal as JSON text: `Value`'s own equality ignores object key order, which the reply's
/// bytes depend on.
fn same(a: &J, b: &J) -> bool {
    serde_json::to_string(a).unwrap() == serde_json::to_string(b).unwrap()
}

fn data(value: &J) -> Data {
    data_from_json(value).unwrap()
}

fn temporary_folder() -> PathBuf {
    let mut suffix = [0u8; 8];
    getrandom::getrandom(&mut suffix).unwrap();
    let folder = std::env::temp_dir().join(format!(
        "sylvia-settings-test-{}",
        suffix.iter().map(|b| format!("{b:02x}")).collect::<String>()
    ));
    std::fs::create_dir(&folder).unwrap();
    folder
}

fn default_config() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../config/config.yaml")
}

#[test]
fn provider_settings_and_session_readers_match_python() {
    let vectors = vectors();
    let env = Environment::default();
    let default_config = default_config();
    let sources = ConfigSources { default_config: &default_config, seal: &FakeSeal, env: &env };
    let folder = temporary_folder();
    let path = folder.join("config.yaml");
    let mut failures = Vec::new();
    for case in vectors["documents"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let _ = std::fs::remove_file(&path);
        if let Some(text) = case["user"].as_str() {
            std::fs::write(&path, text.replace("<PROTECTION>", protection::PROTECTION)).unwrap();
        }
        let actual = reply(settings::provider_settings(sources, &path));
        if !same(&actual, &case["provider_settings"]) {
            failures.push(format!("{name}\n  expected {}\n  got      {actual}", case["provider_settings"]));
        }
        let speeds: Vec<J> = [json!("s1"), json!(""), json!(5)]
            .iter()
            .map(|session| reply(settings::session_speed_settings(sources, &path, &data(session))))
            .collect();
        if !same(&J::Array(speeds.clone()), &case["session_speed"]) {
            failures.push(format!(
                "{name} session_speed\n  expected {}\n  got      {}",
                case["session_speed"],
                J::Array(speeds)
            ));
        }
    }
    std::fs::write(
        &path,
        "providers:\n  openai:\n    enabled: true\n    models: [{id: gpt-5}, {id: 3}, 'x']\n  lm_studio:\n    models: [{id: q}]\n\
         session_speed: {s1: fast, s2: 'NO', s3: 5}\n\
         session_model_once: {s1: {provider_id: openai, model_id: openai/gpt-5}, s2: {provider_id: openai, model_id: nope}, s3: [1], s4: {provider_id: 9}}\n",
    )
    .unwrap();
    for case in vectors["require"].as_array().unwrap() {
        let actual = reply(settings::require_configured_model(
            sources,
            &path,
            &data(&case["provider_id"]),
            &data(&case["model_id"]),
        ));
        let expected = json!({"value": case.get("value"), "error": case.get("error")});
        let actual = json!({"value": actual.get("value"), "error": actual.get("error")});
        if !same(&actual, &expected) {
            failures.push(format!("require {case}\n  got {actual}"));
        }
    }
    for case in vectors["sessions"].as_array().unwrap() {
        let session = data(&case["session_id"]);
        let speed = reply(settings::session_speed_settings(sources, &path, &session));
        let once = reply(settings::session_model_once_settings(sources, &path, &session));
        if !same(&speed, &case["speed"]) || !same(&once, &case["model_once"]) {
            failures.push(format!("session {}\n  got {speed} {once}", case["session_id"]));
        }
    }
    std::fs::remove_dir_all(&folder).unwrap();
    assert!(failures.is_empty(), "{} differences:\n{}", failures.len(), failures.join("\n"));
}

#[test]
fn discovery_model_matches_python() {
    let vectors = vectors();
    let now = vectors["now"].as_str().unwrap().to_owned();
    let mut failures = Vec::new();
    for case in vectors["discovery"].as_array().unwrap() {
        let preset_id = case["preset_id"].as_str().unwrap();
        let row = data(&case["row"]);
        let actual =
            reply(Ok(settings::discovery_model(&row, preset_id, &|| now.clone()).unwrap_or(Data::None)));
        if !same(&actual["value"], &case["value"]) {
            failures.push(format!(
                "{preset_id} {}\n  expected {}\n  got      {}",
                case["row"], case["value"], actual["value"]
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} differences:\n{}",
        failures.len(),
        failures.iter().take(15).cloned().collect::<Vec<_>>().join("\n")
    );
}

#[test]
fn context_capacity_matches_python() {
    let vectors = vectors();
    for case in vectors["capacity"].as_array().unwrap() {
        let inputs = &case["inputs"];
        let int = |key: &str| inputs.get(key).and_then(J::as_i64);
        let text = |key: &str| inputs.get(key).and_then(J::as_str);
        let resolved = resolve_context_capacity(&CapacityInputs {
            declared_context_window: int("declared_context_window"),
            declared_context_source: text("declared_context_source"),
            runtime_context_window: int("runtime_context_window"),
            runtime_context_source: text("runtime_context_source"),
            configured_context_window: int("configured_context_window"),
            legacy_context_window: int("legacy_context_window"),
            observed_at: text("observed_at"),
            declared_observed_at: text("declared_observed_at"),
            runtime_observed_at: text("runtime_observed_at"),
            configured_observed_at: text("configured_observed_at"),
        });
        assert!(same(&J::Object(resolved.projection_json()), &case["value"]), "{inputs}");
    }
}

#[test]
fn now_is_an_aware_utc_isoformat() {
    let now = settings::now_isoformat();
    assert!(now.ends_with("+00:00") && now.len() >= 25 && now.as_bytes()[10] == b'T', "{now}");
}
