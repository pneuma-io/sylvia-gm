"""Native rerolls keep the real source roll and use the durable mechanics choice."""

from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full.srd_reincarnate_native_traits import extend_runtime as native_traits
from scripts.srd_full.srd_reincarnate_rerolls import extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.domain.authority import MechanicContext, MechanicsChoiceResolution, owned_payload
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_reincarnate_native_traits import _new_body
from tests.srd_full.test_srd_reincarnate_native_traits import native_traits_owner as native_traits_owner
from tests.srd_full.test_srd_spell_result_cards import SRD

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def native_traits_runtime():
    from sylvia_gm.domain.content_validation import validate_content_shape
    from sylvia_gm.runtime._schema_validator import validate_ruleset

    text = extend_runtime(
        native_traits((ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text())
    )
    assert extend_runtime(text) == text
    assert not validate_ruleset(text)
    validate_content_shape("package_runtime", _load_runtime(text), file="native-rerolls")
    return _load_runtime(text), text.encode()


def _choose(state, data, request, option, **parameters):
    """Resume the same genuine mechanic with the exact source request, then apply its mutations."""
    from tests.srd_full.test_srd_action_budget import _mechanic

    result = _mechanic("d20_test")(
        MechanicContext(
            campaign_id="budget",
            turn_id="turn",
            action_id="d20_test",
            state=owned_payload(state),
            parameters=owned_payload(parameters),
            participants=owned_payload(
                {
                    "hero": {
                        "participant_id": "hero",
                        "kind": "player_character",
                        "validation_status": "validated",
                        "package_id": SRD,
                        "data": {"name": "Budget Hero", "speed": 30, **data},
                    }
                }
            ),
            choice_resolution=MechanicsChoiceResolution(request.choice_id, option, request),
        )
    )
    assert result.result["status"] == "committed"
    for mutation in result.mutations:
        state[f"{mutation.namespace}.{mutation.key}"] = deepcopy(mutation.value)
    return result.result["context"]


def _parameters(**choices):
    return {
        "actor": "hero",
        "actor_state": "hero",
        "ability": "wisdom",
        "kind": "saving_throw",
        "proficiency_factor": "normal",
        "circumstantial_modifier": 0,
        "target_number": 15,
        **choices,
    }


def test_luck_chooses_only_one_actual_one_and_preserves_the_other_die(
    native_traits_owner,
    monkeypatch,
):
    state, data, _ = _new_body("halfling")
    parameters = _parameters(disadvantage=True)
    before = deepcopy(state)
    monkeypatch.setattr(dice, "_rng", Random(31))
    waiting = _run("d20_test", state, participant_data=data, **parameters)
    assert state == before
    assert [option.option_id for option in waiting.choice.options] == ["keep", "luck_first"]
    monkeypatch.setattr(dice, "_rng", Random(31))
    result = _choose(state, data, waiting.choice, "luck_first", **parameters)
    assert result["_native_d20_original"]["results"] == [1, 16]
    assert result["_d20_test"]["results"] == [result["_native_d20_reroll"]["total"], 16]
    assert result["_d20_test"]["total"] == min(result["_d20_test"]["results"])
    assert result["_d20_test"]["modifier"] == result["_native_d20_original"]["modifier"]


def test_human_real_long_rest_grants_and_actual_reroll_consumes_one_instance(
    native_traits_owner,
    monkeypatch,
):
    state, data, choices = _new_body("human", complete=False)
    choices.update(
        species_skill="athletics",
        skill_proficiencies=["arcana", "athletics"],
        species_origin_feat="savage_attacker",
        origin_feats=["alert", "savage_attacker"],
    )
    completed = _run(
        "complete_srd_reincarnation",
        state,
        participant_data=data,
        target_state="hero",
        target_character="hero",
        hit_points=1,
        **choices,
    )
    data = dict(
        next(row.data for row in completed.participant_mutations if row.participant_id == "hero")
    )
    _run("begin_srd_rest", state, target_state="hero", kind="long", activity="sleep")
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    _run("complete_srd_rest", state, target_state="hero")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["heroic_inspiration"] is True
    parameters = _parameters()
    monkeypatch.setattr(dice, "_rng", Random(10))
    before = deepcopy(state)
    waiting = _run("d20_test", state, participant_data=data, **parameters)
    assert state == before
    assert [option.option_id for option in waiting.choice.options] == ["keep", "heroic_first"]
    monkeypatch.setattr(dice, "_rng", Random(10))
    result = _choose(state, data, waiting.choice, "heroic_first", **parameters)
    assert result["_native_d20_original"]["results"] == [19]
    assert result["_d20_test"]["results"] == [2]
    assert result["_d20_test"]["total"] == 2
    assert state[f"{SRD}.entities"]["hero"]["heroic_inspiration"] is False
    assert result["_outcome"] == "failure"
