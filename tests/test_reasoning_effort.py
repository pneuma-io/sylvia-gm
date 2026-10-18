"""AR-20 native capability, saved discovery, and actual transport request boundaries."""

import json
from dataclasses import replace
from typing import Any

import httpx
import openai
import pytest
import yaml

from sylvia_gm.agent._provider_resolver import ConfiguredProviderResolver
from sylvia_gm.config import chatgpt_settings
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.provider._admin_audit import AdminAuditedProvider
from sylvia_gm.provider._anthropic import AnthropicMessagesTransport
from sylvia_gm.provider._base import ModelInfo, Provider, ProviderResponse, ProviderStreamChunk
from sylvia_gm.provider._credentials import Credential, CredentialPool
from sylvia_gm.provider._openai_compat import (
    ChatCompletionsTransport,
    OpenAICompatProvider,
    ResponsesTransport,
)
from sylvia_gm.provider._reasoning import (
    TurnReasoningProvider,
    configure_reasoning,
    mode_reasoning_default,
    reasoning_values,
    select_reasoning,
)
from tests.test_pes82_chatgpt_failures import SyntheticRuntime, provider_for
from tests.test_pes82_chatgpt_settings import SyntheticAccount


def model(model_id="gpt-5.2", transport="responses", endpoint="https://api.openai.com/v1"):
    return configure_reasoning(
        ModelInfo(
            id=f"test/{model_id}",
            name=model_id,
            provider_key="test",
            provider_name="Test",
            provider_mode="byok",
            provider_model_id=model_id,
            transport=transport,
            max_output_tokens=8192,
            builder_output_tokens=4096,
        ),
        endpoint,
    )


@pytest.mark.parametrize(
    "transport,field",
    [
        ("responses", "reasoning"),
        ("chat_completions", "reasoning_effort"),
    ],
)
def test_direct_gpt_modes_use_native_request_and_preserve_budget(transport, field):
    info = model(transport=transport)
    adapter = ResponsesTransport() if transport == "responses" else ChatCompletionsTransport()
    for role, effort in (("dm", "high"), ("builder", "xhigh")):
        selection = select_reasoning(info, role)
        request = adapter.format_request(
            info.wire_model_id, [], [], True, max_tokens=4096, **selection.options()
        )
        assert request[field] == ({"effort": effort} if field == "reasoning" else effort)
        assert request.get("max_output_tokens", request.get("max_tokens")) == 4096
        assert "service_tier" not in request
        assert selection.projection()["provider_reported_effective"] is None
    assert info.max_output_tokens == 8192
    assert info.builder_output_tokens == 4096


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("reported", [None, "low"])
async def test_response_effort_reaches_audit_without_guessing_from_request(streaming, reported):
    """Exercise real SDK parsing/filtering: requested High need not equal reported Low."""
    traces = []
    info = model()
    response = {
        "id": "synthetic", "object": "response", "created_at": 1,
        "model": "gpt-5.2", "status": "completed", "output": [],
        "reasoning": None if reported is None else {"effort": reported},
        "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
    }

    def respond(request):
        body = json.loads(request.content)
        assert body["reasoning"] == {"effort": "high"}
        assert body["max_output_tokens"] == 4096
        if streaming:
            event = {"type": "response.completed", "sequence_number": 0, "response": response}
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=f"event: response.completed\ndata: {json.dumps(event)}\n\n",
            )
        return httpx.Response(200, json=response)

    base = OpenAICompatProvider(
        base_url="https://synthetic.invalid/v1", model_info=info,
        credential_pool=CredentialPool([Credential(type="api_key", value="synthetic-key")]),
    )
    await base._client.close()
    base._client = openai.AsyncOpenAI(
        api_key="synthetic-key", base_url="https://synthetic.invalid/v1", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    provider = TurnReasoningProvider(
        AdminAuditedProvider(base, sink=traces.append, session_id="synthetic", role="dm"),
        select_reasoning(info, "dm"),
    )
    try:
        if streaming:
            chunks = [chunk async for chunk in provider.generate_stream([], max_tokens=4096)]
            assert chunks[-1].provider_reported_reasoning_effort == reported
            record = next(row for row in traces if row["kind"] == "provider_stream_response")
            assert record["assembled_response"]["provider_reported_reasoning_effort"] == reported
        else:
            result = await provider.generate([], max_tokens=4096)
            assert result.provider_reported_reasoning_effort == reported
            record = next(row for row in traces if row["kind"] == "provider_response")
            assert record["response"]["provider_reported_reasoning_effort"] == reported
        request = next(row for row in traces if row["kind"] == "provider_request")
        selection = request["correlation"]["reasoning_selection"]
        assert selection["effective_request"] == "high"
        assert selection["provider_reported_effective"] is None
    finally:
        await provider.close()


def test_native_anthropic_and_minimax_do_not_fabricate_xhigh():
    claude = model("claude-opus-4-6", "anthropic_messages", "https://api.anthropic.com")
    assert mode_reasoning_default(claude, "builder") == "high"
    request = AnthropicMessagesTransport().format_request(
        claude.wire_model_id,
        [],
        [],
        True,
        max_tokens=8192,
        **select_reasoning(claude, "builder", "max").options(),
    )
    assert request["output_config"] == {"effort": "max"}
    assert request["thinking"] == {"type": "adaptive"}
    assert request["max_tokens"] == 8192
    m3 = model("MiniMax-M3", "anthropic_messages", "https://api.minimax.io/anthropic")
    assert mode_reasoning_default(m3, "dm") == mode_reasoning_default(m3, "builder") == "adaptive"
    assert select_reasoning(m3, "dm", "disabled").options() == {"thinking": {"type": "disabled"}}
    assert select_reasoning(m3, "dm").options() == {"thinking": {"type": "adaptive"}}
    with pytest.raises(CommandRejected, match="does not support"):
        select_reasoning(m3, "builder", "xhigh")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://api.openai.com/v1",
        "https://api.openai.com:444/v1",
        "https://api.openai.com.example.test/v1",
        "http://localhost:1234/v1",
        "https://openrouter.ai/api/v1",
    ],
)
def test_model_name_does_not_grant_effort_support_to_an_unknown_endpoint(endpoint):
    info = model(endpoint=endpoint)
    assert info.supported_reasoning_efforts is None
    assert select_reasoning(info, "builder").options() == {}
    assert select_reasoning(info, "dm", "provider_default").effective is None
    with pytest.raises(CommandRejected, match="does not support"):
        select_reasoning(info, "dm", "high")


