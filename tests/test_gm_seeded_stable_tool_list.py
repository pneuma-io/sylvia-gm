"""The stable GM tool list declares its state-gated tools from the first request (#205).

One stable tool list grows whenever the turn loop first offers a tool, and on ChatGPT each
growth costs a fresh runtime thread and its cache. Every tool whose availability only Ruleset
campaign state decides (state predicates such as an encounter or a turn phase, or exposure by
entity fields such as an active summon) is declared from the first request, in its
state-independent definition, so that state arriving later adds nothing. Admission is
unchanged: such a tool not offered this round is refused and counted, and its current argument
choices go in the callable record, as with the fixed catalog (PR 140).
"""
from __future__ import annotations

import copy
import json
import shutil

from sylvia_gm.agent._context_ledger import CALLABLE_LABEL, ContextLedger
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics, seed_tool_names
from sylvia_gm.application.authority import AuthorityOwner
from tests.test_gm_context_contract import settle
from tests.test_gm_fixed_tool_list import FixedGM, casting_seed, encoded  # noqa: F401
from tests.test_srd_public_spell_casting import SRD

#: Turn-cycle tools the scripted session's short fight never reaches.
TURN_CYCLE = {
    "monster turn": ("finish_srd_monster_turn", "resolve_adjudicated_creature_attack"),
    "turn start and order": ("begin_combat_turn", "advance_group_combat_encounter_turn"),
    "reaction": ("spend_srd_reaction", "resolve_opportunity_attack"),
    "death save": ("death_saving_throw", "stabilize_character"),
}


def test_state_gated_names_are_exactly_the_predicate_gated_declarations(casting_seed):  # noqa: F811
    owner = AuthorityOwner.open(casting_seed[0], "gated-owner", disconnect_grace_seconds=1.0)
    try:
        names = owner.state_gated_tool_names("casting")
        _lock, _state, turn_status, writer, _life, declarations = (
            owner._store.package_catalog_basis("casting"))
        expected = {declaration.name for _entry, declaration in declarations
                    if declaration.state_predicates
                    and turn_status in declaration.turn_statuses
                    and writer in declaration.writers}
        seeded = seed_tool_names(owner, "casting")
    finally:
        owner.close()
    assert names == expected and names
    for tools in TURN_CYCLE.values():
        assert set(tools) <= names
    # Exposure by entity fields is campaign state too; exposure by spells is not seeded.
    assert names <= seeded
    assert "command_srd_summoned_creature" in seeded - names
    assert "dismiss_srd_illumination_spell" not in seeded


def _tool(name: str, participants: list[str] | None = None) -> dict:
    target = {"type": "string"}
    if participants is not None:
        target["enum"] = participants
    return {"type": "function", "function": {"name": name, "description": name.title(),
            "parameters": {"type": "object", "properties": {"target": target}}}}


def test_seeded_tool_keeps_its_definition_and_only_new_tools_grow_the_list():
    from tests.test_gm_context_ledger import CONTEXT, OWNER, _canonical, _talk, _user

    seed = [_tool("attack")]
    ledger = ContextLedger(session_id="s1", context_owner=OWNER, transport="codex_app_server",
                           one_tool_list=True, seed_tools=seed)
    dialogue = [_user("u1", "Look around")]
    rounds = [
        [_tool("look")],                                 # exploration: attack not offered
        [_tool("look"), _tool("attack", ["guard"])],     # fight: offered, state-built
        [_tool("look"), _tool("attack", ["guard", "dragon"])],
        [_tool("look"), _tool("attack", ["guard"]), _tool("cast", ["light"])],  # new tool
    ]
    frames = []
    for offered in rounds:
        dialogue.append(_talk(f"t{len(dialogue)}", "ok"))
        frame = ledger.plan(_canonical(*dialogue), request_context_id=CONTEXT,
                            tools=copy.deepcopy(offered))
        ledger.commit(frame)
        frames.append(frame)
    names = [[tool["function"]["name"] for tool in frame.tool_list] for frame in frames]
    assert names[:3] == [["look", "attack"]] * 3
    assert names[3] == ["look", "attack", "cast"]
    # The seeded definition is never replaced by the state-built one it is offered with.
    assert all(frame.tool_list[1] == seed[0] for frame in frames)
    assert not any(frame.record and frame.record.get("tools_replaced") for frame in frames)
    choices = [entry["m"]["content"] for frame in frames for entry in frame.appended
               if entry.get("l") == CALLABLE_LABEL]
    assert '"allowed_values":["guard"]' in choices[1]
    assert '"allowed_values":["guard","dragon"]' in choices[2]
    # Only seeded tools get choices; a tool the list does not seed keeps its own enum.
    assert all("cast" not in json.loads(c[c.rindex("\n{") + 1:]) for c in choices)
    for previous, current in zip(frames, frames[1:], strict=False):
        assert current.wire[:len(previous.wire)] == previous.wire


