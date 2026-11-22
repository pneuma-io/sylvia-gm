//! The network [`HttpTransport`]: reqwest configured the way the OpenAI SDK configures httpx.
//!
//! | httpx (as the SDK builds it) | here |
//! | --- | --- |
//! | HTTP/1.1 (no `h2` installed) | `http1_only` |
//! | `trust_env=True` proxies | [`crate::proxy::ProxyMap`], reqwest's own proxy lookup off |
//! | `SSL_CERT_FILE`, else `SSL_CERT_DIR`, else certifi | that file, that folder's certificates, else the bundled webpki roots (both Mozilla's set) |
//! | connect 15 s, read 120 s (each read) | `connect_timeout`, `read_timeout` from [`TimeoutConfig`] |
//! | keep-alive expiry 5 s, 100 idle connections | `pool_idle_timeout(5 s)`, `pool_max_idle_per_host(100)` |
//! | `follow_redirects=True`, at most 20 | `redirect::Policy::limited(20)` |
//! | a cookie jar per client | `cookie_store(true)` |
//! | header values decoded as ASCII, else UTF-8, else Latin-1 (one choice for all headers) | [`decode_headers`] |
//!
//! Not reproduced: httpx's write timeout (30 s per write) and pool timeout (5 s waiting for a
//! connection); reqwest has neither. A request body is sent in one piece, and the pool has no
//! wait limit. Header names go out lowercase (HTTP/1.1 names are case-insensitive); see the
//! `differences.md` row for request headers.

use crate::http::{BodyStream, HttpRequest, HttpResponse, HttpTransport, TransportError};
use crate::httpx_url::HttpxUrl;
use crate::provider::BoxFuture;
use crate::proxy::{ProxyError, ProxyMap, ProxySource};
use crate::types::TimeoutConfig;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// Where trusted certificates come from (`httpx.create_ssl_context(trust_env=True)`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TrustSource {
    /// certifi in Python; the bundled webpki roots here.
    Bundled,
    /// `SSL_CERT_FILE`: a PEM bundle, read when the client is built.
    File(PathBuf),
    /// `SSL_CERT_DIR`: every certificate in the folder.
    Folder(PathBuf),
}

impl TrustSource {
    /// `SSL_CERT_FILE` if set and non-empty, else `SSL_CERT_DIR`, else the bundled roots.
    pub fn from_environment() -> Self {
        let get = |name: &str| std::env::var_os(name).filter(|value| !value.is_empty());
        if let Some(file) = get("SSL_CERT_FILE") {
            Self::File(file.into())
        } else if let Some(folder) = get("SSL_CERT_DIR") {
            Self::Folder(folder.into())
        } else {
            Self::Bundled
        }
    }
}

/// What the client is built from.
#[derive(Debug, Clone)]
pub struct ClientSettings {
    pub timeouts: TimeoutConfig,
    pub proxies: ProxySource,
    pub trust: TrustSource,
}

impl ClientSettings {
    /// The process's environment, as the SDK reads it when it builds its client.
    pub fn from_system(timeouts: TimeoutConfig) -> Self {
        Self { timeouts, proxies: ProxySource::from_system(), trust: TrustSource::from_environment() }
    }
}

fn certificates(trust: &TrustSource) -> Result<Option<Vec<reqwest::Certificate>>, ProxyError> {
    // Python raises from ssl.load_verify_locations (FileNotFoundError, SSLError): not a
    // ValueError, so an internal error.
    let bundle = |bytes: &[u8]| {
        reqwest::Certificate::from_pem_bundle(bytes)
            .map_err(|_| ProxyError::Internal("[X509] no certificate or crl found".into()))
    };
    match trust {
        TrustSource::Bundled => Ok(None),
        TrustSource::File(path) => {
            let bytes = std::fs::read(path).map_err(|error| ProxyError::Internal(error.to_string()))?;
            Ok(Some(bundle(&bytes)?))
        }
        TrustSource::Folder(path) => {
            // OpenSSL looks certificates up by hash name when it verifies; a missing or empty
            // folder only fails then. Every readable certificate in the folder is trusted.
            let mut found = Vec::new();
            if let Ok(entries) = std::fs::read_dir(path) {
                for entry in entries.flatten() {
                    if let Ok(bytes) = std::fs::read(entry.path()) {
                        if let Ok(certificates) = reqwest::Certificate::from_pem_bundle(&bytes) {
                            found.extend(certificates);
                        }
                    }
                }
            }
            Ok(Some(found))
        }
    }
}

