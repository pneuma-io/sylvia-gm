"""Deferred GM tool list: one declared catalog, tools surfaced by appended additions."""
from __future__ import annotations

import copy
import json

import pytest

from sylvia_gm.agent._context_ledger import (
    CALLABLE_LABEL,
    LOADER_DEFERRAL,
    NATIVE_DEFERRAL,
    PROVIDER_DEFERRAL,
    TOOL_CHANGE_CLASS,
    TOOL_CHANGE_LABEL,
    ContextLedger,
    deferred_tool_list,
    stable_tool_list,
)
from sylvia_gm.agent._tool_loader import DEFINITIONS_LABEL, MECHANIC_TOOL_NAME
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


def test_a_registered_route_whose_model_cannot_defer_keeps_one_stable_list(monkeypatch):
    from sylvia_gm.agent import _context_ledger as ledger_module

    monkeypatch.setitem(ledger_module.NATIVE_DEFERRED_ROUTES, "chat_completions",
                        lambda model_id: model_id == "able")
    env = {"SYLVIA_GM_TOOL_LIST": "deferred"}
    assert deferred_tool_list(env, transport="chat_completions", model_id="able") == "native"
    assert deferred_tool_list(env, transport="chat_completions", model_id="other") is None
    assert stable_tool_list(env, transport="chat_completions")
    assert deferred_tool_list({"SYLVIA_GM_TOOL_LIST": "fixed"}, transport="chat_completions",
                              model_id="x") is None
    for transport in ("chat_completions", "codex_app_server", "claude_code"):
        assert stable_tool_list(env, transport=transport)


def test_claude_api_defers_natively_by_default_on_models_with_tool_changes():
    claude_api = {"transport": "anthropic_messages", "provider_key": "anthropic"}
    for env in ({"SYLVIA_GM_TOOL_LIST": "deferred"}, {}):
        assert deferred_tool_list(env, **claude_api, model_id=MODEL) == NATIVE_DEFERRAL
        assert deferred_tool_list(env, **claude_api,
                                  model_id="claude-fable-5-2") == NATIVE_DEFERRAL
        # Claude Sonnet 5 takes no mid-conversation tool changes: one stable list, as before.
        assert deferred_tool_list(env, **claude_api, model_id="claude-sonnet-5") is None
        assert stable_tool_list(env, **claude_api)
    for setting in ("stable", "fixed", "rounds"):
        assert deferred_tool_list({"SYLVIA_GM_TOOL_LIST": setting}, **claude_api,
                                  model_id=MODEL) is None
    # Other Messages endpoints keep their own default (per-round lists).
    assert deferred_tool_list({}, transport="anthropic_messages", provider_key="minimax",
                              model_id=MODEL) is None

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
    assert _clean(request["messages"][-1]) == change
    assert request["tools"][0] == {"name": "attack", "description": "Attack",
                                   "input_schema": tools[0]["function"]["parameters"],
                                   "defer_loading": True}
    # Breakpoints: never on a deferred tool; on the message before the tool change (which
    # finds the previous entry) and on the change's last block (its definitions are cached).
    assert "cache_control" not in request["tools"][0]
    assert "cache_control" in request["tools"][1]
    assert "cache_control" in request["messages"][-2]["content"][-1]
    assert "cache_control" in request["messages"][-1]["content"][-1]
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
SESSION_ROUTES = [("claude_api", NATIVE_DEFERRAL), ("chat", LOADER_DEFERRAL)]


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
            last = messages[-1] if messages else {}
            if last.get("role") == "tool" and "definition_given" in str(last.get("content")):
                self.position -= 1  # the loader defined the mechanic instead: call it again
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
            declared = [tool["function"]["name"] for tool in tools or ()]
            offered = tools
            if MECHANIC_TOOL_NAME in declared:
                # Sylvia's loader: mechanics are named in definitions records, called through
                # the generic tool.
                names = [*declared, *_loader_defined(messages)]
                kind = self.steps[self.position][0] if self.position < len(self.steps) else ""
                if kind != "prose" and not any(
                        name == kind or name.endswith("_" + kind) for name in names):
                    names.append(kind)  # a mechanic never offered yet, called anyway
                offered = [*(tools or ()), *({"type": "function", "function": {"name": name}}
                                             for name in names[len(declared):])]
            async for chunk in super().generate_stream(messages, offered, **kwargs):
                delta = chunk.tool_call_delta
                if delta is not None and delta["function"]["name"] not in declared:
                    chunk.tool_call_delta = {**delta, "function": {
                        "name": MECHANIC_TOOL_NAME, "arguments": json.dumps({
                            "name": delta["function"]["name"],
                            "arguments": json.loads(delta["function"]["arguments"])})}}
                yield chunk
    return RouteGM()


def _tool_name_of(tool: dict) -> str:
    return tool.get("name") or tool["function"]["name"]


def _loader_defined(messages) -> list[str]:
    import re

    return [name for message in messages
            if isinstance(message.get("content"), str)
            and DEFINITIONS_LABEL in message["content"][:200]
            for name in re.findall(r"^## (\S+)", message["content"], re.M)]


def _surfaced_names(request: dict, deferral: str) -> list[str]:
    if deferral == NATIVE_DEFERRAL:
        return [block["tool"]["name"] for message in request["messages"]
                if message["role"] == "system" and isinstance(message["content"], list)
                for block in message["content"]]
    return _loader_defined(request["messages"])


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
        assert loaded and len(loaded) < len(declared[0]) // 4
    else:
        # The loader declares the application's own tools and the generic mechanic tool.
        loaded = [tool["function"]["name"] for tool in declared[0]]
        assert loaded[-1] == MECHANIC_TOOL_NAME and len(loaded) < 16
    assert all(not name.endswith("begin_srd_spell_action") for name in loaded)
    # Host-local tools (outside the catalog: session history, an argument reader) are always
    # loaded from the first request; catalog aliases carry a package digest prefix.
    import re

    host_local = [_tool_name_of(tool) for tool in declared[0]
                  if not re.match(r"tool_[0-9a-f]{12}_", _tool_name_of(tool))]
    assert "read_session_history" in host_local
    assert set(host_local) <= set(loaded)
    for previous, current in zip(requests[:-1], requests[1:], strict=True):
        assert _clean(current.get("system")) == _clean(previous.get("system"))
        before = _clean(previous["messages"])
        assert _clean(current["messages"])[:len(before)] == before
    surfaced = _surfaced_names(requests[-1], deferral)
    assert len(surfaced) == len(set(surfaced))
    assert any(name.endswith("begin_srd_spell_action") for name in surfaced)
    assert any(name.endswith("start_group_combat_encounter") for name in surfaced)
    if deferral == NATIVE_DEFERRAL:
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
