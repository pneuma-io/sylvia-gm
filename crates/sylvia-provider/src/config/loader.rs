//! `config/loader.py`: `load_config` and the frozen configuration model.
//!
//! Load order (last wins): the shipped defaults (`config/config.yaml`), the owner-local user
//! file, then `SYLVIA_GM_` environment overrides. Every Sylvia caller passes
//! `load_dotenv_file=False, decrypt_credentials=False`, which is the path ported here: no
//! `.env` file is read, and protected credentials stay sealed until
//! [`provider_credentials`] opens the one selected connection.
//!
//! The model is validated as pydantic 2 validates it in lax mode: text fields take `str` (or
//! UTF-8 `bytes`), booleans take `bool`, `0`/`1`, `0.0`/`1.0` and the words
//! `0 off f false n no 1 on t true y yes` in any case, lists take a list, tuple or set, and
//! unknown fields are ignored. Any failure is the one `ConfigError`.

use super::env::{apply_env_overrides, Environment};
use super::merge::deep_merge;
use super::protection::{unprotect_credentials, Seal, UNAVAILABLE_MESSAGE};
use super::pyvalue::{from_json, json_dumps, type_name};
use super::resolver::resolve_vars;
use super::user_settings::read_user_settings;
use super::ConfigFailure;
use std::path::{Path, PathBuf};
use sylvia_base::pyyaml::{self, data_key_eq, Data};

/// `SecretStr`: text whose `Debug` form hides it.
#[derive(Clone, PartialEq, Eq, Default)]
pub struct Secret(String);

impl Secret {
    pub fn new(value: impl Into<String>) -> Self {
        Self(value.into())
    }

