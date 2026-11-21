//! `LocalWebUI`: serve one local user's UI; the child owner remains the only application owner.
//!
//! An HTTP/1.1 server on `hyper`. Everything aiohttp did for the Python host is written out
//! here so the responses match it: the request-framing refusals (a missing or repeated `Host`),
//! the guard middleware and its security headers, aiohttp's routing (`405` with `Allow`) and its
//! default error texts.

use super::owner::OwnerProcess;
use super::{
    browser_command, host_platform, is_identity, is_valid_upload_name, is_webui_command, token_urlsafe,
    RemoteAccess, WebUIError, COOKIE, EXPORTS, MAX_BYTES, MAX_UPLOAD_BYTES, MAX_UPLOAD_PARAMS_BYTES,
    REMOTE_COOKIE, REMOTE_SESSION_SECONDS,
};
use crate::diagnostics::VERSION;
use bytes::Bytes;
use futures_util::future::{BoxFuture, FutureExt, Shared};
use http_body_util::{BodyExt, Full};
use hyper::body::Incoming;
use hyper::header::HeaderValue;
use hyper::{Method, Request, Response, StatusCode};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::convert::Infallible;
use std::path::{Component, Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};

/// A response body: whole bytes with their length, or (aiohttp's empty streamed body) none of
/// known length, which goes out chunked.
type Body = http_body_util::Either<
    Full<Bytes>,
    http_body_util::StreamBody<futures_util::stream::Empty<Result<hyper::body::Frame<Bytes>, Infallible>>>,
>;

/// An empty body of unknown length (`Transfer-Encoding: chunked`).
fn unsized_body() -> Body {
    http_body_util::Either::Right(http_body_util::StreamBody::new(futures_util::stream::empty()))
}

/// aiohttp's answer to an exception a handler lets escape: a bare 500, outside the middleware
/// that adds the security headers.
const UNHANDLED_TEXT: &str = "500 Internal Server Error\n\nServer got itself in trouble";
type Outcome = Result<Json, WebUIError>;

/// The headers the guard adds to every response it handles.
const SECURITY_HEADERS: &[(&str, &str)] = &[
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    (
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' \
         data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
    ),
];

/// The `Server` header. Sylvia's Python host never set one; aiohttp named itself. The Rust host
/// names Sylvia, as the provider's `User-Agent` does (`docs/rust-port/differences.md` row 11).
const SERVER: &str = concat!("SylviaGM/", env!("CARGO_PKG_VERSION"));

/// An aiohttp `HTTPException`: status, text body and extra headers.
#[derive(Debug)]
struct HttpError {
    status: StatusCode,
    text: String,
    headers: Vec<(&'static str, String)>,
}

impl HttpError {
    fn new(status: StatusCode, text: impl Into<String>) -> Self {
        Self { status, text: text.into(), headers: Vec::new() }
    }

    /// The exception's default text, `"<code>: <reason>"`.
    fn plain(status: StatusCode) -> Self {
        let reason = status.canonical_reason().unwrap_or("");
        Self::new(status, format!("{}: {reason}", status.as_u16()))
    }

    fn too_large(max_size: usize) -> Self {
        Self::new(StatusCode::PAYLOAD_TOO_LARGE, format!("Maximum request body size {max_size} exceeded."))
    }
}

/// What a handler fails with: an HTTP exception or a `WebUIError` (a `400` JSON reply).
enum Failure {
    Http(HttpError),
    WebUI(WebUIError),
    /// An exception Python lets escape (`shutil.copyfile` failing in `export`, for example).
    Unhandled,
}

impl From<HttpError> for Failure {
    fn from(error: HttpError) -> Self {
        Self::Http(error)
    }
}

impl From<WebUIError> for Failure {
    fn from(error: WebUIError) -> Self {
        Self::WebUI(error)
    }
}

type Handled = Result<Response<Body>, Failure>;

fn response(status: StatusCode, content_type: &str, body: impl Into<Bytes>) -> Response<Body> {
    let mut response = Response::new(http_body_util::Either::Left(Full::new(body.into())));
    *response.status_mut() = status;
    response
        .headers_mut()
        .insert("Content-Type", HeaderValue::from_str(content_type).expect("static content type"));
    response
}

/// `web.json_response(value)`: `json.dumps` with its default separators.
fn json_response(status: StatusCode, value: &Json) -> Response<Body> {
    response(status, "application/json; charset=utf-8", pyjson::dumps(value, Dump::DEFAULT))
}

fn ok_result(result: Json) -> Response<Body> {
    let mut body = JsonMap::new();
    body.insert("ok".into(), true.into());
    body.insert("result".into(), result);
    json_response(StatusCode::OK, &Json::Object(body))
}

fn error_response(error: &WebUIError) -> Response<Body> {
    let mut inner = JsonMap::new();
    inner.insert("code".into(), error.code.clone().into());
    inner.insert("message".into(), error.message.clone().into());
    let mut body = JsonMap::new();
    body.insert("ok".into(), false.into());
    body.insert("error".into(), Json::Object(inner));
    json_response(StatusCode::BAD_REQUEST, &Json::Object(body))
}

fn http_response(error: HttpError) -> Response<Body> {
    let status = error.status;
    let mut reply = response(status, "text/plain; charset=utf-8", error.text);
    // aiohttp still names 413 by its RFC 7231 phrase.
    if status == StatusCode::PAYLOAD_TOO_LARGE {
        reply.extensions_mut().insert(hyper::ext::ReasonPhrase::from_static(b"Request Entity Too Large"));
    }
    for (name, value) in error.headers {
        if let Ok(value) = HeaderValue::from_str(&value) {
            reply.headers_mut().insert(name, value);
        }
    }
    reply
}

/// RFC 3986 `remove_dot_segments` on the raw path, as yarl normalises a request URL.
fn remove_dot_segments(path: &str) -> String {
    let mut output: Vec<&str> = Vec::new();
    let segments: Vec<&str> = path.split('/').collect();
    let last = segments.len().saturating_sub(1);
    for (index, segment) in segments.iter().enumerate().skip(1) {
        match *segment {
            "." => {
                if index == last {
                    output.push("");
                }
            }
            ".." => {
                output.pop();
                if index == last {
                    output.push("");
                }
            }
            other => output.push(other),
        }
    }
    format!("/{}", output.join("/"))
}

/// Percent-decode a path as aiohttp's `request.path` does: `%2F` stays encoded, so a decoded
/// path never gains a separator; undecodable text is kept as it was.
fn decode_path(path: &str) -> String {
    let bytes = path.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut index = 0;
    while index < bytes.len() {
        if bytes[index] == b'%' && index + 2 < bytes.len() {
            let hex = std::str::from_utf8(&bytes[index + 1..index + 3])
                .ok()
                .and_then(|text| u8::from_str_radix(text, 16).ok());
            if let Some(byte) = hex.filter(|byte| *byte != b'/') {
                out.push(byte);
                index += 3;
                continue;
            }
        }
        out.push(bytes[index]);
        index += 1;
    }
    String::from_utf8(out).unwrap_or_else(|_| path.to_owned())
}

/// The cookie named `name` in the first `Cookie` header (aiohttp's `request.cookies`; a
/// repeated name keeps its last value).
fn cookie_value(request: &Request<Incoming>, name: &str) -> String {
    let Some(header) = request.headers().get("Cookie").and_then(|value| value.to_str().ok()) else {
        return String::new();
    };
    header
        .split(';')
        .filter_map(|pair| pair.trim().split_once('='))
        .filter(|(key, _)| *key == name)
        .map(|(_, value)| value.trim_matches('"').to_owned())
        .next_back()
        .unwrap_or_default()
}

fn header_values<'a>(request: &'a Request<Incoming>, name: &str) -> Vec<&'a str> {
    request.headers().get_all(name).iter().map(|value| value.to_str().unwrap_or("\u{fffd}")).collect()
}

