//! `config/provider_settings.py`, the read-only part (C2): the safe projections the desktop
//! Models surface reads, without credentials or header values.
//!
//! The writes (`save_provider_settings` and the `set_*` operations) come with C4, and
//! discovery and validation with the HTTP client.

use super::env::Environment;
use super::loader::{load_config, provider_credentials};
use super::presets::{preset, presets};
use super::protection::{Seal, UNAVAILABLE_MESSAGE};
use super::pyvalue::{from_json, py_str, truthy};
use super::user_settings::read_user_settings;
use super::{ConfigFailure, Data};
use crate::context_capacity::{
    resolve_context_capacity, CapacityInputs, ResolvedContextCapacity, CONTEXT_CAPACITY_SOURCES,
};
use crate::model_facts::{documented_model_defaults, documented_model_ids, Catalog};
use crate::routes::model_protocol;
use std::path::Path;

/// What every operation here reads besides the settings file: the shipped defaults
/// (`_DEFAULT_CONFIG_PATH`), the platform's credential protection, and `os.environ`.
#[derive(Clone, Copy)]
pub struct ConfigSources<'a> {
    pub default_config: &'a Path,
    pub seal: &'a dyn Seal,
    pub env: &'a Environment,
}

fn text(value: &str) -> Data {
    Data::Str(value.to_owned())
}

fn dict(pairs: Vec<(&str, Data)>) -> Data {
    Data::Dict(pairs.into_iter().map(|(key, value)| (text(key), value)).collect())
}

fn opt_text(value: Option<&str>) -> Data {
    value.map_or(Data::None, text)
}

fn opt_int(value: Option<i64>) -> Data {
    value.map_or(Data::None, Data::Int)
}

/// `mapping.get(key)` when the value is a mapping, `None` otherwise.
fn mapping_get<'a>(mapping: Option<&'a Data>, key: &str) -> Option<&'a Data> {
    mapping.filter(|value| value.as_dict().is_some()).and_then(|value| value.get_str(key))
}

/// `_PROFILE_ID.fullmatch(value)`: `[a-z][a-z0-9_]{0,63}`.
fn is_profile_id(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some('a'..='z'))
        && value.len() <= 64
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
}

/// `_NATIVE_SETTING.fullmatch(value)`: `[a-z][a-z0-9_-]{0,31}`.
fn is_native_setting(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some('a'..='z'))
        && value.len() <= 32
        && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-')
}

/// `_native_setting(value)`: a saved native option name, or `None`.
fn native_setting(value: Option<&Data>) -> Data {
    match value {
        Some(Data::Str(name)) if is_native_setting(name) => text(name),
        _ => Data::None,
    }
}

/// `_provider_preset_id(provider_id, raw)`.
fn provider_preset_id(provider_id: &str, raw: Option<&Data>) -> &'static str {
    if let Some(Data::Str(declared)) = mapping_get(raw, "preset_id") {
        if let Some(found) = preset(declared) {
            return found.id;
        }
    }
    preset(provider_id).map_or("custom", |found| found.id)
}

/// `_canonical_model_id(provider_id, upstream_model_id)`.
pub fn canonical_model_id(provider_id: &str, upstream_model_id: &str) -> String {
    format!("{}/{upstream_model_id}", provider_id.replace('_', "-"))
}

/// `_configured_model_exists(document, provider_id, canonical_model_id)`.
fn configured_model_exists(document: &Data, provider_id: &str, canonical: &str) -> bool {
    let provider = mapping_get(document.get_str("providers"), provider_id);
    let Some(Data::List(models)) = mapping_get(provider, "models") else { return false };
    models.iter().any(|model| match model.as_dict().and_then(|_| model.get_str("id")) {
        Some(Data::Str(upstream)) => canonical_model_id(provider_id, upstream) == canonical,
        _ => false,
    })
}

