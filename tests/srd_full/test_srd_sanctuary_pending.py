"""Sanctuary retains its failed save before any attack roll, through loss or retargeting."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_reaction_spells import _mage
from tests.srd_full.test_srd_reaction_windows import _parameters
from tests.srd_full.test_srd_scene_relations import _state as _scene_state
from tests.srd_full.test_srd_spell_result_cards import _spell_case


def _blocked_attack(monkeypatch, npc=False):
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("sanctuary")
    _run("cast_spell", state, participant_data=data, **parameters)
    entities = state[f"{SRD}.entities"]
    entities["guard"].update(hp=1000, max_hp=1000)
    entities["other"].update(hp=1000, max_hp=1000)
    scene = deepcopy(_scene_state()[f"{SRD}.state"])
    scene["spatial_scene"]["entities"][1]["position"] = [1, 0, 0]
    other = deepcopy(scene["spatial_scene"]["entities"][1])
    other.update(id="other", position=[3, 0, 0])
    other["metadata"]["participant_ref"] = "other"
    scene["spatial_scene"]["entities"].append(other)
    state[f"{SRD}.state"]["spatial_scene"] = scene["spatial_scene"]
    if npc:
        state[f"{SRD}.combat"].update(active_actor="other", current_actor="other")
        state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["wisdom"] = -30
        begun = _run(
            "begin_srd_creature_attack",
            state,
            actor="other",
            target="guard",
            attack_name="Synthetic fixed damage",
            attack_modifier=7,
            damage_expression="fixed-1",
            damage_modifier=0,
            damage_type="piercing",
            attack_distance=1,
            attack_mode="melee",
            normal_range=5,
            long_range=5,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
            advantage=False,
            disadvantage=False,
        )
    else:
        begun = _run("begin_srd_character_attack", state, participant_data=data, **_parameters())
    event_id = begun.result["context"]["_pending_action_id"]
    event = state[f"{SRD}.state"]["pending_srd_actions"][event_id]
    assert event["stage"] == "awaiting_sanctuary_choice"
    assert "attack_roll" not in event
    assert event["sanctuary_saves"][0]["outcome"] == "failure"
    return state, data, event_id


@pytest.mark.parametrize("npc", [False, True], ids=["character", "creature"])
def test_losing_the_blocked_attack_keeps_the_cost_without_rolling_or_dealing_damage(
    monkeypatch, npc
):
    state, data, event_id = _blocked_attack(monkeypatch, npc)
    remaining = state[f"{SRD}.combat"]["attacks_remaining"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Sanctuary choice"):
        _run(
            "complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id
        )
    assert state == before
    _run(
        "decide_srd_sanctuary_target", state, pending_action_id=event_id, decision="lose_operation"
    )
    monkeypatch.setattr(
        dice, "roll", lambda *args, **kwargs: pytest.fail("A lost attack was rolled")
    )
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert state[f"{SRD}.combat"]["attacks_remaining"] == remaining
    assert state[f"{SRD}.state"]["pending_srd_action_stack"] == []


def test_retargeting_validates_actual_scene_range_and_rolls_only_the_replacement(monkeypatch):
    state, data, event_id = _blocked_attack(monkeypatch)
    event = state[f"{SRD}.state"]["pending_srd_actions"][event_id]
    saved = deepcopy(event["sanctuary_saves"])
    remaining = state[f"{SRD}.combat"]["attacks_remaining"]
    _run(
        "decide_srd_sanctuary_target",
        state,
        pending_action_id=event_id,
        decision="retarget",
        new_target_state_id="other",
    )
    other_scene = state[f"{SRD}.state"]["spatial_scene"]["entities"][2]
    other_scene["position"] = [50, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="reach|range"):
        _run(
            "resume_srd_sanctuary_attack", state, participant_data=data, pending_action_id=event_id
        )
    assert state == before
    other_scene["position"] = [3, 0, 0]
    _run("resume_srd_sanctuary_attack", state, participant_data=data, pending_action_id=event_id)
    event = state[f"{SRD}.state"]["pending_srd_actions"][event_id]
    assert event["stage"] == "reaction"
    assert event["target_state_id"] == "other"
    assert event["sanctuary_saves"] == saved
    assert event["attack_roll"] is not None
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert state[f"{SRD}.entities"]["other"]["hp"] < 1000
    assert state[f"{SRD}.combat"]["attacks_remaining"] == remaining


def _blocked_spell(monkeypatch, spell="magic_missile"):
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("sanctuary")
    _run("cast_spell", state, participant_data=data, **parameters)
    _mage(state, spell)
    entities = state[f"{SRD}.entities"]
    entities["mage"]["saving_throw_modifiers"]["wisdom"] = -30
    for target in ("guard", "other"):
        entities[target].update(hp=1000, max_hp=1000)
    scene = deepcopy(_scene_state()[f"{SRD}.state"]["spatial_scene"])
    scene["map"]["bounds"]["max"][0] = 200
    scene["entities"][1]["position"] = [10, 0, 0]
    for actor, position in (("mage", [5, 0, 0]), ("other", [20, 0, 0])):
        row = deepcopy(scene["entities"][1])
        row.update(id=actor, position=position)
        row["metadata"]["participant_ref"] = actor
        scene["entities"].append(row)
    state[f"{SRD}.state"]["spatial_scene"] = scene
    state[f"{SRD}.combat"].update(active_actor="mage", current_actor="mage",
                                  action_available=True)
    begun = _run("begin_srd_spell_action", state, spell=spell, caster_state="mage",
                 target="guard", target_distance=5, caster_can_see_target=True,
                 verbal_component_available=True, somatic_component_available=True)
    event_id = begun.result["context"]["_pending_action_id"]
    event = state[f"{SRD}.state"]["pending_srd_actions"][event_id]
    assert event["stage"] == "awaiting_sanctuary_choice"
    assert event["sanctuary_saves"][0]["outcome"] == "failure"
    assert state[f"{SRD}.combat"]["action_available"] is False
    return state, data, event_id


@pytest.mark.parametrize("spell", ["magic_missile", "fire_bolt", "ray_of_frost", "sacred_flame"])
def test_lost_damaging_spell_preserves_its_cast_cost_and_rolls_no_damage(monkeypatch, spell):
    state, data, event_id = _blocked_spell(monkeypatch, spell)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Sanctuary choice"):
        _run("complete_srd_pending_action", state, pending_action_id=event_id)
    assert state == before
    _run("decide_srd_sanctuary_target", state, pending_action_id=event_id,
         decision="lose_operation")
    monkeypatch.setattr(dice, "roll", lambda *a, **kw: pytest.fail("A lost spell rolled"))
    _run("complete_srd_pending_action", state, participant_data=data,
         pending_action_id=event_id)
    assert state[f"{SRD}.state"]["turn_effects"] == before[f"{SRD}.state"]["turn_effects"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.state"]["pending_srd_action_stack"] == []
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"][spell] == 1


def test_spell_retarget_keeps_failed_save_and_validates_actual_source_range(monkeypatch):
    state, data, event_id = _blocked_spell(monkeypatch)
    event = state[f"{SRD}.state"]["pending_srd_actions"][event_id]
    saves = deepcopy(event["sanctuary_saves"])
    _run("decide_srd_sanctuary_target", state, pending_action_id=event_id,
         decision="retarget", new_target_state_id="other")
    row = state[f"{SRD}.state"]["spatial_scene"]["entities"][-1]
    row["position"] = [130, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="source range"):
        _run("resume_srd_sanctuary_spell", state, pending_action_id=event_id)
    assert state == before
    row["position"] = [20, 0, 0]
    _run("resume_srd_sanctuary_spell", state, pending_action_id=event_id)
    event = state[f"{SRD}.state"]["pending_srd_actions"][event_id]
    assert event["stage"] == "reaction"
    assert event["sanctuary_saves"] == saves
    assert event["target_state_id"] == "other"
    _run("complete_srd_pending_action", state, participant_data=data,
         pending_action_id=event_id)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert state[f"{SRD}.entities"]["other"]["hp"] < 1000
    assert state[f"{SRD}.combat"]["action_available"] is False


def test_public_failed_spell_targeting_save_reopens_then_loses_spell_once(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "sanctuary-spell.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner, "sanctuary", target_overrides={"hp": 1000, "max_hp": 1000}
        )
        warded, _ = _act(owner, "cast_spell_outside_combat", **parameters)
        assert warded.status is ActionStatus.COMMITTED
        prepared, _ = _prepare_mage(
            owner, [{"spell": "magic_missile", "uses_per_day": 2}], wisdom_save=-10
        )
        assert prepared.status is ActionStatus.COMMITTED
        begun, begin_command = _act(
            owner, "begin_srd_spell_action", spell="magic_missile", caster_state="mage",
            target="guard", target_distance=5, caster_can_see_target=True,
            verbal_component_available=True, somatic_component_available=True,
        )
        assert begun.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        event_id = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        event = saved.state[f"{SRD}.state"]["pending_srd_actions"][event_id]
        assert event["stage"] == "awaiting_sanctuary_choice"
        assert event["sanctuary_saves"][0]["outcome"] == "failure"
        assert owner.execute_action(begin_command) == begun
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(begin_command) == begun
        chosen, choice_command = _act(
            owner, "decide_srd_sanctuary_target", pending_action_id=event_id,
            decision="lose_operation",
        )
        assert chosen.status is ActionStatus.COMMITTED
        assert owner.execute_action(choice_command) == chosen
        finished, finish_command = _act(
            owner, "complete_srd_pending_action", pending_action_id=event_id
        )
        assert finished.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert after.state[f"{SRD}.entities"]["guard"]["hp"] == 1000
        assert after.state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["magic_missile"] == 1
        assert after.state[f"{SRD}.state"]["pending_srd_action_stack"] == []
        summary = finished.result["context"]["_spell_card_summary"]
        assert isinstance(summary, list)
        assert "lost after failed Sanctuary" in str(summary)
        assert owner.execute_action(finish_command) == finished
        assert owner.snapshot("casting").state == after.state


def test_public_failed_targeting_save_reopens_and_retries_before_losing_the_attack(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "sanctuary.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner,
            "sanctuary",
            target_overrides={"hp": 1000, "max_hp": 1000},
        )
        cast, _ = _act(owner, "cast_spell_outside_combat", **parameters)
        assert cast.status is ActionStatus.COMMITTED
        started, _ = _act(
            owner, "start_group_combat_encounter", combatants=["hero", "guard", "other"]
        )
        assert started.status is ActionStatus.COMMITTED
        turn, _ = _act(owner, "begin_combat_turn")
        assert turn.status is ActionStatus.COMMITTED
        attack_parameters = _parameters()
        attack_parameters.pop("actor_state")
        begun, begin_command = _act(owner, "begin_srd_character_attack", **attack_parameters)
        assert begun.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        event_id = saved.state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        event = saved.state[f"{SRD}.state"]["pending_srd_actions"][event_id]
        assert event["stage"] == "awaiting_sanctuary_choice"
        assert "attack_roll" not in event
        assert owner.execute_action(begin_command) == begun
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(begin_command) == begun
        chosen, choice_command = _act(
            owner,
            "decide_srd_sanctuary_target",
            pending_action_id=event_id,
            decision="lose_operation",
        )
        assert chosen.status is ActionStatus.COMMITTED
        assert owner.execute_action(choice_command) == chosen
        finished, finish_command = _act(
            owner, "complete_srd_pending_action", pending_action_id=event_id
        )
        assert finished.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert after.state[f"{SRD}.entities"]["guard"]["hp"] == 1000
        assert (
            after.state[f"{SRD}.combat"]["attacks_remaining"]
            == saved.state[f"{SRD}.combat"]["attacks_remaining"]
        )
        assert after.state[f"{SRD}.state"]["pending_srd_action_stack"] == []
        assert owner.execute_action(finish_command) == finished
        assert owner.snapshot("casting").state == after.state
