//! `provider/_context_capacity.py`: model context capacity, keeping a model's maximum, a
//! runtime's limit and a user's cap apart.

use sylvia_base::{Json, JsonMap};

/// `CONTEXT_CAPACITY_SOURCES`.
pub const CONTEXT_CAPACITY_SOURCES: [&str; 6] = [
    "runtime_reported",
    "user_configured",
    "provider_metadata",
    "model_catalog",
    "legacy_unverified",
    "unknown",
];

/// `_source(value, fallback=...)`: a known source name, else the fallback.
fn source<'a>(value: Option<&'a str>, fallback: &'a str) -> &'a str {
    match value {
        Some(value) if CONTEXT_CAPACITY_SOURCES.contains(&value) => value,
        _ => fallback,
    }
}

/// `ResolvedContextCapacity`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResolvedContextCapacity {
    pub declared_context_window: Option<i64>,
    pub declared_context_source: String,
    pub runtime_context_window: Option<i64>,
    pub runtime_context_source: String,
    pub configured_context_window: Option<i64>,
    pub effective_context_window: Option<i64>,
    pub effective_context_source: String,
    pub declared_observed_at: Option<String>,
    pub runtime_observed_at: Option<String>,
    pub configured_observed_at: Option<String>,
    pub configured_runtime_mismatch: bool,
    pub configured_declared_mismatch: bool,
}

/// The inputs of `resolve_context_capacity`, each defaulting as in Python.
#[derive(Debug, Clone, Default)]
pub struct CapacityInputs<'a> {
    pub declared_context_window: Option<i64>,
    pub declared_context_source: Option<&'a str>,
    pub runtime_context_window: Option<i64>,
    pub runtime_context_source: Option<&'a str>,
    pub configured_context_window: Option<i64>,
    pub legacy_context_window: Option<i64>,
    pub observed_at: Option<&'a str>,
    pub declared_observed_at: Option<&'a str>,
    pub runtime_observed_at: Option<&'a str>,
    pub configured_observed_at: Option<&'a str>,
}

/// `a or b` for optional text: an empty string falls through.
fn or<'a>(first: Option<&'a str>, second: Option<&'a str>) -> Option<&'a str> {
    match first {
        Some(text) if !text.is_empty() => Some(text),
        _ => second,
    }
}

/// `resolve_context_capacity(...)`: a safe effective capacity, every input fact kept.
///
/// A declared model maximum can clamp a known runtime, configured or legacy value, but does
/// not by itself prove what a local server allocated. A user cap can lower an observed limit
/// and stand in when none is observed; it never enlarges a smaller observed or declared cap.
pub fn resolve_context_capacity(inputs: &CapacityInputs<'_>) -> ResolvedContextCapacity {
    let declared_source = source(inputs.declared_context_source, "unknown");
    let runtime_source = source(inputs.runtime_context_source, "unknown");
    let mut candidates: Vec<(i64, &str)> = Vec::new();
    if let Some(runtime) = inputs.runtime_context_window {
        candidates.push((runtime, runtime_source));
    }
    if let Some(configured) = inputs.configured_context_window {
        candidates.push((configured, "user_configured"));
    }
    if candidates.is_empty() {
        if let Some(legacy) = inputs.legacy_context_window {
            candidates.push((legacy, "legacy_unverified"));
        }
    }
    let mut effective: Option<i64> = None;
    let mut effective_source = "unknown";
    // `min(candidates, key=...)` keeps the first of equal values.
    if let Some(&(first_value, first_source)) =
        candidates.iter().fold(None, |best: Option<&(i64, &str)>, item| match best {
            Some(best) if best.0 <= item.0 => Some(best),
            _ => Some(item),
        })
    {
        effective = Some(first_value);
        effective_source = first_source;
        if let Some(declared) = inputs.declared_context_window {
            if declared < first_value {
                effective = Some(declared);
                effective_source = declared_source;
            }
        }
    }
    let declared = inputs.declared_context_window;
    let runtime = inputs.runtime_context_window;
    let configured = inputs.configured_context_window;
    ResolvedContextCapacity {
        declared_context_window: declared,
        declared_context_source: if declared.is_some() { declared_source } else { "unknown" }.to_owned(),
        runtime_context_window: runtime,
        runtime_context_source: if runtime.is_some() { runtime_source } else { "unknown" }.to_owned(),
        configured_context_window: configured,
        effective_context_window: effective,
        effective_context_source: effective_source.to_owned(),
        declared_observed_at: declared
            .and(or(inputs.declared_observed_at, inputs.observed_at))
            .map(str::to_owned),
        runtime_observed_at: runtime
            .and(or(inputs.runtime_observed_at, inputs.observed_at))
            .map(str::to_owned),
        configured_observed_at: configured.and(inputs.configured_observed_at).map(str::to_owned),
        configured_runtime_mismatch: matches!((configured, runtime), (Some(c), Some(r)) if c > r),
        configured_declared_mismatch: matches!((configured, declared), (Some(c), Some(d)) if c > d),
    }
}

impl ResolvedContextCapacity {
    /// `projection()`: the renderer- and config-safe projection with its compatibility aliases,
    /// as ordered `(key, value)` pairs.
    pub fn projection(&self) -> Vec<(&'static str, Json)> {
        let int = |value: Option<i64>| value.map_or(Json::Null, Json::from);
        let text = |value: &Option<String>| value.as_ref().map_or(Json::Null, |v| Json::String(v.clone()));
        vec![
            ("declared_context_window", int(self.declared_context_window)),
            ("declared_context_source", Json::String(self.declared_context_source.clone())),
            ("runtime_context_window", int(self.runtime_context_window)),
            ("runtime_context_source", Json::String(self.runtime_context_source.clone())),
            ("configured_context_window", int(self.configured_context_window)),
            ("effective_context_window", int(self.effective_context_window)),
            ("effective_context_source", Json::String(self.effective_context_source.clone())),
            ("declared_context_observed_at", text(&self.declared_observed_at)),
            ("runtime_context_observed_at", text(&self.runtime_observed_at)),
            ("configured_context_observed_at", text(&self.configured_observed_at)),
            (
                "context_observed_at",
                text(&match &self.runtime_observed_at {
                    Some(value) if !value.is_empty() => Some(value.clone()),
                    _ => self.declared_observed_at.clone(),
                }),
            ),
            ("configured_runtime_mismatch", Json::Bool(self.configured_runtime_mismatch)),
            ("configured_declared_mismatch", Json::Bool(self.configured_declared_mismatch)),
            ("context_window", int(self.effective_context_window)),
            ("context_window_source", Json::String(self.effective_context_source.clone())),
        ]
    }

    /// The projection as a JSON object.
    pub fn projection_json(&self) -> JsonMap {
        self.projection().into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
    }
}
