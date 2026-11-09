"""One helper-owned Windows dialog reports real stages without an invented install percentage."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from typing import Any, Protocol

from .manifest import UpdateError

STAGES = {
    "waiting": "Waiting for Sylvia and its owned background work to close.",
    "applying": "Applying the verified application update.",
    "restoring": "Recovering the previous application using your selected data option.",
    "verifying": "Checking the application and its saved-data compatibility.",
    "starting": "Starting the selected Sylvia application.",
    "confirming": "Waiting for Sylvia to confirm usable startup and saved data.",
}
_ERRORS = {
    "file_access": "An application file could not be replaced. Close programs using this "
    "copy of Sylvia. If it still fails, keep the recovery files for repair.",
    "file_locked": "An application file is in use. Close any program holding this copy "
    "of Sylvia's files before opening Sylvia for recovery. Do not delete the update files.",
    "disk_full": "There is not enough free space to finish the update.",
    "installer_failed": "The installer did not finish successfully.",
    "startup_failed": "Sylvia exited before it confirmed usable startup.",
    "restart_failed": "The selected Sylvia application could not start.",
    "application_busy": "Another owner is still using the selected application or saved data.",
    "restore_choice_required": "The saved data needs a compatible repair or an explicit restore.",
    "stale_restart": "The recovery restart changed after it was reviewed.",
    "restart_limit": "Retained recovery attempts need repair review before another restart.",
}


class Progress(Protocol):
    def stage(self, phase: str) -> None: ...
    def close(self) -> None: ...
    def fail(self, code: str) -> None: ...


class ProgressWindow:
    """The UI thread alone owns its HWND; the worker exchanges only bounded stage state."""

    def __init__(self, runner: Callable[[ProgressWindow], None]) -> None:
        self.ready = threading.Event()
        self.done = threading.Event()
        self._lock = threading.Lock()
        self._phase, self._finished = "waiting", False
        self._failure: str | None = None
        self._created = False
        self._thread = threading.Thread(target=self._run, args=(runner,), name="update-status")

    def _run(self, runner: Callable[[ProgressWindow], None]) -> None:
        try:
            runner(self)
        except Exception:
            pass  # The fixed failure below never exposes native errors or machine paths.
        finally:
            self.done.set()
            self.ready.set()

    def start(self) -> ProgressWindow:
        self._thread.start()
        self.ready.wait()
        if not self._created or self.done.is_set():
            self._thread.join()
            raise UpdateError(
                "progress_unavailable", "The update status window could not open. "
                "Sylvia stays open.",
            )
        return self

    def created(self) -> None:
        self._created = True
        self.ready.set()

    def stage(self, phase: str) -> None:
        if phase not in STAGES:
            raise ValueError("Unknown update stage")
        if self.done.is_set():
            raise UpdateError("progress_lost", "The update status window is no longer available.")
        with self._lock:
            self._phase = phase

    def snapshot(self) -> tuple[str, bool, bool]:
        with self._lock:
            if self._failure is not None:
                text = _ERRORS.get(self._failure, "The update did not finish successfully.")
                return (
                    text + " Saved data and recovery files are retained. Close this window and "
                    "open Sylvia again for update recovery.", True, False,
                )
            return STAGES[self._phase], False, self._finished

    def close(self) -> None:
        with self._lock:
            self._finished = True
        self._thread.join()

    def fail(self, code: str) -> None:
        with self._lock:
            self._failure = code
        self._thread.join()  # The error remains visible until its own Close button is chosen.


def _dialog_types() -> tuple[Any, Any, Any]:
    """Commctrl task-dialog structures use one-byte packing, including on x64."""
    import ctypes as c

    callback = c.WINFUNCTYPE(
        c.c_int32, c.c_void_p, c.c_uint32, c.c_size_t, c.c_ssize_t, c.c_ssize_t,
    )

    class Button(c.Structure):
        _pack_ = 1
        _fields_ = [("identity", c.c_int32), ("text", c.c_wchar_p)]

    class Config(c.Structure):
        _pack_ = 1
        _fields_ = [
            ("size", c.c_uint32), ("parent", c.c_void_p), ("instance", c.c_void_p),
            ("flags", c.c_uint32), ("common_buttons", c.c_uint32), ("title", c.c_wchar_p),
            ("icon", c.c_void_p), ("instruction", c.c_wchar_p), ("content", c.c_wchar_p),
            ("button_count", c.c_uint32), ("buttons", c.POINTER(Button)),
            ("default_button", c.c_int32), ("radio_count", c.c_uint32), ("radios", c.c_void_p),
            ("default_radio", c.c_int32), ("verification", c.c_wchar_p),
            ("expanded", c.c_wchar_p), ("expand_label", c.c_wchar_p),
            ("collapse_label", c.c_wchar_p),
            ("footer_icon", c.c_void_p), ("footer", c.c_wchar_p), ("callback", callback),
            ("callback_data", c.c_ssize_t), ("width", c.c_uint32),
        ]

    return Config, Button, callback


def _windows_dialog(state: ProgressWindow) -> None:
    import ctypes as c

    config_type, button_type, callback_type = _dialog_types()
    controls = c.WinDLL("comctl32", use_last_error=True)
    user = c.WinDLL("user32", use_last_error=True)
    controls.TaskDialogIndirect.argtypes = [
        c.POINTER(config_type), c.POINTER(c.c_int32), c.c_void_p, c.c_void_p,
    ]
    controls.TaskDialogIndirect.restype = c.c_int32
    user.SendMessageW.argtypes = [c.c_void_p, c.c_uint32, c.c_size_t, c.c_ssize_t]
    user.SendMessageW.restype = c.c_ssize_t
    latest: tuple[str, bool, bool] | None = None
    own_window: int | None = None
    allow_close = False

    def notify(hwnd: int, message: int, _button: int, _param: int, _data: int) -> int:
        nonlocal own_window, latest, allow_close
        if message == 0:  # TDN_CREATED: capture only this dialog, never find another window.
            own_window = hwnd
            user.SendMessageW(hwnd, 1135, 1000, 0)  # TDM_ENABLE_BUTTON: disabled during apply.
            user.SendMessageW(hwnd, 1131, 1, 30)  # TDM_SET_PROGRESS_BAR_MARQUEE.
            state.created()
        elif message == 5:  # TDN_DESTROYED; no more messages may use this HWND.
            own_window = None
        elif message == 2:  # TDN_BUTTON_CLICKED: refuse premature Close/Enter/Escape.
            return 0 if allow_close else 1  # S_OK / S_FALSE.
        elif message == 4 and own_window == hwnd:  # TDN_TIMER, roughly 200 ms.
            snapshot = state.snapshot()
            if snapshot != latest:
                latest = snapshot
                text, failed, finished = snapshot
                content = c.create_unicode_buffer(text)
                user.SendMessageW(hwnd, 1132, 0, c.addressof(content))  # TDM_SET_ELEMENT_TEXT.
                if failed or finished:
                    allow_close = True
                    user.SendMessageW(hwnd, 1131, 0, 0)
                    user.SendMessageW(hwnd, 1135, 1000, 1)
                if finished:
                    user.SendMessageW(hwnd, 1126, 1000, 0)  # Close this completed dialog only.
        return 0

    callback = callback_type(notify)
    button = button_type(1000, "Close")
    config = config_type()
    config.size = c.sizeof(config_type)
    config.flags = 0x800 | 0x400 | 0x8000  # CALLBACK_TIMER | MARQUEE | CAN_BE_MINIMIZED.
    config.title = "Sylvia GM application update"
    config.instruction = "Updating Sylvia GM"
    config.content = STAGES["waiting"]
    config.button_count, config.buttons, config.default_button = 1, c.pointer(button), 1000
    config.callback = callback
    config.width = 340
    chosen = c.c_int32()
    if controls.TaskDialogIndirect(c.byref(config), c.byref(chosen), None, None) < 0:
        raise UpdateError(
            "progress_unavailable", "The Windows update status dialog could not open.",
        )


def start_progress() -> Progress:
    if sys.platform != "win32":
        raise UpdateError("unsupported_platform", "Update presentation requires Windows.")
    return ProgressWindow(_windows_dialog).start()
