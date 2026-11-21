//! `winreg` as CPython 3.12 reads a value (`PC/winreg.c`): read-only access to the current
//! user's registry for the two places Sylvia's Python reads it, the desktop update's
//! installed-copy registration (`installed.py`) and the HTTP proxy settings
//! (`urllib.request.getproxies_registry`).
//!
//! The direct Windows calls (`RegOpenKeyExW`, `RegQueryValueExW`, `RegCloseKey`) are the
//! port's rule 9 module for the registry: each sits in a small safe wrapper with its own
//! `SAFETY:` comment and Windows tests. Opening is always read-only and an open key is closed
//! on every path by drop. The conversions ([`sz_units`], [`dword`]) are plain code, available
//! on every platform.
#![allow(unsafe_code)]

/// `REG_SZ`.
pub const REG_SZ: u32 = 1;
/// `REG_EXPAND_SZ`.
pub const REG_EXPAND_SZ: u32 = 2;
/// `REG_DWORD`.
pub const REG_DWORD: u32 = 4;

/// A value `QueryValueEx` returned, for the types Sylvia reads. A `REG_SZ` is kept as
/// UTF-16 code units: Python's `str` from the registry may hold a lone surrogate.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Value {
    Sz(Vec<u16>),
    /// `REG_EXPAND_SZ`, which Python converts exactly as `REG_SZ` (and does not expand).
    ExpandSz(Vec<u16>),
    Dword(u32),
}

/// Why a read gave no [`Value`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// Python's `OSError` with this `winerror`: `2` (`FileNotFoundError`) for a missing key
    /// or value, any other code as Windows gave it.
    Os(i32),
    /// The value exists with another type (Python returns it; Sylvia's callers refuse it).
    OtherType(u32),
}

impl Error {
    /// A missing key or value (`FileNotFoundError`).
    pub fn is_not_found(&self) -> bool {
        *self == Error::Os(2)
    }
}

/// `Reg2Py` for `REG_SZ` and `REG_EXPAND_SZ`: the bytes as UTF-16 code units, an odd
/// trailing byte dropped, cut at the first NUL (all of them when there is none), "to be
/// consistent with reg.exe and regedit.exe".
pub fn sz_units(data: &[u8]) -> Vec<u16> {
    data.as_chunks::<2>()
        .0
        .iter()
        .map(|pair| u16::from_le_bytes(*pair))
        .take_while(|unit| *unit != 0)
        .collect()
}

/// `Reg2Py` for `REG_DWORD`: `0` for no data, otherwise the first four bytes, little-endian.
/// (For one to three bytes CPython reads past the returned data; here the missing bytes are
/// zero.)
pub fn dword(data: &[u8]) -> u32 {
    let mut bytes = [0u8; 4];
    for (target, byte) in bytes.iter_mut().zip(data) {
        *target = *byte;
    }
    u32::from_le_bytes(bytes)
}

/// `Reg2Py` for the types Sylvia reads, from the type and the bytes Windows returned.
pub fn convert(kind: u32, data: &[u8]) -> Result<Value, Error> {
    match kind {
        REG_SZ => Ok(Value::Sz(sz_units(data))),
        REG_EXPAND_SZ => Ok(Value::ExpandSz(sz_units(data))),
        REG_DWORD => Ok(Value::Dword(dword(data))),
        other => Err(Error::OtherType(other)),
    }
}

/// Python's `len(value)` for a registry string: code points, a surrogate pair counting once.
pub fn characters(units: &[u16]) -> usize {
    char::decode_utf16(units.iter().copied()).count()
}

/// The registry root. Only the current user's is read.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Root {
    CurrentUser,
}

/// The registry view a key is opened in: `KEY_READ` (`winreg.OpenKey`'s default) or
/// `KEY_READ | KEY_WOW64_64KEY`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum View {
    Default,
    Wow64_64,
}

