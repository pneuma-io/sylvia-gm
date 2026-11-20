"""Deferred GM tool list: one declared catalog, tools surfaced by appended additions."""
from __future__ import annotations

import copy
import json

import pytest

from sylvia_gm.agent._context_ledger import (
    CALLABLE_LABEL,
    DEFINITIONS_LABEL,
    LOADER_DEFERRAL,
    LOADER_STUB_NOTE,
    NATIVE_DEFERRAL,
    PROVIDER_DEFERRAL,
    TOOL_CHANGE_CLASS,
    TOOL_CHANGE_LABEL,
    ContextLedger,
    deferred_tool_list,
    stable_tool_list,
)
from sylvia_gm.provider._anthropic import (
    TOOL_CHANGES_BETA,
    AnthropicMessagesTransport,
    with_cache_breakpoints,
)
from tests.test_gm_context_ledger import CONTEXT, OWNER, _canonical, _history, _talk, _user

MODEL = "claude-opus-5-5"


def _tool(name: str, *, spells: list[str] | None = None) -> dict:
    properties: dict = {"note": {"type": "string"}}
    if spells is not None:
        properties["spell"] = {"type": "string", "enum": spells}
    return {"type": "function", "function": {"name": name, "description": name.title(),
                                             "parameters": {"type": "object",
                                                            "properties": properties}}}


CATALOG = [_tool("attack"), _tool("cast"), _tool("read_reference"), _tool("rest")]
CORE = frozenset({"read_reference"})


def _ledger(deferral: str = NATIVE_DEFERRAL, transport: str = "anthropic_messages"):
    return ContextLedger(session_id="s1", context_owner=OWNER, transport=transport,
                         fixed_tools=CATALOG, core_tools=CORE, deferral=deferral)


def _additions(frame) -> list[dict]:
    return [block for entry in frame.appended if entry.get("l") == TOOL_CHANGE_LABEL
            for block in entry["m"]["content"]]


def test_route_gate_kinds():
    env = {"SYLVIA_GM_TOOL_LIST": "deferred"}
    for route in ({"transport": "anthropic_messages", "provider_key": "minimax",
                   "model_id": MODEL},
                  {"transport": "chat_completions", "model_id": "qwen3"},
                  {"transport": "gemini_generate_content", "model_id": "gemini-3"}):
        assert deferred_tool_list(env, **route) == LOADER_DEFERRAL
    # Claude Code loads deferred tools by its own search.
    assert deferred_tool_list(env, transport="claude_code", model_id="opus") == PROVIDER_DEFERRAL
    assert deferred_tool_list({}, transport="chat_completions", model_id="x") is None
    assert deferred_tool_list({"SYLVIA_GM_TOOL_LIST": "fixed"}, transport="chat_completions",
                              model_id="x") is None
    for transport in ("chat_completions", "codex_app_server", "claude_code"):
        assert stable_tool_list(env, transport=transport)


def test_ledger_surfaces_each_tool_once_and_keeps_the_declared_list():
    ledger = _ledger()
    dialogue = [_user("u1", "Look around")]
    frames, records = [], []
    rounds = [
        [_tool("read_reference"), _tool("rest")],
        [_tool("read_reference"), _tool("rest")],
        [_tool("attack"), _tool("cast", spells=["light"])],
        [_tool("cast", spells=["aid"]), _tool("summon_breath")],
        [_tool("read_reference")],
    ]
    for offered in rounds:
        dialogue.append(_talk(f"t{len(dialogue)}", "ok"))
        frame = ledger.plan(_canonical(*dialogue), request_context_id=CONTEXT, tools=offered)
        ledger.commit(frame)
        frames.append(frame)
        records.append(frame.record)
    declared = frames[0].tool_list
    assert [t["function"]["name"] for t in declared] == ["attack", "cast", "read_reference",
                                                         "rest"]
    assert [bool(t.get("defer_loading")) for t in declared] == [True, True, False, True]
    assert all(frame.tool_list == declared for frame in frames)
    assert all(not f.record.get("tools_added") for f in frames[1:] if f.record)
    surfaced = [[(b["tool"]["type"], b["tool"].get("name") or b["tool"]["definition"]["name"])
                 for b in _additions(frame)] for frame in frames]
    assert surfaced == [
        [("tool_reference", "rest")], [],
        [("tool_reference", "attack"), ("tool_reference", "cast")],
        # A tool the declared catalog lacks is defined by value, once.
        [("tool_definition", "summon_breath")], [],
    ]
    by_value = _additions(frames[3])[0]["tool"]["definition"]
    assert set(by_value) == {"name", "description", "input_schema"}
    # The tool change is the last message, after the request's user records.
    for frame in frames:
        if _additions(frame):
            assert frame.wire[-1]["role"] == "system" and frame.wire[-2]["role"] == "user"
    for previous, current in zip(frames[:-1], frames[1:], strict=True):
        assert current.wire[:len(previous.wire)] == previous.wire
    # The callable record names only tools the model can see.
    callable_text = [e["m"]["content"] for f in frames for e in f.appended
                     if e.get("l") == CALLABLE_LABEL]
    assert "attack" not in callable_text[0] and "cast" not in callable_text[0]
    # After a restart the same journal rebuilds the same request, with nothing re-surfaced.
    restarted = _ledger(NATIVE_DEFERRAL)
    rebuilt = restarted.plan(_canonical(*dialogue), request_context_id=CONTEXT,
                             tools=rounds[-1], history_records=lambda: _history(records))
    assert rebuilt.wire == frames[-1].wire and not _additions(rebuilt)
    assert rebuilt.record is None


