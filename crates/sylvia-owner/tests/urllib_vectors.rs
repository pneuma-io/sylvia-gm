//! The owner's urllib-faithful client against Python's urllib, recorded by
//! `vectors/generate_urllib_vectors.py` (CPython 3.12.11, OpenSSL 3.0).
//!
//! The pure steps (`proxy_bypass_environment`, the registry override, `fnmatch`, `_parse_proxy`
//! and the header lines) are compared directly. Every scenario is replayed against a local
//! CONNECT proxy and TLS server that follow the same script as the generator's: the real
//! `HttpsTransport` (through `metadata_bytes`) and the real `prepare_model` (through
//! `UrllibFetch`) are the clients, and the bytes they send (the CONNECT head, then the request
//! head inside TLS) and their outcomes must be Python's. The test certificate authority and
//! server certificates are made here from fixed, public, test-only seeds; nothing is committed.

use serde_json::{json, Value as J};
use sha2::{Digest, Sha256};
use std::collections::VecDeque;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use sylvia_owner::desktop_update::https::HttpsTransport;
use sylvia_owner::desktop_update::transport::{metadata_bytes, Cancel};
use sylvia_owner::dictation::download::UrllibFetch;
use sylvia_owner::dictation::{
    prepare_model, Failure as SpeechFailure, Fetch, Model, ModelFactory, ModelSpec, Raised, Runtime, Sound,
};
use sylvia_owner::urllib::proxy::{bypass_environment, bypass_override, fnmatch, parse_proxy, Proxies};
use sylvia_owner::urllib::request_headers;
use sylvia_owner::urllib::trust::TrustSources;
use sylvia_provider::proxy::ProxySource;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

fn vectors() -> &'static J {
    static VECTORS: std::sync::OnceLock<J> = std::sync::OnceLock::new();
    VECTORS.get_or_init(|| {
        serde_json::from_str(include_str!("vectors/urllib_vectors.json")).expect("valid JSON")
    })
}

const HOSTS: [&str; 6] = [
    "api.github.com",
    "github.com",
    "release-assets.githubusercontent.com",
    "huggingface.co",
    "cdn-lfs.huggingface.co",
    "evil.example",
];

/// A TEST-ONLY Ed25519 key from a fixed, public seed (as the generator's). Never trust it.
fn test_key(label: &str) -> rcgen::KeyPair {
    let seed = Sha256::digest(format!("sylvia-test-only-{label}").as_bytes());
    let mut pkcs8 =
        vec![0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70, 0x04, 0x22, 0x04, 0x20];
    pkcs8.extend_from_slice(&seed);
    rcgen::KeyPair::try_from(pkcs8.as_slice()).expect("a valid Ed25519 PKCS#8 key")
}

struct Pki {
    folder: PathBuf,
    ca: PathBuf,
    resolver: Arc<rustls::server::ResolvesServerCertUsingSni>,
    by_host: Vec<(String, Arc<rustls::sign::CertifiedKey>)>,
}

