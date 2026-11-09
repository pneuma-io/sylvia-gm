"""A closed native parent's pipe must not replace a handled helper exit with Python 120."""

import ctypes
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sylvia_gm.application.desktop_update import helper, progress


def test_closed_parent_pipe_preserves_handled_helper_exit():
    source = Path(__file__).resolve().parents[1] / "src"
    program = """
import io, sys
from sylvia_gm.application.desktop_update.helper import main
print('ready', flush=True)
sys.stdin.buffer.read(1)
raise SystemExit(main(io.BytesIO(b'{}\\n')))
"""
    environment = dict(os.environ, PYTHONPATH=str(source))
    with subprocess.Popen(
        [sys.executable, "-c", program], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) as child:
        assert child.stdout is not None and child.stdin is not None
        assert child.stdout.readline() == (b"ready\r\n" if os.name == "nt" else b"ready\n")
        child.stdout.close()
        child.stdin.write(b"x")
        child.stdin.flush()
        child.stdin.close()
        code = child.wait()
        assert child.stderr is not None
        diagnostic = child.stderr.read().decode(errors="replace")
    assert code == 2, (code, diagnostic)


@pytest.mark.parametrize("winerror", [32, 33])
def test_only_native_sharing_and_lock_violations_offer_lock_remedy(winerror):
    error = OSError("fixture-only native detail, never shown")
    error.winerror = winerror
    assert helper._failure_code(error) == "file_locked"
    window = progress.ProgressWindow(lambda _: None)
    window._failure = helper._failure_code(error)
    message, failed, finished = window.snapshot()
    assert failed and not finished
    assert "Close any program holding" in message and "Do not delete" in message
    assert str(error) not in message


def test_access_denial_is_not_mislabeled_as_a_file_lock():
    error = OSError("fixture access error")
    error.winerror = 5
    assert helper._failure_code(error) == "file_access"
    assert "locked" not in progress._ERRORS[helper._failure_code(error)].lower()


@pytest.mark.skipif(os.name != "nt", reason="Windows file-sharing semantics")
def test_actual_locked_application_file_has_actionable_error_and_retry(tmp_path):
    from tests.test_desktop_update_replacement import assert_player_data, assert_tree, upgrade

    update, _, new, data, other = upgrade(tmp_path)
    update.prepare()
    locked = update.live / "sylvia-gm-owner.exe"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(locked), 0x80000000, 3, None, 3, 0, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    try:
        with pytest.raises(OSError) as failure:
            update.apply(lambda: True)
        assert failure.value.winerror in {5, 32, 33}
        assert helper._failure_code(failure.value) == (
            "file_access" if failure.value.winerror == 5 else "file_locked"
        )
        assert_player_data(data, other)
    finally:
        kernel.CloseHandle(handle)
    update.apply(lambda: True)
    assert_tree(update.live, new)
    assert_player_data(data, other)