    /// `get_secret_value()`.
    pub fn expose(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Debug for Secret {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(if self.0.is_empty() { "SecretStr('')" } else { "SecretStr('**********')" })
    }
}

/// `AppConfig`.
#[derive(Debug, Clone, PartialEq)]
pub struct AppConfig {
    pub name: String,
    pub version: String,
    pub data_dir: String,
    pub campaigns_dir: String,
}

/// `LoggingConfig`.
#[derive(Debug, Clone, PartialEq)]
pub struct LoggingConfig {
    pub level: String,
    pub format: String,
    pub file: String,
}

/// `ProviderDef`: one configured connection.
#[derive(Debug, Clone, PartialEq)]
pub struct ProviderDef {
    pub enabled: bool,
    pub preset_id: String,
    pub mode: String,
    pub name: String,
    pub base_url: String,
    pub api_key: Secret,
    pub headers: Vec<(String, Secret)>,
    pub protected_credentials: Option<Secret>,
    pub credential_overrides: Option<Secret>,
    pub credential_error: Option<String>,
    /// `list[dict[str, Any]]`: each a [`Data::Dict`] with text keys.
    pub models: Vec<Data>,
}

/// `RulesetConfig`.
#[derive(Debug, Clone, PartialEq)]
pub struct RulesetConfig {
    pub default: String,
    pub path: String,
}

/// `TTSConfig`.
#[derive(Debug, Clone, PartialEq)]
pub struct TtsConfig {
    pub provider: String,
    pub enabled: bool,
    pub voice: String,
    pub voices: Vec<(String, String)>,
}

/// `SylviaGMConfig`.
#[derive(Debug, Clone, PartialEq)]
pub struct SylviaGMConfig {
    pub app: AppConfig,
    pub logging: LoggingConfig,
    /// `dict[str, ProviderDef]` in the document's order.
    pub providers: Vec<(String, ProviderDef)>,
    pub ruleset: RulesetConfig,
    pub tts: TtsConfig,
}

impl SylviaGMConfig {
    /// `config.providers.get(provider_id)`.
    pub fn provider(&self, provider_id: &str) -> Option<&ProviderDef> {
        self.providers.iter().find(|(id, _)| id == provider_id).map(|(_, definition)| definition)
    }
}

// ── Lax validation ────────────────────────────────────────────────────────────────────────

/// A validation failure; pydantic's details are never shown.
struct Invalid;

type Valid<T> = Result<T, Invalid>;

fn lax_str(value: &Data) -> Valid<String> {
    match value {
        Data::Str(text) => Ok(text.clone()),
        Data::Bytes(bytes) => String::from_utf8(bytes.clone()).map_err(|_| Invalid),
        _ => Err(Invalid),
    }
}

fn lax_bool(value: &Data) -> Valid<bool> {
    let word = |text: &str| match text.to_ascii_lowercase().as_str() {
        "0" | "off" | "f" | "false" | "n" | "no" => Ok(false),
        "1" | "on" | "t" | "true" | "y" | "yes" => Ok(true),
        _ => Err(Invalid),
    };
    match value {
        Data::Bool(flag) => Ok(*flag),
        Data::Int(0) => Ok(false),
        Data::Int(1) => Ok(true),
        Data::Float(number) if *number == 0.0 => Ok(false),
        Data::Float(number) if *number == 1.0 => Ok(true),
        Data::Str(text) => word(text),
        Data::Bytes(bytes) => word(std::str::from_utf8(bytes).map_err(|_| Invalid)?),
        _ => Err(Invalid),
    }
}

fn lax_optional_str(value: &Data) -> Valid<Option<String>> {
    match value {
        Data::None => Ok(None),
        other => lax_str(other).map(Some),
    }
}

/// `dict[str, X]`: a new dict, keys as text, a repeated key keeping its first place.
fn lax_dict<T>(value: &Data, item: impl Fn(&Data) -> Valid<T>) -> Valid<Vec<(String, T)>> {
    let Data::Dict(pairs) = value else { return Err(Invalid) };
    let mut out: Vec<(String, T)> = Vec::with_capacity(pairs.len());
    for (key, value) in pairs {
        let key = lax_str(key)?;
        let value = item(value)?;
        match out.iter_mut().find(|(existing, _)| *existing == key) {
            Some(slot) => slot.1 = value,
            None => out.push((key, value)),
        }
    }
    Ok(out)
}

/// `list[dict[str, Any]]`.
fn lax_model_rows(value: &Data) -> Valid<Vec<Data>> {
    let items = match value {
        Data::List(items) | Data::Tuple(items) | Data::Set(items) => items,
        _ => return Err(Invalid),
    };
    items
        .iter()
        .map(|row| {
            let pairs = lax_dict(row, |value| Ok(value.clone()))?;
            Ok(Data::Dict(pairs.into_iter().map(|(key, value)| (Data::Str(key), value)).collect()))
        })
        .collect()
}

/// A model's input: a dict whose text keys are looked up by field name; other keys ignored.
struct Fields<'a>(&'a [(Data, Data)]);

impl<'a> Fields<'a> {
    fn of(value: &'a Data) -> Valid<Self> {
        value.as_dict().map(Fields).ok_or(Invalid)
    }

    fn get(&self, name: &str) -> Option<&'a Data> {
        self.0.iter().find(|(key, _)| matches!(key, Data::Str(text) if text == name)).map(|(_, value)| value)
    }

    fn text(&self, name: &str, default: &str) -> Valid<String> {
        self.get(name).map_or_else(|| Ok(default.to_owned()), lax_str)
    }

    fn boolean(&self, name: &str, default: bool) -> Valid<bool> {
        self.get(name).map_or(Ok(default), lax_bool)
    }
}

fn app_config(value: Option<&Data>) -> Valid<AppConfig> {
    let empty = Data::Dict(Vec::new());
    let fields = Fields::of(value.unwrap_or(&empty))?;
    Ok(AppConfig {
        name: fields.text("name", "Sylvia GM")?,
        version: fields.text("version", "0.3.0-alpha.1")?,
        data_dir: fields.text("data_dir", "data")?,
        campaigns_dir: fields.text("campaigns_dir", "campaigns")?,
    })
}

