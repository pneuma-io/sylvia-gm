//! Ephemeral on-device speech capture. The renderer remains the only draft editor.
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::{Duration, Instant};

const START_TIMEOUT: Duration = Duration::from_secs(30);
const STOP_TIMEOUT: Duration = Duration::from_secs(130);
const PREPARE_TIMEOUT: Duration = Duration::from_secs(600);
const MAX_LIFETIME: Duration = Duration::from_secs(300);
const EXIT_TIMEOUT: Duration = Duration::from_secs(5);
const IDLE_TIMEOUT: Duration = Duration::from_secs(300);

type Reply = Result<Value, String>;

#[cfg(windows)]
struct ProcessJob(usize);

#[cfg(windows)]
impl ProcessJob {
    fn wait_empty(&self) -> Result<(), String> {
        use windows_sys::Win32::System::JobObjects::*;
        loop {
            let mut info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
            let read = unsafe { QueryInformationJobObject(
                self.0 as _, JobObjectBasicAccountingInformation, &mut info as *mut _ as _,
                std::mem::size_of_val(&info) as u32, std::ptr::null_mut(),
            ) };
            if read == 0 { return Err("The owned dictation job could not be drained".into()); }
            if info.ActiveProcesses == 0 { return Ok(()); }
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    fn terminate(&self) {
        use windows_sys::Win32::System::JobObjects::*;
        unsafe {
            let _ = TerminateJobObject(self.0 as _, 1);
            let deadline = Instant::now() + Duration::from_secs(2);
            loop {
                let mut info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
                if QueryInformationJobObject(
                    self.0 as _,
                    JobObjectBasicAccountingInformation,
                    &mut info as *mut _ as _,
                    std::mem::size_of_val(&info) as u32,
                    std::ptr::null_mut(),
                ) != 0
                    && info.ActiveProcesses == 0
                {
                    return;
                }
                if Instant::now() >= deadline {
                    return;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
        }
    }

    // The primary thread is still suspended, so no descendant can escape assignment.
    fn resume(child: &Child) -> Result<(), String> {
        use windows_sys::Win32::Foundation::{CloseHandle, INVALID_HANDLE_VALUE};
        use windows_sys::Win32::System::Diagnostics::ToolHelp::*;
        use windows_sys::Win32::System::Threading::{
            OpenThread, ResumeThread, THREAD_SUSPEND_RESUME,
        };
        unsafe {
            let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
            if snapshot == INVALID_HANDLE_VALUE {
                return Err("unavailable".into());
            }
            let mut entry = THREADENTRY32::default();
            entry.dwSize = std::mem::size_of_val(&entry) as u32;
            let mut found = Vec::new();
            let mut next = Thread32First(snapshot, &mut entry);
            while next != 0 {
                if entry.th32OwnerProcessID == child.id() {
                    found.push(entry.th32ThreadID);
                }
                next = Thread32Next(snapshot, &mut entry);
            }
            CloseHandle(snapshot);
            if found.len() != 1 {
                return Err("unavailable".into());
            }
            let thread = OpenThread(THREAD_SUSPEND_RESUME, 0, found[0]);
            if thread.is_null() {
                return Err("unavailable".into());
            }
            let previous = ResumeThread(thread);
            CloseHandle(thread);
            if previous != 1 {
                return Err("unavailable".into());
            }
            Ok(())
        }
    }

    // Kill the one-file Python bootloader and its worker together when capture is cancelled.
    fn own(child: &Child) -> Result<Self, String> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::JobObjects::*;
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return Err("unavailable".into());
            }
            let job = Self(handle as usize);
            let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as _,
                std::mem::size_of_val(&limits) as u32,
            ) == 0
                || AssignProcessToJobObject(handle, child.as_raw_handle()) == 0
            {
                return Err("unavailable".into());
            }
            Ok(job)
        }
    }
}

struct WorkerTemp {
    root: PathBuf,
    path: PathBuf,
}

impl WorkerTemp {
    fn create(root: &Path) -> Result<Self, String> {
        std::fs::create_dir_all(root).map_err(|_| "storage")?;
        let root = root.canonicalize().map_err(|_| "storage")?;
        let path = root.join(format!("worker-{:032x}", rand::random::<u128>()));
        std::fs::create_dir(&path).map_err(|_| "storage")?;
        Ok(Self { root, path })
    }
}

