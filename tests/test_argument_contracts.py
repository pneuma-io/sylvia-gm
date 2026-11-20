"""Argument contracts: a small casting envelope whose selected inputs are read on demand."""
from __future__ import annotations

import copy
import json

import pytest

from sylvia_gm.agent._argument_contracts import (
    ARGUMENTS_TOOL_NAME,
    CONTRACT_KEY,
    argument_contract,
    envelope_schema,
    read_tool_arguments,
    selected_schema,
    unwrap_arguments,
)


def schema():
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "spell": {"type": "string", "enum": ["fire_bolt", "bless", "magic_stone"],
                      "x-sylvia-gm-value-aliases": {"magic_stone": "fire_bolt"}},
            "caster": {"type": "string"},
            "target": {"type": "string"},
            "targets": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "fire_bolt_object_facts": {"type": "object"},
        },
        "required": ["spell", "caster"],
        CONTRACT_KEY: {"version": 1, "selector": "spell", "field": "spell_arguments",
                       "common": ["caster"],
                       "by_value": {"fire_bolt": ["target", "fire_bolt_object_facts"],
                                    "bless": ["targets"]}},
    }


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def test_envelope_shows_only_selector_common_inputs_and_one_object():
    full = schema()
    contract = argument_contract(full)
    envelope = envelope_schema(full, contract)
    assert set(envelope["properties"]) == {"spell", "caster", "spell_arguments"}
    assert envelope["required"] == ["spell", "caster"]
    assert envelope["properties"]["spell_arguments"]["type"] == "object"
    assert ARGUMENTS_TOOL_NAME in envelope["properties"]["spell_arguments"]["description"]
    assert CONTRACT_KEY not in envelope and envelope["additionalProperties"] is False
    assert full == schema()  # the executable schema is untouched


def test_selected_schema_is_the_exact_value_contract_and_follows_aliases():
    full = schema()
    contract = argument_contract(full)
    bless = selected_schema(full, contract, "bless")
    assert bless == {"type": "object", "properties": {"targets": full["properties"]["targets"]},
                     "required": [], "additionalProperties": False}
    assert selected_schema(full, contract, "magic_stone") == selected_schema(
        full, contract, "fire_bolt")
    assert selected_schema(full, contract, "wish") is None


@pytest.mark.parametrize("arguments", [
    {"spell": "bless", "caster": "c", "spell_arguments": {"targets": ["a"]}},
    {"spell": "bless", "caster": "c", "targets": ["a"]},
])
def test_unwrap_accepts_nested_or_flat_selected_inputs(arguments):
    full = schema()
    flat, issues = unwrap_arguments(arguments, full, argument_contract(full))
    assert issues == []
    assert flat == {"spell": "bless", "caster": "c", "targets": ["a"]}


@pytest.mark.parametrize(("arguments", "expected"), [
    ({"spell": "bless", "caster": "c", "spell_arguments": {"tsunami_axis": "x"}},
     [{"path": "spell_arguments.tsunami_axis", "kind": "unknown_input"}]),
    ({"spell": "bless", "caster": "c", "targets": ["a"], "spell_arguments": {"targets": ["b"]}},
     [{"path": "spell_arguments.targets", "kind": "duplicate_input"}]),
    ({"spell": "bless", "caster": "c", "spell_arguments": {"caster": "d"}},
     [{"path": "spell_arguments.caster", "kind": "duplicate_input"}]),
    ({"spell": "bless", "spell_arguments": {"caster": "d"}},
     [{"path": "spell_arguments.caster", "kind": "belongs_at_top_level"}]),
    ({"spell": "bless", "caster": "c", "spell_arguments": "targets"},
     [{"path": "spell_arguments", "kind": "not_an_object"}]),
])
def test_unwrap_refuses_misplaced_or_unknown_inputs(arguments, expected):
    full = schema()
    _flat, issues = unwrap_arguments(copy.deepcopy(arguments), full, argument_contract(full))
    assert issues == expected


