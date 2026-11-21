//! `application/desktop_update/processes.py`: wait on the process instances the desktop
//! duplicated into the helper. Nothing is ever found by PID or name, and nothing is stopped.
//!
//! A receipt's handle is only a number until its process identity matches. It is then
//! adopted (on Windows into an `OwnedHandle`, closed exactly once when dropped); an
//! unverified value is never closed, as Python never closes one.

use super::update_error;
use sylvia_base::{CodedResult, Json};

/// `WAIT_QUERY_ACCESS`: `SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION`.
pub const WAIT_QUERY_ACCESS: u32 = 0x0010_1000;

/// `ProcessReceipt`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProcessReceipt {
    pub role: String,
    pub handle: u64,
    pub pid: u64,
    pub creation_time: u64,
}

/// An integer strictly between 0 and `limit` (`type(value) is int and 0 < value < limit`).
fn bounded_integer(value: &Json, limit: u128) -> Option<u64> {
    let number = value.as_u64().filter(|_| value.is_u64())?;
    (number > 0 && u128::from(number) < limit).then_some(number)
}

impl ProcessReceipt {
    /// `ProcessReceipt.parse(value)`.
    pub fn parse(value: &Json) -> CodedResult<Self> {
        let invalid = || update_error("invalid_process_receipt", "The owned process receipt is invalid.");
        let Json::Object(row) = value else {
            return Err(invalid());
        };
        let keys = ["role", "handle", "pid", "creation_time"];
        if row.len() != keys.len() || !keys.iter().all(|key| row.contains_key(*key)) {
            return Err(invalid());
        }
        let role = row["role"].as_str().filter(|role| ["desktop", "owner", "dictation"].contains(role));
        let handle = bounded_integer(&row["handle"], 1 << 64);
        let pid = bounded_integer(&row["pid"], 1 << 32);
        let creation_time = bounded_integer(&row["creation_time"], 1 << 64);
        match (role, handle, pid, creation_time) {
            (Some(role), Some(handle), Some(pid), Some(creation_time)) => {
                Ok(Self { role: role.to_string(), handle, pid, creation_time })
            }
            _ => Err(invalid()),
        }
    }
}

/// `ProcessHandles`: the operating-system side of the barrier.
pub trait ProcessHandles {
    /// The handle once it is this process's own.
    type Owned;
    /// `identity(handle)`: `(pid, creation time)` of the handle's process, read without
    /// taking ownership.
    fn identity(&self, handle: u64) -> CodedResult<(u64, u64)>;
    /// Take ownership of a handle whose identity matched.
    fn adopt(&self, handle: u64) -> CodedResult<Self::Owned>;
    /// `exited(handle)`.
    fn exited(&self, handle: &Self::Owned) -> CodedResult<bool>;
}

/// `WindowsHandles`: the handles the desktop duplicated into this helper.
#[derive(Debug)]
pub struct WindowsHandles(());

impl WindowsHandles {
    /// `WindowsHandles()`: refused off Windows.
    pub fn new() -> CodedResult<Self> {
        if cfg!(windows) {
            Ok(Self(()))
        } else {
            Err(update_error("unsupported_platform", "This updater process barrier requires Windows."))
        }
    }
}

#[cfg(windows)]
impl ProcessHandles for WindowsHandles {
    type Owned = std::os::windows::io::OwnedHandle;

    fn identity(&self, handle: u64) -> CodedResult<(u64, u64)> {
        super::windows::process_identity(handle).ok_or_else(|| {
            update_error("process_identity_unavailable", "The owned process instance could not be verified.")
        })
    }

    fn adopt(&self, handle: u64) -> CodedResult<Self::Owned> {
        // Clearing the inheritance flag is the only step that can fail; Python's child
        // creation has no counterpart, so this reports Python's child-runtime failure.
        super::windows::adopt_process(handle).map_err(|_| {
            update_error("child_runtime_unavailable", "The update child runtime could not be prepared.")
        })
    }

    fn exited(&self, handle: &Self::Owned) -> CodedResult<bool> {
        super::windows::process_exited(handle).ok_or_else(|| {
            update_error("process_wait_failed", "The owned process completion could not be verified.")
        })
    }
}

#[cfg(not(windows))]
impl ProcessHandles for WindowsHandles {
    type Owned = std::convert::Infallible;

    fn identity(&self, _handle: u64) -> CodedResult<(u64, u64)> {
        Err(update_error("unsupported_platform", "This updater process barrier requires Windows."))
    }

    fn adopt(&self, _handle: u64) -> CodedResult<Self::Owned> {
        Err(update_error("unsupported_platform", "This updater process barrier requires Windows."))
    }

    fn exited(&self, handle: &Self::Owned) -> CodedResult<bool> {
        match *handle {}
    }
}

/// `OwnedProcesses`: retained kernel instances survive PID reuse and allow no forced stop.
pub struct OwnedProcesses<A: ProcessHandles> {
    api: A,
    owned: Vec<(ProcessReceipt, A::Owned)>,
    closed: bool,
}

impl<A: ProcessHandles> OwnedProcesses<A> {
    /// `OwnedProcesses(receipts, api)`: the set must be the desktop and the owner (and at
    /// most the dictation worker), each once, each matching its process. On a mismatch the
    /// handles verified so far are released and the rest are left alone.
    pub fn new(receipts: &[ProcessReceipt], api: A) -> CodedResult<Self> {
        let roles: std::collections::HashSet<&str> = receipts.iter().map(|item| item.role.as_str()).collect();
        let handles: std::collections::HashSet<u64> = receipts.iter().map(|item| item.handle).collect();
        let pids: std::collections::HashSet<u64> = receipts.iter().map(|item| item.pid).collect();
        if !(2..=3).contains(&receipts.len())
            || !roles.contains("desktop")
            || !roles.contains("owner")
            || roles.len() != receipts.len()
            || handles.len() != receipts.len()
            || pids.len() != receipts.len()
        {
            return Err(update_error(
                "invalid_process_receipt",
                "The owned process set is incomplete or ambiguous.",
            ));
        }
        let mut owned = Vec::new();
        for receipt in receipts {
            // On an error the verified handles in `owned` are dropped, which closes them.
            if api.identity(receipt.handle)? != (receipt.pid, receipt.creation_time) {
                return Err(update_error(
                    "process_identity_mismatch",
                    "An owned process instance changed. No update was applied.",
                ));
            }
            owned.push((receipt.clone(), api.adopt(receipt.handle)?));
        }
        Ok(Self { api, owned, closed: false })
    }

    /// `all_exited()`.
    pub fn all_exited(&self) -> CodedResult<bool> {
        if self.closed {
            return Err(update_error(
                "process_barrier_closed",
                "The owned process barrier is no longer available.",
            ));
        }
        for (_, handle) in &self.owned {
            if !self.api.exited(handle)? {
                return Ok(false);
            }
        }
        Ok(true)
    }

    /// `close()`: release every owned handle once. Dropping an `OwnedHandle` cannot report a
    /// failure, so Python's `process_handle_close_failed` no longer occurs.
    pub fn close(&mut self) {
        if self.closed {
            return;
        }
        self.closed = true;
        self.owned.clear();
    }
}
