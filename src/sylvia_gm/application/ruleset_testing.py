"""Bounded process isolation for saved Builder Ruleset tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import ExitStack, suppress
from pathlib import Path
from typing import Any, BinaryIO


def _stop_worker(process: subprocess.Popen) -> None:
    """Do not leave a disposable worker alive if cancellation or the caller raises."""
    if process.poll() is None:
        process.kill()
        process.wait()


def run_ruleset_tests(
    archive: BinaryIO,
    *,
    timeout_seconds: float = 120.0,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Test an exact archive in a child process; termination never touches live campaign state."""
    if timeout_seconds <= 0:
        raise ValueError("Scenario timeout must be positive")
    with tempfile.TemporaryDirectory(prefix="sylvia-ruleset-test-") as temporary:
        root = Path(temporary)
        source, destination = root / "candidate.package", root / "result.json"
        position = archive.tell()
        try:
            archive.seek(0)
            data = archive.read(128 * 1024 * 1024 + 1)
        finally:
            archive.seek(position)
        if len(data) > 128 * 1024 * 1024:
            return {
                "status": "error",
                "scenarios": [],
                "message": "Test package exceeds the size limit.",
            }
        source.write_bytes(data)
        command = [sys.executable]
        if getattr(sys, "frozen", False):
            command.append("--ruleset-test-worker")
        else:
            command.extend(["-m", "sylvia_gm.interfaces.ruleset_test_worker"])
        command.extend(["--archive", str(source), "--result", str(destination)])
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        with subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **options,
        ) as process, ExitStack() as cleanup:
            cleanup.callback(_stop_worker, process)
            deadline = time.monotonic() + timeout_seconds
            while process.poll() is None:
                status = "cancelled" if cancelled and cancelled() else None
                if status is None and time.monotonic() >= deadline:
                    status = "timed_out"
                if status:
                    process.kill()
                    process.wait()
                    return {
                        "status": status,
                        "scenarios": [],
                        "message": "Scenario run did not finish.",
                    }
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=0.1)
            if process.returncode != 0 or not destination.is_file():
                return {
                    "status": "error",
                    "scenarios": [],
                    "message": "Scenario worker did not return results.",
                }
        if destination.stat().st_size > 4 * 1024 * 1024:
            return {
                "status": "error",
                "scenarios": [],
                "message": "Scenario results exceed the size limit.",
            }
        try:
            result = json.loads(destination.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError):
            return {
                "status": "error",
                "scenarios": [],
                "message": "Scenario worker returned invalid results.",
            }
        if not isinstance(result, dict) or result.get("status") not in {
            "passed",
            "failed",
            "error",
            "missing_tests",
            "incomplete_coverage",
        }:
            return {
                "status": "error",
                "scenarios": [],
                "message": "Scenario worker returned invalid results.",
            }
        return result
