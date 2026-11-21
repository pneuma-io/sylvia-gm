//! Compare the Chat Completions transport with vectors written by the frozen Python source.
//!
//! Regenerate with `tests/vectors/generate_chat_vectors.py` (needs the snapshot's Python). The
//! streams there are the real `OpenAICompatProvider.generate_stream` reading the given bytes;
//! here the same bytes go through the pure pipeline (SSE decoding, the `[DONE]` tracker, chunk
//! parsing and the per-stream state). The HTTP, hook and retry parts are checked with the
//! provider itself.

use serde_json::Value;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_provider::chat::{
    self, format_request, parse_completion, preserve_flags, sdk_request, status_error, status_error_message,
    ChatCompletionMetadata, ChatError, ChatStreamState, InlineReasoningFilter,
};
use sylvia_provider::pyobj::models;
use sylvia_provider::sse::{stream_item, DoneTracker, SseDecoder, StreamItem};
use sylvia_provider::tool_delta::{delta_value, parse_tool_call_delta};
use sylvia_provider::types::{ProviderStreamChunk, PyValue, RetryConfig};
use sylvia_provider::{ErrorKind, ProviderError};

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/chat_vectors.json")).expect("vector file is valid JSON")
}

fn b64(text: &str) -> Vec<u8> {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = Vec::new();
    let mut buffer = 0u32;
    let mut bits = 0;
    for byte in text.bytes().filter(|b| *b != b'=') {
        let value = ALPHABET.iter().position(|c| *c == byte).expect("base64 digit") as u32;
        buffer = (buffer << 6) | value;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((buffer >> bits) as u8);
            buffer &= (1 << bits) - 1;
        }
    }
    out
}

fn kind_name(error: &ProviderError) -> &'static str {
    error.kind.class_name()
}

#[test]
fn sdk_model_fields_match_the_sdk() {
    let vectors = vectors();
    let sdk = &vectors["sdk_models"];
    let ours: [(&str, &[&str]); 14] = [
        ("chunk", models::CHUNK),
        ("chunk_choice", models::CHUNK_CHOICE),
        ("delta", models::DELTA),
        ("delta_tool_call", models::DELTA_TOOL_CALL),
        ("delta_function", models::DELTA_FUNCTION),
        ("usage", models::USAGE),
        ("completion_details", models::COMPLETION_DETAILS),
        ("prompt_details", models::PROMPT_DETAILS),
        ("completion", models::COMPLETION),
        ("completion_choice", models::COMPLETION_CHOICE),
        ("message", models::MESSAGE),
        ("function_tool_call", models::FUNCTION_TOOL_CALL),
        ("function", models::FUNCTION),
        ("custom_tool_call", models::CUSTOM_TOOL_CALL),
    ];
    for (name, fields) in ours {
        let expected: Vec<&str> = sdk[name].as_array().unwrap().iter().map(|v| v.as_str().unwrap()).collect();
        assert_eq!(fields, expected.as_slice(), "declared fields of {name}");
    }
}

#[test]
fn requests_match_python_bytes() {
    let vectors = vectors();
    let mut ruleless = 0;
    for case in vectors["requests"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let (messages, tools) = match case.get("messages") {
            Some(messages) => {
                (messages.as_array().unwrap().clone(), case["tools"].as_array().unwrap().clone())
            }
            None => {
                let stream = &vectors["streams"][0];
                (stream["messages"].as_array().unwrap().clone(), tools_of(&vectors))
            }
        };
        let kwargs = case["kwargs"].as_object().unwrap();
        let request = format_request(
            case["model"].as_str().unwrap(),
            &messages,
            Some(&tools),
            case["stream"].as_bool().unwrap(),
            kwargs,
        );
        // Key order matters (hooks and the audit see this dict): compare encoded text.
        assert_eq!(
            pyjson::dumps(&Json::Object(request.clone()), Dump::DEFAULT),
            pyjson::dumps(&case["format_request"], Dump::DEFAULT),
            "format_request for {name}"
        );
        let sdk = sdk_request(&request).unwrap();
        let wire = &case["wire"];
        assert_eq!(
            String::from_utf8(sdk.body_bytes()).unwrap(),
            wire["body"].as_str().unwrap(),
            "body bytes for {name}"
        );
        assert_eq!(wire["method"], "POST");
        if let Some(recorded) = case.get("recorded_body") {
            // The comparison recording keeps the body parsed; order and values must agree too.
            assert_eq!(
                pyjson::dumps(&Json::Object(sdk.body.clone()), Dump::DEFAULT),
                pyjson::dumps(recorded, Dump::DEFAULT)
            );
            ruleless += 1;
        }
    }
    assert_eq!(ruleless, 2, "both ruleless-turn requests checked");
}

