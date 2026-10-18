"""Fear/Sleep actual combat-area admission before unchanged control and staging owners."""

import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_control_area_geometry import (
    EXPORTS,
    PAID,
    PREFIX_LENGTH,
    QUALIFY,
    ROOTS,
    SPELLS,
    bind_control_combat_areas,
)
from scripts.srd_lite_flame_strike_geometry import bind_flame_strike_combat_cylinder
from scripts.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_instant_combat_areas import preflight, scene
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _scene_entity, _wall


@pytest.fixture(scope="module")
def controls():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    # Match the current adopted composition after the admitted 9682 package.
    bind_flame_strike_combat_cylinder(data)
    profiles = deepcopy(data["definitions"]["spells"])
    headers = deepcopy(data["definitions"]["spell_casting"])
    paid = deepcopy(data["tools"][PAID])
    completion = deepcopy(data["tools"]["complete_srd_pending_action"])
    parameters = {name: deepcopy(data["tools"][name]["parameters"]) for name in ROOTS}
    assert bind_control_combat_areas(data) == [QUALIFY, *ROOTS]
    assert data["definitions"]["spells"] == profiles
    assert data["definitions"]["spell_casting"] == headers
    assert data["tools"][PAID] == paid
    assert data["tools"]["complete_srd_pending_action"] == completion
    assert {name: data["tools"][name]["parameters"] for name in ROOTS} == parameters
    for name in SPELLS:
        assert data["definitions"]["spells"][name]["base_targets"] == 8
    pending = [QUALIFY]
    for root in ROOTS:
        preview = deepcopy(data["tools"][root])
        preview["effects"] = preview["effects"][:PREFIX_LENGTH]
        preview["pre_conditions"] = []
        data["tools"]["preflight_" + root] = preview
        pending.append("preflight_" + root)
    closure = {}
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in {"tools", "procedures"}}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


def state(position=(10, 0, 0), *, hero=(0, 0, 0), combat=True, plane="material", geometry=()):
    value = scene(list(position), hero=hero, target_circle=True, plane=plane, geometry=geometry)
    value[NAMESPACE + ".combat"]["encounter_active"] = combat
    # Actor references, rather than saved body IDs, own spells and conditions.
    for body in value[NAMESPACE + ".state"]["spatial_scene"]["entities"]:
        body["id"] += "-body"
    return value


def qualify(controls, value, spell, **intent):
    return preflight(controls, value, QUALIFY, {"caster_state": "hero", "spell": spell, **intent})


@pytest.mark.parametrize(
    ("position", "direction", "included"),
    [
        ((10, 0, 0), [1, 0], True),
        ((-10, 0, 0), [1, 0], False),
        ((5, -8, 0), [1, 0], False),
        ((0, 0, 20), [0, 0, 1], True),
    ],
)
def test_fear_uses_saved_body_and_actual_cone_direction(controls, position, direction, included):
    result = qualify(controls, state(position), "fear", instant_area_direction=direction)
    assert result.success, result.error
    assert ("guard" in result.context[EXPORTS[0]]) is included
    assert "hero" not in result.context[EXPORTS[0]]
    assert result.context[EXPORTS[1]] == 0


def test_fear_defaults_to_actual_saved_body_facing(controls):
    result = qualify(controls, state(), "fear")
    assert result.success, result.error
    assert result.context[EXPORTS[0]] == ["guard"]


@pytest.mark.parametrize(("position", "included"), [((5.4, 0, 0), True), ((5.41, 0, 0), False)])
def test_sleep_uses_five_foot_sphere_and_actual_circle_edge(controls, position, included):
    result = qualify(
        controls,
        state(position, hero=(-30, 0, 0)),
        "sleep",
        environment_origin=[0, 0, 3],
    )
    assert result.success, result.error
    assert ("guard" in result.context[EXPORTS[0]]) is included
    assert result.context[EXPORTS[1]] == 30


@pytest.mark.parametrize("root", ROOTS)
@pytest.mark.parametrize("spell", SPELLS)
def test_public_cast_and_pending_admission_replace_supplied_recipients_and_range(
    controls, root, spell
):
    value = state() if spell == "fear" else state((2, 0, 0), hero=(-30, 0, 0))
    intent = (
        {"instant_area_direction": [1, 0]} if spell == "fear" else {"environment_origin": [0, 0, 3]}
    )
    result = preflight(
        controls,
        value,
        "preflight_" + root,
        {
            "caster_state": "hero",
            "spell": spell,
            "slot_resource": "spell_slot_3" if spell == "fear" else "spell_slot_1",
            "targets": ["hero"],
            "target": "hero",
            "targets_max_distance": 999,
            "all_targets_have_clear_path": False,
            **intent,
        },
    )
    assert result.success, result.error
    assert result.context["_control_geometry_targets"] == ["guard"]
    assert result.context["_control_geometry_targets_max_distance"] == (
        0 if spell == "fear" else 30
    )
    assert result.context["_control_geometry_all_targets_have_clear_path"] is True
    assert result.context["_control_geometry_target"] is None
    assert result.context["_control_geometry__target_id"] is None


@pytest.mark.parametrize("root", ROOTS)
@pytest.mark.parametrize("spell", SPELLS)
def test_actual_area_needs_no_supplied_recipient_or_distance_list(controls, root, spell):
    value = state() if spell == "fear" else state((2, 0, 0), hero=(-30, 0, 0))
    intent = {} if spell == "fear" else {"environment_origin": [0, 0, 3]}
    result = preflight(
        controls,
        value,
        "preflight_" + root,
        {
            "caster_state": "hero",
            "spell": spell,
            "slot_resource": "spell_slot_3" if spell == "fear" else "spell_slot_1",
            **intent,
        },
    )
    assert result.success, result.error
    assert result.context["_control_geometry_targets"] == ["guard"]


