//! `provider/_speed.py`: provider-native speed tiers and the per-turn selection.
//!
//! Speed is separate from reasoning effort, model identity and output limits. Sylvia only offers
//! a tier where the exact route documents or advertises it, sends the provider's own value, and
//! never claims the provider served the request at that tier.

use crate::model_facts::{claude_model_facts, openai_model_facts};
use crate::pyurl::UrlError;
use crate::reasoning::{native_name, secure_endpoint};
use crate::types::{transport, ModelInfo, SpeedOption};
use sylvia_base::pystr;
use sylvia_base::{CodedError, Json, JsonMap};

/// `STANDARD_SPEED`: the owner-protocol word for "send no speed setting"; never a provider value.
pub const STANDARD_SPEED: &str = "standard";

const OPENAI_SOURCE: &str = "https://developers.openai.com/api/docs/pricing";
const ANTHROPIC_SOURCE: &str = "https://platform.claude.com/docs/en/build-with-claude/fast-mode";

fn option(id: &str, name: &str, description: &str) -> SpeedOption {
    SpeedOption { id: id.into(), name: name.into(), description: description.into() }
}

fn openai_fast() -> SpeedOption {
    option(
        "fast",
        "Fast",
        "Requests OpenAI Fast mode. Billed at a higher per-token rate; OpenAI may still serve a request at \
         standard speed and price.",
    )
}

fn openai_flex() -> SpeedOption {
    option(
        "flex",
        "Flex",
        "Requests OpenAI Flex processing. Billed at a lower rate; replies are slower and can fail when \
         capacity is unavailable.",
    )
}

fn anthropic_fast() -> SpeedOption {
    option(
        "fast",
        "Fast",
        "Requests Anthropic fast mode. Billed at a higher per-token rate and limited separately from \
         standard speed.",
    )
}

/// One advertised tier as `speed_options` reads it: `value.get(...)` on a mapping.
#[derive(Debug, Clone, PartialEq)]
pub struct AdvertisedTier<'a> {
    /// `value.get("id")`: `None` when absent or not text.
    pub id: Option<&'a str>,
    /// `value.get("name")`.
    pub name: Option<&'a str>,
    /// `value.get("description", "")`: `Some("")` when absent, `None` when present but not text.
    pub description: Option<&'a str>,
}

/// The `service_tiers` value as `speed_options` sees it.
#[derive(Debug, Clone, PartialEq)]
pub enum AdvertisedTiers<'a> {
    /// `None`.
    Missing,
    /// A `list` or `tuple`: each item a mapping (`Some`) or something else (`None`).
    List(Vec<Option<AdvertisedTier<'a>>>),
    /// Anything else.
    Other,
}

impl<'a> AdvertisedTiers<'a> {
    /// A configuration value as `speed_options` sees it (`None` for a missing key).
    pub fn from_data(value: Option<&'a sylvia_base::pyyaml::Data>) -> Self {
        use sylvia_base::pyyaml::Data;
        match value {
            None | Some(Data::None) => Self::Missing,
            Some(Data::List(items) | Data::Tuple(items)) => Self::List(
                items
                    .iter()
                    .map(|item| {
                        item.as_dict().map(|_| AdvertisedTier {
                            id: item.get_str("id").and_then(Data::as_str),
                            name: item.get_str("name").and_then(Data::as_str),
                            description: match item.get_str("description") {
                                None => Some(""),
                                Some(value) => value.as_str(),
                            },
                        })
                    })
                    .collect(),
            ),
            Some(_) => Self::Other,
        }
    }
}

/// `speed_options(values)`: saved runtime-advertised tiers, validated.
pub fn speed_options(values: &AdvertisedTiers<'_>) -> Result<Option<Vec<SpeedOption>>, String> {
    let items = match values {
        AdvertisedTiers::Missing => return Ok(None),
        AdvertisedTiers::List(items) if items.len() <= 8 => items,
        _ => return Err("Speed tiers must be a short list of advertised options".into()),
    };
    let mut options: Vec<SpeedOption> = Vec::new();
    for item in items {
        let Some(tier) = item else { return Err("Speed tiers must be advertised options".into()) };
        let valid_id = tier.id.filter(|id| native_name(id) && *id != STANDARD_SPEED);
        let valid_name = tier.name.filter(|name| !pystr::strip(name).is_empty() && pystr::len(name) <= 40);
        let valid_description = tier.description.filter(|text| pystr::len(text) <= 200);
        let (Some(id), Some(name), Some(description)) = (valid_id, valid_name, valid_description) else {
            return Err("Speed tiers must name a native setting and a short label".into());
        };
        if !options.iter().any(|existing| existing.id == id) {
            options.push(option(id, pystr::strip(name), pystr::strip(description)));
        }
    }
    Ok(Some(options))
}

