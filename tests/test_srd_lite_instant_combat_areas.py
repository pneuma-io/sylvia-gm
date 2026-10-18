"""Prove the shared immediate-area qualifier and combat-only public preflight."""

import json
import os
import re
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_instant_area import QUALIFY, bind_instant_combat_areas
from scripts.srd_lite_instant_area_pruning import omit_unselected_instant_area_branches
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _scene_entity, _wall
from tests.test_srd_lite_vertical_cones import run, state_for

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget


@pytest.fixture(scope="module")
def areas():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    paid = deepcopy(data["tools"]["cast_spell_instantaneous_area_save_damage"])
    profiles = deepcopy(data["definitions"]["spells"])
    headers = deepcopy(data["definitions"]["spell_casting"])
    bind_instant_combat_areas(data)
    assert data["definitions"]["spells"] == profiles
    assert data["definitions"]["spell_casting"] == headers
    assert data["tools"]["cast_spell_instantaneous_area_save_damage"] == paid
    omitted = omit_unselected_instant_area_branches(data)
    assert {row["spell"] for row in omitted}.isdisjoint(profiles)
    retained = data["tools"]["cast_spell_instantaneous_area_save_damage"]
    for spell in profiles:
        assert specialize(paid["effects"], spell) == specialize(retained["effects"], spell), spell
    roots = ["cast_spell", "begin_srd_spell_action"]
    for root in roots:
        tool = data["tools"][root]
        data["tools"]["preflight_" + root] = {
            **deepcopy(tool),
            "effects": deepcopy(tool["effects"][:17]),
        }
    closure, pending = {}, [QUALIFY] + ["preflight_" + name for name in roots]
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in ("tools", "procedures")}
    subset["tools"] = closure
    # Root preflight keeps actual parameter types, but unrelated PC/casting guards are
    # exercised by the genuine public integration check rather than this geometry slice.
    for name in roots:
        subset["tools"]["preflight_" + name]["pre_conditions"] = []
    return RulesetRuntime(ruleset_dict=subset)


def specialize(effects, spell):
    """Compare each selected spell's reachable effects after resolving literal spell guards."""
    result = []
    for original in effects:
        row = deepcopy(original)
        condition = row.get("condition", "")
        match = (
            re.fullmatch(r"spell == '([^']+)'( and .*)?", condition)
            if isinstance(condition, str)
            else None
        )
        if match and "on_true" in row:
            if match.group(1) != spell:
                result += specialize(row.get("on_false", []), spell)
                continue
            if not match.group(2):
                result += specialize(row["on_true"], spell)
                continue
        for branch in ("on_true", "on_false"):
            if branch in row:
                row[branch] = specialize(row[branch], spell)
        if "for_each" in row:
            row["for_each"]["do"] = specialize(row["for_each"]["do"], spell)
        result.append(row)
    return result


def scene(position, *, hero=(-30, 0, 0), target_circle=False, plane="material", geometry=()):
    state = state_for(position, geometry)
    bodies = state[NAMESPACE + ".state"]["spatial_scene"]["entities"]
    bodies[0]["position"] = list(hero)
    bodies[1]["metadata"]["plane"] = plane
    if target_circle:
        bodies[1]["footprint"] = {"kind": "circle", "radius": 0.4}
    state[NAMESPACE + ".state"]["spatial_scene"]["map"]["bounds"] = {
        "min": [-500, -500, -100],
        "max": [500, 500, 100],
    }
    state[NAMESPACE + ".state"]["spatial_scene"]["map"]["grid"]["origin"] = [-500, -500]
    return state


def preflight(parsed, state, tool, parameters):
    """Supply the real top-level combat context without performing the retained paid cast."""
    before = json.dumps(state, sort_keys=True)
    rng = dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, state)

    def nested(name, values, parent):
        return execute_tool(
            name,
            values,
            parsed.tools,
            bridge,
            parsed.ruleset_config,
            parsed.definitions,
            combat_state=parent.combat_state,
            tool_executor=nested,
            budget=parent.budget,
        )

    result = execute_tool(
        tool,
        parameters,
        parsed.tools,
        bridge,
        parsed.ruleset_config,
        parsed.definitions,
        combat_state=state[NAMESPACE + ".combat"],
        tool_executor=nested,
        budget=ExecutionBudget(),
    )
    assert json.dumps(state, sort_keys=True) == before
    assert not bridge.mutations()
    assert dice._rng.getstate() == rng
    return result


