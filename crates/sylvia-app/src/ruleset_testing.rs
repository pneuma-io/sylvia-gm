//! `application/ruleset_testing.py`: bounded process isolation for saved Builder Ruleset tests.
//!
//! [`run_ruleset_tests`] writes the exact archive to a fresh temporary folder and runs the
//! worker in its own process: the owner executable with `--ruleset-test-worker --archive X
//! --result Y` (lane 5's worker mode, Python's frozen command; a Rust owner is always frozen).
//! The parent waits, polling every 0.1 s for cancellation and the deadline, kills the worker
//! when either comes, and admits only a bounded result object with a known status. Termination
//! never touches live campaign state: the worker has only the copied archive.

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::{DomainError, DomainResult};

use crate::workspace_files::os_error;

/// The archive limit, 128 MiB.
const ARCHIVE_LIMIT: usize = 128 * 1024 * 1024;
/// The result limit, 4 MiB.
const RESULT_LIMIT: u64 = 4 * 1024 * 1024;
/// How long one wait for the worker lasts before cancellation and the deadline are checked.
const POLL: Duration = Duration::from_millis(100);
/// How often the worker's exit is checked within one wait.
const EXIT_CHECK: Duration = Duration::from_millis(5);
/// The statuses a worker result may carry.
const STATUSES: [&str; 5] = ["passed", "failed", "error", "missing_tests", "incomplete_coverage"];

/// The worker program and its leading arguments; the parent adds `--archive` and `--result`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkerCommand {
    pub program: PathBuf,
    pub args: Vec<OsString>,
}

impl WorkerCommand {
    /// `[sys.executable, "--ruleset-test-worker"]`: this executable in its worker mode.
    pub fn current() -> std::io::Result<Self> {
        Ok(Self { program: std::env::current_exe()?, args: vec!["--ruleset-test-worker".into()] })
    }
}

/// `{"status": status, "scenarios": [], "message": message}`.
fn outcome(status: &str, message: &str) -> JsonMap {
    let mut result = JsonMap::new();
    result.insert("status".into(), status.into());
    result.insert("scenarios".into(), Json::Array(Vec::new()));
    result.insert("message".into(), message.into());
    result
}

/// `tempfile.TemporaryDirectory(prefix="sylvia-ruleset-test-")`: removed when dropped.
struct TemporaryDirectory {
    path: PathBuf,
}

impl TemporaryDirectory {
    fn new() -> DomainResult<Self> {
        let parent = std::env::temp_dir();
        let mut seed = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|elapsed| elapsed.as_nanos() as u64)
            .unwrap_or_default()
            ^ (u64::from(std::process::id()) << 32);
        const CHARACTERS: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789_";
        for _ in 0..100 {
            let name: String = (0..8)
                .map(|_| {
                    seed =
                        seed.wrapping_mul(6_364_136_223_846_793_005).wrapping_add(1_442_695_040_888_963_407);
                    CHARACTERS[(seed >> 33) as usize % CHARACTERS.len()] as char
                })
                .collect();
            let path = parent.join(format!("sylvia-ruleset-test-{name}"));
            #[cfg_attr(not(unix), allow(unused_mut))]
            let mut builder = std::fs::DirBuilder::new();
            #[cfg(unix)]
            {
                use std::os::unix::fs::DirBuilderExt;
                builder.mode(0o700);
            }
            match builder.create(&path) {
                Ok(()) => return Ok(Self { path }),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(os_error(&error, &path)),
            }
        }
        Err(DomainError::uncaught("FileExistsError", "No usable temporary directory name found"))
    }
}

impl Drop for TemporaryDirectory {
    fn drop(&mut self) {
        // The worker has exited (it is waited for before this), so nothing holds its files.
        let _ = std::fs::remove_dir_all(&self.path);
    }
}

/// `_stop_worker(process)`: never leave a disposable worker alive.
struct Worker(Child);

