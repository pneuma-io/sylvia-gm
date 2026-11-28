//! The update helper, the installed copy, the process barrier and the registry conversion
//! against `vectors/generate_helper_vectors.py`, recorded from the Python snapshot on the same
//! file trees with the same fakes.

mod support;

use serde_json::{json, Value as J};
use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, VecDeque};
use std::io::{BufRead, Read, Write};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::{Arc, Mutex};
use support::{first_difference, listing, outcome, record, temporary_folder};
use sylvia_base::{CodedError, CodedResult};
use sylvia_owner::desktop_update::checkpoint::acquire_update_data_lock;
use sylvia_owner::desktop_update::helper::{self, Child, Hooks, Setup};
use sylvia_owner::desktop_update::installed::{self, Registration, RegistrationRow};
use sylvia_owner::desktop_update::manifest::{b64decode_strict, KeyRing, Version, APP_VERSION};
use sylvia_owner::desktop_update::preparation::{InstalledHooks, PreparedUpdate};
use sylvia_owner::desktop_update::processes::{OwnedProcesses, ProcessHandles, ProcessReceipt};
use sylvia_owner::desktop_update::progress::Progress;
use sylvia_owner::desktop_update::recovery::RecoveryOperation;
use sylvia_owner::desktop_update::restart::{RestartContext, RestartHandshake};
use sylvia_owner::desktop_update::storage::UpdateStore;
use sylvia_owner::desktop_update::update_error;

const OP: &str = "0123456789abcdef0123456789abcdef";
const ATTEMPT: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const NEW: &str = "0.4.2";
const TARGET: &str = "windows-x86_64";
const LIVE_NAME: &str = "app with spaces";

fn vectors() -> &'static J {
    static VECTORS: std::sync::OnceLock<J> = std::sync::OnceLock::new();
    VECTORS.get_or_init(|| {
        serde_json::from_str(include_str!("vectors/helper_vectors.json")).expect("valid JSON")
    })
}

fn keyring() -> KeyRing {
    let key: [u8; 32] = hex::decode(vectors()["public_key"].as_str().unwrap()).unwrap().try_into().unwrap();
    KeyRing::new(BTreeMap::from([(vectors()["key_id"].as_str().unwrap().to_string(), key)]))
}

fn blob(digest: &str) -> Vec<u8> {
    b64decode_strict(vectors()["blobs"][digest].as_str().unwrap()).unwrap()
}

fn identity(version: &str) -> J {
    json!({"format_version": 1, "product": "Sylvia GM", "component": "owner", "version": version,
           "storage_compatibility": 1, "target": TARGET})
}

fn units(text: &str) -> Vec<u16> {
    text.encode_utf16().collect()
}

type Events = Arc<Mutex<Vec<String>>>;

fn push(events: &Events, event: String) {
    events.lock().unwrap().push(event);
}

struct FakeRegistration(Arc<Mutex<BTreeMap<String, String>>>);

impl Registration for FakeRegistration {
    fn read(&self) -> CodedResult<RegistrationRow> {
        Ok(self.0.lock().unwrap().iter().map(|(key, value)| (key.clone(), units(value))).collect())
    }
}

/// The fakes of one scenario and what they saw.
struct Recorder {
    folder: PathBuf,
    live: PathBuf,
    data: PathBuf,
    distribution: String,
    events: Events,
    registry: Arc<Mutex<BTreeMap<String, String>>>,
    installer_modes: Arc<Mutex<VecDeque<String>>>,
}

fn relative(folder: &Path, path: &Path) -> String {
    path.strip_prefix(folder).unwrap().to_string_lossy().replace('\\', "/")
}

impl Recorder {
    fn installed(&self) -> InstalledHooks {
        let (folder, events, registry, modes) =
            (self.folder.clone(), self.events.clone(), self.registry.clone(), self.installer_modes.clone());
        let run = move |artifact: &Path, destination: &Path| -> CodedResult<i64> {
            push(&events, format!("installer:{}", relative(&folder, artifact)));
            let mode = modes.lock().unwrap().pop_front().unwrap_or_else(|| "ok".into());
            let new =
                artifact.parent().and_then(Path::file_name).is_none_or(|name| name != "previous-installer");
            let (files, version) = if new { ("new", NEW) } else { ("old", APP_VERSION) };
            let files = vectors()["installer_files"][files].as_object().unwrap();
            if mode == "partial" {
                let name = "sylvia-gm.exe";
                std::fs::write(destination.join(name), blob(files[name].as_str().unwrap())).unwrap();
                return Ok(1);
            }
            if mode != "registry-only" {
                for (name, digest) in files {
                    let target = destination.join(name);
                    std::fs::create_dir_all(target.parent().unwrap()).unwrap();
                    std::fs::write(target, blob(digest.as_str().unwrap())).unwrap();
                }
            }
            registry.lock().unwrap().insert("DisplayVersion".into(), version.into());
            Ok(0)
        };
        InstalledHooks { registration: Arc::new(FakeRegistration(self.registry.clone())), run: Arc::new(run) }
    }
}

