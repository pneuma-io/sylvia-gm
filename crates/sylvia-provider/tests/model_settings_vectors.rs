//! Compare the model catalogue and settings with vectors written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_model_settings_vectors.py` (needs the snapshot's Python).

use serde_json::{json, Map, Value as J};
use std::sync::{Arc, Mutex};
use sylvia_base::CodedError;
use sylvia_provider::catalog::{build_catalog, output_allowance};
use sylvia_provider::provider::{FakeProvider, Provider};
use sylvia_provider::reasoning::{
    configure_reasoning, mode_reasoning_default, reasoning_values, select_reasoning, Advertised,
};
use sylvia_provider::registry::{ProviderFactory, ProviderRegistry, RegistryError};
use sylvia_provider::request_metadata::{
    opencode_request_headers, with_protocol_headers, without_opencode_protocol_headers,
};
use sylvia_provider::speed::{
    configure_speed, select_speed, speed_options, AdvertisedTier, AdvertisedTiers, SpeedSelection,
};
use sylvia_provider::types::{ModelInfo, ProviderConfig, SpeedOption};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/model_settings_vectors.json"))
        .expect("vector file is valid JSON")
}

/// Equal as JSON text: `Value`'s own equality ignores object key order.
fn same(a: &J, b: &J) -> bool {
    serde_json::to_string(a).unwrap() == serde_json::to_string(b).unwrap()
}

fn text(value: &J) -> Option<String> {
    value.as_str().map(str::to_owned)
}

fn strings(value: &J) -> Option<Vec<String>> {
    value.as_array().map(|items| items.iter().map(|item| item.as_str().unwrap().to_owned()).collect())
}

fn speeds(value: &J) -> Option<Vec<SpeedOption>> {
    value.as_array().map(|items| {
        items
            .iter()
            .map(|item| SpeedOption {
                id: item["id"].as_str().unwrap().into(),
                name: item["name"].as_str().unwrap().into(),
                description: item["description"].as_str().unwrap().into(),
            })
            .collect()
    })
}

/// `model_from(spec)` in the generator.
fn model_from(spec: &J) -> ModelInfo {
    let mut model = ModelInfo::new(spec["id"].as_str().unwrap(), "N", "P", "p", "byok");
    let get = |key: &str| spec.get(key).cloned();
    if let Some(value) = get("transport") {
        model.transport = value.as_str().unwrap().into();
    }
    if let Some(value) = get("provider_model_id") {
        model.provider_model_id = value.as_str().unwrap().into();
    }
    model.supported_reasoning_efforts = get("supported_reasoning_efforts").and_then(|value| strings(&value));
    model.default_reasoning_effort = get("default_reasoning_effort").and_then(|value| text(&value));
    model.reasoning_control = get("reasoning_control").and_then(|value| text(&value));
    if let Some(value) = get("reasoning_source") {
        model.reasoning_source = value.as_str().unwrap().into();
    }
    model.speed_options = get("speed_options").and_then(|value| speeds(&value));
    if let Some(value) = get("max_output_tokens") {
        model.max_output_tokens = value.as_i64();
    }
    model.context_window = get("context_window").and_then(|value| value.as_i64());
    if let Some(value) = get("builder_output_tokens") {
        model.builder_output_tokens = value.as_i64().unwrap();
    }
    model.post_init()
}

fn opt_text(value: &Option<String>) -> J {
    value.clone().map_or(J::Null, J::String)
}

fn speed_list(options: &Option<Vec<SpeedOption>>) -> J {
    options
        .as_ref()
        .map_or(J::Null, |options| J::Array(options.iter().map(SpeedOption::projection).collect()))
}

