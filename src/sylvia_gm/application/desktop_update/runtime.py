"""Child launch hygiene for the packaged helper; no persistent environment or OS settings."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import PureWindowsPath

from .manifest import UpdateError


def child_environment() -> dict[str, str]:
    # Ordinary same-user inheritance remains in memory, never in an update receipt or log.
    result = dict(os.environ)
    if any(
        name in result for name in ("SYLVIA_GM_AUTHORITY_DATABASE", "SYLVIA_GM_OWNER_EXECUTABLE")
    ):
        raise UpdateError(
            "unsupported_launch_override",
            "Restart this copy without engineering owner/data overrides "
            "before applying an update. The selected files have not been changed.",
        )
    result["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    extraction = getattr(sys, "_MEIPASS", None)
    if isinstance(extraction, str) and "PATH" in result:
        root = PureWindowsPath(extraction)
        result["PATH"] = os.pathsep.join(
            part
            for part in result["PATH"].split(os.pathsep)
            if not PureWindowsPath(part.strip('"')).is_relative_to(root)
        )
    return result


@contextmanager
def system_child_libraries() -> Iterator[None]:
    """Temporarily undo this helper's frozen DLL directory for child creation, then restore it."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        yield
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
    kernel.GetDllDirectoryW.restype = wintypes.DWORD
    kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    kernel.SetDllDirectoryW.restype = wintypes.BOOL
    buffer = ctypes.create_unicode_buffer(32768)
    size = kernel.GetDllDirectoryW(len(buffer), buffer)
    if size >= len(buffer) or not kernel.SetDllDirectoryW(None):
        raise UpdateError(
            "child_runtime_unavailable", "The update child runtime could not be prepared."
        )
    try:
        yield
    finally:
        if not kernel.SetDllDirectoryW(buffer.value if size else None):
            raise UpdateError(
                "child_runtime_unavailable", "The helper runtime could not be restored."
            )
