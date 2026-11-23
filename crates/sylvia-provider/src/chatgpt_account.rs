//! `provider/_chatgpt_account.py` and `provider/_chatgpt_model_facts.py`: managed ChatGPT
//! sign-in with safe owner-to-renderer account projections, and the optional setup facts from
//! Sylvia's own Codex catalog. Lane 5.

use crate::codex_rpc::{account_changed, CodexConnection, CodexError};
use crate::model_facts::documented_model_defaults;
use crate::pydatetime::{fromisoformat, utc_now_micros};
use crate::pyobj::{py_eq, py_str};
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;
use std::path::Path;
use std::sync::Mutex;
use std::time::Duration;
use sylvia_base::{Json, JsonMap};

/// `call(...)`'s default timeout.
const CALL_TIMEOUT: Duration = Duration::from_secs(30);
/// The model catalog is read up to this size.
const CATALOG_LIMIT: u64 = 8 * 1024 * 1024;

fn refused(message: &str) -> CodexError {
    CodexError::Connection(message.to_owned())
}

fn text(value: &str) -> Json {
    Json::String(value.to_owned())
}

/// A whole number Python holds as `int` (not `bool`).
fn int(value: Option<&Json>) -> Option<i64> {
    match value? {
        Json::Number(number) if !number.is_f64() => number.as_i64(),
        _ => None,
    }
}

/// `subscription_model_facts(home, model_ids)`: the documented output ceiling, and the context
/// allowance from the runtime's recent local catalog. `now` is `datetime.now(UTC)` in
/// microseconds since 1970.
pub fn subscription_model_facts(
    home: &Path,
    model_ids: &BTreeSet<String>,
    now: i128,
) -> BTreeMap<String, JsonMap> {
    let mut facts: BTreeMap<String, JsonMap> = BTreeMap::new();
    for model_id in model_ids {
        let Some(documented) = documented_model_defaults("openai", model_id) else { continue };
        if let Some(tokens) = documented.max_output_tokens.filter(|tokens| *tokens != 0) {
            let mut entry = JsonMap::new();
            entry.insert("output_reference_tokens".into(), Json::from(tokens));
            entry.insert(
                "output_reference_source".into(),
                documented.documented_source.map_or(Json::Null, text),
            );
            facts.insert(model_id.clone(), entry);
        }
    }
    catalog_facts(home, model_ids, now, &mut facts);
    facts
}

/// The catalog part, which fails closed: any unreadable file or unfamiliar shape adds nothing
/// more (Python catches `OSError`, `ValueError`, `TypeError`, `KeyError` and `RecursionError`).
fn catalog_facts(
    home: &Path,
    model_ids: &BTreeSet<String>,
    now: i128,
    facts: &mut BTreeMap<String, JsonMap>,
) {
    let path = home.join("models_cache.json");
    if std::fs::symlink_metadata(&path).is_ok_and(|meta| meta.file_type().is_symlink()) {
        return;
    }
    let Ok(file) = std::fs::File::open(&path) else { return };
    let mut raw = Vec::new();
    if file.take(CATALOG_LIMIT + 1).read_to_end(&mut raw).is_err() || raw.len() as u64 > CATALOG_LIMIT {
        return;
    }
    let Ok(Json::Object(catalog)) = sylvia_base::pyjson::loads_bytes(&raw) else { return };
    if !catalog.get("client_version").is_some_and(Json::is_string) {
        return;
    }
    let Some(Json::String(fetched)) = catalog.get("fetched_at") else { return };
    let Some(fetched) = fromisoformat(fetched) else { return };
    let Some(fetched_at) = fetched.utc_micros() else { return };
    let age = now - fetched_at;
    if !(-300_000_000..=86_400_000_000).contains(&age) {
        return;
    }
    let Some(Json::Array(rows)) = catalog.get("models") else { return };
    for row in rows {
        let Json::Object(row) = row else { continue };
        let slug = match row.get("slug") {
            // `slug not in model_ids`: a list or dict is unhashable (`TypeError`).
            Some(Json::Array(_) | Json::Object(_)) => return,
            Some(Json::String(slug)) if model_ids.contains(slug) => slug,
            _ => continue,
        };
        let (Some(window), Some(percent)) =
            (int(row.get("context_window")), int(row.get("effective_context_window_percent")))
        else {
            continue;
        };
        if !(1..=10_000_000).contains(&window) || !(1..=100).contains(&percent) {
            continue;
        }
        let allowance = window * percent / 100;
        if allowance != 0 {
            let entry = facts.entry(slug.clone()).or_default();
            entry.insert("runtime_context_window".into(), Json::from(allowance));
            entry.insert("runtime_context_source".into(), text("model_catalog"));
            entry.insert("runtime_context_observed_at".into(), Json::String(fetched.isoformat()));
        }
    }
}

#[derive(Default)]
struct Login {
    id: Option<String>,
    failed: bool,
}

