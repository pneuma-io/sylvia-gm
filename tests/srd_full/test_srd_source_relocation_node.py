"""The relocation seam retains the final owning guards and avoids observer cycles."""

from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_source_relocation_node import NODE, WRAPPER, bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_spell_floating_disk as disks


def test_node_reuses_exact_final_mutation_guards_params_and_observer_locals():
    text = Path("rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    original = _load_runtime(text)["tools"][WRAPPER]
    index = next(i for i, e in enumerate(original["effects"]) if "spatial_mutation" in e)
    split = bind_runtime_consumers(text)
    tools = _load_runtime(split)["tools"]
    assert tools[NODE]["parameters"] == original["parameters"]
    assert tools[NODE]["pre_conditions"] == original["pre_conditions"]
    owning_effects = original["effects"][: index + 1]
    assert tools[NODE]["effects"][-len(owning_effects) :] == owning_effects
    defaults = tools[NODE]["effects"][: -len(owning_effects)]
    assert {effect["set"]["field"] for effect in defaults} >= {
        "_symbol_entry_previous_position",
        "_symbol_entry_previous_plane",
    }
    assert all(effect["set"]["value"] is None for effect in defaults)
    assert tools[WRAPPER]["effects"][1:] == original["effects"][index + 1 :]
    exports = set(tools[WRAPPER]["effects"][0]["export_context"])
    assert isinstance(tools[WRAPPER]["effects"][0]["export_context"], dict)
    assert {
        "_travel_mover",
        "_travel_scene_move",
        "_travel_guard_needed",
        "_travel_guard_target",
        "_symbol_entry_previous_position",
        "_symbol_entry_previous_plane",
    } <= exports
    assert _load_runtime(bind_runtime_consumers(split)) == _load_runtime(split)


def test_actual_force_follow_cycle_is_rejected_and_same_source_node_admits():
    green = disks._runtime_bytes().decode()
    tools = _load_runtime(green)["tools"]
    changes = {}

    def observe_again(value):
        if isinstance(value, list):
            return [observe_again(item) for item in value]
        if isinstance(value, dict):
            result = {key: observe_again(item) for key, item in value.items()}
            if result.get("call") == NODE:
                result["call"] = WRAPPER
            return result
        return value

    for name in (
        "load_srd_floating_disk",
        "unload_srd_floating_disk",
        "follow_srd_floating_disk",
        "drop_srd_floating_disk_cargo",
    ):
        tool = deepcopy(tools[name])
        tool["effects"] = observe_again(tool["effects"])
        changes[name] = tool
    red = _replace_tools(green, changes)
    name = "cast_srd_floating_disk"
    declaration = ToolDeclaration(
        name=name,
        capability="dnd.srd.5.2.1.spells",
        input_schema=owned_payload(_tool_schema(tools[name], tool_name=name)),
        writers=("dm",),
        turn_statuses=("open",),
        state_predicates=(),
    )
    with pytest.raises(PackageRuntimeError, match="static call cycle"):
        materialize_package_mechanic(
            package_id=disks.SRD,
            package_version="0.1.0",
            package_sha256="f" * 64,
            runtime_bytes=red.encode(),
            declaration=declaration,
        )
    materialize_package_mechanic(
        package_id=disks.SRD,
        package_version="0.1.0",
        package_sha256="e" * 64,
        runtime_bytes=green.encode(),
        declaration=declaration,
    )
