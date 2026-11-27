"""Generate reference vectors for `sylvia_owner::session_reasoning` (`_session_reasoning.py`).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-owner/tests/vectors/generate_session_reasoning_vectors.py

For every combination of a session (role, saved effort, next-reply effort), a next-reply model
(none, another listed model, an unlisted one) and a saved speed, over a small catalogue of
`model_choices` rows, the real `reasoning_state`, `speed_state` and `validate_model_preferences`
(both modes) are recorded: each payload with its key order, or the refusal's text. Reference
tooling only; retired with the Python source.
"""

from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

from sylvia_gm.domain.authority import WriterRole  # noqa: E402
from sylvia_gm.domain.errors import CommandRejected  # noqa: E402
from sylvia_gm.domain.session import SessionRecord  # noqa: E402
from sylvia_gm.interfaces._session_reasoning import (  # noqa: E402
    reasoning_state,
    speed_state,
    validate_model_preferences,
)

OUTPUT = HERE / "session_reasoning_vectors.json"

CHOICES = [
    {
        "provider_id": "acme", "provider_name": "Acme", "model_id": "acme/think", "model_name": "Think",
        "supported_reasoning_efforts": ["low", "medium", "high", "xhigh"], "default_reasoning_effort": "medium",
        "reasoning_source": "provider_catalog", "reasoning_control": "effort",
        "speed_options": [{"id": "fast", "name": "Fast", "description": "Lower latency"}],
        "speed_control": "service_tier", "speed_source": "provider_catalog", "transport": "responses",
    },
    {
        "provider_id": "acme", "provider_name": "Acme", "model_id": "acme/adaptive",
        "supported_reasoning_efforts": ["low", "high"], "reasoning_control": "adaptive",
        "reasoning_source": "configured", "transport": "anthropic_messages",
    },
    {
        "provider_id": "local", "model_id": "local/plain", "transport": "chat_completions",
    },
    {
        "provider_id": "acme", "model_id": "acme/low-only", "supported_reasoning_efforts": ["minimal", "low"],
        "speed_options": [], "transport": "chat_completions",
    },
]

SESSION_MODELS = [("acme", "acme/think"), ("acme", "acme/adaptive"), ("local", "local/plain"), ("acme", "acme/low-only"), ("ghost", "ghost/model")]
EFFORTS = [None, "low", "high", "xhigh", "mode_default", "provider_default", "turbo"]
ONCE = [None, "medium", "turbo"]
ONCE_MODELS = [None, {"provider_id": "acme", "model_id": "acme/low-only"}, {"provider_id": "ghost", "model_id": "ghost/model"}]
SPEEDS = [None, "fast", "standard", "warp"]


def outcome(call):
    try:
        value = call()
    except CommandRejected as error:
        return {"error": str(error)}
    return {"ok": value}


def main() -> None:
    cases = []
    for role, (provider_id, model_id), effort, once in itertools.product(
        ("dm", "builder"), SESSION_MODELS, EFFORTS, ONCE,
    ):
        session = SessionRecord(
            session_id="session-1", campaign_id="campaign-1" if role == "dm" else None,
            role=WriterRole(role), provider_id=provider_id, model_id=model_id,
            reasoning_effort=effort, reasoning_effort_once=once,
        )
        record = {
            "session": {"role": role, "provider_id": provider_id, "model_id": model_id,
                        "reasoning_effort": effort, "reasoning_effort_once": once},
            "reasoning": [outcome(lambda m=m: reasoning_state(session, CHOICES, m)) for m in ONCE_MODELS],
            "speed": [
                [outcome(lambda s=s, m=m: speed_state(session, CHOICES, s, m)) for m in ONCE_MODELS]
                for s in SPEEDS
            ],
            "validate": [outcome(lambda once=flag: validate_model_preferences(session, CHOICES, once=once))
                         for flag in (False, True)],
        }
        cases.append(record)
    OUTPUT.write_text(
        json.dumps({"choices": CHOICES, "once_models": ONCE_MODELS, "speeds": SPEEDS, "cases": cases}, indent=1) + "\n",
        encoding="utf-8", newline="\n",
    )
    print(f"{len(cases)} sessions -> {OUTPUT}")


if __name__ == "__main__":
    main()
