"""Check the actual admitted movement chain before omitting its unreachable budget arm."""
import json
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_lite_combat_scene_reentry import (
    MOVEMENT_ERROR,
    omit_combat_movement_exploration_budget,
)

WORK = Path("D:/Codex/2026-10-09/sylvia-main-01a11b3b/integration-01a120e9")
SEGMENT = "move_srd_scene_creature_environment_segment"


@pytest.fixture
def native_chain():
    source = json.loads((WORK / "selected-additional-map-movement-owners-1138.json").read_text())
    assert source["normal_package_admission"] is True
    tools = {name: deepcopy(owner) for name, owner in source["tools"].items()
             if name.startswith("move_srd_scene_creature")}
    return {"tools": tools}, {"tools": [{"name": "move_srd_scene_creature"}]}


def test_actual_private_budget_pruning_preserves_every_combat_effect(native_chain):
    runtime, record = native_chain
    before = deepcopy(runtime)
    assert omit_combat_movement_exploration_budget(runtime, record) == SEGMENT
    branch = next(row for row in runtime["tools"][SEGMENT]["effects"]
                  if row.get("condition") == "combat.encounter_active == true")
    original = next(row for row in before["tools"][SEGMENT]["effects"]
                    if row.get("condition") == "combat.encounter_active == true")
    assert branch["on_false"] == [{"require": {
        "condition": "combat.encounter_active == true", "error": MOVEMENT_ERROR,
    }}]
    branch["on_false"] = original["on_false"]
    assert runtime == before


@pytest.mark.parametrize("drift", ["entry", "caller", "public"])
def test_budget_pruning_refuses_a_new_unguarded_route_before_mutation(native_chain, drift):
    runtime, record = native_chain
    if drift == "entry":
        runtime["tools"]["move_srd_scene_creature_node"]["pre_conditions"] = []
    elif drift == "caller":
        runtime["tools"]["unrelated_route"] = {"effects": [{"call": SEGMENT}]}
    else:
        record["tools"].append({"name": SEGMENT})
    before = deepcopy(runtime)
    with pytest.raises(ValueError, match="guarded entry|caller drift|separate declared entry"):
        omit_combat_movement_exploration_budget(runtime, record)
    assert runtime == before
