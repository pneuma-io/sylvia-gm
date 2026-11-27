//! The Rust `AnthropicProvider` against the Python provider's recorded behaviour
//! (`vectors/generate_anthropic_vectors.py`, lane 5). The same bytes go through the Rust
//! provider over the scripted transport, and everything observable is compared: the request
//! formatted and sent (URL, body bytes, headers), the chunks or response, the error, the hooks
//! fired, and the retained native content. The comparisons follow `responses_vectors.rs`.

use futures_util::StreamExt;
use serde_json::Value;
use std::sync::{Arc, Mutex};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_provider::anthropic::{self, AnthropicEnvironment, AnthropicProvider};
use sylvia_provider::hooks::{HookContext, ProviderHook};
use sylvia_provider::http::{Piece, ScriptedReply, ScriptedTransport};
use sylvia_provider::openai_compat::sylvia_user_agent;
use sylvia_provider::provider::BoxFuture;
use sylvia_provider::tool_delta::parse_tool_call_delta;
use sylvia_provider::types::{ModelInfo, ProviderStreamChunk, PyValue, RetryConfig};
use sylvia_provider::{Provider, ProviderError, ProviderRequest};

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/anthropic_vectors.json")).expect("anthropic vectors")
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

fn dumps(value: &Json) -> String {
    pyjson::dumps(value, Dump::DEFAULT)
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
    provider: AnthropicProvider,
    transport: ScriptedTransport,
    recorder: Arc<Recorder>,
}

struct Setup<'a> {
    base_url: &'a str,
    provider_key: &'a str,
    api_key: &'a str,
    model: &'a str,
    headers: Vec<(String, String)>,
}

impl Default for Setup<'_> {
    fn default() -> Self {
        Self {
            base_url: "http://standin.test/v1",
            provider_key: "byok",
            api_key: "test-key-not-a-secret",
            model: "claude-sonnet-5-5",
            headers: Vec::new(),
        }
    }
}