/// `require_configured_model(config_path, provider_id=..., model_id=...)`: one exact safe
/// configured selection, or `ConfigError`.
pub fn require_configured_model(
    sources: ConfigSources<'_>,
    config_path: &Path,
    provider_id: &Data,
    model_id: &Data,
) -> Result<Data, ConfigFailure> {
    let Data::Str(provider_id) = provider_id else {
        return Err(ConfigFailure::config("Provider identity is invalid"));
    };
    if !is_profile_id(provider_id) {
        return Err(ConfigFailure::config("Provider identity is invalid"));
    }
    let model_ok = matches!(model_id, Data::Str(id)
        if !id.is_empty() && id.chars().count() <= 512 && id == sylvia_base::pystr::strip(id));
    let Data::Str(model_id) = model_id else {
        return Err(ConfigFailure::config("Model identity is invalid"));
    };
    if !model_ok {
        return Err(ConfigFailure::config("Model identity is invalid"));
    }
    let document = read_user_settings(sources.seal, config_path)?;
    if !configured_model_exists(&document, provider_id, model_id) {
        return Err(ConfigFailure::config("The selected model is not configured"));
    }
    Ok(dict(vec![("provider_id", text(provider_id)), ("model_id", text(model_id))]))
}

/// `_model_integer(value, field, minimum, maximum)`.
fn model_integer(value: &Data, field: &str, minimum: i64, maximum: i64) -> Result<i64, ConfigFailure> {
    let Data::Int(number) = value else {
        return Err(ConfigFailure::config(format!("Provider model {field} must be an integer")));
    };
    if *number < minimum || *number > maximum {
        return Err(ConfigFailure::config(format!("Provider model {field} is outside the supported range")));
    }
    Ok(*number)
}

/// `_optional_model_integer(value, field)`: `None` for a missing or empty value.
fn optional_model_integer(value: Option<&Data>, field: &str) -> Result<Option<i64>, ConfigFailure> {
    match value {
        None | Some(Data::None) => Ok(None),
        Some(Data::Str(text)) if text.is_empty() => Ok(None),
        Some(value) => model_integer(value, field, 512, 10_000_000).map(Some),
    }
}

/// `_capacity_source(value, fallback=...)`.
fn capacity_source<'a>(value: Option<&'a Data>, fallback: &'a str) -> &'a str {
    match value {
        Some(Data::Str(name)) if CONTEXT_CAPACITY_SOURCES.contains(&name.as_str()) => name,
        _ => fallback,
    }
}

/// `_capacity_timestamp(value)`: one owner-recorded timestamp, never invented.
fn capacity_timestamp(value: Option<&Data>) -> Option<&str> {
    match value {
        Some(Data::Str(stamp)) if !stamp.is_empty() => Some(stamp),
        _ => None,
    }
}

/// `_capacity_from_model_row(row, declared_fallback=..., declared_fallback_source=...)`: the
/// capacity facts of a saved model, an old single value kept as legacy-unverified.
pub(crate) fn capacity_from_model_row(
    row: &Data,
    declared_fallback: Option<i64>,
    declared_fallback_source: &str,
) -> Result<ResolvedContextCapacity, ConfigFailure> {
    let has_capacity_facts = [
        "declared_context_window",
        "runtime_context_window",
        "configured_context_window",
        "effective_context_window",
    ]
    .iter()
    .any(|key| row.get_str(key).is_some());
    let fallback = declared_fallback.map(Data::Int);
    let declared = optional_model_integer(
        row.get_str("declared_context_window").or(fallback.as_ref()),
        "declared context window",
    )?;
    let runtime = optional_model_integer(row.get_str("runtime_context_window"), "runtime context window")?;
    let configured =
        optional_model_integer(row.get_str("configured_context_window"), "configured context window")?;
    let legacy = if has_capacity_facts {
        None
    } else {
        optional_model_integer(row.get_str("context_window"), "legacy context window")?
    };
    let observed_at = capacity_timestamp(row.get_str("context_observed_at"));
    Ok(resolve_context_capacity(&CapacityInputs {
        declared_context_window: declared,
        declared_context_source: Some(capacity_source(
            row.get_str("declared_context_source"),
            if declared.is_some() { declared_fallback_source } else { "unknown" },
        )),
        runtime_context_window: runtime,
        runtime_context_source: Some(capacity_source(row.get_str("runtime_context_source"), "unknown")),
        configured_context_window: configured,
        legacy_context_window: legacy,
        observed_at,
        declared_observed_at: capacity_timestamp(row.get_str("declared_context_observed_at")),
        runtime_observed_at: capacity_timestamp(row.get_str("runtime_context_observed_at")),
        configured_observed_at: capacity_timestamp(row.get_str("configured_context_observed_at")),
    }))
}

