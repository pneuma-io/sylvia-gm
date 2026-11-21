//! `application/desktop_update/helper.py`: the packaged two-phase helper
//! (`sylvia-gm-owner --desktop-update-helper`). It authenticates and acknowledges, waits for
//! the native parent's commit, waits for the owned processes to exit, applies once and
//! starts the updated (or restored) app once.
//!
//! Only the packaged public pin supplies trust: there is no command-line, environment or
//! IPC key override. In this build no key is pinned, so the helper refuses at once with
//! `trust_not_configured`, as 0.4.1 does. The keyed paths are proven by vectors through
//! [`main_with`].

use super::checkpoint::{acquire_update_data_lock, RESTORE_CONFIRMATION};
use super::manifest::{bounded_json, KeyRing, Version, APP_VERSION};
use super::preparation::{InstalledHooks, PreparedUpdate};
use super::processes::{OwnedProcesses, ProcessHandles, ProcessReceipt};
use super::progress::{phase as stage, Progress};
use super::recovery::{RecoveryOperation, KEEP_DATA};
use super::restart::RestartHandshake;
use super::runtime::child_environment;
use super::storage::{atomic_receipt, operation_id, os_error, phase, present};
use super::update_error;
use crate::util::uuid4_hex;
use std::io::{BufRead, Read, Write};
use std::path::{Path, PathBuf};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_store::owner_lock::OwnerLock;

/// A started app the helper watches: `poll()` is its exit code once it exited.
pub trait Child {
    fn poll(&mut self) -> Option<i64>;
}

impl Child for std::process::Child {
    fn poll(&mut self) -> Option<i64> {
        match self.try_wait() {
            Ok(Some(status)) => Some(status.code().map_or(1, i64::from)),
            Ok(None) => None,
            // Python's `Popen.poll()` treats a failed wait as still running.
            Err(_) => None,
        }
    }
}

/// `launch(executable, arguments)`: start the app, or the error that kept it from starting.
pub type Launch<'a> = dyn Fn(&Path, &[String]) -> std::io::Result<Box<dyn Child>> + 'a;

/// How the helper probes, starts, waits and reports: Python's `probe=`, `launch=`, `pause=`
/// and `notify=` keyword arguments.
pub struct Hooks<'a> {
    pub probe: &'a dyn Fn(&Path) -> CodedResult<Json>,
    pub launch: &'a Launch<'a>,
    /// `pause(0.05)`.
    pub pause: &'a dyn Fn(),
    pub notify: &'a mut dyn FnMut(&str) -> CodedResult<()>,
}

/// `CREATE_NO_WINDOW`.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

fn unsupported_platform() -> CodedError {
    update_error("unsupported_platform", "The update helper requires Windows.")
}

/// A child of the helper: its own folder, the helper's environment (refusing engineering
/// overrides), no window. Rust's `Command` gives it only the handles it creates for it;
/// the inherited ones were made non-inheritable when the helper started.
fn child_command(executable: &Path, arguments: &[&str]) -> CodedResult<std::process::Command> {
    let mut command = std::process::Command::new(executable);
    command
        .args(arguments)
        .env_clear()
        .envs(child_environment()?)
        .current_dir(executable.parent().unwrap_or(Path::new("")))
        .stdin(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    Ok(command)
}

/// `probe_owner(executable)`: the updated owner's identity from `--desktop-update-identity`,
/// at most 4096 bytes.
pub fn probe_owner(executable: &Path) -> CodedResult<Json> {
    if !cfg!(windows) {
        return Err(unsupported_platform());
    }
    let mut child = child_command(executable, &["--desktop-update-identity"])?
        .stdout(std::process::Stdio::piped())
        .spawn()
        .map_err(|error| os_error(&error))?;
    let mut raw = Vec::new();
    if let Some(stdout) = child.stdout.take() {
        stdout.take(4097).read_to_end(&mut raw).map_err(|error| os_error(&error))?;
    }
    let status = child.wait().map_err(|error| os_error(&error))?;
    if !status.success() {
        return Err(update_error("wrong_owner_identity", "The updated owner could not report its identity."));
    }
    bounded_json(&raw, 4096)
}

/// `launch_app(executable, arguments)`.
pub fn launch_app(executable: &Path, arguments: &[String]) -> std::io::Result<Box<dyn Child>> {
    if !cfg!(windows) {
        return Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "The update helper requires Windows.",
        ));
    }
    let arguments: Vec<&str> = arguments.iter().map(String::as_str).collect();
    let mut command =
        child_command(executable, &arguments).map_err(|error| std::io::Error::other(error.message))?;
    let child = command.stdout(std::process::Stdio::null()).spawn()?;
    Ok(Box::new(child))
}

