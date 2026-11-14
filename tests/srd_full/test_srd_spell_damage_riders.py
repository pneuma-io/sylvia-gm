"""Paid marks retain real source timing and add dice to one owning attack packet."""

import json
from copy import deepcopy
from functools import lru_cache
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_damage_riders import PROFILES, bind_runtime_consumers, schema

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_produce_flame_reactions import _source_runtime
from tests.srd_full.test_srd_spell_wave3_information import _case as _scene_case
from tests.srd_full.test_srd_spell_wave3_information import _entity


@lru_cache
def _runtime():
    parent, encoded = _source_runtime()
    runtime = yaml.load(bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader)
    runtime["definitions"]["spells"].update(deepcopy(PROFILES))
    runtime[("definition_row_schemas")][("definitions.spells")]["variants"][
        ("attack_rider_spell")
    ] = schema()
    for name in (
        "cast_spell",
        ("cast_spell_outside_combat"),
        ("begin_srd_spell_cast_internal"),
        ("begin_srd_spell_action"),
    ):
        parameter = next(p for p in runtime["tools"][name]["parameters"] if p["name"] == "spell")
        parameter["enum_values"] = sorted(set(parameter["enum_values"]) | PROFILES.keys())
    assert runtime["tools"]["d20_test"] == parent["tools"]["d20_test"]
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Random(0))
    original = package_runtime.execute_tool

    def diagnosed(*args, **kwargs):
        result = original(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnosed)


def _case(spell, slot=1):
    state, data, params = _scene_case(spell)
    entities = state[f"{SRD}.entities"]
    entities["guard"].update(hp=100, max_hp=100, temp_hp=0)
    for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        if row["id"] == "guard":
            row["position"] = [10, 0, 0]
        if row["id"] == "orb":
            row["position"] = [200, 200, 0]
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("other", [10, 10, 0], participant_ref="other", creature_type="humanoid")
    )
    params["slot_resource"] = f"spell_slot_{slot}"
    params["rider_ability"] = "strength"
    return state, data, params


def _cast(state, data, params):
    context = _run("cast_spell", state, participant_data=data, **params).result["context"]
    return context["_rider_source_id"]


def _next_turn(state, actor="hero"):
    active = state[f"{SRD}.combat"]["active_actor"]
    _run("end_combat_turn", state, actor=active)
    _run("begin_combat_turn", state, actor=actor)


def _components(state, critical=False, target="guard"):
    return _run(
        ("roll_srd_attack_mark_components"),
        state,
        attacker_state="hero",
        target_state=target,
        critical_hit=critical,
    ).result["context"]


@pytest.mark.parametrize(
    ("spell,slot,rounds,kind"), [("hex", 2, 2400, "necrotic"), (("hunters_mark"), 2, 600, "force")]
)
def test_actual_paid_mark_source_upcast_and_critical_roll_survive_save_reopen(
    spell, slot, rounds, kind
):
    state, data, params = _case(spell, slot)
    source_id = _cast(state, data, params)
    source = state[f"{SRD}.state"]["attack_rider_sources"][source_id]
    assert source["expires_tick"] - source["started_tick"] == 2 * rounds
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False
    state = json.loads(json.dumps(state))
    result = _components(state, critical=True)
    assert result["_rider_components"][0]["damage_type"] == kind
    assert len(result["_rider_damage_rolls"][0]["results"]) == 2
    assert result["_rider_components"][0]["source_id"] == source_id
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 100
    assert _components(state, target="other")["_rider_components"] == []


def test_hex_ability_check_clause_never_becomes_a_saving_throw_penalty():
    state, data, params = _case("hex")
    _cast(state, data, params)
    for kind, ability, expected in [
        (("ability_check"), "strength", True),
        (("saving_throw"), "strength", False),
        (("ability_check"), "dexterity", False),
    ]:
        result = _run(
            ("check_srd_attack_rider_d20"), state, actor_state="guard", kind=kind, ability=ability
        ).result["context"]
        assert result["_rider_check_disadvantage"] is expected


def test_hunter_finding_advantage_requires_actual_quarry_and_relevant_wisdom_check():
    state, data, params = _case("hunters_mark")
    _cast(state, data, params)
    for kind, skill, subject, expected in [
        (("ability_check"), "perception", "guard", True),
        (("ability_check"), "survival", "guard", True),
        (("saving_throw"), "perception", "guard", False),
        (("ability_check"), "insight", "guard", False),
        (("ability_check"), "perception", "other", False),
    ]:
        result = _run(
            ("check_srd_attack_rider_d20"),
            state,
            actor_state="hero",
            kind=kind,
            ability="wisdom",
            skill=skill,
            subject_state_id=subject,
        ).result["context"]
        assert result["_rider_finding_advantage"] is expected


@pytest.mark.parametrize("spell", ["hex", "hunters_mark"])
def test_transfer_uses_recorded_real_zero_hp_and_hex_requires_later_caster_turn(spell):
    state, data, params = _case(spell)
    source_id = _cast(state, data, params)
    _next_turn(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="recorded target reached zero"):
        _run(
            ("transfer_srd_attack_mark"),
            state,
            caster_state="hero",
            target_state="other",
            source_id=source_id,
        )
    assert state == before
    # This is the actual zero-HP observer boundary, not a caller damage/defeat verdict.
    _run(
        ("apply_srd_damage"),
        state,
        target="guard",
        amount=100,
        damage_type="force",
        adjustment=0,
        source_state_id="hero",
    )
    _run("observe_srd_attack_mark_zero", state, target_state="guard")
    original = deepcopy(state[f"{SRD}.state"]["attack_rider_sources"][source_id])
    if spell == "hex":
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match="actual later caster turn"):
            _run(
                ("transfer_srd_attack_mark"),
                state,
                caster_state="hero",
                target_state="other",
                source_id=source_id,
            )
        assert state == before
        _next_turn(state)
    result = _run(
        ("transfer_srd_attack_mark"),
        state,
        caster_state="hero",
        target_state="other",
        source_id=source_id,
    ).result
    source = state[f"{SRD}.state"]["attack_rider_sources"][source_id]
    assert source["target"] == "other" and source["dropped_to_zero"] is False
    assert source["expires_tick"] == original["expires_tick"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
    assert source_id not in state[f"{SRD}.entities"]["guard"]["attack_rider_source_ids"]
    assert result["result_card"]["title"] == "Move spell mark"


def test_concentration_end_and_exact_hex_cure_retire_sources_without_unrelated_conditions():
    state, data, params = _case("hex")
    source_id = _cast(state, data, params)
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["poisoned"]
    _run("remove_srd_hex_curses", state, target_state="guard")
    assert state[f"{SRD}.state"]["attack_rider_sources"][source_id]["installed"] is False
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["guard"]["conditions"] == ["poisoned"]
    assert _components(state)["_rider_components"] == []


def test_repeat_composition_keeps_public_schema_and_all_existing_d20_effects():
    runtime, encoded = _runtime()
    second = yaml.load(bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader)
    assert second == runtime
    assert _tool_schema(second["tools"]["cast_spell"]) == _tool_schema(
        runtime["tools"]["cast_spell"]
    )