fn capacity_pairs(capacity: &ResolvedContextCapacity) -> Vec<(&'static str, Data)> {
    capacity
        .projection()
        .into_iter()
        .map(|(key, value)| (key, from_json(&value).expect("capacity values are small")))
        .collect()
}

/// `row.get(key, default)`.
fn row_get(row: &Data, key: &str, default: Data) -> Data {
    row.get_str(key).cloned().unwrap_or(default)
}

/// `_project_configured_model(provider_id, row, default_transport=...)`: one saved model with
/// its resolved capacity and no secrets.
fn project_configured_model(
    provider_id: &str,
    row: &Data,
    default_transport: &str,
) -> Result<Data, ConfigFailure> {
    let capacity = capacity_from_model_row(row, None, "model_catalog")?;
    let upstream_id = row_get(row, "id", Data::None);
    let canonical = if truthy(&upstream_id) {
        text(&canonical_model_id(provider_id, &py_str(&upstream_id)))
    } else {
        Data::None
    };
    let transport = row_get(row, "transport", text(default_transport));
    let runtime_managed =
        matches!(&transport, Data::Str(name) if name == "codex_app_server" || name == "claude_code");
    let mut pairs: Vec<(&str, Data)> = vec![
        ("id", upstream_id.clone()),
        ("canonical_id", canonical),
        ("name", row_get(row, "name", upstream_id)),
    ];
    pairs.extend(capacity_pairs(&capacity));
    let max_output = row_get(row, "max_output_tokens", Data::None);
    pairs.extend([
        ("max_output_tokens", max_output.clone()),
        ("declared_max_output_tokens", row_get(row, "declared_max_output_tokens", Data::None)),
        ("configured_output_tokens", row_get(row, "configured_output_tokens", max_output)),
        ("documented_source", row_get(row, "documented_source", Data::None)),
        ("output_allowance_source", row_get(row, "output_allowance_source", Data::None)),
        ("documented_at", row_get(row, "documented_at", Data::None)),
        ("builder_output_tokens", row_get(row, "builder_output_tokens", Data::None)),
        ("output_budget_mode", text(if runtime_managed { "runtime_managed" } else { "enforced" })),
        ("transport", transport),
        ("supports_streaming", row_get(row, "supports_streaming", Data::None)),
        ("supports_tools", row_get(row, "supports_tools", Data::None)),
        ("supported_reasoning_efforts", row_get(row, "supported_reasoning_efforts", Data::None)),
        ("default_reasoning_effort", row_get(row, "default_reasoning_effort", Data::None)),
        ("service_tiers", row_get(row, "service_tiers", Data::None)),
    ]);
    Ok(dict(pairs))
}

/// `_chat_defaults(document)`: the saved new-chat effort and speed; `None` where unset.
fn chat_defaults(document: &Data) -> Data {
    let raw = document.get_str("chat_defaults").filter(|value| value.as_dict().is_some());
    dict(vec![
        ("reasoning_effort", native_setting(mapping_get(raw, "reasoning_effort"))),
        ("speed", native_setting(mapping_get(raw, "speed"))),
    ])
}

/// `datetime.now(UTC).isoformat()`.
pub fn now_isoformat() -> String {
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    let seconds = now.as_secs() as i64;
    let micros = now.subsec_micros();
    let days = seconds.div_euclid(86_400);
    let rest = seconds.rem_euclid(86_400);
    // Civil date from days since 1970-01-01 (Howard Hinnant's algorithm).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    let mut out =
        format!("{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}", rest / 3600, rest / 60 % 60, rest % 60);
    if micros != 0 {
        out.push_str(&format!(".{micros:06}"));
    }
    out.push_str("+00:00");
    out
}

/// A positive-enough integer, as `isinstance(x, int) and not isinstance(x, bool) and x >= n`.
fn int_at_least(value: Option<&Data>, minimum: i64) -> Option<i64> {
    match value {
        Some(Data::Int(number)) if *number >= minimum => Some(*number),
        _ => None,
    }
}

