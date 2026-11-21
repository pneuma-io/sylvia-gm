//! The HTTP seam under the OpenAI-compatible provider, and a scripted transport for tests.
//!
//! The provider builds an [`HttpRequest`] (method, URL, headers in order, body bytes) and reads
//! an [`HttpResponse`] whose body arrives piece by piece, exactly as the network delivered it
//! (the SSE decoder and the `[DONE]` tracker depend on the pieces). The real client implements
//! [`HttpTransport`] over the network; [`ScriptedTransport`] replays recorded responses,
//! including failures, slow pieces and pieces that never arrive, so streaming, cancellation and
//! retries can be tested without a server.

use crate::provider::BoxFuture;
use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// A request as it goes on the wire.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpRequest {
    pub method: String,
    pub url: String,
    /// Header names and values in the order they are sent.
    pub headers: Vec<(String, String)>,
    pub body: Vec<u8>,
}

/// Why a request or a body read failed below HTTP. The provider maps these the way the OpenAI
/// SDK maps httpx's exceptions (`APITimeoutError`, `APIConnectionError`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TransportError {
    /// Connect, write, read or pool timeout.
    Timeout(String),
    /// Refused, reset, DNS, TLS or a broken body.
    Connection(String),
}

/// A pull-based response body: the next piece, or `None` at the end.
pub trait BodyStream: Send {
    /// The next piece of bytes as received.
    fn next_piece(&mut self) -> BoxFuture<'_, Option<Result<Vec<u8>, TransportError>>>;
}

/// A response with its status, headers and body still to be read.
pub struct HttpResponse {
    pub status: u16,
    /// Header names and values as received.
    pub headers: Vec<(String, String)>,
    pub body: Box<dyn BodyStream>,
}

impl HttpResponse {
    /// The first value of a header, compared without case.
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(key, _)| key.eq_ignore_ascii_case(name)).map(|(_, value)| value.as_str())
    }

    /// Read the whole body.
    pub async fn read_all(&mut self) -> Result<Vec<u8>, TransportError> {
        let mut body = Vec::new();
        while let Some(piece) = self.body.next_piece().await {
            body.extend(piece?);
        }
        Ok(body)
    }
}

impl std::fmt::Debug for HttpResponse {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("HttpResponse")
            .field("status", &self.status)
            .field("headers", &self.headers)
            .finish_non_exhaustive()
    }
}

/// Something that can send one request.
pub trait HttpTransport: Send + Sync {
    /// Send `request` and return once the status and headers have arrived.
    fn send(&self, request: HttpRequest) -> BoxFuture<'_, Result<HttpResponse, TransportError>>;
}

/// One step of a scripted response body.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Piece {
    /// Bytes delivered as one read.
    Bytes(Vec<u8>),
    /// Wait this long before the next step (tokio time, so paused-time tests run instantly).
    Delay(Duration),
    /// Never deliver anything more: for inactivity timeouts and cancellation.
    Hold,
    /// The connection fails here.
    Fail(TransportError),
}

/// One scripted reply to one request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ScriptedReply {
    /// A response with this status, headers and body script.
    Respond { status: u16, headers: Vec<(String, String)>, pieces: Vec<Piece> },
    /// The request fails before any response.
    Fail(TransportError),
    /// The request never gets a response.
    Hold,
}

impl ScriptedReply {
    /// A 200 response delivering `pieces` as separate reads.
    pub fn ok(content_type: &str, pieces: Vec<Vec<u8>>) -> Self {
        Self::Respond {
            status: 200,
            headers: vec![("content-type".into(), content_type.into())],
            pieces: pieces.into_iter().map(Piece::Bytes).collect(),
        }
    }
}

struct ScriptedBody {
    pieces: VecDeque<Piece>,
}

