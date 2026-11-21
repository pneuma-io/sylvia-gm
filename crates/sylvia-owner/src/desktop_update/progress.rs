//! `application/desktop_update/progress.py`: one helper-owned Windows dialog reports the real
//! stages, with no invented percentage.
//!
//! The window state ([`ProgressWindow`]) and the dialog's notification rules
//! ([`DialogCallback`]) are plain code, tested with fakes in every check. Only
//! [`start_progress`] opens a real task dialog, through `windows.rs`.

use super::update_error;
use std::sync::{Arc, Condvar, Mutex};
use sylvia_base::{CodedError, CodedResult};

/// `ProgressPhase`.
pub mod phase {
    pub const WAITING: &str = "waiting";
    pub const APPLYING: &str = "applying";
    pub const RESTORING: &str = "restoring";
    pub const VERIFYING: &str = "verifying";
    pub const STARTING: &str = "starting";
    pub const CONFIRMING: &str = "confirming";
    /// Every phase, in Python's order.
    pub const ALL: [&str; 6] = [WAITING, APPLYING, RESTORING, VERIFYING, STARTING, CONFIRMING];
}

/// `PROGRESS_MOVES`: any stage may follow any stage.
pub const PROGRESS_MOVES: [(&str, &[&str]); 6] = [
    (phase::WAITING, &phase::ALL),
    (phase::APPLYING, &phase::ALL),
    (phase::RESTORING, &phase::ALL),
    (phase::VERIFYING, &phase::ALL),
    (phase::STARTING, &phase::ALL),
    (phase::CONFIRMING, &phase::ALL),
];

/// `STAGES`.
pub fn stage_text(phase: &str) -> Option<&'static str> {
    Some(match phase {
        phase::WAITING => "Waiting for Sylvia and its owned background work to close.",
        phase::APPLYING => "Applying the verified application update.",
        phase::RESTORING => "Recovering the previous application using your selected data option.",
        phase::VERIFYING => "Checking the application and its saved-data compatibility.",
        phase::STARTING => "Starting the selected Sylvia application.",
        phase::CONFIRMING => "Waiting for Sylvia to confirm usable startup and saved data.",
        _ => return None,
    })
}

/// `_ERRORS`.
fn error_text(code: &str) -> &'static str {
    match code {
        "file_access" => "An application file could not be replaced. Close programs using this copy of Sylvia. If it still fails, keep the recovery files for repair.",
        "file_locked" => "An application file is in use. Close any program holding this copy of Sylvia's files before opening Sylvia for recovery. Do not delete the update files.",
        "disk_full" => "There is not enough free space to finish the update.",
        "installer_failed" => "The installer did not finish successfully.",
        "startup_failed" => "Sylvia exited before it confirmed usable startup.",
        "restart_failed" => "The selected Sylvia application could not start.",
        "application_busy" => "Another owner is still using the selected application or saved data.",
        "restore_choice_required" => "The saved data needs a compatible repair or an explicit restore.",
        "stale_restart" => "The recovery restart changed after it was reviewed.",
        "restart_limit" => "Retained recovery attempts need repair review before another restart.",
        _ => "The update did not finish successfully.",
    }
}

/// `Progress`: what the helper reports through.
pub trait Progress {
    fn stage(&mut self, phase: &str) -> CodedResult<()>;
    fn close(&mut self);
    fn fail(&mut self, code: &str);
}

/// What one look at the window state shows: its text, whether it failed, whether it finished.
pub type Snapshot = (String, bool, bool);

#[derive(Debug, Default)]
struct State {
    phase: &'static str,
    finished: bool,
    failure: Option<String>,
    created: bool,
    ready: bool,
    done: bool,
}

/// The state the window's thread and the helper share: bounded stage data only.
#[derive(Debug, Default)]
pub struct Shared {
    state: Mutex<State>,
    changed: Condvar,
}

impl Shared {
    fn lock(&self) -> std::sync::MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// `created()`: the dialog exists.
    pub fn created(&self) {
        let mut state = self.lock();
        state.created = true;
        state.ready = true;
        self.changed.notify_all();
    }

    /// `snapshot()`.
    pub fn snapshot(&self) -> Snapshot {
        let state = self.lock();
        if let Some(failure) = &state.failure {
            return (
                format!(
                    "{} Saved data and recovery files are retained. Close this window and open Sylvia again for update recovery.",
                    error_text(failure)
                ),
                true,
                false,
            );
        }
        (stage_text(state.phase).unwrap_or_default().to_string(), false, state.finished)
    }

    /// `done.is_set()`.
    pub fn is_done(&self) -> bool {
        self.lock().done
    }

