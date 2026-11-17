"""Authentic duration casts retain exact identity through their owning removal callbacks."""

import json
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_spell_removals import extend_runtime as removal_runtime
from scripts.srd_spell_source_instances import extend_runtime as source_runtime

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.test_srd_extended_casting import _begin
from tests.srd_illusion_removals_fixtures import _prepare
from tests.test_srd_npc_spell_casting import _act
from tests.test_srd_spell_wave3_information import _case as _information_case
from tests.test_srd_spell_wave3_information import _entity

SRD = cards.SRD


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    root = os.environ.get("FC14_SOURCE_RUNTIME_ROOT")
    if root:
        monkeypatch.setattr(budget, "ROOT", Path(root))
    budget._mechanic.cache_clear()
    monkeypatch.setattr(
        cards,
        "_spell_definitions",
        lambda: budget._runtime_definition((budget.ROOT / "runtime/ruleset.yaml").read_bytes())[
            "definitions"
        ]["spells"],
    )
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    yield
    budget._mechanic.cache_clear()


def _scene_row(state, subject):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == subject
    )


def _case(spell="blink", slot=None):
    state, data, params = _information_case("mind_blank")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [5, 0, 0]
    scene["entities"] += [
        _entity("paper", [5, 0, 0], is_object=True, writable_surface=True),
        _entity("seen-hill", [30, 0, 0], is_object=True, seen_by=["hero"]),
        _entity("metal", [3, 0, 0], is_object=True),
        _entity("sapphire", [2, 0, 0], is_object=True),
        _entity("item", [4, 0, 0], is_object=True),
    ]
    for row in scene["entities"]:
        if row["id"] in {"hero", "guard"}:
            row["metadata"].update(basic_limb_arrangement="two arms and two legs", size_rank=2)
    scene["zones"] += [
        {
            "id": "adoption-light-" + plane,
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": plane, "lighting": "bright"},
        }
        for plane in ("material", "ethereal")
    ]
    for actor in ("hero", "guard"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, size_rank=2)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["intelligence"] = -100
    material_ids = _prepare(state, data, spell)
    if spell in {"goodberry", "antilife_shell", "heat_metal"}:
        saved = state[f"{SRD}.entities"]["hero"]
        budget._run(
            "prepare_srd_creature",
            state,
            creature_id="mage",
            name="Source caster",
            source="Synthetic creature: prepared source spells, DC 15, CON +6.",
            armor_class=13,
            hit_points=1000,
            speed=30,
            initiative_modifier=0,
            size_rank=2,
            constitution_save=6,
            spellcasting_ability="wisdom",
            spellcasting_modifier=3,
            spell_attack_bonus=7,
            spell_save_dc=15,
            inventory=saved["inventory"],
            spell_materials=saved["spell_materials"],
            spells=[
                {"spell": spell, "uses_per_day": 3},
                {"spell": "dispel_magic", "uses_per_day": 3},
            ],
        )
        params.pop("caster", None)
        params["caster_state"] = "mage"
        scene = state[f"{SRD}.state"]["spatial_scene"]
        scene["entities"][0]["id"] = "mage"
        scene["entities"][0]["metadata"]["participant_ref"] = "mage"
    params.update(
        spell=spell,
        target=params["caster_state"],
        target_distance=5,
        slot_resource=f"spell_slot_{slot or cards._casting_headers()[spell]['level']}",
        material_item_ids=material_ids,
    )
    specs = [
        {
            "subject": actor,
            "description": "A shorter traveler",
            "height_offset_inches": -12,
            "limb_arrangement": "two arms and two legs",
            "willing": True,
        }
        for actor in ("hero", "guard")
    ]
    params.update(
        appearance_specs=specs[:1] if spell == "disguise_self" else specs,
        appearance_position=[0, 0, 0],
        appearance_cube_min=[0, 0, 0],
        appearance_description="A rocky gully",
        appearance_terrain_kind="rocky gully",
        appearance_tactile_kind="rough rock",
        script_surface="paper",
        script_original="The door opens at dawn",
        script_readers=["guard"],
        illusion_position=[20, 0, 0],
        illusion_size=[10, 10, 10],
        illusion_description="A dangerous burning bridge",
        illusion_sound="Flames",
        phantasm_dangerous=True,
        phantasm_damage_appearance="fire",
        project_image_location="seen-hill",
        barrier_subject="",
        target_willing=True,
    )
    if spell == "phantasmal_force":
        params["target"] = "guard"
    if spell == "resilient_sphere":
        params["target"] = "guard"
        params["barrier_subject"] = "guard"
    if spell == "illusory_script":
        params.update(caster_can_touch_target=True, target_distance=0)
    if spell == "goodberry":
        budget._run(
            "record_srd_hand_sized_form",
            state,
            caster_state=params["caster_state"],
            form_id="berries",
            fiction="Ten berries fitting this hand",
            kind="trinket",
            extent=[0.4, 0.4, 0.3],
        )
        params.update(supply_output_id="berries", supply_hand_form_id="berries")
    if spell in {"heat_metal", "instant_summons"}:
        for object_id, metal, kind, item in (
            ("metal", True, "armor", "metal-armor"),
            ("sapphire", False, "sapphire", material_ids[0]),
            ("item", False, "object", ""),
        ):
            budget._run(
                "record_srd_object_utility_facts",
                state,
                object_id=object_id,
                weight_pounds=0.2 if kind == "sapphire" else 7,
                manufactured=True,
                metal=metal,
                object_kind=kind,
                inventory_item=item,
            )
        if spell == "heat_metal":
            state[f"{SRD}.entities"]["guard"].setdefault("inventory", []).append("metal-armor")
            budget._run(
                "record_srd_object_holder",
                state,
                actor_state="guard",
                object_id="metal",
                held_as="worn",
            )
            params["heated_object_id"] = "metal"
        else:
            budget._run(
                "record_srd_object_holder",
                state,
                actor_state="hero",
                object_id="sapphire",
                held_as="carried",
            )
            budget._run(
                "record_srd_spell_material_object",
                state,
                caster_state="hero",
                object_id="sapphire",
                material_item_id=material_ids[0],
            )
            params.update(utility_object_id="item", utility_sapphire_id="sapphire")
    return state, data, params


