"""Illumination source owners consume real scene positions and persisted later actions."""

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_illumination import PROFILES, extend_runtime, validate_profiles

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_wave3_information import _case as _information_case
from tests.srd_full.test_srd_spell_wave3_information import _entity, _free

ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def _authored_runtime():
    """Exercise fragments until the combined canonical runtime contains this family."""
    text = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    if (
        set(PROFILES) <= runtime["definitions"]["spells"].keys()
        and "end_srd_invisibility_for_actor" in runtime["tools"]
    ):
        return runtime, text.encode()
    runtime = yaml.load(extend_runtime(text), Loader=yaml.CSafeLoader)
    runtime["definitions"]["spells"].update(PROFILES)
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast_internal",
        "begin_srd_spell_action",
    ):
        next(p for p in runtime["tools"][name]["parameters"] if p["name"] == "spell")[
            "enum_values"
        ] = sorted(runtime["definitions"]["spells"])
    runtime_bytes = yaml.safe_dump(runtime, sort_keys=False).encode()
    return runtime, runtime_bytes


@lru_cache
def _authored_mechanic(name):
    runtime, runtime_bytes = _authored_runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=runtime_bytes,
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
def _authored_owner_runtime(monkeypatch):
    runtime, _ = _authored_runtime()
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", _authored_mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards, "_spell_definitions", lambda: runtime["definitions"]["spells"]
    )


def _case(spell, **choices):
    state, data, params = _information_case(spell)
    _free(state)
    state[f"{SRD}.entities"]["hero"]["speed"] = 150
    data["speed"] = 150
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [5, 0, 0]
    scene["entities"][2]["position"] = [5, 0, 0]
    scene["entities"][2]["metadata"].update(
        size_rank=2, carried_by="", worn_by="", lighting="darkness"
    )
    scene["entities"] += [
        _entity("other", [5, 5, 0], participant_ref="other", creature_type="humanoid")
    ]
    params.update(
        illumination_object="orb",
        illumination_position=[5, 0, 0],
        illumination_points=[[5, 0, 0], [10, 0, 0]],
        illumination_color="blue",
    )
    params.update(choices)
    return state, data, params


def _cast(spell, **choices):
    state, data, params = _case(spell, **choices)
    result = _run("cast_spell", state, participant_data=data, **params)
    return state, data, params, result


def _light(state, point, ambient="darkness"):
    return _run(
        "resolve_srd_information_light", state, point=point, plane="material", ambient=ambient
    ).result["context"]


def _records(state, spell=None):
    return [
        record
        for record in state[f"{SRD}.state"]["turn_effects"]
        if record.get("kind") == "illumination_spell"
        and (spell is None or record["spell"] == spell)
    ]


@pytest.mark.parametrize("spell", PROFILES)
def test_reviewed_leaves_use_admitted_cast_family_and_source_profiles(spell):
    declaration = yaml.safe_load(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/spell-mechanics.yaml").read_text()
    )
    validate_profiles({spell: declaration["spells"][spell]["definition"]})
    bad = deepcopy(PROFILES[spell])
    bad["bright_radius"] += 1
    with pytest.raises(SystemExit, match="source profile"):
        validate_profiles({spell: bad})
    state, _, _, result = _cast(spell)
    assert result.result["status"] == "committed"
    assert _records(state, spell)
    if PROFILES[spell]["concentration"]:
        assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == spell


def test_light_uses_object_size_holder_and_real_touch_and_recast_ends_old_source():
    state, data, params = _case("light")
    object_entry = state[f"{SRD}.state"]["spatial_scene"]["entities"][2]
    for change, match in [({"size_rank": 4}, "Large"), ({"carried_by": "guard"}, "someone else")]:
        object_entry["metadata"].update(change)
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match=match):
            _run("cast_spell", state, participant_data=data, **params)
        assert state == before
        object_entry["metadata"].update(size_rank=2, carried_by="")
    _run("cast_spell", state, participant_data=data, **params)
    assert _light(state, [25, 0, 0])["_information_light"] == "bright"
    assert _light(state, [45, 0, 0])["_information_light"] == "dim"
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("second", [0, 5, 0], is_object=True, size_rank=3)
    )
    _run("cast_spell", state, participant_data=data, **(params | {"illumination_object": "second"}))
    assert len(_records(state, "light")) == 1
    assert _records(state, "light")[0]["source"] == "second"


