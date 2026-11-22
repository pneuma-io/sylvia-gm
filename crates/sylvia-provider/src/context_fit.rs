//! `provider/_context_fit.py`: request admission against the selected model's context, with
//! provider-managed (native runtime) limits left to the runtime.
//!
//! [`ContextFittingProvider`] measures every request before it is sent: the exact client
//! request representation when the provider chain has one ([`Provider::request_representation`]),
//! else the neutral request, encoded canonically. One token per UTF-8 byte bounds the input
//! from above; a request whose bound plus the reply reserve exceeds the effective context window
//! is refused with Python's `TokenLimitError` before anything is sent.
//!
//! Not ported: the `count_request_tokens` capability. No provider in the 0.4.1 snapshot defines
//! it, so the authoritative-count branch is unreachable and every measurement is a conservative
//! bound. The measured bytes include `timeout` as a JSON float, as every live Python caller
//! passes a float (`120.0`, `60.0`) and the opening preflight passes none.

use crate::catalog::{output_allowance, DEFAULT_OUTPUT_ALLOWANCE};
use crate::error::{ErrorKind, ProviderError, ProviderResult};
use crate::hooks::HookManager;
use crate::provider::{capability, BoxFuture, ChunkStream, Provider, ProviderRequest};
use crate::types::{transport, ModelInfo, ProviderResponse};
use futures_util::StreamExt;
use std::sync::{Arc, Mutex};
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};

/// `GM_MODE_MINIMUM_CONTEXT_TOKENS`: the loaded runtime a local GM model needs.
pub const GM_MODE_MINIMUM_CONTEXT_TOKENS: i64 = 131_000;

/// `RequestTokenMeasurement`: the preflight measurement of one exact prospective request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RequestTokenMeasurement {
    pub precision: &'static str,
    pub source: &'static str,
    pub request_layer: &'static str,
    pub request_bytes: usize,
    pub request_sha256: String,
    pub input_tokens: Option<i64>,
    pub input_token_bound: Option<i64>,
    pub output_token_reserve: i64,
    pub required_tokens: Option<i64>,
    pub required_token_bound: Option<i64>,
    pub effective_context_capacity: Option<i64>,
    pub capacity_source: String,
    pub admission_decision: &'static str,
    pub provider_transformation_overhead: &'static str,
    pub output_budget_mode: &'static str,
}

fn number(value: Option<i64>) -> Json {
    value.map_or(Json::Null, Json::from)
}

fn text(value: &Option<String>) -> Json {
    value.clone().map_or(Json::Null, Json::String)
}

impl RequestTokenMeasurement {
    /// `projection()`: the audit vocabulary, never relabelling a bound as a count.
    pub fn projection(&self) -> JsonMap {
        let mut map = JsonMap::new();
        let mut put = |key: &str, value: Json| {
            map.insert(key.to_owned(), value);
        };
        put("precision", self.precision.into());
        put("source", self.source.into());
        put("request_layer", self.request_layer.into());
        put("request_bytes", self.request_bytes.into());
        put("request_sha256", self.request_sha256.clone().into());
        put("input_tokens", number(self.input_tokens));
        put("input_token_bound", number(self.input_token_bound));
        put("output_token_reserve", self.output_token_reserve.into());
        put("required_tokens", number(self.required_tokens));
        put("required_token_bound", number(self.required_token_bound));
        put("effective_context_capacity", number(self.effective_context_capacity));
        put("capacity_source", self.capacity_source.clone().into());
        put("admission_decision", self.admission_decision.into());
        put("provider_transformation_overhead", self.provider_transformation_overhead.into());
        put("output_budget_mode", self.output_budget_mode.into());
        // Compatibility keys for existing diagnostic consumers.
        put("method", self.source.into());
        put("input_token_upper_bound", number(self.input_token_bound));
        put("required_token_upper_bound", number(self.required_token_bound));
        put("context_window", number(self.effective_context_capacity));
        map
    }
}

/// `f"{value:,}"`.
fn grouped(value: i64) -> String {
    let digits = value.unsigned_abs().to_string();
    let mut out = String::new();
    for (index, digit) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index).is_multiple_of(3) {
            out.push(',');
        }
        out.push(digit);
    }
    if value < 0 {
        out.insert(0, '-');
    }
    out
}