/// The app a restart starts: the desktop of this distribution.
fn app_executable(prepared: &PreparedUpdate) -> PathBuf {
    prepared.live.join(if prepared.distribution == "portable" { "Sylvia GM.exe" } else { "sylvia-gm.exe" })
}

fn wait_for(processes_exited: &dyn Fn() -> CodedResult<bool>, pause: &dyn Fn()) -> CodedResult<()> {
    // No kill, no search for unrelated processes and no artificial whole-update cutoff.
    while !processes_exited()? {
        pause();
    }
    Ok(())
}

fn holding<T>(lock: CodedResult<OwnerLock>, work: impl FnOnce() -> CodedResult<T>) -> CodedResult<T> {
    let mut lock = lock?;
    let outcome = work();
    lock.close();
    outcome
}

/// `apply_and_restart(prepared, processes)`: the native owner armed this update; apply and
/// restart under this operation's helper lock.
pub fn apply_and_restart(
    prepared: &mut PreparedUpdate,
    processes_exited: &dyn Fn() -> CodedResult<bool>,
    hooks: &mut Hooks<'_>,
) -> CodedResult<()> {
    child_environment()?;
    prepared.require_selected(None)?;
    let row = prepared.store.load()?;
    if !row.is_some_and(|row| {
        [phase::CLOSING, phase::APPLYING, phase::AWAITING_RESTART, phase::CONFIRMED]
            .contains(&row.phase.as_str())
    }) {
        return Err(update_error("recovery_required", "The native owner has not armed this update."));
    }
    let lock = acquire_update_data_lock(&prepared.stage.join("helper-runtime"));
    holding(lock, || apply_and_restart_locked(prepared, processes_exited, hooks))
}

/// `_apply_and_restart_locked(...)`.
pub fn apply_and_restart_locked(
    prepared: &mut PreparedUpdate,
    processes_exited: &dyn Fn() -> CodedResult<bool>,
    hooks: &mut Hooks<'_>,
) -> CodedResult<()> {
    child_environment()?;
    prepared.require_selected(None)?;
    let row = prepared.store.load()?;
    let current = row.as_ref().map(|row| row.phase.clone());
    if matches!(current.as_deref(), Some(phase::AWAITING_RESTART | phase::CONFIRMED)) {
        let handshake = RestartHandshake::new(&prepared.root, &prepared.identity, &prepared.attempt, false)?;
        if present(&handshake.ticket()?) && handshake.confirmed()? {
            if current.as_deref() != Some(phase::CONFIRMED) {
                prepared.confirm(None)?;
            }
            return Ok(());
        }
        return Err(update_error(
            "recovery_required",
            "Reconcile the previous startup before another launch.",
        ));
    }
    if !matches!(current.as_deref(), Some(phase::CLOSING | phase::APPLYING)) {
        return Err(update_error(
            "recovery_required",
            "Choose an explicit recovery for this interrupted update.",
        ));
    }
    (hooks.notify)(stage::WAITING)?;
    wait_for(processes_exited, hooks.pause)?;
    (hooks.notify)(stage::APPLYING)?;
    prepared.apply(processes_exited)?;
    (hooks.notify)(stage::VERIFYING)?;
    let identity = (hooks.probe)(&prepared.live.join("sylvia-gm-owner.exe"))?;
    let handshake = prepared.create_restart(&identity)?;
    let executable = app_executable(prepared);
    (hooks.notify)(stage::STARTING)?;
    let launch = hooks.launch;
    let mut child = handshake.launch_once(|arguments| launch(&executable, arguments))?;
    (hooks.notify)(stage::CONFIRMING)?;
    while !handshake.confirmed()? {
        if child.poll().is_some() {
            return Err(update_error(
                "startup_failed",
                "The updated app exited without confirming its data.",
            ));
        }
        (hooks.pause)();
    }
    prepared.confirm(None)
}