fn pki() -> Pki {
    use rcgen::{
        BasicConstraints, CertificateParams, DnType, ExtendedKeyUsagePurpose, IsCa, Issuer, KeyUsagePurpose,
    };
    let folder = std::env::temp_dir().join(format!("sylvia-urllib-vectors-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&folder);
    std::fs::create_dir_all(&folder).unwrap();
    let start = rcgen::date_time_ymd(2026, 1, 1);
    let end = rcgen::date_time_ymd(2036, 1, 1);
    let ca_key = test_key("ca");
    let mut ca_params = CertificateParams::new(Vec::<String>::new()).unwrap();
    ca_params.distinguished_name.push(DnType::CommonName, "Sylvia test-only CA");
    ca_params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    ca_params.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
    ca_params.not_before = start;
    ca_params.not_after = end;
    let ca = ca_params.self_signed(&ca_key).unwrap();
    let ca_path = folder.join("ca.pem");
    std::fs::write(&ca_path, ca.pem()).unwrap();
    let issuer = Issuer::new(ca_params, ca_key);
    let mut resolver = rustls::server::ResolvesServerCertUsingSni::new();
    let mut by_host = Vec::new();
    for host in HOSTS {
        let key = test_key(host);
        let mut params = CertificateParams::new(vec![host.to_string()]).unwrap();
        params.distinguished_name.push(DnType::CommonName, host);
        params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
        params.not_before = start;
        params.not_after = end;
        let leaf = params.signed_by(&key, &issuer).unwrap();
        let signing = rustls::crypto::ring::sign::any_supported_type(
            &rustls::pki_types::PrivateKeyDer::Pkcs8(key.serialize_der().into()),
        )
        .unwrap();
        let certified = Arc::new(rustls::sign::CertifiedKey::new(vec![leaf.der().clone()], signing));
        resolver.add(host, (*certified).clone()).unwrap();
        by_host.push((host.to_string(), certified));
    }
    Pki { folder, ca: ca_path, resolver: Arc::new(resolver), by_host }
}

/// Reads until the blank line, one byte at a time (as the generator's server).
async fn head<S: tokio::io::AsyncRead + Unpin>(stream: &mut S) -> Vec<u8> {
    let mut data = Vec::new();
    while !data.ends_with(b"\r\n\r\n") {
        let mut byte = [0_u8; 1];
        match stream.read(&mut byte).await {
            Ok(1) => data.push(byte[0]),
            _ => break,
        }
    }
    data
}

fn latin1(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| char::from(*byte)).collect()
}

fn encode(text: &str) -> Vec<u8> {
    text.chars().map(|c| u32::from(c) as u8).collect()
}

#[derive(Debug)]
struct ForHost(Arc<rustls::sign::CertifiedKey>);

impl rustls::server::ResolvesServerCert for ForHost {
    fn resolve(&self, _hello: rustls::server::ClientHello<'_>) -> Option<Arc<rustls::sign::CertifiedKey>> {
        Some(Arc::clone(&self.0))
    }
}

/// The scripted CONNECT proxy and TLS server; returns its port and what it saw.
fn serve(
    runtime: &tokio::runtime::Runtime,
    pki: &Pki,
    steps: &J,
    cancel: Cancel,
) -> (u16, Arc<Mutex<Vec<J>>>) {
    let listener = runtime.block_on(tokio::net::TcpListener::bind("127.0.0.1:0")).unwrap();
    let port = listener.local_addr().unwrap().port();
    let seen = Arc::new(Mutex::new(Vec::new()));
    let steps: VecDeque<J> = steps.as_array().unwrap().iter().cloned().collect();
    let (resolver, by_host, record) = (Arc::clone(&pki.resolver), pki.by_host.clone(), Arc::clone(&seen));
    runtime.spawn(async move {
        let mut steps = steps;
        while let Some(step) = steps.pop_front() {
            let Ok((mut connection, _)) = listener.accept().await else { return };
            let first = latin1(&head(&mut connection).await);
            if !first.starts_with("CONNECT ") {
                record.lock().unwrap().push(json!({"plain": first}));
                for part in step["response"].as_array().unwrap() {
                    let _ = connection.write_all(&encode(part.as_str().unwrap())).await;
                }
                continue;
            }
            let mut entry = serde_json::Map::new();
            entry.insert("connect".into(), J::String(first.clone()));
            let reply = step
                .get("connect")
                .and_then(J::as_str)
                .unwrap_or("HTTP/1.1 200 Connection established\r\n\r\n");
            let _ = connection.write_all(&encode(reply)).await;
            if !reply.starts_with("HTTP/1.1 200") {
                record.lock().unwrap().push(J::Object(entry));
                continue;
            }
            let target = first.split(' ').nth(1).unwrap().split(':').next().unwrap().to_string();
            let config = match step.get("certificate").and_then(J::as_str) {
                Some(host) => {
                    let key = by_host.iter().find(|(name, _)| name == host).unwrap().1.clone();
                    rustls::ServerConfig::builder()
                        .with_no_client_auth()
                        .with_cert_resolver(Arc::new(ForHost(key)))
                }
                None => rustls::ServerConfig::builder()
                    .with_no_client_auth()
                    .with_cert_resolver(Arc::clone(&resolver) as _),
            };
            let _ = target;
            let acceptor = tokio_rustls::TlsAcceptor::from(Arc::new(config));
            let mut tls = match acceptor.accept(connection).await {
                Ok(tls) => tls,
                Err(_) => {
                    entry.insert("tls".into(), J::String("failed".into()));
                    record.lock().unwrap().push(J::Object(entry));
                    continue;
                }
            };
            entry.insert("request".into(), J::String(latin1(&head(&mut tls).await)));
            record.lock().unwrap().push(J::Object(entry));
            for part in step["response"].as_array().unwrap() {
                match part.as_str().unwrap() {
                    "cancel" => {
                        cancel.set();
                        tokio::time::sleep(std::time::Duration::from_millis(300)).await;
                    }
                    text => {
                        let _ = tls.write_all(&encode(text)).await;
                        let _ = tls.flush().await;
                    }
                }
            }
            let _ = tls.shutdown().await;
        }
    });
    (port, seen)
}

fn proxies(port: u16, credentials: &str) -> Proxies {
    let url = format!("http://{credentials}127.0.0.1:{port}");
    let source = ProxySource {
        environment: vec![("https_proxy".into(), url.clone()), ("http_proxy".into(), url)],
        windows: cfg!(windows),
        registry: None,
    };
    Proxies::from_source(&source, None)
}

fn sources(ca: &Path, folder: &Path) -> TrustSources {
    TrustSources { system: Vec::new(), cafile: ca.to_path_buf(), capath: folder.join("no-such-folder") }
}

struct NoModel;
impl ModelFactory for NoModel {
    fn load(&mut self, _directory: &Path) -> Result<Box<dyn Model>, Raised> {
        Err(Raised)
    }
}

struct DownloadRuntime {
    fetch: UrllibFetch,
    model: NoModel,
}

impl Runtime for DownloadRuntime {
    fn sound(&mut self) -> Option<&mut dyn Sound> {
        None
    }
    fn languages(&self) -> Option<&'static [&'static str]> {
        None
    }
    fn model_factory(&mut self) -> &mut dyn ModelFactory {
        &mut self.model
    }
    fn fetch(&mut self) -> &mut dyn Fetch {
        &mut self.fetch
    }
    fn disk_free(&mut self, _path: &Path) -> std::io::Result<u64> {
        Ok(1_000_000_000_000)
    }
}

