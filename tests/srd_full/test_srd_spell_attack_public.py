"""Public scalar spell hits retain paid casts and apply only resolved consequences."""

from copy import deepcopy
from dataclasses import replace
from random import Random
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, StateValue
from sylvia_gm.domain.errors import MechanicsRejected
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_public_spell_casting import SRD, _create_spell_campaign
from tests.srd_full.test_srd_scene_relations import _state as _scene_state


class _RecordedRandom(Random):
    """Record actual random draws without supplying dice values or attack verdicts."""

    def __init__(self, seed):
        super().__init__(seed)
        self.draws = []

    def randint(self, a, b):
        result = super().randint(a, b)
        self.draws.append((a, b, result))
        return result


class _NoAttackReroll(_RecordedRandom):
    """Allow real damage dice while detecting any repeat of the retained D20."""

    def randint(self, a, b):
        assert (a, b) != (1, 20), "The saved spell attack was rolled again"
        return super().randint(a, b)


def _campaign(owner, spell, *, npc=False):
    """Admit synthetic scene data before preparing the real reacting or casting NPC."""
    scene = deepcopy(_scene_state()[f"{SRD}.state"]["spatial_scene"])
    scene["entities"][1].update(id="mage", position=[10, 0, 0])
    scene["entities"][1]["metadata"]["participant_ref"] = "mage"
    create = owner.create_campaign

    def admit_scene(command):
        initial = list(command.initial_state)
        for index, value in enumerate(initial):
            if value.namespace == SRD and value.key == "state":
                initial[index] = replace(value, value=value.value | {"spatial_scene": scene})
                break
        else:
            initial.append(StateValue(SRD, "state", {"spatial_scene": scene}))
        return create(replace(command, initial_state=tuple(initial)))

    with patch.object(owner, "create_campaign", side_effect=admit_scene):
        _, _, parameters = _create_spell_campaign(
            owner,
            "shield" if npc else spell,
            entity_overrides={
                "armor_class": 13,
                "hp": 1000,
                "max_hp": 1000,
                "temp_hp": 0,
                "initiative_modifier": -10 if npc else 20,
            },
        )
    prepared, _ = _act(
        owner,
        "prepare_srd_creature",
        creature_id="mage",
        name="Synthetic Mage",
        source="Synthetic stat block: AC 13, 1000 HP, spell attack +10, DC 15.",
        armor_class=13,
        hit_points=1000,
        speed=30,
        initiative_modifier=20 if npc else -10,
        size_rank=2,
        constitution_save=6,
        spellcasting_ability="intelligence",
        spellcasting_modifier=4,
        spell_attack_bonus=10,
        spell_save_dc=15,
        spells=[{"spell": spell if npc else "shield", "uses_per_day": 2}],
    )
    assert prepared.status is ActionStatus.COMMITTED, prepared.result
    started, _ = _act(
        owner,
        "start_combat_encounter_entities",
        first_actor="hero",
        second_actor="mage",
        first_surprised=False,
        second_surprised=False,
        tie_winner="first",
    )
    assert started.status is ActionStatus.COMMITTED, started.result
    turn, _ = _act(owner, "begin_combat_turn")
    assert turn.status is ActionStatus.COMMITTED, turn.result
    actor, target = ("mage", "hero") if npc else ("hero", "mage")
    assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == actor
    parameters.update(spell=spell, caster_state=actor, target=target, target_distance=10)
    if npc:
        parameters.pop("caster")
        parameters.pop("slot_resource")
    return parameters, actor, target


def _shield(owner, target, trigger):
    parameters = {
        "spell": "shield",
        "caster_state": target,
        "reaction_trigger_id": trigger,
        "verbal_component_available": True,
        "somatic_component_available": True,
    }
    if target == "hero":
        parameters.update(caster="hero", slot_resource="spell_slot_1")
    return _act(owner, "cast_srd_reaction_spell", **parameters)


def _assert_replay(owner, command, receipt, state):
    assert owner.execute_action(command) == receipt
    assert owner.snapshot("casting").state == state


