"""Write turn_setup_vectors.json and turn_vectors.json: the GM turn's setup and the whole turn (T5a-1).

Run from the repository root with the snapshot's interpreter, after building the comparison
tool:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_turn_vectors.py

It records `ruleless-turn` and `srd-mechanics-turn` twice through
`generate_round_vectors.record` (`trace_turn_phases.py`):

- **turn_setup_vectors.json:** `prepare_turn_recovery`, `restore_saved_turn_progress` and
  `resume_exchange_results` traced, each real call followed by its `VARIANTS` (a redirected or
  post-mechanics continuation, saved round facts and queue receipts, saved action contracts,
  an out-of-character correction, resumed drafts and tool results, and their refusals). Each
  case keeps the whole provider input, which these phases read.
- **turn_vectors.json:** `ProviderSessionAdapter._turn_events` traced whole (the pseudo-phase
  `turn_events`): each real GM turn's input, every owner call, `uuid4` draw, catalog, callback,
  provider request and chunk, its events and the session loop's answers, and how it ended.

Large repeated values are interned as `generate_batch_vectors.py` does, and times and durations
are fixed. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from generate_batch_vectors import interned  # noqa: E402
from generate_round_vectors import DURATION, ROOT, TIME, cases, record  # noqa: E402

SESSIONS = ("ruleless-turn", "srd-mechanics-turn")
SETUP_PHASES = ("prepare_turn_recovery", "restore_saved_turn_progress", "resume_exchange_results")


def turns(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Each `turn_start` .. `turn_end` stretch as one case."""
    found: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for row in rows:
        if row["event"] == "turn_start":
            current = {key: row[key] for key in ("turn_id", "adapter", "model", "input")}
            current["events"] = []
        elif row["event"] == "turn_end" and current is not None:
            if "raised" in row:
                current["raised"] = row["raised"]
            found.append(current)
            current = None
        elif current is not None:
            current["events"].append(row)  # type: ignore[union-attr]
    assert current is None, "a turn did not end"
    return found


def write(name: str, found: list[dict[str, object]]) -> list[dict[str, object]]:
    text = json.dumps(found, ensure_ascii=False, allow_nan=False)
    assert " at 0x" not in text, "a recorded value carries an object address"
    text = TIME.sub("2026-01-01T00:00:00+00:00", text)
    text = DURATION.sub(lambda match: f'"{match.group(1)}": 0', text)
    found = json.loads(text)
    shared: dict[str, object] = {}
    written = [interned(case, shared) for case in found]
    text = json.dumps({"shared": shared, "cases": written}, ensure_ascii=False, indent=1,
                      allow_nan=False) + "\n"
    (HERE / name).write_bytes(text.encode("utf-8"))
    print(name, len(found), "cases,", len(text), "bytes")
    return found


def main() -> None:
    sessions = [ROOT / f"crates/sylvia-compare/sessions/{name}.session.json" for name in SESSIONS]
    setup = write("turn_setup_vectors.json", [
        case for session in sessions for case in cases(record(",".join(SETUP_PHASES), session))
        if case["phase"] in SETUP_PHASES])
    for case in setup:
        print(" ", case["phase"], "|", case["variant"], "|",
              case.get("raised", {}).get("code") or "ok")
    whole = write("turn_vectors.json", [
        case for session in sessions for case in turns(record("turn_events", session))])
    for case in whole:
        kinds = [event["value"]["kind"] for event in case["events"]
                 if event["event"] == "phase_event"]
        print("  turn", case["turn_id"], "|", case.get("raised", {}).get("code") or "ok", "|",
              kinds)


if __name__ == "__main__":
    main()
