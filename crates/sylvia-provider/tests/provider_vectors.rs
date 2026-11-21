//! The Rust `OpenAICompatProvider` against the Python provider's recorded behaviour.
//!
//! `chat_vectors.json` holds what the real Python provider did with each scenario's response
//! bytes (an httpx mock transport). Here the same bytes go through the Rust provider over the
//! scripted transport, and everything observable is compared: the chunks or response, the error,
//! the hooks fired with their contexts, the requests sent (URL, body bytes, headers) and the
//! number of attempts. `http_vectors.json` covers URL splitting, the OpenCode routes, the SDK's
//! request URL and header assembly.

use futures_util::StreamExt;
use serde_json::Value;
use std::sync::{Arc, Mutex};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_provider::hooks::{HookContext, ProviderHook};
use sylvia_provider::http::{Piece, ScriptedReply, ScriptedTransport};
use sylvia_provider::openai_compat::{sylvia_user_agent, OpenAICompatProvider, SdkEnvironment};
use sylvia_provider::provider::BoxFuture;
use sylvia_provider::tool_delta::{delta_value, parse_tool_call_delta};
use sylvia_provider::types::{ModelInfo, ProviderStreamChunk, PyValue, RetryConfig};
use sylvia_provider::{ErrorKind, Provider, ProviderError, ProviderRequest};

fn chat_vectors() -> Value {
    serde_json::from_str(include_str!("vectors/chat_vectors.json")).expect("chat vectors")
}

fn http_vectors() -> Value {
    serde_json::from_str(include_str!("vectors/http_vectors.json")).expect("http vectors")
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

/// Records every hook call with its context.
#[derive(Default)]
struct Recorder {
    calls: Mutex<Vec<(String, HookContext)>>,
}

impl Recorder {
    fn push(&self, name: &str, context: &HookContext) {
        self.calls.lock().unwrap().push((name.to_owned(), context.clone()));
    }
}

impl ProviderHook for Recorder {
    fn name(&self) -> &str {
        "Recorder"
    }
    fn on_request<'a>(&'a self, context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        self.push("on_request", context);
        Box::pin(async { Ok(()) })
    }
    fn on_response<'a>(&'a self, context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        self.push("on_response", context);
        Box::pin(async { Ok(()) })
    }
    fn on_error<'a>(&'a self, context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        self.push("on_error", context);
        Box::pin(async { Ok(()) })
    }
}

struct Run {
    provider: OpenAICompatProvider,
    transport: ScriptedTransport,
    recorder: Arc<Recorder>,
}

fn provider_for(scenario: &Value, reply: ScriptedReply, retry: RetryConfig) -> Run {
    let transport = ScriptedTransport::new(vec![reply]);
    let info = ModelInfo::new(
        "test/model-a",
        "Model A",
        "Test Provider",
        scenario["provider_key"].as_str().unwrap(),
        "byok",
    );
    let base_url = scenario.get("base_url").and_then(Value::as_str).unwrap_or("http://standin.test/v1");
    let provider = OpenAICompatProvider::new(
        base_url,
        Some("test-key-not-a-secret"),
        Some(info),
        Some(retry),
        None,
        pairs(scenario.get("configured_headers").unwrap_or(&Value::Null)),
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

/// Python's error class as the Rust provider reports it. Where Python's hooks saw a plain
/// exception (`APIError`, `JSONDecodeError`, `AttributeError`, `KeyError`), Python itself
/// raises `ProviderConnectionError` afterwards, which is what Rust hands the hooks.
fn hook_error_class(python: &str) -> &str {
    match python {
        "APIError" | "JSONDecodeError" | "AttributeError" | "KeyError" | "TypeError" => {
            "ProviderConnectionError"
        }
        other => other,
    }
}

/// The headers Python sends that Rust must send too: without httpx's own (`Host`,
/// `Accept-Encoding`, `Connection`, `Content-Length`, which the HTTP client writes) and the
/// SDK's identity headers (`X-Stainless-*`), and with Sylvia's `User-Agent` in place of the SDK's.
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
        .map(|(name, value)| {
            if name == "User-Agent" && value.starts_with("AsyncOpenAI/Python") {
                (name, sylvia_user_agent())
            } else {
                (name, value)
            }
        })
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
        assert_eq!(ours.provider_key, theirs["provider_key"].as_str().unwrap(), "{context}");
        assert_eq!(ours.model_id, theirs["model_id"].as_str().unwrap(), "{context}");
        assert_eq!(ours.message_count as u64, theirs["message_count"].as_u64().unwrap(), "{context}");
        assert_eq!(ours.tool_count as u64, theirs["tool_count"].as_u64().unwrap(), "{context}");
        assert_eq!(ours.stream, theirs["stream"].as_bool().unwrap(), "{context}");
        assert_eq!(ours.retry_attempt, theirs["retry_attempt"].as_i64().unwrap(), "{context}");
        assert_eq!(ours.http_status.map(u64::from), theirs["http_status"].as_u64(), "{context}");
        let request = ours.request_json.clone().map(Json::Object).unwrap_or(Json::Null);
        assert_eq!(
            pyjson::dumps(&request, Dump::DEFAULT),
            pyjson::dumps(&theirs["request_json"], Dump::DEFAULT),
            "request_json, {context}"
        );
        match theirs["error_type"].as_str() {
            None => assert!(ours.error.is_none(), "{context}"),
            Some(class) => {
                let error = ours.error.as_ref().expect("error context");
                assert_eq!(error.kind.class_name(), hook_error_class(class), "{context}");
            }
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
            assert_eq!(
                delta_value(delta, "extra_content").cloned().unwrap_or(Json::Null),
                want["extra_content"],
                "{context}"
            );
        }
        (ours, theirs) => panic!("{context}: delta {ours:?}, Python {theirs}"),
    }
    let metadata = actual.chat_completion_metadata.clone().map(Json::Object).unwrap_or(Json::Null);
    assert_eq!(metadata, expected["chat_completion_metadata"], "metadata, {context}");
}