fn logging_config(value: Option<&Data>) -> Valid<LoggingConfig> {
    let empty = Data::Dict(Vec::new());
    let fields = Fields::of(value.unwrap_or(&empty))?;
    Ok(LoggingConfig {
        level: fields.text("level", "INFO")?,
        format: fields.text("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s")?,
        file: fields.text("file", "data/sylvia-gm.log")?,
    })
}

fn provider_def(value: &Data) -> Valid<ProviderDef> {
    let fields = Fields::of(value)?;
    let secret = |value: &Data| lax_str(value).map(Secret);
    Ok(ProviderDef {
        enabled: fields.boolean("enabled", true)?,
        preset_id: fields.text("preset_id", "")?,
        mode: fields.text("mode", "")?,
        name: fields.text("name", "")?,
        base_url: fields.text("base_url", "")?,
        api_key: fields.get("api_key").map_or(Ok(Secret::default()), secret)?,
        headers: fields.get("headers").map_or(Ok(Vec::new()), |value| lax_dict(value, secret))?,
        protected_credentials: fields
            .get("protected_credentials")
            .map_or(Ok(None), |value| lax_optional_str(value).map(|text| text.map(Secret)))?,
        credential_overrides: fields
            .get("credential_overrides")
            .map_or(Ok(None), |value| lax_optional_str(value).map(|text| text.map(Secret)))?,
        credential_error: fields.get("credential_error").map_or(Ok(None), lax_optional_str)?,
        models: fields.get("models").map_or(Ok(Vec::new()), lax_model_rows)?,
    })
}

fn ruleset_config(value: Option<&Data>) -> Valid<RulesetConfig> {
    let empty = Data::Dict(Vec::new());
    let fields = Fields::of(value.unwrap_or(&empty))?;
    Ok(RulesetConfig { default: fields.text("default", "")?, path: fields.text("path", "rulesets")? })
}

fn tts_config(value: Option<&Data>) -> Valid<TtsConfig> {
    let empty = Data::Dict(Vec::new());
    let fields = Fields::of(value.unwrap_or(&empty))?;
    let default_voices = || {
        vec![
            ("dm".to_owned(), "en-US-AriaNeural".to_owned()),
            ("npc_female".to_owned(), "en-US-JennyNeural".to_owned()),
            ("npc_male".to_owned(), "en-US-GuyNeural".to_owned()),
        ]
    };
    Ok(TtsConfig {
        provider: fields.text("provider", "edge")?,
        enabled: fields.boolean("enabled", false)?,
        voice: fields.text("voice", "en-US-AriaNeural")?,
        voices: fields
            .get("voices")
            .map_or_else(|| Ok(default_voices()), |value| lax_dict(value, lax_str))?,
    })
}

/// `SylviaGMConfig(**merged_dict)`: keyword arguments must be text, so any other top-level key
/// fails as validation does.
fn sylvia_config(document: &Data) -> Valid<SylviaGMConfig> {
    let pairs = document.as_dict().ok_or(Invalid)?;
    if pairs.iter().any(|(key, _)| !matches!(key, Data::Str(_))) {
        return Err(Invalid);
    }
    let fields = Fields(pairs);
    // A field given as None fails, as a model or dict field does in pydantic.
    let present = |name: &str| fields.get(name);
    Ok(SylviaGMConfig {
        app: app_config(present("app"))?,
        logging: logging_config(present("logging"))?,
        providers: present("providers").map_or(Ok(Vec::new()), |value| lax_dict(value, provider_def))?,
        ruleset: ruleset_config(present("ruleset"))?,
        tts: tts_config(present("tts"))?,
    })
}

// ── Loading ───────────────────────────────────────────────────────────────────────────────

