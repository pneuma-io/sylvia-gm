//! The desktop update's direct Windows calls, where Python itself calls the operating system
//! (rule 9 of `docs/rust-port/README.md`, approved for this module). Each call is wrapped in a
//! small safe function with its own `SAFETY:` comment and a Windows test.
//!
//! The calls (the registry reads `installed.py` makes are `sylvia_base::winreg`'s):
//! - `GetDiskFreeSpaceExW` for `shutil.disk_usage(path).free`;
//! - `GetProcessId`, `GetProcessTimes` and `WaitForSingleObject` for `processes.py`, on the
//!   process handles the desktop duplicated into the helper;
//! - `SetHandleInformation`, which keeps those handles and the helper's standard handles out
//!   of the helper's own children (Python's `Popen` passes a child only its own standard
//!   handles; Rust's `Command` lets a child inherit every inheritable handle);
//! - `LoadLibraryExW`, `GetProcAddress` and `SendMessageW` for the task dialog of
//!   `progress.py`. `TaskDialogIndirect` exists only in common controls 6, so it is found at
//!   run time, as Python's `ctypes` finds it: an import would stop the executable starting at
//!   all without a common-controls 6 manifest.
#![allow(unsafe_code)]

use std::os::windows::ffi::OsStrExt;
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle, RawHandle};
use std::path::Path;
#[cfg(test)]
use windows_sys::Win32::Foundation::GetHandleInformation;
use windows_sys::Win32::Foundation::{
    SetHandleInformation, FILETIME, HANDLE, HANDLE_FLAG_INHERIT, HWND, WAIT_FAILED, WAIT_OBJECT_0,
    WAIT_TIMEOUT,
};
use windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW;
use windows_sys::Win32::System::LibraryLoader::{
    GetProcAddress, LoadLibraryExW, LOAD_LIBRARY_SEARCH_SYSTEM32,
};
use windows_sys::Win32::System::Threading::{GetProcessId, GetProcessTimes, WaitForSingleObject};
use windows_sys::Win32::UI::Controls::TASKDIALOGCONFIG;
use windows_sys::Win32::UI::WindowsAndMessaging::SendMessageW;

fn wide_text(text: &str) -> Vec<u16> {
    text.encode_utf16().chain([0]).collect()
}

/// `GetProcessId` and `GetProcessTimes` on a handle value from the helper's receipt, which is
/// only a number until it matches: `(pid, creation time)`, or `None`. Neither call takes
/// ownership or changes the handle, so an unrelated or stale value is harmless.
pub fn process_identity(handle: u64) -> Option<(u64, u64)> {
    let raw = usize::try_from(handle).ok()? as HANDLE;
    // SAFETY: `GetProcessId` only reads the handle; an invalid value makes it return 0.
    let pid = unsafe { GetProcessId(raw) };
    if pid == 0 {
        return None;
    }
    let zero = FILETIME { dwLowDateTime: 0, dwHighDateTime: 0 };
    let (mut created, mut exited, mut kernel, mut user) = (zero, zero, zero, zero);
    // SAFETY: the four out-parameters are writable `FILETIME`s owned by this frame; the call
    // only reads the handle.
    let times = unsafe { GetProcessTimes(raw, &mut created, &mut exited, &mut kernel, &mut user) };
    if times == 0 {
        return None;
    }
    Some((u64::from(pid), (u64::from(created.dwHighDateTime) << 32) | u64::from(created.dwLowDateTime)))
}

/// Take ownership of a receipt's handle once [`process_identity`] matched it, so dropping the
/// result closes it exactly once, and keep it out of the helper's children.
pub fn adopt_process(handle: u64) -> std::io::Result<OwnedHandle> {
    let raw = usize::try_from(handle).map_err(|_| std::io::Error::from(std::io::ErrorKind::InvalidInput))?
        as RawHandle;
    // SAFETY: the desktop duplicated this handle into this process for the helper alone, and
    // its process identity was just verified, so this process owns it. Nothing else wraps or
    // closes it: each receipt's handle is distinct and adopted at most once.
    let owned = unsafe { OwnedHandle::from_raw_handle(raw) };
    keep_from_children(owned.as_raw_handle())?;
    Ok(owned)
}

/// `SetHandleInformation(handle, HANDLE_FLAG_INHERIT, 0)`.
pub fn keep_from_children(handle: RawHandle) -> std::io::Result<()> {
    // SAFETY: the call changes only the inheritance flag of a handle this process holds.
    if unsafe { SetHandleInformation(handle as HANDLE, HANDLE_FLAG_INHERIT, 0) } == 0 {
        return Err(std::io::Error::last_os_error());
    }
    Ok(())
}

