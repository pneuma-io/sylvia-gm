//! `provider/_reasoning.py`: provider-native reasoning capabilities and the per-turn selection.
//!
//! [`TurnReasoningProvider`] and [`bind_session_reasoning`] wrap a provider for one reply.

use crate::error::ProviderResult;
use crate::hooks::HookManager;
use crate::model_facts::{
    claude_model_facts, documented_model_defaults, documented_openai_id, openai_model_facts, Present,
};
use crate::provider::{BoxFuture, ChunkStream, Provider, ProviderRequest};
use crate::pyurl::{urlsplit, UrlError};
use crate::speed::{select_speed, SpeedSelection};
use crate::types::{transport, ModelInfo, ProviderResponse};
use std::sync::Arc;
use sylvia_base::{CodedError, Json, JsonMap};

/// A native setting name: `re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", value)`.
pub(crate) fn native_name(value: &str) -> bool {
    let mut characters = value.chars();
    characters.next().is_some_and(|first| first.is_ascii_lowercase())
        && value.len() <= 32
        && characters.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-')
}

/// A reasoning or speed value as Python's validators see it.
#[derive(Debug, Clone, PartialEq)]
pub enum Advertised<'a> {
    /// `None`.
    Missing,
    /// A `list` or `tuple`, each item a text or something else (`None`).
    List(Vec<Option<&'a str>>),
    /// Anything else.
    Other,
}

impl<'a> Advertised<'a> {
    /// A configuration value as `reasoning_values` sees it (`None` for a missing key).
    pub fn from_data(value: Option<&'a sylvia_base::pyyaml::Data>) -> Self {
        use sylvia_base::pyyaml::Data;
        match value {
            None | Some(Data::None) => Self::Missing,
            Some(Data::List(items) | Data::Tuple(items)) => {
                Self::List(items.iter().map(Data::as_str).collect())
            }
            Some(_) => Self::Other,
        }
    }
}

/// The `suggested` argument from a configuration value: `None` for a missing key or `None`.
pub fn suggested_from_data(value: Option<&sylvia_base::pyyaml::Data>) -> Option<Option<&str>> {
    match value {
        None | Some(sylvia_base::pyyaml::Data::None) => None,
        Some(value) => Some(value.as_str()),
    }
}

/// `reasoning_values(values, suggested)`: validated native names without inventing a universal
/// option list. `suggested` is `None` for Python's `None` and `Some(None)` for a non-text value.
pub fn reasoning_values(
    values: &Advertised<'_>,
    suggested: Option<Option<&str>>,
) -> Result<(Option<Vec<String>>, Option<String>), String> {
    let items = match values {
        Advertised::Missing => {
            if suggested.is_some() {
                return Err("A reasoning default requires advertised reasoning options".into());
            }
            return Ok((None, None));
        }
        Advertised::Other => None,
        Advertised::List(items) => Some(items),
    };
    let Some(items) =
        items.filter(|items| items.len() <= 16 && items.iter().all(|item| item.is_some_and(native_name)))
    else {
        return Err("Reasoning options must be a short list of native setting names".into());
    };
    let mut supported: Vec<String> = Vec::new();
    for item in items.iter().flatten() {
        if !supported.iter().any(|existing| existing == item) {
            supported.push((*item).to_owned());
        }
    }
    let suggested = match suggested {
        None => None,
        Some(Some(text)) if supported.iter().any(|value| value == text) => Some(text.to_owned()),
        Some(_) => return Err("The suggested reasoning default must be an advertised option".into()),
    };
    Ok((Some(supported), suggested))
}

fn efforts(values: &[&str]) -> Option<Vec<String>> {
    Some(values.iter().map(|value| (*value).to_owned()).collect())
}