fn tools_of(vectors: &Value) -> Vec<Json> {
    let request = &vectors["requests"][0]["format_request"];
    request["tools"].as_array().unwrap().clone()
}

#[test]
fn unknown_keyword_is_a_type_error() {
    let mut request = JsonMap::new();
    request.insert("messages".into(), serde_json::json!([]));
    request.insert("bogus".into(), serde_json::json!(1));
    let error = sdk_request(&request).unwrap_err();
    assert_eq!(error.0, "AsyncCompletions.create() got an unexpected keyword argument 'bogus'");
}

#[test]
fn inline_filter_matches_python() {
    for case in vectors()["inline_filter"].as_array().unwrap() {
        let mut filter = InlineReasoningFilter::new();
        let pieces = case["pieces"].as_array().unwrap();
        let results = case["results"].as_array().unwrap();
        for (piece, expected) in pieces.iter().zip(results) {
            let (visible, private) = filter.feed(piece.as_str().unwrap(), false);
            assert_eq!(
                [visible.as_str(), private.as_str()],
                [expected[0].as_str().unwrap(), expected[1].as_str().unwrap()],
                "{case}"
            );
        }
        let (visible, private) = filter.feed("", true);
        let last = results.last().unwrap();
        assert_eq!(
            [visible.as_str(), private.as_str()],
            [last[0].as_str().unwrap(), last[1].as_str().unwrap()],
            "final {case}"
        );
    }
}

fn error_json(error: &ChatError) -> (String, String) {
    match error {
        ChatError::Provider(error) => (kind_name(error).to_owned(), error.message.clone()),
        ChatError::Incidental(incidental) => ("Incidental".to_owned(), incidental.0.clone()),
    }
}

#[test]
fn metadata_matches_python() {
    for case in vectors()["metadata"].as_array().unwrap() {
        let mut metadata = ChatCompletionMetadata::new(
            case["preserve_reasoning"].as_bool().unwrap_or(false),
            case["preserve_content"].as_bool().unwrap_or(false),
        );
        let outcome = case["deltas"]
            .as_array()
            .unwrap()
            .iter()
            .try_for_each(|delta| metadata.add(delta, models::DELTA))
            .and_then(|()| metadata.payload());
        match (&case["error"], outcome) {
            (Value::Null, Ok(payload)) => {
                let payload = payload.map(Json::Object).unwrap_or(Json::Null);
                assert_eq!(
                    pyjson::dumps(&payload, Dump::DEFAULT),
                    pyjson::dumps(&case["payload"], Dump::DEFAULT),
                    "{case}"
                );
            }
            (Value::Null, Err(error)) => panic!("Rust failed where Python did not: {error:?} for {case}"),
            (expected, Ok(payload)) => panic!("Python raised {expected} but Rust gave {payload:?}"),
            (expected, Err(error)) => {
                let (class, message) = error_json(&error);
                assert_eq!(class, expected["class"].as_str().unwrap(), "{case}");
                assert_eq!(message, expected["message"].as_str().unwrap(), "{case}");
            }
        }
    }
}