/// `recover_and_restart(prepared, processes, choice, restart_nonce=)`.
pub fn recover_and_restart(
    prepared: &mut PreparedUpdate,
    processes_exited: &dyn Fn() -> CodedResult<bool>,
    choice: &str,
    restart_nonce: Option<&str>,
    hooks: &mut Hooks<'_>,
) -> CodedResult<()> {
    child_environment()?;
    prepared.require_selected(None)?;
    let lock = acquire_update_data_lock(&prepared.stage.join("helper-runtime"));
    holding(lock, || recover_and_restart_locked(prepared, processes_exited, choice, restart_nonce, hooks))
}

/// `_recover_and_restart_locked(...)`.
pub fn recover_and_restart_locked(
    prepared: &PreparedUpdate,
    processes_exited: &dyn Fn() -> CodedResult<bool>,
    choice: &str,
    restart_nonce: Option<&str>,
    hooks: &mut Hooks<'_>,
) -> CodedResult<()> {
    child_environment()?;
    let mut recovery = RecoveryOperation::new(prepared.clone())?;
    let current = recovery.phase()?;
    let restarting = [phase::ROLLBACK_AWAITING_RESTART, phase::ROLLED_BACK].contains(&current.as_str())
        || (current == phase::ROLLBACK_READY && recovery.handshake.exists());
    if restarting {
        if restart_nonce.is_none() && current != phase::ROLLBACK_READY && recovery.handshake.confirmed()? {
            if current != phase::ROLLED_BACK {
                recovery.confirm()?;
            }
            return Ok(());
        }
        if restart_nonce.is_none() || current == phase::ROLLED_BACK {
            return Err(update_error("recovery_required", "Reconcile the earlier recovery startup."));
        }
    } else if restart_nonce.is_some() {
        return Err(update_error("stale_restart", "The reviewed recovery restart changed."));
    }
    (hooks.notify)(stage::WAITING)?;
    wait_for(processes_exited, hooks.pause)?;
    let owner = prepared.live.join("sylvia-gm-owner.exe");
    let handshake = match restart_nonce {
        Some(nonce) => {
            if choice != KEEP_DATA {
                return Err(update_error("invalid_restart", "A restart retry cannot restore data again."));
            }
            (hooks.notify)(stage::VERIFYING)?;
            let identity = (hooks.probe)(&owner)?;
            recovery.retry_restart(nonce, &identity, processes_exited)?
        }
        None => {
            (hooks.notify)(stage::RESTORING)?;
            recovery.rollback_locked(choice, processes_exited)?;
            (hooks.notify)(stage::VERIFYING)?;
            let identity = (hooks.probe)(&owner)?;
            recovery.create_restart(&identity)?
        }
    };
    let executable = app_executable(prepared);
    (hooks.notify)(stage::STARTING)?;
    let launch = hooks.launch;
    let mut child = handshake.launch_once(|arguments| launch(&executable, arguments))?;
    (hooks.notify)(stage::CONFIRMING)?;
    while !handshake.confirmed()? {
        if child.poll().is_some() {
            return Err(update_error("startup_failed", "The restored app did not confirm startup."));
        }
        (hooks.pause)();
    }
    recovery.confirm()
}

/// `_read(stream)`: one bounded request line.
pub fn read_request(input: &mut dyn BufRead) -> CodedResult<Json> {
    let mut raw = Vec::new();
    input.take(65_537).read_until(b'\n', &mut raw).map_err(|error| os_error(&error))?;
    if raw.last() != Some(&b'\n') {
        return Err(update_error("invalid_helper_request", "The bounded helper request is incomplete."));
    }
    bounded_json(&raw, 65_536)
}