@pytest.mark.parametrize("arguments", [
    {"spell": "bless", "caster": "c", "target": "a"},
    {"spell": "bless", "caster": "c", "spell_arguments": {"target": "a"}},
])
def test_inputs_of_another_choice_reach_the_existing_checks_unchanged(arguments):
    # The engine ignores an input the selected spell never reads and refuses one it forbids,
    # exactly as it does for a call made with the full schema.
    full = schema()
    flat, issues = unwrap_arguments(copy.deepcopy(arguments), full, argument_contract(full))
    assert issues == [] and flat == {"spell": "bless", "caster": "c", "target": "a"}


def test_unwrap_validates_selected_values_and_leaves_unknown_values_to_existing_checks():
    full = schema()
    contract = argument_contract(full)
    _flat, issues = unwrap_arguments(
        {"spell": "bless", "caster": "c", "spell_arguments": {"targets": ["a", "b", "c", "d"]}},
        full, contract)
    assert [issue["kind"] for issue in issues] == ["invalid_value"]
    assert issues[0]["path"] == "spell_arguments.targets"
    flat, issues = unwrap_arguments({"spell": "wish", "caster": "c", "target": "a"},
                                    full, contract)
    assert issues == [] and flat == {"spell": "wish", "caster": "c", "target": "a"}


def test_read_tool_arguments_answers_by_listed_name_or_operation():
    full = schema()
    tools = {"tool_abc_begin_cast": (full, argument_contract(full))}
    answer = read_tool_arguments({"tool": "tool_abc_begin_cast", "value": "magic_stone"}, tools)
    assert answer["status"] == "ok" and answer["spell"] == "fire_bolt"
    assert set(answer["arguments"]["properties"]) == {"target", "fire_bolt_object_facts"}
    assert read_tool_arguments({"tool": "begin_cast", "value": "bless"}, tools)["status"] == "ok"
    # Claude Code lists Sylvia's tools with its MCP server prefix; models pass that name.
    prefixed = {"tool": "mcp__sylvia__tool_abc_begin_cast", "value": "bless"}
    assert read_tool_arguments(prefixed, tools)["status"] == "ok"
    assert read_tool_arguments({"tool": "other", "value": "bless"}, tools)["code"] == (
        "argument_contract_tool_unavailable")
    assert read_tool_arguments({"tool": "begin_cast", "value": "wish"}, tools)["code"] == (
        "argument_contract_value_unknown")


def test_inputs_the_projection_hides_are_dropped_from_contracts():
    full = schema()
    full[CONTRACT_KEY]["by_value"]["bless"].append("lasting_state")
    assert argument_contract(full).selected("bless") == frozenset({"targets"})


def test_envelope_keeps_only_referenced_local_definitions_and_reads_inline_them():
    full = schema()
    point = {"type": "array", "items": {"type": "number"}, "minItems": 3, "maxItems": 3}
    full["$defs"] = {"p0": point, "p1": {"type": "string"}}
    full["properties"]["caster"] = {"$ref": "#/$defs/p1"}
    full["properties"]["target"] = {"$ref": "#/$defs/p0"}
    contract = argument_contract(full)
    envelope = envelope_schema(full, contract)
    assert envelope["$defs"] == {"p1": {"type": "string"}}
    assert selected_schema(full, contract, "fire_bolt")["properties"]["target"] == point


@pytest.mark.parametrize("change", [
    lambda c: c.update(version=2),
    lambda c: c.update(selector="missing"),
    lambda c: c.update(field="caster"),
    lambda c: c["by_value"].update(bless="targets"),
    lambda c: c.update(common="caster"),
])
def test_malformed_contracts_are_package_defects(change):
    full = schema()
    change(full[CONTRACT_KEY])
    with pytest.raises(ValueError):
        argument_contract(full)


# -- the shipped SRD Lite casting tools, through a real scripted GM session ------------------

CASTING_TOOLS = ("begin_srd_spell_action", "cast_spell_outside_combat")


