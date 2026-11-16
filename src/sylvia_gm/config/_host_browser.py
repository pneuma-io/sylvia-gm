"""Open a web address in the person's own browser."""

from __future__ import annotations

import os
import posixpath
import subprocess
import sys
import webbrowser
from collections.abc import Mapping

#: How long to wait for the desktop's opener to report a failure. Some desktops keep the
#: opener running for as long as the browser it started, so no answer in this time is success.
_OPENER_WAIT_SECONDS = 3.0


def _inside(entry: str, folders: tuple[str, ...]) -> bool:
    normal = posixpath.normpath(entry) if entry else ""
    return any(normal == folder or normal.startswith(folder + "/") for folder in folders)


def host_environment(
    environ: Mapping[str, str], *, packed_folder: str | None
) -> dict[str, str]:
    """Return the environment a program outside the packaged application should start with.

    The packaged Linux application runs with library, Python and toolkit search paths that
    point into its own bundle (the AppImage's folder and the packed owner's folder). A browser
    started with those loads the bundle's libraries instead of its own and may not start at
    all. Every entry that points into the bundle is removed, a variable left with nothing is
    dropped, and the library path the packed owner saved before changing it is put back.
    """

    result = dict(environ)
    if packed_folder is not None:
        saved = result.pop("LD_LIBRARY_PATH_ORIG", None)
        if saved is None:
            result.pop("LD_LIBRARY_PATH", None)
        else:
            result["LD_LIBRARY_PATH"] = saved
    folders = tuple(
        posixpath.normpath(folder)
        for folder in (result.get("APPDIR"), packed_folder)
        if folder
    )
    if not folders:
        return result
    for name, value in list(result.items()):
        entries = value.split(":")
        kept = [entry for entry in entries if not _inside(entry, folders)]
        if len(kept) == len(entries):
            continue
        if any(kept):
            result[name] = ":".join(kept)
        else:
            del result[name]
    return result


def _open_outside_bundle(url: str, environment: Mapping[str, str]) -> bool | None:
    """Ask the desktop's opener for the address; ``None`` when there is no opener to ask."""

    try:
        process = subprocess.Popen(  # noqa: S603 - fixed program, address passed as one argument
            ["xdg-open", url],  # noqa: S607 - found on the cleaned PATH, outside the bundle
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return None
    try:
        return process.wait(timeout=_OPENER_WAIT_SECONDS) == 0
    except subprocess.TimeoutExpired:
        return True


def open_in_browser(url: str) -> bool:
    """Open ``url`` in the default browser and say whether a browser was started."""

    if sys.platform.startswith("linux"):
        packed_folder = getattr(sys, "_MEIPASS", None) if getattr(sys, "frozen", False) else None
        if packed_folder is not None or os.environ.get("APPDIR"):
            opened = _open_outside_bundle(
                url, host_environment(os.environ, packed_folder=packed_folder)
            )
            if opened is not None:
                return opened
    try:
        return bool(webbrowser.open(url, new=2))
    except (OSError, webbrowser.Error):
        return False
