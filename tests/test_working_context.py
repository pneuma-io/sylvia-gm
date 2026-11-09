"""Working-copy behavior across compaction, restoration and provider failures."""

import asyncio
import json

import pytest

from sylvia_gm.agent._builder_session import _builder_native_request
from sylvia_gm.agent._working_context import (
    WorkingContextProvider,
    durable_tool_result,
)
from sylvia_gm.provider._base import FakeProvider, ModelInfo, ProviderResponse, ProviderStreamChunk
from sylvia_gm.provider._exceptions import (
    ContextCompactionError,
    InvalidResponseError,
    TokenLimitError,
)


class Model(FakeProvider):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.model_info = ModelInfo(
            "fake/model",
            "Test",
            "Test",
            "fake",
            "local",
            context_window=128_000,
            max_output_tokens=4096,
        )


def pair(index, *, content="result", name="read_file"):
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"tool-{index}",
                    "type": "function",
                    "function": {"name": name, "arguments": '{"path":"rules.md"}'},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": f"tool-{index}",
            "content": json.dumps({"content": content, "path": "rules.md"}),
        },
    ]


def history():
    return [
        {"role": "system", "content": "FRESH STATE: authoritative AP=4"},
        {"role": "user", "content": "Accepted: create a companion named Ida"},
        *pair(1, content="Original source version, before edit"),
        {"role": "assistant", "content": "I have read it"},
        {"role": "user", "content": "Correction: name her Ada"},
        *pair(2, name="write_file"),
        {"role": "assistant", "content": "Draft saved"},
        {"role": "user", "content": "Continue without publishing"},
    ]


def working(model=None, records=(), cancelled=lambda: False):
    saved, progress = [], []

    async def save(record):
        saved.append(record)

    wrapper = WorkingContextProvider(
        model or Model(responses=[ProviderResponse(content="Create Ada. Do not publish.")] * 30),
        records=records,
        save=save,
        cancelled=cancelled,
        progress=progress.append,
    )
    return wrapper, saved, progress


@pytest.mark.asyncio
async def test_complete_oversized_note_is_shortened_once_without_replaying_actions():
    oversized = "A" * 8735
    model = Model(
        responses=[
            ProviderResponse(content=oversized),
            ProviderResponse(content="Create Ada. Do not publish."),
        ]
    )
    model.model_info.context_window = 262144
    wrapper, saved, _ = working(model)
    original = history()
    before = json.dumps(original)
    await wrapper.prepare(original, force=True)
    assert saved[0]["summary"] == "Create Ada. Do not publish."
    assert json.dumps(original) == before
    assert len(model.calls) == 2
    assert all(call["tools"] is None for call in model.calls)
    assert oversized in json.dumps(model.calls[1])
    assert "12000 characters" not in json.dumps(model.calls[0])


@pytest.mark.asyncio
async def test_large_context_summarizes_history_in_a_few_bounded_requests():
    model = Model(responses=[ProviderResponse(content="Create Ada. Do not publish.")] * 30)
    model.model_info.context_window = 262144
    wrapper, saved, _ = working(model)
    original = history()
    original[3]["content"] = json.dumps({"path": "large.json", "content": "data" * 75000})
    before = json.dumps(original)
    await wrapper.prepare(original, force=True)
    assert 2 <= len(model.calls) <= 4
    assert all(len(json.dumps(call["messages"]).encode()) < 262144 for call in model.calls)
    assert json.dumps(original) == before
    assert "data" * 75000 in saved[0]["source_archive"]


@pytest.mark.asyncio
@pytest.mark.parametrize("size, expected_calls", [(8735, 2), (140000, 1)])
async def test_oversized_note_recovery_keeps_previous_checkpoint(size, expected_calls):
    model = Model(
        responses=[ProviderResponse(content="Keep Ada.")]
        + [ProviderResponse(content="A" * size)] * 2
    )
    model.model_info.context_window = 262144
    wrapper, saved, progress = working(model)
    original = history()
    await wrapper.prepare(original, force=True)
    before = wrapper.project(original)
    calls_before = len(model.calls)
    extended = [
        *original,
        {"role": "assistant", "content": "Still pending"},
        {"role": "user", "content": "Continue"},
    ]
    with pytest.raises(ContextCompactionError, match="previous checkpoint were kept"):
        await wrapper.prepare(extended, force=True)
    assert len(model.calls) - calls_before == expected_calls
    assert len(saved) == len(wrapper.records) == 1
    assert wrapper.project(original) == before
    assert progress[-1] is None