def _shipped_record():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    return json.loads((root / "rulesets/srd-lite/package-record.json").read_bytes())


def test_shipped_casting_tools_declare_complete_contracts():
    record = _shipped_record()
    tools = {tool["name"]: tool for tool in record["tools"]}
    for name in CASTING_TOOLS:
        schema = tools[name]["input_schema"]
        contract = argument_contract(schema)
        assert contract is not None and contract.selector == "spell"
        declared = set(schema["properties"]["spell"]["enum"])
        assert declared <= set(contract.by_value), sorted(declared - set(contract.by_value))
        assert "caster_state" in contract.common and "slot_resource" in contract.common


def test_shipped_contracts_match_the_runtime_they_were_derived_from():
    from pathlib import Path

    import yaml
    from scripts.update_srd_argument_contracts import runtime_digest

    root = Path(__file__).resolve().parents[1]
    runtime = yaml.load((root / "rulesets/srd-lite/runtime/ruleset.yaml").read_bytes()
                        .decode("utf-8"), Loader=yaml.CSafeLoader)
    digest = runtime_digest(runtime)
    tools = {tool["name"]: tool for tool in _shipped_record()["tools"]}
    for name in CASTING_TOOLS:
        stamped = tools[name]["input_schema"][CONTRACT_KEY]["derived_from_sha256"]
        assert stamped == digest, (
            "The SRD Lite runtime changed after its argument contracts were derived; run "
            "python -m scripts.update_srd_argument_contracts --version <the new version>")


def _nested(arguments):
    """Arguments split the way the offered envelope asks: its own inputs on top, the rest nested."""
    def split(tools):
        name = next(t["function"]["name"] for t in tools
                    if t["function"]["name"].endswith("_begin_srd_spell_action"))
        envelope = next(t for t in tools if t["function"]["name"] == name)
        top = set(envelope["function"]["parameters"]["properties"]) - {"spell_arguments"}
        return {**{k: v for k, v in arguments.items() if k in top},
                "spell_arguments": {k: v for k, v in arguments.items() if k not in top}}
    return split