/// Whether `base_url` is `https` on the default port. Python evaluates `port` only for `https`,
/// so only then can a bad port raise.
pub(crate) fn secure_endpoint(base_url: &str) -> Result<(crate::pyurl::SplitUrl, bool), UrlError> {
    let endpoint = urlsplit(base_url)?;
    let secure = endpoint.scheme == "https" && matches!(endpoint.port()?, None | Some(443));
    Ok((endpoint, secure))
}

/// `path.rstrip("/")`.
fn without_trailing_slashes(path: &str) -> &str {
    path.trim_end_matches('/')
}

/// `configure_reasoning(model, base_url)`: documented controls bound only to their actual
/// endpoint and supported transport. `Err` is Python's `ValueError` from `urlsplit`.
pub fn configure_reasoning(model: ModelInfo, base_url: &str) -> Result<ModelInfo, UrlError> {
    let mut model = model;
    if model.transport == transport::CODEX_APP_SERVER {
        model.reasoning_control = Some("codex".into());
        model.reasoning_source =
            if model.supported_reasoning_efforts.is_some() { "model/list" } else { "unknown" }.into();
        return Ok(model);
    }
    let (endpoint, secure) = secure_endpoint(base_url)?;
    if !secure {
        model.supported_reasoning_efforts = None;
        model.default_reasoning_effort = None;
        return Ok(model);
    }
    let wire_id = model.wire_model_id().to_owned();
    let hostname = endpoint.hostname();
    let host = hostname.as_deref();
    let path = without_trailing_slashes(&endpoint.path);
    let native_route = match (host, path) {
        (Some("api.groq.com"), "/openai/v1") => Some(("groq", "https://console.groq.com/docs/reasoning")),
        (Some("api.deepseek.com"), "") => {
            Some(("deepseek", "https://api-docs.deepseek.com/api/create-chat-completion/"))
        }
        _ => None,
    };
    let documented_effort = |catalog: &crate::model_facts::Catalog| match catalog.default_reasoning_effort {
        Present::Present(value) => value.map(str::to_owned),
        Present::Absent => None,
    };
    if let Some((route, source)) = native_route {
        if model.transport == transport::CHAT_COMPLETIONS {
            if let Some(defaults) = documented_model_defaults(route, &wire_id) {
                model.supported_reasoning_efforts = defaults.supported_reasoning_efforts.and_then(efforts);
                model.default_reasoning_effort = documented_effort(&defaults);
                model.reasoning_control = Some("openai".into());
                model.reasoning_source = source.into();
                return Ok(model);
            }
        }
    }
    if host == Some("api.openai.com")
        && matches!(model.transport.as_str(), transport::RESPONSES | transport::CHAT_COMPLETIONS)
    {
        let defaults = documented_model_defaults("openai", &wire_id);
        if let Some(defaults) = defaults.filter(|_| model.transport == transport::RESPONSES) {
            model.supported_reasoning_efforts = defaults.supported_reasoning_efforts.and_then(efforts);
            model.default_reasoning_effort = documented_effort(&defaults);
            model.reasoning_control = Some("openai".into());
            model.reasoning_source = defaults.documented_source.unwrap_or_default().into();
            return Ok(model);
        }
        let facts = openai_model_facts(&wire_id);
        if let Some(values) = facts.efforts {
            model.supported_reasoning_efforts = efforts(values);
            model.default_reasoning_effort = Some(facts.default_effort.into());
            model.reasoning_control = Some("openai".into());
            model.reasoning_source =
                format!("https://developers.openai.com/api/docs/models/{}", documented_openai_id(&wire_id));
            return Ok(model);
        }
    }
    if host == Some("api.anthropic.com") && model.transport == transport::ANTHROPIC_MESSAGES {
        if let Some(values) = claude_model_facts(&wire_id).efforts {
            model.supported_reasoning_efforts = efforts(values);
            model.default_reasoning_effort = Some("high".into());
            model.reasoning_control = Some("anthropic".into());
            model.reasoning_source = "https://platform.claude.com/docs/en/build-with-claude/effort".into();
            return Ok(model);
        }
    }
    let minimax_endpoint = matches!(host, Some("api.minimax.io" | "api.minimaxi.com"))
        || (host == Some("opencode.ai") && matches!(path, "/zen/go/v1" | "/zen/v1"));
    if minimax_endpoint
        && model.transport == transport::ANTHROPIC_MESSAGES
        && sylvia_base::pystr::casefold(&wire_id) == "minimax-m3"
    {
        model.supported_reasoning_efforts = efforts(&["disabled", "adaptive"]);
        model.default_reasoning_effort = Some("disabled".into());
        model.reasoning_control = Some("adaptive".into());
        model.reasoning_source = "https://platform.minimax.io/docs/api-reference/text-anthropic-api".into();
        return Ok(model);
    }
    model.supported_reasoning_efforts = None;
    model.default_reasoning_effort = None;
    model.reasoning_control = None;
    model.reasoning_source = "unknown".into();
    Ok(model)
}

