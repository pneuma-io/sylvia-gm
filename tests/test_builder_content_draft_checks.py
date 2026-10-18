"""Saved Builder chats can check drafts without creating usable library content."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider, launch_library_builder
from tests.test_setting_character_composition import _install_setting


@pytest.mark.parametrize("purpose", ["plan", "setting"])
def test_saved_builder_checks_draft_without_publishing(tmp_path: Path, purpose: str) -> None:
    database, candidate_id = _install_setting(tmp_path)
    if purpose == "plan":
        author_tool = "create_campaign_plan_draft"
        author_params = {
            "title": "Relay Expedition",
            "premise": "Restore the relay.",
            "tone": "Hopeful",
            "player_character_mode": "compatible",
            "starting_location_path": "world.md",
            "opening_situation": "The keeper asks for help.",
            "primary_objective": "Reach the silent relay.",
            "completion_conditions": ["The signal is disabled or the relay abandoned, and the keeper has responded to that outcome."],
            "custom_companion_minimum": 0,
            "custom_companion_maximum": 1,
        }
        check_tool = "check_campaign_plan_draft"
        check_params = {}
        retained_file = "campaign-plan-draft/plan.json"
    else:
        author_tool = "write_file"
        retained_file = "setting-draft/files/world.md"
        author_params = {"path": retained_file, "content": "# Glass Harbor Revised\n"}
        check_tool = "check_campaign_setting_draft"
        check_params = {
            "package_id": "example.glass-harbor",
            "version": "1.1.0",
            "title": "Glass Harbor Revised",
        }
    provider = LibraryProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="author-draft",
                        name=author_tool,
                        arguments=json.dumps(author_params),
                    )
                ]
            ),
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="check-draft",
                        name=check_tool,
                        arguments=json.dumps(check_params),
                    )
                ]
            ),
            ProviderResponse(content="The draft passes its format checks and remains unpublished."),
        ]
    )
    harness = _OwnerHarness(database, provider)
    try:
        _draft, session_id = launch_library_builder(
            harness, candidate_id, "example.glass-harbor", purpose
        )
        candidates = harness.request("before-check", "package_candidates")
        result = harness.request(
            "check-draft",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "check-attempt",
                "message_id": "check-message",
                "user_text": "Prepare the draft and check it.",
            },
        )
        assert result["interruption"] is None
        checked = json.loads(
            next(
                message["content"]
                for message in provider.calls[-1]["messages"]
                if message.get("tool_call_id") == "check-draft"
            )
        )
        assert checked["success"] is True, checked
        assert checked["status"] == "valid_draft"
        assert checked["state_changed"] is False
        assert checked["published"] is False
        if purpose == "plan":
            assert checked["required_characters_ready"] is True
        assert (tmp_path / "builder-chats" / session_id / retained_file).is_file()
        assert not (tmp_path / "campaign-plans").exists()
        assert harness.request("after-check", "package_candidates") == candidates
    finally:
        harness.close()
