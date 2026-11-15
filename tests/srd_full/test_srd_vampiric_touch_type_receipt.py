"""Vampiric Touch heals only fresh actual Necrotic damage from its one attack packet."""

from copy import deepcopy

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_retained_spell_attack_consumers as consumers
from tests import test_srd_retained_spell_attacks as source
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    consumers._owners.__wrapped__(monkeypatch)


class MixedRolls(source.Rolls):
    def randint(self, low, high):
        if high == 8:
            self.sides.append(high)
            return 7
        return super().randint(low, high)


def _case():
    state, data, params = source._case()
    actor = state[f"{SRD}.entities"]["hero"]
    actor["prepared_spells"].append("bestow_curse")
    data["spellcasting_grants"][0]["spells"].append("bestow_curse")
    data["wizard_spellbook"].append("bestow_curse")
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = -100
    return state, data, params


def _curse_params(params):
    return params | {
        "spell": "bestow_curse",
        "slot_resource": "spell_slot_5",
        "curse_mode": "damage",
        "targets": ["guard"],
    }


def _paid_curse(state, data, params):
    result = _run(
        "cast_spell_outside_combat", state, participant_data=data, **_curse_params(params)
    ).result
    assert result["status"] == "committed"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
    # The genuine fifth-level printed source does not require Concentration.
    assert state[f"{SRD}.entities"]["hero"].get("concentration_spell_id") is None
    return result


@pytest.mark.parametrize("defense,expected", [("ordinary", 16), ("resistance", 7), ("immunity", 0)])
def test_actual_paid_curse_and_vampiric_attack_use_matching_typed_receipt(
    monkeypatch, defense, expected
):
    state, data, params = _case()
    _paid_curse(state, data, params)
    guard = state[f"{SRD}.entities"]["guard"]
    guard["temp_hp"] = 100
    if defense == "resistance":
        guard["damage_resistances"] = ["necrotic"]
    elif defense == "immunity":
        guard["damage_immunities"] = ["necrotic"]
    rolls = MixedRolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    calls = []
    actual = _effect_executor._EFFECT_HANDLERS["call"]

    def record(body, effect, context, executor):
        if body == "apply_srd_qualifying_damage":
            calls.append(body)
        return actual(body, effect, context, executor)

    monkeypatch.setitem(_effect_executor._EFFECT_HANDLERS, "call", record)
    context = source._cast(state, data, params)
    assert context["_retained_necrotic_damage"] == expected
    assert context["_retained_damage_applied"] == expected
    assert context["_retained_healing_due"] == context["_retained_healing_applied"] == expected // 2
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 20 + expected // 2
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000 and guard["temp_hp"] == 100 - expected
    assert calls == ["apply_srd_qualifying_damage"]
    assert rolls.sides == [20, 6, 6, 6, 8]


def test_later_attack_after_curse_retirement_cannot_reuse_previous_typed_rider(monkeypatch):
    state, data, params = _case()
    _paid_curse(state, data, params)
    monkeypatch.setattr(dice, "_rng", MixedRolls())
    first = source._cast(state, data, params)
    assert first["_retained_necrotic_damage"] == 16
    # Native267 predates Curse's common cast-ID adoption. Use the actual installed
    # source owner's exact retirement and projection rather than inventing a cast ID.
    records = state[f"{SRD}.state"]["timed_condition_sources"]
    source_id = next(k for k, r in records.items() if r["spell_id"] == "bestow_curse")
    _run("retire_srd_timed_condition_source", state, source_id=source_id, lasting_state="state")
    _run("refresh_srd_timed_condition_target", state, target_state="guard", lasting_state="state")
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    result = source._later(state, data)
    context = result["context"]
    assert context["_retained_necrotic_damage"] == 9
    assert context["_retained_healing_applied"] == 4
    rows = {r["label"]: r["value"] for r in result["result_card"]["summary"]}
    assert rows["Necrotic damage"] == 9


def test_public_mixed_necrotic_healing_reopens_and_retries_without_second_packet(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(source, "_runtime", consumers._runtime)
    monkeypatch.setattr(public_casting, "bundled_srd_archive", source._archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _spell: _case())
    rolls = MixedRolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    database = tmp_path / "vampiric-mixed.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner, "vampiric_touch", target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        cursed, curse_command = _act(owner, "cast_spell_outside_combat", **_curse_params(params))
        assert cursed.status is ActionStatus.COMMITTED
        cast, cast_command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        advanced, advance_command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        later, later_command = _act(
            owner, "use_srd_vampiric_touch", caster="hero", caster_state="hero", target="guard"
        )
        assert advanced.status is later.status is ActionStatus.COMMITTED
        rows = {r["label"]: r["value"] for r in later.result["result_card"]["summary"]}
        assert rows["Necrotic damage"] == 16 and rows["HP restored"] == 8
        saved = owner.snapshot("casting")
        final_faces = deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for command, receipt in (
            (curse_command, cursed),
            (cast_command, cast),
            (advance_command, advanced),
            (later_command, later),
        ):
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == saved
        assert rolls.sides == final_faces