@pytest.mark.parametrize(
    ("spell", "position", "included"),
    [
        ("acid_splash", [5.4, 0, 0], True),
        ("acid_splash", [5.41, 0, 0], False),
        ("fireball", [20.4, 0, 0], True),
        ("fireball", [20.41, 0, 0], False),
        ("circle_of_death", [60.4, 0, 0], True),
        ("circle_of_death", [60.41, 0, 0], False),
    ],
)
def test_approved_spheres_use_actual_circular_body_boundary(areas, spell, position, included):
    result = run(
        areas,
        scene(position, target_circle=True),
        QUALIFY,
        {"caster_state": "hero", "spell": spell, "environment_origin": [0, 0, 3]},
    )
    assert result.success, result.error
    assert ("guard" in result.context["_instant_targets"]) is included


@pytest.mark.parametrize("direction", ([1, 0, 1], [0, 0, 1]))
def test_burning_hands_uses_approved_15_foot_cone_and_3d_direction(areas, direction):
    position = [8, 0, 10] if direction[0] else [0, 0, 15]
    result = run(
        areas,
        scene(position, hero=(0, 0, 0), target_circle=True),
        QUALIFY,
        {
            "caster_state": "hero",
            "spell": "burning_hands",
            "environment_origin": [0, 0, 0],
            "instant_area_direction": direction,
        },
    )
    assert result.success, result.error
    assert result.context["_instant_targets"] == ["guard"]
    assert result.context["_instant_origin_distance"] == 0


@pytest.mark.parametrize("root", ("cast_spell", "begin_srd_spell_action"))
def test_combat_preflight_replaces_forged_recipients_before_retained_owner(areas, root):
    state = scene([10, 0, 0])
    state[NAMESPACE + ".combat"]["encounter_active"] = True
    result = preflight(
        areas,
        state,
        "preflight_" + root,
        {
            "caster_state": "hero",
            "spell": "fireball",
            "slot_resource": "spell_slot_3",
            "targets": ["hero"],
            "origin_distance": 0,
            "environment_origin": [10, 0, 3],
        },
    )
    assert result.success, result.error
    assert result.context["_instant_cast_targets"] == ["guard"], {
        "actual": result.context["_instant_cast_targets"],
        "qualified": result.context.get("_instant_targets"),
        "combat": result.context.get("combat"),
    }
    assert result.context["_instant_cast_origin_distance"] == 40


@pytest.mark.parametrize("spell", ("fireball", "lightning_bolt"))
def test_exploration_preflight_does_not_require_a_map(areas, spell):
    state = scene([10, 0, 0])
    state[NAMESPACE + ".state"].pop("spatial_scene")
    result = preflight(
        areas,
        state,
        "preflight_cast_spell",
        {
            "caster_state": "hero",
            "spell": spell,
            "slot_resource": "spell_slot_3",
            "targets": ["guard"],
            "origin_distance": 25,
        },
    )
    assert result.success, result.error
    assert result.context["_instant_cast_targets"] == ["guard"]
    assert result.context["_instant_cast_origin_distance"] == 25


def test_actual_point_range_rejects_without_mutation_or_dice(areas):
    result = run(
        areas,
        scene([10, 0, 0], hero=(0, 0, 0)),
        QUALIFY,
        {"caster_state": "hero", "spell": "fireball", "environment_origin": [151, 0, 3]},
    )
    assert not result.success
    assert "beyond" in result.error


def test_other_plane_is_excluded(areas):
    result = run(
        areas,
        scene([5, 0, 0], plane="ethereal"),
        QUALIFY,
        {"caster_state": "hero", "spell": "fireball", "environment_origin": [0, 0, 3]},
    )
    assert result.success, result.error
    assert "guard" not in result.context["_instant_targets"]


def test_actual_wall_excludes_total_cover_recipient(areas):
    state = scene([10, 0, 0], geometry=[_wall()])
    state[NAMESPACE + ".state"]["spatial_scene"]["entities"][1]["metadata"]["cover_from_points"] = [
        {"point": [0, 0, 3], "plane": "material", "cover": "total"}
    ]
    result = run(
        areas,
        state,
        QUALIFY,
        {"caster_state": "hero", "spell": "fireball", "environment_origin": [0, 0, 3]},
    )
    assert result.success, result.error
    assert "guard" not in result.context["_instant_targets"]


