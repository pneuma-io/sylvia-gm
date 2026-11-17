"""Object spell effects cross actual geometry, access, casting costs and durable replay."""

from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_spell_object_content import PROFILES
from scripts.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, MechanicContext, StateValue, owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_public_spell_casting as public_casting
from tests.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.test_srd_scene_relations import _state as _scene_state

SRD = "sylvia.srd-lite.bg3"
ROOT = Path(__file__).resolve().parents[1] / "rulesets/srd-lite"


@lru_cache
def _runtime_bytes():
    """Read the current Lite runtime without regenerating inactive Full sources."""
    return (ROOT / "runtime/ruleset.yaml").read_bytes()


@lru_cache
def _mechanic(name):
    runtime = _load_runtime(_runtime_bytes().decode())
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.1.0",
        package_sha256="a" * 64,
        runtime_bytes=_runtime_bytes(),
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


def _state():
    state = _scene_state()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["entities"][1]["position"] = [3, 2, 0]
    crate = deepcopy(scene["entities"][1])
    crate.update(id="door", position=[3, 0, 0])
    crate["metadata"] = {
        "plane": "material",
        "lighting": "bright",
        "fiction": "Synthetic wooden door",
    }
    scene["entities"].append(crate)
    other = deepcopy(scene["entities"][1])
    other["id"] = "other"
    other["metadata"]["participant_ref"] = "other"
    other["position"] = [3, -2, 0]
    scene["entities"].append(other)
    state[f"{SRD}.entities"]["other"] = deepcopy(state[f"{SRD}.entities"]["guard"])
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, pending_action=None)
    return state


def _object(state):
    retained = state[f"{SRD}.state"].get("lite_exploration_objects", {})
    if "door" in retained:
        return retained["door"]["body"]
    return next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "door")


def _run(name, state, **parameters):
    result = _mechanic(name)(
        MechanicContext(
            campaign_id="objects",
            turn_id="turn",
            action_id=name,
            state=owned_payload(state),
            parameters=owned_payload(parameters),
            participants=owned_payload({}),
        )
    )
    for mutation in result.mutations:
        state[f"{mutation.namespace}.{mutation.key}"] = deepcopy(mutation.value)
    return result.result["context"]


def _access(state, locks=()):
    return _run(
        "record_srd_object_access",
        state,
        object_id="door",
        object_kind="door",
        closed=True,
        locks=list(locks),
        stuck=False,
        barred=False,
    )


def _cast(state, spell, **changes):
    return _run(
        "cast_spell_object_content_spell",
        state,
        **{
            "caster_state": "hero",
            "object_id": "door",
            "spell": spell,
            "slot_level": PROFILES[spell]["base_level"],
            "object_distance": 3,
            "object_touch": True,
            "object_cover": "none",
            **changes,
        },
    )


def _reject(name, state, **parameters):
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
        _run(name, state, **parameters)
    assert state == before


def test_mending_repairs_one_actual_break_and_retains_magic_damage_and_other_breaks():
    state = _state()
    _object(state)["metadata"]["srd_spell_object"] = {"hp": 0, "destroyed": True}
    _object(state)["metadata"]["magic"] = "Synthetic broken enchantment"
    for break_id, extent in [("tear", 1), ("split", 2)]:
        _run(
            "record_srd_object_break",
            state,
            object_id="door",
            break_id=break_id,
            break_min=[3, 0, 0],
            break_max=[3, 0, extent],
        )
    _cast(state, "mending", object_break_id="tear")
    metadata = _object(state)["metadata"]
    assert [b["id"] for b in metadata["srd_object_breaks"]] == ["split"]
    assert metadata["srd_spell_object"] == {"hp": 0, "destroyed": True}
    assert metadata["magic"] == "Synthetic broken enchantment"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="one foot"):
        _cast(state, "mending", object_break_id="split")
    assert state == before


@lru_cache
def _test_archive():
    """Use the actual public package for the retained Mending owner journey."""
    return bundled_srd_archive()


