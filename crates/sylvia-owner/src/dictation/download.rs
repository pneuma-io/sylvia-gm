//! `urllib.request.urlopen(Request(url, headers={"User-Agent": ...}), timeout=30)` for
//! `prepare_model`, on the owner's urllib-faithful client (`crate::urllib`).
//!
//! `urlopen` builds one opener per process on its first call (`build_opener()`: the proxies of
//! that moment, `HTTPRedirectHandler`'s own limits and no extra check) and http.client's
//! default HTTPS context, which offers ALPN `http/1.1`. Its failures are `OSError`s
//! (`HTTPError`, `URLError`, `TimeoutError`), which `prepare_model` reports as `download`; an
//! `http.client.HTTPException` that is not one (a malformed response, `IncompleteRead`) escapes
//! it, and the worker answers `provider`.

use super::{Fetch, FetchError, Response};
use crate::urllib::{self, proxy::Proxies, trust, Failure, Interrupt, Opener};
use std::sync::{Arc, OnceLock};
use std::time::Duration;

/// `HTTPRedirectHandler.max_redirections` and `max_repeats`.
const MAX_REDIRECTIONS: usize = 10;
const MAX_REPEATS: usize = 4;

fn classify(failure: &Failure) -> FetchError {
    match failure {
        Failure::Http { .. } | Failure::Network(_) => FetchError::Os,
        Failure::Protocol(_) | Failure::Value(_) | Failure::Refused(_) => FetchError::Other,
    }
}

/// The process's `urlopen`.
pub struct UrllibFetch {
    runtime: Arc<tokio::runtime::Runtime>,
    /// The opener's proxies and TLS, built at the first call (`urllib.request._opener`);
    /// `None` when no certificate can anchor a chain.
    base: OnceLock<Option<urllib::Config>>,
    sources: Box<dyn Fn() -> trust::TrustSources>,
    proxies: Box<dyn Fn() -> Proxies>,
}

impl Default for UrllibFetch {
    fn default() -> Self {
        Self::with_sources(Box::new(trust::TrustSources::system), Box::new(Proxies::system))
    }
}

impl UrllibFetch {
    /// `urlopen` reading its trust and proxies from these (tests give their own).
    pub fn with_sources(
        sources: Box<dyn Fn() -> trust::TrustSources>,
        proxies: Box<dyn Fn() -> Proxies>,
    ) -> Self {
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(1)
            .thread_name("sylvia-dictation-download")
            .enable_all()
            .build()
            .expect("the download runtime starts");
        Self { runtime: Arc::new(runtime), base: OnceLock::new(), sources, proxies }
    }
}

struct Download {
    runtime: Arc<tokio::runtime::Runtime>,
    response: urllib::Response,
}

impl Response for Download {
    fn read(&mut self, limit: usize) -> Result<Vec<u8>, FetchError> {
        self.runtime.block_on(self.response.read(limit)).map_err(|failure| classify(&failure))
    }
}

impl Fetch for UrllibFetch {
    fn open(&mut self, url: &str, user_agent: &str, timeout_s: u64) -> Result<Box<dyn Response>, FetchError> {
        let (sources, proxies) = (&self.sources, &self.proxies);
        let base = self.base.get_or_init(|| {
            let trust = trust::DefaultTrust::new(&sources()).ok()?;
            let tls = trust::client_config(trust, true).ok()?;
            Some(urllib::Config {
                timeout: Duration::from_secs(timeout_s),
                headers: Vec::new(),
                max_redirections: MAX_REDIRECTIONS,
                max_repeats: MAX_REPEATS,
                check: None,
                proxies: proxies(),
                tls: Arc::new(tls),
            })
        });
        // No certificate can anchor a chain: every handshake fails, an `SSLError` (`OSError`).
        let Some(base) = base else { return Err(FetchError::Os) };
        let mut config = base.clone();
        config.headers = vec![("User-Agent".into(), user_agent.into())];
        config.timeout = Duration::from_secs(timeout_s);
        let opener = Opener::new(config);
        match self.runtime.block_on(opener.open(url, &Interrupt::default())) {
            Ok(response) => Ok(Box::new(Download { runtime: Arc::clone(&self.runtime), response })),
            Err(failure) => Err(classify(&failure)),
        }
    }
}
