//! `application/desktop_update/identity.py`: the packaged owner's public identity, reported
//! before any storage, provider or migration is opened (`--desktop-update-identity`).

use super::checkpoint::STORAGE_COMPATIBILITY;
use super::manifest::APP_VERSION;
use sylvia_base::{Json, JsonMap};

/// `platform.machine()` mapped as Python maps it.
fn architecture() -> &'static str {
    match std::env::consts::ARCH {
        "x86_64" => "x86_64",
        "aarch64" => "aarch64",
        _ => "unsupported",
    }
}

/// `"windows"` on Windows, otherwise `sys.platform` (`linux`, `darwin`, ...).
fn system() -> &'static str {
    match std::env::consts::OS {
        "macos" => "darwin",
        other => other,
    }
}

/// `runtime_identity()`.
pub fn runtime_identity() -> Json {
    let mut identity = JsonMap::new();
    identity.insert("format_version".into(), 1.into());
    identity.insert("product".into(), "Sylvia GM".into());
    identity.insert("component".into(), "owner".into());
    identity.insert("version".into(), APP_VERSION.into());
    identity.insert("storage_compatibility".into(), STORAGE_COMPATIBILITY.into());
    identity.insert("target".into(), format!("{}-{}", system(), architecture()).into());
    Json::Object(identity)
}

/// The target part of [`runtime_identity`].
pub fn runtime_target() -> String {
    format!("{}-{}", system(), architecture())
}

/// `main()`: `print(json.dumps(runtime_identity(), separators=(",", ":")))`. Python's
/// standard output writes `\r\n` for the newline on Windows.
pub fn identity_line() -> String {
    let mut line = sylvia_base::pyjson::dumps(&runtime_identity(), sylvia_base::pyjson::Dump::WIRE);
    line.push_str(if cfg!(windows) { "\r\n" } else { "\n" });
    line
}