/// `request.content_type`: the media type, lowercased; aiohttp's default when absent.
fn content_type(request: &Request<Incoming>) -> String {
    request
        .headers()
        .get("Content-Type")
        .and_then(|value| value.to_str().ok())
        .map(|value| value.split(';').next().unwrap_or("").trim().to_ascii_lowercase())
        .unwrap_or_else(|| "application/octet-stream".into())
}

/// Constant-time byte comparison (`secrets.compare_digest`).
fn compare_digest(left: &[u8], right: &[u8]) -> bool {
    left.len() == right.len() && left.iter().zip(right).fold(0u8, |acc, (a, b)| acc | (a ^ b)) == 0
}

/// A path's components for comparison: case-folded on Windows, as `Path.is_relative_to` is.
fn components(path: &Path) -> Vec<String> {
    path.components()
        .map(|component| {
            let text = component.as_os_str().to_string_lossy().into_owned();
            if cfg!(windows) {
                text.to_lowercase()
            } else {
                text
            }
        })
        .collect()
}

/// `Path.resolve()` (non-strict): links resolved as far as the path exists.
fn resolve(path: &Path) -> PathBuf {
    let mut existing = path.to_path_buf();
    let mut rest = Vec::new();
    loop {
        if let Ok(resolved) = std::fs::canonicalize(&existing) {
            let mut resolved = PathBuf::from(crate::util::plain_path(&resolved));
            for part in rest.iter().rev() {
                resolved.push(part);
            }
            // Leftover `..` in the missing part are applied lexically, as Python does.
            let mut normal = PathBuf::new();
            for component in resolved.components() {
                match component {
                    Component::ParentDir => {
                        normal.pop();
                    }
                    Component::CurDir => {}
                    other => normal.push(other.as_os_str()),
                }
            }
            return normal;
        }
        match (existing.file_name(), existing.parent()) {
            (Some(name), Some(parent)) => {
                rest.push(name.to_os_string());
                existing = parent.to_path_buf();
            }
            _ => return path.to_path_buf(),
        }
    }
}

struct Receipt {
    digest: String,
    outcome: Shared<BoxFuture<'static, Outcome>>,
}

#[derive(Default)]
struct State {
    remote_sessions: HashMap<String, Instant>,
    login_attempts: Vec<Instant>,
    /// Insertion order is Python's `OrderedDict` order.
    requests: Vec<(String, Receipt)>,
    receipt_sizes: HashMap<String, usize>,
    downloads: Vec<(String, PathBuf)>,
    download_directory: Option<PathBuf>,
}

/// The WebUI host for one data root.
pub struct LocalWebUI {
    owner: Arc<OwnerProcess>,
    frontend: PathBuf,
    remote: Option<RemoteAccess>,
    origin: String,
    authority: String,
    cookie: String,
    session: String,
    state: Mutex<State>,
    export_lock: tokio::sync::Mutex<()>,
    shutdown: tokio::sync::Mutex<Option<Result<(), WebUIError>>>,
    stopped: tokio::sync::watch::Sender<bool>,
}

impl LocalWebUI {
    /// `LocalWebUI(owner, frontend, origin, remote=)`.
    pub fn new(
        owner: Arc<OwnerProcess>,
        frontend: &Path,
        origin: &str,
        remote: Option<RemoteAccess>,
    ) -> Result<Arc<Self>, WebUIError> {
        let loopback = origin.strip_prefix("http://127.0.0.1:").is_some_and(|port| {
            (1..=5).contains(&port.len()) && port.bytes().all(|byte| byte.is_ascii_digit())
        });
        if remote.is_none() && !loopback {
            return Err(WebUIError::unavailable(
                "Non-loopback browser access requires explicit remote authentication.",
            ));
        }
        if remote.as_ref().is_some_and(|remote| remote.origin != origin) {
            return Err(WebUIError::unavailable("Remote authentication and browser origin must match."));
        }
        let authority = origin.split_once("://").map_or(origin, |(_, rest)| rest).to_owned();
        let cookie = if remote.is_some() {
            REMOTE_COOKIE.to_owned()
        } else {
            format!("{COOKIE}_{}", authority.rsplit(':').next().unwrap_or(""))
        };
        let (stopped, _) = tokio::sync::watch::channel(false);
        Ok(Arc::new(Self {
            owner,
            frontend: resolve(frontend),
            remote,
            origin: origin.to_owned(),
            authority,
            cookie,
            session: token_urlsafe(32),
            state: Mutex::new(State::default()),
            export_lock: tokio::sync::Mutex::new(()),
            shutdown: tokio::sync::Mutex::new(None),
            stopped,
        }))
    }

