"""Actual moving aura and worn-ring owners change later authoritative play."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full.srd_spell_wards import PROFILES, extend_runtime

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_barriers import _position, _setup
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene


def _case():
    state, data, parameters = _setup("antilife_shell")
    _run(
        "record_srd_creature_relationship",
        state,
        actor_state="hero",
        toward_state="guard",
        attitude="friendly",
        is_ally=True,
    )
    return state, data, parameters


def _cast(state, spell, **choices):
    return _run(
        "cast_srd_spell_ward",
        state,
        caster_state="hero",
        spell=spell,
        slot_level=PROFILES[spell]["base_level"],
        concentration_save_modifier=3,
        save_dc=18,
        **choices,
    ).result["context"]["_new_ward_id"]


def _benefits(state, target="guard"):
    return _run("resolve_srd_spell_ward_benefits", state, target_state=target).result["context"]


def _ring(state, actor, *, wear=True):
    entity = state[f"{SRD}.entities"][actor]
    name = f"{actor}'s platinum ring"
    entity.setdefault("inventory", []).append(name)
    entity.setdefault("spell_materials", []).append(
        {
            "id": f"{actor}_ring",
            "inventory_item": name,
            "kind": "specific",
            "components": ["warding_bond.1"],
            "quantity": 1,
            "value_cp": 5000,
        }
    )
    if wear:
        _run("wear_srd_warding_bond_ring", state, actor_state=actor, material_id=f"{actor}_ring")


def _save(state, target="guard"):
    return _run(
        "d20_test",
        state,
        actor_state=target,
        kind="saving_throw",
        ability="wisdom",
        ability_modifier=2,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=15,
        advantage=False,
        disadvantage=False,
    ).result["context"]


def test_life_resistance_uses_actual_ally_body_range_and_expiry():
    state, _, _ = _case()
    entity = state[f"{SRD}.entities"]["guard"]
    entity.update(hp=100, max_hp=100, temp_hp=0)
    source = _cast(state, "aura_of_life")
    assert _benefits(state)["_ward_life_effective"] is True
    _run("apply_srd_damage", state, target="guard", amount=11, adjustment=0, damage_type="necrotic")
    assert entity is not state[f"{SRD}.entities"]["guard"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 95
    assert _benefits(state, "other")["_ward_life_effective"] is False
    _position(state, "guard")["position"] = [50, 0, 0]
    assert _benefits(state)["_ward_life_effective"] is False
    _position(state, "guard")["position"] = [20, 0, 0]
    _run("end_concentration", state, caster_state="hero")
    assert not state[f"{SRD}.state"]["spell_ward_sources"][source]["installed"]
    assert _benefits(state)["_ward_life_effective"] is False


def test_life_start_turn_restores_actual_hp_and_preserves_dead_or_outside_subjects():
    state, _, _ = _case()
    _cast(state, "aura_of_life")
    state[f"{SRD}.entities"]["guard"].update(hp=0, temp_hp=0, conditions=["unconscious"])
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="guard",
        boundary_phase="start",
        boundary_turn=1,
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1
    assert "unconscious" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    state[f"{SRD}.entities"]["guard"].update(hp=0, is_alive=False)
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="guard",
        boundary_phase="start",
        boundary_turn=2,
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 0


def test_holy_selected_creature_actual_saves_attacks_and_source_end():
    state, _, _ = _case()
    _cast(state, "holy_aura", chosen_state_ids=["guard"])
    result = _save(state)
    assert result["_effective_advantage"] is True
    assert len(result["_d20_test"]["results"]) == 2
    attack = _run(
        "resolve_srd_attack_conditions",
        state,
        actor_state="other",
        target_state="guard",
        attack_distance=5,
        actor_can_see_target=True,
        target_can_see_actor=True,
        fear_source_in_sight=False,
    ).result["context"]
    assert attack["_condition_disadvantage"] is True
    assert _save(state, "other")["_effective_advantage"] is False
    _run("end_concentration", state, caster_state="hero")
    assert _save(state)["_effective_advantage"] is False


def test_holy_choices_change_actual_later_benefits_without_restarting_source():
    state, _, _ = _case()
    source = _cast(state, "holy_aura", chosen_state_ids=["guard"])
    original = deepcopy(state[f"{SRD}.state"]["spell_ward_sources"][source])
    _position(state, "other")["position"] = [5, 5, 0]
    _run(
        "choose_srd_holy_aura_creatures",
        state,
        caster_state="hero",
        source_id=source,
        chosen_state_ids=["other"],
    )
    assert not _save(state)["_effective_advantage"]
    assert _save(state, "other")["_effective_advantage"]
    after = state[f"{SRD}.state"]["spell_ward_sources"][source]
    assert after == original | {"chosen_state_ids": ["other"]}


def test_holy_real_fiend_melee_hit_installs_blindness_until_its_next_turn_end(monkeypatch):
    state, _, _ = _case()
    _cast(state, "holy_aura", chosen_state_ids=["guard"])
    _run(
        "prepare_srd_creature",
        state,
        creature_id="fiend",
        name="Synthetic Fiend",
        source="Synthetic Fiend: melee Claw +30, d4+1 slashing, Constitution save -10.",
        armor_class=12,
        hit_points=25,
        speed=30,
        size_rank=2,
        initiative_modifier=0,
        creature_type="fiend",
        constitution_save=-10,
    )
    body = deepcopy(_position(state, "guard"))
    body.update(id="fiend", position=[25, 0, 0])
    body["metadata"]["participant_ref"] = "fiend"
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(body)
    state[f"{SRD}.entities"]["fiend"]["turns_started"] = 1
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="fiend",
        current_actor="fiend",
        action_available=True,
        normal_action_spent=False,
        attacks_remaining=0,
    )
    monkeypatch.setattr(dice, "_rng", Random(5))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    result = _run(
        "resolve_adjudicated_creature_attack",
        state,
        actor="fiend",
        target="guard",
        attack_name="Claw",
        attack_modifier=30,
        damage_expression="d4",
        damage_modifier=1,
        damage_type="slashing",
        attack_mode="melee",
        attack_distance=5,
        normal_range=5,
        long_range=5,
        actor_can_see_target=True,
        target_can_see_actor=True,
        target_is_charmer=False,
        fear_source_in_sight=False,
        advantage=False,
        disadvantage=False,
    ).result["context"]
    assert result["_attack_outcome"] == "hit"
    assert result["_holy_aura_save"]["outcome"] == "failure"
    assert "blinded" in state[f"{SRD}.entities"]["fiend"]["conditions"]
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="fiend",
        boundary_phase="end",
        boundary_turn=1,
    )
    assert "blinded" in state[f"{SRD}.entities"]["fiend"]["conditions"]
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="fiend",
        boundary_phase="end",
        boundary_turn=2,
    )
    assert "blinded" not in state[f"{SRD}.entities"]["fiend"]["conditions"]


def test_bond_actual_pair_grants_ac_save_resistance_and_retires_after_real_separation():
    state, _, _ = _case()
    _position(state, "guard")["position"] = [5, 0, 0]
    for actor in ("hero", "guard"):
        _ring(state, actor)
    source = _cast(
        state,
        "warding_bond",
        target_state="guard",
        target_willing=True,
        caster_can_touch_target=True,
    )
    ac = _run(
        "resolve_srd_effective_armor_class",
        state,
        target_state="guard",
        lasting_state="state",
        base_armor_class=12,
    ).result["context"]
    assert ac["_effective_armor_class"] == 13
    assert _save(state)["_modifier"] == 3
    state[f"{SRD}.entities"]["guard"].update(hp=100, max_hp=100, temp_hp=0)
    _run("apply_srd_damage", state, target="guard", amount=11, adjustment=0, damage_type="fire")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 95
    _run(
        "move_srd_travel_subject",
        state,
        subject="guard",
        destination=[61, 0, 0],
        plane="material",
        teleport=False,
    )
    assert not state[f"{SRD}.state"]["spell_ward_sources"][source]["installed"]
    _run(
        "move_srd_travel_subject",
        state,
        subject="guard",
        destination=[5, 0, 0],
        plane="material",
        teleport=False,
    )
    assert _benefits(state)["_ward_bond_effective"] is False


def test_bond_unique_actual_rings_and_removal_keep_duration_without_faking_wear():
    state, _, _ = _case()
    _position(state, "guard")["position"] = [5, 0, 0]
    for actor in ("hero", "guard"):
        _ring(state, actor)
    _cast(
        state,
        "warding_bond",
        target_state="guard",
        target_willing=True,
        caster_can_touch_target=True,
    )
    _run("remove_srd_warding_bond_ring", state, actor_state="guard")
    assert not _benefits(state)["_ward_bond_effective"]
    _run("wear_srd_warding_bond_ring", state, actor_state="guard", material_id="guard_ring")
    assert _benefits(state)["_ward_bond_effective"]
    state[f"{SRD}.entities"]["guard"]["inventory"].append("guard's platinum ring")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("wear_srd_warding_bond_ring", state, actor_state="guard", material_id="guard_ring")
    assert state == before


def test_life_actual_harm_damage_retains_hp_maximum_and_owning_recovery_bookkeeping():
    state, data, parameters = _spell_case("harm")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _admit_scene(state)
    for entity in state[f"{SRD}.entities"].values():
        entity.setdefault("size_rank", 2)
    _position(state, "other")["position"] = [0, 5, 0]
    _run(
        "record_srd_creature_relationship",
        state,
        actor_state="other",
        toward_state="guard",
        attitude="friendly",
        is_ally=True,
    )
    _run(
        "cast_srd_spell_ward",
        state,
        caster_state="other",
        spell="aura_of_life",
        slot_level=4,
        concentration_save_modifier=0,
    )
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=500, max_hp=500, temp_hp=0)
    guard["saving_throw_modifiers"]["constitution"] = -30
    first = _run("cast_spell_outside_combat", state, participant_data=data, **parameters).result[
        "context"
    ]
    assert first["_spell_save_outcome"] == "failure"
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 500
    assert state[f"{SRD}.entities"]["guard"]["max_hp"] == 500
    assert state[f"{SRD}.entities"]["guard"].get("recovery_max_hp_reduction", 0) == 0
    _run("end_concentration", state, caster_state="other")
    state[f"{SRD}.entities"]["hero"]["spell_slot_cast_this_turn"] = False
    second = _run("cast_spell_outside_combat", state, participant_data=data, **parameters).result[
        "context"
    ]
    assert second["_spell_save_outcome"] == "failure"
    assert state[f"{SRD}.entities"]["guard"]["max_hp"] < 500
    assert state[f"{SRD}.entities"]["guard"]["recovery_max_hp_reduction"] > 0


def test_bond_zero_hp_damage_ends_source_before_healing_and_multiple_original_targets():
    state, _, _ = _case()
    for actor in ("hero", "guard", "other"):
        _ring(state, actor)
    _position(state, "guard")["position"] = [5, 0, 0]
    _position(state, "other")["position"] = [0, 5, 0]
    first = _cast(
        state,
        "warding_bond",
        target_state="guard",
        target_willing=True,
        caster_can_touch_target=True,
    )
    second = _cast(
        state,
        "warding_bond",
        target_state="other",
        target_willing=True,
        caster_can_touch_target=True,
    )
    records = state[f"{SRD}.state"]["spell_ward_sources"]
    assert records[first]["installed"] and records[second]["installed"]
    _cast(
        state,
        "warding_bond",
        target_state="guard",
        target_willing=True,
        caster_can_touch_target=True,
    )
    assert not state[f"{SRD}.state"]["spell_ward_sources"][first]["installed"]
    _run(
        "apply_srd_damage",
        state,
        target="hero",
        amount=500,
        adjustment=0,
        damage_type="force",
        source_state_id="other",
    )
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 0
    assert all(not r["installed"] for r in state[f"{SRD}.state"]["spell_ward_sources"].values())


def test_ward_emitter_repeats_without_growing_shared_guards():
    root = Path(__file__).parents[1]
    text = (root / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text(encoding="utf-8")
    first = extend_runtime(text)
    assert extend_runtime(first) == first


def test_public_paid_bond_reopens_retries_and_actual_movement_permanently_ends_source(
    monkeypatch, tmp_path
):
    import tests.srd_full.test_srd_public_spell_casting as public

    state, data, parameters = _spell_case("warding_bond")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _admit_scene(state)
    _position(state, "guard")["position"] = [5, 0, 0]
    for actor in ("hero", "guard"):
        state[f"{SRD}.entities"][actor]["spell_materials"] = []
        _ring(state, actor, wear=False)
    parameters["material_item_ids"] = ["hero_ring"]
    monkeypatch.setattr(public, "_spell_case", lambda _: (state, data, parameters))
    database = tmp_path / "bond.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, "warding_bond")
        mechanics = StatelessMcpMechanics(owner)
        command = ActionCommand(
            campaign_id="casting",
            action_id="wear-hero",
            idempotency_key="wear-hero",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="wear_srd_warding_bond_ring",
            parameters={"actor_state": "hero", "material_id": "hero_ring"},
        )
        first = mechanics.execute_action(command)
        assert first.status is ActionStatus.COMMITTED, first.result
        assert mechanics.execute_action(command) == first
        command = replace(
            command,
            action_id="wear-guard",
            idempotency_key="wear-guard",
            expected_revision=owner.snapshot("casting").revision,
            parameters={"actor_state": "guard", "material_id": "guard_ring"},
        )
        assert mechanics.execute_action(command).status is ActionStatus.COMMITTED
        cast = replace(
            command,
            action_id="bond",
            idempotency_key="bond",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = mechanics.execute_action(cast)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert mechanics.execute_action(cast) == receipt
        assert owner.snapshot("casting").state == saved.state
        source_id, source = next(iter(saved.state[f"{SRD}.state"]["spell_ward_sources"].items()))
        assert source["installed"] and source["spell_level"] == 2
        assert source["caster_state_id"] == "hero" and source["target_state_id"] == "guard"
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        for actor in ("hero", "guard"):
            assert saved.state[f"{SRD}.entities"][actor]["spell_materials"][0]["quantity"] == 1
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        for point in ([35, 0, 0], [65, 0, 0], [35, 0, 0]):
            advance = replace(
                command,
                action_id=f"time-{owner.snapshot('casting').revision}",
                idempotency_key=f"time-{owner.snapshot('casting').revision}",
                expected_revision=owner.snapshot("casting").revision,
                tool_name="advance_srd_lasting_time",
                parameters={"elapsed_rounds": 1},
            )
            assert mechanics.execute_action(advance).status is ActionStatus.COMMITTED
            move = replace(
                command,
                action_id=f"move-{point[0]}-{owner.snapshot('casting').revision}",
                idempotency_key=f"move-{point[0]}-{owner.snapshot('casting').revision}",
                expected_revision=owner.snapshot("casting").revision,
                tool_name="move_srd_scene_creature",
                parameters={"actor_state": "guard", "destination": point},
            )
            receipt = mechanics.execute_action(move)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            after = owner.snapshot("casting")
            assert _position(after.state, "guard")["position"] == point
            assert mechanics.execute_action(move) == receipt
            assert owner.snapshot("casting").state == after.state
        assert not after.state[f"{SRD}.state"]["spell_ward_sources"][source_id]["installed"]
