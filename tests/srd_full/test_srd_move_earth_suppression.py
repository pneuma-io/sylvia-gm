"""Actual field sources qualify the whole selected soil volume, independently of caster position."""

from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts.srd_full.srd_move_earth_suppression import QUERY, _query, bind_runtime_consumers
from scripts.srd_full.srd_parameter_declarations import compact_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.runtime._condition_evaluator import evaluate_expression
from sylvia_gm.runtime._types import ExecutionContext
from tests import test_srd_move_earth as earth
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _case as scene_case
from tests.srd_full.test_srd_spell_wave3_information import _entity

EARTH_RUNTIME = earth._runtime


@lru_cache
def _runtime():
    parent, encoded = EARTH_RUNTIME()
    parent = _load_runtime(encoded.decode())
    text = bind_runtime_consumers(encoded.decode())
    assert bind_runtime_consumers(text) == text
    runtime = _load_runtime(text)
    for name in ("prepare_srd_move_earth_change", "complete_srd_move_earth_change"):
        restored = deepcopy(runtime["tools"][name])
        restored["effects"] = [e for e in restored["effects"] if QUERY not in str(e)]
        assert restored == parent["tools"][name]
    assert runtime["tools"]["d20_test"] == parent["tools"]["d20_test"]
    return runtime, compact_runtime(
        yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    ).encode()


@pytest.fixture(autouse=True)
def owners(monkeypatch):
    monkeypatch.setattr(earth, "_runtime", _runtime)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    earth._mechanic.cache_clear()
    earth._archive.cache_clear()
    earth._owners.__wrapped__(monkeypatch)
    yield
    earth._mechanic.cache_clear()
    earth._archive.cache_clear()


def _field_setup():
    field_state, _, params = scene_case("antimagic_field")
    hero = field_state[f"{SRD}.entities"]["hero"]
    materials = [
        {
            key: item[key]
            for key in ("id", "inventory_item", "kind", "components", "quantity", "value_cp")
        }
        for item in hero["spell_materials"]
    ]
    prepare = {
        "creature_id": "field_caster",
        "name": "Admitted field caster",
        "source": (
            "Admitted test caster: Intelligence +4, spell attack +8, DC16, Constitution save +3."
        ),
        "armor_class": 12,
        "hit_points": 50,
        "speed": 30,
        "initiative_modifier": 2,
        "size_rank": 2,
        "constitution_save": 3,
        "spellcasting_ability": "intelligence",
        "spellcasting_modifier": 4,
        "spell_attack_bonus": 8,
        "spell_save_dc": 16,
        "spells": [{"spell": "antimagic_field", "uses_per_day": 1, "casting_level": 8}],
        "inventory": [item["inventory_item"] for item in materials],
        "spell_materials": materials,
    }
    params.pop("caster")
    params.pop("slot_resource")
    params.update(caster_state="field_caster", target="field_caster")
    return prepare, params


def _field_row(position=(62, 0, 0), plane="material"):
    row = _entity(
        "field_caster",
        list(position),
        participant_ref="field_caster",
        creature_type="humanoid",
        plane=plane,
    )
    row.update(footprint={"kind": "box", "half_extents": [2.5, 2.5]}, height=5)
    return row


def _install_field(state, position=(62, 0, 0), plane="material"):
    prepare, params = _field_setup()
    _run("prepare_srd_creature", state, **prepare)
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(_field_row(position, plane))
    _run("cast_spell_outside_combat", state, **params)
    caster = state[f"{SRD}.entities"]["field_caster"]
    assert caster["spell_uses_remaining"]["antimagic_field"] == 0
    return caster["magic_zone_source_id"]


