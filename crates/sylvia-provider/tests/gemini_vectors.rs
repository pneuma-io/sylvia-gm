//! The Rust `GeminiProvider` against the Python provider's recorded behaviour
//! (`vectors/generate_gemini_vectors.py`, lane 5). The same bytes go through the Rust provider
//! over the scripted transport, and everything observable is compared: the request body and
//! the HTTP request (URL, body bytes, headers), the chunks or response, the error (with a
//! continuation's private values), and the hooks fired.

use futures_util::StreamExt;
use serde_json::Value;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_provider::gemini::GeminiProvider;
use sylvia_provider::hooks::{HookContext, ProviderHook};
use sylvia_provider::http::{Piece, ScriptedReply, ScriptedTransport, TransportError};
use sylvia_provider::openai_compat::sylvia_user_agent;
use sylvia_provider::provider::BoxFuture;
use sylvia_provider::tool_delta::parse_tool_call_delta;
use sylvia_provider::types::{ModelInfo, ProviderResponse, ProviderStreamChunk, PyValue};
use sylvia_provider::{Provider, ProviderError, ProviderRequest};

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/gemini_vectors.json")).expect("gemini vectors")
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
    provider: GeminiProvider,
    transport: ScriptedTransport,
    recorder: Arc<Recorder>,
}

fn transport_error(kind: &str) -> TransportError {
    if kind == "timeout" {
        TransportError::Timeout("timed out".into())
    } else {
        TransportError::Connection("refused".into())
    }
}

/// The scripted reply for a scenario: a failure before any response, a response whose body fails
/// after its pieces, or a plain response.
fn reply_for(scenario: &Value, pieces: Vec<Vec<u8>>) -> ScriptedReply {
    let fail = &scenario["fail"];
    if fail["at"] == "send" {
        return ScriptedReply::Fail(transport_error(fail["kind"].as_str().unwrap()));
    }
    let mut body: Vec<Piece> = pieces.into_iter().map(Piece::Bytes).collect();
    if fail["at"] == "body" {
        body.push(Piece::Fail(transport_error(fail["kind"].as_str().unwrap())));
    }
    ScriptedReply::Respond {
        status: scenario["status"].as_u64().unwrap_or(200) as u16,
        headers: pairs(&scenario["response_headers"]),
        pieces: body,
    }
}

/// `UUID(int=n)` for n = 1, 2, ..., as the Python generator's counter writes them.
fn counter() -> sylvia_provider::gemini::CallIds {
    let next = AtomicU64::new(1);
    Arc::new(move || format!("00000000-0000-0000-0000-{:012x}", next.fetch_add(1, Ordering::SeqCst)))
}

fn provider_for(base_url: &str, model: &str, headers: Vec<(String, String)>, reply: ScriptedReply) -> Run {
    let transport = ScriptedTransport::new(vec![reply]);
    let mut info = ModelInfo::new(format!("test/{model}"), "Model", "Test Provider", "byok", "byok");
    info.transport = "gemini_generate_content".into();
    let provider = GeminiProvider::new(
        base_url,
        "test-key-not-a-secret",
        info.post_init(),
        headers,
        Arc::new(transport.clone()),
    )
    .with_call_ids(counter());
    let recorder = Arc::new(Recorder::default());
    provider.hooks.register(recorder.clone());
    Run { provider, transport, recorder }
}

/// Python's error class as Rust reports it: plain Python exceptions escape the provider as
/// they are, which is Rust's `Internal`.
fn class_of(python: &str) -> &str {
    match python {
        "TypeError" | "AttributeError" | "KeyError" | "ValueError" | "JSONDecodeError" => "Exception",
        other => other,
    }
}

