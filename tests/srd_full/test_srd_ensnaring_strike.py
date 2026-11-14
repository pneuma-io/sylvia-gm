"""A paid saved weapon hit acquires real vines, with one later start-turn consequence."""

import json
from copy import deepcopy

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_attack_hit_riders as hit_owners
from tests import test_srd_public_spell_casting as public_casting
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_attack_hit_riders import BEGIN, Rolls, _begin_hit, _finish, _hit_case
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_reaction_windows import _parameters
from tests.srd_full.test_srd_saved_hit_type_receipts import _admit_spell, _mechanic, _runtime
from tests.srd_full.test_srd_spell_damage_rider_consequences import _start


class HitThenSave(Rolls):
    def __init__(self, *saves):
        super().__init__(20, 1)
        self.d20s = [20, *saves]

    def randint(self, a, b):
        self.sides.append(b)
        return self.d20s.pop(0) if b == 20 and self.d20s else 1


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", HitThenSave(1, 1, 20))


def _paid_hit(*, slot=2, size=2, modifier=-100):
    state, data, params = _hit_case("ensnaring_strike", slot)
    target = state[f"{SRD}.entities"]["guard"]
    target["size_rank"] = size
    target["saving_throw_modifiers"]["strength"] = modifier
    target["skill_check_modifiers"] = {"athletics": 100}
    hit = _begin_hit(state, data)
    _finish(state, data, hit)
    paid = params | {"rider_pending_action_id": hit}
    child = _run(BEGIN, state, participant_data=data, **paid).result["context"][
        "_pending_action_id"
    ]
    state = json.loads(json.dumps(state))
    _finish(state, data, child)
    event = state[f"{SRD}.state"]["pending_srd_actions"][hit]
    source_id = event["smite_cast_id"]
    return state, data, hit, source_id


def test_paid_critical_weapon_hit_acquires_exact_vines_without_initial_extra_damage_then_reopens():
    state, data, hit, source_id = _paid_hit(slot=3)
    entities = state[f"{SRD}.entities"]
    source = state[f"{SRD}.state"]["attack_rider_sources"][source_id]
    assert (
        source["target"] == "guard"
    )  # The public Self cast input is hero, not its real hit victim.
    assert source["slot_level"] == 3 and source["concentration"] is True
    assert "restrained" in entities["guard"]["conditions"]
    assert "restrained" not in entities["hero"]["conditions"]
    condition = state[f"{SRD}.state"]["timed_condition_sources"][source["condition_source_id"]]
    assert condition["repeat_save_timing"] == "none" and condition["cast_id"] == source_id
    assert entities["guard"]["hp"] == 100
    assert state[f"{SRD}.state"]["pending_srd_actions"][hit].get("smite_component") is None
    context = _finish(state, data, hit)
    assert context["_damage_incoming"] == 2 + data["abilities"]["strength"] // 2 - 5
    assert dice._rng.sides.count(20) == 2  # Original attack plus exactly one initial Strength save.
    assert entities["hero"]["resources"]["spell_slot_3"] == 2
    state = json.loads(json.dumps(state))
    turn = _start(state)
    row = next(row for row in turn["_turn_effect_rows"] if row.get("kind") == "Ensnaring Strike")
    assert row["damage_roll"]["count"] == 3 and row["value"] == 3
    assert dice._rng.sides.count(20) == 2  # No start/end repeat save exists in this spell.
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="new start turn"):
        _run("resolve_srd_ensnaring_strike_turn", state, target_state="guard", source_id=source_id)
    assert state == before
    escaped = _run("escape_srd_ensnaring_strike", state, actor_state="guard", source_id=source_id)
    assert escaped.result["context"]["_ensnaring_escape_outcome"] == "success"
    assert state[f"{SRD}.combat"]["action_taken"] == "escape_ensnaring_strike"
    assert state[f"{SRD}.state"]["attack_rider_sources"][source_id]["installed"] is False
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