def test_scripted_session_casts_through_the_envelope(tmp_path, monkeypatch):
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter, _plain_json
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import WriterRole
    from tests.test_gm_context_contract import ContractGM, settle
    from tests.test_srd_public_spell_casting import SRD, _create_spell_campaign

    class GM(ContractGM):
        def __init__(self):
            super().__init__()
            self.definitions = []
            self.results = []

        async def generate_stream(self, messages, tools=None, **kwargs):
            if self.position < len(self.steps) and callable(self.steps[self.position][1]):
                kind, make = self.steps[self.position]
                self.steps[self.position] = kind, make(tools)
            self.definitions.append(copy.deepcopy(tools))
            self.results.extend(json.loads(m["content"]) for m in messages
                                if m.get("role") == "tool" and m.get("content", "").startswith("{"))
            async for chunk in super().generate_stream(messages, tools, **kwargs):
                yield chunk

    monkeypatch.delenv("SYLVIA_GM_TOOL_LIST", raising=False)
    gm = GM()
    owner = AuthorityOwner.open(tmp_path / "session.db", "contract-owner",
                                disconnect_grace_seconds=1.0)
    try:
        _state, _data, parameters = _create_spell_campaign(owner, "summon_dragon")
        owner.create_agent_session(campaign_id="casting", session_id="contract-session",
                                   role=WriterRole.DM, provider_id="test.transport",
                                   model_id="test.transport/model0")
        scene = _plain_json(owner.snapshot("casting").state[f"{SRD}.state"]["spatial_scene"])
        scene["zones"] = [{"id": "daylight", "map_id": "fixture",
                           "shape": {"kind": "box", "min": [-100, -100, -10],
                                     "max": [100, 100, 20]},
                           "metadata": {"lighting": "bright", "plane": "material"}}]
        summon = dict(parameters)
        summon.pop("target")
        summon.update(summon_id="dragon_one", summon_distance=30,
                      caster_can_see_summon_space=True, summon_space_unoccupied=True,
                      selected_damage_type="fire", created_actor_position=[0, 30, 0],
                      created_actor_height=6, created_actor_lighting="bright")

        def completion(_tools):
            pending = owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_action_stack"]
            return {"pending_action_id": pending[-1]}

        gm.steps.extend([
            ("start_group_combat_encounter",
             {"combatants": ["hero", "guard", "other"], "combat_scenes": [scene]}),
            ("read_tool_arguments", {"tool": "begin_srd_spell_action", "value": "summon_dragon"}),
            # An input the tool does not have is refused, with the contract, before anything.
            ("begin_srd_spell_action", lambda tools: {
                **_nested(summon)(tools),
                "spell_arguments": {**_nested(summon)(tools)["spell_arguments"],
                                    "summon_size": "huge"}}),
            ("begin_srd_spell_action", _nested(summon)),
            ("complete_srd_pending_action", completion),
            ("end_combat_encounter", {"end_reason": "fighting_stopped"}),
            settle("The summoned dragon remains as the practice fight ends."),
        ])
        result = CampaignSessionLoop(owner).run(SessionRunRequest(
            session_id="contract-session", turn_id="casting-turn", attempt_id="t1",
            message_id="t1", user_text="Begin a practice fight, summon a dragon, then stop.",
        ), ProviderSessionAdapter(owner, gm, owner_id="contract-owner").for_turn("casting-turn"))
        assert result.interruption is None, (result.interruption, gm.failure)
        assert gm.position == len(gm.steps)
        entity = owner.snapshot("casting").state[f"{SRD}.entities"]["dragon_one"]
        assert entity["summon_active"] is True
    finally:
        owner.close()
    read = next(r for r in gm.results if r.get("arguments_field") == "spell_arguments")
    assert read["spell"] == "summon_dragon" and "summon_id" in read["arguments"]["properties"]
    refused = next(r for r in gm.results if r.get("code") == "selected_arguments_invalid")
    assert refused["validation"]["issues"] == [
        {"path": "spell_arguments.summon_size", "kind": "unknown_input"}]
    assert "summon_id" in refused["selected_arguments"]["properties"]
    for tools in gm.definitions:
        for tool in tools:
            if tool["function"]["name"].endswith(CASTING_TOOLS):
                size = len(encoded(tool).encode("utf-8"))
                assert size < 16_000, (tool["function"]["name"], size)


@pytest.fixture(scope="module")
def loader_seed(tmp_path_factory):
    """The SRD spell-casting campaign with a summon, on the loader's Chat Completions route."""
    from sylvia_gm.application.authority import AuthorityOwner
    from sylvia_gm.domain.authority import WriterRole
    from tests.test_srd_public_spell_casting import _create_spell_campaign

    database = tmp_path_factory.mktemp("contract-loader-seed") / "seed.db"
    with AuthorityOwner.open(database, "loader-seed") as owner:
        _state, _data, parameters = _create_spell_campaign(owner, "summon_dragon")
        owner.create_agent_session(campaign_id="casting", session_id="fixed-session",
                                   role=WriterRole.DM, provider_id="openai_compatible",
                                   model_id="openai_compatible/qwen3-32b")
    return database, parameters