#[test]
fn proxy_rules_and_header_lines_match_python() {
    let v = vectors();
    let mut failures = Vec::new();
    for case in v["bypass_environment"].as_array().unwrap() {
        let (host, no_proxy) = (case["host"].as_str().unwrap(), case["no_proxy"].as_str().unwrap());
        let proxies: Vec<(String, String)> =
            if no_proxy.is_empty() { Vec::new() } else { vec![("no".into(), no_proxy.into())] };
        if bypass_environment(host, &proxies) != case["bypass"].as_bool().unwrap() {
            failures.push(format!("bypass_environment {case}"));
        }
    }
    for case in v["bypass_override"].as_array().unwrap() {
        if bypass_override(case["host"].as_str().unwrap(), case["override"].as_str().unwrap())
            != case["bypass"].as_bool().unwrap()
        {
            failures.push(format!("override {case}"));
        }
    }
    for case in v["fnmatch"].as_array().unwrap() {
        if fnmatch(case["name"].as_str().unwrap(), case["pattern"].as_str().unwrap())
            != case["match"].as_bool().unwrap()
        {
            failures.push(format!("fnmatch {case}"));
        }
    }
    for case in v["parse_proxy"].as_array().unwrap() {
        let got = match parse_proxy(case["proxy"].as_str().unwrap()) {
            Ok((scheme, user, password, hostport)) => json!({"ok": [scheme, user, password, hostport]}),
            Err(sylvia_owner::urllib::Failure::Value(message)) => json!({"error": message}),
            Err(other) => json!({"other": format!("{other:?}")}),
        };
        let mut expected = case.clone();
        expected.as_object_mut().unwrap().remove("proxy");
        if got != expected {
            failures.push(format!("parse_proxy {case}: {got}"));
        }
    }
    for case in v["request_headers"].as_array().unwrap() {
        let given: Vec<(String, String)> = serde_json::from_value(case["given"].clone()).unwrap();
        let sent: Vec<(String, String)> = serde_json::from_value(case["sent"].clone()).unwrap();
        if request_headers("<host>", &given, None) != sent {
            failures.push(format!("headers {case}: {:?}", request_headers("<host>", &given, None)));
        }
    }
    assert!(failures.is_empty(), "{} differ:\n{}", failures.len(), failures.join("\n"));
}

