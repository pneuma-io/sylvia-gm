//! An HTTPS client that behaves as CPython 3.12's `urllib.request` opener does, for the two
//! places Python uses it: the desktop updater's `HttpsTransport` (`build_opener` with its own
//! redirect handler) and dictation's model download (`urlopen`).
//!
//! What is reproduced, step by step:
//! - `Request`: the fragment dropped, the host and selector split (`_splithost`), the given
//!   header names capitalised (`add_header`), then `do_request_`'s `Host` and `do_open`'s
//!   title-cased header dictionary with `Connection: close`; http.client writes
//!   `Accept-Encoding: identity` first when the caller did not give one.
//! - `ProxyHandler`: the `https` entry of `getproxies()` unless `proxy_bypass(host)` (the
//!   environment's `no_proxy`, or the registry's `ProxyOverride`), `_parse_proxy`, Basic
//!   `Proxy-Authorization` from the proxy URL's credentials, and http.client's
//!   `CONNECT host:port HTTP/1.1` tunnel (whatever the proxy URL's scheme), port 443 by default
//!   for both the proxy and the target.
//! - The connection: each resolved address tried in turn with the timeout, TLS with the host as
//!   server name and `ssl.create_default_context()`'s trust ([`trust`]), HTTP/1.1.
//! - `HTTPRedirectHandler.http_error_302`: the first `Location` (else `URI`) header, only
//!   http/https/ftp/relative, an empty path made `/`, `quote(..., "iso-8859-1",
//!   safe=string.punctuation)`, `urljoin`, then the caller's check (`OfficialRedirects`), then
//!   loop detection with the handler's `max_repeats` and `max_redirections`.
//! - The response: 2xx returned with its final URL; anything else is `HTTPError`.
//!
//! Failures keep the class Python raises ([`Failure`]), because each caller maps the classes
//! differently.

pub mod proxy;
pub mod trust;

use bytes::Bytes;
use http_body_util::{BodyExt, Empty};
use hyper::body::Incoming;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use sylvia_base::pyurl;
use sylvia_base::CodedError;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;

/// What a request ended with, by the class of the Python exception.
#[derive(Debug, Clone, PartialEq)]
pub enum Failure {
    /// `HTTPError`: a final status outside 2xx (or a redirect urllib refuses).
    Http {
        /// The status.
        code: u16,
        /// The response headers.
        headers: Vec<(String, String)>,
    },
    /// An `OSError` (`URLError`, `TimeoutError`, `ConnectionError`, a TLS error), by message.
    Network(String),
    /// An `http.client.HTTPException` that is not an `OSError` (a malformed response).
    Protocol(String),
    /// A `ValueError` (an unparseable redirect location or proxy).
    Value(String),
    /// The redirect check refused (`OfficialRedirects` raises `UpdateError`).
    Refused(CodedError),
}

/// A redirect check run on each new location before it is followed.
pub type RedirectCheck = fn(&str) -> Result<(), CodedError>;

/// The opener's fixed settings.
#[derive(Clone)]
pub struct Config {
    /// The socket timeout (each connect, handshake and read).
    pub timeout: Duration,
    /// The request headers as the caller gives them to `Request`.
    pub headers: Vec<(String, String)>,
    /// `HTTPRedirectHandler.max_redirections` (10, or the subclass's).
    pub max_redirections: usize,
    /// `HTTPRedirectHandler.max_repeats` (4, or the subclass's).
    pub max_repeats: usize,
    /// The subclass's `redirect_request` check, if any.
    pub check: Option<RedirectCheck>,
    /// The proxies and their bypass rules.
    pub proxies: proxy::Proxies,
    /// TLS.
    pub tls: Arc<rustls::ClientConfig>,
}

/// Interrupts the request's sockets, as the updater's `_ConnectionCancellation` does.
#[derive(Clone, Default, Debug)]
pub struct Interrupt(pub Arc<AtomicBool>);

impl Interrupt {
    fn is_set(&self) -> bool {
        self.0.load(Ordering::SeqCst)
    }
}

/// `Request(url)`'s parts.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Target {
    /// `full_url` without its fragment.
    pub full_url: String,
    /// `type`.
    pub scheme: String,
    /// `host`, unquoted.
    pub host: String,
    /// `selector` (`/` when empty, as http.client sends it).
    pub selector: String,
}