@pytest.mark.asyncio
async def test_compaction_keeps_intent_and_pairs_then_restores_from_saved_receipts():
    wrapper, saved, progress = working()
    original = history()
    before = json.dumps(original)
    projected = await wrapper.prepare(original, force=True)
    assert json.dumps(original) == before
    assert len(projected) < len(original)
    assert projected[0] == original[0]
    assert original[1] in projected and original[-1] in projected
    assert saved and progress[0].startswith("Compacting") and progress[-1] is None
    assert (
        "Original source version"
        in wrapper.read_history(
            {
                "source_id": saved[0]["checkpoint_id"],
                "limit": 16000,
            }
        )["content"]
    )
    restored = history()
    restored[3]["content"] = json.dumps(
        durable_tool_result("read_file", json.loads(restored[3]["content"])), indent=2
    )
    reopened, _, _ = working(records=saved)
    assert reopened.project(restored) == projected
    restored[1]["content"] = "Different conversation"
    assert reopened.project(restored) == restored


@pytest.mark.asyncio
async def test_repeated_checkpoints_retain_decisions_and_exact_old_source_retrieval():
    wrapper, saved, _ = working()
    messages = history()
    await wrapper.prepare(messages, force=True)
    for index in range(10):
        messages.extend(pair(index + 10, content=f"new reference {index}"))
    await wrapper.prepare(messages, force=True)
    assert len(saved) == 2
    assert saved[1]["previous_checkpoint_id"] == saved[0]["checkpoint_id"]
    assert len(saved[1]["covered_keys"]) > len(saved[0]["covered_keys"])
    assert wrapper.read_history({"source_id": "list"})["sources"][1:] == [
        record["checkpoint_id"] for record in saved
    ]
    assert "Ada" in wrapper.project(messages)[1]["content"]
    result = wrapper.read_history({"source_id": saved[0]["checkpoint_id"], "limit": 17})
    assert result["next_offset"] == 17 and len(result["content"]) == 17


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider", "truncated", "cancelled", "write"])
async def test_failed_compaction_does_not_replace_working_context_or_replay_tools(failure):
    model = Model(responses=[ProviderResponse(content="partial", finish_reason="length")])
    wrapper, saved, progress = working(model)
    if failure == "provider":

        async def fail(*_args, **_kwargs):
            raise InvalidResponseError("offline")

        model.generate = fail
    elif failure == "cancelled":
        wrapper._cancelled = lambda: True
    elif failure == "write":
        model._responses = [ProviderResponse(content="note")]

        async def fail(_record):
            raise OSError("disk full")

        wrapper._save = fail
    messages = history()
    with pytest.raises((InvalidResponseError, asyncio.CancelledError, OSError)):
        await wrapper.prepare(messages, force=True)
    assert not saved and not wrapper.records
    assert wrapper.project(messages) == messages
    assert not progress or progress[-1] is None
    assert all(call["tools"] is None for call in model.calls)


@pytest.mark.asyncio
async def test_private_reasoning_never_enters_summary_or_saved_archive():
    messages = history()
    messages[2]["reasoning_content"] = "SECRET PRIVATE REASONING"
    messages[2]["anthropic_content"] = [{"type": "thinking", "thinking": "NATIVE SECRET"}]
    wrapper, saved, _ = working()
    await wrapper.prepare(messages, force=True)
    assert "SECRET" not in json.dumps(saved)
    assert "SECRET" not in json.dumps(wrapper._provider.calls)