const SCALE: [&str; 8] = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"];

/// `mode_reasoning_default(model, role)`: GM High and Builder X-High as visible native values.
pub fn mode_reasoning_default(model: Option<&ModelInfo>, role: &str) -> Option<String> {
    let model = model?;
    let supported = model.supported_reasoning_efforts.as_deref().filter(|values| !values.is_empty())?;
    let has = |value: &str| supported.iter().any(|item| item == value);
    if model.reasoning_control.as_deref() == Some("adaptive") {
        return Some("adaptive".into());
    }
    let target = if role == "builder" { "xhigh" } else { "high" };
    if has(target) {
        return Some(target.into());
    }
    let rank = SCALE.iter().position(|value| *value == target).expect("on the scale");
    // `next(...) or (...)`: a found value is a non-empty name, so `or` only falls through on `None`.
    if let Some(lower) = SCALE[..rank].iter().rev().find(|value| has(value)) {
        return Some((*lower).into());
    }
    if let Some(default) = model.default_reasoning_effort.as_deref().filter(|value| has(value)) {
        return Some(default.into());
    }
    Some(
        SCALE[rank + 1..]
            .iter()
            .find(|value| has(value))
            .map_or_else(|| supported[0].clone(), |v| (*v).into()),
    )
}

/// `ReasoningSelection`: one reply's validated choice. `effective` is what Sylvia requests, not
/// what the provider confirmed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReasoningSelection {
    pub requested: Option<String>,
    pub effective: Option<String>,
    pub control: Option<String>,
    pub transport: Option<String>,
    pub scope: String,
}

fn object(pairs: Vec<(&str, Json)>) -> Json {
    Json::Object(pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect())
}

impl ReasoningSelection {
    /// `options()`: the native request fields, never budgets, tiers or models.
    pub fn options(&self) -> JsonMap {
        let mut map = JsonMap::new();
        let Some(effective) = &self.effective else { return map };
        let effort = || Json::String(effective.clone());
        match self.control.as_deref() {
            Some("codex") => {
                map.insert("reasoning_effort".into(), effort());
            }
            Some("openai") if self.transport.as_deref() == Some(transport::RESPONSES) => {
                map.insert("reasoning".into(), object(vec![("effort", effort())]));
            }
            Some("openai") => {
                map.insert("reasoning_effort".into(), effort());
            }
            Some("anthropic") => {
                map.insert("thinking".into(), object(vec![("type", Json::String("adaptive".into()))]));
                map.insert("output_config".into(), object(vec![("effort", effort())]));
            }
            Some("adaptive") => {
                map.insert("thinking".into(), object(vec![("type", effort())]));
            }
            _ => {}
        }
        map
    }

    /// `projection()`: the requested native options, kept apart from unreported provider behaviour.
    pub fn projection(&self) -> Json {
        let text = |value: &Option<String>| value.clone().map_or(Json::Null, Json::String);
        object(vec![
            ("requested", text(&self.requested)),
            ("effective_request", text(&self.effective)),
            ("scope", Json::String(self.scope.clone())),
            ("native_options", Json::Object(self.options())),
            ("provider_reported_effective", Json::Null),
        ])
    }
}