/// The Python classes with a stable `code` the helper may report (`getattr(error, "code")`).
const CODED_CLASSES: [&str; 9] = [
    "UpdateError",
    "SchemaIdentityError",
    "WrongOwner",
    "WrongWriter",
    "StaleRevision",
    "IdempotencyConflict",
    "CommandRejected",
    "MechanicsRejected",
    "RulesetPreconditionRejected",
];

/// The Windows error of an escaped `OSError` (`error.winerror`), read from std's
/// `... (os error N)` text, which `os_error` keeps as the message.
fn winerror(error: &CodedError) -> Option<i64> {
    if !cfg!(windows)
        || !matches!(
            error.error_type,
            Some("OSError" | "FileNotFoundError" | "FileExistsError" | "PermissionError")
        )
    {
        return None;
    }
    let rest = error.message.strip_suffix(')')?;
    rest.rsplit_once("(os error ")?.1.parse().ok()
}

/// `_failure_code(error)`: a sharing or lock violation is `file_locked`, an access denial
/// `file_access`, a coded error its own short code, anything else `helper_failed`.
pub fn failure_code(error: &CodedError) -> String {
    match winerror(error) {
        Some(32 | 33) => return "file_locked".into(),
        Some(5) => return "file_access".into(),
        _ => {}
    }
    let coded = error.error_type.is_some_and(|class| CODED_CLASSES.contains(&class));
    let short = !error.code.is_empty()
        && error.code.len() <= 64
        && error.code.bytes().all(|byte| byte.is_ascii_lowercase() || byte == b'_');
    if coded && short {
        error.code.clone()
    } else {
        "helper_failed".into()
    }
}

/// What [`main_with`] runs with: the trust, the platform and every operating-system edge.
pub struct Setup<'a, A: ProcessHandles> {
    pub keyring: KeyRing,
    /// `runtime_identity()["target"]`.
    pub target: String,
    /// The reply line ending: Python's standard output writes `\r\n` on Windows.
    pub newline: &'static str,
    /// Keep the helper's standard handles out of its children (no Python counterpart:
    /// Python's `Popen` passes a child only its own handles).
    pub prepare_children: Box<dyn FnOnce() -> CodedResult<()> + 'a>,
    /// `WindowsHandles()`.
    pub handles: Box<dyn FnOnce() -> CodedResult<A> + 'a>,
    /// `presentation_factory()`.
    pub presentation: Box<dyn FnOnce() -> CodedResult<Box<dyn Progress + 'a>> + 'a>,
    pub probe: &'a dyn Fn(&Path) -> CodedResult<Json>,
    pub launch: &'a Launch<'a>,
    pub pause: &'a dyn Fn(),
    /// An installed copy's registration and installer, when not the real ones.
    pub installed: Option<InstalledHooks>,
}

/// What `main` holds while it runs, released in Python's `finally` order.
struct Held<'a, A: ProcessHandles> {
    processes: Option<OwnedProcesses<A>>,
    prepared: Option<PreparedUpdate>,
    operation_lock: Option<OwnerLock>,
    committed: bool,
    recovering: bool,
    presentation: Option<Box<dyn Progress + 'a>>,
}

fn write_line(output: &mut dyn Write, value: &Json, newline: &str) -> CodedResult<()> {
    let mut line = sylvia_base::pyjson::dumps(value, sylvia_base::pyjson::Dump::WIRE);
    line.push_str(newline);
    output.write_all(line.as_bytes()).and_then(|()| output.flush()).map_err(|error| os_error(&error))
}

fn object(pairs: &[(&str, Json)]) -> Json {
    let mut map = JsonMap::new();
    for (key, value) in pairs {
        map.insert((*key).to_string(), value.clone());
    }
    Json::Object(map)
}

/// The launch request's shape, as `main` checks it.
fn valid_request(row: &Json, recovering: bool) -> bool {
    let Json::Object(row) = row else {
        return false;
    };
    let mut keys = vec!["type", "operation_id", "attempt", "data_root", "live", "distribution", "processes"];
    if recovering {
        keys.extend(["choice", "restart_nonce"]);
    }
    let hex32 =
        |text: &str| text.len() == 32 && text.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'));
    row.len() == keys.len()
        && keys.iter().all(|key| row.contains_key(*key))
        && row["type"]
            .as_str()
            .is_some_and(|kind| ["desktop_update_apply", "desktop_update_recover"].contains(&kind))
        && (!recovering
            || row["choice"]
                .as_str()
                .is_some_and(|choice| choice == KEEP_DATA || choice == RESTORE_CONFIRMATION))
        && (!recovering || row["restart_nonce"].is_null() || row["restart_nonce"].as_str().is_some_and(hex32))
        && row["distribution"].as_str().is_some_and(|kind| kind == "installed" || kind == "portable")
        && row["processes"].as_array().is_some_and(|items| (2..=3).contains(&items.len()))
}

