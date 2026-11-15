"""Real printed form Recharge uses persist across turns, reformation, and campaign retry."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_wave6_transformations import SRD, _cast, _entity, _run, _setup, _sources

ATTACK = {
    "actor_state": "guard",
    "target_state": "other",
    "attack_name": "Rock",
    "attack_distance": 25,
    "actor_can_see_target": True,
    "target_can_see_actor": True,
    "target_is_charmer": False,
    "fear_source_in_sight": False,
}


def _case(spell="animal_shapes"):
    state, data, parameters = _setup(
        spell,
        transformation_form="ape",
        forego_save_targets=["guard"],
    )
    _cast(state, data, parameters)
    return state


def _start(state):
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=True, turn_active=False, current_actor="guard")
    return _run("begin_combat_turn", state, actor="guard")


def _spent(state):
    return _sources(state)[_entity(state)["form_current_source_id"]]["recharge_uses"]["ape"]["Rock"]


def test_source_plain_recharge_attack_is_admitted_without_admitting_save_area_actions():
    from scripts.srd_full.build_srd_beast_forms import build_index

    forms = build_index()["forms"]
    assert len(forms) == 90
    assert forms["ape"]["execution_supported"] is True
    assert forms["ape"]["recharge_attack_names"] == ["Rock"]
    rock = forms["ape"]["attacks"]["Rock"]
    assert (rock["recharge_min"], rock["attack_modifier"], rock["damage_expression"]) == (
        6,
        5,
        "2d6",
    )
    assert (rock["normal_range"], rock["long_range"]) == (25, 50)
    assert forms["giant_ape"]["execution_supported"] is False
    assert forms["giant_spider"]["execution_supported"] is False


def test_real_rock_use_start_turn_roll_and_failed_attempt_rollback(monkeypatch):
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    monkeypatch.setattr(dice, "_rng", Random(0))
    state = _case()
    _start(state)
    result = _run("attack_srd_transformed_form", state, **ATTACK).result["context"]
    assert result["_attack_roll"]["modifier"] == 5
    assert len(result["_damage_roll"]["results"]) == 2
    assert _spent(state)["available"] is False
    _run("end_combat_turn", state, actor="guard")
    monkeypatch.setattr(dice, "_rng", Random(0))  # actual d6 is 4, not a recharge
    _start(state)
    assert _spent(state)["roll"]["total"] == 4
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Recharge"):
        _run("attack_srd_transformed_form", state, **ATTACK)
    assert state == before
    # A duplicate callback at the same actual turn cannot reroll the retained evidence.
    _run("start_srd_form_recharge_turn", state, target_state="guard", lasting_state="state")
    assert state == before
    _run("end_combat_turn", state, actor="guard")
    monkeypatch.setattr(dice, "_rng", Random(19))  # actual first d6 is 6
    _start(state)
    assert _spent(state)["available"] is True
    assert _spent(state)["roll"]["total"] == 6
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="range"):
        _run("attack_srd_transformed_form", state, **(ATTACK | {"attack_distance": 51}))
    assert state == before
    _run("attack_srd_transformed_form", state, **ATTACK)
    assert _spent(state)["available"] is False


def test_same_source_reformation_does_not_refresh_a_spent_attack():
    state = _case()
    _start(state)
    _run("attack_srd_transformed_form", state, **ATTACK)
    _run("end_combat_turn", state, actor="guard")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    source_id = _entity(state)["form_current_source_id"]
    for form in ("camel", "ape"):
        _run(
            "change_srd_transformation",
            state,
            actor_state="hero",
            source_ids=[source_id],
            form_id=form,
        )
    assert _entity(state)["form_has_recharge"] is True
    assert _spent(state)["available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Recharge"):
        _run("attack_srd_transformed_form", state, **ATTACK)
    assert state == before


def test_unknown_native_save_is_clear_rejection_and_foregoing_needs_no_invented_bonus():
    state, data, parameters = _setup("polymorph", transformation_form="ape")
    _entity(state)["saving_throw_modifiers"]["wisdom"] = None
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="saved Wisdom"):
        _cast(state, data, parameters)
    assert state == before
    _cast(state, data, parameters | {"forego_save_targets": ["guard"]})
    assert _entity(state)["form_has_recharge"] is True


@pytest.mark.parametrize("kind,rounds,activity", [("short", 600, "light"), ("long", 4800, "sleep")])
def test_only_actual_completed_rest_renews_recharge(kind, rounds, activity):
    state = _case()
    _start(state)
    _run("attack_srd_transformed_form", state, **ATTACK)
    _run("end_combat_turn", state, actor="guard")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("begin_srd_rest", state, target_state="guard", kind=kind, activity=activity)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="requires"):
        _run("complete_srd_rest", state, target_state="guard")
    assert state == before
    assert _spent(state)["available"] is False
    _run("advance_srd_lasting_time", state, elapsed_rounds=rounds)
    _run("complete_srd_rest", state, target_state="guard")
    assert _sources(state)[_entity(state)["form_current_source_id"]]["recharge_uses"] == {}
    _start(state)
    _run("attack_srd_transformed_form", state, **ATTACK)
    assert _spent(state)["available"] is False


def test_recharge_turn_rest_composition_repeats_without_new_callbacks():
    from scripts.srd_full.srd_form_recharge import compose
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

    tools = _load_runtime(
        (Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    )["tools"]
    changed = {}
    compose(tools, changed)
    once = deepcopy(changed)
    compose(tools, changed)
    assert changed == once


def test_public_actual_ape_rock_survives_reopen_and_retry_once(tmp_path):
    database = tmp_path / "recharge.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "polymorph")
        command = ActionCommand(
            campaign_id="casting",
            action_id="ape",
            idempotency_key="ape",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters
            | {"transformation_form": "ape", "forego_save_targets": ["guard"]},
        )
        bridge = StatelessMcpMechanics(owner)
        prepared = replace(
            command,
            action_id="prepare-subject",
            idempotency_key="prepare-subject",
            tool_name="prepare_srd_creature",
            parameters={
                "creature_id": "ape_subject",
                "name": "Prepared Ape subject",
                "source": "Synthetic read creature stat block: CR1/2, AC12, HP30, Speed30; "
                "no inferred Challenge Rating.",
                "armor_class": 12,
                "hit_points": 30,
                "speed": 30,
                "size_rank": 2,
                "initiative_modifier": 0,
                "challenge_rating_eighths": 4,
                "creature_type": "humanoid",
            },
        )
        assert bridge.execute_action(prepared).status is ActionStatus.COMMITTED
        command = replace(
            command,
            expected_revision=owner.snapshot("casting").revision,
            parameters=parameters
            | {
                "target": "ape_subject",
                "targets": ["ape_subject"],
                "transformation_form": "ape",
                "forego_save_targets": ["ape_subject"],
            },
        )
        assert bridge.execute_action(command).status is ActionStatus.COMMITTED

        def action(name, parameters, identity):
            step = replace(
                command,
                action_id=identity,
                idempotency_key=identity,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=name,
                parameters=parameters,
            )
            result = bridge.execute_action(step)
            assert result.status is ActionStatus.COMMITTED, result.result

        action(
            "start_combat_encounter_entities",
            {
                "first_actor": "ape_subject",
                "second_actor": "hero",
                "first_surprised": False,
                "second_surprised": False,
                "tie_winner": "first",
            },
            "initiative",
        )
        if owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"] != "ape_subject":
            action("begin_combat_turn", {}, "hero-turn")
            action("end_combat_turn", {}, "hero-end")
            action("advance_combat_encounter_turn", {}, "next-creature")
        action("begin_combat_turn", {}, "ape-turn")
        attack = replace(
            command,
            action_id="rock",
            idempotency_key="rock",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="attack_srd_transformed_form",
            parameters=ATTACK | {"actor_state": "ape_subject", "target_state": "guard"},
        )
        receipt = bridge.execute_action(attack)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        source_id = saved.state[f"{SRD}.entities"]["ape_subject"]["form_current_source_id"]
        assert (
            saved.state[f"{SRD}.state"]["transformation_sources"][source_id]["recharge_uses"][
                "ape"
            ]["Rock"]["available"]
            is False
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert StatelessMcpMechanics(owner).execute_action(attack) == receipt
        assert owner.snapshot("casting").state == saved.state