/// The refusal for a reasoning effort the model does not offer.
pub const UNSUPPORTED_REASONING: &str =
    "This model does not support the selected reasoning effort. Choose a \
                                         supported value or use the mode default before sending.";

/// `select_reasoning(model, role, requested, scope=...)`: an unsupported explicit value is
/// refused before inference; absence uses the role default. `requested` is `None` for Python's
/// `None` and `Some(None)` for a non-text value.
pub fn select_reasoning(
    model: Option<&ModelInfo>,
    role: &str,
    requested: Option<Option<&str>>,
    scope: &str,
) -> Result<ReasoningSelection, CodedError> {
    let supported = model.and_then(|model| model.supported_reasoning_efforts.as_deref());
    if let Some(value) = requested {
        let special = matches!(value, Some("provider_default" | "mode_default"));
        let offered = value.is_some_and(|value| {
            supported.is_some_and(|values| !values.is_empty() && values.iter().any(|item| item == value))
        });
        if !special && !offered {
            return Err(CodedError::rejected(UNSUPPORTED_REASONING));
        }
    }
    let requested = requested.flatten();
    let effective = match requested {
        None | Some("mode_default") => mode_reasoning_default(model, role),
        Some("provider_default") => None,
        Some(value) => Some(value.to_owned()),
    };
    Ok(ReasoningSelection {
        requested: requested.map(str::to_owned),
        effective,
        control: model.and_then(|model| model.reasoning_control.clone()),
        transport: model.map(|model| model.transport.clone()),
        scope: scope.to_owned(),
    })
}

/// `TurnReasoningProvider`: one immutable effort and speed selection applied to every provider
/// call in a reply. Tool continuations and retries inside the reply reuse the same selections.
pub struct TurnReasoningProvider {
    provider: Arc<dyn Provider>,
    /// `selection`.
    pub selection: ReasoningSelection,
    /// `speed`.
    pub speed: SpeedSelection,
}

impl TurnReasoningProvider {
    /// `TurnReasoningProvider(provider, selection, speed)`. `None` for `speed` is
    /// `SpeedSelection(None, None, None, "provider_default")`. Like Python's constructor, it
    /// calls `set_audit_context({})`, which hands the selections to the wrapped provider.
    pub fn new(
        provider: Arc<dyn Provider>,
        selection: ReasoningSelection,
        speed: Option<SpeedSelection>,
    ) -> Self {
        let speed = speed.unwrap_or(SpeedSelection {
            requested: None,
            effective: None,
            control: None,
            scope: "provider_default".into(),
        });
        let wrapper = Self { provider, selection, speed };
        wrapper.set_audit_context(&JsonMap::new());
        wrapper
    }

    /// The wrapped provider.
    pub fn inner(&self) -> &Arc<dyn Provider> {
        &self.provider
    }

    /// `{**context, **_selections()}`.
    fn with_selections(&self, context: &JsonMap) -> JsonMap {
        let mut merged = context.clone();
        merged.insert("reasoning_selection".into(), self.selection.projection());
        merged.insert("speed_selection".into(), self.speed.projection());
        merged
    }

    /// `_options(options)`: the snapshotted choice wins over call-local reasoning defaults.
    pub fn options(&self, options: &JsonMap) -> JsonMap {
        let mut result = options.clone();
        for key in ["reasoning_effort", "thinking", "service_tier", "speed"] {
            result.shift_remove(key);
        }
        for key in ["reasoning", "output_config"] {
            if let Some(Json::Object(value)) = result.get_mut(key) {
                value.shift_remove("effort");
                if value.is_empty() {
                    result.shift_remove(key);
                }
            }
        }
        for (key, value) in self.selection.options() {
            let merge = matches!(key.as_str(), "reasoning" | "output_config")
                && value.is_object()
                && matches!(result.get(&key), Some(Json::Object(_)));
            if !merge {
                result.insert(key, value);
            } else if let (Some(Json::Object(existing)), Json::Object(update)) = (result.get_mut(&key), value)
            {
                // `existing.update(value)`.
                for (name, item) in update {
                    existing.insert(name, item);
                }
            }
        }
        for (key, value) in self.speed.options() {
            result.insert(key, value);
        }
        result
    }