@pytest.mark.parametrize(
    "values,suggested",
    [
        (["high"], "medium"),
        ("high", None),
        (["private value"], None),
        (None, "medium"),
    ],
)
def test_invalid_advertised_capability_is_rejected(values, suggested):
    with pytest.raises(ValueError):
        reasoning_values(values, suggested)


def test_saved_subscription_discovery_reaches_picker_and_refresh_preserves_settings(
    tmp_path,
    monkeypatch,
):
    account = SyntheticAccount()
    advertised = [
        {
            "id": "gpt-5.6-sol",
            "supported_reasoning_efforts": ["high", "xhigh", "ultra"],
            "default_reasoning_effort": "high",
        }
    ]
    monkeypatch.setattr(account, "models", lambda: advertised)
    monkeypatch.setattr(chatgpt_settings, "_account", lambda _: account)
    chatgpt_settings.chatgpt_settings_action(
        tmp_path,
        "add_model",
        {
            "id": "gpt-5.6-sol",
            "context_window": 262144,
            "max_output_tokens": 8192,
        },
    )
    path = tmp_path / "config.yaml"
    before = yaml.safe_load(path.read_text())
    choice = ConfiguredProviderResolver(path).model_choices()[0]
    assert choice["supported_reasoning_efforts"] == ["high", "xhigh", "ultra"]
    assert choice["default_reasoning_effort"] == "high"
    assert choice["reasoning_mode_defaults"] == {"dm": "high", "builder": "xhigh"}
    advertised[0]["supported_reasoning_efforts"] = ["low", "high"]
    chatgpt_settings.chatgpt_settings_action(tmp_path, "models")
    after = yaml.safe_load(path.read_text())
    before["providers"]["chatgpt_subscription"]["models"][0]["supported_reasoning_efforts"] = [
        "low",
        "high",
    ]
    assert after == before
    assert ConfiguredProviderResolver(path).model_choices()[0]["reasoning_mode_defaults"] == {
        "dm": "high",
        "builder": "high",
    }


class RecordingProvider(Provider):
    def __init__(self, info):
        super().__init__()
        self.model_info = info
        self.calls = []

    async def generate(self, messages, tools=None, stream=False, timeout=60, **kwargs):
        self.calls.append(kwargs)
        return ProviderResponse(content="Synthetic answer")

    async def generate_stream(self, messages, tools=None, timeout=120, **kwargs):
        self.calls.append(kwargs)
        yield ProviderStreamChunk(content="Synthetic answer", finish_reason="stop")

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_turn_snapshot_wins_through_tool_continuations_without_shared_mutation():
    provider = RecordingProvider(model())
    bound = TurnReasoningProvider(provider, select_reasoning(provider.model_info, "builder", "low"))
    options = {"reasoning": {"effort": "high", "summary": "auto"}, "max_tokens": 4096}
    history = [{"role": "user", "content": "Synthetic question"}]
    await bound.generate(history, **options)
    history.append({"role": "tool", "tool_call_id": "synthetic", "content": "Synthetic result"})
    chunks = [chunk async for chunk in bound.generate_stream(history, **options)]
    assert chunks[-1].finish_reason == "stop"
    assert provider.calls == [
        {"reasoning": {"effort": "low", "summary": "auto"}, "max_tokens": 4096},
        {"reasoning": {"effort": "low", "summary": "auto"}, "max_tokens": 4096},
    ]
    assert options["reasoning"]["effort"] == "high"
    assert "service_tier" not in provider.calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested,expected", [(None, "xhigh"), ("low", "low"), ("provider_default", None)]
)
async def test_subscription_actual_turn_start_uses_validated_native_setting(
    tmp_path,
    requested,
    expected,
):
    class CapturingRuntime(SyntheticRuntime):
        def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            if method == "turn/start":
                self.turn = dict(params)
            return super().call(method, params)

    runtime = CapturingRuntime(
        [
            {
                "method": "turn/completed",
                "params": {"turn": {"status": "completed"}},
            }
        ]
    )
    provider = provider_for(runtime, tmp_path)
    info = configure_reasoning(
        replace(
            provider.model_info,
            supported_reasoning_efforts=("low", "high", "xhigh"),
            default_reasoning_effort="low",
        ),
        "https://chatgpt.com",
    )
    bound = TurnReasoningProvider(provider, select_reasoning(info, "builder", requested))
    await bound.generate([{"role": "user", "content": "Synthetic test"}])
    assert runtime.turn.get("effort") == expected
    if expected is None:
        assert "effort" not in runtime.turn
    assert runtime.turn["model"] == "gpt-5.6-sol"
    assert "serviceTier" not in runtime.turn
    assert "max_tokens" not in runtime.turn