@pytest.mark.parametrize("npc", [False, True], ids=["character-slot", "npc-use"])
@pytest.mark.parametrize("seed,critical", [(1, False), (5, True)], ids=["hit", "critical"])
def test_public_actual_hit_shield_reopens_retries_and_preserves_paid_cast(
    tmp_path,
    monkeypatch,
    npc,
    seed,
    critical,
):
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "scalar-hit.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        parameters, actor, target = _campaign(owner, "guiding_bolt", npc=npc)
        before = owner.snapshot("casting").state
        begun, begin_command = _act(owner, "begin_srd_spell_action", **parameters)
        assert begun.status is ActionStatus.COMMITTED, begun.result
        opened = owner.snapshot("casting").state
        trigger = opened[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        with pytest.raises(MechanicsRejected, match="actual attack hit"):
            _shield(owner, target, trigger)
        assert owner.snapshot("casting").state == opened
        monkeypatch.setattr(dice, "_rng", Random(seed))
        rolled, roll_command = _act(owner, "complete_srd_pending_action", pending_action_id=trigger)
        assert rolled.status is ActionStatus.COMMITTED, rolled.result
        hit_state = owner.snapshot("casting").state
        event = hit_state[f"{SRD}.state"]["pending_srd_actions"][trigger]
        retained = event["spell_attack_context"]["_spell_attack"]
        assert retained["results"] == [20 if critical else 5]
        assert event["attack_outcome"] == ("critical_hit" if critical else "hit")
        assert event["stage"] == "spell_attack_reaction"
        assert hit_state[f"{SRD}.entities"][target]["hp"] == 1000
        assert hit_state[f"{SRD}.combat"]["action_available"] is False
        assert not hit_state[f"{SRD}.state"].get("turn_effects")
        caster = hit_state[f"{SRD}.entities"][actor]
        if npc:
            assert caster["spell_uses_remaining"]["guiding_bolt"] == 1
            assert caster["resources"] == {}
        else:
            assert caster["resources"]["spell_slot_1"] == 2
        _assert_replay(owner, begin_command, begun, hit_state)
        _assert_replay(owner, roll_command, rolled, hit_state)
        shielded, shield_command = _shield(owner, target, trigger)
        assert shielded.status is ActionStatus.COMMITTED, shielded.result
        saved = owner.snapshot("casting").state
        assert saved[f"{SRD}.entities"][target]["reaction_available"] is False
        if npc:
            assert saved[f"{SRD}.entities"][target]["resources"]["spell_slot_1"] == 2
        else:
            assert saved[f"{SRD}.entities"][target]["spell_uses_remaining"]["shield"] == 1
        _assert_replay(owner, shield_command, shielded, saved)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved
        monkeypatch.setattr(dice, "_rng", _NoAttackReroll(2))
        for command, receipt in (
            (begin_command, begun),
            (roll_command, rolled),
            (shield_command, shielded),
        ):
            _assert_replay(owner, command, receipt, saved)
        finished, finish_command = _act(
            owner,
            "complete_srd_pending_action",
            pending_action_id=trigger,
        )
        assert finished.status is ActionStatus.COMMITTED, finished.result
        after = owner.snapshot("casting").state
        assert finished.result["context"]["_spell_attack"] == retained
        assert finished.result["context"]["_spell_attack_kind"] == (
            "critical_hit" if critical else "miss"
        )
        assert (after[f"{SRD}.entities"][target]["hp"] < 1000) is critical
        assert bool(after[f"{SRD}.state"].get("turn_effects")) is critical
        assert after[f"{SRD}.entities"][actor]["resources"] == caster["resources"]
        assert (
            after[f"{SRD}.entities"][target]["resources"]
            == (saved[f"{SRD}.entities"][target]["resources"])
        )
        assert (
            after[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]
            == (saved[f"{SRD}.entities"]["mage"]["spell_uses_remaining"])
        )
        if npc:
            assert (
                after[f"{SRD}.entities"][actor]["spell_uses_remaining"]
                == (caster["spell_uses_remaining"])
            )
        assert after[f"{SRD}.combat"]["action_available"] is False
        assert after[f"{SRD}.state"]["pending_srd_action_stack"] == []
        _assert_replay(owner, finish_command, finished, after)
        assert before[f"{SRD}.entities"][target]["hp"] == 1000


def test_public_original_miss_closes_paid_cast_without_a_shield_window(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    with AuthorityOwner.open(tmp_path / "scalar-miss.db", "casting-owner") as owner:
        parameters, _, target = _campaign(owner, "guiding_bolt")
        begun, _ = _act(owner, "begin_srd_spell_action", **parameters)
        assert begun.status is ActionStatus.COMMITTED
        trigger = owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        rng = _RecordedRandom(2)
        monkeypatch.setattr(dice, "_rng", rng)
        finished, command = _act(owner, "complete_srd_pending_action", pending_action_id=trigger)
        assert finished.status is ActionStatus.COMMITTED, finished.result
        saved = owner.snapshot("casting").state
        assert rng.draws == [(1, 20, 2)]
        assert finished.result["context"]["_spell_summary_rows"] == [
            {"label": "Synthetic Mage", "value": "Miss; damage 0"},
        ]
        card = finished.result["result_card"]
        assert card["title"] == "Guiding Bolt"
        assert any(
            row["label"] == "Synthetic Mage" and row["value"] == "Miss; damage 0"
            for row in card["summary"]
        )
        assert saved[f"{SRD}.entities"][target]["hp"] == 1000
        assert saved[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert saved[f"{SRD}.combat"]["action_available"] is False
        assert saved[f"{SRD}.state"]["pending_srd_action_stack"] == []
        assert not saved[f"{SRD}.state"].get("turn_effects")
        with pytest.raises(MechanicsRejected):
            _shield(owner, target, trigger)
        assert owner.snapshot("casting").state == saved
        _assert_replay(owner, command, finished, saved)


def test_public_direct_spell_path_is_atomic_and_keeps_immediate_consequences(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    with AuthorityOwner.open(tmp_path / "scalar-direct.db", "casting-owner") as owner:
        parameters, _, target = _campaign(owner, "guiding_bolt")
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="Total Cover"):
            _act(owner, "cast_spell", **(parameters | {"cover": "total"}))
        assert owner.snapshot("casting").state == before.state
        assert owner.snapshot("casting").revision == before.revision
        monkeypatch.setattr(dice, "_rng", Random(1))
        receipt, command = _act(owner, "cast_spell", **parameters)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting").state
        assert saved[f"{SRD}.entities"][target]["hp"] < 1000
        assert saved[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert saved[f"{SRD}.combat"]["action_available"] is False
        assert saved[f"{SRD}.state"]["turn_effects"]
        assert not saved[f"{SRD}.state"].get("pending_srd_action_stack")
        _assert_replay(owner, command, receipt, saved)
        with pytest.raises(MechanicsRejected, match="action budget"):
            _act(owner, "cast_spell", **parameters)
        assert owner.snapshot("casting").state == saved


@pytest.mark.parametrize("shielded", [True, False], ids=["shield-miss", "unshielded-hit"])
def test_public_paid_acid_arrow_reopens_then_only_actual_hit_delays_damage(
    tmp_path,
    monkeypatch,
    shielded,
):
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "acid-arrow.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        parameters, _, target = _campaign(owner, "acid_arrow")
        parameters["slot_resource"] = "spell_slot_4"
        begun, _ = _act(owner, "begin_srd_spell_action", **parameters)
        assert begun.status is ActionStatus.COMMITTED
        trigger = owner.snapshot("casting").state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        monkeypatch.setattr(dice, "_rng", Random(1))
        rolled, _ = _act(owner, "complete_srd_pending_action", pending_action_id=trigger)
        assert rolled.status is ActionStatus.COMMITTED, rolled.result
        saved = owner.snapshot("casting").state
        assert saved[f"{SRD}.entities"][target]["hp"] == 1000
        assert saved[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
        assert not saved[f"{SRD}.state"].get("turn_effects")
        if shielded:
            shield, _ = _shield(owner, target, trigger)
            assert shield.status is ActionStatus.COMMITTED, shield.result
            saved = owner.snapshot("casting").state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved
        rng = _NoAttackReroll(9)
        monkeypatch.setattr(dice, "_rng", rng)
        finished, finish_command = _act(
            owner,
            "complete_srd_pending_action",
            pending_action_id=trigger,
        )
        assert finished.status is ActionStatus.COMMITTED, finished.result
        after = owner.snapshot("casting").state
        assert len(rng.draws) == 6 and all(draw[:2] == (1, 4) for draw in rng.draws)
        initial_damage = 1000 - after[f"{SRD}.entities"][target]["hp"]
        assert initial_damage == sum(draw[2] for draw in rng.draws) // (2 if shielded else 1)
        assert bool(after[f"{SRD}.state"].get("turn_effects")) is not shielded
        assert after[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
        assert after[f"{SRD}.combat"]["action_available"] is False
        _assert_replay(owner, finish_command, finished, after)
        caster_ended, _ = _act(owner, "end_combat_turn")
        assert caster_ended.status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state[f"{SRD}.entities"][target]["hp"] == (
            1000 - initial_damage
        )
        advanced, _ = _act(owner, "advance_combat_encounter_turn")
        assert advanced.status is ActionStatus.COMMITTED, advanced.result
        turn, _ = _act(owner, "begin_combat_turn")
        assert turn.status is ActionStatus.COMMITTED, turn.result
        target_started = owner.snapshot("casting").state
        assert target_started[f"{SRD}.combat"]["active_actor"] == target
        assert target_started[f"{SRD}.entities"][target]["hp"] == 1000 - initial_damage
        ended, end_command = _act(owner, "end_combat_turn")
        assert ended.status is ActionStatus.COMMITTED, ended.result
        final = owner.snapshot("casting").state
        if shielded:
            assert len(rng.draws) == 6
            assert final[f"{SRD}.entities"][target]["hp"] == 1000 - initial_damage
            assert ended.result["result_card"] is None
        else:
            assert len(rng.draws) == 10 and all(draw[:2] == (1, 4) for draw in rng.draws)
            assert final[f"{SRD}.entities"][target]["hp"] == (
                1000 - initial_damage - sum(draw[2] for draw in rng.draws[6:])
            )
            assert "4d4" in str(ended.result["result_card"])
        assert not final[f"{SRD}.state"].get("turn_effects")
        assert final[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
        _assert_replay(owner, end_command, ended, final)
