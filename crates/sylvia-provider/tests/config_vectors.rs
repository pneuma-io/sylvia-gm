//! Compare sylvia_provider::config with vectors written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_config_vectors.py` (needs the snapshot's Python).
//! Platform protection is replaced on both sides by the same deterministic fake seal, so no
//! real credential is involved; every key and header is a disposable test value.

use serde_json::{json, Value as J};
use std::path::PathBuf;
use sylvia_provider::config::env::Environment;
use sylvia_provider::config::protection::{self, Seal};
use sylvia_provider::config::{load_config, provider_credentials, user_settings, ConfigFailure, Data};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/config_vectors.json")).expect("vector file is valid JSON")
}

/// The generator's seal: `b"FAKE"` and the bytes reversed.
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

fn untoken(text: &str) -> String {
    text.replace("<UNAVAILABLE>", protection::UNAVAILABLE_MESSAGE)
        .replace("<VERIFY_FAILED>", protection::VERIFY_FAILED)
        .replace("<MIGRATION_RETRY>", user_settings::MIGRATION_RETRY)
        .replace("<PROTECTION>", protection::PROTECTION)
}

fn token(text: &str) -> String {
    text.replace(protection::UNAVAILABLE_MESSAGE, "<UNAVAILABLE>")
        .replace(protection::VERIFY_FAILED, "<VERIFY_FAILED>")
        .replace(user_settings::MIGRATION_RETRY, "<MIGRATION_RETRY>")
        .replace(protection::PROTECTION, "<PROTECTION>")
}

fn data_record(value: &Data) -> J {
    match value {
        Data::None => json!(["none"]),
        Data::Bool(flag) => json!(["bool", flag]),
        Data::Int(number) => json!(["int", number.to_string()]),
        Data::Float(number) => json!(["float", sylvia_base::pyjson::float_repr(*number)]),
        Data::Str(text) => json!(["str", text]),
        Data::Bytes(bytes) => json!(["bytes", bytes.iter().map(|b| format!("{b:02x}")).collect::<String>()]),
        Data::DateTime(datetime) => json!(["datetime", datetime.isoformat()]),
        Data::Date(date) => json!(["date", date.isoformat()]),
        Data::List(items) | Data::Tuple(items) => {
            json!(["list", items.iter().map(data_record).collect::<Vec<_>>()])
        }
        Data::Dict(pairs) => {
            json!([
                "dict",
                pairs.iter().map(|(k, v)| json!([data_record(k), data_record(v)])).collect::<Vec<_>>()
            ])
        }
        Data::Set(_) => panic!("no sets in configuration vectors"),
    }
}

fn config_record(config: &sylvia_provider::config::SylviaGMConfig) -> J {
    let secret = |value: &Option<sylvia_provider::config::Secret>| value.as_ref().map(|s| token(s.expose()));
    json!({
        "app": {"name": config.app.name, "version": config.app.version, "data_dir": config.app.data_dir, "campaigns_dir": config.app.campaigns_dir},
        "logging": {"level": config.logging.level, "format": config.logging.format, "file": config.logging.file},
        "ruleset": {"default": config.ruleset.default, "path": config.ruleset.path},
        "tts": {"provider": config.tts.provider, "enabled": config.tts.enabled, "voice": config.tts.voice, "voices": config.tts.voices},
        "providers": config.providers.iter().map(|(id, p)| json!([id, {
            "enabled": p.enabled, "preset_id": p.preset_id, "mode": p.mode, "name": p.name, "base_url": p.base_url,
            "api_key": token(p.api_key.expose()),
            "headers": p.headers.iter().map(|(k, v)| json!([k, token(v.expose())])).collect::<Vec<_>>(),
            "protected_credentials": secret(&p.protected_credentials),
            "credential_overrides": secret(&p.credential_overrides),
            "credential_error": p.credential_error.as_deref().map(token),
            "models": p.models.iter().map(data_record).collect::<Vec<_>>(),
        }])).collect::<Vec<_>>(),
    })
}

fn error_record(error: &ConfigFailure) -> J {
    let class = match error {
        ConfigFailure::Config(_) => "ConfigError",
        ConfigFailure::Python { class, .. } => class,
    };
    json!({"class": class, "message": token(&error.to_string())})
}

fn temporary_folder() -> PathBuf {
    let mut suffix = [0u8; 8];
    getrandom::getrandom(&mut suffix).unwrap();
    let folder = std::env::temp_dir().join(format!(
        "sylvia-config-test-{}",
        suffix.iter().map(|b| format!("{b:02x}")).collect::<String>()
    ));
    std::fs::create_dir(&folder).unwrap();
    folder
}