/// `_load_yaml(path)`: a YAML file read as a text stream, `{}` for an empty file.
fn load_yaml_file(path: &Path) -> Result<Data, ConfigFailure> {
    let shown = path.display().to_string();
    let bytes = std::fs::read(path).map_err(|error| match error.kind() {
        std::io::ErrorKind::NotFound => ConfigFailure::python(
            "FileNotFoundError",
            format!("[Errno 2] No such file or directory: {}", sylvia_base::pystr::repr(&shown)),
        ),
        _ => ConfigFailure::python("OSError", error.to_string()),
    })?;
    let text =
        pyyaml::read_text(&bytes).map_err(|message| ConfigFailure::python("UnicodeDecodeError", message))?;
    let loaded = pyyaml::safe_load_stream(&text, &shown)
        .map_err(|error| ConfigFailure::python(error.class_name(), error.to_string()))?;
    let data = loaded
        .to_data()
        .map_err(|_| ConfigFailure::python("RecursionError", "maximum recursion depth exceeded"))?;
    match data {
        None | Some(Data::None) => Ok(Data::Dict(Vec::new())),
        Some(document @ Data::Dict(_)) => Ok(document),
        Some(other) => Err(ConfigFailure::config(format!(
            "Config file {shown} must contain a mapping, got {}",
            type_name(&other)
        ))),
    }
}

fn str_key(text: &str) -> Data {
    Data::Str(text.to_owned())
}

/// Set `pairs[name] = value`, keeping an existing key's place.
fn assign(pairs: &mut Vec<(Data, Data)>, name: &str, value: Data) {
    let key = str_key(name);
    match pairs.iter_mut().find(|(existing, _)| data_key_eq(existing, &key)) {
        Some(slot) => slot.1 = value,
        None => pairs.push((key, value)),
    }
}

/// `_DEFAULT_CONFIG_PATH`: the shipped defaults. A packaged app keeps them in
/// `config/config.yaml` beside the executable (Python's bundle folder); a checkout has them at
/// its root.
pub fn default_config_path() -> PathBuf {
    let packaged = std::env::current_exe()
        .ok()
        .and_then(|exe| Some(exe.parent()?.join("config").join("config.yaml")))
        .filter(|path| path.is_file());
    packaged.unwrap_or_else(|| {
        let crate_folder = Path::new(env!("CARGO_MANIFEST_DIR"));
        let root = crate_folder.parent().and_then(Path::parent).unwrap_or(crate_folder);
        root.join("config").join("config.yaml")
    })
}

