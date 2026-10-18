"""Check saved combat facts before the retained scalar spell consumers."""

import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_combat_target_facts import FIELDS, PROJECT, bind_combat_target_facts
from scripts.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.runtime import RulesetRuntime
from tests.test_srd_lite_instant_combat_areas import preflight
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _wall
from tests.test_srd_lite_vertical_cones import state_for


@pytest.fixture(scope="module")
def facts():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    profiles = deepcopy(data["definitions"]["spells"])
    headers = deepcopy(data["definitions"]["spell_casting"])
    paid = {
        name: deepcopy(tool)
        for name, tool in data["tools"].items()
        if name.startswith("cast_spell_")
    }
    recovery = deepcopy(data["tools"]["cast_srd_recovery"])
    selected = bind_combat_target_facts(data)
    assert len(selected) == 22
    assert {"greater_restoration", "revivify"} <= set(selected)
    assert "dispel_magic" not in selected
    assert data["tools"]["cast_srd_recovery"] == recovery
    assert profiles == data["definitions"]["spells"]
    assert headers == data["definitions"]["spell_casting"]
    for name, original in paid.items():
        expected = deepcopy(original)
        expected["parameters"] = [
            {**parameter, "type": "number"}
            if parameter.get("name") == "target_distance" and parameter.get("type") == "integer"
            else parameter
            for parameter in expected["parameters"]
        ]
        assert data["tools"][name] == expected
    for name in ("cast_spell_healing", "cast_spell_instantaneous_restoration"):
        assert (
            next(p for p in data["tools"][name]["parameters"] if p["name"] == "target_distance")[
                "type"
            ]
            == "number"
        )
    pending = [PROJECT]
    for root in ("cast_spell", "begin_srd_spell_action"):
        tool = deepcopy(data["tools"][root])
        tool["effects"] = tool["effects"][: len(FIELDS) + 3]
        tool["pre_conditions"] = []
        data["tools"]["preflight_" + root] = tool
        pending.append("preflight_" + root)
    closure = {}
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in ("tools", "procedures")}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


def state(position=(10, 0, 0), *, combat=True, plane="material", lighting="bright", geometry=()):
    value = state_for(list(position), geometry)
    scene = value[NAMESPACE + ".state"]["spatial_scene"]
    scene["map"]["bounds"] = {"min": [-200, -200, -100], "max": [200, 200, 100]}
    scene["map"]["grid"]["origin"] = [-200, -200]
    value[NAMESPACE + ".combat"]["encounter_active"] = combat
    for body in value[NAMESPACE + ".state"]["spatial_scene"]["entities"]:
        body["id"] += "-body"
        if body["metadata"]["participant_ref"] == "guard":
            body["metadata"].update(plane=plane, lighting=lighting)
            body["footprint"] = {"kind": "circle", "radius": 0.4}
    for actor in value[NAMESPACE + ".entities"].values():
        actor.update(darkvision_feet=0, blindsight_feet=0, truesight_feet=0, senses={})
    return value


def check(parsed, value, spell="heal", **parameters):
    return preflight(
        parsed,
        value,
        "preflight_cast_spell",
        {
            "spell": spell,
            "caster_state": "hero",
            "target": "guard",
            "target_distance": 1,
            "caster_can_see_target": True,
            "caster_can_hear_target": True,
            "caster_can_touch_target": True,
            "cover": "none",
            **parameters,
        },
    )


@pytest.mark.parametrize(
    ("spell", "limit"),
    [
        ("cure_wounds", 5), ("heal", 60), ("blight", 30), ("guiding_bolt", 120),
        ("greater_restoration", 5), ("revivify", 5),
    ],
)
def test_actual_range_boundary_precedes_payment(facts, spell, limit):
    legal = check(facts, state((limit, 0, 0)), spell)
    assert legal.success, legal.error
    assert legal.context["_coord_fact_target_distance"] == limit
    illegal = check(facts, state((limit + 0.0001, 0, 0)), spell)
    assert not illegal.success
    assert "actual combat recipient" in illegal.error


def test_fractional_vertical_distance_uses_saved_positions(facts):
    result = check(facts, state((3, 4, 2)))
    assert result.success, result.error
    assert result.context["_coord_fact_target_distance"] == pytest.approx(29**0.5)
    assert result.context["_coord_fact_caster_can_touch_target"] is False


