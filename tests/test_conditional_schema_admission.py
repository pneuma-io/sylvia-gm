from copy import deepcopy

import pytest

from sylvia_gm.application.schema_admission import (
    validate_json_schema,
    validate_json_value,
    validate_parameters,
    validate_references,
)
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected


def _schema():
    return {
        "type": "object", "required": ["choices"], "additionalProperties": False,
        "properties": {"choices": {
            "type": "array", "items": {"type": "string"}, "uniqueItems": True,
        }},
        "allOf": [{
            "type": "object", "if": {"type": "object", "properties": {"choices": {
                "type": "array", "items": {"type": "string"},
                "contains": {"type": "string", "enum": ["advanced"]},
            }}},
            "then": {"type": "object", "properties": {"choices": {
                "type": "array", "items": {"type": "string"},
                "contains": {"type": "string", "enum": ["first", "second", "third"]},
                "minContains": 2,
            }}},
        }],
    }


@pytest.mark.parametrize("choices, valid", [
    (["unrelated"], True), (["first", "second", "advanced"], True),
    (["first", "advanced"], False), (["first", "first", "advanced"], False),
])
def test_conditional_array_prerequisites_agree_in_both_admission_paths(choices, valid):
    schema, value = owned_payload(_schema()), {"choices": choices}
    before = deepcopy(value)
    validate_json_schema(schema)
    if valid:
        validate_parameters(value, schema)
        validate_json_value(value, schema)
    else:
        with pytest.raises(MechanicsRejected) as failure:
            validate_parameters(value, schema)
        assert failure.value.field_paths == ("choices",)
        assert failure.value.validation_issues[0].kind in {
            "too_few_matching_items", "duplicate_items",
        }
        with pytest.raises(CommandRejected):
            validate_json_value(value, schema)
    assert value == before


@pytest.mark.parametrize("extra", [
    {"if": {"type": "object"}}, {"then": {"type": "object"}},
    {"allOf": []}, {"allOf": [{"type": "object"}] * 257},
    {"contains": {"type": "string"}}, {"minContains": 2},
    {"allOf": [{"type": "object", "unknown_keyword": True}]},
])
def test_uninterpretable_composition_is_rejected_at_schema_admission(extra):
    with pytest.raises(CommandRejected):
        validate_json_schema({"type": "object", **extra})


def test_conditional_and_contains_reference_constraints_still_check_campaign_ownership():
    schema = {
        "type": "object", "properties": {"targets": {
            "type": "array", "items": {"type": "string"},
        }}, "allOf": [{
            "type": "object", "if": {"type": "object", "required": ["targets"],
                                        "properties": {"targets": {
                                            "type": "array", "items": {"type": "string"},
                                        }}},
            "then": {"type": "object", "properties": {"targets": {
                "type": "array", "items": {"type": "string"},
                "contains": {"type": "string", "x-sylvia-gm-reference": "participant"},
            }}},
        }],
    }
    validate_json_schema(schema)
    validate_references({"targets": ["owned"]}, schema, participant_ids={"owned"})
    with pytest.raises(CommandRejected) as failure:
        validate_references({"targets": ["NEVER_ECHO_PRIVATE"]}, schema, participant_ids={"owned"})
    assert failure.value.code == "cross_campaign_reference"
    assert "NEVER_ECHO_PRIVATE" not in str(failure.value)