/// `load_config(user_config_path, load_dotenv_file=False, decrypt_credentials=False)`.
///
/// `default_config` is the shipped `config/config.yaml`; `env` is `os.environ`.
pub fn load_config(
    default_config: &Path,
    user_config_path: Option<&Path>,
    seal: &dyn Seal,
    env: &Environment,
) -> Result<SylviaGMConfig, ConfigFailure> {
    let default_dict = load_yaml_file(default_config)?;
    let mut user_dict = Data::Dict(Vec::new());
    if let Some(user_path) = user_config_path {
        if user_path.exists() {
            user_dict = read_user_settings(seal, user_path)?;
        }
    }
    let merged = Data::Dict(deep_merge(
        default_dict.as_dict().expect("a mapping"),
        user_dict.as_dict().expect("a mapping"),
    ));
    let merged = resolve_vars(&merged, "config", env)?;
    let mut merged = apply_env_overrides(&merged, env, "SYLVIA_GM_")?;

    // Keep protected material out of validation errors; it stays sealed until selected.
    let providers_key = str_key("providers");
    let providers_present = merged.get(&providers_key).is_some();
    if providers_present && merged.get(&providers_key).and_then(Data::as_dict).is_none() {
        return Err(ConfigFailure::config("Provider settings must contain a mapping"));
    }
    let environment_providers = apply_env_overrides(&Data::Dict(Vec::new()), env, "SYLVIA_GM_")?
        .get(&providers_key)
        .cloned()
        .unwrap_or(Data::Dict(Vec::new()));
    if providers_present {
        let pairs = merged.as_dict_mut().expect("a mapping");
        let providers =
            pairs.iter_mut().find(|(key, _)| data_key_eq(key, &providers_key)).map(|(_, value)| value);
        let providers = providers.and_then(Data::as_dict_mut).expect("checked above");
        for (provider_id, provider) in providers.iter_mut() {
            let Some(fields) = provider.as_dict_mut() else { continue };
            let envelope_key = str_key("protected_credentials");
            let Some(envelope) =
                fields.iter().find(|(key, _)| data_key_eq(key, &envelope_key)).map(|(_, v)| v.clone())
            else {
                continue;
            };
            assign(fields, "api_key", Data::Str(String::new()));
            assign(fields, "headers", Data::Dict(Vec::new()));
            let environment = match &environment_providers {
                Data::Dict(_) => {
                    environment_providers.get(provider_id).cloned().unwrap_or(Data::Dict(Vec::new()))
                }
                _ => Data::Dict(Vec::new()),
            };
            let mut overrides: Vec<(Data, Data)> = Vec::new();
            if environment.as_dict().is_some() {
                for name in ["api_key", "headers"] {
                    if let Some(value) = environment.get_str(name) {
                        overrides.push((str_key(name), value.clone()));
                    }
                }
            }
            let overrides = Data::Dict(overrides);
            let key_ok = overrides.get_str("api_key").is_none_or(|value| matches!(value, Data::Str(_)));
            let headers_ok = overrides.get_str("headers").is_none_or(|value| {
                value.as_dict().is_some_and(|pairs| {
                    pairs.iter().all(|(n, v)| n.as_str().is_some() && v.as_str().is_some())
                })
            });
            if !key_ok || !headers_ok {
                return Err(ConfigFailure::config(
                    "Provider credential environment overrides must contain text values",
                ));
            }
            assign(fields, "credential_overrides", Data::Str(json_dumps(&overrides, true)?));
            match json_dumps(&envelope, true) {
                Ok(text) => assign(fields, "protected_credentials", Data::Str(text)),
                Err(_) => {
                    assign(fields, "protected_credentials", Data::Str("null".into()));
                    assign(fields, "credential_error", Data::Str(UNAVAILABLE_MESSAGE.into()));
                }
            }
        }
    }
    sylvia_config(&merged)
        .map_err(|_| ConfigFailure::config("Invalid configuration; check provider and model settings"))
}

/// `provider_credentials(definition)`: unlock only the selected connection; unavailable
/// secrets never become empty requests.
pub fn provider_credentials(
    definition: &ProviderDef,
    seal: &dyn Seal,
    env: &Environment,
) -> Result<(String, Vec<(String, String)>), ConfigFailure> {
    if definition.credential_error.as_deref().is_some_and(|error| !error.is_empty()) {
        return Err(ConfigFailure::config(UNAVAILABLE_MESSAGE));
    }
    if let Some(protected) = &definition.protected_credentials {
        let envelope = sylvia_base::pyjson::loads(protected.expose())
            .ok()
            .and_then(|json| from_json(&json).ok())
            .ok_or_else(|| ConfigFailure::config(UNAVAILABLE_MESSAGE))?;
        let (mut key, mut headers) = unprotect_credentials(seal, &envelope, true, env)?;
        if let Some(overrides) = &definition.credential_overrides {
            let overrides = sylvia_base::pyjson::loads(overrides.expose())
                .map_err(|error| ConfigFailure::python("JSONDecodeError", error.to_string()))
                .and_then(|json| from_json(&json))?;
            if let Some(Data::Str(text)) = overrides.get_str("api_key") {
                key = text.clone();
            }
            if let Some(Data::Dict(pairs)) = overrides.get_str("headers") {
                headers = pairs
                    .iter()
                    .filter_map(|(name, value)| Some((name.as_str()?.to_owned(), value.as_str()?.to_owned())))
                    .collect();
            }
        }
        return Ok((key, headers));
    }
    Ok((
        definition.api_key.expose().to_owned(),
        definition.headers.iter().map(|(name, value)| (name.clone(), value.expose().to_owned())).collect(),
    ))
}
