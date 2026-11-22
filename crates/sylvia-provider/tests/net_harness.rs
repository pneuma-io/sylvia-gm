//! The network transport over a real loopback socket.
//!
//! A raw TCP server records each request exactly as it arrives. The ruleless-turn requests go out
//! through `OpenAICompatProvider` and `NetworkTransport`, and their bytes are compared with what
//! the Python SDK sent for the same messages and tools (`chat_vectors.json`, captured through an
//! httpx mock): the body byte for byte, and every header Sylvia or the protocol sets. The other
//! tests cover proxy routing, header decoding and the cookie jar.

use futures_util::StreamExt;
use serde_json::Value;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::thread;
use sylvia_base::JsonMap;
use sylvia_provider::net::{ClientSettings, NetworkTransport, TrustSource};
use sylvia_provider::openai_compat::{sylvia_user_agent, OpenAICompatProvider, SdkEnvironment};
use sylvia_provider::proxy::ProxySource;
use sylvia_provider::types::{ModelInfo, RetryConfig, TimeoutConfig};
use sylvia_provider::{ErrorKind, Provider, ProviderRequest};

const SSE_OK: &str =
    "data: {\"choices\":[{\"delta\":{\"content\":\"Hi\"},\"finish_reason\":\"stop\"}]}\n\ndata: [DONE]\n\n";

/// One recorded request: the head as text and the body bytes.
#[derive(Debug, Clone)]
struct Seen {
    head: String,
    body: Vec<u8>,
}

impl Seen {
    fn request_line(&self) -> &str {
        self.head.lines().next().unwrap_or("")
    }

    fn headers(&self) -> Vec<(String, String)> {
        self.head
            .lines()
            .skip(1)
            .filter(|line| !line.is_empty())
            .map(|line| {
                let (name, value) = line.split_once(':').unwrap();
                (name.to_ascii_lowercase(), value.trim_start().to_owned())
            })
            .collect()
    }
}

/// A server that answers each connection's requests with the next scripted reply.
struct Server {
    port: u16,
    seen: Arc<Mutex<Vec<Seen>>>,
}

fn read_request(stream: &mut TcpStream) -> Option<Seen> {
    let mut data = Vec::new();
    let mut buffer = [0u8; 8192];
    let head_end = loop {
        if let Some(end) = data.windows(4).position(|w| w == b"\r\n\r\n") {
            break end;
        }
        let read = stream.read(&mut buffer).ok()?;
        if read == 0 {
            return None;
        }
        data.extend_from_slice(&buffer[..read]);
    };
    let head = String::from_utf8(data[..head_end].to_vec()).unwrap();
    let length: usize = head
        .lines()
        .find_map(|line| {
            line.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse().unwrap())
        })
        .unwrap_or(0);
    let mut body = data[head_end + 4..].to_vec();
    while body.len() < length {
        let read = stream.read(&mut buffer).ok()?;
        if read == 0 {
            break;
        }
        body.extend_from_slice(&buffer[..read]);
    }
    Some(Seen { head, body })
}

impl Server {
    fn start(replies: Vec<Vec<u8>>) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let seen = Arc::new(Mutex::new(Vec::new()));
        let record = Arc::clone(&seen);
        thread::spawn(move || {
            let mut replies = replies.into_iter();
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { break };
                // Keep-alive: answer requests on this connection until it closes.
                while let Some(request) = read_request(&mut stream) {
                    record.lock().unwrap().push(request);
                    let Some(reply) = replies.next() else { return };
                    if stream.write_all(&reply).is_err() {
                        break;
                    }
                }
            }
        });
        Self { port, seen }
    }

    fn seen(&self) -> Vec<Seen> {
        self.seen.lock().unwrap().clone()
    }
}