    /// Resolves once the browser stopped the host.
    pub fn stopped(&self) -> tokio::sync::watch::Receiver<bool> {
        self.stopped.subscribe()
    }

    /// Answer one request.
    pub async fn handle(self: Arc<Self>, request: Request<Incoming>) -> Result<Response<Body>, Infallible> {
        let hosts = header_values(&request, "Host");
        // aiohttp's request parser refuses these before any middleware runs.
        let framing = match hosts.len() {
            0 => Some("Missing 'Host' header in request."),
            1 => None,
            _ => Some("Duplicate 'Host' header found."),
        };
        let mut reply = match framing {
            Some(text) => {
                let mut reply = response(StatusCode::BAD_REQUEST, "text/plain; charset=utf-8", text);
                reply.headers_mut().insert("Server", HeaderValue::from_static(SERVER));
                return Ok(reply);
            }
            None => match self.guarded(request).await {
                Ok(reply) => reply,
                Err(Failure::Http(error)) => http_response(error),
                Err(Failure::WebUI(error)) => error_response(&error),
                Err(Failure::Unhandled) => {
                    let mut reply = response(
                        StatusCode::INTERNAL_SERVER_ERROR,
                        "text/plain; charset=utf-8",
                        UNHANDLED_TEXT,
                    );
                    reply.headers_mut().insert("Server", HeaderValue::from_static(SERVER));
                    return Ok(reply);
                }
            },
        };
        let headers = reply.headers_mut();
        for (name, value) in SECURITY_HEADERS {
            headers.insert(*name, HeaderValue::from_static(value));
        }
        headers.insert("Server", HeaderValue::from_static(SERVER));
        Ok(reply)
    }

    fn authenticated(&self, request: &Request<Incoming>) -> bool {
        let token = cookie_value(request, &self.cookie);
        if self.remote.is_none() {
            return compare_digest(token.as_bytes(), self.session.as_bytes());
        }
        let now = Instant::now();
        let mut state = self.state.lock().expect("webui state");
        state.remote_sessions.retain(|_, expiry| *expiry > now);
        state.remote_sessions.contains_key(&token)
    }

    /// `guarded`: the boundary checks, in Python's order, then the route.
    async fn guarded(self: &Arc<Self>, request: Request<Incoming>) -> Handled {
        // Raw Host only. Forwarded, X-Forwarded-* and Tailscale identity are never authority.
        if header_values(&request, "Host") != [self.authority.as_str()] {
            return Err(HttpError::new(
                StatusCode::FORBIDDEN,
                "This WebUI accepts its exact configured Host only.",
            )
            .into());
        }
        let raw_path = request.uri().path().to_owned();
        let path = decode_path(&remove_dot_segments(&raw_path));
        let first = |name: &str| header_values(&request, name).first().map(|value| (*value).to_owned());
        let is_get = request.method() == Method::GET || request.method() == Method::HEAD;
        let bootstrap_navigation = request.method() == Method::GET
            && (path == "/" || path == "/app.html")
            && first("Sec-Fetch-Mode").as_deref() == Some("navigate")
            && first("Sec-Fetch-Dest").as_deref() == Some("document");
        let site = first("Sec-Fetch-Site");
        if !matches!(site.as_deref(), None | Some("none" | "same-origin"))
            && !(bootstrap_navigation && site.as_deref() == Some("cross-site"))
        {
            return Err(HttpError::new(
                StatusCode::FORBIDDEN,
                "Cross-site control requests are unavailable.",
            )
            .into());
        }
        let origins = header_values(&request, "Origin");
        if !(origins.is_empty() || origins == [self.origin.as_str()]) {
            return Err(HttpError::new(StatusCode::FORBIDDEN, "Origin rejected.").into());
        }
        if request.method() == Method::POST
            && (origins.first().copied() != Some(self.origin.as_str())
                || header_values(&request, "X-Sylvia-WebUI") != ["1"])
        {
            return Err(
                HttpError::new(StatusCode::FORBIDDEN, "A same-origin WebUI request is required.").into()
            );
        }
        if path.starts_with("/auth/") && self.remote.is_none() {
            return Err(HttpError::plain(StatusCode::NOT_FOUND).into());
        }
        let authenticated = self.authenticated(&request);
        if self.remote.is_some() && !authenticated && path != "/auth/login" {
            if request.method() == Method::GET && (path == "/" || path == "/app.html") {
                let page = read_text(&self.frontend.join("webui-login.html")).map_err(|_| {
                    HttpError::new(StatusCode::INTERNAL_SERVER_ERROR, "500 Internal Server Error")
                })?;
                return Ok(response(StatusCode::OK, "text/html; charset=utf-8", page));
            }
            return Err(
                HttpError::new(StatusCode::UNAUTHORIZED, "Sign in to the private WebUI to connect.").into()
            );
        }
        if path.starts_with("/api/") && !authenticated {
            return Err(HttpError::new(StatusCode::UNAUTHORIZED, "Open the local WebUI to connect.").into());
        }
        self.route(request, &path, is_get).await
    }

    /// aiohttp's router: the first route whose path and method match; a path some route takes
    /// with another method is `405` with the allowed methods.
    async fn route(self: &Arc<Self>, request: Request<Incoming>, path: &str, is_get: bool) -> Handled {
        let segment = |prefix: &str| {
            path.strip_prefix(prefix)
                .filter(|rest| !rest.is_empty() && !rest.contains('/'))
                .map(str::to_owned)
        };
        let post = request.method() == Method::POST;
        let post_route = match path {
            "/auth/login" | "/auth/logout" | "/api/command" | "/api/stop" => true,
            _ => segment("/api/upload/").is_some(),
        };
        if post && post_route {
            return match path {
                "/auth/login" => self.login(request).await,
                "/auth/logout" => Ok(self.logout(&request)),
                "/api/command" => self.command(request).await,
                "/api/stop" => self.stop().await,
                _ => self.upload(request, &segment("/api/upload/").unwrap_or_default()).await,
            };
        }
        if is_get {
            if let Some(identity) = segment("/api/download/") {
                return self.download(&request, &identity);
            }
            return self.asset(path);
        }
        // Every path is taken by the GET catch-all; some also by a POST route.
        let mut allowed = vec!["GET", "HEAD"];
        if post_route {
            allowed.push("POST");
        }
        let mut error = HttpError::plain(StatusCode::METHOD_NOT_ALLOWED);
        error.headers.push(("Allow", allowed.join(",")));
        Err(error.into())
    }

