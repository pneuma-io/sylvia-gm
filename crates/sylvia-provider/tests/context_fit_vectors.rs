//! Compare context fitting, the token estimator and turn reasoning with vectors written by the
//! frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_context_fit_vectors.py` (needs the snapshot's Python).
//! The fake provider stands for a transport that may have `provider_request_representation`,
//! `record_context_fit` and `set_audit_context`; the cases wrap it in Sylvia's own wrappers and
//! compare the measurement or refusal, every call the fake received and every capability call.

use futures_util::StreamExt;
use serde_json::{json, Value as J};
use std::sync::{Arc, Mutex};
use sylvia_base::JsonMap;
use sylvia_provider::context_fit::{context_fitted_provider, ContextFittingProvider};
use sylvia_provider::hooks::{HookContext, HookManager, ProviderHook};
use sylvia_provider::reasoning::{bind_session_reasoning, ReasoningSelection, TurnReasoningProvider};
use sylvia_provider::request_metadata::SessionBoundRequestMetadataProvider;
use sylvia_provider::speed::SpeedSelection;
use sylvia_provider::token_estimate::{token_estimator, TokenEstimateHook};
use sylvia_provider::types::{ModelInfo, PyValue, SpeedOption, TokenUsage};
use sylvia_provider::{
    BoxFuture, ChunkStream, ErrorKind, Provider, ProviderError, ProviderRequest, ProviderResponse,
    ProviderResult,
};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/context_fit_vectors.json")).expect("vector file is valid JSON")
}

fn text(value: &J) -> String {
    serde_json::to_string(value).unwrap()
}

fn opt_text(value: &J) -> Option<String> {
    value.as_str().map(str::to_owned)
}

fn model_from(spec: &J) -> ModelInfo {
    let get = |key: &str| spec[key].as_str().unwrap();
    let mut model = ModelInfo::new(
        get("id"),
        get("name"),
        get("provider_name"),
        get("provider_key"),
        get("provider_mode"),
    );
    if let Some(value) = spec.get("context_window") {
        model.context_window = value.as_i64();
    }
    if let Some(value) = spec.get("max_output_tokens") {
        model.max_output_tokens = value.as_i64();
    }
    if let Some(value) = spec.get("transport") {
        model.transport = value.as_str().unwrap().into();
    }
    if let Some(value) = spec.get("runtime_context_window") {
        model.runtime_context_window = value.as_i64();
    }
    if let Some(value) = spec.get("runtime_context_source") {
        model.runtime_context_source = value.as_str().unwrap().into();
    }
    if let Some(value) = spec.get("supported_reasoning_efforts") {
        model.supported_reasoning_efforts = value
            .as_array()
            .map(|items| items.iter().map(|item| item.as_str().unwrap().to_owned()).collect());
    }
    if let Some(value) = spec.get("default_reasoning_effort") {
        model.default_reasoning_effort = opt_text(value);
    }
    if let Some(value) = spec.get("reasoning_control") {
        model.reasoning_control = opt_text(value);
    }
    if let Some(value) = spec.get("speed_options") {
        model.speed_options = value.as_array().map(|items| {
            items
                .iter()
                .map(|item| SpeedOption {
                    id: item["id"].as_str().unwrap().into(),
                    name: item["name"].as_str().unwrap().into(),
                    description: String::new(),
                })
                .collect()
        });
    }
    if let Some(value) = spec.get("speed_control") {
        model.speed_control = opt_text(value);
    }
    model.post_init()
}

type Log = Arc<Mutex<Vec<J>>>;

/// The generator's fake transport.
struct Fake {
    model: Option<ModelInfo>,
    flags: Vec<String>,
    calls: Log,
    events: Log,
    hooks: HookManager,
}

impl Fake {
    fn has(&self, flag: &str) -> bool {
        self.flags.iter().any(|item| item == flag)
    }