def _create_object_campaign(owner, monkeypatch, *, stone=False, stock=False):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _test_archive)
    scene = _state()[f"{SRD}.state"]["spatial_scene"]
    if stone:
        door = next(row for row in scene["entities"] if row["id"] == "door")
        door["height"] = 4
        door["metadata"]["material"] = "stone"
    if stock:
        door = next(row for row in scene["entities"] if row["id"] == "door")
        door["footprint"]["half_extents"] = [5, 5]
        door["height"] = 10
    mage = deepcopy(scene["entities"][0])
    mage.update(id="mage", position=[0, 1, 0])
    mage["metadata"]["participant_ref"] = "mage"
    scene["entities"].append(mage)
    create = owner.create_campaign

    def admit_scene(command):
        initial = list(command.initial_state)
        for index, item in enumerate(initial):
            if item.namespace == SRD and item.key == "state":
                initial[index] = replace(item, value=item.value | {"spatial_scene": scene})
                break
        else:
            initial.append(StateValue(SRD, "state", {"spatial_scene": scene}))
        return create(replace(command, initial_state=tuple(initial)))

    with patch.object(owner, "create_campaign", side_effect=admit_scene):
        return public_casting._create_spell_campaign(owner, "fire_bolt")


@pytest.mark.parametrize(
    "spell",
    ['mending'],
)
def test_remaining_public_object_spells_pay_actual_sources_and_replay(tmp_path, monkeypatch, spell):
    database = tmp_path / (spell + ".db")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _create_object_campaign(owner, monkeypatch, stone=spell == "stone_shape")
        headers = _load_runtime(_runtime_bytes().decode())["definitions"]["spell_casting"][spell]
        materials = [
            {
                "id": part["id"],
                "inventory_item": part["id"],
                "kind": "specific",
                "components": [part["id"]],
                "quantity": part["quantity"],
                "value_cp": part["minimum_value_cp"],
            }
            for part in headers["material_parts"]
        ]
        prepared, _ = _prepare_mage(
            owner,
            [{"spell": spell, "uses_per_day": 0 if spell == "mending" else 1}],
            inventory=[item["id"] for item in materials],
            spell_materials=materials,
        )
        assert prepared.status is ActionStatus.COMMITTED
        parameters = {
            "caster_state": "mage",
            "spell": spell,
            "material_item_ids": [item["id"] for item in materials],
            "verbal_component_available": True,
            "somatic_component_available": True,
            "material_component_available": True,
        }
        choices = {
            "object_target_id": "door",
            "target_distance": 10 ** 0.5,
            "caster_can_touch_target": True,
            "cover": "none",
        }
        if spell == "mending":
            _act(
                owner,
                "record_srd_object_break",
                object_id="door",
                break_id="tear",
                break_min=[2.7, -0.2, 0],
                break_max=[3.3, 0.2, 1],
            )
            choices["object_break_id"] = "tear"
        elif spell == "create_food_and_water":
            choices = {
                "object_output_id": "supply",
                "object_point": [2, 1, 0],
                "object_form": "Nourishing bread",
            }
        elif spell == "create_or_destroy_water":
            _act(
                owner,
                "record_srd_object_container",
                object_id="door",
                capacity_gallons=25,
                water_gallons=0,
                is_open=True,
            )
        else:
            _act(
                owner,
                "record_srd_object_material",
                object_id="door",
                material="stone_or_crystal" if spell == "stone_shape" else "vegetable",
                raw_stock=spell == "fabricate",
            )
            choices.update(
                object_extent=[0.8, 0.8, 4 if spell == "stone_shape" else 6],
                object_form="Simple coffer",
            )
        if PROFILES[spell]["casting_time"] != "action":
            started, _ = _act(owner, "begin_srd_spell_cast", **parameters)
            assert started.status is ActionStatus.COMMITTED
            continued, _ = _act(
                owner,
                "continue_srd_spell_cast",
                caster_state="mage",
                elapsed_rounds=10 if spell == "mending" else 100,
                uninterrupted=True,
                verbal_component_available=True,
                somatic_component_available=True,
                material_component_available=True,
            )
            assert continued.status is ActionStatus.COMMITTED
        receipt, command = _act(owner, "cast_spell_outside_combat", **parameters, **choices)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"][spell] == 0
        assert receipt.result["result_card"]["title"] == headers["name"]
        if spell == "mending":
            assert _object(saved.state)["metadata"]["srd_object_breaks"] == []
        elif spell == "create_food_and_water":
            supply = next(
                row
                for row in saved.state[f"{SRD}.state"]["spatial_scene"]["entities"]
                if row["id"] == "supply"
            )
            assert supply["metadata"]["srd_created_supply"]["food_pounds"] == 45
        elif spell == "create_or_destroy_water":
            assert _object(saved.state)["metadata"]["srd_object_container"]["water_gallons"] == 10
        elif spell == "fabricate":
            assert _object(saved.state)["metadata"]["srd_object_material"]["raw_stock"] is False
        else:
            assert _object(saved.state)["metadata"]["srd_stone_shape"]["shape"] == "solid"
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.execute_action(command) == receipt
        assert reopened.snapshot("casting") == saved