/// Python's headers less httpx's own (`Host`, `Accept`, `Accept-Encoding`, `Connection`), with
/// Sylvia's `User-Agent` for httpx's (differences row 8).
fn expected_headers(python: &Value) -> Vec<(String, String)> {
    python
        .as_array()
        .unwrap()
        .iter()
        .map(|pair| (pair[0].as_str().unwrap().to_owned(), pair[1].as_str().unwrap().to_owned()))
        .filter(|(name, _)| {
            !matches!(
                name.to_ascii_lowercase().as_str(),
                "host" | "accept" | "accept-encoding" | "connection"
            )
        })
        .map(
            |(name, value)| {
                if value.starts_with("python-httpx/") {
                    (name, sylvia_user_agent())
                } else {
                    (name, value)
                }
            },
        )
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

fn check_response(response: &ProviderResponse, expected: &Value, context: &str) {
    assert_eq!(response.content.as_deref(), expected["content"].as_str(), "content, {context}");
    assert_eq!(
        response.reasoning_content.as_deref(),
        expected["reasoning_content"].as_str(),
        "reasoning, {context}"
    );
    let calls: Vec<Value> = response
        .tool_calls
        .iter()
        .map(|c| serde_json::json!([c.id.to_json(), c.name.to_json(), c.arguments.to_json()]))
        .collect();
    sylvia_base::testing::assert_same_json(
        &Value::Array(calls),
        &expected["tool_calls"],
        format!("calls, {context}"),
    );
    sylvia_base::testing::assert_same_json(
        &response.finish_reason.to_json(),
        &expected["finish_reason"],
        format!("finish, {context}"),
    );
    if let Some(model) = expected.get("model") {
        assert_eq!(response.model, model.as_str().unwrap(), "model, {context}");
    }
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
    let metadata = response.chat_completion_metadata.clone().map(Json::Object).unwrap_or(Json::Null);
    assert_eq!(dumps(&metadata), dumps(&expected["chat_completion_metadata"]), "metadata, {context}");
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
        assert_eq!(ours.stream, theirs["stream"].as_bool().unwrap(), "{context}");
        assert_eq!(ours.http_status.map(u64::from), theirs["http_status"].as_u64(), "{context}");
        let request = ours.request_json.clone().map(Json::Object).unwrap_or(Json::Null);
        assert_eq!(dumps(&request), dumps(&theirs["request_json"]), "request_json, {context}");
        assert!(ours.error.is_none() && theirs["error_type"].is_null(), "{context}");
        match (&ours.response, &theirs["response"]) {
            (None, Value::Null) => {}
            (Some(response), expected) if !expected.is_null() => check_response(response, expected, &context),
            (ours, theirs) => panic!("{context}: response {ours:?}, Python {theirs}"),
        }
    }
}