def test_fire_area_derives_only_actual_unattended_scenery(areas):
    state = scene([10, 0, 0])
    bodies = state[NAMESPACE + ".state"]["spatial_scene"]["entities"]
    for identity, position, carried in (
        ("fuel", [2, 0, 0], None),
        ("distant", [50, 0, 0], None),
        ("carried", [2, 2, 0], "hero"),
    ):
        body = _scene_entity(identity, position, footprint={"kind": "circle", "radius": 0.5})
        body["metadata"].update(
            participant_ref=None,
            is_object=True,
            creature_type="object",
            flammable=True,
            srd_ordinary_flame={"kind": "ordinary_flame"},
            carried_by=carried,
        )
        bodies.append(body)
    bodies[1]["metadata"]["cover_from_points"] = [
        {"point": [0, 0, 3], "plane": "material", "cover": "half"}
    ]
    result = run(
        areas,
        state,
        QUALIFY,
        {"caster_state": "hero", "spell": "fireball", "environment_origin": [0, 0, 3]},
    )
    assert result.success, result.error
    assert result.context["_instant_objects"] == ["fuel"]


@pytest.mark.parametrize(
    ("position", "included"),
    [
        ([50, 2.9, 0], True),
        ([50, 2.91, 0], False),
        ([100.8, 0, 0], True),
        ([100.81, 0, 0], False),
        ([50, 0, 5], True),
        ([50, 0, 5.01], False),
    ],
)
def test_lightning_bolt_uses_actual_100_by_5_line_and_declared_height(areas, position, included):
    result = run(
        areas,
        scene(position, hero=(0, 0, 0), target_circle=True),
        QUALIFY,
        {
            "caster_state": "hero",
            "spell": "lightning_bolt",
            "environment_origin": [0, 0, 0],
            "instant_area_height": 4,
        },
    )
    assert result.success, result.error
    assert ("guard" in result.context["_instant_targets"]) is included
    assert result.context["_instant_origin_distance"] == 0


@pytest.mark.parametrize(
    ("direction", "position", "included"),
    [
        ([1, 0, 1], [20, 0, 25], True),
        ([1, 0, 1], [20, 5, 25], False),
        ([0, 0, 1], [0, 0, 90], True),
        ([0, 0, 1], [3, 0, 90], False),
    ],
)
def test_lightning_line_uses_tilted_and_vertical_directions(areas, direction, position, included):
    result = run(
        areas,
        scene(position, hero=(0, 0, 0), target_circle=True),
        QUALIFY,
        {
            "caster_state": "hero",
            "spell": "lightning_bolt",
            "environment_origin": [0, 0, 0],
            "instant_area_direction": direction,
            "instant_area_height": 4,
        },
    )
    assert result.success, result.error
    assert ("guard" in result.context["_instant_targets"]) is included


def test_lightning_combat_preflight_replaces_forged_recipients_before_payment(areas):
    state = scene([30, 0, 0], hero=(0, 0, 0), target_circle=True)
    state[NAMESPACE + ".combat"]["encounter_active"] = True
    result = preflight(
        areas,
        state,
        "preflight_cast_spell",
        {
            "caster_state": "hero",
            "spell": "lightning_bolt",
            "slot_resource": "spell_slot_3",
            "targets": ["hero"],
            "instant_area_height": 4,
        },
    )
    assert result.success, result.error
    assert result.context["_instant_cast_targets"] == ["guard"]
    assert result.context["_instant_cast_origin_distance"] == 0


def test_missing_line_height_fails_before_mutation_or_dice(areas):
    state = scene([30, 0, 0], hero=(0, 0, 0))
    state[NAMESPACE + ".combat"]["encounter_active"] = True
    result = preflight(
        areas,
        state,
        "preflight_cast_spell",
        {
            "caster_state": "hero",
            "spell": "lightning_bolt",
            "slot_resource": "spell_slot_3",
            "targets": ["guard"],
        },
    )
    assert not result.success
    assert "vertical extent" in result.error


@pytest.mark.parametrize("spell", ("burning_hands", "lightning_bolt"))
def test_directional_spell_can_explicitly_include_its_origin(areas, spell):
    state = scene([10, 0, 0], hero=(0, 0, 0))
    parameters = {
        "caster_state": "hero",
        "spell": spell,
        "environment_origin": [0, 0, 0],
        "instant_area_height": 4,
    }
    excluded = run(areas, state, QUALIFY, parameters)
    assert excluded.success, excluded.error
    assert "hero" not in excluded.context["_instant_targets"]
    included = run(areas, state, QUALIFY, {**parameters, "instant_area_include_origin": True})
    assert included.success, included.error
    assert "hero" in included.context["_instant_targets"]
