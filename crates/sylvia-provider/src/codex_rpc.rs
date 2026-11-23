//! `provider/_codex_rpc.py`: the private stdio connection to Sylvia's isolated Codex App Server
//! process. Lane 5.
//!
//! The child manages ChatGPT credentials. Sylvia never reads its credential files, inherits
//! another Codex profile, or forwards a server request to a host tool. Raw server errors and
//! standard error are never surfaced, because they may contain secrets.

use crate::runtime_process::{self, allowed_variables, set_variable, Spawn, TextLines};
use std::collections::BTreeSet;
use std::ffi::{OsStr, OsString};
use std::io::Write;
use std::path::{Component, Path, PathBuf};
use std::process::{Child, ChildStdin};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, Sender, SyncSender, TrySendError};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};
use sylvia_base::{Json, JsonMap};

/// `CodexConnectionError` and its subclass `CodexCancelledError`: privacy-safe failures for the
/// settings surface. `Io` is an `OSError` Python lets escape as it is.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CodexError {
    Connection(String),
    Cancelled(String),
    Io(String),
}

impl CodexError {
    /// `str(error)`.
    pub fn message(&self) -> &str {
        match self {
            Self::Connection(message) | Self::Cancelled(message) | Self::Io(message) => message,
        }
    }

    /// Python's class name.
    pub fn class_name(&self) -> &'static str {
        match self {
            Self::Connection(_) => "CodexConnectionError",
            Self::Cancelled(_) => "CodexCancelledError",
            Self::Io(_) => "OSError",
        }
    }

    /// `isinstance(error, CodexConnectionError)`.
    pub fn is_connection_error(&self) -> bool {
        !matches!(self, Self::Io(_))
    }
}

impl std::fmt::Display for CodexError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.message())
    }
}

impl std::error::Error for CodexError {}

fn connection_error(message: &str) -> CodexError {
    CodexError::Connection(message.to_owned())
}

/// `_ACCOUNT_CHANGES`: requests that change which account a runtime home is signed in to.
pub const ACCOUNT_CHANGES: [&str; 2] = ["account/login/start", "account/logout"];

/// A listener told a runtime home's account is about to change.
pub type AccountListener = Arc<dyn Fn(&Path) + Send + Sync>;

fn listeners() -> &'static Mutex<Vec<AccountListener>> {
    static LISTENERS: OnceLock<Mutex<Vec<AccountListener>>> = OnceLock::new();
    LISTENERS.get_or_init(|| Mutex::new(Vec::new()))
}

/// `add_account_change_listener(listener)`: be told before a runtime home's account changes.
/// The same listener is added once.
pub fn add_account_change_listener(listener: AccountListener) {
    let mut registered = listeners().lock().expect("listener lock");
    if !registered.iter().any(|existing| Arc::ptr_eq(existing, &listener)) {
        registered.push(listener);
    }
}

/// `account_changed(home)`: tell listeners a runtime home's account has just changed.
pub fn account_changed(home: &Path) {
    announce_account_change(home);
}

/// `_announce_account_change(home)`: a listener that fails is ignored.
fn announce_account_change(home: &Path) {
    let registered: Vec<AccountListener> = listeners().lock().expect("listener lock").clone();
    for listener in registered {
        let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| listener(home)));
    }
}

const ISOLATED_ALLOWED: [&str; 16] = [
    "systemroot",
    "windir",
    "comspec",
    "path",
    "pathext",
    "temp",
    "tmp",
    "userprofile",
    "homedrive",
    "homepath",
    "localappdata",
    "appdata",
    "programfiles",
    "programfiles(x86)",
    "programdata",
    "systemdrive",
];

/// `Path.drive`: the drive or UNC share of a Windows path, else empty.
pub fn path_drive(home: &Path) -> OsString {
    match home.components().next() {
        Some(Component::Prefix(prefix)) => prefix.as_os_str().to_owned(),
        _ => OsString::new(),
    }
}