fn check_error(error: Option<&ProviderError>, expected: &Value, name: &str) {
    match (expected, error) {
        (Value::Null, None) => {}
        (expected, Some(error)) if !expected.is_null() => {
            assert_eq!(
                error.kind.class_name(),
                expected["class"].as_str().unwrap(),
                "error class for {name}"
            );
            if name != "malformed-json" {
                assert_eq!(error.message, expected["message"].as_str().unwrap(), "error text for {name}");
            }
            assert_eq!(error.retry_after, expected["retry_after"].as_f64(), "retry_after for {name}");
        }
        (expected, actual) => panic!("{name}: Python {expected}, Rust {actual:?}"),
    }
}

fn tools(vectors: &Value) -> Vec<Json> {
    vectors["requests"][0]["format_request"]["tools"].as_array().unwrap().clone()
}

#[tokio::test(start_paused = true)]
async fn streams_through_the_provider_match_python() {
    let vectors = chat_vectors();
    for scenario in vectors["streams"].as_array().unwrap() {
        let name = scenario["name"].as_str().unwrap();
        let pieces =
            scenario["pieces"].as_array().unwrap().iter().map(|p| b64(p.as_str().unwrap())).collect();
        let reply =
            reply_for(scenario["status"].as_u64().unwrap() as u16, &scenario["response_headers"], pieces);
        let run = provider_for(scenario, reply, RetryConfig { max_retries: 0, ..RetryConfig::default() });
        let request = ProviderRequest {
            messages: scenario["messages"].as_array().unwrap().clone(),
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

#[tokio::test(start_paused = true)]
async fn completions_through_the_provider_match_python() {
    let vectors = chat_vectors();
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
        let run = provider_for(scenario, reply, retry);
        let mut options = JsonMap::new();
        options.insert("max_tokens".into(), Json::from(256));
        let request = ProviderRequest {
            messages: vectors["streams"][0]["messages"].as_array().unwrap().clone(),
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
                assert_eq!(
                    response.reasoning_content.as_deref(),
                    expected["reasoning_content"].as_str(),
                    "{name}"
                );
                let calls: Vec<Value> = response
                    .tool_calls
                    .iter()
                    .map(|c| serde_json::json!([c.id.to_json(), c.name.to_json(), c.arguments.to_json()]))
                    .collect();
                assert_eq!(Value::Array(calls), expected["tool_calls"], "{name}");
                assert_eq!(response.finish_reason.to_json(), expected["finish_reason"], "{name}");
            }
            (expected, outcome) => panic!("{name}: Python {expected}, Rust {outcome:?}"),
        }
        check_error(outcome.as_ref().err(), &scenario["error"], name);
        check_hooks(&run, scenario, name);
        check_requests(&run, scenario, name);
    }
}

#[test]
fn urlsplit_matches_python() {
    use sylvia_provider::pyurl::{urlsplit, urlunsplit};
    for case in http_vectors()["urlsplit"].as_array().unwrap() {
        let url = case["url"].as_str().unwrap();
        match (urlsplit(url), case.get("error")) {
            (Err(error), Some(expected)) => assert_eq!(error.0, expected.as_str().unwrap(), "{url:?}"),
            (Ok(parts), None) => {
                let ours = [&parts.scheme, &parts.netloc, &parts.path, &parts.query, &parts.fragment];
                let theirs: Vec<&str> =
                    case["parts"].as_array().unwrap().iter().map(|v| v.as_str().unwrap()).collect();
                assert_eq!(ours.iter().map(|s| s.as_str()).collect::<Vec<_>>(), theirs, "{url:?}");
                assert_eq!(parts.hostname().as_deref(), case["hostname"].as_str(), "hostname of {url:?}");
                match (parts.port(), case["port_error"].as_str()) {
                    (Ok(port), None) => {
                        assert_eq!(port.map(u64::from), case["port"].as_u64(), "port of {url:?}")
                    }
                    (Err(_), Some(_)) => {}
                    (ours, theirs) => panic!("port of {url:?}: Rust {ours:?}, Python {theirs:?}"),
                }
                assert_eq!(urlunsplit(&parts), case["unsplit"].as_str().unwrap(), "unsplit {url:?}");
            }
            (ours, theirs) => panic!("{url:?}: Rust {ours:?}, Python {theirs:?}"),
        }
    }
}

#[test]
fn opencode_routes_match_python() {
    use sylvia_provider::routes::{inference_base_url, is_opencode_inference};
    for case in http_vectors()["inference_base_url"].as_array().unwrap() {
        let (url, protocol) = (case[0].as_str().unwrap(), case[1].as_str().unwrap());
        assert_eq!(is_opencode_inference(url).unwrap(), case[2].as_bool().unwrap(), "{url}");
        assert_eq!(inference_base_url(url, protocol).unwrap(), case[3].as_str().unwrap(), "{url} {protocol}");
    }
}

#[test]
fn sdk_request_urls_match_python() {
    for case in http_vectors()["sdk_urls"].as_array().unwrap() {
        let base = case[0].as_str().unwrap();
        let url = sylvia_provider::httpx_url::HttpxUrl::sdk_endpoint(base, "/chat/completions").unwrap();
        assert_eq!(url.to_string(), case[1].as_str().unwrap(), "{base}");
    }
}

#[tokio::test]
async fn header_assembly_matches_python() {
    for case in http_vectors()["headers"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let env = case.get("env").cloned().unwrap_or(Value::Null);
        let environment = SdkEnvironment {
            organization: env["OPENAI_ORG_ID"].as_str().map(str::to_owned),
            project: env["OPENAI_PROJECT_ID"].as_str().map(str::to_owned),
        };
        let body = b"data: {\"choices\":[{\"delta\":{\"content\":\"x\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n".to_vec();
        let transport = ScriptedTransport::new(vec![ScriptedReply::ok("text/event-stream", vec![body])]);
        let provider = OpenAICompatProvider::new(
            "http://127.0.0.1:1234/v1",
            Some(case.get("api_key").and_then(Value::as_str).unwrap_or("k")),
            Some(ModelInfo::new("t/m", "M", "P", "byok", "byok")),
            Some(RetryConfig { max_retries: 0, ..RetryConfig::default() }),
            None,
            pairs(case.get("headers").unwrap_or(&Value::Null)),
            environment,
            Arc::new(transport.clone()),
        )
        .unwrap();
        let mut options = JsonMap::new();
        if let Some(extra) = case.get("extra_headers") {
            options.insert("extra_headers".into(), extra.clone());
        }
        let request = ProviderRequest {
            messages: vec![serde_json::json!({"role": "user", "content": "hi"})],
            options,
            ..ProviderRequest::new(Vec::new())
        };
        let items: Vec<_> = provider.generate_stream(request).collect().await;
        assert!(items.iter().all(Result::is_ok), "{name}: {items:?}");
        assert_eq!(transport.requests()[0].headers, expected_headers(&case["sent_headers"]), "{name}");
    }
}

#[tokio::test]
async fn unknown_keyword_escapes_as_an_internal_error() {
    let transport = ScriptedTransport::new(Vec::new());
    let provider = OpenAICompatProvider::new(
        "http://h/v1",
        None,
        None,
        None,
        None,
        Vec::new(),
        SdkEnvironment::default(),
        Arc::new(transport.clone()),
    )
    .unwrap();
    let mut options = JsonMap::new();
    options.insert("thinking".into(), serde_json::json!({"type": "adaptive"}));
    let request = ProviderRequest {
        options,
        ..ProviderRequest::new(vec![serde_json::json!({"role": "user", "content": "x"})])
    };
    let items: Vec<_> = provider.generate_stream(request).collect().await;
    let error = items[0].as_ref().unwrap_err();
    assert_eq!(error.kind, ErrorKind::Internal);
    assert_eq!(error.message, "AsyncCompletions.create() got an unexpected keyword argument 'thinking'");
    assert_eq!(items.len(), 1, "an error ends the stream");
    assert!(transport.requests().is_empty(), "nothing is sent");
}
