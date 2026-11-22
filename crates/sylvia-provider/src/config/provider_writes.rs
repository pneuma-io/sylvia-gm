//! `config/provider_settings.py`, the writes (C4): saving and removing a connection, the default
//! model and new-chat defaults, per-chat speed, the one-turn model choice, and the ChatGPT
//! saved-model refresh.
//!
//! Each runs under `CONFIG_LOCK` (`_serialized_settings_write`) and reads, changes and
//! atomically rewrites the owner-local settings file. Python's `object` arguments are [`Data`]
//! (`Data::None` where the caller passed nothing).

use super::presets::{preset, Preset};
use super::protection::unprotect_credentials;
use super::provider_settings::{
    capacity_from_model_row, capacity_pairs, capacity_source, capacity_timestamp, chat_defaults,
    configured_model_exists, dict, is_profile_id, mapping_get, model_integer, native_setting,
    provider_preset_id, require_configured_model, text, ConfigSources,
};
use super::pyvalue::{from_json, truthy};
use super::user_settings::{read_user_settings, write_user_settings};
use super::{ConfigFailure, Data, CONFIG_LOCK};
use crate::catalog::DEFAULT_OUTPUT_ALLOWANCE;
use crate::context_capacity::{resolve_context_capacity, CapacityInputs};
use crate::model_facts::documented_model_defaults;
use crate::pyurl::urlsplit;
use crate::reasoning::{reasoning_values, suggested_from_data, Advertised};
use crate::routes::model_protocol;
use crate::speed::{speed_options, AdvertisedTiers};
use sha2::{Digest, Sha256};
use std::path::Path;
use sylvia_base::pystr;
use sylvia_base::pyyaml::data_key_eq;

const HEADER_COUNT_LIMIT: usize = 16;
const SESSION_SPEED_LIMIT: usize = 500;
const TRANSPORTS: [&str; 6] = [
    "chat_completions",
    "responses",
    "anthropic_messages",
    "codex_app_server",
    "gemini_generate_content",
    "claude_code",
];
const RUNTIME_MANAGED: [&str; 2] = ["codex_app_server", "claude_code"];

fn config(message: impl Into<String>) -> ConfigFailure {
    ConfigFailure::Config(message.into())
}

/// `mapping[key] = value`: an existing key keeps its place.
fn set(mapping: &mut Data, key: &str, value: Data) {
    let pairs = mapping.as_dict_mut().expect("a mapping");
    let key = text(key);
    match pairs.iter_mut().find(|(existing, _)| data_key_eq(existing, &key)) {
        Some(slot) => slot.1 = value,
        None => pairs.push((key, value)),
    }
}

/// `mapping.pop(key, None)`.
fn pop(mapping: &mut Data, key: &Data) -> Option<Data> {
    let pairs = mapping.as_dict_mut()?;
    let index = pairs.iter().position(|(existing, _)| data_key_eq(existing, key))?;
    Some(pairs.remove(index).1)
}

/// `mapping.get(key)` for a mutable mapping.
fn get_mut<'a>(mapping: &'a mut Data, key: &str) -> Option<&'a mut Data> {
    let key = text(key);
    mapping
        .as_dict_mut()?
        .iter_mut()
        .find(|(existing, _)| data_key_eq(existing, &key))
        .map(|(_, value)| value)
}

/// `document.setdefault(key, {})`.
fn setdefault_dict<'a>(document: &'a mut Data, key: &str) -> &'a mut Data {
    if document.get_str(key).is_none() {
        set(document, key, Data::Dict(Vec::new()));
    }
    get_mut(document, key).expect("just set")
}

/// `value == ""` or `None`.
fn missing(value: &Data) -> bool {
    matches!(value, Data::None) || matches!(value, Data::Str(text) if text.is_empty())
}

/// `_optional_model_integer(value, field, minimum=...)`.
fn optional_integer(value: &Data, field: &str, minimum: i64) -> Result<Option<i64>, ConfigFailure> {
    if missing(value) {
        return Ok(None);
    }
    model_integer(value, field, minimum, 10_000_000).map(Some)
}

/// Any character below U+0020.
fn has_control(value: &str) -> bool {
    value.chars().any(|c| (c as u32) < 32)
}

/// `_preset(preset_id)`.
pub(super) fn known_preset(preset_id: &Data) -> Result<&'static Preset, ConfigFailure> {
    preset_id.as_str().and_then(preset).ok_or_else(|| config("Provider preset is unsupported"))
}

