"""Computed choice options keep the same validation and replay contract as static ones."""

import pytest

from sylvia_gm.runtime._exceptions import MechanicsChoiceSuspended
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ChoiceResolutionControl, ToolDef


def _tool(options):
    return ToolDef(name="choose", effects=[
        {"set": {"field": "_options", "value": options}},
        {"request_choice": {"choice_id": "test-choice", "prompt": "Choose a result.",
                            "options": "_options"}, "store_as": "_selection"},
    ])


def test_computed_choice_suspends_and_resumes_only_the_same_options():
    tool = _tool([{"id": "keep", "label": "Keep"}, {"id": "remove", "label": "Remove"}])
    with pytest.raises(MechanicsChoiceSuspended) as waiting:
        execute_tool("choose", {}, {"choose": tool}, None, {}, {})
    request = waiting.value.request
    assert [option.option_id for option in request.options] == ["keep", "remove"]
    control = ChoiceResolutionControl(request.choice_id, "remove", request)
    result = execute_tool("choose", {}, {"choose": tool}, None, {}, {},
                          extras={"mechanics_choice_resolution": control})
    assert result.success, result.error
    assert result.context["_selection"] == "remove"
    assert control.consumed
    changed = _tool([{"id": "keep", "label": "Keep"},
                     {"id": "remove", "label": "A different result"}])
    stale = ChoiceResolutionControl(request.choice_id, "remove", request)
    rejected = execute_tool("choose", {}, {"choose": changed}, None, {}, {},
                            extras={"mechanics_choice_resolution": stale})
    assert not rejected.success
    assert not stale.consumed


@pytest.mark.parametrize("options", [
    [], [{"id": "only", "label": "Only"}],
    [{"id": "same", "label": "First"}, {"id": "same", "label": "Second"}],
    [{"id": "bad id", "label": "First"}, {"id": "good", "label": "Second"}],
    [{"id": "first", "label": ""}, {"id": "second", "label": "Second"}],
    [{"id": str(i), "label": "Option"} for i in range(17)],
])
def test_invalid_computed_options_fail_before_suspension(options):
    tool = _tool(options)
    result = execute_tool("choose", {}, {"choose": tool}, None, {}, {})
    assert not result.success
    assert result.failure_kind == "effect_execution"


def test_sequence_rejects_changed_prior_request_before_consuming_current_answer():
    tool = _tool([{"id": "keep", "label": "Keep"}, {"id": "remove", "label": "Remove"}])
    with pytest.raises(MechanicsChoiceSuspended) as waiting:
        execute_tool("choose", {}, {"choose": tool}, None, {}, {})
    request = waiting.value.request
    control = ChoiceResolutionControl(request.choice_id, "remove", request,
                                      prior_answers=((request, "keep"),))
    changed = _tool([{"id": "keep", "label": "Changed prior result"},
                     {"id": "remove", "label": "Remove"}])
    result = execute_tool("choose", {}, {"choose": changed}, None, {}, {},
                          extras={"mechanics_choice_resolution": control})
    assert not result.success
    assert control.replayed_answers == 0
    assert not control.consumed