@pytest.mark.parametrize("transport", ["chat_completions", "anthropic_messages", "responses",
                                       "gemini_generate_content"])
def test_loader_declares_stubs_and_appends_each_definition_once(transport):
    ledger = _ledger(LOADER_DEFERRAL, transport)
    dialogue = [_user("u1", "Look around")]
    frames, records = [], []
    rounds = [
        [_tool("read_reference"), _tool("rest")],
        [_tool("read_reference"), _tool("rest")],
        [_tool("attack"), _tool("cast", spells=["light"])],
        [_tool("cast", spells=["aid"])],
    ]
    for offered in rounds:
        dialogue.append(_talk(f"t{len(dialogue)}", "ok"))
        frame = ledger.plan(_canonical(*dialogue), request_context_id=CONTEXT, tools=offered)
        ledger.commit(frame)
        frames.append(frame)
        records.append(frame.record)
    declared = frames[0].tool_list
    assert [t["function"]["name"] for t in declared] == ["attack", "cast", "read_reference",
                                                         "rest"]
    # A stub: the name and an open schema; the core in full.
    assert declared[0]["function"] == {"name": "attack", "description": LOADER_STUB_NOTE,
                                       "parameters": {"type": "object"}}
    assert declared[2] == _tool("read_reference")
    assert all(frame.tool_list == declared for frame in frames)
    assert ledger.sent_tools(rounds[-1]) == declared
    definitions = [[entry for entry in frame.appended if entry.get("l") == DEFINITIONS_LABEL]
                   for frame in frames]
    assert [[e["n"] for e in found] for found in definitions] == [
        [["rest"]], [], [["attack", "cast"]], []]
    record = definitions[2][0]["m"]
    assert record["role"] == "user" and DEFINITIONS_LABEL in record["content"]
    body = json.loads(record["content"].split(chr(10), 1)[1])
    # The full state-independent definition; current choices stay in the callable record.
    assert body["cast"]["parameters"] == CATALOG[1]["function"]["parameters"]
    assert not _additions(frames[2])
    for previous, current in zip(frames[:-1], frames[1:], strict=True):
        assert current.wire[:len(previous.wire)] == previous.wire
    restarted = _ledger(LOADER_DEFERRAL, transport)
    rebuilt = restarted.plan(_canonical(*dialogue), request_context_id=CONTEXT,
                             tools=rounds[-1], history_records=lambda: _history(records))
    assert rebuilt.wire == frames[-1].wire and rebuilt.record is None
    # Saved records come back with sorted keys; the list sent keeps its own bytes.
    sorted_records = [json.loads(json.dumps(r, sort_keys=True)) for r in records]
    resent = _ledger(LOADER_DEFERRAL, transport).sent_tools(
        rounds[-1], history_records=lambda: sorted_records)
    assert json.dumps(resent) == json.dumps(declared)


def test_loader_adds_a_tool_outside_the_catalog_to_the_list():
    ledger = _ledger(LOADER_DEFERRAL, "chat_completions")
    frame = ledger.plan(_canonical(_user("u1", "Go")), request_context_id=CONTEXT,
                        tools=[_tool("summon_breath")])
    assert [t["function"]["name"] for t in frame.tool_list][-1] == "summon_breath"
    assert frame.tool_list[-1] == _tool("summon_breath")
    assert frame.record["tools_added"][-1] == _tool("summon_breath")


def test_provider_deferral_declares_flags_and_appends_nothing():
    ledger = _ledger(PROVIDER_DEFERRAL, "claude_code")
    frames = []
    dialogue = [_user("u1", "Go")]
    for offered in ([_tool("rest")], [_tool("attack"), _tool("cast", spells=["aid"])]):
        dialogue.append(_talk(f"t{len(dialogue)}", "ok"))
        frame = ledger.plan(_canonical(*dialogue), request_context_id=CONTEXT, tools=offered)
        ledger.commit(frame)
        frames.append(frame)
    declared = frames[0].tool_list
    assert [bool(t.get("defer_loading")) for t in declared] == [True, True, False, True]
    assert all(frame.tool_list == declared for frame in frames)
    assert not any(e.get("c") == TOOL_CHANGE_CLASS for f in frames for e in f.appended)
    assert ledger.sent_tools([_tool("rest")]) == declared


