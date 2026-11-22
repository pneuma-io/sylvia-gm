//! Compare `DefaultProviderFactory` with vectors written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_factory_vectors.py` (needs the snapshot's Python).
//! The HTTP client is the scripted transport: nothing here opens a connection.

use serde_json::{json, Value as J};
use std::sync::Arc;
use sylvia_provider::factory::{ClientBuilder, DefaultProviderFactory, PORT_PENDING};
use sylvia_provider::http::{HttpTransport, ScriptedTransport};
use sylvia_provider::openai_compat::SdkEnvironment;
use sylvia_provider::types::{ModelInfo, ProviderConfig, RetryConfig, TimeoutConfig};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/factory_vectors.json")).expect("vector file is valid JSON")
}

fn same(a: &J, b: &J) -> bool {
    serde_json::to_string(a).unwrap() == serde_json::to_string(b).unwrap()
}

fn config(case: &J) -> ProviderConfig {
    let text = |key: &str| case.get(key).and_then(J::as_str).unwrap_or("").to_owned();
    let mut config = ProviderConfig::new(text("provider_key"), text("transport"), text("base_url"));
    config.runtime_home = text("runtime_home");
    config.api_key = text("api_key");
    config.default_model = text("default_model");
    if let Some(headers) = case.get("default_headers").and_then(J::as_array) {
        config.default_headers = headers
            .iter()
            .map(|pair| (pair[0].as_str().unwrap().into(), pair[1].as_str().unwrap().into()))
            .collect();
    }
    config.models = case["models"]
        .as_array()
        .unwrap()
        .iter()
        .map(|pair| {
            let mut model = ModelInfo::new(pair[0].as_str().unwrap(), "M", "P", "p", "byok");
            model.transport = pair[1].as_str().unwrap().into();
            model.post_init()
        })
        .collect();
    if let Some(retry) = case.get("retry") {
        config.retry_config = Some(RetryConfig {
            max_retries: retry["max_retries"].as_u64().unwrap() as u32,
            base_delay: retry["base_delay"].as_f64().unwrap(),
            max_delay: retry["max_delay"].as_f64().unwrap(),
            backoff_multiplier: retry["backoff_multiplier"].as_f64().unwrap(),
            retryable_statuses: retry["retryable_statuses"]
                .as_array()
                .unwrap()
                .iter()
                .map(|s| s.as_u64().unwrap() as u16)
                .collect(),
        });
    }
    config
}

fn retry(policy: &RetryConfig) -> J {
    let mut statuses: Vec<u16> = policy.retryable_statuses.iter().copied().collect();
    statuses.sort_unstable();
    json!({
        "max_retries": policy.max_retries,
        "base_delay": policy.base_delay,
        "max_delay": policy.max_delay,
        "backoff_multiplier": policy.backoff_multiplier,
        "retryable_statuses": statuses,
    })
}

fn timeouts(timeouts: &TimeoutConfig, expected: &J) -> J {
    let all = json!({
        "connect": timeouts.connect,
        "read": timeouts.read,
        "write": timeouts.write,
        "pool": timeouts.pool,
        "stream_health_interval": timeouts.stream_health_interval,
    });
    J::Object(expected.as_object().unwrap().keys().map(|key| (key.clone(), all[key].clone())).collect())
}

#[test]
fn the_factory_builds_what_python_builds() {
    let client: Arc<ClientBuilder> = Arc::new(|_: &TimeoutConfig| {
        Ok(Arc::new(ScriptedTransport::new(Vec::new())) as Arc<dyn HttpTransport>)
    });
    let factory = DefaultProviderFactory::new(client, SdkEnvironment::default());
    let mut failures = Vec::new();
    for case in vectors()["cases"].as_array().unwrap() {
        let built = factory.build(config(&case["config"]));
        let actual = match (&built, case.get("value")) {
            (Ok(provider), Some(expected)) => {
                let model = provider.model();
                json!({"value": {
                    "kind": "OpenAICompatProvider",
                    "base_url": provider.base_url(),
                    "model_id": model.id,
                    "model_name": model.name,
                    "model_transport": model.transport,
                    "retry": retry(provider.retry_config()),
                    "timeouts": timeouts(provider.timeout_config(), &expected["timeouts"]),
                    "default_headers": provider.default_headers().iter().map(|(n, v)| json!([n, v])).collect::<Vec<_>>(),
                }})
            }
            // A transport whose adapter is not ported answers `rust_port_pending` where Python
            // built its provider: one of the native providers, or Responses.
            (Err(error), Some(expected)) if error.code == PORT_PENDING => {
                let native = ["ClaudeProvider", "ChatGPTProvider", "GeminiProvider", "AnthropicProvider"];
                let responses =
                    expected["kind"] == "OpenAICompatProvider" && expected["model_transport"] == "responses";
                if native.contains(&expected["kind"].as_str().unwrap_or("")) || responses {
                    json!({"value": expected})
                } else {
                    json!({"pending": error.message})
                }
            }
            // Differences row 12: a non-ASCII host is refused when the provider is built, where
            // httpx would have IDNA-encoded it on the first request.
            (Err(error), Some(expected))
                if !case["config"]["base_url"].as_str().unwrap().is_ascii()
                    && error.message.starts_with("Invalid IDNA hostname: ") =>
            {
                json!({"value": expected})
            }
            (Err(error), _) => {
                json!({"error": {"class": error.error_type.unwrap_or("?"), "message": error.message}})
            }
            (Ok(_), None) => json!("built where Python failed"),
        };
        let mut expected = case.clone();
        expected.as_object_mut().unwrap().remove("config");
        if !same(&actual, &expected) {
            failures.push(format!("{}\n  expected {expected}\n  got      {actual}", case["config"]));
        }
    }
    assert!(failures.is_empty(), "{} differ:\n{}", failures.len(), failures.join("\n"));
}
