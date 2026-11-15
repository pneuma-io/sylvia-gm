"Body immunity suppresses real conditions without erasing their underlying sources."

from dataclasses import replace
from pathlib import Path

import pytest
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_transformation_spells import extend_runtime

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave6_transformations import _cast, _entity, _run, _setup


def test_gaseous_body_suppresses_existing_prone_and_restores_it_on_removal():
    state, data, parameters = _setup("gaseous_form")
    _run("apply_srd_condition", state, target="guard", condition="prone")
    _cast(state, data, parameters)
    body = _entity(state)
    source_id = body["form_current_source_id"]
    assert "prone" not in body["conditions"]
    _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)
    assert "prone" in _entity(state)["conditions"]


def test_removing_suppressed_prone_does_not_restore_a_cured_condition():
    state, data, parameters = _setup("gaseous_form")
    _run("apply_srd_condition", state, target="guard", condition="prone")
    _cast(state, data, parameters)
    source_id = _entity(state)["form_current_source_id"]
    _run("remove_srd_condition", state, target="guard", condition="prone")
    _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)
    assert "prone" not in _entity(state)["conditions"]


def _immune_body(**updates):
    state, data, parameters = _setup(
        "true_polymorph", transformation_form="violet_fungus", forego_save_targets=["guard"]
    )
    _entity(state).update(updates)
    return state, data, parameters


def _condition_source(state, *, duration=600, profile="hypnotic", condition="charmed"):
    context = _run(
        "install_srd_timed_condition_source",
        state,
        target_state="guard",
        lasting_state="state",
        caster_state="other",
        spell="hypnotic_pattern",
        condition=condition,
        save_dc=15,
        duration_rounds=duration,
        repeat_save_timing="none",
        save_ability="wisdom",
        control_profile=profile,
    ).result["context"]
    _run("refresh_srd_timed_condition_target", state, target_state="guard", lasting_state="state")
    return context["_timed_condition_source_id"]


def _source(state, identity):
    return state[f"{SRD}.state"]["timed_condition_sources"][identity]


def test_body_immunity_retains_timed_charm_and_restores_its_linked_control():
    state, data, parameters = _immune_body()
    condition_id = _condition_source(state)
    assert "charmed" in _entity(state)["conditions"]
    assert "incapacitated" in _entity(state)["conditions"]
    _cast(state, data, parameters)
    body_id = _entity(state)["form_current_source_id"]
    assert _source(state, condition_id)["installed"] is True
    assert _source(state, condition_id)["suppressed"] is True
    assert "charmed" not in _entity(state)["conditions"]
    assert "incapacitated" not in _entity(state)["conditions"]
    assert _entity(state)["timed_control_speed_zero"] is False
    _run("end_srd_transformation_source", state, target_state="guard", source_id=body_id)
    assert _source(state, condition_id)["installed"] is True
    assert _source(state, condition_id)["suppressed"] is False
    assert {"charmed", "incapacitated"}.issubset(_entity(state)["conditions"])
    assert _entity(state)["timed_control_speed_zero"] is True


def test_body_immunity_does_not_restore_an_expired_timed_source():
    state, data, parameters = _immune_body()
    condition_id = _condition_source(state, duration=1)
    _cast(state, data, parameters)
    body_id = _entity(state)["form_current_source_id"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=2)
    assert _source(state, condition_id)["installed"] is False
    _run("end_srd_transformation_source", state, target_state="guard", source_id=body_id)
    assert "charmed" not in _entity(state)["conditions"]
    assert "incapacitated" not in _entity(state)["conditions"]


def test_body_immunity_preserves_other_conditions_and_rejects_new_immune_applications():
    state, data, parameters = _immune_body(
        conditions=["blinded", "charmed", "deafened", "frightened", "poisoned"]
    )
    _cast(state, data, parameters)
    body_id = _entity(state)["form_current_source_id"]
    assert _entity(state)["conditions"] == ["poisoned"]
    for condition in ("blinded", "charmed", "deafened", "frightened"):
        with pytest.raises(RulesetPreconditionRejected, match="immune"):
            _run("apply_srd_condition", state, target="guard", condition=condition)
    _run("remove_srd_condition", state, target="guard", condition="blinded")
    _run("end_srd_transformation_source", state, target_state="guard", source_id=body_id)
    assert set(_entity(state)["conditions"]) == {"charmed", "deafened", "frightened", "poisoned"}


