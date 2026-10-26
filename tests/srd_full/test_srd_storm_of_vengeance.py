"""The chosen cloud, paid source, appearance save and original condition lifetime are real."""

from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts import srd_storm_of_vengeance as storm
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_retained_spell_attacks import _lists

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_delayed_blast_fireball as base
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_sunbeam import Rolls

_BASE_RUNTIME = base._runtime


@lru_cache
def _runtime():
    parent = deepcopy(_BASE_RUNTIME()[0])
    runtime = deepcopy(parent)
    runtime["definitions"]["spells"][storm.SPELL] = deepcopy(storm.PROFILE)
    runtime["definition_row_schemas"]["definitions.spells"]["variants"][storm.SPELL] = (
        storm.schema()
    )
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_action",
        "begin_srd_spell_cast_internal",
    ):
        next(p for p in runtime["tools"][name]["parameters"] if p["name"] == "spell")[
            "enum_values"
        ].append(storm.SPELL)
    for name in ("cast_spell", "cast_spell_outside_combat"):
        for sequence in _lists(runtime["tools"][name]["effects"]):
            for effect in sequence:
                if effect.get("set", {}).get("field") == "_spell_time_span_ids":
                    effect["set"]["value"].append("'" + storm.SPELL + "'")
    encoded = storm.bind_runtime_consumers(
        yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    )
    runtime = yaml.load(encoded, Loader=yaml.CSafeLoader)
    assert runtime["tools"]["d20_test"] == parent["tools"]["d20_test"]
    assert (
        runtime["tools"]["resolve_spell_area_save_damage_target"]
        == parent["tools"]["resolve_spell_area_save_damage_target"]
    )
    return runtime, encoded.encode()


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
    runtime, _ = _runtime()
    monkeypatch.setattr(base, "_runtime", _runtime)
    monkeypatch.setattr(base.budget, "_mechanic", _mechanic)
    monkeypatch.setattr(base.cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())
    actual = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


def _case():
    state, data, params = base._source_case(storm.SPELL)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for row in scene["entities"]:
        row["metadata"]["plane"] = "material"
        if row["id"] == "hero":
            row["position"] = [0, 0, 0]
        elif row["id"] == "guard":
            row["position"] = [30, 0, 0]
        else:
            row["position"] = [40, 2, 0]
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(creature_type="humanoid", turns_started=5)
    for name, modifier in (("guard", -30), ("other", 30)):
        entities[name].update(hp=1000, max_hp=1000, temp_hp=0, creature_type="humanoid")
        entities[name]["saving_throw_modifiers"]["constitution"] = modifier
    params.update(storm_cloud_point=[320, 5, 50])
    return state, data, params


def _sources(state):
    return [row for row in state[f"{SRD}.state"]["turn_effects"] if row.get("kind") == storm.SPELL]


def _cast(state, data, params):
    return _run("cast_spell_outside_combat", state, participant_data=data, **params).result


def test_paid_druid_source_thunder_failure_deafened_success_none_and_original_lifetime(monkeypatch):
    state, data, params = _case()
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    before = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"]
    result = _cast(state, data, params)
    source = _sources(state)[0]
    assert (
        source["source_id"]
        == source["cast_id"]
        == state[f"{SRD}.entities"]["hero"]["concentration_cast_id"]
    )
    assert source["caster"] == "hero" and source["origin"] == [320, 5, 50]
    assert source["plane"] == "material" and source["radius"] == 300 and source["slot_level"] == 9
    assert source["storm_ordinal"] == 1 and source["last_caster_start"] == 5
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == before - 1
    assert rolls.sides == [6, 6, 20, 20]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 994
    assert state[f"{SRD}.entities"]["other"]["hp"] == 1000
    assert "deafened" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert "deafened" not in state[f"{SRD}.entities"]["other"]["conditions"]
    condition = state[f"{SRD}.state"]["timed_condition_sources"][
        state[f"{SRD}.state"]["storm_deafened_source_ids"][0]
    ]
    assert condition["storm_source_id"] == source["source_id"]
    assert condition["expires_tick"] == source["expires_tick"]
    assert condition["repeat_save_timing"] == "none"
    assert result["result_card"] and "Storm of Vengeance" in str(result["result_card"])


