//! `ssl.create_default_context()`'s trust, as CPython 3.12 builds it on Windows: every certificate
//! of the current user's "CA" and "ROOT" system stores that is X.509 and either trusted for all
//! purposes or for server authentication (`load_default_certs`, `_load_windows_store_certs`),
//! then OpenSSL's default verify paths (`set_default_verify_paths`: `SSL_CERT_FILE`, else the
//! compiled-in file; `SSL_CERT_DIR`, else the compiled-in folder).
//!
//! Every one of those certificates goes into OpenSSL's trust store, and CPython 3.12 does not
//! set `X509_V_FLAG_PARTIAL_CHAIN`: a chain is accepted only when it ends at a self-signed
//! certificate of the store, and the store's other certificates (the "CA" store's
//! intermediates) help build the chain without being anchors themselves. Here the self-signed
//! ones are the verifier's anchors and the others are added to the intermediates the server
//! sent, which webpki then uses the same way.

use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::client::WebPkiServerVerifier;
use rustls::pki_types::pem::PemObject;
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{DigitallySignedStruct, RootCertStore, SignatureScheme};
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// OpenSSL's compiled-in defaults (`ssl.get_default_verify_paths()`): the CPython 3.12.11 Windows
/// build's, and Ubuntu 24.04's system OpenSSL 3.0.13 under its CPython 3.12.3. On Linux
/// `load_default_certs` has no system store and reads only these.
#[cfg(windows)]
const OPENSSL_CAFILE: &str = r"C:\Program Files\Common Files\SSL/cert.pem";
#[cfg(windows)]
const OPENSSL_CAPATH: &str = r"C:\Program Files\Common Files\SSL/certs";
#[cfg(not(windows))]
const OPENSSL_CAFILE: &str = "/usr/lib/ssl/cert.pem";
#[cfg(not(windows))]
const OPENSSL_CAPATH: &str = "/usr/lib/ssl/certs";

/// The server-authentication purpose (`ssl.Purpose.SERVER_AUTH.oid`).
pub const SERVER_AUTH: &str = "1.3.6.1.5.5.7.3.1";

/// Where the trusted certificates come from; tests give their own.
#[derive(Debug, Clone, Default)]
pub struct TrustSources {
    /// The system stores' certificates, `(DER, trusted for all purposes, purpose OIDs)`.
    pub system: Vec<(Vec<u8>, bool, Vec<String>)>,
    /// `SSL_CERT_FILE` or the compiled-in file.
    pub cafile: PathBuf,
    /// `SSL_CERT_DIR` or the compiled-in folder.
    pub capath: PathBuf,
}

impl TrustSources {
    /// This process's stores and environment, as `ssl.create_default_context()` reads them.
    pub fn system() -> Self {
        let get = |name: &str| std::env::var_os(name).filter(|value| !value.is_empty()).map(PathBuf::from);
        Self {
            system: system_stores(),
            cafile: get("SSL_CERT_FILE").unwrap_or_else(|| OPENSSL_CAFILE.into()),
            capath: get("SSL_CERT_DIR").unwrap_or_else(|| OPENSSL_CAPATH.into()),
        }
    }
}

#[cfg(windows)]
fn system_stores() -> Vec<(Vec<u8>, bool, Vec<String>)> {
    use schannel::cert_context::ValidUses;
    use schannel::cert_store::CertStore;
    let mut found = Vec::new();
    for name in ["CA", "ROOT"] {
        // `enum_certificates` opens the current user's system store; a store that cannot be
        // opened is skipped, as Python's `except PermissionError` warns and goes on.
        let Ok(store) = CertStore::open_current_user(name) else { continue };
        for certificate in store.certs() {
            let (all, purposes) = match certificate.valid_uses() {
                Ok(ValidUses::All) => (true, Vec::new()),
                Ok(ValidUses::Oids(oids)) => (false, oids),
                Err(_) => continue,
            };
            found.push((certificate.to_der().to_vec(), all, purposes));
        }
    }
    found
}

#[cfg(not(windows))]
fn system_stores() -> Vec<(Vec<u8>, bool, Vec<String>)> {
    Vec::new()
}

/// One DER element: `(tag, content, rest)`.
fn der(bytes: &[u8]) -> Option<(u8, &[u8], &[u8])> {
    let tag = *bytes.first()?;
    let first = *bytes.get(1)?;
    let (length, header) = if first < 0x80 {
        (usize::from(first), 2)
    } else {
        let count = usize::from(first & 0x7F);
        if count == 0 || count > 4 {
            return None;
        }
        let mut length = 0_usize;
        for byte in bytes.get(2..2 + count)? {
            length = (length << 8) | usize::from(*byte);
        }
        (length, 2 + count)
    };
    let content = bytes.get(header..header + length)?;
    Some((tag, content, &bytes[header + length..]))
}

