//! `provider/_claude_runtime.py`: owned stdio access to Sylvia's own unmodified copy of Claude
//! Code. Lane 5.
//!
//! Sylvia keeps a separate installation, profile and sign-in under its runtime home and never
//! uses the Claude Code a person may already have. Only the binary accesses native
//! authentication. The child is started, read and stopped by [`crate::runtime_process`].

use crate::error::{ErrorKind, ProviderError};
use crate::runtime_process::{
    self, allowed_variables, set_variable, Captured, LineError, RunError, Spawn, TextLines,
};
use std::ffi::{OsStr, OsString};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, RecvTimeoutError, SyncSender, TrySendError};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};
use sylvia_base::{Json, JsonMap};

/// `MINIMUM_VERSION`.
pub const MINIMUM_VERSION: (u64, u64, u64) = (2, 1, 322);
/// `MINIMUM_VERSION_TEXT`.
pub const MINIMUM_VERSION_TEXT: &str = "2.1.322";
/// `MAX_FRAME_BYTES`: the longest frame the runtime may write.
pub const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;
/// `DOCUMENTED_CONTEXT_WINDOW_AT`: when the documented windows were read.
pub const DOCUMENTED_CONTEXT_WINDOW_AT: &str = "2026-11-17";
/// The events `ClaudeConnection` keeps waiting for a reader.
const EVENT_LIMIT: usize = 1024;

/// `documented_context_window(model_id)`: the documented window for an exact Claude Code model
/// name or alias (a trailing `[1m]` ignored), else `None`.
pub fn documented_context_window(model_id: &Json) -> Option<i64> {
    let name = model_id.as_str()?;
    crate::model_facts::claude_code_context_window(name.strip_suffix("[1m]").unwrap_or(name))
}

/// `_DESKTOP_SESSION`: what a Linux desktop session uses to reach the person's display and
/// browser.
const DESKTOP_SESSION: [&str; 8] = [
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_TYPE",
    "DBUS_SESSION_BUS_ADDRESS",
    "BROWSER",
];

const NATIVE_ALLOWED: [&str; 22] = [
    "systemroot",
    "windir",
    "comspec",
    "path",
    "pathext",
    "temp",
    "tmp",
    "userprofile",
    "home",
    "homedrive",
    "homepath",
    "localappdata",
    "appdata",
    "programfiles",
    "programfiles(x86)",
    "programdata",
    "systemdrive",
    "lang",
    "lc_all",
    "lc_ctype",
    "xdg_config_home",
    "xdg_data_home",
];

/// The platform branches `native_environment` takes (`sys.platform`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Platform {
    /// `sys.platform.startswith("linux")`.
    pub linux: bool,
    /// `sys.platform == "win32"`.
    pub windows: bool,
}

impl Platform {
    /// This build's platform.
    pub const CURRENT: Self = Self { linux: cfg!(target_os = "linux"), windows: cfg!(windows) };
}

/// `str(home / name)`, with the path separator of the platform the home belongs to.
fn child_path(home: &OsStr, separator: &str, name: &str) -> OsString {
    let mut path = home.to_owned();
    path.push(separator);
    path.push(name);
    path
}

fn to_bytes(text: &OsStr) -> Vec<u8> {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;
        text.as_bytes().to_vec()
    }
    #[cfg(not(unix))]
    {
        text.to_string_lossy().into_owned().into_bytes()
    }
}

fn from_bytes(bytes: Vec<u8>) -> OsString {
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStringExt;
        OsString::from_vec(bytes)
    }
    #[cfg(not(unix))]
    {
        OsString::from(String::from_utf8_lossy(&bytes).into_owned())
    }
}

/// `native_environment(home)`: Sylvia's copy against Sylvia's profile, without inherited
/// inference overrides.
pub fn native_environment(home: &Path) -> Vec<(OsString, OsString)> {
    native_environment_for(
        &runtime_process::process_environment(),
        home.as_os_str(),
        std::path::MAIN_SEPARATOR_STR,
        Platform::CURRENT,
    )
}