def _cast(state, data, params):
    hero = state[f"{SRD}.entities"][params["caster_state"]]
    hero["spell_slot_cast_this_turn"] = False
    rounds = {
        "hallucinatory_terrain": 100,
        "illusory_script": 10,
        "tiny_hut": 10,
        "instant_summons": 10,
    }.get(params["spell"])
    if rounds:
        budget._run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
        budget._run(
            "continue_srd_spell_cast",
            state,
            caster_state=params["caster_state"],
            elapsed_rounds=rounds,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
    actual_params = params.copy()
    if params["caster_state"] == "mage":
        actual_params.pop("slot_resource", None)
    return budget._run("cast_spell_outside_combat", state, participant_data=data, **actual_params)


def _records(state, spell):
    lasting = state[f"{SRD}.state"]
    if spell == "blink":
        return [state[f"{SRD}.entities"]["hero"]["travel_blink"]]
    if spell == "goodberry":
        return [_scene_row(state, "berries")["metadata"]["srd_goodberry_batch"]]
    for pool in ("appearance_spell_sources", "illusion_spell_sources", "spell_barrier_sources"):
        if pool in lasting:
            found = [
                r
                for r in lasting[pool].values()
                if r.get("spell", r.get("spell_id")) == spell and r.get("installed")
            ]
            if found:
                return found
    return [r for r in lasting.get("turn_effects", []) if r.get("spell") == spell]


SPELLS = [
    "blink",
    "disguise_self",
    "seeming",
    "hallucinatory_terrain",
    "illusory_script",
    "mirror_image",
    "phantasmal_force",
    "project_image",
    "goodberry",
    "antilife_shell",
    "resilient_sphere",
    "tiny_hut",
    "heat_metal",
    "instant_summons",
]




def _dispel(state, data, params, **choices):
    material_ids = _prepare(state, data, "dispel_magic")
    return _cast(
        state,
        data,
        params
        | {
            "spell": "dispel_magic",
            "target": "hero",
            "slot_resource": "spell_slot_9",
            "material_item_ids": material_ids,
        }
        | choices,
    )
































def _composed_archive():
    """Declare the actual composed runtime over an explicitly selected immutable public package."""
    primary, reference = bundled_packages._srd_drafts()
    content = (budget.ROOT / "runtime/ruleset.yaml").read_bytes()
    runtime = yaml.load(content, Loader=yaml.CSafeLoader)
    record = deepcopy(primary.record)
    for declaration in record["tools"]:
        declaration["input_schema"] = _tool_schema(
            runtime["tools"][declaration["name"]], tool_name=declaration["name"]
        )
    payloads = dict(primary.payloads) | {"runtime/ruleset.yaml": content}
    return bundled_packages._archive(
        (NormalizedPackageDraft(record, payloads, primary.format_version), reference)
    )