/// `_base_url(preset_id, preset, value)`. `Err` may be Python's `ValueError` from `urlsplit`.
pub(super) fn base_url(preset: &Preset, value: &Data) -> Result<String, ConfigFailure> {
    let expected = preset.base_url;
    let mut candidate =
        value.as_str().map(|value| pystr::strip(value).trim_end_matches('/').to_owned()).unwrap_or_default();
    if candidate.is_empty() {
        candidate = expected.to_owned();
    }
    let historical = match preset.id {
        "opencode_go" => Some("https://opencode.ai/zen/go/v1"),
        "opencode_zen" => Some("https://opencode.ai/zen/v1"),
        _ => None,
    };
    if !preset.endpoint_editable && candidate != expected && Some(candidate.as_str()) != historical {
        return Err(config(format!("{} uses its official API endpoint", preset.name)));
    }
    if Some(candidate.as_str()) == historical {
        candidate = expected.to_owned();
    }
    let parsed = urlsplit(&candidate)
        .map_err(|error| ConfigFailure::Python { class: "ValueError", message: error.0 })?;
    if !matches!(parsed.scheme.as_str(), "http" | "https")
        || parsed.hostname().is_none_or(|host| host.is_empty())
        || parsed.username().is_some()
        || parsed.password().is_some()
        || !parsed.query.is_empty()
        || !parsed.fragment.is_empty()
    {
        return Err(config("Provider endpoint must be a plain HTTP(S) API base URL"));
    }
    Ok(candidate)
}

/// `_display_name(value, fallback)`.
fn display_name(value: &Data, fallback: &str) -> Result<String, ConfigFailure> {
    let mut candidate = value.as_str().map(|value| pystr::strip(value).to_owned()).unwrap_or_default();
    if candidate.is_empty() {
        candidate = fallback.to_owned();
    }
    if pystr::len(&candidate) > 120 || has_control(&candidate) {
        return Err(config("Provider display name is invalid"));
    }
    Ok(candidate)
}

/// `_provider_profile_id(provider_id, preset_id=..., display_name=..., base_url=..., document=...)`.
fn provider_profile_id(
    provider_id: &Data,
    preset_id: &str,
    name: &str,
    endpoint: &str,
    document: &Data,
) -> Result<String, ConfigFailure> {
    if let Some(requested) = provider_id.as_str().filter(|id| is_profile_id(id) && *id != "custom") {
        return Ok(requested.to_owned());
    }
    if preset_id != "custom" {
        return Ok(preset_id.to_owned());
    }
    let digest = hex::encode(Sha256::digest(format!("{}\n{endpoint}", pystr::casefold(name)).as_bytes()));
    let identity = format!("custom_{}", &digest[..16]);
    let existing =
        mapping_get(document.get_str("providers"), &identity).filter(|value| value.as_dict().is_some());
    if let Some(Data::Str(existing_url)) = mapping_get(existing, "base_url") {
        if existing_url.trim_end_matches('/') != endpoint {
            return Err(config("Custom provider identity collision"));
        }
    }
    Ok(identity)
}

/// `_model_text(value, field, limit)`.
fn model_text(value: &Data, field: &str, limit: usize) -> Result<String, ConfigFailure> {
    let result = match value {
        Data::Str(text) if !pystr::strip(text).is_empty() => pystr::strip(text).to_owned(),
        _ => return Err(config(format!("Provider model {field} is required"))),
    };
    if pystr::len(&result) > limit || has_control(&result) {
        return Err(config(format!("Provider model {field} is invalid")));
    }
    Ok(result)
}

/// `_transport(value, fallback=...)`.
fn transport(value: &Data, fallback: &str) -> Result<String, ConfigFailure> {
    let candidate = match value {
        Data::Str(text) if !text.is_empty() => text.as_str(),
        _ => fallback,
    };
    if !TRANSPORTS.contains(&candidate) {
        return Err(config("Provider model transport is unsupported"));
    }
    Ok(candidate.to_owned())
}

/// `_HEADER_NAME.fullmatch(name)`: 1 to 80 of ``[A-Za-z0-9!#$%&'*+.^_`|~-]``.
fn is_header_name(name: &str) -> bool {
    (1..=80).contains(&name.len())
        && name.chars().all(|c| c.is_ascii_alphanumeric() || "!#$%&'*+.^_`|~-".contains(c))
}

/// `_private_headers(value)`.
pub(super) fn private_headers(value: &Data) -> Result<Vec<(String, String)>, ConfigFailure> {
    if matches!(value, Data::None) {
        return Ok(Vec::new());
    }
    let pairs = match value.as_dict() {
        Some(pairs) if pairs.len() <= HEADER_COUNT_LIMIT => pairs,
        _ => return Err(config("Provider headers must be a bounded mapping")),
    };
    let mut result: Vec<(String, String)> = Vec::new();
    let mut folded: Vec<String> = Vec::new();
    for (raw_name, raw_value) in pairs {
        let Some(name) = raw_name.as_str().filter(|name| is_header_name(name)) else {
            return Err(config("Provider header name is invalid"));
        };
        let header = match raw_value {
            Data::Str(value) if !value.is_empty() && pystr::len(value) <= 1024 => value,
            _ => return Err(config("Each provider header value must contain 1–1024 characters")),
        };
        if header.chars().any(|c| (c as u32) < 32 && c != '\t') {
            return Err(config("Provider header value is invalid"));
        }
        let folded_name = pystr::casefold(name);
        if folded.contains(&folded_name) {
            return Err(config("Provider header names must be unique"));
        }
        folded.push(folded_name);
        result.push((name.to_owned(), header.clone()));
    }
    Ok(result)
}