/// [`native_environment`] from a given environment, home path and platform.
pub fn native_environment_for(
    environ: &[(OsString, OsString)],
    home: &OsStr,
    separator: &str,
    platform: Platform,
) -> Vec<(OsString, OsString)> {
    let source: Vec<(OsString, OsString)> = if platform.linux {
        // A packed Linux app puts its own folders on PATH; Claude Code and the programs it starts
        // must find the system's own. (The Rust owner is never a frozen Python bundle.)
        let bytes: Vec<(Vec<u8>, Vec<u8>)> =
            environ.iter().map(|(name, value)| (to_bytes(name), to_bytes(value))).collect();
        crate::config::host_browser::host_environment(&bytes)
            .into_iter()
            .map(|(name, value)| (from_bytes(name), from_bytes(value)))
            .collect()
    } else {
        environ.to_vec()
    };
    let mut environment = allowed_variables(&source, &NATIVE_ALLOWED);
    for (name, folder) in [
        ("CLAUDE_CONFIG_DIR", "profile"),
        ("ANTHROPIC_CONFIG_DIR", "anthropic"),
        ("CLAUDE_CODE_TMPDIR", "temp"),
    ] {
        set_variable(&mut environment, name, child_path(home, separator, folder));
    }
    for (name, value) in [
        ("DISABLE_AUTOUPDATER", "1"),
        ("DISABLE_UPDATES", "1"),
        ("DISABLE_INSTALLATION_CHECKS", "1"),
        ("DISABLE_TELEMETRY", "1"),
        ("DISABLE_ERROR_REPORTING", "1"),
        ("ENABLE_CLAUDEAI_MCP_SERVERS", "false"),
        ("ENABLE_TOOL_SEARCH", "false"),
        ("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1"),
    ] {
        set_variable(&mut environment, name, value);
    }
    if !platform.windows {
        set_variable(&mut environment, "XDG_CACHE_HOME", child_path(home, separator, "cache"));
        set_variable(&mut environment, "XDG_STATE_HOME", child_path(home, separator, "state"));
        for name in DESKTOP_SESSION {
            if let Some((_, value)) = source.iter().find(|(existing, _)| existing == name) {
                set_variable(&mut environment, name, value.clone());
            }
        }
    }
    environment
}

/// `native_auth_command(executable, action)`: native authentication without the person's
/// settings and customizations.
pub fn native_auth_command(executable: &Path, action: &str) -> Vec<OsString> {
    vec![
        executable.as_os_str().to_owned(),
        "--safe-mode".into(),
        "--restricted".into(),
        "--setting-sources".into(),
        "".into(),
        "--settings".into(),
        "{\"disableAllHooks\":true}".into(),
        "auth".into(),
        action.into(),
    ]
}

/// `executable_name()`.
pub fn executable_name() -> &'static str {
    if cfg!(windows) {
        "claude.exe"
    } else {
        "claude"
    }
}

/// The value of one run of Unicode decimal digits (`int()` of `\d+`), compared by magnitude:
/// leading zeros dropped, then length, then digits.
fn digits(text: &str) -> Option<String> {
    if text.is_empty() {
        return None;
    }
    let mut value = String::new();
    for c in text.chars() {
        value.push(char::from(b'0' + sylvia_base::pystr::decimal(c)? as u8));
    }
    let trimmed = value.trim_start_matches('0');
    Some(if trimmed.is_empty() { "0".into() } else { trimmed.into() })
}

/// `re.fullmatch(r"\d+\.\d+\.\d+", name)` and its version tuple, comparable as Python's.
fn version_key(name: &str) -> Option<Vec<(usize, String)>> {
    let parts: Vec<&str> = name.split('.').collect();
    if parts.len() != 3 {
        return None;
    }
    parts.iter().map(|part| digits(part).map(|value| (value.len(), value))).collect()
}