impl BodyStream for ScriptedBody {
    fn next_piece(&mut self) -> BoxFuture<'_, Option<Result<Vec<u8>, TransportError>>> {
        Box::pin(async move {
            loop {
                match self.pieces.pop_front()? {
                    Piece::Bytes(bytes) => return Some(Ok(bytes)),
                    Piece::Delay(wait) => tokio::time::sleep(wait).await,
                    Piece::Hold => {
                        self.pieces.push_front(Piece::Hold);
                        std::future::pending::<()>().await;
                    }
                    Piece::Fail(error) => return Some(Err(error)),
                }
            }
        })
    }
}

#[derive(Default)]
struct ScriptState {
    replies: VecDeque<ScriptedReply>,
    last: Option<ScriptedReply>,
    requests: Vec<HttpRequest>,
}

/// Replays scripted replies in order, recording every request. When the script runs out, the
/// last reply repeats (as the Python vectors' mock transport does).
#[derive(Clone, Default)]
pub struct ScriptedTransport {
    state: Arc<Mutex<ScriptState>>,
}

impl ScriptedTransport {
    /// A transport that answers with `replies` in order.
    pub fn new(replies: Vec<ScriptedReply>) -> Self {
        let state = ScriptState { replies: replies.into(), ..ScriptState::default() };
        Self { state: Arc::new(Mutex::new(state)) }
    }

    /// Every request sent so far, in order.
    pub fn requests(&self) -> Vec<HttpRequest> {
        self.state.lock().expect("script lock").requests.clone()
    }
}

impl HttpTransport for ScriptedTransport {
    fn send(&self, request: HttpRequest) -> BoxFuture<'_, Result<HttpResponse, TransportError>> {
        let reply = {
            let mut state = self.state.lock().expect("script lock");
            state.requests.push(request);
            let reply = state.replies.pop_front().or_else(|| state.last.clone());
            state.last.clone_from(&reply);
            reply
        };
        Box::pin(async move {
            match reply {
                None => Err(TransportError::Connection("the script has no reply".into())),
                Some(ScriptedReply::Fail(error)) => Err(error),
                Some(ScriptedReply::Hold) => std::future::pending().await,
                Some(ScriptedReply::Respond { status, headers, pieces }) => Ok(HttpResponse {
                    status,
                    headers,
                    body: Box::new(ScriptedBody { pieces: pieces.into() }),
                }),
            }
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> HttpRequest {
        HttpRequest {
            method: "POST".into(),
            url: "http://t/v1/chat/completions".into(),
            headers: Vec::new(),
            body: b"{}".to_vec(),
        }
    }

    #[tokio::test]
    async fn replays_pieces_and_records_requests() {
        let transport = ScriptedTransport::new(vec![
            ScriptedReply::Fail(TransportError::Connection("refused".into())),
            ScriptedReply::ok("text/event-stream", vec![b"data: a".to_vec(), b"\n\n".to_vec()]),
        ]);
        assert!(transport.send(request()).await.is_err());
        let mut response = transport.send(request()).await.unwrap();
        assert_eq!(response.header("Content-Type"), Some("text/event-stream"));
        assert_eq!(response.body.next_piece().await.unwrap().unwrap(), b"data: a");
        assert_eq!(response.read_all().await.unwrap(), b"\n\n");
        assert_eq!(transport.requests().len(), 2);
    }

    #[tokio::test(start_paused = true)]
    async fn a_held_body_never_completes() {
        let transport = ScriptedTransport::new(vec![ScriptedReply::Respond {
            status: 200,
            headers: Vec::new(),
            pieces: vec![Piece::Bytes(b"x".to_vec()), Piece::Delay(Duration::from_secs(5)), Piece::Hold],
        }]);
        let mut response = transport.send(request()).await.unwrap();
        assert!(response.body.next_piece().await.is_some());
        let waited = tokio::time::timeout(Duration::from_secs(60), response.body.next_piece()).await;
        assert!(waited.is_err(), "a held body must not deliver");
    }
}