#[test]
fn every_scenario_matches_pythons_urllib_on_the_wire() {
    let pki = pki();
    let server_runtime =
        tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build().unwrap();
    let mut failures = Vec::new();
    for scenario in vectors()["scenarios"].as_array().unwrap() {
        let name = scenario["name"].as_str().unwrap();
        let cancel = Cancel::default();
        let (port, seen) = serve(&server_runtime, &pki, &scenario["steps"], cancel.clone());
        let credentials = scenario.get("credentials").and_then(J::as_str).unwrap_or("").to_string();
        let mut outcome = serde_json::Map::new();
        match scenario["kind"].as_str().unwrap() {
            "update" => {
                let transport = HttpsTransport::with_settings(
                    &sources(&pki.ca, &pki.folder),
                    Box::new(move || proxies(port, &credentials)),
                );
                match metadata_bytes(&transport, scenario["url"].as_str().unwrap(), &cancel) {
                    Ok(bytes) => outcome.insert("ok".into(), J::String(latin1(&bytes))),
                    Err(error) => outcome.insert("error".into(), J::String(error.code.to_string())),
                };
            }
            _ => {
                let (ca, folder) = (pki.ca.clone(), pki.folder.clone());
                let fetch = UrllibFetch::with_sources(
                    Box::new(move || sources(&ca, &folder)),
                    Box::new(move || proxies(port, "")),
                );
                let mut runtime = DownloadRuntime { fetch, model: NoModel };
                let target = pki.folder.join(name.replace(' ', "-"));
                std::fs::create_dir_all(&target).unwrap();
                let payload = b"controlled model bytes";
                let spec = ModelSpec {
                    files: vec![(
                        "model.bin".into(),
                        payload.len() as u64,
                        hex::encode(Sha256::digest(payload)),
                    )],
                };
                match prepare_model(&target, &spec, &mut runtime) {
                    Ok(()) => outcome.insert("ok".into(), J::String("ready".into())),
                    Err(SpeechFailure::Speech(code)) => {
                        outcome.insert("error".into(), J::String(code.into()))
                    }
                    Err(SpeechFailure::Other) => {
                        outcome.insert("escaped".into(), J::String("BadStatusLine".into()))
                    }
                };
            }
        }
        std::thread::sleep(std::time::Duration::from_millis(100));
        outcome.insert("seen".into(), J::Array(seen.lock().unwrap().clone()));
        let mut expected = scenario["expect"].clone();
        // Python's transport lets a malformed response escape; the controller reports update_failed.
        if scenario["kind"] == "update" && expected.get("escaped").is_some() {
            let map = expected.as_object_mut().unwrap();
            map.remove("escaped");
            map.insert("error".into(), J::String("update_failed".into()));
        }
        let got = J::Object(outcome);
        if got != expected {
            failures.push(format!("{name}:\n  got      {got}\n  expected {expected}"));
        }
    }
    let _ = std::fs::remove_dir_all(&pki.folder);
    assert!(failures.is_empty(), "{} scenarios differ:\n{}", failures.len(), failures.join("\n"));
}

