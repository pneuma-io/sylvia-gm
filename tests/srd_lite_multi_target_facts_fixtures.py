"""Prove all chosen combat recipients qualify before staging or paying for a spell."""

import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_combat_target_facts import FIELDS, PROJECT
from scripts.srd_lite_multi_target_facts import PREFIX_LENGTH, bind_multi_target_facts
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.srd_lite_combat_target_facts_fixtures import state
from tests.srd_lite_instant_combat_areas_fixtures import preflight
from tests.srd_lite_mass_cure_geometry_fixtures import NAMESPACE, _calls, _wall

from sylvia_gm.runtime import RulesetRuntime


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
    selected = bind_multi_target_facts(data)
    assert len(profiles) == 96
    assert len(selected) == 34
    assert profiles == data["definitions"]["spells"]
    assert headers == data["definitions"]["spell_casting"]
    for name, original in paid.items():
        expected = deepcopy(original)
        expected["parameters"] = [
            {**parameter, "type": "number"}
            if parameter.get("name") == "targets_max_distance"
            and parameter.get("type") == "integer"
            else parameter
            for parameter in expected["parameters"]
        ]
        assert data["tools"][name] == expected, name
    pending = [PROJECT]
    for root in ("cast_spell", "begin_srd_spell_action"):
        tool = deepcopy(data["tools"][root])
        tool["effects"] = tool["effects"][: PREFIX_LENGTH + len(FIELDS) + 3]
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


def scene(first=(4.5, 0, 0), second=(29.5, 0, 0), **options):
    value = state(first, **options)
    entities = value[NAMESPACE + ".entities"]
    entities["other"] = deepcopy(entities["guard"])
    body = deepcopy(value[NAMESPACE + ".state"]["spatial_scene"]["entities"][1])
    body.update(id="other-body", position=list(second))
    body["metadata"]["participant_ref"] = "other"
    value[NAMESPACE + ".state"]["spatial_scene"]["entities"].append(body)
    return value


def check(parsed, value, spell="aid", *, root="preflight_cast_spell", **parameters):
    return preflight(
        parsed,
        value,
        root,
        {
            "spell": spell,
            "caster_state": "hero",
            "target": None,
            "targets": ["guard", "other"],
            "targets_max_distance": 0,
            "caster_can_see_all_targets": True,
            "caster_can_touch_all_targets": True,
            "all_targets_have_clear_path": True,
            "target_distance": 0,
            "cover": "none",
            **parameters,
        },
    )