fn token_limit(message: impl Into<String>) -> ProviderError {
    ProviderError::new(ErrorKind::TokenLimit, message)
}

/// A request option as Python's `isinstance(value, int) and not isinstance(value, bool)` sees
/// it: an integer, saturated beyond `i64` (only its comparison with the limit matters).
fn int_option(value: &Json) -> Option<i64> {
    match value {
        Json::Number(number) if number.is_i64() => number.as_i64(),
        Json::Number(number) if number.is_u64() => Some(i64::MAX),
        _ => None,
    }
}

/// `output_allowance(model)` where the model may be absent (Python's `getattr` on `None`).
fn allowance(model: Option<&ModelInfo>) -> Option<i64> {
    match model {
        Some(model) => output_allowance(model, false),
        None => Some(DEFAULT_OUTPUT_ALLOWANCE),
    }
}

/// `ContextFittingProvider`: ordinary transports are checked here; native runtimes' final
/// assembled requests are deferred to them.
pub struct ContextFittingProvider {
    provider: Arc<dyn Provider>,
    audit_context: Mutex<JsonMap>,
    minimum_context_window: Option<i64>,
}

impl ContextFittingProvider {
    /// `ContextFittingProvider(provider, minimum_context_window=...)`.
    pub fn new(provider: Arc<dyn Provider>, minimum_context_window: Option<i64>) -> Self {
        Self { provider, audit_context: Mutex::new(JsonMap::new()), minimum_context_window }
    }

    /// The wrapped provider.
    pub fn inner(&self) -> &Arc<dyn Provider> {
        &self.provider
    }

    /// `runtime_managed`: native runtimes own output controls and the final input.
    pub fn runtime_managed(&self) -> bool {
        self.provider.model_info().is_some_and(|model| {
            matches!(model.transport.as_str(), transport::CODEX_APP_SERVER | transport::CLAUDE_CODE)
        })
    }

    /// `admit_request(messages, tools, **options)`: preflight a prospective request without
    /// contacting the provider.
    pub fn admit_request(
        &self,
        messages: &[Json],
        tools: Option<&[Json]>,
        options: &JsonMap,
    ) -> ProviderResult<RequestTokenMeasurement> {
        self.admit(messages, tools, options)
    }

    fn unselected() -> RequestTokenMeasurement {
        RequestTokenMeasurement {
            precision: "unavailable",
            source: "unselected_in_process_provider",
            request_layer: "neutral_application_request",
            request_bytes: 0,
            request_sha256: sha256_hex(b""),
            input_tokens: None,
            input_token_bound: None,
            output_token_reserve: 0,
            required_tokens: None,
            required_token_bound: None,
            effective_context_capacity: None,
            capacity_source: "unknown".into(),
            admission_decision: "admitted",
            provider_transformation_overhead: "unavailable",
            output_budget_mode: "enforced",
        }
    }

