"""An empty equipment slot is null; a present object still obeys its complete schema."""

import pytest
from jsonschema import Draft202012Validator

from sylvia_gm.application.schema_admission import (
    validate_json_schema,
    validate_json_value,
    validate_parameters,
    validate_references,
)
from sylvia_gm.domain._content_schema_runtime import SCHEMA_ADMISSION_SUBSET_SCHEMA
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected


def slot_schema():
    return {
        "type": "object", "properties": {"slot": {
            "type": ["object", "null"], "required": ["id", "rounds", "owner"],
            "additionalProperties": False, "properties": {
                "id": {"type": "string", "minLength": 1},
                "rounds": {"type": "integer", "minimum": 0},
                "owner": {"type": ["string", "null"], "minLength": 1,
                          "x-sylvia-gm-reference": "participant"},
            },
        }}, "required": ["slot"], "additionalProperties": False,
    }


@pytest.mark.parametrize("slot", [None, {"id": "weapon", "rounds": 2, "owner": "a"},
                                 {"id": "weapon", "rounds": 0, "owner": None}])
def test_nullable_slot_preserves_shape_constraints_and_references(slot):
    schema, value = owned_payload(slot_schema()), {"slot": slot}
    Draft202012Validator(SCHEMA_ADMISSION_SUBSET_SCHEMA).validate(slot_schema())
    validate_json_schema(schema)
    validate_json_value(value, schema)
    validate_parameters(value, schema)
    validate_references(value, schema, participant_ids={"a"})


@pytest.mark.parametrize("slot", [{}, {"id": "weapon", "rounds": -1, "owner": "a"},
                                 {"id": "weapon", "rounds": 1}, "weapon", False])
def test_present_equipment_cannot_bypass_validation_via_nullable_type(slot):
    schema, value = owned_payload(slot_schema()), {"slot": slot}
    with pytest.raises(CommandRejected):
        validate_json_value(value, schema)
    with pytest.raises(MechanicsRejected):
        validate_parameters(value, schema)


def test_nullable_nested_owner_cannot_reference_another_campaign():
    with pytest.raises(CommandRejected, match="outside"):
        validate_references({"slot": {"id": "weapon", "rounds": 1, "owner": "other"}},
                            owned_payload(slot_schema()), participant_ids={"a"})


@pytest.mark.parametrize("type_value", [[], ["null"], ["null", "null"], ["object", "string"],
                                       ["object", "null", "string"], ["null", {}]])
def test_invalid_or_broader_type_unions_are_rejected(type_value):
    with pytest.raises(CommandRejected):
        validate_json_schema({"type": type_value})


def test_nullable_array_constraints_and_null_enum_remain_explicit():
    array = {"type": ["null", "array"], "items": {"type": "integer"},
             "minItems": 1, "contains": {"type": "integer", "minimum": 2}}
    Draft202012Validator(SCHEMA_ADMISSION_SUBSET_SCHEMA).validate(array)
    validate_json_value(None, owned_payload(array))
    validate_json_value([2], owned_payload(array))
    with pytest.raises(CommandRejected):
        validate_json_value([1], owned_payload(array))
    with pytest.raises(CommandRejected):
        validate_json_value(None, {"type": ["string", "null"], "enum": ["present"]})


@pytest.mark.parametrize("slot", [None, {"id": "rifle", "rounds": 2, "owner": None}])
def test_initial_state_calculation_can_copy_a_complete_nullable_slot(slot):
    from sylvia_gm.application.initial_character_state import (
        materialize_initial_character_state,
        verify_initial_character_state,
    )

    contract = {
        "entity_state_schema": owned_payload(slot_schema()),
        "initial_state_calculations": ({"set": {"field": "slot", "value": "participant.slot"}},),
    }
    complete = materialize_initial_character_state({"slot": slot}, {}, **contract)
    assert complete == {"slot": slot}
    assert verify_initial_character_state({"slot": slot}, complete, **contract) == complete