/// One `ModelInfo` field as Python's `getattr` shows it.
fn model_field(model: &ModelInfo, name: &str) -> J {
    let int = |value: Option<i64>| value.map_or(J::Null, J::from);
    let flag = |value: Option<bool>| value.map_or(J::Null, J::Bool);
    match name {
        "id" => json!(model.id),
        "name" => json!(model.name),
        "provider_name" => json!(model.provider_name),
        "provider_key" => json!(model.provider_key),
        "provider_mode" => json!(model.provider_mode),
        "recommended" => json!(model.recommended),
        "premium" => json!(model.premium),
        "context_window" => int(model.context_window),
        "declared_context_window" => int(model.declared_context_window),
        "declared_context_source" => json!(model.declared_context_source),
        "runtime_context_window" => int(model.runtime_context_window),
        "runtime_context_source" => json!(model.runtime_context_source),
        "configured_context_window" => int(model.configured_context_window),
        "context_window_source" => json!(model.context_window_source),
        "declared_context_observed_at" => opt_text(&model.declared_context_observed_at),
        "runtime_context_observed_at" => opt_text(&model.runtime_context_observed_at),
        "configured_context_observed_at" => opt_text(&model.configured_context_observed_at),
        "context_observed_at" => opt_text(&model.context_observed_at),
        "configured_runtime_mismatch" => json!(model.configured_runtime_mismatch),
        "configured_declared_mismatch" => json!(model.configured_declared_mismatch),
        "max_output_tokens" => int(model.max_output_tokens),
        "builder_output_tokens" => json!(model.builder_output_tokens),
        "cost_per_1k_tokens" => json!(model.cost_per_1k_tokens),
        "transport" => json!(model.transport),
        "provider_model_id" => json!(model.provider_model_id),
        "supports_streaming" => flag(model.supports_streaming),
        "supports_tools" => flag(model.supports_tools),
        "supported_reasoning_efforts" => {
            model.supported_reasoning_efforts.as_ref().map_or(J::Null, |values| json!(values))
        }
        "default_reasoning_effort" => opt_text(&model.default_reasoning_effort),
        "reasoning_control" => opt_text(&model.reasoning_control),
        "reasoning_source" => json!(model.reasoning_source),
        "speed_options" => speed_list(&model.speed_options),
        "speed_control" => opt_text(&model.speed_control),
        "speed_source" => json!(model.speed_source),
        other => panic!("ModelInfo has no field {other}"),
    }
}

/// The fields the expected record names, in its order.
fn record_like(model: &ModelInfo, expected: &J) -> J {
    let fields = expected.as_object().unwrap();
    J::Object(fields.keys().map(|key| (key.clone(), model_field(model, key))).collect())
}

#[test]
fn the_catalogue_matches_python() {
    let vectors = vectors();
    let expected = vectors["catalog"].as_array().unwrap();
    let actual = build_catalog();
    assert_eq!(actual.len(), expected.len());
    for (model, record) in actual.iter().zip(expected) {
        // Every Python field has a Rust counterpart.
        assert_eq!(record.as_object().unwrap().len(), 34, "{record}");
        let got = record_like(model, record);
        assert!(same(&got, record), "{}\n  expected {record}\n  got      {got}", model.id);
    }
}

#[test]
fn output_allowances_match_python() {
    for case in vectors()["allowance"].as_array().unwrap() {
        let model = model_from(&case["model"]);
        assert_eq!(json!(output_allowance(&model, false)), case["dm"], "{case}");
        assert_eq!(json!(output_allowance(&model, true)), case["builder"], "{case}");
    }
}

fn advertised(value: &J) -> Advertised<'_> {
    match value {
        J::Null => Advertised::Missing,
        J::Array(items) => Advertised::List(items.iter().map(J::as_str).collect()),
        _ => Advertised::Other,
    }
}

fn optional_text(value: &J) -> Option<Option<&str>> {
    match value {
        J::Null => None,
        other => Some(other.as_str()),
    }
}

fn error_of(case: &J) -> &str {
    case["error"]["message"].as_str().unwrap()
}

#[test]
fn reasoning_values_match_python() {
    for case in vectors()["reasoning_values"].as_array().unwrap() {
        match reasoning_values(&advertised(&case["values"]), optional_text(&case["suggested"])) {
            Ok((supported, suggested)) => {
                assert_eq!(json!([supported, suggested]), case["value"], "{case}");
            }
            Err(message) => {
                assert_eq!(case["error"]["class"], "ValueError", "{case}");
                assert_eq!(message, error_of(case), "{case}");
            }
        }
    }
}

fn tiers(value: &J) -> AdvertisedTiers<'_> {
    match value {
        J::Null => AdvertisedTiers::Missing,
        J::Array(items) => AdvertisedTiers::List(
            items
                .iter()
                .map(|item| {
                    item.as_object().map(|map| AdvertisedTier {
                        id: map.get("id").and_then(J::as_str),
                        name: map.get("name").and_then(J::as_str),
                        description: map.get("description").map_or(Some(""), J::as_str),
                    })
                })
                .collect(),
        ),
        _ => AdvertisedTiers::Other,
    }
}

#[test]
fn speed_options_match_python() {
    for case in vectors()["speed_options"].as_array().unwrap() {
        match speed_options(&tiers(&case["values"])) {
            Ok(options) => assert!(same(&speed_list(&options), &case["value"]), "{case}"),
            Err(message) => assert_eq!(message, error_of(case), "{case}"),
        }
    }
}

fn settings(model: &ModelInfo) -> J {
    let mut map = Map::new();
    for name in [
        "supported_reasoning_efforts",
        "default_reasoning_effort",
        "reasoning_control",
        "reasoning_source",
        "speed_options",
        "speed_control",
        "speed_source",
    ] {
        map.insert(name.into(), model_field(model, name));
    }
    J::Object(map)
}

