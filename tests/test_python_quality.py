"""Run the repository's Python quality checks as part of the local journey gate."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("module,arguments", [("ruff", ("check", "src"))])
def test_python_quality(module, arguments, tmp_path):
    environment = dict(os.environ)
    environment["RUFF_CACHE_DIR"] = str(tmp_path / "ruff")
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