fn defaults_get_int(defaults: Option<&Catalog>, pick: fn(&Catalog) -> Option<i64>) -> Option<i64> {
    defaults.and_then(pick)
}

/// `_discovery_model(row, preset_id=...)`: a service's model facts, without inferring account
/// access from a model's identity; `None` for a row that is not a usable chat model.
pub fn discovery_model(row: &Data, preset_id: &str, now: &dyn Fn() -> String) -> Option<Data> {
    let mode = preset(preset_id).map_or("", |found| found.mode);
    let default_transport = preset(preset_id).map_or("chat_completions", |found| found.default_transport);
    if preset_id == "together"
        && !matches!(row.get_str("type"), None | Some(Data::None))
        && row.get_str("type") != Some(&text("chat"))
    {
        return None;
    }
    if preset_id == "featherless" && row.get_str("available_on_current_plan") == Some(&Data::Bool(false)) {
        return None;
    }
    let capabilities = row.get_str("capabilities").filter(|value| value.as_dict().is_some());
    if preset_id == "mistral" && mapping_get(capabilities, "completion_chat") == Some(&Data::Bool(false)) {
        return None;
    }
    let Some(Data::Str(value)) = row.get_str("id") else { return None };
    let model_id = sylvia_base::pystr::strip(value);
    if model_id.is_empty() || model_id.chars().count() > 256 || model_id.chars().any(|c| (c as u32) < 32) {
        return None;
    }
    if (preset_id == "opencode_go" || preset_id == "opencode_zen")
        && sylvia_base::pystr::casefold(model_id).starts_with("jev-")
    {
        // OpenCode documents Jev as typed decisions through /systemone, not a GM conversation.
        return None;
    }
    let defaults = documented_model_defaults(preset_id, model_id);
    let default_name = defaults.as_ref().and_then(|d| d.name).map(text).unwrap_or(Data::None);
    let raw_name =
        row.get_str("name").or_else(|| row.get_str("display_name")).cloned().unwrap_or(default_name);
    let name = match &raw_name {
        Data::Str(name) if !sylvia_base::pystr::strip(name).is_empty() => {
            sylvia_base::pystr::strip(name).to_owned()
        }
        _ => model_id.to_owned(),
    };
    let mut context = row.get_str("context_length").or_else(|| row.get_str("context_window")).cloned();
    if matches!(context, None | Some(Data::None)) && preset_id == "anthropic" {
        context = row.get_str("max_input_tokens").cloned();
    }
    if matches!(context, None | Some(Data::None)) && preset_id == "mistral" {
        context = row.get_str("max_context_length").cloned();
    }
    let mut context = int_at_least(context.as_ref(), 512);
    if context.is_none() {
        let top = row.get_str("top_provider").filter(|value| value.as_dict().is_some());
        context = int_at_least(mapping_get(top, "context_length"), 512);
    }
    let mut context_source = if context.is_some() { "provider_metadata" } else { "unknown" };
    if context.is_none() {
        if let Some(documented) =
            defaults_get_int(defaults.as_ref(), |d| d.context_window).filter(|window| *window != 0)
        {
            context = Some(documented);
            context_source = "model_catalog";
        }
    }
    let mut maximum =
        row.get_str("max_completion_tokens").or_else(|| row.get_str("max_output_tokens")).cloned();
    if matches!(maximum, None | Some(Data::None)) && preset_id == "anthropic" {
        maximum = row.get_str("max_tokens").cloned();
    }
    let mut maximum_value = int_at_least(maximum.as_ref(), 1).map(Data::Int);
    if maximum_value.is_none() {
        let top = row.get_str("top_provider").filter(|value| value.as_dict().is_some());
        maximum_value = int_at_least(mapping_get(top, "max_completion_tokens"), 1).map(Data::Int);
    }
    let maximum = maximum_value
        .unwrap_or_else(|| opt_int(defaults_get_int(defaults.as_ref(), |d| d.max_output_tokens)));
    let mut supports_tools = opt_bool(defaults.as_ref().and_then(|d| d.supports_tools));
    let mut supports_streaming = opt_bool(defaults.as_ref().and_then(|d| d.supports_streaming));
    if preset_id == "mistral" {
        if let Some(Data::Bool(flag)) = mapping_get(capabilities, "function_calling") {
            supports_tools = Data::Bool(*flag);
        }
    }
    if let Some(Data::List(supported)) = row.get_str("supported_parameters") {
        let values: Vec<&str> = supported.iter().filter_map(Data::as_str).collect();
        supports_tools = Data::Bool(values.iter().any(|value| *value == "tools" || *value == "tool_choice"));
        if values.contains(&"stream") {
            supports_streaming = Data::Bool(true);
        }
    }
    // Featherless has a separate plan ceiling; /models alone is not the usable allowance.
    let hosted = mode == "byok" && preset_id != "featherless";
    let observed_at = if context_source == "model_catalog" {
        defaults.as_ref().and_then(|d| d.documented_at).map(str::to_owned)
    } else if context.is_some() && hosted {
        Some(now())
    } else {
        None
    };
    let capacity = resolve_context_capacity(&CapacityInputs {
        declared_context_window: context,
        declared_context_source: Some(context_source),
        // A hosted provider's own catalogue describes its served limit. For local and custom
        // endpoints a generic model maximum is not proof of the loaded KV cache.
        runtime_context_window: if hosted { context } else { None },
        runtime_context_source: Some(if context.is_some() && hosted { context_source } else { "unknown" }),
        observed_at: observed_at.as_deref(),
        ..CapacityInputs::default()
    });
    let declared_transport = row.get_str("transport").and_then(Data::as_str);
    let transport = model_protocol(preset_id, model_id, declared_transport, default_transport);
    let mut pairs: Vec<(&str, Data)> = vec![("id", text(model_id)), ("name", text(&name))];
    pairs.extend(capacity_pairs(&capacity));
    pairs.extend([
        ("max_output_tokens", maximum.clone()),
        ("declared_max_output_tokens", maximum),
        ("configured_output_tokens", Data::None),
        ("transport", text(transport)),
        ("supports_tools", supports_tools),
        ("supports_streaming", supports_streaming),
        ("documented_source", opt_text(defaults.as_ref().and_then(|d| d.documented_source))),
        ("documented_at", opt_text(defaults.as_ref().and_then(|d| d.documented_at))),
    ]);
    Some(dict(pairs))
}

