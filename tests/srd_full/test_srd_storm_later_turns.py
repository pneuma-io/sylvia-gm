"""Earned caster starts, shared rolls and native packets are authentic later Storm effects."""

from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts import srd_storm_later_turns as later
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_storm_of_vengeance as base
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_sunbeam import Rolls

_BASE_RUNTIME = base._runtime


@lru_cache
def _runtime():
    parent = _BASE_RUNTIME()[0]
    encoded = later.bind_runtime_consumers(
        yaml.dump(parent, Dumper=yaml.CSafeDumper, sort_keys=False)
    )
    runtime = yaml.load(encoded, Loader=yaml.CSafeLoader)
    for name in ("d20_test", "apply_srd_qualifying_damage", "modify_srd_actor_damage_roll"):
        assert runtime["tools"][name] == parent["tools"][name]
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
    monkeypatch.setattr(base.base, "_runtime", _runtime)
    monkeypatch.setattr(base.base.budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        base.base.cards, "_spell_definitions", lambda: runtime["definitions"]["spells"]
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())
    actual = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


def _start(state, data, turn):
    state[f"{SRD}.combat"]["encounter_active"] = True
    state[f"{SRD}.entities"]["hero"]["turns_started"] = turn
    return _run(
        "process_srd_turn_effects",
        state,
        participant_data=data,
        lasting_state="state",
        boundary_actor="hero",
        boundary_phase="start",
        boundary_turn=turn,
    ).result


def test_actual_starts_no_save_acid_bludgeoning_cold_once_stage_three_remains_pending(monkeypatch):
    state, data, params = base._case()
    base._cast(state, data, params)
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    _start(state, data, 6)
    record = base._sources(state)[0]
    assert record["storm_last_stage_result"]["raw_roll"]["results"] == [3] * 4
    assert record["last_resolved_ordinal"] == 2 and record["later_effect_pending"] is False
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 982
    assert state[f"{SRD}.entities"]["other"]["hp"] == 988
    before = deepcopy(state)
    _start(state, data, 6)
    assert state == before and rolls.sides == [6] * 4
    _start(state, data, 7)
    assert base._sources(state)[0]["storm_ordinal"] == 3
    assert base._sources(state)[0]["later_effect_pending"] is True
    assert rolls.sides == [6] * 4
    _start(state, data, 8)
    _start(state, data, 9)
    assert rolls.sides == [6] * 7
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 973
    assert state[f"{SRD}.entities"]["other"]["hp"] == 979
    assert base._sources(state)[0]["storm_last_stage_result"]["damage_type"] == "cold"


def test_later_stage_uses_current_body_plane_and_typed_temp_hp_receipt(monkeypatch):
    state, data, params = base._case()
    base._cast(state, data, params)
    actor = state[f"{SRD}.entities"]
    actor["guard"].update(temp_hp=3, damage_resistances=["acid"])
    actor["other"]["damage_immunities"] = ["acid"]
    _start(state, data, 6)
    actor = state[f"{SRD}.entities"]
    assert actor["guard"]["temp_hp"] == 0 and actor["guard"]["hp"] == 991
    assert actor["other"]["hp"] == 1000
    rows = {r["id"]: r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]}
    rows["guard"]["position"] = [700, 0, 0]
    rows["other"]["metadata"]["plane"] = "ethereal"
    _start(state, data, 7)
    _start(state, data, 8)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 991
    assert state[f"{SRD}.entities"]["other"]["hp"] == 1000
    assert base._sources(state)[0]["storm_last_stage_result"]["targets"] == []


def test_paid_ray_penalty_is_one_shared_die_for_actual_no_save_recipients(monkeypatch):
    from tests.srd_full.test_srd_flame_blade_objects import _enfeeble

    state, data, params = base._case()
    base._cast(state, data, params)
    _enfeeble(state, data, position=(80, 0, 0))

    class RayRolls(Rolls):
        def randint(self, low, high):
            if high == 8:
                self.sides.append(high)
                return 1
            return super().randint(low, high)

    rolls = RayRolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    _start(state, data, 6)
    # The actual Ray caster is also under the cloud and makes its genuine concentration save.
    assert rolls.sides == [6] * 4 + [8, 20]
    receipt = base._sources(state)[0]["storm_last_stage_result"]
    assert receipt["raw_roll"]["results"] == [3] * 4 and receipt["damage_total"] == 11
    assert receipt["remaining_penalty"] == 0
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 983
    assert state[f"{SRD}.entities"]["other"]["hp"] == 989


def test_real_caster_damage_ends_source_without_restoring_claimed_row(monkeypatch):
    state, data, params = base._case()
    base._cast(state, data, params)
    row = next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "hero")
    row["position"] = [320, 150, 0]
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=1, temp_hp=0, saving_throw_modifiers={"constitution": 0})
    _start(state, data, 6)
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 0
    assert base._sources(state) == []
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 982
    assert "deafened" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_emission_repeat_and_native_roll_packet_owners_are_unchanged():
    runtime, encoded = _runtime()
    assert (
        yaml.load(later.bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader)
        == runtime
    )
    parent = _BASE_RUNTIME()[0]
    for name in ("d20_test", "apply_srd_qualifying_damage", "modify_srd_actor_damage_roll"):
        assert runtime["tools"][name] == parent["tools"][name]


def test_paid_public_turn_stage_reopens_and_every_retry_preserves_one_roll(tmp_path, monkeypatch):
    monkeypatch.setattr(base.base.public_casting, "bundled_srd_archive", base.base._archive)
    monkeypatch.setattr(base.base.public_casting, "_spell_case", lambda _spell: base._case())
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    measured = []
    actual = ExecutionBudget.consume_operation

    def count(budget):
        if not measured or measured[-1] is not budget:
            measured.append(budget)
        actual(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    database, receipts = tmp_path / "storm-turn.db", []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = base.base.public_casting._create_spell_campaign(
            owner, base.storm.SPELL, target_overrides={"hp": 1000, "max_hp": 1000}
        )
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
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
        receipts.append(_act(owner, "begin_combat_turn"))
        saved, faces = owner.snapshot("casting"), deepcopy(rolls.sides)
        assert base._sources(saved.state)[0]["last_resolved_ordinal"] == 2
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 982
        assert "acid" in str(receipts[-1][0].result["result_card"])
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces
    assert all(b.max_operations == 10000 for b in measured)
    print("Storm later/public max operations:", max(b.operations for b in measured))