/// `isolated_environment(home)`: operating-system essentials, without provider secrets or Codex
/// overrides. `home` is resolved.
pub fn isolated_environment(home: &Path) -> Vec<(OsString, OsString)> {
    isolated_environment_for(
        &runtime_process::process_environment(),
        home.as_os_str(),
        &path_drive(home),
        std::path::MAIN_SEPARATOR_STR,
    )
}

/// [`isolated_environment`] from a given environment, home path, its drive and separator.
pub fn isolated_environment_for(
    environ: &[(OsString, OsString)],
    home: &OsStr,
    drive: &OsStr,
    separator: &str,
) -> Vec<(OsString, OsString)> {
    let mut environment = allowed_variables(environ, &ISOLATED_ALLOWED);
    let join = |parts: &[&str]| {
        let mut path = home.to_owned();
        for part in parts {
            path.push(separator);
            path.push(part);
        }
        path
    };
    set_variable(&mut environment, "CODEX_HOME", home);
    set_variable(&mut environment, "HOME", home);
    set_variable(&mut environment, "USERPROFILE", home);
    set_variable(&mut environment, "HOMEDRIVE", drive);
    let text = home.to_string_lossy();
    let rest: String = text.chars().skip(drive.to_string_lossy().chars().count()).collect();
    set_variable(&mut environment, "HOMEPATH", rest);
    set_variable(&mut environment, "APPDATA", join(&["AppData", "Roaming"]));
    set_variable(&mut environment, "LOCALAPPDATA", join(&["AppData", "Local"]));
    environment
}

/// `missing_runtime_message(windows)`.
pub fn missing_runtime_message(windows: bool) -> String {
    let message = "Install the Codex runtime before connecting ChatGPT.";
    if windows {
        return message.into();
    }
    format!(
        "{message} Sylvia looks for the codex program in the folders on its PATH. If Codex is already installed, \
         start Sylvia from a terminal in which codex works, or link codex into a folder your desktop session has \
         on its PATH, such as ~/.local/bin."
    )
}

/// `shutil.which(cmd)` (Python 3.12) for a bare command name, against `PATH` (and `PATHEXT` on
/// Windows) as given.
pub fn which(command: &str, path: Option<&OsStr>, pathext: Option<&str>) -> Option<PathBuf> {
    let path = match path {
        Some(path) => path.to_owned(),
        // `os.confstr("CS_PATH")`, else `os.defpath`.
        None if cfg!(windows) => OsString::from(".;C:\\bin"),
        None => OsString::from("/bin:/usr/bin"),
    };
    if path.is_empty() {
        return None;
    }
    // `path.split(os.pathsep)`: every entry as written, empty ones and quotes included (Rust's
    // `split_paths` would strip quotes on Windows).
    let mut directories: Vec<PathBuf> = split_on_pathsep(&path);
    if cfg!(windows) && std::env::var_os("NoDefaultCurrentDirectoryInExePath").is_none() {
        // `NeedCurrentDirectoryForExePath`: the current folder comes first.
        directories.insert(0, PathBuf::from("."));
    }
    let files: Vec<String> = if cfg!(windows) {
        let source =
            pathext.filter(|text| !text.is_empty()).unwrap_or(".COM;.EXE;.BAT;.CMD;.VBS;.JS;.WS;.MSC");
        let extensions: Vec<String> = source
            .split(';')
            .filter(|ext| !ext.is_empty())
            .map(|ext| ext.trim_end_matches('.').to_owned())
            .collect();
        let mut files: Vec<String> = extensions.iter().map(|ext| format!("{command}{ext}")).collect();
        let upper = command.to_uppercase();
        if extensions.iter().any(|ext| upper.ends_with(&ext.to_uppercase())) {
            files.insert(0, command.to_owned());
        }
        files
    } else {
        vec![command.to_owned()]
    };
    let mut seen = BTreeSet::new();
    for directory in directories {
        let normal = if cfg!(windows) {
            directory.to_string_lossy().to_lowercase().replace('/', "\\")
        } else {
            directory.to_string_lossy().into_owned()
        };
        if !seen.insert(normal) {
            continue;
        }
        for file in &files {
            let name = directory.join(file);
            if executable_file(&name) {
                return Some(name);
            }
        }
    }
    None
}

