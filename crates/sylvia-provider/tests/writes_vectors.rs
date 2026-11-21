//! Compare the provider settings writes with vectors written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_writes_vectors.py` (needs the snapshot's Python).
//! Credentials are disposable test values under a deterministic fake seal, and every settings
//! file is written in a temporary folder the test creates and deletes.

use serde_json::{json, Value as J};
use std::path::{Path, PathBuf};
use sylvia_provider::config::env::Environment;
use sylvia_provider::config::protection::{self, Seal};
use sylvia_provider::config::provider_settings::ConfigSources;
use sylvia_provider::config::provider_writes::{
    clear_session_model_once_settings, refresh_subscription_reasoning, remove_provider_settings,
    save_provider_settings, set_chat_defaults_settings, set_default_model_settings,
    set_session_model_once_settings, set_session_speed_settings, SaveProvider,
};
use sylvia_provider::config::{data_from_json, reply_json, ConfigFailure, Data};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/writes_vectors.json")).expect("vector file is valid JSON")
}

fn same(a: &J, b: &J) -> bool {
    serde_json::to_string(a).unwrap() == serde_json::to_string(b).unwrap()
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

fn json_inner(text: &str) -> String {
    let quoted = J::String(text.to_owned()).to_string();
    quoted[1..quoted.len() - 1].to_owned()
}

fn token(value: J) -> J {
    let text = value
        .to_string()
        .replace(&json_inner(protection::UNAVAILABLE_MESSAGE), "<UNAVAILABLE>")
        .replace(&json_inner(protection::PROTECTION), "<PROTECTION>");
    serde_json::from_str(&text).unwrap()
}

fn temporary_folder() -> PathBuf {
    let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let folder = std::env::temp_dir().join(format!("sylvia-writes-test-{}-{nanos}", std::process::id()));
    std::fs::create_dir(&folder).unwrap();
    folder
}

fn data(value: Option<&J>) -> Data {
    value.map_or(Data::None, |value| data_from_json(value).unwrap())
}

fn save_request(params: &J) -> SaveProvider {
    let get = |key: &str| data(params.get(key));
    SaveProvider {
        provider_id: get("provider_id"),
        base_url: get("base_url"),
        credential: get("credential"),
        model_id: get("model_id"),
        model_name: get("model_name"),
        context_window: get("context_window"),
        max_output_tokens: get("max_output_tokens"),
        declared_max_output_tokens: get("declared_max_output_tokens"),
        builder_output_tokens: get("builder_output_tokens"),
        preset_id: get("preset_id"),
        display_name: get("display_name"),
        transport: get("transport"),
        headers: get("headers"),
        supports_streaming: get("supports_streaming"),
        supports_tools: get("supports_tools"),
        supported_reasoning_efforts: get("supported_reasoning_efforts"),
        default_reasoning_effort: get("default_reasoning_effort"),
        service_tiers: get("service_tiers"),
        declared_context_window: get("declared_context_window"),
        declared_context_source: get("declared_context_source"),
        runtime_context_window: get("runtime_context_window"),
        runtime_context_source: get("runtime_context_source"),
        configured_context_window: get("configured_context_window"),
        context_observed_at: get("context_observed_at"),
        declared_context_observed_at: get("declared_context_observed_at"),
        runtime_context_observed_at: get("runtime_context_observed_at"),
        configured_context_observed_at: get("configured_context_observed_at"),
    }
}

fn run(op: &J, sources: ConfigSources<'_>, path: &Path, now: &str) -> Result<Data, ConfigFailure> {
    let params = &op["params"];
    let get = |key: &str| data(params.get(key));
    match op["op"].as_str().unwrap() {
        "save" => save_provider_settings(sources, path, &save_request(params), &|| now.to_owned()),
        "remove" => remove_provider_settings(sources, path, &get("provider_id")).map(|()| Data::None),
        "default" => set_default_model_settings(sources, path, &get("provider_id"), &get("model_id")),
        "chat_defaults" => set_chat_defaults_settings(
            sources,
            path,
            &get("provider_id"),
            &get("model_id"),
            &get("reasoning_effort"),
            &get("speed"),
        ),
        "speed" => set_session_speed_settings(sources, path, &get("session_id"), &get("speed")),
        "once" => set_session_model_once_settings(
            sources,
            path,
            &get("session_id"),
            &get("provider_id"),
            &get("model_id"),
        ),
        "clear_once" => clear_session_model_once_settings(
            sources,
            path,
            &get("session_id"),
            &get("provider_id"),
            &get("model_id"),
        )
        .map(Data::Bool),
        "refresh" => {
            let models: Vec<Data> = params["models"]
                .as_array()
                .unwrap()
                .iter()
                .map(|model| data_from_json(model).unwrap())
                .collect();
            refresh_subscription_reasoning(sources, path, &models).map(Data::Bool)
        }
        other => panic!("unknown operation {other}"),
    }
}

#[test]
fn settings_writes_match_python() {
    let vectors = vectors();
    let now = vectors["now"].as_str().unwrap();
    let folder = temporary_folder();
    let path = folder.join("config.yaml");
    let env = Environment::default();
    let default_config = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../config/config.yaml");
    let sources = ConfigSources { default_config: &default_config, seal: &FakeSeal, env: &env };
    let mut failures = Vec::new();
    let mut steps = 0;
    for case in vectors["cases"].as_array().unwrap() {
        let _ = std::fs::remove_file(&path);
        if let Some(text) = case["user"].as_str() {
            std::fs::write(&path, text.replace("<PROTECTION>", protection::PROTECTION)).unwrap();
        }
        for step in case["steps"].as_array().unwrap() {
            steps += 1;
            let mut actual = match run(&step["op"], sources, &path, now).and_then(|value| reply_json(&value))
            {
                Ok(value) => json!({"op": step["op"], "value": value}),
                Err(error) => {
                    let class = match &error {
                        ConfigFailure::Config(_) => "ConfigError",
                        ConfigFailure::Python { class, .. } => class,
                    };
                    json!({"op": step["op"], "error": {"class": class, "message": error.to_string()}})
                }
            };
            actual["file"] = std::fs::read_to_string(&path).map_or(J::Null, J::String);
            let actual = token(actual);
            if !same(&actual, step) {
                failures
                    .push(format!("{} {}\n  expected {step}\n  got      {actual}", case["name"], step["op"]));
            }
        }
    }
    let _ = std::fs::remove_dir_all(&folder);
    assert!(
        failures.is_empty(),
        "{} of {steps} differ:\n{}",
        failures.len(),
        failures[..failures.len().min(5)].join("\n")
    );
}
