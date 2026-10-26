"""The actual emitted longer-cast begin accepts Jar guards when optional conditions are absent."""

import ast
import json
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BEGIN = ROOT / "tests/fixtures/srd_magic_jar_emitted_begin.json"


def _actual_guard_helper():
    source = ast.parse((ROOT / "scripts/srd_magic_jar_consumers.py").read_text())
    function = next(
        node
        for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == "_precondition"
    )
    namespace = {}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "actual_jar_guard", "exec"), namespace
    )
    return namespace["_precondition"]


def test_actual_emitted_begin_keeps_jar_guard_once_without_optional_preconditions():
    begin = json.loads(BEGIN.read_text())
    assert "pre_conditions" not in begin
    original = deepcopy(begin)
    guard = _actual_guard_helper()
    condition = "caster_state.magic_jar_role == null or caster_state.magic_jar_role == 'possessed'"
    error = "The container's soul and the abandoned body cannot cast another spell."
    guard(begin, condition, error)
    assert begin["pre_conditions"] == [{"condition": condition, "error": error}]
    once = deepcopy(begin)
    guard(begin, condition, error)
    assert begin == once
    guard(begin, "caster_state.is_alive == true", "The actual caster must remain alive.")
    assert begin["pre_conditions"][0] == once["pre_conditions"][0]
    assert len(begin["pre_conditions"]) == 2
    del begin["pre_conditions"]
    assert begin == original