/// `Path(text)`: rebuilt from its components, as pathlib normalizes it.
fn python_path(text: &str) -> PathBuf {
    Path::new(text).components().collect()
}

fn run<'a, A: ProcessHandles>(
    setup: &mut Option<Setup<'a, A>>,
    held: &mut Held<'a, A>,
    input: &mut dyn BufRead,
    output: &mut dyn Write,
) -> CodedResult<()> {
    let Setup {
        keyring,
        target,
        newline,
        prepare_children,
        handles,
        presentation,
        probe,
        launch,
        pause,
        installed,
    } = setup.take().expect("run once");
    if !keyring.has_keys() {
        return Err(update_error("trust_not_configured", "An approved updater bootstrap is required."));
    }
    prepare_children()?;
    child_environment()?;
    let row = read_request(input)?;
    held.recovering = row.get("type").is_some_and(|kind| kind == "desktop_update_recover");
    if !valid_request(&row, held.recovering) {
        return Err(update_error("invalid_helper_request", "The helper launch context is invalid."));
    }
    for name in ["data_root", "live"] {
        let valid = row[name].as_str().is_some_and(|text| {
            (1..=4096).contains(&text.chars().count()) && python_path(text).is_absolute()
        });
        if !valid {
            return Err(update_error("invalid_helper_request", "The selected helper location is invalid."));
        }
    }
    let receipts = row["processes"]
        .as_array()
        .into_iter()
        .flatten()
        .map(ProcessReceipt::parse)
        .collect::<CodedResult<Vec<_>>>()?;
    held.processes = Some(OwnedProcesses::new(&receipts, handles()?)?);
    let data_root = python_path(row["data_root"].as_str().unwrap_or_default());
    let live = python_path(row["live"].as_str().unwrap_or_default());
    let identity = operation_id(&row["operation_id"])?;
    let attempt = operation_id(&row["attempt"])?;
    let current = Version::parse_text(APP_VERSION)?;
    let distribution = row["distribution"].as_str().unwrap_or_default();
    let mut prepared = PreparedUpdate::new(
        &data_root,
        &live,
        &identity,
        &attempt,
        &current,
        &target,
        distribution,
        &keyring,
    )?;
    if let Some(installed) = installed {
        prepared = prepared.with_installed(installed);
    }
    let prepared = held.prepared.insert(prepared);
    prepared.require_selected(None)?;
    held.operation_lock = Some(acquire_update_data_lock(&prepared.stage.join("helper-runtime"))?);
    prepared.require_selected(None)?;
    // Prove the presentation before the parent may close.
    let presentation = held.presentation.insert(presentation()?);
    let nonce = uuid4_hex();
    write_line(
        output,
        &object(&[
            ("type", "ready".into()),
            ("operation_id", prepared.identity.clone().into()),
            ("attempt", prepared.attempt.clone().into()),
            ("helper_nonce", nonce.clone().into()),
        ]),
        newline,
    )?;
    let commit = read_request(input)?;
    let expected = object(&[
        ("action", "commit".into()),
        ("operation_id", prepared.identity.clone().into()),
        ("attempt", prepared.attempt.clone().into()),
        ("helper_nonce", nonce.into()),
    ]);
    if commit != expected {
        return Err(update_error("helper_not_committed", "The native parent did not commit this update."));
    }
    held.committed = true;
    let processes = held.processes.as_ref().expect("made above");
    let exited = || processes.all_exited();
    let mut notify = |phase: &str| presentation.stage(phase);
    let mut hooks = Hooks { probe, launch, pause, notify: &mut notify };
    if held.recovering {
        let restart_nonce = row["restart_nonce"].as_str();
        let choice = row["choice"].as_str().unwrap_or_default();
        recover_and_restart_locked(prepared, &exited, choice, restart_nonce, &mut hooks)
    } else {
        apply_and_restart_locked(prepared, &exited, &mut hooks)
    }
}

