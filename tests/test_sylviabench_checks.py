"""The grader accepts ordinary wording without converting uncertainty into a model failure."""

import pytest
from sylviabench.checks import breakers, healing_narration


@pytest.mark.parametrize(
    "text",
    [
        "Cure Wounds restores 5 hit points.",
        "You regain **5** HP, going from 4 to 9.",
        "You recover 5 hit points.",
        "The spell heals you for 5 HP.",
        "5 HP restored; one spell slot spent.",
        "+5 HP. You now have 9 HP.",
        "Your hit points increase by 5.",
    ],
)
def test_numeric_healing_paraphrases_match_the_receipt(text):
    assert healing_narration(text, 5).status == "pass"


def test_missing_or_ambiguous_amount_is_unscored_and_cannot_be_a_false_outcome():
    for text in (
        "Your wounds close.",
        "You now have 9 HP.",
        "You regain five hit points.",
        "The spell heals 13 HP; you regain 5 HP after the cap.",
    ):
        check = healing_narration(text, 5)
        assert check.status == "not_evaluated"
        assert "false_outcome" not in breakers([check], True)
    wrong = healing_narration("You regain 6 hit points.", 5)
    assert wrong.status == "fail"
    assert breakers([wrong], True) == ["false_outcome"]


def test_stopping_early_is_not_the_two_retry_session_breaker():
    assert breakers([], False) == []
    assert breakers([], False, retries_exhausted=True) == ["stuck_turn"]
    assert breakers([], True, retries_exhausted=True) == []
