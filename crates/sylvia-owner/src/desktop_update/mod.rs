//! Desktop update: `application/desktop_update` in Python.
//!
//! Ported so far: the signed manifest and its checks (`manifest.py`), release discovery
//! (`discovery.py`), the official request rules and download staging of `transport.py` over
//! a caller's transport, all of `payload.py`, `storage.py`, `drafts.py`, `launch.py`,
//! `controller.py`, `startup.py` and `runtime.py` (one function retired), `service.py`, and
//! update preparation: `preparation.py`, `checkpoint.py`, the portable
//! `replacement.py` and `restart.py`. The location checks use the shared `sylvia_base::pyurl`
//! (CPython's `urllib.parse`); `windows` holds the approved direct Windows calls.
//!
//! Recovery is ported too: `recovery.py`, `recovery_service.py` and the recovery-only owner.
//! So are the installed copy (`installed.py`) and the update helper the owner executable runs
//! (`helper.py`, `processes.py`, `progress.py`, `identity.py`). `runtime.py`'s
//! `system_child_libraries` is retired: a Rust executable has no PyInstaller DLL folder.

pub mod checkpoint;
pub mod controller;
pub mod discovery;
pub mod drafts;
pub mod helper;
pub mod identity;
pub mod installed;
pub mod launch;
pub mod manifest;
pub mod payload;
pub mod preparation;
pub mod processes;
pub mod progress;
pub mod recovery;
pub mod recovery_service;
pub mod replacement;
pub mod restart;
pub mod runtime;
pub mod service;
pub mod startup;
pub mod storage;
pub mod transport;
#[cfg(windows)]
mod windows;

use sylvia_base::CodedError;

/// `UpdateError(code, message)`: a stable updater outcome. Messages never contain submitted
/// values or credentials. Python's class is a `ValueError`, so the owner replies with its
/// code and message. Local diagnostics does not list the class, so it records `OtherError`.
pub fn update_error(code: &str, message: &str) -> CodedError {
    CodedError::new(code, message).with_error_type("UpdateError")
}
