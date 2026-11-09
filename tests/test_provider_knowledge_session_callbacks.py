"""Normal and retry provider requests retain search continuation through actual MCP."""

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._session_loop import (
    CampaignSessionLoop,
    SessionProviderInput,
    SessionRunRequest,
)
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import PackageLock, WriterRole
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.provider._base import ProviderStreamChunk
from sylvia_gm.provider._exceptions import ProviderConnectionError
from tests.test_alpha_f4_provider_session_adapter import _ScriptedProvider


def _call(identity: str, name: str, arguments: Mapping[str, object]) -> list[ProviderStreamChunk]:
    return [
        ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": identity,
                "function": {"name": name, "arguments": json.dumps(arguments)},
            },
            finish_reason="tool_calls",
        )
    ]


@pytest.mark.parametrize("retry", [False, True])
def test_session_search_continuation_reaches_second_page_through_mcp(tmp_path: Path, retry: bool):
    with AuthorityOwner.open(
        tmp_path / "pages.db",
        "pages-owner",
        disconnect_grace_seconds=1.0,
    ) as owner:
        owner.create_campaign(
            CampaignCreate(
                campaign_id="pages",
                display_name="Pagination QA",
                writer=WriterRole.DM,
                package_lock=PackageLock(entries=()),
                turn_id="turn",
            )
        )
        owner.create_agent_session(
            campaign_id="pages",
            session_id="gm",
            role=WriterRole.DM,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
        )
        owner.append_agent_message(
            session_id="gm",
            message_id="seed",
            kind=SessionMessageKind.USER,
            content={"text": "Record four temple keepers for reference discovery."},
        )
        owner.update_campaign_file(
            session_id="gm",
            update_id="keepers",
            turn_id="turn",
            basis_message_ids=("seed",),
            updates=tuple(
                {
                    "operation": "record_entity",
                    "entity_id": f"temple-{index}",
                    "entity_kind": "person",
                    "name": f"Temple Keeper {index}",
                    "attributes": {"current_location": "temple"},
                    "visibility": "player_known",
                    "known_by": ["player"],
                }
                for index in range(4)
            ),
        )
        before = owner.snapshot("pages")
        provider = _ScriptedProvider(
            [
                _call(
                    "first",
                    "search_campaign_knowledge",
                    {
                        "query": "temple",
                        "sources": ["campaign_state"],
                        "limit": 2,
                    },
                ),
                _call(
                    "next",
                    "search_campaign_knowledge",
                    {
                        "query": "temple",
                        "sources": ["campaign_state"],
                        "limit": 2,
                        "offset": 2,
                    },
                ),
                _call(
                    "done",
                    "update_campaign_file",
                    {
                        "state_change": "unchanged",
                        "state_change_summary": "Read-only reference discovery.",
                        "revised_player_facing_response": [
                            {
                                "kind": "narration",
                                "text": "The next two temple references are available.",
                            }
                        ],
                    },
                ),
            ]
        )
        adapter = ProviderSessionAdapter(owner, provider, owner_id="pages-owner")
        loop = CampaignSessionLoop(owner)
        request = SessionRunRequest(
            "gm", "turn", "lookup", "lookup-message", "List temple references."
        )
        if retry:

            def fail_before_commit(_input: SessionProviderInput):
                raise ProviderConnectionError("Controlled failure before reference discovery")
                yield

            with pytest.raises(ProviderConnectionError):
                loop.run(request, fail_before_commit)
            interruption = owner.retryable_interruption("pages", failed_attempt_id="lookup")
            assert interruption is not None
            loop.retry(
                "gm",
                retry_id="continue-lookup",
                interruption_id=interruption.interruption_id,
                narrator=lambda *_: pytest.fail(
                    "Reference discovery must use the tool-capable retry"
                ),
                provider_factory=adapter.for_turn,
            )
        else:
            result = loop.run(request, adapter.for_turn("turn"))
            assert result.receipts == ()
        assert len(provider.calls) == 3
        replies = {
            message["tool_call_id"]: json.loads(message["content"])["result"]
            for message in provider.calls[2][0]
            if message["role"] == "tool"
        }
        assert replies["first"]["offset"] == 0
        assert replies["first"]["next_offset"] == 2
        assert replies["next"]["offset"] == 2
        assert replies["next"]["next_offset"] is None
        paths = [match["path"] for page in ("first", "next") for match in replies[page]["matches"]]
        assert len(paths) == len(set(paths)) == 4
        assert set(paths) == {f"temple-{index}" for index in range(4)}
        after = owner.snapshot("pages")
        assert (after.revision, after.state, after.participants, after.continuity) == (
            before.revision,
            before.state,
            before.participants,
            before.continuity,
        )