fn split_on_pathsep(path: &OsStr) -> Vec<PathBuf> {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        path.as_bytes()
            .split(|byte| *byte == b':')
            .map(|entry| PathBuf::from(OsStr::from_bytes(entry)))
            .collect()
    }
    #[cfg(not(unix))]
    {
        path.to_string_lossy().split(';').map(PathBuf::from).collect()
    }
}

/// `_access_check(name, os.F_OK | os.X_OK)`: it exists, may be executed, and is not a folder.
fn executable_file(path: &Path) -> bool {
    let Ok(metadata) = std::fs::metadata(path) else { return false };
    // On Windows `os.access` grants execution to anything that exists.
    #[cfg(target_os = "linux")]
    let allowed = rustix::fs::access(path, rustix::fs::Access::EXISTS | rustix::fs::Access::EXEC_OK).is_ok();
    #[cfg(not(target_os = "linux"))]
    let allowed = true;
    allowed && !metadata.is_dir()
}

fn is_exe(path: &Path) -> bool {
    path.extension().is_some_and(|ext| ext.to_string_lossy().to_lowercase() == "exe")
}

/// `find_codex_runtime()`: a native runtime on `PATH`, or in the Windows Codex desktop
/// installation.
pub fn find_codex_runtime() -> Option<PathBuf> {
    let path = std::env::var_os("PATH");
    let pathext = std::env::var("PATHEXT").ok();
    if let Some(found) = which("codex", path.as_deref(), pathext.as_deref()) {
        if !cfg!(windows) || is_exe(&found) {
            return Some(found);
        }
    }
    if cfg!(windows) {
        let local = std::env::var_os("LOCALAPPDATA").filter(|value| !value.is_empty())?;
        let directory = Path::new(&local).join("OpenAI").join("Codex").join("bin");
        let mut best: Option<(std::time::SystemTime, PathBuf)> = None;
        for entry in std::fs::read_dir(&directory).ok()?.flatten() {
            // `glob("*/codex.exe")`: hidden names are not matched by `*`.
            if entry.file_name().to_string_lossy().starts_with('.') {
                continue;
            }
            let candidate = entry.path().join("codex.exe");
            let Ok(modified) = std::fs::metadata(&candidate).and_then(|metadata| metadata.modified()) else {
                continue;
            };
            if best.as_ref().is_none_or(|(newest, _)| modified > *newest) {
                best = Some((modified, candidate));
            }
        }
        return best.map(|(_, path)| path);
    }
    None
}

/// `Path.resolve()`: absolute, with links resolved where the path exists, and on Windows
/// without the `\\?\` prefix `canonicalize` adds.
pub fn python_resolve(path: &Path) -> PathBuf {
    let resolved = std::fs::canonicalize(path)
        .or_else(|_| std::path::absolute(path))
        .unwrap_or_else(|_| path.to_owned());
    if cfg!(windows) {
        let text = resolved.to_string_lossy();
        if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
            return PathBuf::from(format!(r"\\{rest}"));
        }
        if let Some(rest) = text.strip_prefix(r"\\?\") {
            return PathBuf::from(rest);
        }
    }
    resolved
}

