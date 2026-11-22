//! `config/chatgpt_settings.py`: owner commands for the managed ChatGPT sign-in and
//! subscription model selection. Lane 5.
//!
//! [`chatgpt_settings_action`] is the owner's `chatgpt_connection` command (wired in its own
//! PR). The `atexit` hook that closes each account's connection is the owner's call to
//! [`close_all`] at shutdown. Python finds the runtime on `PATH` when each connection starts;
//! `executable` here names it instead (tests), `None` keeping Python's lookup.

use super::provider_settings::{provider_settings, ConfigSources};
use super::provider_writes::{refresh_subscription_reasoning, save_provider_settings, SaveProvider};
use super::pyvalue::{from_json, reply_json};
use super::{ConfigFailure, Data};
use crate::chatgpt_account::ChatGPTAccount;
use crate::codex_rpc::{python_resolve, CodexConnection, CodexError};
use crate::pyobj::{py_eq, py_str, truthy};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use sylvia_base::{Json, JsonMap};

/// `chatgpt_settings_action`'s allowlist.
pub const ACTIONS: [&str; 6] = ["status", "login", "cancel", "logout", "models", "add_model"];

/// How a ChatGPT settings action failed.
#[derive(Debug)]
pub enum ChatGPTSettingsError {
    /// `CodexConnectionError` (or an `OSError` the connection lets through).
    Codex(CodexError),
    /// The configuration read or write failed.
    Config(ConfigFailure),
}

impl From<CodexError> for ChatGPTSettingsError {
    fn from(error: CodexError) -> Self {
        Self::Codex(error)
    }
}

impl From<ConfigFailure> for ChatGPTSettingsError {
    fn from(error: ConfigFailure) -> Self {
        Self::Config(error)
    }
}

fn refused(message: &str) -> ChatGPTSettingsError {
    ChatGPTSettingsError::Codex(CodexError::Connection(message.to_owned()))
}

fn accounts() -> &'static Mutex<BTreeMap<PathBuf, Arc<ChatGPTAccount>>> {
    static ACCOUNTS: OnceLock<Mutex<BTreeMap<PathBuf, Arc<ChatGPTAccount>>>> = OnceLock::new();
    ACCOUNTS.get_or_init(|| Mutex::new(BTreeMap::new()))
}

/// `_account(storage_root)`: one account controller per owner profile, never the user's
/// ordinary Codex home.
pub fn account(storage_root: &Path, executable: Option<&Path>) -> Arc<ChatGPTAccount> {
    let home = python_resolve(storage_root).join("chatgpt-runtime");
    let mut accounts = accounts().lock().expect("account lock");
    Arc::clone(accounts.entry(home.clone()).or_insert_with(|| {
        Arc::new(ChatGPTAccount::new(CodexConnection::new(&home, executable, BTreeSet::new())))
    }))
}

/// The `atexit` hooks: close every account's connection (its sign-in stays saved).
pub fn close_all() {
    let all: Vec<Arc<ChatGPTAccount>> = accounts().lock().expect("account lock").values().cloned().collect();
    for account in all {
        account.connection.close();
    }
}

fn model_data(models: &[JsonMap]) -> Result<Vec<Data>, ConfigFailure> {
    models.iter().map(|model| from_json(&Json::Object(model.clone()))).collect()
}

/// `refresh_saved_subscription_reasoning(storage_root)`: refresh legacy saved models through
/// discovery, without sign-in prompts or inference. `false` when the runtime or account is
/// unavailable.
pub fn refresh_saved_subscription_reasoning(
    sources: ConfigSources<'_>,
    storage_root: &Path,
    executable: Option<&Path>,
) -> Result<bool, ChatGPTSettingsError> {
    let models = match account(storage_root, executable).models() {
        Ok(models) => models,
        Err(error) if error.is_connection_error() => return Ok(false),
        Err(error) => return Err(error.into()),
    };
    refresh_subscription_reasoning(sources, &storage_root.join("config.yaml"), &model_data(&models)?)?;
    Ok(true)
}

fn data(value: &Json) -> Result<Data, ConfigFailure> {
    from_json(value)
}

/// `model.get(key)` on a saved model's projection.
fn field<'a>(model: &'a JsonMap, key: &str) -> &'a Json {
    model.get(key).unwrap_or(&Json::Null)
}

/// A count Python holds as `int` (not `bool`).
fn int(value: &Json) -> Option<i64> {
    match value {
        Json::Number(number) if !number.is_f64() => number.as_i64().or(Some(i64::MAX)),
        _ => None,
    }
}