/// `main(input, output, presentation_factory=)` with every edge supplied: `0` once the
/// restarted app confirmed, `2` on any refusal or failure.
pub fn main_with<A: ProcessHandles>(
    setup: Setup<'_, A>,
    input: &mut dyn BufRead,
    output: &mut dyn Write,
) -> i32 {
    let newline = setup.newline;
    let mut setup = Some(setup);
    let mut held = Held {
        processes: None,
        prepared: None,
        operation_lock: None,
        committed: false,
        recovering: false,
        presentation: None,
    };
    let outcome = run(&mut setup, &mut held, input, output);
    let code = match outcome {
        Ok(()) => 0,
        Err(error) => {
            let code = failure_code(&error);
            if let (Some(prepared), true) = (held.prepared.as_mut(), held.committed) {
                // The failure belongs to this attempt. A newer global selection after an
                // explicit recovery fenced this helper is never overwritten.
                let _ = (|| -> CodedResult<()> {
                    prepared.require_selected(None)?;
                    let handshake = RestartHandshake::new(
                        &prepared.root,
                        &prepared.identity,
                        &prepared.attempt,
                        held.recovering,
                    )?;
                    let path = if !held.recovering || handshake.exists() {
                        handshake.error_receipt()?
                    } else {
                        prepared.stage.join("rollback-helper-error.json")
                    };
                    atomic_receipt(&path, &object(&[("code", code.clone().into())]))
                })();
            }
            // The old app may have closed its reply pipe; the durable status does not need it.
            let _ = write_line(
                output,
                &object(&[("type", "error".into()), ("code", code.clone().into())]),
                newline,
            );
            if held.committed {
                if let Some(presentation) = held.presentation.as_mut() {
                    // No global writes follow: recovery may proceed while the user reads this.
                    if let Some(mut lock) = held.operation_lock.take() {
                        lock.close();
                    }
                    if let Some(mut processes) = held.processes.take() {
                        processes.close();
                    }
                    presentation.fail(&code);
                }
            }
            2
        }
    };
    if let Some(mut presentation) = held.presentation.take() {
        presentation.close();
    }
    if let Some(mut lock) = held.operation_lock.take() {
        lock.close();
    }
    if let Some(mut processes) = held.processes.take() {
        processes.close();
    }
    code
}

/// Keep the helper's three standard handles out of its children.
fn keep_standard_handles_from_children() -> CodedResult<()> {
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        let handles = [
            std::io::stdin().as_raw_handle(),
            std::io::stdout().as_raw_handle(),
            std::io::stderr().as_raw_handle(),
        ];
        for handle in handles.into_iter().filter(|handle| !handle.is_null()) {
            super::windows::keep_from_children(handle).map_err(|_| {
                update_error("child_runtime_unavailable", "The update child runtime could not be prepared.")
            })?;
        }
    }
    Ok(())
}

/// `sylvia-gm-owner --desktop-update-helper`: the packaged helper on this process's
/// standard input and output.
pub fn main() -> i32 {
    let keyring = KeyRing::new(super::service::public_keys());
    let pause = || std::thread::sleep(std::time::Duration::from_millis(50));
    let setup = Setup {
        keyring,
        target: super::identity::runtime_target(),
        newline: if cfg!(windows) { "\r\n" } else { "\n" },
        prepare_children: Box::new(keep_standard_handles_from_children),
        handles: Box::new(super::processes::WindowsHandles::new),
        presentation: Box::new(|| {
            super::progress::start_progress().map(|window| Box::new(window) as Box<dyn Progress>)
        }),
        probe: &probe_owner,
        launch: &launch_app,
        pause: &pause,
        installed: None,
    };
    let stdin = std::io::stdin();
    let mut input = stdin.lock();
    let mut output = std::io::stdout();
    main_with(setup, &mut input, &mut output)
}