def test_claude_request_keeps_tool_changes_in_the_conversation():
    tools = [dict(_tool("attack"), defer_loading=True), _tool("read_reference")]
    change = {"role": "system", "content": [
        {"type": "tool_addition", "tool": {"type": "tool_reference", "name": "attack"}}]}
    messages = [{"role": "system", "content": "GM CONTRACT"},
                {"role": "user", "content": "Fight"}, change]
    request = with_cache_breakpoints(AnthropicMessagesTransport().format_request(
        MODEL, copy.deepcopy(messages), tools, True,
        extra_headers={"anthropic-beta": "other-beta"}))
    assert request["system"][0]["text"] == "GM CONTRACT"
    assert request["messages"][-1] == change
    assert request["tools"][0] == {"name": "attack", "description": "Attack",
                                   "input_schema": tools[0]["function"]["parameters"],
                                   "defer_loading": True}
    # Breakpoints: never on a deferred tool or the tool change.
    assert "cache_control" not in request["tools"][0]
    assert "cache_control" in request["tools"][1]
    assert "cache_control" in request["messages"][-2]["content"][-1]
    assert request["extra_headers"]["anthropic-beta"] == f"other-beta,{TOOL_CHANGES_BETA}"
    # A request that failed before its reply: a later user record sorts ahead of the change,
    # which then sits just before the next assistant turn.
    later = [*messages, {"role": "user", "content": "Retry"},
             {"role": "assistant", "content": "Swing."}, {"role": "user", "content": "Next"}]
    roles = [m["role"] for m in AnthropicMessagesTransport().format_request(
        MODEL, later, tools, True)["messages"]]
    assert roles == ["user", "user", "system", "assistant", "user"]


def test_requests_without_deferred_tools_are_unchanged():
    tools = [_tool("attack"), _tool("rest")]
    messages = [{"role": "system", "content": "GM CONTRACT"},
                {"role": "user", "content": "Fight"}]
    request = with_cache_breakpoints(AnthropicMessagesTransport().format_request(
        MODEL, messages, tools, True))
    assert "extra_headers" not in request
    assert all("defer_loading" not in tool for tool in request["tools"])
    assert "cache_control" in request["tools"][-1]
    assert [m["role"] for m in request["messages"]] == ["user"]


# ── The scripted D&D session on a stand-in route ─────────────────────────────────────────

#: (route, how it defers) the session test runs on.
SESSION_ROUTES = [("chat", LOADER_DEFERRAL)]


def _rule_problems(request: dict) -> list[str]:
    """The documented Claude request rules the native deferred list depends on, offline."""
    problems = []
    tools = request.get("tools", [])
    names = {tool["name"] for tool in tools}
    if all(tool.get("defer_loading") for tool in tools):
        problems.append("every tool deferred")
    if any(tool.get("defer_loading") and "cache_control" in tool for tool in tools):
        problems.append("breakpoint on a deferred tool")
    messages = request["messages"]
    for index, message in enumerate(messages):
        if message["role"] != "system":
            continue
        if messages[index - 1]["role"] not in {"user", "system"}:
            problems.append("tool change not after a user turn")
        if index + 1 < len(messages) and messages[index + 1]["role"] not in {"assistant",
                                                                             "system"}:
            problems.append("tool change not before an assistant turn")
        for block in message["content"]:
            reference = block["tool"]
            if reference["type"] == "tool_reference" and reference["name"] not in names:
                problems.append("unresolved tool reference")
    if TOOL_CHANGES_BETA not in (request.get("extra_headers") or {}).get("anthropic-beta", ""):
        problems.append("beta header missing")
    return problems


def _clean(value):
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _route_gm(route: str):
    from sylvia_gm.provider._base import ModelInfo
    from sylvia_gm.provider._openai_compat import ChatCompletionsTransport
    from tests.test_gm_context_contract import ContractGM

    claude = route == "claude_api"

    class RouteGM(ContractGM):
        def __init__(self):
            super().__init__()
            self.model_info = ModelInfo(
                id=f"anthropic/{MODEL}" if claude else "openai_compatible/qwen3-32b",
                name="Scripted", provider_name="Fixture",
                provider_key="anthropic" if claude else "openai_compatible",
                provider_mode="byok",
                transport="anthropic_messages" if claude else "chat_completions",
                context_window=1_000_000, max_output_tokens=8192)
            self.requests = []

        async def generate_stream(self, messages, tools=None, **kwargs):
            if self.position < len(self.steps) and callable(self.steps[self.position][1]):
                operation, parameters = self.steps[self.position]
                self.steps[self.position] = operation, parameters()
            if claude:
                request = with_cache_breakpoints(AnthropicMessagesTransport().format_request(
                    MODEL, copy.deepcopy(messages), copy.deepcopy(tools), True))
            else:
                request = ChatCompletionsTransport().format_request(
                    "qwen3-32b", copy.deepcopy(messages), copy.deepcopy(tools), True)
            self.requests.append(request)
            async for chunk in super().generate_stream(messages, tools, **kwargs):
                yield chunk
    return RouteGM()