    /// `login`: exchange a user-managed key for a bounded, revocable browser session.
    async fn login(self: &Arc<Self>, request: Request<Incoming>) -> Handled {
        let Some(remote) = self.remote.clone() else {
            return Err(HttpError::plain(StatusCode::NOT_FOUND).into());
        };
        let now = Instant::now();
        {
            let mut state = self.state.lock().expect("webui state");
            state.login_attempts.retain(|stamp| now.duration_since(*stamp) < Duration::from_secs(60));
            if state.login_attempts.len() >= 5 {
                let mut error = HttpError::new(
                    StatusCode::TOO_MANY_REQUESTS,
                    "Too many sign-in attempts. Wait one minute.",
                );
                error.headers.push(("Retry-After", "60".into()));
                return Err(error.into());
            }
            state.login_attempts.push(now);
        }
        if content_type(&request) != "application/json" {
            return Err(HttpError::plain(StatusCode::UNSUPPORTED_MEDIA_TYPE).into());
        }
        let previous = cookie_value(&request, &self.cookie);
        let raw = read_body(request.into_body(), 1024).await?;
        let valid = std::str::from_utf8(&raw)
            .ok()
            .and_then(|text| pyjson::loads(text).ok())
            .and_then(|payload| payload.get("key").and_then(Json::as_str).map(str::to_owned))
            .filter(|key| {
                (43..=128).contains(&key.len())
                    && key.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
            })
            .is_some_and(|key| compare_digest(&Sha256::digest(key.as_bytes()), &remote.key_digest));
        if !valid {
            return Err(HttpError::new(StatusCode::UNAUTHORIZED, "Access key rejected.").into());
        }
        let token = {
            let mut state = self.state.lock().expect("webui state");
            state.remote_sessions.retain(|_, expiry| *expiry > now);
            state.remote_sessions.remove(&previous);
            if state.remote_sessions.len() >= 32 {
                return Err(HttpError::new(
                    StatusCode::TOO_MANY_REQUESTS,
                    "Session limit reached. Sign out or restart the WebUI.",
                )
                .into());
            }
            let token = token_urlsafe(32);
            state.remote_sessions.insert(token.clone(), now + Duration::from_secs(REMOTE_SESSION_SECONDS));
            token
        };
        let mut body = JsonMap::new();
        body.insert("ok".into(), true.into());
        let mut reply = json_response(StatusCode::OK, &Json::Object(body));
        let cookie = format!(
            "{}={token}; HttpOnly; Max-Age={REMOTE_SESSION_SECONDS}; Path=/; SameSite=Strict; Secure",
            self.cookie
        );
        reply.headers_mut().insert("Set-Cookie", HeaderValue::from_str(&cookie).expect("cookie text"));
        Ok(reply)
    }

    /// `logout`: revoke this browser's session without stopping the shared owner.
    fn logout(&self, request: &Request<Incoming>) -> Response<Body> {
        let token = cookie_value(request, &self.cookie);
        self.state.lock().expect("webui state").remote_sessions.remove(&token);
        let mut result = JsonMap::new();
        result.insert("status".into(), "signed_out".into());
        let mut reply = ok_result(Json::Object(result));
        let cookie = format!(
            "{}=\"\"; expires=Thu, 01 Jan 1970 00:00:00 GMT; HttpOnly; Max-Age=0; Path=/; SameSite=Strict; Secure",
            self.cookie
        );
        reply.headers_mut().insert("Set-Cookie", HeaderValue::from_str(&cookie).expect("cookie text"));
        reply
    }

    /// `asset`: the frontend files, `app.html` with the WebUI bridge added.
    fn asset(&self, path: &str) -> Handled {
        let name = path.strip_prefix('/').unwrap_or(path);
        let name = if name.is_empty() { "app.html" } else { name };
        if name == "app.html" {
            let mut text = read_text(&self.frontend.join("app.html")).map_err(|_| {
                HttpError::new(StatusCode::INTERNAL_SERVER_ERROR, "500 Internal Server Error")
            })?;
            if self.remote.is_some() {
                text = text.replacen("<html", "<html data-webui-remote=\"true\"", 1);
            }
            text = text.replace("</head>", "<link rel=\"stylesheet\" href=\"webui-mobile.css\">\n</head>");
            text = text.replace(
                "<script src=\"panel-workspace.js\">",
                "<script src=\"webui-bridge.js\"></script>\n<script src=\"webui-mobile.js\"></script>\n<script src=\"panel-workspace.js\">",
            );
            let mut reply = response(StatusCode::OK, "text/html; charset=utf-8", text);
            // The per-launch session never enters a URL, script or browser storage.
            if self.remote.is_none() {
                let cookie = format!("{}={}; HttpOnly; Path=/; SameSite=Strict", self.cookie, self.session);
                reply
                    .headers_mut()
                    .insert("Set-Cookie", HeaderValue::from_str(&cookie).expect("cookie text"));
            }
            return Ok(reply);
        }
        let target = resolve(&self.frontend.join(name));
        let inside = components(&target).starts_with(&components(&self.frontend));
        let content_type = match target.extension().and_then(|suffix| suffix.to_str()) {
            Some("js") => "text/javascript",
            Some("css") => "text/css",
            Some("png") => "image/png",
            Some("svg") => "image/svg+xml",
            Some("woff2") => "font/woff2",
            _ => "",
        };
        if !inside || !target.is_file() || content_type.is_empty() {
            return Err(HttpError::plain(StatusCode::NOT_FOUND).into());
        }
        let bytes = std::fs::read(&target).map_err(|_| HttpError::plain(StatusCode::NOT_FOUND))?;
        Ok(response(StatusCode::OK, content_type, bytes))
    }

