"""Normal campaign admission, GM tools, act transitions and ending choices compose.

The external provider supplies controlled fictional outcomes; it does not bypass the owner
or substitute for genuine-model judgement about whether those outcomes have been earned.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.provider._base import ProviderStreamChunk
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider
from tests.qa_attached_request import request_attached
from tests.test_fc_campaign_journey import _start_campaign


class _ProgressProvider(LibraryProvider):
    """Perform a bounded sequence using only the GM's actual exposed tool surface."""

    def __init__(self, steps: list[tuple[str, dict]]) -> None:
        super().__init__()
        self.steps = steps
        self.sent = 0
        self.receipts: list[dict] = []
        self.failure: str | None = None

    async def generate_stream(self, messages, tools=None, **_kwargs):
        if self.sent:
            previous = next((message for message in reversed(messages)
                             if message.get("tool_call_id") == f"progress-{self.sent}"), None)
            if previous:
                self.receipts.append(json.loads(previous["content"]))
        if self.sent >= len(self.steps):
            self.failure = "The campaign write did not settle the prepared GM reply."
            raise AssertionError(self.failure)
        suffix, arguments = self.steps[self.sent]
        name = next((tool["function"]["name"] for tool in tools or []
                     if tool["function"]["name"].endswith("_" + suffix)), None)
        if name is None:
            self.failure = f"Required journey tool unavailable: {suffix}"
            raise AssertionError(self.failure)
        self.sent += 1
        yield ProviderStreamChunk(tool_call_delta={
            "index": 0, "id": f"progress-{self.sent}",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }, finish_reason="tool_calls")


def _read_act(number: int) -> tuple[str, dict]:
    """Read the admitted act together with its linked conditions, as instructed to the GM."""
    return "read_campaign_reference", {
        "source": "campaign_plan", "path": f"structured/structure/act-{number}",
    }


def _write(
    text: str, *, resolved: tuple[str, ...] = (), act: int | None = None, ooc: bool = False,
):
    """Save the controlled scenario's established progress and visible narration together."""
    arguments = {
        "state_change": "changed" if resolved or act else "unchanged",
        "state_change_summary": text,
        "revised_player_facing_response": [{"kind": "ooc" if ooc else "narration", "text": text}],
    }
    if resolved:
        arguments["plan_progress"] = [{
            "plan_item_id": item, "progress_status": "resolved", "progress_summary": text,
        } for item in resolved]
    if act:
        arguments["scenes"] = [{"act_id": f"act-{act}", "summary": text}]
    return "update_campaign_file", arguments


def _play(database, campaign, session, number, steps, *, choice=None):
    """Reopen the real owner and submit an ordinary player input or ending-button choice."""
    with AuthorityOwner.open(database, f"progress-before-{number}") as owner:
        revision = owner.snapshot(campaign).revision
        turn = owner.current_open_turn(campaign) or f"progress-turn-{number}"
    provider = _ProgressProvider(steps)
    harness = _OwnerHarness(database, provider)
    try:
        params = {
            "session_id": session, "attempt_id": f"progress-attempt-{number}",
            "message_id": f"progress-message-{number}", "turn_id": turn,
            "expected_revision": revision, "user_text": "Continue the selected scene.",
        }
        if choice:
            params.update(user_text="", campaign_ending_choice={
                "ending_id": "adventure-settled", "choice": choice,
            })
        reply = request_attached(harness, f"play-{number}", "submit_session_input", params,
                                 campaign_id=campaign, attempt_id=params["attempt_id"])
        assert reply["interruption"] is None, reply
    finally:
        harness.close()
        assert provider.failure is None, (provider.failure, provider.receipts)
    assert provider.sent == len(steps)
    return provider