/// `ChatGPTAccount`: the login lifecycle, separate from model selection and campaign
/// persistence.
pub struct ChatGPTAccount {
    pub connection: CodexConnection,
    login: Mutex<Login>,
}

/// `urlsplit(url)` and its checks: an `https` link on OpenAI's sign-in hosts, with no user
/// information and the default port.
fn sign_in_link(url: &Json) -> bool {
    let Json::String(url) = url else { return false };
    let Ok(parsed) = sylvia_base::pyurl::urlsplit(url) else { return false };
    let host = parsed.hostname();
    let Ok(port) = parsed.port() else { return false };
    parsed.scheme == "https"
        && matches!(host.as_deref(), Some("auth.openai.com" | "auth.chatgpt.com"))
        && parsed.username().is_none_or(str::is_empty)
        && parsed.password().is_none_or(str::is_empty)
        && matches!(port, None | Some(443))
}

impl ChatGPTAccount {
    pub fn new(connection: CodexConnection) -> Self {
        Self { connection, login: Mutex::new(Login::default()) }
    }

    fn call(&self, method: &str, params: Json) -> Result<JsonMap, CodexError> {
        self.connection.call(method, params, CALL_TIMEOUT)
    }

    /// `status()`: account status without tokens, raw errors or auth file content.
    pub fn status(&self) -> Result<JsonMap, CodexError> {
        let mut login = self.login.lock().expect("login lock");
        self.status_locked(&mut login)
    }

    fn status_locked(&self, login: &mut Login) -> Result<JsonMap, CodexError> {
        self.connection.start()?;
        for event in self.connection.notifications()? {
            let empty = Json::Object(JsonMap::new());
            let params = event.get("params").unwrap_or(&empty);
            let Json::Object(params) = params else { continue };
            if event.get("method").and_then(Json::as_str) != Some("account/login/completed") {
                continue;
            }
            let success = params.get("success") == Some(&Json::Bool(true));
            if success {
                // The home may now answer for another account than when the consent began;
                // inference children kept open meanwhile belong to the old one.
                account_changed(self.connection.home());
            }
            if let Some(id) = &login.id {
                if params.get("loginId").is_some_and(|value| value.as_str() == Some(id)) {
                    login.id = None;
                    login.failed = !success;
                }
            }
        }
        let reply = self.call("account/read", serde_json::json!({"refreshToken": false}))?;
        let account = reply.get("account");
        let details = match account {
            Some(Json::Object(details)) if details.get("type").and_then(Json::as_str) == Some("chatgpt") => {
                Some(details)
            }
            _ => None,
        };
        if details.is_some() {
            login.id = None;
            login.failed = false;
        }
        let field = |key: &str| -> Json {
            details
                .and_then(|details| details.get(key))
                .filter(|value| value.is_string())
                .cloned()
                .unwrap_or(Json::Null)
        };
        let mut status = JsonMap::new();
        status.insert("signed_in".into(), Json::Bool(details.is_some()));
        status.insert("login_pending".into(), Json::Bool(login.id.is_some()));
        status.insert("login_failed".into(), Json::Bool(login.failed));
        status.insert("plan_type".into(), field("planType"));
        status.insert("email".into(), field("email"));
        Ok(status)
    }

    /// `begin_login()`: browser consent through the runtime's managed ChatGPT flow only.
    pub fn begin_login(&self) -> Result<JsonMap, CodexError> {
        let mut login = self.login.lock().expect("login lock");
        self.connection.start()?;
        if login.id.is_some() {
            self.cancel_locked(&mut login)?;
        }
        let result = self.call("account/login/start", serde_json::json!({"type": "chatgpt"}))?;
        if let Some(Json::String(id)) =
            result.get("loginId").filter(|id| id.as_str().is_some_and(|id| !id.is_empty()))
        {
            login.id = Some(id.clone());
        }
        let url = result.get("authUrl").cloned().unwrap_or(Json::Null);
        let valid = sign_in_link(&url);
        if result.get("type").and_then(Json::as_str) != Some("chatgpt") || login.id.is_none() || !valid {
            if login.id.is_some() {
                self.cancel_locked(&mut login)?;
            }
            return Err(refused("The Codex runtime returned an unsupported sign-in link."));
        }
        login.failed = false;
        let mut reply = JsonMap::new();
        reply.insert("auth_url".into(), url);
        reply.insert("login_pending".into(), Json::Bool(true));
        Ok(reply)
    }

    /// `cancel_login()`: only the attempt this controller created.
    pub fn cancel_login(&self) -> Result<JsonMap, CodexError> {
        let mut login = self.login.lock().expect("login lock");
        self.cancel_locked(&mut login)
    }

