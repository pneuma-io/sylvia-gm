"""Ordinary NPC preparation must support its native movement Reaction owner."""

from copy import deepcopy

import pytest

from tests.test_action_sequences import (
    compose,
    create_session,
    execute,
    hero_position,
    lite_step,
)
from tests.test_fc_campaign_journey import _practice_scene
from tests.test_srd_lite_foundation import VARIANT_ID, Case


def test_prepared_creature_grants_base_opportunity_reaction(tmp_path):
    value = Case(tmp_path / "prepared.sqlite3", "magic_missile")
    try:
        assert "prepared-guard" not in value.entities()
        receipt, command = value.act(
            "prepare_srd_creature",
            creature_id="prepared-guard",
            name="Prepared Guard",
            source="Synthetic humanoid: AC13, HP20, speed30, reach5; no special reactions.",
            armor_class=13,
            hit_points=20,
            speed=30,
            initiative_modifier=0,
            size_rank=1,
            creature_type="humanoid",
            melee_reach=5,
        )
        prepared = value.snapshot()
        guard = value.entities()["prepared-guard"]
        assert guard["reaction_available"] is True
        assert guard.get("available_reactions") == ["opportunity_attack"]
        value.reopen()
        assert value.execute(command) == receipt
        assert value.snapshot() == prepared
    finally:
        value.close()


@pytest.mark.parametrize("take_reaction", [False, True])
def test_prepared_guard_actual_reaction_and_saved_movement(tmp_path, take_reaction):
    guard_id = "prepared-guard"
    value = Case(
        tmp_path / "reaction.sqlite3",
        "magic_missile",
        participant_changes={"party_role": "player_character"},
        positions={"hero": [0, 0, 0], guard_id: [5, 0, 0]},
        scene_override=_practice_scene({"hero": [0, 0, 0], guard_id: [5, 0, 0]}),
    )
    value.campaign_id = "lite"
    try:
        value.act(
            "prepare_srd_creature",
            creature_id=guard_id,
            name="Prepared Guard",
            source="Synthetic humanoid: AC13 HP20 speed30 reach5 spear +3 1d6+1 piercing.",
            armor_class=13,
            hit_points=20,
            speed=30,
            initiative_modifier=0,
            size_rank=1,
            creature_type="humanoid",
            melee_reach=5,
            inventory=["spear"],
        )
        value.act(
            "prepare_srd_weapon_set",
            actor_state="hero",
            loadout=[{"set_name": "melee", "main_hand": "spear", "off_hand": ""}],
        )
        for left, right in (("hero", guard_id), (guard_id, "hero")):
            value.act(
                "record_srd_creature_relationship",
                actor_state=left,
                toward_state=right,
                attitude="hostile",
                is_ally=False,
            )
        value.start(second_actor=guard_id)
        create_session(value.owner, "lite")
        queue = compose(
            value,
            [
                lite_step(value, "attack", "attack-melee-strength", target=guard_id),
                lite_step(value, "leave", "move-walk", destination=[0, 15, 0]),
                lite_step(value, "pending-end", "end-turn"),
            ],
        )
        paused = execute(value, queue)
        saved = value.snapshot()
        assert paused["status"] == "paused"
        assert paused["pause_summary"] == (
            "Resolve Movement Reaction decision before continuing queued actions."
        )
        assert [row["status"] for row in paused["steps"]] == ["committed", "committed", "pending"]
        assert saved.state[VARIANT_ID + ".combat"]["opportunity_attack_pending"] is True
        assert saved.state[VARIANT_ID + ".state"]["pending_srd_movement"]["stage"] == "selection"
        assert (
            saved.state[VARIANT_ID + ".state"]["pending_srd_movement"]["candidates"][0]["actor"]
            == guard_id
        )
        assert hero_position(value) != [0, 15, 0]
        assert saved.state[VARIANT_ID + ".entities"][guard_id]["reaction_available"] is True
        value.reopen()
        assert value.snapshot() == saved and execute(value, queue) == paused
        decision, decision_command = value.act(
            "prepare_srd_coordinate_opportunity",
            reactor_state=guard_id,
            actor_can_see_target=True,
            target_can_see_actor=True,
            attack_modifier=3,
            damage_dice_count=1,
            damage_die_size="6",
            damage_modifier=1,
            damage_type="piercing",
            weapon="spear",
            attack_ability="strength",
            wielding_two_handed=False,
            take_reaction=take_reaction,
        )
        prepared = value.snapshot()
        assert prepared.state[VARIANT_ID + ".entities"][guard_id]["reaction_available"] is True
        value.reopen()
        assert value.execute(decision_command) == decision and value.snapshot() == prepared
        if take_reaction:
            resolved, resolve_command = value.act(
                "resolve_opportunity_attack",
                target_is_charmer=False,
                fear_source_in_sight=False,
                advantage=False,
                disadvantage=False,
            )
            resolved_state = value.snapshot()
            assert (
                resolved_state.state[VARIANT_ID + ".entities"][guard_id]["reaction_available"]
                is False
            )
            assert (
                resolved_state.state[VARIANT_ID + ".combat"]["movement_remaining"]
                == prepared.state[VARIANT_ID + ".combat"]["movement_remaining"]
            )
            value.reopen()
            assert value.execute(resolve_command) == resolved and value.snapshot() == resolved_state
        completed, completion_command = value.act(
            "complete_srd_coordinate_movement", continue_movement=True
        )
        final = value.snapshot()
        assert hero_position(value) == [0, 15, 0]
        assert final.state[VARIANT_ID + ".state"]["pending_srd_movement"] is None
        assert final.state[VARIANT_ID + ".entities"][guard_id]["reaction_available"] is (
            not take_reaction
        )
        assert final.state[VARIANT_ID + ".combat"]["movement_remaining"] == 15
        value.reopen()
        assert value.execute(completion_command) == completed and value.snapshot() == final
    finally:
        value.close()