impl Drop for WorkerTemp {
    fn drop(&mut self) {
        // Delete only this invocation's freshly created, resolved child directory.
        if self.path.parent() != Some(self.root.as_path())
            || self.path.canonicalize().ok().as_ref() != Some(&self.path)
        {
            return;
        }
        let deadline = Instant::now() + Duration::from_secs(2);
        while std::fs::remove_dir_all(&self.path).is_err() {
            if !self.path.exists() || Instant::now() >= deadline {
                return;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}

#[cfg(windows)]
impl Drop for ProcessJob {
    fn drop(&mut self) {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(self.0 as _);
        }
    }
}

#[derive(Default)]
struct Capture {
    cancelled: AtomicBool,
    stopping: AtomicBool,
    session: AtomicBool,
    deadline: Mutex<Option<Instant>>,
    child: Mutex<Option<Child>>,
    input: Mutex<Option<ChildStdin>>,
    replies: Mutex<Option<mpsc::Receiver<Reply>>>,
    finished: Mutex<Option<mpsc::Sender<()>>>,
    temporary_parent: Option<PathBuf>,
    temporary: Mutex<Option<WorkerTemp>>,
    #[cfg(windows)]
    job: Mutex<Option<ProcessJob>>,
}

impl Capture {
    fn launch(self: &Arc<Self>, mut command: Command, request: Value) -> Result<(), String> {
        if self.cancelled.load(Ordering::Acquire) {
            return Err("unavailable".into());
        }
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        let temporary = self
            .temporary_parent
            .as_deref()
            .map(WorkerTemp::create)
            .transpose()?;
        if let Some(temporary) = &temporary {
            command
                .env("TEMP", &temporary.path)
                .env("TMP", &temporary.path);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            use windows_sys::Win32::System::Threading::{CREATE_NO_WINDOW, CREATE_SUSPENDED};
            command.creation_flags(CREATE_NO_WINDOW | CREATE_SUSPENDED);
        }
        let mut child = command.spawn().map_err(|_| "unavailable")?;
        #[cfg(test)]
        if command
            .get_envs()
            .any(|(key, _)| key == "SYLVIA_DICTATION_TEST_DELAY_ASSIGNMENT")
        {
            std::thread::sleep(Duration::from_secs(2));
        }
        #[cfg(windows)]
        let assignment = {
            #[cfg(test)]
            if command
                .get_envs()
                .any(|(key, _)| key == "SYLVIA_DICTATION_TEST_FAIL_ASSIGNMENT")
            {
                Err("unavailable".into())
            } else {
                ProcessJob::own(&child)
            }
            #[cfg(not(test))]
            ProcessJob::own(&child)
        };
        #[cfg(windows)]
        let job = match assignment {
            Ok(job) => job,
            Err(error) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(error);
            }
        };
        #[cfg(windows)]
        if let Err(error) = ProcessJob::resume(&child) {
            drop(job);
            let _ = child.kill();
            let _ = child.wait();
            return Err(error);
        }
        let input = child.stdin.take().ok_or("unavailable")?;
        let output = child.stdout.take().ok_or("unavailable")?;
        {
            let mut owned = self.child.lock().map_err(|_| "provider")?;
            if self.cancelled.load(Ordering::Acquire) {
                let _ = child.kill();
                let _ = child.wait();
                return Err("unavailable".into());
            }
            *owned = Some(child);
            #[cfg(windows)]
            {
                *self.job.lock().map_err(|_| "provider")? = Some(job);
            }
            *self.input.lock().map_err(|_| "provider")? = Some(input);
            *self.temporary.lock().map_err(|_| "provider")? = temporary;
        }
        let session = request["mode"] == "session";
        self.session.store(session, Ordering::Release);
        self.extend_deadline(START_TIMEOUT);
        let (send, receive) = mpsc::sync_channel(2);
        *self.replies.lock().map_err(|_| "provider")? = Some(receive);
        std::thread::spawn(move || {
            let mut output = BufReader::new(output);
            // Bound both each reply and the queued replies, including while a runtime is idle.
            let mut replies = 0;
            loop {
                let mut line = String::new();
                let result = (&mut output).take(131_072).read_line(&mut line);
                let reply = match result {
                    Ok(_) if line.ends_with('\n') => {
                        serde_json::from_str(&line).map_err(|_| "provider".into())
                    }
                    _ => Err("provider".into()),
                };
                let failed = reply.is_err();
                if send.send(reply).is_err() || failed {
                    break;
                }
                replies += 1;
                if !session && replies == 2 {
                    break;
                }
            }
        });
        let (finished, watch) = mpsc::channel();
        *self.finished.lock().map_err(|_| "provider")? = Some(finished);
        let weak = Arc::downgrade(self);
        let lifetime = if request["mode"] == "prepare" {
            PREPARE_TIMEOUT + Duration::from_secs(10)
        } else {
            MAX_LIFETIME
        };
        std::thread::spawn(move || {
            if session {
                loop {
                    match watch.recv_timeout(Duration::from_millis(100)) {
                        Ok(()) | Err(mpsc::RecvTimeoutError::Disconnected) => return,
                        Err(mpsc::RecvTimeoutError::Timeout) => {}
                    }
                    let Some(capture) = weak.upgrade() else {
                        return;
                    };
                    let expired = capture.deadline.lock().ok().is_some_and(|deadline| {
                        if deadline.is_some_and(|limit| Instant::now() >= limit) {
                            capture.cancelled.store(true, Ordering::Release);
                            true
                        } else {
                            false
                        }
                    });
                    if expired {
                        capture.close();
                        return;
                    }
                }
            } else if watch.recv_timeout(lifetime).is_err() {
                if let Some(capture) = weak.upgrade() {
                    capture.close();
                }
            }
        });
        self.write(&format!("{request}\n"))
    }

    fn write(&self, text: &str) -> Result<(), String> {
        let mut input = self.input.lock().map_err(|_| "provider")?;
        let input = input.as_mut().ok_or("unavailable")?;
        input
            .write_all(text.as_bytes())
            .and_then(|_| input.flush())
            .map_err(|_| "provider".into())
    }

    fn extend_deadline(&self, duration: Duration) -> bool {
        if let Ok(mut deadline) = self.deadline.lock() {
            if self.cancelled.load(Ordering::Acquire) {
                return false;
            }
            *deadline = Some(Instant::now() + duration);
            return true;
        }
        false
    }

    fn running(&self) -> bool {
        !self.cancelled.load(Ordering::Acquire)
            && self
                .child
                .lock()
                .ok()
                .and_then(|mut child| child.as_mut().map(|c| matches!(c.try_wait(), Ok(None))))
                .unwrap_or(false)
    }

    fn exchange(&self, id: &str, request: Value, timeout: Duration) -> Reply {
        if !self.extend_deadline(timeout + Duration::from_secs(5)) {
            return Err("unavailable".into());
        }
        let mut request = request;
        request["id"] = json!(id);
        self.write(&format!("{request}\n"))?;
        let reply = self.receive(timeout)?;
        if reply["id"] != id {
            return Err("provider".into());
        }
        Ok(reply)
    }

    fn receive(&self, timeout: Duration) -> Reply {
        self.replies
            .lock()
            .map_err(|_| "provider")?
            .as_ref()
            .ok_or("unavailable")?
            .recv_timeout(timeout)
            .map_err(|_| "timeout")?
    }

    fn close(&self) {
        self.cancelled.store(true, Ordering::Release);
        if let Ok(mut input) = self.input.lock() {
            input.take();
        }
        if let Ok(mut child) = self.child.lock() {
            #[cfg(windows)]
            if let Ok(mut job) = self.job.lock() {
                if let Some(job) = job.take() {
                    job.terminate();
                }
            }
            if let Some(mut child) = child.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
        if let Ok(mut temporary) = self.temporary.lock() {
            temporary.take();
        }
        if let Ok(mut finished) = self.finished.lock() {
            if let Some(finished) = finished.take() {
                let _ = finished.send(());
            }
        }
    }

    // A terminal protocol reply precedes the one-file bootloader's extraction cleanup.
    fn finish(&self) {
        if let Ok(mut finished) = self.finished.lock() {
            if let Some(finished) = finished.take() {
                let _ = finished.send(());
            }
        }
        if let Ok(mut input) = self.input.lock() {
            input.take();
        }
        let deadline = Instant::now() + EXIT_TIMEOUT;
        loop {
            if self.cancelled.load(Ordering::Acquire) {
                return;
            }
            let exited = if let Ok(mut owned) = self.child.lock() {
                match owned.as_mut().map(Child::try_wait) {
                    None => true,
                    Some(Ok(Some(_))) => {
                        owned.take();
                        true
                    }
                    _ => false,
                }
            } else {
                false
            };
            if exited {
                #[cfg(windows)]
                if let Ok(mut job) = self.job.lock() {
                    job.take();
                }
                if let Ok(mut temporary) = self.temporary.lock() {
                    temporary.take();
                }
                return;
            }
            if Instant::now() >= deadline {
                self.close();
                return;
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    // Updating drains an idle worker by EOF; it never uses capture cancellation or kill timers.
    fn finish_for_update(&self) -> Result<(), String> {
        {
            let mut deadline = self.deadline.lock().map_err(|_| "Dictation state is unavailable")?;
            if self.cancelled.load(Ordering::Acquire) {
                return Err("The idle dictation worker is already closing".into());
            }
            *deadline = None;
        }
        if let Some(done) = self.finished.lock().map_err(|_| "Dictation state is unavailable")?.take() {
            let _ = done.send(());
        }
        self.input.lock().map_err(|_| "Dictation input is unavailable")?.take();
        let mut owned = self.child.lock().map_err(|_| "The owned dictation handle is unavailable")?;
        if let Some(child) = owned.as_mut() {
            let status = child.wait().map_err(|_| "Dictation exit could not be verified")?;
            if !status.success() {
                return Err("The idle dictation worker did not close normally".into());
            }
        }
        #[cfg(windows)]
        {
            let mut job = self.job.lock().map_err(|_| "The owned dictation job is unavailable")?;
            if let Some(job) = job.as_ref() { job.wait_empty()?; }
            job.take();
        }
        owned.take();
        self.temporary.lock().map_err(|_| "Dictation storage is unavailable")?.take();
        Ok(())
    }
}

impl Drop for Capture {
    fn drop(&mut self) {
        self.close();
    }
}

#[derive(Default)]
struct Registry {
    closed: bool,
    updating: bool,
    active: Option<(String, Arc<Capture>)>,
    warm: Option<Arc<Capture>>,
    cancelled: VecDeque<String>,
}

#[derive(Default)]
pub(crate) struct Dictation(Mutex<Registry>, PathBuf);

// Only fixed entry points and cache paths enter argv; recording options travel over bounded stdin.
fn command(cache: &Path) -> Result<Command, String> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let adjacent = std::env::current_exe()
            .map_err(|_| "unavailable")?
            .parent()
            .ok_or("unavailable")?
            .join("sylvia-gm-owner.exe");
        let mut command = if let Some(executable) = std::env::var_os("SYLVIA_GM_OWNER_EXECUTABLE") {
            let mut command = Command::new(executable);
            command.arg("--dictation-worker");
            command
        } else if cfg!(debug_assertions) && !adjacent.is_file() {
            let python = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .ok_or("unavailable")?
                .join(".venv/Scripts/python.exe");
            let mut command = Command::new(python);
            command.args(["-m", "sylvia_gm.local_dictation_process"]);
            command
        } else {
            let mut command = Command::new(adjacent);
            command.arg("--dictation-worker");
            command
        };
        command.arg(cache).env_clear();
        for key in [
            "SystemRoot",
            "WINDIR",
            "PATH",
            "TEMP",
            "TMP",
            "LOCALAPPDATA",
        ] {
            if let Some(value) = std::env::var_os(key) {
                command.env(key, value);
            }
        }
        command
            .env("OMP_NUM_THREADS", "2")
            .env("MKL_NUM_THREADS", "2")
            .env("TOKENIZERS_PARALLELISM", "false")
            .env("HF_HUB_OFFLINE", "1")
            .env("HF_HUB_DISABLE_TELEMETRY", "1");
        command.creation_flags(0x0800_0000);
        Ok(command)
    }
    #[cfg(not(windows))]
    {
        Err("unavailable".into())
    }
}

fn valid_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 64 && id.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-')
}

fn checked(reply: Value) -> Reply {
    if let Some(error) = reply.get("error").and_then(Value::as_str) {
        return Err(match error {
            "permission" | "device" | "empty" | "timeout" | "unavailable" | "runtime" | "model"
            | "download" | "storage" | "language" | "busy" => error,
            _ => "provider",
        }
        .into());
    }
    Ok(reply)
}

fn idle_reply(reply: &Reply) -> bool {
    reply.as_ref().is_ok_and(|value| {
        value["idle"] == true
            && match value.get("error").and_then(Value::as_str) {
                None | Some("empty" | "device" | "permission" | "model" | "language") => true,
                _ => false,
            }
    })
}

impl Dictation {
    pub(crate) fn new(cache: PathBuf) -> Self {
        Self(Mutex::new(Registry::default()), cache)
    }

    fn capture(&self) -> Arc<Capture> {
        let mut capture = Capture::default();
        capture.temporary_parent = self
            .1
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .map(|p| p.join("worker-temp"));
        Arc::new(capture)
    }

    #[cfg(test)]
    pub(crate) fn capabilities(&self) -> Reply {
        self.capabilities_for(None)
    }

    pub(crate) fn capabilities_for(&self, attempt: Option<&str>) -> Reply {
        self.capabilities_with(attempt, command(&self.1)?)
    }

    fn capabilities_with(&self, attempt: Option<&str>, command: Command) -> Reply {
        let id = attempt
            .map(str::to_owned)
            .unwrap_or_else(|| format!("metadata-{:032x}", rand::random::<u128>()));
        let capture = self.register_runtime(&id)?;
        let reply = self
            .launch_runtime(&capture, command)
            .and_then(|_| capture.exchange(&id, json!({"mode":"capabilities"}), START_TIMEOUT));
        let reusable = idle_reply(&reply);
        self.retire(&id, &capture, reusable);
        reply.and_then(checked)
    }

    pub(crate) fn start(&self, id: &str, engine: &str, device: &str) -> Result<(), String> {
        self.start_selected_with(id, engine, device, command(&self.1)?)
    }

    fn register(&self, id: &str) -> Result<Arc<Capture>, String> {
        if !valid_id(id) {
            return Err("unavailable".into());
        }
        let capture = self.capture();
        {
            let mut registry = self.0.lock().map_err(|_| "provider")?;
            if registry.closed || registry.updating {
                return Err("unavailable".into());
            }
            if registry
                .active
                .as_ref()
                .is_some_and(|(_, capture)| capture.cancelled.load(Ordering::Acquire))
            {
                registry.active = None;
            }
            if registry.active.is_some() || registry.cancelled.iter().any(|entry| entry == id) {
                return Err("unavailable".into());
            }
            registry.active = Some((id.to_owned(), Arc::clone(&capture)));
        }
        Ok(capture)
    }

    fn register_runtime(&self, id: &str) -> Result<Arc<Capture>, String> {
        if !valid_id(id) {
            return Err("unavailable".into());
        }
        let mut registry = self.0.lock().map_err(|_| "provider")?;
        if registry
            .active
            .as_ref()
            .is_some_and(|(_, c)| c.cancelled.load(Ordering::Acquire))
        {
            registry.active = None;
        }
        if registry.closed || registry.updating
            || registry.active.is_some()
            || registry.cancelled.iter().any(|s| s == id)
        {
            return Err("unavailable".into());
        }
        let mut capture = registry
            .warm
            .take()
            .filter(|c| c.running())
            .unwrap_or_else(|| self.capture());
        capture.stopping.store(false, Ordering::Release);
        if !capture.extend_deadline(START_TIMEOUT + Duration::from_secs(5)) {
            capture = self.capture();
            capture.extend_deadline(START_TIMEOUT + Duration::from_secs(5));
        }
        registry.active = Some((id.to_owned(), Arc::clone(&capture)));
        Ok(capture)
    }

    fn launch_runtime(&self, capture: &Arc<Capture>, command: Command) -> Result<(), String> {
        if capture.cancelled.load(Ordering::Acquire) {
            return Err("unavailable".into());
        }
        if capture.session.load(Ordering::Acquire) {
            return Ok(());
        }
        capture.launch(command, json!({"mode":"session"}))?;
        let ready = capture.receive(START_TIMEOUT)?;
        if ready["status"] != "ready" || ready["protocol"] != 1 {
            return Err("provider".into());
        }
        Ok(())
    }

    #[cfg(test)]
    fn start_with(&self, id: &str, engine: &str, command: Command) -> Result<(), String> {
        self.start_one_shot_with(id, engine, "default", command)
    }

    fn start_selected_with(
        &self,
        id: &str,
        engine: &str,
        device: &str,
        command: Command,
    ) -> Result<(), String> {
        if engine.len() > 128 || device.len() > 128 {
            return Err("unavailable".into());
        }
        let capture = self.register_runtime(id)?;
        let reply = self.launch_runtime(&capture, command).and_then(|_| {
            capture.exchange(
                id,
                json!({"mode":"start", "engine":engine, "device":device}),
                START_TIMEOUT,
            )
        });
        let reusable = idle_reply(&reply);
        let result = reply.and_then(checked).and_then(|reply| {
            if reply["status"] == "recording"
                && reply["idle"] == false
                && !capture.cancelled.load(Ordering::Acquire)
            {
                capture.extend_deadline(Duration::from_secs(130));
                Ok(())
            } else {
                Err("unavailable".into())
            }
        });
        if result.is_err() {
            self.retire(id, &capture, reusable);
        }
        result
    }

    #[cfg(test)]
    fn start_one_shot_with(
        &self,
        id: &str,
        engine: &str,
        device: &str,
        command: Command,
    ) -> Result<(), String> {
        if engine.len() > 128 || device.len() > 128 {
            return Err("unavailable".into());
        }
        let capture = self.register(id)?;
        let reply = capture
            .launch(
                command,
                json!({"mode":"start", "engine":engine, "device":device}),
            )
            .and_then(|_| capture.receive(START_TIMEOUT));
        let terminal_error = reply
            .as_ref()
            .is_ok_and(|value| value.get("error").is_some());
        let result = reply.and_then(checked).and_then(|reply| {
            if reply["status"] == "recording" && !capture.cancelled.load(Ordering::Acquire) {
                Ok(())
            } else {
                Err("unavailable".into())
            }
        });
        if result.is_err() {
            self.retire(id, &capture, terminal_error);
        }
        result
    }

    pub(crate) fn prepare(&self, id: &str) -> Reply {
        self.prepare_with(id, command(&self.1)?)
    }

    fn prepare_with(&self, id: &str, command: Command) -> Reply {
        let capture = self.register(id)?;
        let reply = capture
            .launch(command, json!({"mode":"prepare"}))
            .and_then(|_| capture.receive(PREPARE_TIMEOUT));
        let completed = reply.is_ok();
        let result = reply.and_then(checked).and_then(|reply| {
            if reply["status"] == "ready" {
                Ok(reply)
            } else {
                Err("provider".into())
            }
        });
        self.retire(id, &capture, completed);
        if capture.cancelled.load(Ordering::Acquire) {
            Err("unavailable".into())
        } else {
            result
        }
    }

    pub(crate) fn stop(&self, id: &str) -> Result<String, String> {
        let capture = self
            .0
            .lock()
            .map_err(|_| "provider")?
            .active
            .as_ref()
            .filter(|(active, _)| active == id)
            .map(|(_, capture)| Arc::clone(capture))
            .ok_or("unavailable")?;
        if capture.stopping.swap(true, Ordering::AcqRel) {
            return Err("unavailable".into());
        }
        let session = capture.session.load(Ordering::Acquire);
        let reply = if session {
            capture.exchange(id, json!({"mode":"stop"}), STOP_TIMEOUT)
        } else {
            capture
                .write("stop\n")
                .and_then(|_| capture.receive(STOP_TIMEOUT))
        };
        let completed = if session {
            idle_reply(&reply)
        } else {
            reply.is_ok()
        };
        let result = reply.and_then(checked).and_then(|reply| {
            if session && reply["idle"] != true {
                return Err("provider".into());
            }
            reply
                .get("text")
                .and_then(Value::as_str)
                .filter(|text| text.len() <= 128_000)
                .map(str::to_owned)
                .ok_or("provider".into())
        });
        self.retire(id, &capture, completed);
        let cancelled = capture.cancelled.load(Ordering::Acquire);
        if cancelled {
            Err("unavailable".into())
        } else {
            result
        }
    }

    fn retire(&self, id: &str, capture: &Arc<Capture>, completed: bool) {
        if capture.session.load(Ordering::Acquire) {
            if let Ok(mut registry) = self.0.lock() {
                if registry
                    .active
                    .as_ref()
                    .is_some_and(|(active, current)| active == id && Arc::ptr_eq(current, capture))
                {
                    registry.active = None;
                    if completed && !registry.closed && !registry.updating && !capture.cancelled.load(Ordering::Acquire) {
                        capture.extend_deadline(IDLE_TIMEOUT);
                        registry.warm = Some(Arc::clone(capture));
                        return;
                    }
                }
            }
            capture.close();
            return;
        }
        if completed {
            capture.finish();
        } else {
            capture.close();
        }
        if let Ok(mut registry) = self.0.lock() {
            if registry
                .active
                .as_ref()
                .is_some_and(|(active, current)| active == id && Arc::ptr_eq(current, capture))
            {
                registry.active = None;
            }
        }
    }

    pub(crate) fn cancel(&self, id: &str) {
        if !valid_id(id) {
            return;
        }
        let capture = self.0.lock().ok().and_then(|mut registry| {
            if !registry.cancelled.iter().any(|entry| entry == id) {
                registry.cancelled.push_back(id.to_owned());
                if registry.cancelled.len() > 64 {
                    registry.cancelled.pop_front();
                }
            }
            if registry
                .active
                .as_ref()
                .is_some_and(|(active, _)| active == id)
            {
                registry.active.take().map(|(_, capture)| capture)
            } else {
                None
            }
        });
        if let Some(capture) = capture {
            capture.close();
        }
    }

    pub(crate) fn park_for_update(&self) -> Result<(), String> {
        let warm = {
            let mut registry = self.0.lock().map_err(|_| "Dictation state is unavailable")?;
            if registry.closed || registry.updating || registry.active.is_some() {
                return Err("Finish dictation before updating Sylvia".into());
            }
            registry.updating = true;
            registry.warm.take()
        };
        if let Some(warm) = warm {
            if let Err(error) = warm.finish_for_update() {
                let mut registry = self.0.lock().unwrap_or_else(|error| error.into_inner());
                if !registry.closed { registry.warm = Some(warm); }
                registry.updating = false;
                return Err(error);
            }
        }
        Ok(())
    }

    pub(crate) fn resume_after_update_abort(&self) {
        if let Ok(mut registry) = self.0.lock() { registry.updating = false; }
    }

    /// Permanently refuse queued starts before releasing the current capture on shutdown.
    pub(crate) fn close(&self) {
        let captures = self.0.lock().ok().map(|mut registry| {
            registry.closed = true;
            (registry.active.take(), registry.warm.take())
        });
        if let Some((active, warm)) = captures {
            if let Some((_, capture)) = active {
                capture.close();
            }
            if let Some(capture) = warm {
                capture.finish();
            }
        }
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    fn fixture(body: &str) -> Command {
        use std::os::windows::process::CommandExt;
        let mut command = Command::new("powershell.exe");
        command.creation_flags(0x0800_0000);
        command.args(["-NoProfile", "-NonInteractive", "-Command", body]);
        command
    }

    fn session_fixture() -> Command {
        fixture(
            r#"$hello=[Console]::ReadLine()|ConvertFrom-Json; if($hello.mode -ne 'session'){[Console]::WriteLine('{"error":"unavailable"}');exit}; [Console]::WriteLine('{"status":"ready","protocol":1}'); while($line=[Console]::ReadLine()){ $q=$line|ConvertFrom-Json; $r=@{id=$q.id;idle=$true}; if($q.mode -eq 'start'){$r.status='recording';$r.idle=$false}else{$r.text='controlled words'}; [Console]::WriteLine(($r|ConvertTo-Json -Compress)) }"#,
        )
    }

    #[test]
    fn successful_attempt_cleanup_keeps_one_warm_runtime_for_repeat_start() {
        let dictation = Dictation::default();
        dictation
            .start_selected_with("one", "en", "default", session_fixture())
            .unwrap();
        let first = dictation
            .0
            .lock()
            .unwrap()
            .active
            .as_ref()
            .unwrap()
            .1
            .clone();
        let pid = first.child.lock().unwrap().as_ref().unwrap().id();
        assert_eq!(dictation.stop("one").unwrap(), "controlled words");
        dictation.cancel("one"); // frontend release() runs even after a successful Stop
        dictation
            .start_selected_with("two", "en", "default", session_fixture())
            .unwrap();
        assert_eq!(
            dictation
                .0
                .lock()
                .unwrap()
                .active
                .as_ref()
                .unwrap()
                .1
                .child
                .lock()
                .unwrap()
                .as_ref()
                .unwrap()
                .id(),
            pid
        );
        assert_eq!(dictation.stop("two").unwrap(), "controlled words");
        dictation.close();
        assert!(first.child.lock().unwrap().is_none());
    }

    fn blocked_session(stage: &str) -> Command {
        fixture(&r#"$null=[Console]::ReadLine();[Console]::WriteLine('{"status":"ready","protocol":1}');while($line=[Console]::ReadLine()){$q=$line|ConvertFrom-Json;if($q.mode -eq 'STAGE'){$null=[Console]::ReadLine();exit};$r=@{id=$q.id;status='recording';idle=$false};[Console]::WriteLine(($r|ConvertTo-Json -Compress))}"#.replace("STAGE", stage))
    }

    #[test]
    fn loading_preflight_is_cancelled_by_its_actual_attempt_id() {
        let dictation = Arc::new(Dictation::default());
        let worker = Arc::clone(&dictation);
        let pending = std::thread::spawn(move || {
            worker.capabilities_with(Some("loading"), blocked_session("capabilities"))
        });
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            let active = dictation
                .0
                .lock()
                .unwrap()
                .active
                .as_ref()
                .map(|(id, c)| (id.clone(), c.clone()));
            if let Some((id, capture)) = active {
                assert_eq!(id, "loading");
                if capture.running() {
                    break;
                }
            }
            assert!(Instant::now() < deadline);
            std::thread::sleep(Duration::from_millis(10));
        }
        dictation.cancel("loading");
        assert!(pending.join().unwrap().is_err());
        assert!(dictation.0.lock().unwrap().warm.is_none());
        dictation
            .start_selected_with("fresh", "en", "default", session_fixture())
            .unwrap();
        dictation.close();
    }

    #[test]
    fn session_cancel_while_loading_or_decoding_closes_job_and_allows_restart() {
        for stage in ["start", "stop"] {
            let dictation = Arc::new(Dictation::default());
            if stage == "stop" {
                dictation
                    .start_selected_with("blocked", "en", "default", blocked_session(stage))
                    .unwrap();
            }
            let worker = Arc::clone(&dictation);
            let pending = std::thread::spawn(move || {
                if stage == "start" {
                    worker
                        .start_selected_with("blocked", "en", "default", blocked_session(stage))
                        .map(|_| String::new())
                } else {
                    worker.stop("blocked")
                }
            });
            let deadline = Instant::now() + Duration::from_secs(5);
            let capture = loop {
                let active = dictation
                    .0
                    .lock()
                    .unwrap()
                    .active
                    .as_ref()
                    .map(|(_, c)| c.clone());
                if let Some(c) = active {
                    if c.running() && (stage == "start" || c.stopping.load(Ordering::Acquire)) {
                        break c;
                    }
                }
                assert!(Instant::now() < deadline);
                std::thread::sleep(Duration::from_millis(10));
            };
            dictation.cancel("blocked");
            assert!(pending.join().unwrap().is_err());
            assert!(capture.child.lock().unwrap().is_none());
            assert!(capture.job.lock().unwrap().is_none());
            dictation
                .start_selected_with("retry", "en", "default", session_fixture())
                .unwrap();
            dictation.close();
        }
    }

    #[test]
    fn session_recording_cancel_and_idle_expiry_do_not_leave_a_warm_capture() {
        let dictation = Dictation::default();
        dictation
            .start_selected_with("cancel", "en", "default", session_fixture())
            .unwrap();
        let cancelled = dictation
            .0
            .lock()
            .unwrap()
            .active
            .as_ref()
            .unwrap()
            .1
            .clone();
        dictation.cancel("cancel");
        assert!(!cancelled.running());
        dictation
            .start_selected_with("again", "en", "default", session_fixture())
            .unwrap();
        assert_eq!(dictation.stop("again").unwrap(), "controlled words");
        let warm = dictation.0.lock().unwrap().warm.as_ref().unwrap().clone();
        warm.extend_deadline(Duration::from_millis(10));
        let deadline = Instant::now() + Duration::from_secs(3);
        while warm.running() {
            assert!(Instant::now() < deadline);
            std::thread::sleep(Duration::from_millis(10));
        }
        dictation
            .start_selected_with("after-expiry", "en", "default", session_fixture())
            .unwrap();
        dictation.close();
    }

    #[test]
    fn dead_idle_runtime_is_replaced_and_stale_reply_ids_are_refused() {
        let dictation = Dictation::default();
        dictation
            .start_selected_with("first", "en", "default", session_fixture())
            .unwrap();
        assert_eq!(dictation.stop("first").unwrap(), "controlled words");
        let warm = dictation.0.lock().unwrap().warm.as_ref().unwrap().clone();
        let pid = warm.child.lock().unwrap().as_ref().unwrap().id();
        warm.job.lock().unwrap().as_ref().unwrap().terminate();
        let deadline = Instant::now() + Duration::from_secs(3);
        while warm.running() {
            assert!(Instant::now() < deadline);
            std::thread::sleep(Duration::from_millis(10));
        }
        dictation
            .start_selected_with("replacement", "en", "default", session_fixture())
            .unwrap();
        assert_ne!(
            dictation
                .0
                .lock()
                .unwrap()
                .active
                .as_ref()
                .unwrap()
                .1
                .child
                .lock()
                .unwrap()
                .as_ref()
                .unwrap()
                .id(),
            pid
        );
        dictation.close();
        let dictation = Dictation::default();
        let wrong = fixture(
            r#"$null=[Console]::ReadLine();[Console]::WriteLine('{"status":"ready","protocol":1}');$null=[Console]::ReadLine();[Console]::WriteLine('{"id":"old","status":"recording","idle":false}')"#,
        );
        assert_eq!(
            dictation
                .start_selected_with("new", "en", "default", wrong)
                .unwrap_err(),
            "provider"
        );
        assert!(dictation.0.lock().unwrap().active.is_none());
        assert!(dictation.0.lock().unwrap().warm.is_none());
    }

    #[test]
    fn explicit_stop_returns_one_result_and_releases_the_capture() {
        let dictation = Dictation::default();
        dictation.start_with("one", "test", fixture(r#"$null=[Console]::ReadLine(); [Console]::WriteLine('{"status":"recording"}'); $null=[Console]::ReadLine(); [Console]::WriteLine('{"text":"controlled words"}')"#)).unwrap();
        assert_eq!(dictation.stop("one").unwrap(), "controlled words");
        assert_eq!(dictation.stop("one").unwrap_err(), "unavailable");
    }

    #[test]
    fn cancellation_before_start_cannot_open_a_late_capture() {
        let dictation = Dictation::default();
        dictation.cancel("cancelled");
        assert_eq!(
            dictation
                .start_with("cancelled", "test", fixture("exit 99"))
                .unwrap_err(),
            "unavailable"
        );
    }

    #[test]
    fn closing_before_a_queued_start_registers_prevents_capture() {
        let dictation = Arc::new(Dictation::default());
        let worker = Arc::clone(&dictation);
        let (release, queued) = mpsc::channel();
        let pending = std::thread::spawn(move || {
            queued.recv().unwrap();
            worker.start_with("late", "test", fixture(r#"$null=[Console]::ReadLine(); [Console]::WriteLine('{"status":"recording"}'); $null=[Console]::ReadLine()"#))
        });
        dictation.close();
        release.send(()).unwrap();
        assert_eq!(pending.join().unwrap().unwrap_err(), "unavailable");
    }

    #[test]
    fn shutdown_also_refuses_late_model_preparation() {
        let dictation = Dictation::default();
        dictation.close();
        assert_eq!(
            dictation
                .prepare_with("late-model", fixture("exit 99"))
                .unwrap_err(),
            "unavailable"
        );
    }

    #[test]
    fn cancelling_model_preparation_releases_ownership_for_retry() {
        let dictation = Arc::new(Dictation::default());
        let worker = Arc::clone(&dictation);
        let pending = std::thread::spawn(move || {
            worker.prepare_with(
                "model",
                fixture("$null=[Console]::ReadLine(); $null=[Console]::ReadLine()"),
            )
        });
        while dictation.0.lock().unwrap().active.is_none() {
            std::thread::yield_now();
        }
        dictation.cancel("model");
        assert!(pending.join().unwrap().is_err());
        assert_eq!(
            dictation
                .prepare_with(
                    "model-retry",
                    fixture(
                        r#"$null=[Console]::ReadLine(); [Console]::WriteLine('{"status":"ready"}')"#
                    )
                )
                .unwrap()["status"],
            "ready"
        );
    }

    #[test]
    fn cancellation_closes_packaged_worker_descendants() {
        use windows_sys::Win32::Foundation::{CloseHandle, WAIT_OBJECT_0};
        use windows_sys::Win32::System::Threading::{OpenProcess, WaitForSingleObject};
        let capture = Arc::new(Capture::default());
        capture.launch(fixture(r#"$null=[Console]::ReadLine(); $p=Start-Process powershell.exe -WindowStyle Hidden -PassThru -ArgumentList '-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 30'; [Console]::WriteLine(('{"pid":'+$p.Id+'}')); $null=[Console]::ReadLine()"#), json!({})).unwrap();
        let pid = capture.receive(START_TIMEOUT).unwrap()["pid"]
            .as_u64()
            .unwrap() as u32;
        unsafe {
            let process = OpenProcess(0x00100000, 0, pid);
            assert!(!process.is_null());
            capture.close();
            let result = WaitForSingleObject(process, 2000);
            CloseHandle(process);
            assert_eq!(result, WAIT_OBJECT_0);
        }
    }

    #[test]
    fn cancellation_owns_descendants_created_before_the_first_request() {
        use windows_sys::Win32::Foundation::{CloseHandle, WAIT_OBJECT_0};
        use windows_sys::Win32::System::Threading::{
            OpenProcess, TerminateProcess, WaitForSingleObject,
        };
        let capture = Arc::new(Capture::default());
        let mut command = fixture(
            r#"$p=Start-Process powershell.exe -WindowStyle Hidden -PassThru -ArgumentList '-NoProfile','-NonInteractive','-Command','Start-Sleep -Seconds 30'; [Console]::WriteLine(('{"pid":'+$p.Id+'}')); $null=[Console]::ReadLine(); $null=[Console]::ReadLine()"#,
        );
        command.env("SYLVIA_DICTATION_TEST_DELAY_ASSIGNMENT", "1");
        capture.launch(command, json!({})).unwrap();
        let pid = capture.receive(START_TIMEOUT).unwrap()["pid"]
            .as_u64()
            .unwrap() as u32;
        unsafe {
            let process = OpenProcess(0x00100001, 0, pid);
            assert!(!process.is_null());
            capture.close();
            let result = WaitForSingleObject(process, 2000);
            // The red regression must not leave its deliberately escaped fixture running.
            if result != WAIT_OBJECT_0 {
                TerminateProcess(process, 1);
                WaitForSingleObject(process, 2000);
            }
            CloseHandle(process);
            assert_eq!(result, WAIT_OBJECT_0);
        }
    }

    #[test]
    fn a_completed_reply_allows_process_cleanup_before_forced_closure() {
        let directory = std::env::temp_dir().join(format!(
            "sylvia-dictation-cleanup-{}",
            rand::random::<u128>()
        ));
        std::fs::create_dir(&directory).unwrap();
        let marker = directory.join("clean-exit.txt");
        let body = format!(
            r#"$null=[Console]::ReadLine(); [Console]::WriteLine('{{"status":"ready"}}'); Start-Sleep -Milliseconds 300; [IO.File]::WriteAllText('{}','cleaned')"#,
            marker.display()
        );
        let dictation = Dictation::default();
        assert_eq!(
            dictation.prepare_with("cleanup", fixture(&body)).unwrap()["status"],
            "ready"
        );
        let cleaned = marker.exists();
        std::fs::remove_dir_all(&directory).unwrap();
        assert!(
            cleaned,
            "normal completion killed the worker before its cleanup"
        );
    }

    #[test]
    fn invocation_temporary_files_are_removed_after_completion_and_cancellation() {
        for completed in [true, false] {
            let root = std::env::temp_dir().join(format!(
                "sylvia-worker-temp-{:032x}",
                rand::random::<u128>()
            ));
            std::fs::create_dir(&root).unwrap();
            let mut capture = Capture::default();
            capture.temporary_parent = Some(root.clone());
            let capture = Arc::new(capture);
            let mut script = r#"$null=[Console]::ReadLine(); [IO.File]::WriteAllText(([IO.Path]::Combine([IO.Path]::GetTempPath(),'owned-extraction.tmp')),'fixture'); [Console]::WriteLine('{"status":"ready"}');"#.to_owned();
            if !completed {
                script.push_str("$null=[Console]::ReadLine();");
            }
            capture.launch(fixture(&script), json!({})).unwrap();
            assert_eq!(capture.receive(START_TIMEOUT).unwrap()["status"], "ready");
            if completed {
                capture.finish();
            } else {
                capture.close();
            }
            assert_eq!(std::fs::read_dir(&root).unwrap().count(), 0);
            std::fs::remove_dir(&root).unwrap();
        }
    }

    #[test]
    fn failed_assignment_never_runs_the_child_and_removes_only_owned_temporary_files() {
        let directory = std::env::temp_dir().join(format!(
            "sylvia-start-failure-{:032x}",
            rand::random::<u128>()
        ));
        std::fs::create_dir(&directory).unwrap();
        let marker = directory.join("must-not-run.txt");
        let mut capture = Capture::default();
        capture.temporary_parent = Some(directory.join("worker-temp"));
        let capture = Arc::new(capture);
        let mut worker = fixture(&format!(
            "[IO.File]::WriteAllText('{}','ran')",
            marker.display()
        ));
        worker.env("SYLVIA_DICTATION_TEST_FAIL_ASSIGNMENT", "1");
        assert_eq!(
            capture.launch(worker, json!({})).unwrap_err(),
            "unavailable"
        );
        assert!(!marker.exists());
        assert_eq!(
            std::fs::read_dir(directory.join("worker-temp"))
                .unwrap()
                .count(),
            0
        );
        std::fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn cancellation_releases_a_pending_start_and_allows_another_capture() {
        let dictation = Arc::new(Dictation::default());
        let worker = Arc::clone(&dictation);
        let pending = std::thread::spawn(move || {
            worker.start_with(
                "pending",
                "test",
                fixture("$null=[Console]::ReadLine(); $null=[Console]::ReadLine()"),
            )
        });
        while dictation.0.lock().unwrap().active.is_none() {
            std::thread::yield_now();
        }
        dictation.cancel("pending");
        assert!(pending.join().unwrap().is_err());
        dictation.start_with("next", "test", fixture(r#"$null=[Console]::ReadLine(); [Console]::WriteLine('{"status":"recording"}'); $null=[Console]::ReadLine()"#)).unwrap();
        dictation.close();
    }

    #[test]
    fn failure_is_redacted_and_does_not_block_another_start() {
        let dictation = Dictation::default();
        assert_eq!(
            dictation
                .start_with(
                    "denied",
                    "test",
                    fixture(r#"[Console]::WriteLine('{"error":"permission"}')"#)
                )
                .unwrap_err(),
            "permission"
        );
        assert_eq!(
            checked(json!({"error":"private details"})).unwrap_err(),
            "provider"
        );
        assert!(dictation.0.lock().unwrap().active.is_none());
    }

    #[test]
    fn cancelling_transcription_releases_the_wait_without_returning_text() {
        let dictation = Arc::new(Dictation::default());
        dictation.start_with("recorded", "test", fixture(r#"$null=[Console]::ReadLine(); [Console]::WriteLine('{"status":"recording"}'); $null=[Console]::ReadLine(); $null=[Console]::ReadLine()"#)).unwrap();
        let worker = Arc::clone(&dictation);
        let pending = std::thread::spawn(move || worker.stop("recorded"));
        while !dictation
            .0
            .lock()
            .unwrap()
            .active
            .as_ref()
            .unwrap()
            .1
            .stopping
            .load(Ordering::Acquire)
        {
            std::thread::yield_now();
        }
        dictation.cancel("recorded");
        assert_eq!(pending.join().unwrap().unwrap_err(), "unavailable");
    }

    #[test]
    fn a_second_capture_is_refused_and_cancelling_an_old_id_keeps_the_current_one() {
        let dictation = Dictation::default();
        dictation.start_with("current", "test", fixture(r#"$null=[Console]::ReadLine(); [Console]::WriteLine('{"status":"recording"}'); $null=[Console]::ReadLine(); [Console]::WriteLine('{"text":"current text"}')"#)).unwrap();
        assert_eq!(
            dictation
                .start_with("other", "test", fixture("exit 99"))
                .unwrap_err(),
            "unavailable"
        );
        dictation.cancel("old");
        assert_eq!(dictation.stop("current").unwrap(), "current text");
    }

    #[test]
    fn a_silent_or_malformed_helper_cannot_wait_forever_or_expose_its_output() {
        let capture = Arc::new(Capture::default());
        capture
            .launch(
                fixture("$null=[Console]::ReadLine(); $null=[Console]::ReadLine()"),
                json!({}),
            )
            .unwrap();
        assert_eq!(
            capture.receive(Duration::from_millis(20)).unwrap_err(),
            "timeout"
        );
        capture.close();
        let dictation = Dictation::default();
        assert_eq!(
            dictation
                .start_with(
                    "broken",
                    "test",
                    fixture("[Console]::WriteLine('private invalid output')")
                )
                .unwrap_err(),
            "provider"
        );
    }

    #[test]
    #[ignore = "requires the adjacent built owner and an existing controlled model; no download or capture"]
    fn packaged_warm_runtime_repeats_without_capture_and_closes_owned_processes() {
        let root = PathBuf::from(std::env::var_os("SYLVIA_DICTATION_PROOF_ROOT").unwrap());
        let source = PathBuf::from(std::env::var_os("SYLVIA_DICTATION_PROOF_MODEL").unwrap());
        assert!(!root.exists());
        let cache = root.join("model-cache/base");
        std::fs::create_dir_all(&cache).unwrap();
        for name in [
            "config.json",
            "model.bin",
            "tokenizer.json",
            "vocabulary.txt",
        ] {
            std::fs::copy(source.join(name), cache.join(name)).unwrap();
        }
        let dictation = Arc::new(Dictation::new(cache.clone()));
        let mut records = Vec::new();
        let mut first_pid = None;
        for i in 0..5 {
            let id = format!("fixture-{i}");
            let started = Instant::now();
            assert_eq!(
                dictation.capabilities_for(Some(&id)).unwrap()["ready"],
                true
            );
            let preflight = started.elapsed().as_secs_f64();
            assert_eq!(
                dictation
                    .start(&id, "en", "controlled-no-input")
                    .unwrap_err(),
                "device"
            );
            let elapsed = started.elapsed().as_secs_f64();
            dictation.cancel(&id);
            let warm = dictation.0.lock().unwrap().warm.as_ref().unwrap().clone();
            let pid = warm.child.lock().unwrap().as_ref().unwrap().id();
            if let Some(expected) = first_pid {
                assert_eq!(pid, expected);
            } else {
                first_pid = Some(pid);
            }
            assert!(warm.running());
            records.push(json!({"iteration":i,"capabilities_seconds":preflight,"preflight_plus_start_seconds":elapsed,"worker_pid":pid}));
            if i > 0 {
                assert!(elapsed < 1.5, "repeat startup rebuilt its runtime");
            }
        }
        let warm = dictation.0.lock().unwrap().warm.as_ref().unwrap().clone();
        dictation.close();
        assert!(warm.child.lock().unwrap().is_none());
        assert!(warm.job.lock().unwrap().is_none());
        assert!(
            !warm.cancelled.load(Ordering::Acquire),
            "idle exit needed forced termination"
        );
        assert!(std::fs::read_dir(root.join("model-cache/worker-temp"))
            .unwrap()
            .next()
            .is_none());
        std::fs::write(root.join("packaged-warm-timings.json"), serde_json::to_vec_pretty(&json!({"records":records,"capture":false,"download":false,"idle_exit_graceful":true,"retained_processes":0,"retained_extraction_files":0})).unwrap()).unwrap();
    }

    // Run from a disposable directory containing the exact packaged owner beside this test EXE.
    #[test]
    #[ignore = "requires the built owner and explicit pinned-model preparation; never records audio"]
    fn packaged_worker_repeats_complete_cancel_and_error_without_retained_processes_or_extraction()
    {
        use windows_sys::Win32::System::JobObjects::*;
        let root = PathBuf::from(
            std::env::var_os("SYLVIA_DICTATION_PROOF_ROOT").expect("owned fresh proof root"),
        );
        assert!(!root.exists());
        std::fs::create_dir_all(&root).unwrap();
        let cache = root.join("model-cache/base");
        let temporary = root.join("model-cache/worker-temp");
        let adjacent = std::env::current_exe()
            .unwrap()
            .parent()
            .unwrap()
            .join("sylvia-gm-owner.exe");
        assert!(adjacent.is_file());
        assert_eq!(command(&cache).unwrap().get_program(), adjacent.as_os_str());
        let dictation = Arc::new(Dictation::new(cache.clone()));
        let mut records = Vec::new();
        let check_empty = || {
            let entries: Vec<_> = std::fs::read_dir(&temporary).unwrap().collect();
            assert!(entries.is_empty(), "owned worker extraction remained");
        };
        for i in 0..3 {
            let capture = dictation.capture();
            capture
                .launch(command(&cache).unwrap(), json!({"mode":"capabilities"}))
                .unwrap();
            let reply = checked(capture.receive(START_TIMEOUT).unwrap()).unwrap();
            assert_eq!(reply["model"]["compute_type"], "int8");
            capture.finish();
            assert!(
                !capture.cancelled.load(Ordering::Acquire),
                "normal exit required forced termination"
            );
            assert!(capture.child.lock().unwrap().is_none());
            assert!(capture.job.lock().unwrap().is_none());
            check_empty();
            records.push(json!({"case":"capabilities", "iteration":i, "graceful":true,
                                "retained_process_handles":0,"temporary_files":0,"temporary_bytes":0}));
        }
        assert!(!cache.join("model.bin").exists());
        assert_eq!(
            dictation
                .start("missing-model", "auto", "default")
                .unwrap_err(),
            "model"
        );
        assert!(dictation.0.lock().unwrap().warm.is_some());
        dictation.close();
        check_empty();
        records.push(json!({"case":"missing-model error before capture","temporary_files":0,"temporary_bytes":0}));
        let dictation = Arc::new(Dictation::new(cache.clone()));
        assert_eq!(
            std::env::var("SYLVIA_DICTATION_PROOF_PREPARE").unwrap(),
            "1"
        );
        for i in 0..3 {
            let capture = dictation.capture();
            capture
                .launch(command(&cache).unwrap(), json!({"mode":"prepare"}))
                .unwrap();
            assert_eq!(
                checked(capture.receive(PREPARE_TIMEOUT).unwrap()).unwrap()["status"],
                "ready"
            );
            capture.finish();
            assert!(!capture.cancelled.load(Ordering::Acquire));
            assert!(capture.child.lock().unwrap().is_none());
            assert!(capture.job.lock().unwrap().is_none());
            check_empty();
            records.push(json!({"case":"prepare", "iteration":i,"graceful":true,
                                "retained_process_handles":0,"temporary_files":0,"temporary_bytes":0}));
        }
        for i in 0..3 {
            let worker = Arc::clone(&dictation);
            let id = format!("cancel-packaged-{i}");
            let worker_id = id.clone();
            let pending = std::thread::spawn(move || worker.prepare(&worker_id));
            let deadline = Instant::now() + START_TIMEOUT;
            let (capture, active) = loop {
                let capture = dictation
                    .0
                    .lock()
                    .unwrap()
                    .active
                    .as_ref()
                    .map(|(_, c)| Arc::clone(c));
                if let Some(capture) = capture {
                    let mut info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
                    let active = capture
                        .job
                        .lock()
                        .unwrap()
                        .as_ref()
                        .map(|job| unsafe {
                            assert_ne!(
                                QueryInformationJobObject(
                                    job.0 as _,
                                    JobObjectBasicAccountingInformation,
                                    &mut info as *mut _ as _,
                                    std::mem::size_of_val(&info) as u32,
                                    std::ptr::null_mut()
                                ),
                                0
                            );
                            info.ActiveProcesses
                        })
                        .unwrap_or(0);
                    if active >= 2 {
                        break (capture, active);
                    }
                }
                assert!(
                    Instant::now() < deadline && !pending.is_finished(),
                    "worker descendants were not observed"
                );
                std::thread::sleep(Duration::from_millis(1));
            };
            dictation.cancel(&id);
            assert!(pending.join().unwrap().is_err());
            assert!(capture.child.lock().unwrap().is_none());
            assert!(capture.job.lock().unwrap().is_none());
            check_empty();
            records.push(
                json!({"case":"cancel prepare with bootloader descendant", "iteration":i,
                                "observed_owned_processes":active,"retained_process_handles":0,
                                "temporary_files":0,"temporary_bytes":0}),
            );
        }
        assert_eq!(dictation.capabilities().unwrap()["ready"], true);
        dictation.close();
        check_empty();
        std::fs::write(
            root.join("packaged-controller-proof.json"),
            serde_json::to_vec_pretty(&json!({"cases":records,"microphone_opened":false,
                "audio_recorded_or_uploaded":false,"foreground_input":false,
                "worker_selection":"adjacent packaged owner; no executable override"}))
            .unwrap(),
        )
        .unwrap();
    }
}
