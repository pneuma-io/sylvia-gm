"""Fixed GM catalog through real casting, encounter transitions and owner restart."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import WriterRole
from tests.test_gm_context_contract import ContractGM, settle
from tests.test_srd_public_spell_casting import SRD, _create_spell_campaign


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


class FixedGM(ContractGM):
    def __init__(self):
        super().__init__()
        from sylvia_gm.provider._base import ModelInfo
        self.model_info = ModelInfo(
            id="test.transport/model0", name="Scripted", provider_name="Fixture",
            provider_key="test.transport", provider_mode="byok", transport="codex_app_server",
            context_window=1_000_000, max_output_tokens=8192)
        self.definitions = []
        self.responses = []

    async def generate_stream(self, messages, tools=None, **kwargs):
        if self.position < len(self.steps) and callable(self.steps[self.position][1]):
            operation, parameters = self.steps[self.position]
            self.steps[self.position] = operation, parameters()
        self.definitions.append(copy.deepcopy(tools))
        async for chunk in super().generate_stream(messages, tools, **kwargs):
            if chunk.tool_call_delta is not None:
                self.responses.append(copy.deepcopy(chunk.tool_call_delta))
            yield chunk


@pytest.fixture(scope="module")
def casting_seed(tmp_path_factory):
    database = tmp_path_factory.mktemp("fixed-seed") / "seed.db"
    with AuthorityOwner.open(database, "fixed-seed") as owner:
        _state, _data, parameters = _create_spell_campaign(owner, "summon_dragon")
        owner.create_agent_session(campaign_id="casting", session_id="fixed-session",
                                   role=WriterRole.DM, provider_id="test.transport",
                                   model_id="test.transport/model0")
        fixed = StatelessMcpMechanics(owner).list_tools(
            "casting", session_id="fixed-session", fixed=True)
        ordinary = StatelessMcpMechanics(owner).list_tools("casting", session_id="fixed-session")
        from sylvia_gm.agent._provider_session import ProviderSessionAdapter
        adapter = ProviderSessionAdapter(owner, FixedGM(), owner_id="fixed-seed")
        for label, catalog in (("before", ordinary), ("fixed", fixed)):
            definitions, _ = adapter._provider_tools(catalog)
            for tool in definitions:
                if any(tool["function"]["name"].endswith("_" + name) for name in
                       ("begin_srd_spell_action", "cast_spell_outside_combat")):
                    print(label, tool["function"]["name"], "definition bytes", len(encoded(tool)),
                          "schema bytes", len(encoded(tool["function"]["parameters"])), flush=True)
    return database, parameters


def forbid_fixed_code(monkeypatch):
    """Make every fixed-list-only entry point fail, so an off setting provably never reaches it."""
    from sylvia_gm.agent import _fixed_tool_projection, _provider_turn_phases, _stateless_mcp
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter

    def forbidden(*_args, **_kwargs):
        raise AssertionError("fixed tool list code ran with the setting off")
    for module, name in ((_fixed_tool_projection, "argument_choices_record"),
                         (_fixed_tool_projection, "schema_choices"),
                         (_fixed_tool_projection, "unavailable_values"),
                         (_fixed_tool_projection, "compact_schema"),
                         (_stateless_mcp, "_fixed_campaign_file_schema"),
                         (_provider_turn_phases, "record_fixed_tool_refusal"),
                         (ProviderSessionAdapter, "_fixed_provider_tools")):
        monkeypatch.setattr(module, name, forbidden)
    catalog = AuthorityOwner.catalog

    def ordinary_catalog(owner, campaign_id, **kwargs):
        assert not kwargs.get("include_unexposed"), "fixed catalog read with the setting off"
        return catalog(owner, campaign_id, **kwargs)
    monkeypatch.setattr(AuthorityOwner, "catalog", ordinary_catalog)


# "default" leaves SYLVIA_GM_TOOL_LIST unset; with every off setting the session must never run
# fixed-only code, so its requests are the ones the existing modes already send.
@pytest.mark.parametrize("mode", ["fixed", "stable", "rounds", "default"])
def test_fixed_session_catalog(casting_seed, tmp_path, monkeypatch, mode):
    monkeypatch.setenv("SYLVIA_GM_CONTEXT_MODE", "ledger")
    if mode == "default":
        monkeypatch.delenv("SYLVIA_GM_TOOL_LIST", raising=False)
    else:
        monkeypatch.setenv("SYLVIA_GM_TOOL_LIST", mode)
    if mode != "fixed":
        forbid_fixed_code(monkeypatch)
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
        gm.steps.extend(steps)
        from sylvia_gm.agent._provider_session import ProviderSessionAdapter
        from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
        identity = f"turn-{len(gm.calls)}"
        result = CampaignSessionLoop(owner).run(SessionRunRequest(
            session_id="fixed-session", turn_id="casting-turn", attempt_id=identity,
            message_id=identity, user_text=text,
        ), ProviderSessionAdapter(owner, gm, owner_id="fixed-owner").for_turn("casting-turn"))
        assert result.interruption is None, (result.interruption, gm.calls[-1:])
        assert gm.position == len(gm.steps), (gm.position, gm.steps)
        for message in owner.session_context("fixed-session").messages:
            evidence = message.content
            if (evidence.get("kind") == "provider_tool_results"
                    and evidence.get("basis_message_id") == identity):
                for record in evidence["results"]:
                    outcome = json.loads(record["content"])
                    assert outcome.get("status") != "action_failed", outcome
                    if outcome.get("status") == "action_committed":
                        committed.append(record["name"])
                    if outcome.get("status") == "rejected":
                        assert mode == "fixed" and outcome.get("fixed_tool_refusal"), outcome
        return result

    try:
        refused = [("end_combat_encounter", {"end_reason": "fighting_stopped", "known_spell_effect": False})] if mode == "fixed" else []
        turn("We look around the clearing.", [*refused, settle("The clearing is quiet.")])
        from sylvia_gm.agent._provider_session import _plain_json
        scene = _plain_json(owner.snapshot("casting").state[f"{SRD}.state"]["spatial_scene"])
        scene["zones"] = [{"id": "daylight", "map_id": "fixture",
                           "shape": {"kind": "box", "min": [-100, -100, -10], "max": [100, 100, 20]},
                           "metadata": {"lighting": "bright", "plane": "material"}}]
        encounter = {"combatants": ["hero", "guard", "other"], "combat_scenes": [scene]}
        summon = dict(casting_seed[1])
        summon.pop("target")
        summon.update(summon_id="dragon_one", summon_distance=30,
                      caster_can_see_summon_space=True, summon_space_unoccupied=True,
                      selected_damage_type="fire", created_actor_position=[0, 30, 0],
                      created_actor_height=6, created_actor_lighting="bright")
        wrong = [("begin_srd_spell_action", {**summon, "spell": "not_prepared"})] if mode == "fixed" else []
        def completion():
            pending = owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_action_stack"]
            return {"pending_action_id": pending[-1]}
        turn("Begin a practice fight, summon a dragon, then stop the fight.", [
            ("start_group_combat_encounter", encounter),
            *wrong, ("begin_srd_spell_action", summon),
            ("complete_srd_pending_action", completion),
            ("end_combat_encounter", {"end_reason": "fighting_stopped"}),
            settle("The summoned dragon remains as the practice fight ends."),
        ])
        assert owner.snapshot("casting").state[f"{SRD}.entities"]["dragon_one"]["summon_active"] is True
        owner.close()
        owner = AuthorityOwner.open(database, "fixed-owner", disconnect_grace_seconds=1.0)
        turn("Look around again.", [settle("The clearing is quiet again.")])
        turn("Begin another practice fight, then stop it.", [
            ("start_group_combat_encounter", {"combatants": ["hero", "guard", "other", "dragon_one"]}),
            ("end_combat_encounter", {"end_reason": "fighting_stopped", "known_spell_effect": False}),
            settle("The second practice fight ends."),
        ])
        records = [m.content for m in owner.session_context("fixed-session").messages
                   if m.content.get("kind") == "gm_wire_journal"]
        assert records
        if mode == "fixed":
            assert records[0].get("tools_added")
            assert all(not r.get("tools_added") and not r.get("tools_replaced") for r in records[1:])
        elif mode != "rounds":
            # The route default here (codex_app_server) is one stable list, which grows.
            assert sum(bool(r.get("tools_added")) for r in records[1:]) >= 2
    finally:
        owner.close()
    assert sum(name.endswith("_start_group_combat_encounter") for name in committed) == 2
    assert sum(name.endswith("_end_combat_encounter") for name in committed) == 2
    assert sum(name.endswith("_begin_srd_spell_action") for name in committed) == 1
    assert sum(name.endswith("_complete_srd_pending_action") for name in committed) == 1
    definitions = [encoded(tools) for tools in gm.definitions]
    print(mode, "first", len(gm.definitions[0]), len(definitions[0]),
          "requests", len(definitions), "distinct", len(set(definitions)))
    if mode == "fixed":
        assert len(set(definitions)) == 1
        assert sorted(row["reason"] for row in counted) == ["argument_value", "not_callable"]
        assert len({row["call_id"] for row in counted}) == 2
        refusals = {message["tool_call_id"]: json.loads(message["content"])
                    for call in gm.calls for message in call["messages"]
                    if message.get("role") == "tool" and "fixed_tool_refusal" in str(message.get("content"))}
        assert sorted(r["fixed_tool_refusal"] for r in refusals.values()) == ["argument_value", "not_callable"]
        assert next(r for r in refusals.values() if r["fixed_tool_refusal"] == "not_callable")["available_operations"]
        bad_spell = next(r for r in refusals.values() if r["fixed_tool_refusal"] == "argument_value")
        assert bad_spell["validation"]["issues"][0]["allowed_values"] == ["summon_dragon"]
        # Each refusal is followed by the scripted correction, without another refused round.
        assert len(gm.responses) == len(gm.steps)
        import asyncio
        asyncio.run(replay_on_chatgpt(gm, tmp_path / "runtime", monkeypatch))
    else:
        assert not counted
        assert len(set(definitions)) > 1
    for previous, current in zip(gm.calls, gm.calls[1:]):
        assert current["messages"][:len(previous["messages"])] == previous["messages"]
    assert gm.failure is None


@pytest.mark.parametrize("transport", ["chat_completions", "responses", "anthropic_messages",
                                       "gemini_generate_content", "claude_code", "codex_app_server"])
def test_fixed_ledger_rebuilds_identical_definitions_and_only_sends_changed_choices(transport):
    from sylvia_gm.agent._context_ledger import ContextLedger, CALLABLE_LABEL
    from tests.test_gm_context_ledger import OWNER, CONTEXT, _canonical, _user, _talk, _history

    fixed = [{"type": "function", "function": {"name": "cast", "description": "Cast",
              "parameters": {"type": "object", "properties": {"spell": {"type": "string"}}}}}]
    def offered(spells):
        result = copy.deepcopy(fixed)
        result[0]["function"]["parameters"]["properties"]["spell"]["enum"] = spells
        return result
    ledger = ContextLedger(session_id="s1", context_owner=OWNER, transport=transport,
                           fixed_tools=fixed)
    dialogue = [_user("u1", "Cast")]
    records, frames = [], []
    for spells in (["light"], ["light"], ["aid"], []):
        dialogue.append(_talk(f"t{len(dialogue)}", "ok"))
        frame = ledger.plan(_canonical(*dialogue), request_context_id=CONTEXT,
                            tools=offered(spells))
        ledger.commit(frame)
        if transport == "claude_code":
            from tests.gm_context_contract import native_route, simulate_collection
            simulate_collection(frame.state, native_route(frame.state, frame.wire))
        records.append(frame.record)
        frames.append(frame)
        assert frame.tool_list == fixed
    assert frames[0].record["tools_added"] == fixed
    choices = [entry for frame in frames for entry in frame.appended if entry.get("l") == CALLABLE_LABEL]
    assert len(choices) == 3
    assert all(entry["m"]["role"] == "user" for entry in choices)
    assert '"allowed_values":["light"]' in choices[0]["m"]["content"]
    assert '"allowed_values":[]' in choices[-1]["m"]["content"]
    assert all(not f.record.get("tools_added") and not f.record.get("tools_replaced")
               for f in frames[1:])
    assert not any(e.get("l") == CALLABLE_LABEL for e in frames[1].appended)
    for previous, current in zip(frames, frames[1:]):
        assert current.wire[:len(previous.wire)] == previous.wire
    restarted = ContextLedger(session_id="s1", context_owner=OWNER, transport=transport,
                              fixed_tools=fixed)
    if transport == "claude_code":
        restarted.state = copy.deepcopy(ledger.state)
    rebuilt = restarted.plan(_canonical(*dialogue), request_context_id=CONTEXT,
                             tools=offered([]), history_records=lambda: _history(records))
    assert rebuilt.tool_list == fixed and rebuilt.wire == frames[-1].wire
    assert rebuilt.record is None


def test_fixed_projection_keeps_static_enums_and_removes_only_state_choices():
    from sylvia_gm.agent._stateless_mcp import _model_input_schema
    original = {"type": "object", "properties": {
        "actor": {"type": "string", "x-sylvia-gm-reference": "participant"},
        "spell": {"type": "string", "enum": ["light", "aid"],
                  "x-sylvia-gm-castable-spells": True},
        "mode": {"type": "string", "enum": ["walk", "fly"]}}, "required": ["actor"]}
    fixed, _ = _model_input_schema(original, participant_ids=["a"],
                                   castable_spell_ids=frozenset({"light"}), fixed=True)
    again, _ = _model_input_schema(original, participant_ids=["b", "c"],
                                   castable_spell_ids=frozenset(), fixed=True)
    assert encoded(fixed) == encoded(again)
    assert "enum" not in fixed["properties"]["actor"]
    assert "enum" not in fixed["properties"]["spell"]
    assert fixed["properties"]["mode"]["enum"] == ["walk", "fly"]
    stable, _ = _model_input_schema(original, participant_ids=["a"],
                                    castable_spell_ids=frozenset({"light"}))
    assert stable["properties"]["actor"]["enum"] == ["a"]
    assert stable["properties"]["spell"]["enum"] == ["light"]


def test_fixed_narrative_schema_survives_exhausted_secrets_and_plan_choices():
    from sylvia_gm.agent._stateless_mcp import _fixed_campaign_file_schema
    before = {"operations": ["record_memory", "record_entity", "update_scene",
                             "reveal_secret", "update_plan_progress"],
              "scene_act_ids": ["first-act"], "plan_item_ids": ["first-act"],
              "eligible_secret_ids": ["secret-one"]}
    after = {"operations": ["record_memory", "record_entity", "update_scene"],
             "scene_act_ids": [], "plan_item_ids": [], "eligible_secret_ids": []}
    assert encoded(_fixed_campaign_file_schema(before)) == encoded(_fixed_campaign_file_schema(after))


def test_current_choices_and_refusals_include_nested_values_without_echoing_bad_input():
    from sylvia_gm.agent._fixed_tool_projection import schema_choices, unavailable_values
    fixed = {"type": "object", "properties": {"updates": {"type": "array", "items": {
        "type": "object", "properties": {"actor": {"type": "string"}}}}}}
    current = copy.deepcopy(fixed)
    current["properties"]["updates"]["items"]["properties"]["actor"]["enum"] = ["hero"]
    assert schema_choices(current, fixed) == {"$.updates[].actor": {"allowed_values": ["hero"]}}
    assert unavailable_values({"updates": [{"actor": "wrong"}]}, current, fixed) == [
        {"path": "updates[0].actor", "kind": "invalid_enum_value", "allowed_values": ["hero"]}]
    assert unavailable_values({"updates": [{"actor": "hero"}]}, current, fixed) == []


async def replay_on_chatgpt(gm, home, monkeypatch):
    """Send the actual session requests through ChatGPT's stand-in waiting runtime."""
    from sylvia_gm.provider import _chatgpt
    from tests.test_chatgpt_answer_in_place import AnswerRuntime, Waiting, NARRATION
    from tests.test_chatgpt_stable_thread import provider_for
    events = []
    for index, response in enumerate(gm.responses):
        events.append([{"id": index + 1, "method": "item/tool/call", "params": {
            "namespace": "sylvia", "tool": response["function"]["name"],
            "callId": response["id"], "arguments": json.loads(response["function"]["arguments"]),
        }}])
    runtime = AnswerRuntime(Waiting(events[0], *events[1:], NARRATION))
    kept = _chatgpt._KeptThreads()
    monkeypatch.setattr(_chatgpt, "_KEPT_THREADS", kept)
    monkeypatch.setattr(_chatgpt, "_ledger_request", lambda: True)
    monkeypatch.setattr(_chatgpt, "_USAGE_SETTLE_SECONDS", 0.01)
    monkeypatch.setenv("SYLVIA_CHATGPT_THREAD", "answer")
    provider, audit = provider_for(runtime, home)
    try:
        for call, definitions, response in zip(gm.calls, gm.definitions, gm.responses, strict=True):
            reply = await provider.generate(call["messages"], tools=definitions)
            assert [tool.id for tool in reply.tool_calls] == [response["id"]]
        assert runtime.threads == 1
        assert len(runtime.connections) == 1
        assert len(runtime.sent("thread/start")) == 1
        assert all(row["continued"] for row in audit.reuse[1:])
        assert not any(row.get("fresh_reason") for row in audit.reuse)
    finally:
        await provider.close()
        kept.close_all()


