"""Write check_vectors.json: the tool batch's campaign-reading helpers (T3a), traced in real turns.

Run from the repository root with the snapshot's interpreter, after building the comparison
tool:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_check_vectors.py

It records `ruleless-turn` and `srd-mechanics-turn` through `generate_round_vectors.record`
with these helpers traced (`trace_turn_phases.py`: each real call and the variants it lists).
A traced case holds the whole turn state, so each is cut to what its helper uses:

- `before` keeps the obligation groups and only the locals the helper reads or writes
  (`LOCALS`). A local the Rust helper reads beyond those is unassigned there, so the test also
  checks what it reads;
- a `snapshot` read keeps the campaign state's lock and participants and the continuity
  records (what the helpers read), with the game state, history and records emptied;
- the provider input keeps its session.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from generate_round_vectors import DURATION, ROOT, TIME, cases, record  # noqa: E402

OUTPUT = HERE / "check_vectors.json"
SESSIONS = ("ruleless-turn", "srd-mechanics-turn")
RESULT = ("assembly", "local_tool_results", "local_tool_failures")
LOCALS = {
    "reject_ruleless_mechanics": ("campaign_id", "revised_narration_supplied",
                                  "selected_raw_blocks", "buffered_narration", *RESULT),
    "validate_revision_speakers": ("ooc_ack_required", "selected_raw_blocks",
                                   "revised_narration", "campaign_id", "parameters",
                                   "ooc_ack_missing", "unsupported_speakers"),
    "prepare_campaign_file_revision": ("parameters", "campaign_id",
                                       "buffered_raw_response_blocks",
                                       "revised_narration_supplied", "revised_narration",
                                       "revised_raw_blocks", "selected_raw_blocks",
                                       "pending_boundary"),
    "settle_campaign_file_narration": ("buffered_narration", "revised_raw_blocks",
                                       "revised_narration", "revised_narration_supplied",
                                       "buffered_response_blocks",
                                       "buffered_raw_response_blocks", "parameters",
                                       "settled_narration", "parameters_establish_changes",
                                       "empty_collection_paths"),
    "record_unchanged_campaign_file": ("update", "settled_narration", "buffered_response_blocks",
                                       "buffered_narration", *RESULT),
}


def slim_snapshot(snapshot: dict) -> dict:
    continuity = dict(snapshot["continuity"])
    continuity.update(current_scene=None, facts=[], reveals={}, plan_progress=[])
    return {**snapshot, "state": {}, "actions": [], "events": [], "mutations": [],
            "consequences": [], "continuity": continuity}


def slim(case: dict) -> dict:
    keep = LOCALS[case["phase"]]
    before = {group: values for group, values in case["before"].items() if group != "locals"}
    before["locals"] = {key: value for key, value in case["before"]["locals"].items()
                        if key in keep}
    events = []
    for event in case["events"]:
        if event.get("event") == "call" and event.get("name") == "snapshot":
            event = {**event, "result": slim_snapshot(event["result"])}
        events.append(event)
    session = case["input"]["session"]
    return {"phase": case["phase"], "variant": case["variant"], "turn_id": case["turn_id"],
            "input": {"session": session, "history": {"session": session, "messages": []}},
            "before": before, "events": events, "after": case["after"],
            **{key: case[key] for key in ("raised", "returned") if key in case}}


def main() -> None:
    phases = ",".join(LOCALS)
    found = [slim(case) for name in SESSIONS
             for case in cases(record(phases, ROOT / f"crates/sylvia-compare/sessions/{name}.session.json"))]
    text = json.dumps({"cases": found}, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    text = TIME.sub("2026-01-01T00:00:00+00:00", text)
    text = DURATION.sub(lambda match: f'"{match.group(1)}": 0', text)
    OUTPUT.write_bytes(text.encode("utf-8"))
    print(len(found), "cases,", len(text), "bytes")


if __name__ == "__main__":
    main()