    /// `command`: one idempotent browser command.
    async fn command(self: &Arc<Self>, request: Request<Incoming>) -> Handled {
        if content_type(&request) != "application/json" {
            return Err(HttpError::plain(StatusCode::UNSUPPORTED_MEDIA_TYPE).into());
        }
        let raw = read_body(request.into_body(), MAX_BYTES).await?;
        let malformed = || WebUIError::new("Malformed WebUI request.", "invalid_request");
        let payload =
            std::str::from_utf8(&raw).ok().and_then(|text| pyjson::loads(text).ok()).ok_or_else(malformed)?;
        let payload = payload.as_object().ok_or_else(malformed)?;
        let identity = payload
            .get("id")
            .and_then(Json::as_str)
            .filter(|identity| is_identity(identity))
            .ok_or_else(malformed)?;
        let method = payload.get("method").and_then(Json::as_str).ok_or_else(malformed)?.to_owned();
        let params = match payload.get("params") {
            None => Json::Object(JsonMap::new()),
            Some(Json::Object(params)) => Json::Object(params.clone()),
            Some(_) => return Err(malformed().into()),
        };
        let digest = hex::encode(Sha256::digest(
            pyjson::dumps(
                &Json::Array(vec![method.clone().into(), params.clone()]),
                Dump::DEFAULT.sorted(true),
            )
            .as_bytes(),
        ));
        let outcome = {
            let mut state = self.state.lock().expect("webui state");
            match state.requests.iter().find(|(key, _)| key == identity) {
                Some((_, previous)) if previous.digest != digest => {
                    return Err(WebUIError::new(
                        "This request identity has different parameters.",
                        "idempotency_conflict",
                    )
                    .into());
                }
                Some((_, previous)) => previous.outcome.clone(),
                None => {
                    // Completed transport receipts are bounded; durable action receipts and
                    // replay protection always remain in the owner.
                    let mut index = 0;
                    while state.requests.len() >= 128 && index < state.requests.len() {
                        if state.requests[index].1.outcome.peek().is_some() {
                            let (key, _) = state.requests.remove(index);
                            state.receipt_sizes.remove(&key);
                        } else {
                            index += 1;
                        }
                    }
                    if state.requests.len() >= 128 {
                        return Err(WebUIError::new(
                            "Too many pending requests. Wait or Stop the current work.",
                            "busy",
                        )
                        .into());
                    }
                    let ui = Arc::clone(self);
                    let (task_identity, task_method, task_params) =
                        (identity.to_owned(), method.clone(), params.clone());
                    let task = tokio::spawn(async move {
                        let outcome = ui.invoke(&task_method, &task_params).await;
                        ui.trim_receipts(&task_identity, &outcome);
                        outcome
                    });
                    let shared = async move {
                        task.await.unwrap_or_else(|_| {
                            Err(WebUIError::unavailable("The command stopped before it finished."))
                        })
                    }
                    .boxed()
                    .shared();
                    state.requests.push((identity.to_owned(), Receipt { digest, outcome: shared.clone() }));
                    shared
                }
            }
        };
        match outcome.await {
            Ok(result) => Ok(ok_result(result)),
            Err(error) if error.is_unhandled() => Err(Failure::Unhandled),
            Err(error) => Err(error.into()),
        }
    }

    /// `trim_receipts`: bound transient replay memory.
    fn trim_receipts(&self, identity: &str, outcome: &Outcome) {
        let size = match outcome {
            Ok(result) => pyjson::dumps(result, Dump::DEFAULT).len(),
            Err(_) => 1024,
        };
        let mut state = self.state.lock().expect("webui state");
        state.receipt_sizes.insert(identity.to_owned(), size);
        let mut index = 0;
        while index < state.requests.len() {
            if state.receipt_sizes.values().sum::<usize>() <= 16 * 1024 * 1024 {
                break;
            }
            // The receipt finishing now is done too, though its shared future is not polled yet.
            if state.requests[index].0 == identity || state.requests[index].1.outcome.peek().is_some() {
                let (key, _) = state.requests.remove(index);
                state.receipt_sizes.remove(&key);
            } else {
                index += 1;
            }
        }
    }

    /// `invoke(method, raw)`.
    async fn invoke(self: &Arc<Self>, method: &str, raw: &Json) -> Outcome {
        let (method, params) = browser_command(method, raw)?;
        match method.as_str() {
            "application_info" => {
                let mut info = JsonMap::new();
                info.insert("product_name".into(), "Sylvia GM".into());
                info.insert("version".into(), VERSION.into());
                info.insert("release_channel".into(), "source WebUI".into());
                info.insert("signing_status".into(), "unsigned".into());
                // The system this owner runs on, where saved keys are kept; not the browser's.
                info.insert("platform".into(), host_platform().into());
                return Ok(Json::Object(info));
            }
            "desktop_update_startup_state" => {
                let mut state = JsonMap::new();
                state.insert("pending".into(), false.into());
                return Ok(Json::Object(state));
            }
            "desktop_update_state" => {
                let mut state = JsonMap::new();
                state.insert("phase".into(), "unavailable".into());
                state.insert("current_version".into(), VERSION.into());
                state.insert("message".into(), "Application updates require the desktop application.".into());
                return Ok(Json::Object(state));
            }
            _ => {}
        }
        if let Some((_, folder, suffix)) = EXPORTS.iter().find(|(name, _, _)| *name == method) {
            return self.export(&method, &params, folder, suffix).await;
        }
        if !is_webui_command(&method) {
            return Err(WebUIError::new(
                "This capability requires the desktop application and is unavailable in this WebUI.",
                "unsupported",
            ));
        }
        let result = self.owner.call(&method, &params).await?;
        if method == "display_snapshot" {
            // The browser uses this read only for identity, lifecycle and revisions.
            let mut projected = JsonMap::new();
            if let Some(result) = result.as_object() {
                for key in ["campaign_id", "display_name", "lifecycle", "writer", "revision", "package_lock"]
                {
                    if let Some(value) = result.get(key) {
                        projected.insert(key.into(), value.clone());
                    }
                }
            }
            return Ok(Json::Object(projected));
        }
        Ok(result)
    }