fn headers_data(headers: &[(String, String)]) -> Data {
    Data::Dict(headers.iter().map(|(name, value)| (text(name), text(value))).collect())
}

fn opt_int(value: Option<i64>) -> Data {
    value.map_or(Data::None, Data::Int)
}

fn opt_bool(value: Option<bool>) -> Data {
    value.map_or(Data::None, Data::Bool)
}

fn opt_str(value: Option<&str>) -> Data {
    value.map_or(Data::None, text)
}

/// `save_provider_settings(...)`'s arguments, each as Python receives it ([`SaveProvider::default`]
/// is every argument `None`).
#[derive(Debug, Clone)]
pub struct SaveProvider {
    pub provider_id: Data,
    pub base_url: Data,
    pub credential: Data,
    pub model_id: Data,
    pub model_name: Data,
    pub context_window: Data,
    pub max_output_tokens: Data,
    pub declared_max_output_tokens: Data,
    pub builder_output_tokens: Data,
    pub preset_id: Data,
    pub display_name: Data,
    pub transport: Data,
    pub headers: Data,
    pub supports_streaming: Data,
    pub supports_tools: Data,
    pub supported_reasoning_efforts: Data,
    pub default_reasoning_effort: Data,
    pub service_tiers: Data,
    pub declared_context_window: Data,
    pub declared_context_source: Data,
    pub runtime_context_window: Data,
    pub runtime_context_source: Data,
    pub configured_context_window: Data,
    pub context_observed_at: Data,
    pub declared_context_observed_at: Data,
    pub runtime_context_observed_at: Data,
    pub configured_context_observed_at: Data,
}

impl Default for SaveProvider {
    fn default() -> Self {
        let none = || Data::None;
        Self {
            provider_id: none(),
            base_url: none(),
            credential: none(),
            model_id: none(),
            model_name: none(),
            context_window: none(),
            max_output_tokens: none(),
            declared_max_output_tokens: none(),
            builder_output_tokens: none(),
            preset_id: none(),
            display_name: none(),
            transport: none(),
            headers: none(),
            supports_streaming: none(),
            supports_tools: none(),
            supported_reasoning_efforts: none(),
            default_reasoning_effort: none(),
            service_tiers: none(),
            declared_context_window: none(),
            declared_context_source: none(),
            runtime_context_window: none(),
            runtime_context_source: none(),
            configured_context_window: none(),
            context_observed_at: none(),
            declared_context_observed_at: none(),
            runtime_context_observed_at: none(),
            configured_context_observed_at: none(),
        }
    }
}

