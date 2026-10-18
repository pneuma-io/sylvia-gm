"""Public casting must work across exploration, combat and saved campaign boundaries."""

import io
import json
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.domain.authority import (
    ActionStatus,
    Participant,
    ParticipantValidationStatus,
    StateValue,
    WriterRole,
)
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected
from tests.srd_full.test_srd_spell_result_cards import _spell_case

SRD = "wotc.srd.5.2.1"
ROOT = Path(__file__).resolve().parents[2]


def _create_spell_campaign(
    owner, spell, *, participant_overrides=None, entity_overrides=None,
    target_overrides=None,
):
    """Admit one synthetic caster through the installed package and exact character schema."""
    package = owner.install_package_archive(
        io.BytesIO(bundled_srd_archive()),
        install_id="casting",
    )
    record = json.loads((ROOT / "rulesets/dnd-srd-5.2.1/package-record.json").read_text())
    artifact = next(a for a in record["participant_artifacts"] if a["kind"] == "player_character")
    exact = next(e for e in package.package_lock.entries if e.package_id == SRD)
    state, data, parameters = _spell_case(spell)
    data.update(
        species="human",
        background="sage",
        size_rank=1,
        saving_throw_proficiencies=["intelligence", "wisdom"],
        origin_feats=["magic_initiate"],
    )
    state[f"{SRD}.combat"].update(
        encounter_active=False,
        turn_active=False,
        active_actor=None,
        phase="exploration",
        action_available=False,
    )
    state[f"{SRD}.entities"]["guard"].update(hp=1, max_hp=100, temp_hp=0)
    state[f"{SRD}.entities"]["guard"].update(target_overrides or {})
    state[f"{SRD}.entities"]["other"].update(
        hp=1000,
        max_hp=1000,
        melee_reach=5,
        available_reactions=["opportunity_attack"],
    )
    for entity in state[f"{SRD}.entities"].values():
        entity["initiative_modifier"] = 0
    state[f"{SRD}.entities"]["hero"].update(melee_reach=5, initiative_modifier=20)
    data.update(participant_overrides or {})
    state[f"{SRD}.entities"]["hero"].update(entity_overrides or {})
    owner.create_campaign(
        CampaignCreate(
            campaign_id="casting",
            display_name="Public casting",
            writer=WriterRole.DM,
            package_lock=package.package_lock,
            turn_id="casting-turn",
            participants=(
                Participant(
                    participant_id="hero",
                    kind="player_character",
                    package_id=SRD,
                    package_version=exact.version,
                    package_sha256=exact.sha256,
                    schema_id=artifact["schema_id"],
                    schema_sha256=artifact["schema_sha256"],
                    validation_status=ParticipantValidationStatus.VALIDATED,
                    data=data,
                ),
            ),
            initial_state=tuple(
                StateValue(SRD, key.removeprefix(f"{SRD}."), value) for key, value in state.items()
            ),
        )
    )
    return state, data, parameters


def test_public_harm_maximum_reduction_survives_reopen_and_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "harm.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner, "harm",
            target_overrides={"hp": 1000, "max_hp": 1000,
                              "saving_throw_modifiers": dict.fromkeys(
                                  ["strength", "dexterity", "constitution", "intelligence",
                                   "wisdom", "charisma"], -100)},
        )
        mechanics = StatelessMcpMechanics(owner)
        command = ActionCommand(
            campaign_id="casting", action_id="harm", idempotency_key="harm",
            owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="casting-turn", package_id=SRD,
            tool_name="cast_spell_outside_combat", parameters=parameters,
        )
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(replace(
                command, action_id="out-of-range", idempotency_key="out-of-range",
                parameters=parameters | {"target_distance": 1000},
            ))
        assert owner.snapshot("casting").revision == 0
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        guard = saved.state[f"{SRD}.entities"]["guard"]
        assert guard["max_hp"] < 1000
        assert "Hit Point maximum" in str(receipt.result["result_card"])
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state


def test_public_timed_speed_bonus_survives_reopen_then_expires(tmp_path):
    database = tmp_path / "lasting-speed.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "longstrider")
        parameters["targets"] = ["guard"]
        command = ActionCommand(
            campaign_id="casting", action_id="longstrider", idempotency_key="longstrider",
            owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="casting-turn", package_id=SRD,
            tool_name="cast_spell_outside_combat", parameters=parameters,
        )
        receipt = StatelessMcpMechanics(owner).execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["speed"] == 40
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        mechanics = StatelessMcpMechanics(reopened)
        assert reopened.snapshot("casting").state == saved.state
        elapsed = mechanics.execute_action(replace(
            command, action_id="elapsed", idempotency_key="elapsed",
            expected_revision=saved.revision, tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 600},
        ))
        assert elapsed.status is ActionStatus.COMMITTED
        assert reopened.snapshot("casting").state[f"{SRD}.entities"]["guard"]["speed"] == 30