/// `Request(url)`: `_splittag`, `_splittype`, `_splithost` and `unquote(host)`.
pub fn target(url: &str) -> Result<Target, Failure> {
    let full_url = url.split_once('#').map_or(url, |(before, _)| before).to_string();
    // `_splittype`: `^([^/:]+):(.*)`, the scheme lowercased.
    let Some((scheme, rest)) =
        full_url.split_once(':').filter(|(scheme, _)| !scheme.is_empty() && !scheme.contains('/'))
    else {
        return Err(Failure::Value(format!("unknown url type: {}", sylvia_base::pystr::repr(&full_url))));
    };
    // `_splithost`: `//([^/#?]*)(.*)`.
    let (host, selector) = match rest.strip_prefix("//") {
        Some(after) => {
            let end = after.find(['/', '#', '?']).unwrap_or(after.len());
            (after[..end].to_string(), after[end..].to_string())
        }
        None => (String::new(), rest.to_string()),
    };
    let host = pyurl::unquote_strict(&host).map_err(|error| Failure::Value(error.0))?;
    let selector = if selector.is_empty() { "/".to_string() } else { selector };
    Ok(Target { full_url: full_url.clone(), scheme: scheme.to_ascii_lowercase(), host, selector })
}

/// `str.capitalize()` for an ASCII header name: first letter upper, the rest lower.
fn capitalize(name: &str) -> String {
    let lower = name.to_ascii_lowercase();
    let mut characters = lower.chars();
    characters
        .next()
        .map_or_else(String::new, |first| first.to_ascii_uppercase().to_string() + characters.as_str())
}

/// `str.title()` for an ASCII header name: upper after each non-letter.
fn title(name: &str) -> String {
    let mut out = String::with_capacity(name.len());
    let mut start = true;
    for character in name.chars() {
        if character.is_ascii_alphabetic() {
            out.push(if start { character.to_ascii_uppercase() } else { character.to_ascii_lowercase() });
            start = false;
        } else {
            out.push(character);
            start = true;
        }
    }
    out
}

/// The header lines urllib and http.client send, in order, for `host`, the caller's headers and
/// a plain-HTTP proxy's `Proxy-authorization` (added after them by `proxy_open`).
pub fn request_headers(
    host: &str,
    given: &[(String, String)],
    proxy_authorization: Option<&str>,
) -> Vec<(String, String)> {
    // Request.add_header: keys capitalised, later duplicates replace earlier ones in place.
    let mut headers: Vec<(String, String)> = Vec::new();
    for (name, value) in given {
        let key = capitalize(name);
        match headers.iter_mut().find(|(existing, _)| *existing == key) {
            Some(entry) => entry.1 = value.clone(),
            None => headers.push((key, value.clone())),
        }
    }
    if let Some(credentials) = proxy_authorization {
        let key = "Proxy-authorization".to_string();
        match headers.iter_mut().find(|(existing, _)| *existing == key) {
            Some(entry) => entry.1 = credentials.to_string(),
            None => headers.push((key, credentials.to_string())),
        }
    }
    // do_request_: unredirected Host (and the opener's default User-agent if none was given).
    let mut unredirected = Vec::new();
    if !headers.iter().any(|(name, _)| name == "Host") {
        unredirected.push(("Host".to_string(), host.to_string()));
    }
    if !headers.iter().any(|(name, _)| name == "User-agent") {
        unredirected.push(("User-agent".to_string(), "Python-urllib/3.12".to_string()));
    }
    // do_open: unredirected first, then the request's own, then Connection: close; title-cased.
    let mut merged: Vec<(String, String)> = unredirected;
    for (name, value) in headers {
        if !merged.iter().any(|(existing, _)| *existing == name) {
            merged.push((name, value));
        }
    }
    match merged.iter_mut().find(|(name, _)| name == "Connection") {
        Some(entry) => entry.1 = "close".into(),
        None => merged.push(("Connection".into(), "close".into())),
    }
    let mut titled: Vec<(String, String)> = Vec::new();
    for (name, value) in merged {
        let key = title(&name);
        match titled.iter_mut().find(|(existing, _)| *existing == key) {
            Some(entry) => entry.1 = value,
            None => titled.push((key, value)),
        }
    }
    // http.client.putrequest: Accept-Encoding: identity first unless the headers have one.
    if !titled.iter().any(|(name, _)| name.eq_ignore_ascii_case("accept-encoding")) {
        titled.insert(0, ("Accept-Encoding".into(), "identity".into()));
    }
    titled
}

