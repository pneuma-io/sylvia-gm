"""Shared Cylinder admission before existing Flame Strike damage and staging."""

import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_flame_strike_geometry import (
    AREA,
    CYLINDER,
    EXPORTS,
    PREFIX_LENGTH,
    QUALIFY,
    ROOTS,
    bind_flame_strike_combat_cylinder,
)
from scripts.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_instant_combat_areas import preflight, scene
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _wall


@pytest.fixture(scope="module")
def cylinders():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    profiles = deepcopy(data["definitions"]["spells"])
    headers = deepcopy(data["definitions"]["spell_casting"])
    pending_completion = deepcopy(data["tools"]["complete_srd_pending_action"])
    bind_flame_strike_combat_cylinder(data)
    assert data["definitions"]["spells"] == profiles
    assert data["definitions"]["spell_casting"] == headers
    assert data["tools"]["complete_srd_pending_action"] == pending_completion
    pending = [QUALIFY, CYLINDER]
    for root in ROOTS:
        tool = deepcopy(data["tools"][root])
        tool["effects"] = tool["effects"][:PREFIX_LENGTH]
        tool["pre_conditions"] = []
        name = "preflight_" + root
        data["tools"][name] = tool
        pending.append(name)
    leaf = deepcopy(data["tools"][AREA])
    leaf["effects"] = leaf["effects"][:5]
    leaf["pre_conditions"] = [
        row
        for row in leaf["pre_conditions"]
        if "origin_kind == 'point' and origin_distance <= range_feet" in row["condition"]
    ]
    leaf["parameters"] = [
        row
        for row in leaf["parameters"]
        if row["name"]
        in {
            "spell",
            "caster_state",
            "lasting_state",
            "targets",
            "half_cover_targets",
            "three_quarters_cover_targets",
            "origin_distance",
            "environment_origin",
            "origin_kind",
            "area_shape",
            "range_feet",
            "area_radius_feet",
            "area_height_feet",
            "area_point_count",
        }
    ]
    data["tools"]["preflight_composite_leaf"] = leaf
    pending.append("preflight_composite_leaf")
    closure = {}
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in {"tools", "procedures"}}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


def state(position=(8, 0, 0), *, combat=True, plane="material", geometry=()):
    value = scene(list(position), target_circle=True, plane=plane, geometry=geometry)
    value[NAMESPACE + ".combat"]["encounter_active"] = combat
    for body in value[NAMESPACE + ".state"]["spatial_scene"]["entities"]:
        body["id"] += "-body"
    for actor in value[NAMESPACE + ".entities"].values():
        actor.update(
            creature_kind="monster",
            temp_hp=0,
            exhaustion_level=0,
            dodging=False,
            speed=30,
            saving_throw_modifiers={"dexterity": 0},
            damage_immunities=[],
            damage_resistances=[],
            damage_vulnerabilities=[],
        )
    return value


def qualify(cylinders, value, *, origin=(0, 0, 0), tool=QUALIFY, **parameters):
    return preflight(
        cylinders,
        value,
        tool,
        {
            "caster_state": "hero",
            "origin": list(origin),
            **parameters,
        },
    )


@pytest.mark.parametrize(
    ("position", "included"),
    [
        ((10.4, 0, 0), True),
        ((10.41, 0, 0), False),
        ((0, 0, 40), True),
        ((0, 0, 40.01), False),
        ((8, 8, 0), False),
    ],
)
def test_flame_cylinder_uses_actual_circle_and_vertical_extent(cylinders, position, included):
    result = qualify(cylinders, state(position))
    assert result.success, result.error
    assert ("guard" in result.context[EXPORTS[0]]) is included
    assert "hero" not in result.context[EXPORTS[0]]


def test_flame_excludes_other_plane_and_total_cover(cylinders):
    for value in (state(plane="ethereal"), state(geometry=[_wall()])):
        result = qualify(cylinders, value)
        assert result.success, result.error
        assert result.context[EXPORTS[0]] == []