fn opt_bool(value: Option<bool>) -> Data {
    value.map_or(Data::None, Data::Bool)
}

/// `provider_settings(config_path)`: safe provider metadata, no credentials or header values.
pub fn provider_settings(sources: ConfigSources<'_>, config_path: &Path) -> Result<Data, ConfigFailure> {
    let config = load_config(sources.default_config, Some(config_path), sources.seal, sources.env)?;
    let raw_document = read_user_settings(sources.seal, config_path)?;
    let raw_providers = raw_document.get_str("providers").filter(|value| value.as_dict().is_some());
    let mut providers: Vec<&(String, super::ProviderDef)> = config.providers.iter().collect();
    providers.sort_by(|a, b| a.0.cmp(&b.0));
    let mut configured = Vec::new();
    for (provider_id, definition) in providers {
        if !definition.enabled {
            continue;
        }
        let raw = mapping_get(raw_providers, provider_id).filter(|value| value.as_dict().is_some());
        let preset_id = provider_preset_id(provider_id, raw);
        let found = preset(preset_id).expect("a shipped preset");
        let (credential_configured, header_count, credential_error) =
            match provider_credentials(definition, sources.seal, sources.env) {
                Ok((credential, headers)) => (!credential.is_empty(), headers.len() as i64, Data::None),
                Err(ConfigFailure::Config(_)) => (true, 0, text(UNAVAILABLE_MESSAGE)),
                Err(other) => return Err(other),
            };
        let models = definition
            .models
            .iter()
            .map(|row| project_configured_model(provider_id, row, found.default_transport))
            .collect::<Result<Vec<_>, _>>()?;
        configured.push(dict(vec![
            ("provider_id", text(provider_id)),
            ("preset_id", text(preset_id)),
            ("name", text(if definition.name.is_empty() { found.name } else { &definition.name })),
            ("mode", text(if definition.mode.is_empty() { found.mode } else { &definition.mode })),
            ("base_url", text(&definition.base_url)),
            ("credential_configured", Data::Bool(credential_configured)),
            ("header_count", Data::Int(header_count)),
            ("credential_error", credential_error),
            ("models", Data::List(models)),
        ]));
    }
    let mut safe_default = Data::None;
    if let Some(default_model) =
        raw_document.get_str("default_model").filter(|value| value.as_dict().is_some())
    {
        if let (Some(Data::Str(provider_id)), Some(Data::Str(model_id))) =
            (default_model.get_str("provider_id"), default_model.get_str("model_id"))
        {
            if configured_model_exists(&raw_document, provider_id, model_id) {
                safe_default = dict(vec![("provider_id", text(provider_id)), ("model_id", text(model_id))]);
            }
        }
    }
    let model_defaults = documented_model_ids()
        .iter()
        .map(|(preset_id, model_id)| {
            let row = dict(vec![("id", text(model_id))]);
            let mut pairs = vec![(text("preset_id"), text(preset_id))];
            if let Some(Data::Dict(found)) = discovery_model(&row, preset_id, &now_isoformat) {
                pairs.extend(found);
            }
            Data::Dict(pairs)
        })
        .collect();
    Ok(dict(vec![
        ("presets", Data::List(presets().iter().map(|found| found.projection()).collect())),
        ("configured", Data::List(configured)),
        ("default_model", safe_default),
        ("chat_defaults", chat_defaults(&raw_document)),
        ("model_defaults", Data::List(model_defaults)),
    ]))
}