fn build_client(settings: &ClientSettings) -> Result<reqwest::Client, ProxyError> {
    let proxies = Arc::new(ProxyMap::from_source(&settings.proxies)?);
    let lookup = Arc::clone(&proxies);
    let proxy = reqwest::Proxy::custom(move |url| {
        let parsed = HttpxUrl::parse(url.as_str()).ok()?;
        let target = lookup.proxy_for(&parsed)?;
        let mut proxy_url = reqwest::Url::parse(&target.url).ok()?;
        if let Some((user, password)) = &target.auth {
            proxy_url.set_username(user).ok()?;
            proxy_url.set_password(Some(password)).ok()?;
        }
        Some(proxy_url)
    });
    let seconds = |value: f64| Duration::from_secs_f64(value.max(0.0));
    let mut builder = reqwest::Client::builder()
        .http1_only()
        .no_proxy()
        .proxy(proxy)
        .connect_timeout(seconds(settings.timeouts.connect))
        .read_timeout(seconds(settings.timeouts.read))
        .pool_idle_timeout(Duration::from_secs(5))
        .pool_max_idle_per_host(100)
        .redirect(reqwest::redirect::Policy::limited(20))
        .cookie_store(true);
    if let Some(certificates) = certificates(&settings.trust)? {
        builder = builder.tls_built_in_root_certs(false);
        for certificate in certificates {
            builder = builder.add_root_certificate(certificate);
        }
    }
    builder.build().map_err(|error| ProxyError::Internal(error.to_string()))
}

/// httpx's `Headers.encoding`: ASCII if every name and value is ASCII, else UTF-8 if every one
/// is valid UTF-8, else Latin-1, then every header decoded with that one choice.
pub fn decode_headers(raw: &[(Vec<u8>, Vec<u8>)]) -> Vec<(String, String)> {
    let all = |check: fn(&[u8]) -> bool| raw.iter().all(|(name, value)| check(name) && check(value));
    let latin1 = |bytes: &[u8]| bytes.iter().map(|byte| char::from(*byte)).collect::<String>();
    if all(|bytes| bytes.is_ascii()) || all(|bytes| std::str::from_utf8(bytes).is_ok()) {
        raw.iter()
            .map(|(name, value)| {
                (String::from_utf8_lossy(name).into_owned(), String::from_utf8_lossy(value).into_owned())
            })
            .collect()
    } else {
        raw.iter().map(|(name, value)| (latin1(name), latin1(value))).collect()
    }
}

fn transport_error(error: &reqwest::Error) -> TransportError {
    if error.is_timeout() {
        TransportError::Timeout(error.to_string())
    } else {
        TransportError::Connection(error.to_string())
    }
}

struct NetworkBody {
    response: reqwest::Response,
}

impl BodyStream for NetworkBody {
    fn next_piece(&mut self) -> BoxFuture<'_, Option<Result<Vec<u8>, TransportError>>> {
        Box::pin(async move {
            match self.response.chunk().await {
                Ok(Some(bytes)) => Some(Ok(bytes.to_vec())),
                Ok(None) => None,
                Err(error) => Some(Err(transport_error(&error))),
            }
        })
    }
}

/// The network transport. Cheap to share; one connection pool and cookie jar per instance.
pub struct NetworkTransport {
    settings: ClientSettings,
    client: Mutex<reqwest::Client>,
}

impl NetworkTransport {
    /// Build the client now, as the SDK builds httpx's when the provider is created.
    pub fn new(settings: ClientSettings) -> Result<Self, ProxyError> {
        let client = build_client(&settings)?;
        Ok(Self { settings, client: Mutex::new(client) })
    }

    fn client(&self) -> reqwest::Client {
        self.client.lock().expect("client lock").clone()
    }
}

impl HttpTransport for NetworkTransport {
    fn send(&self, request: HttpRequest) -> BoxFuture<'_, Result<HttpResponse, TransportError>> {
        let client = self.client();
        Box::pin(async move {
            let method = reqwest::Method::from_bytes(request.method.as_bytes())
                .map_err(|error| TransportError::Connection(error.to_string()))?;
            let mut headers = reqwest::header::HeaderMap::new();
            for (name, value) in &request.headers {
                let name = reqwest::header::HeaderName::from_bytes(name.as_bytes())
                    .map_err(|error| TransportError::Connection(error.to_string()))?;
                let value = reqwest::header::HeaderValue::from_bytes(value.as_bytes())
                    .map_err(|error| TransportError::Connection(error.to_string()))?;
                headers.append(name, value);
            }
            let response = client
                .request(method, &request.url)
                .headers(headers)
                .body(request.body)
                .send()
                .await
                .map_err(|error| transport_error(&error))?;
            let raw: Vec<(Vec<u8>, Vec<u8>)> = response
                .headers()
                .iter()
                .map(|(name, value)| (name.as_str().as_bytes().to_vec(), value.as_bytes().to_vec()))
                .collect();
            Ok(HttpResponse {
                status: response.status().as_u16(),
                headers: decode_headers(&raw),
                body: Box::new(NetworkBody { response }),
            })
        })
    }

    fn recycle(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move {
            // Python builds a whole new SDK client: new pool, new cookie jar, proxies read again.
            // If that fails here the old client stays (Python would raise from the recycle).
            let fresh = ClientSettings {
                proxies: ProxySource::from_system(),
                trust: TrustSource::from_environment(),
                ..self.settings.clone()
            };
            if let Ok(client) = build_client(&fresh) {
                *self.client.lock().expect("client lock") = client;
            }
        })
    }
}