    /// `export`: run the owner's export, then hand the browser a one-use download handle.
    async fn export(&self, method: &str, params: &JsonMap, folder: &str, suffix: &str) -> Outcome {
        let key = if method == "backup_authority" { "backup_id" } else { "export_id" };
        let identity = params.get(key).and_then(Json::as_str).filter(|identity| is_identity(identity));
        let Some(identity) = identity else {
            return Err(WebUIError::new("Choose a valid export identity.", "invalid_request"));
        };
        let _guard = self.export_lock.lock().await;
        let owner_method = if method == "backup_authority" { "backup_database" } else { method };
        let result = self.owner.call(owner_method, params).await?;
        // `tempfile.TemporaryDirectory` or `shutil.copyfile` failing escapes Python's handler.
        let failed = |_| WebUIError::unhandled();
        let directory = {
            let mut state = self.state.lock().expect("webui state");
            match &state.download_directory {
                Some(directory) => directory.clone(),
                None => {
                    let directory =
                        self.owner.data_root.join(format!(".webui-download-{}", super::token_hex(4)));
                    std::fs::create_dir_all(&directory).map_err(failed)?;
                    state.download_directory = Some(directory.clone());
                    directory
                }
            }
        };
        let token = token_urlsafe(24);
        let path = directory.join(format!("{token}{suffix}"));
        let source = self.owner.data_root.join(folder).join(format!("{identity}{suffix}"));
        std::fs::copy(&source, &path).map_err(failed)?;
        if folder == "exports" {
            // The download copy is the only one needed now.
            let _ = std::fs::remove_file(&source);
        }
        {
            let mut state = self.state.lock().expect("webui state");
            if state.downloads.len() >= 128 {
                let (_, oldest) = state.downloads.remove(0);
                let _ = std::fs::remove_file(oldest);
            }
            state.downloads.push((token.clone(), path));
        }
        let mut reply = JsonMap::new();
        reply.insert("status".into(), "exported".into());
        reply.insert("download".into(), format!("/api/download/{token}").into());
        reply.insert("filename".into(), format!("sylvia{suffix}").into());
        reply.insert("file_name".into(), format!("sylvia{suffix}").into());
        let export = match &result {
            Json::Object(map) => map.get("export").cloned().unwrap_or_else(|| result.clone()),
            other => other.clone(),
        };
        reply.insert("export".into(), export);
        Ok(Json::Object(reply))
    }

    /// `download`: a handle made by `export`, served as aiohttp's `FileResponse` serves it
    /// (`file_response`), with the attachment name.
    fn download(&self, request: &Request<Incoming>, identity: &str) -> Handled {
        let path = {
            let state = self.state.lock().expect("webui state");
            state.downloads.iter().find(|(token, _)| token == identity).map(|(_, path)| path.clone())
        };
        let Some(path) = path.filter(|path| path.is_file()) else {
            return Err(HttpError::plain(StatusCode::NOT_FOUND).into());
        };
        let header = |name: &str| {
            request.headers().get(name).map(|value| String::from_utf8_lossy(value.as_bytes()).into_owned())
        };
        let (if_match, if_none_match, if_modified_since, if_unmodified_since, if_range, range) = (
            header("If-Match"),
            header("If-None-Match"),
            header("If-Modified-Since"),
            header("If-Unmodified-Since"),
            header("If-Range"),
            header("Range"),
        );
        let conditions = super::file_response::Conditions {
            head: request.method() == Method::HEAD,
            if_match: if_match.as_deref(),
            if_none_match: if_none_match.as_deref(),
            if_modified_since: if_modified_since.as_deref(),
            if_unmodified_since: if_unmodified_since.as_deref(),
            if_range: if_range.as_deref(),
            range: range.as_deref(),
        };
        let prepared = super::file_response::prepare(&path, &conditions);
        let status = StatusCode::from_u16(prepared.status).expect("a known status");
        let mut reply = match (prepared.framing, prepared.body) {
            (super::file_response::Framing::Sized, Some(body)) => {
                Response::new(http_body_util::Either::Left(Full::new(Bytes::from(body))))
            }
            (super::file_response::Framing::None, _) => {
                Response::new(http_body_util::Either::Left(Full::new(Bytes::new())))
            }
            _ => Response::new(unsized_body()),
        };
        *reply.status_mut() = status;
        if prepared.status == 416 {
            // aiohttp names 416 by its RFC 7233 phrase.
            reply
                .extensions_mut()
                .insert(hyper::ext::ReasonPhrase::from_static(b"Requested Range Not Satisfiable"));
        }
        for (name, value) in prepared.headers {
            reply.headers_mut().insert(name, HeaderValue::from_str(&value).expect("ascii header"));
        }
        let suffix =
            path.extension().map(|suffix| format!(".{}", suffix.to_string_lossy())).unwrap_or_default();
        let disposition = format!("attachment; filename=\"sylvia{suffix}\"");
        reply
            .headers_mut()
            .insert("Content-Disposition", HeaderValue::from_str(&disposition).expect("ascii"));
        Ok(reply)
    }

