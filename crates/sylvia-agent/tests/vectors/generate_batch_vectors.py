"""Write batch_vectors.json: one round's tool batch (T3b-1), traced in real turns.

Run from the repository root with the snapshot's interpreter, after building the comparison
tool:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_batch_vectors.py

It records `ruleless-turn` and `srd-mechanics-turn` through `generate_round_vectors.record`
with `prepare_tool_batch` and `execute_tool_batch` traced (`trace_turn_phases.py`). Each case
holds the phase's events as the session loop received them, the loop's answer to each action
(`action_handled`), every owner call, and each call of the provider input's narrative writer,
knowledge searcher and reference reader with its result (`callback`). The provider input keeps
its session and which callbacks it has.

The turns repeat large values (tool lists and schemas, snapshots, long messages), so every
JSON subtree whose text exceeds `SHARED_BYTES` is stored once in `shared`, under the first 16
hex digits of its text's SHA-256, and written in its place as `{"__shared__": key}`, children
first. The Rust test expands them back. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from generate_round_vectors import DURATION, ROOT, TIME, cases, record  # noqa: E402

OUTPUT = HERE / "batch_vectors.json"
SESSIONS = ("ruleless-turn", "srd-mechanics-turn")
PHASES = "prepare_tool_batch,execute_tool_batch"
SHARED_BYTES = 4096


def slim(case: dict) -> dict:
    source = case["input"]
    session = source["session"]
    case = dict(case)
    case["input"] = {"session": session, "history": {"session": session, "messages": []},
                     **{key: value for key, value in source.items() if key.startswith("has_")}}
    return case


def interned(value: object, shared: dict[str, object]) -> object:
    """`value` with each subtree larger than `SHARED_BYTES` stored once in `shared`."""
    if isinstance(value, dict):
        value = {key: interned(item, shared) for key, item in value.items()}
    elif isinstance(value, list):
        value = [interned(item, shared) for item in value]
    else:
        return value
    text = json.dumps(value, ensure_ascii=False)
    if len(text) <= SHARED_BYTES:
        return value
    key = hashlib.sha256(text.encode()).hexdigest()[:16]
    shared.setdefault(key, value)
    return {"__shared__": key}


def main() -> None:
    found = [slim(case) for name in SESSIONS
             for case in cases(record(PHASES, ROOT / f"crates/sylvia-compare/sessions/{name}.session.json"))]
    # Times and durations are fixed first, so the shared keys do not depend on the run.
    text = json.dumps(found, ensure_ascii=False, allow_nan=False)
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
        print(" ", case["phase"], "|", case.get("raised", {}).get("code") or "ok", "|", kinds[:12])


if __name__ == "__main__":
    main()
