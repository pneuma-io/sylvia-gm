"""Write campaign_file_vectors.json: the Campaign File commit and the reference reads (T3m-3).

Run from the repository root with the snapshot's interpreter, after building the comparison
tool:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_campaign_file_vectors.py

It records `ruleless-turn` and `srd-mechanics-turn` through `generate_round_vectors.record`
with the `campaign_file` pseudo-phase traced (`trace_turn_phases.py`). Each case holds one call
of `StatelessMcpMechanics.commit_campaign_file`, `search_knowledge` or `read_reference`: its
arguments, every owner call it made with the answer (the Full Audit records among them; a
catalog tool keeps the fields the listing reads), and its result or what it raised. The first
real commit in each run is followed by `campaign_file_variants`.

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
from generate_listing_vectors import renamed_discoveries, slim  # noqa: E402
from generate_round_vectors import DURATION, ROOT, TIME, record  # noqa: E402

OUTPUT = HERE / "campaign_file_vectors.json"
SESSIONS = ("ruleless-turn", "srd-mechanics-turn")


def cases(rows: list[dict[str, object]], session: str) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for row in rows:
        if row["event"] == "campaign_file_start":
            current = {"session": session, **{key: value for key, value in row.items()
                                               if key != "event"}, "calls": []}
        elif row["event"] == "campaign_file_end" and current is not None:
            current.update({key: value for key, value in row.items() if key != "event"})
            found.append(current)
            current = None
        elif current is not None and row["event"] == "call":
            current["calls"].append(slim(row))  # type: ignore[union-attr]
    return found


def main() -> None:
    found = [case for name in SESSIONS
             for case in cases(record("campaign_file",
                                      ROOT / f"crates/sylvia-compare/sessions/{name}.session.json"), name)]
    text = json.dumps(found, ensure_ascii=False, allow_nan=False)
    assert " at 0x" not in text, "a recorded value carries an object address"
    text = renamed_discoveries(text)
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
        outcome = (f"{raised['class']} {raised.get('code')} {raised['message'][:70]}"
                   if raised else str(case.get("result"))[:70])
        print(" ", case["session"], case["method"], case["variant"], "|", outcome)


if __name__ == "__main__":
    main()