/// `save_provider_settings(config_path, ...)`: add or update one model atomically without
/// replacing its provider's catalogue. `now` is `datetime.now(UTC).isoformat()`.
pub fn save_provider_settings(
    sources: ConfigSources<'_>,
    config_path: &Path,
    request: &SaveProvider,
    now: &dyn Fn() -> String,
) -> Result<Data, ConfigFailure> {
    let _guard = CONFIG_LOCK.acquire();
    let requested_id = request.provider_id.as_str().unwrap_or("").to_owned();
    let mut document = read_user_settings(sources.seal, config_path)?;
    setdefault_dict(&mut document, "providers");
    let providers = document.get_str("providers").expect("set above");
    if providers.as_dict().is_none() {
        return Err(config("The local providers setting must be a mapping"));
    }
    let existing_raw = mapping_get(Some(providers), &requested_id).filter(|value| value.as_dict().is_some());
    let selected = match &request.preset_id {
        Data::Str(preset_id) if !preset_id.is_empty() => Data::Str(preset_id.clone()),
        _ => text(provider_preset_id(&requested_id, existing_raw)),
    };
    let preset = known_preset(&selected)?;
    let preset_key = preset.id;
    let endpoint = base_url(preset, &request.base_url)?;
    let name = display_name(&request.display_name, preset.name)?;
    let profile_id = provider_profile_id(&request.provider_id, preset_key, &name, &endpoint, &document)?;
    let upstream_model_id = model_text(&request.model_id, "ID", 256)?;
    let display_source =
        if truthy(&request.model_name) { request.model_name.clone() } else { text(&upstream_model_id) };
    let model_display_name = model_text(&display_source, "name", 128)?;
    let defaults = documented_model_defaults(preset_key, &upstream_model_id);
    let mut declared_output =
        optional_integer(&request.declared_max_output_tokens, "model maximum output", 1)?;
    if declared_output.is_none() {
        declared_output = defaults.and_then(|defaults| defaults.max_output_tokens);
    }
    let configured_output = optional_integer(&request.max_output_tokens, "output limit", 1)?;
    let mut output = configured_output.or(declared_output);
    if let (Some(declared), Some(value)) = (declared_output, output) {
        output = Some(value.min(declared));
    }
    let fallback = model_protocol(preset_key, &upstream_model_id, None, preset.default_transport);
    let model_transport = transport(&request.transport, fallback)?;
    let runtime_managed = RUNTIME_MANAGED.contains(&model_transport.as_str());
    if output.is_none() && !runtime_managed {
        output = Some(DEFAULT_OUTPUT_ALLOWANCE);
    }
    if let Some(defaults) = defaults.filter(|_| preset_key == "openai") {
        if Some(model_transport.as_str()) != defaults.transport {
            return Err(config("Choose Responses for this model to use Sylvia's tools and reasoning"));
        }
    }
    if (model_transport == "codex_app_server") != (preset_key == "chatgpt_subscription") {
        return Err(config("ChatGPT subscription requires its managed runtime connection"));
    }
    if preset_key == "chatgpt_subscription" && (truthy(&request.credential) || truthy(&request.headers)) {
        return Err(config("Use managed ChatGPT sign-in; API keys and private headers are not accepted"));
    }
    if (model_transport == "claude_code") != (preset_key == "claude_runtime") {
        return Err(config("Claude requires its native runtime connection"));
    }
    if preset_key == "claude_runtime" {
        if truthy(&request.credential) || truthy(&request.headers) || profile_id != "claude_runtime" {
            return Err(config(
                "Use Claude's native sign-in; API keys, headers and alternate profiles are not accepted",
            ));
        }
        if configured_output.is_some() {
            return Err(config("The Claude runtime manages the output allowance"));
        }
    }
    let supplied =
        request.credential.as_str().map(|value| pystr::strip(value).to_owned()).unwrap_or_default();

    let providers = document.get_str("providers").expect("set above");
    let existing = match mapping_get(Some(providers), &profile_id) {
        None | Some(Data::None) => Data::Dict(Vec::new()),
        Some(value @ Data::Dict(_)) => value.clone(),
        Some(_) => return Err(config("The existing provider setting is invalid")),
    };
    let (old_key, old_headers) = match existing.get_str("protected_credentials") {
        None => (String::new(), Vec::new()),
        Some(envelope) => match unprotect_credentials(sources.seal, envelope, false, sources.env) {
            Ok(opened) => opened,
            Err(ConfigFailure::Config(message)) => {
                if supplied.is_empty() && !truthy(&request.headers) {
                    return Err(ConfigFailure::Config(message));
                }
                // Explicit recovery replaces the unavailable secret material as a whole.
                (String::new(), Vec::new())
            }
            Err(other) => return Err(other),
        },
    };
    let stored_credential = if supplied.is_empty() { old_key } else { supplied };
    if preset.credential_required && stored_credential.is_empty() {
        return Err(config(format!("{} requires an API key", preset.name)));
    }
    let submitted_headers = private_headers(&request.headers)?;
    let stored_headers = if submitted_headers.is_empty() { old_headers } else { submitted_headers };

    let models = match existing.get_str("models") {
        None => Vec::new(),
        Some(Data::List(models)) => models.clone(),
        Some(_) => return Err(config("The existing provider model catalog is invalid")),
    };
    let same_id = |row: &Data| {
        row.as_dict().is_some() && row.get_str("id").and_then(Data::as_str) == Some(&upstream_model_id)
    };
    let prior = models.iter().find(|row| same_id(row));
    let has_new_capacity = [
        &request.declared_context_window,
        &request.runtime_context_window,
        &request.configured_context_window,
    ]
    .iter()
    .any(|value| !missing(value));
    let mut legacy_context = None;
    if !has_new_capacity {
        legacy_context = optional_integer(&request.context_window, "legacy context window", 512)?;
        if legacy_context.is_none() {
            if let Some(prior) = prior {
                let legacy = capacity_from_model_row(prior, None, "model_catalog")?;
                if legacy.effective_context_source == "legacy_unverified" {
                    legacy_context = legacy.effective_context_window;
                }
            }
        }
    }
    let mut declared = optional_integer(&request.declared_context_window, "declared context window", 512)?;
    let mut runtime = optional_integer(&request.runtime_context_window, "runtime context window", 512)?;
    let mut configured =
        optional_integer(&request.configured_context_window, "configured context window", 512)?;
    let observed = capacity_timestamp(Some(&request.context_observed_at)).map(str::to_owned);
    let prior_capacity =
        prior.map(|prior| capacity_from_model_row(prior, None, "model_catalog")).transpose()?;
    let mut declared_observed = capacity_timestamp(Some(&request.declared_context_observed_at))
        .map(str::to_owned)
        .or_else(|| observed.clone());
    let mut runtime_observed = capacity_timestamp(Some(&request.runtime_context_observed_at))
        .map(str::to_owned)
        .or_else(|| observed.clone());
    let mut configured_observed =
        capacity_timestamp(Some(&request.configured_context_observed_at)).map(str::to_owned);
    let mut declared_source = capacity_source(Some(&request.declared_context_source), "unknown").to_owned();
    let mut runtime_source = capacity_source(Some(&request.runtime_context_source), "unknown").to_owned();
    if let Some(defaults) = defaults {
        if declared.is_none() {
            declared = defaults.context_window;
            declared_source = "model_catalog".into();
            declared_observed = defaults.documented_at.map(str::to_owned);
        }
        if runtime.is_none() {
            runtime = defaults.context_window;
            runtime_source = "model_catalog".into();
            runtime_observed = defaults.documented_at.map(str::to_owned);
        }
        // Old single-value caps still represent the user's selected allowance.
        if configured.is_none() && legacy_context.is_some() {
            configured = legacy_context;
        }
    }
    if configured.is_some() && configured_observed.is_none() {
        configured_observed = match &prior_capacity {
            Some(prior) if prior.configured_context_window == configured => {
                prior.configured_observed_at.clone()
            }
            _ => Some(now()),
        };
    }
    let capacity = resolve_context_capacity(&CapacityInputs {
        declared_context_window: declared,
        declared_context_source: Some(&declared_source),
        runtime_context_window: runtime,
        runtime_context_source: Some(&runtime_source),
        configured_context_window: configured,
        legacy_context_window: legacy_context,
        observed_at: observed.as_deref(),
        declared_observed_at: declared_observed.as_deref(),
        runtime_observed_at: runtime_observed.as_deref(),
        configured_observed_at: configured_observed.as_deref(),
    });
    if configured_output.is_none() && declared_output.is_none() {
        if let (Some(value), Some(window)) = (output, capacity.effective_context_window) {
            output = Some(value.min(window.div_euclid(4).max(1)));
        }
    }
    if let (Some(window), Some(value)) = (capacity.effective_context_window, output) {
        if !runtime_managed && value > window {
            return Err(config("Provider model output limit exceeds its effective context capacity"));
        }
    }
    let previous_builder = match prior.and_then(|prior| prior.get_str("builder_output_tokens")) {
        None | Some(Data::None) => None,
        Some(value) => Some(model_integer(value, "Saved Builder output limit", 1, 2_000_000)?),
    };
    let builder_budget = if matches!(request.builder_output_tokens, Data::None) {
        // `previous or 32_000`: a saved budget is at least 1, so `or` only falls through on `None`.
        previous_builder.unwrap_or(32_000).min(output.unwrap_or(32_000))
    } else {
        model_integer(
            &request.builder_output_tokens,
            "Builder output limit",
            1,
            output.unwrap_or(10_000_000),
        )?
    };
    let allowance_source = if configured_output.is_some() {
        "user_configured"
    } else if declared_output.is_some() {
        "provider_metadata"
    } else if runtime_managed {
        "runtime_managed"
    } else {
        "application_default"
    };
    let flag = |value: &Data, fallback: Option<bool>| match value {
        Data::Bool(flag) => Data::Bool(*flag),
        _ => opt_bool(fallback),
    };
    let mut pairs: Vec<(&str, Data)> =
        vec![("id", text(&upstream_model_id)), ("name", text(&model_display_name))];
    pairs.extend(capacity_pairs(&capacity));
    pairs.extend([
        ("max_output_tokens", opt_int(output)),
        ("declared_max_output_tokens", opt_int(declared_output)),
        ("configured_output_tokens", opt_int(configured_output)),
        ("output_allowance_source", text(allowance_source)),
        ("documented_source", opt_str(defaults.and_then(|defaults| defaults.documented_source))),
        ("documented_at", opt_str(defaults.and_then(|defaults| defaults.documented_at))),
        ("builder_output_tokens", Data::Int(builder_budget)),
        ("transport", text(&model_transport)),
        (
            "supports_streaming",
            flag(&request.supports_streaming, defaults.and_then(|defaults| defaults.supports_streaming)),
        ),
        (
            "supports_tools",
            flag(&request.supports_tools, defaults.and_then(|defaults| defaults.supports_tools)),
        ),
    ]);
    let mut replacement = dict(pairs);
    if model_transport == "codex_app_server" {
        let (efforts, suggested) = match (&request.supported_reasoning_efforts, prior) {
            (Data::None, Some(prior)) => {
                (prior.get_str("supported_reasoning_efforts"), prior.get_str("default_reasoning_effort"))
            }
            (efforts, _) => (Some(efforts), Some(&request.default_reasoning_effort)),
        };
        let (supported, suggested) =
            reasoning_values(&Advertised::from_data(efforts), suggested_from_data(suggested))
                .map_err(config)?;
        set(
            &mut replacement,
            "supported_reasoning_efforts",
            supported
                .map_or(Data::None, |values| Data::List(values.iter().map(|value| text(value)).collect())),
        );
        set(&mut replacement, "default_reasoning_effort", opt_str(suggested.as_deref()));
        let tiers = match (&request.service_tiers, prior) {
            (Data::None, Some(prior)) => prior.get_str("service_tiers"),
            (tiers, _) => Some(tiers),
        };
        let advertised = speed_options(&AdvertisedTiers::from_data(tiers)).map_err(config)?;
        if let Some(advertised) = advertised {
            let projected: Result<Vec<Data>, ConfigFailure> =
                advertised.iter().map(|tier| from_json(&tier.projection())).collect();
            set(&mut replacement, "service_tiers", Data::List(projected?));
        }
    }
    let mut updated_models = Vec::new();
    let mut replaced = false;
    for row in &models {
        if same_id(row) {
            updated_models.push(replacement.clone());
            replaced = true;
        } else if row.as_dict().is_some() {
            updated_models.push(row.clone());
        }
    }
    if !replaced {
        updated_models.push(replacement);
    }
    let entry = dict(vec![
        ("enabled", Data::Bool(true)),
        ("preset_id", text(preset_key)),
        ("mode", text(preset.mode)),
        ("name", text(&name)),
        ("base_url", text(&endpoint)),
        ("api_key", text(&stored_credential)),
        ("headers", headers_data(&stored_headers)),
        ("models", Data::List(updated_models)),
    ]);
    set(get_mut(&mut document, "providers").expect("set above"), &profile_id, entry);
    write_user_settings(sources.seal, config_path, &document)?;
    Ok(dict(vec![
        ("provider_id", text(&profile_id)),
        ("preset_id", text(preset_key)),
        ("name", text(&name)),
        ("model_id", text(&upstream_model_id)),
        (
            "canonical_model_id",
            text(&super::provider_settings::canonical_model_id(&profile_id, &upstream_model_id)),
        ),
        ("credential_configured", Data::Bool(!stored_credential.is_empty())),
    ]))
}

