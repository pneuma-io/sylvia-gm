"""A saved intervention window consumes the original attack once and retains its real roll."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_attack_budget import _combat_case
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import _spell_case


def _parameters():
    return {
        "actor": "hero",
        "actor_state": "hero",
        "target": "guard",
        "attack_sequence": "primary",
        "use_graze_on_miss": False,
        "weapon": "spear",
        "attack_mode": "melee",
        "attack_ability": "strength",
        "wielding_two_handed": False,
        "mounted": False,
        "circumstantial_modifier": 0,
        "advantage": False,
        "disadvantage": False,
        "actor_can_see_target": True,
        "target_can_see_actor": True,
        "target_is_charmer": False,
        "fear_source_in_sight": False,
        "attack_distance": 5,
    }


def _begin(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _combat_case()
    out = _run("begin_srd_character_attack", state, participant_data=data, **_parameters())
    return state, data, out.result["context"]["_pending_action_id"]


def test_pending_attack_spends_cost_and_preserves_roll_before_damage(monkeypatch):
    state, data, event_id = _begin(monkeypatch)
    combat = state[f"{SRD}.combat"]
    event = state[f"{SRD}.state"]["pending_srd_actions"][event_id]
    original_roll = deepcopy(event["attack_roll"])
    assert combat["action_available"] is False
    assert combat["attacks_remaining"] == 1
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    result = _run(
        "complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id
    ).result["context"]
    assert result["_attack_roll"] == original_roll
    assert result["_attack_outcome"] == event["attack_outcome"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 1000
    assert combat["attacks_remaining"] == 1
    assert state[f"{SRD}.state"]["pending_srd_action_stack"] == []
    assert state[f"{SRD}.state"]["pending_srd_actions"] == {}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="does not exist"):
        _run(
            "complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id
        )
    assert state == before


def test_intervening_armor_defense_changes_the_original_hit_without_reroll(monkeypatch):
    state, data, event_id = _begin(monkeypatch)
    event = state[f"{SRD}.state"]["pending_srd_actions"][event_id]
    assert event["attack_outcome"] == "hit"
    state[f"{SRD}.entities"]["guard"]["armor_class"] = event["attack_total"] + 1
    result = _run(
        "complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id
    ).result["context"]
    assert result["_attack_outcome"] == "miss"
    assert result["_attack_roll"] == event["attack_roll"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000


def test_actual_attacks_record_fighting_relationships_without_changing_holdings(monkeypatch):
    state, data, event_id = _begin(monkeypatch)
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id)
    entities = state[f"{SRD}.entities"]
    assert entities["hero"]["fighting_actor_ids"] == ["guard"]
    assert entities["guard"]["fighting_actor_ids"] == ["hero"]
    assert entities["hero"]["inventory"] == ["spear", "dagger", "scimitar"]
    _run("clear_srd_encounter_transients", state, creature="hero")
    assert state[f"{SRD}.entities"]["hero"]["fighting_actor_ids"] == []


def test_pending_window_cannot_be_bypassed_by_extra_attack_or_elapsed_time(monkeypatch):
    state, data, event_id = _begin(monkeypatch)
    for name, parameters in (
        ("begin_srd_character_attack", _parameters()),
        ("resolve_combat_attack", _parameters()),
        ("advance_srd_lasting_time", {"elapsed_rounds": 1}),
    ):
        combat = state[f"{SRD}.combat"]
        original_encounter = combat["encounter_active"]
        if name == "advance_srd_lasting_time":
            combat["encounter_active"] = False
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match="reaction window"):
            _run(name, state, participant_data=data, **parameters)
        assert state == before
        combat["encounter_active"] = original_encounter
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id)


def test_pending_cast_reserves_action_then_spends_slot_once_at_completion():
    state, data, parameters = _spell_case("magic_missile")
    hp_before = state[f"{SRD}.entities"]["guard"]["hp"]
    before = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    out = _run("begin_srd_spell_action", state, participant_data=data, **parameters)
    event_id = out.result["context"]["_pending_action_id"]
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp_before
    assert state[f"{SRD}.entities"]["hero"]["resources"] == before
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id)
    assert state[f"{SRD}.entities"]["guard"]["hp"] < hp_before
    assert (
        state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == before["spell_slot_1"] - 1
    )
    assert state[f"{SRD}.entities"]["hero"]["pending_srd_completion_id"] is None


def test_public_pending_cast_reopens_and_retries_both_stages_without_repeating_effect(tmp_path):
    database = tmp_path / "reaction-window.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner, "magic_missile", target_overrides={"hp": 1000, "max_hp": 1000},
        )
        begun, command = _act(owner, "begin_srd_spell_action", **parameters)
        assert begun.status is ActionStatus.COMMITTED
        pending = owner.snapshot("casting")
        assert pending.state[f"{SRD}.entities"]["guard"]["hp"] == 1000
        assert pending.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 3
        event_id = pending.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        assert owner.execute_action(command) == begun
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == pending.state
        assert owner.execute_action(command) == begun
        finished, finish_command = _act(
            owner, "complete_srd_pending_action", pending_action_id=event_id,
        )
        assert finished.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert after.state[f"{SRD}.entities"]["guard"]["hp"] < 1000
        assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert after.state[f"{SRD}.state"]["pending_srd_actions"] == {}
        assert owner.execute_action(finish_command) == finished
        assert owner.snapshot("casting").state == after.state