/// `find_runtime(home)`: the newest copy Sylvia installed under `home`; never another Claude
/// Code.
pub fn find_runtime(home: &Path) -> Option<PathBuf> {
    let directory = home.join("install");
    let entries = std::fs::read_dir(&directory).ok()?;
    let mut installed: Vec<(Vec<(usize, String)>, PathBuf)> = Vec::new();
    for entry in entries.flatten() {
        let name = entry.file_name();
        let Some(key) = name.to_str().and_then(version_key) else { continue };
        let candidate = entry.path().join(executable_name());
        if candidate.is_file() {
            installed.push((key, candidate));
        }
    }
    let compare_paths = |a: &PathBuf, b: &PathBuf| {
        if cfg!(windows) {
            a.to_string_lossy().to_lowercase().cmp(&b.to_string_lossy().to_lowercase())
        } else {
            a.cmp(b)
        }
    };
    let best = installed
        .into_iter()
        .max_by(|(a, path_a), (b, path_b)| a.cmp(b).then_with(|| compare_paths(path_a, path_b)))?;
    std::path::absolute(&best.1).ok()
}

/// The version `runtime_version` reads from `claude --version`: `re.fullmatch(r"(\d+)\.(\d+)
/// \.(\d+) \(Claude Code\)\s*", stdout)`.
fn parse_version_line(stdout: &str) -> Option<(String, String, String)> {
    let (version, rest) = stdout.split_once(" (Claude Code)")?;
    if !rest.chars().all(sylvia_base::pystr::is_space) {
        return None;
    }
    let parts: Vec<&str> = version.split('.').collect();
    if parts.len() != 3 {
        return None;
    }
    Some((digits(parts[0])?, digits(parts[1])?, digits(parts[2])?))
}

fn connection(message: &str) -> ProviderError {
    ProviderError::connection(message, None)
}

/// `runtime_version`'s reading of `claude --version`'s run.
pub fn parse_runtime_version(run: Result<Captured, RunError>) -> Result<String, ProviderError> {
    let run = run.map_err(|_| connection("The Claude executable could not report its version."))?;
    let damaged =
        || connection("Sylvia's copy of Claude Code is damaged. Install it again in Settings > Accounts.");
    if run.code != Some(0) {
        return Err(damaged());
    }
    let (major, minor, patch) = parse_version_line(&run.stdout).ok_or_else(damaged)?;
    let number = |text: &str| text.parse::<u64>().unwrap_or(u64::MAX);
    if (number(&major), number(&minor), number(&patch)) < MINIMUM_VERSION {
        return Err(connection(&format!(
            "Claude Code {MINIMUM_VERSION_TEXT} or later is required. Update Sylvia's copy in Settings > Accounts."
        )));
    }
    Ok(format!("{major}.{minor}.{patch}"))
}

/// `runtime_version(executable, home)`: the native protocol prerequisite, without sign-in or a
/// model request.
pub fn runtime_version(executable: &Path, home: &Path) -> Result<String, ProviderError> {
    parse_runtime_version(runtime_process::run_captured(
        &executable.as_os_str().to_owned(),
        &["--version".into()],
        &native_environment(home),
        Duration::from_secs(10),
    ))
}

const AUTH_METHODS: [&str; 6] =
    ["none", "claude.ai", "oauth_token", "api_key", "api_key_helper", "third_party"];

/// `authentication_status`'s projection of `claude auth status`'s run: allowlisted fields only.
pub fn parse_authentication_status(run: Result<Captured, RunError>) -> Result<JsonMap, ProviderError> {
    let failed = || connection("Claude could not report its sign-in status.");
    let run = run.map_err(|_| failed())?;
    let raw = sylvia_base::pyjson::loads(&run.stdout).map_err(|_| failed())?;
    let Json::Object(raw) = raw else { return Err(failed()) };
    if !matches!(run.code, Some(0 | 1)) {
        return Err(failed());
    }
    // `method not in {...}`: a list or dict is unhashable, and Python's `TypeError` escapes.
    if let Some(value @ (Json::Array(_) | Json::Object(_))) = raw.get("authMethod") {
        return Err(ProviderError::new(
            ErrorKind::Internal,
            format!("unhashable type: '{}'", crate::pyobj::type_name(value)),
        ));
    }
    let method = raw.get("authMethod").and_then(Json::as_str).filter(|method| AUTH_METHODS.contains(method));
    let signed_in = raw.get("loggedIn") == Some(&Json::Bool(true)) && run.code == Some(0);
    let billing = match method {
        Some("claude.ai") => "subscription",
        Some("api_key" | "api_key_helper") => "api",
        _ => "unknown",
    };
    let mut status = JsonMap::new();
    status.insert("signed_in".into(), Json::Bool(signed_in));
    status.insert("auth_method".into(), method.map_or(Json::Null, |method| Json::String(method.into())));
    status.insert("billing".into(), Json::String(billing.into()));
    Ok(status)
}

