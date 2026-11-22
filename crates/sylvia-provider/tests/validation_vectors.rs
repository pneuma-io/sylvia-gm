//! Compare the provider compatibility probe with vectors written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_validation_vectors.py` (needs the snapshot's Python).
//! Each case's HTTP replies, streamed bodies in their recorded pieces, are replayed in order
//! through the scripted transport; the requests Sylvia sends, the clients it opens and the report
//! must all be Python's. Credentials are disposable values under a deterministic fake seal.

use serde_json::{json, Value as J};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use sylvia_provider::config::discovery::ClientFor;
use sylvia_provider::config::env::Environment;
use sylvia_provider::config::protection::{self, Seal};
use sylvia_provider::config::provider_settings::ConfigSources;
use sylvia_provider::config::validation::{validate_provider_model, Validate};
use sylvia_provider::config::{data_from_json, reply_json, ConfigFailure, Data};
use sylvia_provider::http::{HttpTransport, Piece, ScriptedReply, ScriptedTransport, TransportError};

/// The HTTP client's own headers (differences row 8), left out of the comparison.
const LIBRARY_HEADERS: [&str; 6] =
    ["host", "accept", "accept-encoding", "connection", "user-agent", "content-length"];

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/validation_vectors.json")).expect("vector file is valid JSON")
}

fn same(a: &J, b: &J) -> bool {
    serde_json::to_string(a).unwrap() == serde_json::to_string(b).unwrap()
}

struct FakeSeal;

impl Seal for FakeSeal {
    fn seal(&self, value: &[u8], decrypt: bool) -> Result<Vec<u8>, ConfigFailure> {
        if decrypt {
            let Some(rest) = value.strip_prefix(b"FAKE") else {
                return Err(ConfigFailure::Config(protection::UNAVAILABLE_MESSAGE.into()));
            };
            Ok(rest.iter().rev().copied().collect())
        } else {
            let mut out = b"FAKE".to_vec();
            out.extend(value.iter().rev());
            Ok(out)
        }
    }
}

fn json_inner(text: &str) -> String {
    let quoted = J::String(text.to_owned()).to_string();
    quoted[1..quoted.len() - 1].to_owned()
}

fn token(value: J) -> J {
    let text = value
        .to_string()
        .replace(&json_inner(protection::UNAVAILABLE_MESSAGE), "<UNAVAILABLE>")
        .replace(&json_inner(protection::PROTECTION), "<PROTECTION>");
    serde_json::from_str(&text).unwrap()
}

fn replies(case: &J) -> Vec<ScriptedReply> {
    case["replies"]
        .as_array()
        .unwrap()
        .iter()
        .map(|reply| match reply.get("error") {
            Some(message) => {
                ScriptedReply::Fail(TransportError::Connection(message.as_str().unwrap().into()))
            }
            None => {
                let mut pieces: Vec<Piece> = Vec::new();
                if let Some(filler) = reply.get("filler").and_then(J::as_u64) {
                    pieces.push(Piece::Bytes(vec![b'x'; filler as usize]));
                }
                pieces.extend(
                    reply["pieces"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|piece| Piece::Bytes(hex::decode(piece.as_str().unwrap()).unwrap())),
                );
                ScriptedReply::Respond {
                    status: reply["status"].as_u64().unwrap() as u16,
                    headers: reply["headers"]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|pair| {
                            (pair[0].as_str().unwrap().to_owned(), pair[1].as_str().unwrap().to_owned())
                        })
                        .collect(),
                    pieces,
                }
            }
        })
        .collect()
}

fn data(value: Option<&J>) -> Data {
    value.map_or(Data::None, |value| data_from_json(value).unwrap())
}

fn temporary_folder() -> PathBuf {
    let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let folder = std::env::temp_dir().join(format!("sylvia-validation-test-{}-{nanos}", std::process::id()));
    std::fs::create_dir(&folder).unwrap();
    folder
}

#[tokio::test]
async fn the_probe_matches_python() {
    let vectors = vectors();
    let folder = temporary_folder();
    let path = folder.join("config.yaml");
    let env = Environment::default();
    let default_config = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../config/config.yaml");
    let sources = ConfigSources { default_config: &default_config, seal: &FakeSeal, env: &env };
    let mut failures = Vec::new();
    for case in vectors["cases"].as_array().unwrap() {
        let _ = std::fs::remove_file(&path);
        if let Some(text) = case["user"].as_str() {
            std::fs::write(&path, text.replace("<PROTECTION>", protection::PROTECTION)).unwrap();
        }
        let params = &case["request"];
        let request = Validate {
            provider_id: data(params.get("provider_id")),
            base_url: data(params.get("base_url")),
            credential: data(params.get("credential")),
            model_id: data(params.get("model_id")),
            transport: data(params.get("transport")),
            preset_id: data(params.get("preset_id")),
            headers: data(params.get("headers")),
        };
        let scripted = replies(case);
        let count = scripted.len();
        let transport = ScriptedTransport::new(scripted);
        let clients: Arc<Mutex<Vec<f64>>> = Arc::default();
        let client_for: Arc<ClientFor> = {
            let transport = transport.clone();
            let clients = Arc::clone(&clients);
            Arc::new(move |seconds: f64| {
                clients.lock().unwrap().push(seconds);
                Ok(Arc::new(transport.clone()) as Arc<dyn HttpTransport>)
            })
        };
        let outcome = validate_provider_model(sources, &path, &request, client_for.as_ref()).await;
        let mut actual = match outcome.and_then(|value| reply_json(&value)) {
            Ok(value) => json!({"value": value}),
            Err(error) => {
                let class = match &error {
                    ConfigFailure::Config(_) => "ConfigError",
                    ConfigFailure::Python { class, .. } => class,
                };
                json!({"error": {"class": class, "message": error.to_string()}})
            }
        };
        let requests = transport.requests();
        actual["clients"] = J::Array(
            clients
                .lock()
                .unwrap()
                .iter()
                .map(|seconds| json!({"timeout": seconds, "follow_redirects": false}))
                .collect(),
        );
        actual["requests"] = J::Array(
            requests
                .iter()
                .map(|request| {
                    json!({
                        "method": request.method,
                        "url": request.url,
                        "headers": request
                            .headers
                            .iter()
                            .filter(|(name, _)| !LIBRARY_HEADERS.contains(&name.to_lowercase().as_str()))
                            .map(|(name, value)| json!([name.to_lowercase(), value]))
                            .collect::<Vec<_>>(),
                        "body": String::from_utf8_lossy(&request.body),
                    })
                })
                .collect(),
        );
        actual["unused_replies"] = json!(count.saturating_sub(requests.len()));
        let mut expected = case.clone();
        for key in ["name", "user", "request", "replies"] {
            expected.as_object_mut().unwrap().shift_remove(key);
        }
        let actual = token(actual);
        if !same(&actual, &expected) {
            failures.push(format!("{}\n  expected {expected}\n  got      {actual}", case["name"]));
        }
    }
    let _ = std::fs::remove_dir_all(&folder);
    assert!(
        failures.is_empty(),
        "{} differ:\n{}",
        failures.len(),
        failures[..failures.len().min(6)].join("\n")
    );
}
