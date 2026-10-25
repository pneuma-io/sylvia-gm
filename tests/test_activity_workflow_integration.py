"""Bounded source navigation retains live activity through both Builder entrypoints."""

import json

import pytest

from sylvia_gm.agent._builder_session import run_builder_turn, stream_builder_turn
from sylvia_gm.agent._session_loop import ProviderControl
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_builder_source_navigation import source_tool
from tests.test_builder_tool_budget import AuthoringProvider
from tests.test_context_integrity import _builder_context


@pytest.mark.parametrize("streaming", [False, True])
def test_builder_source_page_recovery_keeps_attempt_progress_and_exact_source(tmp_path, streaming):
    tool, reads = source_tool()
    control = ProviderControl()
    observed = []

    class ObservedProvider(AuthoringProvider):
        async def generate(self, *args, **kwargs):
            observed.append(control.progress.snapshot())
            return await super().generate(*args, **kwargs)

    requests = (
        {"action": "list", "offset": -1},
        {"action": "list"},
        {"action": "list", "offset": 512},
        {"action": "read", "path": "setting/lore/place-0519.md"},
    )
    provider = ObservedProvider(responses=[
        *(ProviderResponse(tool_calls=[ToolCall(
            id=f"source-{index}", name=tool.name, arguments=json.dumps(request),
        )]) for index, request in enumerate(requests)),
        ProviderResponse(content="Read the selected source; ready to draft."),
    ])
    arguments = {
        "context": _builder_context(), "message_id": "input-navigation",
        "user_text": "Read the last source.", "workspace": tmp_path,
        "control": control, "source_tools": (tool,),
    }
    if streaming:
        completed = []
        list(stream_builder_turn(provider, **arguments, complete=completed.append))
        assert len(completed) == 1
        result = completed[0]
    else:
        result = run_builder_turn(provider, **arguments)

    assert len(provider.calls) == len(observed) == 5
    assert observed[0]["phase"] == "provider"
    assert observed[1]["phase"] == "retrying"
    assert all(item["phase"] in {"provider", "retrying"} for item in observed)
    assert observed[-1]["revision"] > observed[0]["revision"]
    assert len(result.tool_calls_made) == 4
    receipts = [json.loads(message["content"]) for message in provider.calls[-1]["messages"]
                if message["role"] == "tool"][-4:]
    assert receipts[0] == {
        "success": False, "status": "rejected", "code": "source_page_invalid",
    }
    assert receipts[1]["total"] == receipts[2]["total"] == 523
    assert receipts[1]["next_offset"] == 512 and receipts[2]["next_offset"] is None
    assert receipts[3]["path"] == requests[-1]["path"]
    assert receipts[3]["content"] == "# Selected source"
    assert reads == ["lore/place-0519.md"]
    assert "place-" not in json.dumps(observed)
    assert "Selected source" not in json.dumps(observed)
    assert not list(tmp_path.iterdir())
