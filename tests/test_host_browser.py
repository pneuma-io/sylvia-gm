"""Opening the person's browser from the packaged Linux application.

The packaged application runs with search paths that point into its own bundle. A browser
started with them may not start, so the address is handed to the desktop's opener with every
such entry removed. Elsewhere the standard library's opener is used as before.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys

import pytest

from sylvia_gm.config import _host_browser

APPDIR = "/tmp/.mount_SylviaAbCdEf"
PACKED = "/tmp/_MEIabc123"
BUNDLE_LIBRARIES = f"{APPDIR}/usr/lib/:{APPDIR}/usr/lib/x86_64-linux-gnu/:{APPDIR}/lib/:"

# What the packaged application's owner really runs with (taken from an installed check).
PACKAGED = {
    "LD_LIBRARY_PATH": f"{PACKED}:{BUNDLE_LIBRARIES}",
    "LD_LIBRARY_PATH_ORIG": BUNDLE_LIBRARIES,
    "APPDIR": APPDIR,
    "APPIMAGE": "/home/player/Apps/Sylvia GM.AppImage",
    "GTK_PATH": f"{APPDIR}//usr/lib/gtk-3.0",
    "GDK_PIXBUF_MODULE_FILE": f"{APPDIR}//usr/lib/gdk-pixbuf-2.0/2.10.0/loaders.cache",
    "GIO_MODULE_DIR": f"{APPDIR}//usr/lib/gio/modules",
    "GSETTINGS_SCHEMA_DIR": f"{APPDIR}/usr/share/glib-2.0/schemas/:{APPDIR}//usr/share/glib-2.0/schemas",
    "XDG_DATA_DIRS": f"{APPDIR}/usr/share/:{APPDIR}/usr/share:/usr/share:",
    "PYTHONHOME": f"{APPDIR}/usr/",
    "PYTHONPATH": f"{APPDIR}/usr/share/pyshared/:",
    "PATH": f"{APPDIR}/usr/bin/:{APPDIR}/bin/:/home/player/.local/bin:/usr/bin:/bin",
    "DISPLAY": ":0",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
    "HOME": "/home/player",
}


def test_host_environment_drops_every_entry_that_points_into_the_bundle():
    cleaned = _host_browser.host_environment(PACKAGED, packed_folder=PACKED)

    assert cleaned == {
        "APPIMAGE": "/home/player/Apps/Sylvia GM.AppImage",
        "XDG_DATA_DIRS": "/usr/share:",
        "PATH": "/home/player/.local/bin:/usr/bin:/bin",
        "DISPLAY": ":0",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        "HOME": "/home/player",
    }
    assert not any(APPDIR in value or PACKED in value for value in cleaned.values())


def test_host_environment_keeps_a_library_path_the_person_set_themselves():
    own = "/opt/vendor/lib"
    environ = {
        "LD_LIBRARY_PATH": f"{PACKED}:{APPDIR}/usr/lib/:{own}",
        "LD_LIBRARY_PATH_ORIG": f"{APPDIR}/usr/lib/:{own}",
        "APPDIR": APPDIR,
    }

    assert _host_browser.host_environment(environ, packed_folder=PACKED) == {
        "LD_LIBRARY_PATH": own,
    }


def test_host_environment_of_a_packed_owner_outside_an_appimage():
    # The packed owner alone (no AppImage): its own folder goes, a saved path comes back.
    assert _host_browser.host_environment(
        {"LD_LIBRARY_PATH": PACKED, "HOME": "/home/player"}, packed_folder=PACKED
    ) == {"HOME": "/home/player"}
    assert _host_browser.host_environment(
        {"LD_LIBRARY_PATH": f"{PACKED}:/opt/lib", "LD_LIBRARY_PATH_ORIG": "/opt/lib"},
        packed_folder=PACKED,
    ) == {"LD_LIBRARY_PATH": "/opt/lib"}


def test_host_environment_leaves_an_ordinary_environment_alone():
    environ = {"PATH": "/usr/bin:/bin", "LD_LIBRARY_PATH": "/opt/lib", "HOME": "/home/dev"}

    assert _host_browser.host_environment(environ, packed_folder=None) == environ


class _Opener:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def __call__(self, command, **options):
        self.calls.append((command, options))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self

    def wait(self, timeout):
        if self.outcome is None:
            raise subprocess.TimeoutExpired("xdg-open", timeout)
        return self.outcome


def _packaged_linux(monkeypatch, opener):
    monkeypatch.setattr(_host_browser.sys, "platform", "linux")
    monkeypatch.setattr(_host_browser.sys, "frozen", True, raising=False)
    monkeypatch.setattr(_host_browser.sys, "_MEIPASS", PACKED, raising=False)
    monkeypatch.setattr(_host_browser.os, "environ", dict(PACKAGED))
    monkeypatch.setattr(_host_browser.subprocess, "Popen", opener)
    fallback = []
    monkeypatch.setattr(
        _host_browser.webbrowser, "open", lambda url, new: fallback.append(url) or True
    )
    return fallback


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [(0, True), (None, True), (3, False), (4, False)],
    ids=["opened", "opener-still-running", "no-browser", "open-failed"],
)
def test_the_packaged_linux_app_asks_the_desktop_opener_with_a_clean_environment(
    monkeypatch, outcome, expected
):
    opener = _Opener(outcome)
    fallback = _packaged_linux(monkeypatch, opener)

    assert _host_browser.open_in_browser("https://example.test/sign-in?state=1") is expected

    (command, options), = opener.calls
    assert command == ["xdg-open", "https://example.test/sign-in?state=1"]
    assert options["env"] == _host_browser.host_environment(PACKAGED, packed_folder=PACKED)
    assert "LD_LIBRARY_PATH" not in options["env"] and "PYTHONHOME" not in options["env"]
    assert options["start_new_session"] is True
    assert fallback == []


def test_without_a_desktop_opener_the_standard_search_is_still_tried(monkeypatch):
    fallback = _packaged_linux(monkeypatch, _Opener(FileNotFoundError("xdg-open")))

    assert _host_browser.open_in_browser("https://example.test/") is True
    assert fallback == ["https://example.test/"]


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_an_unpackaged_run_uses_the_standard_opener_and_reports_its_failures(
    monkeypatch, platform
):
    monkeypatch.setattr(_host_browser.sys, "platform", platform)
    monkeypatch.delattr(_host_browser.sys, "frozen", raising=False)
    monkeypatch.setattr(_host_browser.os, "environ", {"PATH": "/usr/bin"})
    monkeypatch.setattr(
        _host_browser.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("the desktop opener is for the packaged Linux app only"),
    )
    opened = []
    monkeypatch.setattr(
        _host_browser.webbrowser, "open", lambda url, new: opened.append((url, new)) or True
    )
    assert _host_browser.open_in_browser("https://example.test/") is True
    assert opened == [("https://example.test/", 2)]

    def refuse(url, new):
        raise _host_browser.webbrowser.Error("no browser")

    monkeypatch.setattr(_host_browser.webbrowser, "open", refuse)
    assert _host_browser.open_in_browser("https://example.test/") is False


@pytest.mark.runs_on("linux", reason="starts a real opener program found on PATH")
def test_a_real_opener_is_found_outside_the_bundle_and_started_clean(tmp_path, monkeypatch):
    bundle, host = tmp_path / "bundle", tmp_path / "host"
    record = tmp_path / "record.txt"
    for folder, label in ((bundle / "usr/bin", "bundle"), (host, "host")):
        folder.mkdir(parents=True)
        opener = folder / "xdg-open"
        opener.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n%s\\n%s\\n%s\\n" {label} "$1" "${{LD_LIBRARY_PATH-unset}}" '
            f'"${{PYTHONHOME-unset}}" > "{record}"\n',
            encoding="utf-8",
        )
        opener.chmod(opener.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "packed"), raising=False)
    monkeypatch.setenv("APPDIR", str(bundle))
    monkeypatch.setenv("PATH", f"{bundle}/usr/bin:{host}:{os.environ['PATH']}")
    monkeypatch.setenv("LD_LIBRARY_PATH", f"{tmp_path}/packed:{bundle}/usr/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", f"{bundle}/usr/lib")
    monkeypatch.setenv("PYTHONHOME", f"{bundle}/usr")

    assert _host_browser.open_in_browser("https://example.test/a b?c=1&d=2") is True

    assert record.read_text(encoding="utf-8").splitlines() == [
        "host",
        "https://example.test/a b?c=1&d=2",
        "unset",
        "unset",
    ]