/// `set_default_model_settings(config_path, provider_id=..., model_id=...)`.
pub fn set_default_model_settings(
    sources: ConfigSources<'_>,
    config_path: &Path,
    provider_id: &Data,
    model_id: &Data,
) -> Result<Data, ConfigFailure> {
    let _guard = CONFIG_LOCK.acquire();
    let Some(provider_id) = provider_id.as_str().filter(|id| is_profile_id(id)) else {
        return Err(config("Default provider identity is invalid"));
    };
    let Some(model_id) = model_id.as_str().filter(|id| !id.is_empty()) else {
        return Err(config("Default model identity is invalid"));
    };
    let mut document = read_user_settings(sources.seal, config_path)?;
    if !configured_model_exists(&document, provider_id, model_id) {
        return Err(config("Default model must be exactly configured"));
    }
    let default = dict(vec![("provider_id", text(provider_id)), ("model_id", text(model_id))]);
    set(&mut document, "default_model", default.clone());
    write_user_settings(sources.seal, config_path, &document)?;
    Ok(default)
}

/// `set_chat_defaults_settings(config_path, provider_id=..., model_id=..., reasoning_effort=...,
/// speed=...)`: the model, effort and speed new chats start from.
pub fn set_chat_defaults_settings(
    sources: ConfigSources<'_>,
    config_path: &Path,
    provider_id: &Data,
    model_id: &Data,
    reasoning_effort: &Data,
    speed: &Data,
) -> Result<Data, ConfigFailure> {
    let _guard = CONFIG_LOCK.acquire();
    for (value, label) in [(reasoning_effort, "effort"), (speed, "speed")] {
        if !matches!(value, Data::None) && matches!(native_setting(Some(value)), Data::None) {
            return Err(config(format!("Default {label} must be a native setting name")));
        }
    }
    let selection = require_configured_model(sources, config_path, provider_id, model_id)?;
    let mut document = read_user_settings(sources.seal, config_path)?;
    set(&mut document, "default_model", selection.clone());
    if matches!(reasoning_effort, Data::None) && matches!(speed, Data::None) {
        pop(&mut document, &text("chat_defaults"));
    } else {
        set(
            &mut document,
            "chat_defaults",
            dict(vec![("reasoning_effort", reasoning_effort.clone()), ("speed", speed.clone())]),
        );
    }
    write_user_settings(sources.seal, config_path, &document)?;
    Ok(dict(vec![("default_model", selection), ("chat_defaults", chat_defaults(&document))]))
}

