"""Record real SRD Lite and Riichi Mahjong expression evaluations from the frozen Python reference.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-engine/tests/vectors/generate_expression_recorded_vectors.py

(`.venv/bin/python` on Linux.) It runs the snapshot's own tests for the bundled packages under
pytest with `record_evaluations_plugin.py` (beside this file), which records every top-level
expression evaluation: the text, each root value the evaluation read, and the result or
exception. Distinct evaluations are kept, sorted, with the root values in one shared table.
Every object keeps Python's own key order (results are compared key-order exact), so the
same value in two key orders is two table entries.
Deterministic (the plugin seeds the dice and the shuffle before each test) and checked in, one
file per platform (`.windows.json`, `.linux.json`), each recorded on that platform's reference
interpreter, because float `**` uses the platform C library's `pow`. Pytest's base
temporary folder is created beside the output and deleted afterwards. Reference tooling for the
port, retired with the Python source.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
# Per platform: float `**` goes through the C library's `pow` (the Microsoft runtime on Windows,
# glibc on Linux), which can differ in the last bit, in Python as in Rust.
PLATFORM = "windows" if sys.platform == "win32" else "linux"
OUTPUT = HERE / f"expression_recorded_vectors.{PLATFORM}.json"
TESTS = ["tests/test_bundled_riichi.py", "tests/test_srd_lite_foundation.py"]


def main() -> int:
    work = HERE / ".recording-work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    raw = work / "evaluations.jsonl"
    try:
        env = dict(os.environ, SYLVIA_EVAL_RECORD=str(raw), PYTHONPATH=str(HERE), PYTHONDONTWRITEBYTECODE="1")
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "record_evaluations_plugin", *TESTS, "-q", "-x",
             "-p", "no:cacheprovider", f"--basetemp={work / 'pytest'}"],
            cwd=ROOT, env=env, check=False,
        )
        if completed.returncode != 0:
            raise SystemExit(f"pytest failed with {completed.returncode}")
        lines = sorted(set(raw.read_text(encoding="utf-8").splitlines()))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    values: list[object] = []
    index_of: dict[str, int] = {}
    cases = []
    for line in lines:
        record = json.loads(line)
        roots = {}
        for name, value in record.pop("roots").items():
            key = json.dumps(value, ensure_ascii=False)
            if key not in index_of:
                index_of[key] = len(values)
                values.append(value)
            roots[name] = index_of[key]
        record["roots"] = roots
        cases.append(record)
    document = {"python": sys.version.split()[0], "platform": PLATFORM, "tests": TESTS, "values": values,
                "cases": cases}
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True) + "\n").encode("ascii"))
    print(f"{len(cases)} evaluations, {len(values)} distinct root values -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