def test_public_concentration_damage_reduction_survives_reopen_retry_and_expiry(tmp_path):
    database = tmp_path / "resistance.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "resistance")
        command = ActionCommand(
            campaign_id="casting", action_id="resistance", idempotency_key="resistance",
            owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="casting-turn", package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters | {"selected_damage_type": "fire"},
        )
        mechanics = StatelessMcpMechanics(owner)
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(replace(
                command, action_id="wrong-type", idempotency_key="wrong-type",
                parameters=parameters | {"selected_damage_type": "force"},
            ))
        assert owner.snapshot("casting").revision == 0
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        guard = saved.state[f"{SRD}.entities"]["guard"]
        assert hero["concentration_spell_id"] == "resistance"
        assert hero["concentration_selected_damage_type"] == "fire"
        assert guard["concentration_resistance_source_ids"] == ["hero"]
        assert hero["resources"]["spell_slot_1"] == 3
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state
        elapsed = StatelessMcpMechanics(reopened).execute_action(replace(
            command, action_id="elapsed", idempotency_key="elapsed",
            expected_revision=saved.revision, tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 10},
        ))
        assert elapsed.status is ActionStatus.COMMITTED
        expired = reopened.snapshot("casting")
        cleanup = StatelessMcpMechanics(reopened).execute_action(replace(
            command, action_id="expire", idempotency_key="expire",
            expected_revision=expired.revision, tool_name="end_concentration",
            parameters={"caster_state": "hero"},
        ))
        assert cleanup.status is ActionStatus.COMMITTED
        after = reopened.snapshot("casting").state[f"{SRD}.entities"]
        assert after["hero"]["concentration_spell_id"] is None
        assert after["guard"]["concentration_resistance_source_ids"] == []


def test_public_timed_armor_floor_survives_reopen_retry_and_expiry(tmp_path):
    database = tmp_path / "lasting-armor.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "barkskin")
        command = ActionCommand(
            campaign_id="casting", action_id="barkskin", idempotency_key="barkskin",
            owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="casting-turn", package_id=SRD,
            tool_name="cast_spell_outside_combat", parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        with pytest.raises(MechanicsRejected, match="willing"):
            mechanics.execute_action(replace(
                command, action_id="unwilling", idempotency_key="unwilling",
                parameters=parameters | {"target_willing": False},
            ))
        assert owner.snapshot("casting").revision == 0
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["spell_armor_class_floor"] == 17
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state
        elapsed = StatelessMcpMechanics(reopened).execute_action(replace(
            command, action_id="elapsed", idempotency_key="elapsed",
            expected_revision=saved.revision, tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 600},
        ))
        assert elapsed.status is ActionStatus.COMMITTED
        guard = reopened.snapshot("casting").state[f"{SRD}.entities"]["guard"]
        assert guard["spell_armor_class_floor"] == 0


def test_public_timed_hit_point_bonus_upcast_reopens_and_expires(tmp_path):
    database = tmp_path / "lasting-hit-points.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "aid")
        parameters.update(targets=["hero", "guard", "other"], slot_resource="spell_slot_3")
        command = ActionCommand(
            campaign_id="casting", action_id="aid", idempotency_key="aid",
            owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="casting-turn", package_id=SRD,
            tool_name="cast_spell_outside_combat", parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        with pytest.raises(MechanicsRejected, match="within range"):
            mechanics.execute_action(replace(
                command, action_id="too-far", idempotency_key="too-far",
                parameters=parameters | {"targets_max_distance": 31},
            ))
        assert owner.snapshot("casting").revision == 0
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        guard = saved.state[f"{SRD}.entities"]["guard"]
        assert (guard["hp"], guard["max_hp"], guard["spell_hit_point_bonus"]) == (11, 110, 10)
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state
        elapsed = StatelessMcpMechanics(reopened).execute_action(replace(
            command, action_id="elapsed", idempotency_key="elapsed",
            expected_revision=saved.revision, tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 4800},
        ))
        assert elapsed.status is ActionStatus.COMMITTED
        guard = reopened.snapshot("casting").state[f"{SRD}.entities"]["guard"]
        assert (guard["hp"], guard["max_hp"], guard["spell_hit_point_bonus"]) == (1, 100, 0)


