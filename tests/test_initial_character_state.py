from __future__ import annotations

from copy import deepcopy

import pytest

from sylvia_gm.application.initial_character_state import (
    initial_character_input_schema,
    materialize_initial_character_state,
    validate_initial_character_contract,
    verify_initial_character_state,
)
from sylvia_gm.domain.errors import CommandRejected

SCHEMA = {
    "type": "object",
    "properties": {
        "base": {"type": "integer"},
        "bonus": {"type": "integer"},
        "derived": {"type": "integer"},
        "nested": {
            "type": "object",
            "properties": {"total": {"type": "integer"}},
            "required": ["total"],
            "additionalProperties": False,
        },
    },
    "required": ["base", "bonus", "derived", "nested"],
    "additionalProperties": False,
}
CALCULATIONS = [
    {"set": {"field": "derived", "value": "state.base + participant.rank"}},
    {"set": {"field": "nested.total", "value": "state.derived + state.bonus"}},
]


def diagnostic_code(error: pytest.ExceptionInfo[CommandRejected]) -> str:
    assert error.value.diagnostics
    return error.value.diagnostics[0].code


def test_contained_rules_can_calculate_from_setting_choices_without_changing_them() -> None:
    calculations = [
        {"set": {"field": "derived", "value": "state.base + setting_data.training_bonus"}},
        CALCULATIONS[1],
    ]
    world = {"training_bonus": 3}
    complete = materialize_initial_character_state(
        {}, {"base": 4, "bonus": 2}, entity_state_schema=SCHEMA,
        initial_state_calculations=calculations, setting_data=world,
    )
    assert complete["derived"] == 7
    assert world == {"training_bonus": 3}
    assert verify_initial_character_state(
        {}, complete, entity_state_schema=SCHEMA,
        initial_state_calculations=calculations, setting_data=world,
    ) == complete
    with pytest.raises(CommandRejected) as missing:
        materialize_initial_character_state(
            {}, {"base": 4, "bonus": 2}, entity_state_schema=SCHEMA,
            initial_state_calculations=calculations,
        )
    assert diagnostic_code(missing) == "character_initial_state_input_missing"


def test_neutral_ordered_derived_scalars_preserve_inputs() -> None:
    participant = {"rank": 3, "name": "Vela"}
    authored = {"base": 4, "bonus": 2}
    before_participant, before_authored = deepcopy(participant), deepcopy(authored)

    state = materialize_initial_character_state(
        participant,
        authored,
        entity_state_schema=SCHEMA,
        initial_state_calculations=CALCULATIONS,
    )

    assert state == {"base": 4, "bonus": 2, "derived": 7, "nested": {"total": 9}}
    assert participant == before_participant
    assert authored == before_authored


def test_missing_required_expression_input_is_diagnostic() -> None:
    with pytest.raises(CommandRejected) as rejected:
        materialize_initial_character_state(
            {},
            {"base": 4, "bonus": 2},
            entity_state_schema=SCHEMA,
            initial_state_calculations=CALCULATIONS,
        )
    assert diagnostic_code(rejected) == "character_initial_state_input_missing"
    assert rejected.value.diagnostics[0].path == "initial_state_calculations[0].set.value"


def test_wrong_authored_type_reports_schema_or_calculation_without_value() -> None:
    with pytest.raises(CommandRejected) as rejected:
        materialize_initial_character_state(
            {"rank": 3},
            {"base": "four", "bonus": 2},
            entity_state_schema=SCHEMA,
            initial_state_calculations=CALCULATIONS,
        )
    assert diagnostic_code(rejected) == "character_initial_state_schema_invalid"
    assert rejected.value.diagnostics[0].path == "base"
    assert "four" not in str(rejected.value.diagnostics[0].as_payload())


def test_authored_calculated_field_is_rejected() -> None:
    with pytest.raises(CommandRejected) as rejected:
        materialize_initial_character_state(
            {"rank": 3},
            {"base": 4, "bonus": 2, "derived": 99},
            entity_state_schema=SCHEMA,
            initial_state_calculations=CALCULATIONS,
        )
    assert diagnostic_code(rejected) == "character_initial_state_calculated_field_authored"
    assert rejected.value.diagnostics[0].path == "entity_state.derived"