/// A root, an intermediate it signs, and a `github.com` leaf the intermediate signs.
fn chain() -> (Vec<u8>, Vec<u8>, Vec<u8>) {
    use rcgen::{
        BasicConstraints, CertificateParams, DnType, ExtendedKeyUsagePurpose, IsCa, Issuer, KeyUsagePurpose,
    };
    let ca = |name: &str| {
        let mut params = CertificateParams::new(Vec::<String>::new()).unwrap();
        params.distinguished_name.push(DnType::CommonName, name);
        params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        params.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
        params.not_before = rcgen::date_time_ymd(2026, 1, 1);
        params.not_after = rcgen::date_time_ymd(2036, 1, 1);
        params
    };
    let (root_params, root_key) = (ca("Sylvia test-only root"), test_key("chain-root"));
    let root = root_params.self_signed(&root_key).unwrap();
    let root_issuer = Issuer::new(root_params, root_key);
    let (middle_params, middle_key) = (ca("Sylvia test-only intermediate"), test_key("chain-intermediate"));
    let middle = middle_params.signed_by(&middle_key, &root_issuer).unwrap();
    let middle_issuer = Issuer::new(middle_params, middle_key);
    let mut leaf_params = CertificateParams::new(vec!["github.com".to_string()]).unwrap();
    leaf_params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
    leaf_params.not_before = rcgen::date_time_ymd(2026, 1, 1);
    leaf_params.not_after = rcgen::date_time_ymd(2036, 1, 1);
    let leaf = leaf_params.signed_by(&test_key("chain-leaf"), &middle_issuer).unwrap();
    (root.der().to_vec(), middle.der().to_vec(), leaf.der().to_vec())
}

#[test]
fn trust_follows_openssls_chain_rule_over_the_system_stores() {
    use rustls::client::danger::ServerCertVerifier;
    use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
    use sylvia_owner::urllib::trust::{self_issued, DefaultTrust, SERVER_AUTH};
    let (root, middle, leaf) = chain();
    assert!(self_issued(&root));
    assert!(!self_issued(&middle) && !self_issued(&leaf));
    let nowhere = std::env::temp_dir().join("sylvia-no-such-ca");
    let verify = |system: Vec<(Vec<u8>, bool, Vec<String>)>, sent: &[Vec<u8>]| -> Option<bool> {
        let sources = TrustSources { system, cafile: nowhere.join("cert.pem"), capath: nowhere.clone() };
        let trust = DefaultTrust::new(&sources).ok()?;
        let sent: Vec<CertificateDer<'_>> =
            sent.iter().map(|der| CertificateDer::from(der.as_slice())).collect();
        let name = ServerName::try_from("github.com").unwrap();
        Some(
            trust
                .verify_server_cert(
                    &CertificateDer::from(leaf.as_slice()),
                    &sent,
                    &name,
                    &[],
                    UnixTime::now(),
                )
                .is_ok(),
        )
    };
    let all = |der: &Vec<u8>| (der.clone(), true, Vec::new());
    // The root alone: the server must send the intermediate.
    assert_eq!(verify(vec![all(&root)], std::slice::from_ref(&middle)), Some(true));
    assert_eq!(verify(vec![all(&root)], &[]), Some(false));
    // A "CA"-store intermediate (server authentication) fills the gap, as in OpenSSL's store.
    assert_eq!(verify(vec![all(&root), (middle.clone(), false, vec![SERVER_AUTH.into()])], &[]), Some(true));
    // An intermediate without its root anchors nothing (no partial chain): no anchors at all.
    assert_eq!(verify(vec![all(&middle)], &[]), None);
    // A root trusted only for code signing is left out, as `load_default_certs` leaves it out:
    // with nothing else in the stores there is no anchor at all.
    assert_eq!(
        verify(vec![(root, false, vec!["1.3.6.1.5.5.7.3.3".into()])], std::slice::from_ref(&middle)),
        None
    );
}

#[cfg(windows)]
#[test]
fn the_windows_stores_load_through_schannel() {
    use sylvia_owner::urllib::trust::{DefaultTrust, SERVER_AUTH};
    let sources = TrustSources::system();
    let usable = sources
        .system
        .iter()
        .filter(|(_, all, oids)| *all || oids.iter().any(|oid| oid == SERVER_AUTH))
        .count();
    assert!(usable > 0, "the current user's CA and ROOT stores have certificates for server authentication");
    assert!(DefaultTrust::new(&sources).is_ok());
}
