//! The desktop update's direct Windows calls, where Python itself calls the operating system
//! (rule 9 of `docs/rust-port/README.md`, approved for this module). Each call is wrapped in a
//! small safe function with its own `SAFETY:` comment and a Windows test.
//!
//! So far: `GetDiskFreeSpaceExW` for `shutil.disk_usage(path).free`.
#![allow(unsafe_code)]

use std::os::windows::ffi::OsStrExt;
use std::path::Path;
use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;

/// `ERROR_DIRECTORY`: "The directory name is invalid", what a file path gives.
const ERROR_DIRECTORY: i32 = 267;
/// `MAX_PATH`, the bound `_dirnameW` measures with `wcsnlen_s`.
const MAX_PATH: usize = 260;

/// One `GetDiskFreeSpaceExW(path, &_, &total, &free)` call: the third out-parameter,
/// `lpTotalNumberOfFreeBytes`, is what Python returns as `free`.
fn total_free_bytes(wide: &[u16]) -> std::io::Result<u64> {
    debug_assert_eq!(wide.last(), Some(&0), "the path is NUL-terminated");
    let (mut caller, mut total, mut free) = (0u64, 0u64, 0u64);
    // SAFETY: `wide` is a NUL-terminated UTF-16 string that outlives the call, and the three
    // out-parameters are valid, writable `u64`s (`ULARGE_INTEGER`) owned by this frame. The
    // call writes nothing else and keeps no pointer after it returns.
    let succeeded = unsafe { GetDiskFreeSpaceExW(wide.as_ptr(), &mut caller, &mut total, &mut free) };
    if succeeded == 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(free)
}

/// `_dirnameW(path)`: cut the path at its last `\` or `/` (searching from the end, never
/// position 0); `None` when the path is `MAX_PATH` units or longer.
fn dirname_wide(wide: &[u16]) -> Option<Vec<u16>> {
    let length = wide.iter().take(MAX_PATH).take_while(|unit| **unit != 0).count();
    if length == MAX_PATH {
        return None;
    }
    let mut cut = length;
    while cut != 0 && wide[cut] != u16::from(b'\\') && wide[cut] != u16::from(b'/') {
        cut -= 1;
    }
    let mut result = wide[..cut].to_vec();
    result.push(0);
    Some(result)
}

/// `nt._getdiskusage(path)[1]`, as `shutil.disk_usage(path).free` reads it (CPython 3.12.11,
/// `os__getdiskusage_impl`): the total free bytes of the path's volume, retried once with the
/// path's parent when Windows says the path is not a folder.
pub fn disk_free(path: &Path) -> std::io::Result<u64> {
    let mut wide: Vec<u16> = path.as_os_str().encode_wide().collect();
    wide.push(0);
    match total_free_bytes(&wide) {
        Ok(free) => Ok(free),
        Err(error) if error.raw_os_error() == Some(ERROR_DIRECTORY) => match dirname_wide(&wide) {
            Some(parent) => total_free_bytes(&parent),
            None => Err(error),
        },
        Err(error) => Err(error),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain([0]).collect()
    }

    #[test]
    fn dirname_cuts_at_the_last_separator_like_cpython() {
        assert_eq!(dirname_wide(&wide(r"C:\a\b.txt")), Some(wide(r"C:\a")));
        assert_eq!(dirname_wide(&wide("C:/a/b")), Some(wide("C:/a")));
        assert_eq!(dirname_wide(&wide("name")), Some(wide("")));
        let long = format!(r"C:\{}", "a".repeat(MAX_PATH));
        assert_eq!(dirname_wide(&wide(&long)), None);
    }

    #[test]
    fn a_folder_and_a_file_in_it_report_the_same_volume() {
        let folder = std::env::temp_dir().join(format!("sylvia-disk-free-{}", std::process::id()));
        std::fs::create_dir_all(&folder).unwrap();
        let file = folder.join("file.txt");
        std::fs::write(&file, b"x").unwrap();
        let from_folder = disk_free(&folder).unwrap();
        let from_file = disk_free(&file).unwrap();
        assert!(from_folder > 0);
        // Both read the same volume; other processes may write in between.
        assert!(from_folder.abs_diff(from_file) < 256 * 1024 * 1024);
        assert!(disk_free(&folder.join("missing").join("deeper")).is_err());
        let _ = std::fs::remove_dir_all(&folder);
    }
}
