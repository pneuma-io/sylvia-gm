"""Write narration_vectors.json: the narration phases (T4), traced in real turns.

Run from the repository root with the snapshot's interpreter, after building the comparison
tool:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_narration_vectors.py

It records `ruleless-turn` and `srd-mechanics-turn` through `generate_round_vectors.record`
with every narration phase traced (`trace_turn_phases.py`). A stand-in turn narrates through
the Campaign File, so it reaches none of them: each real `reconcile_tool_batch` call runs them
on copies of its state, with the round's prose set (`CROSS_VARIANTS`): an empty completion with and without a finish reason, a repeated
response (with non-ASCII text), a missing tool call, private deliberation, held narration with
native data, a missing Campaign File update (rejected, and with a held response or blocks),
and the three kinds of held prose. Each case holds what `generate_batch_vectors.py` keeps for a
phase, and large repeated values are interned the same way. Reference tooling only; retired
with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from generate_batch_vectors import interned, slim  # noqa: E402
from generate_round_vectors import DURATION, ROOT, TIME, cases, record  # noqa: E402

OUTPUT = HERE / "narration_vectors.json"
SESSIONS = ("ruleless-turn", "srd-mechanics-turn")
NARRATION_PHASES = (
    "record_empty_completion", "retry_repeated_response", "retry_missing_tool_call",
    "retry_private_deliberation", "record_held_narration", "retry_missing_campaign_file_update",
    "preserve_boundary_narration", "preserve_player_intent_narration",
    "retain_campaign_file_narration", "record_final_narration",
)
#: `reconcile_tool_batch` is the anchor the narration phases' cross variants run from; its own
#: cases are `generate_reconcile_vectors.py`'s.
PHASES = ",".join(("reconcile_tool_batch", *NARRATION_PHASES))


def main() -> None:
    found = [slim(case) for name in SESSIONS
             for case in cases(record(PHASES, ROOT / f"crates/sylvia-compare/sessions/{name}.session.json"))
             if case["phase"] in NARRATION_PHASES]
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
        print(" ", case["phase"], "|", case["variant"], "|",
              case.get("raised", {}).get("code") or "ok", "|", kinds[:6])


if __name__ == "__main__":
    main()
