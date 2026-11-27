"""Generate reference vectors for `sylvia_owner::owner_json` (`owner_process._json_value`).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-owner/tests/vectors/generate_owner_json_vectors.py

Each case is a JSON value run through the real `_json_value`: the projection, or the exception's
class and text. The cases cover the mapping kinds that never cross the wire as saved
(`provider_tool_results`, `provider_tool_calls`, `working_context_checkpoint`,
`gm_wire_journal`, `gm_native_records`), nested and at the top, with the shapes Python
tolerates and the ones it refuses. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

from sylvia_gm.interfaces.owner_process import _json_value  # noqa: E402

OUTPUT = HERE / "owner_json_vectors.json"

READ = "read_session_history"
CASES = [
    {"plain": [1, 2.5, None, True, "é"], "nested": {"a": {"b": []}}},
    {"kind": "provider_tool_results", "results": [
        {"name": READ, "content": "secret history", "call_id": "c1"},
        {"name": "roll", "content": "4"},
        {"name": READ},
        "not a mapping",
    ], "extra": 1},
    {"kind": "provider_tool_results"},
    {"kind": "provider_tool_results", "results": "ab"},
    {"kind": "provider_tool_results", "results": {"x": 1}},
    {"kind": "provider_tool_results", "results": None},
    {"kind": "provider_tool_results", "results": 5},
    {"kind": "provider_tool_calls", "assistant": {"role": "assistant", "tool_calls": [
        {"id": "c1", "function": {"name": READ, "arguments": "{\"query\": \"x\"}"}},
        {"id": "c2", "function": {"name": "roll", "arguments": "{}"}},
        {"id": "c3", "function": "not a mapping"},
        {"id": "c4"},
        7,
    ]}},
    {"kind": "provider_tool_calls", "assistant": {"role": "assistant"}},
    {"kind": "provider_tool_calls", "assistant": "text"},
    {"kind": "provider_tool_calls"},
    {"kind": "provider_tool_calls", "assistant": {"tool_calls": None}},
    {"kind": "working_context_checkpoint", "checkpoint_id": "cp-1", "covered_exchanges": 4, "notes": "private"},
    {"kind": "working_context_checkpoint"},
    {"kind": "gm_wire_journal", "journal_record_id": "j1", "epoch": 2, "generation": 3, "base_count": 1,
     "entries": [{"private": True}, {}], "secret": "x"},
    {"kind": "gm_wire_journal"},
    {"kind": "gm_wire_journal", "entries": "abc"},
    {"kind": "gm_wire_journal", "entries": {"a": 1, "b": 2}},
    {"kind": "gm_wire_journal", "entries": 0},
    {"kind": "gm_wire_journal", "entries": False},
    {"kind": "gm_wire_journal", "entries": 3},
    {"kind": "gm_wire_journal", "entries": True},
    {"kind": "gm_wire_journal", "entries": 1.5},
    {"kind": "gm_wire_journal", "omitted_from_export": True, "entries": 3},
    {"kind": "gm_wire_journal", "omitted_from_export": 1, "entries": []},
    {"kind": "gm_native_records", "native_record_id": "n1", "epoch": 1, "generation": 1, "boundary": "b",
     "status": "complete", "records": [1, 2, 3]},
    {"kind": "gm_native_records", "records": ""},
    {"kind": "gm_native_records", "omitted_from_export": True},
    {"messages": [{"kind": "gm_wire_journal", "entries": [1]}, {"kind": "provider_tool_results", "results": []}]},
    [{"kind": "working_context_checkpoint", "checkpoint_id": "cp-2"}],
]


def main() -> None:
    cases = []
    for value in CASES:
        try:
            cases.append({"value": value, "ok": _json_value(value)})
        except Exception as error:  # noqa: BLE001 - the exact Python outcome is the vector
            cases.append({"value": value, "error": {"class": type(error).__name__, "message": str(error)}})
    OUTPUT.write_text(json.dumps({"cases": cases}, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(cases)} cases -> {OUTPUT}")


if __name__ == "__main__":
    main()
