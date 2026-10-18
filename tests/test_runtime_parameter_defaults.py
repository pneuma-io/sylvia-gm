"""Nested tools use the same declared defaults as public calls."""

import pytest

from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ToolDef, ToolParameter


@pytest.mark.parametrize("kind,default,explicit", [
    ("integer", 3, 0), ("boolean", True, False), ("string", "none", "selected"),
    ("array", ["base"], []),
])
def test_nested_calls_apply_missing_defaults_and_preserve_explicit_values(kind, default, explicit):
    leaf = ToolDef(name="leaf", parameters=[
        ToolParameter("choice", kind, required=False, default=default),
    ], effects=[{"set": {"field": "_received", "value": "choice"}}])
    outer = ToolDef(name="outer", effects=[
        {"call": "leaf", "export_context": {"_omitted": "_received"}},
        {"call": "leaf", "params": {"choice": explicit},
         "export_context": {"_explicit": "_received"}},
    ])
    definitions = {"outer": outer, "leaf": leaf}
    bridge = AuthorityMemoryStateBridge("example.test", {})

    def nested(name, parameters, _context):
        return execute_tool(name, parameters, definitions, bridge, {}, {}, tool_executor=nested)

    result = nested("outer", {}, None)
    assert result.success, result.error
    assert result.context["_omitted"] == default
    assert result.context["_explicit"] == explicit


def test_mutable_defaults_are_fresh_per_invocation_and_validated():
    default = ["base"]
    tool = ToolDef(name="collect", parameters=[
        ToolParameter("items", "array", default=default),
    ], effects=[{"set": {"field": "_items", "value": "items"}}])
    bridge = AuthorityMemoryStateBridge("example.test", {})
    supplied = {}
    for _ in range(2):
        result = execute_tool("collect", supplied, {"collect": tool}, bridge, {}, {})
        assert result.success, result.error
        result.context["_items"].append("added")
        assert result.context["_items"] == ["base", "added"]
        assert default == ["base"]
        assert supplied == {}
    tool.parameters[0].default = 3
    result = execute_tool("collect", {}, {"collect": tool}, bridge, {}, {})
    assert not result.success
    assert result.failure_kind == "parameter_validation"