/// A session identity the per-chat settings accept.
fn session_key(session_id: &Data) -> Result<&str, ConfigFailure> {
    match session_id {
        Data::Str(id) if !id.is_empty() && id == pystr::strip(id) && pystr::len(id) <= 512 => Ok(id),
        _ => Err(config("Session identity is invalid")),
    }
}

/// `set_session_speed_settings(config_path, session_id=..., speed=...)`: save or clear one
/// chat's speed tier; the oldest entries fall back to standard speed once the list is full.
pub fn set_session_speed_settings(
    sources: ConfigSources<'_>,
    config_path: &Path,
    session_id: &Data,
    speed: &Data,
) -> Result<Data, ConfigFailure> {
    let _guard = CONFIG_LOCK.acquire();
    let session_id = text(session_key(session_id)?);
    if !matches!(speed, Data::None) && matches!(native_setting(Some(speed)), Data::None) {
        return Err(config("Chat speed must be a native setting name"));
    }
    let mut document = read_user_settings(sources.seal, config_path)?;
    let mut entries = match document.get_str("session_speed") {
        Some(Data::Dict(pairs)) => Data::Dict(pairs.clone()),
        _ => Data::Dict(Vec::new()),
    };
    // `entries.get(session_id) == speed`: an absent entry is `None`.
    let unchanged = match (entries.get(&session_id).unwrap_or(&Data::None), speed) {
        (Data::None, Data::None) => true,
        (Data::Str(saved), Data::Str(wanted)) => saved == wanted,
        _ => false,
    };
    if unchanged {
        return Ok(speed.clone());
    }
    pop(&mut entries, &session_id);
    if !matches!(speed, Data::None) {
        entries.as_dict_mut().expect("a mapping").push((session_id, speed.clone()));
        let pairs = entries.as_dict_mut().expect("a mapping");
        let stale = pairs.len().saturating_sub(SESSION_SPEED_LIMIT);
        pairs.drain(..stale);
    }
    if entries.as_dict().is_some_and(|pairs| !pairs.is_empty()) {
        set(&mut document, "session_speed", entries);
    } else {
        pop(&mut document, &text("session_speed"));
    }
    write_user_settings(sources.seal, config_path, &document)?;
    Ok(speed.clone())
}

