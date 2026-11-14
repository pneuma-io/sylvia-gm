"""Identify follows actual object anchors and source ownership across later casts and expiry."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_result_cards import _spell_case
from tests.srd_full.test_srd_spell_wave3_information import SRD, _entity
from tests.srd_full.test_srd_spell_wave4_divination import _case
from tests.srd_full.test_srd_spell_wave7_divination import _finish_identify, _start_identify


def _admit_spell(state, data, spell):
    """Add another legal Wizard spell to this synthetic public caster's actual sources."""
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append(spell)
    data["spellcasting_grants"][0]["spells"].append(spell)
    if spell != "light":
        data["wizard_spellbook"].append(spell)


def _identify(state, data, parameters, subject):
    choices = parameters | {"divination_subject": subject}
    _start_identify(state, data, choices)
    return _finish_identify(state, data, choices)


def test_light_is_identified_on_its_actual_object_and_not_on_the_bookkeeping_caster():
    state, data, parameters = _case("identify")
    _admit_spell(state, data, "light")
    orb = state[f"{SRD}.state"]["spatial_scene"]["entities"][2]
    orb["metadata"]["size_rank"] = 0
    supplied, _, light = _spell_case("light")
    supplies = deepcopy(supplied[f"{SRD}.entities"]["hero"]["spell_materials"])
    for supply in supplies:
        supply["id"] = "light_" + supply["id"]
    hero = state[f"{SRD}.entities"]["hero"]
    hero["spell_materials"].extend(supplies)
    hero["inventory"].extend(supply["inventory_item"] for supply in supplies)
    light["material_item_ids"] = [supply["id"] for supply in supplies]
    light["illumination_object"] = "orb"
    _run("cast_spell_outside_combat", state, participant_data=data, **light)
    assert "light" in _identify(state, data, parameters, "orb")
    assert "light" not in _identify(state, data, parameters, "hero")
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert "light" not in _identify(state, data, parameters, "orb")


def _enchanted_weapon():
    state, data, parameters = _case("identify")
    _admit_spell(state, data, "magic_weapon")
    inventory = state[f"{SRD}.entities"]["hero"]["inventory"]
    if "spear" not in inventory:
        inventory.append("spear")
    orb = state[f"{SRD}.state"]["spatial_scene"]["entities"][2]
    orb["metadata"].update(inventory_owner_ref="hero", inventory_weapon_id="spear")
    _, _, enchant = _spell_case("magic_weapon")
    enchant.update(target="hero", targets=["hero"], actor_spell_weapon="spear")
    _run("cast_spell_outside_combat", state, participant_data=data, **enchant)
    return state, data, parameters


def test_magic_weapon_identity_requires_the_real_object_join_and_exact_lifetime():
    state, data, parameters = _enchanted_weapon()
    assert "magic_weapon" in _identify(state, data, parameters, "orb")
    assert "magic_weapon" not in _identify(state, data, parameters, "hero")
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert "magic_weapon" not in _identify(state, data, parameters, "orb")


def test_object_join_cannot_select_a_duplicate_or_absent_inventory_weapon():
    state, _, _ = _enchanted_weapon()
    state[f"{SRD}.entities"]["hero"]["inventory"].append("spear")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Duplicate"):
        _run("identify_srd_inventory_object_spells", state, actor_state="hero", weapon="spear")
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="admitted inventory"):
        _run(
            "identify_srd_inventory_object_spells", state, actor_state="hero", weapon="quarterstaff"
        )
    assert state == before


def test_creature_identify_reads_actual_transformation_source_then_exact_retirement():
    state, data, parameters = _case("identify")
    state[f"{SRD}.entities"]["guard"]["challenge_rating_eighths"] = 1
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [5, 0, 0]
    _run(
        "prepare_srd_creature",
        state,
        creature_id="shaper",
        name="Public fixture shaper",
        source="Synthetic public caster with explicit Material-component exemption",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=4,
        spellcasting_ability="intelligence",
        spellcasting_modifier=4,
        spell_attack_bonus=8,
        spell_save_dc=16,
        spell_component_exemptions=["material"],
        spells=[{"spell": "polymorph", "uses_per_day": 0}],
    )
    scene["entities"].append(_entity("shaper", [0, 5, 0], participant_ref="shaper"))
    _, _, change = _spell_case("polymorph")
    change.pop("caster")
    change.pop("slot_resource")
    change.update(
        caster_state="shaper",
        transformation_form="camel",
        forego_save_targets=["guard"],
        material_item_ids=[],
    )
    _run("cast_spell_outside_combat", state, **change)
    assert "polymorph" in _identify(state, data, parameters, "guard")
    source_id = state[f"{SRD}.entities"]["guard"]["transformation_source_ids"][0]
    _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)
    assert "polymorph" not in _identify(state, data, parameters, "guard")
