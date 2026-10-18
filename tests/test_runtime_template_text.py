"""Human-readable interpolations must preserve exact typed references used by mechanics."""

from types import SimpleNamespace

import pytest

from sylvia_gm.runtime._template_resolver import resolve_params, resolve_template


@pytest.mark.parametrize("values,text", [
    ([2, 17], "2, 17"), ((13,), "13"), (["blinded", "deafened"], "blinded, deafened"),
    ([], "none"),
])
def test_scalar_lists_read_as_values_in_text_but_remain_typed_in_call_parameters(values, text):
    context = SimpleNamespace(resolve_field=lambda field: values if field == "values" else None)
    assert resolve_template("Recorded: {values}", context) == f"Recorded: {text}"
    assert resolve_params({"values": "{values}"}, context)["values"] is values
    assert resolve_template("Unknown: {unavailable}", context) == "Unknown: {unavailable}"
