"""Direct Anthropic requests never send forced tool choice or temperature where current
Claude models reject them (HTTP 400), and keep both for models known to accept them."""

import json

import httpx
import pytest

from sylvia_gm.config import provider_settings as settings
from sylvia_gm.provider._anthropic import AnthropicMessagesTransport


def _tool(name):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "Synthetic operation.",
            "parameters": {"type": "object", "properties": {}},
        },
    }


ONE_TOOL = [_tool("update_campaign_file")]
SEVERAL_TOOLS = [_tool("update_campaign_file"), _tool("roll_check"), _tool("read_source")]


def _request(model_id, tools):
    return AnthropicMessagesTransport().format_request(
        model_id,
        [{"role": "user", "content": "I open the door."}],
        tools,
        True,
        max_tokens=4096,
        tool_choice="required",
        temperature=0,
    )


@pytest.mark.parametrize(
    "model_id",
    [
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-fable-5-2",
        "claude-fable-5-1",
        "claude-mythos-5-2",
        # Unknown or newer Claude ids take the safe side: at worst a correction round.
        "claude-opus-6",
        "claude-opus-5-5-20261201",
        "anthropic/claude-sonnet-5-5",
    ],
)
@pytest.mark.parametrize("tools", [ONE_TOOL, SEVERAL_TOOLS], ids=["one-tool", "several-tools"])
def test_current_claude_models_get_no_forced_choice_or_temperature(model_id, tools):
    request = _request(model_id, tools)

    assert "tool_choice" not in request
    assert "temperature" not in request
    assert [tool["name"] for tool in request["tools"]] == [
        tool["function"]["name"] for tool in tools
    ]


@pytest.mark.parametrize(
    "model_id",
    ["claude-sonnet-4-6", "claude-opus-4-6", "claude-haiku-4-5-20251001", "minimax-m3"],
)
def test_models_that_accept_forcing_and_temperature_keep_both(model_id):
    assert _request(model_id, ONE_TOOL)["tool_choice"] == {
        "type": "tool",
        "name": "update_campaign_file",
    }
    several = _request(model_id, SEVERAL_TOOLS)
    assert several["tool_choice"] == {"type": "any"}
    assert several["temperature"] == 0


@pytest.mark.parametrize("model_id", ["claude-opus-5", "claude-sonnet-5", "claude-haiku-5-5"])
def test_models_that_accept_forcing_but_not_temperature(model_id):
    request = _request(model_id, SEVERAL_TOOLS)

    assert request["tool_choice"] == {"type": "any"}
    assert "temperature" not in request


@pytest.fixture
def anthropic_probe(monkeypatch):
    """Answer the setup probe like the Messages API, without network or credentials."""
    original = httpx.Client
    payloads = []

    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "claude-opus-5-5"}]})
        payload = json.loads(request.content)
        payloads.append(payload)
        forced = payload.get("tool_choice", {}).get("type") in {"any", "tool"}
        if forced and payload["model"] == "claude-opus-5-5":
            return httpx.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error",
                "message": 'tool_choice: type "tool" and "any" are not supported for this model.',
            }})
        events = [
            {"type": "message_start", "message": {"id": "msg_synthetic"}},
            {"type": "content_block_start", "index": 0,
             "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {
                "type": "tool_use", "id": "toolu_synthetic",
                "name": "sylvia_compatibility_probe", "input": {},
            }},
            {"type": "message_stop"},
        ]
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(event) + "\n\n" for event in events),
        )

    monkeypatch.setattr(
        settings.httpx, "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    return payloads


def _probe(tmp_path, model_id):
    return settings.validate_provider_model(
        tmp_path / "config.yaml", provider_id="anthropic", preset_id="anthropic",
        base_url="", credential="synthetic-secret", model_id=model_id,
        transport="anthropic_messages",
    )


def test_setup_probe_passes_on_claude_models_that_reject_forced_tool_choice(
    tmp_path, anthropic_probe,
):
    report = _probe(tmp_path, "claude-opus-5-5")

    assert report["transport"] == "passed"
    assert report["tool_calling"] == "passed"
    assert report["agent_ready"] is True
    (payload,) = anthropic_probe
    assert "tool_choice" not in payload
    assert "temperature" not in payload
    assert payload["max_tokens"] == 1024
    assert payload["messages"] == [
        {"role": "user", "content": "Call sylvia_compatibility_probe with an empty object."}
    ]


def test_setup_probe_still_forces_the_call_where_the_model_accepts_it(tmp_path, anthropic_probe):
    assert _probe(tmp_path, "claude-sonnet-4-6")["agent_ready"] is True
    (payload,) = anthropic_probe
    assert payload["tool_choice"] == {"type": "tool", "name": "sylvia_compatibility_probe"}
