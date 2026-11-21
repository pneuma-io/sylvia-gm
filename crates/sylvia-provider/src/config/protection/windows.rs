//! Current-user DPAPI (`_dpapi`): `CryptProtectData` and `CryptUnprotectData` with
//! `CRYPTPROTECT_UI_FORBIDDEN` only (never machine scope), no description, no entropy.
//!
//! The only unsafe code is the inner `ffi` module (approved under rule 9 of
//! `docs/rust-port/README.md`). Native error details are never exposed: a failure is
//! Python's message.

use super::{OVERSIZE_MESSAGE, SECRET_BYTE_LIMIT, UNAVAILABLE_MESSAGE};
use crate::config::ConfigFailure;

/// Overwrite a buffer so the secret does not linger (`ctypes.memset(buffer, 0, ...)`);
/// `black_box` keeps the writes from being optimised away.
fn wipe(buffer: &mut [u8]) {
    buffer.fill(0);
    std::hint::black_box(buffer);
}

#[allow(unsafe_code)]
mod ffi {
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptProtectData, CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    /// The output of one DPAPI call, copied out of the system's buffer, which is wiped and
    /// freed before returning; `None` when the call fails.
    pub(super) fn run(input: &mut [u8], decrypt: bool) -> Option<Vec<u8>> {
        let length = u32::try_from(input.len()).ok()?;
        let source = CRYPT_INTEGER_BLOB { cbData: length, pbData: input.as_mut_ptr() };
        let mut result = CRYPT_INTEGER_BLOB { cbData: 0, pbData: std::ptr::null_mut() };
        // SAFETY: `source` points at `input`, whose length is `cbData`, and `input` outlives the
        // call. Description, entropy, reserved and prompt are null, which both functions
        // accept (a null description output for `CryptUnprotectData` means none is returned,
        // so no string is allocated). On success the system allocates `result.pbData` with
        // `LocalAlloc`; it is freed below on every path.
        let ok = unsafe {
            if decrypt {
                CryptUnprotectData(
                    &source,
                    std::ptr::null_mut(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    CRYPTPROTECT_UI_FORBIDDEN,
                    &mut result,
                )
            } else {
                CryptProtectData(
                    &source,
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    std::ptr::null(),
                    CRYPTPROTECT_UI_FORBIDDEN,
                    &mut result,
                )
            }
        };
        if result.pbData.is_null() {
            return None;
        }
        // SAFETY: on success `result.pbData` holds `result.cbData` initialised bytes that this
        // function owns until `LocalFree`. They are copied, wiped and freed exactly once.
        let copied = unsafe {
            let output = std::slice::from_raw_parts_mut(result.pbData, result.cbData as usize);
            let copied = output.to_vec();
            for byte in output.iter_mut() {
                std::ptr::write_volatile(byte, 0);
            }
            LocalFree(result.pbData.cast());
            copied
        };
        (ok != 0).then_some(copied)
    }
}

/// `_dpapi(value, decrypt=...)`.
pub(crate) fn dpapi(value: &[u8], decrypt: bool) -> Result<Vec<u8>, ConfigFailure> {
    let mut input = value.to_vec();
    let output = ffi::run(&mut input, decrypt);
    wipe(&mut input);
    let Some(mut output) = output else {
        return Err(ConfigFailure::config(if decrypt {
            UNAVAILABLE_MESSAGE
        } else {
            "Windows credential protection failed"
        }));
    };
    if output.len() > SECRET_BYTE_LIMIT {
        wipe(&mut output);
        return Err(ConfigFailure::config(OVERSIZE_MESSAGE));
    }
    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::dpapi;

    /// A disposable value generated here; no real credential is read or written.
    #[test]
    fn protect_then_unprotect_round_trips() {
        let mut secret = [0u8; 48];
        getrandom::getrandom(&mut secret).unwrap();
        let sealed = dpapi(&secret, false).unwrap();
        assert_ne!(sealed, secret);
        assert_eq!(dpapi(&sealed, true).unwrap(), secret);
    }

    #[test]
    fn a_damaged_blob_fails_with_the_python_message() {
        let mut sealed = dpapi(b"disposable", false).unwrap();
        let last = sealed.len() - 1;
        sealed[last] ^= 0xFF;
        assert_eq!(dpapi(&sealed, true).unwrap_err().to_string(), super::UNAVAILABLE_MESSAGE);
        assert_eq!(dpapi(b"not a blob", true).unwrap_err().to_string(), super::UNAVAILABLE_MESSAGE);
    }
}