fn reply(status: &str, headers: &[(&str, &[u8])], body: &str) -> Vec<u8> {
    let mut out = format!("HTTP/1.1 {status}\r\ncontent-length: {}\r\n", body.len()).into_bytes();
    for (name, value) in headers {
        out.extend_from_slice(name.as_bytes());
        out.extend_from_slice(b": ");
        out.extend_from_slice(value);
        out.extend_from_slice(b"\r\n");
    }
    out.extend_from_slice(b"\r\n");
    out.extend_from_slice(body.as_bytes());
    out
}

fn sse_reply(extra: &[(&str, &[u8])]) -> Vec<u8> {
    let mut headers: Vec<(&str, &[u8])> = vec![("content-type", b"text/event-stream")];
    headers.extend_from_slice(extra);
    reply("200 OK", &headers, SSE_OK)
}

fn transport(environment: Vec<(String, String)>) -> Arc<NetworkTransport> {
    let settings = ClientSettings {
        timeouts: TimeoutConfig::default(),
        proxies: ProxySource { environment, windows: false, registry: None },
        trust: TrustSource::Bundled,
        follow_redirects: true,
    };
    Arc::new(NetworkTransport::new(settings).expect("client builds"))
}

fn provider(base_url: &str, model: &str, transport: Arc<NetworkTransport>) -> OpenAICompatProvider {
    OpenAICompatProvider::new(
        base_url,
        Some("standin-key"),
        Some(ModelInfo::new(format!("standin/{model}"), "Stand-in", "Stand-in", "byok", "custom")),
        Some(RetryConfig { max_retries: 0, ..RetryConfig::default() }),
        None,
        Vec::new(),
        SdkEnvironment::default(),
        transport,
    )
    .unwrap()
}

async fn stream_all(
    provider: &OpenAICompatProvider,
    request: ProviderRequest,
) -> Vec<sylvia_provider::ProviderResult<sylvia_provider::ProviderStreamChunk>> {
    provider.generate_stream(request).collect().await
}

#[tokio::test]
async fn ruleless_turn_requests_go_out_as_python_sent_them() {
    let vectors: Value = serde_json::from_str(include_str!("vectors/chat_vectors.json")).unwrap();
    let cases: Vec<&Value> = vectors["requests"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|case| case.get("recorded_body").is_some())
        .collect();
    assert_eq!(cases.len(), 2);
    let server = Server::start(cases.iter().map(|_| sse_reply(&[])).collect());
    let base = format!("http://127.0.0.1:{}/v1", server.port);
    let shared = transport(Vec::new());
    let provider = provider(&base, cases[0]["model"].as_str().unwrap(), shared);
    for case in &cases {
        let request = ProviderRequest {
            messages: case["messages"].as_array().unwrap().clone(),
            tools: Some(case["tools"].as_array().unwrap().clone()),
            stream: false,
            timeout: 30.0,
            options: case["kwargs"].as_object().unwrap().clone(),
        };
        let items = stream_all(&provider, request).await;
        assert!(items.iter().all(Result::is_ok), "{items:?}");
    }
    let seen = server.seen();
    assert_eq!(seen.len(), 2);
    for (ours, case) in seen.iter().zip(&cases) {
        let wire = &case["wire"];
        assert_eq!(ours.request_line(), "POST /v1/chat/completions HTTP/1.1");
        // The body, byte for byte.
        assert_eq!(ours.body, wire["body"].as_str().unwrap().as_bytes(), "body of {}", case["name"]);
        // Headers: Python's set minus differences row 8, names compared without case, plus the
        // two the HTTP client writes.
        let mut expected: Vec<(String, String)> = wire["headers"]
            .as_array()
            .unwrap()
            .iter()
            .map(|pair| {
                (pair[0].as_str().unwrap().to_ascii_lowercase(), pair[1].as_str().unwrap().to_owned())
            })
            .filter(|(name, _)| {
                !name.starts_with("x-stainless") && !matches!(name.as_str(), "accept-encoding" | "connection")
            })
            .map(|(name, value)| match name.as_str() {
                "host" => (name, format!("127.0.0.1:{}", server.port)),
                "authorization" => (name, "Bearer standin-key".into()),
                "user-agent" => (name, sylvia_user_agent()),
                _ => (name, value),
            })
            .collect();
        let mut actual = ours.headers();
        assert_eq!(
            actual.iter().find(|(n, _)| n == "content-length").map(|(_, v)| v.clone()),
            Some(ours.body.len().to_string())
        );
        // hyper writes host and content-length where it chooses; everything else keeps the SDK order.
        let fixed =
            |list: &mut Vec<(String, String)>| list.retain(|(n, _)| n != "host" && n != "content-length");
        let host_ours = actual.iter().find(|(n, _)| n == "host").cloned();
        let host_theirs = expected.iter().find(|(n, _)| n == "host").cloned();
        assert_eq!(host_ours, host_theirs);
        fixed(&mut actual);
        fixed(&mut expected);
        assert_eq!(actual, expected, "headers of {}", case["name"]);
    }
}