    /// `done.wait(timeout)`: wait until the window's thread ends or `timeout` passes.
    pub fn wait_done(&self, timeout: std::time::Duration) -> bool {
        let state = self.lock();
        let (state, _) = self
            .changed
            .wait_timeout_while(state, timeout, |state| !state.done)
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        state.done
    }

    fn finish(&self) {
        let mut state = self.lock();
        state.done = true;
        state.ready = true;
        self.changed.notify_all();
    }

    /// A test or caller may set the failure directly (`state._failure = ...`).
    pub fn set_failure(&self, code: &str) {
        self.lock().failure = Some(code.to_string());
    }
}

/// `ProgressWindow`: the window's own thread alone owns its window; the helper exchanges
/// only stage state with it.
pub struct ProgressWindow {
    shared: Arc<Shared>,
    runner: Option<Box<dyn FnOnce(Arc<Shared>) + Send>>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl ProgressWindow {
    /// `ProgressWindow(runner)`.
    pub fn new(runner: impl FnOnce(Arc<Shared>) + Send + 'static) -> Self {
        let shared = Arc::new(Shared::default());
        shared.lock().phase = phase::WAITING;
        Self { shared, runner: Some(Box::new(runner)), thread: None }
    }

    /// The shared state.
    pub fn shared(&self) -> Arc<Shared> {
        self.shared.clone()
    }

    /// `start()`: run the window on its own thread and wait until it exists, or refuse.
    pub fn start(mut self) -> CodedResult<Self> {
        let runner = self.runner.take().expect("started once");
        let shared = self.shared.clone();
        let thread = std::thread::Builder::new().name("update-status".into()).spawn(move || {
            // The runner's own failures are never shown: the fixed text below is.
            let finish = shared.clone();
            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(move || runner(shared)));
            finish.finish();
        });
        let unavailable = || {
            update_error(
                "progress_unavailable",
                "The update status window could not open. Sylvia stays open.",
            )
        };
        let Ok(thread) = thread else {
            return Err(unavailable());
        };
        let created = {
            let state = self.shared.lock();
            let state = self
                .shared
                .changed
                .wait_while(state, |state| !state.ready)
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            state.created && !state.done
        };
        if !created {
            let _ = thread.join();
            return Err(unavailable());
        }
        self.thread = Some(thread);
        Ok(self)
    }

