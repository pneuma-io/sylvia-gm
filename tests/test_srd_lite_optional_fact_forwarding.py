"""Check the real retained dispatcher's optional fact handoff without paid effects."""

import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_attacks import _copy_optional_fact
from scripts.srd_lite_profiles import lists
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.test_srd_lite_combat_target_facts import state
from tests.test_srd_lite_instant_combat_areas import preflight

from sylvia_gm.runtime import RulesetRuntime

NAMES = {"caster_can_touch_all_targets", "targets_max_distance"}


@pytest.fixture(scope="module")
def handoff():
    with zipfile.ZipFile(os.environ["SRD_LITE_OPTIONAL_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/sylvia.srd-lite.bg3/runtime/ruleset.yaml").decode()
        )
    before = deepcopy(data["tools"]["finish_srd_retained_cast_tail"])
    public = deepcopy(data["tools"]["cast_spell"]["parameters"])
    after = data["tools"]["finish_srd_retained_cast_tail"]
    assert before["effects"] == after["effects"]
    assert public == data["tools"]["cast_spell"]["parameters"]
    assert before.get("pre_conditions", []) == after.get("pre_conditions", [])
    forwards = [
        row
        for rows in lists(data["tools"]["cast_spell"]["effects"])
        for row in rows
        if row.get("call") == "finish_srd_retained_cast_tail"
    ]
    assert len(forwards) == 1
    call = deepcopy(forwards[0])
    call["params"] = {key: value for key, value in call["params"].items() if key in NAMES}
    seeds = [
        deepcopy(row)
        for row in data["tools"]["cast_spell"]["effects"]
        if row.get("set", {}).get("field") in {"_multi_fact_" + name for name in NAMES}
    ]
    assert len(seeds) == 2
    parent = {
        "parameters": [row for row in public if row["name"] in NAMES],
        "effects": [*seeds, call],
    }
    original_parent = deepcopy(parent)
    parent["effects"] = [
        *[_copy_optional_fact(row["set"]["field"], row["set"]["value"]) for row in seeds],
        call,
    ]
    original_child = {
        "parameters": [row for row in before["parameters"] if row["name"] in NAMES],
        "effects": [],
    }
    fixed_child = {
        "parameters": [row for row in after["parameters"] if row["name"] in NAMES],
        "effects": [],
    }

    def parsed(parent, child):
        return RulesetRuntime(
            ruleset_dict={"tools": {"handoff": parent, "finish_srd_retained_cast_tail": child}}
        )

    return parsed(original_parent, original_child), parsed(parent, fixed_child)


def test_omitted_irrelevant_facts_reproduce_old_failure_and_now_forward(handoff):
    original, fixed = handoff
    result = preflight(original, state(), "handoff", {})
    assert not result.success and "must be a boolean" in result.error
    result = preflight(fixed, state(), "handoff", {})
    assert result.success, result.error
    assert result.context["_multi_fact_targets_max_distance"] is None
    assert result.context["_multi_fact_caster_can_touch_all_targets"] is None


@pytest.mark.parametrize(
    "touch,distance", [(True, 4.5), (False, 19.75), (True, 0), (False, 121.25)]
)
def test_explicit_facts_keep_their_values(handoff, touch, distance):
    _, fixed = handoff
    result = preflight(
        fixed,
        state(),
        "handoff",
        {"caster_can_touch_all_targets": touch, "targets_max_distance": distance},
    )
    assert result.success, result.error
    assert result.context["_multi_fact_caster_can_touch_all_targets"] is touch
    assert result.context["_multi_fact_targets_max_distance"] == distance
