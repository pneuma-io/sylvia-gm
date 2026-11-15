"""Real Magic Zone sources pause the actual recipient and exclude only protected targets."""

from copy import deepcopy
from functools import lru_cache
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_dragon_breath_magic_zones import (
    PURE_CAST,
    PURE_EFFECT,
    PURE_SOURCE,
    bind_runtime_consumers,
)

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_dragon_breath as dragon
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_produce_flame_reactions import _mage_spec
from tests.srd_full.test_srd_spell_wave3_information import _entity


@lru_cache
def _runtime():
    parent, encoded = dragon._runtime()
    text = bind_runtime_consumers(encoded.decode())
    actual = yaml.load(text, Loader=yaml.CSafeLoader)
    assert actual["tools"]["d20_test"] == parent["tools"]["d20_test"]
    assert (
        actual["tools"]["resolve_spell_area_save_damage_target"]
        == (parent["tools"]["resolve_spell_area_save_damage_target"])
    )
    return actual, text.encode()


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
    monkeypatch.setattr(dice, "_rng", dragon.Rolls())
    actual = package_runtime.execute_tool

    def diagnosed(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnosed)


def _case(*, slot=2, zone="antimagic_field", mage_x=6):
    state, data, params = dragon._case(slot)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"].append(
        _entity("mage", [mage_x, 3, 0], participant_ref="mage", creature_type="humanoid")
    )
    material_state, _, zone_params = cards._spell_case(zone)
    materials = material_state[f"{SRD}.entities"]["hero"]["spell_materials"]
    level = _runtime()[0]["definitions"]["spells"][zone]["base_level"]
    spec = _mage_spec() | {
        "spells": [{"spell": zone, "uses_per_day": 1, "casting_level": level}],
        "inventory": [m["inventory_item"] for m in materials],
        "spell_materials": materials,
    }
    _run("prepare_srd_creature", state, **spec)
    zone_params.pop("caster")
    zone_params.pop("slot_resource")
    zone_params.update(caster_state="mage", target="mage", target_distance=0)
    return state, data, params, zone_params


def _zone(state, params):
    _run("cast_spell_outside_combat", state, **params)
    owner = state[f"{SRD}.state"]
    return owner["spell_magic_zone_source_ids"][-1]


def test_genuine_field_pauses_recipient_then_exact_removal_resumes_original_source():
    state, data, params, zone_params = _case()
    dragon._cast(state, data, params)
    source = deepcopy(dragon._sources(state)[0])
    zone_id = _zone(state, zone_params)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="No active paid"):
        dragon._use(state)
    assert state == before and dragon._sources(state) == [source]
    _run("refresh_srd_dragon_breath", state)
    assert dragon._sources(state) == [source]  # suppression must not become retirement
    _run("end_srd_spell_magic_zone_source", state, source_id=zone_id)
    out = dragon._use(state)
    assert out["context"]["_breath_source_id"] == source["source_id"]
    assert state[f"{SRD}.entities"]["victim"]["hp"] == 991
    assert dragon._sources(state) == [source]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2


def test_target_field_excludes_save_and_hp_but_keeps_actual_magic_action_and_source(monkeypatch):
    state, data, params, zone_params = _case(mage_x=24)
    dragon._cast(state, data, params)
    source = deepcopy(dragon._sources(state)[0])
    _zone(state, zone_params)
    rolls = dragon.Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    out = dragon._use(state)
    assert rolls.sides == [6] * 3  # shared game roll, no protected creature save
    assert state[f"{SRD}.entities"]["victim"]["hp"] == 1000
    assert dragon._sources(state) == [source]
    assert state[f"{SRD}.entities"]["guard"]["dragon_breath_used_tick"] == 0
    assert "Excluded" in str(out["result_card"])


def test_caster_inside_field_does_not_suppress_actual_recipient_outside_it():
    state, data, params, zone_params = _case(mage_x=0)
    dragon._cast(state, data, params)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(row for row in scene["entities"] if row["id"] == "victim")["position"] = [40, 0, 0]
    # The existing public walking owner spends the actual recipient's movement.
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    _zone(state, zone_params)
    out = dragon._use(state)
    assert out["context"]["_srd_area_targets"] == ["victim"]
    assert state[f"{SRD}.entities"]["victim"]["hp"] == 991
    assert dragon._sources(state)[0]["caster"] == "hero"


@pytest.mark.parametrize("slot,blocked", [(2, True), (6, False)])
def test_globe_uses_actual_effect_origin_and_retained_slot_per_existing_zone_contract(
    slot, blocked
):
    state, data, params, zone_params = _case(slot=slot, zone="globe_of_invulnerability", mage_x=24)
    dragon._cast(state, data, params)
    _zone(state, zone_params)
    out = dragon._use(state)
    assert out["context"]["_breath_save_dc"] == 18
    assert state[f"{SRD}.entities"]["victim"]["hp"] == (1000 if blocked else 979)
    assert dragon._sources(state)[0]["slot_level"] == slot