impl Drop for Worker {
    fn drop(&mut self) {
        if let Ok(None) = self.0.try_wait() {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
}

/// `run_ruleset_tests(archive, timeout_seconds=120.0, cancelled=None)` with this executable as
/// the worker.
pub fn run_ruleset_tests(
    archive: &[u8],
    timeout_seconds: f64,
    cancelled: Option<&dyn Fn() -> bool>,
) -> DomainResult<JsonMap> {
    let worker = WorkerCommand::current().map_err(|error| os_error(&error, Path::new("")))?;
    run_ruleset_tests_with(&worker, archive, timeout_seconds, cancelled)
}

/// `run_ruleset_tests` with a given worker command: test an exact archive in a child process.
///
/// A non-positive timeout is Python's `ValueError`; a file or process failure its `OSError`.
/// Every other outcome is a result object.
pub fn run_ruleset_tests_with(
    worker: &WorkerCommand,
    archive: &[u8],
    timeout_seconds: f64,
    cancelled: Option<&dyn Fn() -> bool>,
) -> DomainResult<JsonMap> {
    if timeout_seconds <= 0.0 {
        return Err(DomainError::value("Scenario timeout must be positive"));
    }
    let temporary = TemporaryDirectory::new()?;
    let (source, destination) =
        (temporary.path.join("candidate.package"), temporary.path.join("result.json"));
    if archive.len() > ARCHIVE_LIMIT {
        return Ok(outcome("error", "Test package exceeds the size limit."));
    }
    std::fs::write(&source, archive).map_err(|error| os_error(&error, &source))?;
    let mut command = Command::new(&worker.program);
    command
        .args(&worker.args)
        .arg("--archive")
        .arg(&source)
        .arg("--result")
        .arg(&destination)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut process = Worker(command.spawn().map_err(|error| os_error(&error, &worker.program))?);
    // A NaN or unbounded timeout never arrives, as `time.monotonic() >= deadline` never holds.
    let deadline = Duration::try_from_secs_f64(timeout_seconds)
        .ok()
        .and_then(|timeout| Instant::now().checked_add(timeout));
    let status = loop {
        if let Some(status) = process.0.try_wait().map_err(|error| os_error(&error, &worker.program))? {
            break status;
        }
        let mut stop = cancelled.is_some_and(|cancelled| cancelled()).then_some("cancelled");
        if stop.is_none() && deadline.is_some_and(|deadline| Instant::now() >= deadline) {
            stop = Some("timed_out");
        }
        if let Some(stop) = stop {
            let _ = process.0.kill();
            let _ = process.0.wait();
            return Ok(outcome(stop, "Scenario run did not finish."));
        }
        // `process.wait(timeout=0.1)`, which returns as soon as the worker exits.
        let started = Instant::now();
        while started.elapsed() < POLL {
            if process.0.try_wait().map_err(|error| os_error(&error, &worker.program))?.is_some() {
                break;
            }
            std::thread::sleep(EXIT_CHECK);
        }
    };
    if !status.success() || !destination.is_file() {
        return Ok(outcome("error", "Scenario worker did not return results."));
    }
    let size = std::fs::metadata(&destination).map_err(|error| os_error(&error, &destination))?.len();
    if size > RESULT_LIMIT {
        return Ok(outcome("error", "Scenario results exceed the size limit."));
    }
    let bytes = std::fs::read(&destination).map_err(|error| os_error(&error, &destination))?;
    let invalid = || outcome("error", "Scenario worker returned invalid results.");
    // `read_text(encoding="utf-8")` then `json.loads`; a decoding or parsing failure is invalid.
    let Ok(text) = String::from_utf8(bytes) else { return Ok(invalid()) };
    let Ok(Json::Object(result)) = pyjson::loads(&text) else { return Ok(invalid()) };
    match result.get("status") {
        // `status not in {...}` hashes the status: a list or an object raises `TypeError`.
        Some(Json::Array(_)) => return Err(DomainError::type_error("unhashable type: 'list'")),
        Some(Json::Object(_)) => return Err(DomainError::type_error("unhashable type: 'dict'")),
        Some(Json::String(status)) if STATUSES.contains(&status.as_str()) => {}
        _ => return Ok(invalid()),
    }
    Ok(result)
}