@pytest.mark.parametrize("spell", SPELLS)
def test_actual_area_excludes_other_plane_and_total_cover(controls, spell):
    position = (10, 0, 0) if spell == "fear" else (5.4, 0, 0)
    hero = (0, 0, 0) if spell == "fear" else (-30, 0, 0)
    for value in (
        state(position, hero=hero, plane="ethereal"),
        state(position, hero=hero, geometry=[_wall()]),
    ):
        value[NAMESPACE + ".state"]["spatial_scene"]["entities"][1]["metadata"][
            "cover_from_points"
        ] = [
            {
                "point": [0.4, 0, 3] if spell == "fear" else [0, 0, 3],
                "plane": "material",
                "cover": "total",
            }
        ]
        result = qualify(
            controls,
            value,
            spell,
            instant_area_direction=[1, 0],
            environment_origin=[0, 0, 3],
        )
        assert result.success, result.error
        assert result.context[EXPORTS[0]] == []


@pytest.mark.parametrize(("origin", "accepted"), [([60.4, 0, 3], True), ([60.41, 0, 3], False)])
def test_sleep_origin_range_uses_actual_nearest_caster_body_point(controls, origin, accepted):
    result = qualify(controls, state((60, 0, 0)), "sleep", environment_origin=origin)
    assert result.success is accepted, result.error
    if accepted:
        assert result.context[EXPORTS[0]] == ["guard"]
        assert result.context[EXPORTS[1]] == 60


@pytest.mark.parametrize("root", ROOTS)
@pytest.mark.parametrize("origin", ([], None, [61, 0, 3]))
def test_invalid_sleep_origin_rejects_before_rolls_costs_or_staging(controls, root, origin):
    result = preflight(
        controls,
        state(),
        "preflight_" + root,
        {
            "caster_state": "hero",
            "spell": "sleep",
            "slot_resource": "spell_slot_1",
            "targets": ["guard"],
            "targets_max_distance": 0,
            "environment_origin": origin,
        },
    )
    assert not result.success


@pytest.mark.parametrize("spell", SPELLS)
def test_exploration_keeps_existing_facts_without_scene(controls, spell):
    value = state(combat=False)
    value[NAMESPACE + ".state"].pop("spatial_scene")
    result = preflight(
        controls,
        value,
        "preflight_cast_spell",
        {
            "caster_state": "hero",
            "spell": spell,
            "slot_resource": "spell_slot_3" if spell == "fear" else "spell_slot_1",
            "targets": ["guard"],
            "target": "guard",
            "targets_max_distance": 25,
            "all_targets_have_clear_path": False,
        },
    )
    assert result.success, result.error
    assert result.context["_control_geometry_targets"] == ["guard"]
    assert result.context["_control_geometry_targets_max_distance"] == 25
    assert result.context["_control_geometry_all_targets_have_clear_path"] is False
    assert result.context["_control_geometry_target"] == value[NAMESPACE + ".entities"]["guard"]
    assert result.context["_control_geometry__target_id"] == "guard"


@pytest.mark.parametrize(("count", "accepted"), [(8, True), (9, False)])
def test_actual_recipient_bound_preserves_approved_eight_creature_adaptation(
    controls, count, accepted
):
    value = state((2, 0, 0), hero=(-30, 0, 0))
    bodies = value[NAMESPACE + ".state"]["spatial_scene"]["entities"]
    actors = value[NAMESPACE + ".entities"]
    positions = (
        [0, 2, 0],
        [-2, 0, 0],
        [0, -2, 0],
        [2, 2, 0],
        [-2, 2, 0],
        [-2, -2, 0],
        [2, -2, 0],
        [3.4, 1.5, 0],
    )
    for index in range(count - 1):
        identity = "extra-" + str(index)
        body = _scene_entity(
            identity, positions[index], footprint={"kind": "circle", "radius": 0.4}
        )
        body["id"] += "-body"
        bodies.append(body)
        actors[identity] = {**deepcopy(actors["guard"]), "name": identity}
    result = qualify(controls, value, "sleep", environment_origin=[0, 0, 3])
    assert result.success is accepted, result.error
    if accepted:
        assert len(result.context[EXPORTS[0]]) == 8


@pytest.mark.parametrize("root", ROOTS)
@pytest.mark.parametrize("spell", SPELLS)
def test_empty_actual_area_is_reported_then_rejected_by_current_nonempty_paid_contract(
    controls, root, spell
):
    value = state((-10, 0, 0)) if spell == "fear" else state((20, 0, 0), hero=(-30, 0, 0))
    intent = (
        {"instant_area_direction": [1, 0]} if spell == "fear" else {"environment_origin": [0, 0, 3]}
    )
    geometry = qualify(controls, value, spell, **intent)
    assert geometry.success, geometry.error
    assert geometry.context[EXPORTS[0]] == []
    result = preflight(
        controls,
        value,
        "preflight_" + root,
        {
            "caster_state": "hero",
            "spell": spell,
            "slot_resource": "spell_slot_3" if spell == "fear" else "spell_slot_1",
            "targets": ["guard"],
            **intent,
        },
    )
    assert not result.success
    assert "at least one actual affected creature" in result.error
