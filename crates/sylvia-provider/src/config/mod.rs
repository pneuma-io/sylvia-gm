//! Configuration: `src/sylvia_gm/config`.
//!
//! - [`env`]: `_env.py`, the `SYLVIA_GM_` overrides, over an [`env::Environment`] snapshot.
//! - [`merge`]: `_merge.py`, `deep_merge`.
//! - [`resolver`]: `_resolver.py`, `${VAR}` resolution.
//! - [`protection`]: `_credential_protection.py`, current-user DPAPI on Windows and the
//!   AES-256-GCM key file on Linux.
//! - [`user_settings`]: `_user_settings.py`, the owner-local `config.yaml`.
//! - [`loader`]: `loader.py`, `load_config` and the configuration model.
//! - [`host_browser`]: `_host_browser.py`, opening an address in the person's browser.
//! - [`presets`] and [`provider_settings`]: the read-only part of `provider_settings.py`.
//!
//! Documents are [`Data`] trees, the values `yaml.safe_load` returns.

pub mod env;
pub mod host_browser;
pub mod loader;
pub mod merge;
pub mod presets;
pub mod protection;
pub mod provider_settings;
pub mod provider_writes;
mod pyvalue;
pub mod resolver;
pub mod user_settings;

pub use loader::{
    default_config_path, load_config, provider_credentials, ProviderDef, Secret, SylviaGMConfig,
};
pub use pyvalue::{from_json as data_from_json, reply_json};
pub use sylvia_base::pyyaml::Data;

use std::sync::{Condvar, Mutex};
use std::thread::ThreadId;

/// How loading or saving configuration fails, as the Python caller sees it.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum ConfigFailure {
    /// `ConfigError(message)`.
    #[error("{0}")]
    Config(String),
    /// A Python exception the configuration code lets through (`ValueError` from `int()` on an
    /// override, `UnicodeDecodeError` from a settings file, a YAML error in the shipped
    /// defaults, ...). `class` is the Python class name; the message is `str(error)`.
    #[error("{message}")]
    Python { class: &'static str, message: String },
}

impl ConfigFailure {
    pub(crate) fn config(message: impl Into<String>) -> Self {
        Self::Config(message.into())
    }

    pub(crate) fn python(class: &'static str, message: impl Into<String>) -> Self {
        Self::Python { class, message: message.into() }
    }

    /// Whether Python raises `ConfigError`.
    pub fn is_config_error(&self) -> bool {
        matches!(self, Self::Config(_))
    }

    /// The owner's reply error, as `owner_process` builds it: a `ConfigError` or any
    /// `ValueError` (its subclasses included) is `command_rejected` with its class as
    /// `error_type`; `None` for anything else, which the owner reports as an internal error.
    pub fn rejection(&self) -> Option<sylvia_base::CodedError> {
        let (message, class) = match self {
            Self::Config(message) => (message.as_str(), "ConfigError"),
            Self::Python { class, message }
                if matches!(
                    *class,
                    "ValueError" | "UnicodeError" | "UnicodeDecodeError" | "JSONDecodeError"
                ) =>
            {
                (message.as_str(), *class)
            }
            Self::Python { .. } => return None,
        };
        Some(sylvia_base::CodedError::rejected(message).with_error_type(class))
    }
}

/// `CONFIG_LOCK`, Python's process-wide `threading.RLock` around every settings read and write.
/// It is reentrant: a settings write that loads the configuration takes it again.
pub struct ConfigLock {
    state: Mutex<(Option<ThreadId>, usize)>,
    released: Condvar,
}

/// Holding [`CONFIG_LOCK`]; released when dropped.
pub struct ConfigGuard<'a> {
    lock: &'a ConfigLock,
}

impl ConfigLock {
    const fn new() -> Self {
        Self { state: Mutex::new((None, 0)), released: Condvar::new() }
    }

    /// `with CONFIG_LOCK:`.
    pub fn acquire(&self) -> ConfigGuard<'_> {
        let me = std::thread::current().id();
        let mut state = self.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        loop {
            match state.0 {
                None => {
                    *state = (Some(me), 1);
                    break;
                }
                Some(owner) if owner == me => {
                    state.1 += 1;
                    break;
                }
                Some(_) => {
                    state = self.released.wait(state).unwrap_or_else(std::sync::PoisonError::into_inner);
                }
            }
        }
        ConfigGuard { lock: self }
    }
}

impl Drop for ConfigGuard<'_> {
    fn drop(&mut self) {
        let mut state = self.lock.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        state.1 -= 1;
        if state.1 == 0 {
            state.0 = None;
            self.lock.released.notify_one();
        }
    }
}

/// The process-wide settings lock.
pub static CONFIG_LOCK: ConfigLock = ConfigLock::new();