#[test]
fn configured_reasoning_and_speed_match_python() {
    let vectors = vectors();
    let cases = vectors["configure"].as_array().unwrap();
    let mut failures = Vec::new();
    for case in cases {
        let url = case["base_url"].as_str().unwrap();
        let mut actual = Map::new();
        actual.insert("model".into(), case["model"].clone());
        actual.insert("base_url".into(), case["base_url"].clone());
        let reasoned = match configure_reasoning(model_from(&case["model"]), url) {
            Ok(model) => {
                actual.insert("reasoning".into(), settings(&model));
                model
            }
            Err(error) => {
                actual.insert("reasoning_error".into(), json!(error.0));
                model_from(&case["model"])
            }
        };
        match configure_speed(reasoned, url) {
            Ok(model) => actual.insert("speed".into(), settings(&model)),
            Err(error) => actual.insert("speed_error".into(), json!(error.0)),
        };
        let actual = J::Object(actual);
        if !same(&actual, case) {
            failures.push(format!("expected {case}\n  got      {actual}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ:\n{}",
        failures.len(),
        cases.len(),
        failures[..failures.len().min(10)].join("\n")
    );
}

#[test]
fn mode_defaults_match_python() {
    for case in vectors()["mode_defaults"].as_array().unwrap() {
        let model = (!case["model"].is_null()).then(|| model_from(&case["model"]));
        assert_eq!(json!(mode_reasoning_default(model.as_ref(), "dm")), case["dm"], "{case}");
        if model.is_some() {
            assert_eq!(json!(mode_reasoning_default(model.as_ref(), "builder")), case["builder"], "{case}");
            assert_eq!(json!(mode_reasoning_default(model.as_ref(), "planner")), case["other"], "{case}");
        }
    }
}

fn requested(value: &J) -> Option<Option<&str>> {
    optional_text(value)
}

fn coded_failure(error: CodedError) -> J {
    json!({"error": {"class": "CommandRejected", "code": error.code, "message": error.message}})
}

#[test]
fn reasoning_selections_match_python() {
    for case in vectors()["select_reasoning"].as_array().unwrap() {
        let model = (!case["model"].is_null()).then(|| model_from(&case["model"]));
        let role = case["role"].as_str().unwrap();
        let actual = match select_reasoning(model.as_ref(), role, requested(&case["requested"]), "session") {
            Ok(selection) => {
                let mut projection = selection.projection();
                projection["control"] = opt_text(&selection.control);
                json!({"value": projection})
            }
            Err(error) => coded_failure(error),
        };
        let mut expected = case.clone();
        for key in ["model", "role", "requested"] {
            expected.as_object_mut().unwrap().remove(key);
        }
        assert!(same(&actual, &expected), "{case}\n  got {actual}");
    }
}

#[test]
fn speed_selections_match_python() {
    for case in vectors()["select_speed"].as_array().unwrap() {
        let model = (!case["model"].is_null()).then(|| model_from(&case["model"]));
        let strict = case["strict"].as_bool().unwrap();
        let actual = match select_speed(model.as_ref(), requested(&case["requested"]), strict, "once") {
            Ok(selection) => json!({"value": selection.projection()}),
            Err(error) => coded_failure(error),
        };
        let mut expected = case.clone();
        for key in ["model", "requested", "strict"] {
            expected.as_object_mut().unwrap().remove(key);
        }
        assert!(same(&actual, &expected), "{case}\n  got {actual}");
    }
    for case in vectors()["speed_controls"].as_array().unwrap() {
        let effective = text(&case["effective"]);
        let selection = SpeedSelection {
            requested: effective.clone(),
            effective,
            control: text(&case["control"]),
            scope: "s".into(),
        };
        assert!(same(&J::Object(selection.options()), &case["options"]), "{case}");
    }
}

fn pairs(value: &J) -> Vec<(String, String)> {
    value
        .as_array()
        .unwrap()
        .iter()
        .map(|pair| (pair[0].as_str().unwrap().to_owned(), pair[1].as_str().unwrap().to_owned()))
        .collect()
}

#[test]
fn request_metadata_matches_python() {
    let vectors = vectors();
    for case in vectors["opencode_headers"].as_array().unwrap() {
        assert_eq!(
            opencode_request_headers(case["session_id"].as_str().unwrap()),
            pairs(&case["headers"]),
            "{case}"
        );
    }
    for case in vectors["without_protocol_headers"].as_array().unwrap() {
        assert_eq!(
            without_opencode_protocol_headers(&pairs(&case["headers"])),
            pairs(&case["result"]),
            "{case}"
        );
    }
    let protocol =
        vec![("x-opencode-session".to_owned(), "S".to_owned()), ("User-Agent".to_owned(), "U".to_owned())];
    for case in vectors["with_protocol_headers"].as_array().unwrap() {
        let result = with_protocol_headers(case["options"].as_object().unwrap(), &protocol);
        assert!(same(&J::Object(result), &case["result"]), "{case}");
    }
}

/// The generator's recording factory: each build is numbered.
#[derive(Default)]
struct Recording {
    built: Mutex<Vec<(Arc<dyn Provider>, ProviderConfig)>>,
}

struct Numbered {
    inner: FakeProvider,
}

impl Provider for Numbered {
    fn model_info(&self) -> Option<&ModelInfo> {
        self.inner.model_info()
    }
    fn generate(
        &self,
        request: sylvia_provider::ProviderRequest,
    ) -> sylvia_provider::BoxFuture<'_, sylvia_provider::ProviderResult<sylvia_provider::ProviderResponse>>
    {
        self.inner.generate(request)
    }
    fn generate_stream(&self, request: sylvia_provider::ProviderRequest) -> sylvia_provider::ChunkStream<'_> {
        self.inner.generate_stream(request)
    }
    fn close(&self) -> sylvia_provider::BoxFuture<'_, ()> {
        self.inner.close()
    }
}

impl ProviderFactory for Recording {
    fn create(&self, config: ProviderConfig) -> Result<Arc<dyn Provider>, CodedError> {
        let provider: Arc<dyn Provider> =
            Arc::new(Numbered { inner: FakeProvider::new(Vec::new(), Vec::new()) });
        self.built.lock().unwrap().push((provider.clone(), config));
        Ok(provider)
    }
}

fn catalog_model(record: &J) -> ModelInfo {
    let mut model = ModelInfo::new(
        record["id"].as_str().unwrap(),
        record["name"].as_str().unwrap(),
        record["provider_name"].as_str().unwrap(),
        record["provider_key"].as_str().unwrap(),
        record["provider_mode"].as_str().unwrap(),
    );
    model.transport = record["transport"].as_str().unwrap().into();
    model.provider_model_id = record["provider_model_id"].as_str().unwrap().into();
    model
}

#[test]
fn registry_resolution_matches_python() {
    let vectors = vectors();
    let case = &vectors["registry"][0];
    let factory = Arc::new(Recording::default());
    let catalog: Vec<ModelInfo> = case["catalog"].as_array().unwrap().iter().map(catalog_model).collect();
    let mut registry = ProviderRegistry::new(catalog, factory.clone());
    for (operation, expected) in
        case["operations"].as_array().unwrap().iter().zip(case["results"].as_array().unwrap())
    {
        let actual = match operation["op"].as_str().unwrap() {
            "configure" => {
                let headers: Vec<(String, String)> = operation["headers"]
                    .as_object()
                    .unwrap()
                    .iter()
                    .map(|(name, value)| (name.clone(), value.as_str().unwrap().to_owned()))
                    .collect();
                registry.configure_provider(
                    operation["key"].as_str().unwrap(),
                    operation["base_url"].as_str().unwrap(),
                    operation["api_key"].as_str().unwrap(),
                    headers,
                    operation.get("runtime_home").and_then(J::as_str).unwrap_or(""),
                );
                J::Null
            }
            "list" => J::Array(
                registry
                    .list_models(operation["key"].as_str())
                    .iter()
                    .map(|model| json!([model.id, model.name]))
                    .collect(),
            ),
            _ => match registry.resolve(operation["model_id"].as_str().unwrap()) {
                Err(RegistryError::NotFound(error)) => json!({"error": error.message}),
                Err(RegistryError::Build(error)) => panic!("{error:?}"),
                Ok((provider, info)) => {
                    let built = factory.built.lock().unwrap();
                    // The provider is the factory's nth build (a cached one keeps its number).
                    let index = built.iter().position(|(made, _)| Arc::ptr_eq(made, &provider)).unwrap();
                    let number = index + 1;
                    let (_, config) = &built[index];
                    json!({
                        "provider": number,
                        "model": [info.id, info.name],
                        "config": {
                            "provider_key": config.provider_key,
                            "transport": config.transport,
                            "base_url": config.base_url,
                            "runtime_home": config.runtime_home,
                            "default_headers": J::Object(config.default_headers.iter().map(|(n, v)| (n.clone(), json!(v))).collect()),
                            "api_key": config.api_key,
                            "models": config.models.iter().map(|model| model.id.clone()).collect::<Vec<_>>(),
                            "default_model": config.default_model,
                        },
                    })
                }
            },
        };
        assert!(same(&actual, expected), "{operation}\n  expected {expected}\n  got      {actual}");
    }
    let empty: Vec<String> = ProviderRegistry::new(Vec::new(), factory)
        .list_models(None)
        .iter()
        .map(|model| model.id.clone())
        .collect();
    assert_eq!(json!(empty), vectors["registry"][1]["empty_catalog"]);
}
