//! `transport.py`'s `HttpsTransport`: the fixed official HTTPS transport, on the owner's
//! urllib-faithful client (`crate::urllib`).
//!
//! `__init__` builds `ssl.create_default_context()` once; `open` builds an opener per request
//! (`build_opener(_TrackedHttpsHandler(context, cancellation), OfficialRedirects())`, so the
//! proxies are read again each time), sends the updater's three headers with a 20 s timeout,
//! checks the final location, and maps urllib's failures as its `except` clauses do. The
//! cancellation interrupts only this request's sockets, while it opens and while the response
//! is read.

use super::transport::{
    check_cancelled, check_final_location, check_redirect, official_request, update_failed, Cancel, Response,
    Transport,
};
use super::update_error;
use crate::urllib::{self, proxy::Proxies, trust, Failure, Interrupt, Opener};
use std::sync::Arc;
use std::time::Duration;
use sylvia_base::{CodedError, CodedResult};

/// `opener.open(request, timeout=20)`.
const TIMEOUT: Duration = Duration::from_secs(20);

/// `OfficialRedirects.max_redirections` and `max_repeats`.
const MAX_REDIRECTIONS: usize = 5;
const MAX_REPEATS: usize = 2;

fn network_unavailable() -> CodedError {
    update_error("network_unavailable", "The update connection failed. You can retry safely.")
}

/// `HttpsTransport`.
pub struct HttpsTransport {
    runtime: tokio::runtime::Runtime,
    /// The TLS configuration, or `None` when no certificate can anchor a chain (every
    /// handshake then fails, as OpenSSL's would).
    tls: Option<Arc<rustls::ClientConfig>>,
    /// `ProxyHandler()`'s settings, read again for each request.
    proxies: Box<dyn Fn() -> Proxies + Send + Sync>,
}

impl HttpsTransport {
    /// `HttpsTransport()`: the default TLS context of this machine.
    pub fn new() -> Self {
        Self::with_trust(&trust::TrustSources::system())
    }

    /// The transport with the given trust (tests give their own certificate authority).
    pub fn with_trust(sources: &trust::TrustSources) -> Self {
        Self::with_settings(sources, Box::new(Proxies::system))
    }

    /// The transport with the given trust and proxy settings (tests give their own).
    pub fn with_settings(
        sources: &trust::TrustSources,
        proxies: Box<dyn Fn() -> Proxies + Send + Sync>,
    ) -> Self {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(1)
            .thread_name("sylvia-update-https")
            .enable_all()
            .build()
            .expect("the update transport's runtime starts");
        // `_TrackedHttpsHandler(context=...)` gives http.client its own context, so no ALPN.
        let tls = trust::DefaultTrust::new(sources)
            .ok()
            .and_then(|trust| trust::client_config(trust, false).ok())
            .map(Arc::new);
        Self { runtime, tls, proxies }
    }
}

impl Default for HttpsTransport {
    fn default() -> Self {
        Self::new()
    }
}

struct HttpsResponse<'a> {
    runtime: &'a tokio::runtime::Runtime,
    response: urllib::Response,
    headers: Vec<(String, String)>,
}

impl Response for HttpsResponse<'_> {
    fn headers(&self) -> &[(String, String)] {
        &self.headers
    }

    fn read(&mut self, maximum: usize) -> std::io::Result<Vec<u8>> {
        // `OSError` and `HTTPException` alike become `network_unavailable` in `_read`.
        self.runtime
            .block_on(self.response.read(maximum))
            .map_err(|failure| std::io::Error::other(format!("{failure:?}")))
    }
}

/// `HTTPError`: 429, or 403 with `X-RateLimit-Remaining: 0`, is rate limited.
fn http_error(code: u16, headers: &[(String, String)]) -> CodedError {
    let remaining =
        headers.iter().find(|(name, _)| name.eq_ignore_ascii_case("X-RateLimit-Remaining")).map(|(_, v)| v);
    if code == 429 || (code == 403 && remaining.is_some_and(|value| value == "0")) {
        return update_error("rate_limited", "The release service is rate limited. Try again later.");
    }
    update_error("service_unavailable", "The official release service is unavailable. Try again later.")
}

impl Transport for HttpsTransport {
    fn open<'a>(&'a self, url: &str, cancel: &Cancel) -> CodedResult<Box<dyn Response + 'a>> {
        official_request(url)?;
        // `cancellation.start()`.
        check_cancelled(cancel)?;
        let Some(tls) = &self.tls else {
            check_cancelled(cancel)?;
            return Err(network_unavailable());
        };
        let opener = Opener::new(urllib::Config {
            timeout: TIMEOUT,
            headers: vec![
                ("User-Agent".into(), "Sylvia-GM-updater/1".into()),
                ("Accept-Encoding".into(), "identity".into()),
                ("Accept".into(), "application/octet-stream".into()),
            ],
            max_redirections: MAX_REDIRECTIONS,
            max_repeats: MAX_REPEATS,
            check: Some(check_redirect),
            proxies: (self.proxies)(),
            tls: Arc::clone(tls),
        });
        let interrupt = Interrupt(cancel.flag());
        match self.runtime.block_on(opener.open(url, &interrupt)) {
            Ok(response) => {
                check_final_location(&response.url)?;
                let headers = response.headers.clone();
                Ok(Box::new(HttpsResponse { runtime: &self.runtime, response, headers }))
            }
            Err(Failure::Http { code, headers }) => {
                check_cancelled(cancel)?;
                Err(http_error(code, &headers))
            }
            Err(Failure::Network(_)) => {
                check_cancelled(cancel)?;
                Err(network_unavailable())
            }
            // `OfficialRedirects` raised an `UpdateError`; it leaves the transport as it is.
            Err(Failure::Refused(error)) => Err(error),
            // Python lets these escape the transport; the controller reports its generic failure.
            Err(Failure::Protocol(_) | Failure::Value(_)) => Err(update_failed()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rate_limits_are_recognised_as_python_does() {
        assert_eq!(http_error(429, &[]).code, "rate_limited");
        assert_eq!(http_error(403, &[("x-ratelimit-remaining".into(), "0".into())]).code, "rate_limited");
        assert_eq!(
            http_error(403, &[("X-RateLimit-Remaining".into(), "1".into())]).code,
            "service_unavailable"
        );
        assert_eq!(http_error(500, &[]).code, "service_unavailable");
    }
}