fn check_chunk(actual: &ProviderStreamChunk, expected: &Value, context: &str) {
    assert_eq!(actual.content, expected["content"].as_str().unwrap_or(""), "content, {context}");
    assert_eq!(
        actual.reasoning_content,
        expected["reasoning_content"].as_str().unwrap_or(""),
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
    let metadata = actual.chat_completion_metadata.clone().map(Json::Object).unwrap_or(Json::Null);
    assert_eq!(dumps(&metadata), dumps(&expected["chat_completion_metadata"]), "metadata, {context}");
}

fn check_error(error: Option<&ProviderError>, expected: &Value, name: &str) {
    match (expected, error) {
        (Value::Null, None) => {}
        (expected, Some(error)) if !expected.is_null() => {
            let class = expected["class"].as_str().unwrap();
            assert_eq!(error.kind.class_name(), class_of(class), "error class for {name}");
            assert_eq!(error.message, expected["message"].as_str().unwrap(), "error text for {name}");
            assert_eq!(error.retry_after, expected["retry_after"].as_f64(), "retry_after for {name}");
            match (&error.continuation, expected.get("continuation_token")) {
                (None, None) => {}
                (Some(continuation), Some(token)) => {
                    assert_eq!(continuation.continuation_token, token.as_str().unwrap(), "{name}");
                    assert_eq!(dumps(&continuation.native_parts), dumps(&expected["native_parts"]), "{name}");
                }
                (ours, theirs) => panic!("{name}: continuation {ours:?}, Python {theirs:?}"),
            }
        }
        (expected, actual) => panic!("{name}: Python {expected}, Rust {actual:?}"),
    }
}

fn array(value: &Value) -> Option<Vec<Json>> {
    value.as_array().cloned()
}

#[tokio::test(start_paused = true)]
async fn requests_match_python() {
    let vectors = vectors();
    for case in vectors["requests"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let stream = case["stream"].as_bool().unwrap();
        let body = if stream {
            b"data: {\"candidates\": [{\"content\": {\"parts\": [{\"text\": \"x\"}]}, \"finishReason\": \"STOP\"}]}\n\n".to_vec()
        } else {
            br#"{"candidates": [{"content": {"role": "model", "parts": [{"text": "The keeper nods."}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 4, "totalTokenCount": 15}}"#.to_vec()
        };
        let content_type = if stream { "text/event-stream" } else { "application/json" };
        let run = provider_for(
            case["base_url"].as_str().unwrap(),
            case["model"].as_str().unwrap(),
            pairs(&case["headers"]),
            ScriptedReply::ok(content_type, vec![body]),
        );
        let messages = array(&case["messages"]).unwrap();
        let tools = array(&case["tools"]);
        let options = case["kwargs"].as_object().unwrap().clone();
        // Through the trait, as the context fit reads it.
        assert!(Provider::hooks(&run.provider).is_some_and(|hooks| std::ptr::eq(hooks, &run.provider.hooks)));
        let representation =
            Provider::request_representation(&run.provider, &messages, tools.as_deref(), &options)
                .expect("the provider has provider_request_representation");
        match (representation, case.get("representation_error")) {
            (Ok(request), None) => {
                assert_eq!(
                    dumps(&Json::Object(request)),
                    dumps(&case["representation"]),
                    "representation of {name}"
                )
            }
            (Err(error), Some(expected)) => check_error(Some(&error), expected, name),
            (ours, theirs) => panic!("{name}: representation {ours:?}, Python {theirs:?}"),
        }
        let request = ProviderRequest { messages, tools, stream: false, timeout: 60.0, options };
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
    }
}

#[tokio::test(start_paused = true)]
async fn completions_match_python() {
    let vectors = vectors();
    for scenario in vectors["completions"].as_array().unwrap() {
        let name = scenario["name"].as_str().unwrap();
        let pieces =
            scenario["pieces"].as_array().unwrap().iter().map(|p| b64(p.as_str().unwrap())).collect();
        let run = provider_for(
            "https://standin.test/v1beta",
            "gemini-2.5-flash",
            Vec::new(),
            reply_for(scenario, pieces),
        );
        let mut options = JsonMap::new();
        options.insert("max_tokens".into(), Json::from(256));
        let request = ProviderRequest {
            messages: array(&vectors["messages"]).unwrap(),
            tools: array(&vectors["tools"]),
            stream: false,
            timeout: 60.0,
            options,
        };
        let outcome = run.provider.generate(request).await;
        match (&scenario["response"], &outcome) {
            (Value::Null, Err(_)) => {}
            (expected, Ok(response)) if !expected.is_null() => check_response(response, expected, name),
            (expected, outcome) => panic!("{name}: Python {expected}, Rust {outcome:?}"),
        }
        check_error(outcome.as_ref().err(), &scenario["error"], name);
        check_hooks(&run, scenario, name);
        check_requests(&run, scenario, name);
    }
}

#[tokio::test(start_paused = true)]
async fn streams_match_python() {
    let vectors = vectors();
    for scenario in vectors["streams"].as_array().unwrap() {
        let name = scenario["name"].as_str().unwrap();
        let pieces =
            scenario["pieces"].as_array().unwrap().iter().map(|p| b64(p.as_str().unwrap())).collect();
        let run = provider_for(
            "https://standin.test/v1beta",
            "gemini-2.5-flash",
            Vec::new(),
            reply_for(scenario, pieces),
        );
        let request = ProviderRequest {
            messages: array(&vectors["messages"]).unwrap(),
            tools: array(&vectors["tools"]),
            stream: scenario["generate"].as_bool().unwrap(),
            timeout: 30.0,
            options: JsonMap::new(),
        };
        let mut chunks = Vec::new();
        let mut error = None;
        if scenario["generate"].as_bool().unwrap() {
            match run.provider.generate(request).await {
                Ok(response) => check_response(&response, &scenario["response"], name),
                Err(failure) => error = Some(failure),
            }
        } else {
            let mut stream = run.provider.generate_stream(request);
            while let Some(item) = stream.next().await {
                match item {
                    Ok(chunk) => chunks.push(chunk),
                    Err(failure) => error = Some(failure),
                }
            }
        }
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
fn a_random_call_id_is_a_version_4_uuid() {
    let id = sylvia_provider::gemini::uuid4();
    let groups: Vec<&str> = id.split('-').collect();
    assert_eq!(groups.iter().map(|group| group.len()).collect::<Vec<_>>(), [8, 4, 4, 4, 12], "{id}");
    assert!(groups[2].starts_with('4'), "{id}");
    assert!(matches!(&groups[3][..1], "8" | "9" | "a" | "b"), "{id}");
    assert_ne!(id, sylvia_provider::gemini::uuid4());
}

/// Python's Gemini client is a plain `httpx.AsyncClient(follow_redirects=False)`, so the
/// factory builds it from its no-redirect builder, never the SDK providers' one.
#[test]
fn the_factory_gives_gemini_the_plain_client() {
    use sylvia_provider::factory::{BuiltProvider, ClientBuilder, DefaultProviderFactory};
    use sylvia_provider::http::HttpTransport;
    use sylvia_provider::openai_compat::SdkEnvironment;
    use sylvia_provider::types::{ProviderConfig, TimeoutConfig};
    let sdk: Arc<ClientBuilder> = Arc::new(|_: &TimeoutConfig| panic!("the SDK providers' client builder"));
    let built = Arc::new(Mutex::new(Vec::new()));
    let seen = built.clone();
    let plain: Arc<ClientBuilder> = Arc::new(move |timeouts: &TimeoutConfig| {
        seen.lock().unwrap().push(timeouts.clone());
        Ok(Arc::new(ScriptedTransport::new(Vec::new())) as Arc<dyn HttpTransport>)
    });
    let factory = DefaultProviderFactory::new(sdk, SdkEnvironment::default()).with_plain_client(plain);
    let config = ProviderConfig::new("gemini", "gemini_generate_content", "https://standin.test/v1beta");
    assert!(matches!(factory.build(config), Ok(BuiltProvider::Gemini(_))));
    assert_eq!(*built.lock().unwrap(), vec![sylvia_provider::factory::GEMINI_CLIENT_TIMEOUTS]);
}