def test_saved_calculated_value_mismatch_does_not_modify_input() -> None:
    saved = {"base": 4, "bonus": 2, "derived": 8, "nested": {"total": 10}}
    before = deepcopy(saved)
    with pytest.raises(CommandRejected) as rejected:
        verify_initial_character_state(
            {"rank": 3},
            saved,
            entity_state_schema=SCHEMA,
            initial_state_calculations=CALCULATIONS,
        )
    assert diagnostic_code(rejected) == "character_initial_state_calculated_value_mismatch"
    assert rejected.value.diagnostics[0].path == "entity_state.derived"
    assert saved == before


def test_complete_saved_state_verifies_and_remains_untouched() -> None:
    saved = {"base": 4, "bonus": 2, "derived": 7, "nested": {"total": 9}}
    before = deepcopy(saved)
    verified = verify_initial_character_state(
        {"rank": 3},
        saved,
        entity_state_schema=SCHEMA,
        initial_state_calculations=CALCULATIONS,
    )
    assert verified == saved
    assert saved == before


def test_missing_indexed_input_is_not_treated_as_null_and_conditions_short_circuit() -> None:
    schema = {
        "type": "object",
        "properties": {"ready": {"type": "boolean"}},
        "required": ["ready"],
        "additionalProperties": False,
    }
    calculations = [{"set": {"field": "ready", "value": "participant.items[0].rank == null"}}]
    with pytest.raises(CommandRejected) as rejected:
        materialize_initial_character_state(
            {"items": [{}]},
            {},
            entity_state_schema=schema,
            initial_state_calculations=calculations,
        )
    assert diagnostic_code(rejected) == "character_initial_state_input_missing"
    calculations[0]["set"]["value"] = "false and participant.items[0].rank == null"
    assert materialize_initial_character_state(
        {"items": [{}]},
        {},
        entity_state_schema=schema,
        initial_state_calculations=calculations,
    ) == {"ready": False}


@pytest.mark.parametrize("expression", ["combat.round", "state.base +", "participant.x[ruleset.x]"])
def test_invalid_formulas_are_rejected_without_character_inputs(expression: str) -> None:
    with pytest.raises(CommandRejected) as rejected:
        validate_initial_character_contract(
            SCHEMA, [{"set": {"field": "derived", "value": expression}}]
        )
    assert diagnostic_code(rejected) == "character_initial_state_expression_invalid"


def test_authored_input_definition_excludes_calculated_fields() -> None:
    inputs = initial_character_input_schema(SCHEMA, CALCULATIONS)
    assert "derived" not in inputs["properties"]
    assert "total" not in inputs["properties"]["nested"]["properties"]
    assert inputs["required"] == ["base", "bonus"]
    assert "derived" in SCHEMA["properties"]


def test_declared_collection_is_copied_and_saved_mismatch_is_rejected() -> None:
    schema = {
        "type": "object",
        "properties": {"specialties": {"type": "array", "items": {"type": "string"}}},
        "required": ["specialties"],
        "additionalProperties": False,
    }
    calculations = [{"set": {"field": "specialties", "value": "participant.specialties"}}]
    participant = {"specialties": ["signal-trace", "signal-jam"]}
    state = materialize_initial_character_state(
        participant,
        {},
        entity_state_schema=schema,
        initial_state_calculations=calculations,
    )
    assert state == participant
    state["specialties"].append("foreign-choice")
    assert participant["specialties"] == ["signal-trace", "signal-jam"]
    with pytest.raises(CommandRejected) as rejected:
        verify_initial_character_state(
            participant,
            state,
            entity_state_schema=schema,
            initial_state_calculations=calculations,
        )
    assert diagnostic_code(rejected) == "character_initial_state_calculated_value_mismatch"


def test_calculated_parent_and_child_cannot_overlap() -> None:
    with pytest.raises(CommandRejected) as rejected:
        validate_initial_character_contract(
            SCHEMA,
            [
                {"set": {"field": "nested", "value": "participant.nested"}},
                {"set": {"field": "nested.total", "value": "state.base"}},
            ],
        )
    assert diagnostic_code(rejected) == "character_initial_state_calculation_invalid"
