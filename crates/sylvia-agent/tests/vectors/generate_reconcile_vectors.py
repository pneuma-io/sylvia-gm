"""Write reconcile_vectors.json: a round's tool results reconciled (T3b-2), traced in real turns.

Run from the repository root with the snapshot's interpreter, after building the comparison
tool:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_reconcile_vectors.py

It records `ruleless-turn` and `srd-mechanics-turn` through `generate_round_vectors.record`
with `reconcile_tool_batch` traced (`trace_turn_phases.py`), each real call repeated with its
`VARIANTS`: failed optional reference reads beside a failed call, a successful local result, an
executed sequence's facts alone, each intent behavior and preparation scope, the end of combat,
a failed action and a missing canonical result. Each case holds what `generate_batch_vectors.py`
keeps for a batch phase, and large repeated values are interned the same way. Reference tooling
only; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from generate_batch_vectors import interned, slim  # noqa: E402
from generate_round_vectors import DURATION, ROOT, TIME, cases, record  # noqa: E402

OUTPUT = HERE / "reconcile_vectors.json"
SESSIONS = ("ruleless-turn", "srd-mechanics-turn")
PHASES = "reconcile_tool_batch"


def main() -> None:
    found = [slim(case) for name in SESSIONS
             for case in cases(record(PHASES, ROOT / f"crates/sylvia-compare/sessions/{name}.session.json"))]
    text = json.dumps(found, ensure_ascii=False, allow_nan=False)
    assert " at 0x" not in text, "a recorded value carries an object address"
    text = TIME.sub("2026-01-01T00:00:00+00:00", text)
    text = DURATION.sub(lambda match: f'"{match.group(1)}": 0', text)
    found = json.loads(text)
    shared: dict[str, object] = {}
    written = [interned(case, shared) for case in found]
    text = json.dumps({"shared": shared, "cases": written}, ensure_ascii=False, indent=1,
                      allow_nan=False) + "\n"
    OUTPUT.write_bytes(text.encode("utf-8"))
    print(len(found), "cases,", len(text), "bytes")
    for case in found:
        kinds = [event.get("name") or event["event"] for event in case["events"]]
        print(" ", case["variant"], "|", case.get("raised", {}).get("code") or "ok", "|", kinds[:10])


if __name__ == "__main__":
    main()