    fn cancel_locked(&self, login: &mut Login) -> Result<JsonMap, CodexError> {
        if let Some(id) = login.id.clone() {
            self.call("account/login/cancel", serde_json::json!({"loginId": id}))?;
            login.id = None;
        }
        login.failed = false;
        let mut reply = JsonMap::new();
        reply.insert("login_pending".into(), Json::Bool(false));
        Ok(reply)
    }

    /// `logout()`: Sylvia's managed login only; never the normal Codex profile.
    pub fn logout(&self) -> Result<JsonMap, CodexError> {
        let mut login = self.login.lock().expect("login lock");
        self.connection.start()?;
        self.cancel_locked(&mut login)?;
        self.call("account/logout", Json::Object(JsonMap::new()))?;
        self.status_locked(&mut login)
    }

    /// `models()`: the advertised models after sign-in; discovery is not inference
    /// verification.
    pub fn models(&self) -> Result<Vec<JsonMap>, CodexError> {
        self.models_at(utc_now_micros())
    }

    /// [`Self::models`] with the catalog's age read against `now` (microseconds since 1970).
    pub fn models_at(&self, now: i128) -> Result<Vec<JsonMap>, CodexError> {
        let mut login = self.login.lock().expect("login lock");
        if self.status_locked(&mut login)?.get("signed_in") != Some(&Json::Bool(true)) {
            return Err(refused("Sign in to ChatGPT before finding subscription models."));
        }
        let mut models: Vec<JsonMap> = Vec::new();
        let mut cursor = Json::Null;
        let mut seen: BTreeSet<String> = BTreeSet::new();
        for _ in 0..20 {
            let reply = self.call("model/list", serde_json::json!({"cursor": cursor, "limit": 100}))?;
            let Some(Json::Array(data)) = reply.get("data") else {
                return Err(refused("The Codex runtime returned an unsupported model list."));
            };
            for item in data {
                let Json::Object(item) = item else { continue };
                let Some(Json::String(model_id)) = item.get("model") else { continue };
                models.push(advertised(item, model_id));
            }
            cursor = reply.get("nextCursor").cloned().unwrap_or(Json::Null);
            if cursor.is_null() {
                let ids = models.iter().map(|model| py_str(&model["id"])).collect();
                let facts = subscription_model_facts(self.connection.home(), &ids, now);
                for model in &mut models {
                    if let Some(found) = facts.get(&py_str(&model["id"])) {
                        for (key, value) in found {
                            model.insert(key.clone(), value.clone());
                        }
                    }
                }
                return Ok(models);
            }
            let Json::String(next) = &cursor else { break };
            if !seen.insert(next.clone()) {
                break;
            }
        }
        Err(refused("The Codex runtime did not finish listing models."))
    }
}

/// One `model/list` item as Sylvia's setup projection.
fn advertised(item: &JsonMap, model_id: &str) -> JsonMap {
    let mut supported: Vec<Json> = Vec::new();
    if let Some(Json::Array(efforts)) = item.get("supportedReasoningEfforts") {
        for effort in efforts {
            if let Some(Json::String(name)) =
                effort.as_object().and_then(|effort| effort.get("reasoningEffort"))
            {
                let name = Json::String(name.clone());
                if !name.as_str().unwrap_or("").is_empty() && !supported.contains(&name) {
                    supported.push(name);
                }
            }
        }
    }
    let suggested = item.get("defaultReasoningEffort").cloned().unwrap_or(Json::Null);
    let mut service_tiers = Vec::new();
    if let Some(Json::Array(tiers)) = item.get("serviceTiers") {
        for tier in tiers {
            let Json::Object(tier) = tier else { continue };
            let (Some(Json::String(id)), Some(Json::String(name))) = (tier.get("id"), tier.get("name"))
            else {
                continue;
            };
            if id.is_empty() || name.is_empty() {
                continue;
            }
            let description = tier
                .get("description")
                .filter(|value| value.is_string())
                .cloned()
                .unwrap_or_else(|| text(""));
            service_tiers.push(serde_json::json!({"id": id, "name": name, "description": description}));
        }
    }
    let supports_text = match item.get("inputModalities") {
        None => true,
        Some(Json::Array(modalities)) => modalities.iter().any(|modality| py_eq(modality, &text("text"))),
        Some(_) => false,
    };
    let mut model = JsonMap::new();
    model.insert("id".into(), text(model_id));
    model.insert("name".into(), item.get("displayName").cloned().unwrap_or_else(|| text(model_id)));
    model.insert("verified".into(), Json::Bool(false));
    model.insert(
        "unavailable_reason".into(),
        if supports_text { Json::Null } else { text("This model does not advertise text input.") },
    );
    let default =
        if supported.iter().any(|effort| py_eq(effort, &suggested)) { suggested } else { Json::Null };
    model.insert("supported_reasoning_efforts".into(), Json::Array(supported));
    model.insert("default_reasoning_effort".into(), default);
    model.insert("service_tiers".into(), Json::Array(service_tiers));
    model
}
