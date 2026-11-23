//! `config/claude_settings.py`: owner-only setup for Sylvia's own copy of Claude Code and its
//! account-owned sign-in. Lane 5.
//!
//! [`claude_settings_action`] is the owner's `claude_connection` command. Python's `main()`, the
//! module's command line for headless setup (SylviaBench runs it with the Python owner), is not
//! part of the Rust owner. The `atexit` hook that cancels each account's download and sign-in
//! is the owner's call to [`cancel_all`] at shutdown.

use super::provider_settings::ConfigSources;
use super::provider_writes::{save_provider_settings, SaveProvider};
use super::{ConfigFailure, Data};
use crate::claude_install::{ClaudeInstaller, InstallSystem};
use crate::claude_runtime::{self, DOCUMENTED_CONTEXT_WINDOW_AT};
use crate::error::{ErrorKind, ProviderError};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};
use sylvia_base::{Json, JsonMap};

/// `_ACTIONS`.
pub const ACTIONS: [&str; 6] = ["status", "install", "login", "cancel", "logout", "add_model"];
/// `_SETUP_NEEDED`.
pub const SETUP_NEEDED: &str =
    "Sylvia uses its own copy of Claude Code. Install it to continue; any Claude Code you already have is left alone.";

/// How a Claude settings action failed.
#[derive(Debug)]
pub enum ClaudeSettingsError {
    /// `ProviderConnectionError`, which the owner reports with its text.
    Connection(String),
    /// The configuration write failed.
    Config(ConfigFailure),
    /// Another exception Python lets escape.
    Internal(String),
}

impl From<ProviderError> for ClaudeSettingsError {
    fn from(error: ProviderError) -> Self {
        if error.is(ErrorKind::Connection) {
            Self::Connection(error.message)
        } else {
            Self::Internal(error.message)
        }
    }
}

fn refused(message: &str) -> ClaudeSettingsError {
    ClaudeSettingsError::Connection(message.to_owned())
}

/// `ClaudeAccount`: this owner's setup worker and sign-in child for one runtime home; never
/// Claude's credentials.
pub struct ClaudeAccount {
    pub home: PathBuf,
    pub installer: ClaudeInstaller,
    login: Mutex<Option<Child>>,
}

fn running(child: &mut Child) -> bool {
    matches!(child.try_wait(), Ok(None))
}

impl ClaudeAccount {
    pub fn new(home: &Path, system: InstallSystem) -> Self {
        Self { home: home.to_owned(), installer: ClaudeInstaller::new(home, system), login: Mutex::new(None) }
    }

    /// `cancel()`: stop an unfinished download and browser sign-in; a completed sign-in stays.
    pub fn cancel(&self) {
        self.installer.cancel();
        let mut login = self.login.lock().expect("login lock");
        if let Some(child) = login.as_mut() {
            crate::runtime_process::stop(child);
        }
        *login = None;
    }

    /// `begin_login(executable, api_account=...)`: the binary's own browser flow, its console
    /// output kept out of Sylvia.
    pub fn begin_login(&self, executable: &Path, api_account: bool) -> Result<(), ClaudeSettingsError> {
        let mut login = self.login.lock().expect("login lock");
        if login.as_mut().is_some_and(running) {
            return Ok(());
        }
        // A process kept for the account being replaced must not serve the next one.
        crate::claude_kept::release_kept_processes(Some(&crate::codex_rpc::python_resolve(&self.home)), None);
        let mut command_line = claude_runtime::native_auth_command(executable, "login");
        if api_account {
            command_line.push("--console".into());
        }
        let mut command = Command::new(&command_line[0]);
        command
            .args(&command_line[1..])
            .env_clear()
            .envs(claude_runtime::native_environment(&self.home))
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        crate::runtime_process::hide_console(&mut command);
        *login =
            Some(command.spawn().map_err(|_| refused("Claude Code's sign-in could not start. Try again."))?);
        Ok(())
    }