    fn bound(&self, mut request: ProviderRequest) -> ProviderRequest {
        request.options = self.options(&request.options);
        request
    }
}

impl Provider for TurnReasoningProvider {
    fn model_info(&self) -> Option<&ModelInfo> {
        self.provider.model_info()
    }

    fn generate(&self, request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        self.provider.generate(self.bound(request))
    }

    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        self.provider.generate_stream(self.bound(request))
    }

    fn close(&self) -> BoxFuture<'_, ()> {
        self.provider.close()
    }

    fn release_idle(&self) -> BoxFuture<'_, ()> {
        self.provider.release_idle()
    }

    fn hooks(&self) -> Option<&HookManager> {
        self.provider.hooks()
    }

    fn wrapped(&self) -> Option<&dyn Provider> {
        Some(self.provider.as_ref())
    }

    fn base_url(&self) -> Option<&str> {
        self.provider.base_url()
    }

    /// `provider_request_representation`: the first wrapped provider down the chain that has
    /// one, given this turn's options; otherwise the neutral request with them.
    fn request_representation(
        &self,
        messages: &[Json],
        tools: Option<&[Json]>,
        options: &JsonMap,
    ) -> Option<ProviderResult<JsonMap>> {
        let options = self.options(options);
        if let Some(value) = crate::provider::capability(self.provider.as_ref(), |p| {
            p.request_representation(messages, tools, &options)
        }) {
            return Some(value);
        }
        let mut neutral = JsonMap::new();
        neutral.insert("messages".into(), Json::Array(messages.to_vec()));
        neutral.insert("tools".into(), Json::Array(tools.map(<[Json]>::to_vec).unwrap_or_default()));
        neutral.insert("options".into(), Json::Object(options));
        Some(Ok(neutral))
    }

    /// Forward preflight evidence without losing the selected native options (one level down).
    fn record_context_fit(&self, context: &JsonMap) -> bool {
        self.provider.record_context_fit(&self.with_selections(context));
        true
    }

    /// Keep the turn's requested effort and speed beside request correlation (one level down).
    fn set_audit_context(&self, context: &JsonMap) -> bool {
        self.provider.set_audit_context(&self.with_selections(context));
        true
    }
}

/// `bind_session_reasoning(provider, session, speed)`: resolve the session's preferences once
/// for every call in its next provider turn. The session is given by its role
/// (`session.role.value`) and its two effort preferences. `speed` is the chat's saved tier;
/// a tier the answering model does not offer is simply not sent.
pub fn bind_session_reasoning(
    provider: Arc<dyn Provider>,
    role: &str,
    reasoning_effort: Option<&str>,
    reasoning_effort_once: Option<&str>,
    speed: Option<&str>,
) -> Result<TurnReasoningProvider, CodedError> {
    // `session.reasoning_effort_once or session.reasoning_effort`.
    let requested = reasoning_effort_once.filter(|value| !value.is_empty()).or(reasoning_effort);
    let scope = if reasoning_effort_once.is_some() {
        "once"
    } else if reasoning_effort.is_some() {
        "session"
    } else {
        "mode_default"
    };
    let model = provider.model_info();
    let selection = select_reasoning(model, role, requested.map(Some), scope)?;
    let speed = select_speed(model, speed.map(Some), false, "session")?;
    Ok(TurnReasoningProvider::new(provider, selection, Some(speed)))
}
