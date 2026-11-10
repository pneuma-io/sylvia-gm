"""The grader accepts ordinary wording without converting uncertainty into a model failure."""

from copy import deepcopy

import pytest
from sylviabench.checks import breakers, evaluate, healing_narration
from sylviabench.contracts import scenarios
from sylviabench.fixtures import FACT


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


def recall_observations():
    fact = {
        "record_id": "signal", "record_kind": "fact", "text": FACT,
        "visibility": "player_known", "known_by": ["original-player"], "admitted_revision": 6,
    }
    return {
        "before": {"talks": ["We agreed on a signal."]},
        "saved": {"facts": [deepcopy(fact)]},
        "reopened": {"facts": [deepcopy(fact)]},
        "after": {"facts": [deepcopy(fact)], "talks": ["We agreed on a signal.", FACT]},
    }


def recall_checks(observations):
    scenario = next(s for s in scenarios() if s.id == "T3")
    assert scenario.version == 2
    return {check.id: check for check in evaluate(scenario, observations)}


def test_recall_keeps_a_fact_when_readmission_updates_bookkeeping_and_adds_audience():
    observations = recall_observations()
    fact = observations["after"]["facts"][0]
    fact["admitted_revision"] = 8
    fact["known_by"].append("new-player")
    assert all(c.status == "pass" for c in recall_checks(observations).values())


@pytest.mark.parametrize(
    "mutation", ["missing", "changed_text", "private", "lost_audience", "identity"]
)
def test_recall_detects_actual_fact_loss_or_access_changes(mutation):
    observations = recall_observations()
    fact = observations["after"]["facts"][0]
    if mutation == "missing":
        observations["after"]["facts"] = []
    elif mutation == "changed_text":
        fact["text"] = "Use a red flag at the east gate."
    elif mutation == "private":
        fact["visibility"] = "gm_private"
    elif mutation == "lost_audience":
        fact["known_by"] = []
    else:
        fact["record_id"] = "different-record"
    assert recall_checks(observations)["saved_fact_survives"].status == "fail"


def test_recall_checks_the_reopened_copy_and_the_answer_separately():
    observations = recall_observations()
    observations["reopened"]["facts"] = []
    assert recall_checks(observations)["saved_fact_survives"].status == "fail"
    observations = recall_observations()
    observations["after"]["talks"][-1] = "Use a red flag at the east gate."
    checks = recall_checks(observations)
    assert checks["saved_fact_survives"].status == "pass"
    assert checks["answer_recalls_fact"].status == "fail"