def test_object_cover_and_carrier_movement_are_consumed_after_reopen():
    state, data, _, _ = _cast("light")
    _run("set_srd_illumination_cover", state, object_id="orb", covered=True)
    assert _light(state, [5, 0, 0])["_information_light"] == "darkness"
    _run("set_srd_illumination_cover", state, object_id="orb", covered=False)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][2]["metadata"]["carried_by"] = "hero"
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[100, 0, 0])
    state = json.loads(json.dumps(state))
    assert _light(state, [100, 0, 0])["_information_light"] == "bright"
    assert _light(state, [5, 0, 0])["_information_light"] == "darkness"
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert _records(state, "light") == []


def test_light_touch_uses_current_carrier_position_and_not_an_unrelated_scalar_target():
    state, data, params = _case("light")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][2]["position"] = [100, 0, 0]
    scene["entities"][2]["metadata"]["carried_by"] = "hero"
    scene["entities"][1]["position"] = [100, 0, 0]
    _run("cast_spell", state, participant_data=data, **params)
    assert _light(state, [0, 0, 0])["_information_light"] == "bright"


def test_continual_flame_is_permanent_priced_consumed_and_can_be_covered():
    state, _, _, result = _cast("continual_flame")
    assert _records(state)[0]["expires_tick"] is None
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"][0]["quantity"] == 2
    _run("advance_srd_lasting_time", state, elapsed_rounds=10000)
    assert _light(state, [25, 0, 0])["_information_light"] == "bright"
    _run("set_srd_illumination_cover", state, object_id="orb", covered=True)
    assert _light(state, [5, 0, 0])["_information_light"] == "darkness"
    assert _records(state, "continual_flame")


@pytest.mark.parametrize("slot_level,dispelled", [(2, True), (3, False)])
def test_darkness_dispels_only_overlapping_eligible_source_spell_levels(slot_level, dispelled):
    state, data, params = _case("continual_flame")
    params["slot_resource"] = f"spell_slot_{slot_level}"
    _run("cast_spell", state, participant_data=data, **params)
    other_state, _, _ = _case("darkness", illumination_object="")
    _run(
        "prepare_srd_creature",
        state,
        creature_id="mage",
        name="Mage",
        source="Synthetic spellcaster",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=2,
        size_rank=2,
        constitution_save=2,
        spellcasting_ability="intelligence",
        spellcasting_modifier=3,
        spell_attack_bonus=5,
        spell_save_dc=15,
        spells=[{"spell": "darkness", "uses_per_day": 1}],
        spell_materials=other_state[f"{SRD}.entities"]["hero"]["spell_materials"],
        inventory=other_state[f"{SRD}.entities"]["hero"]["inventory"],
    )
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("mage", [5, 0, 0], participant_ref="mage", creature_type="humanoid")
    )
    dark_params = params | {
        "caster": None,
        "caster_state": "mage",
        "target": "guard",
        "spell": "darkness",
        "slot_resource": None,
        "illumination_object": "",
        "illumination_position": [20, 0, 0],
    }
    _run("cast_spell", state, **dark_params)
    assert bool(_records(state, "continual_flame")) is not dispelled
    assert _light(state, [20, 0, 0])["_information_light"] == (
        "magical_darkness" if dispelled else "bright"
    )
    state[f"{SRD}.entities"]["hero"]["darkvision_feet"] = 150
    if dispelled:
        sight = _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
        assert sight.result["context"]["_scene_can_see"] is False


@pytest.mark.parametrize("slot_level,dispelled", [(3, True), (4, False)])
def test_daylight_sunlight_and_exact_darkness_dispel_threshold(slot_level, dispelled):
    state, data, params = _case(
        "darkness", illumination_object="", slot_resource=f"spell_slot_{slot_level}"
    )
    _run("cast_spell", state, participant_data=data, **params)
    _, data, _ = _case("daylight")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("daylight")
    params.update(spell="daylight", slot_resource="spell_slot_3", illumination_object="")
    _run("cast_spell", state, participant_data=data, **params)
    assert bool(_records(state, "darkness")) is not dispelled
    light = _light(state, [20, 0, 0])
    assert light["_information_light"] == "bright"
    assert light["_information_sunlight"] is True


def test_dancing_bonus_action_range_pair_separation_and_range_vanish():
    state, _, _, _ = _cast("dancing_lights")
    _run("move_srd_dancing_lights", state, caster_state="hero", points=[[65, 0, 0], [70, 0, 0]])
    assert _light(state, [65, 0, 0])["_information_light"] == "dim"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="20 feet"):
        _run("move_srd_dancing_lights", state, caster_state="hero", points=[[30, 0, 0], [60, 0, 0]])
    assert state == before
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[-100, 0, 0])
    assert _records(state, "dancing_lights") == []


