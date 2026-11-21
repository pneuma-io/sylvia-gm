//! `config/_host_browser.py`: open a web address in the person's own browser.
//!
//! - Windows: `webbrowser.open(url, new=2)` is `os.startfile(url)`, `ShellExecuteW` with no
//!   verb ([`windows`]).
//! - Linux inside the packaged application (`APPDIR` set): `xdg-open` with the bundle's own
//!   search paths removed from its environment, waiting up to three seconds for it to report a
//!   failure (`_open_outside_bundle`).
//! - Linux otherwise, and when `xdg-open` cannot be started: Python falls back to the
//!   `webbrowser` module's browser list. The port uses `xdg-open` alone (differences.md, the
//!   unpackaged-browser row).

#[cfg(windows)]
pub mod windows;

use crate::config::ConfigFailure;

/// `_OPENER_WAIT_SECONDS`.
pub const OPENER_WAIT: std::time::Duration = std::time::Duration::from_secs(3);

/// `posixpath.normpath(path)`.
pub fn posix_normpath(path: &[u8]) -> Vec<u8> {
    if path.is_empty() {
        return b".".to_vec();
    }
    let mut initial_slashes = usize::from(path.starts_with(b"/"));
    if path.starts_with(b"//") && !path.starts_with(b"///") {
        initial_slashes = 2;
    }
    let mut components: Vec<&[u8]> = Vec::new();
    for component in path.split(|b| *b == b'/') {
        if component.is_empty() || component == b"." {
            continue;
        }
        if component != b".."
            || (initial_slashes == 0 && components.is_empty())
            || components.last() == Some(&&b".."[..])
        {
            components.push(component);
        } else if !components.is_empty() {
            components.pop();
        }
    }
    let mut out = b"/".repeat(initial_slashes);
    out.extend(components.join(&b'/'));
    if out.is_empty() {
        b".".to_vec()
    } else {
        out
    }
}

/// `_inside(entry, folders)`.
fn inside(entry: &[u8], folders: &[Vec<u8>]) -> bool {
    let normal = if entry.is_empty() { Vec::new() } else { posix_normpath(entry) };
    folders.iter().any(|folder| {
        normal == *folder || (normal.starts_with(folder) && normal.get(folder.len()) == Some(&b'/'))
    })
}

/// `host_environment(environ, packed_folder=None)`: the environment a program outside the
/// packaged application starts with. Every `:`-separated entry inside the bundle (`APPDIR`) is
/// removed, and a variable left with nothing is dropped. (The Rust owner is never a
/// PyInstaller bundle, so Python's `packed_folder` is always `None` here.)
pub fn host_environment(environ: &[(Vec<u8>, Vec<u8>)]) -> Vec<(Vec<u8>, Vec<u8>)> {
    let mut result = environ.to_vec();
    let folders: Vec<Vec<u8>> = result
        .iter()
        .find(|(name, _)| name == b"APPDIR")
        .map(|(_, value)| value.clone())
        .filter(|value| !value.is_empty())
        .map(|value| posix_normpath(&value))
        .into_iter()
        .collect();
    if folders.is_empty() {
        return result;
    }
    let mut index = 0;
    while index < result.len() {
        let entries: Vec<&[u8]> = result[index].1.split(|b| *b == b':').collect();
        let kept: Vec<&[u8]> = entries.iter().copied().filter(|entry| !inside(entry, &folders)).collect();
        if kept.len() == entries.len() {
            index += 1;
            continue;
        }
        if kept.iter().any(|entry| !entry.is_empty()) {
            result[index].1 = kept.join(&b':');
            index += 1;
        } else {
            result.remove(index);
        }
    }
    result
}

/// `_open_outside_bundle(url, environment)`: ask `xdg-open`; `None` when it cannot be started.
#[cfg(unix)]
fn open_with_xdg(url: &str, environment: &[(Vec<u8>, Vec<u8>)]) -> Result<Option<bool>, ConfigFailure> {
    use std::ffi::OsStr;
    use std::os::unix::ffi::OsStrExt;
    use std::os::unix::process::CommandExt;
    use std::process::{Command, Stdio};
    if url.contains('\0') {
        return Err(ConfigFailure::python("ValueError", "embedded null byte"));
    }
    let mut command = Command::new("xdg-open");
    command
        .arg(url)
        .env_clear()
        .envs(environment.iter().map(|(name, value)| (OsStr::from_bytes(name), OsStr::from_bytes(value))))
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        // `start_new_session=True`: the opener outlives the owner in its own group.
        .process_group(0);
    let Ok(mut child) = command.spawn() else { return Ok(None) };
    let deadline = std::time::Instant::now() + OPENER_WAIT;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Ok(Some(status.code() == Some(0))),
            Ok(None) if std::time::Instant::now() >= deadline => return Ok(Some(true)),
            Ok(None) => std::thread::sleep(std::time::Duration::from_millis(20)),
            Err(_) => return Ok(Some(true)),
        }
    }
}

/// `open_in_browser(url)`: open the address and say whether a browser was started.
pub fn open_in_browser(url: &str) -> Result<bool, ConfigFailure> {
    #[cfg(windows)]
    {
        windows::startfile(url)
    }
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStringExt;
        let environ: Vec<(Vec<u8>, Vec<u8>)> =
            std::env::vars_os().map(|(name, value)| (name.into_vec(), value.into_vec())).collect();
        let packaged = environ.iter().any(|(name, value)| name == b"APPDIR" && !value.is_empty());
        let environment = if packaged { host_environment(&environ) } else { environ };
        Ok(open_with_xdg(url, &environment)?.unwrap_or(false))
    }
    #[cfg(not(any(windows, unix)))]
    {
        let _ = url;
        Ok(false)
    }
}