@pytest.mark.parametrize("spell", ["greater_restoration", "revivify"])
@pytest.mark.parametrize("root", ["preflight_cast_spell", "preflight_begin_srd_spell_action"])
def test_recovery_uses_actual_touch_without_requiring_target_sight(facts, spell, root):
    value = state((4.75, 0, 0), lighting="darkness")
    target = value[NAMESPACE + ".entities"]["guard"]
    target.update(hp=0, is_alive=False, conditions=["dead"])
    result = preflight(
        facts, value, root,
        {
            "spell": spell, "caster_state": "hero", "target": "guard",
            "target_distance": 999, "caster_can_touch_target": False,
            "caster_can_see_target": True,
        },
    )
    assert result.success, result.error
    assert result.context["_coord_fact_target_distance"] == 4.75
    assert result.context["_coord_fact_caster_can_touch_target"] is True
    assert result.context["_coord_fact_caster_can_see_target"] is False
    illegal = preflight(
        facts, state((5.0001, 0, 0)), root,
        {
            "spell": spell, "caster_state": "hero", "target": "guard",
            "target_distance": 0, "caster_can_touch_target": True,
        },
    )
    assert not illegal.success
    assert "actual combat recipient" in illegal.error


@pytest.mark.parametrize("root", ["preflight_cast_spell", "preflight_begin_srd_spell_action"])
def test_false_supplied_facts_do_not_block_a_legal_visible_combat_recipient(facts, root):
    result = preflight(
        facts,
        state((4.5, 0, 0)),
        root,
        {
            "spell": "cure_wounds",
            "caster_state": "hero",
            "target": "guard",
            "target_distance": 999,
            "caster_can_see_target": False,
            "caster_can_hear_target": False,
            "caster_can_touch_target": False,
            "cover": "total",
        },
    )
    assert result.success, result.error
    assert result.context["_coord_fact_target_distance"] == 4.5
    assert result.context["_coord_fact_caster_can_see_target"] is True
    assert result.context["_coord_fact_caster_can_hear_target"] is True
    assert result.context["_coord_fact_caster_can_touch_target"] is True
    assert result.context["_coord_fact_cover"] == "none"


@pytest.mark.parametrize("plane", ["ethereal", "other-plane"])
def test_cross_plane_forged_recipient_rejected(facts, plane):
    result = check(facts, state((4, 0, 0), plane=plane), "cure_wounds")
    assert not result.success
    assert "actual combat recipient" in result.error


def test_total_cover_forged_clear_path_rejected(facts):
    result = check(facts, state(geometry=[_wall()]))
    assert not result.success
    assert "actual combat recipient" in result.error


def test_darkness_overrides_supplied_sight_without_spending(facts):
    result = check(facts, state(lighting="darkness"))
    assert not result.success
    assert "cannot be seen" in result.error


def test_actual_hearing_and_blindness_override_supplied_senses(facts):
    value = state()
    value[NAMESPACE + ".entities"]["hero"]["conditions"] = ["blinded", "deafened"]
    result = check(facts, value, "guiding_bolt")
    assert result.success, result.error
    assert result.context["_coord_fact_caster_can_see_target"] is False
    assert result.context["_coord_fact_caster_can_hear_target"] is False


def test_three_quarters_cover_uses_actual_body_exposure(facts):
    side = _wall()
    side["id"] = "side"
    side["volume"]["max"][1] = 0.05
    result = check(facts, state(geometry=[_wall(height=2), side]), cover="none")
    assert result.success, result.error
    assert result.context["_coord_fact_cover"] == "three_quarters"


@pytest.mark.parametrize(
    ("geometry", "expected"),
    [
        ([_wall(height=0.6)], "none"),
        ([_wall(height=2)], "half"),
    ],
)
def test_partial_cover_follows_the_established_exposure_threshold(facts, geometry, expected):
    result = check(facts, state(geometry=geometry), cover="three_quarters")
    assert result.success, result.error
    assert result.context["_coord_fact_cover"] == expected


def test_exploration_preserves_fiction_facts_without_a_map(facts):
    value = state(combat=False)
    del value[NAMESPACE + ".state"]["spatial_scene"]
    result = check(
        facts,
        value,
        "cure_wounds",
        target_distance=2.25,
        caster_can_touch_target=False,
        cover="half",
    )
    assert result.success, result.error
    assert result.context["_coord_fact_target_distance"] == 2.25
    assert result.context["_coord_fact_caster_can_touch_target"] is False
    assert result.context["_coord_fact_cover"] == "half"


@pytest.mark.parametrize("spell", ["blur", "fireball", "commune", "dispel_magic"])
def test_unrelated_or_self_spell_preflight_does_not_require_a_map(facts, spell):
    value = state()
    del value[NAMESPACE + ".state"]["spatial_scene"]
    result = check(facts, value, spell)
    assert result.success, result.error
