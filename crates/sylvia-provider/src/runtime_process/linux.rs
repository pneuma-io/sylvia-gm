//! `_dies_with_owner()` and `_SPAWNER` (`provider/_claude_runtime.py`) on Linux: a Claude Code
//! child gets `SIGKILL` when the owner process dies, however it dies. Approved under README
//! rule 9 (manager, 23 Nov 2026).
//!
//! The parent-death signal follows the *thread* that started the child, so every such child is
//! started from one thread that lives as long as the owner process ([`spawn_from_owner_thread`]),
//! as Python's `_SPAWNER` does.

#![allow(unsafe_code)]

use std::io;
use std::os::unix::process::CommandExt;
use std::process::{Child, Command};
use std::sync::mpsc;
use std::sync::{Mutex, OnceLock};

/// Arm the child, between fork and exec, to receive `SIGKILL` when its parent dies, and end it
/// at once if the parent is already gone.
pub(super) fn die_with_owner(command: &mut Command) {
    let owner = rustix::process::getpid();
    // SAFETY: the closure runs in the forked child before exec, where only async-signal-safe
    // work is allowed. It makes two system calls through rustix (`prctl(PR_SET_PDEATHSIG)` and
    // `getppid`), which are async-signal-safe, and it allocates nothing: the error it can
    // return is made from a raw OS error code. Python's `arm()` ignores prctl's result and,
    // for the race where the owner died before prctl took effect, checks `getppid()` and
    // `os._exit(1)`s the child. Here the child exits too: an `Err` from `pre_exec` ends it
    // before exec. (The spawning owner would see the start fail, but it is the owner that is
    // gone.)
    unsafe {
        command.pre_exec(move || {
            let _ = rustix::process::set_parent_process_death_signal(Some(rustix::process::Signal::KILL));
            if rustix::process::getppid() != Some(owner) {
                return Err(io::Error::from_raw_os_error(rustix::io::Errno::SRCH.raw_os_error()));
            }
            Ok(())
        });
    }
}

type Job = (Command, mpsc::Sender<io::Result<Child>>);

/// `_SPAWNER.run(spawn)`: start the child from the one long-lived spawner thread.
pub(super) fn spawn_from_owner_thread(command: Command) -> io::Result<Child> {
    static SPAWNER: OnceLock<Mutex<mpsc::Sender<Job>>> = OnceLock::new();
    let sender = SPAWNER.get_or_init(|| {
        let (sender, jobs) = mpsc::channel::<Job>();
        std::thread::Builder::new()
            .name("sylvia-claude-spawner".into())
            .spawn(move || {
                for (mut command, done) in jobs {
                    let _ = done.send(command.spawn());
                }
            })
            .expect("the spawner thread starts");
        Mutex::new(sender)
    });
    let (done, result) = mpsc::channel();
    sender
        .lock()
        .expect("spawner lock")
        .send((command, done))
        .map_err(|_| io::Error::other("the spawner thread has stopped"))?;
    result.recv().map_err(|_| io::Error::other("the spawner thread has stopped"))?
}