/// `http.client._get_hostport(host, default)`: 443 for `HTTPSConnection`, 80 for `HTTPConnection`.
pub fn host_port(host: &str, default: u16) -> Result<(String, u16), Failure> {
    let colon = host.rfind(':');
    let bracket = host.rfind(']');
    let (name, port) = match colon {
        Some(at) if bracket.is_none_or(|close| at > close) => {
            let port = &host[at + 1..];
            if port.is_empty() {
                (&host[..at], default)
            } else {
                let number = port
                    .parse::<u16>()
                    .ok()
                    .filter(|_| port.bytes().all(|b| b.is_ascii_digit()))
                    .ok_or_else(|| Failure::Protocol(format!("nonnumeric port: '{port}'")))?;
                (&host[..at], number)
            }
        }
        _ => (host, default),
    };
    let name = name.strip_prefix('[').and_then(|inner| inner.strip_suffix(']')).unwrap_or(name);
    Ok((name.to_string(), port))
}

/// Latin-1 text of raw header bytes (`http.client.parse_headers` decodes ISO-8859-1).
fn latin1(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| char::from(*byte)).collect()
}

fn header<'a>(headers: &'a [(String, String)], name: &str) -> Option<&'a str> {
    headers.iter().find(|(key, _)| key.eq_ignore_ascii_case(name)).map(|(_, value)| value.as_str())
}

/// One response being read.
pub struct Response {
    /// The final URL (`geturl()`).
    pub url: String,
    /// The status.
    pub status: u16,
    /// The headers, decoded as Latin-1, in order.
    pub headers: Vec<(String, String)>,
    body: Incoming,
    pending: Vec<u8>,
    done: bool,
    length_known: bool,
    timeout: Duration,
    interrupt: Interrupt,
}