def test_flame_projects_actual_half_cover(cylinders):
    result = qualify(cylinders, state(geometry=[_wall(height=1.5)]))
    assert result.success, result.error
    assert result.context[EXPORTS[0]] == ["guard"]
    assert result.context[EXPORTS[1]] == ["guard"]
    assert result.context[EXPORTS[2]] == []


@pytest.mark.parametrize("root", ROOTS)
def test_cast_and_pending_preflight_replace_supplied_facts(cylinders, root):
    result = preflight(
        cylinders,
        state(),
        "preflight_" + root,
        {
            "caster_state": "hero",
            "spell": "flame_strike",
            "slot_resource": "spell_slot_5",
            "targets": ["hero"],
            "target": "hero",
            "origin_distance": 999,
            "half_cover_targets": ["hero"],
            "environment_origin": [0, 0, 0],
        },
    )
    assert result.success, result.error
    assert result.context["_flame_dispatch_targets"] == ["guard"]
    assert result.context["_flame_dispatch_half_cover_targets"] == []
    assert result.context["_flame_dispatch_origin_distance"] == 30
    assert result.context["_flame_dispatch_target"] is None
    assert result.context["_flame_dispatch__target_id"] is None


def test_exploration_preflight_preserves_facts_without_scene(cylinders):
    value = state(combat=False)
    value[NAMESPACE + ".state"].pop("spatial_scene")
    result = preflight(
        cylinders,
        value,
        "preflight_cast_spell",
        {
            "caster_state": "hero",
            "spell": "flame_strike",
            "slot_resource": "spell_slot_5",
            "targets": ["guard"],
            "origin_distance": 35,
        },
    )
    assert result.success, result.error
    assert result.context["_flame_dispatch_targets"] == ["guard"]
    assert result.context["_flame_dispatch_origin_distance"] == 35


@pytest.mark.parametrize("root", ROOTS)
def test_bad_actual_origin_rejects_before_rng_mutation_or_staging(cylinders, root):
    value = state()
    for origin in ([30.01, 0, 0], [], None):
        result = preflight(
            cylinders,
            value,
            "preflight_" + root,
            {
                "caster_state": "hero",
                "spell": "flame_strike",
                "slot_resource": "spell_slot_5",
                "targets": ["guard"],
                "origin_distance": 0,
                "environment_origin": origin,
            },
        )
        assert not result.success


def test_exact_range_accepts_actual_origin(cylinders):
    result = qualify(cylinders, state((30, 0, 0)), origin=(30, 0, 0))
    assert result.success, result.error
    assert result.context[EXPORTS[3]] == 60


def test_flame_leaf_does_not_reject_supplied_distance_before_actual_qualification(cylinders):
    result = preflight(
        cylinders,
        state(),
        "preflight_composite_leaf",
        {
            "caster_state": "hero",
            "spell": "flame_strike",
            "targets": ["hero"],
            "half_cover_targets": [],
            "three_quarters_cover_targets": [],
            "origin_distance": 999,
            "environment_origin": [0, 0, 0],
            "origin_kind": "point",
            "area_shape": "cylinder",
            "range_feet": 60,
            "area_radius_feet": 10,
            "area_height_feet": 40,
            "area_point_count": 1,
        },
    )
    assert result.success, result.error
    assert result.context["_flame_area_targets"] == ["guard"]
    assert result.context["_flame_area_origin_distance"] == 30


def test_default_ice_cylinder_accepts_circular_bodies_and_retains_original_size(cylinders):
    result = qualify(cylinders, state((20.4, 0, 0)), tool=CYLINDER)
    assert result.success, result.error
    assert result.context["_retained_ice_targets"] == ["guard"]
    assert result.context["_retained_ice_ground_intersections"] == []


def test_flame_does_not_require_hail_capacity_or_collect_ground(cylinders):
    value = state()
    value[NAMESPACE + ".state"]["turn_effects"] = [{} for _ in range(128)]
    result = qualify(cylinders, value)
    assert result.success, result.error
    ice = qualify(cylinders, value, tool=CYLINDER)
    assert not ice.success
