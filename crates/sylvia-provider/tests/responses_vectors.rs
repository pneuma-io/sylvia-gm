//! The Rust `OpenAICompatProvider` with the Responses transport against the Python provider's
//! recorded behaviour (`vectors/generate_responses_vectors.py`, lane 5). The same bytes go
//! through the Rust provider over the scripted transport, and everything observable is compared:
//! the request formatted and sent (URL, body bytes, headers), the chunks or response, the error,
//! the hooks fired, and the retained output items. The comparisons follow lane 4's
//! `provider_vectors.rs`.

use futures_util::StreamExt;
use serde_json::Value;
use std::sync::{Arc, Mutex};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_provider::hooks::{HookContext, ProviderHook};
use sylvia_provider::http::{Piece, ScriptedReply, ScriptedTransport};
use sylvia_provider::openai_compat::{sylvia_user_agent, OpenAICompatProvider, SdkEnvironment};
use sylvia_provider::provider::BoxFuture;
use sylvia_provider::tool_delta::parse_tool_call_delta;
use sylvia_provider::types::{ModelInfo, ProviderStreamChunk, PyValue, RetryConfig};
use sylvia_provider::{Provider, ProviderError, ProviderRequest};

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/responses_vectors.json")).expect("responses vectors")
}

fn b64(text: &str) -> Vec<u8> {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let (mut out, mut buffer, mut bits) = (Vec::new(), 0u32, 0);
    for byte in text.bytes().filter(|b| *b != b'=') {
        buffer = (buffer << 6) | ALPHABET.iter().position(|c| *c == byte).expect("base64") as u32;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buffer >> bits) as u8);
            buffer &= (1 << bits) - 1;
        }
    }
    out
}

fn pairs(value: &Value) -> Vec<(String, String)> {
    value
        .as_object()
        .map(|map| map.iter().map(|(k, v)| (k.clone(), v.as_str().unwrap().to_owned())).collect())
        .unwrap_or_default()
}

#[derive(Default)]
struct Recorder {
    calls: Mutex<Vec<(String, HookContext)>>,
}

impl ProviderHook for Recorder {
    fn name(&self) -> &str {
        "Recorder"
    }
    fn on_request<'a>(&'a self, context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        self.calls.lock().unwrap().push(("on_request".into(), context.clone()));
        Box::pin(async { Ok(()) })
    }
    fn on_response<'a>(&'a self, context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        self.calls.lock().unwrap().push(("on_response".into(), context.clone()));
        Box::pin(async { Ok(()) })
    }
    fn on_error<'a>(&'a self, context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        self.calls.lock().unwrap().push(("on_error".into(), context.clone()));
        Box::pin(async { Ok(()) })
    }
}

struct Run {
    provider: OpenAICompatProvider,
    transport: ScriptedTransport,
    recorder: Arc<Recorder>,
}

fn provider_for(base_url: &str, reply: ScriptedReply, retry: RetryConfig) -> Run {
    let transport = ScriptedTransport::new(vec![reply]);
    let mut info = ModelInfo::new("test/model-a", "Model A", "Test Provider", "byok", "byok");
    info.transport = "responses".into();
    let provider = OpenAICompatProvider::new(
        base_url,
        Some("test-key-not-a-secret"),
        Some(info.post_init()),
        Some(retry),
        None,
        Vec::new(),
        SdkEnvironment::default(),
        Arc::new(transport.clone()),
    )
    .expect("provider builds");
    let recorder = Arc::new(Recorder::default());
    provider.hooks.register(recorder.clone());
    Run { provider, transport, recorder }
}

fn reply_for(status: u16, headers: &Value, pieces: Vec<Vec<u8>>) -> ScriptedReply {
    ScriptedReply::Respond {
        status,
        headers: pairs(headers),
        pieces: pieces.into_iter().map(Piece::Bytes).collect(),
    }
}

