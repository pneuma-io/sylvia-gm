"""Character chat and No Ruleset play share one owner without touching each other's state.

The No Ruleset mechanics gate covers Game Master turns in campaigns only. Character chat is a
direct conversation outside any campaign, so its replies are saved as written and never change
a campaign's records, package lock or GM guide. The gate keeps working in the same owner.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sylvia_gm.application.character_profiles import save_character_profile
from sylvia_gm.application.ruleless_play import (
    RULELESS_MECHANICS_REJECTION_CODE,
    mechanics_in_response,
)
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.provider._base import ProviderStreamChunk
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_character_chat import V2_CARD
from tests.test_no_ruleset_campaign_plans import (
    _ASK_ABOUT_KEY,
    _KEY_HANDED_OVER,
    _KEY_RECALLED,
    _WHERE_IS_KEY,
    _fresh_state,
    _PlanAndPlayModel,
)
from tests.test_ruleless_play import (
    _MECHANICS_REPLY,
    _PICK_LOCK,
    _STORY_REPLY,
    _released_narration,
    _RulelessGM,
    _start_no_ruleset_campaign,
)

_CHARACTER_REPLY = "Roll a d20 and add your Dexterity modifier, Mira. *closes the ledger*"


class _SharedProvider(_RulelessGM):
    """One provider for both paths: plain character completions and No Ruleset GM turns."""

    def __init__(self) -> None:
        super().__init__()
        self.character_calls = 0

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        system = "\n".join(
            str(m.get("content", "")) for m in messages if m.get("role") == "system"
        )
        if tools is None and system.startswith("You are Vela."):
            self.character_calls += 1
            yield ProviderStreamChunk(content=_CHARACTER_REPLY, finish_reason="stop")
            return
        async for chunk in super().generate_stream(messages, tools, timeout, **kwargs):
            yield chunk


def test_character_chat_and_no_ruleset_play_share_an_owner_without_crossing(
    tmp_path: Path,
) -> None:
    # The character reply is exactly what the No Ruleset gate would refuse in a campaign turn.
    assert mechanics_in_response(None, _CHARACTER_REPLY)
    provider = _SharedProvider()
    harness = _OwnerHarness(tmp_path / "seams.sqlite3", provider)
    try:
        campaign_id, session_id, turn_id = _start_no_ruleset_campaign(harness)
        provider.inventory_revision = harness.request(
            "snapshot-before-opening", "snapshot", {"campaign_id": campaign_id}
        )["revision"]
        harness.request("open", "start_dm_session", {
            "campaign_id": campaign_id, "session_id": session_id, "turn_id": turn_id,
        })
        before = harness.request("snapshot-before-chat", "snapshot", {"campaign_id": campaign_id})
        guide_before = harness.request(
            "context-before-chat", "session_context", {"session_id": session_id}
        )

        card = save_character_profile(
            harness.storage_root, operation_id="seam-card", source=canonical_json_bytes(V2_CARD)
        )
        chat = harness.request("chat-create", "character_chat", {
            "action": "create", "operation_id": "seam-chat", "artifact_id": card.artifact_id,
            "expected_content_sha256": card.content_sha256,
            "provider_id": "test.transport", "model_id": "test.transport/model0",
            "user_name": "Mira",
        })
        replied = harness.request("chat-send", "send_character_chat_message", {
            "chat_id": chat["chat_id"], "operation_id": "seam-send", "text": "Can I pick it?",
            "expected_message_count": len(chat["messages"]),
        })
        assert provider.character_calls == 1
        # Saved as written: the campaign mechanics gate does not apply to character chat.
        assert replied["messages"][-1]["text"] == _CHARACTER_REPLY
        catalog = harness.request("chat-catalog", "character_chat", {"action": "catalog"})
        assert [item["chat_id"] for item in catalog["chats"]] == [chat["chat_id"]]

        # The campaign is untouched by the chat: same revision, no Ruleset, same session context.
        after = harness.request("snapshot-after-chat", "snapshot", {"campaign_id": campaign_id})
        assert after["revision"] == before["revision"]
        assert after["package_lock"]["entries"] == []
        guide_after = harness.request(
            "context-after-chat", "session_context", {"session_id": session_id}
        )
        assert guide_after["messages"] == guide_before["messages"]

        # The gate still holds back mechanics in the campaign served by the same owner.
        calls_before_lock = len(provider.calls)
        harness.request("lock", "submit_session_input", {
            "session_id": session_id, "turn_id": turn_id,
            "attempt_id": "seam-attempt-lock", "message_id": "seam-message-lock",
            "user_text": _PICK_LOCK,
        })
        gm_calls = [
            call for call in provider.calls[calls_before_lock:] if call[1] is not None
        ]
        assert any(
            message.get("role") == "tool"
            and RULELESS_MECHANICS_REJECTION_CODE in str(message.get("content", ""))
            for message in gm_calls[-1][0]
        )
        narration = _released_narration(harness, session_id, "context-after-lock")
        assert narration[-1] == _STORY_REPLY
        assert all(_MECHANICS_REPLY not in text for text in narration)
        assert all("Can I pick it?" not in text for text in narration)
        assert not any(
            _CHARACTER_REPLY in json.dumps(message) for message in gm_calls[-1][0]
        ), "character chat text never reaches the campaign GM"
    finally:
        harness.close()


class _StructuredSharedProvider(_PlanAndPlayModel):
    """One synthetic provider serves authored Plans, mechanics correction and direct chat."""

    def _gm_payload(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        index, player = self._latest_user(messages)
        if _PICK_LOCK in player:
            corrected = any(
                message.get("role") == "tool"
                and RULELESS_MECHANICS_REJECTION_CODE in str(message.get("content", ""))
                for message in messages[index + 1:]
            )
            return {
                "state_change": "changed",
                "state_change_summary": "The lamp-room door opened; the warden heard it.",
                "facts": [{
                    "fact_id": "lamp-door-opened",
                    "category": "consequence",
                    "text": "The courier opened the lamp-room door; Ilse heard the latch.",
                    "visibility": "player_known",
                    "known_by": ["player"],
                    "subjects": ["lighthouse", "ilse"],
                }],
                "revised_player_facing_response": [{
                    "kind": "narration", "text": _STORY_REPLY if corrected else _MECHANICS_REPLY,
                }],
            }
        return super()._gm_payload(messages)

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        system = "\n".join(
            str(message.get("content", ""))
            for message in messages if message.get("role") == "system"
        )
        if tools is None and system.startswith("You are Vela."):
            if "[fail]" in self._latest_user(messages)[1]:
                raise RuntimeError("synthetic provider outage")
            yield ProviderStreamChunk(content=_CHARACTER_REPLY, finish_reason="stop")
            return
        async for chunk in super().generate_stream(messages, tools, timeout, **kwargs):
            yield chunk


def _prepare_structured_campaign(harness: _OwnerHarness) -> tuple[str, str, str]:
    """Create and bind a Builder-authored Plan through the real owner commands."""

    model = {"provider_id": "test.transport", "model_id": "test.transport/model0"}
    draft = harness.request("draft", "create_guided_campaign_draft", {
        "operation_id": "structured-draft", "display_name": "Greyhook", **model,
    })
    campaign_id, builder = draft["campaign_id"], draft["builder_session_id"]
    for revision, (step, answer) in enumerate((
        ("ruleset", {"candidate_id": None}),
        ("name", {"display_name": "Greyhook"}),
        ("boundaries_preferences", {"boundaries": "No gore", "preferences": "Mystery"}),
        ("specific_requests", {"no_additional_requests": True, "requests": ""}),
    )):
        harness.request(f"answer-{step}", "advance_guided_campaign_draft", {
            "operation_id": f"structured-{step}", "campaign_id": campaign_id,
            "builder_session_id": builder, "expected_construction_revision": revision,
            "current_step": step, "visible_answer": str(answer), "answer": answer,
        })
    harness.request("start", "start_guided_campaign", {
        "operation_id": "structured-start", "campaign_id": campaign_id,
        "builder_session_id": builder, "display_name": "Greyhook", "candidate_id": None,
        "setting_id": None, "plan_id": None, "player_character_id": None,
        "companion_ids": [], "premise": "A courier at a storm-bound lighthouse.",
    })
    harness.request("author", "submit_session_input", {
        "session_id": builder, "turn_id": "turn-author", "attempt_id": "attempt-author",
        "message_id": "message-author", "user_text": "Write the structured Greyhook Plan.",
    })
    finalized = harness.request("bind", "finalize_campaign_construction", {
        "campaign_id": campaign_id, "dm_provider_id": model["provider_id"],
        "dm_model_id": model["model_id"],
    })
    return campaign_id, finalized["handoff"]["dm_session_id"], finalized["dm_turn_id"]


def test_structured_plan_and_character_chat_keep_separate_durable_state(tmp_path: Path) -> None:
    database = tmp_path / "structured-seams.sqlite3"
    provider = _StructuredSharedProvider()
    harness = _OwnerHarness(database, provider)
    try:
        campaign_id, session_id, turn_id = _prepare_structured_campaign(harness)
        harness.request("open", "start_dm_session", {
            "campaign_id": campaign_id, "session_id": session_id, "turn_id": turn_id,
        })
        opening = _fresh_state(provider.calls[-1][0])
        assert all(value in opening for value in (
            "Greyhook Lighthouse", "Warden Ilse", "On Warden Ilse's belt",
            "restarts the foghorn",
        ))
        card = save_character_profile(
            harness.storage_root, operation_id="structured-card",
            source=canonical_json_bytes(V2_CARD),
        )
        chat = harness.request("create-chat", "character_chat", {
            "action": "create", "operation_id": "structured-chat", "artifact_id": card.artifact_id,
            "expected_content_sha256": card.content_sha256,
            "provider_id": "test.transport", "model_id": "test.transport/model0",
        })
        before = harness.request("before-chat", "snapshot", {"campaign_id": campaign_id})
        chat = harness.request("send-chat", "send_character_chat_message", {
            "chat_id": chat["chat_id"], "operation_id": "structured-chat-send", "text": "My key?",
            "expected_message_count": len(chat["messages"]),
        })
        assert chat["messages"][-1]["text"] == _CHARACTER_REPLY
        assert harness.request("after-chat", "snapshot", {"campaign_id": campaign_id}) == before
        for identity, text in (("key", _ASK_ABOUT_KEY), ("lock", _PICK_LOCK)):
            harness.request(identity, "submit_session_input", {
                "session_id": session_id, "turn_id": turn_id,
                "attempt_id": f"structured-{identity}", "message_id": f"message-{identity}",
                "user_text": text,
            })
        narration = _released_narration(harness, session_id, "played-context")
        assert _KEY_HANDED_OVER in narration
        assert narration[-1] == _STORY_REPLY
        assert _MECHANICS_REPLY not in narration
        assert any(
            message.get("role") == "tool"
            and RULELESS_MECHANICS_REJECTION_CODE in str(message.get("content", ""))
            for message in provider.calls[-1][0]
        )
    finally:
        harness.close()

    resumed = _OwnerHarness(database, provider)
    try:
        reopened = resumed.request("reopen-chat", "character_chat", {
            "action": "open", "chat_id": chat["chat_id"],
        })
        assert reopened["messages"] == chat["messages"]
        resumed.request("recall", "submit_session_input", {
            "session_id": session_id, "turn_id": turn_id, "attempt_id": "structured-recall",
            "message_id": "message-recall", "user_text": _WHERE_IS_KEY,
        })
        fresh = _fresh_state(provider.calls[-1][0])
        assert all(value in fresh for value in (
            "lent by Ilse", "restarts the foghorn", "lamp-room door", "Greyhook Lighthouse",
            "Warden Ilse",
        ))
        assert _CHARACTER_REPLY not in json.dumps(provider.calls[-1][0])
        assert len(provider.calls[-1][1] or []) == 3
        snapshot = resumed.request("reopened-state", "snapshot", {"campaign_id": campaign_id})
        assert snapshot["package_lock"]["entries"] == []
        assert _released_narration(resumed, session_id, "recalled-context")[-1] == _KEY_RECALLED
    finally:
        resumed.close()
