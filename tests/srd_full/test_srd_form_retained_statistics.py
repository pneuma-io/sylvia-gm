"Retained mental scores and actual proficiencies feed later saved-stat consumers."

from dataclasses import replace

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave6_transformations import _cast, _entity, _run, _setup


def test_shapechange_passive_perception_matches_actual_retained_check():
    state, data, parameters = _setup(
        "shapechange", transformation_form="owlbear", transformation_seen_before=True
    )
    data["abilities"]["wisdom"] = 20
    data["skill_proficiencies"] = ["perception"]
    data["saving_throw_proficiencies"] = ["intelligence", "wisdom"]
    _cast(state, data, parameters)
    result = _run(
        "ability_check",
        state,
        participant_data=data,
        actor="hero",
        actor_state="hero",
        ability="wisdom",
        skill="perception",
        target_number=15,
        circumstantial_modifier=0,
    ).result["context"]
    assert result["_combined_modifier"] == 11
    assert _entity(state, "hero")["passive_perception"] == 10 + result["_combined_modifier"]
    assert _entity(state, "hero")["saving_throw_modifiers"]["wisdom"] == 11
    saved_save = _run(
        "resolve_srd_condition_saving_throw",
        state,
        target="hero",
        ability="wisdom",
        condition="frightened",
        source_save_dc=15,
    ).result["context"]
    assert saved_save["_combined_modifier"] == 11
    _run(
        "change_srd_transformation",
        state,
        participant_data=data,
        actor_state="hero",
        target_character="hero",
        form_id="grimlock",
        seen_before=True,
    )
    assert _entity(state, "hero")["passive_perception"] == 21
    assert _entity(state, "hero")["saving_throw_modifiers"]["wisdom"] == 11


def test_animal_shapes_passive_perception_uses_retained_wisdom_without_retaining_proficiency():
    state, data, parameters = _setup("animal_shapes", transformation_form="owlbear")
    parameters["transformation_form"] = "brown_bear"
    _entity(state)["ability_scores"]["wisdom"] = 20
    _cast(state, data, parameters)
    # Printed Brown Bear Perception+3 is Wisdom+1 plus proficiency+2.
    assert _entity(state)["passive_perception"] == 17


def test_shapechange_untrained_perception_keeps_real_body_proficiency_bonus():
    state, data, parameters = _setup(
        "shapechange", transformation_form="owlbear", transformation_seen_before=True
    )
    data["abilities"]["wisdom"] = 20
    _cast(state, data, parameters)
    check = _run(
        "ability_check",
        state,
        participant_data=data,
        actor="hero",
        actor_state="hero",
        ability="wisdom",
        skill="perception",
        target_number=15,
        circumstantial_modifier=0,
    ).result["context"]
    assert check["_combined_modifier"] == 9
    assert _entity(state, "hero")["passive_perception"] == 19


def test_public_shapechange_retained_save_survives_reopen_and_retry_then_restores(tmp_path):
    database = tmp_path / "retained-statistics.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner,
            "shapechange",
            participant_overrides={
                "abilities": {
                    "strength": 16,
                    "dexterity": 14,
                    "constitution": 14,
                    "intelligence": 18,
                    "wisdom": 20,
                    "charisma": 10,
                },
                "skill_proficiencies": ["perception"],
                "saving_throw_proficiencies": ["intelligence", "wisdom"],
            },
            entity_overrides={
                "passive_perception": 21,
                "saving_throw_modifiers": {
                    "strength": 3,
                    "dexterity": 2,
                    "constitution": 2,
                    "intelligence": 10,
                    "wisdom": 11,
                    "charisma": 0,
                },
            },
        )
        parameters.update(transformation_form="owlbear", transformation_seen_before=True)
        command = ActionCommand(
            campaign_id="casting",
            action_id="retained-body",
            idempotency_key="retained-body",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        bridge = StatelessMcpMechanics(owner)
        assert bridge.execute_action(command).status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        body = saved.state[f"{SRD}.entities"]["hero"]
        assert body["passive_perception"] == 21 and body["saving_throw_modifiers"]["wisdom"] == 11
        assert body["saving_throw_modifiers"]["constitution"] == 3
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        bridge = StatelessMcpMechanics(owner)
        check = replace(
            command,
            action_id="saved-save",
            idempotency_key="saved-save",
            expected_revision=saved.revision,
            tool_name="resolve_srd_condition_saving_throw",
            parameters={
                "target": "hero",
                "ability": "wisdom",
                "condition": "frightened",
                "source_save_dc": 15,
            },
        )
        result = bridge.execute_action(check)
        assert result.status is ActionStatus.COMMITTED
        assert result.result["context"]["_combined_modifier"] == 11
        after = owner.snapshot("casting")
        assert bridge.execute_action(check) == result
        assert owner.snapshot("casting").state == after.state
        end = replace(
            command,
            action_id="end-body",
            idempotency_key="end-body",
            expected_revision=after.revision,
            tool_name="end_concentration",
            parameters={"caster_state": "hero"},
        )
        assert bridge.execute_action(end).status is ActionStatus.COMMITTED
        reverted = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]
        assert reverted["saving_throw_modifiers"]["constitution"] == 2
        assert reverted["saving_throw_modifiers"]["wisdom"] == 11
        assert reverted["form_base_pc_proficiencies"] is None
