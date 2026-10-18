"""The retained modifier helper uses the runtime expression boundary."""

import pytest

from sylvia_gm.ruleset.schema import AbilityConfig, RulesetFile
from sylvia_gm.runtime._exceptions import ExpressionError


@pytest.mark.parametrize(
    "formula,score,expected",
    [
        ("(score - 10) // 2", 7, -2),
        ("(score - 10) // 2", 18, 4),
        ("floor((score - 10) * 0.5)", 9, -1),
        ("score", 3, 3),
    ],
)
def test_declared_modifier_formula_uses_supported_arithmetic(formula, score, expected):
    ruleset = RulesetFile(
        abilities={"focus": AbilityConfig(name="Focus", modifier_formula=formula)}
    )
    assert ruleset.modifier_for("focus", score) == expected


@pytest.mark.parametrize(
    "formula", ["score.__class__", "().__class__.__mro__", "__import__('os')", "1 % 0", "score / 2"]
)
def test_modifier_rejects_hostile_or_invalid_expression_at_runtime_boundary(formula):
    ruleset = RulesetFile(
        abilities={"focus": AbilityConfig(name="Focus", modifier_formula=formula)}
    )
    with pytest.raises(ExpressionError):
        ruleset.modifier_for("focus", 12)
