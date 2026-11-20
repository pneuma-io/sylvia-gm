"""The deferred GM tool list on the ChatGPT route (``codex_app_server``): Sylvia's loader.

Codex fixes a thread's tools at ``thread/start``, and every tool in Sylvia's direct-only
``sylvia`` namespace is loaded whatever its ``deferLoading`` says (#205). The loader declares
one list that never changes (the core tools in full, a short stub per Ruleset mechanic) and
appends a mechanic's full definition when the turn loop first offers it. So one runtime thread
serves the whole session, through a fight, a summon, an owner restart and a second fight.

No Codex process, sign-in, model or network: the scripted GM and the in-memory runtime.
"""
from __future__ import annotations

import copy
import json
import shutil

from sylvia_gm.agent._context_ledger import (
    DEFINITIONS_LABEL,
    LOADER_DEFERRAL,
    LOADER_STUB_NOTE,
    deferred_tool_list,
)
from sylvia_gm.application.authority import AuthorityOwner
from tests.test_gm_context_contract import settle
from tests.test_gm_fixed_tool_list import FixedGM, casting_seed, replay_on_chatgpt  # noqa: F401
from tests.test_srd_public_spell_casting import SRD


def test_the_chatgpt_route_defers_with_the_loader():
    route = {"transport": "codex_app_server", "provider_key": "chatgpt_subscription"}
    assert deferred_tool_list({"SYLVIA_GM_TOOL_LIST": "deferred"}, **route) == LOADER_DEFERRAL
    for model in ("gpt-6.1-sol", "gpt-6-sol", "gpt-5.6-sol", "gpt-5.5"):
        assert deferred_tool_list({"SYLVIA_GM_TOOL_LIST": "deferred"}, model_id=model,
                                  **route) == LOADER_DEFERRAL


def _stub(tool: dict) -> bool:
    return tool["function"]["description"].endswith(LOADER_STUB_NOTE)


def test_deferred_chatgpt_session_keeps_one_runtime_thread(casting_seed, tmp_path,  # noqa: F811
                                                           monkeypatch):
    monkeypatch.setenv("SYLVIA_GM_CONTEXT_MODE", "ledger")
    monkeypatch.setenv("SYLVIA_GM_TOOL_LIST", "deferred")
    database = tmp_path / "session.db"
    shutil.copy2(casting_seed[0], database)
    gm = FixedGM()
    counted = []
    original_record = AuthorityOwner.record_admin_full_audit

    def record(owner, event):
        if event.get("kind") == "gm_fixed_tool_refusal":
            counted.append(copy.deepcopy(event))
        return original_record(owner, event)
    monkeypatch.setattr(AuthorityOwner, "record_admin_full_audit", record)
    committed = []
    owner = AuthorityOwner.open(database, "fixed-owner", disconnect_grace_seconds=1.0)

    def turn(text, steps):
        from sylvia_gm.agent._provider_session import ProviderSessionAdapter
        from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
        gm.steps.extend(steps)
        identity = f"turn-{len(gm.calls)}"
        result = CampaignSessionLoop(owner).run(SessionRunRequest(
            session_id="fixed-session", turn_id="casting-turn", attempt_id=identity,
            message_id=identity, user_text=text,
        ), ProviderSessionAdapter(owner, gm, owner_id="fixed-owner").for_turn("casting-turn"))
        assert result.interruption is None, (result.interruption, gm.failure)
        assert gm.position == len(gm.steps), (gm.position, gm.steps)
        for message in owner.session_context("fixed-session").messages:
            evidence = message.content
            if (evidence.get("kind") == "provider_tool_results"
                    and evidence.get("basis_message_id") == identity):
                for result_record in evidence["results"]:
                    outcome = json.loads(result_record["content"])
                    assert outcome.get("status") != "action_failed", outcome
                    if outcome.get("status") == "action_committed":
                        committed.append(result_record["name"])
                    if outcome.get("status") == "rejected":
                        assert outcome.get("fixed_tool_refusal"), outcome

    try:
        # A declared stub the game state has not offered: refused and counted, then corrected.
        turn("We look around the clearing.", [
            ("end_combat_encounter", {"end_reason": "fighting_stopped",
                                      "known_spell_effect": False}),
            settle("The clearing is quiet.")])
        from sylvia_gm.agent._provider_session import _plain_json
        scene = _plain_json(owner.snapshot("casting").state[f"{SRD}.state"]["spatial_scene"])
        scene["zones"] = [{"id": "daylight", "map_id": "fixture",
                           "shape": {"kind": "box", "min": [-100, -100, -10],
                                     "max": [100, 100, 20]},
                           "metadata": {"lighting": "bright", "plane": "material"}}]
        summon = dict(casting_seed[1])
        summon.pop("target")
        summon.update(summon_id="dragon_one", summon_distance=30,
                      caster_can_see_summon_space=True, summon_space_unoccupied=True,
                      selected_damage_type="fire", created_actor_position=[0, 30, 0],
                      created_actor_height=6, created_actor_lighting="bright")

        def completion():
            pending = owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_action_stack"]
            return {"pending_action_id": pending[-1]}
        turn("Begin a practice fight, summon a dragon, then stop the fight.", [
            ("start_group_combat_encounter",
             {"combatants": ["hero", "guard", "other"], "combat_scenes": [scene]}),
            ("begin_srd_spell_action", summon),
            ("complete_srd_pending_action", completion),
            ("end_combat_encounter", {"end_reason": "fighting_stopped"}),
            settle("The summoned dragon remains as the practice fight ends."),
        ])
        owner.close()
        owner = AuthorityOwner.open(database, "fixed-owner", disconnect_grace_seconds=1.0)
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
    assert sum(name.endswith("_start_group_combat_encounter") for name in committed) == 2
    assert sum(name.endswith("_begin_srd_spell_action") for name in committed) == 1
    assert [row["reason"] for row in counted] == ["not_callable"]

    # One declared list for the whole session: the core in full, every mechanic a stub.
    assert all(tools == gm.definitions[0] for tools in gm.definitions)
    declared = gm.definitions[0]
    stubs = [tool for tool in declared if _stub(tool)]
    full = [tool for tool in declared if not _stub(tool)]
    assert full and len(full) < len(stubs) // 4
    assert all(tool["function"]["parameters"] == {"type": "object"} for tool in stubs)
    assert any(tool["function"]["name"].endswith("_begin_srd_spell_action") for tool in stubs)

    # Each offered mechanic's definition is appended once, as a user record the open runtime
    # turn can take (``turn/steer``), never as a change to the thread's tools.
    last = gm.calls[-1]["messages"]
    marker = DEFINITIONS_LABEL + " (application control"
    records = [m for m in last if marker in str(m.get("content", ""))]
    assert records and all(m["role"] == "user" for m in records)
    defined = [name for m in records
               for name in json.loads(m["content"][m["content"].index("\n{") + 1:])]
    assert len(defined) == len(set(defined))
    assert any(name.endswith("_begin_srd_spell_action") for name in defined)
    for previous, current in zip(gm.calls, gm.calls[1:], strict=False):
        assert current["messages"][:len(previous["messages"])] == previous["messages"]

    # Through the ChatGPT provider: one thread/start, every later request continues it.
    import asyncio
    asyncio.run(replay_on_chatgpt(gm, tmp_path / "runtime", monkeypatch))
    assert gm.failure is None