/// The pure part of `generate_stream` over the scenario's bytes.
fn run_stream(scenario: &Value) -> (Vec<ProviderStreamChunk>, Option<ProviderError>) {
    let pieces: Vec<Vec<u8>> =
        scenario["pieces"].as_array().unwrap().iter().map(|p| b64(p.as_str().unwrap())).collect();
    let status = scenario["status"].as_u64().unwrap() as u16;
    if status != 200 {
        let body: Vec<u8> = pieces.concat();
        let message = status_error_message(status, &body);
        let header = scenario["response_headers"]["retry-after"].as_str();
        return (Vec::new(), Some(status_error(status, &message, header, None)));
    }
    let host = scenario["base_url"].as_str().unwrap().split('/').nth(2).map(str::to_owned);
    let (reasoning, content) = preserve_flags(host.as_deref(), scenario["provider_key"].as_str().unwrap());
    let mut state = ChatStreamState::new(reasoning, content);
    let mut decoder = SseDecoder::new();
    let mut tracker = DoneTracker::default();
    let mut chunks = Vec::new();
    let mut run = || -> Result<(), ChatError> {
        let mut events = Vec::new();
        'pieces: for piece in &pieces {
            tracker.observe(piece);
            events.extend(decoder.feed(piece)?);
            for event in events.drain(..) {
                match stream_item(&event)? {
                    StreamItem::Done => break 'pieces,
                    StreamItem::Chunk(raw) => chunks.extend(state.on_chunk(&raw)?),
                }
            }
            if pieces.last() == Some(piece) {
                for event in decoder.finish()? {
                    match stream_item(&event)? {
                        StreamItem::Done => break 'pieces,
                        StreamItem::Chunk(raw) => chunks.extend(state.on_chunk(&raw)?),
                    }
                }
            }
        }
        chunks.extend(state.finish(tracker.done)?);
        Ok(())
    };
    let error = run().err().map(ChatError::into_provider_error);
    (chunks, error)
}

fn check_chunk(actual: &ProviderStreamChunk, expected: &Value, context: &str) {
    assert_eq!(actual.content, expected["content"].as_str().unwrap(), "content, {context}");
    assert_eq!(
        actual.reasoning_content,
        expected["reasoning_content"].as_str().unwrap(),
        "reasoning, {context}"
    );
    // Python keeps the provider's value, `7` included.
    let finish = actual.finish_reason.as_ref().map(PyValue::to_json).unwrap_or(Value::Null);
    assert_eq!(finish, expected["finish_reason"], "finish, {context}");
    match (&actual.tool_call_delta, &expected["tool_call_delta"]) {
        (None, Value::Null) => {}
        (Some(delta), expected) if !expected.is_null() => {
            let parsed = parse_tool_call_delta(delta);
            let want = &expected["parsed"];
            assert_eq!(parsed.index as u64, want[0].as_u64().unwrap(), "index, {context}");
            assert_eq!(parsed.call_id, want[1].as_str().unwrap(), "id, {context}");
            assert_eq!(parsed.name, want[2].as_str().unwrap(), "name, {context}");
            // Fragments stay the exact strings received.
            assert_eq!(parsed.arguments, want[3].as_str().unwrap(), "arguments, {context}");
            let extra = delta_value(delta, "extra_content").cloned().unwrap_or(Json::Null);
            assert_eq!(extra, expected["extra_content"], "extra content, {context}");
        }
        (actual, expected) => panic!("tool delta {actual:?} where Python had {expected}, {context}"),
    }
    let metadata = actual.chat_completion_metadata.clone().map(Json::Object).unwrap_or(Json::Null);
    assert_eq!(
        pyjson::dumps(&metadata, Dump::DEFAULT),
        pyjson::dumps(&expected["chat_completion_metadata"], Dump::DEFAULT),
        "metadata, {context}"
    );
}

/// Scenarios where Python's message comes from an exception whose text is not reproduced (it is
/// never stored or shown; the owner replaces it).
const INCIDENTAL_TEXT: [&str; 1] = ["malformed-json"];

#[test]
fn streams_match_python() {
    let vectors = vectors();
    let scenarios = vectors["streams"].as_array().unwrap();
    assert!(scenarios.len() >= 40);
    for scenario in scenarios {
        let name = scenario["name"].as_str().unwrap();
        let (chunks, error) = run_stream(scenario);
        let expected = scenario["chunks"].as_array().unwrap();
        assert_eq!(chunks.len(), expected.len(), "chunk count for {name}: {chunks:?}");
        for (position, (actual, want)) in chunks.iter().zip(expected).enumerate() {
            check_chunk(actual, want, &format!("{name} chunk {position}"));
        }
        match (&scenario["error"], &error) {
            (Value::Null, None) => {}
            (expected, Some(error)) if !expected.is_null() => {
                assert_eq!(kind_name(error), expected["class"].as_str().unwrap(), "error class for {name}");
                if !INCIDENTAL_TEXT.contains(&name) {
                    assert_eq!(error.message, expected["message"].as_str().unwrap(), "error text for {name}");
                }
                assert_eq!(error.retry_after, expected["retry_after"].as_f64(), "retry_after for {name}");
            }
            (expected, actual) => panic!("{name}: Python error {expected}, Rust {actual:?}"),
        }
    }
}