#[tokio::test]
async fn proxy_requests_use_the_absolute_form() {
    let server = Server::start(vec![sse_reply(&[])]);
    let proxy = format!("http://127.0.0.1:{}", server.port);
    let shared = transport(vec![("HTTP_PROXY".into(), proxy)]);
    let provider = provider("http://model.invalid/v1", "m", shared);
    let request = ProviderRequest::new(vec![serde_json::json!({"role": "user", "content": "hi"})]);
    let items = stream_all(&provider, request).await;
    assert!(items.iter().all(Result::is_ok), "{items:?}");
    let seen = server.seen();
    assert_eq!(seen[0].request_line(), "POST http://model.invalid/v1/chat/completions HTTP/1.1");
}

#[tokio::test]
async fn excluded_hosts_skip_the_proxy() {
    let server = Server::start(vec![sse_reply(&[])]);
    let shared = transport(vec![
        ("HTTP_PROXY".into(), "http://127.0.0.1:9".into()),
        ("NO_PROXY".into(), "127.0.0.1".into()),
    ]);
    let provider = provider(&format!("http://127.0.0.1:{}/v1", server.port), "m", shared);
    let items = stream_all(
        &provider,
        ProviderRequest::new(vec![serde_json::json!({"role": "user", "content": "x"})]),
    )
    .await;
    assert!(items.iter().all(Result::is_ok), "{items:?}");
    assert_eq!(server.seen()[0].request_line(), "POST /v1/chat/completions HTTP/1.1");
}

#[tokio::test]
async fn utf8_header_values_decode_as_httpx_does() {
    let digit = "\u{665}".as_bytes();
    let server = Server::start(vec![reply("429 Too Many Requests", &[("retry-after", digit)], "{}")]);
    let provider = provider(&format!("http://127.0.0.1:{}/v1", server.port), "m", transport(Vec::new()));
    let items = stream_all(
        &provider,
        ProviderRequest::new(vec![serde_json::json!({"role": "user", "content": "x"})]),
    )
    .await;
    let error = items[0].as_ref().unwrap_err();
    assert_eq!(error.kind, ErrorKind::RateLimit);
    assert_eq!(error.retry_after, Some(5.0));
}

#[tokio::test]
async fn cookies_are_kept_between_requests() {
    let server = Server::start(vec![sse_reply(&[("set-cookie", b"__cf_bm=abc; Path=/")]), sse_reply(&[])]);
    let provider = provider(&format!("http://127.0.0.1:{}/v1", server.port), "m", transport(Vec::new()));
    for _ in 0..2 {
        let mut options = JsonMap::new();
        options.insert("max_tokens".into(), serde_json::json!(5));
        let request = ProviderRequest {
            options,
            ..ProviderRequest::new(vec![serde_json::json!({"role": "user", "content": "x"})])
        };
        let items = stream_all(&provider, request).await;
        assert!(items.iter().all(Result::is_ok), "{items:?}");
    }
    let seen = server.seen();
    assert!(seen[0].headers().iter().all(|(name, _)| name != "cookie"));
    assert!(seen[1].headers().contains(&("cookie".into(), "__cf_bm=abc".into())), "{:?}", seen[1].headers());
}
