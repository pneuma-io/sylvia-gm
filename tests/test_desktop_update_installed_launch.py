"""Injected Windows launch refusals, not actual denied ACL or OS-consent evidence."""

import json
import sys
from contextlib import contextmanager

import pytest

from sylvia_gm.application.desktop_update import installed
from sylvia_gm.application.desktop_update.manifest import UpdateError
from tests.test_desktop_update_installed import installation


@pytest.mark.skipif(sys.platform != "win32", reason="Windows installer launch contract")
@pytest.mark.parametrize("winerror", [5, 1223], ids=["access-denied", "consent-cancelled"])
def test_refused_installer_launch_keeps_old_copy_and_never_retries_or_elevates(
    tmp_path, monkeypatch, winerror
):
    update, old, new, registration, installers, data = installation(tmp_path)
    update.prepare()
    ordinary_run = update.run
    calls, libraries = [], []

    @contextmanager
    def child_libraries():
        libraries.append("enter")
        try:
            yield
        finally:
            libraries.append("leave")

    def refused(command, **options):
        calls.append((command, options))
        assert command == installed.nsis_command_line(
            update.operation / "artifact.verified", update.live
        )
        assert options["executable"] == str(update.operation / "artifact.verified")
        assert options["shell"] is False
        assert options["creationflags"] == installed.subprocess.CREATE_NO_WINDOW
        assert options["cwd"] == update.operation
        assert set(options["env"]) <= {
            "SystemRoot", "WINDIR", "PATH", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP",
        }
        assert options["env"]["TEMP"] == options["env"]["TMP"] == str(
            update.operation / "installer-temp"
        )
        error = OSError("controlled private OS refusal")
        error.winerror = winerror
        raise error

    monkeypatch.setattr(installed, "system_child_libraries", child_libraries)
    monkeypatch.setattr(installed.subprocess, "Popen", refused)
    update.run = installed.run_nsis
    before = (data / "authority.sqlite3").read_bytes()
    with pytest.raises(UpdateError) as failure:
        update.apply(lambda: True)
    assert failure.value.code == "installer_unavailable"
    assert "controlled private" not in str(failure.value)
    assert failure.value.__cause__.winerror == winerror
    assert len(calls) == 1 and libraries == ["enter", "leave"]
    assert installers == [] and registration.row["DisplayVersion"] == "0.3.0-1"
    assert all((update.live / name).read_bytes() == raw for name, raw in old.items())
    assert (data / "authority.sqlite3").read_bytes() == before
    assert (update.live / "manual.txt").read_bytes() == b"unrelated user file"
    assert json.loads(update._journal.read_bytes())["phase"] == "applying"

    # Only an explicit caller retry invokes the unchanged ordinary installer seam.
    update.run = ordinary_run
    update.apply(lambda: True)
    assert len(installers) == 1 and len(calls) == 1
    assert all((update.live / name).read_bytes() == raw for name, raw in new.items())
    update.rollback(lambda: True, lambda: None)
    update.verify_rollback()
    assert all((update.live / name).read_bytes() == raw for name, raw in old.items())
    assert (data / "authority.sqlite3").read_bytes() == before
