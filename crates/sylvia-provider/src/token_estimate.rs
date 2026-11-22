//! `provider/_token_estimate.py`: per-model planning estimates learned from reported usage,
//! without keeping requests.
//!
//! [`TokenEstimateHook`] keeps the most conservative observed bytes-per-token ratio for each
//! route and model, and [`token_estimator`] shares one across a provider's wrappers and turns.

use crate::hooks::{HookContext, ProviderHook};
use crate::provider::{BoxFuture, Provider};
use crate::types::ModelInfo;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::Json;

/// `_size(value)`: the UTF-8 length of `json.dumps(value, ensure_ascii=False, sort_keys=True,
/// separators=(",", ":"))`.
fn size(value: &Json) -> usize {
    pyjson::dump_bytes(value, Dump::CANONICAL).len()
}

/// `TokenEstimateHook`.
#[derive(Debug, Default)]
pub struct TokenEstimateHook {
    bytes_per_token: Mutex<HashMap<(String, String), f64>>,
}

/// A reported prompt token count as the number Python divides by, or the `TypeError` its
/// `<= 0` comparison raises.
fn prompt_tokens(value: &Json) -> Result<f64, String> {
    match value {
        Json::Bool(flag) => Ok(f64::from(u8::from(*flag))),
        Json::Number(number) => Ok(number.as_f64().unwrap_or(0.0)),
        other => Err(format!(
            "'<=' not supported between instances of '{}' and 'int'",
            crate::pyobj::type_name(other)
        )),
    }
}

impl TokenEstimateHook {
    /// A hook that has observed nothing.
    pub fn new() -> Self {
        Self::default()
    }

    /// `estimate(value, model)`: reserve 25% above measured usage; without a measurement, the
    /// offline estimate of three bytes per token. Both add 32.
    pub fn estimate(&self, value: &Json, model: Option<&ModelInfo>) -> i64 {
        let key = model
            .map_or((String::new(), String::new()), |model| (model.provider_key.clone(), model.id.clone()));
        let ratio = self.bytes_per_token.lock().expect("estimate lock").get(&key).copied();
        let divisor = match ratio {
            Some(ratio) if ratio != 0.0 => ratio / 1.25,
            _ => 3.0,
        };
        // `math.ceil` of a float quotient; sizes are far below 2^53.
        ((size(value) as f64) / divisor).ceil() as i64 + 32
    }

    fn observe(&self, context: &HookContext) -> Result<(), String> {
        let Some(response) = &context.response else { return Ok(()) };
        if response.usage_reported != Some(true) {
            return Ok(());
        }
        let Some(request_json) = context.request_json.as_ref().filter(|request| !request.is_empty()) else {
            return Ok(());
        };
        let tokens = prompt_tokens(&response.usage.prompt_tokens.to_json())?;
        if tokens <= 0.0 {
            return Ok(());
        }
        let ratio = size(&Json::Object(request_json.clone())) as f64 / tokens;
        let key = (context.provider_key.clone(), context.model_id.clone());
        let mut ratios = self.bytes_per_token.lock().expect("estimate lock");
        let kept = ratios.get(&key).copied().unwrap_or(ratio);
        // `min(ratio, kept)`: the first argument wins a tie.
        ratios.insert(key, if kept < ratio { kept } else { ratio });
        Ok(())
    }
}

impl ProviderHook for TokenEstimateHook {
    fn name(&self) -> &str {
        "TokenEstimateHook"
    }

    fn as_any(&self) -> Option<&dyn std::any::Any> {
        Some(self)
    }

    fn on_response<'a>(&'a self, context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        Box::pin(async move { self.observe(context) })
    }
}

/// The estimator [`token_estimator`] found or registered.
#[derive(Clone)]
pub struct TokenEstimator(Arc<dyn ProviderHook>);

impl TokenEstimator {
    /// [`TokenEstimateHook::estimate`].
    pub fn estimate(&self, value: &Json, model: Option<&ModelInfo>) -> i64 {
        self.hook().estimate(value, model)
    }

    /// The hook itself.
    pub fn hook(&self) -> &TokenEstimateHook {
        self.0
            .as_any()
            .and_then(|any| any.downcast_ref::<TokenEstimateHook>())
            .expect("a TokenEstimator holds a TokenEstimateHook")
    }
}

/// `token_estimator(provider)`: the `TokenEstimateHook` already among the provider's hooks, or
/// a new one registered there, so wrappers and turns share one. A provider without a hook
/// manager of its own (`None` from [`Provider::hooks`]) gets an unregistered one, as Python's
/// fresh `HookManager` would hold a hook nothing fires.
pub fn token_estimator(provider: &dyn Provider) -> TokenEstimator {
    let Some(hooks) = provider.hooks() else {
        return TokenEstimator(Arc::new(TokenEstimateHook::new()));
    };
    for hook in hooks.hooks() {
        if hook.as_any().is_some_and(|any| any.is::<TokenEstimateHook>()) {
            return TokenEstimator(hook);
        }
    }
    let hook: Arc<dyn ProviderHook> = Arc::new(TokenEstimateHook::new());
    hooks.register(Arc::clone(&hook));
    TokenEstimator(hook)
}