    fn record(&self, method: &str, request: &ProviderRequest, stream: bool) {
        self.calls.lock().unwrap().push(json!({
            "method": method,
            "stream": stream,
            "timeout": request.timeout,
            "tools": request.tools,
            "options": request.options,
        }));
    }
}

impl Provider for Fake {
    fn model_info(&self) -> Option<&ModelInfo> {
        self.model.as_ref()
    }

    fn generate(&self, request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        self.record("generate", &request, request.stream);
        Box::pin(async { Ok(ProviderResponse { content: Some("ok".into()), ..ProviderResponse::default() }) })
    }

    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        Box::pin(futures_util::stream::once(async move {
            self.record("generate_stream", &request, true);
            Ok(sylvia_provider::types::ProviderStreamChunk { content: "ok".into(), ..Default::default() })
        }))
    }

    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }

    fn hooks(&self) -> Option<&HookManager> {
        Some(&self.hooks)
    }

    fn request_representation(
        &self,
        messages: &[J],
        tools: Option<&[J]>,
        options: &JsonMap,
    ) -> Option<ProviderResult<JsonMap>> {
        if self.has("representation_error") {
            return Some(Err(ProviderError::new(ErrorKind::InvalidRequest, "Fake representation refused.")));
        }
        if !self.has("representation") {
            return None;
        }
        let value = json!({"kind": "fake", "messages": messages, "tools": tools, "options": options});
        Some(Ok(value.as_object().unwrap().clone()))
    }

    fn record_context_fit(&self, context: &JsonMap) -> bool {
        if !self.has("recorder") {
            return false;
        }
        self.events.lock().unwrap().push(json!(["record", context]));
        true
    }

    fn set_audit_context(&self, context: &JsonMap) -> bool {
        if !self.has("setter") {
            return false;
        }
        self.events.lock().unwrap().push(json!(["set", context]));
        true
    }
}

fn reasoning_selection(value: &J) -> ReasoningSelection {
    ReasoningSelection {
        requested: opt_text(&value["requested"]),
        effective: opt_text(&value["effective"]),
        control: opt_text(&value["control"]),
        transport: opt_text(&value["transport"]),
        scope: value["scope"].as_str().unwrap().into(),
    }
}

fn speed_selection(value: &J) -> Option<SpeedSelection> {
    value.as_object().map(|_| SpeedSelection {
        requested: opt_text(&value["requested"]),
        effective: opt_text(&value["effective"]),
        control: opt_text(&value["control"]),
        scope: value["scope"].as_str().unwrap().into(),
    })
}

/// The chain, outermost first, over the fake.
fn build(chain: &[J], fake: Arc<dyn Provider>) -> (Arc<dyn Provider>, Option<Arc<ContextFittingProvider>>) {
    let mut provider = fake;
    let mut fitting = None;
    for layer in chain.iter().rev() {
        match layer["kind"].as_str().unwrap() {
            "session_bound" => {
                let headers = layer["headers"]
                    .as_object()
                    .unwrap()
                    .iter()
                    .map(|(name, value)| (name.clone(), value.as_str().unwrap().to_owned()))
                    .collect();
                provider = Arc::new(SessionBoundRequestMetadataProvider::new(provider, headers));
            }
            "reasoning" => {
                provider = Arc::new(TurnReasoningProvider::new(
                    provider,
                    reasoning_selection(&layer["selection"]),
                    speed_selection(&layer["speed"]),
                ));
            }
            "fit" => {
                let wrapper =
                    Arc::new(context_fitted_provider(provider, layer["gm_mode"].as_bool().unwrap()));
                if let Some(audit) = layer.get("audit").and_then(J::as_object) {
                    wrapper.set_audit_context(audit);
                }
                fitting = Some(Arc::clone(&wrapper));
                provider = wrapper;
            }
            other => panic!("unknown layer {other}"),
        }
    }
    (provider, fitting)
}