@pytest.mark.parametrize("spell,ends", [("invisibility", True), ("greater_invisibility", False)])
def test_invisible_later_attack_roll_ends_only_ordinary_variant(spell, ends):
    state, _, _, _ = _cast(spell)
    assert "invisible" in state[f"{SRD}.entities"]["guard"]["conditions"]
    state = json.loads(json.dumps(state))
    result = _run(
        "d20_test",
        state,
        actor_state="guard",
        kind="attack_roll",
        ability="strength",
        ability_modifier=2,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
    )
    assert len(result.result["context"]["_d20_test"]["results"]) == 2
    assert ("invisible" in state[f"{SRD}.entities"]["guard"]["conditions"]) is not ends
    _run("advance_srd_lasting_time", state, elapsed_rounds=PROFILES[spell]["effect_rounds"])
    assert "invisible" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_upcast_invisibility_full_target_set_actual_contact_and_native_grant_preserved():
    state, data, params = _case(
        "invisibility", slot_resource="spell_slot_3", targets=["hero", "guard"]
    )
    state[f"{SRD}.entities"]["guard"]["conditions"].append("invisible")
    _run("cast_spell", state, participant_data=data, **params)
    assert "invisible" in state[f"{SRD}.entities"]["hero"]["conditions"]
    _run("end_concentration", state, caster_state="hero")
    assert "invisible" not in state[f"{SRD}.entities"]["hero"]["conditions"]
    assert "invisible" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_faerie_fire_actual_cube_saves_and_seen_target_advantage():
    state, _, _, _ = _cast("faerie_fire", illumination_object="", illumination_position=[1, 0, 0])
    assert not any(record["source"] == "guard" for record in _records(state))
    assert any(record["source"] == "other" for record in _records(state))
    assert any(record["source"] == "orb" for record in _records(state))
    state[f"{SRD}.entities"]["other"]["conditions"].append("invisible")
    sight = _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="other")
    assert sight.result["context"]["_scene_can_see"] is True
    attack = _run(
        "resolve_srd_attack_conditions",
        state,
        actor_state="hero",
        target_state="other",
        attack_distance=7,
        actor_can_see_target=False,
        target_can_see_actor=True,
        fear_source_in_sight=False,
    )
    assert attack.result["context"]["_condition_advantage"] is True
    state[f"{SRD}.entities"]["hero"]["conditions"].append("blinded")
    attack = _run(
        "resolve_srd_attack_conditions",
        state,
        actor_state="hero",
        target_state="other",
        attack_distance=7,
        actor_can_see_target=True,
        target_can_see_actor=True,
        fear_source_in_sight=False,
    )
    assert attack.result["context"]["_condition_advantage"] is False


@pytest.mark.parametrize("surprised,rolls", [(False, 2), (True, 1)])
def test_invisible_initiative_advantage_uses_actual_condition_and_cancels_surprise(
    surprised, rolls
):
    state, _, _, _ = _cast("invisibility")
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = 0
    state[f"{SRD}.entities"]["guard"]["initiative_modifier"] = 0
    result = _run(
        "start_combat_encounter_entities",
        state,
        first_actor="hero",
        second_actor="guard",
        first_surprised=False,
        second_surprised=surprised,
        tie_winner="first",
    )
    assert len(result.result["context"]["_initiative_second_roll"]["results"]) == rolls
    assert "invisible" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_faerie_fire_suppresses_native_invisible_group_initiative_benefit():
    state, _, _, _ = _cast("faerie_fire", illumination_object="", illumination_position=[1, 0, 0])
    state[f"{SRD}.entities"]["other"].update(initiative_modifier=0, name="Other")
    state[f"{SRD}.entities"]["other"]["conditions"].append("invisible")
    result = _run("roll_srd_actor_group_initiative", state, actor_state="other")
    assert len(result.result["context"]["_actor_initiative_roll"]["results"]) == 1


