//! Desktop update: `application/desktop_update` in Python.
//!
//! Ported so far: the signed manifest and its checks (`manifest.py`), release discovery
//! (`discovery.py`), the official request rules and download staging of `transport.py` over
//! a caller's transport, all of `payload.py`, `storage.py`, `drafts.py`, `launch.py`,
//! `controller.py`, `startup.py` and `runtime.py` (one function retired), `service.py`, and
//! update preparation for a portable copy: `preparation.py`, `checkpoint.py`, the portable
//! `replacement.py` and `restart.py`. `url` holds the parts of Python's `urllib.parse` and
//! `ipaddress` the location checks depend on; `windows` the approved direct Windows calls.
//!
//! Not ported yet: recovery (`recovery.py`, `recovery_service.py`), the installed copy
//! (`installed.py`) and the helper (`helper.py`, `processes.py`, `progress.py`). Where the
//! owner reaches one of them it answers `rust_port_pending`; in the shipped build (no trusted
//! key) it never does.

pub mod checkpoint;
pub mod controller;
pub mod discovery;
pub mod drafts;
pub mod launch;
pub mod manifest;
pub mod payload;
pub mod preparation;
pub mod replacement;
pub mod restart;
pub mod runtime;
pub mod service;
pub mod startup;
pub mod storage;
pub mod transport;
pub mod url;
#[cfg(windows)]
mod windows;

use sylvia_base::CodedError;

/// `UpdateError(code, message)`: a stable updater outcome. Messages never contain submitted
/// values or credentials. Python's class is a `ValueError`, so the owner replies with its
/// code and message. Local diagnostics does not list the class, so it records `OtherError`.
pub fn update_error(code: &str, message: &str) -> CodedError {
    CodedError::new(code, message).with_error_type("UpdateError")
}