@pytest.mark.parametrize("choice", ["finish", "continue"])
def test_normal_two_act_campaign_completes_and_preserves_the_players_choice(
    tmp_path: Path, choice: str,
) -> None:
    database = tmp_path / "progress.sqlite3"
    campaign, session, _ = _start_campaign(database, "the-long-way-round")
    premature = _play(database, campaign, session, 1, [
        _read_act(2),
        _write("The escort has not happened yet.", resolved=("ending:adventure-settled",)),
        _write("The rescue and escort still need to be played.", act=1),
    ])
    rejected = [receipt for receipt in premature.receipts if receipt.get("status") == "rejected"]
    assert len(rejected) == 1 and rejected[0]["state_changed"] is False, premature.receipts
    with AuthorityOwner.open(database, "progress-premature") as owner:
        assert owner.session_history_page(session).campaign_ending is None
        assert not owner.snapshot(campaign).continuity.plan_progress

    rescue = (
        "Dev and Oss are safe beyond the wayhouse; Ina's escape is confirmed. The crew has "
        "the medicine and marker kit. Oss needs a litter. Keth accepted the iron settlement "
        "and withdrew. On safe ground the crew chooses the ridge route with the rescued cargo."
    )
    first = _play(database, campaign, session, 2, [
        _read_act(1), _write(rescue, resolved=("objective-1", "act-1"), act=1),
    ])
    reference = json.loads(first.receipts[0]["result"]["content"])
    assert reference["act"]["id"] == "act-1"
    assert [item["id"] for item in reference["required_objectives"]] == ["objective-1"]
    _play(database, campaign, session, 3, [
        _read_act(2), _write("The escort enters the ridge road with Oss on his litter.", act=2),
    ])
    with AuthorityOwner.open(database, "progress-act-reopen") as owner:
        snapshot = owner.snapshot(campaign)
        assert snapshot.continuity.current_scene["act_id"] == "act-2"
        progress = {item["plan_item_id"]: item["status"]
                    for item in snapshot.continuity.plan_progress}
        assert progress == {"act-1": "resolved", "objective-1": "resolved"}
        markers = owner.session_history_page(session).act_markers
        assert [(item["act_number"], item["status"]) for item in markers] == [
            (1, "complete"), (2, "begins"),
        ]
        assert owner.session_history_page(session).campaign_ending is None

    # With Act 1 complete, resolving only the ending still cannot skip the escort.
    partial = _play(database, campaign, session, 4, [
        _write("The escort is still on the road.", resolved=("ending:adventure-settled",)),
        _write("The clinic has not yet received the crew.", act=2),
    ])
    assert partial.receipts[0]["status"] == "rejected"
    closing = (
        "The escort reaches the clinic with Dev, Oss and the medicine. The Guild receives "
        "the wagon-loss report and pays the earned fee; the clinic arranges shelter and care. "
        "Keth's settled bargain remains true. The crew thanks the player and the journey ends."
    )
    _play(database, campaign, session, 5, [
        _read_act(2), _write(closing, resolved=(
            "objective-2", "act-2", "ending:adventure-settled",
        ), act=2),
    ])
    with AuthorityOwner.open(database, "progress-ending-reopen") as owner:
        page = owner.session_history_page(session)
        ending = dict(page.campaign_ending)
        assert ending["choice"] is None and ending["open_world"] is True
        assert ending["after_message_id"] in {message.message_id for message in page.messages}
        assert [(item["act_number"], item["status"]) for item in page.act_markers] == [
            (1, "complete"), (2, "begins"), (2, "complete"),
        ]
        expected_progress = owner.snapshot(campaign).continuity.plan_progress
    _play(database, campaign, session, 6, [_write(
        "The story is finished." if choice == "finish" else "What would you like to do next?",
        ooc=True,
    )], choice=choice)
    with AuthorityOwner.open(database, "progress-choice-reopen") as owner:
        assert owner.snapshot(campaign).continuity.plan_progress == expected_progress
        page = owner.session_history_page(session, limit=1)
        assert dict(page.campaign_ending) == {**ending, "choice": choice}
        assert len(page.act_markers) == 3
        selected = next(message for message in owner.session_context(session).messages
                        if message.message_id == "progress-message-6")
        assert selected.content["campaign_ending_choice"]["choice"] == choice