/// Whether a certificate's issuer and subject names are the same bytes (self-issued, the
/// anchors OpenSSL ends a chain at).
pub fn self_issued(certificate: &[u8]) -> bool {
    let issuer_and_subject = || -> Option<(Vec<u8>, Vec<u8>)> {
        let (_, certificate, _) = der(certificate)?;
        let (_, mut tbs, _) = der(certificate)?;
        // [0] version, serial, signature algorithm, issuer, validity, subject.
        if tbs.first() == Some(&0xA0) {
            tbs = der(tbs)?.2;
        }
        let (_, _, rest) = der(tbs)?;
        let (_, _, rest) = der(rest)?;
        let (_, issuer, rest) = der(rest)?;
        let (_, _, rest) = der(rest)?;
        let (_, subject, _) = der(rest)?;
        Some((issuer.to_vec(), subject.to_vec()))
    };
    issuer_and_subject().is_some_and(|(issuer, subject)| issuer == subject)
}

fn pem_certificates(path: &Path) -> Vec<Vec<u8>> {
    let Ok(bytes) = std::fs::read(path) else { return Vec::new() };
    CertificateDer::pem_slice_iter(&bytes).filter_map(Result::ok).map(|der| der.to_vec()).collect()
}

/// A verifier with OpenSSL's chain rule over `TrustSources`.
#[derive(Debug)]
pub struct DefaultTrust {
    inner: Arc<WebPkiServerVerifier>,
    helpers: Vec<CertificateDer<'static>>,
}

/// No certificate could be used as an anchor (OpenSSL would fail every handshake).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct NoAnchors;

impl DefaultTrust {
    /// Build the verifier from the sources.
    pub fn new(sources: &TrustSources) -> Result<Self, NoAnchors> {
        let mut certificates: Vec<Vec<u8>> = sources
            .system
            .iter()
            .filter(|(_, all, purposes)| *all || purposes.iter().any(|oid| oid == SERVER_AUTH))
            .map(|(der, _, _)| der.clone())
            .collect();
        certificates.extend(pem_certificates(&sources.cafile));
        if let Ok(entries) = std::fs::read_dir(&sources.capath) {
            for entry in entries.flatten() {
                certificates.extend(pem_certificates(&entry.path()));
            }
        }
        let mut roots = RootCertStore::empty();
        let mut helpers = Vec::new();
        for certificate in certificates {
            let der = CertificateDer::from(certificate);
            if self_issued(&der) {
                // A certificate the parser refuses is skipped, as Python warns about a bad one.
                let _ = roots.add(der);
            } else {
                helpers.push(der);
            }
        }
        if roots.is_empty() {
            return Err(NoAnchors);
        }
        let inner = WebPkiServerVerifier::builder_with_provider(
            Arc::new(roots),
            Arc::new(rustls::crypto::ring::default_provider()),
        )
        .build()
        .map_err(|_| NoAnchors)?;
        Ok(Self { inner, helpers })
    }
}

impl ServerCertVerifier for DefaultTrust {
    fn verify_server_cert(
        &self,
        end_entity: &CertificateDer<'_>,
        intermediates: &[CertificateDer<'_>],
        server_name: &ServerName<'_>,
        ocsp_response: &[u8],
        now: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        let mut chain: Vec<CertificateDer<'_>> = intermediates.to_vec();
        chain.extend(self.helpers.iter().cloned());
        self.inner.verify_server_cert(end_entity, &chain, server_name, ocsp_response, now)
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        self.inner.verify_tls12_signature(message, cert, dss)
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, rustls::Error> {
        self.inner.verify_tls13_signature(message, cert, dss)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.inner.supported_verify_schemes()
    }
}

/// The client configuration: TLS 1.2 and 1.3 (`create_default_context`'s minimum is TLS 1.2),
/// and ALPN `http/1.1` only where http.client sets it (its own default context).
pub fn client_config(trust: DefaultTrust, alpn_http11: bool) -> Result<rustls::ClientConfig, rustls::Error> {
    let mut config =
        rustls::ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_protocol_versions(&[&rustls::version::TLS13, &rustls::version::TLS12])?
            .dangerous()
            .with_custom_certificate_verifier(Arc::new(trust))
            .with_no_client_auth();
    if alpn_http11 {
        config.alpn_protocols = vec![b"http/1.1".to_vec()];
    }
    Ok(config)
}
