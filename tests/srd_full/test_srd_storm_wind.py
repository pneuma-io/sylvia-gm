"""Storm wind ends only genuine entirely covered saved Fog through its native source owner."""

from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts import srd_storm_wind as wind
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_storm_conditions as base
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_sunbeam import Rolls

_BASE_RUNTIME = base._runtime


@lru_cache
def _runtime():
    encoded = wind.bind_runtime_consumers(_BASE_RUNTIME()[1].decode())
    return yaml.load(encoded, Loader=yaml.CSafeLoader), encoded.encode()


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
            input_schema=_tool_schema(runtime["tools"][name], tool_name=name),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(base.base.base.base, "_runtime", _runtime)
    monkeypatch.setattr(base.base.base.base.budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        base.base.base.base.cards,
        "_spell_definitions",
        lambda: _runtime()[0]["definitions"]["spells"],
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())


def _preparation():
    return {
        "creature_id": "fog_mage",
        "name": "Fog Guard",
        "source": "Synthetic caster: Wisdom +3, DC15, CON+30, one level1 Fog Cloud.",
        "armor_class": 12,
        "hit_points": 1000,
        "speed": 30,
        "initiative_modifier": 0,
        "size_rank": 2,
        "constitution_save": 30,
        "spellcasting_ability": "wisdom",
        "spellcasting_modifier": 3,
        "spell_attack_bonus": 7,
        "spell_save_dc": 15,
        "spells": [{"spell": "fog_cloud", "uses_per_day": 1, "casting_level": 1}],
    }


def _fog_params(origin=(60, 50, 25)):
    _, _, params = base.base.base.base._source_case("fog_cloud")
    params.pop("caster")
    params.pop("slot_resource")
    params.update(caster_state="fog_mage", environment_origin=list(origin))
    return params


def _initial_case(plane="material"):
    state, data, params = base.base.base._case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    caster = deepcopy(next(row for row in scene["entities"] if row["id"] == "guard"))
    caster["id"], caster["position"] = "fog_mage", [30, 50, 0]
    caster["metadata"].update(plane=plane, participant_ref="fog_mage", creature_type="humanoid")
    scene["entities"].append(caster)
    return state, data, params


def _case(origin=(60, 50, 25), plane="material"):
    state, data, params = _initial_case(plane)
    _run("prepare_srd_creature", state, **_preparation())
    _run("cast_spell_outside_combat", state, participant_data=data, **_fog_params(origin))
    area_id = state[f"{SRD}.entities"]["fog_mage"]["environment_area_id"]
    assert state[f"{SRD}.entities"]["fog_mage"]["spell_uses_remaining"]["fog_cloud"] == 0
    base.base.base._cast(state, data, params)
    for turn in range(6, 9):
        base.base._start(state, data, turn)
    return state, data, area_id


def test_paid_other_caster_fog_survives_stage_four_then_real_stage_five_exactly_retires():
    state, data, area_id = _case()
    namespace = state[f"{SRD}.state"]
    assert namespace["persistent_spell_areas"][area_id]["active"] is True
    cloud_id = base.base.base._sources(state)[0]["source_id"]
    base.base._start(state, data, 9)
    namespace = state[f"{SRD}.state"]
    probe = _run(
        wind.QUERY, state, area_id=area_id, caster_state="fog_mage", plane="material"
    ).result["context"]
    assert probe["_storm_fog_full_wind"] is False
    assert namespace["persistent_spell_areas"][area_id]["active"] is False
    assert state[f"{SRD}.entities"]["fog_mage"]["concentration_spell_id"] is None
    assert base.base.base._sources(state)[0]["source_id"] == cloud_id
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "storm_of_vengeance"
    before = deepcopy(state)
    base.base._start(state, data, 9)
    assert state == before


@pytest.mark.parametrize(
    "origin,plane",
    [((25, 50, 25), "material"), ((60, 50, 25), "ethereal")],
)
def test_partial_or_other_plane_fog_is_not_retired_from_one_origin_point(origin, plane):
    state, data, area_id = _case(origin, plane)
    base.base._start(state, data, 9)
    assert state[f"{SRD}.state"]["persistent_spell_areas"][area_id]["active"] is True
    assert state[f"{SRD}.entities"]["fog_mage"]["concentration_spell_id"] == "fog_cloud"
    before = deepcopy(state)
    result = _run(wind.QUERY, state, area_id=area_id, caster_state="fog_mage", plane=plane).result[
        "context"
    ]
    assert result["_storm_fog_full_wind"] is False and state == before


def test_repeat_preserves_native_d20_damage_and_actual_fog_retirement_body():
    runtime, encoded = _runtime()
    assert (
        yaml.load(wind.bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader) == runtime
    )
    parent = _BASE_RUNTIME()[0]
    for name in (
        "d20_test",
        "apply_srd_qualifying_damage",
        "end_srd_concentration_node",
        "remove_srd_environment_concentration",
        "refresh_srd_environment_wind",
    ):
        assert runtime["tools"][name] == parent["tools"][name]


def test_paid_public_two_sources_native_wind_end_reopen_and_all_retries(tmp_path, monkeypatch):
    public = base.base.base.base.public_casting
    monkeypatch.setattr(public, "bundled_srd_archive", base.base.base.base._archive)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: _initial_case())
    rolls, budgets = Rolls(), []
    monkeypatch.setattr(dice, "_rng", rolls)
    consume = ExecutionBudget.consume_operation

    def count(budget):
        if not budgets or budgets[-1] is not budget:
            budgets.append(budget)
        consume(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    database, receipts = tmp_path / "storm-wind.db", []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner, base.base.base.storm.SPELL, target_overrides={"hp": 1000, "max_hp": 1000}
        )
        receipts.append(_act(owner, "prepare_srd_creature", **_preparation()))
        receipts.append(_act(owner, "cast_spell_outside_combat", **_fog_params()))
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        receipts.append(
            _act(
                owner,
                "start_combat_encounter_entities",
                first_actor="hero",
                second_actor="guard",
                first_surprised=False,
                second_surprised=False,
                tie_winner="first",
            )
        )
        for index in range(4):
            receipts.append(_act(owner, "begin_combat_turn"))
            if index < 3:
                for name in (
                    "end_combat_turn",
                    "advance_combat_encounter_turn",
                    "begin_combat_turn",
                    "end_combat_turn",
                    "advance_combat_encounter_turn",
                ):
                    receipts.append(_act(owner, name))
        saved = owner.snapshot("casting")
        fog = saved.state[f"{SRD}.entities"]["fog_mage"]
        area_id = next(
            key
            for key, row in saved.state[f"{SRD}.state"]["persistent_spell_areas"].items()
            if row["caster"] == "fog_mage" and row["spell"] == "fog_cloud"
        )
        assert saved.state[f"{SRD}.state"]["persistent_spell_areas"][area_id]["active"] is False
        assert (
            fog["concentration_spell_id"] is None and fog["spell_uses_remaining"]["fog_cloud"] == 0
        )
        assert base.base.base._sources(saved.state)[0]["storm_ordinal"] == 5
        faces = deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED, result.result
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces
    assert all(budget.max_operations == 10000 for budget in budgets)
    print("Storm wind/public max operations:", max(budget.operations for budget in budgets))
