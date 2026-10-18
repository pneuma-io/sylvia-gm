"""Authoring admission rejects fields that the runtime would otherwise ignore."""

import pytest
import yaml

from sylvia_gm.application.package_runtime import PackageRuntimeError, validate_package_runtime
from sylvia_gm.domain._runtime_executable_reference import VALID_EXECUTABLE_EXAMPLE
from sylvia_gm.domain.content_definitions import get_content_definition
from sylvia_gm.runtime._schema_validator import validate_ruleset


@pytest.mark.parametrize(
    ("effect", "suffix"),
    [
        ({"set": {"field": "state.x", "value": 1, "guess": True}}, ".set.guess"),
        (
            {
                "condition": "true",
                "on_true": [
                    {"increment": {"field": "state.x", "amount": 1, "quietly": True}},
                ],
            },
            ".on_true[0].increment.quietly",
        ),
        ({"condition": {"condition": "true", "on_true": []}, "ignored": 1}, ".ignored"),
        ({"log": "silently ignored"}, ".log"),
        ({"dice": {"count": 1, "sides": 6}}, ".dice.count"),
    ],
)
def test_invalid_effects_reject_before_package_publication(effect, suffix):
    raw = {
        "sylvia_gm_runtime": {"format": 1, "state_namespace": "example.rules"},
        "tools": {"act": {"effects": [effect]}},
    }
    with pytest.raises(PackageRuntimeError) as rejected:
        validate_package_runtime(
            package_id="example.rules",
            declarations=(),
            runtime_bytes=yaml.safe_dump(raw).encode(),
        )
    paths = {item.path for item in rejected.value.diagnostics}
    assert f"$.tools.act.effects[0]{suffix}" in paths


def test_retrieved_runtime_example_uses_the_actual_effect_and_expression_grammar():
    definition = get_content_definition("package_runtime")["procedural_definition"]["executable"]
    assert definition["expressions"]["maximum_characters"] == 500
    raw = {
        "sylvia_gm_runtime": {"format": 1, "state_namespace": "example.rules"},
        **VALID_EXECUTABLE_EXAMPLE,
    }
    assert validate_ruleset(yaml.safe_dump(raw)) == []
