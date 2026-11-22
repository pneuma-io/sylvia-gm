//! Proxy selection against httpx and urllib, from `tests/vectors/proxy_vectors.json`.
//!
//! Regenerate with `tests/vectors/generate_proxy_vectors.py` (needs the snapshot's Python). Each
//! scenario gives an environment (Linux or Windows semantics) and, for Windows, registry values;
//! Python's own `getproxies`, `get_environment_proxies` and `httpx.AsyncClient` chose the proxy
//! for each target URL, or failed to build the client.

use serde_json::Value;
use sylvia_provider::httpx_url::HttpxUrl;
use sylvia_provider::proxy::{getproxies, ProxyError, ProxyMap, ProxySource};

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/proxy_vectors.json")).expect("proxy vectors")
}

#[test]
fn proxy_selection_matches_httpx() {
    let vectors = vectors();
    let targets: Vec<&str> =
        vectors["targets"].as_array().unwrap().iter().map(|t| t.as_str().unwrap()).collect();
    let scenarios = vectors["scenarios"].as_array().unwrap();
    assert!(scenarios.len() >= 50);
    for scenario in scenarios {
        let source = ProxySource {
            environment: scenario["environment"]
                .as_object()
                .unwrap()
                .iter()
                .map(|(name, value)| (name.clone(), value.as_str().unwrap().to_owned()))
                .collect(),
            windows: scenario["platform"] == "windows",
            registry: scenario["registry"]
                .as_array()
                .map(|pair| (pair[0].as_u64().unwrap() as u32, pair[1].as_str().unwrap().to_owned())),
        };
        let context =
            format!("{} {} {}", scenario["platform"], scenario["environment"], scenario["registry"]);
        let proxies: Vec<Value> =
            getproxies(&source).into_iter().map(|(k, v)| Value::Array(vec![k.into(), v.into()])).collect();
        let python: Vec<Value> = scenario["proxies"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| Value::Array(vec![k.clone().into(), v.clone()]))
            .collect();
        assert_eq!(proxies, python, "getproxies for {context}");
        match (ProxyMap::from_source(&source), scenario.get("error")) {
            (Ok(map), None) => {
                for target in &targets {
                    let url = HttpxUrl::parse(target).unwrap();
                    let chosen = map.proxy_for(&url).map(|proxy| proxy.url.clone());
                    assert_eq!(
                        chosen.as_deref(),
                        scenario["chosen"][*target].as_str(),
                        "{target} for {context}"
                    );
                }
            }
            (Err(error), Some(expected)) => {
                let (class, message) = expected.as_str().unwrap().split_once(": ").unwrap();
                let (ours_class, ours) = match &error {
                    ProxyError::Value(message) => ("ValueError", message),
                    ProxyError::Internal(message) => ("ImportError", message),
                };
                assert_eq!((ours_class, ours.as_str()), (class, message), "{context}");
            }
            (ours, theirs) => panic!("{context}: Rust {ours:?}, Python {theirs:?}"),
        }
    }
}

#[test]
fn proxy_credentials_leave_the_url() {
    let source = ProxySource {
        environment: vec![("HTTP_PROXY".into(), "http://us%40er:p%3Ass@p:1".into())],
        windows: false,
        registry: None,
    };
    let map = ProxyMap::from_source(&source).unwrap();
    let proxy = map.proxy_for(&HttpxUrl::parse("http://127.0.0.1:1234/v1").unwrap()).unwrap();
    assert_eq!(proxy.url, "http://p:1/");
    assert_eq!(proxy.auth, Some(("us@er".into(), "p:ss".into())));
}

/// The live registry read against Python's own `urllib.request.getproxies_registry()` on this
/// Windows machine and user. It only reads the Internet Settings proxy values. Opt in with
/// `SYLVIA_REFERENCE_PYTHON` set to the snapshot's Python:
/// `cargo test -p sylvia-provider --test proxy_vectors -- --ignored`.
#[cfg(windows)]
#[test]
#[ignore = "needs the Python reference (SYLVIA_REFERENCE_PYTHON)"]
fn the_registry_proxies_match_python_on_this_machine() {
    let python = std::env::var("SYLVIA_REFERENCE_PYTHON").expect("SYLVIA_REFERENCE_PYTHON");
    let output = std::process::Command::new(&python)
        .args([
            "-I",
            "-c",
            "import json, urllib.request as r; print(json.dumps(list(r.getproxies_registry().items())))",
        ])
        .output()
        .expect("the reference Python runs");
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let expected: Vec<(String, String)> = serde_json::from_slice(&output.stdout).unwrap();
    let registry = sylvia_provider::proxy::system_registry();
    assert_eq!(sylvia_provider::proxy::getproxies_registry(registry.as_ref()), expected);
}
