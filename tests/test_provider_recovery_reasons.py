"""Native stop reasons retain their recovery meaning through the owner boundary."""

from types import SimpleNamespace

import pytest

from sylvia_gm.agent._working_context import WorkingContextProvider
from sylvia_gm.provider._anthropic import AnthropicMessagesTransport
from sylvia_gm.provider._base import ProviderResponse, ProviderStreamChunk
from sylvia_gm.provider._exceptions import (
    ProviderContinuationRequiredError,
    ProviderOutputLimitError,
    ProviderPausedError,
    ProviderRefusalError,
    TokenLimitError,
)
from tests.test_alpha_f6e_owner_session_transport import (
    _create_builder_session,
    _OwnerHarness,
    _ScriptedProvider,
)


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "stop,expected",
    [
        ("model_context_window_exceeded", "context_length"),
        ("max_tokens", "max_tokens"),
        ("refusal", ProviderRefusalError),
        ("pause_turn", ProviderPausedError),
    ],
)
def test_native_stop_has_distinct_parser_signal(streaming, stop, expected):
    transport = AnthropicMessagesTransport()

    def parse():
        if streaming:
            return transport.parse_stream_chunk(
                SimpleNamespace(
                    type="message_delta",
                    delta=SimpleNamespace(stop_reason=stop),
                )
            )
        return transport.parse_response(
            SimpleNamespace(
                content=[],
                usage=None,
                stop_reason=stop,
            ),
            "fixture",
        )

    if isinstance(expected, str):
        assert parse().finish_reason == (
            "length" if not streaming and stop == "max_tokens" else expected
        )
    else:
        with pytest.raises(expected):
            parse()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("finish", ["context_length", "length", "max_tokens"])
async def test_working_context_separates_capacity_recovery_from_reply_allowance(streaming, finish):
    class LimitedProvider(_ScriptedProvider):
        def __init__(self):
            super().__init__(())
            self.calls = 0

        async def generate(self, *args, **kwargs):
            self.calls += 1
            return ProviderResponse(finish_reason=finish)

        async def generate_stream(self, *args, **kwargs):
            self.calls += 1
            yield ProviderStreamChunk(finish_reason=finish)

    async def save(_record):
        raise AssertionError("No checkpoints should be fabricated by this boundary test")

    provider = LimitedProvider()
    working = WorkingContextProvider(provider, save=save)
    preparations = []

    async def prepare(messages, tools=None, **options):
        preparations.append(options.get("force", False))
        return messages

    working.prepare = prepare
    expected = TokenLimitError if finish == "context_length" else ProviderOutputLimitError
    with pytest.raises(expected):
        if streaming:
            async for _ in working.generate_stream([{"role": "user", "content": "Synthetic"}]):
                pass
        else:
            await working.generate([{"role": "user", "content": "Synthetic"}])
    assert provider.calls == (4 if finish == "context_length" else 1)
    assert preparations == ([False, True, True, True] if finish == "context_length" else [False])


@pytest.mark.asyncio
async def test_context_exhaustion_after_visible_output_never_replays_request():
    class VisibleThenFull(_ScriptedProvider):
        async def generate_stream(self, *args, **kwargs):
            yield ProviderStreamChunk(content="Already visible")
            yield AnthropicMessagesTransport().parse_stream_chunk(
                SimpleNamespace(
                    type="message_delta",
                    delta=SimpleNamespace(stop_reason="model_context_window_exceeded"),
                )
            )

    async def save(_record):
        raise AssertionError("Visible output must not initiate automatic compaction")

    working = WorkingContextProvider(VisibleThenFull(()), save=save)
    seen = []
    with pytest.raises(TokenLimitError):
        async for chunk in working.generate_stream([{"role": "user", "content": "Continue"}]):
            seen.append(chunk.content)
    assert seen == ["Already visible"]