struct FakeChild(Option<i64>);

impl Child for FakeChild {
    fn poll(&mut self) -> Option<i64> {
        self.0
    }
}

/// `Recorder.hooks(op)`: the barrier script, probe, launch and pause of one step.
struct StepHooks<'a> {
    recorder: &'a Recorder,
    op: &'a J,
    barrier: RefCell<VecDeque<bool>>,
    calls: Cell<usize>,
}

impl<'a> StepHooks<'a> {
    fn new(recorder: &'a Recorder, op: &'a J) -> Self {
        let barrier =
            op.get("barrier").and_then(J::as_array).map(|items| items.iter().map(|v| v == true).collect());
        Self { recorder, op, barrier: RefCell::new(barrier.unwrap_or_default()), calls: Cell::new(0) }
    }

    fn exited(&self) -> CodedResult<bool> {
        self.calls.set(self.calls.get() + 1);
        Ok(self.barrier.borrow_mut().pop_front().unwrap_or(true))
    }

    fn probe(&self, executable: &Path) -> CodedResult<J> {
        push(&self.recorder.events, format!("probe:{}", relative(&self.recorder.folder, executable)));
        Ok(self.op.get("identity").cloned().unwrap_or_else(|| identity(NEW)))
    }

    fn launch(&self, executable: &Path, arguments: &[String]) -> std::io::Result<Box<dyn Child>> {
        let recorder = self.recorder;
        push(&recorder.events, format!("launch:{}:{}", relative(&recorder.folder, executable), arguments[0]));
        let mode = self.op.get("launch").and_then(J::as_str).unwrap_or("confirm");
        if mode == "fail" {
            return Err(std::io::Error::other("the app could not start"));
        }
        let recovery = arguments[0] == "--sylvia-update-recovery-start";
        if mode == "confirm" {
            let version = if recovery { APP_VERSION } else { NEW };
            let context = RestartContext {
                nonce: &arguments[3],
                live: &recorder.live,
                desktop_version: version,
                owner_version: version,
                target: TARGET,
                distribution: &recorder.distribution,
                storage_compatibility: 1,
            };
            RestartHandshake::new(&recorder.data, OP, &arguments[2], recovery)
                .and_then(|handshake| handshake.confirm(&context))
                .expect("the fake app confirms");
        }
        Ok(Box::new(FakeChild(if mode == "confirm" { None } else { Some(1) })))
    }

    fn pause(&self) {
        push(&self.recorder.events, "pause".into());
    }
}

/// The fake process handles of `helper.main`: an owned handle records its close when dropped.
struct FakeHandles {
    table: BTreeMap<u64, (u64, u64)>,
    events: Events,
}

struct Token(u64, Events);

impl Drop for Token {
    fn drop(&mut self) {
        push(&self.1, format!("closed:{}", self.0));
    }
}

impl ProcessHandles for FakeHandles {
    type Owned = Token;

    fn identity(&self, handle: u64) -> CodedResult<(u64, u64)> {
        self.table.get(&handle).copied().ok_or_else(|| {
            update_error("process_identity_unavailable", "The owned process instance could not be verified.")
        })
    }

    fn adopt(&self, handle: u64) -> CodedResult<Token> {
        Ok(Token(handle, self.events.clone()))
    }

    fn exited(&self, handle: &Token) -> CodedResult<bool> {
        push(&self.events, format!("exited:{}", handle.0));
        Ok(true)
    }
}

struct FakePresentation(Events);

impl Progress for FakePresentation {
    fn stage(&mut self, phase: &str) -> CodedResult<()> {
        push(&self.0, format!("stage:{phase}"));
        Ok(())
    }

    fn close(&mut self) {
        push(&self.0, "close".into());
    }

    fn fail(&mut self, code: &str) {
        push(&self.0, format!("fail:{code}"));
    }
}

