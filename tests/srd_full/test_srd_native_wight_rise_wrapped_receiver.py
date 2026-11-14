"""Native Fortitude binds inside the actual ordinary Magic Jar receiving branch."""

import ast
import inspect
import re
from copy import deepcopy
from pathlib import Path

import pytest
from scripts import srd_magic_jar_consumers as jar
from scripts.srd_full.srd_native_wight_rise import _bind_lethal
from scripts.srd_full.srd_spell_summoning import bind_undead_fortitude_damage_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

ROOT = Path(__file__).resolve().parents[2]


def _actual_wrapped_scalar():
    """Derive only the current scalar owner and unchanged Jar receiving statements."""
    text = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    block = re.search(r"(?ms)^  apply_srd_damage:\n.*?(?=^  [a-zA-Z0-9_]+:)", text)
    assert block is not None
    fragment = "tools:\n" + block.group() + "procedures: {}\n"
    tools = _load_runtime(bind_undead_fortitude_damage_consumers(fragment))["tools"]
    original = deepcopy(tools["apply_srd_damage"])
    function = ast.parse(inspect.getsource(jar.bind_consumers)).body[0]
    start = next(
        i
        for i, node in enumerate(function.body)
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "damage"
    )
    stop = next(
        i
        for i, node in enumerate(function.body[start:], start)
        if isinstance(node, ast.Assign)
        and ast.unparse(node.targets[0]) == "changed['apply_srd_damage']"
    )
    namespace = dict(jar.__dict__, tools=tools, changed={})
    module = ast.Module(body=function.body[start : stop + 1], type_ignores=[])
    exec(compile(module, "<actual Magic Jar scalar receiving owner>", "exec"), namespace)
    return original, namespace["changed"]["apply_srd_damage"]


def test_magic_jar_ordinary_damage_retains_native_fortitude_boundary():
    original, wrapped = _actual_wrapped_scalar()
    assert len(wrapped["effects"]) == 1
    branch = wrapped["effects"][0]
    assert branch["on_false"] == original["effects"]
    assert branch["on_true"][-1]["call"] == "apply_srd_magic_jar_occupied_damage"
    expected = _bind_lethal(original, False, False)
    bound = _bind_lethal(wrapped, False, False)
    assert bound["effects"][0]["on_false"] == expected["effects"]
    assert bound["effects"][0]["on_true"] == branch["on_true"]
    assert bound["parameters"] == wrapped["parameters"]
    assert bound["pre_conditions"] == wrapped["pre_conditions"]
    assert _bind_lethal(bound, False, False) == bound
    assert branch["on_false"] == original["effects"]

    duplicate = deepcopy(wrapped)
    duplicate["effects"][0]["on_false"].append(
        deepcopy(
            next(
                effect
                for effect in original["effects"]
                if effect.get("condition") == "target.hp == 0 and target.creature_kind == 'monster'"
            )
        )
    )
    with pytest.raises(ValueError, match="actual zero-HP pre-death boundary"):
        _bind_lethal(duplicate, False, False)