/// `session_speed_settings(config_path, session_id=...)`: one chat's saved speed tier; `None`
/// is standard speed.
pub fn session_speed_settings(
    sources: ConfigSources<'_>,
    config_path: &Path,
    session_id: &Data,
) -> Result<Data, ConfigFailure> {
    let Data::Str(session_id) = session_id else { return Ok(Data::None) };
    if session_id.is_empty() {
        return Ok(Data::None);
    }
    let document = read_user_settings(sources.seal, config_path)?;
    let saved = document.get_str("session_speed").filter(|value| value.as_dict().is_some());
    Ok(match saved {
        Some(saved) => native_setting(saved.get_str(session_id)),
        None => Data::None,
    })
}

/// `session_model_once_settings(config_path, session_id=...)`: one pending safe model override,
/// not consumed.
pub fn session_model_once_settings(
    sources: ConfigSources<'_>,
    config_path: &Path,
    session_id: &Data,
) -> Result<Data, ConfigFailure> {
    let Data::Str(session_id) = session_id else { return Ok(Data::None) };
    if session_id.is_empty() {
        return Ok(Data::None);
    }
    let document = read_user_settings(sources.seal, config_path)?;
    let pending = document.get_str("session_model_once").filter(|value| value.as_dict().is_some());
    let Some(raw) = mapping_get(pending, session_id).filter(|value| value.as_dict().is_some()) else {
        return Ok(Data::None);
    };
    let provider_id = raw.get_str("provider_id").cloned().unwrap_or(Data::None);
    let model_id = raw.get_str("model_id").cloned().unwrap_or(Data::None);
    match require_configured_model(sources, config_path, &provider_id, &model_id) {
        Ok(selection) => Ok(selection),
        Err(ConfigFailure::Config(_)) => Ok(Data::None),
        Err(other) => Err(other),
    }
}

/// `open_provider_setup(preset_id)`: open only a shipped service's setup page, never an address
/// the renderer supplies.
pub fn open_provider_setup(preset_id: &Data) -> Result<Data, ConfigFailure> {
    let unsupported = || ConfigFailure::config("Provider preset is unsupported");
    let Data::Str(preset_id) = preset_id else { return Err(unsupported()) };
    let found = preset(preset_id).ok_or_else(unsupported)?;
    let Some(url) = found.setup_url else {
        return Err(ConfigFailure::config("Ask your custom service for its connection instructions"));
    };
    let opened = super::host_browser::open_in_browser(url)?;
    Ok(dict(vec![("opened", Data::Bool(opened)), ("url", text(url))]))
}