    fn admit(
        &self,
        messages: &[Json],
        tools: Option<&[Json]>,
        options: &JsonMap,
    ) -> ProviderResult<RequestTokenMeasurement> {
        // Synthetic in-process providers have no selected wire model.
        let Some(model) = self.provider.model_info() else { return Ok(Self::unselected()) };
        let runtime_managed = self.runtime_managed();
        let output_limit = output_allowance(model, false).unwrap_or(0);
        let valid_context_window = model.context_window.filter(|&window| window > 0);
        let valid_runtime_context_window = model.runtime_context_window.filter(|&window| window > 0);
        let minimum_context_satisfied = self.minimum_context_window.is_none_or(|minimum| {
            valid_runtime_context_window.is_some_and(|window| window >= minimum)
                && valid_context_window.is_some_and(|window| window >= minimum)
        });
        // `options.get("max_tokens", options.get("max_output_tokens", output_limit))`.
        let requested = options.get("max_tokens").or_else(|| options.get("max_output_tokens"));
        let requested_output = match requested.map_or(Some(output_limit), int_option) {
            Some(value) if value > 0 => value,
            _ => output_limit,
        };
        if requested_output > output_limit && !runtime_managed {
            return Err(token_limit(
                "Requested output reserve exceeds the configured output token limit. The Campaign Draft was preserved.",
            ));
        }
        let output_reserve = if runtime_managed { 0 } else { requested_output };

        let representation =
            capability(self.provider.as_ref(), |p| p.request_representation(messages, tools, options));
        let (request_bytes, request_layer, mut overhead) = match representation {
            Some(representation) => (
                pyjson::dump_bytes(&Json::Object(representation?), Dump::CANONICAL),
                "provider_client_request_representation",
                "included_in_measured_provider_representation",
            ),
            None => {
                let mut neutral = JsonMap::new();
                neutral.insert("messages".into(), Json::Array(messages.to_vec()));
                neutral.insert("tools".into(), Json::Array(tools.map(<[Json]>::to_vec).unwrap_or_default()));
                neutral.insert("options".into(), Json::Object(options.clone()));
                (
                    pyjson::dump_bytes(&Json::Object(neutral), Dump::CANONICAL),
                    "neutral_application_request",
                    "covered_by_neutral_utf8_structural_bound",
                )
            }
        };
        if runtime_managed {
            overhead = "downstream_runtime_input_not_exposed";
        }
        // One token per UTF-8 byte: a deliberately conservative upper bound, never an estimate.
        let input_bound = request_bytes.len() as i64;
        let required_bound = input_bound + output_reserve;
        let decision = if runtime_managed || valid_context_window.is_none() {
            "runtime_deferred"
        } else if valid_context_window.is_some_and(|window| required_bound <= window)
            && minimum_context_satisfied
        {
            "admitted"
        } else {
            "rejected"
        };
        let measurement = RequestTokenMeasurement {
            precision: "conservative_bound",
            source: "conservative_utf8_upper_bound",
            request_layer,
            request_bytes: request_bytes.len(),
            request_sha256: sha256_hex(&request_bytes),
            input_tokens: None,
            input_token_bound: Some(input_bound),
            output_token_reserve: output_reserve,
            required_tokens: None,
            required_token_bound: Some(required_bound),
            effective_context_capacity: valid_context_window,
            capacity_source: model.context_window_source.clone(),
            admission_decision: decision,
            provider_transformation_overhead: overhead,
            output_budget_mode: if runtime_managed { "runtime_managed" } else { "enforced" },
        };
        let mut report = measurement.projection();
        report.insert("declared_context_window".into(), number(model.declared_context_window));
        report.insert("declared_context_source".into(), model.declared_context_source.clone().into());
        report.insert("runtime_context_window".into(), number(model.runtime_context_window));
        report.insert("runtime_context_source".into(), model.runtime_context_source.clone().into());
        report.insert("configured_context_window".into(), number(model.configured_context_window));
        report.insert("declared_context_observed_at".into(), text(&model.declared_context_observed_at));
        report.insert("runtime_context_observed_at".into(), text(&model.runtime_context_observed_at));
        report.insert("configured_context_observed_at".into(), text(&model.configured_context_observed_at));
        report.insert("context_observed_at".into(), text(&model.context_observed_at));
        report.insert("minimum_context_window".into(), number(self.minimum_context_window));
        report.insert("minimum_context_satisfied".into(), minimum_context_satisfied.into());
        let audit = std::mem::take(&mut *self.audit_context.lock().expect("audit context lock"));
        let mut evidence = audit;
        evidence.insert("context_fit".into(), Json::Object(report));
        // The recorder is found down the wrapper chain; the setter is the wrapped provider's own.
        capability(self.provider.as_ref(), |p| p.record_context_fit(&evidence).then_some(()));
        self.provider.set_audit_context(&evidence);

        if let (false, Some(minimum)) = (minimum_context_satisfied, self.minimum_context_window) {
            let model_name = if model.name.is_empty() { model.id.clone() } else { model.name.clone() };
            let required = grouped(minimum);
            let detected = match valid_runtime_context_window {
                None => "its loaded runtime is unknown".to_owned(),
                Some(runtime) => format!(
                    "it reports {} loaded-runtime tokens and {} effective tokens",
                    grouped(runtime),
                    valid_context_window.map_or_else(|| "unknown".to_owned(), grouped),
                ),
            };
            return Err(token_limit(format!(
                "Local GM model '{model_name}' ({}) cannot start or resume because {detected}; {required} tokens \
                 are required. Reload a local model with at least {required} context tokens. In Settings > Models, \
                 expand Connection and choose Add another model, then Find models. Select the loaded model again \
                 and use Add model, then Manual Retry. The request was not sent.",
                model.id
            )));
        }
        if decision == "rejected" {
            return Err(token_limit(format!(
                "Complete request cannot be shown to fit the selected model ({} token upper bound including \
                 reply reserve; {} available). Choose a larger-context model or reduce the current bounded \
                 answer. The Campaign Draft was preserved.",
                grouped(required_bound),
                valid_context_window.map_or_else(String::new, grouped),
            )));
        }
        Ok(measurement)
    }

