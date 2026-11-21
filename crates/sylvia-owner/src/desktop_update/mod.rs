//! Desktop update: `application/desktop_update` in Python.
//!
//! Ported so far: the signed manifest and its checks (`manifest.py`), release discovery
//! (`discovery.py`), the official request rules and download staging of `transport.py` over
//! a caller's transport, `payload.verify_artifact`, the folder and file checks and atomic
//! receipt of `storage.py`, and all of `drafts.py`. `url` holds the parts of Python's
//! `urllib.parse` and `ipaddress` the location checks depend on.

pub mod discovery;
pub mod drafts;
pub mod manifest;
pub mod payload;
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