/// Python's error class as Rust reports it: plain Python exceptions that escape the provider
/// (`TypeError` from the SDK) are Rust's `Internal`; inside the stream's handler they became
/// `ProviderConnectionError`.
fn class_of(python: &str) -> &str {
    match python {
        "TypeError" | "AttributeError" | "KeyError" => "Exception",
        "APIError" | "JSONDecodeError" => "ProviderConnectionError",
        other => other,
    }
}

/// Python's class in a hook context as Rust reports it: where Python's hooks saw a plain exception,
/// Python raises `ProviderConnectionError` afterwards, which is what Rust hands the hooks (as in
/// lane 4's `provider_vectors.rs`).
fn hook_class_of(python: &str) -> &str {
    match python {
        "APIError" | "JSONDecodeError" | "AttributeError" | "KeyError" | "TypeError" => {
            "ProviderConnectionError"
        }
        other => other,
    }
}

/// Python's headers less httpx's own and the SDK's identity headers, with Sylvia's User-Agent
/// (differences row 8).
fn expected_headers(python: &Value) -> Vec<(String, String)> {
    python
        .as_array()
        .unwrap()
        .iter()
        .map(|pair| (pair[0].as_str().unwrap().to_owned(), pair[1].as_str().unwrap().to_owned()))
        .filter(|(name, _)| {
            let lower = name.to_ascii_lowercase();
            !matches!(lower.as_str(), "host" | "accept-encoding" | "connection" | "content-length")
                && !lower.starts_with("x-stainless")
        })
        .map(|(name, value)| if name == "User-Agent" { (name, sylvia_user_agent()) } else { (name, value) })
        .collect()
}

fn check_requests(run: &Run, scenario: &Value, name: &str) {
    let sent = run.transport.requests();
    let python = scenario["requests"].as_array().unwrap();
    assert_eq!(sent.len(), python.len(), "attempts for {name}");
    for (ours, theirs) in sent.iter().zip(python) {
        assert_eq!(ours.method, theirs["method"].as_str().unwrap(), "{name}");
        assert_eq!(ours.url, theirs["url"].as_str().unwrap(), "url for {name}");
        assert_eq!(
            String::from_utf8(ours.body.clone()).unwrap(),
            theirs["body"].as_str().unwrap(),
            "body for {name}"
        );
        assert_eq!(ours.headers, expected_headers(&theirs["headers"]), "headers for {name}");
    }
}

fn check_hooks(run: &Run, scenario: &Value, name: &str) {
    let calls = run.recorder.calls.lock().unwrap().clone();
    let python = scenario["hooks"].as_array().unwrap();
    let names: Vec<&str> = calls.iter().map(|(hook, _)| hook.as_str()).collect();
    let python_names: Vec<&str> = python.iter().map(|hook| hook["hook"].as_str().unwrap()).collect();
    assert_eq!(names, python_names, "hooks for {name}");
    for ((hook, ours), theirs) in calls.iter().zip(python) {
        let theirs = &theirs["context"];
        let context = format!("{hook} for {name}");
        assert_eq!(ours.stream, theirs["stream"].as_bool().unwrap(), "{context}");
        assert_eq!(ours.http_status.map(u64::from), theirs["http_status"].as_u64(), "{context}");
        let request = ours.request_json.clone().map(Json::Object).unwrap_or(Json::Null);
        assert_eq!(
            pyjson::dumps(&request, Dump::DEFAULT),
            pyjson::dumps(&theirs["request_json"], Dump::DEFAULT),
            "request_json, {context}"
        );
        match theirs["error_type"].as_str() {
            None => assert!(ours.error.is_none(), "{context}"),
            Some(class) => assert_eq!(
                ours.error.as_ref().expect("error context").kind.class_name(),
                hook_class_of(class),
                "{context}"
            ),
        }
        match (&ours.response, &theirs["response"]) {
            (None, Value::Null) => {}
            (Some(response), expected) if !expected.is_null() => {
                let usage = &response.usage;
                assert_eq!(
                    serde_json::json!([
                        usage.prompt_tokens.to_json(),
                        usage.completion_tokens.to_json(),
                        usage.total_tokens.to_json(),
                        usage.reasoning_tokens
                    ]),
                    expected["usage"],
                    "usage, {context}"
                );
                assert_eq!(response.usage_reported, expected["usage_reported"].as_bool(), "{context}");
                let cache = &response.cache_usage;
                assert_eq!(
                    serde_json::json!([
                        cache.cache_read_tokens,
                        cache.cache_write_tokens,
                        cache.cache_miss_tokens,
                        cache.total_prompt_tokens
                    ]),
                    expected["cache"],
                    "cache, {context}"
                );
                assert_eq!(
                    response.provider_reported_reasoning_effort.as_deref(),
                    expected["reported_effort"].as_str(),
                    "{context}"
                );
                assert_eq!(
                    response.provider_reported_service_tier.as_deref(),
                    expected["reported_tier"].as_str(),
                    "{context}"
                );
            }
            (ours, theirs) => panic!("{context}: response {ours:?}, Python {theirs}"),
        }
    }
}

