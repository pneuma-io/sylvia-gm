//! Ephemeral on-device speech capture. The renderer remains the only draft editor.
use serde_json::{json, Value};
use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::time::Duration;

const SCRIPT: &str = include_str!("dictation.ps1");
const START_TIMEOUT: Duration = Duration::from_secs(30);
const STOP_TIMEOUT: Duration = Duration::from_secs(40);
const MAX_LIFETIME: Duration = Duration::from_secs(150);

type Reply = Result<Value, String>;

#[derive(Default)]
struct Capture {
    cancelled: AtomicBool,
    stopping: AtomicBool,
    child: Mutex<Option<Child>>,
    input: Mutex<Option<ChildStdin>>,
    replies: Mutex<Option<mpsc::Receiver<Reply>>>,
    finished: Mutex<Option<mpsc::Sender<()>>>,
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
        let mut child = command.spawn().map_err(|_| "unavailable")?;
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
            *self.input.lock().map_err(|_| "provider")? = Some(input);
        }
        let (send, receive) = mpsc::channel();
        *self.replies.lock().map_err(|_| "provider")? = Some(receive);
        std::thread::spawn(move || {
            let mut output = BufReader::new(output);
            // Two bounded JSON replies; neither diagnostics nor arbitrary process output is exposed.
            for _ in 0..2 {
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
            }
        });
        let (finished, watch) = mpsc::channel();
        *self.finished.lock().map_err(|_| "provider")? = Some(finished);
        let weak = Arc::downgrade(self);
        std::thread::spawn(move || {
            if watch.recv_timeout(MAX_LIFETIME).is_err() {
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
            if let Some(mut child) = child.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
        if let Ok(mut finished) = self.finished.lock() {
            if let Some(finished) = finished.take() {
                let _ = finished.send(());
            }
        }
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
    active: Option<(String, Arc<Capture>)>,
    cancelled: VecDeque<String>,
}

#[derive(Default)]
pub(crate) struct Dictation(Mutex<Registry>);

// The command is constant; engine identity travels over stdin, never through shell interpolation.
fn command() -> Result<Command, String> {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let system = std::env::var_os("SystemRoot").ok_or("unavailable")?;
        let mut command = Command::new(
            std::path::PathBuf::from(system).join("System32/WindowsPowerShell/v1.0/powershell.exe"),
        );
        command.args(["-NoProfile", "-NonInteractive", "-Command", SCRIPT]);
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
            "permission" | "device" | "empty" | "timeout" | "unavailable" => error,
            _ => "provider",
        }
        .into());
    }
    Ok(reply)
}

impl Dictation {
    pub(crate) fn capabilities(&self) -> Reply {
        let capture = Arc::new(Capture::default());
        let result = capture
            .launch(command()?, json!({"mode":"capabilities"}))
            .and_then(|_| capture.receive(START_TIMEOUT))
            .and_then(checked);
        capture.close();
        result
    }

    pub(crate) fn start(&self, id: &str, engine: &str) -> Result<(), String> {
        self.start_with(id, engine, command()?)
    }

    fn start_with(&self, id: &str, engine: &str, command: Command) -> Result<(), String> {
        if !valid_id(id) || engine.len() > 128 {
            return Err("unavailable".into());
        }
        let capture = Arc::new(Capture::default());
        {
            let mut registry = self.0.lock().map_err(|_| "provider")?;
            if registry.closed {
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
        let result = capture
            .launch(command, json!({"mode":"start", "engine":engine}))
            .and_then(|_| capture.receive(START_TIMEOUT))
            .and_then(checked)
            .and_then(|reply| {
                if reply["status"] == "recording" && !capture.cancelled.load(Ordering::Acquire) {
                    Ok(())
                } else {
                    Err("unavailable".into())
                }
            });
        if result.is_err() {
            self.retire(id, &capture);
        }
        result
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
        let result = capture
            .write("stop\n")
            .and_then(|_| capture.receive(STOP_TIMEOUT))
            .and_then(checked)
            .and_then(|reply| {
                reply
                    .get("text")
                    .and_then(Value::as_str)
                    .filter(|text| text.len() <= 128_000)
                    .map(str::to_owned)
                    .ok_or("provider".into())
            });
        let cancelled = capture.cancelled.load(Ordering::Acquire);
        self.retire(id, &capture);
        if cancelled {
            Err("unavailable".into())
        } else {
            result
        }
    }

    fn retire(&self, id: &str, capture: &Arc<Capture>) {
        if let Ok(mut registry) = self.0.lock() {
            if registry
                .active
                .as_ref()
                .is_some_and(|(active, current)| active == id && Arc::ptr_eq(current, capture))
            {
                registry.active = None;
            }
        }
        capture.close();
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

    /// Permanently refuse queued starts before releasing the current capture on shutdown.
    pub(crate) fn close(&self) {
        let capture = self.0.lock().ok().and_then(|mut registry| {
            registry.closed = true;
            registry.active.take()
        });
        if let Some((_, capture)) = capture {
            capture.close();
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
}