def test_public_timed_condition_defense_reopens_and_expires(tmp_path):
    database = tmp_path / "lasting-defense.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "protection_from_poison")
        command = ActionCommand(
            campaign_id="casting", action_id="defense", idempotency_key="defense",
            owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="casting-turn", package_id=SRD,
            tool_name="cast_spell_outside_combat", parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        with pytest.raises(MechanicsRejected, match="touch"):
            mechanics.execute_action(replace(
                command, action_id="untouchable", idempotency_key="untouchable",
                parameters=parameters | {"caster_can_touch_target": False},
            ))
        assert owner.snapshot("casting").revision == 0
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        guard = saved.state[f"{SRD}.entities"]["guard"]
        assert guard["timed_damage_resistances"] == ["poison"]
        assert guard["timed_condition_save_advantages"] == ["poisoned"]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        condition_save = mechanics.execute_action(replace(
            command, action_id="poison-save", idempotency_key="poison-save",
            expected_revision=saved.revision,
            tool_name="resolve_srd_condition_saving_throw",
            parameters={"target": "guard", "ability": "constitution",
                        "condition": "poisoned", "source_save_dc": 15},
        ))
        assert condition_save.status is ActionStatus.COMMITTED
        assert condition_save.result["context"]["_effective_advantage"] is True
        after_save = owner.snapshot("casting")
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == after_save.state
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == after_save.state
        elapsed = StatelessMcpMechanics(reopened).execute_action(replace(
            command, action_id="elapsed", idempotency_key="elapsed",
            expected_revision=after_save.revision, tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 600},
        ))
        assert elapsed.status is ActionStatus.COMMITTED
        guard = reopened.snapshot("casting").state[f"{SRD}.entities"]["guard"]
        assert guard["timed_damage_resistances"] == []
        assert guard["timed_condition_save_advantages"] == []


def test_public_summon_survives_reopen_and_expires_after_retry(tmp_path):
    database = tmp_path / "summon.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "summon_dragon")
        parameters.pop("target")
        parameters.update(
            summon_id="dragon_one", summon_distance=30,
            caster_can_see_summon_space=True, summon_space_unoccupied=True,
            selected_damage_type="fire",
        )
        command = ActionCommand(
            campaign_id="casting", action_id="summon", idempotency_key="summon",
            owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="casting-turn", package_id=SRD,
            tool_name="cast_spell_outside_combat", parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(replace(
                command, action_id="occupied", idempotency_key="occupied",
                parameters=parameters | {"summon_space_unoccupied": False},
            ))
        assert owner.snapshot("casting").revision == 0
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["dragon_one"]["max_hp"] == 50
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(reopened)
        public_tools = {tool.name for tool in mechanics.list_tools("casting")}
        assert "command_srd_summoned_creature" in public_tools
        command_receipt = mechanics.execute_action(replace(
            command, action_id="command-dragon", idempotency_key="command-dragon",
            expected_revision=saved.revision, tool_name="command_srd_summoned_creature",
            parameters={"caster_state": "hero", "summon_state": "dragon_one", "command": "attack"},
        ))
        assert command_receipt.status is ActionStatus.COMMITTED
        creature = reopened.snapshot("casting").state[f"{SRD}.entities"]["dragon_one"]
        assert creature["summon_command"] == "attack"
        elapsed = mechanics.execute_action(replace(
            command, action_id="elapsed", idempotency_key="elapsed",
            expected_revision=reopened.snapshot("casting").revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 600},
        ))
        assert elapsed.status is ActionStatus.COMMITTED
        creature = reopened.snapshot("casting").state[f"{SRD}.entities"]["dragon_one"]
        assert creature["summon_active"] is False


def test_public_noncombat_healing_spends_slots_preserves_receipts_and_reopens(tmp_path):
    database = tmp_path / "casting.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        state, data, parameters = _create_spell_campaign(owner, "cure_wounds")
        mechanics = StatelessMcpMechanics(owner)
        names = {t.name for t in mechanics.list_tools("casting")}
        assert "cast_spell_outside_combat" in names, names
        assert "cast_spell" not in names
        command = ActionCommand(
            campaign_id="casting",
            action_id="heal",
            idempotency_key="heal",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] > 1
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert saved.state[f"{SRD}.combat"] == state[f"{SRD}.combat"]
        assert receipt.result["result_card"]["title"] == "Cure Wounds"
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
        # An invalid touch/range request cannot spend the slot, then ordinary exploration
        # allows a second legal cast without the combat one-slot-per-turn restriction.
        invalid = replace(
            command,
            action_id="unreachable",
            idempotency_key="unreachable",
            expected_revision=saved.revision,
            parameters=parameters | {"target_distance": 60, "caster_can_touch_target": False},
        )
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(invalid)
        assert owner.snapshot("casting").state == saved.state
        second = mechanics.execute_action(
            replace(
                command,
                action_id="second-heal",
                idempotency_key="second-heal",
                expected_revision=owner.snapshot("casting").revision,
            )
        )
        assert second.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 1
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state
        assert "cast_spell_outside_combat" in {
            t.name for t in StatelessMcpMechanics(reopened).list_tools("casting")
        }
        mechanics = StatelessMcpMechanics(reopened)
        started = mechanics.execute_action(
            replace(
                command,
                action_id="combat",
                idempotency_key="combat",
                expected_revision=saved.revision,
                tool_name="start_group_combat_encounter",
                parameters={"combatants": ["hero", "guard", "other"]},
            )
        )
        assert started.status is ActionStatus.COMMITTED, started.result
        assert "cast_spell_outside_combat" not in {t.name for t in mechanics.list_tools("casting")}
        combat = reopened.snapshot("casting")
        with pytest.raises(CommandRejected, match="unavailable"):
            mechanics.execute_action(
                replace(
                    command,
                    action_id="wrong-entry",
                    idempotency_key="wrong-entry",
                    expected_revision=combat.revision,
                )
            )
        assert reopened.snapshot("casting").state == combat.state