@pytest.mark.parametrize(
    "stop,needle,forbidden",
    [
        ("model_context_window_exceeded", "context", ("response budget", "api format")),
        ("max_tokens", "response budget", ("compact context", "api format")),
        ("refusal", "declined", ("response budget", "api format", "retry")),
        ("pause_turn", "server-tool continuation", ("response budget", "api format", "retry")),
        (
            "gemini_continuation",
            "cannot perform automatically",
            ("response budget", "api format", "retry"),
        ),
    ],
)
def test_native_reason_reaches_sanitized_owner_message_and_preserves_saved_chat(
    tmp_path, stop, needle, forbidden
):
    class NativeStopProvider(_ScriptedProvider):
        async def generate_stream(self, *args, **kwargs):
            yield ProviderStreamChunk(content="Synthetic partial response")
            if stop == "gemini_continuation":
                raise ProviderContinuationRequiredError(
                    "PRIVATE_TRANSPORT_DETAIL",
                    continuation_token="PRIVATE_TOKEN",
                    native_parts=[{"text": "PRIVATE_PARTS"}],
                )
            yield AnthropicMessagesTransport().parse_stream_chunk(
                SimpleNamespace(
                    type="message_delta",
                    delta=SimpleNamespace(stop_reason=stop),
                )
            )

    harness = _OwnerHarness(tmp_path / "native-recovery.db", NativeStopProvider(()))
    session_id = None
    try:
        _, session_id = _create_builder_session(harness, "native-recovery")
        harness.request("audit", "set_admin_full_audit", {"enabled": True})
        harness.send(
            "failure",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "attempt_id": "failure",
                    "message_id": "failure",
                    "turn_id": "failure",
                    "user_text": "Continue the synthetic draft.",
                },
            },
        )
        reply = harness.read()
        assert reply["ok"] is False
        message = reply["error"]["message"].lower()
        assert needle in message
        assert all(word not in message for word in forbidden)
        assert "PRIVATE_" not in str(reply)
        before = harness.request("history", "session_history_page", {"session_id": session_id})
    finally:
        harness.close()
    reopened = _OwnerHarness(tmp_path / "native-recovery.db", None)
    try:
        after = reopened.request("reopened", "session_history_page", {"session_id": session_id})
        assert after == before
        assert "Continue the synthetic draft." in str(after)
        assert "PRIVATE_" not in str(after)
    finally:
        reopened.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["length", "context_length"])
async def test_rejected_terminal_closes_entire_provider_chain_in_current_context(finish):
    from sylvia_gm.provider._admin_audit import AdminAuditedProvider
    from sylvia_gm.provider._context_fit import ContextFittingProvider
    from sylvia_gm.provider._reasoning import TurnReasoningProvider, select_reasoning
    from sylvia_gm.provider._request_metadata import SessionBoundRequestMetadataProvider

    class ObservedProvider(_ScriptedProvider):
        closed = False

        async def generate_stream(self, *args, **kwargs):
            try:
                yield ProviderStreamChunk(content="Synthetic visible response")
                yield ProviderStreamChunk(finish_reason=finish)
            finally:
                self.closed = True

    async def save(_record):
        raise AssertionError("A visible rejected response cannot replay through compaction")

    base = ObservedProvider(())
    selected = SessionBoundRequestMetadataProvider(base, headers={"X-Test": "synthetic"})
    selected = TurnReasoningProvider(selected, select_reasoning(None, "builder"))
    selected = ContextFittingProvider(selected)
    audit = []
    selected = AdminAuditedProvider(
        selected, sink=audit.append, session_id="synthetic", role="builder"
    )
    working = WorkingContextProvider(selected, save=save)
    expected = TokenLimitError if finish == "context_length" else ProviderOutputLimitError
    with pytest.raises(expected):
        async for _ in working.generate_stream([{"role": "user", "content": "Synthetic"}]):
            pass
    assert base.closed
    assert base.hooks._scoped_hooks.get() == ()
    assert audit[-1]["kind"] == "provider_stream_response"
    assert audit[-1]["status"] == "failed"
