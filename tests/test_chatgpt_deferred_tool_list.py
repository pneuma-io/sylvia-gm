"""The ChatGPT route (``codex_app_server``) ships the deferred GM tool list: Sylvia's loader.

Codex fixes a runtime thread's tools at ``thread/start`` and loads every tool in Sylvia's
direct-only ``sylvia`` namespace whatever ``deferLoading`` says (#205), so a growing list cost
a fresh runtime thread and its cache. The loader declares a small constant list (the
application's tools, the argument reader and ``call_mechanic``), so one runtime thread serves
a whole session: exploration, a fight with a cast, an owner restart and a second fight.

No Codex process, sign-in, model or network: the scripted GM and the in-memory runtime.
"""
from __future__ import annotations

import copy
import json
import shutil

import pytest

from sylvia_gm.agent._context_ledger import (
    DEFERRED_TOOL_LIST,
    LOADER_DEFERRAL,
    deferred_tool_list,
    tool_list_setting,
)
from sylvia_gm.application.authority import AuthorityOwner
from tests.test_gm_context_contract import settle
from tests.test_srd_public_spell_casting import SRD

ROUTE = {"transport": "codex_app_server", "provider_key": "chatgpt_subscription"}


def test_the_chatgpt_route_ships_the_loader():
    assert tool_list_setting({}, **ROUTE) == DEFERRED_TOOL_LIST
    for environ in ({}, {"SYLVIA_GM_TOOL_LIST": "deferred"}, {"SYLVIA_GM_TOOL_LIST": "other"}):
        for model in ("gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-sol", "gpt-5.5", None):
            assert deferred_tool_list(environ, model_id=model, **ROUTE) == LOADER_DEFERRAL
    # Every explicit setting still decides, in either direction.
    for setting in ("stable", "fixed", "rounds"):
        assert deferred_tool_list({"SYLVIA_GM_TOOL_LIST": setting}, **ROUTE) is None
        assert tool_list_setting({"SYLVIA_GM_TOOL_LIST": setting}, **ROUTE) == setting


def _chatgpt_gm():
    """The shared scripted loader GM, presented as the ChatGPT subscription route."""
    from sylvia_gm.provider._base import ModelInfo
    from tests.test_gm_deferred_tool_list import _route_gm

    gm = _route_gm("chat")
    gm.model_info = ModelInfo(
        id="chatgpt_subscription/gpt-6.1-sol", name="Scripted", provider_name="ChatGPT",
        provider_key="chatgpt_subscription", provider_mode="subscription",
        transport="codex_app_server", context_window=1_000_000, max_output_tokens=8192)
    gm.sent = []
    scripted = type(gm).generate_stream

    async def generate_stream(self, messages, tools=None, **kwargs):
        record = {"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools),
                  "response": None}
        self.sent.append(record)
        async for chunk in scripted(self, messages, tools, **kwargs):
            if chunk.tool_call_delta is not None:
                record["response"] = copy.deepcopy(chunk.tool_call_delta)
            yield chunk

    gm.generate_stream = generate_stream.__get__(gm)
    return gm