@pytest.mark.parametrize("size,faces", [(2, 1), (3, 2)])
def test_large_advantage_applies_only_to_initial_save_and_success_ends_exact_concentration(
    size, faces
):
    state, data, hit, source_id = _paid_hit(size=size, modifier=100)
    source = state[f"{SRD}.state"]["attack_rider_sources"][source_id]
    assert len(source["ensnaring_save"]["results"]) == faces
    assert source["ensnaring_save_outcome"] == "success" and source["installed"] is False
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    _finish(state, data, hit)
    assert state[f"{SRD}.state"]["pending_srd_actions"] == {}


def test_exact_ending_preserves_an_independent_restraint_source_and_clock_ends_vines():
    state, data, hit, source_id = _paid_hit()
    _finish(state, data, hit)
    other_id = _run(
        "install_srd_timed_condition_source",
        state,
        target_state="guard",
        lasting_state="state",
        spell="entangle",
        condition="restrained",
        save_dc=15,
        duration_rounds=100,
        repeat_save_timing="none",
    ).result["context"]["_timed_condition_source_id"]
    _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=20)
    assert state[f"{SRD}.state"]["attack_rider_sources"][source_id]["installed"] is False
    assert state[f"{SRD}.state"]["timed_condition_sources"][other_id]["installed"] is True
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_actual_helper_body_reach_spends_action_and_ends_only_the_selected_vines():
    state, data, hit, source_id = _paid_hit()
    _finish(state, data, hit)
    _start(state, "other")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    # The victim's actual nearest face is in reach despite its distant center.
    for row in scene["entities"]:
        if row["id"] == "guard":
            row["position"] = [20, 0, 0]
            row["footprint"]["half_extents"] = [10, 10]
        elif row["id"] == "other":
            row["position"] = [5, 0, 0]
        elif row["id"] == "hero":
            row["position"] = [-20, 20, 0]
    state[f"{SRD}.entities"]["other"]["skill_check_modifiers"] = {"athletics": 100}
    escaped = _run("escape_srd_ensnaring_strike", state, actor_state="other", source_id=source_id)
    assert escaped.result["context"]["_ensnaring_escape_outcome"] == "success"
    assert state[f"{SRD}.combat"]["action_taken"] == "escape_ensnaring_strike"
    assert state[f"{SRD}.state"]["attack_rider_sources"][source_id]["installed"] is False


def test_actual_ranged_weapon_hit_can_pay_ensnaring_without_melee_or_initial_bonus_damage():
    state, data, params = _hit_case("ensnaring_strike", 1)
    state[f"{SRD}.entities"]["guard"]["size_rank"] = 2
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["strength"] = -100
    state[f"{SRD}.entities"]["hero"]["inventory"].append("longbow")
    state[f"{SRD}.entities"]["hero"]["ammunition"] = {"arrow": 2}
    data["weapon_proficiencies"].append("martial_weapons")
    hit = _run(
        "begin_srd_character_attack",
        state,
        participant_data=data,
        **(
            _parameters()
            | {
                "weapon": "longbow",
                "attack_mode": "ranged",
                "attack_ability": "dexterity",
                "wielding_two_handed": True,
                hit_owners.INTENT: True,
            }
        ),
    ).result["context"]["_pending_action_id"]
    _finish(state, data, hit)
    child = _run(
        BEGIN, state, participant_data=data, **(params | {"rider_pending_action_id": hit})
    ).result["context"]["_pending_action_id"]
    _finish(state, data, child)
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["pending_srd_actions"][hit].get("smite_component") is None
    _finish(state, data, hit)