/// Whether a handle would be inherited by a child (`GetHandleInformation`), for the tests.
#[cfg(test)]
fn inherited(handle: RawHandle) -> bool {
    let mut flags = 0u32;
    // SAFETY: `flags` is a writable out-parameter owned by this frame.
    let read = unsafe { GetHandleInformation(handle as HANDLE, &mut flags) };
    assert_ne!(read, 0, "{}", std::io::Error::last_os_error());
    flags & HANDLE_FLAG_INHERIT != 0
}

/// `WaitForSingleObject(handle, 0)`: `Some(true)` once the process exited, `Some(false)`
/// while it runs, `None` when the wait failed (`WAIT_FAILED` or any other answer).
pub fn process_exited(handle: &OwnedHandle) -> Option<bool> {
    // SAFETY: the handle is open and owned; a zero timeout only polls it.
    let result = unsafe { WaitForSingleObject(handle.as_raw_handle() as HANDLE, 0) };
    match result {
        WAIT_OBJECT_0 => Some(true),
        WAIT_TIMEOUT => Some(false),
        WAIT_FAILED => None,
        _ => None,
    }
}

/// `TaskDialogIndirect`, declared exactly as windows-sys declares it.
pub type TaskDialogIndirect = unsafe extern "system" fn(
    ptaskconfig: *const TASKDIALOGCONFIG,
    pnbutton: *mut i32,
    pnradiobutton: *mut i32,
    pfverificationflagchecked: *mut windows_sys::core::BOOL,
) -> windows_sys::core::HRESULT;

/// `ctypes.WinDLL("comctl32").TaskDialogIndirect`: common controls from the system folder
/// (version 6 when this executable's manifest asks for it), never freed. `None` when this
/// common-controls version has no task dialog.
pub fn task_dialog_indirect() -> Option<TaskDialogIndirect> {
    let library = wide_text("comctl32.dll");
    // SAFETY: `library` is NUL-terminated UTF-16; the library stays loaded for the process,
    // as Python's does, so the function pointer below never dangles.
    let module =
        unsafe { LoadLibraryExW(library.as_ptr(), std::ptr::null_mut(), LOAD_LIBRARY_SEARCH_SYSTEM32) };
    if module.is_null() {
        return None;
    }
    // SAFETY: the module is loaded and the name is a NUL-terminated ASCII export name.
    let found = unsafe { GetProcAddress(module, c"TaskDialogIndirect".as_ptr().cast()) }?;
    // SAFETY: `TaskDialogIndirect` in comctl32 has exactly the `TaskDialogIndirect` signature
    // above (windows-sys's declaration, `extern "system"`); only the pointer's type changes.
    Some(unsafe { std::mem::transmute::<unsafe extern "system" fn() -> isize, TaskDialogIndirect>(found) })
}

/// `SendMessageW(hwnd, message, wparam, lparam)` to the helper's own task dialog.
pub fn send_message(window: isize, message: u32, wparam: usize, lparam: isize) -> isize {
    // SAFETY: `window` is the task dialog this thread created (captured from its own
    // `TDN_CREATED` notification and dropped at `TDN_DESTROYED`); every message sent this way
    // is a task-dialog message whose `lparam` is a plain number.
    unsafe { SendMessageW(window as HWND, message, wparam, lparam) }
}

/// `SendMessageW(hwnd, TDM_SET_ELEMENT_TEXT, element, text)` with `text` as a wide string.
pub fn send_text(window: isize, message: u32, wparam: usize, text: &str) -> isize {
    let wide = wide_text(text);
    // SAFETY: as `send_message`; `wide` is NUL-terminated and outlives the synchronous call,
    // which copies the text.
    unsafe { SendMessageW(window as HWND, message, wparam, wide.as_ptr() as isize) }
}

/// The texts and settings of the helper's one task dialog.
pub struct DialogText<'a> {
    pub title: &'a str,
    pub instruction: &'a str,
    pub content: &'a str,
    pub button: &'a str,
    pub button_id: i32,
    pub flags: i32,
    pub width: u32,
}

type Notify<'a> = &'a mut dyn FnMut(isize, u32) -> i32;

