"""Small pytest progress/phase receipts for isolated deterministic journey workers."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path


def record(row, variable="SYLVIA_JOURNEY_TIMINGS"):
    if target := os.environ.get(variable):
        with Path(target).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, separators=(",", ":")) + "\n")


@contextmanager
def phase(name):
    """Measure fixture work separately from mechanics; never change its result."""
    started = time.perf_counter()
    try:
        yield
    finally:
        record(
            {
                "phase": name,
                "seconds": time.perf_counter() - started,
                "case": os.environ.get("PYTEST_CURRENT_TEST"),
            },
            "SYLVIA_JOURNEY_FIXTURE_TIMINGS",
        )


def timed(name):
    def decorate(function):
        @wraps(function)
        def measured(*args, **kwargs):
            with phase(name):
                return function(*args, **kwargs)

        return measured

    return decorate


def pytest_runtest_logstart(nodeid, location):
    print(
        "\nJOURNEY "
        + json.dumps({"event": "start", "case": nodeid, "monotonic": time.monotonic()}),
        flush=True,
    )


def pytest_runtest_logreport(report):
    row = {
        "event": "phase",
        "case": report.nodeid,
        "phase": report.when,
        "seconds": report.duration,
        "outcome": report.outcome,
    }
    record(row)
    if report.when == "teardown" or report.failed or report.skipped:
        print("\nJOURNEY " + json.dumps(row), flush=True)
    if report.failed:
        print(str(report.longrepr), flush=True)