def _surfaced_names(request: dict, deferral: str) -> list[str]:
    if deferral == NATIVE_DEFERRAL:
        return [block["tool"]["name"] for message in request["messages"]
                if message["role"] == "system" and isinstance(message["content"], list)
                for block in message["content"]]
    names = []
    for message in request["messages"]:
        content = message.get("content")
        if isinstance(content, str) and DEFINITIONS_LABEL in content[:200]:
            names.extend(json.loads(content.split(chr(10), 1)[1]))
    return names


@pytest.mark.parametrize(("route", "deferral"), SESSION_ROUTES)
def test_deferred_session_fight_summon_restart(casting_seed, tmp_path, monkeypatch, route,
                                               deferral):
    import shutil

    from sylvia_gm.agent._provider_session import ProviderSessionAdapter, _plain_json
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
    from sylvia_gm.application.authority import AuthorityOwner
    from tests.test_gm_context_contract import settle
    from tests.test_srd_public_spell_casting import SRD

    monkeypatch.setenv("SYLVIA_GM_CONTEXT_MODE", "ledger")
    monkeypatch.setenv("SYLVIA_GM_TOOL_LIST", "deferred")
    database = tmp_path / "session.db"
    shutil.copy2(casting_seed[0], database)
    gm = _route_gm(route)
    owner = AuthorityOwner.open(database, "fixed-owner", disconnect_grace_seconds=1.0)
    committed = []

    def turn(text, steps):
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
                for record in evidence["results"]:
                    outcome = json.loads(record["content"])
                    if outcome.get("status") == "action_committed":
                        committed.append(record["name"])
                    if outcome.get("status") == "rejected":
                        assert outcome.get("fixed_tool_refusal"), outcome

    try:
        # A refused call to a tool outside this round: refused, counted, then corrected.
        turn("We look around the clearing.", [
            ("end_combat_encounter", {"end_reason": "fighting_stopped",
                                      "known_spell_effect": False}),
            settle("The clearing is quiet.")])
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
    finally:
        owner.close()
    assert sum(name.endswith("_start_group_combat_encounter") for name in committed) == 1
    assert sum(name.endswith("_begin_srd_spell_action") for name in committed) == 1
    requests = gm.requests
    assert len(requests) >= 6
    declared = [_clean(request["tools"]) for request in requests]
    # One declared list for the whole session, byte for byte (key order included), across
    # the owner restart.
    assert all(json.dumps(tools) == json.dumps(declared[0]) for tools in declared)
    if deferral == NATIVE_DEFERRAL:
        loaded = [tool["name"] for tool in declared[0] if not tool.get("defer_loading")]
        for request in requests:
            assert _rule_problems(request) == [], _rule_problems(request)
    else:
        loaded = [tool["function"]["name"] for tool in declared[0]
                  if tool["function"]["description"] != LOADER_STUB_NOTE]
    assert loaded and len(loaded) < len(declared[0]) // 4
    assert all(not name.endswith("begin_srd_spell_action") for name in loaded)
    for previous, current in zip(requests[:-1], requests[1:], strict=True):
        assert _clean(current.get("system")) == _clean(previous.get("system"))
        before = _clean(previous["messages"])
        assert _clean(current["messages"])[:len(before)] == before
    surfaced = _surfaced_names(requests[-1], deferral)
    assert len(surfaced) == len(set(surfaced))
    assert any(name.endswith("begin_srd_spell_action") for name in surfaced)
    assert any(name.endswith("start_group_combat_encounter") for name in surfaced)
    # Tools the session never offered stay deferred and are never surfaced.
    assert len(surfaced) + len(loaded) < len(declared[0])
    assert gm.failure is None


@pytest.fixture(scope="module")
def casting_seed(tmp_path_factory):
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import WriterRole
    from tests.test_srd_public_spell_casting import _create_spell_campaign

    database = tmp_path_factory.mktemp("deferred-seed") / "seed.db"
    with AuthorityOwner.open(database, "deferred-seed") as owner:
        _state, _data, parameters = _create_spell_campaign(owner, "summon_dragon")
        owner.create_agent_session(campaign_id="casting", session_id="fixed-session",
                                   role=WriterRole.DM, provider_id="anthropic",
                                   model_id=f"anthropic/{MODEL}")
    return database, parameters
