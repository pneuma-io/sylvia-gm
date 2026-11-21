"""Vectors for `_chat_start.resolve_chat_start`: a new chat's effort and speed from its chosen
values, the Settings defaults and the model's catalog row.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-owner/tests/vectors/generate_resolve_chat_start_vectors.py

The catalog rows are `generate_chat_start_vectors.py`'s hand-made rows and the first six rows
of its resolver vectors. Each case resolves one row
for one role, one `chat_defaults` mapping and one set of chosen parameters, and records the
`ChatStart` or the failure. It writes `resolve_chat_start_vectors.json`; a re-run writes the same
file. Reference tooling only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))
sys.path.insert(0, str(HERE))

from generate_chat_start_vectors import HAND_MADE, RESOLVER, outcome  # noqa: E402

from sylvia_gm.interfaces._chat_start import resolve_chat_start  # noqa: E402

DEFAULTS: list[object] = [
    {}, {"reasoning_effort": None}, {"speed": None},
    *({"reasoning_effort": value} for value in ("provider_default", "mode_default", "low", "high", "max", "bogus", 5)),
    *({"speed": value} for value in ("fast", "standard", "flex", 5)),
    {"reasoning_effort": "high", "speed": "fast"}, {"reasoning_effort": "low", "speed": "fast", "provider_id": "x"},
]
PARAMS: list[dict[str, object]] = [
    {}, {"reasoning_effort": None, "speed": None}, {"reasoning_effort": "high"}, {"reasoning_effort": "mode_default"},
    {"reasoning_effort": "bogus"}, {"reasoning_effort": 3}, {"speed": "fast"}, {"speed": "standard"}, {"speed": 5},
    {"reasoning_effort": "low", "speed": "fast"},
]


def main() -> int:
    resolver = json.loads(RESOLVER.read_text(encoding="utf-8"))
    rows: list[dict[str, object]] = []
    for case in resolver["cases"]:
        for row in case.get("model_choices") or []:
            if isinstance(row, dict) and row not in rows and len(rows) < 6:
                rows.append(row)
    rows += [row for row in HAND_MADE if row not in rows]
    cases = []
    for index, row in enumerate(rows):
        for role in ("builder", "dm"):
            for d_index, defaults in enumerate(DEFAULTS):
                for p_index, params in enumerate(PARAMS):
                    def call(r=row, d=defaults, p=params, role=role):  # noqa: ANN001, ANN202
                        start = resolve_chat_start([r], d, role=role, provider_id=str(r["provider_id"]),
                                                   model_id=str(r["model_id"]), params=p)
                        return {"provider_id": start.provider_id, "model_id": start.model_id,
                                "reasoning_effort": start.reasoning_effort, "speed": start.speed}
                    cases.append({"row": index, "role": role, "defaults": d_index, "params": p_index, **outcome(call)})
    # A model that is not in the catalog: no capabilities, so only the provider default survives.
    for d_index, defaults in enumerate(DEFAULTS):
        for p_index, params in enumerate(PARAMS):
            def missing(d=defaults, p=params):  # noqa: ANN001, ANN202
                start = resolve_chat_start(rows, d, role="dm", provider_id="p", model_id="p/none", params=p)
                return {"provider_id": start.provider_id, "model_id": start.model_id,
                        "reasoning_effort": start.reasoning_effort, "speed": start.speed}
            cases.append({"row": None, "role": "dm", "defaults": d_index, "params": p_index, **outcome(missing)})
    # Each distinct outcome is stored once; a case names it by index.
    outcomes: list[dict[str, object]] = []
    for case in cases:
        result = {key: case.pop(key) for key in ("value", "error") if key in case}
        if result not in outcomes:
            outcomes.append(result)
        case["outcome"] = outcomes.index(result)
    document = {"python": sys.version.split()[0], "rows": rows, "defaults": DEFAULTS, "params": PARAMS,
                "outcomes": outcomes, "cases": cases}
    output = HERE / "resolve_chat_start_vectors.json"
    output.write_bytes((json.dumps(document, ensure_ascii=True, separators=(",", ":")) + "\n").encode("ascii"))
    print(f"{len(rows)} rows, {len(cases)} cases -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