/// `set_session_model_once_settings(config_path, session_id=..., provider_id=..., model_id=...)`:
/// stage one model for only the session's next provider turn.
pub fn set_session_model_once_settings(
    sources: ConfigSources<'_>,
    config_path: &Path,
    session_id: &Data,
    provider_id: &Data,
    model_id: &Data,
) -> Result<Data, ConfigFailure> {
    let _guard = CONFIG_LOCK.acquire();
    let session_id = session_key(session_id)?.to_owned();
    let selection = require_configured_model(sources, config_path, provider_id, model_id)?;
    let mut document = read_user_settings(sources.seal, config_path)?;
    let pending = setdefault_dict(&mut document, "session_model_once");
    if pending.as_dict().is_none() {
        return Err(config("Pending session model settings are invalid"));
    }
    set(pending, &session_id, selection.clone());
    write_user_settings(sources.seal, config_path, &document)?;
    Ok(selection)
}

/// `raw != {"provider_id": expected_provider_id, "model_id": expected_model_id}`, negated.
fn is_selection(raw: &Data, provider_id: &Data, model_id: &Data) -> bool {
    let Some(pairs) = raw.as_dict() else { return false };
    pairs.len() == 2
        && raw.get_str("provider_id").is_some_and(|value| data_key_eq(value, provider_id))
        && raw.get_str("model_id").is_some_and(|value| data_key_eq(value, model_id))
}

/// `clear_session_model_once_settings(config_path, session_id=..., expected_provider_id=...,
/// expected_model_id=...)`: consume only the pending one-turn selection that actually ran.
pub fn clear_session_model_once_settings(
    sources: ConfigSources<'_>,
    config_path: &Path,
    session_id: &Data,
    expected_provider_id: &Data,
    expected_model_id: &Data,
) -> Result<bool, ConfigFailure> {
    let _guard = CONFIG_LOCK.acquire();
    let Some(session_id) = session_id.as_str().filter(|id| !id.is_empty()) else { return Ok(false) };
    let session_id = text(session_id);
    let mut document = read_user_settings(sources.seal, config_path)?;
    let Some(pending) =
        get_mut(&mut document, "session_model_once").filter(|value| value.as_dict().is_some())
    else {
        return Ok(false);
    };
    if !pending.get(&session_id).is_some_and(|raw| is_selection(raw, expected_provider_id, expected_model_id))
    {
        return Ok(false);
    }
    pop(pending, &session_id);
    if pending.as_dict().is_some_and(<[(Data, Data)]>::is_empty) {
        pop(&mut document, &text("session_model_once"));
    }
    write_user_settings(sources.seal, config_path, &document)?;
    Ok(true)
}

