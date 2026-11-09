"""Truthful live facts and durable timing on the shared response substrate."""

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from sylvia_gm.agent._activity import AttemptProgress, operation_category
from sylvia_gm.agent._mode import AgentMode
from sylvia_gm.agent._runner import AgentRunner
from sylvia_gm.agent._session_loop import (
    CampaignSessionLoop,
    ProviderControl,
    ProviderEvent,
    SessionRunRequest,
)
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.interfaces.owner_process import _dispatch_selected
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from sylvia_gm.provider._exceptions import ProviderConnectionError, ProviderTimeoutError


def test_observed_time_is_not_advanced_by_polling_and_terminal_time_freezes():
    now = [10.0]
    progress = AttemptProgress(lambda: now[0])
    progress.update("provider")
    now[0] = 75.0
    first = progress.snapshot()
    assert first["phase"] == "provider"
    assert first["elapsed_ms"] == first["quiet_ms"] == 65000
    assert progress.snapshot() == first
    progress.observe()
    assert progress.snapshot()["quiet_ms"] == 0
    assert progress.snapshot()["phase"] == "working"
    progress.update("tool", "tool_123456789abc_read_campaign_reference")
    now[0] = 80.0
    assert progress.snapshot()["operation"] == "read"
    assert progress.snapshot()["quiet_ms"] == 5000
    progress.update("retrying")
    progress.update("provider")
    assert progress.snapshot()["phase"] == "retrying"
    ended = progress.finish("failed")
    now[0] = 200.0
    progress.observe()
    progress.update("working")
    assert progress.snapshot() == ended
    assert progress.finish("completed") == ended


def test_a_planned_correction_is_not_displayed_as_a_retry_until_the_request_starts():
    progress = AttemptProgress()
    progress.update("working")
    progress.expect_correction()
    assert progress.snapshot()["phase"] == "working"
    progress.update("provider")
    assert progress.snapshot()["phase"] == "retrying"


@pytest.mark.parametrize("streaming", [False, True])
def test_builder_runner_reports_actual_provider_and_tool_boundaries(tmp_path, streaming):
    from tests.test_builder_library import LibraryProvider

    progress = AttemptProgress()
    observed = []

    class ObservedProvider(LibraryProvider):
        async def generate(self, *args, **kwargs):
            assert progress.snapshot()["phase"] in {"provider", "retrying"}
            return await super().generate(*args, **kwargs)

    provider = ObservedProvider(responses=[
        ProviderResponse(tool_calls=[ToolCall(
            id="bad", name="read_file", arguments=json.dumps({"path": "missing.md"}),
        )]),
        ProviderResponse(tool_calls=[ToolCall(
            id="good", name="read_file", arguments=json.dumps({"path": "notes.md"}),
        )]),
        ProviderResponse(content="Ready."),
    ])
    (tmp_path / "notes.md").write_text("PRIVATE SOURCE CONTENT")

    def update(phase, tool):
        progress.update(phase, tool)
        observed.append(progress.snapshot())

    runner = AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM, provider=provider, history_path=None,
        initial_history=[], project_root=tmp_path, system_prompt_override="Synthetic test",
        trusted_system_override=True, prompt_mode="replace", activity_progress=update,
    )

    async def run():
        if streaming:
            await runner.turn_stream("Read the notes.", asyncio.Queue(), retry_on_length=False)
        else:
            await runner.turn("Read the notes.")

    asyncio.run(run())
    assert len(provider.calls) == 3
    assert sum(item["phase"] == "retrying" for item in observed) == 1
    tools = [item for item in observed if item["phase"] == "tool"]
    assert [item["operation"] for item in tools] == ["read", "read"]
    assert "PRIVATE" not in str(observed) and "missing.md" not in str(observed)


