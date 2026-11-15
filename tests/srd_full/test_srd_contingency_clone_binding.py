"""Actual HP death hooks keep their original witness and every unrelated sibling."""

import ast
import hashlib
import os
import re
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_full.srd_contingency_clone_order import (
    CLONE,
    CORES,
    DAMAGE_OWNERS,
    OBSERVER,
    _bind_owner,
    _observation,
    bind_tools,
)
from scripts.srd_full.srd_control_behaviors import _call, _walk, _when
from scripts.srd_full.srd_qualified_packet_implementations import COHORTS, RENDER, _delegate
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(os.environ.get("FC14_CONTINGENCY_SOURCE_ROOT", ROOT))
PREFIX = Path(
    os.environ.get(
        "FC14_CONTINGENCY_HP_PREFIX",
        ROOT / ".scratch/contingency-jar-order-v2/excluded-root43-prefix.yaml",
    )
)
PREFIX_SHA = "7119e686568681da02851a6e2e06981e5723d9d0a3cb94ab33d75d614e36724a"
BODY_SHA = "7fc681b770e3adf719f54a6a825326234528633cba92ff5bc61222ec241c814c"


def _actual_body_hook(subject):
    """Execute only the unchanged actual binder function from the qualified source AST."""
    path = SOURCE / "scripts/srd_body_soul_spells.py"
    content = path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == BODY_SHA
    syntax = ast.parse(content)
    hooks = [
        node
        for node in ast.walk(syntax)
        if isinstance(node, ast.FunctionDef) and node.name == "hook_actual_death"
    ]
    assert len(hooks) == 1
    namespace = {"_when": _when, "_call": _call, "subject": subject}
    module = ast.fix_missing_locations(ast.Module(body=[hooks[0]], type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)
    return namespace["hook_actual_death"]


@pytest.fixture(scope="module")
def actual_owners():
    """Parse four authentic source fragments, without loading or admitting the whole runtime."""
    content = PREFIX.read_bytes()
    assert hashlib.sha256(content).hexdigest() == PREFIX_SHA
    text = content.decode()
    result = {}
    for name in (*DAMAGE_OWNERS, "cast_spell_instantaneous_threshold_death_damage"):
        match = re.search(
            rf"(?ms)^  {name}:\n.*?(?=^  [A-Za-z_][A-Za-z0-9_]*:|^definitions:|^procedures:)",
            text,
        )
        assert match is not None, name
        tool = _load_runtime("tools:\n" + match[0])["tools"][name]
        assert not any(node.get("call") == CLONE for node in _walk(tool["effects"]))
        subject = "target" if name in DAMAGE_OWNERS else "_target_id"
        _actual_body_hook(subject)(tool["effects"])
        assert any(node.get("call") == CLONE for node in _walk(tool["effects"]))
        result[name] = tool
    return result


def _sequences(sequence):
    yield sequence
    for node in sequence:
        for branch in ("on_true", "on_false"):
            if branch in node:
                yield from _sequences(node[branch])
        if "for_each" in node:
            yield from _sequences(node["for_each"]["do"])


@pytest.mark.parametrize("name", DAMAGE_OWNERS)
def test_actual_hp_clone_branch_observes_before_replacement_and_repeats_identically(
    actual_owners, name
):
    original = actual_owners[name]
    tool = deepcopy(original)
    _bind_owner(tool, name)
    callbacks = [
        (sequence, index)
        for sequence in _sequences(tool["effects"])
        for index, node in enumerate(sequence)
        if node.get("call") == CLONE
    ]
    assert callbacks
    assert all(sequence[index - 1] == _observation() for sequence, index in callbacks)
    assert all(index > 0 for _, index in callbacks)
    once = deepcopy(tool)
    _bind_owner(tool, name)
    assert tool == once
    for sequence in _sequences(tool["effects"]):
        sequence[:] = [node for node in sequence if node != _observation()]
    assert tool == original


def test_adapter_preserves_non_hp_death_and_rejects_an_unwitnessed_hp_owner(actual_owners):
    tools = deepcopy(actual_owners) | {CLONE: {}, OBSERVER: {}}
    original = deepcopy(tools)
    changed = bind_tools(tools)
    assert set(changed) == set(DAMAGE_OWNERS)
    assert tools == original
    non_hp = "cast_spell_instantaneous_threshold_death_damage"
    assert non_hp not in changed and tools[non_hp] == original[non_hp]
    for sequence in _sequences(tools["apply_srd_damage"]["effects"]):
        sequence[:] = [
            node for node in sequence if node.get("set", {}).get("field") != "_hp_before"
        ]
    before = deepcopy(tools)
    with pytest.raises(ValueError, match="witnessed physical HP transition"):
        bind_tools(tools)
    assert tools == before


def test_factored_qualified_entries_patch_their_one_real_core_once(actual_owners):
    tools = deepcopy(actual_owners) | {CLONE: {}, OBSERVER: {}}
    drain = {
        "condition": "lasting_state.has_dream_spells == true",
        "on_true": [{"call": "drain_srd_dream_wakes", "params": {}}],
    }
    for core, entry, *_ in COHORTS:
        tools[core] = deepcopy(tools[entry])
        tools[entry] = _delegate(tools[entry], core, True, [drain])
    original = deepcopy(tools)
    changed = bind_tools(tools)
    assert set(changed) == {"apply_srd_damage", "apply_srd_composite_damage", *CORES}
    assert tools == original
    again = bind_tools(tools | changed)
    assert again == changed
    entry = "apply_srd_qualifying_damage"
    for mutation in ("parameter", "export", "render", "suffix", "core"):
        altered = deepcopy(tools)
        call = altered[entry]["effects"][0]
        if mutation == "parameter":
            call["params"]["target"] = "{source_state_id}"
        elif mutation == "export":
            call["export_context"] = {"_hp_before": "_hp_before"}
        elif mutation == "render":
            call["params"][RENDER] = False
        elif mutation == "suffix":
            altered[entry]["effects"].append({"set": {"field": "_changed", "value": True}})
        else:
            call["call"] = "execute_srd_qualified_composite_packet"
        before = deepcopy(altered)
        with pytest.raises((ValueError, SystemExit), match="exact.*delegate|Packet reuse"):
            bind_tools(altered)
        assert altered == before
