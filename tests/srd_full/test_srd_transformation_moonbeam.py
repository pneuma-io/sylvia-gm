"""A real failed Moonbeam save fences later form producers and preserves paid attempts."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_transformation_moonbeam import bind_runtime_consumers

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene

ROOT = Path(__file__).parents[1]


def _position(state, target):
    return next(
        entity
        for entity in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if entity["id"] == target
    )


def _restricted_case():
    state, data, parameters = _spell_case("polymorph")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.state"].setdefault("lasting_tick", 0)
    _admit_scene(state)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        hp=1000,
        max_hp=1000,
        temp_hp=0,
        challenge_rating_eighths=32,
        ability_scores=dict.fromkeys(data["abilities"], 12),
        size_rank=2,
        languages=["common"],
        passive_perception=11,
    )
    _position(state, "guard")["position"] = [40, 0, 30]
    _run(
        "cast_spell_transformation",
        state,
        caster_state="hero",
        lasting_state="state",
        spell="polymorph",
        actual_spell_level=4,
        save_dc=18,
        casting_modifier=4,
        casting_ability="intelligence",
        target_state="guard",
        form_id="camel",
        forego_save_targets=["guard"],
        caster_can_see_target=True,
        target_distance=40,
        concentration_save_modifier=0,
    )
    original_source_id = state[f"{SRD}.entities"]["guard"]["form_current_source_id"]
    guard = state[f"{SRD}.entities"]["guard"]
    guard["saving_throw_modifiers"]["constitution"] = -100
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="moonbeam",
        slot_level=2,
        save_dc=18,
        concentration_save_modifier=0,
        origin=[40, 0, 0],
        force_targets=["guard"],
    )
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["moonbeam_shapechange_sources"]
    assert guard["form_current_source_id"] is None
    assert not state[f"{SRD}.state"]["transformation_sources"][original_source_id]["installed"]
    parameters.update(
        transformation_form="camel",
        forego_save_targets=["guard"],
        target_distance=40,
    )
    return state, data, parameters


def test_real_moonbeam_receipt_makes_later_paid_polymorph_no_effect_without_save_or_install():
    state, data, parameters = _restricted_case()
    parameters["forego_save_targets"] = []
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = None
    before_sources = deepcopy(state[f"{SRD}.state"]["transformation_sources"])
    before_guard = deepcopy(state[f"{SRD}.entities"]["guard"])
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    hero = state[f"{SRD}.entities"]["hero"]
    guard = state[f"{SRD}.entities"]["guard"]
    assert hero["resources"]["spell_slot_4"] == 2
    assert state[f"{SRD}.state"]["transformation_sources"] == before_sources
    assert (guard["hp"], guard["temp_hp"], guard["speed"]) == (
        before_guard["hp"],
        before_guard["temp_hp"],
        before_guard["speed"],
    )
    assert "no_effect" in str(result.result["result_card"])
    assert "failed Moonbeam save" in str(result.result["result_card"])
    assert guard["moonbeam_shapechange_sources"]


@pytest.mark.parametrize("leave", [True, False])
def test_real_exit_or_source_end_allows_later_paid_change_and_exit_reentry_stays_clear(leave):
    state, data, parameters = _restricted_case()
    if leave:
        _run("move_srd_scene_creature", state, actor_state="guard", destination=[55, 0, 30])
        assert state[f"{SRD}.entities"]["guard"]["moonbeam_shapechange_sources"] == []
        _run("move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 30])
    else:
        _run("end_concentration", state, caster_state="other")
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["form_current_source_id"] is not None and guard["temp_hp"] == 17
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    assert guard["moonbeam_shapechange_sources"] == []


def test_direct_install_cannot_bypass_the_authentic_failed_save_restriction():
    state, _, _ = _restricted_case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="failed Moonbeam save"):
        _run(
            "install_srd_transformation_source",
            state,
            target_state="guard",
            caster_state="hero",
            lasting_state="state",
            spell="polymorph",
            form_id="camel",
            mode="cloud",
            actual_spell_level=4,
            accept_temporary_hp=True,
            casting_ability="intelligence",
            casting_modifier=4,
            equipment_policy="resize",
            appearance="",
        )
    assert state == before


def test_producer_binding_repeats_and_voluntary_validation_precedes_action_and_allows_endings():
    text = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text(encoding="utf-8")
    assert bind_runtime_consumers(text) == text
    tools = _load_runtime(text)["tools"]
    public = str(tools["change_srd_transformation"]["effects"])
    assert public.index("'validate_only': True") < public.index("spend_srd_action")
    change = tools["change_srd_transformation_source"]["effects"]
    gate = next(effect for effect in change if "check_srd_moonbeam_shapechange" in str(effect))
    assert "_changing_form.spell_id != 'gaseous_form'" in gate["condition"]
    assert "_changing_form.mode == 'normal'" in gate["condition"]
    mutation = next(
        effect for effect in change if effect.get("condition") == "validate_only != true"
    )
    assert change.index(gate) < change.index(mutation)


def test_public_paid_attempt_reopens_and_exact_retry_preserves_real_restriction(
    monkeypatch, tmp_path
):
    import tests.srd_full.test_srd_public_spell_casting as public

    state, data, parameters = _restricted_case()
    target = deepcopy(state[f"{SRD}.entities"]["guard"])
    monkeypatch.setattr(public, "_spell_case", lambda _: (state, data, parameters))
    database = tmp_path / "moonbeam-producer.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public._create_spell_campaign(owner, "polymorph", target_overrides=target)
        command = ActionCommand(
            campaign_id="casting",
            action_id="blocked-polymorph",
            idempotency_key="blocked-polymorph",
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
        assert "failed Moonbeam save" in str(receipt.result["result_card"])
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
        assert saved.state[f"{SRD}.entities"]["guard"]["form_current_source_id"] is None
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(command) == receipt
        assert owner.snapshot("casting").state == saved.state
        ended = mechanics.execute_action(
            replace(
                command,
                action_id="end-moonbeam",
                idempotency_key="end-moonbeam",
                expected_revision=saved.revision,
                tool_name="end_concentration",
                parameters={"caster_state": "other"},
            )
        )
        assert ended.status is ActionStatus.COMMITTED
        later = mechanics.execute_action(
            replace(
                command,
                action_id="later-polymorph",
                idempotency_key="later-polymorph",
                expected_revision=owner.snapshot("casting").revision,
            )
        )
        assert later.status is ActionStatus.COMMITTED, later.result
        final = owner.snapshot("casting")
        assert final.state[f"{SRD}.entities"]["guard"]["form_current_source_id"] is not None
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 1