/// `authentication_status(executable, home)`.
pub fn authentication_status(executable: &Path, home: &Path) -> Result<JsonMap, ProviderError> {
    let command = native_auth_command(executable, "status");
    parse_authentication_status(runtime_process::run_captured(
        &command[0],
        &command[1..],
        &native_environment(home),
        Duration::from_secs(10),
    ))
}

/// One item `ClaudeConnection` hands its reader.
#[derive(Debug, Clone, PartialEq)]
pub enum RuntimeEvent {
    /// A protocol event (a JSON object).
    Event(JsonMap),
    /// The stream failed (`InvalidResponseError`).
    Failed(ProviderError),
    /// The process's output ended (Python's `None`).
    Exited,
}

/// `ClaudeConnection`: exactly one native process, with every stdio frame and pending reader
/// bounded. A clone is a handle to the same process.
#[derive(Clone)]
pub struct ClaudeConnection {
    inner: Arc<Inner>,
}

struct Inner {
    executable: PathBuf,
    workspace: PathBuf,
    arguments: Vec<OsString>,
    environment: Vec<(OsString, OsString)>,
    child: Mutex<Option<Child>>,
    stdin: Mutex<Option<ChildStdin>>,
    sender: SyncSender<RuntimeEvent>,
    events: Mutex<Receiver<RuntimeEvent>>,
    stopped: AtomicBool,
    reader: Mutex<Option<JoinHandle<()>>>,
}

impl Inner {
    /// `_enqueue(event)`: wait for room while the connection is open.
    fn enqueue(&self, mut event: RuntimeEvent) {
        while !self.stopped.load(Ordering::SeqCst) {
            match self.sender.try_send(event) {
                Ok(()) | Err(TrySendError::Disconnected(_)) => return,
                Err(TrySendError::Full(back)) => {
                    event = back;
                    std::thread::sleep(Duration::from_millis(10));
                }
            }
        }
    }

    /// `_read()`.
    fn read(&self, stdout: std::process::ChildStdout) {
        let mut lines = TextLines::new(stdout);
        let invalid = || {
            RuntimeEvent::Failed(ProviderError::new(
                ErrorKind::InvalidResponse,
                "Claude returned an invalid runtime stream.",
            ))
        };
        while !self.stopped.load(Ordering::SeqCst) {
            let line = match lines.read_line(Some(MAX_FRAME_BYTES + 1)) {
                Ok(Some(line)) => line,
                Ok(None) => break,
                Err(LineError::Io(_) | LineError::Decode) => {
                    self.enqueue(invalid());
                    break;
                }
            };
            if line.len() > MAX_FRAME_BYTES || !line.ends_with('\n') {
                self.enqueue(invalid());
                break;
            }
            let Ok(Json::Object(event)) = sylvia_base::pyjson::loads(&line) else {
                self.enqueue(invalid());
                break;
            };
            if event.get("type").and_then(Json::as_str) == Some("tool_progress") {
                // Heartbeats while a Sylvia call waits; Sylvia never reads them.
                continue;
            }
            self.enqueue(RuntimeEvent::Event(event));
        }
        self.enqueue(RuntimeEvent::Exited);
    }
}

impl ClaudeConnection {
    /// `ClaudeConnection(executable, workspace, arguments, environment)`.
    pub fn new(
        executable: &Path,
        workspace: &Path,
        arguments: Vec<OsString>,
        environment: Vec<(OsString, OsString)>,
    ) -> Self {
        let (sender, events) = mpsc::sync_channel(EVENT_LIMIT);
        Self {
            inner: Arc::new(Inner {
                executable: executable.to_owned(),
                workspace: workspace.to_owned(),
                arguments,
                environment,
                child: Mutex::new(None),
                stdin: Mutex::new(None),
                sender,
                events: Mutex::new(events),
                stopped: AtomicBool::new(false),
                reader: Mutex::new(None),
            }),
        }
    }

