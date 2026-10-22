"""Explicit reference pages share one bounded response and the existing failure guards."""

import asyncio
import json

import pytest

from sylvia_gm.agent._builder_session import _builder_reference_tool
from sylvia_gm.agent._mode import AgentMode
from sylvia_gm.agent._runner import AgentLoopError, AgentRunner
from sylvia_gm.application.builder_references import BuilderReferences
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_builder_tool_budget import AuthoringProvider


def test_batch_reads_explicit_pages_from_one_snapshot_with_single_read_parity():
    contents = {"ruleset/creation.md": "a" * 18000, "setting/lore.md": "World rules"}
    resolved = []
    reads = []

    def snapshot():
        resolved.append(True)
        return BuilderReferences(tuple(contents), lambda path: reads.append(path) or contents[path])

    pages = [
        {"path": "ruleset/creation.md", "offset": 7000, "limit": 8000},
        {"path": "setting/lore.md", "limit": 100},
    ]
    tool = _builder_reference_tool(snapshot)
    result = tool.handler({"action": "read_many", "pages": pages}, None)
    assert result["success"] is True, result
    assert resolved == [True]
    assert reads == [page["path"] for page in pages]
    assert result["pages"] == [
        tool.handler({"action": "read", **page}, None) for page in pages
    ]
    assert result["pages"][0]["next_offset"] == 15000


@pytest.mark.parametrize("pages", [
    [], None, "all", [{}], [{"path": "../outside"}],
    [{"path": "ruleset/creation.md"}] * 5,
    [{"path": "ruleset/creation.md", "limit": 32001}],
    [{"path": "ruleset/creation.md", "limit": 20000}] * 2,
    [{"path": "ruleset/creation.md", "limit": True}],
    [{"path": "ruleset/creation.md", "offset": -1}],
    [{"path": "ruleset/creation.md", "offset": False}],
    [{"path": "ruleset/creation.md"}, {"path": "outside"}],
    [{"path": "ruleset/creation.md", "action": "find", "query": "x"}],
])
def test_invalid_batch_does_not_read_any_source(pages):
    reads = []
    tool = _builder_reference_tool(BuilderReferences(
        ("ruleset/creation.md",), lambda path: reads.append(path) or "Rules",
    ))
    result = tool.handler({"action": "read_many", "pages": pages}, None)
    assert result["success"] is False
    assert reads == []


def test_batch_output_is_bounded_and_returns_continuation_for_every_page():
    contents = {str(i): "x" * 40000 for i in range(4)}
    tool = _builder_reference_tool(BuilderReferences(tuple(contents), contents.__getitem__))
    result = tool.handler({"action": "read_many", "pages": [
        {"path": path} for path in contents
    ]}, None)
    assert result["success"] is True, result
    assert sum(len(page["content"]) for page in result["pages"]) == 32000
    assert all(page["next_offset"] == 8000 for page in result["pages"])
    assert all(page["total_characters"] == 40000 for page in result["pages"])


def test_batch_stops_at_first_unavailable_source_without_reading_later_pages():
    reads = []

    def read(path):
        reads.append(path)
        if path == "bad":
            raise CommandRejected("Unavailable", code="package_file_oversized")
        return "Creation rules"

    tool = _builder_reference_tool(BuilderReferences(("good", "bad", "later"), read))
    result = tool.handler({"action": "read_many", "pages": [
        {"path": path} for path in ("good", "bad", "later")
    ]}, None)
    assert result["success"] is False
    assert result["code"] == "reference_unavailable"
    assert result["reason"] == "package_file_oversized"
    assert [page["success"] for page in result["pages"]] == [True, False]
    assert reads == ["good", "bad"]


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("structural", [False, True])
async def test_batch_preserves_source_failure_stop_before_later_write(
    tmp_path, streaming, structural,
):
    def read(path):
        if path == "bad":
            raise CommandRejected(
                "Unavailable", code="package_corrupt" if structural else "package_file_unavailable",
            )
        return "Another successfully read reference"

    responses = [ProviderResponse(tool_calls=[ToolCall(
        id=f"read-{i}", name="inspect_builder_references",
        arguments=json.dumps({"action": "read_many", "pages": [
            {"path": "good"}, {"path": "bad"},
        ]}),
    )]) for i in range(3)]
    if structural:
        responses[0].tool_calls.append(ToolCall(
            id="later-write", name="write_file",
            arguments='{"path":"saved.md","content":"Incorrect"}',
        ))
    provider = AuthoringProvider(responses=responses + [ProviderResponse(tool_calls=[ToolCall(
        id="write", name="write_file", arguments='{"path":"saved.md","content":"Incorrect"}',
    )])])
    runner = AgentRunner(
        AgentMode.BUILDER_FREEFORM, provider, None, project_root=tmp_path,
        builder_application_tools=(_builder_reference_tool(
            BuilderReferences(("good", "bad"), read),
        ),),
    )
    with pytest.raises(AgentLoopError) as stopped:
        if streaming:
            await runner.turn_stream("Create the character", asyncio.Queue())
        else:
            await runner.turn("Create the character")
    assert stopped.value.reason == (
        "required_reference_unavailable" if structural else "repeated_reference_failure"
    )
    assert len(provider.calls) == (1 if structural else 3)
    assert not (tmp_path / "saved.md").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_batch_delivers_independent_rules_and_lore_in_one_model_round(tmp_path, streaming):
    contents = {"ruleset/creation.md": "Required creation rules", "setting/lore.md": "World lore"}
    provider = AuthoringProvider(responses=[
        ProviderResponse(tool_calls=[ToolCall(
            id="references", name="inspect_builder_references",
            arguments=json.dumps({"action": "read_many", "pages": [
                {"path": path} for path in contents
            ]}),
        )]),
        ProviderResponse(content="Read the selected sources; ready to author and validate."),
    ])
    runner = AgentRunner(
        AgentMode.BUILDER_FREEFORM, provider, None, project_root=tmp_path,
        builder_application_tools=(_builder_reference_tool(
            BuilderReferences(tuple(contents), contents.__getitem__),
        ),),
    )
    if streaming:
        await runner.turn_stream("Make a character", asyncio.Queue())
    else:
        await runner.turn("Make a character")
    assert len(provider.calls) == 2
    receipts = [json.loads(message["content"]) for message in provider.calls[-1]["messages"]
                if message["role"] == "tool"]
    assert [page["content"] for page in receipts[0]["pages"]] == list(contents.values())
    assert not list(tmp_path.iterdir())