def test_compact_schema_is_lossless_and_retains_validation():
    from jsonschema import Draft202012Validator
    from sylvia_gm.agent._fixed_tool_projection import compact_schema
    point = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
    original = {"type": "object", "properties": {f"point_{i}": point for i in range(40)},
                "required": ["point_0"], "additionalProperties": False}
    projected = compact_schema(copy.deepcopy(original))
    assert len(encoded(projected)) < len(encoded(original))
    expanded = copy.deepcopy(projected)
    definitions = expanded.pop("$defs")
    expanded["properties"] = {key: definitions[value["$ref"].split("/")[-1]]
                              for key, value in expanded["properties"].items()}
    assert expanded == original
    for args in ({"point_0": [1, 2, 3]}, {"point_0": [1, 2]}, {"point_0": [1, "x", 3]},
                 {}, {"point_0": [1, 2, 3], "unknown": 4}):
        assert Draft202012Validator(projected).is_valid(args) == Draft202012Validator(original).is_valid(args)


@pytest.mark.asyncio
async def test_compact_references_survive_every_provider_serializer(tmp_path):
    from sylvia_gm.agent._fixed_tool_projection import compact_schema
    from sylvia_gm.provider._anthropic import AnthropicMessagesTransport
    from sylvia_gm.provider._openai_compat import ChatCompletionsTransport, ResponsesTransport
    from sylvia_gm.provider._chatgpt import app_server_request
    from sylvia_gm.provider._claude import prepare_request
    from tests.test_native_gemini_provider import make_provider

    point = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
    schema = compact_schema({"type": "object", "properties": {f"p{i}": point for i in range(20)}})
    assert "$defs" in schema
    tools = [{"type": "function", "function": {
        "name": "fixture", "description": "Fixture", "parameters": schema}}]
    messages = [{"role": "user", "content": "Fixture"}]
    anthropic = AnthropicMessagesTransport().format_request("fixture", messages, tools, True)
    assert anthropic["tools"][0]["input_schema"] == schema
    chat = ChatCompletionsTransport().format_request("fixture", messages, tools, True)
    assert chat["tools"][0]["function"]["parameters"] == schema
    responses = ResponsesTransport().format_request("fixture", messages, tools, True)
    assert responses["tools"][0]["parameters"] == schema
    assert app_server_request("fixture", messages, tools)[0][0]["inputSchema"] == schema
    assert prepare_request(messages, tools, "fixture", tmp_path)[3][0]["inputSchema"] == schema
    def no_network(request):
        pytest.fail("Serialization must not make a model request")
    gemini = make_provider(no_network)
    try:
        wire = gemini.provider_request_representation(messages, tools, {"stream": False})
        assert wire["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"] == schema
    finally:
        await gemini.close()