def test_body_removal_keeps_independent_mind_blank_charm_suppression():
    state, data, parameters = _immune_body(conditions=["charmed", "blinded"])
    _cast(state, data, parameters)
    body_id = _entity(state)["form_current_source_id"]
    _run(
        "install_srd_timed_actor_defense",
        state,
        target_state="guard",
        caster_state="other",
        spell="mind_blank",
        spellcasting_modifier=3,
    )
    _run("end_srd_transformation_source", state, target_state="guard", source_id=body_id)
    assert "charmed" not in _entity(state)["conditions"]
    assert "blinded" in _entity(state)["conditions"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert "charmed" in _entity(state)["conditions"]


def test_suppressed_hypnotic_preserves_unrelated_incapacitated():
    state, data, parameters = _immune_body()
    _run("apply_srd_condition", state, target="guard", condition="incapacitated")
    _condition_source(state)
    _cast(state, data, parameters)
    assert "charmed" not in _entity(state)["conditions"]
    assert "incapacitated" in _entity(state)["conditions"]
    assert _entity(state)["timed_control_speed_zero"] is False


def test_gaseous_body_suppresses_laughter_prone_without_erasing_incapacitated():
    state, data, parameters = _setup("gaseous_form")
    _run(
        "install_srd_timed_condition_source",
        state,
        target_state="guard",
        lasting_state="state",
        caster_state="other",
        spell="hideous_laughter",
        condition="incapacitated",
        save_dc=15,
        duration_rounds=10,
        repeat_save_timing="none",
        save_ability="wisdom",
        control_profile="laughter",
    )
    _run("refresh_srd_timed_condition_target", state, target_state="guard", lasting_state="state")
    assert {"prone", "incapacitated"}.issubset(_entity(state)["conditions"])
    _cast(state, data, parameters)
    body_id = _entity(state)["form_current_source_id"]
    assert "prone" not in _entity(state)["conditions"]
    assert "incapacitated" in _entity(state)["conditions"]
    _run("end_srd_transformation_source", state, target_state="guard", source_id=body_id)
    assert {"prone", "incapacitated"}.issubset(_entity(state)["conditions"])


def test_form_immunity_composition_preserves_exact_shared_projection_on_repeat():
    text = (Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    once = extend_runtime(text)
    twice = extend_runtime(once)
    first, second = _load_runtime(once), _load_runtime(twice)
    for name in (
        "project_srd_transformation",
        "resolve_srd_form_condition_immunity",
        "resolve_srd_timed_actor_condition_suppression",
        "resolve_srd_control_behavior_projection",
    ):
        assert first["tools"][name] == second["tools"][name]


def test_public_immune_body_reopens_restores_conditions_and_retries_once(tmp_path):
    database = tmp_path / "immune-body.db"
    original = {"blinded", "charmed", "deafened", "frightened", "poisoned"}
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(
            owner,
            "true_polymorph",
            target_overrides={
                "hp": 19,
                "max_hp": 20,
                "challenge_rating_eighths": 32,
                "ability_scores": dict.fromkeys(
                    ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"),
                    12,
                ),
                "size_rank": 2,
                "conditions": sorted(original),
            },
        )
        parameters.update(
            target="guard", transformation_form="violet_fungus", forego_save_targets=["guard"]
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="immune-body",
            idempotency_key="immune-body",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        bridge = StatelessMcpMechanics(owner)
        receipt = bridge.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        body = saved.state[f"{SRD}.entities"]["guard"]
        assert body["conditions"] == ["poisoned"]
        assert bridge.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        bridge = StatelessMcpMechanics(owner)
        end = replace(
            command,
            action_id="end-body",
            idempotency_key="end-body",
            expected_revision=saved.revision,
            tool_name="end_concentration",
            parameters={"caster_state": "hero"},
        )
        receipt = bridge.execute_action(end)
        assert receipt.status is ActionStatus.COMMITTED
        reverted = owner.snapshot("casting")
        assert set(reverted.state[f"{SRD}.entities"]["guard"]["conditions"]) == original
        assert bridge.execute_action(end) == receipt
        assert owner.snapshot("casting").state == reverted.state
