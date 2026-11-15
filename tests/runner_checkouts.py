"""Synthetic checkouts for runner tests carry the real runs_on collection hook, nothing else."""

import json
import sys
from pathlib import Path

from scripts import platform_exclusions

PLUGIN = Path(platform_exclusions.__file__)
OTHER_PLATFORM = "linux" if sys.platform == "win32" else "win32"


def install_runs_on_plugin(root):
    """The runner fails a group whose collection records no platform exclusions."""
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts/platform_exclusions.py").write_bytes(PLUGIN.read_bytes())
    (root / "conftest.py").write_text(
        "from scripts.platform_exclusions import pytest_collection_modifyitems  # noqa: F401\n"
    )


def record_no_platform_exclusions(kwargs):
    """A mocked collection writes what the real conftest hook writes when nothing is excluded."""
    path = Path(kwargs["env"][platform_exclusions.EXCLUSIONS_FILE_VARIABLE])
    path.write_text(json.dumps([]), encoding="utf-8")