def test_real_public_paid_ensnaring_hit_and_retained_turn_survive_database_reopen_and_retries(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(hit_owners, "_runtime", _runtime)
    monkeypatch.setattr(public_casting, "bundled_srd_archive", hit_owners._archive)

    def case(_spell):
        state, data, params = _hit_case("ensnaring_strike", 2)
        state[f"{SRD}.entities"]["guard"]["size_rank"] = 2
        state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["strength"] = -100
        return state, data, params

    monkeypatch.setattr(public_casting, "_spell_case", case)
    database = tmp_path / "ensnaring.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(
            owner, "ensnaring_strike", target_overrides={"hp": 100}
        )
        _act(owner, "start_group_combat_encounter", combatants=["hero", "guard", "other"])
        _act(owner, "begin_combat_turn")
        dice._rng = HitThenSave(1)
        attack = _parameters() | {hit_owners.INTENT: True}
        attack.pop("actor_state")
        begun, command = _act(owner, "begin_srd_character_attack", **attack)
        assert begun.status is ActionStatus.COMMITTED
        hit = begun.result["context"]["_pending_action_id"]
        paused, paused_command = _act(owner, "complete_srd_pending_action", pending_action_id=hit)
        assert paused.status is ActionStatus.COMMITTED
        assert owner.execute_action(command) == begun
        assert owner.execute_action(paused_command) == paused
        before = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == before
        paid = params | {"rider_pending_action_id": hit}
        paid.pop("rider_ability")
        child, child_command = _act(owner, BEGIN, **paid)
        assert child.status is ActionStatus.COMMITTED
        child_id = child.result["context"]["_pending_action_id"]
        resolved, resolved_command = _act(
            owner, "complete_srd_pending_action", pending_action_id=child_id
        )
        assert resolved.status is ActionStatus.COMMITTED
        assert owner.execute_action(child_command) == child
        assert owner.execute_action(resolved_command) == resolved
        retained = owner.snapshot("casting")
        assert retained.state[f"{SRD}.entities"]["guard"]["hp"] == 100
        assert "restrained" in retained.state[f"{SRD}.entities"]["guard"]["conditions"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == retained
        finished, finish_command = _act(owner, "complete_srd_pending_action", pending_action_id=hit)
        assert finished.status is ActionStatus.COMMITTED
        _act(owner, "end_combat_turn")
        _act(owner, "advance_group_combat_encounter_turn")
        turn, turn_command = _act(owner, "begin_combat_turn")
        assert turn.status is ActionStatus.COMMITTED
        assert any(
            row.get("kind") == "Ensnaring Strike"
            for row in turn.result["context"]["_turn_effect_rows"]
        )
        final = owner.snapshot("casting")
        assert owner.execute_action(finish_command) == finished
        assert owner.execute_action(turn_command) == turn
        assert owner.snapshot("casting") == final


def test_paid_freedom_of_movement_suppresses_exact_magical_vines_and_start_damage():
    state, data, params = _hit_case("ensnaring_strike", 1)
    data["classes"] = [{"id": "ranger", "level": 10}, {"id": "cleric", "level": 7}]
    freedom = _admit_spell(state, data, "freedom_of_movement")
    _run("cast_spell", state, participant_data=data, **freedom)
    _start(state, "hero")
    target = state[f"{SRD}.entities"]["guard"]
    target["size_rank"] = 2
    target["saving_throw_modifiers"]["strength"] = -100
    dice._rng = HitThenSave(1)
    hit = _begin_hit(state, data)
    _finish(state, data, hit)
    child = _run(
        BEGIN, state, participant_data=data, **(params | {"rider_pending_action_id": hit})
    ).result["context"]["_pending_action_id"]
    _finish(state, data, child)
    source_id = state[f"{SRD}.state"]["pending_srd_actions"][hit]["smite_cast_id"]
    source = state[f"{SRD}.state"]["attack_rider_sources"][source_id]
    assert source["installed"] is True and source["ensnaring_save_outcome"] == "failure"
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    _finish(state, data, hit)
    turn = _start(state)
    row = next(row for row in turn["_turn_effect_rows"] if row.get("kind") == "Ensnaring Strike")
    assert row["value"] == 0 and row["damage_roll"] is None
    assert state[f"{SRD}.state"]["attack_rider_sources"][source_id]["installed"] is True