def test_turn_cycle_tools_never_grow_the_seeded_list(casting_seed, monkeypatch):  # noqa: F811
    """Offer each turn-cycle tool as the turn loop builds it (state-built), round by round."""
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter
    from tests.test_gm_context_ledger import CONTEXT, OWNER, _canonical, _talk, _user

    owner = AuthorityOwner.open(casting_seed[0], "cycle-owner", disconnect_grace_seconds=1.0)
    try:
        adapter = ProviderSessionAdapter(owner, FixedGM(), owner_id="cycle-owner")
        names = seed_tool_names(owner, "casting")
        fixed = StatelessMcpMechanics(owner).list_tools(
            "casting", session_id="fixed-session", fixed=True)
        seed, _ = adapter._provider_tools(tuple(t for t in fixed if t.name in names))
        exploration, _ = adapter._provider_tools(StatelessMcpMechanics(owner).list_tools(
            "casting", session_id="fixed-session"))
        # The state-built projection of every tool, as the turn loop sends one once offered.
        catalog = AuthorityOwner.catalog
        monkeypatch.setattr(AuthorityOwner, "catalog", lambda self, campaign_id, **kwargs:
                            catalog(self, campaign_id, include_unexposed=True))
        state_built, _ = adapter._provider_tools(StatelessMcpMechanics(owner).list_tools(
            "casting", session_id="fixed-session"))
    finally:
        owner.close()

    def named(tools, wanted):
        return [tool for tool in tools if tool["function"]["name"].split("_", 2)[-1] in wanted]

    ledger = ContextLedger(session_id="s1", context_owner=OWNER, transport="codex_app_server",
                           one_tool_list=True, seed_tools=sorted(
                               seed, key=lambda tool: tool["function"]["name"]))
    dialogue = [_user("u1", "Fight")]
    frames = []
    for phase, wanted in [("exploration", ()), *TURN_CYCLE.items()]:
        offered = [*exploration, *named(state_built, wanted)]
        assert len(named(offered, wanted)) == len(wanted), phase
        dialogue.append(_talk(f"t{len(dialogue)}", phase))
        frame = ledger.plan(_canonical(*dialogue), request_context_id=CONTEXT, tools=offered)
        ledger.commit(frame)
        frames.append(frame)
    first = encoded(frames[0].tool_list)
    assert all(encoded(frame.tool_list) == first for frame in frames)
    assert frames[0].record["tools_added"]
    assert all(not frame.record or (not frame.record.get("tools_added")
                                    and not frame.record.get("tools_replaced"))
               for frame in frames[1:])


def _changes(definitions: list[list[dict]]) -> list[dict]:
    """Per request, the tools added, removed or redefined since the previous request."""
    out = []
    for index in range(1, len(definitions)):
        before = {t["function"]["name"]: encoded(t) for t in definitions[index - 1]}
        now = {t["function"]["name"]: encoded(t) for t in definitions[index]}
        changed = {
            "added": sorted(set(now) - set(before)), "removed": sorted(set(before) - set(now)),
            "redefined": sorted(n for n in set(now) & set(before) if now[n] != before[n]),
            "reordered": list(before) != [n for n in now if n in before],
        }
        if any(changed.values()):
            out.append({"request": index + 1, **changed})
    return out


