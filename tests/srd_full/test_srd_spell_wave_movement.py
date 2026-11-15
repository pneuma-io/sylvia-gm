"""Definition-owned mobility spells change later movement and survive public persistence."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_spells import _validate_definition_profiles

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_public_spell_casting import SRD, _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import _spell_case

ROOT = Path(__file__).resolve().parents[2]
SPELLS = ["fly", "jump", "spider_climb", "water_walk", "expeditious_retreat"]


def _cast(spell, *, target="hero", slot=None):
    state, data, parameters = _spell_case(spell)
    state[f"{SRD}.combat"].update(movement_spent=0, dashes_taken=0)
    parameters.update(targets=[target])
    if slot:
        parameters["slot_resource"] = f"spell_slot_{slot}"
    _run("cast_spell", state, participant_data=data, **parameters)
    return state, data, parameters


def _move(state, data, kind, distance, **facts):
    return _run(
        "spend_combat_movement",
        state,
        participant_data=data,
        movement_kind=kind,
        distance=distance,
        difficult_terrain=False,
        **facts,
    )


@pytest.mark.parametrize("spell", SPELLS)
def test_wave_records_match_pinned_source_and_admitted_family(spell):
    declaration = yaml.safe_load(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/spell-mechanics.yaml").read_text()
    )
    row = declaration["spells"][spell]
    _validate_definition_profiles({spell: row["definition"]})
    source = json.loads(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/spell-mechanics-source.json").read_text()
    )["spells"][spell]
    assert source["source_unit_id"] == "srd.5.2.1.spells." + spell.replace("_", "-")
    gaps = {
        "expeditious_retreat": [
            "immediate exploration Dash movement accounting; "
            "combat Dash and later Bonus Action Dash are enforced",
        ],
    }
    assert source["not_yet_mapped"] == gaps.get(spell, [])
    wrong = deepcopy(row["definition"])
    wrong["duration_rounds"] += 1
    with pytest.raises(SystemExit, match="source profile"):
        _validate_definition_profiles({spell: wrong})


def test_flight_uses_60_foot_speed_and_preserves_movement_spent_when_switching_modes():
    state, data, _ = _cast("fly")
    _move(state, data, "walk", 10)
    _move(state, data, "fly", 40)
    assert state[f"{SRD}.combat"]["movement_remaining"] == 10
    assert state[f"{SRD}.combat"]["movement_spent"] == 50
    assert state[f"{SRD}.entities"]["hero"]["spell_can_hover"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="enough movement"):
        _move(state, data, "walk", 1)
    assert state == before


@pytest.mark.parametrize(
    "native_fly,exhaustion,falls", [(0, 0, True), (80, 0, False), (20, 4, True)]
)
def test_concentration_loss_on_another_flying_target_retains_usable_native_flight(
    native_fly, exhaustion, falls
):
    state, data, _ = _cast("fly", target="guard")
    state[f"{SRD}.combat"].update(active_actor="guard", current_actor="guard", movement_spent=0)
    target = state[f"{SRD}.entities"]["guard"]
    target.update(flying_speed=native_fly, exhaustion_level=exhaustion, bonus_action_available=True)
    _move(state, data, "fly", 20)
    _run("end_concentration", state, caster_state="hero")
    target = state[f"{SRD}.entities"]["guard"]
    assert target["spell_flying_speed"] == 0
    assert target.get("spell_flight_fall_pending", False) is falls
    if not falls:
        _move(state, data, "fly", 30)
        assert state[f"{SRD}.combat"]["movement_remaining"] == 30
    else:
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match="Resolve the fall"):
            _move(state, data, "walk", 1)
        assert state == before
        _run(
            "resolve_srd_fall",
            state,
            target_state="guard",
            distance=10,
            landing="ground",
            reaction="none",
        )
        assert state[f"{SRD}.entities"]["guard"]["spell_flight_fall_pending"] is False


def test_overlapping_fly_casters_preserve_flight_until_the_last_effect_ends():
    state, data, _ = _cast("fly", target="guard")
    _run(
        "cast_spell_timed_actor_mobility",
        state,
        lasting_state="state",
        caster_state="other",
        spell="fly",
        targets=["guard"],
        slot_level=3,
        all_targets_willing=True,
        caster_can_touch_all_targets=True,
        concentration_save_modifier=0,
    )
    state[f"{SRD}.combat"].update(active_actor="guard", current_actor="guard", movement_spent=0)
    _move(state, data, "fly", 20)
    _run("end_concentration", state, caster_state="hero")
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["spell_flying_speed"] == 60
    assert guard.get("spell_flight_fall_pending", False) is False
    _run("end_concentration", state, caster_state="other")
    assert state[f"{SRD}.entities"]["guard"]["spell_flight_fall_pending"] is True


def test_failed_damage_concentration_save_removes_flight_from_its_aloft_target():
    state, data, _ = _cast("fly", target="guard")
    state[f"{SRD}.combat"].update(active_actor="guard", current_actor="guard", movement_spent=0)
    _move(state, data, "fly", 20)
    state[f"{SRD}.entities"]["hero"]["concentration_save_modifier"] = -100
    _run("apply_srd_damage", state, target="hero", amount=1, adjustment=0, damage_type="force")
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["guard"]["spell_flight_fall_pending"] is True


def test_spider_climb_permits_ceiling_with_free_hands_and_uses_current_speed():
    state, data, _ = _cast("spider_climb")
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(speed=45, worn_armor="chain_mail", worn_shield="shield")
    _move(state, data, "ceiling", 40)
    assert state[f"{SRD}.combat"]["movement_remaining"] == 5
    _run("end_concentration", state, caster_state="hero")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Spider Climb"):
        _move(state, data, "ceiling", 1)
    assert state == before


def test_spider_climb_expiry_preserves_saved_native_climb_speed():
    state, data, _ = _cast("spider_climb")
    state[f"{SRD}.entities"]["hero"]["climb_speed"] = 45
    _move(state, data, "climb", 10)
    _run("end_concentration", state, caster_state="hero")
    _move(state, data, "climb", 20)
    assert state[f"{SRD}.combat"]["movement_remaining"] == 15


def test_jump_uses_ten_feet_once_per_turn_without_strength_or_run_up():
    state, data, _ = _cast("jump")
    data["abilities"]["strength"] = 3
    _move(state, data, "spell_jump", 30)
    assert state[f"{SRD}.combat"]["movement_remaining"] == 20
    assert state[f"{SRD}.combat"]["movement_spent"] == 10
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="once"):
        _move(state, data, "spell_jump", 1)
    assert state == before
    state[f"{SRD}.combat"]["turn_active"] = False
    _run("begin_combat_turn", state, actor="hero")
    _move(state, data, "spell_jump", 30)
    assert state[f"{SRD}.combat"]["movement_spent"] == 10


def test_jump_dismissal_rejects_incapacitated_caster_and_expires_on_elapsed_time():
    state, data, _ = _cast("jump")
    state[f"{SRD}.entities"]["hero"]["conditions"] = ["incapacitated"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Incapacitated"):
        _run("dismiss_srd_mobility_spell", state, caster_state="hero", spell="jump")
    assert state == before
    state[f"{SRD}.entities"]["hero"]["conditions"] = []
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert state[f"{SRD}.entities"]["hero"]["spell_jump_distance"] == 0


def test_water_walk_surface_transition_and_falling_into_liquid_use_real_costs():
    state, data, _ = _cast("water_walk")
    _move(state, data, "liquid_surface", 20)
    assert state[f"{SRD}.combat"]["movement_spent"] == 20
    hp = state[f"{SRD}.entities"]["hero"]["hp"]
    _run("apply_srd_damage", state, target="hero", amount=1, adjustment=0, damage_type="fire")
    assert state[f"{SRD}.entities"]["hero"]["hp"] == hp - 1
    _run("transition_srd_water_walk", state, actor_state="hero", enter_liquid=True)
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="transition Bonus Action"):
        _move(state, data, "liquid_surface", 1)
    assert state == before
    state[f"{SRD}.entities"]["hero"]["bonus_action_available"] = True
    _run("transition_srd_water_walk", state, actor_state="hero", enter_liquid=False)
    _run(
        "resolve_srd_fall",
        state,
        target_state="hero",
        distance=5,
        landing="liquid",
        reaction="none",
    )
    assert state[f"{SRD}.entities"]["hero"]["water_walk_in_liquid"] is True


def test_expeditious_retreat_immediate_dash_then_bonus_dash_on_later_turn():
    state, data, _ = _cast("expeditious_retreat")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 60
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is False
    state[f"{SRD}.combat"]["turn_active"] = False
    _run("begin_combat_turn", state, actor="hero")
    _run("dash_srd_mobility_bonus_action", state)
    assert state[f"{SRD}.combat"]["movement_remaining"] == 60
    assert state[f"{SRD}.combat"]["action_available"] is True
    _run("end_concentration", state, caster_state="hero")
    state[f"{SRD}.entities"]["hero"]["bonus_action_available"] = True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="not active"):
        _run("dash_srd_mobility_bonus_action", state)
    assert state == before


@pytest.mark.parametrize(
    "spell,slot,maximum",
    [("fly", 5, 3), ("jump", 3, 3), ("spider_climb", 4, 3), ("water_walk", 3, 10)],
)
def test_mobility_target_limits_willingness_and_late_invalid_target_are_atomic(
    spell, slot, maximum
):
    state, data, parameters = _spell_case(spell)
    targets = ["guard"] + [f"extra{i}" for i in range(maximum)]
    for target in targets[1:]:
        state[f"{SRD}.entities"][target] = deepcopy(state[f"{SRD}.entities"]["guard"])
    choices = parameters | {"targets": targets[:maximum], "slot_resource": f"spell_slot_{slot}"}
    for bad in [choices | {"targets": targets}, choices | {"all_targets_willing": False}]:
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected):
            _run("cast_spell", state, participant_data=data, **bad)
        assert state == before
    state[f"{SRD}.entities"][targets[maximum - 1]]["is_alive"] = False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="living"):
        _run("cast_spell", state, participant_data=data, **choices)
    assert state == before
    state[f"{SRD}.entities"][targets[maximum - 1]]["is_alive"] = True
    _run("cast_spell", state, participant_data=data, **choices)
    effects = state[f"{SRD}.state"]["turn_effects"]
    assert len([effect for effect in effects if effect["kind"] == "actor_mobility"]) == maximum


def test_public_expeditious_retreat_reopen_retry_and_derived_bonus_dash(tmp_path):
    database = tmp_path / "mobility.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "expeditious_retreat")
        mechanics = StatelessMcpMechanics(owner)
        command = ActionCommand(
            campaign_id="casting",
            action_id="retreat",
            idempotency_key="retreat",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        command = replace(
            command,
            action_id="encounter",
            idempotency_key="encounter",
            expected_revision=saved.revision,
            tool_name="start_group_combat_encounter",
            parameters={"combatants": ["hero", "guard", "other"]},
        )
        mechanics.execute_action(command)
        command = replace(
            command,
            action_id="begin",
            idempotency_key="begin",
            expected_revision=saved.revision,
            tool_name="begin_combat_turn",
            parameters={},
        )
        command = replace(command, expected_revision=owner.snapshot("casting").revision)
        mechanics.execute_action(command)
        tool = next(
            tool
            for tool in mechanics.list_tools("casting")
            if tool.name == "dash_srd_mobility_bonus_action"
        )
        assert "actor_state" not in tool.input_schema["properties"]
        command = replace(
            command,
            action_id="dash",
            idempotency_key="dash",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="dash_srd_mobility_bonus_action",
            parameters={},
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["movement_remaining"] == 60
        assert mechanics.execute_action(command) == receipt


def test_public_water_walk_transition_reopen_and_free_dismissal(tmp_path):
    database = tmp_path / "water-walk.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "water_walk")
        parameters["targets"] = ["guard"]
        mechanics = StatelessMcpMechanics(owner)
        command = ActionCommand(
            campaign_id="casting",
            action_id="water",
            idempotency_key="water",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics.execute_action(command)
        assert "transition_srd_water_walk" in {
            tool.name for tool in mechanics.list_tools("casting")
        }
        command = replace(
            command,
            action_id="enter",
            idempotency_key="enter",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="transition_srd_water_walk",
            parameters={"actor_state": "guard", "enter_liquid": True},
        )
        receipt = mechanics.execute_action(command)
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["water_walk_in_liquid"] is True
        assert mechanics.execute_action(command) == receipt
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        assert "dismiss_srd_mobility_spell" in {
            tool.name for tool in mechanics.list_tools("casting")
        }
        command = replace(
            command,
            action_id="dismiss",
            idempotency_key="dismiss",
            expected_revision=saved.revision,
            tool_name="dismiss_srd_mobility_spell",
            parameters={"caster_state": "hero", "spell": "water_walk"},
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["guard"]["spell_water_walk"] is False
        )
        assert mechanics.execute_action(command) == receipt
