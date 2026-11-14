"""Run the repository's Python quality checks as part of the local journey gate."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


# Type-check both supported platforms on every machine, so platform-only code is always checked.
@pytest.mark.parametrize(
    "module,arguments",
    [
        ("ruff", ("check", "src")),
        ("mypy", ("src", "--platform", "win32")),
        ("mypy", ("src", "--platform", "linux")),
    ],
    ids=["ruff", "mypy-win32", "mypy-linux"],
)
def test_python_quality(module, arguments, tmp_path):
    environment = dict(os.environ)
    environment["RUFF_CACHE_DIR"] = str(tmp_path / "ruff")
    # tmp_path is unique per case, so each platform run gets its own mypy cache.
    environment["MYPY_CACHE_DIR"] = str(tmp_path / "mypy")
    result = subprocess.run(
        [sys.executable, "-m", module, *arguments],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