fn failure(error: &ProviderError) -> J {
    json!({"error": {"class": error.kind.class_name(), "message": error.message}})
}

#[tokio::test]
async fn context_fitting_matches_python() {
    let vectors = vectors();
    let mut failures = Vec::new();
    for case in vectors["fit"].as_array().unwrap() {
        let calls: Log = Arc::default();
        let events: Log = Arc::default();
        let fake = Arc::new(Fake {
            model: case.get("model").filter(|value| !value.is_null()).map(model_from),
            flags: case
                .get("flags")
                .and_then(J::as_array)
                .map(|items| items.iter().map(|item| item.as_str().unwrap().to_owned()).collect())
                .unwrap_or_default(),
            calls: Arc::clone(&calls),
            events: Arc::clone(&events),
            hooks: HookManager::new(),
        });
        let (provider, fitting) = build(case["chain"].as_array().unwrap(), fake);
        let construction = std::mem::take(&mut *events.lock().unwrap());
        let request = &case["request"];
        let messages = request["messages"].as_array().unwrap().clone();
        let tools = request.get("tools").and_then(J::as_array).cloned();
        let options = request.get("options").and_then(J::as_object).cloned().unwrap_or_default();
        let outcome = match case["operation"].as_str().unwrap() {
            "admit" => match fitting.unwrap().admit_request(&messages, tools.as_deref(), &options) {
                Ok(measured) => json!({"measurement": measured.projection()}),
                Err(error) => failure(&error),
            },
            "generate" => {
                let call = ProviderRequest {
                    messages,
                    tools,
                    stream: request.get("stream").and_then(J::as_bool).unwrap_or(false),
                    timeout: request.get("timeout").and_then(J::as_f64).unwrap_or(60.0),
                    options,
                };
                match provider.generate(call).await {
                    Ok(_) => json!({"ok": true}),
                    Err(error) => failure(&error),
                }
            }
            _ => {
                let call = ProviderRequest {
                    messages,
                    tools,
                    stream: false,
                    timeout: request.get("timeout").and_then(J::as_f64).unwrap_or(120.0),
                    options,
                };
                let items: Vec<_> = provider.generate_stream(call).collect().await;
                match items.into_iter().collect::<Result<Vec<_>, _>>() {
                    Ok(chunks) => {
                        json!({"chunks": chunks.iter().map(|chunk| chunk.content.clone()).collect::<Vec<_>>()})
                    }
                    Err(error) => failure(&error),
                }
            }
        };
        let mut actual = case.clone();
        actual["construction_events"] = J::Array(construction);
        actual["outcome"] = outcome;
        actual["calls"] = J::Array(calls.lock().unwrap().clone());
        actual["events"] = J::Array(events.lock().unwrap().clone());
        if text(&actual) != text(case) {
            failures.push(format!(
                "{}\n  expected {}\n  got      {}",
                case["name"],
                text(case),
                text(&actual)
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} differ:\n{}",
        failures.len(),
        failures[..failures.len().min(3)].join("\n")
    );
}

#[test]
fn turn_options_match_python() {
    let vectors = vectors();
    for case in vectors["options"].as_array().unwrap() {
        let fake: Arc<dyn Provider> = Arc::new(Fake {
            model: None,
            flags: Vec::new(),
            calls: Arc::default(),
            events: Arc::default(),
            hooks: HookManager::new(),
        });
        let wrapper = TurnReasoningProvider::new(
            fake,
            reasoning_selection(&case["selection"]),
            speed_selection(&case["speed"]),
        );
        let result = wrapper.options(case["options"].as_object().unwrap());
        assert_eq!(text(&J::Object(result)), text(&case["result"]), "{case}");
    }
}

#[test]
fn session_binding_matches_python() {
    let vectors = vectors();
    for case in vectors["bind"].as_array().unwrap() {
        let fake: Arc<dyn Provider> = Arc::new(Fake {
            model: Some(model_from(&case["model"])),
            flags: Vec::new(),
            calls: Arc::default(),
            events: Arc::default(),
            hooks: HookManager::new(),
        });
        let actual = match bind_session_reasoning(
            fake,
            case["role"].as_str().unwrap(),
            case["reasoning_effort"].as_str(),
            case["reasoning_effort_once"].as_str(),
            case["speed"].as_str(),
        ) {
            Ok(wrapper) => {
                let selection = &wrapper.selection;
                let speed = &wrapper.speed;
                json!({
                    "selection": {"requested": selection.requested, "effective": selection.effective,
                                  "control": selection.control, "transport": selection.transport,
                                  "scope": selection.scope},
                    "speed_selection": {"requested": speed.requested, "effective": speed.effective,
                                        "control": speed.control, "scope": speed.scope},
                })
            }
            Err(error) => {
                json!({"error": {"class": error.error_type.unwrap_or("CommandRejected"), "message": error.message}})
            }
        };
        let mut expected = case.clone();
        let map = expected.as_object_mut().unwrap();
        for key in ["model", "role", "reasoning_effort", "reasoning_effort_once", "speed"] {
            map.shift_remove(key);
        }
        assert_eq!(text(&actual), text(&expected), "{case}");
    }
}

#[tokio::test]
async fn token_estimates_match_python() {
    let vectors = vectors();
    let estimate = &vectors["estimate"];
    let hook = TokenEstimateHook::new();
    for (item, expected) in
        estimate["observations"].as_array().unwrap().iter().zip(estimate["results"].as_array().unwrap())
    {
        let usage = TokenUsage {
            prompt_tokens: PyValue::from_json(item["prompt_tokens"].clone()),
            ..TokenUsage::default()
        };
        let response = ProviderResponse {
            content: Some("ok".into()),
            usage,
            usage_reported: item["reported"].as_bool(),
            ..ProviderResponse::default()
        };
        let context = HookContext {
            provider_key: item["provider_key"].as_str().unwrap().into(),
            model_id: item["model_id"].as_str().unwrap().into(),
            message_count: 1,
            tool_count: 0,
            request_json: item["request"].as_object().cloned(),
            response: Some(response),
            ..HookContext::default()
        };
        let actual = match hook.on_response(&context).await {
            Ok(()) => json!({"ok": true}),
            Err(message) => json!({"error": {"class": "TypeError", "message": message}}),
        };
        assert_eq!(actual, *expected, "{item}");
    }
    let models: Vec<Option<ModelInfo>> = estimate["models"]
        .as_array()
        .unwrap()
        .iter()
        .map(|spec| {
            spec.as_object().map(|_| {
                ModelInfo::new(
                    spec["id"].as_str().unwrap(),
                    "Model A",
                    "Test",
                    spec["provider_key"].as_str().unwrap(),
                    "byok",
                )
            })
        })
        .collect();
    for (value, row) in
        estimate["values"].as_array().unwrap().iter().zip(estimate["estimates"].as_array().unwrap())
    {
        let actual: Vec<i64> = models.iter().map(|model| hook.estimate(value, model.as_ref())).collect();
        let expected: Vec<i64> = row.as_array().unwrap().iter().map(|item| item.as_i64().unwrap()).collect();
        assert_eq!(actual, expected, "estimates of {value}");
    }
    // `token_estimator` registers one hook and finds it again.
    let fake = Fake {
        model: None,
        flags: Vec::new(),
        calls: Arc::default(),
        events: Arc::default(),
        hooks: HookManager::new(),
    };
    let first = token_estimator(&fake);
    let second = token_estimator(&fake);
    assert!(std::ptr::eq(first.hook(), second.hook()), "shared");
    assert_eq!(estimate["shared"], json!(true));
    assert_eq!(fake.hooks.hooks().len() as u64, estimate["registered"].as_u64().unwrap());
}