@pytest.mark.asyncio
async def test_fresh_application_data_does_not_displace_the_latest_player_correction():
    messages = history()
    latest_player = messages[-1]
    for index in range(30, 50):
        messages.extend(pair(index))
    fresh = {"role": "user", "content": "CURRENT_ENGINE_REVISION=42",
             "_application_data": True, "_fresh_application_data": True}
    messages.append(fresh)
    wrapper, _, _ = working()
    projected = await wrapper.prepare(messages, force=True)
    assert latest_player in projected
    assert projected[-1] == fresh


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_native_replay_after_restored_compaction_reads_only_retained_responses(streaming):
    messages = history()
    messages[2]["provider_response_id"] = "compacted-response"
    messages[2]["native_continuation"] = {
        "provider_id": "test", "model_id": "test", "reasoning_required": True,
    }
    wrapper, saved, _ = working()
    await wrapper.prepare(messages, force=True)
    assert "compacted-response" not in json.dumps(wrapper.project(messages))
    assert "provider_response_id" not in json.dumps(saved)
    assert "native_continuation" not in json.dumps(saved)
    assert "compacted-response" not in json.dumps(wrapper._provider.calls)

    retained = pair(99)
    retained[0]["provider_response_id"] = "retained-response"
    messages.extend(retained)
    before = json.dumps(messages)
    reads = []

    def read(response):
        reads.append(response)
        assert response == "retained-response", "Compacted-away private data must not be read"
        return {"reasoning_content": "SYNTHETIC PRIVATE CONTINUATION"}

    async def save(_record):
        pytest.fail("Restored checkpoint should serve this request without another summary")

    model = Model(responses=[ProviderResponse(content="Continued.")])
    reopened = WorkingContextProvider(
        model, records=saved, save=save,
        prepare_continuation=lambda selected, has_tools: _builder_native_request(
            selected, transport="chat_completions", read=read, resend_reasoning=has_tools,
            provider_identity=("test", "test"),
        ),
    )
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {}}}]
    if streaming:
        async for _chunk in reopened.generate_stream(messages, tools=tools):
            pass
    else:
        await reopened.generate(messages, tools=tools)
    assert reads == ["retained-response"]
    request = model.calls[-1]["messages"]
    assistant = next(
        message for message in request
        if any(call["id"] == "tool-99" for call in message.get("tool_calls", []))
    )
    assert assistant["reasoning_content"] == "SYNTHETIC PRIVATE CONTINUATION"
    assert "provider_response_id" not in json.dumps(request)
    assert "native_continuation" not in json.dumps(request)
    assert "PRIVATE CONTINUATION" not in json.dumps(saved)
    assert json.dumps(messages) == before


@pytest.mark.asyncio
async def test_overflow_before_output_compacts_without_executing_or_repeating_actions():
    class Overflow(Model):
        requests = 0

        async def generate_stream(self, messages, **kwargs):
            self.requests += 1
            if self.requests == 1:
                raise TokenLimitError("runtime context exceeded")
            yield ProviderStreamChunk(content="continue")

    model = Overflow(responses=[ProviderResponse(content="write_file already completed")])
    wrapper, saved, _ = working(model)
    chunks = [chunk async for chunk in wrapper.generate_stream(history())]
    assert chunks[0].content == "continue"
    assert model.requests == 2 and saved


@pytest.mark.asyncio
async def test_overflow_after_visible_output_is_not_automatically_replayed():
    class Partial(Model):
        async def generate_stream(self, messages, **kwargs):
            yield ProviderStreamChunk(content="partial response")
            raise TokenLimitError("late overflow")

    wrapper, saved, _ = working(Partial())
    with pytest.raises(TokenLimitError):
        _ = [chunk async for chunk in wrapper.generate_stream(history())]
    assert not saved


@pytest.mark.asyncio
async def test_smaller_model_and_oversized_single_read_use_bounded_summary_requests():
    model = Model(responses=[ProviderResponse(content="Source read; continue Ada.")] * 100)
    wrapper, saved, _ = working(model)
    messages = history()
    await wrapper.prepare(messages, force=True)
    model.model_info.context_window = 32_000
    messages.extend(pair(40, content="long document " * 14000))
    messages.extend(pair(41, content="latest"))
    await wrapper.prepare(messages)
    assert len(saved) == 2
    assert len(model.calls) > 2
    assert max(len(json.dumps(call["messages"]).encode()) for call in model.calls) < 24_000


def test_history_search_keeps_original_unicode_offsets():
    wrapper, _, _ = working()
    wrapper._source = [{"role": "user", "content": "Straße. Exact target follows."}]
    result = wrapper.read_history({"query": "Exact target", "limit": 12})
    assert result["content"] == "Exact target"


@pytest.mark.asyncio
async def test_reply_exhaustion_does_not_compact_or_execute_incomplete_tools():
    from sylvia_gm.provider._exceptions import ProviderOutputLimitError

    provider = Model(
        stream_chunks=[
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "partial",
                    "function": {
                        "name": "write_file",
                        "arguments": '{"path":',
                    },
                },
                finish_reason="length",
            )
        ]
    )
    wrapper, saved, _ = working(provider)
    with pytest.raises(ProviderOutputLimitError):
        _ = [chunk async for chunk in wrapper.generate_stream(history())]
    assert not saved and len(provider.calls) == 1


def test_malformed_or_newer_checkpoints_rebuild_from_original_history():
    wrapper, _, _ = working(
        records=[
            {"kind": "working_context_checkpoint", "version": 1, "covered_keys": 7},
            {"kind": "working_context_checkpoint", "version": 999},
        ]
    )
    assert not wrapper.records
    assert wrapper.project(history()) == history()
