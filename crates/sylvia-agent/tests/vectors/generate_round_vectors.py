"""Generate reference vectors for one provider round's preparation (T2b).

Run from the repository root with the snapshot's interpreter, after building the comparison
tool (`cargo build -p sylvia-compare`):

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_round_vectors.py

It records the `ruleless-turn` session with the frozen Python owner, through `sylvia-compare`
and the stand-in model, tracing `prepare_round_catalog` and `prepare_provider_round`
(`trace_turn_phases.py`): every real call and its variants. Each case holds the phase, the
provider input, the turn locals before, everything the phase drew or read in order (`uuid4`
values, the catalog supplier's results, owner calls with results, audit contexts) and the turn
locals the phase changed, or the error. Times are replaced by one fixed value. Reference tooling only;
retired with the Python source.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUTPUT = HERE / "round_vectors.json"
TRACE = HERE / "turn_phases_trace.jsonl"
SESSION = ROOT / "crates/sylvia-compare/sessions/ruleless-turn.session.json"
TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?")
PHASES = "prepare_round_catalog,prepare_provider_round"


def record() -> list[dict[str, object]]:
    TRACE.unlink(missing_ok=True)
    compare = ROOT / "target" / "debug" / ("sylvia-compare.exe" if sys.platform == "win32"
                                          else "sylvia-compare")
    if not compare.is_file():
        raise SystemExit(f"build the comparison tool first: {compare}")
    env = {**os.environ, "SYLVIA_TURN_PHASES_TRACE": str(TRACE), "SYLVIA_TURN_PHASES": PHASES}
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(
            [str(compare), "record", "--session", str(SESSION), "--out", str(Path(folder) / "out"),
             "--owner", sys.executable, "--arg", "-B", "--arg", str(HERE / "trace_turn_phases.py")],
            check=True, cwd=ROOT, env=env,
        )
    rows = [json.loads(line) for line in TRACE.read_text(encoding="utf-8").splitlines()]
    TRACE.unlink()
    return rows


def cases(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for row in rows:
        event = row["event"]
        if event == "phase_start":
            current = {"phase": row["phase"], "variant": row["variant"], "turn_id": row["turn_id"],
                       "adapter": row["adapter"], "model": row["model"], "input": row["input"],
                       "before": row["state"], "events": []}
        elif event == "phase_end" and current is not None:
            # Only what the phase changed; every other local is as before.
            before, after = current["before"], row["state"]  # type: ignore[index]
            current["after"] = {
                group: {key: value for key, value in after[group].items()  # type: ignore[index]
                        if before[group].get(key, "<unassigned>") != value}  # type: ignore[index]
                for group in after}  # type: ignore[union-attr]
            if "raised" in row:
                current["raised"] = row["raised"]
            result.append(current)
            current = None
        elif current is not None:
            current["events"].append(row)  # type: ignore[union-attr]
    return result


def main() -> None:
    found = cases(record())
    text = json.dumps({"cases": found}, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    text = TIME.sub("2026-01-01T00:00:00+00:00", text)
    OUTPUT.write_bytes(text.encode("utf-8"))
    print(len(found), "cases,", len(text), "bytes")
    for case in found:
        print(" ", case["phase"], "|", case["variant"], "->",
              case.get("raised", {}).get("code") or "ok",
              len(case["events"]), "events")  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