/// `remove_provider_settings(config_path, provider_id)`: one owner-local connection, its
/// private values, and the default and one-turn selections that named it.
pub fn remove_provider_settings(
    sources: ConfigSources<'_>,
    config_path: &Path,
    provider_id: &Data,
) -> Result<(), ConfigFailure> {
    let _guard = CONFIG_LOCK.acquire();
    let Some(provider_id) = provider_id.as_str().filter(|id| is_profile_id(id)) else {
        return Err(config("Provider identity is invalid"));
    };
    let key = text(provider_id);
    let mut document = read_user_settings(sources.seal, config_path)?;
    if let Some(providers) = get_mut(&mut document, "providers").filter(|value| value.as_dict().is_some()) {
        pop(providers, &key);
    }
    let names_provider = |selection: &Data| {
        selection.as_dict().is_some()
            && selection.get_str("provider_id").is_some_and(|id| data_key_eq(id, &key))
    };
    if document.get_str("default_model").is_some_and(names_provider) {
        pop(&mut document, &text("default_model"));
    }
    if let Some(pending) =
        get_mut(&mut document, "session_model_once").filter(|value| value.as_dict().is_some())
    {
        pending.as_dict_mut().expect("a mapping").retain(|(_, selection)| !names_provider(selection));
        if pending.as_dict().is_some_and(<[(Data, Data)]>::is_empty) {
            pop(&mut document, &text("session_model_once"));
        }
    }
    write_user_settings(sources.seal, config_path, &document)
}

/// `refresh_subscription_reasoning(config_path, models)`: refresh advertised effort and speed
/// on saved ChatGPT subscription models only. `models` is the runtime's `model/list`.
pub fn refresh_subscription_reasoning(
    sources: ConfigSources<'_>,
    config_path: &Path,
    models: &[Data],
) -> Result<bool, ConfigFailure> {
    let _guard = CONFIG_LOCK.acquire();
    let mut document = read_user_settings(sources.seal, config_path)?;
    let Some(rows) = get_mut(&mut document, "providers")
        .filter(|value| value.as_dict().is_some())
        .and_then(|providers| get_mut(providers, "chatgpt_subscription"))
        .filter(|value| value.as_dict().is_some())
        .and_then(|configured| get_mut(configured, "models"))
    else {
        return Ok(false);
    };
    let Data::List(rows) = rows else { return Ok(false) };
    // `{item.get("id"): item ...}`: the last item with an id wins.
    let advertised_for = |id: &Data| {
        models.iter().rev().find(|item| {
            item.get_str("id").is_some_and(|item_id| item_id.as_str().is_some() && data_key_eq(item_id, id))
        })
    };
    let mut changed = false;
    for row in rows.iter_mut() {
        if row.as_dict().is_none()
            || row.get_str("transport").and_then(Data::as_str) != Some("codex_app_server")
        {
            continue;
        }
        let id = row.get_str("id").cloned().unwrap_or(Data::None);
        let Some(advertised) = advertised_for(&id) else {
            if row.get_str("service_tiers").is_none() {
                // The runtime answered and no longer lists this model: nothing to offer.
                set(row, "service_tiers", Data::List(Vec::new()));
                changed = true;
            }
            continue;
        };
        let (supported, suggested) = reasoning_values(
            &Advertised::from_data(advertised.get_str("supported_reasoning_efforts")),
            suggested_from_data(advertised.get_str("default_reasoning_effort")),
        )
        .map_err(|message| ConfigFailure::Python { class: "ValueError", message })?;
        let tiers =
            speed_options(&AdvertisedTiers::from_data(advertised.get_str("service_tiers"))).unwrap_or(None);
        let projected: Result<Vec<Data>, ConfigFailure> =
            tiers.unwrap_or_default().iter().map(|tier| from_json(&tier.projection())).collect();
        let wanted = [
            (
                "supported_reasoning_efforts",
                supported.map_or(Data::None, |values| {
                    Data::List(values.iter().map(|value| text(value)).collect())
                }),
            ),
            ("default_reasoning_effort", opt_str(suggested.as_deref())),
            // An empty list records "asked, none advertised", unlike a legacy missing key.
            ("service_tiers", Data::List(projected?)),
        ];
        for (key, value) in wanted {
            if row.get_str(key).is_none_or(|current| current != &value) {
                set(row, key, value);
                changed = true;
            }
        }
    }
    if changed {
        write_user_settings(sources.seal, config_path, &document)?;
    }
    Ok(changed)
}