/// The dialog's callback: hands each notification to the caller's `notify`.
unsafe extern "system" fn dialog_callback(
    window: HWND,
    notification: u32,
    _wparam: usize,
    _lparam: isize,
    data: isize,
) -> windows_sys::core::HRESULT {
    // SAFETY: `data` points to the `Notify` that `task_dialog` keeps alive on its own frame,
    // on this same thread, for the whole `TaskDialogIndirect` call that delivers these
    // notifications; nothing else holds a reference to it meanwhile.
    let notify = unsafe { &mut *(data as *mut Notify<'_>) };
    notify(window as isize, notification)
}

/// `TaskDialogIndirect(config, &chosen, None, None)` with one button and the callback timer:
/// returns when the dialog closes. `Err` when this process has no task dialog or the dialog
/// could not open (a negative HRESULT).
pub fn task_dialog(text: &DialogText<'_>, notify: &mut dyn FnMut(isize, u32) -> i32) -> Result<(), ()> {
    use windows_sys::Win32::UI::Controls::TASKDIALOG_BUTTON;
    let dialog = task_dialog_indirect().ok_or(())?;
    let (title, instruction, content, button_text) =
        (wide_text(text.title), wide_text(text.instruction), wide_text(text.content), wide_text(text.button));
    let button = TASKDIALOG_BUTTON { nButtonID: text.button_id, pszButtonText: button_text.as_ptr() };
    let mut notify: Notify<'_> = notify;
    let config = TASKDIALOGCONFIG {
        cbSize: std::mem::size_of::<TASKDIALOGCONFIG>() as u32,
        dwFlags: text.flags,
        pszWindowTitle: title.as_ptr(),
        pszMainInstruction: instruction.as_ptr(),
        pszContent: content.as_ptr(),
        cButtons: 1,
        pButtons: &button,
        nDefaultButton: text.button_id,
        pfCallback: Some(dialog_callback),
        lpCallbackData: &mut notify as *mut Notify<'_> as isize,
        cxWidth: text.width,
        ..Default::default()
    };
    let mut chosen = 0i32;
    // SAFETY: `config` and everything it points to (the button, the four strings and the
    // callback data) live on this frame until the call returns; the dialog runs its own
    // message loop on this thread and keeps no pointer afterwards. The out-parameter for the
    // chosen button is owned by this frame; the other two may be null.
    let result = unsafe { dialog(&config, &mut chosen, std::ptr::null_mut(), std::ptr::null_mut()) };
    if result < 0 {
        return Err(());
    }
    Ok(())
}

/// The sizes of `TASKDIALOGCONFIG` and `TASKDIALOG_BUTTON` (one-byte packing in `commctrl.h`).
pub fn dialog_structure_sizes() -> (usize, usize) {
    (
        std::mem::size_of::<TASKDIALOGCONFIG>(),
        std::mem::size_of::<windows_sys::Win32::UI::Controls::TASKDIALOG_BUTTON>(),
    )
}

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

    /// A child of this process, started with a handle to its own process.
    fn own_process_handle() -> u64 {
        use std::os::windows::io::IntoRawHandle;
        let child =
            std::process::Command::new(std::env::var_os("ComSpec").unwrap_or_else(|| "cmd.exe".into()))
                .args(["/d", "/c", "exit", "0"])
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .spawn()
                .unwrap();
        let handle = std::os::windows::io::OwnedHandle::from(child).into_raw_handle();
        handle as usize as u64
    }

    #[test]
    fn a_process_handle_is_verified_adopted_and_waited_on() {
        let raw = own_process_handle();
        let (pid, created) = process_identity(raw).expect("a process handle");
        assert!(pid > 0 && created > 0);
        // A value that is no handle at all is refused without being touched.
        assert_eq!(process_identity(0x7FFF_FFF0), None);
        let owned = adopt_process(raw).unwrap();
        assert!(!inherited(owned.as_raw_handle()));
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
        while process_exited(&owned) != Some(true) {
            assert!(std::time::Instant::now() < deadline, "the child did not exit");
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
    }

    #[test]
    fn a_cleared_handle_is_not_inherited_by_a_child_started_afterwards() {
        use std::os::windows::io::IntoRawHandle;
        // An inheritable handle, as the desktop's duplicates arrive in the helper.
        let file = std::fs::File::open(std::env::current_exe().unwrap()).unwrap();
        let handle = file.into_raw_handle();
        // SAFETY (test): the handle was just taken from a file this test owns.
        assert_ne!(
            unsafe { SetHandleInformation(handle as HANDLE, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT) },
            0
        );
        assert!(inherited(handle));
        keep_from_children(handle).unwrap();
        assert!(!inherited(handle));
        // A child started now gets only the handles std gives it.
        let status =
            std::process::Command::new(std::env::var_os("ComSpec").unwrap_or_else(|| "cmd.exe".into()))
                .args(["/d", "/c", "exit", "0"])
                .status()
                .unwrap();
        assert!(status.success());
        assert!(!inherited(handle));
        // SAFETY (test): this test owns the handle and closes it once.
        drop(unsafe { OwnedHandle::from_raw_handle(handle) });
    }
}