fn check_chunk(actual: &ProviderStreamChunk, expected: &Value, context: &str) {
    assert_eq!(actual.content, expected["content"].as_str().unwrap(), "content, {context}");
    assert_eq!(
        actual.reasoning_content,
        expected["reasoning_content"].as_str().unwrap(),
        "reasoning, {context}"
    );
    let finish = actual.finish_reason.as_ref().map(PyValue::to_json).unwrap_or(Value::Null);
    assert_eq!(finish, expected["finish_reason"], "finish, {context}");
    match (&actual.tool_call_delta, &expected["tool_call_delta"]) {
        (None, Value::Null) => {}
        (Some(delta), want) if !want.is_null() => {
            let parsed = parse_tool_call_delta(delta);
            let python = &want["parsed"];
            assert_eq!(parsed.index as u64, python[0].as_u64().unwrap(), "{context}");
            assert_eq!(parsed.call_id, python[1].as_str().unwrap(), "{context}");
            assert_eq!(parsed.name, python[2].as_str().unwrap(), "{context}");
            assert_eq!(parsed.arguments, python[3].as_str().unwrap(), "{context}");
        }
        (ours, theirs) => panic!("{context}: delta {ours:?}, Python {theirs}"),
    }
    let metadata = actual.chat_completion_metadata.clone().map(Json::Object).unwrap_or(Json::Null);
    assert_eq!(
        pyjson::dumps(&metadata, Dump::DEFAULT),
        pyjson::dumps(&expected["chat_completion_metadata"], Dump::DEFAULT),
        "metadata, {context}"
    );
    assert_eq!(
        actual.provider_reported_reasoning_effort.as_deref(),
        expected["reported_effort"].as_str(),
        "{context}"
    );
    assert_eq!(
        actual.provider_reported_service_tier.as_deref(),
        expected["reported_tier"].as_str(),
        "{context}"
    );
}

fn check_error(error: Option<&ProviderError>, expected: &Value, name: &str) {
    match (expected, error) {
        (Value::Null, None) => {}
        (expected, Some(error)) if !expected.is_null() => {
            assert_eq!(
                error.kind.class_name(),
                class_of(expected["class"].as_str().unwrap()),
                "error class for {name}"
            );
            assert_eq!(error.message, expected["message"].as_str().unwrap(), "error text for {name}");
            if let Some(retry_after) = expected.get("retry_after") {
                assert_eq!(error.retry_after, retry_after.as_f64(), "retry_after for {name}");
            }
        }
        (expected, actual) => panic!("{name}: Python {expected}, Rust {actual:?}"),
    }
}

fn tools(vectors: &Value) -> Vec<Json> {
    vectors["tools"].as_array().unwrap().clone()
}