    /// `start()`: the official binary, with native auth and no inherited API-key override.
    pub fn start(&self) -> Result<(), ProviderError> {
        let inner = &self.inner;
        if inner.stopped.load(Ordering::SeqCst) {
            return Ok(());
        }
        let program = inner.executable.as_os_str().to_owned();
        let mut child = runtime_process::spawn(&Spawn {
            program: &program,
            arguments: &inner.arguments,
            workspace: &inner.workspace,
            environment: &inner.environment,
            dies_with_owner: true,
        })
        .map_err(|_| connection("The Claude runtime could not start."))?;
        let stdout = child.stdout.take().expect("a piped stdout");
        *inner.stdin.lock().expect("stdin lock") = child.stdin.take();
        *inner.child.lock().expect("child lock") = Some(child);
        let reader = Arc::clone(inner);
        *inner.reader.lock().expect("reader lock") = Some(
            std::thread::Builder::new()
                .name("sylvia-claude-reader".into())
                .spawn(move || reader.read(stdout))
                .expect("the reader thread starts"),
        );
        if inner.stopped.load(Ordering::SeqCst) {
            self.close();
        }
        Ok(())
    }

    /// `write(message)`: one protocol frame, without its private payload in any failure.
    pub fn write(&self, message: &Json) -> Result<(), ProviderError> {
        let mut stdin = self.inner.stdin.lock().expect("stdin lock");
        let running = self.inner.child.lock().expect("child lock").is_some();
        let Some(pipe) = stdin.as_mut().filter(|_| running && !self.inner.stopped.load(Ordering::SeqCst))
        else {
            return Err(connection("The Claude runtime has stopped."));
        };
        pipe.write_all(&runtime_process::frame(message))
            .and_then(|()| pipe.flush())
            .map_err(|_| connection("The Claude runtime connection closed."))
    }

    /// `next_event(timeout)`: fail closed on the end of output, a malformed frame or inactivity.
    pub fn next_event(&self, timeout: Duration) -> Result<JsonMap, ProviderError> {
        let events = self.inner.events.lock().expect("events lock");
        match events.recv_timeout(timeout) {
            Ok(RuntimeEvent::Event(event)) => Ok(event),
            Ok(RuntimeEvent::Failed(error)) => Err(error),
            Ok(RuntimeEvent::Exited) | Err(RecvTimeoutError::Disconnected) => {
                Err(connection("Claude exited before completing its response. Saved work is kept."))
            }
            Err(RecvTimeoutError::Timeout) => {
                Err(ProviderError::new(ErrorKind::Timeout, "Claude stopped responding. Saved work is kept."))
            }
        }
    }

    /// `drain()`: what the process wrote that has not been read, without waiting
    /// ([`RuntimeEvent::Exited`] once it has exited).
    pub fn drain(&self) -> Vec<RuntimeEvent> {
        let events = self.inner.events.lock().expect("events lock");
        let mut drained = Vec::new();
        while let Ok(event) = events.try_recv() {
            let exited = event == RuntimeEvent::Exited;
            drained.push(event);
            if exited {
                break;
            }
        }
        if drained.is_empty() {
            if let Some(child) = self.inner.child.lock().expect("child lock").as_mut() {
                if runtime_process::exited(child) {
                    drained.push(RuntimeEvent::Exited);
                }
            }
        }
        drained
    }

    /// `close()`: collect this process only; never stop, sign out or look for other Claude
    /// sessions.
    pub fn close(&self) {
        let inner = &self.inner;
        inner.stopped.store(true, Ordering::SeqCst);
        if let Some(child) = inner.child.lock().expect("child lock").as_mut() {
            runtime_process::stop(child);
        }
        inner.stdin.lock().expect("stdin lock").take();
        let reader = inner.reader.lock().expect("reader lock").take();
        if let Some(reader) = reader {
            if reader.thread().id() != std::thread::current().id() {
                let started = Instant::now();
                while !reader.is_finished() && started.elapsed() < Duration::from_secs(1) {
                    std::thread::sleep(Duration::from_millis(10));
                }
            }
        }
        let _ = inner.sender.try_send(RuntimeEvent::Exited);
    }

    /// The process id, while it runs.
    pub fn process_id(&self) -> Option<u32> {
        self.inner.child.lock().expect("child lock").as_ref().map(Child::id)
    }
}
