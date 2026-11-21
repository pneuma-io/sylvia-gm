//! Compare `ConfiguredProviderResolver` with vectors written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_resolver_vectors.py` (needs the snapshot's Python).
//! Credentials are disposable test values under a deterministic fake seal; nothing reads a real
//! settings file or credential.

use serde_json::{json, Value as J};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use sylvia_agent::provider_resolver::{
    ConfiguredProviderResolver, LocalDiscovery, ResolverError, ResolverSources, RuntimeHooks,
};
use sylvia_base::CodedError;
use sylvia_domain::authority::WriterRole;
use sylvia_domain::session::{SessionRecord, SessionRecordFields};
use sylvia_provider::config::env::Environment;
use sylvia_provider::config::protection::{self, Seal};
use sylvia_provider::config::{data_from_json, ConfigFailure, Data};
use sylvia_provider::provider::{FakeProvider, Provider};
use sylvia_provider::registry::ProviderFactory;
use sylvia_provider::request_metadata::opencode_request_headers;
use sylvia_provider::types::{ModelInfo, ProviderConfig};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/resolver_vectors.json")).expect("vector file is valid JSON")
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

/// The generator's recording factory.
#[derive(Default)]
struct Recording {
    built: Mutex<Vec<(Arc<dyn Provider>, ProviderConfig)>>,
}

struct Built(FakeProvider);

impl Provider for Built {
    fn model_info(&self) -> Option<&ModelInfo> {
        self.0.model_info()
    }
    fn generate(
        &self,
        request: sylvia_provider::ProviderRequest,
    ) -> sylvia_provider::BoxFuture<'_, sylvia_provider::ProviderResult<sylvia_provider::ProviderResponse>>
    {
        self.0.generate(request)
    }
    fn generate_stream(&self, request: sylvia_provider::ProviderRequest) -> sylvia_provider::ChunkStream<'_> {
        self.0.generate_stream(request)
    }
    fn close(&self) -> sylvia_provider::BoxFuture<'_, ()> {
        self.0.close()
    }
}

impl ProviderFactory for Recording {
    fn create(&self, config: ProviderConfig) -> Result<Arc<dyn Provider>, CodedError> {
        let provider: Arc<dyn Provider> = Arc::new(Built(FakeProvider::new(Vec::new(), Vec::new())));
        self.built.lock().unwrap().push((provider.clone(), config));
        Ok(provider)
    }
}

/// The generator's runtime hooks: the case's refresh answer and discovery reply.
struct Hooks {
    refresh: bool,
    discovery: J,
    refresh_calls: Mutex<Vec<String>>,
    discovery_calls: Mutex<Vec<J>>,
}

impl RuntimeHooks for Hooks {
    fn refresh_subscription_reasoning(&self, storage_root: &Path) -> bool {
        self.refresh_calls
            .lock()
            .unwrap()
            .push(storage_root.file_name().unwrap().to_string_lossy().into_owned());
        self.refresh
    }

    fn discover_local_models(&self, request: &LocalDiscovery<'_>) -> Result<Data, ConfigFailure> {
        self.discovery_calls.lock().unwrap().push(json!({
            "base_url": request.base_url,
            "limit": request.limit,
            "preset_id": request.preset_id,
            "provider_id": request.provider_id,
            "query": request.query,
        }));
        match &self.discovery {
            J::String(message) => Err(ConfigFailure::Config(message.clone())),
            J::Null => Ok(data_from_json(&json!({"models": []})).unwrap()),
            reply => Ok(data_from_json(reply).unwrap()),
        }
    }
}

fn failure(error: ResolverError) -> J {
    let (class, code, message) = match error {
        ResolverError::Config(ConfigFailure::Config(message)) => ("ConfigError", None, message),
        ResolverError::Config(ConfigFailure::Python { class, message }) => (class, None, message),
        ResolverError::Url(error) => ("ValueError", None, error.0),
        ResolverError::Coded(error) => {
            let class = error.error_type.unwrap_or("CommandRejected");
            let code = (class == "CommandRejected").then(|| error.code.clone());
            (class, code, error.message)
        }
    };
    let mut record = json!({"class": class, "message": message});
    if let Some(code) = code {
        record["code"] = json!(code);
    }
    json!({"error": record})
}

fn config_record(config: &ProviderConfig) -> J {
    let home = Path::new(&config.runtime_home).file_name().map(|name| name.to_string_lossy().into_owned());
    json!({
        "provider_key": config.provider_key,
        "transport": config.transport,
        "base_url": config.base_url,
        "runtime_home_name": if config.runtime_home.is_empty() { String::new() } else { home.unwrap_or_default() },
        "default_headers": config.default_headers.iter().map(|(name, value)| json!([name, value])).collect::<Vec<_>>(),
        "api_key": config.api_key,
        "models": config.models.iter().map(|model| model.id.clone()).collect::<Vec<_>>(),
        "default_model": config.default_model,
    })
}