/// The helper's input: each line as the generator's `Input` gives it, the commit built from
/// the last reply.
struct Lines {
    lines: VecDeque<J>,
    output: Rc<RefCell<Vec<u8>>>,
    buffer: Vec<u8>,
    position: usize,
}

impl Read for Lines {
    fn read(&mut self, target: &mut [u8]) -> std::io::Result<usize> {
        let available = self.fill_buf()?;
        let count = available.len().min(target.len());
        target[..count].copy_from_slice(&available[..count]);
        self.consume(count);
        Ok(count)
    }
}

impl BufRead for Lines {
    fn fill_buf(&mut self) -> std::io::Result<&[u8]> {
        if self.position >= self.buffer.len() {
            if let Some(line) = self.lines.pop_front() {
                let line = if line == "<commit>" {
                    let output = String::from_utf8(self.output.borrow().clone()).unwrap();
                    let ready: J = serde_json::from_str(output.lines().last().unwrap()).unwrap();
                    json!({"action": "commit", "operation_id": OP, "attempt": ATTEMPT,
                           "helper_nonce": ready.get("helper_nonce").cloned().unwrap_or(J::Null)})
                } else {
                    line
                };
                self.buffer = match line {
                    J::String(raw) => raw.into_bytes(),
                    other => format!("{other}\n").into_bytes(),
                };
                self.position = 0;
            }
        }
        Ok(&self.buffer[self.position..])
    }

    fn consume(&mut self, amount: usize) {
        self.position += amount;
    }
}

struct Shared(Rc<RefCell<Vec<u8>>>);

