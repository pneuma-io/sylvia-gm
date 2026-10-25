"""Selected-source discovery reports every bounded page without reading source bodies."""

import json

import pytest

from sylvia_gm.agent._builder_session import CampaignPlanToolAuthority, _campaign_source_tool
from sylvia_gm.agent._mode import AgentMode
from sylvia_gm.agent._runner import AgentRunner
from sylvia_gm.domain.campaign_plan import PackageBinding
from sylvia_gm.provider._base import ProviderResponse, ToolCall
from tests.test_builder_tool_budget import AuthoringProvider


def source_tool(count=520):
    reads = []
    authority = CampaignPlanToolAuthority(
        ruleset=PackageBinding("example.rules", "1.0.0", "1" * 64),
        setting=PackageBinding("example.world", "1.0.0", "2" * 64),
        closure_sha256="3" * 64,
        setting_paths=frozenset(f"lore/place-{i:04d}.md" for i in range(count)),
        read_setting=lambda path: reads.append(path) or "# Selected source",
    )
    return _campaign_source_tool(authority), reads


def test_source_listing_recovers_every_path_after_the_old_512_entry_cutoff():
    tool, reads = source_tool()
    first = tool.handler({"action": "list"}, None)
    assert first["success"] is True
    assert first["total"] == 523
    assert len(first["paths"]) == 512
    assert first["next_offset"] == 512
    second = tool.handler({"action": "list", "offset": first["next_offset"]}, None)
    assert second["total"] == first["total"]
    assert second["next_offset"] is None
    paths = [*first["paths"], *second["paths"]]
    assert paths == [
        "campaign-plan/example/plan.json", "campaign-plan/example/plan.md",
        "ruleset/identity.json", *(f"setting/lore/place-{i:04d}.md" for i in range(520)),
    ]
    assert reads == []


def test_filtered_listing_restarts_at_zero_and_continues_within_the_same_filter():
    tool, reads = source_tool()
    unfiltered = tool.handler({"action": "list"}, None)
    stale_filter_page = tool.handler(
        {"action": "list", "query": "place-051", "offset": unfiltered["next_offset"]}, None,
    )
    assert stale_filter_page["success"] is False
    assert stale_filter_page["code"] == "source_page_invalid"
    paths = []
    offset = 0
    while offset is not None:
        result = tool.handler(
            {"action": "list", "query": "PLACE-051", "offset": offset, "limit": 3}, None,
        )
        assert result["total"] == 10
        assert len(result["paths"]) <= 3
        paths.extend(result["paths"])
        offset = result["next_offset"]
    assert paths == [f"setting/lore/place-{i:04d}.md" for i in range(510, 520)]
    assert reads == []


def test_empty_filter_and_completed_page_are_explicitly_complete():
    tool, _ = source_tool(2)
    missing = tool.handler({"action": "list", "query": "absent"}, None)
    assert missing["paths"] == ()
    assert missing["total"] == 0 and missing["next_offset"] is None
    end = tool.handler({"action": "list", "offset": 5}, None)
    assert end["paths"] == ()
    assert end["total"] == 5 and end["next_offset"] is None


@pytest.mark.parametrize("page", [
    {"offset": -1}, {"offset": True}, {"offset": "0"}, {"offset": 9999},
    {"limit": 0}, {"limit": -1}, {"limit": True}, {"limit": "10"}, {"limit": 513},
])
def test_invalid_source_page_cannot_read_a_source_or_claim_an_empty_complete_catalog(page):
    tool, reads = source_tool()
    result = tool.handler({"action": "list", **page}, None)
    assert result == {"success": False, "status": "rejected", "code": "source_page_invalid"}
    assert reads == []


@pytest.mark.parametrize("page", [{"offset": 0}, {"limit": 5}])
def test_list_pagination_cannot_be_mistaken_for_a_partial_source_read(page):
    tool, reads = source_tool(1)
    result = tool.handler({"action": "read", "path": "setting/lore/place-0000.md", **page}, None)
    assert result["success"] is False
    assert result["code"] == "source_path_invalid"
    assert reads == []


def test_listing_continuation_keeps_the_original_readable_source_path():
    tool, reads = source_tool()
    page = tool.handler({"action": "list", "offset": 512, "limit": 1}, None)
    assert page["paths"] == ("setting/lore/place-0509.md",)
    read = tool.handler({"action": "read", "path": page["paths"][0]}, None)
    assert read == {
        "success": True, "status": "available", "path": page["paths"][0],
        "content": "# Selected source",
    }
    assert reads == ["lore/place-0509.md"]


@pytest.mark.asyncio
async def test_general_builder_receives_catalog_continuation_before_reading_the_selected_source(
    tmp_path,
):
    tool, reads = source_tool()
    provider = AuthoringProvider(responses=[
        ProviderResponse(tool_calls=[ToolCall(
            id="first-page", name=tool.name, arguments='{"action":"list"}',
        )]),
        ProviderResponse(tool_calls=[ToolCall(
            id="next-page", name=tool.name, arguments='{"action":"list","offset":512}',
        )]),
        ProviderResponse(tool_calls=[ToolCall(
            id="read-source", name=tool.name,
            arguments='{"action":"read","path":"setting/lore/place-0519.md"}',
        )]),
        ProviderResponse(content="Read the selected source; ready to draft."),
    ])
    runner = AgentRunner(
        AgentMode.BUILDER_FREEFORM, provider, None, project_root=tmp_path,
        builder_application_tools=(tool,),
    )
    await runner.turn("Find and read the last selected Setting source.")
    receipts = [json.loads(message["content"]) for message in provider.calls[-1]["messages"]
                if message["role"] == "tool"]
    assert receipts[0]["total"] == receipts[1]["total"] == 523
    assert receipts[0]["next_offset"] == 512 and receipts[1]["next_offset"] is None
    assert "setting/lore/place-0519.md" in receipts[1]["paths"]
    assert receipts[2]["content"] == "# Selected source"
    assert reads == ["lore/place-0519.md"]
    assert not list(tmp_path.iterdir())