    fn join(&mut self) {
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

impl Progress for ProgressWindow {
    /// `stage(phase)`.
    fn stage(&mut self, phase: &str) -> CodedResult<()> {
        let Some(known) = phase::ALL.iter().find(|known| **known == phase) else {
            return Err(
                CodedError::new("command_rejected", "Unknown update stage").with_error_type("ValueError")
            );
        };
        let mut state = self.shared.lock();
        if state.done {
            return Err(update_error("progress_lost", "The update status window is no longer available."));
        }
        state.phase = known;
        Ok(())
    }

    /// `close()`.
    fn close(&mut self) {
        self.shared.lock().finished = true;
        self.join();
    }

    /// `fail(code)`: the error stays visible until its own Close button is chosen.
    fn fail(&mut self, code: &str) {
        self.shared.set_failure(code);
        self.join();
    }
}

/// Task-dialog notifications and messages (`commctrl.h`).
pub mod message {
    pub const TDN_CREATED: u32 = 0;
    pub const TDN_BUTTON_CLICKED: u32 = 2;
    pub const TDN_TIMER: u32 = 4;
    pub const TDN_DESTROYED: u32 = 5;
    pub const TDM_CLICK_BUTTON: u32 = 1126;
    pub const TDM_SET_PROGRESS_BAR_MARQUEE: u32 = 1131;
    pub const TDM_SET_ELEMENT_TEXT: u32 = 1132;
    pub const TDM_ENABLE_BUTTON: u32 = 1135;
    /// The one button.
    pub const CLOSE_BUTTON: usize = 1000;
    /// `TDF_CALLBACK_TIMER | TDF_SHOW_MARQUEE_PROGRESS_BAR | TDF_CAN_BE_MINIMIZED`.
    pub const FLAGS: i32 = 0x800 | 0x400 | 0x8000;
}

/// Where the dialog's messages go: the real window, or a test's record.
pub trait Messages {
    fn send(&mut self, window: isize, message: u32, wparam: usize, lparam: isize);
    /// `TDM_SET_ELEMENT_TEXT` with the text as a NUL-terminated wide string.
    fn send_text(&mut self, window: isize, message: u32, wparam: usize, text: &str);
}

/// The `notify` callback of `_windows_dialog`: it captures only this dialog, never finds
/// another window, refuses an early Close and never sends to a destroyed window.
pub struct DialogCallback<M: Messages> {
    pub state: Arc<Shared>,
    pub messages: M,
    latest: Option<Snapshot>,
    own_window: Option<isize>,
    allow_close: bool,
}

impl<M: Messages> DialogCallback<M> {
    pub fn new(state: Arc<Shared>, messages: M) -> Self {
        Self { state, messages, latest: None, own_window: None, allow_close: false }
    }

    /// `notify(hwnd, message, button, param, data)`: the callback's HRESULT.
    pub fn notify(&mut self, window: isize, notification: u32) -> i32 {
        use message::*;
        match notification {
            TDN_CREATED => {
                self.own_window = Some(window);
                self.messages.send(window, TDM_ENABLE_BUTTON, CLOSE_BUTTON, 0); // Disabled while applying.
                self.messages.send(window, TDM_SET_PROGRESS_BAR_MARQUEE, 1, 30);
                self.state.created();
            }
            TDN_DESTROYED => self.own_window = None,
            // S_OK closes; S_FALSE refuses a premature Close, Enter or Escape.
            TDN_BUTTON_CLICKED => return if self.allow_close { 0 } else { 1 },
            TDN_TIMER if self.own_window == Some(window) => {
                let snapshot = self.state.snapshot();
                if self.latest.as_ref() != Some(&snapshot) {
                    self.latest = Some(snapshot.clone());
                    let (text, failed, finished) = snapshot;
                    self.messages.send_text(window, TDM_SET_ELEMENT_TEXT, 0, &text);
                    if failed || finished {
                        self.allow_close = true;
                        self.messages.send(window, TDM_SET_PROGRESS_BAR_MARQUEE, 0, 0);
                        self.messages.send(window, TDM_ENABLE_BUTTON, CLOSE_BUTTON, 1);
                    }
                    if finished {
                        self.messages.send(window, TDM_CLICK_BUTTON, CLOSE_BUTTON, 0);
                        // This dialog only.
                    }
                }
            }
            _ => {}
        }
        0
    }
}

#[cfg(windows)]
mod native {
    use super::super::windows::{send_message, send_text, task_dialog, DialogText};
    use super::*;

    struct Window;

    impl Messages for Window {
        fn send(&mut self, window: isize, message: u32, wparam: usize, lparam: isize) {
            send_message(window, message, wparam, lparam);
        }

        fn send_text(&mut self, window: isize, message: u32, wparam: usize, text: &str) {
            send_text(window, message, wparam, text);
        }
    }

    /// `_windows_dialog(state)`.
    pub fn dialog(state: Arc<Shared>) -> CodedResult<()> {
        let text = DialogText {
            title: "Sylvia GM application update",
            instruction: "Updating Sylvia GM",
            content: stage_text(phase::WAITING).unwrap_or_default(),
            button: "Close",
            button_id: message::CLOSE_BUTTON as i32,
            flags: message::FLAGS,
            width: 340,
        };
        let mut callback = DialogCallback::new(state, Window);
        task_dialog(&text, &mut |window, notification| callback.notify(window, notification)).map_err(|()| {
            update_error("progress_unavailable", "The Windows update status dialog could not open.")
        })
    }
}

/// `sizeof(config)` and `sizeof(button)` on this target.
#[cfg(windows)]
pub fn dialog_structure_sizes() -> (usize, usize) {
    super::windows::dialog_structure_sizes()
}

/// `start_progress()`: the helper's one status window.
pub fn start_progress() -> CodedResult<ProgressWindow> {
    #[cfg(windows)]
    {
        ProgressWindow::new(|state| {
            let _ = native::dialog(state);
        })
        .start()
    }
    #[cfg(not(windows))]
    {
        Err(update_error("unsupported_platform", "Update presentation requires Windows."))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    #[test]
    fn the_window_closes_after_success_and_shows_no_percentage() {
        let observed = Arc::new(Mutex::new(Vec::new()));
        let seen = observed.clone();
        let window = ProgressWindow::new(move |state| {
            state.created();
            loop {
                let snapshot = state.snapshot();
                seen.lock().unwrap().push(snapshot.clone());
                if snapshot.2 {
                    return;
                }
                std::thread::sleep(Duration::from_millis(1));
            }
        });
        let mut window = window.start().unwrap();
        window.stage("applying").unwrap();
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        while !observed.lock().unwrap().iter().any(|row| row.0 == stage_text("applying").unwrap()) {
            assert!(std::time::Instant::now() < deadline);
            std::thread::sleep(Duration::from_millis(1));
        }
        window.close();
        assert!(window.shared().is_done());
        assert!(observed.lock().unwrap().iter().all(|row| !row.0.contains('%')));
    }

    #[test]
    fn an_error_stays_until_acknowledged_and_never_shows_raw_errors() {
        let (release_tx, release_rx) = std::sync::mpsc::channel::<()>();
        let (seen_tx, seen_rx) = std::sync::mpsc::channel::<String>();
        let window = ProgressWindow::new(move |state| {
            state.created();
            loop {
                let (text, failed, _) = state.snapshot();
                if failed {
                    seen_tx.send(text).unwrap();
                    let _ = release_rx.recv();
                    return;
                }
                std::thread::sleep(Duration::from_millis(1));
            }
        });
        let mut window = window.start().unwrap();
        let failing = std::thread::spawn(move || {
            window.fail("private-machine-path-or-error");
            window
        });
        let text = seen_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        assert!(!failing.is_finished());
        assert!(!text.contains("private-machine-path"));
        assert!(text.contains("recovery files are retained"));
        release_tx.send(()).unwrap();
        let mut window = failing.join().unwrap();
        window.close();
    }

    #[test]
    fn a_window_that_never_opens_refuses_and_a_lost_window_refuses_stages() {
        let refused = ProgressWindow::new(|_| {}).start().err().unwrap();
        assert_eq!(refused.code, "progress_unavailable");
        let mut state = ProgressWindow::new(|_| {});
        state.shared.finish();
        assert_eq!(state.stage("applying").unwrap_err().code, "progress_lost");
        let unknown = ProgressWindow::new(|_| {}).stage("percent").unwrap_err();
        assert_eq!(unknown.error_type, Some("ValueError"));
    }

    #[derive(Default)]
    struct Record {
        sent: Vec<(isize, u32, usize, isize)>,
        texts: Vec<String>,
    }

    impl Messages for &mut Record {
        fn send(&mut self, window: isize, message: u32, wparam: usize, lparam: isize) {
            self.sent.push((window, message, wparam, lparam));
        }

        fn send_text(&mut self, window: isize, message: u32, wparam: usize, text: &str) {
            self.sent.push((window, message, wparam, -1));
            self.texts.push(text.to_string());
        }
    }

    /// Python's `test_task_dialog_packing_callback_timer_and_own_window_close_rules...`.
    #[test]
    fn the_dialog_callback_keeps_to_its_own_window_and_close_rules() {
        use message::*;
        assert_eq!(FLAGS, 0x8C00);
        assert_eq!(FLAGS & 8, 0, "no unrequested dialog cancellation");
        #[cfg(all(windows, target_pointer_width = "64"))]
        assert_eq!(dialog_structure_sizes(), (160, 12));
        let mut window = ProgressWindow::new(|_| {});
        let mut record = Record::default();
        let mut callback = DialogCallback::new(window.shared(), &mut record);
        callback.notify(12345, TDN_CREATED);
        assert_eq!(callback.notify(12345, TDN_BUTTON_CLICKED), 1);
        window.stage("applying").unwrap();
        callback.notify(12345, TDN_TIMER);
        assert_eq!(callback.messages.texts.last().unwrap(), stage_text("applying").unwrap());
        window.shared().set_failure("disk_full");
        callback.notify(12345, TDN_TIMER);
        assert!(callback.messages.texts.last().unwrap().contains("not enough free space"));
        assert_eq!(callback.notify(12345, TDN_BUTTON_CLICKED), 0);
        callback.notify(12345, TDN_DESTROYED);
        let count = callback.messages.sent.len();
        callback.notify(12345, TDN_TIMER);
        assert_eq!(callback.messages.sent.len(), count, "never send to a destroyed window");
        assert!(callback.messages.sent.iter().all(|row| row.0 == 12345));
        assert_eq!(
            callback.messages.sent[..2],
            [(12345, TDM_ENABLE_BUTTON, CLOSE_BUTTON, 0), (12345, TDM_SET_PROGRESS_BAR_MARQUEE, 1, 30)]
        );
        assert!(window.shared().lock().created);
    }

    #[test]
    fn a_finished_window_clicks_its_own_close_button() {
        use message::*;
        let mut window = ProgressWindow::new(|_| {});
        let mut record = Record::default();
        let mut callback = DialogCallback::new(window.shared(), &mut record);
        callback.notify(7, TDN_CREATED);
        callback.notify(8, TDN_TIMER); // Another window's timer is ignored.
        assert_eq!(callback.messages.sent.len(), 2);
        window.shared().lock().finished = true;
        callback.notify(7, TDN_TIMER);
        callback.notify(7, TDN_TIMER); // Unchanged: nothing more is sent.
        let tail: Vec<u32> = callback.messages.sent[2..].iter().map(|row| row.1).collect();
        assert_eq!(
            tail,
            [TDM_SET_ELEMENT_TEXT, TDM_SET_PROGRESS_BAR_MARQUEE, TDM_ENABLE_BUTTON, TDM_CLICK_BUTTON]
        );
        assert_eq!(callback.notify(7, TDN_BUTTON_CLICKED), 0);
        let _ = window.stage("waiting");
    }
}
