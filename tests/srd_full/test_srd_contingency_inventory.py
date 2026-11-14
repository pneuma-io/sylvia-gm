"""Static source binding preserves actual physical loss guards and all sibling effects."""

import importlib.util
import os
import sys
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from types import ModuleType

import pytest
from scripts.srd_full.srd_contingency_inventory import (
    CHECK,
    INVENTORY_OWNERS,
    _bind_inventory_loss,
    _callback,
    bind_tools,
)

ROOT = Path(__file__).resolve().parents[2]
CURRENT = Path(os.environ.get("FC14_CONTINGENCY_SOURCE_ROOT", ROOT))


@lru_cache
def _actual_owner_tools():
    """Construct genuine current source leaves without parsing a runtime or running a build."""
    dependency = CURRENT / "src/sylvia_gm/runtime/_parameter_declarations.py"
    if not (ROOT / "src/sylvia_gm/runtime/_parameter_declarations.py").exists():
        spec = importlib.util.spec_from_file_location(
            "sylvia_gm.runtime._parameter_declarations", dependency
        )
        declarations = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = declarations
        spec.loader.exec_module(declarations)
    package = ModuleType("_contingency_source_owners")
    package.__path__ = [str(CURRENT / "scripts")]
    sys.modules[package.__name__] = package

    def source(name):
        spec = importlib.util.spec_from_file_location(
            package.__name__ + "." + name, CURRENT / "scripts" / (name + ".py")
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    forms = source("srd_transformation_spells")
    bodies = source("srd_body_soul_spells")
    heat = source("srd_spell_heat_metal")
    return {
        "install_srd_transformation_source": forms._install_tool(),
        "settle_srd_clone_death": bodies._transfer_tools()["settle_srd_clone_death"],
        "settle_srd_clone_transfer": bodies._transfer_tools()["settle_srd_clone_transfer"],
        "drop_srd_heated_object": heat._grip_tools()["drop_srd_heated_object"],
    }


def _sequences(sequence):
    yield sequence
    for node in sequence:
        for branch in ("on_true", "on_false"):
            if branch in node:
                yield from _sequences(node[branch])
        if "for_each" in node:
            yield from _sequences(node["for_each"]["do"])


@pytest.mark.parametrize("name", INVENTORY_OWNERS)
def test_authentic_inventory_write_has_one_immediate_idempotent_contingency_check(name):
    original = _actual_owner_tools()[name]
    tool = deepcopy(original)
    _bind_inventory_loss(tool, name)
    pairs = [
        (sequence, index)
        for sequence in _sequences(tool["effects"])
        for index, node in enumerate(sequence)
        if node.get("set", {}).get("field") == "target_state.inventory"
    ]
    assert len(pairs) == 1
    sequence, index = pairs[0]
    assert sequence[index + 1] == _callback()
    once = deepcopy(tool)
    _bind_inventory_loss(tool, name)
    assert tool == once
    for sequence in _sequences(tool["effects"]):
        sequence[:] = [node for node in sequence if node != _callback()]
    assert tool == original


def test_binder_qualifies_the_actual_mutation_and_preserves_inputs_on_failure():
    tools = deepcopy(_actual_owner_tools())
    tools[CHECK] = {}
    original = deepcopy(tools)
    changed = bind_tools(tools)
    assert set(changed) == set(INVENTORY_OWNERS)
    assert tools == original
    tools["drop_srd_heated_object"]["effects"] = []
    before = deepcopy(tools)
    with pytest.raises(ValueError, match="exactly one actual"):
        bind_tools(tools)
    assert tools == before