    /// The call's options as Python builds them: native runtimes lose `max_tokens` and
    /// `max_output_tokens`; other providers get `max_tokens` from the output allowance when
    /// neither is given.
    fn call_options(&self, mut options: JsonMap) -> JsonMap {
        if self.runtime_managed() {
            options.shift_remove("max_tokens");
            options.shift_remove("max_output_tokens");
            return options;
        }
        if !options.contains_key("max_tokens") && !options.contains_key("max_output_tokens") {
            if let Some(limit) = allowance(self.provider.model_info()).filter(|&limit| limit > 0) {
                options.insert("max_tokens".into(), limit.into());
            }
        }
        options
    }

    /// `{"stream": stream, "timeout": timeout, **kwargs}`.
    fn admission_options(stream: bool, timeout: f64, kwargs: &JsonMap) -> JsonMap {
        let mut options = JsonMap::new();
        options.insert("stream".into(), stream.into());
        options
            .insert("timeout".into(), serde_json::Number::from_f64(timeout).map_or(Json::Null, Json::Number));
        for (key, value) in kwargs {
            options.insert(key.clone(), value.clone());
        }
        options
    }
}

impl Provider for ContextFittingProvider {
    fn model_info(&self) -> Option<&ModelInfo> {
        self.provider.model_info()
    }

    fn generate(&self, mut request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        Box::pin(async move {
            request.options = self.call_options(request.options);
            let options = Self::admission_options(request.stream, request.timeout, &request.options);
            self.admit(&request.messages, request.tools.as_deref(), &options)?;
            self.provider.generate(request).await
        })
    }

    fn generate_stream(&self, mut request: ProviderRequest) -> ChunkStream<'_> {
        // An async generator: nothing is measured until the first chunk is asked for.
        let admitted = futures_util::stream::once(async move {
            request.options = self.call_options(request.options);
            let options = Self::admission_options(true, request.timeout, &request.options);
            self.admit(&request.messages, request.tools.as_deref(), &options).map(|_| request)
        });
        Box::pin(admitted.flat_map(move |admitted| match admitted {
            Ok(request) => self.provider.generate_stream(request),
            Err(error) => Box::pin(futures_util::stream::once(async move { Err(error) })) as ChunkStream<'_>,
        }))
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

    /// `set_audit_context(context)`: kept for the next admission's evidence.
    fn set_audit_context(&self, context: &JsonMap) -> bool {
        *self.audit_context.lock().expect("audit context lock") = context.clone();
        true
    }
}

/// `_context_fitted_provider(provider, gm_mode=...)` (`interfaces/owner_process.py`): the one
/// request-fit wrapper every live Builder and GM call path uses. A local GM model must load at
/// least [`GM_MODE_MINIMUM_CONTEXT_TOKENS`].
pub fn context_fitted_provider(provider: Arc<dyn Provider>, gm_mode: bool) -> ContextFittingProvider {
    let local =
        provider.model_info().is_some_and(|model| matches!(model.provider_mode.as_str(), "local" | "ollama"));
    ContextFittingProvider::new(provider, (gm_mode && local).then_some(GM_MODE_MINIMUM_CONTEXT_TOKENS))
}
