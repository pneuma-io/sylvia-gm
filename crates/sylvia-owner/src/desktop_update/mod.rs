//! Desktop update: `application/desktop_update` in Python.
//!
//! Only what the desktop drafts store needs is ported so far: the bounded JSON reader and
//! canonical encoding of `manifest.py`, the folder and file checks and atomic receipt of
//! `storage.py`, and all of `drafts.py`. The updater itself is a later lane 5 assignment.

pub mod drafts;
pub mod manifest;
pub mod storage;

use sylvia_base::CodedError;

/// `UpdateError(code, message)`: a stable updater outcome. Messages never contain submitted
/// values or credentials. Python's class is a `ValueError`, so the owner replies with its
/// code and message. Local diagnostics does not list the class, so it records `OtherError`.
pub fn update_error(code: &str, message: &str) -> CodedError {
    CodedError::new(code, message).with_error_type("UpdateError")
}
