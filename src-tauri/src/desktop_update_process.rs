//! Create only the retained updater, inheriting an explicit wait/query handle list.
//! PyInstaller's one-file child inherits those same handle values from its bootloader.
use serde_json::Value;
use std::fs::File;
use std::path::Path;
use std::process::Child;

#[cfg(windows)]
pub(crate) struct HelperProcess(std::os::windows::io::OwnedHandle);

#[cfg(not(windows))]
pub(crate) struct HelperProcess;

impl HelperProcess {
    pub fn wait(&self) -> Result<(), String> {
        #[cfg(windows)]
        {
            use std::os::windows::io::AsRawHandle;
            use windows_sys::Win32::System::Threading::{WaitForSingleObject, INFINITE};
            if unsafe { WaitForSingleObject(self.0.as_raw_handle(), INFINITE) } != 0 {
                return Err("The owned update helper exit could not be verified".into());
            }
            Ok(())
        }
        #[cfg(not(windows))]
        Err("Update helpers require Windows".into())
    }
}

#[cfg(windows)]
pub(crate) fn spawn_owned(
    executable: &Path,
    owner: &Child,
) -> Result<(HelperProcess, File, File, Vec<Value>), String> {
    use serde_json::json;
    use std::ffi::OsString;
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use windows_sys::Win32::Foundation::{
        DuplicateHandle, SetHandleInformation, FILETIME, HANDLE_FLAG_INHERIT,
    };
    use windows_sys::Win32::Security::SECURITY_ATTRIBUTES;
    use windows_sys::Win32::System::Pipes::CreatePipe;
    use windows_sys::Win32::System::Threading::*;

    fn wide(value: &std::ffi::OsStr) -> Result<Vec<u16>, String> {
        let mut units: Vec<u16> = value.encode_wide().collect();
        if units.contains(&0) {
            return Err("Invalid helper launch location".into());
        }
        units.push(0);
        Ok(units)
    }

    fn pipe() -> Result<(OwnedHandle, OwnedHandle), String> {
        let mut read = std::ptr::null_mut();
        let mut write = std::ptr::null_mut();
        let security = SECURITY_ATTRIBUTES {
            nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: std::ptr::null_mut(),
            bInheritHandle: 1,
        };
        if unsafe { CreatePipe(&mut read, &mut write, &security, 0) } == 0 {
            return Err("The updater's private pipes could not be created".into());
        }
        Ok(unsafe {
            (
                OwnedHandle::from_raw_handle(read),
                OwnedHandle::from_raw_handle(write),
            )
        })
    }

    struct AttributeGuard(LPPROC_THREAD_ATTRIBUTE_LIST);
    impl Drop for AttributeGuard {
        fn drop(&mut self) {
            unsafe { DeleteProcThreadAttributeList(self.0) };
        }
    }

    let (child_read, parent_write) = pipe()?;
    let (parent_read, child_write) = pipe()?;
    for parent in [&parent_write, &parent_read] {
        if unsafe { SetHandleInformation(parent.as_raw_handle(), HANDLE_FLAG_INHERIT, 0) } == 0 {
            return Err("The updater's private pipe ownership could not be set".into());
        }
    }
    let current = unsafe { GetCurrentProcess() };
    let mut copies = Vec::new();
    let mut receipts = Vec::new();
    for (role, source) in [("desktop", current), ("owner", owner.as_raw_handle())] {
        let mut created: FILETIME = unsafe { std::mem::zeroed() };
        let mut exited: FILETIME = unsafe { std::mem::zeroed() };
        let mut kernel: FILETIME = unsafe { std::mem::zeroed() };
        let mut user: FILETIME = unsafe { std::mem::zeroed() };
        let pid = unsafe { GetProcessId(source) };
        if pid == 0
            || unsafe { GetProcessTimes(source, &mut created, &mut exited, &mut kernel, &mut user) }
                == 0
        {
            return Err("The owned process identity is unavailable".into());
        }
        let mut copy = std::ptr::null_mut();
        // Only SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION; no process-control access.
        if unsafe { DuplicateHandle(current, source, current, &mut copy, 0x0010_1000, 1, 0) } == 0 {
            return Err("The owned wait handle could not be prepared".into());
        }
        let copy = unsafe { OwnedHandle::from_raw_handle(copy) };
        let birth = ((created.dwHighDateTime as u64) << 32) | created.dwLowDateTime as u64;
        receipts.push(
            json!({"role": role, "handle": copy.as_raw_handle() as usize,
                            "pid": pid, "creation_time": birth}),
        );
        copies.push(copy);
    }

    // This explicit list prevents inheriting arbitrary native/WebView handles.
    let mut inherited = vec![child_read.as_raw_handle(), child_write.as_raw_handle()];
    inherited.extend(copies.iter().map(AsRawHandle::as_raw_handle));
    let mut bytes = 0_usize;
    unsafe { InitializeProcThreadAttributeList(std::ptr::null_mut(), 1, 0, &mut bytes) };
    if bytes == 0 || bytes > 65536 {
        return Err("The helper handle list is unavailable".into());
    }
    let mut allocation = vec![0_u128; bytes.div_ceil(std::mem::size_of::<u128>())];
    let attributes = allocation.as_mut_ptr().cast();
    if unsafe { InitializeProcThreadAttributeList(attributes, 1, 0, &mut bytes) } == 0 {
        return Err("The helper handle list could not be initialized".into());
    }
    let _attribute_guard = AttributeGuard(attributes);
    if unsafe {
        UpdateProcThreadAttribute(
            attributes,
            0,
            PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
            inherited.as_mut_ptr().cast(),
            std::mem::size_of_val(inherited.as_slice()),
            std::ptr::null_mut(),
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err("The helper's explicit inherited handles could not be set".into());
    }

    let app_name = wide(executable.as_os_str())?;
    if executable
        .as_os_str()
        .encode_wide()
        .any(|c| c == b'"' as u16)
    {
        return Err("The helper executable location is invalid".into());
    }
    let mut command_line = OsString::from("\"");
    command_line.push(executable);
    command_line.push("\" --desktop-update-helper");
    let mut command_line = wide(&command_line)?;
    let cwd = wide(
        executable
            .parent()
            .ok_or("The helper folder is unavailable")?
            .as_os_str(),
    )?;
    // Same-user inheritance remains in memory. No environment block or value is logged.
    let mut environment: Vec<_> = std::env::vars_os()
        .filter(|(name, _)| {
            !name
                .to_string_lossy()
                .eq_ignore_ascii_case("PYINSTALLER_RESET_ENVIRONMENT")
        })
        .collect();
    environment.push((
        OsString::from("PYINSTALLER_RESET_ENVIRONMENT"),
        OsString::from("1"),
    ));
    environment.sort_by_key(|(name, _)| name.to_string_lossy().to_lowercase());
    let mut environment_block = Vec::new();
    for (name, value) in environment {
        let mut entry = name;
        entry.push("=");
        entry.push(value);
        environment_block.extend(wide(&entry)?);
    }
    environment_block.push(0);
    let mut startup: STARTUPINFOEXW = unsafe { std::mem::zeroed() };
    startup.StartupInfo.cb = std::mem::size_of::<STARTUPINFOEXW>() as u32;
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = child_read.as_raw_handle();
    startup.StartupInfo.hStdOutput = child_write.as_raw_handle();
    startup.StartupInfo.hStdError = child_write.as_raw_handle();
    startup.lpAttributeList = attributes;
    let mut process: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
    if unsafe {
        CreateProcessW(
            app_name.as_ptr(),
            command_line.as_mut_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            1,
            CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT,
            environment_block.as_ptr().cast(),
            cwd.as_ptr(),
            &startup.StartupInfo,
            &mut process,
        )
    } == 0
    {
        return Err("The retained update helper could not start".into());
    }
    let process_handle = unsafe { OwnedHandle::from_raw_handle(process.hProcess) };
    let _thread = unsafe { OwnedHandle::from_raw_handle(process.hThread) };
    // Child and bootloader-child retain the copied handles. Parent copies close by RAII here.
    Ok((
        HelperProcess(process_handle),
        File::from(parent_write),
        File::from(parent_read),
        receipts,
    ))
}

#[cfg(not(windows))]
pub(crate) fn spawn_owned(
    _executable: &Path,
    _owner: &Child,
) -> Result<(HelperProcess, File, File, Vec<Value>), String> {
    Err("Update helpers require Windows".into())
}
