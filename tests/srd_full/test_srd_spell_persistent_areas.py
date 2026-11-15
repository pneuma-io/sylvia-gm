"""Persistent affinity areas trigger at actual scene entry and retain optional save receipts."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene, _turn


def _position(state, actor):
    scene = state[f"{SRD}.state"]["spatial_scene"]
    return next(entry["position"] for entry in scene["entities"] if entry["id"] == actor)


def _fixture(spell="antipathy_sympathy"):
    state, data, parameters = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _admit_scene(state)
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0, size="medium")
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["wisdom"] = -100
    state[f"{SRD}.entities"]["guard"]["creature_type"] = "construct"
    state[f"{SRD}.entities"]["other"]["creature_kind_tags"] = ["goblins"]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(entry for entry in scene["entities"] if entry["id"] == "other")["position"] = [150, 0, 0]
    parameters.update(targets=["guard"], affinity_kind="goblins")
    return state, data, parameters


def _cast(monkeypatch, mode="sympathy", **changes):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _fixture()
    parameters["affinity_mode"] = mode
    state[f"{SRD}.entities"]["other"].update(changes)
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=600,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    return state, data, parameters


def _enter(state):
    return _run("move_srd_scene_creature", state, actor_state="other", destination=[125, 0, 0])


def _source(state):
    source_id = state[f"{SRD}.entities"]["other"]["control_affliction_source_ids"][0]
    return source_id, state[f"{SRD}.state"]["timed_condition_sources"][source_id]


def _reject(state, tool, **parameters):
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(tool, state, **parameters)
    assert state == before


@pytest.mark.parametrize("mode,condition", [("antipathy", "frightened"), ("sympathy", "charmed")])
def test_actual_radius_entry_stops_at_boundary_and_spends_only_the_reached_segment(
    monkeypatch,
    mode,
    condition,
):
    state, _, _ = _cast(monkeypatch, mode)
    result = _enter(state)
    assert _position(state, "other") == [140, 0, 0]
    target = state[f"{SRD}.entities"]["other"]
    assert target["scene_movement_spent"] == 10
    assert condition in target["conditions"] and target["affliction_movement_pending"] is True
    assert result.result["context"]["_scene_area_interrupted"] is True
    _reject(state, "move_srd_scene_creature", actor_state="other", destination=[125, 0, 0])


def test_successful_entry_save_allows_resume_without_another_save_for_one_minute(monkeypatch):
    state, _, _ = _cast(monkeypatch)
    state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["wisdom"] = 100
    _enter(state)
    assert _position(state, "other") == [140, 0, 0]
    target = state[f"{SRD}.entities"]["other"]
    first_roll = deepcopy(target["last_affinity_save_roll"])
    _run("move_srd_scene_creature", state, actor_state="other", destination=[125, 0, 0])
    target = state[f"{SRD}.entities"]["other"]
    assert _position(state, "other") == [125, 0, 0]
    assert target["scene_movement_spent"] == 25 and target["last_affinity_save_roll"] == first_roll


def test_nonmatching_kind_moves_normally_and_never_rolls_an_affinity_save(monkeypatch):
    state, _, _ = _cast(monkeypatch, creature_kind_tags=["orcs"])
    _enter(state)
    assert _position(state, "other") == [125, 0, 0]
    target = state[f"{SRD}.entities"]["other"]
    assert target["scene_movement_spent"] == 25 and "last_affinity_save_roll" not in target


def test_moving_anchor_exposes_stationary_creature_at_actual_boundary(monkeypatch):
    state, _, _ = _cast(monkeypatch)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[35, 0, 0])
    assert _position(state, "guard") == [30, 0, 0]
    assert state[f"{SRD}.entities"]["guard"]["scene_movement_spent"] == 10
    assert "charmed" in state[f"{SRD}.entities"]["other"]["conditions"]


def _offer_damage(state, source="guard", amount=1):
    return _run(
        "apply_srd_damage",
        state,
        target="other",
        amount=amount,
        adjustment=0,
        damage_type="force",
        source_state_id=source,
    )


@pytest.mark.parametrize("accept", [False, True])
def test_anchor_damage_offers_a_source_owned_optional_save_and_choice_consumes_once(
    monkeypatch,
    accept,
):
    state, _, _ = _cast(monkeypatch)
    _enter(state)
    source_id, source = _source(state)
    initial_roll = deepcopy(state[f"{SRD}.entities"]["other"]["last_affinity_save_roll"])
    _offer_damage(state)
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert source["affinity_release_pending"] is True
    assert state[f"{SRD}.entities"]["other"]["last_affinity_save_roll"] == initial_roll
    state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["wisdom"] = 100
    parameters = {
        "target_state": "other",
        "source_id": source_id,
        "opportunity": source["affinity_release_sequence"],
        "accept": accept,
    }
    _run("resolve_srd_affinity_release", state, **parameters)
    assert ("charmed" in state[f"{SRD}.entities"]["other"]["conditions"]) is (not accept)
    _reject(state, "resolve_srd_affinity_release", **parameters)


@pytest.mark.parametrize("source,amount", [("hero", 1), ("guard", 0)])
def test_other_damage_or_zero_damage_cannot_create_an_optional_release(monkeypatch, source, amount):
    state, _, _ = _cast(monkeypatch)
    _enter(state)
    source_id, _ = _source(state)
    _offer_damage(state, source=source, amount=amount)
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    assert source.get("affinity_release_pending") is not True


def test_distinct_damage_opportunities_survive_until_each_choice_is_consumed(monkeypatch):
    state, _, _ = _cast(monkeypatch)
    _enter(state)
    source_id, _ = _source(state)
    _offer_damage(state)
    _offer_damage(state)
    for opportunity in (1, 2):
        _run(
            "resolve_srd_affinity_release",
            state,
            target_state="other",
            source_id=source_id,
            opportunity=opportunity,
            accept=False,
        )
        source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
        assert source["affinity_release_pending"] is (opportunity == 1)
        _reject(
            state,
            "resolve_srd_affinity_release",
            target_state="other",
            source_id=source_id,
            opportunity=opportunity,
            accept=False,
        )


@pytest.mark.parametrize("passes_entry_save", [False, True])
def test_compelled_movement_settles_new_entry_before_finishing_the_order(
    monkeypatch,
    passes_entry_save,
):
    state, _, _ = _cast(monkeypatch)
    _, data, parameters = _spell_case("compulsion")
    data.update(species="human", languages=["common"])
    caster = state[f"{SRD}.entities"]["hero"]
    caster.update(prepared_spells=["compulsion"], languages=["common"])
    state[f"{SRD}.entities"]["other"]["languages"] = ["common"]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(entry for entry in scene["entities"] if entry["id"] == "hero")["position"] = [120, 0, 0]
    parameters.update(targets=["other"], targets_max_distance=30)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source_id = state[f"{SRD}.entities"]["other"]["control_order_source_ids"][0]
    _turn(state, "hero", data)
    _run("designate_srd_compulsion_direction", state, caster_state="hero", direction=[-1, 0, 0])
    _turn(state, "other")
    if passes_entry_save:
        state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["wisdom"] = 100
    _run(
        "resolve_srd_control_order",
        state,
        actor_state="other",
        caster_state="hero",
        source_id=source_id,
        destination=[120, 0, 0],
        safe_route=True,
        shortest_route=True,
    )
    assert _position(state, "other") == ([120, 0, 0] if passes_entry_save else [140, 0, 0])
    assert state[f"{SRD}.combat"]["movement_spent"] == (30 if passes_entry_save else 10)
    assert state[f"{SRD}.combat"]["movement_remaining"] == (0 if passes_entry_save else 20)
    assert state[f"{SRD}.entities"]["other"].get("affliction_movement_pending") is (
        not passes_entry_save
    )


def test_end_turn_beyond_radius_still_makes_mandatory_save_and_receipts_immunity(monkeypatch):
    state, _, _ = _cast(monkeypatch, "antipathy")
    _enter(state)
    _turn(state, "other")
    source_id, _ = _source(state)
    _run(
        "resolve_srd_affliction_movement",
        state,
        actor_state="other",
        source_state="guard",
        source_id=source_id,
        destination=[170, 0, 0],
        safe_route=True,
    )
    state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["wisdom"] = 100
    _run("end_combat_turn", state)
    assert "frightened" not in state[f"{SRD}.entities"]["other"]["conditions"]
    source = state[f"{SRD}.state"]["timed_condition_sources"][source_id]
    cast = state[f"{SRD}.state"]["affinity_casts"][source["affliction_cast_id"]]
    assert cast["saved_until"]["other"] > state[f"{SRD}.state"]["lasting_tick"]


def test_expired_area_stops_observing_movement_and_preserves_independent_charm(monkeypatch):
    state, _, _ = _cast(monkeypatch)
    _enter(state)
    _run("apply_srd_condition", state, target="other", condition="charmed", source_state_id="hero")
    _run("advance_srd_lasting_time", state, elapsed_rounds=100000)
    _run("advance_srd_lasting_time", state, elapsed_rounds=44000)
    assert state[f"{SRD}.state"]["has_persistent_spell_areas"] is False
    assert "charmed" in state[f"{SRD}.entities"]["other"]["conditions"]
    _run("move_srd_scene_creature", state, actor_state="other", destination=[135, 0, 0])
    assert _position(state, "other") == [135, 0, 0]


def test_public_area_entry_reopens_and_retries_without_respending_or_rerolling(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(public_casting, "_spell_case", _fixture)
    database = tmp_path / "area.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(owner, "antipathy_sympathy")
        for tool, inputs in (
            ("begin_srd_spell_cast", _begin(parameters)),
            (
                "continue_srd_spell_cast",
                {
                    "caster_state": "hero",
                    "elapsed_rounds": 600,
                    "uninterrupted": True,
                    "verbal_component_available": True,
                    "somatic_component_available": True,
                    "material_component_available": True,
                },
            ),
            ("cast_spell_outside_combat", parameters | {"affinity_mode": "sympathy"}),
        ):
            receipt, _ = _act(owner, tool, **inputs)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, command = _act(
            owner, "move_srd_scene_creature", actor_state="other", destination=[125, 0, 0]
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting").state
        assert _position(saved, "other") == [140, 0, 0]
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved
        assert owner.execute_action(command) == receipt
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
        actor = owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"]
        if actor == "other":
            # Resolve its existing compelled movement, then reach the actual anchor turn.
            source_id, _ = _source(owner.snapshot("casting").state)
            receipt, _ = _act(
                owner,
                "resolve_srd_affliction_movement",
                actor_state="other",
                source_state="guard",
                source_id=source_id,
                destination=[110, 0, 0],
                safe_route=True,
            )
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            receipt, _ = _act(owner, "end_combat_turn")
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            receipt, _ = _act(owner, "begin_combat_turn")
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
        receipt, _ = _act(
            owner,
            "resolve_adjudicated_creature_attack",
            target="other",
            attack_name="Synthetic anchor arrow",
            attack_modifier=100,
            damage_expression="fixed-1",
            damage_modifier=0,
            damage_type="piercing",
            advantage=True,
            disadvantage=False,
            use_multiattack=False,
            attack_mode="ranged",
            attack_distance=120,
            normal_range=200,
            long_range=200,
            actor_can_see_target=True,
            target_can_see_actor=True,
            target_is_charmer=False,
            fear_source_in_sight=False,
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        pending = owner.snapshot("casting").state
        source_id, source = _source(pending)
        assert source["affinity_release_pending"] is True
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == pending
        receipt, command = _act(
            owner,
            "resolve_srd_affinity_release",
            target_state="other",
            source_id=source_id,
            opportunity=source["affinity_release_sequence"],
            accept=False,
        )
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting").state
        assert _source(saved)[1]["affinity_release_pending"] is False
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved
