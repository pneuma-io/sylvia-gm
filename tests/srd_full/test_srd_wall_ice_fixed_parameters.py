"""Forced Ice movement binds constants without writing package combat state."""

from copy import deepcopy

from scripts.srd_full.srd_wall_ice_relocation import _fixed_parameters

from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime._effect_executor import _handle_set
from sylvia_gm.runtime._template_resolver import resolve_params
from sylvia_gm.runtime._types import ExecutionContext


def test_ice_fixed_parameters_reach_native_calls_without_mutating_combat_state():
    fixed = {
        "teleport": False,
        "levitate_source_id": "",
        "telekinesis_source_id": "",
        "sphere_source_id": "",
    }
    tool = {
        "parameters": [
            {"name": "subject"},
            {"name": "teleport", "type": "boolean", "required": False, "default": True},
            *[
                {"name": name, "required": False, "default": "", "model_hidden": True}
                for name in fixed
                if name != "teleport"
            ],
        ],
        "effects": [
            {
                "set": {
                    "field": "_constant_receipt",
                    "value": {
                        **{name: name for name in fixed},
                        "body_teleport": "_body.metadata.teleport",
                        "body_source": "_body.metadata.sphere_source_id",
                        "literal_name": "'teleport'",
                        "physical_guard": "teleport == false and _body.metadata.teleport == true",
                    },
                }
            },
            {
                "call": "check_srd_barrier_movement",
                "params": {
                    "subject": "{subject}",
                    "teleport": "{teleport}",
                    "sphere_source_id": "{sphere_source_id}",
                },
            },
            {
                "call": "check_srd_levitate_movement",
                "params": {"teleport": "{teleport}", "levitate_source_id": "{levitate_source_id}"},
            },
            {
                "call": "check_srd_telekinesis_movement",
                "params": {
                    "teleport": "{teleport}",
                    "telekinesis_source_id": "{telekinesis_source_id}",
                },
            },
        ],
    }
    original = deepcopy(tool)
    removed = _fixed_parameters(tool)
    assert removed == set(fixed)
    assert tool["parameters"] == [original["parameters"][0]]

    receipt = tool["effects"][len(fixed)]["set"]["value"]
    assert receipt.keys() == original["effects"][0]["set"]["value"].keys()
    assert receipt["body_teleport"] == "_body.metadata.teleport"
    assert receipt["body_source"] == "_body.metadata.sphere_source_id"
    assert receipt["literal_name"] == "'teleport'"

    bridge = AuthorityMemoryStateBridge("ice-fixed-parameter-probe", {})
    context = ExecutionContext(
        params={"subject": "actual-body"},
        vars={"_body": {"metadata": {"teleport": True, "sphere_source_id": "other-source"}}},
        state_bridge=bridge,
        combat_state=bridge.combat_state,
    )
    for effect in tool["effects"][: len(fixed) + 1]:
        _handle_set(effect["set"], effect, context, None)

    actual = context.vars["_constant_receipt"]
    assert actual["teleport"] is False
    assert {name: actual[name] for name in fixed} == fixed
    assert actual["physical_guard"] is True
    assert actual["body_teleport"] is True
    assert actual["body_source"] == "other-source"
    assert actual["literal_name"] == "teleport"
    exports = {}
    for effect in tool["effects"][len(fixed) + 1 :]:
        supplied = resolve_params(effect["params"], context)
        assert supplied["teleport"] is False
        exports.update({name: value for name, value in supplied.items() if name in fixed})
    assert exports == fixed
    assert "" not in bridge.combat_state
    assert bridge.combat_state == {}
    assert bridge.mutations() == ()