/// The arguments `_start_process` starts the App Server with.
pub const APP_SERVER_ARGUMENTS: [&str; 46] = [
    "app-server",
    "--stdio",
    "-c",
    "cli_auth_credentials_store=\"file\"",
    "-c",
    "forced_login_method=\"chatgpt\"",
    "-c",
    "analytics.enabled=false",
    "-c",
    "history.persistence=\"none\"",
    "-c",
    "features.shell_tool=false",
    "-c",
    "features.code_mode_host=true",
    "-c",
    "features.code_mode.direct_only_tool_namespaces=[\"sylvia\"]",
    "-c",
    "features.multi_agent=false",
    "-c",
    "features.multi_agent_v2=false",
    "-c",
    "agents.enabled=false",
    "-c",
    "tools.experimental_request_user_input.enabled=false",
    "-c",
    "tools.update_plan.enabled=false",
    "-c",
    "skills.include_instructions=false",
    "-c",
    "features.apps=false",
    "-c",
    "features.goals=false",
    "-c",
    "features.hooks=false",
    "-c",
    "features.memories=false",
    "-c",
    "features.skip_host_skill_discovery=true",
    "-c",
    "features.skill_search=false",
    "-c",
    "features.tool_suggest=false",
    "-c",
    "features.sleep_tool=false",
    "-c",
    "web_search=\"disabled\"",
];

/// The notifications a connection keeps for its reader.
const NOTIFICATION_LIMIT: usize = 8192;

/// `_lifecycle_observer(name, start_ns, end_ns, status)`: local boundary timings only.
pub type LifecycleObserver = Arc<dyn Fn(&str, u128, u128, &str) + Send + Sync>;

/// `time.perf_counter_ns()`: nanoseconds on a monotonic clock.
pub fn perf_counter_ns() -> u128 {
    static ORIGIN: OnceLock<Instant> = OnceLock::new();
    ORIGIN.get_or_init(Instant::now).elapsed().as_nanos()
}

/// A connection's queues, replaced each time its process starts. The sending ends sit apart
/// from the receiving ones, so `request_stop` can wake a call that is waiting.
struct Queues {
    responses: Mutex<Receiver<Option<JsonMap>>>,
    responder: Mutex<Sender<Option<JsonMap>>>,
    notifications: Mutex<Receiver<JsonMap>>,
}

impl Queues {
    fn new() -> (Self, Sender<Option<JsonMap>>, SyncSender<JsonMap>) {
        let (responder, responses) = mpsc::channel();
        let (notifier, notifications) = mpsc::sync_channel(NOTIFICATION_LIMIT);
        let queues = Self {
            responses: Mutex::new(responses),
            responder: Mutex::new(responder.clone()),
            notifications: Mutex::new(notifications),
        };
        (queues, responder, notifier)
    }
}

struct Inner {
    home: PathBuf,
    workspace: PathBuf,
    executable: Option<PathBuf>,
    dynamic_tools: BTreeSet<String>,
    overflow: AtomicBool,
    child: Mutex<Option<Child>>,
    /// The pipe, and `_write_lock`.
    stdin: Mutex<Option<ChildStdin>>,
    /// `_call_lock`.
    calls: Mutex<()>,
    queues: Mutex<Arc<Queues>>,
    next_id: AtomicU64,
    stop_requested: AtomicBool,
    observer: Mutex<Option<LifecycleObserver>>,
    /// Which process the reader belongs to (`self._process is not process`).
    generation: AtomicU64,
}

/// `CodexConnection`: RPC calls one at a time, with notifications and failures drained
/// independently. Unexpected server requests are denied; only the Sylvia tools named in
/// `dynamic_tools` are forwarded to the provider, which keeps their execution. A clone is a
/// handle to the same child.
#[derive(Clone)]
pub struct CodexConnection {
    inner: Arc<Inner>,
}

impl Inner {
    fn queues(&self) -> Arc<Queues> {
        Arc::clone(&self.queues.lock().expect("queue lock"))
    }

    fn check_stopped(&self) -> Result<(), CodexError> {
        if self.stop_requested.load(Ordering::SeqCst) {
            return Err(CodexError::Cancelled("The ChatGPT response was cancelled.".into()));
        }
        Ok(())
    }

    fn running(&self) -> bool {
        self.child.lock().expect("child lock").as_mut().is_some_and(|child| !runtime_process::exited(child))
    }

