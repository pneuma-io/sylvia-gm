"""Pure collection lengths allow optional character choices to be addressed safely."""

import pytest

from sylvia_gm.application.initial_character_state import materialize_initial_character_state
from sylvia_gm.runtime._condition_evaluator import evaluate
from sylvia_gm.runtime._exceptions import ExpressionError
from sylvia_gm.runtime._expression_parser import parse_expression
from sylvia_gm.runtime._types import ExecutionContext


def test_merge_preserves_unmodified_metadata_without_mutating_inputs():
    original = {"label": "Vesper", "stance": "standing", "extra": {"color": "blue"}}
    changes = {"stance": "crouched"}
    merged = evaluate(parse_expression("merge(original, changes)"), ExecutionContext(
        params={"original": original, "changes": changes},
    ))
    assert merged == {"label": "Vesper", "stance": "crouched", "extra": {"color": "blue"}}
    assert original["stance"] == "standing"
    assert changes == {"stance": "crouched"}


@pytest.mark.parametrize("expression,value", [
    ("merge(value, value)", []), ("merge(value, value)", None),
    ("merge(value)", {}), ("merge(value, value, value)", {}),
])
def test_merge_rejects_nonobjects_and_wrong_arity(expression, value):
    with pytest.raises(ExpressionError):
        evaluate(parse_expression(expression), ExecutionContext(params={"value": value}))


@pytest.mark.parametrize(
    "choices,expected", [([], 0), ([{"rank": 3}], 0), ([{"rank": 3}, {"rank": 7}], 7)]
)
def test_creation_can_guard_an_optional_choice_before_indexing(choices, expected):
    schema = {
        "type": "object",
        "properties": {"bonus": {"type": "integer"}},
        "required": ["bonus"],
        "additionalProperties": False,
    }
    completed = materialize_initial_character_state(
        {"choices": choices},
        {},
        entity_state_schema=schema,
        initial_state_calculations=[
            {"set": {"field": "bonus", "value": "0"}},
            {
                "condition": "len(participant.choices) > 1",
                "set": {"field": "bonus", "value": "participant.choices[1].rank"},
            },
        ],
    )
    assert completed == {"bonus": expected}


@pytest.mark.parametrize("value,expected", [({"a": 1, "b": 2}, 2), ("abc", 3)])
def test_length_supports_other_json_collections(value, expected):
    assert (
        evaluate(parse_expression("len(value)"), ExecutionContext(params={"value": value}))
        == expected
    )


@pytest.mark.parametrize(
    "expression,value",
    [
        ("len(value)", None),
        ("len(value)", True),
        ("len(value)", 3),
        ("len(value, value)", []),
        ("len()", []),
    ],
)
def test_length_rejects_noncollections_and_wrong_arity(expression, value):
    with pytest.raises(ExpressionError):
        evaluate(parse_expression(expression), ExecutionContext(params={"value": value}))
