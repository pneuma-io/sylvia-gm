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