    /// `_write(message)`: one indivisible protocol record, never part of an error.
    fn write(&self, message: &Json) -> Result<(), CodexError> {
        let mut stdin = self.stdin.lock().expect("stdin lock");
        let closed = || connection_error("The ChatGPT connection has closed. Reconnect to continue.");
        if stdin.is_none() || !self.running() {
            return Err(closed());
        }
        let pipe = stdin.as_mut().expect("a pipe");
        pipe.write_all(&runtime_process::frame(message))
            .and_then(|()| pipe.flush())
            .map_err(|_| connection_error("The ChatGPT connection could not send a request."))
    }

    /// `_read(process, responses, notifications)`: drain only this child's protocol and deny any
    /// unsolicited action request.
    fn read(
        &self,
        generation: u64,
        stdout: std::process::ChildStdout,
        responses: Sender<Option<JsonMap>>,
        notifications: SyncSender<JsonMap>,
    ) {
        let mut lines = TextLines::new(stdout);
        // The end of the output, or an `OSError` or `UnicodeDecodeError`, ends the reading.
        while let Ok(Some(line)) = lines.read_line(None) {
            if self.generation.load(Ordering::SeqCst) != generation {
                break;
            }
            let Ok(Json::Object(message)) = sylvia_base::pyjson::loads(&line) else { break };
            let method = message.get("method");
            let overflowed = if message.contains_key("id") && method.is_some() {
                let params = message.get("params");
                let forwarded = method.and_then(Json::as_str) == Some("item/tool/call")
                    && params.and_then(|params| params.get("namespace")).and_then(Json::as_str)
                        == Some("sylvia")
                    && params
                        .filter(|params| params.is_object())
                        .and_then(|params| params.get("tool"))
                        .and_then(Json::as_str)
                        .is_some_and(|tool| self.dynamic_tools.contains(tool));
                if forwarded {
                    matches!(notifications.try_send(message), Err(TrySendError::Full(_)))
                } else {
                    let denial = serde_json::json!({
                        "id": message["id"].clone(),
                        "error": {"code": -32601, "message": "This capability is not available in Sylvia."},
                    });
                    if self.write(&denial).is_err() {
                        break;
                    }
                    false
                }
            } else if message.contains_key("id") {
                let _ = responses.send(Some(message));
                false
            } else if method.is_some_and(Json::is_string) {
                matches!(notifications.try_send(message), Err(TrySendError::Full(_)))
            } else {
                false
            };
            if overflowed {
                self.overflow.store(true, Ordering::SeqCst);
                if let Some(child) = self.child.lock().expect("child lock").as_mut() {
                    runtime_process::terminate(child);
                }
                break;
            }
        }
        let _ = responses.send(None);
    }

