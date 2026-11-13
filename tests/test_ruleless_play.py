"""No Ruleset play keeps Sylvia's campaign memory and refuses game mechanics."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.ruleless_play import (
    RULELESS_GM_GUIDE,
    RULELESS_MECHANICS_REJECTION_CODE,
    mechanics_in_response,
)
from sylvia_gm.domain.authority import PackageLock, WriterRole
from sylvia_gm.provider._base import ProviderStreamChunk
from tests.test_alpha_f6e_owner_session_transport import (
    _campaign_update_tool,
    _OwnerHarness,
    _ScriptedProvider,
)

_GUIDE_HEADING = "NO RULESET PLAY"
_PICK_LOCK = "I try to pick the lock on the lamp-room door."
_ASK_KEY = "Where is the brass key now, and what does it do?"
_MECHANICS_REPLY = (
    "Roll a d20 and add your Dexterity modifier. This is a DC 15 Dexterity check; "
    "on a failure the warden hears you and you take 4 damage."
)
_STORY_REPLY = (
    "The pins resist, then give with a soft click. The lamp-room door swings inward, "
    "but the warden's footsteps have stopped on the stair below."
)
_KEY_REPLY = (
    "The brass key still hangs from Warden Ilse's belt. Its teeth are cut to fit the "
    "lamp-room lock, and turning it twice silences the foghorn."
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Roll a d20 for me.", ("dice notation", "a request to roll")),
        ("Make a Stealth check to slip past.", ("a check or saving throw",
                                                 "a request to make a check")),
        ("You need to beat DC 12.", ("a difficulty number",)),
        ("The blow deals 7 slashing damage.", ("numeric damage",)),
        ("You are down to 3 hit points.", ("numeric health",)),
        ("Roll initiative! Combat round one begins.", ("a request to roll", "turn order")),
        ("You attack with advantage.", ("a mechanical advantage",)),
        ("You gain 50 XP and level up.", ("an action economy term",)),
    ],
)
def test_mechanical_language_is_named(text: str, expected: tuple[str, ...]) -> None:
    assert mechanics_in_response([{"kind": "narration", "text": text}]) == expected
    assert mechanics_in_response(None, text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "The sailors roll dice on an upturned barrel and curse their luck.",
        "Last winter's storm damaged the roof, and rain still drips into the hall.",
        "The smugglers have the advantage of surprise and the tide.",
        "She tells you the history of the lighthouse and checks the lamp oil.",
        "He rolls up his sleeves and makes a careful study of the lock.",
        "A dozen gulls wheel overhead; the 3 fishermen haul in their nets.",
    ],
)
def test_ordinary_fiction_is_not_mechanics(text: str) -> None:
    assert mechanics_in_response([{"kind": "narration", "text": text}]) == ()


def test_ooc_may_explain_that_no_rolls_are_used() -> None:
    explanation = "There are no dice rolls or skill checks in this campaign; I'll judge it."
    assert mechanics_in_response([{"kind": "ooc", "text": explanation}]) == ()
    assert mechanics_in_response([{"kind": "narration", "text": explanation}]) == (
        "a check or saving throw",
    )
    assert mechanics_in_response([{"kind": "ooc", "text": "Please roll a d20."}]) == (
        "dice notation", "a request to roll",
    )
    assert mechanics_in_response(
        [{"kind": "npc_dialogue", "speaker_id": "ilse", "spoken_words": "Make a save!"}]
    ) == ("a request to make a check",)


def test_guide_is_only_for_admitted_no_ruleset_dm_campaigns(tmp_path: Path) -> None:
    with AuthorityOwner.open(tmp_path / "guide.db", "owner-ruleless-guide") as owner:
        for campaign_id, writer in (("ruleless-dm", WriterRole.DM),
                                    ("ruleless-builder", WriterRole.BUILDER)):
            owner.create_campaign(
                CampaignCreate(
                    campaign_id=campaign_id,
                    display_name=campaign_id,
                    writer=writer,
                    package_lock=PackageLock(entries=()),
                    turn_id=f"turn-{campaign_id}",
                )
            )
        assert owner.ruleset_gm_operating_guide("ruleless-dm") == RULELESS_GM_GUIDE
        assert owner.ruleset_gm_operating_guide("ruleless-builder") is None
        assert owner.catalog("ruleless-dm") == ()
    # The guide forbids mechanics generically and adds no D&D-specific vocabulary.
    lowered = RULELESS_GM_GUIDE.casefold()
    for term in ("d20", "hit points", "armor class", "armour class", "spell", "d&d"):
        assert term not in lowered


class _RulelessGM(_ScriptedProvider):
    """A scripted No Ruleset GM that records the world and slips into mechanics once."""

    def __init__(self) -> None:
        super().__init__(())
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []
        self.inventory_revision = 0

    @staticmethod
    def _latest_user(messages: list[dict[str, Any]]) -> tuple[int, str]:
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].get("role") == "user":
                return index, str(messages[index].get("content", ""))
        return -1, ""

    def _payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        index, player = self._latest_user(messages)
        corrected = any(
            message.get("role") == "tool"
            and RULELESS_MECHANICS_REJECTION_CODE in str(message.get("content", ""))
            for message in messages[index + 1:]
        )
        if _PICK_LOCK in player:
            return {
                "state_change": "changed",
                "state_change_summary": "The lamp-room door is open and the warden alerted.",
                "scenes": [{
                    "summary": "The lamp-room door stands open; the warden has stopped below.",
                    "location_id": "lighthouse",
                }],
                "revised_player_facing_response": [{
                    "kind": "narration",
                    "text": _STORY_REPLY if corrected else _MECHANICS_REPLY,
                }],
            }
        if _ASK_KEY in player:
            return {
                "state_change": "unchanged",
                "state_change_summary": "Recalling the recorded key changes nothing.",
                "revised_player_facing_response": [{"kind": "narration", "text": _KEY_REPLY}],
            }
        return {
            "state_change": "changed",
            "state_change_summary": "The lighthouse, its warden and the brass key are set up.",
            "entities": [
                {
                    "entity_id": "lighthouse",
                    "entity_kind": "place",
                    "name": "Greyhook Lighthouse",
                    "attributes": {"description": "A salt-stained tower above the harbour."},
                    "visibility": "player_known",
                    "known_by": ["player"],
                },
                {
                    "entity_id": "ilse",
                    "entity_kind": "person",
                    "name": "Warden Ilse",
                    "attributes": {"location": "lighthouse", "status": "wary of strangers"},
                    "visibility": "player_known",
                    "known_by": ["player"],
                },
                {
                    "entity_id": "brass-key",
                    "entity_kind": "object",
                    "name": "Brass key",
                    "attributes": {
                        "description": "A heavy brass key with sea-green verdigris.",
                        "purpose": "Opens the lamp-room lock; turning it twice silences "
                                   "the foghorn.",
                        "location": "On Warden Ilse's belt",
                    },
                    "visibility": "player_known",
                    "known_by": ["player"],
                },
            ],
            "facts": [{
                "fact_id": "foghorn-silenced-wrecks",
                "category": "discovery",
                "text": "Ships have wrecked on nights the foghorn fell silent.",
                "visibility": "player_known",
                "known_by": ["player"],
                "subjects": ["lighthouse", "brass-key"],
            }],
            "inventory_updates": [{
                "expected_revision": self.inventory_revision,
                "holdings": [{
                    "holding_id": "brass-key-holding",
                    "item_id": "brass-key",
                    "name": "Brass key",
                    "kind": "item",
                    "stackable": False,
                    "quantity": 1,
                    "owner_id": "ilse",
                    "holder_id": "ilse",
                    "visibility": "player_known",
                    "known_by": ["player"],
                }],
            }],
            "scenes": [{
                "summary": "Night at Greyhook Lighthouse; the foghorn is sounding.",
                "location_id": "lighthouse",
            }],
            "revised_player_facing_response": [{
                "kind": "narration",
                "text": "Fog rolls over Greyhook. Warden Ilse watches you from the stair, "
                        "a brass key heavy at her belt.",
            }],
        }

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        self.calls.append((messages.copy(), tools))
        tool_name = _campaign_update_tool(tools)
        if not tool_name:
            yield ProviderStreamChunk(content="Fog gathers.", finish_reason="stop")
            return
        yield ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": f"ruleless-call-{len(self.calls)}",
                "function": {"name": tool_name, "arguments": json.dumps(self._payload(messages))},
            },
            finish_reason="tool_calls",
        )


def _system_text(messages: list[dict[str, Any]]) -> str:
    return "\n".join(
        str(message.get("content", "")) for message in messages if message.get("role") == "system"
    )


def _released_narration(harness: _OwnerHarness, session_id: str, request_id: str) -> list[str]:
    context = harness.request(request_id, "session_context", {"session_id": session_id})
    return [
        str(message["content"]["safe_payload"]["text"])
        for message in context["messages"]
        if message["kind"] == "canonical_reference"
        and isinstance(message["content"].get("safe_payload"), dict)
        and "text" in message["content"]["safe_payload"]
    ]


def _start_no_ruleset_campaign(harness: _OwnerHarness) -> tuple[str, str, str]:
    """Drive the real New Campaign interview with the No Ruleset choice."""

    draft = harness.request("draft", "create_guided_campaign_draft", {
        "operation_id": "ruleless-draft",
        "display_name": "Greyhook",
        "provider_id": "test.transport",
        "model_id": "test.transport/model0",
    })
    campaign_id = draft["campaign_id"]
    builder_session_id = draft["builder_session_id"]
    answers = (
        ("ruleset", {"candidate_id": None}),
        ("name", {"display_name": "Greyhook"}),
        ("boundaries_preferences", {"boundaries": "No gore", "preferences": "Mystery"}),
        ("specific_requests", {"no_additional_requests": False,
                               "requests": "I am a courier sheltering at a lighthouse."}),
    )
    for revision, (step, answer) in enumerate(answers):
        harness.request(f"answer-{step}", "advance_guided_campaign_draft", {
            "operation_id": f"ruleless-answer-{step}",
            "campaign_id": campaign_id,
            "builder_session_id": builder_session_id,
            "expected_construction_revision": revision,
            "current_step": step,
            "visible_answer": str(answer),
            "answer": answer,
        })
    harness.request("start", "start_guided_campaign", {
        "operation_id": "ruleless-start",
        "campaign_id": campaign_id,
        "builder_session_id": builder_session_id,
        "display_name": "Greyhook",
        "candidate_id": None,
        "setting_id": None,
        "plan_id": None,
        "player_character_id": None,
        "companion_ids": [],
        "premise": "I am a courier sheltering at a lighthouse.",
    })
    finalized = harness.request("finalize", "finalize_campaign_construction", {
        "campaign_id": campaign_id,
        "dm_provider_id": "test.transport",
        "dm_model_id": "test.transport/model0",
    })
    return campaign_id, finalized["handoff"]["dm_session_id"], finalized["dm_turn_id"]


def test_no_ruleset_play_records_world_refuses_mechanics_and_survives_reopen(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ruleless.sqlite3"
    provider = _RulelessGM()
    harness = _OwnerHarness(database, provider)
    try:
        campaign_id, session_id, turn_id = _start_no_ruleset_campaign(harness)
        provider.inventory_revision = harness.request(
            "snapshot-before-opening", "snapshot", {"campaign_id": campaign_id}
        )["revision"]
        harness.request("open", "start_dm_session", {
            "campaign_id": campaign_id, "session_id": session_id, "turn_id": turn_id,
        })
        opening_messages, opening_tools = provider.calls[-1]
        system = _system_text(opening_messages)
        assert _GUIDE_HEADING in system
        assert "open-ended narrative sandbox with no Ruleset" in system
        advertised = {tool["function"]["name"] for tool in opening_tools or []}
        assert len(advertised) == 3
        assert all(
            name.endswith(("_search_campaign_knowledge", "_read_campaign_reference",
                           "_update_campaign_file"))
            for name in advertised
        )

        calls_before_lock = len(provider.calls)
        result = harness.request("lock", "submit_session_input", {
            "session_id": session_id,
            "turn_id": turn_id,
            "attempt_id": "attempt-ruleless-lock",
            "message_id": "message-ruleless-lock",
            "user_text": _PICK_LOCK,
        })
        assert result["receipts"] == []
        lock_calls = provider.calls[calls_before_lock:]
        assert len(lock_calls) == 2
        correction = [
            json.loads(str(message["content"]))
            for message in lock_calls[1][0]
            if message.get("role") == "tool"
            and RULELESS_MECHANICS_REJECTION_CODE in str(message.get("content", ""))
        ]
        assert correction and correction[-1]["state_changed"] is False
        assert "dice notation" in correction[-1]["findings"]
        narration = _released_narration(harness, session_id, "context-after-lock")
        assert narration[-1] == _STORY_REPLY
        assert all(_MECHANICS_REPLY not in text for text in narration)
        snapshot = harness.request("snapshot-after-lock", "snapshot", {"campaign_id": campaign_id})
        assert snapshot["package_lock"]["entries"] == []
    finally:
        harness.close()

    with AuthorityOwner.open(database, "owner-ruleless-reopened") as owner:
        key = owner.read_campaign_reference(campaign_id, source="campaign_state", path="brass-key")
        assert "turning it twice silences" in key["content"]
        assert "On Warden Ilse's belt" in key["content"]
        assert owner.catalog(campaign_id) == ()

    resumed = _OwnerHarness(database, provider)
    try:
        calls_before_resume = len(provider.calls)
        resumed.request("ask", "submit_session_input", {
            "session_id": session_id,
            "turn_id": turn_id,
            "attempt_id": "attempt-ruleless-key",
            "message_id": "message-ruleless-key",
            "user_text": _ASK_KEY,
        })
        messages, _tools = provider.calls[calls_before_resume]
        system = _system_text(messages)
        assert _GUIDE_HEADING in system
        fresh = next(
            str(message["content"]) for message in messages
            if str(message.get("content", "")).startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
        )
        for recorded in ("Opens the lamp-room lock", "On Warden Ilse's belt",
                         "Ships have wrecked", "lamp-room door stands open"):
            assert recorded in fresh
        assert "brass-key-holding" in fresh
        assert _released_narration(resumed, session_id, "context-after-key")[-1] == _KEY_REPLY
    finally:
        resumed.close()