def test_actual_disk_box_boundary_altitude_and_source_plane_exclude_unrelated_creatures():
    state, data, params = _case()
    rows = {r["id"]: r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]}
    rows["guard"]["position"] = [320 + 300 + rows["guard"]["footprint"]["half_extents"][0], 5, 0]
    rows["other"]["position"] = [320, 50, 50]
    _cast(state, data, params)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 994
    assert state[f"{SRD}.entities"]["other"]["hp"] == 1000
    state, data, params = _case()
    rows = {r["id"]: r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]}
    rows["guard"]["position"] = [
        320 + 300.001 + rows["guard"]["footprint"]["half_extents"][0],
        5,
        0,
    ]
    rows["other"]["metadata"]["plane"] = "ethereal"
    _cast(state, data, params)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert state[f"{SRD}.entities"]["other"]["hp"] == 1000


def test_genuine_caster_start_ordinal_once_other_actor_and_elapsed_rounds_do_not_count(monkeypatch):
    state, data, params = _case()
    _cast(state, data, params)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _sources(state)[0]["storm_ordinal"] == 1
    state[f"{SRD}.combat"]["encounter_active"] = True
    for actor, phase, turn in (
        ("guard", "start", 6),
        ("hero", "end", 6),
        ("hero", "start", 6),
        ("hero", "start", 6),
    ):
        state[f"{SRD}.entities"][actor]["turns_started"] = turn
        _run(
            "process_srd_turn_effects",
            state,
            lasting_state="state",
            boundary_actor=actor,
            boundary_phase=phase,
            boundary_turn=turn,
        )
    source = _sources(state)[0]
    assert source["storm_ordinal"] == 2 and source["last_caster_start"] == 6
    assert source["later_effect_pending"] is True and rolls.sides == []


def test_creating_caster_down_during_real_appearance_leaves_no_spell_deafened(monkeypatch):
    state, data, params = _case()
    state[f"{SRD}.entities"]["hero"]["hp"] = 1
    # The native area owner reads prepared saves; Druid has no CON save proficiency.
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = {
        "constitution": (data["abilities"]["constitution"] - 10) // 2,
    }
    monkeypatch.setattr(dice, "_rng", Rolls(save=1))
    _cast(state, data, params | {"storm_cloud_point": [0, 0, 50]})
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 0
    assert _sources(state) == []
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 994
    assert "deafened" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert not any(
        row.get("installed") and row.get("spell_id") == storm.SPELL
        for row in state[f"{SRD}.state"].get("timed_condition_sources", {}).values()
    )


@pytest.mark.parametrize("ending", ["concentration", "expiry"])
def test_exact_end_retires_only_linked_deafened_and_preserves_native_source(ending):
    state, data, params = _case()
    state[f"{SRD}.entities"]["guard"]["conditions"] = ["deafened"]
    _cast(state, data, params)
    source_id = _sources(state)[0]["source_id"]
    if ending == "concentration":
        _run("end_concentration", state, caster_state="hero")
    else:
        _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert _sources(state) == [] and state[f"{SRD}.state"]["has_storm_sources"] is False
    assert "deafened" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["storm_deafened_source_ids"] == []
    assert not any(
        row.get("installed") and row.get("storm_source_id") == source_id
        for row in state[f"{SRD}.state"]["timed_condition_sources"].values()
    )


def test_missing_cloud_altitude_and_actual_one_mile_range_reject_atomically_and_emission_repeats():
    state, data, params = _case()
    before = deepcopy(state)
    for point in ([], [6000, 5, 50]):
        with pytest.raises(RulesetPreconditionRejected):
            _cast(state, data, params | {"storm_cloud_point": point})
        assert state == before
    runtime, encoded = _runtime()
    assert (
        yaml.load(storm.bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader)
        == runtime
    )


def test_paid_archived_cloud_and_condition_reopen_exact_cast_and_end_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(base.public_casting, "bundled_srd_archive", base._archive)
    monkeypatch.setattr(base.public_casting, "_spell_case", lambda _spell: _case())
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    measured = []
    actual = ExecutionBudget.consume_operation

    def count(budget):
        if not measured or measured[-1] is not budget:
            measured.append(budget)
        actual(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    database, receipts = tmp_path / "storm.db", []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = base.public_casting._create_spell_campaign(
            owner, storm.SPELL, target_overrides={"hp": 1000, "max_hp": 1000}
        )
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        saved, faces = owner.snapshot("casting"), deepcopy(rolls.sides)
        assert _sources(saved.state)[0]["origin"] == [320, 5, 50]
        assert "deafened" in saved.state[f"{SRD}.entities"]["guard"]["conditions"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(_act(owner, "end_concentration", caster_state="hero"))
        saved = owner.snapshot("casting")
        assert _sources(saved.state) == []
        assert "deafened" not in saved.state[f"{SRD}.entities"]["guard"]["conditions"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces
    assert all(b.max_operations == 10000 for b in measured)
    print("Storm appearance/public max operations:", max(b.operations for b in measured))