    fn measure<T>(&self, name: &str, work: impl FnOnce() -> Result<T, CodexError>) -> Result<T, CodexError> {
        let start = perf_counter_ns();
        let outcome = work();
        let status = match &outcome {
            Ok(_) => "completed",
            Err(CodexError::Cancelled(_)) => "cancelled",
            Err(_) => "error",
        };
        if let Some(observer) = self.observer.lock().expect("observer lock").clone() {
            let end = perf_counter_ns();
            let _ =
                std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| observer(name, start, end, status)));
        }
        outcome
    }

    /// `_start_process()`.
    fn start_process(self: &Arc<Self>) -> Result<(), CodexError> {
        let resolved = self.executable.clone().or_else(find_codex_runtime);
        let resolved = match resolved {
            Some(path) if !cfg!(windows) || is_exe(&path) => path,
            _ => return Err(CodexError::Connection(missing_runtime_message(cfg!(windows)))),
        };
        std::fs::create_dir_all(&self.workspace).map_err(|error| CodexError::Io(error.to_string()))?;
        let (queues, responses, notifications) = Queues::new();
        *self.queues.lock().expect("queue lock") = Arc::new(queues);
        self.overflow.store(false, Ordering::SeqCst);
        let arguments: Vec<OsString> = APP_SERVER_ARGUMENTS.iter().map(OsString::from).collect();
        let program = resolved.into_os_string();
        let mut child = runtime_process::spawn(&Spawn {
            program: &program,
            arguments: &arguments,
            workspace: &self.workspace,
            environment: &isolated_environment(&self.home),
            dies_with_owner: false,
        })
        .map_err(|_| connection_error("The Codex runtime could not be started."))?;
        let generation = self.generation.fetch_add(1, Ordering::SeqCst) + 1;
        let stdout = child.stdout.take().expect("a piped stdout");
        *self.stdin.lock().expect("stdin lock") = child.stdin.take();
        *self.child.lock().expect("child lock") = Some(child);
        let reader = Arc::clone(self);
        std::thread::Builder::new()
            .name("sylvia-chatgpt-rpc".into())
            .spawn(move || reader.read(generation, stdout, responses, notifications))
            .expect("the reader thread starts");
        Ok(())
    }

    /// `call(...)` with the call lock held.
    fn call_locked(&self, method: &str, params: Json, timeout: Duration) -> Result<JsonMap, CodexError> {
        self.check_stopped()?;
        let request_id = self.next_id.fetch_add(1, Ordering::SeqCst) + 1;
        self.write(&serde_json::json!({"id": request_id, "method": method, "params": params}))?;
        let queues = self.queues();
        let received = queues.responses.lock().expect("response lock").recv_timeout(timeout);
        let response = match received {
            Ok(response) => response,
            Err(RecvTimeoutError::Timeout) => {
                self.close_locked();
                return Err(connection_error("The ChatGPT connection timed out. Reconnect to retry."));
            }
            Err(RecvTimeoutError::Disconnected) => None,
        };
        self.check_stopped()?;
        let response = match response {
            Some(response)
                if response.get("id").is_some_and(|id| crate::pyobj::py_eq(id, &Json::from(request_id))) =>
            {
                response
            }
            _ => {
                self.close_locked();
                return Err(connection_error("The ChatGPT connection ended before replying."));
            }
        };
        if response.contains_key("error") {
            return Err(connection_error(
                "The Codex runtime rejected this operation. Check sign-in and runtime compatibility.",
            ));
        }
        match response.get("result") {
            Some(Json::Object(result)) => Ok(result.clone()),
            _ => Err(connection_error("The Codex runtime returned an unsupported reply.")),
        }
    }

    /// `close()` with the call lock held.
    fn close_locked(&self) {
        let mut child = self.child.lock().expect("child lock");
        let Some(mut process) = child.take() else { return };
        runtime_process::stop(&mut process);
        self.generation.fetch_add(1, Ordering::SeqCst);
        drop(child);
        self.stdin.lock().expect("stdin lock").take();
    }
}

impl CodexConnection {
    /// `CodexConnection(home, executable, dynamic_tools=...)`.
    pub fn new(home: &Path, executable: Option<&Path>, dynamic_tools: BTreeSet<String>) -> Self {
        let home = python_resolve(home);
        Self {
            inner: Arc::new(Inner {
                workspace: home.join("workspace"),
                home,
                executable: executable.map(Path::to_owned),
                dynamic_tools,
                overflow: AtomicBool::new(false),
                child: Mutex::new(None),
                stdin: Mutex::new(None),
                calls: Mutex::new(()),
                queues: Mutex::new(Arc::new(Queues::new().0)),
                next_id: AtomicU64::new(0),
                stop_requested: AtomicBool::new(false),
                observer: Mutex::new(None),
                generation: AtomicU64::new(0),
            }),
        }
    }

    /// `home` (resolved).
    pub fn home(&self) -> &Path {
        &self.inner.home
    }

    /// `set_lifecycle_observer(observer)`.
    pub fn set_lifecycle_observer(&self, observer: LifecycleObserver) {
        *self.inner.observer.lock().expect("observer lock") = Some(observer);
    }

    /// `_check_stopped()`: `CodexCancelledError` once a stop was requested.
    pub fn check_stopped(&self) -> Result<(), CodexError> {
        self.inner.check_stopped()
    }