def test_all_level_darkness_area_retirement_uses_actual_sphere_overlap():
    state, data, params = _case(
        "darkness",
        illumination_object="",
        illumination_position=[50, 0, 0],
        slot_resource="spell_slot_9",
    )
    _run("cast_spell", state, participant_data=data, **params)
    _run(
        "dispel_srd_illumination_darkness_area",
        state,
        origin=[-30, 0, 0],
        plane="material",
        radius=60,
    )
    assert _records(state, "darkness")
    _run(
        "dispel_srd_illumination_darkness_area",
        state,
        origin=[-25, 0, 0],
        plane="material",
        radius=60,
    )
    assert _records(state, "darkness") == []


@pytest.mark.parametrize("spell", ["invisibility", "greater_invisibility"])
def test_invisible_equipment_concealment_follows_actual_carrier_after_reopen(spell):
    state, _, _, _ = _cast(spell)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][2]["metadata"]["carried_by"] = "guard"
    state = json.loads(json.dumps(state))
    result = _run(
        "check_srd_illumination_object_visibility", state, object_id="orb", observer_state="hero"
    )
    assert result.result["context"]["_illumination_object_invisible"] is True
    assert result.result["context"]["_illumination_object_holder_scene_id"] == "guard"
    assert result.result["context"]["_illumination_object_origin"] == [5, 0, 0]
    state[f"{SRD}.state"]["spatial_scene"]["entities"][2]["metadata"]["carried_by"] = ""
    result = _run(
        "check_srd_illumination_object_visibility", state, object_id="orb", observer_state="hero"
    )
    assert result.result["context"]["_illumination_object_invisible"] is False


def test_outlined_invisible_equipment_keeps_native_grant_but_loses_concealment():
    state, _, _, _ = _cast("faerie_fire")
    state[f"{SRD}.entities"]["guard"]["conditions"].append("invisible")
    state[f"{SRD}.state"]["spatial_scene"]["entities"][2]["metadata"]["worn_by"] = "guard"
    result = _run(
        "check_srd_illumination_object_visibility", state, object_id="orb", observer_state="hero"
    )
    assert result.result["context"]["_illumination_object_outlined"] is True
    assert result.result["context"]["_illumination_object_invisible"] is False
    assert "invisible" in state[f"{SRD}.entities"]["guard"]["conditions"]


@pytest.mark.parametrize("level,dice_count", [(1, 1), (5, 2), (11, 3), (17, 4)])
def test_produce_flame_retained_source_derives_magic_action_profile(level, dice_count):
    state, data, params = _case("produce_flame")
    data["level"] = level
    data["classes"][0]["level"] = level
    _run("cast_spell", state, participant_data=data, **params)
    state = json.loads(json.dumps(state))
    before = deepcopy(state)
    result = _run(
        "prepare_srd_produce_flame_source",
        state,
        participant_data=data,
        caster_state="hero",
        caster="hero",
    )
    context = result.result["context"]
    ability = state[f"{SRD}.entities"]["hero"]["produce_flame_casting_ability"]
    assert (
        context["_produce_flame_attack_bonus"]
        == (data["abilities"][ability] - 10) // 2 + 2 + (level - 1) // 4
    )
    assert context["_produce_flame_dice_count"] == dice_count
    assert context["_produce_flame_damage_die"] == "d8"
    assert context["_produce_flame_damage_type"] == "fire"
    assert context["_produce_flame_max_range"] == 60
    assert context["_produce_flame_origin"] == [0, 0, 0]
    assert (
        context["_produce_flame_source_instance"] == _records(state, "produce_flame")[0]["instance"]
    )
    assert state == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    with pytest.raises(RulesetPreconditionRejected, match="Summon Produce Flame"):
        _run(
            "prepare_srd_produce_flame_source",
            state,
            participant_data=data,
            caster_state="hero",
            caster="hero",
        )


def test_prepared_creature_produce_flame_source_uses_saved_spell_attack_statistics():
    state, _, params = _case("produce_flame")
    _run(
        "prepare_srd_creature",
        state,
        creature_id="mage",
        name="Mage",
        source="Synthetic creature spellcasting profile",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=2,
        size_rank=2,
        constitution_save=2,
        spellcasting_ability="wisdom",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        cantrip_scaling_level=11,
        spells=[{"spell": "produce_flame", "uses_per_day": 0}],
    )
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("mage", [5, 0, 0], participant_ref="mage", creature_type="humanoid")
    )
    params.update(caster=None, caster_state="mage", slot_resource=None)
    _run("cast_spell", state, **params)
    result = _run("prepare_srd_produce_flame_source", state, caster_state="mage")
    assert result.result["context"]["_produce_flame_attack_bonus"] == 7
    assert result.result["context"]["_produce_flame_dice_count"] == 3
