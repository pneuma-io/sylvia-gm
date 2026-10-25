"""Wait on native-duplicated owned process instances; never discover processes by PID or name."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .manifest import UpdateError

# SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION; no process-control access is required.
WAIT_QUERY_ACCESS = 0x0010_1000


@dataclass(frozen=True)
class ProcessReceipt:
    role: str
    handle: int
    pid: int
    creation_time: int

    @classmethod
    def parse(cls, value: object) -> ProcessReceipt:
        if (
            not isinstance(value, dict)
            or set(value) != {"role", "handle", "pid", "creation_time"}
            or value["role"] not in ("desktop", "owner", "dictation")
            or type(value["handle"]) is not int
            or not 0 < value["handle"] < 2**64
            or type(value["pid"]) is not int
            or not 0 < value["pid"] < 2**32
            or type(value["creation_time"]) is not int
            or not 0 < value["creation_time"] < 2**64
        ):
            raise UpdateError("invalid_process_receipt", "The owned process receipt is invalid.")
        return cls(str(value["role"]), value["handle"], value["pid"], value["creation_time"])


class ProcessHandles(Protocol):
    def identity(self, handle: int) -> tuple[int, int]: ...
    def exited(self, handle: int) -> bool: ...
    def close(self, handle: int) -> None: ...


class WindowsHandles:
    """Handles are duplicated into this helper by its native parent, not opened by numeric PID."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise UpdateError(
                "unsupported_platform", "This updater process barrier requires Windows."
            )
        import ctypes
        from ctypes import wintypes

        self._ctypes, self._types = ctypes, wintypes
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.GetProcessId.argtypes = [wintypes.HANDLE]
        self._kernel.GetProcessId.restype = wintypes.DWORD
        self._kernel.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            *([ctypes.POINTER(wintypes.FILETIME)] * 4),
        ]
        self._kernel.GetProcessTimes.restype = wintypes.BOOL
        self._kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._kernel.WaitForSingleObject.restype = wintypes.DWORD
        self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel.CloseHandle.restype = wintypes.BOOL

    def identity(self, handle: int) -> tuple[int, int]:
        values = [self._types.FILETIME() for _ in range(4)]
        pid = int(self._kernel.GetProcessId(handle))
        if not pid or not self._kernel.GetProcessTimes(
            handle, *(self._ctypes.byref(value) for value in values)
        ):
            raise UpdateError(
                "process_identity_unavailable", "The owned process instance could not be verified."
            )
        birth = (int(values[0].dwHighDateTime) << 32) | int(values[0].dwLowDateTime)
        return pid, birth

    def exited(self, handle: int) -> bool:
        result = int(self._kernel.WaitForSingleObject(handle, 0))
        if result not in (0, 258):
            raise UpdateError(
                "process_wait_failed", "The owned process completion could not be verified."
            )
        return result == 0

    def close(self, handle: int) -> None:
        if not self._kernel.CloseHandle(handle):
            raise UpdateError(
                "process_handle_close_failed", "An owned process handle could not be released."
            )


class OwnedProcesses:
    """Retained kernel instances survive PID reuse and permit no forced process termination."""

    def __init__(self, receipts: Sequence[ProcessReceipt], api: ProcessHandles) -> None:
        validated = tuple(
            ProcessReceipt.parse(
                {
                    "role": item.role,
                    "handle": item.handle,
                    "pid": item.pid,
                    "creation_time": item.creation_time,
                }
            )
            for item in receipts
        )
        roles = {item.role for item in validated}
        if (
            not 2 <= len(validated) <= 3
            or not {"desktop", "owner"} <= roles
            or len(roles) != len(validated)
            or len({item.handle for item in validated}) != len(validated)
            or len({item.pid for item in validated}) != len(validated)
        ):
            raise UpdateError(
                "invalid_process_receipt", "The owned process set is incomplete or ambiguous."
            )
        self._api = api
        self._owned: list[ProcessReceipt] = []
        self._closed = False
        try:
            for receipt in validated:
                if api.identity(receipt.handle) != (receipt.pid, receipt.creation_time):
                    raise UpdateError(
                        "process_identity_mismatch",
                        "An owned process instance changed. No update was applied.",
                    )
                self._owned.append(receipt)
        except Exception:
            # Release only verified duplicates; never close an unverified handle.
            self.close()
            raise

    def all_exited(self) -> bool:
        if self._closed:
            raise UpdateError(
                "process_barrier_closed", "The owned process barrier is no longer available."
            )
        return all(self._api.exited(receipt.handle) for receipt in self._owned)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: Exception | None = None
        for receipt in self._owned:
            try:
                self._api.close(receipt.handle)
            except Exception as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error