#[cfg(windows)]
mod native {
    use super::{convert, Error, Root, Value, View};
    use windows_sys::Win32::Foundation::{ERROR_MORE_DATA, ERROR_SUCCESS};
    use windows_sys::Win32::System::Registry::{
        RegCloseKey, RegOpenKeyExW, RegQueryValueExW, HKEY, HKEY_CURRENT_USER, KEY_READ, KEY_WOW64_64KEY,
    };

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain([0]).collect()
    }

    /// An open, read-only registry key, closed by drop.
    #[derive(Debug)]
    pub struct Key(HKEY);

    impl Drop for Key {
        fn drop(&mut self) {
            // SAFETY: the key was opened by `RegOpenKeyExW` in `Key::open` and is closed exactly
            // once, here.
            unsafe { RegCloseKey(self.0) };
        }
    }

    impl Key {
        /// `winreg.OpenKey(root, subkey, 0, access)`, read-only.
        pub fn open(root: Root, subkey: &str, view: View) -> Result<Key, Error> {
            let root = match root {
                Root::CurrentUser => HKEY_CURRENT_USER,
            };
            let access = match view {
                View::Default => KEY_READ,
                View::Wow64_64 => KEY_READ | KEY_WOW64_64KEY,
            };
            let subkey = wide(subkey);
            let mut key: HKEY = std::ptr::null_mut();
            // SAFETY: `subkey` is NUL-terminated UTF-16 that outlives the call; `key` is a
            // writable out-parameter owned by this frame. The access asked for is read-only.
            let opened = unsafe { RegOpenKeyExW(root, subkey.as_ptr(), 0, access, &mut key) };
            if opened != ERROR_SUCCESS {
                return Err(Error::Os(opened as i32));
            }
            Ok(Key(key))
        }

        /// `winreg.QueryValueEx(key, name)` as CPython 3.12 reads it: the size first, then the
        /// value, doubling the buffer while Windows answers `ERROR_MORE_DATA`; the type and
        /// the bytes Windows returned. (CPython converts its whole buffer; it differs from the
        /// returned bytes only when the value changes between the two calls.)
        pub fn query_raw(&self, name: &str) -> Result<(u32, Vec<u8>), Error> {
            let name = wide(name);
            let mut size: u32 = 0;
            // SAFETY: the key is open; `name` is NUL-terminated; a null type and data pointer
            // ask only for the size, written to `size`, owned by this frame.
            let asked = unsafe {
                RegQueryValueExW(
                    self.0,
                    name.as_ptr(),
                    std::ptr::null(),
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                    &mut size,
                )
            };
            if asked == ERROR_MORE_DATA {
                size = 256;
            } else if asked != ERROR_SUCCESS {
                return Err(Error::Os(asked as i32));
            }
            let mut buffer = vec![0u8; size as usize];
            loop {
                let mut kind: u32 = 0;
                let mut returned = u32::try_from(buffer.len()).map_err(|_| Error::Os(234))?;
                // SAFETY: `buffer` is writable for `returned` bytes; `kind` and `returned` are
                // writable out-parameters owned by this frame. Windows writes at most
                // `returned` bytes and reports how many it wrote.
                let read = unsafe {
                    RegQueryValueExW(
                        self.0,
                        name.as_ptr(),
                        std::ptr::null(),
                        &mut kind,
                        buffer.as_mut_ptr(),
                        &mut returned,
                    )
                };
                if read == ERROR_MORE_DATA {
                    let doubled = buffer.len().max(1).checked_mul(2).ok_or(Error::Os(234))?;
                    buffer.resize(doubled, 0);
                    continue;
                }
                if read != ERROR_SUCCESS {
                    return Err(Error::Os(read as i32));
                }
                buffer.truncate((returned as usize).min(buffer.len()));
                return Ok((kind, buffer));
            }
        }

        /// `winreg.QueryValueEx(key, name)[0]` for a `REG_SZ`, `REG_EXPAND_SZ` or `REG_DWORD`.
        pub fn query_value_ex(&self, name: &str) -> Result<Value, Error> {
            let (kind, data) = self.query_raw(name)?;
            convert(kind, &data)
        }
    }

    /// Open `subkey` (`winreg.OpenKey`'s default access) and read one value.
    pub fn query_value_ex(root: Root, subkey: &str, value_name: &str) -> Result<Value, Error> {
        Key::open(root, subkey, View::Default)?.query_value_ex(value_name)
    }
}

#[cfg(windows)]
pub use native::{query_value_ex, Key};

#[cfg(test)]
mod tests {
    use super::*;

    fn wide(text: &str) -> Vec<u8> {
        text.encode_utf16().flat_map(u16::to_le_bytes).collect()
    }

    #[test]
    fn strings_and_numbers_convert_as_reg2py() {
        assert_eq!(sz_units(&[0x41, 0, 0x42]), [0x41], "an odd byte is dropped");
        assert_eq!(sz_units(&wide("AB")), wide_units("AB"), "no NUL: all of it");
        assert_eq!(sz_units(&wide("A\0B\0")), wide_units("A"), "cut at the first NUL");
        assert!(sz_units(&[]).is_empty());
        assert_eq!(dword(&[]), 0);
        assert_eq!(dword(&[1, 2, 3, 4, 5]), 0x0403_0201);
        assert_eq!(dword(&[7]), 7);
        assert_eq!(convert(3, &[1]), Err(Error::OtherType(3)));
        assert_eq!(convert(REG_EXPAND_SZ, &wide("A ")), Ok(Value::ExpandSz(wide_units("A"))));
        assert_eq!(characters(&wide_units("x\u{1F3B2}")), 2);
        assert_eq!(characters(&[0xDC00, 0x41]), 2);
    }

    fn wide_units(text: &str) -> Vec<u16> {
        text.encode_utf16().collect()
    }
}
