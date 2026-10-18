"""Exercise private continuation through the GM loop, durable owner and next request."""

import json
from dataclasses import replace

import pytest

from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.provider._base import ModelInfo, ProviderStreamChunk
from tests.test_alpha_f4_generic_sessions import _campaign
from tests.test_alpha_f4_provider_session_adapter import _ScriptedProvider


def test_gm_local_worker_fault_preserves_internal_error_type(tmp_path):
    """A local serialization/programming fault is not an upstream disconnection."""
    class BrokenProvider(_ScriptedProvider):
        async def generate_stream(self, *args, **kwargs):
            raise TypeError("synthetic local serialization failure")
            yield

    provider = BrokenProvider([])
    with AuthorityOwner.open(
        tmp_path / "local-fault.db", "owner", disconnect_grace_seconds=1.0,
    ) as owner:
        owner.create_builder_chat(provider_id="synthetic", model_id="synthetic/sol",
                                  session_id="schema")
        _campaign(owner, campaign_id="campaign", writer=WriterRole.DM, turn_id="turn")
        owner.create_agent_session(
            campaign_id="campaign", role=WriterRole.DM, provider_id="synthetic",
            model_id="synthetic/sol", session_id="gm",
        )
        with pytest.raises(TypeError, match="synthetic local serialization failure"):
            CampaignSessionLoop(owner).run(SessionRunRequest(
                session_id="gm", turn_id="turn", attempt_id="fault",
                message_id="user", user_text="I look for tracks.",
            ), ProviderSessionAdapter(owner, provider, owner_id="owner").for_turn("turn"))


def test_gm_tool_completed_opening_allows_next_player_request(tmp_path):
    """A saved tool-completed reply must remain serializable on the next GM request."""
    def reply(call_id, text):
        return [ProviderStreamChunk(tool_call_delta={
            "index": 0, "id": call_id, "function": {
                "name": "update_campaign_file", "arguments": json.dumps({
                    "state_change": "unchanged", "state_change_summary": "No durable change.",
                    "revised_player_facing_response": [{"kind": "narration", "text": text}],
                }),
            },
        }, finish_reason="tool_calls")]

    provider = _ScriptedProvider([reply("opening", "The road is quiet."),
                                  reply("next", "You find no tracks.")])
    provider.model_info = ModelInfo(
        id="synthetic/sol", name="Synthetic", provider_name="Synthetic",
        provider_key="synthetic", provider_mode="byok", transport="codex_app_server",
    )
    with AuthorityOwner.open(
        tmp_path / "two-turns.db", "owner", disconnect_grace_seconds=1.0,
    ) as owner:
        owner.create_builder_chat(provider_id="synthetic", model_id="synthetic/sol",
                                  session_id="schema")
        _campaign(owner, campaign_id="campaign", writer=WriterRole.DM, turn_id="turn")
        owner.create_agent_session(
            campaign_id="campaign", role=WriterRole.DM, provider_id="synthetic",
            model_id="synthetic/sol", session_id="gm",
        )
        for index in range(2):
            result = CampaignSessionLoop(owner).run(SessionRunRequest(
                session_id="gm", turn_id="turn", attempt_id=f"attempt-{index}",
                message_id=f"user-{index}", user_text="I look for tracks.",
            ), ProviderSessionAdapter(owner, provider, owner_id="owner").for_turn("turn"))
            assert result.interruption is None
        assert len(provider.calls) == 2


def test_gm_final_reasoning_survives_owner_reopen_and_reaches_next_tool_request(tmp_path):
    database = tmp_path / "gm-native.db"
    private_text = "Synthetic private reasoning retained only for required continuation."
    first = _ScriptedProvider([[
        ProviderStreamChunk(reasoning_content=private_text),
        ProviderStreamChunk(content="The empty road is quiet.", finish_reason="stop"),
    ]])
    model = ModelInfo(
        id="synthetic/deepseek-v4", name="Synthetic", provider_name="Synthetic",
        provider_key="synthetic", provider_mode="byok", provider_model_id="deepseek-v4",
        transport="chat_completions",
    )
    first.model_info = model
    with AuthorityOwner.open(database, "first-owner", disconnect_grace_seconds=1.0) as owner:
        owner.create_builder_chat(provider_id="synthetic", model_id=model.id, session_id="schema")
        _campaign(owner, campaign_id="campaign", writer=WriterRole.DM, turn_id="turn")
        owner.create_agent_session(
            campaign_id="campaign", role=WriterRole.DM, provider_id="synthetic",
            model_id=model.id, session_id="gm",
        )
        adapter = ProviderSessionAdapter(owner, first, owner_id="first-owner").for_turn("turn")

        # A tool-free request may still produce reasoning that a later tool-bearing request needs.
        def tool_free_request(source):
            return adapter(replace(source, catalog=(), catalog_supplier=None))

        result = CampaignSessionLoop(owner).run(SessionRunRequest(
            session_id="gm", turn_id="turn", attempt_id="first-attempt",
            message_id="first-user", user_text="I look at the empty road.",
        ), tool_free_request)
        assert result.interruption is None
        assert private_text not in repr(owner.session_context("gm"))
        assert private_text not in repr(owner.player_projection("campaign"))
        index = owner.private_provider_index("gm")
        assert len(index) == 1
        assert index[0]["status"] == "completed"
        assert owner.inspect_provider_thinking(
            "gm", index[0]["attempt_id"], index[0]["response_id"],
        ) == private_text

    second = _ScriptedProvider([[
        ProviderStreamChunk(tool_call_delta={
            "index": 0, "id": "unchanged-call", "function": {
                "name": "update_campaign_file", "arguments": json.dumps({
                    "state_change": "unchanged",
                    "state_change_summary": "The player listens; no durable fact changes.",
                    "revised_player_facing_response": [{
                        "kind": "narration", "text": "The road remains quiet.",
                    }],
                }),
            },
        }, finish_reason="tool_calls"),
    ]])
    second.model_info = model
    with AuthorityOwner.open(database, "second-owner", disconnect_grace_seconds=1.0) as owner:
        result = CampaignSessionLoop(owner).run(SessionRunRequest(
            session_id="gm", turn_id="turn", attempt_id="second-attempt",
            message_id="second-user", user_text="I listen without moving.",
        ), ProviderSessionAdapter(owner, second, owner_id="second-owner").for_turn("turn"))
        assert result.interruption is None
        messages, tools = second.calls[0]
        assert tools
        prior = next(m for m in messages if m.get("content") == "The empty road is quiet.")
        assert prior["reasoning_content"] == private_text
        assert "_native_reference" not in prior
        assert private_text not in repr(owner.session_context("gm"))
        assert private_text not in repr(owner.player_projection("campaign"))
