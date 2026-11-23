"""Write execution_vectors.json: one mechanic executed through MCP (T3m-2), traced in a real turn.

Run from the repository root with the snapshot's interpreter, after building the comparison
tool:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_execution_vectors.py

It records `srd-mechanics-turn` through `generate_round_vectors.record` with the
`execute_action` pseudo-phase traced (`trace_turn_phases.py`). Each case holds one call of
`StatelessMcpMechanics.execute_action`: its command, every owner call it made with the answer
(the Full Audit records and the mechanics diagnostic among them; a catalog tool keeps the
fields the listing reads), each `uuid4` it drew, and the receipt it returned or what it raised.
The first call is repeated with `execution_variants`: the owner's refusals of every class, a
choice, and the tool missing from the catalog at either check.

Large repeated values are interned as in `generate_batch_vectors.py`. Reference tooling only;
retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from generate_batch_vectors import interned  # noqa: E402
from generate_listing_vectors import slim  # noqa: E402
from generate_round_vectors import DURATION, ROOT, TIME, record  # noqa: E402

OUTPUT = HERE / "execution_vectors.json"
SESSIONS = ("srd-mechanics-turn",)


def cases(rows: list[dict[str, object]], session: str) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for row in rows:
        if row["event"] == "execution_start":
            current = {"session": session, "variant": row["variant"], "command": row["command"],
                       "calls": [], "uuids": []}
        elif row["event"] == "execution_end" and current is not None:
            current.update({key: value for key, value in row.items() if key != "event"})
            found.append(current)
            current = None
        elif current is not None and row["event"] == "call":
            current["calls"].append(slim(row))  # type: ignore[union-attr]
        elif current is not None and row["event"] == "uuid":
            current["uuids"].append(row["hex"])  # type: ignore[union-attr]
    return found


def main() -> None:
    found = [case for name in SESSIONS
             for case in cases(record("execute_action",
                                      ROOT / f"crates/sylvia-compare/sessions/{name}.session.json"), name)]
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
        raised = case.get("raised")
        outcome = (f"{raised['class']} {raised.get('code')} {raised['message'][:60]}"
                   if raised else case["receipt"].get("__class__"))
        print(" ", case["variant"], "|", outcome, "|", [call["name"] for call in case["calls"]])


if __name__ == "__main__":
    main()