#[test]
fn format_request_matches_python() {
    let vectors = vectors();
    let tools = tools(&vectors);
    for case in vectors["requests"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let mut kwargs = case["kwargs"].as_object().unwrap().clone();
        kwargs.shift_remove("_request_context_id");
        let ours = sylvia_provider::responses::format_request(
            "model-a",
            case["messages"].as_array().unwrap(),
            Some(&tools),
            case["stream"].as_bool().unwrap(),
            &kwargs,
        );
        match (ours, case.get("format_error")) {
            (Ok(request), None) => assert_eq!(
                pyjson::dumps(&Json::Object(request), Dump::DEFAULT),
                pyjson::dumps(&case["format_request"], Dump::DEFAULT),
                "{name}"
            ),
            (Err(sylvia_provider::chat::ChatError::Provider(error)), Some(expected)) => {
                assert_eq!(error.kind.class_name(), expected["class"].as_str().unwrap(), "{name}");
                assert_eq!(error.message, expected["message"].as_str().unwrap(), "{name}");
            }
            (ours, theirs) => panic!("{name}: Rust {ours:?}, Python {theirs:?}"),
        }
    }
}

#[tokio::test(start_paused = true)]
async fn requests_sent_through_the_provider_match_python() {
    let vectors = vectors();
    for case in vectors["requests"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let stream = case["stream"].as_bool().unwrap();
        let body = if stream {
            br#"event: response.completed
data: {"type": "response.completed", "response": {"id": "resp_1", "object": "response", "created_at": 1, "model": "model-a", "status": "completed", "output": []}}

"#
            .to_vec()
        } else {
            br#"{"id": "resp_1", "object": "response", "created_at": 1, "model": "model-a", "status": "completed", "output": []}"#
                .to_vec()
        };
        let headers = serde_json::json!({"content-type": if stream { "text/event-stream" } else { "application/json" }});
        let run = provider_for(
            case["base_url"].as_str().unwrap(),
            reply_for(200, &headers, vec![body]),
            RetryConfig { max_retries: 0, ..RetryConfig::default() },
        );
        let request = ProviderRequest {
            messages: case["messages"].as_array().unwrap().clone(),
            tools: Some(tools(&vectors)),
            stream: false,
            timeout: 60.0,
            options: case["kwargs"].as_object().unwrap().clone(),
        };
        let error = if stream {
            let mut stream = run.provider.generate_stream(request);
            let mut error = None;
            while let Some(item) = stream.next().await {
                if let Err(failure) = item {
                    error = Some(failure);
                }
            }
            error
        } else {
            run.provider.generate(request).await.err()
        };
        check_error(error.as_ref(), &case["provider_error"], name);
        check_requests(&run, case, name);
        check_hooks(&run, case, name);
    }
}

#[tokio::test(start_paused = true)]
async fn completions_through_the_provider_match_python() {
    let vectors = vectors();
    for scenario in vectors["completions"].as_array().unwrap() {
        let name = scenario["name"].as_str().unwrap();
        let reply = reply_for(
            scenario["status"].as_u64().unwrap() as u16,
            &scenario["response_headers"],
            vec![b64(scenario["body"].as_str().unwrap())],
        );
        let retries = scenario["retries"].as_u64().unwrap() as u32;
        let retry =
            RetryConfig { max_retries: retries, base_delay: 0.0, max_delay: 0.0, ..RetryConfig::default() };
        let run = provider_for("http://standin.test/v1", reply, retry);
        let mut options = JsonMap::new();
        options.insert("max_tokens".into(), Json::from(256));
        let request = ProviderRequest {
            messages: vectors["streams"][0]["requests"][0]["body"]
                .as_str()
                .map(|_| vectors["requests"][0]["messages"].as_array().unwrap().clone())
                .unwrap(),
            tools: Some(tools(&vectors)),
            stream: false,
            timeout: 60.0,
            options,
        };
        let outcome = run.provider.generate(request).await;
        match (&scenario["response"], &outcome) {
            (Value::Null, Err(_)) => {}
            (expected, Ok(response)) if !expected.is_null() => {
                assert_eq!(response.content.as_deref(), expected["content"].as_str(), "{name}");
                let calls: Vec<Value> = response
                    .tool_calls
                    .iter()
                    .map(|c| serde_json::json!([c.id.to_json(), c.name.to_json(), c.arguments.to_json()]))
                    .collect();
                assert_eq!(Value::Array(calls), expected["tool_calls"], "{name}");
                assert_eq!(response.finish_reason.to_json(), expected["finish_reason"], "{name}");
                let usage = &response.usage;
                assert_eq!(
                    serde_json::json!([
                        usage.prompt_tokens.to_json(),
                        usage.completion_tokens.to_json(),
                        usage.total_tokens.to_json(),
                        usage.reasoning_tokens
                    ]),
                    expected["usage"],
                    "usage for {name}"
                );
                assert_eq!(response.usage_reported, expected["usage_reported"].as_bool(), "{name}");
                let metadata =
                    response.chat_completion_metadata.clone().map(Json::Object).unwrap_or(Json::Null);
                assert_eq!(
                    pyjson::dumps(&metadata, Dump::DEFAULT),
                    pyjson::dumps(&expected["chat_completion_metadata"], Dump::DEFAULT),
                    "retained output for {name}"
                );
                assert_eq!(
                    response.provider_reported_reasoning_effort.as_deref(),
                    expected["reported_effort"].as_str(),
                    "{name}"
                );
                assert_eq!(
                    response.provider_reported_service_tier.as_deref(),
                    expected["reported_tier"].as_str(),
                    "{name}"
                );
            }
            (expected, outcome) => panic!("{name}: Python {expected}, Rust {outcome:?}"),
        }
        check_error(outcome.as_ref().err(), &scenario["error"], name);
        check_hooks(&run, scenario, name);
        check_requests(&run, scenario, name);
    }
}

