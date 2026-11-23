"""Generate reference vectors for one provider round's preparation (T2b).

Run from the repository root with the snapshot's interpreter, after building the comparison
tool (`cargo build -p sylvia-compare`):

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_round_vectors.py
    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_round_vectors.py         --phases stream_provider_round --output stream_vectors.json

The first writes `round_vectors.json` (T2b's preparation phases); the second, T2c's
`stream_vectors.json`, where each case also holds the GM provider's requests and chunks.

It records the `ruleless-turn` session with the frozen Python owner, through `sylvia-compare`
and the stand-in model, tracing `prepare_round_catalog` and `prepare_provider_round`
(`trace_turn_phases.py`): every real call and its variants. Each case holds the phase, the
provider input, the turn locals before, everything the phase drew or read in order (`uuid4`
values, the catalog supplier's results, owner calls with results, audit contexts) and the turn
locals the phase changed, or the error. Times and durations are replaced by fixed values, and
`uuid4` is seeded, so two runs write identical bytes. `--session NAME` (repeatable) records
other sessions instead; `generate_check_vectors.py` uses `record` and `cases` for T3a's
helpers. Reference tooling only; retired with the Python source.
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
#: Wall-clock durations the run's activity progress saved; fixed, as times are.
DURATION = re.compile(r'"(elapsed_ms|quiet_ms)": \d+')
PHASES = "prepare_round_catalog,prepare_provider_round"


def record(phases: str, session: Path = SESSION) -> list[dict[str, object]]:
    TRACE.unlink(missing_ok=True)
    compare = ROOT / "target" / "debug" / ("sylvia-compare.exe" if sys.platform == "win32"
                                          else "sylvia-compare")
    if not compare.is_file():
        raise SystemExit(f"build the comparison tool first: {compare}")
    # A fixed hash seed keeps the owner's set and dict iteration the same on every run.
    env = {**os.environ, "SYLVIA_TURN_PHASES_TRACE": str(TRACE), "SYLVIA_TURN_PHASES": phases,
           "PYTHONHASHSEED": "0"}
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(
            [str(compare), "record", "--session", str(session), "--out", str(Path(folder) / "out"),
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
            if "returned" in row:
                current["returned"] = row["returned"]
            result.append(current)
            current = None
        elif current is not None:
            current["events"].append(row)  # type: ignore[union-attr]
    return result


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--phases", default=PHASES)
    parser.add_argument("--output", default=OUTPUT.name)
    parser.add_argument("--session", action="append",
                        help="a session name (repeatable); ruleless-turn by default")
    arguments = parser.parse_args()
    output = HERE / arguments.output
    sessions = [ROOT / "crates/sylvia-compare/sessions" / f"{name}.session.json"
                for name in arguments.session or ["ruleless-turn"]]
    found = [case for session in sessions for case in cases(record(arguments.phases, session))]
    text = json.dumps({"cases": found}, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    text = TIME.sub("2026-01-01T00:00:00+00:00", text)
    text = DURATION.sub(lambda match: f'"{match.group(1)}": 0', text)
    output.write_bytes(text.encode("utf-8"))
    print(len(found), "cases,", len(text), "bytes")
    for case in found:
        print(" ", case["phase"], "|", case["variant"], "->",
              case.get("raised", {}).get("code") or "ok",
              len(case["events"]), "events")  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