impl Write for Shared {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.0.borrow_mut().extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

fn run_main(recorder: &Recorder, op: &J, lines: Vec<J>) -> J {
    let hooks = StepHooks::new(recorder, op);
    let table = op
        .get("handles")
        .and_then(J::as_object)
        .map(|table| {
            table
                .iter()
                .map(|(key, value)| {
                    (key.parse().unwrap(), (value[0].as_u64().unwrap(), value[1].as_u64().unwrap()))
                })
                .collect()
        })
        .unwrap_or_default();
    let events = recorder.events.clone();
    let unavailable = op.get("presentation").and_then(J::as_str) == Some("unavailable");
    let presentation_events = events.clone();
    let probe = |path: &Path| hooks.probe(path);
    let launch = |path: &Path, arguments: &[String]| hooks.launch(path, arguments);
    let pause = || hooks.pause();
    let keyring =
        if op.get("trust") == Some(&J::Bool(false)) { KeyRing::new(BTreeMap::new()) } else { keyring() };
    let setup = Setup {
        keyring,
        target: TARGET.into(),
        newline: "\n",
        prepare_children: Box::new(|| Ok(())),
        handles: Box::new(move || Ok(FakeHandles { table, events })),
        presentation: Box::new(move || {
            if unavailable {
                return Err(update_error(
                    "progress_unavailable",
                    "The update status window could not open. Sylvia stays open.",
                ));
            }
            Ok(Box::new(FakePresentation(presentation_events)) as Box<dyn Progress>)
        }),
        probe: &probe,
        launch: &launch,
        pause: &pause,
        installed: (recorder.distribution == "installed").then(|| recorder.installed()),
    };
    let output = Rc::new(RefCell::new(Vec::new()));
    let mut input = Lines { lines: lines.into(), output: output.clone(), buffer: Vec::new(), position: 0 };
    let code = helper::main_with(setup, &mut input, &mut Shared(output.clone()));
    let text = String::from_utf8(output.borrow().clone()).unwrap();
    let rows: Vec<J> = text.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    json!({"code": code, "output": rows, "barrier": hooks.calls.get()})
}

fn run(scenario: &J, index: usize) -> J {
    let folder = temporary_folder(&format!("helper-{index}"));
    let root = folder.0.clone();
    for (relative, digest) in scenario["tree"].as_object().unwrap() {
        let path = root.join(relative);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, blob(digest.as_str().unwrap())).unwrap();
    }
    let distribution = scenario.get("distribution").and_then(J::as_str).unwrap_or("portable").to_string();
    let (live, data) = if distribution == "portable" {
        (root.join("app"), root.join("app").join("isolated-data"))
    } else {
        (root.join(LIVE_NAME), root.join("data"))
    };
    let registry = BTreeMap::from([
        ("DisplayName".to_string(), "Sylvia GM".to_string()),
        ("DisplayVersion".into(), APP_VERSION.into()),
        ("Publisher".into(), "pneuma-io".into()),
        ("MainBinaryName".into(), "sylvia-gm.exe".into()),
        ("InstallLocation".into(), format!("\"{}\"", live.display())),
        ("ProductLocation".into(), live.display().to_string()),
        ("UninstallString".into(), format!("\"{}\"", live.join("uninstall.exe").display())),
    ]);
    let modes = scenario
        .get("installer")
        .and_then(J::as_array)
        .map(|modes| modes.iter().map(|mode| mode.as_str().unwrap().to_string()).collect())
        .unwrap_or_default();
    let recorder = Recorder {
        folder: root.clone(),
        live: live.clone(),
        data: data.clone(),
        distribution: distribution.clone(),
        events: Arc::new(Mutex::new(Vec::new())),
        registry: Arc::new(Mutex::new(registry)),
        installer_modes: Arc::new(Mutex::new(modes)),
    };
    let current = Version::parse_text(APP_VERSION).unwrap();
    let ring = keyring();
    let backup_source = root.join("backup-source.sqlite3");
    let backup = |target: &Path| -> CodedResult<()> {
        std::fs::copy(&backup_source, target)
            .map(|_| ())
            .map_err(|error| CodedError::new("internal_error", error.to_string()))
    };
    let mut prepared: Option<PreparedUpdate> = None;
    let mut lock = None;
    let mut results = Vec::new();
    for op in scenario["ops"].as_array().unwrap() {
        let name = op["op"].as_str().unwrap();
        recorder.events.lock().unwrap().clear();
        let mut extra: Option<usize> = None;
        let result = match name {
            "new" => outcome(
                PreparedUpdate::new(&data, &live, OP, ATTEMPT, &current, TARGET, &distribution, &ring).map(
                    |made| {
                        prepared = Some(if distribution == "installed" {
                            made.with_installed(recorder.installed())
                        } else {
                            made
                        });
                        J::Null
                    },
                ),
            ),
            "prepare" => outcome(prepared.as_mut().unwrap().prepare(&backup, &|| true).map(|()| J::Null)),
            "require_selected" => {
                outcome(prepared.as_mut().unwrap().require_selected(None).map(|()| J::Null))
            }
            "apply" => {
                let exited = op.get("exited").is_none_or(|value| value == true);
                outcome(prepared.as_mut().unwrap().apply(&|| Ok(exited)).map(|()| J::Null))
            }
            "create_restart" => outcome(
                prepared
                    .as_mut()
                    .unwrap()
                    .create_restart(&op["identity"])
                    .and_then(|made| Ok(made.load()?["nonce"].clone())),
            ),
            "confirm" => outcome(prepared.as_mut().unwrap().confirm(None).map(|()| J::Null)),
            "save_phase" => outcome(
                UpdateStore::new(&data.join(".application-updates"))
                    .and_then(|store| store.save(OP, op["phase"].as_str().unwrap(), Some(NEW), None))
                    .map(|()| J::Null),
            ),
            "registry" => {
                let value = op["value"].as_str().unwrap().replace("${live}", &live.display().to_string());
                recorder.registry.lock().unwrap().insert(op["field"].as_str().unwrap().into(), value);
                json!({"ok": null})
            }
            "write" => {
                let path = root.join(op["path"].as_str().unwrap());
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                std::fs::write(path, b64decode_strict(op["b64"].as_str().unwrap()).unwrap()).unwrap();
                json!({"ok": null})
            }
            "hold_lock" => {
                lock = Some(
                    acquire_update_data_lock(&prepared.as_ref().unwrap().stage.join("helper-runtime"))
                        .unwrap(),
                );
                json!({"ok": null})
            }
            "release_lock" => {
                lock.take().unwrap().close();
                json!({"ok": null})
            }
            "recovery_rollback" => {
                let exited = op.get("exited").is_none_or(|value| value == true);
                outcome(
                    RecoveryOperation::new(prepared.clone().unwrap())
                        .and_then(|mut recovery| {
                            recovery.rollback(op["choice"].as_str().unwrap(), &|| Ok(exited))
                        })
                        .map(|()| J::Null),
                )
            }
            "recovery_verify" => outcome(
                prepared
                    .as_mut()
                    .unwrap()
                    .replacement()
                    .and_then(|replacement| replacement.verify_rollback())
                    .map(|()| J::Null),
            ),
            "helper_apply" | "helper_recover" => {
                let hooks = StepHooks::new(&recorder, op);
                let probe = |path: &Path| hooks.probe(path);
                let launch = |path: &Path, arguments: &[String]| hooks.launch(path, arguments);
                let pause = || hooks.pause();
                let events = recorder.events.clone();
                let mut notify = |phase: &str| -> CodedResult<()> {
                    push(&events, format!("notify:{phase}"));
                    Ok(())
                };
                let mut step = Hooks { probe: &probe, launch: &launch, pause: &pause, notify: &mut notify };
                let exited = || hooks.exited();
                let prepared = prepared.as_mut().unwrap();
                let result = if name == "helper_apply" {
                    helper::apply_and_restart(prepared, &exited, &mut step)
                } else {
                    let nonce = match op.get("nonce").and_then(J::as_str) {
                        Some("current") => Some(
                            RestartHandshake::new(&data, OP, ATTEMPT, true).unwrap().load().unwrap()["nonce"]
                                .as_str()
                                .unwrap()
                                .to_string(),
                        ),
                        other => other.map(str::to_string),
                    };
                    helper::recover_and_restart(
                        prepared,
                        &exited,
                        op["choice"].as_str().unwrap(),
                        nonce.as_deref(),
                        &mut step,
                    )
                };
                extra = Some(hooks.calls.get());
                outcome(result.map(|()| J::Null))
            }
            "helper_main" => {
                let request = op.get("request").cloned().unwrap_or(J::Null);
                let lines = op["lines"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|line| {
                        if line != "<request>" {
                            return line.clone();
                        }
                        let mut row = json!({
                            "type": request.get("type").cloned().unwrap_or_else(|| json!("desktop_update_apply")),
                            "operation_id": OP, "attempt": ATTEMPT, "data_root": data.to_string_lossy(),
                            "live": live.to_string_lossy(), "distribution": distribution,
                            "processes": request.get("processes").cloned().unwrap_or_else(|| json!([
                                {"role": "desktop", "handle": 101, "pid": 10, "creation_time": 1},
                                {"role": "owner", "handle": 102, "pid": 11, "creation_time": 2}])),
                        });
                        for (key, value) in request.as_object().unwrap() {
                            if key != "type" && key != "processes" {
                                row[key] = value.clone();
                            }
                        }
                        row
                    })
                    .collect();
                run_main(&recorder, op, lines)
            }
            other => panic!("unknown op {other}"),
        };
        let events = recorder.events.lock().unwrap().clone();
        let mut row = json!({"op": name, "result": result, "events": events});
        if let Some(calls) = extra {
            row["barrier"] = json!(calls);
        }
        results.push(row);
    }
    if let Some(mut lock) = lock {
        lock.close();
    }
    drop(prepared);
    record(results, listing(&root))
}

