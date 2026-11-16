"""A GM turn that ends without narration asks its provider to release what it keeps.

Some providers keep a runtime open between two requests of one turn. When the player
presses Stop (during a request or while a tool runs between requests), or the turn ends in
an error, the owner calls ``release_idle`` once, without letting it delay or mask the Stop.
A turn that ends in narration does not call it.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from sylvia_gm.agent import _provider_session
from sylvia_gm.agent._provider_runtime import _PROVIDER_ASYNC_RUNTIME
from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._session_loop import (
    CampaignSessionLoop,
    ProviderControl,
    ProviderEvent,
    ProviderEventKind,
    SessionProviderInput,
    SessionRunRequest,
    SessionRunResult,
    _close_provider_events,
)
from sylvia_gm.agent._working_context import WorkingContextProvider
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import RetryNarrationInput, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.provider._admin_audit import AdminAuditedProvider
from sylvia_gm.provider._base import (
    FakeProvider,
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
    release_provider_idle,
)
from sylvia_gm.provider._context_fit import ContextFittingProvider
from sylvia_gm.provider._exceptions import ProviderConnectionError, ProviderTimeoutError
from sylvia_gm.provider._fallback_chain import FallbackChain
from sylvia_gm.provider._reasoning import ReasoningSelection, TurnReasoningProvider
from sylvia_gm.provider._request_metadata import SessionBoundRequestMetadataProvider
from tests.test_alpha_f4_generic_sessions import _clockwork_campaign
from tests.test_alpha_f4_provider_session_adapter import _OwnerProjection, _provider_input

_WAIT = 10.0
_Script = Callable[
    [SessionProviderInput, ActionCommand, threading.Event], Iterator[ProviderEvent]
]


class _KeepingProvider(Provider):
    """Fake provider that records requests and every ``release_idle`` call in order."""

    def __init__(
        self,
        rounds: list[object] | None = None,
        *,
        release_error: BaseException | None = None,
        release_hangs: bool = False,
    ) -> None:
        super().__init__()
        self.rounds = list(rounds or [])
        self.order: list[str] = []
        self.release_calls = 0
        self.started = threading.Event()
        self.released = threading.Event()
        self.release_cancelled = threading.Event()
        self._release_error = release_error
        self._release_hangs = release_hangs

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        raise NotImplementedError

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del messages, tools, timeout, kwargs
        self.order.append("request")
        step = self.rounds.pop(0)
        try:
            if step == "block":
                self.started.set()
                await asyncio.sleep(30)
                yield ProviderStreamChunk(content="MUST NOT ARRIVE")
            elif isinstance(step, BaseException):
                raise step
            else:
                for chunk in cast(list[ProviderStreamChunk], step):
                    await asyncio.sleep(0)
                    yield chunk
        finally:
            self.order.append("request_closed")

    async def close(self) -> None:
        return None

    async def release_idle(self) -> None:
        self.release_calls += 1
        self.order.append("release")
        self.released.set()
        if self._release_hangs:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                self.release_cancelled.set()
                raise
        if self._release_error is not None:
            raise self._release_error


def _drain_provider_loop() -> None:
    """Let every release already scheduled on the shared provider loop start its call."""

    loop = _PROVIDER_ASYNC_RUNTIME.loop()
    for _ in range(3):
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(_WAIT)


def _released_once(provider: _KeepingProvider) -> bool:
    assert provider.released.wait(_WAIT)
    _drain_provider_loop()
    return provider.release_calls == 1


def _adapter(provider: Provider) -> ProviderSessionAdapter:
    return ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()), provider, owner_id="owner-provider"
    )


# The adapter's own turn loop with fake providers.


def test_stop_during_a_request_releases_once_after_the_request_is_cancelled() -> None:
    provider = _KeepingProvider(["block"])
    control = ProviderControl()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            tuple(_adapter(provider).for_turn("turn-provider")(_provider_input(control)))
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert provider.started.wait(_WAIT)
        control.cancel()
        worker.join(_WAIT)
    finally:
        control.cancel()
        worker.join(_WAIT)
    assert not worker.is_alive()
    assert len(errors) == 1 and isinstance(errors[0], CommandRejected)
    assert "cancelled" in str(errors[0])
    assert _released_once(provider)
    assert provider.order == ["request", "request_closed", "release"]


def test_a_turn_that_ends_in_a_provider_error_releases_once() -> None:
    provider = _KeepingProvider([ProviderTimeoutError("synthetic timeout")])

    with pytest.raises(ProviderTimeoutError):
        tuple(_adapter(provider).for_turn("turn-provider")(_provider_input()))

    assert _released_once(provider)


def test_a_turn_that_ends_in_narration_does_not_release() -> None:
    provider = _KeepingProvider(
        [[ProviderStreamChunk(content="Rain taps on the shutters.", finish_reason="stop")]]
    )

    events = tuple(_adapter(provider).for_turn("turn-provider")(_provider_input()))

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "Rain taps on the shutters."
    ]
    _drain_provider_loop()
    assert provider.release_calls == 0
    assert not provider.released.is_set()


def test_events_closed_before_the_turn_finishes_release_once() -> None:
    """Closing the stream between two requests (while a tool runs) ends the turn early."""

    provider = _KeepingProvider()
    adapter = _adapter(provider)
    paused = threading.Event()

    def events(
        provider_input: SessionProviderInput, *, turn_id: str
    ) -> Iterator[ProviderEvent]:
        del provider_input, turn_id
        yield ProviderEvent.safe_think("Working.")
        paused.set()
        yield ProviderEvent.safe_think("Never reached.")

    adapter._events = events  # type: ignore[method-assign]  # noqa: SLF001
    stream = iter(adapter.for_turn("turn-provider")(_provider_input()))
    assert next(stream).text == "Working."

    _close_provider_events(stream)
    _close_provider_events(stream)

    assert _released_once(provider)


def test_a_failed_narration_retry_releases_once() -> None:
    provider = _KeepingProvider()
    source = _provider_input()
    narrator = _adapter(provider).for_retry("synthetic-model", provider_input=source)

    with pytest.raises(CommandRejected):
        narrator(
            RetryNarrationInput(
                campaign_id="campaign-provider",
                interruption_id="interruption-missing",
                retry_id="retry-missing",
                failed_attempt_id="attempt-that-never-ran",
                canonical_high_water=0,
                turn_id="turn-provider",
                action_receipt=None,
            ),
            cast(Any, object()),
        )

    assert _released_once(provider)


# The real session loop, with a scripted turn behind the adapter's releasing boundary.


def _scripted_loop_run(
    tmp_path: Path,
    provider: _KeepingProvider,
    script: _Script,
    *,
    stop_when_ready: bool,
) -> tuple[list[SessionRunResult], list[BaseException], int | None, float]:
    """Run one GM turn through ``CampaignSessionLoop`` and optionally press Stop."""

    ready = threading.Event()
    calls: list[int] = []
    results: list[SessionRunResult] = []
    errors: list[BaseException] = []
    cancellation_count: int | None = None
    with AuthorityOwner.open(
        tmp_path / "release.db", "owner-release", disconnect_grace_seconds=1.0
    ) as owner:
        _clockwork_campaign(
            owner, campaign_id="campaign-release", writer=WriterRole.BUILDER,
            turn_id="turn-release", calls=calls,
        )
        owner.create_agent_session(
            campaign_id="campaign-release",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-release",
            selected_skill="BUILD RULESET",
        )
        entry = owner.current_package_lock("campaign-release").package_lock.entries[0]
        command = ActionCommand(
            campaign_id="campaign-release",
            action_id="action-release",
            idempotency_key="idem-release",
            owner_id="owner-release",
            writer=WriterRole.BUILDER,
            expected_revision=0,
            turn_id="turn-release",
            package_id=entry.package_id,
            tool_name="advance",
            parameters={"steps": 2},
        )
        adapter = ProviderSessionAdapter(owner, provider, owner_id="owner-release")

        def events(
            provider_input: SessionProviderInput, *, turn_id: str
        ) -> Iterator[ProviderEvent]:
            assert turn_id == "turn-release"
            return script(provider_input, command, ready)

        adapter._events = events  # type: ignore[method-assign]  # noqa: SLF001
        loop = CampaignSessionLoop(owner)

        def run() -> None:
            try:
                results.append(
                    loop.run(
                        SessionRunRequest(
                            session_id="session-release",
                            turn_id="turn-release",
                            attempt_id="attempt-release",
                            message_id="message-release",
                            user_text="Advance the clock.",
                        ),
                        adapter.for_turn("turn-release"),
                    )
                )
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=run)
        started = time.monotonic()
        worker.start()
        if stop_when_ready:
            assert ready.wait(_WAIT)
            cancellation_count = loop.stop("session-release").cancellation_count
        worker.join(_WAIT)
        elapsed = time.monotonic() - started
        assert not worker.is_alive()
        assert calls == [2]
    return results, errors, cancellation_count, elapsed


def _tool_then_next_request(
    provider_input: SessionProviderInput, command: ActionCommand, ready: threading.Event
) -> Iterator[ProviderEvent]:
    """Run a tool, then try to admit the next request as the real turn loop does."""

    yield ProviderEvent.action(command)
    ready.set()
    assert provider_input.control.wait_cancelled(_WAIT)
    provider_input.control.admit(lambda: None)
    yield ProviderEvent.talk("Must not be narrated.")


def _tool_then_more_events(
    provider_input: SessionProviderInput, command: ActionCommand, ready: threading.Event
) -> Iterator[ProviderEvent]:
    """Run a tool, then keep producing events; the session loop stops reading them."""

    yield ProviderEvent.action(command)
    ready.set()
    assert provider_input.control.wait_cancelled(_WAIT)
    yield ProviderEvent.safe_think("Still thinking.")
    yield ProviderEvent.talk("Must not be narrated.")


@pytest.mark.parametrize("script", [_tool_then_next_request, _tool_then_more_events])
def test_stop_while_the_tool_runs_releases_once_and_stops_as_before(
    tmp_path: Path,
    script: _Script,
) -> None:
    provider = _KeepingProvider()

    results, errors, cancellation_count, _ = _scripted_loop_run(
        tmp_path, provider, script, stop_when_ready=True
    )

    assert not errors and len(results) == 1
    assert results[0].interruption is not None
    assert results[0].interruption.failed_attempt_id == "attempt-release"
    assert cancellation_count == 1
    assert _released_once(provider)


def test_session_loop_turn_ending_in_an_error_releases_once(tmp_path: Path) -> None:
    def failing(
        provider_input: SessionProviderInput, command: ActionCommand, ready: threading.Event
    ) -> Iterator[ProviderEvent]:
        del provider_input, ready
        yield ProviderEvent.action(command)
        raise ProviderConnectionError("synthetic disconnect")

    provider = _KeepingProvider()

    results, errors, _, _ = _scripted_loop_run(tmp_path, provider, failing, stop_when_ready=False)

    assert not results and len(errors) == 1
    assert isinstance(errors[0], ProviderConnectionError)
    assert _released_once(provider)


def test_session_loop_side_error_while_the_turn_is_paused_releases_once(tmp_path: Path) -> None:
    """The loop rejects an event it was handed; the paused turn is closed and released."""

    def malformed(
        provider_input: SessionProviderInput, command: ActionCommand, ready: threading.Event
    ) -> Iterator[ProviderEvent]:
        del provider_input, ready
        yield ProviderEvent.action(command)
        yield ProviderEvent(ProviderEventKind.TALK)
        yield ProviderEvent.talk("Must not be narrated.")

    provider = _KeepingProvider()

    results, errors, _, _ = _scripted_loop_run(
        tmp_path, provider, malformed, stop_when_ready=False
    )

    assert not results and len(errors) == 1
    assert isinstance(errors[0], TypeError)
    # The failed run's traceback is still held here, so only a prompt close can release.
    assert _released_once(provider)


def test_session_loop_turn_ending_in_narration_does_not_release(tmp_path: Path) -> None:
    def narrated(
        provider_input: SessionProviderInput, command: ActionCommand, ready: threading.Event
    ) -> Iterator[ProviderEvent]:
        del provider_input, ready
        yield ProviderEvent.action(command)
        yield ProviderEvent.talk("The clock advances two steps.")

    provider = _KeepingProvider()

    results, errors, _, _ = _scripted_loop_run(tmp_path, provider, narrated, stop_when_ready=False)

    assert not errors and len(results) == 1 and results[0].interruption is None
    _drain_provider_loop()
    assert provider.release_calls == 0


@pytest.mark.parametrize("failure", ["raises", "hangs"])
def test_a_failing_or_hanging_release_never_delays_or_masks_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    monkeypatch.setattr(_provider_session, "_RELEASE_IDLE_TIMEOUT_SECONDS", 0.5)
    caplog.set_level(logging.WARNING, logger=_provider_session.__name__)
    provider = _KeepingProvider(
        release_error=RuntimeError("synthetic release failure") if failure == "raises" else None,
        release_hangs=failure == "hangs",
    )

    results, errors, cancellation_count, elapsed = _scripted_loop_run(
        tmp_path, provider, _tool_then_next_request, stop_when_ready=True
    )

    assert not errors and len(results) == 1
    assert results[0].interruption is not None
    assert cancellation_count == 1
    assert _released_once(provider)
    if failure == "hangs":
        # The Stop finished without waiting for the hung release; the bound then ended it.
        assert elapsed < 30
        assert provider.release_cancelled.wait(_WAIT)
    deadline = time.monotonic() + _WAIT
    expected = "did not finish" if failure == "hangs" else "Provider idle release failed"
    while expected not in caplog.text and time.monotonic() < deadline:
        _drain_provider_loop()
        time.sleep(0.05)
    assert expected in caplog.text


# The interface default and every wrapper between the GM adapter and the concrete provider.


class _SilentProvider(Provider):
    """A provider that does not override ``release_idle``."""

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        return ProviderResponse(content="ok")

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        yield ProviderStreamChunk(content="ok", finish_reason="stop")

    async def close(self) -> None:
        return None


def test_the_default_release_does_nothing_and_leaves_the_provider_usable() -> None:
    silent = _SilentProvider()
    fake = FakeProvider(responses=[ProviderResponse(content="still here")])

    async def exercise() -> tuple[object, object, str | None]:
        first = await silent.release_idle()
        second = await fake.release_idle()
        await release_provider_idle(object())
        response = await fake.generate([{"role": "user", "content": "hello"}])
        return first, second, response.content

    assert asyncio.run(exercise()) == (None, None, "still here")
    assert "release_idle" not in _SilentProvider.__dict__
    assert "release_idle" not in getattr(Provider, "__abstractmethods__", frozenset())


def test_a_provider_without_an_override_still_stops_as_before() -> None:
    class Blocking(_SilentProvider):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()

        async def generate_stream(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            timeout: float = 120.0,
            **kwargs: object,
        ) -> AsyncIterator[ProviderStreamChunk]:
            self.started.set()
            await asyncio.sleep(30)
            yield ProviderStreamChunk(content="MUST NOT ARRIVE")

    provider = Blocking()
    control = ProviderControl()
    canceller = threading.Thread(
        target=lambda: provider.started.wait(_WAIT) and control.cancel()
    )
    canceller.start()
    try:
        with pytest.raises(CommandRejected, match="cancelled"):
            tuple(_adapter(provider).for_turn("turn-provider")(_provider_input(control)))
    finally:
        control.cancel()
        canceller.join(_WAIT)
    _drain_provider_loop()


async def _no_save(payload: dict[str, Any]) -> None:
    del payload


def _wrappers(inner: Provider) -> dict[str, Provider]:
    return {
        "context_fit": ContextFittingProvider(inner),
        "turn_reasoning": TurnReasoningProvider(
            inner, ReasoningSelection(None, None, None, None, "mode_default")
        ),
        "admin_audit": AdminAuditedProvider(
            inner, sink=lambda _record: None, session_id="session-wrap", role="dm"
        ),
        "session_request_metadata": SessionBoundRequestMetadataProvider(
            inner, headers={"x-example": "1"}
        ),
        "working_context": WorkingContextProvider(inner, records=[], save=_no_save),
        "fallback_chain": FallbackChain([inner]),
    }


@pytest.mark.parametrize(
    "wrapper",
    [
        "context_fit",
        "turn_reasoning",
        "admin_audit",
        "session_request_metadata",
        "working_context",
        "fallback_chain",
    ],
)
def test_every_wrapper_forwards_release_once(wrapper: str) -> None:
    inner = _KeepingProvider()
    outer = _wrappers(inner)[wrapper]

    asyncio.run(outer.release_idle())

    assert inner.release_calls == 1


def test_the_full_gm_chain_forwards_release_to_the_concrete_provider() -> None:
    inner = _KeepingProvider()
    chain: Provider = FallbackChain([inner])
    chain = SessionBoundRequestMetadataProvider(chain, headers={"x-example": "1"})
    chain = AdminAuditedProvider(chain, sink=lambda _record: None, session_id="s", role="dm")
    chain = TurnReasoningProvider(chain, ReasoningSelection(None, None, None, None, "mode_default"))
    chain = ContextFittingProvider(chain)
    chain = WorkingContextProvider(chain, records=[], save=_no_save)

    asyncio.run(chain.release_idle())

    assert inner.release_calls == 1


def test_fallback_chain_releases_every_provider_even_when_one_fails() -> None:
    failing = _KeepingProvider(release_error=RuntimeError("first failed"))
    second = _KeepingProvider()
    third = _SilentProvider()

    with pytest.raises(RuntimeError, match="first failed"):
        asyncio.run(FallbackChain([failing, second, third]).release_idle())

    assert failing.release_calls == 1 and second.release_calls == 1
