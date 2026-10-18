"""Structured runtime inputs come from saved package state, not caller replacements."""

from dataclasses import replace

import pytest
import yaml

from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests.test_spatial_runtime import _context, _mechanic, _runtime, _scene


def bound_runtime():
    raw = yaml.safe_load(
        _runtime(
            [
                {"set": {"field": "state.inspected", "value": "geometry.id"}},
            ]
        )
    )
    raw["tools"]["inspect_scene"]["parameters"] = [
        {"name": "geometry", "type": "object", "from_state": "state.spatial_scene.map"},
    ]
    return raw


def test_state_object_replaces_caller_supplied_value_and_does_not_expose_a_model_argument():
    raw = bound_runtime()
    mechanic = _mechanic(yaml.safe_dump(raw).encode())
    context = replace(_context(_scene()), parameters={"geometry": {"id": "caller-forgery"}})
    result = mechanic(context)
    assert thaw_json_value(result.mutations[0].value)["inspected"] == "runtime-map"
    assert "geometry" not in result.result["context"]


def test_object_parameters_require_authority_binding():
    raw = bound_runtime()
    del raw["tools"]["inspect_scene"]["parameters"][0]["from_state"]
    assert any("requires from_state" in error for error in validate_ruleset_data(raw))


def test_wrong_saved_shape_rejects_before_effects():
    raw = bound_runtime()
    raw["tools"]["inspect_scene"]["parameters"][0]["from_state"] = "state.spatial_scene.geometry"
    with pytest.raises(PackageRuntimeError, match="execution failed"):
        _mechanic(yaml.safe_dump(raw).encode())(_context(_scene()))