def test_loader_mode_routes_mechanic_reads_to_the_loader_and_spell_reads_to_the_contract(
    loader_seed, tmp_path, monkeypatch,
):
    """With Sylvia's tool loader, a mechanic's first read goes to the loader (its definition,
    plus the spell's own inputs when a spell is named); a spell read once it is defined goes
    to the argument-contract reader; the cast then runs through spell_arguments."""
    import shutil

    from sylvia_gm.agent._provider_session import ProviderSessionAdapter, _plain_json
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
    from sylvia_gm.application.authority import AuthorityOwner
    from tests.test_gm_context_contract import settle
    from tests.test_gm_tool_loader import _session_gm
    from tests.test_srd_public_spell_casting import SRD

    monkeypatch.setenv("SYLVIA_GM_CONTEXT_MODE", "ledger")
    monkeypatch.setenv("SYLVIA_GM_TOOL_LIST", "deferred")
    database = tmp_path / "session.db"
    shutil.copy2(loader_seed[0], database)
    gm = _session_gm()
    owner = AuthorityOwner.open(database, "loader-owner", disconnect_grace_seconds=1.0)
    outcomes: list[tuple[str, dict]] = []
    try:
        scene = _plain_json(owner.snapshot("casting").state[f"{SRD}.state"]["spatial_scene"])
        scene["zones"] = [{"id": "daylight", "map_id": "fixture",
                           "shape": {"kind": "box", "min": [-100, -100, -10],
                                     "max": [100, 100, 20]},
                           "metadata": {"lighting": "bright", "plane": "material"}}]
        summon = dict(loader_seed[1])
        summon.pop("target")
        summon.update(summon_id="dragon_one", summon_distance=30,
                      caster_can_see_summon_space=True, summon_space_unoccupied=True,
                      selected_damage_type="fire", created_actor_position=[0, 30, 0],
                      created_actor_height=6, created_actor_lighting="bright")
        own = {"summon_id", "selected_damage_type", "created_actor_position",
               "created_actor_height", "created_actor_lighting"}
        nested = {**{k: v for k, v in summon.items() if k not in own},
                  "spell_arguments": {k: summon[k] for k in own}}

        def completion():
            pending = owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_action_stack"]
            return {"pending_action_id": pending[-1]}

        gm.steps.extend([
            ("start_group_combat_encounter",
             {"combatants": ["hero", "guard", "other"], "combat_scenes": [scene]}),
            ("raw", ("read_tool_arguments", json.dumps(
                {"tool": "begin_srd_spell_action", "value": "summon_dragon"}))),
            ("raw", ("read_tool_arguments", json.dumps(
                {"tool": "begin_srd_spell_action", "value": "summon_dragon"}))),
            ("begin_srd_spell_action", nested),
            ("complete_srd_pending_action", completion),
            ("end_combat_encounter", {"end_reason": "fighting_stopped"}),
            settle("The summoned dragon remains as the practice fight ends."),
        ])
        result = CampaignSessionLoop(owner).run(SessionRunRequest(
            session_id="fixed-session", turn_id="casting-turn", attempt_id="t1",
            message_id="t1", user_text="Begin a practice fight, summon a dragon, then stop.",
        ), ProviderSessionAdapter(owner, gm, owner_id="loader-owner").for_turn("casting-turn"))
        assert result.interruption is None, (result.interruption, gm.failure)
        for message in owner.session_context("fixed-session").messages:
            evidence = message.content
            if evidence.get("kind") == "provider_tool_results":
                outcomes.extend((row["name"], json.loads(row["content"]))
                                for row in evidence["results"])
        assert owner.snapshot("casting").state[f"{SRD}.entities"]["dragon_one"]["summon_active"]
    finally:
        owner.close()
    reads = [outcome for name, outcome in outcomes if name == "read_tool_arguments"]
    assert len(reads) == 2
    first, second = reads
    # The loader's read: the envelope's definition and the spell's own inputs together.
    assert first["mechanic_definition"] == "begin_srd_spell_action"
    assert "spell summon_dragon, inside spell_arguments: {" in first["definition"]
    assert "summon_id?: " in first["definition"]
    # Once defined, a spell read is the contract's: the exact selected inputs.
    assert "mechanic_definition" not in second
    assert second["status"] == "ok" and second["arguments_field"] == "spell_arguments"
    assert "summon_id" in second["arguments"]["properties"]
    casts = [outcome.get("status") for name, outcome in outcomes
             if name.endswith("begin_srd_spell_action")]
    assert casts == ["action_committed"]