    /// `logout(executable)`: sign Sylvia's profile out through the binary.
    pub fn logout(&self, executable: &Path) -> Result<(), ClaudeSettingsError> {
        self.cancel();
        crate::claude_kept::release_kept_processes(Some(&crate::codex_rpc::python_resolve(&self.home)), None);
        let command_line = claude_runtime::native_auth_command(executable, "logout");
        let failed = || refused("Claude Code could not sign out. Try again.");
        let mut command = Command::new(&command_line[0]);
        command
            .args(&command_line[1..])
            .env_clear()
            .envs(claude_runtime::native_environment(&self.home))
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        crate::runtime_process::hide_console(&mut command);
        let mut child = command.spawn().map_err(|_| failed())?;
        // `subprocess.run(..., timeout=30)`: the result is not read; a timeout kills the child.
        let started = Instant::now();
        loop {
            match child.try_wait() {
                Ok(Some(_)) => return Ok(()),
                Ok(None) if started.elapsed() < Duration::from_secs(30) => {
                    std::thread::sleep(Duration::from_millis(20))
                }
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(failed());
                }
            }
        }
    }

    /// `pending`: a sign-in is still running.
    pub fn pending(&self) -> bool {
        self.login.lock().expect("login lock").as_mut().is_some_and(running)
    }
}

fn accounts() -> &'static Mutex<BTreeMap<PathBuf, Arc<ClaudeAccount>>> {
    static ACCOUNTS: OnceLock<Mutex<BTreeMap<PathBuf, Arc<ClaudeAccount>>>> = OnceLock::new();
    ACCOUNTS.get_or_init(|| Mutex::new(BTreeMap::new()))
}

/// The `atexit` hooks: cancel every account's download and sign-in.
pub fn cancel_all() {
    let all: Vec<Arc<ClaudeAccount>> = accounts().lock().expect("account lock").values().cloned().collect();
    for account in all {
        account.cancel();
    }
}

/// `re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._\[\]-]{0,127}", model_id)`.
fn official_model_id(text: &str) -> bool {
    let bytes = text.as_bytes();
    text.is_ascii()
        && (1..=128).contains(&bytes.len())
        && bytes[0].is_ascii_alphanumeric()
        && bytes[1..]
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'[' | b']' | b'-'))
}

fn json_data(value: &Json) -> Data {
    match value {
        Json::Null => Data::None,
        Json::Bool(flag) => Data::Bool(*flag),
        Json::Number(number) => {
            number.as_i64().map_or_else(|| Data::Float(number.as_f64().unwrap_or(0.0)), Data::Int)
        }
        Json::String(text) => Data::Str(text.clone()),
        other => Data::Str(other.to_string()),
    }
}

