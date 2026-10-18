"""Subsequent effects and result fields must read same-action entity mutations."""

import pytest

from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ToolDef, ToolParameter


@pytest.mark.parametrize("effect,field,expected", [
    ({"heal": {"target": "target", "amount": 5}}, "hp", 10),
    ({"apply_damage": {"target": "target", "amount": 2}}, "hp", 5),
    ({"apply_condition": {"target": "target", "condition": "marked"}}, "conditions", ["marked"]),
    ({"remove_condition": {"target": "target", "condition": "marked"}}, "conditions", []),
])
def test_effects_refresh_every_parameter_alias_of_the_modified_entity(effect, field, expected):
    bridge = AuthorityMemoryStateBridge("example.test", {
        "example.test.entities": {"unit": {"hp": 7, "max_hp": 10,
            "conditions": ["marked"] if "remove_condition" in effect else []}},
    })
    tool = ToolDef(name="change", parameters=[
        ToolParameter("target", "string", resolve="entity"),
        ToolParameter("same_target", "string", resolve="entity"),
        ToolParameter("optional_character", "string", required=False, resolve="participant"),
    ], effects=[effect,
        {"set": {"field": "_observed", "value": f"target.{field}"}},
    ])
    result = execute_tool(tool.name, {"target": "unit", "same_target": "unit"},
                          {tool.name: tool}, bridge, {}, {}, {})
    assert result.success, result.error
    assert bridge.get_entity("unit")[field] == expected
    assert result.context["_observed"] == expected
    assert result.context["target"][field] == expected
    assert result.context["same_target"][field] == expected