#[test]
fn load_config_matches_python() {
    let vectors = vectors();
    let windows_names = vectors["platform"] == "win32";
    let default_config = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../config/config.yaml");
    let folder = temporary_folder();
    let user_path = folder.join("config.yaml");
    let mut failures = Vec::new();
    for case in vectors["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let _ = std::fs::remove_file(&user_path);
        if let Some(text) = case["user"].as_str() {
            std::fs::write(&user_path, untoken(text)).unwrap();
        }
        let vars = case["env"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(k, v)| (k.clone(), Some(v.as_str().unwrap().to_owned())))
            .collect();
        let env = Environment::from_vars(vars, windows_names);
        let mut actual = serde_json::Map::new();
        match load_config(&default_config, Some(&user_path), &FakeSeal, &env) {
            Ok(config) => {
                actual.insert("config".into(), config_record(&config));
                let credentials: Vec<J> = config
                    .providers
                    .iter()
                    .map(|(id, definition)| match provider_credentials(definition, &FakeSeal, &env) {
                        Ok((key, headers)) => json!([id, {"key": key, "headers": headers}]),
                        Err(error) => json!([id, {"error": error_record(&error)}]),
                    })
                    .collect();
                actual.insert("credentials".into(), J::Array(credentials));
            }
            Err(error) => {
                actual.insert("error".into(), error_record(&error));
            }
        }
        let after = std::fs::read(&user_path).ok().map(|bytes| token(&String::from_utf8(bytes).unwrap()));
        actual.insert("file_after".into(), json!(after));
        for key in ["config", "credentials", "error", "file_after"] {
            if actual.get(key) != case.get(key) {
                failures.push(format!(
                    "{name}: {key}\n  expected {}\n  got      {}",
                    case.get(key).unwrap_or(&J::Null),
                    actual.get(key).unwrap_or(&J::Null)
                ));
            }
        }
    }
    std::fs::remove_dir_all(&folder).unwrap();
    assert!(failures.is_empty(), "{} differences:\n{}", failures.len(), failures.join("\n"));
}

#[test]
fn protect_then_unprotect_round_trips_with_disposable_values() {
    let env = Environment::default();
    let headers = vec![("X-Disposable".to_owned(), "h".to_owned())];
    let envelope = protection::protect_credentials(&FakeSeal, "sk-disposable", &headers).unwrap();
    assert_eq!(
        protection::unprotect_credentials(&FakeSeal, &envelope, true, &env).unwrap(),
        ("sk-disposable".to_owned(), headers)
    );
}

/// The real platform seal on this machine, with a disposable value only.
#[cfg(any(windows, target_os = "linux"))]
#[test]
fn the_platform_seal_opens_its_own_envelopes() {
    #[cfg(target_os = "linux")]
    let folder = temporary_folder();
    #[cfg(target_os = "linux")]
    let seal = protection::linux::KeyFile::in_state_folder(&folder);
    #[cfg(windows)]
    let seal = protection::PlatformSeal;
    let env = Environment::default();
    let envelope = protection::protect_credentials(&seal, "sk-disposable-platform", &[]).unwrap();
    assert_eq!(envelope.get_str("protection").and_then(Data::as_str), Some(protection::PROTECTION));
    let (key, headers) = protection::unprotect_credentials(&seal, &envelope, false, &env).unwrap();
    assert_eq!((key.as_str(), headers.len()), ("sk-disposable-platform", 0));
    #[cfg(target_os = "linux")]
    std::fs::remove_dir_all(&folder).unwrap();
}

/// DPAPI both ways against the Python reference, on this Windows machine and user, with a
/// disposable value only. Opt in with `SYLVIA_REFERENCE_PYTHON` set to the snapshot's Python:
/// `cargo test -p sylvia-provider --test config_vectors -- --ignored`.
#[cfg(windows)]
#[test]
#[ignore = "needs the Python reference (SYLVIA_REFERENCE_PYTHON)"]
fn dpapi_envelopes_open_on_both_sides() {
    let python = std::env::var("SYLVIA_REFERENCE_PYTHON").expect("SYLVIA_REFERENCE_PYTHON");
    let run = |code: &str, input: &str| -> String {
        let output = std::process::Command::new(&python)
            .args(["-I", "-c", code, input])
            .output()
            .expect("the reference Python runs");
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        String::from_utf8(output.stdout).unwrap().trim().to_owned()
    };
    let env = Environment::default();
    // Python seals, Rust opens.
    let sealed_by_python = run(
        "import json,sys; from sylvia_gm.config._credential_protection import protect_credentials as p; \
         print(json.dumps(p(sys.argv[1], {'X-Disposable': 'h'})))",
        "sk-disposable-from-python",
    );
    let envelope = sylvia_provider::config::Data::Dict(
        serde_json::from_str::<serde_json::Map<String, J>>(&sealed_by_python)
            .unwrap()
            .into_iter()
            .map(|(k, v)| {
                let value = match v {
                    J::Number(n) => Data::Int(n.as_i64().unwrap()),
                    J::String(s) => Data::Str(s),
                    other => panic!("{other}"),
                };
                (Data::Str(k), value)
            })
            .collect(),
    );
    let opened =
        protection::unprotect_credentials(&protection::PlatformSeal, &envelope, false, &env).unwrap();
    assert_eq!(
        opened,
        ("sk-disposable-from-python".to_owned(), vec![("X-Disposable".to_owned(), "h".to_owned())])
    );
    // Rust seals, Python opens.
    let envelope =
        protection::protect_credentials(&protection::PlatformSeal, "sk-disposable-from-rust", &[]).unwrap();
    let data = envelope.get_str("data").and_then(Data::as_str).unwrap();
    let opened_by_python = run(
        "import json,sys; from sylvia_gm.config._credential_protection import unprotect_credentials as u; \
         k, h = u({'version': 1, 'protection': 'windows-dpapi-user', 'data': sys.argv[1]}, resolve=False); print(k, len(h))",
        data,
    );
    assert_eq!(opened_by_python, "sk-disposable-from-rust 0");
}
