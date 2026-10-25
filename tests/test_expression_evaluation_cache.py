"""Repeated calculation syntax can be reused without retaining results or public ASTs."""

import pytest

from sylvia_gm.runtime import _condition_evaluator as evaluator
from sylvia_gm.runtime import _expression_parser as parser
from sylvia_gm.runtime import _schema_validator as validator
from sylvia_gm.runtime._exceptions import ExpressionError
from sylvia_gm.runtime._types import ExecutionContext


@pytest.fixture(autouse=True)
def clear_cache():
    cached = getattr(evaluator, "_evaluation_expression", None)
    if cached is not None:
        cached.cache_clear()
    checked = getattr(validator, "_validate_expression", None)
    if checked is not None:
        checked.cache_clear()
    yield
    if cached is not None:
        cached.cache_clear()
    if checked is not None:
        checked.cache_clear()


def test_repeated_expression_parses_once_but_reads_each_current_context(monkeypatch):
    parses = []
    original = parser.parse_expression

    def observed(text):
        parses.append(text)
        return original(text)

    monkeypatch.setattr(parser, "parse_expression", observed)
    assert [evaluator.evaluate_expression("value + 1", ExecutionContext(params={"value": n}))
            for n in range(40)] == list(range(1, 41))
    assert parses == ["value + 1"]


def test_mutating_public_ast_does_not_change_evaluations():
    context = ExecutionContext(params={"value": 3})
    assert evaluator.evaluate_expression("value + 1", context) == 4
    public = parser.parse_expression("value + 1")
    public.right.value = 999
    assert evaluator.evaluate_expression("value + 1", context) == 4
    assert parser.parse_expression("value + 1").right.value == 1
    assert evaluator.evaluate_expression("value + 2", context) == 5


@pytest.mark.parametrize("text", ["open('bad')", "1 +", "1" * 501, "(" * 30 + "1" + ")" * 30])
def test_invalid_expressions_keep_parser_limits_and_are_not_cached(monkeypatch, text):
    parses = []
    original = parser.parse_expression

    def observed(value):
        parses.append(value)
        return original(value)

    monkeypatch.setattr(parser, "parse_expression", observed)
    for _ in range(2):
        with pytest.raises(ExpressionError):
            evaluator.evaluate_expression(text, ExecutionContext())
    assert parses == [text, text]


def test_expression_cache_is_bounded():
    for n in range(1100):
        assert evaluator.evaluate_expression(str(n), ExecutionContext()) == n
        validator._validate_expression(str(n))
    cached = getattr(evaluator, "_evaluation_expression", None)
    assert cached is not None
    assert cached.cache_info().currsize <= 1024
    assert validator._validate_expression.cache_info().currsize <= 1024


def test_repeated_validation_keeps_each_error_location_and_new_effect_checks(monkeypatch):
    parses = []
    original = validator.parse_expression

    def observed(text):
        parses.append(text)
        return original(text)

    monkeypatch.setattr(validator, "parse_expression", observed)
    effect = {"condition": "value > 0", "set": {"field": "_x", "value": "1"}}
    assert validator._validate_effects("first", [effect] * 20) == []
    assert parses == ["value > 0"]
    bad = {**effect, "condition": "value +"}
    errors = validator._validate_effects("second", [bad, bad])
    assert len(errors) == 2 and "effect 0" in errors[0] and "effect 1" in errors[1]
    assert parses[-2:] == ["value +", "value +"]
    # An earlier valid condition cannot bless an invalid surrounding effect.
    assert validator._validate_effects("third", [{"condition": "value > 0", "unknown": {}}])
