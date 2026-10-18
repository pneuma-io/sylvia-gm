"""Prove encounter scene admission before unchanged initiative and combat behavior."""

import json
import os
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_lite_combat_scene_setup import (
    ROOTS,
    _pair_roll_widgets,
    _setup_effects,
    bind_combat_scene_setup,
)
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _wall
from tests.test_srd_lite_multi_target_facts import scene

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._schema_validator import validate_parameter_declarations
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget


@pytest.fixture(scope="module")
def setup():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    record = json.loads(
        Path(os.environ["SRD_LITE_CANDIDATE"]).with_name("package-record.json").read_text()
    )
    original_record = deepcopy(record)
    original = deepcopy(data)
    assert bind_combat_scene_setup(data, record) == list(ROOTS)
    assert not validate_parameter_declarations(data)
    assert data["definitions"] == original["definitions"]
    for name in data["tools"]:
        if name not in ROOTS:
            assert data["tools"][name] == original["tools"][name]
    expected_record = deepcopy(original_record)
    for declaration in expected_record["tools"]:
        if declaration["name"] in ROOTS:
            declaration["input_schema"]["properties"]["combat_scenes"] = {
                "type": "array", "items": {"type": "object"}, "minItems": 0, "maxItems": 1
            }
    assert record == expected_record
    pending = []
    for name in ROOTS:
        tool = data["tools"][name]
        suffix = _pair_roll_widgets() if name == ROOTS[0] else []
        assert tool["effects"] == (
            _setup_effects(name) + original["tools"][name]["effects"] + suffix
        )
        assert tool["pre_conditions"] == original["tools"][name]["pre_conditions"]
        assert tool["parameters"][:-1] == original["tools"][name]["parameters"]
        declaration = next(row for row in record["tools"] if row["name"] == name)
        assert declaration["input_schema"]["properties"]["combat_scenes"] == {
            "type": "array", "items": {"type": "object"}, "minItems": 0, "maxItems": 1
        }
        assert "combat_scenes" not in declaration["input_schema"]["required"]
        data["tools"]["preflight_" + name] = {
            **deepcopy(tool), "effects": tool["effects"][: len(_setup_effects(name))]
        }
        pending.append("preflight_" + name)
    closure = {}
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in ("tools", "procedures")}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


def initial():
    value = scene(first=(4.5, 0, 0), second=(29.5, 0, 0), combat=False)
    for actor in value[NAMESPACE + ".entities"].values():
        actor["initiative_modifier"] = 0
    value[NAMESPACE + ".combat"]["turn_active"] = False
    saved_scene = value[NAMESPACE + ".state"].pop("spatial_scene")
    return value, saved_scene


def check(parsed, root, value, selected=None):
    """Use the real scene bridge without RNG consumption or a persisted campaign write."""
    parameters = {"combatants": ["hero", "guard", "other"]} if root == ROOTS[1] else {
        "first_actor": "hero", "second_actor": "guard", "tie_winner": "first",
        "first_surprised": False, "second_surprised": False,
    }
    if selected is not None:
        parameters["combat_scenes"] = [selected]
    before = json.dumps(value, sort_keys=True)
    rng = dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, value)

    def nested(name, values, parent):
        return execute_tool(
            name, values, parsed.tools, bridge, parsed.ruleset_config, parsed.definitions,
            combat_state=parent.combat_state, tool_executor=nested, budget=parent.budget,
        )

    result = execute_tool(
        "preflight_" + root, parameters, parsed.tools, bridge, parsed.ruleset_config,
        parsed.definitions, combat_state=value[NAMESPACE + ".combat"],
        tool_executor=nested, budget=ExecutionBudget(),
    )
    assert dice._rng.getstate() == rng
    assert json.dumps(value, sort_keys=True) == before
    return result, bridge


@pytest.mark.parametrize("root", ROOTS)
def test_fresh_encounter_admits_fractional_positions_before_initiative(setup, root):
    value, selected = initial()
    result, bridge = check(setup, root, value, selected)
    assert result.success, result.error
    assert bridge.read_field("state", "spatial_scene") == selected
    assert result.context["_setup_snapshot"]["geometry_revision"] == selected["map"]["revision"]
    assert result.context["_setup_body"]["position"] == (
        [29.5, 0, 0] if root == ROOTS[1] else [4.5, 0, 0]
    )


@pytest.mark.parametrize("root", ROOTS)
def test_missing_scene_rejects_before_initiative(setup, root):
    value, _ = initial()
    result, bridge = check(setup, root, value)
    assert not result.success and "needs one combat_scenes" in result.error
    assert not bridge.mutations()


@pytest.mark.parametrize("root", ROOTS)
def test_saved_scene_reused_without_resetting_revision_or_persistent_objects(setup, root):
    value, selected = initial()
    selected["map"]["revision"] = 17
    obstacle = _wall()
    obstacle.update(
        id="saved-obstacle",
        volume={"kind": "box", "min": [40, 0, 0], "max": [41, 1, 6]},
    )
    selected["geometry"] = [obstacle]
    value[NAMESPACE + ".state"]["spatial_scene"] = selected
    result, bridge = check(setup, root, value)
    assert result.success, result.error
    assert not bridge.mutations()
    assert bridge.read_field("state", "spatial_scene") == selected
    result, bridge = check(setup, root, value, deepcopy(selected))
    assert not result.success and "already saved" in result.error
    assert not bridge.mutations()


@pytest.mark.parametrize("root", ROOTS)
@pytest.mark.parametrize(
    "defect", ["bounds", "unit", "missing", "inactive", "ambiguous", "object", "plane", "binding"]
)
def test_invalid_scene_or_last_combatant_rejects_before_initiative(setup, root, defect):
    value, selected = initial()
    target_index = 2 if root == ROOTS[1] else 1
    body = selected["entities"][target_index]
    if defect == "bounds":
        body["position"] = [1000, 0, 0]
    elif defect == "unit":
        selected["map"]["grid"]["unit"] = "m"
    elif defect == "missing":
        selected["entities"].pop(target_index)
    elif defect == "inactive":
        body["active"] = False
    elif defect == "ambiguous":
        duplicate = deepcopy(body)
        duplicate["id"] += "-second"
        selected["entities"].append(duplicate)
    elif defect == "object":
        body["metadata"]["is_object"] = True
    elif defect == "plane":
        del body["metadata"]["plane"]
    elif defect == "binding":
        body["id"] = body["metadata"]["participant_ref"]
        body["metadata"]["participant_ref"] = "unrelated"
    result, _ = check(setup, root, value, selected)
    assert not result.success, defect