fn session(spec: &J) -> SessionRecord {
    let role = WriterRole::parse(spec[1].as_str().unwrap()).unwrap();
    SessionRecord::new(SessionRecordFields {
        session_id: spec[0].as_str().unwrap().into(),
        campaign_id: (role == WriterRole::Dm).then(|| "c1".to_owned()),
        role,
        provider_id: spec[2].as_str().unwrap().into(),
        model_id: spec[3].as_str().unwrap().into(),
        ..SessionRecordFields::default()
    })
    .unwrap()
}

fn sorted(set: &std::collections::HashSet<String>) -> Vec<String> {
    let mut items: Vec<String> = set.iter().cloned().collect();
    items.sort();
    items
}

fn temporary_folder() -> PathBuf {
    let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let folder = std::env::temp_dir().join(format!("sylvia-resolver-test-{}-{nanos}", std::process::id()));
    std::fs::create_dir(&folder).unwrap();
    folder
}

fn run_case(case: &J, sources: &ResolverSources, path: &Path) -> J {
    let _ = std::fs::remove_file(path);
    if let Some(text) = case["user"].as_str() {
        std::fs::write(path, text.replace("<PROTECTION>", protection::PROTECTION)).unwrap();
    }
    let hooks = Arc::new(Hooks {
        refresh: case["refresh"].as_bool().unwrap(),
        discovery: case["discovery"].clone(),
        refresh_calls: Mutex::new(Vec::new()),
        discovery_calls: Mutex::new(Vec::new()),
    });
    let factory = Arc::new(Recording::default());
    let mut record = json!({
        "name": case["name"],
        "user": case["user"],
        "refresh": case["refresh"],
        "discovery": case["discovery"],
        "sessions": case["sessions"],
    });
    let resolver =
        match ConfiguredProviderResolver::new(sources, Some(path), None, factory.clone(), hooks.clone()) {
            Ok(resolver) => resolver,
            Err(error) => {
                record["error"] = failure(error)["error"].clone();
                record["refresh_calls"] = json!(*hooks.refresh_calls.lock().unwrap());
                return record;
            }
        };
    record["subscription_models"] = json!(sorted(&resolver.subscription_models));
    record["claude_code_models"] = json!(sorted(&resolver.claude_code_models));
    let choices = |resolver: &ConfiguredProviderResolver| match resolver.model_choices() {
        Ok(choices) => J::Array(choices),
        Err(error) => failure(error),
    };
    record["model_choices"] = choices(&resolver);
    let mut outcomes = Vec::new();
    for spec in case["sessions"].as_array().unwrap() {
        let before = factory.built.lock().unwrap().len();
        let provider = match resolver.resolve(&session(spec)) {
            Ok(provider) => provider,
            Err(error) => {
                outcomes.push(failure(error));
                continue;
            }
        };
        let built = factory.built.lock().unwrap();
        // Anything but the factory's own build is the request-metadata wrapper around it, whose
        // headers are `opencode_request_headers(session_id)` (checked in the provider vectors).
        let wrapped = built.iter().all(|(made, _)| !Arc::ptr_eq(made, &provider));
        let headers = wrapped.then(|| {
            opencode_request_headers(spec[0].as_str().unwrap())
                .into_iter()
                .map(|(name, value)| json!([name, value]))
                .collect::<Vec<_>>()
        });
        outcomes.push(json!({
            "built": built[before..].iter().map(|(_, config)| config_record(config)).collect::<Vec<_>>(),
            // A cached provider is the latest build for its model (`inner.config` in Python).
            "config": config_record(
                &built.iter().rev().find(|(_, config)| config.default_model == spec[3].as_str().unwrap()).expect("a build").1
            ),
            "headers": headers,
            "model_choices": choices(&resolver),
        }));
    }
    record["outcomes"] = J::Array(outcomes);
    record["refresh_calls"] = json!(*hooks.refresh_calls.lock().unwrap());
    record["discovery_calls"] = J::Array(hooks.discovery_calls.lock().unwrap().clone());
    record
}

#[test]
fn resolver_matches_python() {
    let vectors = vectors();
    let folder = temporary_folder();
    let root = folder.join("owner-root");
    std::fs::create_dir(&root).unwrap();
    let path = root.join("config.yaml");
    let sources = ResolverSources {
        default_config: PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../config/config.yaml"),
        seal: Arc::new(FakeSeal),
        env: Environment::default(),
    };
    let mut failures = Vec::new();
    let cases = vectors["cases"].as_array().unwrap();
    for case in cases {
        let actual = token(run_case(case, &sources, &path));
        if !same(&actual, case) {
            failures.push(format!("{}\n  expected {case}\n  got      {actual}", case["name"]));
        }
    }
    let _ = std::fs::remove_dir_all(&folder);
    assert!(
        failures.is_empty(),
        "{} of {} differ:\n{}",
        failures.len(),
        cases.len(),
        failures[..failures.len().min(5)].join("\n")
    );
}