    /// `upload`: selected files into a temporary folder, then the owner's file command.
    async fn upload(self: &Arc<Self>, request: Request<Incoming>, kind: &str) -> Handled {
        if !matches!(kind, "package" | "recovery" | "builder" | "character") {
            return Err(HttpError::plain(StatusCode::NOT_FOUND).into());
        }
        let boundary = request
            .headers()
            .get("Content-Type")
            .and_then(|value| value.to_str().ok())
            .and_then(|value| multer::parse_boundary(value).ok())
            .ok_or_else(|| HttpError::new(StatusCode::INTERNAL_SERVER_ERROR, "500 Internal Server Error"))?;
        let mut multipart = multer::Multipart::new(request.into_body().into_data_stream(), boundary);
        let _ = std::fs::create_dir_all(&self.owner.data_root);
        let directory = TemporaryDirectory::new(&self.owner.data_root, ".webui-upload-")
            .map_err(|_| HttpError::new(StatusCode::INTERNAL_SERVER_ERROR, "500 Internal Server Error"))?;
        let invalid = |message: &str| Failure::WebUI(WebUIError::new(message, "invalid_request"));
        let mut params = JsonMap::new();
        let mut received_params = false;
        let mut total = 0usize;
        let mut paths: Vec<PathBuf> = Vec::new();
        let limit = if kind == "builder" { 8 } else { 1 };
        while let Some(mut part) =
            multipart.next_field().await.map_err(|_| invalid("Unsupported upload field."))?
        {
            let name = part.name().map(str::to_owned);
            let filename = part.file_name().map(str::to_owned);
            if name.as_deref() == Some("params") && filename.is_none() {
                if received_params {
                    return Err(invalid("Upload parameters were repeated."));
                }
                received_params = true;
                if part.headers().contains_key("Content-Transfer-Encoding") {
                    return Err(invalid("Encoded upload parameters are unsupported."));
                }
                let mut text: Vec<u8> = Vec::new();
                while let Some(chunk) =
                    part.chunk().await.map_err(|_| invalid("Invalid upload parameters."))?
                {
                    total = total.saturating_add(chunk.len());
                    if total > MAX_UPLOAD_BYTES {
                        return Err(HttpError::too_large(MAX_UPLOAD_BYTES).into());
                    }
                    let remaining = (MAX_UPLOAD_PARAMS_BYTES + 1).saturating_sub(text.len());
                    text.extend_from_slice(&chunk[..chunk.len().min(remaining)]);
                    if text.len() > MAX_UPLOAD_PARAMS_BYTES {
                        return Err(HttpError::too_large(MAX_UPLOAD_PARAMS_BYTES).into());
                    }
                }
                let parsed = std::str::from_utf8(&text)
                    .ok()
                    .and_then(|text| pyjson::loads(text).ok())
                    .ok_or_else(|| invalid("Invalid upload parameters."))?;
                let Json::Object(object) = parsed else {
                    return Err(invalid("Upload parameters must be an object."));
                };
                browser_command("upload", &Json::Object(object.clone()))?;
                params = object;
            } else if name.as_deref() == Some("file")
                && filename.as_deref().is_some_and(|name| !name.is_empty())
            {
                if paths.len() >= limit {
                    return Err(invalid("Too many selected files."));
                }
                let filename = filename.unwrap_or_default();
                if !is_valid_upload_name(&filename) {
                    return Err(invalid("Invalid selected filename."));
                }
                let folder = directory.path.join(paths.len().to_string());
                std::fs::create_dir(&folder).map_err(|_| invalid("Invalid selected filename."))?;
                let target = folder.join(&filename);
                let mut output = std::fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&target)
                    .map_err(|_| invalid("Invalid selected filename."))?;
                while let Some(chunk) =
                    part.chunk().await.map_err(|_| invalid("Unsupported upload field."))?
                {
                    total = total.saturating_add(chunk.len());
                    if total > MAX_UPLOAD_BYTES {
                        return Err(HttpError::too_large(MAX_UPLOAD_BYTES).into());
                    }
                    std::io::Write::write_all(&mut output, &chunk).map_err(|_| {
                        HttpError::new(StatusCode::INTERNAL_SERVER_ERROR, "500 Internal Server Error")
                    })?;
                }
                paths.push(target);
            } else {
                return Err(invalid("Unsupported upload field."));
            }
        }
        if paths.is_empty() {
            return Err(invalid("Select a file first."));
        }
        let path_text = |path: &PathBuf| Json::String(path.to_string_lossy().into_owned());
        let result = if kind == "builder" {
            let mut owner_params = JsonMap::new();
            owner_params.insert("session_id".into(), params.get("sessionId").cloned().unwrap_or(Json::Null));
            owner_params
                .insert("ingestion_id".into(), params.get("ingestionId").cloned().unwrap_or(Json::Null));
            owner_params.insert("source_paths".into(), Json::Array(paths.iter().map(path_text).collect()));
            self.file_command("ingest_builder_sources", owner_params).await?
        } else {
            let method = match kind {
                "package" => "install_package_file",
                "recovery" => "recover_campaign_file",
                _ => "preview_character_card_file",
            };
            let (_, mut safe) = browser_command(method, &Json::Object(params))?;
            safe.insert("source_path".into(), path_text(&paths[0]));
            let result = self.file_command(method, safe).await?;
            let file_name =
                paths[0].file_name().map(|name| name.to_string_lossy().into_owned()).unwrap_or_default();
            if kind == "recovery" {
                let mut wrapped = JsonMap::new();
                wrapped.insert("status".into(), "recovered".into());
                wrapped.insert("file_name".into(), file_name.into());
                wrapped.insert("recovery".into(), result);
                Json::Object(wrapped)
            } else if kind == "package" && result.get("status").and_then(Json::as_str) != Some("repairable") {
                let mut wrapped = JsonMap::new();
                wrapped.insert("status".into(), "installed".into());
                wrapped.insert("file_name".into(), file_name.into());
                wrapped.insert("candidate".into(), result);
                Json::Object(wrapped)
            } else {
                result
            }
        };
        drop(directory);
        Ok(ok_result(result))
    }

    /// `_file_command`: an owner command over selected files. It runs to the end even if the
    /// browser goes away, so its files are removed only once the owner is done with them.
    async fn file_command(&self, method: &str, params: JsonMap) -> Outcome {
        let owner = Arc::clone(&self.owner);
        let method = method.to_owned();
        tokio::spawn(async move { owner.call(&method, &params).await })
            .await
            .unwrap_or_else(|_| Err(WebUIError::unavailable("The command stopped before it finished.")))
    }

    /// `stop`: close the owner once, then stop serving.
    async fn stop(self: &Arc<Self>) -> Handled {
        let outcome = {
            let mut shutdown = self.shutdown.lock().await;
            if shutdown.is_none() {
                *shutdown = Some(self.owner.close().await);
            }
            shutdown.clone().expect("set above")
        };
        outcome?;
        let stopped = self.stopped.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(100)).await;
            let _ = stopped.send(true);
        });
        let mut result = JsonMap::new();
        result.insert("status".into(), "closed".into());
        Ok(ok_result(Json::Object(result)))
    }

    /// `cleanup`: close the owner and remove download copies.
    pub async fn cleanup(&self) -> Result<(), WebUIError> {
        self.state.lock().expect("webui state").remote_sessions.clear();
        let closed = self.owner.close().await;
        let directory = {
            let mut state = self.state.lock().expect("webui state");
            state.downloads.clear();
            state.download_directory.take()
        };
        if let Some(directory) = directory {
            let _ = std::fs::remove_dir_all(directory);
        }
        closed
    }
}