#[tokio::test(start_paused = true)]
async fn streams_through_the_provider_match_python() {
    let vectors = vectors();
    for scenario in vectors["streams"].as_array().unwrap() {
        let name = scenario["name"].as_str().unwrap();
        let pieces =
            scenario["pieces"].as_array().unwrap().iter().map(|p| b64(p.as_str().unwrap())).collect();
        let reply =
            reply_for(scenario["status"].as_u64().unwrap() as u16, &scenario["response_headers"], pieces);
        let run = provider_for(
            "http://standin.test/v1",
            reply,
            RetryConfig { max_retries: 0, ..RetryConfig::default() },
        );
        let request = ProviderRequest {
            messages: vectors["requests"][0]["messages"].as_array().unwrap().clone(),
            tools: Some(tools(&vectors)),
            stream: false,
            timeout: 30.0,
            options: scenario["kwargs"].as_object().unwrap().clone(),
        };
        let mut chunks = Vec::new();
        let mut error = None;
        let mut stream = run.provider.generate_stream(request);
        while let Some(item) = stream.next().await {
            match item {
                Ok(chunk) => chunks.push(chunk),
                Err(failure) => error = Some(failure),
            }
        }
        drop(stream);
        let expected = scenario["chunks"].as_array().unwrap();
        assert_eq!(chunks.len(), expected.len(), "chunk count for {name}");
        for (position, (ours, theirs)) in chunks.iter().zip(expected).enumerate() {
            check_chunk(ours, theirs, &format!("{name} chunk {position}"));
        }
        check_error(error.as_ref(), &scenario["error"], name);
        check_hooks(&run, scenario, name);
        check_requests(&run, scenario, name);
    }
}

#[test]
fn retained_output_items_match_pythons_dump() {
    for case in vectors()["native_output"].as_array().unwrap() {
        let ours = sylvia_provider::responses::native_output(Some(&case["items"]));
        match (ours, case.get("error")) {
            (Ok(items), None) => assert_eq!(
                pyjson::dumps(&Json::Array(items), Dump::DEFAULT),
                pyjson::dumps(&case["retained"], Dump::DEFAULT),
                "{}",
                case["items"]
            ),
            (ours, theirs) => panic!("{}: Rust {ours:?}, Python {theirs:?}", case["items"]),
        }
    }
}

#[test]
fn the_model_table_is_the_locked_sdks() {
    let (openai, pydantic) = sylvia_provider::responses::model_table_versions();
    assert_eq!(openai, vectors()["openai"].as_str().unwrap());
    assert_eq!(pydantic, "2.13.4");
}
