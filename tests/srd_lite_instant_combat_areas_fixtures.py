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
from tests.srd_lite_mass_cure_geometry_fixtures import NAMESPACE, _calls, _scene_entity, _wall
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
