"""Class defenses alter shared saving-throw damage before resistance and temporary HP."""

import pytest

from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_unarmed_creatures import _case


@pytest.mark.parametrize("class_id,level", [("monk", 7), ("rogue", 7)])
@pytest.mark.parametrize("save_modifier,expected_damage", [(30, 0), (-30, 15)])
def test_evasion_changes_real_area_damage_before_resistance_and_temporary_hp(
    class_id, level, save_modifier, expected_damage,
):
    state, data = _case()
    data.update(classes=[{"id": class_id, "level": level}], level=level)
    state[f"{SRD}.entities"]["hero"].update(
        hp=100, max_hp=100, temp_hp=5, saving_throw_modifiers={"dexterity": save_modifier},
        damage_resistances=["fire"],
    )
    result = _run(
        "resolve_spell_area_save_damage_target", state, participant_data=data,
        target="hero", save_ability="dexterity", spell_save_dc=15, damage_total=60,
        damage_type="fire", success_damage="half", construct_save_disadvantage=False,
        half_cover_targets=[], three_quarters_cover_targets=[],
    )
    hero = state[f"{SRD}.entities"]["hero"]
    assert 105 - hero["hp"] - hero["temp_hp"] == expected_damage
    assert "Evasion" in str(result.result["context"]["_spell_detail_rows"])


@pytest.mark.parametrize("problem", [
    "unearned", "multiclass", "other_ability", "no_damage_on_success",
    "incapacitated", "stunned", "unconscious",
])
def test_evasion_does_not_apply_without_its_grant_or_to_ineligible_effects(problem):
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 7}], level=7)
    ability, success_damage = "dexterity", "half"
    hero = state[f"{SRD}.entities"]["hero"]
    if problem == "unearned":
        data["classes"] = [{"id": "monk", "level": 6}]
    elif problem == "multiclass":
        data["classes"] = [{"id": "monk", "level": 6}, {"id": "rogue", "level": 6}]
        data["level"] = 12
    elif problem == "other_ability":
        ability = "constitution"
    elif problem == "no_damage_on_success":
        success_damage = "none"
    else:
        hero["conditions"] = [problem]
    result = _run("resolve_srd_save_damage", state, participant_data=data,
                  target_state="hero", damage_total=61, save_ability=ability,
                  save_outcome="failure", success_damage=success_damage)
    assert result.result["context"]["_save_damage_amount"] == 61
    assert result.result["context"]["_save_damage_note"] == ""


def test_evasion_mixed_damage_halves_each_type_before_its_own_resistance():
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 7}], level=7)
    state[f"{SRD}.entities"]["hero"].update(
        hp=100, max_hp=100, temp_hp=0, saving_throw_modifiers={"dexterity": -30},
        damage_resistances=["cold"],
    )
    result = _run(
        "resolve_spell_area_composite_damage_target", state, participant_data=data,
        target="hero", save_ability="dexterity", spell_save_dc=15,
        component_1_total=21, component_1_damage_type="bludgeoning",
        component_2_total=23, component_2_damage_type="cold", success_damage="half",
        construct_save_disadvantage=False, half_cover_targets=[], three_quarters_cover_targets=[],
    )
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 85
    assert "Evasion" in str(result.result["context"]["_spell_detail_rows"])


def test_public_evasion_result_and_unchanged_hp_survive_reopen_and_replay(tmp_path):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign

    database = tmp_path / "evasion.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "fireball", participant_overrides={
            "level": 12, "classes": [{"id": "wizard", "level": 5}, {"id": "monk", "level": 7}],
        }, entity_overrides={
            "hp": 100, "max_hp": 100, "temp_hp": 5, "dodging": False,
            "stable": False, "death_successes": 0, "death_failures": 0,
            "stable_recovery_hours_remaining": 0,
            "saving_throw_modifiers": {"dexterity": 30},
        })
        command = ActionCommand(
            campaign_id="casting", action_id="fireball", idempotency_key="fireball",
            owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="casting-turn", package_id=SRD, tool_name="cast_spell_outside_combat",
            parameters=parameters | {"targets": ["hero"]},
        )
        receipt = StatelessMcpMechanics(owner).execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert hero["hp"] == 100 and hero["temp_hp"] == 5
        assert hero["resources"]["spell_slot_3"] == 2
        assert "Evasion" in str(receipt.result["result_card"])
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert StatelessMcpMechanics(owner).execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
