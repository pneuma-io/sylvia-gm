//! `application/desktop_update/runtime.py`: launch hygiene for the update helper's children.
//! Nothing here persists an environment or changes an operating-system setting.
//!
//! Ported: `child_environment`. Two of Python's steps belong to its PyInstaller packaging
//! and have no counterpart in a Rust build: `PYINSTALLER_RESET_ENVIRONMENT` is still set (a
//! Python child started by a Rust helper would honour it the same way), and the `PATH`
//! clean-up only applies inside a PyInstaller extraction folder (`sys._MEIPASS`), which a
//! Rust executable never has. `system_child_libraries` comes with the helper.

use super::update_error;
use std::ffi::OsString;
use sylvia_base::CodedResult;

/// `child_environment()`: this process's environment for a child, refused when it carries
/// an engineering owner or data override. Ordinary same-user inheritance stays in memory,
/// never in an update receipt or log.
pub fn child_environment() -> CodedResult<Vec<(OsString, OsString)>> {
    let mut result: Vec<(OsString, OsString)> = std::env::vars_os().collect();
    // `os.environ` keys are upper-cased on Windows, where names ignore case.
    let named = |name: &OsString, wanted: &str| {
        let name = name.to_string_lossy();
        if cfg!(windows) {
            name.eq_ignore_ascii_case(wanted)
        } else {
            name == wanted
        }
    };
    if result.iter().any(|(name, _)| {
        named(name, "SYLVIA_GM_AUTHORITY_DATABASE") || named(name, "SYLVIA_GM_OWNER_EXECUTABLE")
    }) {
        return Err(update_error(
            "unsupported_launch_override",
            "Restart this copy without engineering owner/data overrides before applying an update. The selected files have not been changed.",
        ));
    }
    result.retain(|(name, _)| !named(name, "PYINSTALLER_RESET_ENVIRONMENT"));
    result.push(("PYINSTALLER_RESET_ENVIRONMENT".into(), "1".into()));
    Ok(result)
}
