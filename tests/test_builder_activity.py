"""Saved Builder outcomes survive both ordinary closure and abrupt process loss."""

import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from sylvia_gm.agent._session_loop import (
    CampaignSessionLoop,
    ProviderEvent,
    SessionProviderInput,
    SessionRunRequest,
)
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionMessageKind


def test_builder_delete_rejects_live_work_without_cancelling_it(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    database = tmp_path / "busy-delete.sqlite3"
    with AuthorityOwner.open(database, "test-owner") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
        workspace = tmp_path / "builder-chats" / "builder"
        workspace.mkdir(parents=True)
        (workspace / "unfinished.md").write_text("Keep while busy", encoding="utf-8")
        loop = CampaignSessionLoop(owner)

        def provider(_request: SessionProviderInput) -> Iterator[ProviderEvent]:
            entered.set()
            assert release.wait(timeout=5)
            yield ProviderEvent.talk("Work completed after deletion was rejected.")

        worker = threading.Thread(
            target=lambda: loop.run_builder(
                SessionRunRequest("builder", "turn", "attempt", "input", "Build"),
                provider,
                lambda: None,
            )
        )
        worker.start()
        assert entered.wait(timeout=5)
        with pytest.raises(CommandRejected) as rejected:
            owner.delete_builder_chat(
                "builder", confirmation="permanently delete builder"
            )
        assert rejected.value.code == "builder_chat_work_in_progress"
        assert (workspace / "unfinished.md").read_text(encoding="utf-8") == "Keep while busy"
        release.set()
        worker.join(timeout=5)
        assert not worker.is_alive()
        assert owner.session_context("builder").messages[-2].content["text"] == (
            "Work completed after deletion was rejected."
        )


@pytest.mark.parametrize("outcome", ["completed", "stopped", "interrupted"])
def test_builder_outcome_and_saved_prose_survive_reopen(tmp_path: Path, outcome: str) -> None:
    database = tmp_path / "authority.sqlite3"
    with AuthorityOwner.open(database, "test-owner") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
        loop = CampaignSessionLoop(owner)

        def provider(request: SessionProviderInput) -> Iterator[ProviderEvent]:
            assert loop.builder_activity("builder") == {
                "attempt_id": "attempt", "status": "active", "status_hint": None,
            }
            yield ProviderEvent.private_reasoning(
                "Synthetic saved thinking", response_id="response",
            )
            yield ProviderEvent.private_continuation(
                {"anthropic_content": [{"type": "redacted_thinking", "data": "opaque-synthetic"}]},
                response_id="response",
            )
            yield ProviderEvent.talk("This complete paragraph is saved.")
            yield ProviderEvent.prose_complete()
            if outcome == "stopped":
                request.control.cancel()
            elif outcome == "interrupted":
                raise RuntimeError("synthetic provider failure")
            yield ProviderEvent.talk("The final paragraph.")

        request = SessionRunRequest("builder", "turn", "attempt", "input", "Create a character")
        if outcome == "completed":
            loop.run_builder(request, provider, lambda: None)
        else:
            with pytest.raises((CommandRejected, RuntimeError)):
                loop.run_builder(request, provider, lambda: None)
        assert loop.builder_activity("builder") == {"attempt_id": "attempt", "status": outcome}
        original = owner.session_context("builder")
    with AuthorityOwner.open(database, "test-owner") as owner:
        loop = CampaignSessionLoop(owner)
        assert owner.session_context("builder") == original
        assert "Synthetic saved thinking" not in repr(original)
        assert "opaque-synthetic" not in repr(original)
        assert owner.provider_continuation(
            "builder", "response", provider_id="test", model_id="test"
        ) == {"anthropic_content": [{"type": "redacted_thinking", "data": "opaque-synthetic"}]}
        assert owner.provider_continuation(
            "builder", "response", provider_id="different", model_id="test"
        ) is None
        assert owner.inspect_provider_thinking("builder", "attempt", "response") == (
            "Synthetic saved thinking"
        )
        assert owner.private_provider_index("builder")[0]["status"] == (
            "completed" if outcome == "completed" else "interrupted"
        )
        assert loop.builder_activity("builder") == {"attempt_id": "attempt", "status": outcome}
        talks = [m.content["text"] for m in original.messages if m.kind is SessionMessageKind.TALK]
        assert talks == (
            ["This complete paragraph is saved.", "The final paragraph."]
            if outcome == "completed"
            else ["This complete paragraph is saved."]
        )
        assert loop.builder_activity("builder", attempt_id="never-started") == {
            "attempt_id": "never-started",
            "status": "unavailable",
        }


def test_abrupt_builder_process_exit_preserves_work_and_reports_interruption(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite3"
    workspace_file = tmp_path / "unfinished-character.md"
    with AuthorityOwner.open(database, "test-owner") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="builder")
    script = """
import os
import sys
from pathlib import Path
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.agent._session_loop import CampaignSessionLoop, ProviderEvent, SessionRunRequest
with AuthorityOwner.open(Path(sys.argv[1]), 'test-owner') as owner:
    def provider(request):
        Path(sys.argv[2]).write_text('Saved unfinished character', encoding='utf-8')
        yield ProviderEvent.talk('The first section is saved.')
        yield ProviderEvent.prose_complete()
        os._exit(73)
    CampaignSessionLoop(owner).run_builder(
        SessionRunRequest('builder', 'turn', 'lost-attempt', 'input', 'Build a scout'),
        provider, lambda: None,
    )
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(database), str(workspace_file)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert process.returncode == 73, process.stdout + process.stderr
    with AuthorityOwner.open(database, "test-owner") as owner:
        loop = CampaignSessionLoop(owner)
        assert loop.builder_activity("builder") == {
            "attempt_id": "lost-attempt",
            "status": "interrupted",
        }
        context = owner.session_context("builder")
        assert [
            m.content["text"] for m in context.messages if m.kind is SessionMessageKind.TALK
        ] == ["The first section is saved."]
        assert workspace_file.read_text(encoding="utf-8") == "Saved unfinished character"
        assert not any(
            m.content.get("kind") == "builder_attempt_terminal" for m in context.messages
        )
        loop.run_builder(
            SessionRunRequest("builder", "turn", "continued", "next-input", "Continue"),
            lambda request: iter([ProviderEvent.talk("Finished the character.")]),
            lambda: None,
        )
        assert loop.builder_activity("builder") == {
            "attempt_id": "continued",
            "status": "completed",
        }
        assert (
            loop.builder_activity("builder", attempt_id="lost-attempt")["status"] == "interrupted"
        )