def test_seeded_stable_chatgpt_session_keeps_one_list_and_one_thread(
        casting_seed, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv("SYLVIA_GM_CONTEXT_MODE", "ledger")
    monkeypatch.delenv("SYLVIA_GM_TOOL_LIST", raising=False)  # this route's default: stable
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

    try:
        # A seeded tool the game state has not offered: refused and counted, then corrected.
        turn("We look around the clearing.", [
            ("end_combat_encounter", {"end_reason": "fighting_stopped",
                                      "known_spell_effect": False}),
            settle("The clearing is quiet.")])
        from sylvia_gm.agent._provider_session import _plain_json
        scene = _plain_json(owner.snapshot("casting").state[f"{SRD}.state"]["spatial_scene"])
        # A zone id that is not a spell id: spell exposure reads every string in the state.
        scene["zones"] = [{"id": "open_clearing", "map_id": "fixture",
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
        turn("Begin a practice fight, then stop it.", [
            ("start_group_combat_encounter",
             {"combatants": ["hero", "guard", "other"], "combat_scenes": [scene]}),
            ("end_combat_encounter", {"end_reason": "fighting_stopped",
                                      "known_spell_effect": False}),
            settle("The practice fight ends."),
        ])
        first_fight_end = len(gm.definitions)
        owner.close()
        owner = AuthorityOwner.open(database, "fixed-owner", disconnect_grace_seconds=1.0)
        turn("Look around again.", [settle("The clearing is quiet again.")])
        turn("Begin another practice fight, summon a dragon, then stop the fight.", [
            ("start_group_combat_encounter", {"combatants": ["hero", "guard", "other"]}),
            ("begin_srd_spell_action", summon),
            ("complete_srd_pending_action", completion),
            ("end_combat_encounter", {"end_reason": "fighting_stopped"}),
            settle("The summoned dragon remains as the practice fight ends."),
        ])
        from sylvia_gm.agent._tool_exposure import exposure_spell_ids
        spell_exposed = {tool.name for tool in owner.catalog("casting", include_unexposed=True)
                         if exposure_spell_ids([tool.input_schema])}
    finally:
        owner.close()
    assert sum(name.endswith("_start_group_combat_encounter") for name in committed) == 2
    assert sum(name.endswith("_begin_srd_spell_action") for name in committed) == 1
    assert [row["reason"] for row in counted] == ["not_callable"]
    changes = _changes(gm.definitions)
    # The first fight and the owner restart change nothing, byte for byte: every tool the
    # fight admits was declared from the first request, in the bytes a restart rebuilds.
    first = encoded(gm.definitions[0])
    assert all(encoded(tools) == first
               for tools in gm.definitions[:first_fight_end + 1]), changes
    declared = {tool["function"]["name"].split("_", 2)[-1] for tool in gm.definitions[0]}
    assert {"end_combat_encounter", "command_srd_summoned_creature",
            *TURN_CYCLE["death save"]} <= declared
    # Later, only a tool a spell in play exposes joins (here when the summon brings one).
    assert all(not change["removed"] and not change["redefined"] and not change["reordered"]
               for change in changes), changes
    assert all(name.split("_", 2)[-1] in spell_exposed
               for change in changes for name in change["added"]), changes
    for previous, current in zip(gm.calls, gm.calls[1:], strict=False):
        assert current["messages"][:len(previous["messages"])] == previous["messages"]
    # Through the ChatGPT provider: a fresh runtime thread only where a spell added a tool.
    import asyncio
    assert asyncio.run(_replay_threads(gm, tmp_path / "runtime", monkeypatch)) == (
        1 + len(changes))
    assert gm.failure is None


async def _replay_threads(gm, home, monkeypatch) -> int:
    """Replay every request through ChatGPT's stand-in waiting runtime; count its threads.

    Each request's scripted response is played on whichever thread serves it: the kept one,
    or a fresh one when the request cannot continue it (here, only a grown tool list).
    """
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
        for index, (call, definitions, response) in enumerate(
                zip(gm.calls, gm.definitions, gm.responses, strict=True)):
            pending.append([{"id": index + 1, "method": "item/tool/call", "params": {
                "namespace": "sylvia", "tool": response["function"]["name"],
                "callId": response["id"],
                "arguments": json.loads(response["function"]["arguments"])}}])
            reply = await provider.generate(call["messages"], tools=definitions)
            assert [tool.id for tool in reply.tool_calls] == [response["id"]]
        return runtime.threads
    finally:
        await provider.close()
        kept.close_all()
