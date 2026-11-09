"""Mocked instance-handle checks; these tests query no native process or foreground state."""

import ctypes
import os
import subprocess
import sys

import pytest

from sylvia_gm.application.desktop_update.manifest import UpdateError
from sylvia_gm.application.desktop_update.processes import (
    WAIT_QUERY_ACCESS,
    OwnedProcesses,
    ProcessReceipt,
    WindowsHandles,
)


class Handles:
    def __init__(self):
        self.instances = {101: (12, 1000), 102: (13, 1001)}
        self.signaled = set()
        self.queries, self.closed = [], []

    def identity(self, handle):
        self.queries.append(handle)
        return self.instances[handle]

    def exited(self, handle):
        return handle in self.signaled

    def close(self, handle):
        self.closed.append(handle)


def receipts():
    return [ProcessReceipt("desktop", 101, 12, 1000), ProcessReceipt("owner", 102, 13, 1001)]


def test_held_process_instances_must_both_exit_and_handles_close_once():
    api = Handles()
    owned = OwnedProcesses(receipts(), api)
    assert not owned.all_exited()
    api.signaled.add(101)
    assert not owned.all_exited()
    api.signaled.add(102)
    assert owned.all_exited()
    owned.close()
    owned.close()
    assert api.closed == [101, 102]
    with pytest.raises(UpdateError) as stale:
        owned.all_exited()
    assert stale.value.code == "process_barrier_closed"


@pytest.mark.parametrize("actual", [(99, 1000), (12, 2000)])
def test_wrong_pid_or_reused_pid_birth_rejects_without_closing_unverified_handle(actual):
    api = Handles()
    api.instances[101] = actual
    with pytest.raises(UpdateError) as blocked:
        OwnedProcesses(receipts(), api)
    assert blocked.value.code == "process_identity_mismatch"
    assert api.closed == []


def test_second_mismatch_releases_only_the_first_verified_duplicate():
    api = Handles()
    api.instances[102] = (13, 2000)
    with pytest.raises(UpdateError):
        OwnedProcesses(receipts(), api)
    assert api.closed == [101]


@pytest.mark.parametrize(
    "bad",
    [
        [],
        receipts()[:1],
        [receipts()[0], receipts()[0]],
        [ProcessReceipt("desktop", True, 12, 1000), receipts()[1]],
        [ProcessReceipt("desktop", 0, 12, 1000), receipts()[1]],
        [ProcessReceipt("desktop", 101, 12, 0), receipts()[1]],
    ],
)
def test_malformed_or_ambiguous_receipts_make_no_native_query(bad):
    api = Handles()
    with pytest.raises(UpdateError):
        OwnedProcesses(bad, api)
    assert api.queries == api.closed == []


def test_completion_probe_failure_never_means_process_exit():
    api = Handles()
    owned = OwnedProcesses(receipts(), api)

    def failed(_handle):
        raise UpdateError("process_wait_failed", "controlled mock failure")

    api.exited = failed
    with pytest.raises(UpdateError) as blocked:
        owned.all_exited()
    assert blocked.value.code == "process_wait_failed"
    owned.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows handle component")
def test_real_windows_duplicates_wait_only_for_two_owned_windowless_children():
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    current = kernel.GetCurrentProcess()
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}
    }
    children, duplicates = [], []

    class TrackedHandles(WindowsHandles):
        def __init__(self):
            super().__init__()
            self.retained = set()

        def close(self, handle):
            super().close(handle)
            self.retained.remove(handle)

    api = TrackedHandles()
    owned = None
    try:
        for role in ("desktop", "owner"):
            child = subprocess.Popen(
                [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                env=environment,
            )
            children.append(child)
            # The source handle is retained by this Popen instance; no OpenProcess lookup occurs.
            pid, birth = api.identity(int(child._handle))
            assert pid == child.pid
            duplicate = wintypes.HANDLE()
            assert kernel.DuplicateHandle(
                current,
                int(child._handle),
                current,
                ctypes.byref(duplicate),
                WAIT_QUERY_ACCESS,
                False,
                0,
            )
            api.retained.add(duplicate.value)
            duplicates.append(ProcessReceipt(role, duplicate.value, pid, birth))
        owned = OwnedProcesses(duplicates, api)
        assert not owned.all_exited()
        children[0].stdin.close()
        assert children[0].wait(timeout=10) == 0
        assert not owned.all_exited()
        children[1].stdin.close()
        assert children[1].wait(timeout=10) == 0
        assert owned.all_exited()
    finally:
        for child in children:
            if child.stdin and not child.stdin.closed:
                child.stdin.close()
            assert child.wait(timeout=10) == 0
        if owned is not None:
            owned.close()
        for handle in tuple(api.retained):
            api.close(handle)
