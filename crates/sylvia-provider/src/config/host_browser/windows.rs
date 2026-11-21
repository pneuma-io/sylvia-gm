//! `os.startfile(url)` as `webbrowser.WindowsDefault.open` calls it: `ShellExecuteW` with no
//! verb, no parameters and no folder, shown normally. A result above 32 is success; anything
//! else is the `OSError` Python turns into `False`.
//!
//! The only unsafe code is the inner `ffi` module (approved under rule 9 of
//! `docs/rust-port/README.md`).

use crate::config::ConfigFailure;

#[allow(unsafe_code)]
mod ffi {
    use windows_sys::Win32::UI::Shell::ShellExecuteW;
    use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

    /// Whether the shell accepted the address. `file` is NUL-terminated UTF-16 with no
    /// interior NUL.
    pub(super) fn shell_open(file: &[u16]) -> bool {
        debug_assert_eq!(file.last(), Some(&0));
        // SAFETY: `file` is a NUL-terminated wide string that outlives the call; the window,
        // verb, parameters and folder are null, which ShellExecuteW accepts (no owner window,
        // the default verb, no arguments, the current folder). The returned value is a status
        // code, not a handle to free.
        let result = unsafe {
            ShellExecuteW(
                std::ptr::null_mut(),
                std::ptr::null(),
                file.as_ptr(),
                std::ptr::null(),
                std::ptr::null(),
                SW_SHOWNORMAL,
            )
        };
        result as isize > 32
    }
}

/// The address as `os.startfile` passes it: UTF-16 and NUL-terminated. An interior NUL is
/// Python's `ValueError("embedded null character")`.
fn wide(url: &str) -> Result<Vec<u16>, ConfigFailure> {
    if url.contains('\0') {
        return Err(ConfigFailure::python("ValueError", "embedded null character"));
    }
    Ok(url.encode_utf16().chain(std::iter::once(0)).collect())
}

/// `webbrowser.open(url, new=2)` on Windows.
pub(crate) fn startfile(url: &str) -> Result<bool, ConfigFailure> {
    Ok(ffi::shell_open(&wide(url)?))
}

#[cfg(test)]
mod tests {
    use super::wide;

    // The shell call itself is not run in tests: it would open a browser or a dialog on the
    // person's desktop. Its inputs are checked here.
    #[test]
    fn addresses_become_nul_terminated_wide_strings() {
        assert_eq!(wide("https://a.b/é").unwrap(), "https://a.b/é\0".encode_utf16().collect::<Vec<_>>());
        assert_eq!(wide("a\0b").unwrap_err().to_string(), "embedded null character");
    }
}
