"""Generate reference vectors for the chat-start helpers ported with `set_chat_defaults` (C4).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-owner/tests/vectors/generate_chat_start_vectors.py

Writes `chat_start_vectors.json`: `_session_reasoning.catalog_model_info` over every
`model_choices` row the resolver vectors hold (each projected back into a `ModelInfo`) and over
hand-made rows, then `_chat_start.explicit_effort` and `explicit_speed` against those models.
Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.interfaces._chat_start import explicit_effort, explicit_speed
from sylvia_gm.interfaces._session_reasoning import catalog_model_info
from sylvia_gm.provider._base import SpeedOption

HERE = Path(__file__).parent
ROOT = HERE.parents[3]
RESOLVER = ROOT / "crates/sylvia-agent/tests/vectors/resolver_vectors.json"


def plain(value):
    if isinstance(value, SpeedOption):
        return {"id": value.id, "name": value.name, "description": value.description}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def record(model) -> dict | None:
    return None if model is None else {field.name: plain(getattr(model, field.name)) for field in fields(model)}


def outcome(call) -> dict:
    try:
        return {"value": call()}
    except CommandRejected as error:
        return {"error": {"class": "CommandRejected", "code": error.code, "message": str(error)}}
    except Exception as error:  # noqa: BLE001
        return {"error": {"class": type(error).__name__, "message": str(error)}}


BASE = {"provider_id": "p", "provider_name": "P", "model_id": "p/m", "model_name": "M"}
HAND_MADE = [
    {"provider_id": "p", "model_id": "p/m"},
    {**BASE, "transport": None}, {**BASE, "transport": "pigeon"}, {**BASE, "transport": "responses"},
    {**BASE, "supported_reasoning_efforts": ["low", "high"], "default_reasoning_effort": "high", "reasoning_control": "openai",
     "reasoning_source": "s", "transport": "responses"},
    {**BASE, "supported_reasoning_efforts": ["Bad"]}, {**BASE, "default_reasoning_effort": "low"},
    {**BASE, "supported_reasoning_efforts": "low"}, {**BASE, "reasoning_control": 5, "speed_control": ["x"]},
    {**BASE, "speed_options": [], "speed_control": "openai"}, {**BASE, "speed_options": [{"id": "fast", "name": " Fast "}], "speed_control": "openai"},
    {**BASE, "speed_options": [{"id": "standard", "name": "S"}]}, {**BASE, "speed_options": "fast"},
    {**BASE, "model_name": 7, "provider_name": None, "reasoning_source": None, "speed_source": 1.5},
    {**BASE, "supported_reasoning_efforts": ["disabled", "adaptive"], "reasoning_control": "adaptive", "transport": "anthropic_messages"},
    {**BASE, "supported_reasoning_efforts": ["low", "max"], "reasoning_control": "anthropic", "transport": "anthropic_messages",
     "speed_options": [{"id": "fast", "name": "Fast", "description": "d"}], "speed_control": "anthropic"},
]
EFFORTS = ["mode_default", "provider_default", "low", "high", "xhigh", "max", "adaptive", "bogus", 5, None, ""]
SPEEDS = ["standard", "fast", "flex", "bogus", 5, None, ""]


def main() -> int:
    resolver = json.loads(RESOLVER.read_text(encoding="utf-8"))
    rows = []
    for case in resolver["cases"]:
        for row in case.get("model_choices") or []:
            if isinstance(row, dict) and row not in rows:
                rows.append(row)
    rows += HAND_MADE
    projections = []
    for row in rows:
        provider_id, model_id = row["provider_id"], row["model_id"]
        result = outcome(lambda p=provider_id, m=model_id, r=row: record(catalog_model_info(p, m, [r])))
        projections.append({"row": row, **result})
    missing = outcome(lambda: record(catalog_model_info("p", "p/none", rows)))
    choices = []
    for index, row in enumerate(rows):
        try:
            model = catalog_model_info(row["provider_id"], row["model_id"], [row])
        except Exception:  # noqa: BLE001
            continue
        for role in ("builder", "dm"):
            for value in EFFORTS:
                choices.append({"row": index, "kind": "effort", "role": role, "requested": value,
                                **outcome(lambda m=model, r=role, v=value: explicit_effort(m, r, v))})
        for value in SPEEDS:
            choices.append({"row": index, "kind": "speed", "requested": value, **outcome(lambda m=model, v=value: explicit_speed(m, v))})
    for value in EFFORTS:
        choices.append({"row": None, "kind": "effort", "role": "builder", "requested": value,
                        **outcome(lambda v=value: explicit_effort(None, "builder", v))})
    for value in SPEEDS:
        choices.append({"row": None, "kind": "speed", "requested": value, **outcome(lambda v=value: explicit_speed(None, v))})
    document = {"projections": projections, "missing": missing, "choices": choices}
    output = HERE / "chat_start_vectors.json"
    output.write_bytes((json.dumps(document, ensure_ascii=True, indent=0) + "\n").encode("ascii"))
    print(f"{len(projections)} rows, {len(choices)} choices -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