/// `chatgpt_settings_action(storage_root, action, model)`: a small allowlist of account
/// actions; credentials never cross this boundary. `open` is `open_in_browser`; `now` stamps a
/// saved model.
#[allow(clippy::too_many_arguments)]
pub fn chatgpt_settings_action(
    sources: ConfigSources<'_>,
    storage_root: &Path,
    action: &Json,
    model: &Json,
    executable: Option<&Path>,
    open: &dyn Fn(&str) -> Result<bool, ConfigFailure>,
    now: &dyn Fn() -> String,
) -> Result<JsonMap, ChatGPTSettingsError> {
    let Some(action) = action.as_str().filter(|action| ACTIONS.contains(action)) else {
        return Err(refused("This ChatGPT connection action is unavailable."));
    };
    let account = account(storage_root, executable);
    match action {
        "login" => {
            let login = account.begin_login()?;
            if !open(&py_str(field(&login, "auth_url")))? {
                account.cancel_login()?;
                return Err(refused("The sign-in browser could not open. Check your default browser."));
            }
            let mut reply = JsonMap::new();
            reply.insert("login_pending".into(), Json::Bool(true));
            reply.insert("signed_in".into(), Json::Bool(false));
            return Ok(reply);
        }
        "cancel" => {
            account.cancel_login()?;
            return Ok(account.status()?);
        }
        "logout" => return Ok(account.logout()?),
        _ => {}
    }
    let mut status = account.status()?;
    if action == "status" {
        return Ok(status);
    }
    let config_path = storage_root.join("config.yaml");
    // The catalog's age is read against the same clock that stamps a saved model.
    let at = crate::pydatetime::fromisoformat(&now()).and_then(|now| now.utc_micros());
    let models = account.models_at(at.unwrap_or_else(crate::pydatetime::utc_now_micros))?;
    // True when saved models gained newly advertised effort or speed options, so the owner
    // can reload its model catalog without the user re-adding anything.
    let refreshed = refresh_subscription_reasoning(sources, &config_path, &model_data(&models)?)?;
    status.insert("saved_models_refreshed".into(), Json::Bool(refreshed));
    status.insert("models".into(), Json::Array(models.iter().cloned().map(Json::Object).collect()));
    if action != "add_model" {
        return Ok(status);
    }
    let keys: Option<BTreeSet<&str>> =
        model.as_object().map(|model| model.keys().map(String::as_str).collect());
    let shaped = keys == Some(["context_window", "id", "max_output_tokens"].into());
    let (Json::Object(model), true) = (model, shaped) else {
        return Err(refused("Choose a model and supply its capacity settings."));
    };
    let Some(selected) = models.iter().find(|item| py_eq(field(item, "id"), &model["id"])) else {
        return Err(refused("This model is not advertised for this ChatGPT connection."));
    };
    let unavailable = field(selected, "unavailable_reason");
    if truthy(unavailable) {
        return Err(refused(&py_str(unavailable)));
    }
    let configured = reply_json(&provider_settings(sources, &config_path)?)?;
    let mut prior = JsonMap::new();
    if let Some(Json::Array(providers)) = configured.get("configured") {
        'found: for provider in providers {
            if provider.get("provider_id").and_then(Json::as_str) != Some("chatgpt_subscription") {
                continue;
            }
            for saved in provider.get("models").and_then(Json::as_array).into_iter().flatten() {
                if saved.get("id").is_some_and(|id| py_eq(id, field(selected, "id"))) {
                    prior = saved.as_object().cloned().unwrap_or_default();
                    break 'found;
                }
            }
        }
    }
    let mut runtime = field(selected, "runtime_context_window").clone();
    let mut runtime_source = field(selected, "runtime_context_source").clone();
    let mut runtime_observed = field(selected, "runtime_context_observed_at").clone();
    if let Some(prior_runtime) = int(field(&prior, "runtime_context_window")) {
        let reported = field(&prior, "runtime_context_source").as_str() == Some("runtime_reported");
        // `runtime is None or (reported and prior_runtime < runtime)`.
        let smaller = int(&runtime).is_some_and(|runtime| prior_runtime < runtime);
        if runtime.is_null() || (reported && smaller) {
            runtime = Json::from(prior_runtime);
            runtime_source = field(&prior, "runtime_context_source").clone();
            runtime_observed = field(&prior, "runtime_context_observed_at").clone();
        }
    }
    let reference = field(selected, "output_reference_tokens");
    let output_reference = if truthy(reference) {
        reference.clone()
    } else {
        field(&prior, "declared_max_output_tokens").clone()
    };
    let window = &model["context_window"];
    let ceiling = &model["max_output_tokens"];
    let capacity = if window.is_null() { &runtime } else { window };
    let output = if ceiling.is_null() { &output_reference } else { ceiling };
    let bad_window = !window.is_null() && !int(window).is_some_and(|value| value >= 512);
    let bad_count =
        |value: &Json| !value.is_null() && !int(value).is_some_and(|value| value > 0 && value <= 10_000_000);
    if bad_window || bad_count(capacity) || bad_count(output) {
        return Err(refused("Enter positive token capacities, with room for input as well as the reply."));
    }
    let name = field(selected, "name");
    let shown = if truthy(name) { py_str(name) } else { py_str(field(selected, "id")) };
    let text = |value: &str| Data::Str(value.to_owned());
    let request = SaveProvider {
        provider_id: text("chatgpt_subscription"),
        preset_id: text("chatgpt_subscription"),
        base_url: text("https://chatgpt.com"),
        credential: Data::None,
        model_id: data(field(selected, "id"))?,
        model_name: Data::Str(format!("{shown} (ChatGPT)")),
        context_window: Data::None,
        declared_context_window: data(field(&prior, "declared_context_window"))?,
        declared_context_source: data(field(&prior, "declared_context_source"))?,
        runtime_context_window: data(&runtime)?,
        runtime_context_source: data(&runtime_source)?,
        runtime_context_observed_at: data(&runtime_observed)?,
        configured_context_window: data(window)?,
        max_output_tokens: data(ceiling)?,
        declared_max_output_tokens: data(&output_reference)?,
        transport: text("codex_app_server"),
        supports_streaming: Data::Bool(true),
        supports_tools: Data::Bool(true),
        supported_reasoning_efforts: data(field(selected, "supported_reasoning_efforts"))?,
        default_reasoning_effort: data(field(selected, "default_reasoning_effort"))?,
        service_tiers: data(field(selected, "service_tiers"))?,
        ..SaveProvider::default()
    };
    let saved = save_provider_settings(sources, &config_path, &request, now)?;
    status.insert(
        "saved_model".into(),
        reply_json(saved.get_str("canonical_model_id").unwrap_or(&Data::None))?,
    );
    Ok(status)
}
