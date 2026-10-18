"""Immutable dice results remain iterable through ordinary declarative effects."""

from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ToolDef


def test_list_operations_preserve_immutable_saved_members():
    tool = ToolDef(name="update_saved_list", effects=[
        {"dice": {"expression": "2d6"}, "store_as": "_roll"},
        {"set": {"field": "_saved", "value": "_roll.results"}},
        {"set": {"field": "_pending", "value": "_saved"}},
        {"list_push": {"list": "_pending", "value": "companion"}},
        {"set": {"field": "_remaining", "value": "_saved"}},
        {"list_pop": {"list": "_remaining"}, "store_as": "_last"},
    ])
    result = execute_tool(tool.name, {}, {tool.name: tool},
                          AuthorityMemoryStateBridge("example.test", {}), {}, {}, {})
    assert result.success, result.error
    saved = result.context["_roll"].results
    assert result.context["_pending"] == [*saved, "companion"]
    assert result.context["_last"] == saved[-1]
    assert result.context["_remaining"] == list(saved[:-1])
    assert result.context["_saved"] == saved


def test_for_each_consumes_actual_dice_results_without_mutating_the_roll():
    tool = ToolDef(name="sum_dice", effects=[
        {"dice": {"expression": "2d6"}, "store_as": "_roll"},
        {"set": {"field": "_faces", "value": "_roll.results"}},
        {"set": {"field": "_observed", "value": 0}},
        {"for_each": {"collection": "_faces", "as": "_face", "do": [
            {"set": {"field": "_observed", "value": "_observed + _face"}},
        ]}},
    ])
    result = execute_tool(tool.name, {}, {tool.name: tool},
                          AuthorityMemoryStateBridge("example.test", {}), {}, {}, {})
    assert result.success, result.error
    assert result.context["_observed"] == result.context["_roll"].total
    assert result.context["_roll"].results == result.context["_faces"]