def test_fixed_switch_has_no_effect_on_legacy_requests(monkeypatch):
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter
    from sylvia_gm.provider._base import ProviderStreamChunk
    from tests.test_alpha_f4_provider_session_adapter import (
        _OwnerProjection, _ScriptedProvider, _provider_input,
    )
    monkeypatch.setenv("SYLVIA_GM_CONTEXT_MODE", "legacy")
    requests = []
    for mode in ("rounds", "stable", "fixed"):
        monkeypatch.setenv("SYLVIA_GM_TOOL_LIST", mode)
        provider = _ScriptedProvider([[ProviderStreamChunk(content="Fixture", finish_reason="stop")]])
        adapter = ProviderSessionAdapter(_OwnerProjection(), provider, owner_id="owner-provider")
        def unexpected(*args):
            pytest.fail("Legacy must not build a fixed catalog")
        monkeypatch.setattr(adapter, "_fixed_provider_tools", unexpected)
        provider_input = _provider_input()
        tools, _ = adapter._provider_tools(provider_input.catalog)
        list(adapter._stream_round(adapter._messages(provider_input), tools,
                                   control=provider_input.control, provider_input=provider_input))
        requests.append(provider.calls)
    assert requests[0] == requests[1] == requests[2]


def test_choices_record_limits_only_the_fields_it_names():
    # Issue 235: a participant-only field's choices read as the only allowed target, so a
    # model declined to cast on an NPC whose ID belongs in the unlisted target field.
    from sylvia_gm.agent._fixed_tool_projection import argument_choices_record
    fixed = [{"type": "function", "function": {"name": "cast", "parameters": {
        "type": "object", "properties": {"target_character": {"type": "string"},
                                         "target": {"type": "string"}}}}}]
    offered = copy.deepcopy(fixed)
    offered[0]["function"]["parameters"]["properties"]["target_character"]["enum"] = ["hero"]
    record = argument_choices_record(fixed, offered)
    assert '"cast":{"$.target_character":{"allowed_values":["hero"]}}' in record
    assert "limits only the field it names" in record
    assert "A field not listed is not limited by this record" in record
    assert "Use only these allowed_values and available_fields where given" not in record