def test_chatgpt_session_keeps_one_small_list_and_one_runtime_thread(chatgpt_seed, tmp_path,
                                                                     monkeypatch):
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter, _plain_json
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest

    monkeypatch.setenv("SYLVIA_GM_CONTEXT_MODE", "ledger")
    monkeypatch.delenv("SYLVIA_GM_TOOL_LIST", raising=False)  # this route's default
    database = tmp_path / "session.db"
    shutil.copy2(chatgpt_seed[0], database)
    gm = _chatgpt_gm()
    owner = AuthorityOwner.open(database, "chatgpt-owner", disconnect_grace_seconds=1.0)
    refusals: list[str] = []
    committed: list[str] = []
    record = AuthorityOwner.record_admin_full_audit

    def audit(self, event):
        if event.get("kind") == "gm_fixed_tool_refusal":
            refusals.append(event["reason"])
        return record(self, event)
    monkeypatch.setattr(AuthorityOwner, "record_admin_full_audit", audit)

    def turn(text, steps):
        gm.steps.extend(steps)
        identity = f"turn-{len(gm.calls)}"
        result = CampaignSessionLoop(owner).run(SessionRunRequest(
            session_id="chatgpt-session", turn_id="casting-turn", attempt_id=identity,
            message_id=identity, user_text=text,
        ), ProviderSessionAdapter(owner, gm, owner_id="chatgpt-owner").for_turn("casting-turn"))
        assert result.interruption is None, (result.interruption, gm.failure)
        assert gm.position == len(gm.steps), (gm.position, gm.steps)
        for message in owner.session_context("chatgpt-session").messages:
            evidence = message.content
            if (evidence.get("kind") == "provider_tool_results"
                    and evidence.get("basis_message_id") == identity):
                for row in evidence["results"]:
                    if json.loads(row["content"]).get("status") == "action_committed":
                        committed.append(row["name"].split("_", 2)[-1])

    try:
        turn("We look around the clearing.", [settle("The clearing is quiet.")])
        scene = _plain_json(owner.snapshot("casting").state[f"{SRD}.state"]["spatial_scene"])
        scene["zones"] = [{"id": "open_clearing", "map_id": "fixture",
                           "shape": {"kind": "box", "min": [-100, -100, -10],
                                     "max": [100, 100, 20]},
                           "metadata": {"lighting": "bright", "plane": "material"}}]
        summon = dict(chatgpt_seed[1])
        summon.pop("target")
        summon.update(summon_id="dragon_one", summon_distance=30,
                      caster_can_see_summon_space=True, summon_space_unoccupied=True,
                      selected_damage_type="fire", created_actor_position=[0, 30, 0],
                      created_actor_height=6, created_actor_lighting="bright")

        def completion():
            pending = owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_action_stack"]
            return {"pending_action_id": pending[-1]}
        # Each first call of a large mechanic is refused with its definition and repeated.
        turn("Begin a practice fight, summon a dragon, then stop the fight.", [
            ("start_group_combat_encounter",
             {"combatants": ["hero", "guard", "other"], "combat_scenes": [scene]}),
            ("begin_srd_spell_action", summon),
            ("complete_srd_pending_action", completion),
            ("end_combat_encounter", {"end_reason": "fighting_stopped"}),
            settle("The summoned dragon remains as the practice fight ends."),
        ])
        owner.close()
        owner = AuthorityOwner.open(database, "chatgpt-owner", disconnect_grace_seconds=1.0)
        turn("Look around again.", [settle("The clearing is quiet again.")])
        turn("Begin another practice fight, then stop it.", [
            ("start_group_combat_encounter",
             {"combatants": ["hero", "guard", "other", "dragon_one"]}),
            ("end_combat_encounter", {"end_reason": "fighting_stopped",
                                      "known_spell_effect": False}),
            settle("The second practice fight ends."),
        ])
    finally:
        owner.close()
    assert gm.failure is None
    assert committed.count("start_group_combat_encounter") == 2
    assert "begin_srd_spell_action" in committed and "complete_srd_pending_action" in committed
    assert "arguments_unread" in refusals
    # One small declared list, byte for byte, for the whole session and across the restart.
    declared = [json.dumps(row["tools"], ensure_ascii=False) for row in gm.sent]
    assert len(set(declared)) == 1
    assert len(gm.sent[0]["tools"]) < 16
    for previous, current in zip(gm.sent, gm.sent[1:], strict=False):
        assert current["messages"][:len(previous["messages"])] == previous["messages"]
    # Through the ChatGPT provider: one thread/start, every later request continues it.
    import asyncio
    assert asyncio.run(_replay_threads(gm.sent, tmp_path / "runtime", monkeypatch)) == 1


async def _replay_threads(sent, home, monkeypatch) -> int:
    """Replay every request through ChatGPT's stand-in waiting runtime; count its threads."""
    from sylvia_gm.provider import _chatgpt
    from tests.test_chatgpt_answer_in_place import AnswerRuntime, Waiting
    from tests.test_chatgpt_stable_thread import provider_for

    pending: list[list[dict]] = []

    class Next(Waiting):
        def __init__(self) -> None:
            super().__init__([])

        @property
        def events(self):  # type: ignore[override]
            return pending.pop(0)

        @events.setter
        def events(self, value) -> None:
            pass

        @property
        def after_answer(self):  # type: ignore[override]
            class Answers:
                def pop(self, _index: int):
                    return pending.pop(0)
            return Answers()

        @after_answer.setter
        def after_answer(self, value) -> None:
            pass

    class Turns(list):
        def pop(self, _index: int = -1):
            return Next()

    runtime = AnswerRuntime()
    runtime.turns = Turns()
    kept = _chatgpt._KeptThreads()
    monkeypatch.setattr(_chatgpt, "_KEPT_THREADS", kept)
    monkeypatch.setattr(_chatgpt, "_ledger_request", lambda: True)
    monkeypatch.setattr(_chatgpt, "_USAGE_SETTLE_SECONDS", 0.01)
    monkeypatch.setenv("SYLVIA_CHATGPT_THREAD", "answer")
    provider, _audit = provider_for(runtime, home)
    try:
        for index, row in enumerate(sent):
            response = row["response"]
            assert response is not None, "every scripted response here is a tool call"
            pending.append([{"id": index + 1, "method": "item/tool/call", "params": {
                "namespace": "sylvia", "tool": response["function"]["name"],
                "callId": response["id"],
                "arguments": json.loads(response["function"]["arguments"])}}])
            reply = await provider.generate(row["messages"], tools=row["tools"])
            assert [tool.id for tool in reply.tool_calls] == [response["id"]]
        return runtime.threads
    finally:
        await provider.close()
        kept.close_all()


@pytest.fixture(scope="module")
def chatgpt_seed(tmp_path_factory):
    """The SRD spell-casting campaign with a summon, saved once for the module."""
    from sylvia_gm.domain.authority import WriterRole
    from tests.test_srd_public_spell_casting import _create_spell_campaign

    database = tmp_path_factory.mktemp("chatgpt-seed") / "seed.db"
    with AuthorityOwner.open(database, "chatgpt-seed") as owner:
        _state, _data, parameters = _create_spell_campaign(owner, "summon_dragon")
        owner.create_agent_session(campaign_id="casting", session_id="chatgpt-session",
                                   role=WriterRole.DM, provider_id="chatgpt_subscription",
                                   model_id="chatgpt_subscription/gpt-6.1-sol")
    return database, parameters
