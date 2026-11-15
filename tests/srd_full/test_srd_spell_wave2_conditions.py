"""Source-owned control behavior changes persist and reach actual later consumers."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_attack_budget import _npc_attack
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene

SPELLS = ["sleep", "fear", "confusion", "enthrall", "irresistible_dance"]


def _case(monkeypatch, spell):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, params = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    for target in ("guard", "other"):
        state[f"{SRD}.entities"][target].update(hp=100, max_hp=100, temp_hp=5)
        state[f"{SRD}.entities"][target]["saving_throw_modifiers"]["wisdom"] = -100
    params["targets"] = ["guard"]
    if spell in ("fear", "confusion"):
        _admit_scene(state)
    return state, data, params


def _turn(state, actor="guard"):
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor=actor)
    _run("begin_combat_turn", state)


@pytest.mark.parametrize("spell", SPELLS)
def test_all_control_profiles_install_source_owned_effect_and_expire(monkeypatch, spell):
    state, data, params = _case(monkeypatch, spell)
    out = _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert out.result["status"] == "committed"
    assert out.result["result_card"]["details"][0]["label"] == "Guard"
    assert "Wisdom save:" in out.result["result_card"]["details"][0]["value"]
    assert "{" not in out.result["result_card"]["details"][0]["value"]
    target = state[f"{SRD}.entities"]["guard"]
    assert target["has_control_behavior_sources"] is True
    assert state[f"{SRD}.entities"]["hero"]["resources"][params["slot_resource"]] == 2
    if spell == "sleep":
        assert "incapacitated" in target["conditions"]
    if spell == "fear":
        assert "frightened" in target["conditions"]
    if spell == "irresistible_dance":
        assert "charmed" in target["conditions"]
        assert target["control_dance_active"] is True
    if spell == "enthrall":
        assert target["control_enthrall_penalty"] == 10
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert state[f"{SRD}.entities"]["guard"]["has_control_behavior_sources"] is False


def _cast(monkeypatch, spell, **choices):
    state, data, params = _case(monkeypatch, spell)
    out = _run("cast_spell_outside_combat", state, participant_data=data, **(params | choices))
    assert out.result["status"] == "committed"
    return state, data, params, out.result


def _save(state, ability="wisdom", kind="saving_throw", skill=None):
    return _run(
        "d20_test",
        state,
        actor_state="guard",
        ability=ability,
        kind=kind,
        ability_modifier=0,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=15,
        advantage=False,
        disadvantage=False,
        **({"skill": skill} if skill is not None else {}),
    ).result["context"]


def _reject(state, name, **parameters):
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(name, state, **parameters)
    assert state == before


@pytest.mark.parametrize("second_save_success", [False, True])
def test_sleep_second_save_on_next_turn_and_damage_release(monkeypatch, second_save_success):
    state, _, _, _ = _cast(monkeypatch, "sleep")
    _turn(state)
    assert state[f"{SRD}.combat"]["active_actor_can_act"] is False
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = (
        100 if second_save_success else -100
    )
    result = _run("end_combat_turn", state).result
    assert result["status"] == "committed"
    target = state[f"{SRD}.entities"]["guard"]
    assert len(target["last_control_behavior_save"]["results"]) == 1
    if second_save_success:
        assert "incapacitated" not in target["conditions"]
        assert "unconscious" not in target["conditions"]
    else:
        assert {"unconscious", "prone"} <= set(target["conditions"])
        _run("apply_srd_damage", state, target="guard", amount=1, adjustment=0, damage_type="fire")
        target = state[f"{SRD}.entities"]["guard"]
        assert target["hp"] == 100
        assert "unconscious" not in target["conditions"]
        assert "prone" in target["conditions"]


@pytest.mark.parametrize("trait", ["source", "does_not_sleep", "exhaustion"])
def test_sleep_ineligible_targets_automatically_save_and_pay_cast_cost(monkeypatch, trait):
    state, data, params = _case(monkeypatch, "sleep")
    target = state[f"{SRD}.entities"]["guard"]
    if trait == "does_not_sleep":
        target["does_not_sleep"] = True
    if trait == "exhaustion":
        target["condition_immunities"] = ["exhaustion"]
    choices = {"sleepless_targets": ["guard"]} if trait == "source" else {}
    result = _run(
        "cast_spell_outside_combat", state, participant_data=data, **(params | choices)
    ).result
    assert result["context"]["_control_save_rolls"] == [None]
    assert result["context"]["_control_save_outcomes"] == ["success"]
    assert "incapacitated" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.entities"]["hero"]["resources"][params["slot_resource"]] == 2


def test_sleep_shake_is_a_real_action_with_five_foot_boundary(monkeypatch):
    state, _, _, _ = _cast(monkeypatch, "sleep")
    _turn(state, "other")
    parameters = {"actor_state": "other", "target_state": "guard", "target_distance": 6}
    _reject(state, "shake_srd_sleeping_creature", **parameters)
    _run("shake_srd_sleeping_creature", state, **(parameters | {"target_distance": 5}))
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert "incapacitated" not in state[f"{SRD}.entities"]["guard"]["conditions"]


@pytest.mark.parametrize("visible", [False, True])
def test_fear_drops_held_items_and_spends_dash_then_saves_only_out_of_sight(monkeypatch, visible):
    state, data, params = _case(monkeypatch, "fear")
    state[f"{SRD}.entities"]["guard"]["inventory"] = ["spear", "shield"]
    _run(
        "record_srd_control_held_items",
        state,
        target_state="guard",
        held_items=["spear"],
        held_attack_names=["Spear"],
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["held_items"] == []
    assert target["dropped_items"] == ["spear"]
    assert target["inventory"] == ["shield"]
    if not visible:
        state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [{
            "id": "wall", "map_id": "orders", "active": True,
            "volume": {"kind": "box", "min": [10, -10, -10], "max": [11, 10, 10]},
            "movement": "blocking", "visibility": "opaque", "metadata": {},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }]
    _turn(state)
    parameters = {
        "actor_state": "guard",
        "can_flee": True,
        "safe_route": True,
        "moves_away": True,
        "flee_distance": 60,
        "destination": [80, 0, 0],
        "visible_fear_casters": ["hero"] if visible else [],
    }
    _reject(state, "resolve_srd_fear_turn", **(parameters | {"safe_route": False}))
    _reject(state, "resolve_srd_fear_turn", **(parameters | {"visible_fear_casters": ["other"]}))
    _run("resolve_srd_fear_turn", state, **parameters)
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.combat"]["movement_remaining"] == 0
    assert _save(state, kind="ability_check")["_effective_disadvantage"] is visible
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = 100
    _run("end_combat_turn", state)
    target = state[f"{SRD}.entities"]["guard"]
    assert ("frightened" in target["conditions"]) is visible
    assert (target.get("last_control_behavior_save") is None) is visible


def test_fear_no_route_allows_action_but_saved_visibility_and_dropped_attack_still_apply(
    monkeypatch,
):
    state, data, params = _case(monkeypatch, "fear")
    state[f"{SRD}.entities"]["guard"]["inventory"] = ["spear"]
    _run(
        "record_srd_control_held_items",
        state,
        target_state="guard",
        held_items=["spear"],
        held_attack_names=["Spear"],
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _turn(state)
    _run(
        "resolve_srd_fear_turn",
        state,
        actor_state="guard",
        can_flee=False,
        safe_route=False,
        moves_away=False,
        flee_distance=0,
        visible_fear_casters=["hero"],
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _npc_attack(state, "Spear", use_multiattack=False)
    assert state == before
    result = _npc_attack(state, "Unarmed", use_multiattack=False).result
    assert result["context"]["_condition_disadvantage"] is True


def _confused_turn(monkeypatch, roll):
    state, _, _, _ = _cast(monkeypatch, "confusion")
    seed = next(value for value in range(1000) if Random(value).randint(1, 10) == roll)
    monkeypatch.setattr(dice, "_rng", Random(seed))
    _turn(state)
    assert state[f"{SRD}.entities"]["guard"]["control_confusion_roll"] == roll
    return state


@pytest.mark.parametrize("roll", [1, 3, 7, 9])
def test_confusion_all_four_turn_branches_reach_real_budgets(monkeypatch, roll):
    state = _confused_turn(monkeypatch, roll)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["bonus_action_available"] is False
    assert target["reaction_available"] is False
    if roll == 1:
        _reject(
            state,
            "resolve_srd_confused_turn",
            actor_state="guard",
            path_direction=0,
            movement_feet=30,
        )
        delta = {1: [0, 30], 2: [30, 0], 3: [0, -30], 4: [-30, 0]}[
            target["control_confusion_direction"]
        ]
        _run(
            "resolve_srd_confused_turn",
            state,
            actor_state="guard",
            path_direction=target["control_confusion_direction"],
            movement_feet=30,
            destination=[20 + delta[0], delta[1], 0],
        )
        assert state[f"{SRD}.combat"]["movement_remaining"] == 0
        _reject(state, "spend_srd_action", magic_action=False)
    if roll == 3:
        assert state[f"{SRD}.combat"]["movement_remaining"] == 0
        _reject(state, "spend_srd_action", magic_action=False)
    if roll == 7:
        _run("resolve_srd_confused_turn", state, actor_state="guard", melee_targets=[])
        assert state[f"{SRD}.combat"]["action_available"] is False
    if roll == 9:
        _run("spend_srd_action", state, magic_action=False)
        assert state[f"{SRD}.combat"]["action_available"] is False
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = 100
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["control_confusion_active"] is False


def test_confusion_random_melee_attack_is_enforced_and_cannot_become_multiattack(monkeypatch):
    state = _confused_turn(monkeypatch, 7)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for entity in scene["entities"]:
        entity["position"] = {
            "hero": [15, 0, 0], "guard": [20, 0, 0], "other": [25, 0, 0]
        }[entity["id"]]
    _run("resolve_srd_confused_turn", state, actor_state="guard", melee_targets=["hero", "other"])
    chosen = state[f"{SRD}.entities"]["guard"]["control_confusion_target"]
    wrong = "other" if chosen == "hero" else "hero"
    for choices in [
        {"target": wrong, "use_multiattack": False},
        {"target": chosen, "attack_mode": "ranged", "use_multiattack": False},
        {"target": chosen, "use_multiattack": True},
    ]:
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected):
            _npc_attack(state, "Claw", **choices)
        assert state == before
    _npc_attack(state, "Claw", target=chosen, use_multiattack=False)
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 0
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _npc_attack(state, "Claw", target=chosen, use_multiattack=False)
    assert state == before
    _run("end_combat_turn", state)


def test_enthrall_penalty_is_actual_perception_only_and_fighting_automatically_saves(monkeypatch):
    state, _, _, _ = _cast(monkeypatch, "enthrall")
    assert _save(state, kind="ability_check", skill="perception")["_modifier"] == -10
    assert _save(state, kind="ability_check", skill="insight")["_modifier"] == 0
    assert (
        _save(state, ability="intelligence", kind="ability_check", skill="perception")["_modifier"]
        == 0
    )
    _run("read_srd_passive_perception", state, target_state="guard", base_passive_perception=17)
    assert state[f"{SRD}.entities"]["guard"]["last_passive_perception"] == 7
    state, _, _, result = _cast(monkeypatch, "enthrall", fighting_caster_targets=["guard"])
    assert result["context"]["_control_save_rolls"] == [None]
    assert state[f"{SRD}.entities"]["guard"].get("control_enthrall_penalty", 0) == 0


def test_dance_success_still_spends_all_movement_until_end_next_turn(monkeypatch):
    state, data, params = _case(monkeypatch, "irresistible_dance")
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = 100
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert "charmed" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    _turn(state)
    assert state[f"{SRD}.combat"]["movement_remaining"] == 0
    _reject(
        state,
        "spend_combat_movement",
        actor="guard",
        actor_state="guard",
        distance=5,
        difficult_terrain=False,
    )
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["control_dancing"] is False


def test_dance_actual_dexterity_disadvantage_incoming_advantage_and_action_escape(monkeypatch):
    state, _, _, _ = _cast(monkeypatch, "irresistible_dance")
    assert _save(state, ability="dexterity")["_effective_disadvantage"] is True
    assert _save(state, ability="constitution")["_effective_disadvantage"] is False
    _turn(state, "other")
    incoming = _npc_attack(
        state, "Claw", actor="other", target="guard", use_multiattack=False
    ).result
    assert incoming["context"]["_condition_advantage"] is True
    assert "charmed" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _turn(state)
    outgoing = _npc_attack(state, "Claw", target="other", use_multiattack=False).result
    assert outgoing["context"]["_condition_disadvantage"] is True
    _turn(state)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = 100
    _run("collect_srd_irresistible_dance", state, actor_state="guard")
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.entities"]["guard"]["control_dance_active"] is False


def test_control_admitted_target_bound_commits_and_excess_rejects_atomically(monkeypatch):
    state, data, params = _case(monkeypatch, "confusion")
    targets = [f"confused_{index}" for index in range(9)]
    for target in targets:
        state[f"{SRD}.entities"][target] = deepcopy(state[f"{SRD}.entities"]["guard"])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "cast_spell_outside_combat",
            state,
            participant_data=data,
            **(params | {"targets": targets}),
        )
    assert state == before
    _run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(params | {"targets": targets[:8]}),
    )
    assert all(
        state[f"{SRD}.entities"][target]["control_confusion_active"] for target in targets[:8]
    )


def test_sleep_release_preserves_independent_unconscious_condition(monkeypatch):
    state, _, _, _ = _cast(monkeypatch, "sleep")
    _run("apply_srd_condition", state, target="guard", condition="unconscious")
    _turn(state, "other")
    _run(
        "shake_srd_sleeping_creature",
        state,
        actor_state="other",
        target_state="guard",
        target_distance=5,
    )
    assert "unconscious" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert "incapacitated" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_suppressed_newest_control_source_exposes_older_source_without_retiring_either(monkeypatch):
    state, _, _, _ = _cast(monkeypatch, "irresistible_dance")
    first = state[f"{SRD}.entities"]["guard"]["control_behavior_source_ids"][0]
    _run(
        "install_srd_control_behavior_source",
        state,
        target_state="guard",
        caster_state="other",
        spell="irresistible_dance",
        condition="charmed",
        behavior="dance",
        save_dc=15,
        concentration_sequence=0,
    )
    latest = state[f"{SRD}.entities"]["guard"]["control_behavior_source_ids"][0]
    assert first != latest
    state[f"{SRD}.state"]["timed_condition_sources"][latest]["suppressed"] = True
    _run("resolve_srd_control_behavior_projection", state, target_state="guard")
    target = state[f"{SRD}.entities"]["guard"]
    assert target["control_behavior_source_ids"] == [first]
    assert target["control_dance_active"] is True
    assert all(
        state[f"{SRD}.state"]["timed_condition_sources"][source]["installed"]
        for source in [first, latest]
    )


def test_public_sleep_reopens_retries_then_real_turn_transitions_and_shake_spends_action(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "sleep.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = _create_spell_campaign(
            owner,
            "sleep",
            target_overrides={
                "saving_throw_modifiers": dict.fromkeys(
                    ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"],
                    -100,
                ),
            },
        )
        receipt, command = _act(
            owner, "cast_spell_outside_combat", **(params | {"targets": ["guard"]})
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting").state
        assert "incapacitated" in saved[f"{SRD}.entities"]["guard"]["conditions"]
        assert saved[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved
        receipt, _ = _act(
            owner,
            "start_combat_encounter_entities",
            first_actor="guard",
            second_actor="other",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, _ = _act(owner, "begin_combat_turn")
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, _ = _act(owner, "end_combat_turn")
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert (
            "unconscious"
            in owner.snapshot("casting").state[f"{SRD}.entities"]["guard"]["conditions"]
        )
        receipt, _ = _act(owner, "advance_combat_encounter_turn")
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, _ = _act(owner, "begin_combat_turn")
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, command = _act(
            owner,
            "shake_srd_sleeping_creature",
            actor_state="other",
            target_state="guard",
            target_distance=5,
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        after = owner.snapshot("casting").state
        assert "unconscious" not in after[f"{SRD}.entities"]["guard"]["conditions"]
        assert "prone" in after[f"{SRD}.entities"]["guard"]["conditions"]
        assert after[f"{SRD}.combat"]["action_available"] is False
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == after