#[test]
fn the_helper_and_the_installed_copy_move_like_python() {
    assert_eq!(vectors()["python"], "3.12.11");
    for (index, scenario) in vectors()["scenarios"].as_array().unwrap().iter().enumerate() {
        let got = run(scenario, index);
        assert!(
            got == scenario["recorded"],
            "{}: {}",
            scenario["name"],
            first_difference(&got, &scenario["recorded"], "")
        );
    }
}

#[test]
fn process_receipts_parse_like_python() {
    for case in vectors()["receipts"].as_array().unwrap() {
        let got = outcome(ProcessReceipt::parse(&case["value"]).map(|receipt| {
            json!({"role": receipt.role, "handle": receipt.handle, "pid": receipt.pid,
                   "creation_time": receipt.creation_time})
        }));
        sylvia_base::testing::assert_same_json(&got, &case["result"], format!("{}", case["value"]));
    }
}

struct Scripted {
    identities: BTreeMap<u64, (u64, u64)>,
    exits: RefCell<VecDeque<bool>>,
    events: Events,
}

impl ProcessHandles for Scripted {
    type Owned = Token;

    fn identity(&self, handle: u64) -> CodedResult<(u64, u64)> {
        self.identities.get(&handle).copied().ok_or_else(|| {
            update_error("process_identity_unavailable", "The owned process instance could not be verified.")
        })
    }

    fn adopt(&self, handle: u64) -> CodedResult<Token> {
        Ok(Token(handle, self.events.clone()))
    }

    fn exited(&self, handle: &Token) -> CodedResult<bool> {
        push(&self.events, format!("exited:{}", handle.0));
        Ok(self.exits.borrow_mut().pop_front().unwrap_or(true))
    }
}