def test_public_healing_pool_commits_allocations_and_survives_reopen_retry(tmp_path):
    database = tmp_path / "healing-pool.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "mass_heal",
            target_overrides={"hp": 5, "max_hp": 20, "conditions": ["blinded", "poisoned"]})
        command = ActionCommand(
            campaign_id="casting", action_id="pool", idempotency_key="pool",
            owner_id="casting-owner", writer=WriterRole.DM,
            expected_revision=0, turn_id="casting-turn", package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters | {"targets": ["guard", "other"],
                                     "healing_allocations": [15, 5]},
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 20
        assert saved.state[f"{SRD}.entities"]["guard"]["conditions"] == []
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved.state


def test_public_spell_restriction_survives_reopen_rejects_reaction_and_expires(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "secondary.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "shocking_grasp")
        # Target the other healthy creature so the hit leaves a real later Reaction to reject.
        parameters["target"] = "other"
        command = ActionCommand(
            campaign_id="casting",
            action_id="shock",
            idempotency_key="shock",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        receipt = StatelessMcpMechanics(owner).execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["other"]["opportunity_attacks_blocked"] is True
        assert saved.state[f"{SRD}.state"]["opportunity_blocked_targets"] == ["other"]
        assert owner.execute_action(command) == receipt
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)

        def act(name, **parameters):
            return mechanics.execute_action(
                replace(
                    command,
                    action_id=name,
                    idempotency_key=name,
                    tool_name=name,
                    expected_revision=owner.snapshot("casting").revision,
                    parameters=parameters,
                )
            )

        assert (
            act("start_group_combat_encounter", combatants=["hero", "guard", "other"]).status
            is ActionStatus.COMMITTED
        )
        assert act("begin_combat_turn").status is ActionStatus.COMMITTED
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="Opportunity Attacks"):
            act(
                "trigger_opportunity_attack",
                reactor_state="other",
                actor_can_see_target=True,
                target_can_see_actor=True,
                attack_distance=5,
                attack_modifier=3,
                damage_dice_count=1,
                damage_die_size="4",
                damage_modifier=1,
                damage_type="bludgeoning",
            )
        assert owner.snapshot("casting").state == before.state
        assert (
            act("end_combat_encounter", end_reason="fighting_stopped").status
            is ActionStatus.COMMITTED
        )
        assert owner.snapshot("casting").state[f"{SRD}.state"]["opportunity_blocked_targets"] == [
            "other"
        ]
        elapsed = act("advance_srd_lasting_time", elapsed_rounds=1)
        assert elapsed.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert after.state[f"{SRD}.entities"]["other"]["opportunity_attacks_blocked"] is False, (
            after.state[f"{SRD}.state"]
        )
        assert after.state[f"{SRD}.state"]["opportunity_blocked_targets"] == []


def test_public_concentration_dismissal_preserves_slot_and_has_a_saved_result(tmp_path):
    with AuthorityOwner.open(tmp_path / "concentration.db", "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "shield_of_faith")
        mechanics = StatelessMcpMechanics(owner)
        command = ActionCommand(
            campaign_id="casting",
            action_id="ward",
            idempotency_key="ward",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        assert mechanics.execute_action(command).status is ActionStatus.COMMITTED
        before = owner.snapshot("casting")
        assert (
            before.state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "shield_of_faith"
        )
        assert "end_concentration" in {t.name for t in mechanics.list_tools("casting")}
        end = replace(
            command,
            action_id="dismiss",
            idempotency_key="dismiss",
            expected_revision=before.revision,
            tool_name="end_concentration",
            parameters={"caster_state": "hero"},
        )
        receipt = mechanics.execute_action(end)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert receipt.result["result_card"]["title"] == "Concentration ended"
        after = owner.snapshot("casting")
        assert after.state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
        assert (
            after.state[f"{SRD}.entities"]["hero"]["resources"]
            == before.state[f"{SRD}.entities"]["hero"]["resources"]
        )
        assert mechanics.execute_action(end) == receipt
        assert owner.snapshot("casting").state == after.state