#[test]
fn completions_match_python() {
    let vectors = vectors();
    let retryable = RetryConfig::default().retryable_statuses;
    for scenario in vectors["completions"].as_array().unwrap() {
        let name = scenario["name"].as_str().unwrap();
        let body = b64(scenario["body"].as_str().unwrap());
        let status = scenario["status"].as_u64().unwrap() as u16;
        let outcome = if status == 200 {
            let completion = pyjson::loads(std::str::from_utf8(&body).unwrap()).unwrap();
            let (reasoning, content) =
                preserve_flags(Some("standin.test"), scenario["provider_key"].as_str().unwrap());
            parse_completion(&completion, "test/model-a", reasoning, content)
                .map_err(ChatError::into_provider_error)
        } else {
            let message = status_error_message(status, &body);
            Err(status_error(
                status,
                &message,
                scenario["response_headers"]["retry-after"].as_str(),
                Some(&retryable),
            ))
        };
        match (&scenario["error"], outcome) {
            (Value::Null, Ok(response)) => {
                let want = &scenario["response"];
                assert_eq!(response.content.as_deref(), want["content"].as_str(), "{name}");
                assert_eq!(
                    response.reasoning_content.as_deref(),
                    want["reasoning_content"].as_str(),
                    "{name}"
                );
                let calls: Vec<Value> = response
                    .tool_calls
                    .iter()
                    .map(|c| serde_json::json!([c.id.to_json(), c.name.to_json(), c.arguments.to_json()]))
                    .collect();
                // Python keeps a missing id, name or arguments as None.
                assert_eq!(Value::Array(calls), want["tool_calls"], "{name}");
                let usage = &response.usage;
                assert_eq!(
                    serde_json::json!([
                        usage.prompt_tokens.to_json(),
                        usage.completion_tokens.to_json(),
                        usage.total_tokens.to_json(),
                        usage.reasoning_tokens
                    ]),
                    want["usage"],
                    "{name}"
                );
                assert_eq!(response.usage_reported, want["usage_reported"].as_bool(), "{name}");
                assert_eq!(response.finish_reason.to_json(), want["finish_reason"], "{name}");
                let cache = &response.cache_usage;
                assert_eq!(
                    serde_json::json!([
                        cache.cache_read_tokens,
                        cache.cache_write_tokens,
                        cache.cache_miss_tokens,
                        cache.total_prompt_tokens
                    ]),
                    want["cache"],
                    "{name}"
                );
                let metadata =
                    response.chat_completion_metadata.clone().map(Json::Object).unwrap_or(Json::Null);
                assert_eq!(metadata, want["chat_completion_metadata"], "{name}");
            }
            (expected, Err(error)) if !expected.is_null() => {
                assert_eq!(kind_name(&error), expected["class"].as_str().unwrap(), "{name}");
                assert_eq!(error.message, expected["message"].as_str().unwrap(), "{name}");
                assert_eq!(error.retry_after, expected["retry_after"].as_f64(), "{name}");
            }
            (expected, actual) => panic!("{name}: Python {expected}, Rust {actual:?}"),
        }
    }
    let _ = ErrorKind::ALL;
    let _ = chat::CREATE_PARAMETERS;
}

#[test]
fn usage_sums_outside_64_bits_are_refused() {
    use sylvia_provider::chat::stream_usage;
    use sylvia_provider::pyobj::COUNT_OVERFLOW;
    // Python carries on with unbounded integers; Rust refuses (the integer-overflow row of differences.md).
    assert_eq!(stream_usage(&serde_json::json!({"prompt_tokens": 1e30})).unwrap_err().0, COUNT_OVERFLOW);
    // A JSON object is the SDK's usage model, which declares total_tokens: a missing total reads
    // as None and becomes 0, as in Python, so the prompt + completion sum is never computed.
    let missing_total = serde_json::json!({"prompt_tokens": i64::MAX, "completion_tokens": 1});
    assert_eq!(stream_usage(&missing_total).unwrap().0.total_tokens, PyValue::int(0));
    let negative_prompt =
        serde_json::json!({"prompt_tokens": i64::MIN, "cached_tokens": 5, "total_tokens": 1});
    assert_eq!(stream_usage(&negative_prompt).unwrap_err().0, COUNT_OVERFLOW);
}
