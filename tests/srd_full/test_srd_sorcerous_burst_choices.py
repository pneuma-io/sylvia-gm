"""Actual 8s and the caster's modifier bound the player's optional stopping choice."""

from copy import deepcopy

import pytest

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_sorcerous_burst_objects import _object_case
from tests.srd_full.test_srd_spell_special_damage import ActualDiceSequence
from tests.srd_full.test_srd_spell_special_damage import compiled_special as compiled_special
from tests.srd_full.test_srd_spell_special_damage import special_runtime as special_runtime

SRD = cards.SRD


@pytest.mark.parametrize("object_target", [False, True])
@pytest.mark.parametrize("limit,extra", [(0, 0), (1, 1), (3, 3), (100, 4)])
def test_public_burst_honors_optional_stopping_without_bypassing_source_cap(
    monkeypatch, object_target, limit, extra
):
    state, data, parameters = (
        _object_case() if object_target else cards._spell_case("sorcerous_burst")
    )
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, temp_hp=0)
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence())
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(
            parameters
            | {"selected_damage_type": "fire", "sorcerous_burst_extra_dice_limit": limit}
        ),
    ).result
    actual = result["context"]["_spell_damage"]
    assert actual["count"] == 4
    assert actual["results"] == [8] * (4 + extra)
    assert actual["total"] == 8 * (4 + extra)


@pytest.mark.parametrize("object_target", [False, True])
def test_public_critical_burst_stopping_keeps_actual_critical_base_dice(monkeypatch, object_target):
    state, data, parameters = (
        _object_case() if object_target else cards._spell_case("sorcerous_burst")
    )
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d20=20))
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"selected_damage_type": "fire", "sorcerous_burst_extra_dice_limit": 2}),
    ).result
    actual = result["context"]["_spell_damage"]
    assert actual["count"] == 8 and actual["results"] == [8] * 10


def test_burst_stop_choice_does_not_create_extra_dice_without_actual_eights(monkeypatch):
    state, data, parameters = _object_case()
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d8=[1, 1, 1, 1]))
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"sorcerous_burst_extra_dice_limit": 3}),
    ).result
    assert result["context"]["_spell_damage"]["results"] == [1, 1, 1, 1]
    assert result["context"]["_spell_damage_amount"] == 4


def test_burst_negative_actual_casting_modifier_permits_no_optional_extra_die(monkeypatch):
    state, data, parameters = _object_case()
    data["abilities"]["charisma"] = 3
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d20=20))
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"sorcerous_burst_extra_dice_limit": 3}),
    ).result
    assert result["context"]["_spell_damage"]["count"] == 8
    assert result["context"]["_spell_damage"]["results"] == [8] * 8


def test_burst_rejects_invalid_stopping_intent_before_any_mutation():
    state, data, parameters = _object_case()
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
        budget._run(
            "cast_spell_outside_combat",
            state,
            participant_data=data,
            **(parameters | {"sorcerous_burst_extra_dice_limit": -2}),
        )
    assert state == before