/// `Path.read_text(encoding="utf-8")`: universal newlines, so CR LF and a lone CR read as LF.
fn read_text(path: &Path) -> std::io::Result<String> {
    let text = std::fs::read_to_string(path)?;
    Ok(text.replace("\r\n", "\n").replace('\r', "\n"))
}

/// `tempfile.TemporaryDirectory(prefix=, dir=)`: removed when dropped.
struct TemporaryDirectory {
    path: PathBuf,
}

impl TemporaryDirectory {
    fn new(parent: &Path, prefix: &str) -> std::io::Result<Self> {
        for _ in 0..100 {
            let path = parent.join(format!("{prefix}{}", super::token_hex(4)));
            match std::fs::create_dir(&path) {
                Ok(()) => return Ok(Self { path }),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error),
            }
        }
        Err(std::io::Error::other("no temporary folder name was free"))
    }
}

impl Drop for TemporaryDirectory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

/// `request.read()` with aiohttp's `client_max_size`: more than `limit` bytes is a `413`.
async fn read_body(mut body: Incoming, limit: usize) -> Result<Vec<u8>, HttpError> {
    let mut raw = Vec::new();
    while let Some(frame) = body.frame().await {
        let frame = frame.map_err(|_| HttpError::plain(StatusCode::BAD_REQUEST))?;
        if let Ok(data) = frame.into_data() {
            raw.extend_from_slice(&data);
            if raw.len() > limit {
                return Err(HttpError::too_large(limit));
            }
        }
    }
    Ok(raw)
}

/// `serve(data_root, port, remote=)`: start explicitly, print the real address, stop on Stop
/// or Ctrl+C.
pub async fn serve(
    data_root: &Path,
    port: u16,
    remote: Option<RemoteAccess>,
    owner_program: PathBuf,
    frontend: PathBuf,
) -> Result<(), WebUIError> {
    if remote.is_some() && port == 0 {
        return Err(WebUIError::unavailable(
            "Private HTTPS mode requires an explicit nonzero --port for the proxy.",
        ));
    }
    if !frontend.join("app.html").is_file() {
        return Err(WebUIError::unavailable(
            "The WebUI assets are unavailable in this application or source checkout.",
        ));
    }
    let owner = Arc::new(OwnerProcess::new(data_root, owner_program));
    let listener = std::net::TcpListener::bind(("127.0.0.1", port))
        .map_err(|error| WebUIError::unavailable(error.to_string()))?;
    let address = format!(
        "http://127.0.0.1:{}",
        listener.local_addr().map_err(|error| WebUIError::unavailable(error.to_string()))?.port()
    );
    owner.start().await?;
    let origin = remote.as_ref().map_or_else(|| address.clone(), |remote| remote.origin.clone());
    let ui = match LocalWebUI::new(Arc::clone(&owner), &frontend, &origin, remote.clone()) {
        Ok(ui) => ui,
        Err(error) => {
            let _ = owner.close().await;
            return Err(error);
        }
    };
    listener.set_nonblocking(true).map_err(|error| WebUIError::unavailable(error.to_string()))?;
    let listener = tokio::net::TcpListener::from_std(listener)
        .map_err(|error| WebUIError::unavailable(error.to_string()))?;
    match &remote {
        Some(remote) => {
            println!("Sylvia private WebUI: {}/app.html (access key required)", remote.origin);
            println!("Loopback proxy target: {address}; direct local bootstrap is disabled.");
        }
        None => println!("Sylvia WebUI ready: {address}/app.html"),
    }
    println!("Stop from the WebUI or press Ctrl+C. Closing a tab keeps saved sessions available.");
    let mut stopped = ui.stopped();
    let mut connections = tokio::task::JoinSet::new();
    loop {
        tokio::select! {
            accepted = listener.accept() => {
                let Ok((stream, _)) = accepted else { continue };
                let ui = Arc::clone(&ui);
                connections.spawn(async move {
                    let service = hyper::service::service_fn(move |request| Arc::clone(&ui).handle(request));
                    let _ = hyper::server::conn::http1::Builder::new()
                        .serve_connection(hyper_util::rt::TokioIo::new(stream), service)
                        .await;
                });
            }
            changed = stopped.changed() => {
                if changed.is_err() || *stopped.borrow() { break; }
            }
            _ = tokio::signal::ctrl_c() => break,
        }
    }
    connections.abort_all();
    ui.cleanup().await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalises_paths_like_yarl_and_aiohttp() {
        assert_eq!(remove_dot_segments("/frontend/../app.js"), "/app.js");
        assert_eq!(remove_dot_segments("/../pyproject.toml"), "/pyproject.toml");
        assert_eq!(remove_dot_segments("/a/./b/"), "/a/b/");
        assert_eq!(decode_path("/%2e%2e/x"), "/../x");
        assert_eq!(decode_path("/..%2fx"), "/..%2fx");
        assert_eq!(decode_path("/caf%C3%A9.js"), "/café.js");
    }

    #[test]
    fn aiohttp_default_texts() {
        assert_eq!(HttpError::plain(StatusCode::NOT_FOUND).text, "404: Not Found");
        assert_eq!(HttpError::plain(StatusCode::UNSUPPORTED_MEDIA_TYPE).text, "415: Unsupported Media Type");
        assert_eq!(HttpError::too_large(16384).text, "Maximum request body size 16384 exceeded.");
    }
}
