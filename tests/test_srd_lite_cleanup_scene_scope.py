"""Prove empty cleanup is a no-op without weakening actual queued work."""

import os
import zipfile
from copy import deepcopy

import pytest
from scripts.srd_lite_cleanup_scene_scope import CLEANUPS, bind_cleanup_scene_scope
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.test_srd_lite_combat_target_facts import state
from tests.test_srd_lite_instant_combat_areas import preflight

from sylvia_gm.runtime import RulesetRuntime


@pytest.fixture(scope="module")
def cleanup():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/sylvia.srd-lite.bg3/runtime/ruleset.yaml").decode()
        )
    before = {name: deepcopy(data["tools"][name]) for name in CLEANUPS}
    assert bind_cleanup_scene_scope(data) == list(CLEANUPS)
    for name, original in before.items():
        fixed = data["tools"][name]
        assert fixed["parameters"] == original["parameters"]
        assert fixed.get("pre_conditions", []) == original.get("pre_conditions", [])
        assert fixed["effects"][1:] == original["effects"][3:]
        assert fixed["effects"][0]["on_true"] == original["effects"][:3]
    # A pending queue without a map stops at the original scene requirement.
    # The empty path uses the original private defaults and does no spatial work.
    return RulesetRuntime(ruleset_dict={"tools": {name: data["tools"][name] for name in CLEANUPS}})


@pytest.mark.parametrize("name,flag", CLEANUPS.items())
@pytest.mark.parametrize(
    "pending,stack,expected_success",
    ((None, [], True), (False, [], True), (True, [], False), (True, ["ongoing"], True)),
)
def test_cleanup_snapshot_requires_actual_queued_work(
    cleanup, name, flag, pending, stack, expected_success
):
    value = state(combat=False)
    world = value["sylvia.srd-lite.bg3.state"]
    del world["spatial_scene"]
    world[flag.removeprefix("lasting_state.")] = pending
    world["pending_srd_action_stack"] = stack
    result = preflight(cleanup, value, name, {})
    if not expected_success:
        assert not result.success and "needs the admitted spatial scene" in result.error
    else:
        assert result.success, result.error
