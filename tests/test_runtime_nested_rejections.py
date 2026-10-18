"""Nested rule rejections retain their explanation without disguising execution faults."""

import pytest

from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ToolDef


@pytest.mark.parametrize("rule_check", [True, False])
def test_nested_failure_preserves_its_kind_across_two_calls(rule_check):
    message = "Required test supply is unavailable."
    leaf = {"require": {"condition": "false", "error": message}} if rule_check else {"unknown": {}}
    definitions = {
        "outer": ToolDef(name="outer", effects=[{"call": "middle"}]),
        "middle": ToolDef(name="middle", effects=[{"call": "leaf"}]),
        "leaf": ToolDef(name="leaf", effects=[leaf]),
    }
    bridge = AuthorityMemoryStateBridge("example.test", {})

    def nested(name, parameters, _context):
        return execute_tool(name, parameters, definitions, bridge, {}, {}, tool_executor=nested)

    result = nested("outer", {}, None)
    assert not result.success
    assert result.failure_kind == ("precondition" if rule_check else "effect_execution")
    if rule_check:
        assert result.error == message
    assert bridge.mutations() == ()