/// `configure_speed(model, base_url)`: documented tiers bound only to their actual endpoint,
/// transport and model. `Err` is Python's `ValueError` from `urlsplit`.
pub fn configure_speed(model: ModelInfo, base_url: &str) -> Result<ModelInfo, UrlError> {
    let mut model = model;
    if model.transport == transport::CODEX_APP_SERVER {
        let advertised = model.speed_options.take().filter(|options| !options.is_empty());
        model.speed_control = advertised.as_ref().map(|_| "codex".into());
        model.speed_source = if advertised.is_some() { "model/list" } else { "unknown" }.into();
        model.speed_options = advertised;
        return Ok(model);
    }
    let (endpoint, secure) = secure_endpoint(base_url)?;
    let hostname = endpoint.hostname();
    let wire_id = model.wire_model_id().to_owned();
    if secure
        && hostname.as_deref() == Some("api.openai.com")
        && matches!(model.transport.as_str(), transport::RESPONSES | transport::CHAT_COMPLETIONS)
    {
        let facts = openai_model_facts(&wire_id);
        let mut options = Vec::new();
        if facts.fast {
            options.push(openai_fast());
        }
        if facts.flex {
            options.push(openai_flex());
        }
        if !options.is_empty() {
            model.speed_options = Some(options);
            model.speed_control = Some("openai".into());
            model.speed_source = OPENAI_SOURCE.into();
            return Ok(model);
        }
    }
    if secure
        && hostname.as_deref() == Some("api.anthropic.com")
        && model.transport == transport::ANTHROPIC_MESSAGES
        && claude_model_facts(&wire_id).fast
    {
        model.speed_options = Some(vec![anthropic_fast()]);
        model.speed_control = Some("anthropic".into());
        model.speed_source = ANTHROPIC_SOURCE.into();
        return Ok(model);
    }
    model.speed_options = None;
    model.speed_control = None;
    model.speed_source = "unknown".into();
    Ok(model)
}

/// `supported_speed_ids(model)`: the native tier ids this model can be asked for.
pub fn supported_speed_ids(model: Option<&ModelInfo>) -> Vec<&str> {
    model
        .and_then(|model| model.speed_options.as_deref())
        .map(|options| options.iter().map(|option| option.id.as_str()).collect())
        .unwrap_or_default()
}

/// `SpeedSelection`: one reply's speed choice. `effective` is what Sylvia requests, not what the
/// provider confirmed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SpeedSelection {
    pub requested: Option<String>,
    pub effective: Option<String>,
    pub control: Option<String>,
    pub scope: String,
}

impl SpeedSelection {
    /// `options()`: the native request field, or nothing for the provider's own default.
    pub fn options(&self) -> JsonMap {
        let mut map = JsonMap::new();
        let Some(effective) = &self.effective else { return map };
        match self.control.as_deref() {
            Some("codex" | "openai") => {
                map.insert("service_tier".into(), Json::String(effective.clone()));
            }
            Some("anthropic") => {
                map.insert("speed".into(), Json::String(effective.clone()));
            }
            _ => {}
        }
        map
    }

    /// `projection()`.
    pub fn projection(&self) -> Json {
        let text = |value: &Option<String>| value.clone().map_or(Json::Null, Json::String);
        let mut map = JsonMap::new();
        map.insert("requested".into(), text(&self.requested));
        map.insert("effective_request".into(), text(&self.effective));
        map.insert("scope".into(), Json::String(self.scope.clone()));
        map.insert("native_options".into(), Json::Object(self.options()));
        map.insert("provider_reported_effective".into(), Json::Null);
        Json::Object(map)
    }
}

/// The refusal for a speed the model does not offer.
pub const UNSUPPORTED_SPEED: &str =
    "This model does not offer the selected speed. Choose an available speed or Standard before sending.";

/// `select_speed(model, requested, strict=..., scope=...)`. `requested` is `None` for Python's
/// `None` and `Some(None)` for a non-text value.
pub fn select_speed(
    model: Option<&ModelInfo>,
    requested: Option<Option<&str>>,
    strict: bool,
    scope: &str,
) -> Result<SpeedSelection, CodedError> {
    let control = model.and_then(|model| model.speed_control.clone());
    let selection = |requested: Option<&str>, effective: Option<&str>, scope: &str| SpeedSelection {
        requested: requested.map(str::to_owned),
        effective: effective.map(str::to_owned),
        control: control.clone(),
        scope: scope.to_owned(),
    };
    match requested {
        None | Some(Some(STANDARD_SPEED)) => return Ok(selection(None, None, "provider_default")),
        Some(Some(value)) if supported_speed_ids(model).contains(&value) => {
            return Ok(selection(Some(value), Some(value), scope));
        }
        _ => {}
    }
    if strict {
        return Err(CodedError::rejected(UNSUPPORTED_SPEED));
    }
    Ok(selection(requested.flatten(), None, "unavailable_for_model"))
}
