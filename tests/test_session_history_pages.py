"""Display pagination reads bounded rows without changing the agent's saved context."""

from pathlib import Path

import pytest

from sylvia_gm.agent._session_loop import CampaignSessionLoop
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.infrastructure import sqlite_session


@pytest.mark.parametrize("attached", [False, True])
def test_recent_history_pages_are_bounded_stable_and_leave_full_context_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attached: bool,
) -> None:
    with AuthorityOwner.open(tmp_path / "history.sqlite3", "history-owner") as owner:
        if attached:
            owner.create_guided_campaign_draft(
                operation_id="campaign", display_name="History fixture",
                builder_session_id="chat", builder_provider_id="test", builder_model_id="test",
            )
        else:
            owner.create_builder_chat(provider_id="test", model_id="test", session_id="chat")
        for index in range(120):
            owner.append_agent_message(
                session_id="chat", message_id=f"message-{index}", kind=SessionMessageKind.USER,
                content={"text": f"Saved message {index}"},
            )
        complete = owner.session_context("chat")
        decoded: list[int] = []
        original = sqlite_session.message_from_row

        def capture(row):
            decoded.append(row["sequence"])
            return original(row)

        monkeypatch.setattr(sqlite_session, "message_from_row", capture)
        page = owner.session_history_page("chat", limit=25)
        assert page.messages == complete.messages[-25:]
        assert len(decoded) == 25
        assert page.next_before_sequence == page.messages[0].sequence
        assert page.latest_sequence == complete.messages[-1].sequence
        owner.append_agent_message(
            session_id="chat", message_id="new-arrival", kind=SessionMessageKind.USER,
            content={"text": "Arrived while scrolling"},
        )
        older = owner.session_history_page(
            "chat", before_sequence=page.next_before_sequence, limit=25,
        )
        assert older.messages == complete.messages[-50:-25]
        found = list(page.messages)
        cursor = page.next_before_sequence
        while cursor is not None:
            older = owner.session_history_page("chat", before_sequence=cursor, limit=25)
            found = list(older.messages) + found
            cursor = older.next_before_sequence
        assert tuple(found) == complete.messages
        assert owner.session_context("chat").messages[:-1] == complete.messages
        for invalid in (0, -1, 201, True):
            with pytest.raises(ValueError):
                owner.session_history_page("chat", limit=invalid)


def test_activity_indicator_reads_only_attempt_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with AuthorityOwner.open(tmp_path / "activity.sqlite3", "activity-owner") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="chat")
        owner.append_agent_message(
            session_id="chat", message_id="input", kind=SessionMessageKind.USER,
            content={"text": "Build", "attempt_id": "attempt"},
        )
        for index in range(80):
            owner.append_agent_message(
                session_id="chat", message_id=f"prose-{index}", kind=SessionMessageKind.TALK,
                content={"text": "Saved prose"},
            )
        owner.append_agent_message(
            session_id="chat", message_id="done", kind=SessionMessageKind.EVIDENCE,
            content={"kind": "builder_attempt_terminal", "attempt_id": "attempt",
                     "status": "completed"},
        )
        decoded = []
        original = sqlite_session.message_from_row

        def capture(row):
            decoded.append(row["message_id"])
            return original(row)

        monkeypatch.setattr(sqlite_session, "message_from_row", capture)
        loop = CampaignSessionLoop(owner)
        assert loop.builder_activity("chat") == {"attempt_id": "attempt", "status": "completed"}
        assert decoded == ["input", "done"]