    /// `request_stop()`: fence this connection and wake its pending call.
    pub fn request_stop(&self) {
        if !self.inner.stop_requested.swap(true, Ordering::SeqCst) {
            let _ = self.inner.queues().responder.lock().expect("responder lock").send(None);
        }
    }

    /// `start()`: a dedicated stdio child with its own profile and no environment access, then
    /// the `initialize` handshake.
    pub fn start(&self) -> Result<(), CodexError> {
        let inner = &self.inner;
        let _calls = inner.calls.lock().expect("call lock");
        inner.check_stopped()?;
        if inner.running() {
            return Ok(());
        }
        inner.measure("process_boot", || inner.start_process())?;
        let handshake = inner.measure("initialize", || {
            inner.call_locked(
                "initialize",
                serde_json::json!({
                    "clientInfo": {"name": "sylvia_gm", "title": "Sylvia GM", "version": "0.3.0"},
                    "capabilities": {"experimentalApi": true},
                }),
                Duration::from_secs(30),
            )?;
            inner.write(&serde_json::json!({"method": "initialized"}))
        });
        if let Err(error) = handshake {
            inner.close_locked();
            return Err(error);
        }
        Ok(())
    }

    /// The process alone, without the `initialize` handshake (`_start_process`).
    #[doc(hidden)]
    pub fn start_process(&self) -> Result<(), CodexError> {
        let _calls = self.inner.calls.lock().expect("call lock");
        self.inner.start_process()
    }

    /// `call(method, params, timeout=30)`: wait for the correlated reply; a timeout closes the
    /// connection so stale work is never reused.
    pub fn call(&self, method: &str, params: Json, timeout: Duration) -> Result<JsonMap, CodexError> {
        if ACCOUNT_CHANGES.contains(&method) {
            announce_account_change(&self.inner.home);
        }
        let _calls = self.inner.calls.lock().expect("call lock");
        self.inner.call_locked(method, params, timeout)
    }

    /// `respond(request_id, result)`: answer a server request this connection forwarded.
    pub fn respond(&self, request_id: &Json, result: JsonMap) -> Result<(), CodexError> {
        self.inner.check_stopped()?;
        self.inner.write(&serde_json::json!({"id": request_id, "result": result}))
    }

    /// `notifications()`: every pending notification, without waiting.
    pub fn notifications(&self) -> Result<Vec<JsonMap>, CodexError> {
        if self.inner.overflow.load(Ordering::SeqCst) {
            return Err(connection_error("The ChatGPT event stream exceeded its safe buffer."));
        }
        let queues = self.inner.queues();
        let notifications = queues.notifications.lock().expect("notification lock");
        Ok(std::iter::from_fn(|| notifications.try_recv().ok()).collect())
    }

    /// `next_event(timeout=0.25)`: one event, with a short wait so cancellation stays
    /// responsive.
    pub fn next_event(&self, timeout: Duration) -> Result<Option<JsonMap>, CodexError> {
        self.inner.check_stopped()?;
        if self.inner.overflow.load(Ordering::SeqCst) {
            return Err(connection_error("The ChatGPT event stream exceeded its safe buffer."));
        }
        let queues = self.inner.queues();
        let received = queues.notifications.lock().expect("notification lock").recv_timeout(timeout);
        match received {
            Ok(event) => {
                self.inner.check_stopped()?;
                Ok(Some(event))
            }
            Err(_) if !self.inner.running() => {
                Err(connection_error("The ChatGPT runtime stopped before completing its response."))
            }
            Err(_) => Ok(None),
        }
    }

    /// `close()`: stop only the child this connection created; its sign-in stays saved.
    pub fn close(&self) {
        let _calls = self.inner.calls.lock().expect("call lock");
        self.inner.close_locked();
    }

    /// The process id, while it runs.
    pub fn process_id(&self) -> Option<u32> {
        self.inner.child.lock().expect("child lock").as_ref().map(Child::id)
    }
}