/// `claude_settings_action(storage_root, action, options)`: an allowlist of safe setup actions
/// and explicit model registration. `system` makes the installer for a runtime home the first
/// time it is used; `now` stamps a saved model.
pub fn claude_settings_action(
    sources: ConfigSources<'_>,
    storage_root: &Path,
    action: &Json,
    options: &Json,
    system: &dyn Fn() -> InstallSystem,
    now: &dyn Fn() -> String,
) -> Result<JsonMap, ClaudeSettingsError> {
    let Some(action) = action.as_str().filter(|action| ACTIONS.contains(action)) else {
        return Err(refused("This Claude connection action is unavailable."));
    };
    let empty = Json::Object(JsonMap::new());
    let options = if options.is_null() { &empty } else { options };
    let Some(options) = options
        .as_object()
        .filter(|options| options.keys().all(|key| key == "model" || key == "account_type"))
    else {
        return Err(refused("Claude connection options are invalid."));
    };
    let home = crate::codex_rpc::python_resolve(storage_root).join("claude-runtime");
    let account = {
        let mut accounts = accounts().lock().expect("account lock");
        Arc::clone(
            accounts.entry(home.clone()).or_insert_with(|| Arc::new(ClaudeAccount::new(&home, system()))),
        )
    };
    if action == "cancel" {
        account.cancel();
    }
    if action == "install" {
        account.installer.start().map_err(ClaudeSettingsError::Internal)?;
    }
    let executable = claude_runtime::find_runtime(&home);
    let mut status = JsonMap::new();
    status.insert("installed".into(), Json::Bool(executable.is_some()));
    status.insert("supported".into(), Json::Bool(false));
    status.insert("version".into(), Json::Null);
    status.insert("signed_in".into(), Json::Bool(false));
    status.insert("login_pending".into(), Json::Bool(account.pending()));
    status.insert("auth_method".into(), Json::Null);
    status.insert("billing".into(), Json::String("unknown".into()));
    status.insert(
        "executable".into(),
        executable.as_ref().map_or(Json::Null, |path| Json::String(path.to_string_lossy().into_owned())),
    );
    status.insert("install".into(), Json::Object(account.installer.progress()));
    status.insert("message".into(), Json::String(SETUP_NEEDED.into()));
    let needs_runtime = matches!(action, "login" | "logout" | "add_model");
    let Some(executable) = executable else {
        if needs_runtime {
            return Err(refused(SETUP_NEEDED));
        }
        return Ok(status);
    };
    match claude_runtime::runtime_version(&executable, &home) {
        Ok(version) => {
            status.insert("version".into(), Json::String(version));
        }
        Err(error) => {
            status.insert("message".into(), Json::String(error.message.clone()));
            if needs_runtime {
                return Err(error.into());
            }
            return Ok(status);
        }
    }
    status.insert("supported".into(), Json::Bool(true));
    if action == "logout" {
        account.logout(&executable)?;
    }
    if action == "login" {
        let subscription = Json::String("subscription".into());
        let account_type = options.get("account_type").unwrap_or(&subscription);
        let api = match account_type.as_str() {
            Some("subscription") => false,
            Some("api") => true,
            _ => return Err(refused("Choose a Claude subscription or Console API account.")),
        };
        account.begin_login(&executable, api)?;
    }
    for (key, value) in claude_runtime::authentication_status(&executable, &home)? {
        status.insert(key, value);
    }
    let pending = account.pending();
    status.insert("login_pending".into(), Json::Bool(pending));
    let signed_in = status.get("signed_in") == Some(&Json::Bool(true));
    let message = if pending {
        "Finish sign-in in Claude's browser window."
    } else if signed_in {
        "Claude is signed in. Usage is billed directly under the selected account's terms."
    } else {
        "Sign in through Sylvia's copy of Claude Code to continue."
    };
    status.insert("message".into(), Json::String(message.into()));
    if action == "add_model" {
        if !signed_in {
            return Err(refused("Sign in through Claude before adding a runtime model."));
        }
        let invalid = || refused("Enter an official Claude model alias or model ID.");
        let Some(Json::Object(model)) = options.get("model") else { return Err(invalid()) };
        let keys: std::collections::BTreeSet<&str> = model.keys().map(String::as_str).collect();
        if keys != ["context_window", "id", "max_output_tokens"].into() {
            return Err(invalid());
        }
        let Some(model_id) = model["id"].as_str().filter(|id| official_model_id(id)) else {
            return Err(invalid());
        };
        if !model["max_output_tokens"].is_null() {
            return Err(refused("The Claude runtime manages the response allowance."));
        }
        let capacity = &model["context_window"];
        let valid_capacity = match capacity {
            Json::Null => true,
            Json::Number(number) if !number.is_f64() => {
                number.as_i64().is_some_and(|value| (512..=10_000_000).contains(&value))
            }
            _ => false,
        };
        if !valid_capacity {
            return Err(refused("Enter a positive context limit of at least 512 tokens, or leave it blank."));
        }
        let documented = claude_runtime::documented_context_window(&model["id"]);
        let text = |value: &str| Data::Str(value.to_owned());
        let request = SaveProvider {
            provider_id: text("claude_runtime"),
            preset_id: text("claude_runtime"),
            base_url: text("https://claude.ai"),
            credential: Data::None,
            model_id: text(model_id),
            model_name: Data::Str(format!("{model_id} (Claude)")),
            context_window: Data::None,
            configured_context_window: json_data(capacity),
            // The documented window, shown as a catalog figure; none for any other name.
            runtime_context_window: documented.map_or(Data::None, Data::Int),
            runtime_context_source: documented.map_or(Data::None, |_| text("model_catalog")),
            runtime_context_observed_at: documented
                .map_or(Data::None, |_| text(DOCUMENTED_CONTEXT_WINDOW_AT)),
            max_output_tokens: Data::None,
            transport: text("claude_code"),
            supports_streaming: Data::Bool(true),
            supports_tools: Data::Bool(true),
            ..SaveProvider::default()
        };
        let saved = save_provider_settings(sources, &storage_root.join("config.yaml"), &request, now)
            .map_err(ClaudeSettingsError::Config)?;
        let canonical =
            saved.get_str("canonical_model_id").and_then(Data::as_str).unwrap_or_default().to_owned();
        status.insert("saved_model".into(), Json::String(canonical));
    }
    Ok(status)
}