def test_missing_transport_lease_cannot_complete_an_admitted_builder_attempt(tmp_path):
    loop = SimpleNamespace(builder_activity=lambda *_args, **_kwargs: {
        "attempt_id": "a", "status": "active",
    })
    result = _dispatch_selected(
        SimpleNamespace(provider_stream_status=lambda *_args: "completed"),
        "provider_stream_status", {"campaign_id": "chat", "attempt_id": "a"},
        application_owner=None, owner_id="test", provider_resolver=None,
        model_choices=lambda: (), session_loop=loop, storage_root=tmp_path,
    )
    assert result == {"status": "active"}


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_retry_freezes_timing_and_preserves_identity_on_reopen(tmp_path, cancelled):
    from tests.test_alpha_f4_generic_sessions import _campaign

    database = tmp_path / "retry-timing.db"
    with AuthorityOwner.open(database, "test", disconnect_grace_seconds=1.0) as owner:
        _campaign(owner, campaign_id="campaign", writer=WriterRole.DM, turn_id="turn")
        owner.create_agent_session(campaign_id="campaign", role=WriterRole.DM,
                                   provider_id="synthetic", model_id="synthetic", session_id="chat")
        loop = CampaignSessionLoop(owner)

        def initial_failure(_request):
            raise ProviderConnectionError("synthetic initial loss")
            yield  # pragma: no cover

        with pytest.raises(ProviderConnectionError):
            request = SessionRunRequest("chat", "turn", "original", "input", "Continue")
            loop.run(request, initial_failure)
        original = owner.retryable_interruption("campaign", failed_attempt_id="original")
        assert original is not None
        now = [10.0]
        control = ProviderControl()
        control.progress = AttemptProgress(lambda: now[0])

        def retry_provider(request):
            assert request.control is control
            request.control.progress.update("provider")
            now[0] = 15.0
            if cancelled:
                request.control.cancel()
                raise CommandRejected("Synthetic cancellation")
            raise ProviderTimeoutError("Synthetic timeout")
            yield  # pragma: no cover

        with pytest.raises((CommandRejected, ProviderTimeoutError)):
            loop.retry("chat", retry_id="retry", interruption_id=original.interruption_id,
                       narrator=lambda _input, _stream: None,
                       provider_factory=lambda _turn: retry_provider, control=control,
                       cancel=control.cancel)
        terminal = next(message.content for message in owner.session_context("chat").messages
                        if message.message_id == "terminal-retry")
        assert terminal["attempt_id"] == "retry"
        assert terminal["input_id"] == "original" and terminal["message_id"] == "input"
        assert terminal["failure_category"] == ("user_cancelled" if cancelled else "transient")
        assert terminal["activity_timing"]["elapsed_ms"] == 5000
        assert terminal["activity_timing"]["phase"] == ("stopped" if cancelled else "failed")
        now[0] = 100.0
        assert control.progress.snapshot() == terminal["activity_timing"]
        assert loop.activity_progress("chat") is None
        assert owner.retryable_interruption("campaign", failed_attempt_id="retry") is not None
    with AuthorityOwner.open(database, "reopen") as owner:
        reopened = next(message.content for message in owner.session_context("chat").messages
                        if message.message_id == "terminal-retry")
        assert reopened == terminal


def test_stop_fences_late_progress_and_tool_names_are_allowlisted():
    progress = AttemptProgress()
    progress.update("tool", "PRIVATE_CAMPAIGN_SECRET")
    assert progress.snapshot()["operation"] == "action"
    assert "PRIVATE" not in str(progress.snapshot())
    progress.update("stopping")
    progress.update("working")
    progress.observe()
    assert progress.snapshot()["phase"] == "stopping"
    with pytest.raises(ValueError):
        progress.update("PRIVATE_REASONING")
    assert operation_category("tool_123456789abc_read_file") == "read"


@pytest.mark.parametrize("stopped", [False, True])
def test_live_progress_is_attempt_scoped_and_terminal_timing_survives_reopen(tmp_path, stopped):
    database = tmp_path / "activity.sqlite3"
    entered = threading.Event()
    release = threading.Event()
    errors = []
    with AuthorityOwner.open(database, "test") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="chat")
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="other")
        loop = CampaignSessionLoop(owner)

        def provider(request):
            request.control.progress.update("tool", "read_file")
            entered.set()
            assert release.wait(5)
            if stopped:
                request.control.cancel()
            yield ProviderEvent.talk("Complete only if still running")

        def run():
            try:
                loop.run_builder(SessionRunRequest("chat", "turn", "attempt", "input", "Build"),
                                 provider, lambda: None)
            except Exception as error:
                errors.append(error)

        worker = threading.Thread(target=run)
        worker.start()
        assert entered.wait(5)
        try:
            progress = loop.activity_progress("chat")
            assert progress["attempt_id"] == "attempt"
            assert progress["basis_message_id"] == "input"
            assert progress["phase"] == "tool" and progress["operation"] == "read"
            assert loop.activity_progress("other") is None
        finally:
            release.set()
            worker.join(5)
        assert not worker.is_alive()
        assert bool(errors) is stopped
        terminal = owner.session_context("chat").messages[-1].content
        assert terminal["activity_timing"]["phase"] == ("stopped" if stopped else "completed")
        assert terminal["activity_timing"]["elapsed_ms"] >= 0
        assert loop.activity_progress("chat") is None
    with AuthorityOwner.open(database, "reopen") as owner:
        assert owner.session_context("chat").messages[-1].content == terminal
        assert "Complete only" not in str(terminal)
