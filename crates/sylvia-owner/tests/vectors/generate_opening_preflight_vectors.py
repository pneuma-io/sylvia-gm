"""Generate reference vectors for the guided-start opening preflight (`_preflight_guided_start`).

Run from the repository root with the snapshot's interpreter, after building the comparison
tool (`cargo build -p sylvia-compare`):

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_opening_preflight_vectors.py

It records the same two real openings as `sylvia-agent`'s opening-message vectors, with the
frozen Python owner, through `sylvia-compare` and the stand-in model, traced by
`crates/sylvia-agent/tests/vectors/trace_opening_preflight.py`. The trace forces Admin Full
Audit on, so each preflight also writes its context-fit audit record. Each vector holds:

- `request`: `_preflight_guided_start`'s keyword arguments (all but the resolver);
- `resolved`: the provider the resolver gave (its class, model and base URL);
- `reads`: every owner and bus read the preflight made, with arguments and full result;
- `audit`: the Admin Full Audit records it wrote;
- `admit`: `admit_request`'s options and its measurement (or refusal).

Times and the stand-in's port are replaced by fixed values everywhere. Reference tooling only; retired with the
Python source.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
TRACER = ROOT / "crates/sylvia-agent/tests/vectors/trace_opening_preflight.py"
TRACE = TRACER.with_name("opening_preflight_trace.jsonl")
OUTPUT = HERE / "opening_preflight_vectors.json"
SESSIONS = {
    "ruleless": ROOT / "crates/sylvia-compare/sessions/ruleless-turn.session.json",
    "srd": ROOT / "crates/sylvia-agent/tests/vectors/srd-opening.session.json",
}
PORT = re.compile(r"127\.0\.0\.1:\d+")
TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?")


def record(name: str, session: Path, compare: Path) -> dict[str, object]:
    TRACE.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(
            [str(compare), "record", "--session", str(session), "--out", str(Path(folder) / "out"),
             "--owner", sys.executable, "--arg", "-B", "--arg", str(TRACER)],
            check=True, cwd=ROOT,
        )
    rows = [json.loads(line) for line in TRACE.read_text(encoding="utf-8").splitlines()]
    TRACE.unlink()
    start = next(index for index, row in enumerate(rows) if row["event"] == "preflight_start")
    end = next(index for index, row in enumerate(rows)
               if row["event"] in {"preflight_ok", "preflight_error"})
    window = rows[start:end + 1]
    outcome = window[-1]
    return {
        "name": name,
        "request": window[0]["kwargs"],
        "resolved": next(row for row in window if row["event"] == "resolved"),
        "reads": [
            {"name": row["name"], "args": row["args"], "kwargs": row["kwargs"],
             "result": row.get("result"), "raised": row.get("raised")}
            for row in window if row["event"] == "call"
        ],
        "audit": [row["record"] for row in window if row["event"] == "audit"],
        "admit": next(row for row in window if row["event"] == "admit"),
        "outcome": {"ok": True} if outcome["event"] == "preflight_ok" else
        {"error": {"class": outcome["class"], "message": outcome["message"]}},
    }


def main() -> None:
    compare = ROOT / "target" / "debug" / ("sylvia-compare.exe" if sys.platform == "win32"
                                          else "sylvia-compare")
    if not compare.is_file():
        raise SystemExit(f"build the comparison tool first: {compare}")
    cases = [record(name, session, compare) for name, session in SESSIONS.items()]
    text = json.dumps({"cases": cases}, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    text = TIME.sub("2026-01-01T00:00:00+00:00", text)
    # The stand-in's port is per run; every occurrence (the base URL, the audit endpoint) is one.
    text = PORT.sub("127.0.0.1:61000", text)
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(len(cases), "preflights,", [len(case["reads"]) for case in cases], "reads,",
          [len(case["audit"]) for case in cases], "audit records,", len(text), "bytes")


if __name__ == "__main__":
    main()
