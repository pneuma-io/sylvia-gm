"""Abrupt source-process exits around inert file publication, not packaged native proof."""

import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from sylvia_gm.application.desktop_update.replacement import PortableReplacement
from tests.test_desktop_update_replacement import (
    assert_player_data,
    assert_tree,
    mixed_upgrade,
)

CRASH_EXIT = 73
CHILD = r"""
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from sylvia_gm.application.desktop_update import replacement
from sylvia_gm.application.desktop_update.manifest import PayloadFile
from tests.test_desktop_update_manifest import manifest_fixture, parse

root, action, target_name, position = sys.argv[1:]
root = Path(root)
rows = json.loads((root / "new-inventory.json").read_bytes())
manifest = replace(parse(manifest_fixture()), files=tuple(PayloadFile(**row) for row in rows))
update = replacement.PortableReplacement(root / "copy", root / "update-operation", manifest)
target = root / target_name
original = replacement.os.replace

def crash():
    with (root / "crash-witness.json").open("x", encoding="utf-8") as stream:
        json.dump({"pid": os.getpid(), "action": action,
                   "target": target_name, "position": position}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os._exit(73)  # No exception unwinding, finally blocks, or parent-side termination.

def publish(source, destination):
    selected = Path(destination) == target
    if selected and target == update._journal:
        expected = "awaiting_restart" if action == "apply" else "rolled_back"
        selected = json.loads(Path(source).read_bytes())["phase"] == expected
    if selected and position == "before":
        crash()
    result = original(source, destination)
    if selected and position == "after":
        crash()
    return result

replacement.os.replace = publish
if action == "apply":
    update.apply(lambda: True)
else:
    update.rollback(lambda: True, lambda: None)
raise AssertionError("Selected publication was not reached")
"""


@pytest.mark.parametrize(
    "action,target,position,phase",
    [
        ("apply", "copy/Sylvia GM.exe", "before", "applying"),
        ("apply", "copy/Sylvia GM.exe", "after", "applying"),
        ("apply", "copy/new/nested.lib", "after", "applying"),
        ("apply", "update-operation/retired/licenses/retired-notice.txt", "after", "applying"),
        ("apply", "update-operation/replacement.json", "before", "applying"),
        ("apply", "update-operation/replacement.json", "after", "awaiting_restart"),
        ("rollback", "copy/Sylvia GM.exe", "after", "rolling_back"),
        ("rollback", "update-operation/failed-new/new/nested.lib", "after", "rolling_back"),
        ("rollback", "update-operation/replacement.json", "before", "rolling_back"),
        ("rollback", "update-operation/replacement.json", "after", "rolled_back"),
    ],
)
def test_fresh_process_recovers_after_abrupt_publication_exit(
    tmp_path, action, target, position, phase
):
    update, old, new, data, other = mixed_upgrade(tmp_path)
    if action == "rollback":
        update.apply(lambda: True)
    (tmp_path / "new-inventory.json").write_text(
        json.dumps([
            {"path": row.path, "size": row.size, "sha256": row.sha256}
            for row in update.manifest.files
        ]),
        encoding="utf-8",
    )
    source = Path(__file__).resolve().parents[1]
    environment = {
        name: value for name, value in os.environ.items()
        if name.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}
    }
    profile = tmp_path / "test-profile"
    profile.mkdir()
    environment["USERPROFILE"] = str(profile)
    environment["PYTHONPATH"] = os.pathsep.join(
        (str(source / "src"), str(source), sysconfig.get_paths()["purelib"])
    )
    flags = (
        subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
        if sys.platform == "win32" else 0
    )
    # Bypass Windows' venv redirector so the retained handle owns the actual test process.
    executable = getattr(sys, "_base_executable", None) or sys.executable
    with subprocess.Popen(
        [executable, "-B", "-c", CHILD, str(tmp_path), action, target, position],
        cwd=source, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, creationflags=flags,
    ) as child:
        output, errors = child.communicate()
        assert child.returncode == CRASH_EXIT, output + errors
    assert json.loads((tmp_path / "crash-witness.json").read_bytes()) == {
        "pid": child.pid, "action": action, "target": target, "position": position,
    }
    assert json.loads(update._journal.read_bytes())["phase"] == phase
    assert_tree(update.previous, old)
    assert_player_data(data, other)

    resumed = PortableReplacement(update.live, update.operation, update.manifest)
    if action == "apply":
        resumed.apply(lambda: True)
        resumed.apply(lambda: True)
        assert_tree(update.live, new)
        assert not (update.live / "licenses/retired-notice.txt").exists()
    resumed.rollback(lambda: True, lambda: None)
    resumed.rollback(lambda: True, lambda: None)
    resumed.verify_rollback()
    assert_tree(update.live, old)
    assert_tree(update.previous, old)
    assert not (update.live / "new/nested.lib").exists()
    assert not (update.live / "z-added.exe").exists()
    assert_player_data(data, other)
