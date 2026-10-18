"""Real SDK stream failures retain provider categories after response headers arrive."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import anthropic
import httpx
import openai
import pytest

from sylvia_gm.provider._admin_audit import AdminAuditedProvider
from sylvia_gm.provider._anthropic import AnthropicMessagesTransport, AnthropicProvider
from sylvia_gm.provider._base import ModelInfo, ProviderStreamChunk
from sylvia_gm.provider._credentials import Credential, CredentialPool
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    IncompleteProviderStreamError,
    InvalidRequestError,
    InvalidResponseError,
    InvalidToolArgumentsError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    TokenLimitError,
)
from sylvia_gm.provider._openai_compat import ChatCompletionsTransport, OpenAICompatProvider
from sylvia_gm.provider._retry_after import retry_after_seconds
from tests.test_alpha_f6e_owner_session_transport import (
    _create_builder_session,
    _OwnerHarness,
    _ScriptedProvider,
)


@pytest.mark.asyncio
async def test_openai_stream_initial_rate_limit_uses_sylvia_error_type():
    """An HTTP rejection before the first chunk must reach player-facing error mapping."""
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "37"}, json={
            "error": {"type": "rate_limit_error", "code": "rate_limit_exceeded",
                      "message": "Synthetic rate limit"},
        })

    provider = OpenAICompatProvider(
        base_url="http://synthetic.invalid/v1", api_key="synthetic",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        model_info=ModelInfo(id="synthetic", name="Synthetic", provider_name="Synthetic",
                            provider_key="custom", provider_mode="byok", context_window=8192),
    )
    try:
        with pytest.raises(RateLimitError) as caught:
            async for _ in provider.generate_stream([{"role": "user", "content": "Hello"}]):
                pass
        assert caught.value.retry_after == 37
        assert len(requests) == 1
    finally:
        await provider.close()


@pytest.mark.parametrize("api", ["openai", "anthropic"])
@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize(("header", "expected"), [
    ("37", 37), ("1.5", 1.5), ("0", 0), (None, None), ("invalid-private-text", None),
    ("nan", None), ("inf", None), ("-2", None),
])
def test_http_rate_limit_retains_only_valid_retry_timing(api, configured, header, expected):
    """Real SDK status errors retain numeric timing through either mapping policy."""
    response = httpx.Response(429, request=httpx.Request("POST", "https://synthetic.invalid"),
                              headers={} if header is None else {"Retry-After": header})
    sdk = openai if api == "openai" else anthropic
    error = sdk.RateLimitError("PRIVATE_TRANSPORT_DETAIL", response=response, body=None)
    transport = ChatCompletionsTransport() if api == "openai" else AnthropicMessagesTransport()
    mapper = (transport._raise_from_openai_error if api == "openai"
              else transport._raise_from_anthropic_error)
    with pytest.raises(RateLimitError) as caught:
        mapper(error, retryable_statuses={429} if configured else None)
    assert caught.value.retry_after == expected


@pytest.mark.parametrize("api", ["openai", "anthropic"])
@pytest.mark.parametrize("configured", [False, True])
def test_http_rejection_is_distinct_from_malformed_output(api, configured):
    response = httpx.Response(400, request=httpx.Request("POST", "https://synthetic.invalid"))
    sdk = openai if api == "openai" else anthropic
    error = sdk.BadRequestError("Unsupported parameter", response=response, body=None)
    transport = ChatCompletionsTransport() if api == "openai" else AnthropicMessagesTransport()
    mapper = (transport._raise_from_openai_error if api == "openai"
              else transport._raise_from_anthropic_error)
    with pytest.raises(InvalidRequestError):
        mapper(error, retryable_statuses={429} if configured else None)


@pytest.mark.parametrize("api", ["openai", "anthropic"])
@pytest.mark.parametrize("configured", [False, True])
def test_http_context_rejection_does_not_expose_raw_provider_text(api, configured):
    response = httpx.Response(400, request=httpx.Request("POST", "https://synthetic.invalid"))
    sdk = openai if api == "openai" else anthropic
    error = sdk.BadRequestError("context exceeded: PRIVATE_TRANSPORT_DETAIL", response=response,
                               body={"request": "PRIVATE_REQUEST_BODY"})
    transport = ChatCompletionsTransport() if api == "openai" else AnthropicMessagesTransport()
    mapper = (transport._raise_from_openai_error if api == "openai"
              else transport._raise_from_anthropic_error)
    with pytest.raises(TokenLimitError) as caught:
        mapper(error, retryable_statuses={429} if configured else None)
    assert "context" in str(caught.value).lower()
    assert "PRIVATE_" not in str(caught.value)


def test_retry_after_http_date_and_absent_response():
    """Date deadlines become a duration; absent or expired headers cannot invent a wait."""
    def error_at(deadline):
        response = httpx.Response(429, request=httpx.Request("POST", "https://synthetic.invalid"),
                                  headers={"Retry-After": format_datetime(deadline, usegmt=True)})
        return openai.RateLimitError("synthetic", response=response, body=None)

    delay = retry_after_seconds(error_at(datetime.now(UTC) + timedelta(seconds=90)))
    assert delay is not None and 88 <= delay <= 90
    assert retry_after_seconds(error_at(datetime.now(UTC) - timedelta(seconds=90))) is None
    assert retry_after_seconds(Exception("no response")) is None


@pytest.mark.parametrize(("failure", "expected"), [
    (AuthenticationError("PRIVATE_TRANSPORT_DETAIL"), "sign in"),
    (RateLimitError("PRIVATE_TRANSPORT_DETAIL", retry_after=37), "37"),
    (RateLimitError("PRIVATE_TRANSPORT_DETAIL"), "usage limit"),
    (ModelNotFoundError("PRIVATE_TRANSPORT_DETAIL"), "model selection"),
    (ProviderConnectionError("PRIVATE_TRANSPORT_DETAIL"), "could not connect"),
    (InvalidResponseError("PRIVATE_TRANSPORT_DETAIL"), "invalid response"),
    (IncompleteProviderStreamError("PRIVATE_TRANSPORT_DETAIL"), "ended before"),
    (InvalidToolArgumentsError("PRIVATE_TRANSPORT_DETAIL"), "invalid tool arguments"),
    (ProviderUnavailableError("PRIVATE_TRANSPORT_DETAIL"), "unavailable"),
    (InvalidRequestError("PRIVATE_TRANSPORT_DETAIL"), "rejected the request"),
    (ProviderError("PRIVATE_TRANSPORT_DETAIL"), "no specific cause"),
])
def test_owner_explains_provider_recovery_without_exposing_raw_errors(tmp_path, failure, expected):
    """Ordinary players receive the recovery reason without opening private audit records."""
    class FailedProvider(_ScriptedProvider):
        async def generate_stream(self, *args, **kwargs):
            raise failure
            yield  # retain the provider's asynchronous stream interface

    harness = _OwnerHarness(tmp_path / "provider-detail.db", FailedProvider(()))
    try:
        _, session_id = _create_builder_session(harness, "provider-detail")
        harness.send("failure", {"method": "submit_session_input", "params": {
            "session_id": session_id, "attempt_id": "failure", "message_id": "failure",
            "turn_id": "failure", "user_text": "Continue the draft.",
        }})
        reply = harness.read()
        assert reply["ok"] is False
        assert expected in reply["error"]["message"].lower()
        assert "PRIVATE_TRANSPORT_DETAIL" not in str(reply)
        if isinstance(failure, RateLimitError) and failure.retry_after is None:
            assert "Retry delay" not in reply["error"]["message"]
    finally:
        harness.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("terminal", "error_type"), [
    (False, IncompleteProviderStreamError), (True, InvalidToolArgumentsError),
])
async def test_audit_distinguishes_incomplete_stream_from_completed_invalid_arguments(
    terminal, error_type,
):
    """The same partial argument has a different failure when a terminal event arrives."""
    requests = []

    def respond(request):
        requests.append(request)
        content = (
            b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
            b'"content_block":{"type":"tool_use","id":"call-test","name":"save","input":{}}}\n\n'
            b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
            b'"delta":{"type":"input_json_delta","partial_json":"{\\"value\\":"}}\n\n'
        )
        if terminal:
            content += b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content)

    base = AnthropicProvider(
        base_url="http://synthetic.invalid/v1",
        credential_pool=CredentialPool([Credential(type="api_key", value="synthetic")]),
        model_info=ModelInfo(id="synthetic", name="Synthetic", provider_name="Synthetic",
                            provider_key="custom", provider_mode="byok", context_window=8192,
                            transport="anthropic_messages"),
    )
    await base._client.close()
    base._client = anthropic.AsyncAnthropic(
        api_key="synthetic", base_url="http://synthetic.invalid/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    traces = []
    audited = AdminAuditedProvider(base, sink=traces.append, session_id="synthetic", role="builder")
    chunks = []
    try:
        with pytest.raises(error_type) as caught:
            async for chunk in audited.generate_stream([{"role": "user", "content": "Test"}]):
                chunks.append(chunk)
        assert isinstance(caught.value, InvalidResponseError)
        assert len(requests) == 1
        assert any(chunk.tool_call_delta for chunk in chunks)
        assert not any(chunk.anthropic_content for chunk in chunks)
        response = next(t for t in traces if t["kind"] == "provider_stream_response")
        assert response["status"] == "failed"
        assert response["chunks"][-1] == {"error_type": error_type.__name__}
        assert response["retry_count"] == 0
    finally:
        await audited.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(("key", "headers", "credential_type"), [
    ("", {}, "none"), ("", {}, "api_key"), ("synthetic-test-key", {}, "api_key"),
    ("", {"X-Api-Key": "synthetic-header-key"}, "none"),
    ("", {"Authorization": "Bearer synthetic-header-token"}, "none"),
])
async def test_anthropic_optional_auth_reaches_real_sdk_transport(
    streaming, key, headers, credential_type, monkeypatch,
):
    """The supported no-key connection must work beyond the Settings HTTP probe."""
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers.get("x-api-key", "") == headers.get("X-Api-Key", key)
        assert request.headers.get("authorization", "") == headers.get("Authorization", "")
        if streaming:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=(
                b'event: content_block_start\ndata: {"type":"content_block_start","index":0,'
                b'"content_block":{"type":"text","text":""}}\n\n'
                b'event: content_block_delta\ndata: {"type":"content_block_delta","index":0,'
                b'"delta":{"type":"text_delta","text":"Ready."}}\n\n'
                b'event: content_block_stop\ndata: {"type":"content_block_stop","index":0}\n\n'
                b'event: message_delta\ndata: {"type":"message_delta",'
                b'"delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":1}}\n\n'
                b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
            ))
        return httpx.Response(200, json={
            "id": "synthetic", "type": "message", "role": "assistant", "model": "synthetic",
            "content": [{"type": "text", "text": "Ready."}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        })

    provider = AnthropicProvider(
        base_url="http://127.0.0.1:58333/v1",
        credential_pool=CredentialPool([Credential(type=credential_type, value=key)]),
        default_headers=headers,
        model_info=ModelInfo(id="synthetic", name="Synthetic", provider_name="Synthetic",
                            provider_key="custom", provider_mode="byok", context_window=8192,
                            transport="anthropic_messages"),
    )
    await provider._client.close()
    provider._client = anthropic.AsyncAnthropic(
        api_key="", base_url="http://127.0.0.1:58333/v1", max_retries=0, default_headers=headers,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    try:
        messages = [{"role": "user", "content": "Synthetic connection check."}]
        if streaming:
            chunks = [chunk async for chunk in provider.generate_stream(messages)]
            assert "".join(chunk.content or "" for chunk in chunks) == "Ready."
        else:
            assert (await provider.generate(messages)).content == "Ready."
        assert len(requests) == 1
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_anthropic_read_timeout_after_streaming_is_a_provider_failure():
    """httpx can fail while the SDK consumes SSE, outside its request-error wrapper."""
    requests = []

    class TimedOutStream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield (
                b"event: content_block_start\n"
                b'data: {"type":"content_block_start","index":0,'
                b'"content_block":{"type":"text","text":""}}\n\n'
                b"event: content_block_delta\n"
                b'data: {"type":"content_block_delta","index":0,'
                b'"delta":{"type":"text_delta","text":"Draft work is retained."}}\n\n'
            )
            raise httpx.ReadTimeout("PRIVATE_TRANSPORT_DETAIL")

        async def aclose(self):
            self.closed = True

    stream = TimedOutStream()

    def respond(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    provider = AnthropicProvider(
        base_url="https://synthetic.invalid",
        credential_pool=CredentialPool([Credential(type="api_key", value="synthetic")]),
        model_info=ModelInfo(
            id="synthetic/model",
            name="Synthetic",
            provider_name="Synthetic",
            provider_key="synthetic",
            provider_mode="byok",
            context_window=8192,
            transport="anthropic_messages",
        ),
    )
    await provider._client.close()
    provider._client = anthropic.AsyncAnthropic(
        api_key="synthetic",
        base_url="https://synthetic.invalid",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    chunks = []
    try:
        with pytest.raises(ProviderTimeoutError) as caught:
            async for chunk in provider.generate_stream([{"role": "user", "content": "Continue"}]):
                chunks.append(chunk)
        assert "timed out" in str(caught.value)
        assert "PRIVATE_TRANSPORT_DETAIL" not in str(caught.value)
        assert "".join(chunk.content or "" for chunk in chunks) == "Draft work is retained."
        assert len(requests) == 1
        assert stream.closed
    finally:
        await provider.close()


def test_local_stream_fault_is_not_blamed_on_provider(tmp_path):
    """Worker programming errors remain local faults without exposing exception contents."""
    class BrokenProvider(_ScriptedProvider):
        async def generate_stream(self, *args, **kwargs):
            raise TypeError("PRIVATE_TRANSPORT_DETAIL")
            yield

    harness = _OwnerHarness(tmp_path / "local-fault.db", BrokenProvider(()))
    try:
        _, session_id = _create_builder_session(harness, "local-fault")
        harness.send("failure", {"method": "submit_session_input", "params": {
            "session_id": session_id, "attempt_id": "failure", "message_id": "failure",
            "turn_id": "failure", "user_text": "Continue the draft.",
        }})
        reply = harness.read()
        assert reply["ok"] is False
        assert reply["error"]["code"] == "internal_error", reply
        assert "internal error" in reply["error"]["message"].lower()
        assert "PRIVATE_TRANSPORT_DETAIL" not in str(reply)
    finally:
        harness.close()


def test_builder_timeout_is_explained_and_accepts_a_later_message(tmp_path):
    class TimeoutThenResume(_ScriptedProvider):
        attempts = 0

        async def generate_stream(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                yield ProviderStreamChunk(content="Working on the draft.")
                raise ProviderTimeoutError("PRIVATE_TRANSPORT_DETAIL")
            yield ProviderStreamChunk(content="Resumed the saved work.", finish_reason="stop")

    provider = TimeoutThenResume(())
    harness = _OwnerHarness(tmp_path / "timeout.db", provider)
    try:
        _, session_id = _create_builder_session(harness, "timeout")
        params = {
            "session_id": session_id,
            "attempt_id": "first",
            "message_id": "first",
            "turn_id": "timeout-turn",
            "user_text": "Continue the draft.",
        }
        harness.send("first", {"method": "submit_session_input", "params": params})
        failed = harness.read()
        assert failed["ok"] is False
        assert failed["error"]["code"] == "provider_error", failed
        assert "timed out" in failed["error"]["message"]
        assert "PRIVATE_TRANSPORT_DETAIL" not in str(failed)
        assert provider.attempts == 1
        resumed = harness.request(
            "resume",
            "submit_session_input",
            {
                **params,
                "attempt_id": "resume",
                "message_id": "resume",
                "user_text": "Continue",
            },
        )
        assert resumed["interruption"] is None
        assert provider.attempts == 2
    finally:
        harness.close()