def test_grant_generator_is_idempotent_and_retains_every_consumer():
    from pathlib import Path

    from scripts.srd_lite_reaction_grants import bind_reaction_grants
    from scripts.srd_timed_actor_defenses import _load_runtime

    runtime = _load_runtime(
        (
            Path(__file__).resolve().parents[1] / "rulesets/srd-lite/runtime/ruleset.yaml"
        ).read_text()
    )
    before = deepcopy(runtime)
    record = {}
    assert bind_reaction_grants(runtime, record) == ["prepare_srd_creature"]
    assert runtime == before
    assert record == {"version": "0.3.7"}


@pytest.mark.parametrize("explicit", [[], ["shield"]])
def test_grant_generator_preserves_an_explicit_reaction_restriction(explicit):
    from scripts.srd_lite_reaction_grants import bind_reaction_grants

    runtime = {
        "tools": {
            "prepare_srd_creature": {
                "effects": [{"create_entity": {"value": {"available_reactions": explicit}}}]
            }
        }
    }
    before = deepcopy(runtime)
    bind_reaction_grants(runtime, {})
    assert runtime == before


def test_required_reaction_decisions_are_idempotent_and_keep_required_inputs():
    from pathlib import Path

    from scripts.build_srd_5_2_1_mechanics import _tool_schema
    from scripts.srd_lite_reaction_grants import REACTION_DECISIONS, bind_reaction_decisions
    from scripts.srd_timed_actor_defenses import _load_runtime

    runtime = _load_runtime(
        (
            Path(__file__).resolve().parents[1] / "rulesets/srd-lite/runtime/ruleset.yaml"
        ).read_text()
    )
    before = deepcopy(runtime)
    record = {}
    assert bind_reaction_decisions(runtime, record) == list(REACTION_DECISIONS)
    assert runtime == before and record == {"version": "0.3.8"}
    for name in REACTION_DECISIONS:
        assert _tool_schema(runtime["tools"][name])["required"]
        assert runtime["tools"][name]["metadata"]["model_behavior"] == "required_ruleset_decision"
