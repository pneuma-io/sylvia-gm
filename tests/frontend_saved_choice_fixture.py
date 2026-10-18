"""Real admitted-package/SQLite fixture for the frontend receipt regression.

No provider is invoked. Narration is explicitly seeded local text, written through
the ordinary authority narration API so its persistence and rendering can be tested.
"""

import json
import sys
from pathlib import Path

from sylvia_gm.application.authority import AuthorityOwner, ResolveMechanicsChoiceCommand
from sylvia_gm.domain.authority import ProseChannel
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests.test_alpha_f3_package_execution import _archive, _choice_runtime_yaml, _command, _create


def saved_reply(owner):
    snapshot = owner.snapshot("campaign-runtime")
    return {
        "revision": snapshot.revision,
        "action_count": len(snapshot.actions),
        "state": dict(snapshot.state),
        "projections": [
            {
                "kind": p.kind.value,
                "sequence": p.sequence,
                "action_id": p.action_id,
                "safe_payload": p.safe_payload,
                "text": p.text,
            }
            for p in owner.player_projection("campaign-runtime")
        ],
    }


def narrate(owner, attempt, text, action=None):
    owner.start_narration_attempt(
        campaign_id="campaign-runtime",
        attempt_id=attempt,
        turn_id="turn-runtime",
        cancel=lambda: None,
        last_action_id=action,
    )
    owner.emit_narration_token("campaign-runtime", attempt, ProseChannel.TALK, text)
    owner.complete_narration_attempt("campaign-runtime", attempt)


def main():
    mode, database = sys.argv[1], Path(sys.argv[2])
    if mode == "init":
        assert not database.exists(), "The regression must use a fresh isolated database."
    with AuthorityOwner.open(database, "owner-runtime", disconnect_grace_seconds=1) as owner:
        if mode == "init":
            _create(owner, _archive(runtime_bytes=_choice_runtime_yaml()))
            narrate(
                owner,
                "opening-receipt-regression",
                "The relay hums while you choose how to ground the signal.",
            )
            pending = owner.execute_action(_command(revision=0, suffix="saved-ui-choice", steps=3))
            assert pending.status == "awaiting_choice"
            reply = saved_reply(owner)
            assert reply["revision"] == 0 and reply["action_count"] == 0
        elif mode == "resolve":
            command = json.load(sys.stdin)
            resolved = owner.resolve_mechanics_choice(
                ResolveMechanicsChoiceCommand(
                    campaign_id=command["campaignId"],
                    action_id=command["actionId"],
                    resolution_id=command["resolutionId"],
                    expected_revision=command["expectedRevision"],
                    owner_id="owner-runtime",
                    choice_id=command["choiceId"],
                    option_id=command["optionId"],
                    choice_sequence=command.get("choiceSequence", 0),
                )
            )
            reply = {"status": resolved.status.value, **saved_reply(owner)}
            assert reply["revision"] == 1 and reply["action_count"] == 1
            assert reply["state"]["synthetic.clockwork.state"] == {"counter": 0, "bonus": 3}
        elif mode == "narrate":
            narrate(
                owner,
                "response-receipt-regression",
                "The grounded relay steadies. You can continue onward.",
                "action-saved-ui-choice",
            )
            reply = saved_reply(owner)
        elif mode == "reopen":
            reply = saved_reply(owner)
        else:
            raise ValueError(mode)
    print(canonical_json_bytes(reply).decode())


if __name__ == "__main__":
    main()
