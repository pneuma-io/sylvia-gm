"""Befuddlement uses real casting, Magic budgets, elapsed saves and exact named cures."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_recovery import _control_source
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case


def _case(monkeypatch, spell="befuddlement", modifier=-100):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, params = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=1000, max_hp=1000, temp_hp=0)
    target["saving_throw_modifiers"]["intelligence"] = modifier
    return state, data, params


def _install(state, target="guard", dc=20):
    _run(
        "install_srd_befuddlement_source",
        state,
        target_state=target,
        caster_state="hero",
        lasting_state="state",
        save_dc=dc,
        actual_spell_level=8,
    )
    return state[f"{SRD}.entities"][target]["befuddlement_source_ids"][-1]


def _source(state, target="guard"):
    source_id = state[f"{SRD}.entities"][target]["befuddlement_source_ids"][0]
    return state[f"{SRD}.state"]["timed_condition_sources"][source_id]


def _thirty_days(state):
    for rounds in (100000, 100000, 100000, 100000, 32000):
        _run("advance_srd_lasting_time", state, elapsed_rounds=rounds)


@pytest.mark.parametrize("modifier,failed", [(-100, True), (100, False)])
def test_actual_save_installs_only_failed_rider_and_applies_printed_damage(
    monkeypatch, modifier, failed
):
    state, data, params = _case(monkeypatch, modifier=modifier)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **params).result
    target = state[f"{SRD}.entities"]["guard"]
    damage = result["context"]["_spell_damage"]
    assert len(damage["results"]) == 10 and damage["count"] == 10 and damage["sides"] == 12
    assert 1000 - target["hp"] == (damage["total"] if failed else damage["total"] // 2)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
    assert target.get("befuddled", False) is failed
    if failed:
        source = _source(state)
        assert source["expires_tick"] is None and source["concentration_sequence"] == 0
        assert source["save_ability"] == "intelligence" and source["next_repeat_tick"] == 864000
        assert source["spell_level"] == 8
        assert source["dispel_ongoing"] is False
        assert "Cannot cast spells" in str(result["result_card"])
    else:
        assert not target.get("befuddlement_source_ids")


def test_magic_action_rejects_atomically_but_ordinary_action_remains_available(monkeypatch):
    state, data, params = _case(monkeypatch)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    combat = state[f"{SRD}.combat"]
    combat.update(
        turn_active=True,
        encounter_active=True,
        active_actor="guard",
        action_available=True,
        action_surge_available=False,
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Befuddlement"):
        _run("spend_srd_action", state, magic_action=True)
    assert state == before
    _run("spend_srd_action", state, magic_action=False)
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True


@pytest.mark.parametrize(
    "spell,tool",
    [("fire_bolt", "cast_spell_outside_combat"), ("prayer_of_healing", "begin_srd_spell_cast")],
)
def test_spell_cast_and_long_cast_start_reject_without_spending(monkeypatch, spell, tool):
    state, data, params = _case(monkeypatch, spell)
    _install(state, "hero")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Befuddlement"):
        _run(
            tool,
            state,
            participant_data=data,
            **(_begin(params) if tool.startswith("begin") else params),
        )
    assert state == before


def test_repeat_deadlines_roll_once_each_thirty_days_and_success_ends_source(monkeypatch):
    state, data, params = _case(monkeypatch)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    source = _source(state)
    for rounds in (100000, 100000, 100000, 100000, 31999):
        _run("advance_srd_lasting_time", state, elapsed_rounds=rounds)
    assert source["last_repeat_save"] is None
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    source = _source(state)
    assert source["last_repeat_save"]["count"] == 1 and source["last_repeat_save"]["sides"] == 20
    assert source["last_repeat_outcome"] == "failure" and source["next_repeat_tick"] == 1728000
    last = deepcopy(source["last_repeat_save"])
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _source(state)["last_repeat_save"] == last
    source_id = state[f"{SRD}.entities"]["guard"]["befuddlement_source_ids"][0]
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["intelligence"] = 100
    _thirty_days(state)
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert source["last_repeat_outcome"] == "success" and source["installed"] is False
    assert not state[f"{SRD}.entities"]["guard"]["befuddled"]
    assert not state[f"{SRD}.state"]["has_befuddlement_sources"]


@pytest.mark.parametrize("spell,remaining", [("greater_restoration", 1), ("heal", 0)])
def test_actual_named_cure_retires_selected_sources_and_preserves_independent_condition(
    monkeypatch, spell, remaining
):
    state, data, params = _case(monkeypatch, spell)
    first = _install(state)
    second = _install(state, dc=25)
    independent = _control_source(
        state, spell="charm_person", condition="charmed", profile="friendly_charm"
    )
    if spell == "greater_restoration":
        params.update(recovery_choice="befuddlement", recovery_source_id=first)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    target = state[f"{SRD}.entities"]["guard"]
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    assert len(target["befuddlement_source_ids"]) == remaining
    assert sources[first]["installed"] is False
    assert sources[second]["installed"] is bool(remaining)
    assert sources[independent]["installed"] and "charmed" in target["conditions"]
    assert target["befuddled"] is bool(remaining)
    assert state[f"{SRD}.entities"]["hero"]["resources"][params["slot_resource"]] == 2


def test_mind_blank_suppresses_and_expiry_restores_original_saved_rider(monkeypatch):
    state, data, params = _case(monkeypatch, "mind_blank")
    source_id = _install(state)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert source["installed"] and source["suppressed"]
    assert not state[f"{SRD}.entities"]["guard"]["befuddled"]
    before = tuple(state[f"{SRD}.entities"]["guard"]["befuddlement_source_ids"])
    _install(state)
    assert tuple(state[f"{SRD}.entities"]["guard"]["befuddlement_source_ids"]) == before
    _run("advance_srd_lasting_time", state, elapsed_rounds=14401)
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert source["installed"] and not source["suppressed"]
    assert state[f"{SRD}.entities"]["guard"]["befuddled"]
    assert source["next_repeat_tick"] == 864000


def test_actual_death_does_not_retire_rider_or_invent_corpse_save(monkeypatch):
    state, data, params = _case(monkeypatch)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    source_id = state[f"{SRD}.entities"]["guard"]["befuddlement_source_ids"][0]
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=9999,
        adjustment=0,
        damage_type="force",
        critical_hit=False,
    )
    assert not state[f"{SRD}.entities"]["guard"]["is_alive"]
    _thirty_days(state)
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert source["installed"] and source["last_repeat_save"] is None
    assert source["next_repeat_tick"] == 1728000


def test_dispel_ends_ongoing_condition_but_preserves_instantaneous_befuddlement(monkeypatch):
    state, data, params = _case(monkeypatch, "dispel_magic")
    source_id = _install(state)
    ongoing = _control_source(state, spell="blindness_deafness", condition="blinded")
    params["slot_resource"] = "spell_slot_9"
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    sources = state[f"{SRD}.state"]["timed_condition_sources"]
    assert sources[source_id]["installed"] and not sources[ongoing]["installed"]
    assert state[f"{SRD}.entities"]["guard"]["befuddled"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2


def test_public_paid_cast_reopens_then_actual_due_save_retry_preserves_roll(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "befuddlement.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = _create_spell_campaign(
            owner,
            "befuddlement",
            target_overrides={
                "hp": 1000,
                "max_hp": 1000,
                "saving_throw_modifiers": {
                    "strength": 0,
                    "dexterity": 0,
                    "constitution": 0,
                    "intelligence": -100,
                    "wisdom": 0,
                    "charisma": 0,
                },
            },
        )
        receipt, command = _act(owner, "cast_spell_outside_combat", **params)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["befuddled"]
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        for rounds in (100000, 100000, 100000, 100000, 32000):
            receipt, command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=rounds)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
        settled = owner.snapshot("casting")
        source_id = settled.state[f"{SRD}.entities"]["guard"]["befuddlement_source_ids"][0]
        source = settled.state[f"{SRD}.state"]["timed_condition_sources"][source_id]
        assert source["last_repeat_outcome"] == "failure" and source["last_repeat_save"]["results"]
        assert source["next_repeat_tick"] == 1728000
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == settled.state