def test_pure_qualification_has_no_lifecycle_edge_and_repeated_final_emission_is_exact():
    runtime, encoded = _runtime()
    assert bind_runtime_consumers(encoded.decode()) == encoded.decode()
    for name in (PURE_CAST, PURE_EFFECT, PURE_SOURCE):
        text = str(runtime["tools"][name]["effects"])
        assert "refresh_srd_spell_magic_zones" not in text
        assert "end_srd_spell_magic_zone_source" not in text
        assert "end_srd_concentration" not in text


def test_real_clock_counts_duration_while_the_recipient_is_suppressed():
    state, data, params, zone_params = _case()
    dragon._cast(state, data, params)
    source = deepcopy(dragon._sources(state)[0])
    zone_id = _zone(state, zone_params)
    _run("advance_srd_lasting_time", state, elapsed_rounds=9)
    assert dragon._sources(state) == [source]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="No active paid"):
        dragon._use(state)
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert dragon._sources(state) == []
    assert state[f"{SRD}.state"]["spell_magic_zone_sources"][zone_id]["installed"] is True


def test_current_query_budget_is_bounded_without_changing_the_cap(monkeypatch):
    from sylvia_gm.runtime._types import ExecutionBudget

    state, data, params, zone_params = _case(mage_x=24)
    dragon._cast(state, data, params)
    _zone(state, zone_params)
    actual = ExecutionBudget.consume_operation
    budgets = []

    def count(current):
        if not budgets or budgets[-1] is not current:
            budgets.append(current)
        actual(current)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    dragon._use(state)
    assert all(b.max_operations == 10000 for b in budgets)
    print("Actual active-field Dragon query max operations:", max(b.operations for b in budgets))

    def nodes(value):
        if isinstance(value, dict):
            return 1 + sum(nodes(k) + nodes(v) for k, v in value.items())
        if isinstance(value, list):
            return 1 + sum(map(nodes, value))
        return 1

    print(
        "Dragon Magic Zone binder node delta:", nodes(_runtime()[0]) - nodes(dragon._runtime()[0])
    )


@lru_cache
def _archive():
    actual = _runtime()
    with patch.object(dragon, "_runtime", return_value=actual):
        return dragon._archive()


def _public_case(_spell):
    state, data, params = dragon._case()
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("mage", [6, 3, 0], participant_ref="mage", creature_type="humanoid")
    )
    return state, data, params


def test_actual_archived_source_field_reopen_and_end_restores_same_paid_breath(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _archive)
    monkeypatch.setattr(public_casting, "_spell_case", _public_case)
    database = tmp_path / "dragon-zone.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner, "dragons_breath", target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        paid, _ = _act(owner, "cast_spell_outside_combat", **params)
        assert paid.status is ActionStatus.COMMITTED
        (_, _, _, zone_params) = _case()
        material_state, _, _ = cards._spell_case("antimagic_field")
        materials = material_state[f"{SRD}.entities"]["hero"]["spell_materials"]
        spec = _mage_spec() | {
            "spells": [{"spell": "antimagic_field", "uses_per_day": 1, "casting_level": 8}],
            "inventory": [m["inventory_item"] for m in materials],
            "spell_materials": materials,
        }
        prepared, _ = _act(owner, "prepare_srd_creature", **spec)
        assert prepared.status is ActionStatus.COMMITTED
        field, field_command = _act(owner, "cast_spell_outside_combat", **zone_params)
        assert field.status is ActionStatus.COMMITTED
        suppressed = owner.snapshot("casting")
        source = deepcopy(dragon._sources(suppressed.state)[0])
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == suppressed
        assert owner.execute_action(field_command) == field
        with pytest.raises(MechanicsRejected, match="No active paid"):
            _act(owner, "use_srd_dragon_breath", breather_state="guard")
        assert owner.snapshot("casting") == suppressed
        ended, _ = _act(owner, "end_concentration", caster_state="mage")
        assert ended.status is ActionStatus.COMMITTED
        used, use_command = _act(owner, "use_srd_dragon_breath", breather_state="guard")
        assert used.status is ActionStatus.COMMITTED
        resumed = owner.snapshot("casting")
        assert dragon._sources(resumed.state) == [source]
        assert resumed.state[f"{SRD}.entities"]["victim"]["hp"] == 991
        assert resumed.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        assert owner.execute_action(use_command) == used
        assert owner.snapshot("casting") == resumed
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == resumed
        assert owner.execute_action(use_command) == used
        assert owner.snapshot("casting") == resumed
