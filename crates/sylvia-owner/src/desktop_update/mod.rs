//! Desktop update: `application/desktop_update` in Python.
//!
//! Ported so far: the signed manifest and its checks (`manifest.py`), release discovery
//! (`discovery.py`), the official request rules and download staging of `transport.py` over
//! a caller's transport, `payload.verify_artifact`, all of `storage.py`, `drafts.py`,
//! `launch.py` and `controller.py`, the owner boundary of `service.py`, the ordinary-start
//! check of `startup.py` and `runtime.child_environment`. `url` holds the parts of Python's
//! `urllib.parse` and `ipaddress` the location checks depend on.
//!
//! Not ported yet: update preparation, checkpoint, replacement, restart, recovery and the
//! helper. Where the owner reaches one of them it answers `rust_port_pending`; in the shipped
//! build (no trusted key) it never does.

pub mod controller;
pub mod discovery;
pub mod drafts;
pub mod launch;
pub mod manifest;
pub mod payload;
pub mod runtime;
pub mod service;
pub mod startup;
pub mod storage;
pub mod transport;
pub mod url;

use sylvia_base::CodedError;

/// `UpdateError(code, message)`: a stable updater outcome. Messages never contain submitted
/// values or credentials. Python's class is a `ValueError`, so the owner replies with its
/// code and message. Local diagnostics does not list the class, so it records `OtherError`.
pub fn update_error(code: &str, message: &str) -> CodedError {
    CodedError::new(code, message).with_error_type("UpdateError")
}
