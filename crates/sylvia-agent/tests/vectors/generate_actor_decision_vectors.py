"""Write actor_decision_vectors.json: whole GM turns with required transitions and isolated
participant decisions (T6b).

Run from the repository root with the snapshot's interpreter:

    .venv/Scripts/python.exe -I crates/sylvia-agent/tests/vectors/generate_actor_decision_vectors.py

No comparison session reaches an isolated participant: the only Ruleset that declares one is
the hidden-token Ruleset of `tests/test_isolated_participants.py`, whose `isolated_game` fixture
creates its campaign directly. So the turns run here, in-process: that fixture's owner,
`IsolatedFlowProvider` and `gm_input`, with `trace_turn_phases.py` imported first so its
`turn_events` pseudo-phase traces `ProviderSessionAdapter._turn_events` whole (every owner call,
`uuid4` draw, catalog, callback, provider request and chunk, event and ending), as
`generate_turn_vectors.py` records the comparison sessions' turns. `_actor_decisions`' own
`uuid4` is the tracer's seeded one too. Each action the turn emits is executed on the owner, as
the tests do.

Scenarios: the housekeeping transition, then the three seats' decisions; the decisions alone,
on Anthropic Messages and on Chat Completions with private reasoning; a proposal naming another
seat's action, which the engine refuses.

Written by `generate_turn_vectors.write` (times and durations fixed, large values interned). Reference tooling only;
retired with the Python source.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

FOLDER = Path(tempfile.mkdtemp(prefix="actor-decisions-"))
TRACE = FOLDER / "trace.jsonl"
os.environ["SYLVIA_TURN_PHASES_TRACE"] = str(TRACE)
os.environ["SYLVIA_TURN_PHASES"] = "turn_events"

import trace_turn_phases as tracer  # noqa: E402  (wraps `_turn_events` on import)

from generate_turn_vectors import turns, write  # noqa: E402
from sylvia_gm.agent import _actor_decisions  # noqa: E402
from sylvia_gm.agent._provider_session import ProviderSessionAdapter  # noqa: E402
from sylvia_gm.agent._session_loop import ProviderEventKind  # noqa: E402

import tests.test_isolated_participants as iso  # noqa: E402

_actor_decisions.uuid = tracer.Uuids()  # type: ignore[assignment]
FIXTURE = iso.isolated_game._get_wrapped_function()
SCENARIOS = [
    ("housekeeping", {"housekeeping": True}, "anthropic_messages", "minimax-m3", None),
    ("decisions", None, "anthropic_messages", "minimax-m3", None),
    ("decisions-reasoning", None, "chat_completions", "deepseek-v3.2", None),
    ("another-seat", None, "anthropic_messages", "minimax-m3", "b:0"),
]


class AuditedFlowProvider(iso.IsolatedFlowProvider):
    """The test provider with `set_audit_context`, as the context-fitting wrapper the owner gives
    every GM provider has: Python then reads each request's context id, as in production and as
    the Rust adapter always does."""

    def set_audit_context(self, context: object) -> None:
        self.audit_context = context


def run(name: str, options: object, transport: str, model: str, proposal: object) -> None:
    folder = FOLDER / name
    folder.mkdir()
    game = FIXTURE(folder, SimpleNamespace(param=options))
    owner, session, _ = next(game)
    try:
        provider = AuditedFlowProvider(transport, model)
        if proposal is not None:
            provider.proposal = proposal
        adapter = ProviderSessionAdapter(
            owner, provider, owner_id="isolated-test",
            gm_system_prompt="GM_PRIVATE_INSTRUCTIONS_SENTINEL",
            campaign_plan={"structure": [{"id": "one", "title": "GM_PLAN_SENTINEL"}], "objectives": []},
        )
        try:
            for event in adapter.for_turn("turn")(iso.gm_input(owner, session)):
                if event.kind is ProviderEventKind.ACTION:
                    owner.execute_action(event.action_command)
        except Exception as error:  # noqa: BLE001 - the ending is in the trace
            print(f"{name}: ended with {type(error).__name__}: {error}", file=sys.stderr)
    finally:
        game.close()


def main() -> None:
    for scenario in SCENARIOS:
        run(*scenario)
    rows = [json.loads(line) for line in TRACE.read_text(encoding="utf-8").splitlines()]
    found = turns(rows)
    for case, (name, *_rest) in zip(found, SCENARIOS, strict=True):
        case["scenario"] = name
    for case in write("actor_decision_vectors.json", found):
        kinds = [event["value"]["kind"] for event in case["events"] if event["event"] == "phase_event"]
        print(" ", case["scenario"], "|", case.get("raised", {}).get("message") or "ok", "|", kinds)


if __name__ == "__main__":
    main()