async fn until_interrupted(interrupt: &Interrupt) {
    loop {
        if interrupt.is_set() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}

/// Run one socket operation with the timeout and the interrupt (a shut-down socket's read
/// raises `OSError`).
async fn step<T>(
    future: impl std::future::Future<Output = T>,
    timeout: Duration,
    interrupt: &Interrupt,
) -> Result<T, Failure> {
    tokio::select! {
        result = tokio::time::timeout(timeout, future) => result.map_err(|_| Failure::Network("timed out".into())),
        () = until_interrupted(interrupt) => Err(Failure::Network("interrupted".into())),
    }
}

impl Response {
    /// `response.read(maximum)`: up to `maximum` bytes, fewer only at the end.
    pub async fn read(&mut self, maximum: usize) -> Result<Vec<u8>, Failure> {
        while self.pending.len() < maximum && !self.done {
            let frame = step(self.body.frame(), self.timeout, &self.interrupt).await?;
            match frame {
                None => self.done = true,
                Some(Ok(frame)) => {
                    if let Ok(data) = frame.into_data() {
                        self.pending.extend_from_slice(&data);
                    }
                }
                Some(Err(error)) => {
                    // A body cut short: http.client's fixed-length read returns what it has and
                    // then nothing; a chunked read raises IncompleteRead.
                    if self.length_known && ended_early(&error) {
                        self.done = true;
                    } else {
                        return Err(Failure::Protocol(error.to_string()));
                    }
                }
            }
        }
        let take = maximum.min(self.pending.len());
        Ok(self.pending.drain(..take).collect())
    }
}

/// The opener.
pub struct Opener {
    config: Config,
}

impl Opener {
    /// An opener with these settings.
    pub fn new(config: Config) -> Self {
        Self { config }
    }

    /// `opener.open(Request(url, headers=...), timeout=...)`.
    pub async fn open(&self, url: &str, interrupt: &Interrupt) -> Result<Response, Failure> {
        let mut url = url.to_string();
        let mut visited: Option<Vec<(String, usize)>> = None;
        loop {
            let response = self.exchange(&url, interrupt).await?;
            if (200..300).contains(&response.status) {
                return Ok(response);
            }
            let code = response.status;
            let headers = response.headers.clone();
            if !matches!(code, 301 | 302 | 303 | 307 | 308) {
                return Err(Failure::Http { code, headers });
            }
            // http_error_302
            let Some(location) = header(&headers, "location").or_else(|| header(&headers, "uri")) else {
                return Err(Failure::Http { code, headers });
            };
            let mut parts = pyurl::urlparse(location, "").map_err(|error| Failure::Value(error.0))?;
            if !matches!(parts.scheme.as_str(), "http" | "https" | "ftp" | "") {
                return Err(Failure::Http { code, headers });
            }
            if parts.path.is_empty() && !parts.netloc.is_empty() {
                parts.path = "/".into();
            }
            let quoted = pyurl::quote_latin1(&pyurl::urlunparse(&parts), pyurl::PUNCTUATION)
                .map_err(|error| Failure::Value(error.0))?;
            let current = target(&url)?;
            let new_url =
                pyurl::urljoin(&current.full_url, &quoted).map_err(|error| Failure::Value(error.0))?;
            // redirect_request: the subclass's check, then the base class's own.
            if let Some(check) = self.config.check {
                check(&new_url).map_err(Failure::Refused)?;
            }
            let following = new_url.replace(' ', "%20");
            // Loop detection, counted by the location before the space replacement.
            match visited.as_mut() {
                Some(seen) => {
                    let count = seen.iter().find(|(seen_url, _)| *seen_url == new_url).map_or(0, |(_, n)| *n);
                    if count >= self.config.max_repeats || seen.len() >= self.config.max_redirections {
                        return Err(Failure::Http { code, headers });
                    }
                    match seen.iter_mut().find(|(seen_url, _)| *seen_url == new_url) {
                        Some(entry) => entry.1 += 1,
                        None => seen.push((new_url.clone(), 1)),
                    }
                }
                None => visited = Some(vec![(new_url.clone(), 1)]),
            }
            url = following;
        }
    }

    /// One request and its response head.
    async fn exchange(&self, url: &str, interrupt: &Interrupt) -> Result<Response, Failure> {
        let request = target(url)?;
        let secure = match request.scheme.as_str() {
            "https" => true,
            "http" => false,
            // No handler for the type (`URLError("unknown url type: ...")`).
            other => return Err(Failure::Network(format!("unknown url type: {other}"))),
        };
        let default_port = if secure { 443 } else { 80 };
        let timeout = self.config.timeout;
        let route = self.config.proxies.route(&request.scheme, &request.host)?;
        let (connect_host, connect_port) = match &route {
            Some(proxy) => host_port(&proxy.hostport, default_port)?,
            None => host_port(&request.host, default_port)?,
        };
        let mut stream = connect(&connect_host, connect_port, timeout, interrupt).await?;
        let mut selector = request.selector.clone();
        let mut proxy_authorization = None;
        let io: Box<dyn Io> = if secure {
            let (tls_host, tls_port) = host_port(&request.host, default_port)?;
            if let Some(proxy) = &route {
                tunnel(&mut stream, &tls_host, tls_port, proxy.authorization.as_deref(), timeout, interrupt)
                    .await?;
            }
            let server_name = rustls::pki_types::ServerName::try_from(tls_host.clone())
                .map_err(|error| Failure::Network(error.to_string()))?;
            let connector = tokio_rustls::TlsConnector::from(Arc::clone(&self.config.tls));
            let tls = step(connector.connect(server_name, stream), timeout, interrupt)
                .await?
                .map_err(|error| Failure::Network(error.to_string()))?;
            Box::new(tls)
        } else {
            if let Some(proxy) = &route {
                // `req.set_proxy`: the selector becomes the whole URL, the credentials a header.
                selector = request.full_url.clone();
                proxy_authorization = proxy.authorization.clone();
            }
            Box::new(stream)
        };
        let io = hyper_util::rt::TokioIo::new(io);
        let (mut sender, connection) = step(
            hyper::client::conn::http1::Builder::new()
                .title_case_headers(true)
                .handshake::<_, Empty<Bytes>>(io),
            timeout,
            interrupt,
        )
        .await?
        .map_err(|error| Failure::Network(error.to_string()))?;
        tokio::spawn(async move {
            let _ = connection.await;
        });
        let mut builder = hyper::Request::builder().method("GET").uri(selector.as_str());
        for (name, value) in
            request_headers(&request.host, &self.config.headers, proxy_authorization.as_deref())
        {
            builder = builder.header(name, value);
        }
        let outgoing =
            builder.body(Empty::<Bytes>::new()).map_err(|error| Failure::Value(error.to_string()))?;
        let response = step(sender.send_request(outgoing), timeout, interrupt).await?.map_err(|error| {
            if error.is_incomplete_message() || error.is_canceled() || error.is_closed() {
                // RemoteDisconnected (a ConnectionError).
                Failure::Network(error.to_string())
            } else if error.is_parse() {
                Failure::Protocol(error.to_string())
            } else {
                Failure::Network(error.to_string())
            }
        })?;
        let status = response.status().as_u16();
        let headers: Vec<(String, String)> = response
            .headers()
            .iter()
            .map(|(name, value)| (latin1(name.as_str().as_bytes()), latin1(value.as_bytes())))
            .collect();
        let length_known = header(&headers, "content-length").is_some();
        Ok(Response {
            url: request.full_url,
            status,
            headers,
            body: response.into_body(),
            pending: Vec::new(),
            done: false,
            length_known,
            timeout,
            interrupt: interrupt.clone(),
        })
    }
}

/// A connection hyper can run over: TLS or plain TCP.
trait Io: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin + Send {}
impl<T: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin + Send> Io for T {}

/// `socket.create_connection((host, port), timeout)`: each address in turn.
async fn connect(
    host: &str,
    port: u16,
    timeout: Duration,
    interrupt: &Interrupt,
) -> Result<TcpStream, Failure> {
    let addresses: Vec<std::net::SocketAddr> =
        step(tokio::net::lookup_host((host, port)), timeout, interrupt)
            .await?
            .map_err(|error| Failure::Network(error.to_string()))?
            .collect();
    let mut last = Failure::Network("getaddrinfo returns an empty list".into());
    for address in addresses {
        match step(TcpStream::connect(address), timeout, interrupt).await {
            Ok(Ok(stream)) => return Ok(stream),
            Ok(Err(error)) => last = Failure::Network(error.to_string()),
            Err(failure) => last = failure,
        }
    }
    Err(last)
}

/// http.client's `_tunnel`: `CONNECT host:port HTTP/1.1`, the proxy credentials, `Host`, then the
/// proxy's status line (anything but 200 is `OSError("Tunnel connection failed: ...")`).
async fn tunnel(
    stream: &mut TcpStream,
    host: &str,
    port: u16,
    authorization: Option<&str>,
    timeout: Duration,
    interrupt: &Interrupt,
) -> Result<(), Failure> {
    let target = if host.contains(':') { format!("[{host}]:{port}") } else { format!("{host}:{port}") };
    let mut head = format!("CONNECT {target} HTTP/1.1\r\n");
    if let Some(credentials) = authorization {
        head.push_str(&format!("Proxy-Authorization: {credentials}\r\n"));
    }
    head.push_str(&format!("Host: {target}\r\n\r\n"));
    step(stream.write_all(head.as_bytes()), timeout, interrupt)
        .await?
        .map_err(|e| Failure::Network(e.to_string()))?;
    // Read the reply head byte by byte so no tunnelled byte is consumed.
    let mut reply = Vec::new();
    while !reply.ends_with(b"\r\n\r\n") && !reply.ends_with(b"\n\n") {
        let mut byte = [0_u8; 1];
        let read = step(stream.read(&mut byte), timeout, interrupt)
            .await?
            .map_err(|e| Failure::Network(e.to_string()))?;
        if read == 0 {
            return Err(Failure::Network("Remote end closed connection without response".into()));
        }
        reply.push(byte[0]);
        if reply.len() > 65_536 {
            return Err(Failure::Protocol("got more than 65536 bytes when reading header line".into()));
        }
    }
    let text = latin1(&reply);
    let status_line = text.lines().next().unwrap_or_default();
    let mut words = status_line.splitn(3, ' ');
    let version = words.next().unwrap_or_default();
    let code = words.next().unwrap_or_default();
    let reason = words.next().unwrap_or_default();
    if !version.starts_with("HTTP/") || code.len() != 3 || !code.bytes().all(|b| b.is_ascii_digit()) {
        return Err(Failure::Protocol(sylvia_base::pystr::repr(status_line)));
    }
    if code != "200" {
        return Err(Failure::Network(format!(
            "Tunnel connection failed: {code} {}",
            sylvia_base::pystr::strip(reason)
        )));
    }
    Ok(())
}

/// Whether a body error is the connection ending before the declared length (hyper wraps an
/// `UnexpectedEof`), which http.client's fixed-length read meets as the end of the data.
fn ended_early(error: &hyper::Error) -> bool {
    let mut source: Option<&(dyn std::error::Error + 'static)> = std::error::Error::source(error);
    while let Some(cause) = source {
        if cause
            .downcast_ref::<std::io::Error>()
            .is_some_and(|io| io.kind() == std::io::ErrorKind::UnexpectedEof)
        {
            return true;
        }
        source = cause.source();
    }
    error.is_incomplete_message()
}