def test_exact_expression_checks_edges_and_height_not_the_terrain_center():
    query = _query()
    loop = next(e["for_each"] for e in query["effects"] if "for_each" in e)
    same_plane = loop["do"][-1]["on_true"][-1]["on_true"]
    distance_sets = same_plane[:-1]
    clear = same_plane[-1]["require"]
    expression = clear["condition"]
    volume = {"kind": "box", "min": [30, -10, -10], "max": [50, 10, 3]}
    for position, expected_clear in (
        ([62, 0, 0], False),
        ([63, 0, 0], True),
        ([40, 0, 13], False),
        ([40, 0, 14], True),
    ):
        context = ExecutionContext(
            params={"volume": volume},
            vars={
                "_magic_zone_body": {
                    "position": position,
                    "footprint": {"half_extents": [2.5, 2.5]},
                    "height": 5,
                },
                "_magic_zone_radius": 10,
            },
        )
        for effect in distance_sets:
            setter = effect["set"]
            context.vars[setter["field"]] = evaluate_expression(setter["value"], context)
        assert evaluate_expression(expression, context) is expected_clear


def test_paid_field_partial_soil_overlap_rejects_earth_atomically_with_caster_outside():
    state, data, params = earth._case()
    source_id = _install_field(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="overlaps the shaping volume"):
        earth._paid(state, data, params)
    assert state == before
    assert state[f"{SRD}.state"]["spell_magic_zone_sources"][source_id]["installed"] is True


def test_other_plane_field_preserves_actual_whole_area_deadline():
    state, data, params = earth._case()
    _install_field(state, plane="ethereal")
    earth._paid(state, data, params)
    earth._clock(state, 200)
    assert earth._terrain(state)["volume"]["max"] == [50, 10, 3]
    assert earth._source(state)["completed_changes"] == 1


def test_public_pending_work_reopens_and_current_field_blocks_the_real_completion(
    tmp_path, monkeypatch
):
    def prepared_case(_spell="move_earth"):
        state, data, params = earth._case()
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(_field_row())
        return state, data, params

    monkeypatch.setattr(public, "bundled_srd_archive", earth._archive)
    monkeypatch.setattr(public, "_spell_case", prepared_case)
    database = tmp_path / "earth-field.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "move_earth")
        paid, original = _act(owner, "cast_spell_outside_combat", **params)
        assert paid.status is ActionStatus.COMMITTED
        prepare, field_params = _field_setup()
        receipt, _ = _act(owner, "prepare_srd_creature", **prepare)
        assert receipt.status is ActionStatus.COMMITTED
        field, field_command = _act(owner, "cast_spell_outside_combat", **field_params)
        assert field.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        field_source = saved.state[f"{SRD}.entities"]["field_caster"]["magic_zone_source_id"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        with pytest.raises(MechanicsRejected, match="overlaps the shaping volume"):
            _act(owner, "advance_srd_lasting_time", elapsed_rounds=100)
        assert owner.snapshot("casting") == saved
        assert earth._terrain(saved.state)["volume"]["max"] == [50, 10, 0]
        assert earth._source(saved.state)["pending"]["due_tick"] == 200
        assert owner.execute_action(original) == paid
        assert owner.execute_action(field_command) == field
        assert owner.snapshot("casting") == saved
        ended, end_command = _act(owner, "end_concentration", caster_state="field_caster")
        assert ended.status is ActionStatus.COMMITTED
        field_ended = owner.snapshot("casting")
        assert (
            field_ended.state[f"{SRD}.state"]["spell_magic_zone_sources"][field_source]["installed"]
            is False
        )
        assert earth._source(field_ended.state)["pending"] == earth._source(saved.state)["pending"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == field_ended
        completed, clock_command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=100)
        assert completed.status is ActionStatus.COMMITTED
        changed = owner.snapshot("casting")
        assert earth._terrain(changed.state)["volume"]["max"] == [50, 10, 3]
        assert earth._source(changed.state)["completed_changes"] == 1
        assert earth._source(changed.state)["pending"] is None
        completed_soil = deepcopy(earth._terrain(changed.state))
        assert owner.execute_action(original) == paid
        assert owner.execute_action(field_command) == field
        assert owner.execute_action(end_command) == ended
        assert owner.execute_action(clock_command) == completed
        assert owner.snapshot("casting") == changed
        earth_ended, _ = _act(owner, "end_concentration", caster_state="hero")
        assert earth_ended.status is ActionStatus.COMMITTED
        _act(owner, "advance_srd_lasting_time", elapsed_rounds=100)
        assert earth._terrain(owner.snapshot("casting").state) == completed_soil