fn provider_for(setup: &Setup, reply: ScriptedReply, retry: RetryConfig) -> Run {
    let transport = ScriptedTransport::new(vec![reply]);
    let mut info =
        ModelInfo::new(format!("test/{}", setup.model), "Model", "Test Provider", setup.provider_key, "byok");
    info.transport = "anthropic_messages".into();
    let provider = AnthropicProvider::new(
        setup.base_url,
        setup.api_key,
        info.post_init(),
        Some(retry),
        None,
        4096,
        setup.headers.clone(),
        AnthropicEnvironment::default(),
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

/// Python's error class as Rust reports it: plain Python exceptions (`TypeError` from the SDK,
/// `KeyError` from the stream's block bookkeeping) escape the Anthropic provider as they are,
/// which is Rust's `Internal`, and its hooks see them so.
fn class_of(python: &str) -> &str {
    match python {
        "TypeError" | "AttributeError" | "KeyError" | "JSONDecodeError" => "Exception",
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

fn check_usage(response: &sylvia_provider::types::ProviderResponse, expected: &Value, context: &str) {
    let usage = &response.usage;
    sylvia_base::testing::assert_same_json(
        &serde_json::json!([
            usage.prompt_tokens.to_json(),
            usage.completion_tokens.to_json(),
            usage.total_tokens.to_json(),
            usage.reasoning_tokens
        ]),
        &expected["usage"],
        format!("usage, {context}"),
    );
    assert_eq!(response.usage_reported, expected["usage_reported"].as_bool(), "usage reported, {context}");
    let cache = &response.cache_usage;
    sylvia_base::testing::assert_same_json(
        &serde_json::json!([
            cache.cache_read_tokens,
            cache.cache_write_tokens,
            cache.cache_miss_tokens,
            cache.total_prompt_tokens
        ]),
        &expected["cache"],
        format!("cache, {context}"),
    );
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
        assert_eq!(dumps(&request), dumps(&theirs["request_json"]), "request_json, {context}");
        match theirs["error_type"].as_str() {
            None => assert!(ours.error.is_none(), "{context}"),
            Some(class) => assert_eq!(
                ours.error.as_ref().expect("error context").kind.class_name(),
                class_of(class),
                "{context}"
            ),
        }
        match (&ours.response, &theirs["response"]) {
            (None, Value::Null) => {}
            (Some(response), expected) if !expected.is_null() => check_usage(response, expected, &context),
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
    sylvia_base::testing::assert_same_json(&finish, &expected["finish_reason"], format!("finish, {context}"));
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
    let native = actual.anthropic_content.clone().map(Json::Array).unwrap_or(Json::Null);
    assert_eq!(dumps(&native), dumps(&expected["anthropic_content"]), "native content, {context}");
    assert!(actual.chat_completion_metadata.is_none(), "{context}");
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
            assert_eq!(error.retry_after, expected["retry_after"].as_f64(), "retry_after for {name}");
        }
        (expected, actual) => panic!("{name}: Python {expected}, Rust {actual:?}"),
    }
}

fn array(value: &Value) -> Vec<Json> {
    value.as_array().unwrap().clone()
}

fn kwargs_with_max_tokens(kwargs: &Value) -> JsonMap {
    let mut kwargs = kwargs.as_object().unwrap().clone();
    if !kwargs.contains_key("max_tokens") {
        kwargs.insert("max_tokens".into(), Json::from(4096));
    }
    kwargs
}

#[test]
fn format_request_matches_python() {
    let vectors = vectors();
    for case in vectors["requests"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let tools = array(&case["tools"]);
        let ours = anthropic::format_request(
            case["model"].as_str().unwrap(),
            case["messages"].as_array().unwrap(),
            Some(&tools),
            case["stream"].as_bool().unwrap(),
            &kwargs_with_max_tokens(&case["kwargs"]),
        )
        .map(|request| {
            if case["provider_key"] == "anthropic" {
                anthropic::with_cache_breakpoints(&request)
            } else {
                request
            }
        });
        match (ours, case.get("format_error")) {
            (Ok(request), None) => {
                assert_eq!(dumps(&Json::Object(request)), dumps(&case["format_request"]), "{name}")
            }
            (Err(sylvia_provider::chat::ChatError::Provider(error)), Some(expected)) => {
                assert_eq!(error.kind.class_name(), expected["class"].as_str().unwrap(), "{name}");
                assert_eq!(error.message, expected["message"].as_str().unwrap(), "{name}");
            }
            (ours, theirs) => panic!("{name}: Rust {ours:?}, Python {theirs:?}"),
        }
    }
}

#[test]
fn cache_breakpoints_match_python() {
    for case in vectors()["breakpoints"].as_array().unwrap() {
        let marked = anthropic::with_cache_breakpoints(case["request"].as_object().unwrap());
        assert_eq!(dumps(&Json::Object(marked)), dumps(&case["marked"]), "{}", case["request"]);
    }
}

#[tokio::test(start_paused = true)]
async fn requests_sent_through_the_provider_match_python() {
    let vectors = vectors();
    let stream_body = b"event: message_start\ndata: {\"type\": \"message_start\", \"message\": {\"id\": \"m\", \"type\": \"message\", \"role\": \"assistant\", \"content\": [], \"model\": \"claude-sonnet-5-5\", \"usage\": {\"input_tokens\": 1, \"output_tokens\": 1}}}\n\nevent: content_block_start\ndata: {\"type\": \"content_block_start\", \"index\": 0, \"content_block\": {\"type\": \"text\", \"text\": \"\"}}\n\nevent: content_block_delta\ndata: {\"type\": \"content_block_delta\", \"index\": 0, \"delta\": {\"type\": \"text_delta\", \"text\": \"x\"}}\n\nevent: content_block_stop\ndata: {\"type\": \"content_block_stop\", \"index\": 0}\n\nevent: message_delta\ndata: {\"type\": \"message_delta\", \"delta\": {\"stop_reason\": \"end_turn\"}, \"usage\": {\"output_tokens\": 2}}\n\nevent: message_stop\ndata: {\"type\": \"message_stop\"}\n\n";
    let message_body = br#"{"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5", "content": [{"type": "text", "text": "x"}], "stop_reason": "end_turn", "stop_sequence": null, "usage": {"input_tokens": 10, "output_tokens": 4}}"#;
    for case in vectors["requests"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let stream = case["stream"].as_bool().unwrap();
        let body = if stream { stream_body.to_vec() } else { message_body.to_vec() };
        let content_type = if stream { "text/event-stream" } else { "application/json" };
        let setup = Setup {
            base_url: case["base_url"].as_str().unwrap(),
            provider_key: case["provider_key"].as_str().unwrap(),
            api_key: case["api_key"].as_str().unwrap(),
            model: case["model"].as_str().unwrap(),
            headers: pairs(&case["headers"]),
        };
        let run = provider_for(
            &setup,
            reply_for(200, &serde_json::json!({"content-type": content_type}), vec![body]),
            RetryConfig { max_retries: 0, ..RetryConfig::default() },
        );
        let request = ProviderRequest {
            messages: array(&case["messages"]),
            tools: Some(array(&case["tools"])),
            stream: false,
            timeout: if stream { 120.0 } else { 60.0 },
            options: case["kwargs"].as_object().unwrap().clone(),
        };
        let error = if stream {
            let mut chunks = run.provider.generate_stream(request);
            let mut error = None;
            while let Some(item) = chunks.next().await {
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
        // The representation (through the trait, as the context fit reads it) is the request
        // the hooks saw, and the hooks are the provider's own.
        assert!(Provider::hooks(&run.provider).is_some_and(|hooks| std::ptr::eq(hooks, &run.provider.hooks)));
        let representation = Provider::request_representation(
            &run.provider,
            case["messages"].as_array().unwrap(),
            Some(&array(&case["tools"])),
            &{
                let mut options = case["kwargs"].as_object().unwrap().clone();
                options.insert("stream".into(), Json::Bool(stream));
                options
            },
        )
        .expect("the provider has provider_request_representation");
        match (representation, case.get("format_request")) {
            (Ok(request), Some(expected)) => {
                assert_eq!(dumps(&Json::Object(request)), dumps(expected), "representation of {name}")
            }
            (Err(_), None) => {}
            (ours, theirs) => panic!("{name}: representation {ours:?}, Python {theirs:?}"),
        }
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
        let run = provider_for(&Setup::default(), reply, retry);
        let mut options = JsonMap::new();
        options.insert("max_tokens".into(), Json::from(256));
        let request = ProviderRequest {
            messages: array(&vectors["messages"]),
            tools: Some(array(&vectors["tools"])),
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
                sylvia_base::testing::assert_same_json(
                    &Value::Array(calls),
                    &expected["tool_calls"],
                    name.to_string(),
                );
                sylvia_base::testing::assert_same_json(
                    &response.finish_reason.to_json(),
                    &expected["finish_reason"],
                    name.to_string(),
                );
                check_usage(response, expected, name);
                let native = response.anthropic_content.clone().map(Json::Array).unwrap_or(Json::Null);
                assert_eq!(
                    dumps(&native),
                    dumps(&expected["anthropic_content"]),
                    "native content for {name}"
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
        let run =
            provider_for(&Setup::default(), reply, RetryConfig { max_retries: 0, ..RetryConfig::default() });
        let request = ProviderRequest {
            messages: array(&vectors["messages"]),
            tools: Some(array(&vectors["tools"])),
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
fn the_model_table_is_the_locked_sdks() {
    let (sdk, pydantic) = anthropic::model_table_versions();
    assert_eq!(sdk, vectors()["anthropic"].as_str().unwrap());
    assert_eq!(pydantic, "2.13.4");
    assert_eq!(anthropic::TOOL_CHANGES_BETA, vectors()["tool_changes_beta"].as_str().unwrap());
}

/// `context_mode(transport="anthropic_messages", provider_key=...)` decides the cache
/// breakpoints: first-party Anthropic only, unless `SYLVIA_GM_CONTEXT_MODE` names `legacy`.
#[test]
fn the_context_mode_decides_the_cache_breakpoints() {
    let cases = [
        ("anthropic", None, true),
        ("anthropic", Some(" Legacy\n"), false),
        ("anthropic", Some("LEDGER"), true),
        ("anthropic", Some("something else"), true),
        ("byok", None, false),
        ("byok", Some("ledger"), false),
    ];
    for (provider_key, mode, marked) in cases {
        let mut info =
            ModelInfo::new("anthropic/claude-sonnet-5-5", "Model", "Anthropic", provider_key, "byok");
        info.transport = "anthropic_messages".into();
        let provider = AnthropicProvider::new(
            "https://api.anthropic.com/v1",
            "test-key-not-a-secret",
            info.post_init(),
            None,
            None,
            4096,
            Vec::new(),
            AnthropicEnvironment { custom_headers: None, context_mode: mode.map(str::to_owned) },
            Arc::new(ScriptedTransport::new(Vec::new())),
        )
        .unwrap();
        let request = provider
            .provider_request_representation(
                &[serde_json::json!({"role": "user", "content": "x"})],
                None,
                &JsonMap::new(),
            )
            .unwrap();
        assert_eq!(
            dumps(&Json::Object(request)).contains("cache_control"),
            marked,
            "{provider_key} {mode:?}"
        );
    }
}