#[test]
fn the_process_barrier_moves_like_python() {
    for case in vectors()["barriers"].as_array().unwrap() {
        let events: Events = Arc::new(Mutex::new(Vec::new()));
        let identities = case["identities"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(key, value)| {
                (key.parse().unwrap(), (value[0].as_u64().unwrap(), value[1].as_u64().unwrap()))
            })
            .collect();
        let exits = case["exits"].as_array().unwrap().iter().map(|value| value == true).collect();
        let api = Scripted { identities, exits: RefCell::new(exits), events: events.clone() };
        let result = (|| -> CodedResult<J> {
            let receipts = case["receipts"]
                .as_array()
                .unwrap()
                .iter()
                .map(ProcessReceipt::parse)
                .collect::<CodedResult<Vec<_>>>()?;
            let mut barrier = OwnedProcesses::new(&receipts, api)?;
            let first = barrier.all_exited()?;
            barrier.close();
            barrier.close();
            Ok(json!([first, outcome(barrier.all_exited().map(J::Bool))]))
        })();
        let got = json!({"result": outcome(result), "events": events.lock().unwrap().clone()});
        sylvia_base::testing::assert_same_json(
            &got,
            &json!({"result": case["result"], "events": case["events"]}),
            format!("{}", case["receipts"]),
        );
    }
}

#[test]
fn installer_commands_and_registered_versions_match_python() {
    for case in vectors()["commands"].as_array().unwrap() {
        if let Some(arguments) = case.get("list2cmdline") {
            let arguments: Vec<&str> =
                arguments.as_array().unwrap().iter().map(|a| a.as_str().unwrap()).collect();
            assert_eq!(installed::list2cmdline(&arguments), case["result"].as_str().unwrap());
            continue;
        }
        // Windows paths: only meaningful where `Path` parses them as Windows does.
        if cfg!(windows) {
            let path = |key: &str| Path::new(case[key].as_str().unwrap()).components().collect::<PathBuf>();
            let got = outcome(installed::nsis_command_line(&path("artifact"), &path("live")).map(J::String));
            sylvia_base::testing::assert_same_json(
                &got,
                &case["result"],
                format!("{} {}", case["artifact"], case["live"]),
            );
        }
    }
    for case in vectors()["versions"].as_array().unwrap() {
        let version = Version::parse_text(case["version"].as_str().unwrap()).unwrap();
        let got: Vec<String> = installed::registered_versions(&version).into_iter().collect();
        sylvia_base::testing::assert_same_json(
            &json!(got),
            &case["registered"],
            format!("{}", case["version"]),
        );
    }
}

#[test]
fn registry_values_convert_like_reg2py() {
    for case in vectors()["registry"].as_array().unwrap() {
        let value = |raw: &J| {
            let kind = raw[0].as_u64().unwrap() as u32;
            Some((kind, hex::decode(raw[1].as_str().unwrap()).unwrap()))
        };
        let uninstall = case["uninstall"].as_object().unwrap();
        let values: Vec<_> =
            installed::UNINSTALL_VALUES.iter().map(|name| uninstall.get(*name).and_then(value)).collect();
        let default = (!case["product"].is_null()).then(|| value(&case["product"])).flatten();
        let got = match installed::registration_from(&values, default) {
            // The row in its own order (Python's dict order), each value as its UTF-16 units.
            Ok(row) => {
                json!({"ok": J::Object(row.iter().map(|(name, units)| (name.clone(), json!(units))).collect())})
            }
            Err(error) => json!({"error": [error.code, error.message]}),
        };
        sylvia_base::testing::assert_same_json(&got, &case["result"], format!("{}", case["name"]));
    }
}

#[test]
fn helper_failures_report_python_codes() {
    for case in vectors()["failures"].as_array().unwrap() {
        let error = match case["case"].as_str().unwrap() {
            "update error" => update_error("disk_full", "full"),
            "update error with an odd code" => update_error("Disk-Full", "full"),
            "owner conflict" => CodedError::new("command_rejected", "held").with_error_type("OwnerConflict"),
            "value error" => CodedError::new("command_rejected", "x").with_error_type("ValueError"),
            _ => {
                if !cfg!(windows) {
                    continue; // `winerror` exists only on Windows.
                }
                let code = case["winerror"].as_i64().unwrap() as i32;
                sylvia_owner::desktop_update::storage::os_error(&std::io::Error::from_raw_os_error(code))
            }
        };
        assert_eq!(helper::failure_code(&error), case["code"].as_str().unwrap(), "{}", case["case"]);
    }
}
