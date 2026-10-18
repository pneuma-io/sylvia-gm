"""Documented compatible reasoning fields pass through the real SDK into the private channel."""

import json
from types import SimpleNamespace

import httpx
import pytest

from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._exceptions import InvalidResponseError
from sylvia_gm.provider._openai_compat import ChatCompletionsTransport, OpenAICompatProvider


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["reasoning", "reasoning_content"])
@pytest.mark.parametrize("stream", [False, True])
async def test_sdk_preserves_reasoning_only_chunks_and_keeps_answer_separate(field, stream):
    def respond(request):
        assert json.loads(request.content)["stream"] is stream
        base = {"id": "synthetic", "created": 0, "model": "synthetic"}
        if not stream:
            return httpx.Response(200, json={
                **base, "object": "chat.completion", "choices": [{
                    "index": 0, "finish_reason": "stop", "message": {
                        "role": "assistant", "content": "The door opens.",
                        field: "Synthetic private thought.",
                    },
                }],
            })
        chunks = [{
            **base, "object": "chat.completion.chunk", "choices": [{
                "index": 0, "delta": delta, "finish_reason": finish,
            }],
        } for delta, finish in [
            ({field: "Synthetic private "}, None),
            ({field: "thought."}, None),
            ({"content": "The door opens."}, None), ({}, "stop"),
        ]]
        data = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              text=data + "data: [DONE]\n\n")

    provider = OpenAICompatProvider(
        base_url="https://synthetic.invalid/v1", api_key="synthetic",
        model_info=ModelInfo(id="synthetic", name="Synthetic", provider_name="Synthetic",
                            provider_key="custom", provider_mode="byok"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    try:
        messages = [{"role": "user", "content": "Open the door."}]
        if stream:
            chunks = [chunk async for chunk in provider.generate_stream(messages)]
            assert "".join(chunk.content for chunk in chunks) == "The door opens."
            assert "".join(chunk.reasoning_content for chunk in chunks) == (
                "Synthetic private thought."
            )
        else:
            response = await provider.generate(messages)
            assert response.content == "The door opens."
            assert response.reasoning_content == "Synthetic private thought."
    finally:
        await provider.close()


def test_duplicate_compatible_alias_is_not_replayed_twice():
    chunk = ChatCompletionsTransport().parse_stream_chunk(SimpleNamespace(choices=[
        SimpleNamespace(delta=SimpleNamespace(
            reasoning="Synthetic thought.", reasoning_content="Synthetic thought.",
            content="Visible.", tool_calls=None,
        ), finish_reason=None),
    ]))
    assert chunk is not None
    assert chunk.reasoning_content == "Synthetic thought."
    assert chunk.content == "Visible."


@pytest.mark.parametrize("field", ["reasoning", "reasoning_content"])
def test_unsupported_reasoning_shape_fails_without_echoing_payload(field):
    with pytest.raises(InvalidResponseError) as failure:
        ChatCompletionsTransport().parse_stream_chunk(SimpleNamespace(choices=[
            SimpleNamespace(delta=SimpleNamespace(**{field: {"private": "NEVER_ECHO"}}),
                            finish_reason=None),
        ]))
    assert "NEVER_ECHO" not in str(failure.value)
