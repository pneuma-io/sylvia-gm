"""The actual Travel split preserves Meld guards and every movement observer."""

import ast
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from scripts import srd_meld_stone_consumers as meld
from scripts import srd_timed_actor_defenses as effects
from scripts import srd_timed_senses as senses
from scripts.srd_full.srd_scene_relations import _array, _tool_scene
from scripts.srd_full.srd_source_relocation_node import NODE, WRAPPER, bind_runtime_consumers

PARENT = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-meld-travel-composition/input-1"))
TRAVEL_SHA = "27b20c76cfd74f1742e0af6360f9de80cb1f6a1e3e9813d5fd636ef8c9a8ce5b"
MELD_SHA = "a0a467909fac6ec117737fd54d48c49de005777bc523405d4e0c5fdd43610fcc"


def _functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in selected} == set(names)
    exec(compile(ast.Module(selected, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


@lru_cache
def _original():
    path = PARENT / "srd_spell_travel.py"
    assert sha256(path.read_bytes()).hexdigest() == TRAVEL_SHA
    namespace = {**vars(effects), **vars(senses), "_array": _array, "_tool_scene": _tool_scene}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    active = next(
        n
        for n in tree.body
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "ACTIVE"
    )
    exec(compile(ast.Module([active], type_ignores=[]), str(path), "exec"), namespace)
    namespace = _functions(path, {"_scene_subject", "_tools"}, namespace)
    return namespace["_tools"]()[WRAPPER]


def _text(tool=None):
    return yaml.dump(
        {"tools": {WRAPPER: deepcopy(tool or _original())}, "procedures": {}},
        Dumper=yaml.CSafeDumper,
        sort_keys=False,
    )


def _guarded(tool):
    return meld._bind_travel({WRAPPER: deepcopy(tool)})[WRAPPER]


def test_frozen_late_meld_leaf_reproduces_exact_split_failure():
    path = PARENT / "srd_meld_stone_consumers.py"
    assert sha256(path.read_bytes()).hexdigest() == MELD_SHA
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "bind_consumers"
    )
    first = next(
        i
        for i, n in enumerate(function.body)
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "mover"
    )
    last = next(
        i
        for i in range(first, len(function.body))
        if isinstance(function.body[i], ast.Assign)
        and isinstance(function.body[i].targets[0], ast.Subscript)
        and ast.unparse(function.body[i].targets[0]) == "changed['move_srd_travel_subject']"
    )
    namespace = {
        **vars(meld),
        "tools": effects._load_runtime(bind_runtime_consumers(_text()))["tools"],
        "changed": {},
    }
    with pytest.raises(StopIteration):
        exec(
            compile(
                ast.Module(function.body[first : last + 1], type_ignores=[]), str(path), "exec"
            ),
            namespace,
        )


def test_exact_split_node_is_guarded_without_touching_wrapper_or_observers():
    text = bind_runtime_consumers(_text())
    before = effects._load_runtime(text)["tools"]
    changed = meld._bind_travel(before)
    assert set(changed) == {NODE}
    assert before[WRAPPER] == effects._load_runtime(text)["tools"][WRAPPER]
    expected = _guarded(before[NODE])
    assert changed[NODE] == expected
    assert changed[NODE]["parameters"] == before[NODE]["parameters"]
    assert changed[NODE]["pre_conditions"] == before[NODE]["pre_conditions"]
    cleaned = deepcopy(changed[NODE])
    cleaned["effects"] = [e for e in cleaned["effects"] if e not in meld._travel_guards()]
    assert cleaned == before[NODE]
    again = {**before, **changed}
    assert meld._bind_travel(again) == changed


def test_early_guard_split_retains_exact_one_guard_in_native_clone_source():
    guarded = _guarded(_original())
    split = effects._load_runtime(bind_runtime_consumers(_text(guarded)))["tools"]
    assert meld._bind_travel(split)[NODE] == split[NODE]
    assert sum(e == meld._travel_guards()[0] for e in split[NODE]["effects"]) == 1
    assert sum(e == meld._travel_guards()[1] for e in split[NODE]["effects"]) == 1
    mutation = next(i for i, e in enumerate(split[NODE]["effects"]) if "spatial_mutation" in e)
    for guard in meld._travel_guards():
        assert split[NODE]["effects"].index(guard) < mutation


def test_regenerated_owning_body_wins_over_stale_previous_node():
    tools = effects._load_runtime(bind_runtime_consumers(_text()))["tools"]
    prior_node = deepcopy(tools[NODE])
    tools[WRAPPER] = deepcopy(_original())
    changed = meld._bind_travel(tools)
    assert set(changed) == {WRAPPER}
    assert tools[NODE] == prior_node
    assert changed[WRAPPER] == _guarded(_original())


def test_actual_phantom_rider_capture_keeps_both_guards_before_its_mutation():
    path = PARENT / "srd_phantom_steed.py"
    assert sha256(path.read_bytes()).hexdigest() == (
        "725c48b2448174172c7b5086e645dcf98c3902e1b3b6780df5e36e82fcdb281e"
    )
    namespace = _functions(path, {"_walk", "_rider_clone"}, {**vars(effects)})
    tools = effects._load_runtime(bind_runtime_consumers(_text(_guarded(_original()))))["tools"]
    clones = namespace["_rider_clone"](tools)
    node = clones["move_srd_phantom_steed_rider_node"]
    mutation = next(i for i, e in enumerate(node["effects"]) if "spatial_mutation" in e)
    for guard in meld._travel_guards():
        assert node["effects"].count(guard) == 1
        assert node["effects"].index(guard) < mutation


def test_all_nontravel_meld_consumer_statements_are_preserved():
    parent = ast.parse((PARENT / "srd_meld_stone_consumers.py").read_text(encoding="utf-8"))
    current = ast.parse(Path(meld.__file__).read_text(encoding="utf-8"))
    old = next(
        n for n in parent.body if isinstance(n, ast.FunctionDef) and n.name == "bind_consumers"
    )
    new = next(
        n for n in current.body if isinstance(n, ast.FunctionDef) and n.name == "bind_consumers"
    )
    start = next(
        i
        for i, n in enumerate(old.body)
        if isinstance(n, ast.Assign)
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "mover"
    )
    stop = next(
        i
        for i in range(start, len(old.body))
        if isinstance(old.body[i], ast.Assign)
        and ast.unparse(old.body[i].targets[0]) == "changed['move_srd_travel_subject']"
    )
    old.body[start : stop + 1] = []
    new.body = [
        n
        for n in new.body
        if not (isinstance(n, ast.Expr) and ast.unparse(n) == "changed.update(_bind_travel(tools))")
    ]
    assert ast.dump(new) == ast.dump(old)


@pytest.mark.parametrize("change", ["foreign_param", "duplicate", "missing_node", "no_mutation"])
def test_unknown_forwarder_or_owner_shape_fails_clearly(change):
    tools = effects._load_runtime(bind_runtime_consumers(_text()))["tools"]
    if change == "foreign_param":
        tools[WRAPPER]["effects"][0]["params"]["subject"] = "{destination}"
    elif change == "duplicate":
        tools[WRAPPER]["effects"].append(deepcopy(tools[WRAPPER]["effects"][0]))
    elif change == "missing_node":
        del tools[NODE]
    else:
        tools[NODE]["effects"] = [e for e in tools[NODE]["effects"] if "spatial_mutation" not in e]
    with pytest.raises(ValueError, match="Meld"):
        meld._bind_travel(tools)
