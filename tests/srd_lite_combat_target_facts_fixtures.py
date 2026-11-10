"""Check saved combat facts before the retained scalar spell consumers."""

import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_combat_target_facts import FIELDS, PROJECT, bind_combat_target_facts
from scripts.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.runtime import RulesetRuntime
from tests.srd_lite_instant_combat_areas_fixtures import preflight
from tests.srd_lite_mass_cure_geometry_fixtures import NAMESPACE, _calls, _wall
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
